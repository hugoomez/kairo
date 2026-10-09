#!/usr/bin/env python3
"""One paper's card: which versions exist, where it was published, whether it
was retracted, who cites it, and its BibTeX — all from public records.

    paper_card.py (--arxiv ID | --doi DOI | --title "<title>") [--vault <vault>] [--citations 25] [--json]
                  [--raw-dir DIR]

Sources (no model involved, each answer kept with --raw-dir):
  - arXiv: the API entry (title, authors, journal_ref, declared DOI) and the
    abstract page's submission history — every version with its date; a paper
    asked for by DOI or title whose arXiv preprint OpenAlex or Crossref names
    gets the same arXiv record, versions and withdrawal check;
  - OpenAlex: the work (by DOI, and by the arXiv DOI 10.48550/arXiv.<id>): its
    locations (repository vs journal / conference), cited_by_count — when
    OpenAlex keeps the preprint and the published paper as two works, both are
    counted (`openalex_works`) and the citing list covers both (`cites:W1|W2`);
  - Crossref (a non-arXiv DOI): container title, issue date, and the
    `is-preprint-of` / `has-preprint` relations publishers register;
  - OpenReview: an accepted record with exactly this title and the same first
    author (ICLR, NeurIPS, ICML, MLSys, TMLR — venues with no DOI) gives the
    venue and its year; a close title by the same first author is only a
    candidate, and a workshop is shown, never the published version;
  - Semantic Scholar: citation count and the list of citing papers
    (the newest first, up to --citations; `--citing-order cited` lists the most
    cited ones instead, each with its own citation count); a page that does not come marks
    the list `citing_incomplete`;
  - the shared retraction / withdrawal check (Crossref + arXiv).

A preprint server or repository (Semantic Scholar files arXiv.org as a
"journal", OpenAlex and Crossref file SSRN Electronic Journal as one; Research
Square, TechRxiv, Preprints.org, bio/med/ChemRxiv, Zenodo, OSF) is never a
published version, whichever source names it. When no source names one for an
arXiv paper, Crossref's bibliographic search by title + first author is asked:
a close title with the same first author is listed under
`published_candidates`, to confirm — never in the BibTeX.

"Published version" lists every piece of evidence separately (arXiv
journal_ref / declared DOI, an OpenAlex journal or conference location, a
Crossref relation) and says which source said what; when none does, it says
"no consta", never guesses. With --vault, says whether the paper is already a
note (P-XXXX) and prints the ingest_paper.py command otherwise.

--title looks the paper up in OpenAlex: one work with exactly that title (case,
punctuation and accents aside; math and TeX keep their content) is the paper —
a preprint work and its published work linked by their arXiv id are one paper;
anything else lists the closest hits and exits 3, never guessing. When
OpenAlex has no work with that exact title (a preprint indexed days late),
arXiv's title search is asked the same question — one exact title there is the
paper (`found_by` says so, in the card too). The citing papers come from
OpenAlex, which sorts them by date itself, paged past 100 up to --citations;
Semantic Scholar is the fallback (it pages in its own order, so past 1000 the
card says it is the newest of the first 1000).

Prints Markdown (or one JSON object with --json). A source that failed — or
answered with something that is not its format, like an HTML maintenance page
— is named in the card, never silently left out; so is a BibTeX field no source
gave (author, year). Exit: 0 ok · 1 nothing found ·
2 bad input · 3 ambiguous title (candidates listed). Standard library only.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "citations"))
sys.path.insert(0, str(HERE.parent / "search"))
import export_bib  # noqa: E402
import lit_search  # noqa: E402
import net  # noqa: E402
import resolve_refs as rr  # noqa: E402
import retraction  # noqa: E402
from fill_abstract import from_jats  # noqa: E402

TOOL = "kairo/paper_card@1.6.0"
S2 = "https://api.semanticscholar.org/graph/v1"
# preprint servers and repositories (SSRN Electronic Journal, Research Square, TechRxiv,
# Preprints.org, bio/med/ChemRxiv, Zenodo, OSF …): where a preprint is, never a published
# version — whether Semantic Scholar, an OpenAlex location or a Crossref container names it
REPOSITORY = rr.REPOSITORY_VENUE
CANDIDATE_ROWS = 5
Fetch = Callable[[str, dict], bytes]


def default_fetch(url: str, headers: dict) -> bytes:
    return net.get(url, headers=headers)


def arxiv_versions(abs_html: str) -> list[dict]:
    """[v1] Mon, 1 Jan 2030 10:00:00 UTC (512 KB) → [{'version': 'v1', 'date': '2030-01-01'}]."""
    out = []
    for m in re.finditer(r"\[v(\d+)\]\s*(?:</[^>]+>\s*)*([A-Z][a-z]{2}, \d{1,2} [A-Z][a-z]{2} \d{4})", abs_html):
        try:
            d = _dt.datetime.strptime(m.group(2), "%a, %d %b %Y").date().isoformat()
        except ValueError:
            d = None
        v = f"v{m.group(1)}"
        if v not in [x["version"] for x in out]:
            out.append({"version": v, "date": d})
    return out


def _oa_arxiv(w: dict) -> str | None:
    """The arXiv id among an OpenAlex work's locations, if any."""
    for loc in [w.get("primary_location") or {}] + list(w.get("locations") or []):
        # an old-style id may hold a "v" (solv-int/9901001): only a trailing vN is a version
        m = re.search(r"arxiv\.org/(?:abs|pdf)/([^\s?#]+?)(?:v\d+)?(?:\.pdf)?(?:[?#].*)?$",
                      loc.get("landing_page_url") or "")
        if m:
            return retraction.normalize_arxiv(m.group(1))
    return None


