# B-contract-issues — gaps found in `docs/v3-interfaces.md` while implementing Block B

Each issue is written against the frozen contract and is **not** applied. Block
B codes against the contract exactly as written until integration. The
orchestrator may append further issues below.

---

## 1. §1b has no field for findings or for the verified context

**Target:** `docs/v3-interfaces.md` §1b (verification records, written by B2,
read by A2).

**Issue:** The contract's `verifications:` entry has exactly five keys
(`verifier`, `model`, `date`, `verdict`, `scope`). An `errors_found` verdict is
only actionable with its findings (location, why, severity `crítico` /
`importante` / `menor`), and a verdict is only auditable if you can tell which
exact context the verifier received. Neither fits in the contract, and adding
keys would break "exactly those five keys". So a reader of the frontmatter
alone (A2's disclosure, a gate in another skill) sees that errors were found,
but not what they were or what was checked.

**Workaround (implemented in B2):** the frontmatter entry stays exactly
contract-shaped. `scripts/ledger/verifications.py append --report <agent
output> --packet-sha256 <hash>` also appends a dated entry to an append-only
body section, `## Verificación independiente`, of the verified note. The entry
carries verifier@version, model, verdict, scope, the packet's sha256 (the
packet and its manifest come from `scripts/ledger/verifier_packet.py`), and the
findings list with severity tag and location. Frontmatter entries and body
entries are linked only by (date, verifier, scope) plus order, not by an id.

**Proposed contract change:** allow two optional keys per entry, which readers
that don't need them may ignore:

```yaml
    packet_sha256: <hex sha256 of the exact packet the verifier received>
    findings: <n>        # count only; details stay in ## Verificación independiente
```

Or, as a minimum, a single `ref:` key pointing at the body entry's heading.
This makes the frontmatter-to-body link explicit instead of positional.

---

## 2. §1b "latest entry governs" is implied, not stated

**Target:** `docs/v3-interfaces.md` §1b.

**Issue:** The contract says entries are append-only and a re-run adds an
entry, but it doesn't say which entry is authoritative for gating when several
exist for the same scope. B2's gates (`update-confidence` before `apoyada`,
`preregister-experiment`, and the pending `assemble-manuscript` row) all use
**the latest entry per scope**. A2 reads every entry for disclosure, which is
consistent with that, but a future reader could reasonably pick "any
`errors_found` ever" instead.

**Workaround:** documented in the skills and in `verifications.py latest` /
`gate`. The latest entry per scope governs, and every entry is still reported.

**Proposed contract change:** add one line to §1b: "For gating, the last entry
(file order) with a given `scope` governs. For reporting, list all entries."

---

## 3. §1d does not constrain the `role` × `rung` combination

**Target:** `docs/v3-interfaces.md` §1d.

**Issue:** §1d defines `role` and `rung` independently and leaves the meaning
of each rung to B1. B1 defines rung 3 as "the claim's own conditions", so a
confirmatory experiment is rung 3 by definition and exploratory rungs are
0–2. Nothing in the contract says so, so another reader (Block A's
`run-experiment`, A2's disclosure) could legitimately accept
`role: confirmatory, rung: 1`.

**Workaround (implemented in B1):** `scripts/analysis/evidence_gate.py check`
refuses `confirmatory` with `rung` ≠ 3 as `crítico` (it must be fixed by an
amendment before it can count), and exploratory rungs are created with rung 0–2
only (`claim_status.py` refuses `--rung 3` for a rung claim). An absent `rung`
is accepted, per the contract.

**Proposed contract change:** add to §1d: "`role: confirmatory` ⇒ `rung: 3`
(or absent); `role: exploratory` ⇒ `rung` ∈ {0, 1, 2} or absent (non-ladder
exploratory work, e.g. program evolution)."

---

## 4. No id scheme for evolve-program runs

**Target:** `docs/v3-interfaces.md` (new), README id schemes.

**Issue:** B3's evaluator preregistration has no primary hypothesis — the
hypothesis only exists after the search — so it cannot be an `E-XXXX`
experiment note (whose `hypothesis:` is exactly one). The contract has no id
for it.

**Workaround:** `Projects/<slug>/Evolucion/EVO-XXXX.md` (vault-wide ids),
format inline in `skills/evolve-program/SKILL.md`. `claim_status.py` accepts
`--source EVO-XXXX` for lineage claims; `build_graph.py` does not check
`source:` for dangling ids (only `depends_on` / `about`). The hypothesis filed
from a winning program uses `spawned_from: EVO-XXXX`, outside the template's
documented `<H-XXXX | E-XXXX | F-XXX>`.

**Proposed contract change:** register `EVO-` as an id prefix and `Evolucion/`
as a project folder; extend `spawned_from` to accept `EVO-XXXX`.

---

## 5. (Spec interpretation, not the contract) — does the verifier see `## Justificación`?

**Target:** the Block B spec for B2 ("never the conversation, the
justification, prior critiques, or the reasoning that produced it") vs.
`scripts/ledger/verifier_packet.py`.

**Issue:** the packet includes `## Justificación (evidencia citada)`. Read
literally, the spec forbids it by name. B2 reads "the justification" as the
*argument for why the claim holds* (the reasoning), and the section's
citation bullets as *assertions that are part of the artifact* — "P-XXXX
§locator shows X" is a factual claim the note makes, and checking it against
the cited source text is the verifier's main job (it is how the real
`citation-verification.md` error was caught). Dropping the section would make
citation errors unverifiable.

**Workaround (implemented):** the packet carries each bullet quoted verbatim
as an `Afirmación N` with the verbatim source text of its locator, plus each
paper's abstract labelled as non-locator context. `## Revisión del ciclo`,
`## Hipótesis rival descartada`, `## Lección`, `## Verificación
independiente`, history and all other frontmatter are excluded, and
`--section` cannot reach them.

**Decision needed from the researcher:** keep this reading, or reduce each
bullet to "assertion clause + locator + source text" (dropping any
free-standing argumentative bullet). No contract change either way.
