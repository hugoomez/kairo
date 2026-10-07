---
name: critique
description: Use when the researcher asks for a devil's-advocate critique of one artifact in a Kairo vault — a hypothesis (H-XXXX), an ADR, an experiment design, a manuscript section, or a decision recorded in the lab notebook ("critica H-0003", "abogado del diablo sobre ADR-004", "¿qué fallaría en esta decisión?"). Builds an allow-list packet, has the isolated devils-advocate agent attack it, and files the objections as a non-citable Criticas/CR-XXXX note. Never changes any status, verification or flag.
---

# critique — devil's advocate on demand

## Overview

The researcher wants an artifact attacked before relying on it. This skill
gets the strongest honest objections from a critic that **did not see how the
artifact was made** — the same isolation principle as `fresh-verifier`, but
with a different job: `fresh-verifier` hunts concrete *errors* and its verdict
gates promotion; this critic argues *against* the artifact and its output
gates nothing.

**A critique is never evidence.** It never writes a hypothesis `status`
(`update-confidence` is the only writer), never appends to `verifications:`,
never sets `needs_human_review` or `verification_reviewed`, and is marked
`citable: false`. It may *suggest* in its report that the researcher consider
one of those — the researcher decides.

## When to use

- The researcher asks for a critique / devil's advocate / "what would break
  this" on a specific artifact.
- Triggered from the Kairo interface (a `critique` job).

**Not** automatically, never as a gate, never inside another skill's pipeline.

## Steps

### 1. Resolve the target

One of: a note id (`H-XXXX`, `E-XXXX`, `C-XXXX`, `ADR-XXX`, `F-XXX`), a
vault-relative path to a note under `Projects/` (e.g. a manuscript), optionally
with one `## ` section, or a decision — the text of a notebook entry the
researcher points at — plus the project it belongs to.

### 2. Build the packet (mechanical)

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/critique/critique_note.py" packet \
  --vault <vault> --target <id|path> [--section "<heading>"] --out <tmp>/packet.md
# a decision:
python "${CLAUDE_PLUGIN_ROOT}/scripts/critique/critique_note.py" packet \
  --vault <vault> --target decision --text-file <tmp>/decision.txt --out <tmp>/packet.md
```

Exit 3 = refused (a `send: never` note, a note that does not exist, nothing left
after the allow-list): report the reason and stop. **Do not** assemble a packet
by hand, add context "to help", or paraphrase the artifact — the packet is
exactly what the script wrote.

### 3. Dispatch `devils-advocate` with the packet only

Launch the `devils-advocate` subagent with **the stored packet's path** (`packet`
in the JSON step 2 printed) as its whole prompt — never the packet's text, no
conversation, no summary of how the artifact came about, no earlier critiques,
no hint of what you think its weak points are. Its only tool is `Read`, held
by the vault hook to that file; the read leaves a receipt, and step 4 refuses
the result without one. Save its
reply verbatim to `<tmp>/result.md`.

### 4. File the note (mechanical)

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/critique/critique_note.py" write \
  --vault <vault> --target <id|path|decision> --packet <tmp>/packet.md \
  --result <tmp>/result.md --model <the subagent's model id> [--project <slug>]
```

The note records the second critic's availability by itself
(`second_critic: no disponible — …` while `KAIRO_SECOND_CRITIC` is not `on`).
That is never an error: don't try the DeepInfra call and don't mention it as a
failure.

Exit 3 = the reply was not a valid critique (no objections, an objection with
no location, a verdict or status in it). Re-dispatch **once** with the same
packet; if it fails again, report that and stop — never edit the reply into
shape.

Commit the new note alone:
`git add <path> && git commit --only -m "Crítica <CR-id> de <target>" -- <path>`.

### 5. Report

The CR id, then the objections by severity (`crítico` first, then `importante`, then `menor`), each with where it
points. Keep it to what the note says. If an objection looks serious, say which
existing step would address it (e.g. "Check 2 of hypothesis-cycle", "a
`## Enmiendas` entry is not possible after freeze — a new experiment is"), and
that the decision is the researcher's.

## Common mistakes

- **Letting the critic see the reasoning.** Génesis, Revisión del ciclo,
  earlier verifications and critiques are excluded on purpose: a critic that
  reads the defence argues with it instead of with the artifact.
- **Treating the critique as a verdict.** No status, flag or verification
  changes, ever — not even "obviously" warranted ones.
- **Critiquing a `send: never` note** by pasting its content. The script
  refuses it; so does this skill.
