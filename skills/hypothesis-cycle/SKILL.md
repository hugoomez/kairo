---
name: hypothesis-cycle
description: Use when turning a research gap or a researcher's one-line claim into a vetted hypothesis note for a project in a Kairo vault — the v1 single-critic vetting cycle. Invoked by create-project for gap-derived candidates, or directly when the user proposes a claim. Runs dedup, falsifiability/novelty, known-failure, and severe-test checks with a short refinement loop before creating a `propuesta` note.
---

# Hypothesis Cycle

## Overview

v1 **single-critic** cycle: take a candidate, run four cheap-first checks with a
short refinement loop, and either create a `propuesta` hypothesis note or discard
it with a logged reason so it isn't proposed again.

Binding rules (Kairo core principles):

- Every hypothesis carries a **`Justificación`** citing specific papers with
  section / table / figure references (one exception — see Citation requirement).
- This skill only ever **creates** a note at `status: propuesta`. It writes no
  status *transition* — budget-overflow `en_cola` and every later change go
  through `update-confidence`. It never promotes; promotion needs an independent
  replication experiment, handled elsewhere.

## When to use

- `create-project` step 8 — vetting 3–5 candidates derived from
  `Estado-del-arte.md` §"Huecos identificados".
- The user proposes a hypothesis claim for an existing project and wants it
  vetted and filed.

**When not to use:** promoting or preregistering an existing hypothesis;
generating experiments; ranking competing hypotheses (v2).

## The unit

What flows through the cycle is a **candidate package**:

1. **Claim** — one falsifiable sentence.
2. **Test sketch** — a rough idea of how it could be tested: manipulation /
   comparison, what would be measured, what result would count against the claim.
   Enough to judge *severity*, **not** a full preregistration (no frozen analysis
   plan, no seeds, no dataset hash).

Both parts are refined together across loop rounds.

## Entry points

| Trigger | Start at |
|---|---|
| Gap-derived (from `create-project` / a "Huecos" bullet) | Generate the candidate package (claim + test sketch) from the gap, then **Check 1**. |
| **Human-submitted** — user gives a single-sentence claim | **Skip generation.** Wrap the sentence as the claim, draft a test sketch with the user's help if absent, go straight to **Check 1**. |

For a human-submitted claim, confirm which project it belongs to if it isn't
obvious. The human-origin citation exception (below) applies to this path.

## Checks — ordered gate, cheapest first

Run in order. **Stop at the first clear fail.** Each check yields one of:
*pass*, *refinable* (fixable within the loop — go refine and re-enter at Check 1),
or *clear fail* (structural, not fixable by wording — discard).

### 1. Semantic dedup

Query Smart Connections (`mcp__smart-connections__search_by_text` with the claim;
`search_similar` on close hits) against **all existing hypotheses in the same
project** — every status, not just active ones. **Also compare the claim against
the `Lección` section of every `refutada` hypothesis in the project** — a claim
that re-proposes something already refuted, or whose lesson already answers it,
is a clear fail.

- Near-duplicate of a live hypothesis → clear fail.
- Overlaps a `refutada` claim / its `Lección` → clear fail (log which one).
- Partial overlap → *refinable*: sharpen the contrast or narrow scope.

If `mcp__smart-connections__*` is unavailable, fall back to a manual read of the
project's hypothesis notes (title + claim + any `Lección`) and still run the
comparison — but flag in the cycle output that semantic dedup ran **without MCP
coverage**, and point the user to the plugin README → "Smart Connections (optional companion)"
(usual cause: Claude Code launched from a parent folder instead of the vault
root, or `.mcp.json` changed with no restart / `/mcp` reconnect). Don't pass Check 1
silently as if the tool had run.

### 2. Falsifiability and novelty

- **Falsifiability** (cheap, do first): is there a concrete observation that
  would count against the claim? Unfalsifiable as written → *refinable*; if it
  can't be made falsifiable at all → clear fail.
- **Novelty** (only if falsifiable): scope `literature-search` to the claim —
  include the **patent facet only if the project `type` is `producto` or
  `hibrido`**. If the claim is already established in the vault or the literature
  (or already patented, for producto/hibrido) → clear fail. Adjacent-but-distinct
  → pass, and record the neighbours for the `Justificación`.

### 3. Known-failure checklist

Check the test sketch against each:

- **Causal confusion** — does the design let a correlation masquerade as the
  causal claim? Is the manipulation actually on the proposed cause?
- **Insufficient sample size** — is the sketched N plausibly able to detect an
  effect of the expected size? Order-of-magnitude judgement, not a formal power
  calc.
- **Uncontrolled confounders** — name the obvious ones; does the sketch hold them
  fixed or measure them?
- **Auxiliary assumptions (Duhem)** — list the instruments, background theories,
  and enabling conditions the test *silently* relies on (the metric measures what
  it claims to; the baseline is correctly implemented; the data pipeline is
  sound; the library/model behaves as documented). If a plausible failure of any
  one of them would produce the **same null result as a false hypothesis**, the
  test cannot isolate the hypothesis: → *refinable* — make that assumption
  explicit and, where feasible, add a check that tests it directly (a
  manipulation check, a positive control); → *clear fail* if the assumption
  cannot be isolated from the hypothesis at all.

