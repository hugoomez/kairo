---
name: hypothesis-cycle
description: Use when turning a research gap or a researcher's one-line claim into a vetted hypothesis note for a project in a Kairo vault. Runs dedup, falsifiability/novelty, known-failure, and severe-test checks with a short refinement loop before creating a `propuesta` note. v1 (default) is a single-critic cycle. v2 (opt-in, needs `DEEPINFRA_TOKEN`) adds a second, independent critic on Checks 3/4 and escalates any disagreement to human review instead of resolving it automatically. On budget overflow (orthogonal to v1/v2), runs a pairwise Elo tournament, an evolution step, and an opportunistic serendipity-scan-seeded wildcard to decide who advances now vs. queues, plus an end-of-cycle meta-review note. Invoked by create-project for gap-derived candidates, or directly when the user proposes a claim.
---

# Hypothesis Cycle

## Overview

Take a candidate, run four cheap-first checks with a short refinement loop, and
either create a `propuesta` hypothesis note or discard it with a logged reason
so it isn't proposed again.

**v1 (default): single-critic.** The orchestrating session runs all four checks
itself.

**v2 (opt-in): dual-critic on Checks 3 and 4.** Adds an independent second
critic — the `second-critic` subagent, which calls a cloud model on DeepInfra —
running the *same* Check 3 / Check 4 text in parallel with, and without seeing,
the primary critic's own verdict. See "## v2 mode — second, parallel critic"
below. Use v2 when the researcher asks for it, or by default once
`DEEPINFRA_TOKEN` is configured and the project's stakes warrant it (a
`linea_publicacion` candidate, or the researcher's general preference) — v1
remains correct and complete on its own; v2 is an added independence check, not
a replacement for the primary critic's own reasoning.

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

## v2 mode — second, parallel critic

Applies only when v2 is in use (see Overview). Checks 1 and 2 are unchanged —
they're retrieval-heavy (dedup, literature novelty), not adversarial judgement
calls, so a second critic adds little there. Checks 3 and 4 change as follows.

### Running the second critic

1. **Pick the tier.** `high_stakes` if the candidate is (or will be)
   `linea_publicacion: true`, or the researcher explicitly asks for it this
   run; `default` otherwise. Decide this once, at cycle intake — the flag may
   not exist yet on any note (the candidate has none until it passes), so ask
   the researcher if it's genuinely ambiguous rather than guessing.
2. **Dispatch `second-critic`** with the candidate package (claim + test
   sketch), the check number (3 or 4), and the tier — **before** forming your
   own verdict on that check, or at least without showing it the package
   knowing what you concluded. It must never see your verdict; that
   contamination would make the comparison meaningless (see the agent's own
   "independence" note).
3. **Independently run the check yourself**, exactly as v1 describes it.
4. **Compare.** Both verdicts are one of `pass` / `refinable` / `clear_fail`.

### On agreement vs. disagreement

| Outcome | Action |
|---|---|
| **Agree** | Proceed exactly as v1 for this check (continue the gate, refine, or discard). |
| **Disagree** | **Stop immediately** — do not average, do not let the primary's verdict win by default, do not continue to the next check. This is the "dudoso" signal for the autonomy dial: something a human should look at, not something the pipeline resolves on its own. |

**On disagreement:** treat it like the existing "rounds exhausted, no clear
verdict" outcome (see "Loop and stopping rule") — save as `status: propuesta`
anyway, set `needs_human_review: true`, and write the **full** exchange (both
verdicts, both `reasoning` blocks, the rival/discriminating-prediction fields
if it was Check 4) into `## Revisión del ciclo`. Flag it to the user
explicitly as a critic disagreement, not a generic "rounds exhausted" — those
are different situations and the note should say which one happened.

If `second-critic` returns `status: unavailable` (no `DEEPINFRA_TOKEN`, the
call failed, etc.), **do not silently fall back to treating it as agreement or
as v1.** Say so plainly, and either retry once or drop to v1 for this run —
never invent a verdict to fill the gap. Whichever you choose, note it in the
cycle output so the vetting record doesn't silently read as "two critics
agreed" when only one ran.

