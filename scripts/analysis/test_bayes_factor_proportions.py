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
    def test_symmetric_when_a_equals_b(self):
        # log_beta_binom(x, n, a, a) == log_beta_binom(n - x, n, a, a) for
        # any shared shape a == b, since B(x+a, n-x+a) is symmetric in that
        # case -- an exact algebraic identity, independent of this script's
        # own derivation.
        for n, x, a in ((5, 2, 1.0), (10, 3, 2.0), (7, 0, 1.5)):
            self.assertAlmostEqual(
                log_beta_binom(x, n, a, a), log_beta_binom(n - x, n, a, a),
                places=9)


class TestBayesFactor(unittest.TestCase):
    def test_exact_hand_computed_case_small_n(self):
        # x1=1,n1=1,x2=0,n2=1, Beta(1,1) prior: BF10 = 1.5 exactly.
        # Derived independently: B(x+a,n-x+b) = B(2,1) = B(1,2) = 1/2 for
        # both groups; B(x1+x2+a, n1+n2-x1-x2+b) = B(2,2) = 1/6.
        # BF10 = (1/2 * 1/2) / (1/6) = (1/4)/(1/6) = 1.5.
        result = bayes_factor(1, 1, 0, 1, 1.0, 1.0)
        self.assertAlmostEqual(result["bf10"], 1.5, places=9)

    def test_exact_hand_computed_case_perfect_separation(self):
        # x1=2,n1=2 (100%), x2=0,n2=2 (0%), Beta(1,1) prior: BF10 = 10/3
        # exactly. Derived independently: B(3,1)=1/3, B(1,3)=1/3,
        # B(3,3)=1/30. BF10 = (1/3 * 1/3) / (1/30) = (1/9)/(1/30) = 10/3.
        result = bayes_factor(2, 2, 0, 2, 1.0, 1.0)
        self.assertAlmostEqual(result["bf10"], 10.0 / 3.0, places=9)

    def test_exact_hand_computed_case_identical_rates(self):
        # x1=1,n1=2 (50%), x2=1,n2=2 (50%), Beta(1,1) prior: BF10 = 5/6
        # exactly (slightly favors H0, the shared-rate model, as expected
        # when the two groups look identical). Derived independently:
        # B(2,2)=1/6 for each group, B(3,3)=1/30.
        # BF10 = (1/6 * 1/6) / (1/30) = (1/36)/(1/30) = 30/36 = 5/6.
        result = bayes_factor(1, 2, 1, 2, 1.0, 1.0)
        self.assertAlmostEqual(result["bf10"], 5.0 / 6.0, places=9)

    def test_bf01_is_reciprocal_of_bf10(self):
        result = bayes_factor(7, 10, 3, 10, 1.0, 1.0)
        self.assertAlmostEqual(result["bf10"] * result["bf01"], 1.0, places=9)

    def test_strong_divergence_favors_h1_more_than_weak_divergence(self):
        # A starker difference in observed rates should push BF10 higher
        # than a milder one, for matched sample sizes -- a relative,
        # formula-independent property (not a fabricated absolute
        # threshold).
        weak = bayes_factor(55, 100, 45, 100, 1.0, 1.0)["bf10"]
        strong = bayes_factor(90, 100, 10, 100, 1.0, 1.0)["bf10"]
        self.assertGreater(strong, weak)

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            bayes_factor(0, 0, 5, 10, 1.0, 1.0)
        with self.assertRaises(ValueError):
            bayes_factor(11, 10, 5, 10, 1.0, 1.0)
        with self.assertRaises(ValueError):
            bayes_factor(5, 10, 5, 10, 0.0, 1.0)

    def test_non_uniform_prior_matches_independent_gamma_computation(self):
        # Beta(0.5, 0.5) (Jeffreys prior) -- deliberately non-default, since the
        # previous test suite only ever used Beta(1,1), which hid a missing
        # 1/B(a,b) term (B(1,1)=1 masks it). Reference value computed
        # independently via math.gamma() directly (not math.lgamma, a different
        # code path than the implementation):
        #   B(x,y) = gamma(x)*gamma(y)/gamma(x+y)
        #   expected = B(7.5,3.5)*B(3.5,7.5) / (B(0.5,0.5)*B(10.5,10.5))
        #            = 1.7708978328173377
        result = bayes_factor(7, 10, 3, 10, 0.5, 0.5)
        self.assertAlmostEqual(result["bf10"], 1.7708978328173377, places=6)

    def test_extreme_divergence_does_not_raise(self):
        # log_bf10 exceeds math.exp's overflow threshold (~709.78) well before
        # this data pattern -- previously raised an uncaught OverflowError.
        result = bayes_factor(900, 1000, 100, 1000, 1.0, 1.0)
        self.assertEqual(result["bf10"], math.inf)
        self.assertEqual(result["bf01"], 0.0)

    def test_risk_difference_present(self):
        result = bayes_factor(7, 10, 3, 10, 1.0, 1.0)
        self.assertAlmostEqual(result["risk_difference"], 0.7 - 0.3, places=9)


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
