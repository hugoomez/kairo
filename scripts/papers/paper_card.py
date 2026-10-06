#!/usr/bin/env python3
"""One paper's card: which versions exist, where it was published, whether it
was retracted, who cites it, and its BibTeX — all from public records.

    paper_card.py (--arxiv ID | --doi DOI | --title "<title>") [--vault <vault>] [--citations 25] [--json]
                  [--raw-dir DIR]

Sources (no model involved, each answer kept with --raw-dir):
  - arXiv: the API entry (title, authors, journal_ref, declared DOI) and the
    abstract page's submission history — every version with its date;
  - OpenAlex: the work (by DOI, and by the arXiv DOI 10.48550/arXiv.<id>): its
    locations (repository vs journal / conference), cited_by_count — when
    OpenAlex keeps the preprint and the published paper as two works, both are
    counted (`openalex_works`) and the citing list covers both (`cites:W1|W2`);
  - Crossref (a non-arXiv DOI): container title, issue date, and the
    `is-preprint-of` / `has-preprint` relations publishers register;
  - OpenReview: an accepted record with exactly this title (ICLR, NeurIPS,
    ICML, MLSys, TMLR — venues with no DOI) gives the venue and its year;
  - Semantic Scholar: citation count and the list of citing papers
    (the newest first, up to --citations); a page that does not come marks
    the list `citing_incomplete`;
  - the shared retraction / withdrawal check (Crossref + arXiv).

"Published version" lists every piece of evidence separately (arXiv
journal_ref / declared DOI, an OpenAlex journal or conference location, a
Crossref relation) and says which source said what; when none does, it says
"no consta", never guesses. With --vault, says whether the paper is already a
note (P-XXXX) and prints the ingest_paper.py command otherwise.

--title looks the paper up in OpenAlex: one work with exactly that title (case,
punctuation and accents aside) is the paper; anything else lists the closest
hits and exits 3, never guessing. The citing papers come from OpenAlex, which
sorts them by date itself; Semantic Scholar is the fallback (it pages in its
own order, so past 1000 the card says it is the newest of the first 1000).

Prints Markdown (or one JSON object with --json). A source that failed is
named in the card, never silently left out. Exit: 0 ok · 1 nothing found ·
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
import unicodedata
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

TOOL = "kairo/paper_card@1.2.0"
S2 = "https://api.semanticscholar.org/graph/v1"
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
        m = re.search(r"arxiv\.org/(?:abs|pdf)/([^\s?#]+?)(?:v\d+)?(?:\.pdf)?$", loc.get("landing_page_url") or "")
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


def build(arxiv: str | None, doi: str | None, n_cit: int, fetch: Fetch, raw_dir: Path | None,
          vault: Path | None = None) -> dict:
    c = Card(fetch, raw_dir)
    arxiv_entry = None
    card: dict = {"tool": TOOL, "query": {"arxiv": arxiv, "doi": doi}, "identity": {}, "versions": [],
                  "published": [], "retraction": None, "citations": {}, "citing": [], "errors": c.errors}
    ident = card["identity"]
    if arxiv:
        raw = c.get("arxiv-api.xml", retraction.ARXIV_QUERY.format(urllib.parse.quote(arxiv, safe="/"), 1),
                    {"Accept": "application/atom+xml"})
        e = retraction.parse_arxiv_feed(raw).get(arxiv) if raw else None
        arxiv_entry = e
        if e:
            ident.update(title=e["title"], authors=e["authors"], year=(e["published"] or "")[:4], arxiv=arxiv)
            if e.get("doi") or e.get("journal_ref"):
                # journal_ref is free text ("Invented J. 7, 11 (2031)"): its year, when it names one
                years = re.findall(r"\b(?:19|20)\d{2}\b", e.get("journal_ref") or "")
                card["published"].append({"source": "arXiv (declarado por los autores)", "doi": e.get("doi") or "",
                                          "venue": e.get("journal_ref") or "", "year": years[-1] if years else ""})
            doi = doi or (e.get("doi") if e.get("doi") and not retraction.is_arxiv_doi(e["doi"]) else None)
        page = c.get("arxiv-abs.html", f"https://arxiv.org/abs/{arxiv}")
        card["versions"] = arxiv_versions(page.decode("utf-8", "replace")) if page else []
    oa_key = os.environ.get("OPENALEX_API_KEY")
    crossref_msg = None
    oa_ids = ([f"doi:{doi}"] if doi else []) + ([f"doi:10.48550/arXiv.{arxiv}"] if arxiv else [])
    # OpenAlex may hold the preprint and the published paper as two works, each
    # with its own citations: every one of them is counted and listed, never only the first
    works: dict[str, int | None] = {}
    oa_raw: list[bytes] = []
    for oid in oa_ids:
        raw = c.get("openalex.json", f"https://api.openalex.org/works/{urllib.parse.quote(oid, safe=':/')}"
                    + (f"?api_key={urllib.parse.quote(oa_key)}" if oa_key else ""))
        if raw:
            oa_raw.append(raw)
            w = json.loads(raw)
            wid = (w.get("id") or "").rsplit("/", 1)[-1]
            if wid and wid not in works:
                works[wid] = w.get("cited_by_count")
    if works:
        card["citations"]["openalex_works"] = works
    for raw in oa_raw[:1]:                  # identity and published version: the first work that answered
        w = json.loads(raw)
        card["citations"]["openalex"] = w.get("cited_by_count")
        ident.setdefault("title", w.get("title"))
        ident.setdefault("authors", [(a.get("author") or {}).get("display_name") for a in w.get("authorships") or []])
        ident.setdefault("year", str(w.get("publication_year") or ""))
        ident["openalex"] = (w.get("id") or "").rsplit("/", 1)[-1]
        for loc in [w.get("primary_location") or {}] + list(w.get("locations") or []):
            src = loc.get("source") or {}
            if (src.get("type") or "") in ("journal", "conference") and src.get("display_name"):
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
            for loc in w.get("locations") or []:
                m = re.search(r"arxiv\.org/abs/([^\s?#v]+(?:v\d+)?)", loc.get("landing_page_url") or "")
                if m:
                    arxiv = retraction.normalize_arxiv(m.group(1))
                    ident["arxiv"] = arxiv
                    card["published"].append({"source": "OpenAlex", "doi": doi or "",
                                              "venue": "tiene preprint en arXiv: " + arxiv})
                    break
    if doi:
        raw = c.get("crossref.json", retraction.CROSSREF_WORK.format(urllib.parse.quote(doi, safe="/:;()")),
                    {"Accept": "application/json"})
        if raw:
            msg = json.loads(raw).get("message") or {}
            crossref_msg = msg
            ident.setdefault("title", (msg.get("title") or [""])[0])
            ident["doi"] = doi
            venue = (msg.get("container-title") or [""])[0]
            if venue:
                parts = ((msg.get("issued") or {}).get("date-parts") or [[]])[0]
                card["published"].append({"source": f"Crossref ({msg.get('type', '')})", "doi": doi, "venue": venue,
                                          "date": "-".join(f"{x:02d}" if i else str(x) for i, x in enumerate(parts)),
                                          "year": str(parts[0]) if parts else ""})
            rel = msg.get("relation") or {}
            for k in ("is-preprint-of", "has-preprint"):
                for r in rel.get(k) or []:
                    card["published"].append({"source": f"Crossref relation {k}", "doi": r.get("id", ""), "venue": ""})
            ident.setdefault("abstract", from_jats(msg.get("abstract")))
    s2_id = f"arXiv:{arxiv}" if arxiv else (f"DOI:{doi}" if doi else None)
    if s2_id:
        hdr = {"x-api-key": os.environ["SEMANTIC_SCHOLAR_API_KEY"]} if os.environ.get("SEMANTIC_SCHOLAR_API_KEY") else {}
        raw = c.get("s2.json", f"{S2}/paper/{urllib.parse.quote(s2_id, safe=':/')}"
                    "?fields=title,citationCount,influentialCitationCount,venue,publicationVenue,year", hdr)
        if raw:
            p = json.loads(raw)
            card["citations"]["semantic_scholar"] = p.get("citationCount")
            card["citations"]["semantic_scholar_influential"] = p.get("influentialCitationCount")
            pv = p.get("publicationVenue") or {}
            if pv.get("name") and (pv.get("type") or "") in ("journal", "conference"):
                card["published"].append({"source": "Semantic Scholar", "doi": "", "venue": pv["name"]})
        if n_cit and ident.get("openalex"):
            # OpenAlex sorts the citing works by date itself: the newest really are the newest;
            # `cites:W1|W2` lists the papers citing any of the paper's works, each once
            cited = "|".join(works) or ident["openalex"]
            raw = c.get("openalex-citing.json",
                        f"https://api.openalex.org/works?filter=cites:{cited}"
                        f"&sort=publication_date:desc&per-page={min(max(n_cit, 1), 100)}"
                        "&select=id,doi,title,publication_year,publication_date,authorships,primary_location,locations"
                        + (f"&api_key={urllib.parse.quote(oa_key)}" if oa_key else ""))
            data = json.loads(raw) if raw else {}
            if data.get("results"):
                card["citing_source"] = "OpenAlex"
                card["citing_total_listed"] = (data.get("meta") or {}).get("count") or len(data["results"])
                if len(works) > 1:
                    card["citations"]["openalex"] = card["citing_total_listed"]     # the union, not a sum
                for w in data["results"][:n_cit]:
                    d = retraction.normalize_doi(w.get("doi"))
                    card["citing"].append({
                        "title": w.get("title"), "year": w.get("publication_year"), "date": w.get("publication_date"),
                        "venue": ((w.get("primary_location") or {}).get("source") or {}).get("display_name"),
                        "arxiv": _oa_arxiv(w) or (retraction.arxiv_from_doi(d) if d and retraction.is_arxiv_doi(d)
                                                  else None),
                        "doi": d if d and not retraction.is_arxiv_doi(d) else None,
                        "first_author": (((w.get("authorships") or [{}])[0] or {}).get("author") or {}).get(
                            "display_name")})
        if n_cit and not card["citing"]:
            card["citing_source"] = "Semantic Scholar"
            citing: list[dict] = []
            offset = 0
            while offset < 1000:
                raw = c.get(f"s2-citations-{offset}.json",
                            f"{S2}/paper/{urllib.parse.quote(s2_id, safe=':/')}/citations?offset={offset}&limit=100"
                            "&fields=title,year,publicationDate,venue,externalIds,authors", hdr)
                if not raw:
                    card["citing_incomplete"] = True          # a page did not come: the list is partial
                    break
                data = json.loads(raw)
                for it in data.get("data") or []:
                    q = it.get("citingPaper") or {}
                    ext = q.get("externalIds") or {}
                    citing.append({"title": q.get("title"), "year": q.get("year"), "date": q.get("publicationDate"),
                                   "venue": q.get("venue") or None, "arxiv": ext.get("ArXiv"), "doi": ext.get("DOI"),
                                   "first_author": ((q.get("authors") or [{}])[0] or {}).get("name")})
                if data.get("next") is None:
                    break
                offset = data["next"]
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
        raw = c.get("openreview.json", "https://api2.openreview.net/notes/search?term="
                    + urllib.parse.quote(ident["title"]) + "&type=terms&content=all&source=forum&limit=10",
                    {"Accept": "application/json"})
        recs = lit_search.parse_openreview(raw)[0] if raw else []
        first = rr.surname_of((ident.get("authors") or [""])[0] or "")

        def same(r: dict) -> str | None:
            level = rr.title_level(ident["title"], r.get("title") or "")[0]
            if level == "exact":
                return ""
            if level == "close" and first and r.get("authors") and rr.surname_matches(first, r["authors"][0]):
                return f"; título publicado: «{r['title']}»"
            return None
        hit = next(((r, same(r)) for r in recs if r.get("venue") and same(r) is not None), None)
        if hit:
            hit, retitled = hit
            card["published"].append({"source": f"OpenReview (decisión: {hit['openreview_venue']}{retitled})",
                                      "doi": hit.get("doi") or "", "venue": hit["venue"],
                                      "year": str(hit.get("year") or ""), "url": hit["url"]})
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
    real = [p for p in card["published"] if p.get("venue") and "preprint en arXiv" not in p["venue"]]
    pub = next((p for src in ("Crossref (", "OpenReview", "OpenAlex", "Semantic", "arXiv") for p in real
                if p["source"].startswith(src)), None)
    meta = {"title": ident.get("title") or "", "authors": ident.get("authors") or [], "year": ident.get("year") or "",
            "venue": "arXiv preprint" if arxiv else (pub or {}).get("venue", ""), "doi": "" if arxiv else (doi or ""),
            "arxiv": arxiv or "", "published_doi": (pub or {}).get("doi", "") if arxiv else "",
            "published_venue": (pub or {}).get("venue", "") if arxiv else "",
            "published_year": (pub or {}).get("year", "") if arxiv else "",
            "arxiv_version": card["versions"][-1]["version"] if card["versions"] else "", "id": "",
            "status": (card["retraction"] or {}).get("status", ""), "source": ""}
    if crossref_msg and (pub or {}).get("source", "").startswith("Crossref ("):
        # the publisher's own record: its registered type, volume, issue and pages
        meta.update(venue_type=crossref_msg.get("type") or "", volume=str(crossref_msg.get("volume") or ""),
                    issue=str(crossref_msg.get("issue") or ""),
                    pages=str(crossref_msg.get("page") or "").replace("-", "--"))
    card["bibtex"] = export_bib.bibtex_entry(meta, export_bib.base_key(meta)).replace("  keywords = {kairo:},\n", "")
    card["raw"] = c.kept
    return card


def markdown(card: dict) -> str:
    i = card["identity"]
    L = [f"# {i.get('title') or '(sin título)'}", "",
         f"**Autores:** {', '.join(a for a in (i.get('authors') or []) if a) or 'no consta'}  ",
         f"**Año:** {i.get('year') or 'no consta'} · **arXiv:** {i.get('arxiv') or '—'} · "
         f"**DOI:** {i.get('doi') or '—'} · **OpenAlex:** {i.get('openalex') or '—'}", ""]
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
        L.append("- ⚠️ Lista de citantes incompleta: una página de Semantic Scholar no respondió")
    if card["citing"]:
        scope = (f"entre las {card.get('citing_total_listed', 0)} primeras que devuelve Semantic Scholar"
                 if card.get("citing_truncated") else f"de {card.get('citing_total_listed', 0)}")
        L += [f"- Las {len(card['citing'])} más recientes {scope} (fuente: {card.get('citing_source', '—')}):", ""]
        for q in card["citing"]:
            ids = " · ".join(x for x in (f"arXiv:{q['arxiv']}" if q.get("arxiv") else "",
                                         f"DOI:{q['doi']}" if q.get("doi") else "") if x)
            L.append(f"  - {q.get('title')} — {q.get('first_author') or ''} ({q.get('date') or q.get('year') or 's. f.'})"
                     + (f", {q['venue']}" if q.get("venue") else "") + (f" — {ids}" if ids else ""))
    if card["errors"]:
        L += ["", "## ⚠️ Fuentes que fallaron", ""] + [f"- {e}" for e in card["errors"]]
    L += ["", "## BibTeX", "", "```bibtex", card["bibtex"].rstrip(), "```", ""]
    return "\n".join(L)


def _norm_title(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", re.sub(r"<[^>]+>|\$[^$]*\$", " ", t)).strip()


def find_by_title(title: str, fetch: Fetch) -> dict:
    """OpenAlex's title search. Exactly one work whose normalised title equals the
    one asked for is the paper; otherwise the closest hits are listed and nothing
    is guessed."""
    key = os.environ.get("OPENALEX_API_KEY")
    try:
        raw = fetch("https://api.openalex.org/works?search=" + urllib.parse.quote(title)
                    + "&per-page=10&select=id,doi,title,publication_year,primary_location,locations"
                    + (f"&api_key={urllib.parse.quote(key)}" if key else ""), {})
    except net.HttpError as e:
        return {"error": f"OpenAlex: {net.redact(str(e))[:160]}"}
    hits = []
    for w in json.loads(raw).get("results") or []:
        d = retraction.normalize_doi(w.get("doi"))
        arx = _oa_arxiv(w) or (retraction.arxiv_from_doi(d) if d and retraction.is_arxiv_doi(d) else None)
        hits.append({"title": w.get("title"), "year": w.get("publication_year"), "openalex": (w.get("id") or "").rsplit("/", 1)[-1],
                     "doi": d if d and not retraction.is_arxiv_doi(d) else None, "arxiv": arx})
    exact = [h for h in hits if _norm_title(h["title"] or "") == _norm_title(title) and (h["doi"] or h["arxiv"])]
    if len(exact) == 1:
        return {"chosen": exact[0]}
    if not hits:
        return {"error": "OpenAlex no encuentra ningún trabajo con ese título"}
    return {"error": "título ambiguo: elige uno y vuelve con --arxiv o --doi", "candidates": hits[:10]}


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
    card = build(arxiv, doi, a.citations, fetch, a.raw_dir, a.vault)
    if not card["identity"].get("title"):
        print(json.dumps({"tool": TOOL, "error": "no source knows this paper", "errors": card["errors"]},
                         ensure_ascii=False))
        return 1
    print(json.dumps(card, ensure_ascii=False, indent=2) if a.json else markdown(card))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
