"""Estimands of a measurement plan, with intervals, computed from a units file.

    estimate.py --vault V --plan M-XXXX --units <dataset name> [--units-for TABLA=<dataset> ...]
    estimate.py tamano --p P --half-width H [--ci 0.95]   units needed for a Wilson ±H at P
    estimate.py tabla-latex --json <salida de estimate.py> --id <estimando> [--variant V]
                [--caption TEXTO] [--label tab:x] [--decimals N]   booktabs table of one estimand

Prints {"plan", "units_sha256", "estimates": [{"id","type","value","lo","hi","n","method", …}]}.
With named tables (`--units-for`) it adds "tables": {tabla: {"dataset", "sha256"}}; with no
estimand on the default table there is no `units_sha256` (and `--units` is refused).
Exit 0 ok, 3 refused (JSON {"error": ...}), 1 usage/unexpected error.

Types: proporcion (Wilson, or cluster bootstrap), conteo, mediana (bootstrap), diferencia
(p_a - p_b between two values of `group`: Newcombe, or cluster bootstrap), reutilizacion
(share of distinct `column` values seen in >= min_groups distinct `across` values: Wilson
over keys, plus a histogram of counts), supervivencia (Kaplan-Meier median of `column` with
`event` false = censored, bootstrap interval; `at` gives S(t); `curve: true` adds the KM
steps (t, S(t), at_risk) whose risk set holds at least CURVE_MIN_AT_RISK units, the rest
suppressed and counted), tabla (cross-tab of two categorical columns `rows` x `cols`: counts
and row proportions with Wilson intervals; refuses a column with more than 50 values or one
that looks like an identifier), interseccion (share of units with ALL `columns` true, Wilson
or cluster bootstrap, plus the count of every true/false combination), tendencia (trend of
the 0/1 `column` across the ordered `by`: Cochran-Armitage z and two-sided p, the WLS slope
per unit of score with a Wald interval, one Wilson estimate per level), cuantiles
(quantiles `q` of a numeric `column`, linear interpolation (R type 7), bootstrap intervals).
`by` (categorical column, at most 50 values) adds `groups`, one estimate per value, to
proporcion, conteo, mediana, supervivencia, interseccion and cuantiles. `variants` adds
`variants`, the estimand recomputed with each variant's filter merged in (variant wins).
`units` names the units table of an estimand (default: the `--units` one).

Plan-level `families` (diferencia only): each member gets `family` = {id, method, m, p,
p_adjusted, p_method, ci_adjusted, lo_adjusted, hi_adjusted}. p is two-sided: for Newcombe,
the smallest alpha whose 1-alpha Newcombe interval excludes 0 (test inversion); for the
cluster bootstrap, min(1, 2·min(#{d*<=0}, #{d*>=0})/B). p_adjusted is Holm's (or
Bonferroni's); the adjusted interval is the Bonferroni one, at level 1-(1-ci)/m. m counts
the members with a p (a member with an empty side has none).

The units file may be sensitive: only aggregates are ever printed, never rows or cell values
(except the levels of a `by`, `tabla` or `tendencia` column, which must be categories).
In JSONL a row lacking the column counts as falsy (proporcion/conteo) or skipped (mediana); the
missing-column check needs at least the header (CSV) or one row carrying the key (JSONL).
Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import random
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ledger"))

from plan_blocks import _TABLE, DEFAULT_TABLE, PlanError, read_plan  # noqa: E402

TRUTHY = {"1", "true", "sí", "si", "yes"}
CURVE_MIN_AT_RISK = 5   # a KM step is printed only if its risk set holds at least this many units
ID_MIN_DISTINCT = 20    # tabla: this many distinct values, most rows distinct -> an identifier
_ID_SHAPE = re.compile(r"^[0-9a-fA-F]{16,}$|^[A-Za-z0-9+/=_-]{32,}$|@|://")


class Refused(Exception):
    def __init__(self, msg: str, **extra):
        super().__init__(msg)
        self.extra = extra


def _z(conf: float) -> float:
    return statistics.NormalDist().inv_cdf(1 - (1 - conf) / 2)


def wilson(k: int, n: int, conf: float) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 1.0)
    return _wilson_z(k, n, _z(conf))


def _wilson_z(k: int, n: int, z: float) -> tuple[float, float]:
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


def _variant(e: dict, v: dict) -> dict:
    """The estimand as variant `v` computes it: its filter merged in, the variant winning."""
    ve = {k: x for k, x in e.items() if k not in ("variants", "family")}
    ve["filter"] = {**(e.get("filter") or {}), **v["filter"]}
    return ve


def compute(e: dict, rows: list[dict], ivl: dict) -> dict:
    out = _compute_grouped(e, rows, ivl)
    if e.get("variants"):
        out["variants"] = [
            {"variant": v["id"], **{k: x for k, x in _compute_grouped(_variant(e, v), rows, ivl).items()
                                    if k not in ("id", "type")}}
            for v in e["variants"]]
    return out


def _compute_grouped(e: dict, rows: list[dict], ivl: dict) -> dict:
    rows = _filtered(e, rows)
    out = _compute_one(e, rows, ivl)
    by = e.get("by") if e["type"] != "tendencia" else None   # tendencia's `by` is its ordered axis
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
    col = e.get("column")
    n = len(rows)
    conf, boots, seed = ivl["ci"], ivl["bootstrap"], ivl["seed"]
    rng = random.Random(seed)
    if e["type"] == "diferencia":
        return _difference(e, rows, conf, boots, rng)
    if e["type"] == "reutilizacion":
        return _reuse(e, rows, conf)
    if e["type"] == "supervivencia":
        return _survival(e, rows, conf, boots, rng)
    if e["type"] == "tabla":
        return _crosstab(e, rows, conf)
    if e["type"] == "interseccion":
        return _intersection(e, rows, conf, boots, rng)
    if e["type"] == "tendencia":
        return _trend(e, rows, conf)
    if e["type"] == "cuantiles":
        return _quantiles(e, rows, conf, boots, rng)
    if e["type"] == "conteo":
        k = sum(truthy(r.get(col)) for r in rows)
        return _out(e, k, k, k, n, "exacto")
    if e["type"] == "proporcion":
        return _proportion(e, rows, [truthy(r.get(col)) for r in rows], conf, boots, rng)
    # mediana
    vals = [x for x in (_number(r.get(col)) for r in rows) if x is not None]
    skipped = n - len(vals)
    if not vals:
        return _out(e, None, None, None, 0, "bootstrap", skipped=skipped)
    m = len(vals)
    stats = [statistics.median(vals[rng.randrange(m)] for _ in range(m)) for _ in range(boots)]
    lo, hi = _percentile(stats, conf)
    return _out(e, statistics.median(vals), lo, hi, m, "bootstrap", skipped=skipped)


def _proportion(e: dict, rows: list[dict], flags: list[bool], conf: float, boots: int,
                rng: random.Random, **extra) -> dict:
    """Share of true `flags` (one per row): Wilson, or cluster bootstrap over `cluster`."""
    n = len(flags)
    if n == 0:
        return _out(e, None, None, None, 0, "wilson", **extra)
    k = sum(flags)
    cl = e.get("cluster")
    if not cl:
        lo, hi = wilson(k, n, conf)
        return _out(e, k / n, lo, hi, n, "wilson", **extra)
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
    return _out(e, k / n, lo, hi, n, "cluster-bootstrap", **extra)


def _level(v) -> str:
    return "" if v is None else str(v)


def _categorical_levels(e: dict, col: str, values: list[str]) -> list[str]:
    """The sorted levels of a column printed by `tabla`, refusing anything that may be an
    identifier. The refusal never quotes a value."""
    levels = sorted(set(values))
    what = f"el estimando {e['id']} cruza la columna {col!r}"
    if len(levels) > MAX_GROUPS:
        raise Refused(f"{what}, que tiene {len(levels)} valores distintos (máximo {MAX_GROUPS}); "
                      "'tabla' es para categorías, no para identificadores")
    if len(levels) >= ID_MIN_DISTINCT and len(levels) > len(values) / 2:
        raise Refused(f"{what}, en la que casi cada fila tiene un valor distinto: parece un identificador")
    if any(len(v) > 64 or _ID_SHAPE.search(v) for v in levels):
        raise Refused(f"{what}, que tiene valores con forma de identificador (hash, correo, URL o "
                      "texto largo); no se imprimen")
    return levels


def _crosstab(e: dict, rows: list[dict], conf: float) -> dict:
    """Counts of `rows` x `cols`, and the row proportions with Wilson intervals."""
    rv = [_level(r.get(e["rows"])) for r in rows]
    cv = [_level(r.get(e["cols"])) for r in rows]
    rl = _categorical_levels(e, e["rows"], rv)
    cl = _categorical_levels(e, e["cols"], cv)
    counts: dict[tuple[str, str], int] = {}
    for a, b in zip(rv, cv):
        counts[(a, b)] = counts.get((a, b), 0) + 1
    row_totals = [sum(counts.get((a, b), 0) for b in cl) for a in rl]
    col_totals = [sum(counts.get((a, b), 0) for a in rl) for b in cl]
    cells = []
    for a, nr in zip(rl, row_totals):
        for b in cl:
            k = counts.get((a, b), 0)
            lo, hi = wilson(k, nr, conf) if nr else (None, None)
            cells.append({"row": a, "col": b, "count": k, "p": k / nr if nr else None, "lo": lo, "hi": hi})
    return _out(e, None, None, None, len(rows), "wilson", row_levels=rl, col_levels=cl,
                row_totals=row_totals, col_totals=col_totals, cells=cells)


def _intersection(e: dict, rows: list[dict], conf: float, boots: int, rng: random.Random) -> dict:
    """Share of rows with every `columns` true (A ∧ B ∧ …), plus the count of each
    true/false combination (largest first) and of each column on its own."""
    cols = e["columns"]
    bits = [tuple(truthy(r.get(c)) for c in cols) for r in rows]
    seen: dict[tuple, int] = {}
    for b in bits:
        seen[b] = seen.get(b, 0) + 1
    combos = [{"true": [c for c, f in zip(cols, p) if f], "count": seen.get(p, 0)}
              for p in itertools.product((True, False), repeat=len(cols))]
    combos.sort(key=lambda d: -d["count"])   # stable: ties keep the all-true-first order
    marginals = {c: sum(b[i] for b in bits) for i, c in enumerate(cols)}
    return _proportion(e, rows, [all(b) for b in bits], conf, boots, rng,
                       marginals=marginals, combinations=combos)


def _score(x: float):
    return int(x) if float(x).is_integer() else x


def _trend(e: dict, rows: list[dict], conf: float) -> dict:
    """Cochran-Armitage trend of the 0/1 `column` across the ordered `by`. Scores: the
    level's number if every level is numeric and there is no `order`, else its position."""
    col, by, order = e["column"], e["by"], e.get("order")
    allowed = None if order is None else [str(x) for x in order]
    parts: dict[str, list[bool]] = {}
    skipped = 0
    for r in rows:
        lv = _level(r.get(by))
        if lv.strip() == "" or (allowed is not None and lv not in allowed):
            skipped += 1
            continue
        parts.setdefault(lv, []).append(truthy(r.get(col)))
    if allowed is not None:
        levels, scores = allowed, list(range(len(allowed)))
    else:
        levels = sorted(parts)
        nums = [_number(v) for v in levels]
        if levels and all(x is not None for x in nums):
            pairs = sorted(zip(nums, levels))
            levels, scores = [v for _x, v in pairs], [_score(x) for x, _v in pairs]
        else:
            scores = list(range(len(levels)))
    if len(levels) > MAX_GROUPS:
        raise Refused(f"el estimando {e['id']} sigue la tendencia por {by!r} en {len(levels)} niveles "
                      f"(máximo {MAX_GROUPS}); 'by' es para periodos o categorías ordenadas")
    groups, data = [], []
    for lv, s in zip(levels, scores):
        f = parts.get(lv, [])
        n, k = len(f), sum(f)
        lo, hi = wilson(k, n, conf) if n else (None, None)
        groups.append({"group": lv, "score": s, "value": k / n if n else None, "lo": lo, "hi": hi, "n": n})
        if n:
            data.append((s, n, k))
    big_n = sum(n for _s, n, _k in data)
    extra = {"skipped": skipped, "z": None, "p_value": None, "groups": groups}
    if len(data) < 2:
        return _out(e, None, None, None, big_n, "cochran-armitage", **extra)
    sbar = sum(s * n for s, n, _k in data) / big_n
    sxx = sum(n * (s - sbar) ** 2 for s, n, _k in data)
    t = sum((s - sbar) * k for s, _n, k in data)
    pbar = sum(k for _s, _n, k in data) / big_n
    slope = t / sxx
    var = sum(n * (s - sbar) ** 2 * (k / n) * (1 - k / n) for s, n, k in data) / sxx ** 2
    lo = hi = None
    if var > 0:
        h = _z(conf) * math.sqrt(var)
        lo, hi = slope - h, slope + h
    if 0 < pbar < 1:
        z = t / math.sqrt(pbar * (1 - pbar) * sxx)
        extra["z"], extra["p_value"] = z, 2 * statistics.NormalDist().cdf(-abs(z))
    return _out(e, slope, lo, hi, big_n, "cochran-armitage", **extra)


