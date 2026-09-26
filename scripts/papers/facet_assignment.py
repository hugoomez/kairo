#!/usr/bin/env python3
"""Read and write the per-project facet match stored in each paper note.

`literature-search` records, for every candidate, which facet term or synonym
hit it (`matched:`). `create-project` step 6 persists that record in the
paper note's frontmatter, one entry per (project, facet):

    facets:
      - {project: PROJ-900, facet: A, matched: "toy term"}
      - {project: PROJ-900, facet: C, matched: "another term"}
      - {project: PROJ-900, facet: B, matched: null, unrecovered: "<why>"}

`matched: null` + `unrecovered:` marks a facet whose membership is known but
whose matched term could not be recovered (backfills only; a new ingestion
always has the term). Step 7 then reads the assignment with this script and
never re-derives facet membership.

    python facet_assignment.py --vault <vault> --project PROJ-900 [--json]
        facet -> papers for the project. Exit 1 if any paper of the project has
        no facet entry for it (step 7 must stop and report it, not guess).
    python facet_assignment.py --vault <vault> --add P-0901 --project PROJ-900 \\
        --facet A --matched "toy term"        (or --unrecovered "<why>")
        Append one entry to the note's frontmatter (idempotent: an entry for the
        same project + facet is replaced).

Only top-level `Papers/P-*.md` notes are considered: `Papers/_notas/` holds
model-written reading notes and is never read. A `send: never` note is never
opened: it is listed under `send_never` and gets no facet. Output is ids,
paths, facet letters and matched terms only.

Exit codes: 0 ok, 1 papers without facets (listing mode), 2 bad input.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "security"))
from send_guard import is_flagged, is_model_notes  # noqa: E402

__version__ = "1.0.0"
_KEY = re.compile(r"^([A-Za-z_][\w-]*)\s*:(.*)$")
_PAIR = re.compile(r"""\s*([A-Za-z_]\w*)\s*:\s*("(?:[^"\\]|\\.)*"|'[^']*'|[^,]*?)\s*(?:,|$)""")
_ID = re.compile(r"^(P-\d{4,})\b")


def split(text: str) -> tuple[list[str], str] | None:
    t = text.lstrip("﻿")
    if not t.startswith("---\n"):
        return None
    end = t.find("\n---\n", 3)
    if end == -1:
        return None
    return t[4:end].split("\n"), t[end + 5:]


def _value(raw: str):
    raw = raw.strip()
    if raw in ("", "null", "~"):
        return None
    if raw[0] == raw[-1] == '"' and len(raw) >= 2:
        return json.loads(raw)
    if raw[0] == raw[-1] == "'" and len(raw) >= 2:
        return raw[1:-1]
    return raw


def parse_flow_map(s: str) -> dict | None:
    s = s.strip()
    if not (s.startswith("{") and s.endswith("}")):
        return None
    out, inner, pos = {}, s[1:-1].strip(), 0
    while pos < len(inner):
        m = _PAIR.match(inner, pos)
        if not m or m.end() == pos:
            return None
        out[m.group(1)] = _value(m.group(2))
        pos = m.end()
    return out


def facet_block(fm: list[str]) -> tuple[int, int] | None:
    """(start, end) line span of the `facets:` key and its items, or None."""
    for i, line in enumerate(fm):
        m = _KEY.match(line)
        if m and m.group(1) == "facets":
            j = i + 1
            while j < len(fm) and (fm[j].startswith((" ", "\t", "- ")) or not fm[j].strip()):
                j += 1
            while j > i + 1 and not fm[j - 1].strip():
                j -= 1
            return i, j
    return None


def read_facets(fm: list[str]) -> list[dict]:
    span = facet_block(fm)
    if not span:
        return []
    i, j = span
    head = _KEY.match(fm[i]).group(2).strip()
    if head in ("[]", ""):
        items = [ln.strip()[2:] for ln in fm[i + 1:j] if ln.strip().startswith("- ")]
    else:
        return []
    return [e for e in (parse_flow_map(x) for x in items) if e]


def projects_of(fm: list[str]) -> list[str]:
    for line in fm:
        m = _KEY.match(line)
        if m and m.group(1) == "projects":
            return re.findall(r"PROJ-\d+", m.group(2))
    return []


def _q(v: str) -> str:
    return json.dumps(v, ensure_ascii=False)


