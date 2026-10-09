"""lit_search: what a candidate's line in ranked.md says — its OpenReview
decision, an expression of concern, never «publicado en» a preprint server
(invented records only)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lit_search as ls  # noqa: E402


def rec(**kw) -> dict:
    base = {"title": "An invented toy decoder for invented toy codes", "abstract": "toy", "authors": ["Ada Roe"],
            "date": "2031-01-01", "year": 2031, "arxiv": None, "doi": None, "venue": None, "citations": None,
            "url": None, "query": "Q001", "facet": "A", "matched": "toy", "source": "arxiv", "anchor": False,
            "rank": 1}
    return {**base, **kw}


def cand(**kw) -> dict:
    c = {"key": "arxiv:3001.00001", "title": "An invented toy decoder", "arxiv": "3001.00001", "doi": None,
         "venues": [], "years": [2031], "year": 2031, "date": "2031-01-01", "facets": {"A": "toy"},
         "citations": None, "screen": {"decision": "include", "relevance": "alta", "why": "An invented reason."}}
    return {**c, **kw}


class OpenReviewStatus(unittest.TestCase):
    def test_dedup_keeps_the_openreview_decision(self):
        a = rec(arxiv="3001.00001")
        b = rec(arxiv="3001.00001", source="openreview", openreview_venue="Submitted to ICLR 2031")
        [c] = ls.dedup([a, b])
        self.assertEqual(c["openreview_venue"], "Submitted to ICLR 2031")
        self.assertEqual(c["venues"], [])

    def test_ranked_line_shows_a_rejected_submission(self):
        line = ls._line(cand(openreview_venue="ICLR 2031 Conference Rejected Submission"), "2031-06-01")
        self.assertIn("OpenReview: ICLR 2031 Conference Rejected Submission", line)

    def test_an_accepted_or_imported_record_shows_no_status(self):
        self.assertNotIn("OpenReview:", ls._line(cand(openreview_venue="CoRR 2030"), "2031-06-01"))


class PreprintVenues(unittest.TestCase):
    def test_a_preprint_server_is_never_publicado_en(self):
        for v in ("arXiv.org", "ArXiv", "bioRxiv", "SSRN Electronic Journal", "Research Square", "TechRxiv"):
            line = ls._line(cand(venues=[v]), "2031-06-01")
            self.assertNotIn("publicado en", line, v)
            self.assertIn(f"preprint ({v})", line)

    def test_a_real_venue_still_is(self):
        line = ls._line(cand(venues=["Invented Journal", "arXiv.org"]), "2031-06-01")
        self.assertIn("publicado en Invented Journal", line)
        self.assertNotIn("publicado en Invented Journal, arXiv.org", line)


class Concern(unittest.TestCase):
    PLAN = {"scope_out": []}

    def test_the_line_shows_an_expression_of_concern(self):
        c = cand(retraction={"status": "concern", "evidence": ["invented notice 10.0000/eoc.1"], "lost": []})
        line = ls._line(c, "2031-06-01")
        self.assertIn("expresión de preocupación", line)
        self.assertIn("invented notice 10.0000/eoc.1", line)

    def test_an_include_must_mention_the_concern(self):
        c = cand(retraction={"status": "concern", "evidence": ["invented notice"], "lost": []})
        bad = {c["key"]: {"decision": "include", "relevance": "alta",
                          "why": "Reports an invented toy decoder result on toy codes."}}
        errs = ls.validate([c], bad, self.PLAN)
        self.assertTrue(any("concern" in e for e in errs), errs)
        good = {c["key"]: {"decision": "include", "relevance": "alta",
                           "why": "Reports an invented toy decoder result; carries an expresión de preocupación."}}
        self.assertEqual(ls.validate([c], good, self.PLAN), [])


if __name__ == "__main__":
    unittest.main()
