#!/usr/bin/env python3
"""Frontmatter helpers shared by the `scripts/citations/` tools.

Line-based on purpose (no YAML dependency), matching the conventions of
`scripts/code_repo/find_code_repo.py`: `split_frontmatter`, `fm_get`, plus
flow-list parsing (`authors: ["Last, First", ...]`, `linked_papers: [P-0001]`),
the `send: never` guard, field writing, and the append-only
`## Revisión de vigencia` section writer.

`send: never` means the note's content must not reach the model or any
external API. `is_send_never()` accepts the key and value case-insensitively,
with optional quotes and a trailing `# comment`. Callers skip such notes
entirely: no API call with their metadata, no title/authors in output.

This module is imported, not run. Standard library only.
"""

from __future__ import annotations

import re
from pathlib import Path

__version__ = "1.0.0"

_SEND_NEVER = re.compile(r"""^send\s*:\s*(["']?)never\1\s*(#.*)?$""", re.IGNORECASE)
PAPER_ID = re.compile(r"\bP-\d{4}\b")


def split_frontmatter(text: str) -> tuple[list[str], str] | None:
    text = text.lstrip("﻿")
    if not text.startswith("---"):
        return None
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return [ln.rstrip("\r") for ln in lines[1:i]], "\n".join(lines[i + 1:])
    return None


def _strip_comment(v: str) -> str:
    # drop a trailing ` # comment` outside quotes (good enough for flat values)
    if v and v[0] in "\"'":
        q = v[0]
        end = v.find(q, 1)
        return v[: end + 1] if end > 0 else v
    m = re.search(r"\s#", v)
    return v[: m.start()] if m else v


def fm_raw(fm_lines: list[str], key: str) -> str | None:
    """Raw value text of `key:` (comment stripped, not unquoted), or None if absent."""
    for ln in fm_lines:
        if re.match(rf"^{re.escape(key)}\s*:", ln):
            return _strip_comment(ln.split(":", 1)[1].strip()).strip()
    return None


def fm_get(fm_lines: list[str], key: str) -> str | None:
    """Unquoted scalar value, or None if absent or empty."""
    v = fm_raw(fm_lines, key)
    if v is None:
        return None
    v = v.strip().strip('"').strip("'").strip()
    return v or None


def parse_flow_list(raw: str | None) -> list[str]:
    """`["Power, Alethea", "Burda, Yuri"]` -> ['Power, Alethea', 'Burda, Yuri'];
    `[P-0001, P-0002]` -> ['P-0001', 'P-0002']. Quoted items may contain commas."""
    if not raw:
        return []
    s = raw.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    items, cur, quote = [], "", None
    for ch in s:
        if quote:
            if ch == quote:
                quote = None
            else:
                cur += ch
        elif ch in "\"'":
            quote = ch
        elif ch == ",":
            items.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        items.append(cur.strip())
    return [i for i in items if i]


def is_send_never(fm_lines: list[str]) -> bool:
    return any(_SEND_NEVER.match(ln.strip()) for ln in fm_lines)


def note_is_send_never(path: Path) -> bool:
    split = split_frontmatter(path.read_text(encoding="utf-8"))
    return bool(split and is_send_never(split[0]))


def note_id(path: Path, fm_lines: list[str] | None) -> str:
    return (fm_get(fm_lines or [], "id") or path.stem.split(" ")[0]).strip()


def _yaml_scalar(v) -> str:
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    s = str(v)
    if re.fullmatch(r"[A-Za-z0-9_.\-]+", s):
        return s
    return '"' + s.replace("\\", "\\\\").replace('"', "'") + '"'


def set_fields(text: str, fields: dict) -> str:
    """Set flat frontmatter fields, replacing existing lines for those keys or
    appending them (in the given order) just before the closing `---`. Every
    other line of the note is preserved byte for byte."""
    split = split_frontmatter(text)
    if split is None:
        raise ValueError("note has no YAML frontmatter")
    fm, body = split
    keys = set(fields)
    out, placed = [], set()
    for ln in fm:
        m = re.match(r"^([A-Za-z_][\w\-]*)\s*:", ln)
        if m and m.group(1) in keys:
            k = m.group(1)
            if k not in placed:
                out.append(f"{k}: {_yaml_scalar(fields[k])}".rstrip())
                placed.add(k)
            continue
        out.append(ln)
    for k, v in fields.items():
        if k not in placed:
            out.append(f"{k}: {_yaml_scalar(v)}".rstrip())
    return "---\n" + "\n".join(out) + "\n---\n" + body


REVISION_HEADING = "## Revisión de vigencia"


def append_revision_line(text: str, line: str) -> str:
    """Append `line` at the end of the `## Revisión de vigencia` section,
    creating the section at the end of the note if it does not exist.
    Append-only: nothing already in the note is changed or removed."""
    lines = text.split("\n")
    start = next((i for i, ln in enumerate(lines) if ln.strip() == REVISION_HEADING), None)
    if start is None:
        base = text.rstrip("\n")
        return f"{base}\n\n{REVISION_HEADING}\n\n{line}\n"
    end = next((j for j in range(start + 1, len(lines)) if lines[j].startswith("## ")), len(lines))
    # insert after the last non-blank line of the section
    k = end
    while k - 1 > start and not lines[k - 1].strip():
        k -= 1
    if k == start + 1:
        lines[k:k] = ["", line]
    else:
        lines[k:k] = [line]
    return "\n".join(lines)


def read_text(path: Path) -> tuple[str, str]:
    """(text with '\n' newlines, the file's newline style). Use with write_text so
    a note keeps its own line endings (Python's text mode would turn LF into
    CRLF on Windows)."""
    raw = path.read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in raw else "\n"
    return raw.replace("\r\n", "\n"), nl


def write_text(path: Path, text: str, newline: str = "\n") -> None:
    path.write_bytes(text.replace("\r\n", "\n").replace("\n", newline).encode("utf-8"))
