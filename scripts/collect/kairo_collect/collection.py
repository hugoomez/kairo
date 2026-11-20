"""A collection: named search queries → shard plan → pages → items, resumable.

Output folder (``KAIRO_COLLECTION_OUT`` or ``--out``)::

    state.sqlite        resumable state (state.py)
    raw/                every response body + raw/index.jsonl (archive.py)
    progress.json       what the Kairo app displays (progress.py)
    manifest.json       the snapshot's record: start / end UTC, API version sent
                        and selected, authenticated or not, queries, shard plan,
                        counts, waits, tool version, the caller repo's commit.
                        No item content.
    shard_plan.json     the plan per query (shard.py), deterministic
    items.jsonl         after `search`: one line per item {repo, path, sha, name,
                        html_url, queries}, sorted by (repo, path, sha)
    samples/            what the researcher writes for people (redacted)
    replay/             what `replay` rebuilt from raw/ without network

queries.json: ``[{"name": "a", "q": "term language:x", "kind": "code",
"split": {"extension": ["py", "c"]}, "size_split": true}]`` — only name and q
are required; kind is a /search/<kind> endpoint (default code).
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

from . import TOOL, TOOL_VERSION
from .archive import RawArchive, atomic_write_bytes, atomic_write_json, utc_iso, write_for_humans
from .github import API_VERSION, Client
from .progress import Progress
from .shard import CAP, CODE_SIZE_MAX, REPO_SIZE_MAX, SPLIT_QUALIFIERS, plan_query
from .state import State
from .token import Token, load_token

KINDS = ("code", "repositories", "issues", "commits", "users", "topics", "labels")
PER_PAGE = 100
MANIFEST_SCHEMA = "kairo/collect-manifest@1"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,80}$")


def load_queries(src) -> list[dict]:
    """Validated, normalised query specs from a path or a list."""
    if isinstance(src, (str, Path)):
        try:
            src = json.loads(Path(src).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ValueError(f"no se puede leer el fichero de consultas: {e}") from None
    if not isinstance(src, list) or not src:
        raise ValueError("el fichero de consultas debe ser una lista no vacía de {\"name\", \"q\"}")
    out, seen = [], set()
    for i, d in enumerate(src):
        if not isinstance(d, dict) or not isinstance(d.get("name"), str) or not isinstance(d.get("q"), str):
            raise ValueError(f"consulta {i}: hace falta \"name\" y \"q\" (texto)")
        name, q = d["name"], d["q"].strip()
        if not _NAME_RE.match(name):
            raise ValueError(f"consulta {i}: nombre no válido {name!r} (letras, números, _ . -)")
        if name in seen:
            raise ValueError(f"consulta repetida: {name}")
        if not q:
            raise ValueError(f"consulta {name}: q vacía")
        kind = d.get("kind", "code")
        if kind not in KINDS:
            raise ValueError(f"consulta {name}: kind {kind!r} no válido ({', '.join(KINDS)})")
        split = d.get("split") or {}
        if not isinstance(split, dict) or any(k not in SPLIT_QUALIFIERS or not isinstance(v, list)
                                              for k, v in split.items()):
            raise ValueError(f"consulta {name}: split debe ser {{calificador: [valores]}} con "
                             f"calificadores {', '.join(SPLIT_QUALIFIERS)}")
        size_split = bool(d.get("size_split", kind in ("code", "repositories")))
        size_max = int(d.get("size_max", CODE_SIZE_MAX if kind == "code" else REPO_SIZE_MAX))
        seen.add(name)
        out.append({"name": name, "q": q, "kind": kind,
                    "split": {k: [str(v) for v in split[k]] for k in SPLIT_QUALIFIERS if k in split},
                    "size_split": size_split, "size_max": size_max})
    return out


def item_record(kind: str, it: dict) -> dict:
    """The identity of a search result, key (repo full_name, path, sha); no content."""
    if kind == "code":
        repo = (it.get("repository") or {}).get("full_name") or ""
        return {"repo": repo, "path": it.get("path") or "", "sha": it.get("sha") or "",
                "name": it.get("name"), "html_url": it.get("html_url")}
    if kind == "repositories":
        return {"repo": it.get("full_name") or "", "path": "", "sha": "",
                "name": it.get("name"), "html_url": it.get("html_url")}
    if kind == "commits":
        repo = (it.get("repository") or {}).get("full_name") or ""
        return {"repo": repo, "path": "", "sha": it.get("sha") or "", "name": None,
                "html_url": it.get("html_url")}
    repo_url = it.get("repository_url") or ""
    repo = "/".join(repo_url.rstrip("/").split("/")[-2:]) if repo_url else ""
    return {"repo": repo, "path": it.get("html_url") or it.get("url") or "", "sha": str(it.get("id") or ""),
            "name": it.get("name") or it.get("title") or it.get("login"), "html_url": it.get("html_url")}


def write_items(path: Path, items: list[dict]) -> None:
    data = "".join(json.dumps(i, ensure_ascii=False, sort_keys=True) + "\n" for i in items)
    atomic_write_bytes(path, data.encode("utf-8"))


def git_info(repo: Path | str | None) -> dict:
    """The caller repository's HEAD commit and whether its tracked files changed."""
    if not repo:
        return {"git_commit": None, "git_dirty": None}
    try:
        r = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True,
                           text=True, timeout=15)
        if r.returncode != 0:
            return {"git_commit": None, "git_dirty": None}
        s = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"],
                           capture_output=True, text=True, timeout=15)
        return {"git_commit": r.stdout.strip(), "git_dirty": bool(s.stdout.strip()) if s.returncode == 0 else None}
    except (OSError, subprocess.SubprocessError):
        return {"git_commit": None, "git_dirty": None}


