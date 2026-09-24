#!/usr/bin/env python3
"""Isolation check for a Kaggle/Colab transfer bundle, run BEFORE any upload.

`run-experiment` (step 2, external runtime) and `paper-to-tool` (step 4b)
build a flat, self-contained transfer bundle and ship it to a runtime outside
this machine. Anything in that bundle leaves the researcher's control. This
script walks the bundle and fails if it contains credentials, keys, tokens,
git state, or anything taken from the vault's `Papers/` / `Projects/` trees.

Interface (docs/v3-interfaces.md section 1a -- binding):

    python check_bundle.py <bundle_dir> [--verbose] [--max-bytes N]

    exit 0  clean        -- no `block` findings (`warn` findings may be present)
    exit 2  contaminated -- at least one `block` finding
    exit 1  error        -- the check could not complete (missing path, a file
                            instead of a directory, an unreadable file, a
                            crash). Callers treat 1 as NOT clean.

    stdout is exactly one JSON object, UTF-8, nothing else:
      {"status": "clean|contaminated|error",
       "findings": [{"path": "<relative/to/bundle_dir>", "kind": "<kind>",
                     "severity": "block|warn"}]}
    `status` agrees with the exit code. `findings` is [] when clean with no
    warnings. On error, findings collected before the failure are included.
    Diagnostics go to stderr. `--verbose` adds one stderr line per finding
    (rule name + line number). A matched secret value is NEVER printed --
    not on stdout, not on stderr, not truncated, not partially.

    `path` is relative to <bundle_dir> with forward slashes. A finding inside
    an archive is reported as `<archive path>!<member path>`, e.g.
    `data/extra.zip!conf/.env`.

    `--help` / `--version` print to stdout and exit 0 (not contract calls).
    A usage error (bad flag) yields the error JSON and exit 1, never argparse's
    exit 2, which would collide with `contaminated`.

Kinds. Callers branch only on the exit code / status, never on `kind`; kinds
exist so the researcher can see what to remove.

  block (bundle must not be transferred):
    env_file            `.env`, `.env.<anything>` (except the templates
                        below), `.envrc` -- runtime secrets files.
    private_key         a PEM / OpenSSH / PGP private key block in any file
                        (`-----BEGIN ... PRIVATE KEY-----`), including a JSON
                        service-account key's `private_key` field.
    ssh_key_file        `id_rsa`, `id_dsa`, `id_ecdsa`, `id_ed25519` (and
                        `*_sk` variants) or any non-public file under `.ssh/`.
    keystore            `*.p12`, `*.pfx`, `*.jks`, `*.keystore` -- binary key
                        stores whose contents cannot be inspected.
    git_directory       a `.git` directory (or `.git` gitlink file). Git
                        objects carry the full history of whatever repo it
                        came from -- for the vault that is every note ever
                        written -- so it is never shippable, whatever repo.
    git_credentials     `.git-credentials`, or a git config with a
                        `[credential]` section / helper.
    credential_url      `scheme://user:secret@host` with a non-placeholder
                        secret (git remotes with a token, DB URLs, ...).
    api_key             provider-shaped keys: AWS `AKIA`/`ASIA`, GitHub
                        `ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_`/`github_pat_`,
                        Anthropic `sk-ant-`, OpenAI `sk-`/`sk-proj-`,
                        Hugging Face `hf_`, Google `AIza`, Slack `xox?-`,
                        Stripe `sk_live_`/`rk_live_`/`*_test_`; or an
                        assignment `<...api_key|apikey|access_key|secret_key|
                        client_secret|private_key> = <value>` whose value
                        looks like a real secret (see "Placeholders").
    token               a JWT (`eyJ...` three-part); `Bearer <value>`; an
                        assignment `<...TOKEN|SECRET|PASSWORD|PASSWD|PWD|
                        CREDENTIAL> = <value>` with a real-looking value.
    netrc               `.netrc` / `_netrc` (machine passwords).
    credentials_file    well-known credential files: `kaggle.json`,
                        `.pypirc`, `.npmrc`, `.aws/credentials`,
                        `.docker/config.json`, `huggingface/token`,
                        `.config/gcloud/**` credential files.
    vault_path          a bundle file path with a `Papers` or `Projects`
                        segment (case-sensitive, as in the vault) -- a vault
                        note or project file was copied in. Reported once
                        per offending directory (or file, at the top level).
    vault_absolute_path file content with an absolute path into a Kairo vault
                        (an absolute path with a `Kairo` segment, or with
                        `vault/Papers` / `vault/Projects`). Leaks the local
                        layout and is a hard dependency on this machine.
    vault_note          a copied vault note: a markdown frontmatter `id:` of
                        the form `P-`/`H-`/`E-`/`PROJ-`/`ADR-`/`T-<digits>`
                        together with a `project:`/`projects:`/`hypothesis:`/
                        `linked_*` key; or a file named `_digest.md`,
                        `_hub.md`, `Estado-del-arte.md`.
    symlink_escape      a symlink / junction (or an archive link member)
                        whose target resolves outside the bundle. Links are
                        never followed.

  warn (reported, bundle may still be transferred):
    env_template        `.env.example`, `.env.sample`, `.env.template`,
                        `.env.dist` -- templates; their contents are still
                        scanned, so a real key inside one is a `block`.
    ssh_public_file     `id_*.pub`, `known_hosts`, `authorized_keys`,
                        `.ssh/config` -- not secret, but identifies hosts
                        and has no business in a run bundle.
    git_config          a `.gitconfig` / git `config` without credentials
                        (carries name + e-mail).
    key_file            `*.key` without a private-key block in it.
    vault_path_reference file content mentioning a vault-relative
                        `Papers/...` or `Projects/...` path. Provenance prose
                        ("implements Projects/<slug>/Experimentos/E-0001.md")
                        and the guarded local-first fallback that
                        run-experiment step 2 explicitly allows produce this;
                        an unguarded hard-coded vault path is a bug the
                        researcher must fix, but the checker cannot tell the
                        two apart, so it warns rather than blocks.
    truncated_scan      only the first --max-bytes of the file (or archive
                        member / archive total) were scanned.
    archive_unreadable  an archive that could not be opened (corrupt,
                        encrypted member); its members were NOT scanned.
    archive_unscanned   an archive format the standard library cannot read
                        (`.7z`, `.rar`) or nesting deeper than 2 levels.

Placeholders (never secrets): values starting with `$`, `%`, `{`, `<`, `[`;
values containing `your`, `xxx`, `changeme`, `example`, `placeholder`,
`dummy`, `redacted`, `replace`, `todo`, `...`, `***`; code rather than data
(`os.environ[...]`, `getenv(...)`, dotted/snake_case identifiers); values
shorter than 16 chars, with fewer than two of {lower, upper, digit}, or with
Shannon entropy below 3.0 bits/char. An env-var NAME alone is never a secret.

Legitimate bundle files -- `MANIFEST.sha256`, `E-XXXX.data.json`,
`E-XXXX.deps.txt`, `Tools/P-XXXX/<method>/...` (incl. `TOOL.md`) -- are
scanned like any other file; relative data filenames and sha256 digests are
not findings.

Binary files (a NUL in the first 8 KiB) are scanned only for provider-shaped
keys, private-key blocks and JWTs. Archives (`.zip`, `.whl`, `.tar`,
`.tar.gz`, `.tgz`, `.tar.bz2`, `.tbz2`, `.tar.xz`, `.txz`, `.gz`) are opened
with zipfile/tarfile/gzip and their members checked by name and content, up to
2 levels of nesting.

Standard library only.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import lzma
import math
import os
import re
import stat
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

__version__ = "1.0.0"

EXIT_CLEAN, EXIT_ERROR, EXIT_CONTAMINATED = 0, 1, 2
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
ARCHIVE_TOTAL_CAP = 256 * 1024 * 1024
MAX_ARCHIVE_DEPTH = 2
BLOCK, WARN = "block", "warn"


class CheckError(Exception):
    """The check could not complete (maps to exit 1)."""


# --------------------------------------------------------------------------
# value heuristics
# --------------------------------------------------------------------------

_PLACEHOLDER_WORDS = ("your", "xxx", "changeme", "change_me", "example", "placeholder",
                      "dummy", "redacted", "replace", "todo", "...", "***", "sample",
                      "insert", "none", "null", "secret_here", "key_here", "token_here")
_SECRET_CHARS = re.compile(r"^[A-Za-z0-9_\-+/=.~]+$")
_DOTTED_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$")
_SNAKE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9]*(_[A-Za-z0-9]+)+$")


def entropy(s: str) -> float:
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def is_placeholder(value: str) -> bool:
    v = value.strip().strip("\"'`")
    low = v.lower()
    if not v or v[0] in "$%{<[(":
        return True
    if any(w in low for w in _PLACEHOLDER_WORDS):
        return True
    if "environ" in low or "getenv" in low or "secrets." in low or "userdata" in low:
        return True
    return False


def looks_secret(value: str, min_len: int = 16, min_entropy: float = 3.0) -> bool:
    """A value in a `name = value` assignment that looks like real secret material."""
    v = value.strip().strip("\"'`")
    if len(v) < min_len or is_placeholder(v) or not _SECRET_CHARS.match(v):
        return False
    if _DOTTED_IDENT.match(v) or _SNAKE_IDENT.match(v):
        return False  # code (attribute access / identifier), not data
    if v.startswith(("/", "./", "../")) or v.endswith((".py", ".json", ".txt", ".csv", ".md")):
        return False  # a path
    classes = sum(bool(re.search(p, v)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]"))
    if classes < 2:
        return False
    return entropy(v) >= min_entropy


def provider_ok(value: str) -> bool:
    """Filter for provider-shaped matches: drop documented examples / filler."""
    low = value.lower()
    if any(w in low for w in ("example", "xxxx", "your", "placeholder", "redacted", "dummy")):
        return False
    body = value[4:] if len(value) > 8 else value
    return entropy(body) >= 3.0


# --------------------------------------------------------------------------
# content rules: (rule name, kind, compiled regex, validator on match)
# --------------------------------------------------------------------------

_PROVIDER_RULES = [
    ("aws_access_key_id", r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Za-z0-9])"),
    ("github_token", r"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{36,}"),
    ("github_pat", r"(?<![A-Za-z0-9_])github_pat_[A-Za-z0-9_]{22,}"),
    ("anthropic_key", r"(?<![A-Za-z0-9_-])sk-ant-[A-Za-z0-9_\-]{20,}"),
    ("openai_key", r"(?<![A-Za-z0-9_-])sk-(?!ant-)(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}"),
    ("huggingface_token", r"(?<![A-Za-z0-9_])hf_[A-Za-z0-9]{30,}"),
    ("google_api_key", r"(?<![A-Za-z0-9_])AIza[0-9A-Za-z_\-]{35}"),
    ("slack_token", r"(?<![A-Za-z0-9_])xox[abposr]-[A-Za-z0-9\-]{10,}"),
    ("stripe_key", r"(?<![A-Za-z0-9_])(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"),
]
PROVIDER_RULES = [(n, re.compile(p)) for n, p in _PROVIDER_RULES]
# the bytes-level twins, for binary files
PROVIDER_RULES_B = [(n, re.compile(p.encode())) for n, p in _PROVIDER_RULES]

PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")
PRIVATE_KEY_RE_B = re.compile(PRIVATE_KEY_RE.pattern.encode())
JWT_RE = re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")
JWT_RE_B = re.compile(JWT_RE.pattern.encode())
BEARER_RE = re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._~+/\-]{16,}=*)")
_ASSIGN = r"""["']?\s*(?::|=|:=|=>)\s*["']?([^\s"',;#)}\]]+)"""
API_ASSIGN_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])([A-Za-z0-9_.\-]*(?:api[_\-]?key|apikey|access[_\-]?key|secret[_\-]?key|"
    r"client[_\-]?secret|private[_\-]?key))" + _ASSIGN)
TOKEN_ASSIGN_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])([A-Za-z0-9_.\-]*(?:token|secret|password|passwd|pwd|credential))" + _ASSIGN)
CRED_URL_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.\-]*://([^\s:/@\"'<>]+):([^\s@/\"'<>]+)@[^\s/\"'<>]+")
GIT_CRED_SECTION_RE = re.compile(r"(?im)^\s*\[credential\b|^\s*helper\s*=")

