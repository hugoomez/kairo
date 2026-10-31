#!/usr/bin/env python3
"""The frontier map of one Kairo project: validate it, compute it, render it.

    frontier.py check    --vault <vault> --project <PROJ-XXX|slug>
    frontier.py build    --vault <vault> --project <PROJ-XXX|slug>
    frontier.py stale    --vault <vault> --project <PROJ-XXX|slug>
    frontier.py cell     --vault <vault> --project <PROJ-XXX|slug> M1:fila|col [--con-citas]
    frontier.py taxonomy --vault <vault> --project <PROJ-XXX|slug>
    frontier.py summary  --vault <vault> --project <PROJ-XXX|slug>
    frontier.py sample   --vault <vault> --project <PROJ-XXX|slug> [--n 5] [--seed S]
    frontier.py save-extraction --vault <vault> --project <PROJ-XXX|slug> --reply <file> [--date AAAA-MM-DD]

Everything lives in the vault, in `Projects/<slug>/Frontera/`:

- `mapa.yaml` — the source of truth, written by the `kairo:frontier-map` skill:
  `meta`, `ejes` (the approved taxonomy: `{<eje>: {nombre, regla, multivalor,
  valores: {<valor>: <descripción>}}}`), `papers` (per-paper overrides),
  `nuestro_proyecto.componentes`, `ideas`, `relaciones`, `matrices`, `frontera`,
  `huecos`, `hipotesis_clave`, `posicionamiento`.
- `extracciones/P-XXXX.json` — what the `frontier-extractor` agent read in one
  paper: values per axis with verbatim quotes and locators, results,
  limitations, ideas, and `taxonomia_sha` (sha256 of the canonical `ejes`).
  `save-extraction` writes them from the agent's reply, keeping only what
  passes the quote check. An extraction whose `taxonomia_sha` differs from the
  current one is stale and is left out of the map until re-extracted.
- The views `build` writes: `matriz.md`, `frontera.md`, `huecos.md`,
  `posicionamiento.md`, `mapa.json` (what the Kairo app reads) and `mapa.html`.
  Never edited by hand; deterministic (the same inputs give the same bytes).

A paper's entry in the map is its current extraction overlaid by its
`papers:` entry in mapa.yaml (`ejes` and `notas_ejes` merge per axis; any other
key replaces). The «nuestro» layer is computed from the project's notes, never
edited: each hypothesis (`Hipotesis/*.md`) with `frontier_cells: [M1:fila|col]`
and its `status`, and the experiments (`Experimentos/*.md`) whose `hypothesis:`
is one of them. Our work never counts as coverage.

Validation (`check`, and `build` before writing; any error → exit 1 and nothing
is written): every axis value is defined; each multivalued axis is a list and
each single-valued axis one value; ideas, papers, references and cells exist;
every relation has an allowed type and cites a paper; every number in a
result's `valor` appears in its `cita`; **every quote of a paper is the paper's
own text under its locator** (`check_quotes.check_quote`, the fresh-verifier's
resolver); a `fuente_repo` quote is in that file of the project's `code_repo`
(skipped with a warning when the hub has none); every empty matrix cell has a
mark (`hueco`, `sin_sentido`, `no_buscado`) with a reason; every gap says
whether it is cheap and how it fits.

`cell` prints one cell's context as JSON (axes, covering papers, mark, reason,
our hypotheses, related gaps) — what the app and the skills seed a chat or a
cycle with. Locators only; `--con-citas` adds the quoted text. `taxonomy`
prints the approved axes and ideas with their sha (the extractor's input);
`save-extraction` checks a frontier-extractor reply and writes its
extractions; `sample` draws covered cells at random (seeded) with what to
check in one covering paper — the skill's spot check with paper-reader;
`summary` lists what the map knows of each paper with no quoted text, and the
empty cells still unmarked.

Exit codes: 0 ok · 1 the map does not validate (nothing written) · 2 bad input.
Requires PyYAML (`pip install pyyaml`); otherwise standard library only.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "papers"))
sys.path.insert(0, str(HERE.parent / "citations"))
import check_quotes as cq  # noqa: E402
import vaultnotes as vn  # noqa: E402
from verifier_packet import find_paper, fm_scalar, read_text, split_frontmatter  # noqa: E402  (path set by check_quotes)

try:
    import yaml
except ImportError:  # pragma: no cover - reported at run time
    yaml = None

TOOL = "kairo/frontier@1.0.0"
CONFIANZAS = ("leido_completo", "solo_resumen", "insight_kairo", "cita_sin_verificar")
TIPOS_REL = ("se_basa_en", "compite_con", "se_combina_con", "contradice")
MARCAS = ("hueco", "sin_sentido", "no_buscado")
NIVELES_ENCAJE = {
    "directo": "es la apuesta misma",
    "prerrequisito": "hace falta para poder afirmar algo de la apuesta",
    "diseño": "decide cómo construir la apuesta",
    "complementario": "se aplica a lo que produce la apuesta",
    "lejano": "no afecta a la apuesta actual",
}
NO_ESTA = "no está en el texto"
_CELDA = re.compile(r"^([A-Za-z0-9_-]+):([^|]+)\|(.+)$")


class Refused(Exception):
    """Bad input: exit 2."""


# ---------------------------------------------------------------- the project

class Proyecto:
    def __init__(self, vault: Path, slug: str, hub_fm: list[str]):
        self.vault = vault
        self.slug = slug
        self.dir = vault / "Projects" / slug
        self.id = vn.fm_get(hub_fm, "id") or slug
        self.nombre = vn.fm_get(hub_fm, "name") or slug
        repo = vn.fm_get(hub_fm, "code_repo")
        self.code_repo = Path(repo) if repo and not repo.startswith("<") and repo.lower() != "none" else None
        self.frontera = self.dir / "Frontera"


def _hub_fm(path: Path) -> list[str]:
    parsed = vn.split_frontmatter(path.read_text(encoding="utf-8"))
    return parsed[0] if parsed else []


def resolver_proyecto(vault: Path, arg: str) -> Proyecto:
    projects = vault / "Projects"
    direct = projects / arg / "_hub.md"
    if direct.is_file():
        return Proyecto(vault, arg, _hub_fm(direct))
    for hub in sorted(projects.glob("*/_hub.md")):
        fm = _hub_fm(hub)
        if vn.fm_get(fm, "id") == arg:
            return Proyecto(vault, hub.parent.name, fm)
    raise Refused(f"no hay ningún proyecto «{arg}» en {projects} (ni carpeta ni id de un _hub.md)")


# ---------------------------------------------------------------- inputs

def _normalizar(v):
    """YAML dates → ISO strings, recursively, so JSON and hashes are stable."""
    if isinstance(v, dict):
        return {str(k): _normalizar(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_normalizar(x) for x in v]
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    return v


def cargar_yaml(path: Path) -> dict:
    if yaml is None:
        raise Refused("PyYAML no está instalado: pip install pyyaml")
    if not path.is_file():
        raise Refused(f"no existe {path}: la fase 1 de kairo:frontier-map lo crea con la taxonomía aprobada")
    try:
        datos = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise Refused(f"{path.name} no es YAML válido: {e}") from e
    if not isinstance(datos, dict):
        raise Refused(f"{path.name} debe ser un diccionario")
    return _normalizar(datos)


def sha_taxonomia(ejes: dict) -> str:
    canon = json.dumps(_normalizar(ejes or {}), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def cargar_extracciones(proy: Proyecto) -> dict[str, dict]:
    out = {}
    carpeta = proy.frontera / "extracciones"
    for f in sorted(carpeta.glob("P-*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        pid = d.get("paper") or f.stem
        out[pid] = d
    return out


def _fm_list(fm: list[str], key: str) -> list[str]:
    raw = vn.fm_raw(fm, key)
    if raw:
        return vn.parse_flow_list(raw)
    out, inside = [], False
    for ln in fm:
        if re.match(rf"^{re.escape(key)}:\s*$", ln):
            inside = True
            continue
        if inside:
            m = re.match(r"^\s+-\s*(.+?)\s*$", ln)
            if not m:
                break
            out.append(m.group(1).strip("'\""))
    return out


def _nested(fm: list[str], parent: str, key: str) -> str | None:
    inside = False
    for ln in fm:
        if re.match(rf"^{re.escape(parent)}:\s*$", ln):
            inside = True
            continue
        if inside:
            if ln and not ln[0].isspace():
                break
            m = re.match(rf"^\s+{re.escape(key)}:\s*(.*?)\s*$", ln)
            if m:
                v = vn._strip_comment(m.group(1)).strip().strip("'\"")
                return v if v and not v.startswith("<") else None
    return None


def _primera_linea(body: str, seccion: str) -> str | None:
    m = re.search(rf"^## {re.escape(seccion)}\s*\n(.*?)(?=^## |\Z)", body, re.M | re.S)
    if not m:
        return None
    for ln in m.group(1).split("\n"):
        ln = ln.strip()
        if ln and not ln.startswith(("<", ">", "<!--")):
            return ln[:200]
    return None


def notas_nuestras(proy: Proyecto) -> tuple[list[dict], list[dict]]:
    """(hypotheses, experiments) of the project, from their frontmatter."""
    hips, exps = [], []
    for f in sorted((proy.dir / "Hipotesis").glob("H-*.md")):
        text = f.read_text(encoding="utf-8")
        parsed = vn.split_frontmatter(text)
        if not parsed:
            continue
        fm, body = parsed
        hips.append({"id": vn.note_id(f, fm), "status": vn.fm_get(fm, "status") or "desconocido",
                     "frontier_cells": _fm_list(fm, "frontier_cells"),
                     "updated": vn.fm_get(fm, "updated"),
                     "titulo": vn.fm_get(fm, "title") or _primera_linea(body, "Claim")})
    for f in sorted((proy.dir / "Experimentos").glob("E-*.md")):
        parsed = vn.split_frontmatter(f.read_text(encoding="utf-8"))
        if not parsed:
            continue
        fm, _ = parsed
        exps.append({"id": vn.note_id(f, fm), "hipotesis": vn.fm_get(fm, "hypothesis"),
                     "status": vn.fm_get(fm, "status") or "desconocido",
                     "veredicto": _nested(fm, "result", "verdict"), "updated": vn.fm_get(fm, "updated")})
    return hips, exps


def meta_paper(vault: Path, pid: str) -> dict:
    """Frontmatter facts of one paper note (never its text)."""
    path = find_paper(str(vault), pid)
    if not path:
        return {"existe": False}
    fm, body = split_frontmatter(read_text(path))
    return {"existe": True, "ruta": Path(path).relative_to(vault).as_posix(),
            "titulo": fm_scalar(fm, "title"), "anio": fm_scalar(fm, "year"),
            "fulltext": cq.has_fulltext(body), "projects": cq.projects_of(fm)}


# ---------------------------------------------------------------- checks

_ID = re.compile(r"\b(?:P|H|E|C)-\d{3,5}\b")
_DIM = re.compile(r"\b[23]D\b")
_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _normalizar_cita(cita: str, repo: bool) -> str:
    if repo:
        return cita.replace(",", ".")
    return re.sub(r"(\d),(\d{3})(?!\d)", r"\1\2", cita)


def cifras_ausentes(valor: str, cita: str, repo: bool = False) -> list[str]:
    """Numbers of `valor` that are not in `cita` (ids and 2D/3D aside)."""
    limpio = _DIM.sub(" ", _ID.sub(" ", str(valor)))
    cita_n = _normalizar_cita(str(cita), repo)
    return [n for n in (t.replace(",", ".") for t in _NUM.findall(limpio)) if n not in cita_n]


_CACHE_CITAS: dict[tuple[str, str, str, str], str | None] = {}


def problema_cita(vault: Path, paper: str, loc: str, cita: str) -> str | None:
    """None when `cita` is the paper's own text under `loc`; else the reason."""
    quote = cq.ws(str(cita)).strip(cq._OPEN_CLOSE).strip()
    key = (str(vault), paper, str(loc), quote)
    if key not in _CACHE_CITAS:
        r = cq.check_quote(str(vault), {"raw": "", "paper": paper, "locator": str(loc), "quote": quote,
                                        "error": None}, None)
        _CACHE_CITAS[key] = None if r["ok"] else r["reason"]
    return _CACHE_CITAS[key]


