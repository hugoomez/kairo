"""Tests for cross_verify.py: no network, a fake DeepInfra. Invented packet text only."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "security"))
import cross_verify as cv  # noqa: E402
import isolation  # noqa: E402

PACKET = "# Paquete de verificación\n\nAlcance: section:X\n\n### Afirmación 1\nThe toy decoder reaches 1.1%.\n"
ON = {"KAIRO_SECOND_CRITIC": "on", "DEEPINFRA_TOKEN": "tok-secret"}


def answer(verdict="errors_found"):
    findings = [{"severity": "importante", "location": "Afirmación 1", "why": "the source says 1.4%"}] \
        if verdict == "errors_found" else []
    content = "One finding.\n```json\n" + json.dumps({"verifier": cv.TOOL, "verdict": verdict,
                                                       "findings": findings, "cannot_assess_reason": None}) + "\n```"
    return json.dumps({"choices": [{"message": {"content": content}}],
                       "usage": {"prompt_tokens": 2000, "completion_tokens": 300}}).encode()


class CrossVerify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"KAIRO_STATE_DIR": self.tmp.name})
        self.env.start()
        os.environ.pop("KAIRO_PACKETS_DIR", None)
        self.packet = Path(isolation.store(PACKET)["path"])

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_cli(self, *args, post=None, env=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cv.main(["--packet", str(self.packet), *args], post=post or (lambda *a: answer()),
                           env=ON if env is None else env)
        return code, json.loads(buf.getvalue())

    def test_off_means_not_available_and_nothing_sent(self):
        sent = []
        code, out = self.run_cli(post=lambda *a: sent.append(a) or answer(), env={})
        self.assertEqual((code, out["available"]), (4, False))
        self.assertEqual(sent, [])

    def test_the_verdict_cost_and_agreement_are_reported(self):
        seen = {}

        def post(url, headers, body):
            seen.update(url=url, headers=headers, body=json.loads(body))
            return answer()
        code, out = self.run_cli("--fresh-verdict", "no_errors_found", post=post)
        self.assertEqual((code, out["verdict"]), (3, "errors_found"))
        self.assertEqual(out["model"], "deepseek-ai/DeepSeek-V4-Flash")
        self.assertFalse(out["agrees_with_fresh"])
        self.assertAlmostEqual(out["cost_usd"], 2000 / 1e6 * 0.09 + 300 / 1e6 * 0.18, places=6)
        self.assertEqual(seen["body"]["messages"][1]["content"], PACKET)     # the packet, unchanged
        self.assertEqual(seen["headers"]["Authorization"], "Bearer tok-secret")
        self.assertNotIn("tok-secret", json.dumps(out))

    def test_an_answer_without_a_json_block_is_cannot_assess(self):
        bad = json.dumps({"choices": [{"message": {"content": "I think it is fine."}}]}).encode()
        code, out = self.run_cli(post=lambda *a: bad)
        self.assertEqual((code, out["verdict"]), (3, "cannot_assess"))

    def test_only_an_intact_store_packet_is_sent_and_dry_run_sends_nothing(self):
        loose = Path(self.tmp.name) / "packet.md"
        loose.write_text(PACKET, encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(cv.main(["--packet", str(loose)], post=lambda *a: answer(), env=ON), 2)
        sent = []
        code, out = self.run_cli("--dry-run", post=lambda *a: sent.append(a) or answer())
        self.assertEqual((code, sent), (0, []))
        self.assertTrue(out["would_send"]["estimate"])


if __name__ == "__main__":
    unittest.main()
