# Kairo

**Citation-grounded research automation for Claude Code.**

Kairo is a Claude Code plugin for running a small research programme with the
discipline of a preregistered study: every hypothesis cites specific papers,
every experiment is frozen before its code runs, and no claim is promoted to
"supported" without an independent replication. It turns a folder of Markdown
notes (papers, hypotheses, experiments, product decisions) into a linked,
auditable research vault — and it runs entirely on a Claude subscription, with
no API keys.

Kairo pairs naturally with [Obsidian](https://obsidian.md/) — the vault is
plain Markdown with YAML frontmatter — but nothing here depends on it.

---

## Core principles

1. **No API keys required — runs on a Claude subscription.** The core pipeline
   uses Claude Code's own model access. The public literature sources it
   queries (arXiv, Semantic Scholar, Crossref) need no key. US-patent search
   (only for product-oriented projects) uses a free PatentsView key if you have
   one, and is skipped otherwise. The one exception is `hypothesis-cycle`'s
   **v2 dual-critic mode**, which is entirely **opt-in** and needs a separate,
   small-cost `DEEPINFRA_TOKEN` (never an Anthropic key) — see "Optional
   companion: DeepInfra" below. v1 (default) needs nothing beyond your Claude
   subscription, same as every other skill.
2. **Citation-grounded hypotheses.** Every hypothesis carries a justification
   that cites specific papers, with section / table / figure references where
   possible. An agent-generated claim with no citable support does not get
   filed. (A human-submitted intuition may be filed explicitly flagged as
   unsupported — never with a manufactured citation.)
3. **Preregistration before execution.** A preregistration — the exact
   prediction plus the analysis plan, decision thresholds, control condition,
   and an environment manifest with dependency and dataset hashes — is frozen
   in git *before* any experiment code runs. After the freeze, the original
   sections are immutable; every later change is an append-only amendment.
4. **Replication required before promotion.** A hypothesis reaches `apoyada`
   (supported) only after a *second independent* valid experiment also supports
   it. Two experiments that disagree are never averaged into a clean verdict —
   they become `evidencia_mixta`, and Kairo drafts a follow-up hypothesis about
   the likely moderating variable. Evidence is combined by a versioned script
   (random-effects DerSimonian–Laird), never by eyeballing.
   Exploratory experiments (simplification-ladder rungs, program evolution)
   inform designs and never count as evidence; before any promotion to
   `apoyada`, a fresh verifier that never saw the reasoning checks the
   artifact for concrete errors.

---

## Installation

```
/plugin marketplace add hugoomez/kairo
/plugin install kairo@kairo
```

Then restart Claude Code (or run `/reload-plugins`). The skills load under the
`kairo:` namespace.

To try it without installing — from a clone of this repo:

```
claude --plugin-dir /path/to/kairo
```

---

## What's in the plugin

### Skills (`kairo:<name>`)

| Skill | What it does |
|---|---|
| `kairo:create-project` | Bootstraps one research project end to end: a hub note, the folder scaffold, a literature sweep, ingested paper notes, a state-of-the-art map, and 3–5 seed hypotheses, then one commit. |
| `kairo:literature-search` | Multi-facet search across arXiv, Semantic Scholar, and (for product projects) US patents, with citation-graph snowballing, retraction / withdrawal checks, and PRISMA-style counts. Returns a ranked, deduplicated, justified candidate list. |
| `kairo:hypothesis-cycle` | Vetting of a candidate claim: semantic dedup → falsifiability & novelty → known-failure checklist (including a Duhem / auxiliary-assumption check) → severe-test evaluation, with a short refinement loop. v1 (default): single critic. v2 (opt-in): a second, independent critic on the known-failure and severe-test checks, tiered by stakes, with disagreement escalated to human review rather than resolved automatically. Reads the project's `_ledger.md` first. Before creating the note, a `fresh-verifier` pass hunts concrete errors in the artifact alone (`errors_found` → filed with `verification_reviewed: false` until the researcher reviews the findings, never a verdict on truth). Optionally drafts a simplification ladder for an expensive test. Creates a `propuesta` note or logs a discard so it is not re-proposed. |
| `kairo:spawn-hypothesis` | Turns a product / engineering task that hit genuine technical uncertainty into a hypothesis, runs it through the *same* `hypothesis-cycle` gate (no reduced rigor), and cross-links the task and the hypothesis. |
| `kairo:preregister-experiment` | Writes and freezes a preregistration for one hypothesis: exact prediction, primary metric with three-way decision thresholds, a control tied to a known published result, a stopping rule, and an environment manifest. Two independent choices at freeze time: tier (`ligero`, or `completo` — a formal a-priori sample-size justification, required by `linea_publicacion` or a cost threshold) and analysis plan (`frequentist` or `bayesian`, via a versioned Bayes-factor script). Records `frozen_at` / `frozen_commit`. Produces the frozen note only — runs nothing. Optional step 0: a **simplification ladder** — 2–3 cheap exploratory rungs (`role: exploratory`, `rung: 0–2`, each a `ligero` prereg and a `Claims/` node) run before the confirmatory design, which is frozen afterwards citing which rung informed which decision. Offered, never forced, when the design is expensive or reimplements a method from text. |
| `kairo:run-experiment` | Executes a frozen preregistration: checks the environment against the frozen manifest (dependency / dataset hashes, code commit), runs the code, applies the frozen analysis plan mechanically via a bundled script, records validity + verdict, and writes the full log to a file. |
| `kairo:update-confidence` | The single writer of hypothesis `status` after `propuesta`. Implements an exact state machine; enforces the replication gate for `apoyada`; routes disagreement to `evidencia_mixta` and spawns a moderator hypothesis; keeps the digest and the state-of-the-art map in sync. Counts only confirmatory experiments (`scripts/analysis/evidence_gate.py`: exploratory rungs never move status), and runs `fresh-verifier` before any transition to `apoyada` — `errors_found` blocks it. |
| `kairo:adr-check` | For an Architecture Decision Record that cites hypotheses: compares each cited hypothesis's status now against its status when the ADR was written, flags any change, and drives the Nygard supersession lifecycle when a decision must actually change. |
| `kairo:assemble-manuscript` | For a `paper_thread` of `linea_publicacion: true` hypotheses: gates per-hypothesis on `apoyada` status + `completo`-tier evidence, then drafts Introducción / Trabajo relacionado / Método / Resultados / Discusión from Estado-del-arte.md and the qualifying hypotheses' own sections, with real APA citations. Refuses (naming exactly what's missing) around anything that doesn't clear the bar — including a hypothesis whose latest fresh verification isn't clean, and any reference the live citation gate (`resolve_refs.py --gate`) can't prove exists and isn't retracted. Runs `fresh-verifier` on each drafted section, then appends an AI-use disclosure generated mechanically from the vault's records (`ai_disclosure.py`), never written by hand. |
| `kairo:paper-to-tool` | **On demand only.** Extracts *one* specific method from a paper's own public code (`code_repo:`) into a standalone, parameterized function with a line-level source map, and validates it against that code's reference outputs under a tolerance frozen in advance (≤ 6 fix attempts). Nothing executes before the researcher approves the repo, pinned commit and exact commands. Result: a `validated` (frozen, hashed) or `rejected` tool in the vault's shared `Tools/`. See "paper-to-tool" below. |
| `kairo:evolve-program` | **On demand only.** Evaluator-first program search on [OpenEvolve](https://github.com/algorithmicsuperintelligence/openevolve) with Claude Code as the LLM (subscription, never an API key): the evaluator and a held-out split are frozen in an `EVO-XXXX` preregistration first; candidates run in a separate process with a timeout and are scored by a frozen harness they cannot edit; the best program is re-scored once on held-out and the gap reported. Per-run budget, call and iteration caps, a shown estimate and an explicit approval token. The winner is never a result — it becomes a hypothesis for `hypothesis-cycle`. |

### Subagents

| Agent | Role |
|---|---|
| `facet-searcher` | Runs all queries for **one** `literature-search` facet in its own context and returns a compact structured candidate list — never raw Atom XML or JSON. Dispatched one per facet, in parallel. |
| `facet-summarizer` | Reads the paper notes assigned to **one** state-of-the-art facet and returns a compact, fully-cited contribution to the canonical map sections. Dispatched one per facet, in parallel. |
| `second-critic` | `hypothesis-cycle` v2's independent second critic. Calls a cloud model on DeepInfra (tiered by stakes) to run Check 3 or Check 4, and relays its verdict faithfully — never substitutes its own reasoning. Requires `DEEPINFRA_TOKEN`; see "Optional companion: DeepInfra" below. |
| `fresh-verifier` | A fresh Claude instance that receives **only** the artifact — claim, cited-evidence assertions with the cited source text, the frozen preregistration and the analysis output — never the conversation, the reasoning, or prior critiques (a script builds the packet by allow-list). Returns `no_errors_found` / `errors_found` (location + why + severity) / `cannot_assess`, recorded in the note's `verifications:` list. Complements `second-critic`: that one is a different model family judging test *design*; this one is the same family with no shared context, hunting concrete *errors*. Both stay. |

### Templates & scripts

- `templates/` — note templates for projects, hypotheses, experiments, ADRs,
  tasks, and extracted tools (`tool-template.md` → `TOOL.md`). The skills copy these into your vault; you never edit them in
  place. Referenced from skills as `${CLAUDE_PLUGIN_ROOT}/templates/…`.
- `scripts/analysis/` — standard-library-only Python scripts the experiment
  skills call so statistics are applied mechanically, among them:
  - `two_proportion_test.py` — pooled two-proportion z-test with a three-way
    verdict matching a preregistration decision rule.
  - `combine_effects.py` — combines two independent effect estimates
    (random-effects DerSimonian–Laird by default) and flags heterogeneity /
    disagreement.
- `scripts/papers/verbatim_fulltext.py` — builds a paper note's
  `## Texto completo` from the paper's real text (arXiv HTML, then ar5iv,
  then the PDF via `pdftotext`), verbatim and organised by the paper's own
  section / figure / table / appendix numbering, with a `> Fuente:` line (URL,
  version, date, sha256). What can't be extracted is marked
  `[extracción dañada]`, never reconstructed. Model-written summaries go only
  in `## Notas de lectura`, which is never citable and never read by the
  citation checks or `fresh-verifier`.
