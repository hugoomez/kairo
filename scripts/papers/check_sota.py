#!/usr/bin/env python3
r"""Mechanically check a project's Estado-del-arte.md (or any synthesis note)
before it is committed: every citation and every number in a cited sentence.

    check_sota.py --vault <vault> --project-dir Projects/<slug> [--file Estado-del-arte.md] [--write]

The synthesis is the model's prose, but each claim about a paper carries a
locator — `P-0007 §4.2`, `P-0012 Tabla 3`, `[P-0015 §2; Fig. 3]`. With the
fresh-verifier's own resolver (`verifier_packet.resolve_citation`), nothing
judged by a model, this checks per citation:

  - the paper exists, belongs to the project and is not `send: never`;
  - the locator points at text in the note's `## Texto completo` / `## Resumen`;
  - that text is the paper's own (no model-text markers; for a note ingested by
    ingest_paper.py, it still matches the kept source bytes);
  - the paper's reference is not in conflict, retracted or withdrawn
    (`resolution_status`);

and per cited sentence (bullet or paragraph): every number in it — a decimal,
a percentage, a count ≥ 10, a power of ten — appears in the text its
citations point at (thousands separators and the minus sign aside). Years,
section numbers and the locators themselves are not numbers to check. A
number that is in none of the cited texts is the fingerprint of a figure
moved from one paper to another, or invented.

A multiplier (`3×`, `8x`) is a number to check whatever its size, and must
appear in the cited text as a multiplier (`3×`, `3x`, `3 times`, `3-fold`).

Tables are checked too. A row that cites (`P-0007 Tabla 2`) is checked like a
sentence. In a table whose header names papers (`| concepto | P-0007 | P-0012 |`,
the *Matriz de conceptos*), each cell is checked against its column's paper:
every number in it must appear in that paper's text, and a cell with content
but no locator of its own is listed as without locator.

A citation without a locator (`P-0007` alone) is listed, not failed: the rule
is "a locator where possible".

The map cites a paper's text, never its abstract — except a paper whose note
says `fulltext: abstract-only` (paywalled, no preprint), cited only as
`P-0007 Resumen` and marked «(solo abstract)» (a warning when the mark is
missing; a problem when the paper has full text). With the project known,
every paper of the project is either cited or declared in «Cobertura de
lectura» (`sin_cubrir`), and an abstract-only one is declared there as
`solo abstract: P-0007` (`solo_abstract_sin_declarar`): warnings.

«Cobertura de lectura» carries each summarizer's `lectura: P-0007 completo |
ficha + §… abiertas | solo ficha | solo abstract` line: reported as `lectura`,
with a warning when «Huecos identificados» cites a paper read «solo ficha».

None of this checks that a sentence says what its source says — only that its
locators and figures are real. `--packet FILE [--section "<heading>"]` writes
the fresh-verifier packet for that: every cited sentence (or row) of the
document, or of one `##` section, as an `Afirmación` followed by the verbatim
text its locators point at, built by the verifier's own renderer. The
fresh-verifier then hunts the sentences the source does not support. Each
part is also put in the packet store (`stored`: path + sha256): the verifier
gets that path and reads the file itself, never a retyped copy. A packet
larger than `--max-chars` (default 150,000) is split into parts — FILE,
FILE-2, … — each with its own header and "Parte: i de n", so every verifier
reads its part whole.

A value read off a plot is never literal: it is written `≈0.8 (leído de la
Figura 3, no literal)`, is not looked for in the text, and its sentence must
cite that figure (`P-0007 Figura 3`) — else it is a problem. In a table, the
figure is cited in that row's `fuente` cell (a *Matriz* cell names it itself).

An uncited sentence that states a figure or compares papers is checked by
nothing: the report lists it under `sin_cita` (a warning), and the support
packet hands it to the verifier. A model's inference across papers is marked
«(inferencia)» and cites each paper it combines: the report counts them
(`inferencias`) and warns on one citing a single paper. A small count with
a unit (`8 GPUs`, `4 pipeline stages`, `7 qubits`) is checked like any figure.
The support packet ends with where, in the cited text, each figure of each
sentence appears, so the verifier sees whether it belongs to the same quantity.

A figure with a unit glued to it (`530B`, `80GB`, `1.2k`) is checked whatever
its size, against the same figure and unit (or its spelled-out word) in the
cited text; a Spanish decimal comma (`1,1%`) is the same number as `1.1%`.

A power of ten is one value whatever its notation (`$10^{-6}$`, `2.5\times
10^{-3}`, `2.5×10⁻³`, `1e-6`, `\num{2.5e-3}`) and is compared as a value — the
exponent counts, the mantissa is never checked alone. Code parameters
(`[[144,12,12]]`, `\llbracket n,k,d \rrbracket`) are one tuple that must
appear as a tuple. A bare figure in a table column whose header says `(%)` is a
percentage (in the map and in the cited text). A count with a unit (`2048
GPUs`) is a figure, never a year. `2.5(3)` is 2.5; `0.7\,\%` is 0.7%. A small
integer bound to a parameter (`TP=8`, `d = 7`, `distance 9`) must appear bound
to the same parameter.

With `--write`, each failing citation / number is marked in place with a
visible «⚠ …» after it; nothing is removed. Prints a JSON report.
Exit codes: 0 clean · 3 problems found · 1 error · 2 bad input.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "ledger"))
from check_quotes import paper_projects, projects_of  # noqa: E402
sys.path.insert(0, str(HERE.parent / "security"))
import isolation  # noqa: E402
from send_guard import is_flagged, is_model_notes  # noqa: E402, I001  (path set by verifier_packet)
from verifier_packet import (  # noqa: E402
    TOOL_ID as VERIFIER_PACKET_ID,
    body_sections,
    find_paper,
    fm_scalar,
    read_text,
    render_justification,
    resolve_citation,
    split_frontmatter,
)

TOOL = "kairo/check_sota@1.10.0"
_LOC_PART = (r"(?:§\s*[A-Za-zÁÉÍÓÚáéíóú0-9][\w.]*(?:\s*[–-]\s*§?\s*[\w.]+)?"
             r"|(?:Tabla|Table|Figura|Figure|Fig\.?|App(?:endix)?\.?|Apéndice)\s*[A-Z]?\d+(?:\.\d+)*"
             r"|(?:Eq\.?|Ec\.?|Ecuaci[oó]n|Equation)\s*\(?\s*[A-Z]?\d+\s*\)?"
             r"|(?:Resumen|Abstract)\b)")
ABSTRACT_LOC = re.compile(r"(?:§\s*)?\b(?:Resumen|Abstract)\b", re.I)
SOLO_ABSTRACT = "(solo abstract)"
CITE = re.compile(rf"\b(P-\d{{4,5}})((?:[ ,;]*{_LOC_PART})*)")
# A unit glued to a figure ("530B", "80GB", "7B", "1.2k", "312 TFLOPS") is a result
# whatever its size: model sizes, memory and throughput live in these.
UNIT = r"(?:[kKMBT]|[KMGTP]i?B|[KMGTPE]FLOP[sS]?)"
# LaTeX spacing between a figure and its percent sign: "0.7\,\%", "0.7~\%"
_TEX_SP = r"(?:\s|\\[,;:! ]|~)*"
NUMBER = re.compile(r"(?<![\w.§-])[-−]?\d[\d,.]*"
                    r"(?:" + _TEX_SP + r"\\?%|e[-−]?\d+|\s*[×x](?![\w\d])|\s?" + UNIT + r"(?![\w]))?(?![\w])")
MULT = re.compile(r"\s*[×x]$")
SUFFIX = re.compile(r"\s?(" + UNIT + r")$")
UNIT_WORDS = {"k": "thousand|mil", "m": "million|millones", "b": "billion|mil millones", "t": "trillion|billones"}
HEADER_PAPER = re.compile(r"^\[{0,2}(P-\d{4,5})\]{0,2}$")
# a value read off a plot: never literal, always tied to its figure
PLOT = re.compile(r"≈\s*[-−]?\d[^()\n]{0,40}?\(\s*le[ií]do de la Figura\s*(\d+)[^)]*\)", re.I)
COUNT_UNITS = r"(?:GPU|TPU|node|qubit|layer|stage|expert|core|device|bit|head|round|replica|accelerator|socket)"
COUNT = re.compile(r"(?<![\w.,])([1-9])\s+((?:[A-Za-z-]+\s+)?" + COUNT_UNITS + r"s?)\b", re.I)
# a count token of any size ("8 GPUs", "2048 GPUs"): the figure with its unit
COUNT_TOK = re.compile(r"(\d[\d,]*)\s+((?:[A-Za-z-]+\s+)?" + COUNT_UNITS + r"s?)", re.I)
_UNIT_AFTER = re.compile(r"^\s+((?:[A-Za-z-]+\s+)?" + COUNT_UNITS + r"s?)\b", re.I)
# A power of ten, however it is written: $10^{-6}$, 2.5\times 10^{-3}, 2.5 \cdot 10^{-3},
# 2.5×10⁻³, 1e-6 (and \num{2.5e-3} once its wrapper is dropped). One value; the exponent counts.
POW = re.compile(
    r"(?<![\w.,])(?:(\d+(?:[.,]\d+)?)" + _TEX_SP + r"(?:\\times|\\cdot|×|·|\*|x)" + _TEX_SP + r")?"
    r"(?<![\d.])10(?:\s*\^\s*\{?\s*([-−–+]?)\s*(\d+)\s*\}?|([⁻⁺]?)([⁰¹²³⁴⁵⁶⁷⁸⁹]+))"
    r"|(?<![\w.,])(\d+(?:[.,]\d+)?)[eE]([-−+]?\d+)(?![\w])")
_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
_TEX_NUM = re.compile(r"\\(?:num|SI)\{([^{}]*)\}")
# Code parameters [[n,k,d]] / [n,k,d] (any spacing, LaTeX \llbracket … \rrbracket): one tuple
CODE = re.compile(r"(?:\[\[|\\llbracket|⟦)\s*(\d+(?:\s*,\s*\d+){1,3})\s*(?:\]\]|\\rrbracket|⟧)"
                  r"|(?<!\[)\[\s*(\d+(?:\s*,\s*\d+){2})\s*\](?!\])")
CODE_TOK = re.compile(r"(\[\[?)(\d+(?:,\d+)+)\]\]?")
# A small integer bound to a parameter ("TP=8", "d = 7", "distance 9") is a figure
PARAM_WORDS = r"(?:distance|distancia|depth|profundidad|degree|grado|rank|rango)"
PARAM = re.compile(r"(?<![\w\\])([A-Za-z][A-Za-z_]{0,11})\s*=\s*\$?\s*([0-9])(?![\d]|[.,]\d)"
                   r"|\b(" + PARAM_WORDS + r")\s+(?:of\s+|de\s+)?\$?([0-9])(?![\d]|[.,]\d)", re.I)
PARAM_TOK = re.compile(r"([A-Za-z][A-Za-z_]{0,11}) = ([0-9])|(" + PARAM_WORDS + r") ([0-9])", re.I)
PARAM_ALIAS = {"d": ["distance", "distancia"], "distance": ["d", "distancia"], "distancia": ["d", "distance"],
               "depth": ["profundidad"], "profundidad": ["depth"], "degree": ["grado"], "grado": ["degree"],
               "rank": ["rango"], "rango": ["rank"]}
# "2.5(3)": the uncertainty in parentheses is not a second figure
_UNCERTAINTY = re.compile(r"(?<=\d[.,]\d)(\d*)(\(\d{1,3}\))")
FIG_CITE = re.compile(r"\b(?:Figura|Figure|Fig\.?)\s*(\d+)", re.I)
BAD_STATUS = {"mismatch": "referencia en conflicto (resolution_status: mismatch)",
              "retracted": "paper retractado", "withdrawn": "paper retirado"}


COVERAGE_HEADING = "Cobertura de lectura"


def blocks(text: str) -> list[tuple[int, int, str]]:
    """(start, end, text) of each bullet or paragraph, frontmatter excluded."""
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    off = m.end() if m else 0
    # `## Cobertura de lectura` names sections nobody read: its P-XXXX § lines are
    # not claims, so they are never checked as citations
    cov = re.search(r"(?m)^## " + re.escape(COVERAGE_HEADING) + r"\s*$", text)
    cov_end = (cov.end() + (re.search(r"(?m)^## ", text[cov.end():]) or re.search(r"\Z", text[cov.end():])).start()
               if cov else -1)
    out = []
    for mm in re.finditer(r"(?:^[ \t]*[-*] .*(?:\n(?![ \t]*[-*] |\s*\n|#).*)*|^(?![ \t]*[-*] |#|\|).+(?:\n(?![ \t]*[-*] |\s*\n|#).+)*)",
                          text[off:], re.M):
        if cov and cov.start() <= off + mm.start() < cov_end:
            continue
        out.append((off + mm.start(), off + mm.end(), mm.group(0)))
    return out


# a model's inference across papers, marked as such by the summarizer / synthesizer
INFERENCE = re.compile(r"«?\(inferencia\)»?")
COMPARISON = re.compile(r"\b(?:mejor(?:es)? que|peor(?:es)? que|superior(?:es)? a|inferior(?:es)? a|más (?:rápid|lent)\w* que|"
                        r"supera\w*|outperform\w*|better than|worse than|faster than|slower than|frente a|"
                        r"en comparación con|compared (?:to|with)|vs\.?)(?!\w)", re.I)


def uncited_claim(block: str) -> bool:
    """An uncited sentence that states a figure or compares papers: nothing checks it."""
    body = re.sub(r"(?m)^\*as of [^*\n]*\*\s*$", " ", block)          # a staleness line is not a claim
    if not body.strip() or body.lstrip().startswith(">"):
        return False
    return bool(numbers(body)) or bool(COMPARISON.search(body))


def _not_synthesis(text: str) -> list[tuple[int, int]]:
    """Spans that are not the synthesis's prose: the degraded-coverage notice and the
    appended Búsqueda ejecutada block (counts, queries)."""
    out = []
    for m in re.finditer(r"(?m)^## ⚠️ Cobertura degradada.*$", text):
        nxt = re.search(r"(?m)^## ", text[m.end():])
        out.append((m.start(), m.end() + nxt.start() if nxt else len(text)))
    b = re.search(r"(?m)^#{2,3} .*B[uú]squeda ejecutada", text)
    if b:
        out.append((b.start(), len(text)))
    return out


def plot_readings(sentence: str) -> list[str]:
    """The figure numbers whose plots a sentence reads values off."""
    return [m.group(1) for m in PLOT.finditer(sentence)]


def cited_figures(sentence: str) -> set[str]:
    """The figure numbers a sentence's citations point at."""
    return {f for m in CITE.finditer(sentence) for f in FIG_CITE.findall(m.group(2))}


