# Estudios flexibles: preguntas que no son hipótesis, datos sensibles y papers de congreso

Fecha: 2026-11-18. Estado: borrador para aprobación.
Afecta a dos repositorios: el plugin (`kairo-plugin`, público) y la app (`vault-backend`, privada).
Este documento es público: los ejemplos son inventados (`Q-9001`, `M-9002`, `acme/scanner`).

## Por qué

Kairo lleva bien un proyecto que avanza por hipótesis falsables: propuesta → preregistro →
experimento → `apoyada` / `refutada`, y el manuscrito solo se redacta sobre lo que pasó esa
puerta. Un **estudio de medición** (cuánto ocurre algo en una población: prevalencias,
distribuciones, reutilización) no encaja:

- No contrasta una predicción; estima cantidades con su incertidumbre. Exigir `apoyada`, y con
  ella una replicación, no tiene sentido (volver a medir la misma población no la hace más fiable).
- `assemble-manuscript` se niega sin hipótesis `apoyada` + preregistro `completo`.
- Los datos pueden contener **material sensible de terceros** (credenciales, claves, datos
  personales) que ningún modelo debe leer y que no puede acabar en el vault ni en las trazas.
- La recolección dura días (APIs con límites), no cabe en un trabajo de agente.
- Hay **anotación manual** de muestras y **validación de clasificadores** contra ella.
- Hay **avisos a afectados** antes de publicar, con embargo.
- El paper va en inglés, en LaTeX, con la plantilla y el límite de páginas del congreso.

## Objetivos

1. Una unidad de estudio genérica: **pregunta** (`Q-XXXX`) con un **diseño** declarado. Cada
   diseño define sus estados, sus transiciones, su plan congelado y su puerta de manuscrito en
   un registro (`config/designs.toml`). Añadir un diseño nuevo es una entrada en el registro y
   una plantilla, sin código nuevo, mientras use las comprobaciones de puerta ya existentes.
2. El primer diseño completo: **`medicion`**.
3. Protección de datos sensibles: ningún agente lee un dataset marcado como sensible, y las
   trazas y registros de ejecución se guardan redactados.
4. Datasets grandes (hasta 1 000 000 de ficheros) con la huella calculada en segundo plano.
5. **Recolecciones**: ejecuciones largas de un script del repositorio, sin modelo, que
   sobreviven a reinicios del servidor y se reanudan.
6. **Anotación** de muestras en la interfaz, acuerdo entre anotadores y validación de
   clasificadores.
7. **Registro de avisos** (divulgación responsable) con fechas de embargo.
8. **Manuscrito por diseño y por congreso**: secciones del diseño, idioma (`en`), exportación a
   LaTeX con la clase del congreso y comprobación del límite de páginas.

## No objetivos

- Reescribir el flujo de hipótesis. Las hipótesis siguen como están; `hipotesis` no se migra al
  registro de diseños (queda documentado como el diseño implícito).
- Que Kairo haga peticiones a APIs de terceros por el investigador: el script de recolección es
  del investigador, en su repositorio; Kairo lo lanza, lo vigila y registra qué commit corrió.
- Enviar avisos (correo, issues): Kairo solo los registra.
- Más fuentes de literatura (DBLP, IACR ePrint): fuera de este plan.

## Anti-objetivos (sería un fracaso aunque funcione)

- Que un secreto encontrado llegue al contexto de un modelo, al vault o a una traza.
- Que la puerta de `medicion` sea más laxa que la de hipótesis en lo que comparten: plan
  congelado antes de mirar los datos, cálculo reproducible por script, verificador independiente.
- Que el registro de avisos (nombres de afectados bajo embargo) llegue a un modelo.

## Enfoques considerados

