"""Tests for session_capture.py. Run: python -m pytest scripts/traces

Invented vault and transcript (PROJ-900, H-9001)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "bitacora"))
import action_log  # noqa: E402
import session_capture as sc  # noqa: E402


def tool_use(name, path):
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": name, "input": {"file_path": path}}]}})


class TestSessionCapture(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kairo-sess-"))
        self.vault = self.root / "vault"
        (self.vault / "Projects" / "demo" / "Hipotesis").mkdir(parents=True)
        (self.vault / "Projects" / "demo" / "_hub.md").write_text("---\nid: PROJ-900\n---\n", encoding="utf-8")
        for a in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            subprocess.run(["git", *a], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.root, check=True, capture_output=True)
        self.transcript = self.root / "t.jsonl"

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write_transcript(self, *lines):
        self.transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def payload(self):
        return {"session_id": "abcd-1234", "transcript_path": str(self.transcript)}

    def test_a_session_that_wrote_a_hypothesis_is_kept_logged_and_committed(self):
        hyp = self.vault / "Projects" / "demo" / "Hipotesis" / "H-9001 x.md"
        self.write_transcript(
            json.dumps({"type": "user", "message": {"content": "hola"}}),
            tool_use("Read", str(hyp)),
            tool_use("Write", str(hyp)),
            "not json",
        )
        self.assertEqual(sc.run(self.vault, self.payload(), today="2026-01-02"), ["demo"])
        rel = "Projects/demo/_trazas-agente/cli/2026-01-02__abcd-1234.jsonl"
        self.assertTrue((self.vault / rel).exists())
        [e] = action_log.load(self.vault / "Projects" / "demo" / "Bitacora" / "acciones.jsonl")
        self.assertEqual((e["action"], e["refs"], e["trace"]), ("cli_session", ["H-9001"], rel))
        status = subprocess.run(["git", "status", "--porcelain", "--", "vault"], cwd=self.root,
                                capture_output=True, text=True).stdout
        self.assertEqual(status.strip(), "")

    def test_a_resumed_session_updates_its_copy_without_a_second_entry(self):
        hyp = "Projects/demo/Hipotesis/H-9001 x.md"   # relative paths resolve against the vault
        self.write_transcript(tool_use("Edit", hyp))
        sc.run(self.vault, self.payload(), today="2026-01-02")
        self.write_transcript(tool_use("Edit", hyp), tool_use("Edit", hyp))
        sc.run(self.vault, self.payload(), today="2026-01-02")
        log = self.vault / "Projects" / "demo" / "Bitacora" / "acciones.jsonl"
        self.assertEqual(len(action_log.load(log)), 1)
        copy = self.vault / "Projects/demo/_trazas-agente/cli/2026-01-02__abcd-1234.jsonl"
        self.assertEqual(len(copy.read_text(encoding="utf-8").splitlines()), 2)

    def test_read_only_or_outside_sessions_are_not_kept(self):
        self.write_transcript(
            tool_use("Read", str(self.vault / "Projects/demo/Hipotesis/H-9001 x.md")),
            tool_use("Write", str(self.root / "elsewhere.md")),
            tool_use("Write", str(self.vault / "Projects/demo/_hub.md")),
        )
        self.assertEqual(sc.run(self.vault, self.payload()), [])
        self.assertFalse((self.vault / "Projects/demo/_trazas-agente").exists())

    def test_a_literature_session_is_kept_with_its_subagent_transcripts(self):
        """Writing the map, or running a literature script on the project, keeps the
        transcript — and the subagents' own transcripts (the facet summaries)."""
        sota = self.vault / "Projects" / "demo" / "Estado-del-arte.md"
        self.write_transcript(
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash",
                 "input": {"command": "python scripts/papers/ingest_paper.py add --vault . --project PROJ-900 "
                                      "--arxiv 0000.00001"}}]}}),
            tool_use("Write", str(sota)))
        subs = self.transcript.with_suffix("") / "subagents"
        subs.mkdir(parents=True)
        (subs / "agent-a1.jsonl").write_text('{"facet": "A"}\n', encoding="utf-8")
        self.assertEqual(sc.run(self.vault, self.payload(), today="2026-01-02"), ["demo"])
        base = self.vault / "Projects/demo/_trazas-agente/cli"
        self.assertTrue((base / "2026-01-02__abcd-1234.jsonl").exists())
        self.assertTrue((base / "2026-01-02__abcd-1234" / "subagents" / "agent-a1.jsonl").exists())
        [e] = action_log.load(self.vault / "Projects" / "demo" / "Bitacora" / "acciones.jsonl")
        self.assertIn("script(s) de literatura", e["summary"])
        self.assertIn("subagentes", e["summary"])
        status = subprocess.run(["git", "status", "--porcelain", "--", "vault"], cwd=self.root,
                                capture_output=True, text=True).stdout
        self.assertEqual(status.strip(), "")

    def test_a_literature_script_on_another_folder_or_a_plain_command_is_not_kept(self):
        self.write_transcript(
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls Projects/demo"}},
                {"type": "tool_use", "name": "Bash",
                 "input": {"command": "python lit_search.py run --plan p.json --out /tmp/run"}}]}}))
        self.assertEqual(sc.run(self.vault, self.payload()), [])

    def test_missing_transcript_and_main_never_fail(self):
        self.assertEqual(sc.run(self.vault, {"transcript_path": str(self.root / "nope")}), [])
        import io
        old = sys.stdin
        sys.stdin = io.StringIO("{bad json")
        try:
            self.assertEqual(sc.main(["--vault", str(self.vault)]), 0)
        finally:
            sys.stdin = old


if __name__ == "__main__":
    unittest.main()
