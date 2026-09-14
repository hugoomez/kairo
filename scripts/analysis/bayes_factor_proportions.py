#!/usr/bin/env python3
"""Bayes factor for a two-proportion comparison (Gunel-Dickey, 1974).

Computes BF10 = P(data | rates independent) / P(data | rates equal), the
Bayesian analog `run-experiment` calls when a preregistration froze
`analysis_plan: bayesian`, mirroring the input shape of
`two_proportion_test.py` (X1 N1 X2 N2) so the two scripts are interchangeable
at the analysis-plan dispatch point.

Standard library only (uses `math.lgamma` for the closed-form Beta-Binomial
marginal likelihood -- no numerical integration needed).

Method -- Gunel & Dickey (1974) independent-binomials Bayes factor:
    H1: p1 != p2 (independent rates, each Beta(a, b) prior)
    H0: p1 == p2 (one shared rate, Beta(a, b) prior, fit on pooled data)

    log m(H1) = log B(x1+a, n1-x1+b) + log B(x2+a, n2-x2+b)
    log m(H0) = log B(x1+x2+a, n1+n2-x1-x2+b)
    BF10 = exp(log m(H1) - log m(H0))

where B(a, b) is the Beta function. The binomial coefficients C(n1,x1)*C(n2,x2)
cancel exactly between H1 and H0 and are omitted from the computation for
simplicity. The prior normalizer does NOT fully cancel: H1 places two
independent Beta(a, b) priors (one per group), contributing 1/B(a,b)^2, while
H0 places one Beta(a, b) prior, contributing 1/B(a,b) -- so one factor of
1/B(a,b) survives in the BF10 ratio and must be included explicitly.

Default prior: Beta(1, 1) -- uniform, the standard default for a two-proportion
Bayes factor. Override with --prior-a/--prior-b to encode a more informative
prior; state the choice and its justification in the frozen preregistration.

Verdict thresholds (Kass & Raftery, 1995): the default --bf-threshold of 3.0
is "substantial evidence" on their scale (3-10 substantial, 10-30 strong,
30-100 very strong, >100 decisive) -- researcher-overridable at preregistration
time, the same way --alpha is for the frequentist script.

Usage:
    python bayes_factor_proportions.py X1 N1 X2 N2 --json
    python bayes_factor_proportions.py X1 N1 X2 N2 \\
        --direction increase --bf-threshold 3.0 --json
    python bayes_factor_proportions.py --version

Exit codes: 0 ok, 2 invalid input.
"""

from __future__ import annotations

import argparse
import json
import math
import sys

__version__ = "1.0.0"
METHOD = "Gunel-Dickey independent-binomials Bayes factor, Beta(a, b) prior"


def log_beta_binom(x: int, n: int, a: float, b: float) -> float:
    """Log of the Beta-function kernel B(x+a, n-x+b) used to build the
    Gunel-Dickey Bayes factor in bayes_factor() below.

    This is deliberately NOT a normalized Beta-Binomial log-pmf: the
    binomial coefficient C(n,x) is omitted because it appears identically in
    both H1's and H0's marginal likelihood and cancels exactly in the BF10
    ratio below -- including it and letting it cancel, or omitting it here,
    are mathematically equivalent, and omitting it is simpler and avoids a
    mismatched combinatorial factor bug (using n1+n2 choose x1+x2 for the
    null model is WRONG -- it should be n1 choose x1 times n2 choose x2, and
    this kernel-only form sidesteps needing either term). Unlike the
    binomial coefficient, the B(a,b) prior normalizer does NOT cancel
    exactly -- see bayes_factor() below, which adds back the one surviving
    factor of 1/B(a,b)."""
    return math.lgamma(x + a) + math.lgamma(n - x + b) - math.lgamma(n + a + b)


def bayes_factor(x1: int, n1: int, x2: int, n2: int, a: float, b: float) -> dict:
    """Gunel-Dickey BF10 for two independent proportions vs. one shared rate."""
    if n1 <= 0 or n2 <= 0:
        raise ValueError("totals N1 and N2 must be positive")
    if not (0 <= x1 <= n1) or not (0 <= x2 <= n2):
        raise ValueError("successes must satisfy 0 <= X <= N for each group")
    if a <= 0.0 or b <= 0.0:
        raise ValueError("prior parameters a and b must be positive")

    log_bf10 = (
        log_beta_binom(x1, n1, a, b) + log_beta_binom(x2, n2, a, b)
        - log_beta_binom(x1 + x2, n1 + n2, a, b)
        - (math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b))
    )
    try:
        bf10 = math.exp(log_bf10)
    except OverflowError:
        bf10 = math.inf

    return {
        "p1": x1 / n1,
        "p2": x2 / n2,
        "risk_difference": x1 / n1 - x2 / n2,
        "log_bf10": log_bf10,
        "bf10": bf10,
        # Covers both bf10 == 0.0 from a literal input and bf10 that
        # underflowed to exactly 0.0 via math.exp() -- either way,
        # BF01 correctly goes to infinity.
        "bf01": 1.0 / bf10 if bf10 != 0.0 else math.inf,
        "prior_a": a,
        "prior_b": b,
    }


