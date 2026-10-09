"""lit_search: a required facet (`"required": true`) — a candidate must reach it,
whatever `min_facets` says; anchors and title-only records too (invented text)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lit_search as ls  # noqa: E402

PLAN = ls.load_plan_dict({"description": "invented toy codes and their decoders",
                          "facets": [{"id": "A", "term": "toy code", "required": True},
                                     {"id": "B", "term": "decoder"}],
                          "min_facets": 1, "sources": ["arxiv"]})


def cand(title: str, abstract: str = "Some invented text.", facets=None, anchor=False) -> dict:
    return {"title": title, "abstract": abstract, "facets": facets or {}, "anchor": anchor}


def passes(c: dict) -> bool:
    ls.mark_prefilter([c], PLAN)
    return ls.passes_prefilter(c)


class Required(unittest.TestCase):
    def test_the_plan_validates_required(self):
        with self.assertRaises(ls.Refused):
            ls.load_plan_dict({**PLAN, "facets": [{"id": "A", "term": "toy", "required": "yes"}]})
        self.assertTrue(PLAN["facets"][0]["required"])
        self.assertFalse(PLAN["facets"][1]["required"])

    def test_a_candidate_must_reach_every_required_facet(self):
        self.assertTrue(passes(cand("An invented toy code")))
        self.assertTrue(passes(cand("An invented toy code decoder")))
        only_b = cand("A decoder for invented speech")
        self.assertFalse(passes(only_b))
        self.assertEqual(only_b["prefilter"]["missing_required"], ["A"])

    def test_a_query_credit_counts_as_reaching_it(self):
        self.assertTrue(passes(cand("An invented paper", facets={"A": "toy code"})))

    def test_an_anchor_is_exempt_from_min_facets_but_not_from_required(self):
        two = ls.load_plan_dict({**PLAN, "min_facets": 2})
        a = cand("An invented toy code", anchor=True)
        ls.mark_prefilter([a], two)
        self.assertTrue(ls.passes_prefilter(a))
        b = cand("A decoder for invented speech", anchor=True)
        ls.mark_prefilter([b], two)
        self.assertFalse(ls.passes_prefilter(b))

    def test_a_record_with_no_abstract_must_show_a_required_facet_in_its_title(self):
        two = ls.load_plan_dict({**PLAN, "min_facets": 2})
        a, b = cand("An invented toy code", abstract=""), cand("A decoder for invented speech", abstract="")
        ls.mark_prefilter([a, b], two)
        self.assertEqual([ls.passes_prefilter(a), ls.passes_prefilter(b)], [True, False])

    def test_the_snowball_keeps_a_neighbour_only_with_the_required_facet(self):
        self.assertEqual(ls.snowball_keep({"title": "A decoder for invented speech", "abstract": "decoder"}, PLAN), {})
        self.assertEqual(set(ls.snowball_keep({"title": "A toy code decoder", "abstract": "x"}, PLAN)), {"A", "B"})

    def test_no_union_warning_when_a_facet_is_required(self):
        self.assertFalse(any("min_facets 1" in w for w in ls.config_warnings({**PLAN, "sources": ["openalex"]})))


if __name__ == "__main__":
    unittest.main()
