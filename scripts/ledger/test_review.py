"""Tests for review.py. Run: python -m pytest scripts/ledger

Invented hypotheses only."""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import review  # noqa: E402

HYP = """---
id: H-0981
project: PROJ-980
# status enum — comment kept
status: {status}
updated: 2031-01-01
needs_human_review: {nhr}  # set by the cycle
{extra}history:
  - date: 2031-01-01
    status: propuesta
    by: tester
---

## Claim

Un claim inventado.
"""

VERIF = """verifications:
  - date: 2031-01-02
    verifier: kairo/fresh-verifier@1.1.0
    model: test
    verdict: {verdict}
    scope: note
"""


class TestReview(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-review-")).resolve()
        self.vault = self.tmp / "vault"
        (self.vault / "Projects" / "demo" / "Hipotesis").mkdir(parents=True)
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        for m in review.AGENT_MARKERS:
            os.environ.pop(m, None)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def note(self, status="propuesta", nhr="false", verdict=None) -> Path:
        extra = VERIF.format(verdict=verdict) if verdict else ""
        p = self.vault / "Projects" / "demo" / "Hipotesis" / "H-0981 inventada.md"
        p.write_text(HYP.format(status=status, nhr=nhr, extra=extra), encoding="utf-8")
        return p

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = review.main(list(args))
        return code, (json.loads(out.getvalue()) if out.getvalue().strip() else None), err.getvalue()

    def run_decision(self, kind, reason="Lo leí y está bien planteada."):
        return self.cli(kind, "--vault", str(self.vault), "--hypothesis", "H-0981", "--reason", reason, "--by", "Investigadora")

    def test_show_lists_what_applies(self):
        self.note(nhr="true", verdict="errors_found")
        code, out, _ = self.cli("show", "--vault", str(self.vault), "--hypothesis", "H-0981")
        self.assertEqual(code, 0)
        self.assertEqual(out["decisions"], {"mark-reviewed": None, "ack-findings": None, "discard": None})
        self.note(status="preregistrada")
        _, out, _ = self.cli("show", "--vault", str(self.vault), "--hypothesis", "H-0981")
        self.assertTrue(all(out["decisions"].values()))

    def test_mark_reviewed_clears_the_flag_and_keeps_everything_else(self):
        p = self.note(nhr="true")
        code, out, err = self.run_decision("mark-reviewed")
        self.assertEqual(code, 0, err)
        self.assertEqual((out["before"]["needs_human_review"], out["after"]["needs_human_review"]), (True, False))
        text = p.read_text(encoding="utf-8")
        self.assertIn("needs_human_review: false  # set by the cycle", text)
        self.assertIn("# status enum — comment kept", text)
        self.assertIn("status: propuesta", text)
        self.assertIn("reviews:\n  - date:", text)
        self.assertIn("kind: revisada", text)
        self.assertIn("reason: Lo leí y está bien planteada.", text)
        self.assertEqual(text.count("status: propuesta"), 2)  # frontmatter + the untouched history entry

    def test_ack_findings_needs_findings(self):
        self.note(verdict="no_errors_found")
        self.assertEqual(self.run_decision("ack-findings")[0], 3)
        p = self.note(verdict="errors_found")
        code, out, err = self.run_decision("ack-findings")
        self.assertEqual(code, 0, err)
        text = p.read_text(encoding="utf-8")
        self.assertIn("verification_reviewed: true", text)
        self.assertIn("kind: hallazgos_reconocidos", text)
        self.assertIn("verdict: errors_found", text)
        self.assertEqual(self.run_decision("ack-findings")[0], 3)  # already acknowledged

    def test_discard_only_before_preregistration(self):
        for status in ("preregistrada", "en_experimento", "apoyada"):
            self.note(status=status)
            code, _, err = self.run_decision("discard")
            self.assertEqual(code, 3, status)
            self.assertIn("decide la evidencia", err)
        p = self.note(status="en_cola")
        code, out, err = self.run_decision("discard", "Fuera del alcance acordado.")
        self.assertEqual(code, 0, err)
        text = p.read_text(encoding="utf-8")
        self.assertIn("status: descartada", text.split("history:")[0])
        self.assertIn("evidence: \"descartada por el investigador: Fuera del alcance acordado.\"", text)
        from datetime import date
        self.assertIn(f"updated: {date.today().isoformat()}", text)
        self.assertEqual(out["after"]["status"], "descartada")

    def test_refused_inside_an_agent_session_and_without_reason(self):
        self.note(nhr="true")
        os.environ["KAIRO_AGENT_SESSION"] = "1"
        code, _, err = self.run_decision("mark-reviewed")
        self.assertEqual(code, 3)
        self.assertIn("sesión de agente", err)
        del os.environ["KAIRO_AGENT_SESSION"]
        self.assertEqual(self.run_decision("mark-reviewed", reason="ok")[0], 3)

    def test_unknown_or_ambiguous_ids(self):
        self.assertEqual(self.cli("show", "--vault", str(self.vault), "--hypothesis", "H-0999")[0], 3)
        self.assertEqual(self.cli("show", "--vault", str(self.vault), "--hypothesis", "../x")[0], 3)

    def test_an_experiment_can_be_marked_reviewed_only(self):
        e = self.vault / "Projects" / "demo" / "Experimentos"
        e.mkdir()
        (e / "E-0981.md").write_text("---\nid: E-0981\nstatus: completed\nneeds_human_review: true\n---\n", encoding="utf-8")
        code, out, err = self.cli("mark-reviewed", "--vault", str(self.vault), "--hypothesis", "E-0981",
                                  "--reason", "Revisé la desviación.", "--by", "Investigadora")
        self.assertEqual(code, 0, err)
        self.assertIn("needs_human_review: false", (e / "E-0981.md").read_text(encoding="utf-8"))
        code, _, err = self.cli("discard", "--vault", str(self.vault), "--hypothesis", "E-0981",
                                "--reason", "No aplica aquí.", "--by", "Investigadora")
        self.assertEqual(code, 3)
        self.assertIn("solo para hipótesis", err)

    def test_crlf_notes_keep_their_line_endings(self):
        p = self.note(nhr="true")
        p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
        self.assertEqual(self.run_decision("mark-reviewed")[0], 0)
        raw = p.read_bytes()
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))


if __name__ == "__main__":
    unittest.main()
