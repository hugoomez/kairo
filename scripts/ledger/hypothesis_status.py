#!/usr/bin/env python3
"""update-confidence's status writer: the only code that changes a hypothesis's `status`.

    hypothesis_status.py propose --vault <vault> --hypothesis H-XXXX
    hypothesis_status.py apply   --vault <vault> --hypothesis H-XXXX --to <status> --by <who>
                                 --evidence "<why>" [--experiments E-1 E-2] [--combination "<line>"]
    hypothesis_status.py discard --vault <vault> --hypothesis H-XXXX --reason "<why>" --by <name>

`propose` (read-only, JSON) computes the edge update-confidence's state table
allows now, mechanically:
  - From `preregistrada`, once its experiment has run: `en_experimento`.
  - From `en_experimento`, with the adjudicating experiments being exactly
    evidence_gate.py gather's `eligible` list and each one's `result.verdict`:
    - one experiment: refutada → refutada (on a publication line it stays,
      pending replication); apoyada → stays (first support); inconclusa →
      inconclusa;
    - two: combine_effects.py on their effects and CIs (`<e> [<lo>, <hi>]`),
      then the second-experiment table. `apoyada` is conditional on the fresh
      verifier finding no errors, so `needs_verifier: true`.
    - three or more: a human decision, never proposed.
  - An experiment already named in the hypothesis's `history` was adjudicated
    before; with nothing new, there is no edge.
  It reports `to`, whether `to` is terminal, why, and the experiments and
  combination it used.

`apply` writes one edge, and refuses any pair not in the table:
  - it sets `status` and `updated`, and appends one `history` entry;
  - `apoyada` additionally needs the governing `note` verification to be
    `no_errors_found`.
  Everything else in the note is left as it was.

`discard` is the researcher's edge `propuesta | en_cola → descartada` (terminal).
It is never evidence-driven, it refuses to run inside an agent session
(`CLAUDECODE` / `KAIRO_AGENT_SESSION`), and it needs a reason. `review.py
discard` calls it.

Exit codes: 0 ok · 3 refused · 1 error. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "analysis"))
from verifications import (  # noqa: E402
    InputError,
    _set_scalar,
    block_end,
    find_key,
    fm_value,
    latest,
    load,
    write_raw,
    yaml_scalar,
)
from notes import read_note  # noqa: E402
from evidence_gate import cmd_gather  # noqa: E402
from combine_effects import _se_from_ci, combine_effects  # noqa: E402

TOOL = "kairo/hypothesis_status@1.0.0"
AGENT_MARKERS = ("CLAUDECODE", "KAIRO_AGENT_SESSION")
TERMINAL = {"apoyada", "refutada", "inconclusa", "evidencia_mixta", "descartada"}
# update-confidence's state table, as (from, to) pairs. Nothing else is written.
EDGES = {
    ("propuesta", "en_cola"), ("en_cola", "propuesta"),
    ("propuesta", "preregistrada"), ("preregistrada", "en_experimento"),
    ("en_experimento", "en_experimento"), ("en_experimento", "refutada"),
    ("en_experimento", "apoyada"), ("en_experimento", "evidencia_mixta"),
    ("en_experimento", "inconclusa"),
    ("propuesta", "descartada"), ("en_cola", "descartada"),
}
_ID = re.compile(r"^H-\d{4}$")
_EFFECT = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]")


class Refused(Exception):
    pass


def find_hypothesis(vault: Path, hid: str) -> Path:
    if not _ID.match(hid):
        raise Refused("hypothesis must look like H-XXXX")
    hits = [h for h in sorted(vault.glob(f"Projects/*/Hipotesis/{hid}*.md")) if re.match(rf"^{hid}(\b|[ _.-])", h.name)]
    if len(hits) != 1:
        raise Refused(f"{hid}: {'not found' if not hits else 'several notes match'} under Projects/*/Hipotesis/")
    return hits[0]


def _history_experiments(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^history:\s*\n((?:[ \t-].*\n?)*)", text, re.MULTILINE)
    if not m:
        return set()
    return {e for line in m.group(1).splitlines() if re.match(r"^\s+experiments:", line) for e in re.findall(r"E-\d{4}", line)}


def _verdict(lines: list[str]) -> tuple[str | None, str | None]:
    """result.verdict and result.effect from the experiment's frontmatter block."""
    block = re.search(r"^result:\s*\n((?:[ \t]+.*\n?)*)", "".join(lines), re.MULTILINE)
    if not block:
        return None, None
    def field(k: str) -> str | None:
        m = re.search(rf"^\s+{k}:\s*(.*?)\s*(#.*)?$", block.group(1), re.MULTILINE)
        v = m.group(1).strip().strip("\"'") if m else ""
        return v if v and not v.startswith("<") else None
    return field("verdict"), field("effect")


