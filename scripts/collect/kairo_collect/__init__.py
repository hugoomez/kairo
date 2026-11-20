"""kairo_collect — reproducible, rate-limit-safe collection from the GitHub REST API.

A researcher's collection script (in their own code repository, started by the
Kairo app's "Recolecciones" runner with KAIRO_COLLECTION_ID and
KAIRO_COLLECTION_OUT) puts ``<plugin>/scripts/collect`` on ``sys.path`` and::

    from kairo_collect import Collection
    with Collection.from_env() as col:
        col.search([{"name": "a", "q": "term language:x"}])

Modules: token (where the token may live), github (the client: rate limits,
ETags, paging, archive), shard (the 1,000-result cap), state (sqlite, resume),
archive (raw bodies + index), progress (progress.json, the app's contract),
collection (the whole run, manifest, replay, status), manifest (population
statement, manifest.md), history (fetch_history: blobless clone, every version of
chosen paths, plaintext twins), repometa (repo_flags, demo_score), parsereport
(the parsers' report and skeleton()), sensitive (salted fingerprints, scrub). The
CLIs are scripts/collect/collect.py and scripts/collect/parse_summary.py.
Standard library only.
"""
TOOL = "kairo-collect"
TOOL_VERSION = "1.0.0"

from .collection import Collection, load_queries, replay, status  # noqa: E402
from .github import Client, GitHubError, RateLimitError  # noqa: E402
from .token import TokenRefused, load_token  # noqa: E402

__all__ = ["TOOL", "TOOL_VERSION", "Collection", "Client", "GitHubError", "RateLimitError",
           "TokenRefused", "load_queries", "load_token", "replay", "status"]
