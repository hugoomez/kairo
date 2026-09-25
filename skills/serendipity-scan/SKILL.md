---
name: serendipity-scan
description: Use when a researcher explicitly asks for a serendipity scan on a Kairo project or one specific resolved hypothesis — looking for structural analogies in distant-field literature, or papers that bridge this project's field with an unrelated one via citation structure. Otherwise human-invoked only. The one automated caller is hypothesis-cycle's budget-overflow wildcard step (see that skill) — never a background job, schedule, or any other skill.
---

# Serendipity Scan

## Overview

Two independent, opt-in mechanisms for finding candidates a normal literature
search would never surface, because they share no vocabulary with the
project:

1. **Structural analogy** — take a *resolved* hypothesis's `## Lección`,
   strip the domain vocabulary down to its abstract causal/relational shape,
   and search a **distant field** for the same shape (structure-mapping, not
   keyword overlap).
2. **Boundary-spanning citation** — walk the project's citation graph
   looking for a paper that structurally bridges this project's field and an
   unrelated one (a structural hole, applied to citations instead of social
   ties).

Both mechanisms are speculative by design: their output is a *lead to check*,
not evidence. It never enters `Papers/` or a hypothesis's `Justificación`
automatically — it lives in its own section until a human reads the full
paper and explicitly promotes it through the normal ingestion path
(`create-project` step 6, or a fresh citation if it motivates a new
hypothesis).

## When to use

Only when a researcher **explicitly** asks for this — "look for a surprising
connection", "any analogous pattern elsewhere", "scan for serendipity",
"¿hay alguna analogía en otro campo?", "busca huecos estructurales en las
citas". Run it against one project (`PROJ-XXX`) and, for mechanism 1,
optionally one resolved hypothesis (`H-XXXX`).

**When not to use / when not to trigger:**

- Never invoked automatically by `literature-search`, `create-project`, or
  any other skill — there is no caller wiring for those. If you're a skill
  other than `hypothesis-cycle` considering calling this one, don't; point
  the researcher at it instead.
- **One sanctioned exception:** `hypothesis-cycle`'s budget-overflow wildcard
  step (see that skill's "Budget overflow" section) calls this skill
  automatically — at most once per cycle, only after overflow has already
  triggered a tournament, and only to seed one new candidate claim. That call
  still obeys every invariant below: mechanism 1 still needs a resolved
  hypothesis with a filled `## Lección`, mechanism 2 still needs ≥3 tagged
  papers, the combined cap still applies, and the lead it returns is still
  never cited or merged as evidence directly — `hypothesis-cycle` only uses
  it to inspire a claim that then earns its own citations through a real
  Check 2 search, same as any other candidate. This is the only automated
  caller that may exist; do not add another.
- Never run as a scheduled or batch sweep outside that one wildcard call. It
  is otherwise a per-invocation, per-project scan a human starts and reads.
- Not a substitute for `literature-search` — that skill is the citation
  workhorse for anything a hypothesis actually needs to cite. This skill is
  for leads *outside* that pipeline's reach.
- Mechanism 1 needs a hypothesis with a filled `## Lección` (status
  `apoyada`/`refutada`/`inconclusa`/`evidencia_mixta`) — refuse and say so on
  a hypothesis that's still open or has a placeholder Lección.
- Mechanism 2 needs at least ~3 papers in `Papers/` with this `PROJ-XXX` in
  `projects:` — with fewer, say the corpus is too thin for a citation-graph
  read and stop.

## Mechanism 1 — structural analogy from a resolved Lección

### 1. Extract and abstract

If the hypothesis note has `send: never`, stop: its `## Lección` is not query
material (it would be abstracted into an external search). Say so; the
researcher can write the abstract pattern by hand.

Read the hypothesis's `## Lección`. Rewrite it as an abstract pattern with the
domain-specific nouns replaced by role slots — keep the *relation*, drop the
*field*. Example:

> Lección (domain): "El momento óptimo para detener el entrenamiento depende
> de la dinámica del optimizador, no solo de la forma de la curva de
> validación — dos optimizadores con curvas casi idénticas difirieron en el
> punto óptimo de parada."
>
> Patrón abstraído: *an intervention's optimal timing depends on a hidden
> process-level variable, not on the observable summary statistic normally
> used to decide it — two cases with near-identical observable trajectories
> diverged in optimal timing once the hidden variable differed.*

Write the abstracted pattern down before searching — it's the thing you're
actually testing for a match, not the paper's topic.

### 2. Pick a distant field

Determine the project's own field from the majority `fieldsOfStudy` tag
across its `Papers/` notes (same field Semantic Scholar returns for
`literature-search`). Pick a field with **no realistic citation overlap**
with it — if the project is Computer Science, candidates are e.g. Medicine,
Sociology, Economics, Biology, not Mathematics or Physics, which already
cross-cite ML heavily. Ask the researcher if they have a distant field in
mind; otherwise pick the field least represented in the project's own
citation neighborhood (mechanism 2's 1-hop pull, if already run — else a
plausible default, stated explicitly).

### 3. Search on the pattern, not the domain

Query arXiv / Semantic Scholar (same endpoints, auth, and throttling as
`literature-search`) using the **relational terms** of the abstracted
pattern, `fieldsOfStudy` restricted to the distant field, never the project's
own domain nouns. One or two queries — this is not a facet sweep.

### 4. Judge the match

For each hit, read the abstract and ask: does the *same relational shape*
appear, independent of subject matter? Keep only a candidate you can state
the correspondence for in one sentence (our `X` ↔ their `Y`); drop the rest
here, before the combined cap in **Output** below.

## Mechanism 2 — boundary-spanning citations (structural holes)

### 1. Build the project's field-A neighborhood

Take every `Papers/` note with this `PROJ-XXX` in `projects:` as seeds.
Exclude every note with `send: never` in its frontmatter (list them with
`python "${CLAUDE_PLUGIN_ROOT}/scripts/security/send_guard.py" list Papers --json`):
its ids and metadata are not sent to Semantic Scholar. Say how many were
excluded (`menor`). For
each (or the 3-5 most-cited, if the corpus is large), pull one-hop
`references` and `citations` via Semantic Scholar — same call shape as
`literature-search` step 3.

### 2. Flag candidate bridges

A neighbor is a **candidate bridge** only if both hold:

- its `fieldsOfStudy` includes at least one field clearly outside the
  project's own field (step 2 of mechanism 1's field read), **and**
