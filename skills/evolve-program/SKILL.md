---
name: evolve-program
description: Use when a researcher wants to search for a better PROGRAM (a heuristic, a scheduler, a kernel, a scoring rule) against a measurable objective, evaluator-first — the evaluator and a held-out set are written and frozen before any evolution, OpenEvolve evolves candidates locally on CPU through non-interactive Claude Code (subscription, never an API key) under an approved budget, and the best program is re-scored once on the held-out set. The winner is never a result — it becomes a hypothesis that goes through hypothesis-cycle, preregistration, replication and update-confidence. On demand only.
---

# Evolve Program (evaluator-first)

## Overview

An AlphaEvolve-style search, built on [OpenEvolve](https://github.com/algorithmicsuperintelligence/openevolve)
(`pip install openevolve`; checked against 0.3.2), with Claude Code as its LLM
backend. Three things make it Kairo rather than a leaderboard:

1. **Evaluator first, frozen.** The evaluator and a **held-out** split that
   evolution never sees are written and hashed in a preregistration **before**
   any candidate is generated. Evolution optimises the train split only.
2. **Anti-gaming is mechanical** (`scripts/harness.py`): the candidate runs in
   its own process with a timeout, receives only inputs, returns only outputs;
   the frozen harness validates shape and bounds and computes the score with
   targets the candidate never sees; the frozen files are hashed before and
   after every evaluation. At the end the best program is re-scored on the
   held-out split and the train − held-out gap is reported and flagged.
3. **The best program is not a result.** It becomes the claim of a new
   hypothesis — "program X improves metric Y by ≥ Z on held-out data" — which
   goes through `hypothesis-cycle`, `preregister-experiment` (a fresh
   confirmatory test on data neither the search nor its held-out split used),
   replication and `update-confidence`, like any other claim. The evolution
   lineage is recorded as `Claims/` nodes (`kind: linaje`).

**Budget:** every evolution step is a `claude -p` call that spends the
researcher's **subscription quota**. Nothing runs without an explicit, per-run
approval of a shown estimate, a per-call cap, a run cap and an iteration cap.

## When to use

- The researcher asks for it ("evolve a better X for objective Y", "search
  programs for …"), with an objective that a script can score.

**When not to use:** the objective can't be scored by a deterministic script
(→ it's a design question, use `hypothesis-cycle`); the "program" is a
paper's method to reproduce (→ `paper-to-tool`); as a background job, on a
schedule, or from another skill without the researcher asking. Never to
"confirm" a hypothesis — its output is a *candidate* hypothesis.

## Files

`${CLAUDE_PLUGIN_ROOT}/skills/evolve-program/scripts/`:

| File | Role |
|---|---|
| `evolve_run.py` | driver: `freeze`, `billing-check`, `plan`, `run`, `rescore`, `bundle`, `lineage-claims` |
| `harness.py` | the frozen evaluation harness (OpenEvolve's evaluation file); copied and hashed at freeze |
| `cand_runner.py` | runs one candidate in its own process; sees inputs only |
| `kairo_llm.py` | OpenEvolve LLM client: `claude -p`, scrubbed env, no tools/settings/MCP, run-level spend ledger |
| `test_evolve.py` | anti-gaming + bundle tests (no LLM calls) |

`examples/polyfit/` — a tiny task with a known optimum (hidden polynomial,
score 1.0), used by the tests and as the template for a new task.

A **task** is one file, `task.py`, with `check_output(outputs, inputs) -> None |
reason` (shape / bounds) and `score(outputs, targets) -> float in [0, 1]`, plus
two split directories (`train/`, `heldout/`) each holding `inputs.json` and
`targets.json`. The evolved program exposes one entry point (default
`solve(x)`), called once per input.

## Procedure

### 1. Objective and evaluator — written first

With the researcher: the objective in one sentence, the metric, and why a
higher score means a better program *for the research question* (not just for
this script). Write `task.py` and the two splits. Rules:

- The held-out split is **disjoint** from train in the way that matters for the
  claim (new instances, not re-shuffled rows) and is never shown to the LLM,
  never in the run directory, never in the prompt.
- `score` must be bounded in [0, 1] and computed only from outputs + targets.
- Freeze the **maximum acceptable train − held-out gap** now (e.g. 0.05); it is
  what decides "generalises" vs "overfit / gamed" later.
- Decide, and freeze, whether the search sees any **train feedback**
  (`--feedback-points K`: the first K train inputs with the candidate's output
  and the target, as OpenEvolve artifacts). A bare scalar score is often too
  little signal to converge (observed: 20 Haiku iterations on the polyfit
  example reached 0.078 of an optimum of 1.0 without it). Feedback is
  **train only**, never held-out; a program that turns it into a lookup table
  is exactly what the held-out re-score catches.
- Write the **objective statement** the LLM will see (`plan --objective
  <file>`): what `solve` must do and how it is scored, with no data and no
  targets. It is part of the approved plan (it enters the approval token).
- Run the example tests' three attacks mentally against your task (memorise the
  train pairs, print a forged value, rewrite the scorer) — each must fail.

### 2. Preregister the evaluator (`EVO-XXXX`)

```
python ${CLAUDE_PLUGIN_ROOT}/skills/evolve-program/scripts/evolve_run.py freeze \
    --task task.py --train train/ --heldout heldout/ --initial initial.py \
    --entrypoint solve --eval-timeout 10 --max-heldout-gap 0.05 [--feedback-points 8]     --run-dir <sandbox>/EVO-XXXX
```

The run directory lives **outside the vault** (default `~/.kairo-sandbox/`,
`KAIRO_SANDBOX` overrides it, same rule as `paper-to-tool`): evolved code is
third-party-grade code executing on your machine. `freeze` copies the task,
harness and train split into `frozen/`, makes them read-only, and writes
`evolve.lock.json` (hashes) + `heldout.lock.json` (held-out path + hashes —
never given to evolution).

Then create the preregistration note `Projects/<slug>/Evolucion/EVO-XXXX.md`
(next vault-wide `EVO-` id, zero-pad 4) and commit it before any `plan`/`run`:

```markdown
---
id: EVO-XXXX
project: <PROJ-XXX>
status: preregistered          # preregistered | running | completed | void
frozen_at: <UTC ISO 8601>
frozen_commit: <vault HEAD>
objective: "<one sentence>"
metric: "<what score measures, in [0,1]>"
evaluator_lock_sha256: <sha256 of evolve.lock.json>
heldout_sha256: {inputs.json: <sha>, targets.json: <sha>}
max_heldout_gap: <e.g. 0.05>
feedback_points: <K train rows shown to the search, or 0>
budget: {model: <haiku|sonnet|opus>, iterations: <N>, per_call_usd: <x>, run_budget_usd: <y>, workers: <w>}
run_dir: <absolute path outside the vault>
result: {best_train: , best_heldout: , gap: , flag: , spent_usd_equiv: , calls: }
spawned_hypothesis: <H-XXXX, once filed>
---
## Objetivo
## Evaluador congelado      (task.py summary, splits, what counts as better, the three attacks it resists)
## Presupuesto aprobado     (the `plan` output shown and the researcher's approval, verbatim)
## Resultado                (written after the run: report.json key fields, lineage claims)
## Enmiendas                (append-only)
```

After the freeze the evaluator, the splits and the gap threshold are immutable;
a change is a new `EVO-` id, not an edit.

### 3. Verify billing — before the first real run on a machine

```
python .../evolve_run.py billing-check --test-call
```

Must report `ok: true`: `claude auth status` shows `authMethod: claude.ai`,
`apiProvider: firstParty` and a `subscriptionType`; no `apiKeyHelper` in your
user settings; and one test call (Haiku, "Reply with exactly: OK") answered
with `provider: firstParty`. The evolution environment is always scrubbed of
`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL` and the
Bedrock / Vertex / Foundry switches, and `--bare` is never used (bare mode
authenticates *only* with an API key). `ok: false` → **crítico**, do not run.

### 4. Plan, show the estimate, get approval

```
python .../evolve_run.py plan --run-dir <run> --iterations 20 --model haiku \
    --per-call-usd 0.25 --run-budget-usd 2 --workers 2 --objective objective.txt
```

Show the researcher the **whole** output: expected calls, expected and
hard-ceiling cost, and the note that the figures are the CLI's list-price
equivalent of **subscription quota** (`costBasis: "list"`), not an API charge.
Caps: `--iterations` (OpenEvolve iterations), `--max-calls` (default 2 ×
iterations, covers retries), `--run-budget-usd` (calls are refused once the
logged total reaches it; concurrent workers may overshoot by at most
`workers × per_call_usd`, stated in the ceiling). Copy the output into
`## Presupuesto aprobado`. The researcher approves by giving back the
`approval_token`; a changed plan or lock gives a different token.

### 5. Run

```
python .../evolve_run.py run --run-dir <run> --approve <token>
```

The driver re-checks the lock, re-runs the billing check (without the test
call), runs OpenEvolve on CPU with the harness, then re-scores the best and the
initial program on train **and held-out** and writes `report.json`
(`best`, `baseline`, `gap`, `flag`, `lineage`, `calls`, `spent_usd_equiv`,
`alerts`). The LLM calls run with `--tools ""`, no settings and no MCP, in an
empty sandbox dir: the model can only return text.

**Stop conditions:** any `ALERTS.jsonl` entry or a changed frozen file →
**crítico**, the run is `void` (exit 2), nothing from it is used. Budget or call
cap reached → the run ends early; report what it reached.

### 6. GPU-only evaluation → transfer bundle

Evolution itself always runs locally on a CPU-scorable proxy. If the final
re-score (or the later confirmatory experiment) needs a GPU, build a bundle:

```
python .../evolve_run.py bundle --run-dir <run> --program best_program.py --out <dir> --split heldout
```

It contains only the frozen task/harness/runner, the program, one split and a
`MANIFEST.sha256`, and it calls the isolation check (`docs/v3-interfaces.md`
§1a): `python ${CLAUDE_PLUGIN_ROOT}/scripts/security/check_bundle.py <dir>`.
Exit 0 → may be uploaded to Kaggle/Colab under `run-experiment`'s
no-vault-remote rule; exit 2 → **crítico**, contaminated, do not upload; exit 1
or a missing script → not clean, do not upload. A bundle is never handed over
unchecked.

### 7. Record the lineage, report, hand off

```
python .../evolve_run.py lineage-claims --run-dir <run> \
    --project-dir <vault>/Projects/<slug> --source EVO-XXXX --by <who>
python ${CLAUDE_PLUGIN_ROOT}/scripts/ledger/build_graph.py --vault <vault> --project <slug> --write
```

One `Claims/` node (`kind: linaje`, via the single claim writer
`claim_status.py`) per program on the best program's ancestry, each
`depends_on` its parent, `probado` at its train score; plus one final node
"the best program generalises to held-out within the frozen gap" —
`probado`, or `refutado` when the gap exceeds the threshold. Failed and
refuted nodes are recorded, never dropped.

Write `## Resultado` in `EVO-XXXX.md`. Report to the researcher: best train
score, held-out score, **gap** (and the `importante` flag if it exceeds the
frozen max), baseline, calls and spend, lineage claim ids. Then:

- **gap within threshold** → offer to file the program as a hypothesis through
  `hypothesis-cycle` (human-submitted entry point): claim "program
  `<sha12>` improves <metric> by ≥ <Z> over <baseline> on held-out data",
  `depends_on: [<final lineage claim>]`, `spawned_from: EVO-XXXX`, test sketch =
  a confirmatory run on **fresh** data (neither the train nor this held-out
  split — it was used once already). It then goes through preregistration,
  replication and `update-confidence` like any claim.
- **gap above threshold** → do not file it as is. Report it as overfit /
  gamed; a new search needs a new `EVO-` preregistration.

## Severity

| Tag | Examples |
|---|---|
| **crítico** | billing not verified as subscription; a frozen file changed / tamper alert; bundle check exit 2 or not clean; held-out split visible to the search |
| **importante** | train − held-out gap above the frozen max; budget cap reached before convergence; evaluation needs a GPU but only a CPU proxy was scored |
| **menor** | OpenEvolve population/island settings; logging cadence |

## Common mistakes

- **Writing the evaluator after seeing candidates.** It is frozen first; a
  changed evaluator is a new `EVO-`.
- **Reporting the best program as a finding.** It is a candidate hypothesis;
  its held-out score was used once for selection and cannot also confirm it.
- **Letting the candidate score itself.** Only the harness scores, from
  outputs and targets the candidate never sees.
- **Putting the held-out split in the run dir, the prompt, or the
  environment.** Only `rescore` / the final step read it.
- **Running without a shown estimate and an approval token**, or with an
  API key in the environment, or with `--bare`.
- **Dropping failed lineage.** The final `refutado` claim is the record that
  the search overfit.
- **Uploading a bundle the isolation check didn't pass** (exit 1 counts as not
  clean).

## Related

- `hypothesis-cycle` — where the winning program's claim goes next.
- `preregister-experiment` / `update-confidence` — its confirmatory test and status.
- `scripts/ledger/claim_status.py`, `build_graph.py` — lineage nodes and `_ledger.md`.
- `scripts/security/check_bundle.py` (Block A) — bundle isolation check, contract §1a.
- `paper-to-tool` — sandbox location rule reused here.
