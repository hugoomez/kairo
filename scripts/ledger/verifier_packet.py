#!/usr/bin/env python3
"""Build the ONE self-contained packet the fresh-verifier agent receives.

The fresh-verifier is only worth anything if it never sees the reasoning that
produced the artifact it checks. This script makes that isolation mechanical:
it reads the note(s) and emits a single text packet built by ALLOW-LIST --
anything not explicitly listed below is left out, whatever it is called.

Included (hypothesis note, or a draft note file outside the vault):
    frontmatter `id` only
    ## Claim
    ## Justificación (evidencia citada)  -- each bullet verbatim (the assertion
        is part of the artifact), and for every `P-XXXX <locator>` in it, the
        VERBATIM units of that paper's `## Texto completo` that the locator
        points at (so the citation can be checked against its source), plus
        each cited paper's `## Resumen` once, labelled as paper-level context
        that is NOT the text of any locator
    a test-sketch section if present (## Esbozo del test / ## Test sketch)
Included (each --experiment note):
    frontmatter: id, hypothesis, tier, analysis_plan, role, rung,
                 experiment_validity, sanity_checks, result
    ## Predicción, ## Variables, ## Diseño, ## Plan de análisis,
    ## Umbral de invalidez, ## Resultado, and ## Enmiendas (labelled: the
    amendments are part of the frozen record the analysis ran under)
Included (each --analysis-output file): the file verbatim.

Everything else is excluded -- notably ## Revisión del ciclo, ## Hipótesis
rival descartada, ## Lección, ## Verificación independiente, history,
generated_by, needs_human_review, confidence, status, verifications, and any
chat or critique. The manifest (stderr, and --manifest FILE as JSON) lists
exactly what was included and excluded from each source, plus the packet's
sha256, so a run can show precisely what the verifier received.

With --section "<heading>" only that one `##` section of --note is included
(scope `section:<heading>`); citations are still resolved if it is the
Justificación section.

Standard library only.

Usage:
    python verifier_packet.py --vault <vault_root> --note <H-XXXX.md | draft.md>
        [--experiment E-XXXX.md ...] [--analysis-output file ...]
        [--section "<heading>"] [--out packet.md] [--manifest manifest.json]
        [--json]

`send: never` (A3, scripts/security/send_guard.py): a flagged --note or
--experiment is refused (exit 2) -- its content must never reach a model, so
it is never verified. A cited paper flagged `send: never` contributes no
source text and no abstract; its citation is marked as not sent.

Exit codes: 0 ok, 2 invalid input (missing file, unknown section, a
`send: never` note).
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "security"))
from send_guard import is_flagged  # noqa: E402  (A3's single definition of the flag)

__version__ = "1.2.0"
TOOL_ID = f"kairo/verifier_packet@{__version__}"

# --------------------------------------------------------------------------
# Allow-lists
# --------------------------------------------------------------------------

HYP_FM_KEYS = ("id",)
EXP_FM_KEYS = ("id", "hypothesis", "tier", "analysis_plan", "role", "rung",
               "experiment_validity", "sanity_checks", "result")
EXP_SECTIONS = ("Predicción", "Variables", "Diseño", "Plan de análisis",
                "Umbral de invalidez", "Resultado", "Enmiendas")
TEST_SKETCH_RE = re.compile(
    r"^(test sketch|esbozo( del| de)? test|boceto( del| de)? test)\b", re.I)


# Sections that carry reasoning, critiques or prior verdicts. Never verifiable
# on their own and never sent in any mode — `--section` cannot reach them.
NEVER_SECTIONS = ("Revisión del ciclo", "Hipótesis rival descartada", "Lección",
                  "Verificación independiente")

# Source fields must hold only text fetched from the paper (create-project step
# 5). These markers betray model-written text standing in for the source; a
# citation resting on it is flagged in the packet and fresh-verifier reports
# it as `crítico`.
MODEL_TEXT_RE = re.compile(
    r"general knowledge|conocimiento general|from memory|de memoria|"
    r"known bibliographic facts|según resumen|summary from|resumen de memoria",
    re.IGNORECASE)


def provenance_problems(text: str) -> list[str]:
    """The model-text markers found in `text` (deduplicated, in order)."""
    seen: list[str] = []
    for m in MODEL_TEXT_RE.finditer(text):
        if m.group(0).lower() not in seen:
            seen.append(m.group(0).lower())
    return seen


def never_sent(heading: str) -> bool:
    return any(heading.strip().startswith(n) for n in NEVER_SECTIONS)


def hyp_section_allowed(heading: str) -> bool:
    if heading == "Claim":
        return True
    if heading.startswith("Justificación"):
        return True
    return bool(TEST_SKETCH_RE.match(heading))


# --------------------------------------------------------------------------
# Note parsing
# --------------------------------------------------------------------------

def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8-sig") as fh:
        return fh.read().replace("\r\n", "\n").replace("\r", "\n")


def split_frontmatter(text: str) -> tuple[list[str], str]:
    """Return (frontmatter lines, body). Frontmatter absent -> ([], text)."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return [], text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], "\n".join(lines[i + 1:])
    return [], text


