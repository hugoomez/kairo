#!/usr/bin/env python3
"""Versioned technical documentation in a vault's Papers/: tool docs, file-format
specs, changelogs — pages that change while a study is running.

    docs_corpus.py add     --vault <vault> --project PROJ-XXX --url URL --tool <name>
                           [--facet A --matched "<term>"] [--dry-run]
    docs_corpus.py refresh --vault <vault> --project PROJ-XXX [--only P-XXXX …] [--dry-run]
    docs_corpus.py list    --vault <vault> [--project PROJ-XXX]

`add` is `ingest_paper.py add --url URL --doc --tool <name>`: the page is
ingested like any grey-literature page (its bytes kept as pagina.html, its
visible text as `## Texto completo`, `verify` re-derives the three source
sections), with `source_type: documentacion` and:

  tool         the tool, format or standard the page documents (the researcher's
               word, e.g. `--tool widgetctl`)
  doc_version  the documented version, read from the page when it says one —
               a version meta tag (docsearch:version, version, …), a labelled
               version string in its text («Version 2.4.1», «v2.4.1»), else a
               «last updated» date (meta article:modified_time / dateModified,
               or «Last updated: …» in the text) as `actualizado YYYY-MM-DD`;
               empty when the page says none. Never typed by a model.
  accessed_at  the date this version of the page was fetched
  url          the page
  sha256       sha256 of the page's visible text (what `## Texto completo` keeps,
               before its `> Fuente:` line): a rotating nonce or advert in the
               HTML is not a new version; a changed sentence is.

and a `## Versiones` section, one line per version. A page already in the vault
is recognised by its URL (two docs pages titled «Configuration» are different
pages), and gets the project appended.

`refresh` fetches every documentation note of the project again (with
`--only`, those notes). When the visible text's sha256 is the same, it only
records `checked_at`. When it changed, nothing is overwritten silently:

  - the previous version — the note as it was and every kept byte of it — is
    moved to `Papers/_fuentes/<P-id>/historial/v<n>/` and listed in
    `historial/historial.json` (version, accessed_at, doc_version, sha256);
  - the note's three source sections are rewritten from the new bytes, its
    `doc_version`, `accessed_at`, `fetched_at` and `sha256` updated, and one
    line added to `## Versiones` saying what changed (lines added / removed in
    the visible text) and where the previous version is kept;
  - a page that can no longer be fetched, or is refused (it now names a DOI,
    a private URL…), is reported and left as it was.

The output names each note by id and title only, with counts of lines added
and removed — never the page text. `ingest_paper.py rebuild` refuses a
documentation note (it would overwrite it): use `refresh`.

These notes are papers for every other script: ask-corpus (`check_quotes.py
--list` shows `source_type`, `tool`, `doc_version`, `accessed_at`) and explain
cite them by P-id and locator like any paper; a quote of a version that has
since changed stops verifying, as it should.

Prints one JSON object. Exit codes: 0 ok · 1 error · 2 refused.
Standard library only.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import hashlib
import html
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ingest_paper as ip  # noqa: E402
import vaultnotes as vn  # noqa: E402  (scripts/citations, path set by ingest_paper)
import web_meta  # noqa: E402  (scripts/search, path set by ingest_paper)
from send_guard import is_flagged  # noqa: E402

TOOL = "kairo/docs_corpus@1.0.0"
SOURCE_TYPE = "documentacion"
HISTORY = "historial"
VERSIONS = "Versiones"

_VERSION_META = ("docsearch:version", "docsearch:docusaurus_tag", "version", "doc-version", "docs-version",
                 "software-version", "product-version")
_MODIFIED_META = ("article:modified_time", "og:updated_time", "datemodified", "last-modified", "dc.modified",
                  "dcterms.modified")
_NUM = r"\d+(?:\.\d+)+(?:[-.]?(?:rc|alpha|beta|a|b|dev|post)\d*)?"
_LABELLED = re.compile(rf"(?i)\b(?:version|versión|release|versio)\s*:?\s*v?({_NUM})\b")
_BARE_V = re.compile(rf"(?<![\w.])v({_NUM})\b")
_MONTHS = ("january february march april may june july august september october november december "
           "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre").split()
_DATE = (r"(\d{4}-\d{2}-\d{2}|[A-Za-z]+\.? \d{1,2},? \d{4}|\d{1,2} (?:de )?[A-Za-z]+\.? (?:de )?\d{4})")
_UPDATED = re.compile(rf"(?i)\b(?:last updated|last modified|updated on|updated|última actualización|"
                      rf"actualizado(?: el)?)\s*(?:on\s*)?:?\s*{_DATE}")


def ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def content_sha(text: str) -> str:
    """sha256 of a page's visible text, whitespace collapsed within each line."""
    norm = "\n".join(ws(ln) for ln in (text or "").split("\n") if ws(ln))
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _iso(raw: str) -> str | None:
    """A date as YYYY-MM-DD: ISO, «March 3, 2031», «3 March 2031», «3 de marzo de 2031»."""
    d, _ = web_meta.norm_date(raw)
    if d and re.match(r"\d{4}-", raw.strip()):
        return d
    m = re.fullmatch(r"([A-Za-z]+)\.? (\d{1,2}),? (\d{4})", raw.strip()) or None
    if m:
        mon, day, year = m.group(1), m.group(2), m.group(3)
    else:
        m = re.fullmatch(r"(\d{1,2}) (?:de )?([A-Za-z]+)\.? (?:de )?(\d{4})", raw.strip())
        if not m:
            return None
        day, mon, year = m.group(1), m.group(2), m.group(3)
    names = [x for x in _MONTHS if x.startswith(mon.lower()[:3])]
    if not names or not 1 <= int(day) <= 31:
        return None
    month = _MONTHS.index(names[0]) % 12 + 1
    return f"{int(year):04d}-{month:02d}-{int(day):02d}"


