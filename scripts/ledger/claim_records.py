"""Append-only record blocks in a claim note's frontmatter.

Two blocks, same line-based discipline as `verifications:` (no YAML library;
every other frontmatter line stays byte-for-byte, line endings preserved;
entries are never edited or removed — the latest entry governs):

    signoffs:            the researcher's sign-offs (signoff.py)
      - part: statement | proof | infeasibility
        sha256: <sha256 of the exact text signed>
        by: <name>
        date: <YYYY-MM-DD>
    numerical_checks:    runs of the claim's numerical sanity check (numeric_check.py)
      - status: passed | failed | error | not_feasible
        statement_sha256: <sha256 of ## Enunciado when it ran>
        script_sha256: <sha256 of Claims/checks/C-XXXX.py, or none>
        output_sha256: <sha256 of its captured output, or none>
        reason: <why it is not feasible — not_feasible only>
        date: <YYYY-MM-DD>
        by: <who ran it>

Plus the texts these records bind to: `statement_text` / `proof_text` are the
exact `## Enunciado` / `## Demostración` sections (whitespace at the ends
trimmed, line endings normalised), and `text_sha256` hashes them.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from verifications import (InputError, block_end, find_key, frontmatter_bounds, read_raw,  # noqa: F401
                           split_lines, strip_comment, unquote, write_raw, yaml_scalar, KEY_LINE_RE)
from verifier_packet import body_sections, split_frontmatter


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def norm(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").strip().split("\n"))


def sections(path: str | Path) -> dict[str, str]:
    _fm, body = split_frontmatter(read_raw(str(path)))
    return {h: norm(t) for h, t in body_sections(body)}


def statement_text(path: str | Path) -> str:
    return sections(path).get("Enunciado", "")


def proof_text(path: str | Path) -> str:
    return sections(path).get("Demostración", "")


def read_block(path: str | Path, key: str) -> list[dict]:
    lines, _ = split_lines(read_raw(str(path)))
    lo, hi = frontmatter_bounds(lines)
    i = find_key(lines, lo + 1, hi, key)
    if i is None:
        return []
    head, _ = strip_comment(KEY_LINE_RE.match(lines[i].rstrip("\r\n")).group(2))
    if head.strip() not in ("[]", "", "~", "null"):
        raise InputError(f"{key}: must be a block list (or [])")
    entries: list[dict] = []
    for j in range(i + 1, block_end(lines, i, hi)):
        s = lines[j].rstrip("\r\n")
        if not s.strip() or s.lstrip().startswith("#"):
            continue
        m = re.match(r"^(\s*)-\s+(.*)$", s)
        body = m.group(2) if m else s.strip()
        if m:
            entries.append({})
        if not entries:
            raise InputError(f"malformed {key}: block")
        km = re.match(r"^([A-Za-z_0-9]+)\s*:\s?(.*)$", body.strip())
        if not km:
            raise InputError(f"malformed {key} line: {s!r}")
        val, _ = strip_comment(km.group(2))
        entries[-1][km.group(1)] = unquote(val)
    return entries


def append_block(path: str | Path, key: str, entry: dict) -> None:
    raw = read_raw(str(path))
    lines, nl = split_lines(raw)
    lo, hi = frontmatter_bounds(lines)
    i = find_key(lines, lo + 1, hi, key)
    new = [f"  - {k}: {yaml_scalar(str(v))}" if n == 0 else f"    {k}: {yaml_scalar(str(v))}"
           for n, (k, v) in enumerate(entry.items())]
    new = [s + nl for s in new]
    if i is None:
        lines[hi:hi] = [f"{key}:{nl}", *new]
    else:
        head, comment = strip_comment(KEY_LINE_RE.match(lines[i].rstrip("\r\n")).group(2))
        if head.strip() in ("[]", "~", "null"):
            lines[i] = f"{key}:{(' ' + comment) if comment else ''}{nl}"
        end = block_end(lines, i, hi)
        lines[end:end] = new
    write_raw(str(path), "".join(lines))


def latest(entries: list[dict], **match: str) -> dict | None:
    for e in reversed(entries):
        if all(e.get(k) == v for k, v in match.items()):
            return e
    return None
