#!/usr/bin/env python3
"""Bring an existing library into the vault: a BibTeX export (Zotero / Better
BibTeX, JabRef, any .bib) or a CSL-JSON export, one entry at a time through
ingest_paper.py — so every imported paper gets the same verbatim abstract and
full text, kept source bytes and verification as a paper found by a search.

    import_library.py --vault <vault> --project PROJ-XXX (--bib refs.bib | --csl refs.json)
                      [--zotero-keys] [--pdf-dir DIR] [--title-lookup] [--dry-run] [--limit N]

Only identifiers are taken from the file — an arXiv id (`eprint` with
`archivePrefix = {arXiv}`, an arxiv.org URL, a `journal = {arXiv preprint
arXiv:…}`) or a DOI. Titles, authors and venues in the file are never copied
into a note: ingest_paper.py fetches them from the sources, so a typo or a
chimeric entry in the old library does not become a vault fact (resolve_refs.py
then checks the note against the sources). An entry with neither identifier is
listed (`no_identifier`) for the researcher, with its title, to look up by hand
(`paper_card.py --title`), or with `--title-lookup` looked up here by the same
exact-title rule (OpenAlex, then arXiv); nothing is guessed.

The researcher's own PDFs: a DOI entry whose `file` field (Zotero / Better
BibTeX export with files; relative to the .bib) or `--pdf-dir/<key>.pdf`
holds a PDF is ingested from its text (pdftotext; `--pdf-text`, the note
naming the file, never a local path), with text no reader sees marked as at
ingestion. An arXiv entry keeps arXiv's open text.

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
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "citations"))
import retraction  # noqa: E402

TOOL = "kairo/import_library@1.1.0"


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


_PDF_IN = re.compile(r"(?:[A-Za-z]:[\\/])?[^;:]*?\.pdf", re.I)


def local_pdf(e: dict, base: Path, pdf_dir: Path | None) -> Path | None:
    """The researcher's own PDF of an entry: a Zotero / Better BibTeX `file` field
    (`Full Text PDF:files/x.pdf:application/pdf`, a plain path, several joined
    by `;`; relative to the .bib), else `<pdf-dir>/<citation key>.pdf`."""
    raw = (e.get("file") or "").replace("\\:", ":").replace("\\\\", "\\")
    for part in raw.split(";"):
        for m in _PDF_IN.finditer(part.strip()):
            p = Path(m.group(0).strip())
            p = p if p.is_absolute() else base / p
            if p.is_file():
                return p
    if pdf_dir and e.get("key") and (pdf_dir / f"{e['key']}.pdf").is_file():
        return pdf_dir / f"{e['key']}.pdf"
    return None


def pdf_to_text(pdf: Path, out_dir: Path) -> Path | None:
    """pdftotext's UTF-8 text of `pdf` in `out_dir`, or None (no pdftotext, or no text)."""
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    out = out_dir / (pdf.stem + ".txt")
    try:
        r = subprocess.run([exe, "-enc", "UTF-8", str(pdf), str(out)], capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not out.is_file() or not out.stat().st_size:
        return None
    # text the PDF draws but no reader sees stays verbatim, inside the hidden-text mark
    import verbatim_fulltext as vf
    marked = vf.mark_hidden(out.read_text(encoding="utf-8", errors="replace"), vf.pdf_hidden_runs(pdf.read_bytes())["runs"])
    out.write_text(marked, encoding="utf-8", newline="\n")
    return out


def by_title(title: str, fetch) -> dict:
    """paper_card's exact-title rule: one work with exactly this title is the paper;
    none, or several, and nothing is guessed."""
    import paper_card
    if not (title or "").strip():
        return {"why": "sin título ni identificador"}
    found = paper_card.find_by_title(title, fetch)
    ch = found.get("chosen")
    if ch and (ch.get("arxiv") or ch.get("doi")):
        return {"kind": "arxiv" if ch.get("arxiv") else "doi", "id": ch.get("arxiv") or ch.get("doi"),
                "found_by": f"título exacto ({ch.get('found_by') or 'OpenAlex'})"}
    return {"why": found.get("error") or "no encontrado por título exacto"}


def from_csl(items: list[dict]) -> list[dict]:
    out = []
    for it in items:
        e = {"key": str(it.get("id") or it.get("citation-key") or ""), "title": it.get("title") or "",
             "doi": it.get("DOI") or "", "url": it.get("URL") or "", "note": it.get("number") or "",
             "arxiv": it.get("arxiv") or ""}
        out.append(e)
    return out


def default_fetch(url: str, headers: dict) -> bytes:
    import net
    return net.get(url, headers=headers)


def main(argv: list[str] | None = None, fetch=default_fetch) -> int:
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
    ap.add_argument("--pdf-dir", type=Path, help="your PDFs named <citation key>.pdf (a `file` field is used first)")
    ap.add_argument("--title-lookup", action="store_true",
                    help="look an entry with no arXiv id or DOI up by its exact title (OpenAlex, then arXiv); "
                         "an ambiguous or missing title is listed, never guessed")
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
    base = (a.bib or a.csl).resolve().parent
    for e in entries:
        kind, ident = identifier(e)
        found_by = None
        if not kind:
            hit = by_title(e.get("title") or "", fetch) if a.title_lookup else \
                {"why": "sin arXiv ni DOI (--title-lookup lo busca por título exacto)"}
            if not hit.get("kind"):
                none.append({"key": e.get("key"), "title": e.get("title"), "why": hit.get("why")})
                continue
            kind, ident, found_by = hit["kind"], hit["id"], hit["found_by"]
        if (kind, ident) in seen:
            continue
        seen.add((kind, ident))
        row = {"key": e.get("key"), "kind": kind, "id": ident, "title": e.get("title")}
        if found_by:
            row["found_by"] = found_by
        # the researcher's own PDF: the full text of a paper with a DOI and no open text
        # (an arXiv preprint keeps arXiv's open, LaTeX-preserving text)
        pdf = local_pdf(e, base, a.pdf_dir) if kind == "doi" else None
        if pdf:
            row["pdf"], row["_pdf_path"] = pdf.name, pdf
        plan.append(row)
    if a.limit:
        plan = plan[:a.limit]
    result = {"tool": TOOL, "entries": len(entries), "plan": [{k: v for k, v in p.items() if not k.startswith("_")}
                                                               for p in plan], "no_identifier": none}
    if a.dry_run:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0
    done, counts = [], {"created": 0, "exists": 0, "failed": 0, "no_identifier": len(none)}
    tmp = tempfile.TemporaryDirectory(prefix="kairo-import-")
    for p in plan:
        extra: list[str] = []
        if p.get("_pdf_path"):
            txt = pdf_to_text(p["_pdf_path"], Path(tmp.name))
            if txt:
                # the note names the file, never a local path
                extra = ["--pdf-text", str(txt), "--source-url", f"file:{p['_pdf_path'].name}"]
        code, out = ingest(["add", "--vault", str(a.vault), "--project", a.project, f"--{p['kind']}", p["id"],
                            "--source", "manual", *extra])
        status = out.get("status") if code == 0 else "failed"
        counts["created" if status == "created" else "exists" if status == "exists" else "failed"] += 1
        row = {"key": p["key"], "id": out.get("id"), "status": status,
               "reason": out.get("refused") or out.get("error"), "warnings": out.get("warnings") or []}
        if p.get("_pdf_path"):
            row["pdf"] = p["_pdf_path"].name if extra else f"{p['_pdf_path'].name} (sin texto: ¿pdftotext instalado?)"
        if a.zotero_keys and code == 0 and out.get("id") and p.get("key"):
            zc, zout = ingest(["zotero-key", "--vault", str(a.vault), "--id", out["id"], "--key", p["key"]])
            row["zotero_key"] = p["key"] if zc == 0 else None
        done.append(row)
    tmp.cleanup()
    result.update(results=done, counts=counts)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
