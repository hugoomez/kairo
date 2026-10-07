---
name: theorem
description: Use when a Kairo project needs a lemma or theorem stated, proved, verified or checked — "demuestra que…", "añade el lema…", "verifica la demostración de C-0004", "escribe la comprobación numérica de C-0007". Creates the Claims/ node (kind lema | teorema) with depends_on, drafts the proof, writes the numerical sanity check, and runs the fresh verifier on the proof. Never signs off and never marks anything probado on its own: the rigor gate needs the researcher's sign-off on the statement and the proof.
---

# theorem — lemmas and theorems under the rigor gate

## Overview

In a theoretical project every lemma and theorem is a `Claims/` node
(`kind: lema | teorema`) whose `depends_on` lists the lemmas and hypotheses it
uses. `build_graph.py` flags everything downstream of a lemma that fails, so
one broken step is visible everywhere it matters.

A lemma or theorem becomes `probado` only through the three-layer gate
(`scripts/ledger/claim_gate.py`), on its **current** text — every record is
bound to a sha256 and an edit voids it:

1. the **fresh verifier** on the proof;
2. the **researcher's sign-off on the statement and on the proof** —
   `signoff.py`, which refuses inside any agent session, including yours;
3. a **numerical sanity check** on small cases, whenever feasible — or a
   stated reason why not, which the researcher also signs off.

Plus: every dependency must already be established. A formalisation (e.g.
Lean) is optional, only for central results, recorded in `formal:`, and never
replaces the researcher's review of the statement.

## What you do — and what you never do

You may: state (or take the researcher's statement verbatim), draft the proof,
write the check script, run the verifier, report. You **never**:

- sign anything off, or ask the researcher to let you (the script refuses you anyway);
- run the numerical check yourself — it is code; the researcher approves it
  in the interface (`numeric_check.py run`), or runs it in their terminal;
- set `probado` unless `claim_gate.py check` exits 0 — then `claim_status.py
  set --status probado` (which re-checks the gate) with the evidence
  "puerta de rigor: verificador + visto bueno + comprobación numérica";
- change a statement the researcher gave you. If you think it is wrong or
  too strong, say so and propose a new wording; they decide.

## Steps

### 1. The node

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/ledger/claim_status.py" new \
  --project-dir Projects/<slug> --kind <lema|teorema> \
  --statement "<the statement>" --depends-on <C-XXXX H-XXXX …> --by <agent id>
```

List in `--depends-on` every lemma / hypothesis the proof will use — the
verifier only sees their statements, so an unlisted dependency makes a step
unverifiable. Split a long proof into lemmas: each one is checked on its own.
If the project has a paper outline, bind the node to its section:
`python "${CLAUDE_PLUGIN_ROOT}/scripts/manuscript/manuscript.py" bind
--project-dir Projects/<slug> --thread <paper_thread> --section <id> --id <C-XXXX>`.

### 2. The proof

Write `## Demostración` in full, step by step, using only the statement and
the statements of `depends_on`. No "clearly", no skipped cases: whatever you
would not survive a referee on, the verifier will flag.

### 3. The numerical check

Write `Projects/<slug>/Claims/checks/<C-XXXX>.py`: plain Python (standard
library, or what the project's environment already has) that tests the
statement on small cases and **exits 0 only if every case agrees**, printing
which case failed otherwise. Test the statement, not the proof: enumerate
small n, random small instances with a fixed seed, exhaustive small
structures. Describe in `## Comprobación numérica` what it covers.

If no computational check is meaningful (infinite-dimensional objects,
statements with no finite instance), write why in `## Comprobación numérica`
and tell the researcher: they record it with `numeric_check.py infeasible`
and sign off the reason.

### 4. The verifier

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/ledger/verifier_packet.py" \
  --vault <vault> --note Projects/<slug>/Claims/<C-XXXX>.md --out <tmp>/packet.md --store
```

Dispatch `fresh-verifier` with the stored packet's path (`packet`) as its whole
prompt — never the packet's text; its `Read` is held to that file by the hook. Record its verdict with
the report and the packet's sha256 — the gate matches that sha256 against the
packet of the current text:

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/ledger/verifications.py" append \
  --note Projects/<slug>/Claims/<C-XXXX>.md --verifier kairo/fresh-verifier@<v> \
  --model <model> --verdict <verdict> --scope note --report <tmp>/report.md \
  --packet-sha256 <sha256 --store printed>
```

`errors_found`: fix the proof (or the statement, with the researcher), and
verify again. Never argue a finding away.

### 5. Commit and report

Commit the claim note and its check script alone. Report
`claim_gate.py check --vault <vault> --claim <C-XXXX>`: what is done and,
exactly, what the researcher still has to do (sign off the statement, the
proof; approve the numerical check).

## Common mistakes

- **Marking `probado` because the proof "looks fine".** Only the gate decides.
- **A check script that tests the proof's own steps** instead of the
  statement on independent small cases.
- **Leaving a used lemma out of `depends_on`**: the verifier cannot see it,
  and a later failure of that lemma will not be flagged here.
- **Editing a signed statement "for clarity"**: it voids the sign-off and the
  numerical check. Propose the change; the researcher re-signs.
