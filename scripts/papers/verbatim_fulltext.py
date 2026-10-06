#!/usr/bin/env python3
"""Build a Papers/ note's `## Texto completo` from the paper's real text.

The note's source fields hold only text fetched from the paper (create-project
step 5). This script fetches the source and converts it **verbatim**, organised
by the paper's own section / figure / table / appendix numbering:

  1. arXiv HTML (https://arxiv.org/html/<id>v<N>, LaTeXML), else
  2. ar5iv (https://ar5iv.labs.arxiv.org/html/<id>), else
  3. the arXiv PDF, through `pdftotext`.

Nothing is paraphrased or reconstructed:
  - HTML: text copied as rendered; math is the source's own LaTeX (`alttext`),
    as `$…$`; tables as `| cell | … |` rows; footnotes inline as
    `[nota al pie: …]`; text a reader never sees (white, display:none, zero
    size or opacity — the usual carrier of a prompt injection) is kept inside
    `[texto oculto en la fuente: …]`; a cell spanning columns or rows is repeated in each
    position it covers, so every column stays aligned with its header. Math with
    no LaTeX, and LaTeXML error nodes, become
    `[extracción dañada]`. The abstract (already in `## Resumen`), front
    matter, navigation and bibliography are left out.
  - PDF: prose lines as extracted (ligature glyphs → plain letters). Headings
    numbered "3.2", "3.2.", Roman "II." with lettered "A." subsections, and
    appendices; the text starts at the introduction and stops at
    References / REFERENCES. A table's rows stay under its caption, verbatim
    in pdftotext's reading order (columns not verified). A prose line with
    garbled inline math keeps its words and gets
    `[extracción dañada: fórmulas en línea]`; a run of other symbol lines
    (equations, figure-internal labels) becomes one `[extracción dañada] (…)`.

Every fetch goes through scripts/citations/net.py (per-host spacing, retries).
ar5iv serves one snapshot with no version number: its text is recorded as of
an unknown version, never as the latest arXiv version.

The output starts with a `> Fuente:` line: URL, arXiv version, retrieval
date, the sha256 of the exact bytes fetched, and the conversion rules.

Usage:
    verbatim_fulltext.py --arxiv 2201.02177 [--out body.md] [--raw-dir DIR]
    verbatim_fulltext.py --html page.html --source-url URL [--out …]   (offline)
    verbatim_fulltext.py --pdf-text paper.txt --source-url URL [--out …]

Exit codes: 0 ok; 1 no source could be fetched or converted (the note stays
`fulltext: abstract-only`, `## Texto completo` "No disponible"); 2 bad input.
Standard library only (plus the `pdftotext` binary for the PDF path).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "citations"))
import net  # noqa: E402  (shared HTTP helper: spacing, retries, curl fallback)

__version__ = "1.3.0"   # 1.3.0: visually hidden text marked; 1.2.0: spanned table cells repeated (columns aligned); 1.1.0: Roman PDF headings, …
TOOL_ID = f"kairo/verbatim_fulltext@{__version__}"
DAMAGED = "[extracción dañada]"
# A paper with no numbered sections (letter format) is kept whole under this heading.
UNSECTIONED_HEADING = "Texto (sin secciones numeradas en la fuente)"
UNSECTIONED_MIN_WORDS = 800
UA = {"User-Agent": f"Mozilla/5.0 ({TOOL_ID}; research use)"}

# --------------------------------------------------------------------------
# HTML (LaTeXML) → verbatim body
# --------------------------------------------------------------------------

VOID = {"br", "img", "hr", "meta", "link", "input", "col", "area", "base", "wbr", "source"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.children, self.parent = tag, dict(attrs), [], parent

    @property
    def cls(self) -> str:
        return self.attrs.get("class", "") or ""

    def has(self, c: str) -> bool:
        return c in self.cls.split()


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root", [], None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self.cur)
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def norm(s: str) -> str:
    return re.sub(r"[ \t\r\n]+", " ", s.replace(" ", " ")).strip()


HIDDEN = "[texto oculto en la fuente: {}]"
_HIDDEN_STYLE = re.compile(r"display:none|visibility:hidden|opacity:0(?:\.0+)?(?:;|$)"
                           r"|font-size:0(?:\.0+)?(?:px|pt|em|rem|%)?(?:;|$)"
                           r"|(?<![-\w])color:(?:#fff(?:fff)?|white|rgb\(255,255,255\))(?:;|$)")


def hidden(n) -> bool:
    """A node a reader never sees: white text, display:none, zero size or opacity.
    Its text is kept — verbatim is verbatim — but inside HIDDEN, so no reader
    (human or model) takes it for the paper's visible prose."""
    style = (n.attrs.get("style") or "").lower().replace(" ", "")
    return bool(style and _HIDDEN_STYLE.search(style))


