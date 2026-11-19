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


def clean_env() -> dict:
    """No GIT_* from the caller except the isolated global config set by `isolated_git`."""
    keep = ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_") or k in keep}


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *GIT_ID, *args], cwd=repo, capture_output=True, env=clean_env(), check=check)


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """The script as the hook runs it: `run` from the top of the repository being
    committed (the vault's, unless the test says otherwise)."""
    if cwd is None and args[0] == "run":
        cwd = Path(args[2])
    env = clean_env()
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, env=env, cwd=cwd)


@pytest.fixture(autouse=True)
def isolated_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Git reads a temp global config and no system config: the user's never matters."""
    cfg = tmp_path / "gitconfig-global"
    cfg.write_bytes(b"")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return cfg


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


# ---- fix round 1 ---------------------------------------------------------------

def test_precommit_catches_a_rename_that_adds_a_secret(vault: Path):
    old, new = "Projects/x/Criticas/CR-9010.md", "Projects/x/Criticas/CR-9011.md"
    body = "".join(f"Linea inventada numero {i} de una objecion.\n" for i in range(40))
    put(vault / old, body)
    git(vault, "add", old)
    git(vault, "commit", "-q", "-m", "previa")
    git(vault, "mv", old, new)
    put(vault / new, body + fake_block() + "\n")
    git(vault, "add", new)
    st = git(vault, "-c", "diff.renames=true", "diff", "--cached", "--name-status", "-M").stdout.decode()
    assert st.startswith("R"), st  # git does see it as a rename
    r = run("run", "--vault", str(vault))
    assert r.returncode == 0, r.stderr
    assert "PRIVATE KEY" not in staged(vault, new)
    assert f"Kairo: redactado {new} (1)" in r.stderr.decode("utf-8")


def test_precommit_catches_a_type_change(vault: Path):
    # a committed symlink (made in the index, so no symlink privilege is needed)
    # becomes a regular file holding a key: git stages it as T
    rel = "Projects/x/Criticas/CR-9012.md"
    link = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=vault, input=b"otro.md",
                          capture_output=True, env=clean_env(), check=True).stdout.decode().strip()
    git(vault, "update-index", "--add", "--cacheinfo", f"120000,{link},{rel}")
    git(vault, "commit", "-q", "-m", "enlace")
    put(vault / rel, "Ahora es un fichero.\n" + fake_block() + "\n")
    git(vault, "rm", "-q", "--cached", rel)
    git(vault, "add", rel)
    st = git(vault, "diff", "--cached", "--name-status").stdout.decode()
    if not st.startswith("T"):
        pytest.skip(f"este git no deja preparar un cambio de tipo aquí ({st.strip()!r})")
    r = run("run", "--vault", str(vault))
    assert r.returncode == 0, r.stderr
    assert "PRIVATE KEY" not in staged(vault, rel)


def test_install_refuses_a_shared_core_hooks_path(vault: Path, isolated_git: Path, tmp_path: Path):
    shared = tmp_path / "hooks-compartidos"
    shared.mkdir()
    isolated_git.write_text(f"[core]\n\thooksPath = {shared.as_posix()}\n", encoding="utf-8")
    r = run("install", "--vault", str(vault), "--python", sys.executable)
    assert r.returncode == 3, r.stderr
    out = json.loads(r.stdout)
    assert "core.hooksPath" in out["error"] and "git config --local core.hooksPath" in out["error"]
    assert list(shared.iterdir()) == []
    # a local core.hooksPath (even the same folder) is the vault's own decision
    git(vault, "config", "--local", "core.hooksPath", shared.as_posix())
    r = run("install", "--vault", str(vault), "--python", sys.executable)
    assert r.returncode == 0, r.stderr
    assert (shared / "pre-commit").is_file()


def test_run_in_another_repository_does_nothing(vault: Path, tmp_path: Path):
    other = tmp_path / "otro-repo"
    other.mkdir()
    git(other, "init", "-q")
    git(other, "config", "core.autocrlf", "false")
    rel = "nota.md"
    text = "Ajeno.\n" + fake_block() + "\n"
    put(other / rel, text)
    git(other, "add", rel)
    # the vault has its own staged key with unstaged changes: none of its business here
    vrel = "Projects/x/Criticas/CR-9015.md"
    vtext = fake_block() + "\n"
    put(vault / vrel, vtext)
    git(vault, "add", vrel)
    put(vault / vrel, vtext + "sin preparar\n")
    r = run("run", "--vault", str(vault), cwd=other)
    assert r.returncode == 0, r.stderr
    assert r.stderr == b""
    assert staged(vault, vrel) == vtext
    assert staged(other, rel) == text
    assert (other / rel).read_text(encoding="utf-8").replace("\r\n", "\n") == text


