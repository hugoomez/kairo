"""The snapshot's population statement and manifest.md, a human summary of manifest.json.

``population(state)`` → the inputs of a population statement, from state.sqlite:

    {"queries": [{"name", "q", "kind", "executed_at": {"started_at", "ended_at"},
                  "total_count", "sum_shards", "reachable", "items_fetched",
                  "coverage"}],                   coverage = items_fetched / total_count
     "shards": [{"query", "id", "q", "status", "total_count", "fetched", "done",
                 "coverage"}],                    coverage = fetched / total_count
     "truncated_shards": [{"query", "id", "q", "total_count", "reachable", "fetched",
                           "coverage"}],          only the first 1,000 are reachable
     "totals": {"queries", "shards", "truncated_shards", "total_count", "reachable",
                "items_fetched", "coverage"}}

`fetched` counts the results read from a shard's pages (an item found by two shards
counts in both); `items_fetched` counts distinct items per query. GitHub's counts are
estimates, so a coverage can exceed 1; it is reported as computed, never clipped.

``markdown(manifest)`` → manifest.md: counts, dates, API version and the queries (the
researcher's own search strings), never an item, path or repository name; ready to
adapt for a Methodology section. Written next to manifest.json at every finish, and
rebuilt without network by ``collect.py manifest --out DIR``.
"""
from __future__ import annotations

from .shard import CAP


def _cov(n, total):
    if not total:
        return None
    return round(n / total, 4)


def population(state) -> dict:
    qs = state.queries()
    execs = state.query_exec()
    per_q = dict(state.db.execute("SELECT query, COUNT(*) FROM item_queries GROUP BY query").fetchall())
    shards = state.shards()
    queries = []
    for q in qs:
        p = q.get("plan") or {}
        n = per_q.get(q["name"], 0)
        queries.append({"name": q["name"], "q": q["q"], "kind": q["kind"],
                        "executed_at": execs.get(q["name"], {"started_at": None, "ended_at": None}),
                        "total_count": p.get("total_count"), "sum_shards": p.get("sum_shards"),
                        "reachable": p.get("reachable"), "items_fetched": n,
                        "coverage": _cov(n, p.get("total_count"))})
    rows = [{"query": s["query"], "id": s["id"], "q": s["q"], "status": s["status"],
             "total_count": s["total_count"], "fetched": s["fetched"], "done": bool(s["done"]),
             "coverage": _cov(s["fetched"], s["total_count"])} for s in shards]
    trunc = [{"query": s["query"], "id": s["id"], "q": s["q"], "total_count": s["total_count"],
              "reachable": min(s["total_count"] or 0, CAP), "fetched": s["fetched"],
              "coverage": _cov(s["fetched"], s["total_count"])} for s in shards if s["status"] == "truncated"]
    total = sum(q["total_count"] or 0 for q in queries)
    fetched = sum(q["items_fetched"] for q in queries)
    return {"queries": queries, "shards": rows, "truncated_shards": trunc,
            "totals": {"queries": len(queries), "shards": len(rows), "truncated_shards": len(trunc),
                       "total_count": total, "reachable": sum(q["reachable"] or 0 for q in queries),
                       "items_fetched": fetched, "coverage": _cov(fetched, total)}}


def _pct(x) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def _md_cell(s) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def markdown(man: dict) -> str:
    pop = man.get("population") or {}
    tot = pop.get("totals") or {}
    api = man.get("api") or {}
    counts = man.get("counts") or {}
    hist = man.get("history") or {}
    caller = man.get("caller") or {}
    lines = [
        "# Collection snapshot",
        "",
        f"- Collection: {man.get('collection_id') or 'n/a'}",
        f"- Snapshot started: {man.get('snapshot_started_at') or 'n/a'} (UTC)",
        f"- Snapshot ended: {man.get('snapshot_ended_at') or 'not finished'} (UTC)",
        f"- Source: GitHub REST API ({api.get('base_url') or 'n/a'}), version header "
        f"{api.get('api_version_header') or 'n/a'}, selected {api.get('api_version_selected') or 'n/a'}, "
        f"{'authenticated' if api.get('authenticated') else 'unauthenticated'}",
        f"- Tool: {man.get('tool')} {man.get('tool_version')}; collection script commit "
        f"{(caller.get('git_commit') or 'n/a')[:12]}{' (uncommitted changes)' if caller.get('git_dirty') else ''}",
        f"- Runs: {len(man.get('runs') or [])}; raw responses archived: {(man.get('raw') or {}).get('responses', 0)}",
        "",
        "## Population",
        "",
        f"{tot.get('queries', 0)} queries, planned into {tot.get('shards', 0)} shards to work around the "
        f"search API's {CAP:,}-result cap; {tot.get('truncated_shards', 0)} shards still exceed it. GitHub "
        f"reported {tot.get('total_count', 0):,} results in total ({tot.get('reachable', 0):,} reachable); "
        f"{tot.get('items_fetched', 0):,} were collected (coverage {_pct(tot.get('coverage'))}).",
        "",
        "| Query | Search string | Kind | Run (UTC) | Reported | Shards | Truncated | Collected | Coverage |",
        "|---|---|---|---|---:|---:|---:|---:|---:|",
    ]
    nshards: dict = {}
    ntrunc: dict = {}
    for s in pop.get("shards") or []:
        nshards[s["query"]] = nshards.get(s["query"], 0) + 1
    for s in pop.get("truncated_shards") or []:
        ntrunc[s["query"]] = ntrunc.get(s["query"], 0) + 1
    for q in pop.get("queries") or []:
        ex = q.get("executed_at") or {}
        when = f"{ex.get('started_at') or 'n/a'} – {ex.get('ended_at') or 'n/a'}"
        lines.append(f"| {_md_cell(q['name'])} | `{_md_cell(q['q'])}` | {q['kind']} | {when} | "
                     f"{q.get('total_count') or 0:,} | {nshards.get(q['name'], 0)} | {ntrunc.get(q['name'], 0)} | "
                     f"{q.get('items_fetched', 0):,} | {_pct(q.get('coverage'))} |")
    trunc = pop.get("truncated_shards") or []
    if trunc:
        lines += ["", "### Truncated shards", "",
                  "Only the first 1,000 results of each of these shards can be read.", "",
                  "| Query | Shard | Reported | Reachable | Collected | Coverage |", "|---|---|---:|---:|---:|---:|"]
        for t in trunc:
            lines.append(f"| {_md_cell(t['query'])} | `{_md_cell(t['id'])}` | {t['total_count']:,} | "
                         f"{t['reachable']:,} | {t['fetched']:,} | {_pct(t['coverage'])} |")
    lines += ["", "## Counts", "",
              f"- Items (distinct repository, path, blob): {counts.get('items', 0):,}",
              f"- Result pages read: {counts.get('pages', 0):,}; count probes: {counts.get('probes', 0):,}",
              f"- Repositories with metadata fetched: {counts.get('repos', 0):,}"]
    if hist.get("repos"):
        lines += [f"- History: {hist['repos']} repositories cloned (blobless); {hist.get('versions', 0):,} "
                  f"file versions found, {hist.get('extracted', 0):,} read, {hist.get('blobs_kept', 0):,} encrypted "
                  f"blobs kept, {hist.get('unencrypted_versions', 0):,} versions that do not look encrypted "
                  f"(recorded by metadata only)"]
    lines += ["", "_Generated from manifest.json; no item content, path or repository name is included._", ""]
    return "\n".join(lines)
