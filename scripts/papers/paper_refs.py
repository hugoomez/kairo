#!/usr/bin/env python3
"""A paper's own bibliography, from the bytes ingestion kept — no network.

    paper_refs.py extract --vault <vault> (--only P-XXXX … | --project PROJ-XXX | --all) [--dry-run]
    paper_refs.py list    --vault <vault> --id P-XXXX [--text]
    paper_refs.py corpus  --vault <vault> --project PROJ-XXX [--min 2] [--top 30]

A paper note's `## Texto completo` stops at the bibliography (the note holds the
paper's prose). The references are still in the bytes kept in
`Papers/_fuentes/<P-id>/`: `extract` reads them from there and writes
`Papers/_fuentes/<P-id>/referencias.json` beside them — the note itself is never
touched, so `ingest_paper.py verify` is unaffected and a note already ingested
with its bytes kept gets its references without a re-ingestion. A `legacy` note
(ingested before ingest_paper.py kept bytes) has nothing to read: it needs
`ingest_paper.py rebuild --only <P-id>` (a network re-fetch) first.

  - arXiv HTML / ar5iv (LaTeXML): each `ltx_bibitem` — its label as printed
    (`[12]`, `Doe et al. (2020)`), its text verbatim (math as `$…$`), and the
    in-text anchor (`bib.bib12`) the body's citations link to;
  - a PDF (kept as text, or converted with `pdftotext`): the lines after the
    `References` heading, split into entries at `[N]` / `N.` labels; an entry
    split badly by the PDF is kept as extracted, never repaired.

From each entry only identifiers the text itself states are taken — an arXiv
id (`arXiv:2401.01234`, `arxiv.org/abs/…`) or a DOI (`10.xxxx/…`, `doi.org/…`)
— never a title guessed into an id. Each identifier is matched against the
vault (`in_vault: P-XXXX`).

`list` prints a paper's references as labels, identifiers and vault matches;
`--text` adds the reference strings (third-party text: the vault hook refuses
it in the main session, like `cite_text.py`). `corpus` counts, over a project's
papers, the works their bibliographies cite with an identifier and that the
vault does not hold yet, most cited first: the corpus's own blind spots,
ready as `lit_search.py snowball --seeds`. Identifiers only, no text.

`send: never` notes are skipped (never opened). Standard library only (plus the
`pdftotext` binary for a PDF kept as bytes).

Exit: 0 ok · 1 nothing to extract · 2 bad input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "citations"))
sys.path.insert(0, str(HERE.parent / "security"))
import retraction  # noqa: E402
import vaultnotes as vn  # noqa: E402
import verbatim_fulltext as vf  # noqa: E402
from send_guard import is_flagged, is_model_notes  # noqa: E402

TOOL = "kairo/paper_refs@1.0.0"
OUT = "referencias.json"
_ARXIV = re.compile(r"(?:arxiv(?:\.org/(?:abs|pdf)/|:\s*)|\babs/)((?:\d{4}\.\d{4,5})|(?:[a-z-]+(?:\.[A-Z]{2})?/\d{7}))"
                    r"(?:v\d+)?", re.I)
_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>{}]+)", re.I)
_PDF_LABEL = re.compile(r"^\s*(\[\d{1,4}\]|\d{1,4}\.)\s+")


def _ids(text: str, hrefs: list[str] = ()) -> dict:
    hay = " ".join([text, *hrefs])
    arx = sorted({retraction.normalize_arxiv(m.group(1)) for m in _ARXIV.finditer(hay)} - {None})
    dois = sorted({retraction.normalize_doi(m.group(1).rstrip(".,;)]")) for m in _DOI.finditer(hay)} - {None})
    # an arXiv DOI is the arXiv id said another way
    for d in [d for d in dois if retraction.is_arxiv_doi(d)]:
        dois.remove(d)
        a = retraction.arxiv_from_doi(d)
        if a and a not in arx:
            arx.append(a)
    return {"arxiv": arx, "doi": dois}


def _hrefs(n) -> list[str]:
    out = []
    for c in n.children:
        if isinstance(c, str):
            continue
        if c.tag == "a" and c.attrs.get("href"):
            out.append(c.attrs["href"])
        out += _hrefs(c)
    return out


def from_html(html_text: str) -> list[dict]:
    tree = vf._Tree()
    tree.feed(html_text)
    items = list(vf._iter(tree.root, lambda x: x.tag == "li" and x.has("ltx_bibitem")))
    out = []
    for n, item in enumerate(items, 1):
        tag_node = next(vf._iter(item, lambda x: x.has("ltx_tag_bibitem") or x.has("ltx_tag")), None)
        label = vf.norm(vf.text_of(tag_node)) if tag_node is not None else ""
        whole = vf.norm(vf.text_of(item))
        text = whole[len(label):].strip() if label and whole.startswith(label) else whole
        out.append({"n": n, "label": label or f"[{n}]", "anchor": item.attrs.get("id") or None,
                    "text": text, **_ids(text, _hrefs(item))})
    return out


def from_pdf_text(txt: str) -> list[dict]:
    lines = [vf._lig(ln.rstrip()) for ln in txt.replace("\f", "\n").split("\n")]
    start = next((i for i, ln in enumerate(lines) if vf._PDF_REFS.fullmatch(ln.strip())), None)
    if start is None:
        return []
    entries: list[list[str]] = []
    labels: list[str] = []
    for ln in lines[start + 1:]:
        s = ln.strip()
        if not s or re.fullmatch(r"\d{1,3}", s):
            continue
        m = _PDF_LABEL.match(s)
        if m:
            labels.append(m.group(1))
            entries.append([s[m.end():]])
        elif entries:
            entries[-1].append(s)
    out = []
    for n, (label, parts) in enumerate(zip(labels, entries), 1):
        # a line broken at a hyphen is joined as printed: "quan-" + "tum" stays "quan- tum"
        text = vf.norm(" ".join(parts))
        out.append({"n": n, "label": label, "anchor": None, "text": text, **_ids(text)})
    return out


def note_meta(path: Path) -> tuple[list[str], str] | None:
    text = path.read_text(encoding="utf-8")
    split = vn.split_frontmatter(text)
    return (split[0], text) if split else None


def paper_notes(vault: Path) -> list[Path]:
    return sorted(p for p in (vault / "Papers").glob("P-*.md") if p.is_file() and not is_model_notes(p.resolve()))


def note_id(path: Path, fm: list[str]) -> str:
    return vn.fm_get(fm, "id") or path.name.split(" ")[0]


def extract_one(vault: Path, path: Path, dry_run: bool = False) -> dict:
    if is_flagged(path):
        return {"status": "send_never"}
    meta = note_meta(path)
    if not meta:
        return {"status": "no_frontmatter"}
    fm, _ = meta
    rel = vn.fm_get(fm, "fuentes")
    mpath = vault / rel if rel else None
    if not mpath or not mpath.is_file():
        return {"status": "legacy", "note": "sin bytes guardados (ingestión antigua): `ingest_paper.py rebuild`"}
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    f = next((x for x in manifest["files"] if x.get("role") == "fulltext"), None)
    if f is None:
        return {"status": "no_fulltext"}
    data = (mpath.parent / f["file"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != f["sha256"]:
        return {"status": "refused", "reason": f"{f['file']} cambió (sha256 distinto)"}
    kind = f.get("kind") or ""
    if kind == "pdf-text":
        refs, how = from_pdf_text(data.decode("utf-8", errors="replace")), "texto del PDF tras «References»"
    elif kind == "pdf":
        txt = vf.pdf_bytes_to_text(data) if vf.pdftotext_available() else None
        if txt is None:
            return {"status": "no_pdftotext", "note": "instala poppler (pdftotext) para leer el PDF guardado"}
        refs, how = from_pdf_text(txt), "pdftotext tras «References»"
    else:                                   # arXiv HTML / ar5iv (LaTeXML), the same converter as the body
        refs, how = from_html(data.decode("utf-8", errors="replace")), "LaTeXML ltx_bibitem"
    if not refs:
        return {"status": "none_found", "source": f["file"]}
    out = {"tool": TOOL, "source": {"file": f["file"], "sha256": f["sha256"], "kind": kind, "url": f.get("url")},
           "how": how, "entries": refs}
    if not dry_run:
        (mpath.parent / OUT).write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n",
                                        encoding="utf-8", newline="\n")
    return {"status": "ok", "references": len(refs),
            "with_ids": sum(1 for r in refs if r["arxiv"] or r["doi"])}


def vault_index(vault: Path) -> dict[str, str]:
    """arxiv:<id> / doi:<doi> → P-id, for every readable note (published DOIs too)."""
    idx = {}
    for p in paper_notes(vault):
        if is_flagged(p):
            continue
        meta = note_meta(p)
        if not meta:
            continue
        fm, _ = meta
        pid = note_id(p, fm)
        a = retraction.normalize_arxiv(vn.fm_get(fm, "arxiv"))
        if a:
            idx[f"arxiv:{a}"] = pid
        for k in ("doi", "published_doi"):
            d = retraction.normalize_doi(vn.fm_get(fm, k))
            if d:
                idx[f"doi:{d}"] = pid
    return idx


def load_refs(vault: Path, path: Path) -> dict | None:
    meta = note_meta(path)
    if not meta or is_flagged(path):
        return None
    rel = vn.fm_get(meta[0], "fuentes")
    f = (vault / rel).parent / OUT if rel else None
    return json.loads(f.read_text(encoding="utf-8")) if f and f.is_file() else None


def find_note(vault: Path, pid: str) -> Path | None:
    hits = [p for p in paper_notes(vault) if p.name.split(" ")[0].removesuffix(".md") == pid]
    return hits[0] if len(hits) == 1 else None


def project_notes(vault: Path, project: str) -> list[Path]:
    out = []
    for p in paper_notes(vault):
        meta = note_meta(p)
        if meta and re.search(r"(?<![\w-])" + re.escape(project) + r"(?![\w-])", vn.fm_get(meta[0], "projects") or ""):
            out.append(p)
    return out


def cmd_list(vault: Path, pid: str, text: bool) -> dict:
    p = find_note(vault, pid)
    if p is None:
        raise ValueError(f"{pid}: no single paper note")
    if is_flagged(p):
        return {"id": pid, "status": "send_never"}
    data = load_refs(vault, p)
    if data is None:
        return {"id": pid, "status": "not_extracted", "note": f"run `paper_refs.py extract --only {pid}` first"}
    idx = vault_index(vault)
    rows = []
    for r in data["entries"]:
        hit = next((idx[k] for k in [f"arxiv:{a}" for a in r["arxiv"]] + [f"doi:{d}" for d in r["doi"]]
                    if k in idx), None)
        row = {"n": r["n"], "label": r["label"], "arxiv": r["arxiv"], "doi": r["doi"], "in_vault": hit}
        if text:
            row["text"] = r["text"]
        rows.append(row)
    return {"id": pid, "how": data["how"], "references": len(rows),
            "with_ids": sum(1 for r in rows if r["arxiv"] or r["doi"]),
            "in_vault": sum(1 for r in rows if r["in_vault"]), "entries": rows}


def cmd_corpus(vault: Path, project: str, min_count: int, top: int) -> dict:
    idx = vault_index(vault)
    counts: dict[str, set[str]] = {}
    papers = project_notes(vault, project)
    extracted = 0
    for p in papers:
        data = load_refs(vault, p)
        if data is None:
            continue
        extracted += 1
        pid = note_id(p, note_meta(p)[0])
        for r in data["entries"]:
            keys = [f"arxiv:{a}" for a in r["arxiv"]] + [f"doi:{d}" for d in r["doi"]]
            if not keys or any(k in idx for k in keys):
                continue
            counts.setdefault(keys[0], set()).add(pid)
    ranked = sorted(((k, sorted(v)) for k, v in counts.items() if len(v) >= min_count),
                    key=lambda kv: (-len(kv[1]), kv[0]))[:top]
    seeds = [("arXiv:" + k[6:]) if k.startswith("arxiv:") else ("DOI:" + k[4:]) for k, _ in ranked]
    return {"project": project, "papers": len(papers), "with_references": extracted,
            "not_in_vault": [{"id": k, "cited_by": v, "count": len(v)} for k, v in ranked],
            "snowball_seeds": " ".join(seeds)}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract")
    e.add_argument("--vault", required=True, type=Path)
    g = e.add_mutually_exclusive_group(required=True)
    g.add_argument("--only", nargs="+")
    g.add_argument("--project")
    g.add_argument("--all", action="store_true")
    e.add_argument("--dry-run", action="store_true")
    li = sub.add_parser("list")
    li.add_argument("--vault", required=True, type=Path)
    li.add_argument("--id", required=True)
    li.add_argument("--text", action="store_true", help="also the reference strings (third-party text)")
    co = sub.add_parser("corpus")
    co.add_argument("--vault", required=True, type=Path)
    co.add_argument("--project", required=True)
    co.add_argument("--min", type=int, default=2, help="cited by at least this many of the project's papers")
    co.add_argument("--top", type=int, default=30)
    a = ap.parse_args(argv)
    if not (a.vault / "Papers").is_dir():
        print(json.dumps({"tool": TOOL, "error": f"no Papers/ under {a.vault}"}))
        return 2
    try:
        if a.cmd == "extract":
            notes = (project_notes(a.vault, a.project) if a.project else paper_notes(a.vault) if a.all
                     else [p for p in (find_note(a.vault, x) for x in a.only) if p])
            res = [{"id": p.name.split(" ")[0].removesuffix(".md"), **extract_one(a.vault, p, a.dry_run)}
                   for p in notes]
            print(json.dumps({"tool": TOOL, "notes": res}, ensure_ascii=False, indent=1))
            return 0 if any(r["status"] == "ok" for r in res) else 1
        out = cmd_list(a.vault, a.id, a.text) if a.cmd == "list" else cmd_corpus(a.vault, a.project, a.min, a.top)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"tool": TOOL, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"tool": TOOL, **out}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
