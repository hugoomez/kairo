# B-run-experiment-exploratory — run-experiment on an exploratory rung

B1 (simplification ladder) makes `preregister-experiment` freeze **exploratory**
rung experiments (`role: exploratory`, `rung: 0..2`, contract §1d). They are run
by `run-experiment`, which is Block A's file. Nothing breaks if this change is
never applied — `update-confidence` refuses every trigger that carries an
exploratory experiment (it calls `scripts/analysis/evidence_gate.py check`
first, exit 3 = refused) — but without it `run-experiment` fires two triggers
that are then refused, and never records the rung's outcome in its `Claims/`
node. This file is the clean path.

## 1. Target

`skills/run-experiment/SKILL.md` (owner: A).

## 2. Change

### 2a. Step 1 — after the paragraph ending "…this skill never edits hypothesis `status`."

Insert:

```markdown
**Exploratory rung (`role: exploratory`):** skip the `update-confidence`
trigger — a rung never moves hypothesis status (contract §1d; enforced by
`update-confidence` via `scripts/analysis/evidence_gate.py`). The hypothesis can
be `propuesta`, `preregistrada` or `en_experimento` while its rungs run; leave it
where it is. Everything else in this skill — literal implementation, pre-flight,
sanity checks, validity, the frozen mechanical analysis, the log — applies to a
rung exactly as to a confirmatory run. A rung is cheap, not sloppy.
```

### 2b. Step 8 — add a bullet after the "`secondary_hypotheses` (if any)" bullet

```markdown
- **Exploratory rung (`role: exploratory`):** fire **no** `update-confidence`
  trigger, valid or not. Instead settle the rung's `Claims/` node (created by
  `preregister-experiment` at `pendiente`; its `source:` is this experiment) with
  the single claim writer, then refresh the ledger:
  ```
  python ${CLAUDE_PLUGIN_ROOT}/scripts/ledger/claim_status.py set \
      --note <Projects/<slug>/Claims/C-XXXX.md> --status <probado|refutado|fallido> \
      --by run-experiment --evidence "<E-XXXX: validity, verdict, effect — one line>"
  python ${CLAUDE_PLUGIN_ROOT}/scripts/ledger/build_graph.py --vault <vault> --project <slug> --write
  ```
  Mapping: valid run, relaxed prediction held → `probado`; valid run, relaxed
  prediction contradicted → `refutado`; invalid run / did not complete /
  instrument failed → `fallido`. A failed or refuted rung is recorded exactly
  like a passing one — it is often the most useful rung — never skipped, never
  re-run silently until it passes. Write the `## Resultado` of the claim note
  (`## Resultado` / `## Qué informa` body sections) with the exact analysis
  output, as for the experiment note.
```

### 2c. Common mistakes — add

```markdown
- **Firing `update-confidence` for an exploratory rung, or hiding a failed
  one.** Rungs never move hypothesis status and never enter `combine_effects.py`;
  their outcome goes to their `Claims/` node via `claim_status.py`, failed ones
  included.
```

## 3. Verify

1. Grep: `grep -n "role: exploratory" skills/run-experiment/SKILL.md` → the two
   new passages.
2. Dry run on a synthetic vault (temp dir, never the real vault): a hypothesis at
   `propuesta`, an `E-XXXX` with `role: exploratory`, `rung: 0`,
   `experiment_validity: valid`, and a `Claims/C-XXXX.md` rung node at
   `pendiente` (`claim_status.py new --kind rung --role exploratory --rung 0
   --about H-XXXX --source E-XXXX …`). Walk step 8 by hand: `claim_status.py set
   … --status probado` succeeds; `build_graph.py … --write` lists the rung under
   `## Escaleras de simplificación` with `probado`; the hypothesis note is
   byte-identical before/after.
3. Negative: `python scripts/analysis/evidence_gate.py check --experiment <that
   E-XXXX>` exits 3 (`REFUSED … exploratory`) — the same refusal
   `update-confidence` applies if the trigger were fired anyway.
