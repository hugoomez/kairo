"""Tests for isolation.py: packets handed to isolated agents by file, and the
main thread kept away from paper text. Run: python -m pytest scripts/security

Invented ids and text only."""

from __future__ import annotations

import hashlib
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
import isolation  # noqa: E402

PAPER = "---\nid: P-0981\ntitle: Invented\n---\n\n## Texto completo\n\nTexto inventado.\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-iso-"))
        self.state = self.tmp / "state"
        self.env = mock.patch.dict(os.environ, {"KAIRO_STATE_DIR": str(self.state)}, clear=False)
        self.env.start()
        os.environ.pop("KAIRO_PACKETS_DIR", None)
        os.environ.pop("KAIRO_ALLOW_MAIN_PAPER_READ", None)
        self.vault = self.tmp / "vault"
        (self.vault / "Papers" / "_fuentes").mkdir(parents=True)
        (self.vault / "Projects" / "demo").mkdir(parents=True)
        self.paper = self.vault / "Papers" / "P-0981 invented.md"
        self.paper.write_text(PAPER, encoding="utf-8")

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ev(self, tool: str, tin: dict, agent: str | None = None, agent_id: str | None = "a1") -> dict:
        e = {"tool_name": tool, "tool_input": tin, "cwd": str(self.vault)}
        if agent:
            e["agent_type"] = agent
            if agent_id:
                e["agent_id"] = agent_id
        return e


class TestPacketStore(Base):
    def test_store_names_the_file_by_its_sha256(self):
        got = isolation.store("# Paquete\n\nUna afirmación inventada.\n")
        sha = hashlib.sha256("# Paquete\n\nUna afirmación inventada.\n".encode("utf-8")).hexdigest()
        self.assertEqual(got["sha256"], sha)
        self.assertEqual(Path(got["path"]).name, f"{sha}.md")
        self.assertEqual(Path(got["path"]).parent, self.state / "packets")

    def test_store_is_idempotent(self):
        a, b = isolation.store("igual"), isolation.store("igual")
        self.assertEqual(a, b)


class TestIsolatedAgents(Base):
    def test_isolated_agent_reads_its_packet_and_leaves_a_receipt(self):
        p = isolation.store("# Paquete\n")
        applies, reason, receipt = isolation.check_isolated(
            self.ev("Read", {"file_path": p["path"]}, agent="kairo:fresh-verifier", agent_id="v-7"))
        self.assertTrue(applies)
        self.assertIsNone(reason)
        self.assertEqual(receipt["sha256"], p["sha256"])
        isolation.record_receipt(receipt)
        got = isolation.received(p["sha256"])
        self.assertEqual([r["agent_type"] for r in got], ["fresh-verifier"])
        self.assertEqual(got[0]["agent_id"], "v-7")

    def test_isolated_agent_may_not_read_a_vault_note(self):
        applies, reason, _ = isolation.check_isolated(
            self.ev("Read", {"file_path": str(self.paper)}, agent="screener"))
        self.assertTrue(applies)
        self.assertIn("aislado", reason)

    def test_a_packet_edited_after_storing_is_refused(self):
        p = isolation.store("original\n")
        Path(p["path"]).write_text("cambiado\n", encoding="utf-8")
        _, reason, _ = isolation.check_isolated(self.ev("Read", {"file_path": p["path"]}, agent="novelty-judge"))
        self.assertIn("sha256", reason)

    def test_isolated_agent_may_not_use_another_tool(self):
        for tool, tin in (("Grep", {"pattern": "x", "path": str(self.state)}), ("Bash", {"command": "ls"})):
            _, reason, _ = isolation.check_isolated(self.ev(tool, tin, agent="devils-advocate"))
            self.assertTrue(reason, tool)

    def test_other_agents_and_the_main_thread_are_not_isolated(self):
        self.assertFalse(isolation.check_isolated(self.ev("Read", {"file_path": str(self.paper)},
                                                          agent="kairo:facet-summarizer"))[0])
        self.assertFalse(isolation.check_isolated(self.ev("Read", {"file_path": str(self.paper)}))[0])

    def test_received_is_empty_for_a_packet_nobody_read(self):
        self.assertEqual(isolation.received("0" * 64), [])


