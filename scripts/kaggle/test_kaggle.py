"""Tests for bundle_contract.py and kaggle_entry.py. Run: python -m pytest scripts/kaggle

The entry script is executed for real, locally, against a simulated Kaggle
layout (input / working / scratch folders set by environment variables).
Invented experiment and data only (E-9001, H-9001)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bundle_contract as bc  # noqa: E402

TRAIN = """import json, os, sys, time
os.makedirs("results", exist_ok=True)
if "--sleep" in sys.argv:
    time.sleep(30)
json.dump({"accuracy": 0.75, "seed": int(sys.argv[sys.argv.index("--seed") + 1])}, open("results/out.json", "w"))
print("trained")
if "--fail" in sys.argv:
    sys.exit(2)
"""


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write_manifest(bundle: Path) -> None:
    lines = [f"{sha(f)}  {f.relative_to(bundle).as_posix()}"
             for f in sorted(bundle.rglob("*")) if f.is_file() and f.name != "MANIFEST.sha256"]
    (bundle / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")


def description(**kw) -> dict:
    d = {"schema": bc.SCHEMA, "experiment": "E-9001", "hypothesis": "H-9001", "run_id": "E-9001-kaggle-1",
         "role": "confirmatory", "rung": None, "attempt": 1, "retry_of": None, "seeds": [7], "cell": "a",
         "plan_index": 0, "config_hash": "unknown", "entry": ["python", "train.py", "--seed", "7"],
         "accelerator": "NvidiaTeslaT4", "enable_internet": False, "max_wallclock_s": 600,
         "estimated_gpu_h": 0.5, "outputs": ["results/*.json"], "notes": None}
    d.update(kw)
    return d


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-kaggle-"))
        self.run_dir = self.tmp / "run"
        self.bundle = self.run_dir / "bundle"
        self.bundle.mkdir(parents=True)
        (self.bundle / "train.py").write_text(TRAIN, encoding="utf-8")
        (self.bundle / "E-9001.deps.txt").write_text("numpy==2.0.0\n", encoding="utf-8")
        write_manifest(self.bundle)
        self.describe()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def describe(self, **kw):
        (self.run_dir / "bundle.json").write_text(json.dumps(description(**kw)), encoding="utf-8")


class TestCheck(Base):
    def test_a_clean_bundle_passes_with_its_manifest_hash(self):
        r = bc.check(self.run_dir)
        self.assertTrue(r["ok"], r["problems"])
        self.assertEqual(r["manifest_sha256"], sha(self.bundle / "MANIFEST.sha256"))
        self.assertEqual({f["path"] for f in r["files"]}, {"train.py", "E-9001.deps.txt"})
        self.assertEqual(r["check_bundle"]["exit"], 0)

    def test_manifest_drift_is_caught(self):
        (self.bundle / "train.py").write_text(TRAIN + "\n# edited\n", encoding="utf-8")
        (self.bundle / "extra.py").write_text("x = 1\n", encoding="utf-8")
        probs = bc.check(self.run_dir)["problems"]
        self.assertIn("sha256 mismatch: train.py", probs)
        self.assertIn("not in the manifest: extra.py", probs)

    def test_a_secret_in_the_bundle_blocks_it(self):
        fake_key = "0123456789abcdef" * 2  # built at runtime: no key-shaped literal in the source
        (self.bundle / "kaggle.json").write_text(json.dumps({"username": "u", "key": fake_key}), encoding="utf-8")
        write_manifest(self.bundle)
        r = bc.check(self.run_dir)
        self.assertFalse(r["ok"])
        self.assertTrue(any("check_bundle.py" in p for p in r["problems"]))

    def test_description_is_validated(self):
        self.describe(role="maybe", entry=["rm", "-rf", "/"], max_wallclock_s=10**6, enable_internet="no", seeds="7")
        probs = " ".join(bc.check(self.run_dir)["problems"])
        for field in ("role", "entry", "max_wallclock_s", "enable_internet", "seeds"):
            self.assertIn(field, probs)

    def test_pack_refuses_a_bundle_that_does_not_check(self):
        out = self.tmp / "b.tar.gz"
        self.assertEqual(bc.main(["pack", "--run-dir", str(self.run_dir), "--out", str(out)]), 0)
        self.assertTrue(out.exists())
        (self.bundle / "train.py").write_text("changed", encoding="utf-8")
        self.assertEqual(bc.main(["pack", "--run-dir", str(self.run_dir), "--out", str(self.tmp / "c.tar.gz")]), 3)


class TestEntryScript(Base):
    """Runs kaggle_entry.py as the backend would render it, locally."""

    def render(self, **cfg) -> Path:
        config = {"run_id": "E-9001-kaggle-1", "entry": ["python", "train.py", "--seed", "7"],
                  "max_wallclock_s": 60, "outputs": ["results/*.json"]}
        config.update(cfg)
        b64 = base64.b64encode(json.dumps(config).encode()).decode()
        script = self.tmp / "kaggle_entry.py"
        script.write_text((HERE / "kaggle_entry.py").read_text(encoding="utf-8").replace("__KAIRO_CONFIG_B64__", b64),
                          encoding="utf-8")
        return script

    def run_entry(self, script: Path, input_dir: Path) -> dict:
        working = self.tmp / "working"
        env = {**os.environ, "KAIRO_KAGGLE_INPUT": str(input_dir), "KAIRO_KAGGLE_WORKING": str(working),
               "KAIRO_KAGGLE_TMP": str(self.tmp / "scratch" / "bundle")}
        proc = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.working = working
        return json.loads((working / "result.json").read_text(encoding="utf-8"))

    def dataset_with_folder(self) -> Path:
        ds = self.tmp / "input" / "kairo-e-9001"
        shutil.copytree(self.bundle, ds / "bundle")
        return self.tmp / "input"

    def test_runs_the_bundle_and_returns_verifiable_outputs(self):
        r = self.run_entry(self.render(), self.dataset_with_folder())
        self.assertEqual((r["status"], r["manifest_ok"], r["exit_code"], r["timed_out"]), ("ok", True, 0, False))
        self.assertEqual([o["path"] for o in r["outputs"]], ["results/out.json"])
        self.assertIn("trained", (self.working / "run.log").read_text(encoding="utf-8"))
        verified = bc.outputs(self.working)
        self.assertTrue(verified["ok"], verified["problems"])
        self.assertFalse((self.working / "train.py").exists(), "the bundle must not land in /kaggle/working")

    def test_a_packed_bundle_is_extracted(self):
        ds = self.tmp / "input" / "kairo-e-9001"
        bc.pack(self.run_dir, ds / "bundle.tar.gz")
        r = self.run_entry(self.render(), self.tmp / "input")
        self.assertEqual(r["status"], "ok")

    def test_a_tampered_bundle_runs_nothing(self):
        inp = self.dataset_with_folder()
        (inp / "kairo-e-9001" / "bundle" / "train.py").write_text("print('not the frozen code')", encoding="utf-8")
        r = self.run_entry(self.render(), inp)
        self.assertEqual((r["status"], r["manifest_ok"], r["exit_code"]), ("manifest_mismatch", False, None))
        self.assertFalse((self.working / "run.log").exists())
        self.assertFalse(bc.outputs(self.working)["ok"])

    def test_the_wall_clock_limit_stops_the_run(self):
        r = self.run_entry(self.render(entry=["python", "train.py", "--seed", "7", "--sleep"], max_wallclock_s=2),
                           self.dataset_with_folder())
        self.assertEqual((r["status"], r["timed_out"]), ("timed_out", True))
        self.assertIn("wall-clock limit", (self.working / "run.log").read_text(encoding="utf-8"))

    def test_a_failing_command_is_reported_not_hidden(self):
        r = self.run_entry(self.render(entry=["python", "train.py", "--seed", "7", "--fail"]), self.dataset_with_folder())
        self.assertEqual((r["status"], r["exit_code"]), ("failed", 2))

    def test_no_bundle_in_the_dataset(self):
        (self.tmp / "empty").mkdir()
        r = self.run_entry(self.render(), self.tmp / "empty")
        self.assertEqual(r["status"], "bundle_not_found")


if __name__ == "__main__":
    unittest.main()
