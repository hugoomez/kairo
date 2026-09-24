---
name: update-confidence
description: Use when a hypothesis's status must change — a prereg was frozen, a run started, or a valid experiment produced a verdict — and the change plus its bookkeeping (history entry, _digest.md, Estado-del-arte.md) must be applied consistently. The single writer of hypothesis `status` after `propuesta`.
---

# Update Confidence

## Overview

This skill owns every hypothesis `status` transition after a hypothesis is first
proposed. It implements **exactly** the state machine below — **no other states,
no other transitions**. Other skills (`preregister-experiment`, `run-experiment`,
`hypothesis-cycle`) reach it through the triggers in **Callers** below; they never
edit hypothesis `status` themselves. (Initial creation of a note at
`status: propuesta` — by `hypothesis-cycle`, `create-project`, or
`spawn-hypothesis` — is not a transition and is not owned here.)

Encoded rules:

- Kairo principle 2 — a hypothesis reaches **`apoyada` only after a second
  independent valid experiment** also supports the threshold.
- **Never average two disagreeing experiments into a forced clean verdict.**
  Disagreement → `evidencia_mixta`.
- Evidence is **combined by a versioned script**, never by eyeballing.
- **No transition to `apoyada` without a fresh verification** that found no
  errors — see "Fresh-verification gate before `apoyada`". The verifier can
  only block; it never moves `status` anywhere.

## When to use

Always invoked by a **Caller** (see that section for the exact trigger + payload):

- `preregister-experiment` froze a prereg (trigger `prereg frozen`) →
  `propuesta → preregistrada`.
- `run-experiment` started a run (trigger `preregistrada → en_experimento`) →
  `preregistrada → en_experimento`.
- `run-experiment` recorded a **valid** verdict (trigger `evidence result`) →
  resolve the `en_experimento` edges.
- `hypothesis-cycle` overflowed / a queue was drained (triggers `budget
  overflow` / `budget freed`) → `propuesta ↔ en_cola`.

**When not to use:** changing a terminal verdict (`apoyada` / `refutada` /
`inconclusa` / `evidencia_mixta` have no outgoing edges — pursue the question with
a new child hypothesis instead); anything an invalid experiment produced (invalid
experiments never move the state).

## The state machine — exact

| From | To | Trigger |
|---|---|---|
| `propuesta` | `en_cola` | parked for budget (generation-order queue) |
| `en_cola` | `propuesta` | dequeued when budget frees |
| `propuesta` | `preregistrada` | a prereg `E-XXXX` was frozen for it |
| `preregistrada` | `en_experimento` | its experiment run started |
| `en_experimento` | `refutada` | **one** valid experiment's verdict is `refutada`. **If `linea_publicacion: true`**, a refutation also needs replication — the first refuting run stays `en_experimento` (pending), a second independent valid refuting run makes it `refutada`. |
| `en_experimento` | `en_experimento` | **first** valid experiment's verdict is `apoyada` — stays here, replication pending; add a `history` entry noting the first support |
| `en_experimento` | `apoyada` | a **second independent** valid experiment's verdict is also `apoyada` and the combination is consistent |
| `en_experimento` | `evidencia_mixta` | two valid experiments disagree — opposing verdicts, or the combination script flags `conflicting` / `heterogeneous` / `borderline` (see the k = 2 heterogeneity caution) |
| `en_experimento` | `inconclusa` | the available valid experiments neither clearly support nor refute |

No transition fires more than once per invocation. Refuse any pair not in this
table.

## Inputs

- The hypothesis note (`Projects/<slug>/Hipotesis/H-XXXX.md`) — its `status`,
  `linea_publicacion`, `linked_experiment(s)` (adjudicating experiments only;
  `collateral_evidence` is not an evidence input).
- The triggering event, and for evidence edges the relevant `E-XXXX` note(s) with
  `experiment_validity` and, from `## Resultado`, each verdict + effect estimate +
  CI (or SE).

## Deciding an evidence transition

