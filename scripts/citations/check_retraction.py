#!/usr/bin/env python3
"""Retraction / withdrawal check for a list of literature-search candidates.

literature-search step 4a calls this so the detection logic lives in one
place (`retraction.py`). Input: a JSON array of candidates, each an object
with any of `id` (caller's label, echoed back), `doi`, `arxiv`. Extra keys are
ignored and NOT echoed (so titles/abstracts never round-trip). Output: one
JSON object on stdout:

    {
      "version": "1.0.0",
      "endpoints_verified": "2026-09-24",
      "results": [
        {"id": "c1", "doi": "10.x/y", "arxiv": "2201.02177",
         "status": "clear | concern | withdrawn | retracted",
         "retracted": false, "withdrawn": false, "concern": false,
         "notice_for": [],               # this DOI is itself a notice about these DOIs
         "evidence": ["Crossref updated-by: retraction notice 10.x/z (2021-12-15, source retraction-watch)"],
         "checks": {"crossref": {"state": "ok | not_found | lost | not_applicable",
                                 "flag": "clear | concern | withdrawn | retracted"},
                    "arxiv":    {"state": "...", "flag": "..."}}}
      ],
      "counts": {"crossref": {"checked": 3, "removed": 1, "lost": 0},
                 "arxiv":    {"checked": 9, "removed": 0, "lost": 0}}
    }

`removed` counts candidates that source's own check flagged as retracted/withdrawn (an
expression of concern is reported but not removed). `checked` counts
candidates for which that source answered (ok or not_found); `lost` counts
candidates whose lookup failed after retries -- report those, never count
them as checked. arXiv ids are batched (<=100 per call, 3 s serial spacing);
Crossref is one call per unique DOI; 429/5xx are retried with backoff
(3 attempts) then the lookup is LOST.

Usage:
    python check_retraction.py candidates.json
    python check_retraction.py - < candidates.json
    python check_retraction.py candidates.json --mailto you@example.org
    python check_retraction.py --version

Exit codes: 0 ok (flags are in the JSON, not the exit code), 1 error (could
not run), 2 invalid input.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import retraction  # noqa: E402

__version__ = "1.0.0"


def load_candidates(raw: str) -> list[dict]:
    data = json.loads(raw)
    if isinstance(data, dict) and "candidates" in data:
        data = data["candidates"]
    if not isinstance(data, list) or not all(isinstance(c, dict) for c in data):
        raise ValueError("input must be a JSON array of objects (or {\"candidates\": [...]})")
    return data


def run(cands: list[dict], mailto: str | None = None) -> dict:
    aids = [retraction.normalize_arxiv(c.get("arxiv")) or
            retraction.arxiv_from_doi(retraction.normalize_doi(c.get("doi"))) for c in cands]
    entries, lost = retraction.fetch_arxiv([a for a in aids if a])
    results, crossref_cache = [], {}
    counts = {s: {"checked": 0, "removed": 0, "lost": 0} for s in ("crossref", "arxiv")}
    for c in cands:
        doi = retraction.normalize_doi(c.get("doi"))
        key = (doi, retraction.normalize_arxiv(c.get("arxiv")))
        if key in crossref_cache:
            r = dict(crossref_cache[key])
        else:
            r = retraction.check_one(doi, c.get("arxiv"), entries, lost, mailto)
            crossref_cache[key] = r
        r = {"id": c.get("id"), **r}
        for src in ("crossref", "arxiv"):
            st = r["checks"][src]["state"]
            if st in ("ok", "not_found"):
                counts[src]["checked"] += 1
                if r["checks"][src]["flag"] in ("retracted", "withdrawn"):
                    counts[src]["removed"] += 1
            elif st == "lost":
                counts[src]["lost"] += 1
        results.append(r)
    return {"version": __version__, "endpoints_verified": retraction.ENDPOINTS_VERIFIED,
            "results": results, "counts": counts}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("input", nargs="?", help="candidates JSON file, or - for stdin")
    ap.add_argument("--mailto", help="contact address for the Crossref polite pool (optional)")
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args(argv)
    if not a.input:
        print("error: give a candidates JSON file or - for stdin", file=sys.stderr)
        return 2
    try:
        raw = sys.stdin.read() if a.input == "-" else Path(a.input).read_text(encoding="utf-8")
        cands = load_candidates(raw)
    except (OSError, ValueError) as e:
        print(f"error: invalid input: {e}", file=sys.stderr)
        return 2
    try:
        out = run(cands, a.mailto)
    except Exception as e:  # noqa: BLE001 - any crash is "could not run"
        print(f"error: {type(e).__name__}: {retraction.net.redact(str(e))}", file=sys.stderr)
        return 1
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
