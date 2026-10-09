#!/usr/bin/env python3
"""How much a create-project / literature-search run will cost, before it runs.

    estimate_run.py [--run <lit_search run dir>] [--vault <vault> --papers P-0001 …] [--planned-papers N]
                    [--facets N] [--sections 8]

The heavy passes of a Kairo project are subagents: one `screener` per page of
candidates, one `paper-carder` per paper that has no current reading card yet
(a card is reused by every later map and project), one `facet-summarizer` per
≤ 6 papers (reading the cards, not the full texts), one `sota-synthesizer`, one
`fresh-verifier` per section part, the `paper-reader`s that fix what
check_sota.py and the verifiers flag — plus the main session that orchestrates
them and the network time of ingestion. This prints, per stage, how many
subagents run, on which model (config/models.toml), roughly how many input /
output tokens they read and write, and how long the stage takes, so the
researcher approves the run knowing its size (on a Claude subscription the
tokens are usage, not money).

Everything is an **estimate** from sizes on disk: characters / 3.6 per token,
arXiv's 3 s spacing for ingestion, figure images included (a typical count, and
`wall_minutes_max` for a figure-heavy corpus). Each stage gives a low figure
(`input_tokens`) and a high one (`input_tokens_high`); the totals are the low
figures and `range` gives both ends. The terms a count of texts alone misses
are explicit, each a named constant printed under `assumptions`:

  - SUBAGENT_SYSTEM_TOKENS — a subagent's system prompt and tool schemas, plus
    its own agents/*.md prompt (sized from the file), re-sent on **every turn**;
  - context growth — a subagent reading a note in pieces carries what it has read
    into each later turn: reads cost about (turns + 1) / 2 times their size;
  - ORCHESTRATOR_TURNS — the main session re-sends the create-project and
    literature-search skills (sized from the files) and its system prompt on
    every turn, and accumulates the subagents' replies;
  - FIX_REQUESTS_PER_PAPER / FIX_ROUNDS — paper-reader requests (≤ 10 per
    reader) for what check_sota.py and the verifiers flag, and CHECK_SOTA_RUNS —
    each re-run's report read by the main session.

Prompt caching makes re-sent tokens cheaper; this counts them in full. It is
never a measurement; the run's own numbers are what happened.

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

TOOL = "kairo/estimate_run@1.2.0"
ROOT = Path(__file__).resolve().parents[2]
CHARS_PER_TOKEN = 3.6
PAGE = 40                       # candidates per screener page (literature-search step 5)
PREFILTER_READ_MAX = 120        # prefiltered-out candidates still screened when this few
PAPERS_PER_SUMMARIZER = 6
REREAD = 1.6                    # summarizers re-open each locator before citing it
TYPICAL_NOTE_CHARS = 60_000
PROMPT_TOKENS = {"screener": 1_500, "facet_summarizer": 3_000, "sota_synthesizer": 4_000, "fresh_verifier": 4_000,
                 "paper_reader": 1_200}
OUT_TOKENS = {"screener_per_candidate": 60, "facet_summarizer": 2_500, "sota_synthesizer": 9_000,
              "fresh_verifier": 1_500, "paper_reader_per_request": 400}
VERIFY_SHARE = 0.25             # share of the cited papers' text that lands in the section packets
MAX_PACKET_CHARS = 150_000
# Ingestion is bound by arXiv's 3 s spacing between requests to arxiv.org (shared
# across processes, so parallel ingestion is no faster): metadata, the abstract
# page, the HTML (after its own 3 s pause), OpenAlex / Crossref and the reference
# check take about INGEST_BASE_SECONDS; then every figure image is one more
# arxiv.org request, up to MAX_FIGURES (ingest_paper.py) a paper.
INGEST_BASE_SECONDS = 25
ARXIV_SPACING_SECONDS = 3
TYPICAL_FIGURES = 12
MAX_FIGURES = 40
# `ligero` / --abstract-only: metadata, abstract and the reference check — no text, no figures
ABSTRACT_ONLY_SECONDS = 12
SUBAGENT_SECONDS = 150          # one subagent turn-set, running in parallel with its siblings
# Reading cards (ficha.py): a paper-carder reads each paper once, the map reads the cards
TYPICAL_CARD_CHARS = 9_000
CARD_REREAD = 1.3               # a summarizer still opens a section now and then to place a sentence
PROMPT_TOKENS["paper_carder"] = 2_000
OUT_TOKENS["paper_carder"] = 3_000

# --- what a count of texts alone misses (each low, high) -------------------------
SUBAGENT_SYSTEM_TOKENS = 8_000  # Claude Code's subagent system prompt + tool schemas, every turn
MAIN_SYSTEM_TOKENS = 15_000     # the main session's system prompt + tool schemas, every turn
TURNS = {"screener": (1, 2), "paper_carder": (6, 15), "facet_summarizer": (10, 25),
         "sota_synthesizer": (4, 12), "fresh_verifier": (2, 4), "paper_reader": (3, 8)}
ORCHESTRATOR_TURNS = {"base": (30, 60), "per_paper": (1, 3)}
ORCHESTRATOR_OUT_PER_TURN = 300
SYNTH_CHECK_CHARS = (0, 40_000)        # notes the synthesizer opens to confirm locators
FIX_REQUESTS_PER_PAPER = (0.5, 2.0)    # paper-reader requests from check_sota / verifier findings
FIX_ROUNDS = (1, 2)
FIX_REQUESTS_PER_READER = 10
FIX_READ_CHARS = 8_000                 # one request: the section under its locator
CHECK_SOTA_RUNS = (2, 4)
CHECK_SOTA_REPORT_TOKENS = 2_500       # one check_sota.py report, read by the main session
SKILLS = ("create-project", "literature-search")
AGENT_FILES = {"screener": "screener", "paper_carder": "paper-carder", "facet_summarizer": "facet-summarizer",
               "sota_synthesizer": "sota-synthesizer", "fresh_verifier": "fresh-verifier",
               "paper_reader": "paper-reader"}


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


def skill_chars() -> int:
    """The skills the main session carries through a create-project run, re-sent every turn."""
    total = 0
    for name in SKILLS:
        try:
            total += (ROOT / "skills" / name / "SKILL.md").stat().st_size
        except OSError:
            continue
    return total


def prompt_tokens(task: str) -> int:
    """An agent's own prompt (agents/<name>.md), from its size; a fixed figure if absent."""
    try:
        return tokens((ROOT / "agents" / f"{AGENT_FILES[task]}.md").stat().st_size)
    except (OSError, KeyError):
        return PROMPT_TOKENS.get(task, 2_000)


