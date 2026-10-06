"""Tests for mean_difference.py (invented measurements)."""

import contextlib
import io
import json
import math
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mean_difference as mdt  # noqa: E402


def t_pdf(x, df):
    return math.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2)) / math.sqrt(df * math.pi) \
        * (1 + x * x / df) ** (-(df + 1) / 2)


def simpson_tail(t, df, n=20000):
    """An independent two-sided p-value: 1 - ∫_{-|t|}^{|t|} pdf, by Simpson's rule."""
    a, b = -abs(t), abs(t)
    h = (b - a) / n
    s = t_pdf(a, df) + t_pdf(b, df) + sum((4 if i % 2 else 2) * t_pdf(a + i * h, df) for i in range(1, n))
    return 1 - s * h / 3


class TDistribution(unittest.TestCase):
    def test_quantiles_match_the_printed_tables(self):
        for df, q in ((1, 12.706204736), (5, 2.570581836), (10, 2.228138852), (30, 2.042272456)):
            self.assertAlmostEqual(mdt.t_quantile(0.975, df), q, places=6)

    def test_p_values_match_an_independent_integration(self):
        for t, df in ((1.8974, 5.88), (0.5, 3.0), (2.5, 12.0), (4.0, 2.3)):
            self.assertAlmostEqual(mdt.t_two_sided_p(t, df), simpson_tail(t, df), places=6)


class Welch(unittest.TestCase):
    def test_textbook_example(self):
        s = mdt.mean_difference([1, 2, 3, 4, 5], [2, 4, 6, 8, 10], 0.05)
        self.assertAlmostEqual(s["effect"], -3.0)
        self.assertAlmostEqual(s["se"], math.sqrt(2.5))
        self.assertAlmostEqual(s["t"], -1.8973665961, places=8)
        self.assertAlmostEqual(s["df"], 6.25 / 1.0625, places=8)
        self.assertAlmostEqual(s["p_value"], simpson_tail(s["t"], s["df"]), places=6)
        self.assertFalse(s["significant"])
        self.assertLess(s["ci"][0], -3.0)
        self.assertGreater(s["ci"][1], 0.0)           # the interval spans 0

    def test_paired_seeds(self):
        tr = [0.912, 0.905, 0.921, 0.917, 0.909]
        co = [0.901, 0.899, 0.910, 0.905, 0.900]
        s = mdt.mean_difference(tr, co, 0.05, paired=True)
        diffs = [a - b for a, b in zip(tr, co)]
        self.assertAlmostEqual(s["effect"], sum(diffs) / 5)
        self.assertEqual(s["df"], 4.0)
        self.assertTrue(s["significant"])

    def test_log_scale_reports_a_ratio(self):
        s = mdt.mean_difference([2.0, 2.2, 1.9, 2.1], [1.0, 1.1, 0.95, 1.05], 0.05, log=True)
        self.assertAlmostEqual(s["ratio"], 2.0, delta=0.05)
        self.assertLess(s["ratio_ci"][0], s["ratio"])
        with self.assertRaises(ValueError):
            mdt.mean_difference([1.0, -1.0], [1.0, 2.0], 0.05, log=True)

    def test_bad_input(self):
        for args in (([1.0], [1.0, 2.0], 0.05, False), ([1.0, 2.0], [1.0], 0.05, True),
                     ([1.0, 2.0], [1.0, 2.0], 1.5, False)):
            with self.assertRaises(ValueError):
                mdt.mean_difference(*args[:3], paired=args[3])


class Verdict(unittest.TestCase):
    def cli(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = mdt.main(list(args))
        line = next((x for x in buf.getvalue().splitlines() if x.startswith("RESULT_JSON: ")), "")
        return code, json.loads(line.removeprefix("RESULT_JSON: ")) if line else None

    def test_three_way_rule(self):
        tr, co = "0.912,0.905,0.921,0.917,0.909", "0.901,0.899,0.910,0.905,0.900"
        _, r = self.cli("--treatment", tr, "--control", co, "--paired", "--alpha", "0.05",
                        "--direction", "increase", "--min-effect", "0.005", "--json")
        self.assertEqual(r["verdict"], "apoya")
        _, r = self.cli("--treatment", tr, "--control", co, "--paired", "--alpha", "0.05",
                        "--direction", "increase", "--min-effect", "0.05", "--json")
        self.assertEqual(r["verdict"], "inconcluso")             # significant, but smaller than T_apoyo
        _, r = self.cli("--treatment", tr, "--control", co, "--paired", "--alpha", "0.05",
                        "--direction", "decrease", "--min-effect", "0.005", "--json")
        self.assertEqual(r["verdict"], "refuta")                 # wrong direction

    def test_speedup_thresholds_are_ratios(self):
        _, r = self.cli("--treatment", "2.0,2.2,1.9,2.1", "--control", "1.0,1.1,0.95,1.05", "--log",
                        "--alpha", "0.05", "--direction", "increase", "--min-effect", "1.5", "--json")
        self.assertEqual(r["verdict"], "apoya")
        _, r = self.cli("--treatment", "2.0,2.2,1.9,2.1", "--control", "1.0,1.1,0.95,1.05", "--log",
                        "--alpha", "0.05", "--direction", "increase", "--min-effect", "2.5", "--json")
        self.assertEqual(r["verdict"], "inconcluso")

    def test_statistical_only_without_a_rule(self):
        code, r = self.cli("--treatment", "1,2,3", "--control", "1,2,3", "--alpha", "0.05", "--json")
        self.assertEqual((code, r["verdict"]), (0, "no_significativo"))

    def test_invalid_input_exits_2(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.cli("--treatment", "1,x", "--control", "1,2", "--alpha", "0.05")[0], 2)
            self.assertEqual(self.cli("--treatment", "1,2", "--control", "1,2", "--alpha", "0.05",
                                      "--log", "--min-effect", "-1")[0], 2)


if __name__ == "__main__":
    unittest.main()
