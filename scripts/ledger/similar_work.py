#!/usr/bin/env python3
"""Prior work in a Kairo project that overlaps a plan — before designing anything new.

    similar_work.py --vault <vault> --project <slug | PROJ-XXX>
                    [--hypothesis H-XXXX] [--cells M1:a|b,M1:c|d] [--metric <metric_kind>]
                    [--keywords "<words>"] [--json]

At least one of --hypothesis / --cells / --metric / --keywords. With
--hypothesis, its own `frontier_cells`, its claim's words and the metric_kind
of its experiments join the query (each flag given adds to them).

Lists, from the project's own notes:

- **experiments** — every experiment that is this hypothesis's (primary or
  secondary), or whose hypothesis shares a frontier cell or wording with the
  query, or (when the metric is the only criterion) uses that metric_kind
  with
  status, role, verdict and validity;
- **hypotheses** — in the same frontier cells, or with similar claim wording;
- **descartes** — rows of `_digest.md` that hypothesis-cycle discarded before a
  note existed, with similar wording.

The wording comparison is **lexical** (shared words, Jaccard ≥ 0.2, or keyword
hits): it finds the same words, not the same idea. A miss here does not mean
no prior work — read the list and decide; preregister-experiment then reuses
(`collateral_evidence`) or justifies a new experiment in `## Trabajo previo`.

Notes marked `send: never` are skipped (counted, never shown).

Exit codes: 0 ok (found or not) · 2 bad input. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import project_scan as ps  # noqa: E402

__version__ = "1.0.0"
TOOL = f"kairo/similar_work@{__version__}"
LEXICAL_MIN = 0.2
METHOD = ("léxico: palabras compartidas (Jaccard ≥ 0.2 o palabras clave) y casillas de la frontera; "
          "no es similitud semántica")
_H = re.compile(r"^H-\d{4}$")


def keyword_hits(kw: set[str], words: set[str]) -> list[str]:
    return sorted(kw & words)


def enough_keywords(hits: list[str], kw: set[str]) -> bool:
    return bool(hits) and len(hits) >= min(2, len(kw))


def run(vault: Path, project: str, hypothesis: str | None, cells: list[str],
        metric: str | None, keywords: str | None) -> dict:
    pdir, pid = ps.resolve_project(vault, project)
    hyps, skipped_h = ps.hypotheses(pdir)
    exps, skipped_e = ps.experiments(pdir)
    by_h = {h["id"]: h for h in hyps}

    q_cells = list(dict.fromkeys(cells))
    q_claim: set[str] = set()
    q_metrics: set[str] = {metric} if metric else set()
    if hypothesis:
        h = by_h.get(hypothesis)
        if h is None:
            raise ps.InputError(f"{hypothesis} not found in {pdir.name}/Hipotesis (or it is send: never)")
        q_cells += [c for c in h["frontier_cells"] if c not in q_cells]
        q_claim = ps.tokens(h["claim"])
        if not metric:
            q_metrics |= {e["metric_kind"] for e in exps if e["hypothesis"] == hypothesis and e["metric_kind"]}
    kw = ps.tokens(keywords or "")
    only_metric = bool(q_metrics) and not (hypothesis or q_cells or kw)

    def wording(text_tokens: set[str]) -> tuple[float, list[str], list[str]]:
        score = ps.jaccard(q_claim, text_tokens) if q_claim else 0.0
        hits = keyword_hits(kw, text_tokens)
        why = []
        if score >= LEXICAL_MIN:
            why.append(f"léxico {score:.2f} con el claim")
        if enough_keywords(hits, kw):
            why.append("palabras clave: " + ", ".join(hits))
        return score, hits, why

    # hypotheses
    out_h = []
    for h in hyps:
        if h["id"] == hypothesis:
            continue
        shared = [c for c in h["frontier_cells"] if c in q_cells]
        score, hits, why = wording(ps.tokens(h["claim"]))
        if shared:
            why.insert(0, "casillas compartidas: " + ", ".join(shared))
        if why:
            out_h.append({"id": h["id"], "status": h["status"], "claim": h["claim"],
                          "frontier_cells": h["frontier_cells"], "casillas_compartidas": shared,
                          "solapamiento_lexico": round(score, 3), "palabras_clave": hits, "motivos": why,
                          "_rank": 10 * len(shared) + score + 0.1 * len(hits)})
    out_h.sort(key=lambda r: (-r.pop("_rank"), r["id"]))
    related_h = {r["id"]: r for r in out_h}

    # experiments
    out_e = []
    for e in exps:
        why, rank = [], 0.0
        if hypothesis and e["hypothesis"] == hypothesis:
            why.append("adjudica esta misma hipótesis")
            rank += 20
        if hypothesis and hypothesis in e["secondary_hypotheses"]:
            why.append("la toca como hipótesis secundaria")
            rank += 15
        parent = by_h.get(e["hypothesis"] or "")
        if e["hypothesis"] in related_h and e["hypothesis"] != hypothesis:
            why.append(f"su hipótesis {e['hypothesis']} se solapa ({'; '.join(related_h[e['hypothesis']]['motivos'])})")
            rank += 5
        score, hits, wwhy = wording(ps.tokens(e["prediction"]))
        if wwhy:
            why += ["predicción: " + w for w in wwhy]
            rank += score + 0.1 * len(hits)
        same_metric = bool(e["metric_kind"]) and e["metric_kind"] in q_metrics
        if same_metric and (why or only_metric):
            why.append(f"misma métrica ({e['metric_kind']})")
            rank += 1
        if not why:
            continue
        out_e.append({"id": e["id"], "hypothesis": e["hypothesis"],
                      "hypothesis_status": parent["status"] if parent else None,
                      "status": e["status"], "role": e["role"] or "confirmatory", "rung": e["rung"],
                      "metric_kind": e["metric_kind"], "verdict": e["verdict"], "validity": e["validity"],
                      "frontier_cells": parent["frontier_cells"] if parent else [],
                      "motivos": why, "_rank": rank})
    out_e.sort(key=lambda r: (-r.pop("_rank"), r["id"]))

    # digest discards
    out_d = []
    for d in ps.digest_discards(pdir):
        t = ps.tokens(d["claim"] + " " + d["motivo"])
        score, hits, why = wording(t)
        if why:
            out_d.append({"fila": d["fila"], "claim": d["claim"], "estado": d["estado"], "motivo": d["motivo"],
                          "solapamiento_lexico": round(score, 3), "palabras_clave": hits, "motivos": why})

    summary = [f"Trabajo previo en {pid} ({pdir.name}) — {METHOD}."]
    if not (out_e or out_h or out_d):
        summary.append("Sin solapamientos encontrados. Léxico: no prueba que no exista trabajo previo.")
    for e in out_e:
        summary.append(f"  {e['id']} [{e['status'] or '?'}, {e['role']}, veredicto {e['verdict'] or '—'}, "
                       f"validez {e['validity'] or '—'}] → {e['hypothesis'] or '?'}: {'; '.join(e['motivos'])}")
    for h in out_h:
        summary.append(f"  {h['id']} [{h['status']}]: {'; '.join(h['motivos'])}")
    for d in out_d:
        summary.append(f"  descarte del digest «{d['claim'][:80]}»: {'; '.join(d['motivos'])}")
    if skipped_h or skipped_e:
        summary.append(f"  ({skipped_h + skipped_e} nota(s) send: never omitidas)")
    return {
        "tool": TOOL, "project": pid, "slug": pdir.name, "metodo": METHOD,
        "query": {"hypothesis": hypothesis, "cells": q_cells, "metrics": sorted(q_metrics),
                  "keywords": sorted(kw), "claim_tokens": sorted(q_claim)},
        "experiments": out_e, "hypotheses": out_h, "descartes": out_d,
        "omitidas_send_never": skipped_h + skipped_e, "resumen": summary,
    }


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--hypothesis")
    ap.add_argument("--cells", action="append", default=[], help="comma-separated cell ids; repeatable")
    ap.add_argument("--metric")
    ap.add_argument("--keywords")
    ap.add_argument("--json", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    cells = [c.strip() for chunk in a.cells for c in chunk.split(",") if c.strip()]
    if not (a.hypothesis or cells or a.metric or (a.keywords or "").strip()):
        print("similar_work: give at least one of --hypothesis, --cells, --metric, --keywords", file=sys.stderr)
        return 2
    if a.hypothesis and not _H.match(a.hypothesis):
        print("similar_work: --hypothesis must look like H-XXXX", file=sys.stderr)
        return 2
    bad = [c for c in cells if not ps.cells_of([c])]
    if bad:
        print(f"similar_work: cell id(s) not of the form M<n>:<fila>|<col>: {bad}", file=sys.stderr)
        return 2
    try:
        res = run(Path(a.vault), a.project, a.hypothesis, cells, a.metric, a.keywords)
    except ps.InputError as exc:
        print(f"similar_work: {exc}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print("\n".join(res["resumen"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
