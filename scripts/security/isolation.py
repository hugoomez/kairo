#!/usr/bin/env python3
"""Mechanical isolation for the agents that must see only their packet, and
for the main thread, which must never read a paper's text.

1. Packets travel by file, never retyped.
   An isolated agent (fresh-verifier, devils-advocate, novelty-judge,
   screener) gets one tool, `Read`, and the vault's PreToolUse hook lets it
   read exactly one kind of file: a packet in the packet store whose name is
   the sha256 of its content (`<store>/<sha256>.md`). Everything else it
   tries is refused. Each read leaves a receipt (agent type, agent id,
   sha256) in `packet-reads.jsonl`, so `received` can prove which packet an
   agent actually read — the orchestrator never types a packet into a prompt,
   and cannot change one without changing its name.

       isolation.py store <file>                       # → {"path", "sha256"}
       isolation.py received --sha256 H [--agent X]    # exit 0 read · 3 not read

2. The main thread never reads a paper's text.
   The session that can run commands and write files must not have third-party
   text (a paper note: abstract, full text) in its context, where a sentence
   written to look like an instruction could steer it. The hook refuses, in the
   main thread only (no `agent_id` in the hook input), a `Read` of a
   `Papers/P-*.md` note, a content-mode `Grep` whose scope holds one, and a
   shell reader (`cat`, `head`, `grep`, `Get-Content`, …) naming `Papers/`.
   Subagents without a shell (`paper-reader`, `facet-summarizer`, …) read them;
   scripts read them. `KAIRO_ALLOW_MAIN_PAPER_READ=1` turns this off.
   A shell command can still reach a note without naming it: best effort, the
   instruction in every skill is the primary control.

The store is `$KAIRO_PACKETS_DIR`, else `<state dir>/packets` (state dir:
`$KAIRO_STATE_DIR`, else `~/.kairo`) — outside the vault. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

ISOLATED_AGENTS = frozenset({"fresh-verifier", "devils-advocate", "novelty-judge", "screener"})
PACKET_NAME = re.compile(r"^[0-9a-f]{64}\.md$")
RECEIPTS = "packet-reads.jsonl"
PAPER_NAME = re.compile(r"^P-\d{4,5}\b.*\.md$", re.I)
_READERS = re.compile(r"(?:^|[\s;|&(`])(?:cat|type|more|less|head|tail|sed|awk|grep|egrep|fgrep|rg|findstr|"
                      r"get-content|gc|select-string|sls|bat|nl|strings|od|xxd)(?:\.exe)?\b", re.I)
_PAPERS_PATH = re.compile(r"papers[/\\](?:p-\d|\*|[\"']?\s|[\"']?$)", re.I)
# scripts whose output is a paper's own text (for an interface, never the main session)
_TEXT_SCRIPTS = re.compile(r"\bcite_text\.py\b", re.I)
# a search listing that prints the candidates' abstracts (third-party text) into the caller's context
_ABSTRACTS = re.compile(r"\blit_search\.py\b.*\bshow\b.*--with-abstracts", re.I | re.S)


def state_dir() -> Path:
    return Path(os.environ.get("KAIRO_STATE_DIR") or Path.home() / ".kairo")


def packets_dir() -> Path:
    return Path(os.environ.get("KAIRO_PACKETS_DIR") or state_dir() / "packets")


def store(content: str | bytes) -> dict:
    """Write a packet to the store under its own sha256; the same content gives the same file."""
    data = content.encode("utf-8") if isinstance(content, str) else content
    sha = hashlib.sha256(data).hexdigest()
    d = packets_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sha}.md"
    if not p.is_file() or p.read_bytes() != data:
        p.write_bytes(data)
    return {"path": str(p), "sha256": sha}


def agent_name(event: dict) -> str | None:
    """The agent's own name: `kairo:screener` and `screener` are the same agent."""
    t = str(event.get("agent_type") or "").strip()
    return t.rsplit(":", 1)[-1] if t else None


def _resolve(fp: str, cwd: Path) -> Path:
    p = Path(fp.strip().strip("\"'"))
    return (p if p.is_absolute() else cwd / p).resolve()


