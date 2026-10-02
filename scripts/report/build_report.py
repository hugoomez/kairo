#!/usr/bin/env python3
"""Build a shareable project report from an explicit selection (allow-list).

    build_report.py --vault <vault> --project-dir <vault>/Projects/<slug> \
        --selection selection.json [--researcher "<name>"] [--dry-run]

selection.json (everything is opt-in; the default is an empty report):

    {"sections": ["estado", "hipotesis", "experimentos", "cronologia",
                  "siguiente", "divulgacion"],
     "hypotheses": ["H-0001"], "experiments": ["E-0001"],
     "unpublished": ["H-0002"],
     "title": "...", "intro": "text the researcher wrote or edited",
     "next_step": "text the researcher saw and accepted"}

The report is deterministic: it copies the selected notes' own fields and
sections (the hypothesis's `## Claim`, the experiment's `## Predicción`,
`result:` and `## Resultado`), in that order, and nothing else. What was not
selected is absent, not hidden. A `send: never` note is never included, even
when selected. "divulgacion" reuses assemble-manuscript's `ai_disclosure.py`
on the selected hypotheses; it also names the experiments that adjudicate them,
so those experiment ids are allowed in the report too.

Unpublished hypotheses («sin publicar»): a hypothesis whose status is
`propuesta`, `en_cola` (neither is preregistered), `descartada`, or missing.
It is included only when its id is in `hypotheses` AND in `unpublished` (the
researcher's explicit tick in the «Sin publicar» group); otherwise it is
excluded with a reason. An included one is labelled «sin publicar» in the
report itself, so the preview and the reader both see it.

Before anything is written, the report is scanned. Any of these blocks it:
  - an H-/E-/C- id that was not selected;
  - the id or title of a `send: never` note;
  - what `check_bundle.py` blocks (secrets, absolute vault paths, copied
    vault notes), plus any vault-relative `Papers/` or `Projects/` path.
Nothing is written when blocked. Every blocking finding has `severity:
"crítico"` (it stops the export).

The JSON always carries `preview_sha256` (sha256 of the exact Markdown) and
`html_sha256` (of the exact HTML that was scanned). With `--expect-sha256 <hex>`
the report is written only if its Markdown is exactly the one previewed:
otherwise nothing is written and the exit code is 4 (`stale_preview`). The
backend uses this so an export is always of the content the researcher saw.

    build_report.py --options --vault <vault> --project-dir <dir>

lists what can be selected, for the selector: publishable hypotheses,
«sin publicar» ones (in their own group), experiments, and the ids that never
leave (`send: never`, listed without their text).

Output: `<project>/Informes/informe-<date>[-n].md` and `.html` (self-contained,
printable to PDF from the browser). With `--dry-run`, the Markdown is returned
in the JSON (`preview`) and nothing is written.

Prints one JSON object. Exit codes: 0 built (or previewed) · 3 blocked ·
4 stale preview · 1 error. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ledger"))
from notes import parse_frontmatter, section, split_note  # noqa: E402

TOOL = "kairo/build_report@1.1.0"
# Not preregistered (propuesta, en_cola) or discarded: never pre-selected, own group.
UNPUBLISHED = ("propuesta", "en_cola", "descartada")
UNPUBLISHED_LABEL = "sin publicar"
SECTIONS = ("estado", "hipotesis", "experimentos", "cronologia", "siguiente", "divulgacion")
_ID = re.compile(r"\b([HEC]-\d{4})\b")
_SEL_ID = re.compile(r"^[HE]-\d{4}$")
CHECK_BUNDLE = HERE.parent / "security" / "check_bundle.py"
DISCLOSURE = HERE.parent.parent / "skills" / "assemble-manuscript" / "scripts" / "ai_disclosure.py"


class Refused(Exception):
    pass


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_unpublished(fm: dict) -> bool:
    status = str(fm.get("status") or "").strip()
    return not status or status in UNPUBLISHED


# --------------------------------------------------------------------------
# Reading notes
# --------------------------------------------------------------------------

def read(path: Path) -> tuple[dict, list[str], str]:
    parts = split_note(path.read_text(encoding="utf-8-sig"))
    if parts is None:
        return {}, [], path.read_text(encoding="utf-8-sig")
    fm_lines, body = parts
    return parse_frontmatter(fm_lines), fm_lines, body


def is_send_never(fm: dict) -> bool:
    return str(fm.get("send", "")).strip().lower() == "never"


def nested(fm_lines: list[str], key: str) -> dict[str, str]:
    """A one-level nested map (`result:` → {effect, p_value, verdict, …})."""
    out: dict[str, str] = {}
    inside = False
    for ln in fm_lines:
        if re.match(rf"^{re.escape(key)}:\s*(#.*)?$", ln):
            inside = True
            continue
        if inside:
            if ln and not ln[0].isspace():
                break
            m = re.match(r"^\s+([A-Za-z_]\w*):\s*(.*?)\s*(#.*)?$", ln)
            if m and m.group(2):
                out[m.group(1)] = m.group(2).strip("'\"")
    return out


def history(fm_lines: list[str]) -> list[dict[str, str]]:
    """`history:` entries (date, status, …); append-only list of maps."""
    out: list[dict[str, str]] = []
    inside = False
    for ln in fm_lines:
        if re.match(r"^history:\s*(#.*)?$", ln):
            inside = True
            continue
        if not inside:
            continue
        if ln and not ln[0].isspace() and not ln.startswith("- "):
            break
        m = re.match(r"^\s*-\s+([A-Za-z_]\w*):\s*(.*?)\s*$", ln)
        if m:
            out.append({m.group(1): m.group(2).strip("'\"")})
            continue
        m = re.match(r"^\s+([A-Za-z_]\w*):\s*(.*?)\s*$", ln)
        if m and out:
            out[-1][m.group(1)] = m.group(2).strip("'\"")
    return out


def own_text(body: str, heading: str) -> str:
    """A section's text without template placeholder lines (`<…>`) and HTML comments."""
    txt = re.sub(r"<!--.*?-->", "", section(body, heading), flags=re.DOTALL)
    lines = [ln for ln in txt.split("\n") if not re.fullmatch(r"\s*<[^<>]*>\s*", ln)]
    return "\n".join(lines).strip()


