#!/usr/bin/env python3
"""Enforce `send: never` -- notes whose content must never reach the model --
and keep model-written reading notes (`Papers/_notas/`) away from every model
that reads the vault.

A vault note whose frontmatter carries `send: never` is private to the
researcher: Claude must not read it, quote it, or send its metadata to an
external API. Skills and agents skip such notes explicitly; this script is the
mechanical backstop, run as a Claude Code **PreToolUse command hook** (see
the vault's `.claude/settings.json`; `docs/v3-interfaces.md` §3e), plus two read-only
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

  The same four checks also refuse model-written reading notes: any `.md`
  under a `Papers/_notas/` directory (the `## Notas de lectura` moved out of the
  paper notes). They are a reading aid for the researcher, never a source, so
  no skill or agent may read them: a Read of one, a content Grep whose scope
  holds one (unless its `glob` is a negation naming `_notas`, e.g.
  `!**/_notas/**`), a shell command naming a `_notas` path segment, or a
  get_note on one is blocked.

  Everything else passes (exit 0, no output). Glob, Write and Edit pass: Glob
  returns names only; Write/Edit need a prior Read, which is blocked.

  Paths are trimmed of surrounding whitespace/quotes first, as the tools do.

  A reader command (`SHELL_READER`, the one list isolation.py also uses:
  `cat`, `grep`, `rg`, `Get-Content`, `cp`, `xargs`, `python`, `node`, …)
  that reaches a flagged note without naming it is refused too: a glob that
  matches one (`cat Papers/*`) or a recursive reader over a folder holding one
  (`grep -r x Papers`, `rg x .`).

  Limits (stated, not hidden): a program can still open a flagged note in ways
  no command line shows (`python -c …`, a script). The Bash check catches the
  direct cases;
  the instruction in every skill/agent that reads the vault is the primary
  control. Smart Connections search results carry path + title + heading name
  only, never note text, so they are not blocked here.

  Fail-closed inside a Kairo vault: a note that exists but cannot be read
  counts as flagged, and an unparseable hook input or an internal error
  refuses the call (exit 2, one line on stderr; kairo_hook.py also logs it to
  hook-events). Outside a vault (no Papers/ + Projects/ at the root) the hook
  stays out of the way and allows. The vault scan is cached by kairo_hook
  (`CACHE_DIR`: each note's flag keyed by its mtime and size), so a shell call
  in a vault of thousands of notes stats them instead of reading them.

Helper modes (print paths only, never content):

    python send_guard.py check <file> [<file> ...]   # exit 3 if any is flagged
                                                     # or is a Papers/_notas/ note
    python send_guard.py list <dir> [--json]         # every flagged note under dir

Exit codes: 0 allowed / nothing flagged, 2 blocked (hook mode), 3 at least one
flagged (check mode), 1 usage error. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from fnmatch import fnmatch
from pathlib import Path

__version__ = "1.3.0"

HEAD_BYTES = 16 * 1024  # frontmatter lives at the top; never read further
SKIP_DIRS = {".git", ".obsidian", ".smart-env", "node_modules", ".trash", "__pycache__"}
_SEND_LINE = re.compile(r"""^send\s*:\s*["']?never["']?\s*(#.*)?$""", re.IGNORECASE)
_NOTE_ID = re.compile(r"^([A-Z]{1,4}-\d{3,})", re.IGNORECASE)  # P-0001, H-0012, E-0003, ADR-004
MODEL_NOTES_DIR = "_notas"  # Papers/_notas/: model-written reading notes, never a source
_MODEL_NOTES_TOKEN = re.compile(r"(?<![\w.-])_notas(?![\w.-])", re.IGNORECASE)


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


def _read_flag(path: Path) -> bool:
    """Read the top of `path` and say whether its frontmatter has `send: never`.
    A file that exists but cannot be read counts as flagged (fail closed)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(HEAD_BYTES).decode("utf-8", errors="replace")
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return frontmatter_says_never(head)


# (path, mtime_ns, size) -> flagged, for this process
_READ_MEMO: dict[tuple[str, int, int], bool] = {}
# Where the flagged-note scan is cached between hook calls (set by kairo_hook to
# `<state dir>/send-guard-cache`); None: no file is written.
CACHE_DIR: Path | None = None


def is_flagged(path: Path) -> bool:
    """Whether `path` is a markdown note marked `send: never`. Non-markdown and
    missing files are not flagged; a note that exists but cannot be read is
    (the guard fails closed)."""
    if path.suffix.lower() != ".md":
        return False
    return _read_flag(path)


def is_model_notes(path: Path) -> bool:
    """True when `path` is inside a `Papers/_notas/` directory (or is that
    directory). Case-insensitive, like Windows paths, and a backslash counts as a
    separator on every OS: a Windows-style path in a tool call is still refused."""
    parts = [x.lower() for x in Path(str(path).replace("\\", "/")).parts]
    return any(a == "papers" and b == MODEL_NOTES_DIR for a, b in zip(parts, parts[1:]))


def model_notes_under(root: Path) -> list[Path]:
    """Every model-written reading note under `root` (or `root` itself)."""
    if root.is_file():
        return [root] if is_model_notes(root) and root.suffix.lower() == ".md" else []
    out: list[Path] = []
    if not root.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            p = Path(dirpath) / name
            if name.lower().endswith(".md") and is_model_notes(p):
                out.append(p)
    return sorted(out)


def _md_files(root: Path):
    """(path, mtime_ns, size) of every .md under `root`, SKIP_DIRS pruned. scandir
    gives the stat for free on Windows, so no note is opened here."""
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name not in SKIP_DIRS:
                        stack.append(Path(e.path))
                elif e.name.lower().endswith(".md"):
                    st = e.stat()
                    yield Path(e.path), st.st_mtime_ns, st.st_size
            except OSError:
                continue


def _cache_file(root: Path) -> Path | None:
    if CACHE_DIR is None:
        return None
    key = hashlib.sha256(str(root.resolve()).lower().encode("utf-8")).hexdigest()[:24]
    return Path(CACHE_DIR) / f"{key}.json"


def flagged_under(root: Path) -> list[Path]:
    """Every flagged note under `root` (or `root` itself if it is a file).

    Each note is read once per (mtime, size): the answers are kept in memory and,
    when `CACHE_DIR` is set (the hook), in a small JSON file there, so a shell
    call in a large vault stats its notes instead of reading every one."""
    if root.is_file():
        return [root] if is_flagged(root) else []
    if not root.is_dir():
        return []
    cf = _cache_file(root)
    cached: dict = {}
    if cf is not None:
        try:
            cached = json.loads(cf.read_text(encoding="utf-8")).get("files") or {}
        except (OSError, ValueError, AttributeError):
            cached = {}
    fresh: dict[str, list] = {}
    out: list[Path] = []
    for p, mtime, size in _md_files(root):
        key = str(p)
        hit = cached.get(key)
        if isinstance(hit, list) and len(hit) == 3 and hit[0] == mtime and hit[1] == size:
            flagged = bool(hit[2])
        elif (key, mtime, size) in _READ_MEMO:
            flagged = _READ_MEMO[(key, mtime, size)]
        else:
            flagged = _READ_MEMO[(key, mtime, size)] = _read_flag(p)
        fresh[key] = [mtime, size, flagged]
        if flagged:
            out.append(p)
    if cf is not None and fresh != cached:
        try:
            cf.parent.mkdir(parents=True, exist_ok=True)
            tmp = cf.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps({"root": str(root), "files": fresh}), encoding="utf-8")
            os.replace(tmp, cf)
        except OSError:
            pass                      # a cache that cannot be written only costs time
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


# The one list of shell programs that can put a file's bytes in front of the
# model: viewers and filters, copiers (a copy is read next), archivers and
# encoders, and every interpreter that can open a file from inline code.
# isolation.py (the main thread kept away from paper text) uses this same list.
SHELL_READER = re.compile(
    r"(?:^|[\s;|&(`\"':])(?:cat|type|more|less|head|tail|sed|awk|gawk|grep|egrep|fgrep|zgrep|rg|ag|ack|findstr|"
    r"get-content|gc|select-string|sls|bat|nl|strings|od|xxd|hexdump|xargs|cp|copy|copy-item|cpi|mv|move|"
    r"move-item|rsync|scp|tar|zip|7z|base64|certutil|iconv|sort|uniq|cut|tr|paste|diff|cmp|comm|fc|jq|yq|tee|dd|"
    r"vi|vim|nano|pdftotext|mutool|python3?|py|node|deno|perl|ruby|php|powershell|pwsh|bash|sh|zsh|cmd|"
    r"import-csv|convertfrom-json|readall(?:text|lines|bytes)|iex|invoke-expression)(?:\.exe)?\b",
    re.IGNORECASE)
_RECURSIVE = re.compile(r"(?:^|\s)(?:-[a-zA-Z]*[rR][a-zA-Z]*|--recursive|-recurse|/s)(?=\s|$)", re.IGNORECASE)


def _shell_reach(cmd: str, root: Path, cwd: Path) -> str | None:
    """A reader command that reaches a flagged note without naming it: a glob that
    matches one (`cat Papers/*`), or a recursive reader over a folder holding one
    (`grep -r x Papers`, `rg x .`). Best effort, like every shell check here."""
    if not SHELL_READER.search(cmd):
        return None
    flagged = list(flagged_under(root))
    if not flagged:
        return None
    toks = [a or b or c for a, b, c in re.findall(r'"([^"]*)"|\'([^\']*)\'|(\S+)', cmd)]
    recursive = bool(_RECURSIVE.search(cmd)) or bool(re.search(r"(?:^|[\s;|&(])rg(?:\.exe)?\b", cmd))
    for tok in toks:
        t = tok.replace("\\", "/")
        if any(c in t for c in "*?["):
            for p in flagged:
                rel = _rel(p, root).replace("\\", "/").lower()
                full = str(p).replace("\\", "/").lower()
                pat = t.lower().lstrip("./")
                if fnmatch(rel, pat) or fnmatch(full, t.lower()) or fnmatch(rel, f"*/{pat}") or \
                        (recursive and fnmatch(p.name.lower(), Path(pat).name)):
                    return f"El comando alcanza con «{tok}» una nota `send: never` ({_rel(p, root)}); no se ejecuta."
        elif recursive and not t.startswith("-"):
            d = _resolve(t, cwd)
            if d.is_dir():
                hit = next((p for p in flagged if d == p.parent or d in p.parents), None)
                if hit:
                    return (f"El comando recorre «{tok}», que contiene una nota `send: never` "
                            f"({_rel(hit, root)}); acota la ruta.")
    return None


def decide(event: dict, vault: Path | None) -> str | None:
    """Return a block reason, or None to allow."""
    tool = str(event.get("tool_name") or "")
    tin = event.get("tool_input") or {}
    cwd = Path(event.get("cwd") or os.getcwd())
    root = vault or Path(os.environ.get("CLAUDE_PROJECT_DIR") or cwd)

    if tool == "Read":
        fp = _clean(tin.get("file_path") or "")
        if fp and is_model_notes(_resolve(fp, cwd)):
            return (f"{_rel(_resolve(fp, cwd), cwd)} es una nota de lectura escrita por un "
                    "modelo (Papers/_notas/): no es fuente y no se lee.")
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
        # a negated glob that names _notas (`!**/_notas/**`) keeps them out of rg
        excludes_notes = bool(glob) and glob.lstrip().startswith("!") and             MODEL_NOTES_DIR in glob.lower()
        notes: list[Path] = []
        for s in scopes if not excludes_notes else []:
            for p in model_notes_under(_resolve(str(s), cwd)):
                if (not glob or glob.lstrip().startswith("!") or any(c in glob for c in "{}")
                        or fnmatch(p.name, glob) or fnmatch(p.as_posix(), f"*{glob}")):
                    notes.append(p)
        if notes:
            names = ", ".join(_rel(p, cwd) for p in notes[:5]) + (" …" if len(notes) > 5 else "")
            return (f"Grep en modo content incluiría notas de lectura escritas por un modelo "
                    f"({names}). Acota `path` a las notas de Papers/ o usa "
                    "glob `!**/_notas/**`.")
        hits: list[Path] = []
        for s in scopes:
            for p in flagged_under(_resolve(str(s), cwd)):
                # fnmatch has no brace expansion ({md,txt}); any glob it can't
                # evaluate faithfully counts as matching -- err toward not sending
                # a negated glob (`!x`) includes everything else: counts as matching
                if (not glob or glob.lstrip().startswith("!") or any(c in glob for c in "{}")
                        or fnmatch(p.name, glob) or fnmatch(p.as_posix(), f"*{glob}")):
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
        if _MODEL_NOTES_TOKEN.search(norm):
            return ("El comando nombra Papers/_notas/ (notas de lectura escritas por un "
                    "modelo); no se ejecuta.")
        for p in flagged_under(root):
            rel = _rel(p, root)
            if any(t and t in norm for t in (rel.lower(), p.name.lower(),
                                            p.stem.lower() if len(p.stem) >= 6 else "")):
                return f"El comando nombra una nota `send: never` ({rel}); no se ejecuta."
            m = _NOTE_ID.match(p.name)
            if m and re.search(rf"(?<![a-z0-9]){re.escape(m.group(1).lower())}(?!\d)", norm):
                return (f"El comando menciona {m.group(1)}, una nota `send: never` ({rel}); "
                        "no se ejecuta.")
        return _shell_reach(cmd, root, Path(event.get("cwd") or root))

    if tool.startswith("mcp__") and tool.endswith("__get_note"):
        np_ = _clean(tin.get("notePath") or "")
        if np_ and is_model_notes(_resolve(str(np_), root)):
            return f"{np_} es una nota de lectura escrita por un modelo; get_note bloqueado."
        if np_ and is_flagged(_resolve(str(np_), root)):
            return f"{np_} está marcada `send: never`; get_note bloqueado."
        return None

    return None


def _in_vault(root: Path) -> bool:
    return (root / "Papers").is_dir() and (root / "Projects").is_dir()


def run_hook(vault: str | None) -> int:
    root = Path(vault or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError) as exc:
        if _in_vault(root):
            print(f"send_guard (Kairo): entrada del hook ilegible ({exc}); bloqueado por precaución.",
                  file=sys.stderr)
            return 2
        return 0
    try:
        reason = decide(event, Path(vault).resolve() if vault else None)
    except Exception as exc:  # fail closed inside a vault
        if _in_vault(root):
            print(f"send_guard (Kairo): error interno ({exc!r}); bloqueado por precaución.", file=sys.stderr)
            return 2
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
        flagged = []
        for f in args.files:
            if is_model_notes(Path(f).resolve()):
                print(f"notas de modelo — {Path(f).as_posix()}")
                flagged.append(f)
            elif is_flagged(Path(f)):
                print(f"send: never — {Path(f).as_posix()}")
                flagged.append(f)
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
