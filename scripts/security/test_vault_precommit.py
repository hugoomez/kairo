"""Tests for vault_precommit.py: the vault's pre-commit backstop that redacts
private material before it is committed. Each test builds an invented vault in a
temp folder with a real `git init`; nothing touches a real repository. Key-shaped
strings are assembled at runtime from random bytes."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "vault_precommit.py"
DASHES = "-" * 5
GIT_ID = ["-c", "user.name=t", "-c", "user.email=t@t"]


def fake_block(label: str = "OPENSSH PRIVATE KEY") -> str:
    body = base64.b64encode(os.urandom(120)).decode()
    lines = [body[i:i + 64] for i in range(0, len(body), 64)]
    return (DASHES + "BEGIN " + label + DASHES + "\n" + "\n".join(lines) + "\n"
            + DASHES + "END " + label + DASHES)


def put(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))  # LF as written, on every platform


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", *GIT_ID, *args], cwd=repo, capture_output=True, env=env, check=check)


def run(*args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, env=env)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    (v / "Papers").mkdir(parents=True)
    (v / "Projects" / "x" / "Criticas").mkdir(parents=True)
    put(v / "Projects" / "x" / "_hub.md", "---\nid: PROJ-901\n---\n")
    git(v, "init", "-q")
    git(v, "config", "core.autocrlf", "false")  # the repo's own config: the hook sees it too
    git(v, "add", "Projects/x/_hub.md")
    git(v, "commit", "-q", "-m", "inicio")
    return v


def staged(v: Path, rel: str) -> str:
    return git(v, "show", ":" + rel).stdout.decode("utf-8")


def test_precommit_redacts_and_restages(vault: Path):
    block = fake_block()
    rel = "Projects/x/Criticas/CR-9001.md"
    put(vault / rel, "---\nid: CR-9001\n---\n\n" + block + "\n")
    git(vault, "add", rel)
    r = run("run", "--vault", str(vault))
    assert r.returncode == 0, r.stderr
    blob = staged(vault, rel)
    assert "[REDACTADO private_key sha256:" in blob
    assert "PRIVATE KEY" not in blob
    assert "PRIVATE KEY" not in (vault / rel).read_text(encoding="utf-8")
    err = r.stderr.decode("utf-8")
    assert f"Kairo: redactado {rel} (1)" in err
    assert block.splitlines()[1] not in err


def test_precommit_with_autocrlf_and_crlf_working_file(vault: Path):
    git(vault, "config", "core.autocrlf", "true")
    block = fake_block()
    rel = "Projects/x/Criticas/CR-9006.md"
    put(vault / rel, ("Objeción inventada.\n" + block + "\nfin\n").replace("\n", "\r\n"))
    git(vault, "add", rel)
    r = run("run", "--vault", str(vault))
    assert r.returncode == 0, r.stderr
    blob = staged(vault, rel)
    assert "[REDACTADO private_key sha256:" in blob and "PRIVATE KEY" not in blob
    assert (vault / rel).read_bytes().endswith(b"]\r\nfin\r\n")


def test_precommit_leaves_papers_and_clean_files(vault: Path):
    block = fake_block()
    paper = "Papers/P-9001 inventado.md"
    put(vault / paper, "## Texto completo\n\n" + block + "\n")
    clean = "Projects/x/Criticas/CR-9002.md"
    put(vault / clean, "Nada privado.\n")
    git(vault, "add", paper, clean)
    r = run("run", "--vault", str(vault))
    assert r.returncode == 0, r.stderr
    assert "PRIVATE KEY" in staged(vault, paper)
    assert staged(vault, clean) == "Nada privado.\n"
    assert r.stderr == b""


def test_precommit_refuses_partial_staging(vault: Path):
    block = fake_block()
    rel = "Projects/x/Criticas/CR-9003.md"
    put(vault / rel, "linea uno\n" + block + "\n")
    git(vault, "add", rel)
    put(vault / rel, "linea uno\n" + block + "\nlinea sin preparar\n")
    r = run("run", "--vault", str(vault))
    assert r.returncode == 1
    err = r.stderr.decode("utf-8")
    assert f"{rel}: contiene material privado y tiene cambios sin preparar; redáctalo a mano" in err
    assert "PRIVATE KEY" in staged(vault, rel)  # nothing changed
    assert "linea sin preparar" in (vault / rel).read_text(encoding="utf-8")


def test_install_refuses_foreign_hook(vault: Path):
    hook = vault / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    put(hook, "#!/bin/sh\necho ajeno\n")
    r = run("install", "--vault", str(vault))
    assert r.returncode == 3
    out = json.loads(r.stdout)
    assert out["error"] == "ya hay un pre-commit ajeno"
    assert Path(out["path"]).resolve() == hook.resolve()
    assert hook.read_text(encoding="utf-8") == "#!/bin/sh\necho ajeno\n"
    st = json.loads(run("status", "--vault", str(vault)).stdout)
    assert st["installed"] is False and st["foreign"] is True


def test_install_idempotent(vault: Path):
    st = json.loads(run("status", "--vault", str(vault)).stdout)
    assert st["installed"] is False and st["foreign"] is False
    r1 = run("install", "--vault", str(vault), "--python", sys.executable)
    assert r1.returncode == 0, r1.stderr
    path = Path(json.loads(r1.stdout)["installed"])
    first = path.read_bytes()
    r2 = run("install", "--vault", str(vault), "--python", sys.executable)
    assert r2.returncode == 0, r2.stderr
    assert path.read_bytes() == first
    text = first.decode("utf-8")
    assert "# kairo-vault-precommit" in text
    assert b"\r\n" not in first
    assert "\\" not in text
    assert " run --vault " in text
    st = json.loads(run("status", "--vault", str(vault)).stdout)
    assert st == {"installed": True, "foreign": False, "path": st["path"]}
    assert Path(st["path"]).resolve() == path.resolve()


def test_install_respects_core_hooks_path(vault: Path):
    git(vault, "config", "core.hooksPath", "mis-hooks")
    r = run("install", "--vault", str(vault), "--python", sys.executable)
    assert r.returncode == 0, r.stderr
    path = Path(json.loads(r.stdout)["installed"])
    assert path.resolve() == (vault / "mis-hooks" / "pre-commit").resolve()
    assert path.is_file()


def test_hook_runs_on_git_commit(vault: Path):
    r = run("install", "--vault", str(vault), "--python", sys.executable)
    assert r.returncode == 0, r.stderr
    block = fake_block()
    rel = "Projects/x/Criticas/CR-9004.md"
    put(vault / rel, "Objeción inventada.\n" + block + "\n")
    git(vault, "add", rel)
    c = git(vault, "commit", "-q", "-m", "critica", check=False)
    assert c.returncode == 0, c.stderr
    committed = git(vault, "show", "HEAD:" + rel).stdout.decode("utf-8")
    assert "[REDACTADO private_key sha256:" in committed
    assert "PRIVATE KEY" not in committed
    assert "Objeción inventada." in committed


def test_hook_blocks_commit_on_partial_staging(vault: Path):
    run("install", "--vault", str(vault), "--python", sys.executable)
    block = fake_block()
    rel = "Projects/x/Criticas/CR-9005.md"
    put(vault / rel, block + "\n")
    git(vault, "add", rel)
    put(vault / rel, block + "\notra linea\n")
    c = git(vault, "commit", "-q", "-m", "critica", check=False)
    assert c.returncode != 0
    assert git(vault, "rev-list", "--count", "HEAD").stdout.strip() == b"1"
