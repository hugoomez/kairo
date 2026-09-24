#!/usr/bin/env python3
"""Evidence gate for update-confidence — exploratory experiments never count.

docs/v3-interfaces.md §1d: experiments carry ``role: confirmatory | exploratory``
and ``rung: 0..3``; ``update-confidence`` never moves a hypothesis's status on an
exploratory experiment. This script makes that rule mechanical: update-confidence
calls it BEFORE any trigger that carries an experiment, and takes the evidence
set for ``combine_effects.py`` ONLY from ``gather``'s ``eligible`` list.

    evidence_gate.py check  --experiment <E-XXXX.md> [--json]
        exit 0  confirmatory (or role absent = v2 note, read as confirmatory)
        exit 3  refused: exploratory, or malformed role / rung
    evidence_gate.py gather --hypothesis <H-XXXX.md> [--json]
        -> {"eligible": [...], "excluded": [{"id", "reason"}]}
        eligible = in linked_experiment AND primary hypothesis == this one
                   AND experiment_validity: valid AND role != exploratory
        exit 0 always when the note is readable (1 on error)

Rungs (B1): 0 = toy / smoke — minutes on CPU, checks the instrument (does the
control reproduce at all?); 1 = reduced scale (smaller n / modulus / model,
shorter training) — checks the direction of the effect; 2 = near-full pilot at
reduced power; 3 = the claim's own conditions (the confirmatory design).
Exploratory rungs are 0-2; a confirmatory experiment is rung 3 by definition —
a smaller-scale confirmatory test means the claim itself is scoped to that
scale. Absent ``rung`` = unknown (v2 notes) and is accepted.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ledger"))
from notes import as_list, read_note  # noqa: E402

__version__ = "1.0.0"
TOOL = f"kairo/evidence_gate@{__version__}"


def classify(fm: dict) -> tuple[bool, str]:
    """(counts_as_evidence, reason). Pure function of the experiment frontmatter."""
    role = str(fm.get("role", "")).strip()
    rung = str(fm.get("rung", "")).strip()
    if role.startswith("<"):   # unfilled template placeholder
        role = ""
    if rung.startswith("<"):
        rung = ""
    if role not in ("", "confirmatory", "exploratory"):
        return False, f"crítico: role '{role}' fuera del contrato (confirmatory | exploratory)"
    if rung and rung not in ("0", "1", "2", "3"):
        return False, f"crítico: rung '{rung}' fuera del contrato (0 | 1 | 2 | 3)"
    if role == "exploratory":
        return False, (f"exploratory (rung {rung or '?'}): informa el diseño, nunca cuenta "
                       "como evidencia — ninguna transición de status")
    if role == "confirmatory" and rung and rung != "3":
        return False, (f"crítico: confirmatory con rung {rung}; un experimento confirmatorio es "
                       "rung 3 por definición (si el claim está acotado a esa escala, esa escala "
                       "es su rung 3) — corregir con una enmienda antes de contarlo")
    return True, "confirmatory" if role else "role ausente (nota v2) → confirmatory"


def _find_experiment(eid: str, hyp_path: Path) -> Path | None:
    proj = hyp_path.resolve().parent.parent
    cand = proj / "Experimentos" / f"{eid}.md"
    if cand.exists():
        return cand
    vault = proj.parent.parent
    for p in vault.glob("Projects/*/Experimentos/*.md"):
        if p.stem == eid or p.name.startswith(eid + " "):
            return p
    return None


def cmd_check(path: Path) -> tuple[int, dict]:
    parsed = read_note(path)
    if parsed is None:
        return 1, {"error": f"cannot read {path}"}
    fm, _ = parsed
    ok, reason = classify(fm)
    return (0 if ok else 3), {"tool": TOOL, "experiment": fm.get("id", path.stem),
                              "counts_as_evidence": ok, "reason": reason}


def cmd_gather(path: Path) -> tuple[int, dict]:
    parsed = read_note(path)
    if parsed is None:
        return 1, {"error": f"cannot read {path}"}
    fm, _ = parsed
    hid = str(fm.get("id", ""))
    linked = as_list(fm.get("linked_experiment"))
    eligible, excluded = [], []
    seen = set()
    for eid in linked:
        seen.add(eid)
        ep = _find_experiment(eid, path)
        if ep is None:
            excluded.append({"id": eid, "reason": "crítico: nota de experimento no encontrada"})
            continue
        efm, _ = read_note(ep) or ({}, "")
        primary = as_list(efm.get("hypothesis"))
        if primary != [hid]:
            excluded.append({"id": eid, "reason": f"hipótesis primaria es {primary or '—'}, no {hid}"})
            continue
        ok, reason = classify(efm)
        if not ok:
            excluded.append({"id": eid, "reason": reason})
            continue
        validity = str(efm.get("experiment_validity", "")).strip()
        if validity != "valid":
            excluded.append({"id": eid, "reason": f"experiment_validity: {validity or 'sin valor'}"})
            continue
        eligible.append({"id": eid, "path": ep.as_posix(), "role_reason": reason})
    # exploratory rungs about this hypothesis are listed (never hidden) and excluded
    proj = path.resolve().parent.parent
    for ep in sorted((proj / "Experimentos").glob("*.md")) if (proj / "Experimentos").is_dir() else []:
        efm, _ = read_note(ep) or ({}, "")
        eid = str(efm.get("id", ep.stem))
        if eid in seen or as_list(efm.get("hypothesis")) != [hid]:
            continue
        ok, reason = classify(efm)
        if not ok:
            excluded.append({"id": eid, "reason": reason + " (no enlazado en linked_experiment)"})
    return 0, {"tool": TOOL, "hypothesis": hid, "eligible": eligible, "excluded": excluded}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Exploratory experiments never count as evidence.")
    ap.add_argument("--version", action="version", version=TOOL)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--experiment", type=Path, required=True)
    c.add_argument("--json", action="store_true")
    g = sub.add_parser("gather")
    g.add_argument("--hypothesis", type=Path, required=True)
    g.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    code, res = cmd_check(a.experiment) if a.cmd == "check" else cmd_gather(a.hypothesis)
    if a.json or code == 1:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif a.cmd == "check":
        print(f"{'ALLOWED' if code == 0 else 'REFUSED'} {res['experiment']}: {res['reason']}")
    else:
        print(f"{res['hypothesis']}: eligible {[e['id'] for e in res['eligible']]}")
        for e in res["excluded"]:
            print(f"  excluded {e['id']}: {e['reason']}")
    return code


if __name__ == "__main__":
    sys.exit(main())
