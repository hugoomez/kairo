"""Minimal, standard-library-only reader/writer for Kairo note frontmatter.

Shared by build_graph.py and claim_status.py. It understands exactly what the
ledger needs and nothing more:

- top-level scalar keys (``status: propuesta  # comment``),
- inline lists (``depends_on: [H-0001, C-0002]``),
- block lists of scalars (``depends_on:\\n  - H-0001``).

Nested mappings (``environment:``, ``history:``) are skipped, not parsed. Values
are returned as plain strings / lists of strings. Writing is line-based and only
ever touches the lines of the key being changed, so everything else in the note
stays byte-for-byte identical.
"""

from __future__ import annotations

import re
from pathlib import Path

_KEY = re.compile(r"^([A-Za-z_][\w-]*):(.*)$")
_ID = re.compile(r"\b([HCE]-\d{4})\b")


def split_note(text: str) -> tuple[list[str], str] | None:
    """Return (frontmatter lines, body) or None if the note has no frontmatter."""
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    while end != -1 and not (text[end + 4:end + 5] in ("\n", "")):
        end = text.find("\n---", end + 1)
    if end == -1:
        return None
    fm = text[4:end].split("\n")
    body = text[end + 4:]
    if body.startswith("\n"):
        body = body[1:]
    return fm, body


def _strip_comment(value: str) -> str:
    out, quote = [], None
    for i, ch in enumerate(value):
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "#" and (i == 0 or value[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).strip()


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def _parse_inline_list(s: str) -> list[str]:
    inner = s.strip()[1:-1].strip()
    if not inner:
        return []
    return [_unquote(x) for x in inner.split(",") if x.strip()]


def parse_frontmatter(lines: list[str]) -> dict[str, object]:
    """Top-level keys only. Block lists of scalars become lists; nested maps -> ''."""
    fm: dict[str, object] = {}
    i = 0
    while i < len(lines):
        m = _KEY.match(lines[i])
        if not m:
            i += 1
            continue
        key, raw = m.group(1), _strip_comment(m.group(2))
        if raw.startswith("[") and raw.endswith("]"):
            fm[key] = _parse_inline_list(raw)
            i += 1
            continue
        if raw:
            fm[key] = _unquote(raw)
            i += 1
            continue
        # empty value: a block list, a nested map, or genuinely empty
        items: list[str] = []
        j = i + 1
        nested = False
        while j < len(lines) and (lines[j].startswith((" ", "\t")) or not lines[j].strip()):
            stripped = lines[j].strip()
            if stripped.startswith("- ") and not nested:
                item = _strip_comment(stripped[2:])
                if ":" in item and not item.startswith(("'", '"')):
                    nested = True  # list of mappings (e.g. history) -> not a scalar list
                else:
                    items.append(_unquote(item))
            elif stripped and not stripped.startswith("#"):
                nested = True
            j += 1
        fm[key] = "" if nested else items if items else ""
        i = j
    return fm


def read_note(path: Path) -> tuple[dict[str, object], str] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    parts = split_note(text)
    if parts is None:
        return None
    fm_lines, body = parts
    return parse_frontmatter(fm_lines), body


def as_list(value: object) -> list[str]:
    """Normalise a depends_on-style value into a list of ids (placeholders dropped)."""
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str) and value.strip():
        raw = [value]
    else:
        raw = []
    ids = []
    for item in raw:
        ids.extend(_ID.findall(item))
    return ids


def section(body: str, heading: str) -> str:
    pat = re.compile(r"^##\s+" + re.escape(heading) + r"\s*$(.*?)(?=^##\s|\Z)",
                     re.DOTALL | re.MULTILINE)
    m = pat.search(body)
    return m.group(1).strip() if m else ""


def first_line(text: str, limit: int = 140) -> str:
    for ln in text.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith(("<", "<!--")):
            ln = ln.replace("|", r"\|")
            return ln if len(ln) <= limit else ln[: limit - 1].rstrip() + "…"
    return ""


def set_scalar(fm_lines: list[str], key: str, value: str) -> list[str]:
    """Replace a top-level scalar key's value, keeping any trailing comment.
    Appends the key at the end if absent."""
    out = list(fm_lines)
    for i, line in enumerate(out):
        m = _KEY.match(line)
        if m and m.group(1) == key:
            rest = m.group(2)
            comment = ""
            stripped = _strip_comment(rest)
            idx = rest.find(stripped) + len(stripped) if stripped else 0
            tail = rest[idx:]
            if "#" in tail:
                comment = "  " + tail[tail.index("#"):].strip()
            out[i] = f"{key}: {value}{comment}"
            return out
    out.append(f"{key}: {value}")
    return out


def append_block_entry(fm_lines: list[str], key: str, entry_lines: list[str]) -> list[str]:
    """Append one entry (already indented with '  - ...') to a top-level block list
    such as ``history:``. Creates the key if absent; converts ``key: []``."""
    out = list(fm_lines)
    for i, line in enumerate(out):
        m = _KEY.match(line)
        if m and m.group(1) == key:
            if _strip_comment(m.group(2)) == "[]":
                out[i] = f"{key}:"
            j = i + 1
            while j < len(out) and (out[j].startswith((" ", "\t")) or not out[j].strip()):
                j += 1
            # don't swallow trailing blank lines into the list
            while j > i + 1 and not out[j - 1].strip():
                j -= 1
            return out[:j] + entry_lines + out[j:]
    return out + [f"{key}:"] + entry_lines


def join_note(fm_lines: list[str], body: str) -> str:
    return "---\n" + "\n".join(fm_lines) + "\n---\n" + body
