# Estudios flexibles: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que Kairo lleve estudios que no avanzan por hipótesis, empezando por la medición. Eso incluye datos sensibles, recolecciones de días, anotación manual, avisos con embargo y un paper en LaTeX para un congreso.

**Architecture:**
- Un nodo nuevo, la pregunta `Q-XXXX`, con un diseño declarado en `config/designs.toml`. El diseño fija los estados, las transiciones y una puerta de manuscrito hecha de comprobaciones con nombre.
- El plugin aporta los escritores de estado, los cálculos (estimación, acuerdo), la redacción y el guardia de datos.
- La app aporta las recolecciones desacopladas, la anotación, los avisos y las vistas.
- Siete fases. Cada una se entrega sola y deja el sistema funcionando; la 1 va primero porque protege los datos desde el primer día.

**Tech Stack:**
- Plugin: Python 3, solo biblioteca estándar, pytest.
- App: Node/Express + TypeScript (tsx), vitest, eslint, JavaScript sin framework en `public/`.
- Pandoc y latexmk son opcionales, solo para exportar.

**Spec:** `docs/superpowers/specs/2026-11-18-estudios-flexibles-design.md`

## Global Constraints

- **El plugin es público:** ni contenido de investigación ni rutas locales. Tests y ejemplos con ids inventados (`Q-9001`, `M-9002`, `PROJ-901`) y textos inventados.
- **Claves de prueba:** las claves de los tests se generan en el propio test (cadenas con forma de clave, nunca claves reales) y se escriben solo en `tmp_path` / directorios temporales. Ninguna entra en el repositorio: el guardia pre-push las rechazaría, y con razón.
- **Python del plugin:** solo biblioteca estándar. Códigos de salida `0` ok · `3` rechazado · `1` error. JSON por stdout.
- **Commits:** nunca se saltan los hooks. El de la app ejecuta `npm run check` (typecheck, lint y suite completa). Si hay poca memoria, la suite se corre en dos mitades en primer plano antes del commit.
- **Copia de la interfaz:** en español. Los errores dicen la acción que lo arregla. Los estilos usan los tokens del Horario (`--ink`, `--line`, `--surface`, `--s1`…`--s7`).
- **Modelos:**
  - Todo trabajo nuevo con modelo se declara en `config/models.toml` y en `JOB_TASK` (`src/models.ts`), con `PENDING_TASKS` mientras el plugin instalado no lo tenga.
  - Los trabajos sin modelo llevan `null`.
- **Datos sensibles:**
  - Nada de un dataset `sensitive: true` ni de `Projects/*/Avisos/` llega a un modelo, al vault (salvo su registro) ni a una traza sin redactar.
  - Los registros de recolección viven fuera del vault, en `~/.kairo/collections/`.
- **Formato de redacción:** `[REDACTADO <tipo> sha256:<12 hex>]`. El sha256 es del material original en UTF-8.
- **Estados de `medicion`, exactos:** `abierta`, `plan_congelado`, `en_recoleccion`, `estimada`, `sin_respuesta`, `descartada`.

## Review Focus

1. **Un comando de shell que llega a un dataset sensible por una ruta relativa, con barras invertidas o en otra capitalización.** El guardia debe bloquearlo igual: rutas normalizadas con `resolve()` y comparación sin distinguir mayúsculas en Windows. Lo cubre el test `test_shell_relative_and_case` (Task 1.2).
2. **El servidor se reinicia a mitad de una recolección.** Al volver, la recolección no se pierde: si el proceso sigue vivo se ve «en marcha»; si murió sin código de salida, «interrumpida» y se puede reanudar. Lo cubre el test `concilia una recolección cuyo proceso murió con el servidor` (Task 3.2).
3. **Un fichero de unidades que cambió después de congelar el plan.** `estimate.py` se niega (exit 3) y dice qué huella esperaba. Lo cubre el test `test_refuses_changed_units` (Task 2.2).
4. **Una muestra de anotación con un secreto sin redactar.** La importación se niega y no escribe nada en el vault. Lo cubre el test `importar una muestra con material privado se niega y no escribe` (Task 4.1).
5. **Un diseño en `designs.toml` que nombra una comprobación de puerta inexistente o una transición a un estado no declarado.** `designs.py check` falla (exit 3) en vez de dejar pasar la pregunta. Lo cubre el test `test_unknown_gate_and_state_rejected` (Task 2.1).

---

## Fase 1 — Protección de datos sensibles

### Task 1.1: Redacción (`redact.py`)

**Files:**
- Create: `scripts/security/redact.py`
- Create: `scripts/security/test_redact.py`
- Modify: `scripts/security/check_bundle.py` (exponer los patrones como `PATTERNS: list[tuple[str, re.Pattern]]` si hoy son locales; sin cambiar su comportamiento)

**Interfaces:**
- Produces:
  - `redact_text(text: str) -> tuple[str, list[dict]]`. Devuelve el texto redactado y los hallazgos `{"type": str, "sha256": str}`.
  - `has_secret(text: str) -> bool`.
  - CLI:
    - `redact.py redact < in > out` (exit 0).
    - `redact.py check < in` (exit 3 si encuentra algo; JSON `{"findings": [...]}` sin el material).
