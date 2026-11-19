#!/usr/bin/env python3
"""The only code that changes the `status` of a study node (a question, Q-XXXX).

    study_status.py propose --vault V --question Q-XXXX
    study_status.py apply   --vault V --question Q-XXXX --to <status> --by <who> --evidence "<why>"
    study_status.py discard --vault V --question Q-XXXX --reason "<why>" --by <name>
    study_status.py gate    --vault V --question Q-XXXX

The states, the allowed (from, to) pairs, the researcher-only pairs, the publishable state
and its gate checks all come from the node's design in `config/designs.toml`
(`designs.design_for_id`). Nothing here knows a design by name.

`propose` (read-only, JSON) lists the edges open from the current state; the edge to the
`publishable` state carries the gate result, and is proposed (`to`) only when the gate passes.

`apply` writes one edge, and refuses any pair the design does not declare. A researcher-only
pair is refused inside an agent session (`CLAUDECODE` / `KAIRO_AGENT_SESSION`), like
`discard`; outside one the researcher may apply it, except the edge to `descartada`, which
always goes through `discard` (it needs a reason). The edge to `publishable` additionally needs
`gate(...)["ok"]`. It sets `status` and `updated` and appends one `history` entry; nothing
else in the note changes.

`discard` is the researcher's edge to `descartada`. It refuses to run inside an agent session
(`CLAUDECODE` / `KAIRO_AGENT_SESSION`), needs a reason, and only follows a declared
researcher-only pair.

`gate(vault, nid) -> {"applies", "ok", "missing"}`: the design's checks (study_gates.GATES)
on the node. `applies` is False for an id no design claims (H-, C-, …).

Exit codes: 0 ok · 3 refused · 1 error. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import designs as _designs  # noqa: E402
from study_gates import GATES, find_node  # noqa: E402
from notes import read_note  # noqa: E402
from verifications import (  # noqa: E402
    InputError,
    _set_scalar,
    block_end,
    find_key,
    fm_value,
    load,
    write_raw,
    yaml_scalar,
)

TOOL = "kairo/study_status@1.0.0"
AGENT_MARKERS = ("CLAUDECODE", "KAIRO_AGENT_SESSION")
DISCARDED = "descartada"
_ID = re.compile(r"^[A-Z]+-\d{4}$")


class Refused(Exception):
    pass


def _locate(vault: Path, nid: str) -> tuple[dict, Path]:
    if not _ID.match(nid or ""):
        raise Refused("la pregunta debe tener la forma Q-XXXX")
    design = _designs.design_for_id(_designs.load(), nid)
    if design is None:
        raise Refused(f"{nid}: ningún diseño de config/designs.toml usa el prefijo {nid.split('-')[0]}")
    path = find_node(vault, nid, design)
    if path is None:
        raise Refused(f"{nid}: no hay exactamente una nota en Projects/*/{design['folder']}/")
    return design, path


def gate(vault: Path, nid: str) -> dict:
    vault = Path(vault)
    design = _designs.design_for_id(_designs.load(), nid)
    if design is None:
        return {"applies": False, "ok": True, "missing": []}
    path = find_node(vault, nid, design)
    if path is None:
        return {"applies": True, "ok": False, "missing": [f"{nid}: no encontrada en Projects/*/{design['folder']}/"]}
    parsed = read_note(path)
    fm = parsed[0] if parsed else {}
    missing: list[str] = []
    for name in design["gate"]:
        check = GATES.get(name)
        if check is None:
            missing.append(f"{name}: comprobación inexistente (corre designs.py check)")
            continue
        try:
            missing += check(vault, path, fm)
        except Exception as exc:  # noqa: BLE001  a broken check fails the gate, never passes it
            missing.append(f"{name}: error al comprobar ({type(exc).__name__}: {exc})")
    return {"applies": True, "ok": not missing, "missing": missing}


def _status(path: Path) -> str:
    _, lines, _, lo, hi, _ = load(str(path))
    return (fm_value(lines, lo + 1, hi, "status") or "").strip()


def propose(vault: Path, nid: str) -> dict:
    design, path = _locate(vault, nid)
    status = _status(path)
    out: dict = {"tool": TOOL, "question": nid, "from": status, "to": None, "options": [], "reason": ""}
    ro = set(design["researcher_only"])
    for a, b in design["transitions"]:
        if a != status:
            continue
        opt: dict = {"to": b, "terminal": b in design["terminal"], "researcher_only": (a, b) in ro}
        if b == design["publishable"]:
            opt["gate"] = gate(vault, nid)
        out["options"].append(opt)
    pub = next((o for o in out["options"] if "gate" in o), None)
    if pub and pub["gate"]["ok"]:
        out.update(to=pub["to"], reason=f"la puerta de {pub['to']} se cumple")
    elif pub:
        out["reason"] = f"la puerta de {pub['to']} no se cumple: " + "; ".join(pub["gate"]["missing"])
    elif not out["options"]:
        out["reason"] = f"«{status}» no tiene transiciones de salida"
    else:
        out["reason"] = "ningún cambio se decide solo con evidencia desde aquí; lo decide el investigador"
    return out


def _append_history(lines: list[str], nl: str, fields: list[tuple[str, str]]) -> None:
    hi = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    k = find_key(lines, 1, hi, "history")
    entry = [f"  - {fields[0][0]}: {yaml_scalar(fields[0][1])}{nl}"]
    entry += [f"    {n}: {yaml_scalar(v)}{nl}" for n, v in fields[1:]]
    if k is None:
        lines[hi:hi] = [f"history:{nl}", *entry]
        return
    if lines[k].rstrip("\r\n").split(":", 1)[1].split("#", 1)[0].strip() == "[]":
        lines[k] = f"history:{nl}"
    end = block_end(lines, k, hi)
    lines[end:end] = entry


def _write(path: Path, to: str, by: str, evidence: str) -> None:
    _, lines, nl, _, _, _ = load(str(path))
    today = date.today().isoformat()
    _set_scalar(lines, "status", to, nl)
    _set_scalar(lines, "updated", today, nl)
    _append_history(lines, nl, [("date", today), ("status", to), ("by", by), ("evidence", evidence)])
    write_raw(str(path), "".join(lines))


def apply(vault: Path, nid: str, to: str, by: str, evidence: str) -> dict:
    design, path = _locate(vault, nid)
    status = _status(path)
    if (status, to) not in set(design["transitions"]):
        raise Refused(f"«{status} → {to}» no es una transición del diseño de {nid}")
    if (status, to) in set(design["researcher_only"]):
        if to == DISCARDED:
            raise Refused(f"«{status} → {to}» es decisión del investigador: usa discard")
        for m in AGENT_MARKERS:
            if os.environ.get(m):
                raise Refused(f"«{status} → {to}» es decisión del investigador: rechazado dentro de una "
                              f"sesión de agente ({m}); la hace el investigador desde la interfaz")
    evidence = " ".join(evidence.split())
    if not evidence:
        raise Refused("--evidence es obligatorio")
    if not by.strip():
        raise Refused("--by es obligatorio")
    if to == design["publishable"]:
        g = gate(vault, nid)
        if not g["ok"]:
            raise Refused(f"{to} exige pasar la puerta; falta: " + "; ".join(g["missing"]))
    _write(path, to, by.strip(), evidence)
    return {"tool": TOOL, "question": nid, "from": status, "to": to, "terminal": to in design["terminal"],
            "file": path.relative_to(vault).as_posix()}


def discard(vault: Path, nid: str, reason: str, by: str) -> dict:
    for m in AGENT_MARKERS:
        if os.environ.get(m):
            raise Refused(f"descartar es una decisión del investigador: rechazado dentro de una sesión de agente ({m})")
    reason = " ".join(reason.split())
    if len(reason) < 5:
        raise Refused("--reason es obligatorio (al menos 5 caracteres)")
    design, path = _locate(vault, nid)
    status = _status(path)
    if (status, DISCARDED) not in set(design["researcher_only"]):
        raise Refused(f"desde «{status}» no se puede descartar {nid}")
    _write(path, DISCARDED, by.strip() or "investigador", f"descartada por el investigador: {reason}")
    return {"tool": TOOL, "question": nid, "from": status, "to": DISCARDED,
            "terminal": DISCARDED in design["terminal"], "file": path.relative_to(vault).as_posix()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("propose", "apply", "discard", "gate"):
        p = sub.add_parser(name)
        p.add_argument("--vault", required=True, type=Path)
        p.add_argument("--question", required=True)
        if name == "apply":
            p.add_argument("--to", required=True)
            p.add_argument("--by", required=True)
            p.add_argument("--evidence", required=True)
        if name == "discard":
            p.add_argument("--reason", required=True)
            p.add_argument("--by", required=True)
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        vault = a.vault.resolve()
        if a.cmd == "propose":
            out = propose(vault, a.question)
        elif a.cmd == "apply":
            out = apply(vault, a.question, a.to, a.by, a.evidence)
        elif a.cmd == "discard":
            out = discard(vault, a.question, a.reason, a.by)
        else:
            _locate(vault, a.question)
            out = {"tool": TOOL, "question": a.question, **gate(vault, a.question)}
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    except (InputError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