def text_of(n) -> str:
    """Inline text of a node, verbatim, with math as $alttext$."""
    if isinstance(n, str):
        return n
    if n.tag in ("script", "style"):
        return ""
    if hidden(n):
        inner = norm("".join(text_of(c) for c in n.children))
        return f" {HIDDEN.format(inner)} " if inner else ""
    if n.tag == "math":
        alt = n.attrs.get("alttext")
        return f"${alt}$" if alt else DAMAGED
    if n.has("ltx_ERROR") or (n.has("ltx_math_unparsed") and not n.attrs.get("alttext")):
        return DAMAGED
    if n.has("ltx_note_mark") or n.has("ltx_note_type"):
        return ""
    if n.has("ltx_note_outer") or n.has("ltx_note"):
        inner = norm("".join(text_of(c) for c in n.children))
        # LaTeXML nests ltx_note_outer inside ltx_note: mark the footnote once
        p = n.parent
        while p is not None and not (hasattr(p, "has") and (p.has("ltx_note") or p.has("ltx_note_outer"))):
            p = getattr(p, "parent", None)
        if p is not None:
            return inner
        return f" [nota al pie: {inner}]" if inner else ""
    if n.tag == "img" or n.has("ltx_graphics"):
        return ""
    return "".join(text_of(c) for c in n.children)


def _iter(n, pred):
    for c in n.children:
        if isinstance(c, str):
            continue
        if pred(c):
            yield c
        yield from _iter(c, pred)


def _title(h) -> tuple[str, str]:
    tag, rest = "", []
    for c in h.children:
        if not isinstance(c, str) and c.has("ltx_tag"):
            tag = norm(text_of(c))
        else:
            rest.append(text_of(c))
    return tag, norm("".join(rest))


def _owner_figure(n):
    p = n.parent
    while p is not None and p.tag != "figure":
        p = p.parent
    return p


def _span(td, attr: str) -> int:
    try:
        return max(1, min(int(td.attrs.get(attr) or 1), 50))
    except ValueError:
        return 1


def _fill_carried(cells: list[str], col: int, carry: dict[int, list]) -> int:
    """Put the cells that rows above span into (rowspan) from column `col` on; the next free column."""
    while col in carry:
        text, left = carry[col]
        cells.append(text)
        if left <= 1:
            del carry[col]
        else:
            carry[col][1] = left - 1
        col += 1
    return col


def _table(t, out: list[str]) -> None:
    """Rows as `| … |`. A cell spanning columns (colspan) or rows (rowspan) is
    repeated in every position it covers, so each column keeps its own header
    and a number never shifts into the next column (multi-level headers)."""
    rows = []
    carry: dict[int, list] = {}                      # column → [text, rows still covered]
    for tr in _iter(t, lambda x: x.tag == "tr"):
        tds = [td for td in tr.children if not isinstance(td, str) and td.tag in ("td", "th")]
        cells: list[str] = []
        col = 0
        for td in tds:
            col = _fill_carried(cells, col, carry)
            text = norm(text_of(td))
            for _ in range(_span(td, "colspan")):
                cells.append(text)
                if _span(td, "rowspan") > 1:
                    carry[col] = [text, _span(td, "rowspan") - 1]
                col += 1
        _fill_carried(cells, col, carry)
        if any(cells):
            rows.append("| " + " | ".join(cells) + " |")
    out.append("\n".join(rows) if rows else DAMAGED + " (tabla)")


