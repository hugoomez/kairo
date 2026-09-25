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
   A note with `send: never` in its frontmatter is **not** read (the vault's
   `send_guard` hook blocks it anyway): a hypothesis, experiment or paper marked
   that way can't be drafted from. A flagged hypothesis or experiment is
   excluded in Step 2 with reason `send: never` (`importante` — the researcher
   drafts that part by hand). A flagged paper fails the Step 2b citation gate by
   name.
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
| The hypothesis note, or any of its `linked_experiment` notes, has `send: never` | **Excluded — `send: never`** (`importante`). Don't open it; the researcher drafts that part by hand or unmarks the note. |
| Any `linked_experiment` entry `experiment_validity: invalid` | **Excluded — invalid evidence.** Name the invalid experiment; `apoyada` should not have been reachable on invalid evidence, so also flag this as a possible upstream data-integrity gap worth a human look (not this skill's job to fix `update-confidence`'s state, just to refuse to build on it). |
| Latest `scope: note` entry in the hypothesis's `verifications:` is not `no_errors_found` (`errors_found`, `cannot_assess`, or no entry at all), **or** the latest entry of any `section:<heading>` scope is `errors_found` — read them with `python ${CLAUDE_PLUGIN_ROOT}/scripts/ledger/verifications.py list --note <H-XXXX.md>` (the last entry per scope, in file order, governs) | **Excluded — fresh verification not clean.** Name the governing entry (verdict, date, `verifier`, `model`) and copy its findings, with their `crítico` / `importante` / `menor` tags and locations, from the note's `## Verificación independiente`. With no entry, say "never verified". A human override logged in `history` at the `apoyada` gate does **not** clear this: a manuscript needs a re-verification (a new appended entry) that returns `no_errors_found`. Never re-run the verifier here to "fix" the gate, and never edit an entry. (The section-scope clause matches the AI-use disclosure, which flags that same case `crítico`.) |

`status: apoyada` already guarantees ≥ 2 independent replicating experiments
(Kairo principle 2, enforced by `update-confidence` — never re-derive or
re-check replication count yourself; the status *is* the guarantee). This gate
adds two things on top of `apoyada`: the `completo`-tier check, which
`update-confidence`'s state machine doesn't enforce structurally, and a clean
latest fresh verification (`scope: note` → `no_errors_found`). `apoyada` can be
reached through a logged human override of the verifier, and a publication
must not rest on that.

**Report the full classification table to the researcher before drafting**,
even for hypotheses that will end up excluded — that's the "say exactly what's
missing" requirement. If **zero** hypotheses qualify, stop here: no draft, just
the table.

## Step 2b — Citation gate (before any drafting)

Every reference the manuscript will rely on must be proven to exist, match its
note's metadata, and not be retracted or withdrawn. This complements the
locator re-verification in Step 3 (which checks a cited section says what we
claim); it does not replace it.

1. **Build the bibliography set:** every `P-XXXX` cited by a qualifying
   hypothesis (`linked_papers:` and its `## Justificación`) plus every paper
   cited in the Estado-del-arte sections Step 3 will use.
2. **Run the gate live** — stored `resolved:` fields are never trusted here:

   ```
   python "${CLAUDE_PLUGIN_ROOT}/scripts/citations/resolve_refs.py" --papers <vault>/Papers \
     --only <P-id> <P-id> ... --gate --json
   ```

3. **Exit `0`** → every entry is `resolved` with an `exact` / `close` match and
   is not retracted / withdrawn; continue. A `close` match's diff (e.g. year off
   by one, preprint vs proceedings) is listed to the researcher as `menor`.
4. **Exit `2`** → **`crítico` — refuse to draft.** In its own callout at the
   top, name **each** failing entry with its status and the script's reason:
   `mismatch` (possible chimeric citation — the note mixes two papers; fix it
   by hand), `retracted` / `withdrawn` (it can never be support — drop it from
   the argument, and flag the hypotheses that lean on it), `unresolved` (not in
   OpenAlex, or only a fallback source confirmed it), `skipped_send_never` (the
   note is marked not-to-send, so it can't be cited from here — the researcher
   adds that reference by hand or unmarks it). No partial draft "around" the
   failing references.
5. **Exit `1`** → the gate could not run or could not prove the result (e.g.
   OpenAlex unreachable or its keyless budget spent). Treat it as **not
   passed**: no draft. Say so, and point to `OPENALEX_API_KEY`
   (`https://openalex.org/settings/api`) if the script says it's missing.

