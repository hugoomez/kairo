"""Tests for kairo_collect and collect.py. Run: python -m pytest scripts/collect

No network: every request goes to FakeGitHub, an in-memory search API over
invented files (owner "acme-lab", invented paths and sizes), with a fake
clock so rate-limit waits take no real time. Everything is written under
pytest's tmp_path."""
from __future__ import annotations

import base64
import hashlib
import json
import random
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import collect as cli  # noqa: E402
from kairo_collect import shard  # noqa: E402
from kairo_collect.archive import RawArchive  # noqa: E402
from kairo_collect.collection import Collection, git_info, load_queries, replay, status  # noqa: E402
from kairo_collect.github import Client, GitHubError, RateLimitError  # noqa: E402
from kairo_collect.state import State  # noqa: E402
from kairo_collect.token import TokenRefused, load_token, location_problem  # noqa: E402

T0 = 1_800_000_000.0
TERM = "invented_marker_term"


def fake_token() -> str:
    # built at run time: no token-shaped literal in the repository
    return "ghp_" + hashlib.sha256(b"kairo-collect invented test token").hexdigest()[:36]


class FakeClock:
    def __init__(self):
        self.t = T0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        assert s >= 0
        self.sleeps.append(s)
        self.t += s


def make_files(n: int, sizes=None, exts=("py", "c", "md")) -> list[dict]:
    files = []
    for i in range(n):
        ext = exts[i % len(exts)]
        sha = hashlib.sha1(f"blob-{i}".encode()).hexdigest()
        size = sizes(i) if sizes else (i * 37) % 300_000
        files.append({"repo": f"acme-lab/demo-{i % 40}", "path": f"src/mod{i}.{ext}", "sha": sha,
                      "ext": ext, "size": size, "content": f"invented content {i}\n"})
    return files