def audit_blockers(hyp_path: Path, new: list[dict]) -> list[dict]:
    """pitfall_audit.py (dry run, nothing written) on each experiment about to be
    adjudicated: its crítico findings block any evidence edge, exactly as the
    update-confidence skill applies it."""
    script = HERE.parent / "audit" / "pitfall_audit.py"
    out = []
    for c in new:
        exp = next(iter(sorted((hyp_path.parent.parent / "Experimentos").glob(f"{c['id']}*.md"))), None)
        if exp is None:
            continue
        r = subprocess.run([sys.executable, str(script), "audit", "--experiment", str(exp), "--hypothesis", str(hyp_path), "--json"],
                           capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        try:
            rep = json.loads(r.stdout)
        except json.JSONDecodeError:
            out.append({"experiment": c["id"], "check": "audit", "message": f"la auditoría no pudo ejecutarse ({(r.stderr or '').strip()[:120]})"})
            continue
        for check, res in (rep.get("checks") or {}).items():
            for f in res.get("findings") or []:
                if f.get("severity") == "crítico":
                    out.append({"experiment": c["id"], "check": check, "message": f.get("message", "")})
    return out


def propose(vault: Path, hid: str) -> dict:
    path = find_hypothesis(vault, hid)
    _, lines, _, lo, hi, _ = load(str(path))
    status = (fm_value(lines, lo + 1, hi, "status") or "").strip()
    linea = (fm_value(lines, lo + 1, hi, "linea_publicacion") or "").strip().lower() == "true"
    out: dict = {"tool": TOOL, "hypothesis": hid, "from": status, "to": None, "terminal": False,
                 "needs_verifier": False, "experiments": [], "combination": None, "reason": ""}
    if status == "preregistrada":
        proj = path.parent.parent
        ran = []
        for e in sorted((proj / "Experimentos").glob("E-*.md")):
            parsed = read_note(e)
            if parsed and str(parsed[0].get("hypothesis", "")) == hid and parsed[0].get("status") in ("running", "completed"):
                ran.append(str(parsed[0].get("id", e.stem)))
        if ran:
            out.update(to="en_experimento", experiments=ran, reason=f"su experimento ya se ejecutó ({', '.join(ran)})")
        else:
            out["reason"] = "su experimento aún no se ha ejecutado"
        return out
    if status != "en_experimento":
        out["reason"] = f"está «{status}»: la evidencia solo se adjudica en en_experimento"
        return out
    _, gathered = cmd_gather(path)
    counted = []
    for e in gathered.get("eligible", []):
        ep = Path(e["path"])
        _, elines, _, _, _, _ = load(str(ep))
        verdict, effect = _verdict(elines)
        if verdict:
            counted.append({"id": e["id"], "verdict": verdict, "effect": effect})
    out["experiments"] = [c["id"] for c in counted]
    seen = _history_experiments(path)
    new = [c for c in counted if c["id"] not in seen]
    if not counted:
        out["reason"] = "ningún experimento válido y confirmatorio con veredicto todavía"
        return out
    if new:
        blockers = audit_blockers(path, new)
        if blockers:
            out["audit"] = blockers
            out["reason"] = (
                f"la auditoría de trampas (obligatoria antes de cualquier cambio por evidencia) encuentra "
                f"{len(blockers)} problema(s) crítico(s): "
                + "; ".join(f"{b['experiment']} {b['check']}: {b['message']}" for b in blockers[:3])
            )
            return out
    if not new:
        out["reason"] = "nada nuevo que adjudicar: sus experimentos ya están en el historial"
        awaiting = len(counted) == 1 and (counted[0]["verdict"] == "apoyada" or (linea and counted[0]["verdict"] == "refutada"))
        if awaiting:
            out["reason"] += " (falta la replicación independiente)"
        return out
    if len(counted) == 1:
        v = counted[0]["verdict"]
        if v == "refutada" and not linea:
            out.update(to="refutada", reason=f"{counted[0]['id']} la refuta (un experimento válido basta fuera de una línea de publicación)")
        elif v == "refutada":
            out.update(to="en_experimento", reason=f"primera refutación ({counted[0]['id']}); en línea de publicación hace falta replicarla")
        elif v == "apoyada":
            out.update(to="en_experimento", reason=f"primer apoyo ({counted[0]['id']}); hace falta una replicación independiente")
        elif v == "inconclusa":
            out.update(to="inconclusa", reason=f"{counted[0]['id']} no la apoya ni la refuta")
        out["terminal"] = out["to"] in TERMINAL
        return out
    if len(counted) > 2:
        out["reason"] = "tres o más experimentos: la decisión es tuya, no automática"
        return out
    a, b = counted
    vs = {a["verdict"], b["verdict"]}
    if "inconclusa" in vs:
        out.update(to="inconclusa", reason="uno de los dos experimentos es inconcluso")
    elif vs == {"apoyada", "refutada"}:
        out.update(to="evidencia_mixta", reason="un experimento apoya y el otro refuta")
    else:
        ea, eb = _EFFECT.match(a["effect"] or ""), _EFFECT.match(b["effect"] or "")
        if not (ea and eb):
            out["reason"] = "falta el efecto con su intervalo (`<e> [<lo>, <hi>]`) en algún experimento: no se puede combinar"
            return out
        e1, l1, h1 = map(float, ea.groups())
        e2, l2, h2 = map(float, eb.groups())
        comb = combine_effects(e1, _se_from_ci(l1, h1, 0.05), e2, _se_from_ci(l2, h2, 0.05))
        out["combination"] = {k: comb[k] for k in ("consistency", "e_pooled", "ci_pooled", "q_p_value", "i_squared", "script_version")}
        if comb["consistency"] != "consistent":
            out.update(to="evidencia_mixta", reason=f"los dos experimentos no concuerdan (combinación: {comb['consistency']})")
        elif vs == {"apoyada"}:
            out.update(to="apoyada", needs_verifier=True,
                       reason="dos experimentos independientes la apoyan y concuerdan; pasa a apoyada solo si el verificador independiente no encuentra errores")
        else:
            out.update(to="refutada", reason="dos experimentos independientes la refutan y concuerdan")
    out["terminal"] = out["to"] in TERMINAL
    return out


def _append_history(lines: list[str], nl: str, fields: list[tuple[str, str]]) -> None:
    hi = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    k = find_key(lines, 1, hi, "history")
    entry = [f"  - {fields[0][0]}: {yaml_scalar(fields[0][1])}{nl}"]
    entry += [f"    {n}: {v if n in ('experiments', 'replication_frozen') else yaml_scalar(v)}{nl}" for n, v in fields[1:]]
    if k is None:
        lines[hi:hi] = [f"history:{nl}", *entry]
        return
    if lines[k].rstrip("\r\n").split(":", 1)[1].strip() == "[]":
        lines[k] = f"history:{nl}"
    end = block_end(lines, k, hi)
    lines[end:end] = entry


def _link(lines: list[str], nl: str, eid: str) -> None:
    """Append `eid` to `linked_experiment` (block or inline list), once."""
    hi = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    k = find_key(lines, 1, hi, "linked_experiment")
    if k is None:
        lines[hi:hi] = [f"linked_experiment:{nl}", f"  - {eid}{nl}"]
        return
    end = block_end(lines, k, hi)
    if eid in "".join(lines[k:end]):
        return
    head = lines[k].rstrip("\r\n")
    inline = head.split(":", 1)[1].strip()
    if inline.startswith("["):
        items = [x.strip() for x in inline.strip("[]").split(",") if x.strip()]
        lines[k] = f"linked_experiment: [{', '.join([*items, eid])}]{nl}"
    else:
        lines[end:end] = [f"  - {eid}{nl}"]


def apply(vault: Path, hid: str, to: str, by: str, evidence: str, experiments: list[str], combination: str | None,
          replication_frozen: str | None = None) -> dict:
    path = find_hypothesis(vault, hid)
    text, lines, nl, lo, hi, entries = load(str(path))
    status = (fm_value(lines, lo + 1, hi, "status") or "").strip()
    if replication_frozen:
        # The self-loop that links a frozen replication. It records the experiment
        # under `replication_frozen`, never `experiments`: only evidence edges name
        # `experiments`, and propose treats those as already adjudicated.
        if (status, to) != ("en_experimento", "en_experimento"):
            raise Refused("--replication-frozen is the en_experimento self-loop only")
        if experiments:
            raise Refused("--replication-frozen records no evidence: don't pass --experiments")
    if (status, to) not in EDGES:
        raise Refused(f"«{status} → {to}» no está en la tabla de estados de update-confidence")
    if to == "descartada":
        raise Refused("descartada solo la escribe `discard` (decisión del investigador), nunca un apply")
    if to == "apoyada":
        gov = latest(entries, "note")
        if not gov or gov.get("verdict") != "no_errors_found":
            raise Refused("apoyada exige que la verificación independiente (scope note) más reciente sea no_errors_found")
    if not evidence.strip():
        raise Refused("--evidence is required")
    today = date.today().isoformat()
    if to != status:
        _set_scalar(lines, "status", to, nl)
    _set_scalar(lines, "updated", today, nl)
    fields = [("date", today), ("status", to), ("by", by)]
    if replication_frozen:
        _link(lines, nl, replication_frozen)
        fields.append(("replication_frozen", f"[{replication_frozen}]"))
    if experiments:
        fields.append(("experiments", "[" + ", ".join(experiments) + "]"))
    if combination:
        fields.append(("combination", combination))
    fields.append(("evidence", evidence.strip()))
    _append_history(lines, nl, fields)
    write_raw(str(path), "".join(lines))
    return {"tool": TOOL, "hypothesis": hid, "from": status, "to": to, "terminal": to in TERMINAL,
            "file": path.relative_to(vault).as_posix()}


def discard(vault: Path, hid: str, reason: str, by: str) -> dict:
    for m in AGENT_MARKERS:
        if os.environ.get(m):
            raise Refused(f"descartar es una decisión del investigador: rechazado dentro de una sesión de agente ({m})")
    reason = " ".join(reason.split())
    if len(reason) < 5:
        raise Refused("--reason is required (at least 5 characters)")
    path = find_hypothesis(vault, hid)
    text, lines, nl, lo, hi, _ = load(str(path))
    status = (fm_value(lines, lo + 1, hi, "status") or "").strip()
    if (status, "descartada") not in EDGES:
        raise Refused(f"solo se descarta una hipótesis propuesta o en cola (está «{status}»); tras el preregistro decide la evidencia")
    today = date.today().isoformat()
    _set_scalar(lines, "status", "descartada", nl)
    _set_scalar(lines, "updated", today, nl)
    _append_history(lines, nl, [("date", today), ("status", "descartada"), ("by", by),
                                ("evidence", f"descartada por el investigador: {reason}")])
    write_raw(str(path), "".join(lines))
    return {"tool": TOOL, "hypothesis": hid, "from": status, "to": "descartada", "terminal": True,
            "file": path.relative_to(vault).as_posix()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("propose", "apply", "discard"):
        p = sub.add_parser(name)
        p.add_argument("--vault", required=True, type=Path)
        p.add_argument("--hypothesis", required=True)
        if name == "apply":
            p.add_argument("--to", required=True)
            p.add_argument("--by", required=True)
            p.add_argument("--evidence", required=True)
            p.add_argument("--experiments", nargs="*", default=[])
            p.add_argument("--combination", default=None)
            p.add_argument("--replication-frozen", default=None, metavar="E-XXXX",
                           help="the self-loop for a frozen independent replication: links it, records no evidence")
        if name == "discard":
            p.add_argument("--reason", required=True)
            p.add_argument("--by", required=True)
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        vault = a.vault.resolve()
        if a.cmd == "propose":
            out = propose(vault, a.hypothesis)
        elif a.cmd == "apply":
            rf = a.replication_frozen if a.replication_frozen and re.match(r"^E-\d{4}$", a.replication_frozen) else None
            if a.replication_frozen and not rf:
                raise Refused("--replication-frozen must look like E-XXXX")
            out = apply(vault, a.hypothesis, a.to, a.by, a.evidence, [e for e in a.experiments if re.match(r"^E-\d{4}$", e)],
                        a.combination, rf)
        else:
            out = discard(vault, a.hypothesis, a.reason, a.by)
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
