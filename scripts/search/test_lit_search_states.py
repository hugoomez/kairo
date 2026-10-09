"""lit_search: what each query is recorded as — the query Semantic Scholar was
sent, the anchor pass's own state (invented terms only)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lit_search as ls  # noqa: E402

PLAN = ls.load_plan_dict({"description": "invented hyphen question",
                          "facets": [{"id": "A", "term": "toy-grid decoder", "synonyms": ["XQ-OSD"]},
                                     {"id": "B", "term": "invented lattice"}],
                          "sources": ["s2", "arxiv"], "anchors": 10})
COUNTS = {"identificados": {}, "identificados_total": 0, "tras_deduplicacion": 0, "tras_retraccion": 0,
          "retraccion": {"crossref": {"checked": 0, "removed": 0, "lost": 0},
                         "arxiv": {"checked": 0, "removed": 0, "lost": 0}},
          "incluidos": 0, "motivos": {}, "relevante_fuera_de_alcance": 0}


class S2Hyphens(unittest.TestCase):
    def test_s2_relevance_queries_are_sent_without_hyphens_and_recorded_as_sent(self):
        qs = ls.build_queries(PLAN, "2030-12-31")
        s2 = [q for q in qs if q["source"] == "s2" and q["pass"] == "relevance"]
        self.assertEqual([q["query"] for q in s2], ["toy grid decoder", "XQ OSD", "invented lattice"])
        self.assertEqual([q["terms"] for q in s2], [["toy-grid decoder"], ["XQ-OSD"], ["invented lattice"]])
        cross = next(q for q in qs if q["source"] == "s2" and q["pass"] == "cross")
        self.assertNotIn("-", cross["query"])
        url, _ = ls.page_urls("s2", s2[0]["query"], PLAN, 0, 100)
        self.assertIn("query=toy%20grid%20decoder", url)

    def test_the_anchor_and_arxiv_queries_keep_their_hyphens(self):
        qs = ls.build_queries(PLAN, "2030-12-31")
        anchor = next(q for q in qs if q["source"] == "s2-anchor" and q["facet"] == "A")
        self.assertIn('"toy-grid decoder"', anchor["query"])
        arx = next(q for q in qs if q["source"] == "arxiv" and q["facet"] == "A")
        self.assertIn('"toy-grid decoder"', arx["query"])


class AnchorState(unittest.TestCase):
    def test_the_anchor_pass_is_by_citations_not_complete(self):
        q = {"id": "Q009", "facet": "A", "source": "s2-anchor", "pass": "anchor", "query": '("toy")',
             "terms": ["toy"], "fetched": 10, "total": 41000, "error": None, "truncated": False, "ranked": False}
        md = ls.busqueda_md({**PLAN, "date": "2030-12-31"}, [q], COUNTS)
        row = next(x for x in md.splitlines() if x.startswith("| Q009"))
        self.assertIn("por citas (top 10)", row)
        self.assertNotIn("completa", row)


if __name__ == "__main__":
    unittest.main()
