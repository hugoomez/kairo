"""Tests for repo_sync.py. Run: python -m pytest scripts/code_repo

Invented repository, ids and text only. A local bare remote stands in for
GitHub and a fake_gh.py written to tmp_path stands in for `gh` (KAIRO_GH_CMD).
Nothing is written outside tmp_path and nothing touches the network."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import repo_sync as rs  # noqa: E402

FAKE_GH = '''\
import json, os, sys, time
mode = os.environ.get("FAKE_GH_MODE", "ok")
if mode == "sleep":
    time.sleep(30)
if mode == "fail":
    sys.stderr.write("gh: not logged in\\n")
    sys.exit(4)
args = sys.argv[1:]
if args[:2] == ["repo", "view"]:
    print(json.dumps({"nameWithOwner": "acme/solver"}))
elif args[:2] == ["pr", "list"]:
    prs = [
        {"number": 7, "title": "Faster solve", "author": {"login": "ana"}, "createdAt": "2026-01-02T00:00:00Z",
         "mergedAt": "2026-01-03T00:00:00Z", "closedAt": "2026-01-03T00:00:00Z", "state": "MERGED",
         "headRefName": "faster", "baseRefName": "main", "body": "H-9001 speedup",
         "mergeCommit": {"oid": "abc123"}, "url": "https://example.test/acme/solver/pull/7"},
        {"number": 8, "title": "Open one", "author": None, "createdAt": "2026-01-04T00:00:00Z",
         "mergedAt": None, "closedAt": None, "state": "OPEN", "headRefName": "wip2", "baseRefName": "main",
         "body": "", "mergeCommit": None, "url": "https://example.test/acme/solver/pull/8"},
    ]
    print(json.dumps(prs))
else:
    sys.exit(2)
'''


def git(cwd, *a, check=True):
    r = subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)
    if check:
        assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = tmp_path / "fake_gh.py"
    fake.write_text(FAKE_GH, encoding="utf-8")
    monkeypatch.setenv("KAIRO_GH_CMD", json.dumps([sys.executable, str(fake)]))
    monkeypatch.setenv("KAIRO_WORKTREE_DIR", str(tmp_path / "worktrees"))
    monkeypatch.delenv("KAIRO_AGENT_SESSION", raising=False)
    monkeypatch.delenv("FAKE_GH_MODE", raising=False)
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    repo = tmp_path / "solver"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "sucio.py").write_text("x = 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "first")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "origin", "main")
    pushed = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "wip")
    (repo / "wip.py").write_text("y = 2\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "wip work")
    local = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    (repo / "sucio.py").write_text("x = 99  # SECRET-BODY\n", encoding="utf-8")
    (repo / "nuevo.txt").write_text("hello SECRET-BODY\n", encoding="utf-8")
    project = tmp_path / "vault" / "Projects" / "solver"
    project.mkdir(parents=True)
    return {"tmp": tmp_path, "repo": repo, "project": project, "pushed": pushed, "local": local,
            "remote": remote, "fake": fake}


def estado(e) -> dict:
    return json.loads((e["project"] / "Repositorio" / "estado.json").read_text(encoding="utf-8"))


def do_sync(e, timeout=15):
    return rs.sync(e["repo"], e["project"], timeout)


def snapshot(repo):
    return (git(repo, "rev-parse", "--abbrev-ref", "HEAD"), git(repo, "rev-parse", "HEAD"),
            git(repo, "status", "--porcelain"), git(repo, "diff", "--cached"))


def test_sync_writes_state_with_prs_and_local_work(env):
    s = do_sync(env)
    assert s["ok"] and s["remote"] == "acme/solver" and s["prs"][0]["number"] == 7
    assert s["errors"] == [] and s["stale_since"] is None and s["schema"] == 1
    assert s["main"] == {"name": "main", "head": env["pushed"]}
    assert s["local"]["branch"] == "main"
    assert s["local"]["unpushed_branches"] == [{"name": "wip", "ahead": 1}]
    assert "sucio.py" in s["local"]["modified"]
    assert "nuevo.txt" in s["local"]["untracked"]
    assert s["prs"][0]["merge_commit"] == "abc123" and s["prs"][0]["branch"] == "faster"
    assert estado(env) == s
    raw = (env["project"] / "Repositorio" / "estado.json").read_text(encoding="utf-8")
    assert "SECRET-BODY" not in raw  # names only, never contents


def test_sync_does_not_touch_the_working_copy(env):
    before = snapshot(env["repo"])
    do_sync(env)
    assert snapshot(env["repo"]) == before


def test_failed_gh_keeps_last_prs_and_marks_stale(env, monkeypatch):
    first = do_sync(env)
    monkeypatch.setenv("FAKE_GH_MODE", "fail")
    s = do_sync(env)
    assert not s["ok"] and s["prs"] == first["prs"] and s["stale_since"] == first["synced_at"]
    assert s["synced_at"] == first["synced_at"] and s["errors"]
    assert s["remote"] == "acme/solver"
    assert s["main"] == first["main"]  # fetch still worked
    assert estado(env) == s


def test_gh_missing_is_a_failure_not_a_traceback(env, monkeypatch):
    monkeypatch.setenv("KAIRO_GH_CMD", json.dumps([str(env["tmp"] / "no-such-gh")]))
    s = do_sync(env)
    assert not s["ok"] and s["errors"] and s["local"]["branch"] == "main"


def test_no_origin_is_reported_without_crash(env):
    git(env["repo"], "remote", "remove", "origin")
    s = do_sync(env)
    assert not s["ok"] and any("origin" in x for x in s["errors"])
    assert s["local"]["branch"] == "main" and s["prs"] == []
    assert estado(env)["errors"] == s["errors"]


def test_missing_clone_keeps_last_state(env):
    first = do_sync(env)
    import shutil
    shutil.rmtree(env["repo"], onerror=rs._writable)
    s = do_sync(env)
    assert not s["ok"] and s["prs"] == first["prs"] and s["local"] == first["local"]
    assert s["stale_since"] == first["synced_at"] and s["errors"]


def test_missing_clone_without_previous_state(env):
    import shutil
    shutil.rmtree(env["repo"], onerror=rs._writable)
    s = do_sync(env)
    assert not s["ok"] and s["errors"] and s["prs"] == [] and s["stale_since"] is None


def test_timeout_is_a_failure_not_a_hang(env, monkeypatch):
    first = do_sync(env)
    monkeypatch.setenv("FAKE_GH_MODE", "sleep")
    s = do_sync(env, timeout=1)
    assert not s["ok"] and s["errors"] and s["prs"] == first["prs"]


def test_agent_session_skips_unless_forced(env, monkeypatch, capsys):
    monkeypatch.setenv("KAIRO_AGENT_SESSION", "1")
    args = ["sync", "--repo", str(env["repo"]), "--project-dir", str(env["project"])]
    assert rs.main(args) == 0
    assert json.loads(capsys.readouterr().out) == {"skipped": True}
    assert not (env["project"] / "Repositorio" / "estado.json").exists()
    assert rs.main([*args, "--force"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert (env["project"] / "Repositorio" / "estado.json").exists()


def test_deleted_author_becomes_empty_string(env):
    s = do_sync(env)
    assert s["prs"][1]["author"] == "" and s["prs"][1]["merge_commit"] == ""
    assert s["prs"][1]["merged_at"] == "" and s["prs"][0]["author"] == "ana"


def test_commit_status_on_any_remote_branch_and_unpushed(env):
    do_sync(env)
    assert rs.commit_status(env["repo"], env["pushed"])["on_remote"]
    assert "origin/main" in rs.commit_status(env["repo"], env["pushed"])["branches"]
    r = rs.commit_status(env["repo"], env["local"])
    assert not r["on_remote"] and r["branches"] == []
    # pushed to some other branch of the remote still counts
    git(env["repo"], "push", "-q", "origin", "wip")
    git(env["repo"], "fetch", "-q", "origin")
    assert rs.commit_status(env["repo"], env["local"])["on_remote"]


def test_worktree_add_leaves_the_clone_untouched_and_remove_cleans_up(env):
    before = snapshot(env["repo"])
    r = rs.worktree_add(env["repo"], env["pushed"], "E-9002")
    path = Path(r["path"])
    assert path.is_dir() and (path / "sucio.py").read_text(encoding="utf-8") == "x = 1\n"
    assert git(path, "rev-parse", "HEAD") == env["pushed"]
    assert snapshot(env["repo"]) == before
    rs.worktree_remove(env["repo"], path)
    assert not path.exists()
    assert str(path).replace("\\", "/") not in git(env["repo"], "worktree", "list").replace("\\", "/")
    assert snapshot(env["repo"]) == before


def test_worktree_outside_vault_and_clone(env):
    r = rs.worktree_add(env["repo"], env["pushed"], "E-9003")
    path = Path(r["path"]).resolve()
    assert env["tmp"].resolve() / "worktrees" / "solver" / "E-9003" == path
    for other in (env["repo"], env["project"]):
        assert other.resolve() not in path.parents


def test_worktree_inside_clone_is_refused(env, monkeypatch, capsys):
    monkeypatch.setenv("KAIRO_WORKTREE_DIR", str(env["repo"] / "wt"))
    code = rs.main(["worktree", "add", "--repo", str(env["repo"]), "--commit", env["pushed"], "--name", "E-9004"])
    assert code == 3 and not (env["repo"] / "wt").exists()


def test_worktree_remove_refuses_a_path_that_is_not_a_worktree(env):
    victim = env["tmp"] / "precious"
    victim.mkdir()
    code = rs.main(["worktree", "remove", "--repo", str(env["repo"]), "--path", str(victim)])
    assert code == 3 and victim.exists()


def test_worktree_remove_refuses_a_registered_worktree_outside_the_worktree_dir(env):
    other = env["tmp"] / "mine" / "E-9005"
    other.parent.mkdir()
    git(env["repo"], "worktree", "add", "--detach", str(other), env["pushed"])
    code = rs.main(["worktree", "remove", "--repo", str(env["repo"]), "--path", str(other)])
    assert code == 3 and other.is_dir()
    # the worktree root itself is never removable either
    made = rs.worktree_add(env["repo"], env["pushed"], "E-9006")
    root = Path(made["path"]).parent
    assert rs.main(["worktree", "remove", "--repo", str(env["repo"]), "--path", str(root)]) == 3
    assert root.is_dir()


def test_worktree_remove_never_removes_the_repo_given_as_repo(env):
    made = Path(rs.worktree_add(env["repo"], env["pushed"], "E-9007")["path"])
    code = rs.main(["worktree", "remove", "--repo", str(made), "--path", str(made)])
    assert code == 3 and made.is_dir()


def test_gitignore_keeps_derived_state_out_of_vault_commits(env):
    do_sync(env)
    gi = (env["project"] / "Repositorio" / ".gitignore").read_text(encoding="utf-8").split()
    assert "estado.json" in gi and "cambios.json" in gi
