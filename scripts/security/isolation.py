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
       isolation.py prune [--older-than DAYS]          # delete packets/replies older than DAYS (90)

2. The main thread never reads a paper's text.
   The session that can run commands and write files must not have third-party
   text (a paper note: abstract, full text) in its context, where a sentence
   written to look like an instruction could steer it. Protected text
   (`protected_kind`; any case, `/` or `\\`):

       Papers/P-*.md, Papers/_fichas/P-*.md      paper notes and reading cards
       Papers/_fuentes/**                        the kept source bytes (html, pdf, xml, json, figures)
       Projects/*/_trazas-agente/**              captured session and subagent transcripts
       Projects/*/_busquedas/**/candidates.json  search candidates with abstracts
       Projects/*/_busquedas/**/raw/**, Projects/*/_vigilancia/raw-*/**
                                                 raw API answers (abstracts)

   In the main thread only (no `agent_id` in the hook input) the hook refuses:
     - `Read` and Smart Connections `get_note` of a protected file;
     - a content-mode `Grep` whose scope would reach one (its `glob`/`type`
       are honoured: `glob: "*.md"` over Projects/ passes);
     - inside a vault, a shell command with a reader (one list, shared with
       send_guard: viewers, filters, copiers, archivers, interpreters —
       `cat`, `Get-Content`, `cp`, `python -c`, `node -e`, `powershell -c`…)
       that names a protected path segment, globs into protected files, runs
       from inside a protected folder (`cd Papers; cat P-0001*`) or recurses
       over a folder holding one; and `git show`, `git diff`, `git log -p`,
       `git cat-file`, `git grep`, `git blame` run on the vault (`--stat`,
       `--name-only`, `--name-status`, `git status/add/commit/log` pass);
     - scripts that print paper text (`cite_text.py`, `paper_refs.py --text`,
       `lit_search.py show --with-abstracts`).
   Kairo's own scripts (`python <plugin>/scripts/<dir>/<name>.py …`) run even
   when an argument names Papers/. Subagents without a shell (`paper-reader`,
   `facet-summarizer`, …) and scripts read protected text.
   `KAIRO_ALLOW_MAIN_PAPER_READ=1` turns all this off; `=clean` lets the main
   thread `Read`/`get_note` a paper note `unclean_note` finds clean (cards,
   kept bytes, transcripts and search material stay closed).
   Not covered (stated): a program or script that opens a file its command
   line never names, a lit-watch run file (`_vigilancia/vigilancia-*.json`)
   or `screened.json`, which the skills still have the main thread read. A
   narrowing, not a boundary: the instruction in every skill is the primary
   control.

The store is `$KAIRO_PACKETS_DIR`, else `<state dir>/packets` (state dir:
`$KAIRO_STATE_DIR`, else `~/.kairo`) — outside the vault. Packets hold paper
text and replies hold agents' answers, so `prune` deletes those not modified
for 90 days (`--older-than`); the SessionEnd hook runs it at most once a day.
Standard library only.
"""

from __future__ import annotations

import argparse
import glob as _glob
import hashlib
import json
import os
import re
import sys
import time
from fnmatch import fnmatch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from send_guard import _RECURSIVE, SHELL_READER, SKIP_DIRS  # noqa: E402  (one reader list for both guards)

ISOLATED_AGENTS = frozenset({"fresh-verifier", "devils-advocate", "novelty-judge", "screener"})
PACKET_NAME = re.compile(r"^[0-9a-f]{64}\.md$")
RECEIPTS = "packet-reads.jsonl"
PAPER_NAME = re.compile(r"^P-\d{4,5}\b.*\.md$", re.I)
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
# a protected folder or file named in a shell command (backslashes already turned
# into `/`, lower case): Papers (the notes; not the plugin's own scripts/papers/),
# its cards and kept bytes, captured transcripts, search material with abstracts
_PROTECTED_MENTION = re.compile(
    r"(?<!scripts/)(?<![\w.-])(?:papers|_fichas|_fuentes|_trazas-agente)(?![\w.-])"
    r"|_busquedas/\S*?(?:candidates\.json|/raw(?![\w.-]))|_vigilancia/raw-")
_RECURSIVE_RG = re.compile(r"(?:^|[\s;|&(\"'])(?:rg|ag|ack)(?:\.exe)?\b", re.I)
_GIT = re.compile(r"(?:^|[\s(\"'])git(?:\.exe)?\s+((?:(?:-C|-c|--git-dir|--work-tree)\s+(?:\"[^\"]*\"|'[^']*'|\S+)\s+"
                  r"|--?[\w-]+(?:=\S+)?\s+)*)(show|diff|difftool|cat-file|grep|blame|annotate|log|whatchanged|"
                  r"format-patch|archive)\b(.*)", re.I | re.S)
_GIT_NAMES_ONLY = re.compile(r"(?:^|\s)--(?:stat|name-only|name-status|shortstat|numstat|summary|raw)\b")
_GIT_PATCH = re.compile(r"(?:^|\s)(?:-[a-zA-Z0-9]*[pu][a-zA-Z0-9]*|--patch|--full-diff|-L\S*|--cc|-c)(?=\s|$)")
_CD = re.compile(r"^\s*(?:cd|pushd|chdir|set-location|sl)\s+(?:/d\s+)?(\"[^\"]*\"|'[^']*'|\S+)\s*$", re.I)
_PY_SCRIPT = re.compile(r"""^\s*(?:&\s*)?["']?(?:[^\s"']*[/\\])?(?:python3?|py)(?:\.exe)?["']?\s+(?:-[uBIEsOX]\w*\s+)*"""
                        r"""(?:"([^"]+\.py)"|'([^']+\.py)'|(\S+\.py))(?:\s|$)""", re.I)
_PLUGIN_SCRIPT = re.compile(r"(?:^|/)scripts/([\w-]+)/([\w-]+\.py)$", re.I)
_GREP_TYPES = {"md": (".md", ".markdown", ".mdx", ".mkd"), "markdown": (".md", ".markdown", ".mdx", ".mkd"),
               "json": (".json", ".jsonl", ".geojson", ".sarif"), "html": (".html", ".htm", ".xhtml"),
               "xml": (".xml", ".xsd", ".xsl", ".rss", ".atom"), "txt": (".txt",), "py": (".py", ".pyi"),
               "js": (".js", ".mjs", ".cjs", ".jsx"), "ts": (".ts", ".tsx"), "toml": (".toml",),
               "yaml": (".yaml", ".yml"), "csv": (".csv",)}
# scripts whose output is a paper's own text (for an interface, never the main session)
_TEXT_SCRIPTS = re.compile(r"\bcite_text\.py\b|\bpaper_refs\.py\b.*\s--text\b", re.I | re.S)
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
#    own transcript) and the model it ran on (`message.model` in that transcript,
#    None when unknown). The hook stores it by content (`<state>/replies/<sha>.txt`)
#    and records which packet(s) that agent read, so a script that records the
#    agent's verdict can check it against what the agent actually said — the
#    orchestrator saves the reply, but can no longer change it unseen.
# --------------------------------------------------------------------------

REPLIES = "agent-replies.jsonl"


def replies_dir() -> Path:
    return state_dir() / "replies"


def _assistant_messages(transcript: Path) -> list[dict]:
    try:
        lines = transcript.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for raw in lines:
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        msg = obj.get("message") if isinstance(obj, dict) else None
        if isinstance(msg, dict) and msg.get("role", obj.get("type")) == "assistant":
            out.append(msg)
    return out


def _last_assistant_text(transcript: Path) -> str:
    text = ""
    for msg in _assistant_messages(transcript):
        content = msg.get("content")
        parts = [content] if isinstance(content, str) else [
            b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text"]
        joined = "".join(parts).strip()
        if joined:
            text = joined
    return text


def _transcript_model(transcript: Path) -> str | None:
    """The model the agent actually ran on: the last real `message.model` of its
    own transcript (`<synthetic>` entries are Claude Code's, not a model's)."""
    model = None
    for msg in _assistant_messages(transcript):
        m = msg.get("model")
        if isinstance(m, str) and m.strip() and not m.startswith("<"):
            model = m.strip()
    return model


def capture_reply(event: dict) -> dict | None:
    """SubagentStop: keep an isolated agent's final answer, tied to the packets it read."""
    name = agent_name(event)
    aid = event.get("agent_id")
    if name not in ISOLATED_AGENTS or not aid:
        return None
    text = str(event.get("last_assistant_message") or "").strip()
    transcript = Path(str(event["agent_transcript_path"])) if event.get("agent_transcript_path") else None
    if not text and transcript:
        text = _last_assistant_text(transcript)
    if not text:
        return None
    model = _transcript_model(transcript) if transcript else None
    packets = sorted({r["sha256"] for r in _receipts() if r.get("agent_id") == aid and r.get("sha256")})
    data = text.encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    d = replies_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sha}.txt").write_bytes(data)
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "agent_type": name, "agent_id": aid,
           "packets": packets, "reply_sha256": sha, "model": model}
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
    `agent`, when given), oldest first, each with its `text` and the `model`
    the agent ran on (from its own transcript; None when unknown)."""
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
        r.setdefault("model", None)            # stored before the model was recorded
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


def protected_kind(p: Path | str) -> str | None:
    """What third-party text `p` holds, or None. Path-based, case-insensitive,
    `\\` and `/` alike:

      Papers/P-*.md                          a paper note («nota de paper»)
      Papers/_fichas/P-*.md                  its reading card (quotes the paper)
      Papers/_fuentes/**                     the kept source bytes (html, pdf, xml, json, figures)
      Projects/*/_trazas-agente/**           captured transcripts (subagents read papers in them)
      Projects/*/_busquedas/**/candidates.json, Projects/*/_busquedas/**/raw/**
      Projects/*/_vigilancia/raw-*/**        search and watch material holding abstracts
    """
    parts = [x.lower() for x in str(p).replace("\\", "/").split("/") if x not in ("", ".")]
    for i, a in enumerate(parts):
        rest = parts[i + 1:]
        if a == "papers":
            if len(rest) == 1 and PAPER_NAME.match(rest[0]):
                return "nota de paper"
            if len(rest) == 2 and rest[0] == "_fichas" and PAPER_NAME.match(rest[1]):
                return "ficha de paper"
            if len(rest) >= 2 and rest[0] == "_fuentes":
                return "bytes de la fuente de un paper"
        if a == "projects" and len(rest) >= 3:
            sub, below = rest[1], rest[2:]
            if sub == "_trazas-agente":
                return "transcripción de una sesión"
            if sub == "_busquedas" and (below[-1] == "candidates.json" or "raw" in below[:-1]):
                return "material de búsqueda con abstracts"
            if sub == "_vigilancia" and len(below) >= 2 and below[0].startswith("raw-"):
                return "material de vigilancia con abstracts"
    return None


def _is_paper_note(p: Path) -> bool:
    """A paper note, or a paper's reading card (`Papers/_fichas/P-*.md`: it quotes the paper)."""
    return protected_kind(p) in ("nota de paper", "ficha de paper")


def _inside_protected(d: Path) -> bool:
    """`d` is a folder whose files are protected (Papers/, Papers/_fichas/, _fuentes/, a transcripts folder…)."""
    return any(protected_kind(d / probe) for probe in ("P-0000.md", "x"))


def _glob_admits(f: Path, scope: Path, glob: str, gtype: str) -> bool:
    """Would ripgrep, given Grep's `glob` and `type`, search file `f` under `scope`?
    In doubt it would (the guard errs toward refusing)."""
    name = f.name.lower()
    if gtype and gtype.lower() in _GREP_TYPES and not name.endswith(_GREP_TYPES[gtype.lower()]):
        return False
    if not glob:
        return True
    try:
        rel = f.relative_to(scope).as_posix().lower()
    except ValueError:
        rel = f.as_posix().lower()
    pats = [glob.strip()]
    m = re.match(r"^(.*)\{([^{}]*)\}(.*)$", pats[0])
    if m:
        pats = [m.group(1) + alt + m.group(3) for alt in m.group(2).split(",")]
    hit = any(fnmatch(name, q.lstrip("!").lower()) or fnmatch(rel, q.lstrip("!").lower())
              or fnmatch("/" + rel, q.lstrip("!").lower()) or fnmatch(rel, "*/" + q.lstrip("!").lower())
              for q in pats)
    return not hit if glob.lstrip().startswith("!") else hit


