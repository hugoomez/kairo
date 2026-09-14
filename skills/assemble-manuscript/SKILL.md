---
name: assemble-manuscript
description: Use when a project has hypotheses on a shared `paper_thread` marked `linea_publicacion: true` and wants a manuscript draft assembled from them. Gates on a rigor check first — `apoyada` status (which structurally guarantees replication) AND `completo`-tier preregistration (power-justified thresholds) on every adjudicating experiment — before drafting anything. Refuses to draft around evidence that doesn't clear that bar and says exactly what's missing, per hypothesis, rather than silently omitting or downgrading rigor. Assembles Introducción / Trabajo relacionado / Método / Resultados / Discusión from the project's Estado-del-arte.md and the qualifying hypotheses' own sections, with real APA citations built from Papers/*.md — never the internal `P-XXXX §Sección` shorthand.
---

# Assemble Manuscript

## Overview

Turns a publication-track thread of **resolved** hypotheses into a manuscript
draft. The rigor gate is not a formality bolted onto drafting — it is the
first thing this skill does, and refusing (in whole or in part) is a normal,
expected outcome, not an error state to work around.

This skill never lowers the bar to produce a draft. If nothing in a thread
qualifies, the right output is a clear list of what's missing, not a
manuscript built on `ligero`-tier thresholds or a hypothesis that's only
`en_experimento`.

## When to use

- A project has ≥ 1 hypothesis with `linea_publicacion: true` and a shared
  `paper_thread`, and the researcher wants a draft — or wants to know whether
  the thread is ready yet.

**When not to use:**
- The thread's hypotheses are all still `preregistrada` / `en_experimento` /
  `en_cola` — nothing has reached a resolved status yet; there is no evidence
  to draft from. Say so; don't draft an "introduction only" placeholder.
- A general literature survey, not anchored in this project's own resolved
  hypotheses — use `literature-search` or the ARS lit-review skills instead.
  This skill drafts paper *sections built around this project's own findings*,
  using Estado-del-arte.md as context, not a review of the field for its own
  sake.

## Inputs

1. `project: <PROJ-XXX>` (or its slug) and `paper_thread: <slug>` — which
   thread to assemble. If the researcher names a hypothesis instead of a
   thread, read its `paper_thread` field and use that.
2. Every `Projects/<slug>/Hipotesis/*.md` note with that `paper_thread`.
3. `Projects/<slug>/Estado-del-arte.md`.
4. Every `Papers/P-XXXX.md` cited by a qualifying hypothesis or by the
   Estado-del-arte sections used (for `## Referencia` and `authors`/`year`).
5. Every experiment linked from a qualifying hypothesis's `linked_experiment`.

## Step 1 — Collect the thread

Scan the project's `Hipotesis/*.md` for `paper_thread: <slug>` (exact match).
If none are found, refuse: say the thread doesn't exist in this project, and
suggest checking the spelling or that hypotheses were actually tagged with it.

## Step 2 — Rigor gate (per hypothesis, before any drafting)

For **each** hypothesis in the thread, classify it — never as a group verdict
for the whole thread:

| Condition | Classification |
|---|---|
| `linea_publicacion: true`, `status: apoyada`, and **every** hypothesis in `linked_experiment` has `tier: completo` and `experiment_validity: valid` | **Qualifies** — goes into the draft. |
| `linea_publicacion: true`, `status: apoyada`, but at least one `linked_experiment` entry is `tier: ligero` | **Excluded — missing `completo` tier.** Name the exact experiment id(s) and say `completo` tier is required before this can go into a `linea_publicacion` manuscript. As of this writing `preregister-experiment` has not yet implemented `completo` — if so, say that plainly too, rather than implying the researcher just forgot a step. |
| `linea_publicacion: true`, status anything other than `apoyada` (`propuesta`, `en_cola`, `preregistrada`, `en_experimento`) | **Not yet resolved.** Not an error — just not ready. State the current status plainly. |
| `linea_publicacion: false` (or unset) | **Out of scope for this thread's rigor bar.** If the researcher wants it included, they need to set `linea_publicacion: true` and take it through the `completo`-tier path first — this skill does not draft a publication section around a hypothesis nobody flagged as publication-track. |
| Any `linked_experiment` entry `experiment_validity: invalid` | **Excluded — invalid evidence.** Name the invalid experiment; `apoyada` should not have been reachable on invalid evidence, so also flag this as a possible upstream data-integrity gap worth a human look (not this skill's job to fix `update-confidence`'s state, just to refuse to build on it). |

`status: apoyada` already guarantees ≥ 2 independent replicating experiments
(Kairo principle 2, enforced by `update-confidence` — never re-derive or
re-check replication count yourself; the status *is* the guarantee). The only
thing this gate adds on top of `apoyada` is the `completo`-tier check, because
that is the one rigor requirement `update-confidence`'s state machine doesn't
already enforce structurally.

**Report the full classification table to the researcher before drafting**,
even for hypotheses that will end up excluded — that's the "say exactly what's
missing" requirement. If **zero** hypotheses qualify, stop here: no draft, just
the table.

## Step 3 — Draft (only if ≥ 1 hypothesis qualifies)

Use **only** qualifying hypotheses as evidence. Non-qualifying ones may still
inform *framing* in Introducción/Trabajo relacionado if Estado-del-arte
already discusses them as open questions — but never present their claims as
resolved findings.

### Introducción
Motivate from Estado-del-arte's `## Huecos identificados` — which gap(s) this
thread's qualifying hypotheses close. If a gap is marked `cerrado por H-XXXX`
for a qualifying hypothesis, that's the direct link: state the gap, then that
this manuscript reports its closure. Cite the gap's own paper citations.

### Trabajo relacionado
Built from `## Línea de evolución de las ideas`, `## Escuelas de pensamiento en
competencia`, and `## Papers ancla vs. de frontera`. Write real prose — this is
not a re-export of Estado-del-arte's bullet points. Every claim carries a real
in-text citation (below), not the internal `P-XXXX §Sección` shorthand.

### Método
One subsection per qualifying hypothesis:
- The claim (`## Claim`) as the tested prediction.
- The rival hypothesis it was designed to discriminate from (`## Hipótesis
  rival descartada`, from `hypothesis-cycle` Check 4).
- The design of its adjudicating experiment(s): `## Diseño`, `## Plan de
  análisis` (metric + thresholds), and whatever section `preregister-
  experiment`'s `completo` tier records its power / sample-size justification
  in (check that skill's current SKILL.md for the exact section name — this
  is exactly what makes the experiment publication-grade; include it in full,
  not summarized away).

### Resultados
Per qualifying hypothesis: each adjudicating experiment's `## Resultado`
(effect + CI/SE, mechanical verdict), and the **pooled** effect + CI that
`update-confidence` recorded when it set `apoyada` (its `history` entry, or the
Estado-del-arte citation `H-XXXX (E-…, E-…; pooled effect + CI)` — use
whichever has the actual numbers). Never recompute the pooled effect yourself;
report what `combine_effects.py` already produced.

### Discusión
From each qualifying hypothesis's `## Lección`. Tie it back explicitly to
Estado-del-arte's `## Lo establecido vs. lo debatido` — does the finding
resolve a listed debate, sharpen one side, or open a new one? State limitations
honestly: `## Umbral de invalidez` conditions that didn't trigger but were
close, and any `## Enmiendas` on the adjudicating experiments (disclose
amendments as such, per `preregister-experiment`'s own rule).

### Citations — real academic convention, not the internal shorthand

- **In-text:** `(Author, Year)` / `(Author et al., Year)`, built from the
  cited `Papers/P-XXXX.md` frontmatter `authors` / `year`.
- **References list:** one entry per cited paper, copied **verbatim** from
  that paper's own `## Referencia` line — it's already a properly formatted
  reference string; do not rewrite it.
- **Never cite a paper that has no `Papers/P-XXXX.md` note**, and never
  fabricate a reference to fill a gap in the argument. If the argument needs a
  citation you don't have, say so as an open gap rather than inventing one.
- Internal `P-XXXX §Sección` locators are for Kairo's own notes, not this
  document — don't leak them into the manuscript prose. (A traceability
  appendix mapping each in-text citation back to its `P-XXXX` id is fine to
  include, kept clearly separate from the manuscript body.)

## Output

Write `Projects/<slug>/Manuscritos/manuscript-<paper_thread>.md` (create the
`Manuscritos/` folder if it doesn't exist yet — new to this skill; note it in
the vault's folder-conventions doc if it's the first manuscript in that
vault). Frontmatter:

```yaml
project: <PROJ-XXX>
paper_thread: <slug>
status: draft
generated: <YYYY-MM-DD>
hypotheses_included: [H-XXXX, ...]
hypotheses_excluded:
  - id: H-YYYY
    reason: <exact missing-rigor reason from the Step 2 table>
```

This skill **never** edits hypothesis `status`, `linea_publicacion`,
Estado-del-arte.md, or `_digest.md` — it only reads them and writes the new
manuscript note. If the researcher wants an excluded hypothesis included, the
fix is upstream (`preregister-experiment` completo tier, `run-experiment`,
`update-confidence`), not a flag on this skill.

## Common mistakes

- **Refusing (or drafting) the whole thread as one unit.** The gate is
  per-hypothesis; a thread can have some qualifying and some not.
- **Treating `en_experimento` (first support, replication pending) as
  resolved.** Only `apoyada` counts — it's the one that structurally guarantees
  replication.
- **Building on a `ligero`-tier adjudicating experiment for a
  `linea_publicacion` hypothesis.** That is exactly the gap the `completo` tier
  exists to close (see `preregister-experiment`) — never draft around it.
- **Citing with the internal `P-XXXX §Sección` shorthand in the manuscript
  body.** That's Kairo's own cross-reference format, not a publication
  citation — use real in-text citations and the paper's `## Referencia` line.
- **Inventing a citation, or recomputing a pooled effect yourself.** Both are
  fabrication risks — always trace back to an actual `Papers/` note or an
  actual `combine_effects.py` result already on record.
- **Silently dropping a non-qualifying hypothesis instead of naming exactly
  what's missing.** The whole point of the gate is a legible, actionable
  "here's what's not done yet," not a quiet omission.
- **Editing hypothesis status or Estado-del-arte.md from this skill.** It's
  read-only over everything except the new manuscript note.