class Card:
    def __init__(self, fetch: Fetch, raw_dir: Path | None):
        self.fetch, self.raw_dir = fetch, raw_dir
        self.errors: list[str] = []
        self.kept: list[dict] = []

    def get(self, label: str, url: str, headers: dict | None = None) -> bytes | None:
        try:
            data = self.fetch(url, headers or {})
        except net.HttpError as e:
            if not e.not_found:
                self.errors.append(f"{label}: {net.redact(str(e))[:160]}")
            return None
        if self.raw_dir:
            self.raw_dir.mkdir(parents=True, exist_ok=True)
            name = f"{len(self.kept) + 1:02d}-{label}"
            (self.raw_dir / name).write_bytes(data)
            self.kept.append({"file": name, "url": net.redact(url), "sha256": hashlib.sha256(data).hexdigest()})
        return data

    def unreadable(self, label: str, e: Exception) -> None:
        self.errors.append(f"{label}: no se pudo leer la respuesta ({type(e).__name__}; ¿página de mantenimiento?)")

    def json(self, label: str, url: str, headers: dict | None = None) -> dict | None:
        """The answer as a JSON object; an answer that is not one (an HTML
        maintenance page served with 200) makes this a source that failed."""
        raw = self.get(label, url, headers)
        if raw is None:
            return None
        try:
            data = json.loads(raw)
        except ValueError as e:
            self.unreadable(label, e)
            return None
        if not isinstance(data, dict):
            self.unreadable(label, ValueError("not an object"))
            return None
        return data


def retitled_candidates(c: Card, ident: dict) -> list[dict]:
    """A published version no source links to the preprint (a retitled
    camera-ready): Crossref's bibliographic search by title + first author, kept
    only when the title is close and the first author's surname matches —
    resolve_refs' rule. A candidate for the researcher to confirm, never the
    published version: it goes in no BibTeX."""
    first = rr.surname_of((ident.get("authors") or [""])[0] or "")
    if not first:
        return []
    data = c.json("crossref-search.json", "https://api.crossref.org/works?query.bibliographic="
                  + urllib.parse.quote(ident["title"]) + "&query.author=" + urllib.parse.quote(first)
                  + f"&rows={CANDIDATE_ROWS}&select=DOI,title,author,container-title,issued,type",
                  {"Accept": "application/json"}) or {}
    out = []
    for w in (data.get("message") or {}).get("items") or []:
        d = retraction.normalize_doi(w.get("DOI"))
        if not d or retraction.is_arxiv_doi(d) or (w.get("type") or "") == "posted-content" \
                or REPOSITORY.search((w.get("container-title") or [""])[0] or ""):
            continue                                    # a preprint record is not a published version
        title = (w.get("title") or [""])[0]
        level = rr.title_level(ident["title"], title)[0]
        au = (w.get("author") or [{}])[0] or {}
        if level not in ("exact", "close") or not rr.surname_matches(first, au.get("name") or "", au.get("family")):
            continue
        parts = ((w.get("issued") or {}).get("date-parts") or [[None]])[0] or [None]
        out.append({"doi": d, "title": title, "venue": (w.get("container-title") or [""])[0],
                    "year": str(parts[0] or ""),
                    "why": f"título {'idéntico' if level == 'exact' else 'parecido'} y mismo primer autor "
                           f"({first}) en Crossref, sin enlace declarado: confírmalo antes de citarlo así"})
    return out


CITING_ORDERS = ("recent", "cited")


def openreview_search_url(title: str) -> str:
    return ("https://api2.openreview.net/notes/search?term=" + urllib.parse.quote(title)
            + "&type=terms&content=all&source=forum&limit=10")


OR_WORKSHOP = re.compile(r"\bworkshops?\b", re.I)
# a follow-up paper often starts with its predecessor's title: for OpenReview, one
# title being the first words of the other counts only when it is long and most of it
OR_PREFIX = {"min_prefix": 6, "prefix_share": 0.7}


