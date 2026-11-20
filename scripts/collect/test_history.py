"""Tests for kairo_collect.history and Collection.fetch_history.

No network: the "remote" is a bare repository built under tmp_path (file:// URL served
by the fake API's clone_url); every file is invented text."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from kairo_collect import history as hist  # noqa: E402
from kairo_collect.collection import Collection  # noqa: E402
from test_collect import FakeClock, FakeGitHub, fake_token, make_files  # noqa: E402

REPO = "acme-lab/demo-1"
PLAIN = "password: clave-inventada-en-claro-1\napi: otra-inventada-2\n"
ENC = ("password: ENC[AES256_GCM,data:QUJDREVGR0hJSktMTU5PUA==,iv:QUJDREVGR0hJSktMTU5PUA==,tag:QUJDREVGR0g=,type:str]\n"
       "api: ENC[AES256_GCM,data:WFlaV1ZVVFNSUVBPTk1MSw==,iv:QUJDREVGR0hJSktMTU5PUA==,tag:QUJDREVGR0g=,type:str]\n"
       "sops:\n    mac: ENC[AES256_GCM,data:Wm9vbQ==,iv:QUJDREVGR0hJSktMTU5PUA==,tag:QUJDREVGR0g=,type:str]\n"
       "    version: 3.9.0\n")
ENC2 = ENC.replace("WFlaV1ZVVFNSUVBPTk1MSw==", "TU5PUFFSU1RVVldYWVo9PQ==")
ENV_PLAIN = "API_TOKEN=valor-inventado-en-claro\n"
ENV_ENC = "DOTENV_PUBLIC_KEY=\"02aa\"\nAPI_TOKEN=encrypted:QUJDREVGR0hJSktMTU5PUFFSU1RVVldY\n"


def _git(repo: Path, *args, date: str | None = None):
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1"}
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.invalid", "-c", "user.name=t",
                    "-c", "commit.gpgsign=false", *args], check=True, capture_output=True, env=env)


def _commit(work: Path, files: dict, date: str, msg: str, delete=()):
    for rel, text in files.items():
        p = work / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    for rel in delete:
        _git(work, "rm", "-q", rel)
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "--allow-empty", "-m", msg, date=date)


@pytest.fixture
def remote(tmp_path):
    work = tmp_path / "src-work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _commit(work, {"README.md": "demo inventada\n"}, "2030-01-01T00:00:00+00:00", "inicio")
    _commit(work, {"k8s/secret.enc.yaml": PLAIN, ".env": ENV_PLAIN}, "2030-01-02T00:00:00+00:00", "en claro")
    _commit(work, {"k8s/secret.enc.yaml": ENC, ".env": ENV_ENC}, "2030-01-03T00:00:00+00:00", "cifrado")
    _git(work, "checkout", "-q", "-b", "rama")
    _commit(work, {"k8s/secret.enc.yaml": ENC2}, "2030-01-04T00:00:00+00:00", "otra rama")
    _git(work, "checkout", "-q", "main")
    _commit(work, {}, "2030-01-05T00:00:00+00:00", "borrado", delete=[".env"])
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True, capture_output=True)
    _git(bare, "config", "uploadpack.allowFilter", "true")
    return bare


class GitFake(FakeGitHub):
    def __init__(self, clock, clone_urls: dict):
        super().__init__(clock, make_files(41))
        self.clone_urls = clone_urls

    def _repo(self, path: str):
        st, h, body = super()._repo(path)
        if st == 200:
            d = json.loads(body)
            d["clone_url"] = self.clone_urls.get(d["full_name"])
            body = json.dumps(d).encode()
        return st, h, body


def _col(out: Path, remote: Path, token=None, **kw):
    clock = FakeClock()
    fake = GitFake(clock, {REPO: remote.as_uri(), "acme-lab/demo-2": "http://insecure.example/x.git"})
    return Collection(out, transport=fake, clock=clock, sleep=clock.sleep, token=token, caller_repo=out, **kw)


class Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append((list(argv), dict(kw.get("env") or {})))
        return subprocess.run(argv, **kw)


def _all_bytes(root: Path) -> list[tuple[Path, bytes]]:
    return [(p, p.read_bytes()) for p in root.rglob("*") if p.is_file()]


def test_fetch_history_keeps_only_encrypted_blobs_and_records_every_version(tmp_path, remote):
    out = tmp_path / "out"
    with _col(out, remote) as col:
        res = col.fetch_history(REPO, ["k8s/secret.enc.yaml", ".env"], max_bytes_budget=50_000_000)
        assert res["status"] == "done", res
        assert res["versions"] == 5 and res["deletions"] == 1
        assert res["unencrypted"] == 2 and res["kept"] == 3
        rows = col.history_versions(REPO, "k8s/secret.enc.yaml")
        assert [r["author_date"][:10] for r in rows] == ["2030-01-02", "2030-01-03", "2030-01-04"]
        assert {r["change"] for r in rows} <= {"A", "M"} and all(r["size"] for r in rows)
        tw = col.plaintext_twins(REPO, "k8s/secret.enc.yaml")
        assert tw["versions"] == 3 and tw["encrypted_versions"] == 2 and tw["plaintext_twins"] == 1
        first = tw["items"][0]
        assert first["plaintext_twin"] and first["same_keys_as_encrypted"] and not first["encrypted"]
        env_tw = col.plaintext_twins(REPO, ".env")
        # the encrypted .env adds DOTENV_PUBLIC_KEY: the key-name sets differ, exactly compared
        assert env_tw["plaintext_twins"] == 1 and not env_tw["items"][0]["same_keys_as_encrypted"]
        col.finish()
    rdir = out / "history" / "acme-lab__demo-1"
    assert not (rdir / "repo.git").exists()                    # clone removed when done
    assert len(list((rdir / "blobs").iterdir())) == 3
    for p, data in _all_bytes(out):
        for secret in (b"clave-inventada-en-claro-1", b"valor-inventado-en-claro", b"otra-inventada-2"):
            assert secret not in data, p
    man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert man["history"]["versions"] == 5 and man["history"]["unencrypted_versions"] == 2
    assert "History:" in (out / "manifest.md").read_text(encoding="utf-8")


def test_fetch_history_is_idempotent_and_asks_git_nothing_twice(tmp_path, remote):
    out = tmp_path / "out"
    with _col(out, remote) as col:
        col.fetch_history(REPO, ["k8s/secret.enc.yaml"], max_bytes_budget=50_000_000)
        spy = Spy()
        col.git.run = spy
        res = col.fetch_history(REPO, ["k8s/secret.enc.yaml"], max_bytes_budget=50_000_000)
        assert res["status"] == "done" and spy.calls == []
        n = len(col.history_versions(REPO))
        res = col.fetch_history(REPO, [".env"], max_bytes_budget=50_000_000)      # a new path: clone again
        assert res["status"] == "done" and spy.calls
        assert len(col.history_versions(REPO)) == n + 3


def test_budget_stops_with_a_clear_state_and_a_rerun_continues(tmp_path, remote):
    out = tmp_path / "out"
    with _col(out, remote) as col:
        res = col.fetch_history(REPO, ["k8s/secret.enc.yaml", ".env"], max_bytes_budget=1)
        assert res["status"] == "budget" and "presupuesto" in res["message"] and res["extracted"] == 0
        prog = json.loads((out / "progress.json").read_text(encoding="utf-8"))
        assert "presupuesto" in prog["message"]
        # the clone itself went over: the blobs wait, and the clone stays for the rerun
        rdir = out / "history" / "acme-lab__demo-1"
        assert (rdir / "repo.git").is_dir()
        res2 = col.fetch_history(REPO, ["k8s/secret.enc.yaml", ".env"], max_bytes_budget=1)
        assert res2["status"] == "budget"
        res3 = col.fetch_history(REPO, ["k8s/secret.enc.yaml", ".env"], max_bytes_budget=50_000_000)
        assert res3["status"] == "done" and res3["extracted"] == 5
        assert col.state.history_repo(REPO)["status"] == "done"
        assert not (rdir / "repo.git").exists()


def test_budget_after_clone_keeps_the_clone_for_resume(tmp_path, remote, monkeypatch):
    out = tmp_path / "out"
    with _col(out, remote) as col:
        real = hist.dir_size
        calls = {"n": 0}

        def sized(p):                      # the store "grows" past the budget after the first blob
            calls["n"] += 1
            return real(p) + (10**9 if Path(p).name == "acme-lab__demo-1" and (Path(p) / "blobs").exists() else 0)
        monkeypatch.setattr(hist, "dir_size", sized)
        res = col.fetch_history(REPO, ["k8s/secret.enc.yaml", ".env"], max_bytes_budget=10**8)
        assert res["status"] == "budget" and 0 < res["extracted"] < 5
        assert (out / "history" / "acme-lab__demo-1" / "repo.git").is_dir()
        monkeypatch.setattr(hist, "dir_size", real)
        res = col.fetch_history(REPO, ["k8s/secret.enc.yaml", ".env"], max_bytes_budget=10**8)
        assert res["status"] == "done" and res["extracted"] == 5


def test_the_token_goes_through_the_environment_and_never_to_disk_or_argv(tmp_path, remote, monkeypatch):
    tok = fake_token()
    out = tmp_path / "out"
    real_auth = hist.auth_env
    # the remote is a local file:// repository; send the header to it anyway, to watch it travel
    monkeypatch.setattr(hist, "auth_env", lambda t, url, hosts: real_auth(t, "https://github.com/x.git",
                                                                          ["github.com"]))
    with _col(out, remote, token=tok) as col:
        spy = Spy()
        col.git.run = spy
        res = col.fetch_history(REPO, ["k8s/secret.enc.yaml", ".env"], max_bytes_budget=50_000_000, keep_clone=True)
        col.finish()
    assert res["status"] == "done"
    basic = base64.b64encode(f"x-access-token:{tok}".encode()).decode()
    assert spy.calls
    for argv, env in spy.calls:
        assert not any(tok in a or basic in a for a in argv)
        vals = [env[k] for k in env if k.startswith("GIT_CONFIG_VALUE_")]
        assert any(basic in v for v in vals)
        keys = [env[k] for k in env if k.startswith("GIT_CONFIG_KEY_")]
        assert "http.https://github.com/.extraheader" in keys and "credential.helper" in keys
        assert env["GIT_TERMINAL_PROMPT"] == "0" and "GIT_TRACE" not in env
    assert (out / "history" / "acme-lab__demo-1" / "repo.git").is_dir()
    for p, data in _all_bytes(tmp_path):
        assert tok.encode() not in data and basic.encode() not in data, p


def test_auth_env_only_for_the_api_host_over_https():
    tok = fake_token()
    assert hist.auth_env(tok, "https://github.com/acme-lab/demo-1.git", ["github.com"])
    assert hist.auth_env(tok, "https://evil.example/acme-lab/demo-1.git", ["github.com"]) == {}
    assert hist.auth_env(tok, "file:///tmp/x.git", ["github.com"]) == {}
    assert hist.auth_env(None, "https://github.com/a/b.git", ["github.com"]) == {}
    env = hist.git_env({"k": "v"}, "https", base_env={"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "a.b",
                                                      "GIT_CONFIG_VALUE_0": "c", "GIT_TRACE": "1"})
    assert env["GIT_CONFIG_KEY_0"] == "a.b" and env["GIT_CONFIG_COUNT"] == "4" and "GIT_TRACE" not in env
    assert env["GIT_ALLOW_PROTOCOL"] == "https" and env["GIT_LITERAL_PATHSPECS"] == "1"


def test_refusals_and_errors(tmp_path, remote):
    with _col(tmp_path / "out", remote) as col:
        for bad in ("no-slash", "a/b/c", "../x/y"):
            with pytest.raises(hist.HistoryError):
                col.fetch_history(bad, ["x"], max_bytes_budget=10)
        for paths in (["../etc/passwd"], ["/abs"], ["C:/x"], []):
            with pytest.raises(hist.HistoryError):
                col.fetch_history(REPO, paths, max_bytes_budget=10)
        with pytest.raises(ValueError):
            col.fetch_history(REPO, ["x"], max_bytes_budget=0)
        assert col.fetch_history("acme-lab/demo-2", ["x"], max_bytes_budget=10**6)["status"] == "error"
        assert col.fetch_history("acme-lab/missing", ["x"], max_bytes_budget=10**6)["status"] == "not_found"


def test_markers_key_names_and_pure_twins():
    assert hist.looks_encrypted(ENC.encode())["sops"] and hist.looks_encrypted(ENV_ENC.encode())["dotenvx"]
    assert hist.looks_encrypted(b"age-encryption.org/v1\n-> X25519 abc\n--- x\n\x00\x01")["age"]
    assert not hist.looks_encrypted(PLAIN.encode())["encrypted"]
    assert hist.key_names(ENC.encode()) == ["api", "password"] == hist.key_names(PLAIN.encode())
    assert hist.key_names(b'{"a": 1, "sops": {"mac": "x"}, "b": {"c": 2}}') == ["a", "b", "c"]
    vs = [{"commit": "c2", "author_date": "2030-01-02", "blob_sha": "b2", "data": ENC.encode()},
          {"commit": "c1", "author_date": "2030-01-01", "blob_sha": "b1", "data": PLAIN.encode()},
          {"commit": "c3", "author_date": "2030-01-03", "blob_sha": "", "data": b""}]
    tw = hist.plaintext_twins(vs)
    assert tw["plaintext_twins"] == 1 and tw["items"][0]["commit"] == "c1" and tw["items"][0]["same_keys_as_encrypted"]
    assert "clave" not in json.dumps(tw)


def test_parse_log_reads_raw_z_output():
    out = (b"\x01" + b"a" * 40 + b" 2030-01-02T00:00:00+00:00\n:000000 100644 " + b"0" * 40 + b" " + b"b" * 40
           + b" A\x00k/s e.yaml\x00\x00\x01" + b"c" * 40 + b" 2030-01-03T00:00:00+00:00\n:100644 000000 " + b"b" * 40
           + b" " + b"0" * 40 + b" D\x00k/s e.yaml\x00:100644 100644 " + b"d" * 40 + b" " + b"e" * 40 + b" M\x00otro\x00")
    rows = hist.parse_log(out, {"k/s e.yaml"})
    assert [(r["change"], r["blob_sha"][:1]) for r in rows] == [("A", "b"), ("D", "")]