def _figure(f, out: list[str]) -> None:
    is_table = f.has("ltx_table")
    subs = list(_iter(f, lambda x: x.tag == "figure"))
    for s in subs:   # sub-figure captions, or numbered figures nested in a wrapper
        for cap in _iter(s, lambda x, s=s: x.tag == "figcaption" and _owner_figure(x) is s):
            tag, rest = _title(cap)
            tag = tag.rstrip(": ")
            if re.match(r"(Figure|Table|Fig\.)\s*\d", tag):
                out.append(f"**{tag}:** {rest}")
            elif norm(text_of(cap)):
                out.append(f"**Subfigura:** {norm(text_of(cap))}")
    for cap in _iter(f, lambda x: x.tag == "figcaption" and _owner_figure(x) is f):
        tag, rest = _title(cap)
        tag = tag.rstrip(": ")
        out.append(f"**{tag or ('Table' if is_table else 'Figure')}:** {rest}")
    if is_table:
        for t in _iter(f, lambda x: x.tag == "table" and x.has("ltx_tabular")
                       and _owner_figure(x) is f):
            _table(t, out)
    for s in subs:
        if s.has("ltx_table"):
            for t in _iter(s, lambda x: x.tag == "table" and x.has("ltx_tabular")):
                _table(t, out)


def _equation(t, out: list[str]) -> None:
    parts, tag = [], ""
    for td in _iter(t, lambda x: x.tag == "td"):
        if td.has("ltx_eqn_eqno"):
            tag = norm(text_of(td))
        elif norm(text_of(td)):
            parts.append(norm(text_of(td)))
    out.append(f"$$ {' '.join(parts) or DAMAGED} $${' ' + tag if tag else ''}")


_HEAD = {"ltx_title_section": "###", "ltx_title_appendix": "###",
         "ltx_title_subsection": "####", "ltx_title_subsubsection": "#####"}
_SKIP = ("ltx_bibliography", "ltx_page_navbar", "ltx_abstract", "ltx_authors", "ltx_dates",
         "ltx_page_footer", "ltx_page_header")


def _walk(n, out: list[str]) -> None:
    for c in n.children:
        if isinstance(c, str):
            continue
        if any(c.has(k) for k in _SKIP) or c.tag in ("nav", "header", "footer", "script", "style"):
            continue
        if hidden(c):
            if norm(text_of(c)):
                out.append(norm(text_of(c)))
            continue
        if c.tag == "h1" and c.has("ltx_title_document"):
            continue
        if c.tag in ("h2", "h3", "h4", "h5", "h6") and "ltx_title" in c.cls:
            tag, rest = _title(c)
            kind = next((k for k in _HEAD if c.has(k)), None)
            if kind and c.has("ltx_title_appendix"):
                m = re.match(r"(?:Appendix\s+)?([A-Z])\b", tag)
                out.append(f"### Appendix {m.group(1)}: {rest}" if m else f"### {norm(tag + ' ' + rest)}")
            elif kind:
                out.append(f"{_HEAD[kind]} {norm(tag + ' ' + rest)}")
            else:                       # run-in paragraph title: bold, keeps section context
                out.append(f"**{norm(tag + ' ' + rest)}**")
            continue
        if c.tag == "figure" and (c.has("ltx_figure") or c.has("ltx_table") or c.has("ltx_float")):
            _figure(c, out)
            continue
        if c.tag == "table" and (c.has("ltx_equation") or c.has("ltx_equationgroup")
                                 or c.has("ltx_eqn_table")):
            _equation(c, out)
            continue
        if c.tag == "table" and c.has("ltx_tabular"):
            _table(c, out)
            continue
        if c.tag == "li" or c.has("ltx_item"):
            tag = next((norm(text_of(k)) for k in c.children
                        if not isinstance(k, str) and k.has("ltx_tag")), "")
            sub: list[str] = []
            _walk(c, sub)
            body = [b for b in sub if b != tag]
            if body:
                lead_tag = f"{tag} " if tag and tag not in ("•", "-", "–") else ""
                out.append(f"- {lead_tag}{body[0]}")
                out.extend(body[1:])
            continue
        if c.tag == "p" or c.has("ltx_p"):
            buf: list[str] = []
            for k in c.children:
                if not isinstance(k, str) and k.tag in ("table", "figure", "ul", "ol"):
                    if norm("".join(buf)):
                        out.append(norm("".join(buf)))
                    buf = []
                    wrapper = Node("div", [], None)
                    wrapper.children = [k]
                    _walk(wrapper, out)
                else:
                    buf.append(text_of(k))
            if norm("".join(buf)):
                out.append(norm("".join(buf)))
            continue
        if c.has("ltx_tag") and c.parent is not None and c.parent.has("ltx_item"):
            continue
        _walk(c, out)


