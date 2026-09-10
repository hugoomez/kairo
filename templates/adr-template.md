---
id: ADR-XXX
project: <PROJ-XXX>
date: <YYYY-MM-DD>
# status — Nygard ADR lifecycle (NOT a hypothesis status; not managed by update-confidence)
status: Accepted  # Accepted | Superseded by ADR-XXX | Deprecated
# supersedes — set on a replacement ADR to the id of the one it replaces
supersedes: <ADR-XXX or omit>
# cites — list of hypothesis and/or paper ids that motivate this decision
cites: []  # e.g. [H-0001, P-0002]
# cites_status — optional as-of snapshot of each cited hypothesis's status on `date`.
# Fill when authoring so adr-check compares exactly instead of reconstructing from history.
cites_status: {}  # e.g. {H-0001: apoyada}
---

> Al mostrar, revisar o reutilizar este ADR, correr la skill `adr-check`: compara
> el estado actual de cada hipótesis citada con el que tenía cuando se escribió el
> ADR y marca de forma visible cualquier cambio. No editar la decisión; registrar
> cualquier revisión en `## Revisión de vigencia`. Si la decisión debe cambiar,
> `adr-check` crea un ADR nuevo (`status: Accepted`, `supersedes:` este id) y pone
> este en `status: Superseded by ADR-XXX` sin tocar sus secciones.

## Decisión

<Qué se decidió, en una frase en presente.>

## Contexto

<Situación y fuerzas en juego que obligaron a decidir.>

## Alternativas consideradas

- **<Alternativa A>** — <por qué se descartó.>
- **<Alternativa B>** — <por qué se descartó.>

## Papers/hipótesis que la motivan

- <H-XXXX — cómo respalda esta decisión.>
- <P-XXXX §Sección / Tabla N — cómo respalda esta decisión.>

## Revisión de vigencia

<Append-only. Una entrada por revisión: fecha, qué cambió en las hipótesis
citadas (según `adr-check`), y si la decisión sigue en pie o requiere un ADR
nuevo. No modificar las secciones anteriores. Si se decide reemplazarla, la
última línea es: "Reemplazado por ADR-XXX el <fecha> — <razón + flag de
adr-check>".>
