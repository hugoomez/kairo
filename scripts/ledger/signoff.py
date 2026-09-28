#!/usr/bin/env python3
"""The researcher's sign-off on a lemma / theorem — and only the researcher's.

    signoff.py show --vault V --claim C-XXXX --part statement|proof|infeasibility
        prints the exact text that would be signed and its sha256 (JSON)
    signoff.py sign --vault V --claim C-XXXX --part ... --confirm-sha <sha256> --by <name>
        appends a `signoffs:` entry bound to that sha256

`sign` requires `--confirm-sha` to equal the sha256 of the text as it is now:
you sign what you were shown, and a text edited in between is refused.
`infeasibility` signs the reason recorded with the latest `not_feasible`
numerical check.

It refuses to run inside an agent session: Claude Code marks the processes it
starts with CLAUDECODE, and the Kairo backend marks its agent sessions with
KAIRO_AGENT_SESSION. A sign-off happens in your terminal, or from the Kairo
interface (which shows you the text and its hash first). An agent may draft a
statement or a proof; it never approves one.

Exit codes: 0 ok · 3 refused · 1 error. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claim_records as cr  # noqa: E402
from claim_gate import claim_path, frontmatter  # noqa: E402
from verifier_packet import PROOF_KINDS  # noqa: E402

PARTS = ("statement", "proof", "infeasibility")
AGENT_MARKERS = ("CLAUDECODE", "KAIRO_AGENT_SESSION")


class Refused(Exception):
    pass


def in_agent_session() -> str | None:
    return next((m for m in AGENT_MARKERS if os.environ.get(m)), None)


def text_for(path: Path, part: str) -> str:
    if part == "statement":
        return cr.statement_text(path)
    if part == "proof":
        return cr.proof_text(path)
    checks = cr.read_block(path, "numerical_checks")
    last = checks[-1] if checks else None
    if not last or last.get("status") != "not_feasible":
        raise Refused("there is no not_feasible numerical check to sign the reason of")
    return cr.norm(last.get("reason") or "")


def show(vault: str, cid: str, part: str) -> dict:
    path = claim_path(vault, cid)
    if str(frontmatter(path).get("kind")) not in PROOF_KINDS:
        raise Refused(f"{cid} is not a lema / teorema")
    text = text_for(path, part)
    if not text.strip():
        raise Refused(f"{cid}: nothing to sign for {part} (empty)")
    return {"claim": cid, "part": part, "text": text, "sha256": cr.text_sha256(text)}


def sign(vault: str, cid: str, part: str, confirm_sha: str, by: str) -> dict:
    marker = in_agent_session()
    if marker:
        raise Refused(f"sign-offs are the researcher's: refused inside an agent session ({marker} is set)")
    if not by.strip():
        raise Refused("--by must name the researcher")
    shown = show(vault, cid, part)
    if confirm_sha != shown["sha256"]:
        raise Refused("the text changed since it was shown to you (sha256 differs): read it again and re-sign")
    entry = {"part": part, "sha256": shown["sha256"], "by": by.strip(), "date": date.today().isoformat()}
    cr.append_block(claim_path(vault, cid), "signoffs", entry)
    return entry


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("show", "sign"):
        p = sub.add_parser(name)
        p.add_argument("--vault", required=True)
        p.add_argument("--claim", required=True)
        p.add_argument("--part", required=True, choices=PARTS)
        if name == "sign":
            p.add_argument("--confirm-sha", required=True)
            p.add_argument("--by", required=True)
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        r = show(args.vault, args.claim, args.part) if args.cmd == "show" else sign(
            args.vault, args.claim, args.part, args.confirm_sha, args.by)
        print(json.dumps(r, ensure_ascii=False))
        return 0
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
