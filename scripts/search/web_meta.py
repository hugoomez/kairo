#!/usr/bin/env python3
"""Mechanical metadata of a web page or PDF fetched by a script: title, authors,
date, DOI, arXiv id, site and an abstract, read from the page's own tags.

    parse(data: bytes, url: str) -> dict
        {"title", "authors": [...], "date", "year", "doi", "arxiv", "venue",
         "abstract", "abstract_from", "kind": "html"|"pdf"|"other", "text"}

Order of preference, all from the bytes (never typed by a model):
  title     citation_title, dc.title, og:title, <title>
  authors   citation_author (each), dc.creator, author, article:author
  date      citation_publication_date, citation_date, citation_online_date,
            article:published_time, dc.date, date, pubdate
  doi       citation_doi, dc.identifier (10.…), a doi.org link in the URL
  arxiv     citation_arxiv_id, an arxiv.org/abs|pdf URL
  venue     citation_journal_title, citation_conference_title, og:site_name, host
  abstract  citation_abstract, dc.description, og:description, description,
            then the first paragraphs of the page (up to ABSTRACT_CHARS)
`text` is the page's readable text (scripts, styles, navigation dropped),
whitespace collapsed, for a full-text section. A PDF yields kind "pdf" and no
text (it is converted elsewhere). Standard library only.

`public_url(url)` refuses what a fetcher must never ask for: non-http(s)
schemes, credentials in the URL, and loopback / private / link-local hosts.
"""
from __future__ import annotations

import html
import ipaddress
import re
import urllib.parse
from html.parser import HTMLParser

ABSTRACT_CHARS = 1500
_SKIP = {"script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "button", "select"}
_BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "section", "article",
          "br", "tr", "td", "th", "dd", "dt"}
_DATE = re.compile(r"(\d{4})(?:[-/.](\d{1,2})(?:[-/.](\d{1,2}))?)?")
_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>]+)")
_ARXIV_URL = re.compile(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/[0-9]{7})")


def ws(s) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(s or ""))).strip()


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, list[str]] = {}
        self.title = ""
        self._in_title = False
        self._skip = 0
        self._paras: list[str] = []
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            name = (a.get("name") or a.get("property") or a.get("itemprop") or "").strip().lower()
            if name and a.get("content", "").strip():
                self.meta.setdefault(name, []).append(a["content"])
            return
        if tag == "title":
            self._in_title = True
        if tag in _SKIP:
            self._skip += 1
        if tag in _BLOCK:
            self._flush()

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in _SKIP and self._skip:
            self._skip -= 1
        if tag in _BLOCK:
            self._flush()

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if not self._skip:
            self._buf.append(data)

    def _flush(self):
        t = ws("".join(self._buf))
        self._buf = []
        if t:
            self._paras.append(t)

    def paragraphs(self) -> list[str]:
        self._flush()
        return self._paras


def _first(meta: dict, *names: str) -> str:
    for n in names:
        for v in meta.get(n, []):
            if ws(v):
                return ws(v)
    return ""


def norm_date(raw: str) -> tuple[str | None, int | None]:
    m = _DATE.search(raw or "")
    if not m:
        return None, None
    y = int(m.group(1))
    if not 1900 <= y <= 2100:
        return None, None
    if m.group(2) and m.group(3) and 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(3)) <= 31:
        return f"{y:04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", y
    return None, y


def host_of(url: str) -> str:
    h = (urllib.parse.urlsplit(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def public_url(url: str) -> str | None:
    """None if `url` may be fetched, else why not."""
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return "URL ilegible"
    if p.scheme not in ("http", "https"):
        return "solo http(s)"
    if p.username or p.password:
        return "URL con credenciales"
    h = (p.hostname or "").lower().rstrip(".")
    if not h or "." not in h and h != "localhost" and not h.startswith("["):
        return "host sin dominio"
    if h == "localhost" or h.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa")):
        return "host local"
    try:
        ip = ipaddress.ip_address(h.strip("[]"))
    except ValueError:
        return None
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast \
            or ip.is_unspecified:
        return "dirección privada o local"
    return None


def parse(data: bytes, url: str) -> dict:
    host = host_of(url)
    out = {"title": "", "authors": [], "date": None, "year": None, "doi": None, "arxiv": None,
           "venue": host or None, "abstract": "", "abstract_from": None, "kind": "other", "text": ""}
    am = _ARXIV_URL.search(url)
    if am:
        out["arxiv"] = am.group(1)
    dm = re.search(r"doi\.org/(10\.\d{4,9}/\S+)", url)
    if dm:
        out["doi"] = urllib.parse.unquote(dm.group(1)).rstrip(".,;)").lower()
    if data[:5] == b"%PDF-":
        out["kind"] = "pdf"
        return out
    head = data[:4096].lower()
    if b"<html" not in head and b"<!doctype html" not in head and b"<meta" not in head and b"<title" not in head:
        return out
    out["kind"] = "html"
    m = re.search(rb"charset=[\"']?([a-z0-9_\-]+)", head)
    enc = m.group(1).decode() if m else "utf-8"
    try:
        text = data.decode(enc, errors="replace")
    except LookupError:
        text = data.decode("utf-8", errors="replace")
    p = _Page()
    try:
        p.feed(text)
        p.close()
    except Exception:  # noqa: BLE001  a broken page still gives what was read
        pass
    meta = p.meta
    out["title"] = _first(meta, "citation_title", "dc.title", "og:title", "twitter:title") or ws(p.title)
    authors = [ws(a) for a in meta.get("citation_author", []) if ws(a)]
    if not authors:
        authors = [ws(a) for n in ("dc.creator", "author", "article:author") for a in meta.get(n, [])
                   if ws(a) and not ws(a).startswith("http")]
    out["authors"] = list(dict.fromkeys(authors))
    for n in ("citation_publication_date", "citation_date", "citation_online_date", "article:published_time",
              "dc.date", "date", "pubdate", "datepublished"):
        raw = _first(meta, n)
        if raw:
            out["date"], out["year"] = norm_date(raw)
            if out["year"]:
                break
    doi = _first(meta, "citation_doi", "dc.identifier", "prism.doi")
    dm2 = _DOI.search(doi)
    if dm2:
        out["doi"] = dm2.group(1).rstrip(".,;)").lower()
    arx = _first(meta, "citation_arxiv_id")
    if arx:
        out["arxiv"] = arx
    out["venue"] = (_first(meta, "citation_journal_title", "citation_conference_title", "og:site_name")
                    or host or None)
    paras = p.paragraphs()
    for n in ("citation_abstract", "dc.description", "og:description", "description", "twitter:description"):
        v = _first(meta, n)
        if len(v.split()) >= 8:
            out["abstract"], out["abstract_from"] = v, n
            break
    if not out["abstract"]:
        acc = []
        for t in paras:
            if len(t.split()) < 8:
                continue
            acc.append(t)
            if sum(len(x) for x in acc) >= ABSTRACT_CHARS:
                break
        if acc:
            out["abstract"], out["abstract_from"] = " ".join(acc)[:ABSTRACT_CHARS], "parrafos"
    out["text"] = "\n\n".join(paras)
    return out
