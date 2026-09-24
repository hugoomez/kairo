"""Frozen evaluation harness for evolve-program (the OpenEvolve evaluation file).

OpenEvolve calls ``evaluate(program_path)`` for every candidate. This harness,
not the candidate, owns everything that decides the score:

1. **Tamper check before and after** — sha256 of the frozen task file, this
   harness, the candidate runner and the split data, against the lock written
   at freeze time. Any mismatch -> score 0, ``tampered`` metric, and an alert
   file that makes ``evolve_run.py`` abort the run as crítico.
2. **Separate process + timeout** — the candidate runs in a fresh temp dir via
   ``cand_runner.py`` under a scrubbed environment (no API keys, no lock path,
   no held-out path), with the frozen per-evaluation timeout. It receives only
   the split's *inputs* on stdin and returns outputs on stdout.
3. **Shape and bounds** — the output must be a list of the right length whose
   items pass the frozen task's ``check_output``; anything else scores 0.
4. **Scoring in the harness** — the frozen task's ``score(outputs, targets)``
   runs here, with targets the candidate never receives. A candidate cannot
   print its own score. The result must be a finite number in [0, 1].

The harness only ever scores the ``train`` split during evolution. The
held-out split's path is never in the environment evolution runs in; only
``evolve_run.py rescore`` passes it, after evolution has stopped.

Environment (set by evolve_run.py): KAIRO_EVOLVE_LOCK = path of the lock JSON.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARKER = "@@KAIRO_OUTPUT@@"


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def load_lock(path: str | None = None) -> dict:
    return json.loads(Path(path or os.environ["KAIRO_EVOLVE_LOCK"]).read_text(encoding="utf-8"))


def verify_hashes(lock: dict, files_key: str = "files") -> list[str]:
    bad = []
    for rel, want in lock[files_key].items():
        p = Path(rel)
        try:
            got = sha256_file(p)
        except OSError:
            got = "missing"
        if got != want:
            bad.append(f"{rel}: {want[:12]} -> {got[:12]}")
    return bad


def load_task(lock: dict):
    spec = importlib.util.spec_from_file_location("kairo_frozen_task", lock["task"])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def candidate_env() -> dict:
    keep = ("PATH", "SYSTEMROOT", "SystemRoot", "TEMP", "TMP", "HOME", "USERPROFILE",
            "PYTHONIOENCODING", "COMSPEC", "PATHEXT", "WINDIR", "LANG")
    return {k: v for k, v in os.environ.items() if k in keep}


def run_candidate(program_path: str, inputs: list, entrypoint: str, timeout: float) -> tuple[list | None, str]:
    work = Path(tempfile.mkdtemp(prefix="kairo-cand-"))
    try:
        shutil.copyfile(program_path, work / "cand.py")
        shutil.copyfile(HERE / "cand_runner.py", work / "cand_runner.py")
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "cand_runner.py", entrypoint],
                input=json.dumps({"inputs": inputs}), capture_output=True, text=True,
                timeout=timeout, cwd=work, env=candidate_env())
        except subprocess.TimeoutExpired:
            return None, f"timeout after {timeout}s"
        if proc.returncode != 0:
            return None, f"candidate exited {proc.returncode}: {proc.stderr[-300:]}"
        line = next((ln for ln in reversed(proc.stdout.splitlines()) if ln.startswith(MARKER)), None)
        if line is None:
            return None, "no output line"
        try:
            out = json.loads(line[len(MARKER):])
        except json.JSONDecodeError as exc:
            return None, f"unparseable output: {exc}"
        return out, ""
    finally:
        shutil.rmtree(work, ignore_errors=True)


def score_split(program_path: str, lock: dict, split_dir: Path) -> dict:
    task = load_task(lock)
    inputs = json.loads((split_dir / "inputs.json").read_text(encoding="utf-8"))
    targets = json.loads((split_dir / "targets.json").read_text(encoding="utf-8"))
    outputs, err = run_candidate(program_path, inputs, lock["entrypoint"], lock["eval_timeout_s"])
    if outputs is None:
        return {"score": 0.0, "valid": False, "reason": err}
    if not isinstance(outputs, list) or len(outputs) != len(inputs):
        return {"score": 0.0, "valid": False, "reason": "output shape: expected list of "
                f"{len(inputs)}, got {type(outputs).__name__} len {getattr(outputs, '__len__', lambda: '?')()}"}
    if any(isinstance(o, float) and not math.isfinite(o) for o in outputs):
        return {"score": 0.0, "valid": False, "reason": "output bounds: non-finite value"}
    problem = task.check_output(outputs, inputs)
    if problem:
        return {"score": 0.0, "valid": False, "reason": f"output bounds: {problem}"}
    s = task.score(outputs, targets)
    if not isinstance(s, (int, float)) or not math.isfinite(s) or not 0.0 <= s <= 1.0:
        return {"score": 0.0, "valid": False, "reason": f"task.score returned {s!r}, outside [0,1]"}
    return {"score": float(s), "valid": True, "reason": "",
            "inputs": inputs, "outputs": outputs, "targets": targets}


def _alert(lock: dict, msg: str) -> None:
    with open(lock["alert_file"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"t": time.time(), "alert": msg}) + "\n")


def evaluate(program_path: str) -> dict:
    """OpenEvolve entry point — train split only."""
    lock = load_lock()
    bad = verify_hashes(lock)
    if bad:
        _alert(lock, "tampered before evaluation: " + "; ".join(bad))
        return {"combined_score": 0.0, "tampered": 1.0}
    res = score_split(program_path, lock, Path(lock["train_dir"]))
    bad = verify_hashes(lock)
    if bad:
        _alert(lock, f"tampered during evaluation of {program_path}: " + "; ".join(bad))
        return {"combined_score": 0.0, "tampered": 1.0}
    metrics = {"combined_score": res["score"], "valid": 1.0 if res["valid"] else 0.0}
    k = int(lock.get("feedback_points") or 0)
    if not k:
        return metrics
    # Frozen, optional feedback channel: the first k TRAIN inputs with the candidate's
    # output and the target (never held-out). It helps the search converge; a program
    # that turns it into a lookup table is exactly what the held-out re-score catches.
    from openevolve.evaluation_result import EvaluationResult
    lines = [f"x={x!r}  predicted={o!r}  target={t!r}"
             for x, o, t in zip(res.get("inputs", [])[:k], res.get("outputs", [])[:k],
                                res.get("targets", [])[:k])]
    feedback = res["reason"] if not res["valid"] else "\n".join(lines)
    return EvaluationResult(metrics=metrics, artifacts={"train_feedback": feedback})
