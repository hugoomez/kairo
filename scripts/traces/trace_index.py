#!/usr/bin/env python3
"""Full-trace index of experiment runs — append-only, hash-chained.

Every run attempt of an experiment gets entries in
``Projects/<slug>/Experimentos/trazas/index.jsonl``: completed, aborted, crashed,
invalid, retried, never launched, exploratory rungs and pilots alike. The index
is what ``pitfall_audit.py`` compares the analysis against (post-hoc selection:
a run that was executed but left out of the analysis without a preregistered
reason is ``crítico``).

One JSON object per line, UTF-8, keys sorted, no spaces, ``\\n`` line endings
(CRLF from a git ``core.autocrlf`` checkout is accepted and kept: what is hashed
is the line content without its terminator).
Nothing ever rewrites or deletes a line. A correction is a NEW entry
(``event: correction``) that names the entry it corrects by ``seq`` +
``entry_sha256`` and says why; the old entry stays.

Tamper evidence (``verify``):

* ``seq`` equals the line number (0-based) — detects deleted / reordered lines;
* ``prev_sha256`` is the sha256 of the previous line's exact bytes (without its
  newline; 64 zeros for line 0) — detects any edit to any earlier line;
* ``entry_sha256`` is the sha256 of the entry's canonical JSON without that key —
  detects an edit to the last line;
* ``--git``: every committed version of the file must be a byte prefix of every
  later version and of the working copy — detects truncation or a rewrite that
  recomputed the whole chain after a commit.

The chain proves the file was not edited by accident or casually; it does not
stop someone who recomputes every hash and never committed — git is the anchor.

Subcommands::

    trace_index.py start   --index <index.jsonl> --experiment E-XXXX --run-id <id> \\
                           --role confirmatory|exploratory [--rung N] [--attempt N] \\
                           [--retry-of <id>] --seeds 1,2 [--cell <label>] \\
                           [--plan-index N] --config-hash <sha256|unknown> --by <who>
    trace_index.py end     --index <index.jsonl> --run-id <id> \\
                           --outcome completed|aborted|crashed|invalid|not_launched|unknown \\
                           --entered-analysis true|false|unknown \\
                           [--exclusion-reason <text> --exclusion-rule-source <locator>] \\
                           [--artifact <path>[=<sha256>]]... --by <who>
    trace_index.py append  --index <index.jsonl> --entry <file.json | ->   # full entry
    trace_index.py correct --index <index.jsonl> --seq N --reason <text> --by <who> \\
                           --set key=<json value> [--set ...]
    trace_index.py list    --index <index.jsonl> [--experiment E-XXXX] [--json]
    trace_index.py show    --index <index.jsonl> --run-id <id> [--json]
    trace_index.py verify  --index <index.jsonl> [--git] [--json]
    trace_index.py config-hash --file <config.json>

Exit codes: 0 ok · 3 refused (invalid entry, broken chain, bad correction) ·
1 error (unreadable file, bad arguments).

Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

__version__ = "1.0.0"
TOOL = f"kairo/trace_index@{__version__}"
SCHEMA = "kairo/trace@1"
GENESIS = "0" * 64

EVENTS = ("start", "end", "correction")
ROLES = ("confirmatory", "exploratory", "unknown")
OUTCOMES = ("running", "completed", "aborted", "crashed", "invalid", "not_launched", "unknown")
FINAL_OUTCOMES = tuple(o for o in OUTCOMES if o != "running")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_EXP = re.compile(r"^E-\d{4}(-[a-z0-9][a-z0-9-]*)?$")
_RUN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")

# Keys an entry may carry. Anything else is refused (typos must not pass silently).
ALLOWED = {
    "schema", "seq", "prev_sha256", "entry_sha256", "recorded_at", "event",
    "run_id", "experiment", "role", "rung", "attempt", "retry_of", "seeds",
    "cell", "condition", "plan_index", "config_hash", "config_hash_basis",
    "started_at", "ended_at", "started_after", "ended_before", "date",
    "duration_s", "outcome", "entered_analysis", "exclusion_reason",
    "exclusion_rule_source", "artifacts", "provenance", "corrects", "reason",
    "notes", "unknown",
}
CHAIN_KEYS = ("schema", "seq", "prev_sha256", "entry_sha256")


class TraceError(Exception):
    """Refusal: the entry or the index breaks the contract (exit 3)."""


# ---------------------------------------------------------------------------
# Serialisation and hashing
# ---------------------------------------------------------------------------

def canonical(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def entry_hash(entry: dict) -> str:
    return sha256_text(canonical({k: v for k, v in entry.items() if k != "entry_sha256"}))


def config_hash(config: dict) -> str:
    """sha256 of a run configuration's canonical JSON (keys sorted, no spaces)."""
    return sha256_text(canonical(config))


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def read_lines(path: Path) -> list[str]:
    """Raw lines without their '\\n'. A missing file is an empty index."""
    if not path.exists():
        return []
    data = path.read_bytes().decode("utf-8")
    if not data:
        return []
    # git with core.autocrlf may check the file out with CRLF: line CONTENT is what
    # is hashed, so either terminator verifies. A stray lone '\r' is an edit.
    data = data.replace("\r\n", "\n")
    if "\r" in data:
        raise TraceError("the index contains a stray '\\r' (hand-edited?)")
    if not data.endswith("\n"):
        raise TraceError("the index does not end with a newline — last line truncated or hand-edited")
    return data[:-1].split("\n")