def _blank(rx: re.Pattern, s: str) -> str:
    """`s` with every match of `rx` blanked out, offsets unchanged."""
    return rx.sub(lambda m: " " * len(m.group(0)), s)


def _unwrap_num(s: str) -> str:
    r"""`\num{2.5e-3}` → `     2.5e-3 `: the wrapper blanked, offsets unchanged."""
    return _TEX_NUM.sub(lambda m: " " * (m.start(1) - m.start()) + m.group(1) + " " * (m.end() - m.end(1)), s)


def _pow_parts(m: re.Match) -> tuple[str, int] | None:
    """(mantissa as written, exponent) of a POW match."""
    if m.group(6):
        mant, sign, digits = m.group(6), "", m.group(7)
    elif m.group(3):
        mant, sign, digits = m.group(1) or "1", m.group(2), m.group(3)
    else:
        mant, sign, digits = m.group(1) or "1", m.group(4), m.group(5).translate(_SUPERSCRIPT)
    digits = digits.replace("−", "-").replace("+", "")
    sign = "-" if sign in ("-", "−", "–", "⁻") or digits.startswith("-") else ""
    try:
        return mant.replace(",", "."), int(sign + digits.lstrip("-"))
    except ValueError:
        return None


_LABEL_NUM = re.compile(r"\b(?:Tablas?|Tables?|Figuras?|Figures?|Figs?\.?|Ec\.?|Eqs?\.?|Ecuaci[oó]n|Equation|"
                        r"Ap[eé]ndice|Appendix|Secci[oó]n|Section|Sec\.)\s*\(?[A-Z]?\d+(?:\.\d+)*\)?")