def _meta_tags(raw: bytes) -> dict[str, list[str]]:
    head = raw[:4096].lower()
    m = re.search(rb"charset=[\"']?([a-z0-9_\-]+)", head)
    try:
        text = raw.decode(m.group(1).decode() if m else "utf-8", errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")
    page = web_meta._Page()
    try:
        page.feed(text)
        page.close()
    except Exception:  # noqa: BLE001  a broken page still gives what was read
        pass
    meta = dict(page.meta)
    # JSON-LD dateModified («"dateModified": "2031-02-03"»)
    for v in re.findall(r'"dateModified"\s*:\s*"([^"]+)"', text):
        meta.setdefault("datemodified", []).append(v)
    return meta


def detect_version(raw: bytes, text: str) -> tuple[str, str | None]:
    """(doc_version, where it came from) read from the page itself, or ("", None)."""
    meta = _meta_tags(raw)
    for name in _VERSION_META:
        for v in meta.get(name, []):
            v = ws(html.unescape(v))
            if v and len(v) <= 40:
                return v, f"meta {name}"
    head = (text or "")[:20000]
    for rx, origin in ((_LABELLED, "texto (versión)"), (_BARE_V, "texto (vN)")):
        m = rx.search(head)
        if m:
            return m.group(1), origin
    for name in _MODIFIED_META:
        for v in meta.get(name, []):
            d, _ = web_meta.norm_date(v)
            if d:
                return f"actualizado {d}", f"meta {name}"
    m = _UPDATED.search(head)
    if m:
        d = _iso(m.group(1))
        if d:
            return f"actualizado {d}", "texto (última actualización)"
    return "", None


def quote_version(text: str) -> str:
    """`doc_version` always quoted in the frontmatter: unquoted, YAML would read
    `3.10` as the number 3.1."""
    head, sep, rest = text.partition("\n---\n")
    head = re.sub(r"(?m)^doc_version: ([^\"\n][^\n]*)$", lambda m: f'doc_version: "{m.group(1).strip()}"',
                  head, count=1)
    return head + sep + rest


def versions_line(n: int, date: str, doc_version: str, sha: str, change: str = "") -> str:
    return (f"- v{n} · consultada {date} · versión del documento: {doc_version or 'no consta'} · "
            f"sha256 {sha[:12]}" + (f" · {change}" if change else ""))


def doc_fields(a, store, meta: dict, page_text: str) -> tuple[list[tuple[str, object]], str, dict]:
    """The frontmatter fields a documentation note adds, its `## Versiones` section,
    and what `add` reports about the version."""
    dv, origin = detect_version(store.blobs[ip.WEB_PAGE], page_text)
    sha = content_sha(page_text)
    fields = [("tool", a.tool.strip()), ("doc_version", dv), ("accessed_at", store.date), ("sha256", sha)]
    section = f"## {VERSIONS}\n\n{versions_line(1, store.date, dv, sha)}\n"
    return fields, section, {"tool": a.tool.strip(), "doc_version": dv, "doc_version_from": origin,
                             "sha256": sha}


def is_doc(fm: list[str]) -> bool:
    return (vn.fm_get(fm, "source_type") or "") == SOURCE_TYPE


def find_doc(vault: Path, url: str) -> Path | None:
    """The documentation note of this URL (redacted as notes keep it), if any."""
    want = ip.net.redact(url).rstrip("/")
    for p in ip.paper_notes(vault):
        if is_flagged(p):
            continue
        fm = (vn.split_frontmatter(p.read_text(encoding="utf-8")) or ([], ""))[0]
        if is_doc(fm) and (vn.fm_get(fm, "url") or "").rstrip("/") == want:
            return p
    return None


def projects_of(fm: list[str]) -> list[str]:
    return re.findall(r"PROJ-[\w-]+", vn.fm_raw(fm, "projects") or "")


def doc_notes(vault: Path, project: str | None, only: list[str] | None = None) -> list[tuple[Path, list[str]]]:
    out = []
    for p in ip.paper_notes(vault):
        pid = p.name.split(" ")[0]
        if only and pid not in only:
            continue
        if is_flagged(p):
            continue
        fm = (vn.split_frontmatter(p.read_text(encoding="utf-8")) or ([], ""))[0]
        if not is_doc(fm) or (project and project not in projects_of(fm)):
            continue
        out.append((p, fm))
    return out


def load_history(d: Path) -> list[dict]:
    f = d / HISTORY / "historial.json"
    if not f.is_file():
        return []
    data = json.loads(f.read_text(encoding="utf-8"))
    return data.get("versiones", []) if isinstance(data, dict) else []


def _lines(block: str | None) -> list[str]:
    return [ws(ln) for ln in ip._unquoted(block or "").split("\n") if ws(ln) and not ln.startswith("#")]


def diff_counts(old: str | None, new: str | None) -> tuple[int, int]:
    added = removed = 0
    for ln in difflib.ndiff(_lines(old), _lines(new)):
        if ln.startswith("+ "):
            added += 1
        elif ln.startswith("- "):
            removed += 1
    return added, removed


def refresh_note(vault: Path, path: Path, fm: list[str], fetch, date: str, dry_run: bool) -> dict:
    pid = path.name.split(" ")[0]
    text = path.read_text(encoding="utf-8")
    title = vn.fm_get(fm, "title") or ""
    url = vn.fm_get(fm, "url") or ""
    base = {"id": pid, "title": title}
    if not url:
        return {**base, "status": "refused", "reason": "la nota no tiene url"}
    store = ip.Store(vault, pid, date)
    try:
        meta, page_text = ip.gather_web(store, url, fetch)
    except (ip.Refused, ip.net.HttpError) as e:
        return {**base, "status": "lost", "reason": ip.net.redact(str(e))[:300]}
    sha = content_sha(page_text)
    if sha == (vn.fm_get(fm, "sha256") or ""):
        if not dry_run:
            ip.atomic_write(path, vn.set_fields(text, {"checked_at": date}))
        return {**base, "status": "unchanged", "checked_at": date}
    block, kind, warn = ip.web_fulltext(store, meta, page_text, fetch)
    added, removed = diff_counts(ip.section_body(text, "Texto completo"), block)
    src = vault / "Papers" / ip.FUENTES_DIR / pid
    history = load_history(src)
    n = len(history) + 1                            # the version being replaced
    dv, origin = detect_version(store.blobs[ip.WEB_PAGE], page_text)
    hist_rel = f"Papers/{ip.FUENTES_DIR}/{pid}/{HISTORY}/v{n}/"
    change = (f"cambió respecto a v{n}: +{added} −{removed} líneas de texto visible; "
              f"la v{n} se guarda en {hist_rel}")
    new = ip.replace_section(text, "Referencia", ip.referencia(meta))
    new = ip.replace_section(new, "Resumen", ip.web_resumen(meta["abstract"], meta["web_url"], date,
                                                            meta["abstract_from"]))
    new = ip.replace_section(new, "Texto completo", block.strip() if block else ip.NO_FULLTEXT)
    new = ip.set_suspicious(new, block)
    new = vn.set_fields(new, {"fulltext": ip.web_fulltext_field(kind), "ingested_by": ip.TOOL, "fetched_at": date,
                              "accessed_at": date, "checked_at": date, "doc_version": dv, "sha256": sha,
                              "pdf": ip.net.redact(meta["pdf_url"]) if kind == "pdf" else "",
                              "fuentes": f"{store.rel}/fuentes.json"})
    new = quote_version(new)
    prev = ip.section_body(new, VERSIONS)
    line = versions_line(n + 1, date, dv, sha, change)
    if prev is None:
        # a note added before `## Versiones` existed: its first version is named too
        first = versions_line(n, vn.fm_get(fm, "accessed_at") or vn.fm_get(fm, "fetched_at") or "s. f.",
                              vn.fm_get(fm, "doc_version") or "", vn.fm_get(fm, "sha256") or "0" * 12)
        new = ip.replace_section(new, VERSIONS, f"{first}\n{line}")
    else:
        new = ip.replace_section(new, VERSIONS, f"{prev}\n{line}")
    out = {**base, "status": "new_version" if not dry_run else "dry_run", "version": n + 1,
           "doc_version_before": vn.fm_get(fm, "doc_version") or "", "doc_version": dv,
           "doc_version_from": origin, "lines_added": added, "lines_removed": removed,
           "previous_kept_in": hist_rel, "warnings": warn, "path": path.relative_to(vault).as_posix()}
    if dry_run:
        return out
    # the previous version first: every kept byte and the note as it was
    keep = src / HISTORY / f"v{n}"
    keep.mkdir(parents=True, exist_ok=True)
    for item in sorted(src.iterdir()) if src.is_dir() else []:
        if item.name != HISTORY:
            shutil.move(str(item), str(keep / item.name))
    ip.atomic_write(keep / "nota.md", text)
    history.append({"version": n, "accessed_at": vn.fm_get(fm, "accessed_at") or vn.fm_get(fm, "fetched_at") or "",
                    "doc_version": vn.fm_get(fm, "doc_version") or "", "sha256": vn.fm_get(fm, "sha256") or "",
                    "dir": f"v{n}", "replaced_at": date, "lines_added": added, "lines_removed": removed})
    ip.write_manifest(src / HISTORY / "historial.json", {"tool": TOOL, "versiones": history})
    store.write()
    ip.atomic_write(path, new)
    return out


def cmd_refresh(a, fetch, today: str | None = None) -> dict:
    vault = a.vault.resolve()
    if not re.fullmatch(r"PROJ-\d{3,}", a.project or ""):
        raise ip.Refused("--project must be a PROJ-XXX id")
    date = today or _dt.date.today().isoformat()
    with ip.vault_lock(vault):
        notes = doc_notes(vault, a.project, a.only)
        res = [refresh_note(vault, p, fm, fetch, date, a.dry_run) for p, fm in notes]
    counts: dict[str, int] = {}
    for r in res:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    out = {"tool": TOOL, "project": a.project, "date": date, "counts": counts, "notes": res}
    changed = [vault / r["path"] for r in res if r["status"] == "new_version"]
    if changed:
        out.update(ip.derived_views(vault, changed))
    return out


def cmd_list(a) -> dict:
    vault = a.vault.resolve()
    out = []
    for p, fm in doc_notes(vault, a.project):
        pid = p.name.split(" ")[0]
        out.append({"id": pid, "title": vn.fm_get(fm, "title") or "", "tool": vn.fm_get(fm, "tool") or "",
                    "doc_version": vn.fm_get(fm, "doc_version") or "",
                    "accessed_at": vn.fm_get(fm, "accessed_at") or "", "checked_at": vn.fm_get(fm, "checked_at") or "",
                    "url": vn.fm_get(fm, "url") or "",
                    "versions": len(load_history(vault / "Papers" / ip.FUENTES_DIR / pid)) + 1,
                    "path": p.relative_to(vault).as_posix()})
    return {"tool": TOOL, "documents": out}


def main(argv: list[str] | None = None, fetch=ip.default_fetch, today: str | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    ad = sub.add_parser("add", help="ingest a documentation page (= ingest_paper.py add --url … --doc)")
    ad.add_argument("--vault", type=Path, required=True)
    ad.add_argument("--project", required=True)
    ad.add_argument("--url", required=True)
    ad.add_argument("--tool", required=True, help="the tool, format or standard the page documents")
    ad.add_argument("--facet")
    ad.add_argument("--matched")
    ad.add_argument("--dry-run", action="store_true")
    rf = sub.add_parser("refresh", help="fetch the project's documentation again; keep every changed version")
    rf.add_argument("--vault", type=Path, required=True)
    rf.add_argument("--project", required=True)
    rf.add_argument("--only", nargs="*")
    rf.add_argument("--dry-run", action="store_true")
    ls = sub.add_parser("list", help="the documentation notes, with tool, version and dates")
    ls.add_argument("--vault", type=Path, required=True)
    ls.add_argument("--project")
    a = p.parse_args(argv)
    if a.cmd == "add":
        args = ["add", "--vault", str(a.vault), "--project", a.project, "--url", a.url, "--doc", "--tool", a.tool]
        args += (["--facet", a.facet] if a.facet else []) + (["--matched", a.matched] if a.matched else [])
        args += ["--dry-run"] if a.dry_run else []
        return ip.main(args, fetch=fetch, today=today)
    try:
        out = cmd_refresh(a, fetch, today) if a.cmd == "refresh" else cmd_list(a)
    except ip.Refused as e:
        print(json.dumps({"tool": TOOL, "refused": str(e)}, ensure_ascii=False))
        return 2
    except (ip.net.HttpError, OSError, ValueError) as e:
        print(json.dumps({"tool": TOOL, "error": ip.net.redact(str(e))}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
