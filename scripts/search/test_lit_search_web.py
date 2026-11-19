"""lit_search web: pages a session found with its own web search, fetched, kept and
parsed by the script, merged into the run, counted apart in PRISMA and replayed
from the kept bytes (invented pages only)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lit_search as ls  # noqa: E402
from test_lit_search import Base, FakeWeb  # noqa: E402

REPORT = b"""<html><head><title>Invented vendor report on toy code leaks</title>
<meta property="og:site_name" content="Invented Vendor">
<meta property="article:published_time" content="2030-09-01T00:00:00Z">
<meta name="description" content="An invented report: how often a toy code and its toy decoder leak keys in imaginary repositories."></head>
<body><p>Body.</p></body></html>"""

EPRINT = b"""<html><head>
<meta name="citation_title" content="An invented toy decoder note on the imaginary eprint server">
<meta name="citation_author" content="Ada Inventada">
<meta name="citation_publication_date" content="2031/01/15">
<meta name="citation_abstract" content="We propose an invented toy decoder for a toy code and measure nothing real at all."></head>
<body></body></html>"""

OLD = b"""<html><head><title>An old toy code blog post from long ago</title>
<meta name="date" content="2019-02-02"></head><body><p>A toy code and a toy decoder, years before the window opened.</p></body></html>"""

PAGES = {"https://vendor.example.com/report": REPORT,
         "https://eprint.example.org/2031/15": EPRINT,
         "https://blog.example.net/old": OLD,
         "https://eprint.example.org/2031/15.pdf": b"%PDF-1.7 invented"}


class WebAndApis(FakeWeb):
    def __init__(self, blocked=(), **kw):
        super().__init__(**kw)
        self.blocked = blocked

    def __call__(self, url, headers):
        if url in self.blocked:
            self.urls.append(url)
            raise ls.net.HttpError(url, 403, "Forbidden")
        if url in PAGES:
            self.urls.append(url)
            return PAGES[url]
        return super().__call__(url, headers)


BATCH = {"searched_by": "invented-model-1", "queries": [
    {"facet": "A", "engine": "búsqueda web", "query": "toy code leak report", "results": [
        {"url": "https://vendor.example.com/report", "title": "Invented vendor report"},
        {"url": "https://blog.example.net/old", "title": "Old post"},
        {"url": "http://127.0.0.1/admin", "title": "local"},
        {"url": "https://blocked.example.com/x", "title": "A blocked page about a toy decoder for a toy code"}]},
    {"facet": "B", "engine": "búsqueda web", "query": "site:eprint.example.org toy decoder", "results": [
        {"url": "https://eprint.example.org/2031/15", "title": "eprint 2031/15"},
        {"url": "https://vendor.example.com/report", "title": "Invented vendor report"},
        {"url": "https://eprint.example.org/2031/15.pdf", "title": "Toy decoder PDF for a toy code, invented"}]}]}


class Web(Base):
    def add(self, batch=None, web=None):
        p = self.tmp / "web.json"
        p.write_text(json.dumps(batch or BATCH), encoding="utf-8")
        return self.cli("web", "--run", str(self.run_dir), "--batch", str(p),
                        web=web or WebAndApis(blocked=("https://blocked.example.com/x",)))

    def test_pages_are_fetched_kept_and_merged(self):
        self.run_search()
        web = WebAndApis(blocked=("https://blocked.example.com/x",))
        code, out = self.add(web=web)
        self.assertEqual(code, 0, out)
        self.assertNotIn("http://127.0.0.1/admin", web.urls)                 # never asked for
        self.assertEqual(web.urls.count("https://vendor.example.com/report"), 1)   # reused, not refetched
        self.assertEqual(out["queries"], ["W001", "W002"])
        self.assertEqual((out["urls"], out["perdidas"], out["fuera_de_ventana"]), (7, 2, 1))
        cands = ls.load(self.run_dir / "candidates.json")
        rep = next(c for c in cands if c["title"] == "Invented vendor report on toy code leaks")
        self.assertEqual(rep["sources"], ["web"])
        self.assertEqual(rep["queries"], ["W001", "W002"])
        self.assertEqual(rep["venues"], ["web · Invented Vendor"])
        self.assertTrue(rep["abstract"].startswith("An invented report"))
        ep = next(c for c in cands if c["title"].startswith("An invented toy decoder note"))
        self.assertEqual(ep["authors"], ["Ada Inventada"])
        self.assertEqual(ep["date"], "2031-01-15")
        self.assertIn("B", ep["facets"])
        blocked = next(c for c in cands if (c["url"] or "") == "https://blocked.example.com/x")
        self.assertEqual(blocked["abstract"], "")
        self.assertFalse(any("old toy code blog" in c["title"] for c in cands))
        pdf = next(c for c in cands if (c["url"] or "").endswith(".pdf"))
        self.assertEqual(pdf["title"], "Toy decoder PDF for a toy code, invented")
        qs = {q["id"]: q for q in ls.load(self.run_dir / "queries.json")}
        for item in qs["W001"]["raw"] + qs["W002"]["raw"]:
            self.assertTrue((self.run_dir / "raw" / item["file"]).is_file())
        self.assertTrue((self.run_dir / "raw" / "web-lote-001.json").is_file())
        self.assertEqual(qs["W001"]["searched_by"], "invented-model-1")

    def test_prisma_counts_web_apart_and_lists_lost_pages(self):
        self.run_search()
        self.add()
        cands = ls.load(self.run_dir / "candidates.json")
        lines = ls.degraded_lines(ls.load(self.run_dir / "queries.json"))
        self.assertTrue(any("W001" in ln and "2 de 4" in ln for ln in lines), lines)
        summ = ls.summary(ls.load(self.run_dir / "queries.json"), cands, self.run_dir)
        self.assertNotIn("web", summ["identified"])
        self.assertEqual(summ["identified_web"], 7)

    def test_replay_rereads_the_kept_pages_without_network(self):
        self.run_search()
        self.add()

        def no_network(url, headers):
            raise AssertionError(url)
        code, res = self.cli("replay", "--run", str(self.run_dir), "--out", str(self.tmp / "again"), web=no_network)
        self.assertEqual(code, 0, res)
        a = ls.load(self.run_dir / "candidates.json")
        b = ls.load(self.tmp / "again" / "candidates.json")
        self.assertEqual(sorted(c["key"] for c in a if "web" in c["sources"]),
                         sorted(c["key"] for c in b if "web" in c["sources"]))
        self.assertTrue((self.tmp / "again" / "raw" / "web-lote-001.json").is_file())

    def test_bad_batches_are_refused(self):
        self.run_search()
        for bad in ({"queries": []}, {"searched_by": "m", "queries": [{"facet": "Z", "query": "x", "results": []}]},
                    {"searched_by": "m", "queries": [{"facet": "A", "query": "", "results": []}]},
                    {"searched_by": "", "queries": [{"facet": "A", "query": "x", "results": []}]},
                    {"searched_by": "m", "queries": [{"facet": "A", "query": "x", "results": ["https://a.b"]}]}):
            code, out = self.add(batch=bad)
            self.assertEqual(code, 2, (bad, out))

    def test_accept_gives_the_url_of_a_grey_include(self):
        self.run_search()
        self.add()
        cands = ls.load(self.run_dir / "candidates.json")
        rep = next(c for c in cands if c["title"] == "Invented vendor report on toy code leaks")
        for c in cands:
            c["screen"] = {"decision": "include" if c is rep else "exclude", "relevance": "media", "why": "x",
                           "reason": None if c is rep else "fuera de tema"}
        ls.save(self.run_dir / "screened.json", cands)
        out = ls.cmd_accept(self.run_dir)
        inc = next(x for x in out["includes"] if x["key"] == rep["key"])
        self.assertEqual(inc["url"], "https://vendor.example.com/report")


class Keys(Base):
    def test_two_pages_with_the_same_bare_title_get_two_keys(self):
        self.run_search()
        page = b"<html><head><title>x.example</title></head><body></body></html>"
        PAGES["https://x.example/a"] = PAGES["https://x.example/b"] = page
        try:
            batch = {"searched_by": "m", "queries": [{"facet": "A", "query": "q", "results": [
                {"url": "https://x.example/a", "title": "x.example"},
                {"url": "https://x.example/b", "title": "x.example"}]}]}
            p = self.tmp / "w.json"
            p.write_text(json.dumps(batch), encoding="utf-8")
            code, out = self.cli("web", "--run", str(self.run_dir), "--batch", str(p), web=WebAndApis())
        finally:
            PAGES.pop("https://x.example/a")
            PAGES.pop("https://x.example/b")
        self.assertEqual(code, 0, out)
        keys = [c["key"] for c in ls.load(self.run_dir / "candidates.json")]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(sum(k.startswith("w:") for k in keys), 2)