def openreview_acceptance(title: str, authors: list, raw: bytes | None) -> dict | None:
    """The OpenReview record (from a `notes/search` answer) that shows this paper
    accepted: same first author always, and exactly this title (`level: exact`)
    or a close one (`close`: a retitled camera-ready — or a follow-up, so only a
    candidate), with a venue (submitted / withdrawn / rejected are none). An
    exact title wins over a close one wherever it is listed, a main venue over a
    workshop (`workshop: True`, never the published version)."""
    try:
        recs = lit_search.parse_openreview(raw)[0] if raw else []
    except (ValueError, AttributeError):
        recs = []
    first = rr.surname_of((authors or [""])[0] or "")
    if not first:
        return None
    found = []
    for r in recs:
        if not r.get("venue") or not r.get("authors") or not rr.surname_matches(first, r["authors"][0]):
            continue
        level = rr.title_level(title, r.get("title") or "", **OR_PREFIX)[0]
        if level in ("exact", "close"):
            ws = bool(OR_WORKSHOP.search(f"{r.get('venue') or ''} {r.get('openreview_venue') or ''}"))
            found.append({**r, "level": level, "workshop": ws,
                          "retitled": f"; título publicado: «{r['title']}»" if level == "close" else ""})
    found.sort(key=lambda r: (r["level"] != "exact", r["workshop"]))      # stable: listed order otherwise
    return found[0] if found else None


def counts_as_published(p: dict) -> bool:
    """A piece of evidence that can stand for the published version: a venue that
    is not the arXiv pointer, a workshop or a preprint repository."""
    return bool(p.get("venue")) and "preprint en arXiv" not in p["venue"] and not p.get("workshop") \
        and not p.get("repository")


def arxiv_record(c: Card, card: dict, arxiv: str, primary: bool) -> dict | None:
    """The arXiv entry (identity, declared published version) and its versions.
    `primary`: the paper was asked for by its arXiv id, so arXiv names it; a
    preprint found later from a DOI only fills what the publisher left empty."""
    ident = card["identity"]
    raw = c.get("arxiv-api.xml", retraction.ARXIV_QUERY.format(urllib.parse.quote(arxiv, safe="/"), 1),
                {"Accept": "application/atom+xml"})
    e = None
    if raw:
        try:
            e = retraction.parse_arxiv_feed(raw).get(arxiv)
        except (ValueError, SyntaxError) as exc:          # ET.ParseError is a SyntaxError
            c.unreadable("arxiv-api.xml", exc)
    if e:
        found = {"title": e["title"], "authors": e["authors"], "year": (e["published"] or "")[:4]}
        for k, v in found.items():
            if primary:
                ident[k] = v
            else:
                ident.setdefault(k, v)
        ident["arxiv"] = arxiv
        if e.get("primary_class"):
            ident["primary_class"] = e["primary_class"]
        if e.get("doi") or e.get("journal_ref"):
            # journal_ref is free text ("Invented J. 7, 11 (2031)"): its year, when it names one
            years = re.findall(r"\b(?:19|20)\d{2}\b", e.get("journal_ref") or "")
            card["published"].append({"source": "arXiv (declarado por los autores)", "doi": e.get("doi") or "",
                                      "venue": e.get("journal_ref") or "", "year": years[-1] if years else ""})
    page = c.get("arxiv-abs.html", f"https://arxiv.org/abs/{arxiv}")
    card["versions"] = arxiv_versions(page.decode("utf-8", "replace")) if page else []
    return e


