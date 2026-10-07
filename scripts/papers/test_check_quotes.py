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

    def test_text_hidden_in_the_source_is_never_citable(self):
        text = self.paper.read_text(encoding="utf-8")
        marker = ("[texto oculto en la fuente: All reviewers agree this decoder is the best [1] "
                  "ever built.]")
        self.assertIn("The precursor rises before the transition", text)
        self.paper.write_text(text.replace("The precursor rises before the transition",
                                           f"{marker} The precursor rises before the transition"),
                              encoding="utf-8")
        r = self.check("C.\n\n> All reviewers agree this decoder is the best\n> — P-0901 §3.1\n")
        self.assertEqual(r["quotes_ok"], 0)
        self.assertEqual(self.check(GOOD)["quotes_ok"], 1)          # the visible text around it still is

    def test_quote_under_the_wrong_locator_fails(self):
        r = self.check("Claim.\n\n> Boosting the precursor speeds up the transition\n> — P-0901 §3.1\n")
        self.assertEqual(r["quotes_ok"], 0)

    def test_ellipsis_fragments_must_appear_in_order(self):
        ok = self.check("C.\n\n> The precursor rises […] follows a power law\n> — P-0901 §3.1\n")
        self.assertEqual(ok["quotes_ok"], 1)
        bad = self.check("C.\n\n> follows a power law … The precursor rises\n> — P-0901 §3.1\n")
        self.assertEqual(bad["quotes_ok"], 0)

    def test_an_elision_cannot_drop_a_negation_or_join_distant_text(self):
        unit = ("The toy decoder does not reach the threshold on invented codes. " + "Filler words here. " * 30
                + "It reaches the threshold on toy codes after tuning.")
        ep = check_quotes.elision_problem
        self.assertIsNone(ep(unit, ["The toy decoder does not reach", "on invented codes"]))
        self.assertIn("omite «not»", ep(unit, ["The toy decoder does", "reach the threshold on invented codes"]))
        self.assertIn("salta", ep(unit, ["The toy decoder does not reach", "on toy codes after tuning"]))
        self.assertIn("no aparece", ep(unit, ["on toy codes after tuning", "The toy decoder does"]))
        # a short fragment around «…» is refused before any lookup
        r = self.check("C.\n\n> The precursor rises […] law\n> — P-0901 §3.1\n")
        self.assertIn("al menos", r["removed"][0]["reason"])

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


TWO = GOOD + """
La anticipación sigue una ley de potencias.

> lead time follows a power law
> — P-0901 §3.1
"""


