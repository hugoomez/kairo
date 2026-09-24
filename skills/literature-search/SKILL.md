---
name: literature-search
description: Use when finding external papers or patents for a hypothesis, project, or research question in a Kairo vault — keyword search across arXiv, Semantic Scholar, and (for producto/hibrido projects) patents, plus citation-graph snowballing. Covers which public endpoints to query (arXiv and Semantic Scholar need no API key; patents now do) and how to run the multi-facet search-and-rank pipeline.
---

# Literature Search

## Overview

Search external literature (papers + patents) from a plain-language description of
what you're looking for, and return a ranked, deduplicated, justified candidate
list. All primary sources are queryable **without an API key**; PatentsView is the
exception and is only used for `producto`/`hibrido` projects (see below).

Core Kairo principle: **every hypothesis must cite specific papers**.
This skill produces those citations; it does not evaluate them. Relevance
judgement and the one-sentence justification per candidate are still your job.

**Scope limit — disclose it.** This search covers arXiv, Semantic Scholar, the
vault, and (for `producto`/`hibrido`) US patents. It does **not** reach grey
literature (theses, technical reports, non-indexed preprints, industry
whitepapers) and does nothing to correct for **publication bias**. It is a
targeted evidence sweep, not a systematic review. Say so when handing results to
a hypothesis or a reviewer, and treat the `linea_publicacion: true` deep-search
path (below) as the minimum bar for anything heading toward publication.

## When to use

- Building or revising a hypothesis note that needs paper citations.
- Seeding a new project or `Papers/` entry from a topic description.
- Snowballing from a seed paper the user already has ("find related work to X").
- Prior-art scans for `Producto/` decisions (patents included).

**When not to use:** the user already handed you the exact papers; you only need
to search *inside* the vault (use Smart Connections MCP directly); the task is
reading/summarizing one known paper.

## Sources

### 1. arXiv — public Atom API

- **Endpoint:** `http://export.arxiv.org/api/query` (GET)
- **Auth:** none.
- **Rate limit:** arXiv asks for a **3-second delay between calls**. Serialize
  arXiv requests; never fire them in parallel with each other.
- **Response:** Atom 1.0 XML. Parse the XML, not JSON.

Key query parameters:

| Param | Meaning |
|---|---|
| `search_query` | field-prefixed query: `all:`, `ti:`, `abs:`, `au:`, `cat:`; combine with `AND`/`OR`/`ANDNOT`, group with `%28 %29` |
| `id_list` | comma-separated arXiv IDs to fetch specific papers |
| `start` | 0-based offset for paging |
| `max_results` | page size; ≤ 2000 per call, 30000 total |
| `sortBy` / `sortOrder` | `relevance` \| `lastUpdatedDate` \| `submittedDate` ; `ascending` \| `descending` |

Fields to extract from each `<entry>`:

| Wanted | Atom path |
|---|---|
| title | `entry/title` (collapse internal whitespace) |
| abstract | `entry/summary` |
| authors | each `entry/author/name` |
| arXiv id | `entry/id` → strip `http://arxiv.org/abs/`, keep trailing version or drop `vN` for dedup |
| abstract page | `entry/id` |
| PDF link | `entry/link[@title="pdf"]/@href` (fallback: `entry/link[@type="application/pdf"]`) |
| DOI (if any) | `entry/link[@title="doi"]/@href` or `arxiv:doi` |
| dates | `entry/published`, `entry/updated` |
| primary category | `arxiv:primary_category/@term` |

Example request:

```
http://export.arxiv.org/api/query?search_query=abs:%22reinforcement+learning%22+AND+cat:cs.LG&start=0&max_results=25&sortBy=relevance
```

### 2. Semantic Scholar — public Graph API

- **Base:** `https://api.semanticscholar.org/graph/v1`
- **Auth:** none required for reasonable use. Un-keyed traffic shares one global
  rate pool, so throttle to **~1 request/second**, retry `429` with exponential
  backoff, and keep bursts small. A free personal key raises the limit and is
  worth setting when `facet-searcher` subagents run concurrently — see
  **Configuración opcional** below; it is never required.

**Keyword search** — `GET /paper/search`:

| Param | Meaning |
|---|---|
| `query` | required; plain keywords (no field syntax) |
| `fields` | comma list, e.g. `title,abstract,authors,year,externalIds,citationCount,referenceCount,openAccessPdf,venue` |
| `offset` / `limit` | paging; `limit` ≤ 100 |
| `year`, `fieldsOfStudy`, `venue`, `openAccessPdf` | optional filters |

