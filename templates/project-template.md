---
id: PROJ-XXX
name: <value>
created: <YYYY-MM-DD>
status: <active | paused | archived>
type: <ciencia | producto | hibrido>
# deadline — optional
deadline: <YYYY-MM-DD>
# compute_budget — optional (e.g. "500 GPU-h" or a currency figure)
compute_budget: <value>
# completo_cost_threshold — optional, same unit as compute_budget.
# preregister-experiment requires the `completo` tier (formal sample-size
# justification) once a hypothesis's cost_estimated exceeds this value, in
# addition to the linea_publicacion trigger. Unset, or a unit mismatch with
# cost_estimated, means cost alone cannot trigger completo.
completo_cost_threshold: <value>
# ladder_cost_threshold — optional, same unit as compute_budget. When a
# confirmatory design's cost_estimated exceeds it, preregister-experiment step 0
# OFFERS a simplification ladder (2-3 cheap exploratory rungs, role:
# exploratory, rung 0-2) before the confirmatory freeze. Never forced. Unset:
# offered whenever the design needs a GPU or its cost is >= 1 GPU-h or unknown.
# Also offered regardless of cost when method_provenance would be
# reimplemented_from_text.
ladder_cost_threshold: <value>
autonomy_defaults:
  paper_ingestion: <manual | autonomo>
  experiments: <manual | autonomo>
  hypothesis_promotion: <manual | autonomo>
# related_projects — auto-computed from shared papers / hypotheses; do not edit by hand
related_projects: []
# send — optional. `send: never` keeps this note's content from ever reaching
# the model or an external API: skills skip it and the vault's send_guard
# PreToolUse hook blocks reading it. Any vault note may carry it (Papers/,
# hypotheses, experiments). Omit the field for normal notes.
# send: never
---

## Propósito

<Meta o pregunta central del proyecto. No forzar a forma interrogativa.>

## Alcance

### Dentro

- <Qué entra en este proyecto.>

### Fuera

- <Qué queda explícitamente fuera.>

## Motivación

<Por qué vale la pena hacerlo ahora.>

## Objetivos específicos

- <Objetivo medible 1.>
- <Objetivo medible 2.>

## Vocabulario conocido

- **<término>**: <definición operacional en el contexto del proyecto.>

## Papers semilla

- <P-XXXX — por qué es punto de partida.>

## Sistema o baseline actual

<Qué existe hoy: baseline, sistema en producción, o "ninguno".>

## Criterios de éxito

### Científico

- <Condición que decide si la ciencia salió bien.>

### Producto

- <Condición que decide si el producto salió bien.>