def frontmatter_blocks(fm_lines: list[str]) -> list[tuple[str, list[str]]]:
    """Split frontmatter into top-level (key, verbatim lines) blocks.

    Comment-only / blank lines before a key attach to nothing (dropped): they
    are template commentary, not data.
    """
    blocks: list[tuple[str, list[str]]] = []
    key_re = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:")
    for line in fm_lines:
        m = key_re.match(line)
        if m:
            blocks.append((m.group(1), [line]))
        elif blocks and (line.startswith((" ", "\t", "-")) and line.strip()):
            blocks[-1][1].append(line)
    return blocks


def fm_scalar(fm_lines: list[str], key: str) -> str | None:
    for k, lines in frontmatter_blocks(fm_lines):
        if k == key:
            val = lines[0].split(":", 1)[1]
            val = val.split(" #", 1)[0].strip().strip('"').strip("'")
            return val or None
    return None


def body_sections(body: str) -> list[tuple[str, str]]:
    """Split a body into (heading, text) for each `## ` section, in order.

    Text before the first `## ` heading gets heading "" (preamble). `###`
    subsections stay inside their parent. Headings inside ``` fences ignored.
    """
    sections: list[tuple[str, list[str]]] = [("", [])]
    in_fence = False
    for line in body.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        if not in_fence and line.startswith("## "):
            sections.append((line[3:].strip(), []))
            continue
        sections[-1][1].append(line)
    out = []
    for h, lines in sections:
        text = "\n".join(lines).strip("\n")
        if h == "" and not text.strip():
            continue
        out.append((h, text))
    return out


# --------------------------------------------------------------------------
# Citation parsing and source lookup
# --------------------------------------------------------------------------

P_REF_RE = re.compile(r"\bP-(\d{4})\b")
SPAN_STOP_RE = re.compile(r"\bP-\d{4}\b|—|–\s|;|:\s| - ")


def justification_bullets(text: str) -> list[str]:
    """Top-level bullets of a section, each with its continuation lines."""
    bullets: list[list[str]] = []
    for line in text.split("\n"):
        if re.match(r"^[-*]\s+", line):
            bullets.append([line])
        elif bullets and line.strip() and (line.startswith((" ", "\t"))):
            bullets[-1].append(line)
        elif bullets and not line.strip():
            bullets.append([])  # blank ends the bullet
    return ["\n".join(b) for b in bullets if b]


def citation_refs(bullet: str) -> list[tuple[str, str]]:
    """[(paper_id, locator_span)] for every P-XXXX in a bullet, in order."""
    flat = re.sub(r"\s*\n\s*", " ", bullet)
    refs = []
    located: set[str] = set()
    for m in P_REF_RE.finditer(flat):
        pid = f"P-{m.group(1)}"
        rest = flat[m.end():]
        stop = SPAN_STOP_RE.search(rest)
        span = rest[:stop.start()] if stop else rest
        span = re.sub(r"\s+y\s*$", "", span.strip()).strip(" ,")
        if LOCATOR_START_RE.match(span):
            located.add(pid)
        elif pid in located:
            continue  # a prose mention of an already-cited paper, not a citation
        else:
            span = ""  # cited with no locator at all
        refs.append((pid, span))
    return refs