`externalIds` carries `DOI`, `ArXiv`, `PubMed`, etc. — use it to cross-link
arXiv hits with Semantic Scholar records during dedup.

For large sweeps use `GET /paper/search/bulk` (up to 1000/page, boolean query
support, but ranked only by citation count).

**Citation-graph snowballing** — paper details endpoint:

- `GET /paper/{paper_id}` with
  `fields=references.title,references.externalIds,references.year,references.citationCount,citations.title,citations.externalIds,citations.year,citations.citationCount`
- or the paginated `GET /paper/{paper_id}/references` and
  `GET /paper/{paper_id}/citations` (each takes `fields`, `offset`, `limit`).

`{paper_id}` accepts `arXiv:2311.01234`, `DOI:10.1145/...`, `CorpusId:...`, or
the S2 SHA id — so you can snowball directly from an arXiv hit without an extra
lookup. **References** = what this paper cites (look backward). **Citations** =
newer papers citing it (look forward).

### 3. PatentsView — `producto` / `hibrido` projects only

Only run patent search when the target project's `projects:`/type is `producto`
or `hibrido`. Skip entirely for pure research projects.

> **Endpoint change — verified 2026-09-05.** The old keyless
> `api.patentsview.org` host is retired — `api.patentsview.org/patents/query`
> now returns `301 Moved Permanently` to the USPTO transition guide. It was
> replaced by the **PatentSearch API** at `https://search.patentsview.org/api/v1`,
> which **requires a free API key** sent as an `X-Api-Key` header (request form
> linked from `patentsview.org`). Separately, `patentsview.org/apis/*` also
> redirects to the **USPTO Open Data Portal** (`data.uspto.gov`), which
> PatentsView is being folded into; ODP access needs a free USPTO.gov account
> with MFA. **No fully keyless US patent API remains** (EPO OPS and WIPO
> alternatives also need free registration). If neither a PatentsView key nor a
> USPTO.gov account is configured, tell the user and skip patents rather than
> guessing.

With a key configured:

- **Base:** `https://search.patentsview.org/api/v1`
- **Endpoints** (POST or GET): `/patent/`, `/assignee/`, `/inventor/`, plus CPC
  and location endpoints. Names are singular.
- **Query shape:** `q` = JSON query, `f` = JSON list of return fields, `o` =
  options (`size`, pagination via `after`), `s` = sort list.
- **Keyword search:** `{"_text_any": {"patent_abstract": "wound dressing hydrogel"}}`
  or `_text_phrase` for exact phrases; title field is `patent_title`.
- **Assignee search:** `{"_contains": {"assignees.assignee_organization": "Acme"}}`.
- Fields use `g_`/`_` prefixes now (e.g. `patent_id`, not `patent_number`).

Example body:

```json
{
  "q": {"_and": [
    {"_text_any": {"patent_abstract": "hydrogel wound dressing"}},
    {"_gte": {"patent_date": "2019-01-01"}}
  ]},
  "f": ["patent_id", "patent_title", "patent_date", "patent_abstract",
        "assignees.assignee_organization"],
  "o": {"size": 25}
}
```

### 4. Crossref — retraction check (any candidate with a DOI)

- **Endpoint:** `https://api.crossref.org/works/{doi}` (GET, no key; send a
  `User-Agent` / `mailto` for the polite pool).
- **Purpose:** flag retractions before a paper reaches the ranked list. Crossref
  has integrated the **Retraction Watch Database** since 2023, so its record is
  the single check to run **for DOI-bearing candidates**.
- **Fields:** `message.is-retracted` (boolean when present) and
  `message.update-to[]` — a `type` of `retraction`, `withdrawal`,
  `removal`, or `expression_of_concern` pointing at this DOI means it is
  retracted / concerned. Also check `message.updated-by[]` on the DOI itself.
- **Rate limit:** be gentle; batch one lookup per unique DOI, ~a few rps.
- **Coverage gap it leaves:** a preprint-heavy pool is mostly DOI-less. Crossref
  alone then checks almost nothing — pair it with the arXiv withdrawal check
  below.

### 5. arXiv — withdrawal check (any candidate with an arXiv id)

