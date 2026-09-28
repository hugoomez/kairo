#!/usr/bin/env python3
"""The Kaggle run contract between run-experiment and the Kairo backend.

A prepared run is a folder::

    <run_dir>/bundle.json   what to run and how it is traced (kairo/kaggle-bundle@1)
    <run_dir>/bundle/       the transfer bundle, with MANIFEST.sha256

Subcommands (JSON on stdout)::

    bundle_contract.py check   --run-dir D      validate bundle.json, verify the
                                                manifest, run check_bundle.py
    bundle_contract.py pack    --run-dir D --out bundle.tar.gz
    bundle_contract.py outputs --out-dir O      verify a downloaded result:
                                                result.json + every output's sha256

``check`` is what both sides run: the agent before handing the run over, and
the backend again before showing it for approval (the backend never trusts
the agent's word that the bundle is clean). ``ok`` is true only if the
description is valid, every file is in the manifest with the right hash, and
``check_bundle.py`` exits 0 (clean).

Exit codes: 0 ok · 3 refused / not ok · 1 error. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = "kairo/kaggle-bundle@1"
ACCELERATORS = ("NvidiaTeslaT4", "NvidiaL4", "NvidiaTeslaP100", "TpuV5E8", "none")
ROLES = ("confirmatory", "exploratory")
_EXP = re.compile(r"^E-\d{4}(-[a-z0-9][a-z0-9-]*)?$")
_RUN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,80}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
MAX_WALLCLOCK_S = 12 * 3600


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_description(b: dict) -> list[str]:
    p = []
    req = lambda k, ok, what: p.append(f"{k}: {what}") if not ok(b.get(k)) else None  # noqa: E731
    req("schema", lambda v: v == SCHEMA, f"must be {SCHEMA}")
    req("experiment", lambda v: isinstance(v, str) and bool(_EXP.match(v)), "an experiment id E-XXXX")
    req("hypothesis", lambda v: isinstance(v, str) and bool(re.match(r"^H-\d{4}$", v)), "a hypothesis id H-XXXX")
    req("run_id", lambda v: isinstance(v, str) and bool(_RUN.match(v)), "a run id (letters, digits, . _ -)")
    req("role", lambda v: v in ROLES, f"one of {ROLES}")
    req("rung", lambda v: v is None or (isinstance(v, int) and 0 <= v <= 3), "null or 0-3")
    req("attempt", lambda v: isinstance(v, int) and v >= 1, "an integer >= 1")
    req("seeds", lambda v: isinstance(v, list) and all(isinstance(s, int) for s in v), "a list of integers")
    req("config_hash", lambda v: isinstance(v, str) and (v == "unknown" or bool(_HEX64.match(v))), "sha256 hex or unknown")
    req("entry", lambda v: isinstance(v, list) and v and all(isinstance(a, str) and a for a in v), "a non-empty argv list")
    req("accelerator", lambda v: v in ACCELERATORS, f"one of {ACCELERATORS}")
    req("enable_internet", lambda v: isinstance(v, bool), "true or false (frozen in the preregistration)")
    req("max_wallclock_s", lambda v: isinstance(v, int) and 60 <= v <= MAX_WALLCLOCK_S, f"60-{MAX_WALLCLOCK_S} seconds")
    req("estimated_gpu_h", lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0, "a number >= 0")
    req("outputs", lambda v: isinstance(v, list) and v and all(isinstance(g, str) and g for g in v), "a non-empty list of globs")
    for k in ("cell", "retry_of", "notes"):
        req(k, lambda v: v is None or isinstance(v, str), "a string or null")
    req("plan_index", lambda v: v is None or (isinstance(v, int) and v >= 0), "null or an integer >= 0")
    if isinstance(b.get("entry"), list) and b["entry"] and b["entry"][0] not in ("python", "python3", "bash", "sh"):
        p.append("entry: must start with python, python3, bash or sh (run from the bundle root)")
    return p


def manifest_problems(bundle: Path) -> tuple[list[str], list[dict]]:
    mf = bundle / "MANIFEST.sha256"
    if not mf.is_file():
        return ["MANIFEST.sha256 missing"], []
    listed: dict[str, str] = {}
    problems = []
    for line in mf.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, rel = line.strip().partition("  ")
        if not _HEX64.match(digest) or not rel:
            problems.append(f"manifest line not '<sha256>  <path>': {line[:80]}")
            continue
        listed[rel] = digest
    files = []
    for f in sorted(x for x in bundle.rglob("*") if x.is_file()):
        rel = f.relative_to(bundle).as_posix()
        if rel == "MANIFEST.sha256":
            continue
        files.append({"path": rel, "bytes": f.stat().st_size})
        if rel not in listed:
            problems.append(f"not in the manifest: {rel}")
        elif sha256(f) != listed.pop(rel):
            problems.append(f"sha256 mismatch: {rel}")
    problems += [f"listed but missing: {rel}" for rel in listed]
    return problems, files


def check(run_dir: Path) -> dict:
    out: dict = {"ok": False, "problems": [], "bundle": None, "files": [], "total_bytes": 0,
                 "manifest_sha256": None, "check_bundle": None}
    desc = run_dir / "bundle.json"
    bundle = run_dir / "bundle"
    if not desc.is_file() or not bundle.is_dir():
        out["problems"].append("expected <run_dir>/bundle.json and <run_dir>/bundle/")
        return out
    try:
        b = json.loads(desc.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        out["problems"].append(f"bundle.json is not JSON: {exc}")
        return out
    out["bundle"] = b
    out["problems"] += validate_description(b if isinstance(b, dict) else {})
    probs, files = manifest_problems(bundle)
    out["problems"] += probs
    out["files"] = files
    out["total_bytes"] = sum(f["bytes"] for f in files)
    if (bundle / "MANIFEST.sha256").is_file():
        out["manifest_sha256"] = sha256(bundle / "MANIFEST.sha256")
    res = subprocess.run([sys.executable, str(HERE.parent / "security" / "check_bundle.py"), str(bundle)],
                         capture_output=True, text=True, encoding="utf-8")
    try:
        cb = json.loads(res.stdout)
    except json.JSONDecodeError:
        cb = {"status": "error", "findings": [], "detail": (res.stderr or res.stdout)[:500]}
    cb["exit"] = res.returncode
    out["check_bundle"] = cb
    if res.returncode != 0:
        out["problems"].append(f"check_bundle.py: {cb.get('status', 'error')} (exit {res.returncode}) — do not upload")
    out["ok"] = not out["problems"]
    return out


def pack(run_dir: Path, dest: Path) -> dict:
    bundle = run_dir / "bundle"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(dest, "w:gz") as tf:
        for f in sorted(x for x in bundle.rglob("*") if x.is_file()):
            tf.add(f, arcname=f"bundle/{f.relative_to(bundle).as_posix()}", recursive=False)
    return {"out": str(dest), "sha256": sha256(dest), "bytes": dest.stat().st_size}


def outputs(out_dir: Path) -> dict:
    res: dict = {"ok": False, "result": None, "outputs": [], "problems": []}
    rj = out_dir / "result.json"
    if not rj.is_file():
        res["problems"].append("result.json missing from the kernel output")
        return res
    r = json.loads(rj.read_text(encoding="utf-8"))
    res["result"] = r
    for o in r.get("outputs", []):
        f = out_dir / "outputs" / o["path"]
        ok = f.is_file() and sha256(f) == o["sha256"]
        res["outputs"].append({**o, "verified": ok})
        if not ok:
            res["problems"].append(f"output does not match its recorded sha256: {o['path']}")
    if not r.get("manifest_ok"):
        res["problems"].append(f"the bundle did not verify on Kaggle ({r.get('status')}): nothing ran")
    res["ok"] = not res["problems"]
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--run-dir", required=True, type=Path)
    k = sub.add_parser("pack")
    k.add_argument("--run-dir", required=True, type=Path)
    k.add_argument("--out", required=True, type=Path)
    o = sub.add_parser("outputs")
    o.add_argument("--out-dir", required=True, type=Path)
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args.cmd == "check":
            r = check(args.run_dir)
        elif args.cmd == "pack":
            r = check(args.run_dir)
            if r["ok"]:
                r = {"ok": True, **pack(args.run_dir, args.out)}
        else:
            r = outputs(args.out_dir)
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r.get("ok") else 3
    except (OSError, json.JSONDecodeError, tarfile.TarError) as exc:
        print(json.dumps({"ok": False, "problems": [f"error: {exc}"]}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
