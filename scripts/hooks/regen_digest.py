#!/usr/bin/env python3
"""Regenerate a project's _digest.md from its Hipotesis/ notes.

Invoked by the PostToolUse hook on Write|Edit of Projects/**/Hipotesis/**
(routed by scripts/hooks/kairo_hook.py, declared in the plugin's hooks/hooks.json). Reads the hook's stdin JSON, finds the enclosing
Projects/<slug>/ directory of the changed note, and rebuilds _digest.md with one
row per hypothesis:

    | id | claim | estado | lección |

Fires on any create/edit of a hypothesis note, not only status changes -- the
rebuild is idempotent and cheap, which avoids fragile diff parsing. Notes are the
source of truth; the digest is a derived view. Standard library only. Always
exits 0 -- a digest failure must never break the session.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_FRONT = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def frontmatter(text: str) -> dict:
    m = _FRONT.match(text)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).splitlines():
        mm = re.match(r"([A-Za-z_][\w-]*):\s*(.*)", line)
        if mm:
            fm[mm.group(1)] = mm.group(2).strip().strip('"').strip("'")
    return fm


def section(text: str, heading: str) -> str:
    pat = re.compile(r"^##\s+" + re.escape(heading) + r"\s*$(.*?)(?=^##\s|\Z)",
                     re.DOTALL | re.MULTILINE)
    m = pat.search(text)
    return m.group(1).strip() if m else ""


def first_line(s: str) -> str:
    for ln in s.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("<"):
            return ln
    return ""


def cell(s: str) -> str:
    return s.replace("|", r"\|").replace("\n", " ").strip()


def project_dir(p: Path) -> Path | None:
    for parent in p.parents:
        if parent.name == "Hipotesis" and parent.parent.parent.name == "Projects":
            return parent.parent
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    fp = (payload.get("tool_input") or {}).get("file_path")
    if not fp:
        return 0

    proj = project_dir(Path(fp))
    if proj is None or not (proj / "Hipotesis").is_dir():
        return 0

    rows = []
    for note in sorted((proj / "Hipotesis").glob("*.md")):
        try:
            text = note.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = frontmatter(text)
        hid = fm.get("id") or note.stem
        estado = fm.get("status", "")
        claim = first_line(section(text, "Claim"))
        leccion = section(text, "Lección")
        if leccion.startswith("<") or leccion.lower().startswith("se completa"):
            leccion = ""
        rows.append((hid, claim, estado, leccion))

    rows.sort(key=lambda r: r[0])
    out = ["| id | claim | estado | lección |", "|----|-------|--------|---------|"]
    out += [f"| {cell(a)} | {cell(b)} | {cell(c)} | {cell(d)} |" for a, b, c, d in rows]
    try:
        (proj / "_digest.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"regen_digest: could not write {proj.name}/_digest.md: {exc}",
              file=sys.stderr)
        return 0
    print(f"regen_digest: rebuilt {proj.name}/_digest.md ({len(rows)} hypotheses)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
