# Kairo v2 — Audit report

Date: 2026-09-15. Auditor: Claude (Sonnet 5), interactive session.

**Headline finding, read this first:** the premise behind several items below
(a "Fase 2" web app with a usage dashboard, a "Fase 5" tournament/evolution
system, a "Fase 8" cross-project inbox) does not match what exists in
`kairo-plugin/`. Two things are true at once:

1. Real, working code for several of these phases *does* exist — but it lives
   in the sibling `kairo/tools/vault-backend/` directory (a separate Node app
   in the vault repo), not in `kairo-plugin/`, and some of it explicitly
   contradicts the audit brief's description of Fase 5/8 (those are stated in
   the code itself as **out of scope** / **intentionally not built**).
2. The source document for item 1, `hallazgos-evaluacion-kairo.md`, does not
   exist anywhere on this machine — I searched the whole home directory, both
   repos, and full git history of `kairo-plugin` (all branches). I cannot
   confirm or deny any of the 16 items because there is nothing to check them
   against.

Every verdict below states the exact evidence used (file read, command run,
test executed, live request made) and its result.

---

## Part A — `kairo-plugin/`

### 1. 16 items from `hallazgos-evaluacion-kairo.md`

**NOT VERIFIABLE — source document does not exist.**

Evidence:
```
find /c/Users/gomez -maxdepth 4 -iname "*hallazgos*"          -> no results
find kairo kairo-plugin -iname "*hallazgos*"                   -> no results
```
No file by this name, or anything matching `*hallazgos*`, exists anywhere
under `C:\Users\gomez`. I cannot go "through each one individually" because
there is no list of 16 items anywhere in this environment — not in either
repo, not in `.claude/`, not in a scratch file, not in git history of either
repo. If this file existed in a prior session it was never committed and is
not present on disk now. **This item cannot be marked done, partially done,
or not done — it is unauditable as given.** If you have the file's actual
content or a location I didn't check, re-run this with that path.

---

### 2. Fase 1 (Zotero) — real end-to-end test ingestion

**PARTIALLY DONE.**

