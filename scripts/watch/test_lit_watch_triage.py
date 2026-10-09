"""lit_watch: triage lines come from an isolated screener that read the packet —
the session that runs commands never reads a candidate's abstract (invented
ids and text only)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_lit_watch as base  # noqa: E402
import test_lit_watch_state as st  # noqa: E402

sys.path.insert(0, str(HERE.parent / "security"))
import isolation  # noqa: E402


class TriageByScreener(st.Base):
    def packet(self):
        _, res = self.delta()
        run = str(self.run_file(res))
        code, out = self.cli("triage-packet", "--project-dir", str(self.p), "--run", run)
        self.assertEqual(code, 0, out)
        return run, out

    def reply(self, keys, why="Mide lo mismo con widgets inventados.") -> str:
        return "```json\n" + json.dumps({k: {"decision": "include", "relevance": "alta", "why": why}
                                          for k in keys}, ensure_ascii=False) + "\n```"

    def screened(self, out, text: str, read: bool = True) -> None:
        if read:
            isolation.record_receipt({"sha256": out["sha256"], "agent_type": "screener", "agent_id": "s-1"})
        isolation.capture_reply({"agent_type": "kairo:screener", "agent_id": "s-1", "last_assistant_message": text})

    def test_the_packet_holds_the_abstracts_and_the_output_does_not(self):
        run, out = self.packet()
        self.assertEqual(out["keys"], ["arxiv:2031.00002"])
        text = Path(out["packet"]).read_text(encoding="utf-8")
        self.assertIn(base.ABSTRACT, text)
        self.assertIn("arxiv:2031.00002", text)
        self.assertNotIn(base.ABSTRACT, json.dumps(out, ensure_ascii=False))
        self.assertNotIn("Blue widgets spin faster", json.dumps(out, ensure_ascii=False))

    def test_the_screeners_reply_from_the_store_becomes_the_triage_lines(self):
        run, out = self.packet()
        self.screened(out, self.reply(out["keys"]))
        code, res = self.cli("triage", "--project-dir", str(self.p), "--run", run, "--from-store")
        self.assertEqual(code, 0, res)
        self.assertEqual(res["recorded"], 1)
        self.assertNotIn("widgets inventados", json.dumps(res, ensure_ascii=False))   # the session sees counts
        c = json.loads(Path(run).read_text(encoding="utf-8"))["candidates"][0]
        self.assertEqual(c["why"], "alta: Mide lo mismo con widgets inventados.")
        self.assertEqual(c["triage_by"]["agent"], "screener")
        code, chk = self.cli("check", "--project-dir", str(self.p), "--run", run)
        self.assertEqual(code, 0, chk)

    def test_an_exclusion_says_why(self):
        run, out = self.packet()
        text = "```json\n" + json.dumps({out["keys"][0]: {"decision": "exclude", "reason": "fuera de tema",
                                                          "why": "trata de otra cosa inventada"}}) + "\n```"
        self.screened(out, text)
        self.cli("triage", "--project-dir", str(self.p), "--run", run, "--from-store")
        c = json.loads(Path(run).read_text(encoding="utf-8"))["candidates"][0]
        self.assertEqual(c["why"], "fuera de tema: trata de otra cosa inventada")

    def test_a_reply_whose_keys_differ_is_refused(self):
        run, out = self.packet()
        f = self.tmp / "reply.txt"
        f.write_text(self.reply(["arxiv:2031.99999"]), encoding="utf-8")
        isolation.record_receipt({"sha256": out["sha256"], "agent_type": "screener", "agent_id": "s-1"})
        code, res = self.cli("triage", "--project-dir", str(self.p), "--run", run, "--reply", str(f))
        self.assertEqual(code, 2, res)
        self.assertIn("keys", res["error"])

    def test_an_unread_packet_is_refused(self):
        run, out = self.packet()
        f = self.tmp / "reply.txt"
        f.write_text(self.reply(out["keys"]), encoding="utf-8")
        code, res = self.cli("triage", "--project-dir", str(self.p), "--run", run, "--reply", str(f))
        self.assertEqual(code, 2, res)
        self.assertIn("screener", res["error"])

    def test_a_reply_file_that_differs_from_the_stored_reply_is_refused(self):
        run, out = self.packet()
        self.screened(out, self.reply(out["keys"]))
        f = self.tmp / "reply.txt"
        f.write_text(self.reply(out["keys"], why="Una línea que el orquestador cambió."), encoding="utf-8")
        code, res = self.cli("triage", "--project-dir", str(self.p), "--run", run, "--reply", str(f))
        self.assertEqual(code, 2, res)
        self.assertIn("differs", res["error"])


if __name__ == "__main__":
    unittest.main()
