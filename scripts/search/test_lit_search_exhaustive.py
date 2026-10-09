"""lit_search: with `exhaustive`, an arXiv / OpenAlex query whose total is over
max_per_query has its date window split in halves until each part is read
whole, instead of keeping the top results by relevance (invented works only)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lit_search as ls  # noqa: E402

START = date(2020, 1, 1)
WORKS = [{"id": f"https://openalex.org/W{i}", "doi": f"https://doi.org/10.9999/toy.{i}",
          "title": f"Invented toy parallelism study number {i} of many", "publication_year": (START + timedelta(i)).year,
          "publication_date": (START + timedelta(i)).isoformat(), "authorships": [], "cited_by_count": 0,
          "abstract_inverted_index": {"toy": [0], "parallelism": [1]}} for i in range(1500)]
PLAN = {"description": "invented broad question", "facets": [{"id": "A", "term": "toy parallelism"}],
        "sources": ["openalex"], "from": START.isoformat(), "to": (START + timedelta(1499)).isoformat(),
        "per_query": 100, "max_per_query": 1000, "enrich": False}


def fetch(url: str, headers: dict) -> bytes:
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    flt = dict(x.split(":", 1) for x in q.get("filter", [""])[0].split(",") if x)
    lo, hi = flt.get("from_publication_date", "0000"), flt.get("to_publication_date", "9999")
    hits = [w for w in WORKS if lo <= w["publication_date"] <= hi]
    per, page = int(q["per-page"][0]), int(q["page"][0])
    return json.dumps({"meta": {"count": len(hits)}, "results": hits[(page - 1) * per: page * per]}).encode()


class Exhaustive(unittest.TestCase):
    def run_plan(self, plan):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "plan.json"
            p.write_text(json.dumps(plan), encoding="utf-8")
            out = ls.cmd_run(p, Path(d) / "run", None, fetch, "2030-01-01")
            return out, ls.load(Path(d) / "run" / "queries.json")

    def test_default_reads_the_top_results_and_says_truncated(self):
        out, _ = self.run_plan(PLAN)
        self.assertEqual(out["candidates"], 100)
        self.assertEqual(len(out["truncated"]), 1)

    def test_exhaustive_splits_the_window_until_it_is_read_whole(self):
        out, queries = self.run_plan({**PLAN, "exhaustive": True})
        self.assertEqual(out["candidates"], 1500)
        self.assertEqual(out["truncated"], [])
        q = next(x for x in queries if x["source"] == "openalex")
        self.assertGreaterEqual(q["splits"], 1)
        self.assertEqual(q["fetched"], 1500)
        self.assertEqual(q["total"], 1500)


if __name__ == "__main__":
    unittest.main()
