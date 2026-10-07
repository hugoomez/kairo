"""Tests for estimate_run.py (invented runs and papers; no network, no model).
Run: python -m pytest scripts/estimate"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import estimate_run as er  # noqa: E402


class TestEstimate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-est-"))
        self.run = self.tmp / "run"
        self.run.mkdir()
        cands = [{"key": f"k{i}", "title": "An invented title of ten words or so here",
                  "abstract": "x" * 1000, "prefilter": {"pass": i < 100}} for i in range(130)]
        (self.run / "candidates.json").write_text(json.dumps(cands), encoding="utf-8")
        (self.run / "plan.json").write_text(json.dumps({"facets": [{"id": "A"}, {"id": "B"}, {"id": "C"}]}),
                                            encoding="utf-8")
        self.vault = self.tmp / "vault"
        (self.vault / "Papers").mkdir(parents=True)
        for i in range(12):
            (self.vault / "Papers" / f"P-09{i:02d} x.md").write_text("---\nid: x\n---\n" + "y" * 36000,
                                                                       encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = er.main(list(args))
        return code, json.loads(buf.getvalue())

    def test_screening_counts_pages_and_tokens_from_the_run(self):
        code, out = self.cli("--run", str(self.run))
        self.assertEqual(code, 0, out)
        s = out["stages"]["screening"]
        self.assertEqual(s["subagents"], 3 + 1)          # 100 to read in pages of 40, + 30 prefiltered
        self.assertGreater(s["input_tokens"], 30000)
        self.assertEqual(s["model"], er.policy_model("screener"))

    def test_map_reduce_and_verify_from_the_papers(self):
        ids = [f"P-09{i:02d}" for i in range(12)]
        code, out = self.cli("--vault", str(self.vault), "--papers", *ids)
        self.assertEqual(code, 0, out)
        st = out["stages"]
        self.assertEqual(st["ingest"]["papers"], 12)
        self.assertGreaterEqual(st["map"]["subagents"], 2)               # ≤ 6 papers each
        self.assertEqual(st["reduce"]["subagents"], 1)
        self.assertGreaterEqual(st["verify"]["subagents"], 1)
        self.assertGreater(out["total"]["input_tokens"], st["map"]["input_tokens"])
        self.assertIn("estimación", out["note"])

    def test_a_planned_count_without_notes_uses_a_typical_size(self):
        code, out = self.cli("--planned-papers", "20")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["stages"]["ingest"]["papers"], 20)
        self.assertGreater(out["total"]["wall_minutes"], 0)


if __name__ == "__main__":
    unittest.main()