LOCATOR_START_RE = re.compile(
    r"^(?:§|App(?:endix)?\b|Apéndice|Fig|Tabla|Table|Eq\b|Ec\b)")


NUM = r"[A-Z]?\d+(?:\.\d+)*"


def _expand_range(a: str, b: str) -> list[str]:
    pa, pb = a.split("."), b.split(".")
    if (len(pa) == len(pb) and pa[:-1] == pb[:-1]
            and pa[-1].isdigit() and pb[-1].isdigit()
            and 0 <= int(pb[-1]) - int(pa[-1]) <= 20):
        prefix = ".".join(pa[:-1])
        return [(prefix + "." if prefix else "") + str(i)
                for i in range(int(pa[-1]), int(pb[-1]) + 1)]
    return [a, b]


def locator_tokens(span: str) -> list[tuple[str, str]]:
    """Parse a locator span into [(kind, value)].

    kinds: sec, app, fig, table, eq, named.
    """
    toks: list[tuple[str, str]] = []
    s = span
    # appendix first, so "App A.5" is not read as a section
    for m in re.finditer(r"\b(?:App(?:endix)?\.?|Apéndice)\s*([A-Z](?:\.\d+)*)", s):
        toks.append(("app", m.group(1)))
    s_noapp = re.sub(r"\b(?:App(?:endix)?\.?|Apéndice)\s*[A-Z](?:\.\d+)*", " ", s)
    for m in re.finditer(r"\b(?:Fig(?:ura|ure)?s?)\.?\s*(\d+)", s_noapp):
        toks.append(("fig", m.group(1)))
    for m in re.finditer(r"\b(?:Tabla|Table)s?\.?\s*(\d+)", s_noapp):
        toks.append(("table", m.group(1)))
    for m in re.finditer(r"\b(?:Eq|Ec)\.?\s*(\d+)", s_noapp):
        toks.append(("eq", m.group(1)))
    # section numbers: "§5.1–§5.2", "§4.1, §5.2", "§6", "§ 3"
    for m in re.finditer(rf"§\s*({NUM})(?:\s*[–-]\s*§?\s*({NUM}))?", s_noapp):
        if m.group(2):
            toks.extend(("sec", v) for v in _expand_range(m.group(1), m.group(2)))
        else:
            toks.append(("sec", m.group(1)))
    # bare continuation numbers after a § list: "§5.4, 9.2" is rare; skip.
    for m in re.finditer(r"§\s*([^\W\d][\w\s]*?)(?=[,;.)]|$)", s_noapp):
        # "§Resumen." / "§Resumen)" / "§Setup." name the section without the punctuation
        toks.append(("named", m.group(1).strip()))
    seen, out = set(), []
    for t in toks:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def token_label(tok: tuple[str, str]) -> str:
    kind, v = tok
    return {"sec": f"§{v}", "app": f"App. {v}", "fig": f"Fig. {v}",
            "table": f"Tabla {v}", "eq": f"Eq. {v}", "named": f"§{v}"}[kind]


HEADING_NUM_RE = re.compile(r"^(?:§\s*)?(\d+(?:\.\d+)*)\.?(?:\s|$)")
APPENDIX_HEAD_RE = re.compile(r"^(?:Appendix|Apéndice)\s+([A-Z])\b")
APPENDIX_SUB_RE = re.compile(r"^([A-Z](?:\.\d+)+)\.?\s")


def heading_num(title: str) -> str | None:
    """Section number a heading sets: "3.2" for "3.2 Title", "A" for
    "Appendix A: Title", "A.5" for "A.5 Title"; None when unnumbered."""
    for rx in (HEADING_NUM_RE, APPENDIX_HEAD_RE, APPENDIX_SUB_RE):
        m = rx.match(title)
        if m:
            return m.group(1)
    return None


