---
name: pitfall-audit
description: Use before any evidence-based hypothesis status transition — update-confidence runs it on every adjudicating experiment before it decides anything. Audits four pitfalls of automated research (Luo et al., NeurIPS 2025) — inappropriate benchmark, data leakage, metric misuse, post-hoc selection — against the frozen preregistration and the full-trace index. A crítico finding blocks the transition and sets needs_human_review on the hypothesis; it never changes status. Also runs on request as a dry run over past experiments.
---

# Pitfall audit

> **Profile.** Before anything else run `python "${CLAUDE_PLUGIN_ROOT}/scripts/models/kairo_profile.py" check pitfall-audit`; exit 3 means the `literatura` profile is active (`KAIRO_PROFILE`): give the researcher its one-line message and stop.

## Overview

Luo, Kasirzadeh & Shah, *The More You Automate, the Less You See: Hidden
Pitfalls of AI Scientist Systems* (NeurIPS 2025 AI4Science; arXiv:2509.08713;
`github.com/niharshah/AIScientistPitfalls`) name four failure modes that stay
invisible when a reviewer reads only the final write-up and show up when the
trace logs and code are read too:

| Pitfall (Luo et al.) | What goes wrong | Check here |
|---|---|---|
| Inappropriate benchmark selection | the evaluation data or task is chosen because it is easy or available, not because it tests the claim | **(a) benchmark** |
| Data leakage | test information reaches training, validation or model selection, including subsampled or synthetic data that is not disclosed | **(b) leakage** |
| Metric misuse | a metric other than the stated one is reported, or it is computed differently from how it was specified | **(c) metric** |
| Post-hoc selection bias | many candidates or runs are tried and only the favourable ones are reported, with test performance steering the choice | **(d) selection** |

Kairo already freezes the design before any code runs. This audit checks that
the result that is about to move a hypothesis is the one the frozen design
promised. The mechanical checks are in `scripts/audit/pitfall_audit.py`. The
judgement parts are written by this skill, and each one carries a severity.

Binding rules:

- **update-confidence is still the only writer of hypothesis `status`.** The
  audit never changes status, not even on a crítico finding.
- **A `crítico` finding blocks the transition.** With `--apply`, it also sets
  `needs_human_review: true` on the hypothesis, and changes nothing else in the note.
- Frozen preregistrations are immutable. The audit reads them and never edits them.
- Severity tags use the same vocabulary as `run-experiment` and
  `preregister-experiment`: **`crítico`**, **`importante`**, **`menor`**.
  Every finding carries one tag and a location.

## When to use

- **Always** from `update-confidence`, before any evidence edge (`evidence
  result`, and any other edge whose evidence is an experiment result), on
  **every** experiment in `evidence_gate.py gather`'s `eligible` list. Run it
  after `evidence_gate.py check` and before `combine_effects.py`.
- On request, as a **dry run** (the default mode) over past experiments. The
  retroactive audit of a vault's history is one example.

**When not to use:** as a replacement for `run-experiment`'s sanity checks.
Validity is still decided there. The audit also cannot rescue an invalid
experiment, and an invalid experiment never reaches an evidence edge.

## Inputs

| Input | Where it comes from | If missing |
|---|---|---|
| experiment note `E-XXXX.md` | frozen preregistration | error (exit 1) |
| hypothesis note `H-XXXX.md` | the hypothesis being updated | error (exit 1) |
| trace index `Experimentos/trazas/index.jsonl` | `run-experiment` (`scripts/traces/trace_index.py`) | **crítico** (selection cannot be checked) |
| analysis record `Experimentos/trazas/E-XXXX.analysis.json` | `run-experiment` step 5 | **crítico** (metric cannot be checked) |
| plan `E-XXXX.data.json` (`runs[]` with `run_index`, `run_seed`; `task`) | frozen data manifest, default `Experimentos/E-XXXX.data.json`; pass `--plan` if it lives elsewhere | planned-run coverage is not checked |
| claim setup JSON | written by this skill from `## Claim` | `importante` |
| design setup JSON | written by this skill from the frozen `## Variables` / `## Diseño`; the manifest's `task` is read automatically | facets missing from the design → `importante` |
| split ids JSON | exported by the experiment code (or regenerated) | `menor`: overlap is not re-verified, and the recorded sanity check is read instead |
| judgement JSON | written by this skill (step 2) | `importante`: "benchmark judgement not recorded" |

