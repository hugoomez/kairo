"""End-to-end recall regression for the literature search, offline.

A small invented literature is served by a fake of every source the search
queries. A gold set — the papers any competent search of the request must reach
— covers the cases that have cost recall before: a record only one source has,
a proceedings paper that comes without an abstract, a paper on one facet of a
union question, a preprint and its retitled published version, and recent work
reachable only through a seed's citations. The test runs the real search (`run`,
then `snowball`) and measures recall with quality_report.py; a change that
loses any of them fails here, in CI, before it reaches a researcher.

Invented papers and ids only. Run: python -m pytest scripts/quality
"""

from __future__ import annotations

import contextlib
import io
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
sys.path.insert(0, str(HERE.parent / "search"))
import lit_search as ls  # noqa: E402
import quality_report as qr  # noqa: E402

TERMS = ("toy code", "invented code", "toy decoder", "belief toy decoding")

# id, title, abstract, date, sources that index it, extra
WORLD = [
    {"arxiv": "0000.10001", "title": "A toy decoder for toy codes with an invented threshold",
     "abstract": "We give a toy decoder for toy codes.", "date": "2031-02-01", "src": {"arxiv", "openalex"}},
    {"doi": "10.0000/proc.2", "title": "Hardware toy decoder pipelines",
     "abstract": "", "oa_abstract": "A toy decoder pipeline for invented codes on toy code hardware.",
     "date": "2031-03-01", "src": {"crossref"}},
    {"arxiv": "0000.10003", "title": "Constructing a new family of toy codes",
     "abstract": "We construct toy codes with large distance; decoding is left open.",
     "date": "2031-04-01", "src": {"arxiv"}},
    {"arxiv": "0000.10004", "title": "Fast toy decoders for invented codes",
     "abstract": "A fast toy decoder for invented codes.", "date": "2030-11-01", "src": {"arxiv"},
     "authors": ["Jane Doe"]},
    {"doi": "10.0000/journal.4", "title": "Fast toy decoders for invented quantum codes",
     "abstract": "A fast toy decoder for invented codes, journal version.", "date": "2031-05-01",
     "src": {"crossref", "openalex"}, "authors": ["Doe, Jane"]},
    {"doi": "10.0000/cites.seed", "title": "Toy decoder improvements building on the seed",
     "abstract": "We improve the toy decoder of the seed paper on toy codes.", "date": "2031-06-01",
     "src": set(), "cites_seed": True},
    {"arxiv": "0000.19999", "title": "An unrelated paper on invented weather",
     "abstract": "Weather.", "date": "2031-01-05", "src": {"arxiv"}},
]
GOLD = [{"arxiv": "0000.10001"}, {"doi": "10.0000/proc.2"}, {"arxiv": "0000.10003"},
        {"doi": "10.0000/journal.4"}, {"doi": "10.0000/cites.seed"}]
PLAN = {"description": "Toy codes and their toy decoders, invented literature",
        "facets": [{"id": "A", "term": "toy code", "synonyms": ["invented code"]},
                   {"id": "B", "term": "toy decoder", "synonyms": ["belief toy decoding"]}],
        "sources": ["arxiv", "s2", "openalex", "crossref"], "from": "2030-06-01", "min_facets": 1,
        "include": ["a code or a decoder result"], "exclude": [], "scope_out": []}


GROUPS = (("toy code", "invented code"), ("toy decoder", "belief toy decoding"))


def _matches(rec: dict, q: str, boolean: bool = False) -> bool:
    """A record answers a query when its text has one of the query's terms; a
    boolean query that ANDs the facets (arXiv / OpenAlex cross pass) needs one
    term of every facet it names — as the real sources do."""
    text = (rec["title"] + " " + rec.get("abstract", "") + " " + rec.get("oa_abstract", "")).lower()
    named = [g for g in GROUPS if any(t in q for t in g)]
    if boolean and " and " in q and len(named) > 1:
        return all(any(t in text for t in g) for g in named)
    return any(t in q and t in text for t in TERMS)


def _inv(text: str) -> dict:
    ii: dict = {}
    for i, w in enumerate(text.split()):
        ii.setdefault(w, []).append(i)
    return ii


