#!/usr/bin/env python3
"""Keep vault content out of an applied project's code repository.

The code repository is always a separate git repository, outside the vault.
It may have a GitHub remote (private by default, public only by the
researcher's explicit choice); the vault never does. This guard runs as the
repository's `pre-push` hook and blocks a push that would carry vault content.

    repo_guard.py index   --vault V [--out F]
        fingerprint the vault: 12-word shingles of every note's text, hashed.
        Only hashes are stored, outside both repositories (default
        ~/.kairo/vault-fingerprints.json).
    repo_guard.py install --repo R --vault V --project-dir D [--index F]
        write .git/hooks/pre-push; refuse a repository inside the vault.
    repo_guard.py check   --repo R --vault V --project-dir D [--index F]
        the hook itself (reads git's pre-push stdin). Exit 0 = push allowed.
    repo_guard.py scan    --repo R --vault V --project-dir D [--index F] [--rev HEAD]
        the same checks on a revision, without pushing (for the interface).
    repo_guard.py allow   --repo R --file PATH --reason "..." [--vault V --project-dir D]
        a researcher-only override for ONE file's CURRENT content, for a
        verbatim-overlap or vault-note finding (never a secret). Stored in
        .git/kairo-allow.json (never pushed), logged in the notebook.

What `check` blocks, for every file the pushed commits change:
  * everything check_bundle.py blocks — keys, tokens, credential files,
    `.env`, copied vault notes, vault paths (the same scanner, run on a
    scratch copy of the pushed files);
  * verbatim overlap with the vault: >= MIN_SHARED 12-word shingles shared
    with one vault note (paper text, hypotheses, claims, notes, notebook…),
    naming the note;
  * a repository inside the vault or containing it; a remote URL that points
    at the vault;
  * a remote whose visibility (checked with `gh`, when available) differs
    from the hub's `code_visibility`.

Exit codes: 0 ok · 3 blocked / refused · 1 error. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
__version__ = "1.0.0"
SHINGLE = 12
MIN_SHARED = 3
MAX_FILE_BYTES = 2 * 1024 * 1024
def default_index(vault: Path) -> Path:
    """One index per vault, outside both repositories: ~/.kairo/ (or
    KAIRO_FINGERPRINT_DIR), named by a hash of the vault's path."""
    base = Path(os.environ.get("KAIRO_FINGERPRINT_DIR") or (Path.home() / ".kairo"))
    key = hashlib.sha1(str(vault.resolve()).lower().encode()).hexdigest()[:12]
    return base / f"vault-fingerprints-{key}.json"
ZERO = "0" * 40
AGENT_MARKERS = ("CLAUDECODE", "KAIRO_AGENT_SESSION")
# Vault areas fingerprinted (anything under them, *.md). The code repo must
# not carry any of it.
VAULT_AREAS = ("Papers", "Projects", "Ideas", "Audits", "Requisitos")
OVERRIDABLE = ("verbatim_overlap", "vault_note")


class Refused(Exception):
    pass


# --------------------------------------------------------------------------
# Fingerprints
# --------------------------------------------------------------------------

def words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]+", text)


def shingles(text: str) -> set[str]:
    w = words(text)
    return {hashlib.blake2b(" ".join(w[i:i + SHINGLE]).encode(), digest_size=8).hexdigest()
            for i in range(len(w) - SHINGLE + 1)}


def vault_notes(vault: Path) -> list[Path]:
    return [p for area in VAULT_AREAS if (vault / area).is_dir() for p in sorted((vault / area).rglob("*.md"))]


def build_index(vault: Path) -> dict:
    notes: list[str] = []
    table: dict[str, int] = {}
    for p in vault_notes(vault):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        idx = len(notes)
        notes.append(p.relative_to(vault).as_posix())
        for h in shingles(text):
            table.setdefault(h, idx)
    return {"tool": f"kairo/repo_guard@{__version__}", "shingle": SHINGLE, "built": date.today().isoformat(),
            "vault": str(vault.resolve()), "notes": notes, "shingles": table}