class FakeGitHub:
    """A tiny /search/code + /repos API. `script` holds responses served before
    the normal ones (one per request); `limits` simulates primary buckets."""

    def __init__(self, clock: FakeClock, files: list[dict], require_auth: bool = True):
        self.clock = clock
        self.files = files
        self.require_auth = require_auth
        self.script: list[tuple[int, dict, bytes]] = []
        self.requests: list[tuple[str, str, dict]] = []
        self.limits: dict[str, dict] = {}
        self.raise_after: int | None = None
        self.times: list[float] = []

    def set_limit(self, bucket: str, limit: int, window: float = 60.0) -> None:
        self.limits[bucket] = {"limit": limit, "remaining": limit, "reset": self.clock() + window, "window": window}

    def _bucket(self, path: str) -> str:
        return "code_search" if path.startswith("/search/code") else "search" if path.startswith("/search") else "core"

    def _rl_headers(self, bucket: str) -> dict:
        b = self.limits.get(bucket)
        if not b:
            return {"X-RateLimit-Resource": bucket}
        return {"X-RateLimit-Limit": str(b["limit"]), "X-RateLimit-Remaining": str(b["remaining"]),
                "X-RateLimit-Reset": str(int(b["reset"])), "X-RateLimit-Resource": bucket}

    def matches(self, q: str) -> list[dict]:
        out = self.files
        for tok in q.split():
            neg = tok.startswith("-")
            key, _, val = tok.lstrip("-").partition(":")
            if not val:
                continue
            if key == "size":
                lo, hi = (int(x) for x in val.split(".."))
                out = [f for f in out if lo <= f["size"] <= hi]
            elif key == "extension":
                out = [f for f in out if (f["ext"] == val) != neg]
        return sorted(out, key=lambda f: (f["repo"], f["path"]))

    def __call__(self, method: str, url: str, headers: dict):
        if self.raise_after is not None and len(self.requests) >= self.raise_after:
            raise KeyboardInterrupt
        self.requests.append((method, url, dict(headers)))
        self.times.append(self.clock())
        if self.script:
            st, h, body = self.script.pop(0)
            return st, h, body
        parts = urlsplit(url)
        params = dict(parse_qsl(parts.query))
        bucket = self._bucket(parts.path)
        b = self.limits.get(bucket)
        if b:
            if self.clock() >= b["reset"]:
                b["remaining"], b["reset"] = b["limit"], self.clock() + b["window"]
            if b["remaining"] <= 0:
                return 403, self._rl_headers(bucket), b'{"message":"API rate limit exceeded"}'
            b["remaining"] -= 1
        if self.require_auth and parts.path.startswith("/search/code") and "Authorization" not in headers:
            return 401, {}, b'{"message":"Requires authentication"}'
        if parts.path.startswith("/search/code"):
            status, h, body = self._search(params)
        elif "/git/blobs/" in parts.path:
            status, h, body = self._blob(parts.path)
        elif parts.path.startswith("/repos/"):
            status, h, body = self._repo(parts.path)
        else:
            status, h, body = 404, {}, b'{"message":"Not Found"}'
        h = {**h, **self._rl_headers(bucket), "X-GitHub-Api-Version-Selected": "2022-11-28"}
        if status == 200:
            etag = '"' + hashlib.sha256(body).hexdigest()[:20] + '"'
            h["ETag"] = etag
            if headers.get("If-None-Match") == etag:
                return 304, h, b""
        return status, h, body

    def _search(self, params: dict):
        q = params["q"]
        per = int(params.get("per_page", 30))
        page = int(params.get("page", 1))
        found = self.matches(q)
        reach = found[:1000]
        chunk = reach[(page - 1) * per: page * per]
        items = [{"name": f["path"].rsplit("/", 1)[-1], "path": f["path"], "sha": f["sha"],
                  "html_url": f"https://github.example/{f['repo']}/blob/main/{f['path']}",
                  "repository": {"full_name": f["repo"], "id": 1, "fork": False}} for f in chunk]
        body = json.dumps({"total_count": len(found), "incomplete_results": False, "items": items}).encode()
        h = {"Content-Type": "application/json"}
        if page * per < len(reach):
            nxt = "https://api.github.com/search/code?" + urlencode({"q": q, "per_page": per, "page": page + 1})
            h["Link"] = f'<{nxt}>; rel="next"'
        return 200, h, body

    def _repo(self, path: str):
        full = path[len("/repos/"):]
        if not any(f["repo"] == full for f in self.files):
            return 404, {}, b'{"message":"Not Found"}'
        d = {"full_name": full, "id": 7, "fork": False, "archived": True, "is_template": False,
             "owner": {"login": "acme-lab", "type": "Organization"}, "stargazers_count": 3,
             "created_at": "2020-01-01T00:00:00Z", "pushed_at": "2021-01-01T00:00:00Z",
             "default_branch": "main", "size": 12, "license": {"spdx_id": "MIT"}}
        return 200, {}, json.dumps(d).encode()

    def _blob(self, path: str):
        sha = path.rsplit("/", 1)[-1]
        f = next((f for f in self.files if f["sha"] == sha), None)
        if not f:
            return 404, {}, b'{"message":"Not Found"}'
        d = {"sha": sha, "encoding": "base64", "content": base64.b64encode(f["content"].encode()).decode()}
        return 200, {}, json.dumps(d).encode()

    def urls(self, path_prefix: str = "") -> list[str]:
        return [u for _, u, _ in self.requests if urlsplit(u).path.startswith(path_prefix)]


def collection(out: Path, fake: FakeGitHub, clock: FakeClock, **kw) -> Collection:
    kw.setdefault("token", fake_token())
    return Collection(out, transport=fake, clock=clock, sleep=clock.sleep, rng=random.Random(1),
                      caller_repo=out, **kw)


def write_queries(tmp: Path, qs) -> Path:
    p = tmp / "q.json"
    p.write_text(json.dumps(qs), encoding="utf-8")
    return p


Q1 = [{"name": "base", "q": TERM}]


# -- paging, items, archive ------------------------------------------------------

