#!/usr/bin/env python3
"""Reproducible, rate-limit-safe GitHub search collection (kairo_collect's CLI).

    collect.py count  --queries q.json [--out DIR]                 totals + shard plan, no items
    collect.py search --queries q.json [--out DIR] [--max-items N] every page of every shard, resumable
    collect.py replay [--out DIR]                                  rebuild items from raw/, no network
    collect.py status [--out DIR]                                  progress + counts
    collect.py manifest [--out DIR]                                rebuild manifest.md from manifest.json

--out defaults to KAIRO_COLLECTION_OUT; --collection-id to KAIRO_COLLECTION_ID.
The token is read from KAIRO_GITHUB_TOKEN_FILE (default ~/.kairo/github-token)
and refused inside a git working tree, a vault or a synced cloud folder.
`--redact-raw` passes the raw API bodies through scripts/security/redact.py
too (off by default: the researcher decides).

q.json: [{"name": "a", "q": "term language:x", "kind": "code",
          "split": {"extension": ["py", "c"]}}]

JSON on stdout. Exit 0 ok · 3 refused (token location / content) · 1 error.
See kairo_collect/progress.py for the progress.json contract and
kairo_collect/collection.py for the output folder.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kairo_collect import GitHubError, TokenRefused, load_token  # noqa: E402
from kairo_collect.collection import Collection, replay, status, write_manifest_md  # noqa: E402

EXIT_OK, EXIT_ERROR, EXIT_REFUSED = 0, 1, 3


def _emit(obj) -> None:
    sys.stdout.buffer.write((json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="collect.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("count", "search", "replay", "status", "manifest"):
        p = sub.add_parser(name)
        p.add_argument("--out", default=os.environ.get("KAIRO_COLLECTION_OUT"))
        if name in ("count", "search"):
            p.add_argument("--queries", required=True)
            p.add_argument("--collection-id", default=os.environ.get("KAIRO_COLLECTION_ID"))
            p.add_argument("--api-version", default=None)
            p.add_argument("--redact-raw", action="store_true")
        if name == "search":
            p.add_argument("--max-items", type=int, default=None)
    return ap


def main(argv: list[str] | None = None, env: dict | None = None, **inject) -> int:
    """`inject` (tests): transport, clock, sleep, rng, caller_repo, … for Collection."""
    args = parser().parse_args(argv)
    env = os.environ if env is None else env
    if not args.out:
        _emit({"ok": False, "error": "falta --out (o KAIRO_COLLECTION_OUT)"})
        return EXIT_ERROR
    try:
        if args.cmd == "replay":
            _emit(replay(args.out))
            return EXIT_OK
        if args.cmd == "status":
            _emit(status(args.out))
            return EXIT_OK
        if args.cmd == "manifest":
            _emit(write_manifest_md(args.out))
            return EXIT_OK
        token = load_token(env)
        with Collection(args.out, token=token, collection_id=args.collection_id,
                        api_version=args.api_version, redact_raw=args.redact_raw, **inject) as col:
            if args.cmd == "count":
                res = col.count(args.queries)
            else:
                res = col.search(args.queries, max_items=args.max_items)
        _emit(res)
        return EXIT_OK
    except TokenRefused as e:
        _emit({"ok": False, "refused": True, "error": f"token rechazado: {e}"})
        return EXIT_REFUSED
    except KeyboardInterrupt:
        _emit({"ok": False, "error": "interrumpida; al relanzarla sigue donde se quedó"})
        return EXIT_ERROR
    except (GitHubError, ValueError, FileNotFoundError, OSError) as e:
        _emit({"ok": False, "error": str(e)[:500]})
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