def check_isolated(event: dict) -> tuple[bool, str | None, dict | None]:
    """(applies, block reason or None, receipt for an allowed read).

    Applies only to an isolated agent; for it, everything but a Read of an
    intact packet in the store is refused (fail closed)."""
    name = agent_name(event)
    if name not in ISOLATED_AGENTS:
        return False, None, None
    tool = str(event.get("tool_name") or "")
    if tool != "Read":
        return True, f"agente aislado `{name}`: solo puede leer su paquete (Read); `{tool}` no.", None
    cwd = Path(event.get("cwd") or os.getcwd())
    try:
        p = _resolve(str((event.get("tool_input") or {}).get("file_path") or ""), cwd)
        store_dir = packets_dir().resolve()
        if p.parent != store_dir or not PACKET_NAME.match(p.name):
            return True, (f"agente aislado `{name}`: solo lee paquetes del almacén ({store_dir}); "
                          f"{p.name} no lo es."), None
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError as e:
        return True, f"agente aislado `{name}`: no se pudo comprobar el paquete ({e}).", None
    if f"{sha}.md" != p.name:
        return True, (f"agente aislado `{name}`: el paquete {p.name} cambió después de guardarse "
                      f"(su sha256 es {sha}); vuelve a construirlo."), None
    return True, None, {"sha256": sha, "agent_type": name, "agent_id": event.get("agent_id")}