### Logging every comparison

After each Check 3/4 comparison (agree or disagree), run:
```
python ${CLAUDE_PLUGIN_ROOT}/scripts/second_critic/agreement_log.py record \
  --log <vault>/Scripts/second-critic-log.jsonl --project <PROJ-XXX> \
  --hypothesis "<claim or H-XXXX>" --check <3|4> --tier <default|high_stakes> \
  --primary <verdict> --second <verdict> --cost-usd <second-critic's cost_usd>
```
At the end of the run, also run `... summary --log <same path>` and surface its
output (agreement rate, cumulative cost, and the high-agreement warning if it
fires) to the researcher — this is how a rubber-stamping second critic gets
noticed instead of quietly trusted. Never skip the `record` call because the
outcome seemed obvious; the log's value is in *every* comparison, not a
curated subset.

## Citation requirement

The created note's **`## Justificación (evidencia citada)`** cites specific papers
as `P-XXXX §Sección / Tabla N / Figura N — qué muestra y cómo sostiene el claim`.

**Before writing any such locator, re-open the source `Papers/P-XXXX.md` note
and read the exact bullet(s) under that `§Sección` / `Tabla N` / `Figura N`
heading in its `## Texto completo`.** The citing sentence must paraphrase what
is specifically written there — not the paper as a whole, and not a citation
for the same paper already used elsewhere for this project (an earlier
hypothesis's `Justificación`, or `Estado-del-arte.md`). If the claim is
actually supported by a different section than the one that first came to
mind, cite that section instead. If the fact you're about to cite is a
near-duplicate of something already cited elsewhere in this project (same
paper, same or adjacent claim), look up and reuse that earlier citation's
exact locator rather than re-deriving your own — never let the same fact
carry two different section numbers across the project.

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

## Budget overflow — tournament, evolution, wildcard, meta-review

Create **all** passing candidates as `propuesta` notes (next `H-XXXX` each), in
generation order. Run the budget check once, after every candidate for this run
exists.

If the count is within the project's per-cycle budget (`create-project` caps
the first pass at 5; default 3–5), stop here — every candidate stays
`propuesta`, no tournament needed. Still run **Step 4 (meta-review)** below;
that step runs at the end of every cycle regardless of overflow.

If the count **exceeds** the budget, run these steps in order. They change
**who competes for the budget**, nothing else — the three-outcome logic
above (pass / clear fail / rounds exhausted) is unchanged and already
finished by the time these steps start; nothing here re-opens a check or
re-classifies an outcome.

### Step 1 — Wildcard (at most one per cycle, opportunistic)

Before ranking, try to seed one deliberately divergent candidate:

1. Call `serendipity-scan` for this project — mechanism 2 (boundary-spanning
   citations) is eligible whenever `Papers/` has ≥3 notes tagged with this
   `PROJ-XXX`; mechanism 1 (structural analogy) is additionally eligible if
   the project has a resolved hypothesis with a filled `## Lección`. Let
   `serendipity-scan` apply its own eligibility rules and combined cap — do
   not loosen them to force a result.
2. If nothing survives its cap, **skip the wildcard step silently** — don't
   retry, don't lower the bar, don't treat it as a cycle failure. This is
   opportunistic, not mandatory.
3. If ≥1 lead survives, take the strongest one and draft **one** new
   candidate package (claim + test sketch) whose claim is genuinely inspired
   by the lead's abstracted pattern or bridging edge — a different angle than
   anything already generated this cycle, not a rephrasing of an existing
   candidate. Tag it `origin_flag: wildcard` in frontmatter for later note
   metadata (`generated_by.origin` still reads `agent`).
