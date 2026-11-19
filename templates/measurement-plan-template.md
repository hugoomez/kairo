---
id: M-XXXX
project: <PROJ-XXX>
# question — la pregunta cuyo plan es éste. Exactamente una.
question: <Q-XXXX>
status: <borrador | congelado>
# frozen_at — momento en que se congeló el plan, antes de recolectar ni estimar nada.
frozen_at: <YYYY-MM-DDTHH:MM:SSZ>
# frozen_commit — sha del commit del vault que guarda el plan congelado; lo escribe
# freeze_plan.py. Nunca el sha del repositorio de código.
frozen_commit: <git-sha>
---

# M-XXXX — Plan de medición de Q-XXXX

## Población y marco muestral

<Población objetivo, marco del que salen las unidades y cómo se muestrea. Ejemplo inventado: unidades del registro M-9002, muestreo aleatorio simple.>

## Inclusión y exclusión

<Criterios, escritos antes de ver los datos.>

## Estimandos

<Cada estimando es una entrada del bloque JSON, que lee estimate.py. `id` único. `filter`: {columna: valor}, o {} si no hay filtro. `cluster`: columna de agrupación para el intervalo (bootstrap por conglomerados), o null si no hay; solo en proporcion y diferencia. `by` (opcional): columna categórica (herramienta, año, tipo de propietario; como mucho 50 valores) que añade una estimación por valor, para comparar grupos o ver una tendencia por periodos; en proporcion, conteo, mediana y supervivencia. Tipos:
- `proporcion`: fracción de filas cuya `column` es verdadera (1, true, sí). Wilson, o bootstrap si hay cluster.
- `conteo`: filas cuya `column` es verdadera; nombra una columna 0/1, no un identificador.
- `mediana`: de una `column` numérica, con bootstrap.
- `diferencia`: p(a) − p(b) de la `column` 0/1 entre dos valores `a` y `b` de la columna `group`. Newcombe, o bootstrap si hay cluster.
- `reutilizacion`: fracción de valores distintos de `column` (una huella de clave, nunca la clave) que aparecen en al menos `min_groups` (por defecto 2) valores distintos de `across` (repositorio, organización). Wilson sobre las claves, con un histograma de recuentos; nunca imprime una clave ni un grupo.
- `supervivencia`: mediana de Kaplan-Meier de `column` (tiempo hasta el cambio, p. ej. días), con `event` verdadero si el cambio se vio y falso si seguía sin cambiar al final de la observación (censura). `at`: lista de tiempos en los que dar S(t), [] si ninguno. Si la mediana no se alcanza, `value` es null y `median_reached` false.
Ejemplo inventado.>

```json
{"estimands": [
  {"id": "est-1", "type": "proporcion", "column": "tiene_x", "filter": {"grupo": "a"}, "cluster": "sitio"},
  {"id": "est-2", "type": "conteo", "column": "es_valida", "filter": {}, "cluster": null},
  {"id": "est-3", "type": "diferencia", "column": "tiene_x", "group": "formato", "a": "f1", "b": "f2", "filter": {}, "cluster": "sitio"},
  {"id": "est-4", "type": "reutilizacion", "column": "huella", "across": "sitio", "min_groups": 2, "filter": {}, "cluster": null},
  {"id": "est-5", "type": "supervivencia", "column": "dias", "event": "cambio_visto", "at": [365], "by": "formato", "filter": {}, "cluster": null}
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
