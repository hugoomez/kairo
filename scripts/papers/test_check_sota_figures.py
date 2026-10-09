"""check_sota: how a figure is written never decides whether it is checked
(2026-10-09 audit, H2/H3). Powers of ten are one value with their exponent,
code parameters are a tuple, a `(%)` column is a percentage, a count with a
unit is never a year, uncertainty and LaTeX spacing normalise, and a small
integer bound to a parameter is a figure. Invented text only."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_sota as cs  # noqa: E402
import ficha  # noqa: E402


class PowersOfTen(unittest.TestCase):
    def test_every_way_of_writing_a_power_of_ten_is_one_value(self):
        cases = {
            r"- The toy rate falls to $10^{-6}$ — P-0101 §3.2": ["1e-6"],
            r"- The toy rate falls to 10^{-6} — P-0101 §3.2": ["1e-6"],
            r"- The toy rate is $2.5\times 10^{-3}$ — P-0101 §3.2": ["2.5e-3"],
            r"- The toy rate is 2.5 \cdot 10^{-3} — P-0101 §3.2": ["2.5e-3"],
            "- The toy rate is 2.5×10⁻³ — P-0101 §3.2": ["2.5e-3"],
            "- The toy rate is 1e-6 — P-0101 §3.2": ["1e-6"],
            r"- The toy rate is \num{2.5e-3} — P-0101 §3.2": ["2.5e-3"],
            r"- The toy rate is $\sim 10^{-3}$ — P-0101 §3.2": ["1e-3"],
            r"- The toy rate stays < 10^{-4} — P-0101 §3.2": ["1e-4"],
        }
        for sentence, want in cases.items():
            self.assertEqual(cs.numbers(sentence), want, sentence)

    def test_the_exponent_matters(self):
        src = [r"The invented decoder drops the rate to $10^{-7}$ after 10 rounds."]
        self.assertFalse(cs.number_in("1e-6", src))
        self.assertTrue(cs.number_in("1e-7", src))
        self.assertFalse(cs.number_in("1e-6", ["We ran 10 toy seeds with 6 codes."]))

    def test_the_mantissa_matters_and_is_not_checked_alone(self):
        src = [r"A toy rate of $2.5\times 10^{-3}$ is reached."]
        self.assertTrue(cs.number_in("2.5e-3", src))
        self.assertFalse(cs.number_in("2.5e-4", src))
        self.assertFalse(cs.number_in("2.5e-3", ["2.5 toy units over 10 runs"]))

    def test_the_source_may_write_it_any_way(self):
        for t in ("falls to 10⁻⁶ here", "falls to 1E-6 here", r"falls to \num{1e-6} here",
                  "falls to 10^-6 here", "falls to 0.000001 here"):
            self.assertTrue(cs.number_in("1e-6", [t]), t)

    def test_ficha_shares_the_whole_number_and_value_matching(self):
        self.assertEqual(ficha.missing_figures("The toy score is 0.8.", "The toy score is 10.85 on average."),
                         ["0.8"])
        self.assertEqual(ficha.missing_figures("The toy rate is 10^{-6}.", r"The toy rate is $10^{-6}$ here."), [])
        self.assertEqual(ficha.missing_figures("The toy rate is 10^{-6}.", "The toy rate is 10 per 6 runs."),
                         ["1e-6"])


class CodeParametersAndColumns(unittest.TestCase):
    def test_code_parameters_are_a_tuple_in_any_bracket_style(self):
        self.assertEqual(cs.numbers("- A toy [[144,12,12]] code — P-0101 §3.2"), ["[[144,12,12]]"])
        self.assertEqual(cs.numbers(r"- A toy $\llbracket 144, 12, 12 \rrbracket$ code — P-0101 §3.2"),
                         ["[[144,12,12]]"])
        self.assertEqual(cs.numbers("- A toy [7, 1, 3] code — P-0101 §3.2"), ["[7,1,3]"])
        for t in ("the invented [[144, 12, 12]] code", r"the $[[144,12,12]]$ code",
                  r"the $\llbracket 144,12,12\rrbracket$ code"):
            self.assertTrue(cs.number_in("[[144,12,12]]", [t]), t)
        self.assertFalse(cs.number_in("[[144,12,12]]", ["144 qubits, 12 logical, distance 12"]))
        self.assertFalse(cs.number_in("[[144,12,12]]", ["the [[144,12,18]] code"]))

    def test_a_cell_under_a_percent_header_is_a_percentage(self):
        src = ["| method | accuracy (%) |\n|---|---|\n| toy | 52.1 |"]
        self.assertTrue(cs.number_in("52.1%", src))
        self.assertFalse(cs.number_in("52.1%", ["| method | time (s) |\n|---|---|\n| toy | 52.1 |"]))

    def test_a_count_with_a_unit_is_not_a_year(self):
        self.assertEqual(cs.numbers("- Scaled to 2048 GPUs and 1024 nodes — P-0101 §3.2"),
                         ["2048 GPUs", "1024 nodes"])
        self.assertFalse(cs.number_in("2048 GPUs", ["Published in 2048 by the toy group."]))
        self.assertTrue(cs.number_in("2048 GPUs", ["We scale to 2,048 GPUs."]))

    def test_uncertainty_and_latex_spacing_normalise(self):
        self.assertEqual(cs.numbers("- A toy value of 2.5(3) — P-0101 §3.2"), ["2.5"])
        self.assertEqual(cs.numbers(r"- A toy rate of 0.7\,\% — P-0101 §3.2"), [r"0.7\,\%"])
        self.assertTrue(cs.number_in(r"0.7\,\%", ["a toy rate of 0.7 % here"]))
        self.assertTrue(cs.number_in("0.7%", [r"a toy rate of 0.7~\% here"]))
        self.assertTrue(cs.number_in("2.5", ["the toy value 2.5(3) here"]))
        self.assertTrue(cs.number_in("52.1%", [r"a toy score of 52.1(4)\% here"]))

    def test_small_integers_bound_to_a_parameter_are_checked(self):
        self.assertEqual(cs.numbers("- With TP=8 and d = 7 at distance 9 — P-0101 §3.2"),
                         ["TP = 8", "d = 7", "distance 9"])
        self.assertTrue(cs.number_in("d = 7", ["we use $d=7$ toy codes"]))
        self.assertTrue(cs.number_in("d = 7", ["at distance 7 the toy code"]))
        self.assertFalse(cs.number_in("d = 7", ["we use d = 5 and 7 rounds"]))
        self.assertTrue(cs.number_in("TP = 8", ["tensor parallel TP = 8 here"]))
        self.assertFalse(cs.number_in("k = 4", ["we use k = 3"]))


if __name__ == "__main__":
    unittest.main()
