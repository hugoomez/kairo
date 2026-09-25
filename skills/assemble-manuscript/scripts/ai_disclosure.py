#!/usr/bin/env python3
"""Generate a manuscript's "Declaración de uso de IA" from Kairo's own records.

Called by `assemble-manuscript` (its AI-disclosure step). Reads -- never
writes -- a Kairo vault and builds an AI-use disclosure section for one
publication thread, organized per research stage, stating what the records
show an AI system did and what the records show the researcher did.

Records read (all of them, for every note collected):
  - hypotheses of the thread (`Projects/<slug>/Hipotesis/*.md` with
    `paper_thread: <thread>`, or the explicit `--hypotheses` list = the ones
    that cleared assemble-manuscript's gate): `generated_by`, append-only
    `history`, `origin_flag`, `needs_human_review`, `## Revisión del ciclo`;
  - their adjudicating experiments (`linked_experiment` + experiments whose
    `hypothesis:` is one of them): `frozen_at`, `frozen_commit`, `tier`,
    `analysis_plan`, `role` / `rung` (absent = confirmatory / unknown, never an
    error), `environment.tools`, `method_provenance`, `status`,
    `experiment_validity`, `result`, `cost_actual`, `## Enmiendas`;
  - the tools those experiments use (`Tools/P-XXXX/<method>/TOOL.md`);
  - the project's `_hub.md` (`autonomy_defaults`) and `Estado-del-arte.md`
    (`### Búsqueda ejecutada` blocks, attribution, `generated_by` if present);
  - the `Papers/P-XXXX` notes cited (hypotheses' `linked_papers` + P-ids in the
    manuscript note), incl. A1's `resolved` / `resolution_checked`;
  - the manuscript note itself when `--manuscript` is given;
  - `verifications:` (docs/v3-interfaces.md §1b) from every note above.

Rules this script enforces:
  - It never invents a contribution. A stage with no record says so
    explicitly ("No consta en los registros de Kairo ...").
  - It never claims human involvement it cannot see. Evidence of a human is
    `generated_by.origin: human`, a history `by:` that matches a `--researcher`
    name (or the literal label human/investigador), an explicit, non-negated
    approval sentence in `## Enmiendas` / `## Revisión del ciclo`, or the
    manuscript's `ai_disclosure_confirmed_by`. Absence is not evidence. A `by:`
    that is neither an agent (model id, Kairo skill/agent name, `@`, `->`,
    `kairo/`) nor a declared researcher is "unknown": rendered as "no consta si
    fue una persona o un agente", never as human.
    `autonomy_defaults` is reported as configuration, never as an act.
  - Every `verifications:` entry is reported. `no_errors_found` is rendered
    only as "a verifier found no errors in <scope>" -- never as correct.
  - A note with frontmatter `send: never` contributes only its id and an
    `importante` flag; none of its content is output.
  - Every statement carries its source (note id + field) in `sources`; the
    markdown renders those in an internal traceability table.

Usage:
    python ai_disclosure.py --vault <vault> --project <slug|PROJ-XXX> \\
        --thread <paper_thread> [--hypotheses H-0001,H-0002] \\
        [--manuscript <path>] [--researcher "<name>" ...] \\
        [--format markdown|json] [--lang es|en|both]
    python ai_disclosure.py --version

Exit codes: 0 ok (flags do not change the exit code; read them),
            1 error (unexpected failure),
            2 invalid input (vault / project / thread / hypothesis / manuscript
              not found).
Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

VERSION = "1.0.0"
SCRIPT_ID = f"kairo/ai_disclosure.py@{VERSION}"

VERDICTS = ("no_errors_found", "errors_found", "cannot_assess")
SEVERITY_ORDER = {"crítico": 0, "importante": 1, "menor": 2}

ANALYSIS_SCRIPTS = {
    "frequentist": "scripts/analysis/two_proportion_test.py",
    "bayesian": "scripts/analysis/bayes_factor_proportions.py",
}

STAGES = [
    ("literatura", "1. Búsqueda de literatura", "1. Literature search"),
    ("sintesis", "2. Síntesis del estado del arte", "2. State-of-the-art synthesis"),
    ("hipotesis", "3. Generación y crítica de hipótesis", "3. Hypothesis generation and critique"),
    ("preregistro", "4. Preregistro", "4. Preregistration"),
    ("codigo", "5. Implementación del código", "5. Code implementation"),
    ("ejecucion", "6. Ejecución y análisis", "6. Execution and analysis"),
    ("verificacion", "7. Verificación", "7. Verification"),
    ("redaccion", "8. Redacción del manuscrito", "8. Manuscript drafting"),
    ("humano", "9. Responsabilidad humana", "9. Human responsibility"),
]

USE_STAGES = {
    "búsqueda y síntesis de literatura": ("literatura", "sintesis"),
    "generación y crítica de hipótesis": ("hipotesis",),
    "redacción del preregistro": ("preregistro",),
    "implementación del código": ("codigo",),
    "verificación independiente": ("verificacion",),
    "redacción del manuscrito": ("redaccion",),
    "historial: creación de la hipótesis": ("hipotesis",),
    "historial: transición a preregistrada": ("preregistro",),
    "historial: transiciones de estado": ("ejecucion",),
}
USE_EN = {
    "búsqueda y síntesis de literatura": "literature search and synthesis",
    "generación y crítica de hipótesis": "hypothesis generation and critique",
    "redacción del preregistro": "preregistration drafting",
    "implementación del código": "code implementation",
    "verificación independiente": "independent verification",
    "redacción del manuscrito": "manuscript drafting",
    "historial: creación de la hipótesis": "history: hypothesis creation",
    "historial: transición a preregistrada": "history: transition to preregistered",
    "historial: transiciones de estado": "history: status transitions",
}
# model ids named inside free-text fields (history `by:`), kept verbatim
MODEL_IN_TEXT_RE = re.compile(
    r"\b(claude-[a-z0-9.-]+|gpt-[a-z0-9.-]+|gemini-[a-z0-9.-]+|llama[a-z0-9.-]*|qwen[a-z0-9.-]*|"
    r"deepseek[a-z0-9.-]*|mistral[a-z0-9.-]*)", re.IGNORECASE)


def models_in_text(text: str) -> list[str]:
    out = []
    for m in MODEL_IN_TEXT_RE.findall(text or ""):
        m = m.rstrip(".-")
        if m and m not in out:
            out.append(m)
    return out


SEND_NEVER_FLAG = ("{id} marcado send: never; su contribución no puede declararse "
                   "automáticamente, el investigador debe completarla a mano")


class InvalidInput(Exception):
    """Bad CLI input: exit code 2."""


# --------------------------------------------------------------------------
# YAML-subset parser (frontmatter only)
# --------------------------------------------------------------------------

class YamlError(ValueError):
    pass


@dataclass
class _Line:
    indent: int
    text: str
    block: list[str] | None = None  # raw lines of a `|` / `>` block scalar


def _strip_comment(s: str) -> str:
    """Drop a trailing `# comment` that is outside quotes."""
    q = None
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if q:
            if q == '"' and ch == "\\":
                i += 2
                continue
            if ch == q:
                if q == "'" and i + 1 < n and s[i + 1] == "'":
                    i += 2
                    continue
                q = None
        else:
            if ch in "\"'" and (i == 0 or s[i - 1] in " \t:[{,-"):
                q = ch
            elif ch == "#" and (i == 0 or s[i - 1] in " \t"):
                return s[:i].rstrip()
        i += 1
    return s.rstrip()


def _bracket_balance(s: str) -> int:
    depth = 0
    q = None
    for i, ch in enumerate(s):
        if q:
            if ch == q:
                q = None
        elif ch in "\"'" and (i == 0 or s[i - 1] in " \t:[{,-"):
            q = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
    return depth


def _find_colon(s: str) -> int:
    """Index of the key/value `:` (followed by space or EOL) outside quotes, or -1."""
    q = None
    for i, ch in enumerate(s):
        if q:
            if ch == q:
                q = None
            continue
        if ch in "\"'" and i == 0:
            q = ch
            continue
        if ch in "[{" and i == 0:
            return -1
        if ch == ":" and (i + 1 == len(s) or s[i + 1] in " \t"):
            return i
    return -1


def _preprocess(raw_lines: list[str]) -> list[_Line]:
    out: list[_Line] = []
    i = 0
    n = len(raw_lines)
    while i < n:
        raw = raw_lines[i].rstrip("\r").replace("\t", "    ")
        i += 1
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        text = _strip_comment(raw.strip())
        if not text:
            continue
        # block scalar: `key: |` / `key: >-` / `- |`
        m = re.search(r"(?:^|:\s|^-\s)\s*([|>][-+]?)$", text)
        if m:
            block: list[str] = []
            while i < n:
                nxt = raw_lines[i].rstrip("\r").replace("\t", "    ")
                if nxt.strip() and (len(nxt) - len(nxt.lstrip(" "))) <= indent:
                    break
                block.append(nxt)
                i += 1
            out.append(_Line(indent, text, block))
            continue
        # multi-line flow collection: join until brackets balance
        while _bracket_balance(text) > 0 and i < n:
            nxt = _strip_comment(raw_lines[i].rstrip("\r").strip())
            i += 1
            text = f"{text} {nxt}" if nxt else text
        out.append(_Line(indent, text))
    return out


def _is_item(text: str) -> bool:
    return text == "-" or text.startswith("- ")


def _unquote_dq(s: str) -> tuple[str, int]:
    out = []
    i = 1
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(nxt, "\\" + nxt))
            i += 2
            continue
        if ch == '"':
            return "".join(out), i + 1
        out.append(ch)
        i += 1
    raise YamlError(f"unterminated double-quoted string: {s!r}")


def _unquote_sq(s: str) -> tuple[str, int]:
    out = []
    i = 1
    while i < len(s):
        ch = s[i]
        if ch == "'":
            if i + 1 < len(s) and s[i + 1] == "'":
                out.append("'")
                i += 2
                continue
            return "".join(out), i + 1
        out.append(ch)
        i += 1
    raise YamlError(f"unterminated single-quoted string: {s!r}")


def _split_flow(inner: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    q = None
    cur: list[str] = []
    for i, ch in enumerate(inner):
        if q:
            cur.append(ch)
            if ch == q:
                q = None
            continue
        if ch in "\"'" and not "".join(cur).strip():
            q = ch
            cur.append(ch)
            continue
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def _plain(s: str):
    s = s.strip()
    low = s.lower()
    if low in ("null", "~", ""):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    if re.fullmatch(r"[-+]?\d+", s):
        try:
            return int(s)
        except ValueError:
            return s
    return s


def _scalar(s: str, block: list[str] | None = None):
    s = s.strip()
    if block is not None:
        style = s[-2:] if len(s) >= 2 and s[-2] in "|>" else s[-1:]
        non_empty = [b for b in block if b.strip()]
        ind = min((len(b) - len(b.lstrip(" ")) for b in non_empty), default=0)
        lines = [b[ind:] if len(b) >= ind else "" for b in block]
        if style.startswith(">"):
            return " ".join(l.strip() for l in lines if l.strip())
        return "\n".join(lines).rstrip("\n")
    if not s:
        return None
    if s[0] == '"':
        val, _ = _unquote_dq(s)
        return val
    if s[0] == "'":
        val, _ = _unquote_sq(s)
        return val
    if s[0] == "[":
        if not s.endswith("]"):
            raise YamlError(f"unterminated flow list: {s!r}")
        return [_scalar(p) for p in _split_flow(s[1:-1])]
    if s[0] == "{":
        if not s.endswith("}"):
            raise YamlError(f"unterminated flow map: {s!r}")
        d = {}
        for p in _split_flow(s[1:-1]):
            c = _find_colon(p)
            if c < 0:
                d[_plain(p)] = None
            else:
                d[_key(p[:c])] = _scalar(p[c + 1:])
        return d
    return _plain(s)


def _key(k: str) -> str:
    k = k.strip()
    if k[:1] == '"':
        return _unquote_dq(k)[0]
    if k[:1] == "'":
        return _unquote_sq(k)[0]
    return k


def _parse_block(lines: list[_Line], i: int, indent: int):
    if _is_item(lines[i].text):
        return _parse_list(lines, i, indent)
    return _parse_map(lines, i, indent)


def _parse_list(lines: list[_Line], i: int, indent: int):
    items = []
    n = len(lines)
    while i < n and lines[i].indent == indent and _is_item(lines[i].text):
        ln = lines[i]
        rest = ln.text[1:]
        stripped = rest.lstrip(" ")
        if not stripped:
            if i + 1 < n and lines[i + 1].indent > indent:
                val, i = _parse_block(lines, i + 1, lines[i + 1].indent)
            else:
                val, i = None, i + 1
        else:
            new_indent = indent + 1 + (len(rest) - len(stripped))
            if _is_item(stripped) or (_find_colon(stripped) > 0 and stripped[0] not in "[{\"'") \
                    or (stripped[0] in "\"'" and _find_colon(stripped) > 0):
                lines[i] = _Line(new_indent, stripped, ln.block)
                val, i = _parse_block(lines, i, new_indent)
            else:
                val, i = _scalar(stripped, ln.block), i + 1
        items.append(val)
    return items, i


def _parse_map(lines: list[_Line], i: int, indent: int):
    d: dict = {}
    n = len(lines)
    last_key = None
    while i < n and lines[i].indent >= indent:
        ln = lines[i]
        if ln.indent > indent:
            # continuation of a multi-line plain scalar
            if last_key is not None and isinstance(d.get(last_key), str):
                d[last_key] = f"{d[last_key]} {ln.text}"
                i += 1
                continue
            raise YamlError(f"unexpected indentation: {ln.text!r}")
        if _is_item(ln.text):
            break
        c = _find_colon(ln.text)
        if c <= 0:
            if last_key is not None and isinstance(d.get(last_key), str):
                d[last_key] = f"{d[last_key]} {ln.text}"
                i += 1
                continue
            raise YamlError(f"not a key: value line: {ln.text!r}")
        key = _key(ln.text[:c])
        vtext = ln.text[c + 1:].strip()
        if not vtext and ln.block is None:
            if i + 1 < n and lines[i + 1].indent > indent:
                val, i = _parse_block(lines, i + 1, lines[i + 1].indent)
            elif i + 1 < n and lines[i + 1].indent == indent and _is_item(lines[i + 1].text):
                val, i = _parse_list(lines, i + 1, indent)
            else:
                val, i = None, i + 1
        else:
            val, i = _scalar(vtext, ln.block), i + 1
        d[key] = val
        last_key = key
    return d, i


def parse_yaml(text: str) -> dict:
    lines = _preprocess(text.split("\n"))
    if not lines:
        return {}
    base = lines[0].indent
    val, i = _parse_block(lines, 0, base)
    if i < len(lines):
        raise YamlError(f"could not parse from: {lines[i].text!r}")
    if not isinstance(val, dict):
        raise YamlError("frontmatter is not a mapping")
    return val


def split_frontmatter(text: str) -> tuple[str | None, str]:
    text = text.lstrip("﻿").replace("\r\n", "\n")
    if not text.startswith("---"):
        return None, text
    lines = text.split("\n")
    if lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    return None, text


_SEND_NEVER_RE = re.compile(r"""^send\s*:\s*(["']?)never\1\s*(#.*)?$""", re.IGNORECASE)


def raw_send_never(fm_text: str | None) -> bool:
    """Top-level `send: never` (any case, quoted or not, trailing comment ok),
    detected on the raw text so it holds even when the YAML does not parse."""
    if not fm_text:
        return False
    return any(_SEND_NEVER_RE.match(l.rstrip()) for l in fm_text.split("\n"))


# --------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------

@dataclass
class Note:
    id: str
    kind: str  # hypothesis | experiment | tool | hub | sota | paper | manuscript
    path: Path
    rel: str
    fm: dict = field(default_factory=dict)
    body: str = ""
    send_never: bool = False
    parse_error: str | None = None

    def sections(self) -> dict[str, str]:
        out: dict[str, str] = {}
        cur = None
        buf: list[str] = []
        for line in self.body.split("\n"):
            if line.startswith("## "):
                if cur is not None:
                    out[cur] = "\n".join(buf)
                cur = line[3:].strip()
                buf = []
            elif cur is not None:
                buf.append(line)
        if cur is not None:
            out[cur] = "\n".join(buf)
        return out


_ID_RE = re.compile(r"^((?:H|E|P|PROJ)-\d+)")


def load_note(path: Path, kind: str, vault: Path, fallback_id: str | None = None) -> Note:
    text = path.read_text(encoding="utf-8", errors="replace")
    fm_text, body = split_frontmatter(text)
    fm: dict = {}
    err = None
    if fm_text is not None:
        try:
            fm = parse_yaml(fm_text)
        except YamlError as e:
            err = str(e)
    send_never = raw_send_never(fm_text)
    for k, v in fm.items():
        if str(k).lower() == "send" and isinstance(v, str) and v.strip().lower() == "never":
            send_never = True
    m = _ID_RE.match(path.name)
    nid = str(fm.get("id") or (m.group(1) if m else None) or fallback_id or path.stem)
    try:
        rel = path.relative_to(vault).as_posix()
    except ValueError:
        rel = path.as_posix()
    if send_never and not fm.get("id") and not m and not fallback_id:
        # the file stem may be a title: never output it for a send: never note
        nid = f"{kind}-sin-id-{hashlib.sha1(rel.encode('utf-8')).hexdigest()[:8]}"
    return Note(nid, kind, path, rel, fm, body, send_never, err)


def as_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v.strip():
        return [v.strip()]
    return []


def s(v) -> str:
    """Verbatim-ish string of a scalar for rendering."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


_PLACEHOLDER_RE = re.compile(r"^<.*>$")
# generic agent markers: model families, pipeline arrows, versioned ids, kairo/ prefix
_AGENT_BY_RE = re.compile(
    r"claude|gpt|gemini|llama|mistral|mixtral|qwen|deepseek|sonnet|opus|haiku|kimi|grok|gemma|"
    r"\bo[1-9](?:-[a-z0-9]+)*\b|kairo/|->|→|\bagent\b|\bagente\b|@|\bskill\b",
    re.IGNORECASE)
# a bare model-id-like token: lowercase, no spaces, contains a digit (o3, kimi-k2, glm-4.5)
_MODEL_ID_LIKE_RE = re.compile(r"^[a-z][a-z0-9]*(?:[-_.:/][a-z0-9]+)*$")

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
# fallback when the plugin tree is not next to the script; includes Block B names
_STATIC_KAIRO_NAMES = (
    "adr-check", "assemble-manuscript", "create-project", "hypothesis-cycle", "literature-search",
    "paper-to-tool", "preregister-experiment", "run-experiment", "serendipity-scan", "spawn-hypothesis",
    "update-confidence", "facet-searcher", "facet-summarizer", "second-critic", "evolve-program",
    "fresh-verifier",
)
_HUMAN_LABELS = ("human", "humano", "researcher", "investigador", "investigadora")


def kairo_component_names(root: Path | None = None) -> set[str]:
    """Names of Kairo skills (`skills/<name>/SKILL.md`) and agents (`agents/<name>.md`)
    in the plugin tree, plus a static fallback list."""
    root = PLUGIN_ROOT if root is None else Path(root)
    names = set(_STATIC_KAIRO_NAMES)
    try:
        for d in (root / "skills").iterdir():
            if d.is_dir() and (d / "SKILL.md").is_file():
                names.add(d.name.lower())
    except OSError:
        pass
    try:
        for p in (root / "agents").glob("*.md"):
            names.add(p.stem.lower())
    except OSError:
        pass
    return names


_KAIRO_NAMES_RE: re.Pattern | None = None


def _kairo_names_re() -> re.Pattern:
    global _KAIRO_NAMES_RE
    if _KAIRO_NAMES_RE is None:
        alts = "|".join(re.escape(n) for n in sorted(kairo_component_names(), key=len, reverse=True))
        _KAIRO_NAMES_RE = re.compile(rf"(?<![\w-])(?:{alts})(?![\w-])", re.IGNORECASE)
    return _KAIRO_NAMES_RE


def _norm_name(x: str) -> str:
    return re.sub(r"\s+", " ", s(x)).strip().casefold()


def classify_by(by, known_models: set[str], researchers=()) -> str:
    """agent | human | unknown.

    agent   -- a model id (or model-id-like token), a Kairo skill / agent name, or a
               generic agent marker (`@`, `->`, `kairo/`, ...);
    human   -- only positive evidence: the literal label `human` / `investigador`, or
               a name declared with `--researcher`;
    unknown -- everything else (incl. empty): never read as a person."""
    b = s(by).strip()
    if not b or _PLACEHOLDER_RE.match(b):
        return "unknown"
    if b.lower() in _HUMAN_LABELS:
        return "human"
    if (_AGENT_BY_RE.search(b) or _kairo_names_re().search(b)
            or any(m and m in b for m in known_models)
            or (_MODEL_ID_LIKE_RE.match(b) and re.search(r"\d", b))):
        return "agent"
    nb = _norm_name(b)
    for r in researchers or ():
        nr = _norm_name(r)
        if nr and re.search(rf"(?<!\w){re.escape(nr)}(?!\w)", nb):
            return "human"
    return "unknown"


def _unknown_by_note(by, known_models: set[str], researchers) -> tuple[str, str]:
    """Suffix for a rendered non-empty `by:` that is neither agent nor declared researcher."""
    b = s(by).strip()
    if b and not _PLACEHOLDER_RE.match(b) and classify_by(b, known_models, researchers) == "unknown":
        return ("; no consta si fue una persona o un agente",
                "; not recorded whether this was a person or an agent")
    return "", ""


def is_unknown_model(m) -> bool:
    t = s(m).strip()
    return not t or bool(_PLACEHOLDER_RE.match(t)) or t.lower() in (
        "unknown", "desconocido", "n/a", "none", "null", "?")


# --------------------------------------------------------------------------
# Collection
# --------------------------------------------------------------------------

@dataclass
class Ctx:
    vault: Path
    project_dir: Path
    project_id: str
    slug: str
    thread: str
    hub: Note | None = None
    sota: Note | None = None
    hypotheses: list[Note] = field(default_factory=list)
    experiments: list[Note] = field(default_factory=list)
    tools: list[Note] = field(default_factory=list)
    papers: list[Note] = field(default_factory=list)
    manuscript: Note | None = None
    exp_links: dict[str, list[str]] = field(default_factory=dict)
    researchers: list[str] = field(default_factory=list)
    flags: list[dict] = field(default_factory=list)
    statements: list[dict] = field(default_factory=list)

    def all_notes(self) -> list[Note]:
        out = [n for n in (self.hub, self.sota) if n]
        out += self.hypotheses + self.experiments + self.tools + self.papers
        if self.manuscript:
            out.append(self.manuscript)
        return out

    def flag(self, severity: str, message: str, note: str | None = None, field_: str | None = None):
        f = {"severity": severity, "message": message, "note": note, "field": field_}
        if f not in self.flags:
            self.flags.append(f)

    def say(self, stage: str, kind: str, es: str, en: str, sources: list[str]):
        self.statements.append({"stage": stage, "kind": kind, "es": es, "en": en,
                                "sources": sources})


def resolve_project(vault: Path, project: str) -> tuple[Path, str]:
    projects = vault / "Projects"
    if not projects.is_dir():
        raise InvalidInput(f"no Projects/ folder in vault {vault}")
    direct = projects / project
    if direct.is_dir():
        hub = direct / "_hub.md"
        pid = project
        if hub.is_file():
            n = load_note(hub, "hub", vault)
            pid = s(n.fm.get("id")) or project
        return direct, pid
    for d in sorted(projects.iterdir()):
        hub = d / "_hub.md"
        if d.is_dir() and hub.is_file():
            n = load_note(hub, "hub", vault)
            if s(n.fm.get("id")).strip().upper() == project.strip().upper():
                return d, s(n.fm.get("id"))
    raise InvalidInput(f"project not found: {project!r} (neither a folder under Projects/ "
                       f"nor a _hub.md id)")


def _find_by_id(folder: Path, nid: str, kind: str, vault: Path) -> Note | None:
    if not folder.is_dir():
        return None
    for p in sorted(folder.glob("*.md")):
        if p.name == nid + ".md" or p.name.startswith(nid + " ") or p.name.startswith(nid + "."):
            return load_note(p, kind, vault)
    for p in sorted(folder.glob("*.md")):
        n = load_note(p, kind, vault)
        if n.id == nid:
            return n
    return None


def collect(vault: Path, project: str, thread: str, hyp_ids: list[str] | None,
            manuscript: str | None) -> Ctx:
    if not vault.is_dir():
        raise InvalidInput(f"vault not found: {vault}")
    pdir, pid = resolve_project(vault, project)
    ctx = Ctx(vault, pdir, pid, pdir.name, thread)
    if (pdir / "_hub.md").is_file():
        ctx.hub = load_note(pdir / "_hub.md", "hub", vault, fallback_id="_hub")
        ctx.hub.id = "_hub.md"
    if (pdir / "Estado-del-arte.md").is_file():
        ctx.sota = load_note(pdir / "Estado-del-arte.md", "sota", vault)
        ctx.sota.id = "Estado-del-arte.md"

    hdir = pdir / "Hipotesis"
    all_h = [load_note(p, "hypothesis", vault) for p in sorted(hdir.glob("*.md"))] if hdir.is_dir() else []
    in_thread = [h for h in all_h if s(h.fm.get("paper_thread")).strip() == thread]
    if hyp_ids:
        by_id = {h.id: h for h in all_h}
        missing = [x for x in hyp_ids if x not in by_id]
        if missing:
            raise InvalidInput(f"hypotheses not found in project {pid}: {', '.join(missing)}")
        ctx.hypotheses = [by_id[x] for x in hyp_ids]
        for h in ctx.hypotheses:
            if h not in in_thread:
                ctx.flag("menor", f"{h.id} no tiene paper_thread: {thread} y se incluyó por --hypotheses",
                         h.id, "paper_thread")
    else:
        if not in_thread:
            raise InvalidInput(f"no hypothesis with paper_thread: {thread} in project {pid}")
        ctx.hypotheses = in_thread

    # experiments: linked_experiment (not from send: never notes) + back-links
    edir = pdir / "Experimentos"
    all_e = [load_note(p, "experiment", vault) for p in sorted(edir.glob("E-*.md"))] if edir.is_dir() else []
    e_by_id = {e.id: e for e in all_e}
    hset = {h.id for h in ctx.hypotheses}
    wanted: list[str] = []
    for h in ctx.hypotheses:
        if h.send_never:
            continue
        for eid in as_list(h.fm.get("linked_experiment")):
            eid = s(eid).strip()
            if eid and eid not in wanted:
                wanted.append(eid)
            ctx.exp_links.setdefault(eid, [])
            if h.id not in ctx.exp_links[eid]:
                ctx.exp_links[eid].append(h.id)
    for e in all_e:
        hid = s(e.fm.get("hypothesis")).strip()
        if hid in hset:
            if e.id not in wanted:
                wanted.append(e.id)
            ctx.exp_links.setdefault(e.id, [])
            if hid not in ctx.exp_links[e.id]:
                ctx.exp_links[e.id].append(hid)
    for eid in wanted:
        if eid in e_by_id:
            ctx.experiments.append(e_by_id[eid])
        else:
            ctx.flag("importante", f"{eid} aparece en linked_experiment de "
                     f"{', '.join(ctx.exp_links.get(eid, []))} pero no hay nota Experimentos/{eid}.md",
                     eid, "linked_experiment")

    # tools
    seen_tools = set()
    for e in ctx.experiments:
        if e.send_never:
            continue
        env = e.fm.get("environment") if isinstance(e.fm.get("environment"), dict) else {}
        for t in as_list(env.get("tools")):
            tpath = s(t.get("path") if isinstance(t, dict) else t).strip().strip("/")
            if not tpath or tpath in seen_tools:
                continue
            seen_tools.add(tpath)
            tool_md = vault / tpath / "TOOL.md"
            if tool_md.is_file():
                tn = load_note(tool_md, "tool", vault)
                tn.id = tpath
                ctx.tools.append(tn)
            else:
                ctx.flag("importante", f"{e.id} usa la herramienta {tpath} pero no existe {tpath}/TOOL.md",
                         e.id, "environment.tools")

    # manuscript
    if manuscript:
        mp = Path(manuscript)
        if not mp.is_absolute() and not mp.is_file():
            mp = vault / manuscript
        if not mp.is_file():
            raise InvalidInput(f"manuscript note not found: {manuscript}")
        ctx.manuscript = load_note(mp, "manuscript", vault)
        ctx.manuscript.id = ctx.manuscript.rel

    # papers
    pids: list[str] = []
    for h in ctx.hypotheses:
        if h.send_never:
            continue
        for p in as_list(h.fm.get("linked_papers")):
            p = s(p).strip()
            if p and p not in pids:
                pids.append(p)
    if ctx.manuscript and not ctx.manuscript.send_never:
        for p in re.findall(r"\bP-\d{4,}\b", ctx.manuscript.body):
            if p not in pids:
                pids.append(p)
    pdir_papers = vault / "Papers"
    for p in pids:
        n = _find_by_id(pdir_papers, p, "paper", vault)
        if n:
            ctx.papers.append(n)
        else:
            ctx.flag("menor", f"{p} se cita pero no hay nota Papers/{p}*.md", p, "linked_papers")
    return ctx


# --------------------------------------------------------------------------
# Analysis -> statements + flags
# --------------------------------------------------------------------------

def _gb(n: Note) -> dict | None:
    g = n.fm.get("generated_by")
    return g if isinstance(g, dict) else None


def known_models(ctx: Ctx) -> set[str]:
    out = set()
    for n in ctx.all_notes():
        if n.send_never:
            continue
        g = _gb(n)
        if g and not is_unknown_model(g.get("model")):
            out.add(s(g.get("model")))
        for v in as_list(n.fm.get("verifications")):
            if isinstance(v, dict) and not is_unknown_model(v.get("model")):
                out.add(s(v.get("model")))
    return out


_NOT_AN_APPROVAL_RE = re.compile(
    r"(?<!\w)(?:no|nunca|jamás|ni|debe|deben|debería|deberían|deberá|tiene que|hay que|pendiente|"
    r"pendientes|falta|faltan|sin aprobar|sin confirmar|a la espera|requiere|to be|must|should|"
    r"pending|not|never|awaiting|requires?|needs? to)(?!\w)",
    re.IGNORECASE)


def _approval_sentences(text: str) -> list[str]:
    pats = [
        r"aprobad[oa]s?\s+por\s+(?:el|la)\s+investigador[a]?",
        r"decidid[oa]s?\s+(?:y\s+aprobad[oa]s?\s+)?por\s+(?:el|la)\s+investigador[a]?",
        r"(?:el|la)\s+investigador[a]?\s+(?:decidió|aprobó|eligió|confirmó|autorizó)",
        r"decisión\s+(?:explícita\s+)?del\s+investigador",
        r"override\s+manual",
        r"approved\s+by\s+the\s+researcher",
        r"the\s+researcher\s+(?:decided|approved|chose|confirmed)",
    ]
    rx = re.compile("|".join(pats), re.IGNORECASE)
    out = []
    flat = re.sub(r"\s+", " ", text)
    for sent in re.split(r"(?<=[.;])\s+", flat):
        if _NOT_AN_APPROVAL_RE.search(sent):
            continue   # negated / modal / pending: not a record that it happened
        if rx.search(sent):
            sent = sent.strip()
            out.append(sent if len(sent) <= 240 else sent[:237] + "…")
    return out


def _enmiendas(n: Note) -> tuple[str, list[tuple[str, str]]]:
    """(preamble, [(heading, text)]) of `## Enmiendas`."""
    sec = n.sections().get("Enmiendas")
    if sec is None:
        return "", []
    pre: list[str] = []
    entries: list[tuple[str, list[str]]] = []
    for line in sec.split("\n"):
        if line.startswith("### "):
            entries.append((line[4:].strip(), []))
        elif entries:
            entries[-1][1].append(line)
        else:
            pre.append(line)
    return "\n".join(pre), [(h, "\n".join(t)) for h, t in entries]


def _scope_text(scope: str, nid: str, lang: str) -> str:
    sc = s(scope).strip()
    if sc == "note":
        return f"la nota completa {nid}" if lang == "es" else f"the whole note {nid}"
    if sc.startswith("section:"):
        h = sc[len("section:"):].strip()
        return f"la sección «{h}» de {nid}" if lang == "es" else f"section “{h}” of {nid}"
    return (f"el alcance «{sc}» (no previsto por el contrato) de {nid}" if lang == "es"
            else f"scope “{sc}” (not defined by the contract) of {nid}")


_VERDICT_TXT = {
    "no_errors_found": ("no encontró errores en", "found no errors in"),
    "errors_found": ("encontró errores en", "found errors in"),
    "cannot_assess": ("no pudo evaluar", "could not assess"),
}


def analyze(ctx: Ctx) -> dict:
    km = known_models(ctx)
    models: dict[str, dict] = {}  # model -> {uses: set, sources: list}

    def add_model(m, use_es: str, src: str):
        key = s(m).strip()
        if not key:
            return
        e = models.setdefault(key, {"uses": [], "sources": []})
        if use_es not in e["uses"]:
            e["uses"].append(use_es)
        if src not in e["sources"]:
            e["sources"].append(src)

    skill_versions: dict[str, list[str]] = {}

    # ---- send: never + parse errors
    for n in ctx.all_notes():
        if n.send_never:
            ctx.flag("importante", SEND_NEVER_FLAG.format(id=n.id), n.id, "send")
        elif n.parse_error:
            ctx.flag("importante", f"{n.id}: el frontmatter no se pudo leer ({n.parse_error}); "
                     f"sus registros no se incluyen en la declaración", n.id, "frontmatter")

    def ok(n: Note | None) -> bool:
        return bool(n) and not n.send_never and not n.parse_error

    hub_auto = {}
    if ok(ctx.hub) and isinstance(ctx.hub.fm.get("autonomy_defaults"), dict):
        hub_auto = ctx.hub.fm["autonomy_defaults"]

    def autonomy_statement(stage: str, key: str, es_what: str, en_what: str):
        v = s(hub_auto.get(key)).strip()
        if not v:
            return
        if v == "manual":
            es = (f"Configuración del proyecto: autonomy_defaults.{key}: manual — el protocolo de Kairo "
                  f"exige que el investigador decida {es_what}. Es una configuración, no un registro "
                  f"de cada decisión.")
            en = (f"Project configuration: autonomy_defaults.{key}: manual — Kairo's protocol requires "
                  f"the researcher to decide {en_what}. This is a setting, not a record of each decision.")
        else:
            es = (f"Configuración del proyecto: autonomy_defaults.{key}: {v} — el agente puede decidir "
                  f"{es_what} sin confirmación humana.")
            en = (f"Project configuration: autonomy_defaults.{key}: {v} — the agent may decide "
                  f"{en_what} without human confirmation.")
        ctx.say(stage, "config", es, en, [f"_hub.md autonomy_defaults.{key}"])

    # ---- 1/2 literature + synthesis
    lit_model_known = False
    if ok(ctx.sota):
        sota = ctx.sota
        g = _gb(sota)
        blocks = re.findall(r"^###\s+Búsqueda ejecutada\s*[—–-]\s*(.+?)\s*$", sota.body, re.MULTILINE)
        if blocks:
            for d in blocks:
                ctx.say("literatura", "ai",
                        f"La búsqueda de literatura quedó registrada en Estado-del-arte.md "
                        f"(«Búsqueda ejecutada — {d}»), en el formato de la skill literature-search de "
                        f"Kairo: consultas literales por faceta, fuentes y conteos tipo PRISMA.",
                        f"The literature search is recorded in Estado-del-arte.md (“Búsqueda "
                        f"ejecutada — {d}”) in the format of Kairo's literature-search skill: verbatim "
                        f"queries per facet, sources and PRISMA-style counts.",
                        [f"Estado-del-arte.md §Búsqueda ejecutada — {d}"])
            if "Cobertura degradada" in sota.body:
                ctx.say("literatura", "ai",
                        "El registro de búsqueda declara cobertura degradada (alguna faceta perdió un pase "
                        "de búsqueda por errores de la API).",
                        "The search record declares degraded coverage (a facet lost a search pass to API errors).",
                        ["Estado-del-arte.md §Cobertura degradada"])
        else:
            ctx.say("literatura", "none",
                    "No consta en los registros de Kairo ninguna búsqueda de literatura ejecutada "
                    "(Estado-del-arte.md no contiene un bloque «Búsqueda ejecutada»).",
                    "Kairo's records contain no executed literature search (Estado-del-arte.md has no "
                    "“Búsqueda ejecutada” block).", ["Estado-del-arte.md"])
        if g and not is_unknown_model(g.get("model")):
            lit_model_known = True
            add_model(g.get("model"), "búsqueda y síntesis de literatura", "Estado-del-arte.md generated_by")
            ctx.say("sintesis", "ai",
                    f"El mapa del estado del arte lo generó {s(g.get('model'))}"
                    f"{' (' + s(g.get('skill_version')) + ')' if g.get('skill_version') else ''}.",
                    f"The state-of-the-art map was generated by {s(g.get('model'))}"
                    f"{' (' + s(g.get('skill_version')) + ')' if g.get('skill_version') else ''}.",
                    ["Estado-del-arte.md generated_by"])
        elif g and s(g.get("origin")) == "human":
            ctx.say("sintesis", "human",
                    "El mapa del estado del arte está registrado como de origen humano (generated_by.origin: human).",
                    "The state-of-the-art map is recorded as human-authored (generated_by.origin: human).",
                    ["Estado-del-arte.md generated_by"])
            lit_model_known = True
        src_papers = as_list(sota.fm.get("source_papers"))
        attrib = []
        if "create-project" in sota.body:
            attrib.append("create-project")
        if "facet-summarizer" in sota.body or "map-reduce" in sota.body:
            attrib.append("map-reduce")
        lu = s(sota.fm.get("last_updated"))
        base_es = (f"Estado-del-arte.md (last_updated {lu or 'no consta'}; {len(src_papers)} papers en "
                   f"source_papers) sintetiza la literatura del proyecto.")
        base_en = (f"Estado-del-arte.md (last_updated {lu or 'not recorded'}; {len(src_papers)} papers in "
                   f"source_papers) synthesizes the project's literature.")
        if "create-project" in attrib:
            base_es += (" El propio documento atribuye su construcción a la skill create-project de Kairo"
                        + (" (map-reduce por facetas)." if "map-reduce" in attrib else "."))
            base_en += (" The document itself attributes its construction to Kairo's create-project skill"
                        + (" (per-facet map-reduce)." if "map-reduce" in attrib else "."))
            srcs = ["Estado-del-arte.md frontmatter", "Estado-del-arte.md cuerpo (atribución)"]
        else:
            base_es += " Quién la redactó (IA o investigador) no consta en los registros."
            base_en += " Who wrote it (AI or researcher) is not recorded."
            srcs = ["Estado-del-arte.md frontmatter"]
        ctx.say("sintesis", "ai" if attrib else "none", base_es, base_en, srcs)
        if not lit_model_known:
            ctx.say("literatura", "none",
                    "El modelo de IA que ejecutó la búsqueda y la síntesis no consta en los registros "
                    "(Estado-del-arte.md no tiene generated_by).",
                    "The AI model that ran the search and synthesis is not recorded (Estado-del-arte.md "
                    "has no generated_by).", ["Estado-del-arte.md generated_by (ausente)"])
            ctx.flag("importante", "Estado-del-arte.md: el modelo usado en la búsqueda / síntesis de "
                     "literatura no está registrado (sin generated_by)", "Estado-del-arte.md", "generated_by")
    else:
        why = "marcado send: never" if (ctx.sota and ctx.sota.send_never) else "no existe o no se pudo leer"
        ctx.say("literatura", "none",
                f"No consta en los registros de Kairo cómo se hizo la búsqueda de literatura "
                f"(Estado-del-arte.md {why}).",
                f"Kairo's records do not show how the literature search was done (Estado-del-arte.md "
                f"{'is marked send: never' if 'send' in why else 'is missing or unreadable'}).",
                ["Estado-del-arte.md"])
        ctx.say("sintesis", "none",
                "No consta en los registros de Kairo quién sintetizó el estado del arte.",
                "Kairo's records do not show who synthesized the state of the art.", ["Estado-del-arte.md"])
    autonomy_statement("literatura", "paper_ingestion", "qué papers candidatos se ingieren",
                       "which candidate papers are ingested")
    visible_papers = [p for p in ctx.papers if ok(p)]
    if ctx.papers:
        ctx.say("literatura", "record",
                f"Los papers citados en las hipótesis/manuscrito son {len(ctx.papers)} notas de Papers/: "
                f"{', '.join(p.id for p in ctx.papers)}.",
                f"The papers cited by the hypotheses/manuscript are {len(ctx.papers)} Papers/ notes: "
                f"{', '.join(p.id for p in ctx.papers)}.",
                ["linked_papers de las hipótesis", "cuerpo del manuscrito (P-ids)"])

    # ---- 3 hypotheses
    for h in ctx.hypotheses:
        if not ok(h):
            continue
        g = _gb(h)
        hist = [e for e in as_list(h.fm.get("history")) if isinstance(e, dict)]
        if not g:
            ctx.say("hipotesis", "none",
                    f"{h.id}: el origen de la hipótesis (IA o investigador) no consta en los registros "
                    f"(sin generated_by).",
                    f"{h.id}: whether the hypothesis came from AI or the researcher is not recorded "
                    f"(no generated_by).", [f"{h.id} generated_by (ausente)"])
            ctx.flag("importante", f"{h.id} no tiene generated_by: no se puede declarar si la propuso la IA "
                     f"o el investigador", h.id, "generated_by")
        else:
            origin = s(g.get("origin")).strip()
            if origin == "agent":
                model = g.get("model")
                sv = s(g.get("skill_version"))
                pc = s(g.get("pipeline_config"))
                if is_unknown_model(model):
                    ctx.flag("importante", f"{h.id}: generated_by.origin: agent sin modelo registrado",
                             h.id, "generated_by.model")
                    mtxt_es, mtxt_en = "modelo no registrado", "model not recorded"
                else:
                    add_model(model, "generación y crítica de hipótesis", f"{h.id} generated_by")
                    mtxt_es = mtxt_en = f"modelo {s(model)}"
                    mtxt_en = f"model {s(model)}"
                if sv:
                    skill_versions.setdefault(sv, []).append(f"{h.id} generated_by.skill_version")
                else:
                    ctx.flag("menor", f"{h.id}: generated_by sin skill_version", h.id, "generated_by.skill_version")
                of = s(h.fm.get("origin_flag"))
                of_es = {"wildcard": " Semilla: pista de serendipity-scan (origin_flag: wildcard).",
                         "evolution": " Combina dos candidatas de un torneo (origin_flag: evolution)."}.get(of, "")
                of_en = {"wildcard": " Seeded by a serendipity-scan lead (origin_flag: wildcard).",
                         "evolution": " Combines two tournament candidates (origin_flag: evolution)."}.get(of, "")
                ctx.say("hipotesis", "ai",
                        f"{h.id}: hipótesis propuesta y criticada por IA ({mtxt_es}"
                        f"{', skill ' + sv if sv else ''}{', configuración ' + pc if pc else ''}).{of_es}",
                        f"{h.id}: hypothesis proposed and critiqued by AI ({mtxt_en}"
                        f"{', skill ' + sv if sv else ''}{', configuration ' + pc if pc else ''}).{of_en}",
                        [f"{h.id} generated_by"] + ([f"{h.id} origin_flag"] if of else []))
            elif origin == "human":
                ctx.say("hipotesis", "human",
                        f"{h.id}: la hipótesis la propuso una persona (generated_by.origin: human).",
                        f"{h.id}: the hypothesis was proposed by a person (generated_by.origin: human).",
                        [f"{h.id} generated_by.origin"])
            else:
                ctx.flag("importante", f"{h.id}: generated_by.origin «{origin}» no es agent ni human",
                         h.id, "generated_by.origin")
                ctx.say("hipotesis", "none",
                        f"{h.id}: el origen registrado («{origin}») no permite saber si la propuso la IA o una persona.",
                        f"{h.id}: the recorded origin (“{origin}”) does not say whether AI or a person proposed it.",
                        [f"{h.id} generated_by.origin"])
        if hist:
            first = hist[0]
            by = s(first.get("by"))
            ev = s(first.get("evidence"))
            unk_es, unk_en = _unknown_by_note(by, km, ctx.researchers)
            ctx.say("hipotesis", "record",
                    f"{h.id}: primer registro del historial ({s(first.get('date'))}, status "
                    f"{s(first.get('status'))}, by: {by or 'no consta'}{unk_es})"
                    + (f": «{ev}»." if ev else "."),
                    f"{h.id}: first history entry ({s(first.get('date'))}, status {s(first.get('status'))}, "
                    f"by: {by or 'not recorded'}{unk_en})" + (f": “{ev}”." if ev else "."),
                    [f"{h.id} history {s(first.get('date'))}"])
        else:
            ctx.say("hipotesis", "none", f"{h.id}: no tiene historial de estado registrado.",
                    f"{h.id}: no status history is recorded.", [f"{h.id} history (ausente)"])
        if "Revisión del ciclo" in h.sections():
            ctx.say("hipotesis", "record",
                    f"{h.id}: la sección «Revisión del ciclo» registra rondas de refinamiento de la crítica.",
                    f"{h.id}: the “Revisión del ciclo” section records critique refinement rounds.",
                    [f"{h.id} §Revisión del ciclo"])
        if h.fm.get("needs_human_review") is True:
            ctx.say("hipotesis", "record",
                    f"{h.id}: marcada para revisión humana (needs_human_review: true); los registros no "
                    f"muestran si esa revisión ocurrió.",
                    f"{h.id}: flagged for human review (needs_human_review: true); the records do not show "
                    f"whether that review happened.", [f"{h.id} needs_human_review"])
    autonomy_statement("hipotesis", "hypothesis_promotion", "qué hipótesis avanzan",
                       "which hypotheses are promoted")

    # ---- history entries after the first -> execution (+ human evidence)
    human_evidence: list[tuple[str, str, list[str]]] = []
    prereg_by: dict[str, list[tuple[str, str]]] = {}
    for h in ctx.hypotheses:
        if not ok(h):
            continue
        g = _gb(h)
        if g and s(g.get("origin")) == "human":
            human_evidence.append((f"{h.id}: propuesta por una persona (generated_by.origin: human).",
                                   f"{h.id}: proposed by a person (generated_by.origin: human).",
                                   [f"{h.id} generated_by.origin"]))
        hist = [e for e in as_list(h.fm.get("history")) if isinstance(e, dict)]
        for idx, e in enumerate(hist):
            by = s(e.get("by"))
            date = s(e.get("date"))
            st = s(e.get("status"))
            exps = [s(x) for x in as_list(e.get("experiments"))]
            src = f"{h.id} history {date}"
            use = ("historial: creación de la hipótesis" if idx == 0 else
                   "historial: transición a preregistrada" if st == "preregistrada" else
                   "historial: transiciones de estado")
            for mid in models_in_text(by):
                add_model(mid, use, f"{src} by")
            cls = classify_by(by, km, ctx.researchers)
            if cls == "human":
                human_evidence.append((f"{h.id}: el historial atribuye la transición a «{st}» del {date} "
                                       f"a «{by}».",
                                       f"{h.id}: the history attributes the {date} transition to “{st}” "
                                       f"to “{by}”.", [src]))
            elif cls == "unknown" and by.strip() and not _PLACEHOLDER_RE.match(by.strip()):
                ctx.flag("menor", f"{src}: by «{by}» no identifica ni un agente de Kairo ni un investigador "
                         f"declarado (--researcher); se declara que no consta si fue una persona o un agente",
                         h.id, "history.by")
            unk_es, unk_en = _unknown_by_note(by, km, ctx.researchers)
            if st == "preregistrada":
                for x in exps:
                    prereg_by.setdefault(x, []).append((by, src))
            if idx == 0:
                continue
            comb = s(e.get("combination"))
            ev = s(e.get("evidence"))
            es = (f"{h.id}: {date} → {st} (by: {by or 'no consta'}{unk_es}"
                  f"{'; experimentos ' + ', '.join(exps) if exps else ''}"
                  f"{'; combinación ' + comb if comb and comb != 'n/a' else ''})"
                  + (f": «{ev}»." if ev else "."))
            en = (f"{h.id}: {date} → {st} (by: {by or 'not recorded'}{unk_en}"
                  f"{'; experiments ' + ', '.join(exps) if exps else ''}"
                  f"{'; combination ' + comb if comb and comb != 'n/a' else ''})"
                  + (f": “{ev}”." if ev else "."))
            ctx.say("ejecucion", "record", es, en, [src])
            if comb and comb != "n/a":
                skill_versions.setdefault(comb, []).append(f"{src} combination")

    # ---- 4/5/6 experiments
    for e in ctx.experiments:
        if not ok(e):
            continue
        fm = e.fm
        role = s(fm.get("role")).strip()
        rung = fm.get("rung")
        role_es = role if role else "confirmatory (campo ausente; se lee como confirmatorio)"
        role_en = role if role else "confirmatory (field absent; read as confirmatory)"
        rung_es = s(rung) if rung is not None and s(rung) != "" else "desconocido (campo ausente)"
        rung_en = s(rung) if rung is not None and s(rung) != "" else "unknown (field absent)"
        fa, fc = s(fm.get("frozen_at")), s(fm.get("frozen_commit"))
        tier, ap = s(fm.get("tier")), s(fm.get("analysis_plan"))
        if fa:
            ctx.say("preregistro", "record",
                    f"{e.id}: preregistro congelado el {fa} (frozen_at{'; commit ' + fc if fc else ''}); "
                    f"según el protocolo de Kairo, el preregistro se congela antes de ejecutar código "
                    f"(frozen_at registra el congelado, no cuándo se ejecutó el código); tier "
                    f"{tier or 'no consta'}, plan de análisis {ap or 'no consta'}, "
                    f"rol {role_es}, peldaño {rung_es}.",
                    f"{e.id}: preregistration frozen at {fa} (frozen_at{'; commit ' + fc if fc else ''}); "
                    f"under Kairo's protocol the preregistration is frozen before any code runs (frozen_at "
                    f"records the freeze, not when the code ran); tier {tier or 'not recorded'}, analysis plan "
                    f"{ap or 'not recorded'}, role {role_en}, rung {rung_en}.",
                    [f"{e.id} frozen_at", f"{e.id} frozen_commit", f"{e.id} tier", f"{e.id} analysis_plan",
                     f"{e.id} role", f"{e.id} rung"])
        else:
            ctx.say("preregistro", "none", f"{e.id}: no consta la fecha de congelado del preregistro (sin frozen_at).",
                    f"{e.id}: no freeze time is recorded (no frozen_at).", [f"{e.id} frozen_at (ausente)"])
        g = _gb(e)
        if g:
            origin = s(g.get("origin"))
            if origin == "agent" and not is_unknown_model(g.get("model")):
                add_model(g.get("model"), "redacción del preregistro", f"{e.id} generated_by")
                ctx.say("preregistro", "ai", f"{e.id}: preregistro redactado por IA ({s(g.get('model'))}).",
                        f"{e.id}: preregistration drafted by AI ({s(g.get('model'))}).", [f"{e.id} generated_by"])
            elif origin == "human":
                ctx.say("preregistro", "human", f"{e.id}: preregistro de origen humano (generated_by.origin: human).",
                        f"{e.id}: human-authored preregistration (generated_by.origin: human).",
                        [f"{e.id} generated_by.origin"])
                human_evidence.append((f"{e.id}: preregistro de origen humano.",
                                       f"{e.id}: human-authored preregistration.", [f"{e.id} generated_by.origin"]))
            else:
                ctx.flag("importante", f"{e.id}: generated_by sin modelo reconocible", e.id, "generated_by.model")
        elif e.id in prereg_by:
            for by, src in prereg_by[e.id]:
                who = classify_by(by, km, ctx.researchers)
                who_es = {"agent": "un agente", "human": "una persona",
                          "unknown": "un actor del que no consta si fue una persona o un agente"}[who]
                who_en = {"agent": "an agent", "human": "a person",
                          "unknown": "an actor for whom it is not recorded whether it was a person or an agent"}[who]
                ctx.say("preregistro", "ai" if who == "agent" else "record",
                        f"{e.id}: la transición de la hipótesis a «preregistrada» la registró {who_es} "
                        f"(by: {by or 'no consta'}). La nota del experimento no registra quién redactó el diseño.",
                        f"{e.id}: the hypothesis transition to “preregistrada” was recorded by {who_en} "
                        f"(by: {by or 'not recorded'}). The experiment note does not record who drafted the design.",
                        [src])
        else:
            ctx.say("preregistro", "none",
                    f"{e.id}: no consta en los registros de Kairo quién redactó el preregistro (IA o investigador).",
                    f"{e.id}: Kairo's records do not show who drafted the preregistration (AI or researcher).",
                    [f"{e.id} (sin generated_by ni history preregistrada)"])
            ctx.flag("importante", f"{e.id}: la autoría del diseño / preregistro no está registrada", e.id,
                     "generated_by")
        pre, entries = _enmiendas(e)
        if entries:
            ctx.say("preregistro", "record",
                    f"{e.id}: {len(entries)} enmienda(s) posteriores al congelado, declaradas como tales: "
                    + "; ".join(f"«{hd}»" for hd, _ in entries) + ".",
                    f"{e.id}: {len(entries)} post-freeze amendment(s), disclosed as such: "
                    + "; ".join(f"“{hd}”" for hd, _ in entries) + ".",
                    [f"{e.id} §Enmiendas"])
        for sent in _approval_sentences(pre):
            human_evidence.append((f"{e.id} §Enmiendas registra: «{sent}»",
                                   f"{e.id} §Enmiendas records: “{sent}”", [f"{e.id} §Enmiendas (preámbulo)"]))
        for hd, txt in entries:
            for sent in _approval_sentences(txt):
                human_evidence.append((f"{e.id} §Enmiendas «{hd}» registra: «{sent}»",
                                       f"{e.id} §Enmiendas “{hd}” records: “{sent}”", [f"{e.id} §Enmiendas {hd}"]))

        # code
        mp = s(fm.get("method_provenance")).strip()
        env = fm.get("environment") if isinstance(fm.get("environment"), dict) else {}
        etools = [s(t.get("path") if isinstance(t, dict) else t).strip().strip("/") for t in as_list(env.get("tools"))]
        for tp in etools:
            tn = next((t for t in ctx.tools if t.id == tp), None)
            if tn is None or not ok(tn):
                continue
            tf = tn.fm
            val = tf.get("validation") if isinstance(tf.get("validation"), dict) else {}
            vh = s(val.get("validation_hash"))
            ctx.say("codigo", "ai",
                    f"{e.id} usa la herramienta {tp}: método extraído del código público de los autores "
                    f"({s(tf.get('repo'))} @ {s(tf.get('commit'))[:12]}) por la skill paper-to-tool de Kairo; "
                    f"estado {s(tf.get('status')) or 'no consta'}"
                    f"{', validation_hash ' + vh[:16] + '…' if vh else ', sin validation_hash'}.",
                    f"{e.id} uses the tool {tp}: method extracted from the authors' public code "
                    f"({s(tf.get('repo'))} @ {s(tf.get('commit'))[:12]}) by Kairo's paper-to-tool skill; "
                    f"status {s(tf.get('status')) or 'not recorded'}"
                    f"{', validation_hash ' + vh[:16] + '…' if vh else ', no validation_hash'}.",
                    [f"{e.id} environment.tools", f"{tp}/TOOL.md repo/commit/status/validation"])
            if s(tf.get("status")) != "validated":
                ctx.flag("importante", f"{e.id} usa {tp} con status «{s(tf.get('status'))}» (no validated)",
                         tp, "status")
        if mp == "reimplemented_from_text":
            ctx.say("codigo", "record",
                    f"{e.id}: método reimplementado desde el texto del paper, no validado contra el código original.",
                    f"{e.id}: method reimplemented from the paper's text, not validated against the original code.",
                    [f"{e.id} method_provenance"])
            ctx.flag("importante", f"{e.id}: método reimplementado desde el texto, no validado contra el código "
                     f"original", e.id, "method_provenance")
        code_gb = fm.get("code_generated_by") if isinstance(fm.get("code_generated_by"), dict) else None
        code_origin = s(code_gb.get("origin")).strip().lower() if code_gb else ""
        code_model = None if (not code_gb or is_unknown_model(code_gb.get("model"))) else s(code_gb.get("model"))
        if code_origin == "human":
            ctx.say("codigo", "human", f"{e.id}: código escrito por el investigador (code_generated_by.origin: human).",
                    f"{e.id}: code written by the researcher (code_generated_by.origin: human).",
                    [f"{e.id} code_generated_by"])
        elif code_origin in ("agent", "mixed") or code_model:
            if code_model:
                add_model(code_model, "implementación del código", f"{e.id} code_generated_by")
            who_es = ("por IA" if code_origin != "mixed" else "entre IA y el investigador (mixto)")
            who_en = ("by AI" if code_origin != "mixed" else "jointly by AI and the researcher (mixed)")
            ctx.say("codigo", "ai",
                    f"{e.id}: código implementado {who_es} ({code_model or 'modelo no registrado'}).",
                    f"{e.id}: code implemented {who_en} ({code_model or 'model not recorded'}).",
                    [f"{e.id} code_generated_by"])
            if not code_model:
                ctx.flag("importante", f"{e.id}: code_generated_by.origin {code_origin} sin modelo registrado",
                         e.id, "code_generated_by.model")
        else:
            code_amend = [hd for hd, _ in entries if re.search(r"c[oó]digo|code", hd, re.IGNORECASE)]
            extra_es = (f" La enmienda «{code_amend[0]}» registra el commit del código, no su autor."
                        if code_amend else "")
            extra_en = (f" Amendment “{code_amend[0]}” records the code commit, not its author."
                        if code_amend else "")
            ctx.say("codigo", "none",
                    f"{e.id}: no consta en los registros de Kairo quién escribió el código del experimento "
                    f"(IA o investigador).{extra_es}",
                    f"{e.id}: Kairo's records do not show who wrote the experiment code (AI or researcher).{extra_en}",
                    [f"{e.id} (sin registro de autoría del código)"] + ([f"{e.id} §Enmiendas {code_amend[0]}"]
                                                                        if code_amend else []))
            ctx.flag("importante", f"{e.id}: la autoría del código del experimento (IA o investigador) no está "
                     f"registrada", e.id, "code")

        # execution
        st = s(fm.get("status"))
        res = fm.get("result") if isinstance(fm.get("result"), dict) else {}
        if st == "completed":
            script = ANALYSIS_SCRIPTS.get(ap)
            resultado = e.sections().get("Resultado", "")
            ran = [p for p in ANALYSIS_SCRIPTS.values() if p.rsplit("/", 1)[-1] in resultado]
            head_es = (f"{e.id}: ejecutado (status: completed; experiment_validity: "
                       f"{s(fm.get('experiment_validity')) or 'no consta'}). ")
            head_en = (f"{e.id}: executed (status: completed; experiment_validity: "
                       f"{s(fm.get('experiment_validity')) or 'not recorded'}). ")
            verdict_es = f" Veredicto registrado: «{s(res.get('verdict'))}»." if res.get("verdict") else ""
            verdict_en = f" Recorded verdict: “{s(res.get('verdict'))}”." if res.get("verdict") else ""
            srcs = [f"{e.id} status", f"{e.id} experiment_validity", f"{e.id} analysis_plan",
                    f"{e.id} result.verdict"]
            if ran:
                names = ", ".join(ran)
                ctx.say("ejecucion", "mechanical",
                        head_es + f"La sección «Resultado» registra la ejecución del script de análisis "
                        f"congelado ({names}); el veredicto lo fijó ese script sobre los umbrales del "
                        f"preregistro; la IA no juzgó la significación." + verdict_es,
                        head_en + f"The “Resultado” section records the run of the frozen analysis script "
                        f"({names}); that script set the verdict against the preregistered thresholds; the AI "
                        f"did not judge significance." + verdict_en,
                        srcs + [f"{e.id} §Resultado"])
                for p in ran:
                    skill_versions.setdefault(p, []).append(f"{e.id} §Resultado")
                if script and script not in ran:
                    ctx.flag("importante", f"{e.id}: analysis_plan: {ap} prevé {script}, pero «Resultado» "
                             f"registra {names}", e.id, "analysis_plan")
            else:
                ctx.say("ejecucion", "record",
                        head_es + (f"Según el protocolo de Kairo, el veredicto lo aplica mecánicamente el script "
                                   f"de análisis congelado del plan {ap} ({script}); no consta en la nota "
                                   f"(sección «Resultado») que ese script se ejecutara, así que no puede "
                                   f"afirmarse quién o qué fijó el veredicto."
                                   if script else "El plan de análisis no consta, así que no puede afirmarse "
                                                  "qué script fijó el veredicto.") + verdict_es,
                        head_en + (f"Under Kairo's protocol the verdict is applied mechanically by the frozen "
                                   f"analysis script of the {ap} plan ({script}); the note (“Resultado” section) "
                                   f"does not record that this script ran, so who or what set the verdict cannot "
                                   f"be stated." if script else "The analysis plan is not recorded, so which "
                                                                "script set the verdict cannot be stated.")
                        + verdict_en,
                        srcs + [f"{e.id} §Resultado (sin registro del script)"])
                if script:
                    ctx.flag("importante", f"{e.id}: «Resultado» no registra la ejecución de {script}; no "
                             f"puede declararse que el veredicto lo fijara el script", e.id, "Resultado")
            if fm.get("cost_actual"):
                ctx.say("ejecucion", "record", f"{e.id}: coste real registrado: «{s(fm.get('cost_actual'))}».",
                        f"{e.id}: recorded actual cost: “{s(fm.get('cost_actual'))}”.", [f"{e.id} cost_actual"])
        else:
            ctx.say("ejecucion", "none",
                    f"{e.id}: no consta ejecución completada (status: {st or 'no consta'}).",
                    f"{e.id}: no completed run is recorded (status: {st or 'not recorded'}).", [f"{e.id} status"])
    autonomy_statement("preregistro", "experiments", "cuándo se congelan y ejecutan los experimentos",
                       "when experiments are frozen and run")
    if not ctx.experiments:
        for stg, es, en in (("preregistro", "No consta en los registros de Kairo ningún preregistro para las hipótesis de este hilo.",
                             "Kairo's records contain no preregistration for this thread's hypotheses."),
                            ("codigo", "No consta en los registros de Kairo ninguna implementación de código para este hilo.",
                             "Kairo's records contain no code implementation for this thread."),
                            ("ejecucion", "No consta en los registros de Kairo ninguna ejecución para este hilo.",
                             "Kairo's records contain no experiment run for this thread.")):
            ctx.say(stg, "none", es, en, ["linked_experiment de las hipótesis"])

    # ---- 7 verification
    ver_records: list[dict] = []
    never: list[tuple[str, str]] = []
    for n in ctx.all_notes():
        if not ok(n) or n.kind == "hub":
            continue
        present = "verifications" in n.fm
        raw = n.fm.get("verifications")
        if not present or raw is None:
            never.append((n.id, "ausente"))
            continue
        if not isinstance(raw, list):
            ctx.flag("importante", f"{n.id}: verifications no es una lista", n.id, "verifications")
            continue
        if not raw:
            never.append((n.id, "[]"))
            continue
        headings = set(n.sections().keys())
        for idx, v in enumerate(raw):
            if not isinstance(v, dict):
                ctx.flag("menor", f"{n.id}: entrada {idx + 1} de verifications no es un mapa", n.id, "verifications")
                continue
            rec = {"note": n.id, "index": idx, "verifier": s(v.get("verifier")), "model": s(v.get("model")),
                   "date": s(v.get("date")), "verdict": s(v.get("verdict")), "scope": s(v.get("scope"))}
            ver_records.append(rec)
            missing = [k for k in ("verifier", "model", "date", "verdict", "scope") if not rec[k]]
            if missing:
                ctx.flag("menor", f"{n.id}: verificación {idx + 1} sin {', '.join(missing)}", n.id, "verifications")
            if rec["verdict"] and rec["verdict"] not in VERDICTS:
                ctx.flag("importante", f"{n.id}: verificación {idx + 1} con verdict «{rec['verdict']}» fuera del "
                         f"contrato (no_errors_found | errors_found | cannot_assess); no se interpreta", n.id,
                         "verifications")
            if is_unknown_model(rec["model"]):
                ctx.flag("importante", f"{n.id}: verificación {idx + 1} sin modelo registrado", n.id,
                         "verifications.model")
            else:
                add_model(rec["model"], "verificación independiente", f"{n.id} verifications {rec['date']}")
            if rec["verifier"]:
                skill_versions.setdefault(rec["verifier"], []).append(f"{n.id} verifications {rec['date']}")
            sc = rec["scope"]
            if sc.startswith("section:") and sc[len("section:"):].strip() not in headings:
                ctx.flag("menor", f"{n.id}: la verificación {idx + 1} cubre «{sc}», pero la nota ya no tiene esa "
                         f"sección (¿encabezado cambiado?)", n.id, "verifications.scope")
            vt = _VERDICT_TXT.get(rec["verdict"])
            if vt:
                es = (f"{rec['verifier'] or 'verificador no registrado'} (modelo {rec['model'] or 'no registrado'}), "
                      f"{rec['date'] or 'fecha no registrada'}: {vt[0]} {_scope_text(sc, n.id, 'es')}.")
                en = (f"{rec['verifier'] or 'unrecorded verifier'} (model {rec['model'] or 'not recorded'}), "
                      f"{rec['date'] or 'date not recorded'}: {vt[1]} {_scope_text(sc, n.id, 'en')}.")
            else:
                es = (f"{rec['verifier'] or 'verificador no registrado'} (modelo {rec['model'] or 'no registrado'}), "
                      f"{rec['date'] or 'fecha no registrada'}: verdict «{rec['verdict']}» sobre "
                      f"{_scope_text(sc, n.id, 'es')} (valor fuera del contrato; no se interpreta).")
                en = (f"{rec['verifier'] or 'unrecorded verifier'} (model {rec['model'] or 'not recorded'}), "
                      f"{rec['date'] or 'date not recorded'}: verdict “{rec['verdict']}” on "
                      f"{_scope_text(sc, n.id, 'en')} (value outside the contract; not interpreted).")
            ctx.say("verificacion", "ai", es, en, [f"{n.id} verifications[{idx}] {rec['date']}"])
        # latest per scope
        by_scope: dict[str, list[dict]] = {}
        for rec in [r for r in ver_records if r["note"] == n.id]:
            by_scope.setdefault(rec["scope"], []).append(rec)
        for sc, recs in by_scope.items():
            recs.sort(key=lambda r: r["index"])   # file order governs (contract §3a), not date
            last = recs[-1]
            had_errors = any(r["verdict"] == "errors_found" for r in recs)
            if last["verdict"] == "errors_found":
                ctx.flag("crítico", f"{n.id}: la última verificación de {_scope_text(sc, n.id, 'es')} "
                         f"({last['date']}) encontró errores y no hay una verificación posterior del mismo "
                         f"alcance; el manuscrito no debe presentarlo como verificado", n.id, "verifications")
            elif had_errors and last["verdict"] == "cannot_assess":
                ctx.flag("importante", f"{n.id}: {_scope_text(sc, n.id, 'es')} tuvo errores encontrados y la "
                         f"verificación posterior ({last['date']}) no pudo evaluarlo; los errores no constan "
                         f"como resueltos", n.id, "verifications")
    if never:
        ctx.say("verificacion", "none",
                "Sin verificación registrada (campo verifications ausente o []): "
                + ", ".join(f"{nid} ({how})" for nid, how in never) + ".",
                "No verification recorded (verifications field absent or []): "
                + ", ".join(f"{nid} ({'absent' if how == 'ausente' else '[]'})" for nid, how in never) + ".",
                [f"{nid} verifications" for nid, _ in never])
    if not ver_records:
        ctx.say("verificacion", "none",
                "No consta en los registros de Kairo ninguna verificación independiente de las notas en que se "
                "basa este manuscrito.",
                "Kairo's records contain no independent verification of the notes this manuscript is based on.",
                ["verifications de todas las notas"])
        ctx.flag("importante", "Ninguna nota tiene registros de verifications: la declaración no puede decir "
                 "cómo se revisaron los resultados asistidos por IA (ICLR 2027 pide detallarlo)", None, "verifications")
    else:
        ctx.say("verificacion", "record",
                "Una verificación «no encontró errores» significa solo que ese verificador no encontró errores en "
                "ese alcance; no es una certificación de corrección.",
                "A “found no errors” verification means only that this verifier found no errors in that scope; "
                "it is not a certification of correctness.", ["docs/v3-interfaces.md §1b"])
    if visible_papers:
        res_t = [p.id for p in visible_papers if p.fm.get("resolved") is True]
        res_f = [p.id for p in visible_papers if p.fm.get("resolved") is False]
        unchecked = [p.id for p in visible_papers if "resolved" not in p.fm]
        es = (f"Resolución de citas en OpenAlex (comprobación mecánica de que la referencia existe): "
              f"{len(res_t)} resueltas, {len(res_f)} no resueltas, {len(unchecked)} sin comprobar, de "
              f"{len(visible_papers)} papers citados.")
        en = (f"Citation resolution against OpenAlex (mechanical check that the reference exists): "
              f"{len(res_t)} resolved, {len(res_f)} unresolved, {len(unchecked)} unchecked, of "
              f"{len(visible_papers)} cited papers.")
        ctx.say("verificacion", "mechanical", es, en, ["Papers/* resolved / resolution_checked"])

    # ---- 8 drafting
    m = ctx.manuscript
    if ok(m):
        g = _gb(m)
        gen = s(m.fm.get("generated"))
        if g and not is_unknown_model(g.get("model")):
            add_model(g.get("model"), "redacción del manuscrito", f"{m.id} generated_by")
            sv = s(g.get("skill_version"))
            if sv:
                skill_versions.setdefault(sv, []).append(f"{m.id} generated_by.skill_version")
            ctx.say("redaccion", "ai",
                    f"El borrador del manuscrito lo redactó IA: {s(g.get('model'))}"
                    f"{' con ' + sv if sv else ''} (generado {gen or 'fecha no registrada'}, status "
                    f"{s(m.fm.get('status')) or 'no consta'}), a partir de las notas de Kairo citadas arriba.",
                    f"The manuscript draft was written by AI: {s(g.get('model'))}"
                    f"{' with ' + sv if sv else ''} (generated {gen or 'date not recorded'}, status "
                    f"{s(m.fm.get('status')) or 'not recorded'}), from the Kairo notes cited above.",
                    [f"{m.id} generated_by", f"{m.id} generated", f"{m.id} status"])
        elif g and s(g.get("origin")).strip() == "agent":
            sv = s(g.get("skill_version"))
            if sv:
                skill_versions.setdefault(sv, []).append(f"{m.id} generated_by.skill_version")
            ctx.say("redaccion", "ai",
                    f"El borrador del manuscrito lo redactó IA según su generated_by (origin: agent"
                    f"{', ' + sv if sv else ''}); el modelo concreto no consta (generado "
                    f"{gen or 'fecha no registrada'}, status {s(m.fm.get('status')) or 'no consta'}).",
                    f"The manuscript draft was written by AI according to its generated_by (origin: agent"
                    f"{', ' + sv if sv else ''}); the specific model is not recorded (generated "
                    f"{gen or 'date not recorded'}, status {s(m.fm.get('status')) or 'not recorded'}).",
                    [f"{m.id} generated_by", f"{m.id} generated", f"{m.id} status"])
            ctx.flag("importante", f"{m.id}: el modelo que redactó el manuscrito no está registrado "
                     f"(generated_by sin model)", m.id, "generated_by.model")
        elif g and s(g.get("origin")).strip() == "human":
            ctx.say("redaccion", "human",
                    "El borrador del manuscrito está registrado como de origen humano (generated_by.origin: human).",
                    "The manuscript draft is recorded as human-written (generated_by.origin: human).",
                    [f"{m.id} generated_by.origin"])
            human_evidence.append(("El borrador del manuscrito es de origen humano (generated_by.origin: human).",
                                   "The manuscript draft is human-written (generated_by.origin: human).",
                                   [f"{m.id} generated_by.origin"]))
        else:
            ctx.say("redaccion", "none",
                    f"No consta en los registros de Kairo quién redactó el borrador del manuscrito (IA o "
                    f"investigador) ni con qué modelo: la nota {m.id} no tiene generated_by (generado "
                    f"{gen or 'fecha no registrada'}, status {s(m.fm.get('status')) or 'no consta'}).",
                    f"Kairo's records do not show who drafted the manuscript (AI or researcher) or with which "
                    f"model: the note {m.id} has no generated_by (generated {gen or 'date not recorded'}, status "
                    f"{s(m.fm.get('status')) or 'not recorded'}).",
                    [f"{m.id} generated", f"{m.id} status", f"{m.id} generated_by (ausente)"])
            ctx.flag("importante", f"{m.id}: la autoría y el modelo de la redacción del manuscrito no están "
                     f"registrados (añadir generated_by al frontmatter)", m.id, "generated_by")
    elif m:
        ctx.say("redaccion", "none",
                f"La nota del manuscrito {m.id} no puede declararse automáticamente (send: never o frontmatter "
                f"ilegible); el modelo y la fecha de redacción no constan aquí.",
                f"The manuscript note {m.id} cannot be declared automatically (send: never or unreadable "
                f"frontmatter); the drafting model and date are not stated here.", [f"{m.id}"])
    else:
        ctx.say("redaccion", "none",
                "No se proporcionó la nota del manuscrito: no consta en los registros quién redactó el borrador "
                "(IA o investigador), ni el modelo ni la fecha de redacción.",
                "The manuscript note was not provided: the records do not show who drafted the manuscript (AI or "
                "researcher), nor the drafting model or date.", ["--manuscript (no dado)"])
        ctx.flag("importante", "No se pasó --manuscript: la etapa de redacción no tiene registro del modelo "
                 "ni la fecha", None, "--manuscript")
    ctx.say("redaccion", "none",
            "Los registros de Kairo no reflejan ediciones humanas del borrador; si las hubo, el investigador debe "
            "declararlas.",
            "Kairo's records do not reflect human edits to the draft; if there were any, the researcher must "
            "declare them.", ["(ausencia de registro)"])
    ctx.say("redaccion", "none",
            "Los registros de Kairo no registran la generación de imágenes o figuras con IA. [PENDIENTE — el "
            "investigador debe confirmar que ninguna figura se generó con IA: Nature y Science no lo permiten "
            "sin autorización editorial.]",
            "Kairo's records do not record any AI-generated images or figures. [PENDING — the researcher must "
            "confirm no figure was AI-generated: Nature and Science do not allow it without editorial permission.]",
            ["(ausencia de registro)"])
    ctx.flag("menor", "Imágenes/figuras generadas con IA: Kairo no lo registra; el investigador debe confirmarlo "
             "(Nature/Science las prohíben salvo excepción)", None, "figures")
    ctx.flag("importante", "Kairo no registra los prompts individuales (Science pide el prompt completo y la "
             "versión de la herramienta en Métodos): declarar que las instrucciones son las skills de Kairo en la "
             "versión registrada (skill_version) y adjuntarlas como material suplementario", None, "prompts")

    # ---- 9 human
    if ok(m):
        conf_by = s(m.fm.get("ai_disclosure_confirmed_by")).strip()
        if conf_by:
            human_evidence.append((f"El investigador «{conf_by}» confirmó esta declaración "
                                   f"({s(m.fm.get('ai_disclosure_confirmed')) or 'fecha no registrada'}).",
                                   f"The researcher “{conf_by}” confirmed this statement "
                                   f"({s(m.fm.get('ai_disclosure_confirmed')) or 'date not recorded'}).",
                                   [f"{m.id} ai_disclosure_confirmed_by"]))
    else:
        conf_by = ""
    for h in ctx.hypotheses:
        if not ok(h):
            continue
        for sent in _approval_sentences(h.sections().get("Revisión del ciclo", "")):
            human_evidence.append((f"{h.id} §Revisión del ciclo registra: «{sent}»",
                                   f"{h.id} §Revisión del ciclo records: “{sent}”", [f"{h.id} §Revisión del ciclo"]))
    if human_evidence:
        for es, en, src in human_evidence:
            ctx.say("humano", "human", es, en, src)
    else:
        ctx.say("humano", "none",
                "No consta en los registros de Kairo ninguna acción atribuida al investigador en este hilo "
                "(ni hipótesis de origen humano, ni entradas de historial firmadas por una persona, ni "
                "aprobaciones registradas en enmiendas).",
                "Kairo's records attribute no action in this thread to the researcher (no human-origin "
                "hypothesis, no history entry by a person, no recorded approval in amendments).",
                ["generated_by / history / §Enmiendas de las notas del hilo"])
    if conf_by:
        ctx.say("humano", "human",
                "Los autores asumen la responsabilidad del contenido final de este trabajo, incluidos textos, "
                "afirmaciones y artefactos producidos con ayuda de IA generativa. Ninguna herramienta de IA figura "
                "como autora.",
                "The authors take responsibility for the final content of this work, including text, claims and "
                "artifacts produced with the aid of generative AI. No AI tool is listed as an author.",
                [f"{m.id} ai_disclosure_confirmed_by"])
    else:
        ctx.say("humano", "none",
                "[PENDIENTE — debe confirmarlo el investigador; no consta en los registros] Los autores asumen la "
                "responsabilidad del contenido final de este trabajo, incluidos textos, afirmaciones y artefactos "
                "producidos con ayuda de IA generativa. Ninguna herramienta de IA figura como autora.",
                "[PENDING — the researcher must confirm; not in the records] The authors take responsibility for "
                "the final content of this work, including text, claims and artifacts produced with the aid of "
                "generative AI. No AI tool is listed as an author.",
                ["(pendiente: ai_disclosure_confirmed_by en el manuscrito)"])
        ctx.flag("importante", "La declaración de responsabilidad humana y de no autoría de la IA no está "
                 "confirmada por el investigador (ai_disclosure_confirmed_by ausente en el manuscrito)",
                 m.id if m else None, "ai_disclosure_confirmed_by")

    ctx.flags.sort(key=lambda f: SEVERITY_ORDER.get(f["severity"], 9))
    return {
        "models": [{"model": k, "uses": v["uses"], "sources": v["sources"],
                    "stages": sorted({st for u in v["uses"] for st in USE_STAGES.get(u, ())})}
                   for k, v in sorted(models.items())],
        "stages_without_model": [k for k, _, _ in STAGES
                                 if k not in {st for v in models.values() for u in v["uses"]
                                              for st in USE_STAGES.get(u, ())}
                                 and k != "humano"
                                 and not (k == "verificacion" and not ver_records)],
        "versioned_components": [{"id": k, "sources": v} for k, v in sorted(skill_versions.items())],
        "verifications": ver_records,
        "never_verified": [{"note": a, "field": b if b == "[]" else "absent"} for a, b in never],
    }


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def _stage_blocks(ctx: Ctx, lang: str, numbering: dict[int, int]) -> list[str]:
    out = []
    for key, es_t, en_t in STAGES:
        out.append(f"#### {es_t if lang == 'es' else en_t}")
        out.append("")
        sts = [(i, st) for i, st in enumerate(ctx.statements) if st["stage"] == key]
        if not sts:
            out.append("- No consta en los registros de Kairo." if lang == "es"
                       else "- Not in Kairo's records.")
        for i, st in sts:
            out.append(f"- [{numbering[i]}] {st[lang]}")
        out.append("")
    return out


def statement_numbers(ctx: Ctx) -> dict[int, int]:
    """Number statements in rendering order (stage order, then record order)."""
    order = {k: i for i, (k, _, _) in enumerate(STAGES)}
    idx = sorted(range(len(ctx.statements)), key=lambda i: (order[ctx.statements[i]["stage"]], i))
    return {i: n + 1 for n, i in enumerate(idx)}


def render_markdown(ctx: Ctx, info: dict, langs: list[str]) -> str:
    numbering = statement_numbers(ctx)
    L: list[str] = ["## Declaración de uso de IA", ""]
    def prov(m, lang):
        srcs = m["sources"]
        txt = ", ".join(srcs[:4]) + (", …" if len(srcs) > 4 else "")
        uses = "; ".join(m["uses"] if lang == "es" else [USE_EN.get(u, u) for u in m["uses"]])
        return f"{m['model']} ({uses}; {'fuente' if lang == 'es' else 'source'}: {txt})"
    titles = {k: (es, en) for k, es, en in STAGES}
    no_model = info.get("stages_without_model", [])
    models_line_es = "; ".join(prov(m, "es") for m in info["models"]) or "ninguno"
    models_line_en = "; ".join(prov(m, "en") for m in info["models"]) or "none"
    nomodel_es = (" Etapas sin modelo registrado: " + ", ".join(titles[k][0] for k in no_model) + ".") if no_model else ""
    nomodel_en = (" Stages with no recorded model: " + ", ".join(titles[k][1] for k in no_model) + ".") if no_model else ""
    comps = ", ".join(c["id"] for c in info["versioned_components"])
    for lang in langs:
        if lang == "en":
            L += ["### English version (for submission)", ""]
            L.append("In this work we used generative AI tools through Kairo, an agent-assisted research system. "
                     "This statement was generated automatically from the records Kairo keeps in the project's "
                     "vault; it states only what those records show, and every stage without a record says so. "
                     f"Models named in the records: {models_line_en}.{nomodel_en}"
                     + (f" Versioned components (skills, verifiers, analysis scripts): {comps}." if comps else "")
                     + " The instructions given to each model are those of the Kairo skill at the recorded "
                       "version; individual prompts are not recorded.")
        else:
            L.append("En este trabajo se usaron herramientas de IA generativa a través de Kairo, un sistema de "
                     "investigación asistida por agentes. Esta declaración se generó automáticamente a partir de los "
                     "registros que Kairo guarda en el vault del proyecto; solo afirma lo que consta en ellos, y cada "
                     f"etapa sin registro lo dice expresamente. Modelos que constan en los registros: {models_line_es}.{nomodel_es}"
                     + (f" Componentes versionados (skills, verificadores, scripts de análisis): {comps}." if comps else "")
                     + " Las instrucciones que recibió cada modelo son las de la skill de Kairo en la versión "
                       "registrada; los prompts individuales no se registran.")
        L.append("")
        L += _stage_blocks(ctx, lang, numbering)
    L += ["### Trazabilidad (interno — eliminar antes de enviar)", "",
          f"Generado por `{SCRIPT_ID}` · proyecto {ctx.project_id} · hilo `{ctx.thread}`.", "",
          "| # | Fuente (nota · campo) |", "|---|---|"]
    for i in sorted(numbering, key=numbering.get):
        L.append(f"| {numbering[i]} | {'; '.join(ctx.statements[i]['sources']).replace('|', '/')} |")
    L += ["", "### Avisos de cumplimiento (interno — eliminar antes de enviar)", ""]
    if ctx.flags:
        L += ["| Severidad | Aviso |", "|---|---|"]
        for f in ctx.flags:
            L.append(f"| {f['severity']} | {f['message'].replace('|', '/')} |")
    else:
        L.append("Sin avisos.")
    L.append("")
    return "\n".join(L)


def build(args) -> tuple[Ctx, dict]:
    hyp = [x.strip() for x in args.hypotheses.split(",") if x.strip()] if args.hypotheses else None
    ctx = collect(Path(args.vault), args.project, args.thread, hyp, args.manuscript)
    ctx.researchers = [r.strip() for r in (getattr(args, "researcher", None) or []) if r and r.strip()]
    info = analyze(ctx)
    return ctx, info


def to_json(ctx: Ctx, info: dict) -> dict:
    return {
        "script": SCRIPT_ID,
        "project": ctx.project_id,
        "project_dir": ctx.project_dir.relative_to(ctx.vault).as_posix(),
        "thread": ctx.thread,
        # send: never -> id only (a path like `Papers/P-XXXX <title>.md` would leak the title)
        "notes": [{"id": n.id, "kind": n.kind, "send_never": True} if n.send_never else
                  {"id": n.id, "kind": n.kind, "path": n.rel, "send_never": False, "parse_error": n.parse_error}
                  for n in ctx.all_notes()],
        "hypotheses": [h.id for h in ctx.hypotheses],
        "experiments": [e.id for e in ctx.experiments],
        "tools": [t.id for t in ctx.tools],
        "papers": [p.id for p in ctx.papers],
        **info,
        "statements": sorted(({"n": statement_numbers(ctx)[i], **st} for i, st in enumerate(ctx.statements)),
                             key=lambda x: x["n"]),
        "flags": ctx.flags,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate the 'Declaración de uso de IA' section from Kairo records.")
    ap.add_argument("--version", action="version", version=SCRIPT_ID)
    ap.add_argument("--vault", required=True)
    ap.add_argument("--project", required=True, help="project folder slug or PROJ-XXX id")
    ap.add_argument("--thread", required=True, help="paper_thread slug")
    ap.add_argument("--hypotheses", help="comma-separated ids (the gate's qualifying list)")
    ap.add_argument("--manuscript", help="manuscript note path (absolute or vault-relative)")
    ap.add_argument("--researcher", action="append", default=[], metavar="NAME",
                    help="name of a human researcher (repeatable); a history `by:` is read as a person "
                         "only if it matches one of these (or the literal label human/investigador)")
    ap.add_argument("--format", choices=("markdown", "json"), default="markdown")
    ap.add_argument("--json", action="store_true", help="alias for --format json")
    ap.add_argument("--lang", choices=("es", "en", "both"), default="both")
    args = ap.parse_args(argv)
    try:
        ctx, info = build(args)
    except InvalidInput as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    fmt = "json" if args.json else args.format
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if fmt == "json":
        print(json.dumps(to_json(ctx, info), ensure_ascii=False, indent=2))
    else:
        langs = ["es", "en"] if args.lang == "both" else [args.lang]
        print(render_markdown(ctx, info, langs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
