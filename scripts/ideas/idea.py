#!/usr/bin/env python3
"""Idea inbox: capture an idea in seconds; Kairo keeps it and proposes it when it fits.

    idea.py add  --vault V [--source cli|ui|claude-code] [--project PROJ-XXX]
                 [--context "…"] [--commit] <text…>
    idea.py list --vault V [--status nueva|propuesta|usada|descartada] [--json]
    idea.py set  --vault V --id I-XXXX [--status …] [--matches '<json list>']
                 [--job <id>] [--hypothesis H-XXXX] [--commit]

An idea is ``Ideas/I-XXXX.md`` (vault-wide ids): the text in the body, exactly
as captured, and in the frontmatter only metadata — when, from where, its
status, the project it was aimed at (if any), which projects it matches and
what it became. An idea is not a hypothesis: turning one into a hypothesis
always goes through ``hypothesis-cycle``.

``--commit`` commits the idea note alone (``git commit --only``) when the vault
is in a git repository; nothing else in the working tree is touched.

Exit codes: 0 ok · 3 refused · 1 error. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

__version__ = "1.0.0"
STATUSES = ("nueva", "propuesta", "usada", "descartada")
SOURCES = ("cli", "ui", "claude-code")
_ID = re.compile(r"^I-\d{4,}$")
_PROJ = re.compile(r"^PROJ-\d{3,}$")
_HID = re.compile(r"^H-\d{3,4}$")


class Refused(Exception):
    pass


def ideas_dir(vault: Path) -> Path:
    return vault / "Ideas"


def next_id(vault: Path) -> str:
    nums = [int(m.group(1)) for p in ideas_dir(vault).glob("I-*.md") if (m := re.match(r"I-(\d+)", p.name))]
    return f"I-{(max(nums) + 1) if nums else 1:04d}"


def yaml_str(s: str) -> str:
    """A double-quoted YAML scalar (JSON strings are valid YAML)."""
    return json.dumps(s, ensure_ascii=False)


def commit(vault: Path, path: Path, message: str) -> bool:
    def git(*a):
        return subprocess.run(["git", *a], cwd=vault, capture_output=True, text=True)
    if git("rev-parse", "--is-inside-work-tree").returncode != 0:
        return False
    rel = path.resolve().relative_to(vault.resolve()).as_posix()
    if git("add", "--", rel).returncode != 0:
        return False
    return git("commit", "--quiet", "--only", "-m", message, "--", rel).returncode == 0


def add(vault: Path, text: str, source: str, project: str | None, context: str | None, now: str | None = None) -> Path:
    text = text.strip()
    if not text:
        raise Refused("an idea needs some text")
    if source not in SOURCES:
        raise Refused(f"source must be one of {SOURCES}")
    if project is not None and not _PROJ.match(project):
        raise Refused("project must look like PROJ-XXX")
    iid = next_id(vault)
    captured = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fm = ["---", f"id: {iid}", f"captured_at: {captured}", f"source: {source}", "status: nueva"]
    if project:
        fm.append(f"project: {project}")
    fm += ["matches: []", "---", "", "## Idea", "", text, ""]
    if context and context.strip():
        fm += ["## Contexto", "", context.strip(), ""]
    path = ideas_dir(vault) / f"{iid}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(fm), encoding="utf-8", newline="\n")
    return path


def parse(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n(.*)$", text, re.S)
    if not m:
        raise Refused(f"{path.name}: no frontmatter")
    meta: dict = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        v = v.strip()
        if k == "matches":
            meta[k] = json.loads(v) if v else []
        elif k and v.startswith('"'):
            meta[k.strip()] = json.loads(v)
        elif k:
            meta[k.strip()] = v
    body = m.group(2)
    idea = re.search(r"## Idea\s*\n(.*?)(?=\n## |\Z)", body, re.S)
    ctx = re.search(r"## Contexto\s*\n(.*?)(?=\n## |\Z)", body, re.S)
    meta["text"] = idea.group(1).strip() if idea else ""
    meta["context"] = ctx.group(1).strip() if ctx else None
    meta["file"] = str(path)
    return meta


def list_ideas(vault: Path, status: str | None = None) -> list[dict]:
    out = [parse(p) for p in sorted(ideas_dir(vault).glob("I-*.md"))]
    return [i for i in out if status is None or i.get("status") == status]


def set_fields(vault: Path, iid: str, status: str | None, matches: list | None,
               job: str | None, hypothesis: str | None) -> Path:
    if not _ID.match(iid):
        raise Refused("id must look like I-XXXX")
    path = ideas_dir(vault) / f"{iid}.md"
    if not path.exists():
        raise Refused(f"{iid} not found")
    if status is not None and status not in STATUSES:
        raise Refused(f"status must be one of {STATUSES}")
    if hypothesis is not None and not _HID.match(hypothesis):
        raise Refused("hypothesis must look like H-XXXX")
    if matches is not None:
        if not isinstance(matches, list) or not all(
                isinstance(m, dict) and isinstance(m.get("project"), str) and isinstance(m.get("score"), (int, float))
                for m in matches):
            raise Refused("matches must be a list of {project, score, via}")
    text = path.read_text(encoding="utf-8")
    head, sep, rest = text.partition("\n---\n")
    lines = head.split("\n")

    def put(key: str, value: str) -> None:
        for i, ln in enumerate(lines):
            if ln.startswith(f"{key}:"):
                lines[i] = f"{key}: {value}"
                return
        lines.append(f"{key}: {value}")

    if status is not None:
        put("status", status)
    if matches is not None:
        put("matches", json.dumps(matches, ensure_ascii=False))
    if job is not None:
        put("job", yaml_str(job))
    if hypothesis is not None:
        put("became", hypothesis)
    path.write_text("\n".join(lines) + sep + rest, encoding="utf-8", newline="\n")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--vault", required=True, type=Path)
    a.add_argument("--source", default="cli")
    a.add_argument("--project")
    a.add_argument("--context")
    a.add_argument("--commit", action="store_true")
    a.add_argument("text", nargs="+")
    lp = sub.add_parser("list")
    lp.add_argument("--vault", required=True, type=Path)
    lp.add_argument("--status")
    lp.add_argument("--json", action="store_true")
    s = sub.add_parser("set")
    s.add_argument("--vault", required=True, type=Path)
    s.add_argument("--id", required=True)
    s.add_argument("--status")
    s.add_argument("--matches")
    s.add_argument("--job")
    s.add_argument("--hypothesis")
    s.add_argument("--commit", action="store_true")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args.cmd == "add":
            path = add(args.vault, " ".join(args.text), args.source, args.project, args.context)
            committed = commit(args.vault, path, f"Idea {path.stem}") if args.commit else False
            print(json.dumps({"id": path.stem, "path": str(path), "committed": committed}, ensure_ascii=False))
            return 0
        if args.cmd == "list":
            ideas = list_ideas(args.vault, args.status)
            if args.json:
                print(json.dumps(ideas, ensure_ascii=False))
            else:
                for i in ideas:
                    print(f"{i['id']} [{i.get('status')}] {i['text'][:100]}")
            return 0
        matches = json.loads(args.matches) if args.matches else None
        path = set_fields(args.vault, args.id, args.status, matches, args.job, args.hypothesis)
        committed = commit(args.vault, path, f"Idea {args.id}: {args.status or 'actualizada'}") if args.commit else False
        print(json.dumps({"id": args.id, "committed": committed}))
        return 0
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
