#!/usr/bin/env python3
"""Mechanically check a didactic literature review — or a didactic explanation
(kairo:explain) — before it enters the vault.

    check_review.py --vault <vault> --list --project PROJ-XXX
                    [--papers P-0001,P-0002] [--mode proyecto]
    check_review.py --vault <vault> --review draft.md --project PROJ-XXX
                    [--kind revision|explicacion] [--mode papers|celda|proyecto]
                    [--papers P-0001,P-0002] [--cell "M1:fila|col"]
                    [--out Projects/<slug>/Informes/<file>.md] [--title …]

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

`--papers` restricts the sources to those papers (an explanation of one paper,
of several, or of a frontier cell's papers): citing another paper of the
project is marked «⚠ no está entre los papers pedidos», and coverage is
counted over the requested papers only.

Citations of the project's own notes — `[H-0003]`, `[H-0003 §Claim]`,
`[E-0002 §Resultado]`, `[C-0004]` — are checked too, in every mode: the note
must exist under the project's `Hipotesis/`, `Experimentos/` or `Claims/`, and
a `§` locator must name one of its headings. They say what the project did or
proposes, never what a paper says: the note's current `status` is listed under
«Comprobación» straight from its frontmatter, so a stale status in the prose
is visible. `Estado-del-arte.md`, `_hub.md` and the frontier map are maps,
never sources: they are not citable. With `--mode proyecto` (explaining the
project's own work) every paper of the project may be cited and no coverage is
required.

Fenced code blocks (```mermaid diagrams, code) pass through untouched. A
citation inside one is not checked — it is marked under the block, because a
diagram is not where support lives. Every mermaid block is linted with
`scripts/report/check_mermaid.py`; a broken one gets a visible «⚠» line under
it. `<details>` blocks are ordinary text (their citations are checked); the
report counts them and says whether they are balanced. `--kind explicacion`
also reports the explanation's missing sections.

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
sys.path.insert(0, str(HERE.parent / "report"))
from check_mermaid import lint as lint_mermaid  # noqa: E402
from check_quotes import check_quote, is_quote_block, list_papers, paragraphs, parse_quote  # noqa: E402
from send_guard import is_flagged  # noqa: E402  (path set by verifier_packet)
from verifier_packet import fm_scalar, read_text, resolve_citation, split_frontmatter  # noqa: E402

TOOL = "kairo/check_review@1.1.0"
# [P-0015 §3.2] · [P-0015, Tabla 2] · [P-0015 §2; Fig. 3] — one paper per bracket.
_CITE = re.compile(r"\[(P-\d{3,5})(?:[ ,]+([^\]\[]*?))?\]")
# [H-0003] · [H-0003 §Claim] · [E-0002 §Resultado] · [C-0004] — never a [[wiki link]].
_NOTE = re.compile(r"(?<!\[)\[([HEC]-\d{4})(?:[ ,]+([^\]\[]*?))?\](?!\])")
_FENCE = re.compile(r"^\s*(```+|~~~+)")
NOTE_DIRS = {"H": "Hipotesis", "E": "Experimentos", "C": "Claims"}
MODES = ("papers", "celda", "proyecto")
# The sections of a kairo:explain note, in order (skills/explain/SKILL.md).
EXPLAIN_SECTIONS = ("Resumen en una página", "Esquema conceptual", "Cómo funciona el método",
                    "Ejemplo trabajado", "Comparación", "Comprueba que lo has entendido", "Glosario",
                    "Cómo encaja en el proyecto")
QUIZ = "Comprueba que lo has entendido"
QUIZ_RANGE = (4, 6)


def papers_for(vault: str, project: str, only: list[str] | None = None) -> list[dict]:
    """The project's citable papers (or the requested ones among them), with what the review needs."""
    out = []
    for p in list_papers(vault, project):
        if only is not None and p["id"] not in only:
            continue
        fm, body = split_frontmatter(read_text(str(Path(vault, p["path"]))))
        status = fm_scalar(fm, "resolution_status") or ""
        out.append({**p, "year": fm_scalar(fm, "year"), "conflict": status == "mismatch",
                    "abstract": has_abstract(body)})
    return out