After drafting, if Step 3 ended up citing a paper that was not in the set,
re-run the gate on the final references list before writing the note — the
gate covers what the manuscript actually cites.

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

### Fresh verification of each drafted section

Once the manuscript note is written (see Output), run `fresh-verifier` once
per drafted `##` section. It is a fresh Claude instance that receives only a
mechanically built packet, never this session's drafting reasoning.

1. Build the packet for the section. For `Resultados` and `Método`, add the
   qualifying hypotheses' adjudicating experiments and the saved
   `combine_effects.py` output, so the numbers can be checked against their
   source:
   ```
   python ${CLAUDE_PLUGIN_ROOT}/scripts/ledger/verifier_packet.py \
     --vault <vault root> --note <Manuscritos/manuscript-<thread>.md> \
     --section "<exact heading text>" \
     [--experiment <E-XXXX.md> ...] [--analysis-output <combine.txt> ...] \
     --out <tmp>/packet-<heading>.md --manifest <tmp>/manifest-<heading>.json
   ```
2. Dispatch `fresh-verifier` with the packet file's text as the entire prompt,
   verbatim, with nothing added.
3. Record the result on the **manuscript note**, whatever the verdict:
   ```
   python ${CLAUDE_PLUGIN_ROOT}/scripts/ledger/verifications.py append \
     --note <manuscript note> --verifier kairo/fresh-verifier@1.1.0 \
     --model <model id the agent reported> --verdict <verdict> \
     --scope "section:<exact heading text>" \
     --report <tmp>/report-<heading>.txt --packet-sha256 <sha256>
   ```
   `--scope` must name a heading that exists verbatim in the note; the script
   refuses otherwise. Findings go to the note's `## Verificación independiente`.

`errors_found` / `cannot_assess` on a section: report the findings to the
researcher with their severity tags and leave the section as drafted. Never
silently rewrite it to make the finding go away. If the researcher fixes the
section, re-verify, which appends a new entry. The latest entry per scope
governs, and old entries are never edited.

Known limit (packet builder v1.x): the manuscript body uses APA in-text
citations, not `P-XXXX §locator`, so a section packet does not carry the
cited papers' source text. Claims resting on a citation in `Introducción` /
`Trabajo relacionado` can come back `cannot_assess` for that reason. That is an
honest verdict, not a failure.

Run these verifications **before** Step 4: the disclosure reads the
manuscript note's `verifications:`, so it can only report runs that already
happened. If a section is re-verified later, re-run Step 4.

### How the AI-use disclosure reads these entries (contract §1b)

The disclosure (Step 4) reports **every** entry in `verifications:` on the
manuscript note and on each included hypothesis, never a curated subset. Each
entry is one factual line: "A fresh-verifier instance (`<verifier>`, model
`<model>`, `<date>`) found no errors in `<scope>`" / "found errors in
`<scope>`" / "could not assess `<scope>`". Never write "verified", "correct",
or "validated": `no_errors_found` means only that a verifier found no errors in
that scope. When an `errors_found` entry was later followed by a
`no_errors_found` re-verification of the same scope, report both, in date
order.

## Step 4 — Declaración de uso de IA (generated from records, never written by hand)

Every manuscript this skill drafts carries an AI-use disclosure, built mechanically from what Kairo
recorded. The disclosure is required by all target venues. The strictest combination: ICLR 2027 wants a
dedicated section listing, task by task, what AI did, what it didn't, and how its output was reviewed.
Science wants the tool, its version and the prompt. Nature and Science want Methods placement and ban
AI-generated images. Do not write or paraphrase the disclosure yourself.

1. Write the manuscript note first (Output below), including `generated_by`, and finish Step 3's
   fresh verification of each drafted section. Then run:

   ```
   python "${CLAUDE_PLUGIN_ROOT}/skills/assemble-manuscript/scripts/ai_disclosure.py" \
     --vault <vault> --project <slug> --thread <paper_thread> \
     --hypotheses <qualifying ids from Step 2, comma-separated> \
     --manuscript Projects/<slug>/Manuscritos/manuscript-<paper_thread>.md \
     --researcher "<name as it appears in history by:>"   # repeatable flag
   ```

   `--researcher`: a history `by:` counts as a person only when it matches a
   name passed here. **Ask the researcher for the name(s) — never guess.**
   Without it, a `by:` that is neither a model nor a Kairo component is
   reported as "no consta si fue una persona o un agente" and never credited to
   the researcher.

   Exit `2` = wrong project/thread/id/path (fix the input). Exit `1` = the script failed: say so and do not
   write a disclosure by hand. Pass `--format json` to read the flags programmatically.
