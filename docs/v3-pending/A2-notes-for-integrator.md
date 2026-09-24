# A2 — AI-use disclosure (`ai_disclosure.py`) — notes for the integrator

Files (Block A, A2):

- `skills/assemble-manuscript/scripts/ai_disclosure.py` — the generator (stdlib only, `--version` → `kairo/ai_disclosure.py@1.0.0`).
- `skills/assemble-manuscript/scripts/test_ai_disclosure.py` — 23 unittest tests, synthetic temp vault only.
  Run: `cd skills/assemble-manuscript/scripts && python -m unittest test_ai_disclosure -v`.
- This file. `skills/assemble-manuscript/SKILL.md` is **not** edited by A2; the exact text to add is in §3.

Smoke-tested read-only against the real vault (PROJ-001, `--hypotheses H-0006,H-0001`): all 34 vault notes
parse, exit 0, output only on stdout. Nothing was written to the vault.

## 1. Venue requirements matrix (retrieved 2026-09-24)

| Requirement | ICLR (2027 AI Policy for Authors; 2026 LLM policy) | ICML 2026 | NeurIPS 2026 | Science journals | Nature Portfolio |
|---|---|---|---|---|---|
| Disclosure mandatory? | **Yes, any use**; "significant" use in ideation/writing; 2027: mandatory section | Encouraged only ("notable ways … in methodology") | Only if LLM/agent is an important, original or non-standard part of the **method** | Yes, any use in research or writing | Yes, any substantive use; AI copy-editing exempt |
| Where | **Dedicated mandatory section, outside the page limit** (2027); paper text + submission form (2026) | — | Experimental-setup section (or equivalent) | **Cover letter + Methods or Acknowledgments** (depends on use) | **Methods** (or equivalent section) |
| What | **Per task: tasks used, tasks not used, tasks N/A** (required list: propose/refine hypotheses, design methodology/experiments, implement methods, interpret results, data cleaning, …; recommended list: literature search/summary, gap finding, code editing, drafting, figures, …); **how AI output was reviewed** ("Elaborate") | — | The method-level use | **Tool name + version, and the full prompt**, in Methods | Documented use |
| Human responsibility | Authors responsible; must verify LLM contributions; boilerplate "We take responsibility for the final content…" | Full responsibility, incl. AI content (plagiarism, "AI slop") | Fully responsible; verify correctness incl. citations | Authors accountable | Authors accountable |
| AI as author | Not addressed explicitly (responsibility on authors) | **Not eligible** | **Cannot be authors** | **Not allowed**; cited sources may not be AI-authored | **Not allowed** |
| AI-generated images | Recommended disclosure (figures) | — | — | **Not permitted** without explicit editor permission | **Not permitted** (limited exceptions); non-generative ML image manipulation must be disclosed |
| Sanction | Undisclosed extensive use → desk reject | Prompt injection → desk reject | Prompt injection prohibited | Violation = scientific misconduct | — |

Sources (all retrieved 2026-09-24):

- ICLR 2027 AI Policy for Authors — https://iclr.cc/Conferences/2027/AIPolicyForAuthors (mandatory section, required/recommended task lists, boilerplate text)
- ICLR 2026 LLM policy — https://iclr.cc/FAQ/LLM and https://blog.iclr.cc/2025/08/26/policies-on-large-language-model-usage-at-iclr-2026/
- ICML 2026 Call for Papers — https://icml.cc/Conferences/2026/CallForPapers (author LLM use); reviewing policy https://icml.cc/Conferences/2026/LLM-Policy
- NeurIPS 2026 Main Track Handbook — https://neurips.cc/Conferences/2026/MainTrackHandbook (NeurIPS 2025 LLM policy for reference: https://neurips.cc/Conferences/2025/LLM)
- Science journals editorial policies — https://www.science.org/content/page/science-journals-editorial-policies ; https://www.science.org/content/blog-post/change-policy-use-generative-ai-and-large-language-models
- Nature Portfolio AI policy — https://www.nature.com/nature-portfolio/editorial-policies/ai

