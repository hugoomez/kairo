#!/usr/bin/env python3
"""Resolve `Papers/` notes against public bibliographic sources.

Proves that every reference Kairo relies on (1) exists, (2) matches its note's
metadata, and (3) is not retracted or withdrawn. Complements create-project's
locator re-verification (which checks section citations, not the paper).

Sources (all public):
  1. OpenAlex (primary) -- singleton lookup `GET /works/doi:<doi>` (the note's
     DOI, else the arXiv DataCite DOI 10.48550/arXiv.<id>); last resort for a
     note with neither id: `GET /works?filter=title.search:<title>` and the hit
     must pass the metadata match. Checked 2026-09-24 against
     help.openalex.org/api/authentication, /access/pricing, /access/example-costs
     and live response headers: an API key has been expected since
     2026-02-13, but keyless calls still work on a small budget
     ($0.10/day keyless, $1/day with a free key); singleton gets are FREE
     (`X-RateLimit-Cost-USD: 0`), list/filter/search cost $0.001 each; the hard
     limit is 100 req/s; 429 = budget exhausted or >100 req/s. The key is read
     from env OPENALEX_API_KEY and sent as the `api_key` query parameter; it is
     never printed (every URL in a message goes through net.redact).
     `is_retracted` is read as an extra retraction signal.
  2. Crossref by DOI (metadata + retraction/withdrawal via retraction.py).
  3. arXiv API by arXiv id (metadata + withdrawal via retraction.py), batched.
  4. Semantic Scholar `/graph/v1/paper/DOI:..|arXiv:..` (or `/paper/search/match`
     by title) -- only when OpenAlex did not produce a matching record. Optional
     SEMANTIC_SCHOLAR_API_KEY is sent as `x-api-key`.
  429/406/5xx: retried with backoff, 3 attempts, then that source is LOST for
  that note (reported, never silently treated as "not found"). An arXiv answer
  with no entry for an id the note declares is LOST too (the withdrawal check
  did not run).

Metadata match (note vs each source record found by identifier):
  title   normalized (NFKD, accents stripped, casefold, HTML/LaTeX/punctuation
          removed, leading RETRACTED:/WITHDRAWN: dropped). equal -> exact;
          difflib ratio >= 0.90, or token Jaccard >= 0.80, or one title is a
          >=3-word prefix of the other (subtitle dropped) -> close; else differs.
  author  first-author FAMILY name only (given names never compared): 'Last,
          First' / 'First [particles] Last' ('Laurens van der Maaten' -> 'van
          der Maaten') / 'Last F.'; Crossref's `family` when present. Whole
          tokens, accent-insensitive, particles and hyphens ignored, compound
          surnames contained ('Márquez' ~ 'García Márquez'); no suffix rule
          ('Chan' != 'Buchan'). Source's first author -> exact; among its first
          3 -> close (order differs); else differs. Missing on the source ->
          close.
  year    equal -> exact; off by one (preprint vs proceedings) -> close;
          >= 2 -> differs. Missing on the source -> close.
  A NOTE missing its first author or year cannot prove a match: `unresolved`,
  flagged `importante` "note lacks author/year -- cannot prove match".
  Level:  all exact -> exact; any differs -> mismatch; else close.
  A note's overall match is the WORST level over the sources that returned a
  record for its identifiers. A mismatch is the fingerprint of a "chimeric"
  citation (the title of one real paper with the authors/year of another) and
  is a failure.
  OpenAlex title-only mismatch (`source_error`): accepted as `close` ONLY when
  a registrar (Crossref / arXiv) record for the SAME identifier -- the one
  OpenAlex was fetched by, or a DOI / arXiv id OpenAlex declares for itself --
  matches the note. Flagged `importante`; --gate still fails (exit 2) until the
  researcher confirms it by hand.
  Identifier conflict: the note's DOI and arXiv id pointing to different works
  (an arXiv DOI of another id; the arXiv entry's `doi` or OpenAlex's `doi` /
  `ids` naming a different identifier; or the records behind the two ids not
  matching each other) -> `mismatch`, crítico.

Status per note (precedence top-down):
  skipped_send_never  note has `send: never` -- nothing is sent anywhere; the
                      output carries only the id.
  mismatch            some source's record for the note's id does not match.
  retracted/withdrawn any source flags it (concern is reported, not a status).
  resolved            OpenAlex record found, match exact/close.
  unresolved          no OpenAlex record (maybe a fallback confirmed it -- see
                      evidence), OpenAlex could not be reached, or the note
                      lacks author/year.

Fields written with --write (never without it), per docs/v3-interfaces.md §1c:
  resolved: true|false       true only with a matching OpenAlex record, so
                             `resolved: true` <=> `openalex_id: W\\d+`. A paper
                             confirmed only by fallbacks is resolved: false.
                             mismatch -> false (ambiguous identity).
                             retracted/withdrawn with an OpenAlex record -> true
                             (the paper exists; that is what §1c records).
  openalex_id: W...          empty when resolved is false.
  resolution_checked: YYYY-MM-DD
  Block-A extension fields (not in §1c):
  resolution_status: resolved|unresolved|mismatch|retracted|withdrawn
  resolution_match: exact|close|mismatch  (empty when no record matched)
  resolution_evidence: "<one line: which sources, what differed/flagged>"
  send: never notes are never written.
  OpenAlex LOST (unreachable / budget): resolved, openalex_id and
  resolution_checked are NOT written -- previous values stay, or stay absent
  on a never-checked note (§1c: false means "checked and not found"). Nor is
  resolution_status / resolution_match, unless a registrar gave a definite
  retracted / withdrawn (then those two are written) or the ids conflict
  (mismatch, written in full). The loss is appended to resolution_evidence as
  "last check LOST <date>: ..." (replacing an earlier LOST note).

Usage:
    python resolve_refs.py --papers <vault>/Papers                     # report all
    python resolve_refs.py --papers <vault>/Papers --only P-0001 P-0002 --json
    python resolve_refs.py --papers <vault>/Papers --only P-0015 --write
    python resolve_refs.py --papers <vault>/Papers --gate --only P-0001 P-0004
    python resolve_refs.py --version

Exit codes:
  without --gate: 0 ok (report produced; flags are in the report), 1 error
                  (could not run), 2 invalid input (bad --papers, unknown --only id).
  with --gate:    0 every selected note is `resolved` (exact/close, not
                  retracted/withdrawn), live-checked now -- stored fields are
                  never trusted; 2 at least one selected note fails
                  (incl. send: never, fallback-only, not found, mismatch,
                  retracted, withdrawn, an OpenAlex `source_error` -- the
                  researcher confirms it by hand -- or a note lacking
                  author/year); 1 error -- could not run, or the only
                  failures are lookups LOST to errors/budget (OpenAlex, or the
                  Crossref/arXiv retraction check) -- cannot prove either way. Invalid input is also 1 here
                  (the gate could not run).
Standard library only.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import json
import os
import re
import sys
import unicodedata
import urllib.parse
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import net  # noqa: E402
import retraction  # noqa: E402
import vaultnotes as vn  # noqa: E402

__version__ = "1.0.0"

OPENALEX = "https://api.openalex.org"
OPENALEX_SELECT = "id,doi,display_name,title,publication_year,authorships,is_retracted,ids,type"
S2 = "https://api.semanticscholar.org/graph/v1"
S2_FIELDS = "title,year,authors,externalIds,venue"
KEY_HELP = ("get a free OpenAlex API key at https://openalex.org/settings/api and export "
            "OPENALEX_API_KEY in the environment that runs Claude Code (never commit it)")

REGISTRARS = ("arxiv", "crossref")      # the id's own registry: authoritative for its metadata
STATUSES = ("resolved", "unresolved", "mismatch", "retracted", "withdrawn")
LEVEL_RANK = {"exact": 2, "close": 1, "mismatch": 0}


# --------------------------------------------------------------------------- #
# Normalization and matching (pure -- unit-tested)
# --------------------------------------------------------------------------- #

_HTML = re.compile(r"<[^>]+>")
_LATEX_CMD = re.compile(r"\\[a-zA-Z]+\*?(\{([^{}]*)\})?")
_PREFIX = re.compile(r"^\s*(retracted|withdrawn|removed)\s*[:\-]\s*", re.IGNORECASE)


def fold(s: str) -> str:
    """Accent-strip + casefold, keep only letters/digits and single spaces."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("ß", "ss").replace("ø", "o").replace("Ø", "o").replace("ł", "l").replace("Ł", "l")
    s = s.replace("æ", "ae").replace("Æ", "ae").replace("đ", "d").replace("ı", "i")
    s = s.casefold()
    out = []
    for ch in s:
        cat = unicodedata.category(ch)
        out.append(ch if cat[0] in "LN" else " ")
    return " ".join("".join(out).split())


