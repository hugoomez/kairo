"""Raw archive: every response body kept, content-addressed, with an index.

    <out>/raw/<sha256>.json     the body exactly as received (or as redacted, if
                                the researcher turned raw redaction on)
    <out>/raw/index.jsonl       one line per response, in the order received:
        {"ts": "<UTC>", "method", "url", "params", "status", "headers": {selected},
         "sha256", "bytes", "tag", "reused": bool}

`url` never carries a credential (the client authenticates with a header, and
`access_token` / `client_secret` query parameters are blanked anyway); only the
response headers in KEEP_HEADERS are stored, so no request header — the
Authorization one included — ever reaches disk. A 304 keeps no new file: its
line points at the body it reused (`reused: true`).

Redaction: raw API JSON is stored as received by default (the researcher
decides, `redact_raw=True` to change it); anything written for humans goes
through `write_for_humans`, which redacts with scripts/security/redact.py.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

KEEP_HEADERS = ("etag", "last-modified", "link", "date", "content-type", "retry-after",
                "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-used",
                "x-ratelimit-reset", "x-ratelimit-resource", "x-github-request-id",
                "x-github-api-version-selected")
_SECRET_PARAMS = {"access_token", "client_secret", "token"}
_SECURITY = Path(__file__).resolve().parents[2] / "security"


def utc_iso(t: float | None = None) -> str:
    t = time.time() if t is None else t
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write_bytes(path: Path, data: bytes, tries: int = 5) -> None:
    """Write via a temp file in the same folder and os.replace (retried briefly:
    on Windows a reader holding the file open makes the replace fail)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    for i in range(tries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == tries - 1:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05 * (i + 1))


def atomic_write_json(path: Path, obj) -> None:
    atomic_write_bytes(path, (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def clean_url(url: str) -> tuple[str, dict]:
    """The URL with credential parameters blanked, and its query as a dict."""
    parts = urlsplit(url)
    pairs = [(k, "[oculto]" if k.lower() in _SECRET_PARAMS else v)
             for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    netloc = parts.netloc.rsplit("@", 1)[-1]          # never user:password@host
    return urlunsplit((parts.scheme, netloc, parts.path, urlencode(pairs), "")), dict(pairs)


def _redact_module():
    if str(_SECURITY) not in sys.path:
        sys.path.insert(0, str(_SECURITY))
    import redact  # scripts/security/redact.py
    return redact


def redact_obj(obj):
    """A JSON value with every string passed through redact.py."""
    return _redact_module().redact_json_value(obj)


def redact_bytes(body: bytes) -> bytes:
    rd = _redact_module()
    try:
        doc = json.loads(body)
    except ValueError:
        text = rd.decode_text(body)
        return body if text is None else rd.redact_text(text)[0].encode("utf-8")
    return json.dumps(rd.redact_json_value(doc), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def write_for_humans(path: Path, obj, redact: bool = True) -> Path:
    """A sample for people to read (JSON), redacted unless told otherwise."""
    atomic_write_json(Path(path), redact_obj(obj) if redact else obj)
    return Path(path)


class SecretInBody(ValueError):
    pass


class RawArchive:
    def __init__(self, out: Path, redact_raw: bool = False, forbid: list[str] | None = None, clock=time.time):
        self.dir = Path(out) / "raw"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index = self.dir / "index.jsonl"
        self.redact_raw = redact_raw
        self._forbid = [f.encode("utf-8") for f in (forbid or []) if f]
        self.clock = clock

    def path(self, sha: str) -> Path:
        return self.dir / f"{sha}.json"

    def has(self, sha: str) -> bool:
        return self.path(sha).is_file()

    def read(self, sha: str, verify: bool = True) -> bytes:
        data = self.path(sha).read_bytes()
        if verify and hashlib.sha256(data).hexdigest() != sha:
            raise ValueError(f"raw/{sha}.json no es la respuesta guardada (sha256 distinto)")
        return data

    def save(self, method: str, url: str, status: int, headers: dict, body: bytes,
             tag: str = "", reused_sha: str | None = None) -> str:
        """Keep a response; returns the sha256 of the stored bytes."""
        if reused_sha is None:
            if self.redact_raw and body:
                body = redact_bytes(body)
            if any(f in body for f in self._forbid):
                # Defence in depth: a body echoing the credential is never written.
                raise SecretInBody("la respuesta contiene el token; no se archiva")
            sha = hashlib.sha256(body).hexdigest()
            if not self.has(sha):
                atomic_write_bytes(self.path(sha), body)
            size = len(body)
        else:
            sha, size = reused_sha, self.path(reused_sha).stat().st_size
        url_clean, params = clean_url(url)
        line = {"ts": utc_iso(self.clock()), "method": method, "url": url_clean, "params": params,
                "status": status, "headers": {k: headers[k] for k in KEEP_HEADERS if k in headers},
                "sha256": sha, "bytes": size, "tag": tag, "reused": reused_sha is not None}
        with self.index.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
        return sha

    def entries(self) -> list[dict]:
        if not self.index.is_file():
            return []
        out = []
        for ln in self.index.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    continue            # a line cut by an interruption
        return out
