#!/usr/bin/env python3
"""Numerical sanity check of a lemma / theorem: test it on small cases.

    numeric_check.py run --vault V --claim C-XXXX --by <who> [--timeout 600]
        runs Projects/<slug>/Claims/checks/C-XXXX.py and records the result
    numeric_check.py infeasible --vault V --claim C-XXXX --reason "..." --by <who>
        records that no computational check is possible, and why

The check script is plain Python that tests the statement on small cases and
exits 0 when every case agrees with it, non-zero otherwise (its output says
which case failed). It is code: the Kairo interface runs it only after the
researcher approved it, with the script's text on screen.

`run` executes it in a scratch folder outside the vault, with a timeout, and
with credentials stripped from its environment (anything named *TOKEN*,
*KEY*, *SECRET*, *PASSWORD*, KAGGLE_*, ANTHROPIC_*, CLAUDE_*). This is not an
OS sandbox: the approval is the control. The result is appended to
`numerical_checks:` bound to the sha256 of the statement and of the script, so
editing either re-opens the gate; the full output is saved next to the script
(`checks/C-XXXX.out.txt`).

`infeasible` needs a stated reason; the gate then also requires the
researcher's sign-off on that reason (signoff.py --part infeasibility).

Exit codes: 0 recorded (whatever the check's result) · 3 refused · 1 error.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claim_records as cr  # noqa: E402
from claim_gate import check_script, claim_path, frontmatter  # noqa: E402
from verifier_packet import PROOF_KINDS  # noqa: E402

SECRET_ENV = re.compile(r"TOKEN|KEY|SECRET|PASSWORD|PASSWD|^KAGGLE_|^ANTHROPIC_|^CLAUDE_", re.I)


class Refused(Exception):
    pass


def clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not SECRET_ENV.search(k)}


def _proof_claim(vault: str, cid: str) -> Path:
    path = claim_path(vault, cid)
    if str(frontmatter(path).get("kind")) not in PROOF_KINDS:
        raise Refused(f"{cid} is not a lema / teorema")
    if not cr.statement_text(path).strip():
        raise Refused(f"{cid} has no ## Enunciado to check")
    return path


def run(vault: str, cid: str, by: str, timeout: int) -> dict:
    path = _proof_claim(vault, cid)
    script = check_script(path)
    if not script.is_file():
        raise Refused(f"no check script at {script} — write one, or record the check as infeasible")
    code = script.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="kairo-numcheck-") as scratch:
        runner = Path(scratch) / script.name
        runner.write_text(code, encoding="utf-8")
        try:
            proc = subprocess.run([sys.executable, str(runner)], cwd=scratch, env=clean_env(),
                                  capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
            status = "passed" if proc.returncode == 0 else "failed"
            output = f"exit {proc.returncode}\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        except subprocess.TimeoutExpired as exc:
            status = "error"
            output = f"timeout after {timeout}s\n{exc.stdout or ''}\n{exc.stderr or ''}"
    out_file = script.with_suffix(".out.txt")
    out_file.write_text(output, encoding="utf-8", newline="\n")
    entry = {
        "status": status,
        "statement_sha256": cr.text_sha256(cr.statement_text(path)),
        "script_sha256": cr.text_sha256(code),
        "output_sha256": cr.text_sha256(output),
        "date": date.today().isoformat(),
        "by": by,
    }
    cr.append_block(path, "numerical_checks", entry)
    return {**entry, "output_file": str(out_file), "output_tail": output[-2000:]}


def infeasible(vault: str, cid: str, reason: str, by: str) -> dict:
    path = _proof_claim(vault, cid)
    reason = cr.norm(reason)
    if len(reason) < 15:
        raise Refused("say why a computational check is not possible (at least a sentence)")
    entry = {
        "status": "not_feasible",
        "statement_sha256": cr.text_sha256(cr.statement_text(path)),
        "script_sha256": "none",
        "output_sha256": "none",
        "reason": reason,
        "date": date.today().isoformat(),
        "by": by,
    }
    cr.append_block(path, "numerical_checks", entry)
    return entry


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--vault", required=True)
    r.add_argument("--claim", required=True)
    r.add_argument("--by", required=True)
    r.add_argument("--timeout", type=int, default=600)
    i = sub.add_parser("infeasible")
    i.add_argument("--vault", required=True)
    i.add_argument("--claim", required=True)
    i.add_argument("--reason", required=True)
    i.add_argument("--by", required=True)
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        res = run(args.vault, args.claim, args.by, args.timeout) if args.cmd == "run" else infeasible(
            args.vault, args.claim, args.reason, args.by)
        print(json.dumps(res, ensure_ascii=False))
        return 0
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
