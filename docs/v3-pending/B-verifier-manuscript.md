# B-verifier-manuscript — wire `fresh-verifier` into `assemble-manuscript`

Block B2 (fresh-instance verifier). Deferred to INTEGRATION per
`docs/v3-interfaces.md` §2 ("B2's wiring into `skills/assemble-manuscript`").
Block A also edits this skill (citation gate, AI disclosure). If A's edits have
moved the anchor text below, insert at the same logical place: the rigor-gate
table, the paragraph after it, the end of Step 3, the Output frontmatter, and
Common mistakes.

Depends on (all in `v3-block-b`): `agents/fresh-verifier.md`,
`scripts/ledger/verifier_packet.py`, `scripts/ledger/verifications.py`, and
the `update-confidence` gate before `apoyada`.

## 1. Target

`skills/assemble-manuscript/SKILL.md`

## 2. Change

### 2a. Rigor-gate table: new row

**Insert after** this row (currently the last row of the Step 2 table):

```
| Any `linked_experiment` entry `experiment_validity: invalid` | **Excluded — invalid evidence.** Name the invalid experiment; `apoyada` should not have been reachable on invalid evidence, so also flag this as a possible upstream data-integrity gap worth a human look (not this skill's job to fix `update-confidence`'s state, just to refuse to build on it). |
```

**Insert:**

```
| Latest `scope: note` entry in the hypothesis's `verifications:` is not `no_errors_found` (`errors_found`, `cannot_assess`, or no entry at all) — read it with `python ${CLAUDE_PLUGIN_ROOT}/scripts/ledger/verifications.py latest --note <H-XXXX.md>` | **Excluded — fresh verification not clean.** Name the governing entry (verdict, date, `verifier`, `model`) and copy its findings, with their `crítico` / `importante` / `menor` tags and locations, from the note's `## Verificación independiente`. With no entry, say "never verified". A human override logged in `history` at the `apoyada` gate does **not** clear this: a manuscript needs a re-verification (a new appended entry) that returns `no_errors_found`. Never re-run the verifier here to "fix" the gate, and never edit an entry. |
```

### 2b. Paragraph after the table

**Before** (current text):

```
`status: apoyada` already guarantees ≥ 2 independent replicating experiments
(Kairo principle 2, enforced by `update-confidence` — never re-derive or
re-check replication count yourself; the status *is* the guarantee). The only
thing this gate adds on top of `apoyada` is the `completo`-tier check, because
that is the one rigor requirement `update-confidence`'s state machine doesn't
already enforce structurally.
```

**After:**

```
`status: apoyada` already guarantees ≥ 2 independent replicating experiments
(Kairo principle 2, enforced by `update-confidence` — never re-derive or
re-check replication count yourself; the status *is* the guarantee). This gate
adds two things on top of `apoyada`: the `completo`-tier check, which
`update-confidence`'s state machine doesn't enforce structurally, and a clean
latest fresh verification (`scope: note` → `no_errors_found`). `apoyada` can be
reached through a logged human override of the verifier, and a publication
must not rest on that.
```

### 2c. Step 3: verify each drafted section

**Insert after** the end of the `### Citations — real academic convention, not
the internal shorthand` subsection. Its last bullet currently ends:

```
  include, kept clearly separate from the manuscript body.)
```

**Insert:**

```
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
     --note <manuscript note> --verifier kairo/fresh-verifier@1.0.0 \
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

Known limit (packet builder v1.0.0): the manuscript body uses APA in-text
citations, not `P-XXXX §locator`, so a section packet does not carry the
cited papers' source text. Claims resting on a citation in `Introducción` /
`Trabajo relacionado` can come back `cannot_assess` for that reason. That is an
honest verdict, not a failure.

### How the AI-use disclosure reads these entries (contract §1b)

The disclosure (Block A, A2) reports **every** entry in `verifications:` on the
manuscript note and on each included hypothesis, never a curated subset. Each
entry is one factual line: "A fresh-verifier instance (`<verifier>`, model
`<model>`, `<date>`) found no errors in `<scope>`" / "found errors in
`<scope>`" / "could not assess `<scope>`". Never write "verified", "correct",
or "validated": `no_errors_found` means only that a verifier found no errors in
that scope. When an `errors_found` entry was later followed by a
`no_errors_found` re-verification of the same scope, report both, in date
order.
```

### 2d. Output frontmatter

**Before:**

```yaml
hypotheses_excluded:
  - id: H-YYYY
    reason: <exact missing-rigor reason from the Step 2 table>
```

**After:**

```yaml
hypotheses_excluded:
  - id: H-YYYY
    reason: <exact missing-rigor reason from the Step 2 table>
# verifications — appended only by scripts/ledger/verifications.py
# (contract §1b), one entry per fresh-verifier run on a section. Never hand-edited.
verifications: []
```

In the paragraph after the frontmatter, **before**:

```
This skill **never** edits hypothesis `status`, `linea_publicacion`,
Estado-del-arte.md, or `_digest.md` — it only reads them and writes the new
manuscript note.
```

**After:**

```
This skill **never** edits hypothesis `status`, `linea_publicacion`,
Estado-del-arte.md, or `_digest.md`. It only reads them and writes the new
manuscript note, including that note's own `verifications:` entries and
`## Verificación independiente`.
```

### 2e. Common mistakes: append

```
- **Drafting around a hypothesis whose latest fresh verification isn't
  `no_errors_found`.** The gate refuses it and names the finding. Re-verify
  upstream, and don't treat a logged human override as clean.
- **Calling a `no_errors_found` section "verified" or "correct"**, in the
  manuscript or its disclosure. It means only that a verifier found no errors
  in that scope.
- **Giving `fresh-verifier` more than the packet**, such as drafting notes,
  the gate table, or hints about where to look.
```

## 3. Verify

1. **Gate refuses a hypothesis that isn't clean.** On a scratch copy of a
   project, take one `apoyada` + `completo` hypothesis in a
   `linea_publicacion` thread whose `verifications:` ends with an
   `errors_found` entry (make one with `verifications.py append ... --verdict
   errors_found --report <a saved fresh-verifier report>`). Run
   `assemble-manuscript`. The Step 2 table must show **Excluded — fresh
   verification not clean** with the verdict, date and findings. Repeat with no
   `verifications:` key: it must say "never verified".
2. **Clean hypothesis passes.** Append a `no_errors_found` entry to the same
   hypothesis (a re-verification). It must now qualify.
3. **Section entries are written.** After drafting, the manuscript note's
   frontmatter carries one `scope: section:<heading>` entry per drafted
   section. Check each heading exists verbatim with
   `verifications.py list --note <manuscript>`, and check that
   `## Verificación independiente` has one dated entry per run with a packet
   sha256 matching the `manifest-<heading>.json` of that run.
4. **Disclosure wording.** The generated AI-use disclosure lists every entry,
   including superseded `errors_found` ones, and contains none of the words
   "verified", "correct", or "validated" for a `no_errors_found` entry.
5. **Isolation.** For one section, open `<tmp>/packet-<heading>.md`. It
   contains only that section plus any `--experiment` / `--analysis-output`
   material, and the manifest lists every other section as excluded.
