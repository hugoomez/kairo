---
name: study
description: >-
  Use when the researcher wants the day's picture of a measurement study in a
  Kairo project and what to do next — "¿qué toca hoy?", "estado del estudio",
  "director del estudio", "¿cómo va la recolección?", "vigila la recolección",
  "parte del día". Runs scripts/ledger/study_brief.py (deadline countdown, each
  question Q-XXXX and pilot QP-XXXX with its state and gate summary, the app's
  collections, annotation progress and notice counts — counts only — and the
  top next steps from next_steps.py), presents it, and proposes the one next
  action mapped to the skill that does it (plan-study, estimate,
  create-project, literature-search) or to the researcher's own action in the
  app. Offers to watch an active collection with a background Monitor or
  /loop and to say when it stops or fails. Never reads data, logs or Avisos,
  and never applies a researcher-only transition.
---

# study — the study director: what is the state, and what comes next

> **Profile.** Before anything else run `python "${CLAUDE_PLUGIN_ROOT}/scripts/models/kairo_profile.py" check study`; exit 3 means the `literatura` profile is active (`KAIRO_PROFILE`): give the researcher its one-line message and stop.

## Overview

A measurement study moves through many hands: the plan is written with the
researcher (`kairo:plan-study`), the collection runs for days in the app
(Recolecciones), people annotate samples (Anotar), notices wait for an answer
(Avisos), and `kairo:estimate` takes a question to `estimada` only when its
design's gate passes. This skill puts all of that on one screen each day and
says which single step unblocks the most.

It **directs**: it reads the brief, proposes, and hands over. The work itself
is done by the skill it names, or by the researcher in the app.

The brief comes from one script and holds only what is safe to show:

| Part | Where it comes from | What it never shows |
|---|---|---|
| Plazo | hub `deadline` | — |
| Preguntas y pilotos | the notes' frontmatter; the gate (`study_status.gate`) only for a node one step before its publishable state | — |
| Recolecciones | `state.json` of the app's collections of this project (state, attempts, start, end, exit code) and the numbers of `progress.json` | `log.txt`, argv, the collected data |
| Anotación | lines of `muestra.jsonl`, distinct ids of each `etiquetas-*.jsonl`, lines of `referencia.jsonl`, count of `validacion-*.json` | labels, item contents, annotator names |
| Avisos | counts by state (`study_gates.count_avisos`, the gate's own reader) | who, how, what |
| Siguientes pasos | `next_steps.py` (study kinds first within each severity) | — |

## What you do — and what you never do

You may: run `study_brief.py` and `next_steps.py`, present the brief, propose
the next action, start the skill the researcher accepts, and watch a
collection with `study_brief.py --watch`. You **never**:

- open, list, `cat`, sample or grep a data file, a collection's `outDir`, its
  `log.txt` or `state.json`, an annotation file, or anything under
  `Projects/<slug>/Avisos/`. The brief already holds the counts; anything
  else is the researcher's to look at in the app. `data_guard` refuses the
  sensitive paths anyway;
- apply a researcher-only transition. Starting the collection
  (`plan_congelado → en_recoleccion`), `sin_respuesta` and `descartada` are
  the researcher's; `study_status.py` refuses them inside an agent session.
  Say what they do in the app, and stop there;
- decide a pilot's go/no-go, move a deadline, or resume a failed collection.
  You show the facts and their `## Decisión`; they decide;
- run the next skill without asking. Propose it; start it on their yes;
- edit a note to make an item disappear. An item goes away when the work it
  names is done.

## Steps

Run every command with the vault root as the working directory; `<vault>` is
that folder and `<project>` the slug or `PROJ-XXX` id.

### 1. The brief

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/ledger/study_brief.py" --vault <vault> --project <project> --write-bitacora
```

`--write-bitacora` adds one `study_brief` entry (counts only) to
`Projects/<slug>/Bitacora/acciones.jsonl` and re-renders that day's page. Use
it once a day, for the first brief; later briefs of the same day go without
it. Add `--json` when you need the fields (for instance a collection's id),
`--top N` for more next steps. Exit 2: unknown project or vault; say so and
stop.

With no `Preguntas/` or `Pilotos/` notes, the project has no study yet: say
so and propose `kairo:plan-study` (or `kairo:create-project` with the
`medicion` template if there is no project).

### 2. Present it

Show the brief as it came, in Spanish, on one screen. Lead with what changed
the plan of the day: a `crítico` item, the deadline when it is under three
weeks, a collection that stopped. Do not restate every line.

### 3. Propose the next action

Take the first item of «Siguientes pasos» (the list is already ordered by
severity, the study's own items first) and propose exactly one action:

| Item kind | Proposal |
|---|---|
| `dataset_registrado_antes_del_congelado` | Say plainly that this question can never pass its gate with that dataset. The researcher decides: a new question and plan (`kairo:plan-study`), with the data registered after the new freeze |
| `plazo` | Which publication-line questions are behind and the step that moves each; moving the deadline is the researcher's call |
| `pregunta_sin_plan`, `plan_sin_congelar` | `kairo:plan-study` on that question (fix the blocks first if the item names errors; freeze only on the researcher's go-ahead) |
| `plan_congelado_sin_recoleccion` | Researcher, in the app: Recolecciones → «Empezar la recolección». Remind them to register the units dataset only after the freeze |
| `recoleccion_fallida` | Researcher, in the app: read the (redacted) log in Recolecciones and decide whether to resume («Reanudar») |
| `recoleccion_activa` | Nothing to do; offer to watch it (step 4) |
| `en_recoleccion_sin_units_dataset` | When the collection ends: register the dataset, then `kairo:estimate` (it asks which dataset and writes `units_dataset`) |
| `clasificador_sin_validar` | Researcher, in the app: annotate the sample (Anotar) and validate the classifier (Anotar → Validar clasificador) |
| `gate_pendiente` | `kairo:estimate` for `estimates_reproducible` / `verifier_no_errors`; the researcher for classifiers and notices; the table of `kairo:estimate` step 6 for `plan_frozen` lines |
| `avisos_sin_cerrar` | Researcher, in the Avisos panel: send, wait for the embargo, or close. Report only the counts |
| `piloto_sin_plan`, `piloto_sin_decision` | `kairo:plan-study` («Un piloto antes del estudio»); the decision is written before any result |
| `piloto_sin_estimar`, `piloto_por_cerrar` | `kairo:estimate` on the pilot |
| `piloto_explorado_sin_estudio` | Quote the pilot plan's `## Decisión`; if the researcher goes on, `kairo:plan-study` for the study question |
| `hub_sin_valor`, `frontera_obsoleta`, `hueco_sin_hipotesis`, hypothesis kinds | The action the item names (often `kairo:literature-search`, `kairo:frontier-map` or `kairo:hypothesis-cycle`) |

Phrase it as a question: «¿Lanzo `kairo:estimate` sobre Q-0003?» or «Esto lo
haces tú en la app: …». Several independent researcher actions may go in one
short list.

### 4. Watch an active collection (offer, never force)

When the brief shows a collection `en_marcha`, offer once: «¿Quieres que
vigile la recolección <id> y te avise cuando pare o falle?». On yes, pick by
how long it will run:

- **Hours** — a Monitor (each line is a notification; it stops on its own):

  ```
  Monitor(command: python "${CLAUDE_PLUGIN_ROOT}/scripts/ledger/study_brief.py" --vault <vault> --project <project> --watch <rec-id> --interval 600,
          description: "recolección <rec-id>", timeout_ms: 1800000)
  ```

  It prints a line only when the state, attempts or progress numbers change,
  and a last line `FIN …` when the collection is no longer `en_marcha`
  (`terminada`, `fallida`, `interrumpida`, `detenida`); exit 3 if the id is
  not this project's. A Monitor expires after 30 minutes: re-arm it on each
  expiry while the session lasts. For a single «tell me when it stops», run
  the same command with Bash `run_in_background` instead.
- **Days** — `/loop` with a long interval, for example
  `/loop 1h python "${CLAUDE_PLUGIN_ROOT}/scripts/ledger/study_brief.py" --vault <vault> --project <project> --watch <rec-id> --max-polls 1`,
  and report only when the line differs from the previous one or starts with
  `FIN`.

On `FIN` with `fallida` or `interrumpida`, tell the researcher at once (a push
notification if available) and propose the `recoleccion_fallida` action. On
`terminada`, propose the next step for the questions waiting on it (register
the dataset, then `kairo:estimate`). Never open the log or the `outDir` to see
why; the watch line is all you know.

### 5. Close

After the researcher's answer, start the accepted skill and follow it, or end
with the list of their actions in the app. Do not run `study_brief.py` again
just to show that nothing changed.

## Common mistakes

- **Reading a collection's log, `state.json` or `outDir`** "to see why it
  failed". The brief and the watch line are the only channel; the reason is
  in the app, for the researcher.
- **Counting labels by opening the annotation files.** The brief has the
  counts; the labels themselves are never yours to see.
- **Opening `Avisos/`** to say which notice is open. Counts by state only.
- **Starting the collection, or marking `sin_respuesta`,** because the brief
  says it is next. Those edges are the researcher's; say what to click.
- **Proposing three skills at once.** One next action; the rest stays in the
  list.
- **Writing the bitácora entry on every brief.** Once a day is the record;
  more is noise.
- **A watch whose filter misses failure.** `--watch` already prints every
  terminal state with `FIN`; do not grep its output down to `terminada`.
