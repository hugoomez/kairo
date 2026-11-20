"""A careful GitHub REST client: rate limits, conditional requests, paging, archive.

Every request goes through ``Client.request``, which

- paces each bucket (``core``, ``search``, ``code_search``) by its primary
  limit headers (``x-ratelimit-remaining`` 0 → sleep until
  ``x-ratelimit-reset``; the bucket is learnt from ``x-ratelimit-resource``)
  and by a minimum interval between requests (MIN_INTERVAL);
- on a 403/429 that is a rate limit, waits and retries: primary (remaining 0)
  until the reset; secondary (``retry-after``, or the "secondary rate limit"
  message) ``retry-after`` seconds, else at least one minute, then
  exponentially longer with jitter up to ``backoff_cap``, and gives up after
  ``max_retries`` (RateLimitError);
- sends ``If-None-Match`` with the ETag of an earlier 200 for the same URL and,
  on 304, reuses the archived body (an authorised 304 does not count against
  the primary limit);
- keeps every response in the RawArchive (never a request header);
- never sends the token anywhere but api.github.com over https, and drops it on
  a redirect to another host.

Sources (checked 2026-11-20):
- https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
  (primary 5,000/h authenticated, 60/h unauthenticated; x-ratelimit-* and
  x-ratelimit-resource headers; secondary limits: 100 concurrent, 900 points/min;
  on 403/429: retry-after, else reset if remaining is 0, else wait ≥ 1 min,
  increasing exponentially and giving up after a set number of attempts)
- https://docs.github.com/en/rest/search/search  (search: 30 req/min
  authenticated, 10 unauthenticated; code search requires authentication and
  allows 10 req/min; at most 1,000 results per search, 100 per page;
  ``incomplete_results`` on timeouts)
- https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api
  (serial requests; ETag / If-None-Match → 304 not counted when authorised;
  follow the Link header, never build page URLs)

All waiting goes through the injected ``clock`` / ``sleep`` (tests use a fake
clock); the transport is injectable too: ``transport(method, url, headers) ->
(status, headers_lowercase, body_bytes)``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import random
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlsplit

API = "https://api.github.com"
# The REST API version sent with every request and recorded in the manifest.
# 2022-11-28 is the long-supported version; the search docs' examples now also
# show 2026-03-10. Override with api_version= / --api-version /
# KAIRO_GITHUB_API_VERSION after checking the version's breaking changes.
API_VERSION = "2022-11-28"
USER_AGENT = "kairo-collect"
# Minimum seconds between two requests of a bucket (see the docs cited above):
# code search 10/min, other searches 30/min (10/min unauthenticated); core is
# paced by its headers only (5,000/h) and served serially.
MIN_INTERVAL = {"core": 0.0, "search": 2.0, "code_search": 6.0}
MIN_INTERVAL_ANON = {"core": 0.0, "search": 6.0, "code_search": 6.0}
HEARTBEAT_S = 15.0          # a long wait is slept in slices this long (progress stays fresh)
REPORT_WAIT_OVER_S = 10.0    # a pacing wait shorter than this is not shown as a rate-limit wait
_LINK_RE = re.compile(r'<([^>]+)>\s*;\s*rel="([^"]+)"')


class GitHubError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class RateLimitError(GitHubError):
    pass


class NotModifiedMissing(Exception):
    pass


@dataclass
class Response:
    status: int
    headers: dict
    body: bytes
    sha256: str
    url: str
    from_cache: bool = False

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300 or self.from_cache

    def json(self):
        return json.loads(self.body) if self.body else None

    def links(self) -> dict:
        return parse_link(self.headers.get("link", ""))


def parse_link(value: str) -> dict:
    return {rel: url for url, rel in _LINK_RE.findall(value or "")}


def bucket_for(url: str) -> str:
    path = urlsplit(url).path
    if path.startswith("/search/code"):
        return "code_search"
    if path.startswith("/search/"):
        return "search"
    return "core"


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urlsplit(newurl).netloc != urlsplit(req.full_url).netloc:
            new.remove_header("Authorization")
        return new


def urllib_transport(method: str, url: str, headers: dict, timeout: float = 60.0):
    opener = urllib.request.build_opener(_SameHostRedirect())
    req = urllib.request.Request(url, headers=headers, method=method)
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        body = e.read() if e.fp is not None else b""
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, body


@dataclass
class _Bucket:
    remaining: int | None = None
    reset: float | None = None
    last: float | None = None


@dataclass
class WaitLog:
    seconds: dict = field(default_factory=lambda: {"primary": 0.0, "secondary": 0.0,
                                                   "interval": 0.0, "transient": 0.0})
    events: int = 0


class Client:
    def __init__(self, token: str | None = None, transport=None, clock=time.time, sleep=time.sleep,
                 archive=None, etags=None, api_version: str = API_VERSION,
                 min_interval: dict | None = None, max_retries: int = 8,
                 backoff_cap: float = 900.0, rng: random.Random | None = None,
                 on_wait=None, base_url: str = API, user_agent: str = USER_AGENT):
        if token and urlsplit(base_url).scheme != "https":
            raise GitHubError("el token solo se envía por https")
        self._token = token
        self.transport = transport or urllib_transport
        self.clock, self.sleep = clock, sleep
        self.archive, self.etags = archive, etags
        self.api_version = api_version
        self.min_interval = {**(MIN_INTERVAL if token else MIN_INTERVAL_ANON), **(min_interval or {})}
        self.max_retries, self.backoff_cap = max_retries, backoff_cap
        self.rng = rng or random.Random()
        self.on_wait = on_wait
        self.base_url = base_url.rstrip("/")
        self._host = urlsplit(self.base_url).netloc
        self.user_agent = user_agent
        self._buckets: dict[str, _Bucket] = {}
        self._alias: dict[str, str] = {}
        self.waits = WaitLog()
        self.requests = 0
        self.api_version_selected: str | None = None

    @property
    def authenticated(self) -> bool:
        return bool(self._token)

    # -- helpers ---------------------------------------------------------------
    def _clean(self, text: str) -> str:
        return text.replace(self._token, "[token]") if self._token else text

    def url(self, path: str, params: dict | None = None) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            u = path
        else:
            u = self.base_url + "/" + path.lstrip("/")
        if params:
            u += ("&" if "?" in u else "?") + urlencode(sorted((k, str(v)) for k, v in params.items()))
        return u

    def _bucket(self, name: str) -> _Bucket:
        return self._buckets.setdefault(self._alias.get(name, name), _Bucket())

    def _wait_until(self, until: float, reason: str) -> None:
        now = self.clock()
        if until <= now:
            return
        shown = reason != "interval" or until - now > REPORT_WAIT_OVER_S
        self.waits.events += 1
        self.waits.seconds[reason] = self.waits.seconds.get(reason, 0.0) + (until - now)
        while (now := self.clock()) < until:
            if shown and self.on_wait:
                self.on_wait(until, reason)
            self.sleep(min(until - now, HEARTBEAT_S))
        if shown and self.on_wait:
            self.on_wait(None, None)

    def _pace(self, bucket: str) -> None:
        st = self._bucket(bucket)
        now = self.clock()
        until = 0.0
        if st.remaining is not None and st.remaining <= 0 and st.reset and st.reset > now:
            self._wait_until(st.reset + 1, "primary")
        if st.last is not None:
            until = st.last + self.min_interval.get(bucket, 0.0)
        self._wait_until(until, "interval")

    def _learn(self, bucket: str, h: dict) -> None:
        res = h.get("x-ratelimit-resource")
        if res and res != bucket:
            self._alias[bucket] = res
            old = self._buckets.pop(bucket, None)
            if old and res not in self._buckets:
                self._buckets[res] = old
        st = self._bucket(bucket)
        try:
            if "x-ratelimit-remaining" in h:
                st.remaining = int(h["x-ratelimit-remaining"])
            if "x-ratelimit-reset" in h:
                st.reset = float(h["x-ratelimit-reset"])
        except ValueError:
            pass
        if h.get("x-github-api-version-selected"):
            self.api_version_selected = h["x-github-api-version-selected"]

    @staticmethod
    def _message(body: bytes) -> str:
        try:
            doc = json.loads(body)
            return str(doc.get("message", "")) if isinstance(doc, dict) else ""
        except ValueError:
            return body[:200].decode("utf-8", "replace")

    def _backoff(self, n: int, base: float) -> float:
        wait = base if n <= 1 else max(base, min(self.backoff_cap, base * 2 ** (n - 1)))
        return wait + self.rng.uniform(0, 0.1 * wait)          # jitter: never shorter than asked

    # -- the request -----------------------------------------------------------
    def request(self, method: str, path: str, params: dict | None = None, tag: str = "",
                conditional: bool = True) -> Response:
        url = self.url(path, params)
        host = urlsplit(url).netloc
        bucket = bucket_for(url)
        secondary = transient = attempts = 0
        while True:
            attempts += 1
            if attempts > self.max_retries + 1:
                raise RateLimitError(f"límite de GitHub persistente tras {self.max_retries} reintentos: "
                                     f"{self._clean(url)}", 429)
            self._pace(bucket)
            headers = {"Accept": "application/vnd.github+json", "User-Agent": self.user_agent,
                       "X-GitHub-Api-Version": self.api_version}
            if self._token and host == self._host:
                headers["Authorization"] = f"Bearer {self._token}"
            cached = self.etags.get(url) if (self.etags and conditional and method == "GET") else None
            if cached and self.archive is not None and not self.archive.has(cached[1]):
                cached = None
            if cached:
                headers["If-None-Match"] = cached[0]
            self._bucket(bucket).last = self.clock()
            self.requests += 1
            try:
                status, rh, body = self.transport(method, url, headers)
            except (OSError, TimeoutError) as e:          # URLError is an OSError
                transient += 1
                if transient > 3:
                    raise GitHubError(f"red: {self._clean(type(e).__name__ + ': ' + str(e))[:200]}") from None
                self._wait_until(self.clock() + self._backoff(transient, 5.0), "transient")
                continue
            rh = {k.lower(): v for k, v in rh.items()}
            self._learn(bucket, rh)
            if status == 304 and cached:
                sha = cached[1]
                if self.archive is not None:
                    self.archive.save(method, url, 304, rh, b"", tag=tag, reused_sha=sha)
                    body = self.archive.read(sha)
                else:
                    raise NotModifiedMissing(url)
                return Response(304, rh, body, sha, url, from_cache=True)
            if self.archive is not None:
                sha = self.archive.save(method, url, status, rh, body, tag=tag)
            else:
                sha = hashlib.sha256(body).hexdigest()
            if 200 <= status < 300:
                if self.etags is not None and method == "GET" and rh.get("etag"):
                    self.etags.put(url, rh["etag"], sha)
                return Response(status, rh, body, sha, url)
            msg = self._message(body)
            remaining = rh.get("x-ratelimit-remaining")
            if status in (403, 429) and (status == 429 or "retry-after" in rh or remaining == "0"
                                         or "rate limit" in msg.lower()):
                if "retry-after" not in rh and remaining == "0" and rh.get("x-ratelimit-reset"):
                    self._wait_until(float(rh["x-ratelimit-reset"]) + 1, "primary")
                    continue
                secondary += 1
                try:
                    base = max(float(rh.get("retry-after", "60")), 1.0)
                except ValueError:
                    base = 60.0
                self._wait_until(self.clock() + self._backoff(secondary, base), "secondary")
                continue
            if status >= 500:
                transient += 1
                if transient > 3:
                    raise GitHubError(f"GitHub {status}: {self._clean(msg)[:200]}", status)
                self._wait_until(self.clock() + self._backoff(transient, 5.0), "transient")
                continue
            raise GitHubError(f"GitHub {status}: {self._clean(msg)[:200]} ({self._clean(url)})", status)

    def get(self, path: str, params: dict | None = None, tag: str = "") -> Response:
        return self.request("GET", path, params, tag=tag)

    def paginate(self, path: str, params: dict | None = None, tag: str = "page", max_pages: int | None = None):
        """Yields each page's Response, following the Link header's `next`."""
        url: str | None = self.url(path, params)
        n = 0
        while url and (max_pages is None or n < max_pages):
            resp = self.get(url, tag=tag)
            n += 1
            yield resp
            url = resp.links().get("next")

    # -- helpers for a collection script ----------------------------------------
    def repo_metadata(self, full_name: str) -> dict | None:
        """The repository facts a sample is usually filtered on, or None (404)."""
        try:
            d = self.get(f"/repos/{full_name}", tag="repo").json()
        except GitHubError as e:
            if e.status in (404, 451):
                return None
            raise
        return repo_facts(d)

    def blob(self, full_name: str, sha: str) -> bytes | None:
        """A file's bytes by its blob sha (GET /repos/{repo}/git/blobs/{sha}), or None (404)."""
        try:
            d = self.get(f"/repos/{full_name}/git/blobs/{sha}", tag="blob").json()
        except GitHubError as e:
            if e.status in (404, 409, 451):
                return None
            raise
        if d.get("encoding") == "base64":
            return base64.b64decode(d.get("content") or "")
        return (d.get("content") or "").encode("utf-8")

    def readme_present(self, full_name: str) -> bool | None:
        """Whether the repository has a README (GET /repos/{repo}/readme: 200 yes, 404 no);
        None when the repository itself is gone (451, 403 blocked)."""
        try:
            self.get(f"/repos/{full_name}/readme", tag="readme")
            return True
        except GitHubError as e:
            if e.status == 404:
                return False
            if e.status in (403, 451):
                return None
            raise


def repo_facts(d: dict) -> dict:
    owner = d.get("owner") or {}
    lic = d.get("license") or {}
    return {"full_name": d.get("full_name"), "id": d.get("id"), "name": d.get("name"),
            "description": d.get("description"), "topics": list(d.get("topics") or []),
            "clone_url": d.get("clone_url"), "fork": d.get("fork"),
            "archived": d.get("archived"), "is_template": d.get("is_template"),
            "disabled": d.get("disabled"), "owner_type": owner.get("type"),
            "stargazers_count": d.get("stargazers_count"), "forks_count": d.get("forks_count"),
            "created_at": d.get("created_at"), "pushed_at": d.get("pushed_at"),
            "default_branch": d.get("default_branch"), "language": d.get("language"),
            "size_kb": d.get("size"), "license": lic.get("spdx_id")}