class World:
    def __call__(self, url: str, headers: dict) -> bytes:
        u = urllib.parse.unquote(url).lower()
        host = urllib.parse.urlsplit(url).netloc
        if host == "export.arxiv.org":
            recs = [r for r in WORLD if "arxiv" in r["src"] and _matches(r, u, boolean=True)]
            body = "".join(f"<entry><id>http://arxiv.org/abs/{r['arxiv']}v1</id><published>{r['date']}T00:00:00Z"
                           f"</published><title>{r['title']}</title><summary>{r['abstract']}</summary>"
                           f"<author><name>{(r.get('authors') or ['A B'])[0]}</name></author></entry>" for r in recs)
            return (f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
                    f"<opensearch:totalResults>{len(recs)}</opensearch:totalResults>{body}</feed>").encode()
        if host == "api.semanticscholar.org":
            if "/citations" in url:
                return json.dumps({"data": [{"citingPaper": {
                    "paperId": "c1", "title": r["title"], "abstract": r["abstract"], "publicationDate": r["date"],
                    "externalIds": {"DOI": r["doi"]}, "authors": []}} for r in WORLD if r.get("cites_seed")]}).encode()
            if "/references" in url:
                return json.dumps({"data": []}).encode()
            return json.dumps({"total": 0, "data": []}).encode()
        if host == "api.crossref.org":
            recs = [r for r in WORLD if "crossref" in r["src"] and _matches(r, u)]
            return json.dumps({"message": {"total-results": len(recs), "items": [
                {"DOI": r["doi"], "title": [r["title"]], "abstract": r["abstract"],
                 "author": [{"family": (r.get("authors") or ["X"])[0].split(",")[0].split()[-1]}],
                 "issued": {"date-parts": [[int(r["date"][:4]), int(r["date"][5:7]), int(r["date"][8:10])]]},
                 "container-title": ["Invented Proceedings"]} for r in recs]}}).encode()
        if host == "api.openalex.org":
            if "filter=doi:" in u:                     # abstract enrichment by DOI
                return json.dumps({"results": [{"id": "https://openalex.org/W2", "doi": f"https://doi.org/{r['doi']}",
                                                "abstract_inverted_index": _inv(r["oa_abstract"])}
                                               for r in WORLD if r.get("oa_abstract") and r["doi"] in u]}).encode()
            recs = [r for r in WORLD if "openalex" in r["src"] and _matches(r, u, boolean=True)]
            return json.dumps({"meta": {"count": len(recs)}, "results": [
                {"id": f"https://openalex.org/W{i}", "doi": f"https://doi.org/{r['doi']}" if r.get("doi") else None,
                 "title": r["title"], "publication_date": r["date"], "publication_year": int(r["date"][:4]),
                 "authorships": [{"author": {"display_name": a}} for a in r.get("authors") or []],
                 "locations": [{"landing_page_url": f"https://arxiv.org/abs/{r['arxiv']}"}] if r.get("arxiv") else [],
                 "abstract_inverted_index": _inv(r["abstract"])} for i, r in enumerate(recs)]}).encode()
        raise AssertionError(url)


class RecallRegression(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-recall-"))
        self.env = mock.patch.dict(os.environ, {"KAIRO_STATE_DIR": str(self.tmp / "state")})
        self.env.start()
        (self.tmp / "plan.json").write_text(json.dumps(PLAN), encoding="utf-8")
        (self.tmp / "gold.json").write_text(json.dumps(GOLD), encoding="utf-8")
        self.run = self.tmp / "run"

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = ls.main(list(args), fetch=World(), today="2031-07-01")
        return code, json.loads(buf.getvalue())

    def test_every_gold_paper_is_found_and_reaches_a_screener(self):
        code, out = self.cli("run", "--plan", str(self.tmp / "plan.json"), "--out", str(self.run))
        self.assertEqual(code, 0, out)
        code, out = self.cli("snowball", "--run", str(self.run), "--seeds", "DOI:10.0000/old.seed",
                             "--direction", "citations")
        self.assertEqual(code, 0, out)
        rep = qr.eval_search(self.run, self.tmp / "gold.json")
        self.assertEqual(rep["missed"], [], rep)
        self.assertEqual(rep["recall_identified"], 1.0)
        cands = json.loads((self.run / "candidates.json").read_text(encoding="utf-8"))
        for g in GOLD:
            keys = qr.gold_keys(g)
            hit = next(c for c in cands if keys & qr.cand_keys(c))
            self.assertTrue(ls.passes_prefilter(hit), (g, hit.get("prefilter")))
        # a preprint and its retitled published version are one candidate, not two
        pair = [c for c in cands if c.get("arxiv") == "0000.10004" or c.get("doi") == "10.0000/journal.4"]
        self.assertEqual(len(pair), 1, pair)
        # the proceedings paper was read with the abstract OpenAlex filled in
        proc = next(c for c in cands if c.get("doi") == "10.0000/proc.2")
        self.assertTrue(proc["abstract"])


if __name__ == "__main__":
    unittest.main()
