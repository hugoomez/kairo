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