Retrieval caveat: science.org returned HTTP 403 and nature.com a cookie wall to the fetcher. For those two
rows the content comes from search-engine extracts of those official pages (quoted text: "note this in
the cover letter and in the methods section or acknowledgments section"; "the full prompt … as well as the
AI tool and its version, should be disclosed in the methods section"; Nature: "documented in the Methods
section", copy-editing exemption, no generative AI images). **Re-check both pages in a browser before a
submission.**

**Strictest, and what `ai_disclosure.py` satisfies:** no single venue is strictest on every axis, so the
script targets the union:

- *Content:* **ICLR 2027** is strictest — per task, what AI did / did not do / N/A, and how AI output was
  reviewed. → per-stage structure (9 stages); a stage with no record says so explicitly; the verification
  stage lists every `verifications:` entry.
- *Model identity:* **Science** — tool + version + full prompt. → models and versioned components
  (skills, verifier ids, analysis scripts, `combine_effects.py`) listed verbatim as recorded. **Kairo does
  not record prompts** → always an `importante` flag. Mitigation: state that the instructions are the Kairo
  skills at the recorded `skill_version` and attach them as supplementary material.
- *Placement:* **ICLR 2027** dedicated section + **Nature/Science** Methods + **Science** cover letter and
  Acknowledgments. → a dedicated `## Declaración de uso de IA` section, plus one-line pointers in Método
  and Agradecimientos, plus the cover letter (§3).
- *Authorship / responsibility / images (Science, Nature):* the script never asserts these by itself.
  It emits them as `[PENDIENTE — …]` lines plus flags, until the researcher sets
  `ai_disclosure_confirmed_by` in the manuscript frontmatter.

**Language decision:** Spanish (vault language, matches the draft), **plus an English version by default**
(`--lang both`). All five venues publish in English. The English text is generated from the same records by
the same code, so the version that gets submitted does not depend on an LLM translating the Spanish
text afterwards, which could drift from what the records show. `--lang es|en` gives one language.

## 2. What the script does (for review)

`python ai_disclosure.py --vault <vault> --project <slug|PROJ-XXX> --thread <paper_thread> [--hypotheses H-…] [--manuscript <path>] [--format markdown|json] [--lang es|en|both]`

Exit codes: `0` ok (flags don't change the exit code), `1` error, `2` invalid input (vault, project, thread,
a `--hypotheses` id, or the manuscript path not found).

- Collects the thread's hypotheses (or the `--hypotheses` list), their adjudicating experiments
  (`linked_experiment` + experiments whose `hypothesis:` is one of them), the `environment.tools` TOOL.md
  files, `_hub.md`, `Estado-del-arte.md`, cited `Papers/` (hypotheses' `linked_papers` + P-ids found in the
  manuscript) and the manuscript. Reads `verifications:` from all of them except `_hub.md`.
- Output: `## Declaración de uso de IA`. Intro, 9 stages (literatura, síntesis, hipótesis, preregistro,
  código, ejecución y análisis, verificación, redacción, responsabilidad humana), the English version, then
  two internal subsections to remove before submission: **Trazabilidad** (statement # → note · field) and
  **Avisos de cumplimiento** (flags). `--format json` returns the records, statements (with `n` and
  `sources`), models, versioned components, verifications, `never_verified` (`absent` vs `[]`) and flags.
- Models: the intro lists "Modelos que constan en los registros: <model> (<uses>; fuente: <note field>…)"
  and names the stages with no recorded model. Sources are `generated_by.model`, `verifications[].model`,
  `code_generated_by.model`, and model ids found in history `by:` strings (claude-/gpt-/gemini-/llama/qwen/
  deepseek/mistral, kept verbatim). The JSON gets `models[].stages` and `stages_without_model`.
- Verification wording, following §1b: "<verifier> (modelo <m>), <date>: no encontró errores en / encontró
  errores en / no pudo evaluar <la nota completa X | la sección «H» de X>", plus a fixed line saying a
  "no errors found" result is not a certification of correctness. It never writes "correcto" or
  "verificado". A verdict outside the enum is quoted verbatim, flagged `importante`, and not interpreted.
- Human involvement comes only from these records: `generated_by.origin: human`; a history `by:` that
  isn't agent-like (a heuristic: model names, `->`, `@`, skill names count as agent-like; an empty `by:`
  counts as unknown, never as human); explicit approval or override sentences in `## Enmiendas` or
  `## Revisión del ciclo`; and the manuscript's `ai_disclosure_confirmed_by`. `autonomy_defaults` is
  reported as "configuración, no un registro de cada decisión". The execution stage says the verdict came
  from the frozen analysis script (`analysis_plan` → `two_proportion_test.py` / `bayes_factor_proportions.py`),
  not from an AI judgement. It says this only when `status: completed` and `analysis_plan` is recorded.
- **`send: never` (A3 requirement):** a note whose top-level frontmatter has `send: never` (case-insensitive,
  quoted or not, trailing comment allowed; the check runs on the raw line and on the parsed value) contributes
  **only its id** and the flag `importante — <id> marcado send: never; su contribución no puede declararse
  automáticamente, el investigador debe completarla a mano`. None of its claim, history, model or
  verifications is output, and its `linked_experiment` / `linked_papers` are not followed. It is still read
  locally to decide thread membership; that content never reaches stdout. Test: `test_send_never`,
  `test_send_never_detection`. Same regex semantics as `scripts/security/send_guard.py` (A3). The
  script does not import it, so it stays standalone.

Flag severities chosen:

| Flag | Severity | Why |
|---|---|---|
| latest `verifications` entry for a (note, scope) is `errors_found` | **crítico** | the manuscript must not present it as verified |
| `errors_found` later followed only by `cannot_assess` for the same scope | importante | errors not shown as resolved |
| hypothesis without `generated_by`; `origin: agent` with missing/placeholder model; verification without model | importante | ICLR 2027 requires disclosing hypothesis generation; Science requires tool + version |
| **zero** verification records across all notes | **importante** (not menor) | ICLR 2027 boilerplate requires elaborating how AI-assisted work was reviewed; with no records the section cannot say |
| experiment code authorship not recorded; prereg authorship not recorded (no `generated_by`, no `preregistrada` history entry) | importante | ICLR 2027 required tasks: "implement methods", "design … experiments" |
| `method_provenance: reimplemented_from_text`; tool not `validated`; tool TOOL.md missing; linked experiment note missing | importante | mirrors existing Kairo flags |
| SOTA without `generated_by` (search/synthesis model unknown); manuscript without `generated_by` / no `--manuscript` | importante | model identity (Science) |
| prompts not recorded | importante (always) | Science: full prompt |
| responsibility / no-AI-author statement not confirmed (`ai_disclosure_confirmed_by` absent) | importante | all venues |
| `send: never` note | importante | A3 |
| unparseable frontmatter | importante | records missing from the statement |
| AI-generated images: not recorded, researcher must confirm | menor (always) | Nature/Science ban them |
| `--hypotheses` id off-thread; cited paper note missing; malformed verification entry; `section:` heading no longer in the note | menor | |

## 3. Proposed text for `skills/assemble-manuscript/SKILL.md`

### 3a. Insert a new step between `## Step 3 — Draft` (end of its `### Citations …` subsection) and `## Output`

````markdown
## Step 4 — Declaración de uso de IA (generated from records, never written by hand)

Every manuscript this skill drafts carries an AI-use disclosure, built mechanically from what Kairo
recorded. The disclosure is required by all target venues. The strictest combination: ICLR 2027 wants a
dedicated section listing, task by task, what AI did, what it didn't, and how its output was reviewed.
Science wants the tool, its version and the prompt. Nature and Science want Methods placement and ban
AI-generated images. Do not write or paraphrase the disclosure yourself.

1. Write the manuscript note first (Output below), including `generated_by`. Then run:

   ```
   python "${CLAUDE_PLUGIN_ROOT}/skills/assemble-manuscript/scripts/ai_disclosure.py" \
     --vault <vault> --project <slug> --thread <paper_thread> \
     --hypotheses <qualifying ids from Step 2, comma-separated> \
     --manuscript Projects/<slug>/Manuscritos/manuscript-<paper_thread>.md
   ```

   Exit `2` = wrong project/thread/id/path (fix the input). Exit `1` = the script failed: say so and do not
   write a disclosure by hand. Pass `--format json` to read the flags programmatically.
2. Insert its stdout **verbatim** into the manuscript as the section `## Declaración de uso de IA`, placed
   after `## Discusión` and before the references list. Keep its two internal subsections
   (`### Trazabilidad …` and `### Avisos de cumplimiento …`). They are marked "eliminar antes de enviar"
   and are the researcher's checklist.
3. Add one pointer sentence at the end of `## Método`: "El uso de herramientas de IA en cada etapa de este
   trabajo se detalla en la sección *Declaración de uso de IA*." Add the same sentence in an
   `## Agradecimientos` section (create it if absent). Science asks for the disclosure in Methods or
   Acknowledgments **and** in the cover letter, so tell the researcher to copy the English version into
   the cover letter.
4. Show the researcher the flags, most severe first:
   - **`crítico`** (a verification whose latest verdict for that scope is `errors_found`): in its own
     callout at the top. The manuscript must not describe that note or section as verified, and the
     affected content should be fixed and re-verified before submission.
   - `importante` / `menor`: list them. Each is a gap that would fail the strictest venue: an unrecorded
     model, unrecorded code authorship, `send: never` notes the researcher has to declare by hand,
     unrecorded prompts, and the unconfirmed responsibility statement.
5. Lines marked `[PENDIENTE — …]` are for the researcher to confirm or correct: human responsibility,
   no AI authorship, no AI-generated figures. Never fill them in yourself, and never set
   `ai_disclosure_confirmed_by`. Only the researcher does that.
````

### 3b. Output frontmatter additions (append to the YAML block in `## Output`)

```yaml
generated_by:
  origin: agent
  model: <model id of the session drafting the manuscript, e.g. claude-opus-5-5>
  skill_version: kairo/assemble-manuscript@<plugin version>
ai_disclosure:
  script: kairo/ai_disclosure.py@<version printed by --version>
  generated: <YYYY-MM-DD>
  flags: {critico: <n>, importante: <n>, menor: <n>}
# set ONLY by the researcher, by hand, after reading the disclosure — never by this skill:
# ai_disclosure_confirmed_by: <name>
# ai_disclosure_confirmed: <YYYY-MM-DD>
```

(`ai_disclosure.py` reads `generated_by`, `generated`, `status`, `ai_disclosure_confirmed_by` and
`ai_disclosure_confirmed` from the manuscript note. When a re-run regenerates the section,
`ai_disclosure_confirmed_by` must be re-confirmed. Say so in the re-run report.)

### 3c. Add to `## Common mistakes`

```markdown
- **Writing or "improving" the AI-use disclosure by hand.** It is generated from the records by
  `ai_disclosure.py`, and every sentence traces to a note field. Paraphrasing it can invent a contribution
  or drop a gap. Re-run the script instead.
- **Presenting a `no_errors_found` verification as "verified correct".** It only means a verifier found no
  errors in that scope. A scope whose latest verdict is `errors_found` (a `crítico` flag) must not be
  called verified anywhere in the manuscript.
- **Claiming human involvement the records don't show.** No record means the disclosure says "no consta".
  Filling in `[PENDIENTE]` lines or `ai_disclosure_confirmed_by` is the researcher's job, never this skill's.
- **Quoting a `send: never` note's content into the disclosure.** The script outputs only its id and a
  flag. The researcher declares that contribution by hand.
```

Verify after applying: `grep -n "ai_disclosure.py" skills/assemble-manuscript/SKILL.md` → Step 4 hit;
`grep -n "Declaración de uso de IA" skills/assemble-manuscript/SKILL.md` → ≥ 2 hits; run the test suite.

## 4. Contract issues (docs/v3-interfaces.md §1b / §1d — not edited)

1. **Ordering of entries on the same date.** §1b gives no time or sequence field. A2 orders by
   `(date, list index)`, which relies on append-only. If B2 might write two entries on the same day, a
   `datetime` or sequence field would remove the ambiguity.
2. **What "the same scope" means for supersession.** A2 reads scopes literally. A later `scope: note`
   `no_errors_found` does **not** clear an earlier `section:X` `errors_found`, and a later `section:X` does
   not clear an earlier `note`. The contract should say whether a note-level pass covers its sections.
3. **Scope drift.** `section:<exact heading>` breaks when the heading is later renamed. A2 flags `menor`
   when the heading no longer exists in the note. The contract does not say what B2 or readers should do.
4. **Out-of-enum verdicts.** The contract forbids them but gives readers no behaviour. A2 reports the value
   verbatim, flags `importante`, and does not interpret it.
5. **`send: never` × verifications.** A fresh verifier must not read a `send: never` note, since the content
   would reach a model. §1b and B2 should state that such notes are never verified. A2 ignores any
   `verifications:` on them.
6. **Authorship Kairo does not record** (not a §1b issue, but it caps compliance). The strictest venue
   needs these, and today no one writes them:
   - `generated_by` on `Estado-del-arte.md` (`create-project`, owner A).
   - `generated_by` on experiment notes (`preregister-experiment`, owner B).
   - Who wrote the experiment code: a `code_generated_by` field, or a structured line in the code-commit
     `## Enmiendas` entry (`run-experiment`, owner A; a new frontmatter field needs an `A-*` pending file
     per §2).
   - Prompts: currently covered only by `skill_version`.

   `ai_disclosure.py` already reads `generated_by` on the SOTA, experiments and manuscript, and
   `code_generated_by` on experiments, if they appear. Until someone writes them, those stages say
   "no consta" and are flagged `importante`.
7. **`history.by` is free text.** Human vs agent is a heuristic (model names, `->`, `@` and skill names read
   as agent-like; empty reads as unknown). A structured `by_kind: human | agent`, or a `human:` prefix,
   would make "never claim human involvement you can't see" exact rather than heuristic. Today a human
   who signs with a model-like string would be read as an agent, which is the safe direction.
8. §1d is respected: absent `role` renders as "confirmatory (campo ausente; se lee como confirmatorio)",
   absent `rung` as "desconocido (campo ausente)". No failure, no flag.
9. The disclosure's statements cite Kairo ids (H-/E-/P-) so that they trace back to the notes. The
   researcher may want to replace them with paper-facing names in the English version before submission.
   The traceability table keeps the mapping.
