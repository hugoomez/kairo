"""Read the machine-read JSON blocks of a measurement plan (M-XXXX).

    plan_blocks.py show <path>       the plan's own blocks as JSON (as frozen); exit 3 with {"error": ...}
    plan_blocks.py efectivo <path>   the EFFECTIVE blocks (own blocks + `## Enmiendas` in order),
                                     with the amendments, the per-estimand deviations and a summary

Each of `## Estimandos`, `## Clasificadores`, `## Intervalos` holds a fenced json block;
the first one under the heading is read. The Estimandos block may also hold a plan-level
`"families"` list (multiple comparisons); each member estimand then carries `family`
({id, method, estimands}). `## Intervalos` may carry `"avisos": "cerrados" |
"enviados"` (absent = cerrados): which notices check the study gate applies (`avisos_cerrados`
or `avisos_enviados`); being a frozen block, it changes only by amendment.

`## Enmiendas` (optional; only `freeze_plan.py amend` writes it, append-only) holds one entry
per amendment of a frozen plan:

    ### E1

    - fecha: 2031-02-03T10:00:00Z
    - por: <who>
    - datos_vistos: si | no
    - motivo: <one line>
    - commit: <sha of the commit that recorded it> | pendiente

    ```json
    {"estimands": [...], "retire_estimands": [...], "classifiers": [...],
     "retire_classifiers": [...], "intervals": {...}}
    ```

The json is a change, not a copy: `estimands` / `classifiers` replace the entry with the same
`id` / `name` or add it, `retire_*` remove one, `intervals` overrides the keys it names. Every
estimand an amendment changes or adds (all of them when ci, bootstrap or seed change) is a
declared deviation of that amendment (`deviations`). Standard library only.
"""
from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import notes  # noqa: E402

TYPES = ("proporcion", "conteo", "mediana", "diferencia", "reutilizacion", "supervivencia",
         "tabla", "interseccion", "tendencia", "cuantiles")
CLUSTER_TYPES = ("proporcion", "diferencia", "interseccion")
BY_TYPES = ("proporcion", "conteo", "mediana", "supervivencia", "interseccion", "cuantiles")
NO_COLUMN_TYPES = ("tabla", "interseccion")   # they name their columns in rows/cols, columns
FAMILY_TYPES = ("diferencia",)
FAMILY_METHODS = ("holm", "bonferroni")
MAX_INTERSECTION = 6   # 2**6 combinations at most
MAX_QUANTILES = 20
_TABLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")   # a units table name (`units`, --units-for)
DEFAULT_TABLE = "default"
AVISOS = ("cerrados", "enviados")
AMENDMENTS = "Enmiendas"
AMEND_KEYS = ("estimands", "retire_estimands", "classifiers", "retire_classifiers", "intervals")
ENTRY_FIELDS = ("fecha", "por", "datos_vistos", "motivo", "commit")
PENDING = "pendiente"
SAMPLE_KEYS = ("ci", "bootstrap", "seed")   # an intervals change in these alters every estimate
_FENCE = re.compile(r"```json[ \t]*\n(.*?)\n[ \t]*```", re.S)
_ENTRY = re.compile(r"^###[ \t]+(\S+)[ \t]*$", re.M)
_FIELD = re.compile(r"^-[ \t]+([a-z_]+):[ \t]*(.*?)[ \t]*$", re.M)
_COMMIT_LINE = re.compile(r"^-[ \t]+commit:.*$", re.M)
_SHA = re.compile(r"^[0-9a-f]{7,64}$")


class PlanError(ValueError):
    pass