- Tipos de patrón: `private_key` (bloques PEM, OpenSSH y PGP), `age_secret_key` (`AGE-SECRET-KEY-1` + bech32) y los de `check_bundle` que no son de fichero (`credential_url`, asignaciones de secreto).

- [ ] **Step 1: Write the failing tests**
  - `test_pem_block_replaced`: un bloque `-----BEGIN OPENSSH PRIVATE KEY-----…-----END…` generado en el test queda como `[REDACTADO private_key sha256:<12 hex>]`. El hex es igual a `sha256(bloque)[:12]`, y el resto del texto se conserva byte a byte.
  - `test_age_key_replaced`: una cadena `AGE-SECRET-KEY-1` + 58 caracteres bech32 da el tipo `age_secret_key`.
  - `test_public_material_untouched`: una línea `age1…` (clave pública) y un bloque `BEGIN PUBLIC KEY` no se tocan.
  - `test_check_exit_codes`: `check` con texto limpio da exit 0; con una clave, exit 3, y la salida no contiene la clave.
  - `test_idempotent`: `redact_text(redact_text(x)[0])[0] == redact_text(x)[0]`.
- [ ] **Step 2:** `python -m pytest scripts/security/test_redact.py -q` → falla (módulo inexistente).
- [ ] **Step 3: Implement `redact.py`.** Importa los patrones de `check_bundle`. Recorre los patrones de más largo a más corto para que un bloque PEM no se trocee.
- [ ] **Step 4:** `python -m pytest scripts/security/test_redact.py scripts/security/test_check_bundle.py -q` → pasa.
- [ ] **Step 5: Commit** `redact.py: redacción de material privado con huella`.

### Task 1.2: Guardia de datos sensibles en el hook

**Files:**
- Create: `scripts/security/data_guard.py`
- Create: `scripts/security/test_data_guard.py`
- Modify: `scripts/hooks/kairo_hook.py` (`pre_tool`: tras `send_guard.decide`, llamar a `data_guard.decide`, con el mismo registro de eventos y el mismo fallo cerrado)
- Modify: `scripts/hooks/test_kairo_hook.py`

**Interfaces:**
- Consumes: el formato del registro `Projects/<slug>/Datos/registro.json` (lista de `{name, path, kind, sensitive?, …}`).
- Produces:
  - `sensitive_roots(vault: Path) -> list[Path]`: rutas resueltas de los datasets `sensitive: true` de todos los proyectos, más `Projects/*/Avisos`.
  - `decide(event: dict, vault: Path) -> str | None`: devuelve el motivo del bloqueo o `None`.

- [ ] **Step 1: Write the failing tests** (vault y dataset inventados en `tmp_path`):
  - `test_read_inside_sensitive_blocked`: `Read` de `<ds>/sub/f.txt` → motivo que nombra el dataset y no el contenido.
  - `test_read_non_sensitive_allowed`: el mismo dataset con `sensitive: false` → `None`.
  - `test_grep_and_glob_scopes`: `Grep` con `path` dentro de la ruta del dataset o por encima de ella → bloqueado (en cualquier `output_mode`). `Glob` igual.
  - `test_shell_relative_and_case`: un `Bash` con `cat ..\\DS\\f.txt` desde un `cwd` vecino, y otro con la ruta en otra capitalización, quedan bloqueados (en Windows la comparación ignora mayúsculas).
  - `test_avisos_blocked`: `Read` de `Projects/x/Avisos/registro.json` → bloqueado.
  - `test_smart_connections_blocked`: `mcp__smart-connections__get_note` con una ruta dentro del dataset sensible → bloqueado.
  - `test_unreadable_registry_fails_closed`: un `registro.json` corrupto → bloqueo con el motivo «registro de datos ilegible».
  - En `test_kairo_hook.py`: `test_pre_tool_calls_data_guard`, donde `pre_tool` devuelve 2 y el evento queda registrado como `data_guard blocked`.
- [ ] **Step 2:** `python -m pytest scripts/security/test_data_guard.py scripts/hooks/test_kairo_hook.py -q` → falla.
- [ ] **Step 3: Implement `data_guard.py` and wire it into `pre_tool`.** Para la shell, reutiliza la extracción de rutas de `send_guard` (`_shell_reach` o la función que extrae tokens de ruta) en vez de duplicarla. Guarda en caché `sensitive_roots` por `mtime` de cada registro.
- [ ] **Step 4:** Los mismos tests y `python -m pytest scripts/security scripts/hooks -q` → pasan.
- [ ] **Step 5: Commit** `data_guard: ningún agente lee datasets sensibles ni Avisos/`.

### Task 1.3: Dataset sensible y trazas redactadas (app)

**Files:**
- Create: `src/py.ts` (extraer `runPy` de `src/paperImport.ts` y `src/projectData.ts`)
- Create: `src/redact.ts`
- Modify: `src/paperImport.ts`, `src/projectData.ts`, `src/traces.ts`, `src/routesData.ts`, `public/dataPanel.js`
- Test: `test/redact-traces.test.ts`; modify `test/data-routes.test.ts`

