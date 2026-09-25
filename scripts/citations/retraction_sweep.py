#!/usr/bin/env python3
"""Periodic retraction / withdrawal sweep over a Kairo vault.

Re-checks every ingested `Papers/P-*.md` note (Crossref for non-arXiv DOIs,
arXiv for arXiv ids -- batched, OpenAlex `is_retracted` by stored
`openalex_id` or DOI singleton, which is free) through the shared
`retraction.py`. For every paper that is retracted / withdrawn (or has an
expression of concern) it finds every note that cites it:

  * hypotheses  `Projects/*/Hipotesis/*.md` -- `linked_papers:` list, and any
                `P-XXXX` mention in the body;
  * ADRs        `Projects/*/Producto/ADR-*.md` -- `cites:` list;

and flags each one with the adr-check staleness pattern: one visible warning
line per citing note, with a severity (`crítico` / `importante` / `menor`):

    ⚠️ H-0001 cita P-0003 (linked_papers) — P-0003 RETRACTADO (Crossref
    updated-by: retraction notice 10.x/y, 2021-12-15). La evidencia que se
    apoya en este paper puede no sostenerse; re-evaluar. [crítico]

  retracted / withdrawn, cited in linked_papers / cites  -> crítico
  retracted / withdrawn, only mentioned in the body      -> importante
  expression of concern, cited                           -> importante
  expression of concern, only mentioned                  -> menor

It never rewrites a note's content and never touches a hypothesis `status`
(update-confidence is its only writer). Default is REPORT-ONLY. With --write:
  * each retracted/withdrawn paper gets a full re-resolution (resolve_refs)
    and its resolution fields written (`resolved`, `openalex_id`,
    `resolution_checked`, `resolution_status`, ...);
  * each citing hypothesis / ADR gets ONE dated line appended to its
    `## Revisión de vigencia` section (created at the end of the note if
    absent). Append-only; a line for the same paper + state already written by
    this sweep is not repeated on later runs.

`send: never` notes: a Papers note with it is skipped entirely (no API call,
only `P-XXXX: skipped (send: never)` in the output). A citing note with it is
still flagged by id, and nothing of its content is printed -- output only ever
carries note ids, never titles, paths or text.

Usage:
    python retraction_sweep.py --vault <vault>                 # report
    python retraction_sweep.py --vault <vault> --json
    python retraction_sweep.py --vault <vault> --write
    python retraction_sweep.py --version

A check that could not run -- Crossref / OpenAlex LOST to errors or budget
after retries, or arXiv answering without an entry for the note's id -- is
reported as `[importante] check LOST (...)` per paper (JSON: `lost_checks`,
one entry per paper with `sources` and `severity: importante`). Such a paper is
NOT proven clear. With --write, a LOST OpenAlex lookup never overwrites the
paper's §1c fields (see resolve_refs.fields_for).

Exit codes: 0 ok, every check ran (flags are in the report); 1 error (could
not run) OR at least one check LOST (re-run later; JSON `exit` says which);
2 invalid input (no Papers/ directory under --vault).
Standard library only.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import net  # noqa: E402
import resolve_refs as rr  # noqa: E402
import retraction  # noqa: E402
import vaultnotes as vn  # noqa: E402

__version__ = "1.0.0"

STATE_ES = {"retracted": "RETRACTADO", "withdrawn": "RETIRADO (withdrawn)", "concern": "CON EXPRESIÓN DE PREOCUPACIÓN"}
MARKER = "(retraction_sweep)"


@dataclass
class Citer:
    id: str
    kind: str          # hypothesis | adr
    via: str           # linked_papers | cites | body
    severity: str
    send_never: bool = False
    path: Path | None = field(default=None, repr=False)


@dataclass
class PaperFlag:
    id: str
    status: str                     # clear | concern | withdrawn | retracted | skipped_send_never
    newly: bool = False
    evidence: list[str] = field(default_factory=list)
    lost: list[str] = field(default_factory=list)
    citers: list[Citer] = field(default_factory=list)
    written: bool = False
    appended: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Citation index (pure-ish -- unit-tested on a temp vault)
# --------------------------------------------------------------------------- #

def _note_meta(path: Path) -> tuple[str, list[str], str, bool]:
    text = path.read_text(encoding="utf-8")
    split = vn.split_frontmatter(text)
    fm, body = (split if split else ([], text))
    return vn.note_id(path, fm), fm, body, vn.is_send_never(fm)


def citers_of(vault: Path, pid: str) -> list[tuple[str, str, str, bool, Path]]:
    """[(note id, kind, via, send_never, path)] for every hypothesis / ADR citing pid."""
    out = []
    for h in sorted(vault.glob("Projects/*/Hipotesis/*.md")):
        nid, fm, body, sn = _note_meta(h)
        if pid in vn.parse_flow_list(vn.fm_raw(fm, "linked_papers")):
            out.append((nid, "hypothesis", "linked_papers", sn, h))
        elif re.search(rf"\b{re.escape(pid)}\b", body):
            out.append((nid, "hypothesis", "body", sn, h))
    for a in sorted(vault.glob("Projects/*/Producto/ADR-*.md")):
        nid, fm, _body, sn = _note_meta(a)
        if pid in vn.parse_flow_list(vn.fm_raw(fm, "cites")):
            out.append((nid, "adr", "cites", sn, a))
    return out


def severity(state: str, via: str) -> str:
    cited = via in ("linked_papers", "cites")
    if state in ("retracted", "withdrawn"):
        return "crítico" if cited else "importante"
    return "importante" if cited else "menor"


def warning_line(c: Citer, p: PaperFlag) -> str:
    ev = p.evidence[0] if p.evidence else "no evidence text"
    tail = ("La evidencia que se apoya en este paper puede no sostenerse; re-evaluar."
            if p.status in ("retracted", "withdrawn") else "Revisar si la conclusión citada sigue en pie.")
    return f"⚠️ {c.id} cita {p.id} ({c.via}) — {p.id} {STATE_ES[p.status]} ({ev}). {tail} [{c.severity}]"


def revision_line(c: Citer, p: PaperFlag, today: str) -> str:
    ev = p.evidence[0] if p.evidence else ""
    return (f"- {today} — ⚠️ [{c.severity}] {p.id} {STATE_ES[p.status]}"
            + (f" ({ev})" if ev else "") + f". Re-evaluar lo que se apoya en él. {MARKER}")


def already_logged(text: str, pid: str, status: str) -> bool:
    return any(MARKER in ln and pid in ln and STATE_ES[status] in ln for ln in text.split("\n"))


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #

def check_paper(note: dict, fm_openalex: str | None, entries: dict, lost: dict) -> tuple[str, list[str], list[str]]:
    base = retraction.check_one(note["doi"], note["arxiv"], entries, lost)
    # arXiv answering without an entry for the note's id = withdrawal check not run -> LOST
    lost_srcs = [s for s, v in base["checks"].items()
                 if v["state"] == "lost" or (s == "arxiv" and v["state"] == "not_found")]
    checks_status, ev = base["status"], list(base["evidence"])
    try:
        work = None
        if fm_openalex and re.fullmatch(r"W\d+", fm_openalex):
            work = rr.openalex_by_id(fm_openalex)
        else:
            d = note["doi"] or (f"10.48550/arxiv.{note['arxiv']}" if note["arxiv"] else None)
            work = rr.openalex_by_doi(d) if d else None
        oa = retraction.openalex_check(work)
        s2, ev2 = retraction.verdict([oa])
        if retraction.PRECEDENCE[s2] > retraction.PRECEDENCE[checks_status]:
            checks_status = s2
        ev += ev2
    except (net.HttpError, ValueError):
        lost_srcs.append("openalex")
    return checks_status, ev, lost_srcs


def sweep(vault: Path, write: bool, today: str) -> list[PaperFlag]:
    papers = sorted((vault / "Papers").glob("P-*.md"))
    notes = [rr.read_note(p) for p in papers]
    sendable = [n for n in notes if not n["send_never"]]
    entries, lost = retraction.fetch_arxiv([n["arxiv"] or retraction.arxiv_from_doi(n["doi"])
                                            for n in sendable if n["arxiv"] or retraction.arxiv_from_doi(n["doi"])])
    out: list[PaperFlag] = []
    for n in notes:
        if n["send_never"]:
            out.append(PaperFlag(id=n["id"], status="skipped_send_never"))
            continue
        print(f"checking {n['id']} ...", file=sys.stderr, flush=True)
        fm = (vn.split_frontmatter(n["path"].read_text(encoding="utf-8")) or ([], ""))[0]
        status, ev, lost_srcs = check_paper(n, vn.fm_get(fm, "openalex_id"), entries, lost)
        pf = PaperFlag(id=n["id"], status=status, evidence=ev, lost=lost_srcs)
        if status != "clear":
            pf.newly = n["prev_status"] != status
            for nid, kind, via, sn, path in citers_of(vault, n["id"]):
                pf.citers.append(Citer(nid, kind, via, severity(status, via), sn, path))
        if write and status in ("retracted", "withdrawn"):
            r = rr.resolve_note(n, entries, lost)
            pf.written = rr.write_result(n["path"], r, today)
        if write and status != "clear":
            for c in pf.citers:
                text, nl = vn.read_text(c.path)
                if not already_logged(text, pf.id, status):
                    vn.write_text(c.path, vn.append_revision_line(text, revision_line(c, pf, today)), nl)
                    pf.appended.append(c.id)
        out.append(pf)
    return out


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #

LOST_SEVERITY = "importante"


def lost_message(p: PaperFlag) -> str:
    return (f"[{LOST_SEVERITY}] check LOST ({', '.join(p.lost)}) -- retraction/withdrawal status not proven; "
            "re-run later")


def exit_code(flags: list[PaperFlag]) -> int:
    return 1 if any(p.lost for p in flags) else 0


def to_json(flags: list[PaperFlag], today: str) -> dict:
    papers = []
    for p in flags:
        if p.status == "skipped_send_never":
            papers.append({"id": p.id, "status": "skipped_send_never"})
            continue
        d = asdict(p)
        d["citers"] = [{"id": c.id, "kind": c.kind, "via": c.via, "severity": c.severity,
                        "warning": warning_line(c, p)} for c in p.citers]
        papers.append(d)
    summary = {k: sum(1 for p in flags if p.status == k)
               for k in ("clear", "concern", "withdrawn", "retracted", "skipped_send_never")}
    summary["lost_checks"] = sum(1 for p in flags if p.lost)
    summary["citing_notes_flagged"] = sum(len(p.citers) for p in flags)
    lost_checks = [{"id": p.id, "sources": list(p.lost), "severity": LOST_SEVERITY} for p in flags if p.lost]
    return {"version": __version__, "checked": today, "papers": papers, "summary": summary,
            "lost_checks": lost_checks, "exit": exit_code(flags)}


def print_report(flags: list[PaperFlag], write: bool) -> None:
    for p in flags:
        if p.status == "skipped_send_never":
            print(f"{p.id}: skipped (send: never)")
            continue
        line = f"{p.id}  {p.status}" + ("  (NEW)" if p.newly and p.status != "clear" else "")
        if p.lost:
            line += f"  [check LOST: {', '.join(p.lost)}]"
        print(line)
        if p.lost:
            print(f"  ! {lost_message(p)}")
        for e in p.evidence:
            print(f"  . {e}")
        for c in p.citers:
            print(f"  {warning_line(c, p)}")
        if p.status != "clear" and not p.citers:
            print("  (no hypothesis or ADR cites it)")
    flagged = [p for p in flags if p.status in ("retracted", "withdrawn", "concern")]
    print()
    print(f"summary: {len(flags)} papers, {len(flagged)} flagged, "
          f"{sum(len(p.citers) for p in flagged)} citing notes flagged, "
          f"{sum(1 for p in flags if p.lost)} with a LOST check")
    if not write:
        print("Nothing was written (report-only). --write updates the flagged papers' resolution fields "
              "and appends a dated line to each citing note's `## Revisión de vigencia`.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--vault", type=Path, help="the vault root (contains Papers/ and Projects/)")
    ap.add_argument("--json", action="store_true", help="machine-readable report on stdout")
    ap.add_argument("--write", action="store_true",
                    help="update flagged papers' resolution fields and append to citing notes' Revisión de vigencia")
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if a.vault is None or not (a.vault / "Papers").is_dir():
        print("error: --vault must be a vault root containing Papers/", file=sys.stderr)
        return 2
    today = _dt.date.today().isoformat()
    try:
        flags = sweep(a.vault, a.write, today)
    except Exception as e:  # noqa: BLE001
        print(f"error: {type(e).__name__}: {net.redact(str(e))}", file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps(to_json(flags, today), indent=2, ensure_ascii=False, default=str))
    else:
        print_report(flags, a.write)
        if exit_code(flags):
            print("SWEEP INCOMPLETE -- at least one check was LOST; those papers are not proven clear (exit 1)")
    return exit_code(flags)


if __name__ == "__main__":
    sys.exit(main())
