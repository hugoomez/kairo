"""isolation: an isolated agent's final answer is stored by the SubagentStop hook,
tied to the packets it read (invented ids and text only)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "hooks"))
import isolation  # noqa: E402
import kairo_hook  # noqa: E402


class Replies(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"KAIRO_STATE_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_the_reply_is_tied_to_the_packets_the_agent_read(self):
        isolation.record_receipt({"sha256": "a" * 64, "agent_type": "screener", "agent_id": "s-1"})
        isolation.record_receipt({"sha256": "b" * 64, "agent_type": "screener", "agent_id": "s-2"})
        rec = isolation.capture_reply({"agent_type": "kairo:screener", "agent_id": "s-1",
                                       "last_assistant_message": '```json\n{"k": {"decision": "include"}}\n```'})
        self.assertEqual(rec["packets"], ["a" * 64])
        [r] = isolation.replies("a" * 64, "screener")
        self.assertEqual(isolation.json_of(r["text"]), {"k": {"decision": "include"}})
        self.assertEqual(isolation.replies("b" * 64), [])
        self.assertTrue(isolation.reply_matches("a" * 64, "screener", {"k": {"decision": "include"}}))
        self.assertFalse(isolation.reply_matches("a" * 64, "screener", {"k": {"decision": "exclude"}}))
        self.assertIsNone(isolation.reply_matches("b" * 64, "screener", {}))

    def test_the_transcript_is_read_when_no_final_message_is_given(self):
        t = Path(self.tmp.name) / "agent.jsonl"
        t.write_text("\n".join(json.dumps(x) for x in (
            {"type": "user", "message": {"role": "user", "content": "path"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "first"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Read"}, {"type": "text", "text": "```json\n{\"v\": 1}\n```"}]}},
        )) + "\n", encoding="utf-8")
        isolation.record_receipt({"sha256": "c" * 64, "agent_type": "fresh-verifier", "agent_id": "v-9"})
        isolation.capture_reply({"agent_type": "fresh-verifier", "agent_id": "v-9", "agent_transcript_path": str(t)})
        [r] = isolation.replies("c" * 64, "fresh-verifier")
        self.assertEqual(isolation.json_of(r["text"]), {"v": 1})

    def test_other_agents_are_not_stored_and_the_hook_never_fails(self):
        self.assertIsNone(isolation.capture_reply({"agent_type": "Explore", "agent_id": "x",
                                                   "last_assistant_message": "hi"}))
        self.assertEqual(kairo_hook.subagent_stop({"agent_type": "kairo:screener", "agent_id": "s-3",
                                                   "last_assistant_message": "{}"}), 0)
        with mock.patch.object(isolation, "capture_reply", side_effect=OSError("disk")):
            self.assertEqual(kairo_hook.subagent_stop({"agent_type": "screener", "agent_id": "s"}), 0)

    def test_the_last_json_block_is_the_answer(self):
        self.assertEqual(isolation.json_of('```json\n{"a": 1}\n```\nthen\n```json\n{"b": 2}\n```'), {"b": 2})
        self.assertEqual(isolation.json_of('{"bare": true}'), {"bare": True})
        self.assertIsNone(isolation.json_of("no json here"))


if __name__ == "__main__":
    unittest.main()
