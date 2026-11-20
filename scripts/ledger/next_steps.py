#!/usr/bin/env python3
"""What the progress of a Kairo project has left pending — and what resolves each item.

    next_steps.py --vault <vault> --project <slug | PROJ-XXX> [--json]

Read-only. Each item has a `kind`, a `severity` (crítico | importante | menor),
the `target` it is about, a `message`, the `action` that resolves it and the
`refs` (note ids) involved. Kinds:

  experimento_sin_adjudicar  a confirmatory experiment is `completed` (valid, or
                             with a verdict) but its hypothesis is still
                             preregistrada / en_experimento and its history never
                             names it → update-confidence.          importante
  hub_sin_valor              a hub field still holds the template's `<value>`
                             (budget fields importante, others menor).
  estado_del_arte_diferido   the hub says `sota_map: diferido` (light start:
                             only Novedad.md, no map yet) → «construye el
                             estado del arte» (create-project step 7). menor
  hipotesis_sin_evaluar      an open hypothesis lacks worth / cost fields
                             (vale_la_pena … evaluado) → hypothesis-cycle
                             «¿Merece la pena?».                     menor
  hipotesis_no_merece        an open hypothesis says `vale_la_pena: no` → the
                             researcher decides (discard, reformulate, keep). menor
  frontera_obsoleta          Frontera/mapa.json says so (`obsoleto`), a project
                             paper is missing from it or was added after it was
                             generated, mapa.yaml is newer than mapa.json, a
                             marked cell already has papers, or a hypothesis's
                             cells / status differ from the map's «nuestro»
                             layer → frontier-map actualizar / frontier.py build.
  hueco_sin_hipotesis        a hueco of mapa.json that no hypothesis attacks
                             (neither the map's `atacado_por` nor any
                             hypothesis's frontier_cells) → hypothesis-cycle
                             with that gap.                           menor

A project without Frontera/ simply has no frontier items. Notes marked
`send: never` are skipped.

Study nodes (the designs of config/designs.toml: questions Q-XXXX in Preguntas/ with
plans M-XXXX in Mediciones/, pilots QP-XXXX in Pilotos/ with plans MP-XXXX). Every
state name and gate comes from the design; the gate is study_status.gate:

  pregunta_sin_plan          a question `abierta` with no plan → plan-study.  importante
  plan_sin_congelar          `abierta` with a draft plan (and plan_blocks errors when
                             the blocks are invalid) → plan-study, freeze on the
                             researcher's go-ahead.                         importante
  plan_congelado_sin_recoleccion  `plan_congelado` → the researcher starts the
                             collection in the app (researcher-only edge). importante
  dataset_registrado_antes_del_congelado  study_gates.plan_frozen says the units
                             dataset was registered before the freeze.    crítico
  en_recoleccion_sin_units_dataset  `en_recoleccion` without units_dataset. importante
  clasificador_sin_validar   a classifier of the frozen plan without
                             Datos/anotacion/<sample>/validacion-<name>.json
                             (importante en_recoleccion, menor plan_congelado).
  gate_pendiente             one per `missing` line of the gate, for a node whose
                             state leads to the publishable one (lines already
                             reported by a kind above are not repeated). importante
  avisos_sin_cerrar          counts by state of Avisos/registro.json, through
                             study_gates.count_avisos (never a notice's content).
  piloto_sin_plan / piloto_sin_decision (`## Decisión` still a placeholder) /
  piloto_sin_estimar (no estimate-json in `## Resultado`, menor) / piloto_por_cerrar
  (estimated but still `abierta`) / piloto_explorado_sin_estudio (explorada, and the
  project has no live medicion question → plan-study).
  Exploratory questions (QE-XXXX, design `exploratoria`, citable with the label
  `exploratorio`, plan never frozen): pregunta_sin_plan / exploratoria_sin_estimar (menor) /
  exploratoria_por_cerrar (estimated, gate passes, still `abierta`) / gate_pendiente.
  avisos_sin_cerrar counts a running embargo as open unless every node it concerns has a
  plan that chose `"avisos": "enviados"`.
  plazo                      hub `deadline`: crítico under 7 days, importante under 21,
                             while a `linea_publicacion` question is not terminal.
  recoleccion_activa (menor) / recoleccion_fallida (importante, `fallida` or
                             `interrumpida`): read-only scan of the app's collections
                             (--collections-dir, else KAIRO_COLLECTIONS_DIR, else
                             ~/.kairo/collections): `<id>/state.json` of this project
                             (state, attempts, retries, startedAt, endedAt, exitCode
                             only) and the numbers of `progress.json` in its outDir.
                             log.txt is never opened; an absent dir gives no items.

Exit codes: 0 ok (pending items or not) · 2 bad input. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "security"))
import designs as _designs  # noqa: E402
import project_scan as ps  # noqa: E402
import study_gates as sg  # noqa: E402
import study_status as sst  # noqa: E402
from notes import read_note, section, split_note  # noqa: E402
from plan_blocks import PlanError, avisos_mode, read_effective  # noqa: E402
from send_guard import is_flagged, is_model_notes  # noqa: E402

__version__ = "1.1.0"
TOOL = f"kairo/next_steps@{__version__}"
SEVERITY_ORDER = {"crítico": 0, "importante": 1, "menor": 2}
BUDGET_KEYS = ("compute_budget", "completo_cost_threshold", "ladder_cost_threshold")
FRONTIER_BUILD = "python ${CLAUDE_PLUGIN_ROOT}/scripts/frontier/frontier.py build --vault <vault> --project <slug>"


def item(kind: str, severity: str, target: str, message: str, action: str, refs: list[str] | None = None) -> dict:
    return {"kind": kind, "severity": severity, "target": target, "message": message,
            "action": action, "refs": refs or []}


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def unadjudicated(hyps: list[dict], exps: list[dict]) -> list[dict]:
    by_h = {h["id"]: h for h in hyps}
    out = []
    for e in exps:
        if e["status"] != "completed" or not ps.confirmatory(e) or e["validity"] == "invalid":
            continue
        if not (e["validity"] == "valid" or e["verdict"]):
            continue
        h = by_h.get(e["hypothesis"] or "")
        if h is None or h["status"] not in ("preregistrada", "en_experimento"):
            continue
        if e["id"] in h["history_experiments"]:
            continue
        verdict = f", veredicto {e['verdict']}" if e["verdict"] else ""
        out.append(item(
            "experimento_sin_adjudicar", "importante", h["id"],
            f"{e['id']} está completed{verdict}, pero {h['id']} sigue «{h['status']}» y su history no lo nombra.",
            f"update-confidence sobre {h['id']} (disparador: resultado de {e['id']}).",
            [h["id"], e["id"]]))
    return out


def hub_placeholders(pdir: Path) -> list[dict]:
    parsed = split_note((pdir / "_hub.md").read_text(encoding="utf-8-sig")) if (pdir / "_hub.md").is_file() else None
    if not parsed:
        return []
    out = []
    for line in parsed[0]:
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*<value>\s*(#.*)?$", line)
        if not m:
            continue
        key = m.group(1)
        sev = "importante" if key in BUDGET_KEYS else "menor"
        why = (" — sin él, hypothesis_cost.py y preregister-experiment no pueden comparar el coste con el presupuesto"
               if key in BUDGET_KEYS else "")
        out.append(item("hub_sin_valor", sev, f"_hub.md:{key}",
                        f"`{key}` sigue en `<value>`{why}.",
                        f"Rellenar `{key}` en _hub.md, o borrar la línea si no aplica.", []))
    return out


def sota_deferred(pdir: Path) -> list[dict]:
    """One reminder while the hub's `sota_map` is `diferido` (the light start)."""
    v = str(ps.hub_fm(pdir).get("sota_map") or "")
    if re.split(r"\s+#", v, maxsplit=1)[0].strip().strip("\"'").lower() != "diferido":
        return []
    if (pdir / "Estado-del-arte.md").is_file():
        return [item("estado_del_arte_diferido", "menor", "_hub.md:sota_map",
                     "Estado-del-arte.md ya existe, pero el hub sigue en `sota_map: diferido`.",
                     "Poner `sota_map: true` en _hub.md si el mapa pasó sus comprobaciones.", [])]
    nov = " (el proyecto solo tiene Novedad.md)" if (pdir / "Novedad.md").is_file() else ""
    return [item("estado_del_arte_diferido", "menor", "_hub.md:sota_map",
                 f"Estado del arte diferido (`sota_map: diferido`){nov}: todavía no hay mapa.",
                 "«Construye el estado del arte» cuando haga falta (create-project paso 7); "
                 "después, `sota_map: true`.", [])]


