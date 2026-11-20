#!/usr/bin/env python3
"""The paper a `teorico` project is organised around, from day one.

    manuscript.py init  --project-dir D --thread T --title "..." [--venue V] [--design D] [--lang es|en]
                        [--sections sections.json]
    manuscript.py sections --project-dir D --thread T --from sections.json
        replaces the section list of an existing outline (order, titles, kinds, placement,
        subsections, budgets); bodies already written are kept, and removing a written
        section is refused
    manuscript.py bind  --project-dir D --thread T --section S --id C-0003|H-0002
    manuscript.py show  --project-dir D --thread T            (JSON)
    manuscript.py export --vault V --project-dir D --thread T --format latex
                         [--anonymous] [--to-latex-source] [--force]
    manuscript.py pages --vault V --project-dir D --thread T [--no-build]
        body pages and pages per section (from the \\label of each section in main.aux)
    manuscript.py sync-check --vault V --project-dir D --thread T   (exit 0 / 4)
    manuscript.py check-venue --vault V --project-dir D --thread T [--venue V]  (exit 0 / 4)
        export writes Manuscritos/latex-<T>/main.tex (venue's class, from config/venues.toml;
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
        venue's limit. Markers `[[figura F-001]]`, `[[ref F-001]]`, `[[tabla ancha]]`
        and `[[tabla-latex <file>.tex]]` cross pandoc the same way (KAIROFIG / KAIROREF /
        KAIROWIDE / KAIROTAB tokens) and become LaTeX built by latex_parts.py from the
        figure registry (sha256-checked) and checked table files only; pandoc's
        longtables become booktabs tabulars. --anonymous: acmart review,anonymous, no
        author block elsewhere, acknowledgments left out. --to-latex-source writes
        kairo-source.json: later exports refuse without --force (which keeps a .bak).
        sync-check and check-venue live in submission.py

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
it keeping that shape (or use ``bind`` / ``sections``).

A section may also carry ``placement`` (``body`` by default; ``front`` = the
abstract, at most one, kind prosa; ``appendix`` = after the bibliography under
``\\appendix``, outside the body page count), ``budget_pages`` (a page budget
``pages`` checks) and ``parent`` (a level-2 subsection of an earlier top-level
section; its heading in the manuscript is ``###``). ``--sections`` /
``sections --from`` read a JSON list (or ``{"keywords": [...], "sections":
[...]}``) of ``{id, title?, kind, placement?, budget_pages?, depends_on?,
subsections?: [...]}``.

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

Exit codes: 0 ok · 3 refused · 4 problems found (sync-check, check-venue) · 1 error.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

__version__ = "1.3.0"
KINDS = ("prosa", "resultado", "cierre")
PLACEMENTS = ("body", "front", "appendix")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
# section ids with a fixed meaning for the export and check-venue
ACK_IDS = ("acknowledgments", "acknowledgements", "agradecimientos")
AI_IDS = ("ai-disclosure", "declaracion-ia", "ai-use")
_ID = re.compile(r"^(C|H|Q)-\d{4}$")
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
    "discussion": ("Discusión", "Discussion"),
    "related-work": ("Trabajo relacionado", "Related work"),
    "open-science": ("Ciencia abierta", "Open science"),
    "abstract": ("Resumen", "Abstract"),
    "acknowledgments": ("Agradecimientos", "Acknowledgments"),
    "agradecimientos": ("Agradecimientos", "Acknowledgments"),
    "ai-disclosure": ("Declaración de uso de IA", "AI use disclosure"),
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
    lines += [f"lang: {meta.get('lang') or 'es'}", f"created: {meta['created']}"]
    if meta.get("keywords"):
        lines.append(f"keywords: {json.dumps(list(meta['keywords']), ensure_ascii=False)}")
    lines.append("sections:")
    for s in sections:
        lines += [f"  - id: {s['id']}", f"    title: {json.dumps(s['title'], ensure_ascii=False)}",
                  f"    kind: {s['kind']}", f"    depends_on: [{', '.join(s.get('depends_on', []))}]"]
        if s.get("placement", "body") != "body":
            lines.append(f"    placement: {s['placement']}")
        if s.get("budget_pages") is not None:
            lines.append(f"    budget_pages: {_num(s['budget_pages'])}")
        if s.get("parent"):
            lines.append(f"    parent: {s['parent']}")
    lines += ["---", ""]
    return "\n".join(lines) + body


def _num(x) -> int | float:
    x = float(x)
    return int(x) if x.is_integer() else x


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
            elif k == "budget_pages":
                try:
                    sections[-1][k] = _num(v)
                except ValueError:
                    raise Refused(f"section {sections[-1]['id']}: budget_pages {v!r} is not a number") from None
            else:
                sections[-1][k] = v
        elif not line.startswith(" ") and ":" in line:
            in_sections = False
            k, _, v = line.partition(":")
            v = v.strip()
            meta[k] = json.loads(v) if v.startswith('"') or (k == "keywords" and v.startswith("[")) else v
    check_sections(sections)
    return meta, sections, m.group(2)


def check_sections(sections: list[dict]) -> None:
    """Kinds, ids, placements and the one-level parent structure of a section list."""
    seen: dict[str, dict] = {}
    fronts = 0
    top = None
    for s in sections:
        sid = s.get("id", "")
        if not _SLUG.match(sid):
            raise Refused(f"section id {sid!r} must be a lowercase slug")
        if sid in seen:
            raise Refused(f"section {sid} appears twice")
        if s.get("kind") not in KINDS:
            raise Refused(f"section {sid}: kind must be one of {KINDS}")
        for d in s.get("depends_on", []):
            if not _ID.match(d):
                raise Refused(f"section {sid}: depends_on {d!r} is not a C-XXXX / H-XXXX / Q-XXXX id")
        s.setdefault("placement", "body")
        if s["placement"] not in PLACEMENTS:
            raise Refused(f"section {sid}: placement must be one of {PLACEMENTS}")
        b = s.get("budget_pages")
        if b is not None and (isinstance(b, bool) or not isinstance(b, (int, float)) or b <= 0):
            raise Refused(f"section {sid}: budget_pages must be a positive number")
        parent = s.get("parent")
        if not parent:
            top = sid
        else:
            p = seen.get(parent)
            if p is None or p.get("parent") or parent != top:
                raise Refused(f"section {sid}: parent {parent!r} must be the top-level section just before it")
            if s["placement"] != p["placement"]:
                raise Refused(f"section {sid}: a subsection keeps its parent's placement ({p['placement']})")
        if s["placement"] == "front":
            fronts += 1
            if s["kind"] != "prosa" or parent:
                raise Refused(f"section {sid}: the front section (abstract) is a top-level prosa section")
        seen[sid] = s
    if fronts > 1:
        raise Refused("only one section can have placement front (the abstract)")
    for s in sections:
        if s["placement"] == "front" and any(x.get("parent") == s["id"] for x in sections):
            raise Refused(f"section {s['id']}: the abstract has no subsections")


def children_of(sections: list[dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for s in sections:
        if s.get("parent"):
            out.setdefault(s["parent"], []).append(s["id"])
    return out


def _title(sid: str, lang: str) -> str:
    es_en = TITLES.get(sid)
    return es_en[LANGS.index(lang)] if es_en else sid.replace("-", " ").capitalize()


def normalize_sections(raw, lang: str) -> tuple[list[dict], list[str] | None]:
    """A sections file (a list, or {"keywords", "sections"}) as the outline's flat list."""
    keywords = None
    if isinstance(raw, dict):
        keywords = raw.get("keywords")
        if keywords is not None and (not isinstance(keywords, list)
                                     or not all(isinstance(k, str) and k.strip() for k in keywords)):
            raise Refused("keywords must be a list of strings")
        raw = raw.get("sections")
    if not isinstance(raw, list) or not raw:
        raise Refused("the sections file must hold a non-empty list of sections")
    out: list[dict] = []

    def one(s, parent: dict | None) -> None:
        if not isinstance(s, dict):
            raise Refused("each section must be an object with id and kind")
        unknown = set(s) - {"id", "title", "kind", "placement", "budget_pages", "depends_on", "subsections"}
        if unknown:
            raise Refused(f"section {s.get('id')!r}: unknown fields {sorted(unknown)}")
        sid = str(s.get("id", ""))
        title = s.get("title")
        if title is not None and (not isinstance(title, str) or not title.strip() or "\n" in title):
            raise Refused(f"section {sid}: title must be a one-line string")
        deps = s.get("depends_on", [])
        if not isinstance(deps, list):
            raise Refused(f"section {sid}: depends_on must be a list")
        sec = {"id": sid, "title": (title or _title(sid, lang)).strip(), "kind": s.get("kind"),
               "depends_on": [str(d) for d in deps],
               "placement": s.get("placement") or (parent["placement"] if parent else "body")}
        if s.get("budget_pages") is not None:
            sec["budget_pages"] = s["budget_pages"]
        if parent:
            sec["parent"] = parent["id"]
        out.append(sec)
        subs = s.get("subsections") or []
        if subs and parent:
            raise Refused(f"section {sid}: subsections go one level deep only")
        if not isinstance(subs, list):
            raise Refused(f"section {sid}: subsections must be a list")
        for c in subs:
            one(c, sec)

    for s in raw:
        one(s, None)
    check_sections(out)
    return out, [k.strip() for k in keywords] if keywords else None


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
        sec = {"id": s["id"], "title": s.get("title") or _title(s["id"], lang), "kind": s["kind"],
               "depends_on": [], "placement": s.get("placement", "body")}
        out.append(sec)
    check_sections(out)
    return out


