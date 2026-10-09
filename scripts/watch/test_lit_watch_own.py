"""lit_watch delta: what changed for the project's own papers — a newer arXiv
version, a published version arXiv now declares, an OpenReview acceptance.
Invented vault, ids and text only; no network."""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.parse
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lit_watch  # noqa: E402

TODAY = date(2031, 3, 1)
HUB = "---\nid: PROJ-970\nname: Propios inventados\nlast_watch: 2031-02-01\n---\n"
PLAN = {"description": "x", "facets": [{"id": "A", "term": "fictional widgets", "synonyms": []}],
        "sources": ["arxiv"], "from": "2020-01-01", "per_query": 100, "anchors": 0,
        "arxiv_categories": [], "include": [], "exclude": [], "scope_out": []}


def note(pid, title, arxiv, version="v1", extra=""):
    return (f'---\nid: {pid}\ntitle: "{title}"\nauthors: ["Ana Ficticia", "Rui Roe"]\narxiv: {arxiv}\n'
            f"arxiv_version: {version}\nprojects: [PROJ-970]\n{extra}---\n\n## Referencia\n\nx\n")


def entry(aid, version, doi=""):
    return (f"<entry><id>http://arxiv.org/abs/{aid}v{version}</id><title>t</title><summary>s</summary>"
            f"<published>2030-01-01T00:00:00Z</published><updated>2031-02-20T00:00:00Z</updated>"
            + (f"<arxiv:doi>{doi}</arxiv:doi>" if doi else "") + "</entry>")


class Net:
    def __init__(self, accepted_title=None, fail_openreview=False):
        self.urls = []
        self.accepted_title = accepted_title
        self.fail_openreview = fail_openreview

    def __call__(self, url, headers):
        self.urls.append(url)
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if "id_list" in q:
            return ('<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">'
                    + entry("2030.00001", 3) + entry("2030.00002", 1, doi="10.9999/pub.2")
                    + entry("2030.00003", 1) + "</feed>").encode()
        if "export.arxiv.org" in url:
            return b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
        if "openreview" in url:
            if self.fail_openreview:
                raise lit_watch.net.HttpError(url, 503, "down")
            term = q["term"][0]
            if self.accepted_title and term == self.accepted_title:
                return json.dumps({"count": 1, "notes": [{"id": "or1", "cdate": 1916006400000, "content": {
                    "title": {"value": term}, "venue": {"value": "ICLR 2031 Poster"},
                    "authors": {"value": ["Ana Ficticia"]},
                    "_bibtex": {"value": "@inproceedings{a,\ntitle={x},\nbooktitle={Invented ICLR},\nyear={2031}\n}"}}}]
                }).encode()
            return json.dumps({"count": 0, "notes": []}).encode()
        return json.dumps({"data": [], "results": [], "meta": {"count": 0}}).encode()


class OwnPapers(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-own-"))
        self.env = mock.patch.dict(os.environ, {"KAIRO_STATE_DIR": str(self.tmp / "state")})
        self.env.start()
        self.vault = self.tmp / "vault"
        self.p = self.vault / "Projects" / "propios"
        (self.p / "_busquedas" / "2031-01-15").mkdir(parents=True)
        (self.p / "_busquedas" / "2031-01-15" / "plan.json").write_text(json.dumps(PLAN), encoding="utf-8")
        (self.p / "_hub.md").write_text(HUB, encoding="utf-8")
        papers = self.vault / "Papers"
        papers.mkdir()
        (papers / "P-0971 a.md").write_text(note("P-0971", "Revised toy widgets", "2030.00001"), encoding="utf-8")
        (papers / "P-0972 b.md").write_text(note("P-0972", "Published toy widgets", "2030.00002"), encoding="utf-8")
        (papers / "P-0973 c.md").write_text(note("P-0973", "Accepted toy widgets", "2030.00003"), encoding="utf-8")
        (papers / "P-0974 d.md").write_text(note("P-0974", "Other project", "2030.00004").replace(
            "PROJ-970", "PROJ-9700"), encoding="utf-8")

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def delta(self, net, *extra):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = lit_watch.main(["delta", "--vault", str(self.vault), "--project-dir", str(self.p),
                                   "--no-citations", *extra], fetch=net, today=TODAY)
        return code, json.loads(buf.getvalue())

    def test_new_versions_declared_publications_and_acceptances_are_reported(self):
        net = Net(accepted_title="Accepted toy widgets")
        code, res = self.delta(net)
        self.assertEqual(code, 0, res)
        own = json.loads((self.vault / res["run"]).read_text(encoding="utf-8"))["tus_papers"]
        self.assertEqual([(v["id"], v["leida"], v["ultima"]) for v in own["versiones"]], [("P-0971", "v1", "v3")])
        self.assertEqual([(v["id"], v["doi"]) for v in own["publicadas_arxiv"]], [("P-0972", "10.9999/pub.2")])
        self.assertEqual([(v["id"], v["venue"]) for v in own["aceptadas_openreview"]],
                         [("P-0973", "Invented ICLR")])
        self.assertFalse(any("Other%20project" in u for u in net.urls))       # another project's paper
        page = (self.vault / res["digest"]).read_text(encoding="utf-8")
        self.assertIn("## Tus papers: qué ha cambiado", page)
        self.assertIn("P-0971", page)
        self.assertIn("aceptado en OpenReview", page)
        asked = json.loads((self.p / "_vigilancia" / "openreview.json").read_text(encoding="utf-8"))
        self.assertEqual(set(asked), {"P-0971", "P-0973"})     # P-0972 has a declared version now
        # nothing was written to the paper notes
        self.assertNotIn("published", (self.vault / "Papers" / "P-0973 c.md").read_text(encoding="utf-8"))

    def test_openreview_is_asked_a_few_papers_per_watch_least_recently_first(self):
        with mock.patch.object(lit_watch, "OPENREVIEW_PER_RUN", 1):
            code, res = self.delta(Net())
            self.assertEqual(code, 0, res)
            first = json.loads((self.p / "_vigilancia" / "openreview.json").read_text(encoding="utf-8"))
            self.assertEqual(set(first), {"P-0971"})
            self.assertEqual(res["tus_papers"]["openreview_pendientes"], 1)
            with mock.patch.object(lit_watch, "OPENREVIEW_PER_RUN", 1):
                self.delta(Net(), "--since", "2031-02-15")
            second = json.loads((self.p / "_vigilancia" / "openreview.json").read_text(encoding="utf-8"))
            self.assertEqual(set(second), {"P-0971", "P-0973"})

    def test_a_failed_check_is_reported_and_never_costs_the_watch(self):
        code, res = self.delta(Net(fail_openreview=True))
        self.assertEqual(code, 0, res)
        self.assertGreaterEqual(res["tus_papers"]["perdidas"], 2)
        code, res = self.delta(Net(), "--no-own-papers", "--since", "2031-02-15")
        self.assertEqual(code, 0, res)
        self.assertNotIn("tus_papers", res)


if __name__ == "__main__":
    unittest.main()