def notes_in(folder: Path) -> dict[str, Path]:
    out = {}
    if folder.is_dir():
        for p in sorted(folder.glob("*.md")):
            m = re.match(r"^([HEC]-\d{4})", p.name)
            if m:
                out[m.group(1)] = p
    return out


# --------------------------------------------------------------------------
# Building
# --------------------------------------------------------------------------

def cell(s: object) -> str:
    return str(s if s not in (None, "") else "—").replace("|", r"\|").replace("\n", " ")


def build(vault: Path, pdir: Path, sel: dict, researcher: str | None) -> dict:
    sections = [s for s in sel.get("sections", []) if s in SECTIONS]
    unknown = [s for s in sel.get("sections", []) if s not in SECTIONS]
    if unknown:
        raise Refused(f"unknown sections: {', '.join(unknown)}")
    for key in ("hypotheses", "experiments", "unpublished"):
        bad = [x for x in sel.get(key, []) if not _SEL_ID.match(str(x))]
        if bad:
            raise Refused(f"bad ids in {key}: {', '.join(map(str, bad))}")
    hub_fm, _, _ = read(pdir / "_hub.md")
    hyp_paths = notes_in(pdir / "Hipotesis")
    exp_paths = notes_in(pdir / "Experimentos")
    excluded: list[dict] = []

    def pick(ids: list[str], paths: dict[str, Path]) -> list[tuple[str, dict, list[str], str]]:
        out = []
        for i in dict.fromkeys(ids):
            if i not in paths:
                excluded.append({"id": i, "reason": "no existe en este proyecto"})
                continue
            fm, fl, body = read(paths[i])
            if is_send_never(fm):
                excluded.append({"id": i, "reason": "marcada send: never — nunca sale en un informe"})
                continue
            out.append((i, fm, fl, body))
        return out

    ticked = set(sel.get("unpublished", []))
    hyps = []
    for h in pick(sel.get("hypotheses", []), hyp_paths):
        if is_unpublished(h[1]) and h[0] not in ticked:
            excluded.append({"id": h[0], "reason": f"{UNPUBLISHED_LABEL} ({h[1].get('status') or 'sin estado'}): "
                                                   "márcala expresamente en «Sin publicar» para incluirla"})
            continue
        hyps.append(h)
    unpublished = sorted(h[0] for h in hyps if is_unpublished(h[1]))
    exps = pick(sel.get("experiments", []), exp_paths)
    hyp_ids = {h[0] for h in hyps}
    allowed = set(hyp_ids) | {e[0] for e in exps}

    title = (sel.get("title") or "").strip() or f"Informe — {hub_fm.get('name') or pdir.name}"
    md: list[str] = ["---", "tipo: informe", f"fecha: {date.today().isoformat()}", f"generado_por: {TOOL}",
                     f"secciones: [{', '.join(sections)}]", "---", "", f"# {title}", "",
                     f"*{date.today().isoformat()}*", ""]
    intro = (sel.get("intro") or "").strip()
    if intro:
        md += [intro, ""]

    if "estado" in sections:
        md += ["## Estado del proyecto", "",
               "| | |", "|---|---|",
               f"| Proyecto | {cell(hub_fm.get('name') or pdir.name)} |",
               f"| Tipo | {cell(hub_fm.get('type'))} |",
               f"| Estado | {cell(hub_fm.get('status'))} |"]
        if hyps:
            counts: dict[str, int] = {}
            for _, fm, _, _ in hyps:
                counts[str(fm.get("status") or "—")] = counts.get(str(fm.get("status") or "—"), 0) + 1
            md.append(f"| Hipótesis en este informe | {', '.join(f'{v} {k}' for k, v in sorted(counts.items()))} |")
        if exps:
            md.append(f"| Experimentos en este informe | {len(exps)} |")
        md.append("")

    if "hipotesis" in sections and hyps:
        md += ["## Hipótesis", "", "| Id | Estado | Confianza |", "|---|---|---|"]
        for i, fm, fl, _ in hyps:
            conf_line = next((ln for ln in fl if ln.startswith("confidence:")), "")
            kind = re.search(r"kind:\s*(\w+)", conf_line)
            conf = f"{fm.get('confidence') or '—'}" + (f" ({kind.group(1)})" if kind else "")
            st = cell(fm.get("status")) + (f" — {UNPUBLISHED_LABEL}" if i in unpublished else "")
            md.append(f"| {i} | {st} | {cell(conf)} |")
        md.append("")
        for i, _fm, _, body in hyps:
            head = f"### {i} · {UNPUBLISHED_LABEL}" if i in unpublished else f"### {i}"
            note = ([f"*{UNPUBLISHED_LABEL.capitalize()}: hipótesis no preregistrada o descartada; no es un resultado.*", ""]
                    if i in unpublished else [])
            md += [head, "", *note, own_text(body, "Claim") or "*(sin sección Claim)*", ""]

    if "experimentos" in sections and exps:
        md += ["## Experimentos", ""]
        for i, fm, fl, body in exps:
            h = str(fm.get("hypothesis") or "")
            hyp = h if h in hyp_ids else "(hipótesis no incluida en este informe)" if h else "—"
            res = nested(fl, "result")
            md += [f"### {i}", "",
                   "| | |", "|---|---|",
                   f"| Hipótesis | {cell(hyp)} |",
                   f"| Estado | {cell(fm.get('status'))} |",
                   f"| Rol / nivel | {cell(fm.get('role'))} / {cell(fm.get('tier'))} |",
                   f"| Plan de análisis | {cell(fm.get('analysis_plan'))} |",
                   f"| Preregistro congelado | {cell(fm.get('frozen_at'))} |",
                   f"| Validez | {cell(fm.get('experiment_validity'))} |"]
            for k, label in (("effect", "Efecto"), ("p_value", "p"), ("bayes_factor", "Factor de Bayes"),
                             ("verdict", "Veredicto")):
                if res.get(k) and not res[k].startswith("<"):
                    md.append(f"| {label} | {cell(res[k])} |")
            md.append("")
            for sec in ("Predicción", "Resultado"):
                txt = own_text(body, sec)
                if txt:
                    md += [f"**{sec}**", "", txt, ""]

    if "cronologia" in sections and (hyps or exps):
        events: list[tuple[str, str, str]] = []
        for i, _, fl, _ in hyps:
            for h in history(fl):
                if h.get("date"):
                    events.append((h["date"], i, f"estado → {h.get('status', '?')}"))
        for i, fm, fl, _ in exps:
            if fm.get("frozen_at") and not str(fm["frozen_at"]).startswith("<"):
                events.append((str(fm["frozen_at"])[:10], i, "preregistro congelado"))
            v = nested(fl, "result").get("verdict")
            if fm.get("status") == "completed" and v and not v.startswith("<"):
                events.append((str(fm.get("updated") or fm.get("frozen_at") or "")[:10], i, f"completado: {v}"))
        md += ["## Cronología", "", "| Fecha | Nota | Qué pasó |", "|---|---|---|"]
        md += [f"| {cell(d)} | {i} | {cell(w)} |" for d, i, w in sorted(events)]
        md.append("")

    if "siguiente" in sections and (sel.get("next_step") or "").strip():
        md += ["## Siguiente paso", "", sel["next_step"].strip(), ""]

    if "divulgacion" in sections and hyps:
        text, adjudicating = disclosure(vault, pdir, sorted(hyp_ids), researcher)
        allowed |= adjudicating
        md += [text.strip(), ""]

    text = "\n".join(md).rstrip() + "\n"
    return {"markdown": text, "allowed": sorted(allowed), "excluded": excluded,
            "hypotheses": sorted(hyp_ids), "experiments": sorted(e[0] for e in exps), "sections": sections,
            "unpublished": unpublished}


