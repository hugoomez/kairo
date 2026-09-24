"""Anti-gaming and bundle tests for evolve-program (no LLM calls, no openevolve needed).

Run: python -m unittest skills/evolve-program/scripts/test_evolve.py -v

The bundle tests use a TEST DOUBLE for scripts/security/check_bundle.py (Block A
owns the real one — docs/v3-interfaces.md §1a). The double is written into a
temp dir by the test, honours the same CLI and exit codes, and is never a
substitute for the real check.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
EX = HERE.parent / "examples" / "polyfit"
sys.path.insert(0, str(HERE))

import evolve_run  # noqa: E402
import harness  # noqa: E402

PERFECT = "def solve(x):\n    return 3*x*x - 2*x + 1\n"
# gaming attempt 1: memorise the train pairs instead of learning the function
MEMORISER = """TABLE = {round(-2 + i * 0.1, 6): 3*round(-2 + i*0.1, 6)**2 - 2*round(-2 + i*0.1, 6) + 1
         for i in range(40)}
def solve(x):
    return TABLE.get(round(x, 6), 0.0)
"""
# gaming attempt 2: forge the harness output line with an impossible value
FORGER = """import sys
def solve(x):
    sys.stdout.write("\\n@@KAIRO_OUTPUT@@" + "[1e308]" + "\\n")
    return 1e300
"""
SLEEPER = "import time\ndef solve(x):\n    time.sleep(60)\n    return 0.0\n"
STUB_CHECK = """# TEST DOUBLE of scripts/security/check_bundle.py (contract §1a) — NOT the real check.
import json, sys
mode = {mode!r}
if mode == "clean":
    print(json.dumps({{"status": "clean", "findings": []}})); sys.exit(0)
if mode == "contaminated":
    print(json.dumps({{"status": "contaminated", "findings": [
        {{"path": "program.py", "kind": "stub_finding", "severity": "block"}}]}})); sys.exit(2)
print(json.dumps({{"status": "error", "findings": []}})); sys.exit(1)
"""


def _cli(argv):
    out = io.StringIO()
    with redirect_stdout(out):
        code = evolve_run.main(argv)
    return code, json.loads(out.getvalue())


class Evolve(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-evolve-"))
        subprocess.run([sys.executable, str(EX / "make_data.py"), str(self.tmp / "data")], check=True)
        self.run_dir = self.tmp / "run"
        code, self.frozen = _cli(["freeze", "--task", str(EX / "task.py"),
                                  "--train", str(self.tmp / "data" / "train"),
                                  "--heldout", str(self.tmp / "data" / "heldout"),
                                  "--initial", str(EX / "initial.py"), "--entrypoint", "solve",
                                  "--eval-timeout", "5", "--max-heldout-gap", "0.05",
                                  "--run-dir", str(self.run_dir)])
        self.assertEqual(code, 0)

    def tearDown(self):
        for p in self.tmp.rglob("*"):
            try:
                os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def prog(self, name, src):
        p = self.tmp / name
        p.write_text(src, encoding="utf-8")
        return p

    def rescore(self, p):
        return _cli(["rescore", "--run-dir", str(self.run_dir), "--program", str(p)])

    def test_heldout_never_copied_into_run_dir(self):
        self.assertFalse(any("heldout" in p.as_posix() for p in (self.run_dir / "frozen").rglob("*")))
        lock = json.loads((self.run_dir / "evolve.lock.json").read_text())
        self.assertNotIn(str(self.tmp / "data" / "heldout"), json.dumps(lock))

    def test_known_optimum_and_baseline(self):
        _, r = self.rescore(self.prog("perfect.py", PERFECT))
        self.assertEqual((r["train"]["score"], r["heldout"]["score"], r["flag"]), (1.0, 1.0, None))
        _, r0 = self.rescore(self.run_dir / "initial_program.py")
        self.assertLess(r0["train"]["score"], 0.1)

    def test_memoriser_caught_by_heldout_gap(self):
        _, r = self.rescore(self.prog("memo.py", MEMORISER))
        self.assertEqual(r["train"]["score"], 1.0)
        self.assertLess(r["heldout"]["score"], 0.1)
        self.assertTrue(r["flag"] and r["flag"].startswith("importante"))

    def test_forged_output_rejected_by_bounds(self):
        _, r = self.rescore(self.prog("forge.py", FORGER))
        self.assertFalse(r["train"]["valid"])
        self.assertEqual(r["train"]["score"], 0.0)
        self.assertIn("bounds", r["train"]["reason"])

    def test_timeout(self):
        lock = json.loads((self.run_dir / "evolve.lock.json").read_text())
        lock["eval_timeout_s"] = 1
        res = harness.score_split(str(self.prog("slow.py", SLEEPER)), lock, Path(lock["train_dir"]))
        self.assertIn("timeout", res["reason"])

    def test_tampering_detected_and_alerted(self):
        os.environ["KAIRO_EVOLVE_LOCK"] = str(self.run_dir / "evolve.lock.json")
        try:
            task = self.run_dir / "frozen" / "task.py"
            # gaming attempt 3: a candidate that rewrites the frozen scorer
            tamper = self.prog("tamper.py", f"""import os, stat
