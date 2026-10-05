"""Tests for paper_card.py (invented records, no network)."""

import contextlib
import io
import json
import sys
import unittest
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paper_card as pc  # noqa: E402

ATOM = """<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom"><entry>
<id>http://arxiv.org/abs/0000.44444v3</id><published>2030-01-02T00:00:00Z</published>
<title>Distributed Toy State-Vector Simulation</title><summary>An invented abstract.</summary>
<author><name>Jane Doe</name></author><arxiv:comment>9 pages</arxiv:comment></entry></feed>"""

ABS = """<div class="submission-history"><strong>[v1]</strong> Wed, 2 Jan 2030 10:00:00 UTC (100 KB)<br/>
<strong><a href="/abs/0000.44444v2">[v2]</a></strong> Fri, 1 Mar 2030 10:00:00 UTC (110 KB)<br/>
<strong>[v3]</strong> Mon, 2 Sep 2030 10:00:00 UTC (120 KB)</div>"""

OPENALEX = {"id": "https://openalex.org/W444", "doi": "https://doi.org/10.0000/sc.444", "cited_by_count": 31,
            "title": "Distributed Toy State-Vector Simulation", "publication_year": 2030,
            "primary_location": {"source": {"type": "conference", "display_name": "Invented SC 2030"}},
            "locations": [], "authorships": [{"author": {"display_name": "Jane Doe"}}]}

CROSSREF = {"message": {"DOI": "10.0000/sc.444", "type": "proceedings-article", "title": ["Distributed Toy..."],
                        "container-title": ["Proceedings of the Invented SC Conference"],
                        "issued": {"date-parts": [[2030, 11]]},
                        "relation": {"has-preprint": [{"id": "10.48550/arXiv.0000.44444", "id-type": "doi"}]}}}


def web(fail=()):
    def fetch(url, headers):
        host = urllib.parse.urlsplit(url).netloc
        if host in fail:
            raise pc.net.HttpError(url, 503, "down")
        if host == "export.arxiv.org":
            return ATOM.encode()
        if host == "arxiv.org":
            return ABS.encode()
        if host == "api.openalex.org":
            return json.dumps(OPENALEX).encode()
        if host == "api.crossref.org":
            return json.dumps(CROSSREF).encode()
        if "/citations" in url:
            return json.dumps({"data": [
                {"citingPaper": {"title": "Older citing toy paper", "year": 2031, "publicationDate": "2031-01-01",
                                 "externalIds": {"ArXiv": "0000.55555"}, "authors": [{"name": "Rui Roe"}]}},
                {"citingPaper": {"title": "Newer citing toy paper", "year": 2032, "publicationDate": "2032-05-01",
                                 "externalIds": {"DOI": "10.0000/new"}, "venue": "Invented Journal",
                                 "authors": [{"name": "Ana Poe"}]}}]}).encode()
        if host == "api.semanticscholar.org":
            return json.dumps({"citationCount": 29, "influentialCitationCount": 4,
                               "publicationVenue": {"name": "SC", "type": "conference"}}).encode()
        raise AssertionError(url)
    return fetch


class PaperCard(unittest.TestCase):
    def run_cli(self, *args, fetch=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = pc.main(list(args), fetch=fetch or web())
        return code, buf.getvalue()

    def test_versions_published_version_citations_and_bibtex(self):
        code, out = self.run_cli("--arxiv", "0000.44444", "--json")
        self.assertEqual(code, 0, out)
        card = json.loads(out)
        self.assertEqual([v["version"] for v in card["versions"]], ["v1", "v2", "v3"])
        self.assertEqual(card["versions"][1]["date"], "2030-03-01")
        sources = [p["source"] for p in card["published"]]
        self.assertTrue(any(s.startswith("OpenAlex") for s in sources))
        self.assertTrue(any(s.startswith("Crossref (proceedings-article)") for s in sources))
        self.assertTrue(any("has-preprint" in s for s in sources))
        self.assertEqual((card["citations"]["openalex"], card["citations"]["semantic_scholar"]), (31, 29))
        self.assertEqual(card["citing"][0]["title"], "Newer citing toy paper")      # newest first
        self.assertEqual(card["retraction"]["status"], "clear")
        self.assertIn("@inproceedings{doe2030distributed,", card["bibtex"])
        # the publisher's record (Crossref) names the venue, before OpenAlex or arXiv
        self.assertIn("booktitle = {Proceedings of the Invented SC Conference}", card["bibtex"])
        self.assertIn("eprint = {0000.44444}", card["bibtex"])

    def test_markdown_names_a_failed_source(self):
        code, out = self.run_cli("--arxiv", "0000.44444", fetch=web(fail=("api.crossref.org",)))
        self.assertEqual(code, 0)
        self.assertIn("## Versiones de arXiv", out)
        self.assertIn("- v3 — 2030-09-02", out)
        self.assertIn("## ⚠️ Fuentes que fallaron", out)
        self.assertIn("crossref.json", out)

    def test_unknown_paper(self):
        def nothing(url, headers):
            raise pc.net.HttpError(url, 404, "not found")
        code, out = self.run_cli("--doi", "10.0000/none", fetch=nothing)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
