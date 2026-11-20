#!/usr/bin/env python3
"""Print a parse run's summary (counts only), from kairo_collect.parsereport's files.

    parse_summary.py OUT_DIR                     parse-summary.json (rebuilt from
                                                 parse-report.jsonl when missing)
    parse_summary.py OUT_DIR --rebuild           rebuild parse-summary.json first
    parse_summary.py OUT_DIR --skeletons CLASS [--limit N]
                                                 the distinct skeletons of the items that
                                                 failed with CLASS, with counts and formats

stdout is JSON: counts, coverage and skeletons (structural outlines, re-skeletonised
before printing); never an item id, a path or a value. Exit 0 ok, 1 error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kairo_collect.archive import atomic_write_json  # noqa: E402
from kairo_collect.parsereport import REPORT, SUMMARY, skeleton_groups, summarise  # noqa: E402


def _emit(obj) -> None:
    sys.stdout.buffer.write((json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="parse_summary.py", description=__doc__.split("\n")[0])
    ap.add_argument("out")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--skeletons", metavar="ERROR_CLASS")
    ap.add_argument("--limit", type=int, default=20)
    a = ap.parse_args(argv)
    out = Path(a.out)
    try:
        if a.skeletons:
            _emit({"error_class": a.skeletons, "groups": skeleton_groups(out / REPORT, a.skeletons, a.limit)})
            return 0
        path = out / SUMMARY
        if a.rebuild or not path.is_file():
            old = {}
            if path.is_file():
                try:
                    old = json.loads(path.read_text(encoding="utf-8"))
                except ValueError:
                    old = {}
            exp = (old.get("coverage") or {}).get("expected")
            s = summarise(out / REPORT, dataset=old.get("dataset"), expected=exp)
            atomic_write_json(path, s)
        else:
            s = json.loads(path.read_text(encoding="utf-8"))
        _emit(s)
        return 0
    except (OSError, ValueError) as e:
        _emit({"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"})
        return 1


if __name__ == "__main__":
    sys.exit(main())