def numbers_at(sentence: str, every: bool = False) -> list[tuple[str, int]]:
    """Each figure worth checking in a sentence with its offset, citations, ids,
    dates and labels removed. `every`: small integers and years are figures too
    (a reading card's claim against its own quote)."""
    s = PLOT.sub(lambda m: " " * len(m.group(0)), _unwrap_num(sentence))
    for rx in (CITE, re.compile(r"\b(?:H|E|C|PROJ|ADR|F)-\d+\b"), re.compile(r"\d{4}-\d{2}-\d{2}"),
               re.compile(r"§\s*[\w.]+"), _LABEL_NUM):
        s = _blank(rx, s)          # a label is not a figure: "Figura 12", "Table 3", "Eq. (4)"
    out: list[tuple[str, int]] = []
    for m in POW.finditer(s):
        parts = _pow_parts(m)
        if parts:
            out.append((f"{parts[0]}e{parts[1]}", m.start()))
    s = _blank(POW, s)
    for m in CODE.finditer(s):
        body = re.sub(r"\s+", "", m.group(1) or m.group(2))
        out.append((f"[[{body}]]" if m.group(1) else f"[{body}]", m.start()))
    s = _blank(CODE, s)
    if not every:
        for m in PARAM.finditer(s):
            out.append((f"{m.group(1)} = {m.group(2)}" if m.group(1) else f"{m.group(3)} {m.group(4)}", m.start()))
        s = _blank(PARAM, s)
    for m in COUNT.finditer(s):
        out.append((f"{m.group(1)} {m.group(2)}", m.start()))  # "8 GPUs": a small count with its unit is a result
    s = _blank(COUNT, s)
    s = _UNCERTAINTY.sub(lambda m: m.group(1) + " " * len(m.group(2)), s)
    for m in NUMBER.finditer(s):
        tok = m.group(0).strip().rstrip(".,")
        digits = re.sub(r"[^\d.]", "", re.split(r"[×x]", tok)[0]).strip(".")
        if not digits:
            continue
        if MULT.search(tok) or SUFFIX.search(tok):
            out.append((tok, m.start()))      # a multiplier or a sized figure is a result whatever its size
            continue
        is_int = "." not in digits and "%" not in tok and "×" not in tok and "e" not in tok.lower()
        if is_int and re.fullmatch(r"\d+", tok) and (u := _UNIT_AFTER.match(s[m.end():])):
            out.append((f"{tok} {u.group(1)}", m.start()))   # "2048 GPUs": a count, never a year
            continue
        if not every and is_int and (int(digits.replace(".", "") or 0) < 10
                                     or re.fullmatch(r"(?:19|20)\d{2}", digits)):
            continue                      # small counts and years are not results
        out.append((tok, m.start()))
    return sorted(out, key=lambda x: x[1])


