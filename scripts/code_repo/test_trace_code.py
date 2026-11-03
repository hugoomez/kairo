"""Tests for trace_code.py. Run: python -m pytest scripts/code_repo

Invented repos and ids only, in a temp dir; no network."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trace_code as tc  # noqa: E402


def git(cwd, *a):
    r = subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r


def make_repo(path: Path, commits: list[tuple[str, str]]) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q")
    git(path, "config", "user.email", "t@example.org")
    git(path, "config", "user.name", "T")
    git(path, "config", "commit.gpgsign", "false")
    for i, (subject, trailer) in enumerate(commits):
        (path / f"f{i}.txt").write_text(str(i), encoding="utf-8")
        git(path, "add", ".")
        msg = subject + (f"\n\nMotivated-By: {trailer}" if trailer else "")
        git(path, "commit", "-q", "-m", msg)
    return path


class ArchivedTrace(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-trace-"))
        self.current = make_repo(self.tmp / "current", [("widget loop", "H-9002"), ("tidy", "")])
        self.old = make_repo(self.tmp / "old", [("first spinner", "H-9001"), ("old chore", "")])

    def test_archived_repo_commits_keep_their_trace(self):
        t = tc.build(self.current, 500, archived=[self.old])
        self.assertIn("H-9001", t["by_ref"])
        self.assertEqual(t["by_ref"]["H-9001"]["commits"][0]["repo"], str(self.old))
        self.assertNotIn("repo", t["by_ref"]["H-9002"]["commits"][0])
        self.assertEqual(t["archived_missing"], [])

    def test_archived_counts_do_not_change_current_header(self):
        t = tc.build(self.current, 500, archived=[self.old])
        self.assertEqual((t["commits"], t["traced"]), (2, 1))
        self.assertEqual([c["subject"] for c in t["untraced"]], ["tidy"])

    def test_cli_reads_code_repo_archivo_from_hub(self):
        proj = self.tmp / "proj"
        proj.mkdir()
        (proj / "_hub.md").write_text(
            "---\nid: PROJ-902\ncode_repo_archivo: " + json.dumps([str(self.old)]) + "\n---\n", encoding="utf-8")
        out = subprocess.run([sys.executable, str(Path(tc.__file__)), "--repo", str(self.current),
                              "--project-dir", str(proj)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(out.returncode, 0, out.stderr)
        t = json.loads(out.stdout)
        self.assertIn("H-9001", t["by_ref"])

    def test_missing_archived_repo_is_reported_not_fatal(self):
        gone = self.tmp / "gone"
        t = tc.build(self.current, 500, archived=[gone])
        self.assertEqual(t["archived_missing"], [str(gone)])
        self.assertEqual(t["commits"], 2)


if __name__ == "__main__":
    unittest.main()