def test_paging_follows_link_and_collects_every_item(tmp_path):
    clock = FakeClock()
    fake = FakeGitHub(clock, make_files(250))
    with collection(tmp_path / "out", fake, clock) as col:
        res = col.search(Q1)
    assert res["items"] == 250 and res["complete"] and res["pages"] == 3
    page_urls = [u for u in fake.urls("/search/code") if "per_page=100" in u]
    assert len(page_urls) == 3 and "page=2" in page_urls[1] and "page=3" in page_urls[2]
    lines = (tmp_path / "out" / "items.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 250
    first = json.loads(lines[0])
    assert set(first) == {"repo", "path", "sha", "name", "html_url", "queries"} and first["queries"] == ["base"]
    index = RawArchive(tmp_path / "out").entries()
    assert [e["tag"] for e in index] == ["probe", "page", "page", "page"]
    for e in index:
        assert (tmp_path / "out" / "raw" / f"{e['sha256']}.json").is_file()
        assert "authorization" not in json.dumps(e).lower()


def test_code_search_requests_are_spaced_by_the_minimum_interval(tmp_path):
    clock = FakeClock()
    fake = FakeGitHub(clock, make_files(250))
    with collection(tmp_path / "out", fake, clock) as col:
        col.search(Q1)
    gaps = [b - a for a, b in zip(fake.times, fake.times[1:])]
    assert gaps and min(gaps) >= 6.0          # 10 req/min for code search


def test_etag_304_reuses_the_archived_body(tmp_path):
    clock = FakeClock()
    fake = FakeGitHub(clock, make_files(5))
    out = tmp_path / "out"
    st = State(out)
    arch = RawArchive(out, clock=clock)
    c = Client(fake_token(), transport=fake, clock=clock, sleep=clock.sleep, archive=arch, etags=st)
    a = c.get("/repos/acme-lab/demo-1", tag="repo")
    b = c.get("/repos/acme-lab/demo-1", tag="repo")
    assert a.status == 200 and b.status == 304 and b.from_cache and b.body == a.body
    assert "If-None-Match" not in fake.requests[0][2] and fake.requests[1][2]["If-None-Match"]
    entries = arch.entries()
    assert entries[1]["reused"] and entries[1]["status"] == 304 and entries[1]["sha256"] == a.sha256
    assert len(list((out / "raw").glob("*.json"))) == 1
    st.close()


# -- rate limits ---------------------------------------------------------------------

def test_primary_limit_headers_make_the_client_wait_for_the_reset(tmp_path):
    clock = FakeClock()
    fake = FakeGitHub(clock, make_files(5))
    fake.set_limit("core", 2, window=600)
    waits = []
    c = Client(fake_token(), transport=fake, clock=clock, sleep=clock.sleep,
               on_wait=lambda until, reason: waits.append((until, reason)))
    for i in range(3):
        c.get(f"/repos/acme-lab/demo-{i}")
    assert len(fake.requests) == 3                    # never served a 403: it waited first
    assert clock() >= T0 + 600
    assert any(r == "primary" for _, r in waits) and waits[-1] == (None, None)
    assert c.waits.seconds["primary"] >= 599


def test_primary_403_with_remaining_zero_waits_until_reset_then_retries(tmp_path):
    clock = FakeClock()
    fake = FakeGitHub(clock, make_files(5))
    reset = int(T0 + 120)
    fake.script = [(403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset)},
                    b'{"message":"API rate limit exceeded for user"}')]
    c = Client(fake_token(), transport=fake, clock=clock, sleep=clock.sleep)
    r = c.get("/repos/acme-lab/demo-1")
    assert r.status == 200 and len(fake.requests) == 2 and clock() >= reset + 1


def test_secondary_limit_honours_retry_after_then_backs_off_exponentially(tmp_path):
    clock = FakeClock()
    fake = FakeGitHub(clock, make_files(5))
    msg = b'{"message":"You have exceeded a secondary rate limit. Please wait a few minutes."}'
    fake.script = [(403, {"retry-after": "30"}, msg), (403, {}, msg), (429, {}, msg)]
    c = Client(fake_token(), transport=fake, clock=clock, sleep=clock.sleep, rng=random.Random(3))
    r = c.get("/repos/acme-lab/demo-1")
    assert r.status == 200 and len(fake.requests) == 4
    s = c.waits.seconds["secondary"]
    # first wait ≥ retry-after (30), then ≥ 60·2 and ≥ 60·4 (no retry-after: at least one minute, doubling)
    assert s >= 30 + 120 + 240 and s <= (30 + 120 + 240) * 1.1 + 1


def test_secondary_limit_gives_up_after_max_retries(tmp_path):
    clock = FakeClock()
    fake = FakeGitHub(clock, make_files(5))
    msg = b'{"message":"You have exceeded a secondary rate limit."}'
    fake.script = [(403, {"retry-after": "5"}, msg)] * 10
    c = Client(fake_token(), transport=fake, clock=clock, sleep=clock.sleep, max_retries=3, backoff_cap=50)
    with pytest.raises(RateLimitError):
        c.get("/repos/acme-lab/demo-1")
    assert len(fake.requests) == 4
    assert max(clock.sleeps) <= 15.0                      # long waits are sliced (heartbeat)


