"""Tests for the lemma / theorem rigor gate: claim_gate.py, signoff.py,
numeric_check.py, and claim_status.py's use of them.
Run: python -m pytest scripts/ledger/test_claim_gate.py  (invented claims only)."""

from __future__ import annotations

import hashlib
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claim_gate  # noqa: E402
import claim_records as cr  # noqa: E402
import claim_status  # noqa: E402
import numeric_check  # noqa: E402
import signoff  # noqa: E402
import verifications  # noqa: E402
from verifier_packet import build  # noqa: E402

PASSING = "for n in range(1, 50):\n    assert n * (n + 1) % 2 == 0, n\nprint('ok: 49 casos')\n"
FAILING = "import sys\nprint('falla en n=3')\nsys.exit(1)\n"


def run(fn, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = fn(argv)
    return code, out.getvalue(), err.getvalue()


class Gate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-gate-"))
        self.vault = self.tmp / "vault"
        self.proj = self.vault / "Projects" / "teoria"
        self.proj.mkdir(parents=True)
        (self.proj / "_hub.md").write_text("---\nid: PROJ-900\n---\n", encoding="utf-8")
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        for k in signoff.AGENT_MARKERS:
            os.environ.pop(k, None)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def new(self, statement, kind="teorema", how=None, deps=()):
        argv = ["new", "--project-dir", str(self.proj), "--kind", kind, "--statement", statement, "--by", "t"]
        if how:
            argv += ["--how", how]
        if deps:
            argv += ["--depends-on", *deps]
        code, out, err = run(claim_status.main, argv)
        self.assertEqual(code, 0, err)
        return Path(out.strip())

    def cid(self, path):
        return path.stem

    def verify(self, path, verdict="no_errors_found"):
        packet, _ = build(str(self.vault), str(path), [], [], None)
        sha = hashlib.sha256(packet.encode("utf-8")).hexdigest()
        rep = {"verdict": verdict, "scope": "note", "findings": []}
        if verdict == "errors_found":
            rep["findings"] = [{"severity": "crítico", "location": "Demostración, paso 2", "why": "no se sigue"}]
        report = self.tmp / f"report-{sha[:8]}.md"
        report.write_text("```json\n" + json.dumps(rep, ensure_ascii=False) + "\n```\n", encoding="utf-8")
        verifications.append(str(path), "kairo/fresh-verifier@1.1.0", "test-model", verdict, "note", None,
                             report=str(report), packet_sha=sha)

    def sign(self, path, part):
        shown = signoff.show(str(self.vault), self.cid(path), part)
        return signoff.sign(str(self.vault), self.cid(path), part, shown["sha256"], "Investigadora")

    def script(self, path, code):
        d = path.parent / "checks"
        d.mkdir(exist_ok=True)
        (d / f"{self.cid(path)}.py").write_text(code, encoding="utf-8")

    def gate(self, path):
        return claim_gate.gate(str(self.vault), path)

    def all_layers(self, path):
        self.verify(path)
        self.sign(path, "statement")
        self.sign(path, "proof")
        self.script(path, PASSING)
        numeric_check.run(str(self.vault), self.cid(path), "t", 60)

    # ------------------------------------------------------------------

    def test_a_new_theorem_has_the_proof_sections_and_a_closed_gate(self):
        t = self.new("n(n+1) es par para todo n natural.", how="Uno de n, n+1 es par.")
        s = cr.sections(t)
        self.assertEqual(s["Demostración"], "Uno de n, n+1 es par.")
        self.assertIn("Comprobación numérica", s)
        g = self.gate(t)
        self.assertFalse(g["ok"])
        text = " ".join(g["missing"])
        for part in ("verificador", "enunciado", "demostración", "comprobación numérica"):
            self.assertIn(part, text)

    def test_all_three_layers_open_it_and_claim_status_obeys(self):
        t = self.new("n(n+1) es par.", how="Uno de n, n+1 es par.")
        code, _, err = run(claim_status.main, ["set", "--note", str(t), "--status", "probado", "--by", "t", "--evidence", "x"])
        self.assertEqual(code, 3)
        self.assertIn("puerta de rigor", err)
        self.all_layers(t)
        g = self.gate(t)
        self.assertTrue(g["ok"], g["missing"])
        code, _, err = run(claim_status.main, ["set", "--note", str(t), "--status", "probado", "--by", "t", "--evidence", "3 capas"])
        self.assertEqual(code, 0, err)

    def test_editing_the_proof_voids_the_verification_and_its_sign_off(self):
        t = self.new("n(n+1) es par.", how="Uno de n, n+1 es par.")
        self.all_layers(t)
        t.write_text(t.read_text(encoding="utf-8").replace("Uno de n, n+1 es par.", "Por inducción."), encoding="utf-8")
        g = self.gate(t)
        self.assertFalse(g["layers"]["verifier"]["ok"])
        self.assertTrue(g["layers"]["signoff_proof"]["stale"])
        self.assertTrue(g["layers"]["signoff_statement"]["ok"])
        self.assertTrue(g["layers"]["numeric"]["ok"])

    def test_editing_the_statement_voids_its_sign_off_and_the_numeric_check(self):
        t = self.new("n(n+1) es par.", how="Uno de n, n+1 es par.")
        self.all_layers(t)
        t.write_text(t.read_text(encoding="utf-8").replace("n(n+1) es par.", "n(n+1) es par para n >= 0."), encoding="utf-8")
        g = self.gate(t)
        self.assertTrue(g["layers"]["signoff_statement"]["stale"])
        self.assertFalse(g["layers"]["numeric"]["ok"])

    def test_a_verifier_finding_keeps_it_closed(self):
        t = self.new("n(n+1) es par.", how="Uno de n, n+1 es par.")
        self.all_layers(t)
        self.verify(t, "errors_found")
        self.assertIn("errors_found", " ".join(self.gate(t)["missing"]))

    def test_sign_off_is_refused_in_an_agent_session_and_for_text_not_shown(self):
        t = self.new("n(n+1) es par.", how="Uno de n, n+1 es par.")
        shown = signoff.show(str(self.vault), self.cid(t), "statement")
        with self.assertRaises(signoff.Refused):
            signoff.sign(str(self.vault), self.cid(t), "statement", "0" * 64, "Investigadora")
        for marker in signoff.AGENT_MARKERS:
            with mock.patch.dict(os.environ, {marker: "1"}), self.assertRaises(signoff.Refused):
                signoff.sign(str(self.vault), self.cid(t), "statement", shown["sha256"], "Investigadora")
        self.assertEqual(cr.read_block(t, "signoffs"), [])

    def test_a_failing_or_edited_check_script_keeps_it_closed(self):
        t = self.new("n(n+1) es par.", how="Uno de n, n+1 es par.")
        self.verify(t)
        self.sign(t, "statement")
        self.sign(t, "proof")
        self.script(t, FAILING)
        r = numeric_check.run(str(self.vault), self.cid(t), "t", 60)
        self.assertEqual(r["status"], "failed")
        self.assertIn("falla en n=3", r["output_tail"])
        self.assertFalse(self.gate(t)["ok"])
        self.script(t, PASSING)
        numeric_check.run(str(self.vault), self.cid(t), "t", 60)
        self.assertTrue(self.gate(t)["ok"])
        self.script(t, PASSING + "# editado\n")
        self.assertIn("el script cambió", " ".join(self.gate(t)["missing"]))

    def test_the_check_runs_without_credentials(self):
        t = self.new("x es x.", how="trivial")
        self.script(t, "import os, sys\nsys.exit(1 if any('TOKEN' in k for k in os.environ) else 0)\n")
        with mock.patch.dict(os.environ, {"SOME_API_TOKEN": "invented"}):
            self.assertEqual(numeric_check.run(str(self.vault), self.cid(t), "t", 60)["status"], "passed")

    def test_infeasible_needs_a_reason_and_your_sign_off_on_it(self):
        t = self.new("Para todo espacio de Banach X, ...", how="Por Hahn-Banach.")
        self.verify(t)
        self.sign(t, "statement")
        self.sign(t, "proof")
        with self.assertRaises(numeric_check.Refused):
            numeric_check.infeasible(str(self.vault), self.cid(t), "no", "t")
        numeric_check.infeasible(str(self.vault), self.cid(t), "Enunciado sobre espacios de dimensión infinita: no hay casos finitos que lo representen.", "t")
        self.assertIn("motivo", " ".join(self.gate(t)["missing"]))
        self.sign(t, "infeasibility")
        self.assertTrue(self.gate(t)["ok"])

    def test_dependencies_must_stand_and_their_statements_enter_the_packet(self):
        lemma = self.new("Lema: k(k+1) es par.", kind="lema", how="Casos.")
        t = self.new("Teorema: n(n+1)(n+2) es par.", how="Por el lema.", deps=[self.cid(lemma)])
        packet, manifest = build(str(self.vault), str(t), [], [], None)
        self.assertIn("Lema: k(k+1) es par.", packet)
        self.assertNotIn("Casos.", packet)  # a dependency's proof is never sent
        self.assertNotIn("Comprobación numérica", packet)
        self.all_layers(t)
        g = self.gate(t)
        self.assertFalse(g["ok"])
        self.assertIn(f"{self.cid(lemma)} (pendiente)", g["layers"]["dependencies"]["pending"])
        self.all_layers(lemma)
        run(claim_status.main, ["set", "--note", str(lemma), "--status", "probado", "--by", "t", "--evidence", "ok"])
        # the dependency changed status, not text: the theorem's verification still holds
        self.assertTrue(self.gate(t)["ok"], self.gate(t)["missing"])

    def test_non_proof_claims_are_not_gated(self):
        r = self.new("un resultado intermedio", kind="resultado_intermedio")
        self.assertTrue(self.gate(r)["ok"])
        code, _, _ = run(claim_status.main, ["set", "--note", str(r), "--status", "probado", "--by", "t", "--evidence", "x"])
        self.assertEqual(code, 0)

    def test_cli_check_and_project(self):
        t = self.new("n(n+1) es par.", how="Uno de n, n+1 es par.")
        code, out, _ = run(claim_gate.main, ["check", "--vault", str(self.vault), "--claim", self.cid(t)])
        self.assertEqual((code, json.loads(out)["ok"]), (3, False))
        code, out, _ = run(claim_gate.main, ["project", "--vault", str(self.vault), "--project-dir", str(self.proj)])
        self.assertEqual([g["claim"] for g in json.loads(out)], [self.cid(t)])


if __name__ == "__main__":
    unittest.main()