def html_to_body(html_text: str) -> str:
    tree = _Tree()
    tree.feed(html_text)
    raw: list[str] = []
    art = next(_iter(tree.root, lambda x: x.tag == "article" or x.has("ltx_document")), tree.root)
    _walk(art, raw)
    blocks: list[str] = []
    seen_section = False
    for b in (b.rstrip() for b in raw):
        if not b.strip():
            continue
        if b.startswith("### "):
            seen_section = True
        if not seen_section and not re.match(r"\*\*(Figure|Table|Subfigura)", b):
            continue          # front matter before the first section: only captions kept
        if blocks and blocks[-1] == b:
            continue
        blocks.append(b)
    return "\n\n".join(blocks) + "\n"


# --------------------------------------------------------------------------
# PDF text (pdftotext) → verbatim body
# --------------------------------------------------------------------------

_LIG = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl"}
_PDF_HEAD = re.compile(r"^((?:\d+(?:\.\d+)*)|(?:[A-Z](?:\.\d+)*))\s+([A-Z][^.]{2,85})$")
# "1. Introduction": the same numbering with a period after the number
_PDF_HEAD_DOT = re.compile(r"^(\d+(?:\.\d+)*)\.\s+([A-Z][^.]{2,85})$")
# Physics / IEEE style: "II. METHODS", lettered subsections "A. Decoders"
_PDF_ROMAN = re.compile(r"^((?:X{0,3})(?:IX|IV|V?I{1,3}|V))\.\s+([A-Z][^.]{2,85})$")
_PDF_LETTER = re.compile(r"^([A-Z])\.\s+([A-Z][^.]{2,85})$")
_PDF_INTRO = re.compile(r"^(?:(?:1|I)\.?\s+)?INTRODUCTION$", re.IGNORECASE)
_PDF_REFS = re.compile(r"^(?:References|Bibliography|REFERENCES|BIBLIOGRAPHY)$")
_PDF_TABLE_NOTE = "[filas de la tabla tal como las extrae pdftotext, en su orden; columnas no verificadas]"
_ROMANS = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII", "XIV", "XV",
           "XVI", "XVII", "XVIII", "XIX", "XX"]


def _lig(s: str) -> str:
    for k, v in _LIG.items():
        s = s.replace(k, v)
    return s


def _is_prose(line: str) -> bool:
    words = re.findall(r"[A-Za-z]{3,}", line)
    toks = line.split()
    if not toks or len(words) / len(toks) < 0.55:
        return False
    return len(words) >= 8 or (len(words) >= 5 and line.rstrip()[-1:] in ".,;:)")


def _title_ok(title: str) -> bool:
    if re.search(r"[=⊙∑∫≤≥→]", title) or title.count("(") > 2:
        return False
    return len(re.findall(r"[A-Za-z]{4,}", title)) >= 1


def _num_heading(num: str, title: str) -> str:
    return ("###", "####", "#####", "#####")[min(num.count("."), 3)] + f" {num} {title}"


