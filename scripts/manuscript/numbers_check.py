#!/usr/bin/env python3
"""Every number of a manuscript section bound to study questions must come from their estimates.

    numbers_check.py --vault V --project-dir Projects/<slug> --thread T [--json]

For every section of `Manuscritos/outline-<T>.md` whose `depends_on` names one or more
study questions (Q-XXXX; `manuscript.py bind`), every number token in the section's text
(`manuscript-<T>.md`, between its `<!-- kairo:section <id> -->` marker and the next `## `)
is looked for among the numbers of those questions' ```estimate-json blocks.

Tokens: integers, decimals with `.` or `,`, thousands with `,` `.` or a thin / no-break
space (`12,345` `12.345` `12 345`), a sign (`-` or `−`), percentages (`%`, `\\%`, `‰`,
`por ciento`, `percent`, `puntos porcentuales`, `percentage points`, `pp`), and ranges
`12–34 %` (the percent sign applies to both ends). An interval `[0.12, 0.34]` is two
tokens; in `[9,8; 15 %]` the upper end's percent sign applies to the lower end too. An ambiguous token (`1,234`: en thousands or es decimal) is traced when any
reading is.

Traced (status `ok`, with its `source`):
- `estimate-json`: some number of a bound question's block (any numeric leaf: value, lo,
  hi, n, counts, events, histogram counts, S(t), group values, adjusted p-values, …, and
  the numbers inside its strings and keys, e.g. a histogram bucket `3-5`; signs ignored)
  equals the token once rounded to the token's decimals. A percentage also matches a
  number ×100 (0.1234 → `12.3 %`, `12 %`). A bare integer under 10 written without
  decimals only matches the same integer (so `3` is not "traced" to 2.7);
- `plan`: the same, against every number in the ```json blocks of the bound questions'
  plans (sample sizes, ci → `95 %`, thresholds);
- `año`: a four-digit integer from 1900 to 2100;
- `referencia`: a section, figure, table, appendix, equation, chapter, page, line, step or
  note number (`Figura 2`, `Tabla 1 y 3`, `§4.2`, `Sección 3.1`);
- `permitido`: the token is in `Manuscritos/numeros-permitidos.txt`, one per line, as
  `<token><TAB><reason>` (or `<token> — <reason>`, `<token> | <reason>`, or the first
  word as the token). An entry without a reason is ignored and reported.
Never looked at: ids (`P-0001`, `Q-0003`, `F-001`, `est-1`, any word-dash-number), inline
and fenced code, URLs, link targets, HTML comments, footnote markers, list and heading
numbering, LaTeX `\\ref{}` / `\\cite{}` / `\\label{}` arguments, numbers glued to a letter
(`x86`, `H2`) and dotted versions (`1.2.3`).

Anything else is `untraced`, with the `nearest` number it could have come from. An
untraced number in a `resultado` section is an error (exit 4); in any other section a
warning (exit 0). Fix it by re-deriving the number from estimate-json, never by adding it to
the allow-list without a reason. Sections with nothing bound, or still holding their
placeholder, are not checked.

stdout (--json): {"thread", "sections": [{"id", "kind", "questions", "checked"}],
"items": [{"section", "kind", "token", "status", "source"?, "nearest"?}], "errors",
"warnings", "problems": [...]}; otherwise a short report in Spanish.
Exit 0 ok (warnings only) · 3 refused · 4 an untraced number in a resultado section.
Standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEDGER = HERE.parent / "ledger"
for _p in (HERE, LEDGER):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import figures  # noqa: E402
import manuscript as ms  # noqa: E402
from notes import read_note  # noqa: E402

ALLOWLIST = "numeros-permitidos.txt"
_Q = re.compile(r"^Q-\d{4}$")

_NUM = (r"(?:[1-9]\d{0,2}(?:(?:,\d{3})+|(?:\.\d{3})+|(?:[\u00a0\u202f\u2009]\d{3})+)(?:[.,]\d+)?"
        r"|\d+(?:[.,]\d+)?)")
_PCT = (r"(?:%|\\%|‰|por\s+ciento|percent\b|puntos\s+porcentuales|percentage\s+points|"
        r"pp\b|p\.\s?p\.)")
TOKEN = re.compile(
    rf"(?<![\w.])(?P<sign>[-−]?)(?P<a>{_NUM})(?![\w])(?![.,]\d)"
    rf"(?:\s*(?:[–—]|-(?=\d))\s*(?P<b>{_NUM})(?![\w])(?![.,]\d))?"
    rf"(?P<pct>[ \u00a0\u202f]?{_PCT})?")
_PCT_PARTNER = re.compile(rf"\s*[,;]\s*[-−]?{_NUM}[   ]?{_PCT}")
_MASKS = [
    re.compile(r"(?ms)^[ \t]*(?:```|~~~).*?^[ \t]*(?:```|~~~)[^\n]*$"),   # fenced code
    re.compile(r"<!--.*?-->", re.S),                                      # HTML comments
    re.compile(r"`[^`\n]*`"),                                            # inline code
    re.compile(r"\]\([^)]*\)"),                                          # link targets
    re.compile(r"https?://\S+"),                                         # URLs
    re.compile(r"\[\^[^\]]*\]"),                                         # footnote markers
    re.compile(r"\\(?:ref|eqref|cite|label|autoref|cref)\{[^}]*\}"),     # LaTeX refs
    re.compile(r"\b[A-Za-z][A-Za-z0-9_]*-\d[\w-]*"),                    # ids: P-0001, est-1
    re.compile(r"(?m)^[ \t]*\d+[.)][ \t]"),                              # list numbering
    re.compile(r"(?m)^#+[ \t]+[\d.]+[ \t]"),                             # heading numbering
]
_REF_BEFORE = re.compile(
    r"(?i)(?:\b(?:secci[oó]n(?:es)?|sections?|sec\.|figuras?|figures?|figs?\.|tablas?|tables?|"
    r"cuadros?|ap[eé]ndices?|appendix|appendices|anexos?|ecuaci[oó]n(?:es)?|equations?|eqs?\.|"
    r"cap[ií]tulos?|chapters?|p[aá]ginas?|pages?|pp?\.|l[ií]neas?|lines?|pasos?|steps?|notas?|notes?)"
    r"|§)\s*(?:\(?[\d.]+\)?\s*(?:,|y|e|and|&|–|-|a|to)\s*)*\(?$")
_STR_NUM = re.compile(r"\d+(?:\.\d+)?")


class Refused(Exception):
    pass


# ---- numbers -------------------------------------------------------------------

def _mask(text: str) -> str:
    for rx in _MASKS:
        text = rx.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    return text


def readings(core: str) -> list[tuple[float, int]]:
    """Every (value, decimals shown) a number token can mean, en or es."""
    s = re.sub(r"[\u00a0\u202f\u2009]", "", core)
    if s != core:                       # space thousands: the decimal mark is . or ,
        m = re.fullmatch(r"(\d+)(?:[.,](\d+))?", s)
        whole, frac = m.group(1), m.group(2) or ""
        return [(float(f"{whole}.{frac}" if frac else whole), len(frac))]
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        th = "." if dec == "," else ","
        whole, frac = s.replace(th, "").split(dec)
        return [(float(f"{whole}.{frac}"), len(frac))]
    out = []
    for sep in (",", "."):
        if sep in s:
            parts = s.split(sep)
            if re.fullmatch(rf"[1-9]\d{{0,2}}(?:\{sep}\d{{3}})+", s):
                out.append((float("".join(parts)), 0))
            if len(parts) == 2:
                out.append((float(f"{parts[0]}.{parts[1]}"), len(parts[1])))
            return out
    return [(float(s), 0)]


def collect(obj, out: set[float]) -> set[float]:
    """Every number of a JSON value: numeric leaves and the numbers inside strings and keys."""
    if isinstance(obj, bool) or obj is None:
        return out
    if isinstance(obj, (int, float)):
        out.add(abs(float(obj)))
    elif isinstance(obj, str):
        out.update(float(x) for x in _STR_NUM.findall(obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            collect(k, out)
            collect(v, out)
    elif isinstance(obj, list):
        for v in obj:
            collect(v, out)
    return out


def _matches(x: float, d: int, pct: bool, pool: set[float]) -> tuple[bool, float | None]:
    cands = {v * 100 for v in pool} | pool if pct else pool
    tol = 0.5 * 10 ** (-d) + 1e-9 * max(1.0, x)
    nearest = min(cands, key=lambda v: abs(v - x), default=None)
    for v in cands:
        if abs(v - x) <= tol and (d > 0 or pct or float(v).is_integer() or x >= 10):
            return True, v
    return False, nearest


# ---- the vault ------------------------------------------------------------------

def _question_numbers(vault: Path, proj: Path, qid: str, problems: list) -> tuple[set, set]:
    """(numbers of the estimate-json block, numbers of the plan's json blocks)."""
    est, plan = set(), set()
    try:
        node = figures.find_question(vault, qid, proj)
    except figures.Refused as e:
        problems.append({"question": qid, "problem": str(e)})
        return est, plan
    text = figures.estimate_block(node)
    if text is None:
        problems.append({"question": qid, "problem": "sin bloque estimate-json en ## Resultado"})
    else:
        try:
            collect(json.loads(text), est)
        except ValueError:
            problems.append({"question": qid, "problem": "el bloque estimate-json no es JSON válido"})
    parsed = read_note(node)
    pid = str((parsed[0] if parsed else {}).get("linked_plan") or "").strip()
    if pid:
        from study_gates import find_plan  # noqa: PLC0415
        path, _why = find_plan(vault, pid, proj)
        body = (read_note(path) or ({}, ""))[1] if path else ""
        for m in re.finditer(r"```json[ \t]*\n(.*?)\n[ \t]*```", body, re.S):
            try:
                collect(json.loads(m.group(1)), plan)
            except ValueError:
                continue
    return est, plan


def _norm_tok(s: str) -> str:
    return re.sub(r"\s+", "", s.replace("−", "-").replace("\\%", "%").replace("\u00a0", " ")
                  .replace("\u202f", " ").replace("\u2009", " "))


def load_allowlist(project_dir: Path, problems: list) -> dict[str, str]:
    f = project_dir / "Manuscritos" / ALLOWLIST
    out: dict[str, str] = {}
    if not f.is_file():
        return out
    for i, line in enumerate(f.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for sep in ("\t", " — ", " | ", " -- "):
            if sep in line:
                tok, reason = line.split(sep, 1)
                break
        else:
            tok, _, reason = line.partition(" ")
        if not reason.strip():
            problems.append({"allowlist_line": i, "problem": "sin motivo: la entrada no cuenta"})
            continue
        out[_norm_tok(tok)] = reason.strip()
    return out


def check_text(text: str, est: set, plan: set, allow: dict[str, str]) -> list[dict]:
    masked = _mask(text)
    items = []
    for m in TOKEN.finditer(masked):
        # `[9,8; 15 %]`: the percent sign of an interval's upper end applies to its lower end
        pct = bool(m.group("pct")) or bool(_PCT_PARTNER.match(masked, m.end()))
        is_ref = bool(_REF_BEFORE.search(masked[max(0, m.start() - 80):m.start()]))
        for part in ("a", "b"):
            core = m.group(part)
            if core is None:
                continue
            sign = m.group("sign") if part == "a" else ""
            surface = sign + core + (m.group("pct") or "")
            item = {"token": surface.strip()}
            vals = readings(core)
            key = next((k for k in (_norm_tok(surface), _norm_tok(sign + core)) if k in allow), None)
            if not pct and not sign and re.fullmatch(r"\d{4}", core) and 1900 <= int(core) <= 2100:
                item.update(status="ok", source="año")
            elif is_ref:
                item.update(status="ok", source="referencia")
            elif key is not None:
                item.update(status="ok", source="permitido", reason=allow[key])
            else:
                nearest, found = None, None
                for pool, name in ((est, "estimate-json"), (plan, "plan")):
                    for x, d in vals:
                        ok, v = _matches(abs(x), d, pct, pool)
                        if ok:
                            found = name
                            break
                        if v is not None and (nearest is None or abs(v - abs(x)) < abs(nearest - abs(x))):
                            nearest = v
                    if found:
                        break
                if found:
                    item.update(status="ok", source=found)
                else:
                    item.update(status="untraced", nearest=nearest)
            items.append(item)
    return items


def run(vault: Path, project_dir: Path, thread: str) -> dict:
    vault, project_dir = Path(vault), Path(project_dir)
    if not project_dir.is_absolute():
        project_dir = vault / project_dir
    try:
        outline, manuscript = ms.paths(project_dir, thread)
        _meta, sections, _ = ms.parse_outline(outline)
    except ms.Refused as e:
        raise Refused(str(e)) from None
    except OSError:
        raise Refused(f"no hay outline-{thread}.md en Manuscritos/") from None
    if not manuscript.is_file():
        raise Refused(f"no hay manuscript-{thread}.md en Manuscritos/")
    bodies = ms.section_bodies(manuscript)
    problems: list = []
    allow = load_allowlist(project_dir, problems)
    cache: dict[str, tuple[set, set]] = {}
    out_sections, items = [], []
    for s in sections:
        qs = [d for d in s["depends_on"] if _Q.match(d)]
        body = bodies.get(s["id"], "")
        checked = bool(qs) and not ms.is_placeholder(body)
        out_sections.append({"id": s["id"], "kind": s["kind"], "questions": qs, "checked": checked})
        if not checked:
            continue
        est, plan = set(), set()
        for q in qs:
            if q not in cache:
                cache[q] = _question_numbers(vault, project_dir, q, problems)
            est |= cache[q][0]
            plan |= cache[q][1]
        for it in check_text(body, est, plan, allow):
            items.append({"section": s["id"], "kind": s["kind"], **it})
    errors = sum(1 for i in items if i["status"] == "untraced" and i["kind"] == "resultado")
    warnings = sum(1 for i in items if i["status"] == "untraced" and i["kind"] != "resultado")
    return {"thread": thread, "sections": out_sections, "items": items, "errors": errors,
            "warnings": warnings, "problems": problems}


def _fmt(v) -> str:
    return "—" if v is None else (f"{v:.6g}")


def render(res: dict) -> str:
    lines = [f"Comprobación de cifras — hilo {res['thread']}"]
    checked = [s["id"] for s in res["sections"] if s["checked"]]
    lines.append(f"Secciones comprobadas: {', '.join(checked) if checked else 'ninguna'}")
    for i in res["items"]:
        if i["status"] == "untraced":
            level = "ERROR" if i["kind"] == "resultado" else "aviso"
            lines.append(f"{level} [{i['section']}] «{i['token']}» no sale de estimate-json "
                         f"(más cercano: {_fmt(i.get('nearest'))})")
    for p in res["problems"]:
        lines.append("problema: " + json.dumps(p, ensure_ascii=False))
    lines.append(f"{res['errors']} error(es), {res['warnings']} aviso(s); "
                 f"{sum(1 for i in res['items'] if i['status'] == 'ok')} cifra(s) rastreada(s)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", required=True)
    ap.add_argument("--project-dir", required=True)
    ap.add_argument("--thread", required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        res = run(Path(a.vault), Path(a.project_dir), a.thread)
    except Refused as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False) if a.json else f"refused: {e}")
        return 3
    print(json.dumps(res, ensure_ascii=False, indent=2) if a.json else render(res))
    return 4 if res["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
