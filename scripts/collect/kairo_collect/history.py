"""History of chosen paths of a repository: a blobless clone, every version, resumable.

``Collection.fetch_history(repo, paths, out_dir=..., max_bytes_budget=...)`` (see
collection.py) uses this module:

1. a partial, bare clone (``git clone --bare --filter=blob:none``: commits and trees,
   no file contents, no checkout, so no filter, hook or LFS smudge of the cloned
   repository ever runs) into ``<out_dir>/<owner>__<repo>/repo.git``. The URL is the
   ``clone_url`` of the API metadata, https only (``file`` is accepted for local test
   repositories); ``GIT_ALLOW_PROTOCOL`` limits git to that scheme;
2. ``git log --all -m --no-renames --raw -- <paths>`` (literal pathspecs): every
   commit of every branch and tag that touched one of the paths, with the blob sha of
   each version (a deletion is recorded with an empty blob sha). Refs that a clone
   does not fetch (``refs/pull/*``) are not seen;
3. ``git cat-file blob <sha>`` for every version not yet extracted (each one fetches
   that blob lazily from the remote), classified in memory by ``looks_encrypted``.
   Only blobs that look encrypted are written to ``blobs/<sha>``; a version that does
   NOT look encrypted (a plaintext twin: possibly private material) is recorded by its
   metadata, its encryption markers and a fingerprint of its key NAMES, never by its
   bytes, unless the caller passes ``keep_plaintext=True`` (an explicit decision, not
   the default). ``on_blob(path, commit, data)`` sees every version in memory first
   (use kairo_collect.sensitive to keep only fingerprints);
4. when the repository is finished the clone is deleted (``keep_clone=False``):
   git's object store holds every fetched blob, plaintext ones included, until then.
   An interrupted or budget-stopped run keeps it, so a rerun continues.

Everything is in state.sqlite (tables ``history_repos`` and ``history``): a rerun
skips what is done, so the call is resumable and idempotent. ``max_bytes_budget``
caps the bytes under ``out_dir`` (every repository's folder): the run checks it
before the clone and before each blob, and stops with status ``budget`` (and a
message in progress.json) when the next step would start over it. A lazily fetched
blob's size is only known once fetched, so the store can end at most one blob over
the budget; the result says how much.

The token: never in argv, never in a git config file. Each git command gets an
``http.<https://host/>.extraheader`` through ``GIT_CONFIG_COUNT`` /
``GIT_CONFIG_KEY_n`` / ``GIT_CONFIG_VALUE_n`` environment variables (git >= 2.31),
scoped to the API's own host (github.com), so a redirect elsewhere does not carry
it; credential helpers are disabled for the command and prompts are off. Limit: a
process's environment is readable by the same OS user (and by root) while git runs;
that is the least exposed channel git offers (argv is visible to every user; a
config file is on disk).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

GIT_TIMEOUT_S = 1800
_FULL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]{0,38}/[A-Za-z0-9._\-]{1,100}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_ZERO = re.compile(r"^0+$")
_TRACE_VARS = ("GIT_TRACE", "GIT_TRACE_CURL", "GIT_TRACE_PACKET", "GIT_TRACE_SETUP", "GIT_CURL_VERBOSE",
               "GIT_TRACE2", "GIT_TRACE2_EVENT", "GIT_TRACE2_PERF", "GIT_TRACE_PACKFILE",
               "GIT_ASKPASS", "SSH_ASKPASS")


class HistoryError(Exception):
    pass


# ---- encryption markers (bytes in memory → booleans) -------------------------------

_MARKERS = {
    "age": re.compile(rb"\Aage-encryption\.org/v1\r?\n|-----BEGIN AGE ENCRYPTED FILE-----"),
    "pgp": re.compile(rb"-----BEGIN PGP MESSAGE-----"),
    "sops": re.compile(rb"(?m)ENC\[[A-Z0-9_]+,data:|^sops_(?:mac|version)=|^\[sops\]\s*$|"
                       rb"^sops:\s*$|\"sops\"\s*:\s*\{"),
    "dotenvx": re.compile(rb"(?m)^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_.\-]*\s*=\s*[\"']?encrypted:"),
    "sealed_secret": re.compile(rb"(?m)^\s*[\"']?kind[\"']?\s*:\s*[\"']?SealedSecret\b"),
    "ansible_vault": re.compile(rb"\A\$ANSIBLE_VAULT;"),
    "git_crypt": re.compile(rb"\A\x00GITCRYPT\x00"),
}


def looks_encrypted(data: bytes) -> dict:
    """Which encryption formats `data` shows: {"encrypted": bool, <format>: bool, ...}.
    Booleans only. A file is `encrypted` when any marker is present (a SOPS file whose
    `ENC[...]` values sit next to a few plaintext keys still counts as encrypted)."""
    found = {k: bool(rx.search(data)) for k, rx in _MARKERS.items()}
    return {"encrypted": any(found.values()), **found}


_KEY_LINE_RE = re.compile(r"^([ \t]*)(?:-[ \t]+)?(?:export[ \t]+)?[\"']?([A-Za-z_][A-Za-z0-9_.\-]{0,127})[\"']?"
                          r"[ \t]*(?::(?=[ \t]|$)|=)")


def key_names(data: bytes) -> list[str]:
    """The sorted key NAMES of a YAML / JSON / dotenv / INI file, without SOPS metadata
    (the `sops` subtree, `sops_*` lines, a `[sops]` section). Never a value."""
    text = data.decode("utf-8", "replace")
    names: set[str] = set()
    try:
        doc = json.loads(text)
    except ValueError:
        doc = None
    if isinstance(doc, (dict, list)):
        def walk(v):
            if isinstance(v, dict):
                for k, x in v.items():
                    if k == "sops":
                        continue
                    names.add(str(k)[:128])
                    walk(x)
            elif isinstance(v, list):
                for x in v:
                    walk(x)
        walk(doc)
        return sorted(names)
    skip_indent: int | None = None
    in_sops_section = False
    for ln in text.splitlines():
        if not ln.strip() or ln.lstrip().startswith(("#", ";")):
            continue
        sec = re.match(r"^\s*\[([^\]]*)\]\s*$", ln)
        if sec:
            in_sops_section = sec.group(1).strip() == "sops"
            continue
        if in_sops_section:
            continue
        ind = len(ln) - len(ln.lstrip(" \t"))
        if skip_indent is not None:
            if ind > skip_indent:
                continue
            skip_indent = None
        m = _KEY_LINE_RE.match(ln)
        if not m:
            continue
        name = m.group(2)
        if name == "sops":
            skip_indent = ind
            continue
        if name.startswith("sops_"):
            continue
        names.add(name)
    return sorted(names)


def keys_fingerprint(data: bytes) -> str | None:
    names = key_names(data)
    if not names:
        return None
    return hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest()[:16]


def plaintext_twins(versions) -> dict:
    """Flag the versions of ONE path that look unencrypted while a later version is encrypted.

    `versions`: iterable of {"commit", "author_date", "blob_sha", "encrypted": bool,
    "keys_fp": str|None} (what the history table holds), or with "data": bytes instead
    of "encrypted"/"keys_fp" (classified here, in memory). Returns booleans and
    fingerprints only::

        {"versions": n, "encrypted_versions": n, "plaintext_twins": n,
         "items": [{"commit", "author_date", "blob_sha", "encrypted", "plaintext_twin",
                    "same_keys_as_encrypted"}]}

    `plaintext_twin`: this version does not look encrypted and a LATER version (by
    author date) of the same path does. `same_keys_as_encrypted`: its key-name
    fingerprint equals an encrypted version's (SOPS and dotenvx keep key names in clear,
    so equal names suggest the cleartext of the same secrets)."""
    rows = []
    for v in versions:
        if not v.get("blob_sha"):
            continue                    # a deletion
        if "data" in v:
            enc = looks_encrypted(v["data"])["encrypted"]
            kfp = keys_fingerprint(v["data"])
        else:
            enc, kfp = bool(v.get("encrypted")), v.get("keys_fp")
        rows.append({"commit": v.get("commit"), "author_date": v.get("author_date") or "",
                     "blob_sha": v.get("blob_sha"), "encrypted": enc, "keys_fp": kfp})
    rows.sort(key=lambda r: (r["author_date"], r["commit"] or ""))
    enc_fps = {r["keys_fp"] for r in rows if r["encrypted"] and r["keys_fp"]}
    items = []
    for i, r in enumerate(rows):
        later_enc = any(x["encrypted"] for x in rows[i + 1:] if x["author_date"] > r["author_date"])
        items.append({"commit": r["commit"], "author_date": r["author_date"], "blob_sha": r["blob_sha"],
                      "encrypted": r["encrypted"], "plaintext_twin": (not r["encrypted"]) and later_enc,
                      "same_keys_as_encrypted": (not r["encrypted"]) and r["keys_fp"] in enc_fps})
    return {"versions": len(rows), "encrypted_versions": sum(r["encrypted"] for r in rows),
            "plaintext_twins": sum(i["plaintext_twin"] for i in items), "items": items}


# ---- git plumbing ------------------------------------------------------------------

def repo_dirname(full_name: str) -> str:
    if not _FULL_NAME_RE.match(full_name or ""):
        raise HistoryError(f"nombre de repositorio no válido: {full_name!r} (owner/repo)")
    owner, name = full_name.split("/")
    return f"{owner}__{name}"


def check_paths(paths) -> list[str]:
    if isinstance(paths, str):
        paths = [paths]
    out = []
    for p in paths or []:
        p = str(p).replace("\\", "/")
        parts = PurePosixPath(p).parts
        if not p or p.startswith("/") or ".." in parts or re.match(r"^[A-Za-z]:", p) or "\x00" in p:
            raise HistoryError(f"ruta no válida dentro del repositorio: {p!r}")
        out.append(p)
    if not out:
        raise HistoryError("hace falta al menos una ruta")
    return sorted(set(out))


def check_clone_url(url: str | None) -> str:
    if not url or not isinstance(url, str):
        raise HistoryError("los metadatos del repositorio no traen clone_url")
    parts = urlsplit(url)
    if parts.scheme not in ("https", "file") or "@" in parts.netloc:
        raise HistoryError("clone_url debe ser https (sin credenciales en la URL)")
    return url


def auth_env(token: str | None, url: str, token_hosts) -> dict:
    """Environment variables that give ONE git command the token as an http header,
    scoped to the clone URL's host; {} when the host is not one the token may go to."""
    parts = urlsplit(url)
    if not token or parts.scheme != "https" or parts.netloc.lower() not in {h.lower() for h in token_hosts}:
        return {}
    basic = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
    return {f"http.https://{parts.netloc}/.extraheader": f"AUTHORIZATION: basic {basic}"}


