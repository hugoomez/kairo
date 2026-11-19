"""Tests for strict_redact.py. Invented material only, built at run time (fake_material)."""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_material as fm  # noqa: E402
import strict_redact as sr  # noqa: E402


def test_sops_file_keeps_ciphertext_recipient_and_fingerprint():
    rcpt, fp = fm.age_recipient(), os.urandom(20).hex().upper()
    text = fm.sops_yaml(rcpt, fp)
    out, findings = sr.redact_strict(text, "secrets/app.enc.yaml")
    assert out == text, findings
    assert not sr.residual(out, "app.enc.yaml")


def test_sealed_secret_encrypted_data_kept():
    blob = "AgB" + fm.b64(300)
    text = fm.sealed_secret(blob)
    out, findings = sr.redact_strict(text, "sealed.yaml")
    assert blob in out and findings == []


def test_plain_k8s_secret_values_redacted():
    pw = base64.b64encode(b"hunter2").decode()
    tok = fm.b64(32)
    text = fm.k8s_secret(pw, tok, "admin-inventado")
    out, findings = sr.redact_strict(text, "secret.yaml")
    for v in (pw, tok, "admin-inventado"):
        assert v not in out
    assert len(findings) == 3 and "k8s_secret_value" in {f["type"] for f in findings}
    assert "name: inventado" in out and not sr.residual(out, "secret.yaml")


def test_json_k8s_secret_values_redacted():
    doc = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "x"},
           "data": {"password": base64.b64encode(b"clave-corta").decode()}}
    text = json.dumps(doc, indent=2)
    out, findings = sr.redact_strict(text, "secret.json")
    assert doc["data"]["password"] not in out
    assert json.loads(out)["data"]["password"].startswith("[REDACTADO ")
    assert json.loads(out)["metadata"] == {"name": "x"}


def test_env_plaintext_values_redacted_ciphertext_and_public_kept():
    pub = "02" + os.urandom(32).hex()
    cipher = "encrypted:" + fm.b64(60)
    text = ("# comentario inventado\nDB_PASSWORD=hunter2\nexport TOKEN=\"abc\"\nAPI_URL=https://api.example.invalid\n"
            f"HELLO=\"{cipher}\"\nDOTENV_PUBLIC_KEY=\"{pub}\"\nVACIA=\n")
    out, findings = sr.redact_strict(text, "app/.env.production")
    for v in ("hunter2", '"abc"', "api.example.invalid"):
        assert v not in out
    assert cipher in out and pub in out and "# comentario inventado" in out and "VACIA=\n" in out
    assert not sr.residual(out, ".env.production")


def test_env_rule_only_for_env_files():
    out, _ = sr.redact_strict("NAME=valor-legible\n", "config.txt")
    assert out == "NAME=valor-legible\n"
    assert sr.is_env_name(".env") and sr.is_env_name("x/.env.keys") and sr.is_env_name("prod.env")
    assert not sr.is_env_name("environment.yaml")


def test_env_keys_private_redacted():
    priv = os.urandom(32).hex()
    out, _ = sr.redact_strict(f'DOTENV_PRIVATE_KEY_PRODUCTION="{priv}"\n', ".env.keys")
    assert priv not in out


def test_high_entropy_redacted_paths_kept():
    tok = "aZ3" + fm.b64(30).replace("=", "")
    hexs = os.urandom(20).hex()
    text = f"nota: {tok}\ncommit {hexs}\nruta: k8s/overlays/production/values.yaml\nv: v1.2.3-beta\n"
    out, findings = sr.redact_strict(text, "notes.txt")
    assert tok not in out and hexs not in out
    assert "k8s/overlays/production/values.yaml" in out and "v1.2.3-beta" in out
    assert {f["type"] for f in findings} == {"high_entropy"}


def test_public_forms_kept():
    cert = fm.block("CERTIFICATE")
    pgp = fm.block("PGP PUBLIC KEY BLOCK")
    ssh = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA" + fm.b64(24).rstrip("=") + " clave-inventada"
    cert_b64 = base64.b64encode((cert + "\n").encode()).decode()
    text = f"{cert}\n{pgp}\n{ssh}\ntls.crt: {cert_b64}\n"
    out, findings = sr.redact_strict(text, "k.yaml")
    assert out == text, findings


def test_private_key_next_to_public_forms_redacted():
    key = fm.block("RSA PRIVATE KEY")
    agek = fm.age_secret()
    text = fm.block("CERTIFICATE") + "\n" + key + "\n# public key: " + fm.age_recipient() + "\n" + agek + "\n"
    out, _ = sr.redact_strict(text, "k.pem")
    assert key not in out and agek not in out and key.splitlines()[1] not in out
    assert not sr.residual(out, "k.pem")


def test_pseudonymise_repos_and_emails():
    text = ("origin https://github.com/Acme-Inventada/Repo.Demo.git\n"
            "git@github.com:acme-inventada/repo.demo.git\nautor: Ana.Inventada@example.org\n"
            "copia: ana.inventada@example.org\n")
    out, findings = sr.redact_strict(text, "notas.txt")
    assert "Acme" not in out and "acme" not in out and "Ana" not in out and "@example" not in out
    lines = out.splitlines()
    a = lines[0].split("github.com/")[1]
    b = lines[1].split("github.com:")[1]
    assert a == b and a.startswith("owner-") and a.endswith(".git")
    assert lines[2].split(": ")[1] == lines[3].split(": ")[1]
    assert sr.redact_strict(out, "notas.txt") == (out, [])


def test_redact_strict_is_a_fixed_point_on_mixed_input():
    text = (fm.k8s_secret(fm.b64(9), fm.b64(32), "u") + "---\n" + fm.sops_yaml(fm.age_recipient(), "AB" * 20)
            + "# " + fm.b64(40) + "\n")
    out, _ = sr.redact_strict(text, "mixto.yaml")
    assert not sr.residual(out, "mixto.yaml")


def test_pseudo_path():
    p = sr.pseudo_path("acme-inventada/repo/abc/k8s/secret.enc.yaml")
    parts = p.split("/")
    assert len(parts) == 5 and parts[-1] == "secret.enc.yaml" and all(x.startswith("d-") for x in parts[:-1])
    assert sr.pseudo_path("acme-inventada/repo/abc/k8s/secret.enc.yaml") == p
    weird = "x/" + "aZ9" + fm.b64(24).replace("/", "").replace("+", "") + ".txt"
    assert sr.pseudo_path(weird).split("/")[-1].startswith("f-")