- `scripts/code_repo/find_code_repo.py` — backfills `code_repo:` for papers
  ingested before the field existed. Reads public metadata only (arXiv
  comments/abstract, the paper's LaTeX source, one author-stated hop,
  Hugging Face Papers as corroboration), reports each candidate with a
  confidence and the exact sentence it came from, and writes a note only on
  an explicit `--confirm P-XXXX <url> --evidence "…"`. Never clones or runs
  anything.
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
- `scripts/security/` — keeps private material on this machine:
  - `check_bundle.py` — the isolation check every transfer bundle passes
    before it leaves the machine (`run-experiment` external runtimes,
    `paper-to-tool` Kaggle uploads, `evolve-program` runs): exit 0 clean,
    2 contaminated (keys, tokens, `.env`, `.git/`, copied vault notes, vault
    paths, unreadable archives), 1 error — never a pass. Prints finding kinds
    and paths, never a secret's value.
  - `send_guard.py` — the `send: never` guard (see "Do-not-send notes" below):
    `check` / `list` for skills, `hook` for the vault's PreToolUse hook.
- `skills/assemble-manuscript/scripts/ai_disclosure.py` — builds the
  manuscript's AI-use disclosure (Spanish + English) from recorded
  `generated_by`, `history`, `verifications:` and code-authorship fields, per
  stage, with a flag for every gap the strictest venue (ICLR 2027 / Science /
  Nature) would reject.
