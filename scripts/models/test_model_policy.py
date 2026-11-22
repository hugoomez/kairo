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

    def test_three_tiers_pinned_to_exact_ids(self):
        self.assertEqual(self.r["tiers"], {"fable": "claude-fable-5-2", "opus": "claude-opus-5-5",
                                           "sonnet": "claude-sonnet-5-5"})

    def test_every_task_has_an_effort_and_a_reasoned_recommendation(self):
        for k, t in self.r["tasks"].items():
            self.assertIn(t["effort"], mp.EFFORTS, k)
            rec = t["recommended"]
            self.assertIn(rec["tier"], self.r["tiers"], k)
            self.assertIn(rec["effort"], ("low", "medium", "high"), k)  # xhigh / max offered, never recommended
            self.assertTrue(rec["why"].strip(), k)

    def test_recommendations_follow_the_evidence(self):
        rec = {k: (v["recommended"]["tier"], v["recommended"]["effort"]) for k, v in self.r["tasks"].items()}
        # verifiers and critics: a different, stronger model than Opus, which writes what they check
        for k in ("fresh_verifier", "devils_advocate", "critique"):
            self.assertEqual(rec[k], ("fable", "high"), k)
        # classification and short summaries: low effort
        for k in ("screener", "lit_watch", "digest", "project_chat"):
            self.assertEqual(rec[k][1], "low", k)

    def test_every_task_runs_its_recommendation(self):
        # The shipped policy applies every recommendation (the researcher can still change any of them).
        for k, t in self.r["tasks"].items():
            self.assertEqual((t["tier"], t["effort"]), (t["recommended"]["tier"], t["recommended"]["effort"]), k)

    def test_every_agent_and_tool_agrees_with_the_policy(self):
        self.assertEqual(mp.check(), [])

    def test_agents_that_see_untrusted_paper_text_alone_are_isolated(self):
        raw = mp.load()["tasks"]
        for k in ("fresh_verifier", "devils_advocate", "novelty_judge", "screener", "pr_historian"):
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
        for k in ("fresh_verifier", "devils_advocate", "critique", "theorem_work"):
            self.assertEqual(tier[k], "fable", k)
        for k in ("hypothesis_generation", "preregister", "manuscript_draft", "repo_review",
                  "adjudicate", "sota_synthesizer", "novelty_judge",
                  "explain", "explainer", "pitfall_audit", "serendipity_scan", "spawn_hypothesis", "adr_propose",
                  "paper_to_tool", "plan_study"):
            self.assertEqual(tier[k], "opus", k)
        for k in ("create_project", "lit_watch", "project_chat", "ask_corpus", "digest", "run_local", "kaggle_prepare",
                  "slurm_prepare", "run_analysis", "repo_health", "facet_summarizer", "second_critic",
                  "evolve_program", "adr_check", "evolve_program_job", "estimate_study"):
            self.assertEqual(tier[k], "sonnet", k)

    def test_study_jobs_are_interface_jobs(self):
        for k in ("plan_study", "estimate_study"):
            self.assertEqual(self.r["tasks"][k]["kind"], "job", k)

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

    def test_changing_effort_rewrites_the_agent_frontmatter(self):
        out = mp.set_choice("screener", effort="medium", root=self.root)
        self.assertEqual(out["from"], {"tier": "sonnet", "effort": "low"})
        self.assertEqual(out["to"], {"tier": "sonnet", "effort": "medium"})
        self.assertIn("effort: medium", (self.root / "agents/screener.md").read_text(encoding="utf-8"))
        self.assertEqual(mp.check(self.root), [])

    def test_apply_one_recommendation_and_all(self):
        for k in ("fresh_verifier", "devils_advocate"):
            mp.set_choice(k, tier="opus", root=self.root)
        out = mp.set_choice("fresh_verifier", recommended=True, root=self.root)
        self.assertEqual(out["to"], {"tier": "fable", "effort": "high"})
        self.assertIn("model: claude-fable-5-2", (self.root / "agents/fresh-verifier.md").read_text(encoding="utf-8"))
        out = mp.set_all_recommended(self.root)
        self.assertIn("devils_advocate", out["tasks_changed"])
        self.assertNotIn("fresh_verifier", out["tasks_changed"])
        r = mp.resolve(mp.load(self.root / "config/models.toml"))
        for k, t in r["tasks"].items():
            self.assertEqual((t["tier"], t["effort"]), (t["recommended"]["tier"], t["recommended"]["effort"]), k)
        self.assertEqual(mp.check(self.root), [])
        self.assertEqual(mp.set_all_recommended(self.root)["tasks_changed"], [])

    def test_override_is_validated_and_writes_nothing(self):
        toml = self.root / "config/models.toml"
        before = toml.read_text(encoding="utf-8")
        o = mp.override("critique", "sonnet", None, toml)
        self.assertEqual((o["model"], o["effort"], o["overridden"]), ("claude-sonnet-5-5", "high", True))
        self.assertFalse(mp.override("critique", None, None, toml)["overridden"])
        for bad in (("haiku", None), (None, "extreme")):
            with self.assertRaises(mp.Refused):
                mp.override("critique", *bad, toml)
        with self.assertRaises(mp.Refused):
            mp.override("nope", None, None, toml)
        self.assertEqual(toml.read_text(encoding="utf-8"), before)

    def test_bad_effort_in_the_policy_is_refused(self):
        toml = self.root / "config/models.toml"
        toml.write_text(toml.read_text(encoding="utf-8").replace('effort = "low"', 'effort = "minimal"', 1),
                        encoding="utf-8")
        with self.assertRaises(mp.Refused):
            mp.load(toml)

    def test_a_drifted_effort_fails_the_check(self):
        f = self.root / "agents/screener.md"
        f.write_text(f.read_text(encoding="utf-8").replace("effort: low", "effort: max"), encoding="utf-8")
        self.assertTrue(any("screener" in p and "effort" in p for p in mp.check(self.root)))

    def test_a_drifted_agent_fails_the_check(self):
        f = self.root / "agents/fresh-verifier.md"
        f.write_text(f.read_text(encoding="utf-8").replace("model: claude-fable-5-2", "model: fable"), encoding="utf-8")
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

    def test_the_agents_that_read_paper_text_are_marked_and_held_to_read_tools(self):
        raw = mp.load(self.root / "config/models.toml")["tasks"]
        marked = {k for k, t in raw.items() if t.get("reads_papers")}
        self.assertEqual(marked, {"corpus_reader", "explainer", "facet_summarizer", "frontier_extractor",
                                  "paper_carder", "paper_reader", "sota_synthesizer"})
        self.assertEqual(mp.check(self.root), [])
        for agent, task in (("paper-reader", "paper_reader"), ("sota-synthesizer", "sota_synthesizer")):
            f = self.root / f"agents/{agent}.md"
            text = (ROOT / f"agents/{agent}.md").read_text(encoding="utf-8")
            for extra in ("Bash", "WebFetch", "Write", "Edit", "mcp__smart-connections__reindex", "Agent"):
                f.write_text(text.replace("tools: Read, Grep, Glob\n", f"tools: Read, Grep, Glob, {extra}\n"),
                             encoding="utf-8")
                problems = mp.check(self.root)
                self.assertTrue(any(task in p and "reads paper text" in p and extra in p for p in problems),
                                (extra, problems))
            f.write_text(text.replace("tools: Read, Grep, Glob\n",
                                      "tools: Read, Grep, Glob, mcp__smart-connections__get_note\n"), encoding="utf-8")
            self.assertEqual(mp.check(self.root), [])
            f.write_text(text, encoding="utf-8")

    def test_reads_papers_is_an_agent_flag(self):
        toml = self.root / "config/models.toml"
        toml.write_text(toml.read_text(encoding="utf-8").replace(
            'label = "Redactar el paper"', 'label = "Redactar el paper"\nreads_papers = true'), encoding="utf-8")
        with self.assertRaises(mp.Refused):
            mp.load(toml)

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
        self.assertEqual(mp.main(["--root", str(self.root), "set", "--task", "critique"]), 3)
        self.assertEqual(mp.main(["--root", str(self.root), "set", "--all"]), 3)
        self.assertEqual(mp.main(["--root", str(self.root), "set", "--task", "critique", "--effort", "low"]), 0)
        self.assertEqual(mp.main(["--root", str(self.root), "set", "--all", "--recommended"]), 0)
        self.assertEqual(mp.main(["--root", str(self.root), "override", "--task", "critique", "--tier", "sonnet"]), 0)
        self.assertEqual(json.loads(json.dumps({"ok": True}))["ok"], True)


if __name__ == "__main__":
    unittest.main()
