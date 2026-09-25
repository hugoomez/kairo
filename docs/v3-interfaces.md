# Kairo v3 — interfaces and file ownership (Blocks A / B)

Baseline: tag `v2-final` (plugin `26f44c9`, vault `8b96dff`). Block A works on
branch `v3-block-a`, Block B on `v3-block-b`, both forked from the commit that
added this file. **This document is frozen for both blocks** — neither edits it.
If a block needs an interface changed, it writes
`docs/v3-pending/<A|B>-interface-<topic>.md` and codes against the interface as
written here until integration.

## 1. Interfaces

### 1a. Bundle isolation check — built by A4, called by B3

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/security/check_bundle.py" <bundle_dir>
```

| Exit | Meaning |
|---|---|
| `0` | clean — no `block` findings (`warn` findings may be present) |
| `2` | contaminated — at least one `block` finding |
| `1` | error — the check could not run (bad path, unreadable file, crash). Callers treat `1` as **not clean**. |

Stdout is exactly one JSON object (UTF-8, no other stdout output; diagnostics go to stderr):

```json
{
  "status": "clean | contaminated | error",
  "findings": [
    {"path": "relative/to/bundle_dir.md", "kind": "<string>", "severity": "block | warn"}
  ]
}
```

- `status` agrees with the exit code (`0`↔`clean`, `2`↔`contaminated`, `1`↔`error`).
- `path` is relative to `<bundle_dir>`, forward slashes.
- `kind` is a short snake_case string defined and documented by A4. Callers
  branch only on the exit code / `status`, never on `kind`.
- `findings` is `[]` when clean. On `error`, it may be `[]`.
- Until A4 lands, B3 tests against its own test double (a stub inside B's
  tree or a mocked subprocess); B never creates files under `scripts/security/`.

### 1b. Verification records — written by B2 (`fresh-verifier`), read by A2 (AI-use disclosure)

A `verifications:` list in the frontmatter of the verified note. Append-only:
new runs add an entry, existing entries are never edited or removed.

```yaml
verifications:
  - verifier: kairo/fresh-verifier@<version>   # agent id + version
    model: <model id, e.g. claude-opus-5-5>
    date: <YYYY-MM-DD>
    verdict: no_errors_found | errors_found | cannot_assess
    scope: note | section:<exact heading text>
```

- Absent field or `[]` = never verified.
- `verdict` is exactly one of the three values; no others.
- `scope: note` = the whole note; `section:<heading>` = one `##` section,
  heading text verbatim without the `## ` prefix.
- Readers (A2) report every entry; they must not treat `no_errors_found` as
  "correct", only as "a verifier found no errors in <scope>".

### 1c. Citation resolution — written by A1, readable by anyone

Three fields added to `Papers/P-XXXX.md` frontmatter:

```yaml
resolved: true | false
openalex_id: <W followed by digits, e.g. W2741809807 — empty when resolved is false>
resolution_checked: <YYYY-MM-DD>
```

- All three absent = never checked (every v2 paper note).
- `resolved: true` ⇒ `openalex_id` non-empty. `resolved: false` ⇒ checked and
  not found (or ambiguous); `openalex_id` empty.
- `resolution_checked` is the date of the last check, updated on every re-check.

### 1d. Experiment roles — written by B1, respected by `update-confidence`

Two fields in `Experimentos/E-XXXX.md` frontmatter, set by
`preregister-experiment` and frozen with the rest of the preregistration:

```yaml
role: confirmatory | exploratory
rung: 0 | 1 | 2 | 3
```

- `rung` is an integer 0–3, higher = stronger evidential rung; B1 defines what
  each rung means (in `preregister-experiment` / `update-confidence`).
- `update-confidence` never moves a hypothesis's `status` on an `exploratory`
  experiment's verdict.
- Absent `role` (v2 experiment notes) = read as `confirmatory`; absent `rung`
  = unknown. Readers must not fail on either being absent.

## 2. File ownership

Every file in the plugin repo has exactly one owner. A block edits **only**
files it owns (new files included — they belong to whoever owns the
directory/row they fall under). Anything else goes through a pending file.

**Pending changes:** `docs/v3-pending/<A|B>-<topic>.md`, one file per intended
change, containing (1) the target file, (2) the exact snippet or edit, (3) how to
verify it after it is applied. Each block owns only its own `A-*` / `B-*` files.