**Interfaces:**
- Produces:
  - `runPy(config, script: string[], args: string[], opts?: { input?: string; timeoutMs?: number }) => Promise<{ code: number; out: string; err: string }>` en `src/py.ts`. Nunca lanza una excepción, ni siquiera ante EFTYPE.
  - `redactText(config, text: string) => Promise<{ ok: boolean; text: string }>` en `src/redact.ts`. Llama a `redact.py redact` por stdin.
  - `Dataset.sensitive: boolean`. Si falta en el registro, se lee como `false`.
  - `TraceStore.append(...)` pasa a ser `async` y devuelve `Promise<TraceEntry>`; hay que actualizar todos los que lo llaman.
- Ruta: `POST /projects/:id/data/datasets` acepta `sensitive` (booleano).

- [ ] **Step 1: Write the failing tests**
  - `la traza se guarda redactada`: con un `AgentRunResult` cuyo `outcomeText` y un mensaje llevan una clave falsa, el JSON escrito no contiene la clave y sí `[REDACTADO private_key`.
  - `si la redacción falla, la traza se guarda sin mensajes`: con `config.python` inexistente, el fichero tiene `messages: []`, `outcomeText: ""` y `redaction: "fallida"`.
  - `registrar un dataset sensible lo marca`: tras el POST con `sensitive: true`, `registro.json` lo tiene y el GET lo devuelve.
- [ ] **Step 2:** `npx vitest run test/redact-traces.test.ts test/data-routes.test.ts` → falla.
- [ ] **Step 3: Implement.**
  - `append` redacta `prompt`, `outcomeText` y `JSON.stringify(messages)` en una sola llamada (separados por un delimitador que no pueda aparecer) y vuelve a parsear.
  - Casilla «Datos sensibles (ningún modelo los lee)» en el formulario de dataset, con una etiqueta en la tabla.
- [ ] **Step 4:** Los tests de la Task, luego `npm run typecheck` y `npm run lint` → pasan.
- [ ] **Step 5: Commit** (el hook ejecuta `npm run check`) `Datos sensibles y trazas redactadas`.

---

## Fase 2 — Preguntas y diseños (plugin)

### Task 2.1: Registro de diseños y plantillas

**Files:**
- Create: `config/designs.toml`
- Create: `scripts/ledger/designs.py`
- Create: `scripts/ledger/test_designs.py`
- Create: `templates/question-template.md`
- Create: `templates/measurement-plan-template.md`
- Create: `scripts/ledger/study_gates.py` (solo el diccionario `GATES` con funciones vacías que devuelven `["sin implementar"]`; se rellenan en la Task 2.3)

**Interfaces:**
- Produces:
  - `load(plugin_root: Path) -> dict[str, Design]`, con `Design = TypedDict(prefix, folder, statuses, terminal, transitions: list[tuple[str,str]], researcher_only: list[tuple[str,str]], publishable, gate: list[str], plan_prefix, plan_folder, plan_template, manuscript_sections: list[dict])`.
  - `design_for_id(designs, nid) -> Design | None`: busca por prefijo.
  - CLI `designs.py check` (exit 3 con la lista de problemas) y `designs.py show` (JSON).
- Contenido de `medicion`, exacto:
  - `prefix="Q"`, `folder="Preguntas"`.
  - `statuses=["abierta","plan_congelado","en_recoleccion","estimada","sin_respuesta","descartada"]`.
  - `terminal=["estimada","sin_respuesta","descartada"]`.
  - `transitions=[["abierta","plan_congelado"],["plan_congelado","en_recoleccion"],["en_recoleccion","estimada"],["en_recoleccion","sin_respuesta"],["abierta","descartada"],["plan_congelado","descartada"]]`.
  - `researcher_only=[["abierta","descartada"],["plan_congelado","descartada"]]`.
  - `publishable="estimada"`.
  - `gate=["plan_frozen","estimates_reproducible","classifiers_validated","verifier_no_errors","avisos_cerrados"]`.
  - `plan_prefix="M"`, `plan_folder="Mediciones"`, `plan_template="templates/measurement-plan-template.md"`.
  - `manuscript_sections`: `introduction` (prosa), `background` (prosa), `methodology` (prosa), `ethics` (prosa), `results` (resultado), `discussion` (cierre), `related-work` (prosa), `open-science` (prosa).

- [ ] **Step 1: Write the failing tests**
  - `test_load_medicion`: carga el `designs.toml` real y comprueba `publishable == "estimada"` y que `("en_recoleccion","estimada")` está en `transitions`.
  - `test_unknown_gate_and_state_rejected`: un toml temporal con `gate=["no_existe"]` y una transición a `"fantasma"` → `check` da exit 3 y nombra ambos problemas.
  - `test_prefix_collision_rejected`: un diseño con `prefix="H"` o `"C"` → rechazado.
  - `test_templates_exist`: cada `plan_template` existe y su frontmatter tiene `frozen_at:` y `frozen_commit:`.
- [ ] **Step 2:** `python -m pytest scripts/ledger/test_designs.py -q` → falla.
- [ ] **Step 3: Implement.** Usa `tomllib`.
  - Plantilla de pregunta, secciones: `## Pregunta`, `## Población y marco`, `## Estimandos`, `## Resultado`, `## Historia`.
  - Plantilla de plan, secciones: `## Población y marco muestral`, `## Inclusión y exclusión`, `## Estimandos` (bloque YAML `estimands:` con `id`, `type: proporcion|conteo|mediana`, `column`, `filter: {columna: valor}`, `cluster`), `## Clasificadores` (bloque `classifiers:` con `name`, `sample`, `min_precision_lo`, `min_recall_lo`), `## Intervalos` (`ci: 0.95`, `bootstrap: 2000`, `seed`), `## Ética`.
