"""Tests for fill_abstract.py. Run: python -m pytest scripts/papers

Invented notes and invented records only; no network (the fetcher is replaced)."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fill_abstract as fa  # noqa: E402

NOTE = """---
id: P-0960
title: "An invented paper on toy disks"
doi: 10.9999/toy.1990.1
openalex_id: W123
---

## Referencia

Toy, A. (1990). An invented paper on toy disks.

## Resumen

No disponible — ningún abstract recuperado (Crossref).

## Texto completo

No disponible — solo abstract.
"""

ABSTRACT = ("We study invented toy disks and show that every toy disk graph can be drawn "
            "with unit toy radius, which settles a question nobody asked.")


class TestFillAbstract(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-abs-"))
        self.note = self.tmp / "P-0960 Toy 1990.md"
        self.note.write_text(NOTE, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_inverted_index_keeps_the_records_words_in_order(self):
        ii = {"toy": [1, 4], "We": [0], "study": [2], "invented": [3], "disks.": [5]}
        self.assertEqual(fa.from_inverted_index(ii), "We toy study invented toy disks.")
        self.assertEqual(fa.from_inverted_index(None), "")

    def test_jats_markup_is_stripped(self):
        j = "<jats:title>Abstract</jats:title><jats:p>Toy &amp; disks are <jats:italic>fine</jats:italic>.</jats:p>"
        self.assertEqual(fa.from_jats(j), "Toy & disks are fine .")

    def test_candidates_order(self):
        fm, _ = fa.split_frontmatter(NOTE)
        self.assertEqual([c[0] for c in fa.candidates(fm)], ["openalex", "crossref"])

    def test_fills_the_missing_section_from_the_first_source_that_has_it(self):
        calls = []

        def fetcher(label, url):
            calls.append(label)
            return "" if label == "openalex" else ABSTRACT
        r = fa.process(self.note, True, "2030-01-01", fetcher)
        self.assertEqual((r["status"], r["source"]), ("filled", "crossref"))
        self.assertEqual(calls, ["openalex", "crossref"])
        t = self.note.read_text(encoding="utf-8")
        self.assertIn("> Fuente: https://api.crossref.org/works/10.9999/toy.1990.1, obtenido 2030-01-01", t)
        self.assertIn(ABSTRACT, t)
        self.assertNotIn("No disponible — ningún abstract", t)
        self.assertIn("## Texto completo\n\nNo disponible — solo abstract.", t)

    def test_never_touches_a_note_that_has_an_abstract(self):
        self.note.write_text(NOTE.replace("No disponible — ningún abstract recuperado (Crossref).", ABSTRACT),
                             encoding="utf-8")
        r = fa.process(self.note, True, "2030-01-01", lambda *_: "should not be used " * 10)
        self.assertEqual(r["status"], "has_abstract")

    def test_a_too_short_record_is_not_an_abstract(self):
        r = fa.process(self.note, True, "2030-01-01", lambda *_: "Too short.")
        self.assertEqual((r["status"], r["tried"]), ("still_missing", ["openalex", "crossref"]))
        self.assertIn("No disponible", self.note.read_text(encoding="utf-8"))

    def test_without_write_nothing_changes(self):
        r = fa.process(self.note, False, "2030-01-01", lambda *_: ABSTRACT)
        self.assertEqual(r["status"], "found")
        self.assertEqual(self.note.read_text(encoding="utf-8"), NOTE)


if __name__ == "__main__":
    unittest.main()