def _quantile(vals: list[float], q: float) -> float:
    """Linear interpolation between order statistics (R type 7) of sorted `vals`."""
    h = (len(vals) - 1) * q
    i = math.floor(h)
    if i + 1 >= len(vals):
        return vals[-1]
    return vals[i] + (h - i) * (vals[i + 1] - vals[i])


def _quantiles(e: dict, rows: list[dict], conf: float, boots: int, rng: random.Random) -> dict:
    qs = e["q"]
    vals = sorted(x for x in (_number(r.get(e["column"])) for r in rows) if x is not None)
    skipped = len(rows) - len(vals)
    if not vals:
        return _out(e, None, None, None, 0, "bootstrap", skipped=skipped,
                    quantiles=[{"q": q, "value": None, "lo": None, "hi": None} for q in qs])
    m = len(vals)
    stats: list[list[float]] = [[] for _ in qs]
    for _ in range(boots):
        s = sorted(vals[rng.randrange(m)] for _i in range(m))
        for j, q in enumerate(qs):
            stats[j].append(_quantile(s, q))
    out = []
    for j, q in enumerate(qs):
        lo, hi = _percentile(stats[j], conf)
        out.append({"q": q, "value": _quantile(vals, q), "lo": lo, "hi": hi})
    return _out(e, None, None, None, m, "bootstrap", skipped=skipped, quantiles=out)


