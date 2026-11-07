# Repositorio vinculado: un proyecto, un repositorio, y Kairo trabaja sobre él

Fecha: 2026-11-03. Estado: borrador para aprobación.
Afecta a dos repositorios: el plugin (`kairo-plugin`, público) y la app (`vault-backend`, privada).
Este documento es público: los ejemplos son inventados (`acme/solver`, `H-9001`, `E-9002`).

## Por qué

Un proyecto aplicado puede tener su trabajo real en un repositorio compartido de GitHub mientras
Kairo mira otro sitio o solo mira el vault. Cuando pasa, Kairo razona sobre un estado viejo:

- propone como siguiente paso algo que ya está hecho o en marcha en una rama;
- el hub sigue diciendo que no existe código que ya está fusionado;
- los PRs del equipo no dejan rastro en Kairo, y nadie puede leer de un vistazo qué se subió y por qué.

La idea: **cada proyecto aplicado tiene un único repositorio vinculado, y ese repositorio es la
referencia del progreso del código.** Kairo se pone al día con él antes de trabajar, lleva un
historial explicado de sus PRs, y puede escribir código en él hasta justo antes de subirlo.

## Objetivos

1. Vincular un repositorio existente a un proyecto existente desde la interfaz.
2. Lista histórica en Kairo: una entrada por PR fusionado, con una descripción breve de qué se subió
   y por qué, incluidos los PRs anteriores a la vinculación.
3. Todo trabajo de Kairo sobre el proyecto parte del estado del repositorio, incluido el trabajo en
   curso que aún no está en GitHub.
4. Kairo puede escribir en el repositorio: rama propia, commits, tests. Se para antes de subir. Tras
   la aprobación del investigador, sube la rama y abre el PR.
5. Kairo conserva la capacidad de escribir scripts independientes para explorar.

## Fuera de alcance

- Más de un repositorio por proyecto.
- Sincronización en segundo plano o webhooks: GitHub no alcanza al PC del investigador.
- Fusionar PRs, comentar PRs ajenos, crear issues o tocar ramas ajenas.
- Hosts distintos de GitHub.
- Copiar el vault al repositorio, o dar al repositorio la lista histórica.

## Lo que sería un fracaso aunque funcione

- Que contenido del vault (hipótesis, resultados, texto de papers) acabe en GitHub.
- Que `main` cambie sin que una persona lo fusione, o que algo se suba sin aprobación.
- Que Kairo toque la rama actual o los cambios sin commitear del investigador en su clon.
- Que un PR cambie el estado de una hipótesis o cuente como evidencia.
- Que una descripción presente como motivo del autor lo que es una inferencia del modelo.
- Que los agentes aislados (verificador, crítico, juez de novedad, cribador) reciban contexto del
  repositorio que rompa su aislamiento.

## Decidido por el investigador

- Un único repositorio por proyecto.
- La lista de PRs vive solo en Kairo (en el vault). Puede enlazar hipótesis y experimentos.
- Un PR entra en la lista al fusionarse. Los abiertos o cerrados sin fusionar no entran.
- La lista incluye los PRs ya fusionados, no solo los nuevos.
- La descripción la redacta el modelo, en español. Si el motivo no consta, la entrada lo dice y marca
  cualquier inferencia como tal.
- Kairo llega por su cuenta hasta justo antes de subir. Subir requiere aprobación, cada vez.
- La fusión con `main` la hace siempre una persona.
- Si una nota y el código no coinciden, Kairo lo avisa y no corrige la nota por su cuenta.
- Lo que no esté en GitHub se muestra como trabajo en curso, no como progreso.
- El agente que escribe código solo recibe del vault las notas que el investigador indica al pedir el
  cambio. Nunca papers ni el resto del vault.
- Un push no autorizado desde un script del agente se detecta, no se impide. No hay aislamiento real en
  esta versión. Se revisa si el repositorio se hace público o entra más gente.

## Diseño

### 1. Vincular

- `POST /projects/:id/repo/link {path}`, con un formulario en la pestaña Repositorio. Hoy la interfaz
  solo vincula al crear el proyecto.
