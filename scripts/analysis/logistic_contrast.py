#!/usr/bin/env python3
"""Contrast of two logistic-regression effects (mechanical experiment analysis).

Some preregistered claims are not "arm A beats arm B" but "changing factor A
moves the success log-odds more than changing factor B" — e.g. whether the
chance an optimiser finds a feasible solution depends more on the degree than
on the size of the instance. `two_proportion_test.py` cannot express that.
This script fits one logistic regression of a 0/1 outcome on the declared
predictors and compares two pre-declared moves of two predictors, on the
log-odds scale, with a bootstrap interval — a frozen rule, never free-text
reasoning.

Standard library only (Newton / IRLS with a small Gaussian elimination), so it
runs wherever the experiment runs.

Usage:
    python logistic_contrast.py --data runs.csv --outcome success \\
        --predictors n,dmax,margin,occupancy \\
        --a dmax:4:12 --b n:15:50 --alpha 0.05 \\
        [--direction-a decrease|increase] [--min-effect X] [--floor-effect Y] \\
        [--strata cell] [--ridge 0.01] [--n-boot 2000] [--seed 20261017] [--json]

Data: a CSV with a header row. `--outcome` is a column of 0/1; every predictor
is a numeric column. Each row is one instance (one run).

Model: logit P(y = 1) = b0 + sum_j b_j * z_j, with z_j the predictors centred
and scaled to unit sd. A weak ridge penalty (--ridge, default 0.01, on the
standardised slopes, not on the intercept) keeps every coefficient finite when
a cell is all successes or all failures (complete separation); set --ridge 0
to turn it off. The reported moves are in the predictors' own units.

Contrast:
    move_a = b_a * (to_a - from_a)       change in log-odds of the move --a
    move_b = b_b * (to_b - from_b)       change in log-odds of the move --b
    D      = |move_a| - |move_b|         D > 0: move a matters more than move b

Interval: percentile bootstrap of D (--n-boot resamples of the rows, within
--strata when given, one fixed --seed), at level 1 - alpha.

Verdict (with --min-effect; --direction-a optionally requires the sign of
move_a), the same three-way rule as two_proportion_test.py, on D:
    apoya       CI excludes 0, D > 0, D >= min-effect (and sign of move_a ok)
    refuta      CI contains 0, OR D <= 0 (or sign of move_a wrong), OR D <= floor-effect
    inconcluso  anything else
Without --min-effect only significativo / no_significativo is printed: do NOT
infer support.

The JSON line carries `effect` (= D) and `se` (bootstrap sd of D).

Exit codes: 0 ok, 2 invalid input or a base fit that does not converge.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import sys
from pathlib import Path

__version__ = "1.0.0"


# --------------------------------------------------------------------------
# Penalised logistic regression (Newton with step halving)
# --------------------------------------------------------------------------

def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Solve a x = b by Gaussian elimination with partial pivoting."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[piv][c]) < 1e-14:
            raise ValueError("singular design (a predictor is constant or collinear)")
        m[c], m[piv] = m[piv], m[c]
        for r in range(c + 1, n):
            f = m[r][c] / m[c][c]
            if f:
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][k] * x[k] for k in range(r + 1, n))) / m[r][r]
    return x


def _sigmoid(t: float) -> float:
    if t >= 0:
        return 1.0 / (1.0 + math.exp(-t))
    e = math.exp(t)
    return e / (1.0 + e)


def _penalised_loglik(x: list[list[float]], y: list[int], beta: list[float], ridge: float) -> float:
    ll = 0.0
    for xi, yi in zip(x, y):
        t = sum(b * v for b, v in zip(beta, xi))
        ll += (t if yi else 0.0) - (t + math.log1p(math.exp(-t)) if t > 0 else math.log1p(math.exp(t)))
    return ll - 0.5 * ridge * sum(b * b for b in beta[1:])


def fit_logistic(x: list[list[float]], y: list[int], ridge: float,
                 start: list[float] | None = None, max_iter: int = 60) -> tuple[list[float], bool]:
    """Maximise the ridge-penalised log-likelihood. Returns (beta, converged)."""
    p = len(x[0])
    beta = list(start) if start is not None else [0.0] * p
    ll = _penalised_loglik(x, y, beta, ridge)
    for _ in range(max_iter):
        grad = [0.0] * p
        hess = [[0.0] * p for _ in range(p)]
        for xi, yi in zip(x, y):
            mu = _sigmoid(sum(b * v for b, v in zip(beta, xi)))
            w = mu * (1.0 - mu)
            r = yi - mu
            for j in range(p):
                grad[j] += r * xi[j]
                xw = w * xi[j]
                row = hess[j]
                for k in range(j, p):
                    row[k] += xw * xi[k]
        for j in range(p):
            for k in range(j):
                hess[j][k] = hess[k][j]
        for j in range(1, p):
            grad[j] -= ridge * beta[j]
            hess[j][j] += ridge
        hess[0][0] += 1e-10
        step = _solve(hess, grad)
        t = 1.0
        for _ in range(30):
            cand = [b + t * s for b, s in zip(beta, step)]
            new_ll = _penalised_loglik(x, y, cand, ridge)
            if new_ll >= ll - 1e-12:
                break
            t /= 2.0
        else:
            return beta, False
        shift = max(abs(c - b) for c, b in zip(cand, beta))
        beta, ll = cand, new_ll
        if shift < 1e-9:
            return beta, True
    return beta, False


# --------------------------------------------------------------------------
# The contrast
# --------------------------------------------------------------------------

def _standardise(rows: list[list[float]]) -> tuple[list[list[float]], list[float], list[float]]:
    p = len(rows[0])
    means = [statistics.fmean(r[j] for r in rows) for j in range(p)]
    sds = [statistics.pstdev([r[j] for r in rows]) for j in range(p)]
    if any(s == 0.0 for s in sds):
        raise ValueError("a predictor is constant in the data")
    return [[1.0] + [(r[j] - means[j]) / sds[j] for j in range(p)] for r in rows], means, sds


def _moves(beta: list[float], sds: list[float], ia: int, ib: int,
           move_a: tuple[float, float], move_b: tuple[float, float]) -> tuple[float, float]:
    raw_a = beta[1 + ia] / sds[ia]
    raw_b = beta[1 + ib] / sds[ib]
    return raw_a * (move_a[1] - move_a[0]), raw_b * (move_b[1] - move_b[0])


def contrast(rows: list[list[float]], y: list[int], names: list[str], a: tuple[str, float, float],
             b: tuple[str, float, float], alpha: float, ridge: float = 0.01, n_boot: int = 2000,
             seed: int = 0, strata: list[str] | None = None) -> dict:
    if not (0.0 < alpha < 1.0):
        raise ValueError("alpha must be in (0, 1)")
    if ridge < 0 or n_boot < 100:
        raise ValueError("--ridge must be >= 0 and --n-boot >= 100")
    if len(rows) != len(y) or len(rows) < 10:
        raise ValueError("need at least 10 rows")
    if set(y) - {0, 1}:
        raise ValueError("the outcome column must hold only 0 and 1")
    if len(set(y)) < 2:
        raise ValueError("the outcome is constant (all 0 or all 1): nothing to fit")
    for nm, _, _ in (a, b):
        if nm not in names:
            raise ValueError(f"--a/--b names a predictor not in --predictors: {nm}")
    if a[0] == b[0]:
        raise ValueError("--a and --b must move two different predictors")
    if len(rows) <= len(names) + 1:
        raise ValueError("fewer rows than coefficients")
    ia, ib = names.index(a[0]), names.index(b[0])
    ma, mb = (a[1], a[2]), (b[1], b[2])
    x, _, sds = _standardise(rows)
    beta, ok = fit_logistic(x, y, ridge)
    if not ok:
        raise ValueError("the base logistic fit did not converge")
    move_a, move_b = _moves(beta, sds, ia, ib, ma, mb)
    d_hat = abs(move_a) - abs(move_b)

    groups: dict[str, list[int]] = {}
    for i in range(len(rows)):
        groups.setdefault(strata[i] if strata else "", []).append(i)
    rng = random.Random(seed)
    boots: list[float] = []
    failed = 0
    for _ in range(n_boot):
        idx: list[int] = []
        for members in groups.values():
            idx.extend(rng.choice(members) for _ in members)
        yb = [y[i] for i in idx]
        if len(set(yb)) < 2:
            failed += 1
            continue
        xb = [x[i] for i in idx]
        try:
            bb, okb = fit_logistic(xb, yb, ridge, start=beta)
        except ValueError:
            failed += 1
            continue
        if not okb:
            failed += 1
            continue
        ba, bbv = _moves(bb, sds, ia, ib, ma, mb)
        boots.append(abs(ba) - abs(bbv))
    if len(boots) < 0.95 * n_boot:
        raise ValueError(f"{failed} of {n_boot} bootstrap fits failed: the interval would be unreliable")
    boots.sort()
    lo = boots[max(0, int(math.floor((alpha / 2.0) * len(boots))))]
    hi = boots[min(len(boots) - 1, int(math.ceil((1.0 - alpha / 2.0) * len(boots))) - 1)]
    return {"method": "logistic regression, contrast of two moves (bootstrap percentile)",
            "predictors": names, "coefficients_standardised": dict(zip(["intercept"] + names, beta)),
            "move_a": move_a, "move_b": move_b, "effect": d_hat, "se": statistics.stdev(boots),
            "ci": [lo, hi], "ci_level": 1.0 - alpha, "alpha": alpha,
            "significant": lo > 0.0 or hi < 0.0, "a": list(a), "b": list(b),
            "n": len(rows), "n_boot": n_boot, "boot_failed": failed, "seed": seed, "ridge": ridge,
            "strata": bool(strata)}


def classify(stats: dict, direction_a: str | None, min_effect: float | None, floor_effect: float) -> str:
    """The same three-way rule as two_proportion_test.classify, on D = |move_a| - |move_b|."""
    if min_effect is None:
        return "significativo" if stats["significant"] else "no_significativo"
    d = stats["effect"]
    sign_ok = True
    if direction_a is not None:
        sign_ok = stats["move_a"] < 0 if direction_a == "decrease" else stats["move_a"] > 0
    correct = d > 0.0 and sign_ok
    if stats["significant"] and correct and d >= min_effect:
        return "apoya"
    if (not stats["significant"]) or (not correct) or d <= floor_effect:
        return "refuta"
    return "inconcluso"


def _move(text: str, flag: str) -> tuple[str, float, float]:
    parts = text.split(":")
    if len(parts) != 3 or not parts[0]:
        raise ValueError(f"{flag}: expected NAME:FROM:TO, got {text!r}")
    try:
        lo, hi = float(parts[1]), float(parts[2])
    except ValueError:
        raise ValueError(f"{flag}: FROM and TO must be numbers, got {text!r}") from None
    if lo == hi:
        raise ValueError(f"{flag}: FROM and TO must differ")
    return parts[0], lo, hi


def _load(path: Path, outcome: str, predictors: list[str], strata: str | None):
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        for col in [outcome, *predictors] + ([strata] if strata else []):
            if col not in header:
                raise ValueError(f"column not in the CSV: {col}")
        rows, y, st = [], [], []
        for k, rec in enumerate(reader, start=2):
            try:
                rows.append([float(rec[c]) for c in predictors])
                y.append(int(float(rec[outcome])))
            except (TypeError, ValueError):
                raise ValueError(f"line {k}: a predictor or the outcome is not a number") from None
            if strata:
                st.append(rec[strata])
    return rows, y, (st or None)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Contrast of two logistic-regression moves (mechanical analysis).")
    ap.add_argument("--version", action="version", version=f"logistic_contrast.py {__version__}")
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--outcome", required=True)
    ap.add_argument("--predictors", required=True, help="comma-separated numeric columns")
    ap.add_argument("--a", required=True, help="NAME:FROM:TO, the move whose effect the claim says is larger")
    ap.add_argument("--b", required=True, help="NAME:FROM:TO, the move it is compared with")
    ap.add_argument("--alpha", type=float, required=True)
    ap.add_argument("--direction-a", choices=("increase", "decrease"), dest="direction_a")
    ap.add_argument("--min-effect", type=float, dest="min_effect", help="T_apoyo: D in log-odds")
    ap.add_argument("--floor-effect", type=float, dest="floor_effect", default=0.0, help="T_refuta: D in log-odds")
    ap.add_argument("--strata", help="column to resample within (e.g. the design cell)")
    ap.add_argument("--ridge", type=float, default=0.01)
    ap.add_argument("--n-boot", type=int, dest="n_boot", default=2000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        names = [c.strip() for c in a.predictors.split(",") if c.strip()]
        if len(set(names)) != len(names) or not names:
            raise ValueError("--predictors must be distinct column names")
        rows, y, st = _load(a.data, a.outcome, names, a.strata)
        stats = contrast(rows, y, names, _move(a.a, "--a"), _move(a.b, "--b"), a.alpha,
                         ridge=a.ridge, n_boot=a.n_boot, seed=a.seed, strata=st)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    verdict = classify(stats, a.direction_a, a.min_effect, a.floor_effect)
    print(f"{stats['method']}: n = {stats['n']}, {stats['n_boot']} resamples "
          f"({stats['boot_failed']} failed), seed = {stats['seed']}, ridge = {stats['ridge']:g}")
    print(f"move a ({a.a}): {stats['move_a']:+.4f} log-odds    move b ({a.b}): {stats['move_b']:+.4f} log-odds")
    print(f"D = |a| - |b| = {stats['effect']:+.4f}   {100 * stats['ci_level']:.0f}% CI "
          f"[{stats['ci'][0]:.4f}, {stats['ci'][1]:.4f}]   se(boot) = {stats['se']:.4f}")
    if a.min_effect is None:
        print(f"verdict: {verdict}  (statistical only -- no --min-effect -- do NOT infer support)")
    else:
        print(f"verdict: {verdict}  (direction-a={a.direction_a}, min-effect={a.min_effect:g}, "
              f"floor-effect={a.floor_effect:g})")
    if a.json:
        payload = dict(stats, verdict=verdict, direction_a=a.direction_a, min_effect=a.min_effect,
                       floor_effect=a.floor_effect, version=__version__)
        print("RESULT_JSON: " + json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