def build(arxiv: str | None, doi: str | None, n_cit: int, fetch: Fetch, raw_dir: Path | None,
          vault: Path | None = None, order: str = "recent") -> dict:
    c = Card(fetch, raw_dir)
    arxiv_entry = None
    card: dict = {"tool": TOOL, "query": {"arxiv": arxiv, "doi": doi}, "identity": {}, "versions": [],
                  "published": [], "retraction": None, "citations": {}, "citing": [], "citing_order": order,
                  "errors": c.errors}
    ident = card["identity"]
    asked_arxiv = arxiv
    if arxiv:
        e = arxiv_entry = arxiv_record(c, card, arxiv, primary=True)
        if e:
            doi = doi or (e.get("doi") if e.get("doi") and not retraction.is_arxiv_doi(e["doi"]) else None)
    oa_key = os.environ.get("OPENALEX_API_KEY")
    crossref_msg = None
    oa_ids = ([f"doi:{doi}"] if doi else []) + ([f"doi:10.48550/arXiv.{arxiv}"] if arxiv else [])
    # OpenAlex may hold the preprint and the published paper as two works, each
    # with its own citations: every one of them is counted and listed, never only the first
    works: dict[str, int | None] = {}
    oa_works: list[dict] = []
    for oid in oa_ids:
        w = c.json("openalex.json", f"https://api.openalex.org/works/{urllib.parse.quote(oid, safe=':/')}"
                   + (f"?api_key={urllib.parse.quote(oa_key)}" if oa_key else ""))
        if w:
            oa_works.append(w)
            wid = (w.get("id") or "").rsplit("/", 1)[-1]
            if wid and wid not in works:
                works[wid] = w.get("cited_by_count")
    if works:
        card["citations"]["openalex_works"] = works
    for w in oa_works[:1]:                  # identity and published version: the first work that answered
        card["citations"]["openalex"] = w.get("cited_by_count")
        if w.get("title"):
            ident.setdefault("title", w.get("title"))
        names = [n for n in ((a.get("author") or {}).get("display_name") for a in w.get("authorships") or []) if n]
        if names:
            ident.setdefault("authors", names)
        if w.get("publication_year"):
            ident.setdefault("year", str(w["publication_year"]))
        ident["openalex"] = (w.get("id") or "").rsplit("/", 1)[-1]
        for loc in [w.get("primary_location") or {}] + list(w.get("locations") or []):
            src = loc.get("source") or {}
            if (src.get("type") or "") in ("journal", "conference") and src.get("display_name") \
                    and not REPOSITORY.search(src["display_name"]):
                d = retraction.normalize_doi(w.get("doi"))
                if not d or retraction.is_arxiv_doi(d):
                    # the work is the preprint's: the published version's DOI is its landing page's
                    m = re.match(r"https?://(?:dx\.)?doi\.org/(.+)$", loc.get("landing_page_url") or "")
                    d = retraction.normalize_doi(m.group(1)) if m else None
                card["published"].append({"source": f"OpenAlex (ubicación de tipo {src['type']})",
                                          "doi": d if d and not retraction.is_arxiv_doi(d) else "",
                                          "venue": src["display_name"]})
                doi = doi or (d if d and not retraction.is_arxiv_doi(d) else None)
                break
        if not arxiv:
            found_arxiv = _oa_arxiv({"locations": w.get("locations") or []})
            if found_arxiv:
                arxiv = found_arxiv
                ident["arxiv"] = arxiv
                card["published"].append({"source": "OpenAlex", "doi": doi or "",
                                          "venue": "tiene preprint en arXiv: " + arxiv})
    if doi:
        data = c.json("crossref.json", retraction.CROSSREF_WORK.format(urllib.parse.quote(doi, safe="/:;()")),
                      {"Accept": "application/json"})
        if data is not None:
            msg = data.get("message") or {}
            crossref_msg = msg
            if (msg.get("title") or [""])[0]:
                ident.setdefault("title", " ".join(re.sub(r"<[^>]+>", " ", msg["title"][0]).split()))
            ident["doi"] = doi
            # OpenAlex silent: the publisher's record names the authors and the year
            cr_authors = [", ".join(x for x in (a.get("family"), a.get("given")) if x) or (a.get("name") or "")
                          for a in msg.get("author") or []]
            if [a for a in cr_authors if a]:
                ident.setdefault("authors", [a for a in cr_authors if a])
            parts = [x for x in ((msg.get("issued") or {}).get("date-parts") or [[]])[0] or [] if x]
            if parts:
                ident.setdefault("year", str(parts[0]))
            venue = (msg.get("container-title") or [""])[0]
            if venue:
                repo = (msg.get("type") or "") == "posted-content" or bool(REPOSITORY.search(venue))
                card["published"].append({"source": f"Crossref ({msg.get('type', '')})"
                                                    + ("; repositorio de preprints: no es una versión publicada"
                                                       if repo else ""), "doi": doi, "venue": venue,
                                          "date": "-".join(f"{x:02d}" if i else str(x) for i, x in enumerate(parts)),
                                          "year": str(parts[0]) if parts else "",
                                          **({"repository": True} if repo else {})})
            rel = msg.get("relation") or {}
            for k in ("is-preprint-of", "has-preprint"):
                for r in rel.get(k) or []:
                    card["published"].append({"source": f"Crossref relation {k}", "doi": r.get("id", ""), "venue": ""})
                    rd = retraction.normalize_doi(r.get("id"))
                    if k == "has-preprint" and not arxiv and rd and retraction.is_arxiv_doi(rd):
                        arxiv = retraction.arxiv_from_doi(rd)       # the publisher names its arXiv preprint
                        ident["arxiv"] = arxiv
            ident.setdefault("abstract", from_jats(msg.get("abstract")))
    if arxiv and not asked_arxiv:
        # asked by DOI (or title), the preprint found on the way: its versions and its
        # withdrawal check come from arXiv like for a paper asked by its arXiv id, and
        # the preprint's own OpenAlex work counts among the works cited
        arxiv_entry = arxiv_record(c, card, arxiv, primary=False)
        w = c.json("openalex.json", "https://api.openalex.org/works/doi:10.48550/arXiv."
                   + urllib.parse.quote(arxiv, safe="/") + (f"?api_key={urllib.parse.quote(oa_key)}" if oa_key else ""))
        if w:
            wid = (w.get("id") or "").rsplit("/", 1)[-1]
            if wid and wid not in works:
                works[wid] = w.get("cited_by_count")
                card["citations"]["openalex_works"] = works
    s2_id = f"arXiv:{arxiv}" if arxiv else (f"DOI:{doi}" if doi else None)
    if s2_id:
        hdr = {"x-api-key": os.environ["SEMANTIC_SCHOLAR_API_KEY"]} if os.environ.get("SEMANTIC_SCHOLAR_API_KEY") else {}
        p = c.json("s2.json", f"{S2}/paper/{urllib.parse.quote(s2_id, safe=':/')}"
                   "?fields=title,citationCount,influentialCitationCount,venue,publicationVenue,year", hdr)
        if p:
            card["citations"]["semantic_scholar"] = p.get("citationCount")
            card["citations"]["semantic_scholar_influential"] = p.get("influentialCitationCount")
            pv = p.get("publicationVenue") or {}
            # Semantic Scholar files a preprint server as a "journal" ("arXiv.org"): a
            # repository is where the preprint is, never a published version
            if pv.get("name") and (pv.get("type") or "") in ("journal", "conference") \
                    and not REPOSITORY.search(pv["name"]):
                card["published"].append({"source": "Semantic Scholar", "doi": "", "venue": pv["name"]})
        if n_cit and ident.get("openalex"):
            # OpenAlex sorts the citing works by date itself: the newest really are the newest;
            # `cites:W1|W2` lists the papers citing any of the paper's works, each once
            cited = "|".join(works) or ident["openalex"]
            sort = "cited_by_count:desc" if order == "cited" else "publication_date:desc"
            per = min(max(n_cit, 1), 100)
            results: list[dict] = []
            total = None
            for page in range(1, 101):              # OpenAlex pages up to 10 000 results
                data = c.json(f"openalex-citing{'-cited' if order == 'cited' else ''}"
                              f"{'' if page == 1 else f'-p{page}'}.json",
                              f"https://api.openalex.org/works?filter=cites:{cited}"
                              f"&sort={sort}&per-page={per}&page={page}"
                              "&select=id,doi,title,publication_year,publication_date,authorships,primary_location,"
                              "locations,cited_by_count"
                              + (f"&api_key={urllib.parse.quote(oa_key)}" if oa_key else ""))
                if data is None:
                    if results:
                        card["citing_incomplete"] = True      # a later page did not come
                    break
                got = [w for w in data.get("results") or [] if isinstance(w, dict)]
                total = total or (data.get("meta") or {}).get("count")
                results += got
                if len(got) < per or len(results) >= n_cit:
                    break
            if results:
                card["citing_source"] = "OpenAlex"
                card["citing_total_listed"] = total or len(results)
                if len(works) > 1:
                    card["citations"]["openalex"] = card["citing_total_listed"]     # the union, not a sum
                for w in results[:n_cit]:
                    d = retraction.normalize_doi(w.get("doi"))
                    card["citing"].append({
                        "title": w.get("title"), "year": w.get("publication_year"), "date": w.get("publication_date"),
                        "venue": ((w.get("primary_location") or {}).get("source") or {}).get("display_name"),
                        "arxiv": _oa_arxiv(w) or (retraction.arxiv_from_doi(d) if d and retraction.is_arxiv_doi(d)
                                                  else None),
                        "doi": d if d and not retraction.is_arxiv_doi(d) else None,
                        "cited_by": w.get("cited_by_count"),
                        "first_author": (((w.get("authorships") or [{}])[0] or {}).get("author") or {}).get(
                            "display_name")})
        if n_cit and not card["citing"]:
            card["citing_source"] = "Semantic Scholar"
            citing: list[dict] = []
            offset = 0
            while offset < 1000:
                data = c.json(f"s2-citations-{offset}.json",
                              f"{S2}/paper/{urllib.parse.quote(s2_id, safe=':/')}/citations?offset={offset}&limit=100"
                              "&fields=title,year,publicationDate,venue,externalIds,authors,citationCount", hdr)
                if data is None:
                    card["citing_incomplete"] = True          # a page did not come: the list is partial
                    break
                for it in data.get("data") or []:
                    q = it.get("citingPaper") or {}
                    ext = q.get("externalIds") or {}
                    citing.append({"title": q.get("title"), "year": q.get("year"), "date": q.get("publicationDate"),
                                   "venue": q.get("venue") or None, "arxiv": ext.get("ArXiv"), "doi": ext.get("DOI"),
                                   "cited_by": q.get("citationCount"),
                                   "first_author": ((q.get("authors") or [{}])[0] or {}).get("name")})
                if data.get("next") is None:
                    break
                offset = data["next"]
            if order == "cited":
                citing.sort(key=lambda x: x.get("cited_by") or 0, reverse=True)
            else:
                citing.sort(key=lambda x: (x.get("date") or str(x.get("year") or "")), reverse=True)
            card["citing_total_listed"] = len(citing)
            # S2 pages in its own order: past 1000, "newest" means newest of the first 1000 it gave
            card["citing_truncated"] = offset >= 1000
            card["citing"] = citing[:n_cit]
    if ident.get("title"):
        # ICLR / NeurIPS / ICML / MLSys / TMLR have no DOI: OpenReview holds the decision and the
        # year. A record accepted there counts with exactly this title, or with a close one
        # (resolve_refs' rule: a retitled camera-ready) by the same first author — and the
        # card then shows the published title.
        page = c.json("openreview.json", openreview_search_url(ident["title"]), {"Accept": "application/json"})
        hit = openreview_acceptance(ident["title"], ident.get("authors") or [],
                                    json.dumps(page).encode() if page is not None else None)
        if hit and hit["level"] == "exact":
            card["published"].append({"source": f"OpenReview (decisión: {hit['openreview_venue']}"
                                                + ("; workshop: no es la versión publicada" if hit["workshop"] else "")
                                                + ")",
                                      "doi": hit.get("doi") or "", "venue": hit["venue"],
                                      "year": str(hit.get("year") or ""), "url": hit["url"],
                                      **({"workshop": True} if hit["workshop"] else {})})
        elif hit:
            # a close title by the same first author: a retitled camera-ready, or a
            # follow-up — like a Crossref title-search hit, to confirm, never in the BibTeX
            card.setdefault("published_candidates", []).append({
                "doi": hit.get("doi") or "", "title": hit.get("title") or "", "venue": hit["venue"],
                "year": str(hit.get("year") or ""), "url": hit["url"],
                "why": f"título parecido y mismo primer autor en OpenReview (decisión: {hit['openreview_venue']}"
                       + ("; workshop" if hit["workshop"] else "")
                       + "), sin enlace declarado: confírmalo antes de citarlo así"})
    if arxiv and ident.get("title") and not [p for p in card["published"] if counts_as_published(p)]:
        card.setdefault("published_candidates", []).extend(retitled_candidates(c, ident))
    if doi or arxiv:
        # the same detection rules as check_retraction.py, on the records fetched above
        checks = []
        if doi:
            checks.append(retraction.crossref_flags(crossref_msg, doi) if crossref_msg is not None
                          else retraction.Check("crossref", state="lost", error="no Crossref record fetched"))
        if arxiv:
            checks.append(retraction.arxiv_check(arxiv_entry) if arxiv_entry is not None
                          else retraction.Check("arxiv", state="lost", error="no arXiv entry fetched"))
        status, ev = retraction.verdict(checks)
        lost = [ch.source for ch in checks if ch.state == "lost"]
        card["retraction"] = {"status": status if not lost or status != "clear" else "no comprobado del todo",
                              "evidence": ev + [f"{x}: no respondió" for x in lost]}
    ident.setdefault("doi", doi)
    ident.setdefault("arxiv", arxiv)
    if vault:
        sys.path.insert(0, str(HERE))
        import ingest_paper
        hit = ingest_paper.find_existing(vault, retraction.normalize_arxiv(arxiv) or "",
                                         retraction.normalize_doi(doi) or "", ident.get("title") or "")
        card["vault"] = hit.name.split(" ")[0] if hit else None
    # for the BibTeX: the publisher's own record first (clean venue name and year),
    # then OpenAlex, then what arXiv's authors declared (journal_ref is free text)
    real = [p for p in card["published"] if counts_as_published(p)]
    pub = next((p for src in ("Crossref (", "OpenReview", "OpenAlex", "Semantic", "arXiv") for p in real
                if p["source"].startswith(src)), None)
    meta = {"title": ident.get("title") or "", "authors": ident.get("authors") or [], "year": ident.get("year") or "",
            "venue": "arXiv preprint" if arxiv else (pub or {}).get("venue", ""), "doi": "" if arxiv else (doi or ""),
            "arxiv": arxiv or "", "published_doi": (pub or {}).get("doi", "") if arxiv else "",
            "published_venue": (pub or {}).get("venue", "") if arxiv else "",
            "published_year": (pub or {}).get("year", "") if arxiv else "",
            "arxiv_version": card["versions"][-1]["version"] if card["versions"] else "", "id": "",
            "status": (card["retraction"] or {}).get("status", ""), "source": "",
            "primary_class": ident.get("primary_class") or ""}
    if crossref_msg and (pub or {}).get("source", "").startswith("Crossref ("):
        # the publisher's own record: its registered type, volume, issue and pages
        meta.update(venue_type=crossref_msg.get("type") or "", volume=str(crossref_msg.get("volume") or ""),
                    issue=str(crossref_msg.get("issue") or ""),
                    pages=str(crossref_msg.get("page") or "").replace("-", "--"))
        if arxiv:
            # cited as the published version: the publisher's title and authors, not the preprint's
            ptitle = " ".join(re.sub(r"<[^>]+>", " ", (crossref_msg.get("title") or [""])[0] or "").split())
            if ptitle and rr.title_level(meta["title"], ptitle)[0] != "exact":
                meta["published_title"] = ptitle
            pauth = [", ".join(x for x in (a.get("family"), a.get("given")) if x) or (a.get("name") or "")
                     for a in crossref_msg.get("author") or []]
            if [a for a in pauth if a]:
                meta["published_authors"] = [a for a in pauth if a]
    # nothing was ingested here, so no `kairoread` (the text the vault's locators point at)
    card["bibtex"] = export_bib.bibtex_entry(meta, export_bib.base_key(meta), read_field=False)
    # export_bib leaves an empty field out: the card says what no source gave
    missing = [x for x, v in (("autor", [a for a in export_bib.cited_authors(meta) if a]),
                              ("año", re.search(r"\byear = \{\d", card["bibtex"]))) if not v]
    if missing:
        card["bibtex_missing"] = missing
    card["raw"] = c.kept
    return card