- [ ] **Step 4:** Los mismos tests → pasan. Además, `python scripts/ledger/designs.py check` → exit 0.
- [ ] **Step 5: Commit** `designs.toml: registro de diseños de estudio; medicion`.

### Task 2.2: Estimación y acuerdo

**Files:**
- Create: `scripts/analysis/estimate.py`, `scripts/analysis/agreement.py`
- Create: `scripts/analysis/test_estimate.py`, `scripts/analysis/test_agreement.py`

**Interfaces:**
- Consumes: el bloque `estimands:` y `## Intervalos` del plan (Task 2.1), y el registro de datos (huella `sha256` de fichero, como `fingerprint` de la app).
- Produces:
  - `estimate.py --vault V --plan M-XXXX --units <dataset name>`. Sale `{"plan","units_sha256","estimates":[{"id","type","value","lo","hi","n","method"}]}`. Exit 3 si la huella no coincide.
  - `wilson(k:int, n:int, conf:float) -> tuple[float,float]`.
  - `agreement.py kappa --a A.jsonl --b B.jsonl --label L` → `{"kappa","n","observed","expected"}`.
  - `agreement.py validate --gold ref.jsonl --pred clf.jsonl --label L --positive V` → `{"precision","precision_lo","precision_hi","recall","recall_lo","recall_hi","n"}`.

- [ ] **Step 1: Write the failing tests**
  - `test_wilson_known_values`: `wilson(0,10,0.95)` da `lo == 0` y `hi ≈ 0.2775` (±1e-4); `wilson(5,10,0.95)` da `≈ (0.2366, 0.7634)`.
  - `test_proportion_with_filter`: en un CSV inventado de 8 filas, el estimando `proporcion` sobre `column: tiene_x` con `filter: {tipo: org}` da `value == 3/5`.
  - `test_cluster_bootstrap_deterministic`: el mismo `seed` da los mismos `lo`/`hi`, y con `cluster` el intervalo es más ancho o igual que sin él.
  - `test_median_bootstrap`: la mediana de `[1,2,3,4,100]` vale 3.
  - `test_refuses_changed_units`: si la huella del registro no coincide con la del fichero, exit 3 con `esperada` y `actual` en la salida.
  - `test_kappa_perfect_and_chance`: con etiquetas idénticas, kappa = 1; en un caso construido con acuerdo observado igual al esperado, kappa = 0.
  - `test_validate_precision_recall`: con TP=8, FP=2, FN=4 → precisión 0.8, exhaustividad 8/12, y las cotas iguales a `wilson`.
- [ ] **Step 2:** `python -m pytest scripts/analysis/test_estimate.py scripts/analysis/test_agreement.py -q` → falla.
- [ ] **Step 3: Implement.** El bootstrap usa `random.Random(seed)`. Por conglomerados: se remuestrean grupos completos y se recalcula la proporción.
- [ ] **Step 4:** Los mismos tests → pasan.
- [ ] **Step 5: Commit** `estimate.py y agreement.py: estimandos con intervalo, kappa y validación`.

### Task 2.3: Comprobaciones de puerta y escritor de estado

**Files:**
- Modify: `scripts/ledger/study_gates.py`
- Create: `scripts/ledger/study_status.py`, `scripts/ledger/test_study_status.py`
- Modify: `scripts/ledger/build_graph.py` (cargar `Q` de cada carpeta de diseño; estados válidos tomados del registro)
- Modify: `scripts/ledger/verifier_packet.py` (`find_note` para `Q-` vía `designs.design_for_id`; nuevo modo `--question Q-XXXX` con la pregunta, el plan congelado y la salida de `estimate.py` guardada en `## Resultado`)

**Interfaces:**
- Consumes: `designs.load`, `estimate.py`, `agreement.py validate`, `verification` (`vf.latest(..., "note")`, como las hipótesis).
- Produces:
  - `GATES: dict[str, Callable[[Path, Path, dict], list[str]]]` con:
    - `plan_frozen`: `linked_plan` existe y tiene `frozen_at` y `frozen_commit`.
    - `estimates_reproducible`: volver a correr `estimate.py` da exactamente el JSON guardado en el bloque `estimate-json` de `## Resultado`.
    - `classifiers_validated`: cada clasificador del plan tiene `Datos/anotacion/<sample>/validacion-<name>.json` con `precision_lo >= min_precision_lo` y `recall_lo >= min_recall_lo`.
    - `verifier_no_errors`.
    - `avisos_cerrados`: ningún aviso en `pendiente`. Si no hay registro, «sin registro de avisos».
  - `gate(vault: Path, nid: str) -> {"applies": bool, "ok": bool, "missing": list[str]}`.
  - CLI de `study_status.py`:
    - `propose --vault V --question Q`
    - `apply --vault V --question Q --to S --by W --evidence "…"`
    - `discard --vault V --question Q --reason "…" --by W`, que se niega dentro de una sesión de agente (`CLAUDECODE` / `KAIRO_AGENT_SESSION`), igual que `hypothesis_status.py`.

