#!/usr/bin/env python3
"""The researcher's own decisions on a hypothesis. Researcher only, always with a reason.

    review.py show          --vault <vault> --hypothesis H-XXXX
    review.py mark-reviewed --vault <vault> --hypothesis H-XXXX --reason "..." --by "<name>"
    review.py ack-findings  --vault <vault> --hypothesis H-XXXX --reason "..." --by "<name>"
    review.py discard       --vault <vault> --hypothesis H-XXXX --reason "..." --by "<name>"

- `show` (JSON): which decisions apply now, and why the others don't.
- `mark-reviewed`: you read a hypothesis the cycle flagged
  (`needs_human_review: true`). Sets it to `false`. Status is unchanged.
- `ack-findings`: you read the fresh verifier's findings on the governing
  `note` verification. Sets `verification_reviewed: true`. Refused when the
  latest verdict is `no_errors_found`, since there is nothing to acknowledge.
  Status is unchanged; promotion stays with update-confidence and its gates.
- `discard`: the edge `propuesta | en_cola → descartada`, a terminal state.
  It is the one status edge not decided by evidence, so update-confidence
  delegates it to this script (see its state table). Refused from any other
  status: after a preregistration, evidence decides.

The first two decisions append an entry to the note's `reviews:` block
(date, kind, by, reason). A discard appends a `history:` entry
(`status: descartada`, by, evidence: the reason). Nothing else in the note
changes.

Like `signoff.py`, this refuses to run inside an agent session (`CLAUDECODE`
or `KAIRO_AGENT_SESSION` set): these decisions are the researcher's. The
Kairo interface runs it with those markers cleared for its own call.

Exit codes: 0 ok · 3 refused · 1 error. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from verifications import (  # noqa: E402
    InputError,
    _set_scalar,
    block_end,
    find_key,
    fm_value,
    latest,
    load,
    write_raw,
    yaml_scalar,
)

TOOL = "kairo/review@1.0.0"
AGENT_MARKERS = ("CLAUDECODE", "KAIRO_AGENT_SESSION")
DISCARDABLE = ("propuesta", "en_cola")
_ID = re.compile(r"^H-\d{4}$")


class Refused(Exception):
    pass


def find_hypothesis(vault: Path, hid: str) -> Path:
    if not _ID.match(hid):
        raise Refused("hypothesis must look like H-XXXX")
    hits = sorted(vault.glob(f"Projects/*/Hipotesis/{hid}*.md"))
    hits = [h for h in hits if re.match(rf"^{hid}(\b|[ _.-])", h.name)]
    if not hits:
        raise Refused(f"{hid} not found under Projects/*/Hipotesis/")
    if len(hits) > 1:
        raise Refused(f"{hid} matches several notes: {', '.join(h.name for h in hits)}")
    return hits[0]


def state(path: Path) -> dict:
    _, lines, _, lo, hi, entries = load(str(path))
    status = (fm_value(lines, lo + 1, hi, "status") or "").strip()
    nhr = (fm_value(lines, lo + 1, hi, "needs_human_review") or "").lower() == "true"
    reviewed = (fm_value(lines, lo + 1, hi, "verification_reviewed") or "").lower() == "true"
    gov = latest(entries, "note")
    findings = bool(gov) and gov.get("verdict") != "no_errors_found"
    return {
        "status": status,
        "needs_human_review": nhr,
        "verification_reviewed": reviewed,
        "governing_verdict": gov.get("verdict") if gov else None,
        "decisions": {
            "mark-reviewed": None if nhr else "no hay revisión pendiente (needs_human_review no es true)",
            "ack-findings": (None if findings and not reviewed else
                             "el verificador no encontró errores" if not findings else
                             "los hallazgos ya están reconocidos"),
            "discard": None if status in DISCARDABLE else
            f"solo se descarta una hipótesis propuesta o en cola (está «{status}»); tras el preregistro decide la evidencia",
        },
    }


def append_entry(lines: list[str], nl: str, key: str, fields: list[tuple[str, str]]) -> None:
    hi = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    k = find_key(lines, 1, hi, key)
    entry = [f"  - {fields[0][0]}: {yaml_scalar(fields[0][1])}{nl}"]
    entry += [f"    {name}: {yaml_scalar(value)}{nl}" for name, value in fields[1:]]
    if k is None:
        lines[hi:hi] = [f"{key}:{nl}", *entry]
        return
    if lines[k].rstrip("\r\n").split(":", 1)[1].strip() == "[]":
        lines[k] = f"{key}:{nl}"
    end = block_end(lines, k, hi)
    lines[end:end] = entry


def decide(vault: Path, hid: str, kind: str, reason: str, by: str) -> dict:
    for m in AGENT_MARKERS:
        if os.environ.get(m):
            raise Refused(f"las decisiones sobre una hipótesis son del investigador: rechazado dentro de una sesión de agente ({m})")
    reason = " ".join(reason.split())
    if len(reason) < 5:
        raise Refused("--reason is required (at least 5 characters): the decision is recorded with why")
    if not by.strip():
        raise Refused("--by is required")
    path = find_hypothesis(vault, hid)
    before = state(path)
    why_not = before["decisions"][kind]
    if why_not:
        raise Refused(why_not)
    text, lines, nl, _, _, _ = load(str(path))
    today = date.today().isoformat()
    if kind == "mark-reviewed":
        _set_scalar(lines, "needs_human_review", "false", nl)
        append_entry(lines, nl, "reviews", [("date", today), ("kind", "revisada"), ("by", by), ("reason", reason)])
    elif kind == "ack-findings":
        _set_scalar(lines, "verification_reviewed", "true", nl)
        append_entry(lines, nl, "reviews", [("date", today), ("kind", "hallazgos_reconocidos"), ("by", by),
                                            ("verdict", str(before["governing_verdict"])), ("reason", reason)])
    else:
        _set_scalar(lines, "status", "descartada", nl)
        _set_scalar(lines, "updated", today, nl)
        append_entry(lines, nl, "history", [("date", today), ("status", "descartada"), ("by", by),
                                            ("evidence", f"descartada por el investigador: {reason}")])
    write_raw(str(path), "".join(lines))
    after = state(path)
    return {"tool": TOOL, "hypothesis": hid, "decision": kind, "file": path.relative_to(vault).as_posix(),
            "before": {k: before[k] for k in ("status", "needs_human_review", "verification_reviewed")},
            "after": {k: after[k] for k in ("status", "needs_human_review", "verification_reviewed")}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("show", "mark-reviewed", "ack-findings", "discard"):
        p = sub.add_parser(name)
        p.add_argument("--vault", required=True, type=Path)
        p.add_argument("--hypothesis", required=True)
        if name != "show":
            p.add_argument("--reason", required=True)
            p.add_argument("--by", required=True)
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if a.cmd == "show":
            path = find_hypothesis(a.vault.resolve(), a.hypothesis)
            out = {"hypothesis": a.hypothesis, "file": path.relative_to(a.vault.resolve()).as_posix(), **state(path)}
        else:
            out = decide(a.vault.resolve(), a.hypothesis, a.cmd, a.reason, a.by)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    except (InputError, OSError, StopIteration) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