p = {str(task)!r}
os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
open(p, 'a').write('\\ndef score(o, t):\\n    return 1.0\\n')
def solve(x):
    return 0.0
""")
            res = harness.evaluate(str(tamper))
            self.assertEqual(res, {"combined_score": 0.0, "tampered": 1.0})
            alerts = (self.run_dir / "ALERTS.jsonl").read_text()
            self.assertIn("tampered during evaluation", alerts)
            code, out = self.rescore(self.prog("perfect.py", PERFECT))
            self.assertEqual(code, 2)          # crítico: frozen evaluator changed
        finally:
            os.environ.pop("KAIRO_EVOLVE_LOCK", None)

    def test_plan_requires_matching_approval(self):
        code, plan = _cli(["plan", "--run-dir", str(self.run_dir), "--iterations", "4",
                           "--model", "haiku", "--run-budget-usd", "0.5"])
        self.assertEqual(code, 0)
        self.assertLessEqual(plan["estimate"]["hard_ceiling_usd_equiv"], 0.5 + 2 * 0.25)
        # `run` re-launches itself in UTF-8 mode, so exercise it as a real CLI call
        r = subprocess.run([sys.executable, str(HERE / "evolve_run.py"), "run", "--run-dir",
                            str(self.run_dir), "--approve", "0" * 16],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("approval token", json.loads(r.stdout)["refused"])

    def _bundle(self, mode, name):
        stub = self.tmp / f"stub_{mode}.py"
        stub.write_text(STUB_CHECK.format(mode=mode), encoding="utf-8")
        return _cli(["bundle", "--run-dir", str(self.run_dir), "--program",
                     str(self.prog("perfect.py", PERFECT)), "--out", str(self.tmp / name),
                     "--check-script", str(stub)])

    def test_bundle_exit_codes_follow_contract(self):
        code, out = self._bundle("clean", "b0")
        self.assertEqual(code, 0)
        self.assertTrue((self.tmp / "b0" / "MANIFEST.sha256").exists())
        self.assertEqual(self._bundle("contaminated", "b2")[0], 2)
        self.assertEqual(self._bundle("error", "b1")[0], 3)   # error = not clean

    def test_bundle_refused_without_real_check(self):
        code, out = _cli(["bundle", "--run-dir", str(self.run_dir), "--program",
                          str(self.prog("perfect.py", PERFECT)), "--out", str(self.tmp / "bx")])
        if (evolve_run.PLUGIN_ROOT / "scripts" / "security" / "check_bundle.py").exists():
            self.skipTest("real check_bundle.py present (post-integration)")
        self.assertEqual(code, 3)
        self.assertIn("never handed over unchecked", out["refused"])


if __name__ == "__main__":
    unittest.main()