def disclosure(vault: Path, pdir: Path, hyp_ids: list[str], researcher: str | None) -> tuple[str, set[str]]:
    args = [sys.executable, str(DISCLOSURE), "--vault", str(vault), "--project", pdir.name,
            "--thread", "informe", "--hypotheses", ",".join(hyp_ids), "--lang", "es"]
    if researcher:
        args += ["--researcher", researcher]
    r = subprocess.run(args + ["--format", "json"], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise Refused(f"ai_disclosure.py: {(r.stderr or r.stdout).strip()[:300]}")
    js = json.loads(r.stdout)
    exps = {e if isinstance(e, str) else e.get("id") for e in js.get("experiments", [])}
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise Refused(f"ai_disclosure.py: {(r.stderr or r.stdout).strip()[:300]}")
    # vault-relative paths name the local layout; the report says "(proyecto)"
    text = re.sub(r"Projects/[^/\s)]+/", "(proyecto)/", r.stdout)
    return text, {e for e in exps if e}


# --------------------------------------------------------------------------
# The leak scan
# --------------------------------------------------------------------------

def never_notes(vault: Path, pdir: Path) -> list[tuple[str, str]]:
    """(id, title) of every send: never note in the project and in Papers/."""
    out = []
    for p in list(pdir.rglob("*.md")) + list((vault / "Papers").glob("*.md")):
        try:
            fm, _, body = read(p)
        except OSError:
            continue
        if is_send_never(fm):
            m = re.match(r"^((?:[HECP]|PROJ|ADR)-\d{3,4})", p.name)
            title = str(fm.get("title") or fm.get("name") or "")
            if not title:
                h1 = re.search(r"^# (.+)$", body, re.MULTILINE)
                title = h1.group(1).strip() if h1 else ""
            out.append((str(fm.get("id") or (m.group(1) if m else p.stem)), title))
    return out


def scan(vault: Path, pdir: Path, texts: dict[str, str], allowed: set[str]) -> list[dict]:
    blocking: list[dict] = []
    joined = "\n".join(texts.values())
    for i in sorted(set(_ID.findall(joined)) - allowed):
        blocking.append({"kind": "unselected_id", "detail": f"{i} aparece en el informe pero no está seleccionada"})
    for nid, title in never_notes(vault, pdir):
        if re.search(rf"\b{re.escape(nid)}\b", joined):
            blocking.append({"kind": "send_never", "detail": f"{nid} (send: never) aparece en el informe"})
        if title and len(title) >= 8 and title in joined:
            blocking.append({"kind": "send_never", "detail": f"el título de {nid} (send: never) aparece en el informe"})
    with tempfile.TemporaryDirectory(prefix="kairo-report-") as tmp:
        for name, t in texts.items():
            Path(tmp, name).write_text(t, encoding="utf-8")
        r = subprocess.run([sys.executable, str(CHECK_BUNDLE), tmp], capture_output=True, text=True, encoding="utf-8")
        try:
            res = json.loads(r.stdout)
        except json.JSONDecodeError as exc:
            raise Refused(f"check_bundle.py failed: {(r.stderr or r.stdout).strip()[:300]}") from exc
        if res.get("status") == "error":
            raise Refused("check_bundle.py could not complete")
        for f in res.get("findings", []):
            if f["severity"] == "block" or f["kind"] == "vault_path_reference":
                blocking.append({"kind": f["kind"], "detail": f"check_bundle.py: {f['kind']} en {f['path']}"})
    for b in blocking:
        b["severity"] = "crítico"  # every one of these stops the export
    return blocking


# --------------------------------------------------------------------------
# HTML (self-contained, printable)
# --------------------------------------------------------------------------

CSS = """body{font:16px/1.55 Georgia,serif;max-width:46rem;margin:2rem auto;padding:0 1rem;color:#1b1b1b;background:#fff}
h1,h2,h3{font-family:system-ui,sans-serif;line-height:1.25}h2{margin-top:2.2rem;border-bottom:1px solid #ddd}
table{border-collapse:collapse;margin:1rem 0;width:100%}td,th{border:1px solid #ccc;padding:.3rem .5rem;text-align:left;vertical-align:top}
blockquote{border-left:3px solid #aaa;margin:1rem 0;padding:.2rem 1rem;color:#333}code{font-size:.9em}
@media print{body{margin:0;max-width:none}}"""


def inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<em>\1</em>", s)
    return s


def to_html(md: str, title: str) -> str:
    parts = split_note(md)
    body = parts[1] if parts else md
    out: list[str] = []
    lines = body.split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue
        h = re.match(r"^(#{1,4})\s+(.*)$", ln)
        if h:
            n = len(h.group(1))
            out.append(f"<h{n}>{inline(h.group(2))}</h{n}>")
            i += 1
            continue
        if ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(lines[i])
                i += 1
            cells = [[c.strip().replace(r"\|", "|") for c in re.split(r"(?<!\\)\|", r.strip())[1:-1]] for r in rows]
            cells = [c for c in cells if not all(re.fullmatch(r":?-+:?", x) for x in c if x)]
            if cells:
                head, rest = cells[0], cells[1:]
                out.append("<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
                           + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rest)
                           + "</tbody></table>")
            continue
        if ln.startswith(">"):
            q = []
            while i < len(lines) and lines[i].startswith(">"):
                q.append(re.sub(r"^>\s?", "", lines[i]))
                i += 1
            out.append("<blockquote>" + "<br>".join(inline(x) for x in q) + "</blockquote>")
            continue
        if re.match(r"^\s*[-*]\s+", ln):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*]\s+", "", lines[i]))
                i += 1
            out.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ul>")
            continue
        para = []
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,4}\s|\||>|\s*[-*]\s)", lines[i]):
            para.append(lines[i].strip())
            i += 1
        out.append(f"<p>{inline(' '.join(para))}</p>")
    return ("<!doctype html>\n<html lang=\"es\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body>\n"
            + "\n".join(out) + "\n</body></html>\n")