def parse_celda(celda: str) -> tuple[str, str, str] | None:
    m = _CELDA.match(str(celda).strip())
    return (m.group(1), m.group(2), m.group(3)) if m else None


# ---------------------------------------------------------------- the map

class Mapa:
    def __init__(self, proy: Proyecto, datos: dict, extracciones: dict[str, dict] | None = None,
                 hipotesis: list[dict] | None = None, experimentos: list[dict] | None = None):
        self.proy = proy
        self.d = datos
        self.errores: list[str] = []
        self.avisos: list[str] = []
        self.ejes: dict = datos.get("ejes") or {}
        self.sha = sha_taxonomia(self.ejes)
        self.extracciones = extracciones or {}
        self.hipotesis = hipotesis or []
        self.experimentos = experimentos or []
        self.citas_total = 0
        self.citas_ok = 0
        self.vigentes = {p: e for p, e in self.extracciones.items() if e.get("taxonomia_sha") == self.sha}
        self.obsoletas = sorted(p for p, e in self.extracciones.items() if e.get("taxonomia_sha") != self.sha)
        self.papers, self.origen = self._componer_papers()
        self.meta_papers = {pid: meta_paper(proy.vault, pid) for pid in self.papers}
        nuestro = datos.get("nuestro_proyecto") or {}
        self.componentes = {c["id"]: c for c in (nuestro.get("componentes") or []) if isinstance(c, dict) and "id" in c}
        self.ideas = {i["id"]: i for i in (datos.get("ideas") or []) if isinstance(i, dict) and "id" in i}
        self.matrices = [m for m in (datos.get("matrices") or []) if isinstance(m, dict)]
        self.resultados = self._indexar_resultados()
        self._calc: dict[str, dict] = {}
        self.niveles = NIVELES_ENCAJE

    # ---------- papers: extraction overlaid by mapa.yaml ----------

    def _desde_extraccion(self, pid: str, ex: dict) -> dict:
        p: dict = {"id": pid, "ejes": {}, "notas_ejes": {}, "evidencia": []}
        for eje, info in (ex.get("ejes") or {}).items():
            info = info or {}
            valores = [v for v in (info.get("valores") or []) if v]
            if valores:
                multi = (self.ejes.get(eje) or {}).get("multivalor", True)
                p["ejes"][eje] = valores if multi or len(valores) > 1 else valores[0]
            if info.get("nota"):
                p["notas_ejes"][eje] = info["nota"]
            for c in info.get("citas") or []:
                if c.get("cita") and c.get("loc"):
                    p["evidencia"].append({"eje": eje, "cita": c["cita"], "loc": c["loc"]})
        res = []
        for i, r in enumerate(ex.get("resultados") or [], 1):
            r = dict(r)
            r.setdefault("id", f"r{i}")
            res.append(r)
        p["resultados"] = res
        p["limitaciones"] = list(ex.get("limitaciones") or [])
        p["ideas"] = list(ex.get("ideas") or [])
        p["no_leido"] = list(ex.get("no_leido") or [])
        return p

    def _componer_papers(self) -> tuple[dict, dict]:
        papers, origen = {}, {}
        entradas = [e for e in (self.d.get("papers") or []) if isinstance(e, dict) and e.get("id")]
        orden = [e["id"] for e in entradas] + sorted(p for p in self.vigentes if p not in {e["id"] for e in entradas})
        por_id = {e["id"]: e for e in entradas}
        for pid in orden:
            base = self._desde_extraccion(pid, self.vigentes[pid]) if pid in self.vigentes else {"id": pid}
            entrada = por_id.get(pid, {})
            for k, v in entrada.items():
                if k in ("ejes", "notas_ejes") and isinstance(v, dict):
                    base[k] = {**(base.get(k) or {}), **v}
                else:
                    base[k] = v
            if pid in self.vigentes:
                origen[pid] = "extraccion" if not entrada else "extraccion+mapa"
            elif pid in self.extracciones:
                origen[pid] = "mapa (extracción obsoleta)"
            else:
                origen[pid] = "mapa"
            papers[pid] = base
        return papers, origen

    def _confianza(self, pid: str) -> str:
        p = self.papers[pid]
        if p.get("confianza"):
            return p["confianza"]
        return "leido_completo" if self.meta_papers.get(pid, {}).get("fulltext") else "solo_resumen"

    def _indexar_resultados(self) -> dict:
        idx = {}
        for pid, p in self.papers.items():
            for r in p.get("resultados") or []:
                if isinstance(r, dict):
                    idx[f"{pid}#{r.get('id')}"] = (pid, r)
        for cid, c in self.componentes.items():
            for r in c.get("resultados") or []:
                if isinstance(r, dict):
                    idx[f"NUESTRO:{cid}#{r.get('id')}"] = (f"NUESTRO:{cid}", r)
        return idx

    def nombre(self, pid: str) -> str:
        corto = (self.papers.get(pid) or {}).get("nombre_corto")
        return f"{pid} ({corto})" if corto else pid

    @staticmethod
    def valores_eje(item: dict, eje: str) -> list:
        v = (item.get("ejes") or {}).get(eje)
        if v is None:
            return []
        return v if isinstance(v, list) else [v]

    def desc_valor(self, eje: str, valor: str) -> str:
        return str(((self.ejes.get(eje) or {}).get("valores") or {}).get(valor, ""))

    def nombre_eje(self, eje: str) -> str:
        return str((self.ejes.get(eje) or {}).get("nombre") or eje)

    # ---------- our layer ----------

    def nuestros_en(self, celda: str, m: dict, fila: str, col: str) -> list[dict]:
        out = []
        exps = {}
        for e in self.experimentos:
            exps.setdefault(e.get("hipotesis"), []).append(e)
        for h in self.hipotesis:
            if celda in h["frontier_cells"]:
                out.append({"id": h["id"], "tipo": "hipotesis", "status": h["status"]})
                for e in exps.get(h["id"], []):
                    item = {"id": e["id"], "tipo": "experimento", "status": e["status"], "hipotesis": h["id"]}
                    if e.get("veredicto"):
                        item["veredicto"] = e["veredicto"]
                    out.append(item)
        fe, ce = m["filas"]["eje"], m["columnas"]["eje"]
        for c in self.componentes.values():
            if fila in self.valores_eje(c, fe) and col in self.valores_eje(c, ce):
                out.append({"id": c["id"], "tipo": "componente", "status": c.get("estado", "desconocido"),
                            "nombre": c.get("nombre", c["id"])})
        return out

    def atacado_por(self, celdas: list[str]) -> list[dict]:
        return [{"id": h["id"], "status": h["status"]} for h in self.hipotesis
                if set(h["frontier_cells"]) & set(celdas)]

    # ---------- matrices ----------

    def _filtro(self, m: dict) -> tuple[str, set] | None:
        f = m.get("filtro")
        if isinstance(f, dict) and f.get("eje"):
            return f["eje"], set(f.get("valores") or [])
        if m.get("filtro_aportacion"):
            return "aportacion", set(m["filtro_aportacion"])
        return None

    def calcular_matriz(self, m: dict) -> dict:
        mid = m.get("id")
        if mid in self._calc:
            return self._calc[mid]
        fe, ce = m["filas"]["eje"], m["columnas"]["eje"]
        filtro = self._filtro(m)
        defecto = m.get("por_defecto") or {}
        celdas = {}
        for fila in m["filas"]["valores"]:
            for col in m["columnas"]["valores"]:
                cubren = []
                for pid, p in self.papers.items():
                    if filtro and not filtro[1] & set(self.valores_eje(p, filtro[0])):
                        continue
                    if fila in self.valores_eje(p, fe) and col in self.valores_eje(p, ce):
                        cubren.append(pid)
                cid = f"{mid}:{fila}|{col}"
                declarada = (m.get("vacias") or {}).get(f"{fila}|{col}")
                por_def = ((defecto.get("columna") or {}).get(col) or (defecto.get("fila") or {}).get(fila))
                celdas[(fila, col)] = {"id": cid, "papers": cubren, "nuestros": self.nuestros_en(cid, m, fila, col),
                                       "marca_declarada": declarada, "marca": declarada or por_def}
        self._calc[mid] = celdas
        return celdas

    def matriz(self, mid: str) -> dict | None:
        return next((m for m in self.matrices if m.get("id") == mid), None)

    def estado_celda(self, celda: str) -> dict | None:
        p = parse_celda(celda)
        if not p:
            return None
        m = self.matriz(p[0])
        if not m or not self._matriz_bien_formada(m):
            return None
        return self.calcular_matriz(m).get((p[1], p[2]))

    @staticmethod
    def _matriz_bien_formada(m: dict) -> bool:
        try:
            return bool(m["id"] and m["filas"]["eje"] and m["columnas"]["eje"]
                        and list(m["filas"]["valores"]) and list(m["columnas"]["valores"]))
        except (KeyError, TypeError):
            return False

    def todas_las_celdas(self) -> list[str]:
        return [e["id"] for m in self.matrices if self._matriz_bien_formada(m)
                for e in self.calcular_matriz(m).values()]

    # ---------- staleness ----------

    def marcas_cubiertas(self) -> list[dict]:
        out = []
        for m in self.matrices:
            if not self._matriz_bien_formada(m):
                continue
            for e in self.calcular_matriz(m).values():
                if e["papers"] and e["marca_declarada"]:
                    out.append({"celda": e["id"], "marca": (e["marca_declarada"] or {}).get("marca"),
                                "papers": e["papers"]})
        return out

    def huecos_cubiertos(self) -> list[dict]:
        out = []
        for h in self.d.get("huecos") or []:
            for celda in h.get("celdas") or []:
                e = self.estado_celda(celda)
                if e and e["papers"]:
                    out.append({"hueco": h.get("id"), "celda": celda, "papers": e["papers"]})
        return out

    # ---------- validation ----------

    def _err(self, msg: str):
        self.errores.append(msg)

    def _cita(self, quien: str, paper: str, loc, cita):
        if not cita:
            return
        self.citas_total += 1
        if not loc:
            self._err(f"{quien}: cita sin `loc`")
            return
        why = problema_cita(self.proy.vault, paper, str(loc), str(cita))
        if why:
            self._err(f"{quien}: la cita no es texto del paper — {why}")
        else:
            self.citas_ok += 1

    def validar(self, citas: bool = True):
        self._validar_taxonomia()
        for pid, p in self.papers.items():
            mp = self.meta_papers[pid]
            de = f" (de extracciones/{pid}.json)" if self.origen[pid].startswith("extraccion") else ""
            if not mp["existe"]:
                self._err(f"{pid}: no hay nota en Papers/")
                continue
            if self.proy.id not in mp["projects"]:
                self.avisos.append(f"{pid}: la nota no lista {self.proy.id} en `projects:`")
            if self._confianza(pid) not in CONFIANZAS:
                self._err(f"{pid}: confianza «{p.get('confianza')}» no válida ({', '.join(CONFIANZAS)})")
            if p.get("no_leido"):
                self.avisos.append(f"{pid}: secciones sin leer en la extracción: {', '.join(map(str, p['no_leido']))}")
            if not p.get("ejes"):
                self.avisos.append(f"{pid}: sin valores en ningún eje; no cubre ninguna casilla")
            self._validar_ejes(pid, p, de)
            for idea in p.get("ideas") or []:
                if idea not in self.ideas:
                    self._err(f"{pid}: idea «{idea}» no está en `ideas`{de}")
            if citas:
                for ev in p.get("evidencia") or []:
                    self._cita(f"{pid} · evidencia del eje {ev.get('eje')}{de}", ev.get("fuente", pid), ev.get("loc"),
                               ev.get("cita"))
            for r in p.get("resultados") or []:
                self._validar_resultado(f"{pid}#{r.get('id')}{de}", r, pid, citas)
            for i, lim in enumerate(p.get("limitaciones") or [], 1):
                if lim.get("origen") not in ("autores", "deducida"):
                    self._err(f"{pid}: limitación {i} con origen «{lim.get('origen')}» (autores | deducida){de}")
                if lim.get("origen") == "autores" and not lim.get("cita"):
                    self._err(f"{pid}: limitación {i} de los autores sin cita{de}")
                if citas and lim.get("cita"):
                    self._cita(f"{pid} · limitación {i}{de}", lim.get("fuente", pid), lim.get("loc"), lim.get("cita"))
        for p in self.obsoletas:
            if p not in self.papers:
                self.avisos.append(f"{p}: su extracción es de otra taxonomía; queda fuera del mapa hasta re-extraerla")
        for cid, c in self.componentes.items():
            quien = f"NUESTRO:{cid}"
            self._validar_ejes(quien, c, "")
            for idea in c.get("ideas") or []:
                if idea not in self.ideas:
                    self._err(f"{quien}: idea «{idea}» no está en `ideas`")
            for r in c.get("resultados") or []:
                self._validar_resultado(f"{quien}#{r.get('id')}", r, None, citas)
            for lim in c.get("limitaciones") or []:
                if lim.get("fuente_repo"):
                    self._validar_cita_repo(f"{quien} limitación", lim)
        for rel in self.d.get("relaciones") or []:
            etiqueta = f"relación {rel.get('de')} {rel.get('tipo')} {rel.get('a')}"
            if rel.get("tipo") not in TIPOS_REL:
                self._err(f"{etiqueta}: tipo no permitido ({', '.join(TIPOS_REL)})")
            for extremo in (rel.get("de"), rel.get("a")):
                if extremo not in self.ideas:
                    self._err(f"{etiqueta}: idea «{extremo}» no existe")
            if not rel.get("citas"):
                self._err(f"{etiqueta}: no cita ningún paper")
            for c in rel.get("citas") or []:
                if c.get("paper") not in self.papers:
                    self._err(f"{etiqueta}: paper «{c.get('paper')}» no está en el mapa")
                elif citas and c.get("cita"):
                    self._cita(etiqueta, c["paper"], c.get("loc"), c["cita"])
        for f in self.d.get("frontera") or []:
            for ref in f.get("refs") or []:
                if ref not in self.resultados:
                    self._err(f"frontera {f.get('id')}: referencia «{ref}» no existe")
            if f.get("mejor") and f["mejor"] not in (f.get("refs") or []):
                self._err(f"frontera {f.get('id')}: `mejor` no está en `refs`")
        for h in self.d.get("huecos") or []:
            hid = h.get("id")
            for campo in ("titulo", "por_que", "tarea"):
                if not str(h.get(campo) or "").strip():
                    self._err(f"hueco {hid}: falta `{campo}`")
            barato = h.get("barato") or {}
            if not isinstance(barato.get("si"), bool) or not barato.get("por_que"):
                self._err(f"hueco {hid}: falta `barato` (si: true/false y por_que)")
            encaje = h.get("encaje") or {}
            if encaje.get("nivel") not in NIVELES_ENCAJE or not encaje.get("texto"):
                self._err(f"hueco {hid}: `encaje.nivel` debe ser uno de {', '.join(NIVELES_ENCAJE)} y llevar texto")
            for ev in h.get("evidencia") or []:
                ref = ev.get("ref")
                if ref and ref not in self.papers and ref not in self.resultados and ref != "meta":
                    self._err(f"hueco {hid}: referencia «{ref}» no existe")
                if citas and ev.get("cita") and ref in self.papers:
                    self._cita(f"hueco {hid} · evidencia", ref, ev.get("loc"), ev["cita"])
            for celda in h.get("celdas") or []:
                if self.estado_celda(celda) is None:
                    self._err(f"hueco {hid}: casilla «{celda}» no existe")
        hc = self.d.get("hipotesis_clave") or {}
        for lado in ("a_favor", "en_contra"):
            for ev in hc.get(lado) or []:
                ref = ev.get("ref")
                if ref and ref not in self.papers and ref not in self.resultados and ref != "meta":
                    self._err(f"hipotesis_clave.{lado}: referencia «{ref}» no existe")
                if citas and ev.get("cita") and ref in self.papers:
                    self._cita(f"hipotesis_clave.{lado}", ref, ev.get("loc"), ev["cita"])
        self._validar_matrices()
        celdas = set(self.todas_las_celdas())
        for h in self.hipotesis:
            for c in h["frontier_cells"]:
                if c not in celdas:
                    self.avisos.append(f"{h['id']}: frontier_cells nombra «{c}», que no es una casilla del mapa")

    def _validar_taxonomia(self):
        if not self.ejes:
            self._err("`ejes` está vacío: falta la taxonomía aprobada")
        for eje, info in self.ejes.items():
            if not isinstance(info, dict):
                self._err(f"eje «{eje}»: debe ser {{nombre, regla, multivalor, valores}}")
                continue
            if not isinstance(info.get("valores"), dict) or not info["valores"]:
                self._err(f"eje «{eje}»: `valores` debe ser un diccionario valor → descripción")
            if "multivalor" in info and not isinstance(info["multivalor"], bool):
                self._err(f"eje «{eje}»: `multivalor` debe ser true o false")
        meta = self.d.get("meta") or {}
        if meta.get("proyecto") and meta["proyecto"] != self.proy.id:
            self._err(f"meta.proyecto es «{meta['proyecto']}», pero el proyecto es {self.proy.id}")
        if not str(meta.get("aviso_cobertura") or "").strip():
            self.avisos.append("meta.aviso_cobertura vacío: di qué sesgos tiene la búsqueda que formó el corpus")

    def _validar_ejes(self, quien: str, item: dict, de: str):
        for eje, valor in (item.get("ejes") or {}).items():
            if eje not in self.ejes:
                self._err(f"{quien}: eje «{eje}» no definido en `ejes`{de}")
                continue
            info = self.ejes[eje] if isinstance(self.ejes[eje], dict) else {}
            definidos = info.get("valores") if isinstance(info.get("valores"), dict) else {}
            multi = info.get("multivalor", True)
            if multi and not isinstance(valor, list):
                self._err(f"{quien}: el eje «{eje}» es multivalor y debe ser una lista{de}")
            if not multi and isinstance(valor, list):
                self._err(f"{quien}: el eje «{eje}» admite un solo valor, no una lista{de}")
            for v in (valor if isinstance(valor, list) else [valor]):
                if v not in definidos:
                    self._err(f"{quien}: valor «{v}» no definido en el eje «{eje}»{de}")

    def _validar_resultado(self, ref: str, r: dict, pid: str | None, citas: bool):
        for campo in ("aspecto", "valor"):
            if not r.get(campo):
                self._err(f"{ref}: falta `{campo}`")
        if r.get("fuente_repo"):
            self._validar_cita_repo(ref, r)
            faltan = cifras_ausentes(r.get("valor", ""), r.get("cita", ""), repo=True)
        elif r.get("fuente") == "brief":
            return
        elif not r.get("cita") or not r.get("loc"):
            self._err(f"{ref}: resultado sin `cita` o sin `loc`")
            return
        else:
            faltan = cifras_ausentes(r.get("valor", ""), r["cita"])
            if citas and pid:
                self._cita(ref, r.get("fuente") or pid, r["loc"], r["cita"])
        if faltan:
            self._err(f"{ref}: cifras del valor que no están en la cita: {', '.join(faltan)}")

    def _validar_cita_repo(self, ref: str, item: dict):
        if self.proy.code_repo is None:
            self.avisos.append(f"{ref}: `fuente_repo` sin comprobar — el hub no tiene `code_repo`")
            return
        ruta = self.proy.code_repo / item["fuente_repo"]
        if not ruta.is_file():
            self._err(f"{ref}: no existe {item['fuente_repo']} en {self.proy.code_repo}")
            return
        texto = " ".join(ruta.read_text(encoding="utf-8", errors="replace").split())
        if " ".join(str(item.get("cita") or "").split()) not in texto:
            self._err(f"{ref}: la cita no está en {item['fuente_repo']}")

    def _validar_matrices(self):
        vistos = set()
        for m in self.matrices:
            mid = m.get("id")
            if not self._matriz_bien_formada(m):
                self._err(f"matriz «{mid}»: necesita id, filas {{eje, valores}} y columnas {{eje, valores}}")
                continue
            if mid in vistos:
                self._err(f"matriz «{mid}»: id repetido")
            vistos.add(mid)
            bien = True
            for lado in ("filas", "columnas"):
                eje = m[lado]["eje"]
                if eje not in self.ejes:
                    self._err(f"{mid}: el eje de {lado} «{eje}» no está en `ejes`")
                    bien = False
                    continue
                definidos = (self.ejes[eje] or {}).get("valores") or {}
                for v in m[lado]["valores"]:
                    if v not in definidos:
                        self._err(f"{mid}: {lado}: valor «{v}» no definido en el eje «{eje}»")
            filtro = self._filtro(m)
            if filtro and filtro[0] not in self.ejes:
                self._err(f"{mid}: el filtro usa el eje «{filtro[0]}», que no está en `ejes`")
            if not bien:
                continue
            filas, cols = set(m["filas"]["valores"]), set(m["columnas"]["valores"])
            for clave in (m.get("vacias") or {}):
                f, _, c = str(clave).partition("|")
                if f not in filas or c not in cols:
                    self._err(f"{mid}: `vacias` nombra «{clave}», que no es una casilla de la matriz")
            for e in self.calcular_matriz(m).values():
                if e["papers"]:
                    if e["marca_declarada"]:
                        self.avisos.append(f"{e['id']}: marcada «{(e['marca_declarada'] or {}).get('marca')}» "
                                           f"pero ya la cubre {', '.join(e['papers'])}")
                    continue
                marca = e["marca"]
                if not marca:
                    self._err(f"{e['id']}: casilla vacía sin marca (hueco | sin_sentido | no_buscado, con por_que)")
                elif marca.get("marca") not in MARCAS or not str(marca.get("por_que") or "").strip():
                    self._err(f"{e['id']}: marca no válida o sin razón (`por_que`)")

    # ---------- ideas ----------

    def usuarios_idea(self, idea: str) -> tuple[list, list]:
        papers = [pid for pid, p in self.papers.items() if idea in (p.get("ideas") or [])]
        nuestros = [f"{c['id']} ({c.get('estado', '')})" for c in self.componentes.values()
                    if idea in (c.get("ideas") or [])]
        return papers, nuestros

    def estado_idea(self, idea: str) -> str:
        papers, nuestros = self.usuarios_idea(idea)
        if papers and nuestros:
            return "ambos"
        if papers:
            return "hecho"
        if nuestros:
            return "nuestro"
        return "sin_explorar"

    # ---------- dates ----------

    def generado(self) -> str:
        """The latest date among the inputs — deterministic, unlike the clock."""
        fechas = [str((self.d.get("meta") or {}).get("fecha") or "")]
        fechas += [str(e.get("extraido") or "") for e in self.vigentes.values()]
        fechas += [str(n.get("updated") or "") for n in self.hipotesis + self.experimentos]
        fechas = [f[:10] for f in fechas if re.match(r"^\d{4}-\d{2}-\d{2}", f)]
        return max(fechas) if fechas else ""


