"""Pieces of the LaTeX export that are not pandoc's: figures, tables, page labels.

Used by manuscript.py (export, pages) and submission.py (sync-check, check-venue).
Everything here either reads a file the vault controls (the figure registry, a
generated table) and checks it, or rearranges LaTeX that already passed the
export's allow-list. No text a section author typed reaches LaTeX through here
unescaped. Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

FIG_ID = re.compile(r"^F-\d{3,4}$")
FIG_EXT = (".pdf", ".png")
TABLE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}\.tex$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

# markers an author writes on their own line (figure, wide table, generated table) or inline (ref)
MARK_FIG = re.compile(r"(?m)^\[\[figura (F-\d{3,4})\]\][ \t]*$")
MARK_REF = re.compile(r"\[\[ref (F-\d{3,4})\]\]")
MARK_WIDE = re.compile(r"(?m)^\[\[tabla ancha\]\][ \t]*$")
MARK_TABFILE = re.compile(r"(?m)^\[\[tabla-latex ([^\]\s]+)\]\][ \t]*$")
MARK_ANY = re.compile(r"\[\[\s*(?:figura|ref|tabla)\b[^\]\n]*\]?\]?")


class PartError(Exception):
    """A figure or table the export must refuse (exit 3)."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------
# Figures: only the ones registered in Projects/<slug>/Figuras/registro.json
# --------------------------------------------------------------------------

