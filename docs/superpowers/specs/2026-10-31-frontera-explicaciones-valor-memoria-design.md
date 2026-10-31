# Frontera del estado del arte, explicaciones didácticas, valor de cada hipótesis y memoria del progreso

Fecha: 2026-10-31. Afecta al plugin (este repo) y a la app (`Kairo/app/vault-backend`).
Los ejemplos de este documento son inventados (ids `P-9001`, `H-9001`…), nunca de un vault.

## Qué pide el investigador

1. **Explicaciones didácticas** de uno o varios papers, de una casilla de la frontera o del
   propio proyecto, con esquemas y recursos que ayuden a entender, no solo texto.
2. **Mapa de la frontera** como función de Kairo: matrices de cobertura con casillas marcadas
   (`hueco`, `sin_sentido`, `no_buscado`), mejor resultado conocido por aspecto, huecos
   priorizados y posicionamiento del proyecto. En la app, interactivo: cada casilla se puede
   preguntar al modelo, y desde ella se lanzan acciones (buscar, explicar, proponer hipótesis).
3. **Hipótesis que razonan con la frontera**: se proponen desde huecos reales, nunca desde lo
   no buscado sin buscar antes, y cada una queda enlazada a su casilla.
4. **Cada hipótesis dice si merece la pena y cuánto costará**, con la base de la estimación.
5. **Memoria del progreso**: no repetir trabajo (experimentos equivalentes, lecturas ya hechas)
   y que el mapa, los huecos y las siguientes acciones reflejen lo que ya se ha hecho.

## Decisiones

- **La frontera vive en el vault**, en `Projects/<slug>/Frontera/`. `mapa.yaml` es la fuente
  de verdad; `scripts/frontier/frontier.py` valida y genera las vistas y `mapa.json`, que es
  lo que lee la app. Nada de la frontera vive en el repo del plugin salvo el motor.
- **La taxonomía es por proyecto** y la aprueba el investigador (fase 1 de la skill), igual
  que se hizo a mano en PROJ-002. Los ejes y sus valores se declaran en `mapa.yaml`.
- **Lo que se lee se guarda**: cada paper extraído deja `Frontera/extracciones/P-XXXX.json`
  (valores por eje con citas literales). Al añadir un paper solo se extrae ese paper.
- **La capa «nuestro» se calcula de las notas**: hipótesis con `frontier_cells` y su
  `status`, experimentos y su estado. Nadie la edita a mano; se recalcula en cada ejecución.
- **Campos planos en el frontmatter de la hipótesis** (el parser de Kairo es de líneas).
- **Las acciones interactivas de la app reutilizan lo que ya existe**: el chat del proyecto
  (conversación sembrada con el contexto de la casilla), el ciclo de hipótesis con `gap`,
  la búsqueda bibliográfica y el nuevo job de explicación.

## Contratos

### `Projects/<slug>/Frontera/mapa.yaml`

Mismo esquema que el mapa de PROJ-002 hecho a mano, generalizado:

```yaml
meta:
  proyecto: PROJ-XXX
  fecha: AAAA-MM-DD
  escrito_por: modelo
  citable: false
  aviso_cobertura: <texto>            # qué sesgos tiene la búsqueda que formó el corpus
  verificacion: {fecha, herramienta, citas_ok, citas_total}
ejes:                                  # la taxonomía aprobada
  <eje>: {nombre, regla, valores: {<valor>: <descripción>}}
papers:                                # una entrada por paper; los ejes salen de extracciones/
  - id: P-XXXX
    confianza: leido_completo | solo_resumen | insight_kairo | cita_sin_verificar
    ejes: {<eje>: [valores] | valor}
    notas_ejes: {<eje>: "Deducido: …"}
    evidencia: [{eje, cita, loc}]
    ideas: [<idea>]
    resultados: [{id, aspecto, valor, condiciones: {grafos, tamano, metrica, baseline}, cita, loc, fuente?}]
    limitaciones: [{origen: autores|deducida, texto, cita?, loc?}]
nuestro_proyecto:                      # opcional: componentes que no son hipótesis (código, baselines)
  componentes: [{id, nombre, estado, ejes, ideas, resultados}]
ideas: [{id, nombre, descripcion}]
relaciones: [{de, tipo: se_basa_en|compite_con|se_combina_con|contradice, a, origen, citas: [{paper, loc, cita?}], nota?}]
matrices: [{id, titulo, pregunta, filtro_aportacion?, filas: {eje, valores}, columnas: {eje, valores},
            por_defecto: {fila: {...}, columna: {...}}, vacias: {"fila|col": {marca, por_que}}}]
frontera: [{id, aspecto, refs: [P-XXXX#rid], mejor, comparables, nota}]
huecos: [{id, prioridad, titulo, por_que, evidencia: [{ref, loc?, texto}], celdas: ["M1:fila|col"],
          realidad, dificultad, relevancia, barato: {si, por_que}, encaje: {nivel, texto}, tarea}]
hipotesis_clave: {enunciado, a_favor, en_contra, veredicto}   # opcional
posicionamiento: {nuevo: [], ya_existe: [], no_podemos_afirmar: []}
```

