"""Tests for commit_hook.py. Run: python -m pytest scripts/bitacora

Builds an invented vault in a temp git repo (PROJ-900, H-9001, P-9001)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import action_log  # noqa: E402
import commit_hook as ch  # noqa: E402

COMMIT = {"tool_name": "Bash", "tool_input": {"command": 'git add -A && git commit -m "x"'}}


class TestCommitHook(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="kairo-hook-"))
        self.vault = self.repo / "vault"   # vault nested in the repo, as in a real setup
        (self.vault / "Projects" / "demo" / "Hipotesis").mkdir(parents=True)
        (self.vault / "Projects" / "otro").mkdir(parents=True)
        (self.vault / "Papers").mkdir()
        (self.vault / "Projects" / "demo" / "_hub.md").write_text("---\nid: PROJ-900\n---\n", encoding="utf-8")
        (self.vault / "Projects" / "otro" / "_hub.md").write_text("---\nid: PROJ-901\n---\n", encoding="utf-8")
        self.git("init", "-q")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        self.commit("base")

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def git(self, *a):
        return subprocess.run(["git", *a], cwd=self.repo, check=True, capture_output=True)

    def commit(self, msg):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)

    def log(self, slug):
        return self.vault / "Projects" / slug / "Bitacora" / "acciones.jsonl"

    def test_logs_commit_in_touched_project_with_refs_and_page(self):
        (self.vault / "Projects" / "demo" / "Hipotesis" / "H-9001 x.md").write_text("h", encoding="utf-8")
        self.commit("Add H-9001 (inventada)")
        self.assertEqual(ch.run(self.vault, COMMIT, 300), ["demo"])
        [e] = action_log.load(self.log("demo"))
        self.assertEqual((e["action"], e["event"], e["refs"]), ("commit", "commit", ["H-9001"]))
        self.assertEqual(e["summary"], "Add H-9001 (inventada)")
        self.assertTrue((self.log("demo").parent / f"{e['date']}.md").exists())
        self.assertFalse(self.log("otro").exists())

    def test_paper_commit_goes_to_the_papers_projects(self):
        (self.vault / "Papers" / "P-9001 x.md").write_text(
            "---\nid: P-9001\nprojects:\n  - PROJ-901\n---\n", encoding="utf-8")
        self.commit("Ingest P-9001")
        self.assertEqual(ch.run(self.vault, COMMIT, 300), ["otro"])

    def test_not_a_commit_command_is_ignored(self):
        (self.vault / "Projects" / "demo" / "n.md").write_text("n", encoding="utf-8")
        self.commit("c")
        payload = {"tool_name": "Bash", "tool_input": {"command": "git status"}}
        self.assertEqual(ch.run(self.vault, payload, 300), [])
        self.assertEqual(ch.run(self.vault, {"tool_name": "Read"}, 300), [])

    def test_old_head_is_not_attributed(self):
        (self.vault / "Projects" / "demo" / "n.md").write_text("n", encoding="utf-8")
        self.commit("c")
        self.assertEqual(ch.run(self.vault, COMMIT, -1), [])

    def test_same_commit_is_logged_once(self):
        (self.vault / "Projects" / "demo" / "n.md").write_text("n", encoding="utf-8")
        self.commit("c")
        ch.run(self.vault, COMMIT, 300)
        self.assertEqual(ch.run(self.vault, COMMIT, 300), [])
        self.assertEqual(len(action_log.load(self.log("demo"))), 1)

    def test_bitacora_only_commit_is_skipped(self):
        (self.vault / "Projects" / "demo" / "n.md").write_text("n", encoding="utf-8")
        self.commit("c")
        ch.run(self.vault, COMMIT, 300)
        self.commit("Bitácora")
        self.assertEqual(ch.run(self.vault, COMMIT, 300), [])

    def test_main_never_fails(self):
        import io
        old = sys.stdin
        sys.stdin = io.StringIO(json.dumps(COMMIT))
        try:
            self.assertEqual(ch.main(["--vault", str(self.repo / "missing")]), 0)
        finally:
            sys.stdin = old

    def test_command_detection(self):
        for cmd, want in [("git commit -m x", True), ('git -C vault commit -q -m "y"', True),
                          ("git log --grep commit", False), ("echo commit", False)]:
            with self.subTest(cmd=cmd):
                got = ch.is_commit_command({"tool_name": "Bash", "tool_input": {"command": cmd}})
                self.assertEqual(got, want)


if __name__ == "__main__":
    unittest.main()