def markdown(card: dict) -> str:
    i = card["identity"]
    L = [f"# {i.get('title') or '(sin título)'}", "",
         f"**Autores:** {', '.join(a for a in (i.get('authors') or []) if a) or 'no consta'}  ",
         f"**Año:** {i.get('year') or 'no consta'} · **arXiv:** {i.get('arxiv') or '—'} · "
         f"**DOI:** {i.get('doi') or '—'} · **OpenAlex:** {i.get('openalex') or '—'}", ""]
    if card.get("found_by"):
        L += [f"**Encontrado por título en:** {card['found_by']}", ""]
    if "vault" in card:
        L += [f"**En el vault:** {card['vault']}" if card["vault"] else
              "**En el vault:** no — `python scripts/papers/ingest_paper.py add --vault <vault> --project PROJ-XXX "
              + (f"--arxiv {i['arxiv']}`" if i.get("arxiv") else f"--doi {i.get('doi')}`"), ""]
    L += ["## Versiones de arXiv", ""]
    L += [f"- {v['version']} — {v['date'] or 'fecha no leída'}" for v in card["versions"]] or ["- no es un preprint de arXiv / no consta"]
    L += ["", "## Versión publicada (revisada)", ""]
    L += [f"- {p['venue'] or '—'}" + (f" · DOI {p['doi']}" if p.get("doi") else "")
          + (f" · {p['date']}" if p.get("date") else "") + f" — según {p['source']}" for p in card["published"]] \
        or ["- no consta en arXiv, OpenAlex, Crossref ni Semantic Scholar"]
    if card.get("published_candidates"):
        L += ["", "**Posible versión publicada (candidata, no confirmada):**", ""]
        L += [f"- {p['title']} — {p['venue'] or '—'} ({p['year'] or 's. f.'})"
              + (f" · DOI {p['doi']}" if p.get("doi") else "") + (f" · {p['url']}" if p.get("url") else "")
              + f" — {p['why']}" for p in card["published_candidates"]]
    r = card["retraction"] or {}
    L += ["", "## Estado", "", f"- Retracción / retirada: **{r.get('status', 'no comprobado')}**"
          + (f" ({'; '.join(r.get('evidence') or [])})" if r.get("evidence") else "")]
    cit = card["citations"]
    L += ["", "## Citas", "",
          f"- OpenAlex: {cit.get('openalex', '—')} · Semantic Scholar: {cit.get('semantic_scholar', '—')}"
          f" (influyentes: {cit.get('semantic_scholar_influential', '—')})"]
    if len(cit.get("openalex_works") or {}) > 1:
        L.append("- OpenAlex guarda este paper como varios trabajos (preprint y versión publicada): "
                 + ", ".join(f"{w} {n}" for w, n in cit["openalex_works"].items())
                 + "; los que citan a cualquiera de ellos se cuentan una vez")
    if card.get("citing_incomplete"):
        L.append(f"- ⚠️ Lista de citantes incompleta: una página de {card.get('citing_source') or 'la fuente'} "
                 "no respondió")
    if card["citing"]:
        scope = (f"entre las {card.get('citing_total_listed', 0)} primeras que devuelve Semantic Scholar"
                 if card.get("citing_truncated") else f"de {card.get('citing_total_listed', 0)}")
        which = "más citadas" if card.get("citing_order") == "cited" else "más recientes"
        L += [f"- Las {len(card['citing'])} {which} {scope} (fuente: {card.get('citing_source', '—')}):", ""]
        for q in card["citing"]:
            ids = " · ".join(x for x in (f"arXiv:{q['arxiv']}" if q.get("arxiv") else "",
                                         f"DOI:{q['doi']}" if q.get("doi") else "") if x)
            L.append(f"  - {q.get('title')} — {q.get('first_author') or ''} ({q.get('date') or q.get('year') or 's. f.'})"
                     + (f", {q['venue']}" if q.get("venue") else "") + (f" — {ids}" if ids else "")
                     + (f" — citado {q['cited_by']} veces" if q.get("cited_by") is not None else ""))
    if card["errors"]:
        L += ["", "## ⚠️ Fuentes que fallaron", ""] + [f"- {e}" for e in card["errors"]]
    L += ["", "## BibTeX", ""]
    if card.get("bibtex_missing"):
        L += [f"⚠️ BibTeX sin {' ni '.join(card['bibtex_missing'])}: ninguna fuente lo dio; complétalo a mano", ""]
    L += ["```bibtex", card["bibtex"].rstrip(), "```", ""]
    return "\n".join(L)


