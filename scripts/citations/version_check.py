#!/usr/bin/env python3
"""Newer arXiv versions of ingested papers, and who cites them.

    version_check.py --vault <vault> [--only P-0001 …] [--write] [--json]

A paper note is anchored on the arXiv version it was ingested from
(`arxiv_version`): its `## Texto completo` and every locator that cites it
point into that version. Authors keep revising: a v3 may renumber sections,
change a table or fix the result a hypothesis leans on. This script asks arXiv
(batched, 3 s spacing through net.py) for the latest version of every ingested
arXiv paper and reports, per paper whose latest version is newer than the one
ingested:

  - the version stored and the latest one, with its date;
  - a published version arXiv now declares (DOI / journal_ref) that the note
    does not record yet (`resolve_refs.py --write` records it);
  - every note that cites the paper: hypotheses and ADRs (as retraction_sweep
    finds them) and each project's Estado-del-arte.md.

It never re-fetches or changes a paper's text — moving a note to a new version
moves every locator into it, which is the researcher's decision (re-ingest
with `ingest_paper.py rebuild`, then re-check what cites it). Default is
report-only; `--write` records `arxiv_latest_version` and
`arxiv_version_checked` in each checked note's frontmatter, nothing else.

`send: never` notes are skipped and their ids never sent. A batch arXiv did
not answer is LOST: its papers are listed under `lost`, never as unchanged.

Exit: 0 nothing newer · 3 newer versions found · 1 some lookups lost · 2 bad input.
Standard library only.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import retraction  # noqa: E402
import retraction_sweep as sw  # noqa: E402
import vaultnotes as vn  # noqa: E402

TOOL = "kairo/version_check@1.0.0"


def _vnum(v: str | None) -> int | None:
    m = re.fullmatch(r"v?(\d+)", (v or "").strip())
    return int(m.group(1)) if m else None


def cited_by(vault: Path, pid: str) -> list[str]:
    out = [nid for nid, _kind, _via, _sn, _p in sw.citers_of(vault, pid)]
    for sota in sorted(vault.glob("Projects/*/Estado-del-arte.md")):
        if re.search(rf"\b{re.escape(pid)}\b", sota.read_text(encoding="utf-8", errors="replace")):
            out.append(f"Estado-del-arte ({sota.parent.name})")
    return out


def check(vault: Path, only: list[str] | None, write: bool, today: str) -> dict:
    notes, skipped = [], []
    for path in sorted((vault / "Papers").glob("P-*.md")):
        text = path.read_text(encoding="utf-8")
        split = vn.split_frontmatter(text)
        if not split:
            continue
        fm = split[0]
        pid = vn.note_id(path, fm)
        if only and pid not in only:
            continue
        if vn.is_send_never(fm) or vn.text_is_send_never(text):
            skipped.append(pid)
            continue
        aid = retraction.normalize_arxiv(vn.fm_get(fm, "arxiv"))
        if aid:
            notes.append((pid, path, fm, aid))
    entries, lost_ids = retraction.fetch_arxiv([aid for _, _, _, aid in notes])
    changed, lost, unchanged, not_found = [], [], 0, []
    for pid, path, fm, aid in notes:
        if aid in lost_ids:
            lost.append(pid)
            continue
        e = entries.get(aid)
        if e is None:
            not_found.append(pid)
            continue
        stored, latest = _vnum(vn.fm_get(fm, "arxiv_version")), e.get("version")
        declared = {k: v for k, v in (("doi", e.get("doi") or ""), ("journal_ref", e.get("journal_ref") or ""))
                    if v and not retraction.is_arxiv_doi(v)}
        known = {(vn.fm_get(fm, k) or "").strip().lower() for k in ("doi", "published_doi", "journal_ref")}
        new_pub = {k: v for k, v in declared.items() if v.lower() not in known}
        if latest and (stored is None or latest > stored):
            changed.append({"id": pid, "arxiv": aid, "stored": f"v{stored}" if stored else None,
                            "latest": f"v{latest}", "latest_date": (e.get("updated") or "")[:10] or None,
                            "declared_published": new_pub or None, "cited_by": cited_by(vault, pid)})
        else:
            unchanged += 1
        if write and latest:
            text = path.read_text(encoding="utf-8")
            new = vn.set_fields(text, {"arxiv_latest_version": f"v{latest}", "arxiv_version_checked": today})
            if new != text:
                path.write_text(new, encoding="utf-8", newline="\n")
    return {"tool": TOOL, "checked": len(notes) - len(lost), "changed": changed, "unchanged": unchanged,
            "lost": lost, "not_found": not_found, "skipped_send_never": skipped, "written": write}


def markdown(out: dict) -> str:
    L = [f"Comprobadas {out['checked']} · sin cambios {out['unchanged']} · con versión nueva {len(out['changed'])}"]
    if out["lost"]:
        L.append(f"⚠️ arXiv no respondió para {', '.join(out['lost'])}: vuelve a ejecutarlo (no se dan por "
                 "comprobadas).")
    for c in out["changed"]:
        line = f"- {c['id']} (arXiv:{c['arxiv']}): ingerida {c['stored'] or '¿?'} → última {c['latest']}"
        line += f" ({c['latest_date']})" if c["latest_date"] else ""
        if c["declared_published"]:
            line += " · arXiv declara versión publicada: " + ", ".join(f"{k} {v}" for k, v in
                                                                       c["declared_published"].items())
        if c["cited_by"]:
            line += f"\n  Citada por: {', '.join(c['cited_by'])} — sus localizadores apuntan a {c['stored']}."
        L.append(line)
    return "\n".join(L)


def main(argv: list[str] | None = None, today: str | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vault", type=Path, required=True)
    ap.add_argument("--only", nargs="+")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not (a.vault / "Papers").is_dir():
        print(json.dumps({"tool": TOOL, "refused": f"{a.vault} has no Papers/"}))
        return 2
    out = check(a.vault, a.only, a.write, today or _dt.date.today().isoformat())
    print(json.dumps(out, ensure_ascii=False, indent=2) if a.json else markdown(out))
    return 1 if out["lost"] else 3 if out["changed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
