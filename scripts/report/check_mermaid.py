#!/usr/bin/env python3
"""Light structural lint of the mermaid diagrams in a Markdown note.

    check_mermaid.py <note.md>

A broken diagram renders as an error box in the app and in Obsidian. This
catches the usual slips before a note is saved — it is not a mermaid parser:

- a ```mermaid fence that never closes, or a block with nothing in it;
- a first line (after `%%` comments and a `---` config header) that names no
  known diagram type (`flowchart TD`, `sequenceDiagram`, `mindmap`, …), or a
  `flowchart` / `graph` with an unknown direction;
- in `flowchart` / `graph` / `mindmap`: unbalanced brackets ``[] () {}``, an
  odd number of double quotes on a line, and an unquoted label holding
  parentheses (``A[f(x)]`` breaks the parser; ``A["f(x)"]`` does not).

Prints a JSON report {"blocks", "ok", "problems": [{block, line, reason}]}.
`line` is the line number in the note. Exit codes: 0 every diagram passed ·
2 a diagram has a problem · 1 error (file unreadable). Standard library only.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TOOL = "kairo/check_mermaid@1.0.0"
TYPES = frozenset({
    "flowchart", "graph", "sequenceDiagram", "classDiagram", "stateDiagram", "stateDiagram-v2",
    "erDiagram", "journey", "gantt", "pie", "quadrantChart", "requirementDiagram", "gitGraph",
    "C4Context", "C4Container", "C4Component", "C4Dynamic", "C4Deployment", "mindmap", "timeline",
    "sankey-beta", "xychart-beta", "block-beta", "packet-beta", "kanban", "architecture-beta",
    "radar-beta", "treemap-beta",
})
DIRECTIONS = frozenset({"TD", "TB", "BT", "RL", "LR"})
STRICT = frozenset({"flowchart", "graph", "mindmap"})   # node-and-label syntax: brackets are structure
_OPEN = re.compile(r"^\s*(```+|~~~+)\s*mermaid\s*$")
_FENCE = re.compile(r"^\s*(```+|~~~+)")
_QUOTED = re.compile(r'"[^"]*"')
_ASYM = re.compile(r"(\w)>[^\]\[]*\]")                  # A>asymmetric] opens with «>», not «[»
PAIRS = {"]": "[", ")": "(", "}": "{"}


def blocks(text: str) -> list[dict]:
    """Every mermaid block: its first content line number (1-based), lines, closed?"""
    out, cur, fence = [], None, None
    for n, line in enumerate(text.replace("\r\n", "\n").split("\n"), 1):
        if cur is None:
            m = _OPEN.match(line)
            if m:
                fence = m.group(1)
                cur = {"start": n + 1, "lines": [], "closed": False}
                continue
            m = _FENCE.match(line)
            if m:                                   # another code block: skip it whole
                cur, fence = {"skip": True, "lines": []}, m.group(1)
            continue
        if line.strip().startswith(fence[0] * len(fence)) and not line.strip().strip(fence[0]):
            if not cur.get("skip"):
                cur["closed"] = True
                out.append(cur)
            cur = None
            continue
        cur["lines"].append(line)
    if cur is not None and not cur.get("skip"):
        out.append(cur)
    return out


def _content(lines: list[str]) -> list[tuple[int, str]]:
    """(offset, line) of the lines that matter: no blank lines, `%%` comments or `---` config header."""
    out, i = [], 0
    if lines and lines[0].strip() == "---":
        i = 1
        while i < len(lines) and lines[i].strip() != "---":
            i += 1
        i += 1
    for k in range(i, len(lines)):
        s = lines[k].strip()
        if s and not s.startswith("%%"):
            out.append((k, lines[k]))
    return out


def _label_problems(line: str) -> list[str]:
    """Brackets per line, ignoring quoted text; an unquoted label with parentheses."""
    probs = []
    if line.count('"') % 2:
        return ["comillas sin cerrar"]
    bare = _ASYM.sub(r"\1", _QUOTED.sub('""', line))
    stack: list[tuple[str, int]] = []
    for i, ch in enumerate(bare):
        if ch in "[({":
            stack.append((ch, i))
        elif ch in PAIRS:
            if not stack or stack[-1][0] != PAIRS[ch]:
                return [f"«{ch}» sin abrir"]
            stack.pop()
    if stack:
        return [f"«{stack[-1][0]}» sin cerrar"]
    # A [...] / {...} label whose text holds ( or ): fine only for the shapes
    # [( )] cylinder, [[ ]], {{ }}, and when the label is quoted.
    for m in re.finditer(r"\[([^\[\]]*)\]|\{([^{}]*)\}", bare):
        inner = (m.group(1) if m.group(1) is not None else m.group(2)).strip()
        if inner.startswith("(") and inner.endswith(")"):
            inner = inner[1:-1]
        if "(" in inner or ")" in inner:
            probs.append("paréntesis en una etiqueta sin comillas: escribe A[\"…(…)…\"]")
            break
    return probs


def lint_block(b: dict) -> list[tuple[int, str]]:
    """(line offset within the block, reason) for each problem."""
    if not b["closed"]:
        return [(0, "el bloque ```mermaid no se cierra")]
    content = _content(b["lines"])
    if not content:
        return [(0, "diagrama vacío")]
    k, first = content[0]
    words = first.split()
    kind = words[0].rstrip(":") if words else ""
    if kind not in TYPES:
        return [(k, f"«{kind}» no es un tipo de diagrama conocido (flowchart TD, sequenceDiagram, mindmap…)")]
    if kind in ("flowchart", "graph") and len(words) > 1 and words[1] not in DIRECTIONS:
        return [(k, f"dirección «{words[1]}» desconocida (TD, TB, BT, RL, LR)")]
    if len(content) == 1:
        return [(k, "el diagrama solo tiene la línea del tipo")]
    out = []
    if kind in STRICT:
        for k2, line in content[1:]:
            out += [(k2, why) for why in _label_problems(line)]
    return out


def lint(text: str) -> dict:
    report = {"tool": TOOL, "blocks": 0, "ok": 0, "problems": []}
    for i, b in enumerate(blocks(text), 1):
        report["blocks"] += 1
        probs = lint_block(b)
        if not probs:
            report["ok"] += 1
        report["problems"] += [{"block": i, "line": b["start"] + off, "reason": why} for off, why in probs]
    return report


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(args) != 1 or args[0] in ("-h", "--help"):
        print(__doc__)
        return 1 if len(args) != 1 else 0
    try:
        text = Path(args[0]).read_text(encoding="utf-8")
    except OSError as exc:
        print(json.dumps({"tool": TOOL, "error": str(exc)}, ensure_ascii=False))
        return 1
    report = lint(text)
    print(json.dumps(report, ensure_ascii=False))
    return 2 if report["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
