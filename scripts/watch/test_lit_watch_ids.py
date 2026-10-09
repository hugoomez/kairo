"""lit_watch: a paper offered once is never offered again under another id — its
DOI, arXiv id, Semantic Scholar id or exact title already seen (invented ids and
text only)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_lit_watch_state as st  # noqa: E402

TEXT = "Fictional widgets measured under synthetic spin in every invented setting."


def atom_with_doi(aid: str, title: str, doi: str) -> bytes:
    return ('<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">'
            f"<entry><id>http://arxiv.org/abs/{aid}v1</id><title>{title}</title><summary>{TEXT}</summary>"
            "<published>2031-03-05T00:00:00Z</published><author><name>Autora Ficticia</name></author>"
            f"<arxiv:doi>{doi}</arxiv:doi></entry></feed>").encode()


def crossref(doi: str, title: str) -> bytes:
    return json.dumps({"message": {"total-results": 1, "items": [{
        "DOI": doi, "title": [title], "author": [{"given": "Otra", "family": "Persona"}],
        "issued": {"date-parts": [[2031, 2, 20]]}, "abstract": TEXT}]}}).encode()


def empty_atom() -> bytes:
    return b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'


class SameWorkOtherId(st.Base):
    def keys(self, res) -> list[str]:
        return [c["key"] for c in json.loads(self.run_file(res).read_text(encoding="utf-8"))["candidates"]]

    def offer_and_read(self, fetch):
        _, first = self.delta_on(st.TODAY, fetch=fetch)
        run = str(self.run_file(first))
        for c in json.loads(Path(run).read_text(encoding="utf-8"))["candidates"]:
            if c["triage"]:
                self.cli("triage", "--project-dir", str(self.p), "--run", run, "--key", c["key"],
                         "--why", "Línea inventada.")
        return first

    def test_an_arxiv_record_carrying_an_offered_doi_is_not_new(self):
        self.plan(sources=("arxiv", "crossref"))
        week = {"n": 1}

        def fetch(url, headers):
            if "api.crossref.org" in url:
                return crossref("10.9999/toy.5", "Toy widgets under synthetic spin, the journal version") \
                    if week["n"] == 1 else json.dumps({"message": {"total-results": 0, "items": []}}).encode()
            if "export.arxiv.org" in url:
                return empty_atom() if week["n"] == 1 else \
                    atom_with_doi("2031.00555", "An entirely retitled preprint about invented things", "10.9999/toy.5")
            return json.dumps({"results": [], "meta": {"count": 0}}).encode()
        first = self.offer_and_read(fetch)
        self.assertIn("doi:10.9999/toy.5", self.keys(first))
        week["n"] = 2
        _, second = self.delta_on(st.date(2031, 3, 8), fetch=fetch)
        self.assertNotIn("arxiv:2031.00555", self.keys(second))

    def test_the_same_exact_title_under_a_new_id_is_not_new(self):
        self.plan(sources=("arxiv", "crossref"))
        week = {"n": 1}
        title = "Toy widgets under synthetic spin across invented regimes"

        def fetch(url, headers):
            if "api.crossref.org" in url:
                return crossref("10.9999/toy.6", title) if week["n"] == 1 else \
                    json.dumps({"message": {"total-results": 0, "items": []}}).encode()
            if "export.arxiv.org" in url:
                return empty_atom() if week["n"] == 1 else atom_with_doi("2031.00666", title + ".", "")
            return json.dumps({"results": [], "meta": {"count": 0}}).encode()
        self.offer_and_read(fetch)
        week["n"] = 2
        _, second = self.delta_on(st.date(2031, 3, 8), fetch=fetch)
        self.assertNotIn("arxiv:2031.00666", self.keys(second))


if __name__ == "__main__":
    unittest.main()
