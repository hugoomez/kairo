#!/usr/bin/env python3
"""Measure the research quality of a Kairo project — not whether the code runs.

    quality_report.py search --run <lit_search run dir> --gold <gold.json>
    quality_report.py sota   --vault <vault> --project-dir Projects/<slug> [--file Estado-del-arte.md]
    quality_report.py vault  --vault <vault>
    quality_report.py all    --vault <vault> --project-dir Projects/<slug> [--run <dir> --gold <gold.json>]
                         [--log]
    quality_report.py gold-init   --out Projects/<slug>/_eval/gold.json [--bib refs.bib] [--ids arXiv:… DOI:…]
    quality_report.py human-sheet --run <run dir> [--n 30] --out Projects/<slug>/_eval/cribado-humano.md
    quality_report.py human-agree --run <run dir> --sheet Projects/<slug>/_eval/cribado-humano.md

search  Recall of the literature search against a gold set the researcher
        wrote by hand (the papers any competent review of the topic must
        find): how many were identified at all, how many survived dedup and
        were included by the screen, and which were missed — per source, so a
        missing source shows. Gold file: a JSON list of {"doi"|"arxiv"|"title",
        "note"?}. It lives in the vault (e.g. Projects/<slug>/_eval/gold.json),
        never in this repository.
sota    Citation precision of the synthesis: check_sota.py's findings as
        rates — locators that point at no text, figures absent from the cited
        text, citations of conflicting / retracted papers, citations without a
        locator.
vault   Integrity of the paper library: ingest_paper.py verify (source
        sections that no longer match their kept bytes, notes ingested before
        the script), resolve_refs statuses, model reading notes left inside
        paper notes.
all     The three, plus `--log` appends one dated line to
        Projects/<slug>/_eval/historial.jsonl so a regression shows over time.

gold-init    A gold file from identifiers you already trust — your own BibTeX
             / CSL-JSON (e.g. the references of a recent survey you know is
             good) and/or arXiv / DOI ids. Appends, never duplicates.
human-sheet  A blind, reproducible sample of a run's screened candidates
             (half included, half excluded) as a sheet you fill with
             include / exclude — no model decision shown.
human-agree  Your sheet against the screeners: agreement, Cohen's kappa, and
             the papers you would include that the screen excluded.
             `lit_search.py screen` also reports the gold set's recall itself
             when the project has `_eval/gold.json`.

Prints one JSON object. Exit: 0 ok · 1 error · 2 bad input. No network.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import io
import json
import re
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
for sub in ("papers", "citations", "ledger", "security", "search"):
    sys.path.insert(0, str(HERE.parent / sub))
import check_sota  # noqa: E402
import ingest_paper  # noqa: E402
import retraction  # noqa: E402
import vaultnotes as vn  # noqa: E402
from send_guard import is_flagged  # noqa: E402

TOOL = "kairo/quality_report@1.0.0"


def norm_title(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def gold_keys(g: dict) -> set[str]:
    ks = set()
    if g.get("doi"):
        ks.add("doi:" + (retraction.normalize_doi(g["doi"]) or "").lower())
    if g.get("arxiv"):
        ks.add("arxiv:" + (retraction.normalize_arxiv(g["arxiv"]) or "").lower())
    if g.get("title"):
        ks.add("title:" + norm_title(g["title"]))
    return ks - {"doi:", "arxiv:", "title:"}


def cand_keys(c: dict) -> set[str]:
    ks = {"title:" + norm_title(c.get("title", ""))}
    for d in [c.get("doi")] + list((c.get("other_ids") or {}).get("doi", [])):
        if d:
            ks.add("doi:" + d.lower())
    for a in [c.get("arxiv")] + list((c.get("other_ids") or {}).get("arxiv", [])):
        if a:
            ks.add("arxiv:" + a.lower())
    return ks


def eval_search(run: Path, gold_path: Path) -> dict:
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    if not isinstance(gold, list) or not gold:
        raise ValueError("the gold file must be a non-empty JSON list")
    screened = run / "screened.json"
    cands = json.loads((screened if screened.is_file() else run / "candidates.json").read_text(encoding="utf-8"))
    found, included, missed = [], [], []
    by_source: dict[str, int] = {}
    for g in gold:
        gk = gold_keys(g)
        hit = next((c for c in cands if gk & cand_keys(c)), None)
        label = g.get("doi") or g.get("arxiv") or g.get("title")
        if not hit:
            missed.append(label)
            continue
        found.append(label)
        for s in hit.get("sources", []):
            by_source[s] = by_source.get(s, 0) + 1
        if (hit.get("screen") or {}).get("decision") == "include":
            included.append(label)
    n = len(gold)
    inc = sum(1 for c in cands if (c.get("screen") or {}).get("decision") == "include")
    return {"gold": n, "recall_identified": round(len(found) / n, 3),
            "recall_included": round(len(included) / n, 3) if screened.is_file() else None,
            "found_by_source": by_source, "missed": missed,
            "excluded_but_gold": sorted(set(found) - set(included)) if screened.is_file() else None,
            "included_total": inc if screened.is_file() else None,
            "included_not_in_gold": (inc - len(included)) if screened.is_file() else None}


def gold_init(bib: Path | None, ids: list[str], out: Path, note: str | None) -> dict:
    """A gold file from identifiers the researcher already trusts: a BibTeX /
    CSL-JSON export (DOI or arXiv id of each entry; an entry with neither keeps its
    title) and/or `arXiv:<id>` / `DOI:<doi>` strings. Appends to an existing file,
    never duplicating a paper. The researcher's judgement, never a model's."""
    import import_library as il
    rows = []
    if bib:
        text = bib.read_text(encoding="utf-8")
        entries = il.from_csl(json.loads(text)) if bib.suffix.lower() == ".json" else il.parse_bibtex(text)
        for e in entries:
            kind, value = il.identifier(e)
            row = {kind: value} if kind else ({"title": e.get("title")} if e.get("title") else None)
            if row:
                rows.append({**row, "note": note or f"de {bib.name}"})
    for s in ids:
        kind, _, value = s.partition(":")
        if kind.lower() not in ("arxiv", "doi") or not value:
            raise ValueError(f"{s}: use arXiv:<id> or DOI:<doi>")
        rows.append({kind.lower(): value, "note": note or "a mano"})
    gold = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else []
    seen = set().union(*(gold_keys(g) for g in gold)) if gold else set()
    added = 0
    for r in rows:
        k = gold_keys(r)
        if k and not (k & seen):
            gold.append(r)
            seen |= k
            added += 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(gold, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    return {"gold": out.as_posix(), "added": added, "total": len(gold)}


def human_sheet(run: Path, n: int, out: Path) -> dict:
    """A reproducible sample of the screened candidates for the researcher to
    decide blind: titles and abstracts, no model decision shown. Half from the
    includes, half from the excludes (the prefiltered-out ones count as excludes),
    chosen by the sha256 of the plan's description and the key."""
    import hashlib
    cands = json.loads((run / "screened.json").read_text(encoding="utf-8"))
    seed = json.loads((run / "plan.json").read_text(encoding="utf-8"))["description"]
    order = lambda c: hashlib.sha256(f"{seed}\n{c['key']}".encode()).hexdigest()  # noqa: E731
    inc = sorted((c for c in cands if (c.get("screen") or {}).get("decision") == "include"), key=order)
    exc = sorted((c for c in cands if (c.get("screen") or {}).get("decision") != "include"), key=order)
    take = inc[:n // 2] + exc[:n - min(len(inc), n // 2)]
    take = sorted(take, key=order)[:n]
    L = ["# Cribado a ciegas — muestra para medir al screener", "",
         "Decide cada candidato con los criterios del plan, sin mirar `ranked.md`. Escribe `include` o `exclude` "
         "en la columna *decisión* de la tabla (nada más). Después: `quality_report.py human-agree`.", "",
         f"**Plan:** {seed}", "", "| clave | decisión |", "|---|---|"]
    L += [f"| `{c['key']}` |  |" for c in take]
    L += ["", "## Candidatos", ""]
    for c in take:
        L += [f"### `{c['key']}`", "", f"**{c.get('title')}** ({c.get('year') or 's. f.'})", "",
              (c.get("abstract") or "*(sin abstract)*"), ""]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n", encoding="utf-8", newline="\n")
    return {"sheet": out.as_posix(), "sample": len(take), "of_includes": len(inc[:n // 2]),
            "note": "texto de terceros: el investigador lo lee en el fichero; la sesión no lo imprime"}


def human_agree(run: Path, sheet: Path) -> dict:
    """The researcher's blind decisions against the screeners': agreement, Cohen's
    kappa, and — what matters most for a review — the papers the researcher would
    include that the screen excluded (the screener's misses)."""
    rows = re.findall(r"(?m)^\|\s*`([^`]+)`\s*\|\s*(include|exclude)?\s*\|", sheet.read_text(encoding="utf-8"))
    human = {k: d for k, d in rows if d}
    if not human:
        raise ValueError("the sheet has no decision filled in")
    cands = {c["key"]: c for c in json.loads((run / "screened.json").read_text(encoding="utf-8"))}
    keys = sorted(k for k in human if k in cands)
    model = {k: (cands[k].get("screen") or {}).get("decision") == "include" for k in keys}
    hum = {k: human[k] == "include" for k in keys}
    n = len(keys)
    po = sum(model[k] == hum[k] for k in keys) / n
    pm, ph = sum(model.values()) / n, sum(hum.values()) / n
    pe = pm * ph + (1 - pm) * (1 - ph)
    missed = [k for k in keys if hum[k] and not model[k]]
    return {"compared": n, "unfilled": len(rows) - len(human), "agreement": round(po, 3),
            "kappa": None if pe == 1 else round((po - pe) / (1 - pe), 4),
            "screen_missed": missed, "screen_extra": [k for k in keys if model[k] and not hum[k]],
            "miss_rate": round(len(missed) / max(1, sum(hum.values())), 3),
            "reading": "kappa < 0.6: criterios ambiguos o screener poco fiable; screen_missed son los papers que "
                       "el investigador incluiría y el cribado dejó fuera"}


def eval_sota(vault: Path, project_dir: str, file: str) -> dict:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        check_sota.main(["--vault", str(vault), "--project-dir", project_dir, "--file", file])
    rep = json.loads(buf.getvalue())
    if "error" in rep:
        raise ValueError(rep["error"])
    probs = rep["problems"]
    n = max(rep["citations"], 1)
    locator = [p for p in probs if "no señala texto" in p["reason"] or "no existe" in p["reason"]]
    number = [p for p in probs if p.get("number")]
    bad_ref = [p for p in probs if "conflicto" in p["reason"] or "retirado" in p["reason"]
               or "retractado" in p["reason"]]
    return {"citations": rep["citations"], "locator_error_rate": round(len(locator) / n, 3),
            "numbers_checked": rep["numbers_checked"],
            "number_error_rate": round(len(number) / max(rep["numbers_checked"], 1), 3),
            "bad_reference_citations": len(bad_ref), "without_locator": len(rep["without_locator"]),
            "provenance_problems": sum(1 for p in probs if "no es del paper" in p["reason"]),
            "problems": len(probs)}


def eval_vault(vault: Path) -> dict:
    integrity: dict[str, int] = {}
    for p in ingest_paper.paper_notes(vault):
        st = ingest_paper.verify_note(vault, p)["status"]
        integrity[st] = integrity.get(st, 0) + 1
    statuses: dict[str, int] = {}
    reading_notes_inside = 0
    for p in ingest_paper.paper_notes(vault):
        if is_flagged(p):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        fm = (vn.split_frontmatter(text) or ([], ""))[0]
        s = vn.fm_get(fm, "resolution_status") or "sin comprobar"
        statuses[s] = statuses.get(s, 0) + 1
        if re.search(r"^## Notas de lectura", text, re.M):
            reading_notes_inside += 1
    return {"papers": sum(integrity.values()), "integrity": integrity, "resolution": statuses,
            "model_notes_inside_paper_notes": reading_notes_inside}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("--run", type=Path, required=True)
    s.add_argument("--gold", type=Path, required=True)
    so = sub.add_parser("sota")
    so.add_argument("--vault", type=Path, required=True)
    so.add_argument("--project-dir", required=True)
    so.add_argument("--file", default="Estado-del-arte.md")
    v = sub.add_parser("vault")
    v.add_argument("--vault", type=Path, required=True)
    al = sub.add_parser("all")
    al.add_argument("--vault", type=Path, required=True)
    al.add_argument("--project-dir", required=True)
    al.add_argument("--file", default="Estado-del-arte.md")
    al.add_argument("--run", type=Path)
    al.add_argument("--gold", type=Path)
    al.add_argument("--log", action="store_true")
    gi = sub.add_parser("gold-init", help="a gold file from identifiers you trust (.bib / CSL-JSON / ids)")
    gi.add_argument("--out", type=Path, required=True, help="e.g. Projects/<slug>/_eval/gold.json (in the vault)")
    gi.add_argument("--bib", type=Path)
    gi.add_argument("--ids", nargs="*", default=[], help="arXiv:<id> / DOI:<doi>")
    gi.add_argument("--note")
    hs = sub.add_parser("human-sheet", help="a blind sample of screened candidates for you to decide")
    hs.add_argument("--run", type=Path, required=True)
    hs.add_argument("--n", type=int, default=30)
    hs.add_argument("--out", type=Path, required=True)
    ha = sub.add_parser("human-agree", help="your blind decisions against the screeners'")
    ha.add_argument("--run", type=Path, required=True)
    ha.add_argument("--sheet", type=Path, required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "search":
            out = eval_search(a.run, a.gold)
        elif a.cmd == "gold-init":
            if not a.bib and not a.ids:
                raise ValueError("give --bib and/or --ids")
            out = gold_init(a.bib, a.ids, a.out, a.note)
        elif a.cmd == "human-sheet":
            out = human_sheet(a.run, a.n, a.out)
        elif a.cmd == "human-agree":
            out = human_agree(a.run, a.sheet)
        elif a.cmd == "sota":
            out = eval_sota(a.vault, a.project_dir, a.file)
        elif a.cmd == "vault":
            out = eval_vault(a.vault)
        else:
            out = {"date": _dt.date.today().isoformat(), "project_dir": a.project_dir,
                   "sota": eval_sota(a.vault, a.project_dir, a.file), "vault": eval_vault(a.vault)}
            if a.run and a.gold:
                out["search"] = eval_search(a.run, a.gold)
            if a.log:
                d = a.vault / a.project_dir / "_eval"
                d.mkdir(parents=True, exist_ok=True)
                with (d / "historial.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
                    fh.write(json.dumps({"tool": TOOL, **out}, ensure_ascii=False) + "\n")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as e:
        print(json.dumps({"tool": TOOL, "error": str(e)}, ensure_ascii=False))
        return 2 if isinstance(e, ValueError) else 1
    print(json.dumps({"tool": TOOL, **out}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