def _pdf_heading(s: str, st: dict) -> str | None:
    """The `###` heading a pdftotext line opens, or None. Numbered ("3.2 Title",
    "3.2. Title"), Roman ("II. METHODS") with lettered subsections ("A. Decoders"),
    and appendices ("A Title", "Appendix A: Title"); Roman, lettered and dotted
    numbers must follow on from the previous one, so an enumeration is not a heading."""
    if s.endswith(".") or len(s) >= 95:
        return None
    m = re.match(r"^(?:APPENDIX|Appendix)\s+([A-Z])(?:[:.]\s*|\s+)(.*)$", s)
    if m and (not st["app"] or m.group(1) >= st["app"]):
        st.update(app=m.group(1), roman=st["roman"], in_roman=False)
        return f"### Appendix {m.group(1)}: {m.group(2)}".rstrip(": ")
    m = _PDF_ROMAN.match(s)
    if m and _title_ok(m.group(2)) and st["roman"] < len(_ROMANS) and m.group(1) == _ROMANS[st["roman"]]:
        st["roman"] += 1
        st.update(letter="", in_roman=True)
        return f"### {m.group(1)}. {m.group(2)}"
    if st["in_roman"]:
        m = _PDF_LETTER.match(s)
        nxt = chr(ord(st["letter"]) + 1) if st["letter"] else "A"
        if m and _title_ok(m.group(2)) and m.group(1) == nxt:
            st["letter"] = nxt
            return f"#### {nxt}. {m.group(2)}"
    m = _PDF_HEAD_DOT.match(s)
    if m and _title_ok(m.group(2)):
        parts = m.group(1).split(".")
        top = int(parts[0])
        if (len(parts) == 1 and top == st["top"] + 1) or (len(parts) > 1 and top == st["top"]):
            st["top"] = top
            return _num_heading(m.group(1), m.group(2))
    m = _PDF_HEAD.match(s)
    if m and m.group(1)[0].isalpha() and st["app"] and m.group(1)[0] < st["app"]:
        m = None                                   # appendix letters only move forward
    if m and _title_ok(m.group(2)):
        num, title = m.group(1), m.group(2)
        if num[0].isalpha():
            st.update(app=num[0], in_roman=False)
            if re.fullmatch(r"[A-Z]", num):
                return f"### Appendix {num}: {title}"
        else:
            st["top"] = int(num.split(".")[0])
        return _num_heading(num, title)
    return None


def _caption(s: str) -> tuple[str, str] | None:
    """(kind, label line) for a figure / table caption. "FIG. 2." and "TABLE II."
    (physics style) keep their own label next to the arabic number locators use."""
    m = re.match(r"^(Figure|Fig\.|FIG\.|Table|TABLE)\s+(\d+|[IVX]+)[:.]\s*(.*)$", s)
    if not m:
        return None
    word, num, rest = m.groups()
    kind = "Table" if word.lower().startswith("tab") else "Figure"
    if num.isdigit():
        n, own = num, ("" if word in ("Figure", "Table") else f" ({word} {num})")
    elif num in _ROMANS:
        n, own = str(_ROMANS.index(num) + 1), f" ({word} {num})"
    else:
        return None
    return kind, f"**{kind} {n}{own}:** {rest}"


def pdftext_to_body(txt: str, start: str | None = None) -> str:
    """pdftotext output → verbatim body. The text starts at the introduction:
    `start` names its exact line, else the first "1 Introduction" /
    "1. Introduction" / "I. INTRODUCTION" / "Introduction" line, else the top."""
    lines = [_lig(l.rstrip()) for l in txt.replace("\f", "\n").split("\n")]
    if start is not None:
        i0 = next((i for i, l in enumerate(lines) if l.strip() == start), 0)
    else:
        i0 = next((i for i, l in enumerate(lines) if _PDF_INTRO.match(l.strip())), 0)
    out: list[str] = []
    damaged_run = in_refs = False
    st = {"app": "", "roman": 0, "letter": "", "in_roman": False, "top": 0}
    table_rows = -1                                  # -1: not inside a table; else rows kept
    for l in lines[i0:]:
        s = l.strip()
        if not s or re.fullmatch(r"\d{1,3}", s) or s.startswith("arXiv:"):
            continue
        if _PDF_REFS.fullmatch(s):
            in_refs = True
            table_rows = -1
            continue
        if not in_refs and _PDF_INTRO.match(s) and not out:
            # an unnumbered "Introduction" opens the body as its own heading
            if not re.match(r"^(?:1|I)\.?\s", s):
                out.append(f"### {s}")
                continue
        head = _pdf_heading(s, st)
        if head:
            in_refs = False
            table_rows = -1
            out.append(head)
            damaged_run = False
            continue
        if in_refs:
            continue
        cap = _caption(s)
        if cap:
            out.append(cap[1])
            table_rows = 0 if cap[0] == "Table" else -1
            damaged_run = False
            continue
        prose = _is_prose(s)
        if table_rows >= 0 and not prose and table_rows < 80:
            # the rows of a table, kept verbatim in pdftotext's reading order under
            # its caption, so a "Tabla N" locator reaches its numbers
            out[-1] += ("\n" + _PDF_TABLE_NOTE if table_rows == 0 else "") + "\n" + s
            table_rows += 1
            continue
        in_table_text = table_rows > 0
        table_rows = -1
        if prose:
            prev = out[-1] if out else ""
            if (prev and not prev.startswith("#") and not prev.startswith(DAMAGED) and not in_table_text
                    and _PDF_TABLE_NOTE not in prev
                    and prev.rstrip()[-1:] not in ".?!" and s[:1].islower()):
                out[-1] = prev.rstrip() + " " + s      # the same paragraph / caption, wrapped
            else:
                out.append(s)
            damaged_run = False
        elif len(re.findall(r"[A-Za-z]{3,}", s)) >= 8:
            out.append(s + " [extracción dañada: fórmulas en línea]")
            damaged_run = False
        elif not damaged_run:
            out.append(DAMAGED + " (ecuación, tabla o texto interno de figura)")
            damaged_run = True
    return "\n\n".join(out) + "\n"