4. Run it through **Checks 1–4 exactly like any generated candidate**,
   including a real Check 2 novelty search on the new claim. **Never cite the
   serendipity lead itself in this candidate's `Justificación`** — it must
   earn its citations the normal way, or the "additive and disposable, never
   auto-merged" guarantee `serendipity-scan` makes to the researcher breaks.
   If the wildcard candidate fails or gets refined away, that's a normal
   cycle outcome (discard / refine as usual) and the wildcard slot for this
   cycle is simply spent — don't call `serendipity-scan` again this cycle.

### Step 2 — Tournament (only if the passing count, wildcard included, still exceeds budget)

If Step 1's wildcard brought the passing count back down to budget or below
(i.e., it's the only one over), skip straight to Step 3 — there's nothing to
rank. Otherwise:

1. Give every candidate that passed Checks 1–4 this cycle (gap-derived,
   human-submitted, and the wildcard if it passed) a starting **Elo of
   1500**.
2. Run **`min(N-1, 4)` rounds** of Swiss-style pairing — each round pairs
   candidates adjacent in current Elo order (highest with next-highest,
   etc.), never repeating a pair within the same cycle. This is "a handful
   of comparisons," never an exhaustive round-robin.
3. Each match is a **short simulated debate**: argue briefly for each
   candidate on four fixed dimensions — (a) severity of its Check 4 test
   against its named rival, (b) novelty/impact if confirmed, (c) feasibility
   of the test sketch given typical project resources, (d) citation strength
   of its `Justificación`. Declare one winner (no draws; break a genuine
   tie on citation strength). Update both candidates' Elo with the standard
   formula, `K=32`.
4. After the last round, sort candidates by final Elo, descending.

### Step 3 — Advance vs. queue

The **top `budget` candidates** by final Elo stay `propuesta` — they're
already created; nothing to do. Hand the **rest, in ascending Elo order**
(weakest first), to **`update-confidence`** — trigger `budget overflow` —
which transitions each `propuesta → en_cola` and writes their `history`,
exactly as before. **This skill still never writes `en_cola` itself.**

For every candidate that went through a tournament match (won or lost),
record its final Elo and a one-line reason from its last debate in its
`## Revisión del ciclo` (append to the section if a refinement round already
put it there; create the section for this reason alone if not) — the
ranking must be traceable from the note itself, not only from the run's
chat output.

### Step 4 — Evolution (only if ≥2 candidates passed this cycle)

Take the **top 2 candidates** by final Elo (or, if no tournament ran because
the count never exceeded budget, the two you'd judge strongest on the same
four dimensions from Step 2.3). Draft **one** new candidate package whose
claim genuinely **combines a mechanism or contrast from each parent** — not
a restatement of either and not a checklist union of both. Note the
parentage inline in your own working notes (`combina <claim A resumida> +
<claim B resumida>`) — this does not become an extra section in the final
note; the created note reads like any other hypothesis-cycle note.

Run it through Checks 1–4 exactly like any generated candidate. It did not
exist during Step 2/3, so a pass **never reopens this cycle's already-decided
ranking** — file it as `propuesta` and let it compete honestly in the
*next* cycle's tournament if a future overflow arises. A fail or discard here
is a normal outcome; evolution is one attempt per cycle, no retry.

### Step 5 — Meta-review (every cycle, overflow or not)

At the end of every full cycle, write a short note (5–10 lines, in the
project's language) naming **patterns across this round's critiques** — e.g.
"3 of 4 candidates needed a Check 4 refinement round (weak-contrast
problem)", "the second critic disagreed only on Check 3 this round", "the
wildcard's Check 2 search independently surfaced the same paper the
serendipity lead pointed at." Append it to `Projects/<slug>/_digest.md` under
a running `## Meta-revisión` section (create the section on first use), one
dated entry per cycle, oldest first.

At the **start** of the next `hypothesis-cycle` run for this project, read
the most recent 1–2 `## Meta-revisión` entries before generating candidates.
Let them inform what to watch for this round (e.g., spend more care on
rival selection if last cycle flagged a recurring weak-contrast problem) —
never treat a past entry as a hard rule, and never repeat it verbatim in the
new note.

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

## v1 / v2 scope

**In v1:** one critic, cheap-first checks (incl. auxiliary-assumption / Duhem),
≤3 rounds, note-creation at `propuesta` or discard, human entry point.

**In v2 (opt-in, on top of v1):** a second, independent critic on Checks 3/4
only (`second-critic`, cloud-hosted, tiered by stakes), with disagreement
escalating to `needs_human_review: true` rather than being resolved
automatically; a persistent agreement/cost log.

**Not in v1 or v2:** formal power analysis of *this* skill's own checks (the
`completo` preregistration tier's power analysis is a different thing — see
`preregister-experiment`).