# ---------------------------------------------------------------- idea layout

ANCHO, ALTO = 1000, 760


def _semilla(texto: str) -> float:
    return int(hashlib.sha256(texto.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def disposicion(nodos: list[str], aristas: list[tuple[str, str]], ancho=ANCHO, alto=ALTO, iters=800) -> dict:
    """Deterministic Fruchterman–Reingold (seeded by id), rescaled to the box,
    then a pass that pushes overlapping labels apart."""
    pos = {n: [_semilla(n + "x"), _semilla(n + "y")] for n in nodos}
    if not nodos:
        return pos
    aristas = [(a, b) for a, b in aristas if a in pos and b in pos]
    k = 1.0 / math.sqrt(len(nodos))
    t = 0.1
    for _ in range(iters):
        disp = {n: [0.0, 0.0] for n in nodos}
        for i, a in enumerate(nodos):
            for b in nodos[i + 1:]:
                dx, dy = pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]
                d = math.hypot(dx, dy) or 1e-4
                f = k * k / d
                disp[a][0] += dx / d * f
                disp[a][1] += dy / d * f
                disp[b][0] -= dx / d * f
                disp[b][1] -= dy / d * f
        for a, b in aristas:
            dx, dy = pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]
            d = math.hypot(dx, dy) or 1e-4
            f = d * d / k
            disp[a][0] -= dx / d * f
            disp[a][1] -= dy / d * f
            disp[b][0] += dx / d * f
            disp[b][1] += dy / d * f
        for n in nodos:
            disp[n][0] += (0.5 - pos[n][0]) * 0.6
            disp[n][1] += (0.5 - pos[n][1]) * 0.6
            d = math.hypot(*disp[n]) or 1e-4
            pos[n][0] += disp[n][0] / d * min(d, t)
            pos[n][1] += disp[n][1] / d * min(d, t)
        t = max(t * 0.99, 0.002)
    xs, ys = [p[0] for p in pos.values()], [p[1] for p in pos.values()]
    mx, Mx, my, My = min(xs), max(xs), min(ys), max(ys)
    for n in nodos:
        pos[n][0] = 90 + (pos[n][0] - mx) / ((Mx - mx) or 1) * (ancho - 180)
        pos[n][1] = 40 + (pos[n][1] - my) / ((My - my) or 1) * (alto - 70)
    for _ in range(400):
        movido = False
        for i, a in enumerate(nodos):
            for b in nodos[i + 1:]:
                dx, dy = pos[b][0] - pos[a][0], pos[b][1] - pos[a][1]
                sx, sy = 130 - abs(dx), 40 - abs(dy)
                if sx > 0 and sy > 0:
                    movido = True
                    if sx / 130 < sy / 40:
                        s = (1 if dx >= 0 else -1) * sx / 2
                        pos[a][0] -= s
                        pos[b][0] += s
                    else:
                        s = (1 if dy >= 0 else -1) * sy / 2
                        pos[a][1] -= s
                        pos[b][1] += s
        for n in nodos:
            pos[n][0] = min(ancho - 70, max(70, pos[n][0]))
            pos[n][1] = min(alto - 14, max(30, pos[n][1]))
        if not movido:
            break
    return pos


