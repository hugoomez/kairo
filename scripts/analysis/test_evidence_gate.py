"""Tests for evidence_gate.py. Run: python -m unittest scripts/analysis/test_evidence_gate.py"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence_gate  # noqa: E402

EXP = """---
id: {id}
hypothesis: {hyp}
project: PROJ-900
{role}
experiment_validity: {validity}
---

## Resultado
"""


class Gate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-gate-"))
        self.proj = self.tmp / "vault" / "Projects" / "p"
        (self.proj / "Hipotesis").mkdir(parents=True)
        (self.proj / "Experimentos").mkdir()
        # a preregistration only counts once frozen in git (evidence_gate fails closed)
        self.git("init", "-q")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *a):
        subprocess.run(["git", *a], cwd=self.tmp / "vault", check=True, capture_output=True)

    def exp(self, eid, role="", validity="valid", hyp="H-0001", commit=True):
        p = self.proj / "Experimentos" / f"{eid}.md"
        p.write_text(EXP.format(id=eid, hyp=hyp, role=role, validity=validity), encoding="utf-8")
        if commit:
            self.git("add", "-A")
            self.git("commit", "-qm", f"Preregister {eid}")
        return p

    def test_no_git_fails_closed(self):
        loose = Path(tempfile.mkdtemp(prefix="kairo-nogit-"))
        try:
            if subprocess.run(["git", "rev-parse"], cwd=loose, capture_output=True).returncode == 0:
                self.skipTest("temp dir is inside a git repo")
            p = loose / "E-0900.md"
            p.write_text(EXP.format(id="E-0900", hyp="H-0001", role="role: confirmatory\nrung: 3",
                                    validity="valid"), encoding="utf-8")
            code, res = self.run_cli(["check", "--experiment", str(p)])
            self.assertEqual(code, 3, res)
            self.assertIn("git", res["reason"])
        finally:
            shutil.rmtree(loose, ignore_errors=True)

    def run_cli(self, argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = evidence_gate.main(argv + ["--json"])
        return code, json.loads(out.getvalue())

    def test_check(self):
        cases = {
            "": 0,                                       # v2 note: read as confirmatory
            "role: confirmatory\nrung: 3": 0,
            "role: confirmatory": 0,                     # rung unknown is fine
            "role: exploratory\nrung: 0": 3,
            "role: exploratory": 3,
            "role: confirmatory\nrung: 1": 3,            # confirmatory is rung 3 by definition
            "role: pilot": 3,
            "role: confirmatory\nrung: 7": 3,
            "role: <confirmatory | exploratory>\nrung: <0 | 1 | 2 | 3>": 0,  # unfilled template
        }
        for i, (role, want) in enumerate(cases.items()):
            code, res = self.run_cli(["check", "--experiment", str(self.exp(f"E-{i:04d}", role))])
            self.assertEqual(code, want, (role, res))

    def test_rung_without_role_or_with_spaced_key_is_refused(self):
        # review finding (crítico): both used to be ALLOWED as confirmatory
        for i, role in enumerate(["rung: 1", "role : exploratory\nrung: 0", "rung: 0\nrole:  exploratory"]):
            code, res = self.run_cli(["check", "--experiment", str(self.exp(f"E-01{i:02d}", role))])
            self.assertEqual(code, 3, (role, res))

    def test_bom_note_is_read(self):
        p = self.exp("E-0200", "role: exploratory\nrung: 0")
        p.write_bytes(b"\xef\xbb\xbf" + p.read_bytes())
        code, res = self.run_cli(["check", "--experiment", str(p)])
        self.assertEqual(code, 3, res)

    def test_relabel_after_freeze_is_refused(self):
        import subprocess
        git = lambda *a: subprocess.run(["git", *a], cwd=self.tmp / "vault", check=True,
                                        capture_output=True)
        git("init", "-q")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        p = self.exp("E-0300", "role: exploratory\nrung: 0", commit=False)
        git("add", "-A")
        git("commit", "-qm", "Preregister E-0300")
        code, _ = self.run_cli(["check", "--experiment", str(p)])
        self.assertEqual(code, 3)
        p.write_text(EXP.format(id="E-0300", hyp="H-0001", role="role: confirmatory\nrung: 3",
                                validity="valid"), encoding="utf-8")
        code, res = self.run_cli(["check", "--experiment", str(p)])       # uncommitted relabel
        self.assertEqual(code, 3, res)
        self.assertIn("freeze", res["reason"])
        git("commit", "-qam", "relabel")                                     # committed relabel
        self.assertEqual(self.run_cli(["check", "--experiment", str(p)])[0], 3)
        q = self.exp("E-0301", "role: confirmatory\nrung: 3", commit=False)  # never committed
        self.assertEqual(self.run_cli(["check", "--experiment", str(q)])[0], 3)
        git("add", "-A")
        git("commit", "-qm", "Preregister E-0301")
        self.assertEqual(self.run_cli(["check", "--experiment", str(q)])[0], 0)

    def test_relabel_after_draft_commit_is_refused(self):
        # found at integration review: a pre-freeze draft commit hid the freeze
        import subprocess
        git = lambda *a: subprocess.run(["git", *a], cwd=self.tmp / "vault", check=True,
                                        capture_output=True)
        git("init", "-q")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        p = self.exp("E-0310", "role: <confirmatory | exploratory>\nrung: <0 | 1 | 2 | 3>",
                     commit=False)
        git("add", "-A")
        git("commit", "-qm", "draft")                                        # placeholders
        p.write_text(EXP.format(id="E-0310", hyp="H-0001", role="role: exploratory\nrung: 1",
                                validity="valid"), encoding="utf-8")
        git("commit", "-qam", "Preregister E-0310")                          # the real freeze
        p.write_text(EXP.format(id="E-0310", hyp="H-0001", role="role: confirmatory\nrung: 3",
                                validity="valid"), encoding="utf-8")
        git("commit", "-qam", "relabel")
        code, res = self.run_cli(["check", "--experiment", str(p)])
        self.assertEqual(code, 3, res)
        # a draft that said confirmatory before an exploratory freeze doesn't help either
        q = self.exp("E-0311", "role: confirmatory\nrung: 3", commit=False)
        git("add", "-A")
        git("commit", "-qm", "draft")
        q.write_text(EXP.format(id="E-0311", hyp="H-0001", role="role: exploratory\nrung: 0",
                                validity="valid"), encoding="utf-8")
        git("commit", "-qam", "Preregister E-0311")
        q.write_text(EXP.format(id="E-0311", hyp="H-0001", role="role: confirmatory\nrung: 3",
                                validity="valid"), encoding="utf-8")
        git("commit", "-qam", "relabel")
        self.assertEqual(self.run_cli(["check", "--experiment", str(q)])[0], 3)

    def test_exploratory_rung_3_is_critico(self):
        p = self.exp("E-0320", "role: exploratory\nrung: 3")
        code, res = self.run_cli(["check", "--experiment", str(p)])
        self.assertEqual(code, 3)
        self.assertIn("crítico", res["reason"])

    def test_gather_excludes_exploratory_even_when_linked(self):
        self.exp("E-0001", "role: exploratory\nrung: 0")               # rung, wrongly linked
        self.exp("E-0002", "role: confirmatory\nrung: 3")
        self.exp("E-0003", "", validity="invalid")
        self.exp("E-0004", "role: exploratory\nrung: 1")               # rung, not linked
        self.exp("E-0005", "role: confirmatory\nrung: 3", hyp="H-0002")
        self.exp("E-0006", "")                                          # v2, valid
        h = self.proj / "Hipotesis" / "H-0001 x.md"
        h.write_text("---\nid: H-0001\nstatus: en_experimento\n"
                     "linked_experiment: [E-0001, E-0002, E-0003, E-0005, E-0006]\n---\n",
                     encoding="utf-8")
        code, res = self.run_cli(["gather", "--hypothesis", str(h)])
        self.assertEqual(code, 0)
        self.assertEqual([e["id"] for e in res["eligible"]], ["E-0002", "E-0006"])
        excl = {e["id"]: e["reason"] for e in res["excluded"]}
        self.assertEqual(set(excl), {"E-0001", "E-0003", "E-0004", "E-0005"})
        self.assertIn("exploratory", excl["E-0001"])
        self.assertIn("no enlazado", excl["E-0004"])


if __name__ == "__main__":
    unittest.main()
