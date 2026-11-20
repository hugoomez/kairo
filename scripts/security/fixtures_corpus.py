#!/usr/bin/env python3
"""A synthetic corpus of encrypted-secret files, to write parsers test-first on invented material.

    python fixtures_corpus.py --out DIR [--seed N] [--n-per-kind K] [--replace]

Writes K copies of every fixture kind under DIR/<tool>/<kind>-<i>/ and DIR/truth.jsonl, one
JSON line per file:

    {"path", "tool", "format", "kind", "valid", "algorithms": [...],
     "recipients": [{"type", "value_kind"}], "has_private_material", "public_key_recoverable_from": [...],
     "pq", "notes"}

stdout is aggregate counts only ({"files", "by_tool", "private", "pq", "invalid", ...}).
Exit 0 ok, 3 refused ({"error": ...}: DIR exists and is not empty; with --replace a previous
corpus, recognised by its truth.jsonl, is replaced), 1 unexpected error.

Covered: SOPS (YAML, JSON, dotenv, INI, binary mode, `.sops.yaml` creation rules; age, PGP,
AWS KMS, GCP KMS, Azure Key Vault and HashiCorp Vault entries, key groups), age (binary and
armored; X25519, ssh-ed25519, ssh-rsa, scrypt, the post-quantum hybrid `mlkem768x25519`,
`p256tag` / `mlkem768p256tag`, a plugin stanza, grease; recipient and identity files),
Sealed Secrets (SealedSecret in every scope, the controller certificate and its key
Secret), agenix (`secrets.nix` and the `.age` files it names), dotenvx (`.env*`,
`.env.keys`), private keys (OpenSSH, PKCS#8, PKCS#1, SEC1), plaintext twins (the cleartext
a repository's history may hold) and near-misses (tutorials with placeholders, truncated
files, other or unknown formats).

Every byte of key material and ciphertext comes from random.Random(seed): the same seed
gives the same corpus, byte for byte. Where the derivation is cheap the public half is the
real one (X25519, Ed25519, secp256k1, so `# public key:` lines, OpenSSH key pairs and
dotenvx key pairs agree); ciphertexts are random bytes of the right length and never
decrypt; RSA moduli, certificates and ML-KEM keys are random bytes in the right encoding
(the truth `notes` say so). None of it is, or was ever, a real key. Bech32 strings carry a
valid checksum. Standard library only; fake_material.py is reused for its constants.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import random
import shutil
import sys
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_material as fm  # noqa: E402

EXIT_OK, EXIT_ERROR, EXIT_REFUSED = 0, 1, 3
TRUTH = "truth.jsonl"

# ---- encodings ------------------------------------------------------------------------


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def b64raw(data: bytes) -> str:
    """Unpadded standard base64, as the age header uses it."""
    return b64(data).rstrip("=")


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def wrap(s: str, width: int = 64) -> list[str]:
    return [s[i:i + width] for i in range(0, len(s), width)] or [""]


def pem(label: str, der: bytes, width: int = 64) -> str:
    return (fm.DASHES + "BEGIN " + label + fm.DASHES + "\n" + "\n".join(wrap(b64(der), width)) + "\n"
            + fm.DASHES + "END " + label + fm.DASHES + "\n")


# Bech32 (BIP 173), as age encodes recipients and identities. age does not apply the
# 90-character limit, so the post-quantum recipients are ~1950 characters long.
_BECH32_GEN = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)


def _polymod(values: list[int]) -> int:
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            if (top >> i) & 1:
                chk ^= _BECH32_GEN[i]
    return chk


def _hrp_expand(hrp: str) -> list[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _to5(data: bytes) -> list[int]:
    acc, bits, out = 0, 0, []
    for b in data:
        acc = (acc << 8) | b
        bits += 8
        while bits >= 5:
            bits -= 5
            out.append((acc >> bits) & 31)
    if bits:
        out.append((acc << (5 - bits)) & 31)
    return out


def bech32_encode(hrp: str, data: bytes) -> str:
    """Lower-case bech32 of `data` under `hrp` (callers upper-case identities)."""
    hrp = hrp.lower()
    d5 = _to5(data)
    pm = _polymod(_hrp_expand(hrp) + d5 + [0] * 6) ^ 1
    check = [(pm >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(fm.BECH32[x] for x in d5 + check)


def bech32_decode(s: str) -> tuple[str, bytes] | None:
    """(hrp, data) of a bech32 string with a valid checksum, else None (used by the tests)."""
    s = s.lower()
    hrp, _, data = s.rpartition("1")
    if not hrp or len(data) < 6 or any(c not in fm.BECH32 for c in data):
        return None
    d5 = [fm.BECH32.index(c) for c in data]
    if _polymod(_hrp_expand(hrp) + d5) != 1:
        return None
    acc, bits, out = 0, 0, bytearray()
    for v in d5[:-6]:
        acc = (acc << 5) | v
        bits += 5
        if bits >= 8:
            bits -= 8
            out.append((acc >> bits) & 0xFF)
    return hrp, bytes(out)


def crc24(data: bytes) -> bytes:
    """OpenPGP armor checksum (RFC 4880 section 6.1)."""
    crc = 0xB704CE
    for b in data:
        crc ^= b << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
    return (crc & 0xFFFFFF).to_bytes(3, "big")


# DER (just what keys and certificates need)
def _der_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def der(tag: int, body: bytes) -> bytes:
    return bytes([tag]) + _der_len(len(body)) + body


def der_int(value: bytes | int) -> bytes:
    raw = value if isinstance(value, bytes) else value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")
    raw = raw.lstrip(b"\x00") or b"\x00"
    if raw[0] & 0x80:
        raw = b"\x00" + raw
    return der(0x02, raw)


def der_seq(*parts: bytes) -> bytes:
    return der(0x30, b"".join(parts))


OID_RSA = bytes.fromhex("06092a864886f70d010101")
OID_SHA256_RSA = bytes.fromhex("06092a864886f70d01010b")
OID_SECP256K1 = bytes.fromhex("06052b8104000a")
OID_ED25519 = bytes.fromhex("06032b6570")
DER_NULL = b"\x05\x00"


# SSH wire format (RFC 4251 section 5)
def ssh_string(b: bytes | str) -> bytes:
    raw = b.encode() if isinstance(b, str) else b
    return len(raw).to_bytes(4, "big") + raw


def ssh_mpint(raw: bytes) -> bytes:
    raw = raw.lstrip(b"\x00")
    if raw and raw[0] & 0x80:
        raw = b"\x00" + raw
    return ssh_string(raw)


# ---- curves (public halves of random private keys) -------------------------------------

_P25519 = 2 ** 255 - 19


def x25519_base(k: bytes) -> bytes:
    """X25519(k, 9), RFC 7748 section 5."""
    kb = bytearray(k)
    kb[0] &= 248
    kb[31] &= 127
    kb[31] |= 64
    s = int.from_bytes(kb, "little")
    p = _P25519
    x1, x2, z2, x3, z3, swap = 9, 1, 0, 9, 1, 0
    for t in reversed(range(255)):
        bit = (s >> t) & 1
        swap ^= bit
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = bit
        a, b = x2 + z2, x2 - z2
        aa, bb = a * a % p, b * b % p
        e = aa - bb
        c, d = x3 + z3, x3 - z3
        da, cb = d * a % p, c * b % p
        x3, z3 = (da + cb) ** 2 % p, x1 * (da - cb) ** 2 % p
        x2, z2 = aa * bb % p, e * (aa + 121665 * e) % p
    if swap:
        x2, z2 = x3, z3
    return (x2 * pow(z2, p - 2, p) % p).to_bytes(32, "little")


_ED_D = -121665 * pow(121666, _P25519 - 2, _P25519) % _P25519
_ED_B = (15112221349535400772501151409588531511454012693041857206046113283949847762202,
         4 * pow(5, _P25519 - 2, _P25519) % _P25519)


def _ed_add(a: tuple, b: tuple) -> tuple:
    """Extended-coordinate addition (RFC 8032 section 5.1.4, the reference code's point_add)."""
    p = _P25519
    aa = (a[1] - a[0]) * (b[1] - b[0]) % p
    bb = (a[1] + a[0]) * (b[1] + b[0]) % p
    c = 2 * a[3] * b[3] * _ED_D % p
    d = 2 * a[2] * b[2] % p
    e, f, g, h = bb - aa, d - c, d + c, bb + aa
    return e * f % p, g * h % p, f * g % p, e * h % p


def ed25519_public(seed: bytes) -> bytes:
    """The Ed25519 public key of a 32-byte seed (RFC 8032 section 5.1.5)."""
    p = _P25519
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little") & ((1 << 254) - 8) | (1 << 254)
    q, pt = (0, 1, 1, 0), (_ED_B[0], _ED_B[1], 1, _ED_B[0] * _ED_B[1] % p)
    while a:
        if a & 1:
            q = _ed_add(q, pt)
        pt = _ed_add(pt, pt)
        a >>= 1
    zi = pow(q[2], p - 2, p)
    x, y = q[0] * zi % p, q[1] * zi % p
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


_SK_P = 2 ** 256 - 2 ** 32 - 977
_SK_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_SK_G = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
         0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8)


