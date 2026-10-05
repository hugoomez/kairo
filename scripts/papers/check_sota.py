#!/usr/bin/env python3
"""Mechanically check a project's Estado-del-arte.md (or any synthesis note)
before it is committed: every citation and every number in a cited sentence.

    check_sota.py --vault <vault> --project-dir Projects/<slug> [--file Estado-del-arte.md] [--write]

The synthesis is the model's prose, but each claim about a paper carries a
locator — `P-0007 §4.2`, `P-0012 Tabla 3`, `[P-0015 §2; Fig. 3]`. With the
fresh-verifier's own resolver (`verifier_packet.resolve_citation`), nothing
judged by a model, this checks per citation:

  - the paper exists, belongs to the project and is not `send: never`;
  - the locator points at text in the note's `## Texto completo` / `## Resumen`;
  - that text is the paper's own (no model-text markers; for a note ingested by
    ingest_paper.py, it still matches the kept source bytes);
  - the paper's reference is not in conflict, retracted or withdrawn
    (`resolution_status`);

and per cited sentence (bullet or paragraph): every number in it — a decimal,
a percentage, a count ≥ 10, a power of ten — appears in the text its
citations point at (thousands separators and the minus sign aside). Years,
section numbers and the locators themselves are not numbers to check. A
number that is in none of the cited texts is the fingerprint of a figure
moved from one paper to another, or invented.

A citation without a locator (`P-0007` alone) is listed, not failed: the rule
is "a locator where possible".

With `--write`, each failing citation / number is marked in place with a
visible «⚠ …» after it; nothing is removed. Prints a JSON report.
Exit codes: 0 clean · 3 problems found · 1 error · 2 bad input.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "ledger"))
from check_quotes import paper_projects  # noqa: E402
from verifier_packet import find_paper, fm_scalar, read_text, resolve_citation, split_frontmatter  # noqa: E402

TOOL = "kairo/check_sota@1.0.0"
_LOC_PART = (r"(?:§\s*[A-Za-zÁÉÍÓÚáéíóú0-9][\w.]*(?:\s*[–-]\s*§?\s*[\w.]+)?"
             r"|(?:Tabla|Table|Figura|Figure|Fig\.?|App(?:endix)?\.?|Apéndice|Eq\.?|Ec\.?)\s*[A-Z]?\d+(?:\.\d+)*)")
CITE = re.compile(rf"\b(P-\d{{4,5}})((?:[ ,;]*{_LOC_PART})*)")
NUMBER = re.compile(r"(?<![\w.§-])[-−]?\d[\d,.]*(?:\s*%|\s*×\s*10\^?[-−]?\d+|e[-−]?\d+)?(?![\w])")
BAD_STATUS = {"mismatch": "referencia en conflicto (resolution_status: mismatch)",
              "retracted": "paper retractado", "withdrawn": "paper retirado"}


def blocks(text: str) -> list[tuple[int, int, str]]:
    """(start, end, text) of each bullet or paragraph, frontmatter excluded."""
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    off = m.end() if m else 0
    out = []
    for mm in re.finditer(r"(?:^[ \t]*[-*] .*(?:\n(?![ \t]*[-*] |\s*\n|#).*)*|^(?![ \t]*[-*] |#|\|).+(?:\n(?![ \t]*[-*] |\s*\n|#).+)*)",
                          text[off:], re.M):
        out.append((off + mm.start(), off + mm.end(), mm.group(0)))
    return out


def numbers(sentence: str) -> list[str]:
    """Numbers worth checking in a sentence, with citations, ids and dates removed."""
    s = CITE.sub(" ", sentence)
    s = re.sub(r"\b(?:H|E|C|PROJ|ADR|F)-\d+\b", " ", s)
    s = re.sub(r"\d{4}-\d{2}-\d{2}", " ", s)
    s = re.sub(r"§\s*[\w.]+", " ", s)
    out = []
    for m in NUMBER.finditer(s):
        tok = m.group(0).strip().rstrip(".,")
        digits = re.sub(r"[^\d.]", "", tok.split("×")[0]).strip(".")
        if not digits:
            continue
        is_int = "." not in digits and "%" not in tok and "×" not in tok and "e" not in tok.lower()
        if is_int and (int(digits.replace(".", "") or 0) < 10 or re.fullmatch(r"(?:19|20)\d{2}", digits)):
            continue                      # small counts and years are not results
        out.append(tok)
    return out


def _canon(tok: str) -> str:
    t = tok.replace("−", "-").replace(" ", "")
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)     # thousands separators
    return t.rstrip("%")


def number_in(tok: str, texts: list[str]) -> bool:
    want = _canon(tok)
    core = re.sub(r"[^\d.]", "", want.split("×")[0])
    for t in texts:
        canon = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t.replace("−", "-")).replace(" ", "").replace(" ", "")
        if want in canon or (core and re.search(rf"(?<![\d.]){re.escape(core)}(?![\d])", canon)):
            return True
    return False


def check(vault: str, project: str | None, text: str) -> dict:
    report = {"tool": TOOL, "citations": 0, "without_locator": [], "problems": [], "numbers_checked": 0}
    for start, end, block in blocks(text):
        cites = list(CITE.finditer(block))
        if not cites:
            continue
        units: list[str] = []
        for m in cites:
            pid, span = m.group(1), m.group(2).strip(" ,;")
            report["citations"] += 1
            where = {"citation": m.group(0).strip(), "offset": start + m.end()}
            path = find_paper(vault, pid)
            if not path:
                report["problems"].append({**where, "severity": "crítico", "reason": f"{pid} no existe en Papers/"})
                continue
            fm, _ = split_frontmatter(read_text(path))
            if project and project not in paper_projects(vault, pid):
                report["problems"].append({**where, "severity": "importante",
                                           "reason": f"{pid} no es un paper del proyecto {project}"})
            status = (fm_scalar(fm, "resolution_status") or "").strip()
            if status in BAD_STATUS:
                report["problems"].append({**where, "severity": "crítico", "reason": BAD_STATUS[status]})
            if not span:
                report["without_locator"].append(pid)
                res = resolve_citation(vault, pid, "§Resumen")
                units.extend(u.removeprefix("[## Resumen]\n") for u in res["units"])
                continue
            res = resolve_citation(vault, pid, span)
            if res["note"] and not res["units"]:
                report["problems"].append({**where, "severity": "crítico",
                                           "reason": f"el localizador no señala texto: {res['note']}"})
            if res["provenance"]:
                report["problems"].append({**where, "severity": "crítico",
                                           "reason": "el texto citado no es del paper: " + "; ".join(res["provenance"])})
            units.extend(u.removeprefix("[## Resumen]\n") for u in res["units"])
        for tok in numbers(block):
            report["numbers_checked"] += 1
            if not number_in(tok, units):
                report["problems"].append({"citation": ", ".join(c.group(0).strip() for c in cites),
                                           "offset": end, "severity": "importante", "number": tok,
                                           "reason": f"la cifra «{tok}» no aparece en el texto citado"})
    report["without_locator"] = sorted(set(report["without_locator"]))
    return report


def mark(text: str, problems: list[dict]) -> str:
    for p in sorted(problems, key=lambda p: -p["offset"]):
        text = text[:p["offset"]] + f" «⚠ {p['reason']}»" + text[p["offset"]:]
    return text


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vault", required=True)
    ap.add_argument("--project-dir", required=True)
    ap.add_argument("--file", default="Estado-del-arte.md")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    pdir = Path(a.vault, a.project_dir)
    f = pdir / a.file if not Path(a.file).is_absolute() else Path(a.file)
    if not f.is_file():
        print(json.dumps({"tool": TOOL, "error": f"not found: {f}"}, ensure_ascii=False))
        return 2
    hub = pdir / "_hub.md"
    project = fm_scalar(split_frontmatter(read_text(str(hub)))[0], "id") if hub.is_file() else None
    try:
        text = f.read_text(encoding="utf-8")
        report = check(a.vault, project, text)
    except (OSError, ValueError) as e:
        print(json.dumps({"tool": TOOL, "error": str(e)}, ensure_ascii=False))
        return 1
    if a.write and report["problems"]:
        f.write_text(mark(text, report["problems"]), encoding="utf-8", newline="\n")
        report["written"] = True
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 3 if report["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
