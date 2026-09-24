"""Tests for evidence_gate.py. Run: python -m unittest scripts/analysis/test_evidence_gate.py"""

from __future__ import annotations

import io
import json
import shutil
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

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def exp(self, eid, role="", validity="valid", hyp="H-0001"):
        p = self.proj / "Experimentos" / f"{eid}.md"
        p.write_text(EXP.format(id=eid, hyp=hyp, role=role, validity=validity), encoding="utf-8")
        return p

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
