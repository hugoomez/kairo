#!/usr/bin/env python3
"""Show the verbatim source text behind one citation locator.

    cite_text.py --vault <vault> --paper P-XXXX --locator "§4.2, Tabla 2"

Prints JSON: the paper's title and source note, the matched units of its
`## Texto completo` (or `## Resumen` for an abstract locator), and a `note`
when nothing matches. It is the fresh-verifier's own resolver
(`scripts/ledger/verifier_packet.py resolve_citation`), exposed so an
interface can show a researcher exactly the text a hypothesis cites — the
same text the verifier sees, never a summary. A `send: never` paper and the
model-written `Papers/_notas/` are refused by that resolver, not here.

Exit codes: 0 ok (possibly with no match — see `note`) · 1 error.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ledger"))
from verifier_packet import resolve_citation  # noqa: E402

_PID = re.compile(r"^P-\d{3,5}$")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vault", required=True)
    ap.add_argument("--paper", required=True)
    ap.add_argument("--locator", required=True)
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not _PID.match(args.paper):
        print(json.dumps({"error": "paper must look like P-XXXX"}))
        return 1
    try:
        res = resolve_citation(args.vault, args.paper, args.locator)
    except OSError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