def load(path: Path) -> list[dict]:
    """Entries in file order, after a full chain verification (raises TraceError)."""
    lines = read_lines(path)
    problems = chain_problems(lines)
    if problems:
        raise TraceError("chain broken: " + "; ".join(problems[:5]))
    return [json.loads(l) for l in lines]


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
                validate(obj, check_chain_keys=True)
            except TraceError as exc:
                problems.append(f"line {i}: {exc}")
        prev = sha256_text(line)
    return problems


def latest_by_run(entries: list[dict]) -> dict[str, dict]:
    """Current state of each run = its entry with the highest seq."""
    state: dict[str, dict] = {}
    for e in entries:
        state[e["run_id"]] = e
    return state


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _is_time(v, allow_unknown=True) -> bool:
    return v is None or (allow_unknown and v == "unknown") or (isinstance(v, str) and bool(_ISO.match(v)))


def validate(e: dict, check_chain_keys: bool = False) -> None:
    extra = set(e) - ALLOWED
    if extra:
        raise TraceError(f"unknown key(s) {sorted(extra)}")
    if check_chain_keys:
        if e.get("schema") != SCHEMA:
            raise TraceError(f"schema must be {SCHEMA}")
        if not isinstance(e.get("prev_sha256"), str) or not _HEX64.match(e["prev_sha256"]):
            raise TraceError("prev_sha256 must be 64 hex chars")
    ev = e.get("event")
    if ev not in EVENTS:
        raise TraceError(f"event must be one of {EVENTS}")
    if not isinstance(e.get("run_id"), str) or not _RUN.match(e["run_id"]):
        raise TraceError("run_id missing or malformed")
    if not isinstance(e.get("experiment"), str) or not _EXP.match(e["experiment"]):
        raise TraceError("experiment must look like E-XXXX (optionally E-XXXX-suffix)")
    if e.get("role") not in ROLES:
        raise TraceError(f"role must be one of {ROLES}")
    rung = e.get("rung")
    if rung is not None and rung not in (0, 1, 2, 3):
        raise TraceError("rung must be 0-3 or null")
    att = e.get("attempt")
    if att is not None and (not isinstance(att, int) or att < 1):
        raise TraceError("attempt must be an integer >= 1 or null (unknown)")
    seeds = e.get("seeds")
    if not (seeds == "unknown" or (isinstance(seeds, list) and all(isinstance(s, int) for s in seeds))):
        raise TraceError("seeds must be a list of integers or \"unknown\"")
    ch = e.get("config_hash")
    if not (ch == "unknown" or (isinstance(ch, str) and _HEX64.match(ch))):
        raise TraceError("config_hash must be a sha256 hex digest or \"unknown\"")
    for k in ("started_at", "ended_at", "started_after", "ended_before"):
        if not _is_time(e.get(k)):
            raise TraceError(f"{k} must be YYYY-MM-DDTHH:MM:SSZ (UTC), \"unknown\" or null")
    if e.get("date") is not None and not (isinstance(e["date"], str) and _DATE.match(e["date"])):
        raise TraceError("date must be YYYY-MM-DD")
    if not _is_time(e.get("recorded_at"), allow_unknown=False) or e.get("recorded_at") is None:
        raise TraceError("recorded_at must be an ISO UTC timestamp")
    out = e.get("outcome")
    if out not in OUTCOMES:
        raise TraceError(f"outcome must be one of {OUTCOMES}")
    if ev == "start":
        if out != "running":
            raise TraceError("a start entry has outcome: running")
        if e.get("ended_at") not in (None,):
            raise TraceError("a start entry has no ended_at")
        if not isinstance(e.get("started_at"), str) or e["started_at"] == "unknown":
            raise TraceError("a start entry needs started_at (it is written when the run starts)")
    else:
        if ev == "end" and out == "running":
            raise TraceError("an end entry needs a final outcome")
    ea = e.get("entered_analysis")
    if ea not in (True, False, None):
        raise TraceError("entered_analysis must be true, false or null (unknown)")
    prov = e.get("provenance")
    if not isinstance(prov, dict) or not isinstance(prov.get("written_by"), str) or not prov["written_by"].strip():
        raise TraceError("provenance.written_by is required")
    if not isinstance(prov.get("backfilled"), bool):
        raise TraceError("provenance.backfilled must be true or false")
    if prov["backfilled"] and not (isinstance(prov.get("sources"), list) and prov["sources"]):
        raise TraceError("a backfilled entry must list its sources (provenance.sources)")
    if ev == "end" and out in FINAL_OUTCOMES and ea is None and not prov["backfilled"]:
        raise TraceError("an end entry must say whether the run entered the analysis "
                         "(null/unknown is allowed only for backfilled entries)")
    if ea is False and not (isinstance(e.get("exclusion_reason"), str) and e["exclusion_reason"].strip()):
        raise TraceError("entered_analysis: false needs exclusion_reason")
    src = e.get("exclusion_rule_source")
    if src is not None and not (isinstance(src, str) and src.strip()):
        raise TraceError("exclusion_rule_source must be a non-empty locator or null")
    arts = e.get("artifacts")
    if arts is not None:
        if not isinstance(arts, list) or not all(isinstance(a, dict) and isinstance(a.get("path"), str) for a in arts):
            raise TraceError("artifacts must be a list of {path, sha256, archived}")
        for a in arts:
            if a.get("sha256") is not None and not _HEX64.match(str(a["sha256"])):
                raise TraceError("artifact sha256 must be 64 hex chars or null")
    if ev == "correction":
        c = e.get("corrects")
        if not (isinstance(c, dict) and isinstance(c.get("seq"), int) and isinstance(c.get("entry_sha256"), str)):
            raise TraceError("a correction needs corrects: {seq, entry_sha256}")
        if not (isinstance(e.get("reason"), str) and e["reason"].strip()):
            raise TraceError("a correction needs a reason")
    elif e.get("corrects") is not None:
        raise TraceError("only a correction entry carries `corrects`")
    unk = e.get("unknown")
    if unk is not None and not (isinstance(unk, list) and all(isinstance(u, str) for u in unk)):
        raise TraceError("unknown must be a list of field names")


