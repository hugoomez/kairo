"""Resumable state: <out>/state.sqlite.

Tables
    meta(key, value)                         first start, runs, collection id
    queries(name, q, kind, spec, plan, ...)  each named query and its saved shard plan
    probes(kind, q, total_count, incomplete) every count already asked (never asked twice)
    shards(query, id, q, total_count, status, next_url, pages_done, fetched, fetched_total,
           incomplete, done)
    pages(query, shard, page, sha256, n_items)
    items(repo, path, sha, name, html_url, first_query, first_shard)   key (repo, path, sha)
    item_queries(repo, path, sha, query)
    etags(url, etag, sha256)                 conditional-request cache
    repos(full_name, facts)                  repository metadata already fetched
    query_exec(name, started_at, ended_at)   when each query was first probed / last paged
    history_repos(repo, clone_url, dir, paths, status, message, bytes, cloned_at, updated_at)
    history(repo, path, commit_sha, author_date, blob_sha ('' = deleted), change, size,
            extracted, kept, encrypted, markers, keys_fp)   history.py; never content

Every page is committed with its items and the shard's next URL in one
transaction, so an interruption loses at most the page in flight and a rerun
continues from the next URL; inserting an item twice is a no-op.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS queries(name TEXT PRIMARY KEY, q TEXT NOT NULL, kind TEXT NOT NULL,
    spec TEXT NOT NULL, plan TEXT, ord INTEGER);
CREATE TABLE IF NOT EXISTS probes(kind TEXT, q TEXT, total_count INTEGER, incomplete INTEGER,
    PRIMARY KEY(kind, q));
CREATE TABLE IF NOT EXISTS shards(query TEXT, id TEXT, q TEXT, total_count INTEGER, status TEXT,
    next_url TEXT, pages_done INTEGER DEFAULT 0, fetched INTEGER DEFAULT 0, fetched_total INTEGER,
    incomplete INTEGER DEFAULT 0, done INTEGER DEFAULT 0, ord INTEGER, PRIMARY KEY(query, id));
CREATE TABLE IF NOT EXISTS pages(query TEXT, shard TEXT, page INTEGER, sha256 TEXT, n_items INTEGER,
    PRIMARY KEY(query, shard, page));
CREATE TABLE IF NOT EXISTS items(repo TEXT, path TEXT, sha TEXT, name TEXT, html_url TEXT,
    first_query TEXT, first_shard TEXT, PRIMARY KEY(repo, path, sha));
CREATE TABLE IF NOT EXISTS item_queries(repo TEXT, path TEXT, sha TEXT, query TEXT,
    PRIMARY KEY(repo, path, sha, query));
CREATE TABLE IF NOT EXISTS etags(url TEXT PRIMARY KEY, etag TEXT, sha256 TEXT);
CREATE TABLE IF NOT EXISTS repos(full_name TEXT PRIMARY KEY, facts TEXT);
CREATE TABLE IF NOT EXISTS query_exec(name TEXT PRIMARY KEY, started_at TEXT, ended_at TEXT);
CREATE TABLE IF NOT EXISTS history_repos(repo TEXT PRIMARY KEY, clone_url TEXT, dir TEXT, paths TEXT,
    status TEXT, message TEXT, bytes INTEGER, cloned_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS history(repo TEXT, path TEXT, commit_sha TEXT, author_date TEXT, blob_sha TEXT,
    change TEXT, size INTEGER, extracted INTEGER DEFAULT 0, kept INTEGER DEFAULT 0, encrypted INTEGER,
    markers TEXT, keys_fp TEXT, PRIMARY KEY(repo, path, commit_sha, blob_sha));
"""
HISTORY_COLS = ("repo", "path", "commit_sha", "author_date", "blob_sha", "change", "size", "extracted", "kept",
                "encrypted", "markers", "keys_fp")


