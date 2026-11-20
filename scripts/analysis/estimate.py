"""Estimands of a measurement plan, with intervals, computed from a units file.

    estimate.py --vault V --plan M-XXXX --units <dataset name>
    estimate.py tamano --p P --half-width H [--ci 0.95]   units needed for a Wilson ±H at P

Prints {"plan", "units_sha256", "estimates": [{"id","type","value","lo","hi","n","method", …}]}.
The plan's EFFECTIVE blocks are used (plan_blocks.read_effective: its own blocks plus
`## Enmiendas` in order). Only for an amended plan, each estimate an amendment changed or
added carries `desviacion`: [{"enmienda", "datos_vistos"}], and the output adds `enmiendas`
(one summary per amendment); an un-amended plan's output is byte-identical to before.
Exit 0 ok, 3 refused (JSON {"error": ...}), 1 usage/unexpected error.

Types: proporcion (Wilson, or cluster bootstrap), conteo, mediana (bootstrap), diferencia
(p_a - p_b between two values of `group`: Newcombe, or cluster bootstrap), reutilizacion
(share of distinct `column` values seen in >= min_groups distinct `across` values: Wilson
over keys, plus a histogram of counts), supervivencia (Kaplan-Meier median of `column` with
`event` false = censored, bootstrap interval; `at` gives S(t)). `by` (categorical column,
at most 50 values) adds `groups`, one estimate per value, to proporcion, conteo, mediana and
supervivencia — for comparisons between tools or trends over periods.

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

from plan_blocks import PlanError, read_effective  # noqa: E402

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


MAX_GROUPS = 50  # `by` is for categories (tool, year), never for an identifier column


def _filtered(e: dict, rows: list[dict]) -> list[dict]:
    flt = e.get("filter") or {}
    return [r for r in rows if all(str(r.get(k, "")) == str(v) for k, v in flt.items())]


def compute(e: dict, rows: list[dict], ivl: dict) -> dict:
    rows = _filtered(e, rows)
    out = _compute_one(e, rows, ivl)
    by = e.get("by")
    if by:
        parts: dict[str, list[dict]] = {}
        for r in rows:
            v = r.get(by)
            parts.setdefault("" if v is None else str(v), []).append(r)
        if len(parts) > MAX_GROUPS:
            raise Refused(f"el estimando {e['id']} agrupa por {by!r} en {len(parts)} grupos "
                          f"(máximo {MAX_GROUPS}); 'by' es para categorías, no para identificadores")
        groups = []
        for g in sorted(parts):
            d = _compute_one(e, parts[g], ivl)
            groups.append({"group": g, **{k: v for k, v in d.items() if k not in ("id", "type")}})
        out["groups"] = groups
    return out


def _compute_one(e: dict, rows: list[dict], ivl: dict) -> dict:
    col = e["column"]
    n = len(rows)
    conf, boots, seed = ivl["ci"], ivl["bootstrap"], ivl["seed"]
    rng = random.Random(seed)
    if e["type"] == "diferencia":
        return _difference(e, rows, conf, boots, rng)
    if e["type"] == "reutilizacion":
        return _reuse(e, rows, conf)
    if e["type"] == "supervivencia":
        return _survival(e, rows, conf, boots, rng)
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


def newcombe(k1: int, n1: int, k2: int, n2: int, conf: float) -> tuple[float, float]:
    """Newcombe's hybrid score interval for p1 - p2 (method 10), from two Wilson intervals."""
    p1, p2 = k1 / n1, k2 / n2
    l1, u1 = wilson(k1, n1, conf)
    l2, u2 = wilson(k2, n2, conf)
    d = p1 - p2
    lo = d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return max(-1.0, lo), min(1.0, hi)