def git_env(config: dict, scheme: str, base_env=None) -> dict:
    """The environment of a git command: the caller's, minus tracing, plus `config`
    as GIT_CONFIG_KEY_n / VALUE_n entries appended after any the caller already set."""
    env = dict(os.environ if base_env is None else base_env)
    for k in _TRACE_VARS:
        env.pop(k, None)
    try:
        n = int(env.get("GIT_CONFIG_COUNT") or 0)
    except ValueError:
        n = 0
    cfg = {"credential.helper": "", "core.askPass": "", **config}
    for k, v in cfg.items():
        env[f"GIT_CONFIG_KEY_{n}"] = k
        env[f"GIT_CONFIG_VALUE_{n}"] = v
        n += 1
    env["GIT_CONFIG_COUNT"] = str(n)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    env["GIT_LITERAL_PATHSPECS"] = "1"
    env["GIT_ALLOW_PROTOCOL"] = scheme
    return env


def parse_log(out: bytes, wanted: set[str]) -> list[dict]:
    """`git log --raw -z --format=format:%x01%H %aI` → [{commit, author_date, path,
    blob_sha ('' when deleted), change, mode}] for the wanted paths."""
    rows = []
    for chunk in out.split(b"\x01"):
        if not chunk.strip(b"\x00\n"):
            continue
        head, _, rest = chunk.partition(b"\n")
        commit, _, date = head.decode("ascii", "replace").strip().partition(" ")
        if not _SHA_RE.match(commit):
            continue
        fields = [f for f in rest.split(b"\x00") if f.strip(b"\n")]
        i = 0
        while i + 1 < len(fields):
            meta, path = fields[i].decode("ascii", "replace").strip(), fields[i + 1].decode("utf-8", "replace")
            i += 2
            m = meta.lstrip(":").split()
            if len(m) < 5:
                continue
            _, new_mode, _, new_sha, change = m[:5]
            if path not in wanted or new_mode == "160000":
                continue                # another path, or a submodule
            blob = "" if _ZERO.match(new_sha) else new_sha
            rows.append({"commit": commit, "author_date": date, "path": path, "blob_sha": blob,
                         "change": change[:1], "mode": new_mode})
    return rows


def dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for d, _, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(d, f)).st_size
            except OSError:
                pass
    return total


def remove_tree(path: Path) -> None:
    """rmtree that also clears git's read-only pack files (Windows)."""
    def onerr(func, p, _exc):
        try:
            os.chmod(p, 0o700)
            func(p)
        except OSError:
            pass
    if path.exists():
        shutil.rmtree(path, onerror=onerr)


class GitRunner:
    """Runs git with an environment built per command; `run` is replaceable in tests."""

    def __init__(self, token: str | None, token_hosts=("github.com",), run=None):
        self._token = token
        self.token_hosts = tuple(token_hosts)
        self.run = run or subprocess.run

    def _clean(self, text: str) -> str:
        if self._token:
            text = text.replace(self._token, "[token]")
            text = text.replace(base64.b64encode(f"x-access-token:{self._token}".encode()).decode(), "[token]")
        return text

    def __call__(self, args: list[str], url: str, *, cwd=None, timeout=GIT_TIMEOUT_S) -> bytes:
        scheme = urlsplit(url).scheme
        env = git_env(auth_env(self._token, url, self.token_hosts), scheme)
        try:
            r = self.run(["git", *args], cwd=cwd, env=env, capture_output=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as e:
            raise HistoryError(f"git no se pudo ejecutar: {type(e).__name__}") from None
        if r.returncode != 0:
            err = self._clean((r.stderr or b"").decode("utf-8", "replace")).strip().splitlines()
            raise HistoryError(f"git {args[0]} falló ({r.returncode}): {' / '.join(err[-3:])[:300]}")
        return r.stdout
