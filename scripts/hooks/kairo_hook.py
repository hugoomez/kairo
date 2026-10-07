#!/usr/bin/env python3
"""The single entry point of every Kairo hook (declared in the plugin's hooks/hooks.json).

    kairo_hook.py pre-tool      PreToolUse  Read | Grep | Bash | PowerShell | mcp__*__get_note
    kairo_hook.py post-write    PostToolUse Write | Edit
    kairo_hook.py post-shell    PostToolUse Bash | PowerShell
    kairo_hook.py session-end   SessionEnd

Reads the hook's JSON on stdin and routes it:

  pre-tool     send_guard: refuse to read or name `send: never` notes and the
               model-written reading notes (exit 2 blocks the tool).
  post-write   by the written path —
                 Papers/**              Smart Connections re-index + SOTA staleness check
                 Projects/*/Hipotesis/  _digest.md + _ledger.md
                 Projects/*/Claims/     _ledger.md
  post-shell   Bitácora: log a `git commit` the shell call just made.
  session-end  keep the transcript of a session that wrote research notes.

The vault is found, in order, from:
  - the written or read path (the nearest ancestor holding both Papers/ and
    Projects/);
  - `$CLAUDE_PROJECT_DIR` or the session cwd, or its `vault/` subfolder;
  - `$KAIRO_VAULT`.
Outside a Kairo vault every subcommand is a silent no-op, so the plugin's
hooks cost nothing in unrelated sessions. No absolute path is configured
anywhere: scripts are found next to this file (`${CLAUDE_PLUGIN_ROOT}`), and
the Smart Connections re-indexer is found through the vault's own `.mcp.json`.

Every routed action appends one line to `~/.kairo/hook-events.jsonl`
(`KAIRO_STATE_DIR` overrides the folder): time, event, action, vault and
outcome. That is how you can see that a hook actually fired. Nothing from a
note is written there, only paths relative to the vault.

A failing action never breaks the session: everything but a send_guard
refusal exits 0 and logs the error. Standard library only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS / "security"))

TOOL = "kairo/kairo_hook@1.0.0"


# --------------------------------------------------------------------------
# Where things are
# --------------------------------------------------------------------------

def is_vault(p: Path) -> bool:
    return (p / "Papers").is_dir() and (p / "Projects").is_dir()


def vault_from_path(p: Path) -> Path | None:
    for cand in [p, *p.parents]:
        if is_vault(cand):
            return cand
    return None


def find_vault(payload: dict, path: str | None = None) -> Path | None:
    if path:
        base = Path(payload.get("cwd") or os.getcwd())
        pp = Path(path)
        v = vault_from_path(pp if pp.is_absolute() else base / pp)
        if v:
            return v
    for root in (os.environ.get("CLAUDE_PROJECT_DIR"), payload.get("cwd")):
        if root:
            for cand in (Path(root), Path(root) / "vault"):
                if is_vault(cand):
                    return cand.resolve()
    env = os.environ.get("KAIRO_VAULT")
    if env and is_vault(Path(env)):
        return Path(env).resolve()
    return None


def state_dir() -> Path:
    return Path(os.environ.get("KAIRO_STATE_DIR") or Path.home() / ".kairo")


LOG_MAX_BYTES = 1_000_000
ALLOWED_EVERY_S = 60


def record(event: str, action: str, vault: Path | None, outcome: str, detail: str = "") -> None:
    try:
        d = state_dir()
        d.mkdir(parents=True, exist_ok=True)
        log = d / "hook-events.jsonl"
        if outcome == "allowed":  # the hot path: prove it fires, at most once a minute
            mark = d / "pre-tool.last"
            if mark.exists() and time.time() - mark.stat().st_mtime < ALLOWED_EVERY_S:
                return
            mark.touch()
        if log.exists() and log.stat().st_size > LOG_MAX_BYTES:
            log.replace(d / "hook-events.1.jsonl")
        line = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, "action": action,
                "vault": str(vault) if vault else None, "outcome": outcome, "tool": TOOL}
        if detail:
            line["detail"] = detail[:300]
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass


def run_script(rel: str, args: list[str], payload: dict, timeout: int = 60) -> tuple[int, str]:
    r = subprocess.run([sys.executable, str(SCRIPTS / rel), *args], input=json.dumps(payload).encode("utf-8"),
                       capture_output=True, timeout=timeout,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    out = (r.stdout + r.stderr).decode("utf-8", errors="replace").strip()
    return r.returncode, out


def smart_connections_reindexer(vault: Path) -> Path | None:
    """cli-reindex.js next to the smart-connections server the vault's .mcp.json runs."""
    for cfg in (vault / ".mcp.json", vault.parent / ".mcp.json"):
        try:
            servers = json.loads(cfg.read_text(encoding="utf-8")).get("mcpServers", {})
        except (OSError, ValueError):
            continue
        sc = servers.get("smart-connections") or {}
        for a in sc.get("args") or []:
            if str(a).endswith(".js"):
                cand = Path(a).parent / "cli-reindex.js"
                if cand.is_file():
                    return cand
    return None