# ---------------------------------------------------------------------------
# Writing (append only)
# ---------------------------------------------------------------------------

def append(path: Path, entry: dict) -> dict:
    """Validate and append one entry; returns it with its chain fields set.
    Refuses (TraceError) when the existing index does not verify."""
    lines = read_lines(path)
    problems = chain_problems(lines)
    if problems:
        raise TraceError("refusing to append to a broken index: " + "; ".join(problems[:3]))
    entries = [json.loads(l) for l in lines]
    e = {k: v for k, v in entry.items() if k not in CHAIN_KEYS}
    e.setdefault("recorded_at", utc_now())
    validate(e)
    state = latest_by_run(entries)
    prior = state.get(e["run_id"])
    if e["event"] == "start" and prior is not None and prior["experiment"] != e["experiment"]:
        raise TraceError(f"run_id {e['run_id']} already belongs to {prior['experiment']}")
    if e["event"] == "start" and prior is not None:
        raise TraceError(f"run_id {e['run_id']} already has entries — a retry is a new run_id "
                         "with retry_of pointing at the failed one")
    if e["event"] == "end" and prior is None and not e["provenance"]["backfilled"] \
            and e["outcome"] != "not_launched":
        raise TraceError(f"no start entry for run {e['run_id']} — write the start entry when the run starts")
    if e["event"] == "correction":
        c = e["corrects"]
        if not 0 <= c["seq"] < len(entries):
            raise TraceError(f"corrects.seq {c['seq']} does not exist")
        target = entries[c["seq"]]
        if target["entry_sha256"] != c["entry_sha256"]:
            raise TraceError("corrects.entry_sha256 does not match the entry at that seq")
        if target["run_id"] != e["run_id"]:
            raise TraceError("a correction must keep the run_id of the entry it corrects")
    if e.get("retry_of") is not None and e["retry_of"] not in state:
        raise TraceError(f"retry_of {e['retry_of']} is not in the index — trace the failed attempt first")
    e["schema"] = SCHEMA
    e["seq"] = len(entries)
    e["prev_sha256"] = sha256_text(lines[-1]) if lines else GENESIS
    e["entry_sha256"] = entry_hash(e)
    line = canonical(e)
    path.parent.mkdir(parents=True, exist_ok=True)
    nl = "\r\n" if path.exists() and b"\r\n" in path.read_bytes() else "\n"   # keep the checkout's style
    with open(path, "a", encoding="utf-8", newline="") as fh:
        fh.write(line + nl)
    return e


