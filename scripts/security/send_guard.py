#!/usr/bin/env python3
"""Enforce `send: never` -- notes whose content must never reach the model.

A vault note whose frontmatter carries `send: never` is private to the
researcher: Claude must not read it, quote it, or send its metadata to an
external API. Skills and agents skip such notes explicitly; this script is the
mechanical backstop, run as a Claude Code **PreToolUse command hook** (see
`docs/v3-pending/A-hook.md` for the settings.json snippet), plus two read-only
helper modes the skills use to find flagged notes without opening them.

Hook mode (default; reads the PreToolUse JSON from stdin):

    python send_guard.py hook [--vault <vault root>]

  Blocks (exit 2, reason on stderr -- the reason names the file, never its
  content) when the tool call would put a flagged note's content in front of
  the model:

    Read                         `file_path` is a flagged note
    Grep (output_mode=content)   a flagged note lies inside the search scope
                                 (`path`/`paths` + `glob`; a brace glob counts
                                 as matching); `files_with_matches`
                                 and `count` only reveal file names and pass
    Bash / PowerShell            the command names a flagged note -- its path,
                                 file name, stem or Kairo id (`P-9999*`),
                                 case-insensitive -- best effort, see below
    mcp__smart-connections__get_note
                                 `notePath` (relative to the vault) is flagged

  Everything else passes (exit 0, no output). Glob, Write and Edit pass: Glob
  returns names only; Write/Edit need a prior Read, which is blocked.

  Paths are trimmed of surrounding whitespace/quotes first, as the tools do.

  Limits (stated, not hidden): a shell command can reach a flagged note without
  naming it (`grep -r x .`, `cat Papers/*`). The Bash check catches the direct cases;
  the instruction in every skill/agent that reads the vault is the primary
  control. Smart Connections search results carry path + title + heading name
  only, never note text, so they are not blocked here.

  Fail-open on the hook's own errors (unparseable input, unreadable file): a
  broken guard must not lock the researcher out of every Read. Such errors go
  to stderr (debug log only on exit 0).

Helper modes (print paths only, never content):

    python send_guard.py check <file> [<file> ...]   # exit 3 if any is flagged
    python send_guard.py list <dir> [--json]         # every flagged note under dir

Exit codes: 0 allowed / nothing flagged, 2 blocked (hook mode), 3 at least one
flagged (check mode), 1 usage error. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from fnmatch import fnmatch
from pathlib import Path

__version__ = "1.0.0"

HEAD_BYTES = 16 * 1024  # frontmatter lives at the top; never read further
SKIP_DIRS = {".git", ".obsidian", ".smart-env", "node_modules", ".trash", "__pycache__"}
_SEND_LINE = re.compile(r"""^send\s*:\s*["']?never["']?\s*(#.*)?$""", re.IGNORECASE)
_NOTE_ID = re.compile(r"^([A-Z]{1,4}-\d{3,})", re.IGNORECASE)  # P-0001, H-0012, E-0003, ADR-004


# --------------------------------------------------------------------------- #
# Detection (pure; unit-tested)
# --------------------------------------------------------------------------- #

def frontmatter_says_never(head: str) -> bool:
    """True when the YAML frontmatter at the top of `head` has `send: never`."""
    lines = head.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    for line in lines[1:]:
        if line.strip() == "---":
            return False
        # an unterminated header (or one longer than HEAD_BYTES) still counts:
        # when in doubt, the guard errs toward not sending
        if _SEND_LINE.match(line.strip()) and not line[:1].isspace():
            return True
    return False


def is_flagged(path: Path) -> bool:
    """Whether `path` is a markdown note marked `send: never`. Unreadable or
    non-markdown files are not flagged (the guard fails open)."""
    if path.suffix.lower() != ".md":
        return False
    try:
        with open(path, "rb") as fh:
            head = fh.read(HEAD_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return False
    return frontmatter_says_never(head)


def flagged_under(root: Path) -> list[Path]:
    """Every flagged note under `root` (or `root` itself if it is a file)."""
    if root.is_file():
        return [root] if is_flagged(root) else []
    out: list[Path] = []
    if not root.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.lower().endswith(".md"):
                p = Path(dirpath) / name
                if is_flagged(p):
                    out.append(p)
    return sorted(out)


# --------------------------------------------------------------------------- #
# Hook decision
# --------------------------------------------------------------------------- #

def _clean(p) -> str:
    """Tool paths as the tool itself will see them: MCP servers and shells trim
    surrounding whitespace and quotes, so the guard must too."""
    return str(p).strip().strip("\"'").strip()


def _resolve(p: str, base: Path) -> Path:
    q = Path(os.path.expanduser(_clean(p)))
    return (q if q.is_absolute() else base / q).resolve()


def _rel(p: Path, base: Path) -> str:
    try:
        return p.relative_to(base).as_posix()
    except ValueError:
        return p.as_posix()


def decide(event: dict, vault: Path | None) -> str | None:
    """Return a block reason, or None to allow."""
    tool = str(event.get("tool_name") or "")
    tin = event.get("tool_input") or {}
    cwd = Path(event.get("cwd") or os.getcwd())
    root = vault or Path(os.environ.get("CLAUDE_PROJECT_DIR") or cwd)

    if tool == "Read":
        fp = _clean(tin.get("file_path") or "")
        if fp and is_flagged(_resolve(fp, cwd)):
            return f"{_rel(_resolve(fp, cwd), cwd)} está marcada `send: never`; no se lee."
        return None

    if tool == "Grep":
        if (tin.get("output_mode") or "files_with_matches") != "content":
            return None
        scopes = tin.get("paths") or [tin.get("path") or "."]
        if isinstance(scopes, str):
            scopes = [scopes]
        glob = tin.get("glob")
        hits: list[Path] = []
        for s in scopes:
            for p in flagged_under(_resolve(str(s), cwd)):
                # fnmatch has no brace expansion ({md,txt}); any glob it can't
                # evaluate faithfully counts as matching -- err toward not sending
                if (not glob or any(c in glob for c in "{}") or fnmatch(p.name, glob)
                        or fnmatch(p.as_posix(), f"*{glob}")):
                    hits.append(p)
        if hits:
            names = ", ".join(_rel(p, cwd) for p in hits[:5]) + (" …" if len(hits) > 5 else "")
            return (f"Grep en modo content incluiría notas `send: never` ({names}). "
                    "Acota `path`/`glob` para excluirlas, o usa output_mode files_with_matches.")
        return None

    if tool in ("Bash", "PowerShell"):
        cmd = str(tin.get("command") or "")
        if not cmd:
            return None
        # No verb prefilter: any command that names a flagged note -- by path,
        # file name, stem, or its Kairo id (a `P-9999*` glob) -- is refused.
        # Case-insensitive, because Windows paths are.
        norm = cmd.replace("\\", "/").lower()
        for p in flagged_under(root):
            rel = _rel(p, root)
            if any(t and t in norm for t in (rel.lower(), p.name.lower(),
                                            p.stem.lower() if len(p.stem) >= 6 else "")):
                return f"El comando nombra una nota `send: never` ({rel}); no se ejecuta."
            m = _NOTE_ID.match(p.name)
            if m and re.search(rf"(?<![a-z0-9]){re.escape(m.group(1).lower())}(?!\d)", norm):
                return (f"El comando menciona {m.group(1)}, una nota `send: never` ({rel}); "
                        "no se ejecuta.")
        return None

    if tool.startswith("mcp__") and tool.endswith("__get_note"):
        np_ = _clean(tin.get("notePath") or "")
        if np_ and is_flagged(_resolve(str(np_), root)):
            return f"{np_} está marcada `send: never`; get_note bloqueado."
        return None

    return None


def run_hook(vault: str | None) -> int:
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError) as exc:
        print(f"send_guard: unparseable hook input ({exc}); allowing", file=sys.stderr)
        return 0
    try:
        reason = decide(event, Path(vault).resolve() if vault else None)
    except Exception as exc:  # fail open, loudly in the debug log
        print(f"send_guard: internal error ({exc!r}); allowing", file=sys.stderr)
        return 0
    if reason:
        print(f"send_guard (Kairo): {reason}", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="mode")
    h = sub.add_parser("hook", help="PreToolUse hook (JSON on stdin)")
    h.add_argument("--vault", help="vault root (default: $CLAUDE_PROJECT_DIR, else cwd)")
    c = sub.add_parser("check", help="exit 3 if any given file is flagged")
    c.add_argument("files", nargs="+")
    l_ = sub.add_parser("list", help="list flagged notes under a directory")
    l_.add_argument("dir")
    l_.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    if args.mode in (None, "hook"):
        return run_hook(getattr(args, "vault", None))
    if args.mode == "check":
        flagged = [f for f in args.files if is_flagged(Path(f))]
        for f in flagged:
            print(f"send: never — {Path(f).as_posix()}")
        return 3 if flagged else 0
    root = Path(args.dir)
    if not root.exists():
        print(f"send_guard: no such path: {root}", file=sys.stderr)
        return 1
    found = [_rel(p.resolve(), root.resolve()) for p in flagged_under(root)]
    if args.json:
        print(json.dumps({"root": root.as_posix(), "flagged": found}, ensure_ascii=False))
    else:
        print("\n".join(found) if found else "(ninguna nota send: never)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
