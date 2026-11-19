#!/usr/bin/env python3
"""Redact private material from text, leaving a fingerprint instead.

    redact.py redact < in > out    exit 0
    redact.py redact-json < in.json > out.json
                                   redacts every decoded string (keys too) of a JSON
                                   document, so fingerprints hash the real material and
                                   the output stays valid JSON; invalid JSON: exit 1
    redact.py check  < in          exit 0 clean, exit 3 if something was found;
                                   stdout is {"findings": [{"type", "sha256"}]}

Replacement format: `[REDACTADO <type> sha256:<12 hex>]`, the hash being the SHA-256
of the original material in UTF-8. For key blocks and age keys the material is the
whole match; for `name = value` assignments and credential URLs it is the secret
value only, so the surrounding text stays readable. Findings never carry the material.
redact_file(path) redacts a text file in place (used by the post-write hook and
vault_precommit.py). Standard library only; patterns come from check_bundle.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_bundle as cb  # noqa: E402

EXIT_OK, EXIT_ERROR, EXIT_FOUND = 0, 1, 3

# A whole key block (header..END of the same label). A block with no END counts only
# when key body follows the header (after optional Proc-Type:/DEK-Info:/Version:/
# Comment: lines and a blank line): a first run of >= 16 base64 characters, which need
# not reach the end of its line (a truncated paste ending in `-`, `…`, `[truncated]`),
# then any further whole lines of >= 4 base64 characters, the last of which may be cut
# short (end of text, or a non-prose mark such as `…`). Every line may carry the
# prefix of an indented code block, a YAML `|` value or a blockquote (`[ \t>]*`), and a
# line break may be a real one or an escaped `\n` inside a raw JSON/JSONL string. The
# header and that body are redacted, stopping at the first thing that is not body. A
# header alone, or followed by a short word (named in prose or a codebook), is not a
# key and is left as it is.
_NL = r"(?:\r?\n|(?:\\r)?\\n)"  # a real newline, or an escaped `\n` (as check_bundle._NL_B)
_PFX = r"[ \t>]*"
_B64 = r"[A-Za-z0-9+/=]"
_BODY_LINE = _PFX + _B64 + r"{4,}[ \t]*(?=" + _NL + r'|"|\Z)'
# the last body line may be truncated: >= 4 base64 characters cut by the end of the text
# or by a mark that is not prose (`…`, `-`, `[truncated]`; not a space or punctuation)
_CUT = r"(?=\Z|[^A-Za-z0-9+/=\s.,;:!?'\")\]])"
_BLOCK_RE = re.compile(
    r"-----BEGIN ((?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?)-----.*?-----END \1-----"
    r"|-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----[ \t]*" + _NL
    + r"(?:" + _PFX + r"(?:Proc-Type|DEK-Info|Version|Comment):(?:[^\r\n\\]|\\(?![rn]))*"
    + _NL + r")*(?:" + _PFX + _NL + r")?"
    + _PFX + _B64 + r"{16,}(?:[ \t]*" + _NL + _BODY_LINE + r")*"
    + r"(?:[ \t]*" + _NL + _PFX + _B64 + r"{4,}" + _CUT + r")?"
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


MAX_FILE_BYTES = 5 * 1024 * 1024


def is_root_config(rel: str) -> bool:
    """Whether a vault-relative path (posix) is a dot-file or lies in a dot-folder at
    the vault's root (.obsidian/, .env, .mcp.json, .claude/): configuration, which
    the hooks never rewrite."""
    return rel.split("/")[0].startswith(".")


def config_notice(rel: str) -> str:
    return f"{rel}: contiene material privado; quítalo a mano (es configuración, no se reescribe)"


def decode_text(raw: bytes, max_bytes: int | None = None) -> str | None:
    """The bytes as text, or None for what is not redacted: a binary (a NUL byte or
    not UTF-8) or more than `max_bytes` (default MAX_FILE_BYTES)."""
    if len(raw) > (MAX_FILE_BYTES if max_bytes is None else max_bytes) or b"\x00" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def redact_file(path: Path | str, max_bytes: int | None = None) -> int:
    """Redact a text file in place; returns how many findings were replaced (0: the
    file was not touched, e.g. over `max_bytes`, default MAX_FILE_BYTES). Bytes are decoded without newline translation, so the
    newline style and a BOM survive; the new content goes to a temp file next to it
    and replaces it atomically."""
    p = Path(path)
    limit = MAX_FILE_BYTES if max_bytes is None else max_bytes
    if p.stat().st_size > limit:
        return 0
    text = decode_text(p.read_bytes(), limit)
    if text is None or not has_secret(text):
        return 0
    new, findings = redact_text(text)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.kairo-redact.tmp")
    try:
        tmp.write_bytes(new.encode("utf-8"))
        try:
            shutil.copymode(p, tmp)
        except OSError:
            pass
        os.replace(tmp, p)
    finally:
        if tmp.exists():
            tmp.unlink()
    return len(findings)


def redact_json_value(v):
    if isinstance(v, str):
        return redact_text(v)[0]
    if isinstance(v, list):
        return [redact_json_value(x) for x in v]
    if isinstance(v, dict):
        return {redact_text(k)[0]: redact_json_value(x) for k, x in v.items()}
    return v


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] not in ("redact", "redact-json", "check"):
        sys.stderr.write("uso: redact.py redact|redact-json|check < entrada\n")
        return EXIT_ERROR
    try:
        text = sys.stdin.buffer.read().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        sys.stderr.write("error: la entrada no es UTF-8 legible\n")
        return EXIT_ERROR
    if argv[0] == "redact-json":
        try:
            doc = json.loads(text)
        except ValueError:
            sys.stderr.write("error: la entrada no es JSON valido\n")
            return EXIT_ERROR
        payload = json.dumps(redact_json_value(doc), ensure_ascii=False, separators=(",", ":"))
        sys.stdout.buffer.write(payload.encode("utf-8"))
        sys.stdout.buffer.flush()
        return EXIT_OK
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
