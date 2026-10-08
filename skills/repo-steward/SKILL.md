---
name: repo-steward
description: Use when an applied Kairo project (template aplicado, `code_repo:` in its hub) needs its code repository looked after — "revisa la salud del repo", "¿qué código sirve a H-0004?", "revisa este PR / estos cambios", "¿debería esto ser un ADR?". Runs the repository's own tests, lint and type checks, reads CI, writes Projects/<slug>/_repo-health.md, refreshes the code↔science trace, reviews changes against the project's scientific goals, and proposes ADRs. Never pushes, never creates a remote, never copies vault content into the repository.
---

# repo-steward — the applied project's code repository

> **Profile.** Before anything else run `python "${CLAUDE_PLUGIN_ROOT}/scripts/models/kairo_profile.py" check repo-steward`; exit 3 means the `literatura` profile is active (`KAIRO_PROFILE`): give the researcher its one-line message and stop.

## Overview

An applied project's code lives in its own git repository (`code_repo:` in
the hub), outside the vault, guarded against vault content by a pre-push hook.
You act as its administrator: keep it healthy, keep it tied to the
scientific questions it serves, and make the decisions that shape it
explicit (ADRs). The standards are the repository's `CONVENTIONS.md`.

## Never

- `git push`, `gh repo create`, or anything that publishes. Pushing and
  remotes are the researcher's approved actions in the Kairo interface
  (the guard runs there).
- Copy paper text, hypotheses, notes, notebook entries or any vault content
  into the repository — not even into comments, docs or test fixtures.
  Reference ids (`H-0004`, `E-0012`) only.
- Weaken a check to make it pass (skip tests, loosen lint rules, lower the
  coverage floor). Report the failure; propose the fix.

## Tasks

### Health check

1. Read `CONVENTIONS.md`. Detect the toolchain from the repository
   (`pyproject.toml`, `package.json`, …).
2. Run, from the repository root, with a time limit, each check the
   conventions name: formatter check, linter, type checker, tests with
   coverage. Record each command, its exit code, and a one-line summary.
3. CI: `gh run list --limit 5` when the repository has a GitHub remote and
   `gh` is available; otherwise say CI was not checked and why.
4. Dependencies: list outdated or vulnerable packages if the toolchain has a
   command for it (`pip-audit`, `npm audit`), else say it was not checked.
5. Write `Projects/<slug>/_repo-health.md` (vault side, never in the repo):
   the date, the repository HEAD, a table *check · command · result · detail*,
   and the concrete next fixes, most important first. Commit it alone.

### Code ↔ science trace

Commits that serve a hypothesis, experiment, claim or decision say so in a
trailer: `Motivated-By: H-0004` (several ids allowed, comma-separated).
Refresh the trace:
```
python "${CLAUDE_PLUGIN_ROOT}/scripts/code_repo/trace_code.py" \
  --repo <code_repo> --project-dir Projects/<slug> --write
```
It writes `_codigo-ciencia.md`: per id, the commits and files that serve it,
and the commits that name no motivation. When you commit to the repository
yourself, add the trailer.

### Review changes

For a diff or a branch: correctness first (use the code-review skills
available), then fit with the project — does the change serve a hypothesis
or experiment it names? does it change behaviour an adjudicating experiment's
frozen manifest depends on (then a new `code_repo@commit` in a new
preregistration, never a silent change)? is there a test? Report findings
located by file and line.

Every finding carries exactly one severity:

- **crítico** — breaks correctness, a frozen experiment's behaviour, a
  guarantee the project relies on, or leaks vault content / a secret;
- **importante** — must be fixed soon (missing test for changed behaviour, a
  cited hypothesis the change does not actually serve, an undocumented
  structural choice that deserves an ADR);
- **menor** — worth doing, can wait.

Check the change against the hub's goals (`## Propósito` and the goals the hub
lists) and against each hypothesis its commits cite in `Motivated-By:`
trailers: read each cited hypothesis note (skip any `send: never` note — name
its id only) and say whether the change serves it. A change that cites no
hypothesis is reported as such (**menor**, or **importante** when it touches
experiment code).

**Draft-only mode (Kairo interface, «Revisar cambios»).** The prompt gives
the repository, the exact commit range, the commits with their trailers, the
cited hypotheses and a file with the diff (prepared by the backend outside
the vault). In this mode you only read — Read, Grep, Glob; no command, no
file written, nothing in the repository touched. Return the review as one
JSON object between a line `<<<REVISION` and a line `REVISION>>>`:

```json
{"resumen": "<two or three sentences>",
 "objetivos": "<how the change fits the hub's goals>",
 "hipotesis": [{"id": "H-XXXX", "sirve": true, "como": "<one sentence>"}],
 "hallazgos": [{"severidad": "crítico|importante|menor", "archivo": "<path or null>",
                "linea": <int or null>, "titulo": "<short>", "detalle": "<what and why>",
                "sugerencia": "<the fix>"}]}
```

The backend validates it (severity, ids, files in the diff), writes
`Projects/<slug>/Producto/revisiones/RV-<date>-<target>.md` (`citable:
false`, written by a model) and commits it. Never quote vault notes in the
review; reference ids only.

### Architecture decisions

When a change fixes a structural choice (a data format, a framework, an
interface other code will depend on), propose an ADR in
`Projects/<slug>/Producto/ADR-XXX.md` from the ADR template, citing the
hypotheses it rests on, and check it with `kairo:adr-check`. The researcher
accepts or rejects it.

## Experiments and the repository

An experiment that runs this repository's code freezes it as
`code_repo@commit` in its environment manifest: preregister-experiment records
the repository path and the exact commit; run-experiment's pre-flight checks
the repository is at that commit with no uncommitted changes, and a Kaggle
bundle takes its files from that commit (`git archive`), never from the
working tree.
