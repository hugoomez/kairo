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
        if host == "api2.openreview.net":
            return json.dumps({"count": 0, "notes": []}).encode()
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

    def openreview(self, venue, title="Distributed Toy State-Vector Simulation", bib_year="2031",
                   authors=("Jane Doe",)):
        return {"count": 1, "notes": [{"id": "orX", "cdate": 1916006400000, "content": {
            "title": {"value": title}, "venue": {"value": venue}, "authors": {"value": list(authors)},
            "_bibtex": {"value": "@inproceedings{doe" + bib_year + ",\ntitle={" + title + "},\n"
                                 "booktitle={The Invented Conference on Learning Representations},\n"
                                 "year={" + bib_year + "}\n}"}}}]}

    def _with_openreview(self, page):
        work = {**OPENALEX, "doi": "https://doi.org/10.48550/arxiv.0000.44444",
                "primary_location": {"source": {"type": "repository", "display_name": "arXiv"}}}
        base = self._with(work, "404")

        def fetch(url, headers):
            if "api2.openreview.net" in url:
                return json.dumps(page).encode()
            if "api.semanticscholar.org" in url and "/citations" not in url:
                return json.dumps({"citationCount": 1}).encode()
            return base(url, headers)
        return fetch

    def test_an_ml_venue_with_no_doi_comes_from_openreview_with_its_year(self):
        code, out = self.run_cli("--arxiv", "0000.44444", "--json",
                                 fetch=self._with_openreview(self.openreview("ICLR 2031 Poster")))
        self.assertEqual(code, 0, out)
        card = json.loads(out)
        self.assertIn("OpenReview", [p["source"].split(" ")[0] for p in card["published"]])
        bib = card["bibtex"]
        self.assertTrue(bib.startswith("@inproceedings{doe2031distributed,"), bib)
        self.assertIn("booktitle = {The Invented Conference on Learning Representations}", bib)
        self.assertIn("year = {2031}", bib)
        self.assertIn("eprint = {0000.44444}", bib)

    def test_a_retitled_version_by_the_same_first_author_counts_and_shows_its_title(self):
        page = self.openreview("ICLR 2031 Poster", title="Distributed Toy (Almost) State-Vector Simulation")
        code, out = self.run_cli("--arxiv", "0000.44444", "--json", fetch=self._with_openreview(page))
        card = json.loads(out)
        src = next(p["source"] for p in card["published"] if p["source"].startswith("OpenReview"))
        self.assertIn("título publicado: «Distributed Toy (Almost) State-Vector Simulation»", src)
        self.assertTrue(card["bibtex"].startswith("@inproceedings{"), card["bibtex"])

    def test_a_rejected_or_differently_titled_openreview_record_is_not_a_publication(self):
        for page in (self.openreview("Submitted to ICLR 2031"),
                     self.openreview("ICLR 2031 Poster", title="Distributed Toy State-Vector Simulation Revisited",
                                     authors=("Rui Roe",)),
                     self.openreview("ICLR 2031 Poster", title="Toy Simulations of Something Else")):
            code, out = self.run_cli("--arxiv", "0000.44444", "--json", fetch=self._with_openreview(page))
            card = json.loads(out)
            self.assertNotIn("OpenReview", [p["source"].split(" ")[0] for p in card["published"]])
            self.assertTrue(card["bibtex"].startswith("@misc{"), card["bibtex"])

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

    def test_the_most_cited_citing_papers_on_request(self):
        """`--citing-order cited` asks OpenAlex to sort by citations and shows each count."""
        cites = [{"id": "https://openalex.org/W7", "title": "Influential citing toy paper", "publication_year": 2031,
                  "cited_by_count": 120, "authorships": []}]
        seen = []
        base = self._with(lists={"sort=cited_by_count:desc": cites})

        def fetch(url, headers):
            seen.append(urllib.parse.unquote(url))
            return base(url, headers)
        code, out = self.run_cli("--arxiv", "0000.44444", "--citing-order", "cited", fetch=fetch)
        self.assertEqual(code, 0, out)
        self.assertTrue(any("cites:W444" in u and "sort=cited_by_count:desc" in u for u in seen))
        self.assertIn("más citadas", out)
        self.assertIn("citado 120 veces", out)

    def test_the_semantic_scholar_fallback_sorts_by_citations_too(self):
        page = {"data": [
            {"citingPaper": {"title": "Rarely cited", "year": 2033, "publicationDate": "2033-01-01",
                             "citationCount": 1, "externalIds": {}, "authors": []}},
            {"citingPaper": {"title": "Often cited", "year": 2031, "publicationDate": "2031-01-01",
                             "citationCount": 50, "externalIds": {}, "authors": []}}]}
        base = self._with(lists={})

        def fetch(url, headers):
            if "/citations" in url:
                return json.dumps(page).encode()
            if "api.semanticscholar.org" in url:
                return json.dumps({"paperId": "S1", "citationCount": 2}).encode()
            return base(url, headers)
        code, out = self.run_cli("--arxiv", "0000.44444", "--json", "--citing-order", "cited", fetch=fetch)
        self.assertEqual(code, 0, out)
        card = json.loads(out)
        self.assertEqual([c["title"] for c in card["citing"]][:2], ["Often cited", "Rarely cited"])

    def test_citations_of_the_preprint_and_the_published_work_are_both_counted(self):
        """OpenAlex may keep the arXiv preprint and the published paper as two works:
        the citing papers of both are one list."""
        published = {**OPENALEX, "id": "https://openalex.org/W1", "cited_by_count": 10}
        preprint = {**OPENALEX, "id": "https://openalex.org/W2", "doi": "https://doi.org/10.48550/arxiv.0000.44444",
                    "cited_by_count": 40, "primary_location": {"source": {"type": "repository",
                                                                          "display_name": "arXiv"}}}
        cites = [{"id": "https://openalex.org/W8", "title": "A paper citing the preprint", "publication_year": 2033,
                  "publication_date": "2033-03-01", "authorships": []}]
        base = self._with(lists={"cites:W1|W2": cites})
        seen = []

        def fetch(url, headers):
            seen.append(urllib.parse.unquote(url))
            if "api.openalex.org/works/doi:10.0000/sc.444" in urllib.parse.unquote(url):
                return json.dumps(published).encode()
            if "api.openalex.org/works/doi:10.48550/arXiv.0000.44444" in urllib.parse.unquote(url):
                return json.dumps(preprint).encode()
            if url.startswith("https://export.arxiv.org"):
                return ATOM.replace("<arxiv:comment>", "<arxiv:doi>10.0000/sc.444</arxiv:doi><arxiv:comment>").encode()
            return base(url, headers)
        code, out = self.run_cli("--arxiv", "0000.44444", "--json", fetch=fetch)
        self.assertEqual(code, 0, out)
        card = json.loads(out)
        self.assertEqual(card["citations"]["openalex_works"], {"W1": 10, "W2": 40})
        self.assertTrue(any("cites:W1|W2" in u for u in seen))
        self.assertEqual(card["citing"][0]["title"], "A paper citing the preprint")
        self.assertEqual(card["citations"]["openalex"], 1)          # the union OpenAlex counted, not 10 + 40

    def test_a_failed_page_of_citing_papers_is_said_to_be_incomplete(self):
        def fetch(url, headers):
            if "/citations" in url:
                raise pc.net.HttpError(url, 429, "Too Many Requests")
            return web()(url, headers)
        code, out = self.run_cli("--arxiv", "0000.44444", "--json", fetch=fetch)
        card = json.loads(out)
        self.assertTrue(card["citing_incomplete"])
        code, md = self.run_cli("--arxiv", "0000.44444", fetch=fetch)
        self.assertIn("incompleta", md)

    def test_unknown_paper(self):
        def nothing(url, headers):
            raise pc.net.HttpError(url, 404, "not found")
        code, out = self.run_cli("--doi", "10.0000/none", fetch=nothing)
        self.assertEqual(code, 1)