def load_index(path: Path | None, vault: Path) -> dict:
    """The vault's fingerprint index, rebuilt whenever it is missing, belongs
    to another vault, or is older than any vault note — a stale index would
    let new paper text through."""
    path = path or default_index(vault)
    if path.is_file():
        idx = json.loads(path.read_text(encoding="utf-8"))
        built = path.stat().st_mtime
        fresh = (idx.get("vault") == str(vault.resolve())
                 and all(p.stat().st_mtime <= built for p in vault_notes(vault)))
        if fresh:
            return idx
    idx = build_index(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(idx), encoding="utf-8")
    return idx


def overlap(text: str, index: dict) -> list[tuple[str, int]]:
    counts: dict[int, int] = {}
    table = index["shingles"]
    for h in shingles(text):
        n = table.get(h)
        if n is not None:
            counts[n] = counts.get(n, 0) + 1
    return sorted(((index["notes"][n], c) for n, c in counts.items() if c >= MIN_SHARED), key=lambda x: -x[1])


# --------------------------------------------------------------------------
# Repository facts
# --------------------------------------------------------------------------

def git(repo: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise Refused(f"git {' '.join(args[:2])}: {r.stderr.strip()[:300]}")
    return r.stdout


def inside(a: Path, b: Path) -> bool:
    a, b = a.resolve(), b.resolve()
    return a == b or b in a.parents


def location_problems(repo: Path, vault: Path) -> list[str]:
    out = []
    if inside(repo, vault):
        out.append(f"the code repository is inside the vault ({repo})")
    if inside(vault, repo):
        out.append("the code repository contains the vault")
    vault_s = str(vault.resolve()).replace("\\", "/").lower()
    for line in git(repo, "remote", "-v", check=False).splitlines():
        parts = line.split()
        url = parts[1] if len(parts) > 1 else ""
        if not url:
            continue
        low = url.replace("\\", "/").lower()
        local = "://" not in url and not re.match(r"^[\w.-]+@[\w.-]+:", url)
        if (local and inside(Path(url.removeprefix("file://")), vault)) or vault_s in low:
            out.append(f"a remote points at the vault: {url}")
    return sorted(set(out))


def hub_value(project_dir: Path, key: str) -> str | None:
    hub = project_dir / "_hub.md"
    if not hub.is_file():
        return None
    m = re.search(rf"^{key}:\s*(.+?)\s*(?:#.*)?$", hub.read_text(encoding="utf-8"), re.M)
    return m.group(1).strip().strip("'\"") if m else None


def visibility_problem(repo: Path, project_dir: Path, remote_url: str | None) -> tuple[str | None, str | None]:
    """(problem, note). No `gh` → no problem, but a note that it was not checked."""
    expected = (hub_value(project_dir, "code_visibility") or "private").lower()
    if not remote_url or "github.com" not in remote_url.lower():
        return None, None  # only GitHub remotes have a visibility to check
    if not shutil.which("gh"):
        return None, "visibilidad del remoto no comprobada: gh no está instalado"
    r = subprocess.run(["gh", "repo", "view", remote_url, "--json", "visibility", "-q", ".visibility"],
                       cwd=repo, capture_output=True, text=True)
    if r.returncode != 0:
        return None, f"visibilidad del remoto no comprobada: {r.stderr.strip()[:200]}"
    actual = r.stdout.strip().lower()
    if actual and actual != expected:
        return f"the remote is {actual}, the project hub says code_visibility: {expected}", None
    return None, None


# --------------------------------------------------------------------------
# Scanning pushed content
# --------------------------------------------------------------------------

def changed_files(repo: Path, local: str, remote: str) -> list[str]:
    if remote == ZERO:
        out = git(repo, "ls-tree", "-r", "--name-only", local)
    else:
        out = git(repo, "diff", "--name-only", "--diff-filter=ACMR", f"{remote}..{local}")
    return [f for f in out.splitlines() if f]


def allowlist(repo: Path) -> dict:
    f = repo / ".git" / "kairo-allow.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}


def scan(repo: Path, vault: Path, project_dir: Path, index: dict, rev: str, files: list[str],
         remote_url: str | None) -> dict:
    findings: list[dict] = []
    notes: list[str] = []
    for p in location_problems(repo, vault):
        findings.append({"kind": "location", "path": None, "detail": p, "overridable": False})
    vp, note = visibility_problem(repo, project_dir, remote_url)
    if vp:
        findings.append({"kind": "visibility", "path": None, "detail": vp, "overridable": False})
    if note:
        notes.append(note)
    allowed = allowlist(repo)
    with tempfile.TemporaryDirectory(prefix="kairo-push-") as scratch:
        root = Path(scratch)
        for f in files:
            blob = subprocess.run(["git", "show", f"{rev}:{f}"], cwd=repo, capture_output=True)
            if blob.returncode != 0:
                continue
            data = blob.stdout
            dest = root / f
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            if len(data) > MAX_FILE_BYTES or b"\x00" in data[:4096]:
                continue
            sha = hashlib.sha256(data).hexdigest()
            for note_path, count in overlap(data.decode("utf-8", errors="replace"), index):
                ok = allowed.get(f, {}).get("sha256") == sha
                findings.append({"kind": "verbatim_overlap", "path": f, "overridable": True, "allowed": ok,
                                 "detail": f"{count} fragmentos de 12 palabras idénticos a {note_path}"})
        cb = subprocess.run([sys.executable, str(HERE.parent / "security" / "check_bundle.py"), str(root)],
                            capture_output=True, text=True, encoding="utf-8")
        try:
            report = json.loads(cb.stdout)
        except json.JSONDecodeError:
            report = {"status": "error", "findings": []}
            findings.append({"kind": "scanner_error", "path": None, "overridable": False,
                             "detail": (cb.stderr or cb.stdout)[:300]})
        for fd in report.get("findings", []):
            if fd.get("severity") != "block":
                continue
            kind = fd.get("kind", "?")
            path = fd.get("path")
            ok = False
            if kind in OVERRIDABLE and path and path in allowed:
                blob = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=repo, capture_output=True)
                ok = blob.returncode == 0 and allowed[path].get("sha256") == hashlib.sha256(blob.stdout).hexdigest()
            findings.append({"kind": kind, "path": path, "overridable": kind in OVERRIDABLE, "allowed": ok,
                             "detail": f"check_bundle.py: {kind}"})
    blocking = [x for x in findings if not x.get("allowed")]
    return {"ok": not blocking, "findings": findings, "blocking": blocking, "notes": notes, "files": len(files)}


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_check(a) -> int:
    repo, vault = a.repo.resolve(), a.vault.resolve()
    index = load_index(a.index, vault)
    remote_url = os.environ.get("KAIRO_PUSH_REMOTE_URL")
    worst = 0
    for line in sys.stdin.read().splitlines():
        parts = line.split()
        if len(parts) != 4 or parts[1] == ZERO:
            continue  # a deletion carries no content
        local, remote = parts[1], parts[3]
        res = scan(repo, vault, a.project_dir.resolve(), index, local, changed_files(repo, local, remote), remote_url)
        for n in res["notes"]:
            print(f"[kairo] {n}", file=sys.stderr)
        for f in res["blocking"]:
            print(f"[kairo] BLOQUEADO {f['kind']}: {f.get('path') or ''} — {f['detail']}", file=sys.stderr)
        if not res["ok"]:
            worst = 3
    if worst:
        print("[kairo] push bloqueado: el repositorio de código no puede llevar contenido del vault. "
              "Si un hallazgo de texto es legítimo: repo_guard.py allow (en tu terminal).", file=sys.stderr)
    return worst


