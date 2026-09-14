#!/usr/bin/env python3
"""Append-only agreement/cost log for hypothesis-cycle v2's dual-critic mode.

Tracks, per Check-3/Check-4 comparison between the primary critic and the
`second-critic` subagent, whether they agreed, and the actual DeepInfra cost
of that call. `hypothesis-cycle` v2 mode calls `record` after each comparison
and `summary` at the end of a cycle run.

The log file itself lives in the VAULT (e.g. `Scripts/second-critic-log.jsonl`
at the vault root), not in this plugin -- it's runtime data specific to one
vault's usage history, not something the plugin ships. Pass its path with
--log.

Standard library only.

Why this exists (see hypothesis-cycle v1 scope notes): a second critic that
always agrees with the primary isn't adding independent signal, it's a
rubber stamp -- possibly because the model is too weak, the prompt leaks the
primary's reasoning, or the checklist is unambiguous enough that any
competent reader converges. `summary` prints an explicit warning once there's
enough data to judge that, rather than silently assuming the second critic is
doing its job. Do not raise or remove the threshold to make a warning go away
without a documented reason -- that defeats the purpose.

Usage:
    python agreement_log.py record --log PATH --project PROJ-XXX \\
        --hypothesis "<claim or H-XXXX>" --check 3 --tier default \\
        --primary pass --second pass --cost-usd 0.0021 [--note "..."]

    python agreement_log.py summary --log PATH [--window N]

Exit codes: 0 ok, 2 invalid input / log unreadable.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

__version__ = "1.0.0"

VERDICTS = ("pass", "refinable", "clear_fail")

# Below this many comparisons, agreement-rate stats are too noisy to warn on.
_MIN_SAMPLE_FOR_WARNING = 15
# Agreement rate at or above this, with enough samples, triggers the warning.
_HIGH_AGREEMENT_THRESHOLD = 0.90


def record(args: argparse.Namespace) -> int:
    if args.primary not in VERDICTS or args.second not in VERDICTS:
        print(f"error: --primary/--second must be one of {VERDICTS}", file=sys.stderr)
        return 2
    if args.check not in (3, 4):
        print("error: --check must be 3 or 4", file=sys.stderr)
        return 2

    row = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "project": args.project,
        "hypothesis": args.hypothesis,
        "check": args.check,
        "tier": args.tier,
        "primary_verdict": args.primary,
        "second_verdict": args.second,
        "agree": args.primary == args.second,
        "cost_usd": args.cost_usd,
        "note": args.note,
    }

    path = Path(args.log)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")

    print(f"recorded: check {args.check}, {args.primary} vs {args.second} "
          f"-> {'AGREE' if row['agree'] else 'DISAGREE'} (${args.cost_usd:.4f})")
    return 0


def _load_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"warning: skipping unparseable line {line_no}: {exc}", file=sys.stderr)
    return rows


def summary(args: argparse.Namespace) -> int:
    path = Path(args.log)
    rows = _load_rows(path)
    if not rows:
        print(f"no comparisons logged yet at {path}")
        return 0

    window = rows[-args.window:] if args.window else rows
    n = len(window)
    agreements = sum(1 for r in window if r.get("agree"))
    disagreements = n - agreements
    agree_rate = agreements / n
    total_cost = sum(r.get("cost_usd") or 0.0 for r in window)
    cumulative_cost = sum(r.get("cost_usd") or 0.0 for r in rows)

    print(f"second-critic agreement log: {path}")
    print(f"comparisons in window: {n} (of {len(rows)} total logged)")
    print(f"agreements: {agreements}  disagreements: {disagreements}  "
          f"agreement rate: {agree_rate:.1%}")
    print(f"window cost: ${total_cost:.4f}   cumulative cost (all time): ${cumulative_cost:.4f}")

    for c in (3, 4):
        c_rows = [r for r in window if r.get("check") == c]
        if c_rows:
            c_agree = sum(1 for r in c_rows if r.get("agree"))
            print(f"  check {c}: {c_agree}/{len(c_rows)} agree ({c_agree / len(c_rows):.1%})")

    if n >= _MIN_SAMPLE_FOR_WARNING and agree_rate >= _HIGH_AGREEMENT_THRESHOLD:
        print(
            f"\nWARNING: agreement rate {agree_rate:.1%} over the last {n} comparisons "
            f"is at or above the {_HIGH_AGREEMENT_THRESHOLD:.0%} threshold. This may mean "
            "the second critic isn't adding independent signal -- check that it's genuinely "
            "querying a different model and reasoning independently, not converging because "
            "the checklist leaves no real judgement call, before trusting this as a working "
            "disagreement gate."
        )

    if args.json:
        payload = {
            "log": str(path),
            "window": n,
            "total_logged": len(rows),
            "agreements": agreements,
            "disagreements": disagreements,
            "agreement_rate": agree_rate,
            "window_cost_usd": total_cost,
            "cumulative_cost_usd": cumulative_cost,
            "high_agreement_warning": n >= _MIN_SAMPLE_FOR_WARNING and agree_rate >= _HIGH_AGREEMENT_THRESHOLD,
        }
        print("RESULT_JSON: " + json.dumps(payload, sort_keys=True))

    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="hypothesis-cycle v2 dual-critic agreement/cost log.")
    parser.add_argument("--version", action="version",
                        version=f"agreement_log.py {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_record = sub.add_parser("record", help="append one comparison row")
    p_record.add_argument("--log", required=True, help="path to the .jsonl log (in the vault)")
    p_record.add_argument("--project", required=True)
    p_record.add_argument("--hypothesis", required=True, help="claim text or H-XXXX id")
    p_record.add_argument("--check", type=int, required=True, choices=(3, 4))
    p_record.add_argument("--tier", required=True, choices=("default", "high_stakes"))
    p_record.add_argument("--primary", required=True, choices=VERDICTS)
    p_record.add_argument("--second", required=True, choices=VERDICTS)
    p_record.add_argument("--cost-usd", type=float, dest="cost_usd", required=True)
    p_record.add_argument("--note", default=None)
    p_record.set_defaults(func=record)

    p_summary = sub.add_parser("summary", help="print agreement rate + cumulative cost")
    p_summary.add_argument("--log", required=True)
    p_summary.add_argument("--window", type=int, default=0,
                           help="only consider the last N rows (default: all)")
    p_summary.add_argument("--json", action="store_true")
    p_summary.set_defaults(func=summary)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
