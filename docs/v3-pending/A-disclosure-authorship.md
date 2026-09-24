# A-disclosure-authorship: record who wrote the preregistration and the experiment code (A2)

`assemble-manuscript`'s AI-use disclosure (`ai_disclosure.py`) can only state
what Kairo records. Two stages have no record today, so the disclosure says
"no consta" for them and flags each one `importante`:

- who drafted the preregistration;
- who wrote the experiment code.

The strictest venue (ICLR 2027) wants a per-task account, so these gaps are
worth closing. The script already reads the fields below whenever they are
present; no script change is needed. `Estado-del-arte.md` authorship is
already fixed inside Block A (`create-project` step 7 now writes
`generated_by`).

## 1. Target `skills/preregister-experiment/SKILL.md` + `templates/experiment-template.md` (owner B)

**Change:** at freeze time, write into the experiment note's frontmatter the
same shape hypotheses use:

```yaml
generated_by:
  origin: agent | human      # who drafted the design text
  model: <model id>           # omit if origin is human or the id is unknown
  skill_version: preregister-experiment@<plugin version>
```

It is frozen with the rest of the prereg. Being a new frontmatter field, it
goes in the template (B).

**Verify:** freeze a test prereg, then run
`python skills/assemble-manuscript/scripts/ai_disclosure.py … --format json`.
The preregistro stage lists the model with source `E-XXXX generated_by`.

## 2. Target `skills/run-experiment/SKILL.md` step 0 (owner A, but frozen for v3 except bundle wiring, so deferred to integration)

**Change:** in step 0 "Record the code commit", replace the entry template
`implementación de E-XXXX, code sha <sha>` with:

```markdown
`implementación de E-XXXX, code sha <sha>, escrito por <agent <model id> | researcher | mixto>`
```

and add, in the same step:

```markdown
Also set `code_generated_by: {origin: agent | human | mixed, model: <id, omit if unknown>}`
in the experiment note's frontmatter. It is not part of the frozen
preregistration: it describes the implementation, which happens after the
freeze. The AI-use disclosure reads it.
```

`code_generated_by` is a new frontmatter field on an experiment note, so the
template change is B's (`templates/experiment-template.md`). Integration
applies both halves together.

**Verify:** `grep -n "code_generated_by" skills/run-experiment/SKILL.md templates/experiment-template.md`
→ one hit each. `ai_disclosure.py` stage 5 then names the author instead of
"no consta".
