#!/usr/bin/env python3
"""Add a vault paper to the local Zotero library and record its citation key —
mechanically, from the note's own (fetched) metadata.

    zotero_sync.py --vault <vault> P-0001 [P-0002 …]

For each note: Better BibTeX `item.search` by arXiv URL / DOI / title finds an
existing item — a hit counts only when its own arXiv id or DOI is the note's,
or its title is exactly the note's with the same first author (a search
matches substrings anywhere) — and it is reused, never duplicated. Otherwise
the item is created through the local connector (`POST /connector/saveItems`,
the endpoint the browser connector uses, no API key) as the version
export_bib.py cites — the published one (its DOI, venue, year and title) when
its year is known, else the preprint; the item type from the note's
`venue_type`, else export_bib's venue rule; family-name particles ("van der
Maaten") and collaborations ("ATLAS Collaboration", one field) kept — with a
`PROJ-XXX` tag, then searched again for its citation key. When Better
BibTeX's search does not answer, nothing is created (it could not be
deduplicated): «Better BibTeX no disponible: no se crea para no duplicar». The key is
written to the note's `zotero_key` (ingest_paper.py zotero-key's rule). Only
frontmatter metadata is sent — never the abstract or the text — and a
`send: never` note is skipped without any request.

Zotero must be running with "Allow other applications on this computer to
communicate with Zotero" on, and Better BibTeX installed (README → Zotero).
When it is not reachable the output says so once (`unreachable`) and nothing is
written; export_bib.py covers the bibliography without Zotero.

Prints one JSON object. Exit: 0 ok · 2 bad input. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "citations"))
import export_bib  # noqa: E402
import resolve_refs as rr  # noqa: E402
import retraction  # noqa: E402
import vaultnotes as vn  # noqa: E402

TOOL = "kairo/zotero_sync@1.1.0"
BASE = "http://127.0.0.1:23119"
Post = Callable[[str, dict], tuple[int, dict]]


def http_post(path: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "Zotero-Allowed-Request": "true"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        return e.code, {}


def _key_of(item: dict) -> str | None:
    for k in ("citekey", "citationKey", "citation-key", "citationkey"):
        if item.get(k):
            return str(item[k])
    return None


class NoSearch(Exception):
    """Better BibTeX's item.search did not answer: nothing can be deduplicated."""


def _search(post: Post, term: str) -> list[dict]:
    if not term:
        return []
    code, out = post("/better-bibtex/json-rpc", {"jsonrpc": "2.0", "method": "item.search", "params": [term]})
    if code != 200 or not isinstance(out, dict) or "error" in out:
        raise NoSearch(f"item.search answered {code}")
    return [x for x in (out.get("result") or []) if isinstance(x, dict)]


def _first_author(it: dict) -> str:
    """The first author's family name in a search hit (CSL `author` or Zotero `creators`)."""
    for k in ("author", "creators"):
        people = it.get(k) or []
        if people and isinstance(people[0], dict):
            p = people[0]
            return p.get("family") or p.get("lastName") or p.get("literal") or p.get("name") or ""
    return ""


def _is_this(it: dict, meta: dict, how: str) -> bool:
    """A search hit is this paper only when its identifier (or its exact title and
    first author) says so — a search matches substrings anywhere in an item."""
    blob = " ".join(str(it.get(k) or "") for k in ("URL", "url", "archiveID", "extra", "number", "DOI", "doi"))
    if how == "arxiv":
        return bool(re.search(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:\s*)" + re.escape(meta["arxiv"]) + r"(?![\d.])",
                              blob, re.I))
    if how == "doi":
        return retraction.normalize_doi(it.get("DOI") or it.get("doi")) in meta["dois"]
    first = rr.surname_of((meta["authors"] or [""])[0])
    return (rr.title_level(meta["title"], str(it.get("title") or ""))[0] == "exact" and bool(first)
            and rr.surname_matches(first, _first_author(it), _first_author(it) or None))


def _find(post: Post, meta: dict) -> str | None:
    """The key of the item that is this paper, or None. Raises NoSearch when
    Better BibTeX's search is unavailable."""
    terms = [("arxiv", f"arxiv.org/abs/{meta['arxiv']}" if meta["arxiv"] else "")]
    terms += [("doi", d) for d in meta["dois"]] + [("title", meta["title"])]
    for how, term in terms:
        for it in _search(post, term):
            if _key_of(it) and _is_this(it, meta, how):
                return _key_of(it)
    return None


COLLECTIVE = re.compile(r"\b(?:collaboration|consortium|group|team|project|initiative|network|committee)\b", re.I)


def creator_of(a: str) -> dict:
    """'Last, First' as written; 'First [particles] Last' with the particles in the
    family name ('van der Maaten'); a collaboration or one-word name as one field."""
    a = " ".join(a.split())
    if "," in a:
        last, first = a.split(",", 1)
        return {"lastName": last.strip(), "firstName": first.strip(), "creatorType": "author"}
    if COLLECTIVE.search(a) or len(a.split()) == 1:
        return {"name": a, "creatorType": "author"}
    last = rr.surname_of(a)
    first = a[:len(a) - len(last)].strip() if a.endswith(last) else " ".join(a.split()[:-1])
    return {"lastName": last, "firstName": first, "creatorType": "author"}


