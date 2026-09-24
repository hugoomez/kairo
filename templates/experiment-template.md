---
id: E-XXXX
# hypothesis — PRIMARY: the single hypothesis whose verdict this experiment
# adjudicates. Drives `result.verdict` and the update-confidence `evidence result`
# edge. Exactly one.
hypothesis: <H-XXXX>
# secondary_hypotheses — optional: other hypotheses this design also bears on,
# producing collateral / non-confirmatory evidence only. Each needs a
# "## Evidencia colateral" entry (what it shows, why it cannot adjudicate that
# hypothesis). These are NEVER given a decision threshold and NEVER trigger
# update-confidence. Use for rival-pair designs where one sweep informs both sides.
secondary_hypotheses: []   # e.g. [H-0001]
project: <PROJ-XXX>
tier: <ligero | completo>
# analysis_plan — chosen at preregistration time, independent of tier.
# frequentist -> scripts/analysis/two_proportion_test.py (run-experiment step 5)
# bayesian    -> scripts/analysis/bayes_factor_proportions.py (run-experiment step 5)
analysis_plan: <frequentist | bayesian>
status: <preregistered | running | completed>
# frozen_at — timestamp when the preregistration was frozen (before any code ran)
frozen_at: <YYYY-MM-DDTHH:MM:SSZ>
# frozen_commit — git sha of the experiment code at freeze time
frozen_commit: <git-sha>
environment:
  seed: <int>
  dependencies_lockfile: <path to the saved pip-freeze / npm-ls snapshot, e.g. E-XXXX.deps.txt>
  dependencies_hash: <sha256 of that snapshot file>
  dataset_hash: <sha256 of the dataset snapshot, or n/a>
  hardware: <e.g. 1x A100 80GB>
  # tools — validated paper-to-tool tools this design uses (preregister-experiment
  # step 3f); run-experiment pre-flight re-verifies each hash with tool_hash.py.
  tools: []   # e.g. [{path: Tools/P-0002/modular-addition-training, validation_hash: <sha256>}]
# method_provenance — where the code of a reproduced paper's method comes from.
# reimplemented_from_text carries an `importante` flag: "método reimplementado
# desde el texto, no validado contra el código original" (see ## Manifiesto de entorno).
method_provenance: <n/a | validated_tool | reimplemented_from_text>
experiment_validity: <valid | invalid>
sanity_checks:
  baseline_reproduces: <true | false>
  loss_decreases: <true | false>
  no_data_leakage: <true | false>
  seed_controls_variance: <true | false>
cost_estimated: <value>
cost_actual: <value>
result:
  effect: <point estimate + interval, or null until completed>
  # use the field that matches the analysis plan:
  p_value: <value>       # frequentist plan
  bayes_factor: <value>  # bayesian plan
  verdict: <apoyada | refutada | inconclusa | evidencia_mixta>
---

## Predicción

<Qué se espera observar si la hipótesis es cierta, en términos cuantitativos.>

## Variables

- **Independiente(s)**: <...>
- **Dependiente(s)**: <...>
- **Controladas**: <...>

**Registradas — obligatorio, dividido en dos listas:**

- **Primarias (deciden el veredicto)**: <la(s) métrica(s) del `## Plan de
  análisis`. Nada más puede promoverse a insumo del veredicto después.>
- **Secundarias / exploratorias**: <todo lo demás que se registra —
  diagnósticos, ablaciones, cortes por subgrupo. Nunca deciden este veredicto;
  un hallazgo que solo aparece aquí es una hipótesis nueva.>

## Diseño

<Estructura del experimento: condiciones, brazos, réplicas, aleatorización.>

## Evidencia colateral

<Una entrada por hipótesis en `secondary_hypotheses`. Para cada una: qué
observación (secundaria / exploratoria, nunca la métrica primaria) del diseño la
toca, qué sugeriría, y **por qué no puede adjudicarla** (sin umbral, sin
veredicto, sin réplica dedicada). run-experiment añade el resultado colateral
observado a `## Resultado`; no dispara update-confidence para estas hipótesis.
Omitir la sección si `secondary_hypotheses` está vacío.>

## Plan de análisis

<Test o modelo exacto, estadístico de decisión, umbral (α o Bayes factor),
correcciones por comparaciones múltiples. Congelado en `frozen_at`.
Tier `completo`: incluir el tamaño de efecto mínimo de interés (SESOI), α,
potencia objetivo, y el N resultante de `sample_size.py`. `analysis_plan:
bayesian`: incluir el prior (Beta(a, b)) y el umbral de Bayes factor.>

**Regla de parada (obligatoria):** <la condición exacta que termina la
recolección / la corrida, fijada ahora — p. ej. `N fijo = 2000 por brazo`,
`5 semillas fijas`, `hasta agotar 6 GPU-h`, `una pasada sobre el dataset
congelado`. "Hasta que se vea claro" no es una regla de parada.>

## Umbral de invalidez

<Condiciones que marcan `experiment_validity: invalid` (p. ej. sanity check
fallido, fuga de datos, divergencia de entrenamiento, hardware distinto al
preregistrado).>

## Manifiesto de entorno

<Comandos exactos y cuándo se corrieron, hashes por archivo de dataset, rutas de
los snapshots, y a qué repo apunta `frozen_commit`.>

**Procedencia del método:** <solo si el diseño reproduce el método de un paper:
`Tools/P-XXXX/<method>` + `validation_hash`, o — `importante` — "método
reimplementado desde el texto, no validado contra el código original" + motivo
(sin código público / herramienta rechazada: <motivo> / ofrecida y declinada).
Omitir si `method_provenance: n/a`.>

## Resultado

<Sección nueva, escrita por run-experiment tras la corrida (no es edición de una
sección congelada): comando exacto, salida completa del script de análisis,
veredicto mapeado, y `estimado X → real Y` de coste/tiempo.>

## Enmiendas

<Append-only. Cualquier cambio posterior al freeze va aquí, nunca editando una
sección congelada. Una entrada por enmienda: fecha, qué cambió, motivo, sección
afectada, antes → después.>
