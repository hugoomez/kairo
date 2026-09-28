#!/usr/bin/env python3
"""Per-project action log (the lab notebook's record) — append-only, hash-chained.

Every action Kairo or the researcher takes on a project gets one line in
``Projects/<slug>/Bitacora/acciones.jsonl``: a job started or finished, a run
approved or rejected (with the reason, when one was given), an automation
switch changed, a commit made from a Claude Code session. The daily page
``Bitacora/YYYY-MM-DD.md`` is rendered from it; it is also what "where was I"
and cost estimates read.

Same chain rules as ``scripts/traces/trace_index.py`` (it reuses that module's
serialisation and hashing): one canonical JSON object per line, ``seq`` = line
number, ``prev_sha256`` = sha256 of the previous line, ``entry_sha256`` over the
entry without that key, and ``verify --git`` checks every committed version is
a prefix of the next. Nothing rewrites a line; a correction is a new entry.

A human decision without a stated reason is recorded with ``reason: null`` —
never with an invented one.

Subcommands::

    action_log.py append --log <acciones.jsonl> --entry <file.json | ->
    action_log.py recent --log <acciones.jsonl> [--n 5] [--json]
    action_log.py verify --log <acciones.jsonl> [--git] [--json]
    action_log.py render --log <acciones.jsonl> --date YYYY-MM-DD --out <page.md>

Exit codes: 0 ok · 3 refused (invalid entry, broken chain) · 1 error.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "traces"))
from trace_index import (  # noqa: E402  (sibling script, not a package)
    GENESIS,
    TraceError,
    canonical,
    entry_hash,
    git_prefix_problems,
    read_lines,
    sha256_text,
)

__version__ = "1.0.0"
SCHEMA = "kairo/action@1"
CHAIN_KEYS = ("schema", "seq", "prev_sha256", "entry_sha256")

ACTORS = ("human", "agent", "system")
MODES = ("ask", "auto", "manual", "n/a")
EVENTS = (
    "started", "completed", "failed", "cancelled", "awaiting_input",
    "approved", "rejected", "toggled", "commit", "note", "exported",
)
DECISION_EVENTS = ("approved", "rejected", "toggled", "note", "exported")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ACTION = re.compile(r"^[a-z][a-z0-9_]*$")

NOTES_HEADING = "## Notas"
NOTES_PLACEHOLDER = "<!-- Tus notas del día. Kairo nunca reescribe esta sección. -->"


class ActionLogError(Exception):
    """Invalid entry or broken log."""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_date(recorded_at: str) -> str:
    """The researcher's calendar day for a UTC timestamp (this machine's zone)."""
    dt = datetime.strptime(recorded_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return dt.astimezone().date().isoformat()


def local_time(recorded_at: str) -> str:
    dt = datetime.strptime(recorded_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%H:%M")


# ---------------------------------------------------------------------------
# Validation and chain
# ---------------------------------------------------------------------------

def _opt(e: dict, key: str, kind, what: str) -> None:
    v = e.get(key)
    if v is not None and not isinstance(v, kind):
        raise ActionLogError(f"{key} must be {what} or null")


def validate(e: dict) -> None:
    if e.get("actor") not in ACTORS:
        raise ActionLogError(f"actor must be one of {ACTORS}")
    if not isinstance(e.get("by"), str) or not e["by"].strip():
        raise ActionLogError("by must name who acted (a person, an agent id, or 'kairo-backend')")
    if not isinstance(e.get("action"), str) or not _ACTION.match(e["action"]):
        raise ActionLogError("action must be a snake_case action id, e.g. hypothesis_generation")
    if e.get("event") not in EVENTS:
        raise ActionLogError(f"event must be one of {EVENTS}")
    if e.get("mode", "n/a") not in MODES:
        raise ActionLogError(f"mode must be one of {MODES}")
    if not isinstance(e.get("summary"), str) or not e["summary"].strip():
        raise ActionLogError("summary is required (one line saying what happened)")
    if not isinstance(e.get("recorded_at"), str) or not _ISO.match(e["recorded_at"]):
        raise ActionLogError("recorded_at must be UTC YYYY-MM-DDTHH:MM:SSZ")
    if not isinstance(e.get("date"), str) or not _DATE.match(e["date"]):
        raise ActionLogError("date must be YYYY-MM-DD")
    _opt(e, "reason", str, "a string")
    _opt(e, "job_id", str, "a string")
    _opt(e, "commit", str, "a string")
    _opt(e, "trace", str, "a vault-relative path")
    if isinstance(e.get("trace"), str) and (e["trace"].startswith(("/", "\\")) or ":" in e["trace"] or ".." in e["trace"]):
        raise ActionLogError("trace must be a vault-relative path")
    for key in ("cost_usd", "duration_s"):
        v = e.get(key)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0):
            raise ActionLogError(f"{key} must be a non-negative number or null")
    v = e.get("turns")
    if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 0):
        raise ActionLogError("turns must be a non-negative integer or null")
    refs = e.get("refs", [])
    if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
        raise ActionLogError("refs must be a list of note ids")
    if e.get("event") in DECISION_EVENTS and e.get("actor") == "human" and "reason" not in e:
        raise ActionLogError("a human decision records `reason` explicitly (null when none was given)")


def chain_problems(lines: list[str]) -> list[str]:
    problems: list[str] = []
    prev = GENESIS
    for i, line in enumerate(lines):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(f"line {i}: not JSON ({exc.msg})")
            prev = sha256_text(line)
            continue
        if not isinstance(obj, dict):
            problems.append(f"line {i}: not a JSON object")
        else:
            if obj.get("schema") != SCHEMA:
                problems.append(f"line {i}: schema is not {SCHEMA}")
            if obj.get("seq") != i:
                problems.append(f"line {i}: seq {obj.get('seq')!r} != {i} (line deleted, inserted or reordered)")
            if obj.get("prev_sha256") != prev:
                problems.append(f"line {i}: prev_sha256 does not match line {i - 1} (an earlier line was edited)"
                                if i else "line 0: prev_sha256 is not the genesis value")
            if obj.get("entry_sha256") != entry_hash(obj):
                problems.append(f"line {i}: entry_sha256 does not match its content (line edited)")
            if canonical(obj) != line:
                problems.append(f"line {i}: not in canonical form (hand-edited)")
            try:
                validate(obj)
            except ActionLogError as exc:
                problems.append(f"line {i}: {exc}")
        prev = sha256_text(line)
    return problems


def _lines(path: Path) -> list[str]:
    try:
        return read_lines(path)
    except TraceError as exc:
        raise ActionLogError(str(exc)) from exc


def load(path: Path) -> list[dict]:
    lines = _lines(path)
    problems = chain_problems(lines)
    if problems:
        raise ActionLogError("chain broken: " + "; ".join(problems[:5]))
    return [json.loads(line) for line in lines]


def append(path: Path, entry: dict) -> dict:
    """Validate and append one entry; returns it with its chain fields set."""
    lines = _lines(path)
    problems = chain_problems(lines)
    if problems:
        raise ActionLogError("refusing to append to a broken log: " + "; ".join(problems[:3]))
    e = {k: v for k, v in entry.items() if k not in CHAIN_KEYS}
    e.setdefault("recorded_at", utc_now())
    e.setdefault("date", local_date(e["recorded_at"]))
    e.setdefault("mode", "n/a")
    e.setdefault("refs", [])
    validate(e)
    e["schema"] = SCHEMA
    e["seq"] = len(lines)
    e["prev_sha256"] = sha256_text(lines[-1]) if lines else GENESIS
    e["entry_sha256"] = entry_hash(e)
    line = canonical(e)
    path.parent.mkdir(parents=True, exist_ok=True)
    nl = "\r\n" if path.exists() and b"\r\n" in path.read_bytes() else "\n"
    with open(path, "a", encoding="utf-8", newline="") as fh:
        fh.write(line + nl)
    return e


# ---------------------------------------------------------------------------
# Daily page
# ---------------------------------------------------------------------------

def _facts(e: dict) -> str:
    bits = []
    if e.get("commit"):
        bits.append(f"commit `{e['commit'][:10]}`")
    if e.get("turns") is not None:
        bits.append(f"{e['turns']} turnos")
    if e.get("duration_s") is not None:
        bits.append(f"{e['duration_s'] / 60:.1f} min")
    if e.get("cost_usd") is not None:
        bits.append(f"coste equivalente ${e['cost_usd']:.2f}")
    if e.get("trace"):
        bits.append(f"traza `{e['trace']}`")
    return f" ({', '.join(bits)})" if bits else ""


def _line(e: dict) -> str:
    refs = f" [{', '.join(e['refs'])}]" if e.get("refs") else ""
    mode = f" · {e['mode']}" if e.get("mode") not in (None, "n/a") else ""
    return (f"- {local_time(e['recorded_at'])} · {e['by']} · `{e['action']}` {e['event']}{mode}"
            f" — {e['summary']}{refs}{_facts(e)}")


def existing_notes(page: Path) -> str:
    """The researcher's `## Notas` section from an existing page, verbatim."""
    if not page.exists():
        return NOTES_PLACEHOLDER
    text = page.read_text(encoding="utf-8")
    idx = text.find(f"\n{NOTES_HEADING}\n")
    if idx == -1:
        return NOTES_PLACEHOLDER
    body = text[idx + len(NOTES_HEADING) + 2:]
    return body.strip("\n") or NOTES_PLACEHOLDER


