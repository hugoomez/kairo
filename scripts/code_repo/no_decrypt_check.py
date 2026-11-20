#!/usr/bin/env python3
"""Static «nunca descifrar» check for a measurement project's code repository.

    no_decrypt_check.py --repo R [--allow FILE] [--format json|md]

A study of encrypted secrets that others published promises, in its ethics plan, to
never decrypt anything and never use a key it finds. This check looks for code that
would break that promise: it scans every file git tracks (`git ls-files`, the working
tree's current content; binaries and files over 2 MiB are skipped and counted) line by
line for

  CLI invocations   `sops -d|--decrypt|decrypt|exec-env|exec-file`, `age -d|--decrypt`,
                    `rage -d|--decrypt`, `agenix -d|--decrypt`, `kubeseal
                    --recovery-unseal|--recovery-private-key`, `dotenvx decrypt|get|run`,
                    `git-crypt unlock`. The same patterns run a second time on the line
                    with quotes, commas and brackets turned into spaces, so an argument
                    list (`subprocess.run(["sops", "-d", f])`, `execFile('age', ['-d'])`)
                    counts as the command it runs.
  library calls     pyrage / python `age` decryption (`pyrage.decrypt(`,
                    `pyrage.passphrase.decrypt(`, `from age… import Decryptor`),
                    `ecies.decrypt(`, PyNaCl `Box(…).decrypt(` / `SealedBox(…).decrypt(`
                    and a `PrivateKey(<bytes>)` built from existing material, Go sops
                    `decrypt.File(` / `decrypt.Data(`, JS age `new Decrypter(`; and, in a
                    file that loads a private key (`load_pem_private_key`,
                    `load_der_private_key`, `load_ssh_private_key`,
                    `X25519PrivateKey.from_private_bytes`, `Ed25519PrivateKey.from_private_bytes`,
                    `nacl.public.PrivateKey(`), every `.decrypt(` and `.exchange(` call.

Each hit is `{path, line, rule, text}`; `text` is the line, trimmed to 160 characters
and passed through redact.py (a key written on the same line never reaches the report).

The allow-list (`--allow FILE`, default `<repo>/.kairo/no-descifrar.json` when it
exists) is a JSON array of `{path, rule?, line?, justificacion}`: `path` is a
repository-relative path or an fnmatch pattern (`docs/*.md`); `rule` and `line` narrow
it; `justificacion` (at least 15 characters) says why that hit does not decrypt
anything (a README line that forbids the command, a test asserting the check fires).
An entry without a justification makes the allow-list invalid (exit 1). Allowed hits
are reported apart, with their justification; unused entries are listed too.

Patterns, not proof: a decryption assembled at run time (a command built from pieces,
a library this list does not know, an argument list split across lines) is not seen.
Exit codes: 0 no hit · 4 at least one hit not allowed · 1 error (no git repository,
unreadable or invalid allow-list). Standard library only.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "security"))
import redact  # noqa: E402

__version__ = "1.0.0"
TOOL = f"kairo/no_decrypt_check@{__version__}"
DEFAULT_ALLOW = Path(".kairo") / "no-descifrar.json"
MAX_BYTES = 2 * 1024 * 1024
MIN_JUSTIFICATION = 15

_B = r"(?<![\w./-])"                    # start of a command word
_E = r"(?![\w-])"                       # end of a flag / subcommand
_ARGS = r"(?:\s+[^\s;|&]+)*?\s+"        # other arguments, same command segment

CLI_RULES: list[tuple[str, re.Pattern]] = [
    ("sops_decrypt", re.compile(_B + r"sops(?:\.exe)?" + _ARGS + r"(?:-d|--decrypt|decrypt|exec-env|exec-file)" + _E)),
    ("age_decrypt", re.compile(_B + r"r?age(?:\.exe)?" + _ARGS + r"(?:-d|--decrypt)" + _E)),
    ("agenix_decrypt", re.compile(_B + r"agenix(?:\.exe)?" + _ARGS + r"(?:-d|--decrypt)" + _E)),
    ("kubeseal_recovery", re.compile(_B + r"kubeseal(?:\.exe)?" + _ARGS + r"--recovery-(?:unseal|private-key)" + _E)),
    ("dotenvx_decrypt", re.compile(_B + r"dotenvx(?:\.exe)?" + _ARGS + r"(?:decrypt|get|run)" + _E)),
    ("git_crypt_unlock", re.compile(_B + r"git-crypt(?:\.exe)?" + _ARGS + r"unlock" + _E)),
]

LIB_RULES: list[tuple[str, re.Pattern]] = [
    ("pyrage_decrypt", re.compile(r"\bpyrage(?:\.\w+)*\.decrypt\w*\s*\(")),
    ("pyrage_decrypt", re.compile(r"\bfrom\s+pyrage(?:\.\w+)*\s+import\b[^#\n]*\bdecrypt\w*")),
    ("age_lib_decrypt", re.compile(r"\bfrom\s+age(?:\.\w+)*\s+import\b[^#\n]*\b(?:Decryptor|decrypt\w*)")),
    ("age_lib_decrypt", re.compile(r"(?<![\w.])age(?:\.\w+)*\.(?:Decryptor|decrypt\w*)\s*\(")),
    ("ecies_decrypt", re.compile(r"\becies\.decrypt\s*\(|\bfrom\s+ecies\s+import\b[^#\n]*\bdecrypt\b")),
    ("nacl_box_decrypt", re.compile(r"\b(?:Sealed)?Box\s*\([^)]*\)\s*\.decrypt\s*\(")),
    ("nacl_private_key", re.compile(r"\bPrivateKey\s*\(\s*(?!\))")),
    ("go_sops_decrypt", re.compile(r"\bdecrypt\.(?:File|Data|DataWithFormat)\s*\(")),
    ("js_age_decrypter", re.compile(r"\bnew\s+Decrypter\s*\(")),
]

_KEY_LOADER = re.compile(r"\bload_(?:pem|der|ssh)_private_key\s*\(|\b(?:X25519|X448|Ed25519|Ed448)PrivateKey"
                         r"\.from_private_bytes\s*\(|\bnacl\.public\.PrivateKey\s*\(|\bfrom\s+nacl\.public\s+import\b")
_KEY_USE = re.compile(r"\.(?:decrypt|exchange)\s*\(")
_ARGV_PUNCT = re.compile(r"[\"'`\[\](),{}]")


class InputError(Exception):
    pass


def tracked(repo: Path) -> list[str]:
    r = subprocess.run(["git", "ls-files", "-z"], cwd=repo, capture_output=True)
    if r.returncode != 0:
        raise InputError(f"{repo}: no es un repositorio git (git ls-files falló)")
    return [p for p in r.stdout.decode("utf-8", "replace").split("\0") if p]


def head(repo: Path) -> str:
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def _snippet(line: str) -> str:
    s = line.strip()
    s = s if len(s) <= 160 else s[:159] + "…"
    return redact.redact_text(s)[0]


def scan_text(text: str) -> list[tuple[int, str]]:
    """(line number, rule) for every hit in one file's text; one hit per (line, rule)."""
    hits: set[tuple[int, str]] = set()
    lines = text.splitlines()
    loads_key = bool(_KEY_LOADER.search(text))
    for i, line in enumerate(lines, 1):
        flat = _ARGV_PUNCT.sub(" ", line)
        for rule, rx in CLI_RULES:
            if rx.search(line) or rx.search(flat):
                hits.add((i, rule))
        for rule, rx in LIB_RULES:
            if rx.search(line):
                hits.add((i, rule))
        if loads_key and _KEY_USE.search(line):
            hits.add((i, "private_key_decrypt"))
    return sorted(hits)