def cmd_install(a) -> dict:
    repo, vault = a.repo.resolve(), a.vault.resolve()
    probs = [p for p in location_problems(repo, vault) if "remote" not in p]
    if probs:
        raise Refused("; ".join(probs))
    if not (repo / ".git").is_dir():
        raise Refused(f"{repo} is not a git repository")
    index = a.index or default_index(vault)
    load_index(index, vault)
    hook = repo / ".git" / "hooks" / "pre-push"
    py = sys.executable.replace("\\", "/")
    script = str(Path(__file__).resolve()).replace("\\", "/")
    hook.write_text(
        "#!/bin/sh\n"
        "# Kairo pre-push guard: blocks vault content in this code repository.\n"
        '# git passes: $1 = remote name, $2 = remote url; refs on stdin.\n'
        'KAIRO_PUSH_REMOTE_URL="$2" export KAIRO_PUSH_REMOTE_URL\n'
        f'exec "{py}" "{script}" check --repo "{str(repo).replace(chr(92), "/")}" '
        f'--vault "{str(vault).replace(chr(92), "/")}" --project-dir "{str(a.project_dir.resolve()).replace(chr(92), "/")}" '
        f'--index "{str(index).replace(chr(92), "/")}"\n',
        encoding="utf-8", newline="\n")
    try:
        hook.chmod(0o755)
    except OSError:
        pass
    return {"hook": str(hook), "index": str(index)}


