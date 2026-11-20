---
id: MP-XXXX
project: <PROJ-XXX>
# question — el piloto cuyo plan es éste (QP-XXXX). Exactamente uno.
question: <QP-XXXX>
# Un piloto no se congela: su plan se puede corregir a la vista de los datos del piloto.
# Por eso nada de lo que mide es citable; sirve para decidir si se sigue y para escribir
# el plan del estudio (M-XXXX), que sí se congela antes de sus propios datos.
status: borrador
---

# MP-XXXX — Plan del piloto QP-XXXX

## Población y marco muestral

<Qué se explora y de dónde salen las unidades del piloto. Ejemplo inventado: 300 unidades al azar del marco M-9002, semilla 7.>

## Inclusión y exclusión

<Criterios provisionales; el plan del estudio fijará los definitivos.>

## Estimandos

<Mismo formato que el plan de medición (templates/measurement-plan-template.md): proporcion, conteo, mediana, diferencia, reutilizacion, supervivencia, con `filter`, `cluster` y `by`. Lo que el piloto quiere saber para decidir: tasas aproximadas, cuántas unidades hay, cuántas son de cada tipo. Ejemplo inventado.>

```json
{"estimands": [
  {"id": "pil-1", "type": "proporcion", "column": "tiene_x", "filter": {}, "cluster": null, "by": "formato"}
]}
```

## Clasificadores

<Normalmente ninguno: en el piloto se anota a mano. Lista vacía si no hay.>

```json
{"classifiers": []}
```

## Intervalos

```json
{"ci": 0.95, "bootstrap": 2000, "seed": 12345}
```

## Decisión

<Qué decide el piloto y con qué criterio, escrito antes de mirar sus resultados. Ejemplo inventado: seguir si la tasa de x supera el 1 % con al menos 50 casos esperados en el estudio. El tamaño de las muestras de validación del estudio sale de `estimate.py tamano --p <tasa del piloto> --half-width <precisión>`.>

## Ética

<El piloto sigue el plan ético del proyecto (el campo `etica:` del hub, Projects/<slug>/Etica.md, hecho desde templates/ethics-plan-template.md), aunque sus datos no se citen. Aquí solo lo que el piloto añade: qué material de terceros puede aparecer y cómo se protege. Los mismos avisos que el estudio si el piloto encuentra algo que lo exija. Ejemplo inventado: igual que Etica.md; la anotación a mano ve solo la huella y el tipo, nunca el valor.>
