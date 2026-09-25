#!/usr/bin/env python3
"""Append-only writer / reader for `verifications:` records (v3 contract §1b).

Every fresh-verifier run leaves one entry in the frontmatter of the verified
note, in exactly this shape and with exactly these five keys:

    verifications:
      - verifier: kairo/fresh-verifier@<version>
        model: <model id>
        date: <YYYY-MM-DD>
        verdict: no_errors_found | errors_found | cannot_assess
        scope: note | section:<exact heading text>

Entries are never edited or removed; a re-verification appends a new entry and
the LATEST entry for a given scope governs. The frontmatter edit is line-based
(no YAML library): only the `verifications:` block changes -- every other
frontmatter line stays byte-for-byte identical (line endings preserved) unless
`--flag-human-review` is passed, which additionally sets `needs_human_review:
true` (the one other line it may touch; hypothesis `status` is never touched).

The contract has no findings field, so the findings (location + why + severity)
and the sha256 of the exact packet the verifier received go in an append-only
body section `## Verificación independiente` (one dated entry per run), written
when `--report` is given.

Subcommands:
    append  --note N --verifier V --model M --verdict X --scope S [--date D]
            [--report agent_output.txt] [--packet-sha256 H]
            [--flag-human-review]
    latest  --note N [--scope note]    governing entry as JSON, or null
    list    --note N                   all entries as a JSON list
    gate    --note N                   exit 0 = clear; exit 3 = blocked (latest
            `scope: note` verdict is anything but no_errors_found AND the note
            still has needs_human_review: true). Prints a JSON explanation.

`append` refuses a note marked `send: never` (A3): such a note is never sent
to a verifier, so it can carry no verification record.

Standard library only. Exit codes: 0 ok, 2 invalid input, 3 gate blocked.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "security"))
from send_guard import is_flagged  # noqa: E402  (A3's single definition of the flag)

__version__ = "1.1.0"

VERDICTS = ("no_errors_found", "errors_found", "cannot_assess")
KEYS = ("verifier", "model", "date", "verdict", "scope")
SEVERITIES = ("crítico", "importante", "menor")
VERIFIER_RE = re.compile(r"^kairo/fresh-verifier@\S+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SECTION_TITLE = "Verificación independiente"


class InputError(ValueError):
    pass


# --------------------------------------------------------------------------
# File / frontmatter handling (line-based, newline-preserving)
# --------------------------------------------------------------------------

def read_raw(path: str) -> str:
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def write_raw(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def split_lines(text: str) -> tuple[list[str], str]:
    """Lines WITH their terminators, and the file's newline style."""
    nl = "\r\n" if "\r\n" in text else "\n"
    return text.splitlines(keepends=True), nl


def frontmatter_bounds(lines: list[str]) -> tuple[int, int]:
    """(open_idx, close_idx) of the `---` delimiters."""
    if not lines or lines[0].lstrip("﻿").strip() != "---":
        raise InputError("note has no YAML frontmatter")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return 0, i
    raise InputError("frontmatter is not closed with ---")


KEY_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:(.*)$")


def find_key(lines: list[str], lo: int, hi: int, key: str) -> int | None:
    for i in range(lo, hi):
        m = KEY_LINE_RE.match(lines[i].rstrip("\r\n"))
        if m and m.group(1) == key:
            return i
    return None


def block_end(lines: list[str], start: int, hi: int) -> int:
    """Index one past the last line of the block that starts at `start`
    (continuation = indented lines or `-` items at column 0; trailing blank /
    comment-only lines are not part of the block)."""
    last = start
    for i in range(start + 1, hi):
        s = lines[i].rstrip("\r\n")
        if not s.strip() or s.lstrip().startswith("#"):
            continue
        if s.startswith((" ", "\t", "-")):
            last = i
            continue
        break
    return last + 1


def strip_comment(val: str) -> tuple[str, str]:
    """Split a scalar from a trailing ` # comment` (outside quotes)."""
    in_q = None
    for i, ch in enumerate(val):
        if in_q:
            if ch == in_q:
                in_q = None
        elif ch in "\"'":
            in_q = ch
        elif ch == "#" and (i == 0 or val[i - 1] in " \t"):
            return val[:i].rstrip(), val[i:]
    return val.rstrip(), ""


def unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] == '"':
        return json.loads(v)
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1].replace("''", "'")
    return v


def yaml_scalar(v: str) -> str:
    """Emit a plain scalar when safe, else a double-quoted one."""
    if (v and not re.search(r": |\s#|^[\s\-?:,\[\]{}#&*!|>'\"%@`]|\s$|:$", v)
            and v.lower() not in ("null", "true", "false", "~", "yes", "no")):
        return v
    return json.dumps(v, ensure_ascii=False)


# --------------------------------------------------------------------------
# Reading entries
# --------------------------------------------------------------------------

def parse_entries(lines: list[str], lo: int, hi: int) -> list[dict]:
    i = find_key(lines, lo, hi, "verifications")
    if i is None:
        return []
    head, _ = strip_comment(KEY_LINE_RE.match(lines[i].rstrip("\r\n")).group(2))
    head = head.strip()
    if head in ("[]", "", "~", "null"):
        pass
    else:
        raise InputError("verifications: must be a block list (or []); "
                         f"found inline value {head!r}")
    end = block_end(lines, i, hi)
    entries: list[dict] = []
    for j in range(i + 1, end):
        s = lines[j].rstrip("\r\n")
        if not s.strip() or s.lstrip().startswith("#"):
            continue
        m = re.match(r"^(\s*)-\s+(.*)$", s)
        body = m.group(2) if m else s.strip()
        if m:
            entries.append({})
        if not entries:
            raise InputError("malformed verifications: block")
        km = re.match(r"^([A-Za-z_]+)\s*:\s?(.*)$", body.strip())
        if not km:
            raise InputError(f"malformed verifications line: {s!r}")
        val, _ = strip_comment(km.group(2))
        entries[-1][km.group(1)] = unquote(val)
    return entries


def load(path: str) -> tuple[str, list[str], str, int, int, list[dict]]:
    text = read_raw(path)
    lines, nl = split_lines(text)
    lo, hi = frontmatter_bounds(lines)
    return text, lines, nl, lo, hi, parse_entries(lines, lo + 1, hi)


def body_headings(lines: list[str], hi: int) -> list[str]:
    out, fence = [], False
    for s in lines[hi + 1:]:
        s = s.rstrip("\r\n")
        if s.lstrip().startswith("```"):
            fence = not fence
        if not fence and s.startswith("## "):
            out.append(s[3:].strip())
    return out


def latest(entries: list[dict], scope: str) -> dict | None:
    for e in reversed(entries):
        if e.get("scope") == scope:
            return e
    return None


def fm_value(lines: list[str], lo: int, hi: int, key: str) -> str | None:
    i = find_key(lines, lo, hi, key)
    if i is None:
        return None
    val, _ = strip_comment(KEY_LINE_RE.match(lines[i].rstrip("\r\n")).group(2))
    return unquote(val)


# --------------------------------------------------------------------------
# Agent report parsing (findings)
# --------------------------------------------------------------------------

def parse_report(path: str) -> dict:
    """Extract the fresh-verifier's ```json block and validate it."""
    text = read_raw(path)
    blocks = re.findall(r"```json\s*\n(.*?)\n```", text, flags=re.S)
    if not blocks:
        raise InputError("report has no ```json block")
    try:
        rep = json.loads(blocks[-1])
    except json.JSONDecodeError as exc:
        raise InputError(f"report JSON invalid: {exc}") from exc
    if rep.get("verdict") not in VERDICTS:
        raise InputError(f"report verdict invalid: {rep.get('verdict')!r}")
    findings = rep.get("findings") or []
    if not isinstance(findings, list):
        raise InputError("report findings must be a list")
    for f in findings:
        if f.get("severity") not in SEVERITIES:
            raise InputError(f"finding severity must be one of {SEVERITIES}: {f!r}")
        if not f.get("location") or not f.get("why"):
            raise InputError(f"finding needs location and why: {f!r}")
    if rep["verdict"] == "errors_found" and not findings:
        raise InputError("errors_found with no findings")
    if rep["verdict"] == "cannot_assess" and not rep.get("cannot_assess_reason"):
        raise InputError("cannot_assess with no cannot_assess_reason")
    return rep


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