def _holds_protected(scope: Path, glob: str = "", gtype: str = "") -> bool:
    """Would a content search over `scope` (with Grep's glob/type) reach protected text?"""
    if scope.is_file():
        return bool(protected_kind(scope)) and _glob_admits(scope, scope.parent, glob, gtype)
    if not scope.is_dir():
        return False
    for dirpath, dirnames, filenames in os.walk(scope):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            f = Path(dirpath) / name
            if protected_kind(f) and _glob_admits(f, scope, glob, gtype):
                return True
    return False


def _split_shell(cmd: str) -> list[str]:
    """The simple commands of a command line, split on `;`, `&&`, `||`, `|`, `&` and
    newlines outside quotes. A heredoc keeps the whole line together (its body is
    the program's input)."""
    if "<<" in cmd:
        return [cmd]
    out, cur, quote, i = [], [], None, 0
    while i < len(cmd):
        c = cmd[i]
        if quote:
            cur.append(c)
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
            cur.append(c)
        elif c in ";\n" or (c == "|") or (c == "&" and not (cur and cur[-1] in "<>") and cmd[i + 1:i + 2] != ">"):
            out.append("".join(cur))
            cur = []
            if cmd[i:i + 2] in ("&&", "||"):
                i += 1
        else:
            cur.append(c)
        i += 1
    out.append("".join(cur))
    return [s.strip() for s in out if s.strip()]


