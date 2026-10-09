"""check_sota: plot readings in tables are tied to their figure, uncited
sentences with figures or cross-paper comparisons are listed, and inference
marks are counted (2026-10-09 audit, H19). Invented text only."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_sota as cs  # noqa: E402
import ficha  # noqa: E402

VAULT = "/nonexistent-kairo-vault"


def plot_problems(report):
    return [p for p in report["problems"] if str(p.get("number", "")).startswith("≈")]


class PlotReadingsInTables(unittest.TestCase):
    def test_a_table_plot_reading_must_cite_its_figure_in_the_fuente_cell(self):
        bad = ("## Tabla comparativa\n\n| método | speedup | fuente |\n|---|---|---|\n"
               "| A | ≈ 3.1 (leído de la Figura 2, no literal) | P-0001 §4 |\n")
        self.assertEqual(len(plot_problems(cs.check(VAULT, None, bad))), 1)
        ok = bad.replace("P-0001 §4", "P-0001 Figura 2")
        self.assertEqual(plot_problems(cs.check(VAULT, None, ok)), [])

    def test_a_matrix_cell_plot_reading_must_name_its_figure(self):
        bad = ("## Matriz de conceptos\n\n| concepto | P-0001 |\n|---|---|\n"
               "| escalado | ≈ 0.8 (leído de la Figura 5, no literal) |\n")
        self.assertEqual(len(plot_problems(cs.check(VAULT, None, bad))), 1)
        ok = bad.replace("no literal) |", "no literal) — Figura 5 |")
        self.assertEqual(plot_problems(cs.check(VAULT, None, ok)), [])

    def test_ficha_exempts_only_the_value_read_off_the_plot(self):
        quote = "The invented scheme improves the toy rate by 30% over the baseline."
        claim = "Improves by 30% and stays ≈0.8 (leído de la Figura 3, no literal), with 45% fewer errors."
        self.assertEqual(ficha.missing_figures(claim, quote), ["45%"])


class UncitedSentences(unittest.TestCase):
    TEXT = ("---\nlast_updated: 2031-01-01\n---\n\n"
            "## ⚠️ Cobertura degradada\n\n- arXiv truncated at 300 of 1200 hits\n\n"
            "## Lo establecido vs. lo debatido\n*as of 2031-01-01, 12 papers*\n\n"
            "- The invented decoder reaches 1.1% on toy codes.\n"
            "- The invented decoder outperforms the toy baseline.\n"
            "- Both invented decoders use toy codes.\n"
            "- Combining both, the toy threshold should exceed the toy rate «(inferencia)» — P-0001 §3, P-0002 §4\n"
            "- A one-paper guess «(inferencia)» — P-0001 §3\n\n"
            "## Cobertura de lectura\n\n12 papers, 3 sections not read.\n\n"
            "### Búsqueda ejecutada — 2031-01-01\n\n- Identificados: arxiv 120 (total 340)\n")

    def test_uncited_sentences_with_figures_or_comparisons_are_listed(self):
        report = cs.check(VAULT, None, self.TEXT)
        self.assertEqual([s.split(" on ")[0].split(" the ")[0] for s in report["sin_cita"]],
                         ["- The invented decoder reaches 1.1%", "- The invented decoder outperforms"])

    def test_inference_marks_are_counted_and_a_one_paper_inference_warned(self):
        report = cs.check(VAULT, None, self.TEXT)
        self.assertEqual(report["inferencias"], 2)
        self.assertTrue(any("inferencia" in w and "un solo paper" in w for w in report["warnings"]))

    def test_the_packet_lists_the_uncited_sentences_of_its_section(self):
        packet, _ = cs.support_packet(VAULT, self.TEXT, "toy/Estado-del-arte.md", "Lo establecido vs. lo debatido")
        self.assertIn("## Frases sin cita", packet)
        self.assertIn("The invented decoder reaches 1.1% on toy codes.", packet)


if __name__ == "__main__":
    unittest.main()