def entry_line(project: str, facet: str, matched: str | None,
               unrecovered: str | None) -> str:
    parts = [f"project: {project}", f"facet: {facet}",
             f"matched: {_q(matched) if matched is not None else 'null'}"]
    if unrecovered:
        parts.append(f"unrecovered: {_q(unrecovered)}")
    return "  - {" + ", ".join(parts) + "}"


def add_entry(text: str, project: str, facet: str, matched: str | None,
              unrecovered: str | None) -> str:
    nl = "\r\n" if "\r\n" in text else "\n"
    parts = split(text.replace("\r\n", "\n"))
    if parts is None:
        raise ValueError("note has no frontmatter")
    fm, body = parts
    line = entry_line(project, facet, matched, unrecovered)
    span = facet_block(fm)
    if span is None:
        fm = fm + ["facets:", line]
    else:
        i, j = span
        keep = []
        for ln in fm[i + 1:j]:
            e = parse_flow_map(ln.strip()[2:]) if ln.strip().startswith("- ") else None
            if e and e.get("project") == project and e.get("facet") == facet:
                continue
            keep.append(ln)
        fm = fm[:i] + ["facets:"] + keep + [line] + fm[j:]
    return ("---\n" + "\n".join(fm) + "\n---\n" + body).replace("\n", nl)


def paper_notes(vault: Path) -> list[Path]:
    return sorted(p for p in (vault / "Papers").glob("P-*.md")
                  if p.is_file() and not is_model_notes(p.resolve()))


def assignment(vault: Path, project: str) -> dict:
    res: dict = {"project": project, "facets": {}, "sin_facetas": [], "send_never": []}
    for p in paper_notes(vault):
        pid = (_ID.match(p.name) or [None, p.stem])[1]
        rel = p.relative_to(vault).as_posix()
        if is_flagged(p):
            res["send_never"].append(pid)
            continue
        parts = split(p.read_text(encoding="utf-8").replace("\r\n", "\n"))
        if parts is None or project not in projects_of(parts[0]):
            continue
        mine = [e for e in read_facets(parts[0]) if e.get("project") == project]
        if not mine:
            res["sin_facetas"].append(pid)
            continue
        for e in mine:
            res["facets"].setdefault(str(e.get("facet")), []).append(
                {"id": pid, "path": rel, "matched": e.get("matched"),
                 **({"unrecovered": e["unrecovered"]} if e.get("unrecovered") else {})})
    res["facets"] = dict(sorted(res["facets"].items()))
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--vault", required=True, type=Path)
    ap.add_argument("--project", required=True)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--add", metavar="P-XXXX")
    ap.add_argument("--facet")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--matched")
    g.add_argument("--unrecovered")
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    if not (a.vault / "Papers").is_dir():
        print(f"error: no Papers/ under {a.vault}", file=sys.stderr)
        return 2

    if a.add:
        if not a.facet or (a.matched is None and not a.unrecovered):
            print("error: --add needs --facet and --matched or --unrecovered", file=sys.stderr)
            return 2
        hits = [p for p in paper_notes(a.vault) if p.name.startswith(a.add)
                and (_ID.match(p.name) or [None, ""])[1] == a.add]
        if len(hits) != 1:
            print(f"error: {a.add}: {len(hits)} paper notes match", file=sys.stderr)
            return 2
        if is_flagged(hits[0]):
            print(f"error: {a.add} is send: never; not edited", file=sys.stderr)
            return 2
        raw = hits[0].read_bytes().decode("utf-8")
        try:
            new = add_entry(raw, a.project, a.facet, a.matched, a.unrecovered)
        except ValueError as exc:
            print(f"error: {a.add}: {exc}", file=sys.stderr)
            return 2
        hits[0].write_bytes(new.encode("utf-8"))
        print(f"{a.add}: {a.project} faceta {a.facet} "
              f"({'matched ' + _q(a.matched) if a.matched is not None else 'sin término'})")
        return 0

    res = assignment(a.vault, a.project)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        for f, papers in res["facets"].items():
            print(f"Faceta {f}:")
            for e in papers:
                term = _q(e["matched"]) if e["matched"] is not None else "(término no recuperado)"
                print(f"  {e['id']}  {term}  {e['path']}")
        if res["sin_facetas"]:
            print("Sin faceta registrada (no reasignar; avisar): " + ", ".join(res["sin_facetas"]))
        if res["send_never"]:
            print("send: never (omitidas): " + ", ".join(res["send_never"]))
    return 1 if res["sin_facetas"] else 0


if __name__ == "__main__":
    sys.exit(main())
