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
                        service-account key's `private_key` field; a PuTTY key
                        (`PuTTY-User-Key-File-<n>:`).
    ssh_key_file        `id_rsa`, `id_dsa`, `id_ecdsa`, `id_ed25519` (and
                        `*_sk` variants), `*.ppk`, or any non-public file
                        under `.ssh/`.
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
                        Stripe `sk_live_`/`rk_live_`/`*_test_`, GitLab
                        `glpat-`/`gldt-`/`glrt-`/`glptt-`/`glsoat-`/`glft-`/
                        `gloas-`/`glcbt-`; or an assignment `<...api_key|
                        apikey|access_key|secret_key|client_secret|
                        private_key|key|_key> = <value>` whose value looks
                        like a real secret (see "Placeholders"). `key` counts
                        as a whole name or a `_`/`-`/`.` suffix (`KAGGLE_KEY`,
                        JSON `"key":`), not inside a word (`monkey`, `hotkey`),
                        not `keys` / `key_path`, not `public_key` / `pub_key`.
                        The name may be a quoted subscript:
                        `os.environ["X_API_KEY"] = "..."`.
    token               a JWT (`eyJ...` three-part); `Bearer <value>`; an
                        assignment `<...TOKEN|SECRET|PASSWORD|PASSWD|PWD|
                        CREDENTIAL> = <value>` with a real-looking value; a
                        `<...password|passwd|passphrase|pwd|_pass> = <value>`
                        whose value may contain punctuation (`!`, `#`, `@`,
                        ...): >= 12 chars, a punctuation char plus two of
                        {lower, upper, digit}, entropy >= 3.0, not code.
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
                        the form `P-`/`H-`/`E-`/`C-`/`EVO-`/`F-`/`PROJ-`/`ADR-`/`T-<digits>`
                        together with a `project:`/`projects:`/`hypothesis:`/
                        `linked_*` key; or a file named `_digest.md`, `_ledger.md`,
                        `_hub.md`, `Estado-del-arte.md`.
    symlink_escape      a symlink / junction (or an archive link member)
                        whose target resolves outside the bundle. Links are
                        never followed.
    archive_unreadable  an archive (by extension or magic bytes) that could
                        not be read: corrupt, truncated, an encrypted member,
                        an unsupported compression method. What it holds was
                        not seen, so it cannot pass.
    archive_unscanned   an archive format the standard library cannot read
                        (`.7z`, `.rar`, `.zst`, `.lz4`, `.cab`, ... by
                        extension or magic bytes), or nesting deeper than
                        MAX_ARCHIVE_DEPTH (4) levels.
    truncated_scan      a top-level archive whose members decompress to more
                        than EXPANDED_CAP (16 GiB) in total -- the zip-bomb
                        guard; the rest was not scanned.

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

Placeholders (never secrets): values starting with `$`, `%`, `{`, `<`, `[`;
values containing `your`, `xxx`, `changeme`, `example`, `placeholder`,
`dummy`, `redacted`, `replace`, `todo`, `...`, `***`; code rather than data
(`os.environ[...]`, `getenv(...)`, calls / subscripts, dotted identifiers,
single-case snake_case / UPPER_SNAKE identifiers whose segments are letters
optionally followed by digits -- `default_token_v2`, not `Ab3dE_f9GhK2mN`); values
shorter than 16 chars, with fewer than two of {lower, upper, digit}, or with
Shannon entropy below 3.0 bits/char. An env-var NAME alone is never a secret.

Legitimate bundle files -- `MANIFEST.sha256`, `E-XXXX.data.json`,
`E-XXXX.deps.txt`, `Tools/P-XXXX/<method>/...` (incl. `TOOL.md`) -- are
scanned like any other file; relative data filenames and sha256 digests are
not findings.