# --------------------------------------------------------------------------

def options(pdir: Path) -> dict:
    """What the selector may offer. send: never notes are listed by id only."""
    def excerpt(body: str) -> str:
        t = re.sub(r"\s+", " ", own_text(body, "Claim"))
        return t[:160] + ("…" if len(t) > 160 else "")

    out: dict = {"hypotheses": [], "unpublished": [], "experiments": [], "never": []}
    for hid, path in notes_in(pdir / "Hipotesis").items():
        if not hid.startswith("H-"):
            continue
        fm, _, body = read(path)
        if is_send_never(fm):
            out["never"].append(hid)
            continue
        item = {"id": hid, "status": fm.get("status") or None, "claim": excerpt(body)}
        out["unpublished" if is_unpublished(fm) else "hypotheses"].append(item)
    for eid, path in notes_in(pdir / "Experimentos").items():
        if not eid.startswith("E-"):
            continue
        fm, _, _ = read(path)
        if is_send_never(fm):
            out["never"].append(eid)
            continue
        out["experiments"].append({"id": eid, "status": fm.get("status") or None,
                                   "hypothesis": str(fm.get("hypothesis") or "") or None})
    return out


def out_paths(pdir: Path) -> tuple[Path, Path]:
    folder = pdir / "Informes"
    stem = f"informe-{date.today().isoformat()}"
    n = 1
    while (folder / f"{stem}{'' if n == 1 else f'-{n}'}.md").exists():
        n += 1
    name = f"{stem}{'' if n == 1 else f'-{n}'}"
    return folder / f"{name}.md", folder / f"{name}.html"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vault", required=True, type=Path)
    ap.add_argument("--project-dir", required=True, type=Path)
    ap.add_argument("--selection", type=Path, default=None)
    ap.add_argument("--options", action="store_true", help="list what can be selected and exit")
    ap.add_argument("--expect-sha256", default=None, help="write only if the Markdown is exactly the previewed one")
    ap.add_argument("--researcher", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        vault, pdir = a.vault.resolve(), a.project_dir.resolve()
        if not (pdir / "_hub.md").is_file():
            raise Refused(f"{pdir} is not a project folder (no _hub.md)")
        if a.options:
            print(json.dumps({"tool": TOOL, **options(pdir)}, ensure_ascii=False))
            return 0
        if a.selection is None:
            raise Refused("--selection is required")
        sel = json.loads(a.selection.read_text(encoding="utf-8"))
        rep = build(vault, pdir, sel, a.researcher)
        title = re.search(r"^# (.+)$", rep["markdown"], re.MULTILINE).group(1)
        page = to_html(rep["markdown"], title)
        blocking = scan(vault, pdir, {"informe.md": rep["markdown"], "informe.html": page}, set(rep["allowed"]))
        result = {"tool": TOOL, "ok": not blocking, "blocking": blocking, "excluded": rep["excluded"],
                  "hypotheses": rep["hypotheses"], "experiments": rep["experiments"], "sections": rep["sections"],
                  "unpublished": rep["unpublished"], "preview_sha256": sha256(rep["markdown"]),
                  "html_sha256": sha256(page)}
        if a.dry_run or blocking:
            result["preview"] = rep["markdown"]
        stale = bool(a.expect_sha256) and a.expect_sha256 != result["preview_sha256"]
        if not a.dry_run and not blocking and stale:
            result.update(ok=False, stale_preview=True, preview=rep["markdown"],
                          error="el contenido cambió desde la vista previa: vuelve a previsualizar")
            print(json.dumps(result, ensure_ascii=False))
            return 4
        if not a.dry_run and not blocking:
            md_path, html_path = out_paths(pdir)
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(rep["markdown"], encoding="utf-8", newline="\n")
            html_path.write_text(page, encoding="utf-8", newline="\n")
            result["md"] = md_path.relative_to(vault).as_posix()
            result["html"] = html_path.relative_to(vault).as_posix()
        print(json.dumps(result, ensure_ascii=False))
        return 0 if not blocking else 3
    except Refused as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
