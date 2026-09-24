"""Tests for build_graph.py and claim_status.py (standard library unittest).

Run: python -m unittest scripts/ledger/test_ledger.py -v
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build_graph  # noqa: E402
import claim_status  # noqa: E402

HYP = """---
id: {id}
project: PROJ-900
status: {status}
created: 2026-09-24
updated: 2026-09-24
depends_on: {deps}  # comment kept
linked_experiment: []
history:
  - date: 2026-09-24
    status: propuesta
    by: test
    evidence: "x"
---

## Claim

Claim of {id}.
"""


def _run(fn, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = fn(argv)
    return code, out.getvalue(), err.getvalue()


class LedgerFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-ledger-"))
        self.vault = self.tmp / "vault"
        self.proj = self.vault / "Projects" / "synthetic"
        (self.proj / "Hipotesis").mkdir(parents=True)
        (self.proj / "_hub.md").write_text("---\nid: PROJ-900\n---\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def hyp(self, hid, status="propuesta", deps="[]"):
        p = self.proj / "Hipotesis" / f"{hid} x.md"
        p.write_text(HYP.format(id=hid, status=status, deps=deps), encoding="utf-8", newline="\n")
        return p

    def claim(self, statement, deps=(), kind="lema", **kw):
        argv = ["new", "--project-dir", str(self.proj), "--kind", kind,
                "--statement", statement, "--by", "test", "--date", "2026-09-24"]
        if deps:
            argv += ["--depends-on", *deps]
        for k, v in kw.items():
            argv += [f"--{k.replace('_', '-')}", v]
        code, out, err = _run(claim_status.main, argv)
        self.assertEqual(code, 0, err)
        return Path(out.strip())

    def snapshot(self):
        return {p.relative_to(self.vault).as_posix(): p.read_bytes()
                for p in sorted(self.vault.rglob("*.md"))}


class ChainPropagation(LedgerFixture):
    """The required synthetic test: A -> B -> C, refute A."""

    def test_refuting_A_flags_B_and_C_and_changes_nothing_else(self):
        a = self.claim("A: lemma the chain rests on")                    # C-0001
        self.hyp("H-0001", deps="[C-0001]")                              # B
        c = self.claim("C: builds on B", deps=["H-0001"])                # C-0002
        self.hyp("H-0002")                                               # D, unrelated
        self.claim("E: depends on D only", deps=["H-0002"])              # C-0003

        code, _, _ = _run(build_graph.main, ["--vault", str(self.vault), "--write"])
        self.assertEqual(code, 0)
        before = self.snapshot()
        ledger0 = (self.proj / "_ledger.md").read_text(encoding="utf-8")
        self.assertIn("Ninguno.", ledger0)

        code, _, err = _run(claim_status.main, [
            "set", "--note", str(a), "--status", "refutado", "--by", "test",
            "--evidence", "contraejemplo sintético", "--date", "2026-09-24"])
        self.assertEqual(code, 0, err)
        code, out, _ = _run(build_graph.main, ["--vault", str(self.vault), "--write", "--json"])
        self.assertEqual(code, 0)
        res = json.loads(out)
        flagged = {f["node"] for f in res["findings"] if f["kind"] == "depends_on_failed"}
        self.assertEqual(flagged, {"H-0001", "C-0002"})
        self.assertTrue(all(f["severity"] == "importante" for f in res["findings"]))
        via = {f["node"]: f["detail"] for f in res["findings"]}
        self.assertIn("C-0002 ← H-0001 ← C-0001", via["C-0002"])

        after = self.snapshot()
        changed = {k for k in after if before.get(k) != after[k]}
        # only A (via its single writer) and the derived ledger changed
        self.assertEqual(changed, {a.relative_to(self.vault).as_posix(),
                                   "Projects/synthetic/_ledger.md"})
        ledger = (self.proj / "_ledger.md").read_text(encoding="utf-8")
        self.assertIn("**H-0001** — depende de C-0001 (`refutado`)", ledger)
        self.assertIn("**C-0002** — depende de C-0001 (`refutado`)", ledger)
        self.assertNotIn("**H-0002**", ledger)
        self.assertNotIn("**C-0003**", ledger)
        # statuses of dependents untouched
        self.assertIn("status: propuesta", (self.proj / "Hipotesis" / "H-0001 x.md").read_text())
        self.assertIn("status: pendiente", c.read_text())

    def test_refutada_hypothesis_is_a_failure_source(self):
        self.hyp("H-0001", status="refutada")
        self.hyp("H-0002", deps="[H-0001]")
        _, out, _ = _run(build_graph.main, ["--vault", str(self.vault), "--json"])
        nodes = {f["node"] for f in json.loads(out)["findings"]}
        self.assertEqual(nodes, {"H-0002"})


class Integrity(LedgerFixture):
    def test_cycle_is_critico(self):
        self.hyp("H-0001", deps="[H-0003]")
        self.hyp("H-0002", deps="[H-0001]")
        self.hyp("H-0003", deps="[H-0002]")
        self.hyp("H-0004", deps="[H-0004]")
        code, out, _ = _run(build_graph.main, ["--vault", str(self.vault), "--json"])
        self.assertEqual(code, 2)
        cyc = {f["node"] for f in json.loads(out)["findings"] if f["kind"] == "cycle"}
        self.assertEqual(cyc, {"H-0001", "H-0002", "H-0003", "H-0004"})

    def test_dangling_is_critico_block_list_parsed(self):
        p = self.proj / "Hipotesis" / "H-0001 x.md"
        p.write_text(HYP.format(id="H-0001", status="propuesta", deps="")
                     .replace("depends_on:   # comment kept",
                              "depends_on:\n  - H-0009\n  - C-0042"), encoding="utf-8")
        code, out, _ = _run(build_graph.main, ["--vault", str(self.vault), "--json"])
        self.assertEqual(code, 2)
        det = sorted(f["detail"] for f in json.loads(out)["findings"])
        self.assertEqual(len(det), 2)
        self.assertTrue(det[0].startswith("depends_on → C-0042"))

    def test_bad_status_and_duplicate(self):
        self.hyp("H-0003", status="confirmada")
        self.hyp("H-0001")
        (self.proj / "Hipotesis" / "H-0001 dup.md").write_text(
            HYP.format(id="H-0001", status="propuesta", deps="[]"), encoding="utf-8")
        _, out, _ = _run(build_graph.main, ["--vault", str(self.vault), "--json"])
        kinds = sorted(f["kind"] for f in json.loads(out)["findings"])
        self.assertIn("bad_status", kinds)
        self.assertIn("duplicate_id", kinds)

    def test_placeholder_depends_on_is_empty(self):
        self.hyp("H-0001", deps="[]")
        p = self.proj / "Hipotesis" / "H-0002 x.md"
        p.write_text(HYP.format(id="H-0002", status="propuesta", deps="[]")
                     .replace("depends_on: []", "depends_on: []  # e.g. [H-0001]"),
                     encoding="utf-8")
        code, out, _ = _run(build_graph.main, ["--vault", str(self.vault), "--json"])
        self.assertEqual(code, 0, out)

    def test_hook_mode_rebuilds_only_that_project_and_never_fails(self):
        self.hyp("H-0001")
        payload = json.dumps({"tool_name": "Write", "tool_input": {
            "file_path": str(self.proj / "Hipotesis" / "H-0001 x.md")}})
        r = subprocess.run([sys.executable, str(HERE / "build_graph.py"), "--hook"],
                           input=payload, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertTrue((self.proj / "_ledger.md").exists())
        r = subprocess.run([sys.executable, str(HERE / "build_graph.py"), "--hook"],
                           input="not json", capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        r = subprocess.run([sys.executable, str(HERE / "build_graph.py"), "--hook"],
                           input=json.dumps({"tool_input": {"file_path": str(self.tmp / "x.md")}}),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)

    def test_cli_prints_non_ascii_findings_on_any_console(self):
        # regression: '←' / 'crítico' crashed print() on a cp1252 Windows console
        self.hyp("H-0001", status="refutada")
        self.hyp("H-0002", deps="[H-0001]")
        env = {k: v for k, v in __import__("os").environ.items() if k != "PYTHONIOENCODING"}
        env["PYTHONIOENCODING"] = "cp1252"
        r = subprocess.run([sys.executable, str(HERE / "build_graph.py"), "--vault",
                            str(self.vault)], capture_output=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        self.assertIn("←", r.stdout.decode("utf-8"))

    def test_ledger_is_deterministic(self):
        self.hyp("H-0001")
        self.claim("x", deps=["H-0001"])
        _run(build_graph.main, ["--vault", str(self.vault), "--write"])
        one = (self.proj / "_ledger.md").read_bytes()
        _run(build_graph.main, ["--vault", str(self.vault), "--write"])
        self.assertEqual(one, (self.proj / "_ledger.md").read_bytes())


class ClaimWriter(LedgerFixture):
    def test_transitions(self):
        c = self.claim("x")
        code, _, err = _run(claim_status.main, ["set", "--note", str(c), "--status", "probado",
                                                "--by", "t", "--evidence", "ok"])
        self.assertEqual(code, 0, err)
        code, _, _ = _run(claim_status.main, ["set", "--note", str(c), "--status", "fallido",
                                              "--by", "t", "--evidence", "no"])
        self.assertEqual(code, 3)  # probado -> fallido refused
        code, _, _ = _run(claim_status.main, ["set", "--note", str(c), "--status", "refutado",
                                              "--by", "t", "--evidence", "later"])
        self.assertEqual(code, 0)
        code, _, _ = _run(claim_status.main, ["set", "--note", str(c), "--status", "probado",
                                              "--by", "t", "--evidence", "again"])
        self.assertEqual(code, 3)  # terminal
        text = c.read_text(encoding="utf-8")
        self.assertEqual(text.count("  - date:"), 3)  # pendiente + probado + refutado
        self.assertIn("status: refutado", text)

    def test_refuses_hypothesis_notes(self):
        h = self.hyp("H-0001")
        code, _, err = _run(claim_status.main, ["set", "--note", str(h), "--status", "probado",
                                                "--by", "t", "--evidence", "x"])
        self.assertEqual(code, 3)
        self.assertIn("update-confidence", err)

    def test_rung_requires_exploratory_role_and_links(self):
        base = ["new", "--project-dir", str(self.proj), "--kind", "rung",
                "--statement", "s", "--by", "t"]
        self.assertEqual(_run(claim_status.main, base + ["--rung", "0", "--about", "H-0001",
                                                         "--source", "E-0001"])[0], 3)
        self.assertEqual(_run(claim_status.main, base + ["--role", "exploratory", "--rung", "3",
                                                         "--about", "H-0001",
                                                         "--source", "E-0001"])[0], 3)
        self.hyp("H-0001")
        code, out, err = _run(claim_status.main, base + ["--role", "exploratory", "--rung", "0",
                                                         "--about", "H-0001",
                                                         "--source", "E-0001"])
        self.assertEqual(code, 0, err)
        _, js, _ = _run(build_graph.main, ["--vault", str(self.vault), "--write", "--json"])
        node = json.loads(js)["nodes"]["C-0001"]
        self.assertEqual((node["claim_kind"], node["rung"], node["role"]), ("rung", "0", "exploratory"))
        self.assertIn("| H-0001 | C-0001 | 0 | E-0001 | pendiente |",
                      (self.proj / "_ledger.md").read_text(encoding="utf-8"))

    def test_ids_are_vault_wide(self):
        other = self.vault / "Projects" / "other"
        (other / "Claims").mkdir(parents=True)
        (other / "Claims" / "C-0007.md").write_text("---\nid: C-0007\nstatus: pendiente\n---\n",
                                                    encoding="utf-8")
        self.assertEqual(self.claim("x").name, "C-0008.md")


if __name__ == "__main__":
    unittest.main()