def _tokens(seg: str) -> list[str]:
    return [a or b or c for a, b, c in re.findall(r'"([^"]*)"|\'([^\']*)\'|(\S+)', seg)]


def _plugin_script(seg: str) -> bool:
    """`python <this plugin>/scripts/<dir>/<name>.py …`: Kairo's own script, which
    may take a Papers path as an argument (it prints no paper text — the scripts
    that do are refused by name above)."""
    m = _PY_SCRIPT.match(seg)
    if not m:
        return False
    script = (m.group(1) or m.group(2) or m.group(3)).replace("\\", "/")
    sm = _PLUGIN_SCRIPT.search(script)
    if not sm:
        return False
    head = script[:sm.start()].lower()
    if _PROTECTED_MENTION.search(head + "/"):
        return False
    if re.fullmatch(r"(?:\$\{?claude_plugin_root\}?|<plugin>)/?", head) or head in ("", "./"):
        return (PLUGIN_ROOT / "scripts" / sm.group(1) / sm.group(2)).is_file()
    try:
        return Path(script).resolve() == (PLUGIN_ROOT / "scripts" / sm.group(1) / sm.group(2)).resolve() \
            and Path(script).is_file()
    except OSError:
        return False


def _git_targets_vault(git_opts: str, cwd: Path, vault: Path, cmd: str) -> bool:
    m = re.search(r"-C\s+(\"[^\"]*\"|'[^']*'|\S+)", git_opts)
    d = cwd
    if m:
        d = Path(os.path.expanduser(m.group(1).strip("\"'")))
        d = d if d.is_absolute() else cwd / d
    try:
        d, v = d.resolve(), vault.resolve()
    except OSError:
        return True
    return d == v or v in d.parents or (d == v.parent and (d / ".git").exists())


