#!/usr/bin/env python3
"""Combine two independent effect-size estimates (random-effects by default).

Used by the `update-confidence` skill so replications are combined mechanically,
never by eyeballing. Reports the pooled effect with a confidence interval plus a
heterogeneity assessment that flags when two experiments disagree.

Standard library only.

Default method: RANDOM-EFFECTS, DerSimonian-Laird estimator of tau^2.
  w_i     = 1 / se_i**2                       (fixed-effect weights)
  e_fe    = sum(w_i * e_i) / sum(w_i)
  Q       = sum(w_i * (e_i - e_fe)**2)        (df = k - 1 = 1)
  C       = sum(w_i) - sum(w_i**2) / sum(w_i)
  tau2    = max(0, (Q - df) / C)              (between-study variance)
  w*_i    = 1 / (se_i**2 + tau2)
  e_re    = sum(w*_i * e_i) / sum(w*_i)
  se_re   = sqrt(1 / sum(w*_i))
  CI      = e_re +/- z(1 - alpha/2) * se_re
  Q p     = chi-square_1 survival = erfc(sqrt(Q / 2))
  I^2     = max(0, (Q - df) / Q)

Why random-effects as the default: with only two studies the true between-study
heterogeneity is unknown and cannot be estimated with any power. Random-effects
converges to the fixed-effect result under true homogeneity, so it is the safer
default and never the riskier one. Pass `--method fixed` for a
homogeneity-assumed inverse-variance sensitivity check (tau^2 forced to 0).

Consistency flag (feeds `update-confidence`'s verdict table):
  conflicting    point estimates have opposite signs AND both per-estimate CIs
                 exclude 0
  heterogeneous  Q p-value < 0.10  OR  I^2 > 0.50
  borderline     0.10 <= Q p-value < 0.20  OR  0.25 < I^2 <= 0.50
                 (and not conflicting / heterogeneous)
  consistent     none of the above

`borderline` is deliberately wide: Cochran's Q has very low power with k = 2, so
a non-significant Q is NOT evidence of consistency. `update-confidence` treats
`borderline` the same as `heterogeneous` (route to evidencia_mixta, or a human
decision to seek a third replication).

Usage:
    python combine_effects.py --e1 E --se1 SE --e2 E --se2 SE [--alpha 0.05]
    python combine_effects.py --e1 E --ci1 LO HI --e2 E --ci2 LO HI [--alpha 0.05]
    python combine_effects.py ... --method fixed        # sensitivity check
    python combine_effects.py --version

Give, for each estimate, either --seN or --ciN (se is derived from the CI width).
Exit codes: 0 ok, 2 invalid input.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from statistics import NormalDist

__version__ = "2.0.0"
METHOD_DEFAULT = "random-effects (DerSimonian-Laird)"
METHOD_FIXED = "fixed-effect inverse-variance"

# Consistency-band cut-points (documented above; do not loosen in the skill).
_Q_P_HETERO = 0.10
_Q_P_BORDERLINE = 0.20
_I2_HETERO = 0.50
_I2_BORDERLINE = 0.25

_N = NormalDist()


def _se_from_ci(lo: float, hi: float, alpha: float) -> float:
    if hi <= lo:
        raise ValueError("CI upper bound must exceed lower bound")
    zc = _N.inv_cdf(1.0 - alpha / 2.0)
    return (hi - lo) / (2.0 * zc)


def combine_effects(e1: float, se1: float, e2: float, se2: float,
                    alpha: float = 0.05, method: str = "random") -> dict:
    if se1 <= 0 or se2 <= 0:
        raise ValueError("standard errors must be positive")
    if not (0.0 < alpha < 1.0):
        raise ValueError("alpha must be in (0, 1)")
    if method not in ("random", "fixed"):
        raise ValueError("method must be 'random' or 'fixed'")

    zc = _N.inv_cdf(1.0 - alpha / 2.0)
    df = 1  # k - 1, always 2 studies here

    # Fixed-effect weights and Cochran's Q (needed for tau^2 and heterogeneity).
    w1, w2 = 1.0 / se1 ** 2, 1.0 / se2 ** 2
    sum_w = w1 + w2
    e_fe = (w1 * e1 + w2 * e2) / sum_w
    q = w1 * (e1 - e_fe) ** 2 + w2 * (e2 - e_fe) ** 2
    q_p = math.erfc(math.sqrt(q / 2.0)) if q > 0.0 else 1.0
    i2 = max(0.0, (q - df) / q) if q > 0.0 else 0.0

    # tau^2 (DerSimonian-Laird); zero under --method fixed.
    if method == "fixed":
        tau2 = 0.0
    else:
        c = sum_w - (w1 ** 2 + w2 ** 2) / sum_w
        tau2 = max(0.0, (q - df) / c) if c > 0.0 else 0.0

    ws1, ws2 = 1.0 / (se1 ** 2 + tau2), 1.0 / (se2 ** 2 + tau2)
    sum_ws = ws1 + ws2
    e_pooled = (ws1 * e1 + ws2 * e2) / sum_ws
    se_pooled = math.sqrt(1.0 / sum_ws)
    ci_pooled = (e_pooled - zc * se_pooled, e_pooled + zc * se_pooled)

    ci1 = (e1 - zc * se1, e1 + zc * se1)
    ci2 = (e2 - zc * se2, e2 + zc * se2)
    same_sign = (e1 >= 0.0) == (e2 >= 0.0)
    cis_overlap = not (ci1[1] < ci2[0] or ci2[1] < ci1[0])
    e1_excludes_null = ci1[0] > 0.0 or ci1[1] < 0.0
    e2_excludes_null = ci2[0] > 0.0 or ci2[1] < 0.0
    pooled_excludes_null = ci_pooled[0] > 0.0 or ci_pooled[1] < 0.0

    if (not same_sign) and e1_excludes_null and e2_excludes_null:
        consistency = "conflicting"
    elif q_p < _Q_P_HETERO or i2 > _I2_HETERO:
        consistency = "heterogeneous"
    elif q_p < _Q_P_BORDERLINE or i2 > _I2_BORDERLINE:
        consistency = "borderline"
    else:
        consistency = "consistent"

    return {
        "method": METHOD_DEFAULT if method == "random" else METHOD_FIXED,
        "script_version": __version__,
        "alpha": alpha,
        "e1": e1, "se1": se1, "ci1": ci1,
        "e2": e2, "se2": se2, "ci2": ci2,
        "e_pooled": e_pooled,
        "se_pooled": se_pooled,
        "ci_pooled": ci_pooled,
        "pooled_excludes_null": pooled_excludes_null,
        "tau_squared": tau2,
        "cochran_q": q,
        "q_p_value": q_p,
        "i_squared": i2,
        "same_sign": same_sign,
        "cis_overlap": cis_overlap,
        "consistency": consistency,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Combine two independent effect sizes (random-effects DL by default; "
                    "--method fixed for a homogeneity-assumed sensitivity check).",
        epilog="consistency bands: conflicting = opposite signs & both CIs exclude 0; "
               f"heterogeneous = Q p < {_Q_P_HETERO} or I^2 > {_I2_HETERO}; "
               f"borderline = Q p < {_Q_P_BORDERLINE} or I^2 > {_I2_BORDERLINE}; "
               "consistent otherwise. With k=2 Cochran's Q has low power, so a "
               "non-significant Q is not proof of consistency and 'borderline' is "
               "treated as disagreement by update-confidence.")
    parser.add_argument("--version", action="version",
                        version=f"combine_effects.py {__version__} ({METHOD_DEFAULT})")
    parser.add_argument("--e1", type=float, required=True, help="point estimate, experiment 1")
    parser.add_argument("--e2", type=float, required=True, help="point estimate, experiment 2")
    parser.add_argument("--se1", type=float, help="standard error, experiment 1")
    parser.add_argument("--se2", type=float, help="standard error, experiment 2")
    parser.add_argument("--ci1", type=float, nargs=2, metavar=("LO", "HI"),
                        help="confidence interval, experiment 1 (alternative to --se1)")
    parser.add_argument("--ci2", type=float, nargs=2, metavar=("LO", "HI"),
                        help="confidence interval, experiment 2 (alternative to --se2)")
    parser.add_argument("--alpha", type=float, default=0.05,
                        help="two-sided significance level for all intervals (default 0.05)")
    parser.add_argument("--method", choices=("random", "fixed"), default="random",
                        help="'random' (default, DerSimonian-Laird) or 'fixed' (IV, tau^2=0)")
    parser.add_argument("--labels", default="exp1,exp2",
                        help="comma-separated labels for the two experiments")
    parser.add_argument("--json", action="store_true",
                        help="also print a machine-readable 'RESULT_JSON: {...}' line")
    args = parser.parse_args(argv)

    try:
        if not (0.0 < args.alpha < 1.0):
            raise ValueError("alpha must be in (0, 1)")
        se1 = args.se1 if args.se1 is not None else (
            _se_from_ci(args.ci1[0], args.ci1[1], args.alpha) if args.ci1 else None)
        se2 = args.se2 if args.se2 is not None else (
            _se_from_ci(args.ci2[0], args.ci2[1], args.alpha) if args.ci2 else None)
        if se1 is None or se2 is None:
            raise ValueError("give --se1/--se2 or --ci1/--ci2 for each estimate")
        res = combine_effects(args.e1, se1, args.e2, se2, args.alpha, args.method)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    raw = args.labels.split(",", 1)
    l1 = raw[0].strip() or "exp1"
    l2 = (raw[1].strip() if len(raw) > 1 else "") or "exp2"

    print(f"method: {res['method']} (combine_effects.py {res['script_version']})")
    print(f"{l1}: effect {res['e1']:+.6f}  se {res['se1']:.6f}  "
          f"CI [{res['ci1'][0]:+.6f}, {res['ci1'][1]:+.6f}]")
    print(f"{l2}: effect {res['e2']:+.6f}  se {res['se2']:.6f}  "
          f"CI [{res['ci2'][0]:+.6f}, {res['ci2'][1]:+.6f}]")
    print(f"pooled: effect {res['e_pooled']:+.6f}  se {res['se_pooled']:.6f}  "
          f"CI [{res['ci_pooled'][0]:+.6f}, {res['ci_pooled'][1]:+.6f}]  "
          f"(excludes 0: {res['pooled_excludes_null']})")
    print(f"tau^2 = {res['tau_squared']:.6f}")
    print(f"heterogeneity: Q = {res['cochran_q']:.4f}  p = {res['q_p_value']:.4g}  "
          f"I^2 = {res['i_squared']:.3f}")
    print(f"same sign: {res['same_sign']}   per-estimate CIs overlap: {res['cis_overlap']}")
    print(f"consistency: {res['consistency']}")

    if args.json:
        print("RESULT_JSON: " + json.dumps(res, sort_keys=True))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
