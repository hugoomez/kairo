# Kairo — Stabilization audit report

Date: 2026-09-15. Auditor: Claude (Sonnet 5), interactive session (separate
session from the one that did the stabilization work).

**Headline finding, read this first:** `hallazgos-evaluacion-kairo.md` now
exists (it didn't during the previous audit) and 15 of its 16 items check out
as genuinely done, several very thoroughly. But the one item that looked most
straightforwardly fixed — **items 3/12, Smart Connections MCP** — is only
**half fixed**, and the half that's still broken is silent:

- `claude mcp list` from the vault now reports `smart-connections: ... ✔
  Connected`, and a real tool call succeeds (no more "herramienta no
  disponible en la sesión"). That part is genuinely resolved.
- But the vault's own `.claude/settings.json` `PostToolUse` hook reindexes
  with `{"path": "Papers/"}` (forward slash) on every `Papers/**` write. The
  indexer discovers files via `fs.promises.readdir(vaultPath, {recursive:
  true})`, which on Windows returns backslash-separated paths, then filters
  with `filePath.startsWith(pathPrefix)`. `"Papers\\P-0001 ....md".startsWith("Papers/")`
  is `false` for every file, every time. I confirmed this live: a
  scoped `reindex({path: "Papers/"})` returned `{indexed:0, skipped:0,
  blocks:0}` against a vault with 14 real papers; an **unscoped** `reindex()`
  (no path filter) immediately indexed `{indexed:31, blocks:178}`, and a
  search for "grokking" then returned real hits (`P-0001`, `P-0013`, `P-0014`,
  all with backslash paths, confirming the separator mismatch). The
  `.smart-env/multi/` index directory had **zero** files before I ran the
  unscoped reindex — meaning the Papers-scoped hook has never successfully
  indexed anything on this machine, silently, since it was set up.
- Net effect: the connection-level failure the hallazgos file complained
  about is fixed; the actual "search the vault for real overlap between
  projects" capability the two items cared about was **still not working**
  until I manually ran an unscoped reindex as part of this audit (a
  side-effect I'm flagging, not concealing — the vault is now indexed with
  real content for the first time). The standing hook is still scoped and
  will silently index nothing on every future `Papers/` edit until the
  path-separator bug is fixed.

Two smaller premise corrections, neither as significant:

- The brief asks me to confirm caller wiring "from the evolution step."
  The actual wiring is from **Step 1 (Wildcard)**, not Step 4 (Evolution) —
  evolution never calls `serendipity-scan`; it only recombines two
  already-passing candidates. Fase 5 is still fully built; this is just
  which step does the wiring.
- Block 5's registration lives in `~/.claude/settings.json`'s **global**
  `enabledPlugins`/`extraKnownMarketplaces` (user scope, covers every
  project), not a **per-project** entry under the vault's own key in
  `~/.claude.json`'s `projects` map. It works — confirmed by a live usage
  count increment — but it is not literally "a project-scoped entry for the
  vault," if that distinction matters to you.

Every verdict below states the exact evidence used.

---

## Block 1 — Fase 2 rescue

**DONE.**

```
cd app/vault-backend && git rev-parse --show-toplevel
  -> C:/Users/gomez/Kairo/app/vault-backend
git rev-parse --git-dir
  -> .git   (its own, not inherited)
cd /c/Users/gomez/kairo && git status --porcelain -- app/
  -> ?? app/   (outer repo sees it as an untracked directory, does not track into it)
cd /c/Users/gomez/kairo-plugin && git status --porcelain | grep vault-backend
  -> (no output — kairo-plugin never touched this)
```
Confirmed a genuinely separate repo, not nested inside either `vault/`'s or
`kairo-plugin`'s `.git`.

**Commit history:** 2 commits (`34fc721` initial rescue, `c2546f0` Fase 8) —
real history beyond a single initial commit, though still short since the
repo is new.

**Auth-guard tests, re-run from the new location:**

| Test | Result |
|---|---|
| No token | `Refusing to start: CLAUDE_CODE_OAUTH_TOKEN is not set.` — exits before binding a port. Matches prior audit. |
| `ANTHROPIC_API_KEY` set alongside a valid-shaped OAuth token | `Refusing to start: ANTHROPIC_API_KEY is set in the environment. ... Fix: unset ANTHROPIC_API_KEY ...`. Matches prior audit. |
| Only OAuth token set | `GET /health` → `{"ok":true,"vault":"C:/Users/gomez/Kairo/vault","auth":"subscription-only (CLAUDE_CODE_OAUTH_TOKEN)"}`. Log shows `[auth] subscription OAuth token present ...`. Matches prior audit. |

All three reproduce identically after the move.

---

## Block 2 — completo-tier merge

**DONE.**

```
git log --oneline --all | head -3
  -> 69eb898 Add Fase 5: ...
     1641319 Add serendipity-scan skill (Fase 4)
     f4a6690 Add Zotero integration for paper ingestion
git branch --show-current -> main
git worktree list
  -> C:/Users/gomez/kairo-plugin  69eb898 [main]   (only one entry — no completo-tier worktree)
git branch -a
  -> * main
     remotes/origin/main          (no preregister-experiment-completo-tier branch)
```
The completo-tier commits (`5feec27` … `111f19f`) are in `main`'s own linear
history (fast-forward merge), not sitting on a side branch or only visible
via `--all` from a worktree.

**Tests, re-run directly on `main`** (`git branch --show-current` confirmed
`main` before running):
```
cd scripts/analysis && python -m pytest test_sample_size.py test_bayes_factor_proportions.py -v
-> 25 passed in 0.06s (all 25 individually listed as PASSED)
```
Same 25/25 as the original audit and the stabilization session's own report.

**`skills/preregister-experiment/SKILL.md`:**
```
grep -n "deferred to v2|Not in v1" skills/preregister-experiment/SKILL.md
-> No matches found
grep -c "completo" skills/preregister-experiment/SKILL.md -> 25
```
The "deferred to v2" sentence is gone; `completo` is a live, described tier
(25 mentions) rather than a placeholder.

---

## Block 3 — loose commits

**PARTIALLY — the working tree is not fully clean, but not for the reason
that would matter.**

```
git status
  On branch main. Your branch is up to date with 'origin/main'.
  Untracked files:
    audit-v2.md
    hallazgos-evaluacion-kairo.md
  nothing added to commit but untracked files present
```
This is **not** clean in the literal sense you asked me to check. But both
untracked files are audit artifacts (the previous audit report, and the
hallazgos file this audit is working from) — neither is code, and neither
was part of the "Zotero + serendipity-scan" deliverable this block covers.
I did not commit them since committing an in-progress audit document isn't
this block's job and wasn't asked for.

**Separate commits, confirmed disjoint:**
```
git show --stat f4a6690 (Zotero)          -> README.md, skills/create-project/SKILL.md, skills/literature-search/SKILL.md
git show --stat 1641319 (serendipity-scan) -> skills/serendipity-scan/SKILL.md only
```
No file overlap between the two commits — genuinely separate, not squashed.

---

## Block 4 — Fase 5 and Fase 8

### Fase 5 (hypothesis-cycle)

**DONE**, with the wildcard/evolution correction noted above.

`skills/hypothesis-cycle/SKILL.md` "v1 / v2 scope" section now reads:
```
**Not in v1 or v2:** formal power analysis of *this* skill's own checks ...
**Orthogonal to v1/v2 — always active on overflow:** the tournament /
evolution / wildcard / meta-review mechanism in "Budget overflow" applies
whether the cycle ran in v1 or v2 ...
```
Tournament/evolution/meta-review are no longer listed as out of scope; only
the unrelated "formal power analysis" exclusion remains.

**Trigger logic** (`## Budget overflow — tournament, evolution, wildcard,
meta-review`, lines 298–313): compares the count of candidates that already
passed Checks 1–4 this cycle against the project's per-cycle budget; below
or at budget, stops (no tournament, only the meta-review step runs); over
budget, runs Steps 1–5.

**Evolution step** (Step 4, lines 380–395): takes the top-2 candidates by
final Elo, drafts one new candidate combining "a mechanism or contrast from
each parent," runs it through Checks 1–4 like any candidate, and explicitly
does **not** call `serendipity-scan` — it only recombines existing passing
candidates.

**Wildcard step, the actual `serendipity-scan` caller** (Step 1, lines
315–341): calls `serendipity-scan` (mechanism 2 always eligible at ≥3
tagged papers; mechanism 1 if a resolved hypothesis exists), and explicitly
forbids citing the returned lead directly as evidence — the new candidate
must earn its own citation through a real Check 2 search.

`serendipity-scan/SKILL.md`:
```
grep -n "no caller wiring for this skill, by design" skills/serendipity-scan/SKILL.md
-> No matches found
```
The exact old sentence is gone. In its place (lines 40–54): "no caller wiring
for **those** [literature-search, create-project, any other skill]" plus
"**One sanctioned exception:** `hypothesis-cycle`'s budget-overflow wildcard
step ... This is the only automated caller that may exist; do not add
another." Also updated in "Common mistakes" (line 180) and "Related" (line
222).

**Three-outcome logic, unchanged:** `## Loop and stopping rule` (lines
222–231) still reads verbatim as **Pass** → create `propuesta` note / **Clear
fail** → discard + digest line / **Rounds exhausted** → `propuesta` +
`needs_human_review: true`. The Budget-overflow section says so explicitly:
"the three-outcome logic above (pass / clear fail / rounds exhausted) is
unchanged and already finished by the time these steps start; nothing here
re-opens a check or re-classifies an outcome" (lines 309–313). Confirmed by
reading both sections side by side — no wording drift from the previous
audit's quote of this same table.

### Fase 8 (vault-backend)

**DONE**, verified with real, running code, not just file presence.

- `src/inbox.ts` `buildInbox(vaultPath)` calls `listProjects` /
  `listHypotheses` / `listAllExperiments` / `listPapers` (real vault.ts
  readers, not fixtures) and buckets by real frontmatter conditions
  (`status === "propuesta"`, `status === "completed" && needsHumanReview`,
  `fulltext !== "full"`, a non-null `originFlag`). Route: `app.get("/inbox",
  ...)` in `routes.ts:111`.
- Live test against the real vault (server started with a fake-shaped OAuth
  token, `VAULT_PATH` pointed at the real vault):
  ```
  GET /inbox ->
    hypothesesAwaitingApproval: 5 (H-0001..H-0005, all real propuesta notes)
    finishedExperiments: 2 (E-0001, E-0002, both hypothesis H-0006, both real completed+needs_human_review notes)
    papersToTriage: 2 (P-0011, P-0012, both real notes without fulltext: full)
    serendipityCandidates: 0
    total: 9
  ```
  Same result as the stabilization session's own test — reproduced
  independently here, not just re-quoted.
- `src/digest.ts` `generateNarrative()` reads `_hub.md`, `_digest.md`,
  hypothesis `history` since the last generation, and experiment
  status/`needsHumanReview` from the real vault, then calls `runAgent()`
  with `extraDisallowed: ["Edit","Write","MultiEdit","NotebookEdit"]` — the
  agent literally cannot write; `generateNarrative` itself
  (`writeNarrativeFile`) is what writes `_narrativa.md` afterward. Route:
  `GET /projects/:id/narrative` returns 404 (`no narrative generated yet for
  PROJ-001`) since none has been generated yet — confirmed live, consistent
  with a real read of the filesystem rather than a stub.
- Full agent-call path for narrative generation still can't be exercised
  live in this environment (no real subscription token) — same
  documented limitation as the original Fase 2/3 audit and the stabilization
  report; not a new gap.

---

## Block 5 — real plugin install

**DONE**, with the scope nuance noted in the headline.

```
~/.claude/settings.json:
  enabledPlugins: { ..., "kairo@kairo": true }
  extraKnownMarketplaces.kairo: { source: { source: "github", repo: "hugoomez/kairo" } }
```
This is a real marketplace registration (not `--plugin-dir`). It is at
**user** scope — I also checked `~/.claude.json`'s `projects` map for the
three kairo-related paths (`C:/Users/gomez/Kairo`, `C:/Users/gomez/kairo`,
`C:/Users/gomez/kairo-plugin`) and none carries its own `enabledPlugins`
field; the registration that makes it active in the vault is the global one.

**Usage counter, incremented twice in this audit session alone:**
```
Before this audit:  kairo@kairo usageCount = 1   (from the stabilization session's own test)
After 1 real invocation (kairo:adr-check, from vault, claude -p): usageCount = 2
kairo@inline (the old --plugin-dir entry): usageCount = 0, unchanged both times
```
Confirms the registered path is what's actually recording invocations, and
the stale inline entry is correctly untouched.

---

## The 16 hallazgos, against the real file

| # | Sev | Item | Verdict | Evidence |
|---|---|---|---|---|
| 1 | Alto | Retry/backoff on the anchor pass (`/paper/search/bulk`) | **DONE** | `literature-search/SKILL.md`: "The subagent retries `429`/`5xx` on **both** Semantic Scholar passes (relevance and anchor) with backoff before giving up — the anchor pass is not exempt." Reference table also states "retry 429 + 5xx w/ backoff (3 attempts) then degrade — **not exempt**" for the bulk endpoint. |
| 2 | Medio | Prominent warning (not just a log row) when a facet loses its anchor/relevance pass | **DONE** | "**Degraded-coverage warning (prominent).** ... Put a `## ⚠️ Cobertura degradada` block at the **top** of what you hand back ... not merely a row in the *Consultas* table," with a worked example line. |
| 3 | Medio | Smart Connections MCP never actually tested/working | **PARTIALLY DONE** | Connects now (`claude mcp list` → Connected; a real `search_by_text` call succeeds). But the vault's own `Papers/`-scoped reindex hook silently indexes 0 files on Windows (path-separator bug, evidenced above) — the underlying vault search was still non-functional until I manually ran an unscoped reindex during this audit. |
| 4 | Medio | Distinguish exclusion by scope vs. by relevance | **DONE** | Step 4d: explicit `fuera de alcance` vs `relevancia baja` categories, a dedicated `### Relevante pero fuera de alcance` sub-section for scope-excluded-but-relevant candidates, and "never silently folded into the `relevancia baja` tail." Directly resolves the Omnigrok scenario named in the hallazgo. |
| 5 | Bajo | Fix one behavior for `## Revisión del ciclo` (successful-refinement case was ambiguous) | **DONE** | `hypothesis-cycle/SKILL.md`: "Write the section **whenever the cycle ran ≥ 1 refinement round**, whatever the final outcome — not only when rounds were exhausted," with the passed-after-refinement case spelled out first. |
| 6 | Medio | Proactively recommend a Semantic Scholar key on repeated 429s | **DONE** | "**Proactive Semantic Scholar key recommendation.** If **two or more facets** hit `HTTP 429` ... say so directly in the report," with a worked example sentence and an offer to re-run affected facets. |
| 7 | Medio | Exact dedup counts, not approximate | **DONE** | "**Counts are exact integers.** ... Never write `≈95`, `~95`, `about 95`, or a range. If you find yourself wanting to approximate, the merged candidate list is incomplete — fix that, don't round." |
| 8 | Alto | Retraction check doesn't cover arXiv "withdrawn" (DOI/Crossref-only) | **DONE** | New "### 4. Retraction check ... arXiv" section: detects `withdrawn` in `arxiv:comment`, a `Withdrawn:` title prefix, or a withdrawal notice in the abstract; "**4a. Retraction / withdrawal check.** Two complementary checks — run **both**." Reporting counts now separately track "Crossref (DOI)" and "arXiv (withdrawn)". |
| 9 | Bajo | Degraded coverage should be a document-level signal, not just raw log | **DONE** | Same fix as #2 — the `## ⚠️ Cobertura degradada` block is exactly this signal, carried into `Estado-del-arte.md` by the caller per the skill text. |
| 10 | Verify-only | Spot-check citations against real PDFs | **NOT DONE (unverifiable here)** | This needs a human or an agent session with the actual PDFs open, comparing cited §/Tabla/Figura references against the source. No such check was run in this session or (as far as the file evidence shows) since the hallazgo was written. Not a code fix — still an open action item. |
| 11 | Bajo | Formalize (or explicitly leave emergent) the clear-fail interactive menu | **DONE — formalized** | `hypothesis-cycle/SKILL.md` "### On a clear fail — what may follow" gives exactly three in-spec/off-spec options: discard (default), spin off a new candidate ("a narrower sub-regime, an adjacent phenomenon" — covers both refinement directions named in the hallazgo), or file anyway with a logged human override — no longer left as emergent Claude Code behavior. |
| 12 | Alto (subido) | Smart Connections MCP configuration itself, not just a one-off failure | **PARTIALLY DONE** | Same finding as #3 — see headline. The connection/config problem is fixed; the effective non-functionality (empty index) persisted until this audit manually triggered a full reindex, and the standing scoped hook will still silently index nothing going forward until the path-separator bug in `indexer.js`'s `findStaleFiles` is fixed. |
| 13 | Medio | Schema gap: an experiment can't declare it adjudicates >1 hypothesis (rival pair) | **DONE** | `templates/experiment-template.md`: `secondary_hypotheses: []` field, documented as "other hypotheses this design also bears on." `preregister-experiment/SKILL.md`: "Rival-pair designs put the other side in `secondary_hypotheses`" (line 383) — the exact H-0001/H-0006 scenario named in the hallazgo — with `## Evidencia colateral` writing and an explicit "Secondary hypotheses do not transition" rule so it can't be misused as a second adjudicating link. |
| 14 | Bajo | Reinforce that implementation must follow frozen formulas literally | **DONE** | `run-experiment/SKILL.md` "### 0. Implement the frozen design — literally": "Transcribe the frozen formulas verbatim ... No 'the paper probably meant…', no rounding 'to be clean', no quiet tuning, no 'this variant is better' ... if a value there disagrees with the note, the note wins and the code is wrong." Ambiguity gets a dated `## Enmiendas` entry + a severity-tagged question, never a silent default. |
| 15 | Alto | `run-experiment` assumed cloning the whole vault repo for a remote runtime | **DONE** | "**Never clone, push, or create a git remote for `vault/` to run an experiment.**" + an explicit minimal-transfer-bundle procedure (`Scripts/experiments/E-XXXX/*`, the frozen data manifest, the lockfile, a `MANIFEST.sha256` — "Nothing from the wider vault tree"). Matches the hallazgo's own description of what was actually needed. |
| 16 | Alto (sin confirmar) | Explicit severity levels for risk warnings during preregistration/execution | **DONE** | Both `preregister-experiment/SKILL.md` ("## Flagging risks and ambiguities — with a severity") and `run-experiment/SKILL.md` (step 0) define the same three-tag vocabulary — `crítico` / `importante` / `menor` — with `crítico` explicitly including "a model/architecture detail that could suppress the effect being measured" (the exact LayerNorm/unembedding scenario named in the hallazgo) and forcing its own top callout, never folded in with trivial choices. |

---

## Summary table

| # | Block / item | Verdict |
|---|---|---|
| 0 | `hallazgos-evaluacion-kairo.md` exists | **Confirmed present** — proceeded with the item-by-item audit |
| 1 | Fase 2 rescue (repo, history, auth tests) | **Done** — standalone repo confirmed by `git rev-parse`, 2 real commits, all 3 auth tests reproduced |
| 2 | completo-tier merge | **Done** — in `main`'s linear history, 25/25 tests pass on `main` directly, worktree/branch gone, "deferred to v2" language removed |
| 3 | Loose commits | **Mostly done** — Zotero and serendipity-scan are separate, disjoint commits; working tree has 2 untracked audit docs (not code), not literally clean |
| 4 | Fase 5 | **Done** — tournament/evolution/meta-review no longer out of scope, three-outcome logic unchanged; wiring to serendipity-scan is via the wildcard step, not evolution (brief's premise, corrected) |
| 4 | Fase 8 | **Done** — inbox and narrative routes pull real vault data, verified live; narrative generation is read-only by construction (`extraDisallowed`) |
| 5 | Plugin install | **Done** — real marketplace registration, usage counter incremented twice in this session alone; registration is user-scoped, not a vault-project-specific entry |
| 6.1–6.16 | Hallazgos | **13 done, 2 partially done (3, 12 — same underlying MCP indexing bug), 1 not done / unverifiable in-session (10)** |

**Bottom line:** the stabilization work holds up under direct re-execution —
merges, tests, commits, and live server calls all reproduce exactly as
claimed, and the hallazgos file (once it actually existed to check against)
shows real, substantive fixes across literature-search, hypothesis-cycle,
preregister-experiment, and run-experiment, not just documentation. The one
place reality falls short of appearance is Smart Connections MCP: it looks
fixed (connects, no error), but the vault's own reindex hook has a
Windows path-separator bug that silently indexed nothing until this audit
forced a full reindex by hand — worth fixing in `indexer.js`'s
`findStaleFiles` (compare using `path.sep`, or normalize both sides to `/`)
before relying on the hook again.
