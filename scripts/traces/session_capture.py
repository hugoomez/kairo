#!/usr/bin/env python3
"""SessionEnd hook: keep the transcript of a Claude Code session that changed
research notes, inside the vault, next to the project it touched.

Registered in the vault's ``.claude/settings.json`` as a ``SessionEnd`` hook.
It reads the hook's stdin JSON (``session_id``, ``transcript_path``), scans the
transcript for Write / Edit / MultiEdit / NotebookEdit calls on files under
``Projects/<slug>/{Hipotesis,Experimentos,Claims,Producto,Manuscritos,Evolucion}/``,
and for every project touched:

* copies the transcript to
  ``Projects/<slug>/_trazas-agente/cli/<YYYY-MM-DD>__<session>.jsonl``
  (a resumed session overwrites its own copy — it is the same conversation);
* appends one ``cli_session`` entry to the project's action log, with the note
  ids touched as ``refs`` and the copy as ``trace`` (once per session and file);
* re-renders today's notebook page and commits the copy and ``Bitacora/``
  alone (``git commit --only``).

So work done in a plain session is as traceable as work launched from the
interface. Sessions that only read are not kept.

Always exits 0; problems go to stderr. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bitacora"))
import action_log  # noqa: E402

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
RESEARCH_DIRS = {"Hipotesis", "Experimentos", "Claims", "Producto", "Manuscritos", "Evolucion"}
_REF = re.compile(r"\b(?:H|E|C|EVO|ADR|F)-\d{3,4}\b")


def touched(transcript: Path, vault: Path) -> dict[str, set[str]]:
    """slug -> vault-relative research files written in this session."""
    vault = vault.resolve()
    out: dict[str, set[str]] = {}
    with transcript.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = obj.get("message") if isinstance(obj, dict) else None
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list):
                continue
            for block in content:
                if not (isinstance(block, dict) and block.get("type") == "tool_use"
                        and block.get("name") in WRITE_TOOLS):
                    continue
                inp = block.get("input") or {}
                raw = inp.get("file_path") or inp.get("notebook_path")
                if not isinstance(raw, str):
                    continue
                p = Path(raw)
                try:
                    rel = (p if p.is_absolute() else vault / p).resolve().relative_to(vault)
                except ValueError:
                    continue
                parts = rel.parts
                if len(parts) >= 4 and parts[0] == "Projects" and parts[2] in RESEARCH_DIRS:
                    out.setdefault(parts[1], set()).add(rel.as_posix())
    return out


def already_logged(log: Path, trace: str) -> bool:
    if not log.exists():
        return False
    return any(f'"trace":"{trace}"' in ln for ln in log.read_text(encoding="utf-8").splitlines()[-500:])


def run(vault: Path, payload: dict, today: str | None = None) -> list[str]:
    transcript = Path(str(payload.get("transcript_path") or ""))
    session = re.sub(r"[^A-Za-z0-9-]", "", str(payload.get("session_id") or ""))[:36] or "sesion"
    if not transcript.is_file():
        return []
    vault = vault.resolve()
    day = today or date.today().isoformat()
    logged = []
    for slug, files in sorted(touched(transcript, vault).items()):
        rel = f"Projects/{slug}/_trazas-agente/cli/{day}__{session}.jsonl"
        dest = vault / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(transcript, dest)
        log = vault / "Projects" / slug / "Bitacora" / "acciones.jsonl"
        if not already_logged(log, rel):
            refs = sorted(set(_REF.findall(" ".join(files))))
            written = action_log.append(log, {
                "actor": "agent",
                "by": "claude-code",
                "action": "cli_session",
                "event": "completed",
                "summary": f"Sesión de Claude Code que modificó {len(files)} nota(s) de investigación",
                "refs": refs,
                "trace": rel,
            })
            page = log.parent / f"{written['date']}.md"
            page.write_text(action_log.render(action_log.load(log), written["date"], page),
                            encoding="utf-8", newline="\n")
        paths = [rel, f"Projects/{slug}/Bitacora"]
        subprocess.run(["git", "add", "--", *paths], cwd=vault, capture_output=True, check=True)
        staged = subprocess.run(["git", "diff", "--cached", "--name-only", "--", *paths],
                                cwd=vault, capture_output=True, check=True).stdout.strip()
        if staged:
            subprocess.run(["git", "commit", "--quiet", "--only", "-m",
                            f"Traza de sesión {session[:8]} ({slug})", "--", *paths],
                           cwd=vault, capture_output=True, check=True)
        logged.append(slug)
    return logged


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vault", required=True, type=Path)
    args = ap.parse_args(argv)
    try:
        run(args.vault, json.loads(sys.stdin.read() or "{}"))
    except Exception as exc:  # noqa: BLE001 — a hook must never break the session
        print(f"[trazas] session not captured: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
