#!/usr/bin/env python3
"""Strict redaction for debug samples of a sensitive dataset.

    redact_strict(text, filename_hint="") -> (text, findings)
    residual(text, filename_hint="")      -> bool   anything a strict pass would still change

Built on redact.redact_text, and it also:

- env files (hint basename `.env`, `.env.*`, `*.env`, `.env.keys`): redacts the value of
  every `KEY=value` / `export KEY=value` line, except dotenvx ciphertext
  (`encrypted:...`) and a PUBLIC-named hex/base64 value;
- a Kubernetes `kind: Secret` (YAML or JSON): redacts every value under `data:` and
  `stringData:` (plaintext secrets); a SealedSecret's `encryptedData:` is ciphertext and
  stays;
- pseudonymises third-party identities: `github.com/<owner>/<repo>` (and
  `git@github.com:owner/repo`) become `owner-<6 hex>/repo-<6 hex>`, an e-mail address
  becomes `[REDACTADO email sha256:<12 hex>]`; equal inputs give equal outputs;
- redacts every remaining high-entropy token (>= 20 characters of `[A-Za-z0-9+/=_.-]`
  with letters and digits and Shannon entropy >= 3.0 bits per character, or >= 32 hex
  digits), except inside public or ciphertext forms the study needs to parse: SOPS
  `ENC[...]`, armored `AGE ENCRYPTED FILE` / `PGP MESSAGE` / `PGP PUBLIC KEY BLOCK` /
  `CERTIFICATE` / `PUBLIC KEY` / `RSA PUBLIC KEY` blocks, ssh public keys, age
  recipients (`age1...`), SealedSecret `encryptedData:` values, dotenvx `encrypted:`
  values, SOPS PGP fingerprints (`fp:`), PUBLIC-named secp256k1 public keys, the text
  header of a binary age file, and base64 runs that decode to a certificate or public
  key. A protected form that overlaps private key material is not protected.

Over-redaction is accepted; under-redaction is not. Findings never carry the material:
`{"type", "sha256"}` (or `{"type": "repo_seudonimo"}` for a pseudonymised repository).
Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import PurePath, PurePosixPath

sys.path.insert(0, str(PurePath(__file__).parent))
import check_bundle as cb  # noqa: E402
import redact  # noqa: E402

_MASK_FMT = "⟦W{:05d}⟧"
_MASK_RE = re.compile("⟦W(\\d{5})⟧")
_MARK_RE = redact._MARK_RE

# ---- protected (public / ciphertext) forms ---------------------------------
_ARMOR_RE = re.compile(
    r"-----BEGIN (AGE ENCRYPTED FILE|PGP MESSAGE|PGP PUBLIC KEY BLOCK|CERTIFICATE|PUBLIC KEY|"
    r"RSA PUBLIC KEY)-----.*?-----END \1-----", re.S)
_SIMPLE_PROTECTED = [
    re.compile(r"ENC\[[^\]\n]*\]"),
    re.compile(r"(?<![A-Za-z0-9])(?:ssh-(?:ed25519|rsa|dss)|ecdsa-sha2-[a-z0-9]+|sk-ssh-ed25519@openssh\.com|"
               r"sk-ecdsa-sha2-nistp256@openssh\.com)[ \t]+AAAA[A-Za-z0-9+/]+=*"),
    re.compile(r"(?<![A-Za-z0-9])age1[0-9a-z]{20,}"),
    re.compile(r"encrypted:[A-Za-z0-9+/=_\-]+"),
]
_FP_RE = re.compile(r"\bfp:[ \t]*[\"']?([0-9A-Fa-f]{16,40})\b")
_PUB_HEX_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])[A-Za-z0-9_.\-]*public[A-Za-z0-9_.\-]*[\"']?[ \t]*[:=][ \t]*[\"']?"
    r"(0[23][0-9a-f]{64}|04[0-9a-f]{128})(?![0-9a-f])")
_AGE_BIN_HEADER_RE = re.compile(r"\Aage-encryption\.org/v1\r?\n.*?^---[^\n]*$", re.S | re.M)
_DECODED_PUBLIC_RE = re.compile(
    rb"-----BEGIN (?:CERTIFICATE|PUBLIC KEY|RSA PUBLIC KEY|PGP PUBLIC KEY BLOCK|AGE ENCRYPTED FILE|PGP MESSAGE)")
_PSEUDO_REPO_RE = re.compile(r"github\.com[/:]owner-[0-9a-f]{6}/repo-[0-9a-f]{6}(?:\.git)?")

# ---- rules -----------------------------------------------------------------
_ENV_LINE_RE = re.compile(r"(?m)^([ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_.\-]*)[ \t]*=[ \t]*)(.*?)[ \t]*\r?$")
_K8S_SECRET_RE = re.compile(r"(?m)^[ \t]*[\"']?kind[\"']?[ \t]*:[ \t]*[\"']?Secret[\"']?[ \t]*,?[ \t]*\r?$")
_GITHUB_RE = re.compile(
    r"(?i)((?:raw\.)?github(?:usercontent)?\.com[/:](?:repos/)?)"
    r"([A-Za-z0-9](?:[A-Za-z0-9\-]{0,38}))/([A-Za-z0-9._\-]+?)(\.git)?(?=[^A-Za-z0-9._\-]|\Z)")
_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}\b(?![.:@\w\-])")
_TOKEN_RE = re.compile(r"[A-Za-z0-9+/=_.\-]{20,}")
_HEX32_RE = re.compile(r"[0-9a-fA-F]{32,}")
_SEG_SPLIT = re.compile(r"[/._\-]+")
# this module's own pseudonyms (`d-1a2b3c`, `f-…`, `owner-…`, `repo-…`, `<field>-…`): 6 hex digits
_PSEUDONYM_SEG = re.compile(r"(?<![A-Za-z0-9])[a-z_]+-[0-9a-f]{6}(?![A-Za-z0-9])")


def _h(s: str, n: int) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:n]


def _marker(kind: str, material: str) -> tuple[str, dict]:
    fp = _h(material, 12)
    return f"[REDACTADO {kind} sha256:{fp}]", {"type": kind, "sha256": fp}


def is_env_name(hint: str) -> bool:
    name = PurePath(str(hint).replace("\\", "/")).name.lower()
    return name == ".env" or name.startswith(".env.") or name.endswith(".env")


def _yaml_values(text: str, keys: tuple[str, ...]) -> list[tuple[int, int]]:
    """Spans of every value nested under a YAML mapping key in `keys` (block or flow)."""
    spans: list[tuple[int, int]] = []
    lines = text.splitlines(keepends=True)
    offs, pos = [], 0
    for ln in lines:
        offs.append(pos)
        pos += len(ln)
    head = re.compile(r"^([ \t]*)[\"']?(" + "|".join(map(re.escape, keys)) + r")[\"']?[ \t]*:[ \t]*(.*?)[ \t]*\r?\n?$")
    entry = re.compile(r"^([ \t]*)(?:-[ \t]+)?[\"']?[^:#\"']+[\"']?[ \t]*:[ \t]*(.*?)[ \t]*\r?\n?$")
    i = 0
    while i < len(lines):
        m = head.match(lines[i])
        if not m:
            i += 1
            continue
        ind, rest = len(m.group(1)), m.group(3)
        if rest and rest not in ("|", ">") and not rest.startswith(("#",)):
            s = offs[i] + lines[i].index(rest, m.start(3))
            spans.append((s, s + len(rest)))
            i += 1
            continue
        j = i + 1
        block_ind = None  # indent of a `key: |` scalar being skipped
        while j < len(lines):
            ln = lines[j]
            if not ln.strip():
                j += 1
                continue
            li = len(ln) - len(ln.lstrip(" \t"))
            if li <= ind:
                break
            body = ln.strip()
            if block_ind is not None and li > block_ind:
                s = offs[j] + li
                spans.append((s, s + len(body)))
                j += 1
                continue
            block_ind = None
            e = entry.match(ln)
            if e and e.group(2):
                v = e.group(2)
                if re.fullmatch(r"[|>][+\-]?\d*", v):
                    block_ind = li
                else:
                    s = offs[j] + ln.rindex(v)
                    spans.append((s, s + len(v)))
            elif not e:
                s = offs[j] + li
                spans.append((s, s + len(body)))
            j += 1
        i = j
    return spans


def _protected(text: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for m in _ARMOR_RE.finditer(text):
        out.append(m.span())
    for rx in _SIMPLE_PROTECTED:
        out.extend(m.span() for m in rx.finditer(text))
    out.extend(m.span(1) for m in _FP_RE.finditer(text))
    out.extend(m.span(1) for m in _PUB_HEX_RE.finditer(text))
    out.extend(m.span() for m in _AGE_BIN_HEADER_RE.finditer(text))
    out.extend(m.span() for m in _PSEUDO_REPO_RE.finditer(text))
    for m in redact._B64_RUN_RE.finditer(text):
        raw = redact.b64_decode_loose(m.group(0))
        if raw and _DECODED_PUBLIC_RE.search(raw) and not redact._DECODED_PRIVATE_RE.search(raw):
            out.append(m.span())
    out.extend(_yaml_values(text, ("encryptedData",)))
    hard = [(s, e) for s, e, t in redact._spans(text) if t in ("private_key", "age_secret_key")]
    out = [(s, e) for s, e in out if e > s and not any(s < he and hs < e for hs, he in hard)]
    out.sort(key=lambda p: (p[0], -(p[1] - p[0])))
    merged: list[tuple[int, int]] = []
    for s, e in out:
        if merged and s < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def _mask(text: str) -> tuple[str, list[str]]:
    saved: list[str] = []
    parts, pos = [], 0
    for s, e in _protected(text):
        parts.append(text[pos:s])
        parts.append(_MASK_FMT.format(len(saved)))
        saved.append(text[s:e])
        pos = e
    parts.append(text[pos:])
    return "".join(parts), saved


def _unmask(text: str, saved: list[str]) -> str:
    return _MASK_RE.sub(lambda m: saved[int(m.group(1))], text)


def _only_mask_or_mark(v: str) -> bool:
    core = v.strip().strip("\"'")
    return not core or bool(_MASK_RE.fullmatch(core)) or bool(_MARK_RE.fullmatch(core))


def _replace_spans(text: str, spans: list[tuple[int, int]], kind: str, findings: list[dict]) -> str:
    parts, pos = [], 0
    for s, e in sorted(set(spans)):
        if s < pos:
            continue
        val = text[s:e]
        if _only_mask_or_mark(val):
            continue
        mark, f = _marker(kind, val.strip("\"'"))
        parts.append(text[pos:s])
        parts.append(mark)
        findings.append(f)
        pos = e
    parts.append(text[pos:])
    return "".join(parts)


def _env(text: str, findings: list[dict]) -> str:
    spans = []
    for m in _ENV_LINE_RE.finditer(text):
        name, val = m.group(2), m.group(3)
        if not val or val.lstrip().startswith("#") or _only_mask_or_mark(val):
            continue
        if "public" in name.lower() and re.fullmatch(r"[\"']?[A-Za-z0-9+/=]+[\"']?", val):
            continue
        spans.append(m.span(3))
    return _replace_spans(text, spans, "env_value", findings)


def _k8s_json(obj, findings: list[dict]):
    if isinstance(obj, list):
        return [_k8s_json(x, findings) for x in obj]
    if not isinstance(obj, dict):
        return obj
    out = {}
    secret = obj.get("kind") == "Secret"
    for k, v in obj.items():
        if secret and k in ("data", "stringData") and isinstance(v, dict):
            nv = {}
            for dk, dv in v.items():
                s = dv if isinstance(dv, str) else json.dumps(dv)
                if _only_mask_or_mark(s):
                    nv[dk] = dv
                else:
                    mark, f = _marker("k8s_secret_value", s)
                    findings.append(f)
                    nv[dk] = mark
            out[k] = nv
        else:
            out[k] = _k8s_json(v, findings)
    return out


def _k8s(text: str, findings: list[dict]) -> str:
    if not _K8S_SECRET_RE.search(text):
        return text
    if text.lstrip().startswith(("{", "[")):
        try:
            doc = json.loads(text)
        except ValueError:
            doc = None
        if doc is not None:
            return json.dumps(_k8s_json(doc, findings), ensure_ascii=False, indent=2)
    return _replace_spans(text, _yaml_values(text, ("data", "stringData")), "k8s_secret_value", findings)


def _pseudonymise(text: str, findings: list[dict]) -> str:
    def repo(m: re.Match) -> str:
        owner, name = m.group(2), m.group(3)
        if re.fullmatch(r"owner-[0-9a-f]{6}", owner) and re.fullmatch(r"repo-[0-9a-f]{6}", name):
            return m.group(0)
        findings.append({"type": "repo_seudonimo"})
        return (m.group(1) + "owner-" + _h("owner:" + owner.lower(), 6) + "/repo-"
                + _h("repo:" + owner.lower() + "/" + name.lower(), 6) + (m.group(4) or ""))

    text = _GITHUB_RE.sub(repo, text)

    def email(m: re.Match) -> str:
        mark, f = _marker("email", m.group(0).lower())
        findings.append(f)
        return mark

    return _EMAIL_RE.sub(email, text)


def _pathlike(tok: str) -> bool:
    segs = [s for s in _SEG_SPLIT.split(tok) if s]
    if not segs:
        return True
    for s in segs:
        if re.fullmatch(r"[a-z]+", s) or re.fullmatch(r"\d{1,4}", s):
            continue
        if re.fullmatch(r"[a-z0-9]{1,12}", s) and sum(c.isdigit() for c in s) <= 2:
            continue
        return False
    return True


def hot_token(tok: str) -> bool:
    """A token the strict pass redacts: long hex, or letters+digits with high entropy."""
    if _HEX32_RE.fullmatch(tok):
        return True
    if not (re.search(r"[A-Za-z]", tok) and re.search(r"\d", tok)):
        return False
    if _pathlike(_PSEUDONYM_SEG.sub("x", tok)):
        return False
    return cb.entropy(tok) >= 3.0


def _sweep(text: str, findings: list[dict]) -> str:
    marks = [m.span() for m in _MARK_RE.finditer(text)]
    spans = []
    for m in _TOKEN_RE.finditer(text):
        if any(a <= m.start() < b for a, b in marks):
            continue
        if hot_token(m.group(0)):
            spans.append(m.span())
    return _replace_spans(text, spans, "high_entropy", findings)


def redact_strict(text: str, filename_hint: str = "") -> tuple[str, list[dict]]:
    """The strict pass: `(redacted text, findings)`."""
    findings: list[dict] = []
    # pseudonymise first, so the pseudonyms are protected from the entropy sweep
    text = _pseudonymise(text, findings)
    masked, saved = _mask(text)
    out, base = redact.redact_text(masked)
    findings.extend(base)
    if filename_hint and is_env_name(filename_hint):
        out = _env(out, findings)
    out = _k8s(out, findings)
    out = _sweep(out, findings)
    return _unmask(out, saved), findings


def residual(text: str, filename_hint: str = "") -> bool:
    """Whether `text` still holds anything a strict pass would redact (fail closed)."""
    # has_secret runs with the protected forms masked, as the strict pass does: a public
    # `# public key: age1...` line is an assignment-shaped match on public material.
    # Private key material is never protected (_protected drops any overlap with it).
    if redact.has_secret(_mask(text)[0]):
        return True
    return bool(redact_strict(text, filename_hint)[1])


def pseudo_path(rel: str) -> str:
    """A dataset-relative path with every folder hashed (`d-<6 hex>`) and the file name kept,
    unless the name itself is a high-entropy token."""
    parts = [p for p in PurePosixPath(rel.replace("\\", "/")).parts if p not in ("", ".", "/")]
    if not parts:
        return "fichero"
    dirs = ["d-" + _h("dir:" + p, 6) for p in parts[:-1]]
    name = parts[-1]
    if any(hot_token(m.group(0)) for m in _TOKEN_RE.finditer(name)) or _EMAIL_RE.search(name) \
            or name in ("..",):
        suffix = "".join(PurePosixPath(name).suffixes[-2:])
        name = "f-" + _h("file:" + name, 6) + (suffix if len(suffix) <= 16 else "")
    return "/".join([*dirs, name])
