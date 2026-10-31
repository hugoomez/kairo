#!/usr/bin/env python3
"""Measured bases for estimating what testing a hypothesis will cost.

    hypothesis_cost.py --vault <vault> [--project <slug | PROJ-XXX>]
                       [--hypothesis H-XXXX] [--metric <metric_kind>] [--top 5] [--json]

hypothesis-cycle's «¿Merece la pena?» step grounds `coste_base` on this. It
gathers, per experiment of the vault (or of --project), only what was
recorded:

  - wall time of its runs from the full-trace index
    (`Projects/<slug>/Experimentos/trazas/index.jsonl`, scripts/traces/
    trace_index.py): each run's latest entry, `duration_s` or `ended_at −
    started_at`
    runs without both times are counted, never timed;
  - number of runs, their outcomes, seeds and conditions (`cell`);
  - `cost_estimated` / `cost_actual` and `environment.hardware` as written in
    the note, and the `estimado X → real Y` line of its `## Resultado`;
  - whether its full log `Experimentos/logs/E-XXXX.log` exists (path and size;
    the log is not parsed);
  - status, role, rung, metric_kind;

grouped by project, and the hub's `compute_budget`, `completo_cost_threshold`
and `ladder_cost_threshold` (null when still `<value>`).

With --hypothesis, the past experiments are ranked by similarity to it: same
project (+3), each shared frontier cell (+2), same metric_kind (+2, from
--metric or from the hypothesis's own experiments), and the lexical overlap
of the claims (Jaccard, +0–1). Its own experiments are excluded. The
`coste_base` suggestion quotes the most similar experiment with measured wall
time; with none, it says «sin base medida».

It never invents a figure: nothing here is extrapolated. Scaling a measured
time to a new design is the skill's reasoning, written as `estimado: …` with
its assumption.

A broken trace chain is reported (`trace_problems`), and its entries are still
read — a broken chain is a reason to audit, not a reason to hide the times.
Notes marked `send: never` are skipped.

Exit codes: 0 ok (with or without a base) · 2 bad input. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ledger"))
sys.path.insert(0, str(HERE.parent / "traces"))
import project_scan as ps  # noqa: E402
import trace_index as ti  # noqa: E402

__version__ = "1.0.0"
TOOL = f"kairo/hypothesis_cost@{__version__}"
NO_BASE = "sin base medida"
BUDGET_KEYS = ("compute_budget", "completo_cost_threshold", "ladder_cost_threshold")
_H = re.compile(r"^H-\d{4}$")
_EST_REAL = re.compile(r"estimad[oa]\s+.+?→\s*real\s+.+", re.IGNORECASE)


def _iso(s: object) -> datetime | None:
    if not isinstance(s, str) or not ti._ISO.match(s):
        return None
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


def read_traces(pdir: Path) -> tuple[list[dict], list[str]]:
    """Entries of the project's trace index, read leniently; plus chain problems."""
    path = pdir / "Experimentos" / "trazas" / "index.jsonl"
    if not path.is_file():
        return [], []
    try:
        lines = ti.read_lines(path)
        problems = ti.chain_problems(lines)
    except (ti.TraceError, OSError, UnicodeDecodeError) as exc:
        try:
            lines = [ln for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        except OSError:
            return [], [f"{path.name}: unreadable ({exc})"]
        problems = [str(exc)]
    entries = []
    for ln in lines:
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("event") in ti.EVENTS and obj.get("run_id"):
            entries.append(obj)
    return entries, [f"{pdir.name}: {p}" for p in problems[:5]]


def run_stats(entries: list[dict]) -> dict[str, dict]:
    """Per experiment: runs, outcomes, seeds, conditions, measured wall seconds."""
    # a run's state: its last entry with a final outcome (an end, or a correction
    # of one), else its last entry — a correction of the start never hides the end
    state: dict[str, dict] = {}
    for e in entries:
        rid = e["run_id"]
        if e.get("outcome") != "running" or state.get(rid, {}).get("outcome") in (None, "running"):
            state[rid] = e
    starts = {e["run_id"]: e for e in entries if e.get("outcome") == "running" and e.get("started_at")}
    per: dict[str, dict] = {}
    for rid, e in state.items():
        eid = str(e.get("experiment", "")).split("-")[0:2]
        eid = "-".join(eid)
        s = per.setdefault(eid, {"runs": 0, "outcomes": {}, "seeds": set(), "conditions": set(),
                                 "durations_s": [], "untimed_runs": 0})
        s["runs"] += 1
        out = e.get("outcome") or "unknown"
        s["outcomes"][out] = s["outcomes"].get(out, 0) + 1
        seeds = e.get("seeds")
        if isinstance(seeds, list):
            s["seeds"].update(seeds)
        for k in ("cell", "condition"):
            if isinstance(e.get(k), str) and e[k]:
                s["conditions"].add(e[k])
        dur = e.get("duration_s")
        if not isinstance(dur, (int, float)) or isinstance(dur, bool) or dur < 0:
            a = _iso(e.get("started_at")) or _iso((starts.get(rid) or {}).get("started_at"))
            b = _iso(e.get("ended_at"))
            dur = (b - a).total_seconds() if a and b and b >= a else None
        if dur is None or out == "running":
            s["untimed_runs"] += 1
        else:
            s["durations_s"].append(float(dur))
    return per


def human_time(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    if seconds < 172800:
        return f"{seconds / 3600:.1f} h"
    return f"{seconds / 86400:.1f} días"


def project_bases(vault: Path, pdir: Path) -> tuple[dict, list[dict], list[str], int]:
    pid = ps.hub_id(pdir) or pdir.name
    hub = ps.hub_fm(pdir)
    budget = {k: ps.scalar(hub, k) for k in BUDGET_KEYS}
    exps, skipped = ps.experiments(pdir)
    hyps, _ = ps.hypotheses(pdir)
    cells = {h["id"]: h["frontier_cells"] for h in hyps}
    claims = {h["id"]: h["claim"] for h in hyps}
    entries, problems = read_traces(pdir)
    stats = run_stats(entries)
    rows = []
    for e in exps:
        st = stats.get(e["id"], {"runs": 0, "outcomes": {}, "seeds": set(), "conditions": set(),
                                 "durations_s": [], "untimed_runs": 0})
        d = st["durations_s"]
        total = sum(d) if d else None
        log = pdir / "Experimentos" / "logs" / f"{e['id']}.log"
        er = _EST_REAL.search(e["result_section"] or "")
        measured = bool(d)
        recorded = bool(e["cost_actual"] or er)
        rows.append({
            "id": e["id"], "project": pid, "hypothesis": e["hypothesis"], "status": e["status"],
            "role": e["role"] or "confirmatory", "rung": e["rung"], "metric_kind": e["metric_kind"],
            "frontier_cells": cells.get(e["hypothesis"] or "", []),
            "hardware": e["hardware"], "runtime": e["runtime"],
            "cost_estimated": e["cost_estimated"], "cost_actual": e["cost_actual"],
            "estimado_real": er.group(0).strip() if er else None,
            "runs": st["runs"], "outcomes": st["outcomes"], "seeds": len(st["seeds"]),
            "conditions": sorted(st["conditions"]), "timed_runs": len(d), "untimed_runs": st["untimed_runs"],
            "wall_seconds_total": round(total, 1) if total is not None else None,
            "wall_seconds_median_run": round(statistics.median(d), 1) if d else None,
            "log": {"path": str(log.relative_to(vault)).replace("\\", "/"), "bytes": log.stat().st_size}
                   if log.is_file() else None,
            "basis": "medido" if measured else "registrado" if recorded else NO_BASE,
            "_claim": claims.get(e["hypothesis"] or "", ""),
        })
    return {"project": pid, "slug": pdir.name, **budget,
            "sin_definir": [k for k, v in budget.items() if v is None]}, rows, problems, skipped


def describe(r: dict) -> str:
    if r["basis"] == "medido":
        parts = [f"{r['timed_runs']} run(s) cronometrado(s), total {human_time(r['wall_seconds_total'])}"]
        if r["timed_runs"] > 1:
            parts.append(f"mediana {human_time(r['wall_seconds_median_run'])}/run")
        if r["seeds"]:
            parts.append(f"{r['seeds']} semilla(s)")
        if r["conditions"]:
            parts.append(f"{len(r['conditions'])} condición(es)")
        if r["hardware"]:
            parts.append(r["hardware"])
        return f"medido: {r['id']} — " + ", ".join(parts) + " (trazas/index.jsonl)"
    if r["basis"] == "registrado":
        bits = [f"cost_actual {r['cost_actual']}"] if r["cost_actual"] else []
        if r["estimado_real"]:
            bits.append(f"«{r['estimado_real']}»")
        return f"registrado en la nota: {r['id']} — " + "; ".join(bits) + " (sin tiempos en las trazas)"
    return NO_BASE


def rank(rows: list[dict], hyp: dict, pid: str, metrics: set[str]) -> list[dict]:
    q_tokens = ps.tokens(hyp["claim"])
    out = []
    for r in rows:
        if r["hypothesis"] == hyp["id"]:
            continue
        score, why = 0.0, []
        if r["project"] == pid:
            score += 3
            why.append("mismo proyecto")
        shared = [c for c in r["frontier_cells"] if c in hyp["frontier_cells"]]
        if shared:
            score += 2 * len(shared)
            why.append("casillas compartidas: " + ", ".join(shared))
        if r["metric_kind"] and r["metric_kind"] in metrics:
            score += 2
            why.append(f"misma métrica ({r['metric_kind']})")
        lex = ps.jaccard(q_tokens, ps.tokens(r["_claim"]))
        if lex > 0:
            score += lex
            why.append(f"léxico {lex:.2f} entre claims")
        out.append({"id": r["id"], "project": r["project"], "score": round(score, 3), "motivos": why,
                    "basis": r["basis"], "base": describe(r)})
    out.sort(key=lambda x: (-x["score"], x["basis"] != "medido", x["id"]))
    return out


def run(vault: Path, project: str | None, hypothesis: str | None, metric: str | None, top: int) -> dict:
    if not (vault / "Projects").is_dir():
        raise ps.InputError(f"{vault} has no Projects/ folder")
    target = None
    if project:
        target = ps.resolve_project(vault, project)
    pdirs = [target[0]] if target else ps.all_projects(vault)
    hyp = None
    if hypothesis:
        home = None
        for pd in ps.all_projects(vault):
            hs, _ = ps.hypotheses(pd)
            hit = next((h for h in hs if h["id"] == hypothesis), None)
            if hit:
                hyp, home = hit, pd
                break
        if hyp is None:
            raise ps.InputError(f"{hypothesis} not found under Projects/*/Hipotesis (or it is send: never)")
        if home not in pdirs:
            pdirs.append(home)
    projects, rows, problems, skipped = [], [], [], 0
    for pd in pdirs:
        b, r, p, s = project_bases(vault, pd)
        projects.append(b)
        rows += r
        problems += p
        skipped += s
    similar = []
    suggestion = NO_BASE
    if hyp:
        hpid = ps.hub_id(home) or home.name
        metrics = {metric} if metric else {r["metric_kind"] for r in rows
                                           if r["hypothesis"] == hyp["id"] and r["metric_kind"]}
        similar = rank(rows, hyp, hpid, metrics)
        best = next((s for s in similar if s["basis"] == "medido" and s["score"] >= 3), None) \
            or next((s for s in similar if s["basis"] == "registrado" and s["score"] >= 3), None)
        if best:
            suggestion = best["base"]
        similar = similar[:top]
    else:
        measured = [r for r in rows if r["basis"] == "medido"]
        if measured:
            suggestion = f"{len(measured)} experimento(s) con tiempo medido; elegir el más parecido con --hypothesis"
    for r in rows:
        r.pop("_claim", None)
        r["base"] = describe(r)
    n_measured = sum(1 for r in rows if r["basis"] == "medido")
    summary = [f"Bases de coste ({', '.join(p['project'] for p in projects) or 'ningún proyecto'}): "
               f"{n_measured} de {len(rows)} experimento(s) con tiempo medido en las trazas."]
    for p in projects:
        summary.append(f"  {p['project']}: compute_budget {p['compute_budget'] or 'sin definir'}; "
                       f"completo_cost_threshold {p['completo_cost_threshold'] or 'sin definir'}; "
                       f"ladder_cost_threshold {p['ladder_cost_threshold'] or 'sin definir'}")
    if hyp:
        summary.append(f"  Más parecidos a {hyp['id']}:")
        summary += [f"    {s['id']} ({s['score']}: {'; '.join(s['motivos']) or '—'}) → {s['base']}" for s in similar] \
            or ["    ninguno"]
    else:
        summary += [f"  {r['base']}" for r in rows if r["basis"] != NO_BASE]
    summary.append(f"  coste_base sugerido: {suggestion}")
    if problems:
        summary.append("  AVISO trazas: " + "; ".join(problems[:3]))
    return {"tool": TOOL, "projects": projects, "experiments": rows, "hypothesis": hypothesis,
            "similar": similar, "coste_base_sugerido": suggestion, "trace_problems": problems,
            "omitidas_send_never": skipped, "resumen": summary}


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", required=True)
    ap.add_argument("--project")
    ap.add_argument("--hypothesis")
    ap.add_argument("--metric")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--json", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    if a.hypothesis and not _H.match(a.hypothesis):
        print("hypothesis_cost: --hypothesis must look like H-XXXX", file=sys.stderr)
        return 2
    try:
        res = run(Path(a.vault), a.project, a.hypothesis, a.metric, max(1, a.top))
    except ps.InputError as exc:
        print(f"hypothesis_cost: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(res, ensure_ascii=False, indent=2) if a.json else "\n".join(res["resumen"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
