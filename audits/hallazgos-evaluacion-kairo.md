# Hallazgos de la evaluación de Kairo (para arreglar en bloque)

Se van añadiendo por paso del protocolo. Prioridad: Alto / Medio / Bajo. No se arregla
nada hasta cerrar todo el protocolo de evaluación.

## Paso 1-2 — Creación de proyecto, búsqueda y síntesis

1. **[Alto]** El pase-ancla (Semantic Scholar `/paper/search/bulk`) no es robusto a
   fallos del endpoint — la faceta D perdió su pase-ancla por HTTP 500 ×2 sin reintento;
   Nakkiran et al. 2019 solo se rescató por suerte, vía snowballing desde una referencia
   directa de Power et al. Falta backoff/reintento en este pase igual que ya existe para
   el 429.
2. **[Medio]** No hay aviso destacado (solo una fila en la tabla de log) cuando una
   faceta pierde por completo su pase-ancla o su pase de relevancia — se puede pasar por
   alto fácilmente al leer el documento final en vez del log crudo.
3. **[Medio]** La búsqueda en el vault (Smart Connections MCP) no se probó de verdad en
   este proyecto — "herramienta no disponible en la sesión" en las 4 facetas. Confirmar
   configuración antes de que importe con proyectos que sí tengan solapamiento.
4. **[Medio]** La exclusión de un candidato por **alcance** (Fuera: ...) no se distingue
   explícitamente de la exclusión por **relevancia baja** — Omnigrok (P-relevante, #7 en
   el ranking) quedó fuera del set recomendado sin que quede claro si fue por scope o
   por prioridad, y el investigador puede no darse cuenta de que se excluyó algo
   relevante a propósito.
5. **[Bajo]** Plantilla ambigua: la sección `## Revisión del ciclo` apareció en H-0001
   documentando un refinamiento exitoso (Check 2), cuando el diseño original la reservaba
   solo para el caso de rondas agotadas sin veredicto. Decidir un único comportamiento y
   fijarlo en la skill.
6. **[Medio]** Cuando la sesión detecta 429 repetidos en varias facetas, debería
   recomendar activamente configurar la key gratuita de Semantic Scholar en ese momento,
   no dejarlo solo como nota de documentación que hay que saber de antemano.
7. **[Medio]** Los conteos de deduplicación se reportaron aproximados ("≈95", "≈136")
   en vez de exactos — rompe el objetivo de trazabilidad tipo PRISMA que le pedimos
   explícitamente a esta skill.
8. **[Alto]** El chequeo de retracción solo cubre registros DOI vía Crossref — en un
   dominio dominado por preprints de arXiv (95%+ del pool aquí), eso deja casi todo el
   pool sin chequear. Falta el equivalente para "withdrawn" de arXiv.
9. **[Bajo]** No hay señal explícita de "cobertura degradada" a nivel de faceta cuando
   varios de sus pasos fallaron — está en el log crudo pero no se traduce a un aviso en
   el documento final que de verdad se lee.
10. **[Verificar, no es un bug de código]** Comprobar en algún momento, contra los PDFs
    reales, que las citas de sección/tabla/figura/ecuación son exactas y no
    aproximaciones plausibles — riesgo conocido de precisión "confabulada" en LLMs que
    no se puede descartar solo leyendo el propio output.

## Paso 3 — Entrada humana y crítica

11. **[Bajo/Decisión de diseño]** Ante un clear-fail en la crítica, Kairo ofreció un menú
    interactivo de opciones (refinar hacia sub-régimen incierto, refinar hacia un
    fenómeno relacionado, archivar igualmente marcado `needs_human_review`, o descartar
    siguiendo la puerta) — el propio sistema marcó una de esas opciones como "off-spec
    for the gate". El diseño de `hypothesis-cycle` solo documenta "clear fail → no note,
    discard". Decidir si esta negociación interactiva se formaliza en la skill o se deja
    como comportamiento emergente de Claude Code.
12. **[Subir a Alto]** Smart Connections MCP sigue sin cargar en esta sesión — van dos
    invocaciones distintas de `hypothesis-cycle`/`create-project` sin que la búsqueda en
    el vault funcione. Ya no parece un fallo puntual; revisar la configuración del MCP
    antes de seguir, no solo anotarlo.
13. **[Medio]** Hueco de esquema: `Experimentos/E-XXXX.md` solo tiene `hypothesis:
    <H-XXXX>` singular. H-0001 y H-0006 quedaron cross-linkeados como "par rival" donde
    un mismo diseño experimental (barrido de r) adjudica a ambas — el esquema actual no
    tiene forma de declarar que un experimento sirve a más de una hipótesis a la vez.
