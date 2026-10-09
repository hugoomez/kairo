"""lit_search: a lost Semantic Scholar snowball query that its OpenAlex fallback
recovered is not degraded coverage; the retraction check covers every
identifier a candidate holds (invented works only)."""

from __future__ import annotations

import json
import sys
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lit_search as ls  # noqa: E402
from test_lit_search import Base, FakeWeb  # noqa: E402


class S2Down(FakeWeb):
    def __call__(self, url, headers):
        u = urllib.parse.unquote(url)
        if "api.openalex.org/works/doi:" in u:
            self.urls.append(url)
            return json.dumps({"id": "https://openalex.org/W77", "title": "Old seed"}).encode()
        if "api.openalex.org/works?" in url and "cites:W77" in u:
            self.urls.append(url)
            return json.dumps({"meta": {"count": 1, "next_cursor": None}, "results": [
                {"id": "https://openalex.org/W78", "doi": "https://doi.org/10.0000/oa.snow",
                 "title": "A toy code paper with a toy decoder, citing the seed",
                 "publication_date": "2031-02-02", "publication_year": 2031, "authorships": [],
                 "abstract_inverted_index": {"toy": [0], "code": [1], "decoder": [2]}}]}).encode()
        return super().__call__(url, headers)


class Recovered(Base):
    def test_a_recovered_snowball_query_is_not_reported_as_missing_papers(self):
        self.run_search()
        web = S2Down(fail_hosts=("api.semanticscholar.org",))
        code, out = self.cli("snowball", "--run", str(self.run_dir), "--seeds", "DOI:10.0000/old.seed",
                             "--direction", "citations", web=web)
        self.assertEqual(code, 0, out)
        qs = ls.load(self.run_dir / "queries.json")
        lost, fb = [q for q in qs if q["pass"] == "snowball-citations"]
        self.assertEqual(lost["recovered_by"], fb["id"])
        deg = "\n".join(ls.degraded_lines(qs))
        self.assertNotIn(lost["id"], deg)
        md = ls.busqueda_md({**ls.load(self.run_dir / "plan.json")}, qs, {
            "identificados": {}, "identificados_total": 0, "tras_deduplicacion": 0, "tras_retraccion": 0,
            "retraccion": {"crossref": {"checked": 0, "removed": 0, "lost": 0},
                           "arxiv": {"checked": 0, "removed": 0, "lost": 0}},
            "incluidos": 0, "motivos": {}, "relevante_fuera_de_alcance": 0})
        row = next(x for x in md.splitlines() if x.startswith(f"| {lost['id']} "))
        self.assertIn(f"recuperada por {fb['id']}", row)

    def test_an_unrecovered_snowball_query_still_is(self):
        class AllDown(FakeWeb):
            pass
        self.run_search()
        web = AllDown(fail_hosts=("api.semanticscholar.org", "api.openalex.org"))
        self.cli("snowball", "--run", str(self.run_dir), "--seeds", "DOI:10.0000/old.seed",
                 "--direction", "citations", web=web)
        qs = ls.load(self.run_dir / "queries.json")
        lost = next(q for q in qs if q["pass"] == "snowball-citations")
        self.assertNotIn("recovered_by", lost)
        self.assertIn(lost["id"], "\n".join(ls.degraded_lines(qs)))


class OtherIds(Base):
    def test_every_identifier_of_a_candidate_is_checked(self):
        self.run_dir.mkdir()
        cands = [{"key": "arxiv:3001.00001", "title": "An invented toy code decoder", "abstract": "toy code decoder",
                  "arxiv": "3001.00001", "doi": "10.0000/clean.1", "facets": {"A": "toy code", "B": "toy decoder"},
                  "anchor": False, "other_ids": {"doi": ["10.0000/sc.1"], "arxiv": []},
                  "prefilter": {"pass": True}}]
        ls.save(self.run_dir / "candidates.json", cands)
        seen = []
        orig = ls.check_retraction.run

        def spy(cs, mailto=None):
            seen.extend(cs)
            return orig(cs, mailto)
        ls.check_retraction.run = spy
        try:
            out = ls.cmd_retraction(self.run_dir, None)
        finally:
            ls.check_retraction.run = orig
        self.assertIn("10.0000/sc.1", [c.get("doi") for c in seen])
        self.assertEqual(out["removed"], ["arxiv:3001.00001"])
        c = ls.load(self.run_dir / "candidates.json")[0]
        self.assertEqual(c["retraction"]["status"], "retracted")
        self.assertTrue(any("10.0000/sc.1" in e for e in c["retraction"]["evidence"]))
