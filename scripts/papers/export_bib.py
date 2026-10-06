#!/usr/bin/env python3
"""Export the vault's paper notes as BibTeX or CSL-JSON — no Zotero needed.

    export_bib.py --vault <vault> [--project PROJ-XXX | --only P-0001 P-0002 …]
                  [--format bibtex|csl-json] [--out refs.bib]

Built only from each note's frontmatter, which ingest_paper.py / resolve_refs.py
filled from the fetched records (never typed by a model). One entry per note:

  - citation key: the note's `zotero_key` (Better BibTeX) when it has one, else
    <first-author surname><year><first title word>, made unique with a, b, …;
  - a preprint with a known published version (`published_doi`,
    `published_venue`) is exported as that version, with the arXiv id kept as
    `eprint` — cite what was peer-reviewed, keep the link to the text you read;
  - `keywords = {kairo:P-XXXX}` ties each entry back to its note;
  - the entry type follows the type the publisher registered (`venue_type`:
    Crossref `journal-article` / `proceedings-article` / …, OpenAlex source
    `journal` / `conference`); only a note without one falls back to reading
    the venue's name (PNAS, Proceedings of the IEEE and the like are journals);
  - `volume`, `number` and `pages` come with the published record when it has
    them; math in a title (`$[[n,k,d]]$`) is kept as LaTeX, never escaped.

A `send: never` note is never read: it is reported by id only. A note whose
reference is in conflict, retracted or withdrawn (`resolution_status`) is
exported with a `note` field saying so, and listed in the report, so it is not
cited by accident. Prints a JSON report (to stderr when the entries go to
stdout). Exit: 0 ok · 1 error · 2 bad input. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "citations"))
sys.path.insert(0, str(HERE.parent / "security"))
import vaultnotes as vn  # noqa: E402
from send_guard import is_flagged  # noqa: E402

TOOL = "kairo/export_bib@1.2.0"
WARN = {"mismatch": "ATENCIÓN: referencia en conflicto en Kairo (resolution_status: mismatch); no citar sin revisar",
        "retracted": "ATENCIÓN: paper RETRACTADO", "withdrawn": "ATENCIÓN: preprint RETIRADO por sus autores"}
PROCEEDINGS = re.compile(r"\b(proc(?:eedings)?|conference|symposium|workshop|SC\d*|IPDPS|ISC|NeurIPS|ICML|ICLR|"
                         r"MLSys|OSDI|NSDI|SOSP|PPoPP|HPDC|ICS|QCE|QIP|CVPR|ACL|EMNLP|AAAI|"
                         r"Advances in Neural Information Processing Systems)\b", re.I)
# venues named "Proceedings …" that are journals
JOURNAL_PROCEEDINGS = re.compile(r"\b(?:Proc(?:eedings|\.)?\s+(?:of\s+)?(?:the\s+)?(?:National Academy|IEEE\b|"
                                 r"(?:the\s+)?Royal Society|R\.\s*Soc|Natl\.?\s*Acad|Japan Academy|"
                                 r"the American Mathematical Society|Amer\.?\s*Math)|PNAS\b)", re.I)
TYPE_ENTRY = {"journal-article": "article", "journal": "article", "article": "article",
              "proceedings-article": "inproceedings", "conference": "inproceedings",
              "book-chapter": "incollection", "book-section": "incollection", "book": "book",
              "monograph": "book", "dissertation": "phdthesis", "report": "techreport",
              "posted-content": "misc", "repository": "misc"}
VENUE_FIELD = {"article": "journal", "inproceedings": "booktitle", "incollection": "booktitle",
               "book": "publisher", "phdthesis": "school", "techreport": "institution"}


def _latex(s: str) -> str:
    s = s.replace("\\", r"\textbackslash{}")
    for ch in "&%$#_{}":
        s = s.replace(ch, "\\" + ch)
    return s.replace("~", r"\textasciitilde{}").replace("^", r"\textasciicircum{}")


def _protect(title: str) -> str:
    """Keep acronyms and capitalised technical words (qLDPC, GPU, BP-OSD) as written;
    `$…$` math is passed through untouched (it is already LaTeX)."""
    out = []
    for i, part in enumerate(re.split(r"(\$[^$]+\$)", title)):
        if i % 2:
            out.append("{" + part + "}")
        else:
            out.append(re.sub(r"\b(\w*[A-Z]\w*[A-Z0-9]\w*|[A-Z][a-z]*\d\w*)\b", r"{\1}", _latex(part)))
    return "".join(out)


def _surname(author: str) -> str:
    a = author.strip()
    if "," in a:
        return a.split(",", 1)[0].strip()
    parts = a.split()
    return parts[-1] if parts else ""


def _ascii(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode())


def note_meta(fm: list[str]) -> dict:
    g = lambda k: (vn.fm_get(fm, k) or "").strip()  # noqa: E731
    return {"id": g("id"), "title": g("title"), "authors": vn.parse_flow_list(vn.fm_raw(fm, "authors")),
            "year": g("year"), "venue": g("venue"), "doi": g("doi"), "arxiv": g("arxiv"),
            "arxiv_version": g("arxiv_version"), "url": g("url"), "published_doi": g("published_doi"),
            "published_venue": g("published_venue"), "published_year": g("published_year"),
            "zotero_key": g("zotero_key"), "venue_type": g("venue_type"), "volume": g("volume"),
            "issue": g("issue"), "pages": g("pages"), "openalex": g("openalex"),
            "status": g("resolution_status"), "source": g("source")}


def effective(m: dict) -> dict:
    """The record to cite. A published version is cited only with its own year:
    a venue (or DOI) whose year no source gave is never paired with the
    preprint's year — the preprint is cited, and the published version named in
    a note."""
    if (m.get("published_venue") or m.get("published_doi")) and not str(m.get("published_year") or "").strip():
        bits = [b for b in (m.get("published_venue"),
                            f"DOI {m['published_doi']}" if m.get("published_doi") else "") if b]
        return {**m, "published_venue": "", "published_doi": "",
                "pub_note": "Versión publicada: " + ", ".join(bits)
                            + " (año de la versión publicada no consta; se cita el preprint)"}
    return m


def entry_type(m: dict) -> tuple[str, str | None]:
    m = effective(m)
    venue = m.get("published_venue") or (m["venue"] if m.get("venue") and m["venue"] != "arXiv preprint" else "")
    if m.get("source") == "patentsview":
        return "patent", None
    if not venue:
        return "misc", None
    kind = TYPE_ENTRY.get((m.get("venue_type") or "").lower())
    if kind and kind != "misc":
        return kind, VENUE_FIELD.get(kind)
    if JOURNAL_PROCEEDINGS.search(venue):
        return "article", "journal"
    return ("inproceedings", "booktitle") if PROCEEDINGS.search(venue) else ("article", "journal")


def base_key(m: dict) -> str:
    m = effective(m)
    first = _ascii(_surname(m["authors"][0])).lower() if m.get("authors") else "anon"
    word = next((w for w in re.findall(r"[A-Za-z]{4,}", m.get("title") or "")
                 if w.lower() not in {"with", "from", "that", "this", "towards", "using"}), "paper")
    year = (m.get("published_year") if m.get("published_venue") or m.get("published_doi") else "") or m.get("year")
    return f"{first or 'anon'}{year or ''}{word.lower()}"


def notes(m: dict) -> str:
    """The entry's `note`: a reference warning and / or the unpublished-year note."""
    return "; ".join(n for n in (WARN.get(m.get("status") or ""), m.get("pub_note")) if n)