1. Confirm the hypothesis is `en_experimento`. Gather **only
   `experiment_validity: valid`** experiments that **adjudicate** it — the ones
   in its `linked_experiment` list, i.e. where this hypothesis is the experiment's
   primary `hypothesis:`. An experiment that lists this hypothesis only under
   `secondary_hypotheses:` (tracked in the hypothesis's `collateral_evidence:`)
   is **never** gathered here and never enters `combine_effects.py`. Take each
   adjudicating experiment's mechanical verdict (`apoyada` / `refutada` /
   `inconclusa`) and effect + CI/SE from `run-experiment`.
2. **"Independent" replication** = a distinct `E-XXXX`, a **different seed**, and
   not reusing the identical dataset snapshot where that is avoidable. A byte-for-
   byte re-run is *not* independent — stop and ask.
3. Apply:

**First valid experiment** (hypothesis at `en_experimento`):

| Verdict | Result |
|---|---|
| `refutada` | `refutada` — unless `linea_publicacion: true`: stay `en_experimento`, history "primera refutación, replicación pendiente" |
| `apoyada` | stay `en_experimento` (self-loop), history "primer apoyo, replicación pendiente" |
| `inconclusa` | `inconclusa` |

**Second independent valid experiment** (after a self-loop):

Run the combination script (next section), then:

| First + second verdicts | Combination | Result |
|---|---|---|
| `apoyada` + `apoyada` | `consistent` | `apoyada` — **only after the fresh-verification gate below returns `no_errors_found`**; otherwise stay `en_experimento` |
| `apoyada` + `apoyada` | `borderline` / `heterogeneous` / `conflicting` | `evidencia_mixta` |
| `apoyada` + `refutada` (either order) | any | `evidencia_mixta` |
| `refutada` + `refutada` (only reachable under `linea_publicacion`) | `consistent` | `refutada` |
| `refutada` + `refutada` | `borderline` / `heterogeneous` / `conflicting` | `evidencia_mixta` |
| anything + `inconclusa` | any | `inconclusa` |

`borderline` is a real disagreement signal here, not a rounding of `consistent` —
see the heterogeneity caution in the next section. When two same-verdict
experiments come back `borderline`, `evidencia_mixta` is the default; requesting a
**third replication** instead is a legitimate alternative and a human decision.

A third valid experiment is a human decision, not automated here.

## Fresh-verification gate before `apoyada`

Runs **every** time the tables above would move a hypothesis to `apoyada` — the
second-independent-support edge, the only edge into `apoyada` — **before** any
write. No other edge is gated.

1. Save the `combine_effects.py` output you just ran to a temp file outside the
   vault (e.g. `... --json | tee <tmp>/combine.txt`).