def test_server_errors_are_retried_and_plain_403_is_not(tmp_path):
    clock = FakeClock()
    fake = FakeGitHub(clock, make_files(5))
    fake.script = [(502, {}, b"bad gateway")]
    c = Client(fake_token(), transport=fake, clock=clock, sleep=clock.sleep)
    assert c.get("/repos/acme-lab/demo-1").status == 200
    fake.script = [(403, {}, b'{"message":"Resource not accessible"}')]
    with pytest.raises(GitHubError) as e:
        c.get("/repos/acme-lab/demo-2")
    assert e.value.status == 403 and fake_token() not in str(e.value)


def test_progress_shows_the_rate_limit_wait(tmp_path):
    clock = FakeClock()
    fake = FakeGitHub(clock, make_files(250))
    msg = b'{"message":"You have exceeded a secondary rate limit."}'
    seen = []
    with collection(tmp_path / "out", fake, clock) as col:
        orig = col.progress.waiting

        def spy(until, reason=None):
            orig(until, reason)
            seen.append(json.loads((tmp_path / "out" / "progress.json").read_text(encoding="utf-8")))
        col.client.on_wait = spy
        fake.script = [(403, {"retry-after": "90"}, msg)]
        col.search(Q1)
    waiting = [p for p in seen if p["waiting_rate_limit_until"]]
    assert waiting and waiting[0]["waiting_rate_limit_until"].endswith("Z")
    final = json.loads((tmp_path / "out" / "progress.json").read_text(encoding="utf-8"))
    assert set(final) >= {"phase", "done", "total", "unit", "waiting_rate_limit_until", "eta_s",
                          "updated_at", "collection_id"}
    assert final["phase"] == "done" and final["waiting_rate_limit_until"] is None


# -- sharding ------------------------------------------------------------------------

def test_plan_is_a_partition_under_the_cap_and_deterministic():
    files = make_files(3500)

    def count(q):
        return len(FakeGitHub(FakeClock(), files).matches(q))
    p1 = shard.plan_query(TERM, count)
    p2 = shard.plan_query(TERM, count)
    assert p1 == p2 and p1["total_count"] == 3500 and not p1["truncated"]
    assert all(s["total_count"] <= 1000 for s in p1["shards"])
    assert p1["sum_shards"] == 3500 and p1["reachable"] == 3500
    seen = [f["sha"] for s in p1["shards"] for f in FakeGitHub(FakeClock(), files).matches(s["q"])]
    assert sorted(seen) == sorted(f["sha"] for f in files)         # no gap, no overlap


def test_one_size_over_the_cap_is_split_by_extension_or_marked_truncated():
    files = make_files(1500, sizes=lambda i: 4096)

    def count(q):
        return len(FakeGitHub(FakeClock(), files).matches(q))
    p = shard.plan_query(TERM, count)
    assert len(p["truncated"]) == 1 and p["truncated"][0]["total_count"] == 1500
    assert p["reachable"] == 1000
    p2 = shard.plan_query(TERM, count, splitters=[("extension", ["py", "c"])])
    assert not p2["truncated"] and p2["sum_shards"] == 1500
    assert any("-extension:py -extension:c" in s["q"] for s in p2["shards"])
    with pytest.raises(ValueError):
        shard.plan_query(TERM, count, splitters=[("stars", ["1"])])


def test_search_over_1000_collects_every_item_through_shards(tmp_path):
    clock = FakeClock()
    files = make_files(2300)
    fake = FakeGitHub(clock, files)
    with collection(tmp_path / "out", fake, clock) as col:
        res = col.search(Q1)
    assert res["complete"] and res["items"] == 2300 and res["truncated_shards"] == 0
    plan = json.loads((tmp_path / "out" / "shard_plan.json").read_text(encoding="utf-8"))
    assert len(plan["base"]["shards"]) > 2 and all(s["total_count"] <= 1000 for s in plan["base"]["shards"])


