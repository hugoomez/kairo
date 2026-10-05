"""Tests for check_sota.py (invented vault, no network)."""

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_sota as cs  # noqa: E402

PAPER = """---
id: P-0101
title: Toy decoders
projects: [PROJ-101]
fulltext: full
---

## Referencia

Doe (2031). Toy decoders.

## Resumen

> Fuente: invented

A toy decoder reaches a made-up threshold of 1.1% on invented codes.

## Texto completo

> Fuente: invented

### 3 Results

#### 3.2 Threshold

The toy decoder reaches a threshold of 1.1% at distance 15, using 1,024 shots per point.

**Table 2:** Logical error rates.

| d | rate |
| 15 | 0.0042 |
"""

OTHER = PAPER.replace("P-0101", "P-0102").replace("PROJ-101", "PROJ-999")
CONFLICT = PAPER.replace("P-0101", "P-0103").replace("fulltext: full", "fulltext: full\nresolution_status: mismatch")

SOTA = """---
last_updated: 2031-06-01
---

## Lo establecido vs. lo debatido
*as of 2031-06-01, 3 papers*

- The toy decoder reaches a threshold of 1.1% at distance 15 — P-0101 §3.2
- Its logical error rate at d = 15 is 0.0042 — P-0101 Tabla 2
- The toy decoder reaches a threshold of 2.5% — P-0101 §3.2
- Thresholds were measured with 1024 shots — P-0101 §3.2
- Something stated about section nine — P-0101 §9
- A claim on another project's paper — P-0102 §3.2
- A claim on a conflicting reference — P-0103 §3.2
- A claim on a paper that does not exist — P-0999 §1
- Introduced in 2019 by the toy paper — P-0101
"""


class CheckSota(unittest.TestCase):
    def setUp(self):
        self.vault = Path(tempfile.mkdtemp())
        papers = self.vault / "Papers"
        papers.mkdir()
        for name, text in (("P-0101 toy.md", PAPER), ("P-0102 other.md", OTHER), ("P-0103 conflict.md", CONFLICT)):
            (papers / name).write_text(text, encoding="utf-8")
        self.p = self.vault / "Projects" / "toy"
        self.p.mkdir(parents=True)
        (self.p / "_hub.md").write_text("---\nid: PROJ-101\n---\n", encoding="utf-8")
        (self.p / "Estado-del-arte.md").write_text(SOTA, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    def run_cli(self, *extra):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cs.main(["--vault", str(self.vault), "--project-dir", "Projects/toy", *extra])
        return code, json.loads(buf.getvalue())

    def reasons(self, out):
        return [(p["citation"], p.get("number"), p["reason"]) for p in out["problems"]]

    def test_each_kind_of_problem_is_found_and_nothing_else(self):
        code, out = self.run_cli()
        self.assertEqual(code, 3)
        r = self.reasons(out)
        self.assertIn(("P-0101 §3.2", "2.5%", "la cifra «2.5%» no aparece en el texto citado"), r)
        self.assertTrue(any(c == "P-0101 §9" and "no señala texto" in why for c, _, why in r))
        self.assertTrue(any(c == "P-0102 §3.2" and "no es un paper del proyecto" in why for c, _, why in r))
        self.assertTrue(any(c == "P-0103 §3.2" and "en conflicto" in why for c, _, why in r))
        self.assertTrue(any(c == "P-0999 §1" and "no existe" in why for c, _, why in r))
        # correct numbers (1.1%, 15, 0.0042 from a table, 1024 vs 1,024) and the year pass
        flagged_numbers = {n for _, n, _ in r if n}
        self.assertEqual(flagged_numbers, {"2.5%"})
        self.assertEqual(out["without_locator"], ["P-0101"])

    def test_write_marks_in_place_and_removes_nothing(self):
        self.run_cli("--write")
        text = (self.p / "Estado-del-arte.md").read_text(encoding="utf-8")
        self.assertIn("— P-0101 §3.2 «⚠ la cifra «2.5%» no aparece en el texto citado»", text)
        for line in SOTA.splitlines():
            if line.startswith("- "):
                self.assertIn(line.split(" — ")[0], text)

    def test_a_table_locator_reaches_the_rows_not_only_the_caption(self):
        units = cs.resolve_citation(str(self.vault), "P-0101", "Tabla 2")["units"]
        self.assertTrue(any("| 15 | 0.0042 |" in u for u in units), units)

    def test_a_clean_document_passes(self):
        (self.p / "Estado-del-arte.md").write_text(
            "## X\n\n- The toy decoder reaches a threshold of 1.1% — P-0101 §3.2\n", encoding="utf-8")
        code, out = self.run_cli()
        self.assertEqual((code, out["problems"], out["numbers_checked"]), (0, [], 1))


if __name__ == "__main__":
    unittest.main()