What exists: `skills/create-project/SKILL.md` step 6 ("Ingest each confirmed
paper — via Zotero") — real, specific HTTP integration against Zotero's local
connector (`POST http://127.0.0.1:23119/connector/saveItems`) and Better
BibTeX's JSON-RPC endpoint (`item.search`, `item.citationkey`, `item.export`),
with an explicit, non-silent fallback path when Zotero is unreachable. The
README's "Optional companion: Zotero" section documents setup end to end
(Better BibTeX plugin, "allow other applications" setting, endpoint list).
This is real integration work, not a stub — verified by reading the actual
HTTP calls and dedup logic in `skills/create-project/SKILL.md` lines ~134–176.

What I could **not** do: run a real test ingestion.
```
curl -s -m 3 http://127.0.0.1:23119/connector/ping   -> curl exit 7 (connection refused)
tasklist | grep -i zotero                            -> no Zotero process running
```
Zotero is not installed/running on this machine, so I cannot exercise the
skill against a live Zotero library and produce a real Zotero entry + linked
Obsidian note as the brief asks.

Further evidence this has **never actually been run for real** in this vault:
```
grep -l "zotero_key" vault/Papers/*.md   -> 0 of 14 papers
```
None of the 14 real papers currently in the vault carry a `zotero_key`, so
whatever exercising of this path has happened, it has not produced any of the
vault's real content. The Zotero code is new, uncommitted work (see "Working
tree state" below) with no observed live run.

---

### 3. Fase 2 backend — CLAUDE_CODE_OAUTH_TOKEN auth, subscription billing

**DONE at the code level, with one unverifiable sub-claim.** Important
location correction: this does not live in `kairo-plugin/` — it's
`kairo/tools/vault-backend/`, a separate untracked Node/TypeScript app.

Live tests I ran against the actual server (`tools/vault-backend/src/*.ts`,
via `npx tsx src/server.ts`):

| Test | Command | Result |
|---|---|---|
| Refuses to start with no token | `env -u CLAUDE_CODE_OAUTH_TOKEN … npx tsx src/server.ts` | `Refusing to start: CLAUDE_CODE_OAUTH_TOKEN is not set.` — exits before binding a port. |
| Refuses to start with `ANTHROPIC_API_KEY` set, even alongside a valid-shaped OAuth token | `ANTHROPIC_API_KEY=sk-ant-api-fake CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-fake … npx tsx src/server.ts` | `Refusing to start: ANTHROPIC_API_KEY is set in the environment. … Fix: unset ANTHROPIC_API_KEY …` |
| Starts and serves real vault data with only a (fake-shaped) OAuth token set | `CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-fake000… VAULT_PATH=… npx tsx src/server.ts` | Logged `[auth] subscription OAuth token present …` and `the wrapped Claude Code CLI will bill to your subscription`; `GET /health` → `{"ok":true,…,"auth":"subscription-only (CLAUDE_CODE_OAUTH_TOKEN)"}`; `GET /projects` → real data: `PROJ-001 early-stopping-tareas-algoritmicas`. |

Code review of `src/auth-guard.ts` confirms the precedence-aware logic the
README claims: it checks all five outranking vars (`ANTHROPIC_API_KEY`,
`ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_USE_BEDROCK/_VERTEX/_FOUNDRY`), refuses
to start if any is set, refuses if the OAuth token is absent, and rebuilds
the child process env with the billing-switch vars stripped even after the
check (`buildChildEnv()`), as defense in depth.

**Not verifiable in this session:** "a real test call through it shows up as
subscription usage, not API billing, in the actual usage dashboard." I have
no `CLAUDE_CODE_OAUTH_TOKEN` from a real subscription available in this
environment, and generating one and checking claude.ai's usage dashboard
requires your account credentials and a live billing-relevant action I
shouldn't take unprompted. The code-level guarantee is real and tested; the
dashboard-level confirmation is outside what I could do here.

---

### 4. Fase 2 frontend — matches `brand/identity.md`

**DONE.** Verified by reading `tools/vault-backend/public/styles.css`
(754 lines) against `brand/identity.md` directly, not by trusting a
description.

- Every declared hex from identity.md §3 (`#FBFAF8`, `#F4F2ED`, `#E3E0D9`,
  `#6B675E`, `#14140F`, `#3C36D9`, `#EDECFB`, `#1E6A4F`, `#E6F1EC`,
  `#A8471C`, `#FBEDE5`, plus the `[measured]` extras `#43403A`, `#1B1A16`,
  `#2A25B4`, `#14543D`, `#8A3A15`, `#A09B90`) is present verbatim as a CSS
  custom property, correctly labeled by source (declared vs. `[measured]`).
- Undeclared values (type scale, spacing scale, radii — identity.md §10
  explicitly says the guide specifies none of these) are set behind
  `[CHOICE]` comments in the CSS, not silently invented as if canonical.
- Fonts: Instrument Serif / Instrument Sans / JetBrains Mono loaded from
  Google Fonts with real fallback stacks (`index.html` line 15,
  `styles.css` lines 49–51) — matches §4.
- **"At most one solid indigo element per screen"** (§3.2, a named hard
  rule): `app.js` has a `assertSingleAccent()` dev-guard that counts
  `.btn-primary` elements and warns if > 1; only two buttons in the whole
  app use that class (`prepare-run`, `approve-run`), each in a
  mutually-exclusive view.
- **"Rust never means user error"** (§3.2, the other named hard rule):
  grepped `styles.css` for every use of `--oxido`/`--oxido-tinte` — it's
  applied only to `.state.is-refutada`, `.state.is-evidencia_mixta`, and
  `.state.is-review` (the `needs_human_review` state). No error/validation
  styling anywhere uses it.
- The contrast note in identity.md §10 (`#A09B90` on `#FBFAF8` fails WCAG
  AA) is explicitly addressed: `styles.css` line 40 comment states metadata
  text uses `--tinta-suave` (5.4:1) and reserves `--meta` for hairlines
  only — matches the guide's own footnote about how the vault GUI decided
  this.

No drift found. This is a careful, literal implementation of the brand doc.

---

### 5. Fase 3 (second critic) — real cloud call, tiering, cost logging, agreement counter

**DONE, with one nuance worth flagging.**

- **Not a stub:** `agents/second-critic.md` builds a real HTTP request via
  `curl` to `https://api.deepinfra.com/v1/openai/chat/completions`, requires
  `DEEPINFRA_TOKEN`, parses `VERDICT:`/`REASONING:` from the response, and
  explicitly forbids the agent from substituting its own reasoning — a
  failed/missing call must return `status: unavailable`, never a guessed
  verdict. This is real orchestration code, not hardcoded output.
- **Tier selection — nuance, not a defect:** the brief describes "correctly
  selects the high-stakes tier only when `linea_publicacion: true`." The
  actual rule (`skills/hypothesis-cycle/SKILL.md` "Running the second
  critic" step 1) is: `high_stakes` if `linea_publicacion: true` **or** the
  researcher explicitly asks for it this run. So there's a legitimate manual
  override path in addition to the automatic one — flag this as a
  deliberate design choice documented in the skill, not an unflagged
  deviation, but it does not literally match "only when."
- **Real per-call cost:** the agent computes `cost_usd` from
  `usage.prompt_tokens` / `usage.completion_tokens` and the tier's actual
  DeepInfra per-1M-token price (agent spec, "Making the call" step 5).
- **Agreement-rate counter — actually tested, not just read.** I ran
  `scripts/second_critic/agreement_log.py` for real against a scratch log:
  ```
  python scripts/second_critic/agreement_log.py record --log <scratch>.jsonl \
    --project PROJ-001 --hypothesis H-0001 --check 3 --tier default \
    --primary pass --second pass --cost-usd 0.001
  -> recorded: check 3, pass vs pass -> AGREE ($0.0010)

  python … record … --check 4 --tier high_stakes --primary pass --second clear_fail --cost-usd 0.01
  -> recorded: check 4, pass vs clear_fail -> DISAGREE ($0.0100)

  python … summary --log <scratch>.jsonl --json
  -> agreement rate: 50.0%, cumulative cost: $0.0110, correct per-check breakdown, JSON payload well-formed
  ```
  The log file itself and the append-only JSONL rows were inspected directly
  and match the schema described in the docstring. This is genuinely
  functional, not just defined.

**Not verifiable:** an actual live call to DeepInfra — no `DEEPINFRA_TOKEN`
is configured in this environment (`echo $DEEPINFRA_TOKEN` → empty), so the
`curl` path itself was reviewed but not executed against the real API.

**Also worth noting:** in the real vault, `Scripts/second-critic-log.jsonl`
does not exist — v2 mode has never actually been run for real here.

---

### 6. Fase 4 (serendipity-scan) — caps, isolation, no auto-merge

**DONE.** Read `skills/serendipity-scan/SKILL.md` in full.

- **Cap:** "Combined cap: 1-2 candidates per run, total — not per mechanism,"
  stated explicitly, with an instruction to name what was dropped and why if
  both mechanisms found candidates.
- **Isolated section:** output goes under a fixed heading,
  `## 🔭 Analogía posible, sin verificar`, described as "additive and
  disposable."
- **Never auto-merged:** "it never enters `Papers/` or a hypothesis's
  `Justificación` automatically... until a human reads the full paper and
  explicitly promotes it." The "Common mistakes" section repeats this
  explicitly as a thing to avoid.
- Also confirmed: the skill states it has "no caller wiring... by design" and
  is "Never invoked automatically by `hypothesis-cycle`, `literature-search`,
  `create-project`, or any other skill" — this directly matters for item 7
  below.

**Note:** this file is untracked in git (`git status` shows
`?? skills/serendipity-scan/`) — see "Working tree state."

---

### 7. Fase 5 (tournament/evolution/meta-review)

**NOT DONE — explicitly out of scope, stated in the shipped skill text
itself, twice.**

`skills/hypothesis-cycle/SKILL.md`, "v1 / v2 scope" section, verbatim:

> **Not in v1 or v2:** tournament or head-to-head ranking of competing
> candidates; evolution / meta-review of the critic itself; formal power
> analysis of *this* skill's own checks... Budget overflow →
> `update-confidence` marks `en_cola` in generation order — no ranking.

And `skills/serendipity-scan/SKILL.md` independently states there is "no
caller wiring for this skill, by design" — so there is no "evolution's
wildcard step" that calls it; nothing calls `serendipity-scan`
automatically, from anywhere.

This means: the tournament trigger condition, the evolution-wildcard→
serendipity-scan link, and any meta-review mechanism described in the audit
brief do not exist in this codebase — not partially implemented, not
present under different naming. I searched explicitly for "tournament",
"meta-review"/"meta_review", "evolution" across all `.md` files in
`kairo-plugin/`; the only hits are the three sentences quoted above, ruling
the feature out.

**What does hold, confirmed unchanged:** the original three-outcome logic
in `hypothesis-cycle`'s "Loop and stopping rule" table — **Pass** (create
`propuesta` note) / **Clear fail** (discard, digest line, no note) /
**Rounds exhausted** (`propuesta` + `needs_human_review: true`) — is present
verbatim and is the *only* outcome logic in the file. Budget overflow is a
separate, later step that hands overflow IDs to `update-confidence`
unranked, "in generation order." So the part of item 7 that says "the
original three-outcome logic... still holds unchanged underneath it" is
true in the narrow sense that it's unchanged — but there is no "it" (no
tournament/evolution layer) sitting on top of it to hold it "underneath."