- Comprueba lo que ya comprueba `repo_setup.py link`: es un repositorio git y está fuera del vault.
  Instala el guardia pre-push. No añade ficheros al repositorio.
- Hoy `repo_guard.py install` escribe `.git/hooks/pre-push` directamente. Si ya hay otro hook, o
  `core.hooksPath` está definido (git ignoraría el guardia sin avisar), vincular se rechaza y explica por
  qué. `guardInstalled` pasa a comprobar la ruta de hooks efectiva.
- Registra en el hub `code_repo`, y lee `code_remote` del `origin` y `code_visibility` de `gh`.
- Si ya había otro repositorio, va a un campo nuevo `code_repo_archivo: [ruta]`. Solo cuenta como
  referencia histórica: no se sincroniza ni se escribe en él. `trace_code.py` lo lee además del
  actual, para que los experimentos antiguos conserven su traza.
- Es una acción con aprobación (`needsApproval`), con una entrada propia en `automation.ts`.
- Devuelve el escaneo del guardia sobre `HEAD` como información. Un hallazgo no impide vincular, porque
  el guardia solo actúa sobre lo que cambian los commits que se suben.

### 2. Sincronizar

- Script nuevo `scripts/code_repo/repo_sync.py` (solo biblioteca estándar, llama a `git` y `gh`):
  - `git fetch origin --prune`, que no toca la copia de trabajo;
  - `gh pr list --state all` con número, título, autor, fechas, estado, rama, cuerpo y commit de fusión;
  - estado local: rama actual, ramas sin subir, ficheros modificados o sin seguimiento (solo nombres).
- Escribe `Projects/<slug>/Repositorio/estado.json`, con la fecha de sincronización.
- Se ejecuta en dos momentos: al empezar cada trabajo de un proyecto con repositorio y con el botón
  «Actualizar». Lo lanza el backend como código normal, no un agente, porque los agentes siguen sin
  poder usar `gh`.
- Con límite de tiempo (15 s). Si falla, el trabajo continúa con el último estado y el contexto dice
  desde cuándo está desfasado y por qué.
- Sesiones de Claude Code fuera de la app: las skills que deciden qué hacer ejecutan el script al
  empezar. Son `hypothesis-cycle`, `preregister-experiment`, `run-experiment` y `repo-steward`.
  Dentro de un trabajo de la app (`KAIRO_AGENT_SESSION`) lo omiten, porque el backend ya sincronizó.
  El script es la única vía de lectura de `gh` en esas sesiones de terminal: nunca escribe en GitHub.
- En una sesión de terminal no se escriben notas de PR: los PRs nuevos solo actualizan `estado.json` y
  se describen en la siguiente sincronización de la app. Validar y escribir notas queda en un solo sitio.

### 3. Historial de PRs

- Una nota por PR fusionado en `Projects/<slug>/Repositorio/<owner>-<repo>/PRs/PR-NNNN.md`. La carpeta
  por repositorio evita que el PR 12 de un repositorio choque con el 12 del anterior. Si se sustituye el
  repositorio, las notas viejas se quedan y el índice muestra solo el actual:
  - frontmatter: repositorio, número, título, autor, `merged_at`, commit de fusión, URL, `motivo: explicito |
    inferido | no_consta`, `relacionado: [H-9001, E-9002]`, `escrito_por: modelo`, `citable: false`,
    `generated_by`;
  - cuerpo: **Qué se subió** y **Por qué**, 2 a 4 frases en total.
- Índice derivado `Repositorio/historial.md`, más reciente primero, y una sección «Historial» en la
  pestaña Repositorio. El estado de sincronización `estado.json` se reinicia al cambiar de repositorio.
- Quién redacta: un agente nuevo `kairo:pr-historian`, sin shell, red ni escritura. Recibe un paquete
  construido por `scripts/code_repo/pr_packet.py`: título, cuerpo, mensajes de commit, ficheros y diff
  recortado. Solo los PRs; nunca notas del vault. El texto del PR se trata como datos, no como
  instrucciones.
