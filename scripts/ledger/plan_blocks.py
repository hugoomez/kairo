"""Read the machine-read JSON blocks of a measurement plan (M-XXXX).

    plan_blocks.py show <path>   prints the plan blocks as JSON; exit 3 with {"error": ...}

Each of `## Estimandos`, `## Clasificadores`, `## Intervalos` holds a fenced json block;
the first one under the heading is read. Standard library only.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import notes  # noqa: E402

TYPES = ("proporcion", "conteo", "mediana")
_FENCE = re.compile(r"```json[ \t]*\n(.*?)\n[ \t]*```", re.S)


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
        for k in ("id", "type", "column"):
            if not isinstance(e.get(k), str) or not e[k]:
                raise PlanError(f"{h}: el estimando {e.get('id')!r} necesita '{k}' (texto)")
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
    return items


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
    return data


def read_plan(path: Path) -> dict:
    parts = None
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
        parts = notes.split_note(text)
    except (OSError, UnicodeDecodeError) as e:
        raise PlanError(f"no se pudo leer {path}: {e}") from e
    if parts is None:
        raise PlanError("el plan no tiene frontmatter")
    fm_lines, body = parts
    fm = notes.parse_frontmatter(fm_lines)
    return {
        "frozen_at": fm.get("frozen_at", ""),
        "frozen_commit": fm.get("frozen_commit", ""),
        "estimands": _estimands(_block(body, "Estimandos")),
        "classifiers": _classifiers(_block(body, "Clasificadores")),
        "intervals": _intervals(_block(body, "Intervalos")),
    }


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] != "show":
        print(json.dumps({"error": "uso: plan_blocks.py show <ruta>"}))
        return 1
    try:
        print(json.dumps(read_plan(Path(argv[1])), ensure_ascii=False, indent=2))
        return 0
    except PlanError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 3


if __name__ == "__main__":
    sys.exit(main())
