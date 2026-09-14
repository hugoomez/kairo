---
name: preregister-experiment
description: >-
  Use when a project's `propuesta` hypothesis is ready to be tested and needs
  its preregistration written and frozen. Turns the hypothesis claim + test
  sketch into a frozen `Experimentos/E-XXXX.md` note (exact prediction, metric
  + decision thresholds, control condition, environment manifest), records
  `frozen_at` / `frozen_commit`, and sets the experiment note `status` to
  `preregistered`. Two independent choices are fixed at freeze time: tier
  (`ligero` domain-judgement thresholds, or `completo` — required once
  `linea_publicacion: true` or estimated cost exceeds the project's
  `completo_cost_threshold` — a formal a-priori sample-size justification) and
  `analysis_plan` (`frequentist` or `bayesian`, each computed via a versioned
  script). Produces the note only — runs nothing.
---

# Preregister Experiment

## Overview

Writes and **freezes** a preregistration for one hypothesis, at whichever
tier (`ligero` / `completo`) this run determines and whichever
`analysis_plan` (`frequentist` / `bayesian`) the researcher chooses — the two
are independent axes; any combination is valid.

Binding rule (Kairo principle 3): the preregistration is **the
prediction plus the analysis plan**, frozen **before any experiment code runs**.
Once the experiment's code starts, the preregistration is **immutable** — later
changes go only into `## Enmiendas`, never as edits to the original sections.

This skill produces the frozen note and nothing else. It does not run, schedule,
or scaffold experiment code.

## When to use

- A hypothesis is `status: propuesta` and the team wants to test it now.
- You have a test sketch (from `hypothesis-cycle`) to harden into an exact,
  falsifiable protocol.

**When not to use:** the hypothesis isn't `propuesta` yet; you want to execute or
analyze a run (separate skills).

## Inputs

1. A **`propuesta`** hypothesis note (`Projects/<slug>/Hipotesis/H-XXXX.md`).
   Refuse if its `status` is anything else.
2. Its **test sketch** — the manipulation/comparison, what gets measured, what
   result counts against the claim.

## Scope — `ligero` and `completo` tiers

**Both tiers:** exact prediction, one (or few) pre-committed primary metric(s)
with three-way decision thresholds, a control condition tied to a known
result, and a reproducibility manifest.

**`ligero`:** thresholds (`T_apoyo` / `T_refuta`) are picked by domain
judgement — no power calculation.

**`completo`:** required whenever `hypothesis.linea_publicacion == true`, or
`cost_estimated` exceeds the project's `completo_cost_threshold` (same units
— a unit mismatch, a non-numeric value, or an unset threshold means cost
cannot trigger it on its own; see step 1a). Thresholds are instead derived
from a formal a-priori sample-size justification: state the smallest effect
size of interest (SESOI), alpha, and target power; the required N is computed
mechanically (step 1b), never picked first and rationalized after. **Hard
floor — no override.** Once `completo` is required, there is no in-spec way
to freeze at `ligero` anyway; an unresolved requirement blocks the freeze
exactly like an open `crítico` risk (see "Flagging risks and ambiguities").

**Independent of tier — `analysis_plan`:** `frequentist` (the existing
mechanical test) or `bayesian` (a Bayes factor, computed via a versioned
script). Chosen once, at step 1c, regardless of tier.

## Procedure

### 1. Determine tier and analysis plan

Before fixing the protocol (step 3), settle both axes — they are independent
and each is recorded in frontmatter.

**a. Tier.**

```
completo required  <=>  hypothesis.linea_publicacion == true
                        OR (cost_estimated is set AND project.completo_cost_threshold
                            is set AND both are bare numbers in the same unit AND
                            cost_estimated > completo_cost_threshold)
```

- If `cost_estimated` is set but the project has no `completo_cost_threshold`,
  or the two values are in different units (e.g. one is GPU-h, the other a
  currency figure), or either isn't a bare number: cost cannot force
  `completo` on its own. Flag `importante` — say explicitly that only
  `linea_publicacion` was checked.
- If neither condition holds, default to `ligero`. The researcher may still
  opt into `completo` voluntarily for a smaller experiment.
- Set `tier: ligero` or `tier: completo` in frontmatter now.

**b. If `completo`: run the sample-size justification.**

