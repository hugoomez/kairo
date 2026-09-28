---
id: C-XXXX
project: <PROJ-XXX>
# kind — what sort of intermediate node this is:
#   lema                 — a lemma / derivation step other notes rely on
#   resultado_intermedio — an intermediate empirical or analytical result
#   rung                 — one rung of a simplification ladder (hypothesis-cycle /
#                          preregister-experiment, B1): a cheap relaxed experiment
#   linaje               — one step of an evolve-program lineage (B3)
#   teorema              — a theorem (a lema that is a headline result)
# A lema / teorema reaches `probado` only through the rigor gate
# (scripts/ledger/claim_gate.py): fresh verifier on the proof, the
# researcher's sign-off on the statement AND on the proof, and a numerical
# sanity check (or its signed-off infeasibility) — all on the current text —
# plus every dependency established.
kind: <lema | teorema | resultado_intermedio | rung | linaje>
# status enum — use exactly one. Written ONLY by scripts/ledger/claim_status.py
# (creation at pendiente included). Never edit it by hand, never from a skill.
#   pendiente — not settled yet
#   probado   — established (derivation checked / the run's prediction held)
#   fallido   — the attempt to establish it failed (did not run, invalid run,
#               derivation broke) — says nothing about truth either way
#   refutado  — shown false (a valid run / check contradicted it)
status: pendiente
created: <YYYY-MM-DD>
updated: <YYYY-MM-DD>
# about — the hypothesis this claim serves (optional; required for kind: rung)
about: <H-XXXX>
# source — the experiment / run that settles it (optional; required for kind: rung)
source: <E-XXXX>
# depends_on — ids (H-XXXX / C-XXXX, any project) this claim logically relies on.
# build_graph.py flags (never rewrites) every dependent of a refutada / fallido /
# refutado node, and flags cycles and dangling ids as crítico.
depends_on: []
# role / rung — only for kind: rung (and linaje, when it ran an experiment):
# copied from the rung's experiment note (docs/v3-interfaces.md §1d). A rung is
# always role: exploratory and never counts as evidence for `about`.
role: <exploratory>
rung: <0 | 1 | 2>
# --- lema / teorema only: the gate's records (append-only, never edited) ---
# verifications — written by verifications.py after a fresh-verifier run on the
# proof packet (statement + proof + dependencies' statements).
# signoffs — written ONLY by signoff.py, which refuses inside agent sessions:
#   - part: statement | proof | infeasibility
#     sha256: <sha256 of the exact text signed>
#     by: <researcher>
#     date: <YYYY-MM-DD>
# numerical_checks — written by numeric_check.py (Claims/checks/C-XXXX.py):
#   - status: passed | failed | error | not_feasible
#     statement_sha256: …  script_sha256: …  output_sha256: …  reason: …  date: …  by: …
# formal — optional, informational (never replaces the sign-off):
#   {tool: lean, file: <path>, status: <complete | incomplete | failing>}
# history — append-only, written by claim_status.py on every status change.
history:
  - date: <YYYY-MM-DD>
    status: pendiente
    by: <human name | agent id>
    evidence: <short note>
---

## Enunciado

<Una frase: qué afirma este nodo. Para un rung: la predicción relajada que el
rung pone a prueba, p. ej. "el modelo congelado grokea en suma mod 23, r = 0.3,
en ≤ 5k pasos de CPU".>

## Demostración

<Solo lema / teorema, en lugar de «Cómo se establece» y «Resultado»: la
demostración completa, paso a paso, usando solo el enunciado y los enunciados
de sus dependencias (depends_on). Es lo que ve el verificador.>

## Comprobación numérica

<Solo lema / teorema: qué casos pequeños prueba Claims/checks/C-XXXX.py, o por
qué no es factible comprobarlo computacionalmente.>

## Cómo se establece

<Derivación, o experimento (`E-XXXX`, rung N, preregistro ligero) y su regla de
decisión: qué resultado lo deja `probado`, `refutado` o `fallido`.>

## Resultado

<Lo que se observó, con la salida exacta o un enlace al log. Un rung fallido se
registra aquí igual que uno exitoso — nunca se borra ni se oculta.>

## Qué informa

<Para un rung: qué decisiones del diseño confirmatorio informa (la sección
`## Escalera de simplificación` del preregistro confirmatorio lo cita). Para un
lema: qué notas lo usan (ver su `depends_on`).>
