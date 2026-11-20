---
name: estimate
description: >-
  Use when a Kairo study question (Q-XXXX) with a frozen plan has its data and
  must be estimated and taken to `estimada`: "estima Q-0003", "calcula las
  estimaciones", "corre estimate.py", "¿pasa la puerta de estimada?". Runs
  scripts/analysis/estimate.py on the registered units dataset, writes
  `## Resultado` (a readable table plus estimate.py's JSON verbatim in an
  `estimate-json` block), has the isolated fresh-verifier check the question
  packet, records its verdict, and moves the question with study_status.py only
  when the design's gate passes; otherwise lists what is missing and how to fix
  it. Never reads the units file.
---

# estimate — from a frozen plan to `estimada`

> **Profile.** Before anything else run `python "${CLAUDE_PLUGIN_ROOT}/scripts/models/kairo_profile.py" check estimate`; exit 3 means the `literatura` profile is active (`KAIRO_PROFILE`): give the researcher its one-line message and stop.

## Overview

`estimate.py` computes every estimand of the frozen plan, with its interval,
from the units dataset. It checks the dataset's fingerprint against the
registry and prints only aggregates. This skill stores that output, has it
verified independently, and asks `study_status.py` whether the question may
become `estimada`. The design's gate (`config/designs.toml`) decides, never
you. For `medicion` the gate checks:

| Check | What it needs |
|---|---|
| `plan_frozen` | the plan's frozen blocks are unchanged since `frozen_commit`; every amendment in `## Enmiendas` is unchanged since the commit that recorded it and none was removed; the units dataset was registered in a later commit |
| `estimates_reproducible` | re-running `estimate.py` gives exactly the `estimate-json` block of `## Resultado` |
| `classifiers_validated` | each classifier of the effective plan has a `Datos/anotacion/<sample>/validacion-<name>.json` that reproduces and reaches the plan's floors |
| `verifier_no_errors` | the latest fresh verification is `no_errors_found`, on the packet `verifier_packet.py --question` builds now |
| `avisos_cerrados` | every notice in `Avisos/registro.json` is closed or past its embargo |
| `avisos_enviados` (instead of `avisos_cerrados` when the plan's `## Intervalos` says `"avisos": "enviados"`) | every notice is sent; a running embargo is fine, `pendiente` or a sent notice without embargo date is not |

`estimate.py` reads the plan's **effective** blocks: the frozen ones with the
amendments of `## Enmiendas` applied in order. An estimate an amendment changed
or added carries `"desviacion": [{"enmienda", "datos_vistos"}]`, and the
output has `enmiendas`; `study_status.py gate` lists the amendments too. Those
numbers are **declared deviations**, not confirmatory results: say so in the
`## Resultado` table and in the report.

**An exploratory question** (`QE-XXXX`, design `exploratoria`, in
`Projects/<slug>/Exploratorias/`, plan `ME-XXXX` never frozen) is estimated
from `abierta`, like a pilot, but it is citable: its gate is
`estimates_reproducible`, `classifiers_validated`, `verifier_no_errors` and
the notices check its plan chooses. Do steps 2–6 as below and apply `--to
estimada`. Its numbers are always **exploratory**: the gate output carries
`"etiqueta": "exploratorio"`; write «exploratorio» in the `## Resultado`
reading and in the report.

## What you do — and what you never do

You may: set `units_dataset`, run the scripts below, write `## Resultado`,
dispatch the verifier, record its verdict, apply a transition the scripts
allow. You **never**:

- **open, list, search or sample the units file**, or anything under a
  dataset's `path`, sensitive or not. The only thing you know about the data
  is `estimate.py`'s JSON. For a sensitive dataset, `data_guard` refuses the
  call anyway; for any other dataset the rule is the same;
- read `Projects/<slug>/Avisos/`. The gate reports only counts by state;
- type, round or "tidy" a number in the `estimate-json` block. It holds
  `estimate.py`'s stdout exactly;
- argue a verifier finding away, or apply `estimada` with the gate failing.
  `apply` refuses it anyway;
- apply `sin_respuesta` or `descartada` on your own. Those are the
  researcher's calls: el investigador decide sin_respuesta, and
  `study_status.py` refuses that edge inside an agent session.

## Steps

Run every command below with the vault root as the working directory (cwd);
`<vault>` is that same folder.

### 1. The question

Find `Projects/*/Preguntas/Q-XXXX.md` (the folder is the design's `folder`):

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/ledger/study_status.py" propose --vault <vault> --question Q-XXXX
```

`from` must be `en_recoleccion`; `estimada` is reached only from there.

**A pilot** (`QP-XXXX`, design `piloto`, in `Projects/<slug>/Pilotos/`) is
estimated from `abierta`, with its own unfrozen plan `MP-XXXX`. Do steps 2–4
as below, skip step 5 (its gate has no verifier), then run `gate` and apply
`--to explorada` with `--evidence "piloto: estimaciones reproducibles"`. In
the report, say that the pilot's numbers are exploratory and not citable,
and quote its plan's `## Decisión` so the researcher decides; never decide
the go/no-go yourself.

- `plan_congelado`: the collection has not been started. Starting it is the
  researcher's action, never yours. `study_status.py apply` refuses that edge
  inside an agent session. Tell them to start it in the interface
  (Recolecciones → «Empezar la recolección»), and stop.
- `abierta`: the plan is not frozen. Say so, point to `kairo:plan-study`, and
  stop — unless it is an exploratory question (QE), estimated from `abierta`.
- A terminal state: stop.

### 2. `units_dataset`

If the question's frontmatter has no `units_dataset`, ask the researcher which
registered dataset holds the units. You may show them the `name`, `kind`,
`sensitive` and `registered_at` of each entry in
`Projects/<slug>/Datos/registro.json`; that file is the registry, not the
data. The dataset must be `kind: file` (estimate.py refuses a folder). Write
its `name` into `units_dataset`. Never infer the dataset from file names in
the data folder.

### 3. Run `estimate.py`

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/analysis/estimate.py" --vault <vault> --plan <linked_plan> --units <units_dataset>
```

Exit 3 prints `{"error": …}`, sometimes with `esperada`, the fingerprint the
registry expected. Report it and stop. A changed fingerprint means the units
file changed after it was registered. The researcher decides what to do. Never retry with another file
or another dataset on your own.

### 4. `## Resultado`

Replace the contents of `## Resultado` (the template placeholder, or an
earlier result) with:

1. a table for reading: `estimando | tipo | valor | intervalo | n | método`.
   Write one row per entry of `estimates`, in the same order. Copy the values
   as printed. A `proporcion` may also show as a percentage in a second
   column, but only if you also keep the printed value. An estimand with
   `groups` (`by`) gets one more row per group (`est-1 · <group>`). A
   `diferencia` row names its two groups; a `reutilizacion` row is followed
   by its `histogram`; a `supervivencia` with `median_reached: false` shows
   «no alcanzada» as its value, and its `survival_at` as `S(t)` rows. Add a
   column `etiqueta`: «confirmatorio», «desviación E1 (datos vistos: sí)» for
   an estimate with `desviacion`, or «exploratorio» for a QE;
2. one plain sentence per estimand: what it measures, from the plan's
   `## Estimandos`, with its interval. Draw no conclusions beyond that;
3. the block, with `estimate.py`'s stdout **exactly**, nothing added:

````
```estimate-json
<stdout of estimate.py, unchanged>
```
````

Change nothing else in the note.

### 5. Independent verification

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/ledger/verifier_packet.py" \
  --vault <vault> --question Q-XXXX --out <tmp>/packet.md --manifest <tmp>/manifest.json --store
```

The packet holds the question, the frozen plan (not its ethics section) and
`## Resultado`. It never holds a data row. Dispatch `fresh-verifier` with the
stored packet's path (`packet` in the JSON `--store` printed) as its whole
prompt. Never send the packet's text, nothing added, no hint of what to check.
Save its reply to `<tmp>/report.md`, then record it on the question:

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/ledger/verifications.py" append \
  --note Projects/<slug>/Preguntas/Q-XXXX.md --verifier kairo/fresh-verifier@1.1.0 \
  --model <model id the agent reported> --verdict <verdict> --scope note \
  --report <tmp>/report.md --packet-sha256 <sha256 --store printed>
```

`--report` and `--packet-sha256` are both required here. Without them
`verifier_no_errors` fails closed.

- `errors_found`: show each finding. If it points at `## Resultado`, re-run
  step 3 and rewrite step 4 from the new output; never edit the numbers. If
  it points at the plan, the plan is frozen: tell the researcher. A real flaw
  in the plan is fixed by an amendment (labelled a declared deviation) or a
  new question, and that is their decision. Do not go on to step 6 until a new verification is
  `no_errors_found`.
- `cannot_assess`: report what the verifier said is missing. The gate does not
  pass.

### 6. Gate, propose, apply

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/ledger/study_status.py" gate    --vault <vault> --question Q-XXXX
python "${CLAUDE_PLUGIN_ROOT}/scripts/ledger/study_status.py" propose --vault <vault> --question Q-XXXX
```

Apply only when `gate` says `"ok": true`, and `propose` names
`"to": "estimada"`:

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/ledger/study_status.py" apply --vault <vault> \
  --question Q-XXXX --to estimada --by <agent id> \
  --evidence "puerta de estimada: plan congelado, estimaciones reproducibles, clasificadores validados, verificador sin errores, avisos cerrados"
```

If the gate fails, list every `missing` item to the researcher in Spanish,
each with its fix:

| `missing` says | Fix |
|---|---|
| `sin units_dataset` / `units_dataset vacío` | step 2 |
| `no está registrado en ningún commit del vault` | commit `Projects/<slug>/Datos/registro.json` alone (`git commit -m … -- <path>`), with the researcher's OK, then re-run the gate |
| `los datos se registraron antes de congelar el plan` | Not fixable for this plan: the data were in the vault before the freeze. The researcher decides (a new question and plan) |
| `el plan cambió después de congelarlo: …` | the researcher puts back the named sections exactly as they were at `frozen_commit` (keeping the frozen frontmatter); a real change is an amendment (`kairo:plan-study`, «Enmendar un plan congelado») |
| `la enmienda En … cambió después de registrarla` / `se borró` / `ya estaba antes de` / `no es posterior` | an amendment was edited, removed or re-pointed: the researcher restores it as it was at its commit (`git show <commit>:<plan>`); a correction is a new amendment |
| `enmienda En …: sin commit registrado (pendiente)` | `freeze_plan.py amend --vault <vault> --plan M-XXXX --reanudar` |
| `enmienda En …: declara datos_vistos: no, pero …` | the amendment was recorded after the units were registered. Not fixable by editing it: the researcher records a new amendment with `--datos-vistos si` restating the change |
| `el bloque estimate-json … no coincide` / `no tiene bloque` | steps 3–4 again, with no hand edits |
| `estimate.py se niega: …` | as in step 3 |
| `clasificador <n>: falta …validacion-<n>.json` / `…cambió después de validar` / `no coincide` | the researcher validates the classifier in the interface (Anotar → Validar clasificador), or runs `agreement.py validate` |
| `clasificador <n>: precision_lo/recall_lo … < …` | the classifier does not reach the plan's floor. The plan does not change: improve the classifier and validate it again, or the researcher decides |
| `verificación: …` / `otro paquete` / `sin sha256` | step 5 again (anything in the packet changed since) |
| `sin registro de avisos` / `avisos sin cerrar …` | the researcher closes the notices in the Avisos panel, or waits for the embargo date. Report only the counts the gate gave |
| `avisos sin enviar …` (plan with `"avisos": "enviados"`) | the researcher sends the pending notices and records their embargo date in the Avisos panel |

### 7. Commit and report

Commit the question note alone:
`git add <path> && git commit -m "Q-XXXX: estimación" -- <path>`.
Never `git add -A`. Report:

- the estimates, one line each with its interval and its label
  (confirmatorio / desviación En / exploratorio);
- the verifier verdict;
- the state now (`estimada`, or the state it stayed in);
- if the gate failed, the list from step 6.

## Common mistakes

- **Reading the units file** "just to check a column". The plan names the
  columns; `estimate.py` reports a missing one. Only its JSON reaches you.
- **Re-typing the JSON**, or pretty-printing it, into the block. Store the
  stdout as it came. The gate compares it with a fresh run.
- **Handing the verifier your summary** instead of the packet path.
- **Recording the verdict without `--report` and `--packet-sha256`.** The
  gate then fails closed.
- **Applying `sin_respuesta` because the gate failed.** A failed gate is a
  list of things to fix. «No answer» is the researcher's conclusion: el
  investigador decide sin_respuesta.