def _section(body: str, heading: str) -> str:
    m = re.search(rf"^##[ \t]+{re.escape(heading)}[ \t]*$", body, re.M)
    if not m:
        raise PlanError(f"{heading}: falta la sección '## {heading}'")
    rest = body[m.end():]
    nxt = re.search(r"^##[ \t]", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def _block(body: str, heading: str):
    m = _FENCE.search(_section(body, heading))
    if not m:
        raise PlanError(f"{heading}: falta el bloque ```json bajo '## {heading}'")
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise PlanError(f"{heading}: JSON inválido ({e})") from e


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _estimands(data) -> list[dict]:
    h = "Estimandos"
    items = data.get("estimands") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise PlanError(f"{h}: se espera {{\"estimands\": [...]}} con al menos un estimando")
    seen: set[str] = set()
    for e in items:
        if not isinstance(e, dict):
            raise PlanError(f"{h}: cada estimando debe ser un objeto")
        for k in ("id", "type"):
            if not isinstance(e.get(k), str) or not e[k]:
                raise PlanError(f"{h}: el estimando {e.get('id')!r} necesita '{k}' (texto)")
        if e["type"] not in NO_COLUMN_TYPES and (not isinstance(e.get("column"), str) or not e["column"]):
            raise PlanError(f"{h}: el estimando {e.get('id')!r} necesita 'column' (texto)")
        if e["type"] not in TYPES:
            raise PlanError(f"{h}: type {e['type']!r} no es uno de {', '.join(TYPES)}")
        if e["id"] in seen:
            raise PlanError(f"{h}: id {e['id']!r} repetido")
        seen.add(e["id"])
        if not isinstance(e.get("filter", {}), dict):
            raise PlanError(f"{h}: filter de {e['id']} debe ser un objeto ({{}} si no hay)")
        c = e.get("cluster")
        if c is not None and not (isinstance(c, str) and c):
            raise PlanError(f"{h}: cluster de {e['id']} debe ser una columna o null")
        if c is not None and e["type"] not in CLUSTER_TYPES:
            raise PlanError(f"{h}: {e['id']}: cluster solo se admite en proporcion, diferencia e interseccion")
        _type_fields(h, e)
        _units_and_variants(h, e)
    _families(h, data, items)
    return items


def _units_and_variants(h: str, e: dict) -> None:
    """`units` (which units table, F2d) and `variants` (sensitivity analyses, F2e)."""
    if "family" in e:
        raise PlanError(f"{h}: {e['id']}: 'family' lo pone plan_blocks desde \"families\"; "
                        "declara la familia en \"families\", al nivel de \"estimands\"")
    u = e.get("units")
    if u is not None:
        if not isinstance(u, str) or not _TABLE.match(u):
            raise PlanError(f"{h}: {e['id']}: 'units' debe ser el nombre de una tabla de unidades "
                            "(letras, cifras, '-' o '_')")
        if u == DEFAULT_TABLE:
            raise PlanError(f"{h}: {e['id']}: omite 'units' para la tabla por defecto (no escribas "
                            f"\"units\": \"{DEFAULT_TABLE}\")")
    if "variants" not in e:
        return
    vs = e["variants"]
    if not isinstance(vs, list) or not vs:
        raise PlanError(f"{h}: {e['id']}: 'variants' debe ser una lista no vacía de "
                        "{\"id\": ..., \"filter\": {...}}")
    ids: set[str] = set()
    for v in vs:
        if not isinstance(v, dict) or set(v) - {"id", "filter"}:
            raise PlanError(f"{h}: {e['id']}: cada variante es {{\"id\": ..., \"filter\": {{...}}}} y nada más")
        if not isinstance(v.get("id"), str) or not v["id"]:
            raise PlanError(f"{h}: {e['id']}: cada variante necesita 'id' (texto)")
        if v["id"] in ids:
            raise PlanError(f"{h}: {e['id']}: variante {v['id']!r} repetida")
        ids.add(v["id"])
        if not isinstance(v.get("filter"), dict) or not v["filter"]:
            raise PlanError(f"{h}: {e['id']}: la variante {v['id']} necesita 'filter' (un objeto no vacío)")


def _families(h: str, data: dict, items: list[dict]) -> None:
    """Plan-level `families` (multiple comparisons, F3b): each member estimand gets
    `family` = {id, method, estimands}, so a change to a family after the freeze shows as
    a change of the Estimandos."""
    fams = data.get("families")
    if fams is None:
        return
    if not isinstance(fams, list) or not fams:
        raise PlanError(f"{h}: 'families' debe ser una lista no vacía de "
                        "{\"id\": ..., \"estimands\": [...], \"method\": \"holm\"}")
    by_id = {e["id"]: e for e in items}
    seen: set[str] = set()
    for f in fams:
        if not isinstance(f, dict) or not isinstance(f.get("id"), str) or not f["id"]:
            raise PlanError(f"{h}: cada familia es un objeto con 'id' (texto)")
        if f["id"] in seen:
            raise PlanError(f"{h}: familia {f['id']!r} repetida")
        seen.add(f["id"])
        if f.get("method") not in FAMILY_METHODS:
            raise PlanError(f"{h}: familia {f['id']}: method debe ser uno de {', '.join(FAMILY_METHODS)}")
        members = f.get("estimands")
        if (not isinstance(members, list) or len(members) < 2 or len(set(map(str, members))) != len(members)
                or not all(isinstance(m, str) for m in members)):
            raise PlanError(f"{h}: familia {f['id']}: 'estimands' debe nombrar al menos dos estimandos distintos")
        for m in members:
            e = by_id.get(m)
            if e is None:
                raise PlanError(f"{h}: familia {f['id']}: el estimando {m!r} no existe")
            if e["type"] not in FAMILY_TYPES:
                raise PlanError(f"{h}: familia {f['id']}: {m} es {e['type']}; las familias solo "
                                f"agrupan {', '.join(FAMILY_TYPES)}")
            if "family" in e:
                raise PlanError(f"{h}: el estimando {m} está en dos familias ({e['family']['id']} y {f['id']})")
            e["family"] = {"id": f["id"], "method": f["method"], "estimands": list(members)}


def _col(h: str, e: dict, k: str, required: bool = True) -> None:
    v = e.get(k)
    if v is None and not required:
        return
    if not isinstance(v, str) or not v:
        raise PlanError(f"{h}: el estimando {e['id']} necesita '{k}' (nombre de columna)")


def _type_fields(h: str, e: dict) -> None:
    """The fields each type adds (docs: templates/measurement-plan-template.md)."""
    t = e["type"]
    if t == "tendencia":
        _col(h, e, "by")   # the ordered column of the trend, not a grouping
        order = e.get("order")
        if order is not None and (not isinstance(order, list) or len(order) < 2
                                  or not all(isinstance(x, (str, int)) and not isinstance(x, bool) for x in order)
                                  or len({str(x) for x in order}) != len(order)):
            raise PlanError(f"{h}: {e['id']}: 'order' debe ser una lista de al menos dos valores distintos "
                            f"de '{e['by']}', en su orden")
    elif "by" in e and e["by"] is not None:
        if t not in BY_TYPES:
            raise PlanError(f"{h}: {e['id']}: 'by' solo se admite en {', '.join(BY_TYPES)}")
        _col(h, e, "by")
        if e["by"] == e.get("cluster"):
            raise PlanError(f"{h}: {e['id']}: 'by' y 'cluster' no pueden ser la misma columna")
    if t == "diferencia":
        _col(h, e, "group")
        for k in ("a", "b"):
            if not isinstance(e.get(k), (str, int)) or isinstance(e.get(k), bool) or str(e[k]) == "":
                raise PlanError(f"{h}: {e['id']}: diferencia necesita '{k}' (valor de '{e.get('group')}')")
        if str(e["a"]) == str(e["b"]):
            raise PlanError(f"{h}: {e['id']}: 'a' y 'b' deben ser valores distintos")
    if t == "reutilizacion":
        _col(h, e, "across")
        if e["across"] == e["column"]:
            raise PlanError(f"{h}: {e['id']}: 'across' y 'column' no pueden ser la misma columna")
        mg = e.setdefault("min_groups", 2)
        if not isinstance(mg, int) or isinstance(mg, bool) or mg < 2:
            raise PlanError(f"{h}: {e['id']}: min_groups debe ser un entero >= 2")
    if t == "supervivencia":
        _col(h, e, "event")
        at = e.setdefault("at", [])
        if not isinstance(at, list) or not all(_is_num(x) and x >= 0 for x in at):
            raise PlanError(f"{h}: {e['id']}: 'at' debe ser una lista de números >= 0 ([] si no hay)")
        if "curve" in e and not isinstance(e["curve"], bool):
            raise PlanError(f"{h}: {e['id']}: 'curve' debe ser true o false")
    if t == "tabla":
        _col(h, e, "rows")
        _col(h, e, "cols")
        if e["rows"] == e["cols"]:
            raise PlanError(f"{h}: {e['id']}: 'rows' y 'cols' no pueden ser la misma columna")
    if t == "interseccion":
        cols = e.get("columns")
        if (not isinstance(cols, list) or not 2 <= len(cols) <= MAX_INTERSECTION
                or not all(isinstance(c, str) and c for c in cols) or len(set(cols)) != len(cols)):
            raise PlanError(f"{h}: {e['id']}: interseccion necesita 'columns', entre 2 y {MAX_INTERSECTION} "
                            "columnas 0/1 distintas")
    if t == "cuantiles":
        q = e.get("q")
        if (not isinstance(q, list) or not 1 <= len(q) <= MAX_QUANTILES
                or not all(_is_num(x) and 0 < x < 1 for x in q) or len(set(q)) != len(q)):
            raise PlanError(f"{h}: {e['id']}: cuantiles necesita 'q', de 1 a {MAX_QUANTILES} números "
                            "distintos entre 0 y 1 (p. ej. [0.25, 0.5, 0.75])")


def _classifiers(data) -> list[dict]:
    h = "Clasificadores"
    items = data.get("classifiers") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise PlanError(f"{h}: se espera {{\"classifiers\": [...]}}")
    for c in items:
        if not isinstance(c, dict):
            raise PlanError(f"{h}: cada clasificador debe ser un objeto")
        for k in ("name", "sample", "min_precision_lo", "min_recall_lo"):
            if k not in c:
                raise PlanError(f"{h}: al clasificador {c.get('name')!r} le falta '{k}'")
        for k in ("name", "sample"):
            if not isinstance(c[k], str) or not c[k]:
                raise PlanError(f"{h}: '{k}' de {c.get('name')!r} debe ser texto")
        for k in ("min_precision_lo", "min_recall_lo"):
            if not _is_num(c[k]) or not 0 <= c[k] <= 1:
                raise PlanError(f"{h}: '{k}' de {c['name']} debe ser un número entre 0 y 1")
        if not isinstance(c.get("label"), str) or not c["label"].strip():
            raise PlanError(f"{h}: el clasificador {c['name']} debe fijar 'label' (la columna que valida)")
        pos = c.get("positive")
        if isinstance(pos, float) and not pos.is_integer():
            pos = None
        if pos is None or isinstance(pos, (list, dict)) or (isinstance(pos, str) and not pos.strip()):
            raise PlanError(f"{h}: el clasificador {c['name']} debe fijar 'positive' (el valor que cuenta como positivo)")
        if "ci" not in c:
            c["ci"] = 0.95
        elif not _is_num(c["ci"]) or not 0 < c["ci"] < 1:
            raise PlanError(f"{h}: 'ci' de {c['name']} debe ser un número entre 0 y 1")
    return items


def _intervals(data) -> dict:
    h = "Intervalos"
    if not isinstance(data, dict):
        raise PlanError(f"{h}: se espera un objeto con ci, bootstrap y seed")
    ci = data.get("ci")
    if not _is_num(ci) or not 0 < ci < 1:
        raise PlanError(f"{h}: ci debe ser un número entre 0 y 1")
    for k in ("bootstrap", "seed"):
        if not isinstance(data.get(k), int) or isinstance(data.get(k), bool):
            raise PlanError(f"{h}: {k} debe ser un entero")
    if data["bootstrap"] < 1:
        raise PlanError(f"{h}: bootstrap debe ser al menos 1")
    if "avisos" in data and data["avisos"] not in AVISOS:
        raise PlanError(f"{h}: avisos debe ser uno de {', '.join(AVISOS)} (sin la clave: cerrados)")
    return data


def avisos_mode(plan: dict) -> str:
    """Which notices check the plan chose: `cerrados` (default) or `enviados`."""
    return str((plan.get("intervals") or {}).get("avisos") or "cerrados")


def read_plan(path: Path) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise PlanError(f"no se pudo leer {path}: {e}") from e
    return read_plan_text(text)


def _parts(text: str) -> tuple[dict, str]:
    parts = notes.split_note(text)
    if parts is None:
        raise PlanError("el plan no tiene frontmatter")
    fm_lines, body = parts
    return notes.parse_frontmatter(fm_lines), body


def blocks_of_body(body: str) -> dict:
    """The three blocks of a plan body (no frontmatter needed)."""
    return {
        "estimands": _estimands(_block(body, "Estimandos")),
        "classifiers": _classifiers(_block(body, "Clasificadores")),
        "intervals": _intervals(_block(body, "Intervalos")),
    }


def read_plan_text(text: str) -> dict:
    """read_plan on the note's text (e.g. a version from `git show`): the plan's own blocks,
    never its amendments."""
    fm, body = _parts(text)
    return {"frozen_at": fm.get("frozen_at", ""), "frozen_commit": fm.get("frozen_commit", ""),
            **blocks_of_body(body)}


# ---- amendments ------------------------------------------------------------

def _optional_section(body: str, heading: str) -> str | None:
    m = re.search(rf"^##[ \t]+{re.escape(heading)}[ \t]*$", body, re.M)
    if not m:
        return None
    rest = body[m.end():]
    nxt = re.search(r"^##[ \t]", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def entry_content(text: str) -> str:
    """An amendment entry as compared in git: without its `- commit:` line (written after the
    commit that recorded it), line endings and trailing blanks normalised."""
    text = _COMMIT_LINE.sub("", text.replace("\r\n", "\n"))
    return "\n".join(ln.rstrip() for ln in text.split("\n") if ln.strip())


def _entry_texts(body: str) -> list[tuple[str, str]]:
    sec = _optional_section(body, AMENDMENTS)
    if sec is None:
        return []
    heads = list(_ENTRY.finditer(sec))
    return [(m.group(1), sec[m.start(): heads[i + 1].start() if i + 1 < len(heads) else len(sec)])
            for i, m in enumerate(heads)]


def amendment_contents(text: str) -> dict[str, str]:
    """{entry id: entry_content} of a note's `## Enmiendas`, validating nothing (for old
    versions from `git show`)."""
    parts = notes.split_note(text)
    return {eid: entry_content(t) for eid, t in _entry_texts(parts[1] if parts else text)}


def _delta(eid: str, data) -> dict:
    h = f"{AMENDMENTS}: {eid}"
    if not isinstance(data, dict) or not data:
        raise PlanError(f"{h}: el bloque json debe ser un objeto con al menos una de {', '.join(AMEND_KEYS)}")
    extra = sorted(set(data) - set(AMEND_KEYS))
    if extra:
        raise PlanError(f"{h}: claves desconocidas {', '.join(extra)} (admitidas: {', '.join(AMEND_KEYS)})")
    for k in ("estimands", "classifiers"):
        if k in data and (not isinstance(data[k], list) or not all(isinstance(x, dict) for x in data[k])):
            raise PlanError(f"{h}: {k} debe ser una lista de objetos")
    for k in ("retire_estimands", "retire_classifiers"):
        if k in data and (not isinstance(data[k], list) or not all(isinstance(x, str) and x for x in data[k])):
            raise PlanError(f"{h}: {k} debe ser una lista de ids (texto)")
    if "intervals" in data and (not isinstance(data["intervals"], dict) or not data["intervals"]):
        raise PlanError(f"{h}: intervals debe ser un objeto con las claves que cambian")
    return data


def read_amendments(body: str) -> list[dict]:
    """The entries of `## Enmiendas`, in order: {id, fecha, por, datos_vistos, motivo, commit,
    delta, content}. Ids must run E1, E2, …; [] when the section is absent."""
    out: list[dict] = []
    for i, (eid, text) in enumerate(_entry_texts(body)):
        if eid != f"E{i + 1}":
            raise PlanError(f"{AMENDMENTS}: se esperaba la entrada E{i + 1} y aparece {eid!r} "
                            "(las enmiendas se numeran E1, E2, … y nunca se borran)")
        fields = {k: v for k, v in _FIELD.findall(text)}
        miss = [k for k in ENTRY_FIELDS if not fields.get(k)]
        if miss:
            raise PlanError(f"{AMENDMENTS}: a {eid} le falta {', '.join(miss)}")
        if fields["datos_vistos"] not in ("si", "no"):
            raise PlanError(f"{AMENDMENTS}: {eid}: datos_vistos debe ser si o no")
        if fields["commit"] != PENDING and not _SHA.match(fields["commit"]):
            raise PlanError(f"{AMENDMENTS}: {eid}: commit debe ser un sha o {PENDING}")
        fence = _FENCE.search(text)
        if not fence:
            raise PlanError(f"{AMENDMENTS}: {eid}: falta el bloque ```json con el cambio")
        try:
            data = json.loads(fence.group(1))
        except json.JSONDecodeError as e:
            raise PlanError(f"{AMENDMENTS}: {eid}: JSON inválido ({e})") from e
        out.append({"id": eid, **{k: fields[k] for k in ENTRY_FIELDS},
                    "delta": _delta(eid, data), "content": entry_content(text)})
    return out


def _upsert(items: list[dict], new: list[dict], key: str) -> list[str]:
    done = []
    for x in new:
        k = x.get(key)
        idx = next((i for i, y in enumerate(items) if y.get(key) == k), None)
        if idx is None:
            items.append(copy.deepcopy(x))
        else:
            items[idx] = copy.deepcopy(x)
        done.append(str(k))
    return done


def _families_of(estimands: list[dict]) -> dict[str, dict]:
    """The plan-level families as declared, rebuilt from the `family` _estimands put on members."""
    out: dict[str, dict] = {}
    for e in estimands:
        f = e.get("family")
        if f:
            out.setdefault(f["id"], {"id": f["id"], "method": f["method"], "estimands": list(f["estimands"])})
    return out


def _revalidate_with_families(est: list[dict], fams: dict[str, dict]) -> list[dict]:
    """Validate amended estimands as a block: `family` is plan_blocks' own field, so it is taken
    off and the frozen families are declared again (minus retired members); an amendment that
    leaves a family with fewer than two members is invalid, like a plan would be."""
    plain = [{k: v for k, v in e.items() if k != "family"} for e in est]
    ids = {e["id"] for e in plain}
    data: dict = {"estimands": plain}
    famlist = [{**f, "estimands": [m for m in f["estimands"] if m in ids]} for f in fams.values()]
    if famlist:
        data["families"] = famlist
    return _estimands(data)


def apply_amendments(base: dict, entries: list[dict]) -> dict:
    """The effective blocks: `base` (estimands, classifiers, intervals) with `entries` applied
    in order. Returns {estimands, classifiers, intervals, deviations, summary}: deviations maps
    an estimand id to [{enmienda, datos_vistos}] for each amendment that changed or added it."""
    est = copy.deepcopy(base["estimands"])
    fams = _families_of(est)
    cls = copy.deepcopy(base["classifiers"])
    ivl = copy.deepcopy(base["intervals"])
    deviations: dict[str, list[dict]] = {}
    summary: list[dict] = []
    for a in entries:
        d, eid = a["delta"], a["id"]
        retired = []
        for rid in d.get("retire_estimands", []):
            if not any(e["id"] == rid for e in est):
                raise PlanError(f"{AMENDMENTS}: {eid} retira el estimando {rid!r}, que no existe")
            est = [e for e in est if e["id"] != rid]
            retired.append(rid)
        changed = _upsert(est, d.get("estimands", []), "id")
        retired_cls = []
        for name in d.get("retire_classifiers", []):
            if not any(c["name"] == name for c in cls):
                raise PlanError(f"{AMENDMENTS}: {eid} retira el clasificador {name!r}, que no existe")
            cls = [c for c in cls if c["name"] != name]
            retired_cls.append(name)
        changed_cls = _upsert(cls, d.get("classifiers", []), "name")
        ivl_changed = False
        if "intervals" in d:
            new = {**ivl, **d["intervals"]}
            ivl_changed = new != ivl
            if any(new.get(k) != ivl.get(k) for k in SAMPLE_KEYS):
                changed += [e["id"] for e in est]
            ivl = new
        try:
            est = _revalidate_with_families(est, fams)
            _classifiers({"classifiers": cls})
            _intervals(ivl)
        except PlanError as e:
            raise PlanError(f"{AMENDMENTS}: con {eid} el plan no es válido: {e}") from None
        changed = list(dict.fromkeys(changed))
        for i in changed:
            deviations.setdefault(i, []).append({"enmienda": eid, "datos_vistos": a["datos_vistos"]})
        summary.append({"id": eid, "datos_vistos": a["datos_vistos"], "estimandos_cambiados": changed,
                        "estimandos_retirados": retired,
                        "clasificadores_cambiados": changed_cls + retired_cls,
                        "intervalos_cambiados": ivl_changed})
    return {"estimands": est, "classifiers": cls, "intervals": ivl, "deviations": deviations,
            "summary": summary}


def read_effective_text(text: str) -> dict:
    """The plan's own blocks with its amendments applied: {frozen_at, frozen_commit, estimands,
    classifiers, intervals, amendments, deviations, summary}. Without `## Enmiendas` the blocks
    are exactly read_plan_text's."""
    fm, body = _parts(text)
    base = blocks_of_body(body)
    entries = read_amendments(body)
    eff = apply_amendments(base, entries)
    return {"frozen_at": fm.get("frozen_at", ""), "frozen_commit": fm.get("frozen_commit", ""),
            "estimands": eff["estimands"], "classifiers": eff["classifiers"], "intervals": eff["intervals"],
            "amendments": [{k: v for k, v in a.items() if k != "content"} for a in entries],
            "deviations": eff["deviations"], "summary": eff["summary"]}


def read_effective(path: Path) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise PlanError(f"no se pudo leer {path}: {e}") from e
    return read_effective_text(text)


def diff_blocks(current: dict, new: dict) -> dict:
    """The amendment delta that turns the effective blocks `current` into `new` (both as from
    blocks_of_body / read_effective): changed or added entries, retired ones, changed interval
    keys. {} when nothing changes. Order changes alone are not a change."""
    out: dict = {}
    for key, ident, retire in (("estimands", "id", "retire_estimands"),
                               ("classifiers", "name", "retire_classifiers")):
        cur = {x[ident]: x for x in current[key]}
        nw = {x[ident]: x for x in new[key]}
        gone = [i for i in cur if i not in nw]
        upd = [x for i, x in nw.items() if cur.get(i) != x]
        if gone:
            out[retire] = gone
        if upd:
            out[key] = upd
    ivl_cur, ivl_new = current["intervals"], new["intervals"]
    ch = {k: v for k, v in ivl_new.items() if ivl_cur.get(k) != v}
    if "avisos" in ivl_cur and "avisos" not in ivl_new and ivl_cur["avisos"] != "cerrados":
        ch["avisos"] = "cerrados"
    if ch:
        out["intervals"] = ch
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] not in ("show", "efectivo"):
        print(json.dumps({"error": "uso: plan_blocks.py show|efectivo <ruta>"}))
        return 1
    try:
        data = read_plan(Path(argv[1])) if argv[0] == "show" else read_effective(Path(argv[1]))
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    except PlanError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 3


if __name__ == "__main__":
    sys.exit(main())
