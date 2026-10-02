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


def dep_state(vault: Path, dep: str) -> dict:
    """Whether one bound claim / hypothesis passed its gate."""
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
    m = re.search(re.escape(marker) + r"\n(.*?)(?=\n## |\Z)", raw, re.S)
    if not m:
        raise Refused(f"marker for {section} not found in the manuscript")
    new = raw[: m.start(1)] + text.strip() + "\n" + raw[m.end(1):]
    manuscript.write_text(new, encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("init", "bind", "show", "coverage", "write-section"):
        p = sub.add_parser(name)
        p.add_argument("--project-dir", required=True, type=Path)
        p.add_argument("--thread", required=True)
        if name == "init":
            p.add_argument("--title", required=True)
            p.add_argument("--venue")
        if name == "bind":
            p.add_argument("--section", required=True)
            p.add_argument("--id", required=True)
        if name in ("coverage", "write-section"):
            p.add_argument("--vault", required=True, type=Path)
        if name == "coverage":
            p.add_argument("--no-write", action="store_true", help="print only; do not write coverage-<thread>.md")
        if name == "write-section":
            p.add_argument("--section", required=True)
            p.add_argument("--from", dest="source", required=True, type=Path, help="file with the section's text")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args.cmd == "init":
            o, m = init(args.project_dir, args.thread, args.title, args.venue)
            print(json.dumps({"outline": str(o), "manuscript": str(m)}, ensure_ascii=False))
        elif args.cmd == "bind":
            print(json.dumps({"depends_on": bind(args.project_dir, args.thread, args.section, args.id)}))
        elif args.cmd == "coverage":
            cov = coverage(args.vault, args.project_dir, args.thread)
            if not args.no_write:
                out = args.project_dir / "Manuscritos" / f"coverage-{args.thread}.md"
                out.write_text(render_coverage(cov), encoding="utf-8", newline="\n")
            print(json.dumps(cov, ensure_ascii=False))
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
