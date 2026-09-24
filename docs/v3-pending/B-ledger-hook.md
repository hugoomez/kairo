# B-ledger-hook — regenerate `_ledger.md` on every Hipotesis/ or Claims/ write

Block B4 (`scripts/ledger/build_graph.py`). The hook lives in the vault repo's
settings, which are INTEGRATION-owned (`docs/v3-interfaces.md` §2), so this file
carries the exact change instead of applying it. It was **not** tested against
the real vault from Block B (per the parallel-mode rules); the hook-mode code
path itself is covered by `scripts/ledger/test_ledger.py`
(`test_hook_mode_rebuilds_only_that_project_and_never_fails`).

## 1. Target

Two files, kept in sync exactly like the existing `regen_digest.py` hooks:

- `Kairo/vault/.claude/settings.json` — canonical (session rooted at `vault/`)
- `Kairo/.claude/settings.json` — the mirror for sessions rooted at `Kairo/`
  (same rules, `vault/` prefix on the `if` globs and the vault path)

## 2. Change

Rules follow the existing file's conventions: `type: "command"` only, **one
tool per `if` rule** (`Write(...)` and `Edit(...)` are separate entries),
**single `*` globs** (no `**`). Four rules per file: {Write, Edit} × {Hipotesis,
Claims}.

The plugin path is absolute because project-scope hooks do not get
`${CLAUDE_PLUGIN_ROOT}` (only plugin-shipped hooks do) — same situation as the
absolute paths already in `vault/.mcp.json`. Use the path of the plugin checkout
the vault actually loads (`--plugin-dir`); after integration that is the main
repo `C:/Users/gomez/kairo-plugin`. If the plugin moves, edit these four
commands.

### 2a. `Kairo/vault/.claude/settings.json`

Append these four objects to `hooks.PostToolUse[0].hooks` (the `"matcher":
"Write|Edit"` block), after the two `regen_digest.py` entries:

```json
          {
            "type": "command",
            "command": "python \"C:/Users/gomez/kairo-plugin/scripts/ledger/build_graph.py\" --hook",
            "if": "Write(Projects/*/Hipotesis/*)",
            "timeout": 15,
            "statusMessage": "Regenerating _ledger.md"
          },
          {
            "type": "command",
            "command": "python \"C:/Users/gomez/kairo-plugin/scripts/ledger/build_graph.py\" --hook",
            "if": "Edit(Projects/*/Hipotesis/*)",
            "timeout": 15,
            "statusMessage": "Regenerating _ledger.md"
          },
          {
            "type": "command",
            "command": "python \"C:/Users/gomez/kairo-plugin/scripts/ledger/build_graph.py\" --hook",
            "if": "Write(Projects/*/Claims/*)",
            "timeout": 15,
            "statusMessage": "Regenerating _ledger.md"
          },
          {
            "type": "command",
            "command": "python \"C:/Users/gomez/kairo-plugin/scripts/ledger/build_graph.py\" --hook",
            "if": "Edit(Projects/*/Claims/*)",
            "timeout": 15,
            "statusMessage": "Regenerating _ledger.md"
          }
```

### 2b. `Kairo/.claude/settings.json` (mirror)

Same four objects, with the `if` globs prefixed by `vault/`:

```json
          {
            "type": "command",
            "command": "python \"C:/Users/gomez/kairo-plugin/scripts/ledger/build_graph.py\" --hook",
            "if": "Write(vault/Projects/*/Hipotesis/*)",
            "timeout": 15,
            "statusMessage": "Regenerating _ledger.md"
          },
          {
            "type": "command",
            "command": "python \"C:/Users/gomez/kairo-plugin/scripts/ledger/build_graph.py\" --hook",
            "if": "Edit(vault/Projects/*/Hipotesis/*)",
            "timeout": 15,
            "statusMessage": "Regenerating _ledger.md"
          },
          {
            "type": "command",
            "command": "python \"C:/Users/gomez/kairo-plugin/scripts/ledger/build_graph.py\" --hook",
            "if": "Write(vault/Projects/*/Claims/*)",
            "timeout": 15,
            "statusMessage": "Regenerating _ledger.md"
          },
          {
            "type": "command",
            "command": "python \"C:/Users/gomez/kairo-plugin/scripts/ledger/build_graph.py\" --hook",
            "if": "Edit(vault/Projects/*/Claims/*)",
            "timeout": 15,
            "statusMessage": "Regenerating _ledger.md"
          }
```