def worth(hyps: list[dict]) -> list[dict]:
    out = []
    for h in hyps:
        if h["status"] not in ps.OPEN_STATUSES:
            continue
        missing = [k for k, v in h["worth"].items() if v is None]
        if missing:
            out.append(item("hipotesis_sin_evaluar", "menor", h["id"],
                            f"{h['id']} ({h['status']}) no dice si merece la pena ni cuánto cuesta: "
                            f"faltan {', '.join(missing)}.",
                            f"hypothesis-cycle, paso «¿Merece la pena?», sobre {h['id']}.", [h["id"]]))
        if (h["worth"].get("vale_la_pena") or "").lower() == "no" and h["status"] in ("propuesta", "en_cola"):
            razon = h["worth"].get("vale_la_pena_razon") or "sin razón escrita"
            out.append(item("hipotesis_no_merece", "menor", h["id"],
                            f"{h['id']} está evaluada como «no merece la pena» ({razon}) y sigue «{h['status']}».",
                            "Decide el investigador: descartarla (review.py discard), reformularla o mantenerla.",
                            [h["id"]]))
    return out


def _date_of(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def project_papers(vault: Path, pid: str) -> list[dict]:
    out = []
    pat = re.compile(r"(?<![\w-])" + re.escape(pid) + r"(?![\w-])")
    for p in sorted((vault / "Papers").glob("P-*.md")):
        if is_model_notes(p.resolve()) or is_flagged(p):
            continue
        parsed = read_note(p)
        if not parsed:
            continue
        fm = parsed[0]
        projects = fm.get("projects")
        projects = " ".join(projects) if isinstance(projects, list) else str(projects or "")
        if not pat.search(projects):
            continue
        out.append({"id": ps.scalar(fm, "id") or p.stem.split(" ")[0], "added": _date_of(ps.scalar(fm, "added"))})
    return out


def frontier(vault: Path, pdir: Path, pid: str, hyps: list[dict]) -> list[dict]:
    paths = ps.frontier_paths(pdir)
    has_yaml, has_json = paths["yaml"].is_file(), paths["json"].is_file()
    if not (has_yaml or has_json):
        return []
    out = []
    build = f"frontier.py build ({FRONTIER_BUILD.replace('<slug>', pdir.name)})"
    if has_yaml and not has_json:
        return [item("frontera_obsoleta", "importante", "Frontera/mapa.json",
                     "Hay Frontera/mapa.yaml pero no mapa.json: la app no puede mostrar la frontera.",
                     build, [])]
    mapa, problem = ps.load_mapa(pdir)
    if problem:
        return [item("frontera_obsoleta", "importante", "Frontera/mapa.json", problem + ".", build, [])]
    if has_yaml and paths["yaml"].stat().st_mtime > paths["json"].stat().st_mtime + 1:
        out.append(item("frontera_obsoleta", "importante", "Frontera/mapa.json",
                        "mapa.yaml se editó después de generar mapa.json.", build, []))
    obs = mapa.get("obsoleto") or {}
    sin = [str(x) for x in obs.get("papers_sin_extraer") or []]
    tax = [str(x) for x in obs.get("taxonomia_cambiada") or []]
    if sin:
        out.append(item("frontera_obsoleta", "importante", "Frontera",
                        f"{len(sin)} paper(s) del proyecto sin extraer en la frontera: {', '.join(sin[:10])}"
                        f"{'…' if len(sin) > 10 else ''}.",
                        "kairo:frontier-map en modo actualizar (extrae solo esos papers).", sin))
    if tax:
        out.append(item("frontera_obsoleta", "importante", "Frontera",
                        f"{len(tax)} extracción(es) hechas con una taxonomía anterior: {', '.join(tax[:10])}"
                        f"{'…' if len(tax) > 10 else ''}.",
                        "kairo:frontier-map en modo actualizar (re-extrae con la taxonomía vigente).", tax))
    in_map = {str(p.get("id")) for p in mapa.get("papers") or [] if isinstance(p, dict) and p.get("id")}
    gen = _date_of(mapa.get("generado"))
    newer, missing = [], []
    for p in project_papers(vault, pid):
        if p["id"] in sin or p["id"] in tax:
            continue
        if in_map and p["id"] not in in_map:
            missing.append(p["id"])
        elif gen and p["added"] and p["added"] > gen:
            newer.append(p["id"])
    if missing:
        out.append(item("frontera_obsoleta", "importante", "Frontera",
                        f"{len(missing)} paper(s) del proyecto no están en mapa.json: {', '.join(missing[:10])}"
                        f"{'…' if len(missing) > 10 else ''}.",
                        "kairo:frontier-map en modo actualizar.", missing))
    if newer:
        out.append(item("frontera_obsoleta", "menor", "Frontera",
                        f"{len(newer)} paper(s) añadidos después de generar mapa.json ({gen}): "
                        f"{', '.join(newer[:10])}{'…' if len(newer) > 10 else ''}.",
                        "kairo:frontier-map en modo actualizar, si su extracción no está al día.", newer))

    cells = ps.mapa_cells(mapa)
    for cid, c in sorted(cells.items()):
        if c.get("marca") and c.get("papers"):
            refs = [str(x) for x in c["papers"]]
            out.append(item("frontera_obsoleta", "importante", cid,
                            f"La casilla {cid} está marcada «{c['marca']}» pero ya la cubre(n) {', '.join(refs)}.",
                            "Revisar la marca en mapa.yaml (kairo:frontier-map actualizar) y reconstruir.", refs))
    # «nuestro» layer vs the hypotheses as they are now
    drift = []
    for h in hyps:
        for cid in h["frontier_cells"]:
            ours = {str(n.get("id")): n.get("status") for n in (cells.get(cid) or {}).get("nuestros") or []
                    if isinstance(n, dict)}
            if cid not in cells:
                drift.append(f"{h['id']} apunta a {cid}, que no existe en el mapa")
            elif ours.get(h["id"]) != h["status"]:
                drift.append(f"{h['id']} ({h['status']}) en {cid}: el mapa dice {ours.get(h['id']) or 'nada'}")
    if drift:
        out.append(item("frontera_obsoleta", "menor", "Frontera/mapa.json",
                        "La capa «nuestro» del mapa no refleja las hipótesis actuales: " + "; ".join(drift[:5])
                        + ("…" if len(drift) > 5 else "") + ".",
                        build, sorted({d.split(" ")[0] for d in drift})))

    attacked: dict[str, list[str]] = {}
    for h in hyps:
        for cid in h["frontier_cells"]:
            attacked.setdefault(cid, []).append(h["id"])
    for g in ps.mapa_huecos(mapa):
        gid = str(g.get("id") or "?")
        by_map = [str(a.get("id")) for a in g.get("atacado_por") or [] if isinstance(a, dict)]
        # frontier.py writes each gap cell as {celda, cubierta_por, marca}; a hand-written
        # mapa.json may still list plain "M1:fila|col" strings.
        gap_cells = [str(c.get("celda")) if isinstance(c, dict) else str(c) for c in g.get("celdas") or []]
        by_notes = sorted({hid for cid in gap_cells for hid in attacked.get(cid, [])})
        if by_map or by_notes:
            continue
        pr = g.get("prioridad")
        out.append(item("hueco_sin_hipotesis", "menor", gid,
                        f"El hueco {gid}{f' (prioridad {pr})' if pr not in (None, '') else ''} "
                        f"«{g.get('titulo') or ''}» no tiene ninguna hipótesis que lo ataque.",
                        f"hypothesis-cycle con el hueco {gid} como entrada (gap-derived desde la frontera).",
                        gap_cells))
    return out


# ---------------------------------------------------------------------------
# Study nodes (questions, pilots) — designs from config/designs.toml
# ---------------------------------------------------------------------------

_EST_BLOCK = re.compile(r"```estimate-json[ \t]*\n.*?\n[ \t]*```", re.S)
_PROGRESS_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")
COLLECTION_FIELDS = ("id", "state", "attempts", "retries", "exitCode", "startedAt", "endedAt")
FAILED_COLLECTIONS = ("fallida", "interrumpida")
STUDY_KINDS = frozenset({
    "plazo", "pregunta_sin_plan", "plan_sin_congelar", "plan_congelado_sin_recoleccion",
    "dataset_registrado_antes_del_congelado", "en_recoleccion_sin_units_dataset", "clasificador_sin_validar",
    "gate_pendiente", "avisos_sin_cerrar", "piloto_sin_plan", "piloto_sin_decision", "piloto_sin_estimar",
    "piloto_por_cerrar", "piloto_explorado_sin_estudio", "recoleccion_activa", "recoleccion_fallida",
    "exploratoria_sin_estimar", "exploratoria_por_cerrar"})
PLAN_STUDY = "kairo:plan-study"
START_COLLECTION = ("El investigador, en la app: Recolecciones → «Empezar la recolección» (pasa la pregunta a "
                    "en_recoleccion; study_status.py rechaza ese paso dentro de una sesión de agente).")
GATE_ACTIONS = {
    "plan_frozen": "Ver la tabla de la puerta en kairo:estimate (paso 6); un plan cambiado o unos datos "
                   "registrados antes del congelado los decide el investigador.",
    "estimates_reproducible": "kairo:estimate (correr estimate.py y guardar su salida en ## Resultado, sin tocar cifras).",
    "classifiers_validated": "El investigador anota y valida el clasificador en la app (Anotar → Validar clasificador) "
                             "o con agreement.py validate.",
    "verifier_no_errors": "kairo:estimate, paso 5 (verificación independiente sobre el paquete --question).",
    "avisos_cerrados": "El investigador cierra los avisos en el panel Avisos o espera al fin del embargo.",
    "avisos_enviados": "El investigador envía los avisos pendientes y anota su fecha de embargo en el panel Avisos.",
}
AVISOS_CHECKS = ("avisos_cerrados", "avisos_enviados")


def _blank(v) -> bool:
    return sg._blank(v)


def _is_pilot(design: dict) -> bool:
    return design.get("citable", True) is False


def _is_exploratory(design: dict) -> bool:
    """A citable design whose plan is never frozen (exploratoria): estimated from abierta."""
    return not _is_pilot(design) and "plan_frozen" not in design["gate"]


def _placeholder_text(text: str) -> bool:
    """A section still empty or holding only the template's `<…>` text."""
    s = text.strip()
    return not s or (s.startswith("<") and s.endswith(">"))


def _plan_of(vault: Path, pdir: Path, design: dict, nid: str, linked: str) -> tuple[Path | None, str, str]:
    """(path, plan id, problem) of a node's plan: `linked_plan`, else the one plan naming it."""
    if not _blank(linked):
        path, why = sg.find_plan(vault, linked, proj=pdir)
        return path, linked, why
    hits = []
    for p in sorted((pdir / design["plan_folder"]).glob(f"{design['plan_prefix']}-*.md")):
        parsed = read_note(p)
        if parsed and str(parsed[0].get("question") or "").strip() == nid:
            hits.append((p, ps.scalar(parsed[0], "id") or p.stem.split(" ")[0]))
    if len(hits) == 1:
        return hits[0][0], hits[0][1], ""
    if hits:
        return None, "", f"varios planes nombran {nid}: {', '.join(h[1] for h in hits)}; enlaza el bueno en linked_plan"
    return None, "", ""


def study_nodes(vault: Path, pdir: Path) -> list[dict]:
    """Every study node of the project, design by design (send: never skipped)."""
    out = []
    for dname, design in _designs.load().items():
        for p in sorted((pdir / design["folder"]).glob(f"{design['prefix']}-*.md")):
            if is_flagged(p):
                continue
            parsed = read_note(p)
            if not parsed:
                continue
            fm, body = parsed
            nid = ps.scalar(fm, "id") or p.stem.split(" ")[0]
            if not re.match(rf"^{re.escape(design['prefix'])}-\d{{4}}$", nid):
                continue
            linked = str(fm.get("linked_plan") or "").strip()
            plan_path, plan_id, plan_problem = _plan_of(vault, pdir, design, nid, linked)
            plan_fm, plan_body = ({}, "")
            if plan_path is not None:
                pp = read_note(plan_path)
                plan_fm, plan_body = (pp[0], pp[1]) if pp else ({}, "")
            status = ps.scalar(fm, "status") or ""
            try:
                mode = avisos_mode(read_effective(plan_path)) if plan_path is not None else "cerrados"
            except PlanError:
                mode = "cerrados"
            out.append({
                "id": nid, "design_name": dname, "design": design, "pilot": _is_pilot(design),
                "exploratory": _is_exploratory(design), "avisos_mode": mode,
                "path": p, "fm": fm, "status": status,
                "terminal": status in design["terminal"],
                "linea_publicacion": str(fm.get("linea_publicacion") or "").strip().lower() in ("true", "sí", "si", "yes"),
                "units_dataset": "" if _blank(fm.get("units_dataset")) else str(fm.get("units_dataset")).strip(),
                "has_result": bool(_EST_BLOCK.search(section(body, "Resultado"))),
                "plan_id": plan_id, "plan_path": plan_path, "plan_problem": plan_problem,
                "plan_frozen": not _blank(plan_fm.get("frozen_at")) and not _blank(plan_fm.get("frozen_commit")),
                "plan_decision_blank": _placeholder_text(section(plan_body, "Decisión")) if plan_path else None,
            })
    return out


def _leads_to_publishable(n: dict) -> bool:
    d = n["design"]
    return (n["status"], d["publishable"]) in set(d["transitions"])


def _classifiers(n: dict) -> tuple[list[dict], str]:
    if n["plan_path"] is None:
        return [], ""
    try:
        return read_effective(n["plan_path"])["classifiers"], ""
    except PlanError as e:
        return [], str(e)


def _missing_validations(pdir: Path, n: dict) -> list[dict]:
    cls, _ = _classifiers(n)
    return [c for c in cls
            if not (pdir / "Datos" / "anotacion" / c["sample"] / f"validacion-{c['name']}.json").is_file()]


# ---- collections (the app's recolecciones; read-only) ----------------------

def collections_dir(override: Path | str | None = None) -> Path:
    if override:
        return Path(override)
    env = os.environ.get("KAIRO_COLLECTIONS_DIR")
    return Path(env) if env else Path.home() / ".kairo" / "collections"


def _progress(out_dir: object) -> dict:
    """The numbers of `<outDir>/progress.json` (top-level numeric fields only)."""
    if not isinstance(out_dir, str) or not out_dir.strip():
        return {}
    f = Path(out_dir) / "progress.json"
    try:
        if not f.is_file() or f.stat().st_size > 65536:
            return {}
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items()
            if isinstance(k, str) and _PROGRESS_KEY.match(k)
            and isinstance(v, (int, float)) and not isinstance(v, bool)}


