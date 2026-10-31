"""Read-only view of one Kairo project's notes, shared by similar_work.py,
next_steps.py and scripts/estimate/hypothesis_cost.py.

It reads, never writes:

- the hub (`Projects/<slug>/_hub.md`) — a project is named by its folder slug
  or by its `PROJ-XXX` id;
- `Hipotesis/H-*.md` and `Experimentos/E-*.md` frontmatter (via notes.py, plus
  the nested `result:` / `environment:` fields this needs);
- the non-H rows of `_digest.md` (hypothesis-cycle's discard ledger);
- `Frontera/mapa.json` when it exists (written by scripts/frontier/frontier.py;
  absent is normal and never an error).

Notes marked `send: never` are skipped entirely: their id is counted, nothing
of their content is returned.

Similarity here is **lexical**: lower-cased word tokens without accents, of
four letters or more, minus a short stop-word list, compared by Jaccard
overlap. It finds shared wording, not shared meaning.

Imported, not run. Standard library only.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "security"))
from notes import as_list, read_note, section  # noqa: E402
from send_guard import is_flagged  # noqa: E402

__version__ = "1.0.0"

WORTH_FIELDS = ("vale_la_pena", "vale_la_pena_razon", "importancia", "dificultad",
                "coste_computo", "coste_tiempo", "coste_base", "evaluado")
OPEN_STATUSES = ("propuesta", "en_cola", "preregistrada", "en_experimento")
_CELL = re.compile(r"^M\d+:[^|\s]+\|[^|\s]+$")
_PROJ = re.compile(r"^PROJ-\d{3,4}$")


class InputError(Exception):
    """Bad input: unknown project, missing vault (exit 2)."""


def placeholder(value: object) -> bool:
    """Absent, empty, or still the template's `<…>` text."""
    if value is None:
        return True
    if isinstance(value, list):
        return False
    s = str(value).strip()
    return not s or s.startswith("<")


def scalar(fm: dict, key: str) -> str | None:
    v = fm.get(key)
    if isinstance(v, list) or placeholder(v):
        return None
    return str(v).strip()


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

def resolve_project(vault: Path, project: str) -> tuple[Path, str]:
    """(project dir, PROJ id) for a slug or a PROJ-XXX id."""
    root = vault / "Projects"
    if not root.is_dir():
        raise InputError(f"{vault} has no Projects/ folder")
    project = project.strip()
    if project.startswith("Projects/"):
        project = project[len("Projects/"):]
    direct = root / project
    if not _PROJ.match(project) and (direct / "_hub.md").is_file():
        return direct, hub_id(direct) or project
    for hub in sorted(root.glob("*/_hub.md")):
        if hub_id(hub.parent) == project:
            return hub.parent, project
    raise InputError(f"project {project!r} not found (no Projects/<slug>/_hub.md with that slug or id)")


def hub_fm(pdir: Path) -> dict:
    parsed = read_note(pdir / "_hub.md")
    return parsed[0] if parsed else {}


def hub_id(pdir: Path) -> str | None:
    return scalar(hub_fm(pdir), "id")


def all_projects(vault: Path) -> list[Path]:
    return [h.parent for h in sorted((vault / "Projects").glob("*/_hub.md"))]


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def _nested(text: str, block: str, key: str) -> str | None:
    """`block:\n  key: value` from a note's frontmatter (one level)."""
    fm = text.split("\n---", 1)[0] if text.startswith("---") else ""
    m = re.search(rf"^{block}:\s*\n((?:[ \t]+.*\n?)*)", fm, re.MULTILINE)
    if not m:
        return None
    f = re.search(rf"^[ \t]+{key}:\s*(.*?)\s*(#.*)?$", m.group(1), re.MULTILINE)
    v = f.group(1).strip().strip("\"'") if f else ""
    return v if v and not v.startswith("<") else None


def _history_experiments(text: str) -> list[str]:
    m = re.search(r"^history:\s*\n((?:[ \t-].*\n?)*)", text, re.MULTILINE)
    if not m:
        return []
    out: list[str] = []
    for line in m.group(1).splitlines():
        if re.match(r"^\s+experiments:", line):
            out += [e for e in re.findall(r"E-\d{4}", line) if e not in out]
    return out


def cells_of(value: object) -> list[str]:
    raw = value if isinstance(value, list) else ([value] if isinstance(value, str) and value.strip() else [])
    return [c.strip() for c in raw if isinstance(c, str) and _CELL.match(c.strip())]


def _read(path: Path) -> tuple[dict, str, str] | None:
    try:
        text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    except (OSError, UnicodeDecodeError):
        return None
    parsed = read_note(path)
    if parsed is None:
        return None
    return parsed[0], parsed[1], text


def hypotheses(pdir: Path) -> tuple[list[dict], int]:
    """(hypotheses, number skipped as send: never)."""
    out, skipped = [], 0
    for p in sorted((pdir / "Hipotesis").glob("H-*.md")):
        if is_flagged(p):
            skipped += 1
            continue
        r = _read(p)
        if r is None:
            continue
        fm, body, text = r
        hid = scalar(fm, "id") or p.stem.split(" ")[0]
        out.append({
            "id": hid, "path": p, "status": scalar(fm, "status") or "",
            "claim": first_text(section(body, "Claim")),
            "frontier_cells": cells_of(fm.get("frontier_cells")),
            "linked_experiment": as_list(fm.get("linked_experiment")),
            "collateral_evidence": as_list(fm.get("collateral_evidence")),
            "depends_on": as_list(fm.get("depends_on")),
            "history_experiments": _history_experiments(text),
            "worth": {k: scalar(fm, k) for k in WORTH_FIELDS},
            "updated": scalar(fm, "updated"),
        })
    return out, skipped


