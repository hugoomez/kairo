#!/usr/bin/env python3
"""Reading cards: each paper read once, its quotes checked, reused everywhere.

    ficha.py status --vault <vault> (--project PROJ-XXX | --ids P-XXXX …)
    ficha.py write  --vault <vault> --id P-XXXX --reply <paper-carder answer> --model <model id>

A state-of-the-art map used to re-read every paper's full text in every facet
summarizer, in every project. A card (`Papers/_fichas/<P-id>.md`) is written
once per paper by the `paper-carder` subagent: 8–25 items (problem, method,
setup, result, limitation, definition, benchmark), each a one-sentence claim
with a **verbatim quote** and the locator it sits under.

`write` takes the agent's answer and keeps an item only when its quote is
found, character for character (whitespace aside; an ellipsis only inside one
sentence), under its locator — the same check `ask-corpus` answers pass
(`check_quotes.check_quote`, the fresh-verifier's resolver). A number in a
claim must be in its quote (a plot reading `≈… (leído de la Figura N, no
literal)` excepted). Dropped items are listed in the card, never silently.
The card records the sha256 of the note's `## Texto completo`, so a note
rebuilt or reconverted later makes its card stale (`status` says so).

The card is a model's reading aid with checked quotes: `citable: false` —
the map cites the paper (`P-XXXX §3.2`), never the card, and `check_sota.py`
still checks every figure against the paper's text. A `send: never` note
never gets a card. The main session does not read cards (they hold paper
text): the vault hook treats them like paper notes.

Exit: 0 ok · 1 nothing written · 2 bad input. Standard library only.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "citations"))
sys.path.insert(0, str(HERE.parent / "security"))
import check_quotes as cq  # noqa: E402
import vaultnotes as vn  # noqa: E402
from send_guard import is_flagged, is_model_notes  # noqa: E402

TOOL = "kairo/ficha@1.0.0"
DIR = "_fichas"
KINDS = ("problema", "método", "setup", "resultado", "limitación", "definición", "benchmark")
MIN_QUOTE_WORDS = 6
_NUM = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?")
_PLOT = re.compile(r"≈[^()]*\(leído de la Figura")


def paper_notes(vault: Path) -> list[Path]:
    return sorted(p for p in (vault / "Papers").glob("P-*.md") if p.is_file() and not is_model_notes(p.resolve()))


def find_note(vault: Path, pid: str) -> Path | None:
    hits = [p for p in paper_notes(vault) if p.name.split(" ")[0].removesuffix(".md") == pid]
    return hits[0] if len(hits) == 1 else None


def text_sha(note_text: str) -> str:
    """sha256 of the note's `## Texto completo` (whitespace collapsed)."""
    m = re.search(r"(?ms)^## Texto completo\s*\n(.*?)(?=^## |\Z)", note_text)
    body = " ".join((m.group(1) if m else "").split())
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def card_path(vault: Path, pid: str) -> Path:
    return vault / "Papers" / DIR / f"{pid}.md"


def card_state(vault: Path, path: Path) -> str:
    pid = path.name.split(" ")[0].removesuffix(".md")
    if is_flagged(path):
        return "send_never"
    text = path.read_text(encoding="utf-8")
    fm = (vn.split_frontmatter(text) or ([], ""))[0]
    if (vn.fm_get(fm, "fulltext") or "") != "full":
        return "sin_texto"
    c = card_path(vault, pid)
    if not c.is_file():
        return "falta"
    cfm = (vn.split_frontmatter(c.read_text(encoding="utf-8")) or ([], ""))[0]
    return "vigente" if vn.fm_get(cfm, "nota_sha256") == text_sha(text) else "caducada"


def cmd_status(vault: Path, project: str | None, ids: list[str] | None) -> dict:
    notes = []
    for p in paper_notes(vault):
        pid = p.name.split(" ")[0].removesuffix(".md")
        if ids and pid not in ids:
            continue
        if project:
            fm = (vn.split_frontmatter(p.read_text(encoding="utf-8")) or ([], ""))[0]
            if not re.search(r"(?<![\w-])" + re.escape(project) + r"(?![\w-])", vn.fm_get(fm, "projects") or ""):
                continue
        notes.append({"id": pid, "path": p.relative_to(vault).as_posix(), "state": card_state(vault, p)})
    todo = [n for n in notes if n["state"] in ("falta", "caducada")]
    return {"tool": TOOL, "papers": len(notes), "vigentes": sum(n["state"] == "vigente" for n in notes),
            "to_card": [{"id": n["id"], "path": n["path"], "state": n["state"]} for n in todo],
            "sin_texto": [n["id"] for n in notes if n["state"] == "sin_texto"],
            "send_never": [n["id"] for n in notes if n["state"] == "send_never"]}