Nothing is skipped for size: every file and archive member is read to the
end in windows of --max-bytes (default 8 MiB) that overlap by
min(64 KiB, window/4), so a match straddling a window edge is still seen.
Text is UTF-8, or UTF-16 (BOM, or no BOM but a NUL in every other byte).
Binary files (another NUL in the first 8 KiB) are scanned only for
provider-shaped keys, private-key blocks and JWTs. Archives -- recognised by
extension (`.zip`, `.whl`, `.jar`, `.egg`, office/`.epub` zips, `.tar`,
`.tar.gz`, `.tgz`, `.tar.bz2`, `.tar.xz`, ..., `.gz`, `.bz2`, `.xz`) or by
magic bytes, whatever the name -- are opened with zipfile/tarfile/gzip/bz2/
lzma and every member is streamed and checked by name and content, up to
MAX_ARCHIVE_DEPTH levels of nesting. A transfer gate must not pass what it did
not read, so an archive that cannot be read is a `block`, never a `warn`.

Standard library only.
"""

from __future__ import annotations

import argparse
import bz2
import gzip
import json
import lzma
import math
import os
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

__version__ = "1.2.0"

EXIT_CLEAN, EXIT_ERROR, EXIT_CONTAMINATED = 0, 1, 2
DEFAULT_MAX_BYTES = 8 * 1024 * 1024       # scan window (bytes held in memory per file)
MIN_WINDOW = 1024
MAX_OVERLAP = 64 * 1024                   # windows overlap by min(this, window // 4)
EXPANDED_CAP = 16 * 1024 ** 3             # decompressed bytes per top-level archive
SPOOL_IN_MEMORY = 32 * 1024 * 1024        # nested archives spill to a temp file beyond this
SNIFF = 8192
MAX_ARCHIVE_DEPTH = 4
BLOCK, WARN = "block", "warn"


class CheckError(Exception):
    """The check could not complete (maps to exit 1)."""


class _ExpandedCapExceeded(Exception):
    """An archive decompresses to more than EXPANDED_CAP bytes (bomb guard)."""


# --------------------------------------------------------------------------
# value heuristics
# --------------------------------------------------------------------------

_PLACEHOLDER_WORDS = ("your", "xxx", "changeme", "change_me", "example", "placeholder",
                      "dummy", "redacted", "replace", "todo", "...", "***", "sample",
                      "insert", "none", "null", "secret_here", "key_here", "token_here")
_SECRET_CHARS = re.compile(r"^[A-Za-z0-9_\-+/=.~]+$")
_DOTTED_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$")
# snake_case / UPPER_SNAKE identifiers: one case only, and each segment is letters
# optionally followed by digits (`name_v2`, `x86_64`), never digits mixed into
# letters (`f9GhK2mN`) -- a mixed-case or digit-riddled value with `_` is data.
_SNAKE_IDENT = re.compile(r"^_*(?:[a-z]+[0-9]*|[0-9]+)(?:_+(?:[a-z]+[0-9]*|[0-9]+))+_*$"
                          r"|^_*(?:[A-Z]+[0-9]*|[0-9]+)(?:_+(?:[A-Z]+[0-9]*|[0-9]+))+_*$")
_CODE_VALUE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*[(\[]")  # a call / subscript: code, not data
_MEMBER_EXPR = re.compile(r"^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+[^\w.]")  # `this.nextId++`
_PATHLIKE = re.compile(r"/[\w.\-]*\.[A-Za-z][A-Za-z0-9]{0,7}$")  # `dir/sub/part-0.parquet`
_PUNCT = re.compile(r"[^\sA-Za-z0-9_]")  # `_` is an identifier char, not punctuation


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


_SHORT_WORDS = {"is", "in", "of", "to", "by", "id", "on", "at", "up", "as", "or", "if", "no", "do",
                "be", "an", "it", "db", "io", "ui", "os", "ok", "js", "co", "fn", "op", "ms", "px"}
_WORD_TOKEN = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|[0-9]+[A-Z]?(?![a-z])|_+")


def _wordy_ident(v: str) -> bool:
    """A CamelCase / word-built identifier (`QuadraticTermKey`, `_ArrayLikeInt_co`,
    `componentWillUnmount`, `PositionalIndexer2D`): split into words, at most one word
    shorter than 3 letters (common 2-letter words like `is`/`by`/`id` don't count),
    at most 2 digits. Random base62 rarely passes (measured:
    ~2% at 16 chars, ~0.6% at 20, <0.01% at 32) -- it breaks into many 1-2 char words."""
    if not re.fullmatch(r"_*[A-Za-z][A-Za-z0-9_]*", v) or sum(c.isdigit() for c in v) > 2:
        return False
    short = sum(1 for t in _WORD_TOKEN.findall(v)
                if len(t) < 3 and not t.startswith("_") and not t[0].isdigit()
                and t.lower() not in _SHORT_WORDS)
    return short <= 1


def _word_path(v: str) -> bool:
    """`America/Los_Angeles`, `models/bert_base`: every `/` segment is a word identifier."""
    segs = [x for x in v.split("/") if x]
    return "/" in v and len(segs) >= 2 and all(
        re.fullmatch(r"[a-z]+", x) or _SNAKE_IDENT.match(x) or _wordy_ident(x) for x in segs)


def looks_secret(value: str, min_len: int = 16, min_entropy: float = 3.0) -> bool:
    """A value in a `name = value` assignment that looks like real secret material."""
    v = value.strip().strip("\"'`")
    if len(v) < min_len or is_placeholder(v) or not _SECRET_CHARS.match(v):
        return False
    if _DOTTED_IDENT.match(v) or _SNAKE_IDENT.match(v) or _MEMBER_EXPR.match(v) or _wordy_ident(v):
        return False  # code (attribute access / identifier), not data
    if v.startswith(("/", "./", "../")) or v.endswith((".py", ".json", ".txt", ".csv", ".md")) \
            or _PATHLIKE.search(v) or _word_path(v):
        return False  # a path
    classes = sum(bool(re.search(p, v)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]"))
    if classes < 2:
        return False
    return entropy(v) >= min_entropy


def looks_password(value: str) -> bool:
    """A password-ish value: may contain punctuation (`Hunter2!Secure#...`)."""
    v = value.strip()
    if looks_secret(v):
        return True
    if len(v) < 12 or is_placeholder(v) or _CODE_VALUE.match(v) or _DOTTED_IDENT.match(v) \
            or _MEMBER_EXPR.match(v):
        return False
    if not _PUNCT.search(v) or re.search(r"\s", v):
        return False
    classes = sum(bool(re.search(p, v)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]")) + 1
    return classes >= 3 and entropy(v) >= 3.0


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
    ("gitlab_token", r"(?<![A-Za-z0-9_])gl(?:pat|dt|rt|ptt|soat|ft|oas|cbt)-[A-Za-z0-9_\-]{20,}"),
]
PROVIDER_RULES = [(n, re.compile(p)) for n, p in _PROVIDER_RULES]
# the bytes-level twins, for binary files
PROVIDER_RULES_B = [(n, re.compile(p.encode())) for n, p in _PROVIDER_RULES]

PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"
                            r"|PuTTY-User-Key-File-\d+:")
# In binaries a bare header is a common library constant (OpenSSL, Azure SDK, ...), so
# there it counts only when a key body follows (or, for PuTTY, a key-type).
_NL_B = rb"(?:\r?\n|(?:\\r)?\\n)"  # a real newline, or an escaped `\n` (JSON)
PRIVATE_KEY_RE_B = re.compile(
    rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----" + _NL_B
    + rb"(?:[A-Za-z][A-Za-z-]*: [^\r\n\\]*" + _NL_B + rb")*" + _NL_B + rb"?[A-Za-z0-9+/=]{32}"
    + rb"|PuTTY-User-Key-File-\d+: *[a-z][a-z0-9-]+")
JWT_RE = re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")
JWT_RE_B = re.compile(JWT_RE.pattern.encode())
BEARER_RE = re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._~+/\-]{16,}=*)")
# after the name: an optional closing quote and subscript bracket (`os.environ["X_KEY"] =`),
# then `:` / `=` / `:=` / `=>` (never `==`)
_ASSIGN_OP = r"""["']?\]?\s*(?::=|=>|:|=(?!=))\s*"""
_ASSIGN = _ASSIGN_OP + r"""["']?([^\s"',;#)}\]]+)"""
# `key` as a whole name or a `_key` / `-key` / `.key` suffix (KAGGLE_KEY, "key": ...), but
# not `monkey`, `hotkey`, `keys`, `key_path`, or `public_key` / `pub_key`.
_BARE_KEY = r"(?:(?<![A-Za-z0-9])|(?<=[_.\-]))(?<!public[_.\-])(?<!pub[_.\-])key"
API_ASSIGN_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])([A-Za-z0-9_.\-]*(?:api[_\-]?key|apikey|access[_\-]?key|secret[_\-]?key|"
    r"client[_\-]?secret|private[_\-]?key|" + _BARE_KEY + r"))" + _ASSIGN)
TOKEN_ASSIGN_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])([A-Za-z0-9_.\-]*(?:token|secret|password|passwd|pwd|credential))" + _ASSIGN)
# password-ish names take punctuation in the value: quoted -> up to the closing quote,
# unquoted -> up to whitespace, a quote or `,;(){}[]<>` (so `#` / `!` stay in the value);
# `pass` only as a whole name or a `_`/`-` suffix (`DB_PASS`), not `this.pass` / `bypass`
PASSWORD_ASSIGN_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])([A-Za-z0-9_.\-]*(?:password|passwd|passphrase|pwd|"
    r"(?:(?<![A-Za-z0-9.])|(?<=[_\-]))pass))" + _ASSIGN_OP
    + r"""(?:"([^"\n]*)"|'([^'\n]*)'|([^\s"'`,;(){}\[\]<>]+))""")
CRED_URL_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.\-]*://([^\s:/@\"'<>]+):([^\s@/\"'<>]+)@[^\s/\"'<>]+")
GIT_CRED_SECTION_RE = re.compile(r"(?im)^\s*\[credential\b|^\s*helper\s*=")