def _sk_double(a):
    """Jacobian doubling on y^2 = x^3 + 7 (None is the point at infinity)."""
    if a is None or a[1] == 0:
        return None
    p = _SK_P
    x, y, z = a
    s = 4 * x * y * y % p
    m = 3 * x * x % p
    x3 = (m * m - 2 * s) % p
    return x3, (m * (s - x3) - 8 * pow(y, 4, p)) % p, 2 * y * z % p


def _sk_add(a, b):
    """Jacobian addition."""
    if a is None:
        return b
    if b is None:
        return a
    p = _SK_P
    x1, y1, z1 = a
    x2, y2, z2 = b
    z1s, z2s = z1 * z1 % p, z2 * z2 % p
    u1, u2 = x1 * z2s % p, x2 * z1s % p
    s1, s2 = y1 * z2s * z2 % p, y2 * z1s * z1 % p
    if u1 == u2:
        return _sk_double(a) if s1 == s2 else None
    h, r = (u2 - u1) % p, (s2 - s1) % p
    h2 = h * h % p
    h3 = h2 * h % p
    x3 = (r * r - h3 - 2 * u1 * h2) % p
    return x3, (r * (u1 * h2 - x3) - s1 * h3) % p, h * z1 * z2 % p


def secp256k1_public(priv: bytes, compressed: bool = True) -> bytes:
    p = _SK_P
    k = int.from_bytes(priv, "big")
    q, pt = None, (_SK_G[0], _SK_G[1], 1)
    while k:
        if k & 1:
            q = _sk_add(q, pt)
        pt = _sk_double(pt)
        k >>= 1
    zi = pow(q[2], p - 2, p)
    x, y = q[0] * zi * zi % p, q[1] * zi * zi * zi % p
    if compressed:
        return bytes([2 + (y & 1)]) + x.to_bytes(32, "big")
    return b"\x04" + x.to_bytes(32, "big") + y.to_bytes(32, "big")


# ---- seeded generator -------------------------------------------------------------------

_WORDS = ("alba", "brisa", "cedro", "duna", "enebro", "faro", "garza", "helecho", "isla", "junco",
          "lince", "marea", "nogal", "olmo", "pardo", "quejigo", "roble", "sauce", "tejo", "urraca")
_ALNUM = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


