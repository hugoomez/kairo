"""Locator resolution (verifier_packet.resolve_citation): parenthesised
equations, supplementary / appendix figures and tables, a bare appendix letter,
and named sections that match headings, not body text (2026-10-09 audit, H18).
Invented note text only."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "papers"))
import check_sota as cs  # noqa: E402
import verifier_packet as vp  # noqa: E402

NOTE = """---
id: P-0951
title: An invented toy study
fulltext: full
---

## Resumen

> Fuente: invented

An invented abstract.

## Texto completo

> Fuente: invented

### 1 Introduction

Our results show that the invented toy method is fast in the intro.

A study of toy decoders appears in a separate paragraph here.

### 2 Method

The invented loss is defined in Eq. (3) as a toy sum.

$$ L = \\sum_i x_i \\qquad (3) $$

See Appendix A for the toy proof sketch.

### 3 Results

The toy method reaches 0.91 on the invented benchmark.

**Ablations.** Removing the toy term lowers the score to 0.85.

**Table S1:** Toy hyper-parameters.

| lr | 0.01 |

Figure A2: Toy loss curve over invented epochs.

Figura S3: Otra curva inventada.

### Appendix A Toy proof

The invented proof sketch closes here.
"""


class Locators(unittest.TestCase):
    def setUp(self):
        self.vault = Path(tempfile.mkdtemp(prefix="kairo-loc-"))
        (self.vault / "Papers").mkdir()
        (self.vault / "Papers" / "P-0951 toy.md").write_text(NOTE, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    def units(self, span):
        return vp.resolve_citation(str(self.vault), "P-0951", span)["units"]

    def test_a_parenthesised_equation_resolves(self):
        for span in ("Eq. (3)", "Ec. (3)", "Eq. 3", "Ecuación (3)"):
            self.assertEqual(vp.locator_tokens(span), [("eq", "3")], span)
            units = self.units(span)
            self.assertTrue(any("defined in Eq. (3)" in u for u in units), (span, units))
            self.assertTrue(any("(3) $$" in u for u in units), (span, units))

    def test_supplementary_and_appendix_figures_and_tables_resolve(self):
        self.assertEqual(vp.locator_tokens("Tabla S1"), [("table", "S1")])
        self.assertEqual(vp.locator_tokens("Fig. A2"), [("fig", "A2")])
        self.assertEqual(vp.locator_tokens("Figura S3"), [("fig", "S3")])
        self.assertTrue(any("| lr | 0.01 |" in u for u in self.units("Tabla S1")))
        self.assertTrue(any("Toy loss curve" in u for u in self.units("Fig. A2")))
        self.assertTrue(any("Otra curva" in u for u in self.units("Figura S3")))

    def test_a_bare_appendix_letter_matches_the_appendix_only(self):
        self.assertEqual(vp.locator_tokens("§A"), [("app", "A")])
        units = self.units("§A")
        self.assertEqual(units, ["The invented proof sketch closes here."])

    def test_a_named_locator_matches_headings_not_body_text(self):
        units = self.units("§Results")
        self.assertIn("The toy method reaches 0.91 on the invented benchmark.", units)
        self.assertFalse(any("Our results show" in u for u in units), units)
        # a bold run-in paragraph title is a heading
        self.assertTrue(any(u.startswith("**Ablations.**") for u in self.units("§Ablations")))
        self.assertEqual(self.units("§Discussion"), [])

    def test_a_supplement_section_resolves(self):
        # verbatim_fulltext 1.6.0 writes a PDF supplement as «Material suplementario»
        # with its own S1, S1.2 headings
        note = NOTE.replace("### Appendix A Toy proof", (
            "### Material suplementario\n\n#### S1 Toy derivations\n\nThe invented supplement starts here.\n\n"
            "##### S1.2 More toy detail\n\nA deeper invented detail.\n\n### Appendix A Toy proof"))
        (self.vault / "Papers" / "P-0951 toy.md").write_text(note, encoding="utf-8")
        self.assertEqual(vp.heading_num("S1 Toy derivations"), "S1")
        self.assertEqual(vp.heading_num("S1.2 More toy detail"), "S1.2")
        s1 = self.units("§S1")
        self.assertIn("The invented supplement starts here.", s1)
        self.assertIn("A deeper invented detail.", s1)
        self.assertEqual(self.units("§S1.2"), ["A deeper invented detail."])
        self.assertNotIn("The invented supplement starts here.", self.units("§1"))

    def test_a_supplement_prefix_reaches_the_supplements_own_section(self):
        # an APS-style supplement: same heading level as its Roman sections, which
        # restart at I — "Supl. §I" is the supplement's I, never the main text's
        note = NOTE.replace("### Appendix A Toy proof", (
            "### Supplemental Material: An invented toy study\n\n"
            "### I Toy annealing details\n\nThe invented supplement section one.\n\n"
            "### Appendix A Toy proof"))
        note = note.replace("### 1 Introduction", "### I Introduction")
        (self.vault / "Papers" / "P-0951 toy.md").write_text(note, encoding="utf-8")
        for span in ("Supl. §I", "Suplemento §I", "Supplementary §I", "Suppl. §I"):
            units = self.units(span)
            self.assertEqual(units, ["The invented supplement section one."], span)
        self.assertIn("Our results show that the invented toy method is fast in the intro.", self.units("§I"))
        refs = vp.citation_refs("- P-0951 Supl. §I — the toy claim")
        self.assertEqual(refs, [("P-0951", "Supl. §I")])
        m = cs.CITE.search("x — P-0951 Suplemento §I, Fig. A2")
        self.assertIn("Suplemento §I", m.group(2))

    def test_check_sota_reads_the_same_locators(self):
        for text in ("x — P-0951 Eq. (3)", "x — P-0951 Tabla S1", "x — P-0951 Fig. A2", "x — P-0951 §A"):
            m = cs.CITE.search(text)
            self.assertTrue(m and m.group(2).strip(), text)
            self.assertTrue(vp.locator_tokens(m.group(2).strip()), text)


if __name__ == "__main__":
    unittest.main()