def _shell_reads_protected(cmd: str, cwd: Path, vault: Path) -> str | None:
    """Why a shell command in the main thread would read protected text, or None.
    Best effort: a program can still open a file no command line names."""
    here = cwd
    for seg in _split_shell(cmd):
        cd = _CD.match(seg)
        if cd:
            t = Path(os.path.expanduser(cd.group(1).strip("\"'")))
            here = t if t.is_absolute() else here / t
            continue
        norm = seg.replace("\\", "/").lower()
        g = _GIT.search(seg)
        if g:
            sub, rest = g.group(2).lower(), g.group(3)
            names_only = bool(_GIT_NAMES_ONLY.search(rest)) and not _GIT_PATCH.search(rest)
            content = (sub in ("log", "whatchanged") and bool(_GIT_PATCH.search(rest))) or \
                      (sub == "cat-file" and not re.search(r"(?:^|\s)-[tse](?=\s|$)", rest)) or \
                      (sub in ("show", "diff", "difftool") and not names_only) or \
                      sub in ("grep", "blame", "annotate", "format-patch", "archive")
            if content and _git_targets_vault(g.group(1) or "", here, vault, cmd):
                return f"`git {sub}` imprime el contenido de notas del vault (también de papers)"
            continue
        if not SHELL_READER.search(seg) or _plugin_script(seg):
            continue
        if _PROTECTED_MENTION.search(norm):
            return "el comando lee notas de Papers/, sus bytes guardados, transcripciones o material con abstracts"
        if _inside_protected(here):
            return f"el comando lee desde {here.name}/, una carpeta con texto de papers"
        recursive = bool(_RECURSIVE.search(seg)) or bool(_RECURSIVE_RG.search(seg))
        dirs = []
        for tok in _tokens(seg)[1:]:
            if tok.startswith("-") or len(tok) > 400:
                continue
            base = Path(os.path.expanduser(tok))
            base = base if base.is_absolute() else here / base
            if any(ch in tok for ch in "*?["):
                try:
                    matches = [Path(x) for x in _glob.glob(str(base))[:500]]
                except (OSError, ValueError):
                    matches = []
                if any(protected_kind(x) for x in matches):
                    return f"«{tok}» alcanza texto de papers"
            elif base.is_dir():
                dirs.append(base)
        if recursive:
            for d in dirs or [here]:
                if _holds_protected(d):
                    return f"el comando recorre {d.name or d}/, que contiene texto de papers"
    return None


