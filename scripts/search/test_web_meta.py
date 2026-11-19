"""Tests for web_meta.py (invented pages only)."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import web_meta  # noqa: E402

SCHOLARLY = b"""<!doctype html><html><head><meta charset="utf-8">
<title>Ignored page title</title>
<meta name="citation_title" content="Measuring Invented Things in Imaginary Repositories">
<meta name="citation_author" content="Ada Inventada">
<meta name="citation_author" content="Bo Ficticio">
<meta name="citation_publication_date" content="2031/04/07">
<meta name="citation_doi" content="10.9999/INVENT.2031.1">
<meta name="citation_conference_title" content="Imaginary Security Symposium">
<meta name="citation_pdf_url" content="/paper/1.pdf">
<meta name="citation_abstract" content="We measure invented things across imaginary repositories and find that most of them are made up.">
</head><body><nav>menu menu menu menu menu menu menu menu</nav><p>Body text.</p></body></html>"""

BLOG = b"""<html><head><title>  A blog post  about invented secrets </title>
<meta property="og:site_name" content="Example Blog">
<meta property="article:published_time" content="2030-12-01T10:00:00Z">
<script>var x = "not text not text not text not text not text";</script></head>
<body><header>site header words words words words words words words</header>
<p>This first paragraph explains, at some length, how the invented tool stores secrets in a repository.</p>
<p>short</p>
<p>A second paragraph adds more invented detail about keys and recipients in the files.</p>
<footer>footer words words words words words words words words</footer></body></html>"""


def test_scholarly_tags_win():
    m = web_meta.parse(SCHOLARLY, "https://example.org/paper/1")
    assert m["kind"] == "html"
    assert m["title"] == "Measuring Invented Things in Imaginary Repositories"
    assert m["authors"] == ["Ada Inventada", "Bo Ficticio"]
    assert (m["date"], m["year"]) == ("2031-04-07", 2031)
    assert m["doi"] == "10.9999/invent.2031.1"
    assert m["venue"] == "Imaginary Security Symposium"
    assert m["pdf_url"] == "https://example.org/paper/1.pdf"
    assert m["abstract"].startswith("We measure") and m["abstract_from"] == "citation_abstract"


def test_blog_falls_back_to_paragraphs_and_drops_chrome():
    m = web_meta.parse(BLOG, "https://www.blog.example.com/post")
    assert m["title"] == "A blog post about invented secrets"
    assert m["date"] == "2030-12-01" and m["venue"] == "Example Blog"
    assert m["abstract_from"] == "parrafos" and m["abstract"].startswith("This first paragraph")
    assert "second paragraph" in m["abstract"]
    for noise in ("not text", "site header", "footer words"):
        assert noise not in m["text"]


def test_pdf_and_arxiv_url():
    m = web_meta.parse(b"%PDF-1.7 ...", "https://arxiv.org/pdf/2101.00001")
    assert m["kind"] == "pdf" and m["arxiv"] == "2101.00001" and m["title"] == ""


def test_public_url():
    ok = ["https://eprint.example.org/2031/123", "http://blog.example.com/x"]
    bad = ["file:///etc/passwd", "http://localhost:8080/", "http://127.0.0.1/", "http://10.0.0.5/x",
           "http://192.168.1.1/", "http://169.254.169.254/latest", "https://user:pw@example.com/",
           "http://intranet/", "http://printer.local/", "ftp://example.com/f"]
    assert all(web_meta.public_url(u) is None for u in ok)
    assert all(web_meta.public_url(u) for u in bad)


def test_article_content_wins_over_site_chrome_and_generic_descriptions():
    page = (b'<html><head><title>invented/tool</title><meta property="og:description" '
            b'content="Contribute to invented/tool development by creating an account on GitHub."></head><body>'
            b"<div>You signed in with another tab or window. Reload to refresh your session, please now.</div>"
            b"<article><p>The invented tool encrypts toy secrets with imaginary recipients and stores them in git.</p>"
            b"</article></body></html>")
    m = web_meta.parse(page, "https://github.com/invented/tool")
    assert m["abstract_from"] == "parrafos"
    assert m["abstract"].startswith("The invented tool") and "signed in" not in m["text"]


def test_pdf_meta_without_poppler_is_empty(monkeypatch):
    monkeypatch.setattr(web_meta, "_run", lambda *a, **k: None)
    m = web_meta.parse(b"%PDF-1.7 invented", "https://example.org/x.pdf")
    assert m["kind"] == "pdf" and m["title"] == "" and m["abstract"] == ""


def test_pdf_meta_reads_title_and_abstract(monkeypatch):
    text = ("Invented Workshop 2031\nA Study of Invented Secrets in Imaginary Repositories\nAda Inventada\n\n"
            "Abstract\nWe study invented secrets across imaginary repositories and report made-up numbers.\n\n"
            "1 Introduction\nBody.")
    monkeypatch.setattr(web_meta, "_run", lambda args, data, to_stdout=True:
                        text if args[0] == "pdftotext" else "Title:          paper.pdf\n")
    m = web_meta.parse(b"%PDF-1.7 invented", "https://example.org/x.pdf")
    assert m["title"] == "A Study of Invented Secrets in Imaginary Repositories"
    assert m["abstract"].startswith("We study invented secrets") and "Body" not in m["abstract"]
    assert m["abstract_from"] == "pdf-abstract"