def _difference(e: dict, rows: list[dict], conf: float, boots: int, rng: random.Random) -> dict:
    col, grp, a, b = e["column"], e["group"], str(e["a"]), str(e["b"])
    side = {a: [], b: []}
    clusters: dict[str, list[tuple[str, bool]]] = {}
    cl = e.get("cluster")
    for r in rows:
        g = str(r.get(grp, ""))
        if g in side:
            f = truthy(r.get(col))
            side[g].append(f)
            if cl:
                clusters.setdefault(str(r.get(cl, "")), []).append((g, f))
    n1, n2 = len(side[a]), len(side[b])
    info = {"a": {"group": a, "n": n1, "value": (sum(side[a]) / n1) if n1 else None},
            "b": {"group": b, "n": n2, "value": (sum(side[b]) / n2) if n2 else None}}
    if not n1 or not n2:
        return _out(e, None, None, None, n1 + n2, "newcombe" if not cl else "cluster-bootstrap", **info)
    k1, k2 = sum(side[a]), sum(side[b])
    d = k1 / n1 - k2 / n2
    if not cl:
        lo, hi = newcombe(k1, n1, k2, n2, conf)
        return _out(e, d, lo, hi, n1 + n2, "newcombe", **info)
    cls = list(clusters.values())
    stats = []
    for _ in range(boots):
        t = {a: [0, 0], b: [0, 0]}
        for _i in range(len(cls)):
            for g, f in cls[rng.randrange(len(cls))]:
                t[g][0] += f
                t[g][1] += 1
        if t[a][1] and t[b][1]:
            stats.append(t[a][0] / t[a][1] - t[b][0] / t[b][1])
    if not stats:
        return _out(e, d, None, None, n1 + n2, "cluster-bootstrap", **info)
    lo, hi = _percentile(stats, conf)
    return _out(e, d, lo, hi, n1 + n2, "cluster-bootstrap", **info)


_REUSE_BUCKETS = ((1, 1, "1"), (2, 2, "2"), (3, 5, "3-5"), (6, 10, "6-10"), (11, None, ">10"))


def _reuse(e: dict, rows: list[dict], conf: float) -> dict:
    """Share of distinct keys (`column`) seen in >= min_groups distinct `across` values.
    Prints counts only: never a key or a group value."""
    col, across, mg = e["column"], e["across"], e.get("min_groups", 2)
    seen: dict[str, set[str]] = {}
    rows_per_key: dict[str, int] = {}
    for r in rows:
        k = r.get(col)
        if k is None or str(k).strip() == "":
            continue
        k = str(k)
        seen.setdefault(k, set()).add(str(r.get(across, "")))
        rows_per_key[k] = rows_per_key.get(k, 0) + 1
    n = len(seen)
    hist = {label: 0 for _lo, _hi, label in _REUSE_BUCKETS}
    for groups in seen.values():
        c = len(groups)
        for lo_, hi_, label in _REUSE_BUCKETS:
            if c >= lo_ and (hi_ is None or c <= hi_):
                hist[label] += 1
                break
    shared = [k for k, g in seen.items() if len(g) >= mg]
    units = sum(rows_per_key.values())
    units_shared = sum(rows_per_key[k] for k in shared)
    extra = {"histogram": hist, "max_groups": max((len(g) for g in seen.values()), default=0),
             "rows": units, "rows_with_shared_key": units_shared}
    if n == 0:
        return _out(e, None, None, None, 0, "wilson", **extra)
    lo, hi = wilson(len(shared), n, conf)
    return _out(e, len(shared) / n, lo, hi, n, "wilson", **extra)


def _km(times: list[tuple[float, bool]], w: list[int] | None = None) -> list[tuple[float, float]]:
    """Kaplan-Meier steps [(t, S(t))] at each event time; `times` sorted by t, `w` the
    number of copies of each (a bootstrap resample without re-sorting)."""
    w = w or [1] * len(times)
    at_risk = sum(w)
    s = 1.0
    steps = []
    i = 0
    while i < len(times):
        t = times[i][0]
        d = c = 0
        while i < len(times) and times[i][0] == t:
            d += times[i][1] * w[i]
            c += w[i]
            i += 1
        if d:
            s *= 1 - d / at_risk
            steps.append((t, s))
        at_risk -= c
    return steps


