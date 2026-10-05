#!/usr/bin/env python3
"""Ingest one paper into a vault's Papers/ — mechanically, end to end.

    ingest_paper.py add     --vault <vault> --project PROJ-XXX (--arxiv ID | --doi DOI)
                            [--version vN] [--pdf-text FILE --source-url URL]
                            [--facet A --matched "<term>"] [--source arxiv|semantic-scholar|manual]
                            [--no-fulltext] [--dry-run]
    ingest_paper.py rebuild --vault <vault> --only P-XXXX [P-YYYY …]
    ingest_paper.py verify  --vault <vault> [--only P-XXXX …]
    ingest_paper.py zotero-key --vault <vault> --id P-XXXX --key <citekey>

A paper note's source fields — the frontmatter metadata, `## Referencia`,
`## Resumen`, `## Texto completo` — are never typed by a model. `add` fetches
them and writes the whole note itself:

  - metadata: the arXiv API entry (arXiv id given) or the Crossref record (DOI
    given); OpenAlex for the abstract when neither has one, and for the
    published version of a preprint;
  - `## Resumen`: the abstract exactly as the record holds it (whitespace
    collapsed), with its `> Fuente:` line;
  - `## Texto completo`: verbatim_fulltext.py's output (arXiv HTML → ar5iv →
    PDF), or `--pdf-text` for an open-access PDF that is not on arXiv.

Every byte fetched is kept in `Papers/_fuentes/<P-id>/` with a manifest
(`fuentes.json`: file, URL, sha256, date, converter version). `verify`
rebuilds the three sections from those bytes and compares them with the note:
a section edited after ingestion (by hand or by a model) is reported, and so
is a raw file whose sha256 changed. `rebuild` re-fetches and rewrites the
three sections of a note ingested before this script existed, keeping its
frontmatter. A `send: never` note is never opened or rewritten.

A preprint that arXiv or OpenAlex says was published (journal_ref, DOI,
a journal / proceedings location) gets `published_doi`, `published_venue`,
`published_year` — the note stays anchored on the text it holds.

Prints one JSON object. Exit codes: 0 ok · 1 error · 2 refused (bad input,
send: never, nothing fetchable) · 3 verify found an integrity problem.
Standard library only (plus `pdftotext` for PDFs).
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
sys.path.insert(0, str(HERE.parent / "security"))
import facet_assignment as fa  # noqa: E402
import net  # noqa: E402
import retraction  # noqa: E402
import vaultnotes as vn  # noqa: E402
import verbatim_fulltext as vf  # noqa: E402
from fill_abstract import from_inverted_index, from_jats  # noqa: E402
from send_guard import is_flagged  # noqa: E402

TOOL = "kairo/ingest_paper@1.0.0"
FUENTES_DIR = "_fuentes"
NO_ABSTRACT = "No disponible — ningún abstract recuperado"
NO_FULLTEXT = "No disponible — solo abstract."
SECTIONS = ("Referencia", "Resumen", "Texto completo")

Fetch = Callable[[str, dict], bytes]


class Refused(Exception):
    pass


def default_fetch(url: str, headers: dict) -> bytes:
    return net.get(url, headers=headers)


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def ws(s: str) -> str:
    return " ".join((s or "").split())


def norm_title(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


# --------------------------------------------------------------------------
# Records: raw bytes → metadata (pure; verify re-runs these on the kept bytes)
# --------------------------------------------------------------------------

def from_arxiv_feed(raw: bytes, aid: str) -> dict | None:
    entries = retraction.parse_arxiv_feed(raw)
    e = entries.get(retraction.normalize_arxiv(aid) or "")
    if not e:
        return None
    return {"title": e["title"], "authors": e["authors"], "year": (e["published"] or "")[:4] or None,
            "venue": "arXiv preprint", "abstract": e["summary"], "arxiv": e["id"],
            "version": f"v{e['version']}" if e.get("version") else "",
            "published_doi": e.get("doi") or "", "journal_ref": e.get("journal_ref") or ""}


def from_crossref(raw: bytes) -> dict | None:
    msg = (json.loads(raw) or {}).get("message") or {}
    if not msg:
        return None
    year = None
    for k in ("issued", "published", "published-print", "published-online"):
        parts = ((msg.get(k) or {}).get("date-parts") or [[None]])[0]
        if parts and parts[0]:
            year = str(parts[0])
            break
    authors = []
    for a in msg.get("author") or []:
        fam, giv = a.get("family"), a.get("given")
        authors.append(f"{fam}, {giv}" if fam and giv else (fam or a.get("name") or ""))
    return {"title": ws((msg.get("title") or [""])[0]), "authors": [a for a in authors if a], "year": year,
            "venue": ws((msg.get("container-title") or [""])[0]) or msg.get("type") or "",
            "abstract": from_jats(msg.get("abstract")), "doi": retraction.normalize_doi(msg.get("DOI")) or ""}


def from_openalex(raw: bytes) -> dict | None:
    w = json.loads(raw) or {}
    if not w.get("id"):
        return None
    published = None
    for loc in [w.get("primary_location") or {}] + list(w.get("locations") or []):
        src = loc.get("source") or {}
        kind = (src.get("type") or "").lower()
        if kind in ("journal", "conference") and src.get("display_name"):
            published = {"venue": src["display_name"], "doi": retraction.normalize_doi(w.get("doi")) or "",
                         "year": w.get("publication_year")}
            break
    return {"abstract": from_inverted_index(w.get("abstract_inverted_index")), "published": published,
            "openalex_id": (w.get("id") or "").rsplit("/", 1)[-1]}


# --------------------------------------------------------------------------
# Note text (pure)
# --------------------------------------------------------------------------

def referencia(meta: dict) -> str:
    a = meta.get("authors") or []
    who = "; ".join(a[:3]) + (" et al." if len(a) > 3 else "") if a else "Autores no disponibles"
    ids = []
    if meta.get("doi"):
        ids.append(f"DOI: {meta['doi']}")
    if meta.get("arxiv"):
        ids.append(f"arXiv: {meta['arxiv']}{meta.get('version') or ''}")
    parts = [f"{who} ({meta.get('year') or 's. f.'}). {meta.get('title') or 'Sin título'}."]
    if meta.get("venue"):
        parts.append(f"{meta['venue']}.")
    if ids:
        parts.append(" · ".join(ids) + ".")
    return " ".join(parts)


def resumen(abstract: str, url: str, date: str, tried: list[str]) -> str:
    if not abstract:
        return f"{NO_ABSTRACT} ({', '.join(tried) or 'ninguna fuente'})."
    return f"> Fuente: {url}, obtenido {date} (texto del registro, sin reescribir; {TOOL})\n\n{abstract}"


def section_body(text: str, name: str) -> str | None:
    m = re.search(rf"^## {re.escape(name)}[ \t]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else None


def replace_section(text: str, name: str, body: str) -> str:
    block = f"## {name}\n\n{body.strip()}\n\n"
    if re.search(rf"^## {re.escape(name)}[ \t]*$", text, re.M):
        return re.sub(rf"^## {re.escape(name)}[ \t]*\n.*?(?=^## |\Z)", lambda _: block, text,
                      count=1, flags=re.M | re.S)
    return text.rstrip("\n") + "\n\n" + block


def short_title(title: str) -> str:
    words = re.sub(r"[^\w\s-]", "", unicodedata.normalize("NFKC", title)).split()
    return " ".join(words[:8])[:70].strip() or "paper"


def frontmatter(fields: list[tuple[str, object]]) -> str:
    out = []
    for k, v in fields:
        if isinstance(v, list):
            out.append(f"{k}: [" + ", ".join(x if re.fullmatch(r"[A-Za-z0-9_.\-]+", x)
                                             else json.dumps(x, ensure_ascii=False) for x in v) + "]")
        else:
            out.append(f"{k}: {vn._yaml_scalar(v)}".rstrip())
    return "---\n" + "\n".join(out) + "\n---\n"


# --------------------------------------------------------------------------
# Vault
# --------------------------------------------------------------------------

def paper_notes(vault: Path) -> list[Path]:
    return sorted(p for p in (vault / "Papers").glob("P-*.md") if p.is_file())


def next_id(vault: Path) -> str:
    nums = [int(m.group(1)) for p in paper_notes(vault) if (m := re.match(r"P-(\d+)", p.name))]
    return f"P-{(max(nums) + 1 if nums else 1):04d}"


def find_existing(vault: Path, arxiv: str, doi: str, title: str) -> Path | None:
    """By file content for normal notes; a send: never note is matched only by
    what the guard lets through (its file name's title), never opened here."""
    t = norm_title(title)
    for p in paper_notes(vault):
        if is_flagged(p):
            if t and t[:40] and t[:40] in norm_title(p.stem):
                return p
            continue
        fm = (vn.split_frontmatter(p.read_text(encoding="utf-8")) or ([], ""))[0]
        if arxiv and retraction.normalize_arxiv(vn.fm_get(fm, "arxiv")) == arxiv:
            return p
        if doi and retraction.normalize_doi(vn.fm_get(fm, "doi")) == doi:
            return p
        if t and norm_title(vn.fm_get(fm, "title") or "") == t:
            return p
    return None


def find_note(vault: Path, pid: str) -> Path:
    hits = [p for p in paper_notes(vault) if p.name.startswith(pid + " ") or p.stem == pid]
    if not hits:
        raise Refused(f"{pid}: no note in Papers/")
    return hits[0]


def add_project(text: str, project: str) -> str:
    """Append `project` to the note's inline `projects: […]` list (created if absent)."""
    split = vn.split_frontmatter(text)
    if split is None:
        raise Refused("the existing note has no frontmatter")
    fm, body = split
    have = re.findall(r"PROJ-[\w-]+", vn.fm_raw(fm, "projects") or "")
    if project in have:
        return text
    line = "projects: [" + ", ".join(have + [project]) + "]"
    out = [line if re.match(r"^projects\s*:", ln) else ln for ln in fm]
    if line not in out:
        out.append(line)
    # a block list (`projects:` then `- PROJ-…` lines) collapses into the inline form
    out = [ln for i, ln in enumerate(out)
           if not (re.match(r"^\s*-\s*PROJ-", ln) and any(o == line for o in out[:i]))]
    return "---\n" + "\n".join(out) + "\n---\n" + body


class Store:
    """Papers/_fuentes/<P-id>/: the fetched bytes and their manifest."""

    def __init__(self, vault: Path, pid: str, date: str):
        self.dir = vault / "Papers" / FUENTES_DIR / pid
        self.rel = Path("Papers", FUENTES_DIR, pid).as_posix()
        self.date = date
        self.files: list[dict] = []
        self.blobs: dict[str, bytes] = {}

    def keep(self, name: str, role: str, url: str, data: bytes, **extra) -> None:
        self.blobs[name] = data
        self.files.append({"file": name, "role": role, "url": net.redact(url), "sha256": sha256(data),
                           "bytes": len(data), "obtenido": self.date, **extra})

    def write(self) -> str:
        self.dir.mkdir(parents=True, exist_ok=True)
        for name, data in self.blobs.items():
            (self.dir / name).write_bytes(data)
        manifest = {"tool": TOOL, "converter": vf.TOOL_ID, "files": self.files}
        (self.dir / "fuentes.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8", newline="\n")
        return f"{self.rel}/fuentes.json"


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

def _oa(url_path: str) -> str:
    key = os.environ.get("OPENALEX_API_KEY")
    return f"https://api.openalex.org/{url_path}" + (("?api_key=" + urllib.parse.quote(key)) if key else "")


def gather(store: Store, arxiv: str, doi: str, fetch: Fetch) -> tuple[dict, list[str]]:
    """Fetch the metadata records into `store`; return (meta, warnings)."""
    warn: list[str] = []
    meta: dict = {}
    if arxiv:
        url = retraction.ARXIV_QUERY.format(urllib.parse.quote(arxiv, safe="/"), 1)
        raw = fetch(url, {"Accept": "application/atom+xml"})
        rec = from_arxiv_feed(raw, arxiv)
        if not rec:
            raise Refused(f"arXiv has no entry for {arxiv}")
        store.keep("metadata-arxiv.xml", "metadata", url, raw, parser="arxiv")
        meta = {**rec, "doi": ""}
        meta["abstract_url"], meta["abstract_file"] = f"https://arxiv.org/abs/{arxiv}", "metadata-arxiv.xml"
    elif doi:
        url = retraction.CROSSREF_WORK.format(urllib.parse.quote(doi, safe="/:;()"))
        try:
            raw = fetch(url, {"Accept": "application/json"})
        except net.HttpError as e:
            raise Refused(f"Crossref has no record for {doi} ({e})") from None
        rec = from_crossref(raw)
        if not rec:
            raise Refused(f"Crossref has no record for {doi}")
        store.keep("metadata-crossref.json", "metadata", url, raw, parser="crossref")
        meta = {**rec, "arxiv": "", "version": ""}
        meta["abstract_url"], meta["abstract_file"] = f"https://doi.org/{doi}", "metadata-crossref.json"
    # OpenAlex: the abstract when the record has none, and a preprint's published version
    oa_id = f"doi:10.48550/arXiv.{arxiv}" if arxiv else f"doi:{doi}"
    try:
        raw = fetch(_oa(f"works/{urllib.parse.quote(oa_id, safe=':/')}"), {})
        oa = from_openalex(raw)
        if oa:
            store.keep("metadata-openalex.json", "metadata", _oa(f"works/{oa_id}"), raw, parser="openalex")
            if not meta.get("abstract") and oa["abstract"]:
                meta["abstract"] = oa["abstract"]
                meta["abstract_url"] = f"https://api.openalex.org/works/{oa['openalex_id']}"
                meta["abstract_file"] = "metadata-openalex.json"
            if arxiv and oa["published"]:
                meta.setdefault("published_venue", oa["published"]["venue"])
    except net.HttpError as e:
        if e.not_found:
            warn.append(f"OpenAlex no tiene registro para {oa_id}")
        else:
            warn.append(f"OpenAlex no respondió ({net.redact(str(e))[:120]}): sin comprobación de versión publicada")
    except ValueError as e:
        warn.append(f"OpenAlex devolvió algo ilegible ({str(e)[:80]})")
    pdoi = meta.get("published_doi") or ""
    if arxiv and pdoi and not retraction.is_arxiv_doi(pdoi):
        # the published version's own record: its venue and year as the publisher registered them
        url = retraction.CROSSREF_WORK.format(urllib.parse.quote(pdoi, safe="/:;()"))
        try:
            raw = fetch(url, {"Accept": "application/json"})
            pub = from_crossref(raw)
            if pub:
                store.keep("metadata-crossref-published.json", "metadata", url, raw, parser="crossref-published")
                if pub.get("venue"):
                    meta["published_venue"] = pub["venue"]
                meta["published_year"] = pub.get("year") or ""
        except (net.HttpError, ValueError) as e:
            warn.append(f"Crossref no dio el registro de la versión publicada {pdoi} ({net.redact(str(e))[:80]})")
    if arxiv and (pdoi or meta.get("journal_ref")):
        meta.setdefault("published_venue", meta.get("journal_ref") or "")
    return meta, warn


def fulltext(store: Store, arxiv: str, version: str, pdf_text: Path | None, source_url: str | None,
             fetch_arxiv=vf.fetch_arxiv) -> tuple[str | None, str]:
    """(Texto completo block or None, kind)."""
    if pdf_text:
        data = pdf_text.read_bytes()
        block = vf.build_from_text(data, source_url or "", "?", store.date)
        if block:
            store.keep("texto.txt", "fulltext", source_url or "", data, kind="pdf-text", version="?")
        return block, "pdf-text"
    if not arxiv:
        return None, ""
    got = fetch_arxiv(arxiv, version=version) if version else fetch_arxiv(arxiv)
    if not got:
        return None, ""
    block = vf.build(got["kind"], got["url"], got["version"], got["bytes"], store.date)
    if block:
        ext = "pdf" if got["kind"] == "pdf" else "html"
        store.keep(f"texto.{ext}", "fulltext", got["url"], got["bytes"], kind=got["kind"], version=got["version"])
    return block, got["kind"]


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_add(a, fetch: Fetch = default_fetch, fetch_arxiv=vf.fetch_arxiv, today: str | None = None) -> dict:
    vault = a.vault.resolve()
    if not (vault / "Papers").is_dir():
        raise Refused(f"{vault} has no Papers/ folder")
    if not re.fullmatch(r"PROJ-\d{3,}", a.project or ""):
        raise Refused("--project must be a PROJ-XXX id")
    arxiv = retraction.normalize_arxiv(a.arxiv) or ""
    doi = retraction.normalize_doi(a.doi) or ""
    if bool(arxiv) == bool(doi):
        raise Refused("give exactly one of --arxiv or --doi (a preprint is anchored on its arXiv id)")
    if retraction.is_arxiv_doi(doi):
        arxiv, doi = retraction.arxiv_from_doi(doi), ""
    date = today or _dt.date.today().isoformat()
    pid = next_id(vault)
    store = Store(vault, pid, date)
    meta, warn = gather(store, arxiv, doi, fetch)

    existing = find_existing(vault, arxiv, doi, meta.get("title", ""))
    if existing:
        if is_flagged(existing):
            raise Refused(f"ya está en el vault como {existing.name.split(' ')[0]}, marcada send: never: "
                          "no se abre ni se modifica — añade el proyecto a mano si procede")
        text = existing.read_text(encoding="utf-8")
        new = add_project(text, a.project)
        if a.facet:
            new = fa.add_entry(new, a.project, a.facet, a.matched, None)
        if not a.dry_run and new != text:
            existing.write_text(new, encoding="utf-8", newline="\n")
        return {"status": "exists", "id": existing.name.split(" ")[0], "path": existing.relative_to(vault).as_posix(),
                "project_added": new != text, "written": not a.dry_run}

    tried = [store.files[0]["url"]] if store.files else []
    block, kind = (None, "") if a.no_fulltext else fulltext(
        store, arxiv, a.version or "", a.pdf_text, a.source_url, fetch_arxiv)
    if not block and not a.no_fulltext:
        warn.append("sin texto completo abierto: la nota queda abstract-only (añade el PDF con --pdf-text)")
    meta_out = {**meta, "version": (a.version or meta.get("version") or "")}
    fields: list[tuple[str, object]] = [
        ("id", pid), ("title", meta.get("title")), ("authors", meta.get("authors") or []),
        ("year", meta.get("year")), ("venue", meta.get("venue")), ("doi", doi), ("arxiv", arxiv),
        ("arxiv_version", meta_out["version"] if arxiv else ""),
        ("url", f"https://arxiv.org/abs/{arxiv}" if arxiv else f"https://doi.org/{doi}"),
        ("pdf", f"https://arxiv.org/pdf/{arxiv}{meta_out['version']}" if arxiv else (a.source_url or "")),
        ("projects", [a.project]), ("added", date), ("source", a.source),
        ("fulltext", "full" if block else "abstract-only"),
    ]
    if arxiv and (meta.get("published_doi") or meta.get("published_venue")):
        fields += [("published_doi", meta.get("published_doi") or ""),
                   ("published_venue", meta.get("published_venue") or ""),
                   ("published_year", meta.get("published_year") or ""),
                   ("journal_ref", meta.get("journal_ref") or "")]
    fields += [("ingested_by", TOOL), ("fuentes", f"{store.rel}/fuentes.json")]
    body = (f"## Referencia\n\n{referencia({**meta, 'doi': doi, 'arxiv': arxiv, 'version': meta_out['version']})}\n\n"
            f"## Resumen\n\n{resumen(meta.get('abstract', ''), meta.get('abstract_url', ''), date, tried)}\n\n"
            f"## Texto completo\n\n{block.strip() if block else NO_FULLTEXT}\n")
    text = frontmatter(fields) + "\n" + body
    if a.facet:
        text = fa.add_entry(text, a.project, a.facet, a.matched, None)
    path = vault / "Papers" / f"{pid} {short_title(meta.get('title') or pid)}.md"
    if not a.dry_run:
        # the manifest records which raw file the abstract came from, so verify can rebuild it
        for f in store.files:
            if f["file"] == meta.get("abstract_file"):
                f["abstract"] = True
        store.write()
        path.write_text(text, encoding="utf-8", newline="\n")
    return {"status": "created" if not a.dry_run else "dry_run", "id": pid,
            "path": path.relative_to(vault).as_posix(), "fulltext": kind or "abstract-only",
            "abstract": "found" if meta.get("abstract") else "missing",
            "published_version": meta.get("published_venue") or meta.get("published_doi") or None,
            "fuentes": f"{store.rel}/fuentes.json", "warnings": warn,
            # for the Zotero add: the fetched metadata, never anything a model wrote
            "csl": {"title": meta.get("title"), "authors": meta.get("authors"), "year": meta.get("year"),
                    "venue": meta.get("venue"), "doi": doi, "arxiv": arxiv,
                    "abstract": meta.get("abstract") or ""}}


def expected_sections(vault: Path, text: str) -> tuple[dict, list[str]]:
    """The three source sections as the kept bytes give them; problems found."""
    fm = (vn.split_frontmatter(text) or ([], ""))[0]
    mpath = vault / (vn.fm_get(fm, "fuentes") or "")
    problems: list[str] = []
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    raws: dict[str, bytes] = {}
    for f in manifest["files"]:
        p = mpath.parent / f["file"]
        if not p.is_file():
            problems.append(f"falta el archivo fuente {f['file']}")
            continue
        data = p.read_bytes()
        if sha256(data) != f["sha256"]:
            problems.append(f"el archivo fuente {f['file']} cambió (sha256 distinto)")
        raws[f["file"]] = data
    exp: dict[str, str] = {}
    meta: dict = {}
    for f in manifest["files"]:
        data = raws.get(f["file"])
        if data is None or f["role"] != "metadata":
            continue
        if f["parser"] == "arxiv":
            meta = {**(from_arxiv_feed(data, vn.fm_get(fm, "arxiv") or "") or {}), **meta}
        elif f["parser"] == "crossref":
            meta = {**(from_crossref(data) or {}), **meta}
        if f.get("abstract"):
            ab = (from_openalex(data) or {}).get("abstract") if f["parser"] == "openalex" else \
                (from_arxiv_feed(data, vn.fm_get(fm, "arxiv") or "") or {}).get("abstract") if f["parser"] == "arxiv" \
                else (from_crossref(data) or {}).get("abstract")
            exp["Resumen"] = ab or ""
    got = section_body(text, "Resumen") or ""
    got_text = "\n".join(ln for ln in got.split("\n") if not ln.startswith(">")).strip()
    if "Resumen" in exp:
        if ws(got_text) != ws(exp["Resumen"]):
            problems.append("## Resumen no coincide con el abstract del registro guardado")
    elif not got_text.startswith(NO_ABSTRACT):
        problems.append("## Resumen tiene texto, pero ningún registro guardado traía abstract")
    for f in manifest["files"]:
        data = raws.get(f["file"])
        if data is None or f["role"] != "fulltext":
            continue
        if manifest.get("converter") != vf.TOOL_ID:
            problems.append(f"convertidor distinto ({manifest.get('converter')} → {vf.TOOL_ID}): "
                            "## Texto completo no se puede recomprobar; `rebuild` lo regenera")
            break
        if f["kind"] == "pdf-text":
            block = vf.build_from_text(data, f["url"], f.get("version", "?"), f["obtenido"])
        else:
            block = vf.build(f["kind"], f["url"], f.get("version", "?"), data, f["obtenido"])
        if ws(block or "") != ws(section_body(text, "Texto completo") or ""):
            problems.append("## Texto completo no coincide con el texto fuente guardado")
    if meta:
        want = referencia({**meta, "doi": retraction.normalize_doi(vn.fm_get(fm, "doi")) or "",
                           "arxiv": vn.fm_get(fm, "arxiv") or "", "version": vn.fm_get(fm, "arxiv_version") or "",
                           "venue": meta.get("venue")})
        if ws(want) != ws(section_body(text, "Referencia") or ""):
            problems.append("## Referencia no coincide con los metadatos guardados")
    return exp, problems


def verify_note(vault: Path, path: Path) -> dict:
    pid = path.name.split(" ")[0]
    if is_flagged(path):
        return {"id": pid, "status": "skipped_send_never"}
    text = path.read_text(encoding="utf-8")
    fm = (vn.split_frontmatter(text) or ([], ""))[0]
    if not vn.fm_get(fm, "fuentes"):
        return {"id": pid, "status": "legacy",
                "note": "ingerida antes de ingest_paper.py: sus secciones fuente no se pueden recomprobar; "
                        "`rebuild` las regenera desde la fuente"}
    if not (vault / vn.fm_get(fm, "fuentes")).is_file():
        return {"id": pid, "status": "raw_missing", "problems": ["falta fuentes.json"]}
    _, problems = expected_sections(vault, text)
    return {"id": pid, "status": "ok" if not problems else "integrity_problem", "problems": problems}


def integrity_problems(vault: str, path: str) -> list[str]:
    """For verifier_packet: [] when the note's source sections match their kept
    bytes (or it predates this script), else what differs."""
    r = verify_note(Path(vault), Path(path))
    return r.get("problems", []) if r["status"] in ("integrity_problem", "raw_missing") else []


def cmd_verify(a) -> dict:
    vault = a.vault.resolve()
    paths = [find_note(vault, i) for i in a.only] if a.only else paper_notes(vault)
    res = [verify_note(vault, p) for p in paths]
    counts: dict[str, int] = {}
    for r in res:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"tool": TOOL, "counts": counts, "notes": res}


