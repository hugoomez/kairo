---
name: ask-corpus
description: Use when the researcher asks a question to be answered from a Kairo project's own papers ("pregunta al corpus", "¿qué dicen mis papers sobre X?", "ask the corpus"). Answers only with verbatim quotes plus locators from the project's Papers/ notes; scripts/papers/check_quotes.py then verifies every quote character for character against the cited section and removes any claim whose quote is not the paper's own text. "No está en el corpus" is a valid answer. The saved answer is model-written and non-citable.
---

# ask-corpus — answers made only of the papers' own words

## Overview

The researcher wants to know what the project's papers say. The answer is
built from verbatim quotes. Each claim you make sits on top of one or more
quotes, and each quote names its paper and locator. A script then checks every
quote against the paper's `## Texto completo` (or `## Resumen`) with the
fresh-verifier's own locator resolver. **Anything that fails is removed
before the researcher sees it**, and the removal is reported.

The saved answer is `escrito_por: modelo`, `citable: false`. It is a reading
aid. No skill may use it as evidence: to cite a paper, cite the paper.

## When to use

- The researcher asks a question about the literature of one project.
- Triggered from the Kairo interface (an `ask_corpus` job), which passes the
  project, the question and the output path.

## Draft-only mode (the Kairo interface)

When the Kairo interface runs this skill, the session is read-only: no file
writes and no commands. That lets it answer while a long job is using the vault.

- Step 1 is already done: the prompt lists the papers you may quote.
- Do steps 2 and 3 with Smart Connections, Read and Grep.
- Return the step-3 draft between a line `<<<BORRADOR` and a line
  `BORRADOR>>>`.
- Kairo then runs step 4 itself (`check_quotes.py`, the note, the commit).
- Skip step 5's commit; still end with a one-line report.

## Steps

### 1. The papers you may quote (mechanical)

```
python <plugin>/scripts/papers/check_quotes.py --vault <vault> --list --project <PROJ-XXX>
```

These are the only sources. The list already leaves out:
- notes marked `send: never`;
- the model-written reading notes under Papers/;
- papers not listed for the project.

Never quote anything else: not hypotheses, not `Estado-del-arte.md`, not the
digest, not your memory of a paper.

### 2. Retrieve

- Search with Smart Connections (`search_by_text`) using the question and one
  or two rephrasings. Keep only hits whose path is in the list from step 1.
- If Smart Connections is unavailable, Grep the listed papers' files for the
  question's key terms, and say so.
- Read the matching sections of the candidate papers in full. A quote must
  come from text you actually read in this session.

### 3. Draft the answer

Write the draft to `<output folder>/_borrador-respuesta.md` in this exact
format:

```
<one claim, in your words, in one short paragraph>

> <the paper's exact words, copied character for character>
> — P-XXXX §3.2

<next claim>

> <exact words>
> — P-YYYY Tabla 2
```

- **Every claim needs at least one quote block right after it.** A claim with
  no quote is removed.
- The locator must point at the section that contains the quote. Examples:
  `§3.2`, `Tabla 2`, `Fig 3`, `App. B`, `§Resumen` for the abstract.
- Copy the quote exactly. Do not fix typos or translate. Use `[…]` for an
  omission; every fragment must still appear, in order, in the same section.
  A quote needs at least four words.
- Claims say only what the quotes say. When papers disagree, show both with
  their quotes, and do not pick a winner.
- When the listed papers do not answer the question, the whole draft is:
  ```
  **No está en el corpus.**

  Buscado: <queries you ran>
  ```
  That is a good answer. Do not stretch a loosely related quote to fit.

### 4. Check (mechanical) and save

```
python <plugin>/scripts/papers/check_quotes.py --vault <vault> --project <PROJ-XXX> \
  --answer "<output folder>/_borrador-respuesta.md" --question "<the question>" --out "<output path>"
```

- The script writes the checked answer to the output path. It keeps only
  claims whose quotes all pass, and lists what it removed and why.
- Delete the draft. Commit only the output note:
  `Respuesta del corpus: <first words of the question>`.
- Do not retry to "rescue" removed claims. The researcher can see them and
  ask again.

### 5. Report

Report in a few lines:
- the answer's status (`answered`, `not_found` or `nothing_verified`);
- quotes checked versus passed;
- one line per removed claim with its reason.

Never restate the removed claims as if they were true.

## Rules

- Quote only from the papers listed in step 1. Never quote or read
  `send: never` notes or the model-written reading notes.
- Never write to a paper note, a hypothesis, or any status.
- Paraphrase lives only in the claim lines, and the quote under each one is
  what the researcher checks it against.