def figure_registry(project_dir: Path) -> dict[str, dict]:
    """id -> entry of Figuras/registro.json (missing file = no figures)."""
    reg = project_dir / "Figuras" / "registro.json"
    if not reg.is_file():
        return {}
    try:
        data = json.loads(reg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise PartError(f"Figuras/registro.json no es JSON válido ({e})") from None
    if not isinstance(data, list):
        raise PartError("Figuras/registro.json debe ser una lista de figuras")
    out: dict[str, dict] = {}
    for e in data:
        if isinstance(e, dict) and isinstance(e.get("id"), str) and FIG_ID.match(e["id"]):
            out[e["id"]] = e
    return out


def figure_file(vault: Path, project_dir: Path, entry: dict) -> Path:
    """The registered file, which must sit inside the project's Figuras/ folder and be a PDF or PNG."""
    rel = str(entry.get("path") or "").replace("\\", "/")
    fid = entry.get("id")
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        raise PartError(f"la figura {fid} tiene una ruta no válida en el registro: {rel!r}")
    cand = project_dir / rel if rel.startswith("Figuras/") else vault / rel
    folder = (project_dir / "Figuras").resolve()
    real = cand.resolve()
    if folder not in real.parents:
        raise PartError(f"la figura {fid} no está dentro de Figuras/ del proyecto: {rel}")
    if real.suffix.lower() not in FIG_EXT:
        raise PartError(f"la figura {fid} debe ser .pdf o .png: {rel}")
    if not real.is_file():
        raise PartError(f"falta el fichero de la figura {fid}: {rel}")
    return real


def checked_figure(vault: Path, project_dir: Path, registry: dict, fid: str) -> tuple[Path, dict]:
    """(file, entry) for a registered figure whose bytes still match the registry."""
    entry = registry.get(fid)
    if entry is None:
        raise PartError(f"la figura {fid} no está en Figuras/registro.json: regístrala con figures.py")
    path = figure_file(vault, project_dir, entry)
    want = str(entry.get("sha256") or "").lower()
    if not _HEX64.match(want):
        raise PartError(f"la figura {fid} no tiene un sha256 válido en el registro")
    if sha256_file(path) != want:
        raise PartError(f"la figura {fid} ha cambiado desde que se registró (sha256 distinto): "
                        "vuelve a generarla y registrarla con figures.py")
    return path, entry


def figure_env(fid: str, target: str, caption_tex: str, wide: bool, describe: bool = False) -> str:
    """`target` is the copied file's name (figuras/F-001.pdf); `caption_tex` is already escaped.
    `describe` adds acmart's \\Description (its accessibility text) with the caption."""
    env = "figure*" if wide else "figure"
    desc = f"\\Description{{{caption_tex}}}\n" if describe else ""
    return (f"\\begin{{{env}}}[t]\n\\centering\n\\includegraphics[width=\\linewidth]{{{target}}}\n"
            f"\\caption{{{caption_tex}}}\n{desc}\\label{{fig:{fid}}}\n\\end{{{env}}}")


# --------------------------------------------------------------------------
# Tables: pandoc's longtable -> booktabs tabular inside table / table*
# --------------------------------------------------------------------------

def _braced(text: str, i: int) -> tuple[str, int]:
    """Content of the brace group opening at text[i] == '{', and the index after it."""
    if i >= len(text) or text[i] != "{":
        raise ValueError("no brace group")
    depth, j = 0, i
    while j < len(text):
        c = text[j]
        if c == "\\":                       # a control symbol (`\{`, `\}`, `\\`) is no brace
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[i + 1:j], j + 1
        j += 1
    raise ValueError("unbalanced braces")


def column_count(spec: str) -> int:
    """Columns in a tabular spec: l c r and p/m/b{…}/X, skipping @{…} !{…} >{…} <{…}."""
    n, i = 0, 0
    while i < len(spec):
        c = spec[i]
        if c in "@!><":
            i += 1
            if i < len(spec) and spec[i] == "{":
                _, i = _braced(spec, i)
            continue
        if c in "pmb" and i + 1 < len(spec) and spec[i + 1] == "{":
            n += 1
            _, i = _braced(spec, i + 1)
            continue
        if c in "lcrX":
            n += 1
        elif c == "{":
            _, i = _braced(spec, i)
            continue
        i += 1
    return n


_LT_OPEN = re.compile(r"(\{\\def\\LTcaptype\{none\} % do not increment counter\n)?\\begin\{longtable\}\[\]\{")


def convert_longtables(tex: str, wide_cols: int, wide_marks: set[int] | None = None) -> tuple[str, int]:
    """Replace every pandoc longtable by a booktabs tabular in `table` (or `table*` when it
    has more than `wide_cols` columns, or its index is in `wide_marks`). Returns (tex, count)."""
    out, pos, idx = [], 0, 0
    wide_marks = wide_marks or set()
    while True:
        m = _LT_OPEN.search(tex, pos)
        if not m:
            break
        spec, after = _braced(tex, m.end() - 1)
        end = tex.find("\\end{longtable}", after)
        if end < 0:
            raise PartError("tabla sin cerrar en la salida de pandoc")
        inner = tex[after:end]
        stop = end + len("\\end{longtable}")
        if m.group(1):                      # the `{\def\LTcaptype…` group pandoc opens around it
            close = re.match(r"\n?\}", tex[stop:])
            if close:
                stop += close.end()
        inner = inner.replace("\\noalign{}", "")
        caption = ""
        cm = re.match(r"\s*\\caption\{", inner)
        if cm:
            caption, k = _braced(inner, cm.end() - 1)
            inner = re.sub(r"^\\tabularnewline", "", inner[k:])
        if "\\endfirsthead" in inner:
            head, rest = inner.split("\\endfirsthead", 1)
            rest = rest.split("\\endhead", 1)[1] if "\\endhead" in rest else rest
        elif "\\endhead" in inner:
            head, rest = inner.split("\\endhead", 1)
        else:
            head, rest = "", inner
        foot = ""
        if "\\endlastfoot" in rest:
            foot, rows = rest.split("\\endlastfoot", 1)
            foot = foot.split("\\endfoot", 1)[-1]
        else:
            rows = rest
        wide = column_count(spec) > wide_cols > 0 or idx in wide_marks
        env = "table*" if wide else "table"
        body = "\n".join(p.strip("\n") for p in (head, rows, foot) if p.strip())
        spec_out = spec.replace("\\columnwidth", "\\linewidth")
        block = (f"\\begin{{{env}}}[t]\n\\centering\n" + (f"\\caption{{{caption}}}\n" if caption else "")
                 + f"\\begin{{tabular}}{{{spec_out}}}\n{body}\n\\end{{tabular}}\n\\end{{{env}}}")
        out += [tex[pos:m.start()], block]
        pos = stop
        idx += 1
    out.append(tex[pos:])
    return "".join(out), idx


# A table generated by `estimate.py tabla-latex`: only these commands and environments.
_TABLE_CS = frozenset("""
begin end toprule midrule bottomrule cmidrule addlinespace centering caption label small
footnotesize scriptsize multicolumn textbf emph textit texttt textsc textsuperscript
textsubscript tabularnewline hline ldots textasciitilde textasciicircum textbackslash
""".split())
_TABLE_ENVS = frozenset(("table", "table*", "tabular"))


def table_file_not_allowed(tex: str, math_cs: frozenset, symbols: frozenset) -> str | None:
    """The first thing in a generated table the strict allow-list does not admit, or None."""
    if "^^" in tex:
        return "^^"
    depth = 0
    for i, c in enumerate(tex):
        if c in "{}" and (i == 0 or tex[i - 1] != "\\"):
            depth += 1 if c == "{" else -1
            if depth < 0:
                return "}"
    if depth:
        return "{"
    if "%" in tex.replace("\\%", ""):
        return "%"
    in_math = False
    for m in re.finditer(r"\\(?:[A-Za-z@]+|.)|\$", tex, re.S):
        tok = m.group(0)
        if tok == "$":
            in_math = not in_math
            continue
        name = tok[1:]
        if name in ("begin", "end"):
            env = re.match(r"\{([^{}]*)\}", tex[m.end():])
            if not env or env.group(1) not in _TABLE_ENVS:
                return tok + (env.group(0) if env else "")
            continue
        if name in _TABLE_CS or name in symbols or (in_math and name in math_cs):
            continue
        return tok
    return "$" if in_math else None


def table_file(project_dir: Path, name: str, wide_cols: int,
               math_cs: frozenset, symbols: frozenset) -> str:
    """A generated table from Manuscritos/tablas/<name>, checked, ready to inline."""
    if not TABLE_FILE.match(name):
        raise PartError(f"[[tabla-latex {name}]]: el nombre debe ser un .tex de Manuscritos/tablas/")
    path = project_dir / "Manuscritos" / "tablas" / name
    if not path.is_file():
        raise PartError(f"falta la tabla generada Manuscritos/tablas/{name}: genérala con estimate.py tabla-latex")
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
    bad = table_file_not_allowed(text, math_cs, symbols)
    if bad:
        raise PartError(f"la tabla Manuscritos/tablas/{name} usa `{bad}`, que una tabla generada no admite")
    if "\\begin{table" in text:
        return text
    spec = re.search(r"\\begin\{tabular\}\{", text)
    wide = False
    if spec:
        try:
            wide = column_count(_braced(text, spec.end() - 1)[0]) > wide_cols > 0
        except ValueError:
            pass
    env = "table*" if wide else "table"
    return f"\\begin{{{env}}}[t]\n\\centering\n{text}\n\\end{{{env}}}"


# --------------------------------------------------------------------------
# Labels in main.aux: body pages and pages per section
# --------------------------------------------------------------------------

_NEWLABEL = re.compile(r"\\newlabel\{(kairo:[a-z0-9:-]+)\}\{\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}\{(\d+)\}")


def aux_labels(aux: str) -> dict[str, int]:
    return {m.group(1): int(m.group(2)) for m in _NEWLABEL.finditer(aux)}


def body_pages(labels: dict[str, int]) -> int | None:
    """The page the body ends on: `kairo:body-end`, or `kairo:refs-start` in older exports."""
    return labels.get("kairo:body-end", labels.get("kairo:refs-start"))


def section_pages(labels: dict[str, int], order: list[dict]) -> list[dict]:
    """Pages per section from the labels, in document order. `order` lists the emitted
    sections ({id, parent, budget_pages}) followed by the boundary labels' names
    ({"boundary": "kairo:body-end"} …). A section ends where the next label that is not
    one of its own subsections begins; pages = end page − start page (whole pages, ±1)."""
    out = []
    for i, s in enumerate(order):
        if "boundary" in s:
            continue
        start = labels.get(f"kairo:sec:{s['id']}")
        end = None
        for nxt in order[i + 1:]:
            if "boundary" in nxt:
                end = labels.get(nxt["boundary"])
            elif nxt.get("parent") == s["id"]:
                continue
            else:
                end = labels.get(f"kairo:sec:{nxt['id']}")
            if end is not None:
                break
        pages = (end - start) if start is not None and end is not None else None
        budget = s.get("budget_pages")
        out.append({"section": s["id"], "start_page": start, "end_page": end, "pages": pages,
                    "budget": budget, "over": bool(budget is not None and pages is not None and pages > budget)})
    return out


# --------------------------------------------------------------------------
# Numbers, citations and figures in an edited main.tex (sync-check)
# --------------------------------------------------------------------------

_NUM = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)*(?![\w])")
_MD_IDS = re.compile(r"\b(?:[A-Z]{1,2})-\d{3,4}\b|\[\[[^\]\n]*\]\]|<!--.*?-->", re.S)
_BRACED = r"\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}"
_TEX_DROP = re.compile(
    r"\\begin\{(figure\*?)\}.*?\\end\{\1\}"
    r"|\\begin\{(?:tabular|longtable|minipage)\}(?:\[[^\]]*\])*(?:\{[^{}]*\})?" + _BRACED +
    r"|\\(?:label|ref|eqref|cite[a-zA-Z]*|nocite|includegraphics|begin|end|real|url|href)\*?(?:\[[^\]]*\])*\{[^{}]*\}"
    r"|\\cmidrule(?:\([^)]*\))?\{[^{}]*\}|\\multicolumn\{\d+\}"
    r"|\\[A-Za-z]+\*?\[[^\]]*\]"
    r"|\\(?:sub)*section\*?\{[^{}]*\}", re.S)


