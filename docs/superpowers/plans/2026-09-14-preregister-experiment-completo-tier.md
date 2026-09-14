# preregister-experiment: completo tier + analysis-plan choice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `completo` preregistration tier (formal a-priori sample-size
justification, required by `linea_publicacion` or a cost threshold) and an
independent `frequentist`/`bayesian` analysis-plan choice to
`preregister-experiment`, with the two new mechanical scripts and the
downstream `run-experiment` branch they require.

**Architecture:** Two new stdlib-only Python scripts under
`scripts/analysis/` (mirroring the existing `two_proportion_test.py` /
`combine_effects.py` conventions exactly: `argparse`, `--version`,
`--json`/`RESULT_JSON:`), a rewritten `preregister-experiment/SKILL.md` that
determines tier + analysis_plan at a new step 1 and threads both through the
rest of the freeze procedure, two small additive template fields, and a
branch in `run-experiment/SKILL.md` step 5 so a frozen `bayesian` plan can
actually be executed.

**Tech Stack:** Python 3 standard library only (`argparse`, `math`, `json`,
`statistics.NormalDist`) — no new dependencies. No test framework exists yet
in this repo (`two_proportion_test.py` / `combine_effects.py` have no unit
tests); this plan introduces `unittest` (stdlib, zero new dependency) for the
two new scripts, run via `python -m unittest <file> -v`. The `SKILL.md` /
template tasks are documentation, not code — TDD's red/green cycle is
replaced by an edit-then-verify cycle (grep/read-back checks for internal
consistency) on those tasks.

**Spec:** `docs/superpowers/specs/2026-09-14-preregister-experiment-completo-tier-design.md`

## Global Constraints

- Standard library only for both new scripts — no numpy/scipy (matches
  `two_proportion_test.py` / `combine_effects.py`).
- Every script: `--version` prints `<script>.py <version> (<method>)`;
  `--json` prints one `RESULT_JSON: {...}` line in addition to the normal
  human-readable stdout; exit codes 0 (ok) / 2 (invalid input).
- Tier (`ligero`/`completo`) and `analysis_plan` (`frequentist`/`bayesian`)
  are **independent axes** — any combination is valid.
- `completo` is a **hard floor, no override** — once required, the freeze
  blocks (`crítico`) until satisfied. No logged-override escape hatch.
- `confidence` is a real posterior only when *every* adjudicating experiment
  used `analysis_plan: bayesian` — never upgraded by a single Bayesian
  experiment among frequentist ones.
- Combining two Bayesian-plan replications (`update-confidence` /
  `combine_effects.py`) is **out of scope** — flagged at freeze time
  (`crítico`, only when `bayesian` + `linea_publicacion` coincide), not
  solved.
