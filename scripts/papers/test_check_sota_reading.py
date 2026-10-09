"""check_sota reads the summarizers' `lectura:` lines from «Cobertura de
lectura» and warns when a gap rests on a paper read «solo ficha»
(2026-10-09 audit, H5). Invented text only."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_sota as cs  # noqa: E402

VAULT = "/nonexistent-kairo-vault"

TEXT = """## Huecos identificados
*as of 2031-01-01, 3 papers*

- Declarado: the toy decoder is untested on invented hardware — P-0401 §7
- No en estos papers: no toy scheme handles invented noise — P-0402 §2, P-0403 §5

## Cobertura de lectura

3 papers.

- lectura: P-0401 completo
- lectura: P-0402 ficha + §2, §4 abiertas
- lectura: P-0403 solo ficha
"""


class ReadingDepth(unittest.TestCase):
    def test_reading_lines_are_parsed(self):
        report = cs.check(VAULT, None, TEXT)
        self.assertEqual(report["lectura"], {"P-0401": "completo", "P-0402": "ficha + §2, §4 abiertas",
                                             "P-0403": "solo ficha"})

    def test_a_gap_resting_on_a_card_only_paper_is_warned(self):
        report = cs.check(VAULT, None, TEXT)
        warned = [w for w in report.get("warnings", []) if "solo ficha" in w]
        self.assertEqual(len(warned), 1, report.get("warnings"))
        self.assertIn("P-0403", warned[0])
        self.assertNotIn("P-0402", warned[0])

    def test_no_warning_when_every_gap_paper_was_opened(self):
        report = cs.check(VAULT, None, TEXT.replace("P-0403 solo ficha", "P-0403 completo"))
        self.assertFalse([w for w in report.get("warnings", []) if "solo ficha" in w])


if __name__ == "__main__":
    unittest.main()
