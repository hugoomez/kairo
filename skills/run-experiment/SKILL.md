---
name: run-experiment
description: Use when a frozen `preregistered` experiment note is ready to execute — runs the real experiment code, checks the environment against the frozen manifest, applies the frozen analysis plan mechanically, and records the verdict. Reports validity + result + verdict only; the full log goes to a file.
---

# Run Experiment

## Overview

Executes one frozen preregistration and records its outcome. The analysis is
**mechanical**: apply exactly the frozen `## Plan de análisis` — the specific test
and threshold — via a script. **Never** judge in prose whether the result "seems"
to support the hypothesis.

Binding rules:

- Kairo principle 3: the preregistration froze before code ran and is
  immutable. Any deviation forced by the run goes in the note's `## Enmiendas`,
  never as an edit to a frozen section.
- Kairo principle 2: a single supporting run does **not** promote the
  hypothesis to `apoyada` — that needs an independent replication.

## When to use

- An experiment note has `status: preregistered` and the team approved the run.

**When not to use:** the note isn't frozen (`status` ≠ `preregistered`); re-running
a `completed` experiment (needs a human decision + `## Enmiendas`); designing or
preregistering (use `preregister-experiment`).

## Inputs

A frozen `Projects/<slug>/Experimentos/E-XXXX.md` (`status: preregistered`,
`frozen_at` / `frozen_commit` set, full `environment:` manifest). Refuse otherwise.

## Procedure

### 0. Implement the frozen design — literally

If the experiment code (`Scripts/experiments/E-XXXX/`) isn't written yet, write it
now — *after* the prereg is frozen, *before* pre-flight. Two non-negotiable rules:

**Transcribe the frozen formulas verbatim.** `## Diseño`, `## Plan de análisis`,
`## Variables`, `## Umbral de invalidez` and every constant in them — plateau
detection, stop point, Δ, the decision thresholds, the stopping rule, seed
handling — go into code exactly as frozen. No "the paper probably meant…", no
rounding "to be clean", no quiet tuning, no "this variant is better". A `spec.py`
(or equivalent) is a transcription of the frozen note, **not** a fresh judgement:
if a value there disagrees with the note, the note wins and the code is wrong.
This holds under deadline pressure too — a queued run is not a licence to
interpret.

**Genuine ambiguity → `## Enmiendas` + a direct question, never a silent
default.** If the frozen text truly admits two defensible readings:

1. write a dated `## Enmiendas` entry — what is ambiguous, the candidate
   readings, the one you propose and why, marked unresolved;
2. ask the researcher directly, with a severity tag (below);
3. proceed on that point only once they answer. If they can't answer in time,
   the run waits. A silent default on a frozen-design ambiguity is the exact
   failure preregistration exists to prevent.