# vault references in content
_ABS_PATH_RE = re.compile(r"(?:(?<![A-Za-z0-9])[A-Za-z]:[\\/]|(?<![A-Za-z0-9_.])/(?:Users|home|mnt|root|media|Volumes)/|~[\\/])"
                          r"[^\s\"'`<>|*?]+")
_VAULT_ABS_SEG = re.compile(r"(?i)(?:^|[\\/])kairo(?:[\\/]|$)|[\\/]vault[\\/](?:Papers|Projects)(?:[\\/]|$)")
VAULT_REL_RE = re.compile(r"(?<![A-Za-z0-9_\-.])(?:Papers|Projects)[\\/][^\s\"'`<>|]+")

_FM_RE = re.compile(r"\A\ufeff?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
_FM_ID_RE = re.compile(r"(?m)^id:\s*[\"']?(?:P|H|E|C|EVO|F|PROJ|ADR|T|TASK)-\d{3,}\b")
_FM_LINK_RE = re.compile(r"(?m)^(?:projects?|hypothesis|linked_[a-z_]+):")
_VAULT_NOTE_NAMES = {"_digest.md", "_ledger.md", "_hub.md", "estado-del-arte.md"}


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
        self.window = max(MIN_WINDOW, max_bytes + (max_bytes & 1))
        self.overlap = min(MAX_OVERLAP, self.window // 4) & ~1
        self.expanded_left = EXPANDED_CAP
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
        if low.endswith(".ppk"):
            self.add(shown, "ssh_key_file", BLOCK, "putty_key_name")
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
    def scan_stream(self, fh, shown: str, parts: list[str], depth: int,
                    seekable: bool = False) -> None:
        """Scan one file / archive member from a binary stream, all of it.

        Content is read in overlapping windows, so size never skips content. Archives
        (by extension or magic bytes) are opened and their members streamed the same way.
        """
        head = _read_full(fh, SNIFF)
        if not head:
            return  # empty (even if named `.zip`): nothing unread
        low = parts[-1].lower()
        akind = _archive_kind(low) or _magic_kind(head)
        if akind == "unsupported":
            self.add(shown, "archive_unscanned", BLOCK, "unsupported_archive")
            return
        if akind:
            if depth >= MAX_ARCHIVE_DEPTH:
                self.add(shown, "archive_unscanned", BLOCK, "nesting_depth")
                return
            if seekable:
                fh.seek(0)
                self.scan_archive(fh, shown, akind, low, depth + 1)
                return
            with tempfile.SpooledTemporaryFile(max_size=SPOOL_IN_MEMORY) as spool:
                spool.write(head)
                shutil.copyfileobj(fh, spool, 1024 * 1024)
                spool.seek(0)
                self.scan_archive(spool, shown, akind, low, depth + 1)
            return
        self.scan_content(head, fh, shown, parts)

    def scan_content(self, head: bytes, fh, shown: str, parts: list[str]) -> None:
        name = parts[-1].lower()
        codec = _text_codec(head)
        state = {"private_key": False, "git_cred": False}
        for buf, line0, first in _windows(fh, head, self.window, self.overlap):
            if codec is None:
                self.scan_binary(buf, shown, state)
                continue
            if codec.startswith("utf-16"):
                self.scan_binary(buf, shown, state)  # raw ASCII runs too, in case it is not text
            self.scan_text(buf.decode(codec, "replace"), shown, parts, line0, first, state)
        if codec is not None:
            if not state["private_key"] and name.endswith(".key"):
                self.add(shown, "key_file", WARN, "key_ext")
            if name == ".gitconfig" or (name == "config" and ".git" in [p.lower() for p in parts[:-1]]):
                if state["git_cred"]:
                    self.add(shown, "git_credentials", BLOCK, "credential_helper")
                else:
                    self.add(shown, "git_config", WARN, "gitconfig")

    def scan_binary(self, data: bytes, shown: str, state: dict) -> None:
        for rule, rx in PROVIDER_RULES_B:
            for m in rx.finditer(data):
                if provider_ok(m.group(0).decode("ascii", "replace")):
                    self.add(shown, "api_key", BLOCK, rule)
                    break
        if PRIVATE_KEY_RE_B.search(data):
            state["private_key"] = True
            self.add(shown, "private_key", BLOCK, "private_key_block")
        if JWT_RE_B.search(data):
            self.add(shown, "token", BLOCK, "jwt")

    def scan_text(self, text: str, shown: str, parts: list[str], line0: int = 1,
                  first: bool = True, state: dict | None = None) -> None:
        state = state if state is not None else {}
        name = parts[-1].lower()

        def line(pos):
            return line0 + text.count("\n", 0, pos)

        for rule, rx in PROVIDER_RULES:
            for m in rx.finditer(text):
                if provider_ok(m.group(0)):
                    self.add(shown, "api_key", BLOCK, rule, line(m.start()))
                    break
        m = PRIVATE_KEY_RE.search(text)
        if m:
            state["private_key"] = True
            self.add(shown, "private_key", BLOCK, "private_key_block", line(m.start()))
        m = JWT_RE.search(text)
        if m:
            self.add(shown, "token", BLOCK, "jwt", line(m.start()))
        for m in BEARER_RE.finditer(text):
            if looks_secret(m.group(1)):
                self.add(shown, "token", BLOCK, "bearer", line(m.start()))
                break
        for m in API_ASSIGN_RE.finditer(text):
            if looks_secret(m.group(2)):
                self.add(shown, "api_key", BLOCK, "api_key_assignment", line(m.start()))
                break
        for m in TOKEN_ASSIGN_RE.finditer(text):
            if looks_secret(m.group(2)):
                self.add(shown, "token", BLOCK, "secret_assignment", line(m.start()))
                break
        for m in PASSWORD_ASSIGN_RE.finditer(text):
            value = next(g for g in m.groups()[1:] if g is not None)
            if looks_password(value):
                self.add(shown, "token", BLOCK, "password_assignment", line(m.start()))
                break
        for m in CRED_URL_RE.finditer(text):
            if not is_placeholder(m.group(2)) and len(m.group(2)) >= 8 \
                    and m.group(2).lower() not in ("password", "passw0rd"):
                self.add(shown, "credential_url", BLOCK, "userinfo_url", line(m.start()))
                break
        if GIT_CRED_SECTION_RE.search(text):
            state["git_cred"] = True
        # vault references
        for m in _ABS_PATH_RE.finditer(text):
            if _VAULT_ABS_SEG.search(m.group(0)):
                self.add(shown, "vault_absolute_path", BLOCK, "abs_vault_path", line(m.start()))
                break
        m = VAULT_REL_RE.search(text)
        if m:
            self.add(shown, "vault_path_reference", WARN, "rel_vault_path", line(m.start()))
        if first and name.endswith(".md"):
            fm = _FM_RE.match(text)
            if fm and _FM_ID_RE.search(fm.group(1)) and _FM_LINK_RE.search(fm.group(1)):
                self.add(shown, "vault_note", BLOCK, "kairo_frontmatter")

    # ---- archives ------------------------------------------------------
    def scan_archive(self, src, shown: str, kind: str, low: str, depth: int) -> None:
        """Open an archive from a seekable binary stream; stream every member.

        Anything that prevents reading a member (corrupt, truncated, encrypted, an
        unsupported compression method) is an `archive_unreadable` block.
        """
        try:
            if kind == "zip":
                with zipfile.ZipFile(src) as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        mode = (info.external_attr >> 16) & 0xFFFF
                        if stat.S_ISLNK(mode):
                            target = zf.read(info).decode("utf-8", "replace")
                            self._archive_link(shown, info.filename, target)
                            continue
                        if info.flag_bits & 0x1:
                            self.add(shown, "archive_unreadable", BLOCK, "encrypted_member")
                            self._member_name(shown, info.filename)
                            continue
                        with zf.open(info) as fh:
                            self._member(shown, info.filename, fh, depth)
            elif kind == "tar":
                with tarfile.open(fileobj=src, mode="r:*") as tf:
                    for ti in tf:
                        if ti.issym() or ti.islnk():
                            self._archive_link(shown, ti.name, ti.linkname)
                            continue
                        if not ti.isfile():
                            continue
                        fh = tf.extractfile(ti)
                        if fh is None:
                            self._member_name(shown, ti.name)
                            continue
                        with fh:
                            self._member(shown, ti.name, fh, depth)
            else:  # single-stream compression: gz / bz2 / xz
                opener = {"gz": gzip.GzipFile, "bz2": bz2.BZ2File, "xz": lzma.LZMAFile}[kind]
                ext = {"gz": (".gz",), "bz2": (".bz2",), "xz": (".xz", ".lzma")}[kind]
                inner = PurePosixPath(low).name
                for e in ext:
                    if inner.endswith(e) and len(inner) > len(e):
                        inner = inner[: -len(e)]
                        break
                with opener(fileobj=src) if kind == "gz" else opener(src) as fh:
                    self._member(shown, inner, fh, depth)
        except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError, lzma.LZMAError,
                zlib_error(), RuntimeError, ValueError, NotImplementedError, KeyError):
            self.add(shown, "archive_unreadable", BLOCK, "archive_read")

    def _member_name(self, shown: str, member: str) -> list[str]:
        parts = [p for p in member.replace("\\", "/").split("/") if p not in ("", ".")]
        if parts:
            self.check_name(parts, f"{shown}!{'/'.join(parts)}", prefix=f"{shown}!")
        return parts

    def _member(self, shown, member, fh, depth) -> None:
        parts = self._member_name(shown, member)
        if not parts:
            return
        self.scan_stream(_Counted(fh, self), f"{shown}!{'/'.join(parts)}", parts, depth)

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
                self.expanded_left = EXPANDED_CAP
                try:
                    with p.open("rb") as fh:
                        self.scan_stream(fh, relp, parts, 0, seekable=True)
                except _ExpandedCapExceeded:
                    self.add(relp, "truncated_scan", BLOCK, "expanded_cap")
                except OSError as e:
                    raise CheckError(f"cannot read {relp}: {e.strerror or e}") from None
        if errors:
            raise CheckError("cannot read part of the bundle: " + "; ".join(errors))


class _Counted:
    """Read-only wrapper for an archive member stream that enforces EXPANDED_CAP."""

    def __init__(self, fh, scanner: Scanner):
        self.fh, self.sc = fh, scanner

    def read(self, n: int = -1) -> bytes:
        b = self.fh.read(n)
        self.sc.expanded_left -= len(b)
        if self.sc.expanded_left < 0:
            raise _ExpandedCapExceeded()
        return b


def _read_full(fh, n: int) -> bytes:
    chunks, got = [], 0
    while got < n:
        b = fh.read(n - got)
        if not b:
            break
        chunks.append(b)
        got += len(b)
    return b"".join(chunks)


def _windows(fh, head: bytes, win: int, overlap: int):
    """Yield (window bytes, line number of its first byte, is_first) over the whole stream.

    Consecutive windows share `overlap` bytes, so any match no longer than `overlap`
    is seen whole in at least one window. `win` and `overlap` are even, which keeps
    UTF-16 windows aligned to code units.
    """
    buf = head + _read_full(fh, max(0, win - len(head)))
    line0, first = 1, True
    while True:
        nxt = _read_full(fh, win - overlap)
        yield buf, line0, first
        if not nxt:
            return
        advance = len(buf) - overlap
        line0 += buf.count(b"\n", 0, advance)
        buf = buf[advance:] + nxt
        first = False


def zlib_error():
    import zlib
    return zlib.error


def _is_env_template(low: str) -> bool:
    return low.split(".env.", 1)[1] in ("example", "sample", "template", "dist", "defaults", "tmpl")


_ZIP_EXT = (".zip", ".whl", ".jar", ".egg", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp",
            ".epub", ".apk", ".nupkg")
_TAR_EXT = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tbz", ".tar.xz", ".txz")
_UNSUPPORTED_EXT = (".7z", ".rar", ".zst", ".zstd", ".lz4", ".cab", ".arj", ".lzh", ".ace")


def _archive_kind(low: str) -> str | None:
    """Archive kind from the file name: zip / tar / gz / bz2 / xz / unsupported / None."""
    if low.endswith(_ZIP_EXT):
        return "zip"
    if low.endswith(_TAR_EXT):
        return "tar"
    if low.endswith(_UNSUPPORTED_EXT):
        return "unsupported"
    if low.endswith(".gz"):
        return "gz"
    if low.endswith(".bz2"):
        return "bz2"
    if low.endswith((".xz", ".lzma")):
        return "xz"
    return None


def _magic_kind(head: bytes) -> str | None:
    """Archive kind from the first bytes, whatever the file is called."""
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return "zip"
    if head.startswith(b"\x1f\x8b"):
        return "gz"
    if head.startswith(b"BZh") and head[4:10] == b"1AY&SY":
        return "bz2"
    if head.startswith(b"\xfd7zXZ\x00"):
        return "xz"
    if head[257:262] == b"ustar":
        return "tar"
    if head.startswith((b"7z\xbc\xaf\x27\x1c", b"Rar!\x1a\x07", b"\x28\xb5\x2f\xfd",
                        b"\x04\x22\x4d\x18", b"MSCF\x00\x00\x00\x00")):
        return "unsupported"  # 7z, rar, zstd, lz4, cab
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


def _text_codec(head: bytes) -> str | None:
    """The codec to decode a file with, or None for binary (a NUL, and not UTF-16)."""
    if head.startswith(b"\xff\xfe"):
        return "utf-16-le"
    if head.startswith(b"\xfe\xff"):
        return "utf-16-be"
    sample = head[:4096]
    if b"\x00" not in sample:
        return "utf-8"
    if len(sample) >= 4:
        even, odd = sample[0::2], sample[1::2]
        # UTF-16 without a BOM: ASCII-range text has a NUL in every other byte
        if odd.count(0) >= 0.9 * len(odd) and even.count(0) <= 0.1 * len(even):
            return "utf-16-le"
        if even.count(0) >= 0.9 * len(even) and odd.count(0) <= 0.1 * len(odd):
            return "utf-16-be"
    return None


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
                    help=f"scan window in bytes (default {DEFAULT_MAX_BYTES}); larger files are "
                         "scanned whole, in overlapping windows")
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
