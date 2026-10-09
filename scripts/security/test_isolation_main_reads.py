"""isolation.main_paper_read: every way the main thread could put third-party
paper text in its own context — paper notes and cards, the kept source bytes,
captured transcripts, search material holding abstracts — through Read,
content Grep, Smart Connections get_note and the shell (H10).

Invented ids and text only. Run: python -m pytest scripts/security"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import isolation  # noqa: E402
import send_guard  # noqa: E402

PLUGIN = HERE.parents[1]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-iso-main-"))
        self.env = mock.patch.dict(os.environ, {"KAIRO_STATE_DIR": str(self.tmp / "state")}, clear=False)
        self.env.start()
        os.environ.pop("KAIRO_ALLOW_MAIN_PAPER_READ", None)
        v = self.vault = self.tmp / "vault"
        (v / "Papers" / "_fuentes" / "P-0981").mkdir(parents=True)
        (v / "Papers" / "_fichas").mkdir()
        (v / "Papers" / "P-0981 invented.md").write_text("---\nid: P-0981\n---\n\nTexto inventado.\n", encoding="utf-8")
        (v / "Papers" / "_fuentes" / "P-0981" / "source.pdf").write_bytes(b"%PDF-1.4 invented")
        (v / "Papers" / "_fuentes" / "P-0981" / "source.html").write_text("<p>invented</p>", encoding="utf-8")
        p = self.proj = v / "Projects" / "demo"
        (p / "_trazas-agente" / "cli").mkdir(parents=True)
        (p / "_trazas-agente" / "cli" / "2031-01-01__abc.jsonl").write_text("{}\n", encoding="utf-8")
        (p / "_busquedas" / "2031-01-01" / "raw").mkdir(parents=True)
        (p / "_busquedas" / "2031-01-01" / "candidates.json").write_text("[]", encoding="utf-8")
        (p / "_busquedas" / "2031-01-01" / "raw" / "Q001-1.json").write_text("{}", encoding="utf-8")
        (p / "_busquedas" / "2031-01-01" / "ranked.md").write_text("# ranked\n", encoding="utf-8")
        (p / "_vigilancia" / "raw-2031-01-02").mkdir(parents=True)
        (p / "_vigilancia" / "raw-2031-01-02" / "W1-1.json").write_text("{}", encoding="utf-8")
        (p / "Hipotesis").mkdir()
        (p / "Hipotesis" / "H-0981.md").write_text("---\nid: H-0981\n---\n\nClaim inventado.\n", encoding="utf-8")
        (p / "_hub.md").write_text("---\nid: PROJ-981\n---\n", encoding="utf-8")

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ev(self, tool: str, tin: dict, cwd: Path | None = None, agent: str | None = None) -> dict:
        e = {"tool_name": tool, "tool_input": tin, "cwd": str(cwd or self.vault)}
        if agent:
            e["agent_type"], e["agent_id"] = agent, "a-1"
        return e

    def blocked(self, tool: str, tin: dict, **kw) -> str | None:
        return isolation.main_paper_read(self.ev(tool, tin, **kw), self.vault)


PROTECTED = ("Papers/_fuentes/P-0981/source.pdf", "Papers/_fuentes/P-0981/source.html",
             "Projects/demo/_trazas-agente/cli/2031-01-01__abc.jsonl",
             "Projects/demo/_busquedas/2031-01-01/candidates.json",
             "Projects/demo/_busquedas/2031-01-01/raw/Q001-1.json",
             "Projects/demo/_vigilancia/raw-2031-01-02/W1-1.json")


class TestReadAndGetNote(Base):
    def test_kept_bytes_transcripts_and_search_material_are_closed_to_read(self):
        for rel in PROTECTED:
            self.assertTrue(self.blocked("Read", {"file_path": str(self.vault / rel)}), rel)
            self.assertTrue(self.blocked("Read", {"file_path": rel.replace("/", "\\")}), rel)   # Windows style
            self.assertTrue(self.blocked("Read", {"file_path": str(self.vault / rel).upper()}), rel)
            self.assertIsNone(self.blocked("Read", {"file_path": str(self.vault / rel)}, agent="paper-reader"), rel)

    def test_other_project_files_stay_open(self):
        for rel in ("Projects/demo/_busquedas/2031-01-01/ranked.md", "Projects/demo/Hipotesis/H-0981.md",
                    "Projects/demo/_hub.md"):
            self.assertIsNone(self.blocked("Read", {"file_path": str(self.vault / rel)}), rel)

    def test_get_note_on_a_paper_note_or_card_is_refused_like_read(self):
        for np_ in ("Papers/P-0981 invented.md", "Papers/_fichas/P-0981.md", "papers\\P-0981 invented.md"):
            r = self.blocked("mcp__smart-connections__get_note", {"notePath": np_})
            self.assertTrue(r, np_)
        self.assertIsNone(self.blocked("mcp__smart-connections__get_note",
                                       {"notePath": "Projects/demo/Hipotesis/H-0981.md"}))
        self.assertIsNone(self.blocked("mcp__smart-connections__get_note", {"notePath": "Papers/P-0981 invented.md"},
                                       agent="paper-reader"))

    def test_opt_out_keeps_its_meaning(self):
        with mock.patch.dict(os.environ, {"KAIRO_ALLOW_MAIN_PAPER_READ": "1"}):
            for rel in PROTECTED:
                self.assertIsNone(self.blocked("Read", {"file_path": str(self.vault / rel)}), rel)
        with mock.patch.dict(os.environ, {"KAIRO_ALLOW_MAIN_PAPER_READ": "clean"}):     # clean opens notes only
            for rel in PROTECTED:
                self.assertTrue(self.blocked("Read", {"file_path": str(self.vault / rel)}), rel)


class TestContentGrep(Base):
    def g(self, path, **kw):
        return self.blocked("Grep", {"pattern": "x", "path": str(path), "output_mode": "content", **kw})

    def test_a_scope_holding_transcripts_or_search_material_is_refused(self):
        for scope in (self.vault / "Projects", self.proj, self.proj / "_trazas-agente",
                      self.proj / "_busquedas", self.vault / "Papers" / "_fuentes"):
            self.assertTrue(self.g(scope), scope)

    def test_a_glob_or_type_that_leaves_them_out_passes(self):
        self.assertIsNone(self.g(self.vault / "Projects", glob="*.md"))
        self.assertIsNone(self.g(self.vault / "Projects", type="md"))
        self.assertIsNone(self.g(self.proj / "Hipotesis"))
        self.assertTrue(self.g(self.vault / "Projects", glob="*.jsonl"))
        self.assertTrue(self.g(self.vault, glob="*.md"))                  # the paper notes are .md


SHELL_BLOCKED = (
    "cd Papers; cat P-0981*",
    "cd papers && type P-0981*",
    'Get-Content "Papers\\P-0981 invented.md"',
    "more PAPERS/P-0981*",
    "strings Papers/_fuentes/P-0981/source.pdf",
    "xxd Papers/_fuentes/P-0981/source.pdf | head",
    "cp 'Papers/P-0981 invented.md' /tmp/x.md",
    "copy Papers\\P-0981* x.txt",
    "python -c \"print(open('Papers/P-0981 invented.md').read())\"",
    "python - <<'EOF'\nprint(open('Papers/P-0981 invented.md').read())\nEOF",
    "node -e \"console.log(require('fs').readFileSync('Papers/P-0981 invented.md','utf8'))\"",
    "perl -ne print Papers/P-0981*",
    "powershell -c \"[IO.File]::ReadAllText('Papers/P-0981 invented.md')\"",
    "jq . Projects/demo/_busquedas/2031-01-01/candidates.json",
    "cat Projects/demo/_trazas-agente/cli/*.jsonl",
    "head Projects/demo/_vigilancia/raw-2031-01-02/W1-1.json",
    "grep -r qubit Projects/demo/_busquedas/2031-01-01/raw",
    "git show HEAD:'Papers/P-0981 invented.md'",
    "git show HEAD",
    "git diff HEAD~1",
    "git log -p -3",
    "git log --patch",
    "git cat-file -p HEAD:Papers",
    "git grep qubit",
    "git blame 'Papers/P-0981 invented.md'",
)

SHELL_ALLOWED = (
    "ls Papers/",
    "git status",
    "git add Papers/ && git commit -m x",
    "git log --oneline -5",
    "git log --stat -3",
    "git show --stat HEAD",
    "git diff --name-only HEAD~1",
    "git diff --name-status",
    "cat Projects/demo/_hub.md",
    "grep -n Claim Projects/demo/Hipotesis/H-0981.md",
    "python scripts/papers/ingest_paper.py verify --vault . --only P-0981",
    'python "${CLAUDE_PLUGIN_ROOT}/scripts/citations/resolve_refs.py" --papers ./Papers --vault .',
    f'python "{(PLUGIN / "scripts" / "citations" / "resolve_refs.py").as_posix()}" --papers "C:\\v\\Papers"',
)


class TestShell(Base):
    def test_readers_naming_protected_material_and_git_content_commands_are_refused(self):
        for cmd in SHELL_BLOCKED:
            self.assertTrue(self.blocked("Bash", {"command": cmd}), cmd)
            self.assertTrue(self.blocked("PowerShell", {"command": cmd}), cmd)

    def test_everyday_commands_and_kairos_own_scripts_pass(self):
        for cmd in SHELL_ALLOWED:
            self.assertIsNone(self.blocked("Bash", {"command": cmd}), cmd)

    def test_a_plugin_script_does_not_open_the_rest_of_the_command_line(self):
        cmd = 'python "${CLAUDE_PLUGIN_ROOT}/scripts/citations/resolve_refs.py" --papers Papers; cat Papers/P-0981*'
        self.assertTrue(self.blocked("Bash", {"command": cmd}))
        self.assertTrue(self.blocked("Bash", {"command": "python Papers/scripts/papers/x.py Papers/P-0981.md"}))

    def test_a_reader_run_from_inside_a_protected_folder_is_refused(self):
        self.assertTrue(self.blocked("Bash", {"command": "cat P-0981*"}, cwd=self.vault / "Papers"))
        self.assertTrue(self.blocked("Bash", {"command": "cat *.json"}, cwd=self.proj / "_busquedas" / "2031-01-01"))
        self.assertIsNone(self.blocked("Bash", {"command": "cat H-0981.md"}, cwd=self.proj / "Hipotesis"))

    def test_git_content_commands_outside_the_vault_pass(self):
        other = self.tmp / "code"
        other.mkdir()
        self.assertIsNone(self.blocked("Bash", {"command": "git diff HEAD~1"}, cwd=other))
        self.assertIsNone(self.blocked("Bash", {"command": "git show HEAD"}, cwd=other))
        self.assertTrue(self.blocked("Bash", {"command": f'git -C "{self.vault}" show HEAD'}, cwd=other))

    def test_subagents_and_the_opt_out_are_not_held(self):
        self.assertIsNone(self.blocked("Bash", {"command": "cd Papers; cat P-0981*"}, agent="x"))
        with mock.patch.dict(os.environ, {"KAIRO_ALLOW_MAIN_PAPER_READ": "1"}):
            self.assertIsNone(self.blocked("Bash", {"command": "git show HEAD"}))

    def test_one_reader_list_serves_both_guards(self):
        self.assertIs(send_guard.SHELL_READER, isolation.SHELL_READER)
        for word in ("xargs", "cat", "python", "cp", "Get-Content"):
            self.assertTrue(isolation.SHELL_READER.search(f"x | {word} y"), word)


if __name__ == "__main__":
    unittest.main()
