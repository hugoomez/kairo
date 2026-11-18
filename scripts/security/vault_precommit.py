#!/usr/bin/env python3
"""The vault's pre-commit backstop: no private material is committed in clear.

    vault_precommit.py run --vault V
        What the git hook runs, from the top of the worktree being committed;
        when that repository is not the vault's (another git common dir: a hook
        shared through a global core.hooksPath), it does nothing. In any worktree
        of the vault's repository (linked ones share its hooks) the vault's place
        is carried over to that worktree's top; when it cannot be, exit 1. For
        every staged file (added, copied,
        modified, renamed or changed in type; renames are seen as new files)
        inside the vault, not under Papers/, text and at most 5 MB: if the STAGED
        content holds private material (redact.has_secret), the working file is
        redacted in place, staged again, and stderr says
        «Kairo: redactado <path> (<n>)». A file whose working copy differs from
        what is staged (partial staging) is not touched: the commit is refused.
        Under `git commit -- <paths>` the redacted file is staged in the index git
        installs after the commit too, not only in its temporary one.
        exit 0 commit goes on · exit 1 commit refused (one-line reason on stderr)

    vault_precommit.py install --vault V [--python P]
        Writes the vault repository's pre-commit hook (core.hooksPath respected), a
        POSIX sh script marked `# kairo-vault-precommit` that runs `run` with the
        given Python (default: this one; if it is gone the hook says so and blocks
        the commit). A core.hooksPath that does not come from the vault's local
        config (global or system, shared with other repositories): exit 3, nothing
        written. Ours already there: rewritten. Someone
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


REPO_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX",
            "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES")


def own_repo_env(**extra: str) -> dict:
    """The environment without what a running git (the hook's caller) says about
    its repository and index, so a git call sees only the folder it runs in."""
    env = {k: v for k, v in os.environ.items() if k not in REPO_ENV}
    env.update(extra)
    return env


def git(cwd: Path, *args: str, env: dict | None = None) -> bytes:
    r = subprocess.run(["git", "--literal-pathspecs", *args], cwd=cwd, capture_output=True, env=env)
    if r.returncode != 0:
        why = r.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise Refused(f"git {args[0]} falló: {why[-1] if why else f'exit {r.returncode}'}")
    return r.stdout


def git_path(cwd: Path, *args: str, env: dict | None = None) -> Path:
    out = Path(git(cwd, *args, env=env).decode("utf-8").strip())
    return (out if out.is_absolute() else cwd / out).resolve()


def same(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------

def real_index_env(cwd: Path) -> dict | None:
    """`git commit -- <paths>` (and `--only`) hands the hook a temporary index
    (next-index-*.lock) and keeps the index git will install afterwards in
    <git dir>/index.lock. The environment that reaches that real index, or None
    when the hook's index already is it (`git commit`, `git commit -a`)."""
    current = os.environ.get("GIT_INDEX_FILE")
    if not current:
        return None
    real = git_path(cwd, "rev-parse", "--git-path", "index", env=own_repo_env())
    cur = Path(current) if Path(current).is_absolute() else cwd / current
    lock = real.with_name(real.name + ".lock")
    if same(cur, real) or same(cur, lock):
        return None
    if lock.exists():
        return own_repo_env(GIT_INDEX_FILE=str(lock))
    return own_repo_env()


def vault_in_committing_worktree(vault: Path, cwd: Path) -> Path:
    """The vault folder as it sits in the worktree being committed: the vault's
    place inside its own worktree (its top, or a subfolder), carried over to the
    committing worktree's top. Refused when that place cannot be worked out."""
    unmapped = (f"no sé dónde queda el vault {vault.as_posix()} en el repositorio que se está "
                "confirmando; revisa --vault en el hook (vault_precommit.py install)")
    try:
        own_top = git_path(vault, "rev-parse", "--show-toplevel", env=own_repo_env())
        top = git_path(cwd, "rev-parse", "--show-toplevel")
        inside = vault.relative_to(own_top)
    except (Refused, ValueError):
        raise Refused(unmapped)
    if ".." in inside.parts:
        raise Refused(unmapped)
    return (top / inside).resolve()


def run(vault: Path) -> int:
    vault = vault.resolve()
    cwd = Path.cwd()
    # The hook runs from the top of the worktree being committed. A hook reached
    # through a shared core.hooksPath in another repository has nothing to check;
    # every worktree of the vault's repository (they share its hooks) is checked.
    try:
        committing = git_path(cwd, "rev-parse", "--git-common-dir")
    except Refused:
        return EXIT_OK
    if not same(committing, git_path(vault, "rev-parse", "--git-common-dir", env=own_repo_env())):
        return EXIT_OK
    vault = vault_in_committing_worktree(vault, cwd)
    top = git_path(cwd, "rev-parse", "--show-toplevel")
    real_env = real_index_env(top)
    names = [n for n in git(top, "diff", "--cached", "--name-only", "--no-renames", "--diff-filter=ACMRT", "-z")
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
        if real_env is not None:  # the index left after the commit must not keep the material either
            git(top, "add", "--", name, env=real_env)
        again = redact.decode_text(git(top, "cat-file", "blob", ":" + name))
        if n == 0 or again is None or redact.has_secret(again):
            raise Refused(f"{name}: contiene material privado que no se pudo redactar; redáctalo a mano")
        print(f"Kairo: redactado {name} ({n})", file=sys.stderr)
    return EXIT_OK


# --------------------------------------------------------------------------
# install / status
# --------------------------------------------------------------------------

def hook_path(vault: Path) -> Path:
    return git_path(vault, "rev-parse", "--git-path", "hooks", env=own_repo_env()) / "pre-commit"


def config_value(vault: Path, *args: str) -> str | None:
    """`git config <args>`, None when the key is not set (git's exit 1)."""
    r = subprocess.run(["git", "config", *args], cwd=vault, capture_output=True, env=own_repo_env())
    if r.returncode == 1:
        return None
    if r.returncode != 0:
        why = r.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise Refused(f"git config falló: {why[-1] if why else f'exit {r.returncode}'}")
    return r.stdout.decode("utf-8").strip()


def shared_hooks_path(vault: Path) -> str | None:
    """The effective core.hooksPath when it does not come from the vault's own
    (local) config: a global or system hooks folder that other repositories share."""
    effective = config_value(vault, "--get", "core.hooksPath")
    if effective is None:
        return None
    return None if config_value(vault, "--local", "--get", "core.hooksPath") == effective else effective


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
            f"PY={sh_quote(Path(python).as_posix())}\n"
            'if ! command -v "$PY" >/dev/null 2>&1; then\n'
            '  echo "Kairo: no encuentro Python en $PY; vuelve a ejecutar vault_precommit.py install" >&2\n'
            "  exit 1\n"
            "fi\n"
            f'exec "$PY" {sh_quote(script)} run --vault {sh_quote(vault.as_posix())}\n')


def read_hook(path: Path) -> str | None:
    try:
        return path.read_bytes().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return None


def install(vault: Path, python: str) -> int:
    vault = vault.resolve()
    shared = shared_hooks_path(vault)
    if shared is not None:
        print(json.dumps({"error": "core.hooksPath viene de la configuración global o del sistema y lo comparten "
                                   "otros repositorios; define uno propio del vault (git config --local "
                                   "core.hooksPath .git/hooks) y vuelve a instalar",
                          "hooksPath": shared}, ensure_ascii=False))
        return EXIT_REFUSED
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
