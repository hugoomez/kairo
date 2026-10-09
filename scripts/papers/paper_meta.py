#!/usr/bin/env python3
"""A paper note's metadata — never its text — for the main session.

    paper_meta.py --vault <vault> P-0001 [P-0002 …] [--fields resolution_status code_repo …] [--referencia]

The main session may not read a paper note (the vault hook refuses it: the
abstract and full text are third-party text that can carry instructions aimed
at a model, and the main session can run commands). What it legitimately needs
— `resolution_status` before citing, `authors` / `year` for an in-text
citation, `code_repo`, the `## Referencia` line for a references list — comes
from here: the frontmatter fields (flow lists as lists) and, with
`--referencia`, the reference line written by ingest_paper.py. `## Resumen`
and `## Texto completo` are never printed; to look at a paper's text, use the
`paper-reader` subagent.

A `send: never` note gives `{"send_never": true}` and nothing else. Prints one
JSON object. Exit: 0 ok · 2 bad input. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "citations"))
import vaultnotes as vn  # noqa: E402

PID = re.compile(r"^P-\d{4,5}$")


def find(vault: Path, pid: str) -> Path | None:
    hits = sorted((vault / "Papers").glob(f"{pid} *.md")) + sorted((vault / "Papers").glob(f"{pid}.md"))
    return hits[0] if hits else None


def meta(path: Path, fields: list[str] | None, referencia: bool) -> dict:
    text, _ = vn.read_text(path)
    parts = vn.split_frontmatter(text)
    if not parts:
        return {}
    fm, body = parts
    if vn.is_send_never(fm):
        return {"send_never": True}
    out: dict = {}
    for line in fm:
        m = re.match(r"^([A-Za-z_][\w-]*):", line)
        if not m or (fields and m.group(1) not in fields):
            continue
        raw = vn.fm_raw(fm, m.group(1)) or ""
        out[m.group(1)] = vn.parse_flow_list(raw) if raw.strip().startswith("[") else (vn.fm_get(fm, m.group(1)) or "")
    if referencia:
        m = re.search(r"^## Referencia\s*\n(.*?)(?=^## |\Z)", body, re.S | re.M)
        out["referencia"] = " ".join(m.group(1).split()) if m else None
    return out


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vault", required=True, type=Path)
    ap.add_argument("ids", nargs="+")
    ap.add_argument("--fields", nargs="+")
    ap.add_argument("--referencia", action="store_true", help="also the ## Referencia line")
    a = ap.parse_args(argv)
    bad = [i for i in a.ids if not PID.match(i)]
    if bad:
        print(json.dumps({"error": f"not paper ids: {bad}"}))
        return 2
    papers, missing = {}, []
    for pid in a.ids:
        p = find(a.vault, pid)
        if p is None:
            missing.append(pid)
        else:
            papers[pid] = meta(p, a.fields, a.referencia)
    print(json.dumps({"papers": papers, "missing": missing}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
