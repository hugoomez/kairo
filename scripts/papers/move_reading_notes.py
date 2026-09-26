#!/usr/bin/env python3
"""Move each paper note's `## Notas de lectura` into `Papers/_notas/<id>.md`.

A `Papers/` note holds only source text (`## Referencia`, `## Resumen`,
`## Texto completo`). Model-written reading notes live apart, in
`Papers/_notas/`, where send_guard blocks every model read and the verifier
packet builder never looks (send_guard.is_model_notes).

    python move_reading_notes.py --vault <vault>            # dry run: what would move
    python move_reading_notes.py --vault <vault> --write    # move them
    python move_reading_notes.py --vault <vault> --check    # exit 1 if any paper note
                                                            # still has the section

The section is moved verbatim (heading dropped, body kept) under a frontmatter
that marks it model-written and not citable, and removed from the paper note;
nothing else in the note changes. A `send: never` note is never opened. An
existing `_notas/<id>.md` with different content is not overwritten (reported,
exit 1). The script prints file names and counts only, never note text.

Exit codes: 0 ok, 1 something left to do (--check) or a conflict, 2 bad input.
Standard library only.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "security"))
from send_guard import MODEL_NOTES_DIR, is_flagged  # noqa: E402

__version__ = "1.0.0"
HEADING = "Notas de lectura"
_ID = re.compile(r"^(P-\d{4,})\b")


def split_section(text: str, heading: str = HEADING) -> tuple[str, str | None]:
    """Return (text without the `## <heading>` section, section body or None).

    The section runs to the next `## ` heading outside a ``` fence, or to EOF.
    """
    lines = text.split("\n")
    in_fence = False
    start = end = None
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not line.startswith("## "):
            continue
        if start is None and line[3:].strip() == heading:
            start = i
        elif start is not None:
            end = i
            break
    if start is None:
        return text, None
    end = len(lines) if end is None else end
    body = "\n".join(lines[start + 1:end]).strip("\n")
    rest = lines[:start] + lines[end:]
    kept = "\n".join(rest).rstrip("\n") + "\n"
    return kept, body


def notes_file(paper_id: str, paper_name: str, body: str, today: str) -> str:
    return "\n".join([
        "---",
        f"notas_de: {paper_id}",
        f'nota_del_paper: "{paper_name}"',
        "escrito_por: modelo",
        "citable: false",
        f"movido: {today}",
        "---",
        "",
        f"# Notas de lectura — {paper_id} (escritas por un modelo)",
        "",
        "> **Texto escrito por un modelo, no por los autores del paper. No se cita,",
        "> no es fuente y ningún localizador apunta aquí.** Ninguna skill ni agente",
        "> de Kairo lee esta carpeta (send_guard la bloquea). La fuente es la nota del",
        f"> paper: `Papers/{paper_name}`.",
        "",
        body,
        "",
    ])


def paper_notes(vault: Path) -> list[Path]:
    return sorted(p for p in (vault / "Papers").glob("P-*.md") if p.is_file())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--vault", required=True, type=Path)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    ap.add_argument("--date", default=_dt.date.today().isoformat())
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    if not (a.vault / "Papers").is_dir():
        print(f"error: no Papers/ under {a.vault}", file=sys.stderr)
        return 2

    out_dir = a.vault / "Papers" / MODEL_NOTES_DIR
    pending, conflicts, moved = [], [], []
    for p in paper_notes(a.vault):
        if is_flagged(p):
            print(f"omitida (send: never): {p.name}")
            continue
        raw = p.read_bytes().decode("utf-8")
        nl = "\r\n" if "\r\n" in raw else "\n"
        kept, body = split_section(raw.replace("\r\n", "\n"))
        if body is None:
            continue
        m = _ID.match(p.name)
        if not m:
            print(f"error: {p.name}: no P-id in the file name", file=sys.stderr)
            return 2
        pid = m.group(1)
        pending.append(p.name)
        if a.check or not a.write:
            continue
        target = out_dir / f"{pid}.md"
        new = notes_file(pid, p.name, body, a.date)
        if target.exists():
            old = target.read_text(encoding="utf-8").split("---", 2)[-1]
            if old != new.split("---", 2)[-1]:
                conflicts.append(target.name)
                continue
        out_dir.mkdir(exist_ok=True)
        target.write_bytes(new.encode("utf-8"))
        p.write_bytes(kept.replace("\n", nl).encode("utf-8"))
        moved.append(f"{p.name} -> Papers/{MODEL_NOTES_DIR}/{target.name}")

    if a.check:
        for n in pending:
            print(f"con ## {HEADING}: {n}")
        return 1 if pending else 0
    if not a.write:
        for n in pending:
            print(f"se movería: {n}")
        print(f"{len(pending)} nota(s) con ## {HEADING} (dry run; --write para moverlas)")
        return 0
    for line in moved:
        print(f"movida: {line}")
    for n in conflicts:
        print(f"conflicto: Papers/{MODEL_NOTES_DIR}/{n} ya existe con otro contenido; "
              "no se tocó", file=sys.stderr)
    print(f"{len(moved)} movida(s), {len(conflicts)} conflicto(s)")
    return 1 if conflicts else 0


if __name__ == "__main__":
    sys.exit(main())