| Path | Owner |
|---|---|
| `scripts/citations/**` (new) | A |
| `scripts/security/**` (new) | A |
| `scripts/code_repo/**` | A |
| `scripts/paper_to_tool/**` | A |
| `skills/literature-search/**` | A |
| `skills/create-project/**` (incl. its inline Paper note format, where A1's fields go) | A |
| `skills/paper-to-tool/**` | A |
| `skills/run-experiment/**` (bundle-check wiring only; nothing else changes in v3) | A |
| `skills/assemble-manuscript/**` (citation gate, AI disclosure) — **B2's wiring here is deferred** | A |
| `agents/facet-searcher.md`, `agents/facet-summarizer.md` | A |
| `templates/project-template.md`, `templates/tool-template.md` | A |
| `docs/v3-pending/A-*.md` | A |
| `agents/fresh-verifier.md` (new) | B |
| `agents/second-critic.md` | B |
| `scripts/ledger/**` (new) | B |
| `scripts/analysis/**` | B |
| `scripts/second_critic/**` | B |
| `skills/evolve-program/**` (new) | B |
| `skills/hypothesis-cycle/**` | B |
| `skills/update-confidence/**` | B |
| `skills/preregister-experiment/**` | B |
| `templates/claim-template.md` and any other new template for `Claims/` | B |
| `templates/experiment-template.md` (B1's `role` / `rung`) | B |
| `templates/hypothesis-template.md` | B |
| `docs/v3-pending/B-*.md` | B |
| `README.md` | INTEGRATION |
| B2's wiring into `skills/assemble-manuscript` | INTEGRATION (via `B-*` pending file) |
| The entire vault repo (`Kairo/`), incl. `vault/.claude/settings.json`, outer `Kairo/.claude/settings.json`, `vault/Scripts/hooks/*` | INTEGRATION |
| `docs/v3-interfaces.md`, `docs/v3-pending/README.md` | INTEGRATION |
| `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` (version bump) | INTEGRATION |
| `skills/adr-check/**`, `skills/serendipity-scan/**`, `skills/spawn-hypothesis/**` | INTEGRATION |
| `templates/adr-template.md`, `templates/task-template.md` | INTEGRATION |
| `audits/**` (removed 2026-09-25: audits live in the vault, see README), `docs/superpowers/**`, `LICENSE`, `.gitattributes`, `.gitignore` | INTEGRATION |

Notes on boundaries (adjusted from the initial split after reading the files):

- `scripts/analysis/**` → B: `combine_effects.py` is `update-confidence`'s
  combiner and `sample_size.py` is `preregister-experiment`'s. `run-experiment`
  (A) only calls the analysis scripts and must not edit them.
- `templates/experiment-template.md` → B: B1 adds `role` / `rung`. If A4 wants
  the bundle-check result recorded in the experiment note, `run-experiment`
  writes it in the `## Resultado` body. A new frontmatter field needs an `A-*` pending file.
- `run-experiment` stays A even though B1 introduces roles. The role rule is
  enforced in `update-confidence` (1d). Any B change to `run-experiment` is a `B-*` pending file.
- There is no Paper template file; the Paper note format lives inside
  `skills/create-project/SKILL.md`, so A1's fields land there (A).
- New test files belong to the owner of the directory they sit in.

## 3. Integration amendments (2026-09-25)

`docs/v3-pending/` was applied and deleted at integration; its files remain in
git history (`git log -- docs/v3-pending`). References to it in §1–§2
describe the parallel phase.

Applied at integration, after reading `A-contract-issues.md` and
`B-contract-issues.md`. §1–§2 above are unchanged; where this section adds a
rule, it wins. Each item names the issue it resolves.

### 3a. §1b verification records

- **Governing entry (B#2, A#4, A#5).** For gating, the last entry *in file
  order* with a given `scope` governs; entries of different scopes never
  supersede each other (a later `scope: note` pass does not clear an earlier
  `section:X` `errors_found`). For reporting, list every entry. Order within a
  date is list order, which is safe because the list is append-only.
- **Manuscript gate (A2 × B2).** `assemble-manuscript` excludes a hypothesis
  when its governing `note` entry is not `no_errors_found` **or** any
  `section:` scope's governing entry is `errors_found` — the same condition
  under which A2's disclosure raises `crítico`. The `apoyada` gate in
  `update-confidence` and the `preregister-experiment` gate keep B2's
  `note`-scope rule.
- **Extra keys (B#1): not adopted.** Entries keep exactly the five keys.
  Findings and the packet sha256 stay in the note's append-only
  `## Verificación independiente`, linked by (date, verifier, scope) + order.
  Readers must still tolerate unknown keys (A2 does).
- **Out-of-enum verdicts (A#7).** Readers quote them verbatim, never interpret
  them, and treat them as not clean: `verifications.py gate` blocks on any
  governing `note` verdict other than `no_errors_found` while
  `verification_reviewed` is not `true`. The manuscript gate (above) has no
  such escape: it needs a `no_errors_found` entry.
- **Review of verifier findings (amended 2026-09-25).** Verifier findings are
  tracked in a dedicated frontmatter field, `verification_reviewed: true |
  false`, never in `needs_human_review`:
  - `verifications.py append` sets it to `false` on any verdict other than
    `no_errors_found`, and a new bad entry resets it.
  - Only the researcher sets it to `true`, after reviewing the findings.
  - `needs_human_review` keeps its other causes (rounds exhausted, critic
    disagreement, an invalid run), so clearing it never clears verifier
    findings.
  - `verifications.py gate`, which preregistration requires to be clear,
    blocks on either field.
- **Scope drift (A#6).** A `section:` scope whose heading no longer exists is
  reported (A2: `menor`); re-verify under the new heading.
- **`send: never` (A#8).** A `send: never` note is never verified:
  `verifier_packet.py` refuses it (and any `--experiment` that is flagged),
  gives a flagged cited paper no source text, and `verifications.py append`
  refuses to write an entry on it. Readers ignore entries found on one.

### 3b. §1c citation resolution (A#1, A#2)

- `resolution_status: resolved | unresolved | mismatch | retracted | withdrawn`
  (plus A1's `resolution_match`, `resolution_evidence`) is part of the
  contract. **Any reader deciding whether a paper can be cited uses
  `resolution_status`, never `resolved` alone** — a retracted paper is
  `resolved: true`.
- The invariant `resolved: true ⇒ openalex_id` stays strict. A paper
  confirmed only by a fallback registrar is `resolved: false`,
  `resolution_status: unresolved`, with the source in `resolution_evidence`;
  the manuscript gate refuses it until the researcher decides by hand.

### 3c. §1d experiment roles (B#3)

- `role: confirmatory` ⇒ `rung: 3` or absent. `role: exploratory` ⇒ `rung` ∈
  {0, 1, 2} or absent (non-ladder exploratory work). Enforced by
  `scripts/analysis/evidence_gate.py check` (`crítico` on violation).

### 3d. New ids, folders and fields (B#4, A#13, A2 authorship)

- `C-XXXX` claims in `Projects/<slug>/Claims/`; `EVO-XXXX` evolution runs in
  `Projects/<slug>/Evolucion/` (vault-wide, zero-padded, max + 1).
  `spawned_from` accepts `EVO-XXXX`. `check_bundle.py` (≥ 1.2.0) treats a
  `C-`/`EVO-`/`F-` note and `_ledger.md` as a copied vault note (`vault_note`,
  block).
- `send: never` (any note, frontmatter): content never reaches a model or an
  external API. Detection is `scripts/security/send_guard.py` (`is_flagged`);
  other scripts import it rather than re-implementing it, except
  `ai_disclosure.py`, which stays standalone with the same regex semantics.
- Experiment notes gain `generated_by` (set at freeze by
  `preregister-experiment`) and `code_generated_by` (set by `run-experiment`
  step 0, not frozen). Both are read by A2's disclosure.

### 3e. Hooks (A#14)

The `send_guard` hook ships as a vault copy (`vault/Scripts/hooks/send_guard.py`,
byte-identical to the plugin's canonical `scripts/security/send_guard.py`),
not as a plugin `hooks/hooks.json`: the vault's settings are where every
other Kairo hook lives, and a plugin hook would also fire in non-vault
projects. Re-copy it whenever the plugin copy changes (compare sha256).