- [ ] **Step 1: Write the failing tests** (vault inventado con `PROJ-901`, `Q-9001`, `M-9002`):
  - `test_apply_allowed_edge`: `abierta → plan_congelado` con el plan congelado: escribe `status`, `updated` y una entrada en `history`.
  - `test_apply_refuses_unknown_edge`: `abierta → estimada` → exit 3.
  - `test_estimada_requires_gate`: `en_recoleccion → estimada` con `classifiers_validated` fallando → exit 3, y el motivo nombra el clasificador.
  - `test_discard_refused_in_agent`: con `CLAUDECODE=1` → exit 3.
  - `test_gate_estimates_reproducible_detects_edit`: si se cambia una cifra de `## Resultado` a mano, aparece en `missing`.
  - `test_graph_includes_questions`: `build_graph` lista `Q-9001` con su estado.
- [ ] **Step 2:** `python -m pytest scripts/ledger/test_study_status.py -q` → falla.
- [ ] **Step 3: Implement.** `apply` hacia el estado `publishable` exige `gate(...)["ok"]`; el resto de transiciones solo exige el par.
- [ ] **Step 4:** `python -m pytest scripts/ledger -q` → pasa, incluidos los tests existentes de hipótesis y claims.
- [ ] **Step 5: Commit** `study_status.py: estados y puerta de las preguntas desde el registro de diseños`.

### Task 2.4: Skills `plan-study` y `estimate`, y modelos

**Files:**
- Create: `skills/plan-study/SKILL.md`, `skills/estimate/SKILL.md`
- Modify: `config/models.toml` (tareas `plan_study` opus y `estimate_study` sonnet, `kind="job"`)
- Modify: `scripts/models/test_model_policy.py` (`test_tier_assignment`)
- Modify: `skills/critique/SKILL.md` (acepta como artefacto un plan `M-XXXX`)

**Interfaces:**
- `plan-study`:
  1. Crea `Q-XXXX` desde la plantilla, con el diseño que diga el investigador (por defecto `medicion`).
  2. Redacta `M-XXXX` con él y pregunta lo que no esté claro (población, marco muestral, estimandos, cotas de los clasificadores).
  3. Ofrece la crítica.
  4. Congela el plan: `frozen_at` + commit del vault en `frozen_commit` y `study_status.py apply --to plan_congelado`.
- `estimate`: corre `estimate.py`, escribe `## Resultado` (tabla legible y bloque `estimate-json`), construye el paquete `verifier_packet.py --question`, despacha `fresh-verifier` y llama a `study_status.py propose`/`apply`. **Nunca lee el fichero de unidades** (si es sensible, el guardia lo impediría): solo usa la salida de `estimate.py`.

- [ ] **Step 1:** Añade a `test_model_policy.py` las dos tareas con su nivel. `python -m pytest scripts/models -q` → falla.
- [ ] **Step 2:** Edita `models.toml` y escribe las dos skills. Siguen el formato de las existentes (frontmatter `name`, `description` con los disparadores en español).
- [ ] **Step 3:** `python -m pytest scripts/models -q` y `python scripts/models/model_policy.py check` → pasan.
- [ ] **Step 4: Commit** `Skills plan-study y estimate; tareas en models.toml`.

---

## Fase 3 — Datos grandes y recolecciones (app)

### Task 3.1: Huella en streaming en segundo plano

**Files:**
- Modify: `src/projectData.ts` (`MAX_FILES = 1_000_000`; `fingerprint` recorre el árbol y hashea por lotes con `onProgress(files: number)`)
- Modify: `src/jobs.ts`, `src/models.ts` (`dataset_fingerprint: null`), `src/executors.ts`, `src/routesData.ts`
- Test: `test/data-fingerprint.test.ts`

**Interfaces:**
- Produces:
  - `fingerprint(path, onProgress?: (files: number) => void)`. El resultado es idéntico al actual para los mismos ficheros (mismo formato `"<sha>  <relpath>\n"` ordenado).
  - `POST /projects/:id/data/datasets` responde 202 con `{ job }` cuando la carpeta pasa de 2000 ficheros; si no, 201 como hoy.
- [ ] **Step 1: Write the failing tests**
  - `la huella no cambia respecto a la versión anterior`: un árbol inventado de 5 ficheros da el mismo sha que el valor fijado en el test, calculado con la implementación actual antes de tocarla.
  - `una carpeta grande se registra con un trabajo`: con 2001 ficheros vacíos en tmp → 202, el trabajo termina y el registro tiene `files: 2001`.
- [ ] **Step 2:** `npx vitest run test/data-fingerprint.test.ts` → falla.
- [ ] **Step 3: Implement.** El orden de los ficheros: se ordenan las rutas relativas (como hoy) antes de hashear.
- [ ] **Step 4:** Los tests de la Task y `test/data-routes.test.ts` → pasan.
- [ ] **Step 5: Commit** `Datasets hasta un millón de ficheros, huella en segundo plano`.

### Task 3.2: Recolecciones desacopladas

**Files:**
- Create: `src/collections.ts`, `src/routesCollections.ts`, `public/collectionsPanel.js`
- Modify: `src/server.ts` (`reconcileCollections()` al arrancar), `src/routes.ts` (registrar rutas), `public/app.js` (`PNAV` Código: `["recolecciones", "Recolecciones"]`), `public/styles.css`
- Test: `test/collections.test.ts`

