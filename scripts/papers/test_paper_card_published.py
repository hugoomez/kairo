"""paper_card: what counts as a published version (invented ids and text only)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "citations"))
import net  # noqa: E402
import paper_card as pc  # noqa: E402

AID = "2999.00001"
TITLE = "An invented decoder for invented codes"

FEED = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/{AID}v1</id>
    <published>2030-03-01T00:00:00Z</published>
    <updated>2030-03-01T00:00:00Z</updated>
    <title>{TITLE}</title>
    <summary>We invent a decoder.</summary>
    <author><name>Ada Inventa</name></author>
  </entry>
</feed>""".encode()

ABS = b"<b>[v1]</b> Fri, 1 Mar 2030 10:00:00 UTC (100 KB)"


def world(s2_venue: dict | None, crossref_items: list[dict] | None = None):
    """A fake of every source paper_card asks; `calls` records the URLs."""
    calls: list[str] = []

    def fetch(url: str, headers: dict) -> bytes:
        calls.append(url)
        if url.startswith("https://export.arxiv.org/api/query"):
            return FEED
        if url.startswith("https://arxiv.org/abs/"):
            return ABS
        if url.startswith("https://api.semanticscholar.org/graph/v1/paper/") and "/citations" not in url:
            return json.dumps({"title": TITLE, "citationCount": 3, "influentialCitationCount": 0,
                               "year": 2030, "venue": (s2_venue or {}).get("name", ""),
                               "publicationVenue": s2_venue}).encode()
        if url.startswith("https://api.crossref.org/works?"):
            return json.dumps({"message": {"items": crossref_items or [], "total-results": len(crossref_items or [])}}
                              ).encode()
        if url.startswith("https://api2.openreview.net/"):
            return json.dumps({"notes": [], "count": 0}).encode()
        raise net.HttpError(url, 404, "Not Found")
    return fetch, calls


class RepositoryIsNotAVenue(unittest.TestCase):
    def test_semantic_scholar_arxiv_venue_is_not_a_published_version(self):
        fetch, _ = world({"name": "arXiv.org", "type": "journal"})
        card = pc.build(AID, None, 0, fetch, None)
        self.assertEqual([p for p in card["published"] if p["source"] == "Semantic Scholar"], [])
        self.assertTrue(card["bibtex"].startswith("@misc"), card["bibtex"])

    def test_semantic_scholar_real_venue_still_counts(self):
        fetch, _ = world({"name": "Invented Conference on Codes", "type": "conference"})
        card = pc.build(AID, None, 0, fetch, None)
        self.assertIn("Invented Conference on Codes",
                      [p["venue"] for p in card["published"] if p["source"] == "Semantic Scholar"])


class RetitledPublishedVersion(unittest.TestCase):
    """No source links the preprint to its published version: a Crossref
    bibliographic search by title + first author may find it. It is shown as
    a candidate, never as the published version."""

    ITEM = {"DOI": "10.9999/invented.2031.7", "title": ["Invented decoders for invented codes"],
            "author": [{"given": "Ada", "family": "Inventa"}], "container-title": ["Invented Journal"],
            "issued": {"date-parts": [[2031, 2, 1]]}, "type": "journal-article"}

    def test_close_title_and_first_author_is_a_candidate(self):
        fetch, calls = world(None, [self.ITEM])
        card = pc.build(AID, None, 0, fetch, None)
        self.assertTrue(any(u.startswith("https://api.crossref.org/works?") for u in calls))
        cands = card.get("published_candidates") or []
        self.assertEqual([c["doi"] for c in cands], ["10.9999/invented.2031.7"])
        self.assertIn("sin enlace declarado", cands[0]["why"])
        # a candidate is never the published version, nor in the BibTeX
        self.assertNotIn("10.9999/invented.2031.7", [p.get("doi") for p in card["published"]])
        self.assertNotIn("10.9999/invented.2031.7", card["bibtex"])
        self.assertIn("10.9999/invented.2031.7", pc.markdown(card))

    def test_other_first_author_is_not_a_candidate(self):
        item = {**self.ITEM, "author": [{"given": "Otto", "family": "Fremd"}]}
        fetch, _ = world(None, [item])
        card = pc.build(AID, None, 0, fetch, None)
        self.assertEqual(card.get("published_candidates") or [], [])

    def test_no_search_when_a_source_already_names_the_published_version(self):
        fetch, calls = world({"name": "Invented Conference on Codes", "type": "conference"}, [self.ITEM])
        pc.build(AID, None, 0, fetch, None)
        self.assertFalse(any(u.startswith("https://api.crossref.org/works?") for u in calls))


if __name__ == "__main__":
    unittest.main()