def newcombe(k1: int, n1: int, k2: int, n2: int, conf: float) -> tuple[float, float]:
    """Newcombe's hybrid score interval for p1 - p2 (method 10), from two Wilson intervals."""
    return _newcombe_z(k1, n1, k2, n2, _z(conf))


def _newcombe_z(k1: int, n1: int, k2: int, n2: int, z: float) -> tuple[float, float]:
    p1, p2 = k1 / n1, k2 / n2
    l1, u1 = _wilson_z(k1, n1, z)
    l2, u2 = _wilson_z(k2, n2, z)
    d = p1 - p2
    lo = d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return max(-1.0, lo), min(1.0, hi)


def newcombe_p(k1: int, n1: int, k2: int, n2: int) -> float:
    """Two-sided p of p1 = p2 by inverting Newcombe's interval: the smallest alpha whose
    1-alpha interval excludes 0 (bisection on z; 0.0 when even z = 64 excludes it)."""
    d = k1 / n1 - k2 / n2
    if d == 0:
        return 1.0

    def inside(z: float) -> bool:   # 0 still inside the interval at this z
        lo, hi = _newcombe_z(k1, n1, k2, n2, z)
        return lo <= 0 if d > 0 else hi >= 0

    lo_z, hi_z = 0.0, 1.0
    while not inside(hi_z):
        lo_z, hi_z = hi_z, hi_z * 2
        if hi_z > 64:
            return 0.0
    for _ in range(100):
        mid = (lo_z + hi_z) / 2
        if inside(mid):
            hi_z = mid
        else:
            lo_z = mid
    return min(1.0, 2 * statistics.NormalDist().cdf(-hi_z))