def source_units(texto: str) -> list[dict]:
    """Split a `## Texto completo` body into units with section context.

    Units are bullets (with continuation lines) or paragraphs. Headings
    (`###`+ or a whole-line **bold** heading) set the section context.
    """
    units: list[dict] = []
    heading_nums: dict[int, str | None] = {}
    current: dict | None = None

    def ctx_num() -> str | None:
        for lvl in sorted(heading_nums, reverse=True):
            if heading_nums[lvl]:
                return heading_nums[lvl]
        return None

    def close():
        nonlocal current
        if current is not None:
            current["text"] = "\n".join(current["lines"]).strip()
            if current["text"]:
                units.append(current)
        current = None

    for line in texto.split("\n"):
        stripped = line.strip()
        hm = re.match(r"^(#{3,6})\s+(.*)$", stripped)
        bm = re.match(r"^\*\*(.+?)\*\*\s*$", stripped)
        # a whole-line **bold** is a heading only when numbered ("**3. Title**");
        # an unnumbered one is a run-in paragraph title and keeps the context
        if bm and not hm and not heading_num(bm.group(1).strip()):
            bm = None
        if hm or bm:
            close()
            level = len(hm.group(1)) if hm else 3
            title = (hm.group(2) if hm else bm.group(1)).strip()
            for lvl in list(heading_nums):
                if lvl >= level:
                    del heading_nums[lvl]
            heading_nums[level] = heading_num(title)
            continue
        if not stripped:
            close()
            continue
        if re.match(r"^\s*[-*]\s+", line):
            close()
            item = re.sub(r"^\s*[-*]\s+", "", line)
            # a bullet names its own subsection only as a dotted number ("- 3.2 …");
            # "- 1. first point" is an enumeration inside the current section
            nm = re.match(r"^(\d+\.\d+(?:\.\d+)*)\.?\s", item)
            current = {"lines": [line.rstrip()],
                       "num": nm.group(1) if nm else ctx_num()}
            continue
        if current is None:
            current = {"lines": [line.rstrip()], "num": ctx_num()}
        else:
            current["lines"].append(line.rstrip())
    close()
    return units


def unit_matches(unit: dict, tok: tuple[str, str]) -> bool:
    kind, v = tok
    text = unit["text"]
    ev = re.escape(v)
    if kind == "sec":
        inline = re.search(
            rf"(?:§\s*|\bSecs?\.?\s*|\bSection\s+|\bSección\s+){ev}(?:\.\d+)*(?![\d])",
            text)
        num = unit.get("num")
        structural = bool(num) and (num == v or num.startswith(v + "."))
        # a subsection packed mid-bullet ("- 3.1 SC = ... 3.2 Across splits ...");
        # dotted numbers only, at an item boundary, before a capital or bracket
        packed = "." in v and bool(re.search(
            rf"(?:^|[.;:]\s+|\(\s*|[-*]\s+){ev}(?:\.\d+)*\s+(?=[A-ZÁÉÍÓÚÑ(\[⟂\"'])", text))
        return bool(inline) or structural or packed
    if kind == "app":
        num = unit.get("num") or ""
        structural = bool(re.match(r"[A-Z]", num)) and (num == v or num.startswith(v + "."))
        return structural or bool(re.search(
            rf"\b(?:App(?:endix)?\.?|Apéndice)\s*{ev}(?:\.\d+)*(?![\d])", text))
    if kind == "fig":
        return bool(re.search(rf"\bFig(?:ura|ure)?s?\.?\s*{ev}(?![\d])", text))
    if kind == "table":
        return bool(re.search(rf"\b(?:Tabla|Table)s?\.?\s*{ev}(?![\d])", text))
    if kind == "eq":
        return bool(re.search(rf"\b(?:Eq|Ec)\.?\s*{ev}(?![\d])", text))
    return False


def find_paper(vault: str, pid: str) -> str | None:
    hits = sorted(glob.glob(os.path.join(vault, "Papers", f"{pid}*.md")))
    return hits[0] if hits else None


