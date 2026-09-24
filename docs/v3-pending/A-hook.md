# A-hook — `send: never` PreToolUse guard (A3)

Deferred because both `settings.json` files and `vault/Scripts/hooks/*` belong to
INTEGRATION (`docs/v3-interfaces.md` §2). Nothing here has been applied or
tested against the real vault. The guard script itself is unit-tested in the
plugin (`scripts/security/test_send_guard.py`, 14 tests).

## 1. Target

1. `Kairo/vault/Scripts/hooks/send_guard.py` (new): a byte-identical copy of
   plugin `scripts/security/send_guard.py`. It sits beside the vault's other
   hook scripts (`check_sota_staleness.py`, `regen_digest.py`), because a
   project `settings.json` has no `${CLAUDE_PLUGIN_ROOT}`. The plugin copy is
   canonical. Re-copy it when it changes. The skills also call the plugin copy
   in `check` mode.
2. `Kairo/vault/.claude/settings.json`: add a `PreToolUse` key.
3. `Kairo/.claude/settings.json` (outer, mirrored): the same, with the
   `vault/` prefix.

## 2. Change

### Design constraints (from this vault's history, all mandatory)

- **`type: "command"` only.** `mcp_tool` hooks never worked in this vault
  (vault commit `917bf3f` converted the reindex hook from `mcp_tool` to
  `command`).
- **One tool per rule, no `**`.** Vault commit `7d8ec95` fixed broken
  `if`-conditions that used `Tool1|Tool2(...)` and `**`. The snippet below uses
  **one matcher group per tool with a single exact tool name, and no `if` at
  all**. A path glob can't express "this file's frontmatter says `send: never`",
  and any note in any folder may carry the flag, so the script decides by
  reading the target's frontmatter (first 16 KB only). Leaving out `if` also
  avoids the `*` vs `**` semantics entirely.
- **PreToolUse format and blocking**, checked against
  `code.claude.com/docs/en/hooks` on 2026-09-24:
  - stdin JSON has `tool_name`, `tool_input`, `cwd`, `hook_event_name`. When
    the call comes from a subagent it also has `agent_id` / `agent_type`.
  - `tool_input` for `Read` is `{file_path}`. For `Grep` it is
    `{pattern, path, glob, output_mode, …}`; the script also accepts `paths`.
    For Bash/PowerShell it is `{command}`. For `get_note` it is `{notePath}`.
  - **Exit 2 blocks the call and shows stderr to Claude as the reason.** It
    takes precedence over any JSON. The guard uses exit 2 with a stderr reason
    that names the file and never includes its content.
  - Hooks from settings files **also fire for subagent tool calls**, so
    `facet-summarizer` and the others are covered.
  - `$CLAUDE_PROJECT_DIR` is the project root.
- **Timeout 30 s.** On a hook timeout the call goes through (fail-open).
  Bash/PowerShell checks walk the vault's `.md` files, reading at most 16 KB
  each; that took 0.3 s on this vault (2026-09-24). Raise the timeout if the
  vault grows large.
- **Fail-open** on the guard's own errors (unparseable stdin, internal
  exception): exit 0 with a message on stderr, which only reaches the debug
  log. A crashing guard must not lock the researcher out of every `Read`. The
  skill-level "skip" instructions remain the first line of defense.

### Snippet — `Kairo/vault/.claude/settings.json`

Insert inside `"hooks"`, **before** the existing `"PostToolUse"` key. The
fragment ends in `],` so that `"PostToolUse"` follows it; pasted after
`PostToolUse`, the last key, the file would be invalid JSON:

```json
    "PreToolUse": [
      {
        "matcher": "Read",
        "hooks": [
          {
            "type": "command",
            "command": "python \"$CLAUDE_PROJECT_DIR/Scripts/hooks/send_guard.py\" hook --vault \"$CLAUDE_PROJECT_DIR\"",
            "timeout": 30,
            "statusMessage": "send_guard: checking send: never"
          }
        ]
      },
      {
        "matcher": "Grep",
        "hooks": [
          {
            "type": "command",
            "command": "python \"$CLAUDE_PROJECT_DIR/Scripts/hooks/send_guard.py\" hook --vault \"$CLAUDE_PROJECT_DIR\"",
            "timeout": 30,
            "statusMessage": "send_guard: checking send: never"
          }
        ]
      },
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python \"$CLAUDE_PROJECT_DIR/Scripts/hooks/send_guard.py\" hook --vault \"$CLAUDE_PROJECT_DIR\"",
            "timeout": 30,
            "statusMessage": "send_guard: checking send: never"
          }
        ]
      },
      {
        "matcher": "PowerShell",
        "hooks": [
          {
            "type": "command",
            "command": "python \"$CLAUDE_PROJECT_DIR/Scripts/hooks/send_guard.py\" hook --vault \"$CLAUDE_PROJECT_DIR\"",
            "timeout": 30,
            "statusMessage": "send_guard: checking send: never"
          }
        ]
      },
      {
        "matcher": "mcp__smart-connections__get_note",
        "hooks": [
          {
            "type": "command",
            "command": "python \"$CLAUDE_PROJECT_DIR/Scripts/hooks/send_guard.py\" hook --vault \"$CLAUDE_PROJECT_DIR\"",
            "timeout": 30,
            "statusMessage": "send_guard: checking send: never"
          }
        ]
      }
    ],
```

