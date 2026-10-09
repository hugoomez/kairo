"""kairo_hook post-write: every step runs inside a budget that fits the hook's
own timeout in hooks.json, and a step that runs out of time is logged and said
(1d). Invented ids and text only; the state dir is a temp dir."""

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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import kairo_hook  # noqa: E402


class TestPostWriteBudget(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-budget-"))
        self.state = self.tmp / "state"
        self.vault = self.tmp / "vault"
        (self.vault / "Papers").mkdir(parents=True)
        (self.vault / "Projects" / "demo" / "Hipotesis").mkdir(parents=True)
        self.env = mock.patch.dict(os.environ, {"KAIRO_STATE_DIR": str(self.state)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def events(self) -> list[dict]:
        f = self.state / "hook-events.jsonl"
        return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []

    def test_the_budgets_fit_inside_the_hook_timeout(self):
        hooks = json.loads((HERE.parents[1] / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        [post_write] = [h for g in hooks["PostToolUse"] for h in g["hooks"] if h["command"].endswith("post-write")]
        self.assertLess(kairo_hook.POST_WRITE_BUDGET_S, post_write["timeout"])
        # the heaviest path (a hypothesis: digest then ledger; a paper: staleness then reindex) fits too
        b = kairo_hook.STEP_BUDGET_S
        self.assertLessEqual(max(b["digest"] + b["ledger"], b["sota_staleness"] + b["reindex"]),
                             kairo_hook.POST_WRITE_BUDGET_S)

    def test_a_step_that_times_out_is_logged_and_said_and_the_next_still_runs(self):
        note = self.vault / "Projects" / "demo" / "Hipotesis" / "H-0951.md"
        note.write_text("---\nid: H-0951\n---\n", encoding="utf-8")
        calls = []

        def fake(script, args, payload, timeout=60):
            calls.append((script, timeout))
            if script.endswith("regen_digest.py"):
                raise subprocess.TimeoutExpired(script, timeout)
            return 0, ""

        out = io.StringIO()
        with mock.patch.object(kairo_hook, "run_script", side_effect=fake), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(kairo_hook.post_write({"tool_input": {"file_path": str(note)}, "cwd": str(self.vault)}), 0)
        self.assertEqual([c[0] for c in calls], ["hooks/regen_digest.py", "ledger/build_graph.py"])
        self.assertTrue(all(t <= kairo_hook.POST_WRITE_BUDGET_S for _, t in calls))
        self.assertIn(("digest", "timeout"), {(e["action"], e["outcome"]) for e in self.events()})
        said = json.loads(out.getvalue())
        self.assertIn("digest", said["systemMessage"])

    def test_no_time_left_skips_a_step_visibly(self):
        note = self.vault / "Projects" / "demo" / "Hipotesis" / "H-0952.md"
        note.write_text("---\nid: H-0952\n---\n", encoding="utf-8")
        out = io.StringIO()
        with mock.patch.object(kairo_hook, "POST_WRITE_BUDGET_S", 0), \
                mock.patch.object(kairo_hook, "run_script", return_value=(0, "")) as rs, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            kairo_hook.post_write({"tool_input": {"file_path": str(note)}, "cwd": str(self.vault)})
        rs.assert_not_called()
        self.assertEqual({e["outcome"] for e in self.events()}, {"timeout"})
        self.assertIn("ledger", json.loads(out.getvalue())["systemMessage"])

    def test_the_staleness_message_and_a_timeout_share_one_json_answer(self):
        paper = self.vault / "Papers" / "P-0951 x.md"
        paper.write_text("---\nid: P-0951\n---\n", encoding="utf-8")
        stale = json.dumps({"systemMessage": "Estado-del-arte.md is stale", "hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": "refresh"}})
        out = io.StringIO()
        with mock.patch.object(kairo_hook, "run_script", return_value=(0, stale)), \
                mock.patch.object(kairo_hook, "smart_connections_reindexer", return_value=self.tmp / "r.js"), \
                mock.patch.object(kairo_hook.subprocess, "run", side_effect=subprocess.TimeoutExpired("node", 1)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            kairo_hook.post_write({"tool_input": {"file_path": str(paper)}, "cwd": str(self.vault)})
        said = json.loads(out.getvalue())
        self.assertEqual(said["hookSpecificOutput"]["additionalContext"], "refresh")
        self.assertIn("stale", said["systemMessage"])
        self.assertIn("reindex", said["systemMessage"])


if __name__ == "__main__":
    unittest.main()
