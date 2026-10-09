"""The model policy is the one source of truth: these tests fail if any
component's model diverges from config/models.toml."""
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "security"))
import model_policy as mp  # noqa: E402

ROOT = HERE.parents[1]


def _evolve():
    spec = importlib.util.spec_from_file_location("evolve_run", ROOT / "skills/evolve-program/scripts/evolve_run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestPolicy(unittest.TestCase):
    def setUp(self):
        self.r = mp.resolve(mp.load())

    def test_two_tiers_pinned_to_exact_ids(self):
        self.assertEqual(self.r["tiers"], {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5"})

    def test_every_agent_and_tool_agrees_with_the_policy(self):
        self.assertEqual(mp.check(), [])

    def test_agents_that_see_untrusted_paper_text_alone_are_isolated(self):
        raw = mp.load()["tasks"]
        for k in ("fresh_verifier", "devils_advocate", "novelty_judge", "screener"):
            self.assertIs(raw[k].get("isolated"), True, k)
            tools = mp._frontmatter_tools((ROOT / raw[k]["file"]).read_text(encoding="utf-8"))
            self.assertEqual(tools, ["Read"], k)

    def test_the_hook_holds_exactly_the_isolated_agents(self):
        # isolated agents read their packet through the vault hook (scripts/security/isolation.py),
        # which must know every one of them, or one would read the whole vault with its Read tool
        import isolation
        raw = mp.load()["tasks"]
        names = {Path(t["file"]).stem for t in raw.values() if t.get("isolated")}
        self.assertEqual(names, set(isolation.ISOLATED_AGENTS))

    def test_no_kairo_component_uses_haiku(self):
        for t in self.r["tasks"].values():
            self.assertNotIn("haiku", t["model"])
        for f in (ROOT / "agents").glob("*.md"):
            self.assertNotIn("haiku", (mp._frontmatter_model(f.read_text(encoding="utf-8")) or ""), f.name)

    def test_tier_assignment(self):
        tier = {k: v["tier"] for k, v in self.r["tasks"].items()}
        for k in ("hypothesis_generation", "critique", "preregister", "theorem_work", "manuscript_draft", "repo_review",
                  "adjudicate", "fresh_verifier", "devils_advocate", "sota_synthesizer", "novelty_judge"):
            self.assertEqual(tier[k], "opus", k)
        for k in ("create_project", "lit_watch", "project_chat", "ask_corpus", "digest", "run_local", "kaggle_prepare",
                  "slurm_prepare", "run_analysis", "repo_health", "facet_summarizer", "second_critic",
                  "evolve_program"):
            self.assertEqual(tier[k], "sonnet", k)

    def test_evolve_default_comes_from_the_policy_and_opus_is_selectable(self):
        ev = _evolve()
        self.assertEqual(ev.policy_model(None), ("sonnet", "claude-sonnet-5-5"))
        self.assertEqual(ev.policy_model("opus"), ("opus", "claude-opus-5-5"))
        self.assertEqual(ev.policy_model("claude-opus-5-5"), ("opus", "claude-opus-5-5"))
        for bad in ("haiku", "claude-haiku-4-5", "opus-latest"):
            with self.assertRaises(SystemExit):
                ev.policy_model(bad)


class TestRefusals(unittest.TestCase):
    def _write(self, text):
        d = Path(tempfile.mkdtemp())
        p = d / "models.toml"
        p.write_text(text, encoding="utf-8")
        return p

    def test_aliases_are_refused(self):
        for model in ("opus", "sonnet", "claude-opus-latest", "claude-opus"):
            p = self._write(f'[tiers]\nopus = "{model}"\n[tasks.x]\nkind = "job"\ntier = "opus"\n')
            with self.assertRaises(mp.Refused, msg=model):
                mp.load(p)

    def test_unknown_tier_or_kind_is_refused(self):
        with self.assertRaises(mp.Refused):
            mp.load(self._write('[tiers]\nopus = "claude-opus-5-5"\n[tasks.x]\nkind = "job"\ntier = "haiku"\n'))
        with self.assertRaises(mp.Refused):
            mp.load(self._write('[tiers]\nopus = "claude-opus-5-5"\n[tasks.x]\nkind = "script"\ntier = "opus"\n'))


class TestSetTier(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        shutil.copytree(ROOT / "config", self.root / "config")
        shutil.copytree(ROOT / "agents", self.root / "agents")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_changing_an_agent_tier_rewrites_its_frontmatter_and_keeps_the_rest(self):
        before = (self.root / "agents/facet-summarizer.md").read_text(encoding="utf-8")
        out = mp.set_tier("facet_summarizer", "opus", self.root)
        self.assertEqual((out["from"], out["to"], out["model"]), ("sonnet", "opus", "claude-opus-5-5"))
        after = (self.root / "agents/facet-summarizer.md").read_text(encoding="utf-8")
        self.assertIn("model: claude-opus-5-5", after)
        self.assertEqual(after.replace("claude-opus-5-5", "claude-sonnet-5-5"), before)
        self.assertEqual(mp.check(self.root), [])
        toml = (self.root / "config/models.toml").read_text(encoding="utf-8")
        self.assertIn('[tasks.facet_summarizer]\nkind = "agent"\nfile = "agents/facet-summarizer.md"\ntier = "opus"', toml)

    def test_a_drifted_agent_fails_the_check(self):
        f = self.root / "agents/fresh-verifier.md"
        f.write_text(f.read_text(encoding="utf-8").replace("model: claude-opus-5-5", "model: opus"), encoding="utf-8")
        self.assertTrue(any("fresh_verifier" in p for p in mp.check(self.root)))

    def test_an_empty_tools_line_fails_the_check(self):
        # `tools: ""` reads as "no tools" but Claude Code gives the agent every tool.
        f = self.root / "agents/facet-summarizer.md"
        f.write_text(f.read_text(encoding="utf-8").replace("tools: Read, Grep, Glob", 'tools: ""'), encoding="utf-8")
        self.assertTrue(any("facet_summarizer" in p and "inherits every tool" in p for p in mp.check(self.root)))

    def test_an_isolated_agent_with_a_file_or_shell_tool_fails_the_check(self):
        for tools in ("Read, Grep", "Bash", "Read, WebFetch"):
            f = self.root / "agents/fresh-verifier.md"
            text = (ROOT / "agents/fresh-verifier.md").read_text(encoding="utf-8")
            f.write_text(text.replace("tools: Read\n", f"tools: {tools}\n"), encoding="utf-8")
            self.assertTrue(any("fresh_verifier" in p and "isolated" in p for p in mp.check(self.root)), tools)

    def test_isolated_is_an_agent_flag(self):
        toml = self.root / "config/models.toml"
        toml.write_text(toml.read_text(encoding="utf-8").replace(
            'label = "Abogado del diablo (crítica)"', 'label = "Abogado del diablo (crítica)"\nisolated = true'),
            encoding="utf-8")
        with self.assertRaises(mp.Refused):
            mp.load(toml)

    def test_cli(self):
        code = mp.main(["--root", str(self.root), "set", "--task", "critique", "--tier", "sonnet"])
        self.assertEqual(code, 0)
        self.assertEqual(mp.model_for("critique", self.root / "config/models.toml"), "claude-sonnet-5-5")
        self.assertEqual(mp.main(["--root", str(self.root), "set", "--task", "nope", "--tier", "opus"]), 3)
        self.assertEqual(json.loads(json.dumps({"ok": True}))["ok"], True)


if __name__ == "__main__":
    unittest.main()