ITEM_TYPE = {"article": "journalArticle", "inproceedings": "conferencePaper", "incollection": "bookSection",
             "book": "book", "phdthesis": "thesis", "techreport": "report", "patent": "patent"}
VENUE_KEY = {"journalArticle": "publicationTitle", "conferencePaper": "proceedingsTitle",
             "bookSection": "bookTitle", "book": "publisher", "thesis": "university", "report": "institution"}


def item_of(meta: dict) -> dict:
    """The Zotero item for the version export_bib.py cites: the published version
    (its DOI, venue, year, the publisher's title) when its year is known, else
    the preprint; the item type from the registered `venue_type`, else
    export_bib's venue rule."""
    m = export_bib.effective(meta)
    kind, _ = export_bib.entry_type(m)
    itype = ITEM_TYPE.get(kind) or ("preprint" if m.get("arxiv") else "journalArticle")
    published = bool(m.get("published_venue") or m.get("published_doi"))
    venue = m.get("published_venue") or (m.get("venue") if m.get("venue") != "arXiv preprint" else "") or ""
    year = (m.get("published_year") if published else "") or m.get("year") or ""
    it = {"itemType": itype, "title": export_bib.cited_title(m),
          "creators": [creator_of(a) for a in export_bib.cited_authors(m) if a.strip()], "date": str(year),
          "url": m.get("url") or "", "tags": [{"tag": p} for p in meta.get("projects") or []]}
    doi = m.get("published_doi") or m.get("doi")
    if doi:
        it["DOI"] = doi
    if venue and VENUE_KEY.get(itype):
        it[VENUE_KEY[itype]] = venue
    for src, dst in (("volume", "volume"), ("issue", "issue"), ("pages", "pages")):
        if m.get(src) and itype in ("journalArticle", "conferencePaper", "bookSection"):
            it[dst] = m[src].replace("--", "-")
    if m.get("arxiv"):
        it["archiveID"] = f"arXiv:{m['arxiv']}"
    return it


def sync(vault: Path, pid: str, post: Post = http_post) -> dict:
    hits = sorted((vault / "Papers").glob(f"{pid} *.md")) + sorted((vault / "Papers").glob(f"{pid}.md"))
    if not hits:
        return {"id": pid, "status": "missing"}
    path = hits[0]
    text, nl = vn.read_text(path)
    fm = (vn.split_frontmatter(text) or ([], ""))[0]
    if vn.is_send_never(fm):
        return {"id": pid, "status": "skipped_send_never"}
    meta = {**export_bib.note_meta(fm), "projects": vn.parse_flow_list(vn.fm_raw(fm, "projects"))}
    meta["arxiv"] = retraction.normalize_arxiv(meta["arxiv"]) or ""
    item = item_of(meta)
    # what an existing item may be found by: the arXiv id, either DOI, the title it carries
    find = {"arxiv": meta["arxiv"], "authors": meta["authors"], "title": item["title"],
            "dois": [d for d in dict.fromkeys(retraction.normalize_doi(x) for x in
                                               (item.get("DOI"), meta["published_doi"], meta["doi"])) if d]}
    try:
        key = _find(post, find)
        status = "exists"
        if not key:
            code, _ = post("/connector/saveItems", {"items": [item], "uri": meta["url"]})
            if code not in (200, 201):
                return {"id": pid, "status": "failed", "reason": f"saveItems answered {code}"}
            status = "created"
            key = _find(post, find)
    except NoSearch:
        return {"id": pid, "status": "failed",
                "reason": "Better BibTeX no disponible: no se crea para no duplicar (instala Better BibTeX "
                          "y deja Zotero abierto)"}
    except (OSError, ValueError) as e:
        return {"id": pid, "status": "unreachable",
                "reason": f"Zotero no responde en {BASE} ({e}); ábrelo con «Allow other applications…» activado"}
    if not key:
        return {"id": pid, "status": status, "zotero_key": None,
                "reason": "Better BibTeX no devolvió clave (¿instalado?): la nota queda sin zotero_key"}
    vn.write_text(path, vn.set_fields(text, {"zotero_key": key}), nl)
    return {"id": pid, "status": status, "zotero_key": key}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vault", required=True, type=Path)
    ap.add_argument("ids", nargs="+")
    a = ap.parse_args(argv)
    if any(not re.fullmatch(r"P-\d{4,5}", i) for i in a.ids):
        print(json.dumps({"tool": TOOL, "error": "ids must look like P-XXXX"}))
        return 2
    out = []
    for pid in a.ids:
        r = sync(a.vault, pid)
        out.append(r)
        if r["status"] == "unreachable":
            break                                   # said once, not once per paper
    print(json.dumps({"tool": TOOL, "results": out}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