def resolve_citation(vault: str, pid: str, span: str) -> dict:
    res = {"paper": pid, "locator": span, "tokens": [], "source": None,
           "title": None, "units": [], "note": None, "provenance": []}
    toks = locator_tokens(span)
    res["tokens"] = [token_label(t) for t in toks]
    path = find_paper(vault, pid)
    if not path:
        res["note"] = f"nota {pid} no encontrada en Papers/"
        return res
    if is_flagged(Path(path)):
        res["note"] = (f"{pid} está marcada send: never — su texto no se envía; "
                       "esta cita no se puede comprobar aquí")
        return res
    res["source"] = os.path.relpath(path, vault).replace("\\", "/")
    fm, body = split_frontmatter(read_text(path))
    res["title"] = fm_scalar(fm, "title")
    secs = dict(body_sections(body))
    texto = secs.get("Texto completo", "")
    if not toks:
        res["note"] = "localizador sin sección / tabla / figura reconocible"
        return res
    chosen: list[str] = []
    for tok in toks:
        if tok[0] == "named":
            name = tok[1].lower()
            if name in ("resumen", "abstract"):
                txt = secs.get("Resumen", "").strip()
                if txt:
                    chosen.append("[## Resumen]\n" + txt)
                continue
            for u in source_units(texto):
                if name in u["text"].lower()[:80]:
                    chosen.append(u["text"])
            continue
        for u in source_units(texto):
            if unit_matches(u, tok) and u["text"] not in chosen:
                chosen.append(u["text"])
    res["units"] = chosen
    if not chosen:
        res["note"] = ("ningún fragmento de ## Texto completo coincide con "
                       "este localizador")
    # provenance: the text sent must be the paper's own
    problems = [f"marcador «{m}»" for m in provenance_problems("\n".join(chosen))]
    if (fm_scalar(fm, "fulltext") or "").strip() == "abstract-only" and any(
            not u.startswith("[## Resumen]") for u in chosen):
        problems.append("la nota es fulltext: abstract-only, así que su ## Texto "
                        "completo no puede ser texto del paper")
    res["provenance"] = problems
    return res


# --------------------------------------------------------------------------
# Packet assembly
# --------------------------------------------------------------------------

def quote(text: str) -> str:
    return "\n".join("> " + ln if ln else ">" for ln in text.split("\n"))


def note_label(fm: list[str], path: str) -> str:
    return fm_scalar(fm, "id") or os.path.basename(path)


def build(vault: str, note: str, experiments: list[str],
          analysis_outputs: list[str], section: str | None) -> tuple[str, dict]:
    manifest: dict = {"tool": TOOL_ID, "scope": f"section:{section}" if section else "note",
                      "sources": [], "citations": [], "analysis_outputs": []}
    out: list[str] = []
    for f in [note, *experiments]:
        if is_flagged(Path(f)):
            raise ValueError(f"{os.path.basename(f)} is marked send: never; it is never "
                             "sent to the verifier (record no verification for it)")
    fm, body = split_frontmatter(read_text(note))
    label = note_label(fm, note)
    scope = manifest["scope"]
    out.append(f"# Paquete de verificación ({TOOL_ID})")
    out.append("")
    out.append(f"- Nota verificada: {label}")
    out.append(f"- Alcance: {scope}")
    out.append("- Contenido: solo el artefacto (claim, afirmaciones de cita con el "
               "texto fuente que su localizador señala, preregistro congelado y "
               "resultado de los experimentos, salidas de análisis).")
    out.append("")

    # ---- the verified note
    sections = body_sections(body)
    headings = [h for h, _ in sections]
    if section is not None and section not in headings:
        raise ValueError(f"section '## {section}' not found in {note}")
    if section is not None and never_sent(section):
        raise ValueError(f"section '## {section}' carries reasoning / critiques / prior "
                         "verdicts and is never sent to the verifier")
    inc, exc = [], []
    fm_inc, fm_exc = [], []
    for k, lines in frontmatter_blocks(fm):
        (fm_inc if k in HYP_FM_KEYS else fm_exc).append(k)
    out.append(f"## Nota: {label}")
    out.append("")
    for h, text in sections:
        shown = h or "(preámbulo)"
        allowed = (h == section) if section is not None else hyp_section_allowed(h)
        if not allowed:
            exc.append(shown)
            continue
        inc.append(shown)
        if h.startswith("Justificación"):
            out.append(f"### {h}")
            out.append("")
            out.extend(render_justification(vault, text, manifest))
        else:
            out.append(f"### {h}")
            out.append("")
            out.append(text.strip())
            out.append("")
    manifest["sources"].append({
        "path": _display(note, vault), "role": "note",
        "included_sections": inc, "excluded_sections": exc,
        "included_frontmatter": fm_inc, "excluded_frontmatter": fm_exc})

    # ---- experiments
    for ep in experiments:
        efm, ebody = split_frontmatter(read_text(ep))
        elabel = note_label(efm, ep)
        out.append(f"## Experimento: {elabel}")
        out.append("")
        fm_inc, fm_exc = [], []
        kept = []
        for k, lines in frontmatter_blocks(efm):
            if k in EXP_FM_KEYS:
                fm_inc.append(k)
                kept.extend(lines)
            else:
                fm_exc.append(k)
        if kept:
            out.append("### Frontmatter (subconjunto)")
            out.append("")
            out.append("```yaml")
            out.extend(kept)
            out.append("```")
            out.append("")
        inc, exc = [], []
        for h, text in body_sections(ebody):
            shown = h or "(preámbulo)"
            if h not in EXP_SECTIONS:
                exc.append(shown)
                continue
            inc.append(shown)
            if h == "Enmiendas":
                out.append("### Enmiendas (registro posterior al freeze; forma "
                           "parte del registro congelado bajo el que corrió el "
                           "análisis)")
            elif h == "Resultado":
                out.append("### Resultado (escrito por run-experiment tras la "
                           "corrida)")
            else:
                out.append(f"### {h} (preregistro congelado)")
            out.append("")
            out.append(text.strip() or "(sección vacía)")
            out.append("")
        manifest["sources"].append({
            "path": _display(ep, vault), "role": "experiment",
            "included_sections": inc, "excluded_sections": exc,
            "included_frontmatter": fm_inc, "excluded_frontmatter": fm_exc})

    # ---- analysis outputs
    for ap in analysis_outputs:
        name = os.path.basename(ap)
        out.append(f"## Salida de análisis: {name}")
        out.append("")
        out.append("```")
        out.append(read_text(ap).rstrip("\n"))
        out.append("```")
        out.append("")
        manifest["analysis_outputs"].append(_display(ap, vault))

    packet = "\n".join(out).rstrip("\n") + "\n"
    manifest["sha256"] = hashlib.sha256(packet.encode("utf-8")).hexdigest()
    manifest["bytes"] = len(packet.encode("utf-8"))
    return packet, manifest


