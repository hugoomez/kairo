#!/usr/bin/env python3
"""The paper a `teorico` project is organised around, from day one.

    manuscript.py init  --project-dir D --thread T --title "..." [--venue V] [--design D] [--lang es|en]
    manuscript.py bind  --project-dir D --thread T --section S --id C-0003|H-0002
    manuscript.py show  --project-dir D --thread T            (JSON)
    manuscript.py export --vault V --project-dir D --thread T --format latex
        writes Manuscritos/latex-<T>/main.tex (venue's class, from config/venues.toml;
        an unknown venue is `generic`, with a warning) and refs.bib (export_bib.py);
        needs pandoc, run as `-f markdown-raw_tex-raw_attribute` (raw TeX in a section
        comes out escaped, but math is written verbatim). The guard is an allow-list on
        pandoc's output: every control sequence must be one pandoc emits for text or a
        listed math macro, else exit 3 naming the section and the command; also refused:
        a bare `$`, a nested or stray math delimiter, `^^`, an unlisted environment and
        any verbatim block. P-ids cross pandoc as KAIROCITE<nonce>N<n> tokens and become
        \\cite{key} after the check. With latexmk (run with -no-shell-escape, without
        TEXINPUTS/TEXMF*/BIBINPUTS/BSTINPUTS, with openin_any=openout_any=p, which only
        TeX Live honours; MiKTeX ignores them) it reports the body pages against the
        venue's limit

``init`` writes two files in ``<project>/Manuscritos/``:

* ``outline-<thread>.md`` — the section plan. Its frontmatter lists every
  section with an ``id``, a ``title``, a ``kind`` and the ``depends_on`` ids
  (claims ``C-XXXX``, hypotheses ``H-XXXX``) that must pass their gate before
  the section can hold results. Kinds:
    - ``prosa``     — drafted early (introduction, related work, problem
                      setup / definitions), from Estado-del-arte and the hub;
    - ``resultado`` — theorems or empirical results: drafted only from
                      claims / hypotheses that passed their gate;
    - ``cierre``    — discussion / conclusion: drafted once a result exists.
* ``manuscript-<thread>.md`` — the skeleton: one heading per section, each
  holding a ``<!-- kairo:section <id> -->`` marker and a placeholder. The
  body between a marker and the next heading is what assemble-manuscript's
  progressive mode fills; nothing else in the file is touched by it.

No prose is written here. ``bind`` adds an id to a section's ``depends_on``.
The frontmatter is written and read by this script in one fixed shape — edit
it keeping that shape (or use ``bind``).

``coverage`` gives every section one of three states:

* ``respaldada`` — at least one claim / hypothesis is bound, every bound one
  passed its gate, and the section is ready by its kind. A section with nothing
  bound is never respaldada: it is ``pendiente`` (a prosa section can still be
  drafted early — ``ready`` — but nothing supports it yet);
* ``bloqueada``  — a bound node failed (hypothesis ``refutada`` /
  ``descartada``, claim ``fallido`` / ``refutado``) or depends, directly or
  transitively, on one that failed. The propagation is build_graph.py's
  (never re-derived here); the reason names the failed node and the chain.
  A blocked section is never ``ready``, so ``write-section`` refuses it;
* ``pendiente``  — anything else.

Labelled designs. A study design with ``etiqueta`` in config/designs.toml (today
``exploratoria``, QE-XXXX, label ``exploratorio``) is citable only with its label,
checked mechanically: ``bind`` refuses it unless the section's title contains one of
the label's words (``exploratori`` or ``exploratory``, case-insensitive — e.g.
«Resultados exploratorios»); ``write-section`` refuses a body of a section bound to
it unless the body contains one of those words too; ``coverage`` writes the label
next to the dependency («QE-0004 ✓ «exploratorio»»). A question whose plan was
amended shows «desviación E1 (datos_vistos: si)» the same way.

Exit codes: 0 ok · 3 refused · 1 error. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

__version__ = "1.2.0"
KINDS = ("prosa", "resultado", "cierre")
_ID = re.compile(r"^(C|H|Q|QE)-\d{4}$")
# The words that state a design's label in a section title and body (lower-case substrings).
LABEL_WORDS = {"exploratorio": ("exploratori", "exploratory")}
LANGS = ("es", "en")
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
_THREAD = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")

DEFAULT_SECTIONS = [
    {"id": "introduccion", "title": "Introducción", "kind": "prosa"},
    {"id": "trabajo-relacionado", "title": "Trabajo relacionado", "kind": "prosa"},
    {"id": "planteamiento", "title": "Planteamiento y definiciones", "kind": "prosa"},
    {"id": "resultados", "title": "Resultados principales", "kind": "resultado"},
    {"id": "discusion", "title": "Discusión", "kind": "cierre"},
]

# Titles of the sections a design names only by id, per manuscript language.
TITLES = {
    "introduction": ("Introducción", "Introduction"),
    "background": ("Antecedentes", "Background"),
    "methodology": ("Metodología", "Methodology"),
    "ethics": ("Ética", "Ethics"),
    "results": ("Resultados", "Results"),
    "exploratory-results": ("Resultados exploratorios", "Exploratory results"),
    "discussion": ("Discusión", "Discussion"),
    "related-work": ("Trabajo relacionado", "Related work"),
    "open-science": ("Ciencia abierta", "Open science"),
}

PLACEHOLDER = {
    "prosa": "_(pendiente — prosa: se redacta pronto, desde el Estado-del-arte y el propósito, con citas literales)_",
    "resultado": "_(pendiente — resultados: solo se redacta con claims / hipótesis que pasaron su puerta; ver cobertura)_",
    "cierre": "_(pendiente — se redacta cuando haya al menos un resultado redactado)_",
}


class Refused(Exception):
    pass


def paths(project_dir: Path, thread: str) -> tuple[Path, Path]:
    if not _THREAD.match(thread):
        raise Refused("thread must be a lowercase slug (letters, digits, dashes)")
    m = project_dir / "Manuscritos"
    return m / f"outline-{thread}.md", m / f"manuscript-{thread}.md"


def project_id(project_dir: Path) -> str:
    hub = project_dir / "_hub.md"
    if not hub.is_file():
        raise Refused(f"no _hub.md in {project_dir}")
    m = re.search(r"^id:\s*(\S+)", hub.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else "?"


def _scalar(value: str) -> str:
    return value if re.fullmatch(r"[a-z0-9][a-z0-9-]*", value) else json.dumps(value, ensure_ascii=False)


def render_outline(meta: dict, sections: list[dict], body: str) -> str:
    lines = ["---", f"paper_thread: {meta['paper_thread']}", f"project: {meta['project']}",
             f"title: {json.dumps(meta['title'], ensure_ascii=False)}",
             f"venue: {_scalar(meta.get('venue') or 'por decidir')}"]
    if meta.get("design"):
        lines.append(f"design: {meta['design']}")
    lines += [f"lang: {meta.get('lang') or 'es'}", f"created: {meta['created']}", "sections:"]
    for s in sections:
        lines += [f"  - id: {s['id']}", f"    title: {json.dumps(s['title'], ensure_ascii=False)}",
                  f"    kind: {s['kind']}", f"    depends_on: [{', '.join(s.get('depends_on', []))}]"]
    lines += ["---", ""]
    return "\n".join(lines) + body


def parse_outline(path: Path) -> tuple[dict, list[dict], str]:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        raise Refused(f"{path.name}: no frontmatter")
    meta: dict = {}
    sections: list[dict] = []
    in_sections = False
    for line in m.group(1).split("\n"):
        if line.startswith("sections:"):
            in_sections = True
            continue
        if in_sections and line.startswith("  - id:"):
            sections.append({"id": line.split(":", 1)[1].strip(), "depends_on": []})
        elif in_sections and line.startswith("    ") and sections:
            k, _, v = line.strip().partition(":")
            v = v.strip()
            if k == "depends_on":
                sections[-1][k] = [x.strip() for x in v.strip("[]").split(",") if x.strip()]
            elif k == "title":
                sections[-1][k] = json.loads(v) if v.startswith('"') else v
            else:
                sections[-1][k] = v
        elif not line.startswith(" ") and ":" in line:
            in_sections = False
            k, _, v = line.partition(":")
            v = v.strip()
            meta[k] = json.loads(v) if v.startswith('"') else v
    for s in sections:
        if s.get("kind") not in KINDS:
            raise Refused(f"section {s['id']}: kind must be one of {KINDS}")
        for d in s["depends_on"]:
            if not _ID.match(d):
                raise Refused(f"section {s['id']}: depends_on {d!r} is not a C-XXXX / H-XXXX / Q-XXXX / QE-XXXX id")
    return meta, sections, m.group(2)


def _venues(plugin_root: Path = PLUGIN_ROOT) -> dict:
    import tomllib  # noqa: PLC0415
    with open(plugin_root / "config" / "venues.toml", "rb") as fh:
        return tomllib.load(fh)


def design_sections(design: str, lang: str, plugin_root: Path = PLUGIN_ROOT) -> list[dict]:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ledger"))
    import designs as _designs  # noqa: PLC0415
    reg = _designs.load(plugin_root)
    if design not in reg:
        raise Refused(f"unknown design {design!r}; known: {', '.join(sorted(reg))}")
    if not reg[design].get("citable", True):
        raise Refused(f"design {design!r} is exploratory (citable = false): it has no manuscript")
    out = []
    for s in reg[design]["manuscript_sections"]:
        es_en = TITLES.get(s["id"])
        title = s.get("title") or (es_en[LANGS.index(lang)] if es_en else s["id"].replace("-", " ").capitalize())
        out.append({"id": s["id"], "title": title, "kind": s["kind"], "depends_on": []})
    return out


def init(project_dir: Path, thread: str, title: str, venue: str | None,
         design: str | None = None, lang: str = "es") -> tuple[Path, Path]:
    outline, manuscript = paths(project_dir, thread)
    if lang not in LANGS:
        raise Refused(f"lang must be one of {LANGS}")
    if venue is not None and venue not in _venues():
        raise Refused(f"unknown venue {venue!r}; known: {', '.join(sorted(_venues()))}")
    if outline.exists() or manuscript.exists():
        raise Refused(f"the manuscript for thread {thread} already exists — never overwritten")
    meta = {"paper_thread": thread, "project": project_id(project_dir), "title": title.strip(),
            "venue": venue, "design": design, "lang": lang, "created": date.today().isoformat()}
    sections = (design_sections(design, lang) if design
                else [dict(s, depends_on=[]) for s in DEFAULT_SECTIONS])
    body = (
        f"# Plan del paper — {meta['title']}\n\n"
        "## Contribución\n\n"
        "<Una o dos frases: qué aporta el paper. Escríbela tú; se refina a medida que el proyecto avanza.>\n\n"
        "## Cómo se usa\n\n"
        "- Cada sección de arriba lista en `depends_on` los claims (`C-XXXX`) e hipótesis (`H-XXXX`)\n"
        "  en que se apoya: `manuscript.py bind` los añade.\n"
        "- `prosa` se redacta pronto; `resultado` solo con lo que pasó su puerta (teoremas: verificador,\n"
        "  tu visto bueno al enunciado y a la demostración, y comprobación numérica; hipótesis:\n"
        "  `apoyada` + `completo`); `cierre` cuando ya hay un resultado.\n"
        "- `Manuscritos/coverage-<thread>.md` muestra qué sección está respaldada y qué falta.\n"
    )
    outline.parent.mkdir(parents=True, exist_ok=True)
    outline.write_text(render_outline(meta, sections, body), encoding="utf-8", newline="\n")
    ms = ["---", f"paper_thread: {thread}", f"project: {meta['project']}",
          f"title: {json.dumps(meta['title'], ensure_ascii=False)}", "status: esqueleto",
          f"generated_by: kairo/manuscript@{__version__}", "---", "", f"# {meta['title']}", ""]
    for s in sections:
        ms += [f"## {s['title']}", "", f"<!-- kairo:section {s['id']} -->", PLACEHOLDER[s["kind"]], ""]
    manuscript.write_text("\n".join(ms), encoding="utf-8", newline="\n")
    return outline, manuscript


def _study_design(note_id: str) -> dict | None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ledger"))
    import designs as _designs  # noqa: PLC0415
    return _designs.design_for_id(_designs.load(), note_id)


def label_words(etiqueta: str) -> tuple[str, ...]:
    return LABEL_WORDS.get(etiqueta, (etiqueta.lower(),))


def states_label(text: str, etiqueta: str) -> bool:
    """Whether `text` states the label (one of its words, case-insensitive)."""
    low = (text or "").lower()
    return any(w in low for w in label_words(etiqueta))


def bind(project_dir: Path, thread: str, section: str, note_id: str) -> list[str]:
    if not _ID.match(note_id):
        raise Refused("id must be C-XXXX, H-XXXX, Q-XXXX or QE-XXXX")
    outline, _ = paths(project_dir, thread)
    meta, sections, body = parse_outline(outline)
    target = next((s for s in sections if s["id"] == section), None)
    if target is None:
        raise Refused(f"no section {section} in the outline")
    design = _study_design(note_id) if note_id.startswith("Q") else None
    folder = design["folder"] if design else {"C": "Claims", "H": "Hipotesis", "Q": "Preguntas"}[note_id[0]]
    if not any((project_dir / folder).glob(f"{note_id}*.md")):
        raise Refused(f"{note_id} not found in {folder}/")
    etiqueta = (design or {}).get("etiqueta")
    if etiqueta and not states_label(target.get("title", ""), etiqueta):
        raise Refused(f"{note_id} is {etiqueta}: bind it only to a section whose title says so "
                      f"(one of: {', '.join(label_words(etiqueta))}), e.g. «Resultados exploratorios»; "
                      f"section {section} is titled {target.get('title')!r}")
    if note_id not in target["depends_on"]:
        target["depends_on"].append(note_id)
    outline.write_text(render_outline(meta, sections, body), encoding="utf-8", newline="\n")
    return target["depends_on"]


# --------------------------------------------------------------------------
# Coverage: which section is backed, and what is missing
# --------------------------------------------------------------------------

def _ledger():
    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here.parent / "ledger"))
    import claim_gate  # noqa: PLC0415
    import verifications  # noqa: PLC0415
    from notes import parse_frontmatter, split_note  # noqa: PLC0415
    from verifier_packet import find_note  # noqa: PLC0415
    return claim_gate, verifications, parse_frontmatter, split_note, find_note


def _question_state(vault: Path, dep: str) -> dict:
    """A study question: its design's gate plus the design's publishable status."""
    _ledger()
    import designs as _designs  # noqa: PLC0415
    import study_status  # noqa: PLC0415
    from study_gates import find_node  # noqa: PLC0415
    from notes import read_note  # noqa: PLC0415
    design = _designs.design_for_id(_designs.load(), dep)
    path = find_node(Path(vault), dep, design) if design else None
    if path is None:
        return {"id": dep, "ok": False, "status": None, "missing": ["no encontrada en el vault"]}
    parsed = read_note(path)
    fm = parsed[0] if parsed else {}
    status = str(fm.get("status", ""))
    g = study_status.gate(Path(vault), dep)
    missing = list(g["missing"])
    if status != design["publishable"]:
        missing.append(f"estado «{status}», no {design['publishable']}")
    out = {"id": dep, "ok": g["ok"] and status == design["publishable"], "status": status, "missing": missing}
    labels = [g["etiqueta"]] if g.get("etiqueta") else []
    labels += [f"desviación {a['id']} (datos_vistos: {a['datos_vistos']})" for a in g.get("enmiendas") or []]
    if g.get("etiqueta"):
        out["etiqueta"] = g["etiqueta"]
    if labels:
        out["labels"] = labels
    return out


def dep_state(vault: Path, dep: str) -> dict:
    """Whether one bound claim / hypothesis passed its gate."""
    _ledger()
    import designs as _designs  # noqa: PLC0415
    design = _designs.design_for_id(_designs.load(), dep)
    if design is not None and not design.get("citable", True):
        return {"id": dep, "ok": False, "status": None,
                "missing": [f"{dep} es exploratorio (diseño no citable): no sostiene una cifra del "
                            "manuscrito; apóyala en la pregunta del estudio y cuenta el piloto en la metodología"]}
    if design is not None:
        return _question_state(vault, dep)
    claim_gate, vf, parse_fm, split_note, find_note = _ledger()
    path = find_note(str(vault), dep)
    if path is None:
        return {"id": dep, "ok": False, "status": None, "missing": ["no encontrada en el vault"]}
    fm = parse_fm(split_note(Path(path).read_text(encoding="utf-8-sig"))[0])
    status = str(fm.get("status", ""))
    missing: list[str] = []
    if str(fm.get("send", "")) == "never":
        return {"id": dep, "ok": False, "status": status, "missing": ["send: never — se redacta a mano"]}
    if dep.startswith("C-"):
        g = claim_gate.gate(str(vault), Path(path))
        if g["applies"]:
            missing += g["missing"]
        if status != "probado":
            missing.append(f"estado «{status}», no probado")
    else:
        if str(fm.get("linea_publicacion", "")).lower() != "true":
            missing.append("linea_publicacion no es true")
        if status != "apoyada":
            missing.append(f"estado «{status}», no apoyada")
        linked = fm.get("linked_experiment") or []
        linked = linked if isinstance(linked, list) else [linked]
        for e in [str(x) for x in linked if str(x).strip()]:
            ep = next(Path(vault, "Projects").glob(f"*/Experimentos/{e}.md"), None)
            efm = parse_fm(split_note(ep.read_text(encoding="utf-8-sig"))[0]) if ep else {}
            if str(efm.get("send", "")) == "never":
                missing.append(f"{e}: send: never")
            if str(efm.get("tier", "")) != "completo":
                missing.append(f"{e}: tier «{efm.get('tier', '?')}», se exige completo")
            if str(efm.get("experiment_validity", "")) == "invalid":
                missing.append(f"{e}: inválido")
        last = vf.latest(vf.load(str(path))[5], "note")
        if not last or last.get("verdict") != "no_errors_found":
            missing.append(f"verificación: {last.get('verdict') if last else 'nunca verificada'}")
    return {"id": dep, "ok": not missing, "status": status, "missing": missing}


def section_bodies(manuscript: Path) -> dict[str, str]:
    """Text under each `<!-- kairo:section <id> -->` marker, up to the next `## `."""
    text = manuscript.read_text(encoding="utf-8").replace("\r\n", "\n")
    out: dict[str, str] = {}
    for m in re.finditer(r"<!-- kairo:section ([a-z0-9-]+) -->\n(.*?)(?=\n## |\Z)", text, re.S):
        out[m.group(1)] = m.group(2).strip()
    return out


def is_placeholder(body: str) -> bool:
    return not body.strip() or body.strip().startswith("_(pendiente")


# Hypotheses that were dropped also block what rests on them; build_graph's own
# FAILED set (refutada / fallido / refutado) does not include `descartada`, so
# its propagation runs once with it added. The set is the only thing changed.
BLOCKING = {("H", "refutada"), ("H", "descartada"), ("C", "fallido"), ("C", "refutado")}
_VIA = re.compile(r"depende de (\S+) \(`([^`]+)`\) vía (.+?) — ")


def failure_map(vault: Path) -> dict[str, dict]:
    """id -> {by, status, chain} for every graph node that failed or rests on a
    failed node, from build_graph.py's propagation (shortest chain, first
    failed source in id order). Nodes that are fine are absent."""
    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here.parent / "ledger"))
    import build_graph as bg  # noqa: PLC0415
    nodes, base = bg.load_nodes(vault)
    saved = bg.FAILED
    bg.FAILED = set(saved) | BLOCKING
    try:
        findings = bg.analyse(nodes, base)
    finally:
        bg.FAILED = saved
    out: dict[str, dict] = {}
    for n in sorted(nodes.values(), key=lambda x: x.id):
        if (n.kind, n.status) in BLOCKING:
            out[n.id] = {"by": n.id, "status": n.status, "chain": [n.id]}
    for f in findings:
        if f.kind != "depends_on_failed" or f.node in out:
            continue
        m = _VIA.search(f.detail)
        if m:
            out[f.node] = {"by": m.group(1), "status": m.group(2),
                           "chain": [x.strip() for x in m.group(3).split("←")]}
    return out


def blocked_reason(dep: str, info: dict) -> str:
    if info["chain"] == [dep]:
        return f"{dep} está «{info['status']}»"
    return f"{dep} depende de {info['by']} («{info['status']}») vía {' ← '.join(info['chain'])}"


def coverage(vault: Path, project_dir: Path, thread: str) -> dict:
    outline, manuscript = paths(project_dir, thread)
    meta, sections, _ = parse_outline(outline)
    bodies = section_bodies(manuscript) if manuscript.exists() else {}
    failed = failure_map(vault)
    out = []
    for s in sections:
        deps = [dep_state(vault, d) for d in s["depends_on"]]
        for d in deps:
            d["blocked"] = failed.get(d["id"])
        drafted = not is_placeholder(bodies.get(s["id"], ""))
        if s["kind"] == "prosa":
            ready, why = True, "prosa: se redacta pronto"
        elif s["kind"] == "resultado":
            ready = bool(deps) and all(d["ok"] for d in deps)
            why = ("sin claims ni hipótesis vinculados" if not deps
                   else "todas sus dependencias pasaron su puerta" if ready
                   else "hay dependencias que no pasan su puerta")
        else:
            ready = False
            why = "cierre: espera a que haya un resultado redactado"
        out.append({**s, "deps": deps, "drafted": drafted, "ready": ready, "why": why})
    has_result = any(x["kind"] == "resultado" and x["drafted"] and not any(d["blocked"] for d in x["deps"])
                     for x in out)
    for x in out:
        if x["kind"] == "cierre" and has_result:
            x["ready"], x["why"] = True, "cierre: ya hay un resultado redactado"
    for x in out:
        blockers = [d for d in x["deps"] if d["blocked"]]
        if blockers:
            x["state"], x["ready"] = "bloqueada", False
            x["why"] = "bloqueada: " + "; ".join(blocked_reason(d["id"], d["blocked"]) for d in blockers)
            x["blocked_by"] = [{"dep": d["id"], **d["blocked"]} for d in blockers]
        else:
            backed = bool(x["deps"]) and all(d["ok"] for d in x["deps"])
            x["state"] = "respaldada" if x["ready"] and backed else "pendiente"
            if not x["deps"]:
                x["why"] = f'{x["why"]} · sin respaldo vinculado'
            x["blocked_by"] = []
    return {"meta": meta, "sections": out}


def render_coverage(cov: dict) -> str:
    lines = ["---", f"paper_thread: {cov['meta'].get('paper_thread')}", f"generated_by: kairo/manuscript@{__version__}",
             f"generated: {date.today().isoformat()}", "---", "",
             f"# Cobertura del paper — {cov['meta'].get('title')}", "",
             "> Vista derivada: la regenera `manuscript.py coverage`. No editar a mano.", "",
             "| sección | tipo | estado | respaldo | ¿lista? | ¿redactada? |", "|---|---|---|---|---|---|"]
    for s in cov["sections"]:
        backing = "; ".join(f"{d['id']} {'✓' if d['ok'] else '✗'}"
                            + "".join(f" «{lb}»" for lb in d.get("labels") or []) for d in s["deps"]) or "—"
        lines.append(f"| {s['title']} | {s['kind']} | {s['state']} | {backing} | "
                     f"{'sí' if s['ready'] else 'no'} | {'sí' if s['drafted'] else 'no'} |")
    lines.append("")
    for s in cov["sections"]:
        if s["state"] == "bloqueada":
            lines += [f"## Bloqueada: «{s['title']}»", "", f"- {s['why']}", ""]
    for s in cov["sections"]:
        bad = [d for d in s["deps"] if not d["ok"]]
        if bad:
            lines += [f"## Falta en «{s['title']}»", ""]
            for d in bad:
                lines += [f"- **{d['id']}**: " + "; ".join(d["missing"])]
            lines.append("")
    return "\n".join(lines)


def write_section(vault: Path, project_dir: Path, thread: str, section: str, text: str) -> None:
    """Replace one section's body. Refused unless coverage says it is ready:
    the gate is enforced here, not only described in a skill."""
    cov = coverage(vault, project_dir, thread)
    s = next((x for x in cov["sections"] if x["id"] == section), None)
    if s is None:
        raise Refused(f"no section {section}")
    if not s["ready"]:
        raise Refused(f"section {section} is not ready: {s['why']}")
    if not text.strip():
        raise Refused("empty section text")
    for d in s["deps"]:
        et = d.get("etiqueta")
        if et and not states_label(text, et):
            raise Refused(f"section {section} rests on {d['id']}, which is {et}: the text must say so "
                          f"(one of: {', '.join(label_words(et))})")
    _, manuscript = paths(project_dir, thread)
    raw = manuscript.read_text(encoding="utf-8").replace("\r\n", "\n")
    marker = f"<!-- kairo:section {section} -->"
    m = re.search(re.escape(marker) + r"\n(.*?)(?=\n## |\Z)", raw, re.S)
    if not m:
        raise Refused(f"marker for {section} not found in the manuscript")
    new = raw[: m.start(1)] + text.strip() + "\n" + raw[m.end(1):]
    manuscript.write_text(new, encoding="utf-8", newline="\n")


# P-ids are cited in markdown, skipping fenced code, inline code, link targets and URLs
_CITE = re.compile(r"(?ms)^(?:```|~~~).*?^(?:```|~~~)[^\n]*$|`[^`\n]*`|\]\([^)]*\)|https?://\S+|\bP-\d{4}\b")
_AUX_LABEL = re.compile(r"\\newlabel\{kairo:refs-start\}\{\{[^}]*\}\{(\d+)\}")
_TEX_SPECIAL = {"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
                "~": r"\textasciitilde{}", "^": r"\textasciicircum{}", "\\": r"\textbackslash{}"}
PANDOC_HINT = "winget install --id JohnMacFarlane.Pandoc"
PANDOC_FROM = "markdown-raw_tex-raw_attribute"
# A citation crosses pandoc as an opaque token, KAIROCITE<nonce>N<4 digits> with a random
# per-export nonce (so literal text cannot collide with one), and becomes \cite{key} in
# its LaTeX output.
# Pandoc writes math verbatim even with raw TeX off, so its output is checked against an
# ALLOW-list before the citation tokens are replaced: every control sequence must be one
# pandoc's markdown writer emits for text, or one of a fixed set of math macros. Also
# refused: a bare `$`, a math opener inside math or a closer outside it, TeX's `^^`
# notation, a `\begin`/`\end` of an environment not listed, and any verbatim block.
_PANDOC_CS = frozenset("""
section subsection subsubsection paragraph subparagraph label hyperref href url footnote
emph textbf texttt textit textsc textsuperscript textsubscript st underline ldots
textless textgreater textbar textbackslash textasciitilde textasciicircum textquotesingle
textquotedbl item tightlist begin end caption tabularnewline toprule midrule bottomrule
noalign endhead endfirsthead endfoot endlastfoot raggedright raggedleft centering
arraybackslash linewidth columnwidth tabcolsep real rule strut protect
""".split())
_GREEK = ("alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu"
          " xi pi varpi rho varrho sigma varsigma tau upsilon phi varphi chi psi omega"
          " Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega")
_MATH_CS = frozenset((_GREEK + """
frac sqrt sum prod int lim log ln exp min max le ge leq geq neq approx sim times cdot pm mp
infty partial hat bar tilde mathrm mathbf mathit mathcal text left right quad qquad ldots
cdots in notin subset subseteq cup cap to mid
""").split())
# control symbols: the escapes, a line break, a control space and pandoc's `\^{}`
_SYMBOLS = frozenset([",", ";", "!", "{", "}", "_", "%", "&", "#", "$", "\\", " ", "\n", "^"])
_ENVS = frozenset("itemize enumerate description quote center longtable minipage"
                  " aligned cases matrix pmatrix bmatrix".split())
# exact constructs pandoc writes that use a macro the allow-list does not hold
_PANDOC_FIXED = re.compile(
    r"\\def\\labelenum(?:i{1,3}|iv)\{\(?\\(?:arabic|alph|Alph|roman|Roman)\{enum(?:i{1,3}|iv)\}[.)]?\}"
    r"|\\def\\LTcaptype\{none\} % do not increment counter"
    r"|\$\\(?:square|boxtimes)\$")
_CS_TOKEN = re.compile(r"\\(?:[A-Za-z@]+|.)|\$|\^\^", re.S)
_OPEN = {"\\(": "\\)", "\\[": "\\]"}


def _tex_not_allowed(tex: str) -> str | None:
    """The first thing in pandoc's LaTeX output the allow-list does not admit, or None."""
    text = _PANDOC_FIXED.sub("", tex)
    closer = None
    for m in _CS_TOKEN.finditer(text):
        tok = m.group(0)
        if tok in ("$", "^^"):
            return tok
        if tok in _OPEN:
            if closer:
                return tok
            closer = _OPEN[tok]
            continue
        if tok in ("\\)", "\\]"):
            if tok != closer:
                return tok
            closer = None
            continue
        name = tok[1:]
        if name in ("begin", "end"):
            env = re.match(r"\{([^{}]*)\}", text[m.end():])
            if not env or env.group(1) not in _ENVS:
                return tok + (env.group(0) if env else "")
            continue
        if name not in _PANDOC_CS and name not in _MATH_CS and name not in _SYMBOLS:
            return tok
    return "\\(" if closer else None