class Gen:
    """Every random choice of the corpus, from one random.Random(seed)."""

    def __init__(self, seed: int):
        self.r = random.Random(seed)

    def raw(self, n: int) -> bytes:
        return self.r.randbytes(n)

    def num(self, a: int, b: int) -> int:
        return self.r.randint(a, b)

    def pick(self, seq):
        return seq[self.r.randrange(len(seq))]

    def word(self) -> str:
        return self.pick(_WORDS)

    def name(self) -> str:
        return f"{self.word()}-{self.word()}-inventado"

    def alnum(self, n: int) -> str:
        return "".join(self.pick(_ALNUM) for _ in range(n))

    def password(self, n: int = 20) -> str:
        s = list(self.alnum(n - 3) + self.pick("!#%&*+?") + self.pick("ABCDEFGH") + self.pick("23456789"))
        self.r.shuffle(s)
        return "".join(s)

    def hex(self, n: int) -> str:
        return self.raw(n).hex()

    def uuid(self) -> str:
        return str(uuid.UUID(bytes=self.raw(16), version=4))

    def when(self) -> datetime:
        start = datetime(2023, 1, 1, tzinfo=timezone.utc)
        return start + timedelta(seconds=self.r.randrange(3 * 365 * 86400))

    def ts(self) -> str:
        return self.when().strftime("%Y-%m-%dT%H:%M:%SZ")

    def scalar(self) -> bytes:
        """A valid secp256k1 private scalar."""
        while True:
            k = self.raw(32)
            if 0 < int.from_bytes(k, "big") < _SK_N:
                return k

    def rsa_modulus(self, bits: int = 4096) -> bytes:
        """Random bytes shaped like an RSA modulus (top bit set, odd). Not a product of primes."""
        n = bytearray(self.raw(bits // 8))
        n[0] |= 0x80
        n[-1] |= 1
        return bytes(n)


# ---- truth ------------------------------------------------------------------------------

@dataclass
class Fixture:
    path: str
    data: bytes
    tool: str
    format: str
    kind: str = ""
    algorithms: list[str] = field(default_factory=list)
    recipients: list[dict] = field(default_factory=list)
    has_private_material: bool = False
    public_key_recoverable_from: list[str] = field(default_factory=list)
    pq: bool = False
    notes: str = ""
    valid: bool = True

    def truth(self) -> dict:
        return {"path": self.path, "tool": self.tool, "format": self.format, "kind": self.kind,
                "valid": self.valid, "algorithms": self.algorithms, "recipients": self.recipients,
                "has_private_material": self.has_private_material,
                "public_key_recoverable_from": self.public_key_recoverable_from,
                "pq": self.pq, "notes": self.notes}


def rcpt(type_: str, value_kind: str) -> dict:
    return {"type": type_, "value_kind": value_kind}


# ---- age ---------------------------------------------------------------------------------
# Format: https://github.com/C2SP/C2SP/blob/main/age.md (header, X25519, scrypt,
# mlkem768x25519, p256tag, mlkem768p256tag stanzas; Bech32 HRPs; armor = strict PEM).
# ssh-ed25519 / ssh-rsa stanzas: https://github.com/FiloSottile/age (agessh: 4-byte tag =
# SHA-256 of the SSH wire public key, truncated). Plugin stanza piv-p256:
# https://github.com/str4d/age-plugin-yubikey (tag + 33-byte compressed P-256 share).

AGE_ALGS = ["ChaCha20-Poly1305", "HKDF-SHA256", "HMAC-SHA256"]
_STANZA_ALG = {"X25519": "X25519", "ssh-ed25519": "ssh-ed25519/X25519", "ssh-rsa": "RSA-OAEP-SHA256",
               "scrypt": "scrypt", "mlkem768x25519": "ML-KEM-768+X25519 (HPKE)",
               "p256tag": "P-256 (HPKE)", "mlkem768p256tag": "ML-KEM-768+P-256 (HPKE)",
               "piv-p256": "P-256 (age-plugin-yubikey)"}
PQ_STANZAS = {"mlkem768x25519", "mlkem768p256tag"}


@dataclass
class Stanza:
    args: list[str]
    body: bytes
    recipient: dict | None = None

    @property
    def type(self) -> str:
        return self.args[0]


def ssh_ed25519_key(g: Gen, comment: str) -> tuple[bytes, bytes, str]:
    """(seed, wire public key, `ssh-ed25519 AAAA... comment`)."""
    seed = g.raw(32)
    wire = ssh_string("ssh-ed25519") + ssh_string(ed25519_public(seed))
    return seed, wire, f"ssh-ed25519 {b64(wire)} {comment}"


def ssh_rsa_key(g: Gen, comment: str, bits: int = 4096) -> tuple[bytes, str]:
    """(wire public key, `ssh-rsa AAAA... comment`). The modulus is random bytes."""
    wire = ssh_string("ssh-rsa") + ssh_mpint(b"\x01\x00\x01") + ssh_mpint(g.rsa_modulus(bits))
    return wire, f"ssh-rsa {b64(wire)} {comment}"


def ssh_tag(wire: bytes) -> str:
    return b64raw(hashlib.sha256(wire).digest()[:4])


def st_x25519(g: Gen) -> Stanza:
    return Stanza(["X25519", b64raw(g.raw(32))], g.raw(32), rcpt("age-x25519", "stanza_only"))


def st_ssh_ed25519(g: Gen, wire: bytes) -> Stanza:
    return Stanza(["ssh-ed25519", ssh_tag(wire), b64raw(g.raw(32))], g.raw(32), rcpt("ssh-ed25519", "ssh_key_tag"))


def st_ssh_rsa(g: Gen, wire: bytes, bits: int = 4096) -> Stanza:
    return Stanza(["ssh-rsa", ssh_tag(wire)], g.raw(bits // 8), rcpt("ssh-rsa", "ssh_key_tag"))


def st_scrypt(g: Gen) -> Stanza:
    return Stanza(["scrypt", b64raw(g.raw(16)), str(g.pick((15, 16, 18, 18, 20)))], g.raw(32),
                  rcpt("scrypt", "passphrase"))


def st_mlkem(g: Gen) -> Stanza:
    return Stanza(["mlkem768x25519", b64raw(g.raw(1120))], g.raw(32), rcpt("age-mlkem768x25519", "stanza_only"))


def st_p256tag(g: Gen) -> Stanza:
    return Stanza(["p256tag", b64raw(g.raw(4)), b64raw(b"\x04" + g.raw(64))], g.raw(32),
                  rcpt("age-p256tag", "p256_tag"))


def st_mlkem_p256tag(g: Gen) -> Stanza:
    return Stanza(["mlkem768p256tag", b64raw(g.raw(4)), b64raw(g.raw(1153))], g.raw(32),
                  rcpt("age-mlkem768p256tag", "p256_tag"))


def st_piv(g: Gen) -> Stanza:
    share = bytes([g.pick((2, 3))]) + g.raw(32)
    return Stanza(["piv-p256", b64raw(g.raw(4)), b64raw(share)], g.raw(32), rcpt("age-plugin-yubikey", "plugin_tag"))


def st_grease(g: Gen) -> Stanza:
    # age's Go implementation adds a random `<x>-grease` stanza so parsers must skip unknown types
    return Stanza([f"{g.alnum(3)}-grease", g.alnum(g.num(4, 12))], g.raw(g.num(0, 60)), None)


def age_binary(g: Gen, stanzas: list[Stanza], plain_len: int) -> bytes:
    lines = ["age-encryption.org/v1"]
    for s in stanzas:
        lines.append("-> " + " ".join(s.args))
        body = wrap(b64raw(s.body))
        if len(body[-1]) == 64:
            body.append("")  # the body always ends with a line shorter than 64 columns
        lines.extend(body)
    header = "\n".join(lines) + "\n--- " + b64raw(g.raw(32)) + "\n"
    chunks = max(1, -(-plain_len // 65536))
    return header.encode() + g.raw(16) + g.raw(plain_len + 16 * chunks)


def age_armor(data: bytes) -> str:
    return pem("AGE ENCRYPTED FILE", data)


def age_meta(stanzas: list[Stanza]) -> tuple[list[str], list[dict], bool]:
    algs = list(AGE_ALGS)
    for s in stanzas:
        a = _STANZA_ALG.get(s.type)
        if a and a not in algs:
            algs.append(a)
    rs = [s.recipient for s in stanzas if s.recipient]
    return algs, rs, any(s.type in PQ_STANZAS for s in stanzas)


def age_fixture(g: Gen, path: str, kind: str, stanzas: list[Stanza], armored: bool, notes: str,
                tool: str = "age") -> Fixture:
    data = age_binary(g, stanzas, g.num(20, 400))
    algs, rs, pq = age_meta(stanzas)
    return Fixture(path, age_armor(data).encode() if armored else data, tool,
                   "age-armored" if armored else "age-binary", kind, algs, rs, False, [], pq, notes)


def x25519_identity(g: Gen) -> tuple[str, str]:
    seed = g.raw(32)
    return (bech32_encode("AGE-SECRET-KEY-", seed).upper(), bech32_encode("age", x25519_base(seed)))


def pq_identity(g: Gen) -> tuple[str, str]:
    """(AGE-SECRET-KEY-PQ-1..., age1pq1...). The recipient is random bytes of the X-Wing public
    key length (1216 = 1184 ML-KEM-768 + 32 X25519), not derived from the seed."""
    return (bech32_encode("AGE-SECRET-KEY-PQ-", g.raw(32)).upper(), bech32_encode("age1pq", g.raw(1216)))


def sops_age_enc(g: Gen) -> str:
    """The armored age file SOPS stores as an age recipient's `enc` (a 32-byte data key)."""
    return age_armor(age_binary(g, [st_x25519(g)], 32))


# ---- SOPS --------------------------------------------------------------------------------
# Metadata fields: https://github.com/getsops/sops/blob/main/stores/stores.go
# dotenv/INI flattening (`__map_` / `__list_`, newlines escaped as `\n`, dotenv prefix
# `sops_`, INI section [sops]): stores/flatten.go, stores/dotenv/store.go, stores/ini/store.go.
# JSON indent is a tab; binary mode is JSON with one `data` key: stores/json/store.go.
# Docs: https://getsops.io/docs/

SOPS_VERSIONS = ("3.7.3", "3.8.1", "3.9.4", "3.10.2")
_SOPS_ALG = {"age": "age-X25519", "pgp": "OpenPGP", "kms": "AWS-KMS", "gcp_kms": "GCP-KMS",
             "azure_kv": "Azure-KeyVault", "hc_vault": "Vault-Transit"}
_SOPS_RCPT = {"age": ("age-x25519", "bech32"), "pgp": ("pgp", "pgp_fingerprint"), "kms": ("aws_kms", "kms_arn"),
              "gcp_kms": ("gcp_kms", "gcp_resource_id"), "azure_kv": ("azure_kv", "azure_key_url"),
              "hc_vault": ("hc_vault", "vault_transit_path")}


@dataclass
class Comment:
    """A YAML comment, which SOPS encrypts as `#ENC[...,type:comment]`."""
    text: str


def enc_value(g: Gen, plain: str, typ: str = "str") -> str:
    n = len(plain.encode())
    return f"ENC[AES256_GCM,data:{b64(g.raw(n)) if n else ''},iv:{b64(g.raw(32))},tag:{b64(g.raw(16))},type:{typ}]"


def pgp_message(g: Gen, fp: str) -> str:
    """An OpenPGP message as gpg armors it: a v3 PKESK (RSA, key id = the fingerprint's low 64
    bits) and a SEIPD packet, random inside."""
    pkesk = b"\x03" + bytes.fromhex(fp[-16:]) + b"\x01" + (4096).to_bytes(2, "big") + g.raw(512)
    seipd_body = b"\x01" + g.raw(g.num(60, 90))
    packets = (b"\x85" + len(pkesk).to_bytes(2, "big") + pkesk
               + b"\xd2" + bytes([len(seipd_body)]) + seipd_body)
    return ("-----BEGIN PGP MESSAGE-----\n\n" + "\n".join(wrap(b64(packets))) + "\n="
            + b64(crc24(packets)) + "\n-----END PGP MESSAGE-----\n")


def sops_entry(g: Gen, kind: str, ctx: dict) -> dict:
    if kind == "age":
        r = ctx.setdefault("age_recipients", [])
        recipient = x25519_identity(g)[1]
        r.append(recipient)
        return {"recipient": recipient, "enc": sops_age_enc(g)}
    if kind == "pgp":
        fp = g.hex(20).upper()
        ctx.setdefault("fps", []).append(fp)
        return {"created_at": g.ts(), "enc": pgp_message(g, fp), "fp": fp}
    if kind == "kms":
        acct = "".join(str(g.num(0, 9)) for _ in range(12))
        e = {"arn": f"arn:aws:kms:{g.pick(('eu-west-1', 'us-east-1', 'eu-central-1'))}:{acct}:key/{g.uuid()}"}
        if g.num(0, 1):
            e["role"] = f"arn:aws:iam::{acct}:role/sops-{g.word()}-inventado"
        if g.num(0, 1):
            e["context"] = {"app": g.name(), "entorno": g.pick(("prod", "staging"))}
        e.update({"created_at": g.ts(), "enc": b64(b"\x01\x02\x02\x00\x78" + g.raw(179)), "aws_profile": ""})
        return e
    if kind == "gcp_kms":
        return {"resource_id": f"projects/{g.name()}/locations/global/keyRings/sops/cryptoKeys/{g.word()}-key",
                "created_at": g.ts(), "enc": b64(b"\x0a\x24\x00" + g.raw(70))}
    if kind == "azure_kv":
        return {"vault_url": f"https://kv-{g.word()}-inventado.vault.azure.net", "name": f"sops-{g.word()}",
                "version": g.hex(16), "created_at": g.ts(), "enc": b64url(g.raw(256))}
    if kind == "hc_vault":
        return {"vault_address": "http://127.0.0.1:8200", "engine_path": "sops", "key_name": f"{g.word()}key",
                "created_at": g.ts(), "enc": "vault:v1:" + b64(g.raw(60))}
    raise ValueError(kind)


_SOPS_ORDER = ("kms", "gcp_kms", "azure_kv", "hc_vault", "age")


def sops_meta(g: Gen, kinds: list[str], *, groups: list[list[str]] | None = None, threshold: int = 0,
              unencrypted_suffix: str = "_unencrypted", encrypted_regex: str = "",
              ctx: dict | None = None) -> dict:
    """The `sops` metadata tree, in the order the Go struct serialises it. With no other rule
    sops records its default `unencrypted_suffix: _unencrypted`."""
    if encrypted_regex:
        unencrypted_suffix = ""
    ctx = {} if ctx is None else ctx
    meta: dict = {}
    if groups:
        meta["shamir_threshold"] = threshold
        meta["key_groups"] = [{k: [sops_entry(g, k, ctx)] for k in grp} for grp in groups]
    for k in _SOPS_ORDER:
        if k in kinds:
            meta[k] = [sops_entry(g, k, ctx) for _ in range(2 if k == "age" and g.num(0, 2) == 0 else 1)]
    meta["lastmodified"] = g.ts()
    meta["mac"] = enc_value(g, g.hex(64).upper())
    if "pgp" in kinds:
        meta["pgp"] = [sops_entry(g, "pgp", ctx)]
    if unencrypted_suffix:
        meta["unencrypted_suffix"] = unencrypted_suffix
    if encrypted_regex:
        meta["encrypted_regex"] = encrypted_regex
    meta["version"] = g.pick(SOPS_VERSIONS)
    return meta


def sops_truth(kinds: list[str], groups: list[list[str]] | None) -> tuple[list[str], list[dict]]:
    used = list(kinds) + [k for grp in (groups or []) for k in grp]
    algs = ["AES256_GCM", "SHA512-MAC"]
    rs: list[dict] = []
    for k in ("age", "pgp", "kms", "gcp_kms", "azure_kv", "hc_vault"):
        if k in used:
            algs.append(_SOPS_ALG[k])
            rs.append(rcpt(*_SOPS_RCPT[k]))
    return algs, rs


def _resolve(g: Gen, tree, encrypt, path=()):
    """The document as SOPS writes it: every leaf whose key path `encrypt` accepts becomes
    ENC[...] with its type; `encrypt=None` gives the plaintext (a decrypted twin)."""
    if isinstance(tree, dict):
        return {k: _resolve(g, v, encrypt, path + (k,)) for k, v in tree.items()}
    if isinstance(tree, list):
        return [_resolve(g, v, encrypt, path + (str(i),)) for i, v in enumerate(tree)]
    on = encrypt is not None and encrypt(path)
    if isinstance(tree, Comment):
        return enc_value(g, tree.text, "comment") if on else " " + tree.text
    if not on or tree is None:
        return tree
    if isinstance(tree, bool):
        return enc_value(g, "True" if tree else "False", "bool")
    typ = "int" if isinstance(tree, int) else "float" if isinstance(tree, float) else "str"
    return enc_value(g, str(tree), typ)


_YAML_PLAIN_BAD = set("-?:,[]{}#&*!|>'\"%@`")


def _yaml_scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if (not s or s[0] in _YAML_PLAIN_BAD or s[0] == " " or s[-1] == " " or ": " in s or " #" in s
            or s.lower() in ("true", "false", "null", "yes", "no", "on", "off", "~")
            or s.replace(".", "", 1).replace("-", "", 1).isdigit() or s[:4].isdigit() and "T" in s):
        return json.dumps(s, ensure_ascii=False)
    return s


def yaml_dump(obj, indent: int = 0, step: int = 4) -> list[str]:
    """A small block-style YAML emitter (4-space maps, sequences indented under their key,
    multi-line strings as `|` / `|-` blocks; keys starting with `#` become comment lines)."""
    pad = " " * indent
    out: list[str] = []
    for k, v in obj.items():
        if k.startswith("#"):
            out.append(f"{pad}#{v}")
            continue
        out.extend(_yaml_kv(pad, k, v, indent, step))
    return out


def _yaml_kv(pad: str, k: str, v, col: int, step: int) -> list[str]:
    if isinstance(v, dict) and v:
        return [f"{pad}{k}:"] + yaml_dump(v, col + step, step)
    if isinstance(v, list) and v:
        out = [f"{pad}{k}:"]
        ipad = " " * (col + step)
        for item in v:
            if isinstance(item, dict) and item:
                sub = yaml_dump(item, col + step + 2, step)
                out.append(ipad + "- " + sub[0].lstrip())
                out.extend(sub[1:])
            else:
                out.append(f"{ipad}- {_yaml_scalar(item)}")
        return out
    if isinstance(v, str) and "\n" in v.rstrip("\n"):
        marker = "|" if v.endswith("\n") else "|-"
        body = " " * (col + 2)
        return [f"{pad}{k}: {marker}"] + [body + ln if ln else "" for ln in v.rstrip("\n").split("\n")]
    if isinstance(v, (dict, list)):
        return [f"{pad}{k}: {'{}' if isinstance(v, dict) else '[]'}"]
    return [f"{pad}{k}: {_yaml_scalar(v)}"]


def _flatten(prefix: str, v, out: dict) -> None:
    if isinstance(v, dict):
        for k, x in v.items():
            _flatten(f"{prefix}__map_{k}" if prefix else k, x, out)
    elif isinstance(v, list):
        for i, x in enumerate(v):
            _flatten(f"{prefix}__list_{i}", x, out)
    else:
        out[prefix] = v


def flat_meta(meta: dict) -> dict:
    out: dict = {}
    for k, v in meta.items():
        _flatten(k, v, out)
    return {k: (str(v).replace("\n", "\\n") if not isinstance(v, bool) else str(v).lower())
            for k, v in sorted(out.items())}


def _app_tree(g: Gen) -> dict:
    return {
        "app": {"name": g.name(), "replicas": g.num(1, 5), "debug": bool(g.num(0, 1))},
        "database": {"host": f"db-{g.word()}.example.invalid", "user": f"{g.word()}_app",
                     "password": g.password(), "port_unencrypted": 5432},
        "api": {"token": g.alnum(32), "timeout": round(g.num(10, 300) / 10, 1)},
        "allowed_hosts": [f"{g.word()}.example.invalid", f"{g.word()}.example.invalid"],
    }


def _k8s_secret_tree(g: Gen, name: str, comment: bool) -> dict:
    data: dict = {}
    if comment:
        data["#c"] = Comment("rotate every 90 days")
    data.update({"password": g.password(), "api-token": g.alnum(40)})
    return {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name, "namespace": g.word()},
            "type": "Opaque", "stringData": data}


def _yaml_doc(tree: dict) -> str:
    return "\n".join(yaml_dump(tree)) + "\n"


def _default_rule(path) -> bool:
    """sops's default: encrypt every value except under a key ending in `_unencrypted`."""
    return not any(x.endswith("_unencrypted") for x in path)


def _sops_yaml_fixture(g, base, kind, fname, tree, meta_kwargs, kinds, groups=None, notes="",
                       encrypt=_default_rule, twin=False) -> list[Fixture]:
    ctx: dict = {}
    meta = sops_meta(g, kinds, groups=groups, ctx=ctx, **meta_kwargs)
    doc = _resolve(g, tree, encrypt)
    doc["sops"] = meta
    algs, rs = sops_truth(kinds, groups)
    rec = ["sops.age[].recipient"] if ctx.get("age_recipients") else []
    out = [Fixture(f"{base}/{fname}", _yaml_doc(doc).encode(), "sops", "sops-yaml", kind, algs, rs, False,
                   rec, False, notes)]
    if twin:
        plain = _resolve(g, tree, None)
        out.append(Fixture(f"{base}/previo/{fname.replace('.enc', '')}", _yaml_doc(plain).encode(), "sops",
                           "yaml-plaintext", kind, [], [], True, [], False,
                           "plaintext twin: the same keys in clear, as an earlier commit could hold them; "
                           "the ciphertexts next to it are random and do not decrypt to these values"))
    return out


def k_sops_yaml_age(g: Gen, base: str) -> list[Fixture]:
    tree = _k8s_secret_tree(g, g.name(), comment=True)
    return _sops_yaml_fixture(g, base, "sops-yaml-age", "secret.enc.yaml", tree, {}, ["age"], twin=True,
                              notes="Kubernetes Secret encrypted whole; a comment is encrypted too (type:comment)")


def k_sops_yaml_cloud(g: Gen, base: str) -> list[Fixture]:
    tree = _app_tree(g)
    return _sops_yaml_fixture(
        g, base, "sops-yaml-cloud", "config.enc.yaml", tree, {},
        ["kms", "gcp_kms", "azure_kv", "hc_vault", "pgp"],
        notes="int/float/bool/str value types; keys ending in _unencrypted stay in clear")


def k_sops_yaml_regex(g: Gen, base: str) -> list[Fixture]:
    tree = _k8s_secret_tree(g, g.name(), comment=False)
    return _sops_yaml_fixture(
        g, base, "sops-yaml-encrypted-regex", "secret.yaml", tree, {"encrypted_regex": "^(data|stringData)$"},
        ["age", "pgp"], encrypt=lambda p: bool(p) and p[0] in ("data", "stringData"),
        notes="encrypted_regex: only data/stringData are encrypted; the rest is clear")


def k_sops_yaml_groups(g: Gen, base: str) -> list[Fixture]:
    tree = _app_tree(g)
    return _sops_yaml_fixture(
        g, base, "sops-yaml-key-groups", "prod.enc.yaml", tree, {"threshold": 2}, [],
        groups=[["pgp"], ["age"], ["kms"]],
        notes="key_groups with shamir_threshold 2: the data key is split across groups")


def k_sops_json(g: Gen, base: str) -> list[Fixture]:
    ctx: dict = {}
    tree = _app_tree(g)
    doc = _resolve(g, tree, _default_rule)
    doc["sops"] = sops_meta(g, ["age", "pgp"], ctx=ctx)
    algs, rs = sops_truth(["age", "pgp"], None)
    text = json.dumps(doc, indent="\t", ensure_ascii=False) + "\n"
    return [Fixture(f"{base}/config.enc.json", text.encode(), "sops", "sops-json", "sops-json", algs, rs, False,
                    ["sops.age[].recipient"], False, "tab-indented, as sops emits JSON by default")]


def _dotenv_pairs(g: Gen) -> list[tuple[str, str]]:
    return [("DATABASE_URL", f"postgres://app:{g.password()}@db.example.invalid:5432/app"),
            ("API_TOKEN", g.alnum(36)), ("SESSION_SECRET", g.hex(24)), ("LOG_LEVEL", "info")]


def k_sops_dotenv(g: Gen, base: str) -> list[Fixture]:
    ctx: dict = {}
    pairs = _dotenv_pairs(g)
    meta = sops_meta(g, ["age"], unencrypted_suffix="_unencrypted", ctx=ctx)
    lines = [f"{k}={enc_value(g, v)}" for k, v in pairs]
    lines.append("REGION_unencrypted=eu-west-1")
    lines += [f"sops_{k}={v}" for k, v in flat_meta(meta).items()]
    algs, rs = sops_truth(["age"], None)
    return [Fixture(f"{base}/secrets.enc.env", ("\n".join(lines) + "\n").encode(), "sops", "sops-dotenv",
                    "sops-dotenv", algs, rs, False, ["sops_age__list_N__map_recipient"], False,
                    "metadata flattened with sops_ prefix, __map_/__list_ separators, newlines as \\n")]


def k_sops_ini(g: Gen, base: str) -> list[Fixture]:
    ctx: dict = {}
    meta = sops_meta(g, ["age", "pgp"], ctx=ctx)
    lines = ["[database]", f"user = {enc_value(g, g.word() + '_app')}", f"password = {enc_value(g, g.password())}",
             "", "[api]", f"token = {enc_value(g, g.alnum(32))}", f"endpoint = {enc_value(g, 'https://api.example.invalid')}",
             "", "[sops]"]
    lines += [f"{k} = {v}" for k, v in flat_meta(meta).items()]
    algs, rs = sops_truth(["age", "pgp"], None)
    return [Fixture(f"{base}/settings.enc.ini", ("\n".join(lines) + "\n").encode(), "sops", "sops-ini", "sops-ini",
                    algs, rs, False, ["[sops] age__list_N__map_recipient"], False,
                    "metadata in a [sops] section, flattened below the top level, newlines as \\n")]


def k_sops_binary(g: Gen, base: str) -> list[Fixture]:
    ctx: dict = {}
    kinds = [g.pick(("age", "kms")), "pgp"]
    blob = g.raw(g.num(40, 200))
    doc = {"data": enc_value(g, "x" * len(blob)), "sops": sops_meta(g, kinds, ctx=ctx)}
    algs, rs = sops_truth(kinds, None)
    text = json.dumps(doc, indent="\t") + "\n"
    rec = ["sops.age[].recipient"] if ctx.get("age_recipients") else []
    return [Fixture(f"{base}/keystore.bin.enc", text.encode(), "sops", "sops-binary", "sops-binary", algs, rs,
                    False, rec, False, "binary mode: the whole file is one ENC value under `data`, JSON output")]


def k_sops_config(g: Gen, base: str) -> list[Fixture]:
    ages = [x25519_identity(g)[1] for _ in range(3)]
    fps = [g.hex(20).upper() for _ in range(2)]
    acct = "".join(str(g.num(0, 9)) for _ in range(12))
    arn = f"arn:aws:kms:eu-west-1:{acct}:key/{g.uuid()}"
    gcp = f"projects/{g.name()}/locations/global/keyRings/sops/cryptoKeys/sops-key"
    az = f"https://kv-{g.word()}-inventado.vault.azure.net/keys/sops-key/{g.hex(16)}"
    lines = [
        "creation_rules:",
        "    - path_regex: k8s/.*\\.yaml$",
        "      encrypted_regex: ^(data|stringData)$",
        "      age: >-",
        f"        {ages[0]},",
        f"        {ages[1]}",
        "    - path_regex: \\.env(\\..+)?$",
        f"      kms: {arn}",
        f"      pgp: {fps[0]},{fps[1]}",
        "    - path_regex: prod/.*",
        "      shamir_threshold: 2",
        "      key_groups:",
        "        - age:",
        f"            - {ages[2]}",
        "          pgp:",
        f"            - {fps[0]}",
        "        - kms:",
        f"            - arn: {arn}",
        f"              role: arn:aws:iam::{acct}:role/sops-inventado",
        "    - path_regex: cloud/.*",
        f"      gcp_kms: {gcp}",
        f"      azure_keyvault: {az}",
        "      hc_vault_transit_uri: http://127.0.0.1:8200/v1/sops/keys/firstkey",
        "    - unencrypted_suffix: _unencrypted",
        f"      age: {ages[0]}",
    ]
    rs = [rcpt("age-x25519", "bech32"), rcpt("pgp", "pgp_fingerprint"), rcpt("aws_kms", "kms_arn"),
          rcpt("gcp_kms", "gcp_resource_id"), rcpt("azure_kv", "azure_key_url"), rcpt("hc_vault", "vault_transit_path")]
    return [Fixture(f"{base}/.sops.yaml", ("\n".join(lines) + "\n").encode(), "sops", "sops-config", "sops-config",
                    [], rs, False, ["creation_rules[].age", "creation_rules[].pgp"], False,
                    "creation rules only: no ciphertext; recipients listed for each path_regex")]


# ---- age kinds -----------------------------------------------------------------------------

def k_age_x25519(g: Gen, base: str) -> list[Fixture]:
    st = [st_x25519(g) for _ in range(g.num(1, 3))]
    if g.num(0, 1):
        st.insert(g.num(0, len(st)), st_grease(g))
    return [age_fixture(g, f"{base}/backup.tar.age", "age-x25519-binary", st, False,
                        "X25519 recipients (not identifiable from the stanza); may carry a grease stanza")]


def k_age_armored(g: Gen, base: str) -> list[Fixture]:
    st = [st_x25519(g) for _ in range(g.num(1, 2))]
    return [age_fixture(g, f"{base}/notes.txt.age", "age-x25519-armored", st, True,
                        "ASCII armor: strict PEM, padded base64, 64 columns")]


def k_age_ssh(g: Gen, base: str) -> list[Fixture]:
    _, ed_wire, _ = ssh_ed25519_key(g, "ana@portatil-inventado")
    rsa_wire, _ = ssh_rsa_key(g, "ci@inventado")
    st = [st_ssh_ed25519(g, ed_wire), st_ssh_rsa(g, rsa_wire)]
    return [age_fixture(g, f"{base}/deploy.env.age", "age-ssh", st, False,
                        "ssh-ed25519 and ssh-rsa (4096) stanzas; the tag is SHA-256(wire key)[:4]")]


def k_age_scrypt(g: Gen, base: str) -> list[Fixture]:
    return [age_fixture(g, f"{base}/vault.kdbx.age", "age-scrypt", [st_scrypt(g)], bool(g.num(0, 1)),
                        "passphrase-encrypted: one scrypt stanza (salt, log2 work factor), alone in the header")]


def k_age_pq(g: Gen, base: str) -> list[Fixture]:
    st = [st_mlkem(g) for _ in range(g.num(1, 2))]
    armored = bool(g.num(0, 1))
    return [age_fixture(g, f"{base}/secrets.json.age", "age-pq-hybrid", st, armored,
                        "post-quantum hybrid stanza mlkem768x25519 (C2SP age spec, age >= 1.3.0); the 1120-byte "
                        "encapsulated key is random bytes of the spec length")]


def k_age_tag(g: Gen, base: str) -> list[Fixture]:
    st = [st_p256tag(g), st_mlkem_p256tag(g)]
    return [age_fixture(g, f"{base}/token.age", "age-tag", st, False,
                        "tagged recipients p256tag and mlkem768p256tag (C2SP age spec): a 4-byte tag names the key")]


def k_age_plugin(g: Gen, base: str) -> list[Fixture]:
    st = [st_piv(g), st_x25519(g)]
    return [age_fixture(g, f"{base}/root-ca.key.age", "age-plugin", st, False,
                        "plugin stanza piv-p256 (age-plugin-yubikey: tag + compressed P-256 share), "
                        "taken from the plugin's documentation, not from the age spec")]


def k_age_recipients(g: Gen, base: str) -> list[Fixture]:
    _, _, ed = ssh_ed25519_key(g, "ana@portatil-inventado")
    _, rsa = ssh_rsa_key(g, "ci@inventado", 3072)
    pq = pq_identity(g)[1]
    yk = bech32_encode("age1yubikey", bytes([g.pick((2, 3))]) + g.raw(32))
    lines = ["# team (invented)", f"# {g.name()}", x25519_identity(g)[1], f"# {g.name()}", x25519_identity(g)[1],
             "", "# post-quantum", pq, "# hardware key", yk, ed, rsa]
    rs = [rcpt("age-x25519", "bech32"), rcpt("age-mlkem768x25519", "bech32"), rcpt("age-plugin-yubikey", "bech32"),
          rcpt("ssh-ed25519", "ssh_public_key"), rcpt("ssh-rsa", "ssh_public_key")]
    return [Fixture(f"{base}/recipients.txt", ("\n".join(lines) + "\n").encode(), "age", "age-recipients",
                    "age-recipients", [], rs, False, ["file"], True,
                    "recipients file: one per line, # comments, blank lines; age1pq1 (~1950 chars) and age1yubikey1")]


def k_age_identity(g: Gen, base: str) -> list[Fixture]:
    secret, recipient = x25519_identity(g)
    created = g.when().astimezone(timezone(timedelta(hours=g.pick((0, 1, 2))))).isoformat(timespec="seconds")
    text = f"# created: {created}\n# public key: {recipient}\n{secret}\n"
    return [Fixture(f"{base}/key.txt", text.encode(), "age", "age-identity", "age-identity",
                    ["X25519"], [rcpt("age-x25519", "bech32")], True,
                    ["comment:public key", "derive:X25519(identity)"], False,
                    "age-keygen output; the public key comment is the real X25519 public key of the identity")]


def k_age_identity_pq(g: Gen, base: str) -> list[Fixture]:
    secret, recipient = pq_identity(g)
    created = g.when().isoformat(timespec="seconds")
    text = f"# created: {created}\n# public key: {recipient}\n{secret}\n"
    return [Fixture(f"{base}/pq-key.txt", text.encode(), "age", "age-identity", "age-identity-pq",
                    ["ML-KEM-768+X25519 (HPKE)"], [rcpt("age-mlkem768x25519", "bech32")], True,
                    ["comment:public key"], True,
                    "age-keygen -pq output (AGE-SECRET-KEY-PQ-1, 32-byte seed); the age1pq1 recipient is random "
                    "bytes of the 1216-byte length, not derived from the seed")]


# ---- Sealed Secrets -------------------------------------------------------------------------
# https://github.com/bitnami-labs/sealed-secrets (README: scopes via annotations
# sealedsecrets.bitnami.com/namespace-wide | cluster-wide; key Secret labelled
# sealedsecrets.bitnami.com/sealed-secrets-key) and docs/developer/crypto.md (value =
# 2-byte length of the RSA-OAEP session key || RSA-OAEP(4096) || AES-GCM ciphertext).

SEALED_ALGS = ["RSA-OAEP-SHA256", "AES-256-GCM"]


def sealed_value(g: Gen, plain_len: int) -> str:
    return b64((512).to_bytes(2, "big") + g.raw(512) + g.raw(plain_len + 16))


def _sealed_doc(g: Gen, name: str, ns: str, scope: str) -> dict:
    ann = {}
    if scope != "strict":
        ann = {f"sealedsecrets.bitnami.com/{scope}": "true"}
    meta = {"creationTimestamp": None, "name": name}
    if scope != "cluster-wide":
        meta["namespace"] = ns
    if ann:
        meta = {"annotations": ann, **meta}
    data = {k: sealed_value(g, n) for k, n in (("password", 20), ("api-token", 40), ("ca.crt", 900))}
    return {"apiVersion": "bitnami.com/v1alpha1", "kind": "SealedSecret", "metadata": meta,
            "spec": {"encryptedData": data,
                     "template": {"metadata": dict(meta), "type": g.pick(("Opaque", "kubernetes.io/basic-auth"))}}}


def _yaml2(tree: dict) -> str:
    return "\n".join(yaml_dump(tree, 0, 2)) + "\n"


def fake_cert(g: Gen, modulus: bytes) -> bytes:
    """An X.509 v3 certificate, DER, parseable: O=sealed-secret, RSA key = `modulus`, random
    serial and signature (the signature does not verify)."""
    def name():
        return der_seq(der(0x31, der_seq(bytes.fromhex("0603550403"), der(0x0C, b""))),
                       der(0x31, der_seq(bytes.fromhex("060355040a"), der(0x0C, b"sealed-secret"))))
    t0 = g.when()
    t1 = t0 + timedelta(days=3650)
    validity = der_seq(der(0x17, t0.strftime("%y%m%d%H%M%SZ").encode()), der(0x17, t1.strftime("%y%m%d%H%M%SZ").encode()))
    spki = der_seq(der_seq(OID_RSA, DER_NULL),
                   der(0x03, b"\x00" + der_seq(der_int(modulus), der_int(65537))))
    tbs = der_seq(der(0xA0, der_int(2)), der_int(g.raw(16)), der_seq(OID_SHA256_RSA, DER_NULL), name(),
                  validity, name(), spki)
    return der_seq(tbs, der_seq(OID_SHA256_RSA, DER_NULL), der(0x03, b"\x00" + g.raw(len(modulus))))


def fake_rsa_pkcs1(g: Gen, modulus: bytes) -> bytes:
    """A PKCS#1 RSAPrivateKey, DER-valid, with random numbers of the right lengths (not a key)."""
    half = len(modulus) // 2
    return der_seq(der_int(0), der_int(modulus), der_int(65537), der_int(g.raw(len(modulus))),
                   *(der_int(g.raw(half)) for _ in range(5)))


def k_sealed_strict(g: Gen, base: str) -> list[Fixture]:
    doc = _sealed_doc(g, g.name(), g.word(), "strict")
    return [Fixture(f"{base}/sealed-secret.yaml", ("---\n" + _yaml2(doc)).encode(), "sealed-secrets",
                    "sealedsecret-yaml", "sealed-strict", SEALED_ALGS,
                    [rcpt("sealed-secrets-controller", "controller_cert_external")], False, [], False,
                    "strict scope (OAEP label = namespace + name); kubeseal -o yaml output")]


def k_sealed_namespace(g: Gen, base: str) -> list[Fixture]:
    doc = _sealed_doc(g, g.name(), g.word(), "namespace-wide")
    return [Fixture(f"{base}/sealed-secret.yaml", _yaml2(doc).encode(), "sealed-secrets", "sealedsecret-yaml",
                    "sealed-namespace-wide", SEALED_ALGS,
                    [rcpt("sealed-secrets-controller", "controller_cert_external")], False, [], False,
                    "namespace-wide scope via annotation (OAEP label = namespace)")]


def k_sealed_cluster_cert(g: Gen, base: str) -> list[Fixture]:
    doc = _sealed_doc(g, g.name(), g.word(), "cluster-wide")
    modulus = g.rsa_modulus(4096)
    cert = pem("CERTIFICATE", fake_cert(g, modulus))
    return [
        Fixture(f"{base}/sealed-secret.json", (json.dumps(doc, indent=2) + "\n").encode(), "sealed-secrets",
                "sealedsecret-json", "sealed-cluster-wide", SEALED_ALGS,
                [rcpt("sealed-secrets-controller", "x509_certificate")], False, ["pub-cert.pem"], False,
                "cluster-wide scope (empty OAEP label); kubeseal's default JSON output; the controller "
                "certificate is committed next to it"),
        Fixture(f"{base}/pub-cert.pem", cert.encode(), "sealed-secrets", "x509-pem", "sealed-cluster-wide",
                ["RSA-4096"], [rcpt("sealed-secrets-controller", "x509_certificate")], False, ["file"], False,
                "kubeseal --fetch-cert output: DER-valid X.509, random modulus and signature (does not verify)"),
    ]


def k_sealed_master_key(g: Gen, base: str) -> list[Fixture]:
    modulus = g.rsa_modulus(4096)
    crt = b64(pem("CERTIFICATE", fake_cert(g, modulus)).encode())
    key = b64(pem("RSA PRIVATE KEY", fake_rsa_pkcs1(g, modulus)).encode())
    suffix = g.alnum(5).lower()
    doc = {"apiVersion": "v1", "items": [{
        "apiVersion": "v1", "data": {"tls.crt": crt, "tls.key": key}, "kind": "Secret",
        "metadata": {"creationTimestamp": g.ts(), "generateName": "sealed-secrets-key",
                     "labels": {"sealedsecrets.bitnami.com/sealed-secrets-key": "active"},
                     "name": f"sealed-secrets-key{suffix}", "namespace": "kube-system",
                     "resourceVersion": str(g.num(1000, 99999)), "uid": g.uuid()},
        "type": "kubernetes.io/tls"}], "kind": "List", "metadata": {"resourceVersion": ""}}
    return [Fixture(f"{base}/main.key", _yaml2(doc).encode(), "sealed-secrets", "k8s-secret-tls",
                    "sealed-master-key", ["RSA-4096"], [rcpt("sealed-secrets-controller", "x509_certificate")], True,
                    ["data.tls.crt", "derive:tls.key"], False,
                    "controller key backup (kubectl get secret -l sealedsecrets.bitnami.com/sealed-secrets-key "
                    "-o yaml); tls.key is base64 of a PKCS#1 PEM with random numbers")]


# ---- agenix -------------------------------------------------------------------------------
# https://github.com/ryantm/agenix (README: secrets.nix maps "<name>.age".publicKeys to SSH or
# age public keys; `armor = true` writes ASCII-armored files).

def k_agenix(g: Gen, base: str) -> list[Fixture]:
    _, user_wire, user_line = ssh_ed25519_key(g, "")
    _, sys_wire, sys_line = ssh_ed25519_key(g, "")
    age_rcpt = x25519_identity(g)[1]
    u, h = g.word(), f"{g.word()}-host"
    nix = "\n".join([
        "let",
        f"  {u} = \"{user_line.strip()}\";",
        f"  users = [ {u} ];",
        "",
        f"  {h} = \"{sys_line.strip()}\";",
        f"  recovery = \"{age_rcpt}\";",
        f"  systems = [ {h} ];",
        "in",
        "{",
        f"  \"secret1.age\".publicKeys = [ {u} {h} ];",
        "  \"secret2.age\".publicKeys = users ++ systems ++ [ recovery ];",
        "  \"armored-secret.age\" = {",
        f"    publicKeys = [ {u} ];",
        "    armor = true;",
        "  };",
        "}",
    ]) + "\n"
    out = [Fixture(f"{base}/secrets/secrets.nix", nix.encode(), "agenix", "agenix-secrets-nix", "agenix", [],
                   [rcpt("ssh-ed25519", "ssh_public_key"), rcpt("age-x25519", "bech32")], False, ["file"], False,
                   "Nix attribute set: each .age file and the public keys it is encrypted to")]
    note = "agenix .age file; ssh-ed25519 tags match the keys in secrets.nix"
    out.append(age_fixture(g, f"{base}/secrets/secret1.age", "agenix",
                           [st_ssh_ed25519(g, user_wire), st_ssh_ed25519(g, sys_wire)], False, note, "agenix"))
    out.append(age_fixture(g, f"{base}/secrets/secret2.age", "agenix",
                           [st_ssh_ed25519(g, user_wire), st_ssh_ed25519(g, sys_wire), st_x25519(g)], False, note,
                           "agenix"))
    out.append(age_fixture(g, f"{base}/secrets/armored-secret.age", "agenix", [st_ssh_ed25519(g, user_wire)],
                           True, note + " (armor = true)", "agenix"))
    return out


# ---- dotenvx ------------------------------------------------------------------------------
# https://dotenvx.com/docs and https://github.com/dotenvx/dotenvx (DOTENV_PUBLIC_KEY[_ENV]
# header, `encrypted:` values, .env.keys with DOTENV_PRIVATE_KEY[_ENV]); value layout from
# eciesjs (https://github.com/ecies/js): uncompressed ephemeral secp256k1 key (65) || nonce
# (16) || tag (16) || AES-256-GCM ciphertext, base64.

DOTENVX_ALGS = ["ECIES-secp256k1", "AES-256-GCM", "HKDF-SHA256"]
_HEADER_OLD = ("#/-------------------[DOTENV_PUBLIC_KEY]--------------------/\n"
               "#/            public-key encryption for .env files          /\n"
               "#/       [how it works](https://dotenvx.com/encryption)     /\n"
               "#/----------------------------------------------------------/\n")
_KEYS_HEADER = ("#/------------------!DOTENV_PRIVATE_KEYS!-------------------/\n"
                "#/ private decryption keys. DO NOT commit to source control /\n"
                "#/     [how it works](https://dotenvx.com/encryption)       /\n"
                "#/----------------------------------------------------------/\n")


def dotenvx_value(g: Gen, plain: str) -> str:
    eph = secp256k1_public(g.scalar(), compressed=False)
    return "encrypted:" + b64(eph + g.raw(16) + g.raw(16) + g.raw(len(plain.encode())))


def k_dotenvx(g: Gen, base: str) -> list[Fixture]:
    out: list[Fixture] = []
    keys: list[str] = []
    for env, fname, header in (("", ".env", _HEADER_OLD), ("PRODUCTION", ".env.production", "# ◈ encrypted with Dotenvx\n")):
        priv = g.scalar()
        pub = secp256k1_public(priv).hex()
        suffix = f"_{env}" if env else ""
        pairs = _dotenv_pairs(g) + [("HELLO", g.word())]
        body = [header + f'DOTENV_PUBLIC_KEY{suffix}="{pub}"', "", f"# {fname}"]
        body += [f'{k}="{dotenvx_value(g, v)}"' for k, v in pairs]
        out.append(Fixture(f"{base}/{fname}", ("\n".join(body) + "\n").encode(), "dotenvx", "dotenvx-env",
                           "dotenvx", DOTENVX_ALGS, [rcpt("dotenvx-secp256k1", "secp256k1_compressed_hex")], False,
                           [f"DOTENV_PUBLIC_KEY{suffix}"], False,
                           "encrypted: values carry a real secp256k1 ephemeral point; header style "
                           + ("pre-1.x box" if not env else "newer one-line")))
        keys.append(f"# {fname}\nDOTENV_PRIVATE_KEY{suffix}=\"{priv.hex()}\"\n")
        if not env:
            plain = ["# .env"] + [f'{k}="{v}"' for k, v in pairs]
            out.append(Fixture(f"{base}/previo/.env", ("\n".join(plain) + "\n").encode(), "dotenvx",
                               "dotenv-plaintext", "dotenvx", [], [], True, [], False,
                               "plaintext twin of .env (the same names in clear), as an earlier commit could "
                               "hold it; the ciphertexts do not decrypt to these values"))
    out.append(Fixture(f"{base}/.env.keys", (_KEYS_HEADER + "\n" + "\n".join(keys)).encode(),
                       "dotenvx", "dotenvx-keys", "dotenvx", ["secp256k1"],
                       [rcpt("dotenvx-secp256k1", "secp256k1_private_hex")], True,
                       ["derive:secp256k1(DOTENV_PRIVATE_KEY*)"], False,
                       "private keys of .env and .env.production; their public keys are the headers' ones"))
    return out


# ---- private keys -------------------------------------------------------------------------

def openssh_private(g: Gen, seed: bytes, wire: bytes, comment: str) -> str:
    """An unencrypted openssh-key-v1 Ed25519 private key (PROTOCOL.key), consistent with `wire`."""
    pub = wire[-32:]
    check = g.raw(4)
    priv = check + check + ssh_string("ssh-ed25519") + ssh_string(pub) + ssh_string(seed + pub) + ssh_string(comment)
    priv += bytes(range(1, 1 + (-len(priv) % 8)))
    blob = (b"openssh-key-v1\x00" + ssh_string("none") + ssh_string("none") + ssh_string(b"")
            + (1).to_bytes(4, "big") + ssh_string(wire) + ssh_string(priv))
    return pem("OPENSSH PRIVATE KEY", blob, 70)


def k_ssh_keypair(g: Gen, base: str) -> list[Fixture]:
    comment = f"{g.word()}@{g.word()}-inventado"
    seed, wire, line = ssh_ed25519_key(g, comment)
    return [
        Fixture(f"{base}/.ssh/id_ed25519", openssh_private(g, seed, wire, comment).encode(), "openssh",
                "openssh-private", "ssh-keypair", ["Ed25519"], [], True,
                ["embedded:public key blob", "derive:Ed25519(seed)"], False,
                "unencrypted openssh-key-v1; the public key inside matches the seed and id_ed25519.pub"),
        Fixture(f"{base}/.ssh/id_ed25519.pub", (line + "\n").encode(), "openssh", "ssh-public", "ssh-keypair",
                ["Ed25519"], [rcpt("ssh-ed25519", "ssh_public_key")], False, ["file"], False,
                "public half (an age / agenix recipient)"),
    ]


def k_pem_keys(g: Gen, base: str) -> list[Fixture]:
    ed = der_seq(der_int(0), der_seq(OID_ED25519), der(0x04, der(0x04, g.raw(32))))
    rsa = fake_rsa_pkcs1(g, g.rsa_modulus(2048))
    k = g.scalar()
    ec = der_seq(der_int(1), der(0x04, k), der(0xA0, OID_SECP256K1),
                 der(0xA1, der(0x03, b"\x00" + secp256k1_public(k, compressed=False))))
    return [
        Fixture(f"{base}/tls/ed25519.key", pem("PRIVATE KEY", ed).encode(), "pem", "pkcs8-pem", "pem-keys",
                ["Ed25519"], [], True, ["derive:Ed25519(seed)"], False, "PKCS#8 Ed25519, a valid key"),
        Fixture(f"{base}/tls/server.key", pem("RSA PRIVATE KEY", rsa).encode(), "pem", "pkcs1-pem", "pem-keys",
                ["RSA-2048"], [], True, ["modulus in the key"], False,
                "PKCS#1, DER-valid, random numbers (not a usable RSA key)"),
        Fixture(f"{base}/tls/wallet.key", pem("EC PRIVATE KEY", ec).encode(), "pem", "sec1-pem", "pem-keys",
                ["secp256k1"], [], True, ["embedded:publicKey", "derive:secp256k1(d)"], False,
                "SEC1 secp256k1 with its real public point"),
    ]


# ---- near-misses --------------------------------------------------------------------------

def k_doc_tutorial(g: Gen, base: str) -> list[Fixture]:
    text = f"""# Managing secrets in {g.name()} (invented tutorial)

1. Generate a key with `age-keygen -o key.txt`. The file holds a line starting with
   `AGE-SECRET-KEY-1...` and a comment with your public key, `age1...`.
2. Encrypt: `sops --encrypt --age age1<your-public-key> secret.yaml > secret.enc.yaml`.
   Values then look like `ENC[AES256_GCM,data:...,iv:...,tag:...,type:str]`.
3. dotenvx writes `DOTENV_PUBLIC_KEY="<public-key-hex>"` and values such as
   `HELLO="encrypted:..."`; keep `DOTENV_PRIVATE_KEY="<your-private-key>"` out of git.
4. A SealedSecret carries `encryptedData:` with values like `AgB...`.
5. Never commit a file starting with `-----BEGIN OPENSSH PRIVATE KEY-----`.

```yaml
sops:
    age:
        - recipient: age1xxxxxxxx
          enc: <omitted>
    mac: ENC[...]
```
"""
    return [Fixture(f"{base}/docs/secrets-howto.md", text.encode(), "none", "doc-placeholders", "doc-tutorial",
                    [], [], False, [], False,
                    "near-miss: names every format with placeholders only; no key, recipient or ciphertext",
                    valid=False)]


def k_truncated(g: Gen, base: str) -> list[Fixture]:
    tree = _k8s_secret_tree(g, g.name(), comment=False)
    doc = _resolve(g, tree, lambda p: True)
    doc["sops"] = sops_meta(g, ["age"])
    full = _yaml_doc(doc)
    cut = full.index("    mac: ") + 20
    age_full = age_binary(g, [st_x25519(g), st_x25519(g)], 100)
    age_cut = age_full[: age_full.index(b"\n--- ") - g.num(5, 30)]
    armored = age_armor(age_binary(g, [st_x25519(g)], 300))
    arm_cut = armored[: len(armored) * 2 // 3]
    note = "near-miss: truncated, a parser must report it as incomplete"
    return [
        Fixture(f"{base}/secret.enc.yaml", full[:cut].encode(), "sops", "sops-yaml-truncated", "truncated",
                ["AES256_GCM"], [rcpt("age-x25519", "bech32")], False, [], False, note + " (cut inside mac)",
                valid=False),
        Fixture(f"{base}/data.age", age_cut, "age", "age-binary-truncated", "truncated", list(AGE_ALGS) + ["X25519"],
                [rcpt("age-x25519", "stanza_only")], False, [], False, note + " (header without --- line)",
                valid=False),
        Fixture(f"{base}/data.txt.age", arm_cut.encode(), "age", "age-armored-truncated", "truncated",
                list(AGE_ALGS), [], False, [], False, note + " (armor without END line)", valid=False),
    ]


def k_unknown(g: Gen, base: str) -> list[Fixture]:
    inner = (g.hex(32) + "\n" + g.hex(32) + "\n" + g.hex(g.num(48, 160))).encode()
    vault = "$ANSIBLE_VAULT;1.1;AES256\n" + "\n".join(wrap(binascii.hexlify(inner).decode(), 80)) + "\n"
    ci = f"pipeline:\n  name: build-{g.word()}\n  steps: [lint, test]\nsops: enabled\nencrypted_regex: none\n"
    example = ("# copy to .env and fill in\nDOTENV_PUBLIC_KEY=\"\"\nDB_PASSWORD=changeme\n"
               "API_KEY=<your-api-key>\nHELLO=world\n")
    return [
        Fixture(f"{base}/group_vars/vault.yml", vault.encode(), "ansible-vault", "ansible-vault-1.1", "unknown",
                ["AES-256-CTR", "HMAC-SHA256", "PBKDF2"], [rcpt("ansible-vault", "passphrase")], False, [], False,
                "near-miss: another tool's format (outside the five covered); hex body is random", valid=False),
        Fixture(f"{base}/blob.enc", b"KXF\x01" + g.raw(g.num(100, 300)), "unknown", "unknown-binary", "unknown",
                [], [], False, [], False, "near-miss: invented magic and random bytes", valid=False),
        Fixture(f"{base}/ci.yaml", ci.encode(), "none", "yaml-sops-key", "unknown", [], [], False, [], False,
                "near-miss: a top-level `sops` key that is not SOPS metadata", valid=False),
        Fixture(f"{base}/.env.example", example.encode(), "none", "dotenv-placeholders", "unknown", [], [], False,
                [], False, "near-miss: dotenv template with empty or placeholder values", valid=False),
    ]


KINDS = [
    ("sops", "sops-yaml-age", k_sops_yaml_age), ("sops", "sops-yaml-cloud", k_sops_yaml_cloud),
    ("sops", "sops-yaml-encrypted-regex", k_sops_yaml_regex), ("sops", "sops-yaml-key-groups", k_sops_yaml_groups),
    ("sops", "sops-json", k_sops_json), ("sops", "sops-dotenv", k_sops_dotenv), ("sops", "sops-ini", k_sops_ini),
    ("sops", "sops-binary", k_sops_binary), ("sops", "sops-config", k_sops_config),
    ("age", "age-x25519-binary", k_age_x25519), ("age", "age-x25519-armored", k_age_armored),
    ("age", "age-ssh", k_age_ssh), ("age", "age-scrypt", k_age_scrypt), ("age", "age-pq-hybrid", k_age_pq),
    ("age", "age-tag", k_age_tag), ("age", "age-plugin", k_age_plugin), ("age", "age-recipients", k_age_recipients),
    ("age", "age-identity", k_age_identity), ("age", "age-identity-pq", k_age_identity_pq),
    ("sealed-secrets", "sealed-strict", k_sealed_strict), ("sealed-secrets", "sealed-namespace-wide", k_sealed_namespace),
    ("sealed-secrets", "sealed-cluster-wide", k_sealed_cluster_cert),
    ("sealed-secrets", "sealed-master-key", k_sealed_master_key),
    ("agenix", "agenix", k_agenix), ("dotenvx", "dotenvx", k_dotenvx),
    ("private", "ssh-keypair", k_ssh_keypair), ("private", "pem-keys", k_pem_keys),
    ("negatives", "doc-tutorial", k_doc_tutorial), ("negatives", "truncated", k_truncated),
    ("negatives", "unknown", k_unknown),
]


def build(seed: int, n_per_kind: int) -> list[Fixture]:
    """Every fixture of the corpus, in a fixed order (no file is written)."""
    g = Gen(seed)
    out: list[Fixture] = []
    for folder, kind, fn in KINDS:
        for i in range(n_per_kind):
            out.extend(fn(g, f"{folder}/{kind}-{i}"))
    paths = [f.path for f in out]
    if len(paths) != len(set(paths)):
        raise RuntimeError("dos fixtures con la misma ruta")
    return out


def write(out: Path, fixtures: list[Fixture]) -> None:
    for f in fixtures:
        p = out / f.path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(f.data)
    lines = [json.dumps(f.truth(), ensure_ascii=False, sort_keys=True) for f in sorted(fixtures, key=lambda f: f.path)]
    (out / TRUTH).write_bytes(("\n".join(lines) + "\n").encode())


def summary(fixtures: list[Fixture], seed: int, n_per_kind: int) -> dict:
    return {"files": len(fixtures), "seed": seed, "n_per_kind": n_per_kind,
            "by_tool": dict(sorted(Counter(f.tool for f in fixtures).items())),
            "private": sum(f.has_private_material for f in fixtures), "pq": sum(f.pq for f in fixtures),
            "invalid": sum(not f.valid for f in fixtures)}


class Refused(Exception):
    pass


def _prepare(out: Path, replace: bool) -> None:
    if out.exists() and not out.is_dir():
        raise Refused("--out existe y no es una carpeta")
    if out.is_dir() and any(out.iterdir()):
        if not replace:
            raise Refused("--out no está vacía (usa --replace para rehacer un corpus anterior)")
        if not (out / TRUTH).is_file():
            raise Refused("--out no está vacía y no es un corpus anterior (no tiene truth.jsonl); no la toco")
        for child in out.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
    out.mkdir(parents=True, exist_ok=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Corpus sintético de ficheros de secretos cifrados (todo inventado).")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--n-per-kind", type=int, default=2)
    ap.add_argument("--replace", action="store_true")
    a = ap.parse_args(argv)
    if a.n_per_kind < 1:
        print(json.dumps({"error": "--n-per-kind debe ser >= 1"}))
        return EXIT_REFUSED
    try:
        _prepare(a.out, a.replace)
        fixtures = build(a.seed, a.n_per_kind)
        write(a.out, fixtures)
    except Refused as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return EXIT_REFUSED
    except OSError as e:
        sys.stderr.write(f"error: {e}\n")
        return EXIT_ERROR
    print(json.dumps(summary(fixtures, a.seed, a.n_per_kind), ensure_ascii=False))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