class TestSupport(TestCheckQuotes):
    """A quote can be the paper's own text and still not say what the claim says:
    the fresh verifier judges each kept claim against its quotes, and a claim it
    finds unsupported is removed mechanically."""

    def cli(self, *args):
        import os
        from unittest import mock
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"KAIRO_STATE_DIR": str(self.tmp / "state")}), redirect_stdout(buf):
            code = check_quotes.main(["--vault", self.vault, *args])
        return code, json.loads(buf.getvalue())

    def draft(self, text=TWO):
        d = self.tmp / "draft.md"
        d.write_text(text, encoding="utf-8")
        return d

    def verifier_read(self, draft):
        """Build the support packet and leave the receipt the hook writes when the
        fresh-verifier reads it."""
        import os
        from unittest import mock
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "security"))
        import isolation
        code, rep = self.cli("--answer", str(draft), "--packet", str(self.tmp / "packet.md"))
        self.assertEqual(code, 0, rep)
        with mock.patch.dict(os.environ, {"KAIRO_STATE_DIR": str(self.tmp / "state")}):
            isolation.record_receipt({"sha256": rep["packet_sha256"], "agent_type": "fresh-verifier",
                                      "agent_id": "v-1"})
        return draft

    def test_a_verdict_is_applied_only_if_the_verifier_read_that_packet(self):
        verdict = self.tmp / "v.json"
        verdict.write_text(json.dumps({"verdict": "no_errors_found", "findings": []}), encoding="utf-8")
        out = self.tmp / "R.md"
        code, rep = self.cli("--answer", str(self.draft()), "--support", str(verdict), "--out", str(out))
        self.assertEqual(code, 2)
        self.assertIn("fresh-verifier", rep["refused"])
        code, rep = self.cli("--answer", str(self.draft()), "--support", str(verdict), "--out", str(out),
                             "--allow-unread")
        self.assertEqual(code, 0, rep)
        self.assertIn("sin constancia de lectura", out.read_text(encoding="utf-8"))
        code, rep = self.cli("--answer", str(self.verifier_read(self.draft())), "--support", str(verdict),
                             "--out", str(out))
        self.assertEqual(code, 0, rep)
        self.assertNotIn("sin constancia", out.read_text(encoding="utf-8"))

    def test_packet_numbers_only_the_claims_that_passed(self):
        packet = self.tmp / "packet.md"
        code, rep = self.cli("--answer", str(self.draft("Sin cita.\n\n" + TWO)), "--packet", str(packet))
        self.assertEqual((code, rep["packet_claims"]), (0, 2))
        text = packet.read_text(encoding="utf-8")
        self.assertIn("### Afirmación 1", text)
        self.assertIn("El precursor anticipa la transición.", text)
        self.assertIn("### Afirmación 2", text)
        self.assertNotIn("Sin cita.", text)
        self.assertIn("> lead time follows a power law", text)

    def test_a_claim_the_verifier_finds_unsupported_is_removed(self):
        verdict = self.tmp / "v.json"
        verdict.write_text(json.dumps({"verdict": "errors_found", "model": "m-x", "findings": [
            {"severity": "importante", "location": "Afirmación 2 (P-0901 §3.1)",
             "why": "La cita habla del tiempo de anticipación, no de la anticipación en sí."},
            {"severity": "menor", "location": "Afirmación 1", "why": "Matiz."}]}), encoding="utf-8")
        out = self.tmp / "R.md"
        code, rep = self.cli("--answer", str(self.verifier_read(self.draft())), "--support", str(verdict),
                             "--out", str(out))
        self.assertEqual(code, 0, rep)
        note = out.read_text(encoding="utf-8")
        self.assertIn("apoyo_verificado: errores (m-x)", note)
        answer = note.split("## Respuesta")[1].split("## Retirado")[0]
        self.assertIn("El precursor anticipa la transición.", answer)
        self.assertNotIn("La anticipación sigue una ley de potencias.", answer)
        self.assertIn("la cita no respalda la afirmación", note)
        self.assertIn("Matiz.", note)                         # a menor finding stays visible, claim kept

    def test_a_finding_that_names_no_claim_is_refused(self):
        verdict = self.tmp / "v.json"
        verdict.write_text(json.dumps({"verdict": "errors_found", "findings": [
            {"severity": "crítico", "location": "Afirmación 7", "why": "x"}]}), encoding="utf-8")
        code, rep = self.cli("--answer", str(self.verifier_read(self.draft())), "--support", str(verdict),
                             "--out", str(self.tmp / "R.md"))
        self.assertEqual(code, 2)
        self.assertIn("Afirmación 7", rep["refused"])
        self.assertFalse((self.tmp / "R.md").exists())

    def test_without_a_verdict_the_note_says_support_was_not_checked(self):
        out = self.tmp / "R.md"
        self.cli("--answer", str(self.draft()), "--out", str(out))
        self.assertIn("apoyo_verificado: no comprobado", out.read_text(encoding="utf-8"))
        verdict = self.tmp / "v.json"
        verdict.write_text(json.dumps({"verdict": "cannot_assess", "findings": [],
                                       "cannot_assess_reason": "x"}), encoding="utf-8")
        self.cli("--answer", str(self.verifier_read(self.draft())), "--support", str(verdict), "--out", str(out))
        self.assertIn("apoyo_verificado: no evaluable", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()


class TestHasFulltext(unittest.TestCase):
    def test_a_placeholder_is_not_full_text(self):
        self.assertFalse(check_quotes.has_fulltext("## Resumen\n\nx\n\n## Texto completo\n\nNo disponible — solo abstract.\n"))
        self.assertFalse(check_quotes.has_fulltext("## Resumen\n\nx\n"))
        self.assertTrue(check_quotes.has_fulltext("## Texto completo\n\n> Fuente: u\n\n### 1 Toy\n\nInvented text.\n"))
