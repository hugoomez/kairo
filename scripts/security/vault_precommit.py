#!/usr/bin/env python3
"""The vault's pre-commit backstop: no private material is committed in clear.

    vault_precommit.py run --vault V
        What the git hook runs. For every staged file (added, copied or modified)
        inside the vault, not under Papers/, text and at most 5 MB: if the STAGED
        content holds private material (redact.has_secret), the working file is
        redacted in place, staged again, and stderr says
        «Kairo: redactado <path> (<n>)». A file whose working copy differs from
        what is staged (partial staging) is not touched: the commit is refused.
        exit 0 commit goes on · exit 1 commit refused (one-line reason on stderr)

    vault_precommit.py install --vault V [--python P]
        Writes the vault repository's pre-commit hook (core.hooksPath respected), a
        POSIX sh script marked `# kairo-vault-precommit` that runs `run` with the
        given Python (default: this one). Ours already there: rewritten. Someone
        else's: exit 3 {"error": "ya hay un pre-commit ajeno", "path": …}, nothing
        changed. stdout {"installed": path}.

    vault_precommit.py status --vault V
        stdout {"installed": bool, "foreign": bool, "path": …}

The post-write hook (kairo_hook.py) already redacts what agents write through the
editing tools; this catches the rest (a script, a shell redirect, a copy). Paths
and counts are printed, never the material. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import redact  # noqa: E402

EXIT_OK, EXIT_ERROR, EXIT_REFUSED = 0, 1, 3
MARKER = "# kairo-vault-precommit"


class Refused(Exception):
    """The commit must not go on; the message is the one line to print."""


def git(cwd: Path, *args: str) -> bytes:
    r = subprocess.run(["git", "--literal-pathspecs", *args], cwd=cwd, capture_output=True)
    if r.returncode != 0:
        why = r.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise Refused(f"git {args[0]} falló: {why[-1] if why else f'exit {r.returncode}'}")
    return r.stdout


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------

def run(vault: Path) -> int:
    vault = vault.resolve()
    top = Path(git(vault, "rev-parse", "--show-toplevel").decode("utf-8").strip()).resolve()
    names = [n for n in git(top, "diff", "--cached", "--name-only", "--diff-filter=ACM", "-z")
             .decode("utf-8").split("\0") if n]
    for name in names:
        path = top / name
        try:
            rel = path.relative_to(vault).as_posix()
        except ValueError:
            continue                                  # outside the vault
        if os.path.normcase(rel.split("/")[0]) == os.path.normcase("Papers") or path.is_symlink():
            continue
        if int(git(top, "cat-file", "-s", ":" + name).strip() or 0) > redact.MAX_FILE_BYTES:
            continue
        text = redact.decode_text(git(top, "cat-file", "blob", ":" + name))
        if text is None or not redact.has_secret(text):
            continue
        staged_oid = git(top, "rev-parse", ":" + name).strip()
        if not path.is_file():
            raise Refused(f"{name}: contiene material privado y tiene cambios sin preparar; redáctalo a mano")
        work_oid = git(top, "hash-object", f"--path={name}", "--", name).strip()
        if work_oid != staged_oid:
            raise Refused(f"{name}: contiene material privado y tiene cambios sin preparar; redáctalo a mano")
        n = redact.redact_file(path)
        git(top, "add", "--", name)
        again = redact.decode_text(git(top, "cat-file", "blob", ":" + name))
        if n == 0 or again is None or redact.has_secret(again):
            raise Refused(f"{name}: contiene material privado que no se pudo redactar; redáctalo a mano")
        print(f"Kairo: redactado {name} ({n})", file=sys.stderr)
    return EXIT_OK


# --------------------------------------------------------------------------
# install / status
# --------------------------------------------------------------------------

def hook_path(vault: Path) -> Path:
    hooks = Path(git(vault, "rev-parse", "--git-path", "hooks").decode("utf-8").strip())
    return (hooks if hooks.is_absolute() else vault / hooks).resolve() / "pre-commit"


def sh_quote(s: str) -> str:
    """Inside double quotes for POSIX sh."""
    for ch in ("\\", '"', "$", "`"):
        s = s.replace(ch, "\\" + ch)
    return '"' + s + '"'


def hook_text(python: str, vault: Path) -> str:
    script = Path(__file__).resolve().as_posix()
    return ("#!/bin/sh\n"
            f"{MARKER}\n"
            "# Kairo: redacta material privado antes de cada commit del vault (no lo edites a mano;\n"
            "# se reescribe con vault_precommit.py install).\n"
            f"exec {sh_quote(Path(python).as_posix())} {sh_quote(script)} run --vault {sh_quote(vault.as_posix())}\n")


def read_hook(path: Path) -> str | None:
    try:
        return path.read_bytes().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return None


def install(vault: Path, python: str) -> int:
    vault = vault.resolve()
    path = hook_path(vault)
    current = read_hook(path)
    if current is not None and MARKER not in current.splitlines():
        print(json.dumps({"error": "ya hay un pre-commit ajeno", "path": str(path)}, ensure_ascii=False))
        return EXIT_REFUSED
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".pre-commit.kairo.tmp")
    tmp.write_bytes(hook_text(python, vault).encode("utf-8"))
    os.chmod(tmp, 0o755)
    os.replace(tmp, path)
    print(json.dumps({"installed": str(path)}, ensure_ascii=False))
    return EXIT_OK


def status(vault: Path) -> int:
    path = hook_path(vault.resolve())
    current = read_hook(path)
    ours = current is not None and MARKER in current.splitlines()
    print(json.dumps({"installed": ours, "foreign": current is not None and not ours, "path": str(path)},
                     ensure_ascii=False))
    return EXIT_OK


# --------------------------------------------------------------------------

class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        self.print_usage(sys.stderr)
        print(f"error: {message}", file=sys.stderr)
        sys.exit(EXIT_ERROR)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = _Parser(prog="vault_precommit.py")
    sub = ap.add_subparsers(dest="cmd", required=True, parser_class=_Parser)
    for name in ("run", "install", "status"):
        p = sub.add_parser(name)
        p.add_argument("--vault", required=True)
        if name == "install":
            p.add_argument("--python", default=sys.executable)
    a = ap.parse_args(argv)
    vault = Path(a.vault)
    try:
        if not vault.is_dir():
            raise Refused(f"no existe la carpeta del vault {vault}; revisa --vault")
        if a.cmd == "run":
            return run(vault)
        if a.cmd == "install":
            return install(vault, a.python)
        return status(vault)
    except Refused as exc:
        print(f"Kairo: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # any error blocks the commit, with one line
        print(f"Kairo: el pre-commit del vault falló ({type(exc).__name__}: {exc}); "
              "corrígelo o redacta el fichero a mano", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