def cmd_rebuild(a, fetch: Fetch = default_fetch, fetch_arxiv=vf.fetch_arxiv, today: str | None = None) -> dict:
    vault = a.vault.resolve()
    date = today or _dt.date.today().isoformat()
    out = []
    for pid in a.only:
        path = find_note(vault, pid)
        if is_flagged(path):
            out.append({"id": pid, "status": "skipped_send_never"})
            continue
        text = path.read_text(encoding="utf-8")
        fm = (vn.split_frontmatter(text) or ([], ""))[0]
        arxiv = retraction.normalize_arxiv(vn.fm_get(fm, "arxiv")) or ""
        doi = "" if arxiv else (retraction.normalize_doi(vn.fm_get(fm, "doi")) or "")
        if not arxiv and not doi:
            out.append({"id": pid, "status": "refused", "reason": "la nota no tiene arxiv ni doi"})
            continue
        store = Store(vault, pid, date)
        try:
            meta, warn = gather(store, arxiv, doi, fetch)
        except (Refused, net.HttpError) as e:
            out.append({"id": pid, "status": "refused", "reason": str(e)})
            continue
        version = vn.fm_get(fm, "arxiv_version") or ""
        block, kind = fulltext(store, arxiv, version, None, None, fetch_arxiv)
        for f in store.files:
            if f["file"] == meta.get("abstract_file"):
                f["abstract"] = True
        meta_r = {**meta, "doi": retraction.normalize_doi(vn.fm_get(fm, "doi")) or "", "arxiv": arxiv,
                  "version": version or meta.get("version", "")}
        new = replace_section(text, "Referencia", referencia(meta_r))
        new = replace_section(new, "Resumen", resumen(meta.get("abstract", ""), meta.get("abstract_url", ""),
                                                      date, [store.files[0]["url"]] if store.files else []))
        new = replace_section(new, "Texto completo", block.strip() if block else NO_FULLTEXT)
        new = vn.set_fields(new, {"fulltext": "full" if block else "abstract-only", "ingested_by": TOOL,
                                  "fuentes": f"{store.rel}/fuentes.json",
                                  **({"arxiv_version": meta_r["version"]} if arxiv else {})})
        if not a.dry_run:
            store.write()
            path.write_text(new, encoding="utf-8", newline="\n")
        out.append({"id": pid, "status": "rebuilt" if not a.dry_run else "dry_run",
                    "fulltext": kind or "abstract-only", "changed": new != text, "warnings": warn})
    return {"tool": TOOL, "notes": out}


