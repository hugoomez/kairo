#!/usr/bin/env python3
"""The single writer of ``Claims/`` note status.

Hypothesis ``status`` belongs to ``update-confidence``; claim ``status``
(``pendiente | probado | fallido | refutado``) belongs to this script and to
nothing else — not a skill, not a hand edit. Two subcommands:

    claim_status.py new --project-dir <vault>/Projects/<slug> --kind <kind> \
        --statement "<one sentence>" --by <who> [--about H-XXXX [H-YYYY ...]] [--source E-XXXX] \
        [--depends-on ID ...] [--role exploratory --rung 0..2] [--how "..."]
        -> creates Claims/C-XXXX.md (next vault-wide id) at `pendiente`

    claim_status.py set --note <path> --status <probado|fallido|refutado> \
        --by <who> --evidence "<one line>" [--date YYYY-MM-DD]
        -> validates the transition, sets status/updated, appends history

Transitions (anything else is refused, exit 3):

    pendiente -> probado | fallido | refutado
    probado   -> refutado        (later evidence overturns an established node)

``fallido`` and ``refutado`` are terminal: pursue the question with a new claim.
A rung claim (``kind: rung``) must carry ``role: exploratory`` and a ``rung``
of 0-2 — rungs never count as evidence (docs/v3-interfaces.md §1d).

``--about`` takes one or more hypothesis/claim ids (a rung shared by two
hypotheses' designs links to both). One id is written as a scalar
(``about: H-0001``), several as an inline list (``about: [H-0006, H-0001]``);
build_graph reads either form.

Exit codes: 0 ok, 1 error (bad path / args), 3 refused transition or invalid
input. Standard library only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notes import (append_block_entry, join_note, newline_of,  # noqa: E402
                   parse_frontmatter, set_scalar, split_note)

__version__ = "1.1.0"
TOOL = f"kairo/claim_status@{__version__}"

STATUSES = ("pendiente", "probado", "fallido", "refutado")
KINDS = ("lema", "teorema", "resultado_intermedio", "rung", "linaje")
PROOF_KINDS = ("lema", "teorema")   # probado only through claim_gate.py
TRANSITIONS = {"pendiente": {"probado", "fallido", "refutado"},
               "probado": {"refutado"},
               "fallido": set(), "refutado": set()}
_ID_RE = re.compile(r"^[HCE]-\d{4}$")
_SOURCE_RE = re.compile(r"^(?:E|EVO)-\d{4}$")   # an experiment, or an evolve-program run


class Refused(Exception):
    pass


def _yaml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)  # JSON string == valid YAML double-quoted scalar


def _vault_root(project_dir: Path) -> Path:
    if project_dir.parent.name != "Projects":
        raise Refused(f"--project-dir must be <vault>/Projects/<slug>, got {project_dir}")
    return project_dir.parent.parent


def next_claim_id(vault: Path) -> str:
    top = 0
    for p in vault.glob("Projects/*/Claims/*.md"):
        m = re.match(r"C-(\d{4})", p.name)
        if m:
            top = max(top, int(m.group(1)))
        try:
            head = p.read_text(encoding="utf-8-sig")[:400]
        except OSError:
            continue
        m2 = re.search(r"^id:\s*C-(\d{4})", head, re.MULTILINE)
        if m2:
            top = max(top, int(m2.group(1)))
    return f"C-{top + 1:04d}"


def cmd_new(a) -> Path:
    project_dir = a.project_dir.resolve()
    vault = _vault_root(project_dir)
    if a.kind not in KINDS:
        raise Refused(f"--kind must be one of {KINDS}")
    about = list(dict.fromkeys(a.about or []))   # dedup, keep order
    for ref in [*(a.depends_on or []), *about]:
        if not _ID_RE.match(ref):
            raise Refused(f"not a Kairo id: {ref}")
    if a.source and not _SOURCE_RE.match(a.source):
        raise Refused(f"--source must be E-XXXX or EVO-XXXX, got {a.source}")
    if a.kind == "rung":
        if a.role != "exploratory":
            raise Refused("a rung claim must be --role exploratory (rungs never count as evidence)")
        if a.rung not in ("0", "1", "2"):
            raise Refused("a rung claim needs --rung 0, 1 or 2 (3 is the confirmatory design)")
        if not (about and a.source):
            raise Refused("a rung claim needs --about H-XXXX and --source E-XXXX")
    hub_id = ""
    hub = project_dir / "_hub.md"
    if hub.exists():
        parts = split_note(hub.read_text(encoding="utf-8"))
        if parts:
            hub_id = str(parse_frontmatter(parts[0]).get("id", ""))
    today = a.date or dt.date.today().isoformat()
    fm = [
        "id: {cid}",
        f"project: {hub_id or project_dir.name}",
        f"kind: {a.kind}",
        "status: pendiente",
        f"created: {today}",
        f"updated: {today}",
    ]
    if len(about) == 1:
        fm.append(f"about: {about[0]}")
    elif about:
        fm.append(f"about: [{', '.join(about)}]")
    if a.source:
        fm.append(f"source: {a.source}")
    fm.append(f"depends_on: [{', '.join(a.depends_on or [])}]")
    if a.role:
        fm.append(f"role: {a.role}")
    if a.rung is not None:
        fm.append(f"rung: {a.rung}")
    fm += ["history:",
           f"  - date: {today}",
           "    status: pendiente",
           f"    by: {_yaml_str(a.by)}",
           f"    evidence: {_yaml_str('creado por ' + TOOL)}"]
    if a.kind in PROOF_KINDS:
        body = (f"\n## Enunciado\n\n{a.statement.strip()}\n\n"
                f"## Demostración\n\n{(a.how or '<pendiente>').strip()}\n\n"
                "## Comprobación numérica\n\n<Qué casos pequeños prueba Claims/checks/"
                "C-XXXX.py, o por qué no es factible comprobarlo computacionalmente.>\n\n"
                "## Qué informa\n\n<Qué notas lo usan (ver su depends_on).>\n")
    else:
        body = (f"\n## Enunciado\n\n{a.statement.strip()}\n\n"
                f"## Cómo se establece\n\n{(a.how or '<pendiente>').strip()}\n\n"
                "## Resultado\n\n<pendiente>\n\n## Qué informa\n\n<pendiente>\n")
    claims = project_dir / "Claims"
    claims.mkdir(exist_ok=True)
    # Allocate the id with an exclusive create: parallel `new` calls (independent
    # ladder rungs are frozen together) must never get the same C-XXXX.
    for _ in range(50):
        cid = next_claim_id(vault)
        target = claims / f"{cid}.md"
        try:
            with open(target, "x", encoding="utf-8", newline="\n") as fh:
                fh.write(join_note([fm[0].format(cid=cid), *fm[1:]], body))
            return target
        except FileExistsError:
            continue
    raise Refused("could not allocate a claim id after 50 attempts")


def cmd_set(a) -> Path:
    note = a.note.resolve()
    if note.parent.name != "Claims":
        raise Refused(f"{note} is not inside a Claims/ folder — hypothesis status "
                      "belongs to update-confidence, not this script")
    raw = note.read_bytes().decode("utf-8-sig")   # bytes: read_text would hide CRLF
    parts = split_note(raw)
    if parts is None:
        raise Refused(f"{note} has no frontmatter")
    fm_lines, body = parts
    fm = parse_frontmatter(fm_lines)
    cur = str(fm.get("status", ""))
    if cur not in STATUSES:
        raise Refused(f"current status '{cur}' is not a claim status")
    if a.status not in TRANSITIONS[cur]:
        raise Refused(f"transition {cur} -> {a.status} not allowed "
                      f"(allowed from {cur}: {sorted(TRANSITIONS[cur]) or 'none, terminal'})")
    if not a.evidence.strip():
        raise Refused("--evidence is required")
    if a.status == "probado" and str(fm.get("kind", "")) in PROOF_KINDS:
        # the three-layer rigor gate: verifier, the researcher's sign-offs,
        # numerical check (+ dependencies) — all on the current text
        from claim_gate import gate
        vault = note.parent.parent.parent.parent
        g = gate(str(vault), note)
        if not g["ok"]:
            raise Refused("la puerta de rigor no está abierta: " + " · ".join(g["missing"]))
    today = a.date or dt.date.today().isoformat()
    fm_lines = set_scalar(fm_lines, "status", a.status)
    fm_lines = set_scalar(fm_lines, "updated", today)
    fm_lines = append_block_entry(fm_lines, "history", [
        f"  - date: {today}",
        f"    status: {a.status}",
        f"    by: {_yaml_str(a.by)}",
        f"    evidence: {_yaml_str(a.evidence.strip())}",
    ])
    note.write_bytes(join_note(fm_lines, body, newline_of(raw)).encode("utf-8"))
    return note


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Single writer of Claims/ status.")
    ap.add_argument("--version", action="version", version=TOOL)
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new", help="create a claim at pendiente")
    n.add_argument("--project-dir", type=Path, required=True)
    n.add_argument("--kind", required=True, choices=KINDS)
    n.add_argument("--statement", required=True)
    n.add_argument("--by", required=True)
    n.add_argument("--about", nargs="+", action="extend",
                   help="H-XXXX / C-XXXX this claim is about; repeatable or space-separated")
    n.add_argument("--source")
    n.add_argument("--depends-on", nargs="*", default=[])
    n.add_argument("--role", choices=("exploratory", "confirmatory"))
    n.add_argument("--rung", choices=("0", "1", "2", "3"))
    n.add_argument("--how")
    n.add_argument("--date")
    s = sub.add_parser("set", help="change a claim's status")
    s.add_argument("--note", type=Path, required=True)
    s.add_argument("--status", required=True, choices=("probado", "fallido", "refutado"))
    s.add_argument("--by", required=True)
    s.add_argument("--evidence", required=True)
    s.add_argument("--date")
    a = ap.parse_args(argv)
    try:
        path = cmd_new(a) if a.cmd == "new" else cmd_set(a)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