def bibtex_entry(m: dict, key: str) -> str:
    m = effective(m)
    kind, venue_field = entry_type(m)
    venue = m.get("published_venue") or (m["venue"] if m.get("venue") != "arXiv preprint" else "")
    doi = m.get("published_doi") or m.get("doi") or ""
    year = (m.get("published_year") if m.get("published_venue") or m.get("published_doi") else "") or m.get("year") or ""
    fields = [("title", "{" + _protect(m.get("title") or "") + "}"),
              ("author", "{" + " and ".join(_latex(a) for a in m.get("authors") or []) + "}"),
              ("year", "{" + str(year) + "}")]
    if venue_field and venue:
        fields.append((venue_field, "{" + _latex(venue) + "}"))
    for src, dst in (("volume", "volume"), ("issue", "number"), ("pages", "pages")):
        if m.get(src) and kind != "misc":
            fields.append((dst, "{" + _latex(m[src]) + "}"))
    if doi:
        fields.append(("doi", "{" + doi + "}"))
    if m.get("arxiv"):
        fields += [("eprint", "{" + m["arxiv"] + "}"), ("archiveprefix", "{arXiv}")]
        if kind == "misc":
            fields.append(("howpublished", "{arXiv preprint arXiv:" + m["arxiv"] + (m.get("arxiv_version") or "") + "}"))
    if m.get("url") and not doi:
        fields.append(("url", "{" + m["url"] + "}"))
    if notes(m):
        fields.append(("note", "{" + _latex(notes(m)) + "}"))
    if m.get("id"):
        fields.append(("keywords", "{kairo:" + m["id"] + "}"))
    body = ",\n".join(f"  {k} = {v}" for k, v in fields if v not in ("{}",))
    return f"@{kind}{{{key},\n{body}\n}}\n"


