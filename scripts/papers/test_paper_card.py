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
                        "issued": {"date-parts": [[2030, 11]]}, "page": "1-12",
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
        self.assertIn("pages = {1--12}", card["bibtex"])           # from the publisher's record

    def test_markdown_names_a_failed_source(self):
        code, out = self.run_cli("--arxiv", "0000.44444", fetch=web(fail=("api.crossref.org",)))
        self.assertEqual(code, 0)
        self.assertIn("## Versiones de arXiv", out)
        self.assertIn("- v3 — 2030-09-02", out)
        self.assertIn("## ⚠️ Fuentes que fallaron", out)
        self.assertIn("crossref.json", out)

    def _with(self, openalex_work=None, crossref=None, lists=None, fail=()):
        """web() with the OpenAlex work, the Crossref record and OpenAlex list answers replaced."""
        base = web(fail)

        def fetch(url, headers):
            host = urllib.parse.urlsplit(url).netloc
            if host == "api.openalex.org" and "/works?" in url and lists is not None:
                for marker, results in lists.items():
                    if marker in urllib.parse.unquote(url):
                        return json.dumps({"meta": {"count": len(results)}, "results": results}).encode()
                return json.dumps({"meta": {"count": 0}, "results": []}).encode()
            if host == "api.openalex.org" and openalex_work is not None:
                return json.dumps(openalex_work).encode()
            if host == "api.crossref.org" and crossref is not None:
                if crossref == "404":
                    raise pc.net.HttpError(url, 404, "not found")
                return json.dumps(crossref).encode()
            return base(url, headers)
        return fetch

    def test_a_venue_known_only_to_openalex_takes_doi_and_year_from_the_publisher(self):
        work = {**OPENALEX, "doi": "https://doi.org/10.48550/arxiv.0000.44444", "publication_year": 2030,
                "primary_location": {"landing_page_url": "https://doi.org/10.0000/conf.9",
                                     "source": {"type": "conference", "display_name": "Invented Systems Conf"}}}
        cr = {"message": {"DOI": "10.0000/conf.9", "type": "proceedings-article", "title": ["x"],
                          "container-title": ["Proceedings of the Invented Systems Conf"],
                          "issued": {"date-parts": [[2031, 4, 2]]}}}
        code, out = self.run_cli("--arxiv", "0000.44444", "--json", fetch=self._with(work, cr))
        self.assertEqual(code, 0, out)
        bib = json.loads(out)["bibtex"]
        self.assertIn("year = {2031}", bib)
        self.assertIn("doi = {10.0000/conf.9}", bib)
        self.assertIn("booktitle = {Proceedings of the Invented Systems Conf}", bib)

    def test_a_venue_without_a_known_year_is_never_paired_with_the_preprint_year(self):
        work = {**OPENALEX, "doi": "https://doi.org/10.48550/arxiv.0000.44444",
                "primary_location": {"source": {"type": "conference", "display_name": "Invented Systems Conf"}}}
        code, out = self.run_cli("--arxiv", "0000.44444", "--json", fetch=self._with(work, "404"))
        self.assertEqual(code, 0, out)
        bib = json.loads(out)["bibtex"]
        self.assertTrue(bib.startswith("@misc{"), bib)              # cited as the preprint it is
        self.assertNotIn("booktitle", bib)
        self.assertIn("year = {2030}", bib)
        self.assertIn("Invented Systems Conf", bib)                 # the venue, in a note
        self.assertIn("año de la versión publicada no consta", bib)

    def test_a_paper_is_found_by_its_title(self):
        hit = {**OPENALEX, "title": "Distributed Toy State-Vector Simulation"}
        other = {**OPENALEX, "id": "https://openalex.org/W9", "title": "Something else entirely"}
        code, out = self.run_cli("--title", "distributed toy state vector simulation", "--json",
                                 fetch=self._with(lists={"search=": [other, hit]}))
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(out)["identity"]["doi"], "10.0000/sc.444")

    def test_an_ambiguous_title_lists_the_candidates_and_stops(self):
        a = {**OPENALEX, "title": "Toy simulation on invented clusters"}
        b = {**OPENALEX, "id": "https://openalex.org/W9", "title": "Toy simulation on invented clusters, revisited"}
        code, out = self.run_cli("--title", "toy simulation", fetch=self._with(lists={"search=": [a, b]}))
        self.assertEqual(code, 3)
        res = json.loads(out)
        self.assertEqual(len(res["candidates"]), 2)

    def test_the_newest_citing_papers_come_sorted_from_openalex(self):
        cites = [{"id": "https://openalex.org/W7", "title": "Newest citing toy paper", "publication_year": 2033,
                  "publication_date": "2033-02-01", "doi": "https://doi.org/10.0000/c7",
                  "authorships": [{"author": {"display_name": "Li Wu"}}],
                  "primary_location": {"source": {"display_name": "Invented Journal"}}}]
        code, out = self.run_cli("--arxiv", "0000.44444", "--json", fetch=self._with(lists={"cites:W444": cites}))
        self.assertEqual(code, 0, out)
        card = json.loads(out)
        self.assertEqual(card["citing_source"], "OpenAlex")
        self.assertEqual(card["citing"][0]["title"], "Newest citing toy paper")

    def test_unknown_paper(self):
        def nothing(url, headers):
            raise pc.net.HttpError(url, 404, "not found")
        code, out = self.run_cli("--doi", "10.0000/none", fetch=nothing)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