The command needs no vault argument: `--hook` reads the PostToolUse stdin JSON,
takes `tool_input.file_path`, walks up to `Projects/<slug>/`, and rebuilds only
that project's `_ledger.md` (the graph itself is built vault-wide, so
cross-project `depends_on` resolves). It always exits 0 — a ledger failure never
breaks the session; errors go to stderr.

**What the hook does NOT catch:** `claim_status.py` and `update-confidence`'s
own scripts write notes from Bash, not through the Write/Edit tools, so no
PostToolUse hook fires for them. The skills therefore also run
`build_graph.py --vault <vault> --project <slug> --write` explicitly after
those writes (see `hypothesis-cycle`, `update-confidence`,
`preregister-experiment`). The hook is the safety net for hand edits and
note-creating Write calls, not the only trigger.

## 3. Verify (real test, after applying — run once, then clean up)

Start Claude Code **from `Kairo/vault/`** (the canonical root; `/hooks` should
list the four new rules), then:

1. `git -C Kairo/vault status --short` → note the baseline (should be clean).
2. With the **Write** tool, create
   `Projects/_ledgertest/_hub.md` = `---\nid: PROJ-999\n---\n` and
   `Projects/_ledgertest/Hipotesis/H-9001 prueba.md` =
   `---\nid: H-9001\nproject: PROJ-999\nstatus: propuesta\ndepends_on: [C-9001]\n---\n\n## Claim\n\nprueba\n`.
   → `Projects/_ledgertest/_ledger.md` must appear, with a **crítico**
   `depends_on → C-9001: no existe ninguna nota con ese id` (dangling). This
   proves `Write(Projects/*/Hipotesis/*)`.
3. With the **Write** tool, create `Projects/_ledgertest/Claims/C-9001.md` =
   `---\nid: C-9001\nproject: PROJ-999\nkind: lema\nstatus: pendiente\ndepends_on: []\n---\n\n## Enunciado\n\nlema de prueba\n`.
   → `_ledger.md` now shows `Ninguno.` under `## Problemas` and a Claims row
   for C-9001. This proves `Write(Projects/*/Claims/*)`.
4. With the **Edit** tool, change C-9001's `status: pendiente` to
   `status: refutado` (a hand edit is acceptable **only** in this throwaway
   test; real claims go through `claim_status.py`). → `_ledger.md` shows an
   **importante** flag on H-9001 (`depende de C-9001 (\`refutado\`)`), and
   H-9001's own file is unchanged. This proves `Edit(Projects/*/Claims/*)`.
5. With the **Edit** tool, change H-9001's `depends_on: [C-9001]` to
   `depends_on: []`. → the flag disappears. This proves
   `Edit(Projects/*/Hipotesis/*)`.
6. Negative check: Edit any `Papers/*.md` note trivially and revert it →
   `_ledgertest/_ledger.md` mtime does not change.
7. Clean up: delete `Projects/_ledgertest/` entirely; `git -C Kairo/vault status
   --short` must match step 1. (The test ids H-9001 / C-9001 / PROJ-999 never
   reach git.)
8. Repeat steps 2–7 once from a session rooted at `Kairo/` to check the mirror
   (paths then carry the `vault/` prefix).

Also run once, outside a session, on the real vault (read-only, no `--write`):

```
python C:/Users/gomez/kairo-plugin/scripts/ledger/build_graph.py --vault C:/Users/gomez/Kairo/vault
```

Observed from Block B on 2026-09-24 (worktree copy of the script, read-only):
`kairo/build_graph@1.0.0: 6 nodos, 0 crítico, 0 importante` (exit 0) — the six
existing H-0001…H-0006 notes parse; none has `depends_on` yet.