# ---------------------------------------------------------------- mapa.json

def _marca_de(e: dict) -> dict | None:
    return None if e["papers"] else (e["marca"] or {})


def datos_json(mapa: Mapa, obsoleto: dict) -> dict:
    d = mapa.d
    meta = d.get("meta") or {}
    ejes = [{"id": eje, "nombre": mapa.nombre_eje(eje), "regla": (info or {}).get("regla"),
             "multivalor": (info or {}).get("multivalor", True),
             "valores": [{"v": v, "desc": str(desc)} for v, desc in ((info or {}).get("valores") or {}).items()]}
            for eje, info in mapa.ejes.items()]
    papers = []
    for pid, p in mapa.papers.items():
        mp = mapa.meta_papers[pid]
        papers.append({
            "id": pid, "titulo": p.get("titulo") or mp.get("titulo"), "anio": p.get("anio") or mp.get("anio"),
            "corto": p.get("nombre_corto"), "confianza": mapa._confianza(pid), "cobertura": p.get("cobertura"),
            "origen": mapa.origen[pid], "ejes": dict(p.get("ejes") or {}), "notas": dict(p.get("notas_ejes") or {}),
            "ideas": list(p.get("ideas") or []), "no_leido": list(p.get("no_leido") or []),
            "evidencia": [{"eje": e.get("eje"), "loc": e.get("loc"), "cita": e.get("cita")} for e in p.get("evidencia") or []],
            "resultados": [{"id": r.get("id"), "aspecto": r.get("aspecto"), "valor": r.get("valor"), "loc": r.get("loc"),
                            "condiciones": r.get("condiciones") or {}} for r in p.get("resultados") or []],
            "limitaciones": [{"origen": li.get("origen"), "texto": li.get("texto"), "loc": li.get("loc")}
                             for li in p.get("limitaciones") or []],
        })
    cuenta = {"cubiertas": 0, "hueco": 0, "no_buscado": 0, "sin_sentido": 0}
    matrices, donde = [], []
    for m in mapa.matrices:
        calc = mapa.calcular_matriz(m)
        fe, ce = m["filas"]["eje"], m["columnas"]["eje"]
        celdas = []
        for fila in m["filas"]["valores"]:
            linea = []
            for col in m["columnas"]["valores"]:
                e = calc[(fila, col)]
                marca = _marca_de(e)
                linea.append({"id": e["id"], "papers": e["papers"], "nuestros": e["nuestros"],
                              "marca": marca.get("marca") if marca else None,
                              "por_que": marca.get("por_que") if marca else None})
                cuenta["cubiertas" if e["papers"] else marca.get("marca")] += 1
                for n in e["nuestros"]:
                    if n["tipo"] != "experimento":
                        donde.append({**n, "matriz": m["id"], "celda": e["id"], "fila": fila, "col": col,
                                      "papers": e["papers"], "marca": marca.get("marca") if marca else None})
            celdas.append(linea)
        matrices.append({"id": m["id"], "titulo": m.get("titulo", m["id"]), "pregunta": m.get("pregunta", ""),
                         "fila_eje": fe, "col_eje": ce, "fila_eje_nombre": mapa.nombre_eje(fe),
                         "col_eje_nombre": mapa.nombre_eje(ce),
                         "filtro": ({"eje": mapa._filtro(m)[0], "valores": sorted(mapa._filtro(m)[1])}
                                    if mapa._filtro(m) else None),
                         "filas": [{"v": v, "desc": mapa.desc_valor(fe, v)} for v in m["filas"]["valores"]],
                         "cols": [{"v": v, "desc": mapa.desc_valor(ce, v)} for v in m["columnas"]["valores"]],
                         "celdas": celdas})
    frontera = []
    for f in d.get("frontera") or []:
        filas = []
        for ref in f.get("refs") or []:
            quien, r = mapa.resultados[ref]
            cond = {k: v for k, v in (r.get("condiciones") or {}).items() if v not in (None, "", "—")}
            if quien.startswith("NUESTRO:"):
                fuente = f"nuestro · {r['fuente_repo']}" if r.get("fuente_repo") else "nuestro · brief"
                conf = "repo" if r.get("fuente_repo") else "brief"
            else:
                via = f" (vía {r['fuente']})" if r.get("fuente") and r["fuente"] != quien else ""
                fuente = f"{quien} {r.get('loc', '')}{via}".strip()
                conf = mapa._confianza(quien)
            filas.append({"ref": ref, "quien": quien, "valor": r.get("valor"), "condiciones": cond, "fuente": fuente,
                          "confianza": conf, "mejor": f.get("mejor") == ref, "nota": r.get("nota")})
        frontera.append({"id": f.get("id"), "aspecto": f.get("aspecto"), "comparables": bool(f.get("comparables")),
                         "nota": str(f.get("nota") or "").strip(), "filas": filas, "hay_mejor": bool(f.get("mejor"))})
    huecos = []
    for h in sorted(d.get("huecos") or [], key=lambda h: (h.get("prioridad", 99), str(h.get("id")))):
        celdas = []
        for celda in h.get("celdas") or []:
            e = mapa.estado_celda(celda)
            marca = _marca_de(e)
            celdas.append({"celda": celda, "cubierta_por": e["papers"], "marca": marca.get("marca") if marca else None})
        huecos.append({"id": h.get("id"), "prioridad": h.get("prioridad"), "titulo": h.get("titulo"),
                       "por_que": str(h.get("por_que") or "").strip(), "realidad": h.get("realidad"),
                       "dificultad": h.get("dificultad"), "relevancia": h.get("relevancia"), "tarea": h.get("tarea"),
                       "barato": h.get("barato"), "encaje": h.get("encaje"),
                       "evidencia": [{"ref": "Corpus" if e.get("ref") == "meta" else e.get("ref"), "loc": e.get("loc"),
                                      "texto": e.get("texto")} for e in h.get("evidencia") or []],
                       "celdas": celdas, "atacado_por": mapa.atacado_por(h.get("celdas") or [])})
    rels = [r for r in d.get("relaciones") or []]
    pos = disposicion(list(mapa.ideas), [(r.get("de"), r.get("a")) for r in rels])
    nodos = []
    for n, idea in mapa.ideas.items():
        ps, ns = mapa.usuarios_idea(n)
        nodos.append({"id": n, "nombre": idea.get("nombre", n), "desc": idea.get("descripcion", ""),
                      "estado": mapa.estado_idea(n), "x": round(pos[n][0], 1), "y": round(pos[n][1], 1),
                      "papers": ps, "nuestros": ns})
    aristas = [{"de": r.get("de"), "a": r.get("a"), "tipo": r.get("tipo"), "origen": r.get("origen"),
                "nota": r.get("nota"),
                "citas": [{"paper": c.get("paper"), "loc": c.get("loc"), "cita": c.get("cita")} for c in r.get("citas") or []]}
               for r in rels]
    componentes = [{"id": c["id"], "nombre": c.get("nombre", c["id"]), "estado": c.get("estado"), "nota": c.get("nota"),
                    "resultados": [{"valor": r.get("valor"), "fuente": r.get("fuente_repo") or r.get("fuente"),
                                    "nota": r.get("nota")} for r in c.get("resultados") or []]}
                   for c in mapa.componentes.values()]
    confianzas = {k: 0 for k in CONFIANZAS}
    for pid in mapa.papers:
        c = mapa._confianza(pid)
        confianzas[c] = confianzas.get(c, 0) + 1
    generado = mapa.generado()
    return {
        "proyecto": mapa.proy.id, "slug": mapa.proy.slug, "nombre": mapa.proy.nombre,
        "generado": generado, "herramienta": TOOL, "taxonomia_sha": mapa.sha,
        "obsoleto": obsoleto,
        "meta": {"fecha": meta.get("fecha"), "escrito_por": meta.get("escrito_por", "modelo"),
                 "citable": bool(meta.get("citable", False)), "apuesta": meta.get("apuesta"),
                 "aviso": meta.get("aviso_cobertura"),
                 "verificacion": {"citas_total": mapa.citas_total, "citas_ok": mapa.citas_ok, "herramienta": TOOL,
                                  "fecha": generado, "muestreo": meta.get("verificacion")}},
        "ejes": ejes,
        "resumen": {"n_papers": len(mapa.papers), "confianzas": confianzas, "n_resultados": len(mapa.resultados),
                    "n_ideas": len(mapa.ideas), "n_relaciones": len(rels), "casillas": cuenta,
                    "n_hipotesis": len(mapa.hipotesis), "n_huecos": len(huecos)},
        "papers": papers, "matrices": matrices, "frontera": frontera, "huecos": huecos,
        "niveles_encaje": NIVELES_ENCAJE, "ideas": {"nodos": nodos, "aristas": aristas},
        "posicionamiento": d.get("posicionamiento") or {}, "donde": donde,
        "hipotesis_clave": d.get("hipotesis_clave") or {}, "componentes": componentes,
        "hipotesis": [{"id": h["id"], "status": h["status"], "frontier_cells": h["frontier_cells"],
                       "titulo": h.get("titulo")} for h in mapa.hipotesis],
        "avisos": list(mapa.avisos),
    }