**Analysis record** (`kairo/analysis@1`), written by `run-experiment` next to the trace:

```json
{"schema": "kairo/analysis@1", "experiment": "E-XXXX", "reconstructed": false,
 "script": "two_proportion_test.py@1.0.0",
 "primary_metric": {"name": "...", "cell": {"<factor>": <value>, ...}, "estimator": "median",
                    "interval": {"level": 95, "resamples": 10000, "seed": 1},
                    "thresholds": {"T_apoyo": 2.0, "T_refuta": 5.0}, "value": 0.0, "ci": [0, 0]},
 "reported_metric": {... the metric the verdict actually used; defaults to primary_metric ...},
 "runs_in_analysis": ["<run_id>", ...],
 "cells": {"<cell label as in the trace>": {"n_completed": 6}},
 "parameters_used": {"<name>": <value> | {"value": <v>, "chosen_on": "frozen|train|validation|test",
                                          "chosen_at": "<ISO UTC>"}}}
```

`parameters_used` lists every constant the analysis applies: thresholds, the
stop rule's constants, r\*, the bootstrap seed and so on. A `reconstructed: true`
record was built after the fact from `## Resultado`. In a dry-run retro it is
reported as `menor`; with `--apply` (a live transition) it is `crítico`.

**Setup JSON** (claim and design): `{"<facet>": {"value": <v>, "source": "<note ## Section ('quote')>", "severity": "crítico|importante|menor"}}`.
The claim side may use `{"min": n}`, `{"max": n}`, `{"any_of": [...]}`,
`{"includes": [...]}` or `"any"`. A `task` mismatch is `crítico` by default,
and any other mismatch is `importante` unless the facet sets its own severity.

**Judgement JSON**: `[{"check": "benchmark|leakage|metric|selection", "severity": "...", "location": "...", "message": "...", "by": "..."}]`.

## Procedure

### 1. Build the setups — only what the notes say

Read the hypothesis `## Claim` and the experiment's frozen `## Variables`,
`## Diseño` and `## Plan de análisis`. Write one facet per setup property the
claim states in words: task, modulus / dataset, model family and size, optimiser,
seeds, precision, training horizon, the data regime, and how any threshold such
as r\* is fixed. Quote the phrase it comes from in `source`. Never add a facet
the claim does not state. If something is implied rather than stated, leave it
to the judgement in step 2. If the design does not state a facet, leave it out
of the design setup, and the script reports it.

### 2. Write the judgement — never pass silently

Answer the question: **is this task, data and model the right test of the claim
as it is worded?** Answer it for (a), and for anything in (b)–(d) that the script
cannot see: a stopping rule that cannot fire before the effect, a metric that
cannot measure the claim, a pilot that chose the instrument. Every item goes in
the judgement file with a severity:

- **`crítico`**: the benchmark cannot adjudicate the claim. Examples: a
  different task, a model detail known to suppress the effect under test, or a
  metric that is 0 by construction.
- **`importante`**: the benchmark tests a narrower or different version of the
  claim. Examples: fewer seeds than the claim states, or a precision or horizon
  the claim requires that the design lacks.
- **`menor`**: a deviation the result does not depend on.

Also write an entry when you find nothing. A `menor` entry that says "no
benchmark concern: <why>" is a judgement. An empty file is not.

### 3. Run the audit

```
python ${CLAUDE_PLUGIN_ROOT}/scripts/audit/pitfall_audit.py audit \
    --experiment <Projects/<slug>/Experimentos/E-XXXX.md> \
    --hypothesis <Projects/<slug>/Hipotesis/H-XXXX.md> \
    --claim-setup <tmp>/claim.json --design-setup <tmp>/design.json \
    --judgement <tmp>/judgement.json [--splits <tmp>/splits.json] [--plan <E-XXXX.data.json>] \
    [--apply] --json --out <tmp>/pitfall-E-XXXX.json
```

Keep the temp files outside the vault. `update-confidence` passes `--apply`.
A retro or exploratory audit leaves it off (dry run).

What the script checks mechanically:

- **(a) benchmark**: each claim facet is compared with the design facet. The
  frozen manifest's `task` (name, modulus) overrides the design file.
- **(b) leakage**: the recorded `no_data_leakage` sanity check (false →
  `crítico`), and every pairwise intersection of the train / val / test ids
  (any overlap → `crítico`). For information from the future: a run that
  started before `frozen_at` (`crítico`); an analysis parameter whose value is
  not in the frozen sections, or that was chosen on validation/test data after
  the freeze (`crítico`); an analysis amendment dated after the first run
  (`importante`, or `menor` when it falls on the same day).
