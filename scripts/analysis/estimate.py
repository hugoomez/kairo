"""Estimands of a measurement plan, with intervals, computed from a units file.

    estimate.py --vault V --plan M-XXXX --units <dataset name>

Prints {"plan", "units_sha256", "estimates": [{"id","type","value","lo","hi","n","method"}]}.
Exit 0 ok, 3 refused (JSON {"error": ...}), 1 usage/unexpected error.

The units file may be sensitive: only aggregates are ever printed, never rows or cell values.
In JSONL a row lacking the column counts as falsy (proporcion/conteo) or skipped (mediana); the
missing-column check needs at least the header (CSV) or one row carrying the key (JSONL).
Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ledger"))

from plan_blocks import PlanError, read_plan  # noqa: E402

TRUTHY = {"1", "true", "sí", "si", "yes"}


class Refused(Exception):
    def __init__(self, msg: str, **extra):
        super().__init__(msg)
        self.extra = extra


def _z(conf: float) -> float:
    return statistics.NormalDist().inv_cdf(1 - (1 - conf) / 2)


def wilson(k: int, n: int, conf: float) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 1.0)
    z = _z(conf)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    lo = 0.0 if k == 0 else max(0.0, c - h)
    hi = 1.0 if k == n else min(1.0, c + h)
    return (lo, hi)


def truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v == 1
    return str(v).strip().lower() in TRUTHY


def _number(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        x = float(str(v).strip())
    except ValueError:
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def _percentile(vals: list[float], conf: float) -> tuple[float, float]:
    vals = sorted(vals)
    b = len(vals)
    a = (1 - conf) / 2
    return vals[math.floor(a * (b - 1) + 1e-9)], vals[math.ceil((1 - a) * (b - 1) - 1e-9)]


def _out(e, value, lo, hi, n, method, **extra):
    d = {"id": e["id"], "type": e["type"], "value": value, "lo": lo, "hi": hi, "n": n, "method": method}
    d.update(extra)
    return d


def compute(e: dict, rows: list[dict], ivl: dict) -> dict:
    col = e["column"]
    flt = e.get("filter") or {}
    rows = [r for r in rows if all(str(r.get(k, "")) == str(v) for k, v in flt.items())]
    n = len(rows)
    conf, boots, seed = ivl["ci"], ivl["bootstrap"], ivl["seed"]
    rng = random.Random(seed)
    if e["type"] == "conteo":
        k = sum(truthy(r.get(col)) for r in rows)
        return _out(e, k, k, k, n, "exacto")
    if e["type"] == "proporcion":
        flags = [truthy(r.get(col)) for r in rows]
        if n == 0:
            return _out(e, None, None, None, 0, "wilson")
        k = sum(flags)
        cl = e.get("cluster")
        if not cl:
            lo, hi = wilson(k, n, conf)
            return _out(e, k / n, lo, hi, n, "wilson")
        groups: dict[str, list[bool]] = {}
        for r, f in zip(rows, flags):
            groups.setdefault(str(r.get(cl, "")), []).append(f)
        gl = list(groups.values())
        stats = []
        for _ in range(boots):
            tot = pos = 0
            for _i in range(len(gl)):
                g = gl[rng.randrange(len(gl))]
                tot += len(g)
                pos += sum(g)
            stats.append(pos / tot)
        lo, hi = _percentile(stats, conf)
        return _out(e, k / n, lo, hi, n, "cluster-bootstrap")
    # mediana
    vals = [x for x in (_number(r.get(col)) for r in rows) if x is not None]
    skipped = n - len(vals)
    if not vals:
        return _out(e, None, None, None, 0, "bootstrap", skipped=skipped)
    m = len(vals)
    stats = [statistics.median(vals[rng.randrange(m)] for _ in range(m)) for _ in range(boots)]
    lo, hi = _percentile(stats, conf)
    return _out(e, statistics.median(vals), lo, hi, m, "bootstrap", skipped=skipped)


def load_rows(data: bytes, ext: str) -> list[dict]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise Refused("el fichero de unidades no es UTF-8; guarda el fichero en UTF-8") from None
    ext = ext.lower()
    if ext == ".csv":
        return list(csv.DictReader(text.splitlines()))
    if ext in (".jsonl", ".ndjson"):
        rows = []
        for i, line in enumerate(text.splitlines(), 1):
            if line.strip():
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    raise Refused(f"línea {i} del fichero de unidades no es JSON válido") from None
                if not isinstance(r, dict):
                    raise Refused(f"línea {i} del fichero de unidades no es un objeto")
                rows.append(r)
        return rows
    raise Refused("el fichero de unidades debe ser .csv o .jsonl")


def _find_plan(vault: Path, plan_id: str) -> Path:
    """The plan in its design's plan_folder, `M-XXXX.md` or `M-XXXX <título>.md`
    (study_gates.find_plan, the rule the gate uses)."""
    import study_gates  # noqa: PLC0415
    path, why = study_gates.find_plan(vault, plan_id)
    if path is None:
        raise Refused(why)
    return path


def run(vault: Path, plan_id: str, units: str) -> dict:
    plan_path = _find_plan(vault, plan_id)
    proj = plan_path.parents[1]
    try:
        plan = read_plan(plan_path)
    except PlanError as e:
        raise Refused(f"plan inválido: {e}") from None
    try:
        reg = json.loads((proj / "Datos" / "registro.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Refused("falta o es ilegible Datos/registro.json del proyecto") from None
    ds = next((d for d in reg if isinstance(d, dict) and d.get("name") == units), None)
    if ds is None:
        raise Refused(f"el dataset {units!r} no está en el registro del proyecto del plan")
    if ds.get("kind") != "file":
        raise Refused(f"el dataset {units!r} no es un fichero (kind: {ds.get('kind')}); "
                      "registra el fichero de unidades como dataset de tipo file")
    raw = Path(str(ds.get("path", "")))
    cands = [raw] if raw.is_absolute() else [vault / raw, proj / raw]
    path = next((c for c in cands if c.is_file()), None)
    if path is None:
        raise Refused(f"no se encuentra el fichero del dataset {units!r}")
    data = path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    expected = ds.get("sha256", "")
    if actual != expected:
        raise Refused("la huella del fichero de unidades no coincide con el registro; "
                      "vuelve a registrar el dataset o restaura el fichero",
                      esperada=expected, actual=actual)
    rows = load_rows(data, path.suffix)
    header = set(rows[0]) if rows else set()
    if path.suffix.lower() == ".jsonl":
        header = set().union(*(r.keys() for r in rows)) if rows else set()
    if rows:
        for e in plan["estimands"]:
            for c in [e["column"], e.get("cluster"), *(e.get("filter") or {})]:
                if c and c not in header:
                    raise Refused(f"el estimando {e['id']} usa la columna {c!r}, que no está en el fichero")
    for e in plan["estimands"]:
        cl = e.get("cluster")
        if cl:
            flt = e.get("filter") or {}
            miss = sum(1 for r in rows
                       if all(str(r.get(k, "")) == str(v) for k, v in flt.items())
                       and (r.get(cl) is None or str(r.get(cl)).strip() == ""))  # JSONL null = missing
            if miss:
                raise Refused(f"el estimando {e['id']} usa el cluster {cl!r}, pero {miss} filas no lo tienen")
    estimates = [compute(e, rows, plan["intervals"]) for e in plan["estimands"]]
    return {"plan": plan_id, "units_sha256": actual, "estimates": estimates}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--units", required=True)
    a = ap.parse_args(argv)
    try:
        print(json.dumps(run(Path(a.vault), a.plan, a.units), ensure_ascii=False, indent=2))
        return 0
    except Refused as e:
        print(json.dumps({"error": str(e), **e.extra}, ensure_ascii=False))
        return 3


if __name__ == "__main__":
    sys.exit(main())
