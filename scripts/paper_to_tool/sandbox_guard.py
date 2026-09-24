#!/usr/bin/env python3
"""Guard rails for running third-party paper code in `paper-to-tool`.

This is NOT an operating-system sandbox. A process started here runs with the
researcher's user permissions and, on a normal Windows/macOS/Linux account,
CAN read any file that user can -- the vault included. What this script
enforces is narrower and mechanical:

  1. Location. The sandbox directory must be outside the vault AND outside the
     vault's git work tree (the vault may sit inside a larger repo); the vault
     must not be inside the sandbox either. Every run's working directory is
     the sandbox (or a subdirectory of it).
  2. Approval. `run` executes only commands listed verbatim in the approved
     plan file -- the exact argv the researcher saw and approved before
     anything executed. Anything else is refused; a new command needs a new
     approval, never an ad-hoc run.
  3. No vault paths. Any argument that resolves inside the vault's git work
     tree (e.g. a `Papers/` or `Projects/` path) is refused, so the approved
     commands cannot be pointed at vault content.
  4. No credentials. The child environment drops variables that look like
     credentials (…TOKEN, …KEY, …SECRET, …PASSWORD, and the ANTHROPIC_/
     CLAUDE_/DEEPINFRA_/GITHUB_/GH_/HF_/AWS_/AZURE_/GOOGLE_/OPENAI_ families),
     sets PYTHONNOUSERSITE=1 and PIP_REQUIRE_VIRTUALENV=1.
  5. Record. Every run appends {argv, cwd, exit code, wall time, log path} to
     `<sandbox>/runs.jsonl` and streams stdout+stderr to a per-run log.

Stronger isolation (a container with no network and no vault mount, e.g.
`docker run --network none -v <sandbox>:/work ...`) is recommended whenever it
is available, and the approval step must say which of the two is in use.

Usage:
    python sandbox_guard.py check --sandbox <dir> --vault <vault_dir>
    python sandbox_guard.py run --sandbox <dir> --vault <vault_dir> \\
        --plan <sandbox>/approved-commands.json [--cwd <subdir>] \\
        [--env KEY=VALUE ...] [--timeout SECONDS] [--stdout FILE] -- <argv...>

Plan file (written when the researcher approves, never edited afterwards):
    {"approved_at": "2026-09-24T15:00:00Z", "approved_by": "<researcher>",
     "commands": [["git", "clone", "--no-checkout", "<url>", "repo"], ...]}

Exit codes: `check` 0 ok / 1 refused / 2 invalid input;
            `run` = the child's exit code, or 90 refused / 2 invalid input.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

__version__ = "1.0.0"
REFUSED = 90

_CRED_NAME = re.compile(r"(TOKEN|_KEY$|^KEY$|APIKEY|API_KEY|SECRET|PASSWORD|PASSWD|CREDENTIAL|COOKIE|SESSION)", re.I)
_CRED_PREFIX = ("ANTHROPIC_", "CLAUDE_", "DEEPINFRA_", "GITHUB_", "GH_", "HF_", "HUGGINGFACE",
                "AWS_", "AZURE_", "GOOGLE_", "OPENAI_", "WANDB_API", "SEMANTIC_SCHOLAR", "PATENTSVIEW", "ZOTERO")


def git_toplevel(path: Path) -> Path | None:
    try:
        r = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return Path(r.stdout.strip()).resolve() if r.returncode == 0 and r.stdout.strip() else None


def protected_root(vault: Path) -> Path:
    """The vault's git work tree if it has one (it may be larger than the vault), else the vault."""
    return git_toplevel(vault) or vault.resolve()


def is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def location_problems(sandbox: Path, vault: Path) -> list[str]:
    probs = []
    root = protected_root(vault)
    if is_within(sandbox, vault):
        probs.append(f"sandbox {sandbox} is inside the vault {vault}")
    elif is_within(sandbox, root):
        probs.append(f"sandbox {sandbox} is inside the vault's git work tree {root}")
    if is_within(vault, sandbox):
        probs.append(f"the vault {vault} is inside the sandbox {sandbox}")
    return probs


def scrubbed_env(extra: list[str]) -> tuple[dict[str, str], list[str]]:
    env, dropped = {}, []
    for k, v in os.environ.items():
        if _CRED_NAME.search(k) or k.upper().startswith(_CRED_PREFIX):
            dropped.append(k)
        else:
            env[k] = v
    env["PYTHONNOUSERSITE"] = "1"
    env["PIP_REQUIRE_VIRTUALENV"] = "1"
    for kv in extra:
        k, _, v = kv.partition("=")
        if not k or _CRED_NAME.search(k) or k.upper().startswith(_CRED_PREFIX):
            raise ValueError(f"--env {k!r} looks like a credential and is not allowed")
        env[k] = v
    return env, sorted(dropped)


