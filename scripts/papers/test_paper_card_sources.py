"""paper_card: sources that fail, mislead or say too little (invented records only)."""

from __future__ import annotations

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
import test_paper_card as t  # noqa: E402

MAINTENANCE = b"<html><body>Down for maintenance</body></html>"


def fetch_with(rules: dict, base=None):
    """t.web() with some answers replaced: {substring of the URL: bytes | Exception | callable}."""
    base = base or t.web()

    def fetch(url, headers):
        u = urllib.parse.unquote(url)
        for marker, ans in rules.items():
            if marker in u:
                if isinstance(ans, Exception):
                    raise ans
                return ans(u) if callable(ans) else ans
        return base(url, headers)
    return fetch


def run(*args, fetch):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = pc.main(list(args), fetch=fetch)
    return code, buf.getvalue()


def down(url="x"):
    return pc.net.HttpError(url, 503, "down")


class DoiWithoutOpenAlex(unittest.TestCase):
    def test_authors_and_year_come_from_crossref(self):
        cr = {"message": {**t.CROSSREF["message"], "relation": {},
                          "author": [{"given": "Jane", "family": "Doe"}, {"name": "TOY Collaboration"}]}}
        code, out = run("--doi", "10.0000/sc.444", "--json",
                        fetch=fetch_with({"api.openalex.org": down(), "api.crossref.org": json.dumps(cr).encode()}))
        self.assertEqual(code, 0, out)
        card = json.loads(out)
        self.assertEqual(card["identity"]["authors"], ["Doe, Jane", "TOY Collaboration"])
        self.assertEqual(card["identity"]["year"], "2030")
        self.assertIn("@inproceedings{doe2030distributed,", card["bibtex"])
        self.assertIn("year = {2030}", card["bibtex"])

    def test_a_bibtex_without_author_or_year_says_so(self):
        cr = {"message": {"DOI": "10.0000/bare.1", "title": ["A bare invented record"], "type": "journal-article",
                          "container-title": ["Invented Journal"]}}
        code, out = run("--doi", "10.0000/bare.1",
                        fetch=fetch_with({"api.openalex.org": down(), "api.crossref.org": json.dumps(cr).encode()}))
        self.assertEqual(code, 0, out)
        self.assertIn("BibTeX sin autor ni año", out)


class Unparseable(unittest.TestCase):
    """A source answering 200 with an HTML maintenance page is a failed source, not a crash."""

    def test_every_source_with_a_maintenance_page(self):
        for host in ("api.openalex.org", "api.crossref.org", "api.semanticscholar.org", "export.arxiv.org",
                     "api2.openreview.net"):
            code, out = run("--arxiv", "0000.44444", "--json", fetch=fetch_with({host: MAINTENANCE}))
            if host == "export.arxiv.org":
                self.assertIn(code, (0, 1), out)
            else:
                self.assertEqual(code, 0, (host, out))
                card = json.loads(out)
                self.assertTrue(any("no se pudo leer" in e for e in card["errors"]), (host, card["errors"]))

    def test_title_search_with_a_maintenance_page(self):
        code, out = run("--title", "Distributed toy state vector simulation",
                        fetch=fetch_with({"api.openalex.org/works?": MAINTENANCE, "export.arxiv.org": MAINTENANCE}))
        self.assertIn(code, (1, 3))
        self.assertIn("error", json.loads(out))


class TitleLookup(unittest.TestCase):
    def test_a_preprint_and_its_published_work_are_one_paper(self):
        pre = {**t.OPENALEX, "id": "https://openalex.org/W2", "doi": "https://doi.org/10.48550/arxiv.0000.44444",
               "primary_location": {"source": {"type": "repository", "display_name": "arXiv"}}}
        pub = {**t.OPENALEX, "locations": [{"landing_page_url": "https://arxiv.org/abs/0000.44444v2"}]}
        got = pc.find_by_title("Distributed Toy State-Vector Simulation",
                               fetch_with({"api.openalex.org/works?": json.dumps({"results": [pre, pub]}).encode()}))
        self.assertEqual((got["chosen"]["doi"], got["chosen"]["arxiv"]), ("10.0000/sc.444", "0000.44444"))

    def test_math_in_a_title_is_compared_not_deleted(self):
        a = {**t.OPENALEX, "title": "Toy codes over $\\mathbb{Z}_4$"}
        b = {**t.OPENALEX, "id": "https://openalex.org/W9", "doi": "https://doi.org/10.0000/other.9",
             "title": "Toy codes over $\\mathbb{F}_2$"}
        got = pc.find_by_title("Toy codes over $\\mathbb{F}_2$",
                               fetch_with({"api.openalex.org/works?": json.dumps({"results": [a, b]}).encode()}))
        self.assertEqual(got["chosen"]["doi"], "10.0000/other.9")

    def test_the_arxiv_fallback_needs_the_exact_title_and_says_how_it_was_found(self):
        feed = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/0000.55555v1</id>'
                "<title>Toy codes over $\\mathbb{Z}_4$</title><summary>x</summary></entry></feed>").encode()
        f = fetch_with({"api.openalex.org/works?": json.dumps({"results": []}).encode(), "export.arxiv.org": feed})
        self.assertIn("error", pc.find_by_title("Toy codes over $\\mathbb{F}_2$", f))
        code, out = run("--title", "Toy codes over $\\mathbb{Z}_4$", fetch=fetch_with(
            {"api.openalex.org/works?": json.dumps({"results": []}).encode(),
             "search_query": feed}))
        self.assertEqual(code, 0, out)
        self.assertIn("arXiv (OpenAlex no lo tiene aún)", out)