def _skeleton(sections: list[dict], bodies: dict[str, str] | None = None) -> list[str]:
    """Heading, marker and body (kept, or the kind's placeholder) of every section. A
    section with subsections starts with an empty body: its own text is optional."""
    bodies = bodies or {}
    kids = children_of(sections)
    lines: list[str] = []
    for s in sections:
        body = bodies.get(s["id"], "")
        if is_placeholder(body):
            body = "" if s["id"] in kids else PLACEHOLDER[s["kind"]]
        lines += [f"{'###' if s.get('parent') else '##'} {s['title']}", "", f"<!-- kairo:section {s['id']} -->",
                  body, ""]
    return lines


def init(project_dir: Path, thread: str, title: str, venue: str | None,
         design: str | None = None, lang: str = "es", sections_file: Path | None = None) -> tuple[Path, Path]:
    outline, manuscript = paths(project_dir, thread)
    if lang not in LANGS:
        raise Refused(f"lang must be one of {LANGS}")
    if venue is not None and venue not in _venues():
        raise Refused(f"unknown venue {venue!r}; known: {', '.join(sorted(_venues()))}")
    if outline.exists() or manuscript.exists():
        raise Refused(f"the manuscript for thread {thread} already exists — never overwritten")
    meta = {"paper_thread": thread, "project": project_id(project_dir), "title": title.strip(),
            "venue": venue, "design": design, "lang": lang, "created": date.today().isoformat()}
    if sections_file is not None:
        sections, meta["keywords"] = normalize_sections(_read_json(sections_file), lang)
    elif design:
        sections = design_sections(design, lang)
    else:
        sections = [dict(s, depends_on=[], placement="body") for s in DEFAULT_SECTIONS]
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
    ms += _skeleton(sections)
    manuscript.write_text("\n".join(ms), encoding="utf-8", newline="\n")
    return outline, manuscript


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise Refused(f"{Path(path).name} is not valid JSON ({e.msg}, line {e.lineno})") from None


