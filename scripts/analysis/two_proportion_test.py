#!/usr/bin/env python3
"""Two-proportion z-test for mechanical experiment analysis.

Given two groups (successes / totals) and a significance level, print the effect
size, the two-sided p-value, and a plain verdict string. This is the tool that
`run-experiment` step 5 calls so the frozen analysis plan is applied
mechanically -- never by free-text reasoning about statistics.

Standard library only (no numpy / scipy) so it runs in whatever environment the
experiment code runs in.

Usage:
    python two_proportion_test.py X1 N1 X2 N2 --alpha 0.05 \\
        [--direction increase|decrease] [--min-effect 0.05] \\
        [--floor-effect 0.0] [--labels treatment,control] [--json]

Positional arguments:
    X1 N1   successes and total for group 1 (treatment / predicted-higher group)
    X2 N2   successes and total for group 2 (control / baseline group)

Verdict:
    With --direction AND --min-effect, a three-way verdict matching the
    preregistration decision rule is produced:
        apoya       significant (p < alpha) in the predicted direction and
                    |risk difference| >= min-effect
        refuta      not significant, OR significant in the wrong direction,
                    OR |risk difference| <= floor-effect
        inconcluso  anything else (e.g. significant and in the predicted
                    direction, but the effect is smaller than min-effect)
    Without those flags only a two-way statistical verdict is printed
    (significativo / no_significativo); the caller must NOT infer support.

Exit codes: 0 ok, 2 invalid input.
"""

from __future__ import annotations

import argparse
import json
import math
import sys

__version__ = "1.0.0"
METHOD = "pooled two-proportion z-test"


def two_proportion_test(x1: int, n1: int, x2: int, n2: int, alpha: float) -> dict:
    """Pooled two-proportion z-test. Returns a dict of statistics."""
    if n1 <= 0 or n2 <= 0:
        raise ValueError("totals N1 and N2 must be positive")
    if not (0 <= x1 <= n1) or not (0 <= x2 <= n2):
        raise ValueError("successes must satisfy 0 <= X <= N for each group")
    if not (0.0 < alpha < 1.0):
        raise ValueError("alpha must be in (0, 1)")

    p1 = x1 / n1
    p2 = x2 / n2
    risk_diff = p1 - p2

    p_pool = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p_pool * (1.0 - p_pool) * (1.0 / n1 + 1.0 / n2))
    if se == 0.0:
        # both groups all-success or all-failure: no detectable difference
        z = 0.0
        p_value = 1.0
    else:
        z = risk_diff / se
        # two-sided p-value: erfc(|z| / sqrt(2)) == 2 * (1 - Phi(|z|))
        p_value = math.erfc(abs(z) / math.sqrt(2.0))

    # Cohen's h -- standardized effect size for a difference of proportions
    cohens_h = 2.0 * math.asin(math.sqrt(p1)) - 2.0 * math.asin(math.sqrt(p2))

    return {
        "p1": p1,
        "p2": p2,
        "risk_difference": risk_diff,
        "cohens_h": cohens_h,
        "z": z,
        "p_value": p_value,
        "alpha": alpha,
        "significant": p_value < alpha,
    }


def classify(stats: dict, direction: str | None, min_effect: float | None,
             floor_effect: float) -> str:
    """Map statistics to a verdict string."""
    if direction is None or min_effect is None:
        return "significativo" if stats["significant"] else "no_significativo"

    eff = stats["risk_difference"]
    predicted_sign = 1.0 if direction == "increase" else -1.0
    correct_direction = (eff * predicted_sign) > 0.0
    mag = abs(eff)

    if stats["significant"] and correct_direction and mag >= min_effect:
        return "apoya"
    if (not stats["significant"]) or (not correct_direction) or (mag <= floor_effect):
        return "refuta"
    return "inconcluso"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Two-proportion z-test (mechanical experiment analysis).")
    parser.add_argument("--version", action="version",
                        version=f"two_proportion_test.py {__version__} ({METHOD})")
    parser.add_argument("x1", type=int, help="successes, group 1 (treatment)")
    parser.add_argument("n1", type=int, help="total, group 1")
    parser.add_argument("x2", type=int, help="successes, group 2 (control)")
    parser.add_argument("n2", type=int, help="total, group 2")
    parser.add_argument("--alpha", type=float, required=True,
                        help="significance level, e.g. 0.05")
    parser.add_argument("--direction", choices=("increase", "decrease"),
                        help="predicted direction of group 1 relative to group 2")
    parser.add_argument("--min-effect", type=float, dest="min_effect",
                        help="T_apoyo: minimum meaningful |risk difference| for 'apoya'")
    parser.add_argument("--floor-effect", type=float, dest="floor_effect", default=0.0,
                        help="T_refuta: |risk difference| at or below this is 'refuta' "
                             "(default 0.0)")
    parser.add_argument("--labels", default="treatment,control",
                        help="comma-separated labels for the two groups")
    parser.add_argument("--json", action="store_true",
                        help="also print a machine-readable 'RESULT_JSON: {...}' line")
    args = parser.parse_args(argv)

    try:
        stats = two_proportion_test(args.x1, args.n1, args.x2, args.n2, args.alpha)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    verdict = classify(stats, args.direction, args.min_effect, args.floor_effect)
    raw = args.labels.split(",", 1)
    l1 = raw[0].strip() or "treatment"
    l2 = (raw[1].strip() if len(raw) > 1 else "") or "control"

    print(f"{l1}: {args.x1}/{args.n1} = {stats['p1']:.6f}")
    print(f"{l2}: {args.x2}/{args.n2} = {stats['p2']:.6f}")
    print(f"risk difference ({l1} - {l2}): {stats['risk_difference']:+.6f}")
    print(f"Cohen's h: {stats['cohens_h']:+.6f}")
    print(f"z = {stats['z']:.4f}")
    print(f"p-value (two-sided): {stats['p_value']:.6g}")
    print(f"alpha = {args.alpha:g}  ->  "
          f"{'significant' if stats['significant'] else 'not significant'}")
    if args.direction is None or args.min_effect is None:
        print(f"verdict: {verdict}  "
              f"(statistical only -- no --direction/--min-effect -- do NOT infer support)")
    else:
        print(f"verdict: {verdict}  "
              f"(direction={args.direction}, min-effect={args.min_effect:g}, "
              f"floor-effect={args.floor_effect:g})")

    if args.json:
        payload = dict(stats)
        payload.update(verdict=verdict, direction=args.direction,
                       min_effect=args.min_effect, floor_effect=args.floor_effect,
                       x1=args.x1, n1=args.n1, x2=args.x2, n2=args.n2)
        print("RESULT_JSON: " + json.dumps(payload, sort_keys=True))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