def obsoleto_de(mapa: Mapa) -> dict:
    proyecto = cq.list_papers(str(mapa.proy.vault), mapa.proy.id)
    manuales = {pid for pid, o in mapa.origen.items() if o.startswith("mapa") and mapa.papers[pid].get("ejes")}
    sin = [p["id"] for p in proyecto if p["id"] not in mapa.extracciones and p["id"] not in manuales]
    return {"papers_sin_extraer": sin, "taxonomia_cambiada": list(mapa.obsoletas),
            "marcas_cubiertas": mapa.marcas_cubiertas(), "huecos_cubiertos": mapa.huecos_cubiertos()}


# ---------------------------------------------------------------- commands

def cargar(vault: Path, project: str) -> Mapa:
    proy = resolver_proyecto(vault, project)
    datos = cargar_yaml(proy.frontera / "mapa.yaml")
    hips, exps = notas_nuestras(proy)
    return Mapa(proy, datos, cargar_extracciones(proy), hips, exps)


def cmd_check(mapa: Mapa) -> tuple[int, dict]:
    mapa.validar()
    rep = {"ok": not mapa.errores, "proyecto": mapa.proy.id, "errores": mapa.errores, "avisos": mapa.avisos,
           "papers": len(mapa.papers), "citas": {"total": mapa.citas_total, "ok": mapa.citas_ok},
           "taxonomia_sha": mapa.sha}
    return (0 if rep["ok"] else 1), rep


