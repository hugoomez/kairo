#!/usr/bin/env python3
"""Fill a Papers/ note's missing `## Resumen` from public bibliographic records.

    fill_abstract.py --papers <vault>/Papers --only P-0031 P-0033 [--write]

A note whose `## Resumen` says "No disponible" (step 5 of create-project found
no abstract) is looked up again, in order:

  1. OpenAlex `abstract_inverted_index` (by the note's openalex_id, else DOI,
     else the arXiv DOI). The abstract is rebuilt by placing each word at the
     positions OpenAlex lists for it: the words are the record's own, in its
     order; nothing is added or reworded.
  2. Crossref `abstract` (JATS markup stripped to its text).
  3. arXiv `summary` (for a note with an arXiv id).

The first source that returns at least MIN_WORDS words wins, and the section
becomes `> Fuente: <URL>, obtenido <date>` + the abstract verbatim. When none
does, the section is left as it is and the sources tried are reported. A note
that already has an abstract, or is `send: never`, is never touched. Without
--write nothing is written. Prints a JSON report.

Exit codes: 0 done (possibly with notes still missing) · 1 error.
Standard library only.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ledger"))
from verifier_packet import fm_scalar, split_frontmatter  # noqa: E402  (also puts security/ on the path)
from send_guard import is_flagged  # noqa: E402, I001

TOOL = "kairo/fill_abstract@1.0.0"
MIN_WORDS = 20
MISSING = "No disponible"
UA = {"User-Agent": f"Mozilla/5.0 ({TOOL}; research use)"}


def from_inverted_index(ii: dict | None) -> str:
    """OpenAlex's abstract_inverted_index -> the abstract, each word at its positions."""
    if not ii:
        return ""
    pos: dict[int, str] = {}
    for word, places in ii.items():
        for i in places:
            pos[int(i)] = word
    return " ".join(pos[i] for i in sorted(pos)).strip()


def from_jats(s: str | None) -> str:
    """Crossref's JATS abstract -> its text (tags removed, the word 'Abstract' heading dropped)."""
    if not s:
        return ""
    t = re.sub(r"<jats:title>\s*Abstract\s*</jats:title>", " ", s, flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t).strip()


def get_json(url: str) -> dict | None:
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(2 * (attempt + 1))
    return None


def get_text(url: str) -> str | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
            return r.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError):
        return None


def needs_abstract(text: str) -> bool:
    m = re.search(r"^## Resumen\s*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        return True
    body = "\n".join(ln for ln in m.group(1).split("\n") if not ln.startswith(">")).strip()
    return not body or body.startswith(MISSING)


def candidates(fm: list[str]) -> list[tuple[str, str]]:
    """[(source label, URL)] to try, in order."""
    out = []
    oa = (fm_scalar(fm, "openalex_id") or "").strip()
    doi = (fm_scalar(fm, "doi") or "").strip().removeprefix("https://doi.org/")
    arxiv = (fm_scalar(fm, "arxiv") or "").strip()
    if oa:
        out.append(("openalex", f"https://api.openalex.org/works/{oa}"))
    elif doi:
        out.append(("openalex", f"https://api.openalex.org/works/doi:{urllib.parse.quote(doi, safe='/')}"))
    elif arxiv:
        out.append(("openalex", f"https://api.openalex.org/works/doi:10.48550/arXiv.{arxiv}"))
    if doi:
        out.append(("crossref", f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='/')}"))
    if arxiv:
        out.append(("arxiv", f"https://export.arxiv.org/api/query?id_list={arxiv}"))
    return out


def fetch(label: str, url: str) -> str:
    if label == "openalex":
        w = get_json(url)
        return from_inverted_index(w.get("abstract_inverted_index")) if w else ""
    if label == "crossref":
        w = get_json(url)
        return from_jats((w or {}).get("message", {}).get("abstract")) if w else ""
    if label == "arxiv":
        t = get_text(url) or ""
        m = re.search(r"<entry>.*?<summary[^>]*>(.*?)</summary>", t, re.S)
        return re.sub(r"\s+", " ", html.unescape(m.group(1))).strip() if m else ""
    return ""


def with_abstract(text: str, abstract: str, source_url: str, today: str) -> str:
    block = f"## Resumen\n\n> Fuente: {source_url}, obtenido {today} (texto del registro, sin reescribir; {TOOL})\n\n{abstract}\n\n"
    if re.search(r"^## Resumen\s*$", text, re.M):
        return re.sub(r"^## Resumen\s*\n.*?(?=^## |\Z)", lambda _: block, text, count=1, flags=re.M | re.S)
    return re.sub(r"^## Texto completo", lambda _: block + "## Texto completo", text, count=1, flags=re.M)


def process(path: Path, write: bool, today: str, fetcher=fetch) -> dict:
    text = path.read_text(encoding="utf-8")
    fm, _ = split_frontmatter(text)
    pid = fm_scalar(fm, "id") or path.stem.split(" ")[0]
    if is_flagged(path):
        return {"id": pid, "status": "skipped_send_never"}
    if not needs_abstract(text):
        return {"id": pid, "status": "has_abstract"}
    tried = []
    for label, url in candidates(fm):
        tried.append(label)
        abstract = fetcher(label, url)
        if len(abstract.split()) >= MIN_WORDS:
            if write:
                path.write_text(with_abstract(text, abstract, url, today), encoding="utf-8", newline="\n")
            return {"id": pid, "status": "filled" if write else "found", "source": label, "words": len(abstract.split())}
    return {"id": pid, "status": "still_missing", "tried": tried}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--papers", required=True, type=Path)
    ap.add_argument("--only", nargs="+", required=True, metavar="P-XXXX")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    today = _dt.date.today().isoformat()
    out = []
    for pid in a.only:
        hits = sorted(a.papers.glob(f"{pid} *.md")) + sorted(a.papers.glob(f"{pid}.md"))
        if not hits:
            out.append({"id": pid, "status": "not_found"})
            continue
        try:
            out.append(process(hits[0], a.write, today))
        except OSError as exc:
            print(json.dumps({"error": str(exc)}))
            return 1
    print(json.dumps({"tool": TOOL, "results": out}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