_HEADING = re.compile(r"(?m)^(#{2,3}) [^\n]*$")


def _split_manuscript(text: str) -> tuple[str, list[str]]:
    """(text before the first section heading, the `## ` blocks that hold no section
    marker — a disclosure pasted in, for instance — each with its `###` lines)."""
    heads = list(_HEADING.finditer(text))
    chunks = []
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[h.end():end]
        marked = re.match(r"\s*<!-- kairo:section [a-z0-9-]+ -->", body) is not None
        chunks.append((h.group(1), text[h.start():end], marked))
    first = next((i for i, c in enumerate(chunks) if c[2]), None)
    prefix = text[: heads[first].start()] if first is not None else text
    extras: list[str] = []
    in_section = False
    for level, block, marked in chunks[first or 0:] if first is not None else []:
        if marked:
            in_section = True
        elif level == "##":
            in_section = False
            extras.append(block)
        elif not in_section and extras:
            extras[-1] += block
    return prefix, extras


def set_sections(project_dir: Path, thread: str, raw) -> list[dict]:
    """Replace the outline's section list; written bodies and bound ids are kept."""
    outline, manuscript = paths(project_dir, thread)
    meta, old, body = parse_outline(outline)
    new, keywords = normalize_sections(raw, meta.get("lang") or "es")
    text = manuscript.read_text(encoding="utf-8").replace("\r\n", "\n")
    bodies = section_bodies(manuscript)
    keep = {s["id"] for s in new}
    dropped = [s["id"] for s in old if s["id"] not in keep and not is_placeholder(bodies.get(s["id"], ""))]
    if dropped:
        raise Refused("these sections are written and would be lost: " + ", ".join(dropped)
                      + " — keep them in the file (or move their text first)")
    old_by = {s["id"]: s for s in old}
    for s in new:
        for d in old_by.get(s["id"], {}).get("depends_on", []):
            if d not in s["depends_on"]:
                s["depends_on"].append(d)
    if keywords is not None:
        meta["keywords"] = keywords
    prefix, extras = _split_manuscript(text)
    outline.write_text(render_outline(meta, new, body), encoding="utf-8", newline="\n")
    out = prefix.rstrip("\n") + "\n\n" + "\n".join(_skeleton(new, bodies))
    if extras:
        out = out.rstrip("\n") + "\n\n" + "\n".join(x.strip("\n") + "\n" for x in extras)
    manuscript.write_text(out, encoding="utf-8", newline="\n")
    return new


def bind(project_dir: Path, thread: str, section: str, note_id: str) -> list[str]:
    if not _ID.match(note_id):
        raise Refused("id must be C-XXXX, H-XXXX or Q-XXXX")
    outline, _ = paths(project_dir, thread)
    meta, sections, body = parse_outline(outline)
    target = next((s for s in sections if s["id"] == section), None)
    if target is None:
        raise Refused(f"no section {section} in the outline")
    folder = {"C": "Claims", "H": "Hipotesis", "Q": "Preguntas"}[note_id[0]]
    if not any((project_dir / folder).glob(f"{note_id}*.md")):
        raise Refused(f"{note_id} not found in {folder}/")
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
    return {"id": dep, "ok": g["ok"] and status == design["publishable"], "status": status, "missing": missing}


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


