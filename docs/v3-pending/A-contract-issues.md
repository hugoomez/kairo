# A-contract-issues — problems found in `docs/v3-interfaces.md` (Block A)

The contract was **not** edited. Each item says what Block A did instead.
Items are ordered by how much they matter to integration.

## §1c Citation resolution

1. **`resolved` can't express the five outcomes the spec asks for**
   (`resolved | unresolved | mismatch | retracted | withdrawn`). A retracted
   paper *exists* (it has an OpenAlex id), so `resolved` alone would call it
   good.
   **Workaround:** A1 writes the three §1c fields exactly as specified and
   adds A-owned extension fields to the Paper note format (`create-project`,
   owned by A): `resolution_status`, `resolution_match`,
   `resolution_evidence`. Retracted or withdrawn papers get
   `resolved: true` plus `resolution_status: retracted | withdrawn`.
   **Readers other than A must use `resolution_status`, not `resolved`,** to
   decide whether a paper can be cited. Suggest adding `resolution_status` to
   §1c at integration.
2. **The invariant `resolved: true ⇒ openalex_id` rejects papers that exist
   but aren't in OpenAlex.** Example: P-0003's 2019 arXiv version, which arXiv
   confirms exactly.
   **Workaround:** the invariant is kept strictly. A fallback-only
   confirmation writes `resolved: false`,
   `resolution_status: unresolved`, and the confirming source goes in
   `resolution_evidence` (flag `importante`). The manuscript gate refuses
   these papers.
   Decide at integration whether §1c should allow `resolved: true` with an
   empty `openalex_id` when a registrar (Crossref/arXiv) confirms the paper.
3. **The premise that OpenAlex "requires" a key since Feb 2026 is only half
   true.** Current docs (help.openalex.org/api/authentication, read
   2026-09-24) say basic keyless use still works on a small daily budget and
   that a free key raises it 10×. Singleton DOI lookups are free. A1 therefore
   runs keyless when `OPENALEX_API_KEY` is unset. When the budget or network
   fails, the lookup is marked *lost*: `--gate` exits 1, never a pass.

## §1b Verification records (reported by A2, which reads them)

4. **There is no time or sequence field.** Two entries on the same date are
   ordered by list position. That is safe only because the list is
   append-only.
5. **Scope supersession is undefined.** It isn't stated whether a later
   `scope: note` pass clears an earlier `section:X` error. A2 reads scopes
   literally: a later note-scope entry does not clear an earlier section-scope
   error.
6. **Scope drift.** `section:<heading>` breaks when the heading is renamed.
   A2 flags `menor`.
7. **No reader behaviour is defined for out-of-enum verdicts.** A2 quotes
   them verbatim and flags `importante`.
8. **`send: never` × verifications (A3 × B2).** The fresh verifier must not
   read a `send: never` note, because its content would reach a model.
   **B2's `agents/fresh-verifier.md` should skip flagged notes** and write no
   `verifications:` entry on them. A2 ignores any entries it finds on them.
   Not a Block A file: raise it with Block B at integration.
9. **`history.by` is free text,** so telling a human from an agent is
   heuristic. A structured `by_kind: human | agent` field would make the
   disclosure exact.

## §1a Bundle check (reported by A4, which implements it)

10. An archive member's finding path is `<archive>!<member>`, which isn't a
    real filesystem path. Harmless, since callers never branch on `path`.
11. On exit 1, `findings` may list findings found before the failure. §1a
    allows this ("may be `[]`").
12. **Severity of `Papers/` / `Projects/` mentions inside file contents:**
    `vault_path_reference` is a `warn`. What blocks: a `Papers/` or
    `Projects/` path *in the bundle*, an absolute vault path, and a copied
    vault note. Mentions in content only warn, because E-0001's compliant
    guarded local-first fallback (allowed by `run-experiment` step 2) names a
    `Projects/…` path. Blocking on it would make that bundle untransferable.
    Changing it is a one-line change, if integration wants content mentions
    to block.

## Not in the contract at all

13. **`send: never` (A3) is a new cross-cutting field.** Block B's readers
    (`hypothesis-cycle`, `update-confidence`, `preregister-experiment`,
    `fresh-verifier`, `evolve-program`) need to respect it; see
    `A-send-never-readers.md`. `check_bundle.py` already blocks a flagged note
    inside a bundle (`vault_note`), which covers B3's evolve-program bundles.
14. **The hook script has no owned home.** A PreToolUse hook in the vault's
    `settings.json` can't reference `${CLAUDE_PLUGIN_ROOT}`, and
    `vault/Scripts/hooks/` belongs to INTEGRATION.
    **Workaround:** the canonical script is `scripts/security/send_guard.py`
    (A), and `A-hook.md` has integration copy it into `vault/Scripts/hooks/`.
    **Alternative worth deciding at integration:** ship it as a *plugin* hook
    (`hooks/hooks.json` at the plugin root, currently unowned in §2). It would
    then travel with the plugin and use `${CLAUDE_PLUGIN_ROOT}`, with no copy
    to keep in sync.
15. **Retraction-sweep flags on hypotheses.**
    `retraction_sweep.py --write` appends a dated line to a
    `## Revisión de vigencia` section in citing hypotheses (and ADRs). ADRs
    already use that section (`adr-check`). Hypotheses don't have it in
    `templates/hypothesis-template.md` (B). The sweep creates it when absent.
    It never touches `status`. Integration may want the template to mention
    the section.
