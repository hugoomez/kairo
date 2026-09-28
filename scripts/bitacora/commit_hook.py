#!/usr/bin/env python3
"""PostToolUse hook: record a vault commit in each touched project's action log.

Registered in the vault's ``.claude/settings.json`` on ``Bash`` / ``PowerShell``.
It fires for every shell call, so it exits immediately unless the command ran
``git commit``. Then it reads HEAD and, for every project the commit touched
(``Projects/<slug>/…``, or a ``Papers/`` note whose ``projects:`` lists the
project), appends one ``commit`` entry to ``Projects/<slug>/Bitacora/acciones.jsonl``
and re-renders today's page. This covers commits made by skills in a plain
Claude Code session as well as by the backend's agent sessions (they load the
vault's hooks too).

Skipped: a HEAD older than ``--max-age`` seconds (the commit command failed and
HEAD is someone else's), a commit already logged, and a commit that only touched
``Bitacora/`` files.

Usage (hook command)::

    python <plugin>/scripts/bitacora/commit_hook.py --vault "$CLAUDE_PROJECT_DIR"

Always exits 0 — a logging failure must never break the session; problems go to
stderr. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import action_log  # noqa: E402

# `git [global options] commit`: `commit` must be the subcommand, not an argument
# (`git log --grep commit` is not a commit).
_GIT_COMMIT = re.compile(
    r"""\bgit(?:\s+(?:-[Cc]\s+(?:"[^"]*"|'[^']*'|\S+)|--[\w-]+(?:=\S+)?))*\s+commit\b""")
_REF = re.compile(r"\b(?:H|E|C|P|EVO|ADR|F)-\d{3,4}\b")
_PROJ_INLINE = re.compile(r"^projects:\s*\[(.*?)\]\s*$", re.M)
_PROJ_BLOCK = re.compile(r"^projects:\s*\n((?:\s+-\s*.+\n?)+)", re.M)


def git(vault: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=vault, capture_output=True, check=True)
    return out.stdout.decode("utf-8", errors="replace")


def is_commit_command(payload: dict) -> bool:
    if payload.get("tool_name") not in ("Bash", "PowerShell"):
        return False
    command = (payload.get("tool_input") or {}).get("command") or ""
    return bool(_GIT_COMMIT.search(command))


def frontmatter(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[: end if end != -1 else 0]


def paper_projects(path: Path) -> list[str]:
    fm = frontmatter(path)
    m = _PROJ_INLINE.search(fm)
    if m:
        return [p.strip().strip("'\"") for p in m.group(1).split(",") if p.strip()]
    m = _PROJ_BLOCK.search(fm)
    if m:
        return [ln.strip()[1:].strip().strip("'\"") for ln in m.group(1).splitlines() if ln.strip()]
    return []


def project_ids(vault: Path) -> dict[str, str]:
    """PROJ id -> project folder slug, from each `_hub.md`."""
    out: dict[str, str] = {}
    for hub in (vault / "Projects").glob("*/_hub.md"):
        m = re.search(r"^id:\s*(\S+)", frontmatter(hub), re.M)
        if m:
            out[m.group(1)] = hub.parent.name
    return out


def touched_projects(vault: Path, files: list[str]) -> dict[str, list[str]]:
    """slug -> changed files attributed to it (Bitacora files excluded)."""
    ids = None
    out: dict[str, list[str]] = {}
    for f in files:
        parts = f.split("/")
        if len(parts) >= 3 and parts[0] == "Projects":
            if parts[2] == "Bitacora":
                continue
            out.setdefault(parts[1], []).append(f)
        elif len(parts) >= 2 and parts[0] == "Papers":
            if ids is None:
                ids = project_ids(vault)
            for pid in paper_projects(vault / f):
                if pid in ids:
                    out.setdefault(ids[pid], []).append(f)
    return out


def already_logged(log: Path, sha: str) -> bool:
    if not log.exists():
        return False
    tail = log.read_text(encoding="utf-8").splitlines()[-200:]
    return any(f'"commit":"{sha}"' in line for line in tail)


def run(vault: Path, payload: dict, max_age: int) -> list[str]:
    """Returns the slugs logged (for tests); raises on git errors."""
    if not is_commit_command(payload):
        return []
    head = git(vault, "log", "-1", "--format=%H%x1f%ct%x1f%s").strip()
    if not head:
        return []
    sha, ct, subject = head.split("\x1f", 2)
    if time.time() - int(ct) > max_age:
        return []
    files = [f for f in git(vault, "show", "--name-only", "--relative", "--format=", "HEAD").splitlines() if f]
    logged = []
    for slug, changed in sorted(touched_projects(vault, files).items()):
        log = vault / "Projects" / slug / "Bitacora" / "acciones.jsonl"
        if already_logged(log, sha):
            continue
        refs = sorted(set(_REF.findall(subject + " " + " ".join(changed))))
        written = action_log.append(log, {
            "actor": "agent",
            "by": "claude-code",
            "action": "commit",
            "event": "commit",
            "summary": subject.strip() or "(commit sin mensaje)",
            "commit": sha,
            "refs": refs,
        })
        page = log.parent / f"{written['date']}.md"
        page.write_text(action_log.render(action_log.load(log), written["date"], page),
                        encoding="utf-8", newline="\n")
        logged.append(slug)
    return logged


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vault", required=True, type=Path)
    ap.add_argument("--max-age", type=int, default=300, help="seconds; older HEADs are not this call's commit")
    args = ap.parse_args(argv)
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        run(args.vault.resolve(), payload, args.max_age)
    except Exception as exc:  # noqa: BLE001 — a hook must never break the session
        print(f"[bitacora] commit not logged: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