# a section body ends at the next `## ` heading or at a `### ` heading that opens a subsection
_BODY_END = r"(?=\n## |\n### [^\n]*\n+<!-- kairo:section |\Z)"


def section_bodies(manuscript: Path) -> dict[str, str]:
    """Text under each `<!-- kairo:section <id> -->` marker, up to the next `## ` (or the
    `### ` heading of a subsection)."""
    text = manuscript.read_text(encoding="utf-8").replace("\r\n", "\n")
    out: dict[str, str] = {}
    for m in re.finditer(r"<!-- kairo:section ([a-z0-9-]+) -->\n(.*?)" + _BODY_END, text, re.S):
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
    # a section with subsections and nothing bound of its own takes its state from them
    by_id = {x["id"]: x for x in out}
    for pid, kids in children_of(sections).items():
        x, cs = by_id[pid], [by_id[k] for k in kids]
        x["children"] = kids
        x["drafted"] = x["drafted"] or any(c["drafted"] for c in cs)
        if x["deps"]:
            continue
        if any(c["state"] == "bloqueada" for c in cs):
            x["state"], x["ready"] = "bloqueada", False
            x["why"] = "bloqueada: subsección bloqueada (" + ", ".join(c["id"] for c in cs if c["state"] == "bloqueada") + ")"
        elif all(c["state"] == "respaldada" for c in cs):
            x["state"], x["ready"], x["why"] = "respaldada", True, "todas sus subsecciones están respaldadas"
        else:
            x["state"] = "pendiente"
            x["ready"] = x["kind"] == "prosa" or all(c["ready"] for c in cs)
            x["why"] = "subsecciones pendientes: " + ", ".join(c["id"] for c in cs if c["state"] != "respaldada")
    return {"meta": meta, "sections": out}


def render_coverage(cov: dict) -> str:
    lines = ["---", f"paper_thread: {cov['meta'].get('paper_thread')}", f"generated_by: kairo/manuscript@{__version__}",
             f"generated: {date.today().isoformat()}", "---", "",
             f"# Cobertura del paper — {cov['meta'].get('title')}", "",
             "> Vista derivada: la regenera `manuscript.py coverage`. No editar a mano.", "",
             "| sección | tipo | estado | respaldo | ¿lista? | ¿redactada? |", "|---|---|---|---|---|---|"]
    for s in cov["sections"]:
        backing = "; ".join(f"{d['id']} {'✓' if d['ok'] else '✗'}" for d in s["deps"]) or "—"
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
    _, manuscript = paths(project_dir, thread)
    raw = manuscript.read_text(encoding="utf-8").replace("\r\n", "\n")
    marker = f"<!-- kairo:section {section} -->"
    m = re.search(re.escape(marker) + r"\n(.*?)" + _BODY_END, raw, re.S)
    if not m:
        raise Refused(f"marker for {section} not found in the manuscript")
    new = raw[: m.start(1)] + text.strip() + "\n" + raw[m.end(1):]
    manuscript.write_text(new, encoding="utf-8", newline="\n")


# P-ids are cited in markdown, skipping fenced code, inline code, link targets and URLs
_CITE = re.compile(r"(?ms)^(?:```|~~~).*?^(?:```|~~~)[^\n]*$|`[^`\n]*`|\]\([^)]*\)|https?://\S+|\bP-\d{4}\b")
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


def _last_error(log: str) -> str:
    errs = [ln.strip() for ln in log.splitlines() if ln.startswith("!")]
    return errs[-1] if errs else ""


SOURCE_MARK = "kairo-source.json"
CCS_PLACEHOLDER = ("% CCS concepts (ACM): genera el bloque en https://dl.acm.org/ccs y pégalo aquí\n"
                   "% (el bloque CCSXML y las líneas ccsdesc); también los datos de derechos que pide el congreso.\n")


def _lp():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import latex_parts  # noqa: PLC0415
    return latex_parts


def latex_dir(project_dir: Path, thread: str) -> Path:
    paths(project_dir, thread)
    return project_dir / "Manuscritos" / f"latex-{thread}"


def venue_of(meta: dict, override: str | None = None) -> tuple[str, dict, str | None]:
    """(name, entry, warning) of the outline's venue (or `override`); unknown = generic."""
    venues = _venues()
    name = override or meta.get("venue")
    if name not in venues:
        return "generic", venues["generic"], "venue desconocido: se usa generic"
    return name, venues[name], None


def layout(sections: list[dict], venue: dict, skip=frozenset()) -> dict:
    """Where each section goes: `front` (the abstract), `body`, `pre_refs` (the venue's
    required appendices when it wants them before the references) and `appendix`."""
    req = set(venue.get("requires") or [])
    pre = bool(venue.get("required_before_refs"))
    groups: dict[str, list[dict]] = {"front": [], "body": [], "pre_refs": [], "appendix": []}
    for s in sections:
        top = s.get("parent") or s["id"]
        if s["id"] in skip or top in skip:
            continue
        place = s.get("placement", "body")
        if place == "appendix" and pre and top in req:
            groups["pre_refs"].append(s)
        else:
            groups[place].append(s)
    return groups