def render(entries: list[dict], day: str, page: Path) -> str:
    todays = [e for e in entries if e.get("date") == day]
    done = [e for e in todays if not (e["actor"] == "human" and e["event"] in DECISION_EVENTS)]
    decided = [e for e in todays if e["actor"] == "human" and e["event"] in DECISION_EVENTS]
    seqs = f"{todays[0]['seq']}–{todays[-1]['seq']}" if todays else "ninguna"
    out = [
        "---",
        f"date: {day}",
        "generated_from: acciones.jsonl",
        f"entries: \"{seqs}\"",
        "escrito_por: kairo (derivado del log; solo la sección Notas es tuya)",
        "---",
        "",
        f"# Bitácora — {day}",
        "",
        "## Hecho",
        "",
    ]
    out += [_line(e) for e in done] or ["- (sin acciones registradas)"]
    out += ["", "## Decidido (y por qué)", ""]
    for e in decided:
        why = e.get("reason")
        out.append(_line(e) + (f"\n  - **Por qué:** {why}" if why else "\n  - _(sin motivo registrado)_"))
    if not decided:
        out.append("- (sin decisiones registradas)")
    out += ["", NOTES_HEADING, "", existing_notes(page), ""]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _read_entry(src: str) -> dict:
    raw = sys.stdin.read() if src == "-" else Path(src).read_text(encoding="utf-8")
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ActionLogError("entry must be a JSON object")
    return obj


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("append")
    p.add_argument("--log", required=True, type=Path)
    p.add_argument("--entry", required=True)
    p = sub.add_parser("recent")
    p.add_argument("--log", required=True, type=Path)
    p.add_argument("--n", type=int, default=5)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("verify")
    p.add_argument("--log", required=True, type=Path)
    p.add_argument("--git", action="store_true")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("render")
    p.add_argument("--log", required=True, type=Path)
    p.add_argument("--date", default=None, help="YYYY-MM-DD (default: today, local time)")
    p.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)

    # Windows consoles default to a legacy code page; the log is UTF-8.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        if args.cmd == "append":
            written = append(args.log, _read_entry(args.entry))
            print(json.dumps(written, ensure_ascii=False))
            return 0
        if args.cmd == "recent":
            entries = load(args.log)[-args.n:][::-1] if args.n > 0 else []
            if args.json:
                print(json.dumps(entries, ensure_ascii=False))
            else:
                for e in entries:
                    print(_line(e))
            return 0
        if args.cmd == "verify":
            problems = chain_problems(_lines(args.log))
            if args.git and not problems:
                problems += git_prefix_problems(args.log)
            if args.json:
                print(json.dumps({"ok": not problems, "problems": problems}, ensure_ascii=False))
            else:
                print("ok" if not problems else "\n".join(problems))
            return 0 if not problems else 3
        if args.cmd == "render":
            day = args.date or date.today().isoformat()
            if not _DATE.match(day):
                raise ActionLogError("--date must be YYYY-MM-DD")
            text = render(load(args.log), day, args.out)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8", newline="\n")
            print(str(args.out))
            return 0
    except ActionLogError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
