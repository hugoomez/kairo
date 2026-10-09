"""session_capture: secrets are redacted before a transcript is copied into the
vault, capture can be switched off, and a session that touched several
projects is copied once (H8). Invented vault, ids, text and secrets only."""

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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "bitacora"))
import action_log  # noqa: E402
import session_capture as sc  # noqa: E402

FAKE_ENV_SECRET = "invented-env-secret-0123456789"
SECRETS = ("sk-ant-api03-INVENTEDinventedINVENTED0123456789", "sk-proj-INVENTEDinvented0123456789abcd",
           "ghp_INVENTEDinventedINVENTED0123456789ab", "github_pat_11INVENTED0_invented0123456789",
           "AKIAINVENTED0123456", "xoxb-1234-invented-token", FAKE_ENV_SECRET)
PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIEinventedinvented\n-----END RSA PRIVATE KEY-----"


def tool_use(name, path):
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": name, "input": {"file_path": path}}]}})


class Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kairo-sess-priv-"))
        self.vault = self.root / "vault"
        for slug, pid in (("alfa", "PROJ-901"), ("beta", "PROJ-902")):
            (self.vault / "Projects" / slug / "Hipotesis").mkdir(parents=True)
            (self.vault / "Projects" / slug / "_hub.md").write_text(f"---\nid: {pid}\n---\n", encoding="utf-8")
        for a in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            subprocess.run(["git", *a], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.root, check=True, capture_output=True)
        self.transcript = self.root / "t.jsonl"
        self.env = mock.patch.dict(os.environ, {"INVENTED_API_KEY": FAKE_ENV_SECRET, "SHORT_TOKEN": "abc",
                                                "KAIRO_STATE_DIR": str(self.root / "state")})
        self.env.start()
        os.environ.pop("KAIRO_CAPTURE_TRANSCRIPTS", None)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def payload(self):
        return {"session_id": "abcd-5678", "transcript_path": str(self.transcript)}

    def secret_lines(self) -> list[str]:
        said = (f"Mi clave es {SECRETS[0]} y {SECRETS[1]}; GitHub {SECRETS[2]} o {SECRETS[3]}; "
                f"AWS {SECRETS[4]}; Slack {SECRETS[5]}; Authorization: Bearer invented.jwt.token-value; "
                f"url https://api.invented.test/x?api_key=invented123456&q=1&token=invtok98765 "
                f"y el entorno {FAKE_ENV_SECRET}; corto abc.\n{PEM}")
        return [json.dumps({"type": "user", "message": {"content": said}}),
                tool_use("Write", str(self.vault / "Projects/alfa/Hipotesis/H-9011 x.md")),
                "not json " + SECRETS[0]]


class TestRedaction(Base):
    def test_secrets_are_redacted_and_the_jsonl_stays_valid(self):
        self.transcript.write_text("\n".join(self.secret_lines()) + "\n", encoding="utf-8")
        subs = self.transcript.with_suffix("") / "subagents"
        subs.mkdir(parents=True)
        (subs / "agent-a1.jsonl").write_text(json.dumps({"k": SECRETS[2]}) + "\n", encoding="utf-8")
        self.assertEqual(sc.run(self.vault, self.payload(), today="2031-01-02"), ["alfa"])
        base = self.vault / "Projects/alfa/_trazas-agente/cli"
        copy = (base / "2031-01-02__abcd-5678.jsonl").read_text(encoding="utf-8")
        sub = (base / "2031-01-02__abcd-5678" / "subagents" / "agent-a1.jsonl").read_text(encoding="utf-8")
        for s in (*SECRETS, "invented.jwt.token-value", "invented123456", "invtok98765", "MIIEinvented"):
            self.assertNotIn(s, copy + sub, s)
        self.assertIn("«redactado»", copy)
        self.assertIn("corto abc", copy)                       # short values are not secrets
        self.assertIn("&q=1", copy)
        lines = copy.splitlines()
        for ln in lines[:2]:
            json.loads(ln)
        self.assertTrue(lines[2].startswith("not json"))
        json.loads(sub)
        self.assertIn(SECRETS[0], self.transcript.read_text(encoding="utf-8"))   # the original is untouched

    def test_capture_can_be_switched_off(self):
        self.transcript.write_text("\n".join(self.secret_lines()) + "\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"KAIRO_CAPTURE_TRANSCRIPTS": "off"}):
            self.assertEqual(sc.run(self.vault, self.payload(), today="2031-01-02"), [])
        self.assertFalse((self.vault / "Projects/alfa/_trazas-agente").exists())
        self.assertFalse((self.vault / "Projects/alfa/Bitacora").exists())


class TestOneCopy(Base):
    def test_a_session_on_two_projects_is_copied_once_and_both_logs_point_at_it(self):
        self.transcript.write_text("\n".join([
            tool_use("Write", str(self.vault / "Projects/alfa/Hipotesis/H-9011 x.md")),
            tool_use("Write", str(self.vault / "Projects/beta/Hipotesis/H-9012 y.md"))]) + "\n", encoding="utf-8")
        self.assertEqual(sc.run(self.vault, self.payload(), today="2031-01-02"), ["alfa", "beta"])
        rel = "Projects/alfa/_trazas-agente/cli/2031-01-02__abcd-5678.jsonl"
        self.assertTrue((self.vault / rel).is_file())
        self.assertFalse((self.vault / "Projects/beta/_trazas-agente").exists())
        for slug, ref in (("alfa", "H-9011"), ("beta", "H-9012")):
            [e] = action_log.load(self.vault / "Projects" / slug / "Bitacora" / "acciones.jsonl")
            self.assertEqual((e["trace"], e["refs"]), (rel, [ref]))
        status = subprocess.run(["git", "status", "--porcelain", "--", "vault"], cwd=self.root,
                                capture_output=True, text=True).stdout
        self.assertEqual(status.strip(), "")
        sc.run(self.vault, self.payload(), today="2031-01-02")          # resumed: no second entry anywhere
        for slug in ("alfa", "beta"):
            self.assertEqual(len(action_log.load(self.vault / "Projects" / slug / "Bitacora" / "acciones.jsonl")), 1)


if __name__ == "__main__":
    unittest.main()