Most arXiv preprints have no DOI, so Crossref never sees them. arXiv has its own
"withdrawn" state (the authors retract the submission), and it must be checked
directly for every candidate carrying an arXiv id — especially in a pool that is
mostly arXiv, where DOI-only checking covers next to nothing.

- **Endpoint:** `http://export.arxiv.org/api/query?id_list=<arxivid>` (GET, no
  key; the same 3-second serial discipline as any arXiv call — batch multiple ids
  into one `id_list` of up to ~100).
- **What "withdrawn" looks like** (arXiv exposes no boolean field for it — detect
  from text, case-insensitive):
  - `entry/arxiv:comment` contains `withdrawn` / `this submission has been
    withdrawn` / `paper withdrawn` — the usual signal;
  - `entry/title` begins `Withdrawn:`;
  - `entry/summary` (latest version) is replaced by a short withdrawal notice
    ("This paper has been withdrawn by the author(s)", often citing an error).
  - A later version number alone is **not** a withdrawal — only the notice text
    counts.
- **Result:** treat a withdrawn preprint exactly as a retracted paper in step 4a
  — dropped from the ranked list, or kept-but-flagged `WITHDRAWN` in bold if it
  is itself the object of study, never counted as support. Record how many
  arXiv-id candidates were checked and how many were removed.

### Internal vault — Smart Connections MCP

Always search the vault first and in parallel with the external sources:

- `mcp__smart-connections__search_by_text` — freeform semantic search over
  existing notes (`query`, `limit`, `threshold`).
- `mcp__smart-connections__search_similar` / `search_by_embedding` — find notes
  near a given note or vector.
- `mcp__smart-connections__get_note` — pull a matched note's content.

An empty result set is normal for a sparse vault — proceed with the external
hits.

## Search pipeline

Run these five steps in order. Steps 1–2 fan out; 3–5 converge. Every step feeds
the **Búsqueda ejecutada** block (see the template after this section) — a short
reproducibility record the caller saves alongside the results.

### 1. Decompose the description into facets, and fix the criteria

Break the input into **2–5 independent facets** — the orthogonal concepts that a
paper must combine to be relevant (method, application domain, data modality,
constraint, outcome). For each facet, list **known synonyms / near-terms**
(acronym ↔ expansion, British/US spelling, task name ↔ metric name). Keep facets
independent: if two "facets" always co-occur they're one facet.

Then, **before any query**, write down — into the Búsqueda ejecutada block —
today's date, the facet table, and the **inclusion / exclusion criteria** derived
from the facets. Be explicit, e.g.:

- *Include:* peer-reviewed or preprint; reports an empirical result or a method on
  facet A ∧ facet B; any year unless the description says otherwise.
- *Exclude:* off-topic on any hard facet; survey/opinion with no primary result
  (unless the task is a landscape scan); retracted / withdrawn (see step 4);
  non-English without an English abstract; patents when `type` is `ciencia`.