def numbers(sentence: str, every: bool = False) -> list[str]:
    """Numbers worth checking in a sentence, with citations, ids and dates removed."""
    return [t for t, _ in numbers_at(sentence, every)]


def _canon(tok: str) -> str:
    t = tok.replace("−", "-").replace("\\%", "%").replace("~", "")
    t = re.sub(r"\\[,;:! ]", "", t).replace(" ", "")
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)     # thousands separators
    t = re.sub(r"(?<=\d),(?=\d)", ".", t)          # a Spanish decimal comma: 1,1 = 1.1
    return t.rstrip("%")


_SCI_TOK = re.compile(r"^(\d+(?:\.\d+)?)(?:e|×10\^?)([-−]?\d+)$", re.IGNORECASE)


def _sci_values(text: str) -> list[float]:
    """Every power of ten in `text` as a value, however the paper writes it."""
    out = []
    for m in POW.finditer(_unwrap_num(text)):
        parts = _pow_parts(m)
        if not parts:
            continue
        try:
            out.append(float(parts[0]) * 10 ** parts[1])
        except (ValueError, OverflowError):
            continue
    return out


def _plain_values(text: str) -> list[float]:
    t = re.sub(r"(?<=\d)[,  ](?=\d{3}(?!\d))", "", text)
    out = []
    for m in re.finditer(r"(?<![\w.])\d+(?:\.\d+)?(?![\w]|\.\d)", t):
        try:
            out.append(float(m.group(0)))
        except ValueError:
            continue
    return out


def _tuple_in(tok: str, texts: list[str]) -> bool:
    want = CODE_TOK.fullmatch(tok).group(2).split(",")
    for t in texts:
        for m in CODE.finditer(t):
            if re.sub(r"\s+", "", m.group(1) or m.group(2)).split(",") == want:
                return True
    return False


def _param_in(pm: re.Match, texts: list[str]) -> bool:
    name, val = (pm.group(1), pm.group(2)) if pm.group(1) else (pm.group(3), pm.group(4))
    names = [name] + PARAM_ALIAS.get(name.lower(), [])
    alts = "|".join(re.escape(n) if len(n) == 1 else f"(?i:{re.escape(n)})" for n in names)
    rx = re.compile(rf"(?<![\w\\])(?:{alts})\s*(?:=|:|of\b|de\b)?\s*\$?\s*{val}(?![\d]|[.,]\d)")
    return any(rx.search(t) for t in texts)


def number_in(tok: str, texts: list[str]) -> bool:
    cm = COUNT_TOK.fullmatch(tok)
    if cm:
        words = cm.group(2).split()
        unit = re.sub(r"s$", "", words[-1], flags=re.I)
        n = cm.group(1).replace(",", "")
        rx = re.compile(rf"(?<![\w.,]){n}\s*-?\s*(?:[A-Za-z-]+\s+)?{re.escape(unit)}s?\b", re.I)
        return any(rx.search(re.sub(r"(?<=\d)[,  ](?=\d{3}(?!\d))", "", t)) for t in texts)
    if CODE_TOK.fullmatch(tok):
        return _tuple_in(tok, texts)       # [[144,12,12]]: the tuple, never its parts
    pm = PARAM_TOK.fullmatch(tok)
    if pm:
        return _param_in(pm, texts)        # "d = 7": the value bound to the same parameter
    sci = _SCI_TOK.match(_canon(tok))
    if sci:
        # one value, however it is written: the sentence's 1e-7 is the paper's $10^{-7}$;
        # never its mantissa or its 10 alone
        v = float(sci.group(1)) * 10 ** int(sci.group(2).replace("−", "-"))
        return any(abs(x - v) <= 1e-9 * abs(v) for t in texts for x in _sci_values(t) + _plain_values(t))
    want = _canon(tok)
    unit = SUFFIX.search(tok) if not MULT.search(tok) else None
    if unit:
        # the figure with the same unit, glued, spaced or hyphenated, or spelled out
        core = re.escape(re.sub(r"[^\d.]", "", _canon(tok[:unit.start()])))
        u = unit.group(1)
        alt = UNIT_WORDS.get(u.lower()) if len(u) == 1 else None
        rx = re.compile(rf"(?<![\d.]){core}\s*-?\s*(?:{re.escape(u)}(?![a-z])" + (rf"|(?:{alt})\b" if alt else "") + ")",
                        re.IGNORECASE if len(u) > 1 else 0)
        return any(rx.search(re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)) for t in texts)
    if MULT.search(tok):
        core = re.sub(r"[^\d.]", "", MULT.sub("", tok))
        rx = re.compile(rf"(?<![\d.]){re.escape(core)}\s*(?:×|x\b|times\b|-?fold\b|veces\b)", re.IGNORECASE)
        return any(rx.search(re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)) for t in texts)
    core = re.sub(r"[^\d.]", "", want.split("×")[0]).strip(".")
    pct = tok.strip().endswith("%")
    return bool(core) and any(_figure_in(core, pct, t) for t in texts)


# A number right after one of these is a label, not a figure: "Table 12", "Eq. (12)", "§4.2".
_LABEL_BEFORE = re.compile(
    r"(?:\b(?:tables?|tablas?|figures?|figuras?|figs?|sections?|secciones|sección|secs?|eqs?|ecs?|equations?|"
    r"ecuaci[oó]n(?:es)?|appendix|ap[eé]ndices?|algorithms?|algoritmos?|theorems?|teoremas?|lemmas?|lemas?|"
    r"definitions?|definici[oó]n|corollary|corolario|propositions?|proposici[oó]n|chapters?|cap[ií]tulos?|"
    r"steps?|lines?|refs?)\.?|§)\s*\(?\s*$", re.IGNORECASE)