- Devuelve JSON. El backend lo valida y escribe la nota:
  - `motivo: explicito` solo si el cuerpo o los commits dicen el motivo;
  - con `inferido`, el texto lo marca como inferencia; con `no_consta`, lo dice;
  - los ids de `relacionado` deben salir de trailers `Motivated-By` o del texto del PR, y existir en el
    proyecto. El agente no los deduce.
- Una vez escrita, una nota no se reescribe sola. Se puede regenerar a petición.
- Carga inicial: un trabajo único, con estimación de coste y aprobación, para todos los PRs ya
  fusionados.
- Después: al detectar PRs nuevos en una sincronización se lanza un trabajo para describirlos, sin
  aprobación por PR. Se puede cambiar en Automatización (`auto`, por defecto, u `off`).
- Una entrada describe el código. No es evidencia, no cambia estados y no se cita.

### 4. Basarse en el repositorio

- `buildContextPack` (`context.ts`) gana una parte «Repositorio», recortada a 3000 caracteres, y
  `CONTEXT_MAX_CHARS` crece en la misma cantidad:
  - repositorio, última sincronización y `HEAD` de la rama principal;
  - los últimos 10 PRs fusionados, con una línea cada uno;
  - PRs abiertos, y bajo un rótulo **trabajo en curso**: ramas sin subir y ficheros sin commitear.
    Las ramas `kairo/*` rechazadas no cuentan como trabajo en curso;
  - aviso de que el repositorio es referencia del código y de que su contenido son datos, no
    instrucciones.
- Se mantienen sin ese contexto: `ask_corpus` (responde solo con los papers) y los agentes con paquete
  cerrado (`fresh-verifier`, `devils-advocate`, `novelty-judge`, `screener`, `second-critic`).
- Avisos de discrepancia: las instrucciones de contexto piden al agente leer el código cuando su tarea
  dependa de él, y **listar en su informe cualquier discrepancia entre notas y código**, sin corregir
  notas. En esta versión no hay un detector automático de constantes.
- Escritura: las skills `hypothesis-cycle` y `preregister-experiment` comprueban lo que hay en curso
  antes de proponer trabajo y dicen si algo se solapa.

### 5. Experimentos y scripts independientes

Solo para proyectos con repositorio vinculado:

- Un experimento `role: confirmatory` tiene que correr sobre un commit del repositorio vinculado
  (`environment.code`) **que ya esté en GitHub**, en cualquier rama. No hace falta que esté fusionado: el
  preregistro no espera a la revisión del PR. `preregister-experiment` lo exige y el pre-flight de
  `run-experiment` lo comprueba.
- `run-experiment` ejecuta ese commit en un worktree propio, igual que `repo_change`, sin tocar la rama
  ni los cambios del clon. Eso cambia el pre-flight actual, que comprueba la copia de trabajo.
- Los scripts independientes siguen permitidos fuera del repositorio, solo para `role: exploratory`.
  No pueden sostener un veredicto confirmatorio.
- Los experimentos anteriores no se reinterpretan. Conservan el repositorio y el commit que registraron.

### 6. Escribir en el repositorio

Trabajo nuevo `repo_change`, `POST /projects/:id/repo/change {tarea, notas?, motivated_by?, base?}`, con
un formulario «Pedir un cambio». Solo lo lanza el investigador: los agentes no pueden llamar al backend,
a propósito, así que una skill que necesite código lo recomienda y no lo crea.

1. **Preparar (código normal).** Sincroniza y crea un worktree en una carpeta fuera del clon y fuera
   del vault, con una rama nueva `kairo/<tema>` desde `origin/<rama principal>` o desde `base`. Nunca
   hace `checkout` en el clon del investigador.
2. **Trabajar (agente).** Edita solo dentro del worktree. Hoy Read, Grep, Glob, Edit y Write están
   preaprobados en `BASE_ALLOWED_TOOLS`, así que `canUseTool` no los ve: este trabajo los saca de esa
   lista para que la ruta se compruebe de verdad, y solo admite rutas dentro del worktree. `runAgent` hoy
   usa siempre el vault como `cwd` con `settingSources: ["project","local"]`: este trabajo corre con `cwd`
   en el worktree y **sin** cargar la configuración del repositorio (`.claude/`, `CLAUDE.md`, `.mcp.json`),
   que son datos de un repositorio compartido, no instrucciones. Usa las convenciones del repositorio,
   ejecuta los tests y hace commits con trailer `Motivated-By` si procede y atribución. La lista de
   comandos prohibidos no cambia: `git push` y `gh` siguen vedados al agente. Redacta título y descripción
   del PR.

