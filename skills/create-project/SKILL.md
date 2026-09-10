---
name: create-project
description: >-
  Use when starting a new research project in a Kairo vault — the user says
  "create a project", "new project on X", "set up a project for Y", or hands
  you a project brief. Orchestrates the full bootstrap: fill the project
  template, assign a PROJ id and scaffold folders, compute related projects,
  run literature search, ingest confirmed papers, build the state-of-the-art
  map, and seed candidate hypotheses.
---

# Create Project

## Overview

Bootstraps one research project end to end: a `PROJ-XXX` hub note, the folder
scaffold, a literature sweep, ingested `Papers/` notes, an `Estado-del-arte.md`
map, and 3–5 seed hypotheses — then one commit.

Binding rules (Kairo core principles):

- **Every claim in `Estado-del-arte.md` and every hypothesis justification cites a
  specific paper id + section/table/figure** when possible. No unsupported
  claims.
- Seed hypotheses are created at `status: propuesta` only. This skill never
  promotes anything.
- Prefer asking the user over guessing on workflow ambiguity, but don't block on
  questions that can wait — draft first, then raise open questions. The one hard
  gate: **Propósito and Alcance must exist before step 2** (see Inputs).

## When to use

- User asks to create / start / set up a new project.
- User hands you a project brief (goal + scope) and expects a project built from
  it.

**When not to use:** adding a hypothesis or experiment to an *existing* project;
a pure literature search with no project (use `literature-search` directly);
editing an existing hub.

## Inputs

The project brief. Minimum to proceed: **Propósito** (central goal/question) and
**Alcance** (Dentro / Fuera). If either is missing, ask the user for it and stop
until you have both.

Everything else in `${CLAUDE_PLUGIN_ROOT}/templates/project-template.md` is optional but improves later
stages — especially **Vocabulario conocido** and **Papers semilla** (feed the
literature search, step 4) and **type** + **autonomy_defaults** (gate steps 4–5).

## Prerequisites

- The `literature-search` skill is available.
- Smart Connections MCP available and indexed (`mcp__smart-connections__*`). An
  empty index is fine — related-project and similarity steps just return less.
  If the tool is **not available at all** (not just empty), don't silently skip
  the similarity steps: tell the user, point them to the plugin README → "Smart Connections
  (optional companion)" (usual cause: Claude Code launched from a parent folder
  instead of the vault root, or `.mcp.json` changed without a restart / `/mcp`
  reconnect), and
  mark step 3 as run without vault-similarity coverage.

## Procedure

Run the steps in order. Steps 4, 6, 7 are the heavy passes.

### 1. Fill the project template

Copy `${CLAUDE_PLUGIN_ROOT}/templates/project-template.md` and fill it from the brief. Required:
`Propósito`, `Alcance` (Dentro + Fuera). Fill every other section the brief
supports; leave untouched placeholders only where the brief is genuinely silent.

Frontmatter: set `name`, `created` (today), `status: active`, `type`
(`ciencia | producto | hibrido`), and `autonomy_defaults.*`. Leave
`related_projects: []` — step 3 fills it. Leave `id` for step 2.

### 2. Assign id and scaffold

- **Next id:** scan `Projects/*/_hub.md` frontmatter `id:`, take the max
  `PROJ-NNN`, add 1, zero-pad to 3 digits. First project is `PROJ-001`.
- **Slug:** kebab-case the project name → `<slug>`. Folder is `Projects/<slug>/`.
- Write the filled template to **`Projects/<slug>/_hub.md`** with `id:` set.
- Create empty subfolders `Projects/<slug>/Hipotesis/`,
  `Projects/<slug>/Experimentos/`, `Projects/<slug>/Producto/`, each with a
  `.gitkeep` so git tracks them.

### 3. Compute `related_projects`