def project_collections(pid: str, slug: str, base: Path | str | None = None) -> list[dict]:
    """This project's collections: the allowed state fields plus progress numbers. Never log.txt."""
    root = collections_dir(base)
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir()):
        f = d / "state.json"
        try:
            st = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None
        except (OSError, ValueError):
            continue
        if not isinstance(st, dict) or str(st.get("projectId") or "") not in (pid, slug):
            continue
        c = {k: st.get(k) for k in COLLECTION_FIELDS}
        c["id"] = str(c["id"] or d.name)
        c["progress"] = _progress(st.get("outDir"))
        out.append(c)
    out.sort(key=lambda c: str(c.get("startedAt") or ""), reverse=True)
    return out


def progress_text(p: dict) -> str:
    return ", ".join(f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}" for k, v in p.items())


def collection_items(cols: list[dict]) -> list[dict]:
    out = []
    for c in cols:
        tries = f"intento {c.get('attempts') or 0} de {1 + int(c.get('retries') or 0)}"
        prog = f"; progreso: {progress_text(c['progress'])}" if c["progress"] else ""
        if c.get("state") == "en_marcha":
            out.append(item("recoleccion_activa", "menor", c["id"],
                            f"La recolección {c['id']} está en marcha desde {c.get('startedAt') or '?'} ({tries}){prog}.",
                            "Nada que hacer; kairo:study puede vigilarla (study_brief.py --watch en un Monitor o /loop).",
                            [c["id"]]))
        elif c.get("state") in FAILED_COLLECTIONS:
            code = c.get("exitCode")
            out.append(item("recoleccion_fallida", "importante", c["id"],
                            f"La recolección {c['id']} terminó «{c['state']}»"
                            f"{f' con código de salida {code}' if code is not None else ''} "
                            f"tras {c.get('attempts') or 0} intento(s){prog}.",
                            "El investigador mira su registro (redactado) en Recolecciones y decide si la reanuda "
                            "(«Reanudar»); el agente nunca abre log.txt.", [c["id"]]))
    return out


