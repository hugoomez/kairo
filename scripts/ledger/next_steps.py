#!/usr/bin/env python3
"""What the progress of a Kairo project has left pending — and what resolves each item.

    next_steps.py --vault <vault> --project <slug | PROJ-XXX> [--json]

Read-only. Each item has a `kind`, a `severity` (crítico | importante | menor),
the `target` it is about, a `message`, the `action` that resolves it and the
`refs` (note ids) involved. Kinds:

  experimento_sin_adjudicar  a confirmatory experiment is `completed` (valid, or
                             with a verdict) but its hypothesis is still
                             preregistrada / en_experimento and its history never
                             names it → update-confidence.          importante
  hub_sin_valor              a hub field still holds the template's `<value>`
                             (budget fields importante, others menor).
  hipotesis_sin_evaluar      an open hypothesis lacks worth / cost fields
                             (vale_la_pena … evaluado) → hypothesis-cycle
                             «¿Merece la pena?».                     menor
  hipotesis_no_merece        an open hypothesis says `vale_la_pena: no` → the
                             researcher decides (discard, reformulate, keep). menor
  frontera_obsoleta          Frontera/mapa.json says so (`obsoleto`), a project
                             paper is missing from it or was added after it was
                             generated, mapa.yaml is newer than mapa.json, a
                             marked cell already has papers, or a hypothesis's
                             cells / status differ from the map's «nuestro»
                             layer → frontier-map actualizar / frontier.py build.
  hueco_sin_hipotesis        a hueco of mapa.json that no hypothesis attacks
                             (neither the map's `atacado_por` nor any
                             hypothesis's frontier_cells) → hypothesis-cycle
                             with that gap.                           menor

A project without Frontera/ simply has no frontier items. Notes marked
`send: never` are skipped.

Exit codes: 0 ok (pending items or not) · 2 bad input. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "security"))
import project_scan as ps  # noqa: E402
from notes import read_note, split_note  # noqa: E402
from send_guard import is_flagged, is_model_notes  # noqa: E402

__version__ = "1.0.0"
TOOL = f"kairo/next_steps@{__version__}"
SEVERITY_ORDER = {"crítico": 0, "importante": 1, "menor": 2}
BUDGET_KEYS = ("compute_budget", "completo_cost_threshold", "ladder_cost_threshold")
FRONTIER_BUILD = "python ${CLAUDE_PLUGIN_ROOT}/scripts/frontier/frontier.py build --vault <vault> --project <slug>"


def item(kind: str, severity: str, target: str, message: str, action: str, refs: list[str] | None = None) -> dict:
    return {"kind": kind, "severity": severity, "target": target, "message": message,
            "action": action, "refs": refs or []}


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def unadjudicated(hyps: list[dict], exps: list[dict]) -> list[dict]:
    by_h = {h["id"]: h for h in hyps}
    out = []
    for e in exps:
        if e["status"] != "completed" or not ps.confirmatory(e) or e["validity"] == "invalid":
            continue
        if not (e["validity"] == "valid" or e["verdict"]):
            continue
        h = by_h.get(e["hypothesis"] or "")
        if h is None or h["status"] not in ("preregistrada", "en_experimento"):
            continue
        if e["id"] in h["history_experiments"]:
            continue
        verdict = f", veredicto {e['verdict']}" if e["verdict"] else ""
        out.append(item(
            "experimento_sin_adjudicar", "importante", h["id"],
            f"{e['id']} está completed{verdict}, pero {h['id']} sigue «{h['status']}» y su history no lo nombra.",
            f"update-confidence sobre {h['id']} (disparador: resultado de {e['id']}).",
            [h["id"], e["id"]]))
    return out


def hub_placeholders(pdir: Path) -> list[dict]:
    parsed = split_note((pdir / "_hub.md").read_text(encoding="utf-8-sig")) if (pdir / "_hub.md").is_file() else None
    if not parsed:
        return []
    out = []
    for line in parsed[0]:
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*<value>\s*(#.*)?$", line)
        if not m:
            continue
        key = m.group(1)
        sev = "importante" if key in BUDGET_KEYS else "menor"
        why = (" — sin él, hypothesis_cost.py y preregister-experiment no pueden comparar el coste con el presupuesto"
               if key in BUDGET_KEYS else "")
        out.append(item("hub_sin_valor", sev, f"_hub.md:{key}",
                        f"`{key}` sigue en `<value>`{why}.",
                        f"Rellenar `{key}` en _hub.md, o borrar la línea si no aplica.", []))
    return out


def worth(hyps: list[dict]) -> list[dict]:
    out = []
    for h in hyps:
        if h["status"] not in ps.OPEN_STATUSES:
            continue
        missing = [k for k, v in h["worth"].items() if v is None]
        if missing:
            out.append(item("hipotesis_sin_evaluar", "menor", h["id"],
                            f"{h['id']} ({h['status']}) no dice si merece la pena ni cuánto cuesta: "
                            f"faltan {', '.join(missing)}.",
                            f"hypothesis-cycle, paso «¿Merece la pena?», sobre {h['id']}.", [h["id"]]))
        if (h["worth"].get("vale_la_pena") or "").lower() == "no" and h["status"] in ("propuesta", "en_cola"):
            razon = h["worth"].get("vale_la_pena_razon") or "sin razón escrita"
            out.append(item("hipotesis_no_merece", "menor", h["id"],
                            f"{h['id']} está evaluada como «no merece la pena» ({razon}) y sigue «{h['status']}».",
                            "Decide el investigador: descartarla (review.py discard), reformularla o mantenerla.",
                            [h["id"]]))
    return out


def _date_of(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def project_papers(vault: Path, pid: str) -> list[dict]:
    out = []
    pat = re.compile(r"(?<![\w-])" + re.escape(pid) + r"(?![\w-])")
    for p in sorted((vault / "Papers").glob("P-*.md")):
        if is_model_notes(p.resolve()) or is_flagged(p):
            continue
        parsed = read_note(p)
        if not parsed:
            continue
        fm = parsed[0]
        projects = fm.get("projects")
        projects = " ".join(projects) if isinstance(projects, list) else str(projects or "")
        if not pat.search(projects):
            continue
        out.append({"id": ps.scalar(fm, "id") or p.stem.split(" ")[0], "added": _date_of(ps.scalar(fm, "added"))})
    return out


def frontier(vault: Path, pdir: Path, pid: str, hyps: list[dict]) -> list[dict]:
    paths = ps.frontier_paths(pdir)
    has_yaml, has_json = paths["yaml"].is_file(), paths["json"].is_file()
    if not (has_yaml or has_json):
        return []
    out = []
    build = f"frontier.py build ({FRONTIER_BUILD.replace('<slug>', pdir.name)})"
    if has_yaml and not has_json:
        return [item("frontera_obsoleta", "importante", "Frontera/mapa.json",
                     "Hay Frontera/mapa.yaml pero no mapa.json: la app no puede mostrar la frontera.",
                     build, [])]
    mapa, problem = ps.load_mapa(pdir)
    if problem:
        return [item("frontera_obsoleta", "importante", "Frontera/mapa.json", problem + ".", build, [])]
    if has_yaml and paths["yaml"].stat().st_mtime > paths["json"].stat().st_mtime + 1:
        out.append(item("frontera_obsoleta", "importante", "Frontera/mapa.json",
                        "mapa.yaml se editó después de generar mapa.json.", build, []))
    obs = mapa.get("obsoleto") or {}
    sin = [str(x) for x in obs.get("papers_sin_extraer") or []]
    tax = [str(x) for x in obs.get("taxonomia_cambiada") or []]
    if sin:
        out.append(item("frontera_obsoleta", "importante", "Frontera",
                        f"{len(sin)} paper(s) del proyecto sin extraer en la frontera: {', '.join(sin[:10])}"
                        f"{'…' if len(sin) > 10 else ''}.",
                        "kairo:frontier-map en modo actualizar (extrae solo esos papers).", sin))
    if tax:
        out.append(item("frontera_obsoleta", "importante", "Frontera",
                        f"{len(tax)} extracción(es) hechas con una taxonomía anterior: {', '.join(tax[:10])}"
                        f"{'…' if len(tax) > 10 else ''}.",
                        "kairo:frontier-map en modo actualizar (re-extrae con la taxonomía vigente).", tax))
    in_map = {str(p.get("id")) for p in mapa.get("papers") or [] if isinstance(p, dict) and p.get("id")}
    gen = _date_of(mapa.get("generado"))
    newer, missing = [], []
    for p in project_papers(vault, pid):
        if p["id"] in sin or p["id"] in tax:
            continue
        if in_map and p["id"] not in in_map:
            missing.append(p["id"])
        elif gen and p["added"] and p["added"] > gen:
            newer.append(p["id"])
    if missing:
        out.append(item("frontera_obsoleta", "importante", "Frontera",
                        f"{len(missing)} paper(s) del proyecto no están en mapa.json: {', '.join(missing[:10])}"
                        f"{'…' if len(missing) > 10 else ''}.",
                        "kairo:frontier-map en modo actualizar.", missing))
    if newer:
        out.append(item("frontera_obsoleta", "menor", "Frontera",
                        f"{len(newer)} paper(s) añadidos después de generar mapa.json ({gen}): "
                        f"{', '.join(newer[:10])}{'…' if len(newer) > 10 else ''}.",
                        "kairo:frontier-map en modo actualizar, si su extracción no está al día.", newer))

    cells = ps.mapa_cells(mapa)
    for cid, c in sorted(cells.items()):
        if c.get("marca") and c.get("papers"):
            refs = [str(x) for x in c["papers"]]
            out.append(item("frontera_obsoleta", "importante", cid,
                            f"La casilla {cid} está marcada «{c['marca']}» pero ya la cubre(n) {', '.join(refs)}.",
                            "Revisar la marca en mapa.yaml (kairo:frontier-map actualizar) y reconstruir.", refs))
    # «nuestro» layer vs the hypotheses as they are now
    drift = []
    for h in hyps:
        for cid in h["frontier_cells"]:
            ours = {str(n.get("id")): n.get("status") for n in (cells.get(cid) or {}).get("nuestros") or []
                    if isinstance(n, dict)}
            if cid not in cells:
                drift.append(f"{h['id']} apunta a {cid}, que no existe en el mapa")
            elif ours.get(h["id"]) != h["status"]:
                drift.append(f"{h['id']} ({h['status']}) en {cid}: el mapa dice {ours.get(h['id']) or 'nada'}")
    if drift:
        out.append(item("frontera_obsoleta", "menor", "Frontera/mapa.json",
                        "La capa «nuestro» del mapa no refleja las hipótesis actuales: " + "; ".join(drift[:5])
                        + ("…" if len(drift) > 5 else "") + ".",
                        build, sorted({d.split(" ")[0] for d in drift})))

    attacked: dict[str, list[str]] = {}
    for h in hyps:
        for cid in h["frontier_cells"]:
            attacked.setdefault(cid, []).append(h["id"])
    for g in ps.mapa_huecos(mapa):
        gid = str(g.get("id") or "?")
        by_map = [str(a.get("id")) for a in g.get("atacado_por") or [] if isinstance(a, dict)]
        # frontier.py writes each gap cell as {celda, cubierta_por, marca}; a hand-written
        # mapa.json may still list plain "M1:fila|col" strings.
        gap_cells = [str(c.get("celda")) if isinstance(c, dict) else str(c) for c in g.get("celdas") or []]
        by_notes = sorted({hid for cid in gap_cells for hid in attacked.get(cid, [])})
        if by_map or by_notes:
            continue
        pr = g.get("prioridad")
        out.append(item("hueco_sin_hipotesis", "menor", gid,
                        f"El hueco {gid}{f' (prioridad {pr})' if pr not in (None, '') else ''} "
                        f"«{g.get('titulo') or ''}» no tiene ninguna hipótesis que lo ataque.",
                        f"hypothesis-cycle con el hueco {gid} como entrada (gap-derived desde la frontera).",
                        gap_cells))
    return out


# ---------------------------------------------------------------------------

def run(vault: Path, project: str) -> dict:
    pdir, pid = ps.resolve_project(vault, project)
    hyps, sh = ps.hypotheses(pdir)
    exps, se = ps.experiments(pdir)
    items = unadjudicated(hyps, exps) + hub_placeholders(pdir) + frontier(vault, pdir, pid, hyps) + worth(hyps)
    items.sort(key=lambda i: SEVERITY_ORDER.get(i["severity"], 9))
    counts = {s: sum(1 for i in items if i["severity"] == s) for s in SEVERITY_ORDER}
    summary = [f"Siguientes pasos de {pid} ({pdir.name}): {len(items)} pendiente(s)"
               + (f" — {', '.join(f'{n} {s}' for s, n in counts.items() if n)}" if items else ".")]
    for i in items:
        summary.append(f"  [{i['severity']}] {i['message']}\n      → {i['action']}")
    if sh or se:
        summary.append(f"  ({sh + se} nota(s) send: never omitidas)")
    return {"tool": TOOL, "project": pid, "slug": pdir.name,
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "has_frontier": ps.frontier_paths(pdir)["json"].is_file(),
            "counts": counts, "items": items, "omitidas_send_never": sh + se, "resumen": summary}


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--json", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        res = run(Path(a.vault), a.project)
    except ps.InputError as exc:
        print(f"next_steps: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(res, ensure_ascii=False, indent=2) if a.json else "\n".join(res["resumen"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