def agent_input(read_chars: float, turns: int, prompt_tokens: int) -> int:
    """Input tokens of one subagent: its system prompt + tool schemas + own prompt on
    every turn, and what it reads, carried into every later turn (read in pieces over
    its turns, the reads cost about (turns + 1) / 2 times their size)."""
    per_turn = SUBAGENT_SYSTEM_TOKENS + prompt_tokens
    read = tokens(read_chars)
    if turns <= 1:
        return per_turn + read
    return turns * per_turn + int(math.ceil(read * (turns + 1) / 2))


def _both(fn) -> tuple[int, int]:
    """fn(0) low, fn(1) high."""
    return fn(0), fn(1)


def _stage(subagents: int, model: str | None, inp: tuple[int, int], out: tuple[int, int],
           wall: float, **extra) -> dict:
    return {"subagents": subagents, "model": model, "input_tokens": inp[0], "input_tokens_high": inp[1],
            "output_tokens": out[0], "output_tokens_high": out[1], "wall_minutes": wall, **extra}


def screening(run: Path) -> dict:
    cands = json.loads((run / "candidates.json").read_text(encoding="utf-8"))
    passing = [c for c in cands if (c.get("prefilter") or {}).get("pass", True)]
    out_pf = [c for c in cands if not (c.get("prefilter") or {}).get("pass", True)]
    read_pf = out_pf if len(out_pf) <= PREFILTER_READ_MAX else []
    pool = passing + read_pf
    pages = math.ceil(len(passing) / PAGE) + math.ceil(len(read_pf) / PAGE)
    chars = sum(len(c.get("title") or "") + len(c.get("abstract") or "") + 200 for c in pool)
    p = prompt_tokens("screener")
    inp = _both(lambda i: sum(agent_input(chars / pages, TURNS["screener"][i], p) for _ in range(pages))
                if pages else 0)
    out = len(pool) * OUT_TOKENS["screener_per_candidate"]
    return _stage(pages, policy_model("screener"), inp, (out, out),
                  round(math.ceil(pages / 6) * SUBAGENT_SECONDS / 60, 1), candidates=len(pool))


def paper_sizes(vault: Path | None, ids: list[str], planned: int) -> list[int]:
    sizes = []
    for pid in ids:
        hits = sorted((vault / "Papers").glob(f"{pid} *.md")) + sorted((vault / "Papers").glob(f"{pid}.md")) \
            if vault else []
        sizes.append(hits[0].stat().st_size if hits else TYPICAL_NOTE_CHARS)
    return sizes + [TYPICAL_NOTE_CHARS] * planned


