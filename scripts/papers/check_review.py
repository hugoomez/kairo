#!/usr/bin/env python3
"""Mechanically check a didactic literature review before it enters the vault.

    check_review.py --vault <vault> --list --project PROJ-XXX
    check_review.py --vault <vault> --review draft.md --project PROJ-XXX
                    [--out Projects/<slug>/Informes/<file>.md]

A literature review explains (that prose is the model's, and the note says so),
but every statement about what a paper says carries a citation:

    inline:  … el embedding se aprende con una GNN [P-0015 §3.2].
    quoted:  > <exact words from the paper>
             > — P-0015 §3.2

What is checked, with the fresh-verifier's own resolver
(`verifier_packet.resolve_citation`) — nothing is judged by a model:

- every quote block must be the paper's own words under that locator
  (`check_quotes.check_quote`); a failing quote is taken out and a visible
  «⚠ cita retirada» line with the reason is left in its place;
- every inline citation must name a paper of the project, and its locator must
  point at text in the note; otherwise it is kept but marked «⚠ …» in place;
- a paper whose identifiers conflict (`resolution_status: mismatch`) is marked
  «⚠ referencia en conflicto» wherever it is cited: it is not support until the
  researcher fixes it;
- the papers of the project that the review never cites are listed.

`send: never` papers, `Papers/_notas/` and papers of other projects never count
as sources. With `--out`, writes the note marked `escrito_por: modelo`,
`citable: false`: a reading aid, never evidence. Prints a JSON report.

Exit codes: 0 checked · 1 error. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "ledger"))
from check_quotes import check_quote, is_quote_block, list_papers, paragraphs, parse_quote  # noqa: E402
from verifier_packet import fm_scalar, read_text, resolve_citation, split_frontmatter  # noqa: E402

TOOL = "kairo/check_review@1.0.0"
# [P-0015 §3.2] · [P-0015, Tabla 2] · [P-0015 §2; Fig. 3] — one paper per bracket.
_CITE = re.compile(r"\[(P-\d{3,5})(?:[ ,]+([^\]\[]*?))?\]")


def papers_for(vault: str, project: str) -> list[dict]:
    """The project's citable papers, with what the review needs to know about each."""
    out = []
    for p in list_papers(vault, project):
        fm, body = split_frontmatter(read_text(str(Path(vault, p["path"]))))
        status = fm_scalar(fm, "resolution_status") or ""
        out.append({**p, "year": fm_scalar(fm, "year"), "conflict": status == "mismatch",
                    "abstract": has_abstract(body)})
    return out


def has_abstract(body: str) -> bool:
    m = re.search(r"^## Resumen\s*\n(.*?)(?=^## |\Z)", body, re.M | re.S)
    text = "\n".join(ln for ln in (m.group(1) if m else "").split("\n") if not ln.startswith(">")).strip()
    return bool(text) and not text.startswith("No disponible")


def listed_as_untreated(review: str) -> set[str]:
    """The P-ids the review itself lists under «## Papers no tratados», with a reason."""
    m = re.search(r"^## Papers no tratados\s*\n(.*?)(?=^## |\Z)", review, re.M | re.S)
    return set(re.findall(r"P-\d{3,5}", m.group(1))) if m else set()


def check_inline(vault: str, pid: str, loc: str, papers: dict[str, dict]) -> str | None:
    """None when the citation holds; otherwise the reason, shown next to it."""
    p = papers.get(pid)
    if not p:
        return "no es un paper del proyecto"
    if p["conflict"]:
        return "referencia en conflicto: no es respaldo hasta corregirla"
    if not loc.strip():
        return "sin localizador"
    res = resolve_citation(vault, pid, loc)
    if not res["source"]:
        return res["note"] or "paper no disponible"
    if not res["units"]:
        return "localizador no encontrado en la nota"
    return None