- it has a **direct** citation edge to a project seed paper (not a 2-hop
  guess) — that edge is the bridge itself.

A neighbor squarely inside the project's own field is not a bridge, however
interesting; a distant-field paper with no direct edge into the seed set
isn't one either — it's just a paper the search happened to touch.

### 3. Prefer the sparsest bridges

Rank candidates by how few direct field-A edges they have (1-2, ideally) —
that scarcity is what makes them a structural hole rather than an
already-absorbed cross-citation. Name the exact bridging edge(s) for each
survivor: which project paper, which direction (cites it / is cited by it).

## Output — always its own section, never merged

**Combined cap: 1-2 candidates per run, total — not per mechanism.** If both
mechanisms ran and both turned up plausible candidates, keep only the
strongest 1-2 across both and say what you dropped and why (don't silently
drop; one line each: "mechanism 2 also found X, dropped for a weaker bridge
than the two kept").

Present survivors under exactly this heading, verbatim:

```markdown
## 🔭 Analogía posible, sin verificar

### [Analogía estructural | Cita puente] — <título>, <id/link>

- **Patrón / puente:** <the abstracted pattern (mechanism 1) or the exact
  bridging edge (mechanism 2)>
- **Correspondencia:** <one sentence: our X ↔ their Y — or which project
  paper connects to which distant-field cluster>
- **Por qué no está verificada:** coincidencia de patrón estructural /
  estructura de citas, no lectura completa ni juicio de relevancia real.
- **Para promoverla:** leer el paper completo; si sostiene, ingestarlo vía
  `create-project` paso 6 (Zotero) y citarlo en una `Justificación`, o
  levantar una hipótesis nueva con `spawn-hypothesis` / `hypothesis-cycle`
  citándolo explícitamente.
```

This section is **additive and disposable** — it is never written into
`Estado-del-arte.md`, never appended to a hypothesis's `Justificación`, and
never used as a `linked_papers` entry. It exists only until a human reads it
and either discards it or starts the normal ingestion path by hand.

If a run turns up nothing that survives the cap, say so in one line — don't
lower the bar to fill the section.

## Common mistakes

- **Wiring this into another skill's pipeline.** Only `hypothesis-cycle`'s
  budget-overflow wildcard step may invoke this automatically — see **When
  not to use**. If you're tempted to call it from `hypothesis-cycle`'s
  novelty check (Check 2), a different skill, or a scheduled sweep, don't;
  surface it as a suggestion to the human instead.
- **(hypothesis-cycle caller only) Citing the lead directly as evidence.**
  The wildcard step may use a lead to inspire a new claim; it may never put
  the lead itself in that candidate's `Justificación` — the candidate earns
  its own citations through a real Check 2 search, exactly like any other
  candidate. Skipping that search and citing the lead instead is the exact
  auto-merge this skill's output is not allowed to have.
- **Searching the domain nouns instead of the abstracted pattern.** That's
  just `literature-search` with extra steps and will surface same-field
  papers this skill exists to route around.
- **Pattern-matching on topic similarity instead of structure.** "Also about
  neural networks" is not a structural analogy.
- **Calling a same-field neighbor a bridge.** A candidate bridge's own field
  must sit outside the project's — otherwise it's an ordinary citation, not a
  structural hole.
- **Padding past the combined cap.** 1-2 candidates total is the ceiling, not
  a target — a thin, high-confidence result beats a padded list; when both
  mechanisms find candidates, cut down to the strongest 1-2, don't just
  concatenate each mechanism's own shortlist.
- **Merging a candidate into `Papers/`, `Estado-del-arte.md`, or a
  `Justificación` directly.** Every candidate stays in the
  `## 🔭 Analogía posible, sin verificar` section until a human explicitly
  promotes it through the normal ingestion path.
- **Running mechanism 1 on an open hypothesis.** No `## Lección` yet means
  nothing to abstract — wait for resolution.
- **Running mechanism 2 on a 1-2 paper corpus.** Too thin to distinguish a
  real bridge from noise — say so and stop.

## Related

- `literature-search` — same API access patterns (arXiv/Semantic Scholar
  endpoints, auth, throttling) and the ingestion path this skill's
  candidates eventually go through if promoted; this skill borrows its
  plumbing, not its relevance-ranking pipeline.
- `create-project` step 6 — the Zotero-first ingestion a promoted candidate
  goes through to become a real `Papers/` note.
- `spawn-hypothesis` / `hypothesis-cycle` — where a promoted candidate goes if
  it motivates a new hypothesis rather than just supporting an existing one.
  `hypothesis-cycle`'s "Budget overflow" section is also this skill's one
  automated caller (the wildcard step) — see **When not to use** above.
- `${CLAUDE_PLUGIN_ROOT}/templates/hypothesis-template.md` (`## Lección`) —
  the field mechanism 1 reads.