Embed the **Propósito** text via Smart Connections
(`mcp__smart-connections__search_by_text` with the Propósito as query, or
`search_similar` on `_hub.md` once written) and compare against existing
`Projects/*/_hub.md` hubs. Write the matching `PROJ-XXX` ids into the hub's
`related_projects` frontmatter list, strongest first; keep only matches above a
similarity cutoff (default ≈ 0.7 — tune, don't dump the whole list).

> The template comments say `related_projects` is auto-computed from shared
> papers/hypotheses. At creation there are none, so seed it from Propósito
> similarity here; later maintenance is by shared papers/hypotheses.

### 4. Literature search

Invoke the **`literature-search`** skill. Input = **Propósito** +
**Vocabulario conocido** + **Papers semilla** (as seed papers for snowballing) +
the **`Alcance: Fuera`** clauses (so it can classify scope exclusions separately
from low relevance — step 4d there).

Include the **PatentsView / patent facet only if `type` is `producto` or
`hibrido`** — otherwise omit it entirely (matches that skill's own gate).

Keep the facet table and the ranked, justified candidate list it returns — steps
5 and 7 both consume them. Each ranked candidate carries a **`matched:` record**
(which facet term/synonym hit, from the `facet-searcher` output); step 7 reuses it
to assign papers to facets without re-deriving membership.

### 5. Confirm candidates — respect `autonomy_defaults.paper_ingestion`

| `paper_ingestion` | Behavior |
|---|---|
| `manual` | Present the full ranked list with the one-sentence justification per candidate. **Wait** for the user to pick which to ingest. Ingest nothing until they answer. |
| `autonomo` | **Auto-accept** candidates that clear the strong-match bar *and* whose similarity to Propósito is high (default ≈ 0.8). Ingest those. For every remaining candidate, list it with its justification as a notification — do **not** block, do **not** ingest it. |

The confirmed set = user picks (`manual`) or auto-accepted set (`autonomo`).

If `literature-search` returned a `### Relevante pero fuera de alcance` list,
show it to the user in both modes (it is never auto-ingested) — a relevant paper
held out only by an `Alcance: Fuera` clause is a scope decision the researcher
may want to revisit.

### 6. Ingest each confirmed paper

For each confirmed paper:

1. **PDF:** if openly available (Semantic Scholar `openAccessPdf`, arXiv PDF
   link), download it. If not, skip the download — do not paywall-scrape.
2. **Full text:** extract from the PDF when you have it; otherwise fall back to
   the abstract and mark the note `fulltext: abstract-only`.
3. **Note:** dedup by DOI → arXiv id → title similarity against existing
   `Papers/` notes.
   - **New:** create `Papers/<P-id> <short-title>.md` (next `P-XXXX`, scan
     `Papers/` frontmatter for the max), `projects: [<PROJ-XXX>]`.
   - **Exists (from another project):** append `<PROJ-XXX>` to its `projects:`
     list; refresh full text only if the note had none.
4. Include a short **bibliographic section** (see Paper note format below).

**v1 scope:** no automatic Zotero sync — create the Obsidian note directly. Real
Zotero integration is a fast-follow, out of scope here.

### 7. Generate `Projects/<slug>/Estado-del-arte.md` (map-reduce via subagents)

**Map — `facet-summarizer` subagents, in parallel.** For each facet from step 4,
assign it the ingested papers that `literature-search` recorded as matching that
facet — **reuse the per-candidate `matched:` record from the ranked list; do not
re-derive facet membership.** Then **dispatch one `facet-summarizer` subagent per
facet, all launched together in the same turn**, each given its facet + the
explicit list of `Papers/P-XXXX ….md` note paths assigned to it + the project
`type`. Each subagent reads **only its assigned notes** and returns a compact,
fully-cited contribution to whichever canonical sections its papers support (it
never touches §4 or §8). Collect every contribution.

**Reduce — one pass in the main session.** Merge the sub-contributions into the
final document in the canonical 9-section order below. Two sections are produced
**here, not by any subagent**, because they need the whole cross-facet picture:

- **§4 Escuelas de pensamiento en competencia** — from the `(para el reduce)
  señales de escuelas en competencia` material the summarizers flagged; include
  it only if genuinely competing schools emerge across facets.
- **§8 Orden de lectura recomendado** — from the `(para el reduce) dificultad de
  lectura / prerequisitos` material, sequenced across all facets.

Also in the Reduce pass: resolve overlaps where two facets cite the same paper,
add the frontmatter and per-section staleness notes below, append the Búsqueda
ejecutada block, and add the `## Matriz de conceptos` appendix when it is
warranted (see the gate below).

**If `literature-search` returned a `## ⚠️ Cobertura degradada` block** (a facet
lost its anchor or relevance pass), reproduce it **verbatim at the very top of
`Estado-del-arte.md`**, directly under the frontmatter — not only inside the
appended Búsqueda ejecutada block at the bottom. The reader must see, before the
synthesis, that part of the literature was not covered. Also mention it in the
one-paragraph summary you give the user when the skill finishes.

**Frontmatter:** set `last_updated: <YYYY-MM-DD>` (today). `update-confidence`
bumps this whenever it edits the map; a stale `last_updated` is the at-a-glance
signal that the map has drifted from the evidence.

Use exactly these `##` section titles, in this order. Include **§4 only if**
genuinely competing schools exist; include **§9 only if** `type` is
`producto`/`hibrido`. All other sections always appear. **Directly under each
`##` heading, put a one-line staleness note:** `*as of <YYYY-MM-DD>, N papers*`
(N = the project's ingested papers that informed that section).

1. Vocabulario y notación
2. Línea de evolución de las ideas
3. Papers ancla vs. de frontera
4. Escuelas de pensamiento en competencia *(if relevant)*
5. Lo establecido vs. lo debatido
6. Huecos identificados
7. Herramientas/benchmarks/datasets estándar
8. Orden de lectura recomendado
9. Panorama competitivo y de propiedad intelectual *(producto/hibrido only)*

**Every claim cites a specific paper id + section/table/figure** where possible
(e.g. `P-0007 §4.2`, `P-0012 Tabla 3`). A claim with no citable source does not
go in.

**Optional appendix — `## Matriz de conceptos`.** When the ingested set is large
enough to be hard to hold in the head (roughly **> 15 papers**), add a concept ×
paper table: concepts (the recurring claims / mechanisms / definitions) as rows,
paper ids as columns, each cell = one phrase for what that paper says about that
concept (blank = doesn't address it). Skip this appendix entirely for smaller
sets — a 6-paper matrix is noise, not signal.

Also append the **Búsqueda ejecutada** block from step 4 (the frozen queries +
PRISMA counts) so the map's evidence base is auditable.

### 8. Seed candidate hypotheses

Input = the **"Huecos identificados"** section. Feed **3–5** gaps as candidates —
never more than 5 on this first pass.

- **The `hypothesis-cycle` skill is available — delegate to it** (gap-derived entry point). It runs the full gate (incl. the Duhem check
  and the written `## Hipótesis rival descartada`), creates the passing notes at
  `status: propuesta`, and — if more than the budget pass — hands the overflow to
  `update-confidence` (trigger `budget overflow`) for `en_cola`. Do not
  post-process its output.
- **Fallback (no `hypothesis-cycle` present)** — a reduced-rigor stopgap: draft
  plain notes from `${CLAUDE_PLUGIN_ROOT}/templates/hypothesis-template.md` into
  `Projects/<slug>/Hipotesis/` as `H-XXXX <slug>.md` (next `H-XXXX`, scan the
  whole vault). For each:
  - `status: propuesta`, `project: <PROJ-XXX>`, `created`/`updated` = today.
  - `Claim`: one falsifiable sentence.
  - `Justificación (evidencia citada)`: bullets citing `P-XXXX §…` — grounded in
    the state-of-the-art map, not invented.
  - `generated_by`: `origin: agent`, `model: claude-sonnet-5`,
    `skill_version: create-project@v1`, `pipeline_config: <hub path>`.
  - `history`: first entry — `date` today, `status: propuesta`,
    `by: <agent id>`, `evidence: "seeded from Estado-del-arte.md §Huecos"`.

### 9. Initialize `_digest.md`

Write `Projects/<slug>/_digest.md` as an empty table — header + separator row
only, no data rows:

```markdown
| id | claim | estado | lección |
|----|-------|--------|---------|
```

From here on the table is maintained by `update-confidence` (hypothesis rows,
regenerated) and `hypothesis-cycle` (append-only `descartada` discard rows,
preserved across regeneration). This skill only creates it.

### 10. Commit

Stage the new project folder and any new/modified `Papers/` notes. Commit with
subject:

```
Create project <name>
```

(`<name>` = the human project name.) Keep whatever commit trailers the
environment mandates.

## Naming & ids

| Thing | Rule |
|---|---|
| Project folder | `Projects/<slug>/` — kebab-case of `name` |
| Hub file | `Projects/<slug>/_hub.md` |
| Project id | `PROJ-NNN`, max existing + 1, zero-padded 3 digits, first = `PROJ-001` |
| Paper id | `P-NNNN`, max existing in `Papers/` + 1, zero-padded 4 digits |
| Hypothesis id | `H-NNNN`, max existing vault-wide + 1, zero-padded 4 digits |
| Subfolders | `Hipotesis/`, `Experimentos/`, `Producto/` (+ `.gitkeep` while empty) |
| SOTA map | `Projects/<slug>/Estado-del-arte.md` |
| Digest | `Projects/<slug>/_digest.md` |

## Paper note format (v1, no template file exists)

```markdown
---
id: P-XXXX
title: <full title>
authors: [<Last, First>, ...]
year: <YYYY>
venue: <journal / conference / "arXiv preprint">
doi: <10.xxxx/... or empty>
arxiv: <id or empty>
url: <abstract/landing page>
pdf: <local path or source URL or empty>
projects: [<PROJ-XXX>]
added: <YYYY-MM-DD>
source: <arxiv | semantic-scholar | patentsview | manual>
fulltext: <full | abstract-only>
---

## Referencia

<One-line bibliographic citation. DOI/arXiv id. Open-access status.>

## Resumen

<Abstract verbatim or a 2–4 sentence summary.>

## Texto completo

<Extracted full text, or "No disponible — solo abstract." >
```

When a paper is already ingested for another project, only append this project's
`PROJ-XXX` to `projects:` — don't duplicate the note.

## Common mistakes

- **Starting without Propósito + Alcance.** Hard gate — ask first.
- **Dispatching the `facet-summarizer` subagents sequentially (step 7 Map).**
  Launch all facets in one turn; serial dispatch defeats the map-reduce.
- **Re-deriving facet membership in step 7.** Reuse `literature-search`'s
  per-candidate `matched:` record — don't recompute which paper belongs to which
  facet.
- **Letting a subagent's raw material back into the main context unfiltered.**
  `facet-searcher` returns a compact list, `facet-summarizer` returns cited
  bullets — never raw payloads or whole paper texts. The Reduce pass merges those
  compact outputs.
- **Running the patent facet for a `ciencia` project.** Only `producto`/`hibrido`.
- **Ingesting papers in `manual` mode before the user answers.** Wait.
- **In `autonomo` mode, silently dropping the low-similarity tail.** Auto-accept
  the strong matches, but still *list* the rest as a notification.
- **Uncited claims in `Estado-del-arte.md`.** Every claim needs a paper id +
  location. Drop it or find the source.
- **Renumbering the SOTA sections when §4/§9 are omitted.** Keep the canonical
  titles and order; just leave the conditional ones out.
- **More than 5 seed hypotheses on the first pass.** Cap at 5.
- **Promoting a seed hypothesis past `propuesta`.** Not this skill's job.
- **Duplicating a `Papers/` note that already exists for another project.**
  Append to `projects:` instead.
- **Paywall-scraping a closed-access PDF.** Open-access only; else abstract-only.

## Not in v1

- Automatic Zotero sync (create Obsidian notes directly; Zotero is a fast-follow).
- Promotion / experiment scaffolding (separate skills).