- `scripts/paper_to_tool/` — `paper-to-tool`'s mechanical parts:
  `sandbox_guard.py` (runs only the approved argv, never inside the vault's
  git tree or with vault paths, credentials scrubbed; not OS isolation),
  `compare_outputs.py` (pass/fail against a frozen rtol/atol), and
  `tool_hash.py` (a tool's `validation_hash`, re-verified by
  `run-experiment` pre-flight).
- `scripts/second_critic/agreement_log.py` — append-only log of every
  `hypothesis-cycle` v2 primary-vs-second-critic comparison (agree/disagree,
  actual DeepInfra cost). `summary` reports the agreement rate and warns
  explicitly if it stays suspiciously high — a rubber-stamping second critic
  should be visible, not quietly trusted. The log file itself lives in your
  vault (e.g. `Scripts/second-critic-log.jsonl`), not in this plugin.
- `templates/claim-template.md` — `Claims/C-XXXX.md` notes: lemmas,
  intermediate results, simplification-ladder rungs and evolution lineage,
  status `pendiente | probado | fallido | refutado`.
- `scripts/ledger/` — the state ledger. `claim_status.py` is the **single
  writer** of claim status (hypothesis status stays with `update-confidence`).
  `build_graph.py` builds the dependency graph across `Hipotesis/` and `Claims/`
  from `depends_on:`, flags cycles and dangling references (`crítico`) and
  propagates refutations / failures to every dependent as `importante` flags —
  never rewriting a note — and writes a compact per-project `_ledger.md` that
  `hypothesis-cycle` reads instead of re-reading the whole project.
  `verifier_packet.py` / `verifications.py` build the fresh-verifier's
  context-free packet and append its `verifications:` entries (both refuse a
  `send: never` note).