`frontier.py` comprueba lo mismo que `generar.py` (valores definidos, cifras en la cita,
casillas vacías con marca y razón) y además **cada cita contra el paper** con el resolver de
`verifier_packet` (el de `check_quotes.py`): una cita que no pasa es un error.

### `Projects/<slug>/Frontera/extracciones/P-XXXX.json`

```json
{"paper": "P-9001", "taxonomia_sha": "<sha256 de ejes>", "extraido": "2026-10-31",
 "ejes": {"<eje>": {"valores": ["..."], "citas": [{"cita": "...", "loc": "§3.2"}], "nota": "Deducido: …"}},
 "resultados": [...], "limitaciones": [...], "ideas": [...], "no_leido": ["§5"]}
```

Si cambia la taxonomía (`taxonomia_sha` distinto) la extracción se marca como obsoleta.

### `Projects/<slug>/Frontera/mapa.json` (lo lee la app)

```json
{"proyecto": "PROJ-XXX", "generado": "<iso>", "obsoleto": {"papers_sin_extraer": [], "taxonomia_cambiada": []},
 "resumen": {...}, "papers": [...], "matrices": [{"id", "titulo", "pregunta", "filas": [{"v","desc"}], "cols": [...],
   "celdas": [[{"papers": [], "nuestros": [{"id": "H-9001", "tipo": "hipotesis", "status": "propuesta"}],
               "marca": null|"hueco"|"no_buscado"|"sin_sentido", "por_que": null|"..."}]]}],
 "frontera": [...], "huecos": [{..., "atacado_por": [{"id": "H-9001", "status": "propuesta"}]}],
 "ideas": {"nodos": [...], "aristas": [...]}, "posicionamiento": {...}, "hipotesis_clave": {...}}
```

Cada casilla tiene un id estable `M1:fila|col` que usan la app y las hipótesis.

### Frontmatter nuevo de la hipótesis (`templates/hypothesis-template.md`)

```yaml
frontier_cells: []            # p. ej. [M1:udg_plantado|amortizado]; vacío si no hay frontera
vale_la_pena: <si | dudoso | no>
vale_la_pena_razon: <una línea>
importancia: <alta | media | baja>
dificultad: <baja | media | alta>
coste_computo: <p. ej. "CPU, ~2 h" | "GPU, ~1 día">
coste_tiempo: <tiempo del investigador, p. ej. "medio día">
coste_base: <medido: E-9001 tardó 40 min con N=12 | estimado: …>
evaluado: <AAAA-MM-DD>
```

y una sección `## Evaluación` con el razonamiento: qué desbloquea si sale (casilla, hueco,
otras hipótesis que dependen de ella), qué cuesta y en qué se basa, y si hay una versión más
barata que responda lo mismo (enlaza con la escalera de simplificación existente).

### Scripts nuevos

- `scripts/frontier/frontier.py` — `build | check | stale | cell <id>` sobre un proyecto.
  `cell` imprime el contexto de una casilla (ejes, papers, marca, razón, hipótesis que la
  atacan) en JSON: es lo que la app y las skills usan para sembrar un chat o un ciclo.
- `scripts/estimate/hypothesis_cost.py` — bases medidas para estimar: duración y recursos de
  los experimentos y runs ya hechos en el vault (trace_index, logs), agrupados por proyecto y
  tamaño; y el presupuesto del hub. Nunca inventa: sin datos dice «sin base medida».
