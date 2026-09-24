#!/usr/bin/env python3
"""Find the public code repository of already-ingested `Papers/` notes.

Backfill for the `code_repo:` paper-note field (create-project step 6 fills it
for new papers). This script only READS public metadata and REPORTS what it
found, with a confidence level, for the researcher to confirm. It writes a
note only on an explicit `--confirm P-XXXX <url>`. It never clones, installs,
or runs anything -- fetching a repository is `paper-to-tool`'s job, and only
on demand.

Sources, strongest first (all public, no API key):

  1. arXiv Atom API -- the `arxiv:comment` field and the abstract. Written by
     the authors, so a repo URL here is author-stated          -> `alta`
  2. arXiv e-print source (the paper's own .tex) -- repo URLs in the paper
     text itself (footnotes, "code is available at ...")       -> `alta`
     A non-repo URL in a sentence that mentions code (e.g. a project page)
     is followed ONE hop; if that landing page links exactly one repo   -> `media`
  3. Hugging Face Papers API (`githubRepo`) -- Papers with Code's successor
     (paperswithcode.com has redirected to huggingface.co since 2025).
     `githubRepoAddedBy: auto` is an automatic match, so on its own it is
                                                                -> `baja`
     a human-added link                                         -> `media`
     Any source that agrees with an `alta` candidate only corroborates it.

Decision per paper:
  - exactly one distinct repo at the best confidence, and that confidence is
    `alta` or `media`                                        -> `proposed`
  - two or more distinct repos tied at the best confidence   -> `conflict`
    (no proposal; the researcher picks, or leaves it empty)
  - only `baja` candidates                                   -> `weak`
    (reported, never proposed -- an automatic match is not confident)
  - nothing                                                  -> `none`
Never guess: an empty `code_repo:` is the correct value when nothing is
confidently identified.

Usage:
    python find_code_repo.py --papers <vault>/Papers                 # report all
    python find_code_repo.py --papers <vault>/Papers --only P-0002 --json
    python find_code_repo.py --papers <vault>/Papers --missing-only  # skip filled notes
    python find_code_repo.py --papers <vault>/Papers --confirm P-0002 \\
        https://github.com/owner/repo --evidence "paper footnote 1 -> project page"
    python find_code_repo.py --version

Exit codes: 0 ok, 2 invalid input / note not found.
Standard library only.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

__version__ = "1.0.0"

USER_AGENT = "kairo-find-code-repo/1.0 (research vault backfill; mailto via plugin README)"
ARXIV_API = "https://export.arxiv.org/api/query?id_list={}"
ARXIV_EPRINT = "https://arxiv.org/e-print/{}"
ARXIV_ABS = "https://arxiv.org/abs/{}"
HF_PAPER_API = "https://huggingface.co/api/papers/{}"
ARXIV_DELAY_S = 3.0  # arXiv asks for ~1 request / 3 s
EPRINT_MAX_BYTES = 60 * 1024 * 1024
PAGE_MAX_BYTES = 10 * 1024 * 1024

REPO_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org")
CONFIDENCE_ORDER = {"alta": 3, "media": 2, "baja": 1}

_URL_RE = re.compile(r"https?://[^\s{}<>\"'\\\]\[)(,;]+", re.IGNORECASE)
_CODE_WORDS = re.compile(
    r"\b(code|implementation|repositor(y|ies)|github|source|reproduc\w*|colab|notebook)\b",
    re.IGNORECASE,
)
_ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
# A repo in the paper text is the paper's OWN code only when the sentence says so.
# "Our implementation is adapted from <repo>" / "We use the VGG11 from <repo>"
# name a dependency, not the paper's code -- a dependency marker wins.
_DEPENDENCY = re.compile(
    r"\b(adapted from|we (use|used|adopt|adopted|borrow|borrowed|build on|built on|follow|followed)"
    r"|using the implementation|implementation (provided|released) by|based on|taken from|forked from)\b",
    re.IGNORECASE,
)
_OWNERSHIP = re.compile(
    r"\b(code|implementation|source)\b[^.]{0,120}\b(available|found|released|provided|hosted|open[- ]sourced?)\b"
    r"|\bour (code|implementation|codebase|experiments?|results)\b"
    r"|\b(code|implementation) (for|of) (this|our) (paper|work)\b"
    r"|\bto reproduce\b",
    re.IGNORECASE,
)
_ANCHOR = re.compile(r"<a\b[^>]*\bhref\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")


# --------------------------------------------------------------------------- #
# URL handling (pure functions -- unit-tested)
# --------------------------------------------------------------------------- #

def normalize_repo_url(url: str) -> str | None:
    """Return the canonical `https://<host>/<owner>/<repo>` form, or None if the
    URL is not a repository on a known code host (profile pages, gists, raw
    files and bare hosts are not repositories)."""
    url = url.strip().rstrip(".,;:)]}>'\"")
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in REPO_HOSTS:
        return None
    segs = [s for s in parts.path.split("/") if s]
    if len(segs) < 2:
        return None
    owner, repo = segs[0], segs[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo or owner.lower() in {"orgs", "users", "topics", "sponsors", "features"}:
        return None
    return f"https://{host}/{owner}/{repo}"


def extract_urls(text: str) -> list[str]:
    """All http(s) URLs in free text / LaTeX, with LaTeX escapes undone."""
    text = text.replace("\\_", "_").replace("\\#", "#").replace("\\%", "%").replace("\\~{}", "~")
    return [m.group(0) for m in _URL_RE.finditer(text)]


def repo_urls_in(text: str) -> list[str]:
    seen: list[str] = []
    for u in extract_urls(text):
        n = normalize_repo_url(u)
        if n and n not in seen:
            seen.append(n)
    return seen


def sentence_around(text: str, start: int, end: int) -> str:
    """The sentence containing text[start:end]: back to the previous '. ' / blank
    line / footnote opening, forward to the next '. ' / blank line (bounded)."""
    lo = max(0, start - 400)
    before = text[lo:start]
    cut = max(before.rfind(". "), before.rfind("\n\n"), before.rfind("\\footnote{"), before.rfind("\\section"))
    s0 = lo + cut + 1 if cut >= 0 else lo
    after = text[end:end + 250]
    stops = [i for i in (after.find(". "), after.find("\n\n")) if i >= 0]
    s1 = end + (min(stops) if stops else len(after))
    return text[s0:s1]


def classify_sentence(sentence: str, neutral: str = "media") -> str:
    """alta = the sentence claims the repo as the paper's own code; baja = the
    repo is named as a dependency; otherwise `neutral`."""
    if _DEPENDENCY.search(sentence):
        return "baja"
    if _OWNERSHIP.search(sentence):
        return "alta"
    return neutral


def classified_repo_urls(text: str, neutral: str = "media") -> list[tuple[str, str, str]]:
    """(repo_url, confidence, sentence) for every repo URL in `text`, keeping the
    strongest classification when a repo appears several times."""
    text = text.replace("\\_", "_").replace("\\#", "#").replace("\\%", "%").replace("\\~{}", "~")
    best: dict[str, tuple[str, str]] = {}
    for m in _URL_RE.finditer(text):
        url = normalize_repo_url(m.group(0))
        if not url:
            continue
        sent = sentence_around(text, m.start(), m.end())
        conf = classify_sentence(sent, neutral)
        if url not in best or CONFIDENCE_ORDER[conf] > CONFIDENCE_ORDER[best[url][0]]:
            best[url] = (conf, " ".join(sent.split())[:240])
    return [(u, c, s) for u, (c, s) in best.items()]


def anchored_repo_urls(html: str, window: int = 300) -> list[str]:
    """Repos a landing page links as <a href> near a code word. Script/CSS/asset
    URLs (a site's own JS libraries) are not anchors and are ignored."""
    out: list[str] = []
    for m in _ANCHOR.finditer(html):
        url = normalize_repo_url(m.group(1))
        if not url or url in out:
            continue
        ctx = _TAGS.sub(" ", html[max(0, m.start() - window): m.end() + window])
        if _CODE_WORDS.search(ctx):
            out.append(url)
    return out


def code_context_urls(text: str, window: int = 200) -> list[str]:
    """Non-repo URLs that sit within `window` characters of a code-related word:
    candidates for a one-hop follow (e.g. a project page that links the repo)."""
    out: list[str] = []
    for m in _URL_RE.finditer(text):
        raw = m.group(0).rstrip(".,;:)]}>'\"")
        if normalize_repo_url(raw):
            continue
        ctx = text[max(0, m.start() - window): m.end() + window]
        if _CODE_WORDS.search(ctx) and raw not in out:
            out.append(raw)
    return out


# --------------------------------------------------------------------------- #
# Candidates and the decision rule (pure -- unit-tested)
# --------------------------------------------------------------------------- #

@dataclass
class Candidate:
    url: str
    confidence: str          # alta | media | baja
    source: str              # human-readable provenance, goes into code_repo_evidence


@dataclass
class Report:
    paper: str
    arxiv: str | None
    current_code_repo: str | None
    status: str = "none"     # proposed | conflict | weak | none | skipped
    proposed: str | None = None
    proposed_confidence: str | None = None
    proposed_evidence: str | None = None
    candidates: list[Candidate] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def decide(candidates: list[Candidate]) -> tuple[str, str | None, str | None, str | None]:
    """(status, url, confidence, evidence). Merge same-URL candidates keeping the
    strongest confidence and concatenating provenance."""
    if not candidates:
        return "none", None, None, None
    merged: dict[str, Candidate] = {}
    for c in candidates:
        m = merged.get(c.url)
        if m is None:
            merged[c.url] = Candidate(c.url, c.confidence, c.source)
            continue
        if CONFIDENCE_ORDER[c.confidence] > CONFIDENCE_ORDER[m.confidence]:
            m.confidence = c.confidence
        if c.source not in m.source:
            m.source = f"{m.source}; {c.source}"
    best = max(CONFIDENCE_ORDER[c.confidence] for c in merged.values())
    top = [c for c in merged.values() if CONFIDENCE_ORDER[c.confidence] == best]
    if len(top) > 1:
        return "conflict", None, None, None
    only = top[0]
    if only.confidence == "baja":
        return "weak", None, None, None
    return "proposed", only.url, only.confidence, only.source


# --------------------------------------------------------------------------- #
# Frontmatter I/O (pure-ish -- unit-tested)
# --------------------------------------------------------------------------- #

def split_frontmatter(text: str) -> tuple[list[str], str] | None:
    if not text.startswith("---"):
        return None
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], "\n".join(lines[i + 1:])
    return None


