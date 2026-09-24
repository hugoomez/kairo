# B-readme — README entries for Block B (B1–B4)

`README.md` is INTEGRATION-owned. These are the exact additions for Block B's
four sub-blocks. Block A's own README pending file edits other rows; apply both.

## 1. Target

`README.md`

## 2. Change

### 2a. `### Skills (\`kairo:<name>\`)` table — add one row after `kairo:paper-to-tool`

```markdown
| `kairo:evolve-program` | **On demand only.** Evaluator-first program search on [OpenEvolve](https://github.com/algorithmicsuperintelligence/openevolve) with Claude Code as the LLM (subscription, never an API key): the evaluator and a held-out split are frozen in an `EVO-XXXX` preregistration first; candidates run in a separate process with a timeout and are scored by a frozen harness they cannot edit; the best program is re-scored once on held-out and the gap reported. Per-run budget, call and iteration caps, a shown estimate and an explicit approval token. The winner is never a result — it becomes a hypothesis for `hypothesis-cycle`. |
```

### 2b. Same table — replace the end of the `kairo:hypothesis-cycle` row

Before: `… Creates a \`propuesta\` note or logs a discard so it is not re-proposed. |`
After:

```markdown
… Reads the project's `_ledger.md` first. Before creating the note, a `fresh-verifier` pass hunts concrete errors in the artifact alone (`errors_found` → filed with `needs_human_review`, never a verdict on truth). Optionally drafts a simplification ladder for an expensive test. Creates a `propuesta` note or logs a discard so it is not re-proposed. |
```

### 2c. Same table — `kairo:update-confidence` row, append before the final `|`

```markdown
 Counts only confirmatory experiments (`scripts/analysis/evidence_gate.py`: exploratory rungs never move status), and runs `fresh-verifier` before any transition to `apoyada` — `errors_found` blocks it.
```

### 2d. Same table — `kairo:preregister-experiment` row, append before the final `|`

```markdown
 Optional step 0: a **simplification ladder** — 2–3 cheap exploratory rungs (`role: exploratory`, `rung: 0–2`, each a `ligero` prereg and a `Claims/` node) run before the confirmatory design, which is frozen afterwards citing which rung informed which decision. Offered, never forced, when the design is expensive or reimplements a method from text.
```

### 2e. `### Subagents` table — add

```markdown
| `fresh-verifier` | A fresh Claude instance that receives **only** the artifact — claim, cited-evidence assertions with the cited source text, the frozen preregistration and the analysis output — never the conversation, the reasoning, or prior critiques (a script builds the packet by allow-list). Returns `no_errors_found` / `errors_found` (location + why + severity) / `cannot_assess`, recorded in the note's `verifications:` list. Complements `second-critic`: that one is a different model family judging test *design*; this one is the same family with no shared context, hunting concrete *errors*. Both stay. |
```

### 2f. `### Templates & scripts` — add bullets after the `scripts/second_critic/…` bullet

```markdown
- `templates/claim-template.md` — `Claims/C-XXXX.md` notes: lemmas,
  intermediate results, simplification-ladder rungs and evolution lineage,
  status `pendiente | probado | fallido | refutado`.
- `scripts/ledger/` — the state ledger. `claim_status.py` is the **single
  writer** of claim status (hypothesis status stays with `update-confidence`).
  `build_graph.py` builds the dependency graph across `Hipotesis/` and `Claims/`
  from `depends_on:`, flags cycles and dangling references (`crítico`) and
  propagates refutations / failures to every dependent as `importante` flags —
  never rewriting a note — and writes a compact per-project `_ledger.md` that
  `hypothesis-cycle` reads instead of re-reading the whole project.
  `verifier_packet.py` / `verifications.py` build the fresh-verifier's
  context-free packet and append its `verifications:` entries.
- `scripts/analysis/evidence_gate.py` — the mechanical rule that exploratory
  experiments never count as evidence (`check`, `gather`).
```

### 2g. `## Vault conventions the skills assume` — layout block

Replace the `Projects/<slug>/` block with:

```
Projects/<slug>/
  _hub.md                   PROJ-XXX project hub
  Hipotesis/                H-XXXX <slug>.md
  Claims/                   C-XXXX.md  (lemmas, intermediate results, rungs, lineage; created on first use)
  Experimentos/             E-XXXX.md  (+ logs/)
  Evolucion/                EVO-XXXX.md (evolve-program preregistrations; created on first use)
  Producto/                 ADR-XXX.md, F-XXX.md (tasks)
  Manuscritos/              manuscript-<paper_thread>.md (assemble-manuscript; created on first use)
  Estado-del-arte.md        the state-of-the-art map
  _digest.md                derived hypothesis table
  _ledger.md                derived dependency ledger (build_graph.py)
```

and in **ID schemes** add `C-` claims and `EVO-` evolution runs (vault-wide,
zero-padded, max-existing + 1). After the hypothesis status enum add:

```markdown
**Claim status enum** (`Claims/` only, written only by `claim_status.py`):

```
pendiente | probado | fallido | refutado
```

**Experiment roles** (docs/v3-interfaces.md §1d): `role: confirmatory |
exploratory`, `rung: 0–3`. Only confirmatory experiments move a hypothesis's
status.
```

### 2h. Core principles — append to principle 4

```markdown
   Exploratory experiments (simplification-ladder rungs, program evolution)
   inform designs and never count as evidence; before any promotion to
   `apoyada`, a fresh verifier that never saw the reasoning checks the
   artifact for concrete errors.
```

### 2i. `## Optional companion: DeepInfra` — append a paragraph

```markdown
**Not the same as `fresh-verifier`.** `second-critic` (this section) buys
*model-family* independence on test-design judgement, opt-in. `fresh-verifier`
buys *context* independence — a fresh Claude instance that sees only the
artifact — on concrete errors, always on, subscription only. They catch
different failures and both stay.
```

## 3. Verify

1. `grep -n "evolve-program\|fresh-verifier\|_ledger.md\|evidence_gate\|claim_status" README.md`
   → each appears in the sections above.
2. Every path named exists after merging `v3-block-b`:
   `ls skills/evolve-program/SKILL.md agents/fresh-verifier.md templates/claim-template.md scripts/ledger/build_graph.py scripts/ledger/claim_status.py scripts/ledger/verifier_packet.py scripts/ledger/verifications.py scripts/analysis/evidence_gate.py`.
3. `claude plugin validate .` passes.
