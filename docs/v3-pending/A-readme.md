# A-readme — document the citation-resolution scripts and the OpenAlex key (A1)

## Target

`README.md` (owner: INTEGRATION). Two insertions, nothing removed.

## Change

### 1. In `### Templates & scripts`, insert this bullet directly after the `scripts/code_repo/find_code_repo.py` bullet (before the `scripts/paper_to_tool/` bullet)

```markdown
- `scripts/citations/` — proves every reference Kairo relies on exists,
  matches its note, and is not retracted or withdrawn (standard library only;
  `--json` on each; `send: never` notes are skipped and never sent anywhere):
  - `resolve_refs.py` — resolves `Papers/` notes against OpenAlex (by DOI,
    arXiv DOI, or a title search whose hit must pass the match), with Crossref,
    arXiv and Semantic Scholar as fallbacks; fuzzy-compares title, first-author
    surname and year (`exact` / `close` / `mismatch` — a mismatch is the
    fingerprint of a "chimeric" citation mixing two real papers). Report-only
    by default; `--write` records `resolved` / `openalex_id` /
    `resolution_checked` (+ `resolution_status` / `_match` / `_evidence`);
    `--gate` re-checks live and exits 0 only if every selected paper is
    resolved and not retracted/withdrawn. `create-project` runs it at
    ingestion; `assemble-manuscript` uses `--gate`.
  - `check_retraction.py` — the retraction / withdrawal check that
    `literature-search` step 4a runs on its candidates (Crossref `updated-by` /
    Retraction Watch, arXiv withdrawal notices), with exact per-check counts.
  - `retraction_sweep.py` — periodic re-check of every ingested paper; flags
    each hypothesis / ADR citing a newly retracted or withdrawn paper
    (adr-check staleness pattern, never touching a hypothesis `status`).
    `--write` appends a dated line to the citing note's `## Revisión de
    vigencia`. Run it now and then, e.g. before a planning session:
    `python scripts/citations/retraction_sweep.py --vault <vault>`.
```

### 2. New section: insert after the `## Optional companion: DeepInfra (second critic, v2 dual-critic mode)` section (and its closing `---`), immediately before `## Local development`

```markdown
## Optional companion: OpenAlex API key (citation resolution)

`scripts/citations/resolve_refs.py` and `retraction_sweep.py` use
[OpenAlex](https://openalex.org/) as their primary source. OpenAlex has asked
for an API key on every request since 2026-02-13, but **keyless requests still
work on a small budget** (checked 2026-09-24): single-work lookups by DOI or id
are free either way; list/search calls (the title-search fallback) cost $0.001
each against a daily budget of **$0.10 keyless** vs **$1 with a free key**.
A handful of papers resolve fine without a key; for a whole vault, a sweep, or
a manuscript gate, set one up so a spent budget doesn't turn papers into
`unresolved` ("OpenAlex unreachable").

**Setup:**

1. Create a free account at [openalex.org](https://openalex.org/) and copy your
   key from **<https://openalex.org/settings/api>**.
2. Set it as an environment variable named **`OPENALEX_API_KEY`** in the
   environment that runs Claude Code (shell profile, or the `env` block of a
   settings file). The scripts send it as the `api_key` query parameter and
   never print it (it is redacted from every error message).
3. **Never commit it** — keep it out of the vault, out of this repo, and out of
   any note.

Optional, same pattern: `SEMANTIC_SCHOLAR_API_KEY` (see `literature-search` →
Configuración opcional) is sent as `x-api-key` when the resolver falls back to
Semantic Scholar.

---
```

### 3. `send: never` notes (A3) — insert as a new bullet list item in the section that documents note frontmatter conventions (or, if none, directly after the new OpenAlex section)

```markdown
**Do-not-send notes.** Any vault note can carry `send: never` in its
frontmatter. Its content (and, for external APIs, its metadata) never reaches
the model or a third-party service: every Kairo skill and agent that reads the
vault skips it explicitly, the citation scripts never look it up, the AI-use
disclosure lists it by id only, `check_bundle.py` blocks it inside a transfer
bundle, and the vault's `send_guard` PreToolUse hook
(`scripts/security/send_guard.py`, copied to `vault/Scripts/hooks/`) refuses
`Read`, content-mode `Grep`, shell commands naming it, and Smart Connections
`get_note` on it. File names and titles can still surface in listings — give a
sensitive note a neutral file name. List flagged notes with
`python scripts/security/send_guard.py list <vault>`.
```

## Verify

1. `grep -n "scripts/citations/" README.md` shows the new bullet between the
   `find_code_repo.py` and `scripts/paper_to_tool/` bullets.
2. `grep -n "## Optional companion: OpenAlex API key" README.md` appears after
   the DeepInfra section and before `## Local development`.
3. `grep -n "OPENALEX_API_KEY\|openalex.org/settings/api" README.md` hits the
   new section; `git grep -n "api_key=" -- ':!scripts/citations/test_*'`
   finds no literal key anywhere.
4. The script names match the files: `ls scripts/citations/` lists
   `resolve_refs.py`, `check_retraction.py`, `retraction_sweep.py`.
5. `grep -n "send: never" README.md` hits the do-not-send paragraph.
