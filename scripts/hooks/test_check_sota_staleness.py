"""Tests for check_sota_staleness.py (invented vault in a temp dir, no network)."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "check_sota_staleness.py"
sys.path.insert(0, str(HERE))
import check_sota_staleness as css  # noqa: E402


class Staleness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-stale-"))
        self.vault = self.tmp / "vault"
        self.proj = self.vault / "Projects" / "proyecto-inventado"
        self.proj.mkdir(parents=True)
        (self.vault / "Papers").mkdir()
        (self.proj / "Estado-del-arte.md").write_text("---\ngenerated: 2030-01-01\n---\n\n## X\n", encoding="utf-8")
        self.paths = []
        for i in range(5):
            p = self.vault / "Papers" / f"P-098{i} inventado {i}.md"
            p.write_text(f"---\nid: P-098{i}\nprojects: [PROJ-980]\nadded: 2031-0{i + 1}-01\n---\n", encoding="utf-8")
            self.paths.append(p)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def hub(self, extra: str = "") -> None:
        (self.proj / "_hub.md").write_text(f"---\nid: PROJ-980\n{extra}---\n", encoding="utf-8")

    def run_hook(self) -> str:
        payload = {"tool_input": {"file_path": self.paths[-1].as_posix()}, "cwd": str(self.vault)}
        r = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(payload), capture_output=True,
                           text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0)
        return r.stdout

    def test_stale_map_is_flagged(self):
        for extra in ("", "sota_map: true\n"):
            self.hub(extra)
            out = self.run_hook()
            self.assertIn("PROJ-980", json.loads(out)["systemMessage"])

    def test_diferido_is_never_flagged(self):
        for extra in ("sota_map: diferido\n", "sota_map: diferido  # light start\n", 'sota_map: "diferido"\n'):
            self.hub(extra)
            self.assertEqual(self.run_hook().strip(), "", extra)

    def test_diferido_without_a_map_is_quiet(self):
        (self.proj / "Estado-del-arte.md").unlink()
        self.hub("sota_map: diferido\n")
        self.assertEqual(self.run_hook().strip(), "")

    def test_sota_deferred_helper(self):
        self.assertTrue(css.sota_deferred({"sota_map": "Diferido"}))
        self.assertFalse(css.sota_deferred({"sota_map": "true"}))
        self.assertFalse(css.sota_deferred({}))


if __name__ == "__main__":
    unittest.main()
