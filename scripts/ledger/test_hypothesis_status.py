"""Tests for hypothesis_status.py — update-confidence's single status writer.
Run: python -m pytest scripts/ledger

Invented hypotheses and experiments, committed to a temp git repo (evidence_gate
checks the freeze in git)."""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hypothesis_status as hs  # noqa: E402

HYP = """---
id: H-0991
project: PROJ-990
status: {status}
updated: 2031-01-01
linea_publicacion: {linea}
linked_experiment: [{linked}]
{verif}history:
  - date: 2031-01-01
    status: propuesta
    by: tester
{extra_history}---

## Claim

Un claim inventado.
"""

EXP = """---
id: {eid}
hypothesis: H-0991
project: PROJ-990
role: confirmatory
rung: 3
status: completed
experiment_validity: valid
result:
  effect: {effect}
  verdict: {verdict}
---

## Resultado

Inventado.
"""

CLEAN = "verifications:\n  - date: 2031-02-01\n    verifier: kairo/fresh-verifier@1.1.0\n    model: t\n    verdict: no_errors_found\n    scope: note\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-hs-")).resolve()
        self.vault = self.tmp / "vault"
        self.proj = self.vault / "Projects" / "demo"
        (self.proj / "Hipotesis").mkdir(parents=True)
        (self.proj / "Experimentos").mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "tester")
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        for m in hs.AGENT_MARKERS:
            os.environ.pop(m, None)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *a):
        subprocess.run(["git", *a], cwd=self.vault, check=True, capture_output=True)

    def hyp(self, status="en_experimento", linea="false", linked=(), verified=False, adjudicated=()):
        extra = "".join(f"  - date: 2031-01-15\n    status: en_experimento\n    by: kairo\n    experiments: [{e}]\n" for e in adjudicated)
        (self.proj / "Hipotesis" / "H-0991 prueba.md").write_text(
            HYP.format(status=status, linea=linea, linked=", ".join(linked), verif=CLEAN if verified else "", extra_history=extra),
            encoding="utf-8")

    def exp(self, eid, verdict, effect="0.10 [0.04, 0.16]"):
        (self.proj / "Experimentos" / f"{eid}.md").write_text(EXP.format(eid=eid, verdict=verdict, effect=effect), encoding="utf-8")

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "x")

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = hs.main(list(args))
        return code, (json.loads(out.getvalue()) if out.getvalue().strip() else None), err.getvalue()

    def propose(self):
        return hs.propose(self.vault, "H-0991")


class TestPropose(Base):
    """The state table. The pitfall audit is stubbed here (these invented
    experiments are deliberately thin); TestAuditGate runs the real one."""

    def setUp(self):
        super().setUp()
        self.audit = mock.patch.object(hs, "audit_blockers", return_value=[])
        self.audit.start()

    def tearDown(self):
        self.audit.stop()
        super().tearDown()

    def test_one_refutation_is_terminal_unless_on_a_publication_line(self):
        self.exp("E-0991", "refutada")
        self.hyp(linked=["E-0991"])
        self.commit()
        p = self.propose()
        self.assertEqual((p["to"], p["terminal"]), ("refutada", True))
        self.hyp(linked=["E-0991"], linea="true")
        p = self.propose()
        self.assertEqual((p["to"], p["terminal"]), ("en_experimento", False))
        self.assertIn("replicarla", p["reason"])

    def test_first_support_stays_then_nothing_new(self):
        self.exp("E-0991", "apoyada")
        self.hyp(linked=["E-0991"])
        self.commit()
        self.assertEqual(self.propose()["to"], "en_experimento")
        self.hyp(linked=["E-0991"], adjudicated=["E-0991"])
        p = self.propose()
        self.assertIsNone(p["to"])
        self.assertIn("falta la replicación", p["reason"])

    def test_two_consistent_supports_need_the_verifier(self):
        self.exp("E-0991", "apoyada", "0.10 [0.04, 0.16]")
        self.exp("E-0992", "apoyada", "0.11 [0.05, 0.17]")
        self.hyp(linked=["E-0991", "E-0992"], adjudicated=["E-0991"])
        self.commit()
        p = self.propose()
        self.assertEqual((p["to"], p["needs_verifier"], p["terminal"]), ("apoyada", True, True))
        self.assertEqual(p["combination"]["consistency"], "consistent")

    def test_disagreement_is_mixed_evidence(self):
        self.exp("E-0991", "apoyada", "0.30 [0.20, 0.40]")
        self.exp("E-0992", "apoyada", "-0.30 [-0.40, -0.20]")
        self.hyp(linked=["E-0991", "E-0992"], adjudicated=["E-0991"])
        self.commit()
        self.assertEqual(self.propose()["to"], "evidencia_mixta")
        self.exp("E-0992", "refutada")
        self.commit()
        self.assertEqual(self.propose()["to"], "evidencia_mixta")

    def test_invalid_or_unlinked_experiments_never_count(self):
        self.exp("E-0991", "refutada")
        self.hyp(linked=[])  # not in linked_experiment
        self.commit()
        self.assertIsNone(self.propose()["to"])

    def test_preregistrada_moves_once_it_ran(self):
        self.exp("E-0991", "apoyada")
        self.hyp(status="preregistrada", linked=["E-0991"])
        self.commit()
        self.assertEqual(self.propose()["to"], "en_experimento")


