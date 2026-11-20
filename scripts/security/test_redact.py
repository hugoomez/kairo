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


def test_unterminated_block_plain_text():
    # a header with no END is redacted with the body lines that follow it, and no further
    head = DASHES + "BEGIN OPENSSH PRIVATE KEY" + DASHES + "\n" + base64.b64encode(os.urandom(60)).decode()
    text = "antes\n" + head + "\nresto que no se va\n"
    out, findings = redact.redact_text(text)
    assert out == "antes\n[REDACTADO private_key sha256:" + sha12(head) + "]\nresto que no se va\n"
    assert len(findings) == 1


def test_unterminated_header_three_body_lines_then_prose():
    body = base64.b64encode(os.urandom(144)).decode()
    lines = [body[i:i + 64] for i in range(0, len(body), 64)]
    assert len(lines) == 3
    head = DASHES + "BEGIN RSA PRIVATE KEY" + DASHES + "\n" + "\n".join(lines)
    text = "nota previa\n" + head + "\nAqui sigue la prosa del cuaderno.\nY otra linea.\n"
    out, findings = redact.redact_text(text)
    assert out == ("nota previa\n[REDACTADO private_key sha256:" + sha12(head) + "]\n"
                   "Aqui sigue la prosa del cuaderno.\nY otra linea.\n")
    assert len(findings) == 1


def test_unterminated_with_proc_type_headers():
    body = base64.b64encode(os.urandom(96)).decode()
    head = (DASHES + "BEGIN RSA PRIVATE KEY" + DASHES + "\nProc-Type: 4,ENCRYPTED\n"
            "DEK-Info: AES-128-CBC,00112233445566778899AABBCCDDEEFF\n\n" + body[:64] + "\n" + body[64:])
    out, findings = redact.redact_text(head + "\nfin del texto")
    assert out == "[REDACTADO private_key sha256:" + sha12(head) + "]\nfin del texto"
    assert len(findings) == 1


def test_header_alone_in_prose_not_redacted():
    header = DASHES + "BEGIN OPENSSH PRIVATE KEY" + DASHES
    text = ("El codebook marca las lineas que empiezan por " + header + " como clave.\n"
            "Despues viene el resto de la nota, que no es secreto.\n")
    out, findings = redact.redact_text(text)
    assert out == text and findings == []
    text2 = header + "\nEsta frase no es el cuerpo de una clave.\n"
    assert redact.redact_text(text2) == (text2, [])


def _key_lines(n=3):
    body = base64.b64encode(os.urandom(48 * n)).decode()
    return [body[i:i + 64] for i in range(0, len(body), 64)]


def test_unterminated_indented_block():
    header = DASHES + "BEGIN OPENSSH PRIVATE KEY" + DASHES
    lines = _key_lines()
    span = header + "".join("\n    " + ln for ln in lines)
    text = "Ejemplo:\n\n    " + span + "\nSigue la nota.\n"
    out, findings = redact.redact_text(text)
    assert out == "Ejemplo:\n\n    [REDACTADO private_key sha256:" + sha12(span) + "]\nSigue la nota.\n"
    assert len(findings) == 1 and redact.has_secret(text)


def test_unterminated_blockquoted_block():
    header = DASHES + "BEGIN RSA PRIVATE KEY" + DASHES
    lines = _key_lines()
    span = header + "".join("\n> " + ln for ln in lines)
    text = "> " + span + "\n> Gracias por revisarlo.\n"
    out, findings = redact.redact_text(text)
    assert out == "> [REDACTADO private_key sha256:" + sha12(span) + "]\n> Gracias por revisarlo.\n"
    assert len(findings) == 1


def test_unterminated_escaped_newlines_in_raw_json():
    header = DASHES + "BEGIN RSA PRIVATE KEY" + DASHES
    lines = _key_lines()
    raw = json.dumps({"content": "pegado " + header + "\n" + "\n".join(lines)})
    assert "\\n" in raw
    out, findings = redact.redact_text(raw)
    assert len(findings) == 1 and redact.has_secret(raw)
    for ln in lines:
        assert ln not in out
    assert json.loads(out)["content"].startswith("pegado [REDACTADO private_key sha256:")


def test_unterminated_truncated_first_line():
    header = DASHES + "BEGIN OPENSSH PRIVATE KEY" + DASHES
    run = base64.b64encode(os.urandom(30)).decode().rstrip("=")
    for tail in ("-", "…", "[truncated]"):
        text = header + "\n" + run + tail + "\nfin"
        out, findings = redact.redact_text(text)
        assert out == "[REDACTADO private_key sha256:" + sha12(header + "\n" + run) + "]" + tail + "\nfin"
        assert len(findings) == 1 and redact.has_secret(text)


