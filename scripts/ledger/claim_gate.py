#!/usr/bin/env python3
"""The rigor gate for lemmas and theorems (`kind: lema | teorema`).

A proof claim may become `probado` only when ALL of these hold for its
CURRENT text (every record is bound to a sha256, so an edit voids it):

1. **Fresh verifier on the proof** — the latest `scope: note` entry in
   `verifications:` is `no_errors_found`, and the packet it was given (its
   sha256, recorded in `## Verificación independiente`) is exactly the packet
   `verifier_packet.py` builds now: statement + proof + the statements of the
   dependencies. Editing the proof or a dependency's statement re-opens it.
2. **The researcher's sign-off on the statement AND on the proof** —
   `signoffs:` entries (written only by `signoff.py`, never from an agent
   session) whose sha256 matches the current `## Enunciado` / `## Demostración`.
3. **A numerical sanity check** — the latest `numerical_checks:` entry is
   `passed` for the current statement and the current check script
   (`Claims/checks/C-XXXX.py`); or it is `not_feasible` with a reason the
   researcher signed off (`part: infeasibility`, sha256 of the reason).
4. **Its dependencies stand** — every `depends_on` claim is `probado` and
   every `depends_on` hypothesis is `apoyada`.

A formalisation (`formal:` — e.g. Lean) is optional, reported, and never
replaces the researcher's review of the statement.

    claim_gate.py check   --vault V --claim C-XXXX   (JSON; exit 0 open, 3 closed)
    claim_gate.py project --vault V --project-dir D  (JSON for every claim)

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claim_records as cr  # noqa: E402
import verifications as vf  # noqa: E402
from notes import parse_frontmatter, split_note  # noqa: E402
from verifier_packet import PROOF_KINDS, build, find_note  # noqa: E402

__version__ = "1.0.0"


def frontmatter(path: Path) -> dict:
    parts = split_note(path.read_text(encoding="utf-8-sig"))
    return parse_frontmatter(parts[0]) if parts else {}


def recorded_packet_sha(path: Path) -> str | None:
    """sha256 of the packet behind the LATEST `(note)` entry of
    `## Verificación independiente`."""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^## Verificación independiente\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        return None
    shas = re.findall(r"^### [^\n]*\(note\)[ \t]*\n(?:(?!^### )[^\n]*\n)*?^- paquete sha256:[ \t]*([0-9a-f]{64})",
                      m.group(1), re.M)
    return shas[-1] if shas else None


def check_script(path: Path) -> Path:
    return path.parent / "checks" / f"{path.stem.split(' ')[0]}.py"


def gate(vault: str, path: Path) -> dict:
    fm = frontmatter(path)
    kind = str(fm.get("kind", ""))
    cid = str(fm.get("id", path.stem))
    res: dict = {"claim": cid, "kind": kind, "status": fm.get("status"), "applies": kind in PROOF_KINDS,
                 "layers": {}, "formal": fm.get("formal") or None, "missing": [], "ok": False}
    if not res["applies"]:
        res["ok"] = True
        return res
    statement = cr.statement_text(path)
    proof = cr.proof_text(path)
    s_sha, p_sha = cr.text_sha256(statement), cr.text_sha256(proof)
    res["hashes"] = {"statement": s_sha, "proof": p_sha}
    L = res["layers"]
    miss = res["missing"]

    # 1. fresh verifier on the proof, bound to the packet it saw
    v: dict = {"ok": False}
    try:
        packet, _manifest = build(vault, str(path), [], [], None)
        current = cr.text_sha256(packet)
    except ValueError as exc:
        current = None
        v["detail"] = str(exc)
    entries = vf.load(str(path))[5]
    last = vf.latest(entries, "note")
    recorded = recorded_packet_sha(path)
    v.update({"verdict": last.get("verdict") if last else None, "packet_now": current, "packet_verified": recorded})
    if current is None:
        miss.append(f"verificador: no se puede construir el paquete ({v.get('detail')})")
    elif not last:
        miss.append("verificador: la demostración no se ha verificado")
    elif recorded != current:
        miss.append("verificador: la última verificación fue sobre otro texto (demostración, enunciado o dependencias cambiaron)")
    elif last.get("verdict") != "no_errors_found":
        miss.append(f"verificador: último veredicto «{last.get('verdict')}» — corrige y vuelve a verificar")
    else:
        v["ok"] = True
    L["verifier"] = v

    # 2. researcher sign-offs, bound to the exact texts
    signoffs = cr.read_block(path, "signoffs")
    for part, sha, label in (("statement", s_sha, "enunciado"), ("proof", p_sha, "demostración")):
        s = cr.latest(signoffs, part=part)
        ok = bool(statement if part == "statement" else proof) and s is not None and s.get("sha256") == sha
        L[f"signoff_{part}"] = {"ok": ok, "by": s.get("by") if s else None, "date": s.get("date") if s else None,
                                "stale": bool(s) and s.get("sha256") != sha}
        if not ok:
            miss.append(f"tu visto bueno al {label}: " + ("el texto cambió desde que lo firmaste" if s else "falta"))

    # 3. numerical sanity check
    checks = cr.read_block(path, "numerical_checks")
    last_check = checks[-1] if checks else None
    script = check_script(path)
    script_sha = cr.text_sha256(script.read_text(encoding="utf-8")) if script.is_file() else None
    n: dict = {"ok": False, "status": last_check.get("status") if last_check else None,
               "script": script.name if script.is_file() else None}
    if not last_check:
        miss.append("comprobación numérica: no se ha ejecutado (o declarado no factible)")
    elif last_check.get("statement_sha256") != s_sha:
        miss.append("comprobación numérica: se hizo sobre otro enunciado")
    elif last_check.get("status") == "passed":
        if last_check.get("script_sha256") != script_sha:
            miss.append("comprobación numérica: el script cambió desde que pasó")
        else:
            n["ok"] = True
    elif last_check.get("status") == "not_feasible":
        reason = last_check.get("reason") or ""
        s = cr.latest(signoffs, part="infeasibility")
        if s and s.get("sha256") == cr.text_sha256(cr.norm(reason)):
            n["ok"] = True
            n["reason"] = reason
        else:
            miss.append("comprobación numérica declarada no factible: falta tu visto bueno al motivo")
    else:
        miss.append(f"comprobación numérica: «{last_check.get('status')}»")
    L["numeric"] = n

    # 4. dependencies stand
    pending = []
    raw_deps = fm.get("depends_on") or []
    deps_text = " ".join(map(str, raw_deps)) if isinstance(raw_deps, list) else str(raw_deps)
    for dep in dict.fromkeys(re.findall(r"\b[CH]-\d{4}\b", deps_text)):
        p = find_note(vault, dep)
        st = str(frontmatter(Path(p)).get("status")) if p else "no encontrada"
        need = "probado" if dep.startswith("C-") else "apoyada"
        if st != need:
            pending.append(f"{dep} ({st})")
    L["dependencies"] = {"ok": not pending, "pending": pending}
    if pending:
        miss.append("dependencias sin establecer: " + ", ".join(pending))

    res["ok"] = not miss
    return res


def claim_path(vault: str, cid: str) -> Path:
    p = find_note(vault, cid)
    if p is None or not cid.startswith("C-"):
        raise vf.InputError(f"claim {cid} not found")
    return Path(p)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--vault", required=True)
    c.add_argument("--claim", required=True)
    p = sub.add_parser("project")
    p.add_argument("--vault", required=True)
    p.add_argument("--project-dir", required=True, type=Path)
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args.cmd == "check":
            r = gate(args.vault, claim_path(args.vault, args.claim))
            print(json.dumps(r, ensure_ascii=False))
            return 0 if r["ok"] else 3
        out = [gate(args.vault, q) for q in sorted((args.project_dir / "Claims").glob("C-*.md"))]
        print(json.dumps(out, ensure_ascii=False))
        return 0
    except (vf.InputError, OSError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