# ---- the study checks -------------------------------------------------------

def _gate_items(n: dict, g: dict, skip_classifiers: set[str], skip_avisos: bool,
                skip_units: bool = False) -> tuple[list[dict], bool]:
    """gate_pendiente items, and whether plan_frozen reported data registered before the freeze."""
    out, data_first = [], False
    for row in g.get("checks") or []:
        for line in row["missing"]:
            if line == sg.DATA_BEFORE_FREEZE:
                data_first = True
                continue
            if row["name"] in AVISOS_CHECKS and skip_avisos:
                continue
            if any(line.startswith(f"clasificador {c}: falta ") for c in skip_classifiers):
                continue
            if skip_units and "units_dataset" in line:
                continue
            out.append(item("gate_pendiente", "importante", n["id"],
                            f"{n['id']} ({n['status']}), puerta de {n['design']['publishable']} — {row['name']}: {line}",
                            GATE_ACTIONS.get(row["name"], "Ver kairo:estimate, paso 6."), [n["id"]]))
    if not g.get("checks") and not g.get("ok"):
        out += [item("gate_pendiente", "importante", n["id"], f"{n['id']}: {line}",
                     "Ver kairo:estimate, paso 6.", [n["id"]]) for line in g.get("missing") or []]
    return out, data_first