def _diff_sides(e: dict, rows: list[dict]):
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
    return a, b, side, clusters


def _diff_boot(clusters: dict, a: str, b: str, boots: int, rng: random.Random) -> list[float]:
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
    return stats


def _diff_test(e: dict, rows: list[dict], ivl: dict):
    """(p, p_method, interval(conf)) of a diferencia on its filtered rows, or (None, method,
    None) when a side is empty. Same rows, seed and resamples as its estimate."""
    a, b, side, clusters = _diff_sides(e, _filtered(e, rows))
    n1, n2 = len(side[a]), len(side[b])
    if not e.get("cluster"):
        if not n1 or not n2:
            return None, "newcombe-inversion", None
        k1, k2 = sum(side[a]), sum(side[b])
        return (newcombe_p(k1, n1, k2, n2), "newcombe-inversion",
                lambda conf: newcombe(k1, n1, k2, n2, conf))
    if not n1 or not n2:
        return None, "bootstrap-percentil", None
    stats = _diff_boot(clusters, a, b, ivl["bootstrap"], random.Random(ivl["seed"]))
    if not stats:
        return None, "bootstrap-percentil", None
    le, ge = sum(s <= 0 for s in stats), sum(s >= 0 for s in stats)
    return min(1.0, 2 * min(le, ge) / len(stats)), "bootstrap-percentil", lambda conf: _percentile(stats, conf)


def _adjust(ps: list[float], method: str) -> list[float]:
    m = len(ps)
    if method == "bonferroni":
        return [min(1.0, m * p) for p in ps]
    adj = [0.0] * m   # Holm step-down, monotone
    running = 0.0
    for rank, i in enumerate(sorted(range(m), key=lambda j: ps[j])):
        running = max(running, min(1.0, (m - rank) * ps[i]))
        adj[i] = running
    return adj


def apply_families(plan: dict, estimates: list[dict], rows_of: list[list[dict]]) -> None:
    """Adds `family` to each member of a plan-level family (plan_blocks puts `family` on them)."""
    ivl = plan["intervals"]
    fams: dict[str, list[int]] = {}
    for i, e in enumerate(plan["estimands"]):
        if e.get("family"):
            fams.setdefault(e["family"]["id"], []).append(i)
    for fid, idx in fams.items():
        method = plan["estimands"][idx[0]]["family"]["method"]
        tests = [_diff_test(plan["estimands"][i], rows_of[i], ivl) for i in idx]
        have = [j for j, t in enumerate(tests) if t[0] is not None]
        m = len(have)
        adj = dict(zip(have, _adjust([tests[j][0] for j in have], method)))
        ci_adj = 1 - (1 - ivl["ci"]) / m if m else None
        for j, i in enumerate(idx):
            p, p_method, interval = tests[j]
            lo, hi = interval(ci_adj) if p is not None else (None, None)
            estimates[i]["family"] = {"id": fid, "method": method, "m": m, "p": p, "p_adjusted": adj.get(j),
                                      "p_method": p_method, "ci_adjusted": ci_adj,
                                      "lo_adjusted": lo, "hi_adjusted": hi}


def _difference(e: dict, rows: list[dict], conf: float, boots: int, rng: random.Random) -> dict:
    cl = e.get("cluster")
    a, b, side, clusters = _diff_sides(e, rows)
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
    stats = _diff_boot(clusters, a, b, boots, rng)
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
    curve = _km_curve(data) if e.get("curve") else {}
    if not m:
        return _out(e, None, None, None, 0, "kaplan-meier-bootstrap", skipped=skipped, events=0,
                    median_reached=False, survival_at=[], **curve)
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
                median_reached=med is not None, survival_at=surv, **curve)


