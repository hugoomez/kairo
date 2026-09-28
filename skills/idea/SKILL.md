---
name: idea
description: Use when the researcher wants to jot down an idea for later in a Kairo vault — "/kairo:idea …", "idea: …", "apunta esta idea", "guárdame esto para luego". Captures it verbatim in Ideas/I-XXXX.md in seconds, commits it alone, and does nothing else. Not for vetting a claim now (that is hypothesis-cycle).
---

# idea — capture now, decide later

Capture the idea exactly as the researcher said it and get out of the way.

1. Run, from the vault root:

   ```
   python "${CLAUDE_PLUGIN_ROOT}/scripts/ideas/idea.py" add --vault . \
     --source claude-code --commit [--project PROJ-XXX] [--context "<one line>"] <the idea text>
   ```

   - The text is the researcher's words, verbatim. Do not rephrase, expand,
     correct or "improve" it.
   - `--project` only if they named one. `--context` only if they gave one
     (e.g. where it came from); never invent context.
2. Reply with one line: the id (`I-XXXX`) and "guardada".

Do **not** evaluate the idea, search literature, match it to projects, or
start a hypothesis cycle. Matching happens later in the Kairo interface, and
turning an idea into a hypothesis always goes through `hypothesis-cycle` when
the researcher asks for it.

From a plain terminal the same capture is `kairo-idea <text>`
(`scripts/ideas/kairo-idea` / `kairo-idea.cmd`, with `KAIRO_VAULT` set).