Any item fixable by tightening the sketch → *refinable*. An item that can't be
addressed even in principle for this claim → clear fail.

### 4. Severe-test evaluation (Mayo)

Would the sketched test **actually be capable of refuting the hypothesis if it
were false** — or does it merely measure something correlated with it?

- Reject the **weak-contrast problem** (Meehl): a test whose "confirmation" is
  near-guaranteed because the alternative it's contrasted against is implausible
  or vague provides no severe test. The predicted result must be one the
  hypothesis genuinely risks getting wrong.
- Ask: what does the sketch predict that a plausible rival hypothesis does *not*?
  If nothing → *refinable* (design a discriminating comparison) or, if the claim
  admits none → clear fail.
- **Write the rival down.** The plausible rival you contrasted against, and the
  specific prediction that discriminates this claim from it, go into a
  **`## Hipótesis rival descartada`** section of the created note — not just
  reasoned about in passing. If you cannot name a concrete rival to write, the
  test is not yet severe (*refinable*).

## Citation requirement

The created note's **`## Justificación (evidencia citada)`** cites specific papers
as `P-XXXX §Sección / Tabla N / Figura N — qué muestra y cómo sostiene el claim`.

**Exception — `generated_by.origin: human`:** missing *direct* literature support
does **not** block the hypothesis. Search for supporting context anyway; cite what
genuinely bears on it. If nothing does, write exactly:

> intuición del investigador, sin respaldo directo en la literatura

Do **not** manufacture a weak or tangential citation to fill the section. This
exception never applies to `origin: agent` candidates.

## Loop and stopping rule

**Max 2–3 refinement rounds** per candidate. A round = apply the *refinable*
feedback to the claim + test sketch, then re-enter at Check 1.

| Outcome | Action |
|---|---|
| **Pass** all four checks | Assign next `H-XXXX` (scan vault-wide, zero-pad 4). Create the note in `Projects/<slug>/Hipotesis/` with `status: propuesta`. If **≥ 1 refinement round** happened on the way, write `## Revisión del ciclo` (see below). |
| **Clear fail** at any check | **Do not create a note** (one logged exception — see "On a clear fail"). Append one line to `Projects/<slug>/_digest.md` stating the claim and which check killed it and why — so it isn't re-proposed. |
| **Rounds exhausted, no clear verdict** | Save as `status: propuesta` anyway, set `needs_human_review: true`, and write the **full** round-by-round critic exchange in `## Revisión del ciclo`. Flag it to the user. |

### `## Revisión del ciclo` — when to include it (single rule)

Write the section **whenever the cycle ran ≥ 1 refinement round**, whatever the
final outcome — not only when rounds were exhausted:

- **Passed after ≥ 1 refinement round** → one short entry per round: what was
  *refinable*, the change made to the claim / test sketch, and that it re-entered
  at Check 1. A claim that was re-scoped mid-cycle (e.g. refuted in Check 2, then
  narrowed) **must** show that pivot here — it is part of the hypothesis's
  provenance, not disposable scratch work.
- **Rounds exhausted, no verdict** → the full round-by-round exchange, plus
  `needs_human_review: true`.
- **Passed on the first pass, 0 refinement rounds** → **omit the section
  entirely** (nothing to record).
- **Clear fail** → no note at all, so no section (unless the logged exception in
  "On a clear fail" is taken).

### On a clear fail — what may follow

The gate's meaning is fixed: *clear fail = structural, not fixable by wording*.
What the researcher may do next is one of:

**In-spec:**

1. **Discard** *(default)* — append the `_digest.md` line, create no note.
2. **Spin off a new candidate** — propose a **different** claim (a narrower
   sub-regime, an adjacent phenomenon). This is **not** a refinement round and
   **not** a continuation of the failed claim: it re-enters at generation /
   Check 1 as a fresh candidate package with its own identity, and the original
   claim still gets its `_digest.md` discard line. Nothing about the failed
   claim's id or history carries over.

**Off-spec exception — explicit, logged human override only:**

3. **File the failed claim anyway** as `status: propuesta` + `needs_human_review:
   true`, with a `## Revisión del ciclo` that records the clear fail, the check
   that killed it, and the researcher's stated reason for overriding the gate.
   This **contradicts** the gate, so it is allowed only as a deliberate human
   decision taken on the record — never an agent's choice, never a default, and
   never without the user explicitly asking for it. The `history` evidence line
   must say `override manual de un clear fail (Check N)`.

**Never:** keep running refinement rounds on a clear fail as if it were
*refinable*. If it is genuinely fixable by wording, it was not a clear fail —
re-classify it, don't quietly loop.

### Discard line for `_digest.md`

