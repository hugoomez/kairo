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
- Zotero available locally (`localhost:23119`, "Allow other applications..."
  enabled) for step 6's ingestion. Optional — see the plugin README → "Zotero
  (reference manager)" for setup. When unreachable, step 6 falls back to
  writing the `Papers/` note directly and says so; it never silently skips the
  Zotero add.

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

### 6. Ingest each confirmed paper — via Zotero

For each confirmed paper, add it to Zotero **first**, then generate the
`Papers/` note from that Zotero entry. See the plugin README → "Zotero
(reference manager)" for the endpoints and one-time setup this step assumes.

1. **PDF:** if openly available (Semantic Scholar `openAccessPdf`, arXiv PDF
   link), download it. If not, skip the download — do not paywall-scrape.
2. **Dedup, Zotero-side first:** Better BibTeX `item.search` for the DOI, then
   the arXiv id, then title. A hit means this paper already has a Zotero
   record (possibly from another project) — reuse it, don't create a second
   one; add a `PROJ-XXX` tag to it via the same JSON-RPC item-update path
   instead. No hit → create a new item.
3. **Add to Zotero (new items only):** `POST
   http://127.0.0.1:23119/connector/saveItems` with one item — `itemType`
   `preprint` for an arXiv-only record, `journalArticle`/`conferencePaper` when
   a venue is known, `patent` for PatentsView candidates; `title`, `creators`
   (split `Last, First` into `firstName`/`lastName`), `date`, `DOI`, `url`,
   `abstractNote`, and a `tags` entry for `PROJ-XXX`. If the PDF downloaded in
   step 1, attach it in the same call so Zotero holds its own copy.
4. **Read the record back:** Better BibTeX `item.citationkey` for the stable
   key (→ note frontmatter `zotero_key`), `item.export` (format `CSL-JSON`) for
   clean title/authors/date/DOI/abstract — these populate `## Referencia` and
   `## Resumen` below, replacing what step 6 used to take straight from the
   arXiv/Semantic Scholar/PatentsView response.
5. **Full text:** extract from the downloaded PDF when you have it (same
   process as before, independent of Zotero); otherwise fall back to the
   Zotero/CSL-JSON abstract and mark the note `fulltext: abstract-only`.

   **Source fields are never model-written.** `## Referencia`, `## Resumen` and
   `## Texto completo` hold only text taken from a fetched document or record:
   - `## Referencia`: built only from the fetched metadata (CSL-JSON, Crossref,
     arXiv, OpenAlex). A field that no source returned is left out, never
     filled in from memory.
   - `## Resumen`: the abstract **verbatim**, preceded by a `> Fuente: <URL or
     API>, obtenido <YYYY-MM-DD>` line. No source returned an abstract (null,
     429, paywall) → write `No disponible — ningún abstract recuperado
     (<sources tried>).` Never a summary, and never "from general knowledge".
   - `## Texto completo`: **verbatim excerpts** from the downloaded document,
     each quoted, under the paper's own section / figure / table headings (so
     locators can point at them), preceded by a `> Fuente:` line. No
     paraphrase, no restatement of the abstract. No document → `No disponible
     — solo abstract.` and nothing else.
   A field left empty and marked unavailable is correct. A plausible field
   written by the model is a fabricated source: every citation of it would be
   unverifiable, and the fresh verifier flags it `crítico`.
6. **Note:** dedup against existing `Papers/` notes by `zotero_key` first (a
   paper already ingested for another project has one), then DOI → arXiv id →
   title similarity, same as before.
   - **New:** create `Papers/<P-id> <short-title>.md` (next `P-XXXX`, scan
     `Papers/` frontmatter for the max), `projects: [<PROJ-XXX>]`.
   - **Exists (from another project):** append `<PROJ-XXX>` to its `projects:`
     list; refresh full text only if the note had none. If the existing note has
     `send: never`, don't open it (dedup by file name / `send_guard.py check`
     only): tell the researcher the paper is already in the vault but marked
     not-to-send, and ask whether to add `<PROJ-XXX>` by hand — never edit or
     re-derive it yourself.
