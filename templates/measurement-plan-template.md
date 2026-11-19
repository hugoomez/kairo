---
id: M-XXXX
project: <PROJ-XXX>
# question — la pregunta cuyo plan es éste. Exactamente una.
question: <Q-XXXX>
status: <borrador | congelado>
# frozen_at — momento en que se congeló el plan, antes de recolectar ni estimar nada.
frozen_at: <YYYY-MM-DDTHH:MM:SSZ>
# frozen_commit — sha git del código de recolección y estimación al congelar.
frozen_commit: <git-sha>
---

# M-XXXX — Plan de medición de Q-XXXX

## Población y marco muestral

<Población objetivo, marco del que salen las unidades y cómo se muestrea. Ejemplo inventado: unidades del registro M-9002, muestreo aleatorio simple.>

## Inclusión y exclusión

<Criterios, escritos antes de ver los datos.>

## Estimandos

<Cada estimando es una entrada del bloque JSON, que lee estimate.py. `type`: proporcion | conteo | mediana. `id` único. `filter`: {columna: valor}, o {} si no hay filtro. `cluster`: columna de agrupación para el intervalo, o null si no hay. Ejemplo inventado.>

```json
{"estimands": [
  {"id": "est-1", "type": "proporcion", "column": "tiene_x", "filter": {"grupo": "a"}, "cluster": "sitio"},
  {"id": "est-2", "type": "conteo", "column": "id_unidad", "filter": {}, "cluster": null}
]}
```

## Clasificadores

<Clasificadores que etiquetan unidades. `sample`: muestra de validación; las cotas, entre 0 y 1, son el límite inferior mínimo aceptable del intervalo de precisión y de exhaustividad. Lista vacía si no hay clasificadores. `label` y `positive` fijan la columna y el valor positivo sobre los que se valida; `ci` (opcional, 0.95 por defecto) es el nivel del intervalo. La puerta rechaza una validación hecha con otros.>

```json
{"classifiers": [
  {"name": "clasif-1", "sample": "validacion-1", "label": "es_marca", "positive": true, "min_precision_lo": 0.90, "min_recall_lo": 0.80}
]}
```

## Intervalos

<Nivel del intervalo, remuestreos del bootstrap y semilla fija.>

```json
{"ci": 0.95, "bootstrap": 2000, "seed": 12345}
```

## Ética

<Datos personales, consentimiento, riesgos y cómo se protegen. Qué material no puede salir del entorno del investigador.>
