#!/usr/bin/env python3
"""The code ↔ science trace of an applied project.

    trace_code.py --repo R --project-dir D [--vault V] [--write]

Reads the code repository's history for `Motivated-By:` trailers (one or more
`H-XXXX`, `E-XXXX`, `C-XXXX`, `ADR-XXX` per commit) and builds, per scientific
note, the commits and files that serve it — plus the commits that name no
motivation, so untraced work is visible rather than hidden. With `--write` it
writes `Projects/<slug>/_codigo-ciencia.md` (a derived view; commit subjects
and file paths only, never code).

Exit codes: 0 ok · 1 error. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

REF = re.compile(r"\b(?:H|E|C)-\d{4}\b|\bADR-\d{3,4}\b")
SEP = "\x1e"


def history(repo: Path, limit: int) -> list[dict]:
    """One record per commit: git parses the trailers itself
    (`%(trailers:key=Motivated-By,valueonly,separator=...)`), so the header is
    a single line; the changed files follow it."""
    fmt = f"{SEP}%H%x1f%ad%x1f%s%x1f%(trailers:key=Motivated-By,valueonly,separator=%x2c)"
    out = subprocess.run(["git", "log", f"-{limit}", f"--format={fmt}", "--date=short", "--name-only"],
                         cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout
    commits = []
    for chunk in out.split(SEP)[1:]:
        header, _, rest = chunk.partition("\n")
        sha, day, subject, trailers = (header.split("\x1f") + ["", "", "", ""])[:4]
        commits.append({"sha": sha[:10], "date": day, "subject": subject.strip(),
                        "refs": list(dict.fromkeys(REF.findall(trailers))),
                        "files": [f.strip() for f in rest.splitlines() if f.strip()]})
    return commits


def hub_archived(project_dir: Path) -> list[Path]:
    """`code_repo_archivo: ["C:/old/path", ...]` (a JSON array on one line) from the hub."""
    hub = project_dir / "_hub.md"
    if not hub.is_file():
        return []
    m = re.search(r"^code_repo_archivo:\s*(\[.*?\])\s*(?:#.*)?$", hub.read_text(encoding="utf-8"), re.M)
    if not m:
        return []
    try:
        return [Path(str(p)) for p in json.loads(m.group(1))]
    except ValueError:
        return []


def build(repo: Path, limit: int = 500, archived: list[Path] | None = None) -> dict:
    """`commits`, `traced` and `untraced` describe the current repository only;
    archived repositories add their traced commits to `by_ref` (each tagged with
    its `repo`), and an unreachable one is listed in `archived_missing`."""
    commits = history(repo, limit)
    old: list[dict] = []
    missing: list[str] = []
    for path in archived or []:
        try:
            old += [{**c, "repo": str(path)} for c in history(path, limit)]
        except (OSError, subprocess.CalledProcessError):
            missing.append(str(path))
    by_ref: dict[str, dict] = {}
    for c in commits + old:
        for r in c["refs"]:
            e = by_ref.setdefault(r, {"commits": [], "files": set()})
            e["commits"].append(c)
            e["files"].update(c["files"])
    untraced = [c for c in commits if not c["refs"]]
    return {"commits": len(commits), "traced": len(commits) - len(untraced),
            "by_ref": {k: {"commits": v["commits"], "files": sorted(v["files"])} for k, v in sorted(by_ref.items())},
            "untraced": untraced, "archived_missing": missing}


def render(t: dict, repo: Path) -> str:
    lines = ["---", "generated_by: kairo/trace_code@1.0.0", f"generated: {date.today().isoformat()}", f"repo: {repo}", "---", "",
             "# Traza código ↔ ciencia", "",
             "> Vista derivada del historial del repositorio (trailers `Motivated-By:`). No editar a mano.", "",
             f"{t['traced']} de {t['commits']} commits nombran la hipótesis, experimento o decisión que los motiva.", ""]
    for ref, e in t["by_ref"].items():
        lines += [f"## {ref}", "", f"- archivos: {', '.join(f'`{f}`' for f in e['files'][:30]) or '—'}"]
        lines += [f"- `{c['sha']}` {c['date']} — {c['subject']}" + (f" (repo archivado: {c['repo']})" if c.get("repo") else "")
                  for c in e["commits"][:30]]
        lines.append("")
    if t["untraced"]:
        lines += ["## Sin motivación declarada", ""]
        lines += [f"- `{c['sha']}` {c['date']} — {c['subject']}" for c in t["untraced"][:50]]
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--project-dir", required=True, type=Path)
    ap.add_argument("--vault", type=Path)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--limit", type=int, default=500)
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        t = build(a.repo, a.limit, hub_archived(a.project_dir))
        if a.write:
            (a.project_dir / "_codigo-ciencia.md").write_text(render(t, a.repo), encoding="utf-8", newline="\n")
        print(json.dumps(t, ensure_ascii=False))
        return 0
    except (OSError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