def cmd_build(mapa: Mapa) -> tuple[int, dict]:
    code, rep = cmd_check(mapa)
    if code:
        rep["escritos"] = []
        rep["mensaje"] = f"{len(mapa.errores)} errores; no se escribe nada."
        return code, rep
    import vistas
    obs = obsoleto_de(mapa)
    datos = datos_json(mapa, obs)
    salidas = {
        "matriz.md": vistas.vista_matriz(mapa),
        "frontera.md": vistas.vista_frontera(mapa),
        "huecos.md": vistas.vista_huecos(mapa),
        "posicionamiento.md": vistas.vista_posicionamiento(mapa),
        "mapa.json": json.dumps(datos, ensure_ascii=False, indent=1) + "\n",
        "mapa.html": vistas.vista_html(datos),
    }
    mapa.proy.frontera.mkdir(parents=True, exist_ok=True)
    for nombre, texto in salidas.items():
        (mapa.proy.frontera / nombre).write_text(texto, encoding="utf-8", newline="\n")
    rep["escritos"] = [f"Projects/{mapa.proy.slug}/Frontera/{n}" for n in salidas]
    rep["obsoleto"] = obs
    return 0, rep


def cmd_stale(vault: Path, project: str) -> dict:
    proy = resolver_proyecto(vault, project)
    yaml_path = proy.frontera / "mapa.yaml"
    proyecto = cq.list_papers(str(vault), proy.id)
    rutas = {p["id"]: p["path"] for p in proyecto}
    if not yaml_path.is_file():
        return {"proyecto": proy.id, "sin_mapa": True, "papers_sin_extraer": sorted(rutas), "taxonomia_cambiada": [],
                "marcas_cubiertas": [], "huecos_cubiertos": [], "rutas": rutas, "al_dia": False}
    mapa = cargar(vault, project)
    obs = obsoleto_de(mapa)
    al_dia = not any(obs.values())
    return {"proyecto": proy.id, "sin_mapa": False, "taxonomia_sha": mapa.sha, **obs,
            "rutas": {p: rutas[p] for p in obs["papers_sin_extraer"] + obs["taxonomia_cambiada"] if p in rutas},
            "al_dia": al_dia}