def test_run_refuses_when_the_vault_cannot_be_placed(vault: Path, tmp_path: Path):
    # --vault names the repository itself but not a worktree of it (a bare clone
    # here): same repository as the committing worktree, no place to map: refuse
    bare = tmp_path / "vault.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(vault), str(bare)], capture_output=True,
                   env=clean_env(), check=True)
    wt = tmp_path / "wt-bare"
    git(bare, "worktree", "add", "-q", str(wt))
    put(wt / "nota.md", fake_block() + "\n")
    git(wt, "add", "nota.md")
    r = run("run", "--vault", str(bare), cwd=wt)
    assert r.returncode == 1
    assert "no sé dónde queda el vault" in r.stderr.decode("utf-8")


def test_hook_checks_a_linked_worktree_of_the_vault(vault: Path, tmp_path: Path):
    assert run("install", "--vault", str(vault), "--python", sys.executable).returncode == 0
    wt = tmp_path / "wt"
    git(vault, "worktree", "add", "-q", "-b", "rama", str(wt))
    rel = "Projects/x/Criticas/CR-9016.md"
    (wt / rel).parent.mkdir(parents=True, exist_ok=True)  # empty folders are not in git
    put(wt / rel, "Desde otro worktree.\n" + fake_block() + "\n")
    git(wt, "add", rel)
    c = git(wt, "commit", "-q", "-m", "critica", check=False)
    assert c.returncode == 0, c.stderr
    committed = git(wt, "show", "HEAD:" + rel).stdout.decode("utf-8")
    assert "PRIVATE KEY" not in committed and "[REDACTADO private_key" in committed
    assert "PRIVATE KEY" not in (wt / rel).read_text(encoding="utf-8")
    assert "PRIVATE KEY" not in staged(wt, rel)


def test_hook_maps_a_vault_subfolder_into_a_linked_worktree(tmp_path: Path):
    repo = tmp_path / "Kairo"
    v = repo / "vault"
    (v / "Papers").mkdir(parents=True)
    (v / "Projects" / "x").mkdir(parents=True)
    put(v / "Projects" / "x" / "_hub.md", "---\nid: PROJ-902\n---\n")
    git(repo, "init", "-q")
    git(repo, "config", "core.autocrlf", "false")
    git(repo, "add", "vault/Projects/x/_hub.md")
    git(repo, "commit", "-q", "-m", "inicio")
    assert run("install", "--vault", str(v), "--python", sys.executable).returncode == 0
    wt = tmp_path / "wt-sub"
    git(repo, "worktree", "add", "-q", "-b", "rama", str(wt))
    inside, outside = "vault/Projects/x/CR-9017.md", "fuera.md"
    put(wt / inside, fake_block() + "\n")
    outside_text = "Fuera del vault.\n" + fake_block() + "\n"
    put(wt / outside, outside_text)
    git(wt, "add", inside, outside)
    c = git(wt, "commit", "-q", "-m", "x", check=False)
    assert c.returncode == 0, c.stderr
    assert "PRIVATE KEY" not in git(wt, "show", "HEAD:" + inside).stdout.decode("utf-8")
    assert git(wt, "show", "HEAD:" + outside).stdout.decode("utf-8") == outside_text  # not the vault's


def test_commit_with_paths_also_redacts_the_real_index(vault: Path):
    run("install", "--vault", str(vault), "--python", sys.executable)
    rel = "Projects/x/Criticas/CR-9013.md"
    put(vault / rel, "Primera version.\n")
    git(vault, "add", rel)
    git(vault, "commit", "-q", "-m", "previa")
    put(vault / rel, "Segunda version.\n" + fake_block() + "\n")
    c = git(vault, "commit", "-q", "-m", "solo esta", "--", rel, check=False)
    assert c.returncode == 0, c.stderr
    committed = git(vault, "show", "HEAD:" + rel).stdout.decode("utf-8")
    assert "PRIVATE KEY" not in committed and "[REDACTADO private_key" in committed
    assert "PRIVATE KEY" not in staged(vault, rel)  # the index git leaves behind
    assert git(vault, "status", "--porcelain", "--", rel).stdout == b""


def test_hook_says_when_python_is_gone(vault: Path, tmp_path: Path):
    gone = tmp_path / "no-existe" / "python.exe"
    r = run("install", "--vault", str(vault), "--python", str(gone))
    assert r.returncode == 0, r.stderr
    rel = "Projects/x/Criticas/CR-9014.md"
    put(vault / rel, "Nada privado.\n")
    git(vault, "add", rel)
    c = git(vault, "commit", "-q", "-m", "x", check=False)
    assert c.returncode != 0
    assert (f"Kairo: no encuentro Python en {gone.as_posix()}; vuelve a ejecutar vault_precommit.py install"
            in c.stderr.decode("utf-8"))


# --- a staged text file over redact.MAX_FILE_BYTES is scanned in chunks (limits
# lowered here so the "large" file stays small and the test fast) ---------------

sys.path.insert(0, str(SCRIPT.parent))
import redact as _redact  # noqa: E402
import vault_precommit as _vp  # noqa: E402