def fm_get(fm_lines: list[str], key: str) -> str | None:
    for ln in fm_lines:
        if ln.startswith(f"{key}:"):
            v = ln.split(":", 1)[1].strip().strip('"').strip("'")
            return v or None
    return None


def set_code_repo(text: str, url: str, evidence: str) -> str:
    """Set `code_repo:` + `code_repo_evidence:` in a note's frontmatter, replacing
    existing values or inserting them after `pdf:` (else before the closing ---)."""
    split = split_frontmatter(text)
    if split is None:
        raise ValueError("note has no YAML frontmatter")
    fm, body = split
    evidence = evidence.replace('"', "'")
    new = [f"code_repo: {url}", f'code_repo_evidence: "{evidence}"']
    fm = [ln for ln in fm if not ln.startswith(("code_repo:", "code_repo_evidence:"))]
    idx = next((i + 1 for i, ln in enumerate(fm) if ln.startswith("pdf:")), len(fm))
    fm[idx:idx] = new
    return "---\n" + "\n".join(fm) + "\n---\n" + body


# --------------------------------------------------------------------------- #
# Network sources
# --------------------------------------------------------------------------- #

def _get(url: str, max_bytes: int, timeout: float = 30.0, retry: bool = True) -> tuple[bytes, str]:
    """GET; with `retry`, 406/429/5xx back off (10/30/60 s) and retry, other
    errors raise. Observed 2026-09-24: export.arxiv.org answers most urllib
    requests with 406 Not Acceptable (occasionally one succeeds; curl from the
    same machine gets 200), so the arXiv API is called once with retry=False
    and the arxiv.org/abs page is the fallback. gzip is requested explicitly
    and decoded if the server uses it."""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "*/*", "Accept-Encoding": "gzip"})
    for wait in ((10.0, 30.0, 60.0, None) if retry else (None,)):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise ValueError(f"response larger than {max_bytes} bytes")
                if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                    data = gzip.decompress(data)
                return data, r.geturl()
        except urllib.error.HTTPError as e:
            if wait is None or not (e.code in (406, 429) or e.code >= 500):
                raise
            time.sleep(wait)
    raise AssertionError("unreachable")


