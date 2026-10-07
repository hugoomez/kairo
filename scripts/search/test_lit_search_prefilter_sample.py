"""lit_search: when too many candidates are prefiltered out to read them all, a
reproducible sample of them is screened and the record estimates how many
includes the prefilter cost (invented ids and text only)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lit_search as ls  # noqa: E402

PLAN = {"description": "invented request", "facets": [{"id": "A", "term": "toy code"},
                                                     {"id": "B", "term": "toy decoder"}],
        "sources": ["arxiv"], "from": "2030-01-01"}


def cand(i: int, passing: bool) -> dict:
    return {"key": f"arxiv:2999.{i:05d}", "title": f"Invented paper number {i} on toy codes", "authors": ["A B"],
            "year": 2030, "years": [2030], "date": "2030-02-01", "doi": None, "arxiv": f"2999.{i:05d}",
            "venues": [], "abstract": "An invented abstract.", "citations": None, "url": None,
            "facets": {"A": "toy code"}, "sources": ["arxiv"], "queries": ["Q001"], "anchor": False,
            "best_rank": i, "prefilter": {"facets": ["A"], "need": 2, "pass": passing},
            "retraction": {"status": "clear", "evidence": [], "notice_for": [], "lost": []}}


class PrefilterSample(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name) / "run"
        self.run.mkdir()
        self.env = mock.patch.dict(os.environ, {"KAIRO_PACKETS_DIR": str(Path(self.tmp.name) / "packets")})
        self.env.start()
        plan = ls.load_plan_dict(PLAN)
        ls.save(self.run / "plan.json", {**plan, "tool": ls.TOOL, "date": "2030-06-01"})
        ls.save(self.run / "queries.json", [])
        ls.save(self.run / "candidates.json", [cand(i, i < 2) for i in range(132)])
        zero = {"checked": 0, "removed": 0, "lost": 0}
        ls.save(self.run / "retraction.json", {"results": [], "counts": {"crossref": zero, "arxiv": zero}})

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def sample_keys(self) -> list[str]:
        keys, off = [], 0
        while off is not None:
            out = ls.cmd_show(self.run, 40, off, prefiltered=True, sample=60)
            keys += [c["key"] for c in out["candidates"]]
            off = out["next_offset"]
        return keys

    def test_sample_is_reproducible_recorded_and_paged(self):
        keys = self.sample_keys()
        self.assertEqual(len(keys), 60)
        self.assertEqual(len(set(keys)), 60)
        self.assertEqual(keys, self.sample_keys())               # the same run gives the same sample
        rec = ls.load(self.run / "prefilter_sample.json")
        self.assertEqual((rec["size"], rec["of"]), (60, 130))
        self.assertEqual(sorted(rec["keys"]), sorted(keys))
        self.assertTrue(all(p.get("sample") for p in ls.load(self.run / "pages.json").values()))

    def test_screen_estimates_the_includes_the_prefilter_lost(self):
        keys = self.sample_keys()
        decisions = {k: {"decision": "exclude", "reason": "relevancia baja", "why": "only an invented mention"}
                     for k in ["arxiv:2999.00000", "arxiv:2999.00001"] + keys}
        for k in keys[:3]:
            decisions[k] = {"decision": "include", "relevance": "media", "why": "Invented toy decoder on toy codes."}
        ls.save(self.run / "decisions.json", decisions)
        out = ls.cmd_screen(self.run, self.run / "decisions.json")
        self.assertEqual(out["counts"]["muestra_prefiltro"],
                         {"leidos": 60, "de": 130, "incluidos": 3, "perdidos_estimados": 4})
        text = (self.run / "busqueda.md").read_text(encoding="utf-8")
        self.assertIn("Muestra del prefiltro", text)
        self.assertIn("≈ 4", text)


if __name__ == "__main__":
    unittest.main()
