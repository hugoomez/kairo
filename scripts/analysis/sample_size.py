#!/usr/bin/env python3
"""A-priori power analysis for a two-proportion design (`completo` tier).

Given a target effect size (Cohen's h -- the arcsine-transformed metric
for two proportions), alpha, and desired power, computes the required N per arm
via the standard normal-approximation formula. This is the mechanical tool
`preregister-experiment` calls for a `completo`-tier preregistration's
sample-size justification, so the required-N figure is derived the same way
every time -- never picked first and rationalized after. Note: `cohens_h(p1, p2)
here computes baseline→target (p2 - p1 direction); `two_proportion_test.py` uses
treatment−control convention (p1 - p2 direction), so the sign differs for the
same (p1, p2) pair, though `required_n()` uses |h| so this never affects N.

Standard library only (uses `statistics.NormalDist`, the same facility
`combine_effects.py` uses for its own CI z-values).

Method -- normal approximation (Cohen, 1988):
    h = 2*asin(sqrt(p2)) - 2*asin(sqrt(p1))          (if --p1/--p2 given)
    n_per_group = ceil( ((z_(alpha/2) + z_power) / h) ** 2 )

This is an approximation (not an exact noncentral-distribution solve) -- the
same kind of documented approximation `combine_effects.py` makes for its own
default method. It matches standard reference tables (e.g. Cohen 1988 Table
6.4.2: h=0.2, alpha=.05 two-sided, power=.80 -> n=197 per group).

Usage:
    python sample_size.py --p1 0.50 --p2 0.60 --alpha 0.05 --power 0.80 --json
    python sample_size.py --h 0.2 --alpha 0.05 --power 0.80 --json
    python sample_size.py --version

--p2 is the smallest treatment rate that would still count as meaningful
(baseline -> the smallest effect size of interest) -- not the effect you
expect to actually find.

Exit codes: 0 ok, 2 invalid input.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from statistics import NormalDist

__version__ = "1.1.0"   # 1.1.0: --d (Cohen's d) for a continuous metric, --paired
METHOD = "a-priori power analysis via Cohen's h (arcsine effect size, normal approximation)"

_N = NormalDist()


def cohens_h(p1: float, p2: float) -> float:
    """Arcsine-transformed effect size for two proportions (baseline→target).
    Both this and `two_proportion_test.py`'s `cohens_h` use arcsine transform,
    but with different argument-order conventions, so signs are opposite."""
    return 2.0 * math.asin(math.sqrt(p2)) - 2.0 * math.asin(math.sqrt(p1))


def required_n(h: float, alpha: float, power: float) -> dict:
    """Required N per arm for a two-sided test at the given alpha/power."""
    if h == 0.0:
        raise ValueError("effect size h must be non-zero (p1 and p2 cannot be equal)")
    if not (0.0 < alpha < 1.0):
        raise ValueError("alpha must be in (0, 1)")
    if not (0.0 < power < 1.0):
        raise ValueError("power must be in (0, 1)")

    z_alpha2 = _N.inv_cdf(1.0 - alpha / 2.0)
    z_power = _N.inv_cdf(power)
    n_per_group = math.ceil(((z_alpha2 + z_power) / abs(h)) ** 2)

    return {
        "h": h,
        "alpha": alpha,
        "power": power,
        "z_alpha2": z_alpha2,
        "z_power": z_power,
        "n_per_group": n_per_group,
        "n_total": 2 * n_per_group,
    }


def required_n_d(d: float, alpha: float, power: float, paired: bool = False) -> dict:
    """Required N for a difference of means (mean_difference.py): per group for
    Welch's test, or pairs (seeds) for a paired test on d_z. Normal approximation
    with Guenther's t correction (+ z²/4 per group, + z²/2 for pairs); it matches
    Cohen (1988) Table 2.4.1: d = 0.5, alpha .05 two-sided, power .80 -> 64."""
    if d == 0.0:
        raise ValueError("effect size d must be non-zero")
    if not (0.0 < alpha < 1.0):
        raise ValueError("alpha must be in (0, 1)")
    if not (0.0 < power < 1.0):
        raise ValueError("power must be in (0, 1)")
    za, zb = _N.inv_cdf(1.0 - alpha / 2.0), _N.inv_cdf(power)
    base = ((za + zb) / abs(d)) ** 2
    if paired:
        n = math.ceil(base + za * za / 2.0)
        return {"d": d, "alpha": alpha, "power": power, "z_alpha2": za, "z_power": zb, "paired": True,
                "n_pairs": n, "n_per_group": n, "n_total": n}
    n = math.ceil(2.0 * base + za * za / 4.0)
    return {"d": d, "alpha": alpha, "power": power, "z_alpha2": za, "z_power": zb, "paired": False,
            "n_per_group": n, "n_total": 2 * n}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="A-priori power analysis for a two-proportion design (completo tier).")
    parser.add_argument("--version", action="version",
                        version=f"sample_size.py {__version__} ({METHOD})")

    effect_group = parser.add_mutually_exclusive_group(required=True)
    effect_group.add_argument("--h", type=float,
                              help="target effect size directly, as Cohen's h")
    effect_group.add_argument("--p1", type=float,
                              help="baseline proportion (use with --p2)")
    effect_group.add_argument("--d", type=float,
                              help="a continuous metric's smallest effect of interest as Cohen's d "
                                   "(d_z with --paired): SESOI / the expected SD")
    parser.add_argument("--paired", action="store_true",
                        help="with --d: a paired design (same seeds in both arms); N is pairs")

    parser.add_argument("--p2", type=float,
                        help="smallest treatment proportion still meaningful "
                             "(the smallest effect size of interest, expressed "
                             "as baseline -> target); required with --p1")
    parser.add_argument("--alpha", type=float, required=True,
                        help="significance level, two-sided, e.g. 0.05")
    parser.add_argument("--power", type=float, required=True,
                        help="target power, e.g. 0.80")
    parser.add_argument("--json", action="store_true",
                        help="also print a machine-readable 'RESULT_JSON: {...}' line")
    args = parser.parse_args(argv)

    if args.p1 is not None and args.p2 is None:
        print("error: --p2 is required when --p1 is given", file=sys.stderr)
        return 2
    if args.p1 is not None and not (0.0 <= args.p1 <= 1.0 and 0.0 <= args.p2 <= 1.0):
        print("error: --p1 and --p2 must be in [0, 1]", file=sys.stderr)
        return 2

    if args.d is not None:
        try:
            result = required_n_d(args.d, args.alpha, args.power, args.paired)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"Cohen's {'d_z' if args.paired else 'd'}: {args.d:+.6f}")
        print(f"alpha (two-sided) = {args.alpha:g}, power = {args.power:g}")
        if args.paired:
            print(f"required pairs (seeds run in both arms): {result['n_pairs']}")
        else:
            print(f"required N per group: {result['n_per_group']}")
            print(f"required N total: {result['n_total']}")
        if args.json:
            print("RESULT_JSON: " + json.dumps(result, sort_keys=True))
        return 0

    h = args.h if args.h is not None else cohens_h(args.p1, args.p2)

    try:
        result = required_n(h, args.alpha, args.power)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.p1 is not None:
        print(f"p1 (baseline): {args.p1:.6f}")
        print(f"p2 (smallest meaningful): {args.p2:.6f}")
    print(f"Cohen's h: {result['h']:+.6f}")
    print(f"alpha (two-sided) = {args.alpha:g}  ->  z_alpha/2 = {result['z_alpha2']:.4f}")
    print(f"power = {args.power:g}  ->  z_power = {result['z_power']:.4f}")
    print(f"required N per group: {result['n_per_group']}")
    print(f"required N total: {result['n_total']}")

    if args.json:
        payload = dict(result)
        payload.update(p1=args.p1, p2=args.p2)
        print("RESULT_JSON: " + json.dumps(payload, sort_keys=True))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