2. Insert its stdout **verbatim** into the manuscript as the section `## Declaración de uso de IA`, placed
   after `## Discusión` and before the references list. Keep its two internal subsections
   (`### Trazabilidad …` and `### Avisos de cumplimiento …`). They are marked "eliminar antes de enviar"
   and are the researcher's checklist.
3. Add one pointer sentence at the end of `## Método`: "El uso de herramientas de IA en cada etapa de este
   trabajo se detalla en la sección *Declaración de uso de IA*." Add the same sentence in an
   `## Agradecimientos` section (create it if absent). Science asks for the disclosure in Methods or
   Acknowledgments **and** in the cover letter, so tell the researcher to copy the English version into
   the cover letter.
4. Show the researcher the flags, most severe first:
   - **`crítico`** (a verification whose latest verdict for that scope is `errors_found`): in its own
     callout at the top. The manuscript must not describe that note or section as verified, and the
     affected content should be fixed and re-verified before submission.
   - `importante` / `menor`: list them. Each is a gap that would fail the strictest venue: an unrecorded
     model, unrecorded code authorship, `send: never` notes the researcher has to declare by hand,
     unrecorded prompts, and the unconfirmed responsibility statement.
5. Lines marked `[PENDIENTE — …]` are for the researcher to confirm or correct: human responsibility,
   no AI authorship, no AI-generated figures. Never fill them in yourself, and never set
   `ai_disclosure_confirmed_by`. Only the researcher does that.

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
generated_by:
  origin: agent
  model: <model id of the session drafting the manuscript, e.g. claude-opus-5-5>
  skill_version: kairo/assemble-manuscript@<plugin version>
ai_disclosure:
  script: kairo/ai_disclosure.py@<version printed by --version>
  generated: <YYYY-MM-DD>
  flags: {critico: <n>, importante: <n>, menor: <n>}
# set ONLY by the researcher, by hand, after reading the disclosure — never by this skill:
# ai_disclosure_confirmed_by: <name>
# ai_disclosure_confirmed: <YYYY-MM-DD>
# verifications — appended only by scripts/ledger/verifications.py
# (contract §1b), one entry per fresh-verifier run on a section. Never hand-edited.
verifications: []
```

This skill **never** edits hypothesis `status`, `linea_publicacion`,
Estado-del-arte.md, or `_digest.md`. It only reads them and writes the new
manuscript note, including that note's own `verifications:` entries and
`## Verificación independiente`. If the researcher wants an excluded hypothesis included, the
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
- **Trusting stored `resolved: true` instead of running the gate.** Step 2b
  re-checks live with `--gate`; a paper can be retracted after ingestion.
  Exit 1 is not a pass.
- **Writing or "improving" the AI-use disclosure by hand.** It is generated from the records by
  `ai_disclosure.py`, and every sentence traces to a note field. Paraphrasing it can invent a contribution
  or drop a gap. Re-run the script instead.
- **Presenting a `no_errors_found` verification as "verified correct".** It only means a verifier found no
  errors in that scope. A scope whose latest verdict is `errors_found` (a `crítico` flag) must not be
  called verified anywhere in the manuscript.
- **Claiming human involvement the records don't show.** No record means the disclosure says "no consta".
  Filling in `[PENDIENTE]` lines or `ai_disclosure_confirmed_by` is the researcher's job, never this skill's.
- **Quoting a `send: never` note's content into the disclosure.** The script outputs only its id and a
  flag. The researcher declares that contribution by hand.
- **Drafting around a hypothesis whose latest fresh verification isn't
  `no_errors_found`.** The gate refuses it and names the finding. Re-verify
  upstream, and don't treat a logged human override as clean.
- **Calling a `no_errors_found` section "verified" or "correct"**, in the
  manuscript or its disclosure. It means only that a verifier found no errors
  in that scope.
- **Giving `fresh-verifier` more than the packet**, such as drafting notes,
  the gate table, or hints about where to look.