- *Fuera de alcance (scope):* if the caller supplied the project's **`Alcance:
  Fuera`** clauses (or an explicit out-of-scope list), record each as a **hard
  scope-exclusion criterion**, tracked **separately** from relevance. A paper can
  be highly relevant to the description **and** out of scope — that is a
  deliberate exclusion with a citable reason, not a low-relevance drop.

The criteria are frozen here and applied consistently in steps 4–5 — not
improvised per candidate.

### 2. Search each facet in parallel — via `facet-searcher` subagents

**Dispatch one `facet-searcher` subagent per facet, all launched together in the
same turn** (real parallelism — not one after another). Each subagent gets:

- its facet term + synonym list;
- the sources to query: `vault`, `arxiv`, `semantic-scholar`, and
  `patentsview` **only if** the project is `producto`/`hibrido` and a key is
  configured (say so explicitly, or omit it);
- any seed papers relevant to that facet (for a shallow 1-hop expansion);
- year / recency hints if the description gives them.

The subagent runs the actual queries in its **own isolated context**, enforcing
arXiv's ~1-request/3s discipline and Semantic Scholar's ~1 rps throttle itself,
and returns **only a compact candidate list** — title, authors, year, ids
(doi/arxiv/patent), url, a one-line summary, source, and which facet
term/synonym matched. **The calling session never receives raw Atom XML or raw
Semantic Scholar JSON** — parsing lives entirely inside each subagent.

When **no seed papers** are supplied for a facet, the subagent also runs a
second Semantic Scholar pass on the **`/paper/search/bulk`** endpoint with
server-side `sort=citationCount:desc` over the whole matching pool, and tags the
top results `anchor_candidate: true` — this is how the field's foundational
papers get in despite arXiv's weak relevance sort. When seed papers *are*
supplied, that pass is skipped (snowballing covers anchors). Carry the
`anchor_candidate` flag through to step 4.

The subagent retries `429`/`5xx` on **both** Semantic Scholar passes (relevance
and anchor) with backoff before giving up — the anchor pass is not exempt. A pass
it still can't complete comes back as a structured `coverage:` line
(e.g. `anchor pass LOST (HTTP 500 x3)`), distinct from a pass deliberately not
run.

Collect every subagent's list. From each subagent's header:

- copy the **verbatim query strings + raw hit counts** into the Búsqueda
  ejecutada block's *Consultas* table, and fold in any `notes` line (source
  skipped / needed a key / truncated);
- read the **`coverage:`** line. Any facet whose `coverage:` reports a pass
  **LOST** to errors (anchor pass or relevance pass) is a **degraded-coverage
  event** — record it for the prominent warning in step 5, not only as a row in
  the query table;
- if **two or more facets** report a Semantic Scholar `429` (in `coverage:` or
  `notes`), that is the trigger for the proactive API-key recommendation in
  step 5.

> Build the facet query as `(term OR synonym OR …)` — one OR-group per source per
> facet, not one call per synonym. Page sizes ~25. Facets stay separate (partial-
> match recall now; precision comes at ranking).

### 3. Expand via citation snowballing

Stays in the main session — it works on the small compact lists the subagents
returned, and its handful of Semantic Scholar calls are made with **WebFetch + an
extraction prompt** (same discipline: the parsed reference/citation list comes
back, never raw JSON).

Take the **strongest hits** from step 2 (high relevance to the full description,
not just one facet) plus **any seed papers the user supplied**. For each:

- pull **references** (backward) and **citations** (forward) via Semantic Scholar,
- keep snowballed items that match **≥ 2 facets**,
- **one hop by default**; a second hop only from a snowballed item that itself is
  a strong multi-facet match.

The hop cap is a **deliberate cost/rigor tradeoff, not the full method.**
Wohlin-style snowballing iterates backward and forward *to closure* — you keep
adding hops until a round yields no new in-scope papers. The 1-hop default trades
that completeness for speed and is adequate for hypothesis-seeding. For
`linea_publicacion: true` work, or on explicit request, run the **deep search**
instead: snowball to closure (log the hop count reached), and note in the
Búsqueda ejecutada block that closure was the stopping rule.

### 4. Retraction check, deduplicate, then rank

**4a. Retraction / withdrawal check.** Two complementary checks — run **both**,
because a preprint-heavy pool is mostly DOI-less and each check sees only part of
the pool:

- **DOI-bearing candidates → Crossref** (`api.crossref.org/works/{doi}`): inspect
  `is-retracted` / `update-to` / `updated-by` (see the Crossref source entry).
- **arXiv-id-bearing candidates → arXiv withdrawal check** (`export.arxiv.org/api/query?id_list=…`,
  batched): inspect `arxiv:comment` / `title` / `summary` for a withdrawal notice
  (see source entry 5). A candidate with both a DOI and an arXiv id is checked
  on both.

A paper flagged by **either** check is **dropped** from the ranked list (or, if
it is itself the object of study, kept but flagged `RETRACTED` / `WITHDRAWN` in
bold and never counted as support). Record, **per check**, how many candidates
were checked and how many were removed — as exact integers.

**4b. Dedup key**, in priority order: normalized DOI → arXiv id
(version-stripped) → normalized title similarity (lowercase, strip punctuation,
fuzzy match ~0.9). Merge duplicates into one record, keeping the richest metadata
and all source links.

**4c. Rank** surviving records by relevance to the **full original description**
(all facets weighted together — a paper hitting every facet outranks one that
saturates a single facet), then by **recency**, then by **citation count**.

Citation count is a **weak positive signal that decays with age**: for papers
< ~18 months old, show the count as a **separate flagged signal**
("recent — 4 citations, low-confidence") rather than folding it into the score,
so genuinely new work isn't buried under older, heavily-cited papers.

**Anchor candidates.** A record tagged `anchor_candidate: true` (from a
`facet-searcher`'s citation-sorted pass) **stays in the ranked list even if its
direct relevance-to-description score is below the cutoff** that would otherwise
drop it. Annotate it in the list: *«candidato ancla por citas, no por relevancia
directa»*. It still passes through 4a (retraction) and 4b (dedup) unchanged — the
flag only overrides the **relevance** cutoff, nothing else (a scope exclusion, 4d,
still removes it). If it *also* clears the cutoff on its own merits, drop the
annotation and rank it normally.

**4d. Classify every exclusion — scope vs. relevance are not the same drop.**
Each candidate that will **not** be in the recommended set carries exactly **one
primary reason**:

| Reason | Meaning |
|---|---|
| `fuera de alcance` | matched an `Alcance: Fuera` clause (step 1). **Cite the clause.** Applies regardless of how relevant the paper is. |
| `relevancia baja` | in scope, but below the relevance-to-description cutoff |
| `fuera de tema` | fails a hard facet (not on-topic at all) |
| `solo survey` / `retractado/retirado` / `duplicado` / `sin justificación` | the other frozen exclusion criteria |

A candidate excluded **only** by `fuera de alcance` — i.e. it would otherwise
rank well — is listed in a visible **`### Relevante pero fuera de alcance`**
sub-section under the ranked list, each with its one-sentence relevance note
**and** the scope clause it hit. It is never silently folded into the
`relevancia baja` tail: the researcher must be able to see that something
relevant was set aside on purpose, and why.

