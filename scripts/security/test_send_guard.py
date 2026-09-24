import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from send_guard import decide, flagged_under, frontmatter_says_never  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "send_guard.py"
SECRET_BODY = "CONTENIDO-PRIVADO-NO-ENVIAR"


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

    def test_other_tools_pass(self):
        self.assertIsNone(decide(self.ev("Glob", pattern="Papers/*"), self.v))
        self.assertIsNone(decide(self.ev("Write", file_path=str(self.secret), content=""), self.v))


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
