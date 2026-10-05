"""Tests for check_review.py. Run: python -m pytest scripts/papers

Invented fixture papers only (P-0901 from the verifier tests, P-0902 made here)."""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "ledger"))
import check_review  # noqa: E402
from test_verifier_scripts import build_fixture  # noqa: E402

CONFLICT = """---
id: P-0902
title: An invented paper whose identifiers disagree
projects: [PROJ-900]
resolution_status: mismatch
---

## Resumen

An invented abstract about toy embeddings of small graphs.
"""

REVIEW = """## Qué es el problema

Un precursor es una señal que aparece antes de una transición. El efecto crece con el tiempo de anticipación [P-0901 §3.1].

> The precursor rises before the transition; lead time follows a power law
> — P-0901 §3.1

## Métodos

| Método | Ventaja | Inconveniente |
|---|---|---|
| Intervenir el precursor | acelera la transición [P-0901 §3.2] | solo en dinámicas de juguete |

> Boosting the precursor slows the transition down
> — P-0901 §3.2

Un trabajo sobre grafos pequeños lo discute [P-0902 §Resumen]. Otro sin localizador [P-0901]. Uno ajeno [P-0999 §1].
"""


class TestCheckReview(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-review-"))
        self.f = build_fixture(self.tmp)
        self.vault = str(self.f["vault"])
        paper = next(Path(self.vault, "Papers").glob("P-0901*.md"))
        paper.write_text(paper.read_text(encoding="utf-8").replace("title:", "projects: [PROJ-900]\ntitle:", 1),
                         encoding="utf-8")
        Path(self.vault, "Papers", "P-0902 Roe 2031 Conflict.md").write_text(CONFLICT, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_list_marks_conflicts(self):
        papers = {p["id"]: p for p in check_review.papers_for(self.vault, "PROJ-900")}
        self.assertEqual(set(papers), {"P-0901", "P-0902"})
        self.assertTrue(papers["P-0902"]["conflict"])
        self.assertFalse(papers["P-0901"]["conflict"])
        self.assertTrue(papers["P-0901"]["fulltext"])

    def test_quotes_and_inline_citations(self):
        r = check_review.check(self.vault, REVIEW, "PROJ-900")
        self.assertEqual((r["quotes"], r["quotes_ok"]), (2, 1))
        self.assertEqual(r["retired"][0]["locator"], "§3.2")
        self.assertIn("⚠ Cita retirada por la comprobación (P-0901 §3.2)", r["text"])
        self.assertNotIn("slows the transition down", r["text"])
        # explanations without a citation stay: the review is allowed to explain
        self.assertIn("Un precursor es una señal", r["text"])
        self.assertEqual((r["citations"], r["citations_ok"]), (5, 2))
        reasons = {f["paper"]: f["reason"] for f in r["flagged"]}
        self.assertIn("conflicto", reasons["P-0902"])
        self.assertEqual(reasons["P-0999"], "no es un paper del proyecto")
        self.assertIn("[P-0901 ⚠ sin localizador]", r["text"])
        self.assertIn("[P-0902 §Resumen ⚠ referencia en conflicto", r["text"])
        self.assertIn("| acelera la transición [P-0901 §3.2] |", r["text"])

    def test_wrong_locator_is_flagged(self):
        r = check_review.check(self.vault, "Algo [P-0901 §9.9].", "PROJ-900")
        self.assertEqual(r["flagged"][0]["reason"], "localizador no encontrado en la nota")

    def test_quote_from_a_conflicting_paper_is_retired(self):
        text = "Claim.\n\n> An invented abstract about toy embeddings of small graphs.\n> — P-0902 §Resumen\n"
        r = check_review.check(self.vault, text, "PROJ-900")
        self.assertEqual(r["quotes_ok"], 0)
        self.assertIn("conflicto", r["retired"][0]["reason"])

    def test_uncited_papers_are_listed(self):
        r = check_review.check(self.vault, "Solo uno [P-0901 §3.1].", "PROJ-900")
        self.assertEqual((r["cited"], r["uncited"]), (["P-0901"], ["P-0902"]))

    def test_a_readable_paper_left_out_without_a_reason_is_unaccounted(self):
        r = check_review.check(self.vault, "Solo uno [P-0901 §3.1].", "PROJ-900")
        self.assertEqual(r["unaccounted"], ["P-0902"])          # it has an abstract
        text = "Solo uno [P-0901 §3.1].\n\n## Papers no tratados\n\n- P-0902: referencia en conflicto.\n"
        self.assertEqual(check_review.check(self.vault, text, "PROJ-900")["unaccounted"], [])

    def test_out_writes_a_non_citable_note(self):
        draft = self.tmp / "borrador.md"
        draft.write_text(REVIEW, encoding="utf-8")
        out = self.tmp / "Informes" / "revision.md"
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = check_review.main(["--vault", self.vault, "--project", "PROJ-900", "--review", str(draft),
                                      "--out", str(out), "--title", "Revisión de prueba"])
        self.assertEqual(code, 0)
        rep = json.loads(buf.getvalue())
        self.assertNotIn("text", rep)
        note = out.read_text(encoding="utf-8")
        for line in ("tipo: revision-literatura", "escrito_por: modelo", "citable: false",
                     "citas_literales: 1/2", "referencias_comprobadas: 2/5", "papers_citados: 2/2",
                     "# Revisión de prueba", "## Comprobación"):
            self.assertIn(line, note)


if __name__ == "__main__":
    unittest.main()
