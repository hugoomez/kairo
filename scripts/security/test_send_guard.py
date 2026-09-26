import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from send_guard import (decide, flagged_under, frontmatter_says_never,  # noqa: E402
                        is_model_notes, model_notes_under)

SCRIPT = Path(__file__).resolve().parent / "send_guard.py"
SECRET_BODY = "CONTENIDO-PRIVADO-NO-ENVIAR"
NOTES_BODY = "PARAFRASIS-DEL-MODELO-NO-CITABLE"


def note(send: str | None, body: str = "texto") -> str:
    fm = ["---", "id: P-0099", "title: Nota de prueba"]
    if send is not None:
        fm.append(send)
    return "\n".join(fm + ["---", "", body, ""])


class TestFrontmatter(unittest.TestCase):
    def test_variants_that_flag(self):
        for line in ("send: never", "send: 'never'", 'send: "never"', "SEND: Never",
                     "send:never", "send: never  # privada"):
            self.assertTrue(frontmatter_says_never(note(line)), line)

    def test_variants_that_do_not_flag(self):
        for line in (None, "send: always", "send: nevermind", "resend: never", "  send: never"):
            self.assertFalse(frontmatter_says_never(note(line)), line)

    def test_only_frontmatter_counts(self):
        self.assertFalse(frontmatter_says_never(note(None, body="send: never")))
        self.assertFalse(frontmatter_says_never("send: never\n"))
        # unterminated header: err toward not sending
        self.assertTrue(frontmatter_says_never("---\nsend: never\n"))

    def test_bom(self):
        self.assertTrue(frontmatter_says_never("﻿" + note("send: never")))


class VaultCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.v = Path(self.tmp.name).resolve()
        (self.v / "Papers").mkdir()
        (self.v / "Projects" / "p" / "Hipotesis").mkdir(parents=True)
        (self.v / ".obsidian").mkdir()
        self.secret = self.v / "Papers" / "P-0099 Privado.md"
        self.secret.write_text(note("send: never", SECRET_BODY), encoding="utf-8")
        self.normal = self.v / "Papers" / "P-0001 Normal.md"
        self.normal.write_text(note(None), encoding="utf-8")
        (self.v / ".obsidian" / "x.md").write_text(note("send: never"), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def ev(self, tool, **tin):
        return {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tin,
                "cwd": str(self.v)}


class TestDecide(VaultCase):
    def test_flagged_under_skips_tool_dirs(self):
        self.assertEqual(flagged_under(self.v), [self.secret])

    def test_read(self):
        self.assertIsNotNone(decide(self.ev("Read", file_path=str(self.secret)), self.v))
        self.assertIsNotNone(decide(self.ev("Read", file_path="Papers/P-0099 Privado.md"), self.v))
        self.assertIsNone(decide(self.ev("Read", file_path=str(self.normal)), self.v))
        self.assertIsNone(decide(self.ev("Read", file_path="no/such.md"), self.v))

    def test_grep_content_blocked_names_only(self):
        self.assertIsNotNone(decide(self.ev("Grep", pattern="x", output_mode="content"), self.v))
        self.assertIsNotNone(decide(self.ev("Grep", pattern="x", path="Papers",
                                            output_mode="content"), self.v))
        self.assertIsNone(decide(self.ev("Grep", pattern="x"), self.v))  # files_with_matches
        self.assertIsNone(decide(self.ev("Grep", pattern="x", output_mode="count"), self.v))
        self.assertIsNone(decide(self.ev("Grep", pattern="x", path="Projects",
                                         output_mode="content"), self.v))
        self.assertIsNone(decide(self.ev("Grep", pattern="x", glob="*.py",
                                         output_mode="content"), self.v))
        self.assertIsNone(decide(self.ev("Grep", pattern="x", path=str(self.normal),
                                         output_mode="content"), self.v))

    def test_bash(self):
        self.assertIsNotNone(decide(self.ev("Bash", command='cat "Papers/P-0099 Privado.md"'), self.v))
        self.assertIsNotNone(decide(self.ev("PowerShell",
                                            command='Get-Content "Papers\\P-0099 Privado.md"'), self.v))
        self.assertIsNone(decide(self.ev("Bash", command='cat "Papers/P-0001 Normal.md"'), self.v))
        self.assertIsNone(decide(self.ev("Bash", command="ls Papers"), self.v))

    def test_mcp_get_note(self):
        t = "mcp__smart-connections__get_note"
        self.assertIsNotNone(decide(self.ev(t, notePath="Papers/P-0099 Privado.md"), self.v))
        self.assertIsNone(decide(self.ev(t, notePath="Papers/P-0001 Normal.md"), self.v))

    def test_review_bypasses_are_closed(self):
        # brace glob: fnmatch can't expand it, so it must count as matching
        self.assertIsNotNone(decide(self.ev("Grep", pattern="x", glob="*.{md,txt}",
                                            output_mode="content"), self.v))
        # the MCP server trims notePath; so must the guard
        t = "mcp__smart-connections__get_note"
        self.assertIsNotNone(decide(self.ev(t, notePath=" Papers/P-0099 Privado.md"), self.v))
        self.assertIsNotNone(decide(self.ev(t, notePath="Papers/P-0099 Privado.md\n"), self.v))
        self.assertIsNotNone(decide(self.ev("Read", file_path=' "Papers/P-0099 Privado.md" '), self.v))
        # commands with no classic reader verb, and id globs
        for cmd in ("sort 'Papers/P-0099 Privado.md'",
                    "[IO.File]::ReadAllText('Papers/P-0099 Privado.md')",
                    "cat Papers/P-0099*",
                    "CAT 'papers/p-0099 privado.md'"):
            self.assertIsNotNone(decide(self.ev("Bash", command=cmd), self.v), cmd)
        # an id that merely shares a prefix is not the flagged note
        self.assertIsNone(decide(self.ev("Bash", command="cat Papers/P-00991*"), self.v))
        self.assertIsNone(decide(self.ev("Bash", command="git log --oneline"), self.v))

    def test_other_tools_pass(self):
        self.assertIsNone(decide(self.ev("Glob", pattern="Papers/*"), self.v))
        self.assertIsNone(decide(self.ev("Write", file_path=str(self.secret), content=""), self.v))

    def test_negated_glob_does_not_hide_a_flagged_note(self):
        # rg treats `!x` as "everything except x", so the flagged note is in scope
        self.assertIsNotNone(decide(self.ev("Grep", pattern="x", glob="!*.py",
                                            output_mode="content"), self.v))


class NotesCase(VaultCase):
    """A vault whose reading notes live in Papers/_notas/ (model-written)."""

    def setUp(self):
        super().setUp()
        (self.v / "Papers" / "_notas").mkdir()
        self.notes = self.v / "Papers" / "_notas" / "P-0001.md"
        self.notes.write_text(note(None, NOTES_BODY), encoding="utf-8")


class TestModelNotes(NotesCase):
    def test_detection(self):
        self.assertTrue(is_model_notes(self.notes))
        self.assertTrue(is_model_notes(Path("C:/v/PAPERS/_Notas/P-0001.md")))
        self.assertFalse(is_model_notes(self.normal))
        self.assertFalse(is_model_notes(Path("Projects/_notas/x.md")))  # only under Papers/
        self.assertEqual(model_notes_under(self.v), [self.notes])
        # the notes are not `send: never`: the id-based Bash rule must not fire on P-0001
        self.assertEqual(flagged_under(self.v), [self.secret])

    def test_read_blocked_in_every_spelling(self):
        for fp in (str(self.notes), "Papers/_notas/P-0001.md", r"papers\_NOTAS\P-0001.md",
                   ' "Papers/_notas/P-0001.md" ', "Papers/x/../_notas/P-0001.md"):
            self.assertIsNotNone(decide(self.ev("Read", file_path=fp), self.v), fp)
        self.assertIsNone(decide(self.ev("Read", file_path=str(self.normal)), self.v))

    def test_grep(self):
        blocked = [dict(), dict(path="Papers"), dict(path="Papers/_notas"),
                   dict(path="Papers", glob="P-*.md"), dict(path="Papers", glob="*.md"),
                   dict(path="Papers", glob="!*.py")]
        for kw in blocked:
            self.assertIsNotNone(decide(self.ev("Grep", pattern="x", output_mode="content", **kw),
                                        self.v), kw)
        # names-only modes, a scope without notes, or an explicit exclusion pass
        # (the send: never note is excluded by path here, so only notes are at stake)
        self.assertIsNone(decide(self.ev("Grep", pattern="x", path="Papers/_notas"), self.v))
        self.assertIsNone(decide(self.ev("Grep", pattern="x", path=str(self.normal),
                                         output_mode="content"), self.v))
        self.secret.unlink()
        self.assertIsNone(decide(self.ev("Grep", pattern="x", path="Papers",
                                         glob="!**/_notas/**", output_mode="content"), self.v))

    def test_shell_and_mcp(self):
        for cmd in ("cat Papers/_notas/P-0001.md", r"type papers\_notas\*",
                    "cd Papers && cat _notas/*", "Get-Content 'Papers/_notas/P-0001.md'"):
            self.assertIsNotNone(decide(self.ev("Bash", command=cmd), self.v), cmd)
            self.assertIsNotNone(decide(self.ev("PowerShell", command=cmd), self.v), cmd)
        # the paper note itself and its id stay usable
        self.assertIsNone(decide(self.ev("Bash", command="cat 'Papers/P-0001 Normal.md'"), self.v))
        self.assertIsNone(decide(self.ev("Bash", command="grep -l P-0001 Projects"), self.v))
        self.assertIsNone(decide(self.ev("Bash", command="echo mis_notas_viejas"), self.v))
        t = "mcp__smart-connections__get_note"
        self.assertIsNotNone(decide(self.ev(t, notePath="Papers/_notas/P-0001.md"), self.v))


class TestSummarizerRunCannotReachNotes(NotesCase):
    """Replays, through the real hook process, every tool call a facet-summarizer
    makes (it has Read, Grep and Glob only) plus the ways it could stray into
    Papers/_notas/. Every stray call exits 2 and no note text ever comes back."""

    def hook(self, tool, **tin):
        return subprocess.run([sys.executable, str(SCRIPT), "hook", "--vault", str(self.v)],
                              input=json.dumps(self.ev(tool, **tin)).encode(),
                              capture_output=True)

    def test_run(self):
        assigned = str(self.normal)
        allowed = [
            ("Grep", dict(pattern=r"^send:\s*[\"']?never", path=assigned, **{"-i": True},
                          output_mode="files_with_matches")),
            ("Read", dict(file_path=assigned)),
            ("Grep", dict(pattern="Table 2", path=assigned, output_mode="content")),
            ("Glob", dict(pattern="Papers/**/*.md")),  # names only
        ]
        stray = [
            ("Read", dict(file_path=str(self.notes))),
            ("Read", dict(file_path="Papers/_notas/P-0001.md")),
            ("Grep", dict(pattern="toy dynamics", path="Papers", output_mode="content")),
            ("Grep", dict(pattern="toy dynamics", path="Papers", glob="P-0001*",
                          output_mode="content")),
            ("Grep", dict(pattern="toy dynamics", path=str(self.v / "Papers" / "_notas"),
                          output_mode="content")),
        ]
        for tool, tin in allowed:
            r = self.hook(tool, **tin)
            self.assertEqual((r.returncode, r.stdout), (0, b""), (tool, tin, r.stderr))
        for tool, tin in stray:
            r = self.hook(tool, **tin)
            self.assertEqual(r.returncode, 2, (tool, tin))
            self.assertEqual(r.stdout, b"")
            self.assertNotIn(NOTES_BODY.encode(), r.stderr)

    def test_check_mode_reports_notes(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "check", str(self.normal), str(self.notes)],
                           capture_output=True)
        self.assertEqual(r.returncode, 3)
        self.assertIn(b"notas de modelo", r.stdout)
        self.assertNotIn(NOTES_BODY.encode(), r.stdout)


