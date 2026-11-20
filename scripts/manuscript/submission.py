"""Before submitting: does main.tex still say what the vault says, and does the paper
meet its venue's rules? Called by manuscript.py (`sync-check`, `check-venue`, and the
anonymity report of `export --anonymous`), which passes itself in as `ms`.

sync-check (main.tex edited by hand after `export --to-latex-source`):
  - numbers: in every `resultado` section bound to a study question, each number of
    the vault's text that writes one of the question's estimates (its ```estimate-json
    block; as printed, rounded to the token's decimals, or as a percentage) must still
    be in main.tex's part of that section (between its \\label{kairo:sec:<id>} and the
    next kairo label). Reported per section: `removed`, `changed` (a removed number and
    a number main.tex added, paired in order) and `added` (numbers main.tex has and the
    vault text does not: listed, not a failure);
  - citations: every \\cite key is in refs.bib and its `keywords = {kairo:P-XXXX}`
    names a Papers/ note whose key is that key;
  - figures: every \\includegraphics file is in the latex folder and its sha256 is
    the sha256 of a figure in Figuras/registro.json.

check-venue: anonymity (double-blind venues), body pages and section budgets from
main.aux, the venue's required appendices, the AI-use disclosure. Each finding is
{check, severity: problema | aviso, detail}; a `problema` makes the command exit 4.
Standard library only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import latex_parts as lp  # noqa: E402

_SELF_REF = re.compile(
    r"\b(?:our|my)\s+(?:own\s+)?(?:previous|prior|earlier|past|recent)\s+(?:work|paper|study|studies|"
    r"results?|tool|system|measurement)s?\b"
    r"|\bwe\s+(?:previously|earlier|have\s+previously)\s+(?:showed|shown|proposed|presented|measured|found|"
    r"reported|introduced)\b"
    r"|\bnuestros?\s+(?:trabajos?|artículos?|estudios?|resultados?|herramientas?)\s+(?:previos?|anteriores?)\b"
    r"|\ben\s+(?:un|nuestro)\s+(?:trabajo|artículo|estudio)\s+(?:previo|anterior)\s+(?:nuestro\s+)?",
    re.I)
_REPO_URL = re.compile(r"(?:https?://)?(?:www\.)?(?:github|gitlab|bitbucket)\.(?:com|org|io)/[\w.-]+(?:/[\w.-]+)?", re.I)
_AI_HEADING = re.compile(r"(?mi)^#{2,3} +(?:Declaración de uso de IA|AI[- ]use disclosure|AI use disclosure)\b")
_INTERNAL = "eliminar antes de enviar"


def _f(check: str, severity: str, detail: str) -> dict:
    return {"check": check, "severity": severity, "detail": detail}


# --------------------------------------------------------------------------
# Anonymity
# --------------------------------------------------------------------------

def identity_terms(project_dir: Path) -> list[tuple[str, str]]:
    """(term, where) the paper must not contain under double-blind review: the hub's
    `authors:` and `code_remote:` and each line of the project's anonimato.txt."""
    out: list[tuple[str, str]] = []
    hub = project_dir / "_hub.md"
    if hub.is_file():
        text = hub.read_text(encoding="utf-8").replace("\r\n", "\n")
        fm = re.match(r"^---\n(.*?)\n---", text, re.S)
        fm = fm.group(1) if fm else ""
        m = re.search(r"(?m)^authors:[ \t]*\[(.*?)\]", fm)
        names = [x.strip().strip("'\"") for x in m.group(1).split(",")] if m else []
        m = re.search(r"(?m)^authors:[ \t]*\n((?:[ \t]+- .*\n?)+)", fm)
        if m:
            names += [ln.strip()[2:].strip().strip("'\"") for ln in m.group(1).splitlines() if ln.strip()]
        out += [(n, "_hub.md authors") for n in names if len(n) >= 3]
        m = re.search(r"(?m)^code_remote:[ \t]*(\S+)", fm)
        if m and "/" in m.group(1):
            out.append((re.sub(r"^(?:https?://|git@)", "", m.group(1)).removesuffix(".git"), "_hub.md code_remote"))
    anon = project_dir / "anonimato.txt"
    if anon.is_file():
        for ln in anon.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if len(ln) >= 3 and not ln.startswith("#"):
                out.append((ln, "anonimato.txt"))
    return out