_REF_BEFORE = re.compile(r"\[[\d,\s–-]*$")         # "[12]", "[3, 12, 15]": reference numbers
_REF_AFTER = re.compile(r"^[\d,\s–-]*\]")
_PCT_AFTER = re.compile(r"^(?:\(\d{1,3}\))?" + _TEX_SP + r"(?:\\?%|per\s?cent\b|por\s+ciento\b)", re.IGNORECASE)


def _pct_column(t: str, pos: int) -> bool:
    """Whether `pos` sits in a Markdown table cell whose column header says `%`
    ("accuracy (%)", "[%]"): that cell's 52.1 is 52.1%."""
    ls = t.rfind("\n", 0, pos) + 1
    le = t.find("\n", pos)
    line = t[ls:le if le >= 0 else len(t)]
    if not line.lstrip().startswith("|"):
        return False
    col = line[:pos - ls].count("|")
    header = line
    for prev in reversed(t[:ls].split("\n")[:-1]):
        if not prev.lstrip().startswith("|"):
            break
        header = prev
    cells = header.split("|")
    return header is not line and col < len(cells) and "%" in cells[col]


def _figure_in(core: str, pct: bool, text: str) -> bool:
    """`core` as a whole number in `text` — never a digit run inside another number,
    never a label or reference number; with `pct`, followed by a percent sign or word."""
    t = text.replace("−", "-")
    t = re.sub(r"(?<=\d)[,\u2009\u202f](?=\d{3}(?!\d))", "", t)       # thousands separators
    for m in re.finditer(rf"(?<![\d.]){re.escape(core)}(?!\d|\.\d)", t):
        before, after = t[max(0, m.start() - 30):m.start()], t[m.end():m.end() + 20]
        if _LABEL_BEFORE.search(before) or (_REF_BEFORE.search(before) and _REF_AFTER.match(after)):
            continue
        if pct and not _PCT_AFTER.match(after) and not _pct_column(t, m.start()):
            continue
        return True
    return False


def tables(text: str) -> list[dict]:
    """Every Markdown table body row: its span, cells and the header's cells."""
    out = []
    lines = text.splitlines(keepends=True)
    starts, pos = [], 0
    for ln in lines:
        starts.append(pos)
        pos += len(ln)
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|") and i + 1 < len(lines) \
                and re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", lines[i + 1]) and "-" in lines[i + 1]:
            header = _cells(lines[i])
            j = i + 2
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                row = lines[j].rstrip("\r\n")
                out.append({"start": starts[j], "end": starts[j] + len(row), "row": row,
                            "cells": _cells(row), "header": header})
                j += 1
            i = j
        else:
            i += 1
    return out


def _cells(row: str) -> list[str]:
    s = row.strip()
    s = s[1:] if s.startswith("|") else s
    s = s[:-1] if s.endswith("|") else s
    return [c.strip() for c in re.split(r"(?<!\\)\|", s)]


def paper_text(vault: str, pid: str) -> list[str] | None:
    """The paper's own text (## Resumen + ## Texto completo), or None if it may not be read."""
    path = find_paper(vault, pid)
    if not path or is_model_notes(Path(path).resolve()) or is_flagged(Path(path)):
        return None
    secs = dict(body_sections(split_frontmatter(read_text(path))[1]))
    return [secs.get("Resumen", ""), secs.get("Texto completo", "")]


def cited_units(vault: str, project: str | None, block: str, start: int, report: dict,
                per: list | None = None) -> tuple[list, list[str]]:
    """Check every citation in `block`; return (its matches, the text they point at).
    `per`, when given, gets one (match, its own units) pair per citation."""
    cites = list(CITE.finditer(block))
    units: list[str] = []
    for m in cites:
        mark = len(units)
        _one_citation(vault, project, m, start, report, units)
        if per is not None:
            per.append((m, units[mark:]))
    return cites, units


def _one_citation(vault: str, project: str | None, m, start: int, report: dict, units: list[str]) -> None:
    """Check one citation; add the text it points at to `units`."""
    pid, span = m.group(1), m.group(2).strip(" ,;")
    report["citations"] += 1
    where = {"citation": m.group(0).strip(), "offset": start + m.end()}
    path = find_paper(vault, pid)
    if not path:
        report["problems"].append({**where, "severity": "crítico", "reason": f"{pid} no existe en Papers/"})
        return
    fm, _ = split_frontmatter(read_text(path))
    if project and project not in paper_projects(vault, pid):
        report["problems"].append({**where, "severity": "importante",
                                   "reason": f"{pid} no es un paper del proyecto {project}"})
    status = (fm_scalar(fm, "resolution_status") or "").strip()
    if status in BAD_STATUS:
        report["problems"].append({**where, "severity": "crítico", "reason": BAD_STATUS[status]})
    if not span:
        report["without_locator"].append(pid)
        res = resolve_citation(vault, pid, "§Resumen")
        units.extend(u.removeprefix("[## Resumen]\n") for u in res["units"])
        return
    if ABSTRACT_LOC.search(span) and (fm_scalar(fm, "fulltext") or "").strip() != "abstract-only":
        # the map cites a paper's text; only a paper with no full text is cited by its abstract
        report["problems"].append({**where, "severity": "importante",
                                   "reason": f"{pid} tiene texto completo: cita la sección del texto, no el "
                                             "Resumen (solo un paper fulltext: abstract-only se cita por su Resumen)"})
    res = resolve_citation(vault, pid, span)
    if res["note"] and not res["units"]:
        report["problems"].append({**where, "severity": "crítico",
                                   "reason": f"el localizador no señala texto: {res['note']}"})
    if res["provenance"]:
        report["problems"].append({**where, "severity": "crítico",
                                   "reason": "el texto citado no es del paper: " + "; ".join(res["provenance"])})
    units.extend(u.removeprefix("[## Resumen]\n") for u in res["units"])


_SENTENCE = re.compile(r"(?:[^.;!?]|[.](?=\d))+(?:[.;!?]|$)")      # a decimal point ends no sentence