class TitleFallback(unittest.TestCase):
    """A preprint from this week is not in OpenAlex yet: its title is looked up on arXiv."""

    ATOM = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/0000.55555v1</id>'
            "<title>Brand New Toy\n  Decoders</title><summary>x</summary></entry>"
            "<entry><id>http://arxiv.org/abs/0000.55556v1</id><title>Brand new toy decoders, revisited</title>"
            "<summary>x</summary></entry></feed>").encode()

    def fetch(self, url, headers):
        if "api.openalex.org" in url:
            return json.dumps({"results": []}).encode()
        if "export.arxiv.org" in url:
            self.assertIn('ti:"brand new toy decoders"', urllib.parse.unquote(url).lower())
            return self.ATOM
        raise AssertionError(url)

    def test_an_exact_arxiv_title_is_the_paper(self):
        got = pc.find_by_title("Brand new toy decoders", self.fetch)
        self.assertEqual(got["chosen"]["arxiv"], "0000.55555")
        self.assertEqual(got["chosen"]["found_by"], "arXiv (OpenAlex no lo tiene aún)")

    def test_no_exact_title_anywhere_is_not_found(self):
        def fetch(url, headers):
            if "api.openalex.org" in url:
                return json.dumps({"results": []}).encode()
            return b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
        self.assertIn("error", pc.find_by_title("Nothing like it", fetch))


if __name__ == "__main__":
    unittest.main()
