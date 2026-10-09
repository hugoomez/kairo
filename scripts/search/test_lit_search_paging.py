"""lit_search: how one query is paged and how a failure is recorded — OpenAlex
pages by page number, a keyless OpenAlex query is never split, a malformed
arXiv query or a parser crash is a lost query (invented works only)."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lit_search as ls  # noqa: E402

WORKS = [{"id": f"https://openalex.org/W{i}", "doi": f"https://doi.org/10.9999/pg.{i}",
          "title": f"Invented paging study number {i} of a toy series", "publication_year": 2030,
          "publication_date": "2030-03-01", "authorships": [], "cited_by_count": 0,
          "abstract_inverted_index": {"toy": [0], "paging": [1]}} for i in range(250)]


def oa_fetch(url: str, headers: dict) -> bytes:
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    per, page = int(q["per-page"][0]), int(q["page"][0])
    return json.dumps({"meta": {"count": len(WORKS)}, "results": WORKS[(page - 1) * per: page * per]}).encode()


def plan(**kw) -> dict:
    return ls.load_plan_dict({"description": "invented paging question",
                              "facets": [{"id": "A", "term": "toy paging"}], "sources": ["openalex"],
                              "from": "2030-01-01", "to": "2030-12-31", "enrich": False, **kw})


class Paging(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def query(self, p: dict) -> dict:
        return next(q for q in ls.build_queries(p, "2030-12-31") if q["source"] == p["sources"][0])

    def test_openalex_extended_with_per_query_50_reads_every_record_once(self):
        p = plan(per_query=50)
        q = self.query(p)
        with mock.patch.dict(os.environ, {"OPENALEX_API_KEY": "invented-key"}):
            recs = ls.run_query(q, p, self.tmp, oa_fetch)
        dois = [r["doi"] for r in recs]
        self.assertEqual(len(dois), 250)
        self.assertEqual(len(set(dois)), 250)
        self.assertEqual((q["fetched"], q["truncated"]), (250, False))

    def test_openalex_per_query_50_not_extended_reads_its_first_50(self):
        p = plan(per_query=50, max_per_query=60)
        q = self.query(p)
        recs = ls.run_query(q, p, self.tmp, oa_fetch)
        self.assertEqual([r["doi"] for r in recs], [f"10.9999/pg.{i}" for i in range(50)])
        self.assertTrue(q["truncated"])

    def test_keyless_openalex_is_never_split_and_says_why(self):
        p = plan(per_query=50, max_per_query=60, exhaustive=True)
        q = self.query(p)
        calls = []

        def fetch(url, headers):
            calls.append(url)
            return oa_fetch(url, headers)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENALEX_API_KEY", None)
            ls.run_exhaustive(q, p, self.tmp, fetch, "2030-12-31")
        self.assertEqual(len(calls), 1)
        self.assertTrue(q["truncated"])
        self.assertIn("sin OPENALEX_API_KEY", q["not_extended"])
        self.assertNotIn("splits", q)


class LostQueries(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_malformed_arxiv_query_is_lost_with_arxivs_message(self):
        err = (b'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
               b"<opensearch:totalResults>1</opensearch:totalResults><entry>"
               b"<id>http://arxiv.org/api/errors#incorrect_id_format_for_x</id>"
               b"<title>Error</title><summary>incorrect id format for x</summary></entry></feed>")
        p = ls.load_plan_dict({"description": "invented", "facets": [{"id": "A", "term": "toy"}],
                               "sources": ["arxiv"]})
        q = ls.build_queries(p, "2030-12-31")[0]
        recs = ls.run_query(q, p, self.tmp, lambda u, h: err)
        self.assertEqual(recs, [])
        self.assertIn("incorrect id format for x", q["error"])
        self.assertFalse(q["truncated"])

    def test_an_unexpected_parser_error_loses_the_query_not_the_run(self):
        p = ls.load_plan_dict({"description": "invented", "facets": [{"id": "A", "term": "toy"}],
                               "sources": ["crossref"]})
        q = ls.build_queries(p, "2030-12-31")[0]
        bad = json.dumps({"message": {"total-results": 1, "items": [{"DOI": 7, "title": None}]}}).encode()

        def boom(raw):
            raise TypeError("invented parser bug")
        with mock.patch.dict(ls.PARSERS, {"crossref": boom}):
            recs = ls.run_query(q, p, self.tmp, lambda u, h: bad)
        self.assertEqual(recs, [])
        self.assertIn("invented parser bug", q["error"])

    def test_an_incomplete_read_loses_the_query(self):
        import http.client
        p = ls.load_plan_dict({"description": "invented", "facets": [{"id": "A", "term": "toy"}],
                               "sources": ["crossref"]})
        q = ls.build_queries(p, "2030-12-31")[0]

        def cut(url, headers):
            raise http.client.IncompleteRead(b"{")
        self.assertEqual(ls.run_query(q, p, self.tmp, cut), [])
        self.assertIn("IncompleteRead", q["error"])


if __name__ == "__main__":
    unittest.main()
