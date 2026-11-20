"""Tests for fixtures_corpus.py. Everything is generated into tmp_path; no network."""
from __future__ import annotations

import base64
import configparser
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import debug_sample as ds  # noqa: E402
import fixtures_corpus as fc  # noqa: E402
import redact  # noqa: E402
import strict_redact as sr  # noqa: E402

HERE = Path(__file__).resolve().parent
REQUIRED = {"path": str, "tool": str, "format": str, "kind": str, "valid": bool, "algorithms": list,
            "recipients": list, "has_private_material": bool, "public_key_recoverable_from": list,
            "pq": bool, "notes": str}
PUBLIC_TOOLS = {"sops", "age", "agenix", "sealed-secrets", "dotenvx"}


def _run(out: Path, seed: int, k: int, capsys, *extra: str) -> tuple[int, dict]:
    code = fc.main(["--out", str(out), "--seed", str(seed), "--n-per-kind", str(k), *extra])
    return code, json.loads(capsys.readouterr().out)


def _truth(out: Path) -> list[dict]:
    return [json.loads(ln) for ln in (out / "truth.jsonl").read_text(encoding="utf-8").splitlines()]


def _files(out: Path) -> dict[str, bytes]:
    return {p.relative_to(out).as_posix(): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> tuple[Path, list[dict]]:
    out = tmp_path_factory.mktemp("corpus")
    fc.write(out, fc.build(5, 1))
    return out, _truth(out)


def test_cli_writes_files_truth_and_counts(tmp_path, capsys):
    out = tmp_path / "c"
    code, summary = _run(out, 3, 1, capsys)
    assert code == 0
    truth = _truth(out)
    assert summary["files"] == len(truth) == len(_files(out)) - 1
    assert summary["private"] == sum(t["has_private_material"] for t in truth) > 0
    assert summary["pq"] == sum(t["pq"] for t in truth) > 0
    assert summary["invalid"] == sum(not t["valid"] for t in truth) > 0
    assert sum(summary["by_tool"].values()) == len(truth)
    assert set(summary) == {"files", "seed", "n_per_kind", "by_tool", "private", "pq", "invalid"}
    for t in truth:
        assert set(t) == set(REQUIRED)
        for k, typ in REQUIRED.items():
            assert isinstance(t[k], typ), (t["path"], k)
        assert all(set(r) == {"type", "value_kind"} for r in t["recipients"])
        assert (out / t["path"]).is_file()


def test_deterministic_for_a_seed(tmp_path, capsys):
    _run(tmp_path / "a", 42, 2, capsys)
    _run(tmp_path / "b", 42, 2, capsys)
    _run(tmp_path / "c", 43, 2, capsys)
    a, b, c = _files(tmp_path / "a"), _files(tmp_path / "b"), _files(tmp_path / "c")
    assert a == b
    assert a.keys() == c.keys() and a != c


def test_refuses_a_non_empty_folder_and_replaces_a_corpus(tmp_path, capsys):
    other = tmp_path / "otra"
    other.mkdir()
    (other / "nota.txt").write_text("no es un corpus", encoding="utf-8")
    code, err = _run(other, 1, 1, capsys)
    assert code == 3 and "error" in err
    code, err = _run(other, 1, 1, capsys, "--replace")
    assert code == 3 and (other / "nota.txt").exists()
    out = tmp_path / "c"
    assert _run(out, 1, 1, capsys)[0] == 0
    (out / "sobra.txt").write_text("x", encoding="utf-8")
    assert _run(out, 1, 1, capsys)[0] == 3
    assert _run(out, 2, 1, capsys, "--replace")[0] == 0
    assert not (out / "sobra.txt").exists()


def test_covers_every_tool_format_and_pq(corpus):
    out, truth = corpus
    tools = {t["tool"] for t in truth}
    assert PUBLIC_TOOLS <= tools
    formats = {t["format"] for t in truth}
    assert {"sops-yaml", "sops-json", "sops-dotenv", "sops-ini", "sops-binary", "sops-config", "age-binary",
            "age-armored", "age-recipients", "age-identity", "sealedsecret-yaml", "sealedsecret-json", "x509-pem",
            "k8s-secret-tls", "agenix-secrets-nix", "dotenvx-env", "dotenvx-keys", "openssh-private", "pkcs8-pem",
            "yaml-plaintext", "dotenv-plaintext"} <= formats
    stanzas = Counter()
    for t in truth:
        data = (out / t["path"]).read_bytes()
        stanzas.update(m.decode() for m in re.findall(rb"(?m)^-> ([^ \n]+)", data))
        if t["pq"]:
            assert b"mlkem768" in data or b"age1pq1" in data or b"AGE-SECRET-KEY-PQ-1" in data, t["path"]
    assert {"X25519", "ssh-ed25519", "ssh-rsa", "scrypt", "mlkem768x25519", "p256tag", "mlkem768p256tag",
            "piv-p256"} <= set(stanzas)
    rtypes = {r["type"] for t in truth for r in t["recipients"]}
    assert {"age-x25519", "pgp", "aws_kms", "gcp_kms", "azure_kv", "hc_vault", "ssh-ed25519",
            "age-mlkem768x25519", "sealed-secrets-controller", "dotenvx-secp256k1"} <= rtypes


def test_every_private_fixture_is_caught_by_redact_check(corpus):
    out, truth = corpus
    private = [t for t in truth if t["has_private_material"]]
    assert len(private) >= 9
    for t in private:
        r = subprocess.run([sys.executable, str(HERE / "redact.py"), "check"], input=(out / t["path"]).read_bytes(),
                           capture_output=True)
        assert r.returncode == 3, t["path"]
        assert json.loads(r.stdout)["findings"]


def test_placeholder_docs_are_not_private(corpus):
    out, truth = corpus
    for t in truth:
        if t["format"] in ("doc-placeholders", "dotenv-placeholders", "yaml-sops-key"):
            assert not redact.has_secret((out / t["path"]).read_text(encoding="utf-8")), t["path"]


def _public_forms(text: str, path: str) -> list[str]:
    """The public / ciphertext forms a parser needs, as they appear in the source file."""
    forms = re.findall(r"ENC\[[^\]\n]*\]", text)
    forms += re.findall(r"(?<![A-Za-z0-9])age1[0-9a-z]{20,}", text)
    forms += re.findall(r"encrypted:[A-Za-z0-9+/=]+", text)
    forms += [m.group(0) for m in re.finditer(r"-----BEGIN (AGE ENCRYPTED FILE|CERTIFICATE)-----.*?-----END \1-----",
                                               text, re.S)]
    forms += re.findall(r"(?:fp|pgp)\"?\s*[:=]\s*\"?([0-9A-F]{40})", text)
    forms += re.findall(r"(?m)^\s*- ([0-9A-F]{40})$", text)
    forms += re.findall(r"ssh-(?:ed25519|rsa) AAAA[A-Za-z0-9+/]+=*", text)
    if path.endswith(".json") and '"encryptedData"' in text:
        forms += list(json.loads(text)["spec"]["encryptedData"].values())
    elif "kind: SealedSecret" in text:
        forms += re.findall(r"(?m)^    [\w.\-]+: (Ag[A-Za-z0-9+/=]+)$", text)
    return forms


def test_debug_sample_keeps_the_public_structure(corpus):
    out, truth = corpus
    checked = Counter()
    for t in truth:
        if t["has_private_material"] or not t["valid"] or t["tool"] not in PUBLIC_TOOLS:
            continue
        raw = (out / t["path"]).read_bytes()
        item = ds._file_item(out / t["path"], t["path"], Counter(), Counter())
        assert item is not None, t["path"]
        sample = item[1]
        if raw.startswith(ds.AGE_MAGIC):
            header = raw[: raw.index(b"\n---")].decode()
            assert sample.startswith(header) and "[BINARIO " in sample, t["path"]
            checked["age-binary"] += 1
            continue
        forms = _public_forms(raw.decode("utf-8"), t["path"])
        assert forms, t["path"]
        for f in forms:
            assert f in sample, (t["path"], f[:40])
        checked[t["format"]] += 1
    assert checked["age-binary"] >= 8 and checked["sops-dotenv"] and checked["sops-ini"] and checked["sops-json"]
    assert checked["sealedsecret-json"] and checked["sealedsecret-yaml"] and checked["dotenvx-env"]
    assert checked["sops-config"] and checked["sops-binary"] and checked["age-recipients"]


def test_debug_sample_strips_the_private_material(corpus):
    out, truth = corpus
    for t in truth:
        if not t["has_private_material"]:
            continue
        item = ds._file_item(out / t["path"], t["path"], Counter(), Counter())
        if item is None:
            continue  # dropped whole: fail closed
        assert not sr.residual(item[1], Path(t["path"]).name), t["path"]
        text = (out / t["path"]).read_text(encoding="utf-8")
        for secret in re.findall(r"AGE-SECRET-KEY-(?:PQ-)?1[0-9A-Z]{58}|\"([0-9a-f]{64})\"", text):
            assert not secret or secret not in item[1]


def test_key_material_is_well_formed_and_consistent(corpus):
    out, truth = corpus
    seen = Counter()
    for t in truth:
        data = (out / t["path"]).read_bytes()
        text = data.decode("utf-8", "replace")
        for s in re.findall(r"(?<![A-Za-z0-9])age1[0-9a-z]{20,}|AGE-SECRET-KEY-(?:PQ-)?1[0-9A-Z]+", text):
            assert fc.bech32_decode(s) is not None, (t["path"], s[:30])
            seen["bech32"] += 1
        if t["format"] == "age-identity" and not t["pq"]:
            secret = re.search(r"AGE-SECRET-KEY-1\w+", text).group(0)
            public = re.search(r"# public key: (age1\w+)", text).group(1)
            assert fc.bech32_decode(public) == ("age", fc.x25519_base(fc.bech32_decode(secret)[1]))
            seen["age-pair"] += 1
        if t["format"] == "age-identity" and t["pq"]:
            hrp, seed = fc.bech32_decode(re.search(r"AGE-SECRET-KEY-PQ-1\w+", text).group(0))
            assert hrp == "age-secret-key-pq-" and len(seed) == 32
            assert len(fc.bech32_decode(re.search(r"age1pq1\w+", text).group(0))[1]) == 1216
        if t["format"] == "dotenvx-keys":
            folder = (out / t["path"]).parent
            for suffix, fname in (("", ".env"), ("_PRODUCTION", ".env.production")):
                priv = re.search(rf'DOTENV_PRIVATE_KEY{suffix}="([0-9a-f]{{64}})"', text).group(1)
                env = (folder / fname).read_text(encoding="utf-8")
                pub = re.search(rf'DOTENV_PUBLIC_KEY{suffix}="([0-9a-f]{{66}})"', env).group(1)
                assert fc.secp256k1_public(bytes.fromhex(priv)).hex() == pub
                for v in re.findall(r'"encrypted:([^"]+)"', env):
                    assert base64.b64decode(v)[0] == 4  # uncompressed ephemeral point
                seen["dotenvx-pair"] += 1
        if t["format"] == "ssh-public":
            line = text.split()
            priv = (out / t["path"]).with_suffix("").read_text(encoding="utf-8")
            blob = base64.b64decode("".join(priv.splitlines()[1:-1]))
            assert blob.startswith(b"openssh-key-v1\x00") and base64.b64decode(line[1]) in blob
            wire = base64.b64decode(line[1])
            assert blob.count(wire[-32:]) >= 2  # public key in the header and in the private section
            seen["ssh-pair"] += 1
    assert seen["bech32"] > 20 and seen["age-pair"] and seen["dotenvx-pair"] == 2 and seen["ssh-pair"]


def _age_header(data: bytes) -> list[str]:
    if data.startswith(b"-----BEGIN AGE ENCRYPTED FILE-----"):
        body = data.decode().strip().splitlines()[1:-1]
        assert all(len(ln) == 64 for ln in body[:-1]) and 0 < len(body[-1]) <= 64
        data = base64.b64decode("".join(body))
    end = data.index(b"\n--- ")
    head = data[:end].decode().split("\n")
    mac = data[end + 5: data.index(b"\n", end + 1)]
    assert len(mac) == 43 and len(base64.b64decode(mac + b"=")) == 32
    return head


_ARG_BYTES = {"X25519": [32], "ssh-ed25519": [4, 32], "ssh-rsa": [4], "scrypt": [16, None],
              "mlkem768x25519": [1120], "p256tag": [4, 65], "mlkem768p256tag": [4, 1153], "piv-p256": [4, 33]}


def _unb64(s: str) -> bytes:
    return base64.b64decode(s + "=" * (-len(s) % 4))


def test_age_headers_follow_the_spec(corpus):
    out, truth = corpus
    n = 0
    for t in truth:
        if t["format"] not in ("age-binary", "age-armored"):
            continue
        head = _age_header((out / t["path"]).read_bytes())
        assert head[0] == "age-encryption.org/v1"
        i, types = 1, []
        while i < len(head):
            assert head[i].startswith("-> "), t["path"]
            args = head[i][3:].split(" ")
            types.append(args[0])
            for want, a in zip(_ARG_BYTES.get(args[0], []), args[1:]):
                if want:
                    assert len(_unb64(a)) == want, (t["path"], args[0])
            i += 1
            body = []
            while i < len(head) and not head[i].startswith("-> "):
                body.append(head[i])
                i += 1
            assert all(len(ln) == 64 for ln in body[:-1]) and len(body[-1]) < 64
            if args[0] in ("X25519", "ssh-ed25519", "scrypt", "mlkem768x25519", "p256tag", "piv-p256"):
                assert len(_unb64("".join(body))) == 32
        if "scrypt" in types:
            assert types == ["scrypt"]
        n += 1
    assert n >= 10


def test_structured_files_parse(corpus):
    out, truth = corpus
    yaml = pytest.importorskip("yaml")
    n = Counter()
    for t in truth:
        p = out / t["path"]
        if not t["valid"]:
            continue
        if t["format"] in ("sops-yaml", "sops-config", "sealedsecret-yaml", "k8s-secret-tls", "yaml-plaintext"):
            doc = yaml.safe_load(p.read_text(encoding="utf-8"))
            if t["format"] == "sops-yaml":
                assert doc["sops"]["mac"].startswith("ENC[AES256_GCM,") and "version" in doc["sops"]
            n["yaml"] += 1
        elif t["format"] in ("sops-json", "sops-binary", "sealedsecret-json"):
            doc = json.loads(p.read_text(encoding="utf-8"))
            if t["format"] == "sops-binary":
                assert set(doc) == {"data", "sops"}
            n["json"] += 1
        elif t["format"] == "sops-ini":
            cp = configparser.ConfigParser(interpolation=None)
            cp.read_string(p.read_text(encoding="utf-8"))
            assert cp["sops"]["mac"].startswith("ENC[") and "\\n" in cp["sops"]["age__list_0__map_enc"]
            n["ini"] += 1
        elif t["format"] == "sops-dotenv":
            kv = dict(ln.split("=", 1) for ln in p.read_text(encoding="utf-8").splitlines())
            assert kv["sops_mac"].startswith("ENC[") and kv["sops_age__list_0__map_recipient"].startswith("age1")
            n["dotenv"] += 1
    assert n["yaml"] >= 7 and n["json"] >= 3 and n["ini"] and n["dotenv"]


def test_curve_helpers_match_published_vectors():
    # RFC 7748 section 6.1 (Alice), RFC 8032 section 7.1 (test 1), secp256k1 generator
    alice = bytes.fromhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
    assert fc.x25519_base(alice).hex() == "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a"
    seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    assert fc.ed25519_public(seed).hex() == "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
    g_x = "79be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798"
    for k, want in ((1, "02" + g_x), (2, "02c6047f9441ed7d6d3045406e95c07cd85c778e4b8cef3ca7abac09b95c709ee5"),
                    (3, "02f9308a019258c31049344f85f89d5229b531c845836f99b08601f113bce036f9"),
                    (fc._SK_N - 1, "03" + g_x)):
        assert fc.secp256k1_public(k.to_bytes(32, "big")).hex() == want
    assert fc.bech32_decode(fc.bech32_encode("age", b"\x01" * 32)) == ("age", b"\x01" * 32)
    assert fc.bech32_decode("age1" + "q" * 58) is None