def project_dir(vault: str, project: str) -> Path | None:
    """Projects/<slug>/ whose _hub.md has `id: <project>` (or whose folder is named <project>)."""
    for hub in sorted(Path(vault, "Projects").glob("*/_hub.md")):
        if fm_scalar(split_frontmatter(read_text(str(hub)))[0], "id") == project:
            return hub.parent
    d = Path(vault, "Projects", project)
    return d if d.is_dir() else None


def project_notes(vault: str, project: str) -> list[dict]:
    """The project's own notes an explanation of the project may read (never `send: never`)."""
    pdir = project_dir(vault, project)
    if pdir is None:
        return []
    out = []
    for rel in ("_hub.md", "Estado-del-arte.md", "_digest.md", "Frontera/mapa.yaml"):
        f = pdir / rel
        if f.is_file() and not is_flagged(f):
            out.append({"id": None, "kind": "mapa", "path": f.relative_to(vault).as_posix()})
    for prefix, sub in NOTE_DIRS.items():
        for f in sorted((pdir / sub).glob(f"{prefix}-*.md")):
            if is_flagged(f):
                continue
            fm, _ = split_frontmatter(read_text(str(f)))
            out.append({"id": fm_scalar(fm, "id") or f.stem.split(" ")[0], "kind": sub,
                        "path": f.relative_to(vault).as_posix(), "status": fm_scalar(fm, "status")})
    return out


def has_abstract(body: str) -> bool:
    m = re.search(r"^## Resumen\s*\n(.*?)(?=^## |\Z)", body, re.M | re.S)
    text = "\n".join(ln for ln in (m.group(1) if m else "").split("\n") if not ln.startswith(">")).strip()
    return bool(text) and not text.startswith("No disponible")


def listed_as_untreated(review: str) -> set[str]:
    """The P-ids the review itself lists under «## Papers no tratados», with a reason."""
    m = re.search(r"^## Papers no tratados\s*\n(.*?)(?=^## |\Z)", review, re.M | re.S)
    return set(re.findall(r"P-\d{3,5}", m.group(1))) if m else set()


def check_inline(vault: str, pid: str, loc: str, papers: dict[str, dict],
                 project_papers: set[str] | None = None) -> str | None:
    """None when the citation holds; otherwise the reason, shown next to it."""
    p = papers.get(pid)
    if not p:
        if project_papers is not None and pid in project_papers:
            return "no está entre los papers pedidos"
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