State, with the researcher: the smallest effect size of interest (SESOI, as
two proportions `p1`/`p2` — baseline and the smallest treatment rate that
would still count as meaningful — or directly as Cohen's `h`), alpha, and
target power. Then call:

```
python ${CLAUDE_PLUGIN_ROOT}/scripts/analysis/sample_size.py \
    --p1 <baseline> --p2 <smallest meaningful> --alpha <alpha> --power <power> --json
```

(or `--h <h>` if the effect is given directly, e.g. for a design that isn't
naturally two proportions). Take `n_per_group` / `n_total` verbatim — this
becomes the stopping rule's fixed N in step 3b, replacing a
domain-judgement-chosen figure. Record the exact command, its output, and the
SESOI/alpha/power inputs in `## Plan de análisis` (step 3b).

**c. Analysis plan.** Ask the researcher: `frequentist` (the existing
`two_proportion_test.py` mechanical test — default) or `bayesian` (a Bayes
factor via `scripts/analysis/bayes_factor_proportions.py`). Set
`analysis_plan: frequentist` or `analysis_plan: bayesian` in frontmatter.

- **`frequentist`:** step 3b states alpha and the min/floor effect thresholds,
  as today.
- **`bayesian`:** step 3b states the prior (`Beta(a, b)`, default `Beta(1, 1)`
  — uniform — unless a different prior is justified) and the BF decision
  threshold (`--bf-threshold`, default 3.0, "substantial evidence" — Kass &
  Raftery 1995) instead of alpha/min-effect.

**d. Confidence-field semantics — state this now, not later.** If `bayesian`
is chosen: `confidence` on the hypothesis note is a real posterior **only**
once *every* experiment adjudicating it used `analysis_plan: bayesian` — a
single frequentist-plan experiment among them keeps `confidence` a
`frequentist_heuristic`, never silently upgraded. Say this explicitly to the
researcher when they pick `bayesian`, don't leave it to the template comment.

**e. Mixed bayesian + linea_publicacion — flag, don't solve.** If
`analysis_plan: bayesian` AND `hypothesis.linea_publicacion: true` both hold:
flag `crítico`. `update-confidence`'s replication combiner
(`combine_effects.py`) works on effect-size + standard error and has **no**
defined way to combine two Bayes factors — a second independent Bayesian
replication will hit a combination step that doesn't exist yet. This blocks
the freeze until the researcher explicitly acknowledges it (e.g. by switching
to `frequentist`, or by accepting that a future combination method is not yet
built). Do not attempt to invent a combination method here — that is a
separate, unscoped piece of work.

### 2. Create the note

Copy `${CLAUDE_PLUGIN_ROOT}/templates/experiment-template.md` to
**`Projects/<slug>/Experimentos/E-XXXX.md`** — next `E-XXXX` id (scan all
`Projects/*/Experimentos/*.md` frontmatter `id:`, max + 1, zero-pad 4).

Frontmatter now: `hypothesis: <H-XXXX>` (the **one** hypothesis this prereg
adjudicates), `project: <PROJ-XXX>`, `tier` and `analysis_plan` (both from
step 1), `status` left at placeholder until step 4. If the same design also
bears on a **rival / sibling** hypothesis without adjudicating it, list those
under `secondary_hypotheses: [<H-YYYY>]` and write a `## Evidencia colateral`
entry for each (step 3e). Leave `sanity_checks`, `experiment_validity`,
`cost_actual`, `result` as placeholders — a later run/analysis skill fills
them.

### 3. Fix the protocol

All four must be exact and unambiguous before freezing.

**a. Exact prediction** → `## Predicción`
Quantitative statement of what will be observed **if the hypothesis is true** —
direction and rough magnitude, in the metric's units. Not "X improves Y" but "X
raises Y by at least <amount> relative to control".

**b. Metric + decision thresholds** → `## Plan de análisis`
Name the **primary metric(s)** (prefer one). Pre-commit the three-way rule:

| Verdict (`result.verdict`) | Condition on the primary metric |
|---|---|
| `apoyada` | effect in the predicted direction and ≥ the pre-set meaningful size `T_apoyo` (or, `bayesian`: `BF10 ≥` the frozen `--bf-threshold` in the predicted direction) |
| `refutada` | no effect, or effect ≤ `T_refuta` / wrong direction (or, `bayesian`: `BF01 ≥` the frozen `--bf-threshold`) |
| `inconclusa` | between `T_refuta` and `T_apoyo`, or the interval spans both (or, `bayesian`: neither BF crosses the threshold) |
| `evidencia_mixta` | **only if** the plan names >1 primary metric and they land in conflicting regions |

State the estimator and the interval you'll report (e.g. mean difference + 95%
CI, or `bayesian`: `BF10`/`BF01` + the prior).

