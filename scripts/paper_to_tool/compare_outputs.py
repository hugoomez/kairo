#!/usr/bin/env python3
"""Mechanical comparison of an extracted tool's outputs against ground truth.

`paper-to-tool` step 6 calls this to decide whether an extracted method
reproduces the reference outputs captured from the paper's own code (step 4).
The verdict is mechanical -- the same way `run-experiment` never judges
significance in prose, `paper-to-tool` never judges "close enough" by eye.

Both inputs are JSON documents of the same shape (objects, arrays, numbers,
strings, booleans, null). The walk is strict:

  - object keys must match exactly (a missing or extra key is a failure);
  - arrays must have the same length (a shape mismatch is a failure);
  - int, bool, str and null values must be equal exactly;
  - floats pass when  |cand - ref| <= atol + rtol * |ref|  (numpy.isclose's
    rule, asymmetric: `ref` is the ground truth);
  - NaN matches NaN and +/-inf matches the same inf only with --nan-equal;
    otherwise any non-finite value is a failure.

An int in one document and a float in the other is compared as floats (JSON
writers disagree on `1` vs `1.0`), never as a type failure.

--ignore drops keys by dotted path before comparing (e.g. `meta.wall_time_s`),
for fields that are provenance, not results. Ignoring a result field to make a
comparison pass is exactly what this script exists to prevent -- every ignored
path is echoed in the output so the TOOL.md record shows it.

Usage:
    python compare_outputs.py --reference ref.json --candidate cand.json \\
        --rtol 1e-5 --atol 1e-8 [--nan-equal] [--ignore meta.wall_time_s ...] --json

Exit codes: 0 = pass, 1 = fail (a mismatch), 2 = invalid input.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

__version__ = "1.0.0"
MAX_REPORTED = 25


class Result:
    def __init__(self) -> None:
        self.n_compared = 0
        self.n_float = 0
        self.max_abs_err = 0.0
        self.max_rel_err = 0.0
        self.max_err_path: str | None = None
        self.failures: list[dict] = []
        self.n_failures = 0

    def fail(self, path: str, kind: str, ref, cand) -> None:
        self.n_failures += 1
        if len(self.failures) < MAX_REPORTED:
            self.failures.append({"path": path or "$", "kind": kind, "reference": _short(ref), "candidate": _short(cand)})


def _short(v):
    s = json.dumps(v)
    return v if len(s) <= 120 else s[:117] + "..."


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def drop_paths(doc, paths: list[str]):
    for p in paths:
        parts = p.split(".")
        node = doc
        for k in parts[:-1]:
            node = node.get(k) if isinstance(node, dict) else None
            if node is None:
                break
        if isinstance(node, dict):
            node.pop(parts[-1], None)
    return doc


def compare(ref, cand, rtol: float, atol: float, nan_equal: bool, res: Result, path: str = "") -> None:
    if isinstance(ref, dict) or isinstance(cand, dict):
        if not (isinstance(ref, dict) and isinstance(cand, dict)):
            res.fail(path, "type", type(ref).__name__, type(cand).__name__)
            return
        for k in sorted(set(ref) | set(cand)):
            p = f"{path}.{k}" if path else k
            if k not in cand:
                res.fail(p, "missing_in_candidate", ref[k], None)
            elif k not in ref:
                res.fail(p, "extra_in_candidate", None, cand[k])
            else:
                compare(ref[k], cand[k], rtol, atol, nan_equal, res, p)
        return
    if isinstance(ref, list) or isinstance(cand, list):
        if not (isinstance(ref, list) and isinstance(cand, list)):
            res.fail(path, "type", type(ref).__name__, type(cand).__name__)
            return
        if len(ref) != len(cand):
            res.fail(path, "length", len(ref), len(cand))
            return
        for i, (r, c) in enumerate(zip(ref, cand)):
            compare(r, c, rtol, atol, nan_equal, res, f"{path}[{i}]")
        return

    res.n_compared += 1
    if _is_num(ref) and _is_num(cand):
        if isinstance(ref, int) and isinstance(cand, int):
            if ref != cand:
                res.fail(path, "int_mismatch", ref, cand)
            return
        r, c = float(ref), float(cand)
        res.n_float += 1
        if not (math.isfinite(r) and math.isfinite(c)):
            same = (math.isnan(r) and math.isnan(c)) or (r == c)
            if not (nan_equal and same):
                res.fail(path, "non_finite", ref, cand)
            return
        abs_err = abs(c - r)
        rel_err = abs_err / abs(r) if r != 0 else (0.0 if abs_err == 0 else math.inf)
        if abs_err > res.max_abs_err:
            res.max_abs_err, res.max_err_path = abs_err, path or "$"
        if rel_err > res.max_rel_err and math.isfinite(rel_err):
            res.max_rel_err = rel_err
        if abs_err > atol + rtol * abs(r):
            res.fail(path, "float_out_of_tolerance", ref, cand)
        return
    if type(ref) is not type(cand) or ref != cand:
        res.fail(path, "value_mismatch", ref, cand)


def _load(p: Path):
    # Python's json accepts NaN / Infinity, which numeric writers (json.dump of
    # a float('nan')) emit; they must survive to reach the non-finite rule.
    return json.loads(p.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--reference", type=Path, required=True, help="ground-truth JSON (from the paper's own code)")
    ap.add_argument("--candidate", type=Path, required=True, help="the extracted tool's JSON output")
    ap.add_argument("--rtol", type=float, required=True, help="relative tolerance -- frozen in TOOL.md before the first comparison")
    ap.add_argument("--atol", type=float, required=True, help="absolute tolerance -- frozen in TOOL.md before the first comparison")
    ap.add_argument("--nan-equal", action="store_true", help="NaN==NaN and inf==inf of the same sign count as matches")
    ap.add_argument("--ignore", nargs="*", default=[], metavar="DOTTED.PATH", help="provenance-only keys to drop before comparing")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args(argv)

    if a.rtol < 0 or a.atol < 0 or not (math.isfinite(a.rtol) and math.isfinite(a.atol)):
        print("error: --rtol and --atol must be finite and >= 0", file=sys.stderr)
        return 2
    try:
        ref = drop_paths(_load(a.reference), a.ignore)
        cand = drop_paths(_load(a.candidate), a.ignore)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    res = Result()
    compare(ref, cand, a.rtol, a.atol, a.nan_equal, res)
    verdict = "pass" if res.n_failures == 0 and res.n_compared > 0 else "fail"
    out = {
        "verdict": verdict,
        "rule": "|cand - ref| <= atol + rtol*|ref|; exact for int/str/bool/null; keys and lengths must match",
        "rtol": a.rtol,
        "atol": a.atol,
        "nan_equal": a.nan_equal,
        "ignored_paths": a.ignore,
        "values_compared": res.n_compared,
        "floats_compared": res.n_float,
        "max_abs_err": res.max_abs_err,
        "max_rel_err": res.max_rel_err,
        "max_abs_err_at": res.max_err_path,
        "n_failures": res.n_failures,
        "failures": res.failures,
        "reference": str(a.reference),
        "candidate": str(a.candidate),
        "script_version": __version__,
    }
    if res.n_compared == 0:
        out["note"] = "nothing was compared -- an empty comparison is a fail, not a vacuous pass"
    if a.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"{verdict.upper()}  compared={res.n_compared} floats={res.n_float} "
              f"max_abs_err={res.max_abs_err:.3g} max_rel_err={res.max_rel_err:.3g} failures={res.n_failures}")
        for f in res.failures:
            print(f"  {f['kind']:<24} {f['path']}: ref={f['reference']} cand={f['candidate']}")
    return 0 if verdict == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
