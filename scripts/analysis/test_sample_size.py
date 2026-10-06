import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sample_size as ss  # noqa: E402
from sample_size import cohens_h, required_n  # noqa: E402


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


class ContinuousMetric(unittest.TestCase):
    def test_cohen_1988_table_2_4_1(self):
        self.assertEqual(ss.required_n_d(0.5, 0.05, 0.80)["n_per_group"], 64)
        self.assertEqual(ss.required_n_d(0.8, 0.05, 0.80)["n_per_group"], 26)
        self.assertEqual(ss.required_n_d(0.2, 0.05, 0.80)["n_per_group"], 394)

    def test_paired_counts_pairs(self):
        r = ss.required_n_d(0.5, 0.05, 0.80, paired=True)
        self.assertEqual((r["n_pairs"], r["n_total"]), (34, 34))

    def test_rejects_zero_d(self):
        with self.assertRaises(ValueError):
            ss.required_n_d(0.0, 0.05, 0.8)


if __name__ == "__main__":
    unittest.main()
