#!/usr/bin/env python3
"""The daily brief of a study project (kairo:study): one screen, counts only.

    study_brief.py --vault V --project P [--json] [--top N] [--write-bitacora] [--by WHO]
                   [--collections-dir DIR]
    study_brief.py --vault V --project P --watch REC-ID [--interval S] [--max-polls N]

Read-only except --write-bitacora. In order: the deadline countdown (hub `deadline`),
each question and pilot with its state, plan and gate summary (the gate is run only
for a node whose state leads to its design's publishable one), the app's collections
of the project, annotation progress, notice counts, and the top next steps
(next_steps.py, which computes everything above once).

What it reads, and what it never reads:
- annotation: per `Datos/anotacion/<sample>/`, the number of lines of muestra.jsonl
  and, per `etiquetas-*.jsonl`, the number of distinct item ids labelled (only the
  `id` of each line; the labels are never looked at, annotators are not named),
  the lines of referencia.jsonl and the number of validacion-*.json files;
- notices: counts by state through study_gates.count_avisos, never a notice;
- collections: state.json fields and progress.json numbers (next_steps.py); never
  log.txt, argv or the collected data.

--write-bitacora appends one `study_brief` entry (counts only) to
`Projects/<slug>/Bitacora/acciones.jsonl` with scripts/bitacora/action_log.py and
re-renders that day's page, like the commit and session hooks. It commits nothing.

--watch polls one collection of the project every --interval seconds (default 600)
and prints one line when its state, attempts or progress numbers change; it exits
when the collection is no longer `en_marcha` (exit 0; the last line starts with
`FIN`), when it disappears (exit 3), or after --max-polls polls (exit 0). Meant for
a Claude Code Monitor or a background shell; each line holds only the allowed fields.

Exit codes: 0 ok · 2 bad input · 3 collection not found (--watch). Standard library only.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "bitacora"))
import next_steps as ns  # noqa: E402
import project_scan as ps  # noqa: E402

TOOL = "kairo/study_brief@1.0.0"


# ---------------------------------------------------------------------------
# Annotation progress — counts only
# ---------------------------------------------------------------------------

def _lines(path: Path) -> int:
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            return sum(1 for ln in fh if ln.strip())
    except OSError:
        return 0


def _labelled(path: Path) -> int:
    """Distinct item ids in an etiquetas-*.jsonl (the last line per id wins; ids only)."""
    ids = set()
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            for ln in fh:
                if not ln.strip():
                    continue
                try:
                    obj = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(obj, dict) and "id" in obj:
                    ids.add(json.dumps(obj["id"], sort_keys=True))
    except OSError:
        return 0
    return len(ids)


def annotation(pdir: Path) -> list[dict]:
    root = pdir / "Datos" / "anotacion"
    if not root.is_dir():
        return []
    out = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if not (d / "muestra.jsonl").is_file():
            continue
        ref = d / "referencia.jsonl"
        out.append({
            "muestra": d.name,
            "items": _lines(d / "muestra.jsonl"),
            "etiquetados": [_labelled(f) for f in sorted(d.glob("etiquetas-*.jsonl"))],
            "referencia": _lines(ref) if ref.is_file() else None,
            "validaciones": len(list(d.glob("validacion-*.json"))),
        })
    return out


# ---------------------------------------------------------------------------
# The brief
# ---------------------------------------------------------------------------

def _gate_summary(g: dict | None) -> dict | None:
    if g is None:
        return None
    checks = g.get("checks") or []
    return {"ok": bool(g.get("ok")), "pasan": sum(1 for c in checks if c["ok"]), "total": len(checks),
            "fallan": [c["name"] for c in checks if not c["ok"]], "faltan": len(g.get("missing") or [])}


def brief(vault: Path, project: str, today: date | None = None, collections: Path | str | None = None,
          top: int = 8) -> dict:
    today = today or date.today()
    res, ctx = ns.analyse(Path(vault), project, today, collections)
    pdir = ctx["pdir"]
    nodes = [{
        "id": n["id"], "diseno": n["design_name"], "piloto": n["pilot"], "status": n["status"],
        "terminal": n["terminal"], "linea_publicacion": n["linea_publicacion"],
        "plan": n["plan_id"] or None, "plan_congelado": n["plan_frozen"] if n["plan_id"] else None,
        "units_dataset": bool(n["units_dataset"]), "resultado": n["has_result"],
        "puerta": _gate_summary(ctx["gates"].get(n["id"])),
    } for n in ctx["nodes"]]
    av = ctx["avisos"]
    # the study's own steps first within each severity (the sort is stable)
    steps = sorted(res["items"], key=lambda i: (ns.SEVERITY_ORDER.get(i["severity"], 9),
                                                 i["kind"] not in ns.STUDY_KINDS))
    return {
        "tool": TOOL, "project": res["project"], "slug": res["slug"], "fecha": today.isoformat(),
        "plazo": ctx["deadline"], "nodos": nodes, "recolecciones": ctx["collections"],
        "anotacion": annotation(pdir),
        "avisos": ({"total": av["total"], "abiertos": av["total"] - av["cerrados"],
                    **{k: av[k] for k in ("pendiente", "sin_fecha", "embargo", "desconocido", "cerrados")}}
                   if av else {"problema": ctx["avisos_problem"]}),
        "counts": res["counts"], "pendientes": len(res["items"]), "siguientes": steps[:top],
    }


def _plazo_line(dl: dict | None) -> str:
    if dl is None:
        return "Plazo: sin deadline en el hub."
    if dl["date"] is None:
        return f"Plazo: `deadline` ilegible ({dl['raw']})."
    d = dl["days_left"]
    if d < 0:
        return f"Plazo: {dl['date']} — venció hace {-d} día(s)."
    return f"Plazo: {dl['date']} — quedan {d} día(s)."


def text(b: dict) -> str:
    out = [f"Estudio de {b['project']} ({b['slug']}) — {b['fecha']}", _plazo_line(b["plazo"]), ""]
    out.append("Preguntas y pilotos:")
    if not b["nodos"]:
        out.append("  (ninguno)")
    for n in b["nodos"]:
        bits = [f"{n['id']:<8}", n["status"] or "?"]
        if n["linea_publicacion"]:
            bits.append("publicación")
        if n["plan"]:
            bits.append(f"plan {n['plan']}" + ("" if n["piloto"] else (" congelado" if n["plan_congelado"] else " borrador")))
        else:
            bits.append("sin plan")
        if n["resultado"]:
            bits.append("con resultado")
        g = n["puerta"]
        if g:
            bits.append("puerta: se cumple" if g["ok"] else
                        f"puerta: {g['pasan']}/{g['total']} (fallan: {', '.join(g['fallan']) or '—'})")
        out.append("  " + " · ".join(bits))
    out += ["", "Recolecciones:"]
    if not b["recolecciones"]:
        out.append("  (ninguna de este proyecto)")
    for c in b["recolecciones"]:
        line = f"  {c['id']} · {c.get('state')} · intentos {c.get('attempts') or 0} · desde {c.get('startedAt') or '?'}"
        if c.get("endedAt"):
            line += f" · fin {c['endedAt']}"
        if c.get("exitCode") is not None:
            line += f" · código {c['exitCode']}"
        if c["progress"]:
            line += f" · progreso: {ns.progress_text(c['progress'])}"
        out.append(line)
    out += ["", "Anotación:"]
    if not b["anotacion"]:
        out.append("  (sin muestras)")
    for s in b["anotacion"]:
        lab = ", ".join(str(x) for x in s["etiquetados"]) or "0"
        line = (f"  {s['muestra']}: {s['items']} ítem(s); etiquetados {lab} "
                f"({len(s['etiquetados'])} anotador(es))")
        if s["referencia"] is not None:
            line += f"; referencia {s['referencia']}"
        line += f"; {s['validaciones']} validación(es)"
        out.append(line)
    av = b["avisos"]
    if "problema" in av:
        out += ["", f"Avisos: {av['problema']}."]
    else:
        parts = [f"{av[k]} {lbl}" for k, lbl in (("pendiente", "pendiente(s)"), ("sin_fecha", "enviado(s) sin fecha"),
                                                  ("embargo", "en embargo"), ("desconocido", "desconocido(s)"))
                 if av[k]]
        out += ["", f"Avisos: {av['total']} — {av['abiertos']} abierto(s)"
                + (f" ({', '.join(parts)})" if parts else "") + f", {av['cerrados']} cerrado(s)."]
    counts = ", ".join(f"{n} {s}" for s, n in b["counts"].items() if n)
    out += ["", f"Siguientes pasos ({len(b['siguientes'])} de {b['pendientes']}{'; ' + counts if counts else ''}):"]
    if not b["siguientes"]:
        out.append("  (nada pendiente)")
    for i in b["siguientes"]:
        out.append(f"  [{i['severity']}] {i['message']}\n      → {i['action']}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Bitácora
# ---------------------------------------------------------------------------

def bitacora_summary(b: dict) -> str:
    q = sum(1 for n in b["nodos"] if not n["piloto"])
    p = len(b["nodos"]) - q
    running = sum(1 for c in b["recolecciones"] if c.get("state") == "en_marcha")
    failed = sum(1 for c in b["recolecciones"] if c.get("state") in ns.FAILED_COLLECTIONS)
    av = b["avisos"]
    bits = [f"{q} pregunta(s) y {p} piloto(s)",
            f"{running} recolección(es) en marcha" + (f", {failed} fallida(s)" if failed else ""),
            (f"{av['abiertos']} aviso(s) abierto(s)" if "abiertos" in av else "avisos: sin registro"),
            f"{b['pendientes']} pendiente(s)"
            + (" (" + ", ".join(f"{n} {s}" for s, n in b["counts"].items() if n) + ")" if b["pendientes"] else "")]
    dl = b["plazo"]
    if dl and dl.get("days_left") is not None:
        bits.append(f"plazo en {dl['days_left']} día(s)")
    return "Parte del estudio: " + "; ".join(bits) + "."


def write_bitacora(vault: Path, b: dict, by: str) -> dict:
    import action_log  # noqa: PLC0415  (scripts/bitacora, on sys.path above)

    log = Path(vault) / "Projects" / b["slug"] / "Bitacora" / "acciones.jsonl"
    written = action_log.append(log, {
        "actor": "agent", "by": by, "action": "study_brief", "event": "completed",
        "summary": bitacora_summary(b), "refs": [n["id"] for n in b["nodos"]],
    })
    page = log.parent / f"{written['date']}.md"
    page.write_text(action_log.render(action_log.load(log), written["date"], page), encoding="utf-8", newline="\n")
    return written


# ---------------------------------------------------------------------------
# Watch one collection
# ---------------------------------------------------------------------------

def _watch_line(c: dict) -> str:
    bits = [c["id"], str(c.get("state")), f"intentos {c.get('attempts') or 0}"]
    if c.get("exitCode") is not None:
        bits.append(f"código {c['exitCode']}")
    if c["progress"]:
        bits.append(f"progreso: {ns.progress_text(c['progress'])}")
    return " · ".join(bits)


def watch(pid: str, slug: str, rec: str, collections: Path | str | None, interval: float, max_polls: int,
          out=None, sleep=time.sleep) -> int:
    out = out or sys.stdout
    last = None
    polls = 0
    while True:
        polls += 1
        c = next((x for x in ns.project_collections(pid, slug, collections) if x["id"] == rec), None)
        if c is None:
            print(f"FIN {rec}: no hay ninguna recolección con ese id en este proyecto", file=out, flush=True)
            return 3
        key = (c.get("state"), c.get("attempts"), c.get("exitCode"), tuple(sorted(c["progress"].items())))
        done = c.get("state") != "en_marcha"
        if done:
            print(f"FIN {_watch_line(c)}", file=out, flush=True)
            return 0
        if key != last:
            print(_watch_line(c), file=out, flush=True)
            last = key
        if max_polls and polls >= max_polls:
            return 0
        sleep(interval)


# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--top", type=int, default=8, help="how many next steps to show (default 8)")
    ap.add_argument("--write-bitacora", action="store_true")
    ap.add_argument("--by", default="claude-code", help="who the bitácora entry names (default claude-code)")
    ap.add_argument("--collections-dir", default=None)
    ap.add_argument("--watch", metavar="REC_ID", default=None)
    ap.add_argument("--interval", type=float, default=600.0)
    ap.add_argument("--max-polls", type=int, default=0, help="0 = until the collection stops")
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        if a.watch:
            pdir, pid = ps.resolve_project(Path(a.vault), a.project)
            return watch(pid, pdir.name, a.watch, a.collections_dir, max(a.interval, 1.0), a.max_polls)
        b = brief(Path(a.vault), a.project, collections=a.collections_dir, top=max(a.top, 0))
    except ps.InputError as exc:
        print(f"study_brief: {exc}", file=sys.stderr)
        return 2
    if a.write_bitacora:
        try:
            b["bitacora"] = write_bitacora(Path(a.vault), b, a.by)["seq"]
        except Exception as exc:  # noqa: BLE001  the brief still prints; the failure is said
            print(f"study_brief: no se pudo escribir en la bitácora ({type(exc).__name__}: {exc})", file=sys.stderr)
            b["bitacora"] = None
    print(json.dumps(b, ensure_ascii=False, indent=2, default=str) if a.json else text(b))
    return 0


if __name__ == "__main__":
    sys.exit(main())
