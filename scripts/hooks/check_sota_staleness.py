#!/usr/bin/env python3
"""Flag a project's Estado-del-arte.md when >= 5 of its papers are newer than it.

Invoked by the PostToolUse hook on Write|Edit of Papers/** (routed by
scripts/hooks/kairo_hook.py). Reads the hook's stdin JSON, takes the changed Papers
note's `projects:` list, and for each of those projects counts how many of the
project's Papers notes were `added:` after Estado-del-arte.md was `generated:`.
If any project is at or past the threshold, prints a JSON object whose
`additionalContext` tells the model to refresh that SOTA map (create-project
skill, step 7).

`generated:` is read from Estado-del-arte.md frontmatter; if absent, falls back
to the file's last git commit time, then its mtime.

Standard library only. Always exits 0.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

THRESHOLD = 5
_FRONT = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def frontmatter(text: str) -> dict:
    m = _FRONT.match(text)
    if not m:
        return {}
    fm: dict = {}
    key = None
    for line in m.group(1).splitlines():
        li = re.match(r"\s*-\s+(.*)", line)
        if key == "projects" and li:
            fm.setdefault("projects", []).append(li.group(1).strip().strip("[]").strip('"\''))
            continue
        mm = re.match(r"([A-Za-z_][\w-]*):\s*(.*)", line)
        if not mm:
            continue
        key, val = mm.group(1), mm.group(2).strip()
        if key == "projects":
            inline = val.strip("[] ")
            fm["projects"] = [p.strip().strip('"\'') for p in inline.split(",") if p.strip()] if inline else []
        else:
            fm[key] = val.strip('"').strip("'")
    return fm


def parse_dt(s: str):
    s = (s or "").strip().strip('"').strip("'")
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def sota_generated(proj_dir: Path):
    sota = proj_dir / "Estado-del-arte.md"
    if not sota.is_file():
        return None, None
    dt = parse_dt(frontmatter(sota.read_text(encoding="utf-8")).get("generated", ""))
    if dt is None:
        try:
            out = subprocess.run(
                ["git", "-C", str(proj_dir), "log", "-1", "--format=%cI",
                 "--", "Estado-del-arte.md"],
                capture_output=True, text=True, timeout=10)
            dt = parse_dt(out.stdout.strip())
        except Exception:
            dt = None
    if dt is None:
        dt = datetime.fromtimestamp(sota.stat().st_mtime, tz=timezone.utc)
    return sota, dt


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    fp = (payload.get("tool_input") or {}).get("file_path")
    if not fp:
        return 0

    changed = Path(fp)
    parts = changed.as_posix().split("/")
    if "Papers" not in parts:
        return 0
    vault = Path("/".join(parts[: parts.index("Papers")]))
    if not (vault / "Projects").is_dir():
        vault = Path(payload.get("cwd") or ".")
    if not (vault / "Projects").is_dir():
        return 0

    try:
        target_projects = frontmatter(changed.read_text(encoding="utf-8")).get("projects") or []
    except OSError:
        return 0
    if not target_projects:
        return 0

    papers = list((vault / "Papers").glob("*.md"))
    stale = []
    for proj_dir in sorted(d for d in (vault / "Projects").iterdir() if d.is_dir()):
        hub = proj_dir / "_hub.md"
        if not hub.is_file():
            continue
        proj_id = frontmatter(hub.read_text(encoding="utf-8")).get("id")
        if not proj_id or proj_id not in target_projects:
            continue
        sota, gen_dt = sota_generated(proj_dir)
        if sota is None or gen_dt is None:
            continue
        n_new = 0
        for p in papers:
            try:
                pfm = frontmatter(p.read_text(encoding="utf-8"))
            except OSError:
                continue
            if proj_id not in (pfm.get("projects") or []):
                continue
            added = parse_dt(pfm.get("added", ""))
            if added is not None and added > gen_dt:
                n_new += 1
        if n_new >= THRESHOLD:
            stale.append((proj_id, proj_dir.name, n_new, gen_dt.date().isoformat()))

    if not stale:
        return 0

    detail = "; ".join(
        f"{pid} ({slug}): {n} new papers since Estado-del-arte.md generated {d}"
        for pid, slug, n, d in stale)
    msg = "Estado-del-arte.md is stale -- " + detail
    ctx = (msg + ". Refresh each listed project's Estado-del-arte.md following the "
           "create-project skill step 7 (map-reduce: one sub-pass per facet, then a "
           "combining pass; keep the canonical section order and per-claim citations), "
           "then set its `generated:` frontmatter to the current UTC timestamp.")
    print(json.dumps({
        "systemMessage": msg,
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": ctx,
        },
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