def numbers_md(text: str) -> list[str]:
    return _NUM.findall(_MD_IDS.sub(" ", text))


def numbers_tex(text: str) -> list[str]:
    text = re.sub(r"(?m)%.*$", "", text.replace("\\%", " "))
    return _NUM.findall(_TEX_DROP.sub(" ", text))


def _parses(tok: str) -> list[tuple[float, int]]:
    """(value, decimals) readings of a number token: `.` or `,` as the decimal mark, or
    either as a thousands separator."""
    out = []
    for dec in (".", ","):
        other = "," if dec == "." else "."
        t = tok.replace(other, "") if re.fullmatch(r"\d{1,3}(?:\%s\d{3})+(?:\%s\d+)?" % (other, dec), tok) else tok
        if t.count(dec) <= 1 and other not in t:
            try:
                out.append((float(t.replace(dec, ".")), len(t.split(dec)[1]) if dec in t else 0))
            except ValueError:
                pass
    return out


def estimate_numbers(obj) -> list[float]:
    """Every number in an estimate-json block, recursively (strings and booleans skipped)."""
    out: list[float] = []
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        return [float(obj)]
    if isinstance(obj, dict):
        for v in obj.values():
            out += estimate_numbers(v)
    elif isinstance(obj, list):
        for v in obj:
            out += estimate_numbers(v)
    return out