class TestMainThreadPaperText(Base):
    def test_main_thread_read_of_a_paper_note_is_blocked(self):
        reason = isolation.main_paper_read(self.ev("Read", {"file_path": str(self.paper)}))
        self.assertIn("paper-reader", reason)

    def test_a_subagent_may_read_a_paper_note(self):
        self.assertIsNone(isolation.main_paper_read(
            self.ev("Read", {"file_path": str(self.paper)}, agent="kairo:paper-reader")))

    def test_main_thread_reads_other_notes(self):
        hub = self.vault / "Projects" / "demo" / "_hub.md"
        hub.write_text("---\nid: PROJ-981\n---\n", encoding="utf-8")
        self.assertIsNone(isolation.main_paper_read(self.ev("Read", {"file_path": str(hub)})))

    def test_main_thread_content_grep_over_papers_is_blocked(self):
        for scope in (str(self.vault), str(self.vault / "Papers"), str(self.paper)):
            r = isolation.main_paper_read(self.ev("Grep", {"pattern": "x", "path": scope, "output_mode": "content"}))
            self.assertTrue(r, scope)
        self.assertIsNone(isolation.main_paper_read(
            self.ev("Grep", {"pattern": "x", "path": str(self.vault), "output_mode": "files_with_matches"})))
        self.assertIsNone(isolation.main_paper_read(
            self.ev("Grep", {"pattern": "x", "path": str(self.vault / "Projects"), "output_mode": "content"})))

    def test_main_thread_shell_readers_naming_papers_are_blocked(self):
        for cmd in ('cat "Papers/P-0981 invented.md"', "head -50 Papers/P-0981*", "grep -r qLDPC Papers/",
                    "Get-Content 'vault\\Papers\\P-0981 invented.md'"):
            self.assertTrue(isolation.main_paper_read(self.ev("Bash", {"command": cmd})), cmd)
        for cmd in ("python scripts/papers/ingest_paper.py verify --vault . --only P-0981",
                    "git add Papers/ && git commit -m x", "ls Papers/"):
            self.assertIsNone(isolation.main_paper_read(self.ev("Bash", {"command": cmd})), cmd)

    def test_main_thread_may_not_print_paper_text_through_a_script(self):
        cmd = "python scripts/papers/cite_text.py --vault . --paper P-0981 --locator 3"
        self.assertTrue(isolation.main_paper_read(self.ev("Bash", {"command": cmd})))
        self.assertIsNone(isolation.main_paper_read(self.ev("Bash", {"command": cmd}, agent="x", agent_id="a")))
        refs = "python scripts/papers/paper_refs.py list --vault . --id P-0981"
        self.assertIsNone(isolation.main_paper_read(self.ev("Bash", {"command": refs})))        # ids only
        self.assertTrue(isolation.main_paper_read(self.ev("Bash", {"command": refs + " --text"})))

    def test_opt_out_env(self):
        with mock.patch.dict(os.environ, {"KAIRO_ALLOW_MAIN_PAPER_READ": "1"}):
            self.assertIsNone(isolation.main_paper_read(self.ev("Read", {"file_path": str(self.paper)})))


class TestCli(Base):
    def run_cli(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(HERE / "isolation.py"), *args], capture_output=True,
                              env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=60)

    def test_store_and_received(self):
        src = self.tmp / "paquete.md"
        src.write_text("# Paquete inventado\n", encoding="utf-8")
        r = self.run_cli("store", str(src))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(self.run_cli("received", "--sha256", out["sha256"]).returncode, 3)
        isolation.record_receipt({"sha256": out["sha256"], "agent_type": "screener", "agent_id": "s1"})
        r = self.run_cli("received", "--sha256", out["sha256"], "--agent", "screener")
        self.assertEqual(r.returncode, 0, r.stdout)


if __name__ == "__main__":
    unittest.main()