HIDDEN_MARK = "[texto oculto en la fuente"
MIN_CONVERTER = (1, 5, 0)          # verbatim_fulltext marks hidden text in HTML and PDF from this version


def unclean_note(p: Path) -> str | None:
    """Why a paper note is not clean enough for the main thread to read, or None.

    Clean: written by ingest_paper.py from kept bytes with a converter that marks
    hidden text (≥ 1.5.0), no `texto_sospechoso` at ingestion, no hidden-text
    mark in it, and nothing in its whole text that reads like an instruction to
    a model today (the patterns are re-run on every read, so a newer list
    applies to old notes)."""
    from untrusted import suspicious
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        return f"no se pudo leer ({e})"
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    fm = m.group(1) if m else ""
    if re.search(r"(?m)^texto_sospechoso:\s*\S", fm):
        return "la ingesta marcó texto sospechoso"
    fuentes = re.search(r"(?m)^fuentes:\s*[\"']?([^\"'\n]+)", fm)
    if not fuentes:
        return "no la escribió ingest_paper.py desde bytes guardados"
    vault = p.parent.parent
    try:
        conv = json.loads((vault / fuentes.group(1).strip()).read_text(encoding="utf-8")).get("converter") or ""
    except (OSError, ValueError):
        return "su manifiesto de fuentes no se puede leer"
    vm = re.search(r"@(\d+)\.(\d+)\.(\d+)", conv)
    if not vm or tuple(int(x) for x in vm.groups()) < MIN_CONVERTER:
        return f"convertida con {conv or 'un convertidor desconocido'}, anterior a la detección de texto oculto"
    if HIDDEN_MARK in text:
        return "contiene texto oculto en la fuente"
    flags = suspicious(text)
    if flags:
        return "texto que parece una instrucción: " + ", ".join(flags)
    return None


