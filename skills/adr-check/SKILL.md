---
name: adr-check
description: Use when displaying, reviewing, quoting, or reusing an ADR that cites a hypothesis — checks each cited hypothesis's current status against what it was when the ADR was written and flags any change visibly. Also runs as a batch sweep over all ADRs.
---

# ADR Staleness Check

## Overview

An ADR's rationale can rot silently: a hypothesis it cites as support may later
be **refuted**, go to **evidencia_mixta**, or come back **inconclusa**. This
check compares each cited hypothesis's status *now* against its status *when the
ADR was written*, and surfaces any change so the decision can be re-examined.

It never rewrites a decision's content — ADRs are historical records. It flags,
and (if asked) appends to a `## Revisión de vigencia` section. When a flag is
actually acted on, it drives the Nygard supersession lifecycle: a **new** ADR
replaces the old, and the old one's `status` is flipped to `Superseded by
ADR-XXX` — its `## Decisión` / `## Contexto` / `## Alternativas consideradas` are
still left untouched.

## When to use

- Any time an ADR whose `cites:` contains an `H-XXXX` id is shown, reviewed, or
  cited as justification for new work.
- As a **batch sweep** over `Projects/*/Producto/ADR-*.md` (or wherever ADRs
  live) — e.g. before a planning session.

## Procedure

For each ADR in scope:

1. Read `date:` and `cites:`. Keep the `H-XXXX` ids; ignore `P-XXXX`.
2. For each cited `H-XXXX`, get its **status as of the ADR**:
   - if the ADR frontmatter has a `cites_status:` snapshot
     (e.g. `cites_status: {H-0001: apoyada}`) — use it;
   - else reconstruct from the hypothesis note's append-only `history`: the
     `status` of the latest `history` entry with `date <= ADR.date`;
   - else (no history entry that old) — treat as **unknown**.
3. Read the hypothesis's **current** `status`.
4. Compare:

| Situation | Flag |
|---|---|
| unchanged | quiet line: `✓ ADR-XXX — H-YYYY still \`<status>\` since <ADR date>` |
| changed | visible warning (see below) |
| unknown as-of status | visible warning: `cannot verify H-YYYY's status when ADR-XXX was written` |

5. Warning format — one line per affected hypothesis, shown **above/with** the
   ADR:

   ```
   ⚠️ ADR-004 staleness — H-0001 was `apoyada` when written (2026-06-10); now
   `refutada` (2026-08-22, E-0007). The decision may rest on a false premise; re-evaluate.
   ```

6. Only when explicitly updating the ADR: append to `## Revisión de vigencia`
   (append-only, dated) and/or write a fresh `cites_status:` snapshot. Do **not**
   rewrite `## Decisión`, `## Contexto`, or `## Alternativas consideradas`.

## Severity ordering

1. **`refutada` / `evidencia_mixta`** — high: the support the ADR leaned on is
   gone or contested.
2. **unknown as-of status** — high: can't tell, treat as suspect.
3. **`inconclusa`** — medium: support weakened.
4. **`propuesta` → `apoyada` / `en_experimento`** — low / positive: the premise
   got *stronger*; note it, no alarm.

## Batch mode

Sweep every ADR; print a table of only the stale/unknown ones:

```
| ADR | hypothesis | as-of | now | since | severity |
|-----|-----------|-------|-----|-------|----------|
```

If none are stale, say so in one line.

## Cuando se decide revisar la decisión

`## Revisión de vigencia` (procedure step 6) records the **flag**. A flag can sit
there indefinitely with the decision still standing — that is a valid state
("reviewed, still holds"). This section is for the other case: a human decides the
decision itself must **change**. Then walk the full Nygard supersession:

1. **Draft a new ADR** from `${CLAUDE_PLUGIN_ROOT}/templates/adr-template.md`:
   - next `ADR-XXX` id, `date:` today, **`status: Accepted`**;
   - `supersedes: ADR-OLD` in frontmatter;
   - `cites:` the evidence **as it stands now**, with a fresh `cites_status:`
     snapshot (today's statuses);
   - `## Contexto` opens by naming the staleness finding that forced the revisit
     (which hypothesis changed, from what to what, which experiments);
   - `## Decisión` states the new decision in the present tense.
2. **On the old ADR:** set **`status: Superseded by ADR-XXX`**. Do **not** touch
   `## Decisión`, `## Contexto`, or `## Alternativas consideradas`. Append one
   final line to its `## Revisión de vigencia`:
   `Reemplazado por ADR-XXX el <fecha> — <razón + el flag de adr-check que lo motivó>.`
3. **Commit:** `Supersede ADR-OLD with ADR-XXX`.

An ADR is never deleted or rewritten — the chain `ADR-OLD (Superseded) → ADR-XXX
(Accepted)` is the record of how the decision evolved.

## ADR template

`${CLAUDE_PLUGIN_ROOT}/templates/adr-template.md` carries: `status:` (`Accepted` | `Superseded by
ADR-XXX` | `Deprecated`), an optional `supersedes:` field, an optional
`cites_status:` snapshot, and a reminder to run this check when the ADR is shown.
When authoring an ADR that cites a hypothesis, fill `cites_status:` so later
checks are exact rather than reconstructed from `history`.

## Common mistakes

- **Rewriting the old ADR's decision.** It is a dated record. Flag → `## Revisión
  de vigencia`; act on the flag → new ADR + old one's `status: Superseded by …`,
  contents untouched.
- **Editing `status` to something outside the ADR lifecycle.** `status:` on an
  ADR is Nygard vocabulary (`Accepted` / `Superseded by …` / `Deprecated`), a
  different enum from a hypothesis `status` — this is not an
  `update-confidence` concern.
- **Superseding on a flag alone.** The flag is a prompt to review; a human
  decides whether the decision actually changes.
- **Ignoring a positive change.** A cited hypothesis reaching `apoyada` still
  gets a (calm) note — reviewers should know the ground shifted.
- **Only checking `cites_status`.** Fall back to `history`-as-of-date; only then
  "unknown".
- **Checking papers.** `P-XXXX` ids don't have a status — skip them.

## Related

- `update-confidence` — writes the hypothesis `history` entries this check reads.
- `${CLAUDE_PLUGIN_ROOT}/templates/adr-template.md` — `status:` / `supersedes:` / `cites_status:` fields
  and the usage reminder.