def _tex_escape(text: str) -> str:
    return "".join(_TEX_SPECIAL.get(c, c) for c in text)


def _bib_module():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "papers"))
    import export_bib  # noqa: PLC0415
    return export_bib


def _cite_keys(vault: Path) -> dict[str, str]:
    """P-id -> BibTeX key, from export_bib's own functions (never re-derived here)."""
    eb = _bib_module()
    every, _ = eb.collect(vault, None, None)
    return dict(zip((m["id"] for m in every), eb.keys_for(every)))


def _pandoc(exe: str, markdown: str) -> str:
    import subprocess  # noqa: PLC0415
    # raw TeX and raw attributes off: a section body can never inject a LaTeX command
    r = subprocess.run([exe, "-f", PANDOC_FROM, "-t", "latex"], input=markdown, capture_output=True,
                       text=True, encoding="utf-8")
    if r.returncode != 0:
        raise Refused(f"pandoc falló: {r.stderr.strip()[:300]}")
    return r.stdout


def _refs_page(aux: str) -> int | None:
    """Page of `\\label{kairo:refs-start}` from main.aux: where the references start."""
    m = _AUX_LABEL.search(aux)
    return int(m.group(1)) if m else None


def _last_error(log: str) -> str:
    errs = [ln.strip() for ln in log.splitlines() if ln.startswith("!")]
    return errs[-1] if errs else ""


