"""Invented, pattern-valid key material and study files for the strict-redaction tests.

Everything is assembled at run time from pieces and random bytes: no literal key block
lives in the repository. Names (`app-inventada`, `acme-inventada`) are invented."""
from __future__ import annotations

import base64
import os

DASHES = "-" * 5
BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def b64(n: int) -> str:
    return base64.b64encode(os.urandom(n)).decode()


def block(label: str, nbytes: int = 120) -> str:
    body = b64(nbytes)
    lines = [body[i:i + 64] for i in range(0, len(body), 64)]
    return DASHES + "BEGIN " + label + DASHES + "\n" + "\n".join(lines) + "\n" + DASHES + "END " + label + DASHES


def age_secret() -> str:
    return "AGE-SECRET-KEY-1" + "".join(BECH32.upper()[b % 32] for b in os.urandom(58))


def age_recipient() -> str:
    return "age1" + "".join(BECH32[b % 32] for b in os.urandom(58))


def enc(n: int = 24) -> str:
    return f"ENC[AES256_GCM,data:{b64(n)},iv:{b64(32)},tag:{b64(16)},type:str]"


def indent(text: str, pad: str) -> str:
    return "\n".join(pad + ln for ln in text.splitlines())


def sops_yaml(recipient: str, fp: str) -> str:
    armor = indent(block("AGE ENCRYPTED FILE", 200), " " * 16)
    return ("apiVersion: v1\nkind: Secret\nmetadata:\n    name: app-inventada\ntype: Opaque\n"
            f"data:\n    password: {enc()}\n    api: {enc(40)}\n"
            f"sops:\n    age:\n        - recipient: {recipient}\n          enc: |\n{armor}\n"
            f"    lastmodified: \"2031-01-01T00:00:00Z\"\n    mac: {enc(32)}\n"
            f"    pgp:\n        - fp: {fp}\n    version: 3.9.0\n")


def sealed_secret(blob: str) -> str:
    return ("apiVersion: bitnami.com/v1alpha1\nkind: SealedSecret\nmetadata:\n  name: inventado\n"
            f"  namespace: demo\nspec:\n  encryptedData:\n    password: {blob}\n"
            "  template:\n    metadata:\n      name: inventado\n")


def k8s_secret(pw_b64: str, token_b64: str, user: str) -> str:
    return ("apiVersion: v1\nkind: Secret\nmetadata:\n  name: inventado\ntype: Opaque\n"
            f"data:\n  password: {pw_b64}\n  token: {token_b64}\nstringData:\n  usuario: {user}\n")


def age_binary(recipient_share: str) -> bytes:
    header = (f"age-encryption.org/v1\n-> X25519 {recipient_share}\n{b64(32).rstrip('=')}\n"
              f"--- {b64(32).rstrip('=')}\n")
    return header.encode() + os.urandom(300)