def cmd_allow(a) -> dict:
    marker = next((m for m in AGENT_MARKERS if os.environ.get(m)), None)
    if marker:
        raise Refused(f"overrides are the researcher's: refused inside an agent session ({marker} is set)")
    repo = a.repo.resolve()
    rel = Path(a.file).as_posix()
    if len(a.reason.strip()) < 10:
        raise Refused("say why this file may leave the machine (at least a sentence)")
    # Bind the override to the committed content — what a push carries (the
    # working copy may differ, e.g. in line endings).
    blob = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=repo, capture_output=True)
    if blob.returncode != 0:
        raise Refused(f"{rel} is not committed in HEAD — commit it first, then allow that exact content")
    allowed = allowlist(repo)
    sha = hashlib.sha256(blob.stdout).hexdigest()
    allowed[rel] = {"sha256": sha, "reason": a.reason.strip(), "date": date.today().isoformat()}
    (repo / ".git" / "kairo-allow.json").write_text(json.dumps(allowed, indent=1, ensure_ascii=False), encoding="utf-8")
    if a.vault and a.project_dir:
        log = a.project_dir / "Bitacora" / "acciones.jsonl"
        sys.path.insert(0, str(HERE.parent / "bitacora"))
        import action_log  # noqa: PLC0415
        action_log.append(log, {"actor": "human", "by": a.by, "action": "repo_push", "event": "approved",
                                "summary": f"Permitido publicar {Path(a.file).as_posix()} pese al aviso del guardia (sha256 {sha[:12]})",
                                "reason": a.reason.strip()})
    return {"file": Path(a.file).as_posix(), "sha256": sha}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("index")
    p.add_argument("--vault", required=True, type=Path)
    p.add_argument("--out", type=Path, default=None)
    for name in ("install", "check", "scan"):
        p = sub.add_parser(name)
        p.add_argument("--repo", required=True, type=Path)
        p.add_argument("--vault", required=True, type=Path)
        p.add_argument("--project-dir", required=True, type=Path)
        p.add_argument("--index", type=Path, default=None, help="default: one index per vault in ~/.kairo/")
        if name == "scan":
            p.add_argument("--rev", default="HEAD")
            p.add_argument("--all", action="store_true", help="every tracked file, not only changes since the remote")
    p = sub.add_parser("allow")
    p.add_argument("--repo", required=True, type=Path)
    p.add_argument("--file", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--by", default=os.environ.get("USERNAME") or os.environ.get("USER") or "investigador")
    p.add_argument("--vault", type=Path)
    p.add_argument("--project-dir", type=Path)
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        if a.cmd == "index":
            out = a.out or default_index(a.vault)
            idx = build_index(a.vault.resolve())
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(idx), encoding="utf-8")
            print(json.dumps({"out": str(out), "notes": len(idx["notes"]), "shingles": len(idx["shingles"])}))
            return 0
        if a.cmd == "check":
            return cmd_check(a)
        if a.cmd == "scan":
            repo, vault = a.repo.resolve(), a.vault.resolve()
            index = load_index(a.index, vault)
            upstream = git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False).strip()
            files = (git(repo, "ls-tree", "-r", "--name-only", a.rev).splitlines() if a.all or not upstream
                     else changed_files(repo, a.rev, git(repo, "rev-parse", upstream).strip()))
            url = git(repo, "remote", "get-url", upstream.split("/")[0], check=False).strip() if upstream else None
            res = scan(repo, vault, a.project_dir.resolve(), index, a.rev, [f for f in files if f], url or None)
            print(json.dumps(res, ensure_ascii=False))
            return 0 if res["ok"] else 3
        out = cmd_install(a) if a.cmd == "install" else cmd_allow(a)
        print(json.dumps(out, ensure_ascii=False))
        return 0
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
