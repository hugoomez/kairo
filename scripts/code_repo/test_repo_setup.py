"""Tests for repo_setup.py and trace_code.py (invented repo, real git)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import repo_setup as rs  # noqa: E402
import trace_code as tc  # noqa: E402


def git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True)


class TestSetup(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-setup-"))
        self.vault = self.tmp / "vault"
        self.proj = self.vault / "Projects" / "codigo"
        self.proj.mkdir(parents=True)
        (self.proj / "_hub.md").write_text("---\nid: PROJ-901\n---\n", encoding="utf-8")
        (self.vault / "Papers").mkdir()
        # never touch the researcher's ~/.kairo from a test
        self.env = mock.patch.dict(os.environ, {"KAIRO_FINGERPRINT_DIR": str(self.tmp / "fp")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_scaffolds_commits_and_guards(self):
        repo = self.tmp / "nuevo"
        r = rs.create(repo, self.vault, self.proj, "Proyecto aplicado", "python")
        self.assertTrue(r["created"])
        for f in ("CONVENTIONS.md", ".gitignore", "README.md", ".git/hooks/pre-push"):
            self.assertTrue((repo / f).exists(), f)
        self.assertIn("Motivated-By", (repo / "CONVENTIONS.md").read_text(encoding="utf-8"))
        self.assertIn(".env", (repo / ".gitignore").read_text(encoding="utf-8"))
        self.assertEqual(git(repo, "log", "--format=%s").stdout.strip(), "Repositorio inicial (Kairo, plantilla aplicado)")
        self.assertEqual(git(repo, "remote").stdout.strip(), "")  # no remote at creation

    def test_refusals(self):
        with self.assertRaises(rs.Refused):
            rs.create(self.vault / "dentro", self.vault, self.proj, "x", "python")
        busy = self.tmp / "ocupado"
        busy.mkdir()
        (busy / "algo.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(rs.Refused):
            rs.create(busy, self.vault, self.proj, "x", "python")
        with self.assertRaises(rs.Refused):
            rs.link(busy, self.vault, self.proj)

    def test_link_guards_an_existing_repo(self):
        repo = self.tmp / "existente"
        repo.mkdir()
        git(repo, "init", "-q")
        self.assertFalse(rs.link(repo, self.vault, self.proj)["created"])
        self.assertTrue((repo / ".git" / "hooks" / "pre-push").exists())


class TestTrace(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="kairo-trace-"))
        for a in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            git(self.repo, *a)

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def commit(self, name, msg):
        (self.repo / name).write_text(msg, encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", msg)

    def test_trailers_map_code_to_science_and_untraced_work_is_visible(self):
        self.commit("model.py", "Modelo base\n\nMotivated-By: H-9001")
        self.commit("train.py", "Bucle de entrenamiento\n\nMotivated-By: H-9001, E-9002")
        self.commit("misc.py", "Limpieza")
        t = tc.build(self.repo)
        self.assertEqual((t["commits"], t["traced"]), (3, 2))
        self.assertEqual(t["by_ref"]["H-9001"]["files"], ["model.py", "train.py"])
        self.assertEqual([c["subject"] for c in t["by_ref"]["E-9002"]["commits"]], ["Bucle de entrenamiento"])
        self.assertEqual([c["subject"] for c in t["untraced"]], ["Limpieza"])
        text = tc.render(t, self.repo)
        self.assertIn("## H-9001", text)
        self.assertIn("## Sin motivación declarada", text)


if __name__ == "__main__":
    unittest.main()