| Enfoque | Qué cambia | Coste | Veredicto |
|---|---|---|---|
| A. Disfrazar mediciones de hipótesis con umbral | nada en el código | cero | Descartado: falsea el método, exige replicación absurda. |
| B. Un tipo de proyecto `medicion` cableado | nuevos estados y puerta escritos en código | medio | Descartado: el siguiente diseño (revisión sistemática, cualitativo, benchmark) obliga a repetirlo. |
| **C. Pregunta + registro de diseños** | nodo `Q`, estados y puerta declarados en `designs.toml`, comprobaciones de puerta como funciones con nombre | medio-alto | **Elegido**: lo que pide el investigador («total flexibilidad»); `medicion` es el primer diseño. |

Elección del implementador del plan (el investigador delegó el cómo): C.

## Diseño

### Nodo pregunta y registro de diseños (plugin)

- Notas `Projects/<slug>/Preguntas/Q-XXXX.md` (plantilla `templates/question-template.md`):
  `id`, `design`, `status`, `linea_publicacion`, `linked_plan`, `history`, secciones
  `## Pregunta`, `## Población y marco`, `## Estimandos`, `## Resultado`.
- Plan congelado `Projects/<slug>/Mediciones/M-XXXX.md`
  (`templates/measurement-plan-template.md`): población y marco muestral, criterios de
  inclusión/exclusión, estimandos (tipo `proporcion` | `conteo` | `mediana`, columna, filtro,
  agrupación para el intervalo), clasificadores (nombre, muestra de validación, cota mínima de
  precisión y exhaustividad), método del intervalo, plan de ética. `frozen_at`, `frozen_commit`.
- `config/designs.toml`, por diseño: `prefix`, `folder`, `statuses`, `terminal`, `transitions`
  (pares desde→hasta), `researcher_only` (transiciones que solo hace una persona), `publishable`
  (estado que exige el manuscrito), `gate` (lista de comprobaciones con nombre), `plan_prefix`,
  `plan_folder`, `plan_template`, `manuscript_sections`.
- `medicion`: `abierta → plan_congelado → en_recoleccion → estimada | sin_respuesta`;
  `abierta | plan_congelado → descartada` (solo el investigador). Puerta:
  `plan_frozen`, `estimates_reproducible`, `classifiers_validated`, `verifier_no_errors`,
  `avisos_cerrados`.
- Comprobaciones de puerta (código, `scripts/ledger/study_gates.py`): cada una es una función
  `check(vault, node_path, fm) -> list[str]` (lista de lo que falta). Un diseño solo puede nombrar
  comprobaciones que existen; `designs.py check` lo valida.
- `study_status.py` es el único escritor de `status` de una pregunta, como
  `hypothesis_status.py` para las hipótesis; lee las transiciones del registro.
- `build_graph.py` y `manuscript.py` aceptan `Q-` además de `C-` y `H-`.

### Estimación y validación (plugin, solo biblioteca estándar)

- `scripts/analysis/estimate.py`: lee el plan congelado y un fichero de unidades (CSV o JSONL,
  registrado como dataset) y escribe los estimandos con su intervalo: proporción con Wilson;
  si el estimando declara `cluster`, bootstrap por conglomerados con semilla fija; mediana con
  bootstrap. Comprueba que la huella del fichero coincide con la registrada. Sale en JSON.
- `scripts/analysis/agreement.py`: kappa de Cohen entre dos anotadores; precisión y exhaustividad
  de un clasificador frente a las etiquetas de referencia, con intervalo de Wilson.

### Datos sensibles

- El registro de datos (`Datos/registro.json`) gana `sensitive: true|false`.
- `scripts/security/data_guard.py`: `decide(event, vault)` bloquea `Read`, `Grep`, `Glob`,
  `Bash`, `PowerShell` y las búsquedas de Smart Connections que nombren una ruta dentro de un
  dataset sensible de cualquier proyecto, o `Projects/*/Avisos/`. Lo llama `kairo_hook.py
  pre-tool` tras `send_guard`. Falla cerrado. Límite declarado: un comando de shell que llegue a
  la ruta de forma indirecta no se detecta; por eso la redacción en origen es obligatoria.
