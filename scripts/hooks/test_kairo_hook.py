"""Tests for kairo_hook.py, the single entry point of the plugin's hooks.
Run: python -m pytest scripts/hooks

Every test runs the dispatcher as the hook would (a subprocess with JSON on
stdin) against a temp vault, with KAIRO_STATE_DIR pointed at a temp folder.
Invented ids and text only."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "kairo_hook.py"

HYP = "---\nid: H-0971\nproject: PROJ-970\nstatus: propuesta\n---\n\n## Claim\n\nUn claim inventado para la prueba.\n"
PAPER = "---\nid: P-0971\ntitle: Invented\nprojects: [PROJ-970]\nadded: 2031-01-01\n---\n\n## Resumen\n\nTexto.\n"
NEVER = "---\nid: P-0972\nsend: never\n---\n\nPrivado.\n"


class TestKairoHook(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-hook-"))
        self.state = self.tmp / "state"
        self.root = self.tmp / "Kairo"
        self.vault = self.root / "vault"
        self.proj = self.vault / "Projects" / "demo"
        (self.proj / "Hipotesis").mkdir(parents=True)
        (self.vault / "Papers").mkdir()
        (self.proj / "_hub.md").write_text("---\nid: PROJ-970\nname: Demo\n---\n", encoding="utf-8")
        (self.vault / "Papers" / "P-0971 invented.md").write_text(PAPER, encoding="utf-8")
        (self.vault / "Papers" / "P-0972 private.md").write_text(NEVER, encoding="utf-8")
        self.outside = self.tmp / "elsewhere"
        self.outside.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def hook(self, event: str, payload: dict, project_dir: Path | None = None) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "KAIRO_VAULT")}
        env.update({"KAIRO_STATE_DIR": str(self.state), "PYTHONIOENCODING": "utf-8"})
        if project_dir:
            env["CLAUDE_PROJECT_DIR"] = str(project_dir)
        return subprocess.run([sys.executable, str(HOOK), event], input=json.dumps(payload).encode(),
                              capture_output=True, env=env, timeout=120)

    def events(self) -> list[dict]:
        f = self.state / "hook-events.jsonl"
        return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []

    def test_outside_a_vault_every_event_is_a_silent_noop(self):
        for ev, payload in (("pre-tool", {"tool_name": "Bash", "tool_input": {"command": "ls"}}),
                            ("post-write", {"tool_input": {"file_path": str(self.outside / "x.md")}}),
                            ("post-shell", {"tool_input": {"command": "git commit -m x"}}),
                            ("session-end", {})):
            r = self.hook(ev, {**payload, "cwd": str(self.outside)}, project_dir=self.outside)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, b"", b""), ev)
        self.assertEqual(self.events(), [])

    def test_send_guard_blocks_from_any_session_root(self):
        target = str(self.vault / "Papers" / "P-0972 private.md")
        r = self.hook("pre-tool", {"tool_name": "Read", "tool_input": {"file_path": target}, "cwd": str(self.outside)},
                      project_dir=self.outside)
        self.assertEqual(r.returncode, 2)
        self.assertIn("send: never", r.stderr.decode("utf-8"))
        self.assertEqual(self.events()[-1]["outcome"], "blocked")

    def test_shell_check_finds_the_vault_under_the_repo_root(self):
        r = self.hook("pre-tool", {"tool_name": "Bash", "tool_input": {"command": "cat P-0972*"}, "cwd": str(self.root)},
                      project_dir=self.root)
        self.assertEqual(r.returncode, 2)
        r = self.hook("pre-tool", {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": str(self.root)},
                      project_dir=self.root)
        self.assertEqual(r.returncode, 0)

    def test_allowed_events_are_recorded_at_most_once_a_minute(self):
        for _ in range(3):
            self.hook("pre-tool", {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": str(self.vault)},
                      project_dir=self.vault)
        self.assertEqual([e["outcome"] for e in self.events()], ["allowed"])

    def test_hypothesis_write_rebuilds_digest_and_ledger(self):
        note = self.proj / "Hipotesis" / "H-0971 inventada.md"
        note.write_text(HYP, encoding="utf-8")
        r = self.hook("post-write", {"tool_name": "Write", "tool_input": {"file_path": str(note)}, "cwd": str(self.vault)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("H-0971", (self.proj / "_digest.md").read_text(encoding="utf-8"))
        self.assertTrue((self.proj / "_ledger.md").exists())
        self.assertEqual({(e["action"], e["outcome"]) for e in self.events()}, {("digest", "ok"), ("ledger", "ok")})

    def test_paper_write_reindexes_through_the_vaults_own_mcp_config(self):
        fake = self.tmp / "sc" / "dist"
        fake.mkdir(parents=True)
        (fake / "index.js").write_text("", encoding="utf-8")
        marker = self.tmp / "reindexed.txt"
        (fake / "cli-reindex.js").write_text(
            f"require('fs').writeFileSync({json.dumps(str(marker))}, process.argv.slice(2).join(' '));", encoding="utf-8")
        (self.vault / ".mcp.json").write_text(json.dumps(
            {"mcpServers": {"smart-connections": {"command": "node", "args": [str(fake / "index.js")]}}}), encoding="utf-8")
        paper = self.vault / "Papers" / "P-0971 invented.md"
        r = self.hook("post-write", {"tool_name": "Edit", "tool_input": {"file_path": str(paper)}, "cwd": str(self.vault)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--path Papers/", marker.read_text(encoding="utf-8"))
        acts = {e["action"]: e["outcome"] for e in self.events()}
        self.assertEqual((acts.get("reindex"), acts.get("sota_staleness")), ("ok", "ok"))

    def test_paper_write_without_smart_connections_says_so(self):
        paper = self.vault / "Papers" / "P-0971 invented.md"
        self.hook("post-write", {"tool_name": "Edit", "tool_input": {"file_path": str(paper)}, "cwd": str(self.vault)})
        self.assertIn(("reindex", "skipped"), {(e["action"], e["outcome"]) for e in self.events()})

    def test_a_commit_is_logged_in_the_bitacora(self):
        g = lambda *a: subprocess.run(["git", *a], cwd=self.root, capture_output=True, check=True)  # noqa: E731
        g("init", "-q")
        g("config", "user.email", "t@example.invalid")
        g("config", "user.name", "tester")
        note = self.proj / "Hipotesis" / "H-0971 inventada.md"
        note.write_text(HYP, encoding="utf-8")
        g("add", "-A")
        g("commit", "-q", "-m", "Nota inventada")
        r = self.hook("post-shell", {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'Nota inventada'"},
                                     "cwd": str(self.vault)}, project_dir=self.vault)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.events()[-1]["action"], "bitacora_commit")
        self.assertTrue(list((self.proj / "Bitacora").glob("*")), "the notebook got the commit")

    def test_the_literature_profile_leaves_the_notebook_alone(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"KAIRO_PROFILE": "literatura"}):
            r = self.hook("post-shell", {"tool_name": "Bash", "tool_input": {"command": "git commit -m x"},
                                         "cwd": str(self.vault)}, project_dir=self.vault)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("bitacora_commit", [e["action"] for e in self.events()])

    def packet(self, text: str) -> Path:
        import hashlib
        d = self.state / "packets"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{hashlib.sha256(text.encode()).hexdigest()}.md"
        p.write_text(text, encoding="utf-8", newline="")
        return p

    def test_isolated_agent_reads_only_its_packet_and_leaves_a_receipt(self):
        p = self.packet("# Paquete inventado\n")
        r = self.hook("pre-tool", {"tool_name": "Read", "tool_input": {"file_path": str(p)}, "cwd": str(self.vault),
                                   "agent_type": "kairo:fresh-verifier", "agent_id": "ag-1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        reads = (self.state / "packet-reads.jsonl").read_text(encoding="utf-8")
        self.assertIn(p.stem, reads)
        r = self.hook("pre-tool", {"tool_name": "Read", "tool_input": {"file_path": str(self.vault / "Projects" / "demo" / "_hub.md")},
                                   "cwd": str(self.vault), "agent_type": "kairo:fresh-verifier", "agent_id": "ag-1"})
        self.assertEqual(r.returncode, 2)
        self.assertIn("aislado", r.stderr.decode("utf-8"))

    def test_isolated_agent_is_held_even_outside_a_vault(self):
        r = self.hook("pre-tool", {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": str(self.outside),
                                   "agent_type": "screener", "agent_id": "s-1"})
        self.assertEqual(r.returncode, 2)

    def test_main_thread_may_not_read_a_paper_note(self):
        paper = self.vault / "Papers" / "P-0971 invented.md"
        r = self.hook("pre-tool", {"tool_name": "Read", "tool_input": {"file_path": str(paper)}, "cwd": str(self.vault)})
        self.assertEqual(r.returncode, 2)
        self.assertIn("paper-reader", r.stderr.decode("utf-8"))
        r = self.hook("pre-tool", {"tool_name": "Read", "tool_input": {"file_path": str(paper)}, "cwd": str(self.vault),
                                   "agent_type": "kairo:paper-reader", "agent_id": "pr-1"})
        self.assertEqual(r.returncode, 0, r.stderr)


LAUNCHER = HOOK.with_name("kairo_hook.sh")
SH = shutil.which("sh")


@unittest.skipUnless(SH, "no sh on PATH")
class TestLauncher(unittest.TestCase):
    """kairo_hook.sh: the command hooks.json runs. It finds python or python3, and
    without either says so and refuses reads inside a vault (never a silent pass)."""

    setUp = TestKairoHook.setUp
    tearDown = TestKairoHook.tearDown

    def launch(self, event: str, payload: dict, path: str, project_dir: Path) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "KAIRO_VAULT")}
        env.update({"KAIRO_STATE_DIR": str(self.state), "PYTHONIOENCODING": "utf-8", "PATH": path,
                    "CLAUDE_PROJECT_DIR": str(project_dir)})
        return subprocess.run([SH, LAUNCHER.as_posix(), event], input=json.dumps(payload).encode(),
                              capture_output=True, env=env, cwd=project_dir, timeout=120)

    def test_hooks_json_runs_every_hook_through_the_launcher(self):
        hooks = json.loads((HOOK.parents[2] / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        commands = [h["command"] for groups in hooks.values() for g in groups for h in g["hooks"]]
        self.assertTrue(commands)
        for c in commands:
            self.assertTrue(c.startswith('sh "${CLAUDE_PLUGIN_ROOT}/scripts/hooks/kairo_hook.sh" '), c)

    def test_with_python_on_the_path_the_guard_runs(self):
        target = str(self.vault / "Papers" / "P-0972 private.md")
        path = os.pathsep.join([str(Path(sys.executable).parent), str(Path(SH).parent)])
        r = self.launch("pre-tool", {"tool_name": "Read", "tool_input": {"file_path": target}}, path, self.vault)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("send: never", r.stderr.decode("utf-8"))

    def test_without_python_a_read_in_a_vault_is_refused_and_said(self):
        empty = self.tmp / "empty-path"
        empty.mkdir()
        payload = {"tool_name": "Read", "tool_input": {"file_path": str(self.vault / "Papers" / "P-0971 invented.md")}}
        r = self.launch("pre-tool", payload, str(empty), self.vault)
        self.assertEqual(r.returncode, 2)
        self.assertIn("no se encontró Python", r.stderr.decode("utf-8"))
        r = self.launch("pre-tool", payload, str(empty), self.root)              # the vault/ subfolder counts
        self.assertEqual(r.returncode, 2)

    def test_without_python_outside_a_vault_or_after_a_write_nothing_is_blocked(self):
        empty = self.tmp / "empty-path"
        empty.mkdir()
        r = self.launch("pre-tool", {"tool_name": "Bash", "tool_input": {"command": "ls"}}, str(empty), self.outside)
        self.assertEqual(r.returncode, 0)
        self.assertIn("no se encontró Python", r.stderr.decode("utf-8"))        # said, even here
        r = self.launch("post-write", {"tool_input": {"file_path": str(self.vault / "x.md")}}, str(empty), self.vault)
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