def test_unterminated_truncated_last_line():
    header = DASHES + "BEGIN RSA PRIVATE KEY" + DASHES
    full = _key_lines(1)[0]
    last = base64.b64encode(os.urandom(9)).decode()[:10]
    for tail in ("", "…", "-", "[truncated]", "…\nprosa"):
        span = header + "\n" + full + "\n" + last
        text = span + tail
        out, findings = redact.redact_text(text)
        assert out == "[REDACTADO private_key sha256:" + sha12(span) + "]" + tail, tail
        assert len(findings) == 1 and redact.has_secret(text)
    # prose after the body is not taken as a truncated line
    text = header + "\n" + full + "\nAqui sigue, la nota.\n"
    assert redact.redact_text(text)[0] == ("[REDACTADO private_key sha256:" + sha12(header + "\n" + full)
                                           + "]\nAqui sigue, la nota.\n")


def test_header_then_short_word_not_redacted():
    header = DASHES + "BEGIN OPENSSH PRIVATE KEY" + DASHES
    for text in (header + "\nGracias\n", header + "\nGracias", header + "\n> Gracias\n"):
        assert redact.redact_text(text) == (text, [])
        assert not redact.has_secret(text)


def test_redact_json_keeps_valid_json():
    block = fake_block()
    open_head = DASHES + "BEGIN RSA PRIVATE KEY" + DASHES + "\n" + base64.b64encode(os.urandom(60)).decode()
    doc = {"messages": [
        {"role": "user", "content": "mira\n" + block + "\nfin"},
        {"role": "tool", "content": "cortado " + open_head},
        {"role": "assistant", "content": "mensaje posterior intacto", "n": 3, "ok": True},
    ]}
    res = run_cli("redact-json", json.dumps(doc))
    assert res.returncode == 0
    out = json.loads(res.stdout.decode("utf-8"))
    msgs = out["messages"]
    assert msgs[0]["content"] == "mira\n[REDACTADO private_key sha256:" + sha12(block) + "]\nfin"
    assert msgs[1]["content"] == "cortado [REDACTADO private_key sha256:" + sha12(open_head) + "]"
    assert msgs[2] == doc["messages"][2]
    assert block.splitlines()[1] not in res.stdout.decode("utf-8")


def test_redact_json_invalid():
    res = run_cli("redact-json", "{no es json")
    assert res.returncode == 1
    assert res.stdout == b""
    assert len(res.stderr.decode().strip().splitlines()) == 1


def test_overlap_nested():
    pw = "Zq8vK2mXr7TpL4nB9wYc"
    inner = DASHES + "BEGIN RSA PRIVATE KEY" + DASHES + "\nAPI_KEY=" + pw + "\n" + DASHES + "END RSA PRIVATE KEY" + DASHES
    out, findings = redact.redact_text("a " + inner + " b")
    assert out == "a [REDACTED_PLACEHOLDER] b".replace("[REDACTED_PLACEHOLDER]", "[REDACTADO private_key sha256:" + sha12(inner) + "]")
    assert [f["type"] for f in findings] == ["private_key"]
    url = "TOKEN=https://user:" + pw + "@host.example/x"
    out2, f2 = redact.redact_text(url)
    assert pw not in out2 and len(f2) == 1 and len(re.findall(r"\[REDACTADO", out2)) == 1
    assert redact.redact_text(url) == (out2, f2)


# ---- base64-encoded keys and dotenvx key files ------------------------------

def test_base64_pem_in_yaml_redacted():
    enc = base64.b64encode((fake_block("RSA PRIVATE KEY") + "\n").encode()).decode()
    text = "apiVersion: v1\nkind: Secret\ndata:\n  tls.key: " + enc + "\n  otro: x\n"
    out, findings = redact.redact_text(text)
    assert enc not in out and enc[:40] not in out
    assert "[REDACTADO private_key sha256:" + sha12(enc) + "]" in out
    assert "otro: x" in out and redact.has_secret(text)
    assert run_cli("check", text).returncode == 3


def test_urlsafe_unpadded_base64_key_redacted():
    enc = base64.urlsafe_b64encode((fake_block() + "\n").encode()).decode().rstrip("=")
    out, findings = redact.redact_text("k=" + enc)
    assert enc not in out and findings[0]["type"] == "private_key"