def page_order(groups: dict) -> list[dict]:
    return (list(groups["body"]) + [{"boundary": "kairo:body-end"}] + list(groups["pre_refs"])
            + [{"boundary": "kairo:refs-start"}] + list(groups["appendix"]) + [{"boundary": "kairo:end"}])


def _build(out: Path) -> tuple[dict | None, list[str]]:
    """latexmk on main.tex, when installed: (labels of main.aux, warnings)."""
    import os  # noqa: PLC0415
    import shutil  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    latexmk = shutil.which("latexmk")
    if not latexmk:
        return None, []
    for stale in ("main.log", "main.aux"):
        (out / stale).unlink(missing_ok=True)
    # no shell escape; no search-path overrides; paranoid file access under TeX Live
    # (MiKTeX ignores openin_any/openout_any: the allow-list is the guard)
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith("TEXMF") and k.upper() not in ("TEXINPUTS", "BIBINPUTS", "BSTINPUTS")}
    env.update(openin_any="p", openout_any="p")
    proc = subprocess.run([latexmk, "-pdf", "-no-shell-escape", "-interaction=nonstopmode", "main.tex"],
                          cwd=out, capture_output=True, text=True, env=env)
    log = out / "main.log"
    logtext = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    if proc.returncode != 0:
        last = _last_error(logtext)
        return None, ["latex_failed: " + last if last else "latex_failed"]
    aux = out / "main.aux"
    return (_lp().aux_labels(aux.read_text(encoding="utf-8", errors="replace")) if aux.is_file() else {}), []


def page_report(labels: dict | None, groups: dict, venue: dict) -> tuple[dict, list[str]]:
    lp = _lp()
    limit = int(venue.get("body_pages", 0))
    pages = lp.body_pages(labels) if labels is not None else None
    warnings = []
    if labels is not None and pages is None:
        warnings.append("no se encontró kairo:body-end ni kairo:refs-start en main.aux: páginas desconocidas")
    over = bool(limit and pages > limit) if pages is not None else None
    per = lp.section_pages(labels, page_order(groups)) if labels else []
    return {"pages": pages, "limit": limit, "over": over, "sections_pages": per}, warnings


class _Parts:
    """Figure, generated-table and reference markers of one export, as opaque tokens
    (KAIROFIG / KAIROTAB / KAIROREF / KAIROWIDE + the export's nonce) that cross
    pandoc and the allow-list, then become LaTeX built here from checked files only."""

    def __init__(self, vault: Path, project_dir: Path, nonce: str, wide_cols: int, acm: bool = False):
        self.vault, self.project_dir, self.n, self.wide_cols = vault, project_dir, nonce, wide_cols
        self.acm = acm
        self.figs: list[str] = []
        self.refs: list[str] = []
        self.tabs: list[str] = []
        self.files: dict[str, tuple[Path, str, dict]] = {}
        self._registry: dict | None = None

    def registry(self) -> dict:
        if self._registry is None:
            self._registry = _lp().figure_registry(self.project_dir)
        return self._registry

    def figure(self, fid: str) -> tuple[Path, str, dict]:
        if fid not in self.files:
            path, entry = _lp().checked_figure(self.vault, self.project_dir, self.registry(), fid)
            self.files[fid] = (path, f"figuras/{fid}{path.suffix.lower()}", entry)
        return self.files[fid]

    def tokenize(self, md: str, sid: str) -> str:
        lp = _lp()

        def fig(m: re.Match) -> str:
            self.figure(m.group(1))
            self.figs.append(m.group(1))
            return f"KAIROFIG{self.n}N{len(self.figs):04d}"

        def ref(m: re.Match) -> str:
            if m.group(1) not in self.registry():
                raise lp.PartError(f"[[ref {m.group(1)}]]: la figura no está en Figuras/registro.json")
            self.refs.append(m.group(1))
            return f"KAIROREF{self.n}N{len(self.refs):04d}"

        def tab(m: re.Match) -> str:
            self.tabs.append(lp.table_file(self.project_dir, m.group(1), self.wide_cols, _MATH_CS, _SYMBOLS))
            return f"KAIROTAB{self.n}N{len(self.tabs):04d}"

        md = lp.MARK_FIG.sub(fig, md)
        md = lp.MARK_REF.sub(ref, md)
        md = lp.MARK_WIDE.sub(lambda m: f"KAIROWIDE{self.n}N0000", md)
        md = lp.MARK_TABFILE.sub(tab, md)
        bad = lp.MARK_ANY.search(md)
        if bad:
            raise Refused(f"la sección {sid} tiene un marcador no reconocido «{bad.group(0)}»: los válidos son "
                          "[[figura F-001]], [[tabla ancha]] y [[tabla-latex <fichero>.tex]], cada uno en su "
                          "propia línea, y [[ref F-001]] dentro del texto")
        return md

    def apply(self, tex: str, sid: str) -> str:
        lp = _lp()
        wide_tok, marks = f"KAIROWIDE{self.n}N0000", set()
        while (i := tex.find(wide_tok)) >= 0:
            rest = tex[i + len(wide_tok):].lstrip("\n")
            if not lp._LT_OPEN.match(rest):
                raise Refused(f"la sección {sid}: [[tabla ancha]] debe ir justo antes de una tabla")
            marks.add(len(lp._LT_OPEN.findall(tex[:i])))
            tex = tex[:i] + rest
        tex, _ = lp.convert_longtables(tex, self.wide_cols, marks)

        def fig(m: re.Match) -> str:
            fid = self.figs[int(m.group(1)) - 1]
            _, target, entry = self.files[fid]
            return lp.figure_env(fid, target, _tex_escape(" ".join(str(entry.get("caption") or "").split())), bool(entry.get("wide")),
                                 self.acm)

        tex = re.sub(rf"(?m)^KAIROFIG{self.n}N(\d{{4}})$", fig, tex)
        tex = re.sub(rf"(?m)^KAIROTAB{self.n}N(\d{{4}})$", lambda m: self.tabs[int(m.group(1)) - 1], tex)
        if f"KAIROFIG{self.n}" in tex or f"KAIROTAB{self.n}" in tex:
            raise Refused(f"la sección {sid}: [[figura …]] y [[tabla-latex …]] deben ir en su propio párrafo")
        return re.sub(rf"KAIROREF{self.n}N(\d{{4}})",
                      lambda m: "\\ref{fig:" + self.refs[int(m.group(1)) - 1] + "}", tex)