def _km_median(steps) -> float | None:
    return next((t for t, s in steps if s <= 0.5), None)


def _km_at(steps, t: float) -> float:
    s = 1.0
    for st, sv in steps:
        if st > t:
            break
        s = sv
    return s


def _finite(v: float) -> float | None:
    return None if v == math.inf else v


def _survival(e: dict, rows: list[dict], conf: float, boots: int, rng: random.Random) -> dict:
    """Kaplan-Meier median of `column` (time to the event) with right censoring: `event`
    true = the change was seen, false = still unchanged at the end of observation."""
    data = []
    for r in rows:
        t = _number(r.get(e["column"]))
        if t is None or t < 0:
            continue
        data.append((t, truthy(r.get(e["event"]))))
    skipped = len(rows) - len(data)
    data.sort(key=lambda x: x[0])
    m = len(data)
    events = sum(f for _t, f in data)
    if not m:
        return _out(e, None, None, None, 0, "kaplan-meier-bootstrap", skipped=skipped, events=0,
                    median_reached=False, survival_at=[])
    steps = _km(data)
    med = _km_median(steps)
    at = e.get("at") or []
    meds, at_stats = [], [[] for _ in at]
    for _ in range(boots):
        w = [0] * m
        for _i in range(m):
            w[rng.randrange(m)] += 1
        st = _km(data, w)
        bm = _km_median(st)
        meds.append(math.inf if bm is None else bm)
        for j, t in enumerate(at):
            at_stats[j].append(_km_at(st, t))
    lo, hi = _percentile(meds, conf)
    surv = []
    for j, t in enumerate(at):
        slo, shi = _percentile(at_stats[j], conf)
        surv.append({"t": t, "s": _km_at(steps, t), "lo": slo, "hi": shi})
    return _out(e, med, _finite(lo), _finite(hi), m, "kaplan-meier-bootstrap", skipped=skipped, events=events,
                median_reached=med is not None, survival_at=surv)


def sample_size(p: float, half_width: float, conf: float = 0.95, max_n: int = 10_000_000) -> int:
    """Smallest n whose Wilson interval at proportion p is no wider than ±half_width."""
    def width(n: int) -> float:
        lo, hi = wilson(round(p * n), n, conf)
        return (hi - lo) / 2
    lo_n, hi_n = 1, 1
    while width(hi_n) > half_width:
        hi_n *= 2
        if hi_n > max_n:
            raise Refused(f"hacen falta más de {max_n} unidades para ±{half_width}")
    while lo_n < hi_n:
        mid = (lo_n + hi_n) // 2
        if width(mid) <= half_width:
            hi_n = mid
        else:
            lo_n = mid + 1
    return hi_n


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
        plan = read_effective(plan_path)
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
            for c in [e["column"], *(e.get(k) for k in ("cluster", "by", "group", "across", "event")),
                      *(e.get("filter") or {})]:
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
    out = {"plan": plan_id, "units_sha256": actual, "estimates": estimates}
    if plan["amendments"]:  # an un-amended plan's output stays byte-identical
        for est in estimates:
            if est["id"] in plan["deviations"]:
                est["desviacion"] = plan["deviations"][est["id"]]
        out["enmiendas"] = plan["summary"]
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["tamano"]:
        tp = argparse.ArgumentParser(prog="estimate.py tamano")
        tp.add_argument("--p", type=float, required=True)
        tp.add_argument("--half-width", type=float, required=True)
        tp.add_argument("--ci", type=float, default=0.95)
        t = tp.parse_args(argv[1:])
        if not (0 <= t.p <= 1 and 0 < t.half_width < 0.5 and 0 < t.ci < 1):
            print(json.dumps({"error": "p en [0, 1], half-width en (0, 0.5) y ci en (0, 1)"}))
            return 3
        try:
            n = sample_size(t.p, t.half_width, t.ci)
        except Refused as e:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
            return 3
        print(json.dumps({"p": t.p, "half_width": t.half_width, "ci": t.ci, "n": n, "method": "wilson"}))
        return 0
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
