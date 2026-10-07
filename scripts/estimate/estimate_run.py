#!/usr/bin/env python3
"""How much a create-project / literature-search run will cost, before it runs.

    estimate_run.py [--run <lit_search run dir>] [--vault <vault> --papers P-0001 …] [--planned-papers N]
                    [--facets N] [--sections 8]

The heavy passes of a Kairo project are subagents: one `screener` per page of
candidates, one `facet-summarizer` per ≤ 6 papers, one `sota-synthesizer`, one
`fresh-verifier` per section part — plus the network time of ingestion. This
prints, per stage, how many subagents run, on which model (config/models.toml),
roughly how many input / output tokens they read and write, and how long the
stage takes, so the researcher approves the run knowing its size (on a Claude
subscription the tokens are usage, not money).

Everything is an **estimate** from sizes on disk: characters / 3.6 per token,
a re-read factor for the summarizers (they re-open each locator before citing
it), arXiv's 3 s spacing for ingestion. It is never a measurement; the run's
own numbers are what happened.

  --run             a lit_search run directory (candidates.json): the screening stage
  --vault --papers  ingested notes: ingestion is done, map / reduce / verify from their real sizes
  --planned-papers  N papers not ingested yet (typical size: 60,000 characters each)

Prints one JSON object. Exit: 0 ok · 2 bad input. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHARS_PER_TOKEN = 3.6
PAGE = 40                       # candidates per screener page (literature-search step 5)
PREFILTER_READ_MAX = 120        # prefiltered-out candidates still screened when this few
PAPERS_PER_SUMMARIZER = 6
REREAD = 1.6                    # summarizers re-open each locator before citing it
TYPICAL_NOTE_CHARS = 60_000
PROMPT_TOKENS = {"screener": 1_500, "facet_summarizer": 3_000, "sota_synthesizer": 4_000, "fresh_verifier": 4_000}
OUT_TOKENS = {"screener_per_candidate": 60, "facet_summarizer": 2_500, "sota_synthesizer": 9_000,
              "fresh_verifier": 1_500}
VERIFY_SHARE = 0.25             # share of the cited papers' text that lands in the section packets
MAX_PACKET_CHARS = 150_000
INGEST_SECONDS = 45             # per paper: abstract page, HTML, ~10 figures at 3 s, metadata, resolution
SUBAGENT_SECONDS = 150          # one subagent turn-set, running in parallel with its siblings


def policy_model(task: str) -> str | None:
    """The model a subagent task runs on, from config/models.toml (tier → id)."""
    try:
        text = (ROOT / "config" / "models.toml").read_text(encoding="utf-8")
    except OSError:
        return None
    tiers = dict(re.findall(r'^(opus|sonnet|haiku)\s*=\s*"([^"]+)"', text, re.M))
    m = re.search(rf"^\[tasks\.{re.escape(task)}\]\s*\n(.*?)(?=^\[|\Z)", text, re.M | re.S)
    tier = re.search(r'^tier\s*=\s*"(\w+)"', m.group(1), re.M) if m else None
    return tiers.get(tier.group(1)) if tier else None


def tokens(chars: float) -> int:
    return int(math.ceil(chars / CHARS_PER_TOKEN))


def screening(run: Path) -> dict:
    cands = json.loads((run / "candidates.json").read_text(encoding="utf-8"))
    passing = [c for c in cands if (c.get("prefilter") or {}).get("pass", True)]
    out_pf = [c for c in cands if not (c.get("prefilter") or {}).get("pass", True)]
    read_pf = out_pf if len(out_pf) <= PREFILTER_READ_MAX else []
    pool = passing + read_pf
    pages = math.ceil(len(passing) / PAGE) + math.ceil(len(read_pf) / PAGE)
    chars = sum(len(c.get("title") or "") + len(c.get("abstract") or "") + 200 for c in pool)
    return {"subagents": pages, "model": policy_model("screener"), "candidates": len(pool),
            "input_tokens": tokens(chars) + pages * PROMPT_TOKENS["screener"],
            "output_tokens": len(pool) * OUT_TOKENS["screener_per_candidate"],
            "wall_minutes": round(math.ceil(pages / 6) * SUBAGENT_SECONDS / 60, 1)}


def paper_sizes(vault: Path | None, ids: list[str], planned: int) -> list[int]:
    sizes = []
    for pid in ids:
        hits = sorted((vault / "Papers").glob(f"{pid} *.md")) + sorted((vault / "Papers").glob(f"{pid}.md")) \
            if vault else []
        sizes.append(hits[0].stat().st_size if hits else TYPICAL_NOTE_CHARS)
    return sizes + [TYPICAL_NOTE_CHARS] * planned


def downstream(sizes: list[int], already_ingested: int, sections: int) -> dict:
    n = len(sizes)
    total = sum(sizes)
    summarizers = max(1, math.ceil(n / PAPERS_PER_SUMMARIZER)) if n else 0
    packet_chars = total * VERIFY_SHARE
    parts = max(sections, math.ceil(packet_chars / MAX_PACKET_CHARS)) if n else 0
    to_ingest = n - already_ingested
    return {
        "ingest": {"papers": n, "to_fetch": to_ingest, "subagents": 0, "model": None, "input_tokens": 0,
                   "output_tokens": 0, "wall_minutes": round(to_ingest * INGEST_SECONDS / 60, 1)},
        "map": {"subagents": summarizers, "model": policy_model("facet_summarizer"),
                "input_tokens": tokens(total * REREAD) + summarizers * PROMPT_TOKENS["facet_summarizer"],
                "output_tokens": summarizers * OUT_TOKENS["facet_summarizer"],
                "wall_minutes": round(math.ceil(summarizers / 6) * SUBAGENT_SECONDS * 2 / 60, 1)},
        "reduce": {"subagents": 1 if n else 0, "model": policy_model("sota_synthesizer"),
                   "input_tokens": (summarizers * OUT_TOKENS["facet_summarizer"] + PROMPT_TOKENS["sota_synthesizer"])
                   if n else 0,
                   "output_tokens": OUT_TOKENS["sota_synthesizer"] if n else 0,
                   "wall_minutes": round(SUBAGENT_SECONDS * 2 / 60, 1) if n else 0},
        "verify": {"subagents": parts, "model": policy_model("fresh_verifier"),
                   "input_tokens": tokens(packet_chars) + parts * PROMPT_TOKENS["fresh_verifier"],
                   "output_tokens": parts * OUT_TOKENS["fresh_verifier"],
                   "wall_minutes": round(math.ceil(parts / 6) * SUBAGENT_SECONDS / 60, 1) if parts else 0},
    }


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", type=Path)
    ap.add_argument("--vault", type=Path)
    ap.add_argument("--papers", nargs="*", default=[])
    ap.add_argument("--planned-papers", type=int, default=0)
    ap.add_argument("--sections", type=int, default=8, help="Estado-del-arte sections with citations")
    a = ap.parse_args(argv)
    if not (a.run or a.papers or a.planned_papers):
        print(json.dumps({"error": "give --run, --papers or --planned-papers"}))
        return 2
    if a.papers and not a.vault:
        print(json.dumps({"error": "--papers needs --vault"}))
        return 2
    stages: dict = {}
    try:
        if a.run:
            stages["screening"] = screening(a.run)
    except (OSError, ValueError) as e:
        print(json.dumps({"error": f"cannot read the run: {e}"}, ensure_ascii=False))
        return 2
    if a.papers or a.planned_papers:
        stages.update(downstream(paper_sizes(a.vault, a.papers, a.planned_papers), len(a.papers), a.sections))
    total = {k: round(sum(s[k] for s in stages.values()), 1) for k in ("input_tokens", "output_tokens",
                                                                       "wall_minutes", "subagents")}
    print(json.dumps({"tool": "kairo/estimate_run@1.0.0", "stages": stages, "total": total,
                      "note": "estimación a partir de tamaños en disco (caracteres / 3.6 por token, factores fijos); "
                              "no es una medida — la ejecución dirá lo que costó"}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