def export_latex(vault: Path, project_dir: Path, thread: str, anonymous: bool = False,
                 to_source: bool = False, force: bool = False) -> dict:
    import shutil  # noqa: PLC0415
    from datetime import datetime  # noqa: PLC0415
    lp = _lp()
    pandoc = shutil.which("pandoc")
    if not pandoc:
        raise Refused(f"falta pandoc: instálalo con `{PANDOC_HINT}` y repite")
    out = latex_dir(project_dir, thread)
    mark = out / SOURCE_MARK
    if mark.is_file() and not force:
        try:
            when = json.loads(mark.read_text(encoding="utf-8")).get("exported_at", "?")
        except (OSError, json.JSONDecodeError):
            when = "?"
        raise Refused(f"latex-{thread}/ es la fuente del manuscrito desde {when} (kairo-source.json): "
                      "exportar de nuevo borraría lo pulido en LaTeX. Comprueba que sigue de acuerdo con el "
                      "vault con `manuscript.py sync-check`; --force sobrescribe (guarda antes una copia de main.tex)")
    cov = coverage(vault, project_dir, thread)
    meta = cov["meta"]
    _, manuscript = paths(project_dir, thread)
    if not manuscript.exists():
        raise Refused(f"no hay manuscrito para el hilo {thread}")
    venue_name, venue, warning = venue_of(meta)
    skip = {s["id"] for s in cov["sections"] if s["id"] in ACK_IDS} if anonymous else set()
    secs = [s for s in cov["sections"] if s["id"] not in skip and s.get("parent") not in skip]
    bad = [s["id"] for s in secs if s["kind"] == "resultado" and s["state"] != "respaldada"]
    if bad:
        raise Refused("secciones de resultados sin respaldo (deben estar respaldadas): " + ", ".join(bad))
    bodies = section_bodies(manuscript)
    pending = [s["id"] for s in secs if is_placeholder(bodies.get(s["id"], "")) and not s.get("children")]
    if pending:
        raise Refused("secciones sin redactar: " + ", ".join(pending))

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
    parts = _Parts(vault, project_dir, nonce, int(venue.get("wide_table_cols", 0)), venue["class"] == "acmart")
    rendered: dict[str, str] = {}
    for s in secs:
        md = bodies.get(s["id"], "")
        if is_placeholder(md):
            rendered[s["id"]] = ""
            continue
        try:
            md = parts.tokenize(md, s["id"])
        except lp.PartError as e:
            raise Refused(f"la sección {s['id']}: {e}") from None
        tex = _pandoc(pandoc, _CITE.sub(to_cite, md))
        bad = _tex_not_allowed(tex)
        if bad:
            raise Refused(f"la sección {s['id']} usa `{bad.strip()}`, que la exportación no admite "
                          "(solo texto, fórmulas con las órdenes permitidas y nada de bloques de código): "
                          "quítalo y repite")
        try:
            rendered[s["id"]] = parts.apply(token.sub(from_token, tex), s["id"]).strip()
        except lp.PartError as e:
            raise Refused(f"la sección {s['id']}: {e}") from None

    groups = layout(secs, venue)
    acm = venue["class"] == "acmart"
    lang = meta.get("lang") or "es"
    opts = venue.get("options") or ""
    if anonymous and acm:
        opts = venue.get("anonymous_options") or ",".join(x for x in (opts, "review", "anonymous") if x)
    head = [f"\\documentclass{'[' + opts + ']' if opts else ''}{{{venue['class']}}}", "\\usepackage[utf8]{inputenc}"]
    if not acm:
        head.append(f"\\usepackage[{'english' if lang == 'en' else 'spanish'}]{{babel}}")
    if venue.get("package"):
        head.append(f"\\usepackage{{{venue['package']}}}")
    head += ["\\usepackage{graphicx}", "\\usepackage{booktabs}", "\\usepackage{array}", "\\usepackage{calc}"]
    if not acm:
        head.append("\\usepackage{url}")
    if acm or not anonymous:            # acmart hides it under `anonymous`; elsewhere no author block
        head.append("\\author{Autores por decidir}")
    style = "ACM-Reference-Format" if acm else "plain"
    keywords = ", ".join(_tex_escape(k) for k in (meta.get("keywords") or []))
    abstract = rendered[groups["front"][0]["id"]] if groups["front"] else None
    doc = "\n".join(head) + f"\n\\title{{{_tex_escape(meta.get('title') or '')}}}\n\\begin{{document}}\n"
    if acm:
        if abstract is not None:
            doc += f"\\begin{{abstract}}\n{abstract}\n\\end{{abstract}}\n\n"
        doc += CCS_PLACEHOLDER
        if keywords:
            doc += f"\\keywords{{{keywords}}}\n"
        doc += "\\maketitle\n\n"
    else:
        doc += "\\maketitle\n\n"
        if abstract is not None:
            kw = (f"\n\n\\noindent\\textbf{{{'Keywords' if lang == 'en' else 'Palabras clave'}:}} {keywords}"
                  if keywords else "")
            doc += f"\\begin{{abstract}}\n{abstract}{kw}\n\\end{{abstract}}\n\n"

    titles = venue.get("required_titles") or {}

    def section(s: dict) -> str:
        cmd = "subsection" if s.get("parent") else "section"
        title = (None if s.get("parent") else titles.get(s["id"])) or s["title"]
        body = rendered[s["id"]]
        return f"\\{cmd}{{{_tex_escape(title)}}}\\label{{kairo:sec:{s['id']}}}\n" + (f"\n{body}\n" if body else "")

    doc += "\n".join(section(s) for s in groups["body"])
    doc += "\n\\label{kairo:body-end}\n"
    if groups["pre_refs"]:
        doc += "\\appendix\n" + "\n".join(section(s) for s in groups["pre_refs"])
    doc += "\n\\label{kairo:refs-start}\n"
    if cited:
        doc += f"\\bibliographystyle{{{style}}}\n\\bibliography{{refs}}\n"
    if groups["appendix"]:
        doc += ("" if groups["pre_refs"] else "\\appendix\n") + "\n".join(section(s) for s in groups["appendix"])
    doc += "\n\\label{kairo:end}\n\\end{document}\n"

    out.mkdir(parents=True, exist_ok=True)
    tex_path, bib_path = out / "main.tex", out / "refs.bib"
    if mark.is_file() and tex_path.is_file():          # --force over a LaTeX source: keep a copy
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(tex_path, out / f"main.{stamp}.tex.bak")
    tex_path.write_text(doc, encoding="utf-8", newline="\n")
    if parts.files:
        (out / "figuras").mkdir(exist_ok=True)
        for _fid, (src, target, _entry) in parts.files.items():
            shutil.copyfile(src, out / target)
    if cited:
        import contextlib  # noqa: PLC0415
        import io  # noqa: PLC0415
        with contextlib.redirect_stdout(io.StringIO()):
            rc = _bib_module().main(["--vault", str(vault), "--only", *cited, "--out", str(bib_path),
                                     "--lang", lang])
        if rc != 0:
            raise Refused("export_bib.py no pudo escribir refs.bib")
    else:
        bib_path.write_text("", encoding="utf-8", newline="\n")
    if to_source:
        mark.write_text(json.dumps({"thread": thread, "exported_at": datetime.now().isoformat(timespec="seconds"),
                                    "main_tex_sha256": lp.sha256_file(tex_path),
                                    "generated_by": f"kairo/manuscript@{__version__}"}, indent=2) + "\n",
                        encoding="utf-8", newline="\n")
    elif mark.is_file():
        mark.unlink()

    warnings = [warning] if warning else []
    warnings += [f"modo anónimo: se omite la sección {x}" for x in sorted(skip)]
    unplaced = sorted(set(parts.refs) - set(parts.figs))
    if unplaced:
        warnings.append("se referencian figuras que no se incluyen: " + ", ".join(unplaced))
    labels, w = _build(out)
    warnings += w
    report, w = page_report(labels, groups, venue)
    warnings += w
    res = {"tex": str(tex_path), "bib": str(bib_path), **report, "uncited": uncited,
           "figures": list(parts.files), "anonymous": anonymous, "latex_source": to_source}
    if anonymous:
        sub = _submission()
        res["anonymity"] = sub.anonymity_findings(
            sys.modules[__name__], vault, project_dir, thread, "\n\n".join(bodies.get(s["id"], "") for s in secs))
    warning = "; ".join(warnings) or None
    if warning:
        res["warning"] = warning
    return res


