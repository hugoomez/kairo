#!/usr/bin/env python3
"""Shared HTTP helper for the `scripts/citations/` tools (named `net.py`, not
`http.py`: a file called http.py next to the scripts would shadow the stdlib
`http` package that urllib itself imports).

One GET function with Kairo's retry/backoff convention and a per-host
throttle, so every citation script talks to public APIs the same way:

  - 406 / 429 / 5xx are retried with backoff, **3 attempts total**, then the
    error is raised and the caller degrades (records the source as LOST).
    A `Retry-After` header (seconds, capped at 60) overrides the backoff wait.
    406 is included because export.arxiv.org intermittently answers urllib
    requests with 406 Not Acceptable (observed 2026-09-24, often for minutes at
    a time, while `curl` from the same machine gets 200 -- apparently client
    fingerprinting). After the final 406 from an arXiv host, one `curl` call is
    made if curl is on PATH (`CURL_FALLBACK_HOSTS`). Only keyless hosts are in
    that list, so no secret ever appears on a curl command line.
  - Other 4xx (404 not found, 400 bad request) raise immediately -- they are
    answers, not transient failures.
  - Per-host minimum spacing between requests: arXiv 3 s (arXiv's own rule,
    serial), Semantic Scholar 1 s (~1 rps keyless pool), Crossref 0.2 s
    (polite pool), OpenAlex 0.1 s (10 rps). The spacing holds across
    processes: each host's last request time is shared through
    `~/.kairo/throttle/` (`KAIRO_THROTTLE_DIR`), under a lock file; if that
    place cannot be used, the spacing falls back to this process alone, and if
    the lock stays busy past its deadline the request waits the full spacing.

Secrets: `redact()` strips `api_key=` values from any URL before it is put in
an error message or log line. Never print a raw request URL that may carry a
key -- print `redact(url)`.

This module is imported, not run. Standard library only.
"""

from __future__ import annotations

import contextlib
import gzip
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

__version__ = "1.2.0"   # 1.2.0: a busy shared lock waits the full spacing; 1.1.0: shared across processes

PROJECT_URL = "https://github.com/hugoomez/kairo"


def user_agent(product: str) -> str:
    """An agent the APIs can identify and contact (arXiv, Crossref and OpenAlex ask
    for one): the tool, the project's URL and `KAIRO_MAILTO` when set."""
    mailto = (os.environ.get("KAIRO_MAILTO") or "").strip()
    return f"{product} (+{PROJECT_URL}; research use" + (f"; mailto:{mailto}" if mailto else "") + ")"


USER_AGENT = user_agent("kairo-citations/1.1")

# minimum seconds between two requests to the same host
HOST_SPACING = {
    "export.arxiv.org": 3.0,
    "arxiv.org": 3.0,
    "api.semanticscholar.org": 1.0,
    "api.crossref.org": 0.2,
    "api.openalex.org": 0.1,         # OpenAlex asks for at most 10 requests per second
    "dblp.org": 1.0,
    "api2.openreview.net": 1.0,      # no published limit: one request per second
}
BACKOFF = (5.0, 15.0)            # waits before attempt 2 and 3
RETRYABLE = (406, 429)            # plus every 5xx
MAX_RETRY_AFTER = 60.0
DEFAULT_MAX_BYTES = 20 * 1024 * 1024
CURL_FALLBACK_HOSTS = {"export.arxiv.org", "arxiv.org"}

# indirection so tests can replace sleeping and the opener
sleep = time.sleep
monotonic = time.monotonic
wallclock = time.time
_poll = time.sleep                 # waiting for another process's lock (not a throttle wait)
_last_call: dict[str, float] = {}

# The spacing holds across processes too (two scripts querying arXiv at once):
# each host's last request time is kept in a small file, under a lock file.
# KAIRO_THROTTLE_DIR overrides the place; when it cannot be used, the spacing
# falls back to this process alone.
SHARED_DIR: Path | None = Path(os.environ.get("KAIRO_THROTTLE_DIR") or Path.home() / ".kairo" / "throttle")
LOCK_STALE_S = 30.0


class HttpError(Exception):
    """A request that failed for good. `code` is the HTTP status, or None for a
    network-level failure (DNS, timeout, connection reset)."""

    def __init__(self, url: str, code: int | None, reason: str):
        self.url = redact(url)
        self.code = code
        self.reason = reason
        super().__init__(f"{'HTTP ' + str(code) if code else 'network error'}: {reason} ({self.url})")

    @property
    def not_found(self) -> bool:
        return self.code == 404