- **`ligero`:** thresholds are domain judgement, fixed now, no power calc.
- **`completo`:** state the SESOI, alpha, target power, and
  `sample_size.py`'s `n_per_group`/`n_total` output (step 1b) — `T_apoyo`
  equals the stated SESOI, not a separately chosen figure.
- **`bayesian`:** state the prior (`Beta(a, b)`) and the `--bf-threshold`
  instead of alpha/min-effect.

**Stopping rule (required).** State the *exact* condition under which the run /
data collection ends, decided **now**, not during the run. For `completo`,
this **is** the `n_per_group`/`n_total` from step 1b (e.g. `fixed N = 197 per
arm`) — not a separately chosen figure. For `ligero`, e.g. `fixed N = 2000
per arm`, `fixed 5 seeds`, `runs until the wall-clock budget of 6 GPU-h is
spent`, `one pass over the frozen dataset`. "Until the result looks clear" is
not a stopping rule.

**Variables recorded (required)** → the template's `## Variables` section, split
into two lists:

- **Primarias** — the metric(s) named above that drive the three-way verdict.
  Nothing else may be promoted to a verdict input after the fact.
- **Secundarias / exploratorias** — everything else that will be logged
  (diagnostics, ablation metrics, per-subgroup breakdowns). Recorded for context
  and future hypotheses; **never** used for this experiment's verdict.

Listing both closes the selective-reporting loophole without a power analysis: a
result that only appears in a secondary variable is a new hypothesis, not this
one's outcome.

**c. Control condition + known result** → `## Diseño`
Define the control arm and **the specific published/established result it must
reproduce** (cite `P-XXXX §…`), within a stated tolerance. This is the
experiment's sanity anchor and feeds `## Umbral de invalidez`: if the control
fails to reproduce that result, the run is `experiment_validity: invalid` and
verdicts are not read.

**d. Environment manifest** → `environment:` frontmatter + `## Manifiesto de entorno`
(see next section).

**e. Collateral evidence (only if `secondary_hypotheses` is non-empty)** →
`## Evidencia colateral`
For each secondary hypothesis: which **secondary / exploratory** observation of
this design touches it, what that observation would suggest, and **why this
design cannot adjudicate it** — no decision threshold, no verdict, no dedicated
replication. The frozen `## Predicción` and `## Plan de análisis` (thresholds,
stopping rule, verdict map) cover the **primary hypothesis only**. A secondary
hypothesis never gets a `T_apoyo` / `T_refuta` (or BF threshold); if you find
yourself wanting to set one, it is not secondary — make it the primary of its
own preregistration.

### 4. Freeze

Only once a–d (and e, if `secondary_hypotheses` is non-empty) are complete and
exact, **and no `crítico` risk is still open** (see "Flagging risks and
ambiguities" — this now includes an unmet `completo` requirement and an
unacknowledged bayesian+linea_publicacion gap, step 1e). An unresolved
`crítico` ambiguity blocks the freeze — resolve it with the researcher first.

1. `environment.seed`, `dependencies_hash`, `dependencies_lockfile`,
   `dataset_hash`, `hardware` all filled (step 3d).
2. Set `frozen_at` = current UTC timestamp, ISO 8601 (`YYYY-MM-DDTHH:MM:SSZ`).
3. Set `frozen_commit` = current commit hash (`git rev-parse HEAD`). Use the
   **experiment-code** repo's HEAD if code lives in its own repo; otherwise the
   vault HEAD, and say which in `## Manifiesto de entorno`.
4. Set `status: preregistered`.
5. Commit the note: `Preregister E-XXXX (<H-XXXX>)`. The preregistration is not
   frozen until it is in git.

### 4b. Hand off the hypothesis transition to `update-confidence`

A frozen prereg with a dangling link is a data-integrity gap — but this skill
does **not** edit hypothesis `status`, `linked_experiment`, `history`, or
`_digest.md`. Once the note is committed (step 4, item 5), **invoke
`update-confidence`** — trigger `prereg frozen`, payload = the **primary**
hypothesis id + this experiment id. It moves that hypothesis
`propuesta → preregistrada`, appends `E-XXXX` to its `linked_experiment`, appends
the `history` entry, and regenerates `_digest.md`.