def record_receipt(receipt: dict) -> None:
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    line = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **receipt}
    with open(d / RECEIPTS, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")


def received(sha: str, agent: str | None = None) -> list[dict]:
    """Every recorded read of packet `sha` (by `agent`, when given)."""
    return [r for r in _receipts()
            if r.get("sha256") == sha and (agent is None or r.get("agent_type") == agent.rsplit(":", 1)[-1])]


# --------------------------------------------------------------------------
# 3. Replies travel by hook, never retyped.
#    When an isolated agent stops, Claude Code's SubagentStop hook hands over its
#    final answer (`last_assistant_message`, else the last assistant text of its
#    own transcript). The hook stores it by content (`<state>/replies/<sha>.txt`)
#    and records which packet(s) that agent read, so a script that records the
#    agent's verdict can check it against what the agent actually said — the
#    orchestrator saves the reply, but can no longer change it unseen.
# --------------------------------------------------------------------------

REPLIES = "agent-replies.jsonl"


def replies_dir() -> Path:
    return state_dir() / "replies"


def _last_assistant_text(transcript: Path) -> str:
    text = ""
    try:
        lines = transcript.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for raw in lines:
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        msg = obj.get("message") if isinstance(obj, dict) else None
        if not isinstance(msg, dict) or msg.get("role", obj.get("type")) != "assistant":
            continue
        content = msg.get("content")
        parts = [content] if isinstance(content, str) else [
            b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text"]
        joined = "".join(parts).strip()
        if joined:
            text = joined
    return text


def capture_reply(event: dict) -> dict | None:
    """SubagentStop: keep an isolated agent's final answer, tied to the packets it read."""
    name = agent_name(event)
    aid = event.get("agent_id")
    if name not in ISOLATED_AGENTS or not aid:
        return None
    text = str(event.get("last_assistant_message") or "").strip()
    if not text and event.get("agent_transcript_path"):
        text = _last_assistant_text(Path(str(event["agent_transcript_path"])))
    if not text:
        return None
    packets = sorted({r["sha256"] for r in _receipts() if r.get("agent_id") == aid and r.get("sha256")})
    data = text.encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    d = replies_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sha}.txt").write_bytes(data)
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "agent_type": name, "agent_id": aid,
           "packets": packets, "reply_sha256": sha}
    with open(state_dir() / REPLIES, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def _receipts() -> list[dict]:
    f = state_dir() / RECEIPTS
    if not f.is_file():
        return []
    out = []
    for raw in f.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out


def replies(packet_sha: str, agent: str | None = None) -> list[dict]:
    """The stored final answers of the agents that read packet `packet_sha` (by
    `agent`, when given), oldest first, each with its `text`."""
    f = state_dir() / REPLIES
    if not f.is_file():
        return []
    out = []
    for raw in f.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(raw)
        except ValueError:
            continue
        if packet_sha not in (r.get("packets") or []):
            continue
        if agent is not None and r.get("agent_type") != agent.rsplit(":", 1)[-1]:
            continue
        try:
            r["text"] = (replies_dir() / f"{r['reply_sha256']}.txt").read_text(encoding="utf-8")
        except OSError:
            continue
        out.append(r)
    return out


def json_of(text: str):
    """The JSON object an agent's reply carries (bare, or in its last ```json
    fence — the block an agent's instructions put at the end), or None."""
    fences = re.findall(r"```(?:json)?\s*\n?(\{.*?\})\s*\n?```", text, re.S)
    try:
        return json.loads(fences[-1] if fences else text)
    except (ValueError, TypeError):
        return None


def reply_matches(packet_sha: str, agent: str, obj) -> bool | None:
    """Does `obj` equal the JSON of a stored reply of `agent` to this packet?
    None when no reply was stored (a session without Kairo's hooks)."""
    got = replies(packet_sha, agent)
    if not got:
        return None
    return any(json_of(r["text"]) == obj for r in got)


def _is_paper_note(p: Path) -> bool:
    return p.parent.name == "Papers" and bool(PAPER_NAME.match(p.name)) and p.suffix.lower() == ".md"


def _holds_papers(scope: Path) -> bool:
    if scope.is_file():
        return _is_paper_note(scope)
    for d in (scope, scope / "Papers"):
        if d.name == "Papers" and d.is_dir() and any(_is_paper_note(x) for x in d.glob("P-*.md")):
            return True
    return False


def main_paper_read(event: dict) -> str | None:
    """A block reason when the main thread would put paper text in its own context."""
    if event.get("agent_id") or os.environ.get("KAIRO_ALLOW_MAIN_PAPER_READ") == "1":
        return None
    tool = str(event.get("tool_name") or "")
    tin = event.get("tool_input") or {}
    cwd = Path(event.get("cwd") or os.getcwd())
    why = ("el hilo principal no lee texto de papers (puede contener instrucciones dirigidas a un modelo): "
           "delega la lectura en el subagente `paper-reader` (sin shell ni red) o usa los scripts de Kairo")
    if tool == "Read":
        fp = str(tin.get("file_path") or "")
        if fp and _is_paper_note(_resolve(fp, cwd)):
            return f"{Path(fp).name}: {why}."
        return None
    if tool == "Grep":
        if (tin.get("output_mode") or "files_with_matches") != "content":
            return None
        scopes = tin.get("paths") or [tin.get("path") or "."]
        scopes = [scopes] if isinstance(scopes, str) else scopes
        glob = str(tin.get("glob") or "")
        if glob.startswith("!") and ("papers" in glob.lower() or "p-" in glob.lower()):
            return None
        if any(_holds_papers(_resolve(str(s), cwd)) for s in scopes):
            return f"Grep en modo content sobre notas de Papers/: {why}; acota `path` (p. ej. a Projects/)."
        return None
    if tool in ("Bash", "PowerShell"):
        cmd = str(tin.get("command") or "")
        if _READERS.search(cmd) and _PAPERS_PATH.search(cmd + " "):
            return f"el comando lee notas de Papers/: {why}."
        if _TEXT_SCRIPTS.search(cmd):
            return f"cite_text.py imprime texto de un paper: {why}."
        if _ABSTRACTS.search(cmd):
            return ("lit_search.py show --with-abstracts imprime abstracts de terceros en el hilo principal: "
                    "pasa la ruta del paquete de la página a un `screener` (lo lee él), o quita --with-abstracts.")
    return None


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("store", help="copy a packet into the store under its sha256")
    s.add_argument("file", type=Path)
    r = sub.add_parser("received", help="did an isolated agent read this packet?")
    r.add_argument("--sha256", required=True)
    r.add_argument("--agent")
    a = ap.parse_args(argv)
    if a.cmd == "store":
        try:
            print(json.dumps(store(a.file.read_bytes()), ensure_ascii=False))
        except OSError as e:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
            return 1
        return 0
    got = received(a.sha256, a.agent)
    print(json.dumps({"sha256": a.sha256, "agent": a.agent, "reads": got}, ensure_ascii=False))
    return 0 if got else 3


if __name__ == "__main__":
    raise SystemExit(main())