# vault references in content
_ABS_PATH_RE = re.compile(r"(?:(?<![A-Za-z0-9])[A-Za-z]:[\\/]|(?<![A-Za-z0-9_.])/(?:Users|home|mnt|root|media|Volumes)/|~[\\/])"
                          r"[^\s\"'`<>|*?]+")
_VAULT_ABS_SEG = re.compile(r"(?i)(?:^|[\\/])kairo(?:[\\/]|$)|[\\/]vault[\\/](?:Papers|Projects)(?:[\\/]|$)")
VAULT_REL_RE = re.compile(r"(?<![A-Za-z0-9_\-.])(?:Papers|Projects)[\\/][^\s\"'`<>|]+")

_FM_RE = re.compile(r"\A\ufeff?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
_FM_ID_RE = re.compile(r"(?m)^id:\s*[\"']?(?:P|H|E|PROJ|ADR|T|TASK)-\d{3,}\b")
_FM_LINK_RE = re.compile(r"(?m)^(?:projects?|hypothesis|linked_[a-z_]+):")
_VAULT_NOTE_NAMES = {"_digest.md", "_hub.md", "estado-del-arte.md"}


def _line_of(text, pos: int) -> int:
    nl = b"\n" if isinstance(text, bytes) else "\n"
    return text.count(nl, 0, pos) + 1


# --------------------------------------------------------------------------
# the scanner
# --------------------------------------------------------------------------

class Scanner:
    def __init__(self, root: Path, max_bytes: int, verbose: bool):
        self.root = root
        self.root_resolved = root.resolve()
        self.max_bytes = max_bytes
        self.verbose = verbose
        self.findings: dict[tuple[str, str], str] = {}

    # ---- recording -----------------------------------------------------
    def add(self, path: str, kind: str, severity: str, rule: str = "", line: int | None = None):
        key = (path, kind)
        prev = self.findings.get(key)
        if prev != BLOCK:
            self.findings[key] = severity
        if self.verbose:
            where = f":{line}" if line else ""
            extra = f" rule={rule}" if rule else ""
            err(f"[{severity}] {kind} {path}{where}{extra} (value redacted)")

    def result(self) -> list[dict]:
        return [{"path": p, "kind": k, "severity": s}
                for (p, k), s in sorted(self.findings.items())]

    # ---- names ---------------------------------------------------------
    def check_name(self, parts: list[str], shown: str, prefix: str = "") -> None:
        """Checks on a (relative) path's segments. `prefix` = 'archive!' for members."""
        for i, seg in enumerate(parts[:-1]):
            if seg in ("Papers", "Projects"):
                self.add(prefix + "/".join(parts[: i + 1]), "vault_path", BLOCK, "path_segment")
                break
            if seg == ".git":
                self.add(prefix + "/".join(parts[: i + 1]), "git_directory", BLOCK, "git_dir")
                break
        name = parts[-1]
        low = name.lower()
        parent = parts[-2].lower() if len(parts) > 1 else ""
        lower_parts = [p.lower() for p in parts]
        if name in ("Papers", "Projects") and len(parts) == 1:
            self.add(shown, "vault_path", BLOCK, "path_segment")
        if low in (".env", ".envrc") or (low.startswith(".env.") and not _is_env_template(low)):
            self.add(shown, "env_file", BLOCK, "env_name")
        elif low.startswith(".env.") and _is_env_template(low):
            self.add(shown, "env_template", WARN, "env_template_name")
        if re.fullmatch(r"id_(?:rsa|dsa|ecdsa|ed25519)(?:_sk)?", low):
            self.add(shown, "ssh_key_file", BLOCK, "ssh_key_name")
        elif re.fullmatch(r"id_(?:rsa|dsa|ecdsa|ed25519)(?:_sk)?\.pub", low) or low in ("known_hosts", "authorized_keys"):
            self.add(shown, "ssh_public_file", WARN, "ssh_public_name")
        elif ".ssh" in lower_parts[:-1]:
            if low == "config" or low.endswith(".pub"):
                self.add(shown, "ssh_public_file", WARN, "ssh_dir")
            else:
                self.add(shown, "ssh_key_file", BLOCK, "ssh_dir")
        if low.endswith((".p12", ".pfx", ".jks", ".keystore")):
            self.add(shown, "keystore", BLOCK, "keystore_ext")
        if low == ".git-credentials":
            self.add(shown, "git_credentials", BLOCK, "git_credentials_name")
        if low == ".git" and len(parts) >= 1:
            self.add(shown, "git_directory", BLOCK, "gitlink_file")
        if low in (".netrc", "_netrc"):
            self.add(shown, "netrc", BLOCK, "netrc_name")
        if (low in ("kaggle.json", ".pypirc", ".npmrc")
                or (low == "credentials" and parent == ".aws")
                or (low == "config.json" and parent == ".docker")
                or (low == "token" and parent in ("huggingface", ".huggingface"))
                or ("gcloud" in lower_parts[:-1] and ("credential" in low or low.endswith(".db")))):
            self.add(shown, "credentials_file", BLOCK, "credentials_name")
        if low in _VAULT_NOTE_NAMES:
            self.add(shown, "vault_note", BLOCK, "vault_note_name")

    # ---- content -------------------------------------------------------
    def scan_bytes(self, data: bytes, shown: str, parts: list[str], total_size: int,
                   depth: int = 0) -> None:
        if total_size > self.max_bytes:
            self.add(shown, "truncated_scan", WARN, "size_cap")
            data = data[: self.max_bytes]
        low = parts[-1].lower()
        if depth < MAX_ARCHIVE_DEPTH and _archive_kind(low):
            self.scan_archive(data, shown, low, depth + 1)
            return
        if low.endswith((".7z", ".rar")):
            self.add(shown, "archive_unscanned", WARN, "unsupported_archive")
        elif _archive_kind(low):
            self.add(shown, "archive_unscanned", WARN, "nesting_depth")
        if _is_binary(data):
            self.scan_binary(data, shown)
            return
        self.scan_text(_decode(data), shown, parts)

    def scan_binary(self, data: bytes, shown: str) -> None:
        for rule, rx in PROVIDER_RULES_B:
            for m in rx.finditer(data):
                if provider_ok(m.group(0).decode("ascii", "replace")):
                    self.add(shown, "api_key", BLOCK, rule)
                    break
        if PRIVATE_KEY_RE_B.search(data):
            self.add(shown, "private_key", BLOCK, "pem_private_key")
        if JWT_RE_B.search(data):
            self.add(shown, "token", BLOCK, "jwt")

    def scan_text(self, text: str, shown: str, parts: list[str]) -> None:
        name = parts[-1].lower()
        for rule, rx in PROVIDER_RULES:
            for m in rx.finditer(text):
                if provider_ok(m.group(0)):
                    self.add(shown, "api_key", BLOCK, rule, _line_of(text, m.start()))
                    break
        m = PRIVATE_KEY_RE.search(text)
        if m:
            self.add(shown, "private_key", BLOCK, "pem_private_key", _line_of(text, m.start()))
        elif name.endswith(".key"):
            self.add(shown, "key_file", WARN, "key_ext")
        m = JWT_RE.search(text)
        if m:
            self.add(shown, "token", BLOCK, "jwt", _line_of(text, m.start()))
        for m in BEARER_RE.finditer(text):
            if looks_secret(m.group(1)):
                self.add(shown, "token", BLOCK, "bearer", _line_of(text, m.start()))
                break
        for m in API_ASSIGN_RE.finditer(text):
            if looks_secret(m.group(2)):
                self.add(shown, "api_key", BLOCK, "api_key_assignment", _line_of(text, m.start()))
                break
        for m in TOKEN_ASSIGN_RE.finditer(text):
            if looks_secret(m.group(2)):
                self.add(shown, "token", BLOCK, "secret_assignment", _line_of(text, m.start()))
                break
        for m in CRED_URL_RE.finditer(text):
            if not is_placeholder(m.group(2)) and len(m.group(2)) >= 8 \
                    and m.group(2).lower() not in ("password", "passw0rd"):
                self.add(shown, "credential_url", BLOCK, "userinfo_url", _line_of(text, m.start()))
                break
        if name == ".gitconfig" or (name == "config" and ".git" in [p.lower() for p in parts[:-1]]):
            if GIT_CRED_SECTION_RE.search(text):
                self.add(shown, "git_credentials", BLOCK, "credential_helper")
            else:
                self.add(shown, "git_config", WARN, "gitconfig")
        # vault references
        for m in _ABS_PATH_RE.finditer(text):
            if _VAULT_ABS_SEG.search(m.group(0)):
                self.add(shown, "vault_absolute_path", BLOCK, "abs_vault_path", _line_of(text, m.start()))
                break
        m = VAULT_REL_RE.search(text)
        if m:
            self.add(shown, "vault_path_reference", WARN, "rel_vault_path", _line_of(text, m.start()))
        if name.endswith(".md"):
            fm = _FM_RE.match(text)
            if fm and _FM_ID_RE.search(fm.group(1)) and _FM_LINK_RE.search(fm.group(1)):
                self.add(shown, "vault_note", BLOCK, "kairo_frontmatter")

    # ---- archives ------------------------------------------------------
    def scan_archive(self, data: bytes, shown: str, low: str, depth: int) -> None:
        kind = _archive_kind(low)
        budget = [ARCHIVE_TOTAL_CAP]
        try:
            if kind == "zip":
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        mode = (info.external_attr >> 16) & 0xFFFF
                        if stat.S_ISLNK(mode):
                            target = zf.read(info).decode("utf-8", "replace")
                            self._archive_link(shown, info.filename, target)
                            continue
                        if info.flag_bits & 0x1:
                            self.add(shown, "archive_unreadable", WARN, "encrypted_member")
                            self._member_name(shown, info.filename)
                            continue
                        with zf.open(info) as fh:
                            blob = fh.read(min(self.max_bytes, budget[0]) + 1)
                        self._member(shown, info.filename, blob, info.file_size, depth, budget)
            elif kind == "tar":
                with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
                    for ti in tf:
                        if ti.issym() or ti.islnk():
                            self._archive_link(shown, ti.name, ti.linkname)
                            continue
                        if not ti.isfile():
                            continue
                        fh = tf.extractfile(ti)
                        blob = fh.read(min(self.max_bytes, budget[0]) + 1) if fh else b""
                        self._member(shown, ti.name, blob, ti.size, depth, budget)
            elif kind == "gz":
                with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
                    blob = gz.read(self.max_bytes + 1)
                inner = low[:-3] or "member"
                self._member(shown, PurePosixPath(inner).name, blob,
                             len(blob), depth, budget)
        except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError, lzma.LZMAError,
                zlib_error(), RuntimeError, ValueError, NotImplementedError):
            self.add(shown, "archive_unreadable", WARN, "archive_open")

    def _member_name(self, shown: str, member: str) -> list[str]:
        parts = [p for p in member.replace("\\", "/").split("/") if p not in ("", ".")]
        if parts:
            self.check_name(parts, f"{shown}!{'/'.join(parts)}", prefix=f"{shown}!")
        return parts

    def _member(self, shown, member, blob, size, depth, budget) -> None:
        parts = self._member_name(shown, member)
        if not parts:
            return
        mshown = f"{shown}!{'/'.join(parts)}"
        if budget[0] <= 0:
            self.add(shown, "truncated_scan", WARN, "archive_total_cap")
            return
        budget[0] -= len(blob)
        self.scan_bytes(blob, mshown, parts, max(size, len(blob)), depth)

    def _archive_link(self, shown: str, member: str, target: str) -> None:
        parts = self._member_name(shown, member)
        t = target.replace("\\", "/")
        base = PurePosixPath(*parts[:-1]) if len(parts) > 1 else PurePosixPath(".")
        escapes = t.startswith("/") or re.match(r"^[A-Za-z]:", t) is not None
        if not escapes:
            depth = 0
            for seg in (base / t).parts:
                depth = depth - 1 if seg == ".." else depth + (seg != ".")
                if depth < 0:
                    escapes = True
                    break
        if escapes:
            self.add(f"{shown}!{'/'.join(parts)}", "symlink_escape", BLOCK, "archive_link")

    # ---- the walk ------------------------------------------------------
    def rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix()

    def link_escapes(self, p: Path) -> bool:
        try:
            target = Path(os.path.realpath(p))
        except OSError:
            return True
        try:
            target.relative_to(self.root_resolved)
            return False
        except ValueError:
            return True

    def walk(self) -> None:
        errors: list[str] = []

        def onerror(e: OSError):
            errors.append(f"{getattr(e, 'filename', '?')}: {e.strerror or e}")

        for dirpath, dirnames, filenames in os.walk(self.root, topdown=True, onerror=onerror,
                                                    followlinks=False):
            d = Path(dirpath)
            keep = []
            for dn in dirnames:
                p = d / dn
                relp = self.rel(p)
                if _is_link(p):
                    if self.link_escapes(p):
                        self.add(relp, "symlink_escape", BLOCK, "dir_link")
                    continue  # never follow
                if dn == ".git":
                    self.add(relp, "git_directory", BLOCK, "git_dir")
                    continue  # do not descend into git objects
                keep.append(dn)
            dirnames[:] = keep
            for fn in filenames:
                p = d / fn
                relp = self.rel(p)
                parts = relp.split("/")
                if _is_link(p):
                    if self.link_escapes(p):
                        self.add(relp, "symlink_escape", BLOCK, "file_link")
                    self.check_name(parts, relp)
                    continue
                self.check_name(parts, relp)
                try:
                    size = p.stat().st_size
                    with p.open("rb") as fh:
                        data = fh.read(self.max_bytes + 1 if not _archive_kind(fn.lower())
                                       else max(self.max_bytes, ARCHIVE_TOTAL_CAP) + 1)
                except OSError as e:
                    raise CheckError(f"cannot read {relp}: {e.strerror or e}") from None
                if _archive_kind(fn.lower()):
                    if size > ARCHIVE_TOTAL_CAP:
                        self.add(relp, "truncated_scan", WARN, "archive_size_cap")
                        self.add(relp, "archive_unreadable", WARN, "archive_too_large")
                        continue
                    self.scan_archive(data, relp, fn.lower(), 1)
                else:
                    self.scan_bytes(data, relp, parts, size)
        if errors:
            raise CheckError("cannot read part of the bundle: " + "; ".join(errors))


