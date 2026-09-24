#!/usr/bin/env python3
"""Retraction / withdrawal detection -- the single shared implementation.

Used by `check_retraction.py` (literature-search step 4a), `resolve_refs.py`
(ingestion-time resolution, `--gate`) and `retraction_sweep.py` (periodic
re-check). The parsing functions are pure and unit-tested with synthetic
payloads; the `fetch_*` functions do the network calls through `net.get`.

Crossref (any non-arXiv DOI) -- `https://api.crossref.org/works/{doi}`
------------------------------------------------------------------------
Crossref has carried the Retraction Watch Database since 2023 (entries with
`source: "retraction-watch"`). Field shapes **verified live on 2026-09-24**:

  * The RETRACTED paper's own record carries `updated-by[]`: one entry per
    notice, e.g. `{"DOI": "<notice doi>", "type": "retraction",
    "label": "Retraction", "source": "retraction-watch" | "publisher",
    "updated": {"date-parts": [[2021, 12, 15]]}, "record-id": "34999"}`.
    (checked on 10.1177/1758835919874651.)
  * The NOTICE's record carries `update-to[]` pointing at the retracted DOI
    (checked on 10.1177/17588359211061903). A DOI whose `update-to[]` points
    at *another* DOI is itself a notice -- citing it is almost certainly a
    wrong DOI; it is reported with `notice_for`.
  * Withdrawals / removals are often self-referencing: the record has both
    `update-to[]` and `updated-by[]` with its own DOI and `type: "withdrawal"`
    / `"removal"` (checked on 10.1016/j.adengl.2021.11.027).
  * `is-retracted` was NOT present on any of these records on 2026-09-24. It
    is still honoured if it appears, but absence means nothing.
  * The title of a retracted / withdrawn record is often prefixed
    `RETRACTED:` / `WITHDRAWN:` -- used as corroborating evidence.
  * Types used: `retraction`, `withdrawal`, `removal`, `expression_of_concern`
    (underscores; confirmed via `/works?filter=update-type:<t>` on 2026-09-24).
    Other update types (`correction`, `erratum`, `new_version`, ...) are ignored.

  Mapping: retraction -> retracted; withdrawal, removal -> withdrawn;
  expression_of_concern -> concern (flag, not a removal).

arXiv (any arXiv id) -- `https://export.arxiv.org/api/query?id_list=...`
-----------------------------------------------------------------------
arXiv has no boolean for "withdrawn"; detect from text, case-insensitive:
`arxiv:comment` mentions withdrawn / withdrawal; `title` starts `Withdrawn:`;
`summary` is a withdrawal notice ("This paper has been withdrawn ...").
A later version number alone is NOT a withdrawal. Live example checked
2026-09-24: arXiv:0910.4008 (comment "Withdrawn", summary "This paper has
been withdrawn, as it should not have been a new submission ...").

OpenAlex -- `is_retracted` on a work (verified 2026-09-24: true on
W2976826629 = 10.1177/1758835919874651). An extra signal only.

Standard library only. Imported, not run (see check_retraction.py for a CLI).
"""

from __future__ import annotations

import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field

try:  # imported as a sibling module when the scripts run from this directory
    import net
except ImportError:  # pragma: no cover - package-style import
    from . import net  # type: ignore

__version__ = "1.0.0"

CROSSREF_WORK = "https://api.crossref.org/works/{}"
ARXIV_QUERY = "https://export.arxiv.org/api/query?id_list={}&max_results={}"
ARXIV_BATCH = 100
ENDPOINTS_VERIFIED = "2026-09-24"

CROSSREF_TYPES = {
    "retraction": "retracted",
    "withdrawal": "withdrawn",
    "removal": "withdrawn",
    "expression_of_concern": "concern",
}
# higher wins when several signals are present
PRECEDENCE = {"clear": 0, "concern": 1, "withdrawn": 2, "retracted": 3}

_ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
_TITLE_PREFIX = re.compile(r"^\s*(retracted|withdrawn|removed)\s*[:\-]", re.IGNORECASE)
_WD_COMMENT = re.compile(r"\bwithdrawn\b|\bwithdrawal\b|\bwithdraw(?:ing)? (?:this|the) (?:paper|submission|article)\b",
                         re.IGNORECASE)
_WD_TITLE = re.compile(r"^\s*withdrawn\b\s*[:\-.]?", re.IGNORECASE)
_WD_SUMMARY = re.compile(
    r"^\W*(this (paper|submission|article|manuscript|preprint|version|work)\s+(has been|is|was)\s+withdrawn"
    r"|(paper|submission|article)\s+withdrawn"
    r"|withdrawn\b"
    r"|the authors? (has|have) withdrawn)",
    re.IGNORECASE)


@dataclass
class Signal:
    source: str          # crossref | arxiv | openalex
    kind: str            # retracted | withdrawn | concern
    evidence: str        # human-readable, public bibliographic facts only


@dataclass
class Check:
    """Outcome of one source's check for one record."""
    source: str
    state: str = "ok"                      # ok | not_found | lost | not_applicable
    signals: list[Signal] = field(default_factory=list)
    notice_for: list[str] = field(default_factory=list)   # this DOI is a notice about these DOIs
    error: str | None = None


def normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = doi.strip()
    d = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", d, flags=re.IGNORECASE)
    d = re.sub(r"^doi:\s*", "", d, flags=re.IGNORECASE)
    d = d.strip().rstrip(".")
    return d.lower() or None


def normalize_arxiv(aid: str | None) -> str | None:
    """'arXiv:2201.02177v3' / 'https://arxiv.org/abs/2201.02177' -> '2201.02177'."""
    if not aid:
        return None
    a = aid.strip()
    a = re.sub(r"^(https?://)?(www\.)?(export\.)?arxiv\.org/(abs|pdf)/", "", a, flags=re.IGNORECASE)
    a = re.sub(r"^arxiv:\s*", "", a, flags=re.IGNORECASE)
    a = re.sub(r"\.pdf$", "", a, flags=re.IGNORECASE)
    a = re.sub(r"v\d+$", "", a)
    return a or None


def is_arxiv_doi(doi: str | None) -> bool:
    return bool(doi) and doi.lower().startswith("10.48550/arxiv.")


def arxiv_from_doi(doi: str | None) -> str | None:
    return doi[len("10.48550/arxiv."):] if is_arxiv_doi(doi) else None


def _date(entry: dict) -> str:
    parts = ((entry.get("updated") or {}).get("date-parts") or [[None]])[0]
    return "-".join(f"{p:02d}" if i else str(p) for i, p in enumerate(parts) if p is not None)


def crossref_flags(message: dict, doi: str | None = None) -> Check:
    """Pure: read retraction signals from a Crossref `message` object."""
    c = Check("crossref")
    own = normalize_doi(doi or message.get("DOI"))
    if message.get("is-retracted") is True:
        c.signals.append(Signal("crossref", "retracted", "Crossref is-retracted: true"))
    seen = set()
    for e in message.get("updated-by") or []:
        kind = CROSSREF_TYPES.get((e.get("type") or "").lower())
        if not kind:
            continue
        key = (kind, normalize_doi(e.get("DOI")))
        if key in seen:           # the same notice listed by publisher and Retraction Watch
            continue
        seen.add(key)
        src = e.get("source") or "unknown"
        c.signals.append(Signal("crossref", kind,
                                f"Crossref updated-by: {e.get('type')} notice {e.get('DOI')} "
                                f"({_date(e) or 'undated'}, source {src})"))
    for e in message.get("update-to") or []:
        kind = CROSSREF_TYPES.get((e.get("type") or "").lower())
        if not kind:
            continue
        target = normalize_doi(e.get("DOI"))
        if own and target == own:
            key = (kind, own)
            if key not in seen:
                seen.add(key)
                c.signals.append(Signal("crossref", kind,
                                        f"Crossref update-to (self): {e.get('type')} ({_date(e) or 'undated'})"))
        elif target:
            if target not in c.notice_for:
                c.notice_for.append(target)
            c.signals.append(Signal("crossref", kind,
                                    f"this DOI is itself a {e.get('type')} notice for {target} -- "
                                    f"the note probably cites the notice instead of the paper"))
    titles = message.get("title") or []
    t0 = titles[0] if isinstance(titles, list) and titles else (titles if isinstance(titles, str) else "")
    m = _TITLE_PREFIX.match(t0 or "")
    if m:
        word = m.group(1).lower()
        kind = "retracted" if word == "retracted" else "withdrawn"
        if not any(s.kind == kind for s in c.signals):
            c.signals.append(Signal("crossref", kind, f"Crossref title prefixed '{m.group(1).upper()}:'"))
    return c