### 5. Justify every survivor, then report the counts

For **each** candidate that survives step 4, write **one sentence** stating why
it's relevant to the original description — which facets it satisfies and what it
contributes (method / evidence / prior art / contradiction). If you can't write
that sentence honestly, or it fails the frozen exclusion criteria, drop the
candidate.

Carry any `code_hint` a `facet-searcher` returned (the paper's own repo, as
stated in its arXiv comment/abstract) through to the ranked list unchanged.
It is a hint for ingestion (`create-project` step 6.8), which confirms it and
fills `code_repo:`; this skill neither verifies nor records it, and never
clones or runs anything.

Then finish the **Búsqueda ejecutada** block with PRISMA-style counts and present
both the block and the ranked list:

- **Identificados** — raw hit count per source (from step 2).
- **Tras deduplicación** — records after step 4b.
- **Tras cribado por retracción/retirada** — after step 4a.
- **Incluidos** — candidates that passed the step-5 justification gate.

State the drop reason tally, keeping **`fuera de alcance` and `relevancia baja`
as separate lines** (never summed): fuera de tema, solo survey,
retracted/withdrawn, duplicado, sin justificación, **fuera de alcance**,
**relevancia baja**. If the `### Relevante pero fuera de alcance` sub-section has
any entries, say how many.

**Counts are exact integers.** This skill exists for PRISMA-style traceability —
every count above is a deterministic set operation on lists you hold in hand, so
an exact number always exists. Never write `≈95`, `~95`, `about 95`, or a range.
If you find yourself wanting to approximate, the merged candidate list is
incomplete — fix that, don't round.

**Degraded-coverage warning (prominent).** If any facet's `coverage:` (step 2)
reported a pass **LOST** to errors — the anchor pass or the relevance pass failed
for that facet after retries — the results have a known blind spot. Put a
**`## ⚠️ Cobertura degradada`** block at the **top** of what you hand back (above
the ranked list, and carried into `Estado-del-arte.md` by the caller), not merely
a row in the *Consultas* table. One line per affected facet × pass, e.g.:

> **Faceta D — pase-ancla perdido (HTTP 500 ×3).** Los papers fundacionales de
> esta faceta pueden faltar del conjunto. Mitigación: sembrar manualmente un
> paper semilla de la faceta y re-ejecutar, o configurar `SEMANTIC_SCHOLAR_API_KEY`
> y repetir la faceta.

If no pass was lost, omit the block (don't add a "coverage OK" line — absence is
the signal).

**Proactive Semantic Scholar key recommendation.** If **two or more facets** hit
`HTTP 429` on Semantic Scholar in this run (from the `coverage:` / `notes` lines
in step 2), the shared keyless pool is the bottleneck **now** — say so directly
in the report, not only via the static docs:

> Varias facetas (`<list>`) recibieron `HTTP 429` de Semantic Scholar en esta
> ejecución. Configura una API key gratuita (`SEMANTIC_SCHOLAR_API_KEY`, ver
> **Configuración opcional**) y puedo re-ejecutar las facetas afectadas sin el
> límite del pool compartido.