def abs_page_fields(html: str) -> tuple[str, str]:
    """(comments, abstract) from an arxiv.org/abs/<id> page. Links there are
    rendered as <a href="URL">this https URL</a>, so each anchor is replaced by
    its href before tags are stripped."""
    def cell(pattern: str) -> str:
        m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if not m:
            return ""
        frag = re.sub(r"<a\b[^>]*\bhref\s*=\s*[\"']([^\"']+)[\"'][^>]*>.*?</a>", r" \1 ", m.group(1),
                      flags=re.IGNORECASE | re.DOTALL)
        return " ".join(_TAGS.sub(" ", frag).split())
    return (cell(r'<td class="tablecell comments[^"]*">(.*?)</td>'),
            cell(r'<blockquote class="abstract[^"]*">(.*?)</blockquote>'))


def from_arxiv_api(arxiv_id: str, rep: Report) -> list[Candidate]:
    """Comments + abstract: the Atom API first; if the API is lost to throttling,
    the same two fields from the arxiv.org/abs page (a different host)."""
    comment = summary = ""
    try:
        # one attempt: from urllib the API 406s persistently (curl does not),
        # and the abs page below carries the same two fields
        data, _ = _get(ARXIV_API.format(urllib.parse.quote(arxiv_id)), PAGE_MAX_BYTES, retry=False)
        entry = ET.fromstring(data).find("a:entry", _ATOM)
        if entry is None:
            rep.notes.append("arXiv API: no entry")
            return []
        comment = entry.findtext("arxiv:comment", default="", namespaces=_ATOM) or ""
        summary = entry.findtext("a:summary", default="", namespaces=_ATOM) or ""
    except (urllib.error.URLError, ValueError, TimeoutError, ET.ParseError) as e:
        try:
            page, _ = _get(ARXIV_ABS.format(arxiv_id), PAGE_MAX_BYTES)
            comment, summary = abs_page_fields(page.decode("utf-8", errors="replace"))
            rep.notes.append(f"arXiv API lost ({e}); comments/abstract read from the abs page instead")
        except (urllib.error.URLError, ValueError, TimeoutError) as e2:
            rep.notes.append(f"arXiv comments/abstract LOST (API: {e}; abs page: {e2})")
            return []
    out = []
    # authors put their own code link in comments/abstract, so a neutral mention
    # there still counts as author-stated; an explicit dependency does not
    for u, conf, sent in classified_repo_urls(comment, neutral="alta"):
        out.append(Candidate(u, conf, f"arXiv comments: \"{sent}\""))
    for u, conf, sent in classified_repo_urls(summary, neutral="alta"):
        out.append(Candidate(u, conf, f"arXiv abstract: \"{sent}\""))
    return out