def vault_path_args(argv: list[str], root: Path, cwd: Path) -> list[str]:
    bad = []
    for arg in argv:
        for piece in re.split(r"[=,;]", arg):
            if not piece or piece.startswith("-") and "=" not in arg:
                continue
            if any(sep in piece for sep in ("/", "\\")) or piece.startswith("."):
                cand = Path(piece)
                cand = cand if cand.is_absolute() else (cwd / cand)
                if is_within(cand, root):
                    bad.append(arg)
                    break
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "run"):
        s = sub.add_parser(name)
        s.add_argument("--sandbox", type=Path, required=True)
        s.add_argument("--vault", type=Path, required=True)
        if name == "run":
            s.add_argument("--plan", type=Path, required=True)
            s.add_argument("--cwd", type=Path, help="subdirectory of the sandbox to run in")
            s.add_argument("--env", nargs="*", default=[], metavar="KEY=VALUE")
            s.add_argument("--timeout", type=float, default=None)
            s.add_argument("--stdout", type=Path, help="write the child's stdout to this file (inside the sandbox) instead of the log")
            s.add_argument("child", nargs=argparse.REMAINDER)
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args(argv)

    if not a.vault.is_dir():
        print(f"error: vault {a.vault} is not a directory", file=sys.stderr)
        return 2
    a.sandbox.mkdir(parents=True, exist_ok=True)
    probs = location_problems(a.sandbox, a.vault)

    if a.cmd == "check":
        root = protected_root(a.vault)
        if probs:
            for p in probs:
                print(f"REFUSED: {p}")
            return 1
        print(f"OK: sandbox {a.sandbox.resolve()} is outside the vault's git work tree {root}")
        return 0

    if probs:
        for p in probs:
            print(f"REFUSED: {p}", file=sys.stderr)
        return REFUSED
    child = a.child[1:] if a.child[:1] == ["--"] else a.child
    if not child:
        print("error: no command given after --", file=sys.stderr)
        return 2
    try:
        plan = json.loads(a.plan.read_text(encoding="utf-8"))
        approved = [list(c) for c in plan["commands"]]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as e:
        print(f"error: unreadable plan {a.plan}: {e}", file=sys.stderr)
        return 2
    if child not in approved:
        print("REFUSED: command is not in the approved plan (exact argv match required):", file=sys.stderr)
        print("  " + json.dumps(child), file=sys.stderr)
        return REFUSED
    cwd = (a.sandbox / a.cwd) if a.cwd and not a.cwd.is_absolute() else (a.cwd or a.sandbox)
    if not is_within(cwd, a.sandbox):
        print(f"REFUSED: --cwd {cwd} is outside the sandbox", file=sys.stderr)
        return REFUSED
    bad = vault_path_args(child, protected_root(a.vault), cwd)
    if bad:
        print(f"REFUSED: argument(s) resolve inside the vault's git work tree: {bad}", file=sys.stderr)
        return REFUSED
    try:
        env, dropped = scrubbed_env(a.env)
    except ValueError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return REFUSED

    out_file = None
    if a.stdout is not None:
        out_file = a.stdout if a.stdout.is_absolute() else cwd / a.stdout
        if not is_within(out_file, a.sandbox):
            print(f"REFUSED: --stdout {out_file} is outside the sandbox", file=sys.stderr)
            return REFUSED
    cwd.mkdir(parents=True, exist_ok=True)
    logs = a.sandbox / "logs"
    logs.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = logs / f"run-{stamp}-{os.getpid()}.log"
    t0 = time.monotonic()
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        log.write(f"$ {json.dumps(child)}\n# cwd={cwd.resolve()}\n# dropped env: {dropped}\n")
        log.flush()
        try:
            if out_file is not None:
                out_file.parent.mkdir(parents=True, exist_ok=True)
                with out_file.open("wb") as fo:
                    rc = subprocess.run(child, cwd=cwd, env=env, stdout=fo, stderr=log,
                                        timeout=a.timeout).returncode
            else:
                rc = subprocess.run(child, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                    timeout=a.timeout).returncode
        except subprocess.TimeoutExpired:
            rc = 124
            log.write(f"\n# TIMEOUT after {a.timeout} s\n")
        except OSError as e:
            rc = 127
            log.write(f"\n# could not start: {e}\n")
    wall = time.monotonic() - t0
    rec = {"ts": stamp, "argv": child, "cwd": str(cwd.resolve()), "exit": rc,
           "wall_s": round(wall, 3), "log": str(log_path.resolve()), "env_dropped": dropped}
    with (a.sandbox / "runs.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps({k: rec[k] for k in ("exit", "wall_s", "log")}))
    return rc


if __name__ == "__main__":
    sys.exit(main())