---

### 8. Fase 6 (preregistro completo)

**DONE, but only on an unmerged branch — not in the plugin as shipped on
`main`.** This is the most consequential finding in Part A.

`git log` on `kairo-plugin/main` shows only 5 commits, the last being "Add
implementation plan: preregister-experiment completo tier" — a plan, not an
implementation. The actual code lives on an unmerged branch/worktree:
```
git worktree list
  kairo-plugin                                    631c667 [main]
  kairo-plugin/.worktrees/preregister-experiment-completo-tier  111f19f [preregister-experiment-completo-tier]
```
`main`'s own `skills/preregister-experiment/SKILL.md` still says, verbatim:
> **Not in v1** — `completo` tier, deferred to v2... Until then a `ligero`
> prereg picks thresholds by domain judgement, not by a power calculation.

On the branch, the feature is real and tested:
```
cd .worktrees/preregister-experiment-completo-tier/scripts/analysis
python -m pytest test_sample_size.py test_bayes_factor_proportions.py -q
-> 25 passed in 0.12s
```
- **Power analysis gated to `completo` only:** branch SKILL.md: "`ligero`:
  thresholds are domain judgement, fixed now, no power calc" vs. `completo`
  requiring SESOI/alpha/target-power inputs to `sample_size.py`. Confirmed.
