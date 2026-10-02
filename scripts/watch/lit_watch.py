#!/usr/bin/env python3
"""Literature watch: what is new since the last watch, and does it threaten a hypothesis?

    lit_watch.py delta   --vault <vault> --project-dir <dir> [--since YYYY-MM-DD] [--top 10]
    lit_watch.py triage  --project-dir <dir> --run <file> --key <k> --why "<one line>"
    lit_watch.py threat  --vault <vault> --project-dir <dir> --run <file> --key <k>
                         --hypothesis H-XXXX --sentence "<verbatim from the abstract>"
                         --severity crítico|importante|menor
                         --judgement "<one line>" [--model <id>]
    lit_watch.py check   --project-dir <dir> --run <file>
    lit_watch.py decide  --project-dir <dir> --run <file> --key <k> --decision ingerir|descartar
                         [--reason "..."] [--by <name>]
    lit_watch.py threat-decide --vault <vault> --project-dir <dir> --run <file> --key <k>
                         --hypothesis H-XXXX --decision no_afecta|revisar --reason "..." --by <name>

`delta` (mechanical, network):
  - Re-runs the project's own recorded queries — every row of the
    "Consultas (verbatim)" tables in `Estado-del-arte.md`'s
    `### Búsqueda ejecutada` blocks — restricted to papers since the last
    watch: arXiv with `submittedDate:[since TO today]`, Semantic Scholar with
    `publicationDateOrYear=since:`. `SEMANTIC_SCHOLAR_API_KEY` is used when set.
  - Drops papers already in `Papers/` (arXiv id, DOI, normalised title) and
    papers offered by an earlier watch of this project.
  - A candidate is *strong* when queries of two or more facets found it (or the
    project has one facet). The top `--top` strong candidates are marked for
    triage; the rest are listed, not surfaced.
  - Each candidate keeps `abstract_sha256`, the hash of the abstract exactly
    as the source returned it, so a later threat can only quote that text.
  - Novelty prefilter: word overlap between each active hypothesis's (not
    `refutada`, not `descartada`, not `send: never`)
    `## Claim` and each candidate's title + abstract. Only ids and scores are
    stored — the claim text never leaves the note.
  - Writes `<project>/_vigilancia/vigilancia-<date>[-n].json` and sets the hub's
    `last_watch:` — unless every query failed, in which case nothing moves and
    the run says so.

`threat` records a novelty threat, which is a model's judgement and not
evidence:
  - `--sentence` must appear verbatim (whitespace aside) in the candidate's
    abstract — the abstract's own words, never the title or a summary — or
    the threat is refused. A candidate without an abstract cannot carry a
    threat. If the abstract no longer matches `abstract_sha256`, the run file
    was edited and the threat is refused. (This catches an accidental or
    model-made edit; whoever can edit the run file can also recompute the
    hash, so it is not a security boundary. Runs written before the hash
    existed carry none and skip this check.)
  - `--severity` is required: `crítico` (the abstract reports the same claim:
    same effect, same kind of system, same direction), `importante` (a close
    result that narrows what is new), `menor` (adjacent; to cite, novelty
    intact).
  - One dated line goes into the hypothesis's `## Revisión de vigencia`, and
    the threat is recorded in the run file.
  - Never touches `status`, confidence or any frontmatter.

`decide` / `threat-decide` record the researcher's decision on a candidate or
a threat. A threat decision appends one more dated line to the hypothesis.

`check` says whether a run is complete before it is committed: every
triaged candidate has its one-line reason and every threat has a severity and
a verbatim sentence. Exit 3 when something is missing (listed in `missing`).

Prints one JSON object. Exit codes: 0 ok · 2 refused (bad input) · 3 run
incomplete (`check`) · 1 error.
Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "citations"))
sys.path.insert(0, str(HERE.parent / "security"))
import net  # noqa: E402
from send_guard import is_flagged, is_model_notes  # noqa: E402
from vaultnotes import (  # noqa: E402
    append_revision_line,
    fm_get,
    read_text,
    set_fields,
    split_frontmatter,
    write_text,
)

TOOL = "kairo/lit_watch@1.2.0"
SEVERITIES = ("crítico", "importante", "menor")
INACTIVE = ("refutada", "descartada")
ATOM = "{http://www.w3.org/2005/Atom}"
S2_FIELDS = "title,abstract,authors,year,externalIds,publicationDate,venue,citationCount,url"
MAX_RESULTS = 50
DEFAULT_LOOKBACK_DAYS = 30
NOVELTY_MIN_SCORE = 0.25
NOVELTY_MIN_SHARED = 3
STOP = set("""
the and for with that this from are was were been have has into over under than then them they their there these those
which while where when what about also such only more most very using used use based between among within without upon
del las los una uno unos unas por para con sin sobre entre desde hasta como cuando donde este esta estos estas ese esa
esos esas que qué más menos muy también solo sólo cada otro otra otros otras sus son ser está están fue han hay puede
pueden mediante según tras ante bajo cual cuales cuyo cuya
""".split())

Fetch = Callable[[str, dict], bytes]


class Refused(Exception):
    pass


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def sha256(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-záéíóúñü][a-záéíóúñü0-9-]{3,}", (s or "").lower()) if w not in STOP}


def section(body: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}\s*$(.*?)(?=^## |\Z)", body, re.MULTILINE | re.DOTALL)
    return m.group(1).strip() if m else ""


def hub_path(pdir: Path) -> Path:
    return pdir / "_hub.md"


def hub_field(pdir: Path, key: str) -> str | None:
    text, _ = read_text(hub_path(pdir))
    parts = split_frontmatter(text)
    return fm_get(parts[0], key) if parts else None


def load_run(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_run(path: Path, run: dict) -> None:
    path.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def find_candidate(run: dict, key: str) -> dict:
    for c in run["candidates"]:
        if c["key"] == key:
            return c
    raise Refused(f"no candidate {key} in this run")


# --------------------------------------------------------------------------
# The project's recorded queries
# --------------------------------------------------------------------------

def recorded_queries(pdir: Path) -> list[dict]:
    """Every (facet, source, query) row of the Consultas tables, deduplicated."""
    f = pdir / "Estado-del-arte.md"
    if not f.is_file():
        return []
    text, _ = read_text(f)
    out: list[dict] = []
    seen = set()
    for block in re.split(r"^#{2,4} Búsqueda ejecutada", text, flags=re.MULTILINE)[1:]:
        m = re.search(r"\*\*Consultas \(verbatim\):\*\*\s*\n(.*?)(?:\n\s*\n|\Z)", block, re.DOTALL)
        if not m:
            continue
        for row in m.group(1).split("\n"):
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            if len(cells) < 3 or set(cells[0]) <= set("-: ") or cells[0].lower() == "faceta":
                continue
            facet, source, query = cells[0], cells[1].lower(), cells[2].strip().strip("`").strip()
            if "arxiv" in source:
                src = "arxiv"
            elif "semantic" in source or source.startswith("s2"):
                src = "s2"
            else:
                continue  # vault, PatentsView: not watched
            if not query or query in ("…", "..."):
                continue
            k = (facet, src, query)
            if k not in seen:
                seen.add(k)
                out.append({"facet": facet, "source": src, "query": query})
    return out


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------

def default_fetch(url: str, headers: dict) -> bytes:
    return net.get(url, headers=headers)


def arxiv_delta(q: str, since: date, until: date, fetch: Fetch) -> list[dict]:
    query = urllib.parse.unquote_plus(q) if "%" in q else q
    sq = f"({query}) AND submittedDate:[{since:%Y%m%d}0000 TO {until:%Y%m%d}2359]"
    url = ("https://export.arxiv.org/api/query?search_query=" + urllib.parse.quote(sq, safe="")
           + f"&start=0&max_results={MAX_RESULTS}&sortBy=submittedDate&sortOrder=descending")
    root = ET.fromstring(fetch(url, {}))
    out = []
    for e in root.findall(f"{ATOM}entry"):
        aid = (e.findtext(f"{ATOM}id") or "").rsplit("/abs/", 1)[-1]
        aid = re.sub(r"v\d+$", "", aid)
        doi = e.findtext("{http://arxiv.org/schemas/atom}doi") or ""
        out.append({
            "arxiv": aid or None, "doi": doi.lower() or None,
            "title": ws(e.findtext(f"{ATOM}title") or ""),
            "abstract": ws(e.findtext(f"{ATOM}summary") or ""),
            "authors": [ws(a.findtext(f"{ATOM}name") or "") for a in e.findall(f"{ATOM}author")][:8],
            "date": (e.findtext(f"{ATOM}published") or "")[:10] or None,
            "url": f"https://arxiv.org/abs/{aid}" if aid else None,
            "venue": "arXiv preprint", "citations": None,
        })
    return out


def s2_delta(q: str, since: date, fetch: Fetch) -> list[dict]:
    url = ("https://api.semanticscholar.org/graph/v1/paper/search?query=" + urllib.parse.quote(q)
           + f"&publicationDateOrYear={since.isoformat()}:&limit={MAX_RESULTS}&fields={S2_FIELDS}")
    headers = {}
    if os.environ.get("SEMANTIC_SCHOLAR_API_KEY"):
        headers["x-api-key"] = os.environ["SEMANTIC_SCHOLAR_API_KEY"]
    data = json.loads(fetch(url, headers))
    out = []
    for p in data.get("data") or []:
        ext = p.get("externalIds") or {}
        pub = p.get("publicationDate")
        if pub and pub < since.isoformat():
            continue
        out.append({
            "arxiv": ext.get("ArXiv"), "doi": (ext.get("DOI") or "").lower() or None,
            "s2": p.get("paperId"),
            "title": ws(p.get("title") or ""), "abstract": ws(p.get("abstract") or ""),
            "authors": [a.get("name", "") for a in (p.get("authors") or [])][:8],
            "date": pub or (str(p["year"]) if p.get("year") else None),
            "url": p.get("url"), "venue": p.get("venue") or None, "citations": p.get("citationCount"),
        })
    return out


def cand_key(c: dict) -> str:
    if c.get("arxiv"):
        return f"arxiv:{c['arxiv']}"
    if c.get("doi"):
        return f"doi:{c['doi']}"
    if c.get("s2"):
        return f"s2:{c['s2']}"
    return "title:" + norm_title(c["title"])[:80]


# --------------------------------------------------------------------------
# What we already have
# --------------------------------------------------------------------------

def known_papers(vault: Path) -> tuple[set[str], set[str], set[str]]:
    arx, dois, titles = set(), set(), set()
    for p in (vault / "Papers").glob("*.md"):
        if is_model_notes(p.resolve()) or is_flagged(p):
            continue
        text, _ = read_text(p)
        parts = split_frontmatter(text)
        if not parts:
            continue
        fm = parts[0]
        if a := (fm_get(fm, "arxiv") or "").strip():
            arx.add(re.sub(r"v\d+$", "", a.lower()))
        if d := (fm_get(fm, "doi") or "").strip():
            dois.add(d.lower())
        if t := (fm_get(fm, "title") or "").strip():
            titles.add(norm_title(t))
    return arx, dois, titles


def earlier_keys(pdir: Path) -> set[str]:
    out = set()
    for f in (pdir / "_vigilancia").glob("vigilancia-*.json"):
        try:
            out |= {c["key"] for c in load_run(f).get("candidates", [])}
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    return out


def hypotheses(pdir: Path) -> list[tuple[str, set[str]]]:
    out = []
    for f in sorted((pdir / "Hipotesis").glob("H-*.md")):
        if is_flagged(f):
            continue
        text, _ = read_text(f)
        parts = split_frontmatter(text)
        if not parts:
            continue
        fm, body = parts
        if (fm_get(fm, "status") or "") in INACTIVE:
            continue
        hid = fm_get(fm, "id") or f.stem.split(" ")[0]
        out.append((hid, words(section(body, "Claim"))))
    return out


# --------------------------------------------------------------------------
# delta
# --------------------------------------------------------------------------

def run_path(pdir: Path, today: date) -> Path:
    folder = pdir / "_vigilancia"
    folder.mkdir(exist_ok=True)
    stem = f"vigilancia-{today.isoformat()}"
    p, n = folder / f"{stem}.json", 2
    while p.exists():
        p, n = folder / f"{stem}-{n}.json", n + 1
    return p


def delta(vault: Path, pdir: Path, since: date | None, top: int, fetch: Fetch, today: date) -> dict:
    if not hub_path(pdir).is_file():
        raise Refused(f"{pdir} is not a project folder (no _hub.md)")
    queries = recorded_queries(pdir)
    if not queries:
        raise Refused("Estado-del-arte.md has no recorded queries (Búsqueda ejecutada → Consultas) to re-run")
    if since is None:
        lw = (hub_field(pdir, "last_watch") or "").strip()
        since = date.fromisoformat(lw) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", lw) else today - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    arx, dois, titles = known_papers(vault)
    seen_before = earlier_keys(pdir)
    facets_all = {q["facet"] for q in queries}
    merged: dict[str, dict] = {}
    by_title: dict[str, str] = {}
    log = []
    for q in queries:
        try:
            got = arxiv_delta(q["query"], since, today, fetch) if q["source"] == "arxiv" else s2_delta(q["query"], since, fetch)
            log.append({**q, "hits": len(got), "error": None})
        except (net.HttpError, ET.ParseError, json.JSONDecodeError, ValueError) as exc:
            log.append({**q, "hits": None, "error": net.redact(str(exc))[:200]})
            continue
        for c in got:
            if not c["title"]:
                continue
            k = cand_key(c)
            t = norm_title(c["title"])
            k = by_title.get(t, k)
            cur = merged.setdefault(k, {**c, "key": k, "facets": [], "sources": []})
            by_title.setdefault(t, k)
            for field in ("abstract", "doi", "arxiv", "url", "citations"):
                if not cur.get(field) and c.get(field):
                    cur[field] = c[field]
            if q["facet"] not in cur["facets"]:
                cur["facets"].append(q["facet"])
            if q["source"] not in cur["sources"]:
                cur["sources"].append(q["source"])
    lost_all = all(x["error"] for x in log)
    cands = []
    for c in merged.values():
        if (c.get("arxiv") and c["arxiv"].lower() in arx) or (c.get("doi") and c["doi"] in dois) \
                or norm_title(c["title"]) in titles or c["key"] in seen_before:
            continue
        c["strong"] = len(c["facets"]) >= 2 or len(facets_all) == 1
        c["abstract_sha256"] = sha256(c.get("abstract") or "") if c.get("abstract") else None
        cands.append(c)
    hyps = hypotheses(pdir)
    for c in cands:
        cw = words(c["title"] + " " + c.get("abstract", ""))
        scored = []
        for hid, hw in hyps:
            shared = hw & cw
            if hw and len(shared) >= NOVELTY_MIN_SHARED:
                score = round(len(shared) / len(hw), 3)
                if score >= NOVELTY_MIN_SCORE:
                    scored.append({"hypothesis": hid, "score": score})
        c["novelty"] = sorted(scored, key=lambda x: -x["score"])[:3]
    cands.sort(key=lambda c: c.get("date") or "", reverse=True)  # newest first within a rank
    cands.sort(key=lambda c: (not c["strong"], -len(c["facets"]), -max([n["score"] for n in c["novelty"]] or [0])))
    for i, c in enumerate(cands):
        c["triage"] = c["strong"] and i < top
        c.update({"why": None, "decision": None})
    run = {"tool": TOOL, "project": hub_field(pdir, "id"), "since": since.isoformat(), "until": today.isoformat(),
           "degraded": any(x["error"] for x in log), "lost_all": lost_all, "queries": log,
           "candidates": cands, "threats": []}
    out = None
    if not lost_all:
        out = run_path(pdir, today)
        save_run(out, run)
        text, nl = read_text(hub_path(pdir))
        write_text(hub_path(pdir), set_fields(text, {"last_watch": today.isoformat()}), nl)
    return {"run": out.relative_to(vault).as_posix() if out else None, "since": run["since"], "until": run["until"],
            "queries": len(log), "lost": sum(1 for x in log if x["error"]), "lost_all": lost_all,
            "candidates": len(cands), "strong": sum(c["strong"] for c in cands),
            "to_triage": sum(c["triage"] for c in cands),
            "novelty_candidates": sum(1 for c in cands if c["novelty"])}


# --------------------------------------------------------------------------
# triage / threat / decisions
# --------------------------------------------------------------------------

def hypothesis_file(pdir: Path, hid: str) -> Path:
    if not re.fullmatch(r"H-\d{4}", hid):
        raise Refused("hypothesis must look like H-XXXX")
    hits = sorted((pdir / "Hipotesis").glob(f"{hid}*.md"))
    if not hits:
        raise Refused(f"{hid} not found in this project")
    if is_flagged(hits[0]):
        raise Refused(f"{hid} is marked send: never")
    return hits[0]


def cmd_triage(a) -> dict:
    run = load_run(a.run)
    c = find_candidate(run, a.key)
    why = ws(a.why)
    if not why or len(why) > 300:
        raise Refused("--why must be one line of at most 300 characters")
    c["why"] = why
    save_run(a.run, run)
    return {"key": a.key, "why": why}


def cmd_threat(a) -> dict:
    run = load_run(a.run)
    c = find_candidate(run, a.key)
    sentence = ws(a.sentence).strip("\"“”«»")
    abstract = c.get("abstract") or ""
    if not abstract.strip():
        raise Refused("the candidate has no abstract: a threat must quote the abstract's own words")
    if c.get("abstract_sha256") and sha256(abstract) != c["abstract_sha256"]:
        raise Refused("the candidate's abstract was edited after the watch fetched it")
    if len(sentence.split()) < 6:
        raise Refused("--sentence must quote at least six words of the abstract")
    if sentence not in ws(abstract):
        raise Refused("--sentence is not verbatim in the candidate's abstract")
    a.severity = {"critico": "crítico"}.get(ws(a.severity).lower(), ws(a.severity).lower())
    if a.severity not in SEVERITIES:
        raise Refused(f"--severity must be one of {', '.join(SEVERITIES)}")
    judgement = ws(a.judgement)
    if not judgement or len(judgement) > 300:
        raise Refused("--judgement must be one line of at most 300 characters")
    f = hypothesis_file(a.project_dir, a.hypothesis)
    text, nl = read_text(f)
    parts = split_frontmatter(text)
    status = (fm_get(parts[0], "status") or "") if parts else ""
    if status in INACTIVE:
        raise Refused(f"{a.hypothesis} is {status}: the watch does not raise novelty threats on it")
    body_rev = text.split("## Revisión de vigencia", 1)[-1] if "## Revisión de vigencia" in text else ""
    new = f"{a.key} " not in body_rev and f"({a.key})" not in body_rev
    if new:
        who = f", {a.model}" if a.model else ""
        line = (f"- {date.today().isoformat()} · posible amenaza de novedad, gravedad {a.severity} "
                f"(juicio de un modelo{who}; no es evidencia): "
                f"«{sentence}» — {c['title']} ({a.key}). {judgement} · Pendiente de tu decisión.")
        write_text(f, append_revision_line(text, line), nl)
    if not any(t["key"] == a.key and t["hypothesis"] == a.hypothesis for t in run["threats"]):
        run["threats"].append({"key": a.key, "hypothesis": a.hypothesis, "sentence": sentence,
                               "severity": a.severity, "judgement": judgement, "model": a.model,
                               "decision": None})
        save_run(a.run, run)
    return {"key": a.key, "hypothesis": a.hypothesis, "severity": a.severity, "written": new,
            "file": f.relative_to(a.vault.resolve()).as_posix() if a.vault else None}


def cmd_decide(a) -> dict:
    run = load_run(a.run)
    c = find_candidate(run, a.key)
    c["decision"] = {"decision": a.decision, "reason": ws(a.reason or "") or None, "by": a.by,
                     "date": date.today().isoformat()}
    save_run(a.run, run)
    return {"key": a.key, "decision": a.decision}


def cmd_threat_decide(a) -> dict:
    run = load_run(a.run)
    t = next((t for t in run["threats"] if t["key"] == a.key and t["hypothesis"] == a.hypothesis), None)
    if not t:
        raise Refused(f"no threat of {a.key} on {a.hypothesis} in this run")
    reason = ws(a.reason)
    if not reason:
        raise Refused("--reason is required: the decision is recorded with why")
    t["decision"] = {"decision": a.decision, "reason": reason, "by": a.by, "date": date.today().isoformat()}
    save_run(a.run, run)
    f = hypothesis_file(a.project_dir, a.hypothesis)
    text, nl = read_text(f)
    label = "no afecta a la hipótesis" if a.decision == "no_afecta" else "hay que revisar la hipótesis"
    write_text(f, append_revision_line(
        text, f"- {date.today().isoformat()} · decisión de {a.by} sobre {a.key}: {label}. Motivo: {reason}"), nl)
    return {"key": a.key, "hypothesis": a.hypothesis, "decision": a.decision}


def check_run(run: dict) -> list[str]:
    """What a run still lacks before it may be committed."""
    missing = []
    for c in run.get("candidates", []):
        if c.get("triage") and not ws(c.get("why") or ""):
            missing.append(f"{c['key']}: destacado sin su línea de por qué")
    by_key = {c["key"]: c for c in run.get("candidates", [])}
    for t in run.get("threats", []):
        tag = f"{t.get('key')} → {t.get('hypothesis')}"
        if t.get("severity") not in SEVERITIES:
            missing.append(f"{tag}: alerta sin gravedad (crítico / importante / menor)")
        c = by_key.get(t.get("key"))
        if not c or not t.get("sentence") or ws(t["sentence"]) not in ws(c.get("abstract") or ""):
            missing.append(f"{tag}: la frase citada no está literal en el resumen")
    return missing


def cmd_check(a) -> dict:
    run = load_run(a.run)
    missing = check_run(run)
    return {"run": a.run.name, "complete": not missing, "missing": missing,
            "triaged": sum(1 for c in run.get("candidates", []) if c.get("triage")),
            "threats": len(run.get("threats", []))}


# --------------------------------------------------------------------------

def main(argv: list[str] | None = None, fetch: Fetch = default_fetch, today: date | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("delta")
    p.add_argument("--vault", required=True, type=Path)
    p.add_argument("--project-dir", required=True, type=Path)
    p.add_argument("--since", default=None)
    p.add_argument("--top", type=int, default=10)
    for name in ("triage", "threat", "decide", "threat-decide", "check"):
        p = sub.add_parser(name)
        p.add_argument("--project-dir", required=True, type=Path)
        p.add_argument("--run", required=True, type=Path)
        if name != "check":
            p.add_argument("--key", required=True)
        p.add_argument("--vault", type=Path, default=None)
        if name == "triage":
            p.add_argument("--why", required=True)
        if name == "threat":
            p.add_argument("--hypothesis", required=True)
            p.add_argument("--sentence", required=True)
            p.add_argument("--judgement", required=True)
            p.add_argument("--severity", required=True, help="crítico | importante | menor")
            p.add_argument("--model", default=None)
        if name == "decide":
            p.add_argument("--decision", required=True, choices=("ingerir", "descartar"))
            p.add_argument("--reason", default=None)
            p.add_argument("--by", default="investigador")
        if name == "threat-decide":
            p.add_argument("--hypothesis", required=True)
            p.add_argument("--decision", required=True, choices=("no_afecta", "revisar"))
            p.add_argument("--reason", required=True)
            p.add_argument("--by", default="investigador")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if a.cmd == "delta":
            since = date.fromisoformat(a.since) if a.since else None
            out = delta(a.vault.resolve(), a.project_dir.resolve(), since, a.top, fetch, today or date.today())
        else:
            a.project_dir = a.project_dir.resolve()
            run_file = a.run if a.run.is_absolute() else (a.vault or Path.cwd()) / a.run
            a.run = run_file.resolve()
            if a.project_dir / "_vigilancia" != a.run.parent:
                raise Refused("--run must be a file in this project's _vigilancia/")
            out = {"triage": cmd_triage, "threat": cmd_threat, "decide": cmd_decide,
                   "threat-decide": cmd_threat_decide, "check": cmd_check}[a.cmd](a)
    except Refused as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 3 if out.get("complete") is False else 0


if __name__ == "__main__":
    sys.exit(main())