class TestCli(VaultCase):
    def run_hook(self, event):
        return subprocess.run([sys.executable, str(SCRIPT), "hook", "--vault", str(self.v)],
                              input=json.dumps(event).encode(), capture_output=True)

    def test_hook_blocks_with_exit_2_and_never_leaks_content(self):
        r = self.run_hook(self.ev("Read", file_path=str(self.secret)))
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, b"")
        self.assertIn("send: never", r.stderr.decode("utf-8"))
        self.assertNotIn(SECRET_BODY, r.stderr.decode("utf-8"))

    def test_hook_allows_normal(self):
        r = self.run_hook(self.ev("Read", file_path=str(self.normal)))
        self.assertEqual((r.returncode, r.stdout), (0, b""))

    def test_hook_fails_open_on_garbage(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "hook"], input=b"not json",
                           capture_output=True)
        self.assertEqual(r.returncode, 0)

    def test_check_and_list(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "check", str(self.secret), str(self.normal)],
                           capture_output=True)
        self.assertEqual(r.returncode, 3)
        self.assertNotIn(SECRET_BODY.encode(), r.stdout)
        r = subprocess.run([sys.executable, str(SCRIPT), "list", str(self.v), "--json"],
                           capture_output=True)
        self.assertEqual(json.loads(r.stdout)["flagged"], ["Papers/P-0099 Privado.md"])


if __name__ == "__main__":
    unittest.main()