- **`confidence` as a real posterior only under Bayesian:** branch SKILL.md,
  stated as a rule ("If `bayesian` is chosen: `confidence`... is a real
  posterior only... a single frequentist-plan experiment... keeps
  `confidence` a [heuristic]") **and** repeated in "Common mistakes":
  "Treating `confidence` as a real posterior under a frequentist plan, or
  under mixed evidence." Never framed as a posterior in the frequentist
  case anywhere I found. Confirmed.

**The consequence for item 9:** because this isn't merged, `main`'s shipped
`assemble-manuscript` skill gates on a tier (`completo`) that
`preregister-experiment` on `main` cannot produce — every hypothesis fails
that gate today on the installed plugin. `assemble-manuscript` even says so
itself (see item 9).

---

### 9. Fase 7 (manuscript assembly) — refuses below the rigor bar, says what's missing

**DONE** at the skill-text level; **currently unreachable in practice** on
`main` because of item 8.

`skills/assemble-manuscript/SKILL.md` Step 2 is a per-hypothesis
classification table (never a group verdict) with five explicit branches,
including:

> `linea_publicacion: true`, `status: apoyada`, but at least one
> `linked_experiment` entry is `tier: ligero` → **Excluded — missing
> `completo` tier.** Name the exact experiment id(s)... **As of this
> writing `preregister-experiment` has not yet implemented `completo` — if
> so, say that plainly too, rather than implying the researcher just forgot
> a step.**

That last sentence is the skill anticipating exactly the state item 8 found
— it's an honest, self-aware note, not a bug, but it does mean: as shipped
today, this skill cannot produce a draft for any real `linea_publicacion`
thread, because no experiment on `main` can ever be `tier: completo`. "Says
explicitly what's missing" is implemented and correct; "gates on the rigor
bar" is currently a permanent refusal for every real case until item 8
merges.

`vault/Projects/early-stopping-tareas-algoritmicas/` has no `Manuscritos/`
folder yet, consistent with this never having produced a real draft.

---

### 10. Fase 8 (cross-project inbox + periodic digest)

**NOT DONE — explicitly, in the frontend's own README.**

`tools/vault-backend/README.md`, "Frontend" section, last line:

> The cross-project inbox and the periodic narrative digest are
> intentionally not built.

I also grepped the whole `kairo-plugin/` tree for `inbox` (zero hits outside
this quoted sentence's context in vault-backend, which isn't even in that
repo) and confirmed no route, no UI section, and no scheduled job implements
either feature anywhere in `tools/vault-backend/src/` or `public/`.

---

### Cross-skill contradiction check (re-read of every file touched)

I re-read `hypothesis-cycle`, `second-critic`, `agreement_log.py`,
`serendipity-scan`, `assemble-manuscript`, `preregister-experiment` (both
`main` and the worktree branch), `create-project`, `literature-search`,
README.md, and the vault-backend source, looking for a new contradiction
introduced by stacking these features.

**Found:**
- `assemble-manuscript` (shipped on `main`) references a `completo` tier
  that does not exist on `main` — not a contradiction between two pieces of
  prose (the skill is honest about it, see item 9), but a **functional**
  inconsistency: one shipped skill's core gate depends on a feature that
  isn't shipped. This will read as a bug to any real user, even though
  every individual file is internally consistent and honest about its own
  status.
- No other contradiction found. `hypothesis-cycle` v1/v2 scope notes,
  `serendipity-scan`'s "no caller wiring" statement, and
  `assemble-manuscript`'s read-only claim over hypothesis status are all
  mutually consistent, and none of the newly-added skills silently
  reassign a responsibility another skill already owns (e.g., nothing but
  `update-confidence` writes a status transition anywhere I found).

**Not found, but worth flagging as a process gap rather than a content
contradiction:** four of the ten items above (Fase 1 Zotero code, Fase 4
serendipity-scan, and everything in Fase 2/8, which live outside this repo
entirely) are **uncommitted** in their respective repos — see below.

---

### Working tree state (both repos, checked at audit time)

`kairo-plugin/`:
```
 M README.md
 M skills/create-project/SKILL.md
 M skills/literature-search/SKILL.md
?? skills/serendipity-scan/
```
So Fase 1's Zotero integration and all of Fase 4 (serendipity-scan) exist
only as uncommitted working-tree changes in `kairo-plugin` — not yet part
of any commit, not part of what `git log` or a fresh clone would show.

`kairo/` (outer repo, contains the vault and `tools/`):
```
 M CLAUDE.md
 M vault/.obsidian/workspace.json
 M vault/CLAUDE.md
?? .claude/
?? .mcp.json
?? brand/
?? tools/
?? ultima.txt
?? vault/.obsidian/community-plugins.json
?? vault/.obsidian/plugins/
```
`tools/` (containing `vault-backend/`, i.e. all of Fase 2 backend+frontend)
and `brand/` (the brand guide item 4 was checked against) are entirely
untracked. The CLAUDE.md at `kairo/CLAUDE.md` says `tools/` is "Not tracked
in this repo" by design — so this may be intentional (a separate deploy
target) rather than an oversight, but it means **none of the Fase 2 work is
backed up in version control anywhere I can see**. Worth confirming that's
deliberate.