def _tex_from_eprint(blob: bytes) -> str:
    """The e-print is a gzipped tar of the source, a gzipped single .tex, or a PDF."""
    if blob[:4] == b"%PDF":
        return ""
    try:
        raw = gzip.decompress(blob)
    except (OSError, EOFError):   # not gzip, or a truncated stream
        raw = blob
    try:
        with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
            chunks = []
            for m in tf.getmembers():
                if m.isfile() and m.name.lower().endswith((".tex", ".bbl")):
                    f = tf.extractfile(m)   # read into memory only -- nothing is written to disk
                    if f:
                        chunks.append(f.read().decode("utf-8", errors="replace"))
            return "\n".join(chunks)
    except tarfile.TarError:
        return raw.decode("utf-8", errors="replace")


def from_arxiv_source(arxiv_id: str, rep: Report) -> list[Candidate]:
    try:
        blob, _ = _get(ARXIV_EPRINT.format(arxiv_id), EPRINT_MAX_BYTES, timeout=60.0)
    except (urllib.error.URLError, ValueError, TimeoutError) as e:
        rep.notes.append(f"arXiv e-print LOST ({e})")
        return []
    tex = _tex_from_eprint(blob)
    if not tex:
        rep.notes.append("arXiv e-print is PDF-only -- no source text scanned")
        return []
    # drop LaTeX comments: a commented-out URL is not author-stated
    tex = "\n".join(re.sub(r"(?<!\\)%.*$", "", ln) for ln in tex.split("\n"))
    out = []
    for u, conf, sent in classified_repo_urls(tex, neutral="media"):
        label = {"alta": "paper text claims it as the paper's code",
                 "media": "repo URL in paper text, no ownership statement",
                 "baja": "paper text names it as a dependency"}[conf]
        out.append(Candidate(u, conf, f"{label}: \"{sent}\""))
    for hop in code_context_urls(tex)[:3]:
        time.sleep(1.0)
        try:
            page, final = _get(hop, PAGE_MAX_BYTES)
        except (urllib.error.URLError, ValueError, TimeoutError) as e:
            rep.notes.append(f"one-hop {hop} LOST ({e})")
            continue
        final_repo = normalize_repo_url(final)
        linked = [final_repo] if final_repo else anchored_repo_urls(page.decode("utf-8", errors="replace"))
        if len(linked) == 1:
            out.append(Candidate(linked[0], "media", f"paper text links {hop} in a code sentence -> that page links this repo"))
        elif len(linked) > 1:
            rep.notes.append(f"one-hop {hop} links {len(linked)} repos -- not proposed: {', '.join(linked[:5])}")
    return out