class State:
    def __init__(self, out: Path, create: bool = True):
        path = Path(out) / "state.sqlite"
        if not create and not path.is_file():
            raise FileNotFoundError(f"no hay recolección en {out} (falta state.sqlite)")
        Path(out).mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # meta
    def get_meta(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key: str, value) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, json.dumps(value)))

    # queries
    def register_query(self, spec: dict, ord_: int) -> None:
        row = self.db.execute("SELECT q, kind, spec FROM queries WHERE name=?", (spec["name"],)).fetchone()
        spec_json = json.dumps(spec, sort_keys=True)
        if row:
            if row[2] != spec_json:
                raise ValueError(f"la consulta '{spec['name']}' ya existe en esta recolección con otra "
                                 "definición; usa otro nombre u otra carpeta --out")
            return
        with self.db:
            self.db.execute("INSERT INTO queries(name,q,kind,spec,ord) VALUES(?,?,?,?,?)",
                            (spec["name"], spec["q"], spec["kind"], spec_json, ord_))

    def query_plan(self, name: str) -> dict | None:
        row = self.db.execute("SELECT plan FROM queries WHERE name=?", (name,)).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def save_plan(self, name: str, plan: dict) -> None:
        with self.db:
            self.db.execute("UPDATE queries SET plan=? WHERE name=?", (json.dumps(plan), name))
            for i, s in enumerate(plan["shards"]):
                self.db.execute(
                    "INSERT OR IGNORE INTO shards(query,id,q,total_count,status,done,ord) VALUES(?,?,?,?,?,?,?)",
                    (name, s["id"], s["q"], s["total_count"], s["status"], int(s["total_count"] == 0), i))

    def queries(self) -> list[dict]:
        rows = self.db.execute("SELECT spec, plan FROM queries ORDER BY ord").fetchall()
        return [{**json.loads(s), "plan": json.loads(p) if p else None} for s, p in rows]

    # probes
    def probe(self, kind: str, q: str):
        return self.db.execute("SELECT total_count, incomplete FROM probes WHERE kind=? AND q=?",
                               (kind, q)).fetchone()

    def save_probe(self, kind: str, q: str, total: int, incomplete: bool) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO probes VALUES(?,?,?,?)", (kind, q, total, int(incomplete)))

    # shards / pages / items
    def shards(self, query: str | None = None) -> list[dict]:
        sql = ("SELECT query,id,q,total_count,status,next_url,pages_done,fetched,fetched_total,incomplete,done "
               "FROM shards")
        args: tuple = ()
        if query is not None:
            sql += " WHERE query=?"
            args = (query,)
        sql += " ORDER BY (SELECT ord FROM queries WHERE name=shards.query), ord"
        cols = ("query", "id", "q", "total_count", "status", "next_url", "pages_done", "fetched",
                "fetched_total", "incomplete", "done")
        return [dict(zip(cols, r)) for r in self.db.execute(sql, args).fetchall()]

    def save_page(self, query: str, shard: str, page: int, sha: str, items: list[dict],
                  next_url: str | None, total: int | None, incomplete: bool) -> int:
        """One page and its items, atomically; returns how many items were new."""
        new = 0
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO pages VALUES(?,?,?,?,?)", (query, shard, page, sha, len(items)))
            for it in items:
                cur = self.db.execute("INSERT OR IGNORE INTO items VALUES(?,?,?,?,?,?,?)",
                                      (it["repo"], it["path"], it["sha"], it.get("name"), it.get("html_url"),
                                       query, shard))
                new += cur.rowcount
                self.db.execute("INSERT OR IGNORE INTO item_queries VALUES(?,?,?,?)",
                                (it["repo"], it["path"], it["sha"], query))
            self.db.execute(
                "UPDATE shards SET next_url=?, pages_done=?, fetched=fetched+?, "
                "fetched_total=COALESCE(fetched_total, ?), incomplete=MAX(incomplete, ?), done=? "
                "WHERE query=? AND id=?",
                (next_url, page, len(items), total, int(incomplete), int(next_url is None), query, shard))
        return new

    def count_items(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM items").fetchone()[0]

    def items(self) -> list[dict]:
        rows = self.db.execute("SELECT repo,path,sha,name,html_url FROM items ORDER BY repo,path,sha").fetchall()
        qs: dict = {}
        for r, p, s, q in self.db.execute("SELECT repo,path,sha,query FROM item_queries"):
            qs.setdefault((r, p, s), []).append(q)
        return [{"repo": r, "path": p, "sha": s, "name": n, "html_url": u, "queries": sorted(qs.get((r, p, s), []))}
                for r, p, s, n, u in rows]

    def counts(self) -> dict:
        one = lambda sql: self.db.execute(sql).fetchone()[0]  # noqa: E731
        by_status = dict(self.db.execute("SELECT status, COUNT(*) FROM shards GROUP BY status").fetchall())
        return {"queries": one("SELECT COUNT(*) FROM queries"),
                "queries_planned": one("SELECT COUNT(*) FROM queries WHERE plan IS NOT NULL"),
                "shards": one("SELECT COUNT(*) FROM shards"), "shards_by_status": by_status,
                "shards_done": one("SELECT COUNT(*) FROM shards WHERE done=1"),
                "pages": one("SELECT COUNT(*) FROM pages"), "items": one("SELECT COUNT(*) FROM items"),
                "probes": one("SELECT COUNT(*) FROM probes"), "repos": one("SELECT COUNT(*) FROM repos")}

    # conditional-request cache (Client's `etags` interface: get(url) / put(url, etag, sha))
    def get(self, url: str):
        row = self.db.execute("SELECT etag, sha256 FROM etags WHERE url=?", (url,)).fetchone()
        return tuple(row) if row else None

    def put(self, url: str, etag: str, sha: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO etags VALUES(?,?,?)", (url, etag, sha))

    # repository metadata
    def repo(self, full_name: str):
        row = self.db.execute("SELECT facts FROM repos WHERE full_name=?", (full_name,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_repo(self, full_name: str, facts) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO repos VALUES(?,?)", (full_name, json.dumps(facts)))

    # when each query ran (first probe / last page), for the manifest
    def touch_query(self, name: str, ts: str) -> None:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO query_exec VALUES(?,?,?)", (name, ts, ts))
            self.db.execute("UPDATE query_exec SET ended_at=? WHERE name=?", (ts, name))

    def query_exec(self) -> dict:
        return {n: {"started_at": s, "ended_at": e}
                for n, s, e in self.db.execute("SELECT name, started_at, ended_at FROM query_exec")}

    # history (history.py)
    def history_repo(self, repo: str) -> dict | None:
        row = self.db.execute("SELECT repo, clone_url, dir, paths, status, message, bytes, cloned_at, updated_at "
                              "FROM history_repos WHERE repo=?", (repo,)).fetchone()
        if not row:
            return None
        d = dict(zip(("repo", "clone_url", "dir", "paths", "status", "message", "bytes", "cloned_at",
                      "updated_at"), row))
        d["paths"] = json.loads(d["paths"] or "[]")
        return d

    def save_history_repo(self, repo: str, **fields) -> None:
        cur = self.history_repo(repo) or {"repo": repo, "paths": []}
        cur.update(fields)
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO history_repos VALUES(?,?,?,?,?,?,?,?,?)",
                            (repo, cur.get("clone_url"), cur.get("dir"), json.dumps(sorted(set(cur["paths"]))),
                             cur.get("status"), cur.get("message"), cur.get("bytes"), cur.get("cloned_at"),
                             cur.get("updated_at")))

    def add_history(self, repo: str, rows: list[dict]) -> int:
        new = 0
        with self.db:
            for r in rows:
                cur = self.db.execute(
                    "INSERT OR IGNORE INTO history(repo,path,commit_sha,author_date,blob_sha,change) "
                    "VALUES(?,?,?,?,?,?)", (repo, r["path"], r["commit"], r["author_date"], r["blob_sha"], r["change"]))
                new += cur.rowcount
        return new

    def history(self, repo: str, path: str | None = None, pending: bool = False) -> list[dict]:
        sql = f"SELECT {','.join(HISTORY_COLS)} FROM history WHERE repo=?"
        args: list = [repo]
        if path is not None:
            sql += " AND path=?"
            args.append(path)
        if pending:
            sql += " AND extracted=0 AND blob_sha!=''"
        sql += " ORDER BY path, author_date, commit_sha"
        out = []
        for row in self.db.execute(sql, args):
            d = dict(zip(HISTORY_COLS, row))
            d["markers"] = json.loads(d["markers"]) if d["markers"] else None
            out.append(d)
        return out

    def blob_done(self, repo: str, blob_sha: str) -> dict | None:
        row = self.db.execute("SELECT size, kept, encrypted, markers, keys_fp FROM history WHERE repo=? AND "
                              "blob_sha=? AND extracted=1 LIMIT 1", (repo, blob_sha)).fetchone()
        return dict(zip(("size", "kept", "encrypted", "markers", "keys_fp"), row)) if row else None

    def mark_blob(self, repo: str, blob_sha: str, size: int, kept: bool, encrypted: bool, markers, keys_fp) -> None:
        with self.db:
            self.db.execute("UPDATE history SET extracted=1, size=?, kept=?, encrypted=?, markers=?, keys_fp=? "
                            "WHERE repo=? AND blob_sha=?",
                            (size, int(kept), int(encrypted),
                             markers if isinstance(markers, str) or markers is None else json.dumps(markers,
                                                                                                     sort_keys=True),
                             keys_fp, repo, blob_sha))

    def history_counts(self) -> dict:
        one = lambda sql: self.db.execute(sql).fetchone()[0]  # noqa: E731
        by_status = dict(self.db.execute("SELECT status, COUNT(*) FROM history_repos GROUP BY status").fetchall())
        return {"repos": one("SELECT COUNT(*) FROM history_repos"), "repos_by_status": by_status,
                "versions": one("SELECT COUNT(*) FROM history WHERE blob_sha!=''"),
                "deletions": one("SELECT COUNT(*) FROM history WHERE blob_sha=''"),
                "extracted": one("SELECT COUNT(*) FROM history WHERE extracted=1"),
                "blobs_kept": one("SELECT COUNT(DISTINCT repo || blob_sha) FROM history WHERE kept=1"),
                "unencrypted_versions": one("SELECT COUNT(*) FROM history WHERE extracted=1 AND encrypted=0")}