**Interfaces:**
- Produces:
  - `type CollectionState = "en_marcha" | "terminada" | "fallida" | "interrumpida" | "detenida"`.
  - `interface Collection { id: string; projectId: string; argv: string[]; commit: string; cwd: string; outDir: string; retries: number; attempts: number; pid: number | null; state: CollectionState; exitCode: number | null; startedAt: string; endedAt: string | null }`.
  - `startCollection(config, proj, { argv, outDir, retries }) => Collection`. Exige `code_repo` con árbol limpio; `commit` = HEAD; `cwd` = `code_repo`. El PATH lleva delante el `code_env`.
  - `listCollections(projectId)`.
  - `stopCollection(id)`.
  - `tailLog(id, lines=50) => Promise<string>` (redactado con `redactText`).
  - `reconcileCollections()`.
  - Base: `~/.kairo/collections/<id>/` (`state.json`, `log.txt`). Se puede cambiar con `KAIRO_COLLECTIONS_DIR` para los tests.
  - Rutas:
    - `GET /projects/:id/collections`
    - `POST /projects/:id/collections` (body `{argv: string[], outDir, retries}`)
    - `POST /collections/:cid/stop`
    - `GET /collections/:cid/log`
- [ ] **Step 1: Write the failing tests** (`argv` = `node -e` con un script inventado, en un repo git temporal):
  - `se niega con el árbol sucio`: un fichero sin commitear → 409 y un mensaje con «haz commit».
  - `termina y apunta el commit y el código`: `state: "terminada"`, `exitCode: 0`, `commit` igual al HEAD, y una nota en el cuaderno.
  - `reintenta tras un fallo`: un script que falla la primera vez (mira un fichero marcador en `outDir`) y acierta la segunda → `attempts: 2`, `terminada`.
  - `concilia una recolección cuyo proceso murió con el servidor`: un `state.json` con `en_marcha` y un pid inexistente → `reconcileCollections()` lo deja `interrumpida`.
  - `el registro se muestra redactado`: un script que imprime una clave falsa → `GET …/log` no la contiene.
- [ ] **Step 2:** `npx vitest run test/collections.test.ts` → falla.
- [ ] **Step 3: Implement.**
  - `spawn(argv[0], argv.slice(1), { detached: true, windowsHide: true, stdio: ["ignore", fd, fd] })` y `unref()`.
  - Un vigilante en proceso escucha `exit` mientras el servidor vive. Tras un reinicio, la conciliación comprueba con `process.kill(pid, 0)` y, si el proceso vive, sondea cada 30 s.
  - Espera entre reintentos: 30 s × 2^(intento−1), con un tope de 30 min.
  - Panel: formulario (orden, carpeta de salida con `attachFolderPicker`, reintentos), tabla con estado y botones «Ver registro» y «Detener».
- [ ] **Step 4:** Los tests de la Task, luego `npm run typecheck` y `npm run lint` → pasan. Comprueba también el panel en el vault de demostración inventado (sin errores de página y legible a 375 px de ancho).
- [ ] **Step 5: Commit** `Recolecciones: ejecuciones largas desacopladas que sobreviven a reinicios`.

---

## Fase 4 — Anotación (app)

### Task 4.1: Muestras, etiquetas, acuerdo y validación

**Files:**
- Create: `src/annotation.ts`, `src/routesAnnotation.ts`, `public/annotationPanel.js`
- Modify: `src/routes.ts`, `public/app.js` (`PNAV` Código: `["anotar", "Anotar muestras"]`), `public/styles.css`
- Test: `test/annotation.test.ts`

**Interfaces:**
- Consumes: `runPy` y `redact.py check` (Fase 1), y `agreement.py` (Task 2.2).
- Produces:
  - `importSample(vault, proj, { name, file, codebook }) => Sample`. Copia `muestra.jsonl` y `codebook.json` a `Datos/anotacion/<name>/` tras pasar `redact.py check` por la muestra entera; si encuentra algo, se niega y no escribe nada.
  - `nextItem(proj, sample, annotator)`.
  - `saveLabel(proj, sample, annotator, itemId, labels: Record<string,string>)`: añade a `etiquetas-<annotator>.jsonl`; la última línea de cada ítem es la que vale.
  - `buildReference(proj, sample)`: escribe `referencia.jsonl` con los ítems en que coinciden los anotadores y una lista de desacuerdos para adjudicar.
  - `adjudicate(proj, sample, itemId, labels)`.
  - `agreementFor(...)`.
  - `validateClassifier(proj, sample, { name, predFile, label, positive })`: escribe `validacion-<name>.json`.
  - El nombre del anotador sale de la configuración del usuario de la app.
  - Rutas bajo `/projects/:id/annotation/...`. Cada escritura hace commit en el vault, y las etiquetas se agrupan en un commit cada 25 etiquetas o al salir del panel.
- [ ] **Step 1: Write the failing tests**
  - `importar una muestra con material privado se niega y no escribe`: la carpeta de destino no existe tras el 409.
  - `etiquetar avanza al siguiente sin etiqueta`.
  - `la referencia toma los acuerdos y lista los desacuerdos`.
  - `validar un clasificador escribe sus cotas`: comprueba los valores con TP=8, FP=2, FN=4 (los mismos que la Task 2.2).