### Snippet — outer `Kairo/.claude/settings.json`

The same five groups, with every command replaced by:

```json
"command": "python \"$CLAUDE_PROJECT_DIR/vault/Scripts/hooks/send_guard.py\" hook --vault \"$CLAUDE_PROJECT_DIR/vault\""
```

`--vault` matters for `get_note`: its `notePath` is relative to the vault, not
to `Kairo/`.

### What the guard blocks and what it doesn't

| Tool | Blocked when | Passes |
|---|---|---|
| `Read` | `file_path` is a note with `send: never` | everything else |
| `Grep` | `output_mode: content` and a flagged note lies inside `path`/`paths` (+`glob`) | `files_with_matches` (the default) and `count`, which return names only |
| `Bash`, `PowerShell` | the command uses a content-reading verb (`cat`, `grep`, `Get-Content`, `python`, `cp`, …) **and** names a flagged note by path, file name or stem | `ls`, anything that names no flagged note |
| `mcp__smart-connections__get_note` | `notePath` is flagged | — |
| `Glob`, `Write`, `Edit`, Smart Connections `search_*` | never | these return names, titles and heading names only. `Edit` needs a prior `Read`, and that `Read` is blocked |

**Known limit (`importante`).** A shell command can reach a flagged note
without naming it (`grep -r foo .`, `cat Papers/*`). The Bash check is best
effort. The primary control is the explicit skip in every skill and agent that
reads the vault (A3, plus `docs/v3-pending/A-send-never-readers.md` for skills
outside Block A). A flagged note's **file name and title** can still surface
through `Glob` or Smart Connections search. If a title itself is sensitive,
give the file a neutral name.

## 3. Verify — real-test procedure (run at integration, not before)

Run all of this from a normal interactive session, **not** with
`--dangerously-skip-permissions`.

1. **Unit tests:** `python -m unittest discover -s <plugin>/scripts/security -p "test_send_guard.py"` → OK.
2. **Copy + byte check:** copy the script into `vault/Scripts/hooks/`, then
   `sha256sum` both copies. They must be identical.
3. **Pipe test (no Claude):** create `vault/Papers/P-9999 send-never-test.md`:
   ```markdown
   ---
   id: P-9999
   title: send-never canary
   send: never
   ---
   CANARY-7f3a-NO-ENVIAR
   ```
   From the vault root:
   `echo '{"tool_name":"Read","tool_input":{"file_path":"Papers/P-9999 send-never-test.md"},"cwd":"."}' | python Scripts/hooks/send_guard.py hook --vault .; echo "exit=$?"`
   → `exit=2`, and stderr names the file without `CANARY`. The same with
   `Papers/<any existing normal P-XXXX note>.md` → `exit=0`, no output.
4. **Apply both snippets.** Validate the JSON:
   `python -c "import json;json.load(open('.claude/settings.json'))"`.
5. **Session from `vault/`** (`cd Kairo/vault && claude`). Run `/hooks` and
   confirm the five `PreToolUse` entries are listed. Then ask, one per turn:
   - "Read `Papers/P-9999 send-never-test.md`" → refused with the send_guard
     reason. `CANARY-7f3a` must not appear anywhere in the transcript.
   - "Read `Papers/<any existing normal P-XXXX note>.md`" → **reads normally**.
     This is the regression check that the hook doesn't block ordinary notes.
   - "Grep `CANARY` in `Papers/` with content output" → refused. The same with
     files_with_matches → allowed, and it lists the file name only.
   - "Run `cat 'Papers/P-9999 send-never-test.md'`" → refused.
   - "Use smart-connections get_note on `Papers/P-9999 send-never-test.md`" →
     refused. `get_note` on P-0001 → works.
   - Subagent: "Dispatch a facet-summarizer on [P-9999, P-0001]" → P-9999 is
     listed under `omitidas por send: never`, P-0001 is summarized, and no
     canary appears.
6. **Session from outer `Kairo/`**: repeat the P-9999 and P-0001 `Read` pair
   plus the `get_note` pair.
7. **Clean up:** delete `P-9999 send-never-test.md`. The PostToolUse reindex
   hook may have indexed it, so run the MCP `reindex` tool and confirm it's
   gone from `list_indexed`. `git -C vault status` must show only the intended
   settings and hook-script changes.

If step 5's P-0001 read is refused, or any canary leaks, revert both snippets.
Then report which tool call leaked.
