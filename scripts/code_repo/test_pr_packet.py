"""Tests for pr_packet.py. Run: python -m pytest scripts/code_repo

Invented repository, ids and text only. Everything happens inside tmp_path;
nothing touches the network or a vault."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pr_packet as pp  # noqa: E402

MARGIN = 800


def git(cwd, *a):
    r = subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def pr_entry(number, merge_commit, title="Faster solve", body="Speeds up the solver."):
    return {"number": number, "title": title, "author": "ana", "created_at": "2026-01-02T00:00:00Z",
            "merged_at": "2026-01-03T00:00:00Z", "closed_at": "2026-01-03T00:00:00Z", "state": "MERGED",
            "branch": f"feat-{number}", "base": "main", "body": body, "merge_commit": merge_commit,
            "url": f"https://example.test/acme/solver/pull/{number}"}


def merge_branch(repo, name, files, message, extra_msg=""):
    git(repo, "checkout", "-q", "-b", name)
    for fname, content in files.items():
        p = repo / fname
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")
        git(repo, "add", fname)
    git(repo, "commit", "-q", "-m", message + (("\n\n" + extra_msg) if extra_msg else ""))
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", name, "-m", f"Merge {name}")
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "solver"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.test")
    git(r, "config", "user.name", "T")
    (r / "README.md").write_text("solver\n", encoding="utf-8")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "init")
    return r


def estado(tmp_path, prs):
    f = tmp_path / "estado.json"
    f.write_text(json.dumps({"prs": prs}), encoding="utf-8")
    return f


def run(repo, est, prs, *extra):
    return subprocess.run([sys.executable, str(HERE / "pr_packet.py"), "--repo", str(repo), "--estado", str(est),
                           "--prs", prs, *extra], capture_output=True, text=True, encoding="utf-8")


def test_packet_has_body_commits_files_and_trailers(repo, tmp_path):
    sha = merge_branch(repo, "feat-7", {"solve.py": "def solve():\n    return 1\n"}, "Cache the factorization",
                       "Motivated-By: H-9001")
    est = estado(tmp_path, [pr_entry(7, sha, body="Closes the gap in E-9002.")])
    r = run(repo, est, "7")
    assert r.returncode == 0, r.stderr
    p = r.stdout
    assert "Faster solve" in p and "ana" in p
    assert "Closes the gap in E-9002." in p
    assert "Cache the factorization" in p
    assert "Motivated-By: H-9001" in p
    assert "solve.py" in p
    assert "def solve" in p
    assert "ids_en_el_texto: [" in p
    assert '"H-9001"' in p and '"E-9002"' in p


def test_ids_come_from_title_body_and_commits_only(repo, tmp_path):
    sha = merge_branch(repo, "feat-7", {"a.py": "# C-9999 appears only in the diff\n"}, "Tune", "Ref: ADR-0042")
    est = estado(tmp_path, [pr_entry(7, sha, title="Fix H-9001", body="")])
    ids = pp.ids_en_el_texto(pr_entry(7, sha, title="Fix H-9001", body=""),
                             ["Tune\n\nRef: ADR-0042"])
    assert ids == ["H-9001", "ADR-0042"]
    assert "C-9999" not in run(repo, est, "7").stdout.split("ids_en_el_texto:")[1]


def test_packet_never_reads_the_vault(repo, tmp_path):
    sha = merge_branch(repo, "feat-7", {"a.py": "x = 1\n"}, "Add a")
    est = estado(tmp_path, [pr_entry(7, sha)])
    for flag in ("--project-dir", "--vault"):
        r = run(repo, est, "7", flag, str(tmp_path))
        assert r.returncode != 0
        assert "unrecognized arguments" in r.stderr


def test_binary_and_huge_diffs_are_clipped_and_said_so(repo, tmp_path):
    big = "\n".join(f"line {i} of a very long generated file" for i in range(5000))
    sha = merge_branch(repo, "feat-7", {"big.txt": big, "img.bin": bytes(range(256)) * 50}, "Add data")
    est = estado(tmp_path, [pr_entry(7, sha)])
    p = run(repo, est, "7", "--max-chars", "12000").stdout
    assert len(p) <= 12000 + MARGIN
    assert "[diff recortado" in p
    assert "img.bin" in p


def test_one_clipped_pr_never_pushes_another_out(repo, tmp_path):
    big = "\n".join(f"row {i} filler filler filler" for i in range(5000))
    s1 = merge_branch(repo, "feat-1", {"big.txt": big}, "Huge change")
    s2 = merge_branch(repo, "feat-2", {"small.py": "y = 2\n"}, "Small change")
    est = estado(tmp_path, [pr_entry(1, s1, title="Huge PR"), pr_entry(2, s2, title="Small PR")])
    p = run(repo, est, "1,2", "--max-chars", "12000").stdout
    assert len(p) <= 12000 + 2 * MARGIN
    assert "Huge PR" in p and "Small PR" in p
    assert "y = 2" in p and "Small change" in p


def test_empty_body_is_explicit(repo, tmp_path):
    sha = merge_branch(repo, "feat-7", {"a.py": "x = 1\n"}, "Add a")
    est = estado(tmp_path, [pr_entry(7, sha, body="")])
    assert "(sin descripción)" in run(repo, est, "7").stdout


def test_unknown_merge_commit_does_not_crash(repo, tmp_path):
    est = estado(tmp_path, [pr_entry(7, "deadbeef" * 5), pr_entry(8, None)])
    r = run(repo, est, "7,8")
    assert r.returncode == 0, r.stderr
    assert "commit no disponible" in r.stdout
    assert "Faster solve" in r.stdout


def test_unknown_pr_number_is_refused(repo, tmp_path):
    est = estado(tmp_path, [pr_entry(7, None)])
    r = run(repo, est, "99")
    assert r.returncode == 3
    assert "99" in r.stderr