def from_estimate(tok: str, values: list[float]) -> bool:
    """Whether a number token in the text writes one of the estimates, as printed (rounded
    to the token's decimals) or as a percentage."""
    for val, dec in _parses(tok):
        tol = 0.5 * 10 ** (-dec) + 1e-9
        for v in values:
            if abs(v - val) <= tol or abs(v * 100 - val) <= tol:
                return True
    return False


def compare_numbers(md_tokens: list[str], tex_tokens: list[str], values: list[float]) -> dict:
    """removed: estimate numbers of the vault text missing from main.tex; added: numbers in
    main.tex the vault text does not have; changed: a removed number paired with the
    closest added one written with the same decimals and within ±50 % of it."""
    have = Counter(tex_tokens)
    removed = []
    for t in md_tokens:
        if not from_estimate(t, values):
            continue
        if have[t] > 0:
            have[t] -= 1
        else:
            removed.append(t)
    md = Counter(md_tokens)
    added = []
    for t in tex_tokens:
        if md[t] > 0:
            md[t] -= 1
        else:
            added.append(t)
    changed, left = [], []
    for r in removed:
        rv, rd = _parses(r)[0] if _parses(r) else (None, None)
        best = None
        for i, a in enumerate(added):
            av, ad = _parses(a)[0] if _parses(a) else (None, None)
            if rv is None or av is None or ad != rd or abs(av - rv) > 0.5 * max(abs(rv), 1e-9):
                continue
            if best is None or abs(av - rv) < abs(_parses(added[best])[0][0] - rv):
                best = i
        if best is None:
            left.append(r)
        else:
            changed.append({"vault": r, "tex": added.pop(best)})
    return {"changed": changed, "removed": left, "added": added}


_CITE_CMD = re.compile(r"\\(?:no)?cite[a-zA-Z]*\*?(?:\[[^\]]*\])*\{([^{}]*)\}")
_BIB_ENTRY = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)(?=\n@|\Z)", re.S)
_INCLUDE = re.compile(r"\\includegraphics\*?(?:\[[^\]]*\])*\{([^{}]*)\}")


def cite_keys(tex: str) -> list[str]:
    keys: list[str] = []
    for m in _CITE_CMD.finditer(re.sub(r"(?m)(?<!\\)%.*$", "", tex)):
        for k in m.group(1).split(","):
            if k.strip() and k.strip() not in keys:
                keys.append(k.strip())
    return keys


def bib_entries(bib: str) -> dict[str, str | None]:
    """key -> P-id from its `keywords = {kairo:P-XXXX}` (None when it has none)."""
    out: dict[str, str | None] = {}
    for m in _BIB_ENTRY.finditer(bib):
        pid = re.search(r"kairo:(P-\d{4})", m.group(3))
        out[m.group(2)] = pid.group(1) if pid else None
    return out


def included_graphics(tex: str) -> list[str]:
    return [m.group(1).strip() for m in _INCLUDE.finditer(re.sub(r"(?m)(?<!\\)%.*$", "", tex))]