def _data_first_item(n: dict) -> dict:
    return item("dataset_registrado_antes_del_congelado", "crítico", n["id"],
                f"{n['id']}: el dataset de unidades «{n['units_dataset']}» se registró antes de congelar el plan "
                f"{n['plan_id']}; la puerta de {n['design']['publishable']} no pasará nunca con él.",
                "Lo decide el investigador: una pregunta y un plan nuevos (kairo:plan-study), con los datos "
                "registrados después del nuevo congelado.", [n["id"], n["plan_id"]] if n["plan_id"] else [n["id"]])


def study(vault: Path, pdir: Path, pid: str, today: date, cols: list[dict]) -> tuple[list[dict], dict]:
    """(items, context). context: nodes, gates (by id), avisos, deadline — for study_brief.py."""
    nodes = study_nodes(vault, pdir)
    items: list[dict] = []
    gates: dict[str, dict] = {}
    measure = [n for n in nodes if not n["pilot"]]
    live_measure = [n for n in measure if n["status"] != "descartada"]
    collecting = [n for n in measure if not n["terminal"] and (
        n["status"] not in ("abierta", "") or (n["exploratory"] and n["has_result"]))]
    avisos, avisos_problem = sg.count_avisos(pdir, today)
    # `enviados` (the plan's choice) lets a running embargo pass; one `cerrados` node rules
    only_sent = bool(collecting) and all(n["avisos_mode"] == "enviados" for n in collecting)
    open_keys = ("pendiente", "sin_fecha", "desconocido") + (() if only_sent else ("embargo",))
    open_avisos = sum(avisos[k] for k in open_keys) if avisos else 0
    skip_avisos = False
    if collecting and (avisos_problem or open_avisos):
        sev = "importante" if any(_leads_to_publishable(n) for n in collecting) else "menor"
        if avisos:
            parts = [f"{avisos[k]} {lbl}" for k, lbl in (
                ("pendiente", "pendiente(s)"), ("sin_fecha", "enviado(s) sin fecha de embargo"),
                ("embargo", "enviado(s) con el embargo sin vencer"), ("desconocido", "con estado desconocido"))
                if avisos[k] and k in open_keys]
            msg = f"Avisos sin {'enviar' if only_sent else 'cerrar'}: {', '.join(parts)} (de {avisos['total']})."
        else:
            msg = f"Avisos: {avisos_problem}."
        action = GATE_ACTIONS["avisos_enviados" if only_sent else "avisos_cerrados"]
        items.append(item("avisos_sin_cerrar", sev, "Avisos/registro.json", msg, action
                          + " El agente nunca lee Avisos/.", [n["id"] for n in collecting]))
        skip_avisos = True

    for n in nodes:
        nid, st = n["id"], n["status"]
        if n["terminal"]:
            if n["pilot"] and st == n["design"]["publishable"] and not live_measure:
                items.append(item("piloto_explorado_sin_estudio", "importante", nid,
                                  f"El piloto {nid} está «{st}» y el proyecto no tiene ninguna pregunta del estudio.",
                                  f"{PLAN_STUDY}: abre la pregunta del estudio (Q) y su plan, nombrando el piloto. "
                                  f"Seguir o no lo decide el investigador con la ## Decisión de {n['plan_id'] or 'su plan'}.",
                                  [nid]))
            continue
        if n["pilot"]:
            if n["plan_path"] is None:
                items.append(item("piloto_sin_plan", "importante", nid,
                                  f"El piloto {nid} no tiene plan{': ' + n['plan_problem'] if n['plan_problem'] else ''}.",
                                  f"{PLAN_STUDY} («Un piloto antes del estudio»): redacta su plan MP-XXXX y enlázalo.",
                                  [nid]))
                continue
            if n["plan_decision_blank"]:
                items.append(item("piloto_sin_decision", "importante", nid,
                                  f"El plan {n['plan_id']} del piloto {nid} no tiene ## Decisión escrita.",
                                  f"Escribir con el investigador, antes de mirar resultados, qué decide el piloto y con "
                                  f"qué criterio ({PLAN_STUDY}).", [nid, n["plan_id"]]))
            if not n["has_result"]:
                items.append(item("piloto_sin_estimar", "menor", nid,
                                  f"El piloto {nid} ({st}) no tiene estimaciones en ## Resultado.",
                                  f"kairo:estimate sobre {nid} cuando su dataset esté registrado.", [nid]))
                continue
            if _leads_to_publishable(n):
                g = sst.gate(vault, nid)
                gates[nid] = g
                if g["ok"]:
                    items.append(item("piloto_por_cerrar", "importante", nid,
                                      f"El piloto {nid} tiene estimaciones y su puerta se cumple, pero sigue «{st}».",
                                      f"kairo:estimate: aplicar --to {n['design']['publishable']} y presentar la "
                                      f"## Decisión de {n['plan_id']} al investigador.", [nid]))
                else:
                    items += _gate_items(n, g, set(), skip_avisos)[0]
            continue

        if n["exploratory"]:  # citable with its label, never frozen: estimated from abierta
            d = n["design"]
            if n["plan_path"] is None:
                items.append(item("pregunta_sin_plan", "importante", nid,
                                  f"{nid} está abierta y no tiene plan{': ' + n['plan_problem'] if n['plan_problem'] else ''}.",
                                  f"{PLAN_STUDY} («Una pregunta exploratoria»): redacta su plan "
                                  f"{d['plan_prefix']}-XXXX y enlázalo en linked_plan.", [nid]))
                continue
            if not n["has_result"]:
                items.append(item("exploratoria_sin_estimar", "menor", nid,
                                  f"La pregunta exploratoria {nid} ({st}) no tiene estimaciones en ## Resultado.",
                                  f"kairo:estimate sobre {nid} cuando su dataset esté registrado.", [nid]))
                continue
            if _leads_to_publishable(n):
                g = sst.gate(vault, nid)
                gates[nid] = g
                if g["ok"]:
                    items.append(item("exploratoria_por_cerrar", "importante", nid,
                                      f"La pregunta exploratoria {nid} tiene estimaciones y su puerta se cumple, "
                                      f"pero sigue «{st}».",
                                      f"kairo:estimate: aplicar --to {d['publishable']}; sus cifras se citan con la "
                                      f"etiqueta «{d.get('etiqueta', 'exploratorio')}».", [nid]))
                else:
                    items += _gate_items(n, g, set(), skip_avisos, skip_units=not n["units_dataset"])[0]
            continue

        # a study question
        if st == "abierta":
            if n["plan_path"] is None:
                items.append(item("pregunta_sin_plan", "importante", nid,
                                  f"{nid} está abierta y no tiene plan{': ' + n['plan_problem'] if n['plan_problem'] else ''}.",
                                  f"{PLAN_STUDY}: redactar el plan con el investigador y congelarlo.", [nid]))
            else:
                _, err = _classifiers(n)
                why = f" y sus bloques no son válidos ({err})" if err else ""
                items.append(item("plan_sin_congelar", "importante", nid,
                                  f"El plan {n['plan_id']} de {nid} está en borrador{why}.",
                                  f"{PLAN_STUDY}: " + ("corregir el plan y " if err else "")
                                  + "congelarlo con freeze_plan.py cuando el investigador dé el visto bueno.",
                                  [nid, n["plan_id"]]))
            continue
        missing_val = _missing_validations(pdir, n) if n["plan_frozen"] else []
        publishable_next = _leads_to_publishable(n)
        for c in missing_val:
            items.append(item("clasificador_sin_validar", "importante" if publishable_next else "menor", nid,
                              f"El clasificador {c['name']} del plan {n['plan_id']} ({nid}) no tiene "
                              f"Datos/anotacion/{c['sample']}/validacion-{c['name']}.json.",
                              GATE_ACTIONS["classifiers_validated"], [nid, n["plan_id"]]))
        if publishable_next:
            if not n["units_dataset"]:
                items.append(item("en_recoleccion_sin_units_dataset", "importante", nid,
                                  f"{nid} está «{st}» sin units_dataset.",
                                  "Cuando la recolección termine: registrar el dataset (después del congelado) y "
                                  "kairo:estimate, que pregunta al investigador cuál es y lo anota.", [nid]))
            g = sst.gate(vault, nid)
            gates[nid] = g
            gi, data_first = _gate_items(n, g, {c["name"] for c in missing_val}, skip_avisos,
                                         skip_units=not n["units_dataset"])
            if data_first:
                items.append(_data_first_item(n))
            items += gi
        else:
            if (st, "en_recoleccion") in set(n["design"]["transitions"]):
                items.append(item("plan_congelado_sin_recoleccion", "importante", nid,
                                  f"{nid} tiene el plan {n['plan_id']} congelado y la recolección no ha empezado.",
                                  START_COLLECTION, [nid, n["plan_id"]]))
            if n["units_dataset"]:
                lines = sg.plan_frozen(vault, n["path"], n["fm"])
                if sg.DATA_BEFORE_FREEZE in lines:
                    items.append(_data_first_item(n))

    deadline = deadline_info(pdir, today)
    items = deadline_items(deadline, nodes) + collection_items(cols) + items
    return items, {"nodes": nodes, "gates": gates, "avisos": avisos, "avisos_problem": avisos_problem,
                   "deadline": deadline}