def render_justification(vault: str, text: str, manifest: dict) -> list[str]:
    out: list[str] = []
    bullets = justification_bullets(text)
    if not bullets:
        out.append(text.strip())
        out.append("")
        return out
    for i, b in enumerate(bullets, 1):
        out.append(f"#### Afirmación {i} (verbatim de la nota)")
        out.append("")
        out.append(quote(b.strip()))
        out.append("")
        refs = citation_refs(b)
        if not refs:
            out.append("(sin cita a Papers/ — no hay fuente contra la que "
                       "contrastar esta afirmación en el paquete)")
            out.append("")
            continue
        for pid, span in refs:
            r = resolve_citation(vault, pid, span)
            manifest["citations"].append({
                "assertion": i, "paper": pid, "locator": span,
                "tokens": r["tokens"], "source": r["source"],
                "matched_units": len(r["units"]), "note": r["note"],
                "provenance": r["provenance"]})
            title = f" — {r['title']}" if r["title"] else ""
            out.append(f"Fuente citada: {pid}{title}; localizador "
                       f"\"{span}\" → {', '.join(r['tokens']) or '(ninguno)'}")
            out.append("")
            if r["units"]:
                out.append(f"Texto de {pid} (## Texto completo) que coincide con "
                           "ese localizador, verbatim:")
                out.append("")
                for u in r["units"]:
                    out.append(quote(u))
                    out.append("")
            if r["note"]:
                out.append(f"({r['note']})")
                out.append("")
            if r["provenance"]:
                out.append(f"**ATENCIÓN — procedencia:** el texto de {pid} mostrado "
                           "arriba no parece ser texto del paper ("
                           + "; ".join(r["provenance"]) + "). Una afirmación "
                           "apoyada en él es `crítico`.")
                out.append("")
    # Paper-level context: an assertion may also characterise the paper as a
    # whole ("its own experiments are on X"), which no locator snippet can
    # confirm or refute. The abstract is source text, not the author's
    # reasoning, so it is included once per cited paper -- clearly labelled as
    # NOT the text of any locator.
    cited = []
    for c in manifest["citations"]:
        if c["source"] and c["paper"] not in cited:
            cited.append(c["paper"])
    abstracts = []
    for pid in cited:
        path = find_paper(vault, pid)
        _, body = split_frontmatter(read_text(path))
        resumen = dict(body_sections(body)).get("Resumen", "").strip()
        if resumen:
            abstracts.append((pid, resumen))
    if abstracts:
        out.append("#### Resúmenes de los papers citados (contexto de nivel "
                   "paper — NO es el texto de ningún localizador)")
        out.append("")
        for pid, resumen in abstracts:
            out.append(f"{pid} — ## Resumen, verbatim:")
            out.append("")
            out.append(quote(resumen))
            out.append("")
            marks = provenance_problems(resumen)
            if marks:
                out.append(f"**ATENCIÓN — procedencia:** el ## Resumen de {pid} no "
                           "parece ser el abstract del paper ("
                           + "; ".join(f"marcador «{m}»" for m in marks) + "). "
                           "No es fuente válida para ninguna afirmación.")
                out.append("")
                manifest.setdefault("abstract_provenance", {})[pid] = marks
        manifest["paper_abstracts"] = [pid for pid, _ in abstracts]
    return out


