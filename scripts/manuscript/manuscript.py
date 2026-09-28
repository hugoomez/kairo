#!/usr/bin/env python3
"""The paper a `teorico` project is organised around, from day one.

    manuscript.py init  --project-dir D --thread T --title "..." [--venue V]
    manuscript.py bind  --project-dir D --thread T --section S --id C-0003|H-0002
    manuscript.py show  --project-dir D --thread T            (JSON)

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

Exit codes: 0 ok · 3 refused · 1 error. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

__version__ = "1.0.0"
KINDS = ("prosa", "resultado", "cierre")
_ID = re.compile(r"^(C|H)-\d{4}$")
_THREAD = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")

DEFAULT_SECTIONS = [
    {"id": "introduccion", "title": "Introducción", "kind": "prosa"},
    {"id": "trabajo-relacionado", "title": "Trabajo relacionado", "kind": "prosa"},
    {"id": "planteamiento", "title": "Planteamiento y definiciones", "kind": "prosa"},
    {"id": "resultados", "title": "Resultados principales", "kind": "resultado"},
    {"id": "discusion", "title": "Discusión", "kind": "cierre"},
]

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


def render_outline(meta: dict, sections: list[dict], body: str) -> str:
    lines = ["---", f"paper_thread: {meta['paper_thread']}", f"project: {meta['project']}",
             f"title: {json.dumps(meta['title'], ensure_ascii=False)}", f"venue: {json.dumps(meta.get('venue') or 'por decidir', ensure_ascii=False)}",
             f"created: {meta['created']}", "sections:"]
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
                raise Refused(f"section {s['id']}: depends_on {d!r} is not a C-XXXX / H-XXXX id")
    return meta, sections, m.group(2)


def init(project_dir: Path, thread: str, title: str, venue: str | None) -> tuple[Path, Path]:
    outline, manuscript = paths(project_dir, thread)
    if outline.exists() or manuscript.exists():
        raise Refused(f"the manuscript for thread {thread} already exists — never overwritten")
    meta = {"paper_thread": thread, "project": project_id(project_dir), "title": title.strip(),
            "venue": venue, "created": date.today().isoformat()}
    sections = [dict(s, depends_on=[]) for s in DEFAULT_SECTIONS]
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


def bind(project_dir: Path, thread: str, section: str, note_id: str) -> list[str]:
    if not _ID.match(note_id):
        raise Refused("id must be C-XXXX or H-XXXX")
    outline, _ = paths(project_dir, thread)
    meta, sections, body = parse_outline(outline)
    target = next((s for s in sections if s["id"] == section), None)
    if target is None:
        raise Refused(f"no section {section} in the outline")
    folder = "Claims" if note_id.startswith("C-") else "Hipotesis"
    if not any((project_dir / folder).glob(f"{note_id}*.md")):
        raise Refused(f"{note_id} not found in {folder}/")
    if note_id not in target["depends_on"]:
        target["depends_on"].append(note_id)
    outline.write_text(render_outline(meta, sections, body), encoding="utf-8", newline="\n")
    return target["depends_on"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("init", "bind", "show"):
        p = sub.add_parser(name)
        p.add_argument("--project-dir", required=True, type=Path)
        p.add_argument("--thread", required=True)
        if name == "init":
            p.add_argument("--title", required=True)
            p.add_argument("--venue")
        if name == "bind":
            p.add_argument("--section", required=True)
            p.add_argument("--id", required=True)
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args.cmd == "init":
            o, m = init(args.project_dir, args.thread, args.title, args.venue)
            print(json.dumps({"outline": str(o), "manuscript": str(m)}, ensure_ascii=False))
        elif args.cmd == "bind":
            print(json.dumps({"depends_on": bind(args.project_dir, args.thread, args.section, args.id)}))
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
