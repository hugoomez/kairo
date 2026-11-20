"""Tests for the versioned documentation corpus: `ingest_paper.py add --url --doc`
and `docs_corpus.py refresh | list`. Invented pages served by an injected fetch:
no network. Invented tool, ids and text only."""

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_quotes  # noqa: E402
import docs_corpus as dc  # noqa: E402
import ingest_paper as ip  # noqa: E402

URL = "https://docs.example.org/widgetctl/config"
OTHER = "https://docs.example.org/gadgetd/config"
PARA = ("The invented widgetctl tool reads its settings from an imaginary file named widget.conf, "
        "and every option described here is made up for this test. ")
DESC = "Configuration reference for the invented widgetctl tool and its imaginary widget.conf file."


def page(version: str, extra: str = "", title: str = "Configuration | widgetctl docs") -> bytes:
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{title}</title>
<meta name="docsearch:version" content="{version}">
<meta name="description" content="{DESC}">
</head><body><nav>Home Guide Reference</nav><main>
<h1>Configuration</h1>
<p>{PARA * 3}</p><p>{PARA * 2}</p>{extra}
</main><footer>Invented footer</footer></body></html>""".encode()


V1 = page("2.4.1")
V2 = page("2.5.0", "<p>The option fictional_spin was added in this invented release and changes nothing real "
                   "at all, since every word of this page is made up.</p>")


def serving(pages: dict):
    def fetch(url, headers):
        if url in pages:
            return pages[url]
        raise ip.net.HttpError(url, 503, "down")
    return fetch


class DocsCorpus(unittest.TestCase):
    def setUp(self):
        self.vault = Path(tempfile.mkdtemp(prefix="kairo-docs-"))
        (self.vault / "Papers").mkdir()

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    def run_main(self, main, args, pages, today="2031-06-01"):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(args, fetch=serving(pages), today=today)
        return code, json.loads(buf.getvalue()), buf.getvalue()

    def add(self, url=URL, pages=None, *extra):
        return self.run_main(ip.main, ["add", "--vault", str(self.vault), "--project", "PROJ-001", "--url", url,
                                       "--doc", "--tool", "widgetctl", *extra], pages or {URL: V1})[:2]

    def refresh(self, pages, today="2031-07-01", *extra):
        return self.run_main(dc.main, ["refresh", "--vault", str(self.vault), "--project", "PROJ-001", *extra],
                             pages, today)

    def note(self, pid="P-0001") -> Path:
        return next((self.vault / "Papers").glob(f"{pid} *.md"))

    def verify(self) -> dict:
        return self.run_main(ip.main, ["verify", "--vault", str(self.vault)], {})[1]

    def test_add_records_a_documentation_note(self):
        code, out = self.add()
        self.assertEqual(code, 0, out)
        self.assertEqual((out["source_type"], out["tool"], out["doc_version"], out["doc_version_from"]),
                         ("documentacion", "widgetctl", "2.4.1", "meta docsearch:version"))
        text = self.note().read_text(encoding="utf-8")
        for line in ("source: web", "source_type: documentacion", "tool: widgetctl", 'doc_version: "2.4.1"',
                     "accessed_at: 2031-06-01", f'url: "{URL}"', "fulltext: pagina"):
            self.assertIn(line, text)
        self.assertIn(f"sha256: {out['sha256']}", text)
        versions = ip.section_body(text, "Versiones")
        self.assertIn("v1 · consultada 2031-06-01 · versión del documento: 2.4.1", versions)
        self.assertIn("imaginary file named widget.conf", ip.section_body(text, "Texto completo"))
        self.assertEqual(self.verify()["counts"], {"ok": 1})
        listed = check_quotes.list_papers(str(self.vault), "PROJ-001")
        self.assertEqual({k: listed[0][k] for k in ("source_type", "tool", "doc_version", "accessed_at")},
                         {"source_type": "documentacion", "tool": "widgetctl", "doc_version": "2.4.1",
                          "accessed_at": "2031-06-01"})

    def test_ask_corpus_can_quote_a_documentation_note(self):
        self.add()
        answer = (f"La herramienta inventada tiene una referencia de configuración.\n\n> {DESC}\n"
                  "> — P-0001 §Resumen\n")
        report = check_quotes.check(str(self.vault), answer, "PROJ-001")
        self.assertEqual((report["quotes"], report["quotes_ok"], report["removed"]), (1, 1, []), report)

    def test_a_page_is_recognised_by_its_url_not_its_title(self):
        self.add()
        code, out = self.add()
        self.assertEqual((code, out["status"]), (0, "exists"))
        code, out = self.add(OTHER, {OTHER: page("1.0.0")})       # same title, another tool's page
        self.assertEqual((code, out["status"], out["id"]), (0, "created", "P-0002"), out)

    def test_doc_needs_url_and_tool(self):
        _, out, _ = self.run_main(ip.main, ["add", "--vault", str(self.vault), "--project", "PROJ-001",
                                            "--url", URL, "--doc"], {URL: V1})
        self.assertIn("--tool", out["refused"])
        _, out, _ = self.run_main(ip.main, ["add", "--vault", str(self.vault), "--project", "PROJ-001",
                                            "--doi", "10.9999/x", "--doc", "--tool", "w"], {})
        self.assertIn("--doc goes with --url", out["refused"])

    def test_refresh_without_change_only_records_the_check(self):
        self.add()
        before = self.note().read_text(encoding="utf-8")
        code, out, _ = self.refresh({URL: V1.replace(b"Invented footer", b"Another invented footer")})
        self.assertEqual((code, out["counts"]), (0, {"unchanged": 1}), out)
        after = self.note().read_text(encoding="utf-8")
        self.assertIn("checked_at: 2031-07-01", after)
        self.assertEqual(before.split("---\n", 2)[2], after.split("---\n", 2)[2])     # body untouched
        self.assertFalse((self.vault / "Papers" / "_fuentes" / "P-0001" / "historial").exists())

    def test_refresh_keeps_the_previous_version_and_reports_counts_only(self):
        self.add()
        old = self.note().read_text(encoding="utf-8")
        code, out, raw = self.refresh({URL: V2})
        self.assertEqual(code, 0, out)
        r = out["notes"][0]
        self.assertEqual((r["status"], r["version"], r["doc_version_before"], r["doc_version"]),
                         ("new_version", 2, "2.4.1", "2.5.0"))
        self.assertEqual((r["lines_added"], r["lines_removed"]), (1, 0))
        self.assertEqual(r["title"], "Configuration | widgetctl docs")
        self.assertNotIn("fictional_spin", raw)                   # never the page text on stdout
        self.assertNotIn("widget.conf", raw)
        text = self.note().read_text(encoding="utf-8")
        self.assertIn("fictional_spin", ip.section_body(text, "Texto completo"))
        for line in ('doc_version: "2.5.0"', "accessed_at: 2031-07-01", "fetched_at: 2031-07-01"):
            self.assertIn(line, text)
        versions = ip.section_body(text, "Versiones").split("\n")
        self.assertEqual(len(versions), 2)
        self.assertIn("v2 · consultada 2031-07-01 · versión del documento: 2.5.0", versions[1])
        self.assertIn("+1 −0 líneas", versions[1])
        self.assertIn("Papers/_fuentes/P-0001/historial/v1/", versions[1])
        hist = self.vault / "Papers" / "_fuentes" / "P-0001" / "historial"
        self.assertEqual((hist / "v1" / "nota.md").read_text(encoding="utf-8"), old)
        self.assertEqual((hist / "v1" / "pagina.html").read_bytes(), V1)
        self.assertTrue((hist / "v1" / "fuentes.json").is_file())
        entries = json.loads((hist / "historial.json").read_text(encoding="utf-8"))["versiones"]
        self.assertEqual([(e["version"], e["doc_version"], e["accessed_at"]) for e in entries],
                         [(1, "2.4.1", "2031-06-01")])
        self.assertEqual(self.verify()["counts"], {"ok": 1})       # the new version verifies from its bytes
        _, listed, _ = self.run_main(dc.main, ["list", "--vault", str(self.vault), "--project", "PROJ-001"], {})
        self.assertEqual((listed["documents"][0]["versions"], listed["documents"][0]["doc_version"]), (2, "2.5.0"))
        # a third version keeps both earlier ones
        code, out, _ = self.refresh({URL: page("2.6.0")}, "2031-08-01")
        self.assertEqual((out["notes"][0]["version"], out["notes"][0]["lines_removed"]), (3, 1), out)
        self.assertTrue((hist / "v2" / "nota.md").is_file())
        self.assertTrue((hist / "v1" / "nota.md").is_file())
        self.assertEqual(len(ip.section_body(self.note().read_text("utf-8"), "Versiones").split("\n")), 3)

    def test_a_lost_page_leaves_the_note_as_it_was(self):
        self.add()
        before = self.note().read_text(encoding="utf-8")
        code, out, _ = self.refresh({})
        self.assertEqual((code, out["counts"]), (0, {"lost": 1}), out)
        self.assertEqual(before, self.note().read_text(encoding="utf-8"))

    def test_dry_run_writes_nothing(self):
        self.add()
        before = self.note().read_text(encoding="utf-8")
        code, out, _ = self.refresh({URL: V2}, "2031-07-01", "--dry-run")
        self.assertEqual(out["notes"][0]["status"], "dry_run")
        self.assertEqual(before, self.note().read_text(encoding="utf-8"))
        self.assertFalse((self.vault / "Papers" / "_fuentes" / "P-0001" / "historial").exists())

    def test_rebuild_refuses_a_documentation_note(self):
        self.add()
        _, out, _ = self.run_main(ip.main, ["rebuild", "--vault", str(self.vault), "--only", "P-0001"], {URL: V2})
        self.assertEqual(out["notes"][0]["status"], "refused")
        self.assertIn("docs_corpus.py refresh", out["notes"][0]["reason"])

    def test_refresh_only_touches_the_projects_documentation(self):
        self.add()
        self.run_main(ip.main, ["add", "--vault", str(self.vault), "--project", "PROJ-002", "--url", OTHER,
                                "--doc", "--tool", "gadgetd"], {OTHER: page("1.0.0")})
        code, out, _ = self.refresh({URL: V1, OTHER: page("9.9.9")})
        self.assertEqual([n["id"] for n in out["notes"]], ["P-0001"])

    def test_detect_version(self):
        def html(head="", body=""):
            return (f"<html><head><title>t</title>{head}</head><body>{body}</body></html>").encode()
        cases = [
            (html('<meta name="version" content="3.1">'), "", ("3.1", "meta version")),
            (html(), "Widgetctl Version 1.12.0 documentation", ("1.12.0", "texto (versión)")),
            (html(), "Docs for widgetctl v0.9.3 (invented)", ("0.9.3", "texto (vN)")),
            (html('<meta property="article:modified_time" content="2031-02-03T10:00:00Z">'), "",
             ("actualizado 2031-02-03", "meta article:modified_time")),
            (html(), "Last updated: March 4, 2031", ("actualizado 2031-03-04", "texto (última actualización)")),
            (html(), "Última actualización: 5 de mayo de 2031",
             ("actualizado 2031-05-05", "texto (última actualización)")),
            (html(), "Nothing here says which release this page is.", ("", None)),
        ]
        for raw, text, want in cases:
            self.assertEqual(dc.detect_version(raw, text), want, text)


if __name__ == "__main__":
    unittest.main()
