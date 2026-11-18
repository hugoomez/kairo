---
id: Q-XXXX
project: <PROJ-XXX>
# design — clave de config/designs.toml que rige los estados y la puerta de esta pregunta.
design: medicion
# status — lo escribe solo study_status.py; no lo edites a mano.
# abierta | plan_congelado | en_recoleccion | estimada | sin_respuesta | descartada
status: abierta
# linea_publicacion — true si esta pregunta alimenta el manuscrito.
linea_publicacion: false
# linked_plan — plan de medición congelado (M-XXXX); vacío hasta que se congele.
linked_plan:
history: []
---

# Q-XXXX — <título corto>

## Pregunta

<La pregunta en una frase, respondible con una estimación. Ejemplo inventado: ¿qué proporción de las unidades de la muestra Q-9001 presenta la característica X?>

## Población y marco

<A quién o a qué se refiere y de dónde se sacan las unidades. El detalle muestral va en el plan M-XXXX.>

## Estimandos

<Lo que se quiere estimar: tipo (proporcion | conteo | mediana), sobre qué y con qué filtro. Se fija de forma definitiva en el plan.>

## Resultado

<Vacío hasta que la pregunta pase a estimada o sin_respuesta. Estimaciones con su intervalo, tal como las escribe estimate.py.>

## Historia

<Una línea por cambio de estado: fecha, de -> a, motivo.>
