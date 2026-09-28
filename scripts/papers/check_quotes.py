#!/usr/bin/env python3
"""Mechanically check an ask-corpus answer: every quote must be the paper's own text.

    check_quotes.py --vault <vault> --list [--project PROJ-XXX]
    check_quotes.py --vault <vault> --answer draft.md [--project PROJ-XXX]
                    [--question "..."] [--out Projects/<slug>/Notas de proyecto/<file>.md]

The answer format (kairo:ask-corpus writes it):

    <one claim, in a paragraph>

    > <exact words from the paper>
    > — P-XXXX §3.2

    <next claim> ...

Each quote block ends with an attribution line naming one paper and one
locator (`§3.2`, `Tabla 2`, `§Resumen` for the abstract). The locator is resolved with the fresh-verifier's own resolver
(`verifier_packet.resolve_citation`): the quote must be an exact substring of
the units it points at, after collapsing whitespace — nothing else is
normalised. An ellipsis (`…`, `...`, `[…]`) splits a quote into fragments that
must all appear, in order, in the same unit.

A claim is kept only when it has at least one quote and every one of its
quotes passes. A claim without a quote, or with any failing quote, is removed
and reported. `send: never` papers, `Papers/_notas/` and (with `--project`)
papers not listed for that project never count as sources.

`**No está en el corpus.**` as the first line is a valid answer; the only
other lines kept with it are `Buscado: …` lines.

Prints a JSON report. With `--out`, writes the checked answer as a note:
`escrito_por: modelo`, `citable: false` — it is a reading aid, never evidence.

Exit codes: 0 checked (possibly with removals) · 1 error.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ledger"))
from verifier_packet import find_paper, fm_scalar, read_text, resolve_citation, split_frontmatter  # noqa: E402
from send_guard import is_flagged, is_model_notes  # noqa: E402, I001  (path set by verifier_packet)

TOOL = "kairo/check_quotes@1.0.0"
NOT_FOUND = "**No está en el corpus.**"
MIN_WORDS = 4
_ATTR = re.compile(r"^[—–-]{1,2}\s*\[{0,2}(P-\d{3,5})\]{0,2}\s+(.+?)\s*$")
_ELLIPSIS = re.compile(r"\s*(?:\[\s*(?:…|\.\.\.)\s*\]|…|\.\.\.)\s*")
_OPEN_CLOSE = "\"“”«»'‘’"


def ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def paragraphs(text: str) -> list[list[str]]:
    out: list[list[str]] = []
    cur: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.strip():
            cur.append(line.rstrip())
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def is_quote_block(par: list[str]) -> bool:
    return all(ln.lstrip().startswith(">") for ln in par)


def parse_quote(par: list[str]) -> dict:
    lines = [re.sub(r"^\s*>\s?", "", ln) for ln in par]
    lines = [ln for ln in lines if ln.strip()]
    m = _ATTR.match(lines[-1].strip()) if lines else None
    if not m:
        return {"raw": "\n".join(par), "paper": None, "locator": None, "quote": ws(" ".join(lines)),
                "error": "sin atribución «— P-XXXX §loc» en la última línea de la cita"}
    quote = ws(" ".join(lines[:-1])).strip(_OPEN_CLOSE).strip()
    return {"raw": "\n".join(par), "paper": m.group(1), "locator": m.group(2), "quote": quote, "error": None}


def paper_projects(vault: str, pid: str) -> list[str]:
    path = find_paper(vault, pid)
    return projects_of(split_frontmatter(read_text(path))[0]) if path else []


def projects_of(fm: list[str]) -> list[str]:
    raw = fm_scalar(fm, "projects") or ""
    inline = re.findall(r"PROJ-[\w-]+", raw)
    if inline:
        return inline
    # block list form
    out, inside = [], False
    for ln in fm:
        if re.match(r"^projects:\s*$", ln):
            inside = True
            continue
        if inside:
            m = re.match(r"^\s+-\s*(\S+)", ln)
            if not m:
                break
            out.append(m.group(1).strip("'\""))
    return out


def list_papers(vault: str, project: str | None) -> list[dict]:
    """The papers an answer may quote: in Papers/, never the model-written
    reading notes, never send: never, and (with a project) listed for it."""
    out = []
    for p in sorted(Path(vault, "Papers").glob("P-*.md")):
        if is_model_notes(p.resolve()) or is_flagged(p):
            continue
        fm, body = split_frontmatter(read_text(str(p)))
        if project and project not in projects_of(fm):
            continue
        out.append({"id": fm_scalar(fm, "id") or p.stem.split(" ")[0], "title": fm_scalar(fm, "title"),
                    "path": p.relative_to(vault).as_posix(), "fulltext": "## Texto completo" in body})
    return out


def fragments_in(unit: str, frags: list[str]) -> bool:
    pos = 0
    for f in frags:
        i = unit.find(f, pos)
        if i < 0:
            return False
        pos = i + len(f)
    return True


def check_quote(vault: str, q: dict, project: str | None) -> dict:
    if q["error"]:
        return {**q, "ok": False, "reason": q["error"]}
    frags = [ws(f) for f in _ELLIPSIS.split(q["quote"]) if ws(f)]
    if sum(len(f.split()) for f in frags) < MIN_WORDS:
        return {**q, "ok": False, "reason": f"cita demasiado corta (menos de {MIN_WORDS} palabras)"}
    res = resolve_citation(vault, q["paper"], q["locator"])
    if not res["source"]:
        return {**q, "ok": False, "reason": res["note"] or "paper no disponible"}
    if project and project not in paper_projects(vault, q["paper"]):
        return {**q, "ok": False, "reason": f"{q['paper']} no es un paper del proyecto {project}"}
    if res["provenance"]:
        return {**q, "ok": False, "reason": "el texto fuente no es del paper: " + "; ".join(res["provenance"])}
    if not res["units"]:
        return {**q, "ok": False, "reason": res["note"] or "el localizador no señala ningún texto"}
    for unit in res["units"]:
        if fragments_in(ws(unit.removeprefix("[## Resumen]\n")), frags):
            return {**q, "ok": True, "reason": None, "source": res["source"]}
    return {**q, "ok": False, "reason": f"el texto citado no aparece literalmente en {q['paper']} {q['locator']}"}


def check(vault: str, answer: str, project: str | None) -> dict:
    pars = paragraphs(answer)
    report: dict = {"tool": TOOL, "status": "answered", "claims": [], "removed": [], "quotes": 0,
                    "quotes_ok": 0, "kept_lines": []}
    if pars and pars[0][0].strip() == NOT_FOUND:
        report["status"] = "not_found"
        kept = [NOT_FOUND] + [ln for par in pars for ln in par if ln.strip().startswith("Buscado:")]
        report["kept_lines"] = kept
        report["removed"] = [{"text": "\n".join(par), "reason": "texto libre en una respuesta «no está en el corpus»"}
                             for par in pars[1:] if not all(ln.strip().startswith("Buscado:") for ln in par)]
        return report
    groups: list[dict] = []
    for par in pars:
        if is_quote_block(par):
            if not groups:
                groups.append({"claim": None, "quotes": []})
            groups[-1]["quotes"].append(parse_quote(par))
        else:
            groups.append({"claim": "\n".join(par), "quotes": []})
    kept: list[str] = []
    for g in groups:
        checked = [check_quote(vault, q, project) for q in g["quotes"]]
        report["quotes"] += len(checked)
        report["quotes_ok"] += sum(c["ok"] for c in checked)
        entry = {"claim": g["claim"], "quotes": [{k: c.get(k) for k in ("paper", "locator", "quote", "ok", "reason")}
                                                 for c in checked]}
        if not checked:
            report["removed"].append({"text": g["claim"], "reason": "afirmación sin cita del paper"})
            continue
        bad = [c for c in checked if not c["ok"]]
        if bad:
            report["removed"].append({"text": g["claim"], "reason": "; ".join(c["reason"] for c in bad),
                                      "quotes": entry["quotes"]})
            continue
        report["claims"].append(entry)
        if g["claim"]:
            kept.append(g["claim"])
        for c in checked:
            kept.append(c["raw"])
    report["kept_lines"] = kept
    if not report["claims"]:
        report["status"] = "nothing_verified"
    return report


def render(report: dict, question: str | None, project: str | None) -> str:
    fm = ["---", "tipo: respuesta-corpus", "escrito_por: modelo", "citable: false",
          f"comprobado_por: {TOOL}", f"fecha: {date.today().isoformat()}",
          f"estado: {report['status']}"]
    if project:
        fm.append(f"proyecto: {project}")
    if question:
        fm.append(f"pregunta: {json.dumps(question, ensure_ascii=False)}")
    fm += [f"citas_comprobadas: {report['quotes_ok']}/{report['quotes']}", "---", ""]
    out = fm + ["> Respuesta escrita por el modelo a partir del corpus. Solo sobrevive lo que va",
                "> respaldado por texto literal del paper; no es evidencia citable.", ""]
    if question:
        out += ["## Pregunta", "", question, ""]
    out += ["## Respuesta", ""]
    if report["status"] == "nothing_verified":
        out += ["Ninguna afirmación pasó la comprobación de citas: trátalo como «no está en el corpus».", ""]
    for block in report["kept_lines"]:
        out += [block, ""]
    if report["removed"]:
        out += ["## Retirado por la comprobación", "",
                "Estas afirmaciones del borrador se quitaron porque su cita no es texto literal del paper:", ""]
        for r in report["removed"]:
            first = (r["text"] or "(cita suelta)").split("\n")[0]
            out.append(f"- «{first[:200]}» — {r['reason']}")
        out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vault", required=True)
    ap.add_argument("--answer", type=Path, help="the draft answer to check")
    ap.add_argument("--list", action="store_true", help="list the papers an answer may quote, then exit")
    ap.add_argument("--project", default=None)
    ap.add_argument("--question", default=None)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if a.list:
        print(json.dumps({"papers": list_papers(a.vault, a.project)}, ensure_ascii=False))
        return 0
    if not a.answer:
        ap.error("--answer is required (or --list)")
    try:
        report = check(a.vault, a.answer.read_text(encoding="utf-8"), a.project)
        if a.out:
            a.out.parent.mkdir(parents=True, exist_ok=True)
            a.out.write_text(render(report, a.question, a.project), encoding="utf-8", newline="\n")
            report["out"] = str(a.out)
    except OSError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    report.pop("kept_lines", None)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