**Secondary hypotheses do not transition.** For each id in
`secondary_hypotheses`, this skill appends `E-XXXX` to that note's
`collateral_evidence:` list directly (it is not a status-linked field, so
`update-confidence` is not involved) and adds no `history` entry. Their `status`
is untouched.

### 5. Stop

Do not run, schedule, or scaffold anything. Output is the frozen note plus the
`update-confidence` handoff. Writing the experiment code is `run-experiment`
step 0 ("Implement the frozen design — literally"), which happens after this
freeze.

## Environment manifest

Fill `environment:` in frontmatter; record how each value was produced in
`## Manifiesto de entorno`.

| Field | How |
|---|---|
| `seed` | the single integer seeded everywhere (framework, data shuffling, sampling). |
| `dependencies_lockfile` | run `pip freeze` (Python) or `npm ls --json` (Node); save output next to the note as `E-XXXX.deps.txt` / `.json`; put that relative path here. |
| `dependencies_hash` | `sha256` of that saved snapshot file. |
| `dataset_hash` | `sha256` of the dataset file. Multiple files → list per-file `sha256` in the body and put the hash of the sorted-hash manifest here. No dataset → `n/a`. |
| `hardware` | one line, e.g. `1x RTX 4090 24GB, 32 GB RAM` or `MacBook Pro M2, CPU only`. |

`## Manifiesto de entorno` records: exact commands run and when, per-file dataset
hashes, the snapshot file paths, and which repo `frozen_commit` refers to.

## Flagging risks and ambiguities — with a severity

Any risk or open question you raise with the researcher during design (or while
writing code — `run-experiment` step 0 uses the same vocabulary) carries an
explicit severity. A choice that can burn the whole compute budget must not be
presented with the same weight as a trivial one.

| Tag | Meaning | What it forces |
|---|---|---|
| **`crítico`** | can invalidate the entire experiment or spend the full compute budget with no readable result — e.g. a control that won't reproduce the cited result, a model/architecture detail that could suppress the effect being measured, a stopping rule open to interpretation, a primary metric that doesn't actually measure the claim, a threshold with no `inconclusa` band, an unmet `completo` requirement, an unacknowledged bayesian+linea_publicacion combination gap | **Blocks the freeze.** Present it in its **own callout at the top** of what you show the researcher — never a bullet among minor items. The design is not frozen until the researcher gives an explicit decision on it. If found *after* freeze: `## Enmiendas` entry + a direct `crítico`-tagged question before any run. |
| **`importante`** | plausibly shifts the result or its interpretation, but the experiment stays readable either way — a defensible-but-contested hyperparameter, an estimator choice, a borderline exclusion criterion, a cost-based `completo` trigger that couldn't be evaluated (missing/mismatched threshold) | Flag prominently with a proposed default + rationale. Get a decision before freezing if the researcher is available; otherwise freeze on the stated default and note the choice in `## Manifiesto de entorno`. |
| **`menor`** | implementation detail, low impact either way — activation function where the design doesn't turn on it, logging cadence, variable naming, RNG library when results are seed-identical | State the choice in one line. No decision needed, no freeze block. |

Never fold a `crítico` risk into a list next to `menor` ones. "This control may
not reproduce the cited baseline result and the run comes back invalid" is not the same kind of
sentence as "ReLU or GELU?" — and must not look like it.

## After freeze — amendments only

Once `status: preregistered` (and absolutely once code has run), the original
sections — `## Predicción`, `## Plan de análisis` (including the stopping rule),
`## Variables` (including the primary/secondary split), `## Diseño`,
`## Umbral de invalidez`, `## Manifiesto de entorno`, and the `environment` /
`frozen_*` / `tier` / `analysis_plan` frontmatter — are **immutable**.

Every later change goes in a `## Enmiendas` section, append-only:

```markdown
## Enmiendas

### 2026-09-12 — <qué cambió>
**Motivo:** <por qué>
**Afecta a:** <sección / campo>
**Antes → después:** <resumen>
```

Never silently edit a frozen section. An amendment after code has run is
disclosed as such when results are reported.

## Frontmatter at freeze

- `id`, `hypothesis` (primary — exactly one), `secondary_hypotheses` (`[]` or a
  list, each with a `## Evidencia colateral` entry), `project`,
  `tier: <ligero | completo>` (step 1a), `analysis_plan: <frequentist | bayesian>`
  (step 1c), `status: preregistered`
