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
  - its `type`: `proporcion`, `conteo`, `mediana`, `diferencia` (p(a) − p(b)
    between two values of a `group` column), `reutilizacion` (share of
    distinct keys — a fingerprint column, never the key — seen in at least
    `min_groups` distinct values of `across`, such as repository or
    organisation), or `supervivencia` (Kaplan-Meier median of a time column,
    with an `event` column that is false when the unit was still unchanged at
    the end of observation, and `at`, the times at which to report S(t)).
    The template documents every field;
  - the `column` it reads, and its `filter` (`{columna: valor}`, or `{}`);
  - whether units are grouped (several per site, author or repository). If
    so, `cluster` names the grouping column, and the interval becomes a
    bootstrap by clusters (`proporcion` and `diferencia`). Otherwise
    `cluster: null`;
  - whether it is broken down by a category (`by`: tool, year, owner type;
    at most 50 values; never an identifier column), for comparisons between
    groups or a trend over periods.
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

  **The project's ethics plan.** A `medicion` project has
  `Projects/<slug>/Etica.md` (hub field `etica:`, from
  `templates/ethics-plan-template.md`): affected parties, Menlo principles,
  what is collected and what never, sensitive material, terms of use, ethics
  committee, disclosure, retention and publication. Read it before drafting
  `## Ética`, and write in the plan only what this question or pilot adds or
  changes. If `Etica.md` still has placeholders, or `comite` is
  `pendiente`/`solicitado`, say so to the researcher before the freeze; never
  fill it or change its `status`/`comite` yourself. If the hub has
  `sota_map: diferido`, `Novedad.md` is the project's only literature check:
  cite it where the question's motivation needs prior measurements, and
  propose a pilot first.

  **The environment.** Before the researcher registers a sensitive dataset,
  run `python "${CLAUDE_PLUGIN_ROOT}/scripts/study/doctor.py" --vault <vault>
  --project <slug>` and show any `critico` item (dataset inside a git tree or
  a synced folder, `GITHUB_TOKEN`/`GH_TOKEN` in the environment, …) with its
  fix. Exit 4 means at least one `critico`.

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

You may help write the wording of the notices: a template in
`Projects/<slug>/Plantillas-avisos/<name>.md` (outside `Avisos/`), with
placeholders and no real data — start it with `python
"${CLAUDE_PLUGIN_ROOT}/scripts/study/notices.py" template --vault <vault>
--project <slug> --name <name> [--kind vendor] --new` and run the same command
without `--new` after every edit until it exits 0. Rendering the drafts
(`notices.py draft` / `vendor`) is the researcher's command, run in their own
terminal: it reads the findings and writes `Avisos/`, and refuses inside an
agent session. Nothing is ever sent by a script.

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

## A pilot before the study

A study often starts with a pilot: counts, a few hundred units labelled by
hand, a go/no-go decision. The pilot looks at data **before** the study plan
exists, so it cannot be the study question. Open it as its own node with the
design `piloto`:

- the pilot `Projects/<slug>/Pilotos/QP-XXXX.md` (`question-template.md`,
  `design: piloto`) and its plan `Projects/<slug>/Pilotos/MP-XXXX.md`
  (`templates/pilot-plan-template.md`). Set `linked_plan` by hand: a pilot
  plan is never frozen (`freeze_plan.py` refuses it) and may be corrected in
  view of the pilot's data;
- write `## Decisión` in the pilot plan before its results: what it decides
  and by which criterion;
- its dataset may be registered at any time. It must never become the
  study's units: the study gate rejects a dataset (by name or by sha256)
  registered before the study plan's freeze, and the pilot's always is;
- `kairo:estimate` takes it to `explorada` (the only gate is
  `estimates_reproducible`). It is **not citable**: `manuscript.py` refuses
  a pilot as a dependency and has no sections for its design. The paper's
  methodology may describe the pilot; no number of the paper rests on it;
- size the study's annotation samples from the pilot's rates:
  `python "${CLAUDE_PLUGIN_ROOT}/scripts/analysis/estimate.py" tamano --p <rate> --half-width <precision>`.
- The pilot does not appear yet in the interface's «Preguntas» view (it
  lists `Q-` notes only).

Draw each annotation sample from a registered dataset, by its `name`, with
`python "${CLAUDE_PLUGIN_ROOT}/scripts/security/draw_sample.py" --vault <vault> --project <slug> --dataset <name> --n <N> --seed <S> --out <absolute path outside the vault>.jsonl`
(add `--stratify-by <column> --per-stratum <K>` for a stratified sample;
`--filter COL=VAL` and `--glob` narrow the frame). The draw is reproducible
(same arguments and dataset sha256, same bytes), refused when the dataset
changed since it was registered, and redacted (items that cannot be cleaned
are dropped and counted); stratified items carry `_stratum` and `_weight`
to weight precision and recall back to the frame. Repository URLs stay
readable because the annotator must open them. The interface imports the
file into `Datos/anotacion/`; the command prints counts only.

Then open the study question (`Q-XXXX`, `medicion`) and its plan as above,
naming the pilot in `## Población y marco muestral` of the plan.

## Parsers on sensitive data

Never read a sensitive dataset to write or debug a parser. Take a debug
sample instead:

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/security/debug_sample.py" --vault <vault> --project <slug> \
  --dataset <name> --n 50 --seed 1 [--glob "*.yaml"]
```

The dataset is named by its `name` in `Datos/registro.json`, never by its
path, so the data guard lets the command run (a `cat` or `Read` of the path
stays blocked). The sample lands in `Projects/<slug>/Datos/depuracion/<name>/`
under strict redaction: `.env` values, `data:` / `stringData:` of a
Kubernetes `Secret`, private keys (base64-encoded too), every high-entropy
string, e-mail addresses and `github.com/<owner>/<repo>` (pseudonymised).
It keeps what a parser needs to read: SOPS `ENC[...]`, age and PGP encrypted
blocks, certificates and public keys, `age1…` recipients, Sealed Secrets
`encryptedData:`, dotenvx `encrypted:` values and the header of binary age
files. Any item with something left after redaction is dropped whole; the
command and `manifest.json` print counts only. `--replace` with the same seed
rebuilds the same sample. Limits: redaction is by pattern, so a short
password in a free-text field of an unknown format could survive. The sample
is for understanding formats: never cite or publish it, never register it as
a dataset, and it does not replace annotation.

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
