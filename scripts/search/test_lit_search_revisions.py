"""lit_search: with `arxiv_revisions`, an arXiv paper first posted before the
window but revised inside it is found too (invented ids and text only)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lit_search as ls  # noqa: E402

FEED = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"
 xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"><opensearch:totalResults>1</opensearch:totalResults>
<entry><id>http://arxiv.org/abs/2999.00031v3</id><published>2028-03-01T00:00:00Z</published>
<updated>2030-04-02T00:00:00Z</updated><title>An invented toy code revised</title>
<summary>A toy code.</summary><author><name>Ada Inventa</name></author></entry></feed>""".encode()
PLAN = {"description": "invented", "facets": [{"id": "A", "term": "toy code"}], "sources": ["arxiv"],
        "from": "2030-01-01", "to": "2030-12-31", "enrich": False}


class Revisions(unittest.TestCase):
    def run_plan(self, plan):
        urls = []

        def fetch(url, headers):
            urls.append(urllib.parse.unquote(url))
            return FEED
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "plan.json"
            p.write_text(json.dumps(plan), encoding="utf-8")
            out = ls.cmd_run(p, Path(d) / "run", None, fetch, "2031-01-01")
        return out, urls

    def test_default_keeps_the_first_version_date(self):
        out, urls = self.run_plan(PLAN)
        self.assertEqual(out["candidates"], 0)
        self.assertNotIn("lastUpdatedDate", urls[0])

    def test_revisions_in_the_window_count(self):
        out, urls = self.run_plan({**PLAN, "arxiv_revisions": True})
        self.assertIn("lastUpdatedDate:[203001010000 TO 203012312359]", urls[0])
        self.assertEqual(out["candidates"], 1)


if __name__ == "__main__":
    unittest.main()