def arxiv_withdrawn(title: str, comment: str, summary: str) -> list[Signal]:
    """Pure: withdrawal signals from one arXiv entry's text fields."""
    out = []
    t = " ".join((title or "").split())
    cm = " ".join((comment or "").split())
    sm = " ".join((summary or "").split())
    if cm and _WD_COMMENT.search(cm):
        out.append(Signal("arxiv", "withdrawn", f"arXiv comment: \"{cm[:160]}\""))
    if _WD_TITLE.match(t):
        out.append(Signal("arxiv", "withdrawn", f"arXiv title begins 'Withdrawn': \"{t[:120]}\""))
    if sm and _WD_SUMMARY.match(sm) and len(sm) < 600:
        out.append(Signal("arxiv", "withdrawn", f"arXiv abstract is a withdrawal notice: \"{sm[:160]}\""))
    return out


def parse_arxiv_feed(xml_bytes: bytes) -> dict[str, dict]:
    """Pure: Atom feed -> {version-stripped id: {title, comment, summary, authors,
    published, updated, doi, version, journal_ref}}. arXiv error entries
    (id .../api/errors) are skipped."""
    root = ET.fromstring(xml_bytes)
    out: dict[str, dict] = {}
    for e in root.findall("a:entry", _ATOM):
        raw_id = (e.findtext("a:id", default="", namespaces=_ATOM) or "").strip()
        if "/api/errors" in raw_id or not raw_id:
            continue
        tail = re.sub(r"^https?://arxiv\.org/abs/", "", raw_id)
        vm = re.search(r"v(\d+)$", tail)
        aid = normalize_arxiv(tail)
        title = " ".join((e.findtext("a:title", default="", namespaces=_ATOM) or "").split())
        if not aid or (not title and not e.findall("a:author", _ATOM)):
            continue
        out[aid] = {
            "id": aid,
            "version": int(vm.group(1)) if vm else None,
            "title": title,
            "summary": " ".join((e.findtext("a:summary", default="", namespaces=_ATOM) or "").split()),
            "comment": " ".join((e.findtext("arxiv:comment", default="", namespaces=_ATOM) or "").split()),
            "authors": [" ".join((a.findtext("a:name", default="", namespaces=_ATOM) or "").split())
                        for a in e.findall("a:author", _ATOM)],
            "published": (e.findtext("a:published", default="", namespaces=_ATOM) or "").strip(),
            "updated": (e.findtext("a:updated", default="", namespaces=_ATOM) or "").strip(),
            "doi": normalize_doi(e.findtext("arxiv:doi", default="", namespaces=_ATOM)),
            "journal_ref": " ".join((e.findtext("arxiv:journal_ref", default="", namespaces=_ATOM) or "").split()),
        }
    return out


def arxiv_check(entry: dict | None) -> Check:
    c = Check("arxiv")
    if entry is None:
        c.state = "not_found"
        return c
    c.signals = arxiv_withdrawn(entry.get("title", ""), entry.get("comment", ""), entry.get("summary", ""))
    return c


def openalex_check(work: dict | None) -> Check:
    c = Check("openalex")
    if work is None:
        c.state = "not_found"
        return c
    if work.get("is_retracted") is True:
        wid = (work.get("id") or "").rsplit("/", 1)[-1]
        c.signals.append(Signal("openalex", "retracted", f"OpenAlex is_retracted: true ({wid})"))
    return c


