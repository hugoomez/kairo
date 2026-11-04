"""Tests for repo_guard.py. Run: python -m pytest scripts/code_repo

Invented vault text and code only; a real git repo with a real pre-push hook
pushing to a local bare remote."""

from __future__ import annotations

import contextlib
import io
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


class TestNewBranches(Base):
    """A branch that does not exist on the remote yet is compared with the
    main branch, from the merge base, not scanned whole."""

    def seed_main(self, files):
        self.commit(files)
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "fetch", "-q", "origin")

    def sha(self, rev="HEAD"):
        return git(self.repo, "rev-parse", rev).stdout.strip()

    def test_new_branch_only_scans_what_differs_from_main(self):
        self.seed_main({"viejo.md": f"{PAPER_TEXT}\n"})
        git(self.repo, "checkout", "-q", "-b", "feat")
        self.commit({"limpio.py": "x = 1\n"})
        self.assertEqual(rg.changed_files(self.repo, self.sha(), rg.ZERO), ["limpio.py"])

    def test_new_branch_is_compared_at_the_merge_base(self):
        self.seed_main({"base.py": "a = 1\n"})
        git(self.repo, "checkout", "-q", "-b", "feat")
        self.commit({"rama.py": "b = 2\n"})
        branch_sha = self.sha()
        git(self.repo, "checkout", "-q", "main")
        self.seed_main({"avance.py": "c = 3\n"})
        self.assertEqual(rg.changed_files(self.repo, branch_sha, rg.ZERO), ["rama.py"])

    def test_new_branch_without_remote_main_scans_whole_tree(self):
        self.commit({"a.py": "a = 1\n", "b.py": "b = 2\n"})
        self.assertIsNone(rg.main_ref(self.repo))
        self.assertEqual(set(rg.changed_files(self.repo, self.sha(), rg.ZERO)), {"a.py", "b.py"})

    def test_main_ref_falls_back_to_origin_master(self):
        self.commit({"a.py": "a = 1\n"})
        git(self.repo, "update-ref", "refs/remotes/origin/master", self.sha())
        self.assertEqual(rg.main_ref(self.repo), "origin/master")

    def test_overlap_in_a_file_the_branch_changes_still_blocks(self):
        self.seed_main({"base.py": "a = 1\n"})
        git(self.repo, "checkout", "-q", "-b", "feat")
        self.commit({"docs/cita.md": f"{PAPER_TEXT}\n"})
        index = rg.load_index(self.index, self.vault)
        files = rg.changed_files(self.repo, self.sha(), rg.ZERO)
        res = rg.scan(self.repo, self.vault, self.proj, index, self.sha(), files, None)
        self.assertFalse(res["ok"])
        self.assertEqual(res["blocking"][0]["kind"], "verbatim_overlap")

    def test_scan_rev_uses_that_branchs_upstream_not_heads(self):
        import io
        from contextlib import redirect_stdout
        self.seed_main({"base.py": "a = 1\n"})
        git(self.repo, "checkout", "-q", "-b", "feat")
        self.commit({"x.py": "x = 1\n"})
        git(self.repo, "push", "-q", "-u", "origin", "feat")
        self.commit({"y.py": "y = 1\n"})
        git(self.repo, "checkout", "-q", "main")  # HEAD's upstream is origin/main
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = rg.main(["scan", "--repo", str(self.repo), "--vault", str(self.vault),
                            "--project-dir", str(self.proj), "--index", str(self.index), "--rev", "feat"])
        res = json.loads(buf.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(res["files"], 1)

    def test_scan_of_a_branch_without_upstream_compares_with_main(self):
        import io
        from contextlib import redirect_stdout
        self.seed_main({"viejo.md": f"{PAPER_TEXT}\n"})
        git(self.repo, "checkout", "-q", "-b", "feat")
        self.commit({"limpio.py": "x = 1\n"})
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = rg.main(["scan", "--repo", str(self.repo), "--vault", str(self.vault),
                            "--project-dir", str(self.proj), "--index", str(self.index), "--rev", "feat"])
        self.assertEqual(code, 0, buf.getvalue())
        self.assertEqual(json.loads(buf.getvalue())["files"], 1)


class TestScanRange(Base):
    """scan --range: everything a push of those commits uploads, not only the tip."""

    def setUp(self):
        super().setUp()
        self.commit({"ok.py": "x = 1\n"})
        self.base = git(self.repo, "rev-parse", "HEAD").stdout.strip()

    def head(self):
        return git(self.repo, "rev-parse", "HEAD").stdout.strip()

    def commit_msg(self, name, text, message):
        (self.repo / name).write_text(text, encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", message)

    def scan_range(self, rng=None, *extra):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = rg.main(["scan", "--repo", str(self.repo), "--vault", str(self.vault), "--project-dir", str(self.proj),
                            "--index", str(self.index), f"--range={rng or f'{self.base}..{self.head()}'}", *extra])
        return code, (json.loads(buf.getvalue()) if buf.getvalue().strip() else None)

    def test_clean_range_passes(self):
        self.commit({"pieza.py": "y = 2\n"})
        code, res = self.scan_range()
        self.assertEqual(code, 0)
        self.assertTrue(res["ok"])
        self.assertEqual(res["findings"], [])

    def test_text_added_in_an_intermediate_commit_and_removed_at_the_tip_blocks(self):
        self.commit({"docs/cita.md": f"{PAPER_TEXT}\n"})
        self.commit({"docs/cita.md": "ya no hay nada\n"})
        code, res = self.scan_range()
        self.assertEqual(code, 3)
        self.assertEqual([(f["kind"], f["path"]) for f in res["blocking"]], [("verbatim_overlap", "docs/cita.md")])

    def test_a_file_added_and_deleted_inside_the_range_blocks(self):
        self.commit({"borrador.md": f"{PAPER_TEXT}\n"})
        git(self.repo, "rm", "-q", "borrador.md")
        git(self.repo, "commit", "-qm", "quita el borrador")
        code, res = self.scan_range()
        self.assertEqual(code, 3)
        self.assertEqual(res["blocking"][0]["path"], "borrador.md")

    def test_a_secret_in_an_intermediate_blob_blocks_with_its_real_path(self):
        self.commit({"config.py": 'API_TOKEN = "' + "a1B2c3D4" * 5 + '"\n'})
        self.commit({"config.py": "API_TOKEN = None\n"})
        code, res = self.scan_range()
        self.assertEqual(code, 3)
        self.assertIn("config.py", [f["path"] for f in res["blocking"]])

    def test_a_commit_message_body_with_vault_text_blocks_and_cannot_be_allowed(self):
        self.commit_msg("ok2.py", "z = 1\n", f"Asunto corto\n\n{PAPER_TEXT}\n")
        code, res = self.scan_range()
        self.assertEqual(code, 3)
        f = res["blocking"][0]
        self.assertEqual(f["kind"], "verbatim_overlap")
        self.assertIs(f["overridable"], False)
        self.assertEqual(f["commit"], self.head())
        self.assertIsNone(f["path"])

    def test_a_secret_in_a_commit_message_blocks(self):
        self.commit_msg("ok2.py", "z = 1\n", 'Asunto\n\nAPI_TOKEN = "' + "a1B2c3D4" * 5 + '"\n')
        code, res = self.scan_range()
        self.assertEqual(code, 3)
        f = res["blocking"][0]
        self.assertIs(f["overridable"], False)
        self.assertEqual(f["commit"], self.head())

    def test_an_allowed_tip_passes_but_an_earlier_version_still_blocks(self):
        self.commit({"docs/cita.md": f"{PAPER_TEXT}\n"})
        rg.main(["allow", "--repo", str(self.repo), "--file", "docs/cita.md", "--reason", "cita legítima"])
        code, res = self.scan_range()
        self.assertEqual(code, 0, res)
        self.assertTrue(res["findings"][0]["allowed"])
        self.commit({"docs/cita.md": f"{PAPER_TEXT}\nUna línea más.\n"})
        rg.main(["allow", "--repo", str(self.repo), "--file", "docs/cita.md", "--reason", "cita legítima"])
        code, res = self.scan_range()
        self.assertEqual(code, 3)
        self.assertEqual(len(res["blocking"]), 1)

    def test_the_same_blob_in_two_commits_is_reported_once(self):
        self.commit({"docs/cita.md": f"{PAPER_TEXT}\n"})
        self.commit({"docs/cita.md": "nada\n"})
        self.commit({"docs/cita.md": f"{PAPER_TEXT}\n"})
        _, res = self.scan_range()
        self.assertEqual(len(res["blocking"]), 1)

    def test_oversized_and_binary_blobs_skip_the_overlap_check(self):
        self.commit({"grande.txt": f"{PAPER_TEXT}\n" + "relleno inventado\n" * ((rg.MAX_FILE_BYTES // 18) + 1)})
        (self.repo / "dato.bin").write_bytes(b"\x00\x01" + PAPER_TEXT.encode())
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "binario")
        _, res = self.scan_range()
        self.assertEqual([f for f in res["findings"] if f["kind"] == "verbatim_overlap"], [])

    def test_merge_and_root_commits_are_covered(self):
        git(self.repo, "checkout", "-q", "-b", "lado")
        self.commit({"lado.md": f"{PAPER_TEXT}\n"})
        git(self.repo, "checkout", "-q", "main")
        self.commit({"otro.py": "o = 1\n"})
        git(self.repo, "merge", "-q", "--no-edit", "lado")
        self.assertEqual(self.scan_range()[0], 3)
        git(self.repo, "checkout", "-q", "--orphan", "suelta")
        self.commit({"suelta.md": f"{PAPER_TEXT}\n"})
        code, res = self.scan_range()
        self.assertEqual(code, 3)
        self.assertIn("suelta.md", [f["path"] for f in res["blocking"]])

    def test_bad_range_or_flags_are_refused(self):
        self.commit({"pieza.py": "y = 2\n"})
        for bad in ("--upload-pack=x..y", "HEAD", "a b..c", "no-existe..tampoco"):
            self.assertEqual(self.scan_range(bad)[0], 3, bad)
        self.assertEqual(self.scan_range(None, "--all")[0], 3)
        self.assertEqual(self.scan_range(None, "--rev", "main")[0], 3)

    def test_an_empty_range_is_refused_not_passed(self):
        """Zero commits scanned says nothing about what a push would upload."""
        code, res = self.scan_range(f"{self.base}..{self.base}")
        self.assertEqual(code, 3)
        self.assertIsNone(res)

    def test_a_commit_hidden_by_a_replace_graft_is_still_scanned(self):
        self.commit({"pieza.py": "y = 2\n"})
        first = self.head()
        self.commit({"docs/cita.md": f"{PAPER_TEXT}\n"})
        self.commit({"docs/cita.md": "ya no hay nada\n"})
        tip = self.head()
        self.assertEqual(git(self.repo, "replace", "--graft", tip, first).returncode, 0)
        code, res = self.scan_range(f"{self.base}..{tip}")
        self.assertEqual(code, 3)
        self.assertEqual(res["commits"], 3)
        self.assertIn(("verbatim_overlap", "docs/cita.md"), [(f["kind"], f["path"]) for f in res["blocking"]])

    def test_a_blob_hidden_by_replace_is_still_scanned(self):
        self.commit({"limpio.md": "nada que ver\n"})
        clean = git(self.repo, "rev-parse", "HEAD:limpio.md").stdout.strip()
        self.commit({"docs/cita.md": f"{PAPER_TEXT}\n"})
        dirty = git(self.repo, "rev-parse", "HEAD:docs/cita.md").stdout.strip()
        self.assertEqual(git(self.repo, "replace", dirty, clean).returncode, 0)
        code, res = self.scan_range()
        self.assertEqual(code, 3)
        self.assertIn(("verbatim_overlap", "docs/cita.md"), [(f["kind"], f["path"]) for f in res["blocking"]])

    def test_a_blob_hidden_by_replace_is_still_seen_at_the_tip(self):
        """The same for the plain scan, the one the pre-push check shares."""
        import io
        from contextlib import redirect_stdout
        self.commit({"limpio.md": "nada que ver\n"})
        clean = git(self.repo, "rev-parse", "HEAD:limpio.md").stdout.strip()
        self.commit({"docs/cita.md": f"{PAPER_TEXT}\n"})
        dirty = git(self.repo, "rev-parse", "HEAD:docs/cita.md").stdout.strip()
        self.assertEqual(git(self.repo, "replace", dirty, clean).returncode, 0)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = rg.main(["scan", "--repo", str(self.repo), "--vault", str(self.vault), "--project-dir", str(self.proj),
                            "--index", str(self.index), "--all"])
        self.assertEqual(code, 3)
        self.assertIn("docs/cita.md", [f["path"] for f in json.loads(buf.getvalue())["blocking"]])

    def test_the_pre_push_check_does_not_change(self):
        """check does not read message bodies: the same as before --range existed."""
        self.install()
        self.assertEqual(self.push().returncode, 0)
        self.commit_msg("ok2.py", "z = 1\n", f"Asunto\n\n{PAPER_TEXT}\n")
        self.assertEqual(self.push().returncode, 0)


class TestFingerprintScope(Base):
    def test_fingerprints_exclude_project_repositorio_folder(self):
        pr = self.vault / "Projects" / "demo" / "Repositorio" / "acme-solver" / "PRs"
        pr.mkdir(parents=True)
        (pr / "PR-0001.md").write_text("Nota derivada de un PR inventado.\n", encoding="utf-8")
        hip = self.vault / "Projects" / "demo" / "Hipotesis"
        hip.mkdir(parents=True)
        (hip / "x.md").write_text("Una hipótesis inventada.\n", encoding="utf-8")
        notes = rg.build_index(self.vault)["notes"]
        self.assertFalse(any("/Repositorio/" in n for n in notes), notes)
        self.assertIn("Projects/demo/Hipotesis/x.md", notes)

    def test_index_of_an_older_guard_version_is_rebuilt(self):
        rg.load_index(self.index, self.vault)
        idx = json.loads(self.index.read_text(encoding="utf-8"))
        idx["tool"] = "kairo/repo_guard@0.9.0"
        self.index.write_text(json.dumps(idx), encoding="utf-8")
        self.assertEqual(rg.load_index(self.index, self.vault)["tool"], f"kairo/repo_guard@{rg.__version__}")
        self.assertEqual(rg.__version__, "1.1.0")


class TestHooksEffective(Base):
    def hook(self):
        return self.repo / ".git" / "hooks" / "pre-push"

    def test_install_refuses_foreign_pre_push_hook(self):
        self.hook().parent.mkdir(parents=True, exist_ok=True)
        self.hook().write_text("#!/bin/sh\necho ajeno\n", encoding="utf-8")
        self.assertEqual(self.install(), 3)
        self.assertEqual(self.hook().read_text(encoding="utf-8"), "#!/bin/sh\necho ajeno\n")

    def test_install_refuses_core_hookspath(self):
        git(self.repo, "config", "core.hooksPath", "otros-hooks")
        self.assertEqual(self.install(), 3)
        self.assertFalse(self.hook().exists())
        self.assertIn("hooksPath", rg.hooks_problem(self.repo))

    def test_install_over_own_guard_is_allowed(self):
        self.assertEqual(self.install(), 0)
        self.assertIsNone(rg.hooks_problem(self.repo))
        self.assertEqual(self.install(), 0)


class TestScanText(Base):
    """The PR's title and body reach GitHub without being a blob or a commit message."""

    def scan_text(self, path):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = rg.main(["scan-text", "--file", str(path), "--vault", str(self.vault), "--index", str(self.index)])
        return code, out.getvalue()

    def test_text_from_a_vault_note_is_blocked_and_not_overridable(self):
        f = self.tmp / "pr.txt"
        f.write_text(f"Añade la pieza\n\nMotivo: {PAPER_TEXT}\n", encoding="utf-8")
        code, out = self.scan_text(f)
        self.assertEqual(code, 3)
        res = json.loads(out)
        self.assertFalse(res["ok"])
        self.assertEqual(res["blocking"], res["findings"])
        self.assertEqual(res["notes"], [])
        self.assertEqual(res["files"], 1)
        hit = res["blocking"][0]
        self.assertEqual(hit["kind"], "verbatim_overlap")
        self.assertIs(hit["overridable"], False)
        self.assertIsNone(hit["path"])
        self.assertIn("P-9001", hit["detail"])

    def test_clean_text_passes_with_the_same_shape(self):
        f = self.tmp / "pr.txt"
        f.write_text("Añade la pieza nueva\n\nDescripción inventada del cambio, sin nada del vault.\n", encoding="utf-8")
        code, out = self.scan_text(f)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"ok": True, "findings": [], "blocking": [], "notes": [], "files": 1})

    def test_a_file_it_cannot_read_is_an_error_not_a_pass(self):
        code, out = self.scan_text(self.tmp / "no-existe.txt")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        code, out = self.scan_text(self.tmp)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")


class TestGhArgv(unittest.TestCase):
    def test_gh_cmd_env_replaces_gh(self):
        with mock.patch.dict(os.environ, {"KAIRO_GH_CMD": '["python", "x.py"]'}):
            self.assertEqual(rg.gh_argv(), ["python", "x.py"])
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KAIRO_GH_CMD", None)
            self.assertEqual(rg.gh_argv(), ["gh"])


if __name__ == "__main__":
    unittest.main()