def anonymity_findings(ms, vault: Path, project_dir: Path, thread: str, text: str) -> list[dict]:
    """Names, own-repository URLs and self-references in the text a double-blind venue gets."""
    found: list[dict] = []
    low = text.lower()
    for term, where in identity_terms(project_dir):
        if term.lower() in low:
            found.append(_f("anonimato", "problema", f"el texto contiene «{term}» (de {where})"))
    for url in sorted(set(m.group(0) for m in _REPO_URL.finditer(text))):
        found.append(_f("anonimato", "aviso", f"URL de repositorio en el texto: {url} — en revisión doble ciego "
                                              "usa un enlace anónimo (p. ej. anonymous.4open.science)"))
    for m in _SELF_REF.finditer(text):
        a, b = max(0, m.start() - 40), min(len(text), m.end() + 40)
        snippet = " ".join(text[a:b].split())
        found.append(_f("anonimato", "aviso", f"posible autocita en primera persona: «…{snippet}…» — cita tu "
                                              "trabajo previo en tercera persona"))
    return found


# --------------------------------------------------------------------------
# check-venue
# --------------------------------------------------------------------------

def _submission_text(ms, project_dir: Path, thread: str, sections: list[dict], bodies: dict,
                     skip_ack: bool) -> tuple[str, str]:
    """(text, what) the venue gets: main.tex when it is the source, else the sections."""
    out = ms.latex_dir(project_dir, thread)
    if (out / ms.SOURCE_MARK).is_file() and (out / "main.tex").is_file():
        tex = (out / "main.tex").read_text(encoding="utf-8")
        return re.sub(r"(?m)(?<!\\)%.*$", "", tex), "main.tex"
    keep = [s for s in sections if not (skip_ack and (s["id"] in ms.ACK_IDS or s.get("parent") in ms.ACK_IDS))]
    return "\n\n".join(bodies.get(s["id"], "") for s in keep), "las secciones del manuscrito"


def _is_ack(ms, s: dict) -> bool:
    return s["id"] in ms.ACK_IDS or bool(re.search(r"agradecimiento|acknowledg", s["title"], re.I))