def load_allow(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        raise InputError(f"lista de permitidos ilegible: {path}") from None
    if not isinstance(data, list):
        raise InputError("la lista de permitidos debe ser un array JSON")
    out = []
    for i, e in enumerate(data):
        if not isinstance(e, dict) or not isinstance(e.get("path"), str) or not e["path"].strip():
            raise InputError(f"permitidos[{i}]: falta `path`")
        just = e.get("justificacion")
        if not isinstance(just, str) or len(just.strip()) < MIN_JUSTIFICATION:
            raise InputError(f"permitidos[{i}] ({e['path']}): falta `justificacion` (al menos {MIN_JUSTIFICATION} caracteres)")
        if "line" in e and not isinstance(e["line"], int):
            raise InputError(f"permitidos[{i}]: `line` debe ser un entero")
        out.append({"path": e["path"].strip().replace("\\", "/"), "rule": e.get("rule"), "line": e.get("line"),
                    "justificacion": " ".join(just.split()), "used": False})
    return out


def _allowed(hit: dict, allow: list[dict]) -> dict | None:
    for e in allow:
        if not (hit["path"] == e["path"] or fnmatch.fnmatchcase(hit["path"], e["path"])):
            continue
        if e["rule"] and e["rule"] != hit["rule"]:
            continue
        if e["line"] is not None and e["line"] != hit["line"]:
            continue
        e["used"] = True
        return e
    return None


def run(repo: Path, allow_file: Path | None = None) -> dict:
    repo = Path(repo)
    if not repo.is_dir():
        raise InputError(f"no existe el repositorio {repo}")
    files = tracked(repo)
    if allow_file is None and (repo / DEFAULT_ALLOW).is_file():
        allow_file = repo / DEFAULT_ALLOW
    allow = load_allow(allow_file) if allow_file else []
    hits, allowed = [], []
    skipped = scanned = 0
    for rel in files:
        p = repo / rel
        try:
            if not p.is_file() or p.stat().st_size > MAX_BYTES:
                skipped += 1
                continue
            raw = p.read_bytes()
        except OSError:
            skipped += 1
            continue
        text = redact.decode_text(raw, MAX_BYTES)
        if text is None:
            skipped += 1
            continue
        scanned += 1
        lines = text.splitlines()
        for ln, rule in scan_text(text):
            hit = {"path": rel, "line": ln, "rule": rule, "text": _snippet(lines[ln - 1])}
            e = _allowed(hit, allow)
            if e:
                allowed.append({**hit, "justificacion": e["justificacion"]})
            else:
                hits.append(hit)
    return {"tool": TOOL, "repo": str(repo), "head": head(repo), "status": "encontrado" if hits else "limpio",
            "files_tracked": len(files), "files_scanned": scanned, "files_skipped": skipped,
            "allow_file": str(allow_file) if allow_file else None, "hits": hits, "allowed": allowed,
            "allow_unused": [{"path": e["path"], "rule": e["rule"], "line": e["line"]} for e in allow if not e["used"]]}


def markdown(res: dict) -> str:
    """The section repo-steward puts in _repo-health.md."""
    out = ["## Nunca descifrar", "",
           f"`no_decrypt_check.py` sobre HEAD `{res['head'][:10] or '?'}`: **{res['status']}** — "
           f"{len(res['hits'])} llamada(s) que descifran o usan claves, {len(res['allowed'])} permitida(s); "
           f"{res['files_scanned']} de {res['files_tracked']} ficheros versionados leídos.", ""]
    if res["hits"]:
        out += ["| fichero | línea | regla | texto |", "|---|---|---|---|"]
        out += [f"| `{h['path']}` | {h['line']} | {h['rule']} | `{h['text'].replace('|', '/').replace('`', '')}` |"
                for h in res["hits"]]
        out.append("")
    if res["allowed"]:
        out += ["Permitidas:", ""]
        out += [f"- `{h['path']}`:{h['line']} ({h['rule']}) — {h['justificacion']}" for h in res["allowed"]]
        out.append("")
    if res["allow_unused"]:
        out += [f"Entradas de la lista de permitidos sin uso: {len(res['allow_unused'])} (quítalas)", ""]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="no_decrypt_check.py", description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--allow")
    ap.add_argument("--format", choices=("json", "md"), default="json")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code else 0
    try:
        res = run(Path(a.repo), Path(a.allow) if a.allow else None)
    except InputError as exc:
        print(f"no_decrypt_check: {exc}", file=sys.stderr)
        return 1
    print(markdown(res) if a.format == "md" else json.dumps(res, ensure_ascii=False, indent=2))
    return 4 if res["hits"] else 0


if __name__ == "__main__":
    sys.exit(main())