**Lo que recibe del vault.** Solo las notas de `notas`, que el investigador elige en el formulario. El
backend las lee y las pone en el prompt, marcadas como referencia que no se copia al repositorio, así que
el agente no necesita leer el vault:

- `notas` admite notas del proyecto. Rechaza `Papers/` y notas de otro proyecto. Sin `notas`, el agente no
  recibe nada del vault;
- este trabajo no usa `buildContextPack`: solo recibe la parte «Repositorio» de la sección 4;
- no carga los servidores MCP del vault (la búsqueda semántica lo recorrería entero) ni los agentes y
  skills del plugin;
- el `cwd` deja de ser el vault, y las reglas de ruta rechazan las herramientas de ficheros fuera del
  worktree. La lista de comandos prohibidos añade cualquier comando que nombre la ruta del vault.

**Límite conocido:** un script que el agente escriba y ejecute puede leer el vault sin nombrarlo en el
comando, como puede hacer push. Es el mismo hueco que describe «Comprobaciones tras el agente», y esas
comprobaciones no detectan una lectura. Por eso la tarjeta sigue siendo parte de la protección.

Pasos siguientes:

3. **Pararse.** El trabajo queda en `awaiting_approval` (`repo_publish`). La tarjeta muestra commits,
   diff, resultado de tests, escaneo del guardia sobre la rama, y título y descripción del PR, editables.
   También permite «Pedir correcciones»: el agente sigue en el mismo worktree y vuelve a pararse aquí.
4. **Subir (código normal, tras aprobar).** Antes de subir, el backend ejecuta él mismo el escaneo del
   guardia, sin depender del hook, y comprueba las condiciones de la sección «Comprobaciones tras el
   agente». Después `git push -u origin kairo/<tema>` por el guardia y `gh pr create`. La descripción
   incluye una línea que dice que el cambio lo ha preparado Kairo. Si algo falla, no se sube nada y la
   tarjeta lo explica.
5. **Rechazar.** La rama queda en local con el worktree, y la tarjeta permite borrarlos.
6. **Después del PR.** Si el equipo pide cambios, un `repo_change` nuevo con `rama` añade commits a la rama
   `kairo/*` existente y pasa por la misma aprobación. Solo ramas `kairo/*`.

Reglas:

- Un `repo_change` activo a la vez por proyecto. Deja de estar activo al subirse o al rechazarse.
- El cambio no se fusiona nunca desde Kairo; entra en el historial cuando una persona lo fusione.
- Nada del vault en commits ni en PRs, salvo ids (`H-9001`). El guardia solo detecta texto copiado, no
  paráfrasis, así que **la revisión de la tarjeta es parte de la protección**, y el prompt del agente lo
  prohíbe de forma explícita.
- `repo-steward` mantiene «nunca hace push»: sigue siendo cierto para los agentes. Se actualiza la
  explicación para incluir este flujo.
- Entorno de Python: `code_env` opcional en el hub. Si no está, se busca `venv/` o `.venv/` en el clon.
  Si no hay ninguno, se dice que los tests no se pudieron ejecutar.

### Comprobaciones tras el agente

El agente tiene un shell para ejecutar tests, y la lista de comandos prohibidos solo ve el texto del
comando: un script suyo podría llamar a `git push` sin que se detecte. Por eso, además de esa lista, el
backend comprueba como código normal, antes de la tarjeta y otra vez antes de subir:

- que la rama y el `HEAD` del clon, y su lista de ficheros modificados, son los mismos que antes de empezar;
- que `core.hooksPath` y el hook pre-push no han cambiado;
- que `origin` no tiene ninguna rama nueva ni movida respecto a lo anterior a empezar, salvo cambios ajenos
  a `kairo/*`, que se muestran como aviso;
