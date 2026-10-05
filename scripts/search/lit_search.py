#!/usr/bin/env python3
"""Literature search with a reproducible record: the queries are built, run,
paged, deduplicated and counted by this script, never by a model.

    lit_search.py run        --plan plan.json --out <run dir> [--vault <vault>]
    lit_search.py snowball   --run <run dir> --keys K1 [K2 …] [--direction both|references|citations]
    lit_search.py retraction --run <run dir> [--mailto you@example.org]
    lit_search.py screen     --run <run dir> --decisions decisions.json
    lit_search.py show       --run <run dir> [--limit 60]

The model's part is the judgement around it: it writes `plan.json` (the
facets and their synonyms, the date window, the inclusion / exclusion /
scope criteria) before anything is fetched, and `decisions.json` (include or
exclude every candidate, with the reason and one sentence of justification)
after reading the candidates. Everything that is a count or a set operation
happens here, from the raw responses kept in `<run>/raw/` with their sha256.

plan.json
    {"description": "<verbatim>", "facets": [{"id": "A", "term": "…", "synonyms": ["…"]}],
     "sources": ["arxiv", "s2", "openalex", "dblp"], "from": "2024-01-01", "to": null,
     "arxiv_categories": ["quant-ph"], "per_query": 100, "anchors": 10,
     "include": ["…"], "exclude": ["…"], "scope_out": ["<Alcance: Fuera clause>", …]}

Sources and how each facet is queried
    arxiv     one OR-group over ti:/abs: per facet (+ categories, + submittedDate window),
              sorted by relevance, paged by 100 up to per_query
    s2        /paper/search takes plain keywords only: one query per term and synonym,
              `year=` window, paged by 100 up to per_query; plus one /paper/search/bulk
              anchor pass per facet (OR-group with `|`, sort=citationCount:desc, top `anchors`)
    openalex  /works?search=<"t1" OR "t2">, from/to_publication_date filter, paged
              (OPENALEX_API_KEY optional)
    crossref  /works?query=<term>, one per term and synonym, from/until-pub-date filter —
              the ACM / IEEE / Springer proceedings and journals (SC, IPDPS, ISC, QCE,
              PRX Quantum, …), with their DOI and venue (KAIRO_MAILTO: polite pool)
    dblp      on request only (not a default): one query per term; DBLP's API now
              answers with an anti-bot challenge page, which is recorded as a lost
              query and never worked around
Default sources: arxiv, s2, openalex, crossref.
A query whose source reports more matches than were fetched is `truncated`;
a query that failed after retries is `lost`. Both are listed as degraded
coverage, never hidden.

Dedup: records sharing a normalised DOI, an arXiv id, or a normalised title
(≥ 4 words) are one candidate; a preprint and its published version found
separately are merged and both identifiers kept. Each candidate keeps, per
facet, the term that matched it (the query term for per-term queries; for an
OR-group, the first facet term found in its title or abstract, else the
OR-group itself).

decisions.json
    {"<key>": {"decision": "include", "relevance": "alta|media|baja", "why": "<one sentence>"},
     "<key>": {"decision": "exclude", "reason": "fuera de tema|solo survey|fuera de alcance|
               relevancia baja|sin justificación|duplicado", "why": "…", "scope_clause": "…"}}
`screen` refuses a decisions file that leaves a candidate undecided, uses an
unknown key or reason, includes without a sentence, or excludes `fuera de
alcance` without the clause. It then writes `busqueda.md` (the Búsqueda
ejecutada block: criteria, verbatim queries with hits / available /
truncation, exact PRISMA counts, the degraded-coverage warning) and
`ranked.md` (the included list, the relevant-but-out-of-scope list).

Prints one JSON object. Exit: 0 ok · 1 error · 2 refused (bad input).
Standard library only.
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
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "citations"))
sys.path.insert(0, str(HERE.parent / "papers"))
import check_retraction  # noqa: E402
import net  # noqa: E402
import retraction  # noqa: E402

TOOL = "kairo/lit_search@1.0.0"
SOURCES = ("arxiv", "s2", "openalex", "crossref", "dblp")
# DBLP's API now sits behind an anti-bot challenge, which Kairo never works
# around: it stays available on request but is not a default source. Crossref
# covers the ACM / IEEE / Springer proceedings (SC, IPDPS, ISC, QCE, …) instead.
DEFAULT_SOURCES = ["arxiv", "s2", "openalex", "crossref"]
CROSSREF_SELECT = "DOI,title,author,issued,container-title,type,abstract,is-referenced-by-count"
ATOM = "{http://www.w3.org/2005/Atom}"
OS = "{http://a9.com/-/spec/opensearch/1.1/}"
ARX = "{http://arxiv.org/schemas/atom}"
S2 = "https://api.semanticscholar.org/graph/v1"
S2_FIELDS = "title,abstract,authors,year,publicationDate,externalIds,venue,citationCount,url"
OA_SELECT = ("id,doi,title,publication_year,publication_date,authorships,primary_location,locations,"
             "cited_by_count,type,abstract_inverted_index")
PAGE = 100
REASONS = ("fuera de tema", "solo survey", "fuera de alcance", "relevancia baja", "sin justificación", "duplicado")
RELEVANCE = ("alta", "media", "baja")

Fetch = Callable[[str, dict], bytes]


class Refused(Exception):
    pass


def default_fetch(url: str, headers: dict) -> bytes:
    return net.get(url, headers=headers)


def ws(s) -> str:
    return " ".join(str(s or "").split())


def norm_title(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    t = re.sub(r"<[^>]+>|\$[^$]*\$", " ", t)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def from_inverted_index(ii: dict | None) -> str:
    if not ii:
        return ""
    pos = {int(i): w for w, places in ii.items() for i in places}
    return " ".join(pos[i] for i in sorted(pos))


# --------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------

def load_plan(path: Path) -> dict:
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise Refused(f"cannot read the plan: {e}") from None
    facets = plan.get("facets") or []
    if not (1 <= len(facets) <= 6):
        raise Refused("the plan needs 1–6 facets")
    ids = set()
    for f in facets:
        if not f.get("id") or not ws(f.get("term")):
            raise Refused("every facet needs an id and a term")
        if f["id"] in ids:
            raise Refused(f"facet id {f['id']} repeated")
        ids.add(f["id"])
        f["synonyms"] = [ws(s) for s in f.get("synonyms") or [] if ws(s)]
    srcs = plan.get("sources") or list(DEFAULT_SOURCES)
    bad = [s for s in srcs if s not in SOURCES]
    if bad:
        raise Refused(f"unknown sources {bad}; use {SOURCES}")
    plan["sources"] = srcs
    for k in ("from", "to"):
        if plan.get(k) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", plan[k]):
            raise Refused(f"`{k}` must be YYYY-MM-DD")
    plan["per_query"] = int(plan.get("per_query") or 100)
    plan["anchors"] = int(plan.get("anchors") if plan.get("anchors") is not None else 10)
    for k in ("include", "exclude", "scope_out", "arxiv_categories"):
        plan[k] = list(plan.get(k) or [])
    if not ws(plan.get("description")):
        raise Refused("the plan needs the verbatim `description`")
    return plan


def terms(f: dict) -> list[str]:
    return [f["term"]] + f["synonyms"]


def _q(t: str) -> str:
    return f'"{t}"' if " " in t or "-" in t else t


def arxiv_query(f: dict, plan: dict, until: str) -> str:
    group = " OR ".join(f"ti:{_q(t)} OR abs:{_q(t)}" for t in terms(f))
    q = f"({group})"
    if plan["arxiv_categories"]:
        q += " AND (" + " OR ".join(f"cat:{c}" for c in plan["arxiv_categories"]) + ")"
    if plan.get("from"):
        q += f" AND submittedDate:[{plan['from'].replace('-', '')}0000 TO {until.replace('-', '')}2359]"
    return q


def s2_year(plan: dict) -> str:
    a = plan["from"][:4] if plan.get("from") else ""
    b = plan["to"][:4] if plan.get("to") else ""
    return f"{a}-{b}" if (a or b) else ""


def in_window(date: str | None, year, plan: dict) -> bool:
    d = (date or (f"{year}-12-31" if year else "")) or ""
    lo_d = date or (f"{year}-01-01" if year else "")
    if not d:
        return True                         # undated: kept, the screen decides
    if plan.get("from") and d < plan["from"]:
        return False
    if plan.get("to") and lo_d > plan["to"]:
        return False
    return True


# --------------------------------------------------------------------------
# Source adapters: raw bytes → (records, total available)   (pure, unit-tested)
# --------------------------------------------------------------------------

def parse_arxiv(raw: bytes) -> tuple[list[dict], int]:
    root = ET.fromstring(raw)
    total = int(root.findtext(f"{OS}totalResults") or 0)
    out = []
    for e in root.findall(f"{ATOM}entry"):
        rid = (e.findtext(f"{ATOM}id") or "")
        if "/api/errors" in rid:
            continue
        aid = retraction.normalize_arxiv(rid.rsplit("/abs/", 1)[-1])
        out.append({"title": ws(e.findtext(f"{ATOM}title")), "abstract": ws(e.findtext(f"{ATOM}summary")),
                    "authors": [ws(a.findtext(f"{ATOM}name")) for a in e.findall(f"{ATOM}author")],
                    "date": (e.findtext(f"{ATOM}published") or "")[:10] or None, "year": None,
                    "arxiv": aid, "doi": retraction.normalize_doi(e.findtext(f"{ARX}doi")),
                    "venue": ws(e.findtext(f"{ARX}journal_ref")) or None, "citations": None,
                    "url": f"https://arxiv.org/abs/{aid}" if aid else None})
    return out, total


def parse_s2(raw: bytes) -> tuple[list[dict], int]:
    data = json.loads(raw)
    out = []
    for p in data.get("data") or []:
        ext = p.get("externalIds") or {}
        doi = retraction.normalize_doi(ext.get("DOI"))
        aid = retraction.normalize_arxiv(ext.get("ArXiv"))
        if doi and retraction.is_arxiv_doi(doi):
            aid, doi = aid or retraction.arxiv_from_doi(doi), None
        out.append({"title": ws(p.get("title")), "abstract": ws(p.get("abstract")),
                    "authors": [ws(a.get("name")) for a in p.get("authors") or []],
                    "date": p.get("publicationDate"), "year": p.get("year"), "arxiv": aid, "doi": doi,
                    "venue": ws(p.get("venue")) or None, "citations": p.get("citationCount"),
                    "url": p.get("url"), "s2": p.get("paperId")})
    return out, int(data.get("total") or len(out))


def _oa_arxiv(w: dict) -> str | None:
    for loc in [w.get("primary_location") or {}] + list(w.get("locations") or []):
        m = re.search(r"arxiv\.org/(?:abs|pdf)/([^\s?#]+?)(?:v\d+)?(?:\.pdf)?$", loc.get("landing_page_url") or "")
        if m:
            return retraction.normalize_arxiv(m.group(1))
    return None


def parse_openalex(raw: bytes) -> tuple[list[dict], int]:
    data = json.loads(raw)
    out = []
    for w in data.get("results") or []:
        doi = retraction.normalize_doi(w.get("doi"))
        aid = _oa_arxiv(w)
        if doi and retraction.is_arxiv_doi(doi):
            aid, doi = aid or retraction.arxiv_from_doi(doi), None
        src = ((w.get("primary_location") or {}).get("source") or {})
        venue = src.get("display_name") if (src.get("type") or "") in ("journal", "conference") else None
        out.append({"title": ws(w.get("title")), "abstract": from_inverted_index(w.get("abstract_inverted_index")),
                    "authors": [ws((a.get("author") or {}).get("display_name")) for a in w.get("authorships") or []],
                    "date": w.get("publication_date"), "year": w.get("publication_year"), "arxiv": aid, "doi": doi,
                    "venue": venue, "citations": w.get("cited_by_count"), "url": w.get("id"),
                    "openalex": (w.get("id") or "").rsplit("/", 1)[-1] or None})
    return out, int((data.get("meta") or {}).get("count") or len(out))


def parse_dblp(raw: bytes) -> tuple[list[dict], int]:
    hits = (json.loads(raw).get("result") or {}).get("hits") or {}
    out = []
    for h in hits.get("hit") or []:
        i = h.get("info") or {}
        au = (i.get("authors") or {}).get("author") or []
        au = au if isinstance(au, list) else [au]
        doi = retraction.normalize_doi(i.get("doi"))
        aid = None
        if doi and retraction.is_arxiv_doi(doi):
            aid, doi = retraction.arxiv_from_doi(doi), None
        venue = i.get("venue")
        venue = ", ".join(venue) if isinstance(venue, list) else venue
        y = i.get("year")
        out.append({"title": ws(i.get("title")).rstrip("."), "abstract": "",
                    "authors": [ws(a.get("text") if isinstance(a, dict) else a) for a in au],
                    "date": None, "year": int(y) if str(y or "").isdigit() else None, "arxiv": aid, "doi": doi,
                    "venue": None if (venue or "").upper() == "CORR" else venue, "citations": None,
                    "url": i.get("ee") or i.get("url"), "dblp": i.get("key")})
    return out, int(hits.get("@total") or len(out))


def parse_crossref(raw: bytes) -> tuple[list[dict], int]:
    msg = json.loads(raw).get("message") or {}
    out = []
    for w in msg.get("items") or []:
        doi = retraction.normalize_doi(w.get("DOI"))
        aid = None
        if doi and retraction.is_arxiv_doi(doi):
            aid, doi = retraction.arxiv_from_doi(doi), None
        parts = ((w.get("issued") or {}).get("date-parts") or [[None]])[0] or [None]
        year = parts[0] if isinstance(parts[0], int) else None
        date = f"{parts[0]:04d}-{parts[1]:02d}-{parts[2]:02d}" if len(parts) == 3 and year else None
        abstract = ws(re.sub(r"<[^>]+>", " ", w.get("abstract") or ""))
        abstract = re.sub(r"^Abstract\s+", "", abstract)
        authors = [ws(f"{a.get('given', '')} {a.get('family', '')}") or ws(a.get("name")) for a in w.get("author") or []]
        out.append({"title": ws((w.get("title") or [""])[0]), "abstract": abstract, "authors": authors,
                    "date": date, "year": year, "arxiv": aid, "doi": doi,
                    "venue": ws((w.get("container-title") or [""])[0]) or None,
                    "citations": w.get("is-referenced-by-count"), "url": f"https://doi.org/{doi}" if doi else None})
    return out, int(msg.get("total-results") or len(out))


PARSERS = {"arxiv": parse_arxiv, "s2": parse_s2, "s2-anchor": parse_s2, "openalex": parse_openalex,
           "crossref": parse_crossref, "dblp": parse_dblp}


# --------------------------------------------------------------------------
# Running the queries
# --------------------------------------------------------------------------

def _oa_key() -> str:
    k = os.environ.get("OPENALEX_API_KEY")
    return f"&api_key={urllib.parse.quote(k)}" if k else ""


def _s2_headers() -> dict:
    k = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    return {"x-api-key": k} if k else {}


def page_urls(source: str, query: str, plan: dict, start: int, size: int) -> tuple[str, dict]:
    if source == "arxiv":
        return ("https://export.arxiv.org/api/query?search_query=" + urllib.parse.quote(query, safe="")
                + f"&start={start}&max_results={size}&sortBy=relevance&sortOrder=descending",
                {"Accept": "application/atom+xml"})
    if source == "s2":
        y = s2_year(plan)
        return (f"{S2}/paper/search?query={urllib.parse.quote(query)}&offset={start}&limit={size}"
                f"&fields={S2_FIELDS}" + (f"&year={y}" if y else ""), _s2_headers())
    if source == "s2-anchor":
        y = s2_year(plan)
        return (f"{S2}/paper/search/bulk?query={urllib.parse.quote(query)}&sort=citationCount:desc"
                f"&fields={S2_FIELDS}" + (f"&year={y}" if y else ""), _s2_headers())
    if source == "openalex":
        flt = []
        if plan.get("from"):
            flt.append(f"from_publication_date:{plan['from']}")
        if plan.get("to"):
            flt.append(f"to_publication_date:{plan['to']}")
        return ("https://api.openalex.org/works?search=" + urllib.parse.quote(query)
                + (f"&filter={','.join(flt)}" if flt else "") + f"&per-page={size}&page={start // size + 1}"
                + f"&select={OA_SELECT}" + _oa_key(), {})
    if source == "crossref":
        flt = []
        if plan.get("from"):
            flt.append(f"from-pub-date:{plan['from']}")
        if plan.get("to"):
            flt.append(f"until-pub-date:{plan['to']}")
        mailto = os.environ.get("KAIRO_MAILTO")          # Crossref's polite pool, optional
        return ("https://api.crossref.org/works?query=" + urllib.parse.quote(query)
                + (f"&filter={','.join(flt)}" if flt else "") + f"&rows={size}&offset={start}"
                + f"&select={CROSSREF_SELECT}" + (f"&mailto={urllib.parse.quote(mailto)}" if mailto else ""),
                {"Accept": "application/json"})
    if source == "dblp":
        return (f"https://dblp.org/search/publ/api?q={urllib.parse.quote(query)}&format=json&h={size}&f={start}", {})
    raise ValueError(source)


def build_queries(plan: dict, until: str) -> list[dict]:
    qs = []
    for f in plan["facets"]:
        ts = terms(f)
        for src in plan["sources"]:
            if src == "arxiv":
                qs.append({"facet": f["id"], "source": "arxiv", "pass": "relevance",
                           "query": arxiv_query(f, plan, until), "terms": ts})
            elif src == "s2":
                for t in ts:
                    qs.append({"facet": f["id"], "source": "s2", "pass": "relevance", "query": t, "terms": [t]})
                if plan["anchors"]:
                    qs.append({"facet": f["id"], "source": "s2-anchor", "pass": "anchor",
                               "query": "(" + " | ".join(f'"{t}"' for t in ts) + ")", "terms": ts})
            elif src == "openalex":
                qs.append({"facet": f["id"], "source": "openalex", "pass": "relevance",
                           "query": " OR ".join(f'"{t}"' for t in ts), "terms": ts})
            elif src in ("crossref", "dblp"):
                for t in ts:
                    qs.append({"facet": f["id"], "source": src, "pass": "relevance", "query": t, "terms": [t]})
    for i, q in enumerate(qs, 1):
        q["id"] = f"Q{i:03d}"
    return qs


def run_query(q: dict, plan: dict, raw_dir: Path, fetch: Fetch) -> list[dict]:
    """Fetch every page of one query, keep the bytes, return its records."""
    cap = plan["anchors"] if q["source"] == "s2-anchor" else plan["per_query"]
    parse = PARSERS[q["source"]]
    recs: list[dict] = []
    q.update(total=None, fetched=0, raw=[], error=None)
    start = 0
    while start < cap:
        size = min(PAGE, cap - start)
        url, headers = page_urls(q["source"], q["query"], plan, start, size)
        try:
            data = fetch(url, headers)
            if q["source"] != "arxiv" and data.lstrip()[:15].lower().startswith((b"<!doctype html", b"<html")):
                # a challenge page (e.g. DBLP's anti-bot check): never worked around
                raise ValueError("la fuente respondió con una página HTML (comprobación anti-bot o error), "
                                 "no con datos; no se fuerza")
            got, total = parse(data)
        except (net.HttpError, ET.ParseError, json.JSONDecodeError, ValueError) as e:
            q["error"] = net.redact(str(e))[:200]
            break
        name = f"{q['id']}-{len(q['raw']) + 1}.{'xml' if q['source'] == 'arxiv' else 'json'}"
        (raw_dir / name).write_bytes(data)
        q["raw"].append({"file": name, "url": net.redact(url), "sha256": hashlib.sha256(data).hexdigest()})
        q["total"] = total
        if q["source"] == "s2-anchor":
            got = got[:cap]
        recs.extend(got)
        if q["source"] == "s2-anchor" or len(got) < size or start + size >= total:
            break
        start += size
    q["fetched"] = len(recs)
    kept = [r for r in recs if in_window(r.get("date"), r.get("year"), plan)]
    q["outside_window"] = len(recs) - len(kept)
    q["truncated"] = bool(q["total"] and q["source"] != "s2-anchor" and q["total"] > q["fetched"])
    for rank, r in enumerate(kept, 1):
        r.update(query=q["id"], facet=q["facet"], source=q["source"].replace("-anchor", ""),
                 anchor=q["source"] == "s2-anchor", rank=rank, matched=matched_term(r, q))
    return kept


def matched_term(r: dict, q: dict) -> str:
    if len(q["terms"]) == 1:
        return q["terms"][0]
    hay = norm_title(r.get("title", "") + " " + r.get("abstract", ""))
    for t in q["terms"]:
        if norm_title(t) and f" {norm_title(t)} " in f" {hay} ":
            return t
    return q["query"]


# --------------------------------------------------------------------------
# Dedup
# --------------------------------------------------------------------------

def record_keys(r: dict) -> list[str]:
    ks = []
    if r.get("doi"):
        ks.append("doi:" + r["doi"])
    if r.get("arxiv"):
        ks.append("arxiv:" + r["arxiv"].lower())
    t = norm_title(r.get("title", ""))
    if len(t.split()) >= 4:
        ks.append("title:" + t)
    return ks


def dedup(records: list[dict]) -> list[dict]:
    parent: dict[int, int] = {}

    def find(i):
        while parent.setdefault(i, i) != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    owner: dict[str, int] = {}
    for i, r in enumerate(records):
        find(i)
        for k in record_keys(r):
            if k in owner:
                parent[find(i)] = find(owner[k])
            else:
                owner[k] = i
    groups: dict[int, list[dict]] = {}
    for i, r in enumerate(records):
        groups.setdefault(find(i), []).append(r)
    order = {"arxiv": 0, "openalex": 1, "crossref": 2, "s2": 3, "dblp": 4}
    out = []
    for rs in groups.values():
        rs = sorted(rs, key=lambda r: order.get(r["source"], 9))
        first = rs[0]
        dois = sorted({r["doi"] for r in rs if r.get("doi")})
        arx = sorted({r["arxiv"] for r in rs if r.get("arxiv")})
        years = sorted({int(r["year"] or (r["date"] or "")[:4]) for r in rs
                        if r.get("year") or (r.get("date") or "")[:4].isdigit()})
        venues = sorted({r["venue"] for r in rs if r.get("venue")})
        facets: dict[str, str] = {}
        for r in sorted(rs, key=lambda r: (r["facet"], r["rank"])):
            facets.setdefault(r["facet"], r["matched"])
        c = {"title": next((r["title"] for r in rs if r.get("title")), ""),
             "authors": next((r["authors"] for r in rs if r.get("authors")), []),
             "year": years[0] if years else None,
             "date": next((r["date"] for r in rs if r.get("date")), None),
             "doi": dois[0] if dois else None, "arxiv": arx[0] if arx else None,
             "venues": venues, "abstract": next((r["abstract"] for r in rs if r.get("abstract")), ""),
             "citations": max([r["citations"] for r in rs if r.get("citations") is not None], default=None),
             "url": first.get("url"), "facets": facets,
             "sources": sorted({r["source"] for r in rs}, key=lambda s: order.get(s, 9)),
             "queries": sorted({r["query"] for r in rs}), "anchor": any(r.get("anchor") for r in rs),
             "best_rank": min(r["rank"] for r in rs)}
        if len(dois) > 1 or len(arx) > 1:
            c["other_ids"] = {"doi": dois[1:], "arxiv": arx[1:]}
        c["key"] = ("doi:" + c["doi"]) if c["doi"] else ("arxiv:" + c["arxiv"]) if c["arxiv"] else \
            "t:" + hashlib.sha256(norm_title(c["title"]).encode()).hexdigest()[:12]
        out.append(c)
    out.sort(key=lambda c: (-len(c["facets"]), c["best_rank"], -(c["year"] or 0)))
    return out


def known_vault_keys(vault: Path | None) -> set[str]:
    if not vault or not (vault / "Papers").is_dir():
        return set()
    ks = set()
    sys.path.insert(0, str(HERE.parent / "security"))
    from send_guard import is_flagged
    for p in (vault / "Papers").glob("P-*.md"):
        if is_flagged(p):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for key, pat in (("doi:", r"^doi:\s*\"?([^\s\"]+)"), ("arxiv:", r"^arxiv:\s*\"?([^\s\"]+)")):
            m = re.search(pat, text, re.M)
            if m:
                v = retraction.normalize_doi(m.group(1)) if key == "doi:" else retraction.normalize_arxiv(m.group(1))
                if v:
                    ks.add(key + v.lower())
    return ks


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def save(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def cmd_run(plan_path: Path, out: Path, vault: Path | None, fetch: Fetch, today: str) -> dict:
    plan = load_plan(plan_path)
    if out.exists() and any(out.iterdir()):
        raise Refused(f"{out} is not empty: a run directory is written once")
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    until = plan.get("to") or today
    queries = build_queries(plan, until)
    records = []
    for q in queries:
        records.extend(run_query(q, plan, raw, fetch))
    cands = dedup(records)
    known = known_vault_keys(vault)
    for c in cands:
        c["in_vault"] = bool({f"doi:{c['doi']}" if c.get("doi") else "",
                              f"arxiv:{(c.get('arxiv') or '').lower()}" if c.get("arxiv") else ""} & known)
    save(out / "plan.json", {**plan, "tool": TOOL, "date": today})
    save(out / "queries.json", queries)
    save(out / "candidates.json", cands)
    return summary(queries, cands, out)


def summary(queries: list[dict], cands: list[dict], out: Path) -> dict:
    per_source: dict[str, int] = {}
    for q in queries:
        s = q["source"].replace("-anchor", "")
        per_source[s] = per_source.get(s, 0) + q["fetched"] - q.get("outside_window", 0)
    return {"tool": TOOL, "run": out.as_posix(), "queries": len(queries),
            "identified": per_source, "identified_total": sum(per_source.values()),
            "candidates": len(cands), "in_vault": sum(1 for c in cands if c.get("in_vault")),
            "lost": [f"{q['id']} {q['source']} faceta {q['facet']}: {q['error']}" for q in queries if q["error"]],
            "truncated": [f"{q['id']} {q['source']} faceta {q['facet']}: {q['fetched']} de {q['total']}"
                          for q in queries if q.get("truncated")]}


def snowball_terms_match(c: dict, plan: dict) -> dict[str, str]:
    hay = f" {norm_title(c.get('title', '') + ' ' + c.get('abstract', ''))} "
    out = {}
    for f in plan["facets"]:
        for t in terms(f):
            if norm_title(t) and f" {norm_title(t)} " in hay:
                out[f["id"]] = t
                break
    return out


def cmd_snowball(run: Path, keys: list[str], direction: str, fetch: Fetch) -> dict:
    plan, queries, cands = load(run / "plan.json"), load(run / "queries.json"), load(run / "candidates.json")
    by_key = {c["key"]: c for c in cands}
    missing = [k for k in keys if k not in by_key]
    if missing:
        raise Refused(f"unknown candidate keys: {missing}")
    need = 2 if len(plan["facets"]) > 1 else 1
    new_records = []
    for k in keys:
        c = by_key[k]
        pid = f"DOI:{c['doi']}" if c.get("doi") else f"arXiv:{c['arxiv']}" if c.get("arxiv") else None
        if not pid:
            continue
        for rel in (("references", "citations") if direction == "both" else (direction,)):
            q = {"id": f"S{len(queries) + 1:03d}", "facet": "*", "source": "s2", "pass": f"snowball-{rel}",
                 "query": f"{pid}/{rel}", "terms": [], "raw": [], "error": None, "total": None, "fetched": 0}
            # `fields` name the cited / citing paper's own fields (no prefix)
            url = f"{S2}/paper/{urllib.parse.quote(pid, safe=':/')}/{rel}?limit=500&fields={S2_FIELDS}"
            try:
                data = fetch(url, _s2_headers())
                items = json.loads(data).get("data") or []
            except (net.HttpError, json.JSONDecodeError, ValueError) as e:
                q["error"] = net.redact(str(e))[:200]
                queries.append(q)
                continue
            name = f"{q['id']}-1.json"
            (run / "raw" / name).write_bytes(data)
            q["raw"].append({"file": name, "url": net.redact(url), "sha256": hashlib.sha256(data).hexdigest()})
            papers = [it.get("citedPaper" if rel == "references" else "citingPaper") or {} for it in items]
            recs, _ = parse_s2(json.dumps({"data": papers}).encode())
            q["fetched"] = q["total"] = len(recs)
            kept = 0
            for rank, r in enumerate(recs, 1):
                fm = snowball_terms_match(r, plan)
                if len(fm) < need or not in_window(r.get("date"), r.get("year"), plan):
                    continue
                kept += 1
                for fid, t in fm.items():
                    new_records.append({**r, "query": q["id"], "facet": fid, "source": "s2", "anchor": False,
                                        "rank": rank, "matched": t, "snowball_from": k})
            q["kept"] = kept
            queries.append(q)
    # re-merge: existing candidates are re-expanded into one record per facet
    old = []
    for c in cands:
        for fid, t in c["facets"].items():
            old.append({"title": c["title"], "authors": c["authors"], "year": c["year"], "date": c["date"],
                        "doi": c["doi"], "arxiv": c["arxiv"], "venue": (c["venues"] or [None])[0],
                        "abstract": c["abstract"], "citations": c["citations"], "url": c["url"],
                        "query": c["queries"][0], "facet": fid, "source": c["sources"][0], "anchor": c["anchor"],
                        "rank": c["best_rank"], "matched": t})
    merged = dedup(old + new_records)
    prev = {c["key"]: c for c in cands}
    for c in merged:
        p = prev.get(c["key"])
        if p:
            c["queries"] = sorted(set(c["queries"]) | set(p["queries"]))
            c["sources"] = sorted(set(c["sources"]) | set(p["sources"]))
            c["in_vault"] = p.get("in_vault", False)
        else:
            c["snowball"] = True
    save(run / "queries.json", queries)
    save(run / "candidates.json", merged)
    stale = run / "retraction.json"
    if stale.exists():
        stale.unlink()                      # new candidates: the retraction check must run again
    return {"tool": TOOL, "added": sum(1 for c in merged if c.get("snowball")), "candidates": len(merged),
            "next": "retraction"}


def cmd_retraction(run: Path, mailto: str | None) -> dict:
    cands = load(run / "candidates.json")
    res = check_retraction.run([{"id": c["key"], "doi": c.get("doi"), "arxiv": c.get("arxiv")} for c in cands], mailto)
    by = {r["id"]: r for r in res["results"]}
    for c in cands:
        r = by.get(c["key"]) or {}
        c["retraction"] = {"status": r.get("status", "clear"), "evidence": r.get("evidence", []),
                           "notice_for": r.get("notice_for", [])}
    save(run / "candidates.json", cands)
    save(run / "retraction.json", res)
    flagged = [c["key"] for c in cands if c["retraction"]["status"] in ("retracted", "withdrawn")]
    return {"tool": TOOL, "counts": res["counts"], "removed": flagged,
            "concern": [c["key"] for c in cands if c["retraction"]["status"] == "concern"]}


def validate(cands: list[dict], decisions: dict, plan: dict) -> list[str]:
    errs = []
    keys = {c["key"] for c in cands}
    auto = {c["key"] for c in cands if (c.get("retraction") or {}).get("status") in ("retracted", "withdrawn")}
    for k in decisions:
        if k not in keys:
            errs.append(f"{k}: not a candidate of this run")
    for c in cands:
        k = c["key"]
        d = decisions.get(k)
        if k in auto:
            continue
        if not d:
            errs.append(f"{k}: no decision")
            continue
        if d.get("decision") == "include":
            if d.get("relevance") not in RELEVANCE:
                errs.append(f"{k}: include needs relevance {RELEVANCE}")
            if len(ws(d.get("why")).split()) < 5:
                errs.append(f"{k}: include needs one sentence saying why (facets, contribution)")
        elif d.get("decision") == "exclude":
            if d.get("reason") not in REASONS:
                errs.append(f"{k}: exclude reason must be one of {REASONS}")
            if d.get("reason") == "fuera de alcance":
                cl = ws(d.get("scope_clause"))
                if not cl or (plan["scope_out"] and cl not in [ws(s) for s in plan["scope_out"]]):
                    errs.append(f"{k}: «fuera de alcance» must quote one of the plan's scope_out clauses")
                if len(ws(d.get("why")).split()) < 5:
                    errs.append(f"{k}: a relevant-but-out-of-scope paper needs its relevance sentence")
        else:
            errs.append(f"{k}: decision must be include or exclude")
    return errs


def cmd_screen(run: Path, decisions_path: Path) -> dict:
    plan, queries, cands = load(run / "plan.json"), load(run / "queries.json"), load(run / "candidates.json")
    if not (run / "retraction.json").is_file():
        raise Refused("run `retraction` before `screen`: a retracted paper must never be screened in")
    try:
        decisions = load(decisions_path)
    except (OSError, json.JSONDecodeError) as e:
        raise Refused(f"cannot read decisions: {e}") from None
    errs = validate(cands, decisions, plan)
    if errs:
        raise Refused("decisions incomplete or invalid:\n" + "\n".join(errs[:50]))
    retr = load(run / "retraction.json")
    for c in cands:
        st = (c.get("retraction") or {}).get("status")
        c["screen"] = ({"decision": "exclude", "reason": "retractado/retirado", "why": "; ".join(
            c["retraction"]["evidence"])} if st in ("retracted", "withdrawn") else decisions[c["key"]])
    save(run / "screened.json", cands)
    identified: dict[str, int] = {}
    for q in queries:
        s = q["source"].replace("-anchor", "")
        identified[s] = identified.get(s, 0) + q.get("fetched", 0) - q.get("outside_window", 0)
    tally = {r: 0 for r in REASONS + ("retractado/retirado",)}
    for c in cands:
        if c["screen"]["decision"] == "exclude":
            tally[c["screen"]["reason"]] += 1
    n_retr = tally["retractado/retirado"]
    counts = {"identificados": identified, "identificados_total": sum(identified.values()),
              "tras_deduplicacion": len(cands), "tras_retraccion": len(cands) - n_retr,
              "retraccion": retr["counts"],
              "incluidos": sum(1 for c in cands if c["screen"]["decision"] == "include"),
              "motivos": tally,
              "relevante_fuera_de_alcance": tally["fuera de alcance"]}
    (run / "busqueda.md").write_text(busqueda_md(plan, queries, counts), encoding="utf-8", newline="\n")
    (run / "ranked.md").write_text(ranked_md(cands, plan), encoding="utf-8", newline="\n")
    return {"tool": TOOL, "counts": counts, "busqueda": (run / "busqueda.md").as_posix(),
            "ranked": (run / "ranked.md").as_posix()}


def degraded_lines(queries: list[dict]) -> list[str]:
    out = []
    for q in queries:
        what = "pase-ancla" if q["pass"] == "anchor" else "pase de relevancia" if q["pass"] == "relevance" \
            else q["pass"]
        if q.get("error"):
            out.append(f"- Faceta {q['facet']} — {what} en {q['source'].replace('-anchor', '')} perdido "
                       f"({q['error']}). Consulta {q['id']}: los papers que solo esta consulta habría traído faltan.")
        elif q.get("truncated"):
            out.append(f"- Faceta {q['facet']} — {what} en {q['source']} truncado: {q['fetched']} de "
                       f"{q['total']} resultados (consulta {q['id']}). Sube `per_query` o estrecha la faceta.")
    return out


def busqueda_md(plan: dict, queries: list[dict], counts: dict) -> str:
    L = [f"### Búsqueda ejecutada — {plan['date']}", ""]
    deg = degraded_lines(queries)
    if deg:
        L += ["## ⚠️ Cobertura degradada", *deg, ""]
    L += [f"**Registro:** generado por {TOOL}; respuestas originales y sha256 en el directorio de la ejecución "
          "(`raw/`, `queries.json`).", "",
          f"**Descripción original:** {plan['description']}", "",
          "**Facetas:**", "", "| faceta | término | sinónimos |", "|---|---|---|"]
    L += [f"| {f['id']} | {f['term']} | {', '.join(f['synonyms'])} |" for f in plan["facets"]]
    window = f"{plan.get('from') or 'sin límite'} → {plan.get('to') or plan['date']}"
    L += ["", f"**Ventana de fechas:** {window}" + (f" · **Categorías arXiv:** {', '.join(plan['arxiv_categories'])}"
                                                  if plan["arxiv_categories"] else ""),
          "**Criterios de inclusión:** " + ("; ".join(plan["include"]) or "—"),
          "**Criterios de exclusión:** " + ("; ".join(plan["exclude"]) or "—"),
          "**Fuera de alcance:** " + ("; ".join(plan["scope_out"]) or "—"),
          f"**Fuentes:** {', '.join(plan['sources'])} · hasta {plan['per_query']} resultados por consulta, "
          f"{plan['anchors']} anclas por faceta", "",
          "**Consultas (verbatim):**", "",
          "| id | faceta | fuente | pase | query | hits | disponibles | estado |", "|---|---|---|---|---|---|---|---|"]
    for q in queries:
        state = "perdida" if q.get("error") else "truncada" if q.get("truncated") else "completa"
        qtext = q["query"].replace("|", "\\|")
        L.append(f"| {q['id']} | {q['facet']} | {q['source']} | {q['pass']} | `{qtext}` | {q.get('fetched', 0)} | "
                 f"{q.get('total') if q.get('total') is not None else '—'} | {state} |")
    c = counts
    L += ["", "**Conteos** (calculados por el script a partir de las respuestas guardadas):",
          "- Identificados: " + ", ".join(f"{k} {v}" for k, v in c["identificados"].items())
          + f" (total {c['identificados_total']})",
          f"- Tras deduplicación: {c['tras_deduplicacion']}",
          f"- Tras cribado por retracción/retirada: {c['tras_retraccion']}",
          f"  - Crossref (DOI): revisados {c['retraccion']['crossref']['checked']}, retirados "
          f"{c['retraccion']['crossref']['removed']}, perdidos {c['retraccion']['crossref']['lost']}",
          f"  - arXiv (withdrawn): revisados {c['retraccion']['arxiv']['checked']}, retirados "
          f"{c['retraccion']['arxiv']['removed']}, perdidos {c['retraccion']['arxiv']['lost']}",
          f"- Incluidos: {c['incluidos']}",
          "- Motivos de descarte: " + ", ".join(f"{k} {v}" for k, v in c["motivos"].items()),
          f"- Relevante pero fuera de alcance: {c['relevante_fuera_de_alcance']}", ""]
    return "\n".join(L)


def _line(c: dict, today: str) -> str:
    ids = []
    if c.get("arxiv"):
        ids.append(f"arXiv:{c['arxiv']}")
    if c.get("doi"):
        ids.append(f"DOI:{c['doi']}")
    fac = ", ".join(f'{k}: "{v}"' for k, v in sorted(c["facets"].items()))
    cit = ""
    if c.get("citations") is not None:
        recent = c.get("date") and c["date"] >= (_dt.date.fromisoformat(today) - _dt.timedelta(days=548)).isoformat()
        cit = f" · {c['citations']} citas" + (" (reciente: señal poco fiable)" if recent else "")
    venue = f" · publicado en {', '.join(c['venues'])}" if c.get("venues") and c.get("arxiv") else \
        (f" · {', '.join(c['venues'])}" if c.get("venues") else "")
    anchor = " · *candidato ancla por citas, no por relevancia directa*" if c.get("anchor") else ""
    vault = " · **ya en el vault**" if c.get("in_vault") else ""
    return (f"- **{c['title']}** ({c.get('year') or 's. f.'}) — {', '.join(ids) or c.get('url') or ''}"
            f"{venue}{cit}{anchor}{vault}\n  Facetas: {fac}. `{c['key']}`\n  {c['screen'].get('why', '')}")


def ranked_md(cands: list[dict], plan: dict) -> str:
    rel = {"alta": 0, "media": 1, "baja": 2}
    inc = [c for c in cands if c["screen"]["decision"] == "include"]
    inc.sort(key=lambda c: (rel[c["screen"]["relevance"]], -len(c["facets"]), -(c.get("year") or 0)))
    out = ["## Candidatos incluidos", ""] + [_line(c, plan["date"]) for c in inc]
    scope = [c for c in cands if c["screen"].get("reason") == "fuera de alcance"]
    if scope:
        out += ["", "### Relevante pero fuera de alcance", ""]
        out += [_line(c, plan["date"]) + f"\n  Cláusula: «{c['screen']['scope_clause']}»" for c in scope]
    return "\n".join(out) + "\n"


def cmd_show(run: Path, limit: int) -> dict:
    cands = load(run / "candidates.json")
    return {"tool": TOOL, "candidates": [
        {"key": c["key"], "title": c["title"], "year": c["year"], "facets": c["facets"], "sources": c["sources"],
         "venues": c["venues"], "anchor": c["anchor"], "in_vault": c.get("in_vault"),
         "retraction": (c.get("retraction") or {}).get("status"),
         "abstract": c["abstract"][:1200]} for c in cands[:limit]], "total": len(cands)}


def main(argv: list[str] | None = None, fetch: Fetch = default_fetch, today: str | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--plan", type=Path, required=True)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--vault", type=Path)
    s = sub.add_parser("snowball")
    s.add_argument("--run", type=Path, required=True)
    s.add_argument("--keys", nargs="+", required=True)
    s.add_argument("--direction", choices=("both", "references", "citations"), default="both")
    rt = sub.add_parser("retraction")
    rt.add_argument("--run", type=Path, required=True)
    rt.add_argument("--mailto")
    sc = sub.add_parser("screen")
    sc.add_argument("--run", type=Path, required=True)
    sc.add_argument("--decisions", type=Path, required=True)
    sh = sub.add_parser("show")
    sh.add_argument("--run", type=Path, required=True)
    sh.add_argument("--limit", type=int, default=60)
    a = p.parse_args(argv)
    today = today or _dt.date.today().isoformat()
    try:
        if a.cmd == "run":
            out = cmd_run(a.plan, a.out, a.vault, fetch, today)
        elif a.cmd == "snowball":
            out = cmd_snowball(a.run, a.keys, a.direction, fetch)
        elif a.cmd == "retraction":
            out = cmd_retraction(a.run, a.mailto)
        elif a.cmd == "screen":
            out = cmd_screen(a.run, a.decisions)
        else:
            out = cmd_show(a.run, a.limit)
    except Refused as e:
        print(json.dumps({"tool": TOOL, "refused": str(e)}, ensure_ascii=False))
        return 2
    except (OSError, ValueError, KeyError) as e:
        print(json.dumps({"tool": TOOL, "error": net.redact(str(e))}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
