---
# TOOL.md — manifest of one extracted, validated (or rejected) method.
# Lives at <vault>/Tools/P-XXXX/<method>/TOOL.md. Written by `paper-to-tool`.
# TOOL.md is the only file NOT covered by validation_hash (it carries the hash);
# once status is validated or rejected, only `## Enmiendas` may change.
paper: <P-XXXX>
method: <kebab-case method name — same as the folder name>
method_description: "<one sentence: exactly which method / analysis, in the paper's terms>"
status: <in_progress | validated | rejected>
rejection_reason: "<one line — only when status is rejected>"
repo: <https://github.com/<owner>/<repo> — the paper note's code_repo>
repo_license: <SPDX id | none — `none` means research use in this local vault only, never redistributed>
commit: <full 40-char SHA the clone was pinned to>
commit_date: <YYYY-MM-DD>
environment:
  python: <e.g. 3.11.9>
  lockfile: env/requirements.lock.txt
  lockfile_hash: <sha256 of env/requirements.lock.txt>
  hardware: "<e.g. CPU only, 8-core x86_64 — or the Kaggle GPU it ran on>"
  isolation: <container | process + sandbox_guard (not OS isolation)>
reference:
  kind: <test | example | shipped_outputs | paper_reported>
  source: "<upstream path@sha, or P-XXXX §… for paper-reported results>"
  outputs: <reference/outputs/… — the captured ground truth>
tolerance:                          # frozen before attempt 1 — see ## Tolerancia
  replay: {rtol: <1e-5>, atol: <1e-8>}
  published: {rtol: <0.03>, atol: <0>}     # or "n/a — no authors' outputs"
validation:
  attempts: <n of max 6>
  replay: <pass | fail | not_run>
  published: <pass | fail | not_run | n/a>
  validation_hash: <sha256 printed by tool_hash.py compute — empty until frozen>
created: <YYYY-MM-DD>
frozen_at: <YYYY-MM-DDTHH:MM:SSZ — when status became validated / rejected>
cost:
  wall_clock: "<total, and per phase: env build / reference run / validation>"
  compute: "<CPU-h / GPU-h, local or Kaggle>"
  claude_session: "<from /cost if readable; else `not measured`>"
used_by: []                         # experiment ids whose preregistration froze this tool's hash
---

# <P-XXXX> — <method>

## Qué hace

<Two or three sentences: the method, its inputs and outputs, and what it is
for. Not a restatement of the paper.>

## Uso

```
python tool/<method>.py --params <params.json> --out <fresh output dir>
```

<Parameter table: name, default (= upstream value), meaning. Output files.>

## Especificación exacta

<For a model / training setup: normalization, weight tying, biases, init per
parameter, optimizer + schedule, loss (incl. dtype), data split, seeding.
This is what `preregister-experiment` quotes into a preregistration.>

## Mapa de fuentes

| Bloque extraído | Archivo upstream | Líneas | Cambio |
|---|---|---|---|
| <class / function> | <path> | <a–b> | <verbatim / parameterized: … / removed: …> |

All line numbers refer to commit `<sha>`.

## Particularidades del código original

<Upstream behavior preserved as-is, with its consequence — e.g. an unseeded
RNG, a dtype cast that contradicts its own comment, a constant hard-coded to
one configuration. Each with how the tool exposes it (parameter + default).>

## Material de referencia

<What was run to get ground truth, the exact command(s), inputs, where the
outputs are, and whether a replay of upstream reproduced itself.>

## Tolerancia

<Frozen before attempt 1. Per compared quantity: class (replay / published),
rtol / atol or landmark window, horizon for chaotic trajectories, and the
justification.>

## Validación

| Intento | Comparación | Resultado | max_abs_err | max_rel_err | Cambio aplicado |
|---|---|---|---|---|---|
| 1 | replay | <pass/fail> | <…> | <…> | <—> |

## Motivo de rechazo

<Only when rejected: which comparison failed, last max error, what each
attempt tried, what would have to change for a retry to make sense.>

## Riesgos señalados

<Each flag with its severity: crítico / importante / menor.>

## Enmiendas

<Append-only after freeze. Date, what, why.>