def start_entry(experiment: str, run_id: str, role: str, by: str, *, rung=None, attempt=1,
                retry_of=None, seeds="unknown", cell=None, plan_index=None,
                config_hash_value="unknown", started_at=None, condition=None, notes=None) -> dict:
    e = {"event": "start", "run_id": run_id, "experiment": experiment, "role": role,
         "rung": rung, "attempt": attempt, "retry_of": retry_of, "seeds": seeds,
         "cell": cell, "plan_index": plan_index, "config_hash": config_hash_value,
         "started_at": started_at or utc_now(), "ended_at": None, "outcome": "running",
         "entered_analysis": None,
         "provenance": {"written_by": by, "backfilled": False}}
    if condition is not None:
        e["condition"] = condition
    if notes:
        e["notes"] = notes
    return e


def end_entry(entries: list[dict], run_id: str, outcome: str, entered, by: str, *,
              exclusion_reason=None, exclusion_rule_source=None, artifacts=None,
              ended_at=None, notes=None) -> dict:
    prior = latest_by_run(entries).get(run_id)
    if prior is None:
        raise TraceError(f"no start entry for run {run_id}")
    if prior["event"] != "start":
        raise TraceError(f"run {run_id} already ended (seq {prior['seq']}); fix it with `correct`")
    e = {k: v for k, v in prior.items() if k not in CHAIN_KEYS + ("recorded_at", "notes")}
    e.update({"event": "end", "outcome": outcome, "entered_analysis": entered,
              "ended_at": ended_at or utc_now(),
              "provenance": {"written_by": by, "backfilled": False}})
    if exclusion_reason is not None:
        e["exclusion_reason"] = exclusion_reason
    if exclusion_rule_source is not None:
        e["exclusion_rule_source"] = exclusion_rule_source
    if artifacts:
        e["artifacts"] = artifacts
    if notes:
        e["notes"] = notes
    return e


