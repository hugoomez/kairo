"""Live contract tests: do the sources still answer in the shape Kairo's parsers
read? Every other test serves invented records from fakes, so a change in an
API (a renamed field, a new page layout, an anti-bot wall) would leave CI green
while searches quietly lose papers. These ask the real public APIs.

Off by default (network, rate limits): run with

    KAIRO_LIVE_TESTS=1 python -m pytest scripts/quality/test_live_contracts.py -v

No identifier is written here: each run discovers a recent paper from the
source itself and checks the parsers on it. Semantic Scholar's keyless 429 is a
skip (its pool is shared), never a pass. Free endpoints only.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
for sub in ("search", "citations", "papers"):
    sys.path.insert(0, str(HERE.parent / sub))
import lit_search as ls  # noqa: E402
import net  # noqa: E402

LIVE = os.environ.get("KAIRO_LIVE_TESTS") == "1"
PLAN = ls.load_plan_dict({"description": "live contract", "facets": [{"id": "A", "term": "quantum error correction"}],
                          "from": None})


def fetch(url: str, headers: dict) -> bytes:
    return net.get(url, headers=headers)


def page(source: str, query: str, size: int = 5) -> tuple[list[dict], int]:
    url, headers = ls.page_urls(source, query, PLAN, 0, size)
    try:
        data = fetch(url, headers)
    except net.HttpError as e:
        if source == "s2" and e.code == 429:
            raise unittest.SkipTest("Semantic Scholar keyless pool answered 429") from None
        raise
    return ls.PARSERS[source](data)


@unittest.skipUnless(LIVE, "set KAIRO_LIVE_TESTS=1 to ask the real APIs")
class SearchSources(unittest.TestCase):
    def check(self, recs, total, need=("title",)):
        self.assertTrue(recs, "no records parsed")
        self.assertGreaterEqual(total, len(recs))
        for f in need:
            self.assertTrue(all(r.get(f) for r in recs), f"a record without {f}: {recs[:2]}")

    def test_arxiv(self):
        recs, total = page("arxiv", ls.arxiv_query(PLAN["facets"][0], PLAN, "2100-01-01"))
        self.check(recs, total, ("title", "arxiv", "date"))

    def test_openalex(self):
        recs, total = page("openalex", '"quantum error correction"')
        self.check(recs, total, ("title", "year"))

    def test_crossref(self):
        recs, total = page("crossref", "quantum error correction")
        self.check(recs, total, ("title", "doi"))

    def test_semantic_scholar(self):
        recs, total = page("s2", "quantum error correction")
        self.check(recs, total, ("title",))

    def test_openreview(self):
        recs, total = page("openreview", "transformer")
        self.check(recs, total, ("title",))


@unittest.skipUnless(LIVE, "set KAIRO_LIVE_TESTS=1 to ask the real APIs")
class PaperCard(unittest.TestCase):
    def test_a_recent_arxiv_paper_gets_versions_identity_and_bibtex(self):
        import paper_card
        recs, _ = page("arxiv", "cat:quant-ph", size=1)
        aid = recs[0]["arxiv"]
        card = paper_card.build(aid, None, 3, fetch, None)
        self.assertTrue(card["identity"].get("title"), card["errors"])
        # the abstract page's submission history is scraped: its layout is the contract
        self.assertTrue(card["versions"] and card["versions"][0]["version"] == "v1", card["versions"])
        self.assertTrue(card["versions"][0]["date"], card["versions"])
        self.assertIn(aid, card["bibtex"])


if __name__ == "__main__":
    unittest.main()