def rel(p: Path, vault: Path) -> str:
    try:
        return p.resolve().relative_to(vault.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------

def pre_tool(payload: dict) -> int:
    import isolation
    import send_guard
    tin = payload.get("tool_input") or {}
    vault = find_vault(payload, tin.get("file_path") or tin.get("path") or tin.get("notePath"))
    tool = str(payload.get("tool_name") or "")
    # isolated agents (fresh-verifier, screener, …) read their packet and nothing else — fail closed
    try:
        applies, reason, receipt = isolation.check_isolated(payload)
    except Exception as exc:
        applies = isolation.agent_name(payload) in isolation.ISOLATED_AGENTS
        reason, receipt = f"comprobación de aislamiento fallida ({exc!r})", None
    if applies:
        if reason:
            record("PreToolUse", "isolation", vault, "blocked", f"{isolation.agent_name(payload)} {tool}")
            print(f"Kairo: {reason}", file=sys.stderr)
            return 2
        try:
            isolation.record_receipt(receipt)
        except OSError as exc:
            print(f"Kairo: no se pudo registrar la lectura del paquete ({exc!r})", file=sys.stderr)
            return 2
        record("PreToolUse", "isolated_read", vault, "ok", f"{receipt['agent_type']} {receipt['sha256']}")
        return 0
    # Outside a vault only the path-based checks (Read, Grep) make sense; the
    # shell check scans the vault for flagged notes and needs one.
    if vault is None and tool in ("Bash", "PowerShell"):
        return 0
    try:
        reason = send_guard.decide(payload, vault)
    except Exception as exc:  # fail open, loudly
        record("PreToolUse", "send_guard", vault, "error", repr(exc))
        print(f"send_guard: internal error ({exc!r}); allowing", file=sys.stderr)
        return 0
    if reason:
        record("PreToolUse", "send_guard", vault, "blocked", tool)
        print(f"send_guard (Kairo): {reason}", file=sys.stderr)
        return 2
    try:
        reason = isolation.main_paper_read(payload)
    except Exception as exc:  # fail open, loudly (as send_guard)
        reason = None
        record("PreToolUse", "main_paper_read", vault, "error", repr(exc))
    if reason:
        record("PreToolUse", "main_paper_read", vault, "blocked", tool)
        print(f"Kairo: {reason}", file=sys.stderr)
        return 2
    if vault is not None:
        record("PreToolUse", "send_guard", vault, "allowed", tool)
    return 0


def post_write(payload: dict) -> int:
    fp = (payload.get("tool_input") or {}).get("file_path")
    if not fp:
        return 0
    vault = find_vault(payload, fp)
    if vault is None:
        return 0
    path = Path(fp) if Path(fp).is_absolute() else Path(payload.get("cwd") or os.getcwd()) / fp
    r = rel(path, vault)
    parts = r.split("/")
    actions: list[tuple[str, str, list[str]]] = []
    if parts[0] == "Papers":
        actions.append(("sota_staleness", "hooks/check_sota_staleness.py", []))
    if len(parts) >= 4 and parts[0] == "Projects" and parts[2] == "Hipotesis":
        actions.append(("digest", "hooks/regen_digest.py", []))
    if len(parts) >= 4 and parts[0] == "Projects" and parts[2] in ("Hipotesis", "Claims"):
        actions.append(("ledger", "ledger/build_graph.py", ["--hook"]))
    forwarded = {**payload, "tool_input": {**(payload.get("tool_input") or {}), "file_path": str(path)}}
    for name, script, args in actions:
        try:
            code, out = run_script(script, args, forwarded)
            record("PostToolUse", name, vault, "ok" if code == 0 else f"exit {code}", r)
            if out and name == "sota_staleness" and out.startswith("{"):
                print(out)  # additionalContext for the model
            elif out:
                print(out, file=sys.stderr)
        except Exception as exc:
            record("PostToolUse", name, vault, "error", repr(exc))
    if parts[0] == "Papers":
        js = smart_connections_reindexer(vault)
        if js is None:
            record("PostToolUse", "reindex", vault, "skipped", "no smart-connections server in .mcp.json")
        else:
            try:
                p = subprocess.run(["node", str(js), "--path", "Papers/", "--vault", str(vault)],
                                   capture_output=True, timeout=30)
                record("PostToolUse", "reindex", vault, "ok" if p.returncode == 0 else f"exit {p.returncode}", r)
            except (OSError, subprocess.SubprocessError) as exc:
                record("PostToolUse", "reindex", vault, "error", repr(exc))
    return 0


def post_shell(payload: dict) -> int:
    cmd = str((payload.get("tool_input") or {}).get("command") or "")
    if "commit" not in cmd:  # cheap prefilter; commit_hook decides for real
        return 0
    vault = find_vault(payload)
    if vault is None:
        return 0
    try:
        code, out = run_script("bitacora/commit_hook.py", ["--vault", str(vault)], payload)
        record("PostToolUse", "bitacora_commit", vault, "ok" if code == 0 else f"exit {code}", out[:200])
        if out:
            print(out, file=sys.stderr)
    except Exception as exc:
        record("PostToolUse", "bitacora_commit", vault, "error", repr(exc))
    return 0


def session_end(payload: dict) -> int:
    vault = find_vault(payload)
    if vault is None:
        return 0
    try:
        code, out = run_script("traces/session_capture.py", ["--vault", str(vault)], payload, timeout=120)
        record("SessionEnd", "session_capture", vault, "ok" if code == 0 else f"exit {code}", out[:200])
    except Exception as exc:
        record("SessionEnd", "session_capture", vault, "error", repr(exc))
    return 0


EVENTS = {"pre-tool": pre_tool, "post-write": post_write, "post-shell": post_shell, "session-end": session_end}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in EVENTS:
        print(f"usage: kairo_hook.py {{{'|'.join(EVENTS)}}}", file=sys.stderr)
        return 0
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return 0
    return EVENTS[argv[0]](payload)


if __name__ == "__main__":
    sys.exit(main())
