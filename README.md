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

1. **No API keys — runs on a Claude subscription.** The pipeline uses Claude
   Code's own model access. The public literature sources it queries (arXiv,
   Semantic Scholar, Crossref) need no key. US-patent search (only for
   product-oriented projects) uses a free PatentsView key if you have one, and
   is skipped otherwise.
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
| `kairo:hypothesis-cycle` | Single-critic vetting of a candidate claim: semantic dedup → falsifiability & novelty → known-failure checklist (including a Duhem / auxiliary-assumption check) → severe-test evaluation, with a short refinement loop. Creates a `propuesta` note or logs a discard so it is not re-proposed. |
| `kairo:spawn-hypothesis` | Turns a product / engineering task that hit genuine technical uncertainty into a hypothesis, runs it through the *same* `hypothesis-cycle` gate (no reduced rigor), and cross-links the task and the hypothesis. |
| `kairo:preregister-experiment` | Writes and freezes a preregistration for one hypothesis: exact prediction, primary metric with three-way decision thresholds, a control tied to a known published result, a stopping rule, and an environment manifest. Records `frozen_at` / `frozen_commit`. Produces the frozen note only — runs nothing. |
| `kairo:run-experiment` | Executes a frozen preregistration: checks the environment against the frozen manifest (dependency / dataset hashes, code commit), runs the code, applies the frozen analysis plan mechanically via a bundled script, records validity + verdict, and writes the full log to a file. |
| `kairo:update-confidence` | The single writer of hypothesis `status` after `propuesta`. Implements an exact state machine; enforces the replication gate for `apoyada`; routes disagreement to `evidencia_mixta` and spawns a moderator hypothesis; keeps the digest and the state-of-the-art map in sync. |
| `kairo:adr-check` | For an Architecture Decision Record that cites hypotheses: compares each cited hypothesis's status now against its status when the ADR was written, flags any change, and drives the Nygard supersession lifecycle when a decision must actually change. |

### Subagents

| Agent | Role |
|---|---|
| `facet-searcher` | Runs all queries for **one** `literature-search` facet in its own context and returns a compact structured candidate list — never raw Atom XML or JSON. Dispatched one per facet, in parallel. |
| `facet-summarizer` | Reads the paper notes assigned to **one** state-of-the-art facet and returns a compact, fully-cited contribution to the canonical map sections. Dispatched one per facet, in parallel. |

### Templates & scripts

- `templates/` — note templates for projects, hypotheses, experiments, ADRs,
  and tasks. The skills copy these into your vault; you never edit them in
  place. Referenced from skills as `${CLAUDE_PLUGIN_ROOT}/templates/…`.
- `scripts/analysis/` — two standard-library-only Python scripts the experiment
  skills call so statistics are applied mechanically:
  - `two_proportion_test.py` — pooled two-proportion z-test with a three-way
    verdict matching a preregistration decision rule.
  - `combine_effects.py` — combines two independent effect estimates
    (random-effects DerSimonian–Laird by default) and flags heterogeneity /
    disagreement.

---

## Vault conventions the skills assume

Kairo's skills are opinionated: they encode one research method, not a set of
generic helpers. They expect a vault laid out like this:

```
Papers/                     P-XXXX <short title>.md   — shared paper library
Projects/<slug>/
  _hub.md                   PROJ-XXX project hub
  Hipotesis/                H-XXXX <slug>.md
  Experimentos/             E-XXXX.md  (+ logs/)
  Producto/                 ADR-XXX.md, F-XXX.md (tasks)
  Estado-del-arte.md        the state-of-the-art map
  _digest.md                derived hypothesis table
```

**ID schemes:** `P-` papers, `H-` hypotheses, `E-` experiments, `PROJ-`
projects, `ADR-` decisions, `F-` tasks (each zero-padded, max-existing + 1).

**Hypothesis status enum** — the skills use exactly these values (Spanish, as
in the original vault) and no others:

```
propuesta | en_cola | preregistrada | en_experimento | apoyada | refutada | inconclusa | evidencia_mixta
```

If you want a running example of the layout, create a project with
`kairo:create-project` and let it scaffold one.

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
