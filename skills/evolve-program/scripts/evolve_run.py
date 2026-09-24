#!/usr/bin/env python3
"""evolve-program driver: freeze the evaluator, check billing, plan, run OpenEvolve,
re-score on held-out, bundle for a GPU runtime, record the lineage as Claims.

    evolve_run.py freeze   --task T.py --train DIR --heldout DIR --initial P.py \
                           --entrypoint solve --eval-timeout 10 --max-heldout-gap 0.05 \
                           --run-dir RUN
    evolve_run.py billing-check [--test-call] [--run-dir RUN]
    evolve_run.py plan     --run-dir RUN --iterations N --model haiku \
                           --per-call-usd 0.25 --run-budget-usd 2 [--workers 2] [--max-calls M]
    evolve_run.py run      --run-dir RUN --approve <token printed by plan>
    evolve_run.py rescore  --run-dir RUN --program P.py
    evolve_run.py bundle   --run-dir RUN --program P.py --out DIR [--split heldout]
    evolve_run.py lineage-claims --run-dir RUN --project-dir <vault>/Projects/<slug> \
                           --source EVO-XXXX --by <who>

See skills/evolve-program/SKILL.md for the flow and the rules. Requires the
`openevolve` package (pip install openevolve) only for `run`; everything else is
standard library.

Exit codes: 0 ok; 1 error; 2 crítico (tampering, billing off-subscription,
bundle contaminated); 3 refused (bad/missing approval, budget, not clean).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))

import harness  # noqa: E402

__version__ = "1.0.0"
TOOL = f"kairo/evolve-program@{__version__}"
OFF_SUBSCRIPTION_VARS = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    "AWS_BEARER_TOKEN_BEDROCK",
)
# Rough list-price-equivalent USD per evolution call (a ~6-12k-token prompt with
# code + ~1-3k output). Used only for the pre-run estimate shown for approval;
# the real per-call figure is logged from each call's total_cost_usd.
EST_PER_CALL = {"haiku": 0.03, "sonnet": 0.12, "opus": 0.40}


class Refused(Exception):
    code = 3


class Critical(Exception):
    code = 2


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _json(p: Path) -> dict:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _write_json(p: Path, obj) -> None:
    Path(p).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _split_hashes(d: Path) -> dict:
    return {f: sha(d / f) for f in ("inputs.json", "targets.json")}


# ---------------------------------------------------------------- freeze
def cmd_freeze(a) -> dict:
    run = a.run_dir.resolve()
    if run.exists() and any(run.iterdir()):
        raise Refused(f"{run} is not empty — a frozen run dir is never reused")
    frozen = run / "frozen"
    (frozen / "train").mkdir(parents=True)
    shutil.copyfile(a.task, frozen / "task.py")
    shutil.copyfile(HERE / "harness.py", frozen / "harness.py")
    shutil.copyfile(HERE / "cand_runner.py", frozen / "cand_runner.py")
    for f in ("inputs.json", "targets.json"):
        shutil.copyfile(a.train / f, frozen / "train" / f)
    shutil.copyfile(a.initial, run / "initial_program.py")
    # the held-out split is NOT copied into the run dir: only its hash is frozen here,
    # its path goes to heldout.lock.json, which evolution never receives.
    heldout = a.heldout.resolve()
    ho_hashes = _split_hashes(heldout)
    for f in ("task.py", "harness.py", "cand_runner.py", "train/inputs.json", "train/targets.json"):
        os.chmod(frozen / f, stat.S_IREAD)
    files = {(frozen / f).as_posix(): sha(frozen / f)
             for f in ("task.py", "harness.py", "cand_runner.py",
                       "train/inputs.json", "train/targets.json")}
    lock = {
        "tool": TOOL, "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "task": (frozen / "task.py").as_posix(), "entrypoint": a.entrypoint,
        "eval_timeout_s": a.eval_timeout, "train_dir": (frozen / "train").as_posix(),
        "alert_file": (run / "ALERTS.jsonl").as_posix(),
        "files": files, "initial_program_sha256": sha(run / "initial_program.py"),
        "heldout_sha256": ho_hashes, "max_heldout_gap": a.max_heldout_gap,
        "feedback_points": a.feedback_points,
    }
    _write_json(run / "evolve.lock.json", lock)
    _write_json(run / "heldout.lock.json", {"dir": heldout.as_posix(), "files": ho_hashes})
    lock_sha = sha(run / "evolve.lock.json")
    return {"run_dir": run.as_posix(), "evaluator_lock_sha256": lock_sha,
            "heldout_sha256": ho_hashes, "files": files}


# ---------------------------------------------------------------- billing
def billing_check(test_call: bool) -> dict:
    res: dict = {"ok": False, "checks": {}}
    set_vars = [v for v in OFF_SUBSCRIPTION_VARS if os.environ.get(v)]
    res["checks"]["off_subscription_env_vars_set"] = set_vars
    res["checks"]["note_env"] = ("these are scrubbed from every evolution call's environment"
                                 if set_vars else "none set")
    helpers = []
    for f in (Path.home() / ".claude" / "settings.json", Path.home() / ".claude" / "settings.local.json"):
        if f.exists() and "apiKeyHelper" in f.read_text(encoding="utf-8", errors="replace"):
            helpers.append(f.as_posix())
    res["checks"]["apiKeyHelper_in"] = helpers
    env = {k: v for k, v in os.environ.items() if k not in OFF_SUBSCRIPTION_VARS}
    try:
        st = subprocess.run(["claude", "auth", "status"], capture_output=True, text=True,
                            encoding="utf-8", env=env, timeout=60)
        auth = json.loads(st.stdout)
    except Exception as exc:  # noqa: BLE001
        res["error"] = f"claude auth status failed: {exc}"
        return res
    res["checks"]["auth"] = {k: auth.get(k) for k in
                             ("loggedIn", "authMethod", "apiProvider", "subscriptionType")}
    sub_ok = (auth.get("loggedIn") and auth.get("authMethod") == "claude.ai"
              and auth.get("apiProvider") == "firstParty" and bool(auth.get("subscriptionType")))
    ok = bool(sub_ok) and not helpers
    if test_call:
        import kairo_llm  # needs openevolve importable only for the class; build_cmd is plain
        cmd = kairo_llm.build_cmd("haiku", 0.05, None)
        env2 = kairo_llm.scrubbed_env()
        t = subprocess.run(cmd, input="Reply with exactly: OK", capture_output=True, text=True,
                           encoding="utf-8", env=env2, timeout=180)
        try:
            out = json.loads(t.stdout)
            mu = out.get("modelUsage") or {}
            res["test_call"] = {
                "result": out.get("result"), "is_error": out.get("is_error"),
                "total_cost_usd": out.get("total_cost_usd"),
                "models": {k: {"provider": v.get("provider"), "costBasis": v.get("costBasis")}
                           for k, v in mu.items()},
                "env_had_api_key": "ANTHROPIC_API_KEY" in env2,
                "argv_has_bare": "--bare" in cmd,
            }
            ok = ok and out.get("result", "").strip() == "OK" and not out.get("is_error") and \
                all(v.get("provider") == "firstParty" for v in mu.values())
        except (json.JSONDecodeError, TypeError):
            res["test_call"] = {"error": (t.stderr or t.stdout)[-400:]}
            ok = False
    res["ok"] = bool(ok)
    res["verdict"] = ("subscription (claude.ai OAuth); no API key in the evolution environment"
                      if ok else "NOT verified — do not run")
    return res


# ---------------------------------------------------------------- plan / approval
def _token(plan: dict) -> str:
    return hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()[:16]


def cmd_plan(a) -> dict:
    run = a.run_dir.resolve()
    lock_sha = sha(run / "evolve.lock.json")
    per_call_est = EST_PER_CALL.get(a.model, EST_PER_CALL["sonnet"])
    max_calls = a.max_calls or a.iterations * 2
    plan = {
        "evaluator_lock_sha256": lock_sha, "iterations": a.iterations, "model": a.model,
        "per_call_cap_usd": a.per_call_usd, "run_budget_usd": a.run_budget_usd,
        "workers": a.workers, "max_calls": max_calls, "seed": a.seed,
        # the researcher's objective statement, sent as the LLM system message. It is
        # part of what is approved (it enters the token) and must never contain
        # held-out data or targets.
        "objective": a.objective.read_text(encoding="utf-8").strip() if a.objective else None,
    }
    est = {
        "expected_calls": a.iterations,
        "expected_usd_equiv": round(a.iterations * per_call_est, 3),
        "hard_ceiling_usd_equiv": round(min(max_calls * a.per_call_usd,
                                            a.run_budget_usd + a.workers * a.per_call_usd), 3),
        "note": ("USD figures are the CLI's list-price equivalent (costBasis 'list') of the "
                 "SUBSCRIPTION quota these calls consume — not a charge to an API account. "
                 "The run stops refusing calls once the logged total reaches run_budget_usd; "
                 "concurrent workers can overshoot by at most workers x per_call_cap."),
    }
    token = _token(plan)
    _write_json(run / "plan.json", {"plan": plan, "estimate": est, "approval_token": token})
    return {"plan": plan, "estimate": est, "approval_token": token,
            "next": f"evolve_run.py run --run-dir {run.as_posix()} --approve {token}"}


# ---------------------------------------------------------------- run
def _lineage(out_dir: Path, best_id: str | None) -> list[dict]:
    progs = {}
    for p in sorted(out_dir.glob("checkpoints/checkpoint_*/programs/*.json"),
                    key=lambda x: int(x.parent.parent.name.split("_")[-1])):
        rec = _json(p)
        progs[rec["id"]] = rec
    chain, cur = [], best_id
    while cur and cur in progs and len(chain) < 10_000:
        r = progs[cur]
        chain.append({"id": r["id"], "parent_id": r.get("parent_id"),
                      "iteration_found": r.get("iteration_found"),
                      "train_score": (r.get("metrics") or {}).get("combined_score")})
        cur = r.get("parent_id")
    return list(reversed(chain))


def _rescore(run: Path, program: Path) -> dict:
    lock = harness.load_lock(str(run / "evolve.lock.json"))
    ho = _json(run / "heldout.lock.json")
    ho_dir = Path(ho["dir"])
    if _split_hashes(ho_dir) != ho["files"]:
        raise Critical(f"held-out split changed since freeze: {ho_dir}")
    bad = harness.verify_hashes(lock)
    if bad:
        raise Critical("frozen evaluator changed: " + "; ".join(bad))
    strip = lambda r: {k: v for k, v in r.items() if k not in ("inputs", "outputs", "targets")}
    tr = strip(harness.score_split(str(program), lock, Path(lock["train_dir"])))
    he = strip(harness.score_split(str(program), lock, ho_dir))
    gap = tr["score"] - he["score"]
    flag = None
    if gap > lock["max_heldout_gap"]:
        flag = (f"importante: train {tr['score']:.4f} vs held-out {he['score']:.4f} — gap "
                f"{gap:.4f} > frozen max {lock['max_heldout_gap']}: overfit to the train split "
                "or evaluator gaming; the program is not a candidate result as is")
    return {"program": program.as_posix(), "program_sha256": sha(program),
            "train": tr, "heldout": he, "gap": round(gap, 6), "flag": flag}


def cmd_run(a) -> dict:
    run = a.run_dir.resolve()
    pj = _json(run / "plan.json")
    plan = pj["plan"]
    if a.approve != pj["approval_token"] or _token(plan) != a.approve:
        raise Refused("approval token does not match plan.json — re-run `plan`, show the "
                      "estimate to the researcher, and pass the token they approve")
    if sha(run / "evolve.lock.json") != plan["evaluator_lock_sha256"]:
        raise Critical("evolve.lock.json changed after the plan was approved")
    lock = harness.load_lock(str(run / "evolve.lock.json"))
    bad = harness.verify_hashes(lock)
    if bad:
        raise Critical("frozen evaluator changed before the run: " + "; ".join(bad))
    bill = billing_check(test_call=False)
    _write_json(run / "billing.json", bill)
    if not bill["ok"]:
        raise Critical(f"billing not verified as subscription: {bill}")

    for v in OFF_SUBSCRIPTION_VARS:           # scrub for this process and its workers
        os.environ.pop(v, None)
    sandbox = run / "llm-sandbox"
    sandbox.mkdir(exist_ok=True)
    os.environ.update({
        "KAIRO_EVOLVE_LOCK": (run / "evolve.lock.json").as_posix(),
        "KAIRO_EVOLVE_LEDGER": (run / "spend.jsonl").as_posix(),
        "KAIRO_EVOLVE_RUN_BUDGET_USD": str(plan["run_budget_usd"]),
        "KAIRO_EVOLVE_MAX_CALLS": str(plan["max_calls"]),
        "KAIRO_EVOLVE_PER_CALL_USD": str(plan["per_call_cap_usd"]),
        "KAIRO_EVOLVE_SANDBOX": sandbox.as_posix(),
        "PYTHONPATH": os.pathsep.join([str(HERE), os.environ.get("PYTHONPATH", "")]),
    })
    from openevolve import run_evolution
    from openevolve.config import Config, LLMModelConfig
    import kairo_llm

    cfg = Config()
    m = LLMModelConfig(name=plan["model"], weight=1.0, timeout=300, retries=1, retry_delay=5,
                       init_client=kairo_llm.init_client)
    if plan.get("objective"):
        cfg.prompt.system_message = plan["objective"]
    cfg.llm.models = [m]
    cfg.llm.evaluator_models = [m]
    cfg.max_iterations = plan["iterations"]
    cfg.random_seed = plan["seed"]
    cfg.checkpoint_interval = max(1, plan["iterations"] // 4)
    cfg.evaluator.timeout = int(lock["eval_timeout_s"]) + 30
    cfg.evaluator.cascade_evaluation = False
    cfg.evaluator.use_llm_feedback = False
    cfg.evaluator.parallel_evaluations = plan["workers"]
    cfg.database.population_size = max(10, plan["iterations"])
    out_dir = run / "openevolve"
    t0 = time.time()
    result = run_evolution(initial_program=str(run / "initial_program.py"),
                           evaluator=str(Path(lock["task"]).parent / "harness.py"),
                           config=cfg, iterations=plan["iterations"],
                           output_dir=str(out_dir), cleanup=False)
    elapsed = time.time() - t0

    alerts = []
    if Path(lock["alert_file"]).exists():
        alerts = [json.loads(x) for x in Path(lock["alert_file"]).read_text().splitlines() if x]
    best = run / "best_program.py"
    best.write_text(result.best_code, encoding="utf-8")
    spent, calls = 0.0, 0
    if (run / "spend.jsonl").exists():
        recs = [json.loads(x) for x in (run / "spend.jsonl").read_text().splitlines() if x]
        spent, calls = sum(r.get("cost_usd", 0.0) for r in recs), len(recs)
    report = {
        "tool": TOOL, "elapsed_s": round(elapsed, 1), "plan": plan,
        "calls": calls, "spent_usd_equiv": round(spent, 4),
        "alerts": alerts,
        "best": _rescore(run, best) if not alerts else None,
        "baseline": _rescore(run, run / "initial_program.py") if not alerts else None,
        "lineage": _lineage(out_dir, getattr(result.best_program, "id", None)),
        "evaluator_unchanged": not harness.verify_hashes(lock),
    }
    _write_json(run / "report.json", report)
    if alerts or not report["evaluator_unchanged"]:
        raise Critical(f"tampering detected during the run — results void: {alerts}")
    return report


# ---------------------------------------------------------------- bundle
def cmd_bundle(a) -> dict:
    run = a.run_dir.resolve()
    lock = harness.load_lock(str(run / "evolve.lock.json"))
    out = a.out.resolve()
    if out.exists():
        raise Refused(f"{out} exists — bundles are built fresh")
    out.mkdir(parents=True)
    frozen = Path(lock["task"]).parent
    for f in ("task.py", "harness.py", "cand_runner.py"):
        shutil.copyfile(frozen / f, out / f)
    shutil.copyfile(a.program, out / "program.py")
    src = Path(_json(run / "heldout.lock.json")["dir"]) if a.split == "heldout" else Path(lock["train_dir"])
    (out / a.split).mkdir()
    for f in ("inputs.json", "targets.json"):
        shutil.copyfile(src / f, out / a.split / f)
    lines = [f"{sha(p)}  {p.relative_to(out).as_posix()}"
             for p in sorted(out.rglob("*")) if p.is_file()]
    (out / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    script = a.check_script or PLUGIN_ROOT / "scripts" / "security" / "check_bundle.py"
    if not Path(script).exists():
        raise Refused(f"bundle isolation check not found at {script} — a bundle is never "
                      "handed over unchecked")
    chk = subprocess.run([sys.executable, str(script), str(out)], capture_output=True,
                         text=True, encoding="utf-8")
    try:
        parsed = json.loads(chk.stdout)
    except json.JSONDecodeError:
        parsed = {"status": "error", "findings": [], "raw": chk.stdout[-300:]}
    res = {"bundle": out.as_posix(), "check_exit": chk.returncode, "check": parsed}
    # contract §1a: 0 clean, 2 contaminated, 1 error; anything but 0 is NOT clean
    if chk.returncode == 2:
        raise Critical(f"bundle contaminated — do not upload: {parsed}")
    if chk.returncode != 0:
        raise Refused(f"bundle check could not run (exit {chk.returncode}) — treated as not "
                      f"clean, do not upload: {parsed}")
    res["handover"] = "clean — may be uploaded to the external runtime"
    return res


# ---------------------------------------------------------------- lineage -> Claims
def cmd_lineage_claims(a) -> dict:
    run = a.run_dir.resolve()
    rep = _json(run / "report.json")
    lock_sha = sha(run / "evolve.lock.json")[:12]
    cs = PLUGIN_ROOT / "scripts" / "ledger" / "claim_status.py"
    created, parent = [], None

    def claim(statement, how, deps, status, evidence):
        argv = [sys.executable, str(cs), "new", "--project-dir", str(a.project_dir),
                "--kind", "linaje", "--statement", statement, "--how", how,
                "--source", a.source, "--by", a.by]
        if deps:
            argv += ["--depends-on", *deps]
        p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8")
        if p.returncode != 0:
            raise Refused(f"claim_status new failed: {p.stderr}")
        path = p.stdout.strip()
        s = subprocess.run([sys.executable, str(cs), "set", "--note", path, "--status", status,
                            "--by", a.by, "--evidence", evidence],
                           capture_output=True, text=True, encoding="utf-8")
        if s.returncode != 0:
            raise Refused(f"claim_status set failed: {s.stderr}")
        cid = Path(path).stem
        created.append({"claim": cid, "path": path, "status": status})
        return cid

    for step in rep["lineage"]:
        parent = claim(
            f"Programa {step['id'][:8]} (iteración {step['iteration_found']}) puntúa "
            f"{step['train_score']:.4f} en el split de entrenamiento del evaluador congelado.",
            f"Evaluador congelado {lock_sha} ({a.source}); linaje OpenEvolve, padre "
            f"{(step['parent_id'] or '—')[:8]}. Puntuación en train: señal de búsqueda, no evidencia.",
            [parent] if parent else [], "probado",
            f"{a.source}: train combined_score {step['train_score']:.6f} (report.json)")
    best = rep["best"]
    ok = best["flag"] is None and best["heldout"]["valid"]
    claim(f"El mejor programa ({best['program_sha256'][:12]}) generaliza al held-out: "
          f"gap train−held-out ≤ {_json(run / 'evolve.lock.json')['max_heldout_gap']}.",
          f"Re-puntuación única tras la evolución sobre el held-out congelado ({a.source}).",
          [parent] if parent else [], "probado" if ok else "refutado",
          f"train {best['train']['score']:.4f}, held-out {best['heldout']['score']:.4f}, "
          f"gap {best['gap']:.4f}" + (f"; {best['flag']}" if best["flag"] else ""))
    return {"created": created}


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="evolve-program driver")
    ap.add_argument("--version", action="version", version=TOOL)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--task", type=Path, required=True)
    f.add_argument("--train", type=Path, required=True)
    f.add_argument("--heldout", type=Path, required=True)
    f.add_argument("--initial", type=Path, required=True)
    f.add_argument("--entrypoint", default="solve")
    f.add_argument("--eval-timeout", type=float, default=10.0)
    f.add_argument("--max-heldout-gap", type=float, required=True)
    f.add_argument("--run-dir", type=Path, required=True)
    f.add_argument("--feedback-points", type=int, default=0,
                   help="frozen: show the search this many TRAIN (input, output, target) rows")
    b = sub.add_parser("billing-check")
    b.add_argument("--test-call", action="store_true")
    b.add_argument("--run-dir", type=Path)
    p = sub.add_parser("plan")
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--iterations", type=int, required=True)
    p.add_argument("--model", default="haiku")
    p.add_argument("--per-call-usd", type=float, default=0.25)
    p.add_argument("--run-budget-usd", type=float, required=True)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--max-calls", type=int)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--objective", type=Path,
                   help="text file: objective statement for the LLM (no data, no targets)")
    r = sub.add_parser("run")
    r.add_argument("--run-dir", type=Path, required=True)
    r.add_argument("--approve", required=True)
    s = sub.add_parser("rescore")
    s.add_argument("--run-dir", type=Path, required=True)
    s.add_argument("--program", type=Path, required=True)
    bu = sub.add_parser("bundle")
    bu.add_argument("--run-dir", type=Path, required=True)
    bu.add_argument("--program", type=Path, required=True)
    bu.add_argument("--out", type=Path, required=True)
    bu.add_argument("--split", choices=("train", "heldout"), default="heldout")
    bu.add_argument("--check-script", type=Path,
                    help="TEST ONLY: a stub honoring the check_bundle.py CLI (contract §1a)")
    lc = sub.add_parser("lineage-claims")
    lc.add_argument("--run-dir", type=Path, required=True)
    lc.add_argument("--project-dir", type=Path, required=True)
    lc.add_argument("--source", required=True)
    lc.add_argument("--by", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "run" and not sys.flags.utf8_mode:
        # OpenEvolve writes checkpoints / logs with the platform default encoding;
        # on Windows (cp1252) LLM-written code with any non-ASCII char crashes the
        # run at its final checkpoint. Re-launch in UTF-8 mode (inherited by workers).
        env = dict(os.environ, PYTHONUTF8="1")
        return subprocess.run([sys.executable, "-X", "utf8", __file__, *(argv or sys.argv[1:])],
                              env=env).returncode
    try:
        if a.cmd == "freeze":
            res = cmd_freeze(a)
        elif a.cmd == "billing-check":
            res = billing_check(a.test_call)
            if a.run_dir:
                _write_json(a.run_dir / "billing.json", res)
        elif a.cmd == "plan":
            res = cmd_plan(a)
        elif a.cmd == "run":
            res = cmd_run(a)
        elif a.cmd == "rescore":
            res = _rescore(a.run_dir.resolve(), a.program.resolve())
        elif a.cmd == "bundle":
            res = cmd_bundle(a)
        else:
            res = cmd_lineage_claims(a)
    except (Refused, Critical) as exc:
        print(json.dumps({"refused" if exc.code == 3 else "critico": str(exc)},
                         ensure_ascii=False, indent=2))
        return exc.code
    print(json.dumps(res, indent=2, ensure_ascii=False))
    if a.cmd == "billing-check" and not res.get("ok"):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