def correction_entry(entries: list[dict], seq: int, reason: str, by: str, changes: dict) -> dict:
    if not 0 <= seq < len(entries):
        raise TraceError(f"seq {seq} does not exist")
    target = entries[seq]
    bad = set(changes) & (set(CHAIN_KEYS) | {"run_id", "event", "corrects", "provenance", "recorded_at"})
    if bad:
        raise TraceError(f"a correction cannot set {sorted(bad)}")
    e = {k: v for k, v in target.items() if k not in CHAIN_KEYS + ("recorded_at",)}
    e.update(changes)
    e["event"] = "correction"
    e["corrects"] = {"seq": seq, "entry_sha256": target["entry_sha256"]}
    e["reason"] = reason
    e["provenance"] = {"written_by": by, "backfilled": False}
    return e


# ---------------------------------------------------------------------------
# git anchor
# ---------------------------------------------------------------------------

def git_prefix_problems(path: Path) -> list[str]:
    """Every committed version must be a prefix of every later one and of the file."""
    path = path.resolve()
    def git(*args):
        return subprocess.run(["git", *args], cwd=path.parent, capture_output=True)
    top = git("rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return ["not inside a git repository — the append-only property cannot be anchored"]
    root = Path(top.stdout.decode().strip()).resolve()
    rel = path.relative_to(root).as_posix()
    log = subprocess.run(["git", "log", "--format=%H", "--", rel], cwd=root, capture_output=True)
    shas = list(reversed(log.stdout.decode().split()))
    versions = []
    for sha in shas:
        shown = subprocess.run(["git", "show", f"{sha}:{rel}"], cwd=root, capture_output=True)
        if shown.returncode == 0:
            versions.append((sha[:10], shown.stdout))
    current = path.read_bytes() if path.exists() else b""
    versions.append(("working copy", current))
    versions = [(s, b.replace(b"\r\n", b"\n")) for s, b in versions]   # autocrlf-neutral
    problems = []
    for (sa, a), (sb, b) in zip(versions, versions[1:]):
        if not b.startswith(a):
            problems.append(f"{sb} is not an append-only extension of {sa} (lines rewritten or removed)")
    return problems


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _bool(s: str):
    s = s.strip().lower()
    if s in ("true", "yes", "1"):
        return True
    if s in ("false", "no", "0"):
        return False
    if s in ("unknown", "null", "none"):
        return None
    raise argparse.ArgumentTypeError("expected true | false | unknown")


def _seeds(s: str):
    if s.strip().lower() == "unknown":
        return "unknown"
    return [int(x) for x in s.split(",") if x.strip()]


def _artifact(s: str) -> dict:
    path, _, sha = s.partition("=")
    return {"path": path, "sha256": sha or None, "archived": bool(sha)}


def _summary(e: dict) -> str:
    ea = {True: "in-analysis", False: "EXCLUDED", None: "analysis?"}[e.get("entered_analysis")]
    extra = f" ({e['exclusion_reason']})" if e.get("entered_analysis") is False else ""
    bf = " [backfilled]" if e["provenance"].get("backfilled") else ""
    return (f"#{e['seq']:<4} {e['experiment']:<14} {e['run_id']:<22} {e['event']:<10} "
            f"{e['outcome']:<12} {ea}{extra}{bf}")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Append-only, hash-chained index of every experiment run.")
    ap.add_argument("--version", action="version", version=TOOL)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start")
    s.add_argument("--index", type=Path, required=True)
    s.add_argument("--experiment", required=True)
    s.add_argument("--run-id", required=True)
    s.add_argument("--role", required=True, choices=ROLES)
    s.add_argument("--rung", type=int)
    s.add_argument("--attempt", type=int, default=1)
    s.add_argument("--retry-of")
    s.add_argument("--seeds", type=_seeds, default="unknown")
    s.add_argument("--cell")
    s.add_argument("--plan-index", type=int)
    s.add_argument("--config-hash", default="unknown")
    s.add_argument("--started-at")
    s.add_argument("--notes")
    s.add_argument("--by", required=True)

    en = sub.add_parser("end")
    en.add_argument("--index", type=Path, required=True)
    en.add_argument("--run-id", required=True)
    en.add_argument("--outcome", required=True, choices=FINAL_OUTCOMES)
    en.add_argument("--entered-analysis", type=_bool, required=True)
    en.add_argument("--exclusion-reason")
    en.add_argument("--exclusion-rule-source")
    en.add_argument("--artifact", type=_artifact, action="append")
    en.add_argument("--ended-at")
    en.add_argument("--notes")
    en.add_argument("--by", required=True)

    a = sub.add_parser("append")
    a.add_argument("--index", type=Path, required=True)
    a.add_argument("--entry", required=True, help="JSON file with one entry, or - for stdin")

    c = sub.add_parser("correct")
    c.add_argument("--index", type=Path, required=True)
    c.add_argument("--seq", type=int, required=True)
    c.add_argument("--reason", required=True)
    c.add_argument("--by", required=True)
    c.add_argument("--set", action="append", default=[], metavar="KEY=JSON")

    for name in ("list", "show", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--index", type=Path, required=True)
        p.add_argument("--json", action="store_true")
        if name == "list":
            p.add_argument("--experiment")
        if name == "show":
            p.add_argument("--run-id", required=True)
        if name == "verify":
            p.add_argument("--git", action="store_true")

    h = sub.add_parser("config-hash")
    h.add_argument("--file", type=Path, required=True)

    args = ap.parse_args(argv)
    try:
        if args.cmd == "config-hash":
            print(config_hash(json.loads(args.file.read_text(encoding="utf-8"))))
            return 0
        if args.cmd == "verify":
            lines = read_lines(args.index)
            problems = chain_problems(lines)
            if args.git:
                problems += [f"git: {p}" for p in git_prefix_problems(args.index)]
            res = {"tool": TOOL, "index": args.index.as_posix(), "entries": len(lines),
                   "ok": not problems, "problems": problems}
            if args.json:
                print(json.dumps(res, ensure_ascii=False, indent=2))
            else:
                print(f"{'OK' if not problems else 'BROKEN'} {args.index} ({len(lines)} entries)")
                for p in problems:
                    print(f"  {p}")
            return 0 if not problems else 3
        if args.cmd in ("list", "show"):
            entries = load(args.index)
            if args.cmd == "list":
                sel = [e for e in entries if not args.experiment or e["experiment"] == args.experiment]
            else:
                sel = [e for e in entries if e["run_id"] == args.run_id]
                if not sel:
                    print(f"no entries for run {args.run_id}", file=sys.stderr)
                    return 3
            if args.json:
                print(json.dumps(sel, ensure_ascii=False, indent=2))
            else:
                for e in sel:
                    print(_summary(e))
            return 0
        if args.cmd == "start":
            e = start_entry(args.experiment, args.run_id, args.role, args.by, rung=args.rung,
                            attempt=args.attempt, retry_of=args.retry_of, seeds=args.seeds,
                            cell=args.cell, plan_index=args.plan_index,
                            config_hash_value=args.config_hash, started_at=args.started_at,
                            notes=args.notes)
        elif args.cmd == "end":
            e = end_entry(load(args.index), args.run_id, args.outcome, args.entered_analysis, args.by,
                          exclusion_reason=args.exclusion_reason,
                          exclusion_rule_source=args.exclusion_rule_source,
                          artifacts=args.artifact, ended_at=args.ended_at, notes=args.notes)
        elif args.cmd == "correct":
            changes = {}
            for kv in args.set:
                k, _, v = kv.partition("=")
                changes[k] = json.loads(v)
            e = correction_entry(load(args.index), args.seq, args.reason, args.by, changes)
        else:
            raw = sys.stdin.read() if args.entry == "-" else Path(args.entry).read_text(encoding="utf-8")
            e = json.loads(raw)
            if not isinstance(e, dict):
                raise TraceError("--entry must hold one JSON object")
        written = append(args.index, e)
        print(json.dumps({"tool": TOOL, "appended": {"seq": written["seq"], "run_id": written["run_id"],
                                                     "event": written["event"],
                                                     "entry_sha256": written["entry_sha256"]}},
                         ensure_ascii=False))
        return 0
    except TraceError as exc:
        print(json.dumps({"tool": TOOL, "refused": str(exc)}, ensure_ascii=False))
        return 3
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"tool": TOOL, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