# --------------------------------------------------------------------------
# Fetching and the Fuente line
# --------------------------------------------------------------------------

MAX_SOURCE_BYTES = 80 * 1024 * 1024


def _get(url: str) -> tuple[int | None, bytes]:
    """(status, body) through the shared HTTP helper: per-host spacing, 3 attempts
    on 406/429/5xx with backoff, curl fallback for arXiv. A final failure is
    (code or None, b"")."""
    try:
        return 200, net.get(url, headers={"User-Agent": UA["User-Agent"]}, timeout=90,
                            max_bytes=MAX_SOURCE_BYTES)
    except net.HttpError as e:
        return e.code, b""


def latest_version(abs_page: bytes) -> str:
    vs = re.findall(rb"\[v(\d+)\]", abs_page)
    return f"v{max(int(x) for x in vs)}" if vs else ""


def fetch_arxiv(aid: str, pause: float = 3.0, version: str = "") -> dict | None:
    """First source that yields real text: arXiv HTML, ar5iv, PDF — of `version`
    when given ("v2"), else of the latest version. ar5iv serves one snapshot
    with no version number, so its text is recorded as of an unknown version."""
    ver = version
    if not ver:
        _, abs_page = _get(f"https://arxiv.org/abs/{aid}")
        ver = latest_version(abs_page)
    for kind, url in (("arxiv-html", f"https://arxiv.org/html/{aid}{ver}"),
                      ("ar5iv", f"https://ar5iv.labs.arxiv.org/html/{aid}"),
                      ("pdf", f"https://arxiv.org/pdf/{aid}{ver}")):
        if kind == "ar5iv" and version:
            continue                     # a pinned version cannot come from ar5iv's snapshot
        time.sleep(pause)
        st, body = _get(url)
        if st != 200:
            continue
        if kind == "pdf" and body[:4] == b"%PDF" or kind != "pdf" and b"ltx_section" in body:
            if kind == "ar5iv":
                shown = (f"desconocida: ar5iv no indica qué versión convirtió (la última en arXiv es {ver})"
                         if ver else "desconocida: ar5iv no indica qué versión convirtió")
            else:
                shown = ver or "?"
            return {"kind": kind, "url": url, "version": shown, "bytes": body}
    return None


def pdf_bytes_to_text(data: bytes) -> str | None:
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    with tempfile.TemporaryDirectory() as d:
        pdf, txt = Path(d) / "p.pdf", Path(d) / "p.txt"
        pdf.write_bytes(data)
        if subprocess.run([exe, "-enc", "UTF-8", str(pdf), str(txt)],
                          capture_output=True).returncode != 0:
            return None
        return txt.read_text(encoding="utf-8", errors="replace")


def fuente_line(url: str, version: str, kind: str, sha: str, date: str) -> str:
    how = {"arxiv-html": "HTML de arXiv (LaTeXML)", "ar5iv": "HTML de ar5iv (LaTeXML)",
           "pdf": ("PDF de arXiv" if "arxiv.org" in url else "PDF") + ", texto extraído con pdftotext"
           }.get(kind, kind)
    rules = ("ecuaciones como su LaTeX fuente ($…$); tablas como filas «| … |» (una celda que abarca varias "
             "columnas o filas se repite en cada una); notas al pie "
             "en línea; se omiten el abstract (en ## Resumen) y la bibliografía"
             if kind != "pdf" else
             "ligaduras tipográficas normalizadas a letras; las filas de cada tabla, tal como "
             "las extrae pdftotext, bajo su pie; se omiten el abstract (en ## Resumen) y la "
             "bibliografía")
    return (f"> Fuente: {url} ({how}, versión {version}), obtenido {date}; sha256 del "
            f"archivo descargado: {sha}.\n> Texto verbatim generado por {TOOL_ID}: {rules}; "
            f"«{DAMAGED}» marca lo que no se pudo extraer, sin reconstruirlo.")


