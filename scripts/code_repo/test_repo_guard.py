"""Tests for repo_guard.py. Run: python -m pytest scripts/code_repo

Invented vault text and code only; a real git repo with a real pre-push hook
pushing to a local bare remote."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import repo_guard as rg  # noqa: E402

PAPER_TEXT = ("Los widgets azules giran más rápido que los rojos en todas las condiciones medidas "
              "porque su eje tiene menos fricción y el lubricante inventado reduce el calor generado "
              "durante cada rotación del mecanismo principal del dispositivo experimental.")


def git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-guard-"))
        self.vault = self.tmp / "vault"
        (self.vault / "Papers").mkdir(parents=True)
        self.proj = self.vault / "Projects" / "codigo"
        self.proj.mkdir(parents=True)
        (self.proj / "_hub.md").write_text("---\nid: PROJ-901\ncode_visibility: private\n---\n", encoding="utf-8")
        (self.vault / "Papers" / "P-9001 x.md").write_text(f"---\nid: P-9001\n---\n\n## Texto completo\n\n{PAPER_TEXT}\n", encoding="utf-8")
        self.repo = self.tmp / "code"
        self.repo.mkdir()
        for a in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            git(self.repo, *a)
        self.remote = self.tmp / "remote.git"
        git(self.tmp, "init", "-q", "--bare", str(self.remote))
        git(self.repo, "remote", "add", "origin", str(self.remote))
        self.index = self.tmp / "fp.json"
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        for m in rg.AGENT_MARKERS:
            os.environ.pop(m, None)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def commit(self, files: dict[str, str]):
        for name, text in files.items():
            p = self.repo / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "c")

    def install(self):
        return rg.main(["install", "--repo", str(self.repo), "--vault", str(self.vault),
                        "--project-dir", str(self.proj), "--index", str(self.index)])

    def push(self):
        return git(self.repo, "push", "-q", "origin", "main")


class TestPrePush(Base):
    def test_clean_code_is_pushed(self):
        self.assertEqual(self.install(), 0)
        self.commit({"src/model.py": "def f(x):\n    return 2 * x\n", "README.md": "# Código del proyecto\n"})
        r = self.push()
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_paper_text_is_blocked_naming_the_note(self):
        self.install()
        self.commit({"docs/notes.md": f"Resumen: {PAPER_TEXT}\n"})
        r = self.push()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("P-9001", r.stderr)
        self.assertIn("verbatim_overlap", r.stderr)

    def test_a_secret_is_blocked_and_cannot_be_allowed(self):
        self.install()
        self.commit({"config.py": 'API_TOKEN = "' + "a1B2c3D4" * 5 + '"\n'})
        self.assertNotEqual(self.push().returncode, 0)
        rg.main(["allow", "--repo", str(self.repo), "--file", "config.py", "--reason", "no debería funcionar para secretos"])
        self.assertNotEqual(self.push().returncode, 0)

    def test_an_allowed_quotation_passes_only_for_that_exact_content(self):
        self.install()
        self.commit({"docs/cita.md": f"{PAPER_TEXT}\n"})
        self.assertNotEqual(self.push().returncode, 0)
        self.assertEqual(rg.main(["allow", "--repo", str(self.repo), "--file", "docs/cita.md",
                                  "--reason", "cita pública con permiso del autor"]), 0)
        self.assertEqual(self.push().returncode, 0)
        self.commit({"docs/cita.md": f"{PAPER_TEXT}\nY una línea más que cambia el contenido.\n"})
        self.assertNotEqual(self.push().returncode, 0)

    def test_override_is_refused_in_an_agent_session(self):
        self.commit({"docs/cita.md": f"{PAPER_TEXT}\n"})
        for m in rg.AGENT_MARKERS:
            with mock.patch.dict(os.environ, {m: "1"}):
                self.assertEqual(rg.main(["allow", "--repo", str(self.repo), "--file", "docs/cita.md",
                                          "--reason", "un agente intentándolo"]), 3)
        self.assertFalse((self.repo / ".git" / "kairo-allow.json").exists())

    def test_a_repository_inside_the_vault_is_refused(self):
        inner = self.vault / "code"
        inner.mkdir()
        git(inner, "init", "-q")
        self.assertEqual(rg.main(["install", "--repo", str(inner), "--vault", str(self.vault),
                                  "--project-dir", str(self.proj), "--index", str(self.index)]), 3)

    def test_a_remote_pointing_at_the_vault_is_blocked(self):
        git(self.tmp, "init", "-q", "--bare", str(self.vault / "leak.git"))
        git(self.repo, "remote", "add", "leak", str(self.vault / "leak.git"))
        probs = rg.location_problems(self.repo, self.vault)
        self.assertTrue(any("points at the vault" in p for p in probs), probs)

    def test_scan_reports_without_pushing(self):
        self.commit({"docs/notes.md": f"{PAPER_TEXT}\n", "ok.py": "x = 1\n"})
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = rg.main(["scan", "--repo", str(self.repo), "--vault", str(self.vault), "--project-dir", str(self.proj),
                            "--index", str(self.index), "--all"])
        res = json.loads(buf.getvalue())
        self.assertEqual(code, 3)
        self.assertEqual(res["blocking"][0]["path"], "docs/notes.md")


class TestIndexFreshness(Base):
    def test_a_note_newer_than_the_index_triggers_a_rebuild(self):
        import time
        n = len(rg.load_index(self.index, self.vault)["shingles"])
        time.sleep(1.1)
        (self.vault / "Papers" / "P-9002 y.md").write_text(
            "Un segundo paper inventado describe como los engranajes verdes se desgastan antes que los amarillos "
            "cuando la temperatura del taller supera los treinta grados durante la jornada.\n", encoding="utf-8")
        self.assertGreater(len(rg.load_index(self.index, self.vault)["shingles"]), n)

    def test_the_default_index_is_per_vault(self):
        with mock.patch.dict(os.environ, {"KAIRO_FINGERPRINT_DIR": str(self.tmp / "fp")}):
            a = rg.default_index(self.vault)
            self.assertNotEqual(a, rg.default_index(self.tmp / "otro"))
            self.assertTrue(str(a).startswith(str(self.tmp)))


class TestFingerprints(unittest.TestCase):
    def test_shingles_ignore_case_accents_and_punctuation(self):
        a = rg.shingles("Los Widgets AZULES giran, más rápido que los rojos en todas las condiciones medidas.")
        b = rg.shingles("los widgets azules giran mas rapido que los rojos en todas las condiciones medidas")
        self.assertEqual(a, b)
        self.assertEqual(rg.shingles("demasiado corto"), set())

    def test_short_common_phrases_do_not_trigger(self):
        idx = {"notes": ["x.md"], "shingles": {h: 0 for h in rg.shingles(PAPER_TEXT)}}
        self.assertEqual(rg.overlap("los widgets azules giran más rápido", idx), [])
        self.assertTrue(rg.overlap(PAPER_TEXT, idx))


if __name__ == "__main__":
    unittest.main()
