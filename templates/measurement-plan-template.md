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

<Cada estimando es una entrada del bloque. `type`: proporcion | conteo | mediana. `filter`: columna: valor. `cluster`: columna de agrupación para el intervalo (vacío si no hay).>

```yaml
estimands:
  - id: est-1
    type: proporcion
    column: tiene_x
    filter: {grupo: a}
    cluster: sitio
```

## Clasificadores

<Clasificadores que etiquetan unidades. `sample`: muestra de validación; las cotas son el límite inferior mínimo aceptable del intervalo.>

```yaml
classifiers:
  - name: clasif-1
    sample: validacion-1
    min_precision_lo: 0.90
    min_recall_lo: 0.80
```

## Intervalos

```yaml
ci: 0.95
bootstrap: 2000
seed: 12345
```

## Ética

<Datos personales, consentimiento, riesgos y cómo se protegen. Qué material no puede salir del entorno del investigador.>