- **(c) metric**: the reported metric equals the primary one (name, cell,
  estimator); every cell value is in the frozen primary metric, and a value
  found only in the secondary list is `crítico`; thresholds and interval
  constants are as frozen; the script matches the frozen `analysis_plan`.
- **(d) selection**: the trace verifies (hash chain), every planned run is
  traced, and every seed matches the plan. A run left out of the analysis with
  no preregistered rule is `crítico`, and so is one whose cited rule does not
  exist or postdates the run. A run still `running` is `crítico`, and so is a run
  outside the plan that entered the analysis. `runs_in_analysis` / `cells` must
  equal the trace. Unarchived outputs and conditions with no completed run are
  `importante`.

### 4. Read the exit code

| Exit | Meaning | What happens |
|---|---|---|
| `0` | no `crítico` | `update-confidence` continues. `importante` / `menor` findings go into the transition's report and the `history` evidence line (`pitfall-audit: 0 crítico, N importante`). |
| `3` | at least one `crítico` | **Blocked.** No transition, no `history`, no digest. With `--apply` the hypothesis gets `needs_human_review: true`. Report every crítico (check, location, message) to the researcher. |
| `1` | an input could not be read | Not clean. Treat it like `3` without writing the flag: fix the input and re-run. Never "continue anyway". |

## Blocking semantics

- A `crítico` finding **blocks** the evidence transition. It never moves the
  hypothesis to `refutada`, `inconclusa` or anywhere else. The audit is not
  evidence about the claim.
- With `--apply`, it sets `needs_human_review: true` on the hypothesis through
  `set_needs_human_review` (`pitfall_audit.py flag-review --hypothesis <H>` by
  hand). That call touches that single frontmatter line, keeping its comment and
  line endings, and inserts the line if it is absent. It never touches `status`.
- **After review:** the fix goes where the rules put it. A missing trace entry
  becomes a new trace entry (`trace_index.py correct` / `append`, never an edit).
  A design problem gets a new preregistration. A frozen-section error gets an
  `## Enmiendas` entry. Once the fix is in, re-invoke `update-confidence`,
  which runs the audit again. Only the researcher clears `needs_human_review`.
  If the researcher judges a crítico to be a false positive, they may authorise
  the transition explicitly. That decision is recorded in `history` as
  `override humano de pitfall-audit (<fecha>): <motivo>`.

## Failure modes this audit still has

- **It only runs at an evidence edge.** An experiment that comes back invalid
  never reaches one, so the audit never runs on it. Run it as a dry run on
  request.
- **It runs after the compute is spent.** It can stop a wrong transition, but
  it cannot save the budget. The benchmark judgement (step 2) is worth doing at
  freeze time too.
- **Backfilled traces are only as good as their sources.** Unknown times,
  attempts and hashes stay `unknown`, and the report says what could not be
  ordered or verified.
- **Number matching is textual.** A parameter "appears in the frozen text" when
  the same number is written there. It does not show that the number is used in
  the same role. The judgement covers that.
- **Code fidelity is not checked.** The audit does not check whether the
  experiment code implements the frozen design, for example a model rewritten
  from the text instead of copied from a validated pilot. That belongs to
  `run-experiment` step 0 and to the code sha in `## Enmiendas`.

## Common mistakes

- **Running the audit without the judgement file.** The script then reports
  `importante: benchmark judgement not recorded`. That line is a visible gap,
  not a pass.
- **Writing a claim facet the claim never states**, or copying the design
  into the claim setup so that they match. The claim side comes only from
  `## Claim`.
- **Treating exit `1` as clean.**
- **Letting a crítico change status.** It blocks, and it sets
  `needs_human_review`. Nothing else.
- **Editing the trace to make selection pass.** The trace is append-only, and
  a correction is a new entry. `verify` catches edits, deletions and reorders.

## Related

- `${CLAUDE_PLUGIN_ROOT}/scripts/audit/pitfall_audit.py`: the mechanical checks
  and the `needs_human_review` helper.
- `${CLAUDE_PLUGIN_ROOT}/scripts/traces/trace_index.py`: the full-trace index
  (`start` / `end` / `correct` / `verify`).
- `update-confidence`: the caller. `run-experiment` writes the trace and the
  analysis record.
- `docs/v3-interfaces.md` §4: the contract for the trace and the audit.