def _expected_pages(n: int) -> int:
    return math.ceil(min(n, CAP) / PER_PAGE)


class Collection:
    """What a collection script uses::

        from kairo_collect import Collection
        with Collection.from_env() as col:            # KAIRO_COLLECTION_OUT / _ID, token file
            col.search("queries.json", max_items=None)
            for it in col.items():
                facts = col.repo_metadata(it["repo"])
                data = col.blob(it["repo"], it["sha"])
            col.write_sample("ejemplo.json", {...})   # redacted for people
    """

    def __init__(self, out, *, token=None, transport=None, clock=time.time, sleep=time.sleep,
                 collection_id: str | None = None, api_version: str | None = None,
                 min_interval: dict | None = None, redact_raw: bool = False, rng=None,
                 progress_every_s: float = 2.0, caller_repo=None, max_retries: int = 8,
                 backoff_cap: float = 900.0, base_url: str | None = None):
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        tok = token.reveal() if isinstance(token, Token) else token
        self.clock = clock
        self.state = State(self.out)
        self.archive = RawArchive(self.out, redact_raw=redact_raw, forbid=[tok] if tok else None, clock=clock)
        self.progress = Progress(self.out, collection_id, clock, progress_every_s)
        kw = {"base_url": base_url} if base_url else {}
        self.client = Client(tok, transport=transport, clock=clock, sleep=sleep, archive=self.archive,
                             etags=self.state, api_version=(api_version or os.environ.get("KAIRO_GITHUB_API_VERSION")
                                          or API_VERSION),
                             min_interval=min_interval, max_retries=max_retries, backoff_cap=backoff_cap,
                             rng=rng, on_wait=self.progress.waiting, **kw)
        self.collection_id = collection_id
        self.caller_repo = caller_repo if caller_repo is not None else os.getcwd()
        self.redact_raw = redact_raw
        self._mode: str | None = None
        self._run_start = utc_iso(clock())
        self._stopped: str | None = None
        if self.state.get_meta("first_start") is None:
            self.state.set_meta("first_start", self._run_start)
        if collection_id:
            self.state.set_meta("collection_id", collection_id)

    @classmethod
    def from_env(cls, **kw) -> Collection:
        out = kw.pop("out", None) or os.environ.get("KAIRO_COLLECTION_OUT")
        if not out:
            raise ValueError("falta la carpeta de salida: KAIRO_COLLECTION_OUT o out=")
        kw.setdefault("collection_id", os.environ.get("KAIRO_COLLECTION_ID"))
        if "token" not in kw:
            kw["token"] = load_token()
        return cls(out, **kw)

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        if et is KeyboardInterrupt:
            self.fail("interrupted", "interrumpida; al relanzarla sigue donde se quedó")
        elif et is not None:
            self.fail("error", f"{et.__name__}: {ev}"[:300])
        self.close()
        return False

    def close(self) -> None:
        self.state.close()

    # -- counting -------------------------------------------------------------
    def _count(self, kind: str, q: str) -> int:
        cached = self.state.probe(kind, q)
        if cached:
            return cached[0]
        d = self.client.get(f"/search/{kind}", {"q": q, "per_page": 1}, tag="probe").json() or {}
        total = int(d.get("total_count") or 0)
        self.state.save_probe(kind, q, total, bool(d.get("incomplete_results")))
        return total

    def plan(self, queries) -> list[dict]:
        """Register the queries and plan each one not yet planned (probes are cached)."""
        specs = load_queries(queries)
        for i, s in enumerate(specs):
            self.state.register_query(s, i)
        self.progress.update(phase="planning", done=0, total=len(specs), unit="queries")
        for i, s in enumerate(specs):
            if self.state.query_plan(s["name"]) is None:
                plan = plan_query(s["q"], lambda q, k=s["kind"]: self._count(k, q),
                                  size_split=s["size_split"], size_max=s["size_max"],
                                  splitters=list(s["split"].items()))
                self.state.save_plan(s["name"], plan)
            self.progress.update(done=i + 1)
        self._write_plan()
        return specs

    def _write_plan(self) -> None:
        atomic_write_json(self.out / "shard_plan.json",
                          {q["name"]: {"kind": q["kind"], **q["plan"]} for q in self.state.queries() if q["plan"]})

    def _summary(self, specs: list[dict]) -> list[dict]:
        per_q = dict(self.state.db.execute("SELECT query, COUNT(*) FROM item_queries GROUP BY query").fetchall())
        out = []
        for s in specs:
            p = self.state.query_plan(s["name"]) or {}
            probe = self.state.probe(s["kind"], s["q"])
            out.append({"name": s["name"], "q": s["q"], "kind": s["kind"],
                        "total_count": p.get("total_count"),
                        "incomplete_results": bool(probe[1]) if probe else None,
                        "shards": len(p.get("shards", [])), "sum_shards": p.get("sum_shards"),
                        "reachable": p.get("reachable"), "truncated": p.get("truncated", []),
                        "items": per_q.get(s["name"], 0)})
        return out

    def count(self, queries) -> dict:
        """total_count per query and its shard plan; downloads no items."""
        self._mode = "count"
        specs = self.plan(queries)
        rows = self._summary(specs)
        for r in rows:
            r.pop("items")
        res = {"ok": True, "mode": "count", "queries": rows,
               "totals": {"total_count": sum(r["total_count"] or 0 for r in rows),
                          "reachable": sum(r["reachable"] or 0 for r in rows),
                          "truncated_shards": sum(len(r["truncated"]) for r in rows),
                          "shards": sum(r["shards"] for r in rows)},
               "requests": self.client.requests}
        self.finish()
        return res

    # -- collecting -----------------------------------------------------------
    def search(self, queries, max_items: int | None = None) -> dict:
        """Plan, then read every page of every shard (resuming where a previous
        run stopped), until done or `max_items` items are stored."""
        self._mode = "search"
        specs = self.plan(queries)
        kinds = {s["name"]: s["kind"] for s in specs}
        names = set(kinds)
        shards = [s for s in self.state.shards() if s["query"] in names]
        total = sum(_expected_pages(s["total_count"]) for s in shards)
        done = sum(s["pages_done"] for s in shards)
        self.progress.update(phase="collecting", done=done, total=max(total, done), unit="pages")
        for sh in shards:
            if sh["done"]:
                continue
            if max_items is not None and self.state.count_items() >= max_items:
                self._stopped = "max_items"
                break
            kind = kinds[sh["query"]]
            url = sh["next_url"] or self.client.url(f"/search/{kind}", {"q": sh["q"], "per_page": PER_PAGE})
            page = sh["pages_done"]
            while url:
                resp = self.client.get(url, tag="page")
                d = resp.json() or {}
                items = [item_record(kind, it) for it in d.get("items") or []]
                nxt = resp.links().get("next")
                page += 1
                self.state.save_page(sh["query"], sh["id"], page, resp.sha256, items, nxt,
                                     d.get("total_count"), bool(d.get("incomplete_results")))
                done += 1
                self.progress.update(done=done, total=max(total, done))
                url = nxt
                if max_items is not None and self.state.count_items() >= max_items:
                    self._stopped = "max_items"
                    break
            if self._stopped:
                break
        items = self.state.items()
        write_items(self.out / "items.jsonl", items)
        rows = self._summary(specs)
        res = {"ok": True, "mode": "search", "queries": rows, "items": len(items),
               "pages": done, "complete": self._stopped is None and all(
                   s["done"] for s in self.state.shards() if s["query"] in names),
               "stopped": self._stopped, "requests": self.client.requests,
               "truncated_shards": sum(len(r["truncated"]) for r in rows)}
        self.finish()
        return res

    def items(self) -> list[dict]:
        return self.state.items()

    # -- helpers ----------------------------------------------------------------
    def repo_metadata(self, full_name: str, refresh: bool = False) -> dict | None:
        if not refresh:
            row = self.state.db.execute("SELECT facts FROM repos WHERE full_name=?", (full_name,)).fetchone()
            if row:
                return self.state.repo(full_name)
        facts = self.client.repo_metadata(full_name)
        self.state.save_repo(full_name, facts)
        return facts

    def blob(self, full_name: str, sha: str) -> bytes | None:
        return self.client.blob(full_name, sha)

    def write_sample(self, rel: str, obj, redact: bool = True) -> Path:
        """A sample for people under <out>/samples/, redacted with redact.py by default."""
        base = (self.out / "samples").resolve()
        dest = (base / rel).resolve()
        if base not in dest.parents:
            raise ValueError(f"la muestra debe quedar dentro de {base}")
        return write_for_humans(dest, obj, redact=redact)

    # -- manifest / progress -----------------------------------------------------
    def manifest(self, end: str | None) -> dict:
        runs = self.state.get_meta("runs", [])
        qs = self.state.queries()
        return {"schema": MANIFEST_SCHEMA, "tool": TOOL, "tool_version": TOOL_VERSION,
                "collection_id": self.collection_id or self.state.get_meta("collection_id"),
                "snapshot_start_utc": self.state.get_meta("first_start"), "snapshot_end_utc": end,
                "runs": runs,
                "api": {"base_url": self.client.base_url, "api_version_header": self.client.api_version,
                        "api_version_selected": self.client.api_version_selected,
                        "authenticated": self.client.authenticated},
                "raw_redacted": self.redact_raw,
                "queries": [{k: q[k] for k in ("name", "q", "kind", "split", "size_split", "size_max")} for q in qs],
                "shard_plan": {q["name"]: q["plan"] for q in qs if q["plan"]},
                "counts": {**self.state.counts(), "per_query": self._summary(qs)},
                "caller": git_info(self.caller_repo),
                "raw": {"index": "raw/index.jsonl", "responses": len(self.archive.entries())}}

    def finish(self, phase: str = "done", message: str | None = None) -> None:
        end = utc_iso(self.clock())
        runs = self.state.get_meta("runs", [])
        runs.append({"mode": self._mode, "start_utc": self._run_start, "end_utc": end, "outcome": phase,
                     "requests": self.client.requests,
                     "waits_s": {k: round(v, 1) for k, v in self.client.waits.seconds.items()},
                     "stopped": self._stopped})
        self.state.set_meta("runs", runs)
        atomic_write_json(self.out / "manifest.json", self.manifest(end if phase == "done" else None))
        if message is None and self._stopped == "max_items":
            message = "parada al llegar a --max-items; al relanzarla sigue"
        self.progress.waiting(None)
        self.progress.update(phase=phase, message=message, force=True)

    def fail(self, phase: str, message: str) -> None:
        try:
            self.finish(phase, message)
        except Exception:   # noqa: BLE001 — reporting the failure must not hide it
            self.progress.update(phase=phase, message=message, force=True)