def check_attributed(block: str, per: list, offset: int, report: dict) -> None:
    """Every figure against the paper whose citation follows it in its sentence (see below
    for the order): a figure of one cited paper never passes as another's."""
    if len({m.group(1) for m, _ in per}) < 2:
        check_numbers(block, [u for _, us in per for u in us],
                      ", ".join(m.group(0).strip() for m, _ in per), offset, report)
        return
    every = [u for _, us in per for u in us]
    masked = CITE.sub(lambda c: " " * len(c.group(0)), block)       # same offsets, citations blanked
    for sm in _SENTENCE.finditer(block):
        if not sm.group(0).strip():
            continue
        mine = [x for x in per if sm.start() <= x[0].start() < sm.end()]
        for tok, rel in numbers_at(masked[sm.start():sm.end()]):
            pos = sm.start() + rel
            # its sentence's next citation, else that sentence's last one, else the
            # paragraph's next citation (one that closes the bullet), else the last before
            owner = next((x for x in mine if x[0].start() >= pos), None) \
                or next((x for x in reversed(mine) if x[0].start() < pos), None) \
                or next((x for x in per if x[0].start() >= pos), None) \
                or [x for x in per if x[0].start() < pos][-1]
            report["numbers_checked"] += 1
            if number_in(tok, owner[1]):
                continue
            cit = owner[0].group(0).strip()
            why = (f"la cifra «{tok}» es de otro paper citado en el mismo párrafo, no de {cit}"
                   if number_in(tok, every) else f"la cifra «{tok}» no aparece en el texto citado")
            report["problems"].append({"citation": cit, "offset": offset, "severity": "importante",
                                       "number": tok, "reason": why})


def row_numbers(t: dict) -> list[str]:
    """A table row's figures, cell by cell: a bare figure under a header that says
    `%` ("acc. (%)", "[%]") is a percentage, as the reader reads it."""
    out = []
    for i, cell in enumerate(t["cells"]):
        head = t["header"][i] if i < len(t["header"]) else ""
        for tok in numbers(cell):
            plain = re.fullmatch(r"[-−]?\d[\d,.]*", tok) is not None
            out.append(tok + "%" if "%" in head and plain else tok)
    return out


def check_numbers(block: str, units: list[str], citation: str, offset: int, report: dict,
                  tokens: list[str] | None = None) -> None:
    for tok in (numbers(block) if tokens is None else tokens):
        report["numbers_checked"] += 1
        if not number_in(tok, units):
            report["problems"].append({"citation": citation, "offset": offset, "severity": "importante",
                                       "number": tok, "reason": f"la cifra «{tok}» no aparece en el texto citado"})


def _check_plots(text: str, figures: set[str], offset: int, report: dict) -> None:
    """A value read off a plot must cite that figure (`figures`: the ones cited)."""
    for fig in plot_readings(text):
        if fig not in figures:
            report["problems"].append({"citation": None, "offset": offset, "severity": "importante",
                                       "number": f"≈ (Figura {fig})",
                                       "reason": f"valor leído de la Figura {fig} sin citar esa figura "
                                                 "(P-XXXX Figura " + fig + ")"})


FUENTE_HEADER = re.compile(r"^(?:fuentes?|sources?)$", re.I)


def check_tables(vault: str, project: str | None, text: str, report: dict) -> None:
    for t in tables(text):
        cols = {i: m.group(1) for i, h in enumerate(t["header"]) if (m := HEADER_PAPER.match(h))}
        # a plot reading in a row is tied to its figure in that row's `fuente` cell (or,
        # with no such column, anywhere in the row); a matrix cell names it itself
        fuente = next((t["cells"][i] for i, h in enumerate(t["header"])
                       if FUENTE_HEADER.match(h) and i < len(t["cells"])), None)
        if cols and not CITE.search(t["row"]):
            for cell in t["cells"]:
                _check_plots(cell, set(FIG_CITE.findall(PLOT.sub(" ", cell))), t["end"], report)
        else:
            _check_plots(t["row"], cited_figures(fuente if fuente is not None else t["row"]), t["end"], report)
        if CITE.search(t["row"]):
            check_abstract_marks(vault, t["row"], report)
            cites, units = cited_units(vault, project, t["row"], t["start"], report)
            check_numbers(t["row"], units, ", ".join(c.group(0).strip() for c in cites), t["end"], report,
                          row_numbers(t))
            report["table_rows_checked"] += 1
            continue
        if not cols:
            continue                      # a table that cites nothing is not a claim about papers
        report["table_rows_checked"] += 1
        for i, pid in cols.items():
            cell = t["cells"][i] if i < len(t["cells"]) else ""
            if not cell or cell in ("—", "-", "–"):
                continue
            report["without_locator"].append(pid)
            src = paper_text(vault, pid)
            if src is None:
                report["problems"].append({"citation": pid, "offset": t["end"], "severity": "crítico",
                                           "reason": f"{pid} (columna de la tabla) no existe o no se puede leer"})
                continue
            check_numbers(cell, src, f"{pid} (columna)", t["end"], report)


def number_contexts(tokens: list[str], units: list[str]) -> dict[str, str | None]:
    """Each figure → the first sentence of the cited text it appears in (None: in none)."""
    out: dict[str, str | None] = {}
    for tok in tokens:
        out[tok] = None
        for u in units:
            for sm in _SENTENCE.finditer(u):
                sent = " ".join(sm.group(0).split())
                if sent and number_in(tok, [sent]):
                    out[tok] = sent
                    break
            if out[tok]:
                break
    return out


def check(vault: str, project: str | None, text: str) -> dict:
    report = {"tool": TOOL, "citations": 0, "without_locator": [], "problems": [], "numbers_checked": 0,
              "table_rows_checked": 0,
              # values read off a plot: never checkable against text — say how many there are
              "plot_readings": len(PLOT.findall(text))}
    report["sin_cita"] = []
    report["inferencias"] = len(INFERENCE.findall(text))
    skip = _not_synthesis(text)
    for start, end, block in blocks(text):
        _check_plots(block, cited_figures(block), end, report)
        if not CITE.search(block):
            if not any(a <= start < b for a, b in skip) and uncited_claim(block):
                report["sin_cita"].append(" ".join(block.split()))
            continue
        if INFERENCE.search(block) and len({m.group(1) for m in CITE.finditer(block)}) < 2:
            report.setdefault("warnings", []).append(
                "una inferencia «(inferencia)» cita un solo paper: una inferencia entre papers cita cada paper "
                "que combina — " + " ".join(block.split())[:160])
        check_abstract_marks(vault, block, report)
        per: list = []
        cited_units(vault, project, block, start, report, per)
        check_attributed(block, per, end, report)
    check_tables(vault, project, text, report)
    report["without_locator"] = sorted(set(report["without_locator"]))
    # what the map's summarizers said they did not read: the reader must see it
    try:
        cov = section_text(text, COVERAGE_HEADING)
    except ValueError:
        cov = ""
    report["cobertura_lectura"] = ("ausente" if not cov.strip() else
                                   f"{len(re.findall(r'(?m)^- .*no leído', cov))} secciones no leídas declaradas")
    if not cov.strip():
        # a warning, not a problem: maps written before the section existed stay valid
        report.setdefault("warnings", []).append(
            f"falta la sección `## {COVERAGE_HEADING}` (lo que el map no leyó): el sota-synthesizer la escribe "
            "siempre; sin ella el lector no sabe qué secciones quedaron sin leer")
    check_reading_depth(text, cov, report)
    if project:
        check_coverage(vault, project, text, cov, report)
    return report


