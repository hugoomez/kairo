#!/usr/bin/env python3
"""Difference of means for a continuous metric (mechanical experiment analysis).

The two-proportion test fits success / failure outcomes. Most ML and HPC
results are continuous and come from a few seeds or repetitions: accuracy or
loss per seed, throughput, time to solution, speedup. This script applies a
frozen preregistration rule to such measurements the same way
`two_proportion_test.py` does to counts — never by free-text reasoning.

Standard library only (the t distribution is computed here through the
regularized incomplete beta function), so it runs wherever the experiment runs.

Usage:
    python mean_difference.py --treatment 0.91,0.93,0.92 --control 0.88,0.90,0.89 --alpha 0.05 \\
        [--paired] [--log] [--direction increase|decrease] [--min-effect X] [--floor-effect Y] [--json]
    (--treatment-file / --control-file: one value per line, instead of the lists)

Tests:
    default   Welch's t-test (unequal variances; Welch–Satterthwaite df)
    --paired  paired t-test on per-seed differences (same seeds, same order)
    --log     the analysis runs on log(values): the effect is a ratio
              (speedup, throughput, time) — `--min-effect 1.10` means "at least
              10 % higher"; thresholds are given as ratios, reported as ratios.

Verdict (with --direction AND --min-effect), the same rule as
two_proportion_test.py:
    apoya       significant (p < alpha), predicted direction, |effect| >= min-effect
    refuta      not significant, OR wrong direction, OR |effect| <= floor-effect
    inconcluso  anything else
Without them only significativo / no_significativo is printed: do NOT infer support.

The JSON line carries `effect` and `se` on the analysis scale, the input
`combine_effects.py --e1 … --se1 …` takes to pool two replications.

Exit codes: 0 ok, 2 invalid input.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

__version__ = "1.0.0"


# --------------------------------------------------------------------------
# Student's t distribution (standard library only)
# --------------------------------------------------------------------------

def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (Lentz)."""
    tiny, eps = 1e-300, 3e-16
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_two_sided_p(t: float, df: float) -> float:
    return betainc(df / 2.0, 0.5, df / (df + t * t))


def t_cdf(t: float, df: float) -> float:
    tail = 0.5 * t_two_sided_p(t, df)
    return 1.0 - tail if t >= 0 else tail


def t_quantile(p: float, df: float) -> float:
    """Inverse of t_cdf by bisection (monotone; plenty precise for an interval)."""
    lo, hi = -1e4, 1e4
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# --------------------------------------------------------------------------
# The test
# --------------------------------------------------------------------------