def _km_curve(data: list[tuple[float, bool]]) -> dict:
    """The KM steps (t, S(t), at_risk) of `data` (sorted by t), as aggregates: a step whose
    risk set (units still unchanged just before t) is smaller than CURVE_MIN_AT_RISK is not
    printed, only counted, so no step describes fewer than that many units."""
    steps, shown, hidden = _km(data), [], 0
    at_risk, i, k = len(data), 0, 0
    while i < len(data):
        t = data[i][0]
        c = 0
        while i < len(data) and data[i][0] == t:
            c += 1
            i += 1
        if k < len(steps) and steps[k][0] == t:
            if at_risk >= CURVE_MIN_AT_RISK:
                shown.append({"t": t, "s": steps[k][1], "at_risk": at_risk})
            else:
                hidden += 1
            k += 1
        at_risk -= c
    return {"curve": shown, "curve_min_at_risk": CURVE_MIN_AT_RISK, "curve_suppressed": hidden}


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


def _load_table(vault: Path, proj: Path, reg: list, units: str, table: str | None):
    """(rows, header, sha256) of a registered `kind: file` dataset whose sha256 matches the
    registry. `table` None is the default table: its messages are the ones estimate.py
    always gave; a named table's start with «tabla 'x': »."""
    pre = "" if table is None else f"tabla {table!r}: "
    ds = next((d for d in reg if isinstance(d, dict) and d.get("name") == units), None)
    if ds is None:
        raise Refused(f"{pre}el dataset {units!r} no está en el registro del proyecto del plan")
    if ds.get("kind") != "file":
        raise Refused(f"{pre}el dataset {units!r} no es un fichero (kind: {ds.get('kind')}); "
                      "registra el fichero de unidades como dataset de tipo file")
    raw = Path(str(ds.get("path", "")))
    cands = [raw] if raw.is_absolute() else [vault / raw, proj / raw]
    path = next((c for c in cands if c.is_file()), None)
    if path is None:
        raise Refused(f"{pre}no se encuentra el fichero del dataset {units!r}")
    data = path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    expected = ds.get("sha256", "")
    if actual != expected:
        raise Refused(f"{pre}la huella del fichero de unidades no coincide con el registro; "
                      "vuelve a registrar el dataset o restaura el fichero",
                      esperada=expected, actual=actual, **({"tabla": table} if table else {}))
    try:
        rows = load_rows(data, path.suffix)
    except Refused as e:
        raise Refused(f"{pre}{e}") from None
    header = set(rows[0]) if rows else set()
    if path.suffix.lower() == ".jsonl":
        header = set().union(*(r.keys() for r in rows)) if rows else set()
    return rows, header, actual


def _columns(e: dict) -> list[str]:
    """Every column an estimand reads, in the order the missing-column check reports them."""
    cols = [e.get("column"), *(e.get(k) for k in ("cluster", "by", "group", "across", "event")),
            *(e.get("filter") or {})]
    cols += [e.get("rows"), e.get("cols"), *(e.get("columns") or [])]
    for v in e.get("variants") or []:
        cols += list(v["filter"])
    return cols


def _check_cluster(e: dict, rows: list[dict], label: str) -> None:
    cl = e.get("cluster")
    if cl:
        flt = e.get("filter") or {}
        miss = sum(1 for r in rows
                   if all(str(r.get(k, "")) == str(v) for k, v in flt.items())
                   and (r.get(cl) is None or str(r.get(cl)).strip() == ""))  # JSONL null = missing
        if miss:
            raise Refused(f"el estimando {label} usa el cluster {cl!r}, pero {miss} filas no lo tienen")