def card_sizes(vault: Path | None, ids: list[str], planned: int) -> list[int | None]:
    """Per paper (in --papers order, then the planned ones), the size of its current
    reading card, or None when it has none yet (or a stale one)."""
    if not vault:
        return [None] * (len(ids) + planned)
    sys.path.insert(0, str(ROOT / "scripts" / "papers"))
    import ficha
    out: list[int | None] = []
    for pid in ids:
        note = ficha.find_note(vault, pid)
        card = ficha.card_path(vault, pid)
        out.append(card.stat().st_size if note and ficha.card_state(vault, note) == "vigente" else None)
    return out + [None] * planned


def downstream(sizes: list[int], already_ingested: int, sections: int,
               cards: list[int | None] | None = None) -> dict:
    n = len(sizes)
    total = sum(sizes)
    cards = cards if cards is not None else [None] * n
    missing = [s for s, c in zip(sizes, cards) if c is None]
    map_chars = sum(c if c is not None else TYPICAL_CARD_CHARS for c in cards)
    summarizers = max(1, math.ceil(n / PAPERS_PER_SUMMARIZER)) if n else 0
    packet_chars = total * VERIFY_SHARE
    parts = max(sections, math.ceil(packet_chars / MAX_PACKET_CHARS)) if n else 0
    to_ingest = n - already_ingested
    pc, ps, pr, pv = (prompt_tokens(t) for t in ("paper_carder", "facet_summarizer", "sota_synthesizer",
                                                 "fresh_verifier"))
    contributions = summarizers * OUT_TOKENS["facet_summarizer"] * CHARS_PER_TOKEN
    out = {
        "ingest": {"papers": n, "to_fetch": to_ingest, "subagents": 0, "model": None,
                   "input_tokens": 0, "input_tokens_high": 0, "output_tokens": 0, "output_tokens_high": 0,
                   "wall_minutes": round(to_ingest * (INGEST_BASE_SECONDS + TYPICAL_FIGURES * ARXIV_SPACING_SECONDS)
                                         / 60, 1),
                   # a figure-heavy corpus (MAX_FIGURES images a paper)
                   "wall_minutes_max": round(to_ingest * (INGEST_BASE_SECONDS + MAX_FIGURES * ARXIV_SPACING_SECONDS)
                                             / 60, 1)},
        "cards": _stage(len(missing), policy_model("paper_carder"),
                        _both(lambda i: sum(agent_input(s, TURNS["paper_carder"][i], pc) for s in missing)),
                        (len(missing) * OUT_TOKENS["paper_carder"],) * 2,
                        round(math.ceil(len(missing) / 6) * SUBAGENT_SECONDS / 60, 1) if missing else 0,
                        reused=n - len(missing)),
        "map": _stage(summarizers, policy_model("facet_summarizer"),
                      _both(lambda i: summarizers and summarizers * agent_input(
                          map_chars * CARD_REREAD / summarizers, TURNS["facet_summarizer"][i], ps)),
                      (summarizers * OUT_TOKENS["facet_summarizer"],) * 2,
                      round(math.ceil(summarizers / 6) * SUBAGENT_SECONDS * 2 / 60, 1)),
        "reduce": _stage(1 if n else 0, policy_model("sota_synthesizer"),
                         _both(lambda i: agent_input(contributions + SYNTH_CHECK_CHARS[i],
                                                     TURNS["sota_synthesizer"][i], pr) if n else 0),
                         (OUT_TOKENS["sota_synthesizer"] if n else 0,) * 2,
                         round(SUBAGENT_SECONDS * 2 / 60, 1) if n else 0),
        "verify": _stage(parts, policy_model("fresh_verifier"),
                         _both(lambda i: parts and parts * agent_input(packet_chars / parts,
                                                                       TURNS["fresh_verifier"][i], pv)),
                         (parts * OUT_TOKENS["fresh_verifier"],) * 2,
                         round(math.ceil(parts / 6) * SUBAGENT_SECONDS / 60, 1) if parts else 0),
    }
    if n:
        out["fixes"] = fixes(n)
    return out


