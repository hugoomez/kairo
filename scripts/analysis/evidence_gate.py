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
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ledger"))
from notes import as_list, parse_frontmatter, read_note, split_note  # noqa: E402

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
    if not role and rung in ("0", "1", "2"):
        # fail closed: a rung-0..2 note with its role line missing is a ladder rung
        return False, (f"crítico: rung {rung} sin `role` — un rung 0-2 es exploratorio; no se "
                       "lee como confirmatorio por omisión")
    if role == "confirmatory" and rung and rung != "3":
        return False, (f"crítico: confirmatory con rung {rung}; un experimento confirmatorio es "
                       "rung 3 por definición (si el claim está acotado a esa escala, esa escala "
                       "es su rung 3) — corregir con una enmienda antes de contarlo")
    return True, "confirmatory" if role else "role ausente (nota v2) → confirmatory"


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def frozen_check(path: Path) -> tuple[bool, str]:
    """role / rung are frozen with the preregistration: the version of the note in
    the commit that first added it (preregister-experiment's freeze commit) must
    classify the same way. A rung relabelled `confirmatory` after it ran is refused."""
    path = path.resolve()
    top = _git(["rev-parse", "--show-toplevel"], path.parent)
    if top.returncode != 0:
        return True, "sin repositorio git: la inmutabilidad de role/rung no se pudo verificar"
    root = Path(top.stdout.strip())
    rel = path.relative_to(root.resolve()).as_posix()
    log = _git(["log", "--diff-filter=A", "--format=%H", "--", rel], root)
    shas = log.stdout.split()
    if log.returncode != 0 or not shas:
        return False, ("crítico: el preregistro no está congelado en git (ningún commit lo "
                       "añade) — no puede contar como evidencia")
    shown = _git(["show", f"{shas[-1]}:{rel}"], root)
    parts = split_note(shown.stdout) if shown.returncode == 0 else None
    if parts is None:
        return False, f"crítico: no se pudo leer la versión congelada ({shas[-1][:10]})"
    ok, reason = classify(parse_frontmatter(parts[0]))
    if not ok:
        return False, (f"crítico: en su commit de freeze {shas[-1][:10]} la nota era: {reason} — "
                       "role/rung no se reetiquetan después del freeze")
    return True, f"role/rung coinciden con el freeze {shas[-1][:10]}"


def counts(path: Path, fm: dict) -> tuple[bool, str]:
    ok, reason = classify(fm)
    if not ok:
        return ok, reason
    fok, freason = frozen_check(path)
    return fok, (f"{reason}; {freason}" if fok else freason)


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
    ok, reason = counts(path, fm)
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
        ok, reason = counts(ep, efm)
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