def page_status(project_dir: Path, thread: str, build: bool = True) -> dict:
    """Body pages and pages per section of the current main.tex (rebuilt when latexmk is
    installed and `build`, else read from an existing main.aux)."""
    outline, _ = paths(project_dir, thread)
    meta, sections, _ = parse_outline(outline)
    _, venue, warning = venue_of(meta)
    out = latex_dir(project_dir, thread)
    if not (out / "main.tex").is_file():
        raise Refused(f"no hay latex-{thread}/main.tex: exporta primero")
    labels, warnings = _build(out) if build else (None, [])
    if labels is None and not warnings:
        aux = out / "main.aux"
        if aux.is_file():
            labels = _lp().aux_labels(aux.read_text(encoding="utf-8", errors="replace"))
        else:
            warnings.append("sin latexmk ni main.aux: páginas desconocidas")
    report, w = page_report(labels, layout(sections, venue), venue)
    warnings = ([warning] if warning else []) + warnings + w
    if warnings:
        report["warning"] = "; ".join(warnings)
    return report


def _submission():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import submission  # noqa: PLC0415
    return submission


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("init", "sections", "bind", "show", "coverage", "write-section", "export", "pages",
                 "sync-check", "check-venue"):
        p = sub.add_parser(name)
        p.add_argument("--project-dir", required=True, type=Path)
        p.add_argument("--thread", required=True)
        if name == "init":
            p.add_argument("--title", required=True)
            p.add_argument("--venue")
            p.add_argument("--design")
            p.add_argument("--lang", choices=LANGS, default="es")
            p.add_argument("--sections", type=Path, help="JSON file with the paper's own section list")
        if name == "sections":
            p.add_argument("--from", dest="source", required=True, type=Path, help="JSON file with the section list")
        if name == "bind":
            p.add_argument("--section", required=True)
            p.add_argument("--id", required=True)
        if name in ("coverage", "write-section", "export", "pages", "sync-check", "check-venue"):
            p.add_argument("--vault", required=True, type=Path)
        if name == "coverage":
            p.add_argument("--no-write", action="store_true", help="print only; do not write coverage-<thread>.md")
        if name == "export":
            p.add_argument("--format", choices=("latex",), required=True)
            p.add_argument("--anonymous", action="store_true", help="double-blind: acmart review,anonymous; no author block")
            p.add_argument("--to-latex-source", action="store_true",
                           help="from now on main.tex is the source: later exports refuse without --force")
            p.add_argument("--force", action="store_true", help="overwrite a latex folder marked as the source")
        if name == "pages":
            p.add_argument("--no-build", action="store_true", help="read main.aux; do not run latexmk")
        if name == "check-venue":
            p.add_argument("--venue")
        if name == "write-section":
            p.add_argument("--section", required=True)
            p.add_argument("--from", dest="source", required=True, type=Path, help="file with the section's text")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args.cmd == "init":
            o, m = init(args.project_dir, args.thread, args.title, args.venue, args.design, args.lang, args.sections)
            print(json.dumps({"outline": str(o), "manuscript": str(m)}, ensure_ascii=False))
        elif args.cmd == "sections":
            new = set_sections(args.project_dir, args.thread, _read_json(args.source))
            print(json.dumps({"sections": new}, ensure_ascii=False))
        elif args.cmd == "bind":
            print(json.dumps({"depends_on": bind(args.project_dir, args.thread, args.section, args.id)}))
        elif args.cmd == "coverage":
            cov = coverage(args.vault, args.project_dir, args.thread)
            if not args.no_write:
                out = args.project_dir / "Manuscritos" / f"coverage-{args.thread}.md"
                out.write_text(render_coverage(cov), encoding="utf-8", newline="\n")
            print(json.dumps(cov, ensure_ascii=False))
        elif args.cmd == "export":
            print(json.dumps(export_latex(args.vault, args.project_dir, args.thread, args.anonymous,
                                          args.to_latex_source, args.force), ensure_ascii=False))
        elif args.cmd == "pages":
            print(json.dumps(page_status(args.project_dir, args.thread, not args.no_build), ensure_ascii=False))
        elif args.cmd in ("sync-check", "check-venue"):
            sub_mod, me = _submission(), sys.modules[__name__]
            if args.cmd == "sync-check":
                res = sub_mod.sync_check(me, args.vault, args.project_dir, args.thread)
                print(json.dumps(res, ensure_ascii=False, indent=2))
                return 0 if res["ok"] else 4
            findings = sub_mod.check_venue(me, args.vault, args.project_dir, args.thread, args.venue)
            print(json.dumps(findings, ensure_ascii=False, indent=2))
            return 4 if any(f["severity"] == "problema" for f in findings) else 0
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
