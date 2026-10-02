#!/usr/bin/env python3
"""Critique packets and critique notes (the devil's advocate on demand).

    critique_note.py packet --vault V --target H-0001 [--section "<heading>"] --out packet.md
    critique_note.py packet --vault V --target decision --text-file decision.txt --out packet.md
    critique_note.py write  --vault V --target H-0001 --packet packet.md --result result.json \\
                            [--model M] [--project <slug>]

``packet`` builds what the ``devils-advocate`` agent may see, by allow-list:

* a hypothesis (``H-XXXX``) → the fresh-verifier's own packet
  (``scripts/ledger/verifier_packet.py``: claim, justification, the verbatim
  text each citation points at), re-headed as a critique packet;
* any other note (``ADR-…``, ``E-…``, ``C-…``, ``F-…``, or a path to a note
  under ``Projects/``, e.g. a manuscript) → its body minus the sections that
  carry reasoning, critiques or verdicts, and none of its frontmatter;
* a recorded decision (``--text-file``) → that text alone.

A ``send: never`` note and the model-written ``Papers/_notas/`` are refused.

``write`` validates the agent's JSON, assigns the next ``CR-XXXX`` (vault-wide),
and writes ``Projects/<slug>/Criticas/CR-XXXX.md`` — marked
``escrito_por: modelo`` / ``citable: false``. A critique is never evidence: it
touches no status, no ``verifications:``, no flag on the target.

Exit codes: 0 ok · 3 refused (bad input, flagged note, invalid result) · 1 error.
Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ledger"))
sys.path.insert(0, str(HERE.parent / "security"))
from send_guard import is_flagged, is_model_notes  # noqa: E402
from verifier_packet import body_sections, split_frontmatter  # noqa: E402

__version__ = "1.0.0"
CRITIC = "kairo/devils-advocate@1.0.0"
SEVERITIES = ("alta", "media", "baja")
_ID = re.compile(r"^(H|E|C|ADR|F|EVO)-\d{3,4}$")
# Sections that carry reasoning, critiques, verdicts or lessons: the critic
# judges the artifact, not what was said about it.
EXCLUDED = ("Génesis", "Revisión del ciclo", "Verificación independiente", "Revisión de vigencia",
            "Lección", "Críticas", "Notas de lectura", "Hipótesis rival descartada")


class Refused(Exception):
    pass


def find_note(vault: Path, target: str) -> Path:
    if _ID.match(target):
        hits = [p for p in (vault / "Projects").glob(f"*/*/{target}*.md")
                if p.name == f"{target}.md" or p.name.startswith(f"{target} ")]
        if len(hits) != 1:
            raise Refused(f"{target}: {'no note found' if not hits else 'more than one note matches'}")
        return hits[0]
    p = (vault / target).resolve()
    try:
        p.relative_to((vault / "Projects").resolve())
    except ValueError as exc:
        raise Refused("a path target must be a note under Projects/") from exc
    if not p.is_file():
        raise Refused(f"{target}: not a file")
    return p


def project_slug(vault: Path, note: Path) -> str:
    return note.resolve().relative_to((vault / "Projects").resolve()).parts[0]


def generic_packet(note: Path, label: str, section: str | None) -> str:
    _fm, body = split_frontmatter(note.read_text(encoding="utf-8"))
    out = ["# Paquete de crítica", "", f"Artefacto: {label}", ""]
    kept = 0
    for heading, text in body_sections(body):
        if heading and any(heading.startswith(x) for x in EXCLUDED):
            continue
        if section is not None and heading != section:
            continue
        if not text.strip():
            continue
        out += [f"## {heading}" if heading else "## (preámbulo)", "", text.strip(), ""]
        kept += 1
    if not kept:
        raise Refused("nothing left to critique after the allow-list")
    return "\n".join(out)


def build_packet(vault: Path, target: str, section: str | None, text_file: Path | None) -> str:
    if text_file is not None:
        text = text_file.read_text(encoding="utf-8").strip()
        if not text:
            raise Refused("empty decision text")
        return "\n".join(["# Paquete de crítica", "", f"Artefacto: decisión registrada ({target})", "", text, ""])
    note = find_note(vault, target)
    if is_model_notes(note.resolve()):
        raise Refused("model-written reading notes are never critiqued or sent")
    if is_flagged(note):
        raise Refused(f"{target} is send: never — its content is never sent to a model")
    if target.startswith("H-"):
        cmd = [sys.executable, str(HERE.parent / "ledger" / "verifier_packet.py"),
               "--vault", str(vault), "--note", str(note)]
        if section:
            cmd += ["--section", section]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if res.returncode != 0:
            raise Refused(f"verifier_packet refused: {res.stderr.strip() or res.stdout.strip()}")
        return res.stdout.replace("# Paquete de verificación", "# Paquete de crítica", 1)
    return generic_packet(note, target, section)


def validate_result(r: dict) -> None:
    if not isinstance(r, dict):
        raise Refused("result must be a JSON object")
    if "verdict" in r or "status" in r:
        raise Refused("a critique carries no verdict and no status")
    obj = r.get("objections")
    if not isinstance(obj, list) or not obj:
        raise Refused("result needs at least one objection")
    for i, o in enumerate(obj, 1):
        if not isinstance(o, dict):
            raise Refused(f"objection {i} is not an object")
        for k in ("where", "objection"):
            if not isinstance(o.get(k), str) or not o[k].strip():
                raise Refused(f"objection {i}: `{k}` is required")
        if o.get("severity") not in SEVERITIES:
            raise Refused(f"objection {i}: severity must be one of {SEVERITIES}")
    for k in ("alternative_explanations", "auxiliary_assumptions", "cannot_assess"):
        v = r.get(k, [])
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise Refused(f"`{k}` must be a list of strings")
    for k in ("weakest_link", "embarrassing_result"):
        if k in r and not isinstance(r[k], str):
            raise Refused(f"`{k}` must be a string")


def next_id(vault: Path) -> str:
    nums = [int(m.group(1)) for p in (vault / "Projects").glob("*/Criticas/CR-*.md")
            if (m := re.match(r"CR-(\d+)", p.name))]
    return f"CR-{(max(nums) + 1) if nums else 1:04d}"


def bullets(items: list[str]) -> list[str]:
    return [f"- {x}" for x in items] or ["- (ninguna)"]


def _second_critic_label() -> str:
    """Recorded on every critique: the second critic never fails a critique."""
    import importlib.util  # noqa: PLC0415
    spec = importlib.util.spec_from_file_location(
        "second_critic_status", Path(__file__).resolve().parent.parent / "second_critic" / "status.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.label(mod.status())


def write_note(vault: Path, target: str, packet: Path, result: dict, model: str | None,
               project: str | None = None) -> Path:
    validate_result(result)
    if project:
        if not (vault / "Projects" / project / "_hub.md").is_file():
            raise Refused(f"no project folder {project}")
        slug = project
    elif _ID.match(target) or "/" in target:
        slug = project_slug(vault, find_note(vault, target))
    else:
        raise Refused("a decision critique needs --project <slug>")
    cid = next_id(vault)
    sha = hashlib.sha256(packet.read_bytes()).hexdigest()
    lines = [
        "---",
        f"id: {cid}",
        f"target: {target}",
        f"created: {date.today().isoformat()}",
        f"critic: {CRITIC}",
        f"model: {model or 'desconocido'}",
        f"packet_sha256: {sha}",
        f"second_critic: {_second_critic_label()}",
        "escrito_por: modelo",
        "citable: false",
        "---",
        "",
        f"# Crítica {cid} de {target}",
        "",
        "> Generada por un modelo que solo vio el artefacto (y el texto citado), no cómo se",
        "> hizo. No es evidencia, no cambia ningún estado y no es citable. Tú decides qué",
        "> hacer con cada objeción.",
        "",
        f"*Segundo crítico (otra familia de modelos, DeepInfra): {_second_critic_label()}.*",
        "",
        "## Objeciones",
        "",
    ]
    for i, o in enumerate(result["objections"], 1):
        lines += [
            f"{i}. **[{o['severity']}]** {o['objection'].strip()}",
            f"   - *Dónde:* {o['where'].strip()}",
            f"   - *Qué lo zanjaría:* {(o.get('would_settle_it') or '—').strip()}",
        ]
    lines += ["", "## Explicaciones alternativas", "", *bullets(result.get("alternative_explanations", []))]
    lines += ["", "## Supuestos auxiliares", "", *bullets(result.get("auxiliary_assumptions", []))]
    lines += ["", "## Eslabón más débil", "", (result.get("weakest_link") or "—").strip()]
    lines += ["", "## Resultado que la avergonzaría", "", (result.get("embarrassing_result") or "—").strip()]
    if result.get("cannot_assess"):
        lines += ["", "## No evaluable con el paquete", "", *bullets(result["cannot_assess"])]
    lines.append("")
    out = vault / "Projects" / slug / "Criticas" / f"{cid}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return out


def parse_result(raw: str) -> dict:
    """The agent's reply: the fenced JSON block, or bare JSON."""
    m = re.search(r"```json\s*(\{.*\})\s*```", raw, re.S)
    return json.loads(m.group(1) if m else raw)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("packet")
    p.add_argument("--vault", required=True, type=Path)
    p.add_argument("--target", required=True)
    p.add_argument("--section")
    p.add_argument("--text-file", type=Path)
    p.add_argument("--out", required=True, type=Path)
    w = sub.add_parser("write")
    w.add_argument("--vault", required=True, type=Path)
    w.add_argument("--target", required=True)
    w.add_argument("--packet", required=True, type=Path)
    w.add_argument("--result", required=True, type=Path)
    w.add_argument("--model")
    w.add_argument("--project", help="project folder slug (required when the target is a decision)")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args.cmd == "packet":
            text = build_packet(args.vault, args.target, args.section, args.text_file)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8", newline="\n")
            print(json.dumps({"out": str(args.out), "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}))
            return 0
        result = parse_result(args.result.read_text(encoding="utf-8"))
        out = write_note(args.vault, args.target, args.packet, result, args.model, args.project)
        print(json.dumps({"id": out.stem, "path": str(out)}, ensure_ascii=False))
        return 0
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
