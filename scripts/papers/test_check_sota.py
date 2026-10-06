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

    def write(self, text):
        (self.p / "Estado-del-arte.md").write_text(text, encoding="utf-8")

    def test_tables_are_checked_row_by_row_and_column_by_paper(self):
        self.write("## Comparativa\n\n"
                   "| sistema | umbral | fuente |\n|---|---|---|\n"
                   "| toy | 1.1% | P-0101 §3.2 |\n"
                   "| toy | 7.7% | P-0101 §3.2 |\n\n"
                   "## Matriz de conceptos\n\n"
                   "| concepto | P-0101 |\n|---|---|\n"
                   "| umbral | 1.1% a d = 15 |\n"
                   "| disparos | 9,999 por punto |\n")
        code, out = self.run_cli()
        self.assertEqual(code, 3)
        flagged = {p.get("number") for p in out["problems"]}
        self.assertEqual(flagged, {"7.7%", "9,999"})
        self.assertEqual(out["table_rows_checked"], 4)
        self.assertEqual(out["without_locator"], ["P-0101"])       # matrix cells carry no locator

    def test_a_multiplier_is_checked_whatever_its_size(self):
        paper = (self.vault / "Papers" / "P-0101 toy.md")
        paper.write_text(PAPER.replace("using 1,024 shots", "a 3x speedup, using 1,024 shots"), encoding="utf-8")
        self.write("## X\n\n- A 3× speedup over the baseline — P-0101 §3.2\n"
                   "- A 4× speedup over the baseline — P-0101 §3.2\n")
        code, out = self.run_cli()
        self.assertEqual([p.get("number") for p in out["problems"]], ["4×"])

    def test_numbers_with_a_unit_suffix_are_checked_whatever_their_size(self):
        paper = (self.vault / "Papers" / "P-0101 toy.md")
        paper.write_text(PAPER.replace("using 1,024 shots", "with a 530B-parameter toy on 80GB devices, using 1,024 shots"),
                         encoding="utf-8")
        self.write("## X\n\n- A 530B toy trained on 80 GB devices — P-0101 §3.2\n"
                   "- A 175B toy — P-0101 §3.2\n- An 8B toy — P-0101 §3.2\n")
        code, out = self.run_cli()
        self.assertEqual(sorted(p.get("number") for p in out["problems"]), ["175B", "8B"])

    def test_a_spanish_decimal_comma_is_the_same_number(self):
        self.write("## X\n\n- El decodificador alcanza un umbral del 1,1% a distancia 15 — P-0101 §3.2\n"
                   "- Y del 2,5% en otro caso — P-0101 §3.2\n")
        code, out = self.run_cli()
        self.assertEqual([p.get("number") for p in out["problems"]], ["2,5%"])

    def test_a_section_locator_does_not_borrow_text_that_only_mentions_it(self):
        paper = (self.vault / "Papers" / "P-0101 toy.md")
        paper.write_text(PAPER + "\n### 5 Discussion\n\nAs shown in Section 3, a rate of 0.77 is possible elsewhere.\n",
                         encoding="utf-8")
        self.write("## X\n\n- A rate of 0.77 — P-0101 §3\n")
        code, out = self.run_cli()
        self.assertEqual([p.get("number") for p in out["problems"]], ["0.77"])
        # with no structural §3 at all, an inline mention is still what the locator reaches
        units = cs.resolve_citation(str(self.vault), "P-0101", "§7")["units"]
        self.assertEqual(units, [])

    def test_the_support_packet_pairs_each_sentence_with_its_source_text(self):
        packet = self.vault / "packet.md"
        code, out = self.run_cli("--packet", str(packet), "--section", "Lo establecido vs. lo debatido")
        self.assertEqual(code, 0, out)
        text = packet.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Paquete de verificación"))
        self.assertIn("- Alcance: section:Lo establecido vs. lo debatido", text)
        self.assertIn("#### Afirmación 1 (verbatim de la nota)", text)
        self.assertIn("The toy decoder reaches a threshold of 1.1% at distance 15, using 1,024 shots", text)
        self.assertEqual(out["assertions"], 9)
        code, out = self.run_cli("--packet", str(packet), "--section", "No such section")
        self.assertEqual(code, 2)

    def test_a_large_packet_is_split_into_parts_that_cover_every_sentence(self):
        packet = self.vault / "packet.md"
        code, out = self.run_cli("--packet", str(packet), "--section", "Lo establecido vs. lo debatido",
                                 "--max-chars", "1500")
        self.assertEqual(code, 0, out)
        self.assertGreater(len(out["packets"]), 1)
        self.assertEqual(out["packet"], str(packet))
        total = 0
        for i, p in enumerate(out["packets"], 1):
            text = Path(p).read_text(encoding="utf-8")
            self.assertTrue(text.startswith("# Paquete de verificación"))
            self.assertIn(f"- Parte: {i} de {len(out['packets'])}", text)
            total += text.count("#### Afirmación ")
        self.assertEqual((total, out["assertions"]), (9, 9))


if __name__ == "__main__":
    unittest.main()
