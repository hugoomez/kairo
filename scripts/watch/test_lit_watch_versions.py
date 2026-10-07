"""lit_watch: a published version of a preprint the watch already offered, or
the vault already holds, is reported as that — never offered again as a new
paper (invented ids and text only)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lit_watch as lw  # noqa: E402

PRE_TITLE = "Distributed toy-vector simulation of invented circuits on many toy GPUs"
PUB_TITLE = "Distributed toy-vector simulation of invented circuits on many toy GPUs at scale"
ABSTRACT = "We simulate invented circuits with a distributed toy vector on invented GPUs."

PLAN = {"description": "invented watch", "sources": ["arxiv", "crossref"], "from": "2030-01-01",
        "facets": [{"id": "A", "term": "toy-vector simulation"}, {"id": "B", "term": "invented circuits"}],
        "cross": False}


def atom(entries: list[dict]) -> bytes:
    body = "".join(f"""<entry><id>http://arxiv.org/abs/{e['arxiv']}v1</id><published>{e['date']}T00:00:00Z</published>
<title>{e['title']}</title><summary>{ABSTRACT}</summary><author><name>Ada Inventa</name></author></entry>"""
                   for e in entries)
    return (f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" '
            f'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"><opensearch:totalResults>{len(entries)}'
            f"</opensearch:totalResults>{body}</feed>").encode()


def crossref(items: list[dict]) -> bytes:
    return json.dumps({"message": {"total-results": len(items), "items": [{
        "DOI": it["doi"], "title": [it["title"]], "author": [{"given": "Ada", "family": "Inventa"}],
        "issued": {"date-parts": [[2030, 6, 1]]}, "container-title": ["Invented Journal of Toy Computing"],
        "type": "journal-article", "abstract": ABSTRACT} for it in items]}}).encode()


def source(arxiv_entries, crossref_items):
    def fetch(url, headers):
        if "arxiv.org" in url:
            return atom(arxiv_entries)
        if "api.crossref.org" in url:
            return crossref(crossref_items)
        return json.dumps({"results": [], "meta": {"count": 0}}).encode()
    return fetch


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / "Papers").mkdir()
        plan = self.vault / "plan.json"
        plan.write_text(json.dumps(PLAN), encoding="utf-8")
        lw.cmd_init(self.vault, "toy-watch", plan, date(2030, 5, 1))
        self.pdir = self.vault / "Projects" / "toy-watch"

    def tearDown(self):
        self.tmp.cleanup()

    def watch(self, fetch, day):
        return lw.delta(self.vault, self.pdir, None, 10, fetch, day, citations=False)

    def run_file(self, out):
        return lw.load_run(self.vault / out["run"])


class PublishedAfterOffered(Base):
    def test_published_version_of_an_offered_preprint_is_not_a_new_candidate(self):
        first = self.watch(source([{"arxiv": "2999.00011", "title": PRE_TITLE, "date": "2030-05-03"}], []),
                           date(2030, 5, 8))
        self.assertEqual([c["key"] for c in self.run_file(first)["candidates"]], ["arxiv:2999.00011"])
        second = self.watch(source([], [{"doi": "10.9999/toy.77", "title": PUB_TITLE}]), date(2030, 6, 5))
        run = self.run_file(second)
        self.assertEqual([c["key"] for c in run["candidates"] if c["key"] == "doi:10.9999/toy.77"], [])
        pubs = run.get("publicadas") or []
        self.assertEqual([(p["key"], p["of"]) for p in pubs], [("doi:10.9999/toy.77", "arxiv:2999.00011")])
        self.assertIn("Invented Journal of Toy Computing", pubs[0]["venue"])
        page = (self.vault / second["digest"]).read_text(encoding="utf-8")
        self.assertIn("Versiones publicadas", page)
        self.assertIn("10.9999/toy.77", page)

    def test_different_first_author_is_a_different_paper(self):
        self.watch(source([{"arxiv": "2999.00011", "title": PRE_TITLE, "date": "2030-05-03"}], []), date(2030, 5, 8))

        def fetch(url, headers):
            if "api.crossref.org" in url:
                data = json.loads(crossref([{"doi": "10.9999/toy.78", "title": PUB_TITLE}]))
                data["message"]["items"][0]["author"] = [{"given": "Otto", "family": "Fremd"}]
                return json.dumps(data).encode()
            return source([], [])(url, headers)
        run = self.run_file(self.watch(fetch, date(2030, 6, 5)))
        self.assertIn("doi:10.9999/toy.78", [c["key"] for c in run["candidates"]])
        self.assertEqual(run.get("publicadas") or [], [])


class ConfigWarnings(Base):
    def test_a_keyless_watch_names_the_missing_key(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {}, clear=True):
            out = lw.delta(self.vault, self.pdir, None, 10, source([], []), date(2030, 5, 8), citations=True)
        self.assertTrue(any(w.startswith("OPENALEX_API_KEY") for w in out["config_warnings"]))


class EnrichOnlyWhatMatters(Base):
    def test_loose_matches_get_no_abstract_lookup(self):
        urls = []

        def fetch(url, headers):
            urls.append(url)
            if "api.crossref.org" in url:
                data = json.loads(crossref([{"doi": "10.9999/rail.1", "title": "An invented railway signalling model"},
                                            {"doi": "10.9999/sim.1", "title": PRE_TITLE}]))
                for it in data["message"]["items"]:
                    it.pop("abstract")
                return json.dumps(data).encode()
            return source([], [])(url, headers)
        self.watch(fetch, date(2030, 5, 8))
        lookups = [u for u in urls if "api.openalex.org/works?filter=doi:" in u]
        self.assertTrue(lookups)
        self.assertTrue(all("10.9999/rail.1" not in u for u in lookups))
        self.assertTrue(any("10.9999/sim.1" in u for u in lookups))


class PublishedAfterIngested(Base):
    def test_published_version_of_an_ingested_preprint_names_the_paper(self):
        (self.vault / "Papers" / "P-0001 Distributed toy.md").write_text(
            f'---\nid: P-0001\ntitle: "{PRE_TITLE}"\nauthors: ["Ada Inventa"]\narxiv: 2999.00011\n---\n\n'
            "## Referencia\n\ninvented\n", encoding="utf-8")
        run = self.run_file(self.watch(source([], [{"doi": "10.9999/toy.77", "title": PUB_TITLE}]),
                                       date(2030, 6, 5)))
        self.assertNotIn("doi:10.9999/toy.77", [c["key"] for c in run["candidates"]])
        self.assertEqual([p["of"] for p in run.get("publicadas") or []], ["P-0001"])


if __name__ == "__main__":
    unittest.main()
