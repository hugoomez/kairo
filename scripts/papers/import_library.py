#!/usr/bin/env python3
"""Bring an existing library into the vault: a BibTeX export (Zotero / Better
BibTeX, JabRef, any .bib) or a CSL-JSON export, one entry at a time through
ingest_paper.py — so every imported paper gets the same verbatim abstract and
full text, kept source bytes and verification as a paper found by a search.

    import_library.py --vault <vault> --project PROJ-XXX (--bib refs.bib | --csl refs.json)
                      [--zotero-keys] [--dry-run] [--limit N]

Only identifiers are taken from the file — an arXiv id (`eprint` with
`archivePrefix = {arXiv}`, an arxiv.org URL, a `journal = {arXiv preprint
arXiv:…}`) or a DOI. Titles, authors and venues in the file are never copied
into a note: ingest_paper.py fetches them from the sources, so a typo or a
chimeric entry in the old library does not become a vault fact (resolve_refs.py
then checks the note against the sources). An entry with neither identifier is
listed (`no_identifier`) for the researcher, with its title, to look up by hand
(`paper_card.py --title`); nothing is guessed.

`--zotero-keys` records each entry's citation key as the note's `zotero_key`
(the key of a Better BibTeX export is the key Zotero uses). `--dry-run` prints
the plan and ingests nothing. Entries are ingested one after another (the
ingestion lock and arXiv's 3 s spacing make parallel imports no faster).

Prints one JSON object. Exit: 0 ok (failures are listed, not fatal) · 2 bad input.
Standard library only.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "citations"))
import retraction  # noqa: E402

TOOL = "kairo/import_library@1.0.0"


def ingest(argv: list[str]) -> tuple[int, dict]:
    """Run ingest_paper.py in-process; (exit code, its JSON)."""
    import ingest_paper
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = ingest_paper.main(argv)
    try:
        return code, json.loads(buf.getvalue())
    except ValueError:
        return code, {"error": buf.getvalue()[-300:]}


# --------------------------------------------------------------------------
# BibTeX (a small, tolerant parser: entries, braces, quotes, bare values)
# --------------------------------------------------------------------------

def _value(text: str, i: int) -> tuple[str, int]:
    """One field value starting at text[i] (after `=`): {…} with nesting, "…", or a bare word."""
    while i < len(text) and text[i].isspace():
        i += 1
    if i < len(text) and text[i] == "{":
        depth, j = 0, i
        while j < len(text):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    return text[i + 1:j], j + 1
            j += 1
        return text[i + 1:], len(text)
    if i < len(text) and text[i] == '"':
        j = i + 1
        while j < len(text) and not (text[j] == '"' and text[j - 1] != "\\"):
            j += 1
        return text[i + 1:j], j + 1
    m = re.match(r"[^,}\s]+", text[i:])
    return (m.group(0), i + m.end()) if m else ("", i)


def _clean(v: str) -> str:
    v = re.sub(r"\\url\{([^}]*)\}", r"\1", v)
    return " ".join(v.replace("{", "").replace("}", "").split())


def parse_bibtex(text: str) -> list[dict]:
    out = []
    for m in re.finditer(r"@(\w+)\s*\{", text):
        kind = m.group(1).lower()
        if kind in ("string", "preamble", "comment"):
            continue
        i = m.end()
        km = re.match(r"\s*([^,\s]+)\s*,", text[i:])
        if not km:
            continue
        entry = {"type": kind, "key": km.group(1)}
        i += km.end()
        while i < len(text):
            fm = re.match(r"\s*([A-Za-z][\w-]*)\s*=", text[i:])
            if not fm:
                break
            val, i = _value(text, i + fm.end())
            entry[fm.group(1).lower()] = _clean(val)
            cm = re.match(r"\s*,", text[i:])
            if cm:
                i += cm.end()
            if re.match(r"\s*\}", text[i:]):
                break
        out.append(entry)
    return out


ARXIV_IN = re.compile(r"arxiv\.org/(?:abs|pdf)/([^\s?#}]+)|arXiv:\s*([0-9]{4}\.[0-9]{4,5}(?:v\d+)?|[a-z-]+/\d{7})",
                      re.I)


def identifier(e: dict) -> tuple[str | None, str | None]:
    """(kind, id) of one entry: an arXiv id first (the open text the note is anchored on), else a DOI."""
    if (e.get("archiveprefix") or e.get("eprinttype") or "").lower() == "arxiv" and e.get("eprint"):
        return "arxiv", retraction.normalize_arxiv(e["eprint"])
    for field in ("arxiv", "url", "howpublished", "journal", "note", "eprint"):
        m = ARXIV_IN.search(e.get(field) or "")
        if m:
            return "arxiv", retraction.normalize_arxiv(m.group(1) or m.group(2))
    doi = retraction.normalize_doi(e.get("doi"))
    if doi:
        if retraction.is_arxiv_doi(doi):
            return "arxiv", retraction.arxiv_from_doi(doi)
        return "doi", doi
    return None, None


def from_csl(items: list[dict]) -> list[dict]:
    out = []
    for it in items:
        e = {"key": str(it.get("id") or it.get("citation-key") or ""), "title": it.get("title") or "",
             "doi": it.get("DOI") or "", "url": it.get("URL") or "", "note": it.get("number") or "",
             "arxiv": it.get("arxiv") or ""}
        out.append(e)
    return out


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vault", required=True, type=Path)
    ap.add_argument("--project", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--bib", type=Path)
    g.add_argument("--csl", type=Path)
    ap.add_argument("--zotero-keys", action="store_true", help="record each entry's citation key as zotero_key")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)
    if not re.fullmatch(r"PROJ-\d{3,}", a.project):
        print(json.dumps({"tool": TOOL, "refused": "--project must be a PROJ-XXX id"}))
        return 2
    try:
        entries = parse_bibtex(a.bib.read_text(encoding="utf-8")) if a.bib else \
            from_csl(json.loads(a.csl.read_text(encoding="utf-8")))
    except (OSError, ValueError) as e:
        print(json.dumps({"tool": TOOL, "refused": f"cannot read the library: {e}"}, ensure_ascii=False))
        return 2
    plan, none = [], []
    seen: set[tuple[str, str]] = set()
    for e in entries:
        kind, ident = identifier(e)
        if not kind:
            none.append({"key": e.get("key"), "title": e.get("title")})
            continue
        if (kind, ident) in seen:
            continue
        seen.add((kind, ident))
        plan.append({"key": e.get("key"), "kind": kind, "id": ident, "title": e.get("title")})
    if a.limit:
        plan = plan[:a.limit]
    result = {"tool": TOOL, "entries": len(entries), "plan": plan, "no_identifier": none}
    if a.dry_run:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0
    done, counts = [], {"created": 0, "exists": 0, "failed": 0, "no_identifier": len(none)}
    for p in plan:
        code, out = ingest(["add", "--vault", str(a.vault), "--project", a.project, f"--{p['kind']}", p["id"],
                            "--source", "manual"])
        status = out.get("status") if code == 0 else "failed"
        counts["created" if status == "created" else "exists" if status == "exists" else "failed"] += 1
        row = {"key": p["key"], "id": out.get("id"), "status": status,
               "reason": out.get("refused") or out.get("error"), "warnings": out.get("warnings") or []}
        if a.zotero_keys and code == 0 and out.get("id") and p.get("key"):
            zc, zout = ingest(["zotero-key", "--vault", str(a.vault), "--id", out["id"], "--key", p["key"]])
            row["zotero_key"] = p["key"] if zc == 0 else None
        done.append(row)
    result.update(results=done, counts=counts)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
