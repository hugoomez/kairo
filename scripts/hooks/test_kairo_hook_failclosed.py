"""kairo_hook pre-tool: inside a Kairo vault a guard that cannot decide refuses
(exit 2, one-line reason, error in hook-events); outside a vault it stays a
no-op. send_guard's flagged-note scan is cached so a large vault stays fast
(H11). Invented ids and text only; every state dir is a temp dir."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "security"))
import kairo_hook  # noqa: E402
import send_guard  # noqa: E402
import isolation  # noqa: E402, I001

HOOK = HERE / "kairo_hook.py"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-failclosed-"))
        self.state = self.tmp / "state"
        self.vault = self.tmp / "vault"
        (self.vault / "Papers").mkdir(parents=True)
        (self.vault / "Projects" / "demo").mkdir(parents=True)
        (self.vault / "Projects" / "demo" / "_hub.md").write_text("---\nid: PROJ-961\n---\n", encoding="utf-8")
        self.outside = self.tmp / "elsewhere"
        self.outside.mkdir()
        env = {"KAIRO_STATE_DIR": str(self.state)}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        for k in ("CLAUDE_PROJECT_DIR", "KAIRO_VAULT", "KAIRO_ALLOW_MAIN_PAPER_READ"):
            os.environ.pop(k, None)

    def tearDown(self):
        self.env.stop()
        send_guard.CACHE_DIR = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pre(self, payload: dict) -> tuple[int, str]:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = kairo_hook.pre_tool(payload)
        return code, err.getvalue()

    def events(self) -> list[dict]:
        f = self.state / "hook-events.jsonl"
        return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


class TestFailClosed(Base):
    def read(self, where: Path) -> dict:
        return {"tool_name": "Read", "tool_input": {"file_path": str(where / "x.md")}, "cwd": str(where)}

    def test_a_send_guard_error_inside_a_vault_refuses_and_is_logged(self):
        with mock.patch.object(send_guard, "decide", side_effect=RuntimeError("boom")):
            code, err = self.pre(self.read(self.vault / "Projects" / "demo"))
        self.assertEqual(code, 2)
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertIn("send_guard", err)
        self.assertTrue(any(e["outcome"] == "error" and e["action"] == "send_guard" for e in self.events()))

    def test_a_main_paper_read_error_inside_a_vault_refuses(self):
        with mock.patch.object(isolation, "main_paper_read", side_effect=RuntimeError("boom")):
            code, err = self.pre(self.read(self.vault / "Projects" / "demo"))
        self.assertEqual(code, 2, err)
        self.assertTrue(any(e["outcome"] == "error" and e["action"] == "main_paper_read" for e in self.events()))

    def test_outside_a_vault_a_guard_error_stays_a_noop(self):
        with mock.patch.object(send_guard, "decide", side_effect=RuntimeError("boom")), \
                mock.patch.object(isolation, "main_paper_read", side_effect=RuntimeError("boom")):
            code, _ = self.pre(self.read(self.outside))
        self.assertEqual(code, 0)

    def run_hook(self, raw: bytes, cwd: Path) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "KAIRO_VAULT")}
        env.update({"KAIRO_STATE_DIR": str(self.state), "PYTHONIOENCODING": "utf-8"})
        return subprocess.run([sys.executable, str(HOOK), "pre-tool"], input=raw, capture_output=True, env=env,
                              cwd=cwd, timeout=60)

    def test_unparseable_hook_input_inside_a_vault_refuses(self):
        r = self.run_hook(b"{not json", self.vault)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("Kairo", r.stderr.decode("utf-8"))
        self.assertTrue(any(e["outcome"] == "error" for e in self.events()))
        r = self.run_hook(b"{not json", self.outside)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_send_guard_run_alone_fails_closed_inside_a_vault_too(self):
        sg = HERE.parent / "security" / "send_guard.py"
        for cwd, want in ((self.vault, 2), (self.outside, 0)):
            env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
            r = subprocess.run([sys.executable, str(sg), "hook"], input=b"{not json", capture_output=True, env=env,
                               cwd=cwd, timeout=60)
            self.assertEqual(r.returncode, want, (cwd, r.stderr))

    def test_an_unreadable_note_counts_as_flagged(self):
        note = self.vault / "Papers" / "P-0961 x.md"
        note.write_text("---\nid: P-0961\n---\n", encoding="utf-8")
        with mock.patch("builtins.open", side_effect=PermissionError("locked")):
            self.assertTrue(send_guard.is_flagged(note))
        self.assertFalse(send_guard.is_flagged(self.vault / "Papers" / "missing.md"))   # nothing to send


class TestFlaggedCache(Base):
    def test_a_large_vault_is_scanned_once_then_from_the_cache(self):
        papers = self.vault / "Papers"
        for i in range(600):
            head = "send: never\n" if i % 97 == 0 else ""
            (papers / f"P-{1000 + i:04d} invented.md").write_text(f"---\nid: P-{1000 + i:04d}\n{head}---\n\nTexto.\n",
                                                                  encoding="utf-8")
        send_guard.CACHE_DIR = self.state / "send-guard-cache"
        ev = {"tool_name": "Bash", "tool_input": {"command": "cat notes.txt"}, "cwd": str(self.vault)}
        t0 = time.perf_counter()
        self.assertIsNone(send_guard.decide(ev, self.vault))
        first = time.perf_counter() - t0
        self.assertLess(first, 20)
        send_guard._READ_MEMO.clear()                        # a new hook process: only the file cache remains
        real = send_guard._read_flag
        with mock.patch.object(send_guard, "_read_flag", side_effect=real) as reads:
            t0 = time.perf_counter()
            self.assertIsNone(send_guard.decide(ev, self.vault))
            second = time.perf_counter() - t0
        self.assertEqual(reads.call_count, 0)
        self.assertLess(second, 5)
        changed = papers / "P-1001 invented.md"
        changed.write_text("---\nid: P-1001\nsend: never\n---\n\nTexto cambiado.\n", encoding="utf-8")
        os.utime(changed, ns=(time.time_ns(), time.time_ns() + 5_000_000_000))
        send_guard._READ_MEMO.clear()
        with mock.patch.object(send_guard, "_read_flag", side_effect=real) as reads:
            got = send_guard.flagged_under(self.vault)
        self.assertEqual(reads.call_count, 1)                 # only the changed note is read again
        self.assertIn(changed, got)
        self.assertEqual(len(got), 8)

    def test_without_a_cache_dir_nothing_is_written(self):
        (self.vault / "Papers" / "P-0962 x.md").write_text("---\nsend: never\n---\n", encoding="utf-8")
        self.assertEqual(len(send_guard.flagged_under(self.vault)), 1)
        self.assertFalse((self.state / "send-guard-cache").exists())


if __name__ == "__main__":
    unittest.main()