READING = re.compile(r"(?m)^[ \t]*[-*]?\s*lectura:\s*(P-\d{4,5})\s+(.+?)\s*$")
GAPS_HEADING = re.compile(r"(?m)^## (?:\d+\.\s*)?Huecos identificados\s*$")


def check_reading_depth(text: str, cov: str, report: dict) -> None:
    """How deep each paper was read, as the summarizers returned it (`lectura: P-XXXX
    completo | ficha + §… abiertas | solo ficha | solo abstract`); a gap resting on a
    paper read only through its card is a warning: the card is 8–25 items, not the paper."""
    report["lectura"] = {m.group(1): m.group(2) for m in READING.finditer(cov)}
    g = GAPS_HEADING.search(text)
    if not g:
        return
    nxt = re.search(r"(?m)^## ", text[g.end():])
    gaps = text[g.end():g.end() + nxt.start()] if nxt else text[g.end():]
    thin = sorted({m.group(1) for m in CITE.finditer(gaps)
                   if report["lectura"].get(m.group(1), "").lower().startswith("solo ficha")})
    if thin:
        report.setdefault("warnings", []).append(
            "«Huecos identificados» se apoya en papers leídos solo por su ficha (lectura: solo ficha): "
            + ", ".join(thin) + " — un hueco «no aparece» necesita las secciones abiertas")


def project_papers(vault: str, project: str) -> list[tuple[str, list[str]]]:
    """(id, frontmatter) of every paper note of the project (reading notes never)."""
    out = []
    for path in sorted(Path(vault, "Papers").glob("P-*.md")):
        if not path.is_file() or is_model_notes(path.resolve()):
            continue
        fm = split_frontmatter(read_text(str(path)))[0]
        if project in projects_of(fm):
            out.append((path.name.split(" ")[0].removesuffix(".md"), fm))
    return out


def check_coverage(vault: str, project: str, text: str, cov: str, report: dict) -> None:
    """Every paper of the project is cited by the map or declared in «Cobertura de
    lectura»; an abstract-only one is declared there as `solo abstract: P-XXXX`."""
    body = section_text(text, None)
    cited = {m.group(1) for m in CITE.finditer(body)} | {
        m.group(1) for t in tables(body) for h in t["header"] if (m := HEADER_PAPER.match(h))}
    loose, abstract = [], []
    for pid, fm in project_papers(vault, project):
        if (fm_scalar(fm, "fulltext") or "").strip() == "abstract-only" \
                and not re.search(rf"solo abstract:\s*{re.escape(pid)}\b", cov, re.I):
            abstract.append(pid)
        elif pid not in cited and not re.search(rf"\b{re.escape(pid)}\b", cov):
            loose.append(pid)
    report["sin_cubrir"] = loose
    report["solo_abstract_sin_declarar"] = abstract
    if loose:
        report.setdefault("warnings", []).append(
            "papers del proyecto que el mapa ni cita ni declara en «Cobertura de lectura»: " + ", ".join(loose)
            + " — cada uno se cita o se lista allí con el motivo")
    if abstract:
        report.setdefault("warnings", []).append(
            "papers solo abstract sin declarar en «Cobertura de lectura» como `solo abstract: P-XXXX`: "
            + ", ".join(abstract))


def check_abstract_marks(vault: str, block: str, report: dict) -> None:
    """A sentence citing an abstract-only paper's Resumen says so: «(solo abstract)»."""
    for m in CITE.finditer(block):
        if not ABSTRACT_LOC.search(m.group(2) or "") or SOLO_ABSTRACT in block:
            continue
        path = find_paper(vault, m.group(1))
        if path and (fm_scalar(split_frontmatter(read_text(path))[0], "fulltext") or "").strip() == "abstract-only":
            report.setdefault("warnings", []).append(
                f"{m.group(0).strip()}: una frase que cita un paper solo abstract lleva «{SOLO_ABSTRACT}» — "
                + " ".join(block.split())[:160])


def section_text(text: str, heading: str | None) -> str:
    """The document (frontmatter dropped), or one `## heading` section of it."""
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    body = text[m.end():] if m else text
    if heading is None:
        return body
    parts = re.split(r"(?m)^## ", body)
    for part in parts[1:]:
        title, _, rest = part.partition("\n")
        if title.strip() == heading.strip():
            return rest
    raise ValueError(f"no '## {heading}' section")


MAX_PACKET_CHARS = 150_000          # ~40k tokens: one verifier reads a part whole, never a truncated one


def section_claims(text: str, heading: str | None) -> list[str]:
    body = section_text(text, heading)
    claims = [" ".join(b.split()) for _, _, b in blocks(body) if CITE.search(b)]
    return claims + [t["row"].strip() for t in tables(body) if CITE.search(t["row"])]


def section_uncited(text: str, heading: str | None) -> list[str]:
    """The uncited sentences of the document (or one section) that state a figure or
    compare papers — what `sin_cita` lists."""
    body = section_text(text, heading)
    skip = _not_synthesis(body)
    return [" ".join(b.split()) for s, _, b in blocks(body)
            if not CITE.search(b) and not any(x <= s < y for x, y in skip) and uncited_claim(b)]


