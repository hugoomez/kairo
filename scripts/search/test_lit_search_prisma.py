"""lit_search: PRISMA counts — database search and citation search on their own
lines, records that are not works (a peer review, a journal issue, a dataset)
dropped and counted, a snowball neighbour's missing abstract filled by DOI
(invented works only)."""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lit_search as ls  # noqa: E402
from test_lit_search import Base, FakeWeb  # noqa: E402


def crossref_page(items):
    return json.dumps({"message": {"total-results": len(items), "items": items}}).encode()


class NonWorks(unittest.TestCase):
    def test_crossref_non_work_types_are_dropped_and_counted(self):
        items = [{"DOI": f"10.9999/nw.{i}", "title": [f"Invented toy record {i}"], "type": t,
                  "issued": {"date-parts": [[2030, 2, 2]]}}
                 for i, t in enumerate(["journal-article", "peer-review", "journal-issue", "dataset",
                                        "proceedings-article", "component"])]
        p = ls.load_plan_dict({"description": "invented", "facets": [{"id": "A", "term": "toy record"}],
                               "sources": ["crossref"]})
        q = ls.build_queries(p, "2030-12-31")[0]
        with tempfile.TemporaryDirectory() as d:
            recs = ls.run_query(q, p, Path(d), lambda u, h: crossref_page(items))
        self.assertEqual(sorted(r["doi"] for r in recs), ["10.9999/nw.0", "10.9999/nw.4"])
        self.assertEqual((q["fetched"], q["not_work"]), (6, 4))

    def test_openalex_paratext_and_erratum_are_not_works(self):
        raw = json.dumps({"meta": {"count": 3}, "results": [
            {"id": "https://openalex.org/W1", "title": "Invented toy article", "type": "article"},
            {"id": "https://openalex.org/W2", "title": "Front matter of an invented issue", "type": "paratext"},
            {"id": "https://openalex.org/W3", "title": "Correction to an invented article", "type": "erratum"}]})
        recs, _ = ls.parse_openalex(raw.encode())
        self.assertEqual([ls.non_work(r) for r in recs], [False, True, True])
        self.assertIn("type", ls.OA_SELECT.split(","))


class Reconciled(Base):
    class Web(FakeWeb):
        """Crossref also returns a peer review; the snowball brings a neighbour
        with no abstract whose abstract OpenAlex has."""

        def __call__(self, url, headers):
            host = urllib.parse.urlsplit(url).netloc
            if host == "api.crossref.org":
                self.urls.append(url)
                return crossref_page([
                    {"DOI": "10.0000/QCE.7", "title": ["Toy decoder on invented hardware"], "type": "proceedings-article",
                     "author": [{"given": "Ana", "family": "Poe"}], "issued": {"date-parts": [[2031, 3, 2]]},
                     "abstract": "A toy decoder for a toy code."},
                    {"DOI": "10.0000/review.1", "title": ["Review of: Toy decoder on invented hardware"],
                     "type": "peer-review", "issued": {"date-parts": [[2031, 3, 9]]}}])
            if host == "api.semanticscholar.org" and "/citations" in url:
                self.urls.append(url)
                return json.dumps({"data": [{"citingPaper": {
                    "paperId": "s7", "title": "A toy code meets a toy decoder in an invented lab", "year": 2031,
                    "publicationDate": "2031-04-04", "externalIds": {"DOI": "10.0000/bare.1"},
                    "authors": [{"name": "Eva Loe"}]}}, {"citingPaper": {
                        "paperId": "s8", "title": "Unrelated invented botany", "year": 2031,
                        "externalIds": {"DOI": "10.0000/far.1"}, "abstract": "Leaves.",
                        "authors": [{"name": "Ira Moe"}]}}]}).encode()
            if host == "api.openalex.org" and "filter=doi" in url:
                self.urls.append(url)
                return json.dumps({"results": [{"id": "https://openalex.org/W77", "doi": "https://doi.org/10.0000/bare.1",
                                                "abstract_inverted_index": {"Invented": [0], "abstract.": [1]}}]}).encode()
            return super().__call__(url, headers)

    def test_identified_by_database_and_by_citations_reconcile_with_dedup(self):
        web = self.Web()
        code, out = self.run_search(web)
        self.assertEqual(code, 0, out)
        n_crossref = sum(1 for q in ls.load(self.run_dir / "queries.json") if q["source"] == "crossref")
        self.assertEqual(out["not_work"], n_crossref)            # the peer review, once per Crossref query
        cands = ls.load(self.run_dir / "candidates.json")
        self.assertNotIn("10.0000/review.1", [c.get("doi") for c in cands])
        key = next(c["key"] for c in cands if c.get("arxiv") == "0000.00003")
        code, out = self.cli("snowball", "--run", str(self.run_dir), "--keys", key, "--direction", "citations",
                             web=web)
        self.assertEqual(code, 0, out)
        cands = ls.load(self.run_dir / "candidates.json")
        bare = next(c for c in cands if c.get("doi") == "10.0000/bare.1")
        self.assertEqual(bare["abstract"], "Invented abstract.")
        self.assertIn("completado por DOI", bare["abstract_fuente"])
        self.cli("retraction", "--run", str(self.run_dir))
        d = {c["key"]: {"decision": "exclude", "reason": "relevancia baja", "why": "Only an invented toy."}
             for c in cands if ls.passes_prefilter(c) and c.get("doi") != "10.0000/sc.1"}
        p = self.tmp / "d.json"
        p.write_text(json.dumps(d), encoding="utf-8")
        code, out = self.cli("screen", "--run", str(self.run_dir), "--decisions", str(p))
        self.assertEqual(code, 0, out)
        c = out["counts"]
        self.assertEqual(c["identificados_citas"], {"s2": 2})
        self.assertNotIn("snowball", json.dumps(c["identificados"]))
        self.assertEqual(c["citas_conservadas"], 1)
        self.assertEqual(c["tipo_no_articulo"], n_crossref)
        self.assertEqual(c["identificados_total"] - c["tipo_no_articulo"] + c["citas_conservadas"]
                         - c["duplicados"], c["tras_deduplicacion"])
        md = (self.run_dir / "busqueda.md").read_text(encoding="utf-8")
        self.assertRegex(md, r"- Identificados en bases de datos: .*\(total \d+\)")
        self.assertIn("- Identificados por búsqueda de citas (snowball): s2 2", md)
        self.assertRegex(md, rf"tipo no artículo \([^\n]*: {n_crossref}\n")
        self.assertTrue(re.search(r"- Duplicados eliminados: \d+", md))


if __name__ == "__main__":
    unittest.main()