**Severity tags — same vocabulary as `preregister-experiment` ("Flagging risks
and ambiguities"):**

| Tag | Meaning | What it forces |
|---|---|---|
| **`crítico`** | can invalidate the whole experiment or spend the full compute budget with no readable result (a control that won't reproduce the cited result, a model detail that could suppress the effect, an ambiguous stopping rule, a metric that doesn't measure the claim) | **Stop. Do not run.** Its **own callout at the top** of what you show the researcher — never a bullet beside minor items. Explicit decision required, recorded in `## Enmiendas`. |
| **`importante`** | plausibly shifts the result or its reading, but the run stays readable either way (a contested hyperparameter, an estimator choice) | Flag prominently with a proposed default + rationale; get a decision if the researcher is reachable, else proceed on the stated default and log it in `## Enmiendas`. |
| **`menor`** | implementation detail, low impact either way (activation function where the design doesn't turn on it, logging cadence, naming) | State the choice in one line. No decision needed. |

"This control may not reproduce the cited baseline result and the run comes back invalid" is a
`crítico` sentence — it must never be presented with the same weight as
"ReLU or GELU?".

**Use validated tools as they are.** If the prereg lists `environment.tools`
(`method_provenance: validated_tool`), the experiment code **calls** each
tool's `tool/<method>.py` (import it, or run its CLI) with the parameters the
frozen design declares. Never re-transcribe, "simplify", or copy-and-edit a
tool's code — a changed copy is a from-text reimplementation again, and its
hash no longer proves anything. A tool that can't express the frozen design
is a `crítico` ambiguity (above), not a licence to patch it.

**Record the code commit.** The frozen prereg predates the code, so
`frozen_commit` (often the vault HEAD at freeze) does not by itself pin the
implementation. Once the code is written and committed, add a dated `## Enmiendas`
entry — `implementación de E-XXXX, code sha <sha>` — plus any `importante` /
`menor` choices made while coding. Pre-flight (step 1) checks the code against
*this* sha.

### 1. Pre-flight — environment matches the frozen manifest

Recompute, **the same way `preregister-experiment` did**:

- **Dependency hash:** re-run `pip freeze` (or `npm ls --json`), `sha256` the
  output, compare to `environment.dependencies_hash`.
- **Dataset hash(es):** `sha256` each dataset file, rebuild the manifest hash,
  compare to `environment.dataset_hash`.
- **Tool hash(es):** for every `environment.tools` entry, check that
  `TOOL.md` says `status: validated`, then
  `python ${CLAUDE_PLUGIN_ROOT}/scripts/paper_to_tool/tool_hash.py verify
  <vault>/<path> --expected <validation_hash> --json`. Exit 1 (any changed,
  added, or missing file under the tool) is a mismatch, exactly like a
  dependency or dataset hash mismatch.
- **Code commit:** verify the working tree is at the experiment-code sha — the
  one recorded in `## Enmiendas` (step 0), or `frozen_commit` itself if that
  already points at the code repo. If it isn't and can't be checked out, that is
  a mismatch. *(On an external runtime the code is a transfer bundle, not a git
  tree — instead verify `MANIFEST.sha256` inside the bundle and that those files
  are byte-identical to `Scripts/experiments/E-XXXX/` at that sha. See step 2.)*

**Any mismatch → stop. Do not run.** Report: which check failed, expected vs
actual value, and that no code was executed. A hardware string that differs from
`environment.hardware` does **not** stop the run here, but record it — it feeds
the validity check (step 4) via the note's `## Umbral de invalidez`.

On success: set the **experiment note's** `status: running`, record the start
timestamp, then **invoke `update-confidence`** — trigger
`preregistrada → en_experimento`, payload = the hypothesis id + this experiment
id. `update-confidence` moves the hypothesis and writes its `history`; this skill
never edits hypothesis `status`.

### 2. Execution

Run the experiment command from the note's `## Diseño` / documented entrypoint
via the Bash tool. If there is no unambiguous command, stop and ask — do not
guess.

**Where the code lives.** The experiment code is a self-contained directory,
`Scripts/experiments/E-XXXX/` — the scripts plus the frozen `E-XXXX.data.json`
manifest and `E-XXXX.deps.txt` lockfile. Every script must resolve its inputs
**relative to its own location** (`Path(__file__).resolve().parent`), never by
walking up to a vault path or hard-coding `Projects/<slug>/…`. A script that only
runs inside the full vault tree is a bug — fix it before running. A guarded
local-first fallback (`use the file next to me; only if absent, look in the vault
tree`) is acceptable for local dev, but the bundle copy must always be the one
used on an external runtime.

**External runtime (GPU/TPU the local box lacks).** When the run needs hardware
this machine doesn't have — Kaggle, Colab, a rented box:

- **Never clone, push, or create a git remote for `vault/` to run an
  experiment.** The vault is local-only by design (Fase 0: all local, no remote
  repo) and that principle is not negotiable for a run. `frozen_commit` is
  already the provenance link; nothing about the run requires the vault to be
  reachable from the runtime.
- Identify the **minimal fileset the experiment actually needs** and flatten it
  into one self-contained transfer bundle: `Scripts/experiments/E-XXXX/*` (all
  scripts), `E-XXXX.data.json` (frozen data manifest), `E-XXXX.deps.txt`
  (lockfile), and a `MANIFEST.sha256` covering every file in the bundle. Nothing
  from the wider vault tree — no `Projects/…` notes, no other experiments.
  Tools listed in `environment.tools` are the one addition: copy each
  `Tools/P-XXXX/<method>/` folder (its `tool/`, `env/`, and its own
  `MANIFEST.sha256`) into the bundle, and in the runtime also run
  `sha256sum -c MANIFEST.sha256` inside each tool folder — the same file
  `tool_hash.py` hashed, so a byte-identical tool is proven on the runtime too.
- Transfer the bundle by whatever the runtime supports — dataset upload, file
  copy, or a throwaway scratch repo that is **not** the vault. In the runtime,
  run `sha256sum -c MANIFEST.sha256` and re-check the `dependencies_hash` before
  any training starts.
- Record in `## Resultado` which files were transferred and the bundle's
  manifest hash. If the bundle layout had to differ from anything stated in the
  frozen `## Manifiesto de entorno`, that difference is a `## Enmiendas` entry,
  not a silent adjustment.

Capture:

- **stdout + stderr** — streamed to the log file (step 7).
- **wall-clock time** — always.
- **resources** — peak memory / CPU where the platform provides it
  (`/usr/bin/time -v` on Linux/macOS; a sampling loop on Windows); otherwise
  wall-clock only, and note the limitation in the log.

### 3. Sanity checks

| Check | Passes when |
|---|---|
| Completed cleanly | process exited 0, no errors/exceptions in stderr |
| Control reproduced | the control condition matched the cited known result within the tolerance stated in `## Diseño` / `## Umbral de invalidez` |
| Result plausible | the primary metric landed inside the plausible range from `## Predicción` / `## Umbral de invalidez` |
| No data leakage | **(a)** train / test / validation split files share no IDs — hash-compare the ID sets, zero intersection; **(b)** for time-series or temporally-ordered data, every test timestamp is strictly after every training timestamp; **(c)** a near-duplicate scan between splits (row hash, or embedding cosine above a stated threshold) finds no matches |

Fill the note's `sanity_checks:` fields from these — `baseline_reproduces`,
`loss_decreases`, `no_data_leakage`, `seed_controls_variance`; leave any check the
experiment gives no signal for at its placeholder.

A **failed leakage check is infrastructure-type invalidity** — handle it exactly
like the other sanity checks in step 4 (retry up to 2×, then
`experiment_validity: invalid` + `needs_human_review: true`). Never continue to
analysis with a leak present.

### 4. Validity

- **Any sanity check fails as an infrastructure / data-integrity failure** (crash,
  OOM, missing file, network error, non-deterministic blow-up, **data leakage
  detected**) → `experiment_validity: invalid`; **auto-retry the run up to 2 more
  times** (3 attempts total). Still failing →
  **stop**, set `status: completed`, `experiment_validity: invalid`,
  `needs_human_review: true`, and flag for human review. Do **not** run analysis.
  If all 3 attempts failed identically and deterministically, say so in the flag —
  it looks like a real result, not infrastructure.
- **All sanity checks pass** → `experiment_validity: valid`; continue.

### 5. Mechanical analysis

Apply **exactly** the frozen `## Plan de análisis`. Branch on the frozen
`analysis_plan` — do not compute or reason about significance yourself:

**`analysis_plan: frequentist`** — the two-proportion z-test:

```
python ${CLAUDE_PLUGIN_ROOT}/scripts/analysis/two_proportion_test.py X1 N1 X2 N2 \
    --alpha <frozen alpha> \
    --direction <increase|decrease from the frozen prediction> \
    --min-effect <frozen T_apoyo> --floor-effect <frozen T_refuta> --json
```

Take `risk_difference` / `cohens_h`, `p_value`, and `verdict` verbatim.

**`analysis_plan: bayesian`** — the Beta-Binomial Bayes factor:

```
python ${CLAUDE_PLUGIN_ROOT}/scripts/analysis/bayes_factor_proportions.py X1 N1 X2 N2 \
    --prior-a <frozen prior a> --prior-b <frozen prior b> \
    --direction <increase|decrease from the frozen prediction> \
    --bf-threshold <frozen bf-threshold> --json
```

Take `bf10`, `bf01`, `risk_difference`, and `verdict` verbatim — `risk_difference`
is the mechanical source for `result.effect` in the bayesian branch, matching
how `risk_difference` / `cohens_h` sources it in the frequentist branch above.

If the frozen plan names a test with **no script available**, stop and report
the missing tool — do not eyeball it.

Write into the note's `result:` frontmatter: `effect` (point estimate +
interval), `p_value` (`frequentist`) or `bayes_factor` (`bayesian` — the
script's `bf10`), and `verdict`, mapping the script's short string to the
enum (same mapping regardless of which script produced it):

| script | `result.verdict` |
|---|---|
| `apoya` | `apoyada` |
| `refuta` | `refutada` |
| `inconcluso` | `inconclusa` |

(`evidencia_mixta` only if the frozen plan named >1 primary metric and they
conflict.) Add a `## Resultado` section (new content, not a frozen edit) with the
exact command run, the full script output, and the mapped verdict.

### 6. Cost / time calibration

Record `cost_actual` and actual wall-clock, and in `## Resultado` note them
against `cost_estimated` (the figure shown before approval) — e.g.
`estimado 2.0 GPU-h → real 3.1 GPU-h (+55%)`. This is the calibration signal for
future estimates.

### 7. Log and report

- Write the full run log (stdout+stderr, timings, resources, retry attempts) to
  **`Projects/<slug>/Experimentos/logs/E-XXXX.log`** (create `logs/` if missing).
- Set `status: completed`. Commit the note + log: `Run E-XXXX: <verdict>` (or
  `Run E-XXXX: invalid (flagged)`).
- **Report back only:** `experiment_validity`, `result` (effect + p-value),
  `verdict`. Not the log. Example:

  ```
  E-0007 — validity: valid | effect: risk diff +0.043 (95% CI 0.01–0.08), p = 0.011 | verdict: apoyada
  ```

### 8. Hand off the hypothesis transition to `update-confidence`

This skill does **not** touch hypothesis `status`, `history`, `_digest.md`, or
`Estado-del-arte.md`. It calls `update-confidence`:

- **Run started** (already done at the end of step 1): trigger
  `preregistrada → en_experimento`, payload = hypothesis id + experiment id.
- **After a valid analysis** (step 5 done, `experiment_validity: valid`): trigger
  `evidence result`, payload = hypothesis id, experiment id, the mechanical
  verdict (`apoyada` / `refutada` / `inconclusa`), and the effect estimate + CI
  (or SE) from `## Resultado`.
  `update-confidence` owns everything downstream: gathering *all*
  `experiment_validity: valid` linked experiments, checking `linea_publicacion`,
  running `combine_effects.py`, and deciding
  `apoyada` / `refutada` / `inconclusa` / `evidencia_mixta` (and spawning the
  moderator hypothesis on `evidencia_mixta`). Do not pre-compute that verdict
  here — one experiment's mechanical verdict is an **input**, not the outcome.
- **Invalid / flagged** (step 4 exhausted retries): pass **no** trigger. The
  experiment note carries `experiment_validity: invalid` + `needs_human_review:
  true`; the hypothesis stays where it is.
- **`secondary_hypotheses` (if any):** the `evidence result` trigger fires for the
  **primary `hypothesis:` only**. For each secondary hypothesis, record the
  observed collateral outcome under `## Evidencia colateral` in `## Resultado` —
  do **not** fire `update-confidence`, do **not** add this experiment to that
  hypothesis's `linked_experiment` (it belongs in `collateral_evidence:` at most).
  Non-confirmatory by construction.

## Common mistakes

- **Running despite a hash mismatch.** Pre-flight mismatch = stop, no execution.
- **Reasoning about significance in prose.** Call the script; copy its numbers and
  verdict.
- **Calling the wrong script for the frozen `analysis_plan`.** `frequentist` ->
  `two_proportion_test.py`; `bayesian` -> `bayes_factor_proportions.py`. The
  frozen field decides which one runs, not which one is more familiar.
- **Applying your own "does this support it?" judgment.** The frozen threshold
  decides, not you.
- **Editing a frozen section to reflect what actually happened.** New content goes
  in `## Resultado`; deviations go in `## Enmiendas`.
- **"Improving" the frozen design while coding it.** The code transcribes the
  frozen formulas exactly (step 0). A cleaner/better variant is not the frozen
  design.
- **Silently defaulting on an ambiguous frozen spec — especially under deadline.**
  Genuine ambiguity → dated `## Enmiendas` entry + a severity-tagged question to
  the researcher, then wait for the answer.
- **Flagging a `crítico` risk like a `menor` one.** A choice that can invalidate
  the run or burn the budget gets its own callout, not a bullet in a list.
- **Editing hypothesis `status` / `history` / `_digest.md` inline.** This skill
  never does that — it fires the `update-confidence` triggers
  (`preregistrada → en_experimento`, then `evidence result`).
- **Pre-computing the hypothesis verdict from one run.** The mechanical verdict is
  an input to `update-confidence`, which decides `apoyada` only after a second
  independent valid experiment.
- **Firing `evidence result` for a `secondary_hypotheses` entry.** Only the
  primary `hypothesis:` gets the trigger; secondary evidence is collateral, goes
  in `## Evidencia colateral`, and never moves a hypothesis's status.
- **Continuing to analysis with a leak.** A failed "No data leakage" check is
  invalidity — retry then flag, same as the other sanity checks.
- **Retrying a deterministic non-infra sanity failure forever.** 2 retries, then
  flag for human review.
- **Dumping the full log into the report.** Report validity + result + verdict;
  the log lives in the file.
- **Guessing the run command.** No unambiguous entrypoint → ask.
- **Cloning `vault/` or giving it a git remote to run on Kaggle/Colab.** The
  vault stays local. Ship a flat, self-contained bundle of just the experiment's
  own files (scripts + `E-XXXX.data.json` + lockfile + `MANIFEST.sha256`).
- **Running with a tool whose hash doesn't match the prereg**, or editing a
  tool's code "just to make it fit". Pre-flight mismatch = stop; a tool that
  can't express the frozen design is a `crítico` question.
- **Shipping a script that hard-codes a vault path.** Bundled scripts resolve
  inputs from `Path(__file__).parent`; a vault-tree dependency breaks the run on
  any external runtime.

## Related

- `Scripts/experiments/E-XXXX/` — the self-contained experiment code directory
  (scripts + frozen `E-XXXX.data.json` + `E-XXXX.deps.txt`); also the transfer
  bundle for external runtimes (step 2).
- `${CLAUDE_PLUGIN_ROOT}/scripts/analysis/two_proportion_test.py` — the
  `frequentist` mechanical test for step 5. `--help` documents the three-way
  verdict and thresholds.
- `${CLAUDE_PLUGIN_ROOT}/scripts/analysis/bayes_factor_proportions.py` — the
  `bayesian` mechanical test for step 5. `--help` documents the Bayes factor
  and verdict thresholds.
- `${CLAUDE_PLUGIN_ROOT}/scripts/paper_to_tool/tool_hash.py` — the pre-flight
  check of every `environment.tools` hash (step 1); tools are built by
  `paper-to-tool`.