def fixes(n: int) -> dict:
    """The paper-readers that check what check_sota.py and the verifiers flag (≤ 10
    requests each, a round per re-check), and the main session reading each re-run's report."""
    p = prompt_tokens("paper_reader")

    def readers(i: int) -> tuple[int, int]:
        req = math.ceil(n * FIX_REQUESTS_PER_PAPER[i])
        per_round = math.ceil(req / FIX_REQUESTS_PER_READER) if req else 0
        return per_round * FIX_ROUNDS[i], req * FIX_ROUNDS[i]

    def inp(i: int) -> int:
        count, requests = readers(i)
        reads = (requests / count) * FIX_READ_CHARS if count else 0
        return count * agent_input(reads, TURNS["paper_reader"][i], p) + CHECK_SOTA_RUNS[i] * CHECK_SOTA_REPORT_TOKENS

    (lo_readers, lo_req), (hi_readers, hi_req) = readers(0), readers(1)
    return _stage(lo_readers, policy_model("paper_reader"), _both(inp),
                  (lo_req * OUT_TOKENS["paper_reader_per_request"], hi_req * OUT_TOKENS["paper_reader_per_request"]),
                  round(FIX_ROUNDS[0] * SUBAGENT_SECONDS / 60, 1), subagents_high=hi_readers,
                  check_sota_runs=CHECK_SOTA_RUNS[0], check_sota_runs_high=CHECK_SOTA_RUNS[1])


def orchestrator(n: int, replies: tuple[int, int]) -> dict:
    """The main session: its system prompt and the skills it follows re-sent on every
    turn, and the subagents' replies accumulating in its context."""
    skills = tokens(skill_chars())

    def turns(i: int) -> int:
        return ORCHESTRATOR_TURNS["base"][i] + ORCHESTRATOR_TURNS["per_paper"][i] * n

    def inp(i: int) -> int:
        t = turns(i)
        return t * (MAIN_SYSTEM_TOKENS + skills) + int(math.ceil(replies[i] * (t + 1) / 2))

    return _stage(0, None, _both(inp), (turns(0) * ORCHESTRATOR_OUT_PER_TURN, turns(1) * ORCHESTRATOR_OUT_PER_TURN),
                  0, turns=turns(0), turns_high=turns(1), skill_tokens=skills)


def assumptions() -> dict:
    return {"CHARS_PER_TOKEN": CHARS_PER_TOKEN, "SUBAGENT_SYSTEM_TOKENS": SUBAGENT_SYSTEM_TOKENS,
            "MAIN_SYSTEM_TOKENS": MAIN_SYSTEM_TOKENS, "TURNS": TURNS, "ORCHESTRATOR_TURNS": ORCHESTRATOR_TURNS,
            "SKILLS": list(SKILLS), "SYNTH_CHECK_CHARS": SYNTH_CHECK_CHARS,
            "FIX_REQUESTS_PER_PAPER": FIX_REQUESTS_PER_PAPER, "FIX_ROUNDS": FIX_ROUNDS,
            "FIX_REQUESTS_PER_READER": FIX_REQUESTS_PER_READER, "FIX_READ_CHARS": FIX_READ_CHARS,
            "CHECK_SOTA_RUNS": CHECK_SOTA_RUNS, "CHECK_SOTA_REPORT_TOKENS": CHECK_SOTA_REPORT_TOKENS,
            "context_growth": "(turns + 1) / 2 × lo leído", "prompt_caching": "no descontado"}


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
    ap.add_argument("--abstract-only", action="store_true",
                    help="the `ligero` template: abstract-only notes, no full text, map or verification")
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
    n = len(a.papers) + a.planned_papers
    if a.abstract_only and n:
        k = a.planned_papers
        stages["ingest"] = {"papers": n, "to_fetch": k, "subagents": 0, "model": None,
                            "input_tokens": 0, "input_tokens_high": 0, "output_tokens": 0, "output_tokens_high": 0,
                            "wall_minutes": round(k * ABSTRACT_ONLY_SECONDS / 60, 1),
                            "wall_minutes_max": round(k * ABSTRACT_ONLY_SECONDS / 60, 1)}
    elif n:
        stages.update(downstream(paper_sizes(a.vault, a.papers, a.planned_papers), len(a.papers), a.sections,
                                 card_sizes(a.vault, a.papers, a.planned_papers)))
    replies = (sum(s["output_tokens"] for s in stages.values()), sum(s["output_tokens_high"] for s in stages.values()))
    stages["orchestrator"] = orchestrator(0 if a.abstract_only else n, replies)
    total = {k: round(sum(s[k] for s in stages.values()), 1) for k in ("input_tokens", "output_tokens",
                                                                       "wall_minutes", "subagents")}
    rng = {k: [round(sum(s[k] for s in stages.values())), round(sum(s[k + "_high"] for s in stages.values()))]
           for k in ("input_tokens", "output_tokens")}
    print(json.dumps({"tool": TOOL, "stages": stages, "total": total, "range": rng,
                      "assumptions": assumptions(),
                      "note": "estimación a partir de tamaños en disco (caracteres / 3.6 por token, factores fijos "
                              "con nombre en `assumptions`; `range` = bajo–alto, sin descontar la caché de prompts); "
                              "no es una medida — la ejecución dirá lo que costó"}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