14. **[Bajo]** El código del experimento se escribe *después* de congelar el texto del
    preregistro (frozen_commit es el HEAD del vault, no del código, que aún no existe).
    El hash del código se registra antes de ejecutar, lo cual mitiga el riesgo, pero
    queda una ventana temporal donde quien escribe la implementación conoce ya el
    diseño completo — vale la pena reforzar explícitamente que la implementación debe
    seguir literalmente las fórmulas ya congeladas (plateau_step, stop_point, Δ) sin
    margen de ajuste de detalle.
15. **[Alto]** `run-experiment` asumió que ejecutar en un runtime remoto (Kaggle/Colab)
    requiere clonar el repo de git del vault completo — lo cual exigiría crear un
    remoto para `vault/`, rompiendo el principio de "todo local, sin repo remoto"
    establecido desde la Fase 0. En la práctica, solo hacían falta los scripts del
    experimento + `E-0001.data.json`, transferibles sin git. Revisar cómo la skill
    maneja rutas relativas para que no asuma dependencia del árbol completo del vault
    cuando la ejecución ocurre en un runtime externo.
16. **[Alto, sin confirmar]** Un aviso durante la escritura del código de E-0001
    (LayerNorm/unembedding atado puede suprimir el grokking) se señaló pero no se
    escaló como riesgo grave — el diseño se congeló tal cual, y el experimento resultó
    `invalid` exactamente por esa razón, quemando 7.5 GPU-h reales. No se confirmó si
    el aviso se comunicó con la urgencia real que merecía o como una nota más entre
    varias decisiones triviales. Revisar si `preregister-experiment`/`run-experiment`
    necesitan un nivel de severidad explícito al señalar riesgos al investigador —
    "esto puede invalidar el experimento entero" no debería sonar igual que "¿ReLU o
    GELU?".

## Plan de resolución para la siguiente fase

Agrupado por dónde vive el arreglo, en el orden en que tiene sentido atacarlo —
primero lo que rompe la promesa central (todo local, sin repo remoto; el vault
buscable), luego robustez de búsqueda, luego esquema y plantillas, y al final lo que
solo necesita verificación, no código.

**Bloque 1 — Infraestructura y promesas centrales (15, 12, 3)**
- Arreglar la asunción de git-clone-del-vault-completo en `run-experiment` (15).
- Diagnosticar y arreglar por qué Smart Connections MCP nunca cargó en ninguna
  sesión de esta evaluación (12, 3) — antes de seguir usando la herramienta con
  proyectos que sí tengan solapamiento real entre ellos.

**Bloque 2 — Robustez de `literature-search` (1, 2, 6, 7, 8, 9)**
- Retry/backoff en el pase-ancla igual que ya existe para 429 (1).
- Aviso destacado (no solo fila de log) cuando una faceta pierde cobertura (2, 9).
- Recomendación proactiva de key de Semantic Scholar ante 429 repetidos (6).
- Conteos de deduplicación exactos, no aproximados (7).
- Chequeo de retracción para "withdrawn" de arXiv, no solo DOI/Crossref (8).

**Bloque 3 — Esquema y plantillas (4, 5, 11, 13)**
- Distinguir explícitamente exclusión por alcance vs. por relevancia baja en los
  candidatos de búsqueda (4).
- Fijar un único comportamiento para `## Revisión del ciclo` (5).
- Decidir si el menú interactivo ante un clear-fail se formaliza en la skill (11).
- Añadir soporte de esquema para que un experimento sirva a más de una hipótesis
  (13).

**Bloque 4 — Disciplina de escritura de código y comunicación de riesgo (14, 16)**
- Reforzar que la implementación debe seguir literalmente las fórmulas congeladas,
  sin margen de ajuste de detalle (14).
- Definir un nivel de severidad explícito para los avisos que se le hacen al
  investigador durante preregistro/ejecución, para que un riesgo grave no se lea
  igual que una elección trivial (16).

**Bloque 5 — Solo verificación, no código (10)**
- Spot-check contra PDFs reales de que las citas de sección/tabla/figura son
  exactas.

**Bloque 6 — Migración a plugin**
- Empaquetar skills/agentes/plantillas/scripts como plugin de Claude Code en un
  repo separado (público en el futuro), dejando `vault/` intacto, privado y local.