def _run_in_process(vault: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> tuple[int, str]:
    for k in list(os.environ):
        if k.startswith("GIT_") and k not in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM"):
            monkeypatch.delenv(k)
    monkeypatch.chdir(vault)
    code = _vp.main(["run", "--vault", str(vault)])
    return code, capsys.readouterr().err


def _large_with_key_across_chunks(chunk: int, total: int) -> tuple[str, str]:
    block = fake_block()
    line = "relleno inventado de una nota larga\n"
    head = (line * (chunk // len(line) + 1))[: chunk - 21]   # the cut falls inside the BEGIN header
    tail = line * ((total - chunk) // len(line) + 1)
    return head + "\n" + block + "\n" + tail, block


def test_precommit_scans_a_large_file_in_chunks_and_redacts_it(vault: Path, monkeypatch, capsys):
    monkeypatch.setattr(_redact, "MAX_FILE_BYTES", 64 * 1024)
    monkeypatch.setattr(_vp, "SCAN_CHUNK_BYTES", 32 * 1024)
    monkeypatch.setattr(_vp, "SCAN_OVERLAP_BYTES", 8 * 1024)
    text, block = _large_with_key_across_chunks(32 * 1024, 200 * 1024)
    rel = "Projects/x/Criticas/CR-9010.md"
    put(vault / rel, text)
    assert (vault / rel).stat().st_size > _redact.MAX_FILE_BYTES
    git(vault, "add", rel)
    monkeypatch.setattr(_vp, "SCAN_OVERLAP_BYTES", 0)       # without the overlap the cut key is missed
    assert _vp.large_blob_has_secret(vault, rel) is False
    monkeypatch.setattr(_vp, "SCAN_OVERLAP_BYTES", 8 * 1024)
    assert _vp.large_blob_has_secret(vault, rel) is True
    code, err = _run_in_process(vault, monkeypatch, capsys)
    assert code == 0, err
    blob = staged(vault, rel)
    assert "[REDACTADO private_key sha256:" in blob and "PRIVATE KEY" not in blob
    assert block.splitlines()[1] not in (vault / rel).read_text(encoding="utf-8")
    assert f"Kairo: redactado {rel} (1)" in err


def test_precommit_refuses_a_large_file_it_cannot_redact_in_memory(vault: Path, monkeypatch, capsys):
    monkeypatch.setattr(_redact, "MAX_FILE_BYTES", 64 * 1024)
    monkeypatch.setattr(_vp, "SCAN_CHUNK_BYTES", 32 * 1024)
    monkeypatch.setattr(_vp, "SCAN_OVERLAP_BYTES", 8 * 1024)
    monkeypatch.setattr(_vp, "MAX_REDACT_BYTES", 100 * 1024)
    text, _ = _large_with_key_across_chunks(32 * 1024, 200 * 1024)
    rel = "Projects/x/Criticas/CR-9011.md"
    put(vault / rel, text)
    git(vault, "add", rel)
    code, err = _run_in_process(vault, monkeypatch, capsys)
    assert code == 1
    assert rel in err and "redáctalo a mano" in err
    assert "PRIVATE KEY" in staged(vault, rel)


def test_precommit_leaves_a_clean_large_file(vault: Path, monkeypatch, capsys):
    monkeypatch.setattr(_redact, "MAX_FILE_BYTES", 64 * 1024)
    monkeypatch.setattr(_vp, "SCAN_CHUNK_BYTES", 32 * 1024)
    monkeypatch.setattr(_vp, "SCAN_OVERLAP_BYTES", 8 * 1024)
    rel = "Projects/x/Criticas/CR-9012.md"
    body = "nada privado en esta nota larga\n" * 5000
    put(vault / rel, body)
    git(vault, "add", rel)
    code, err = _run_in_process(vault, monkeypatch, capsys)
    assert code == 0 and err == ""
    assert staged(vault, rel) == body


def test_precommit_refuses_root_config_with_private_material(vault: Path):
    block = fake_block()
    for rel in (".env", ".obsidian/plugins/x/data.json"):
        (vault / rel).parent.mkdir(parents=True, exist_ok=True)
        content = "VALOR=1\n" + block + "\n"
        put(vault / rel, content)
        git(vault, "add", "-f", rel)
        r = run("run", "--vault", str(vault))
        assert r.returncode == 1
        err = r.stderr.decode("utf-8")
        assert f"{rel}: contiene material privado; quítalo a mano (es configuración, no se reescribe)" in err
        assert (vault / rel).read_text(encoding="utf-8") == content
        assert "PRIVATE KEY" in staged(vault, rel)
        git(vault, "rm", "-q", "--cached", rel)


def test_precommit_lets_clean_root_config_through(vault: Path):
    rel = ".obsidian/app.json"
    (vault / rel).parent.mkdir(parents=True)
    put(vault / rel, "{}")
    git(vault, "add", rel)
    r = run("run", "--vault", str(vault))
    assert r.returncode == 0 and r.stderr == b""
