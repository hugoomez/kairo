---
id: ME-XXXX
project: <PROJ-XXX>
# question — la pregunta exploratoria cuyo plan es éste (QE-XXXX). Exactamente una.
question: <QE-XXXX>
# Un plan exploratorio no se congela: se puede escribir después de ver los datos, y sus
# datos pueden estar registrados antes que él (incluso ser las unidades de una pregunta
# confirmatoria). Por eso todo lo que mide es citable solo con la etiqueta «exploratorio»:
# el manuscrito la escribe junto a cada cifra y solo lo vincula a una sección cuyo título
# lo dice.
status: borrador
---

# ME-XXXX — Plan exploratorio de QE-XXXX

## Población y marco muestral

<Qué se mide y de dónde salen las unidades. Si reutiliza las unidades de una pregunta confirmatoria, nómbrala. Ejemplo inventado: las unidades del dataset de Q-9001, sin filtro.>

## Inclusión y exclusión

<Criterios. Si se fijaron después de ver los datos, dilo aquí.>

## Por qué es exploratoria

<Qué se vio antes de escribir el plan, o por qué no se congeló antes de los datos. Ejemplo inventado: el desglose por formato surgió al leer los resultados de Q-9001.>

## Estimandos

<Mismo formato que el plan de medición (templates/measurement-plan-template.md): proporcion, conteo, mediana, diferencia, reutilizacion, supervivencia, con `filter`, `cluster` y `by`. Ejemplo inventado.>

```json
{"estimands": [
  {"id": "exp-1", "type": "proporcion", "column": "tiene_x", "filter": {}, "cluster": null, "by": "formato"}
]}
```

## Clasificadores

<Como en el plan de medición; lista vacía si no hay. La puerta exige su validación igual.>

```json
{"classifiers": []}
```

## Intervalos

<Nivel, remuestreos, semilla. `avisos` (opcional): "cerrados" (por defecto: la puerta exige cada aviso cerrado o con el embargo vencido) o "enviados" (basta con que estén enviados, aunque el embargo siga); "enviados" solo se justifica si la publicación da únicamente agregados.>

```json
{"ci": 0.95, "bootstrap": 2000, "seed": 12345}
```

## Ética

<Como en el plan de medición: lo que esta pregunta añade al plan ético del proyecto (Projects/<slug>/Etica.md). Ejemplo inventado: igual que Etica.md.>