class Repositories(unittest.TestCase):
    def test_a_repository_is_never_the_published_version(self):
        work = {**t.OPENALEX, "doi": "https://doi.org/10.48550/arxiv.0000.44444",
                "primary_location": {"source": {"type": "journal", "display_name": "SSRN Electronic Journal"},
                                     "landing_page_url": "https://doi.org/10.2139/ssrn.999"}}
        cr = {"message": {"DOI": "10.2139/ssrn.999", "type": "journal-article", "title": ["x"],
                          "container-title": ["SSRN Electronic Journal"], "issued": {"date-parts": [[2030]]}}}
        f = fetch_with({"api.openalex.org/works/": json.dumps(work).encode(),
                        "api.crossref.org/works/": json.dumps(cr).encode(),
                        "api.semanticscholar.org": json.dumps({"citationCount": 1}).encode()})
        code, out = run("--arxiv", "0000.44444", "--json", "--citations", "0", fetch=f)
        card = json.loads(out)
        self.assertTrue(card["bibtex"].startswith("@misc{"), card["bibtex"])
        self.assertNotIn("SSRN", card["bibtex"])

    def test_a_research_square_doi_asked_for_is_cited_as_what_it_is(self):
        cr = {"message": {"DOI": "10.21203/rs.3.rs-999/v1", "type": "posted-content", "title": ["Invented"],
                          "author": [{"given": "Jane", "family": "Doe"}], "issued": {"date-parts": [[2030]]},
                          "container-title": ["Research Square"]}}
        f = fetch_with({"api.openalex.org": down(), "api.crossref.org": json.dumps(cr).encode(),
                        "api.semanticscholar.org": down()})
        code, out = run("--doi", "10.21203/rs.3.rs-999/v1", "--json", fetch=f)
        bib = json.loads(out)["bibtex"]
        self.assertTrue(bib.startswith("@misc{"), bib)


class SmallThings(unittest.TestCase):
    def test_an_openalex_author_without_a_name_is_left_out(self):
        work = {**t.OPENALEX, "authorships": [{"author": {"display_name": None}},
                                              {"author": {"display_name": "Jane Doe"}}]}
        f = fetch_with({"api.openalex.org/works/": json.dumps(work).encode(), "export.arxiv.org": down()})
        code, out = run("--doi", "10.0000/sc.444", "--json", "--citations", "0", fetch=f)
        card = json.loads(out)
        self.assertEqual(card["identity"]["authors"], ["Jane Doe"])

    def test_an_old_style_arxiv_id_with_a_v_is_read_whole(self):
        work = {**t.OPENALEX, "locations": [{"landing_page_url": "https://arxiv.org/abs/solv-int/9901001v2"}]}
        cr = {"message": {**t.CROSSREF["message"], "relation": {}}}
        f = fetch_with({"api.openalex.org/works/": json.dumps(work).encode(),
                        "api.crossref.org": json.dumps(cr).encode()})
        code, out = run("--doi", "10.0000/sc.444", "--json", "--citations", "0", fetch=f)
        self.assertEqual(json.loads(out)["identity"]["arxiv"], "solv-int/9901001")

    def test_more_than_a_hundred_citing_papers_are_paged(self):
        def page(u):
            n = int(urllib.parse.parse_qs(urllib.parse.urlsplit(u).query).get("page", ["1"])[0])
            res = [{"id": f"https://openalex.org/W{n}{i:03d}", "title": f"Citing {n}-{i}", "authorships": []}
                   for i in range(100 if n == 1 else 30)]
            return json.dumps({"meta": {"count": 130}, "results": res}).encode()
        code, out = run("--arxiv", "0000.44444", "--json", "--citations", "120",
                        fetch=fetch_with({"filter=cites:": page}))
        card = json.loads(out)
        self.assertEqual(len(card["citing"]), 120)
        self.assertEqual(card["citing"][-1]["title"], "Citing 2-19")

    def test_the_arxiv_primary_class_goes_in_the_bibtex(self):
        feed = t.ATOM.replace("<arxiv:comment>", '<arxiv:primary_category term="quant-ph"/><arxiv:comment>')
        f = fetch_with({"export.arxiv.org": feed.encode(), "api.openalex.org/works/": down(),
                        "api.crossref.org": down(), "api.semanticscholar.org": down()})
        code, out = run("--arxiv", "0000.44444", "--json", "--citations", "0", fetch=f)
        self.assertIn("primaryclass = {quant-ph}", json.loads(out)["bibtex"])


if __name__ == "__main__":
    unittest.main()