def experiments(pdir: Path) -> tuple[list[dict], int]:
    out, skipped = [], 0
    for p in sorted((pdir / "Experimentos").glob("E-*.md")):
        if is_flagged(p):
            skipped += 1
            continue
        r = _read(p)
        if r is None:
            continue
        fm, body, text = r
        out.append({
            "id": scalar(fm, "id") or p.stem.split(" ")[0], "path": p,
            "hypothesis": (as_list(fm.get("hypothesis")) or [None])[0],
            "secondary_hypotheses": as_list(fm.get("secondary_hypotheses")),
            "status": scalar(fm, "status"), "role": scalar(fm, "role"),
            "rung": scalar(fm, "rung"), "tier": scalar(fm, "tier"),
            "metric_kind": scalar(fm, "metric_kind") or ("proportion" if "metric_kind" not in fm else None),
            "validity": scalar(fm, "experiment_validity"),
            "verdict": _nested(text, "result", "verdict"),
            "effect": _nested(text, "result", "effect"),
            "hardware": _nested(text, "environment", "hardware"),
            "runtime": scalar(fm, "runtime"),
            "cost_estimated": scalar(fm, "cost_estimated"),
            "cost_actual": scalar(fm, "cost_actual"),
            "frozen_at": scalar(fm, "frozen_at"),
            "prediction": first_text(section(body, "Predicción")),
            "result_section": section(body, "Resultado"),
        })
    return out, skipped


def first_text(s: str, limit: int = 300) -> str:
    for ln in s.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith(("<", "<!--")):
            return ln if len(ln) <= limit else ln[: limit - 1].rstrip() + "…"
    return ""


def confirmatory(e: dict) -> bool:
    return (e.get("role") or "confirmatory") == "confirmatory"


_HYP_ROW = re.compile(r"^\|\s*H-\d+\s*\|")


def digest_discards(pdir: Path) -> list[dict]:
    """The table rows of _digest.md that are not H-XXXX rows (discards written
    by hypothesis-cycle: `| (sin nota) | <claim> | descartada | <motivo> |`)."""
    try:
        lines = (pdir / "_digest.md").read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        s = ln.strip()
        if not s.startswith("|") or _HYP_ROW.match(s) or re.match(r"^\|[\s:|-]+\|?$", s):
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", s.strip("|"))]
        if len(cells) < 3 or cells[0].lower() == "id":
            continue
        out.append({"fila": cells[0], "claim": cells[1].replace(r"\|", "|"),
                    "estado": cells[2], "motivo": cells[3] if len(cells) > 3 else ""})
    return out


# ---------------------------------------------------------------------------
# Frontier
# ---------------------------------------------------------------------------

def frontier_paths(pdir: Path) -> dict[str, Path]:
    f = pdir / "Frontera"
    return {"dir": f, "yaml": f / "mapa.yaml", "json": f / "mapa.json"}


def load_mapa(pdir: Path) -> tuple[dict | None, str | None]:
    """(mapa.json, problem). (None, None) when there is no frontier."""
    p = frontier_paths(pdir)["json"]
    if not p.is_file():
        return None, None
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"Frontera/mapa.json ilegible: {exc}"
    if not isinstance(data, dict):
        return None, "Frontera/mapa.json no es un objeto JSON"
    return data, None


def mapa_cells(mapa: dict) -> dict[str, dict]:
    """Every cell of mapa.json by its stable id `M1:fila|col`."""
    out: dict[str, dict] = {}
    for m in mapa.get("matrices") or []:
        if not isinstance(m, dict):
            continue
        mid = m.get("id")
        rows = [r.get("v") if isinstance(r, dict) else r for r in m.get("filas") or []]
        cols = [c.get("v") if isinstance(c, dict) else c for c in m.get("cols") or []]
        for i, row in enumerate(m.get("celdas") or []):
            for j, cell in enumerate(row or []):
                if i < len(rows) and j < len(cols) and isinstance(cell, dict):
                    out[f"{mid}:{rows[i]}|{cols[j]}"] = cell
    return out


def mapa_huecos(mapa: dict) -> list[dict]:
    return [h for h in mapa.get("huecos") or [] if isinstance(h, dict)]


# ---------------------------------------------------------------------------
# Lexical similarity
# ---------------------------------------------------------------------------

STOP = {
    # es
    "para", "como", "pero", "sobre", "entre", "desde", "hasta", "este", "esta", "estos", "estas",
    "cuando", "donde", "porque", "tiene", "tienen", "más", "mas", "menos", "cada", "todo", "todos",
    "otro", "otra", "otros", "otras", "sino", "solo", "también", "tambien", "mismo", "misma",
    "puede", "pueden", "será", "sera", "sean", "según", "segun", "mediante", "respecto", "hace",
    "frente", "bajo", "tras", "siempre", "nunca", "mejor", "peor", "mayor", "menor",
    # en
    "that", "this", "with", "from", "than", "then", "when", "where", "which", "while", "into",
    "over", "under", "more", "less", "each", "only", "also", "same", "does", "have", "their",
    "they", "there", "these", "those", "will", "would", "should", "could", "about", "between",
}


def tokens(text: str) -> set[str]:
    norm = unicodedata.normalize("NFKD", (text or "").lower())
    norm = "".join(ch for ch in norm if not unicodedata.combining(ch))
    return {w for w in re.findall(r"[a-z0-9_]+", norm) if len(w) >= 4 and w not in STOP and not w.isdigit()}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