The digest table is `| id | claim | estado | lección |`. For a discard, append
one row:

```
| (sin nota) | <claim en una línea> | descartada | <check N: por qué es un fallo estructural> |
```

`descartada` is digest-ledger vocabulary for "rejected before a note existed" —
it is **not** a hypothesis `status` value (there is no note, so no status).

These rows are an **append-only ledger**. `update-confidence`'s `_digest.md`
regeneration is a *merge*: it rebuilds the `H-XXXX` rows from `Hipotesis/*.md` but
**preserves every discard row verbatim** (see `update-confidence` "After any
status change" step 2). So appending here is safe — a later regeneration will not
drop your row, and it must not: losing it would let the rejected candidate be
re-proposed.

## Budget overflow

Create **all** passing candidates as `propuesta` notes (next `H-XXXX` each), in
generation order. Run the budget check once, after every candidate for this run
exists.

If the count exceeds the project's per-cycle budget (`create-project` caps the
first pass at 5; default 3–5), hand the **ordered list of overflow `H-XXXX` ids**
to **`update-confidence`** — trigger `budget overflow` — which transitions each
`propuesta → en_cola` in that order and writes their `history`. This skill never
writes `en_cola` itself. No ranking, no tournament — that's v2.

## Frontmatter + sections for created notes

From `${CLAUDE_PLUGIN_ROOT}/templates/hypothesis-template.md`. Set at minimum:

- `id`, `project: <PROJ-XXX>`, `status: propuesta` (always — never `en_cola` here;
  overflow → `update-confidence`), `created`/`updated` = today.
- `generated_by`: `origin: agent`, `model: claude-sonnet-5`,
  `skill_version: hypothesis-cycle@v1`, `pipeline_config: <hub path>` — **or**
  `origin: human` (then `model`/`pipeline_config` omitted) for the human entry
  point.
- `history`: first entry — `date` today, `status: propuesta`, `by: <agent id |
  human name>`, `evidence: "hypothesis-cycle v1: passed dedup/falsifiability/
  known-failure/severe-test"` (or `"rounds exhausted — flagged for human review"`,
  or `"override manual de un clear fail (Check N)"`).
- `needs_human_review: true` in the exhausted-rounds outcome **and** in the
  logged clear-fail override (option 3 of "On a clear fail"); otherwise `false`.

Sections:

- `## Claim` = the refined sentence.
- `## Justificación (evidencia citada)` = per the Citation requirement.
- `## Hipótesis rival descartada` = the rival from Check 4 and the discriminating
  prediction (required — see Check 4).
- `## Revisión del ciclo` = per the single rule in "Loop and stopping rule":
  present whenever ≥ 1 refinement round ran (short per-round entries on a pass;
  full exchange when rounds were exhausted; also the logged clear-fail override).
  Omitted only for a clean first-pass.
- `## Lección` stays empty until the hypothesis is resolved.

## v1 scope

**In:** one critic, cheap-first checks (incl. auxiliary-assumption / Duhem), ≤3
rounds, note-creation at `propuesta` or discard, human entry point.

**Not in v1 (v2):** tournament or head-to-head ranking of competing candidates;
a second local critic; evolution / meta-review of the critic; formal power
analysis. Budget overflow → `update-confidence` marks `en_cola` in generation
order — no ranking.

## Common mistakes

- **Running checks out of order or past a clear fail.** It's a gate — stop at the
  first structural failure.
- **Treating a `refutada` overlap as refinable.** Re-proposing a refuted claim
  (or one its `Lección` already answers) is a clear fail — log the id.
- **Running the patent novelty check for a `ciencia` project.** Patents only for
  `producto`/`hibrido`.
- **Forcing a citation for a human-origin claim.** Use the exact
  "intuición del investigador…" string instead.
- **Creating a note for a clear fail.** Discards get an appended `_digest.md` line
  and no note — the *only* exception is option 3 of "On a clear fail" (an explicit,
  logged human override), never an agent decision.
- **Continuing to refine a clear fail.** Clear fail is structural. Spin off a
  *new* candidate if there's an adjacent claim worth testing; don't loop the
  failed one.
- **Dropping the refinement trail.** If ≥ 1 refinement round ran (especially a
  mid-cycle re-scope), `## Revisión del ciclo` is written — on a pass too, not
  just when rounds are exhausted.
- **Skipping the auxiliary-assumption check.** A test that can't isolate the
  hypothesis from a broken instrument / baseline is not a test of the hypothesis.
- **Reasoning about the rival implicitly.** Check 4's rival goes in a written
  `## Hipótesis rival descartada` section.
- **Looping more than 3 rounds.** Exhausted → `propuesta` + `needs_human_review`,
  not another round.
- **Writing `en_cola` (or any transition) directly.** This skill only creates
  notes at `propuesta`; budget overflow and everything after go through
  `update-confidence` (trigger `budget overflow`, then `evidence result` etc.).
  Ranking overflow candidates is also out — `update-confidence` queues them in
  generation order; ranking is v2.