def _norm_heading(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def check_note(nid: str, loc: str, notes: dict[str, dict]) -> str | None:
    """A project-note citation: the note exists in the project; a § locator names one of its headings."""
    n = notes.get(nid)
    if not n:
        return "no es una nota de este proyecto"
    loc = loc.strip()
    if not loc:
        return None
    if not loc.startswith("§"):
        return "el localizador de una nota del proyecto es «§<encabezado>»"
    want = _norm_heading(loc[1:])
    if want not in n["headings"]:
        return "encabezado no encontrado en la nota"
    return None


def _notes_index(vault: str, project: str) -> dict[str, dict]:
    out = {}
    for n in project_notes(vault, project):
        if not n["id"]:
            continue
        fm, body = split_frontmatter(read_text(str(Path(vault, n["path"]))))
        heads = {_norm_heading(h) for h in re.findall(r"^#{2,4} +(.+?)\s*$", body, re.M)}
        out[n["id"]] = {**n, "headings": heads, "vale_la_pena": fm_scalar(fm, "vale_la_pena")}
    return out


def segments(review: str) -> list[tuple[str, list[str]]]:
    """("fence", lines) for each fenced code block, kept whole; ("text", par) for each
    paragraph outside them, with runs of «>» lines split from the text around them
    (a quote glued to a line of prose is still a quote, and still checked)."""
    out: list[tuple[str, list[str]]] = []
    buf: list[str] = []
    fence: list[str] | None = None
    marker = ""

    def flush():
        for par in paragraphs("\n".join(buf)):
            run: list[str] = []
            for ln in par:
                if run and ln.lstrip().startswith(">") != run[-1].lstrip().startswith(">"):
                    out.append(("text", run))
                    run = []
                run.append(ln)
            if run:
                out.append(("text", run))
        buf.clear()

    for line in review.replace("\r\n", "\n").split("\n"):
        if fence is None:
            m = _FENCE.match(line)
            if m:
                flush()
                fence, marker = [line], m.group(1)
            else:
                buf.append(line)
            continue
        fence.append(line)
        s = line.strip()
        if s.startswith(marker) and not s.strip(marker[0]):
            out.append(("fence", fence))
            fence = None
    if fence is not None:
        out.append(("fence", fence))
    flush()
    return out


def _sections(review: str) -> dict[str, str]:
    return {m.group(1).strip(): m.group(2) for m in
            re.finditer(r"^## +(.+?)\s*\n(.*?)(?=^## |\Z)", review, re.M | re.S)}


def check(vault: str, review: str, project: str, *, only: list[str] | None = None,
          mode: str = "papers", kind: str = "revision") -> dict:
    papers = {p["id"]: p for p in papers_for(vault, project, only)}
    project_papers = {p["id"] for p in list_papers(vault, project)} if only is not None else None
    notes = _notes_index(vault, project)
    report: dict = {"tool": TOOL, "kind": kind, "mode": mode, "quotes": 0, "quotes_ok": 0,
                    "citations": 0, "citations_ok": 0, "note_citations": 0, "note_citations_ok": 0,
                    "retired": [], "flagged": [], "cited": [], "uncited": [], "notes_cited": [], "text": ""}
    if only is not None:
        report["requested"] = list(only)
        report["requested_missing"] = sorted(set(only) - papers.keys())
    cited: set[str] = set()
    notes_cited: set[str] = set()
    out_pars: list[str] = []

    def mark(m: re.Match) -> str:
        pid, loc = m.group(1), (m.group(2) or "").strip()
        report["citations"] += 1
        cited.add(pid)
        why = check_inline(vault, pid, loc, papers, project_papers)
        if why is None:
            report["citations_ok"] += 1
            return m.group(0)
        report["flagged"].append({"paper": pid, "locator": loc, "reason": why})
        return f"[{pid}{' ' + loc if loc else ''} ⚠ {why}]"

    def mark_note(m: re.Match) -> str:
        nid, loc = m.group(1), (m.group(2) or "").strip()
        report["note_citations"] += 1
        why = check_note(nid, loc, notes)
        if why is None:
            report["note_citations_ok"] += 1
            notes_cited.add(nid)
            return m.group(0)
        report["flagged"].append({"note": nid, "locator": loc, "reason": why})
        return f"[{nid}{' ' + loc if loc else ''} ⚠ {why}]"

    lint = lint_mermaid(review)
    bad_lines = {}
    for p in lint["problems"]:
        bad_lines.setdefault(p["block"], []).append(p)
    mermaid_n = 0
    for kind_, par in segments(review):
        if kind_ == "fence":
            out_pars.append("\n".join(par))
            body = "\n".join(par[1:])
            if re.match(r"^\s*(```+|~~~+)\s*mermaid\s*$", par[0]):
                mermaid_n += 1
                for p in bad_lines.get(mermaid_n, []):
                    out_pars.append(f"> ⚠ Diagrama con un problema de sintaxis (línea {p['line']} del borrador): "
                                    f"{p['reason']}.")
            inside = [m.group(1) for m in _CITE.finditer(body)] + [m.group(1) for m in _NOTE.finditer(body)]
            if inside:
                why = "cita dentro de un diagrama o bloque de código: no se comprueba; ponla en el texto"
                report["flagged"] += [{"paper" if i.startswith("P-") else "note": i, "locator": "", "reason": why}
                                      for i in inside]
                out_pars.append(f"> ⚠ El bloque anterior cita {', '.join(dict.fromkeys(inside))}: "
                                "una cita dentro de un diagrama no se comprueba; su respaldo está en el texto.")
            continue
        if is_quote_block(par):
            q = parse_quote(par)
            report["quotes"] += 1
            if q["paper"]:
                cited.add(q["paper"])
            p = papers.get(q["paper"] or "")
            if p and p["conflict"]:
                c = {**q, "ok": False, "reason": "referencia en conflicto: no es respaldo hasta corregirla"}
            elif q["paper"] and not p and project_papers is not None and q["paper"] in project_papers:
                c = {**q, "ok": False, "reason": "no está entre los papers pedidos"}
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
        out_pars.append(_NOTE.sub(mark_note, _CITE.sub(mark, "\n".join(par))))

    report["cited"] = sorted(cited & papers.keys())
    report["uncited"] = sorted(set(papers) - cited)
    # Coverage: a paper with something to read (text or abstract) that the review
    # neither cites nor explains under «Papers no tratados» was skipped. An
    # explanation of the project's own work owes no coverage of the corpus.
    readable = {pid for pid, p in papers.items() if p["fulltext"] or p["abstract"]}
    report["unaccounted"] = [] if mode == "proyecto" else sorted(readable - cited - listed_as_untreated(review))
    report["papers"] = len(papers)
    report["notes_cited"] = [{"id": n, "status": notes[n]["status"], "vale_la_pena": notes[n]["vale_la_pena"],
                              "path": notes[n]["path"]} for n in sorted(notes_cited)]
    opened, closed = len(re.findall(r"<details\b", review)), len(re.findall(r"</details>", review))
    report["details"] = {"blocks": opened, "balanced": opened == closed}
    report["diagrams"] = {"blocks": lint["blocks"], "ok": lint["ok"], "problems": lint["problems"]}
    if kind == "explicacion":
        secs = _sections(review)
        need = [s for s in EXPLAIN_SECTIONS
                if not (s == "Comparación" and (mode == "proyecto" or len(papers) < 2))]
        report["missing_sections"] = [s for s in need if s not in secs]
        report["questions"] = len(re.findall(r"<details\b", secs.get(QUIZ, "")))
    report["text"] = "\n\n".join(out_pars)
    return report


HEADER = {
    "revision": ["> Escrita por un modelo a partir de los papers del proyecto, para entender el campo.",
                 "> Las explicaciones son interpretación del modelo; lo que se atribuye a un paper va",
                 "> con su referencia [P-XXXX §sección], comprobada por Kairo. No es evidencia citable:",
                 "> para citar, ve al paper."],
    "explicacion": ["> Explicación escrita por un modelo para entender, no para citar. Lo que se atribuye a un",
                    "> paper va con su referencia [P-XXXX §sección] y lo que se atribuye al proyecto con la",
                    "> nota [H-XXXX], ambas comprobadas por Kairo. Los esquemas, ejemplos y preguntas son del",
                    "> modelo. No es evidencia citable: para citar, ve al paper."],
}


def render(report: dict, project: str, title: str | None, *, cell: str | None = None) -> str:
    kind = report.get("kind", "revision")
    explain = kind == "explicacion"
    fm = ["---", f"tipo: {'explicacion' if explain else 'revision-literatura'}"]
    if explain:
        fm.append(f"modo: {report.get('mode', 'papers')}")
        if report.get("requested") is not None:
            fm.append(f"papers: [{', '.join(report['requested'])}]")
        if cell:
            fm.append(f"celda: {cell}")
    fm += ["escrito_por: modelo", "citable: false",
           f"comprobado_por: {TOOL}", f"fecha: {date.today().isoformat()}", f"proyecto: {project}",
           f"citas_literales: {report['quotes_ok']}/{report['quotes']}",
           f"referencias_comprobadas: {report['citations_ok']}/{report['citations']}",
           f"papers_citados: {len(report['cited'])}/{report['papers']}"]
    if report.get("note_citations"):
        fm.append(f"notas_comprobadas: {report['note_citations_ok']}/{report['note_citations']}")
    if report["diagrams"]["blocks"]:
        fm.append(f"diagramas_ok: {report['diagrams']['ok']}/{report['diagrams']['blocks']}")
    if explain:
        fm.append(f"preguntas: {report.get('questions', 0)}")
    fm += ["---", ""]
    default = "Explicación didáctica" if explain else "Revisión didáctica de la literatura"
    out = fm + [f"# {title or default}", ""] + HEADER[kind] + ["", report["text"], ""]
    out += ["## Comprobación", "",
            f"- Citas literales comprobadas letra a letra: {report['quotes_ok']} de {report['quotes']}.",
            f"- Referencias con localizador que existe en la nota: {report['citations_ok']} de {report['citations']}.",
            f"- Papers {'pedidos' if report.get('requested') is not None else 'del proyecto'} citados: "
            f"{len(report['cited'])} de {report['papers']}."]
    if report.get("note_citations"):
        out.append(f"- Referencias a notas del proyecto que existen: {report['note_citations_ok']} de "
                   f"{report['note_citations']}.")
    if report["diagrams"]["blocks"]:
        out.append(f"- Diagramas sin problemas de sintaxis: {report['diagrams']['ok']} de "
                   f"{report['diagrams']['blocks']}.")
    out.append("")
    if report["notes_cited"]:
        out += ["Estado actual de las notas del proyecto citadas (de su frontmatter, no del texto):", "",
                "| Nota | Estado | ¿Merece la pena? |", "|---|---|---|"]
        out += [f"| {n['id']} | {n['status'] or '—'} | {n['vale_la_pena'] or '—'} |" for n in report["notes_cited"]]
        out.append("")
    if report["retired"]:
        out += ["Citas retiradas (no eran texto literal del paper):", ""]
        out += [f"- {r['paper'] or '?'} {r['locator'] or ''}: «{r['quote']}» — {r['reason']}" for r in report["retired"]]
        out.append("")
    if report["flagged"]:
        out += ["Referencias marcadas con ⚠ en el texto:", ""]
        out += [f"- {f.get('paper') or f.get('note')} {f['locator']}: {f['reason']}" for f in report["flagged"]]
        out.append("")
    if report.get("requested_missing"):
        out += ["⚠ Papers pedidos que no son papers citables del proyecto: "
                + ", ".join(report["requested_missing"]) + ".", ""]
    who = "la explicación" if explain else "la revisión"
    if report["uncited"]:
        out += [f"Papers {'pedidos' if report.get('requested') is not None else 'del proyecto'} que {who} no cita: "
                + ", ".join(report["uncited"]) + ".", ""]
    if report["unaccounted"]:
        out += [f"⚠ Papers con texto o resumen que {who} ni cita ni explica por qué deja fuera: "
                + ", ".join(report["unaccounted"]) + ".", ""]
    if explain and report.get("missing_sections"):
        out += ["⚠ Secciones que faltan: " + ", ".join(f"«{s}»" for s in report["missing_sections"]) + ".", ""]
    if explain:
        lo, hi = QUIZ_RANGE
        if not lo <= report.get("questions", 0) <= hi:
            out += [f"⚠ «{QUIZ}» tiene {report.get('questions', 0)} preguntas con respuesta plegada "
                    f"(se esperan de {lo} a {hi}).", ""]
    if not report["details"]["balanced"]:
        out += ["⚠ Hay bloques <details> sin cerrar: alguna respuesta puede verse desplegada o cortada.", ""]
    return "\n".join(out)


def _ids(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    ids = [x.strip() for x in re.split(r"[,\s]+", raw) if x.strip()]
    bad = [x for x in ids if not re.fullmatch(r"P-\d{3,5}", x)]
    if bad or not ids:
        raise ValueError(f"--papers takes P-ids separated by commas, not {raw!r}")
    return list(dict.fromkeys(ids))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vault", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--list", action="store_true", help="list the papers (and notes) that may be cited, then exit")
    ap.add_argument("--review", type=Path, help="the draft review to check")
    ap.add_argument("--kind", choices=("revision", "explicacion"), default="revision")
    ap.add_argument("--mode", choices=MODES, default="papers")
    ap.add_argument("--papers", default=None, help="restrict the sources to these P-ids (comma-separated)")
    ap.add_argument("--cell", default=None, help="the frontier cell explained (M1:fila|col), recorded in the note")
    ap.add_argument("--title", default=None)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        only = _ids(a.papers)
        if a.list:
            listing: dict = {"papers": papers_for(a.vault, a.project, only)}
            if only is not None:
                listing["requested_missing"] = sorted(set(only) - {p["id"] for p in listing["papers"]})
            pdir = project_dir(a.vault, a.project)
            listing["project_dir"] = pdir.relative_to(a.vault).as_posix() if pdir else None
            if a.mode == "proyecto":
                listing["project_notes"] = project_notes(a.vault, a.project)
            print(json.dumps(listing, ensure_ascii=False))
            return 0
        if not a.review:
            ap.error("--review is required (or --list)")
        report = check(a.vault, a.review.read_text(encoding="utf-8"), a.project, only=only, mode=a.mode,
                       kind=a.kind)
        if a.out:
            a.out.parent.mkdir(parents=True, exist_ok=True)
            a.out.write_text(render(report, a.project, a.title, cell=a.cell), encoding="utf-8", newline="\n")
            report["out"] = str(a.out)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    report.pop("text", None)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