Offer to re-run the affected facets once the key is set.

## Búsqueda ejecutada — block template

The caller (usually `create-project` or a hypothesis `Justificación`) saves this
next to the results:

```markdown
### Búsqueda ejecutada — <YYYY-MM-DD>

<!-- Only if a pass was LOST to errors — put this FIRST, above everything, and
     the caller mirrors it into Estado-del-arte.md. Omit entirely if nothing failed. -->
## ⚠️ Cobertura degradada
- Faceta <X> — <pase-ancla | pase de relevancia> perdido (<HTTP code ×n>).
  <qué falta y cómo mitigarlo>

**Descripción original:** <verbatim>
**Facetas:** <facet table>
**Criterios de inclusión:** <list>
**Criterios de exclusión:** <list>
**Modo:** targeted (1-hop)  |  deep (snowball a cierre, N hops)

**Consultas (verbatim):**
| faceta | fuente | query | hits crudos |
|--------|--------|-------|-------------|
| …      | arXiv  | `…`   | 42          |

**Conteos** (enteros exactos, nunca aproximados):
- Identificados: <sum per source>
- Tras deduplicación: <n>
- Tras cribado por retracción/retirada: <n>
  - Crossref (DOI): revisados <n>, retirados <n>
  - arXiv (withdrawn): revisados <n>, retirados <n>
- Incluidos: <n>
- Motivos de descarte: fuera de tema <n>, solo survey <n>, retractado/retirado <n>, duplicado <n>, sin justificación <n>, fuera de alcance <n>, relevancia baja <n>
- Relevante pero fuera de alcance: <n>  (listados aparte bajo la lista rankeada)
```

## Quick reference

| Source | Endpoint | Key? | Throttle | Format |
|---|---|---|---|---|
| Internal | `mcp__smart-connections__search_by_text` | n/a | n/a | JSON |
| arXiv | `http://export.arxiv.org/api/query` | no | 3 s between calls, serial | Atom XML |
| Semantic Scholar search (relevance) | `https://api.semanticscholar.org/graph/v1/paper/search` | no* | ~1 rps, retry 429 + 5xx w/ backoff (3 attempts) then degrade | JSON |
| Semantic Scholar bulk (anchor pass) | `.../graph/v1/paper/search/bulk` — boolean query, `sort=citationCount:desc`, `year`, token pages ≤ 1000 | no* | ~1 rps, retry 429 + 5xx w/ backoff (3 attempts) then degrade — **not exempt** | JSON |
| Semantic Scholar snowball | `.../graph/v1/paper/{id}` + `references`/`citations` | no* | ~1 rps | JSON |
| Crossref retraction check | `https://api.crossref.org/works/{doi}` | no | polite pool; ~few rps | JSON |
| arXiv withdrawal check | `http://export.arxiv.org/api/query?id_list=…` | no | 3 s serial; batch ids | Atom XML |
| PatentsView (producto/hibrido) | `https://search.patentsview.org/api/v1/patent/` | **yes, free** | per API throttle | JSON |

`*` Semantic Scholar works keyless; an optional free key raises the shared-pool
limit — see **Configuración opcional**.

## Common mistakes

- **Dispatching the `facet-searcher` subagents one at a time.** Launch all facets
  in a single turn — serial dispatch throws away the whole point of the split.
- **Letting a raw source payload reach the main session.** Atom XML / Semantic
  Scholar JSON is parsed inside each subagent (step 2) or by WebFetch's
  extraction prompt (step 3). Only compact structured lists cross back.
- **Parsing arXiv as JSON.** It's Atom XML only, errors included.
- **Hammering arXiv in parallel.** 3-second serial delay; batch your `id_list`
  instead of looping single IDs.
- **Using `api.patentsview.org`.** Retired — use `search.patentsview.org/api/v1`
  with an `X-Api-Key`, or skip patents.
- **Running PatentsView for a research-only project.** Gate on
  `producto`/`hibrido`.
- **Ranking by citation count first.** Relevance to the *full* description leads;
  citation count is a tie-breaker and is unreliable for recent papers — show it
  separately there.
- **Collapsing dependent facets.** If two terms always appear together, they're
  one facet; you'll over-constrain the search.
- **Showing candidates before writing justifications.** Step 5 is a gate, not a
  formatting nicety.