def _norm_title(t: str) -> str:
    """resolve_refs' normalisation: math and TeX commands keep their content
    (`$\\mathbb{Z}_4$` → `z 4`), so two math titles never collapse into one."""
    return rr.normalize_title(t or "")


def _collapse_versions(exact: list[dict]) -> list[dict]:
    """OpenAlex may keep a preprint and its published paper as two works: works
    linked by the arXiv id one of them carries and the other lists are one paper
    (its DOI and its arXiv id); two different DOIs stay two."""
    out: list[dict] = []
    for h in exact:
        same = next((o for o in out if h["arxiv"] and o["arxiv"] == h["arxiv"]
                     and not (h["doi"] and o["doi"] and h["doi"] != o["doi"])), None)
        if same:
            same["doi"] = same["doi"] or h["doi"]
            same["openalex_works"] = same.get("openalex_works", [same["openalex"]]) + [h["openalex"]]
        else:
            out.append(dict(h))
    return out


def find_by_title(title: str, fetch: Fetch) -> dict:
    """OpenAlex's title search. Exactly one work whose normalised title equals the
    one asked for is the paper (a preprint work and its published work linked by
    their arXiv id count as one); otherwise the closest hits are listed and
    nothing is guessed."""
    key = os.environ.get("OPENALEX_API_KEY")
    try:
        raw = fetch("https://api.openalex.org/works?search=" + urllib.parse.quote(title)
                    + "&per-page=10&select=id,doi,title,publication_year,primary_location,locations"
                    + (f"&api_key={urllib.parse.quote(key)}" if key else ""), {})
        results = json.loads(raw).get("results") or []
    except (net.HttpError, ValueError, AttributeError) as e:
        arx = _arxiv_by_title(title, fetch)
        if len(arx) == 1:
            return {"chosen": {**arx[0], "found_by": "arXiv (OpenAlex no respondió)"}}
        return {"error": f"OpenAlex: {net.redact(str(e))[:160]}"}
    hits = []
    for w in results:
        d = retraction.normalize_doi(w.get("doi"))
        arx = _oa_arxiv(w) or (retraction.arxiv_from_doi(d) if d and retraction.is_arxiv_doi(d) else None)
        hits.append({"title": w.get("title"), "year": w.get("publication_year"), "openalex": (w.get("id") or "").rsplit("/", 1)[-1],
                     "doi": d if d and not retraction.is_arxiv_doi(d) else None, "arxiv": arx})
    exact = _collapse_versions([h for h in hits if _norm_title(h["title"] or "") == _norm_title(title)
                                and (h["doi"] or h["arxiv"])])
    if len(exact) == 1:
        return {"chosen": exact[0]}
    if not exact:
        arx = _arxiv_by_title(title, fetch)
        if len(arx) == 1:
            return {"chosen": {**arx[0], "found_by": "arXiv (OpenAlex no lo tiene aún)"}}
    if not hits:
        return {"error": "ni OpenAlex ni arXiv tienen un trabajo con ese título exacto"}
    return {"error": "título ambiguo: elige uno y vuelve con --arxiv o --doi", "candidates": hits[:10]}