def append(path: str, verifier: str, model: str, verdict: str, scope: str,
           date: str | None, report: str | None = None,
           packet_sha: str | None = None, flag_review: bool = False) -> dict:
    if verdict not in VERDICTS:
        raise InputError(f"verdict must be one of {VERDICTS}, got {verdict!r}")
    if not VERIFIER_RE.match(verifier):
        raise InputError("verifier must look like kairo/fresh-verifier@<version>")
    if not model.strip() or "\n" in model:
        raise InputError("model id must be a non-empty single line")
    date = date or _dt.date.today().isoformat()
    if not DATE_RE.match(date):
        raise InputError("date must be YYYY-MM-DD")
    if is_flagged(Path(path)):
        raise InputError("note is marked send: never; it is never verified, so no "
                         "verifications entry is written")
    text, lines, nl, lo, hi, _ = load(path)
    if scope != "note":
        if not scope.startswith("section:") or not scope[len("section:"):]:
            raise InputError("scope must be 'note' or 'section:<heading>'")
        heading = scope[len("section:"):]
        if heading not in body_headings(lines, hi):
            raise InputError(f"section '## {heading}' does not exist verbatim "
                             "in the note")
        if any(heading.startswith(n) for n in ("Revisión del ciclo", "Hipótesis rival descartada",
                                                "Lección", "Verificación independiente")):
            raise InputError(f"section '## {heading}' is never verified on its own "
                             "(it carries reasoning / critiques / prior verdicts)")
    rep = parse_report(report) if report else None
    if rep and rep["verdict"] != verdict:
        raise InputError(f"--verdict {verdict} disagrees with the report's "
                         f"verdict {rep['verdict']}")

    entry = {"verifier": verifier, "model": model, "date": date,
             "verdict": verdict, "scope": scope}

    i = find_key(lines, lo + 1, hi, "verifications")
    if i is None:
        indent = "  "
        new = [f"verifications:{nl}"] + _entry_lines(entry, indent, nl)
        insert_at = hi
        lines[insert_at:insert_at] = new
    else:
        parse_entries(lines, lo + 1, hi)  # validates shape
        m = KEY_LINE_RE.match(lines[i].rstrip("\r\n"))
        head, comment = strip_comment(m.group(2))
        end = block_end(lines, i, hi)
        indent = "  "
        for j in range(i + 1, end):
            im = re.match(r"^(\s*)-\s", lines[j])
            if im:
                indent = im.group(1)
                break
        if head.strip() in ("[]", "~", "null"):
            lines[i] = _replace_value(lines[i], "", nl)
        lines[end:end] = _entry_lines(entry, indent, nl)
    if flag_review:
        _, hi2 = frontmatter_bounds(lines)
        k = find_key(lines, 1, hi2, "needs_human_review")
        if k is None:
            lines.insert(hi2, f"needs_human_review: true{nl}")
        else:
            lines[k] = _replace_value(lines[k], "true", nl)
    if rep is not None:
        lines = _append_body_entry(lines, nl, entry, rep, packet_sha)
    write_raw(path, "".join(lines))
    return entry


def _replace_value(line: str, value: str, nl: str) -> str:
    """Swap the value of a `key: value  # comment` line, keeping the key and
    the comment (and the spacing before it) exactly as they were."""
    raw = line.rstrip("\r\n")
    key, rest = raw.split(":", 1)
    val, comment = strip_comment(rest)
    gap = rest[len(val):len(rest) - len(comment)] if comment else ""
    if not value:
        return f"{key}:{gap}{comment}{nl}"
    return f"{key}: {value}{gap}{comment}{nl}"


def _entry_lines(e: dict, indent: str, nl: str) -> list[str]:
    pad = indent + "  "
    out = [f"{indent}- verifier: {yaml_scalar(e['verifier'])}{nl}"]
    for k in KEYS[1:]:
        out.append(f"{pad}{k}: {yaml_scalar(e[k])}{nl}")
    return out