def deadline_info(pdir: Path, today: date) -> dict | None:
    raw = ps.scalar(ps.hub_fm(pdir), "deadline")
    if raw is None:
        return None
    try:
        d = date.fromisoformat(raw[:10])
    except ValueError:
        return {"raw": raw, "date": None, "days_left": None}
    return {"raw": raw, "date": d.isoformat(), "days_left": (d - today).days}


def deadline_items(dl: dict | None, nodes: list[dict]) -> list[dict]:
    if dl is None:
        return []
    if dl["date"] is None:
        return [item("plazo", "menor", "_hub.md:deadline", f"`deadline` ({dl['raw']}) no es una fecha AAAA-MM-DD.",
                     "Corregir `deadline` en _hub.md.", [])]
    pending = [n["id"] for n in nodes if not n["pilot"] and n["linea_publicacion"] and not n["terminal"]]
    days = dl["days_left"]
    if not pending or days >= 21:
        return []
    sev = "crítico" if days < 7 else "importante"
    when = (f"El plazo ({dl['date']}) venció hace {-days} día(s)" if days < 0
            else f"Quedan {days} día(s) para el plazo ({dl['date']})")
    return [item("plazo", sev, "_hub.md:deadline",
                 f"{when} y {len(pending)} pregunta(s) de la línea de publicación sin terminar: {', '.join(pending)}.",
                 "Priorizar esas preguntas (los pasos de abajo); mover el plazo lo decide el investigador.", pending)]