2. **Build the packet** from the hypothesis note, **both** adjudicating
   experiments, and that output:
   ```
   python ${CLAUDE_PLUGIN_ROOT}/scripts/ledger/verifier_packet.py \
     --vault <vault root> --note <H-XXXX.md> \
     --experiment <E-first.md> --experiment <E-second.md> \
     --analysis-output <tmp>/combine.txt \
     --out <tmp>/packet.md --manifest <tmp>/manifest.json
   ```
   By allow-list it carries the claim, the cited evidence with its source text,
   each experiment's frozen `## Predicción` / `## Variables` / `## Diseño` /
   `## Plan de análisis` / `## Umbral de invalidez`, its `## Resultado`, its
   `## Enmiendas` (labelled), and the combination output — nothing else (no
   `history`, `confidence`, `status`, `## Revisión del ciclo`, prior
   verifications, or this session's reasoning).
3. **Dispatch `fresh-verifier`** with the packet file's text as the entire
   prompt, verbatim, nothing added.
4. **Record it** on the hypothesis note, whatever the verdict (agent output
   saved to `<tmp>/report.txt`):
   ```
   python ${CLAUDE_PLUGIN_ROOT}/scripts/ledger/verifications.py append \
     --note <H-XXXX.md> --verifier kairo/fresh-verifier@1.0.0 \
     --model <model id the agent reported> --verdict <verdict> --scope note \
     --report <tmp>/report.txt --packet-sha256 <sha256 from the manifest> \
     [--flag-human-review]      # whenever verdict != no_errors_found
   ```

| Verdict | Result |
|---|---|
| `no_errors_found` | Proceed: `en_experimento → apoyada`, with the usual bookkeeping. The `history` `evidence` line adds `fresh-verifier: no_errors_found (paquete <first 12 chars of sha256>)`. |
| `errors_found` | **Do not transition.** The hypothesis stays `en_experimento` — no `history` entry, no `_digest.md` / SOTA refresh (nothing changed state). `needs_human_review: true` and the findings (severity + location) go in `## Verificación independiente` via the command above. Report the findings to the user as the outcome of this invocation. |
| `cannot_assess` | Same as `errors_found`: no transition, `needs_human_review: true`, the reason recorded and reported. |

**The verifier never refutes.** An error in the analysis or a citation is not
evidence about the claim: never move the hypothesis to `refutada`,
`inconclusa`, `evidencia_mixta`, or anything else because of a verification
finding, and never treat its findings as an experiment verdict. Only the
experiments decide status, through the tables above.

**After review.** Once a human has looked: if the artifact was wrong, the fix
goes where the rules put it (a correction to the hypothesis note, an
`## Enmiendas` entry on the experiment — never an edit to a frozen section),
then re-invoke this skill with the same trigger; the gate runs again on a fresh
packet and appends a new entry (the latest `scope: note` entry governs). If the
human judges the findings are **not** errors, they may authorise the
transition explicitly — a human decision on the record, never an agent's
choice: proceed to `apoyada` and write the `history` `evidence` line as
`override humano de la verificación independiente (<fecha>): <motivo>`. The
`errors_found` entry stays in `verifications:`; nothing is edited.

If the dispatch fails, say so and do not transition — an unverified `apoyada`
is exactly what this gate exists to prevent.

## Combining evidence — versioned script

Never combine by inspection. Use:

```
python ${CLAUDE_PLUGIN_ROOT}/scripts/analysis/combine_effects.py \
    --e1 <effect exp1> --se1 <se exp1>   (or --ci1 LO HI) \
    --e2 <effect exp2> --se2 <se exp2>   (or --ci2 LO HI) \
    --alpha <shared alpha> --json
```

**Random-effects (DerSimonian–Laird) pooling** is the default. Rationale: with
only two studies the true between-study heterogeneity is unknown and unmeasurable
with any power; random-effects converges to the fixed-effect result under true
homogeneity anyway, so it is the safer default and never the riskier one.
Fixed-effect IV is available via `--method fixed` for a homogeneity-assumed
sensitivity check, not as the primary result.

The script reports the pooled effect + CI, τ², Cochran's Q / I², and a
`consistency` flag (`consistent` | `borderline` | `heterogeneous` | `conflicting`)
that feeds the table above. Record `combine_effects.py@<version>` and the method
string in the history entry (`--version` prints both).

**Heterogeneity caution — k = 2.** Cochran's Q has very low power with two
studies: a non-significant Q is **not** evidence of consistency. The script
therefore flags a `borderline` band (wider than "clearly significant") and
`update-confidence` treats `borderline` the same as `heterogeneous` for the
verdict — routing to `evidencia_mixta`, or to a human decision to seek a third
replication. The exact Q p-value / I² cut-points for each band are documented in
the script's `--help`; do not re-derive or loosen them here.

For a single-experiment edge, the "combination method" is the experiment's own
mechanical test (e.g. `two_proportion_test.py@1.0.0`), no pooling.

## `evidencia_mixta` → spawn a moderator hypothesis

On any transition to `evidencia_mixta`, also draft a follow-up hypothesis about
the **likely moderating variable** that could explain why the two experiments
diverged (different data regime, population, scale, implementation detail…):

- via `hypothesis-cycle` if available (entry = the moderator claim), else a plain
  `${CLAUDE_PLUGIN_ROOT}/templates/hypothesis-template.md` note at `status: propuesta`;
- `spawned_from: H-XXXX` (the `evidencia_mixta` hypothesis);
- `Justificación` cites **both** conflicting experiments (`E-XXXX`, `E-YYYY`) and
  their diverging effects.

## After any status change

1. **`history`** — append one entry to the hypothesis note (append-only, never
   edit past entries):

   ```yaml
   - date: <YYYY-MM-DD>
     status: <new status>
     by: <agent id | human name>
     experiments: [<E-XXXX>, ...]        # which experiment(s) drove this ([] for queue/prereg edges)
     combination: <script@version (method)> | n/a
     evidence: <one line: pooled effect + CI, Q p-value, or the single verdict>
   ```

   (`experiments` / `combination` extend the template's history entry.)

2. **Regenerate `Projects/<slug>/_digest.md`** — a **merge**, not a wholesale
   rewrite:
   - **Hypothesis rows** (rows whose `id` cell is a real `H-XXXX`): rebuild these
     from the project's `Hipotesis/*.md` — one row per note, `| id | claim |
     estado | lección |` (claim = first line of `## Claim`; `estado` = current
     `status`; `lección` = `## Lección` text if resolved, else blank). Notes are
     the source of truth for this half.
   - **Discard / ledger rows** (rows with no `H-XXXX` id — `id` cell is
     `(sin nota)` or blank, `estado` = `descartada`): these are an **append-only
     ledger** written by `hypothesis-cycle` for candidates that clear-failed
     before any note existed. **Preserve them verbatim** across every
     regeneration — never drop a discard row just because no note backs it.
   - Write the merged table: hypothesis rows (sorted by `id`) first, then the
     preserved discard rows in their existing order.
   - **`## Meta-revisión` section** (below the table, if present — written by
     `hypothesis-cycle`'s end-of-cycle meta-review step): **preserve verbatim,
     unchanged, in its existing order.** This regeneration only rebuilds the
     table above it; it never touches this section.

   Losing a discard row would let a rejected candidate be re-proposed — the exact
   failure the ledger exists to prevent. Losing the meta-review section would
   silently erase the cross-cycle context `hypothesis-cycle` reads at the start
   of its next run.

3. **Update `Projects/<slug>/Estado-del-arte.md`** — only for evidence edges (skip
   for queue / prereg / run-start edges):

   | New status | "Lo establecido vs. lo debatido" | "Huecos identificados" |
   |---|---|---|
   | `apoyada` | add to *establecido*, cite `H-XXXX (E-…, E-…; pooled effect + CI)` | mark the originating gap `cerrado por H-XXXX` |
   | `refutada` | move the claim to *debatido* / mark refuted, cite `H-XXXX (E-…)` | mark the gap `cerrado (refutado) por H-XXXX` |
   | `evidencia_mixta` | add to *debatido* with both experiments cited | add a **new** gap — the moderator question — referencing the spawned `H-YYYY` |
   | `inconclusa` | note under *debatido* as unresolved | leave the gap open, annotate `intentado, sin resolver (H-XXXX, E-…)` |

   Annotate, don't delete. Keep the citation rule: paper id + section/table where
   possible, plus experiment ids. After editing, set the file's `last_updated:`
   frontmatter to today and refresh the affected section's "as of <date>, N
   papers" line (the fields `create-project` step 7 established).

4. **Commit** — `Update H-XXXX: <new status>`.

## Callers

Every post-creation `status` change enters through one of these. The caller does
**no** inline `status` edit, no `history` write, no `_digest.md` / SOTA edit —
this skill does all of it.

| Caller | When | Trigger passed | Payload | This skill then does |
|---|---|---|---|---|
| `preregister-experiment` | after the prereg note is frozen (its old step 3b) | `prereg frozen` | **primary** hypothesis id, experiment id | `propuesta → preregistrada`; append `E-XXXX` to `linked_experiment`; `history`; digest. (Secondary hypotheses don't transition — `preregister-experiment` writes their `collateral_evidence` itself.) |
| `run-experiment` | after pre-flight passes and the run starts (its step 1) | `preregistrada → en_experimento` | hypothesis id, experiment id | `preregistrada → en_experimento`; `history`; digest |
| `run-experiment` | after a **valid** mechanical analysis (its step 5) | `evidence result` | hypothesis id, experiment id, mechanical verdict (`apoyada`/`refutada`/`inconclusa`), effect + CI/SE | gather **all** `experiment_validity: valid` linked experiments, check `linea_publicacion`, run `combine_effects.py`, decide `apoyada`/`refutada`/`inconclusa`/`evidencia_mixta` per the tables above; **before `apoyada`, run the fresh-verification gate** (a non-`no_errors_found` verdict leaves it `en_experimento` + `needs_human_review`); on `evidencia_mixta` spawn the moderator hypothesis; `history`; digest; SOTA |
| `hypothesis-cycle` | budget overflow — more candidates passed than the per-cycle budget | `budget overflow` | ordered list of the overflow candidate `H-XXXX` ids (already created at `propuesta`) | `propuesta → en_cola` for each, in the given order; `history`; digest |
| `hypothesis-cycle` / human | a queued candidate is picked up | `budget freed` | `H-XXXX` id(s) | `en_cola → propuesta`; `history`; digest |

`run-experiment`'s invalid / flagged case passes **no** trigger — an invalid
experiment never moves the state (the hypothesis stays `en_experimento`;
`run-experiment` records its own `needs_human_review` flag on the experiment
note).

## Common mistakes

- **Editing hypothesis `status` from another skill.** Route every transition
  through a **Callers** trigger. (Setting an *experiment* note's
  `status: preregistered | running | completed` is a different enum and stays
  with `preregister-experiment` / `run-experiment`.)
- **Counting an invalid experiment.** Only `experiment_validity: valid` moves the
  state.
- **Counting a collateral-evidence experiment.** Only experiments where the
  hypothesis is the primary `hypothesis:` (its `linked_experiment`) are gathered;
  `secondary_hypotheses` / `collateral_evidence` links never enter the combine.
- **Reaching `apoyada` from one supporting experiment.** Needs a second
  independent valid support (principle 2).
- **Forcing `apoyada`/`refutada` when the two disagree.** That is
  `evidencia_mixta` — and it spawns a moderator hypothesis.
- **Combining effects in prose.** Call `combine_effects.py`; cite its version.
- **Treating a same-seed re-run as a replication.** Not independent — ask.
- **Trying to move a terminal verdict.** No outgoing edges; use a child
  hypothesis.
- **Skipping the `_digest.md` / `Estado-del-arte.md` refresh.** The transition
  isn't done until the derived views match.
- **Reaching `apoyada` without the fresh-verification gate, or through an
  `errors_found` / `cannot_assess` verdict.** Stay `en_experimento`, set
  `needs_human_review: true`, record the findings; only a fresh
  `no_errors_found` or an explicit, logged human override lets it through.
- **Letting a verification finding move status.** The verifier only blocks
  `apoyada`; it never makes a hypothesis `refutada` (or anything else) —
  experiments decide status, not the verifier.
- **Giving `fresh-verifier` more than the packet.** The packet file's text,
  verbatim, is its whole prompt — no history, no reasoning, no hints.

## Related

- `${CLAUDE_PLUGIN_ROOT}/scripts/analysis/combine_effects.py` — replication combiner (random-effects
  DerSimonian–Laird; `--help` documents the `consistency` band cut-points).
- `${CLAUDE_PLUGIN_ROOT}/scripts/analysis/two_proportion_test.py` — single-experiment mechanical test.
- `${CLAUDE_PLUGIN_ROOT}/agents/fresh-verifier.md`,
  `${CLAUDE_PLUGIN_ROOT}/scripts/ledger/verifier_packet.py`,
  `${CLAUDE_PLUGIN_ROOT}/scripts/ledger/verifications.py` — the `apoyada` gate.
- **Callers** (above): `preregister-experiment`, `run-experiment`,
  `hypothesis-cycle` — the trigger vocabulary is the contract; keep both sides in
  sync.