- `scripts/ledger/similar_work.py` — dado un plan (hipótesis, casillas, métrica, palabras
  clave), lista el trabajo previo del vault que se solapa: experimentos (estado, veredicto),
  hipótesis en las mismas casillas, descartes del digest. `preregister-experiment` lo
  ejecuta antes de diseñar y debe reutilizar (`collateral_evidence`) o justificar.
- `scripts/ledger/next_steps.py` — lo pendiente que el progreso ha dejado atrás: experimentos
  confirmatorios completados sin adjudicar, campos del hub en `<value>`, hipótesis sin
  evaluación, frontera obsoleta (papers sin extraer, casillas marcadas que un paper ya cubre),
  huecos sin hipótesis. Lo muestra la app en la portada del proyecto.

### Skills

- **`kairo:frontier-map`** (nueva). Fase 1: propone la taxonomía a partir de los papers y del
  Estado del arte y **para** hasta que el investigador la apruebe. Fase 2: un agente
  `frontier-extractor` (solo Read/Grep/Glob) por lote de papers sin extracción vigente;
  guarda las extracciones; construye `mapa.yaml`, marca cada casilla vacía con razón, escribe
  huecos con `barato` y `encaje`; `frontier.py build`; comprueba 5 casillas al azar con
  `paper-reader`; commit en el vault. Modo `actualizar`: solo papers nuevos y recálculo.
- **`kairo:explain`** (nueva). Explica uno o varios papers, una casilla (`M1:…`) o el propio
  proyecto. Un agente `explainer` aislado escribe el borrador; `check_review.py` comprueba
  cada cita y localizador. Secciones: resumen en una página, esquema conceptual (mermaid),
  cómo funciona el método (diagrama de flujo mermaid), ejemplo trabajado, comparación (si hay
  varios), preguntas para comprobar que se ha entendido (con respuesta plegada), glosario,
  cómo encaja en el proyecto (casillas, huecos, hipótesis). Guarda en `Informes/`.
- **`hypothesis-cycle`**: (a) con frontera, los candidatos derivados de huecos salen de
  `huecos` y de casillas `hueco`; una casilla `no_buscado` lleva primero a una búsqueda o se
  marca `importante`; `sin_sentido` nunca; (b) nuevo paso tras crear la nota: **¿merece la
  pena?** — rellena los campos y `## Evaluación` con `hypothesis_cost.py` como base; un `no`
  se dice al investigador y la nota queda `propuesta` (no se descarta sola).
- **`create-project`**: si existe la frontera, las hipótesis semilla salen de ella.
- **`preregister-experiment`**: paso previo `similar_work.py`; sección `## Trabajo previo`.
- **`update-confidence`** y **`run-experiment`**: tras un cambio de estado o un resultado,
  `frontier.py build` si existe la frontera (la capa «nuestro» cambia).

### App

- **Pestaña «Frontera»** del proyecto: lee `GET /projects/:id/frontier` (`mapa.json`).
  Matrices como mapa de color; al pulsar una casilla, un panel con quién la cubre o por qué
  está vacía, nuestras hipótesis en ella y **acciones**:
  - «Preguntar sobre esta casilla» → conversación del chat del proyecto sembrada con el
    contexto de `frontier.py cell` (mismo job `project_chat`).
  - Preguntas rápidas según la marca: `no_buscado` → «¿Qué habría que buscar?» y
    «Buscar en la literatura»; `hueco` → «¿Es un hueco real?» y «Proponer hipótesis desde
    aquí» (ciclo con `gap`); cubierta → «Explícame estos papers» (job `explain`).
  - Frontera, huecos (★ y encaje), ideas, posicionamiento y papers, como en el prototipo
    `mwis-pulser/docs/frontera/mapa.html`.
  - Botones «Crear el mapa» / «Actualizar» (job `frontier_map`), con aviso si está obsoleto.
- **Explicaciones**: job `explain` con selector de papers (o casilla, o proyecto); render de
  mermaid en la app.
- **Hipótesis**: muestra `vale_la_pena`, importancia, dificultad y coste con su base.
- **Portada del proyecto**: «Siguientes pasos» de `next_steps.py`.

## Fuera de alcance

Cambiar la búsqueda bibliográfica; ejecutar en hardware; migrar automáticamente mapas
hechos a mano (el de PROJ-002 se importará una vez, a mano, desde `mwis-pulser`).
