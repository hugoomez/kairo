"""lit_search: a keyword-relevance source (Crossref, Semantic Scholar,
OpenReview) returns records that match any word of the query, so a record it
returns is credited with the query's facet only when the facet's term is in its
own title or abstract — never for having been returned (invented records only)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lit_search as ls  # noqa: E402

PLAN = {"description": "invented", "facets": [{"id": "A", "term": "toy decoder"}, {"id": "B", "term": "toy code"}],
        "sources": ["crossref"], "from": "2030-01-01", "cross": False, "enrich": False}


def crossref(items):
    return json.dumps({"message": {"total-results": len(items), "items": [{
        "DOI": doi, "title": [title], "abstract": abstract, "author": [{"given": "A", "family": "B"}],
        "issued": {"date-parts": [[2030, 5, 1]]}, "container-title": ["Invented"], "type": "journal-article"}
        for doi, title, abstract in items]}}).encode()


def fetch(url, headers):
    if "toy%20decoder" in url or "toy+decoder" in url:
        return crossref([("10.9999/rail.1", "An invented railway signalling model", "Loose match on a word."),
                         ("10.9999/dec.1", "A toy decoder for a toy code", "We decode a toy code.")])
    return crossref([("10.9999/dec.1", "A toy decoder for a toy code", "We decode a toy code.")])


class RankedCredit(unittest.TestCase):
    def test_a_loose_match_gets_no_facet_and_is_prefiltered(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "plan.json"
            p.write_text(json.dumps(PLAN), encoding="utf-8")
            ls.cmd_run(p, Path(d) / "run", None, fetch, "2031-01-01")
            cands = {c["doi"]: c for c in ls.load(Path(d) / "run" / "candidates.json")}
        self.assertEqual(cands["10.9999/rail.1"]["facets"], {})
        self.assertFalse(ls.passes_prefilter(cands["10.9999/rail.1"]))
        self.assertEqual(sorted(cands["10.9999/dec.1"]["facets"]), ["A", "B"])
        self.assertTrue(ls.passes_prefilter(cands["10.9999/dec.1"]))


if __name__ == "__main__":
    unittest.main()