- `frozen_at` (ISO 8601 UTC), `frozen_commit` (sha)
- `environment.seed`, `.dependencies_lockfile`, `.dependencies_hash`,
  `.dataset_hash`, `.hardware`
- `experiment_validity`, `sanity_checks.*`, `cost_actual`, `result.*` — left as
  placeholders for the run/analysis skill
- `cost_estimated` — a rough figure if the sketch supports one, else placeholder
  (also the input to the `completo`-tier cost trigger in step 1a)

## Not in scope

- Running, scheduling, or scaffolding experiment code (that's `run-experiment`).
- Filling sanity checks, validity, actual cost, or results (also
  `run-experiment`).
- Combining two Bayesian-plan replications for the `update-confidence`
  replication gate (flagged, not solved — step 1e).

## Common mistakes

- **Freezing before the protocol is exact.** "Improves accuracy" is not a
  prediction; give direction + magnitude + units.
- **No `inconclusa` band.** The decision rule must have three regions, not a
  single pass/fail cut.
- **Control with no named known result.** The control must reproduce a specific
  cited result, or it can't anchor validity.
- **Editing a frozen section.** After `status: preregistered`, changes go in
  `## Enmiendas` — always.
- **Recording `frozen_commit` from the wrong repo.** It's the experiment *code*
  state; say which repo in the manifest.
- **Picking domain-judgement thresholds under `completo`.** `completo` requires
  the sample-size script's output (step 1b); `T_apoyo` is the stated SESOI, not
  a separately chosen figure.
- **Skipping the sample-size justification because `linea_publicacion` was set
  after the fact.** Check step 1a *before* fixing thresholds — retrofitting a
  `completo` justification onto already-chosen `ligero` thresholds is not the
  same as deriving them from the SESOI/alpha/power calc.
- **No stopping rule.** An open-ended run is optional-stopping. Commit a fixed N /
  seed count / budget in `## Plan de análisis` now.
- **Un-split variables.** Every variable that will be recorded is declared *now*
  as primary (verdict-driving) or secondary/exploratory. A result found only in a
  secondary variable is a new hypothesis.
- **Editing hypothesis `status` / `linked_experiment` inline.** This skill never
  does — fire `update-confidence`'s `prereg frozen` trigger (step 4b).
- **Running the experiment.** This skill stops at the frozen note.
- **Giving a `secondary_hypotheses` entry a decision threshold.** Secondary =
  collateral, non-adjudicating. Thresholds and a verdict map exist for the
  primary `hypothesis:` only; a secondary hypothesis that needs its own threshold
  needs its own preregistration.
- **Listing more than one primary `hypothesis:`.** One prereg adjudicates one
  hypothesis. Rival-pair designs put the other side in `secondary_hypotheses`.
- **Flagging a budget-burning risk like a trivial one.** Every risk raised gets a
  `crítico` / `importante` / `menor` tag; a `crítico` gets its own callout and
  blocks the freeze until the researcher decides.
- **Treating `confidence` as a real posterior under a frequentist plan, or under
  mixed evidence.** It is a real posterior only once every adjudicating
  experiment used `analysis_plan: bayesian` (step 1d) — otherwise it stays a
  `frequentist_heuristic`.
- **Letting `bayesian` + `linea_publicacion` through without the step-1e flag.**
  There is no combination method yet for two Bayesian replications — say so at
  freeze time, don't discover it at the second experiment.

## Related

- `${CLAUDE_PLUGIN_ROOT}/scripts/analysis/sample_size.py` — a-priori power
  analysis for the `completo` tier (step 1b). `--help` documents the formula.
- `${CLAUDE_PLUGIN_ROOT}/scripts/analysis/two_proportion_test.py` — the
  `frequentist` mechanical test, run by `run-experiment` step 5.
- `${CLAUDE_PLUGIN_ROOT}/scripts/analysis/bayes_factor_proportions.py` — the
  `bayesian` mechanical test, run by `run-experiment` step 5.
- `update-confidence` — the `prereg frozen` trigger this skill fires (step 4b);
  also owns the replication combiner that cannot yet combine two Bayes factors
  (step 1e).
- `hypothesis-template.md` — the `confidence` field's `frequentist_heuristic` /
  `bayesian_posterior` kinds (step 1d).