- `scripts/analysis/evidence_gate.py` — the mechanical rule that exploratory
  experiments never count as evidence (`check`, `gather`).

---

## Vault conventions the skills assume

Kairo's skills are opinionated: they encode one research method, not a set of
generic helpers. They expect a vault laid out like this:

```
Papers/                     P-XXXX <short title>.md   — shared paper library
Tools/P-XXXX/<method>/      TOOL.md + extracted tool  — shared tool library (paper-to-tool; created on first use)
Projects/<slug>/
  _hub.md                   PROJ-XXX project hub
  Hipotesis/                H-XXXX <slug>.md
  Claims/                   C-XXXX.md  (lemmas, intermediate results, rungs, lineage; created on first use)
  Experimentos/             E-XXXX.md  (+ logs/)
  Evolucion/                EVO-XXXX.md (evolve-program preregistrations; created on first use)
  Producto/                 ADR-XXX.md, F-XXX.md (tasks)
  Manuscritos/              manuscript-<paper_thread>.md (assemble-manuscript; created on first use)
  Estado-del-arte.md        the state-of-the-art map
  _digest.md                derived hypothesis table
  _ledger.md                derived dependency ledger (build_graph.py)
```

**ID schemes:** `P-` papers, `H-` hypotheses, `E-` experiments, `C-` claims,
`EVO-` evolution runs, `PROJ-` projects, `ADR-` decisions, `F-` tasks (each
zero-padded, vault-wide, max-existing + 1).

**Hypothesis status enum** — the skills use exactly these values (Spanish, as
in the original vault) and no others:

```
propuesta | en_cola | preregistrada | en_experimento | apoyada | refutada | inconclusa | evidencia_mixta
```

**Claim status enum** (`Claims/` only, written only by `claim_status.py`):

```
pendiente | probado | fallido | refutado
```

**Experiment roles** (docs/v3-interfaces.md §1d): `role: confirmatory |
exploratory`, `rung: 0–3`. Only confirmatory experiments move a hypothesis's
status.

**Do-not-send notes.** Any vault note can carry `send: never` in its
frontmatter. Its content (and, for external APIs, its metadata) never reaches
the model or a third-party service: every Kairo skill and agent that reads the
vault skips it explicitly, the citation scripts never look it up, the
fresh-verifier's packet builder refuses it, the AI-use disclosure lists it by
id only, `check_bundle.py` blocks it inside a transfer bundle, and the vault's
`send_guard` PreToolUse hook (`scripts/security/send_guard.py`, copied to
`vault/Scripts/hooks/`) refuses `Read`, content-mode `Grep`, shell commands
naming it, and Smart Connections `get_note` on it. File names and titles can
still surface in listings — give a sensitive note a neutral file name. List
flagged notes with `python scripts/security/send_guard.py list <vault>`.

If you want a running example of the layout, create a project with
`kairo:create-project` and let it scaffold one.

---

## paper-to-tool — when it triggers, and when it doesn't

When an experiment must reproduce a specific paper's method, the old default
was for Claude to reimplement it from the paper's text. That is how this
plugin's own reference vault lost an experiment: the reimplemented grokking
transformer had LayerNorm and a tied unembedding that the paper's code never
had, and the control could not reproduce the paper's result. `paper-to-tool`
extracts the method from the paper's own code instead, and proves the
extraction computes what that code computes. It is inspired by Paper2Agent
(Miao et al., *Nature* 2026), but deliberately narrow: one paper, one method,
one tool, shared across every project in the vault, never "agentify every
paper".