class TestAuditGate(Base):
    def test_a_critical_audit_finding_blocks_any_evidence_edge(self):
        self.exp("E-0991", "refutada")  # no frozen_at, no analysis record, no trace: the audit blocks
        self.hyp(linked=["E-0991"])
        self.commit()
        p = self.propose()
        self.assertIsNone(p["to"])
        self.assertIn("auditoría de trampas", p["reason"])
        self.assertTrue(any(b["check"] == "leakage" for b in p["audit"]))
        text = (self.proj / "Hipotesis" / "H-0991 prueba.md").read_text(encoding="utf-8")
        self.assertNotIn("needs_human_review: true", text)  # the preview's audit is a dry run


class TestApplyAndDiscard(Base):
    def test_apply_refuses_pairs_outside_the_table(self):
        self.hyp(status="propuesta")
        code, _, err = self.cli("apply", "--vault", str(self.vault), "--hypothesis", "H-0991", "--to", "apoyada",
                                "--by", "kairo", "--evidence", "x")
        self.assertEqual(code, 3)
        self.assertIn("no está en la tabla", err)

    def test_apply_writes_status_and_one_history_entry(self):
        self.hyp()
        code, out, err = self.cli("apply", "--vault", str(self.vault), "--hypothesis", "H-0991", "--to", "refutada",
                                  "--by", "kairo/update-confidence", "--evidence", "E-0991 la refuta", "--experiments", "E-0991")
        self.assertEqual(code, 0, err)
        self.assertEqual((out["to"], out["terminal"]), ("refutada", True))
        text = (self.proj / "Hipotesis" / "H-0991 prueba.md").read_text(encoding="utf-8")
        self.assertIn("status: refutada", text.split("history:")[0])
        self.assertIn("experiments: [E-0991]", text)
        self.assertIn("evidence: E-0991 la refuta", text)

    def test_apoyada_needs_a_clean_verification(self):
        self.hyp()
        code, _, err = self.cli("apply", "--vault", str(self.vault), "--hypothesis", "H-0991", "--to", "apoyada",
                                "--by", "kairo", "--evidence", "dos apoyos")
        self.assertEqual(code, 3)
        self.assertIn("no_errors_found", err)
        self.hyp(verified=True)
        self.assertEqual(self.cli("apply", "--vault", str(self.vault), "--hypothesis", "H-0991", "--to", "apoyada",
                                  "--by", "kairo", "--evidence", "dos apoyos")[0], 0)

    def test_descartada_only_through_discard_by_the_researcher(self):
        self.hyp(status="propuesta")
        code, _, err = self.cli("apply", "--vault", str(self.vault), "--hypothesis", "H-0991", "--to", "descartada",
                                "--by", "kairo", "--evidence", "x")
        self.assertEqual(code, 3)
        self.assertIn("solo la escribe `discard`", err)
        os.environ["KAIRO_AGENT_SESSION"] = "1"
        code, _, err = self.cli("discard", "--vault", str(self.vault), "--hypothesis", "H-0991", "--reason", "No sigue.", "--by", "Inv")
        self.assertEqual(code, 3)
        self.assertIn("sesión de agente", err)
        del os.environ["KAIRO_AGENT_SESSION"]
        code, out, err = self.cli("discard", "--vault", str(self.vault), "--hypothesis", "H-0991", "--reason", "No sigue.", "--by", "Inv")
        self.assertEqual((code, out["to"], out["terminal"]), (0, "descartada", True), err)
        code, _, err = self.cli("discard", "--vault", str(self.vault), "--hypothesis", "H-0991", "--reason", "Otra vez.", "--by", "Inv")
        self.assertEqual(code, 3)  # terminal: no outgoing edge


class TestSingleWriter(unittest.TestCase):
    def test_only_the_status_writers_write_a_status(self):
        """A hypothesis status is written only by hypothesis_status.py; claims by
        claim_status.py; ideas by idea.py. Any new script writing `status` fails here."""
        allowed = {"hypothesis_status.py", "claim_status.py", "idea.py"}
        scripts = HERE.parent
        writers = set()
        pattern = re.compile(r"""(_?set_scalar\([^)]*["']status["']|set_fields\([^)]*["']status["']|["']status["']\s*:\s*["']descartada)""")
        for py in scripts.rglob("*.py"):
            if py.name.startswith("test_"):
                continue
            if pattern.search(py.read_text(encoding="utf-8")):
                writers.add(py.name)
        self.assertTrue(writers <= allowed, f"unexpected status writers: {sorted(writers - allowed)}")
        self.assertIn("hypothesis_status.py", writers)

    def test_review_discard_goes_through_the_writer(self):
        src = (HERE / "review.py").read_text(encoding="utf-8")
        self.assertIn("hypothesis_status.discard(", src)
        self.assertNotRegex(src, r'_set_scalar\(lines, "status"')


if __name__ == "__main__":
    unittest.main()