def _append_body_entry(lines: list[str], nl: str, entry: dict, rep: dict,
                       packet_sha: str | None) -> list[str]:
    block = [f"### {entry['date']} — {entry['verifier']} ({entry['scope']})", "",
             f"- modelo: {entry['model']}",
             f"- veredicto: {entry['verdict']}",
             f"- alcance: {entry['scope']}",
             f"- paquete sha256: {packet_sha or 'no registrado'}"]
    findings = rep.get("findings") or []
    if findings:
        block.append("- hallazgos:")
        for f in findings:
            block.append(f"  - **{f['severity']}** — {f['location']}: "
                         f"{' '.join(str(f['why']).split())}")
    else:
        block.append("- hallazgos: ninguno")
    if rep.get("cannot_assess_reason"):
        block.append(f"- no evaluable: {' '.join(str(rep['cannot_assess_reason']).split())}")
    _, hi = frontmatter_bounds(lines)
    heads = body_headings(lines, hi)
    text_lines = [ln.rstrip("\r\n") for ln in lines]
    if SECTION_TITLE not in heads:
        while text_lines and not text_lines[-1].strip():
            text_lines.pop()
        text_lines += ["", f"## {SECTION_TITLE}", "",
                       "Append-only: una entrada por corrida de "
                       "kairo/fresh-verifier. Registra lo que encontró el "
                       "verificador independiente; nunca es un veredicto sobre "
                       "la verdad de la hipótesis.", ""] + block
    else:
        # insert at the end of that section (before the next ## heading)
        start = next(k for k, s in enumerate(text_lines)
                     if k > hi and s == f"## {SECTION_TITLE}")
        end = len(text_lines)
        for k in range(start + 1, len(text_lines)):
            if text_lines[k].startswith("## "):
                end = k
                break
        seg = text_lines[:end]
        while seg and not seg[-1].strip():
            seg.pop()
        rest = text_lines[end:]
        text_lines = seg + [""] + block + (([""] + rest) if rest else [])
    return [s + nl for s in text_lines]


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(description="Append-only verifications: records "
                                "(v3 contract §1b).")
    p.add_argument("--version", action="version",
                   version=f"verifications.py {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("append", help="append one verification entry")
    a.add_argument("--note", required=True)
    a.add_argument("--verifier", required=True)
    a.add_argument("--model", required=True)
    a.add_argument("--verdict", required=True)
    a.add_argument("--scope", required=True)
    a.add_argument("--date")
    a.add_argument("--report", help="fresh-verifier output; its findings go to "
                   "## Verificación independiente")
    a.add_argument("--packet-sha256", dest="packet_sha")
    a.add_argument("--flag-human-review", action="store_true",
                   help="also set needs_human_review: true")
    for name in ("latest", "list", "gate"):
        s = sub.add_parser(name)
        s.add_argument("--note", required=True)
        if name == "latest":
            s.add_argument("--scope", default="note")
    args = p.parse_args(argv)

    try:
        if args.cmd == "append":
            e = append(args.note, args.verifier, args.model, args.verdict,
                       args.scope, args.date, args.report, args.packet_sha,
                       args.flag_human_review)
            print(json.dumps(e, ensure_ascii=False))
            return 0
        _, lines, _, lo, hi, entries = load(args.note)
        if args.cmd == "list":
            print(json.dumps(entries, ensure_ascii=False))
            return 0
        if args.cmd == "latest":
            print(json.dumps(latest(entries, args.scope), ensure_ascii=False))
            return 0
        # gate
        gov = latest(entries, "note")
        nhr = (fm_value(lines, lo + 1, hi, "needs_human_review") or "").lower() == "true"
        blocked = bool(gov and gov.get("verdict") != "no_errors_found"
                       and nhr)
        print(json.dumps({"blocked": blocked, "governing": gov,
                          "needs_human_review": nhr}, ensure_ascii=False))
        return 3 if blocked else 0
    except (InputError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