def zlib_error():
    import zlib
    return zlib.error


def _is_env_template(low: str) -> bool:
    return low.split(".env.", 1)[1] in ("example", "sample", "template", "dist", "defaults", "tmpl")


def _archive_kind(low: str) -> str | None:
    if low.endswith((".zip", ".whl", ".jar")):
        return "zip"
    if low.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")):
        return "tar"
    if low.endswith(".gz"):
        return "gz"
    return None


def _is_link(p: Path) -> bool:
    if p.is_symlink():
        return True
    isj = getattr(os.path, "isjunction", None)
    if isj is not None:
        return isj(p)
    try:  # Windows junction on Python < 3.12
        st = os.lstat(p)
        return bool(getattr(st, "st_reparse_tag", 0) == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", -1))
    except OSError:
        return False


def _is_binary(data: bytes) -> bool:
    head = data[:8192]
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return False
    return b"\x00" in head


def _decode(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    return data.decode("utf-8", "replace")


# --------------------------------------------------------------------------
# I/O
# --------------------------------------------------------------------------

def err(msg: str) -> None:
    try:
        sys.stderr.buffer.write((msg + "\n").encode("utf-8", "backslashreplace"))
        sys.stderr.flush()
    except (AttributeError, ValueError):
        print(msg, file=sys.stderr)


def emit(status: str, findings: list[dict]) -> None:
    payload = json.dumps({"status": status, "findings": findings}, ensure_ascii=False)
    out = getattr(sys.stdout, "buffer", None)
    if out is not None:
        out.write(payload.encode("utf-8") + b"\n")
        out.flush()
    else:
        sys.stdout.write(payload + "\n")


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # never argparse's exit 2 (= contaminated)
        raise CheckError(f"usage: {message}")


def run(argv: list[str] | None) -> int:
    ap = _Parser(prog="check_bundle.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("bundle_dir", type=Path)
    ap.add_argument("--verbose", action="store_true",
                    help="one stderr line per finding (rule, line); values are never printed")
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES,
                    help=f"bytes scanned per file (default {DEFAULT_MAX_BYTES}); beyond -> truncated_scan")
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args(argv)
    if a.max_bytes <= 0:
        raise CheckError("--max-bytes must be positive")
    root = a.bundle_dir
    if not root.exists():
        raise CheckError(f"bundle_dir does not exist: {root}")
    if not root.is_dir():
        raise CheckError(f"bundle_dir is not a directory: {root}")
    sc = Scanner(root, a.max_bytes, a.verbose)
    try:
        sc.walk()
    except CheckError as e:
        err(f"error: {e}")
        emit("error", sc.result())
        return EXIT_ERROR
    findings = sc.result()
    blocked = any(f["severity"] == BLOCK for f in findings)
    n_block = sum(f["severity"] == BLOCK for f in findings)
    err(f"check_bundle: {'CONTAMINATED' if blocked else 'clean'} -- "
        f"{n_block} block, {len(findings) - n_block} warn finding(s) in {root}")
    emit("contaminated" if blocked else "clean", findings)
    return EXIT_CONTAMINATED if blocked else EXIT_CLEAN


def main(argv: list[str] | None = None) -> int:
    try:
        return run(argv)
    except SystemExit as e:  # --help / --version
        if e.code in (0, None):
            return 0
        err(f"error: exit {e.code}")
        emit("error", [])
        return EXIT_ERROR
    except CheckError as e:
        err(f"error: {e}")
        emit("error", [])
        return EXIT_ERROR
    except Exception as e:  # noqa: BLE001 -- a crash must still be exit 1 + valid JSON
        err(f"error: internal failure: {type(e).__name__}")
        emit("error", [])
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