def _arxiv_by_title(title: str, fetch: Fetch) -> list[dict]:
    """arXiv entries whose title is exactly `title` (case, punctuation and accents aside)."""
    q = urllib.parse.quote(f'ti:"{" ".join(_norm_title(title).split())}"', safe="")
    try:
        entries = retraction.parse_arxiv_feed(
            fetch(f"https://export.arxiv.org/api/query?search_query={q}&max_results=10", {}))
    except (net.HttpError, ValueError, SyntaxError):          # ET.ParseError: a maintenance page
        return []
    out = []
    for aid, e in entries.items():
        if _norm_title(e.get("title") or "") == _norm_title(title):
            out.append({"title": e.get("title"), "arxiv": aid, "doi": None, "year": (e.get("published") or "")[:4]})
    return out


def main(argv: list[str] | None = None, fetch: Fetch = default_fetch) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--arxiv")
    g.add_argument("--doi")
    g.add_argument("--title", help="find the paper by its title (OpenAlex); an ambiguous title lists candidates")
    ap.add_argument("--vault", type=Path)
    ap.add_argument("--citations", type=int, default=25)
    ap.add_argument("--citing-order", choices=CITING_ORDERS, default="recent",
                    help="the citing papers listed: the newest (default) or the most cited")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--raw-dir", type=Path)
    a = ap.parse_args(argv)
    if a.title:
        found = find_by_title(a.title, fetch)
        if not found.get("chosen"):
            print(json.dumps({"tool": TOOL, "title": a.title, **found}, ensure_ascii=False, indent=2))
            return 3 if found.get("candidates") else 1
        a.arxiv, a.doi = found["chosen"].get("arxiv"), found["chosen"].get("doi")
    arxiv = retraction.normalize_arxiv(a.arxiv) if a.arxiv else None
    doi = retraction.normalize_doi(a.doi) if a.doi else None
    if a.arxiv and not arxiv or a.doi and not doi:
        print(json.dumps({"tool": TOOL, "error": "unrecognised identifier"}))
        return 2
    if doi and retraction.is_arxiv_doi(doi):
        arxiv, doi = retraction.arxiv_from_doi(doi), None
    card = build(arxiv, doi, a.citations, fetch, a.raw_dir, a.vault, order=a.citing_order)
    if a.title:
        # how the title was resolved: OpenAlex's exact title, or arXiv's when OpenAlex had none
        card["found_by"] = found["chosen"].get("found_by") or "OpenAlex (título exacto)"
    if not card["identity"].get("title"):
        print(json.dumps({"tool": TOOL, "error": "no source knows this paper", "errors": card["errors"]},
                         ensure_ascii=False))
        return 1
    print(json.dumps(card, ensure_ascii=False, indent=2) if a.json else markdown(card))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