def cmd_zotero_key(a) -> dict:
    vault = a.vault.resolve()
    path = find_note(vault, a.id)
    if is_flagged(path):
        raise Refused(f"{a.id} es send: never: no se modifica")
    text = path.read_text(encoding="utf-8")
    path.write_text(vn.set_fields(text, {"zotero_key": a.key}), encoding="utf-8", newline="\n")
    return {"id": a.id, "zotero_key": a.key}


def main(argv: list[str] | None = None, fetch: Fetch = default_fetch, fetch_arxiv=vf.fetch_arxiv,
         today: str | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    ad = sub.add_parser("add")
    ad.add_argument("--vault", type=Path, required=True)
    ad.add_argument("--project", required=True)
    ad.add_argument("--arxiv")
    ad.add_argument("--doi")
    ad.add_argument("--version", help="pin an arXiv version, e.g. v2 (default: the latest)")
    ad.add_argument("--pdf-text", type=Path, help="pdftotext output of an open-access PDF not on arXiv")
    ad.add_argument("--source-url", help="where --pdf-text came from")
    ad.add_argument("--facet")
    ad.add_argument("--matched")
    ad.add_argument("--source", default="arxiv", choices=("arxiv", "semantic-scholar", "openalex", "crossref", "dblp",
                                                         "patentsview", "manual"))
    ad.add_argument("--no-fulltext", action="store_true")
    ad.add_argument("--dry-run", action="store_true")
    rb = sub.add_parser("rebuild")
    rb.add_argument("--vault", type=Path, required=True)
    rb.add_argument("--only", nargs="+", required=True)
    rb.add_argument("--dry-run", action="store_true")
    ve = sub.add_parser("verify")
    ve.add_argument("--vault", type=Path, required=True)
    ve.add_argument("--only", nargs="*")
    zk = sub.add_parser("zotero-key")
    zk.add_argument("--vault", type=Path, required=True)
    zk.add_argument("--id", required=True)
    zk.add_argument("--key", required=True)
    a = p.parse_args(argv)
    try:
        if a.cmd == "add":
            if a.pdf_text and not a.source_url:
                raise Refused("--pdf-text needs --source-url")
            if a.facet and not a.matched:
                raise Refused("--facet needs --matched (the term that found the paper)")
            out = cmd_add(a, fetch, fetch_arxiv, today)
        elif a.cmd == "rebuild":
            out = cmd_rebuild(a, fetch, fetch_arxiv, today)
        elif a.cmd == "verify":
            out = cmd_verify(a)
        else:
            out = cmd_zotero_key(a)
    except Refused as e:
        print(json.dumps({"tool": TOOL, "refused": str(e)}, ensure_ascii=False))
        return 2
    except (net.HttpError, OSError, ValueError) as e:
        print(json.dumps({"tool": TOOL, "error": net.redact(str(e))}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if a.cmd == "verify" and out["counts"].get("integrity_problem", 0) + out["counts"].get("raw_missing", 0):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
