#!/usr/bin/env python3
"""SessionEnd hook: keep the transcript of a Claude Code session that changed
research notes, inside the vault, next to the project it touched.

Registered in the vault's ``.claude/settings.json`` as a ``SessionEnd`` hook.
It reads the hook's stdin JSON (``session_id``, ``transcript_path``), scans the
transcript for Write / Edit / MultiEdit / NotebookEdit calls on files under
``Projects/<slug>/{Hipotesis,Experimentos,Claims,Producto,Manuscritos,Evolucion}/``
— and, for the literature path, on ``Estado-del-arte.md``, ``_busquedas/``,
``_vigilancia/`` and ``Notas de proyecto/`` — plus the shell commands that run a Kairo
literature script on a project (``lit_search.py``, ``ingest_paper.py``,
``check_sota.py``, ``lit_watch.py``, ``check_quotes.py``, ``export_bib.py``,
``import_library.py``, named by its ``Projects/<slug>`` folder or its
``--project PROJ-XXX`` id), and for every project touched:

* copies the transcript to
  ``Projects/<slug>/_trazas-agente/cli/<YYYY-MM-DD>__<session>.jsonl``
  (a resumed session overwrites its own copy — it is the same conversation),
  and the session's subagent transcripts, when Claude Code keeps them beside
  it (``<session>/subagents/*.jsonl``), to ``…__<session>/subagents/`` — so a
  state-of-the-art map can be followed back to each facet summary and the
  prompts that produced it;
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
SHELL_TOOLS = {"Bash", "PowerShell"}
RESEARCH_DIRS = {"Hipotesis", "Experimentos", "Claims", "Producto", "Manuscritos", "Evolucion"}
# the literature path: the map, the search and watch records, the corpus answers
LITERATURE_DIRS = {"_busquedas", "_vigilancia", "Notas de proyecto"}
LITERATURE_FILES = {"Estado-del-arte.md"}
LITERATURE_SCRIPTS = ("lit_search.py", "ingest_paper.py", "check_sota.py", "lit_watch.py", "check_quotes.py",
                      "export_bib.py", "import_library.py")
_REF = re.compile(r"\b(?:H|E|C|EVO|ADR|F|P)-\d{3,4}\b")
_PROJ_DIR = re.compile(r"Projects[/\\]+([A-Za-z0-9][A-Za-z0-9._-]*)")
_PROJ_ID = re.compile(r"--project[= ]+[\"']?(PROJ-\d{3,})")


def research_file(parts: tuple[str, ...]) -> bool:
    """A vault-relative path (as parts) that a research or literature step writes."""
    if len(parts) < 3 or parts[0] != "Projects":
        return False
    if len(parts) == 3:
        return parts[2] in LITERATURE_FILES
    return parts[2] in RESEARCH_DIRS or parts[2] in LITERATURE_DIRS


def project_ids(vault: Path) -> dict[str, str]:
    """PROJ-XXX -> slug, from each hub's `id:` line."""
    out = {}
    for hub in (vault / "Projects").glob("*/_hub.md"):
        try:
            m = re.search(r"^id:\s*[\"']?(PROJ-\d+)", hub.read_text(encoding="utf-8", errors="replace"), re.M)
        except OSError:
            continue
        if m:
            out[m.group(1)] = hub.parent.name
    return out


def shell_projects(command: str, vault: Path, ids: dict[str, str]) -> set[str]:
    """The projects a shell command runs a Kairo literature script on."""
    if not any(s in command for s in LITERATURE_SCRIPTS):
        return set()
    slugs = {m.group(1) for m in _PROJ_DIR.finditer(command)}
    slugs |= {ids[m.group(1)] for m in _PROJ_ID.finditer(command) if m.group(1) in ids}
    return {s for s in slugs if (vault / "Projects" / s / "_hub.md").is_file()}


def touched(transcript: Path, vault: Path) -> dict[str, set[str]]:
    """slug -> vault-relative research files written (or literature scripts run) in this session."""
    vault = vault.resolve()
    ids: dict[str, str] | None = None
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
                if not (isinstance(block, dict) and block.get("type") == "tool_use"):
                    continue
                inp = block.get("input") or {}
                if block.get("name") in SHELL_TOOLS and isinstance(inp.get("command"), str):
                    if ids is None:
                        ids = project_ids(vault)
                    for slug in shell_projects(inp["command"], vault, ids):
                        script = next(s for s in LITERATURE_SCRIPTS if s in inp["command"])
                        out.setdefault(slug, set()).add(f"(shell) {script}")
                    continue
                if block.get("name") not in WRITE_TOOLS:
                    continue
                raw = inp.get("file_path") or inp.get("notebook_path")
                if not isinstance(raw, str):
                    continue
                p = Path(raw)
                try:
                    rel = (p if p.is_absolute() else vault / p).resolve().relative_to(vault)
                except ValueError:
                    continue
                if research_file(rel.parts):
                    out.setdefault(rel.parts[1], set()).add(rel.as_posix())
    return out


def subagent_transcripts(transcript: Path) -> list[Path]:
    """The session's subagent transcripts, when Claude Code keeps them beside the
    main one (`<session>/subagents/*.jsonl`); none otherwise."""
    d = transcript.with_suffix("") / "subagents"
    return sorted(d.glob("*.jsonl")) if d.is_dir() else []


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
        paths = [rel, f"Projects/{slug}/Bitacora"]
        subs = subagent_transcripts(transcript)
        if subs:
            sub_rel = f"Projects/{slug}/_trazas-agente/cli/{day}__{session}/subagents"
            (vault / sub_rel).mkdir(parents=True, exist_ok=True)
            for s in subs:
                shutil.copyfile(s, vault / sub_rel / s.name)
            paths.append(sub_rel)
        log = vault / "Projects" / slug / "Bitacora" / "acciones.jsonl"
        if not already_logged(log, rel):
            refs = sorted(set(_REF.findall(" ".join(files))))
            n_notes = sum(1 for f in files if not f.startswith("(shell)"))
            n_shell = len(files) - n_notes
            written = action_log.append(log, {
                "actor": "agent",
                "by": "claude-code",
                "action": "cli_session",
                "event": "completed",
                "summary": (f"Sesión de Claude Code que modificó {n_notes} nota(s) de investigación"
                            + (f" y ejecutó {n_shell} script(s) de literatura" if n_shell else "")
                            + (f"; {len(subs)} transcripción(es) de subagentes" if subs else "")),
                "refs": refs,
                "trace": rel,
            })
            page = log.parent / f"{written['date']}.md"
            page.write_text(action_log.render(action_log.load(log), written["date"], page),
                            encoding="utf-8", newline="\n")
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