def test_count_mode_downloads_no_items(tmp_path):
    clock = FakeClock()
    fake = FakeGitHub(clock, make_files(1500, sizes=lambda i: 4096))
    qf = write_queries(tmp_path, [{"name": "base", "q": TERM}, {"name": "narrow", "q": f"{TERM} extension:py"}])
    with collection(tmp_path / "out", fake, clock) as col:
        res = col.count(qf)
    rows = {r["name"]: r for r in res["queries"]}
    assert rows["base"]["total_count"] == 1500 and len(rows["base"]["truncated"]) == 1
    assert rows["narrow"]["total_count"] == 500 and rows["narrow"]["shards"] == 1
    assert res["totals"]["total_count"] == 2000 and res["totals"]["reachable"] == 1500
    assert all("per_page=1" in u for u in fake.urls("/search/"))
    assert not (tmp_path / "out" / "items.jsonl").exists()
    assert all(e["tag"] == "probe" for e in RawArchive(tmp_path / "out").entries())


# -- resume, idempotence, replay -------------------------------------------------------

def test_interrupted_search_resumes_without_refetching_pages(tmp_path):
    files = make_files(2300)
    clock = FakeClock()
    ref = FakeGitHub(clock, files)
    with collection(tmp_path / "ref", ref, clock) as col:
        col.search(Q1)
    expected_pages = len([u for u in ref.urls("/search/code") if "per_page=100" in u])

    clock = FakeClock()
    first = FakeGitHub(clock, files)
    first.raise_after = len(ref.requests) // 2
    out = tmp_path / "out"
    with pytest.raises(KeyboardInterrupt):
        with collection(out, first, clock) as col:
            col.search(Q1)
    prog = json.loads((out / "progress.json").read_text(encoding="utf-8"))
    assert prog["phase"] == "interrupted" and "message" in prog

    second = FakeGitHub(clock, files)
    with collection(out, second, clock) as col:
        res = col.search(Q1)
    assert res["complete"] and res["items"] == 2300
    pages = [u for f in (first, second) for u in f.urls("/search/code") if "per_page=100" in u]
    assert len(pages) == expected_pages                       # no page asked twice
    assert not [u for u in second.urls("/search/code") if "per_page=1&" in u or u.endswith("per_page=1")]
    assert (out / "items.jsonl").read_bytes() == (tmp_path / "ref" / "items.jsonl").read_bytes()

    third = FakeGitHub(clock, files)                          # a finished collection: nothing to ask
    with collection(out, third, clock) as col:
        res = col.search(Q1)
    assert third.requests == [] and res["items"] == 2300
    man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert [r["outcome"] for r in man["runs"]] == ["interrupted", "done", "done"]


def test_max_items_stops_and_a_rerun_continues(tmp_path):
    clock = FakeClock()
    fake = FakeGitHub(clock, make_files(250))
    out = tmp_path / "out"
    with collection(out, fake, clock) as col:
        res = col.search(Q1, max_items=100)
    assert res["items"] == 100 and res["stopped"] == "max_items" and not res["complete"]
    with collection(out, FakeGitHub(clock, make_files(250)), clock) as col:
        res = col.search(Q1)
    assert res["items"] == 250 and res["complete"]


def test_changing_a_query_definition_is_refused(tmp_path):
    clock = FakeClock()
    out = tmp_path / "out"
    with collection(out, FakeGitHub(clock, make_files(5)), clock) as col:
        col.count(Q1)
    with pytest.raises(ValueError):
        with collection(out, FakeGitHub(clock, make_files(5)), clock) as col:
            col.count([{"name": "base", "q": TERM + " extension:py"}])


def test_replay_rebuilds_items_without_network_and_checks_hashes(tmp_path):
    clock = FakeClock()
    out = tmp_path / "out"
    with collection(out, FakeGitHub(clock, make_files(1300)), clock) as col:
        col.search(Q1)
    res = replay(out)
    assert res["ok"] and res["items"] == 1300 and res["matches_items_jsonl"]
    assert res["queries"]["base"]["total_count"] == 1300
    victim = next((out / "raw").glob("*.json"))
    victim.write_bytes(victim.read_bytes() + b" ")
    res2 = replay(out)
    assert not res2["ok"] and victim.stem in res2["sha_mismatches"]


def test_status_reports_progress_and_counts(tmp_path):
    clock = FakeClock()
    out = tmp_path / "out"
    with collection(out, FakeGitHub(clock, make_files(250)), clock, collection_id="REC-9001") as col:
        col.search(Q1)
    s = status(out)
    assert s["progress"]["collection_id"] == "REC-9001" and s["progress"]["phase"] == "done"
    assert s["counts"]["items"] == 250 and s["counts"]["pages"] == 3
    with pytest.raises(FileNotFoundError):
        status(tmp_path / "nothing")