def build_from_text(data: bytes, url: str, version: str, date: str) -> str | None:
    """--pdf-text: pdftotext output already on disk; the same rules as build()."""
    return _assemble("pdf", url, version, data, pdftext_to_body(data.decode("utf-8", errors="replace")), date)


def build(kind: str, url: str, version: str, data: bytes, date: str) -> str | None:
    if kind == "pdf":
        txt = pdf_bytes_to_text(data)
        if txt is None:
            return None
        body = pdftext_to_body(txt)
    else:
        body = html_to_body(data.decode("utf-8", errors="replace"))
    return _assemble(kind, url, version, data, body, date)


def _assemble(kind: str, url: str, version: str, data: bytes, body: str, date: str) -> str | None:
    sha = hashlib.sha256(data).hexdigest()
    if not re.search(r"^### ", body, re.M):
        # A letter-format paper (PRL, Nature Physics…) numbers no sections. Its text is
        # still the paper's own: kept verbatim under one heading that says so, so it is
        # cited by figure / table or as «§Texto». A short or empty extraction is refused.
        if len(body.split()) < UNSECTIONED_MIN_WORDS:
            return None                  # nothing usable recovered
        # without sections nothing can be cut off safely: say what stays in
        head = re.sub(r"; se omiten el abstract \(en ## Resumen\) y la bibliografía", "",
                      fuente_line(url, version, kind, sha, date))
        return (head
                + "\n> La fuente no numera secciones: el texto va entero, en el orden del documento,"
                  " bajo un solo encabezado, incluidos su cabecera, su resumen y su bibliografía."
                + f"\n\n### {UNSECTIONED_HEADING}\n\n" + body)
    return fuente_line(url, version, kind, sha, date) + "\n\n" + body


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(description="Verbatim ## Texto completo for a Papers/ note.")
    p.add_argument("--version", action="version", version=TOOL_ID)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--arxiv", help="arXiv id, e.g. 2201.02177")
    g.add_argument("--html", type=Path, help="an already-downloaded LaTeXML HTML file")
    g.add_argument("--pdf-text", type=Path, help="pdftotext output of the paper")
    p.add_argument("--source-url", help="URL the --html / --pdf-text came from")
    p.add_argument("--source-version", default="?")
    p.add_argument("--out", type=Path, help="write the block here (default stdout)")
    p.add_argument("--raw-dir", type=Path, help="also save the fetched bytes here")
    a = p.parse_args(argv)
    date = _dt.date.today().isoformat()
    if a.arxiv:
        got = fetch_arxiv(a.arxiv)
        if not got:
            print("error: no arXiv HTML, ar5iv or PDF could be fetched", file=sys.stderr)
            return 1
        if a.raw_dir:
            a.raw_dir.mkdir(parents=True, exist_ok=True)
            ext = "pdf" if got["kind"] == "pdf" else "html"
            (a.raw_dir / f"{a.arxiv}.{ext}").write_bytes(got["bytes"])
        block = build(got["kind"], got["url"], got["version"], got["bytes"], date)
    else:
        if not a.source_url:
            print("error: --source-url is required with --html / --pdf-text", file=sys.stderr)
            return 2
        src = a.html or a.pdf_text
        if not src.is_file():
            print(f"error: not found: {src}", file=sys.stderr)
            return 2
        data = src.read_bytes()
        if a.html:
            block = build("arxiv-html", a.source_url, a.source_version, data, date)
        else:
            block = build_from_text(data, a.source_url, a.source_version, date)
    if block is None:
        print("error: the source yielded no section-structured text", file=sys.stderr)
        return 1
    if a.out:
        a.out.write_text(block, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(block)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
