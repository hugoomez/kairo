#!/usr/bin/env python3
"""Validation hash of a frozen tool folder (`Tools/P-XXXX/<method>/`).

`paper-to-tool` step 7 computes it when a tool is frozen; `preregister-experiment`
records it in a preregistration's environment manifest; `run-experiment`
pre-flight re-verifies it -- the same way dependency and dataset hashes are
frozen and re-checked.

`compute` hashes every file in the folder except `TOOL.md` (the manifest that
*carries* the hash, and whose `## Enmiendas` stays append-only after freeze)
and `MANIFEST.sha256` itself, writes one `<sha256>  <relative/posix/path>` line
per file, sorted by path, to `MANIFEST.sha256`, and prints

    validation_hash = sha256(MANIFEST.sha256 bytes)

Bytes are hashed as stored on disk. The vault's `Tools/.gitattributes` must
say `* -text` so git never rewrites line endings under a frozen tool
(`paper-to-tool` creates it); an autocrlf checkout would otherwise change the
hash on another machine.

`verify` recomputes everything and compares against --expected (the hash the
preregistration froze) and against the stored MANIFEST.sha256, naming every
changed, missing or added file.

Usage:
    python tool_hash.py compute <tool_dir> [--json]
    python tool_hash.py verify  <tool_dir> --expected <hash> [--json]

Exit codes: 0 ok / match, 1 mismatch, 2 invalid input.
Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

__version__ = "1.0.0"
EXCLUDED = {"TOOL.md", "MANIFEST.sha256"}
SKIP_DIRS = {"__pycache__", ".pytest_cache"}


def file_sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect(tool_dir: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(tool_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(tool_dir)
        if rel.as_posix() in EXCLUDED or any(part in SKIP_DIRS for part in rel.parts):
            continue
        out[rel.as_posix()] = file_sha256(p)
    return out


def manifest_text(files: dict[str, str]) -> str:
    return "".join(f"{h}  {path}\n" for path, h in sorted(files.items()))


def validation_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_manifest(text: str) -> dict[str, str]:
    out = {}
    for ln in text.splitlines():
        if ln.strip():
            h, path = ln.split("  ", 1)
            out[path] = h
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compute")
    c.add_argument("tool_dir", type=Path)
    c.add_argument("--json", action="store_true")
    v = sub.add_parser("verify")
    v.add_argument("tool_dir", type=Path)
    v.add_argument("--expected", required=True)
    v.add_argument("--json", action="store_true")
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args(argv)

    if not a.tool_dir.is_dir():
        print(f"error: {a.tool_dir} is not a directory", file=sys.stderr)
        return 2
    files = collect(a.tool_dir)
    if not files:
        print("error: no files to hash (only TOOL.md?)", file=sys.stderr)
        return 2
    text = manifest_text(files)
    h = validation_hash(text)

    if a.cmd == "compute":
        (a.tool_dir / "MANIFEST.sha256").write_bytes(text.encode("utf-8"))
        out = {"validation_hash": h, "n_files": len(files), "manifest": "MANIFEST.sha256"}
        print(json.dumps(out, indent=2) if a.json else h)
        return 0

    stored_path = a.tool_dir / "MANIFEST.sha256"
    stored = parse_manifest(stored_path.read_text(encoding="utf-8")) if stored_path.exists() else {}
    changed = sorted(p for p in files if p in stored and stored[p] != files[p])
    added = sorted(p for p in files if p not in stored)
    missing = sorted(p for p in stored if p not in files)
    ok = (h == a.expected)
    out = {
        "match": ok,
        "expected": a.expected,
        "actual": h,
        "changed_files": changed,
        "added_files": added,
        "missing_files": missing,
        "stored_manifest_present": stored_path.exists(),
    }
    if a.json:
        print(json.dumps(out, indent=2))
    else:
        print(("MATCH " if ok else "MISMATCH ") + f"expected={a.expected} actual={h}")
        for label, lst in (("changed", changed), ("added", added), ("missing", missing)):
            for p in lst:
                print(f"  {label}: {p}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