7. Include a short **bibliographic section** (see Paper note format below).
8. **Code repository (`code_repo:`)** — record the paper's own public code
   repository when it is **confidently** identifiable; otherwise leave the
   field empty. This step only records a URL — it never clones, installs, or
   runs anything (that is `paper-to-tool`, on demand only). Look, strongest
   first:
   - the paper itself — arXiv comments / abstract, or the paper text ("code
     to reproduce our results is available at …", a `\section*{Code}`, a
     footnote). The sentence must claim the repo as **the paper's own code**:
     "our implementation is adapted from <repo>" or "we use X from <repo>"
     names a **dependency**, not the paper's code — never record that;
   - an author-stated link one hop away (the paper says "code at
     <project page>" and that page links exactly one repo in a code
     sentence);
   - Hugging Face Papers' `githubRepo` (the successor of Papers with Code,
     which has redirected there since 2025) **only as corroboration** — an
     entry with `githubRepoAddedBy: auto` is an automatic match and is never
     enough on its own.
   Same rule as the backfill script
   `${CLAUDE_PLUGIN_ROOT}/scripts/code_repo/find_code_repo.py` (run it with
   `--only <P-id> --json` to apply it mechanically). Fill `code_repo:` only
   at `alta` / `media` confidence with a single distinct repo, and write
   where it came from in `code_repo_evidence:`. Two candidate repos, or only
   an automatic match → leave `code_repo:` empty and list the candidates to
   the researcher at the end of the run (step 10). **Never guess a URL** (e.g. from
   the authors' GitHub handles or the title) — an empty field is the correct
   value when nothing is confidently identified.
9. **Resolve the reference** — prove the paper exists, matches the note's
   metadata, and is not retracted or withdrawn. Once the note is written, run:

   ```
   python "${CLAUDE_PLUGIN_ROOT}/scripts/citations/resolve_refs.py" --papers <vault>/Papers --only <P-id> --write
   ```

   It looks the paper up in OpenAlex (by DOI, else the arXiv DOI, else a title
   search whose hit must pass the metadata match), cross-checks Crossref /
   arXiv (and Semantic Scholar if OpenAlex has nothing), fuzzy-compares title,
   first-author surname and year, and runs the shared retraction/withdrawal
   check. It writes `resolved`, `openalex_id`, `resolution_checked`,
   `resolution_status`, `resolution_match`, `resolution_evidence` into the
   note's frontmatter (see Paper note format) and nothing else. The note is
   **ingested regardless of the outcome** — don't delete or block on it — but
   any `resolution_status` other than `resolved` (`unresolved`, `mismatch`,
   `retracted`, `withdrawn`) is listed at the end of the run (step 10) with the
   severity the script printed (at least `importante`; `mismatch` / `retracted`
   / `withdrawn` / not-found-anywhere are `crítico`). A `mismatch` means the
   note mixes metadata of two papers (a "chimeric" citation) — fix it by hand
   from the sources the script names; never "fix" it by trusting one source
   blindly (a `mismatch` also covers a DOI and arXiv id that point to two
   different works). A note missing its first author or year stays
   `unresolved` ("note lacks author/year — cannot prove match", `importante`)
   until they are added. A `send: never` note is skipped entirely (nothing is sent).
   **OpenAlex API key:** `OPENALEX_API_KEY` is optional — keyless OpenAlex
   singleton lookups are free (checked 2026-09-24) — but the keyless daily
   budget is small ($0.10). If OpenAlex is unreachable or the budget is spent,
   the lookup is LOST: the script does **not** write `resolved` /
   `openalex_id` / `resolution_checked` / `resolution_status` (they keep their
   previous values, or stay absent on a new note) and only appends
   `last check LOST <date>: …` to `resolution_evidence` — so go by the
   script's output (it reports the paper as `unresolved`, `importante`), not
   by the fields. Flag that paper `importante` in step 10 and tell the researcher to get a free key at
   `https://openalex.org/settings/api`, export `OPENALEX_API_KEY` in the
   environment that runs Claude Code (never commit it), and re-run the command
   above.

**Zotero unreachable (not running, or "allow other applications" disabled):**
don't silently skip it and don't block ingestion either — tell the researcher
plainly (e.g. "⚠️ Zotero unavailable — P-00NN ingested without a Zotero
record"), fall back to building the note directly from the
arXiv/Semantic-Scholar/PatentsView response as before v1, and leave
`zotero_key` out of that note's frontmatter (not empty-string — omitted, so a
later retrofit pass can find these by the field's absence).

### 7. Generate `Projects/<slug>/Estado-del-arte.md` (map-reduce via subagents)

**Map — `facet-summarizer` subagents, in parallel.** For each facet from step 4,
assign it the ingested papers that `literature-search` recorded as matching that
facet — **reuse the per-candidate `matched:` record from the ranked list; do not
re-derive facet membership.** Then **dispatch one `facet-summarizer` subagent per
facet, all launched together in the same turn**, each given its facet + the
explicit list of `Papers/P-XXXX ….md` note paths assigned to it + the project
`type`. **Never assign a `send: never` note** (check the candidate list with
`python "${CLAUDE_PLUGIN_ROOT}/scripts/security/send_guard.py" check <paths…>`;
exit 3 names the flagged ones): it stays in `Papers/` but contributes nothing to
the map, and the end-of-run message lists it as `menor` (`P-XXXX omitida del
Estado del arte: send: never`). Each subagent reads **only its assigned notes** and returns a compact,
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

**Citation consistency during the merge — the specific failure this guards
against.** If a fact being merged restates something already cited elsewhere
in the document you're assembling (same paper, same or adjacent claim — two
facets both describing what one paper says about the same mechanism, or the
same numeric result showing up under both "Línea de evolución" and "Lo
establecido"), **do not re-derive the citation independently for the second
occurrence.** Look up the locator already used the first time and reuse it
verbatim. If the two occurrences disagree on the section number, that
disagreement is itself the signal that one of them is wrong — stop and
re-verify both against the source `Papers/P-XXXX.md` `## Texto completo`
before writing either one; do not resolve the conflict by just picking
whichever number was written down first. Never let the same fact carry two
different section citations in the finished document.

**If `literature-search` returned a `## ⚠️ Cobertura degradada` block** (a facet
lost its anchor or relevance pass), reproduce it **verbatim at the very top of
`Estado-del-arte.md`**, directly under the frontmatter — not only inside the
appended Búsqueda ejecutada block at the bottom. The reader must see, before the
synthesis, that part of the literature was not covered. Also mention it in the
one-paragraph summary you give the user when the skill finishes.

**Frontmatter:** set `last_updated: <YYYY-MM-DD>` (today), and `generated_by:` —
`origin: agent`, `model: <this session's model id>`, `skill_version:
create-project@<plugin version>`, `summarizer_model: <facet-summarizer's
model>` — so `assemble-manuscript`'s AI-use disclosure can state who wrote the
synthesis instead of "no consta". Never fill it with a guess; if a model id is
not known to the session, omit that key (absent reads as "no consta"). `update-confidence`
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
go in. **Before writing (or copying forward from a subagent's contribution) any
such locator, re-open that exact heading in the source `Papers/P-XXXX.md` note
and confirm the sentence paraphrases what's under it — not the paper in
general, and not a similar-looking citation used earlier in this document.**
This applies to §4 and §8, which the Reduce pass drafts itself, exactly as it
applies to merging subagent contributions.

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

In the end-of-run message, list any paper whose `code_repo:` was left empty
with candidates still on the table (step 6.8: a conflict, or only an automatic
match), so the researcher can confirm one by hand with `find_code_repo.py
--confirm` or leave it empty.

Also list every paper whose `resolution_status` is not `resolved` (step 6.9),
one line each with its severity, status and the script's reason — e.g.
`[crítico] P-0017 mismatch — first_author differs (openalex, crossref); posible
cita quimérica, corregir a mano` or `[importante] P-0018 unresolved — OpenAlex
unreachable; configure OPENALEX_API_KEY (https://openalex.org/settings/api) and
re-run resolve_refs.py --only P-0018 --write`. These papers are ingested, but
must not be cited as support until resolved (a `retracted` / `withdrawn` paper
never is).

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
zotero_key: <Better BibTeX citekey, or Zotero's raw item key if BBT was
  unreachable — omit the field entirely if Zotero itself was unreachable>
code_repo: <https://github.com/<owner>/<repo> — the paper's OWN public code
  (step 6.8), or empty if not confidently identified. Never guessed.>
code_repo_evidence: <"where it was found", e.g. "arXiv comments: 'Code
  available at …'" — empty when code_repo is empty>
# Citation resolution (step 6.9, written only by scripts/citations/resolve_refs.py
# --write or retraction_sweep.py --write — never by hand). All absent = never checked.
resolved: <true | false — true only with a matching OpenAlex record (the paper
  exists); false = checked and not found, ambiguous (mismatch, incl. a DOI and
  arXiv id that point to different works), confirmed only by a fallback source,
  or the note lacks author/year. A lookup LOST to errors/budget never writes
  these fields (the previous values stay; the loss goes in resolution_evidence).
  A retracted paper that exists is still `true` — see resolution_status>
openalex_id: <W followed by digits, e.g. W2741809807 — empty when resolved is false>
resolution_checked: <YYYY-MM-DD of the last check, updated on every re-check>
resolution_status: <resolved | unresolved | mismatch | retracted | withdrawn —
  the full outcome; anything but `resolved` is flagged at the end of the run>
resolution_match: <exact | close | mismatch — title / first author / year vs
  the sources; empty when no source record matched>
resolution_evidence: <"one line: which sources, what differed or was flagged">
# send — optional, set only by the researcher. `send: never` = this note's
# content and metadata must never reach the model or an external API: no
# skill reads, summarizes, cites or resolves it, and the vault's send_guard
# PreToolUse hook blocks reading it. Omit the field for normal notes.
send: <never — or omit>
---

## Referencia

<One-line bibliographic citation built only from fetched metadata. DOI/arXiv
id. Open-access status.>

## Resumen

> Fuente: <URL or API the abstract came from>, obtenido <YYYY-MM-DD>

<Abstract VERBATIM — or exactly "No disponible — ningún abstract recuperado
(<sources tried>)." Never a model-written summary (step 5).>

## Texto completo

> Fuente: <PDF / HTML the excerpts came from>, obtenido <YYYY-MM-DD>

<Verbatim, quoted excerpts under the paper's own section / figure / table
headings — or exactly "No disponible — solo abstract." Never paraphrase.>
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
- **Citing a section from memory of a similar earlier citation instead of
  re-reading it.** A cited-but-wrong locator is worse than an obviously
  missing one — it looks verified and isn't. Re-open the exact heading every
  time, even for a paper you just cited two paragraphs ago.
- **The same fact carrying two different section numbers in one document.**
  If a fact restates something already cited elsewhere in this
  `Estado-del-arte.md`, reuse that exact locator — don't re-derive a fresh
  one that can drift from it.
- **Renumbering the SOTA sections when §4/§9 are omitted.** Keep the canonical
  titles and order; just leave the conditional ones out.
- **More than 5 seed hypotheses on the first pass.** Cap at 5.
- **Promoting a seed hypothesis past `propuesta`.** Not this skill's job.
- **Duplicating a `Papers/` note that already exists for another project.**
  Append to `projects:` instead.
- **Duplicating a Zotero item that already exists for another project.** Same
  principle as the note-level dedup, one level earlier: `item.search` by
  DOI/arXiv id/title *before* `saveItems`, and tag the existing item with the
  new `PROJ-XXX` instead of creating a second Zotero record for the same paper.
- **Blocking ingestion because Zotero is down.** Degrade and flag it (see step
  6) — a missing optional companion doesn't stop the pipeline.
- **Paywall-scraping a closed-access PDF.** Open-access only; else abstract-only.
- **Filling a source field the sources left empty.** No abstract returned → the
  `## Resumen` says so and stays empty. A "summary from general knowledge", a
  restatement of the abstract as `## Texto completo` bullets, or a paraphrase
  of the PDF is model-written text dressed as a source. Every claim that cites
  it is unverifiable. Verbatim or nothing (step 5).
- **Recording a dependency as `code_repo`.** A repo the paper *uses*
  ("adapted from", "we use X from") is not the paper's code. And an automatic
  Hugging Face match alone is not confident — leave the field empty.
- **Cloning or running a paper's `code_repo` during ingestion.** Ingestion
  records the URL only; extraction is `paper-to-tool`, on explicit request.
- **Skipping reference resolution, or hiding its result.** Every ingested paper
  gets `resolve_refs.py --only <P-id> --write` (step 6.9). An unresolved /
  mismatched / retracted paper is still ingested but always surfaces in the
  end-of-run message — never silently.
- **Reading, assigning or citing a `send: never` note.** It is skipped
  explicitly (steps 6–7) — never worked around through another tool when the
  `send_guard` hook refuses a read.
- **Hand-writing the resolution fields.** `resolved` / `openalex_id` /
  `resolution_*` come only from the script; `resolved: true` without an OpenAlex
  id breaks the contract other skills rely on.

## Not in v1

- Automatic Zotero sync (create Obsidian notes directly; Zotero is a fast-follow).
- Promotion / experiment scaffolding (separate skills).