- que los commits de la rama solo tocan ficheros del worktree y descienden de la base.

Si algo no coincide, el trabajo se detiene y lo muestra. Esto **detecta, no impide**: un push así se
vería después de hecho. El investigador lo ha aceptado para esta versión (ver «Decidido por el
investigador»).

Antes de subir, la tarjeta puede llevar horas esperando y el investigador sigue trabajando en su clon. Por
eso, en esa segunda comprobación, un cambio del clon o de una rama ajena solo es un aviso. Bloquean: el
hook o `core.hooksPath` cambiados, una rama `kairo/*` nueva o movida, una cabeza de la rama distinta de la
mostrada en la tarjeta, y el guardia. Decisión aprobada por el investigador.

### 7. Guardia

- El índice de huellas excluye `Projects/*/Repositorio/`. Esas notas derivan del propio repositorio y,
  si no, bloquearían pushes de los ficheros que describen.
- **Rama nueva:** hoy, en el primer push de una rama, `changed_files` revisa el árbol entero. Cada PR de
  Kairo es una rama nueva, así que cualquier coincidencia ya presente en `main` lo bloquearía todo. Pasa a
  comparar con `origin/<rama principal>`: lo que ya está ahí no se expone de nuevo. Si no hay rama
  principal en el remoto, sigue revisando el árbol entero.
- No se relaja nada más. Las coincidencias en ficheros que el push cambia siguen pasando por
  `repo_guard.py allow`, que ya rechaza sesiones de agente (`AGENT_MARKERS`).

## Pruebas

- Repositorios git inventados en carpetas temporales, con `gh` simulado. Sin red, sin notas reales del
  vault y sin escribir fuera de la carpeta temporal.
- Casos que deben quedar cubiertos:
  - vincular y sustituir un repositorio, con rechazo dentro del vault;
  - sincronizar sin red, con `gh` sin sesión y sin remoto;
  - carga inicial e incremental sin duplicar PRs, con cuerpo vacío y con `no_consta`;
  - ids inventados por el agente rechazados;
  - contexto presente en trabajos normales y ausente en los aislados;
  - Read/Grep/Glob/Edit/Write fuera del worktree rechazados; `git push` y `gh` del agente rechazados;
  - `notas` con un paper o con una nota de otro proyecto rechazadas; prompt sin contexto del vault cuando
    no hay `notas`; comando que nombra la ruta del vault rechazado;
  - la rama y los cambios sin commitear del clon intactos tras un `repo_change`;
  - sin aprobación no hay push; guardia que bloquea; rechazo.
- Comprobación final en la app con un vault de demostración, y después en el vault real mirando que
  la interfaz lo muestra.

## Decisiones de diseño a revisar

No las pidió el investigador; son criterio de quien redactó este documento:

- El guardia compara las ramas nuevas con `origin/<rama principal>` (sección 7).
- Un experimento confirmatorio exige un commit en GitHub, en cualquier rama (sección 5).
- `run-experiment` corre en un worktree propio (sección 5).
- Las skills recomiendan `repo_change`, pero no lo lanzan (sección 6).
- Las notas de PR van en carpetas por repositorio (sección 3).
- «Pedir correcciones» y commits posteriores en ramas `kairo/*` (sección 6).
- Las sesiones de terminal no escriben notas de PR (sección 2).
- Vincular se rechaza si ya hay otro hook o `core.hooksPath` (sección 1).
- El agente de `repo_change` recibe las notas en el prompt, sin leer el vault (sección 6).

## Fuera de este documento

Aplicarlo al primer proyecto real (vincular, carga inicial, poner al día su hub y sus notas) es una
tarea aparte, con notas solo en el vault.

## A criterio de quien lo construya

- Forma exacta de los esquemas JSON, nombres de campos internos y orden de implementación.
- Cómo recortar el diff del paquete y cuántos PRs describe cada agente.
- Diseño visual de las secciones nuevas, dentro del estilo de la pestaña actual.
- Ubicación concreta de los worktrees.
