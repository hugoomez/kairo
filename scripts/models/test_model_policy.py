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
                  "slurm_prepare", "run_analysis", "repo_health", "facet_searcher", "facet_summarizer", "second_critic",
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

    def test_cli(self):
        code = mp.main(["--root", str(self.root), "set", "--task", "critique", "--tier", "sonnet"])
        self.assertEqual(code, 0)
        self.assertEqual(mp.model_for("critique", self.root / "config/models.toml"), "claude-sonnet-5-5")
        self.assertEqual(mp.main(["--root", str(self.root), "set", "--task", "nope", "--tier", "opus"]), 3)
        self.assertEqual(json.loads(json.dumps({"ok": True}))["ok"], True)


if __name__ == "__main__":
    unittest.main()