def mean_difference(treatment: list[float], control: list[float], alpha: float,
                    paired: bool = False, log: bool = False) -> dict:
    if not (0.0 < alpha < 1.0):
        raise ValueError("alpha must be in (0, 1)")
    if log and any(v <= 0 for v in treatment + control):
        raise ValueError("--log needs positive values (a ratio of times, throughputs, speedups)")
    tr = [math.log(v) for v in treatment] if log else list(treatment)
    co = [math.log(v) for v in control] if log else list(control)
    if paired:
        if len(tr) != len(co):
            raise ValueError("--paired needs the same number of treatment and control values (same seeds)")
        if len(tr) < 2:
            raise ValueError("--paired needs at least 2 pairs")
        diffs = [a - b for a, b in zip(tr, co)]
        effect = statistics.fmean(diffs)
        sd = statistics.stdev(diffs)
        se = sd / math.sqrt(len(diffs))
        df = float(len(diffs) - 1)
        std_effect = effect / sd if sd > 0 else math.inf if effect else 0.0       # Cohen's d_z
        method = "paired t-test"
    else:
        if len(tr) < 2 or len(co) < 2:
            raise ValueError("Welch's test needs at least 2 values per group")
        m1, m2 = statistics.fmean(tr), statistics.fmean(co)
        v1, v2 = statistics.variance(tr), statistics.variance(co)
        n1, n2 = len(tr), len(co)
        effect = m1 - m2
        a, b = v1 / n1, v2 / n2
        se = math.sqrt(a + b)
        df = (a + b) ** 2 / ((a * a) / (n1 - 1) + (b * b) / (n2 - 1)) if se > 0 else float(n1 + n2 - 2)
        pooled = math.sqrt(((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2))
        j = 1.0 - 3.0 / (4.0 * (n1 + n2) - 9.0)                                     # Hedges' correction
        std_effect = j * effect / pooled if pooled > 0 else math.inf if effect else 0.0   # Hedges' g
        method = "Welch's t-test"
    if se == 0.0:
        t, p = (0.0, 1.0) if effect == 0.0 else (math.copysign(math.inf, effect), 0.0)
        lo = hi = effect
    else:
        t = effect / se
        p = t_two_sided_p(t, df)
        q = t_quantile(1.0 - alpha / 2.0, df)
        lo, hi = effect - q * se, effect + q * se
    out = {"method": method + (" on log values" if log else ""), "effect": effect, "se": se, "df": df,
           "t": t, "p_value": p, "alpha": alpha, "significant": p < alpha,
           "ci": [lo, hi], "ci_level": 1.0 - alpha, "standardized_effect": std_effect,
           "n_treatment": len(treatment), "n_control": len(control), "paired": paired, "log": log}
    if log:
        out["ratio"] = math.exp(effect)
        out["ratio_ci"] = [math.exp(lo), math.exp(hi)]
    return out


def classify(stats: dict, direction: str | None, min_effect: float | None, floor_effect: float) -> str:
    """The same three-way rule as two_proportion_test.classify, on the analysis scale."""
    if direction is None or min_effect is None:
        return "significativo" if stats["significant"] else "no_significativo"
    eff = stats["effect"]
    sign = 1.0 if direction == "increase" else -1.0
    correct = eff * sign > 0.0
    mag = abs(eff)
    if stats["significant"] and correct and mag >= min_effect:
        return "apoya"
    if (not stats["significant"]) or (not correct) or mag <= floor_effect:
        return "refuta"
    return "inconcluso"


def _values(listed: str | None, path: Path | None, name: str) -> list[float]:
    if (listed is None) == (path is None):
        raise ValueError(f"give exactly one of --{name} or --{name}-file")
    raw = listed.split(",") if listed is not None else path.read_text(encoding="utf-8").split()
    try:
        return [float(x) for x in (r.strip() for r in raw) if x]
    except ValueError as e:
        raise ValueError(f"--{name}: not a number ({e})") from None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Difference of means for a continuous metric (mechanical analysis).")
    ap.add_argument("--version", action="version", version=f"mean_difference.py {__version__}")
    ap.add_argument("--treatment")
    ap.add_argument("--treatment-file", type=Path)
    ap.add_argument("--control")
    ap.add_argument("--control-file", type=Path)
    ap.add_argument("--alpha", type=float, required=True)
    ap.add_argument("--paired", action="store_true", help="paired t-test on per-seed differences")
    ap.add_argument("--log", action="store_true", help="analyse log(values): the effect is a ratio")
    ap.add_argument("--direction", choices=("increase", "decrease"))
    ap.add_argument("--min-effect", type=float, dest="min_effect",
                    help="T_apoyo: a difference in the metric's units, or a ratio (> 1) with --log")
    ap.add_argument("--floor-effect", type=float, dest="floor_effect", default=None,
                    help="T_refuta: same units as --min-effect (default 0, or ratio 1 with --log)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        tr = _values(a.treatment, a.treatment_file, "treatment")
        co = _values(a.control, a.control_file, "control")
        stats = mean_difference(tr, co, a.alpha, paired=a.paired, log=a.log)
        min_e, floor_e = a.min_effect, a.floor_effect
        if a.log:
            if (min_e is not None and min_e <= 0) or (floor_e is not None and floor_e <= 0):
                raise ValueError("with --log, thresholds are ratios (> 0), e.g. --min-effect 1.10")
            min_e = abs(math.log(min_e)) if min_e is not None else None
            floor_e = abs(math.log(floor_e)) if floor_e is not None else 0.0
        else:
            floor_e = floor_e if floor_e is not None else 0.0
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    verdict = classify(stats, a.direction, min_e, floor_e)
    unit = "ratio" if a.log else "difference"
    shown = stats["ratio"] if a.log else stats["effect"]
    ci = stats["ratio_ci"] if a.log else stats["ci"]
    print(f"{stats['method']}: n = {stats['n_treatment']} vs {stats['n_control']}")
    print(f"effect ({unit}, treatment vs control): {shown:+.6g}   "
          f"{100 * stats['ci_level']:.0f}% CI [{ci[0]:.6g}, {ci[1]:.6g}]")
    print(f"t = {stats['t']:.4f}, df = {stats['df']:.3f}, p (two-sided) = {stats['p_value']:.6g}")
    print(f"standardized effect ({'d_z' if a.paired else 'Hedges g'}): {stats['standardized_effect']:+.4f}")
    if a.direction is None or a.min_effect is None:
        print(f"verdict: {verdict}  (statistical only -- no --direction/--min-effect -- do NOT infer support)")
    else:
        print(f"verdict: {verdict}  (direction={a.direction}, min-effect={a.min_effect:g}, "
              f"floor-effect={a.floor_effect if a.floor_effect is not None else (1 if a.log else 0):g})")
    if a.json:
        payload = dict(stats, verdict=verdict, direction=a.direction, min_effect=a.min_effect,
                       floor_effect=a.floor_effect, version=__version__)
        print("RESULT_JSON: " + json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
