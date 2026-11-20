"""Shard plan for GitHub search's 1,000-result cap.

A search returns at most 1,000 results however many it counts
(https://docs.github.com/en/rest/search/search). A query whose total_count is
above the cap is split into shards that together cover it:

1. by ``size:`` byte ranges, binary split (``size:lo..hi``, the range syntax of
   https://docs.github.com/en/search-github/getting-started-with-searching-on-github/understanding-the-search-syntax).
   Legacy code search only indexes files under 384 KB
   (https://docs.github.com/en/search-github/searching-on-github/searching-code),
   so code ranges cover 0..393215;
2. when one size (lo == hi) still holds more than the cap, by the optional
   splitters, in order: each value of a qualifier (``extension:py``,
   ``path:src``, ``filename:x``, ``language:x``) plus the remainder that
   excludes them all (``-extension:py -extension:c``), so the parts are a
   partition of their parent;
3. what still exceeds the cap is a ``truncated`` shard, kept with its count:
   only its first 1,000 results can be read, and the plan says so.

The plan is deterministic: the same counts give the same shards in the same
order, with ids built from their qualifiers. GitHub's counts are estimates, so
``sum_shards`` (the shards' counts added up) is reported beside the query's own
``total_count`` and never forced to match.
"""
from __future__ import annotations

CAP = 1000
CODE_SIZE_MAX = 384 * 1024 - 1
REPO_SIZE_MAX = 100_000_000          # repositories: size: is in KB
SPLIT_QUALIFIERS = ("extension", "path", "filename", "language")
MAX_DEPTH = 40


def _value(v: str) -> str:
    v = str(v)
    return f'"{v}"' if any(c.isspace() for c in v) else v


def compose(base: str, quals: list[str]) -> str:
    return " ".join([base.strip(), *quals]).strip()


def _sid(quals: list[str]) -> str:
    return "|".join(quals) if quals else "all"


def plan_query(base_q: str, count, *, size_split: bool = True, size_max: int = CODE_SIZE_MAX,
               splitters: list[tuple[str, list[str]]] | None = None, cap: int = CAP) -> dict:
    """`count(q) -> int` is the probe (one search request with per_page=1).
    Returns {"q", "total_count", "shards": [{"id","q","total_count","status"}],
    "sum_shards", "reachable", "truncated"}."""
    splitters = [(k, [str(v) for v in vs]) for k, vs in (splitters or []) if vs]
    for k, _ in splitters:
        if k not in SPLIT_QUALIFIERS:
            raise ValueError(f"calificador de partición no admitido: {k} (válidos: {', '.join(SPLIT_QUALIFIERS)})")
    shards: list[dict] = []

    def add(quals: list[str], n: int, status: str | None = None) -> None:
        st = status or ("empty" if n == 0 else "ok")
        shards.append({"id": _sid(quals), "q": compose(base_q, quals), "total_count": n, "status": st})

    def by_other(quals: list[str], n: int, idx: int) -> None:
        if n <= cap:
            add(quals, n)
            return
        if idx >= len(splitters):
            add(quals, n, "truncated")
            return
        name, values = splitters[idx]
        parts = [[*quals, f"{name}:{_value(v)}"] for v in values]
        parts.append([*quals, *(f"-{name}:{_value(v)}" for v in values)])
        for p in parts:
            by_other(p, count(compose(base_q, p)), idx + 1)

    def by_size(lo: int, hi: int, n: int, depth: int) -> None:
        quals = [f"size:{lo}..{hi}"]
        if n <= cap:
            add(quals, n)
        elif lo < hi and depth < MAX_DEPTH:
            mid = (lo + hi) // 2
            for a, b in ((lo, mid), (mid + 1, hi)):
                by_size(a, b, count(compose(base_q, [f"size:{a}..{b}"])), depth + 1)
        else:
            by_other(quals, n, 0)

    total = count(base_q)
    if total <= cap:
        add([], total)
    elif size_split:
        by_size(0, size_max, count(compose(base_q, [f"size:0..{size_max}"])), 0)
    else:
        by_other([], total, 0)
    trunc = [s for s in shards if s["status"] == "truncated"]
    return {"q": base_q, "total_count": total, "shards": shards,
            "sum_shards": sum(s["total_count"] for s in shards),
            "reachable": sum(min(s["total_count"], cap) for s in shards),
            "truncated": [{"id": s["id"], "q": s["q"], "total_count": s["total_count"]} for s in trunc]}
