"""check_sota: the report says how many values of a synthesis were read off a
plot (never checkable against text), so a comparison resting on plot readings
shows it (invented text only)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_sota as cs  # noqa: E402

TEXT = """## Tabla comparativa

The invented scheme reaches ≈ 3.1 (leído de la Figura 2, no literal) on toy GPUs.

The other invented scheme reaches ≈ 40 % (leído de la Figura 5, no literal) too.
"""


class PlotReadings(unittest.TestCase):
    def test_plot_readings_are_counted(self):
        report = cs.check("/nonexistent-vault", None, TEXT)
        self.assertEqual(report["plot_readings"], 2)

    def test_plot_readings_in_a_table_cell_count_too(self):
        table = ("## Tabla comparativa\n\n| método | speedup | fuente |\n|---|---|---|\n"
                 "| A | ≈ 3.1 (leído de la Figura 2, no literal) | P-0001 Figura 2 |\n")
        self.assertEqual(cs.check("/nonexistent-vault", None, table)["plot_readings"], 1)


if __name__ == "__main__":
    unittest.main()