def from_huggingface(arxiv_id: str, rep: Report) -> list[Candidate]:
    try:
        data, _ = _get(HF_PAPER_API.format(arxiv_id), PAGE_MAX_BYTES)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        rep.notes.append(f"Hugging Face Papers LOST (HTTP {e.code})")
        return []
    except (urllib.error.URLError, ValueError, TimeoutError) as e:
        rep.notes.append(f"Hugging Face Papers LOST ({e})")
        return []
    try:
        d = json.loads(data)
    except json.JSONDecodeError:
        rep.notes.append("Hugging Face Papers: unparseable response")
        return []
    url = normalize_repo_url(d.get("githubRepo") or "")
    if not url:
        return []
    added = d.get("githubRepoAddedBy") or "unknown"
    conf = "baja" if added == "auto" else "media"
    return [Candidate(url, conf, f"Hugging Face Papers githubRepo (addedBy={added})")]


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #

def find_note(papers: Path, pid: str) -> Path | None:
    for p in sorted(papers.glob(f"{pid} *.md")) + sorted(papers.glob(f"{pid}.md")):
        return p
    return None


def scan(note: Path) -> Report:
    text = note.read_text(encoding="utf-8")
    split = split_frontmatter(text)
    fm = split[0] if split else []
    pid = fm_get(fm, "id") or note.stem.split(" ")[0]
    rep = Report(paper=pid, arxiv=fm_get(fm, "arxiv"), current_code_repo=fm_get(fm, "code_repo"))
    if not rep.arxiv:
        rep.notes.append("no arXiv id -- only arXiv-indexed papers can be scanned; check the publisher page by hand")
        rep.status = "none"
        return rep
    cands: list[Candidate] = []
    for i, source in enumerate((from_arxiv_api, from_arxiv_source, from_huggingface)):
        if i:
            time.sleep(ARXIV_DELAY_S)
        try:
            cands += source(rep.arxiv, rep)
        except Exception as e:  # one broken source is a reported gap, never a crash of the whole backfill
            rep.notes.append(f"{source.__name__} LOST ({type(e).__name__}: {e})")
    rep.candidates = cands
    rep.status, rep.proposed, rep.proposed_confidence, rep.proposed_evidence = decide(cands)
    return rep


