import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import compare_outputs as co
import sandbox_guard as sg
import tool_hash as th


def run_compare(ref, cand, *extra):
    with tempfile.TemporaryDirectory() as d:
        r, c = Path(d, "r.json"), Path(d, "c.json")
        r.write_text(json.dumps(ref))
        c.write_text(json.dumps(cand))
        return co.main(["--reference", str(r), "--candidate", str(c), *extra])


class TestCompareOutputs(unittest.TestCase):
    def test_within_relative_tolerance_passes(self):
        self.assertEqual(run_compare({"loss": [1.0, 2.0]}, {"loss": [1.02, 2.05]}, "--rtol", "0.03", "--atol", "0"), 0)

    def test_outside_tolerance_fails(self):
        self.assertEqual(run_compare({"loss": 1.0}, {"loss": 1.04}, "--rtol", "0.03", "--atol", "0"), 1)

    def test_tolerance_is_relative_to_the_reference(self):
        # |1.0 - 0.9705| = 0.0295 <= 0.03*1.0 passes; with roles swapped the bound
        # is 0.03*0.9705 = 0.029115 < 0.0295 and it fails
        res = co.Result()
        co.compare(1.0, 0.9705, 0.03, 0.0, False, res)
        self.assertEqual(res.n_failures, 0)
        res = co.Result()
        co.compare(0.9705, 1.0, 0.03, 0.0, False, res)
        self.assertEqual(res.n_failures, 1)

    def test_ints_are_exact(self):
        self.assertEqual(run_compare({"n": 3830}, {"n": 3831}, "--rtol", "0.5", "--atol", "10"), 1)

    def test_int_vs_float_compared_numerically(self):
        self.assertEqual(run_compare({"x": 1}, {"x": 1.0}, "--rtol", "0", "--atol", "0"), 0)

    def test_shape_and_key_mismatches_fail(self):
        self.assertEqual(run_compare({"a": [1.0, 2.0]}, {"a": [1.0]}, "--rtol", "1", "--atol", "1"), 1)
        self.assertEqual(run_compare({"a": 1.0}, {"a": 1.0, "b": 2.0}, "--rtol", "1", "--atol", "1"), 1)
        self.assertEqual(run_compare({"a": 1.0, "b": 2.0}, {"a": 1.0}, "--rtol", "1", "--atol", "1"), 1)

    def test_nan_needs_explicit_flag(self):
        res = co.Result()
        co.compare(math.nan, math.nan, 0.1, 0.1, False, res)
        self.assertEqual(res.n_failures, 1)
        res = co.Result()
        co.compare(math.nan, math.nan, 0.1, 0.1, True, res)
        self.assertEqual(res.n_failures, 0)
        res = co.Result()
        co.compare(1.0, math.nan, 0.1, 0.1, True, res)
        self.assertEqual(res.n_failures, 1)

    def test_empty_comparison_is_a_fail(self):
        self.assertEqual(run_compare({}, {}, "--rtol", "0", "--atol", "0"), 1)

    def test_ignore_drops_provenance_only(self):
        self.assertEqual(run_compare({"r": 1.0, "meta": {"wall_s": 5}}, {"r": 1.0, "meta": {"wall_s": 9}},
                                     "--rtol", "0", "--atol", "0", "--ignore", "meta.wall_s"), 0)


class TestToolHash(unittest.TestCase):
    def make_tool(self, d: Path):
        (d / "tool").mkdir()
        (d / "tool" / "m.py").write_bytes(b"def f():\n    return 1\n")
        (d / "reference").mkdir()
        (d / "reference" / "out.json").write_bytes(b'{"x": 1}\n')
        (d / "TOOL.md").write_text("---\nvalidation_hash: x\n---\n")

    def test_compute_then_verify_matches(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            self.make_tool(d)
            files = th.collect(d)
            self.assertNotIn("TOOL.md", files)
            h = th.validation_hash(th.manifest_text(files))
            self.assertEqual(th.main(["compute", str(d)]), 0)
            self.assertEqual(th.main(["verify", str(d), "--expected", h]), 0)

    def test_tool_md_edits_do_not_change_the_hash(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            self.make_tool(d)
            h1 = th.validation_hash(th.manifest_text(th.collect(d)))
            (d / "TOOL.md").write_text("---\nvalidation_hash: x\n---\n## Enmiendas\nnote\n")
            self.assertEqual(h1, th.validation_hash(th.manifest_text(th.collect(d))))

    def test_any_frozen_byte_change_is_a_mismatch(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            self.make_tool(d)
            th.main(["compute", str(d)])
            h = th.validation_hash((d / "MANIFEST.sha256").read_text())
            (d / "tool" / "m.py").write_bytes(b"def f():\r\n    return 1\r\n")   # CRLF rewrite
            self.assertEqual(th.main(["verify", str(d), "--expected", h]), 1)


class TestSandboxGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.vault = base / "Kairo" / "vault"
        (self.vault / "Papers").mkdir(parents=True)
        self.sandbox = base / "sandbox"
        self.sandbox.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_sandbox_inside_vault_is_refused(self):
        self.assertTrue(sg.location_problems(self.vault / "x", self.vault))
        self.assertTrue(sg.location_problems(self.vault.parent, self.vault))   # vault inside sandbox
        self.assertEqual(sg.location_problems(self.sandbox, self.vault), [])

    def test_vault_path_arguments_are_refused(self):
        root = self.vault
        bad = sg.vault_path_args(["python", "x.py", f"--data={self.vault / 'Papers' / 'P-0001.md'}"], root, self.sandbox)
        self.assertEqual(len(bad), 1)
        self.assertEqual(sg.vault_path_args(["python", "x.py", "--out", "./out"], root, self.sandbox), [])

    def test_credentials_are_dropped(self):
        os.environ["DEEPINFRA_TOKEN"] = "secret"
        os.environ["MY_API_KEY"] = "secret"
        try:
            env, dropped = sg.scrubbed_env(["WANDB_MODE=disabled"])
        finally:
            del os.environ["DEEPINFRA_TOKEN"], os.environ["MY_API_KEY"]
        self.assertNotIn("DEEPINFRA_TOKEN", env)
        self.assertNotIn("MY_API_KEY", env)
        self.assertEqual(env["WANDB_MODE"], "disabled")
        with self.assertRaises(ValueError):
            sg.scrubbed_env(["GITHUB_TOKEN=x"])

    def test_run_refuses_unapproved_and_runs_approved(self):
        cmd = [sys.executable, "-c", "print('hello')"]
        plan = self.sandbox / "approved-commands.json"
        plan.write_text(json.dumps({"approved_at": "t", "commands": [cmd]}))
        base = ["run", "--sandbox", str(self.sandbox), "--vault", str(self.vault), "--plan", str(plan), "--"]
        self.assertEqual(sg.main(base + [sys.executable, "-c", "print('other')"]), sg.REFUSED)
        self.assertEqual(sg.main(base + cmd), 0)
        rec = json.loads((self.sandbox / "runs.jsonl").read_text().splitlines()[-1])
        self.assertEqual(rec["exit"], 0)
        self.assertIn("hello", Path(rec["log"]).read_text())


if __name__ == "__main__":
    unittest.main()