def _reply(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    fences = re.findall(r"```(?:json)?\s*\n?(\{.*?\})\s*\n?```", text, re.S)
    try:
        obj = json.loads(fences[-1] if fences else text)
    except ValueError as e:
        raise ValueError(f"the reply has no JSON block ({e})") from None
    if not isinstance(obj, dict) or not isinstance(obj.get("items"), list):
        raise ValueError("the reply must be {\"paper\": …, \"items\": [...]}")
    return obj


def check_item(vault: Path, pid: str, it: dict) -> str | None:
    """Why an item is dropped, or None to keep it."""
    if it.get("kind") not in KINDS:
        return f"tipo desconocido {it.get('kind')!r}"
    claim, quote, loc = (str(it.get(k) or "").strip() for k in ("claim", "quote", "locator"))
    if not claim or not quote or not loc:
        return "falta claim, quote o locator"
    if len(quote.split()) < MIN_QUOTE_WORDS:
        return f"cita de menos de {MIN_QUOTE_WORDS} palabras"
    got = cq.check_quote(str(vault), {"paper": pid, "locator": loc, "quote": quote, "error": None}, None)
    if not got["ok"]:
        return got["reason"]
    if not _PLOT.search(claim):
        missing = [n for n in _NUM.findall(claim) if n.replace(",", ".") not in quote.replace(",", ".")]
        if missing:
            return "cifras de la afirmación que no están en la cita: " + ", ".join(missing)
    return None


def render(pid: str, note_name: str, sha: str, model: str, kept: list[dict], dropped: list[dict],
           reply: dict, today: str) -> str:
    L = ["---", f"ficha_de: {pid}", f'nota_del_paper: "{note_name}"', f"nota_sha256: {sha}",
         f"modelo: {model}", f"fecha: {today}", f"tool: {TOOL}", "escrito_por: modelo", "citable: false",
         f"items: {len(kept)}", f"descartados: {len(dropped)}", "---", "",
         f"# Ficha — {pid} (escrita por un modelo; citas comprobadas)", "",
         "> Las afirmaciones son de un modelo; cada cita es texto literal del paper, comprobado por script bajo "
         "su localizador. Se cita el paper (`" + pid + " §…`), nunca esta ficha.", ""]
    for kind in KINDS:
        items = [i for i in kept if i["kind"] == kind]
        if not items:
            continue
        L += [f"## {kind.capitalize()}", ""]
        for i in items:
            L += [f"- {i['claim']} — {pid} {i['locator']}", f"  > {' '.join(i['quote'].split())}", ""]
    if reply.get("not_read"):
        L += ["## No leído por el modelo", ""] + [f"- {x}" for x in reply["not_read"]] + [""]
    if reply.get("suspicious"):
        L += ["## Texto sospechoso señalado", ""] + [f"- {x}" for x in reply["suspicious"]] + [""]
    if dropped:
        L += ["## Descartados por el script", ""]
        L += [f"- [{d['kind']}] {d['locator']}: {d['why']}" for d in dropped] + [""]
    return "\n".join(L)


def cmd_write(vault: Path, pid: str, reply_path: Path, model: str, today: str | None = None) -> dict:
    note = find_note(vault, pid)
    if note is None:
        raise ValueError(f"{pid}: no single paper note")
    if is_flagged(note):
        raise ValueError(f"{pid} is send: never: it never gets a card")
    text = note.read_text(encoding="utf-8")
    fm = (vn.split_frontmatter(text) or ([], ""))[0]
    if (vn.fm_get(fm, "fulltext") or "") != "full":
        raise ValueError(f"{pid} has no full text (abstract-only): a card needs the paper")
    reply = _reply(reply_path)
    if reply.get("paper") and reply["paper"] != pid:
        raise ValueError(f"the reply is about {reply['paper']}, not {pid}")
    kept, dropped = [], []
    for it in reply["items"]:
        why = check_item(vault, pid, it) if isinstance(it, dict) else "no es un objeto"
        row = {k: str((it or {}).get(k) or "").strip() for k in ("kind", "claim", "quote", "locator")} \
            if isinstance(it, dict) else {"kind": "?", "claim": "", "quote": "", "locator": "?"}
        (dropped.append({**row, "why": why}) if why else kept.append(row))
    if not kept:
        return {"tool": TOOL, "id": pid, "written": False, "kept": 0, "dropped": dropped}
    out = card_path(vault, pid)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(pid, note.name, text_sha(text), model, kept, dropped, reply,
                          today or _dt.date.today().isoformat()), encoding="utf-8", newline="\n")
    return {"tool": TOOL, "id": pid, "written": True, "card": out.relative_to(vault).as_posix(),
            "kept": len(kept), "dropped": [{"locator": d["locator"], "why": d["why"]} for d in dropped]}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("status")
    st.add_argument("--vault", required=True, type=Path)
    g = st.add_mutually_exclusive_group(required=True)
    g.add_argument("--project")
    g.add_argument("--ids", nargs="+")
    w = sub.add_parser("write")
    w.add_argument("--vault", required=True, type=Path)
    w.add_argument("--id", required=True)
    w.add_argument("--reply", required=True, type=Path)
    w.add_argument("--model", required=True)
    a = ap.parse_args(argv)
    try:
        out = cmd_status(a.vault, a.project, a.ids) if a.cmd == "status" else \
            cmd_write(a.vault, a.id, a.reply, a.model)
    except (ValueError, OSError) as e:
        print(json.dumps({"tool": TOOL, "error": str(e)}, ensure_ascii=False))
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if a.cmd == "status" or out.get("written") else 1


if __name__ == "__main__":
    raise SystemExit(main())