- **Skipping the vault search.** Check for existing `Papers/` notes first to
  avoid duplicate entries.
- **Ingesting a candidate yourself.** This skill ranks and justifies; it never
  writes to `Papers/` or Zotero. Ingestion — including the Zotero-first add —
  is `create-project` step 6's job. Calling this skill directly (e.g. from
  `hypothesis-cycle`'s novelty check) never triggers ingestion as a side
  effect.
- **Not freezing the queries / criteria.** Inclusion-exclusion criteria and the
  verbatim query strings go into the Búsqueda ejecutada block *before* searching
  and *during* step 2 — not reconstructed afterward.
- **Skipping the Crossref retraction check.** Any DOI-bearing candidate is checked
  before ranking; a retracted paper never counts as support.
- **DOI-only retraction checking on an arXiv-heavy pool.** Crossref sees only
  DOI-bearing candidates. Run the arXiv withdrawal check (source 5) on every
  arXiv-id candidate too, or a preprint pool goes essentially unchecked.
- **Letting the anchor pass fail without a retry.** `429`/`5xx` on the
  citation-sorted anchor pass gets the same 3-attempt backoff as every other S2
  call — it is not exempt. A lost anchor pass loses the facet's foundational
  papers.
- **Burying a lost pass in the query table.** A pass LOST to errors goes in the
  `## ⚠️ Cobertura degradada` block at the top of the results and into
  `Estado-del-arte.md`, not just as an `ERROR` row nobody reads.
- **Approximate counts.** `≈95` / `~136` breaks PRISMA traceability. Every count
  is an exact integer computed from lists in hand.
- **Sitting on repeated 429s.** Two or more facets 429'd → recommend the free
  `SEMANTIC_SCHOLAR_API_KEY` to the user in this run's report and offer to re-run
  the affected facets — don't leave it to the static docs.
- **Silently dropping a relevant-but-out-of-scope paper.** `fuera de alcance` and
  `relevancia baja` are different drops (step 4d). A paper cut only by scope is
  listed under `### Relevante pero fuera de alcance` with the clause it hit, never
  merged into the low-relevance tail.
- **Presenting 1-hop snowballing as exhaustive.** It is a cost tradeoff. Say
  "targeted" vs "deep (to closure)" in the block; use deep for
  `linea_publicacion: true`.
- **Omitting the PRISMA-style counts.** The caller needs identified → dedup →
  retraction → included to judge coverage.

## Configuración opcional

**`SEMANTIC_SCHOLAR_API_KEY`** — a free Semantic Scholar API key. Optional; the
skill works keyless as documented above.

- **Effect:** when the env var is set, each `facet-searcher` subagent sends it as
  an `x-api-key` header (via `curl`, inside its own context) on its Semantic
  Scholar requests, moving off the shared keyless rate pool and largely removing
  the `HTTP 429` failures that hit concurrent dispatch. When the var is unset,
  requests go out keyless and the existing 429 degradation applies (record the
  error, drop that source for the run, continue). When ≥ 2 facets 429 in one run,
  step 5 recommends setting this key **in that run's report** — not just here.
  Step 3 snowballing stays
  WebFetch-keyless by design — WebFetch can't send headers, and routing raw S2
  JSON through the main session to use `curl` there would defeat the
  no-raw-payload rule.
- **Get one:** request it free from the form linked at
  `https://www.semanticscholar.org/product/api` (approval is by email, usually a
  day or two).
- **Where to put it:** export `SEMANTIC_SCHOLAR_API_KEY=…` in the environment that
  runs Claude Code (shell profile, or the `env` block of a settings file). Do
  **not** commit it to your repo.

## Endpoint verification

Endpoints confirmed against source docs on **2026-09-05**:

- arXiv API User's Manual — `info.arxiv.org/help/api/user-manual.html`
- Semantic Scholar Academic Graph API — `api.semanticscholar.org/api-docs/graph`
  and the public-API FAQ
- PatentsView / PatentSearch API — `search.patentsview.org/docs/` and the USPTO
  Open Data Portal transition notice at `data.uspto.gov`
- Crossref `works` — `api.crossref.org` (retraction fields; Retraction Watch
  Database integrated since 2023). Added 2026-09-06 from documented behaviour, not
  re-fetched in the 2026-09-05 sweep — confirm the `update-to` field shape on
  first use.

Re-check the PatentsView path first if patent calls fail — that source is
mid-migration to USPTO ODP.