_KEY_RE = re.compile(r"(?i)((?:api_key|apikey|key|token)=)[^&#\s]+")


def redact(text: str) -> str:
    """Replace secret query-parameter values with `***`."""
    return _KEY_RE.sub(r"\1***", text)


BUSY = "busy"                      # the shared lock stayed held past its deadline


@contextlib.contextmanager
def _host_lock(host: str):
    """Yields the host's shared state file; None when the shared place cannot be
    used at all; BUSY when another process kept the lock past the deadline."""
    try:
        SHARED_DIR.mkdir(parents=True, exist_ok=True)
    except (OSError, AttributeError):
        yield None
        return
    lock = SHARED_DIR / f"{host}.lock"
    deadline = time.monotonic() + 60
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > LOCK_STALE_S:
                    lock.unlink()                   # left by a crashed process
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                yield BUSY                          # never block a run on a stuck lock …
                return
            _poll(0.05)
        except OSError:
            yield None
            return
    try:
        yield SHARED_DIR / f"{host}.last"
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def _throttle(url: str) -> None:
    host = urllib.parse.urlsplit(url).netloc.lower()
    spacing = HOST_SPACING.get(host, 0.0)
    if not spacing:
        return
    with _host_lock(host) if SHARED_DIR is not None else contextlib.nullcontext() as state:
        wait = 0.0
        last = _last_call.get(host)
        if last is not None:
            wait = spacing - (monotonic() - last)
        if state == BUSY:
            # … but never skip the spacing: another process may have called just now
            wait, state = spacing, None
        if state is not None:
            try:
                other = float(state.read_text(encoding="utf-8").strip())
                wait = max(wait, spacing - (wallclock() - other))
            except (OSError, ValueError):
                pass
        if wait > 0:
            sleep(wait)
        _last_call[host] = monotonic()
        if state is not None:
            try:
                state.write_text(repr(wallclock()), encoding="utf-8")
            except OSError:
                pass


def _retry_after(e: urllib.error.HTTPError) -> float | None:
    try:
        v = e.headers.get("Retry-After") if e.headers else None
        return min(float(v), MAX_RETRY_AFTER) if v else None
    except (TypeError, ValueError):
        return None


def _curl(url: str, headers: dict[str, str], timeout: float, max_bytes: int) -> bytes | None:
    """One curl attempt for hosts in CURL_FALLBACK_HOSTS; None if not applicable/failed."""
    host = urllib.parse.urlsplit(url).netloc.lower()
    exe = shutil.which("curl")
    if host not in CURL_FALLBACK_HOSTS or not exe or redact(url) != url:
        return None
    _throttle(url)
    cmd = [exe, "-sS", "-f", "-L", "--compressed", "-m", str(int(timeout)), "--max-filesize", str(max_bytes)]
    for k, v in headers.items():
        if k.lower() != "accept-encoding":
            cmd += ["-H", f"{k}: {v}"]
    try:
        p = subprocess.run(cmd + [url], capture_output=True, timeout=timeout + 5)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout if p.returncode == 0 and p.stdout else None


def get(url: str, headers: dict[str, str] | None = None, timeout: float = 30.0,
        retry: bool = True, max_bytes: int = DEFAULT_MAX_BYTES) -> bytes:
    """GET `url` and return the body. Raises HttpError on a final failure."""
    h = {"User-Agent": user_agent("kairo-citations/1.1"), "Accept": "*/*", "Accept-Encoding": "gzip"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    waits = list(BACKOFF) + [None] if retry else [None]
    for wait in waits:
        _throttle(url)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise HttpError(url, None, f"response larger than {max_bytes} bytes")
                if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as e:
            transient = e.code in RETRYABLE or e.code >= 500
            if wait is None or not transient:
                if e.code == 406:
                    body = _curl(url, h, timeout, max_bytes)
                    if body is not None:
                        return body
                raise HttpError(url, e.code, str(e.reason)) from None
            sleep(_retry_after(e) or wait)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            if wait is None:
                raise HttpError(url, None, str(getattr(e, "reason", e))) from None
            sleep(wait)
    raise AssertionError("unreachable")
