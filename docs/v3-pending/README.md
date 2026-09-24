# v3 pending changes

Changes a block wants in a file it does not own (see `docs/v3-interfaces.md` §2).
The integration session applies them after merging `v3-block-a` and `v3-block-b`.

One file per change: `A-<topic>.md` (Block A) or `B-<topic>.md` (Block B). Each contains:

1. **Target** — the file path (repo-relative; prefix `Kairo/` for the vault repo).
2. **Change** — the exact snippet to insert, or the exact before → after edit.
3. **Verify** — how to confirm it works once applied.
