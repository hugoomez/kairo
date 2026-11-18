"""post-write redacts private material that an agent wrote into a vault note.

Key-shaped strings are assembled here from pieces and random bytes; they are
written only into a temp vault. Invented ids and text only."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "kairo_hook.py"
DASHES = "-" * 5


def fake_block(label: str = "OPENSSH PRIVATE KEY") -> str:
    body = base64.b64encode(os.urandom(120)).decode()
    lines = [body[i:i + 64] for i in range(0, len(body), 64)]
    return (DASHES + "BEGIN " + label + DASHES + "\n" + "\n".join(lines) + "\n"
            + DASHES + "END " + label + DASHES)


class TestPostWriteRedacts(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-redact-hook-"))
        self.state = self.tmp / "state"
        self.vault = self.tmp / "vault"
        (self.vault / "Papers").mkdir(parents=True)
        (self.vault / "Projects" / "x" / "Criticas").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def hook(self, payload: dict) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "KAIRO_VAULT")}
        env.update({"KAIRO_STATE_DIR": str(self.state), "PYTHONIOENCODING": "utf-8"})
        return subprocess.run([sys.executable, str(HOOK), "post-write"], input=json.dumps(payload).encode(),
                              capture_output=True, env=env, timeout=120)

    def log_text(self) -> str:
        f = self.state / "hook-events.jsonl"
        return f.read_text(encoding="utf-8") if f.exists() else ""

    def test_post_write_redacts_in_place(self):
        block = fake_block()
        note = self.vault / "Projects" / "x" / "Criticas" / "CR-9001.md"
        note.write_bytes(("---\nid: CR-9001\n---\r\n\r\nUna objeción inventada.\r\n" + block
                          + "\r\nfin\r\n").encode("utf-8"))
        r = self.hook({"tool_name": "Write", "tool_input": {"file_path": str(note)}, "cwd": str(self.vault)})
        self.assertEqual(r.returncode, 0, r.stderr)
        raw = note.read_bytes()
        text = raw.decode("utf-8")
        self.assertIn("[REDACTADO private_key", text)
        self.assertNotIn("PRIVATE KEY", text)
        self.assertIn("\r\nfin\r\n", text)  # newline style preserved
        self.assertTrue(text.startswith("---\nid: CR-9001\n---\r\n"))
        err = r.stderr.decode("utf-8")
        self.assertIn("Projects/x/Criticas/CR-9001.md", err)
        self.assertIn("1", err)
        log = self.log_text()
        events = [json.loads(x) for x in log.splitlines()]
        red = [e for e in events if e["action"] == "redact"]
        self.assertEqual(len(red), 1)
        self.assertEqual(red[0]["outcome"], "redacted")
        self.assertEqual(red[0]["detail"], "Projects/x/Criticas/CR-9001.md 1 hallazgos")
        body = block.splitlines()[1]
        self.assertNotIn(body, log)
        self.assertNotIn("PRIVATE KEY", log)
        self.assertNotIn(body, err)
        self.assertEqual(list(note.parent.iterdir()), [note])  # no temp file left behind

    def test_notebook_path_is_redacted_too(self):
        block = fake_block()
        nb = self.vault / "Projects" / "x" / "analisis.ipynb"
        nb.write_text(json.dumps({"cells": [{"source": [block]}]}), encoding="utf-8")
        r = self.hook({"tool_name": "NotebookEdit", "tool_input": {"notebook_path": str(nb)},
                       "cwd": str(self.vault)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("PRIVATE KEY", nb.read_text(encoding="utf-8"))

    def test_post_write_skips_papers_and_binaries(self):
        block = fake_block()
        paper = self.vault / "Papers" / "P-9001 inventado.md"
        paper_text = "---\nid: P-9001\n---\n\n## Texto completo\n\n" + block + "\n"
        paper.write_text(paper_text, encoding="utf-8")
        r = self.hook({"tool_name": "Write", "tool_input": {"file_path": str(paper)}, "cwd": str(self.vault)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(paper.read_text(encoding="utf-8"), paper_text)

        binary = self.vault / "Projects" / "x" / "figura.bin"
        payload = b"\x89PNG\r\n\x1a\n\xff\xfe\x00" + block.encode("utf-8")
        binary.write_bytes(payload)
        r = self.hook({"tool_name": "Write", "tool_input": {"file_path": str(binary)}, "cwd": str(self.vault)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(binary.read_bytes(), payload)

        big = self.vault / "Projects" / "x" / "grande.md"
        big_text = block + "\n" + ("a" * 80 + "\n") * (5 * 1024 * 1024 // 81 + 10)
        big.write_text(big_text, encoding="utf-8")
        r = self.hook({"tool_name": "Write", "tool_input": {"file_path": str(big)}, "cwd": str(self.vault)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(big.read_text(encoding="utf-8"), big_text)
        self.assertNotIn('"redact"', self.log_text())

    def test_post_write_never_replaces_a_symlink(self):
        block = fake_block()
        target = self.vault / "Projects" / "x" / "destino.md"
        target.write_text(block + "\n", encoding="utf-8")
        link = self.vault / "Projects" / "x" / "Criticas" / "CR-9003.md"
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"no se pueden crear enlaces simbólicos aquí ({exc})")
        r = self.hook({"tool_name": "Write", "tool_input": {"file_path": str(link)}, "cwd": str(self.vault)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(link.is_symlink())
        self.assertEqual(target.read_text(encoding="utf-8"), block + "\n")

    def test_redact_written_skips_what_is_a_symlink(self):
        # runs where symlinks cannot be created: the path only claims to be one
        from unittest import mock
        sys.path.insert(0, str(HOOK.parent))
        import kairo_hook
        block = fake_block()
        note = self.vault / "Projects" / "x" / "Criticas" / "CR-9004.md"
        note.write_text(block + "\n", encoding="utf-8")
        real = Path.is_symlink
        with mock.patch.dict(os.environ, {"KAIRO_STATE_DIR": str(self.state)}):  # never the real ~/.kairo
            with mock.patch.object(Path, "is_symlink", lambda p: p == note or real(p)):
                kairo_hook.redact_written(note, self.vault)
            self.assertEqual(note.read_text(encoding="utf-8"), block + "\n")
            kairo_hook.redact_written(note, self.vault)  # and without the claim it is redacted
        self.assertNotIn("PRIVATE KEY", note.read_text(encoding="utf-8"))

    def test_clean_note_is_left_untouched(self):
        note = self.vault / "Projects" / "x" / "Criticas" / "CR-9002.md"
        note.write_text("---\nid: CR-9002\n---\n\nSin nada privado.\n", encoding="utf-8")
        before = note.stat().st_mtime_ns
        r = self.hook({"tool_name": "Edit", "tool_input": {"file_path": str(note)}, "cwd": str(self.vault)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(note.stat().st_mtime_ns, before)
        self.assertEqual(r.stderr.decode("utf-8"), "")

    def test_post_write_outside_vault_is_untouched(self):
        block = fake_block()
        other = self.tmp / "elsewhere" / "nota.md"
        other.parent.mkdir()
        other.write_text(block + "\n", encoding="utf-8")
        r = self.hook({"tool_name": "Write", "tool_input": {"file_path": str(other)}, "cwd": str(self.vault)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(other.read_text(encoding="utf-8"), block + "\n")

    def test_hooks_json_post_matcher(self):
        hooks = json.loads((HOOK.parents[2] / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        [group] = [g for g in hooks["PostToolUse"] if any(h["command"].endswith("post-write") for h in g["hooks"])]
        self.assertEqual(group["matcher"], "Write|Edit|MultiEdit|NotebookEdit")
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            self.assertTrue(re.fullmatch(group["matcher"], tool), tool)
        self.assertFalse(re.fullmatch(group["matcher"], "Bash"))


if __name__ == "__main__":
    unittest.main()
