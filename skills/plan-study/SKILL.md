---
name: plan-study
description: >-
  Use when a Kairo project needs a study question that is not a hypothesis — a
  measurement (how often something happens in a population, a proportion, a
  count, a median) — opened, planned and frozen: "abre una pregunta de
  medición", "plan de medición para…", "¿qué proporción de…?", "quiero medir…",
  "congela el plan de M-0002". Creates the question Q-XXXX from the template
  with its design (default `medicion`), drafts the measurement plan M-XXXX with
  the researcher, asking for whatever is unclear instead of guessing, offers a
  devil's-advocate critique of the plan, and freezes it (frozen_at,
  frozen_commit, `plan_congelado`) with scripts/ledger/freeze_plan.py. Never
  reads data, never registers the units dataset, never estimates.
---

# plan-study — a study question and its frozen plan

> **Profile.** Before anything else run `python "${CLAUDE_PLUGIN_ROOT}/scripts/models/kairo_profile.py" check plan-study`; exit 3 means the `literatura` profile is active (`KAIRO_PROFILE`): give the researcher its one-line message and stop.

## Overview

A **question** (`Projects/<slug>/Preguntas/Q-XXXX.md`) is a study unit that
estimates quantities instead of testing a prediction. Its **design** (a table
of `config/designs.toml`, default `medicion`) fixes its states, its
transitions and the gate it must pass before `estimada`. Its **plan**
(`Projects/<slug>/Mediciones/M-XXXX.md`) says, before any data is looked at,
what population is measured, what each estimand is and how its interval is
computed, which classifiers label the units and how good they must be.

Binding rule — the same as a preregistration: **the plan is frozen before the
data exist in the vault.** The gate of `estimada` checks it in git
(`study_gates.plan_frozen`): the plan at `frozen_commit` must have the same
Estimandos, Clasificadores and Intervalos as the current file, and the units
dataset must be registered in `Datos/registro.json` in a commit that comes
**after** `frozen_commit`. A measurement gate laxer than the hypothesis gate is
a failure even if everything runs.

This skill writes two notes and freezes one of them. It runs nothing else.

## What you do — and what you never do

You may: create the question, draft the plan, ask the researcher, run the
critique if they want it, freeze when they say so. You **never**:

- fill a gap with a guess. Anything the researcher has not said — a column
  name, a filter, a floor, the sampling frame — is a question to them, not a
  default you pick;