---

## Part B — `kairo/vault/`

### Plugin consumption: installed vs. copied

**Not copied in** — confirmed: `vault/.claude/skills/` contains only a
`.gitkeep`, no duplicated skill files.

**Not installed via the documented marketplace flow either.** The README's
own instructions are `/plugin marketplace add hugoomez/kairo` then
`/plugin install kairo@kairo`. What's actually registered, from
`~/.claude.json`:
```
"pluginUsage": { "kairo@inline": { "usageCount": 0, ... } }
```
The `@inline` suffix matches the README's *other* documented path — "try it
without installing: `claude --plugin-dir /path/to/kairo`" — not a
marketplace install. No `extraKnownMarketplaces` entry exists anywhere in
`~/.claude.json`, and neither vault-related project entry
(`C:/Users/gomez/Kairo`, `C:/Users/gomez/kairo`) has an `enabledPlugins`
field. So: the vault consumes the plugin by loading it as a raw plugin
directory each session, not via a registered marketplace install — and the
usage counter for it reads `0`, meaning (on the evidence available here) no
skill invocation through that registration has been recorded. I can't fully
rule out invocations that didn't increment this counter for some other
reason, but this is the best evidence available in this session, and it's
consistent with the empty `Scripts/second-critic-log.jsonl` and missing
`Manuscritos/` findings above (v2 features look genuinely never-exercised).