def main_paper_read(event: dict, vault: Path | None = None) -> str | None:
    """A block reason when the main thread would put paper text in its own context.

    `vault` is the Kairo vault the hook found (else the nearest ancestor of the
    session cwd holding Papers/ and Projects/); the shell rules need one."""
    if event.get("agent_id") or os.environ.get("KAIRO_ALLOW_MAIN_PAPER_READ") == "1":
        return None
    tool = str(event.get("tool_name") or "")
    tin = event.get("tool_input") or {}
    cwd = Path(event.get("cwd") or os.getcwd())
    why = ("el hilo principal no lee texto de papers (puede contener instrucciones dirigidas a un modelo): "
           "delega la lectura en el subagente `paper-reader` (sin shell ni red) o usa los scripts de Kairo")
    if vault is None:
        vault = next((c for c in (cwd, *cwd.parents) if (c / "Papers").is_dir() and (c / "Projects").is_dir()), None)
    get_note = tool.startswith("mcp__") and tool.endswith("__get_note")
    if tool == "Read" or get_note:
        fp = str(tin.get("notePath" if get_note else "file_path") or "")
        if not fp:
            return None
        p = _resolve(fp.replace("\\", "/"), (vault or cwd) if get_note else cwd)
        kind = protected_kind(p)
        if not kind:
            return None
        if kind == "nota de paper" and os.environ.get("KAIRO_ALLOW_MAIN_PAPER_READ") == "clean":
            dirty = unclean_note(p)
            if dirty is None:
                return None
            return (f"{Path(fp).name}: KAIRO_ALLOW_MAIN_PAPER_READ=clean solo abre notas limpias, y esta no "
                    f"lo es ({dirty}): {why}.")
        return f"{Path(fp).name} ({kind}): {why}."
    if tool == "Grep":
        if (tin.get("output_mode") or "files_with_matches") != "content":
            return None
        scopes = tin.get("paths") or [tin.get("path") or "."]
        scopes = [scopes] if isinstance(scopes, str) else scopes
        glob, gtype = str(tin.get("glob") or ""), str(tin.get("type") or "")
        if any(_holds_protected(_resolve(str(s), cwd), glob, gtype) for s in scopes):
            return (f"Grep en modo content sobre texto de papers (notas, fichas, _fuentes/, transcripciones o "
                    f"material de búsqueda con abstracts): {why}; acota `path` o usa `glob: \"*.md\"` fuera de Papers/.")
        return None
    if tool in ("Bash", "PowerShell"):
        cmd = str(tin.get("command") or "")
        if _TEXT_SCRIPTS.search(cmd):
            return f"el script imprime texto de un paper (cite_text.py, paper_refs.py --text): {why}."
        if _ABSTRACTS.search(cmd):
            return ("lit_search.py show --with-abstracts imprime abstracts de terceros en el hilo principal: "
                    "pasa la ruta del paquete de la página a un `screener` (lo lee él), o quita --with-abstracts.")
        if vault is not None:
            reason = _shell_reads_protected(cmd, cwd, vault)
            if reason:
                return f"{reason}: {why}."
    return None


PRUNE_DAYS = 90
_REPLY_NAME = re.compile(r"^[0-9a-f]{64}\.txt$")


def prune(older_than_days: float = PRUNE_DAYS) -> dict:
    """Delete packets (`<sha>.md`) and stored replies (`<sha>.txt`) not modified for
    `older_than_days` days. They hold paper text and agents' answers; nothing
    younger than the threshold is ever deleted, and no other file is touched.
    The receipt and reply logs (ids and hashes only) are kept."""
    if not older_than_days or older_than_days <= 0:
        raise ValueError("older_than_days must be > 0")
    cutoff = time.time() - older_than_days * 86400
    deleted, kept = 0, 0
    for d, name in ((packets_dir(), PACKET_NAME), (replies_dir(), _REPLY_NAME)):
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if not name.match(f.name) or not f.is_file():
                continue
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    deleted += 1
                else:
                    kept += 1
            except OSError:
                kept += 1
    return {"older_than_days": older_than_days, "deleted": deleted, "kept": kept}


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
    pr = sub.add_parser("prune", help="delete packets and stored replies older than --older-than days")
    pr.add_argument("--older-than", type=float, default=PRUNE_DAYS, metavar="DAYS")
    a = ap.parse_args(argv)
    if a.cmd == "prune":
        try:
            print(json.dumps(prune(a.older_than), ensure_ascii=False))
        except ValueError as e:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
            return 1
        return 0
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