def print_report(reps: list[Report]) -> None:
    for r in reps:
        head = f"{r.paper}  arXiv:{r.arxiv or '-'}  status={r.status}"
        if r.current_code_repo:
            head += f"  (current code_repo: {r.current_code_repo})"
        print(head)
        if r.proposed:
            print(f"  PROPOSED [{r.proposed_confidence}] {r.proposed}")
            print(f"    evidence: {r.proposed_evidence}")
        for c in r.candidates:
            print(f"  - [{c.confidence}] {c.url}  <- {c.source}")
        for n in r.notes:
            print(f"  ! {n}")
    print()
    print("Nothing was written. To record a repo after checking it yourself:")
    print("  python find_code_repo.py --papers <dir> --confirm P-XXXX <url> --evidence \"<where it was found>\"")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--papers", type=Path, help="the vault's Papers/ directory")
    ap.add_argument("--only", nargs="+", metavar="P-XXXX", help="restrict to these paper ids")
    ap.add_argument("--missing-only", action="store_true", help="skip notes whose code_repo is already set")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--confirm", nargs=2, metavar=("P-XXXX", "URL"),
                    help="write code_repo for one paper (the researcher's explicit confirmation)")
    ap.add_argument("--evidence", help="with --confirm: where the URL was found (required)")
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args(argv)

    if a.papers is None or not a.papers.is_dir():
        print("error: --papers must be an existing directory", file=sys.stderr)
        return 2

    if a.confirm:
        pid, url = a.confirm
        norm = normalize_repo_url(url)
        if norm is None:
            print(f"error: {url!r} is not a repository URL on {', '.join(REPO_HOSTS)}", file=sys.stderr)
            return 2
        if not a.evidence:
            print("error: --confirm requires --evidence (where the URL was found)", file=sys.stderr)
            return 2
        note = find_note(a.papers, pid)
        if note is None:
            print(f"error: no note for {pid} in {a.papers}", file=sys.stderr)
            return 2
        note.write_text(set_code_repo(note.read_text(encoding="utf-8"), norm, a.evidence), encoding="utf-8")
        print(f"{pid}: code_repo = {norm}  ({note.name})")
        return 0

    notes = sorted(a.papers.glob("P-*.md"))
    if a.only:
        wanted = set(a.only)
        notes = [n for n in notes if n.stem.split(" ")[0] in wanted]
    reps = []
    for n in notes:
        text = n.read_text(encoding="utf-8")
        split = split_frontmatter(text)
        if a.missing_only and split and fm_get(split[0], "code_repo"):
            reps.append(Report(paper=n.stem.split(" ")[0], arxiv=fm_get(split[0], "arxiv"),
                               current_code_repo=fm_get(split[0], "code_repo"), status="skipped"))
            continue
        print(f"scanning {n.stem.split(' ')[0]} ...", file=sys.stderr, flush=True)
        reps.append(scan(n))
    if a.json:
        print(json.dumps([asdict(r) for r in reps], indent=2, ensure_ascii=False))
    else:
        print_report(reps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