def normalize_title(t: str) -> str:
    t = _HTML.sub(" ", t or "")
    t = _LATEX_CMD.sub(lambda m: m.group(2) or " ", t)
    t = t.replace("$", " ").replace("{", "").replace("}", "")
    t = _PREFIX.sub("", t)
    return fold(t)


def title_level(note: str, source: str) -> tuple[str, float]:
    """(exact | close | differs, similarity score in [0,1])."""
    a, b = normalize_title(note), normalize_title(source)
    if not a or not b:
        return "missing", 0.0
    if a == b:
        return "exact", 1.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    ta, tb = a.split(), b.split()
    sa, sb = set(ta), set(tb)
    jac = len(sa & sb) / len(sa | sb)
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    prefix = len(short) >= 3 and long_[:len(short)] == short
    score = max(ratio, jac)
    if ratio >= 0.90 or jac >= 0.80 or prefix:
        return "close", round(max(score, 0.9 if prefix else score), 3)
    return "differs", round(score, 3)


# lowercase surname particles ("van der Maaten", "de la Cruz", "bin Talal")
PARTICLES = {"van", "von", "der", "den", "de", "del", "della", "degli", "da", "di", "du", "dos", "das",
             "do", "le", "la", "les", "ten", "ter", "te", "bin", "ibn", "al", "el", "zu", "af", "av"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
_INITIAL = re.compile(r"^(?:[^\W\d_]\.?-?)+$")      # "A." / "A" / "A.B." / "J.-P."


def _is_initials(tok: str) -> bool:
    return bool(_INITIAL.match(tok)) and len(tok.replace(".", "").replace("-", "")) <= 2


def surname_of(author: str) -> str:
    """The family name only. 'Last, First' -> 'Last' (whatever its length);
    'First Middle Last' -> 'Last' plus any preceding particles ('Laurens van der
    Maaten' -> 'van der Maaten'); trailing 'Jr.' / 'III' dropped; 'Last F.'
    (initials after the surname) -> 'Last'. Never returns a given name."""
    a = " ".join((author or "").split())
    if "," in a:
        return a.split(",", 1)[0].strip()
    parts = a.split()
    while len(parts) > 1 and fold(parts[-1]) in SUFFIXES:
        parts.pop()
    if not parts:
        return ""
    if len(parts) > 1 and not _is_initials(parts[0]) and all(_is_initials(x) for x in parts[1:]):
        return parts[0]                                   # "Vaswani A." / "Kipf T. N."
    i = len(parts) - 1
    while i > 1 and fold(parts[i - 1]) in PARTICLES:
        i -= 1
    return " ".join(parts[i:])


def family_matches(a: str, b: str) -> bool:
    """Two FAMILY names (already extracted), accent-insensitive. Equal ignoring
    spaces/hyphens ('Ben-David' / 'Ben David' / 'BenDavid'), equal once leading
    particles are dropped ('Maaten' / 'van der Maaten'), or one's tokens appear
    contiguously in the other's (compound surnames: 'Márquez' / 'García
    Márquez'). Whole tokens only -- 'Chan' never matches 'Buchan'."""
    ta, tb = fold(a).split(), fold(b).split()
    if not ta or not tb:
        return False
    if "".join(ta) == "".join(tb):
        return True

    def core(tk):
        k = 0
        while k < len(tk) - 1 and tk[k] in PARTICLES:
            k += 1
        return tk[k:]
    ca, cb = core(ta), core(tb)
    if "".join(ca) == "".join(cb):
        return True
    short, long_ = (ca, cb) if len(ca) <= len(cb) else (cb, ca)
    return any(long_[i:i + len(short)] == short for i in range(len(long_) - len(short) + 1))


def surname_matches(surname: str, name: str, family: str | None = None) -> bool:
    """Does the note's first-author family name match a source author? The
    source's `family` field is used when it has one, else the family name
    parsed from `name` -- given names are never compared."""
    if not fold(surname):
        return False
    return family_matches(surname, family if family else surname_of(name))


@dataclass
class SourceRecord:
    """A candidate record normalized from any source."""
    source: str                    # openalex | crossref | arxiv | semantic_scholar
    title: str = ""
    authors: list[dict] = field(default_factory=list)   # [{"name": ..., "family": ...}]
    year: int | None = None
    found_by: str = ""             # doi | arxiv-doi | arxiv | title-search
    openalex_id: str | None = None
    id_key: str = ""               # identifier it was fetched by: "doi:<doi>" | "arxiv:<id>"; "" = title search
    raw: dict = field(default_factory=dict, repr=False)


def author_level(note_first_surname: str, rec_authors: list[dict]) -> str:
    if not note_first_surname or not rec_authors:
        return "missing"
    first = rec_authors[0]
    if surname_matches(note_first_surname, first.get("name", ""), first.get("family")):
        return "exact"
    for a in rec_authors[1:3]:
        if surname_matches(note_first_surname, a.get("name", ""), a.get("family")):
            return "close"
    return "differs"


def year_level(note_year: int | None, rec_year: int | None) -> str:
    if note_year is None or rec_year is None:
        return "missing"
    d = abs(note_year - rec_year)
    return "exact" if d == 0 else "close" if d == 1 else "differs"


def match_record(note: dict, rec: SourceRecord) -> tuple[str, list[dict]]:
    """(exact | close | mismatch, diff entries for every field that is not exact)."""
    t_lvl, t_score = title_level(note.get("title", ""), rec.title)
    note_sn = surname_of(note["authors"][0]) if note.get("authors") else ""
    a_lvl = author_level(note_sn, rec.authors)
    y_lvl = year_level(note.get("year"), rec.year)
    rec_first = rec.authors[0].get("name", "") if rec.authors else ""
    diff = []
    for fld, lvl, nv, sv in (
        ("title", t_lvl, note.get("title", ""), rec.title),
        ("first_author", a_lvl, note_sn, rec_first),
        ("year", y_lvl, note.get("year"), rec.year),
    ):
        if lvl != "exact":
            d = {"field": fld, "level": lvl, "note": nv, "source": sv, "source_name": rec.source}
            if fld == "title":
                d["similarity"] = t_score
            diff.append(d)
    levels = {t_lvl, a_lvl, y_lvl}
    if "differs" in levels:
        return "mismatch", diff
    if levels == {"exact"}:
        return "exact", diff
    return "close", diff


def worst(levels: list[str]) -> str | None:
    return min(levels, key=lambda lv: LEVEL_RANK[lv]) if levels else None


# --------------------------------------------------------------------------- #
# Identifier consistency (pure -- unit-tested)
# --------------------------------------------------------------------------- #

def key_for_doi(doi: str | None) -> str:
    """'doi:<doi>', or 'arxiv:<id>' for an arXiv DataCite DOI; '' when none."""
    d = retraction.normalize_doi(doi)
    if not d:
        return ""
    return f"arxiv:{retraction.arxiv_from_doi(d)}" if retraction.is_arxiv_doi(d) else f"doi:{d}"


def key_for_arxiv(aid: str | None) -> str:
    a = retraction.normalize_arxiv(aid)
    return f"arxiv:{a.lower()}" if a else ""


def declared_keys(rec: SourceRecord) -> set[str]:
    """Identifiers a record declares for itself (OpenAlex `doi` / `ids.doi` /
    `ids.arxiv`; arXiv entry `doi`)."""
    raw = rec.raw or {}
    out = {key_for_doi(raw.get("doi"))}
    ids = raw.get("ids") or {}
    if isinstance(ids, dict):
        out.add(key_for_doi(ids.get("doi")))
        out.add(key_for_arxiv(ids.get("arxiv")))
    out.discard("")
    return out


def note_keys(note: dict) -> dict[str, str]:
    """{'doi': 'doi:<doi>', 'arxiv': 'arxiv:<id>'} for the identifiers the note
    declares (an arXiv DataCite DOI in `doi:` stands for `arxiv:` when that is empty)."""
    out = {}
    dk, ak = key_for_doi(note.get("doi")), key_for_arxiv(note.get("arxiv"))
    if dk.startswith("doi:"):
        out["doi"] = dk
    if ak:
        out["arxiv"] = ak
    elif dk.startswith("arxiv:"):
        out["arxiv"] = dk
    return out


def rec_as_note(rec: SourceRecord) -> dict:
    first = rec.authors[0] if rec.authors else None
    fam = (first.get("family") or surname_of(first.get("name", ""))) if first else ""
    return {"title": rec.title, "authors": [f"{fam}, "] if fam else [], "year": rec.year}


def identifier_conflicts(note: dict, records: list[SourceRecord]) -> list[str]:
    """Evidence that the note's DOI and arXiv id point to DIFFERENT works:
    an arXiv DOI naming another arXiv id; a record fetched by one identifier
    declaring a different value for the other (arXiv entry `doi`, OpenAlex
    `doi` / `ids`); or the records behind the two identifiers not matching
    each other (the registrar's record stands for its identifier when present)."""
    msgs: list[str] = []
    nd, na = retraction.normalize_doi(note.get("doi")), retraction.normalize_arxiv(note.get("arxiv"))
    if nd and retraction.is_arxiv_doi(nd) and na and retraction.arxiv_from_doi(nd) != na.lower():
        msgs.append(f"doi {nd} is the arXiv DOI of {retraction.arxiv_from_doi(nd)}, but arxiv: is {na}")
    mine = note_keys(note)
    for rec in records:
        if not rec.id_key:
            continue                       # title-search hits are not tied to either identifier
        fetched_kind = rec.id_key.split(":", 1)[0]
        for k in sorted(declared_keys(rec)):
            kind = k.split(":", 1)[0]
            if kind != fetched_kind and kind in mine and k != mine[kind]:
                msgs.append(f"{rec.source} record for {rec.id_key} declares {k}, but the note has {mine[kind]}")
    reps: dict[str, SourceRecord] = {}
    for rec in records:
        if rec.id_key and (rec.id_key not in reps
                           or (rec.source in REGISTRARS and reps[rec.id_key].source not in REGISTRARS)):
            reps[rec.id_key] = rec
    dk, ak = mine.get("doi"), mine.get("arxiv")
    if dk in reps and ak in reps:
        lvl, diff = match_record(rec_as_note(reps[dk]), reps[ak])
        if lvl == "mismatch":
            bad = ", ".join(d["field"] for d in diff if d["level"] == "differs")
            msgs.append(f"the {reps[dk].source} record for {dk} and the {reps[ak].source} record for {ak} "
                        f"are different works ({bad} differ)")
    return msgs


# --------------------------------------------------------------------------- #
# Source adapters (pure -- unit-tested with synthetic JSON)
# --------------------------------------------------------------------------- #

def openalex_short_id(url_or_id: str | None) -> str | None:
    if not url_or_id:
        return None
    m = re.search(r"(W\d+)$", url_or_id.strip())
    return m.group(1) if m else None


def from_openalex(work: dict, found_by: str) -> SourceRecord:
    authors = []
    for a in work.get("authorships") or []:
        name = ((a.get("author") or {}).get("display_name")) or a.get("raw_author_name") or ""
        authors.append({"name": name})
    return SourceRecord("openalex", title=work.get("display_name") or work.get("title") or "",
                        authors=authors, year=work.get("publication_year"), found_by=found_by,
                        openalex_id=openalex_short_id(work.get("id")), raw=work)


def from_crossref(msg: dict) -> SourceRecord:
    titles = msg.get("title") or []
    title = titles[0] if titles else ""
    authors = [{"name": " ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name", ""),
                "family": a.get("family")} for a in msg.get("author") or []]
    year = None
    for k in ("issued", "published", "published-print", "published-online", "created"):
        parts = ((msg.get(k) or {}).get("date-parts") or [[None]])[0]
        if parts and parts[0]:
            year = int(parts[0])
            break
    return SourceRecord("crossref", title=title, authors=authors, year=year, found_by="doi", raw={})


