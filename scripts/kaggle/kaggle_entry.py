#!/usr/bin/env python3
"""Kaggle kernel entry point for a Kairo transfer bundle (template).

The Kairo backend fills in __KAIRO_CONFIG_B64__ (base64 JSON: run_id, entry
argv, max_wallclock_s, output globs) and pushes this file as the kernel's
code. On Kaggle it:

1. finds the bundle in the attached private dataset (a folder holding
   MANIFEST.sha256, or a .tar.gz/.zip of one, which it extracts);
2. copies it to a scratch folder (datasets are read-only) and verifies every
   line of MANIFEST.sha256 — on any mismatch it runs NOTHING;
3. runs the frozen entry command with a hard wall-clock limit of its own
   (Kaggle's API cannot cancel a kernel; this limit is the brake), stdout and
   stderr to /kaggle/working/run.log;
4. copies the declared outputs to /kaggle/working/outputs/ and writes
   /kaggle/working/result.json with the exit code, timing, whether it timed
   out, and the sha256 of every output file.

It always exits 0 after writing result.json, so the kernel finishes
"complete" and its outputs can be downloaded; the outcome is in result.json.
Only files under /kaggle/working are retrievable, so the bundle copy stays in
the scratch folder. Standard library only.
"""

from __future__ import annotations

import base64
import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

CONFIG = json.loads(base64.b64decode("__KAIRO_CONFIG_B64__").decode("utf-8"))
SCHEMA = "kairo/kaggle-result@1"

INPUT = Path(os.environ.get("KAIRO_KAGGLE_INPUT", "/kaggle/input"))
WORKING = Path(os.environ.get("KAIRO_KAGGLE_WORKING", "/kaggle/working"))
SCRATCH = Path(os.environ.get("KAIRO_KAGGLE_TMP", "/tmp/kairo_bundle"))


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_extract(tf: tarfile.TarFile, dest: Path) -> None:
    """Extract refusing absolute paths, `..`, and links (Python < 3.11.4 has no
    extraction filter)."""
    try:
        tf.extractall(dest, filter="data")
        return
    except TypeError:
        pass
    for m in tf.getmembers():
        if m.name.startswith(("/", "\\")) or ".." in Path(m.name).parts or m.issym() or m.islnk():
            raise ValueError(f"unsafe archive member: {m.name}")
    tf.extractall(dest)


def find_bundle() -> Path | None:
    """The shallowest folder under INPUT holding MANIFEST.sha256; archives are
    extracted first (a dataset may keep an uploaded .tar.gz as a file)."""
    manifests = sorted(INPUT.rglob("MANIFEST.sha256"), key=lambda p: len(p.parts))
    if manifests:
        return manifests[0].parent
    extract = SCRATCH.with_name(SCRATCH.name + "_archive")
    for arc in sorted(INPUT.rglob("*")):
        if arc.name.endswith((".tar.gz", ".tgz")):
            with tarfile.open(arc) as tf:
                safe_extract(tf, extract)
        elif arc.suffix == ".zip":
            with zipfile.ZipFile(arc) as zf:
                zf.extractall(extract)
        else:
            continue
        found = sorted(extract.rglob("MANIFEST.sha256"), key=lambda p: len(p.parts))
        if found:
            return found[0].parent
    return None


def verify_manifest(root: Path) -> list[str]:
    problems = []
    for line in (root / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, rel = line.strip().partition("  ")
        f = root / rel
        if not f.is_file():
            problems.append(f"missing: {rel}")
        elif sha256(f) != digest:
            problems.append(f"sha256 mismatch: {rel}")
    return problems


def write_result(result: dict) -> None:
    WORKING.mkdir(parents=True, exist_ok=True)
    (WORKING / "result.json").write_text(json.dumps(result, indent=1), encoding="utf-8")


def main() -> int:
    result = {"schema": SCHEMA, "run_id": CONFIG["run_id"], "status": None, "manifest_ok": False,
              "exit_code": None, "timed_out": False, "started_at": None, "ended_at": None,
              "wall_s": None, "outputs": [], "problems": []}
    source = find_bundle()
    if source is None:
        result.update(status="bundle_not_found", problems=["no MANIFEST.sha256 in the attached dataset"])
        write_result(result)
        return 0
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH)
    shutil.copytree(source, SCRATCH)
    problems = verify_manifest(SCRATCH)
    if problems:
        result.update(status="manifest_mismatch", problems=problems)
        write_result(result)
        return 0
    result["manifest_ok"] = True

    argv = list(CONFIG["entry"])
    if argv and argv[0] in ("python", "python3"):
        argv[0] = sys.executable
    WORKING.mkdir(parents=True, exist_ok=True)
    result["started_at"] = now()
    t0 = time.monotonic()
    with (WORKING / "run.log").open("w", encoding="utf-8") as log:
        try:
            proc = subprocess.run(argv, cwd=SCRATCH, stdout=log, stderr=subprocess.STDOUT,
                                  timeout=int(CONFIG["max_wallclock_s"]))
            result["exit_code"] = proc.returncode
        except subprocess.TimeoutExpired:
            result["timed_out"] = True
            log.write(f"\n[kairo] wall-clock limit of {CONFIG['max_wallclock_s']} s reached: stopped\n")
    result["ended_at"] = now()
    result["wall_s"] = round(time.monotonic() - t0, 1)

    out_root = WORKING / "outputs"
    for f in sorted(p for p in SCRATCH.rglob("*") if p.is_file()):
        rel = f.relative_to(SCRATCH).as_posix()
        if any(fnmatch.fnmatch(rel, g) for g in CONFIG.get("outputs", [])):
            dest = out_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
            result["outputs"].append({"path": rel, "sha256": sha256(dest), "bytes": dest.stat().st_size})
    result["status"] = "timed_out" if result["timed_out"] else ("ok" if result["exit_code"] == 0 else "failed")
    write_result(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