- No changes to `hypothesis-cycle`, `create-project`, `spawn-hypothesis`,
  `update-confidence`, or `hypothesis-template.md` — none are needed (see
  spec's "File-level footprint").
- `README.md`, `skills/create-project/SKILL.md`, `skills/literature-search/SKILL.md`
  may carry unrelated uncommitted changes from other concurrent work in this
  repo — edit `README.md` additively (one row only) and leave the other two
  untouched.

---

### Task 1: `sample_size.py` — a-priori power analysis script

**Files:**
- Create: `scripts/analysis/sample_size.py`
- Test: `scripts/analysis/test_sample_size.py`

**Interfaces:**
- Produces: `cohens_h(p1: float, p2: float) -> float`,
  `required_n(h: float, alpha: float, power: float) -> dict` (keys: `h`,
  `alpha`, `power`, `z_alpha2`, `z_power`, `n_per_group`, `n_total`) — both
  importable from `scripts/analysis/sample_size.py`. CLI:
  `python sample_size.py (--h H | --p1 P1 --p2 P2) --alpha A --power PW [--json]`.
  `--json` output includes a `RESULT_JSON:` line with the same keys as
  `required_n`'s dict plus `p1`/`p2` (null if `--h` was used).

- [ ] **Step 1: Write the failing tests**

Create `scripts/analysis/test_sample_size.py`:

```python
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sample_size import cohens_h, required_n


class TestCohensH(unittest.TestCase):
    def test_zero_when_proportions_equal(self):
        self.assertAlmostEqual(cohens_h(0.4, 0.4), 0.0, places=9)

    def test_antisymmetric(self):
        h_ab = cohens_h(0.3, 0.5)
        h_ba = cohens_h(0.5, 0.3)
        self.assertAlmostEqual(h_ab, -h_ba, places=9)

    def test_matches_known_reference_value(self):
        # h for p1=0.5, p2=0.6 is a commonly cited small-to-medium effect
        expected = 2 * math.asin(math.sqrt(0.6)) - 2 * math.asin(math.sqrt(0.5))
        self.assertAlmostEqual(cohens_h(0.5, 0.6), expected, places=9)


class TestRequiredN(unittest.TestCase):
    def test_cohen_1988_reference_table_value(self):
        # Cohen (1988) Table 6.4.2: h=0.2, alpha=.05 two-sided, power=.80
        # -> n=197 per group. This is an independently documented textbook
        # value, not derived from this script's own formula.
        result = required_n(0.2, 0.05, 0.80)
        self.assertEqual(result["n_per_group"], 197)
        self.assertEqual(result["n_total"], 394)

    def test_larger_effect_needs_fewer_subjects(self):
        small = required_n(0.2, 0.05, 0.80)["n_per_group"]
        large = required_n(0.5, 0.05, 0.80)["n_per_group"]
        self.assertLess(large, small)

    def test_higher_power_needs_more_subjects(self):
        lo = required_n(0.3, 0.05, 0.80)["n_per_group"]
        hi = required_n(0.3, 0.05, 0.95)["n_per_group"]
        self.assertLess(lo, hi)

    def test_rejects_zero_effect(self):
        with self.assertRaises(ValueError):
            required_n(0.0, 0.05, 0.80)

    def test_rejects_bad_alpha(self):
        with self.assertRaises(ValueError):
            required_n(0.2, 1.5, 0.80)
        with self.assertRaises(ValueError):
            required_n(0.2, 0.0, 0.80)

    def test_rejects_bad_power(self):
        with self.assertRaises(ValueError):
            required_n(0.2, 0.05, 1.0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest scripts/analysis/test_sample_size.py -v`
Expected: FAIL / ERROR — `ModuleNotFoundError: No module named 'sample_size'`
(the module doesn't exist yet).

- [ ] **Step 3: Write the implementation**

Create `scripts/analysis/sample_size.py`:

```python
#!/usr/bin/env python3
"""A-priori power analysis for a two-proportion design (`completo` tier).

Given a target effect size (Cohen's h -- the same arcsine-transformed metric
`two_proportion_test.py` reports as its own `cohens_h`), alpha, and desired
power, computes the required N per arm via the standard normal-approximation
formula. This is the mechanical tool `preregister-experiment` calls for a
`completo`-tier preregistration's sample-size justification, so the
required-N figure is derived the same way every time -- never picked first
and rationalized after.

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

__version__ = "1.0.0"
METHOD = "a-priori power analysis via Cohen's h (arcsine effect size, normal approximation)"

_N = NormalDist()


def cohens_h(p1: float, p2: float) -> float:
    """Arcsine-transformed effect size for two proportions (same transform
    `two_proportion_test.py` uses for its own `cohens_h` output)."""
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest scripts/analysis/test_sample_size.py -v`
Expected: `OK` — all 9 tests pass, including
`test_cohen_1988_reference_table_value` (`n_per_group == 197`).

- [ ] **Step 5: Manual CLI smoke check**

Run:
```bash
python scripts/analysis/sample_size.py --p1 0.50 --p2 0.60 --alpha 0.05 --power 0.80 --json
python scripts/analysis/sample_size.py --version
```
Expected: human-readable output plus one `RESULT_JSON:` line; `--version`
prints `sample_size.py 1.0.0 (a-priori power analysis via Cohen's h (arcsine effect size, normal approximation))`.

- [ ] **Step 6: Commit**

```bash
git add scripts/analysis/sample_size.py scripts/analysis/test_sample_size.py
git commit -m "Add sample_size.py: a-priori power analysis for the completo tier"
```

---

### Task 2: `bayes_factor_proportions.py` — Bayesian analysis-plan script

**Files:**
- Create: `scripts/analysis/bayes_factor_proportions.py`
- Test: `scripts/analysis/test_bayes_factor_proportions.py`

**Interfaces:**
- Produces: `log_beta_binom(x: int, n: int, a: float, b: float) -> float`,
  `bayes_factor(x1: int, n1: int, x2: int, n2: int, a: float, b: float) -> dict`
  (keys: `p1`, `p2`, `log_bf10`, `bf10`, `bf01`, `prior_a`, `prior_b`),
  `qualitative_label(bf10: float) -> str`,
  `classify(stats: dict, direction: str | None, bf_threshold: float | None) -> str | None`
  (returns `"apoya"` / `"refuta"` / `"inconcluso"` / `None`) — all importable
  from `scripts/analysis/bayes_factor_proportions.py`. CLI:
  `python bayes_factor_proportions.py X1 N1 X2 N2 [--prior-a A] [--prior-b B] [--direction increase|decrease] [--bf-threshold T] [--json]`.
  `--json` output includes a `RESULT_JSON:` line with `bf10`/`bf01`/`verdict`
  and the echoed inputs.
- Consumes: nothing from Task 1 (independent script).

- [ ] **Step 1: Write the failing tests**

Create `scripts/analysis/test_bayes_factor_proportions.py`:

```python
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bayes_factor_proportions import (
    bayes_factor,
    classify,
    log_beta_binom,
    qualitative_label,
)


class TestLogBetaBinom(unittest.TestCase):
    def test_uniform_prior_is_invariant_to_x(self):
        # With a Beta(1,1) prior, the Beta-Binomial marginal likelihood is
        # exactly 1/(n+1) for EVERY x in [0, n] (Laplace's rule of
        # succession) -- an exact, independently-derivable identity, not
        # something computed from this script's own code.
        for n in (5, 10, 20):
            expected = -math.log(n + 1)
            for x in (0, n // 2, n):
                self.assertAlmostEqual(
                    log_beta_binom(x, n, 1.0, 1.0), expected, places=9,
                    msg=f"x={x}, n={n}")


class TestBayesFactor(unittest.TestCase):
    def test_uniform_prior_closed_form_bf10(self):
        # For a=b=1, BF10 = (n1+n2+1) / ((n1+1)*(n2+1)), independent of
        # x1/x2 -- derived exactly from the Laplace's-rule identity above,
        # not from this script's own implementation.
        n1, n2 = 10, 10
        expected_bf10 = (n1 + n2 + 1) / ((n1 + 1) * (n2 + 1))
        for x1, x2 in ((7, 3), (0, 0), (10, 10), (5, 5)):
            result = bayes_factor(x1, n1, x2, n2, 1.0, 1.0)
            self.assertAlmostEqual(result["bf10"], expected_bf10, places=9,
                                   msg=f"x1={x1}, x2={x2}")

    def test_bf01_is_reciprocal_of_bf10(self):
        result = bayes_factor(7, 10, 3, 10, 1.0, 1.0)
        self.assertAlmostEqual(result["bf10"] * result["bf01"], 1.0, places=9)

    def test_strong_divergence_favors_h1(self):
        # A stark difference in observed rates should push BF10 well above 1.
        result = bayes_factor(90, 100, 10, 100, 1.0, 1.0)
        self.assertGreater(result["bf10"], 10.0)

    def test_identical_rates_favor_h0(self):
        result = bayes_factor(50, 100, 50, 100, 1.0, 1.0)
        self.assertLess(result["bf10"], 1.0)

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            bayes_factor(0, 0, 5, 10, 1.0, 1.0)
        with self.assertRaises(ValueError):
            bayes_factor(11, 10, 5, 10, 1.0, 1.0)
        with self.assertRaises(ValueError):
            bayes_factor(5, 10, 5, 10, 0.0, 1.0)


class TestQualitativeLabel(unittest.TestCase):
    def test_boundaries(self):
        self.assertIn("anecdotal", qualitative_label(2.0))
        self.assertIn("substantial", qualitative_label(5.0))
        self.assertIn("strong", qualitative_label(15.0))
        self.assertIn("H0", qualitative_label(0.05))  # favors null


class TestClassify(unittest.TestCase):
    def test_none_without_direction_or_threshold(self):
        stats = {"bf10": 50.0, "bf01": 0.02, "p1": 0.9, "p2": 0.1}
        self.assertIsNone(classify(stats, None, 3.0))
        self.assertIsNone(classify(stats, "increase", None))

    def test_apoya_when_bf_high_and_direction_correct(self):
        stats = {"bf10": 50.0, "bf01": 0.02, "p1": 0.9, "p2": 0.1}
        self.assertEqual(classify(stats, "increase", 3.0), "apoya")

    def test_refuta_when_bf_favors_null(self):
        stats = {"bf10": 0.1, "bf01": 10.0, "p1": 0.5, "p2": 0.5}
        self.assertEqual(classify(stats, "increase", 3.0), "refuta")

    def test_inconcluso_when_bf_ambiguous(self):
        stats = {"bf10": 1.5, "bf01": 1.0 / 1.5, "p1": 0.55, "p2": 0.45}
        self.assertEqual(classify(stats, "increase", 3.0), "inconcluso")

    def test_inconcluso_when_direction_wrong(self):
        stats = {"bf10": 50.0, "bf01": 0.02, "p1": 0.1, "p2": 0.9}
        self.assertEqual(classify(stats, "increase", 3.0), "inconcluso")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest scripts/analysis/test_bayes_factor_proportions.py -v`
Expected: FAIL / ERROR — `ModuleNotFoundError: No module named 'bayes_factor_proportions'`.

- [ ] **Step 3: Write the implementation**

Create `scripts/analysis/bayes_factor_proportions.py`:

```python
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

    log BetaBinom(x, n, a, b) = lgamma(n+1) - lgamma(x+1) - lgamma(n-x+1)
                               + lgamma(x+a) + lgamma(n-x+b) - lgamma(n+a+b)
                               - (lgamma(a) + lgamma(b) - lgamma(a+b))

    log m(H1) = log BetaBinom(x1, n1, a, b) + log BetaBinom(x2, n2, a, b)
    log m(H0) = log BetaBinom(x1+x2, n1+n2, a, b)
    BF10 = exp(log m(H1) - log m(H0))

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
    """Log marginal likelihood of x successes in n trials under a Beta(a, b)
    prior on the success rate (log Beta-Binomial pmf)."""
    return (
        math.lgamma(n + 1) - math.lgamma(x + 1) - math.lgamma(n - x + 1)
        + math.lgamma(x + a) + math.lgamma(n - x + b) - math.lgamma(n + a + b)
        - (math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b))
    )


def bayes_factor(x1: int, n1: int, x2: int, n2: int, a: float, b: float) -> dict:
    """Gunel-Dickey BF10 for two independent proportions vs. one shared rate."""
    if n1 <= 0 or n2 <= 0:
        raise ValueError("totals N1 and N2 must be positive")
    if not (0 <= x1 <= n1) or not (0 <= x2 <= n2):
        raise ValueError("successes must satisfy 0 <= X <= N for each group")
    if a <= 0.0 or b <= 0.0:
        raise ValueError("prior parameters a and b must be positive")

    log_m_h1 = log_beta_binom(x1, n1, a, b) + log_beta_binom(x2, n2, a, b)
    log_m_h0 = log_beta_binom(x1 + x2, n1 + n2, a, b)
    log_bf10 = log_m_h1 - log_m_h0
    bf10 = math.exp(log_bf10)

    return {
        "p1": x1 / n1,
        "p2": x2 / n2,
        "log_bf10": log_bf10,
        "bf10": bf10,
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest scripts/analysis/test_bayes_factor_proportions.py -v`
Expected: `OK` — all tests pass, including
`test_uniform_prior_is_invariant_to_x` and
`test_uniform_prior_closed_form_bf10` (the exact Laplace's-rule identities).

- [ ] **Step 5: Manual CLI smoke check**

Run:
```bash
python scripts/analysis/bayes_factor_proportions.py 90 100 10 100 --direction increase --json
python scripts/analysis/bayes_factor_proportions.py --version
```
Expected: `bf10` well above 100, verdict `apoya`; `--version` prints
`bayes_factor_proportions.py 1.0.0 (Gunel-Dickey independent-binomials Bayes factor, Beta(a, b) prior)`.

- [ ] **Step 6: Commit**

```bash
git add scripts/analysis/bayes_factor_proportions.py scripts/analysis/test_bayes_factor_proportions.py
git commit -m "Add bayes_factor_proportions.py: Bayesian analysis-plan script"
```

---

### Task 3: Template updates (`completo_cost_threshold`, `analysis_plan`)

**Files:**
- Modify: `templates/project-template.md:9-10`
- Modify: `templates/experiment-template.md:14`, `templates/experiment-template.md:73-77`

**Interfaces:**
- Produces: project frontmatter field `completo_cost_threshold` (optional,
  free-form value like `compute_budget`); experiment frontmatter field
  `analysis_plan: <frequentist | bayesian>`. Both are read by Task 4's
  `preregister-experiment/SKILL.md` rewrite.

- [ ] **Step 1: Edit `templates/project-template.md`**

Find:
```
# compute_budget — optional (e.g. "500 GPU-h" or a currency figure)
compute_budget: <value>
```

Replace with:
```
# compute_budget — optional (e.g. "500 GPU-h" or a currency figure)
compute_budget: <value>
# completo_cost_threshold — optional, same unit as compute_budget.
# preregister-experiment requires the `completo` tier (formal sample-size
# justification) once a hypothesis's cost_estimated exceeds this value, in
# addition to the linea_publicacion trigger. Unset, or a unit mismatch with
# cost_estimated, means cost alone cannot trigger completo.
completo_cost_threshold: <value>
```

- [ ] **Step 2: Edit `templates/experiment-template.md` frontmatter**

Find:
```
tier: <ligero | completo>
status: <preregistered | running | completed>
```

Replace with:
```
tier: <ligero | completo>
# analysis_plan — chosen at preregistration time, independent of tier.
# frequentist -> scripts/analysis/two_proportion_test.py (run-experiment step 5)
# bayesian    -> scripts/analysis/bayes_factor_proportions.py (run-experiment step 5)
analysis_plan: <frequentist | bayesian>
status: <preregistered | running | completed>
```

- [ ] **Step 3: Edit `templates/experiment-template.md`'s `## Plan de análisis` placeholder**

Find:
```
## Plan de análisis

<Test o modelo exacto, estadístico de decisión, umbral (α o Bayes factor),
correcciones por comparaciones múltiples. Congelado en `frozen_at`.>
```

Replace with:
```
## Plan de análisis

<Test o modelo exacto, estadístico de decisión, umbral (α o Bayes factor),
correcciones por comparaciones múltiples. Congelado en `frozen_at`.
Tier `completo`: incluir el tamaño de efecto mínimo de interés (SESOI), α,
potencia objetivo, y el N resultante de `sample_size.py`. `analysis_plan:
bayesian`: incluir el prior (Beta(a, b)) y el umbral de Bayes factor.>
```

- [ ] **Step 4: Verify**

Run:
```bash
grep -n "completo_cost_threshold" templates/project-template.md
grep -n "analysis_plan" templates/experiment-template.md
```
Expected: each prints the new lines exactly as written above — confirms the
fields Task 4/5 will reference actually exist with these exact names.

- [ ] **Step 5: Commit**

```bash
git add templates/project-template.md templates/experiment-template.md
git commit -m "Add completo_cost_threshold and analysis_plan template fields"
```

---

### Task 4: Rewrite `preregister-experiment/SKILL.md`

**Files:**
- Modify: `skills/preregister-experiment/SKILL.md` (complete rewrite)
- Modify: `README.md:76` (one-line, additive)

**Interfaces:**
- Consumes: `scripts/analysis/sample_size.py` CLI (Task 1),
  `scripts/analysis/bayes_factor_proportions.py` CLI (Task 2),
  `completo_cost_threshold` / `analysis_plan` fields (Task 3).
- Produces: the tier-determination + analysis-plan procedure (step 1) that
  Task 5's `run-experiment` branch depends on (`analysis_plan` frontmatter
  value, `tier` frontmatter value).

- [ ] **Step 1: Replace the full content of `skills/preregister-experiment/SKILL.md`**

```markdown
---
name: preregister-experiment
description: >-
  Use when a project's `propuesta` hypothesis is ready to be tested and needs
  its preregistration written and frozen. Turns the hypothesis claim + test
  sketch into a frozen `Experimentos/E-XXXX.md` note (exact prediction, metric
  + decision thresholds, control condition, environment manifest), records
  `frozen_at` / `frozen_commit`, and sets the experiment note `status` to
  `preregistered`. Two independent choices are fixed at freeze time: tier
  (`ligero` domain-judgement thresholds, or `completo` — required once
  `linea_publicacion: true` or estimated cost exceeds the project's
  `completo_cost_threshold` — a formal a-priori sample-size justification) and
  `analysis_plan` (`frequentist` or `bayesian`, each computed via a versioned
  script). Produces the note only — runs nothing.
---

# Preregister Experiment

## Overview

Writes and **freezes** a preregistration for one hypothesis, at whichever
tier (`ligero` / `completo`) this run determines and whichever
`analysis_plan` (`frequentist` / `bayesian`) the researcher chooses — the two
are independent axes; any combination is valid.

Binding rule (Kairo principle 3): the preregistration is **the
prediction plus the analysis plan**, frozen **before any experiment code runs**.
Once the experiment's code starts, the preregistration is **immutable** — later
changes go only into `## Enmiendas`, never as edits to the original sections.

This skill produces the frozen note and nothing else. It does not run, schedule,
or scaffold experiment code.

## When to use

- A hypothesis is `status: propuesta` and the team wants to test it now.
- You have a test sketch (from `hypothesis-cycle`) to harden into an exact,
  falsifiable protocol.

**When not to use:** the hypothesis isn't `propuesta` yet; you want to execute or
analyze a run (separate skills).

## Inputs

1. A **`propuesta`** hypothesis note (`Projects/<slug>/Hipotesis/H-XXXX.md`).
   Refuse if its `status` is anything else.
2. Its **test sketch** — the manipulation/comparison, what gets measured, what
   result counts against the claim.

## Scope — `ligero` and `completo` tiers

**Both tiers:** exact prediction, one (or few) pre-committed primary metric(s)
with three-way decision thresholds, a control condition tied to a known
result, and a reproducibility manifest.

**`ligero`:** thresholds (`T_apoyo` / `T_refuta`) are picked by domain
judgement — no power calculation.

**`completo`:** required whenever `hypothesis.linea_publicacion == true`, or
`cost_estimated` exceeds the project's `completo_cost_threshold` (same units
— a unit mismatch, a non-numeric value, or an unset threshold means cost
cannot trigger it on its own; see step 1a). Thresholds are instead derived
from a formal a-priori sample-size justification: state the smallest effect
size of interest (SESOI), alpha, and target power; the required N is computed
mechanically (step 1b), never picked first and rationalized after. **Hard
floor — no override.** Once `completo` is required, there is no in-spec way
to freeze at `ligero` anyway; an unresolved requirement blocks the freeze
exactly like an open `crítico` risk (see "Flagging risks and ambiguities").

**Independent of tier — `analysis_plan`:** `frequentist` (the existing
mechanical test) or `bayesian` (a Bayes factor, computed via a versioned
script). Chosen once, at step 1c, regardless of tier.

## Procedure

### 1. Determine tier and analysis plan

Before fixing the protocol (step 2), settle both axes — they are independent
and each is recorded in frontmatter.

**a. Tier.**

```
completo required  <=>  hypothesis.linea_publicacion == true
                        OR (cost_estimated is set AND project.completo_cost_threshold
                            is set AND both are bare numbers in the same unit AND
                            cost_estimated > completo_cost_threshold)
```

- If `cost_estimated` is set but the project has no `completo_cost_threshold`,
  or the two values are in different units (e.g. one is GPU-h, the other a
  currency figure), or either isn't a bare number: cost cannot force
  `completo` on its own. Flag `importante` — say explicitly that only
  `linea_publicacion` was checked.
- If neither condition holds, default to `ligero`. The researcher may still
  opt into `completo` voluntarily for a smaller experiment.
- Set `tier: ligero` or `tier: completo` in frontmatter now.

**b. If `completo`: run the sample-size justification.**

State, with the researcher: the smallest effect size of interest (SESOI, as
two proportions `p1`/`p2` — baseline and the smallest treatment rate that
would still count as meaningful — or directly as Cohen's `h`), alpha, and
target power. Then call:

```
python ${CLAUDE_PLUGIN_ROOT}/scripts/analysis/sample_size.py \
    --p1 <baseline> --p2 <smallest meaningful> --alpha <alpha> --power <power> --json
```

(or `--h <h>` if the effect is given directly, e.g. for a design that isn't
naturally two proportions). Take `n_per_group` / `n_total` verbatim — this
becomes the stopping rule's fixed N in step 2b, replacing a
domain-judgement-chosen figure. Record the exact command, its output, and the
SESOI/alpha/power inputs in `## Plan de análisis` (step 2b).

**c. Analysis plan.** Ask the researcher: `frequentist` (the existing
`two_proportion_test.py` mechanical test — default) or `bayesian` (a Bayes
factor via `scripts/analysis/bayes_factor_proportions.py`). Set
`analysis_plan: frequentist` or `analysis_plan: bayesian` in frontmatter.

- **`frequentist`:** step 2b states alpha and the min/floor effect thresholds,
  as today.
- **`bayesian`:** step 2b states the prior (`Beta(a, b)`, default `Beta(1, 1)`
  — uniform — unless a different prior is justified) and the BF decision
  threshold (`--bf-threshold`, default 3.0, "substantial evidence" — Kass &
  Raftery 1995) instead of alpha/min-effect.

**d. Confidence-field semantics — state this now, not later.** If `bayesian`
is chosen: `confidence` on the hypothesis note is a real posterior **only**
once *every* experiment adjudicating it used `analysis_plan: bayesian` — a
single frequentist-plan experiment among them keeps `confidence` a
`frequentist_heuristic`, never silently upgraded. Say this explicitly to the
researcher when they pick `bayesian`, don't leave it to the template comment.

**e. Mixed bayesian + linea_publicacion — flag, don't solve.** If
`analysis_plan: bayesian` AND `hypothesis.linea_publicacion: true` both hold:
flag `crítico`. `update-confidence`'s replication combiner
(`combine_effects.py`) works on effect-size + standard error and has **no**
defined way to combine two Bayes factors — a second independent Bayesian
replication will hit a combination step that doesn't exist yet. This blocks
the freeze until the researcher explicitly acknowledges it (e.g. by switching
to `frequentist`, or by accepting that a future combination method is not yet
built). Do not attempt to invent a combination method here — that is a
separate, unscoped piece of work.

### 2. Create the note

Copy `${CLAUDE_PLUGIN_ROOT}/templates/experiment-template.md` to
**`Projects/<slug>/Experimentos/E-XXXX.md`** — next `E-XXXX` id (scan all
`Projects/*/Experimentos/*.md` frontmatter `id:`, max + 1, zero-pad 4).

Frontmatter now: `hypothesis: <H-XXXX>` (the **one** hypothesis this prereg
adjudicates), `project: <PROJ-XXX>`, `tier` and `analysis_plan` (both from
step 1), `status` left at placeholder until step 4. If the same design also
bears on a **rival / sibling** hypothesis without adjudicating it, list those
under `secondary_hypotheses: [<H-YYYY>]` and write a `## Evidencia colateral`
entry for each (step 3e). Leave `sanity_checks`, `experiment_validity`,
`cost_actual`, `result` as placeholders — a later run/analysis skill fills
them.

### 3. Fix the protocol

All four must be exact and unambiguous before freezing.

**a. Exact prediction** → `## Predicción`
Quantitative statement of what will be observed **if the hypothesis is true** —
direction and rough magnitude, in the metric's units. Not "X improves Y" but "X
raises Y by at least <amount> relative to control".

**b. Metric + decision thresholds** → `## Plan de análisis`
Name the **primary metric(s)** (prefer one). Pre-commit the three-way rule:

| Verdict (`result.verdict`) | Condition on the primary metric |
|---|---|
| `apoyada` | effect in the predicted direction and ≥ the pre-set meaningful size `T_apoyo` (or, `bayesian`: `BF10 ≥` the frozen `--bf-threshold` in the predicted direction) |
| `refutada` | no effect, or effect ≤ `T_refuta` / wrong direction (or, `bayesian`: `BF01 ≥` the frozen `--bf-threshold`) |
| `inconclusa` | between `T_refuta` and `T_apoyo`, or the interval spans both (or, `bayesian`: neither BF crosses the threshold) |
| `evidencia_mixta` | **only if** the plan names >1 primary metric and they land in conflicting regions |

State the estimator and the interval you'll report (e.g. mean difference + 95%
CI, or `bayesian`: `BF10`/`BF01` + the prior).

- **`ligero`:** thresholds are domain judgement, fixed now, no power calc.
- **`completo`:** state the SESOI, alpha, target power, and
  `sample_size.py`'s `n_per_group`/`n_total` output (step 1b) — `T_apoyo`
  equals the stated SESOI, not a separately chosen figure.
- **`bayesian`:** state the prior (`Beta(a, b)`) and the `--bf-threshold`
  instead of alpha/min-effect.

**Stopping rule (required).** State the *exact* condition under which the run /
data collection ends, decided **now**, not during the run. For `completo`,
this **is** the `n_per_group`/`n_total` from step 1b (e.g. `fixed N = 197 per
arm`) — not a separately chosen figure. For `ligero`, e.g. `fixed N = 2000
per arm`, `fixed 5 seeds`, `runs until the wall-clock budget of 6 GPU-h is
spent`, `one pass over the frozen dataset`. "Until the result looks clear" is
not a stopping rule.

**Variables recorded (required)** → the template's `## Variables` section, split
into two lists:

- **Primarias** — the metric(s) named above that drive the three-way verdict.
  Nothing else may be promoted to a verdict input after the fact.
- **Secundarias / exploratorias** — everything else that will be logged
  (diagnostics, ablation metrics, per-subgroup breakdowns). Recorded for context
  and future hypotheses; **never** used for this experiment's verdict.

Listing both closes the selective-reporting loophole without a power analysis: a
result that only appears in a secondary variable is a new hypothesis, not this
one's outcome.

**c. Control condition + known result** → `## Diseño`
Define the control arm and **the specific published/established result it must
reproduce** (cite `P-XXXX §…`), within a stated tolerance. This is the
experiment's sanity anchor and feeds `## Umbral de invalidez`: if the control
fails to reproduce that result, the run is `experiment_validity: invalid` and
verdicts are not read.

**d. Environment manifest** → `environment:` frontmatter + `## Manifiesto de entorno`
(see next section).

**e. Collateral evidence (only if `secondary_hypotheses` is non-empty)** →
`## Evidencia colateral`
For each secondary hypothesis: which **secondary / exploratory** observation of
this design touches it, what that observation would suggest, and **why this
design cannot adjudicate it** — no decision threshold, no verdict, no dedicated
replication. The frozen `## Predicción` and `## Plan de análisis` (thresholds,
stopping rule, verdict map) cover the **primary hypothesis only**. A secondary
hypothesis never gets a `T_apoyo` / `T_refuta` (or BF threshold); if you find
yourself wanting to set one, it is not secondary — make it the primary of its
own preregistration.

### 4. Freeze

Only once a–d (and e, if `secondary_hypotheses` is non-empty) are complete and
exact, **and no `crítico` risk is still open** (see "Flagging risks and
ambiguities" — this now includes an unmet `completo` requirement and an
unacknowledged bayesian+linea_publicacion gap, step 1e). An unresolved
`crítico` ambiguity blocks the freeze — resolve it with the researcher first.

1. `environment.seed`, `dependencies_hash`, `dependencies_lockfile`,
   `dataset_hash`, `hardware` all filled (step 3d).
2. Set `frozen_at` = current UTC timestamp, ISO 8601 (`YYYY-MM-DDTHH:MM:SSZ`).
3. Set `frozen_commit` = current commit hash (`git rev-parse HEAD`). Use the
   **experiment-code** repo's HEAD if code lives in its own repo; otherwise the
   vault HEAD, and say which in `## Manifiesto de entorno`.
4. Set `status: preregistered`.
5. Commit the note: `Preregister E-XXXX (<H-XXXX>)`. The preregistration is not
   frozen until it is in git.

### 4b. Hand off the hypothesis transition to `update-confidence`

A frozen prereg with a dangling link is a data-integrity gap — but this skill
does **not** edit hypothesis `status`, `linked_experiment`, `history`, or
`_digest.md`. Once the note is committed (step 4, item 5), **invoke
`update-confidence`** — trigger `prereg frozen`, payload = the **primary**
hypothesis id + this experiment id. It moves that hypothesis
`propuesta → preregistrada`, appends `E-XXXX` to its `linked_experiment`, appends
the `history` entry, and regenerates `_digest.md`.

**Secondary hypotheses do not transition.** For each id in
`secondary_hypotheses`, this skill appends `E-XXXX` to that note's
`collateral_evidence:` list directly (it is not a status-linked field, so
`update-confidence` is not involved) and adds no `history` entry. Their `status`
is untouched.

### 5. Stop

Do not run, schedule, or scaffold anything. Output is the frozen note plus the
`update-confidence` handoff. Writing the experiment code is `run-experiment`
step 0 ("Implement the frozen design — literally"), which happens after this
freeze.

## Environment manifest

Fill `environment:` in frontmatter; record how each value was produced in
`## Manifiesto de entorno`.

| Field | How |
|---|---|
| `seed` | the single integer seeded everywhere (framework, data shuffling, sampling). |
| `dependencies_lockfile` | run `pip freeze` (Python) or `npm ls --json` (Node); save output next to the note as `E-XXXX.deps.txt` / `.json`; put that relative path here. |
| `dependencies_hash` | `sha256` of that saved snapshot file. |
| `dataset_hash` | `sha256` of the dataset file. Multiple files → list per-file `sha256` in the body and put the hash of the sorted-hash manifest here. No dataset → `n/a`. |
| `hardware` | one line, e.g. `1x RTX 4090 24GB, 32 GB RAM` or `MacBook Pro M2, CPU only`. |

`## Manifiesto de entorno` records: exact commands run and when, per-file dataset
hashes, the snapshot file paths, and which repo `frozen_commit` refers to.

## Flagging risks and ambiguities — with a severity

Any risk or open question you raise with the researcher during design (or while
writing code — `run-experiment` step 0 uses the same vocabulary) carries an
explicit severity. A choice that can burn the whole compute budget must not be
presented with the same weight as a trivial one.

| Tag | Meaning | What it forces |
|---|---|---|
| **`crítico`** | can invalidate the entire experiment or spend the full compute budget with no readable result — e.g. a control that won't reproduce the cited result, a model/architecture detail that could suppress the effect being measured, a stopping rule open to interpretation, a primary metric that doesn't actually measure the claim, a threshold with no `inconclusa` band, an unmet `completo` requirement, an unacknowledged bayesian+linea_publicacion combination gap | **Blocks the freeze.** Present it in its **own callout at the top** of what you show the researcher — never a bullet among minor items. The design is not frozen until the researcher gives an explicit decision on it. If found *after* freeze: `## Enmiendas` entry + a direct `crítico`-tagged question before any run. |
| **`importante`** | plausibly shifts the result or its interpretation, but the experiment stays readable either way — a defensible-but-contested hyperparameter, an estimator choice, a borderline exclusion criterion, a cost-based `completo` trigger that couldn't be evaluated (missing/mismatched threshold) | Flag prominently with a proposed default + rationale. Get a decision before freezing if the researcher is available; otherwise freeze on the stated default and note the choice in `## Manifiesto de entorno`. |
| **`menor`** | implementation detail, low impact either way — activation function where the design doesn't turn on it, logging cadence, variable naming, RNG library when results are seed-identical | State the choice in one line. No decision needed, no freeze block. |

Never fold a `crítico` risk into a list next to `menor` ones. "This control may
not reproduce the cited baseline result and the run comes back invalid" is not the same kind of
sentence as "ReLU or GELU?" — and must not look like it.

## After freeze — amendments only

Once `status: preregistered` (and absolutely once code has run), the original
sections — `## Predicción`, `## Plan de análisis` (including the stopping rule),
`## Variables` (including the primary/secondary split), `## Diseño`,
`## Umbral de invalidez`, `## Manifiesto de entorno`, and the `environment` /
`frozen_*` / `tier` / `analysis_plan` frontmatter — are **immutable**.

Every later change goes in a `## Enmiendas` section, append-only:

```markdown
## Enmiendas

### 2026-09-12 — <qué cambió>
**Motivo:** <por qué>
**Afecta a:** <sección / campo>
**Antes → después:** <resumen>
```

Never silently edit a frozen section. An amendment after code has run is
disclosed as such when results are reported.

## Frontmatter at freeze

- `id`, `hypothesis` (primary — exactly one), `secondary_hypotheses` (`[]` or a
  list, each with a `## Evidencia colateral` entry), `project`,
  `tier: <ligero | completo>` (step 1a), `analysis_plan: <frequentist | bayesian>`
  (step 1c), `status: preregistered`
- `frozen_at` (ISO 8601 UTC), `frozen_commit` (sha)
- `environment.seed`, `.dependencies_lockfile`, `.dependencies_hash`,
  `.dataset_hash`, `.hardware`
- `experiment_validity`, `sanity_checks.*`, `cost_actual`, `result.*` — left as
  placeholders for the run/analysis skill
- `cost_estimated` — a rough figure if the sketch supports one, else placeholder
  (also the input to the `completo`-tier cost trigger in step 1a)

## Not in scope

- Running, scheduling, or scaffolding experiment code (that's `run-experiment`).
- Filling sanity checks, validity, actual cost, or results (also
  `run-experiment`).
- Combining two Bayesian-plan replications for the `update-confidence`
  replication gate (flagged, not solved — step 1e).

## Common mistakes

- **Freezing before the protocol is exact.** "Improves accuracy" is not a
  prediction; give direction + magnitude + units.
- **No `inconclusa` band.** The decision rule must have three regions, not a
  single pass/fail cut.
- **Control with no named known result.** The control must reproduce a specific
  cited result, or it can't anchor validity.
- **Editing a frozen section.** After `status: preregistered`, changes go in
  `## Enmiendas` — always.
- **Recording `frozen_commit` from the wrong repo.** It's the experiment *code*
  state; say which repo in the manifest.
- **Picking domain-judgement thresholds under `completo`.** `completo` requires
  the sample-size script's output (step 1b); `T_apoyo` is the stated SESOI, not
  a separately chosen figure.
- **Skipping the sample-size justification because `linea_publicacion` was set
  after the fact.** Check step 1a *before* fixing thresholds — retrofitting a
  `completo` justification onto already-chosen `ligero` thresholds is not the
  same as deriving them from the SESOI/alpha/power calc.
- **No stopping rule.** An open-ended run is optional-stopping. Commit a fixed N /
  seed count / budget in `## Plan de análisis` now.
- **Un-split variables.** Every variable that will be recorded is declared *now*
  as primary (verdict-driving) or secondary/exploratory. A result found only in a
  secondary variable is a new hypothesis.
- **Editing hypothesis `status` / `linked_experiment` inline.** This skill never
  does — fire `update-confidence`'s `prereg frozen` trigger (step 4b).
- **Running the experiment.** This skill stops at the frozen note.
- **Giving a `secondary_hypotheses` entry a decision threshold.** Secondary =
  collateral, non-adjudicating. Thresholds and a verdict map exist for the
  primary `hypothesis:` only; a secondary hypothesis that needs its own threshold
  needs its own preregistration.
- **Listing more than one primary `hypothesis:`.** One prereg adjudicates one
  hypothesis. Rival-pair designs put the other side in `secondary_hypotheses`.
- **Flagging a budget-burning risk like a trivial one.** Every risk raised gets a
  `crítico` / `importante` / `menor` tag; a `crítico` gets its own callout and
  blocks the freeze until the researcher decides.
- **Treating `confidence` as a real posterior under a frequentist plan, or under
  mixed evidence.** It is a real posterior only once every adjudicating
  experiment used `analysis_plan: bayesian` (step 1d) — otherwise it stays a
  `frequentist_heuristic`.
- **Letting `bayesian` + `linea_publicacion` through without the step-1e flag.**
  There is no combination method yet for two Bayesian replications — say so at
  freeze time, don't discover it at the second experiment.

## Related

- `${CLAUDE_PLUGIN_ROOT}/scripts/analysis/sample_size.py` — a-priori power
  analysis for the `completo` tier (step 1b). `--help` documents the formula.
- `${CLAUDE_PLUGIN_ROOT}/scripts/analysis/two_proportion_test.py` — the
  `frequentist` mechanical test, run by `run-experiment` step 5.
- `${CLAUDE_PLUGIN_ROOT}/scripts/analysis/bayes_factor_proportions.py` — the
  `bayesian` mechanical test, run by `run-experiment` step 5.
- `update-confidence` — the `prereg frozen` trigger this skill fires (step 4b);
  also owns the replication combiner that cannot yet combine two Bayes factors
  (step 1e).
- `hypothesis-template.md` — the `confidence` field's `frequentist_heuristic` /
  `bayesian_posterior` kinds (step 1d).
```

- [ ] **Step 2: Edit `README.md`'s `preregister-experiment` row**

Find (README.md line 76):
```
| `kairo:preregister-experiment` | Writes and freezes a preregistration for one hypothesis: exact prediction, primary metric with three-way decision thresholds, a control tied to a known published result, a stopping rule, and an environment manifest. Records `frozen_at` / `frozen_commit`. Produces the frozen note only — runs nothing. |
```

Replace with:
```
| `kairo:preregister-experiment` | Writes and freezes a preregistration for one hypothesis: exact prediction, primary metric with three-way decision thresholds, a control tied to a known published result, a stopping rule, and an environment manifest. Two independent choices at freeze time: tier (`ligero`, or `completo` — a formal a-priori sample-size justification, required by `linea_publicacion` or a cost threshold) and analysis plan (`frequentist` or `bayesian`, via a versioned Bayes-factor script). Records `frozen_at` / `frozen_commit`. Produces the frozen note only — runs nothing. |
```

(Only this one row — `README.md` has unrelated uncommitted sections from
other concurrent work; do not touch anything else in the file.)

- [ ] **Step 3: Verify internal consistency**

Run:
```bash
grep -n "^### " skills/preregister-experiment/SKILL.md
grep -c "step 1[a-e]" skills/preregister-experiment/SKILL.md
```
Expected: step headers read `1. Determine tier and analysis plan`,
`2. Create the note`, `3. Fix the protocol`, `4. Freeze`, `4b. Hand off...`,
`5. Stop` in that order (no orphaned old step numbers); every `step 1a`–`1e`
cross-reference elsewhere in the file resolves to a real subsection above.

- [ ] **Step 4: Commit**

```bash
git add skills/preregister-experiment/SKILL.md README.md
git commit -m "Add completo tier and frequentist/bayesian analysis-plan choice to preregister-experiment"
```

---

### Task 5: `run-experiment` step 5 branch

**Files:**
- Modify: `skills/run-experiment/SKILL.md:185-214` (step 5, "Mechanical analysis")
- Modify: `skills/run-experiment/SKILL.md` (Common mistakes list)
- Modify: `skills/run-experiment/SKILL.md:303-309` ("Related" section)

**Interfaces:**
- Consumes: `analysis_plan` frontmatter field (Task 3/4),
  `bayes_factor_proportions.py` CLI (Task 2).

- [ ] **Step 1: Replace step 5 ("Mechanical analysis")**

Find:
```
### 5. Mechanical analysis

Apply **exactly** the frozen `## Plan de análisis`. For a two-proportion
comparison, call the script — do not compute or reason about significance
yourself:

```
python ${CLAUDE_PLUGIN_ROOT}/scripts/analysis/two_proportion_test.py X1 N1 X2 N2 \
    --alpha <frozen alpha> \
    --direction <increase|decrease from the frozen prediction> \
    --min-effect <frozen T_apoyo> --floor-effect <frozen T_refuta> --json
```

Take the script's `risk_difference` / `cohens_h`, `p_value`, and `verdict`
verbatim. If the frozen plan names a test with **no script available**, stop and
report the missing tool — do not eyeball it.

Write into the note's `result:` frontmatter: `effect` (point estimate +
interval), `p_value` (or `bayes_factor` for a bayesian plan), and `verdict`,
mapping the script's short string to the enum:

| script | `result.verdict` |
|---|---|
| `apoya` | `apoyada` |
| `refuta` | `refutada` |
| `inconcluso` | `inconclusa` |

(`evidencia_mixta` only if the frozen plan named >1 primary metric and they
conflict.) Add a `## Resultado` section (new content, not a frozen edit) with the
exact command run, the full script output, and the mapped verdict.
```

Replace with:
```
### 5. Mechanical analysis

Apply **exactly** the frozen `## Plan de análisis`. Branch on the frozen
`analysis_plan` — do not compute or reason about significance yourself:

**`analysis_plan: frequentist`** — the two-proportion z-test:

```
python ${CLAUDE_PLUGIN_ROOT}/scripts/analysis/two_proportion_test.py X1 N1 X2 N2 \
    --alpha <frozen alpha> \
    --direction <increase|decrease from the frozen prediction> \
    --min-effect <frozen T_apoyo> --floor-effect <frozen T_refuta> --json
```

Take `risk_difference` / `cohens_h`, `p_value`, and `verdict` verbatim.

**`analysis_plan: bayesian`** — the Beta-Binomial Bayes factor:

```
python ${CLAUDE_PLUGIN_ROOT}/scripts/analysis/bayes_factor_proportions.py X1 N1 X2 N2 \
    --prior-a <frozen prior a> --prior-b <frozen prior b> \
    --direction <increase|decrease from the frozen prediction> \
    --bf-threshold <frozen bf-threshold> --json
```

Take `bf10`, `bf01`, and `verdict` verbatim.

If the frozen plan names a test with **no script available**, stop and report
the missing tool — do not eyeball it.

Write into the note's `result:` frontmatter: `effect` (point estimate +
interval), `p_value` (`frequentist`) or `bayes_factor` (`bayesian` — the
script's `bf10`), and `verdict`, mapping the script's short string to the
enum (same mapping regardless of which script produced it):

| script | `result.verdict` |
|---|---|
| `apoya` | `apoyada` |
| `refuta` | `refutada` |
| `inconcluso` | `inconclusa` |

(`evidencia_mixta` only if the frozen plan named >1 primary metric and they
conflict.) Add a `## Resultado` section (new content, not a frozen edit) with the
exact command run, the full script output, and the mapped verdict.
```

- [ ] **Step 2: Add a Common mistakes entry**

Find:
```
- **Reasoning about significance in prose.** Call the script; copy its numbers and
  verdict.
```

Replace with:
```
- **Reasoning about significance in prose.** Call the script; copy its numbers and
  verdict.
- **Calling the wrong script for the frozen `analysis_plan`.** `frequentist` ->
  `two_proportion_test.py`; `bayesian` -> `bayes_factor_proportions.py`. The
  frozen field decides which one runs, not which one is more familiar.
```

- [ ] **Step 3: Update the "Related" section**

Find:
```
## Related

- `Scripts/experiments/E-XXXX/` — the self-contained experiment code directory
  (scripts + frozen `E-XXXX.data.json` + `E-XXXX.deps.txt`); also the transfer
  bundle for external runtimes (step 2).
- `${CLAUDE_PLUGIN_ROOT}/scripts/analysis/two_proportion_test.py` — the mechanical test for step 5.
  `--help` documents the three-way verdict and thresholds.
```

Replace with:
```
## Related

- `Scripts/experiments/E-XXXX/` — the self-contained experiment code directory
  (scripts + frozen `E-XXXX.data.json` + `E-XXXX.deps.txt`); also the transfer
  bundle for external runtimes (step 2).
- `${CLAUDE_PLUGIN_ROOT}/scripts/analysis/two_proportion_test.py` — the
  `frequentist` mechanical test for step 5. `--help` documents the three-way
  verdict and thresholds.
- `${CLAUDE_PLUGIN_ROOT}/scripts/analysis/bayes_factor_proportions.py` — the
  `bayesian` mechanical test for step 5. `--help` documents the Bayes factor
  and verdict thresholds.
```

- [ ] **Step 4: Verify**

Run:
```bash
grep -n "analysis_plan" skills/run-experiment/SKILL.md
grep -n "bayes_factor_proportions.py" skills/run-experiment/SKILL.md
```
Expected: step 5, the new Common mistakes bullet, and the Related section all
reference `analysis_plan` / `bayes_factor_proportions.py` consistently with
Task 2's actual script name and Task 4's frontmatter field name.

- [ ] **Step 5: Commit**

```bash
git add skills/run-experiment/SKILL.md
git commit -m "Branch run-experiment step 5 on the frozen analysis_plan"
```

---

## Self-Review

**Spec coverage:**
- §A (tier determination) → Task 4 step 1 (step "1. Determine tier and
  analysis plan", sub-step a) + Task 3 step 1 (`completo_cost_threshold`).
- §B (sample-size script) → Task 1.
- §C (analysis-plan choice + Bayes-factor script) → Task 2, Task 3 step 2,
  Task 4 step 1 (sub-step c).
- §D (confidence semantics) → Task 4 step 1 (sub-step d) + Common mistakes.
- §E (mixed bayesian+linea_publicacion gap) → Task 4 step 1 (sub-step e) +
  Common mistakes.
- §F (run-experiment touch) → Task 5.
- File-level footprint → every listed file has a task; `update-confidence`
  and `hypothesis-template.md` correctly have none (spec: no changes needed).

**Placeholder scan:** no TBD/TODO; every code block is complete, runnable
source, not a sketch.

**Type/interface consistency:** `sample_size.py`'s `required_n()` dict keys
(`h`, `alpha`, `power`, `z_alpha2`, `z_power`, `n_per_group`, `n_total`) are
used identically in Task 1's tests and CLI. `bayes_factor_proportions.py`'s
`bayes_factor()` dict keys (`p1`, `p2`, `bf10`, `bf01`, ...) are used
identically in its tests, `classify()`, and the CLI's `RESULT_JSON`. Frontmatter
field names (`completo_cost_threshold`, `analysis_plan`) match exactly
between Task 3 (definition), Task 4 (consumption in `preregister-experiment`),
and Task 5 (consumption in `run-experiment`).

## Execution Handoff

Plan complete and saved to
`docs/superpowers/plans/2026-09-14-preregister-experiment-completo-tier.md`.
Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