def check_venue(ms, vault: Path, project_dir: Path, thread: str, venue_name: str | None = None) -> list[dict]:
    outline, manuscript = ms.paths(project_dir, thread)
    meta, sections, _ = ms.parse_outline(outline)
    bodies = ms.section_bodies(manuscript) if manuscript.exists() else {}
    name, venue, warning = ms.venue_of(meta, venue_name)
    found: list[dict] = []
    if warning:
        found.append(_f("venue", "aviso", f"{warning} (venue «{venue_name or meta.get('venue')}»)"))
    text, what = _submission_text(ms, project_dir, thread, sections, bodies, skip_ack=bool(venue.get("double_blind")))

    if venue.get("double_blind"):
        found += anonymity_findings(ms, vault, project_dir, thread, text)
        for s in sections:
            if _is_ack(ms, s) and not ms.is_placeholder(bodies.get(s["id"], "")):
                found.append(_f("anonimato", "problema" if what == "main.tex" else "aviso",
                                f"la sección «{s['title']}» tiene texto: en revisión doble ciego no se envía "
                                "(export --anonymous la omite)"))

    by_id = {s["id"]: s for s in sections}
    for req in venue.get("requires") or []:
        s = by_id.get(req)
        title = (venue.get("required_titles") or {}).get(req, req)
        if s is None:
            found.append(_f("apendices", "problema", f"{name} exige el apéndice «{title}»: añade la sección {req} "
                                                     "(manuscript.py sections) con placement appendix"))
        elif s.get("placement") != "appendix":
            found.append(_f("apendices", "problema", f"la sección {req} («{title}») debe ir como apéndice "
                                                     "(placement: appendix)"))
        elif ms.is_placeholder(bodies.get(req, "")) and not any(x.get("parent") == req for x in sections):
            found.append(_f("apendices", "problema", f"el apéndice exigido {req} («{title}») está sin redactar"))

    out = ms.latex_dir(project_dir, thread)
    aux = out / "main.aux"
    if not (out / "main.tex").is_file():
        found.append(_f("paginas", "aviso", "aún no hay main.tex: exporta (export --format latex) para medir páginas"))
    elif not aux.is_file():
        found.append(_f("paginas", "aviso", "main.tex sin compilar (no hay main.aux): `manuscript.py pages` "
                                            "lo compila con latexmk y mide las páginas"))
    else:
        labels = lp.aux_labels(aux.read_text(encoding="utf-8", errors="replace"))
        report, warns = ms.page_report(labels, ms.layout(sections, venue), venue)
        for w in warns:
            found.append(_f("paginas", "aviso", w))
        if report["over"]:
            found.append(_f("paginas", "problema", f"el cuerpo ocupa {report['pages']} páginas; {name} admite "
                                                   f"{report['limit']} (sin referencias ni apéndices)"))
        maxp = venue.get("required_max_pages")
        for sp in report["sections_pages"]:
            if sp["over"]:
                found.append(_f("paginas", "aviso", f"la sección {sp['section']} ocupa ~{sp['pages']} páginas; "
                                                    f"su presupuesto es {sp['budget']}"))
            if maxp and sp["section"] in (venue.get("requires") or []) and sp["pages"] is not None \
                    and sp["pages"] > maxp:
                found.append(_f("paginas", "problema", f"el apéndice {sp['section']} ocupa ~{sp['pages']} páginas; "
                                                       f"{name} admite {maxp}"))

    ai = [s for s in sections if s["id"] in ms.AI_IDS and not ms.is_placeholder(bodies.get(s["id"], ""))]
    raw = manuscript.read_text(encoding="utf-8") if manuscript.exists() else ""
    if not ai and not _AI_HEADING.search(raw):
        found.append(_f("declaracion_ia", "problema", "falta la declaración de uso de IA: genérala con "
                                                      "ai_disclosure.py (assemble-manuscript, paso 4)"))
    elif not ai and what != "main.tex":
        found.append(_f("declaracion_ia", "aviso", "la declaración de uso de IA está en el manuscrito pero no en "
                                                   "una sección (ai-disclosure): la exportación no la incluye"))
    if _INTERNAL in text.lower():
        found.append(_f("declaracion_ia", "problema", f"{what} conserva partes internas marcadas «{_INTERNAL}»"))
    return found


# --------------------------------------------------------------------------
# sync-check
# --------------------------------------------------------------------------

_KLABEL = re.compile(r"\\label\{kairo:(sec:[a-z0-9-]+|body-end|refs-start|end)\}")


def tex_segments(tex: str) -> dict[str, str]:
    """section id -> main.tex text from its kairo label to the next kairo label."""
    marks = list(_KLABEL.finditer(tex))
    out = {}
    for i, m in enumerate(marks):
        if m.group(1).startswith("sec:"):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(tex)
            out[m.group(1)[4:]] = tex[m.end():end]
    return out


def _estimate_values(ms, vault: Path, qid: str) -> tuple[list[float] | None, str | None]:
    ms._ledger()
    import designs as _designs  # noqa: PLC0415
    import study_gates  # noqa: PLC0415
    design = _designs.design_for_id(_designs.load(), qid)
    path = study_gates.find_node(Path(vault), qid, design) if design else None
    if path is None:
        return None, f"{qid}: no encontrada en el vault"
    est, err = study_gates._stored_estimate(path)
    if est is None:
        return None, f"{qid}: {err}"
    return lp.estimate_numbers(est.get("estimates", est)), None


