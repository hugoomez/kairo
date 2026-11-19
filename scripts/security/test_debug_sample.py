"""Tests for debug_sample.py. Invented vault, datasets and material, all under tmp_path."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import data_guard  # noqa: E402
import debug_sample as ds  # noqa: E402
import fake_material as fm  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "debug_sample.py"


def _write(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        p.write_bytes(data)
    else:
        p.write_text(data, encoding="utf-8", newline="")


@pytest.fixture
def env(tmp_path):
    vault = tmp_path / "vault"
    datos = vault / "Projects" / "demo" / "Datos"
    datos.mkdir(parents=True)
    root = tmp_path / "fuera" / "repos"
    secrets: list[str] = []
    rcpt = fm.age_recipient()
    sops = fm.sops_yaml(rcpt, "AB" * 20)
    _write(root / "acme-inventada/repo-a/k8s/app.enc.yaml", sops)
    blob = "AgB" + fm.b64(300)
    _write(root / "acme-inventada/repo-a/sealed.yaml", fm.sealed_secret(blob))
    pw, tok = base64.b64encode(b"hunter2-inventada").decode(), fm.b64(32)
    _write(root / "acme-inventada/repo-b/secret.yaml", fm.k8s_secret(pw, tok, "usuario-inventado"))
    secrets += [pw, tok, "usuario-inventado"]
    _write(root / "acme-inventada/repo-b/.env", "DB_PASSWORD=clave-en-claro-1\nexport API=otra-en-claro\n")
    secrets += ["clave-en-claro-1", "otra-en-claro"]
    priv = os.urandom(32).hex()
    _write(root / "org-x/repo/.env.keys", f'DOTENV_PRIVATE_KEY_PRODUCTION="{priv}"\n')
    secrets.append(priv)
    _write(root / "org-x/repo/secret.age", fm.age_binary(fm.b64(32).rstrip("=")))
    key = fm.block("RSA PRIVATE KEY")
    _write(root / "org-x/repo/key.pem", "certificado de prueba\n" + key + "\n")
    secrets += key.splitlines()[1:-1]
    _write(root / "org-x/repo/grande-inventado.txt", "a" * (ds.MAX_BYTES + 1))
    _write(root / "org-x/repo/imagen-inventada.bin", b"\xff\xfe\x00\x01binario")
    rows = [{"repo": "acme-inventada/repo-a", "email": "ana@example.org", "path": "acme-inventada/repo-a/.env",
             "content": f"TOKEN=valor-en-claro-{i}\n", "n": i} for i in range(5)]
    single = tmp_path / "fuera" / "unidades.jsonl"
    _write(single, "".join(json.dumps(r) + "\n" for r in rows))
    secrets += [f"valor-en-claro-{i}" for i in range(5)] + ["ana@example.org", "acme-inventada"]
    reg = datos / "registro.json"
    entries = [
        {"name": "repos", "path": str(root), "kind": "dir", "sha256": "0" * 64, "sensitive": True},
        {"name": "unidades", "path": str(single), "kind": "file", "sha256": "0" * 64, "sensitive": True},
    ]
    reg.write_text(json.dumps(entries), encoding="utf-8")
    data_guard._MEMO.clear()
    return {"vault": vault, "root": root, "secrets": secrets, "reg": reg, "entries": entries,
            "sops": sops, "blob": blob, "rcpt": rcpt, "out": datos / "depuracion"}


def _all_text(folder: Path) -> str:
    return "\n".join(p.relative_to(folder).as_posix() + "\n" + p.read_text(encoding="utf-8")
                     for p in sorted(folder.rglob("*")) if p.is_file())


def test_dir_sample_redacts_keeps_formats_and_counts(env):
    m = ds.sample(env["vault"], "demo", "repos", 100, 7, None, False)
    assert m["disponibles"] == 9 and m["conservados"] == 7
    assert m["descartados"] == {"binario": 1, "grande": 1}
    out = env["out"] / "repos"
    text = _all_text(out)
    for s in env["secrets"]:
        assert s not in text
    assert "acme" not in text and "org-x" not in text
    assert env["sops"] in text and env["blob"] in text and env["rcpt"] in text
    assert "age-encryption.org/v1\n-> X25519 " in text and "[BINARIO 300 bytes]" in text
    names = {p.name for p in out.rglob("*") if p.is_file()}
    assert {"app.enc.yaml", "sealed.yaml", ".env", ".env.keys", "secret.age", "key.pem", "manifest.json"} <= names
    assert m["redacciones"].get("private_key") == 1
    assert json.loads((out / "manifest.json").read_text(encoding="utf-8")) == m


def test_cli_stdout_is_manifest_only(env):
    r = subprocess.run([sys.executable, str(SCRIPT), "--vault", str(env["vault"]), "--project", "demo",
                        "--dataset", "repos", "--n", "100", "--seed", "1"], capture_output=True)
    assert r.returncode == 0, r.stdout
    out = r.stdout.decode("utf-8")
    m = json.loads(out)
    assert m["conservados"] == 7
    for s in [*env["secrets"], "grande-inventado", "imagen-inventada", str(env["root"])]:
        assert s not in out
    manifest = (env["out"] / "repos" / "manifest.json").read_text(encoding="utf-8")
    assert "grande-inventado" not in manifest and "imagen" not in manifest


def test_same_seed_same_sample(env):
    a = ds.sample(env["vault"], "demo", "repos", 3, 42, None, False)
    first = _all_text(env["out"] / "repos").replace(a["creado"], "")
    b = ds.sample(env["vault"], "demo", "repos", 3, 42, None, True)
    assert _all_text(env["out"] / "repos").replace(b["creado"], "") == first


def test_glob_filters(env):
    m = ds.sample(env["vault"], "demo", "repos", 10, 1, "*.yaml", False)
    assert m["disponibles"] == 3 and m["conservados"] == 3


def test_residual_item_dropped_whole(env, monkeypatch):
    real = ds.sr.residual
    monkeypatch.setattr(ds.sr, "residual", lambda t, h="": "BEGIN CERTIFICATE" in t or "sops:" in t or real(t, h))
    m = ds.sample(env["vault"], "demo", "repos", 100, 7, None, False)
    assert m["descartados"]["residuo_tras_redactar"] == 1 and m["conservados"] == 6
    assert "sops:" not in _all_text(env["out"] / "repos")


def test_jsonl_rows_pseudonymised(env):
    m = ds.sample(env["vault"], "demo", "unidades", 3, 5, None, False)
    assert m["unidad"] == "fila" and m["conservados"] == 3 and m["disponibles"] == 5
    text = (env["out"] / "unidades" / "muestra.jsonl").read_text(encoding="utf-8")
    for s in env["secrets"]:
        assert s not in text
    rows = [json.loads(x) for x in text.splitlines()]
    assert all(r["path"].endswith("/.env") and r["path"].startswith("d-") for r in rows)
    assert all(r["repo"].startswith("repo-") and r["email"].startswith("[REDACTADO email") for r in rows)
    assert all("TOKEN=[REDACTADO" in r["content"] for r in rows)


@pytest.mark.parametrize("args, why", [
    (("demo", "no-existe"), "no está"),
    (("otro", "repos"), "no existe el proyecto"),
    (("demo", "../repos"), "nombres simples"),
])
def test_refusals(env, args, why):
    with pytest.raises(ds.Refused, match=why):
        ds.sample(env["vault"], args[0], args[1], 3, 1, None, False)


def test_refuses_unreadable_registry(env):
    env["reg"].write_text("{roto", encoding="utf-8")
    with pytest.raises(ds.Refused, match="ilegible"):
        ds.sample(env["vault"], "demo", "repos", 3, 1, None, False)


def test_refuses_existing_output(env):
    ds.sample(env["vault"], "demo", "repos", 2, 1, None, False)
    with pytest.raises(ds.Refused, match="--replace"):
        ds.sample(env["vault"], "demo", "repos", 2, 1, None, False)
    foreign = env["out"] / "unidades"
    foreign.mkdir(parents=True)
    (foreign / "notas.md").write_text("mío", encoding="utf-8")
    with pytest.raises(ds.Refused, match="no la toco"):
        ds.sample(env["vault"], "demo", "unidades", 2, 1, None, True)
    assert (foreign / "notas.md").read_text(encoding="utf-8") == "mío"


def test_refuses_output_inside_sensitive_root(env):
    inside = env["out"] / "repos"
    _write(inside / "x.txt", "dato")
    entries = env["entries"] + [{"name": "dentro", "path": str(env["out"]), "kind": "dir", "sha256": "0" * 64,
                                 "sensitive": True}]
    env["reg"].write_text(json.dumps(entries), encoding="utf-8")
    data_guard._MEMO.clear()
    with pytest.raises(ds.Refused, match="protegidos"):
        ds.sample(env["vault"], "demo", "repos", 2, 1, None, True)


def test_cli_refusal_exit_3(env):
    r = subprocess.run([sys.executable, str(SCRIPT), "--vault", str(env["vault"]), "--project", "demo",
                        "--dataset", "nada", "--n", "1", "--seed", "1"], capture_output=True)
    assert r.returncode == 3 and "error" in json.loads(r.stdout.decode("utf-8"))


def test_data_guard_allows_the_sampler_and_blocks_direct_reads(env):
    cmd = (f'python "{SCRIPT}" --vault "{env["vault"]}" --project demo --dataset repos --n 5 --seed 1')
    ev = {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(env["vault"])}
    assert data_guard.decide(ev, env["vault"]) is None
    target = env["root"] / "acme-inventada" / "repo-b" / ".env"
    ev2 = {"tool_name": "Bash", "tool_input": {"command": f'cat "{target}"'}, "cwd": str(env["vault"])}
    assert data_guard.decide(ev2, env["vault"])
    ev3 = {"tool_name": "Read", "tool_input": {"file_path": str(target)}, "cwd": str(env["vault"])}
    assert data_guard.decide(ev3, env["vault"])
