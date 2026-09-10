---
name: spawn-hypothesis
description: Use when a product or engineering task hits genuine technical uncertainty that needs a research answer — typically "spawn hypothesis from <task-id>: <the question>". Turns the task's open question into a hypothesis that runs the full vetting pipeline, and cross-links the task and the hypothesis.
---

# Spawn Hypothesis (product → science)

## Overview

A thin front-end to `hypothesis-cycle`. It takes a task that has run into a real
technical unknown, frames that unknown as a candidate hypothesis, hands it to the
**exact same rigor pipeline** every other hypothesis goes through, and links the
two notes both ways.

**No reduced rigor.** Being product-motivated buys the hypothesis nothing —
`hypothesis-cycle` still runs dedup, falsifiability/novelty, the known-failure
checklist, and the severe-test evaluation, with the same ≤3-round loop and the
same outcomes.

## When to use

- The user says "spawn hypothesis from `F-XXX`: …" or otherwise marks a task as
  blocked on a question that experiment/evidence — not a decision or more
  engineering — would answer.

**When not to use:** the blocker is a product/process/priority call (that is an
ADR, not a hypothesis); the question is already covered by an existing hypothesis
(let `hypothesis-cycle`'s dedup catch it — still route it through); the "task" has
no note.

## Inputs

- A **task id** (`F-XXX`) with a note carrying `project:` and a `## Descripción`.
- The **uncertainty**, stated as a question or a rough claim.

## Procedure

### 1. Read the task

Locate the `F-XXX` note (under the project's task area, e.g. `Producto/`). Read
its `project:`, `status`, and `## Descripción` for context.

### 2. Frame the candidate

Turn the uncertainty into a **candidate package** for `hypothesis-cycle`:

- **Claim** — one falsifiable sentence (rewrite a question into a testable
  assertion).
- **Test sketch** — how the team could resolve it: the manipulation/comparison,
  what would be measured, what result would count against the claim.

### 3. Delegate to `hypothesis-cycle`

Invoke **`hypothesis-cycle`** via its **human-submitted entry point** (skip
generation, start at the dedup check). Pass:

- the candidate claim + test sketch;
- `project:` = the task's project;
- `generated_by.origin: human` — the researcher flagged this uncertainty, so the
  **citation exception applies**: `hypothesis-cycle` searches for supporting
  literature and, if none genuinely bears on it, writes
  `intuición del investigador, sin respaldo directo en la literatura` rather than
  forcing a weak citation;
- `spawned_from: <F-XXX>` (the task id — a new accepted value for that field,
  alongside `H-XXXX` / `E-XXXX`).

Do not pre-empt, reorder, or skip any check. `hypothesis-cycle` is authoritative.

### 4. Apply the outcome and cross-link

| `hypothesis-cycle` outcome | Do |
|---|---|
| **`propuesta` note created** (`H-XXXX`) | Confirm `spawned_from: <F-XXX>` on the hypothesis. On the task note set `motiva_hipotesis: <H-XXXX>` and append to `## Notas`: `Incertidumbre técnica derivada a H-XXXX el <fecha>.` Leave the task's `status` and `motivada_por` untouched. |
| **rounds exhausted → `propuesta` + `needs_human_review`** | Same linking as above; also surface the review flag to the user. |
| **clear fail — no note** | `hypothesis-cycle` already logged the `_digest.md` line. On the task note append to `## Notas`: `Spawn de hipótesis intentado el <fecha>, descartado en <check>: <razón>.` Do **not** set `motiva_hipotesis`. |

### 5. Commit

`Spawn H-XXXX from F-XXX` (or `Spawn from F-XXX: descartado` on a clear fail) —
in addition to whatever `hypothesis-cycle` committed.

## Same rigor — what is *not* different

- Every `hypothesis-cycle` check runs, in order, as a gate — including the
  auxiliary-assumption (Duhem) item in Check 3 and the written
  `## Hipótesis rival descartada` section from Check 4.
- The hypothesis is *created* at `status: propuesta`. Every later transition —
  and there is no budget-overflow case here (one candidate) — goes through
  `update-confidence`; promotion to `apoyada` still needs a second independent
  valid experiment.
- A clear fail still means no note — the task just records that the question was
  posed and rejected (`hypothesis-cycle` appends the `_digest.md` discard row,
  which later regenerations preserve).

## Cross-note fields

| Note | Field | Value |
|---|---|---|
| Hypothesis | `spawned_from` | `<F-XXX>` (the task) |
| Hypothesis | `project` | the task's `project` |
| Hypothesis | `generated_by.origin` | `human` |
| Task (`F-XXX`) | `motiva_hipotesis` | `<H-XXXX>` (only if a note was created) |
| Task (`F-XXX`) | `## Notas` | one dated line recording the spawn (created or discarded) |

## Common mistakes

- **Granting a shortcut because "the product needs an answer."** Same pipeline,
  same gate.
- **Forcing a literature citation.** `origin: human` → use the
  `intuición del investigador…` string when nothing genuine supports it.
- **Setting `motiva_hipotesis` after a clear fail.** No note, no reverse link —
  only a `## Notas` line.
- **Skipping dedup.** Route it through `hypothesis-cycle` even if you think it is
  novel; the `refutada`-lesson check matters here.
- **Editing the task's upstream `motivada_por`.** That link is unrelated; only
  add `motiva_hipotesis`.

## Related

- `hypothesis-cycle` — the vetting pipeline this delegates to (human-submitted
  entry point).
- `update-confidence` — later status transitions for the spawned hypothesis.
