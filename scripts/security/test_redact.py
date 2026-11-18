"""Tests for redact.py. Key-shaped strings are assembled here from pieces and random
bytes; no literal key block lives in this file and nothing is written to disk."""
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import redact  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "redact.py"
DASHES = "-" * 5


def fake_block(label="OPENSSH PRIVATE KEY"):
    body = base64.b64encode(os.urandom(120)).decode()
    lines = [body[i:i + 64] for i in range(0, len(body), 64)]
    return (DASHES + "BEGIN " + label + DASHES + "\n" + "\n".join(lines) + "\n"
            + DASHES + "END " + label + DASHES)


def sha12(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def run_cli(mode, text):
    return subprocess.run([sys.executable, str(SCRIPT), mode], input=text.encode("utf-8"),
                          capture_output=True)


def test_pem_block_replaced():
    block = fake_block()
    text = "antes\n" + block + "\ndespues\n"
    out, findings = redact.redact_text(text)
    assert out == "antes\n[REDACTADO private_key sha256:" + sha12(block) + "]\ndespues\n"
    assert findings == [{"type": "private_key", "sha256": sha12(block)}]


def test_pgp_and_rsa_blocks():
    for label in ("RSA PRIVATE KEY", "PGP PRIVATE KEY BLOCK"):
        block = fake_block(label)
        out, findings = redact.redact_text("x " + block + " y")
        assert out == "x [REDACTADO private_key sha256:" + sha12(block) + "] y"
        assert findings[0]["type"] == "private_key"


def test_age_key_replaced():
    key = "AGE-SECRET-KEY-1" + "QPZRY9X8GF2TVDW0S3JN54KHCE6MUA7L"[:29] * 2
    key = key[:16 + 58]
    assert len(key) == 16 + 58
    out, findings = redact.redact_text("clave: " + key + "\n")
    assert key not in out
    assert findings == [{"type": "age_secret_key", "sha256": sha12(key)}]


def test_public_material_untouched():
    pub = "age1" + "qpzry9x8gf2tvdw0s3jn54khce6mua7l" * 2
    pem = (DASHES + "BEGIN PUBLIC KEY" + DASHES + "\nAAAAB3NzaC1yc2EAAAADAQABAAABAQ\n"
           + DASHES + "END PUBLIC KEY" + DASHES + "\n")
    text = pub + "\n" + pem
    out, findings = redact.redact_text(text)
    assert out == text and findings == []
    assert not redact.has_secret(text)


def test_credential_url_and_assignment():
    pw = "Zq8vK2mXr7TpL4nB9wYc"
    text = "remote https://user:" + pw + "@host.example/repo.git\nAPI_KEY = \"" + pw + "\"\n"
    out, findings = redact.redact_text(text)
    assert pw not in out
    assert {f["type"] for f in findings} >= {"credential_url", "api_key"}
    assert "https://user:" in out and "@host.example" in out


def test_check_exit_codes():
    clean = run_cli("check", "texto limpio sin nada\n")
    assert clean.returncode == 0
    assert json.loads(clean.stdout)["findings"] == []
    block = fake_block()
    body = block.splitlines()[1]
    dirty = run_cli("check", "a\n" + block + "\n")
    assert dirty.returncode == 3
    assert body.encode() not in dirty.stdout + dirty.stderr
    data = json.loads(dirty.stdout)
    assert data["findings"] == [{"type": "private_key", "sha256": sha12(block)}]


def test_redact_cli():
    block = fake_block()
    res = run_cli("redact", "a\n" + block + "\nb\n")
    assert res.returncode == 0
    assert res.stdout.decode() == "a\n[REDACTADO private_key sha256:" + sha12(block) + "]\nb\n"


def test_idempotent():
    text = "uno " + fake_block() + "\nTOKEN=Zq8vK2mXr7TpL4nB9wYc\n" + "AGE-SECRET-KEY-1" + "Q" * 58
    once = redact.redact_text(text)[0]
    assert redact.redact_text(once)[0] == once
    assert not redact.has_secret(once)
    assert re.search(r"\[REDACTADO \w+ sha256:[0-9a-f]{12}\]", once)


def test_check_bundle_exposes_patterns():
    import check_bundle
    assert all(isinstance(n, str) and hasattr(rx, "finditer") for n, rx in check_bundle.PATTERNS)