_JEFFREYS_LABELS = (
    (100.0, "decisive"),
    (30.0, "very strong"),
    (10.0, "strong"),
    (3.0, "substantial"),
    (1.0, "anecdotal"),
)


def qualitative_label(bf10: float) -> str:
    """Kass & Raftery (1995) / Jeffreys (1961) qualitative evidence label,
    stated in favor of whichever hypothesis the BF (as given) favors."""
    bf = bf10 if bf10 >= 1.0 else 1.0 / bf10
    for threshold, label in _JEFFREYS_LABELS:
        if bf >= threshold:
            favors = "H1 (independent rates)" if bf10 >= 1.0 else "H0 (shared rate)"
            return f"{label} evidence for {favors}"
    return "anecdotal evidence"


def classify(stats: dict, direction: str | None, bf_threshold: float | None) -> str | None:
    """Map the Bayes factor to a three-way verdict. Returns None (no verdict)
    when direction/threshold are not both given -- mirrors
    `two_proportion_test.py`'s two-way-only behavior without those flags."""
    if direction is None or bf_threshold is None:
        return None

    bf10 = stats["bf10"]
    if bf10 <= 0.0:
        return "inconcluso"

    if 1.0 / bf10 >= bf_threshold:
        # strong evidence FOR the shared-rate (null) model -- direction-independent
        return "refuta"

    diff = stats["p1"] - stats["p2"]
    predicted_sign = 1.0 if direction == "increase" else -1.0
    correct_direction = (diff * predicted_sign) > 0.0

    if bf10 >= bf_threshold and correct_direction:
        return "apoya"
    return "inconcluso"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Bayes factor for a two-proportion comparison (Gunel-Dickey).")
    parser.add_argument("--version", action="version",
                        version=f"bayes_factor_proportions.py {__version__} ({METHOD})")
    parser.add_argument("x1", type=int, help="successes, group 1 (treatment)")
    parser.add_argument("n1", type=int, help="total, group 1")
    parser.add_argument("x2", type=int, help="successes, group 2 (control)")
    parser.add_argument("n2", type=int, help="total, group 2")
    parser.add_argument("--prior-a", type=float, default=1.0, dest="prior_a",
                        help="Beta prior shape a (default 1.0, uniform)")
    parser.add_argument("--prior-b", type=float, default=1.0, dest="prior_b",
                        help="Beta prior shape b (default 1.0, uniform)")
    parser.add_argument("--direction", choices=("increase", "decrease"),
                        help="predicted direction of group 1 relative to group 2")
    parser.add_argument("--bf-threshold", type=float, dest="bf_threshold", default=3.0,
                        help="BF at or above which evidence counts as decisive for "
                             "the verdict (default 3.0, 'substantial' -- Kass & Raftery 1995)")
    parser.add_argument("--labels", default="treatment,control",
                        help="comma-separated labels for the two groups")
    parser.add_argument("--json", action="store_true",
                        help="also print a machine-readable 'RESULT_JSON: {...}' line")
    args = parser.parse_args(argv)

    try:
        stats = bayes_factor(args.x1, args.n1, args.x2, args.n2, args.prior_a, args.prior_b)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    verdict = classify(stats, args.direction, args.bf_threshold if args.direction else None)

    raw = args.labels.split(",", 1)
    l1 = raw[0].strip() or "treatment"
    l2 = (raw[1].strip() if len(raw) > 1 else "") or "control"

    print(f"{l1}: {args.x1}/{args.n1} = {stats['p1']:.6f}")
    print(f"{l2}: {args.x2}/{args.n2} = {stats['p2']:.6f}")
    print(f"risk_difference: {stats['risk_difference']:.6f}")
    print(f"prior: Beta({args.prior_a:g}, {args.prior_b:g})")
    print(f"BF10 = {stats['bf10']:.6g}   BF01 = {stats['bf01']:.6g}")
    print(qualitative_label(stats["bf10"]))
    if verdict is None:
        print("verdict: (no --direction/--bf-threshold given -- do NOT infer support)")
    else:
        print(f"verdict: {verdict}  (direction={args.direction}, "
              f"bf-threshold={args.bf_threshold:g})")

    if args.json:
        payload = dict(stats)
        payload.update(verdict=verdict, direction=args.direction,
                       bf_threshold=args.bf_threshold,
                       x1=args.x1, n1=args.n1, x2=args.x2, n2=args.n2)
        print("RESULT_JSON: " + json.dumps(payload, sort_keys=True))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
