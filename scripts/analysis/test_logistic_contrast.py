"""Tests for logistic_contrast.py (invented, seeded data)."""

import contextlib
import io
import json
import math
import random
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import logistic_contrast as lc  # noqa: E402


def simulate(n, b0, b_a, b_b, seed, a_levels=(4, 12), b_levels=(15, 50)):
    """Rows (a, b, noise) and 0/1 outcomes from a known logistic model."""
    rng = random.Random(seed)
    rows, y = [], []
    for _ in range(n):
        a = rng.choice(a_levels)
        b = rng.choice(b_levels)
        z = rng.gauss(0, 1)
        p = 1 / (1 + math.exp(-(b0 + b_a * (a - 8) + b_b * (b - 32) + 0.0 * z)))
        rows.append([float(a), float(b), z])
        y.append(1 if rng.random() < p else 0)
    return rows, y


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = lc.main(argv)
    return code, out.getvalue(), err.getvalue()


class Fit(unittest.TestCase):
    def test_score_equations_vanish_at_the_solution(self):
        rows, y = simulate(600, 0.3, -0.25, -0.01, seed=1)
        x, _, _ = lc._standardise(rows)
        beta, ok = lc.fit_logistic(x, y, ridge=0.0)
        self.assertTrue(ok)
        for j in range(len(beta)):
            score = sum((yi - lc._sigmoid(sum(b * v for b, v in zip(beta, xi)))) * xi[j]
                        for xi, yi in zip(x, y))
            self.assertAlmostEqual(score, 0.0, places=5)

    def test_recovers_known_coefficients_with_a_large_sample(self):
        rows, y = simulate(20000, 0.2, -0.20, -0.02, seed=2)
        x, _, sds = lc._standardise(rows)
        beta, ok = lc.fit_logistic(x, y, ridge=0.0)
        self.assertTrue(ok)
        self.assertAlmostEqual(beta[1] / sds[0], -0.20, delta=0.02)
        self.assertAlmostEqual(beta[2] / sds[1], -0.02, delta=0.004)

    def test_single_binary_predictor_matches_the_log_odds_ratio_of_the_counts(self):
        # 40/50 successes at x = 1, 20/50 at x = 0 -> slope = log(4/1) - log(2/3)... computed from counts
        y = [1] * 40 + [0] * 10 + [1] * 20 + [0] * 30
        rows = [[1.0]] * 50 + [[0.0]] * 50
        x, _, sds = lc._standardise(rows)
        beta, ok = lc.fit_logistic(x, y, ridge=0.0)
        self.assertTrue(ok)
        expected = math.log((40 / 10) / (20 / 30))
        self.assertAlmostEqual(beta[1] / sds[0], expected, places=6)

    def test_complete_separation_stays_finite_with_the_ridge(self):
        y = [1] * 30 + [0] * 30
        rows = [[1.0, float(i % 2)] for i in range(30)] + [[0.0, float(i % 2)] for i in range(30)]
        x, _, _ = lc._standardise(rows)
        beta, ok = lc.fit_logistic(x, y, ridge=0.01)
        self.assertTrue(ok)
        self.assertTrue(all(math.isfinite(b) and abs(b) < 50 for b in beta))