def support_packets(vault: str, text: str, label: str, heading: str | None,
                    max_chars: int = MAX_PACKET_CHARS) -> list[tuple[str, dict]]:
    """The support packet, split into parts of at most `max_chars` (a single
    sentence whose cited text is larger still goes alone, whole): one fresh
    verifier per part, so none receives a packet too large to read."""
    claims = section_claims(text, heading)
    groups: list[list[str]] = [[]]
    size = 0
    for c in claims:
        n = len(support_packet(vault, text, label, heading, [c], with_uncited=False)[0])
        if groups[-1] and size + n > max_chars:
            groups.append([])
            size = 0
        groups[-1].append(c)
        size += n
    # the uncited sentences go once, with the first part
    parts = [support_packet(vault, text, label, heading, g, with_uncited=i == 0) for i, g in enumerate(groups)]
    if len(parts) > 1:
        parts = [(p.replace("\n- Alcance:", f"\n- Parte: {i} de {len(parts)}\n- Alcance:", 1), m)
                 for i, (p, m) in enumerate(parts, 1)]
    return parts


def support_packet(vault: str, text: str, label: str, heading: str | None,
                   claims: list[str] | None = None, with_uncited: bool = True) -> tuple[str, dict]:
    """The fresh-verifier packet for a synthesis: every cited sentence or table row
    as an Afirmación, each followed by the verbatim text its locators point at."""
    if claims is None:
        claims = section_claims(text, heading)
    manifest: dict = {"tool": VERIFIER_PACKET_ID, "built_by": TOOL,
                      "scope": f"section:{heading}" if heading else "note",
                      "sources": [{"path": label, "role": "note"}], "citations": [], "analysis_outputs": []}
    out = [f"# Paquete de verificación ({VERIFIER_PACKET_ID})", "",
           f"- Nota verificada: {label}", f"- Alcance: {manifest['scope']}",
           "- Contenido: solo las frases citadas de una síntesis del estado del arte, cada una con el texto "
           "fuente que su localizador señala. Busca frases que su fuente no respalda (atribución, dirección, "
           "condiciones, cifras, fuerza de la afirmación).", "",
           f"## Nota: {label}", "", "### Justificación (evidencia citada)", ""]
    text_claims = "\n".join("- " + c.lstrip("-* ").strip() for c in claims)
    out += render_justification(vault, text_claims, manifest) if claims else ["(ninguna frase citada)", ""]
    ctx_lines = []
    for i, c in enumerate(claims, 1):
        toks = numbers(c)
        if not toks:
            continue
        units: list[str] = []
        for m in CITE.finditer(c):
            units += [u.removeprefix("[## Resumen]\n")
                      for u in resolve_citation(vault, m.group(1), (m.group(2) or "").strip(" ,;"))["units"]]
        for tok, sent in number_contexts(toks, units).items():
            ctx_lines.append(f"- Afirmación {i}: «{tok}» → «{sent}»" if sent
                             else f"- Afirmación {i}: «{tok}» → no aparece en el texto citado")
    if ctx_lines:
        out += ["## Dónde aparece cada cifra en el texto citado", "",
                "(Mecánico: la primera frase del texto citado que contiene cada cifra de la afirmación. "
                "Comprueba que mide lo mismo que la afirmación dice: misma magnitud, misma condición, misma fila.)",
                "", *ctx_lines, ""]
    loose = section_uncited(text, heading) if with_uncited else []
    if loose:
        out += ["## Frases sin cita con cifras o comparaciones", "",
                "(Mecánico: frases de esta parte del mapa que dan una cifra o comparan papers sin citar ninguno; "
                "no tienen texto fuente. Señala como error la que atribuye un resultado a un paper o compara "
                "papers: debe citar cada paper que usa.)", "", *(f"- «{x}»" for x in loose), ""]
    manifest["assertions"] = len(claims)
    return "\n".join(out).rstrip() + "\n", manifest


def mark(text: str, problems: list[dict]) -> str:
    for p in sorted(problems, key=lambda p: -p["offset"]):
        text = text[:p["offset"]] + f" «⚠ {p['reason']}»" + text[p["offset"]:]
    return text


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vault", required=True)
    ap.add_argument("--project-dir", required=True)
    ap.add_argument("--file", default="Estado-del-arte.md")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--packet", help="write the fresh-verifier support packet here instead of checking")
    ap.add_argument("--section", help="with --packet: only this ## section (heading text, without '## ')")
    ap.add_argument("--max-chars", type=int, default=MAX_PACKET_CHARS,
                    help="with --packet: split into parts of at most this size (FILE, FILE-2, …), one verifier each")
    a = ap.parse_args(argv)
    pdir = Path(a.vault, a.project_dir)
    f = pdir / a.file if not Path(a.file).is_absolute() else Path(a.file)
    if not f.is_file():
        print(json.dumps({"tool": TOOL, "error": f"not found: {f}"}, ensure_ascii=False))
        return 2
    hub = pdir / "_hub.md"
    project = fm_scalar(split_frontmatter(read_text(str(hub)))[0], "id") if hub.is_file() else None
    if a.packet:
        try:
            parts = support_packets(a.vault, f.read_text(encoding="utf-8"),
                                    f"{Path(a.project_dir).name}/{f.name}", a.section, a.max_chars)
        except (OSError, ValueError) as e:
            print(json.dumps({"tool": TOOL, "error": str(e)}, ensure_ascii=False))
            return 2
        first = Path(a.packet)
        paths = [first] + [first.with_name(f"{first.stem}-{i}{first.suffix}") for i in range(2, len(parts) + 1)]
        stored = []
        for path, (packet, _) in zip(paths, parts):
            path.write_text(packet, encoding="utf-8", newline="\n")
            # the verifier reads the stored copy itself (its Read is held to the store by the hook)
            stored.append(isolation.store(packet.encode("utf-8")))
        print(json.dumps({"tool": TOOL, "packet": a.packet, "packets": [str(p) for p in paths], "stored": stored,
                          "assertions": sum(m["assertions"] for _, m in parts),
                          "citations": sum(len(m["citations"]) for _, m in parts)}, ensure_ascii=False, indent=2))
        return 0
    try:
        text = f.read_text(encoding="utf-8")
        report = check(a.vault, project, text)
    except (OSError, ValueError) as e:
        print(json.dumps({"tool": TOOL, "error": str(e)}, ensure_ascii=False))
        return 1
    if a.write and report["problems"]:
        f.write_text(mark(text, report["problems"]), encoding="utf-8", newline="\n")
        report["written"] = True
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 3 if report["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