def run(vault: Path, plan_id: str, units: str | None, units_for: dict[str, str] | None = None) -> dict:
    """Every estimand of the plan; `units` is the default table's dataset, `units_for` maps
    the other tables (an estimand's `units`) to their datasets."""
    units_for = dict(units_for or {})
    plan_path = _find_plan(vault, plan_id)
    proj = plan_path.parents[1]
    try:
        plan = read_plan(plan_path)
    except PlanError as e:
        raise Refused(f"plan inválido: {e}") from None
    needed = {e.get("units") for e in plan["estimands"]}
    for e in plan["estimands"]:
        t = e.get("units")
        if t is None and not units:
            raise Refused(f"el estimando {e['id']} usa la tabla por defecto: falta --units <dataset>")
        if t is not None and t not in units_for:
            raise Refused(f"el estimando {e['id']} usa la tabla {t!r}: falta --units-for {t}=<dataset>")
    if units and None not in needed:
        raise Refused("ningún estimando usa la tabla por defecto: quita --units (cada uno nombra su 'units')")
    unused = sorted(set(units_for) - needed)
    if unused:
        raise Refused(f"ningún estimando usa la tabla {unused[0]!r}: quita --units-for {unused[0]}=…")
    try:
        reg = json.loads((proj / "Datos" / "registro.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Refused("falta o es ilegible Datos/registro.json del proyecto") from None
    tables: dict[str | None, tuple[list[dict], set, str]] = {}
    if units:
        tables[None] = _load_table(vault, proj, reg, units, None)
    for t in sorted(units_for):
        tables[t] = _load_table(vault, proj, reg, units_for[t], t)
    for e in plan["estimands"]:
        rows, header, _sha = tables[e.get("units")]
        if rows:
            for c in _columns(e):
                if c and c not in header:
                    where = "el fichero" if e.get("units") is None else f"la tabla {e['units']!r}"
                    raise Refused(f"el estimando {e['id']} usa la columna {c!r}, que no está en {where}")
    for e in plan["estimands"]:
        rows = tables[e.get("units")][0]
        _check_cluster(e, rows, e["id"])
        for v in e.get("variants") or []:
            _check_cluster(_variant(e, v), rows, f"{e['id']} (variante {v['id']})")
    rows_of = [tables[e.get("units")][0] for e in plan["estimands"]]
    estimates = [compute(e, rows, plan["intervals"]) for e, rows in zip(plan["estimands"], rows_of)]
    apply_families(plan, estimates, rows_of)
    out: dict = {"plan": plan_id}
    if None in tables:
        out["units_sha256"] = tables[None][2]
    if units_for:
        out["tables"] = {t: {"dataset": units_for[t], "sha256": tables[t][2]} for t in sorted(units_for)}
    out["estimates"] = estimates
    return out


# ---- the question's units tables (study_gates.estimates_reproducible) -------

def _unquote(s: str) -> str:
    s = s.strip()
    return s[1:-1] if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"') else s


def _strip_comment(s: str) -> str:
    return re.sub(r"\s+#.*$", "", s).strip()


def _blank(s: str) -> bool:
    return not s or (s.startswith("<") and s.endswith(">"))


def parse_units_spec(fm_lines: list[str]) -> dict | None:
    """`units_dataset` of a question's frontmatter lines, as {"default": name | None,
    "tables": {tabla: name}}; None when absent or empty. Accepted forms:

        units_dataset: unidades-ficheros                         (one table, the default)
        units_dataset: {default: unidades-ficheros, repos: unidades-repos}
        units_dataset:
          default: unidades-ficheros
          repos: unidades-repos

    `default` is the table of the estimands without `units`. Raises ValueError on a
    malformed value."""
    for i, line in enumerate(fm_lines):
        m = re.match(r"^units_dataset[ \t]*:(.*)$", line)
        if not m:
            continue
        raw = _strip_comment(m.group(1))
        pairs: list[tuple[str, str]] = []
        if raw.startswith("{"):
            if not raw.endswith("}"):
                raise ValueError("units_dataset: falta la '}' del mapa")
            items = [x for x in raw[1:-1].split(",") if x.strip()]
            for it in items:
                k, sep, v = it.partition(":")
                if not sep:
                    raise ValueError("units_dataset: cada entrada del mapa es «tabla: dataset»")
                pairs.append((_unquote(k), _unquote(v)))
        elif raw:
            name = _unquote(raw)
            return None if _blank(name) else {"default": name, "tables": {}}
        else:
            for nxt in fm_lines[i + 1:]:
                if not nxt.strip() or nxt.strip().startswith("#"):
                    continue
                if not nxt.startswith((" ", "\t")):
                    break
                k, sep, v = _strip_comment(nxt).partition(":")
                if not sep:
                    raise ValueError("units_dataset: cada línea del mapa es «tabla: dataset»")
                pairs.append((_unquote(k), _unquote(v)))
        if not pairs:
            return None
        spec: dict = {"default": None, "tables": {}}
        for k, v in pairs:
            if not v or _blank(v):
                raise ValueError(f"units_dataset: la tabla {k!r} no nombra un dataset")
            if k == DEFAULT_TABLE:
                spec["default"] = v
            elif not _TABLE.match(k):
                raise ValueError(f"units_dataset: {k!r} no es un nombre de tabla (letras, cifras, '-' o '_')")
            elif k in spec["tables"]:
                raise ValueError(f"units_dataset: tabla {k!r} repetida")
            else:
                spec["tables"][k] = v
        return spec
    return None


def units_map_from_question(note: Path) -> dict | None:
    """parse_units_spec on a question note (None: no frontmatter or no units_dataset)."""
    import notes  # noqa: PLC0415  (scripts/ledger is on sys.path)
    try:
        parts = notes.split_note(Path(note).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError):
        return None
    return parse_units_spec(parts[0]) if parts else None


def units_argv(spec: dict) -> list[str]:
    """The estimate.py arguments for a parse_units_spec result."""
    out = ["--units", spec["default"]] if spec.get("default") else []
    for t in sorted(spec.get("tables") or {}):
        out += ["--units-for", f"{t}={spec['tables'][t]}"]
    return out


def units_datasets(spec: dict) -> list[str]:
    """Every dataset name of a spec (default first), for the registered-after-freeze check."""
    names = [spec["default"]] if spec.get("default") else []
    return names + [spec["tables"][t] for t in sorted(spec.get("tables") or {})]


# ---- tabla-latex -------------------------------------------------------------

_TEX = {"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
        "~": r"\textasciitilde{}", "^": r"\textasciicircum{}", "\\": r"\textbackslash{}"}


def _tex(s) -> str:
    return "".join(_TEX.get(ch, ch) for ch in str(s))


def _num_tex(x, dec: int | None) -> str:
    """A number as estimate.py printed it (Python's float repr is JSON's), or with the
    declared --decimals; never any other rounding."""
    if x is None:
        return "---"
    if isinstance(x, bool):
        return "sí" if x else "no"
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float):
        return repr(x) if dec is None else f"{x:.{dec}f}"
    return _tex(x)


def _ivl_tex(d: dict, dec: int | None, lo: str = "lo", hi: str = "hi") -> str:
    if d.get(lo) is None and d.get(hi) is None:
        return "---"
    return f"[{_num_tex(d.get(lo), dec)}, {_num_tex(d.get(hi), dec)}]"


def _scalar_rows(d: dict, typ: str, label: str, dec: int | None) -> list[list[str]]:
    """Rows (label, value, interval, n, method) of one result and what hangs from it."""
    val = _num_tex(d.get("value"), dec)
    if typ == "supervivencia" and d.get("value") is None and d.get("median_reached") is False:
        val = "no alcanzada"
    main = label + (" (pendiente)" if typ == "tendencia" else "")
    rows = [[main, val, _ivl_tex(d, dec), _num_tex(d.get("n"), None), _tex(d.get("method", ""))]]
    sub = label + r" $\cdot$ "
    if typ == "diferencia":
        for k in ("a", "b"):
            s = d.get(k) or {}
            rows.append([f"{sub}{k} = {_tex(s.get('group', ''))}", _num_tex(s.get("value"), dec), "",
                         _num_tex(s.get("n"), None), ""])
    if typ == "reutilizacion":
        for k, c in (d.get("histogram") or {}).items():
            rows.append([f"{sub}claves en {_tex(k)} grupos", _num_tex(c, None), "", "", ""])
    if typ == "supervivencia":
        for s in d.get("survival_at") or []:
            rows.append([f"{sub}S({_num_tex(s.get('t'), None)})", _num_tex(s.get("s"), dec), _ivl_tex(s, dec), "", ""])
    if typ == "interseccion":
        cols = list(d.get("marginals") or {})
        for c, k in (d.get("marginals") or {}).items():
            rows.append([f"{sub}{_tex(c)}", _num_tex(k, None), "", "", ""])
        for comb in d.get("combinations") or []:
            name = r" $\wedge$ ".join(_tex(c) if c in comb["true"] else r"$\neg$" + _tex(c) for c in cols)
            rows.append([f"{sub}{name}", _num_tex(comb["count"], None), "", "", ""])
    if typ == "tendencia":
        for k in ("z", "p_value"):
            rows.append([f"{sub}{'z' if k == 'z' else 'p'}", _num_tex(d.get(k), dec), "", "", ""])
    for g in d.get("groups") or []:
        glabel = f"{sub}{_tex(g['group'])}"
        if typ == "tendencia":
            glabel += f" ({_num_tex(g.get('score'), None)})"
        rows.append([glabel, _num_tex(g.get("value"), dec) if not (
            typ == "supervivencia" and g.get("value") is None and g.get("median_reached") is False)
            else "no alcanzada", _ivl_tex(g, dec), _num_tex(g.get("n"), None), _tex(g.get("method", ""))])
    f = d.get("family")
    if f:
        rows.append([f"{sub}p ({_tex(f['p_method'])})", _num_tex(f.get("p"), dec), "", "", ""])
        rows.append([f"{sub}p ajustado ({_tex(f['method'])}, m = {f['m']})", _num_tex(f.get("p_adjusted"), dec),
                     "", "", ""])
        rows.append([f"{sub}intervalo ajustado ({_num_tex(f.get('ci_adjusted'), None)})", "",
                     _ivl_tex(f, dec, "lo_adjusted", "hi_adjusted"), "", ""])
    return rows


def latex_table(result: dict, eid: str, variant: str | None = None, caption: str | None = None,
                label: str | None = None, decimals: int | None = None, source_sha: str = "") -> str:
    """A booktabs table of one estimand of estimate.py's output. Raises Refused."""
    d = next((x for x in result.get("estimates") or [] if isinstance(x, dict) and x.get("id") == eid), None)
    if d is None:
        raise Refused(f"no hay estimando {eid!r} en la salida de estimate.py")
    typ = d.get("type", "")
    name = _tex(eid)
    if variant is not None:
        v = next((x for x in d.get("variants") or [] if x.get("variant") == variant), None)
        if v is None:
            raise Refused(f"el estimando {eid} no tiene la variante {variant!r}")
        d, name = v, f"{name} [{_tex(variant)}]"
    head = [f"% estimate.py tabla-latex: plan {_tex(result.get('plan', ''))}, estimando {name}"
            + (f", JSON sha256 {source_sha}" if source_sha else "")
            + ("" if decimals is None else f", {decimals} decimales") + ". No edites las cifras a mano.",
            r"\begin{table}[ht]", r"\centering",
            rf"\caption{{{_tex(caption) if caption else f'Estimando {name} ({_tex(typ)})'}}}",
            rf"\label{{{label or 'tab:' + eid}}}"]
    if typ == "tabla":
        cl, rl = d.get("col_levels") or [], d.get("row_levels") or []
        cells = {(c["row"], c["col"]): c for c in d.get("cells") or []}
        body = [" & ".join([r"\textit{filas} / \textit{columnas}", *(_tex(c) for c in cl), "Total"]) + r" \\",
                r"\midrule"]
        for r, tot in zip(rl, d.get("row_totals") or []):
            parts = [_tex(r)]
            for c in cl:
                x = cells.get((r, c), {})
                parts.append(f"{_num_tex(x.get('count'), None)} ({_num_tex(x.get('p'), decimals)} {_ivl_tex(x, decimals)})")
            parts.append(_num_tex(tot, None))
            body.append(" & ".join(parts) + r" \\")
        body.append(" & ".join(["Total", *(_num_tex(t, None) for t in d.get("col_totals") or []),
                                _num_tex(d.get("n"), None)]) + r" \\")
        spec = "l" + "r" * (len(cl) + 1)
    elif typ == "cuantiles":
        body = [r"Cuantil & Valor & Intervalo \\", r"\midrule"]
        for q in d.get("quantiles") or []:
            body.append(f"q = {_num_tex(q['q'], None)} & {_num_tex(q.get('value'), decimals)} & {_ivl_tex(q, decimals)}"
                        + r" \\")
        body.append(f"$n$ & {_num_tex(d.get('n'), None)} & " + r"\\")
        spec = "lrr"
    else:
        rows = _scalar_rows(d, typ, name, decimals)
        for v in (d.get("variants") or []) if variant is None else []:
            rows += _scalar_rows(v, typ, f"{name} [{_tex(v['variant'])}]", decimals)
        body = [r"Estimando & Valor & Intervalo & $n$ & Método \\", r"\midrule"]
        body += [" & ".join(r) + r" \\" for r in rows]
        spec = "lrrrl"
    return "\n".join([*head, rf"\begin{{tabular}}{{{spec}}}", r"\toprule", *body, r"\bottomrule",
                      r"\end{tabular}", r"\end{table}"]) + "\n"


def _latex_main(argv: list[str]) -> int:
    lp = argparse.ArgumentParser(prog="estimate.py tabla-latex")
    lp.add_argument("--json", required=True, help="fichero con la salida de estimate.py")
    lp.add_argument("--id", required=True)
    lp.add_argument("--variant")
    lp.add_argument("--caption")
    lp.add_argument("--label")
    lp.add_argument("--decimals", type=int, help="decimales declarados; sin él, las cifras tal como se imprimieron")
    a = lp.parse_args(argv)
    try:
        if a.decimals is not None and not 0 <= a.decimals <= 12:
            raise Refused("--decimals entre 0 y 12")
        try:
            raw = Path(a.json).read_bytes()
            result = json.loads(raw.decode("utf-8-sig"))
        except (OSError, UnicodeDecodeError, ValueError):
            raise Refused("no se puede leer el JSON de estimate.py") from None
        if not isinstance(result, dict) or "error" in result:
            raise Refused("el fichero no es una salida correcta de estimate.py")
        sys.stdout.write(latex_table(result, a.id, a.variant, a.caption, a.label, a.decimals,
                                     hashlib.sha256(raw).hexdigest()))
        return 0
    except Refused as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 3


def _units_for(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in pairs:
        t, sep, ds = p.partition("=")
        if not sep or not t or not ds:
            raise Refused(f"--units-for {p!r}: se espera TABLA=DATASET")
        if t == DEFAULT_TABLE:
            raise Refused("la tabla por defecto va con --units, no con --units-for default=…")
        if not _TABLE.match(t):
            raise Refused(f"--units-for: {t!r} no es un nombre de tabla (letras, cifras, '-' o '_')")
        if t in out:
            raise Refused(f"--units-for: tabla {t!r} repetida")
        out[t] = ds
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    # Always UTF-8: the study gate re-runs this script with PYTHONIOENCODING=utf-8 and
    # compares byte for byte, so a Windows console's cp1252 would break any non-ASCII value.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if argv[:1] == ["tabla-latex"]:
        return _latex_main(argv[1:])
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
    ap.add_argument("--units", help="dataset de la tabla por defecto (estimandos sin 'units')")
    ap.add_argument("--units-for", action="append", default=[], metavar="TABLA=DATASET",
                    help="dataset de la tabla TABLA (estimandos con \"units\": \"TABLA\"); repetible")
    a = ap.parse_args(argv)
    if not a.units and not a.units_for:
        ap.error("hace falta --units o --units-for")
    try:
        print(json.dumps(run(Path(a.vault), a.plan, a.units, _units_for(a.units_for)),
                         ensure_ascii=False, indent=2))
        return 0
    except Refused as e:
        print(json.dumps({"error": str(e), **e.extra}, ensure_ascii=False))
        return 3


if __name__ == "__main__":
    sys.exit(main())
