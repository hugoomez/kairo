# A-send-never-readers — explicit `send: never` skips outside Block A (A3)

A3 adds the frontmatter field `send: never`: the note's content (and metadata,
for external APIs) must never reach the model or a third-party service. The
Block A readers already skip flagged notes: `agents/facet-summarizer.md`,
`agents/facet-searcher.md`, `skills/create-project`, `skills/assemble-manuscript`,
`scripts/citations/*`, `skills/assemble-manuscript/scripts/ai_disclosure.py`,
`scripts/security/check_bundle.py` (`vault_note`). The `PreToolUse` hook
(`A-hook.md`) is the backstop.

The files below read vault notes but belong to Block B or INTEGRATION.
Integration applies each edit verbatim.

**How to test for the flag** (used in every snippet):
`python "${CLAUDE_PLUGIN_ROOT}/scripts/security/send_guard.py" check <note path> …`
exits `3` if any given note is flagged and prints paths only, never content. A
`Read` refused by the `send_guard` hook means the same thing and is never
retried through another tool.

---

## 1. `skills/hypothesis-cycle/SKILL.md` (owner B)

**Change 1.** In `## Citation requirement`, directly after the paragraph that
ends "…cite that section instead." (the one beginning **"Before writing any
such locator, re-open the source `Papers/P-XXXX.md` note"**), insert:

```markdown
**`send: never` papers are not citable evidence.** If the paper note's
frontmatter has `send: never` (check with
`python "${CLAUDE_PLUGIN_ROOT}/scripts/security/send_guard.py" check <note>` —
exit 3 — or a `Read` refused by the `send_guard` hook), do not open it and do
not cite a locator in it: the claim must stand on other papers, or the
researcher adds that citation by hand. Flag it `importante` in the cycle output
(`P-XXXX omitida: send: never`).
```

**Change 2.** In v2 dual-critic mode, the second critic sends the candidate
package to DeepInfra, an external API. Where the skill describes dispatching
`second-critic` (the Check 3/4 v2 section), add:

```markdown
Never send a package that includes content from a `send: never` note (the
candidate itself, or a cited paper) to `second-critic` — it leaves the machine.
Run that check single-critic (v1 behaviour) and flag it `importante`:
`v2 omitido para <id>: contiene material send: never`.
```

**Verify:** `grep -n "send: never" skills/hypothesis-cycle/SKILL.md` → two hits.

## 2. `skills/serendipity-scan/SKILL.md` (owner INTEGRATION)

In `## Mechanism 2` → `### 1. Build the project's field-A neighborhood`, after
"Take every `Papers/` note with this `PROJ-XXX` in `projects:` as seeds.",
insert:

```markdown
Exclude every note with `send: never` in its frontmatter (list them with
`python "${CLAUDE_PLUGIN_ROOT}/scripts/security/send_guard.py" list Papers --json`):
its ids and metadata are not sent to Semantic Scholar. Say how many were
excluded (`menor`).
```

Apply the same rule to Mechanism 1 wherever it reads project notes as query
material.

**Verify:** `grep -n "send: never" skills/serendipity-scan/SKILL.md` → ≥ 1 hit.

## 3. `skills/adr-check/SKILL.md` (owner INTEGRATION)

In `## Procedure`, step 2, add a bullet:

```markdown
   - if the hypothesis note has `send: never`, don't open it: report
     `cannot verify H-YYYY (send: never)` as an unknown-status warning — the
     researcher checks it by hand.
```

**Verify:** grep shows the bullet.

## 4. `skills/update-confidence/SKILL.md`, `skills/preregister-experiment/SKILL.md` (owner B)

Both read hypothesis and experiment notes they are asked to act on. Add one line
to each skill's `## Inputs` (or the first procedure step):

```markdown
If the target note has `send: never`, stop and tell the researcher: this skill
must read the note to act on it, and the note is marked not-to-send. Don't work
around the `send_guard` hook.
```

**Verify:** grep shows one hit per file.

## 5. Templates (owners B / INTEGRATION)

`templates/hypothesis-template.md`, `templates/experiment-template.md` (B);
`templates/adr-template.md`, `templates/task-template.md` (INTEGRATION). Add this
commented optional field at the end of each frontmatter, the same text already
in `templates/project-template.md` (A):

```yaml
# send — optional. `send: never` keeps this note's content from ever reaching
# the model or an external API: skills skip it and the vault's send_guard
# PreToolUse hook blocks reading it. Omit the field for normal notes.
# send: never
```

**Verify:** `grep -c "send: never" templates/*.md` → ≥ 1 per edited file.

## 6. `README.md` (owner INTEGRATION)

See `A-readme.md`, which carries the `send: never` paragraph together with the
other Block A README changes.
