#!/usr/bin/env python3
"""Add a vault paper to the local Zotero library and record its citation key —
mechanically, from the note's own (fetched) metadata.

    zotero_sync.py --vault <vault> P-0001 [P-0002 …]

For each note: Better BibTeX `item.search` by arXiv URL / DOI / title finds an
existing item (it is reused, never duplicated); otherwise the item is created
through the local connector (`POST /connector/saveItems`, the endpoint the
browser connector uses, no API key) with the note's title, authors, year, DOI,
URL and a `PROJ-XXX` tag, then searched again for its citation key. The key is
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
sys.path.insert(0, str(HERE.parent / "citations"))
import vaultnotes as vn  # noqa: E402

TOOL = "kairo/zotero_sync@1.0.0"
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


def _search(post: Post, term: str) -> list[dict]:
    if not term:
        return []
    code, out = post("/better-bibtex/json-rpc", {"jsonrpc": "2.0", "method": "item.search", "params": [term]})
    return list(out.get("result") or []) if code == 200 else []


def _find(post: Post, meta: dict) -> str | None:
    for term in (f"arxiv.org/abs/{meta['arxiv']}" if meta["arxiv"] else "", meta["doi"], meta["title"]):
        for it in _search(post, term):
            if _key_of(it):
                return _key_of(it)
    return None


def item_of(meta: dict) -> dict:
    creators = []
    for a in meta["authors"]:
        last, first = (a.split(",", 1) + [""])[:2] if "," in a else (a.split()[-1], " ".join(a.split()[:-1]))
        creators.append({"lastName": last.strip(), "firstName": first.strip(), "creatorType": "author"})
    venue = meta["venue"] if meta["venue"] and meta["venue"] != "arXiv preprint" else ""
    kind = "preprint" if meta["arxiv"] and not venue else \
        "conferencePaper" if re.search(r"proc|conference|symposium|workshop", venue, re.I) else "journalArticle"
    it = {"itemType": kind, "title": meta["title"], "creators": creators, "date": meta["year"],
          "url": meta["url"], "tags": [{"tag": p} for p in meta["projects"]]}
    if meta["doi"]:
        it["DOI"] = meta["doi"]
    if venue:
        it["publicationTitle" if kind == "journalArticle" else "proceedingsTitle"] = venue
    if meta["arxiv"]:
        it["archiveID"] = f"arXiv:{meta['arxiv']}"
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
    g = lambda k: (vn.fm_get(fm, k) or "").strip()  # noqa: E731
    meta = {"title": g("title"), "authors": vn.parse_flow_list(vn.fm_raw(fm, "authors")), "year": g("year"),
            "doi": g("published_doi") or g("doi"), "arxiv": g("arxiv"), "venue": g("published_venue") or g("venue"),
            "url": g("url"), "projects": vn.parse_flow_list(vn.fm_raw(fm, "projects"))}
    try:
        key = _find(post, meta)
        status = "exists"
        if not key:
            code, _ = post("/connector/saveItems", {"items": [item_of(meta)], "uri": meta["url"]})
            if code not in (200, 201):
                return {"id": pid, "status": "failed", "reason": f"saveItems answered {code}"}
            status = "created"
            key = _find(post, meta)
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