- [ ] **Step 2:** `npx vitest run test/annotation.test.ts` → falla.
- [ ] **Step 3: Implement.** Panel:
  - Muestra los campos del ítem como texto (nunca `innerHTML` sin `esc`).
  - Botones por valor del codebook, con atajos `1`…`9`.
  - Barra de progreso.
  - Pestañas «Acuerdo» (kappa por etiqueta) y «Validar clasificador».
- [ ] **Step 4:** Los tests de la Task, `npm run typecheck`, `npm run lint` y la comprobación en el vault de demostración → pasan.
- [ ] **Step 5: Commit** `Anotación de muestras, acuerdo y validación de clasificadores`.

---

## Fase 5 — Avisos (app)

### Task 5.1: Registro de avisos con embargo

**Files:**
- Create: `src/notices.ts`, `public/noticesPanel.js`
- Modify: `src/routesData.ts` (rutas), `src/timeline.ts` (fechas `embargo_hasta`), `src/context.ts` o el constructor del contexto de proyecto que reciben los agentes (solo recuentos por estado), `public/app.js` (`PNAV` Escritos: `["avisos", "Avisos"]`)
- Test: `test/notices.test.ts`

**Interfaces:**
- Produces:
  - `type NoticeState = "pendiente" | "enviado" | "respondido" | "resuelto" | "sin_respuesta"`.
  - `interface Notice { id: string; destinatario: string; canal: string; enviado: string | null; embargo_hasta: string | null; respuesta: string; estado: NoticeState }`.
  - `readNotices(proj)`, `addNotice`, `updateNotice`.
  - `noticeCounts(proj): Record<NoticeState, number>`.
  - Fichero: `Projects/<slug>/Avisos/registro.json`.
- [ ] **Step 1: Write the failing tests**
  - `añadir y actualizar un aviso hace commit`.
  - `el contexto de los agentes solo lleva recuentos`: el texto de contexto del proyecto contiene `pendiente: 1` y no contiene el `destinatario` inventado.
  - `el Horario muestra el fin del embargo`.
- [ ] **Step 2:** `npx vitest run test/notices.test.ts` → falla.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Los tests de la Task, `npm run typecheck` y `npm run lint` → pasan.
- [ ] **Step 5: Commit** `Avisos a afectados con embargo; a los modelos solo llegan recuentos`.

---

## Fase 6 — Manuscrito por diseño y congreso (plugin)

### Task 6.1: Secciones por diseño, idioma y congreso

**Files:**
- Create: `config/venues.toml`
- Modify: `scripts/manuscript/manuscript.py`, `scripts/manuscript/test_manuscript.py`, `skills/assemble-manuscript/SKILL.md`

**Interfaces:**
- Consumes: `designs.load`, `study_status.gate` (Task 2.3).
- Produces:
  - `init(project_dir, thread, title, venue, design: str | None = None, lang: str = "es")`. Las secciones salen de `designs[design].manuscript_sections` si hay diseño; si no, `DEFAULT_SECTIONS`. `lang` y `venue` van a la cabecera del esquema.
  - `_ID` acepta `^(C|H|Q)-\d{4}$`.
  - `dep_state` para `Q-` delega en `study_status.gate`.
- `venues.toml`, exacto:
  - `acm-sigconf`: `class="acmart"`, `options="sigconf"`, `body_pages=12`, `excludes=["references","appendix"]`.
  - `usenix`: `class="article"`, `package="usenix-2020-09"`, `body_pages=13`, `excludes=["references","appendix"]`.
  - `generic`: `class="article"`, `body_pages=0` (sin límite).
- [ ] **Step 1: Write the failing tests**
  - `test_init_measurement_sections`: con `--design medicion --lang en --venue acm-sigconf`, el esquema tiene las 8 secciones en orden, y `lang: en` y `venue: acm-sigconf` en la cabecera.
  - `test_question_dep_blocks_until_estimada`: un `Q-9001` en `en_recoleccion` ligado a `results` deja la sección `bloqueada`, y el motivo nombra el estado.
  - `test_unknown_venue_refused`: exit 3.
- [ ] **Step 2:** `python -m pytest scripts/manuscript -q` → falla.
- [ ] **Step 3: Implement.** En la skill, `write-section` redacta en `lang` y el resto de reglas no cambia: citas reales, nada sin puerta.
- [ ] **Step 4:** `python -m pytest scripts/manuscript -q` → pasa.
- [ ] **Step 5: Commit** `Manuscrito: secciones por diseño, idioma y congreso`.

### Task 6.2: Exportar a LaTeX y comprobar páginas

**Files:**
- Modify: `scripts/manuscript/manuscript.py` (subcomando `export`), `scripts/manuscript/test_manuscript.py`

**Interfaces:**
- Consumes: `scripts/papers/export_bib.py` (la bibliografía de los papers citados).
- Produces:
  - `manuscript.py export --project-dir D --thread T --format latex`. Escribe `Manuscritos/latex-<T>/main.tex` y `refs.bib`.
  - Salida JSON `{"tex","bib","pages": int|null,"limit": int,"over": bool|null}`.
  - Exit 3 si falta pandoc, con `winget install --id JohnMacFarlane.Pandoc` en el mensaje.
  - Exit 3 si alguna sección `resultado` no está `respaldada`.