class Contrast(unittest.TestCase):
    NAMES = ["a", "b", "noise"]

    def go(self, rows, y, **kw):
        return lc.contrast(rows, y, self.NAMES, ("a", 4, 12), ("b", 15, 50), 0.05, n_boot=200, seed=7, **kw)

    def test_a_dominant_effect_gives_a_positive_d_with_an_interval_above_zero(self):
        rows, y = simulate(1500, 0.0, -0.25, -0.005, seed=3)
        s = self.go(rows, y)
        self.assertLess(s["move_a"], -1.0)           # about -0.25 * 8 = -2
        self.assertLess(abs(s["move_b"]), 0.5)        # about -0.005 * 35 = -0.175
        self.assertGreater(s["effect"], 1.0)
        self.assertGreater(s["ci"][0], 0.0)
        self.assertTrue(s["significant"])
        self.assertEqual(lc.classify(s, "decrease", 0.5, 0.0), "apoya")

    def test_the_rival_effect_gives_a_negative_d_and_refutes(self):
        rows, y = simulate(1500, 0.0, -0.01, -0.05, seed=4)   # b move about -1.75, a move about -0.08
        s = self.go(rows, y)
        self.assertLess(s["effect"], 0.0)
        self.assertEqual(lc.classify(s, "decrease", 0.5, 0.0), "refuta")

    def test_equal_effects_are_not_significant(self):
        rows, y = simulate(1500, 0.0, -0.125, -0.0357142857, seed=5)   # both moves about -1
        s = self.go(rows, y)
        self.assertFalse(s["significant"])
        self.assertEqual(lc.classify(s, None, None, 0.0), "no_significativo")

    def test_same_seed_same_interval_and_other_seed_differs(self):
        rows, y = simulate(400, 0.0, -0.15, -0.01, seed=6)
        s1, s2 = self.go(rows, y), self.go(rows, y)
        self.assertEqual(s1["ci"], s2["ci"])
        s3 = lc.contrast(rows, y, self.NAMES, ("a", 4, 12), ("b", 15, 50), 0.05, n_boot=200, seed=8)
        self.assertNotEqual(s1["ci"], s3["ci"])
        self.assertEqual(s1["effect"], s3["effect"])

    def test_strata_resampling_keeps_every_cell_in_each_resample(self):
        rows, y = simulate(400, 0.0, -0.15, -0.01, seed=9)
        strata = [f"{int(r[0])}-{int(r[1])}" for r in rows]
        s = self.go(rows, y, strata=strata)
        self.assertTrue(s["strata"])
        self.assertEqual(s["boot_failed"], 0)

    def test_the_sign_of_move_a_is_required_when_declared(self):
        s = {"significant": True, "effect": 1.0, "move_a": 2.0}
        self.assertEqual(lc.classify(s, "decrease", 0.5, 0.0), "refuta")
        self.assertEqual(lc.classify(s, "increase", 0.5, 0.0), "apoya")

    def test_classify_has_an_inconclusive_band(self):
        s = {"significant": True, "effect": 0.3, "move_a": -2.0}
        self.assertEqual(lc.classify(s, "decrease", 0.5, 0.1), "inconcluso")
        s["effect"] = 0.05
        self.assertEqual(lc.classify(s, "decrease", 0.5, 0.1), "refuta")


class Validation(unittest.TestCase):
    def setUp(self):
        self.rows, self.y = simulate(100, 0.0, -0.1, -0.01, seed=10)

    def call(self, **kw):
        args = dict(rows=self.rows, y=self.y, names=["a", "b", "noise"], a=("a", 4, 12), b=("b", 15, 50), alpha=0.05)
        args.update(kw)
        return lc.contrast(args.pop("rows"), args.pop("y"), args.pop("names"), args.pop("a"), args.pop("b"),
                           args.pop("alpha"), n_boot=100, **args)

    def test_rejections(self):
        for kw in ({"alpha": 0.0}, {"alpha": 1.0}, {"a": ("zzz", 4, 12)}, {"b": ("a", 4, 12)},
                   {"y": [0] * 100}, {"y": [2] * 100}, {"rows": self.rows[:5], "y": self.y[:5]}):
            with self.assertRaises(ValueError, msg=str(kw)):
                self.call(**kw)

    def test_a_constant_predictor_is_rejected(self):
        rows = [[r[0], 3.0, r[2]] for r in self.rows]
        with self.assertRaises(ValueError):
            self.call(rows=rows)


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        rows, y = simulate(600, 0.0, -0.25, -0.005, seed=11)
        self.csv = Path(self.tmp.name) / "runs.csv"
        lines = ["success,a,b,noise,cell"]
        for r, yi in zip(rows, y):
            lines.append(f"{yi},{r[0]},{r[1]},{r[2]:.4f},{int(r[0])}-{int(r[1])}")
        self.csv.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def argv(self, *extra):
        return ["--data", str(self.csv), "--outcome", "success", "--predictors", "a,b,noise",
                "--a", "a:4:12", "--b", "b:15:50", "--alpha", "0.05", "--n-boot", "200", "--seed", "1", *extra]

    def test_json_output_and_verdict(self):
        code, out, _ = run_cli(self.argv("--direction-a", "decrease", "--min-effect", "0.5", "--strata", "cell", "--json"))
        self.assertEqual(code, 0)
        payload = json.loads(out.split("RESULT_JSON: ")[1])
        self.assertEqual(payload["verdict"], "apoya")
        self.assertIn("effect", payload)
        self.assertIn("se", payload)

    def test_without_min_effect_no_support_is_inferred(self):
        code, out, _ = run_cli(self.argv())
        self.assertEqual(code, 0)
        self.assertIn("do NOT infer support", out)

    def test_bad_input_exits_2(self):
        for extra in (["--a", "a:4"], ["--a", "a:4:4"], ["--predictors", "a,zzz"], ["--ridge", "-1"]):
            code, _, err = run_cli(self.argv(*extra))      # the last occurrence of a flag wins
            self.assertEqual(code, 2, msg=str(extra))
            self.assertIn("error:", err)

    def test_missing_file_exits_2(self):
        argv = self.argv()
        argv[1] = str(Path(self.tmp.name) / "nope.csv")
        code, _, _ = run_cli(argv)
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