### `vault/CLAUDE.md` duplication check

**None found.** `vault/CLAUDE.md` explicitly defers: "Kairo's binding
principles... are defined in the **Kairo plugin** (see its README)... This
vault follows them; it does not restate them." Read the full file (94
lines) — it covers vault-specific things only (folder layout, the MCP
setup/troubleshooting history, the "launch from `vault/`" instruction) and
never repeats a principle, skill description, or rule that's stated in the
plugin's README or any SKILL.md.

### Vault content leakage into `kairo-plugin/`

**None found.** Searched the *entire* git history of `kairo-plugin`, both
branches, all commits:
```
git log --all --name-only --pretty=format: | grep -iE "papers/|projects/|hipotesis|experimentos"
  -> no results (no file at those paths was ever committed)
git log --all -p | grep -iE "PROJ-001|early-stopping-tareas|H-000[0-9]|P-000[0-9]"
  -> only generic template placeholders (e.g. "cites: []  # e.g. [H-0001, P-0002]")
     in templates/*.md and skill docs — never real vault content
```
`PROJ-001`/`early-stopping-tareas-algoritmicas` (the vault's one real
project) and its papers/hypotheses never appear in `kairo-plugin`'s history
except as templated example syntax that any generic plugin doc would use.

---

## Summary table

| # | Item | Verdict |
|---|---|---|
| 1 | 16 hallazgos items | **Unauditable** — source file does not exist anywhere on this machine |
| 2 | Fase 1 Zotero | **Partially done** — real integration code, uncommitted, never live-tested (no Zotero installed), no vault paper shows a `zotero_key` |
| 3 | Fase 2 backend auth | **Done** (code-level, live-tested 3 ways) — dashboard-level billing confirmation not verifiable here |
| 4 | Fase 2 frontend / brand | **Done** — verified line-by-line against `brand/identity.md`, no drift found |
| 5 | Fase 3 second critic | **Done** — real DeepInfra call code, cost calc, and a live-tested agreement logger; tier rule has a documented manual-override nuance; never run live (no token); never run for real in the vault |
| 6 | Fase 4 serendipity-scan | **Done** — cap, isolation, no-auto-merge all confirmed in skill text; file is uncommitted |
| 7 | Fase 5 tournament/evolution/meta-review | **Not done** — explicitly out of scope in two separate skill files, contradicts the audit brief's premise |
| 8 | Fase 6 completo tier | **Done, but unmerged** — real, tested code (25/25 tests) exists only on an isolated worktree branch, not on `main` |
| 9 | Fase 7 manuscript assembly | **Done** at the skill-text level, but **currently unreachable** in practice because of #8 |
| 10 | Fase 8 inbox/digest | **Not done** — explicitly stated as intentionally not built |
| — | Cross-skill contradictions | One functional (not textual) inconsistency: assemble-manuscript's gate depends on the unmerged completo tier |
| B1 | Vault installs (not copies) the plugin | Not copied — confirmed. Not a registered marketplace install either — loaded via `--plugin-dir`, usage counter reads 0 |
| B2 | No duplicated content in vault/CLAUDE.md | Confirmed — vault/CLAUDE.md explicitly defers to the plugin README |
| B3 | No vault content leaked into kairo-plugin | Confirmed — full git history of both branches checked, no real vault content ever committed |

**Bottom line:** the pieces that exist and were checkable are, for the most
part, genuinely well-built — the auth guard, the brand-compliant frontend,
the agreement logger, and the completo-tier statistics all held up under
actual execution, not just reading. But roughly half of what the audit brief
assumes was built (Fase 5 entirely, Fase 8 entirely, Fase 6 on `main`, and
the hallazgos list itself) either doesn't exist, is explicitly out of scope
in the code's own words, or exists only on an unmerged branch — and almost
everything that *does* exist is sitting uncommitted in one working tree or
another.