- `scripts/security/redact.py`: sustituye material privado por
  `[REDACTADO <tipo> sha256:<12 hex>]`. Reutiliza los patrones de `check_bundle.py`. Se usa como
  biblioteca (desde el script de recolección del investigador) y como CLI por stdin/stdout.
- La app redacta cada traza (`traces.ts append`) y cada registro de recolección pasándolos por
  `redact.py`; si la redacción falla, la traza se guarda sin `messages` ni `outcomeText`, con
  una nota.

### Datos grandes y recolecciones (app)

- `MAX_FILES = 1_000_000`; la huella se calcula en streaming en un trabajo sin modelo
  `dataset_fingerprint` con progreso.
- `src/collections.ts`: una recolección es `{id, projectId, argv, commit, cwd, outDir, retries,
  state}`; se lanza desacoplada (`spawn` con `detached`), con su estado en
  `~/.kairo/collections/<id>/state.json` y su registro en `…/log.txt` (fuera del vault). Al
  arrancar el servidor se concilia el estado (pid vivo o no). Si sale con código ≠ 0 y quedan
  reintentos, se relanza con espera creciente; el script debe reanudar desde `outDir`. Exige un
  árbol limpio y apunta el commit. Cada inicio y fin queda en el cuaderno.
- Panel «Recolecciones» en el grupo Código: lanzar, ver estado y las últimas líneas del
  registro, ya redactadas, detener.

### Anotación (app + plugin)

- `Projects/<slug>/Datos/anotacion/<muestra>/`: `muestra.jsonl` (id + campos a mostrar, que el
  script del investigador ya redactó; la importación pasa `redact.py --check` y se niega si
  encuentra algo), `codebook.json` (etiquetas, valores, definición), `etiquetas-<anotador>.jsonl`,
  `referencia.jsonl` (acuerdo o adjudicación), `validacion-<clasificador>.json`.
- Panel «Anotar»: un elemento cada vez, atajos de teclado, progreso; después «Acuerdo» y
  «Validar clasificador», que llaman a `agreement.py`.

### Avisos (app)

- `Projects/<slug>/Avisos/registro.json`: `{id, destinatario, canal, enviado, embargo_hasta,
  respuesta, estado: pendiente|enviado|respondido|resuelto|sin_respuesta}`. Lo protege
  `data_guard`; a un modelo solo le llegan los recuentos por estado.
- Panel «Avisos»; las fechas de embargo aparecen en el Horario.

### Manuscrito por diseño y congreso (plugin)

- `config/venues.toml`: `acm-sigconf` (clase `acmart`, opción `sigconf`, 12 páginas sin
  referencias ni apéndices), `usenix` (13 páginas), `generic`.
- `manuscript.py init --design medicion --lang en --venue acm-sigconf`: secciones del diseño
  (Introduction, Background, Methodology, Ethics, Results, Discussion, Related Work,
  Open Science); `lang` y `venue` en la cabecera del esquema.
- `manuscript.py export --format latex`: `Manuscritos/latex-<thread>/main.tex` vía pandoc
  (si falta, se niega con la orden de instalación), `refs.bib` con `export_bib.py`; si hay
  `latexmk`, compila y cuenta las páginas del cuerpo contra el límite.
- `assemble-manuscript` redacta en `lang`.

### Skills y trabajos

- Skill `kairo:plan-study`: crea la pregunta, redacta el plan con el investigador, lo critica
  (abogado del diablo sobre el plan) y lo congela.
- Skill `kairo:estimate`: corre `estimate.py`, escribe `## Resultado`, construye el paquete del
  verificador independiente y propone el cambio de estado a `study_status.py`.
- App: trabajos `plan_study` (opus) y `estimate_study` (sonnet) en `models.toml`; vista
  «Preguntas» con su lista de comprobación de la puerta.
- Plantilla de proyecto `medicion`: `type: ciencia`, `code_repo` obligatorio,
  `seed_hypotheses: false`.

## Lo que queda al implementador

Nombres internos no fijados aquí, maquetación de los paneles con los tokens del Horario, mensajes
de error (en español, con la acción que lo arregla).