# ---------------------------------------------------------------------------

def analyse(vault: Path, project: str, today: date | None = None,
            collections: Path | str | None = None) -> tuple[dict, dict]:
    """(the next_steps result, the study context study_brief.py also shows)."""
    today = today or date.today()
    pdir, pid = ps.resolve_project(vault, project)
    hyps, sh = ps.hypotheses(pdir)
    exps, se = ps.experiments(pdir)
    cols = project_collections(pid, pdir.name, collections)
    study_items, ctx = study(vault, pdir, pid, today, cols)
    ctx["collections"] = cols
    items = (unadjudicated(hyps, exps) + hub_placeholders(pdir) + sota_deferred(pdir)
             + frontier(vault, pdir, pid, hyps) + worth(hyps) + study_items)
    items.sort(key=lambda i: SEVERITY_ORDER.get(i["severity"], 9))
    counts = {s: sum(1 for i in items if i["severity"] == s) for s in SEVERITY_ORDER}
    summary = [f"Siguientes pasos de {pid} ({pdir.name}): {len(items)} pendiente(s)"
               + (f" — {', '.join(f'{n} {s}' for s, n in counts.items() if n)}" if items else ".")]
    for i in items:
        summary.append(f"  [{i['severity']}] {i['message']}\n      → {i['action']}")
    if sh or se:
        summary.append(f"  ({sh + se} nota(s) send: never omitidas)")
    res = {"tool": TOOL, "project": pid, "slug": pdir.name,
           "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "has_frontier": ps.frontier_paths(pdir)["json"].is_file(),
           "counts": counts, "items": items, "omitidas_send_never": sh + se, "resumen": summary}
    ctx["pdir"] = pdir
    return res, ctx


def run(vault: Path, project: str, today: date | None = None, collections: Path | str | None = None) -> dict:
    return analyse(vault, project, today, collections)[0]


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--collections-dir", default=None,
                    help="the app's collections folder (default: KAIRO_COLLECTIONS_DIR or ~/.kairo/collections)")
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        res = run(Path(a.vault), a.project, collections=a.collections_dir)
    except ps.InputError as exc:
        print(f"next_steps: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(res, ensure_ascii=False, indent=2) if a.json else "\n".join(res["resumen"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