def cmd_cell(mapa: Mapa, celda: str, con_citas: bool = False) -> dict:
    p = parse_celda(celda)
    e = mapa.estado_celda(celda)
    if not p or e is None:
        raise Refused(f"«{celda}» no es una casilla del mapa (formato M1:fila|col)")
    m = mapa.matriz(p[0])
    fe, ce = m["filas"]["eje"], m["columnas"]["eje"]

    def eje_info(eje, valor):
        info = mapa.ejes.get(eje) or {}
        return {"eje": eje, "eje_nombre": mapa.nombre_eje(eje), "regla": info.get("regla"),
                "valor": valor, "desc": mapa.desc_valor(eje, valor)}

    papers = []
    for pid in e["papers"]:
        pp, mp = mapa.papers[pid], mapa.meta_papers[pid]
        ev = [{"eje": x.get("eje"), "loc": x.get("loc"), **({"cita": x.get("cita")} if con_citas else {})}
              for x in pp.get("evidencia") or [] if x.get("eje") in (fe, ce)]
        notas = {k: v for k, v in (pp.get("notas_ejes") or {}).items() if k in (fe, ce)}
        papers.append({"id": pid, "titulo": pp.get("titulo") or mp.get("titulo"), "anio": pp.get("anio") or mp.get("anio"),
                       "confianza": mapa._confianza(pid), "evidencia": ev, "notas_ejes": notas,
                       "resultados": [{"id": r.get("id"), "aspecto": r.get("aspecto"), "valor": r.get("valor"),
                                       "loc": r.get("loc")} for r in pp.get("resultados") or []]})
    marca = _marca_de(e)
    huecos = []
    for h in mapa.d.get("huecos") or []:
        if celda in (h.get("celdas") or []):
            huecos.append({"id": h.get("id"), "prioridad": h.get("prioridad"), "titulo": h.get("titulo"),
                           "por_que": str(h.get("por_que") or "").strip(), "barato": h.get("barato"),
                           "encaje": h.get("encaje"), "tarea": h.get("tarea"),
                           "atacado_por": mapa.atacado_por(h.get("celdas") or [])})
    titulos = {h["id"]: h.get("titulo") for h in mapa.hipotesis}
    nuestros = [{**n, **({"titulo": titulos.get(n["id"])} if n["tipo"] == "hipotesis" else {})} for n in e["nuestros"]]
    return {"id": celda, "proyecto": mapa.proy.id,
            "matriz": {"id": m["id"], "titulo": m.get("titulo"), "pregunta": m.get("pregunta")},
            "fila": eje_info(fe, p[1]), "columna": eje_info(ce, p[2]),
            "papers": papers, "marca": marca.get("marca") if marca else None,
            "por_que": marca.get("por_que") if marca else None,
            "marca_declarada_pero_cubierta": bool(e["papers"] and e["marca_declarada"]),
            "nuestros": nuestros, "huecos": huecos,
            "aviso_cobertura": (mapa.d.get("meta") or {}).get("aviso_cobertura")}


def cmd_summary(mapa: Mapa) -> dict:
    """What the map knows of each paper, without any quoted text: axis values
    and notes, results (aspect, value, conditions, locator), limitations,
    ideas — what the skill assembles matrices, frontier and gaps from."""
    papers = []
    for pid, p in mapa.papers.items():
        ex = mapa.vigentes.get(pid) or {}
        papers.append({"id": pid, "titulo": mapa.meta_papers[pid].get("titulo"), "origen": mapa.origen[pid],
                       "confianza": mapa._confianza(pid), "ejes": p.get("ejes") or {},
                       "notas_ejes": p.get("notas_ejes") or {},
                       "resultados": [{k: r.get(k) for k in ("id", "aspecto", "valor", "condiciones", "loc")}
                                      for r in p.get("resultados") or []],
                       "limitaciones": [{k: li.get(k) for k in ("origen", "texto", "loc")}
                                        for li in p.get("limitaciones") or []],
                       "ideas": p.get("ideas") or [], "ideas_nuevas": ex.get("ideas_nuevas") or [],
                       "no_leido": p.get("no_leido") or [], "descartado": len(ex.get("descartado") or [])})
    vacias = [e["id"] for m in mapa.matrices if mapa._matriz_bien_formada(m)
              for e in mapa.calcular_matriz(m).values() if not e["papers"] and not e["marca"]]
    return {"proyecto": mapa.proy.id, "taxonomia_sha": mapa.sha, "papers": papers,
            "extracciones_obsoletas": list(mapa.obsoletas), "casillas_sin_marca": vacias}


def cmd_sample(mapa: Mapa, n: int, seed: str) -> dict:
    """`n` covered cells drawn at random (seeded), each with one covering paper
    and what to check in it: the paper-reader requests of the spot check."""
    import random
    cubiertas = [e for m in mapa.matrices if mapa._matriz_bien_formada(m)
                 for e in mapa.calcular_matriz(m).values() if e["papers"]]
    rng = random.Random(seed)
    elegidas = rng.sample(cubiertas, min(n, len(cubiertas)))
    out = []
    for e in sorted(elegidas, key=lambda x: x["id"]):
        _, fila, col = parse_celda(e["id"])
        m = mapa.matriz(e["id"].split(":", 1)[0])
        pid = rng.choice(e["papers"])
        pp = mapa.papers[pid]
        checks = []
        for eje, valor in ((m["filas"]["eje"], fila), (m["columnas"]["eje"], col)):
            locs = sorted({str(x.get("loc")) for x in pp.get("evidencia") or [] if x.get("eje") == eje and x.get("loc")})
            checks.append({"eje": eje, "valor": valor, "desc": mapa.desc_valor(eje, valor),
                           "regla": (mapa.ejes.get(eje) or {}).get("regla"), "locs": locs,
                           "nota": (pp.get("notas_ejes") or {}).get(eje),
                           "frase": f"{pid} tiene «{valor}» en el eje «{mapa.nombre_eje(eje)}»: "
                                    f"{mapa.desc_valor(eje, valor)}"})
        out.append({"celda": e["id"], "paper": pid, "ruta": mapa.meta_papers[pid].get("ruta"), "checks": checks})
    return {"proyecto": mapa.proy.id, "semilla": seed, "cubiertas": len(cubiertas), "muestra": out}


def cmd_taxonomy(vault: Path, project: str) -> dict:
    proy = resolver_proyecto(vault, project)
    datos = cargar_yaml(proy.frontera / "mapa.yaml")
    return {"proyecto": proy.id, "taxonomia_sha": sha_taxonomia(datos.get("ejes") or {}),
            "ejes": datos.get("ejes") or {},
            "ideas": [{"id": i.get("id"), "nombre": i.get("nombre"), "descripcion": i.get("descripcion")}
                      for i in datos.get("ideas") or [] if isinstance(i, dict)]}


# ---------------------------------------------------------------- save-extraction

def bloques_json(texto: str) -> list:
    out = []
    for m in re.finditer(r"```(?:json)?\s*\n(.*?)\n```", texto, re.S):
        try:
            out.append(json.loads(m.group(1)))
        except ValueError:
            continue
    if not out:
        try:
            out.append(json.loads(texto))
        except ValueError:
            pass
    return out


