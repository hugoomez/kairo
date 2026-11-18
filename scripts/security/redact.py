#!/usr/bin/env python3
"""Redact private material from text, leaving a fingerprint instead.

    redact.py redact < in > out    exit 0
    redact.py check  < in          exit 0 clean, exit 3 if something was found;
                                   stdout is {"findings": [{"type", "sha256"}]}

Replacement format: `[REDACTADO <type> sha256:<12 hex>]`, the hash being the SHA-256
of the original material in UTF-8. For key blocks and age keys the material is the
whole match; for `name = value` assignments and credential URLs it is the secret
value only, so the surrounding text stays readable. Findings never carry the material.
Standard library only; patterns come from check_bundle.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_bundle as cb  # noqa: E402

EXIT_OK, EXIT_ERROR, EXIT_FOUND = 0, 1, 3

# A whole key block (header..END of the same label). A block with no END is redacted
# from its header to the end of the text, which is the safe side.
_BLOCK_RE = re.compile(
    r"-----BEGIN ((?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?)-----.*?-----END \1-----"
    r"|-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----.*\Z"
    r"|PuTTY-User-Key-File-\d+:.*?(?:Private-MAC|Private-Hash): *[0-9a-fA-F]+",
    re.S)
_AGE_RE = re.compile(r"AGE-SECRET-KEY-1[QPZRY9X8GF2TVDW0S3JN54KHCE6MUA7L]{58}")
_MARK_RE = re.compile(r"\[REDACTADO \w+ sha256:[0-9a-f]{12}\]")


def _spans(text: str) -> list[tuple[int, int, str]]:
    found: list[tuple[int, int, str]] = []
    for m in _BLOCK_RE.finditer(text):
        found.append((m.start(), m.end(), "private_key"))
    for m in _AGE_RE.finditer(text):
        found.append((m.start(), m.end(), "age_secret_key"))
    for _name, rx in cb.PROVIDER_RULES:
        for m in rx.finditer(text):
            if cb.provider_ok(m.group(0)):
                found.append((m.start(), m.end(), "api_key"))
    for m in cb.JWT_RE.finditer(text):
        found.append((m.start(), m.end(), "token"))
    for m in cb.BEARER_RE.finditer(text):
        if cb.looks_secret(m.group(1)):
            found.append((m.start(1), m.end(1), "token"))
    for m in cb.API_ASSIGN_RE.finditer(text):
        if cb.looks_secret(m.group(2)):
            found.append((m.start(2), m.end(2), "api_key"))
    for m in cb.TOKEN_ASSIGN_RE.finditer(text):
        if cb.looks_secret(m.group(2)):
            found.append((m.start(2), m.end(2), "token"))
    for m in cb.PASSWORD_ASSIGN_RE.finditer(text):
        for i in (2, 3, 4):
            if m.group(i) is not None and cb.looks_password(m.group(i)):
                found.append((m.start(i), m.end(i), "token"))
    for m in cb.CRED_URL_RE.finditer(text):
        pw = m.group(2)
        if not cb.is_placeholder(pw) and len(pw) >= 8 and pw.lower() not in ("password", "passw0rd"):
            found.append((m.start(2), m.end(2), "credential_url"))
    # Drop anything inside an existing redaction mark (idempotence), then resolve
    # overlaps longest-first so a key block is never cut in pieces.
    marks = [(m.start(), m.end()) for m in _MARK_RE.finditer(text)]
    found = [f for f in found if not any(a <= f[0] and f[1] <= b for a, b in marks)]
    found.sort(key=lambda f: (-(f[1] - f[0]), f[0]))
    kept: list[tuple[int, int, str]] = []
    for s, e, t in found:
        if not any(s < ke and ks < e for ks, ke, _ in kept):
            kept.append((s, e, t))
    return sorted(kept)


def _fingerprint(material: str) -> str:
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


def redact_text(text: str) -> tuple[str, list[dict]]:
    out: list[str] = []
    findings: list[dict] = []
    pos = 0
    for s, e, t in _spans(text):
        fp = _fingerprint(text[s:e])
        out.append(text[pos:s])
        out.append(f"[REDACTADO {t} sha256:{fp}]")
        findings.append({"type": t, "sha256": fp})
        pos = e
    out.append(text[pos:])
    return "".join(out), findings


def has_secret(text: str) -> bool:
    return bool(_spans(text))


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] not in ("redact", "check"):
        sys.stderr.write("uso: redact.py redact|check < entrada\n")
        return EXIT_ERROR
    try:
        text = sys.stdin.buffer.read().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        sys.stderr.write("error: la entrada no es UTF-8 legible\n")
        return EXIT_ERROR
    redacted, findings = redact_text(text)
    if argv[0] == "redact":
        sys.stdout.buffer.write(redacted.encode("utf-8"))
        sys.stdout.buffer.flush()
        return EXIT_OK
    sys.stdout.buffer.write((json.dumps({"findings": findings}) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return EXIT_FOUND if findings else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
