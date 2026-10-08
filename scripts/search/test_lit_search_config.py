"""lit_search: a run says up front which free keys are missing for its sources
(invented ids and text only)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lit_search as ls  # noqa: E402

PLAN = {"description": "invented", "facets": [{"id": "A", "term": "toy code"}]}


class MissingKeys(unittest.TestCase):
    def test_keyless_openalex_and_semantic_scholar_are_named(self):
        plan = ls.load_plan_dict({**PLAN, "sources": ["arxiv", "s2", "openalex"]})
        with mock.patch.dict(os.environ, {}, clear=True):
            warn = ls.config_warnings(plan)
        self.assertEqual(sorted(w.split(":")[0] for w in warn), ["OPENALEX_API_KEY", "SEMANTIC_SCHOLAR_API_KEY"])

    def test_nothing_to_say_when_keys_are_set_or_sources_unused(self):
        plan = ls.load_plan_dict({**PLAN, "sources": ["arxiv", "s2", "openalex"]})
        with mock.patch.dict(os.environ, {"OPENALEX_API_KEY": "x", "SEMANTIC_SCHOLAR_API_KEY": "y"}, clear=True):
            self.assertEqual(ls.config_warnings(plan), [])
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(ls.config_warnings(ls.load_plan_dict({**PLAN, "sources": ["arxiv"]})), [])


TWO = {"description": "invented", "facets": [{"id": "A", "term": "toy code"}, {"id": "B", "term": "toy decoder"}]}


class Fields(unittest.TestCase):
    def test_fields_limit_openalex_and_semantic_scholar(self):
        plan = ls.load_plan_dict({**TWO, "fields": ["physics", "computer-science"]})
        oa, _ = ls.page_urls("openalex", '"toy code"', plan, 0, 100)
        self.assertIn("primary_topic.field.id:31|17", oa)
        for src in ("s2", "s2-anchor"):
            url, _ = ls.page_urls(src, "toy code", plan, 0, 100)
            self.assertIn("fieldsOfStudy=Physics%2CComputer%20Science", url)
        cr, _ = ls.page_urls("crossref", "toy code", plan, 0, 100)
        self.assertNotIn("primary_topic", cr)          # Crossref cannot be limited to a field
        self.assertNotIn("fieldsOfStudy", cr)

    def test_no_fields_no_filter_and_unknown_fields_refused(self):
        plan = ls.load_plan_dict(TWO)
        self.assertNotIn("primary_topic", ls.page_urls("openalex", "x", plan, 0, 100)[0])
        self.assertNotIn("fieldsOfStudy", ls.page_urls("s2", "x", plan, 0, 100)[0])
        with self.assertRaises(ls.Refused):
            ls.load_plan_dict({**TWO, "fields": ["astrology"]})

    def test_a_union_question_without_fields_is_warned(self):
        keys = {"OPENALEX_API_KEY": "x", "SEMANTIC_SCHOLAR_API_KEY": "y"}
        with mock.patch.dict(os.environ, keys, clear=True):
            warn = ls.config_warnings(ls.load_plan_dict({**TWO, "min_facets": 1, "sources": ["arxiv", "openalex"]}))
            self.assertTrue(any(w.startswith("min_facets 1 sin `fields`") for w in warn), warn)
            self.assertEqual(ls.config_warnings(ls.load_plan_dict(
                {**TWO, "min_facets": 1, "sources": ["arxiv", "openalex"], "fields": ["physics"]})), [])
            self.assertEqual(ls.config_warnings(ls.load_plan_dict({**TWO, "sources": ["arxiv", "openalex"]})), [])
            self.assertEqual(ls.config_warnings(ls.load_plan_dict({**TWO, "min_facets": 1, "sources": ["arxiv"]})),
                             [])


if __name__ == "__main__":
    unittest.main()