def csl_item(m: dict, key: str) -> dict:
    m = effective(m)
    kind, _ = entry_type(m)
    item = {"id": key, "type": {"article": "article-journal", "inproceedings": "paper-conference",
                                "incollection": "chapter", "book": "book", "phdthesis": "thesis",
                                "techreport": "report", "misc": "article", "patent": "patent"}[kind],
            "title": m.get("title"),
            "author": [{"family": _surname(a), "given": (a.split(",", 1)[1].strip() if "," in a
                                                         else " ".join(a.split()[:-1]))} for a in m.get("authors") or []]}
    year = str((m.get("published_year") if m.get("published_venue") or m.get("published_doi") else "")
               or m.get("year") or "")
    if year.isdigit():
        item["issued"] = {"date-parts": [[int(year)]]}
    venue = m.get("published_venue") or (m["venue"] if m.get("venue") != "arXiv preprint" else "")
    if venue:
        item["container-title"] = venue
    if m.get("published_doi") or m.get("doi"):
        item["DOI"] = m.get("published_doi") or m.get("doi")
    for src, dst in (("volume", "volume"), ("issue", "issue"), ("pages", "page")):
        if m.get(src) and kind != "misc":
            item[dst] = m[src].replace("--", "-")
    if m.get("arxiv"):
        item["number"] = f"arXiv:{m['arxiv']}"
    if notes(m):
        item["note"] = notes(m)
    item["keyword"] = f"kairo:{m['id']}"
    return item


def collect(vault: Path, project: str | None, only: list[str] | None) -> tuple[list[dict], list[str]]:
    metas, skipped = [], []
    for p in sorted((vault / "Papers").glob("P-*.md")):
        pid = p.name.split(" ")[0].removesuffix(".md")
        if only and pid not in only:
            continue
        if is_flagged(p):
            skipped.append(pid)
            continue
        fm = (vn.split_frontmatter(p.read_text(encoding="utf-8")) or ([], ""))[0]
        if project and project not in re.findall(r"PROJ-[\w-]+", vn.fm_raw(fm, "projects") or ""):
            continue
        m = note_meta(fm)
        m["id"] = m["id"] or pid
        metas.append(m)
    return metas, skipped


def keys_for(metas: list[dict]) -> list[str]:
    used: dict[str, int] = {}
    out = []
    for m in metas:
        k = m.get("zotero_key") or base_key(m)
        n = used.get(k, 0)
        used[k] = n + 1
        out.append(k if n == 0 else k + "abcdefghijklmnopqrstuvwxyz"[min(n - 1, 25)])
    return out


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vault", type=Path, required=True)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--project")
    g.add_argument("--only", nargs="+")
    ap.add_argument("--format", choices=("bibtex", "csl-json"), default="bibtex")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    if not (a.vault / "Papers").is_dir():
        print(json.dumps({"tool": TOOL, "error": "no Papers/ folder"}), file=sys.stderr)
        return 2
    metas, skipped = collect(a.vault, a.project, a.only)
    keys = keys_for(metas)
    if a.format == "bibtex":
        text = "\n".join(bibtex_entry(m, k) for m, k in zip(metas, keys))
    else:
        text = json.dumps([csl_item(m, k) for m, k in zip(metas, keys)], ensure_ascii=False, indent=2) + "\n"
    report = {"tool": TOOL, "entries": len(metas), "skipped_send_never": skipped,
              "flagged": [{"id": m["id"], "status": m["status"]} for m in metas if m["status"] in WARN],
              "as_published_version": [m["id"] for m in metas if m.get("published_doi") or m.get("published_venue")]}
    if a.out:
        a.out.write_text(text, encoding="utf-8", newline="\n")
        report["out"] = str(a.out)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        sys.stdout.write(text)
        print(json.dumps(report, ensure_ascii=False), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