**It runs only when:**

- the researcher asks for it ("extract <method> from P-XXXX's code"), or
- `preregister-experiment` step 3f **offers** it (the design reproduces a
  paper's method, the paper has `code_repo:`, no tool exists yet), with a
  time/cost estimate, **and the researcher says yes**.

**It never runs:** at ingestion (`create-project` only *records* `code_repo:`),
in the background or on a schedule, for "the whole paper", or before the
researcher has approved the exact commands (a `crítico` gate: repo, pinned
commit, sandbox, isolation level, network use, exact argv).

**What preregistration does with it (step 3f):**

| Situation | What happens |
|---|---|
| `validated` tool in `Tools/` | used as is; path + `validation_hash` frozen in `environment.tools`; its exact spec copied into `## Variables`; an undeclared departure from it is `crítico` |
| `code_repo:` but no tool | `paper-to-tool` offered with an estimate; never run silently |
| no code / tool `rejected` / offer declined | reimplemented from the text as before, `method_provenance: reimplemented_from_text`, flagged `importante`: "método reimplementado desde el texto, no validado contra el código original" |

`run-experiment` re-verifies every tool hash in pre-flight, the same way it
checks dependency and dataset hashes.

**Isolation, honestly.** With Docker available, the paper's code runs in a
container with `--network none` and only the sandbox mounted. Without it,
`sandbox_guard.py` enforces location, approved-argv-only, no vault paths and
no credentials, but it is not an OS sandbox: the process could read your
files, and your approval is the real control. The sandbox defaults to
`~/.kairo-sandbox/` (`KAIRO_SANDBOX` overrides it); only the small frozen
result is copied into the vault. **No local GPU?** A GPU-only reference is
never faked on CPU: `paper-to-tool` looks for a CPU-sized reference, or builds
a Kaggle transfer bundle under `run-experiment`'s no-vault-remote rule.

---

## Optional companion: Smart Connections (semantic vault search)

`create-project` (related-project detection) and `hypothesis-cycle` (semantic
dedup of new claims against existing ones) can use a
[Smart Connections](https://github.com/brianpetro/obsidian-smart-connections)
MCP server for similarity search across notes already in your vault. This is
**optional**. When the MCP tools are not available, both skills fall back to a
manual read and say so in their output — they never silently skip the check.
Set up the MCP server separately and point it at your vault; it is not bundled
with this plugin.

---

## Optional companion: Zotero (reference manager)

`create-project` step 6 (paper ingestion) adds every confirmed paper to a local
Zotero library first, then generates the `Papers/` note from that Zotero entry
(citation, abstract) rather than writing the note directly from the raw
arXiv/Semantic Scholar/PatentsView API response. This is **optional** — when
Zotero is unreachable, ingestion falls back to writing the note directly and
says so loudly in the output (never a silent skip).

**Setup (one-time, in the Zotero desktop app — this plugin has no installer for
it):**

1. Install [Zotero](https://www.zotero.org/download/) (7 or later; write
   support needs **Zotero 10+** — check `Help → About Zotero`).
2. Install [Better BibTeX](https://retorque.re/zotero-better-bibtex/) (stable
   citation keys + a scriptable JSON-RPC endpoint): download the latest `.xpi`
   from its [releases page](https://github.com/retorquere/zotero-better-bibtex/releases),
   then in Zotero: `Tools → Plugins` → gear icon → *Install Plugin From File…*
   → pick the `.xpi` → restart if asked. Auto-updates itself after that.
3. Enable the local API: `Zotero → Settings → Advanced` → check **"Allow other
   applications on this computer to communicate with Zotero"**. This gates both
   the endpoints below; without it they 403.
4. Leave Zotero running while running Kairo skills that ingest papers.

**How the skill talks to Zotero** (no separate library/SDK — plain HTTP, all on
`localhost`, nothing installed by this plugin):

| Purpose | Call |
|---|---|
| Create the item | `POST http://127.0.0.1:23119/connector/saveItems` — the same unauthenticated endpoint the official browser connector uses; no API key needed locally. |
| Dedup / look up an existing item by DOI or arXiv id | Better BibTeX `item.search` via `POST http://127.0.0.1:23119/better-bibtex/json-rpc` |
| Stable citation key for the new item | Better BibTeX `item.citationkey` (same JSON-RPC endpoint) |
| Clean bibliographic fields for the note | Better BibTeX `item.export` with format `CSL-JSON` (same endpoint) |

The newer read/write `/api/...` local endpoint (Zotero 10+, needs a
`Zotero-API-Key` obtained via `POST /api/local/authorize`) exists but was
inconsistently available across recent Zotero point releases in testing — the
skill does not depend on it. If Better BibTeX itself is the piece that's down
(installed but its JSON-RPC unreachable), ingestion still creates the Zotero
item via `/connector/saveItems` and falls back to Zotero's own item fields for
the note, flagging that the citation key is Zotero's raw item key instead of a
Better BibTeX key.

The Obsidian-side [Zotero Integration](https://github.com/community-archive/obsidian-zotero-integration)
plugin (`obsidian-zotero-desktop-connector`) is installed alongside this for
*your* manual use inside Obsidian (insert citations, pull in PDF annotations,
etc.) — the skill automation above talks to Zotero directly and does not
depend on that plugin or drive it.

---

## Optional companion: DeepInfra (second critic, v2 dual-critic mode)

`hypothesis-cycle`'s v2 mode adds a second, independent critic — the
`second-critic` subagent — on Checks 3/4, by calling an open-weight model
hosted on [DeepInfra](https://deepinfra.com/). This is **entirely optional**.
v1 (default, single-critic) needs none of this. Without a key configured, v2
is simply unavailable and `hypothesis-cycle` runs v1.

This is the **one** paid, non-Anthropic dependency in this plugin. Set it up
only if you want the independence check; nothing else in Kairo needs it.

**Setup:**

1. Create a [DeepInfra](https://deepinfra.com/) account and an API key.
2. Set it as an environment variable named **`DEEPINFRA_TOKEN`** — deliberately
   not `ANTHROPIC_*` anything; this key authenticates to a separate, small-cost
   service, for this one purpose only. Keep it out of the vault and out of git.
3. That's it — no local server, no install. `second-critic` calls DeepInfra's
   OpenAI-compatible endpoint directly over HTTPS.

**Models and pricing** (checked against deepinfra.com on 2026-09-14 — DeepInfra's
catalog and prices move; re-verify before trusting these for real spend):

| Tier | Model | Price / 1M tokens (in / out) | Used when |
|---|---|---|---|
| `default` | `deepseek-ai/DeepSeek-V4-Flash` | $0.09 / $0.18 | routine candidates |
| `high_stakes` | `deepseek-ai/DeepSeek-V4-Pro` | $1.30 / $2.60 | `linea_publicacion: true`, or manually flagged |

A typical Check-3/Check-4 comparison is a few hundred to low-thousands of
tokens each way — cents per candidate at the default tier, still cents (not
dollars) even at the high-stakes tier for a single check.

**Cost and independence tracking:** every comparison is logged via
`scripts/second_critic/agreement_log.py` (see "Templates & scripts" above) to
a file in *your vault*, not this plugin — run its `summary` subcommand any
time to see the running agreement rate and cumulative spend. Watch for the
high-agreement warning: if the second critic agrees with the primary almost
every time over a real sample, it likely isn't adding independent signal, and
that's worth investigating (prompt leakage, too weak a model, or a checklist
that leaves no real judgement call) rather than trusting the mode by default.

**Not the same as `fresh-verifier`.** `second-critic` (this section) buys
*model-family* independence on test-design judgement, opt-in. `fresh-verifier`
buys *context* independence — a fresh Claude instance that sees only the
artifact — on concrete errors, always on, subscription only. They catch
different failures and both stay.

---

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

## Local development

```
claude --plugin-dir .          # load this repo as a plugin for one session
claude plugin validate .       # check the manifest and component paths
/reload-plugins                # pick up edits without restarting
```

---

## Licensing

**Suggested license: MIT** (see [`LICENSE`](./LICENSE)). This is a suggested
default, not a considered legal decision — it is the common choice for Claude
Code plugins and the code here is small and utility-grade. If you would prefer
a license with an explicit patent grant (Apache-2.0), a copyleft license, or a
content license for the prose-heavy skill text (e.g. CC-BY-4.0), change
`LICENSE` and the `license` fields in `.claude-plugin/plugin.json` and
`.claude-plugin/marketplace.json` before relying on it.