def test_manifest_records_the_snapshot_and_no_item_content(tmp_path):
    clock = FakeClock()
    files = make_files(250)
    out = tmp_path / "out"
    with collection(out, FakeGitHub(clock, files), clock, collection_id="REC-9002") as col:
        col.search(Q1)
    man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    for k in ("snapshot_start_utc", "snapshot_end_utc", "api", "queries", "shard_plan", "counts",
              "tool_version", "caller"):
        assert k in man
    assert man["api"]["api_version_header"] and man["api"]["api_version_selected"] == "2022-11-28"
    assert man["api"]["authenticated"] is True and man["collection_id"] == "REC-9002"
    text = json.dumps(man)
    assert not any(f["path"] in text or f["sha"] in text for f in files)


def test_git_info_reads_the_callers_commit(tmp_path):
    repo = tmp_path / "caller"
    repo.mkdir()
    g = ["git", "-C", str(repo), "-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    subprocess.run(g + ["init", "-q"], check=True)
    (repo / "a.txt").write_text("x", encoding="utf-8")
    subprocess.run(g + ["add", "a.txt"], check=True)
    subprocess.run(g + ["commit", "-q", "-m", "init"], check=True)
    info = git_info(repo)
    assert len(info["git_commit"]) == 40 and info["git_dirty"] is False
    assert git_info(tmp_path / "nope")["git_commit"] is None


# -- helpers ----------------------------------------------------------------------------

def test_repo_metadata_and_blob_helpers(tmp_path):
    clock = FakeClock()
    files = make_files(5)
    fake = FakeGitHub(clock, files)
    with collection(tmp_path / "out", fake, clock) as col:
        facts = col.repo_metadata("acme-lab/demo-1")
        assert facts["archived"] is True and facts["owner_type"] == "Organization"
        assert facts["fork"] is False and facts["stargazers_count"] == 3 and facts["is_template"] is False
        n = len(fake.requests)
        assert col.repo_metadata("acme-lab/demo-1") == facts and len(fake.requests) == n   # cached
        assert col.repo_metadata("acme-lab/missing") is None
        assert col.blob(files[2]["repo"], files[2]["sha"]) == files[2]["content"].encode()
        assert col.blob("acme-lab/demo-1", "0" * 40) is None


def test_load_queries_validates():
    assert load_queries(Q1)[0]["kind"] == "code"
    for bad in ([], [{"name": "a"}], [{"name": "a b", "q": "x"}], [{"name": "a", "q": "x"}] * 2,
                [{"name": "a", "q": "x", "kind": "gists"}], [{"name": "a", "q": "x", "split": {"stars": ["1"]}}]):
        with pytest.raises(ValueError):
            load_queries(bad)


# -- redaction -------------------------------------------------------------------------

def test_samples_for_humans_are_redacted_and_raw_is_not_by_default(tmp_path):
    clock = FakeClock()
    secret = "glpat-" + hashlib.sha256(b"invented sample secret").hexdigest()[:20]
    other = "ghp_" + hashlib.sha256(b"another invented secret").hexdigest()[:36]
    with collection(tmp_path / "out", FakeGitHub(clock, make_files(5)), clock) as col:
        p = col.write_sample("muestra.json", {"snippet": f"token = {other}", "note": "texto inventado"})
        assert other not in p.read_text(encoding="utf-8") and "REDACTADO" in p.read_text(encoding="utf-8")
        with pytest.raises(ValueError):
            col.write_sample("../fuera.json", {})
    body = json.dumps({"description": f"api_key = {other}", "x": secret}).encode()
    a = RawArchive(tmp_path / "plain")
    assert other.encode() in a.read(a.save("GET", "https://api.github.com/x", 200, {}, body))
    r = RawArchive(tmp_path / "red", redact_raw=True)
    assert other.encode() not in r.read(r.save("GET", "https://api.github.com/x", 200, {}, body))


# -- token -----------------------------------------------------------------------------

def _token_file(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / "github-token"
    p.write_text(fake_token() + "\n", encoding="utf-8")
    return p


@pytest.mark.parametrize("layout", ["git", "vault", "onedrive", "dropbox", "gdrive"])
def test_token_file_in_a_risky_place_is_refused(tmp_path, layout):
    if layout == "git":
        (tmp_path / "repo" / ".git").mkdir(parents=True)
        p = _token_file(tmp_path / "repo" / "conf")
    elif layout == "vault":
        (tmp_path / "vault" / "Projects").mkdir(parents=True)
        (tmp_path / "vault" / ".obsidian").mkdir()
        p = _token_file(tmp_path / "vault" / "secrets")
    elif layout == "onedrive":
        p = _token_file(tmp_path / "OneDrive - Invented Org" / "keys")
    elif layout == "dropbox":
        p = _token_file(tmp_path / "Dropbox" / "keys")
    else:
        p = _token_file(tmp_path / "My Drive" / "keys")
    env = {"KAIRO_GITHUB_TOKEN_FILE": str(p)}
    with pytest.raises(TokenRefused) as e:
        load_token(env)
    assert fake_token() not in str(e.value)
    code = cli.main(["count", "--queries", str(write_queries(tmp_path, Q1)), "--out", str(tmp_path / "o")],
                    env=env, transport=lambda *a: pytest.fail("refused but went to the network"))
    assert code == 3


def test_token_under_the_onedrive_env_root_is_refused(tmp_path):
    root = tmp_path / "SyncRoot"
    p = _token_file(root / "k")
    assert location_problem(p, {"OneDrive": str(root)})
    assert location_problem(_token_file(tmp_path / "plain"), {"OneDrive": str(root)}) is None


def test_missing_token_is_unauthenticated_with_a_warning_and_bad_content_is_refused(tmp_path):
    warned = []
    assert load_token({"KAIRO_GITHUB_TOKEN_FILE": str(tmp_path / "none")}, warn=warned.append) is None
    assert warned and "sin autenticar" in warned[0]
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "t").write_text("two words here and more text", encoding="utf-8")
    with pytest.raises(TokenRefused) as e:
        load_token({"KAIRO_GITHUB_TOKEN_FILE": str(bad / "t")})
    assert "two words" not in str(e.value)
    (bad / "e").write_text("  \n", encoding="utf-8")
    with pytest.raises(TokenRefused):
        load_token({"KAIRO_GITHUB_TOKEN_FILE": str(bad / "e")})
    tok = load_token({"KAIRO_GITHUB_TOKEN_FILE": str(_token_file(tmp_path / "ok"))})
    assert fake_token() not in repr(tok) and tok.reveal() == fake_token()


def test_the_token_is_sent_but_never_written_or_printed(tmp_path, capsys):
    clock = FakeClock()
    fake = FakeGitHub(clock, make_files(1300))
    tok_file = _token_file(tmp_path / "home" / ".kairo")
    out = tmp_path / "out"
    qf = write_queries(tmp_path, Q1)
    env = {"KAIRO_GITHUB_TOKEN_FILE": str(tok_file)}
    inject = {"transport": fake, "clock": clock, "sleep": clock.sleep, "caller_repo": tmp_path}
    assert cli.main(["search", "--queries", str(qf), "--out", str(out)], env=env, **inject) == 0
    fake.script = [(403, {}, b'{"message":"Resource not accessible"}')]
    assert cli.main(["search", "--queries", str(qf), "--out", str(tmp_path / "out2")], env=env, **inject) == 1
    assert cli.main(["status", "--out", str(out)]) == 0
    assert cli.main(["replay", "--out", str(out)]) == 0
    printed = capsys.readouterr()
    tok = fake_token()
    assert all(h.get("Authorization") == f"Bearer {tok}" for _, _, h in fake.requests)
    assert tok not in printed.out and tok not in printed.err
    files = [p for d in (out, tmp_path / "out2") for p in d.rglob("*") if p.is_file()]
    assert len(files) > 10
    for p in files:
        assert tok.encode() not in p.read_bytes(), p
        assert b"Bearer" not in p.read_bytes(), p


def test_cli_unauthenticated_code_search_reports_an_error(tmp_path, capsys):
    clock = FakeClock()
    fake = FakeGitHub(clock, make_files(5))
    env = {"KAIRO_GITHUB_TOKEN_FILE": str(tmp_path / "absent")}
    code = cli.main(["count", "--queries", str(write_queries(tmp_path, Q1)), "--out", str(tmp_path / "o")],
                    env=env, transport=fake, clock=clock, sleep=clock.sleep, caller_repo=tmp_path)
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and "401" in out["error"] and "Authorization" not in fake.requests[0][2]
    prog = json.loads((tmp_path / "o" / "progress.json").read_text(encoding="utf-8"))
    assert prog["phase"] == "error"
