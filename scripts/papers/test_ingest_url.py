"""Tests for `ingest_paper.py add --url` (grey literature) and its bibliography
entry. Invented pages, served by an injected fetch: no network."""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import export_bib  # noqa: E402
import ingest_paper as ip  # noqa: E402
from test_ingest_paper import Base  # noqa: E402

URL = "https://reports.example.org/2031/invented-secrets"
PARA = ("This invented report describes how an imaginary tool keeps encrypted values in a repository, "
        "which recipients it names and why none of this is real. ")
PAGE = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Invented Secrets Report 2031 | Example Org</title>
<meta property="og:site_name" content="Example Org Research">
<meta name="author" content="Ana Ficticia">
<meta property="article:published_time" content="2031-02-03T09:00:00Z">
<meta name="description" content="A made-up annual report on invented secrets kept in imaginary repositories.">
</head><body><nav>Home Products Pricing Login</nav>
<h1>Invented Secrets Report 2031</h1>
<p>{PARA * 3}</p><p>{PARA * 2}</p>
<footer>Copyright Example Org</footer></body></html>""".encode()


def fetch_serving(pages: dict):
    def fetch(url, headers):
        if url in pages:
            return pages[url]
        raise ip.net.HttpError(url, 404, "not found")
    return fetch


class AddUrl(Base):
    def add_url(self, url=URL, pages=None, *extra):
        return self.add("--url", url, *extra, fetch=fetch_serving(pages if pages is not None else {URL: PAGE}))

    def test_a_web_page_becomes_a_verifiable_note(self):
        code, out = self.add_url()
        self.assertEqual(code, 0, out)
        self.assertEqual((out["status"], out["fulltext"], out["abstract"]), ("created", "pagina", "found"))
        text = self.note().read_text(encoding="utf-8")
        self.assertIn("fulltext: pagina", text)
        self.assertIn("texto de la página web, no de un documento", ip.section_body(text, "Texto completo"))
        for line in ('title: "Invented Secrets Report 2031 | Example Org"', "source: web", "source_type: gris",
                     "fetched_at: 2031-06-01", f"url: \"{URL}\"", "year: 2031", "venue: \"Example Org Research\""):
            self.assertIn(line, text)
        self.assertIn("A made-up annual report on invented secrets", ip.section_body(text, "Resumen"))
        self.assertIn("(página web, description;", ip.section_body(text, "Resumen"))
        body = ip.section_body(text, "Texto completo")
        self.assertIn("imaginary tool keeps encrypted values", body)
        self.assertNotIn("Pricing", body)
        self.assertIn(f"URL: {URL} (consultada 2031-06-01)", ip.section_body(text, "Referencia"))
        manifest = json.loads((self.vault / "Papers" / "_fuentes" / "P-0001" / "fuentes.json").read_text("utf-8"))
        self.assertEqual([(f["file"], f["parser"]) for f in manifest["files"]], [("pagina.html", "web")])
        code, out = self.run_cli("verify", "--vault", str(self.vault))
        self.assertEqual(out["counts"], {"ok": 1}, out)

    def test_an_edited_summary_or_text_is_caught(self):
        self.add_url()
        p = self.note()
        p.write_text(p.read_text("utf-8").replace("made-up annual report", "genuine annual report"), "utf-8")
        _, out = self.run_cli("verify", "--vault", str(self.vault))
        self.assertIn("## Resumen no coincide con el abstract del registro guardado", out["notes"][0]["problems"])
        self.add_url()                                           # the same page again: no duplicate
        self.assertEqual(len(list((self.vault / "Papers").glob("P-*.md"))), 1)
        p.write_text(p.read_text("utf-8").replace("genuine annual report", "made-up annual report")
                     .replace("why none of this is real", "why all of this is real", 1), "utf-8")
        _, out = self.run_cli("verify", "--vault", str(self.vault))
        self.assertEqual(out["notes"][0]["problems"], ["## Texto completo no coincide con el texto de la página guardada"])

    def test_no_fulltext_is_abstract_only_and_still_verifies(self):
        code, out = self.add_url(URL, None, "--no-fulltext")
        self.assertEqual((code, out["fulltext"]), (0, "abstract-only"))
        self.assertEqual(ip.section_body(self.note().read_text("utf-8"), "Texto completo"), ip.NO_FULLTEXT)
        _, out = self.run_cli("verify", "--vault", str(self.vault))
        self.assertEqual(out["counts"], {"ok": 1}, out)

    def test_rebuild_fetches_the_page_again(self):
        self.add_url()
        newer = PAGE.replace(b"A made-up annual report", b"A revised made-up annual report")
        code, out = self.run_cli("rebuild", "--vault", str(self.vault), "--only", "P-0001",
                                 fetch=fetch_serving({URL: newer}))
        self.assertEqual(out["notes"][0]["status"], "rebuilt", out)
        self.assertIn("A revised made-up annual report", ip.section_body(self.note().read_text("utf-8"), "Resumen"))
        _, out = self.run_cli("verify", "--vault", str(self.vault))
        self.assertEqual(out["counts"], {"ok": 1}, out)

    def test_refusals(self):
        with_doi = PAGE.replace(b"</head>", b'<meta name="citation_doi" content="10.9999/invented.1"></head>')
        no_title = (b"<html><head></head><body><p>" + PARA.encode() * 3 + b"</p></body></html>")
        cases = [
            ("http://192.168.1.20/internal", {}, "privada o local"),
            ("http://localhost/x", {}, "host local"),
            (URL, {URL: with_doi}, "--doi 10.9999/invented.1"),
            (URL, {URL: no_title}, "no tiene título"),
            (URL, {URL: b"%PDF-1.7 invented"}, "PDF suelto"),
            (URL, {}, "no se sirvió"),
        ]
        for url, pages, why in cases:
            code, out = self.add_url(url, pages)
            self.assertEqual(code, 2, (url, out))
            self.assertIn(why, json.dumps(out, ensure_ascii=False))
        self.assertEqual(list((self.vault / "Papers").glob("P-*.md")), [])

    def test_url_is_exclusive_with_other_ids(self):
        code, out = self.add("--url", URL, "--doi", "10.9999/x", fetch=fetch_serving({URL: PAGE}))
        self.assertEqual(code, 2, out)

    def test_bibliography_entry_is_a_web_page(self):
        self.add_url()
        fm = ip.vn.split_frontmatter(self.note().read_text("utf-8"))[0]
        m = export_bib.note_meta(fm)
        bib = export_bib.bibtex_entry(m, "ficticia2031invented")
        self.assertTrue(bib.startswith("@misc{ficticia2031invented,"), bib)
        for field in (f"howpublished = {{\\url{{{URL}}}}}", "organization = {Example Org Research}",
                      "note = {Accedido el 2031-06-01}"):
            self.assertIn(field, bib)
        self.assertNotIn("journal", bib)
        item = export_bib.csl_item(m, "k")
        self.assertEqual((item["type"], item["URL"], item["accessed"]),
                         ("webpage", URL, {"date-parts": [[2031, 6, 1]]}))


PDF_URL = "https://reports.example.org/2031/invented-secrets.pdf"
PDF_TEXT_LINES = ("1 Introduction\nThis invented paper studies imaginary encrypted values in made-up repositories "
                  "and reports an invented rate of 4.2% across them.\n" * 30)
WITH_PDF = PAGE.replace(b"</head>", b'<meta name="citation_pdf_url" content="/2031/invented-secrets.pdf"></head>')


class LinkedPdf(Base):
    def setUp(self):
        super().setUp()
        self.orig_pdf = ip.vf.pdf_bytes_to_text
        ip.vf.pdf_bytes_to_text = lambda data: PDF_TEXT_LINES if data.startswith(b"%PDF") else None

    def tearDown(self):
        ip.vf.pdf_bytes_to_text = self.orig_pdf
        super().tearDown()

    def test_the_linked_pdf_is_the_full_text_and_verifies(self):
        pages = {URL: WITH_PDF, PDF_URL: b"%PDF-1.7 invented paper"}
        code, out = self.add("--url", URL, fetch=fetch_serving(pages))
        self.assertEqual((code, out["fulltext"]), (0, "pdf"), out)
        text = self.note().read_text(encoding="utf-8")
        self.assertIn("fulltext: full", text)
        self.assertIn(f'pdf: "{PDF_URL}"', text)
        self.assertIn("invented rate of 4.2%", ip.section_body(text, "Texto completo"))
        self.assertIn("A made-up annual report", ip.section_body(text, "Resumen"))      # still the page's
        manifest = json.loads((self.vault / "Papers" / "_fuentes" / "P-0001" / "fuentes.json").read_text("utf-8"))
        roles = {f["file"]: (f["role"], f.get("fulltext")) for f in manifest["files"]}
        self.assertEqual(roles, {"pagina.html": ("metadata", False), "texto.pdf": ("fulltext", None)})
        self.assertEqual(self.run_cli("verify", "--vault", str(self.vault))[1]["counts"], {"ok": 1})
        _, out = self.run_cli("reconvert", "--vault", str(self.vault))
        self.assertEqual((out["notes"][0]["status"], out["notes"][0]["changed"]), ("reconverted", False), out)
        _, out = self.run_cli("rebuild", "--vault", str(self.vault), "--only", "P-0001", fetch=fetch_serving(pages))
        self.assertEqual((out["notes"][0]["status"], out["notes"][0]["fulltext"]), ("rebuilt", "pdf"), out)
        self.assertEqual(self.run_cli("verify", "--vault", str(self.vault))[1]["counts"], {"ok": 1})

    def test_a_blocked_pdf_falls_back_to_the_page_text(self):
        blocked = {URL: WITH_PDF, PDF_URL: b"<!doctype html><title>Checking your browser</title>"}
        code, out = self.add("--url", URL, fetch=fetch_serving(blocked))
        self.assertEqual((code, out["fulltext"]), (0, "pagina"), out)
        self.assertTrue(any("no se fuerza" in w for w in out["warnings"]), out["warnings"])
        text = self.note().read_text(encoding="utf-8")
        self.assertIn("fulltext: pagina", text)
        self.assertIn("imaginary tool keeps encrypted values", ip.section_body(text, "Texto completo"))
        self.assertEqual(self.run_cli("verify", "--vault", str(self.vault))[1]["counts"], {"ok": 1})

    def test_a_private_pdf_url_is_never_fetched(self):
        asked = []
        page = PAGE.replace(b"</head>", b'<meta name="citation_pdf_url" content="http://10.0.0.7/p.pdf"></head>')

        def fetch(url, headers):
            asked.append(url)
            return fetch_serving({URL: page})(url, headers)
        code, out = self.add("--url", URL, fetch=fetch)
        self.assertEqual((code, out["fulltext"]), (0, "pagina"), out)
        self.assertEqual(asked, [URL])