def verdict(checks: list[Check]) -> tuple[str, list[str]]:
    """Combine checks -> (clear | concern | withdrawn | retracted, evidence lines)."""
    best, ev = "clear", []
    for c in checks:
        for s in c.signals:
            ev.append(s.evidence)
            if PRECEDENCE[s.kind] > PRECEDENCE[best]:
                best = s.kind
    return best, ev


# --------------------------------------------------------------------------- #
# Network
# --------------------------------------------------------------------------- #

def fetch_crossref(doi: str, mailto: str | None = None) -> tuple[dict | None, Check]:
    """(Crossref message or None, Check). arXiv DataCite DOIs are not in Crossref
    -> not_applicable, no call made."""
    doi = normalize_doi(doi) or ""
    if not doi:
        return None, Check("crossref", state="not_applicable")
    if is_arxiv_doi(doi):
        return None, Check("crossref", state="not_applicable",
                           error="arXiv DOI (DataCite) -- not registered with Crossref")
    url = CROSSREF_WORK.format(urllib.parse.quote(doi, safe="/:;()"))
    if mailto:
        url += "?mailto=" + urllib.parse.quote(mailto)
    try:
        data = net.get(url, headers={"Accept": "application/json"})
        msg = json.loads(data).get("message") or {}
    except net.HttpError as e:
        if e.not_found:
            return None, Check("crossref", state="not_found")
        return None, Check("crossref", state="lost", error=str(e))
    except (ValueError, json.JSONDecodeError) as e:
        return None, Check("crossref", state="lost", error=f"unparseable response: {e}")
    return msg, crossref_flags(msg, doi)


def fetch_arxiv(ids: list[str]) -> tuple[dict[str, dict], dict[str, str]]:
    """Batched arXiv lookup. Returns ({id: entry}, {id: error} for ids whose batch
    was LOST). Ids missing from both maps were answered but not found."""
    ids = [i for i in dict.fromkeys(normalize_arxiv(x) for x in ids) if i]
    found: dict[str, dict] = {}
    lost: dict[str, str] = {}
    for k in range(0, len(ids), ARXIV_BATCH):
        chunk = ids[k:k + ARXIV_BATCH]
        url = ARXIV_QUERY.format(",".join(urllib.parse.quote(i, safe="/") for i in chunk), len(chunk))
        try:
            found.update(parse_arxiv_feed(net.get(url, headers={"Accept": "application/atom+xml"})))
        except (net.HttpError, ET.ParseError) as e:
            for i in chunk:
                lost[i] = str(e)
    return found, lost


def check_one(doi: str | None, arxiv: str | None, arxiv_entries: dict[str, dict],
              arxiv_lost: dict[str, str], mailto: str | None = None) -> dict:
    """Crossref + arXiv checks for one candidate (arXiv entries prefetched in batch)."""
    doi = normalize_doi(doi)
    aid = normalize_arxiv(arxiv) or arxiv_from_doi(doi)
    checks: list[Check] = []
    if doi and not is_arxiv_doi(doi):
        checks.append(fetch_crossref(doi, mailto)[1])
    else:
        checks.append(Check("crossref", state="not_applicable"))
    if aid:
        if aid in arxiv_lost:
            checks.append(Check("arxiv", state="lost", error=arxiv_lost[aid]))
        else:
            checks.append(arxiv_check(arxiv_entries.get(aid)))
    else:
        checks.append(Check("arxiv", state="not_applicable"))
    status, ev = verdict(checks)
    notice_for = [d for c in checks for d in c.notice_for]
    return {
        "doi": doi, "arxiv": aid, "status": status,
        "retracted": status == "retracted", "withdrawn": status == "withdrawn",
        "concern": any(s.kind == "concern" for c in checks for s in c.signals),
        "notice_for": notice_for,
        "evidence": ev,
        "checks": {c.source: {"state": c.state, "flag": verdict([c])[0],
                              **({"error": net.redact(c.error)} if c.error else {})}
                   for c in checks},
    }


def as_dict(c: Check) -> dict:
    return asdict(c)