def _display(path: str, vault: str) -> str:
    ap, av = os.path.abspath(path), os.path.abspath(vault)
    try:
        if os.path.commonpath([ap, av]) == av:
            return os.path.relpath(ap, av).replace("\\", "/")
    except ValueError:
        pass
    return os.path.basename(path)


def manifest_text(m: dict) -> str:
    lines = [f"packet sha256: {m['sha256']}  ({m['bytes']} bytes, scope {m['scope']})"]
    for s in m["sources"]:
        lines.append(f"[{s['role']}] {s['path']}")
        lines.append(f"  included sections: {s['included_sections']}")
        lines.append(f"  excluded sections: {s['excluded_sections']}")
        lines.append(f"  included frontmatter: {s['included_frontmatter']}")
        lines.append(f"  excluded frontmatter: {s['excluded_frontmatter']}")
    for c in m["citations"]:
        lines.append(f"  citation #{c['assertion']}: {c['paper']} \"{c['locator']}\" "
                     f"-> {c['tokens']} matched {c['matched_units']} unit(s)"
                     + (f" [{c['note']}]" if c["note"] else ""))
    if m.get("paper_abstracts"):
        lines.append(f"  paper abstracts (## Resumen, labelled as non-locator "
                     f"context): {m['paper_abstracts']}")
    for a in m["analysis_outputs"]:
        lines.append(f"[analysis-output] {a}")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Build the fresh-verifier packet "
                                "(allow-list isolation).")
    p.add_argument("--version", action="version", version=TOOL_ID)
    p.add_argument("--vault", required=True, help="vault root (for Papers/)")
    p.add_argument("--note", required=True,
                   help="hypothesis note, or a draft note file outside the vault")
    p.add_argument("--experiment", action="append", default=[],
                   help="adjudicating experiment note (repeatable)")
    p.add_argument("--analysis-output", action="append", default=[],
                   dest="analysis_output",
                   help="analysis output file included verbatim (repeatable)")
    p.add_argument("--section", help="scope section:<heading> -- include only "
                   "this ## section of --note")
    p.add_argument("--out", help="write the packet here (UTF-8, LF)")
    p.add_argument("--manifest", help="write the manifest JSON here")
    p.add_argument("--json", action="store_true",
                   help="print {out, sha256, manifest} as JSON on stdout")
    a = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    for f in [a.note, *a.experiment, *a.analysis_output]:
        if not os.path.isfile(f):
            print(f"error: file not found: {f}", file=sys.stderr)
            return 2
    if not os.path.isdir(a.vault):
        print(f"error: vault not found: {a.vault}", file=sys.stderr)
        return 2
    try:
        packet, manifest = build(a.vault, a.note, a.experiment,
                                 a.analysis_output, a.section)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(packet)
    if a.manifest:
        with open(a.manifest, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    print(manifest_text(manifest), file=sys.stderr)
    if a.json:
        print(json.dumps({"out": a.out, "sha256": manifest["sha256"],
                          "manifest": manifest}, ensure_ascii=False))
    elif a.out:
        print(manifest["sha256"])
    else:
        sys.stdout.write(packet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
