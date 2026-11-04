#!/usr/bin/env python3
"""Record the state of a project's linked code repository, and run experiments
in throw-away worktrees of it.

    repo_sync.py sync --repo R --project-dir D [--timeout 15] [--force]
        write Projects/<slug>/Repositorio/estado.json: the remote, the PRs, the
        main branch, and the local work (branches with unpushed commits, the
        NAMES of modified and untracked files — never diffs or contents).
        Skipped (`{"skipped": true}`) inside an agent session unless --force
        (the backend passes it). Never fails with a traceback: any failure
        still writes the file, keeping the last good data, ok=false, errors,
        and stale_since = the last successful synced_at.
    repo_sync.py commit-status --repo R --commit SHA
        {"on_remote": bool, "branches": [...]}: is the commit on any remote branch?
    repo_sync.py worktree add    --repo R --commit SHA --name E-9002
    repo_sync.py worktree remove --repo R --path P
        a detached worktree under $KAIRO_WORKTREE_DIR (default
        ~/.kairo/worktrees)/<repo name>/<name>, outside the vault and the clone.

A sync only reads GitHub and only runs `git fetch origin --prune`: it never
changes the working copy, the current branch or the index.

Exit codes: 0 ok · 3 refused · 1 error. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import repo_guard  # noqa: E402

SCHEMA = 1
GITIGNORE = "estado.json\ncambios.json\n"
PR_FIELDS = "number,title,author,createdAt,mergedAt,closedAt,state,headRefName,baseRefName,body,mergeCommit,url"
PR_LIMIT = 1000


class Refused(Exception):
    pass


class StepError(Exception):
    pass


# --------------------------------------------------------------------------
# Running commands under one shared deadline
# --------------------------------------------------------------------------

class Budget:
    def __init__(self, seconds: float):
        self.end = time.monotonic() + seconds

    def run(self, argv: list[str], cwd: Path, label: str) -> str:
        left = self.end - time.monotonic()
        if left <= 0:
            raise StepError(f"{label}: timed out")
        try:
            r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=left)
        except subprocess.TimeoutExpired:
            raise StepError(f"{label}: timed out") from None
        except OSError as exc:
            raise StepError(f"{label}: {exc}") from None
        if r.returncode != 0:
            raise StepError(f"{label}: {(r.stderr or r.stdout).strip()[:300] or 'exit ' + str(r.returncode)}")
        return r.stdout


def git_out(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "--no-optional-locks", *args], cwd=repo, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout if r.returncode == 0 else ""


# --------------------------------------------------------------------------
# Local facts (read-only)
# --------------------------------------------------------------------------

def local_state(repo: Path) -> dict:
    branch = git_out(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    head = git_out(repo, "rev-parse", "HEAD").strip()
    unpushed = []
    for name in git_out(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads").split():
        n = git_out(repo, "rev-list", "--count", f"refs/heads/{name}", "--not", "--remotes").strip()
        if n.isdigit() and int(n) > 0:
            unpushed.append({"name": name, "ahead": int(n)})
    modified, untracked = [], []
    parts = git_out(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").split("\0")
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        xy, name = entry[:2], entry[3:]
        if xy[0] in "RC":
            i += 1  # the original name follows as its own entry
        (untracked if xy == "??" else modified).append(name)
    return {"branch": branch, "head": head, "unpushed_branches": unpushed,
            "modified": sorted(modified), "untracked": sorted(untracked)}


def map_pr(p: dict) -> dict:
    def s(v):
        return v if isinstance(v, str) else ""
    author = p.get("author") or {}
    merge = p.get("mergeCommit") or {}
    return {"number": p.get("number"), "title": s(p.get("title")),
            "author": s(author.get("login")) if isinstance(author, dict) else "",
            "created_at": s(p.get("createdAt")), "merged_at": s(p.get("mergedAt")),
            "closed_at": s(p.get("closedAt")), "state": s(p.get("state")),
            "branch": s(p.get("headRefName")), "base": s(p.get("baseRefName")), "body": s(p.get("body")),
            "merge_commit": s(merge.get("oid")) if isinstance(merge, dict) else "", "url": s(p.get("url"))}


# --------------------------------------------------------------------------
# sync
# --------------------------------------------------------------------------

def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_state(project_dir: Path, state: dict) -> None:
    d = project_dir / "Repositorio"
    d.mkdir(parents=True, exist_ok=True)
    (d / ".gitignore").write_text(GITIGNORE, encoding="utf-8", newline="\n")
    tmp = d / "estado.json.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, d / "estado.json")


def read_previous(project_dir: Path) -> dict | None:
    f = project_dir / "Repositorio" / "estado.json"
    try:
        prev = json.loads(f.read_text(encoding="utf-8"))
        return prev if isinstance(prev, dict) else None
    except (OSError, ValueError):
        return None


def sync(repo: Path, project_dir: Path, timeout: float = 15) -> dict:
    repo = repo.resolve()
    prev = read_previous(project_dir)
    state = {"schema": SCHEMA, "repo": repo.as_posix(), "remote": (prev or {}).get("remote"),
             "synced_at": (prev or {}).get("synced_at"), "ok": False, "errors": [], "stale_since": None,
             "main": (prev or {}).get("main"), "prs": (prev or {}).get("prs") or [],
             "local": (prev or {}).get("local"), "notes": []}
    errors: list[str] = state["errors"]
    budget = Budget(timeout)
    try:
        if not (repo / ".git").exists():
            errors.append(f"clone missing: {repo} is not a git repository")
        else:
            state["local"] = local_state(repo)
            if not git_out(repo, "remote", "get-url", "origin").strip():
                errors.append("no origin remote: this repository is not linked to GitHub")
            else:
                try:
                    budget.run(["git", "fetch", "origin", "--prune"], repo, "git fetch")
                except StepError as exc:
                    errors.append(str(exc))
                ref = repo_guard.main_ref(repo)
                if ref:
                    state["main"] = {"name": ref.split("/", 1)[-1],
                                     "head": git_out(repo, "rev-parse", ref).strip()}
                try:
                    gh = repo_guard.gh_argv()
                    out = budget.run([*gh, "repo", "view", "--json", "nameWithOwner"], repo, "gh repo view")
                    state["remote"] = json.loads(out)["nameWithOwner"]
                    out = budget.run([*gh, "pr", "list", "--state", "all", "--limit", str(PR_LIMIT),
                                      "--json", PR_FIELDS], repo, "gh pr list")
                    state["prs"] = [map_pr(p) for p in json.loads(out)]
                    if len(state["prs"]) >= PR_LIMIT:
                        state["notes"].append(f"gh devolvió {PR_LIMIT} PRs, su límite aquí: los más antiguos pueden no estar")
                except (StepError, ValueError, KeyError, TypeError) as exc:
                    errors.append(str(exc) if isinstance(exc, StepError) else f"gh: unreadable answer ({exc})")
    except Exception as exc:  # noqa: BLE001 - a sync must never end in a traceback
        errors.append(f"unexpected: {exc}")
    state["ok"] = not errors
    if state["ok"]:
        state["synced_at"] = now()
        state["stale_since"] = None
    else:
        state["stale_since"] = (prev or {}).get("synced_at")
    # A good sync saw a repository with no merged PR: from then on a merged PR is new, not a backlog.
    # A failed sync's empty list says nothing, and estado.json goes away when the repository changes.
    state["nothing_to_backfill"] = bool((prev or {}).get("nothing_to_backfill")) or (
        state["ok"] and not any(p.get("merged_at") for p in state["prs"]))
    write_state(project_dir, state)
    return state


# --------------------------------------------------------------------------
# commit status and worktrees
# --------------------------------------------------------------------------

def commit_status(repo: Path, commit: str) -> dict:
    r = subprocess.run(["git", "branch", "-r", "--contains", commit], cwd=repo, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return {"on_remote": False, "branches": []}
    branches = [ln.strip() for ln in r.stdout.splitlines() if ln.strip() and "->" not in ln]
    return {"on_remote": bool(branches), "branches": branches}


def worktree_root() -> Path:
    return Path(os.environ.get("KAIRO_WORKTREE_DIR") or (Path.home() / ".kairo" / "worktrees"))


def worktree_add(repo: Path, commit: str, name: str) -> dict:
    repo = repo.resolve()
    if not name or any(c in name for c in "/\\") or name in (".", ".."):
        raise Refused(f"not a usable worktree name: {name!r}")
    path = (worktree_root() / repo.name / name).resolve()
    if repo_guard.inside(path, repo):
        raise Refused(f"the worktree would be inside the clone: {path}")
    if path.exists():
        raise Refused(f"{path} already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["git", "worktree", "add", "--detach", str(path), commit], cwd=repo,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise Refused(f"git worktree add: {r.stderr.strip()[:300]}")
    return {"path": str(path)}


def registered_worktrees(repo: Path) -> list[Path]:
    out = git_out(repo, "worktree", "list", "--porcelain")
    return [Path(ln[len("worktree "):]) for ln in out.splitlines() if ln.startswith("worktree ")]


def _writable(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def worktree_remove(repo: Path, path: Path) -> dict:
    repo, path = repo.resolve(), path.resolve()
    known = [os.path.normcase(str(p.resolve())) for p in registered_worktrees(repo)[1:]]  # [0] is the clone
    if os.path.normcase(str(path)) not in known:
        raise Refused(f"{path} is not a worktree of {repo}")
    if os.path.normcase(str(path)) == os.path.normcase(str(repo)):
        raise Refused("refusing to remove the repository given as --repo")
    root = worktree_root().resolve() / repo.name
    if path == root or not repo_guard.inside(path, root):
        raise Refused(f"{path} is not under {root}: only worktrees made by this tool are removed")
    shutil.rmtree(path, onerror=_writable)
    subprocess.run(["git", "worktree", "prune"], cwd=repo, capture_output=True)
    return {"removed": str(path)}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sync")
    p.add_argument("--repo", required=True, type=Path)
    p.add_argument("--project-dir", required=True, type=Path)
    p.add_argument("--timeout", type=float, default=15)
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("commit-status")
    p.add_argument("--repo", required=True, type=Path)
    p.add_argument("--commit", required=True)
    p = sub.add_parser("worktree")
    wsub = p.add_subparsers(dest="wcmd", required=True)
    p = wsub.add_parser("add")
    p.add_argument("--repo", required=True, type=Path)
    p.add_argument("--commit", required=True)
    p.add_argument("--name", required=True)
    p = wsub.add_parser("remove")
    p.add_argument("--repo", required=True, type=Path)
    p.add_argument("--path", required=True, type=Path)
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        if a.cmd == "sync":
            if os.environ.get("KAIRO_AGENT_SESSION") and not a.force:
                out = {"skipped": True}
            else:
                out = sync(a.repo, a.project_dir, a.timeout)
        elif a.cmd == "commit-status":
            out = commit_status(a.repo, a.commit)
        elif a.wcmd == "add":
            out = worktree_add(a.repo, a.commit, a.name)
        else:
            out = worktree_remove(a.repo, a.path)
        print(json.dumps(out, ensure_ascii=False))
        return 0
    except (Refused, repo_guard.Refused) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
