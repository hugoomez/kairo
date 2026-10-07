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


if __name__ == "__main__":
    unittest.main()