def check(vault: str, review: str, project: str) -> dict:
    papers = {p["id"]: p for p in papers_for(vault, project)}
    report: dict = {"tool": TOOL, "quotes": 0, "quotes_ok": 0, "citations": 0, "citations_ok": 0,
                    "retired": [], "flagged": [], "cited": [], "uncited": [], "text": ""}
    cited: set[str] = set()
    out_pars: list[str] = []

    def mark(m: re.Match) -> str:
        pid, loc = m.group(1), (m.group(2) or "").strip()
        report["citations"] += 1
        cited.add(pid)
        why = check_inline(vault, pid, loc, papers)
        if why is None:
            report["citations_ok"] += 1
            return m.group(0)
        report["flagged"].append({"paper": pid, "locator": loc, "reason": why})
        return f"[{pid}{' ' + loc if loc else ''} ⚠ {why}]"

    for par in paragraphs(review):
        if is_quote_block(par):
            q = parse_quote(par)
            report["quotes"] += 1
            if q["paper"]:
                cited.add(q["paper"])
            p = papers.get(q["paper"] or "")
            if p and p["conflict"]:
                c = {**q, "ok": False, "reason": "referencia en conflicto: no es respaldo hasta corregirla"}
            else:
                c = check_quote(vault, q, project)
            if c["ok"]:
                report["quotes_ok"] += 1
                out_pars.append("\n".join(par))
            else:
                report["retired"].append({"paper": q["paper"], "locator": q["locator"], "quote": q["quote"][:200],
                                          "reason": c["reason"]})
                where = f" ({q['paper']} {q['locator']})" if q["paper"] else ""
                out_pars.append(f"> ⚠ Cita retirada por la comprobación{where}: {c['reason']}.")
            continue
        out_pars.append(_CITE.sub(mark, "\n".join(par)))

    report["cited"] = sorted(cited & papers.keys())
    report["uncited"] = sorted(set(papers) - cited)
    # Coverage: a paper with something to read (text or abstract) that the review
    # neither cites nor explains under «Papers no tratados» was skipped.
    readable = {pid for pid, p in papers.items() if p["fulltext"] or p["abstract"]}
    report["unaccounted"] = sorted(readable - cited - listed_as_untreated(review))
    report["papers"] = len(papers)
    report["text"] = "\n\n".join(out_pars)
    return report


def render(report: dict, project: str, title: str | None) -> str:
    fm = ["---", "tipo: revision-literatura", "escrito_por: modelo", "citable: false",
          f"comprobado_por: {TOOL}", f"fecha: {date.today().isoformat()}", f"proyecto: {project}",
          f"citas_literales: {report['quotes_ok']}/{report['quotes']}",
          f"referencias_comprobadas: {report['citations_ok']}/{report['citations']}",
          f"papers_citados: {len(report['cited'])}/{report['papers']}", "---", ""]
    out = fm + [f"# {title or 'Revisión didáctica de la literatura'}", "",
                "> Escrita por un modelo a partir de los papers del proyecto, para entender el campo.",
                "> Las explicaciones son interpretación del modelo; lo que se atribuye a un paper va",
                "> con su referencia [P-XXXX §sección], comprobada por Kairo. No es evidencia citable:",
                "> para citar, ve al paper.", "",
                report["text"], ""]
    out += ["## Comprobación", "",
            f"- Citas literales comprobadas letra a letra: {report['quotes_ok']} de {report['quotes']}.",
            f"- Referencias con localizador que existe en la nota: {report['citations_ok']} de {report['citations']}.",
            f"- Papers del proyecto citados: {len(report['cited'])} de {report['papers']}.", ""]
    if report["retired"]:
        out += ["Citas retiradas (no eran texto literal del paper):", ""]
        out += [f"- {r['paper'] or '?'} {r['locator'] or ''}: «{r['quote']}» — {r['reason']}" for r in report["retired"]]
        out.append("")
    if report["flagged"]:
        out += ["Referencias marcadas con ⚠ en el texto:", ""]
        out += [f"- {f['paper']} {f['locator']}: {f['reason']}" for f in report["flagged"]]
        out.append("")
    if report["uncited"]:
        out += ["Papers del proyecto que la revisión no cita: " + ", ".join(report["uncited"]) + ".", ""]
    if report["unaccounted"]:
        out += ["⚠ Papers con texto o resumen que la revisión ni cita ni explica por qué deja fuera: "
                + ", ".join(report["unaccounted"]) + ".", ""]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vault", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--list", action="store_true", help="list the papers the review may cite, then exit")
    ap.add_argument("--review", type=Path, help="the draft review to check")
    ap.add_argument("--title", default=None)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if a.list:
            print(json.dumps({"papers": papers_for(a.vault, a.project)}, ensure_ascii=False))
            return 0
        if not a.review:
            ap.error("--review is required (or --list)")
        report = check(a.vault, a.review.read_text(encoding="utf-8"), a.project)
        if a.out:
            a.out.parent.mkdir(parents=True, exist_ok=True)
            a.out.write_text(render(report, a.project, a.title), encoding="utf-8", newline="\n")
            report["out"] = str(a.out)
    except OSError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    report.pop("text", None)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