**Orthogonal to v1/v2 — always active on overflow:** the tournament /
evolution / wildcard / meta-review mechanism in "Budget overflow" applies
whether the cycle ran in v1 or v2, and does not change Checks 1–4 themselves.
Budget overflow → `update-confidence` marks `en_cola` in **tournament order**
(weakest final Elo first) when a tournament ran, or generation order when the
count never exceeded budget and no tournament was needed.

## Common mistakes

- **Running checks out of order or past a clear fail.** It's a gate — stop at the
  first structural failure.
- **Treating a `refutada` overlap as refinable.** Re-proposing a refuted claim
  (or one its `Lección` already answers) is a clear fail — log the id.
- **Running the patent novelty check for a `ciencia` project.** Patents only for
  `producto`/`hibrido`.
- **Forcing a citation for a human-origin claim.** Use the exact
  "intuición del investigador…" string instead.
- **Citing a section from memory of an earlier citation instead of re-reading
  it.** A confidently-wrong locator is worse than an obviously missing one —
  it looks verified and isn't. Re-open the exact heading every time, even for
  a paper already cited elsewhere in this project.
- **The same fact carrying two different section numbers across the
  project.** If this hypothesis restates a fact already cited in
  `Estado-del-arte.md` or another hypothesis, reuse that exact locator
  instead of re-deriving a fresh one.
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
- **(v2) Letting the primary critic's verdict win on disagreement.** Disagreement
  escalates to human review; it is never resolved by trusting one critic over
  the other, averaging, or majority-of-one.
- **(v2) Showing `second-critic` the primary's verdict before it answers.** That
  contaminates the independence the whole mode exists for.
- **(v2) Skipping the `agreement_log.py record` call on an "obvious" agreement.**
  The log's warning only works if every comparison is logged, not a curated
  subset that looks interesting.
- **(v2) Treating `second-critic: unavailable` as agreement.** A failed or
  missing call is not a verdict — say so and either retry once or drop to v1
  for that run.
- **Writing `en_cola` (or any transition) directly.** This skill only creates
  notes at `propuesta`; budget overflow and everything after go through
  `update-confidence` (trigger `budget overflow`, then `evidence result` etc.)
  — the tournament decides the *order* it hands over, never the transition
  itself.
- **Running an exhaustive round-robin tournament.** `min(N-1, 4)` Swiss-style
  rounds is the ceiling — "a handful of comparisons," not every pair.
- **Citing the wildcard's serendipity lead directly in its `Justificación`.**
  The lead only seeds the claim; the candidate earns its own citations
  through a real Check 2 search, same as any other candidate.
- **Forcing the wildcard step on a thin corpus or with no resolved
  hypothesis.** If `serendipity-scan`'s own eligibility rules or cap rule it
  out, skip the step silently — don't loosen its bar to fill a slot.
- **Letting an evolution or wildcard candidate reopen an already-decided
  tournament ranking.** A pass after Step 2/3 has run competes in the *next*
  cycle's tournament, not this one.
- **Skipping the meta-review because there was no overflow this cycle.** Step
  5 runs at the end of every cycle, overflow or not.
- **Treating a past `## Meta-revisión` entry as a hard rule or repeating it
  verbatim in a new note.** It's context to inform judgement, not a
  checklist item to satisfy.
