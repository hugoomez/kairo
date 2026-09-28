"""Tests for check_quotes.py. Run: python -m pytest scripts/papers

Invented fixture paper P-0901 (from the verifier tests) only."""

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
import check_quotes  # noqa: E402
from test_verifier_scripts import build_fixture, send_never  # noqa: E402

GOOD = """El precursor anticipa la transición.

> The precursor rises before the transition; lead time follows a power law
> — P-0901 §3.1
"""


class TestCheckQuotes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-quotes-"))
        self.f = build_fixture(self.tmp)
        self.vault = str(self.f["vault"])
        self.paper = next(Path(self.vault, "Papers").glob("P-0901*.md"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, text, project=None):
        return check_quotes.check(self.vault, text, project)

    def test_exact_quote_survives(self):
        r = self.check(GOOD)
        self.assertEqual((r["status"], r["quotes"], r["quotes_ok"], r["removed"]), ("answered", 1, 1, []))

    def test_whitespace_is_the_only_normalisation(self):
        r = self.check("Claim.\n\n>   The precursor   rises before\n> the transition;\n> — P-0901 §3.1\n")
        self.assertEqual(r["quotes_ok"], 1)
        r = self.check("Claim.\n\n> The precursor rose before the transition\n> — P-0901 §3.1\n")
        self.assertEqual((r["status"], r["quotes_ok"]), ("nothing_verified", 0))
        self.assertIn("no aparece literalmente", r["removed"][0]["reason"])

    def test_quote_under_the_wrong_locator_fails(self):
        r = self.check("Claim.\n\n> Boosting the precursor speeds up the transition\n> — P-0901 §3.1\n")
        self.assertEqual(r["quotes_ok"], 0)

    def test_ellipsis_fragments_must_appear_in_order(self):
        ok = self.check("C.\n\n> The precursor rises […] follows a power law\n> — P-0901 §3.1\n")
        self.assertEqual(ok["quotes_ok"], 1)
        bad = self.check("C.\n\n> follows a power law … The precursor rises\n> — P-0901 §3.1\n")
        self.assertEqual(bad["quotes_ok"], 0)

    def test_claim_without_quote_is_removed(self):
        r = self.check("Una afirmación sin respaldo.\n\n" + GOOD)
        self.assertEqual(len(r["claims"]), 1)
        self.assertEqual(r["removed"][0]["reason"], "afirmación sin cita del paper")

    def test_one_bad_quote_removes_its_claim(self):
        r = self.check(GOOD.rstrip() + "\n\n> A sentence the paper never wrote at all\n> — P-0901 §3.1\n")
        self.assertEqual((r["status"], len(r["removed"])), ("nothing_verified", 1))

    def test_missing_attribution_and_short_quote(self):
        r = self.check("C.\n\n> The precursor rises before the transition\n")
        self.assertIn("sin atribución", r["removed"][0]["reason"])
        r = self.check("C.\n\n> power law\n> — P-0901 §3.1\n")
        self.assertIn("demasiado corta", r["removed"][0]["reason"])

    def test_abstract_locator(self):
        r = self.check("C.\n\n> We study toy dynamics and report an interventional speedup.\n> — P-0901 §Resumen\n")
        self.assertEqual(r["quotes_ok"], 1)

    def test_send_never_paper_never_counts(self):
        self.paper.write_text(send_never(self.paper.read_text(encoding="utf-8")), encoding="utf-8")
        r = self.check(GOOD)
        self.assertEqual(r["quotes_ok"], 0)
        self.assertIn("send: never", r["removed"][0]["reason"])

    def test_project_restriction(self):
        r = self.check(GOOD, project="PROJ-900")
        self.assertIn("no es un paper del proyecto", r["removed"][0]["reason"])
        text = self.paper.read_text(encoding="utf-8").replace("title:", "projects: [PROJ-900]\ntitle:", 1)
        self.paper.write_text(text, encoding="utf-8")
        self.assertEqual(self.check(GOOD, project="PROJ-900")["quotes_ok"], 1)

    def test_not_found_answer(self):
        r = self.check("**No está en el corpus.**\n\nBuscado: precursor; transición\n\nPero creo que sí.\n")
        self.assertEqual(r["status"], "not_found")
        self.assertEqual(r["kept_lines"], ["**No está en el corpus.**", "Buscado: precursor; transición"])
        self.assertEqual(len(r["removed"]), 1)

    def test_list_papers(self):
        self.assertEqual([p["id"] for p in check_quotes.list_papers(self.vault, None)], ["P-0901"])
        self.assertEqual(check_quotes.list_papers(self.vault, "PROJ-900"), [])
        self.paper.write_text(send_never(self.paper.read_text(encoding="utf-8")), encoding="utf-8")
        self.assertEqual(check_quotes.list_papers(self.vault, None), [])

    def test_cli_writes_a_non_citable_note(self):
        draft = self.tmp / "draft.md"
        draft.write_text("Sin respaldo.\n\n" + GOOD, encoding="utf-8")
        out = self.tmp / "vault" / "Projects" / "demo" / "Notas de proyecto" / "R-1.md"
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = check_quotes.main(["--vault", self.vault, "--answer", str(draft), "--question", "¿Qué anticipa?", "--out", str(out)])
        self.assertEqual(code, 0)
        rep = json.loads(buf.getvalue())
        self.assertEqual(rep["quotes_ok"], 1)
        note = out.read_text(encoding="utf-8")
        for s in ("escrito_por: modelo", "citable: false", "## Respuesta", "> — P-0901 §3.1",
                  "## Retirado por la comprobación", "Sin respaldo."):
            self.assertIn(s, note)
        self.assertLess(note.index("## Respuesta"), note.index("The precursor rises"))
        self.assertLess(note.index("The precursor rises"), note.index("## Retirado"))


if __name__ == "__main__":
    unittest.main()