def export_latex(vault: Path, project_dir: Path, thread: str) -> dict:
    import os  # noqa: PLC0415
    import shutil  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    pandoc = shutil.which("pandoc")
    if not pandoc:
        raise Refused(f"falta pandoc: instálalo con `{PANDOC_HINT}` y repite")
    cov = coverage(vault, project_dir, thread)
    meta = cov["meta"]
    _, manuscript = paths(project_dir, thread)
    if not manuscript.exists():
        raise Refused(f"no hay manuscrito para el hilo {thread}")
    bad = [s["id"] for s in cov["sections"] if s["kind"] == "resultado" and s["state"] != "respaldada"]
    if bad:
        raise Refused("secciones de resultados sin respaldo (deben estar respaldadas): " + ", ".join(bad))
    bodies = section_bodies(manuscript)
    pending = [s["id"] for s in cov["sections"] if is_placeholder(bodies.get(s["id"], ""))]
    if pending:
        raise Refused("secciones sin redactar: " + ", ".join(pending))

    venues = _venues()
    venue_name = meta.get("venue")
    warning = None
    if venue_name not in venues:
        venue_name, warning = "generic", "venue desconocido: se usa generic"
    venue = venues[venue_name]
    keys = _cite_keys(vault)
    cited: list[str] = []

    uncited: list[str] = []

    def to_cite(m: re.Match) -> str:
        pid = m.group(0)
        if not re.fullmatch(r"P-\d{4}", pid):
            return pid
        if pid not in keys:
            if pid not in uncited:
                uncited.append(pid)
            return pid
        if pid not in cited:
            cited.append(pid)
        return f"KAIROCITE{nonce}N{cited.index(pid) + 1:04d}"

    def from_token(m: re.Match) -> str:
        i = int(m.group(1)) - 1
        return "\\cite{" + keys[cited[i]] + "}" if 0 <= i < len(cited) else m.group(0)

    import secrets  # noqa: PLC0415
    nonce = secrets.token_hex(4)
    token = re.compile(rf"KAIROCITE{nonce}N(\d{{4}})")
    parts = []
    for s in cov["sections"]:
        tex = _pandoc(pandoc, _CITE.sub(to_cite, bodies[s["id"]]))
        bad = _tex_not_allowed(tex)
        if bad:
            raise Refused(f"la sección {s['id']} usa `{bad.strip()}`, que la exportación no admite "
                          "(solo texto, fórmulas con las órdenes permitidas y nada de bloques de código): "
                          "quítalo y repite")
        tex = token.sub(from_token, tex)
        parts.append(f"\\section{{{_tex_escape(s['title'])}}}\n\n{tex.strip()}\n")

    opts = f"[{venue['options']}]" if venue.get("options") else ""
    head = [f"\\documentclass{opts}{{{venue['class']}}}", "\\usepackage[utf8]{inputenc}"]
    if venue["class"] != "acmart":
        head.append(f"\\usepackage[{'english' if meta.get('lang') == 'en' else 'spanish'}]{{babel}}")
    if venue.get("package"):
        head.append(f"\\usepackage{{{venue['package']}}}")
    style = "ACM-Reference-Format" if venue["class"] == "acmart" else "plain"
    if venue["class"] == "acmart":
        head.append("\\author{Autores por decidir}")
    doc = "\n".join(head) + f"\n\\title{{{_tex_escape(meta.get('title') or '')}}}\n\\begin{{document}}\n\\maketitle\n\n"
    doc += "\n".join(parts)
    doc += "\n\\label{kairo:refs-start}\n"
    if cited:
        doc += f"\\bibliographystyle{{{style}}}\n\\bibliography{{refs}}\n"
    doc += "\\end{document}\n"

    out = project_dir / "Manuscritos" / f"latex-{thread}"
    out.mkdir(parents=True, exist_ok=True)
    tex_path, bib_path = out / "main.tex", out / "refs.bib"
    tex_path.write_text(doc, encoding="utf-8", newline="\n")
    if cited:
        import contextlib  # noqa: PLC0415
        import io  # noqa: PLC0415
        with contextlib.redirect_stdout(io.StringIO()):
            rc = _bib_module().main(["--vault", str(vault), "--only", *cited, "--out", str(bib_path)])
        if rc != 0:
            raise Refused("export_bib.py no pudo escribir refs.bib")
    else:
        bib_path.write_text("", encoding="utf-8", newline="\n")

    limit = int(venue.get("body_pages", 0))
    pages = over = None
    warnings = [warning] if warning else []
    latexmk = shutil.which("latexmk")
    if latexmk:
        for stale in ("main.log", "main.aux"):
            (out / stale).unlink(missing_ok=True)
        # no shell escape; no search-path overrides; paranoid file access under TeX Live
        # (MiKTeX ignores openin_any/openout_any: the allow-list above is the guard)
        env = {k: v for k, v in os.environ.items()
               if not k.upper().startswith("TEXMF") and k.upper() not in ("TEXINPUTS", "BIBINPUTS", "BSTINPUTS")}
        env.update(openin_any="p", openout_any="p")
        proc = subprocess.run([latexmk, "-pdf", "-no-shell-escape", "-interaction=nonstopmode", "main.tex"],
                              cwd=out, capture_output=True, text=True, env=env)
        log = out / "main.log"
        logtext = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        if proc.returncode != 0:
            last = _last_error(logtext)
            warnings.append("latex_failed: " + last if last else "latex_failed")
        else:
            aux = out / "main.aux"
            pages = _refs_page(aux.read_text(encoding="utf-8", errors="replace")) if aux.is_file() else None
            if pages is None:
                warnings.append("no se encontró kairo:refs-start en main.aux: páginas desconocidas")
            else:
                over = bool(limit and pages > limit)
    res = {"tex": str(tex_path), "bib": str(bib_path), "pages": pages, "limit": limit, "over": over,
           "uncited": uncited}
    warning = "; ".join(warnings) or None
    if warning:
        res["warning"] = warning
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("init", "bind", "show", "coverage", "write-section", "export"):
        p = sub.add_parser(name)
        p.add_argument("--project-dir", required=True, type=Path)
        p.add_argument("--thread", required=True)
        if name == "init":
            p.add_argument("--title", required=True)
            p.add_argument("--venue")
            p.add_argument("--design")
            p.add_argument("--lang", choices=LANGS, default="es")
        if name == "bind":
            p.add_argument("--section", required=True)
            p.add_argument("--id", required=True)
        if name in ("coverage", "write-section", "export"):
            p.add_argument("--vault", required=True, type=Path)
        if name == "coverage":
            p.add_argument("--no-write", action="store_true", help="print only; do not write coverage-<thread>.md")
        if name == "export":
            p.add_argument("--format", choices=("latex",), required=True)
        if name == "write-section":
            p.add_argument("--section", required=True)
            p.add_argument("--from", dest="source", required=True, type=Path, help="file with the section's text")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args.cmd == "init":
            o, m = init(args.project_dir, args.thread, args.title, args.venue, args.design, args.lang)
            print(json.dumps({"outline": str(o), "manuscript": str(m)}, ensure_ascii=False))
        elif args.cmd == "bind":
            print(json.dumps({"depends_on": bind(args.project_dir, args.thread, args.section, args.id)}))
        elif args.cmd == "coverage":
            cov = coverage(args.vault, args.project_dir, args.thread)
            if not args.no_write:
                out = args.project_dir / "Manuscritos" / f"coverage-{args.thread}.md"
                out.write_text(render_coverage(cov), encoding="utf-8", newline="\n")
            print(json.dumps(cov, ensure_ascii=False))
        elif args.cmd == "export":
            print(json.dumps(export_latex(args.vault, args.project_dir, args.thread), ensure_ascii=False))
        elif args.cmd == "write-section":
            write_section(args.vault, args.project_dir, args.thread, args.section, args.source.read_text(encoding="utf-8"))
            print(json.dumps({"written": args.section}))
        else:
            meta, sections, _ = parse_outline(paths(args.project_dir, args.thread)[0])
            print(json.dumps({"meta": meta, "sections": sections}, ensure_ascii=False))
        return 0
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
