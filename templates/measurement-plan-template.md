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
- `supervivencia`: mediana de Kaplan-Meier de `column` (tiempo hasta el cambio, p. ej. días), con `event` verdadero si el cambio se vio y falso si seguía sin cambiar al final de la observación (censura). `at`: lista de tiempos en los que dar S(t), [] si ninguno. Si la mediana no se alcanza, `value` es null y `median_reached` false. `curve: true` (opcional) añade la curva de Kaplan-Meier como escalones agregados (t, S(t), unidades en riesgo); un escalón con menos de 5 unidades en riesgo no se imprime, solo se cuenta en `curve_suppressed`.
- `tabla`: tabla cruzada de dos columnas categóricas `rows` y `cols` (sin `column`): recuentos y proporción por fila con Wilson. Como mucho 50 valores por columna; se niega con una columna que parezca un identificador (casi un valor por fila, o valores con forma de hash, correo o URL).
- `interseccion`: fracción de filas con TODAS las `columns` (de 2 a 6 columnas 0/1, sin `column`) verdaderas, p. ej. A ∧ B ∧ C. Wilson, o bootstrap si hay cluster. Añade el recuento de cada combinación verdadero/falso (tipo UpSet) y el de cada columna.
- `tendencia`: tendencia de la proporción de la `column` 0/1 a lo largo de la columna ordenada `by` (p. ej. el año): estadístico z de Cochran-Armitage y su p bilateral, pendiente por unidad de `by` e intervalo de Wald, y una estimación por nivel. Puntuaciones: el valor del nivel si todos son números; si no, o con `order` (lista de los niveles en su orden), su posición.
- `cuantiles`: cuantiles `q` (lista de números entre 0 y 1) de una `column` numérica, interpolación lineal (tipo 7 de R), con bootstrap.
Campos de cualquier estimando: `units` (opcional) nombra la tabla de unidades del estimando cuando el estudio tiene varias unidades de análisis (fichero, repositorio, huella de clave); sin `units`, la tabla por defecto. La pregunta dice qué dataset es cada tabla en `units_dataset`. `variants` (opcional): análisis de sensibilidad, cada uno `{"id", "filter"}`; se calcula como el estimando con su filtro añadido (si chocan, gana el de la variante) y sale bajo el estimando en `variants`.
`families` (opcional, al nivel de `estimands`): comparaciones múltiples entre estimandos `diferencia`. Cada familia `{"id", "estimands": [...], "method": "holm" | "bonferroni"}`; un estimando, como mucho en una familia. Se informa del p bruto (inversión del intervalo de Newcombe, o bootstrap por conglomerados) y del ajustado, y del intervalo ajustado de Bonferroni.
Ejemplo inventado.>

```json
{"estimands": [
  {"id": "est-1", "type": "proporcion", "column": "tiene_x", "filter": {"grupo": "a"}, "cluster": "sitio"},
  {"id": "est-2", "type": "conteo", "column": "es_valida", "filter": {}, "cluster": null},
  {"id": "est-3", "type": "diferencia", "column": "tiene_x", "group": "formato", "a": "f1", "b": "f2", "filter": {}, "cluster": "sitio"},
  {"id": "est-4", "type": "reutilizacion", "column": "huella", "across": "sitio", "min_groups": 2, "filter": {}, "cluster": null},
  {"id": "est-5", "type": "supervivencia", "column": "dias", "event": "cambio_visto", "at": [365], "by": "formato", "curve": true, "filter": {}, "cluster": null},
  {"id": "est-6", "type": "proporcion", "column": "es_publica", "units": "claves", "filter": {}, "cluster": null,
   "variants": [{"id": "sin-copias", "filter": {"es_copia": "0"}}]},
  {"id": "est-7", "type": "diferencia", "column": "tiene_x", "group": "formato", "a": "f1", "b": "f3", "filter": {}, "cluster": "sitio"},
  {"id": "est-8", "type": "tabla", "rows": "formato", "cols": "tipo_propietario", "filter": {}, "cluster": null},
  {"id": "est-9", "type": "interseccion", "columns": ["tiene_x", "tiene_y", "tiene_z"], "filter": {}, "cluster": "sitio"},
  {"id": "est-10", "type": "tendencia", "column": "tiene_x", "by": "anio", "filter": {}, "cluster": null},
  {"id": "est-11", "type": "cuantiles", "column": "dias", "q": [0.25, 0.5, 0.75], "filter": {}, "cluster": null}
],
 "families": [{"id": "fam-1", "estimands": ["est-3", "est-7"], "method": "holm"}]}
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

<El marco ético del estudio está en el plan ético del proyecto (el campo `etica:` del hub, Projects/<slug>/Etica.md, hecho desde templates/ethics-plan-template.md): partes afectadas, qué se recoge y qué nunca, material sensible, condiciones de uso, comité, divulgación, conservación y publicación. Aquí solo lo que esta pregunta añade o cambia: datos personales, consentimiento, riesgos propios y cómo se protegen; qué material no puede salir del entorno del investigador. Ejemplo inventado: igual que Etica.md; además, la columna `huella` del estimando est-4 nunca se publica, ni siquiera agregada por sitio.>