# -- replay / status (no network, no client) -----------------------------------
def replay(out) -> dict:
    """Rebuild items and counts from raw/ alone: every kept body checked against
    its sha256, the `page` responses parsed again, mapped to their queries by
    shard_plan.json. Writes <out>/replay/{items.jsonl, summary.json}."""
    out = Path(out)
    arch = RawArchive(out)
    entries = arch.entries()
    if not entries:
        raise FileNotFoundError(f"no hay respuestas guardadas en {out / 'raw'}")
    plan_path = out / "shard_plan.json"
    plans = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.is_file() else {}
    by_q: dict[str, list[str]] = {}
    for name, p in plans.items():
        for s in p.get("shards", []):
            by_q.setdefault(s["q"], []).append(name)
    mismatches, items, quer = [], {}, {}
    probes: dict[str, int] = {}
    pages = 0
    checked: dict[str, bool] = {}
    for e in entries:
        sha = e["sha256"]
        if sha not in checked:
            try:
                arch.read(sha)
                checked[sha] = True
            except (OSError, ValueError):
                checked[sha] = False
                mismatches.append(sha)
        if not checked[sha] or e.get("status") not in (200, 304):
            continue
        path = urlsplit(e["url"]).path
        if not path.startswith("/search/"):
            continue
        kind = path.split("/")[2]
        d = json.loads(arch.read(sha, verify=False) or b"{}")
        q = e.get("params", {}).get("q", "")
        if e.get("tag") == "probe":
            probes[q] = int(d.get("total_count") or 0)
            continue
        if e.get("tag") != "page":
            continue
        pages += 1
        for it in d.get("items") or []:
            rec = item_record(kind, it)
            key = (rec["repo"], rec["path"], rec["sha"])
            items.setdefault(key, rec)
            quer.setdefault(key, set()).update(by_q.get(q, ["?"]))
    rebuilt = [{**items[k], "queries": sorted(quer[k])} for k in sorted(items)]
    (out / "replay").mkdir(exist_ok=True)
    write_items(out / "replay" / "items.jsonl", rebuilt)
    per_query = {name: {"total_count": probes.get(p.get("q")),
                        "items": sum(1 for k in quer if name in quer[k])} for name, p in plans.items()}
    summary = {"ok": not mismatches, "mode": "replay", "responses": len(entries), "pages": pages,
               "items": len(rebuilt), "sha_mismatches": mismatches, "queries": per_query}
    orig = out / "items.jsonl"
    if orig.is_file():
        summary["matches_items_jsonl"] = orig.read_bytes() == (out / "replay" / "items.jsonl").read_bytes()
    atomic_write_json(out / "replay" / "summary.json", summary)
    return summary


def status(out) -> dict:
    out = Path(out)
    st = State(out, create=False)
    try:
        counts = st.counts()
        first = st.get_meta("first_start")
        runs = st.get_meta("runs", [])
    finally:
        st.close()
    prog_path, man_path = out / "progress.json", out / "manifest.json"
    prog = json.loads(prog_path.read_text(encoding="utf-8")) if prog_path.is_file() else None
    man = json.loads(man_path.read_text(encoding="utf-8")) if man_path.is_file() else {}
    return {"ok": True, "mode": "status", "progress": prog, "counts": counts,
            "snapshot_start_utc": first, "snapshot_end_utc": man.get("snapshot_end_utc"),
            "runs": len(runs), "last_run": runs[-1] if runs else None}