- [ ] **Step 1: Write the failing tests** (pandoc y latexmk simulados con ejecutables falsos en el PATH del test):
  - `test_export_refuses_without_pandoc`.
  - `test_export_writes_tex_with_venue_class`: `main.tex` contiene `\documentclass[sigconf]{acmart}` y `\bibliography{refs}`.
  - `test_page_limit_reported`: el latexmk falso deja un `.log` con «Output written … (14 pages» y la marca de inicio de las referencias en la página 13 → `pages == 12`, `over == False`.
- [ ] **Step 2:** `python -m pytest scripts/manuscript -q` → falla.
- [ ] **Step 3: Implement.**
  - Las citas internas `P-XXXX` pasan a claves `\cite{}` mediante el mapa de `export_bib.py`.
  - El cuerpo se cuenta hasta la etiqueta `\label{kairo:refs-start}`, que se inserta antes de la bibliografía.
- [ ] **Step 4:** `python -m pytest scripts/manuscript -q` → pasa.
- [ ] **Step 5: Commit** `manuscript.py export: LaTeX con la clase del congreso y límite de páginas`.

---

## Fase 7 — Plantilla de proyecto e interfaz de preguntas

### Task 7.1: Plantilla `medicion` en create-project

**Files:**
- Modify: `templates/project-template.md` (añadir `medicion` a `template:` con su comentario), `skills/create-project/SKILL.md` (con `medicion`: `code_repo` obligatorio, `seed_hypotheses: false`, ofrecer `plan-study` al final en lugar de sembrar hipótesis)
- Modify: el test de plantillas de create-project que ya existe (búscalo con `rg -l "ligero" scripts --glob "test_*.py"`)

- [ ] **Step 1:** Escribe el test `test_medicion_template_fields`: `template: medicion` implica `seed_hypotheses: false`, `type: ciencia`, y se exige `code_repo`. Al ejecutarlo → falla.
- [ ] **Step 2:** Implementa.
- [ ] **Step 3:** El test → pasa.
- [ ] **Step 4: Commit** `Plantilla de proyecto medicion`.

### Task 7.2: Vista «Preguntas» y trabajos en la app

**Files:**
- Create: `src/questions.ts`, `public/questionsPanel.js`
- Modify: `src/jobs.ts`, `src/models.ts` (`plan_study`, `estimate_study`; ambos en `PENDING_TASKS` hasta que el plugin instalado los tenga), `src/executors.ts`, `src/prompts.ts`, `src/routes.ts`, `public/app.js` (`PNAV` Hipótesis → el grupo pasa a llamarse «Estudio», con `["hipotesis","Hipótesis"]` y `["preguntas","Preguntas"]`)
- Test: `test/questions.test.ts`

**Interfaces:**
- Consumes: `study_status.py propose` y `gate` (vía `runPy`), y los trabajos `plan_study` y `estimate_study`.
- Produces:
  - `listQuestions(proj)`.
  - `questionDetail(config, proj, qid) => { note, plan, gate: {ok, missing[]}, estimates }`.
  - Rutas:
    - `GET /projects/:id/questions`
    - `GET /questions/:qid`
    - `POST /projects/:id/questions/plan` (lanza `plan_study` con `{question, design}`)
    - `POST /questions/:qid/estimate` (lanza `estimate_study`; 409 si el estado no es `en_recoleccion`)
    - `POST /questions/:qid/start-collection` (`plan_congelado → en_recoleccion`, acción del investigador)
- [ ] **Step 1: Write the failing tests**
  - `la lista muestra las preguntas con su estado`.
  - `el detalle trae la lista de comprobación de la puerta`: con un `classifiers_validated` que falla, `missing` lo nombra.
  - `estimar fuera de en_recoleccion da 409`.
- [ ] **Step 2:** `npx vitest run test/questions.test.ts` → falla.
- [ ] **Step 3: Implement.** El panel muestra:
  - la pregunta y el plan (con su estado congelado);
  - la tabla de estimandos con el intervalo;
  - la puerta como lista con ✓ y ✗ y el motivo;
  - los botones según el estado.
- [ ] **Step 4:** Los tests de la Task, `npm run check` y la comprobación en el vault de demostración, que necesita un `Q-9001` inventado → pasan.
- [ ] **Step 5: Commit** `Vista de preguntas: plan, estimandos y puerta`.

---

## Orden de entrega y pruebas de punta a punta

- **Orden:** Fase 1 (ya protege a quien empiece a recolectar), 3, 2, 4, 5, 6, 7.
- **Al cerrar cada fase** se fusiona en `main` (plugin) y en `phase-0-foundations` (app) solo con la suite completa en verde, y se hace `/plugin update` antes de probar en la app.
- **Prueba final, en el vault de demostración, con un proyecto inventado `PROJ-901`:**
  1. Una pregunta `Q-9001` y un plan `M-9002` congelado.
  2. Una recolección con un script inventado que escribe un CSV con una clave falsa (el registro aparece redactado).
  3. El dataset sensible registrado: un `Read` desde una sesión de agente queda bloqueado.
  4. Una muestra anotada por dos anotadores y un clasificador validado.
  5. Un aviso resuelto.
  6. La estimación y el verificador.
  7. `estimada`.
  8. `manuscript.py export` produce un `main.tex` con `acmart`.
