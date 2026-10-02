---
name: lit-watch
description: Use for the periodic literature watch of one Kairo project ("vigila la literatura", "¿hay papers nuevos?", "lit watch"), or to ingest the papers the researcher picked from a watch. Re-runs the project's own recorded queries restricted to papers since the last watch (scripts/watch/lit_watch.py), writes a one-line triage reason for each strong candidate, and checks whether a new paper threatens the novelty of an existing hypothesis. A threat is a model's judgement recorded in the hypothesis's Revisión de vigencia with a verbatim sentence of the abstract. It never changes a status, and it ingests nothing unless the researcher chose the paper or the hub says paper_ingestion: auto.
---

# lit-watch — what is new, and does it matter to a hypothesis

## Overview

A project's `Estado-del-arte.md` records every query its literature search
ran, verbatim. This skill re-runs those queries each week, restricted to
papers since the last watch. It surfaces only strong candidates, meaning
papers found by the queries of two or more facets, each with one line of why.
It also flags papers that may say what a hypothesis says.

**A novelty threat is not evidence.** It is your judgement that a new paper
may already report the hypothesis's claim.
- It never moves `status`, `confidence`, `verifications` or any flag.
- It is recorded with the abstract's own words: the script refuses a
  sentence that is not verbatim in the abstract.
- It waits for the researcher's decision.
- Do not summarise the paper's claim in your own words as if it were the
  paper's.

## Modes

- **watch** (default): steps 1–4.
- **ingest `<keys>`**: step 5 only, for the candidates the researcher marked
  `ingerir`, or for every triaged candidate when the hub has
  `autonomy_defaults.paper_ingestion: auto`.

## Steps

### 1. Delta (mechanical)

```
python <plugin>/scripts/watch/lit_watch.py delta --vault <vault> --project-dir <vault>/Projects/<slug>
```

The JSON gives the run file (`_vigilancia/vigilancia-<date>.json`), the window
(`since` → `until`), how many queries were lost, and the counts.

- `lost_all: true`: nothing was written and `last_watch` did not move. Report
  the failure (network, 429, Semantic Scholar key) and stop.
- Some queries lost: continue, and say which ones in the report. `last_watch`
  did not move (`last_watch_moved: false`): the next watch covers this window
  again, so say that the window stays open — never call it "nothing new".
- Refused because there are no recorded queries: the project has no
  "Búsqueda ejecutada" block. Report that literature-search must run first,
  then stop.

### 2. Triage — one line per surfaced candidate

Read the run file. For each candidate with `triage: true`, read its title
and abstract. Write **one line** saying which facet it bears on and what it
reports. Use the abstract's content, not a guess from the title.

```
python <plugin>/scripts/watch/lit_watch.py triage --project-dir <dir> --run <run file> --key <key> --why "<one line>"
```

If a triaged candidate is plainly off-topic despite matching two facets, say
so in its line ("fuera de tema: …"). Do not drop it: the researcher decides.

### 3. Novelty threats

Look at each candidate whose `novelty` list names hypotheses. The list is a
word-overlap prefilter and not a verdict. Take at most the 3 highest scores
per hypothesis.

The judgement is not yours: it runs on the policy's hard-task model
(`config/models.toml`, task `novelty_judge`).

1. For each (hypothesis, candidate) pair, dispatch one `novelty-judge`
   subagent, all in the same turn. Give it the hypothesis's `## Claim` and
   nothing else of it (not its justification, Génesis or reviews), and the
   candidate's title and abstract exactly as in the run file.
2. It answers `threat: true|false`, the exact abstract sentence, a severity
   (`crítico` / `importante` / `menor`) and a one-line judgement. A candidate
   with no abstract cannot carry a threat; say so in its triage line instead.
3. Record only its `threat: true` answers, with its sentence, severity and
   judgement, and `--model` set to the judge's model id:

```
python <plugin>/scripts/watch/lit_watch.py threat --vault <vault> --project-dir <dir> --run <run file> \
  --key <key> --hypothesis H-XXXX --sentence "<exact words from the abstract>" \
  --severity crítico|importante|menor \
  --judgement "<one line: why this may take the novelty, and what would tell>" --model <your model id>
```

If the script refuses the sentence because it is not verbatim, copy it again
from the abstract. Never paraphrase it to make it pass.

### 4. Commit and report

First check the run is complete:

```
python <plugin>/scripts/watch/lit_watch.py check --project-dir <dir> --run <run file>
```

Exit 3 lists what is missing: a triaged candidate without its line, or a
threat without a severity or with a sentence that is not in the abstract. Fix
each one with `triage` / `threat`, then check again. Do not commit an
incomplete run.

- Commit the run file, `_hub.md` (`last_watch`) and any hypothesis that got a
  Revisión de vigencia line, together:
  `Vigilancia de literatura <PROJ>: <n> nuevos, <m> alertas`.
- Report in a few lines:
  - the window;
  - candidates, strong and triaged;
  - each threat, as severity + hypothesis + paper + the quoted sentence;
  - the lost queries.
- End with: "Las alertas son juicios de un modelo; decide tú en la bandeja."

### 5. Ingest (mode ingest only)

For each chosen candidate, follow **create-project step 6 ("Ingest each
confirmed paper — via Zotero")** exactly:
- the paper note format;
- `projects:`, with the project id appended if the note exists;
- the facet entries (`facet_assignment.py --add`, with the run file's
  `facets` and a matched term you can point to in the title or abstract);
- verbatim full text;
- retraction and resolution checks.

Then record the decision if the researcher has not:
`lit_watch.py decide --decision ingerir`. Commit the new paper notes.
`Estado-del-arte.md` is not regenerated here: its staleness hook will say
when it is due.

## Rules

- Never change a hypothesis's `status`, `confidence` or frontmatter. The only
  write to a hypothesis is the script's Revisión de vigencia line.
- Never ingest a paper in watch mode.
- Never read `send: never` notes, the model-written reading notes under
  Papers/, or a hypothesis's reasoning sections.
- Quote abstracts only verbatim. Your own words go only in the triage line
  and the one-line judgement, and both are labelled as yours.
