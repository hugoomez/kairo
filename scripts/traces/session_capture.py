#!/usr/bin/env python3
"""SessionEnd hook: keep the transcript of a Claude Code session that changed
research notes, inside the vault, next to the project it touched.

Run by the plugin's ``SessionEnd`` hook (``hooks/hooks.json`` →
``scripts/hooks/kairo_hook.py session-end``). It reads the hook's stdin JSON (``session_id``, ``transcript_path``), scans the
transcript for Write / Edit / MultiEdit / NotebookEdit calls on files under
``Projects/<slug>/{Hipotesis,Experimentos,Claims,Producto,Manuscritos,Evolucion}/``
— and, for the literature path, on ``Estado-del-arte.md``, ``_busquedas/``,
``_vigilancia/`` and ``Notas de proyecto/`` — plus the shell commands that run a Kairo
literature script on a project (``lit_search.py``, ``ingest_paper.py``,
``check_sota.py``, ``lit_watch.py``, ``check_quotes.py``, ``export_bib.py``,
``import_library.py``, named by its ``Projects/<slug>`` folder or its
``--project PROJ-XXX`` id), and for every project touched:

* copies the transcript ONCE, into the first touched project (by folder name):
  ``Projects/<slug>/_trazas-agente/cli/<YYYY-MM-DD>__<session>.jsonl``
  (a resumed session overwrites its own copy — it is the same conversation),
  and the session's subagent transcripts, when Claude Code keeps them beside
  it (``<session>/subagents/*.jsonl``), to ``…__<session>/subagents/`` — so a
  state-of-the-art map can be followed back to each facet summary and the
  prompts that produced it;
* appends one ``cli_session`` entry to each touched project's action log, with
  that project's note ids as ``refs`` and the one copy as ``trace`` (once per
  session and file);
* re-renders today's notebook page and commits the copy and ``Bitacora/``
  alone (``git commit --only``).

Privacy. A transcript holds everything the session saw. Before it is copied,
secrets are replaced by ``«redactado»``: the values of environment variables
whose names contain KEY, TOKEN, SECRET, PASSWORD, PASS, AUTH or CREDENTIAL
(values longer than 8 characters), and common secret shapes — ``sk-…`` /
``sk-ant-…`` keys, GitHub tokens (``ghp_``, ``gho_``, ``ghu_``, ``ghs_``,
``ghr_``, ``github_pat_``), AWS key ids (``AKIA…``), Slack tokens
(``xoxb-``/``xoxa-``/``xoxp-``…), ``Bearer …``, ``api_key=`` / ``apikey=`` /
``token=`` / ``access_token=`` values and PEM private-key blocks. Each JSONL
line is redacted string by string, so the copy stays valid JSONL. A
heuristic: a secret of another shape is copied as is. The copy also keeps
third-party paper text that subagents read (the main thread's hook refuses
to read `_trazas-agente/`). ``KAIRO_CAPTURE_TRANSCRIPTS=off`` turns capture
off entirely (no copy, no log entry, no commit).

So work done in a plain session is as traceable as work launched from the
interface. Sessions that only read are not kept.

Always exits 0; problems go to stderr. Standard library only.
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
REDACTED = "«redactado»"
_SECRET_ENV_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASS|AUTH|CREDENTIAL", re.I)
_SECRET_SHAPES = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.S),
    re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{12,}\b"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{8,}"),
    re.compile(r"(?<=\bBearer )[A-Za-z0-9._~+/=-]{8,}", re.I),
)
_QUERY_SECRET = re.compile(r"(\b(?:api[_-]?key|apikey|token|access_token|auth_token|secret)=)([^&\s\"'<>]+)", re.I)


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


def _env_secrets() -> list[str]:
    vals = {v for k, v in os.environ.items() if _SECRET_ENV_NAME.search(k) and len(v or "") > 8}
    return sorted(vals, key=len, reverse=True)


def redact_text(text: str, env_secrets: list[str] | None = None) -> str:
    """`text` with secrets replaced by «redactado» (see the module docstring)."""
    for v in env_secrets if env_secrets is not None else _env_secrets():
        text = text.replace(v, REDACTED)
    for pat in _SECRET_SHAPES:
        text = pat.sub(REDACTED, text)
    return _QUERY_SECRET.sub(lambda m: m.group(1) + REDACTED, text)


def _redact_obj(obj, env_secrets: list[str]):
    if isinstance(obj, str):
        return redact_text(obj, env_secrets)
    if isinstance(obj, list):
        return [_redact_obj(x, env_secrets) for x in obj]
    if isinstance(obj, dict):
        return {redact_text(k, env_secrets): _redact_obj(v, env_secrets) for k, v in obj.items()}
    return obj


def copy_redacted(src: Path, dest: Path) -> None:
    """Copy a JSONL transcript with its secrets redacted, line by line: a JSON line
    is redacted string by string and written back as JSON (still valid JSONL);
    any other line is redacted as text."""
    env_secrets = _env_secrets()
    out = []
    with src.open(encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\r\n")
            try:
                obj = json.loads(line)
            except ValueError:
                out.append(redact_text(line, env_secrets))
                continue
            red = _redact_obj(obj, env_secrets)
            out.append(line if red == obj else json.dumps(red, ensure_ascii=False))
    dest.write_text("".join(x + "\n" for x in out), encoding="utf-8", newline="\n")


def already_logged(log: Path, trace: str) -> bool:
    if not log.exists():
        return False
    return any(f'"trace":"{trace}"' in ln for ln in log.read_text(encoding="utf-8").splitlines()[-500:])


def run(vault: Path, payload: dict, today: str | None = None) -> list[str]:
    transcript = Path(str(payload.get("transcript_path") or ""))
    session = re.sub(r"[^A-Za-z0-9-]", "", str(payload.get("session_id") or ""))[:36] or "sesion"
    if (os.environ.get("KAIRO_CAPTURE_TRANSCRIPTS") or "").strip().lower() in ("off", "0", "no", "false"):
        return []
    if not transcript.is_file():
        return []
    vault = vault.resolve()
    day = today or date.today().isoformat()
    logged = []
    rel = None                  # the one copy, in the first touched project
    subs = subagent_transcripts(transcript)
    for slug, files in sorted(touched(transcript, vault).items()):
        paths = [f"Projects/{slug}/Bitacora"]
        if rel is None:
            rel = f"Projects/{slug}/_trazas-agente/cli/{day}__{session}.jsonl"
            dest = vault / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            copy_redacted(transcript, dest)
            paths.append(rel)
            if subs:
                sub_rel = f"Projects/{slug}/_trazas-agente/cli/{day}__{session}/subagents"
                (vault / sub_rel).mkdir(parents=True, exist_ok=True)
                for s in subs:
                    copy_redacted(s, vault / sub_rel / s.name)
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