def from_arxiv(entry: dict) -> SourceRecord:
    y = entry.get("published", "")[:4]
    return SourceRecord("arxiv", title=entry.get("title", ""),
                        authors=[{"name": n} for n in entry.get("authors", [])],
                        year=int(y) if y.isdigit() else None, found_by="arxiv",
                        raw={"doi": entry["doi"]} if entry.get("doi") else {})


def from_s2(p: dict, found_by: str) -> SourceRecord:
    return SourceRecord("semantic_scholar", title=p.get("title") or "",
                        authors=[{"name": a.get("name", "")} for a in p.get("authors") or []],
                        year=p.get("year"), found_by=found_by, raw={})


# --------------------------------------------------------------------------- #
# Note reading
# --------------------------------------------------------------------------- #

def read_note(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    split = vn.split_frontmatter(text)
    fm = split[0] if split else []
    y = vn.fm_get(fm, "year")
    return {
        "id": vn.note_id(path, fm),
        "path": path,
        "send_never": vn.is_send_never(fm),
        "title": vn.fm_get(fm, "title") or "",
        "authors": vn.parse_flow_list(vn.fm_raw(fm, "authors")),
        "year": int(y) if y and re.fullmatch(r"\d{4}", y) else None,
        "doi": retraction.normalize_doi(vn.fm_get(fm, "doi")),
        "arxiv": retraction.normalize_arxiv(vn.fm_get(fm, "arxiv")),
        "prev_status": vn.fm_get(fm, "resolution_status"),
    }


# --------------------------------------------------------------------------- #
# Network lookups
# --------------------------------------------------------------------------- #

@dataclass
class SourceTrace:
    name: str
    state: str                 # found | not_found | lost | skipped | not_applicable
    found_by: str = ""
    match: str | None = None
    error: str | None = None


def _oa_url(path: str, params: dict) -> str:
    key = os.environ.get("OPENALEX_API_KEY", "").strip()
    if key:
        params = {**params, "api_key": key}
    return f"{OPENALEX}{path}?{urllib.parse.urlencode(params, safe=':/,')}"


def openalex_by_doi(doi: str) -> dict | None:
    """Singleton. None on 404; raises net.HttpError otherwise."""
    try:
        return json.loads(net.get(_oa_url(f"/works/doi:{doi}", {"select": OPENALEX_SELECT})))
    except net.HttpError as e:
        if e.not_found:
            return None
        raise


def openalex_by_id(wid: str) -> dict | None:
    try:
        return json.loads(net.get(_oa_url(f"/works/{wid}", {"select": OPENALEX_SELECT})))
    except net.HttpError as e:
        if e.not_found:
            return None
        raise


def openalex_title_search(title: str, n: int = 5) -> list[dict]:
    q = re.sub(r"[,:|!()\[\]\"']", " ", title)
    q = " ".join(q.split())
    data = json.loads(net.get(_oa_url("/works", {"filter": f"title.search:{q}", "per-page": n,
                                                  "select": OPENALEX_SELECT})))
    return data.get("results") or []


def s2_lookup(paper_id: str | None, title: str | None) -> dict | None:
    headers = {}
    key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip()
    if key:
        headers["x-api-key"] = key
    try:
        if paper_id:
            return json.loads(net.get(f"{S2}/paper/{urllib.parse.quote(paper_id, safe=':/')}?fields={S2_FIELDS}",
                                      headers=headers))
        d = json.loads(net.get(f"{S2}/paper/search/match?query={urllib.parse.quote(title or '')}"
                               f"&fields={S2_FIELDS}", headers=headers))
        data = d.get("data") or []
        return data[0] if data else None
    except net.HttpError as e:
        if e.not_found:
            return None
        raise


# --------------------------------------------------------------------------- #
# Resolution of one note
# --------------------------------------------------------------------------- #

@dataclass
class Result:
    id: str
    status: str
    resolved: bool = False
    match: str | None = None
    openalex_id: str | None = None
    diff: list[dict] = field(default_factory=list)
    sources: list[SourceTrace] = field(default_factory=list)
    retraction: dict = field(default_factory=dict)
    concern: bool = False
    evidence: list[str] = field(default_factory=list)
    flags: list[dict] = field(default_factory=list)
    openalex_lost: bool = False
    note_incomplete: bool = False            # note has no first author or no year -> cannot prove a match
    retraction_lost: list[str] = field(default_factory=list)   # checks that could not run
    newly_flagged: bool = False
    written: bool = False


def decide(note: dict, records: list[SourceRecord], oa_rec: SourceRecord | None, oa_lost: bool,
           ret_status: str, ret_ev: list[str], concern: bool, traces: list[SourceTrace],
           ret_lost: list[str] | None = None) -> Result:
    """Pure: combine per-source records + retraction verdict into a Result."""
    r = Result(id=note["id"], status="unresolved", sources=traces, concern=concern, openalex_lost=oa_lost,
               retraction_lost=list(ret_lost or []))
    r.note_incomplete = not note.get("authors") or note.get("year") is None
    per = [(rec, *match_record(note, rec)) for rec in records]
    registrar_ok = {rec.id_key: rec.source for rec, lvl, _ in per
                    if rec.source in REGISTRARS and lvl in ("exact", "close") and rec.id_key}
    levels = []
    for rec, lvl, diff in per:
        same_work = (sorted(({rec.id_key} | declared_keys(rec)) & set(registrar_ok))
                     if rec.source == "openalex" else [])
        if (same_work and lvl == "mismatch"
                and {d["field"] for d in diff if d["level"] == "differs"} == {"title"}):
            # OpenAlex holds a wrong title while the registrar (arXiv / Crossref)
            # record for the SAME identifier -- the one OpenAlex was fetched by, or
            # one OpenAlex declares for itself -- matches the note, and OpenAlex's
            # author + year agree: probably an OpenAlex data error, not a chimera.
            # Not proven automatically: --gate fails until a human confirms it.
            lvl = "close"
            for d in diff:
                if d["field"] == "title":
                    d["level"] = "source_error"
            r.flags.append({"severity": "importante",
                            "message": f"OpenAlex's title for {rec.openalex_id} differs from the "
                                       f"{'/'.join(registrar_ok[k] for k in same_work)} record for the same "
                                       f"identifier ({', '.join(same_work)}), which matches the note (first "
                                       "author and year agree) -- probably an OpenAlex data error; id recorded, "
                                       "but --gate fails until the researcher confirms it by hand"})
        levels.append(lvl)
        r.diff.extend(diff)
        for t in traces:
            if t.name == rec.source and t.state == "found":
                t.match = lvl
    r.match = worst(levels)
    conflicts = identifier_conflicts(note, records)
    if conflicts:
        r.match = "mismatch"
    r.retraction = {"status": ret_status, "evidence": ret_ev}
    r.evidence.extend(ret_ev)
    oa_ok = oa_rec is not None and oa_rec.openalex_id and r.match in ("exact", "close")
    if conflicts:
        r.status = "mismatch"
        r.flags.append({"severity": "crítico",
                        "message": "the note's DOI and arXiv id point to different works -- "
                                   + "; ".join(conflicts) + ". Fix the note's identifiers by hand."})
        r.evidence.append("identifier conflict: " + "; ".join(conflicts))
    elif r.match == "mismatch":
        r.status = "mismatch"
        bad = sorted({f"{d['field']} ({d['source_name']})" for d in r.diff if d["level"] == "differs"})
        r.flags.append({"severity": "crítico",
                        "message": "metadata mismatch -- possible chimeric citation (title of one paper, "
                                   f"authors/year of another); differs: {', '.join(bad)}. Fix the note by hand."})
    elif ret_status in ("retracted", "withdrawn"):
        r.status = ret_status
        r.flags.append({"severity": "crítico",
                        "message": f"paper is {ret_status.upper()} -- never counts as support "
                                   f"({'; '.join(ret_ev[:2])})"})
    elif oa_ok and not r.note_incomplete:
        r.status = "resolved"
    else:
        r.status = "unresolved"
    r.resolved = bool(oa_ok) and r.status in ("resolved", "retracted", "withdrawn")
    r.openalex_id = oa_rec.openalex_id if (oa_rec and r.resolved) else None
    if r.note_incomplete:
        missing = "/".join(f for f, ok in (("author", bool(note.get("authors"))),
                                           ("year", note.get("year") is not None)) if not ok)
        r.flags.append({"severity": "importante",
                        "message": f"note lacks author/year ({missing} missing) -- cannot prove match; "
                                   "add it to the note and re-run"})
    if r.status == "unresolved" and not (oa_ok and r.note_incomplete):
        confirmed = [rec.source for rec in records if rec.source != "openalex"]
        if oa_lost:
            r.flags.append({"severity": "importante",
                            "message": "OpenAlex could not be reached (errors/budget after retries) -- "
                                       "resolution not proven; re-run later"
                                       + ("" if os.environ.get("OPENALEX_API_KEY") else f"; {KEY_HELP}")})
        elif confirmed:
            r.flags.append({"severity": "importante",
                            "message": f"exists per {', '.join(confirmed)} but has no OpenAlex record -- "
                                       "cannot satisfy resolved:true (needs an OpenAlex id)"})
        else:
            r.flags.append({"severity": "crítico",
                            "message": "not found in any source -- the reference may not exist as written"})
        if confirmed:
            r.evidence.append(f"confirmed by fallback: {', '.join(confirmed)} (match {r.match})")
    if concern:
        r.flags.append({"severity": "importante", "message": "expression of concern published for this paper"})
    if r.retraction_lost and r.status not in ("retracted", "withdrawn"):
        r.flags.append({"severity": "importante",
                        "message": f"retraction/withdrawal check LOST ({', '.join(r.retraction_lost)}) -- "
                                   "not proven clear; re-run later"})
    note_side = sorted({d["field"] for d in r.diff if d["level"] != "source_error"})
    if r.status in ("resolved", "retracted", "withdrawn") and r.match == "close" and note_side:
        r.flags.append({"severity": "menor", "message": f"close match -- check {', '.join(note_side)} in the note"})
    if oa_rec and oa_rec.openalex_id:
        r.evidence.insert(0, f"OpenAlex {oa_rec.openalex_id} via {oa_rec.found_by}")
    r.newly_flagged = r.status in ("retracted", "withdrawn") and note.get("prev_status") != r.status
    return r


def resolve_note(note: dict, arxiv_entries: dict, arxiv_lost: dict) -> Result:
    if note["send_never"]:
        return Result(id=note["id"], status="skipped_send_never")
    traces: list[SourceTrace] = []
    records: list[SourceRecord] = []
    checks: list[retraction.Check] = []
    doi, aid = note["doi"], note["arxiv"] or retraction.arxiv_from_doi(note["doi"])

    # 1. OpenAlex
    oa_rec, oa_lost, oa_err = None, False, None
    dois = [d for d in dict.fromkeys([doi, f"10.48550/arxiv.{aid}" if aid else None]) if d]
    for d in dois:
        try:
            w = openalex_by_doi(d)
        except (net.HttpError, ValueError) as e:
            oa_lost, oa_err = True, net.redact(str(e))
            continue
        if w:
            oa_rec = from_openalex(w, "doi" if d == doi else "arxiv-doi")
            oa_rec.id_key = key_for_doi(d)
            break
    rejected = None
    if oa_rec is None and not oa_lost and note["title"]:
        # last resort (no DOI, or OpenAlex has no record for it): a title-search
        # hit is accepted only if it passes the metadata match
        try:
            hits = openalex_title_search(note["title"])
            best = None
            for w in hits:
                cand = from_openalex(w, "title-search")
                lvl, cdiff = match_record(note, cand)
                if lvl in ("exact", "close") and (best is None or LEVEL_RANK[lvl] > LEVEL_RANK[best[0]]):
                    best = (lvl, cand)
                elif lvl == "mismatch" and rejected is None and title_level(note["title"], cand.title)[0] != "differs":
                    rejected = (cand, cdiff)
            oa_rec = best[1] if best else None
        except (net.HttpError, ValueError) as e:
            oa_lost, oa_err = True, net.redact(str(e))
    if oa_rec is not None:
        oa_lost = False
        records.append(oa_rec)
        checks.append(retraction.openalex_check(oa_rec.raw))
        traces.append(SourceTrace("openalex", "found", oa_rec.found_by))
    else:
        traces.append(SourceTrace("openalex", "lost" if oa_lost else "not_found",
                                  "doi" if dois else "title-search", error=oa_err))

    # 2. Crossref (non-arXiv DOI)
    if doi and not retraction.is_arxiv_doi(doi):
        msg, chk = retraction.fetch_crossref(doi)
        checks.append(chk)
        if msg:
            cr = from_crossref(msg)
            cr.id_key = key_for_doi(doi)
            records.append(cr)
            traces.append(SourceTrace("crossref", "found", "doi"))
        else:
            traces.append(SourceTrace("crossref", chk.state, "doi", error=chk.error))
    else:
        traces.append(SourceTrace("crossref", "not_applicable"))

    # 3. arXiv
    if aid:
        if aid in arxiv_lost:
            checks.append(retraction.Check("arxiv", state="lost", error=arxiv_lost[aid]))
            traces.append(SourceTrace("arxiv", "lost", "arxiv", error=net.redact(arxiv_lost[aid])))
        elif aid in arxiv_entries:
            ax = from_arxiv(arxiv_entries[aid])
            ax.id_key = key_for_arxiv(aid)
            records.append(ax)
            checks.append(retraction.arxiv_check(arxiv_entries[aid]))
            traces.append(SourceTrace("arxiv", "found", "arxiv"))
        else:
            # arXiv answered but returned no entry for an id the note declares: the
            # withdrawal check did not run for it -> LOST (never "clear")
            err = f"arXiv returned no entry for {aid}"
            checks.append(retraction.Check("arxiv", state="lost", error=err))
            traces.append(SourceTrace("arxiv", "lost", "arxiv", error=err))
    else:
        traces.append(SourceTrace("arxiv", "not_applicable"))

    # 4. Semantic Scholar, only if OpenAlex gave nothing
    if oa_rec is None:
        pid = f"DOI:{doi}" if doi and not retraction.is_arxiv_doi(doi) else (f"arXiv:{aid}" if aid else None)
        try:
            p = s2_lookup(pid, note["title"] if not pid else None)
            if p:
                rec = from_s2(p, "id" if pid else "title-search")
                if pid:
                    rec.id_key = key_for_doi(pid[4:]) if pid.startswith("DOI:") else key_for_arxiv(pid[6:])
                if pid or match_record(note, rec)[0] != "mismatch":
                    records.append(rec)
                    traces.append(SourceTrace("semantic_scholar", "found", rec.found_by))
                else:
                    traces.append(SourceTrace("semantic_scholar", "not_found", "title-search"))
            else:
                traces.append(SourceTrace("semantic_scholar", "not_found", "id" if pid else "title-search"))
        except (net.HttpError, ValueError) as e:
            traces.append(SourceTrace("semantic_scholar", "lost", error=net.redact(str(e))))
    else:
        traces.append(SourceTrace("semantic_scholar", "skipped"))

    status, ev = retraction.verdict(checks)
    concern = any(s.kind == "concern" for c in checks for s in c.signals)
    if status == "concern":
        status = "clear"
    lost_checks = [c.source for c in checks if c.state == "lost"]
    r = decide(note, records, oa_rec, oa_lost, status, ev, concern, traces, lost_checks)
    if rejected and oa_rec is None:
        cand, cdiff = rejected
        what = ", ".join(f"{d['field']} {d['source']!r} vs note {d['note']!r}" for d in cdiff if d["level"] == "differs")
        r.evidence.append(f"OpenAlex title-search candidate {cand.openalex_id} rejected ({what}) -- possibly another "
                          "version of the same work (e.g. journal vs preprint); if the note should cite that "
                          "version, update the note's DOI/year by hand")
    return r


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #

EVIDENCE_MAX = 400
_LOST_TAIL = re.compile(r"(?:;\s*)?last check LOST \d{4}-\d{2}-\d{2}\b.*$")


def _join_ev(head: str, tail: str) -> str:
    """head + '; ' + tail within EVIDENCE_MAX, truncating head (never tail)."""
    if not head:
        return tail[:EVIDENCE_MAX]
    room = EVIDENCE_MAX - len(tail) - 2
    return (head[:room].rstrip() + "; " + tail) if room > 0 else tail[:EVIDENCE_MAX]


def fields_for(r: Result, today: str, prev_evidence: str | None = None) -> dict:
    """Frontmatter fields to write. OpenAlex lookup LOST: the §1c fields
    (resolved, openalex_id, resolution_checked) are NOT written -- previous
    values stay, or stay absent -- nor is resolution_status / resolution_match,
    unless a registrar gave a definite retracted / withdrawn; the loss goes into
    resolution_evidence as 'last check LOST <date>' (previous evidence kept).
    A definite `mismatch` is written in full even then."""
    ev = "; ".join(r.evidence[:3])
    if r.diff:
        ev += ("; " if ev else "") + "diff: " + ", ".join(f"{d['field']}={d['level']}" for d in r.diff)
    flags = [f["message"] for f in r.flags if f["severity"] != "menor"]
    if flags and r.status == "unresolved":
        ev += ("; " if ev else "") + flags[0]
    if r.openalex_lost and r.status != "mismatch":
        lost = (f"last check LOST {today}: OpenAlex unreachable (errors/budget) -- resolved/openalex_id/"
                "resolution_checked left unchanged; re-run")
        if r.status in ("retracted", "withdrawn"):
            return {"resolution_status": r.status, "resolution_match": r.match or "",
                    "resolution_evidence": _join_ev(ev, lost)}
        prev = _LOST_TAIL.sub("", prev_evidence or "").strip().rstrip(";").strip()
        return {"resolution_evidence": _join_ev(prev, lost)}
    return {
        "resolved": r.resolved,
        "openalex_id": r.openalex_id or "",
        "resolution_checked": today,
        "resolution_status": r.status,
        "resolution_match": r.match or "",
        "resolution_evidence": ev[:EVIDENCE_MAX],
    }


def write_result(path: Path, r: Result, today: str) -> bool:
    if r.status == "skipped_send_never":
        return False
    text, nl = vn.read_text(path)
    split = vn.split_frontmatter(text)
    prev = vn.fm_get(split[0], "resolution_evidence") if split else None
    vn.write_text(path, vn.set_fields(text, fields_for(r, today, prev)), nl)
    return True


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #

def result_json(r: Result) -> dict:
    if r.status == "skipped_send_never":
        return {"id": r.id, "status": "skipped_send_never"}
    d = asdict(r)
    return d


def print_report(results: list[Result], key_set: bool, written: bool) -> None:
    if not key_set:
        print("note: OPENALEX_API_KEY not set -- running keyless (singleton lookups are free; "
              "title searches draw on a $0.10/day keyless budget). " + KEY_HELP)
    for r in results:
        if r.status == "skipped_send_never":
            print(f"{r.id}: skipped (send: never)")
            continue
        src = " ".join(f"{t.name}:{t.state}" + (f"({t.match})" if t.match else "") for t in r.sources)
        print(f"{r.id}  {r.status}  match={r.match or '-'}  {r.openalex_id or '-'}  [{src}]")
        for d in r.diff:
            extra = f", similarity {d['similarity']}" if "similarity" in d else ""
            print(f"  ~ {d['field']}: note {d['note']!r} vs {d['source_name']} {d['source']!r} ({d['level']}{extra})")
        for e in r.evidence:
            print(f"  . {e}")
        for f in r.flags:
            print(f"  ! [{f['severity']}] {f['message']}")
    counts = {s: sum(1 for r in results if r.status == s) for s in STATUSES + ("skipped_send_never",)}
    print()
    print("summary: " + ", ".join(f"{k} {v}" for k, v in counts.items() if v))
    if not written:
        print("Nothing was written (report-only). Re-run with --write to record the resolution fields.")


def has_source_error(r: Result) -> bool:
    return any(d.get("level") == "source_error" for d in r.diff)


def gate_passes(r: Result) -> bool:
    return (r.status == "resolved" and r.match in ("exact", "close") and not r.retraction_lost
            and not has_source_error(r) and not r.note_incomplete)


def gate_exit(results: list[Result]) -> int:
    """0 all pass; 2 any definite failure (incl. an accepted OpenAlex title
    `source_error` -- a human must confirm it -- and a note lacking author/year);
    1 only 'could not prove' failures (OpenAlex lookup or a retraction check
    LOST to errors)."""
    failing = [r for r in results if not gate_passes(r)]
    if not failing:
        return 0

    def indefinite(r: Result) -> bool:
        if r.note_incomplete or has_source_error(r):
            return False
        return (r.status == "unresolved" and r.openalex_lost) or (r.status == "resolved" and r.retraction_lost)
    return 2 if any(not indefinite(r) for r in failing) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--papers", type=Path, help="the vault's Papers/ directory")
    ap.add_argument("--only", nargs="+", metavar="P-XXXX", help="restrict to these paper ids")
    ap.add_argument("--write", action="store_true", help="write the resolution fields into the notes")
    ap.add_argument("--json", action="store_true", help="machine-readable report on stdout")
    ap.add_argument("--gate", action="store_true",
                    help="exit 0 only if every selected note is live-resolved (exact/close, not retracted/"
                         "withdrawn, no LOST check); 2 on any definite failure, incl. an OpenAlex title "
                         "source_error (the researcher confirms it by hand) or a note lacking author/year; "
                         "1 if the only failures are LOST lookups")
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args(argv)
    bad_input = 1 if a.gate else 2
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    if a.papers is None or not a.papers.is_dir():
        print("error: --papers must be an existing directory", file=sys.stderr)
        return bad_input
    notes = sorted(a.papers.glob("P-*.md"))
    if a.only:
        wanted = list(dict.fromkeys(a.only))
        by_id = {}
        for n in notes:
            by_id.setdefault(n.stem.split(" ")[0], n)
        missing = [w for w in wanted if w not in by_id]
        if missing:
            print(f"error: no note for {', '.join(missing)} in {a.papers}", file=sys.stderr)
            return bad_input
        notes = [by_id[w] for w in wanted]
    if not notes:
        print("error: no P-*.md notes selected", file=sys.stderr)
        return bad_input

    try:
        parsed = [read_note(n) for n in notes]
        sendable = [p for p in parsed if not p["send_never"]]
        entries, lost = retraction.fetch_arxiv(
            [p["arxiv"] or retraction.arxiv_from_doi(p["doi"]) for p in sendable
             if p["arxiv"] or retraction.arxiv_from_doi(p["doi"])])
        today = _dt.date.today().isoformat()
        results = []
        for p in parsed:
            if not p["send_never"]:
                print(f"resolving {p['id']} ...", file=sys.stderr, flush=True)
            r = resolve_note(p, entries, lost)
            if a.write:
                r.written = write_result(p["path"], r, today)
            results.append(r)
    except Exception as e:  # noqa: BLE001 - any crash is "could not run"
        print(f"error: {type(e).__name__}: {net.redact(str(e))}", file=sys.stderr)
        return 1

    key_set = bool(os.environ.get("OPENALEX_API_KEY", "").strip())
    code = gate_exit(results) if a.gate else 0
    if a.json:
        out = {"version": __version__, "checked": today, "openalex_key": "set" if key_set else "missing",
               "results": [result_json(r) for r in results],
               "summary": {s: sum(1 for r in results if r.status == s)
                           for s in STATUSES + ("skipped_send_never",)}}
        if a.gate:
            out["gate"] = {"passed": code == 0, "exit": code}
        print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    else:
        print_report(results, key_set, a.write)
        if a.gate:
            print({0: "GATE PASSED", 2: "GATE FAILED -- at least one reference is not proven resolved",
                   1: "GATE ERROR -- a lookup was LOST to errors; resolution cannot be proven either way"}[code])
    return code


if __name__ == "__main__":
    sys.exit(main())