- open, list or sample a data file to "see the columns". Column names come
  from the researcher (or from their collection script's documentation),
  never from the data. A sensitive dataset is unreadable anyway
  (`scripts/security/data_guard.py`);
- register the units dataset, or tell the researcher to register it before
  the freeze;
- write `status` by hand. `study_status.py` is its only writer; the freeze
  uses it. Discarding a question (`descartada`) is the researcher's decision
  and `study_status.py discard` refuses inside an agent session;
- touch `Projects/<slug>/Avisos/` (the notices registry; `data_guard` refuses
  it). Only the freeze script creates it, empty;
- freeze without the researcher's explicit go-ahead.

## Steps

Run every command below with the vault root as the working directory (cwd);
`<vault>` is that same folder.

### 1. The design

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/ledger/designs.py" show
```

Use the design the researcher names; default `medicion`. Read from it
`prefix` (`Q`), `folder` (`Preguntas`), `plan_prefix` (`M`), `plan_folder`
(`Mediciones`) and `plan_template`. An unknown design: say which exist and
stop. `freeze_plan.py` freezes designs with the `abierta → plan_congelado`
transition (today, `medicion`).

### 2. The question

Next free id, vault-wide: the highest `Q-NNNN` under
`Projects/*/<folder>/` plus one (four digits). Copy
`${CLAUDE_PLUGIN_ROOT}/templates/question-template.md` to
`Projects/<slug>/<folder>/Q-XXXX.md` and fill:

- `id`, `project` (the hub's `id`), `design`, `status: abierta` (as the
  template has it; never again by hand);
- `linea_publicacion`: ask whether this question feeds the manuscript;
- `linked_plan` and `units_dataset`: leave empty (the freeze sets
  `linked_plan`; `kairo:estimate` sets `units_dataset`);
- `## Pregunta` (one sentence answerable with an estimate), `## Población y
  marco`, `## Estimandos`, in the researcher's words. The title after the id
  is short.

Remove the template's `<…>` placeholder lines you filled; leave `## Resultado`
as it is.

### 3. The plan, with the researcher

Next free id: the highest `M-NNNN` under `Projects/*/<plan_folder>/` plus
one. Copy `${CLAUDE_PLUGIN_ROOT}/<plan_template>` to
`Projects/<slug>/<plan_folder>/M-XXXX.md`: `id`, `project`, `question: Q-XXXX`,
`status: borrador`. **Leave `frozen_at` and `frozen_commit` as the template's
placeholders** — the freeze writes them.

Ask about everything the researcher has not made explicit. Ask in one batch
when you can, then follow up. Typical questions:

- **Population**: what, exactly, the estimate is about. Which period, which
  versions, which units are in scope?
- **Sampling frame**: where the units come from. Is it the whole frame or a
  sample? If a sample, how is it drawn, and with which seed?
- **The unit**: what counts as one unit, and how duplicates are handled.
- **Inclusion and exclusion** criteria, written now, before any data.
- **Each estimand**:
  - its `type` (`proporcion`, `conteo` or `mediana`);
  - the `column` it reads, and its `filter` (`{columna: valor}`, or `{}`);
  - whether units are grouped (several per site, author or repository). If
    so, `cluster` names the grouping column, and the interval becomes a
    bootstrap by clusters. Otherwise `cluster: null`.
- **Classifiers** that label units:
  - the `name` of each, and its validation `sample`
    (`Datos/anotacion/<sample>/`);
  - the `label` (the annotation field it is validated on) and the `positive`
    value that counts as positive; optionally `ci` (default 0.95). The
    study gate refuses a validation made with any other label, positive or ci;
  - the floors `min_precision_lo` / `min_recall_lo`: the lowest acceptable
    lower bound of each interval, and why that value.

  An empty list if there are none.
- **Intervals**: the level (`ci`), the `bootstrap` resamples, and a fixed
  `seed`.
- **Ethics**: what personal or third-party material the data may hold, the
  consent involved, and the risks. Ask whether the units dataset will be
  registered `sensitive: true`. Note that the dataset must then be redacted at
  the source, by the collection script, with `scripts/security/redact.py`.

Write the answers into the plan's sections and JSON blocks, exactly in the
template's shape. Then check the blocks:

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/ledger/plan_blocks.py" show Projects/<slug>/<plan_folder>/M-XXXX.md
```

Exit 3 prints what is wrong. Fix it, and ask the researcher if the fix is not
purely syntactic. Anything still open stays an open question. Never freeze a
plan with a gap the researcher has not closed.

Show the researcher the finished plan, section by section.

### 4. Offer the critique

Offer once: «¿Quieres que un abogado del diablo critique el plan antes de
congelarlo?». It is never forced and never a gate. If they accept, follow the
`kairo:critique` skill with target `M-XXXX`. Its packet carries the plan (with
no frontmatter) plus the question's `## Pregunta` and `## Población y marco`.
Show the objections, most serious first. The researcher decides what to change.
Changes go into the plan **before** the freeze. Run `plan_blocks.py show`
again after any change.

### 5. Freeze — only on the researcher's explicit go-ahead

Say what freezing means, then run:

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/ledger/freeze_plan.py" \
  --vault <vault> --question Q-XXXX --by <agent id>
```

It does, in order:

1. Commits the plan and the question as they are. It commits only those two
   paths: nothing else you or the researcher left uncommitted goes in. If
   `linked_plan` was empty, it is set first.
2. Writes `frozen_at` (UTC) and `frozen_commit` (that commit's sha), and sets
   `status: congelado` in the plan.
3. Creates `Projects/<slug>/Avisos/registro.json` as `[]` if it does not
   exist. It never opens an existing one, and never commits one.
4. Runs `study_status.py apply --to plan_congelado` on the question.
5. Commits the plan and the question again. If step 3 created the registry
   in this same run, the commit includes it.

Exit 3 means refused, and nothing was written. The reasons: the question is
not `abierta`, there is no plan or there are several, the plan names another
question, the plan is already frozen, the blocks are invalid, or the vault has
no git. Report the reason and its fix.

Exit 1 is a git error, for example a vault pre-commit hook that refused. Show
git's message. Never skip the hook. Fix the content it names, then run the
same command again. If the second commit failed, the plan already has
`frozen_at` and `frozen_commit`. The rerun finishes that freeze with the same
`frozen_commit` (`reanudado: true`) and does not freeze again. Never edit the
frozen fields by hand.

When `avisos_creado` is true, tell the researcher, verbatim: «sin avisos
registrados todavía; añade los que correspondan antes de estimar». The gate of
`estimada` needs every notice closed (`avisos_cerrados`). They manage notices
in the interface's Avisos panel. You never read or write that registry.

### 6. Report

Report:

- the question and plan ids, `frozen_commit` and `frozen_at`;
- the state (`plan_congelado`);
- what comes next, and why the order matters:

  - **Register the units dataset only now, after the freeze.** The gate checks
    in git that the first commit holding the dataset in `Datos/registro.json`
    comes after `frozen_commit`. It matches the dataset by name or by the
    file's sha256, so renaming does not help. A dataset registered earlier,
    or committed in the freeze commit, fails «los datos se registraron antes
    de congelar el plan» forever. That is the mechanical proof that the plan was fixed before
    the data could be seen.
  - Start the collection: Recolecciones → «Empezar la recolección». That
    moves the question to `en_recoleccion`, and only the researcher does it,
    because `study_status.py` refuses that edge inside an agent session.
  - Register the dataset (`kind: file`, `sensitive: true` if it holds
    third-party material), and commit `Datos/registro.json`.
  - The data guard acts only when the hook finds the vault. En las sesiones
    de Claude Code abiertas en el repositorio de código (fuera del vault),
    define KAIRO_VAULT con la ruta del vault para que el guardia de datos
    actúe. A git-tracked copy of a sensitive dataset inside the code
    repository is not covered: keep the data out of that repository.
  - Annotate and validate the classifiers
    (`Datos/anotacion/<sample>/`, `agreement.py validate`).
  - Then run `kairo:estimate`.

The plan is now immutable. A change of mind means a new plan for a new
question, never an edit: the gate compares the plan with its frozen version.

## Common mistakes

- **Guessing a column, filter or floor** so the plan "parses". The plan
  then measures something the researcher never chose. Ask.
- **Looking at the data to write the plan.** That breaks exactly what the
  freeze exists to prove. Column names come from the researcher.
- **Registering, or committing, the units dataset before the freeze**, or
  together with it. The gate fails, and the plan cannot be re-frozen.
- **Editing the plan after the freeze** ("only a typo in a filter"). It
  voids the gate.
- **Committing with `git add -A`** around the freeze. `freeze_plan.py`
  commits only the plan and the question, on purpose.
- **Creating `Avisos/registro.json` by hand.** The guard refuses it, and
  only the freeze script creates it.