def _extracciones_de(respuesta: str) -> tuple[list[dict], list]:
    items, sospechoso = [], []
    for b in bloques_json(respuesta):
        if isinstance(b, dict) and isinstance(b.get("extracciones"), list):
            items += [x for x in b["extracciones"] if isinstance(x, dict)]
            sospechoso += list(b.get("suspicious") or [])
        elif isinstance(b, dict) and b.get("paper"):
            items.append(b)
            sospechoso += list(b.get("suspicious") or [])
        elif isinstance(b, list):
            items += [x for x in b if isinstance(x, dict)]
    return items, sospechoso


def limpiar_extraccion(vault: Path, ex: dict, ejes: dict, ideas: set | None, sha: str, fecha: str) -> dict:
    """Keep only what the paper's text supports; list everything dropped."""
    pid = ex["paper"]
    fuera: list[dict] = []
    out_ejes = {}
    for eje, info in (ex.get("ejes") or {}).items():
        info = info or {}
        if eje not in ejes:
            fuera.append({"donde": f"ejes.{eje}", "motivo": "eje no definido en la taxonomía"})
            continue
        definidos = (ejes[eje] or {}).get("valores") or {}
        valores = []
        for v in info.get("valores") or []:
            if v in definidos:
                valores.append(v)
            else:
                fuera.append({"donde": f"ejes.{eje}", "motivo": f"valor «{v}» no definido"})
        citas = []
        for c in info.get("citas") or []:
            why = problema_cita(vault, pid, str(c.get("loc") or ""), str(c.get("cita") or "")) \
                if c.get("cita") and c.get("loc") else "cita o localizador vacíos"
            if why:
                fuera.append({"donde": f"ejes.{eje}", "loc": c.get("loc"), "motivo": why})
            else:
                citas.append({"cita": cq.ws(c["cita"]), "loc": c["loc"]})
        nota = str(info.get("nota") or "").strip()
        if valores and not citas and not nota.lower().startswith("deducido"):
            fuera.append({"donde": f"ejes.{eje}", "motivo": "valores sin cita que pase ni nota «Deducido: …»"})
            valores = []
        entry = {"valores": valores, "citas": citas}
        if nota:
            entry["nota"] = nota
        out_ejes[eje] = entry
    resultados = []
    for i, r in enumerate(ex.get("resultados") or [], 1):
        r = dict(r)
        r.setdefault("id", f"r{i}")
        if not r.get("cita") or not r.get("loc") or not r.get("valor") or not r.get("aspecto"):
            fuera.append({"donde": f"resultados.{r['id']}", "motivo": "falta aspecto, valor, cita o loc"})
            continue
        faltan = cifras_ausentes(r["valor"], r["cita"])
        if faltan:
            fuera.append({"donde": f"resultados.{r['id']}", "motivo": f"cifras que no están en la cita: {', '.join(faltan)}"})
            continue
        why = problema_cita(vault, r.get("fuente") or pid, str(r["loc"]), str(r["cita"]))
        if why:
            fuera.append({"donde": f"resultados.{r['id']}", "loc": r["loc"], "motivo": why})
            continue
        r["cita"] = cq.ws(r["cita"])
        resultados.append(r)
    limitaciones = []
    for i, li in enumerate(ex.get("limitaciones") or [], 1):
        li = dict(li)
        if li.get("origen") not in ("autores", "deducida") or not li.get("texto"):
            fuera.append({"donde": f"limitaciones.{i}", "motivo": "origen (autores | deducida) o texto ausentes"})
            continue
        if li.get("origen") == "autores" or li.get("cita"):
            why = problema_cita(vault, pid, str(li.get("loc") or ""), str(li.get("cita") or "")) \
                if li.get("cita") and li.get("loc") else "limitación de los autores sin cita o sin loc"
            if why:
                fuera.append({"donde": f"limitaciones.{i}", "loc": li.get("loc"), "motivo": why})
                continue
            li["cita"] = cq.ws(li["cita"])
        limitaciones.append(li)
    ideas_ok = []
    for idea in ex.get("ideas") or []:
        if ideas is not None and idea not in ideas:
            fuera.append({"donde": "ideas", "motivo": f"idea «{idea}» no está en `ideas`"})
        else:
            ideas_ok.append(idea)
    return {"paper": pid, "taxonomia_sha": sha, "extraido": fecha, "herramienta": TOOL,
            "ejes": out_ejes, "resultados": resultados, "limitaciones": limitaciones, "ideas": ideas_ok,
            "ideas_nuevas": [{k: i.get(k) for k in ("nombre", "descripcion", "loc")}
                             for i in ex.get("ideas_nuevas") or [] if isinstance(i, dict) and i.get("nombre")],
            "no_leido": list(ex.get("no_leido") or []), "descartado": fuera}


def cmd_save_extraction(vault: Path, project: str, reply: Path, fecha: str) -> tuple[int, dict]:
    proy = resolver_proyecto(vault, project)
    datos = cargar_yaml(proy.frontera / "mapa.yaml")
    ejes = datos.get("ejes") or {}
    if not ejes:
        raise Refused("mapa.yaml no tiene `ejes`: aprueba la taxonomía primero")
    sha = sha_taxonomia(ejes)
    ideas = {i["id"] for i in datos.get("ideas") or [] if isinstance(i, dict) and "id" in i} or None
    items, sospechoso = _extracciones_de(reply.read_text(encoding="utf-8"))
    if not items:
        raise Refused("la respuesta no trae ningún bloque JSON con extracciones")
    proyecto = {p["id"] for p in cq.list_papers(str(vault), proy.id)}
    carpeta = proy.frontera / "extracciones"
    rep = {"escritos": [], "rechazados": [], "descartado": {}, "suspicious": sospechoso}
    for ex in items:
        pid = str(ex.get("paper") or "")
        if not re.fullmatch(r"P-\d{3,5}", pid):
            rep["rechazados"].append({"paper": pid, "motivo": "id de paper no válido"})
            continue
        if pid not in proyecto:
            rep["rechazados"].append({"paper": pid, "motivo": f"no es un paper del proyecto {proy.id} (o es send: never)"})
            continue
        limpio = limpiar_extraccion(vault, ex, ejes, ideas, sha, fecha)
        carpeta.mkdir(parents=True, exist_ok=True)
        (carpeta / f"{pid}.json").write_text(json.dumps(limpio, ensure_ascii=False, indent=1) + "\n",
                                             encoding="utf-8", newline="\n")
        rep["escritos"].append(f"Projects/{proy.slug}/Frontera/extracciones/{pid}.json")
        if limpio["descartado"]:
            rep["descartado"][pid] = limpio["descartado"]
    return (0 if rep["escritos"] else 1), rep


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=("build", "check", "stale", "cell", "taxonomy", "save-extraction", "sample",
                                       "summary"))
    ap.add_argument("cell_id", nargs="?", help="cell id for `cell` (M1:fila|col)")
    ap.add_argument("--vault", required=True, type=Path)
    ap.add_argument("--project", required=True)
    ap.add_argument("--con-citas", action="store_true", help="cell: include the quoted text of the evidence")
    ap.add_argument("--reply", type=Path, help="save-extraction: the frontier-extractor's reply, saved unchanged")
    ap.add_argument("--date", default=None, help="save-extraction: extraction date (default today)")
    ap.add_argument("--n", type=int, default=5, help="sample: how many covered cells")
    ap.add_argument("--seed", default=None, help="sample: random seed (default today's date)")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    vault = a.vault.resolve()
    try:
        if a.command == "stale":
            out, code = cmd_stale(vault, a.project), 0
        elif a.command == "taxonomy":
            out, code = cmd_taxonomy(vault, a.project), 0
        elif a.command == "save-extraction":
            if not a.reply:
                raise Refused("save-extraction necesita --reply <archivo>")
            code, out = cmd_save_extraction(vault, a.project, a.reply, a.date or _dt.date.today().isoformat())
        else:
            mapa = cargar(vault, a.project)
            if a.command == "summary":
                out, code = cmd_summary(mapa), 0
            elif a.command == "sample":
                out, code = cmd_sample(mapa, a.n, a.seed or _dt.date.today().isoformat()), 0
            elif a.command == "cell":
                if not a.cell_id:
                    raise Refused("cell necesita el id de la casilla (M1:fila|col)")
                out, code = cmd_cell(mapa, a.cell_id, a.con_citas), 0
            elif a.command == "check":
                code, out = cmd_check(mapa)
            else:
                code, out = cmd_build(mapa)
    except Refused as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return code


if __name__ == "__main__":
    sys.exit(main())