def test_base64_age_secret_key_redacted():
    key = "AGE-SECRET-KEY-1" + "".join("QPZRY9X8GF2TVDW0S3JN54KHCE6MUA7L"[b % 32] for b in os.urandom(58))
    enc = base64.b64encode(("# created: 2031-01-01\n" + key + "\n").encode()).decode()
    out, _ = redact.redact_text("keys.txt: " + enc)
    assert enc not in out


def test_post_quantum_age_identity_redacted():
    key = "AGE-SECRET-KEY-PQ-1" + "".join("QPZRY9X8GF2TVDW0S3JN54KHCE6MUA7L"[b % 32] for b in os.urandom(58))
    out, findings = redact.redact_text("# public key: age1pq1inventado\n" + key + "\n")
    assert key not in out and findings == [{"type": "age_secret_key", "sha256": sha12(key)}]
    enc = base64.b64encode((key + "\n").encode()).decode()
    assert enc not in redact.redact_text("pq.txt: " + enc)[0]
    assert run_cli("check", key).returncode == 3


def test_base64_certificate_not_redacted():
    cert = (DASHES + "BEGIN CERTIFICATE" + DASHES + "\n" + base64.b64encode(os.urandom(90)).decode()
            + "\n" + DASHES + "END CERTIFICATE" + DASHES + "\n")
    enc = base64.b64encode(cert.encode()).decode()
    assert enc.startswith("LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0t")
    text = "  tls.crt: " + enc + "\n"
    assert redact.redact_text(text) == (text, [])


def test_dotenvx_private_key_redacted_public_kept():
    priv = os.urandom(32).hex()
    pub = "02" + os.urandom(32).hex()
    keys = ('#/ private decryption keys /\nDOTENV_PRIVATE_KEY_PRODUCTION="' + priv + '"\n')
    out, findings = redact.redact_text(keys)
    assert priv not in out and findings[0]["type"] == "api_key"
    assert 'DOTENV_PRIVATE_KEY_PRODUCTION="[REDACTADO' in out
    env = 'DOTENV_PUBLIC_KEY_PRODUCTION="' + pub + '"\nHELLO="encrypted:BAzP1aQ"\n'
    assert redact.redact_text(env) == (env, [])


def test_existing_formats_still_redacted():
    for label in ("OPENSSH PRIVATE KEY", "PGP PRIVATE KEY BLOCK", "PRIVATE KEY", "EC PRIVATE KEY"):
        block = fake_block(label)
        out, findings = redact.redact_text("x\n" + block + "\n")
        assert block not in out and findings[0]["type"] == "private_key", label
    key = "AGE-SECRET-KEY-1" + "".join("QPZRY9X8GF2TVDW0S3JN54KHCE6MUA7L"[b % 32] for b in os.urandom(58))
    out, findings = redact.redact_text(key)
    assert key not in out and findings[0]["type"] == "age_secret_key"


def _sealed(n: int, seed: int = 7) -> str:
    import random
    rnd = random.Random(seed)
    body = bytes(rnd.randrange(256) for _ in range(n + 48))
    return base64.b64encode(n.to_bytes(2, "big") + body).decode()


def test_sealed_secret_ciphertext_kept_under_secret_names():
    ct = _sealed(512)
    text = f"spec:\n  encryptedData:\n    password: {ct}\n    api-token: {ct}\n"
    out, found = redact.redact_text(text)
    assert out == text and found == []
    assert redact.is_public_ciphertext(ct)


def test_lookalike_not_public_ciphertext():
    # wrong length prefix, too short, or a real-looking password: still redacted
    bad = base64.b64encode((300).to_bytes(2, "big") + b"x" * 400).decode()
    assert not redact.is_public_ciphertext(bad)
    assert not redact.is_public_ciphertext(_sealed(512)[:120])
    out, _ = redact.redact_text("password: Zq8!vT2#pL9@wR4m\n")
    assert "Zq8!vT2" not in out


def test_encoded_private_key_never_passes_as_ciphertext():
    pem = b"-----BEGIN PRIVATE KEY-----\n" + b"A" * 600
    v = base64.b64encode((256).to_bytes(2, "big") + pem).decode()
    assert not redact.is_public_ciphertext(v)


def test_sealed_secret_ciphertext_kept_inside_a_json_line():
    ct = _sealed(512, seed=9)
    text = f"spec:\n  encryptedData:\n    password: {ct}\n    api-token: {ct}\n"
    line = json.dumps({"id": "x", "content": text}, ensure_ascii=False)
    assert not redact.has_secret(line)
    assert redact.redact_text(line)[1] == []
