"""Tests for quality_report.py (invented run, gold set and vault)."""

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
sys.path.insert(0, str(HERE.parent / "papers"))
import quality_report as ek  # noqa: E402
import test_check_sota as tcs  # noqa: E402


class Eval(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = ek.main(list(args))
        return code, json.loads(buf.getvalue())

    def test_search_recall_against_a_gold_set(self):
        run = self.tmp / "run"
        run.mkdir()
        cands = [
            {"title": "Toy codes A", "doi": "10.0000/a", "arxiv": None, "sources": ["openalex", "s2"],
             "screen": {"decision": "include"}},
            {"title": "Toy codes B", "doi": None, "arxiv": "0000.00002", "sources": ["arxiv"],
             "screen": {"decision": "exclude", "reason": "relevancia baja"}},
            {"title": "Something else entirely", "doi": "10.0000/c", "arxiv": None, "sources": ["dblp"],
             "screen": {"decision": "include"}},
        ]
        (run / "screened.json").write_text(json.dumps(cands), encoding="utf-8")
        gold = self.tmp / "gold.json"
        gold.write_text(json.dumps([{"doi": "10.0000/A"}, {"arxiv": "0000.00002v2"}, {"title": "Toy codes Z"}]),
                        encoding="utf-8")
        code, out = self.cli("search", "--run", str(run), "--gold", str(gold))
        self.assertEqual(code, 0, out)
        self.assertEqual((out["gold"], out["recall_identified"], out["recall_included"]), (3, 0.667, 0.333))
        self.assertEqual(out["missed"], ["Toy codes Z"])
        self.assertEqual(out["excluded_but_gold"], ["0000.00002v2"])
        self.assertEqual(out["included_not_in_gold"], 1)
        self.assertEqual(out["found_by_source"], {"openalex": 1, "s2": 1, "arxiv": 1})

    def test_sota_and_vault_rates_and_history(self):
        t = tcs.CheckSota("test_a_clean_document_passes")
        t.setUp()
        try:
            code, out = self.cli("all", "--vault", str(t.vault), "--project-dir", "Projects/toy", "--log")
            self.assertEqual(code, 0, out)
            s = out["sota"]
            self.assertEqual(s["citations"], 9)
            self.assertGreater(s["locator_error_rate"], 0)
            self.assertEqual(s["number_error_rate"], round(1 / s["numbers_checked"], 3))
            self.assertEqual(out["vault"]["integrity"], {"legacy": 3})
            hist = (t.p / "_eval" / "historial.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(hist), 1)
        finally:
            t.tearDown()


if __name__ == "__main__":
    unittest.main()
