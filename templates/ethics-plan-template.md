---
project: <PROJ-XXX>
# tipo — fijo: identifica la nota como el plan ético del proyecto. El hub la nombra en `etica:`.
tipo: plan-etico
# Un estudio de medición sobre datos públicos de terceros. create-project copia esta
# plantilla a Projects/<slug>/Etica.md al crear un proyecto `medicion`; la rellena el
# investigador. Cada plan de medición (M-XXXX) y de piloto (MP-XXXX) remite aquí en su
# `## Ética` y solo añade lo propio de su pregunta.
status: <borrador | revisado>
# comite — ¿necesita el estudio un comité de ética, y en qué estado está?
comite: <no_necesario | pendiente | solicitado | aprobado | exento>
# revisado — última fecha en que el investigador revisó este plan entero.
revisado: <YYYY-MM-DD>
---

# Plan ético del proyecto

> Lo escribe y lo decide el investigador. Un agente puede proponer texto, pero nunca
> marca el plan como `revisado` ni cambia `comite`.

## Partes afectadas

<Quién puede verse afectado por la recogida, el análisis o la publicación: titulares de los datos, terceros que aparecen en ellos sin haberlos publicado, los operadores de la plataforma, los usuarios de lo que se mida. Para cada parte, qué puede perder. Ejemplo inventado: autores de las unidades del marco M-9002 (exposición de un descuido suyo); terceros cuyos datos aparecen dentro de esas unidades (no publicaron nada); el operador del servicio S-9001 (carga en su API).>

## Beneficios y daños

<Los cuatro principios del Menlo Report, uno por línea: qué exige aquí cada uno y cómo se cumple.
- Respeto a las personas: consentimiento, o por qué no es posible pedirlo y qué lo sustituye.
- Beneficencia: el beneficio esperado frente al peor daño plausible, y qué lo reduce.
- Justicia: quién carga con el riesgo y quién se beneficia; que no sean siempre los mismos.
- Respeto a la ley y al interés público: normas y condiciones aplicables, y cómo se rinde cuentas de lo hecho.
Ejemplo inventado: beneficencia — saber cuántas unidades exponen material x ayuda a corregirlo; el peor daño es señalar a una persona concreta, y se evita publicando solo agregados.>

## Qué se recoge y qué nunca

<Lista cerrada de lo que el código de recogida guarda (campos, metadatos) y lista de lo que nunca hace, aunque fuera técnicamente posible. Lo que no está en la primera lista no se guarda. Ejemplo inventado: se guarda la ruta del fichero, la fecha del cambio y si contiene el patrón p; nunca se descifra nada, nunca se usa una credencial encontrada (ni para comprobar si funciona), nunca se sondea activamente un servicio ajeno, nunca se guarda el contenido completo de una unidad.>

## Material sensible encontrado y su tratamiento

<Qué material sensible puede aparecer (claves, credenciales, datos personales) y qué se hace con él: solo en memoria durante el análisis, sustituido en origen por su huella (scripts/security/redact.py en el script de recogida), dataset registrado `sensitive: true`. Quién puede verlo y durante cuánto tiempo. Ejemplo inventado: una clave encontrada se reduce en memoria a su huella SHA-256 truncada y a su tipo; el valor nunca toca el disco ni el vault, y la huella basta para contar reutilizaciones.>

## Condiciones de uso de la plataforma o API

<Qué condiciones de uso, licencias y límites de tasa rigen los datos y las API usadas, y cómo las respeta la recogida (autenticación propia, ritmo, caché, identificación del cliente). Si una condición prohíbe algo que el estudio necesita, se dice aquí y se decide antes de recoger. Ejemplo inventado: la API del servicio S-9001 permite uso de investigación con 1200 peticiones por hora; la recogida usa la mitad, guarda en caché cada respuesta y no vuelve a pedirla.>

## Comité de ética

<Si el estudio necesita la aprobación de un comité de ética (o una exención por escrito), por qué, y su estado; el campo `comite` del frontmatter lo resume. Referencia del expediente y fecha si existe. Ejemplo inventado: no hay sujetos humanos en sentido estricto, pero los datos pueden identificar personas; se pide una exención al comité de la institución (expediente E-9001, solicitado el 2030-01-15).>

## Divulgación a los afectados

<Cómo se avisa a quien queda expuesto: canal (contacto de seguridad del proyecto, formulario del operador, correo), qué se le da, plazo de no divulgación antes de publicar y qué pasa si no responde. Cada aviso se registra en el panel Avisos de la interfaz (Projects/<slug>/Avisos/registro.json); la puerta `avisos_cerrados` impide estimar con avisos abiertos y sin vencer. Ejemplo inventado: aviso privado al contacto de seguridad de cada unidad afectada, con la ruta y la huella pero nunca el valor; 90 días de no divulgación; sin respuesta a los 90 días, el caso entra solo en los agregados.>

## Conservación y borrado

<Qué se guarda, dónde (repositorio de código, almacenamiento aparte; nunca el vault para datos crudos), con qué acceso, y en qué fecha se borra cada cosa. Lo que se conserva para reproducir el estudio y lo que no. Ejemplo inventado: los datos crudos se borran el 2031-06-30; se conservan las huellas y la tabla de unidades sin contenido hasta el fin del proyecto; el borrado se apunta en la bitácora.>

## Publicación

<Qué se publica: solo agregados (recuentos, proporciones con su intervalo), nunca una unidad, una persona o un valor sensible identificable; tamaño mínimo de celda; qué se comparte con otros investigadores y en qué condiciones. Ejemplo inventado: tablas con celdas de al menos 10 unidades; ningún ejemplo literal; las huellas no se publican; el código se publica sin datos.>

## Riesgos residuales

<Lo que puede salir mal aun cumpliendo todo lo anterior, su probabilidad, su efecto y qué se haría si pasa. Ejemplo inventado: un agregado pequeño permite adivinar la unidad de origen — se fusionan categorías antes de publicar; una fuga del almacenamiento de datos crudos — se borra en cuanto acaba el análisis y se avisa a los afectados.>