def sync_check(ms, vault: Path, project_dir: Path, thread: str) -> dict:
    outline, manuscript = ms.paths(project_dir, thread)
    meta, sections, _ = ms.parse_outline(outline)
    out = ms.latex_dir(project_dir, thread)
    tex_path = out / "main.tex"
    if not tex_path.is_file():
        raise ms.Refused(f"no hay latex-{thread}/main.tex: exporta primero")
    tex = tex_path.read_text(encoding="utf-8")
    body = re.sub(r"(?m)(?<!\\)%.*$", "", tex)
    bodies = ms.section_bodies(manuscript)
    segments = tex_segments(body)
    problems: list[str] = []
    warnings: list[str] = []
    mark = out / ms.SOURCE_MARK
    if not mark.is_file():
        warnings.append("latex-%s/ no está marcada como fuente (export --to-latex-source)" % thread)

    per_section = []
    for s in sections:
        qs = [d for d in s.get("depends_on", []) if d.startswith("Q-")]
        if s["kind"] != "resultado" or not qs:
            continue
        values: list[float] = []
        for q in qs:
            v, err = _estimate_values(ms, vault, q)
            if err:
                warnings.append(f"sección {s['id']}: {err}")
            values += v or []
        md = bodies.get(s["id"], "")
        md_tokens = lp.numbers_md(md)
        for name in lp.MARK_TABFILE.findall(md):
            tf = project_dir / "Manuscritos" / "tablas" / name
            if lp.TABLE_FILE.match(name) and tf.is_file():
                md_tokens += lp.numbers_tex(tf.read_text(encoding="utf-8"))
        seg = segments.get(s["id"])
        if seg is None:
            problems.append(f"main.tex no tiene \\label{{kairo:sec:{s['id']}}}: se compara con todo el documento")
            seg = body
        cmp = lp.compare_numbers(md_tokens, lp.numbers_tex(seg), values)
        per_section.append({"section": s["id"], **cmp})
        for c in cmp["changed"]:
            problems.append(f"sección {s['id']}: la cifra {c['vault']} del vault es {c['tex']} en main.tex")
        for r in cmp["removed"]:
            problems.append(f"sección {s['id']}: la cifra {r} del vault (de una estimación) no está en main.tex")

    cites = {"unknown": [], "no_note": []}
    bib = out / "refs.bib"
    entries = lp.bib_entries(bib.read_text(encoding="utf-8")) if bib.is_file() else {}
    keys = ms._cite_keys(vault)
    for k in lp.cite_keys(body):
        if k not in entries:
            cites["unknown"].append(k)
            problems.append(f"\\cite{{{k}}}: no está en refs.bib")
            continue
        pid = entries[k]
        if pid is None or keys.get(pid) != k:
            cites["no_note"].append(k)
            problems.append(f"\\cite{{{k}}}: no corresponde a una nota P- del vault"
                            + (f" ({pid})" if pid else " (sin keywords kairo:P-XXXX)"))

    figures = []
    try:
        reg = lp.figure_registry(project_dir)
    except lp.PartError as e:
        reg = {}
        problems.append(str(e))
    by_sha = {str(e.get("sha256") or "").lower(): fid for fid, e in reg.items()}
    root = out.resolve()
    for f in lp.included_graphics(body):
        cands = [out / f] + ([] if Path(f).suffix else [out / (f + ext) for ext in lp.FIG_EXT])
        path = next((c for c in cands if c.is_file()), None)
        if path is None or root not in path.resolve().parents:
            figures.append({"file": f, "problem": "no existe en la carpeta latex"})
            problems.append(f"\\includegraphics{{{f}}}: el fichero no está en latex-{thread}/")
            continue
        fid = by_sha.get(lp.sha256_file(path))
        if fid is None:
            figures.append({"file": f, "problem": "sha256 que no es el de ninguna figura registrada"})
            problems.append(f"\\includegraphics{{{f}}}: no coincide con ninguna figura de Figuras/registro.json")
        else:
            figures.append({"file": f, "figure": fid})

    edited = None
    if mark.is_file():
        import json  # noqa: PLC0415
        try:
            edited = json.loads(mark.read_text(encoding="utf-8")).get("main_tex_sha256") != lp.sha256_file(tex_path)
        except (OSError, ValueError):
            edited = None
    return {"ok": not problems, "edited_since_export": edited, "sections": per_section, "cites": cites,
            "figures": figures, "problems": problems, "warnings": warnings}
