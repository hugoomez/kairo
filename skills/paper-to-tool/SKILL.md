---
name: paper-to-tool
description: >-
  Use only when a researcher explicitly asks to extract ONE specific method or
  analysis from a paper's own public code (the paper note has `code_repo:`
  set) and validate it against that code's reference outputs — typically
  because an experiment must reproduce that paper's method and reimplementing
  it from the paper's text is the risk. Clones the repo at a pinned commit
  outside the vault, builds an isolated environment, captures ground truth
  from the repo's own reference material, extracts a standalone parameterized
  function with a source map back to the original lines, and validates it
  mechanically against a tolerance frozen in advance (at most 6 fix
  attempts). Result: a `validated` (frozen) or `rejected` tool in the vault's
  shared `Tools/P-XXXX/<method>/`. Never runs at ingestion or in the
  background, and never executes anything before the researcher approves
  the exact commands.
---

# Paper to Tool

## Overview

When an experiment has to reproduce a specific paper's method, Kairo's
default is for Claude to reimplement it from the paper's text. That is where
E-0001 died: the reimplemented grokking transformer had LayerNorm and a tied
unembedding that the paper's own code never had, and the control could not
reproduce the paper's result. This skill replaces "reimplement from the text"
with "extract from the paper's own code, then prove the extraction computes
what that code computes."

It is deliberately **narrow**. It is not Paper2Agent's "agentify every paper":
one paper, one requested method, one tool, paid for once and shared by every
project in the vault. The pipeline is adapted from Paper2Agent (Miao et al.,
*Nature* 2026, `github.com/jmiao24/Paper2Agent`): its tutorial-scanner →
executor → extractor → test-verifier-improver split, its "bind every tool to
concrete source code, never invent the scientific computation" rule, its
6-attempt verify/fix loop, and its "a tool that keeps failing is excluded,
never silently used."

Binding rules:

- **On demand only.** Invoked by the researcher, or offered (never run) by
  `preregister-experiment`. Never at ingestion, never on a schedule, never as
  a side effect of another skill.
- **Nothing executes before approval.** Third-party code is shown — repo,
  pinned commit, sandbox location, the exact commands — and the researcher
  approves it explicitly first. See "Security" below. This is a `crítico`
  gate: it is never folded into a list of minor points.
- **Extract, don't reinvent.** The tool's computation is the repo's code,
  copied with its source lines cited. Only I/O and hard-coded constants
  change. Upstream quirks are preserved and documented, never "fixed".
- **Mechanical validation against a frozen tolerance.** Tolerances are
  written into `TOOL.md` before the first comparison, and
  `compare_outputs.py` decides pass/fail. Nobody loosens a tolerance, edits a
  reference output, or drops a compared field to get a pass.
- **Kairo contracts are untouched.** This skill writes only under
  `Tools/`. It never edits hypothesis status (`update-confidence` is the only
  writer), never edits a frozen preregistration (changes go only in that
  note's `## Enmiendas`), and flags everything with `crítico` /
  `importante` / `menor`.

## When to use

- A researcher asks: "extract <method> from P-XXXX's code", "build a tool
  for P-XXXX's <analysis>", "validate P-XXXX's training setup from its repo".
- `preregister-experiment` offered it (the design reproduces a paper's method,
  the paper has `code_repo:`, no tool exists yet) **and the researcher said
  yes**.

**When not to use:**

- At ingestion, or for "the whole paper". Ask which method; one per run.
- The paper note has no `code_repo:` — find one first (`create-project` step
  6.8 or `scripts/code_repo/find_code_repo.py`). Never guess a repo.
- A `validated` tool for the same paper + method already exists in `Tools/` —
  use it. A `rejected` one exists — read its reason first; retry only if
  something named in that reason has changed (new upstream commit, a
  reference output found, a GPU now available), and say which.
- To run an experiment. This skill validates a tool; it produces no evidence
  about any hypothesis.

## Inputs

1. **Paper id** `P-XXXX` whose note has `code_repo:` set.
2. **Precise method description** — the specific function, analysis, or
   training setup needed, in the terms the experiment needs it (e.g. "the
   modular-addition training setup: model, data split, optimizer, loss, as in
   §3"). Refuse "the paper's method" in general; ask.
3. Optionally, a pinned commit (default: the default branch's HEAD at run
   time, recorded as a full SHA).

## Layout

```
<sandbox>/P-XXXX/<method>/          outside the vault's git tree (default ~/.kairo-sandbox/)
  approved-commands.json             the exact argv list the researcher approved
  repo/                              clone, checked out at the pinned SHA
  .venv/                             isolated environment
  reference/  extracted/  logs/  runs.jsonl

<vault>/Tools/P-XXXX/<method>/       the shared library entry (see the vault's Tools/README.md)
  TOOL.md                            manifest (templates/tool-template.md)
  MANIFEST.sha256                    per-file hashes; its sha256 = validation_hash
  tool/<method>.py                   the extracted, parameterized function (+ CLI)
  env/requirements.lock.txt          `pip freeze` of the build environment
  reference/driver.py                how ground truth was produced from upstream code
  reference/inputs/  reference/outputs/   small JSON; large artifacts by hash + URL only
  validation/test_<method>.py        the comparison harness
  validation/attempt-NN.json         compare_outputs.py output per attempt
```

`<method>` is kebab-case (`modular-addition-training`). `<sandbox>` is
`$KAIRO_SANDBOX` if set, else `~/.kairo-sandbox`.

## Procedure

### 0. Pre-checks (read-only, no approval needed)

1. Read the paper note: `code_repo:`, `code_repo_evidence:`. If the evidence
   is weak (no author statement), say so — `importante`.
2. Check `Tools/P-XXXX/` for an existing tool for this method (see "When not
   to use").
3. Read the repository **without executing it**: GitHub API tree / raw files
   at the chosen commit, README, license, dependency files, tutorials,
   examples, tests. Identify:
   - the upstream code that implements the requested method (files + line
     ranges);
   - candidate **reference material** (step 3);
   - dependencies, and what will run on import (module-level code, network
     calls, `pickle`/`torch.load` of shipped files);
   - the **GPU requirement** of the reference computation, classified as
     Paper2Agent does: `none` / `optional` / `required` / `unknown`. A CUDA
     default in a config is not a requirement — check for a CPU path.
     `unknown` must be resolved before approval.
   - the **license**. No license → flag `importante`: the extracted code is
     stored in the local vault for research use only and must not be
     redistributed; say so in `TOOL.md`.

### 1. Approval gate — `crítico`

Present, **in its own callout at the top**, before anything is cloned or run:

```
⛔ crítico — ejecución de código de terceros (requiere aprobación explícita)
Repo:          <url>   (license: <…>)
Commit fijado: <full sha> — <date> "<message>"
Sandbox:       <path>  — fuera del árbol git del vault (sandbox_guard check: OK)
Aislamiento:   <container (docker, --network none, sin montar el vault)
                | proceso de usuario: NO es un sandbox de SO — puede leer el vault>
Red:           <what is downloaded: PyPI packages, reference data (URL, size)>
Se ejecutará:  <which third-party files run, and any pickle/torch.load of shipped files>
GPU:           <none | optional | required → Kaggle bundle (step 4b)>
Comandos (exactos, en orden):
  1. git clone --no-checkout <url> repo
  2. git -C repo checkout <sha>
  ...
Coste estimado: <wall-clock, CPU/GPU-h, and a rough Claude-session estimate>
```

Ask with `AskUserQuestion` (approve / reject / change something). Record the
approval as `<sandbox>/approved-commands.json` (argv lists exactly as shown,
`approved_at`, `approved_by`). From here on, **every** command runs through
`sandbox_guard.py run --plan …`, which refuses anything not in that file. A
command that turns out to be needed later (a missing package, a patched
path) goes back to the researcher as a new approval — never an ad-hoc run.

On Windows under Git Bash, export `MSYS_NO_PATHCONV=1` before calling
`sandbox_guard.py run`: MSYS otherwise rewrites POSIX-looking arguments
(`-w /work` → `C:/Program Files/Git/work`), the argv no longer matches the
approved plan, and the guard (correctly) refuses it.

Never run anything with the vault as working directory; never pass a
`Papers/` or `Projects/` path to third-party code (`sandbox_guard` refuses
both). If a container runtime is available, prefer it and say so; if not, say
plainly that the guard is not OS isolation.

### 2. Clone at a pinned commit; build the environment

```
python ${CLAUDE_PLUGIN_ROOT}/scripts/paper_to_tool/sandbox_guard.py check --sandbox <sb> --vault <vault>
git clone --no-checkout <url> repo && git -C repo checkout <sha>     # no submodules, no LFS unless approved
<python> -m venv .venv
.venv/<bin>/python -m pip install <pinned requirements>
.venv/<bin>/python -m pip freeze > env-requirements.lock.txt
```

(each through `sandbox_guard.py run`). Record the SHA (`git rev-parse HEAD`),
the interpreter version, and `sha256(env-requirements.lock.txt)`. When the
repo pins nothing, pick versions contemporary with the commit where that
matters and record the choice as `menor` (or `importante` if a numerical
library's behavior changed across versions in a way that touches the method).
Do not edit the scientific source to make an incompatible stack import.

### 3. Locate reference material — for the requested method only

In order of preference:

1. an official **test** or **example / tutorial / README snippet** that runs
   the method and produces known outputs;
2. **outputs shipped with the repo** (saved runs, checkpoints, result files)
   that the method's code produced;
3. **results reported in the paper itself** that the code can reproduce at
   small scale (cite `P-XXXX §…`, re-reading the section, never from memory).

Record which one and why. If nothing qualifies, stop: the tool cannot be
validated → record it `rejected` ("no reference output") — don't invent one.

### 4. Execute the reference material → ground truth

Run the **upstream** code (not your extraction) on the reference inputs,
through a small `reference/driver.py` that only sets inputs, calls upstream
code, and captures outputs to JSON — it must not recreate the algorithm
(Paper2Agent's executor rule). Seed every RNG the upstream code exposes, and
record which RNGs upstream leaves unseeded (a reproducibility finding in its
own right). Capture numbers, shapes, identifiers; store figures and large
artifacts by `sha256` + upstream path/URL, not as vault blobs. Replay once
from the saved inputs to confirm the reference is itself deterministic —
if two upstream runs disagree, the replay tolerance cannot be tighter than
that disagreement; record it.

If upstream code needs a change just to run (a removed API, a hard-coded
path), keep each change as a diff under `reference/patches/`, flag it
`importante`, and confirm it touches no computation.

**4b. GPU.** The researcher has no local GPU. If the reference computation is
GPU-`required`, do not fake it on CPU (a different device is a different
reference). Either (a) find a smaller reference example that runs on CPU and
still exercises the requested method, or (b) build a minimal self-contained
**transfer bundle** for Kaggle, under the same rule as `run-experiment`
step 2: never clone, push, or add a git remote for the vault; the bundle is
only the repo files the method needs at the pinned SHA, the driver, the
extracted tool (step 5), the lockfile, and a `MANIFEST.sha256`, run with
`sha256sum -c MANIFEST.sha256` first. Before any upload, run
`python "${CLAUDE_PLUGIN_ROOT}/scripts/security/check_bundle.py" <bundle_dir>`
(`crítico` gate): exit 0 → upload may proceed; exit 2 → do not upload, report
each `block` finding (path + kind), remove the file or move the secret to
Kaggle Secrets, rebuild the manifest and re-run until exit 0; exit 1 → the
check did not complete, not clean, do not upload. Note in `TOOL.md`
`## Validación` that the check ran and its final `status`. Both the upstream reference and the
extracted tool run **in the same Kaggle session**, so the step-6 replay
comparison is same-environment; the outputs come back as JSON and are
compared locally.

### 5. Extract the method into a standalone function

Write `tool/<method>.py`:

- **Copy the upstream computation verbatim** — the classes/functions the
  method uses, in their original order of operations. Remove only what the
  method does not need (logging to external services, plotting, notebook
  scaffolding, unrelated metrics), and only if removal cannot change the
  computed values (e.g. dropping a metric that consumes RNG draws *does*
  change them — then keep it or prove it doesn't).
- **Hard-coded paths, thresholds, constants → parameters**, each defaulting
  to the upstream value. Hidden choices become explicit parameters too
  (device, dtype, a warmup length inlined in a lambda).
- **File-based I/O:** inputs as JSON / arrays on disk; outputs written to a
  fresh output directory as JSON (+ native artifacts where useful). A
  `main()` CLI (`--params <json> --out <dir>`) so experiment code and the
  validation harness call it the same way.
- **Source map:** a module-level `SOURCE_MAP` (and the same table in
  `TOOL.md`): for every extracted block, `upstream file`, `line range`, and
  the pinned `commit SHA`, plus what changed (I/O only / parameterized / removed).
- **Upstream quirks stay.** An unseeded RNG, an odd init scale, a dtype cast
  that contradicts its own comment — preserved as upstream behavior (default)
  and listed in `TOOL.md` `## Particularidades del código original`. If a
  quirk needs a knob (e.g. `init_seed=None` = upstream's unseeded behavior),
  add a parameter whose default reproduces upstream.
- An `ARCHITECTURE` / `SPEC` dict (for methods that have one) stating the
  exact configuration in plain terms — what `preregister-experiment` will
  quote. For a model: normalization, weight tying, biases, init per matrix,
  optimizer, schedule, loss dtype.

Never import the extraction to produce ground truth, and never import the
upstream repo from the extraction — the tool must stand alone.

### 6. Validate — frozen tolerance, mechanical comparison, ≤ 6 attempts

**Freeze the tolerance first.** Before the first comparison, write into
`TOOL.md` `## Tolerancia`: each compared quantity, its comparison class,
`rtol` / `atol`, and the justification. Two classes:

| Class | What is compared | Default | Why |
|---|---|---|---|
| **replay** (primary) | extracted tool vs upstream code, same environment, same device, same inputs and seeds | `rtol 1e-5`, `atol 1e-8` | This is the fidelity test. An identical sequence of floating-point operations on the same machine reproduces bit-for-bit; re-associated but mathematically identical ops drift at ~1e-7 relative per op in float32. A real behavioral difference (a missing warmup step, an extra bias, a norm layer) shows up at ≥ 1e-3 almost immediately. 1e-5 sits between the two. |
| **published** (secondary) | tool or upstream vs outputs the authors shipped or reported | `rtol 0.03` for summary scalars (Paper2Agent's float tolerance); landmark windows from the paper's own stated precision | Different hardware, library versions and unseeded RNGs move reported numbers by ~1e-3–1e-2; papers print 2–3 significant figures. 3% absorbs that while still catching a wrong method. A landmark the paper states loosely ("around 10k epochs") gets a window derived from that wording and the paper's figures, written down now. |

Chaotic computations (long training runs) amplify rounding-level differences:
compare trajectories pointwise only over a horizon where same-op replay is
expected to hold (or require bit-exactness when the op sequence is
identical), and compare the rest through landmarks. State the horizon in
`## Tolerancia`. `replay` is required for `validated`; `published` is
required when step 3 found authors' outputs or reported results, and is
recorded either way.

Then loop, **at most 6 attempts**:

1. write / adjust `validation/test_<method>.py` — it runs the tool on the
   reference inputs, writes the candidate JSON, and calls
   `python ${CLAUDE_PLUGIN_ROOT}/scripts/paper_to_tool/compare_outputs.py
   --reference … --candidate … --rtol … --atol … --json`;
2. run it (through `sandbox_guard.py run`);
3. save the script's JSON as `validation/attempt-NN.json`;
4. on `fail`: diagnose from the failing paths, and fix **the extracted
   code** so it matches upstream. Never the reference outputs, never the
   tolerance, never `--ignore` of a result field, never a relaxed assertion
   (Paper2Agent's verifier rule, and Kairo's prereg rule in miniature).

A fresh subagent may do the diagnosis if the context is getting long, but the
verdict is always `compare_outputs.py`'s exit code, never an agent's opinion.

### 7. Freeze or reject

- **All required comparisons `pass`** → `status: validated`. Write the
  remaining `TOOL.md` fields, create `Tools/.gitattributes` with `* -text` if
  absent (so git never rewrites line endings under a frozen tool), run
  `python ${CLAUDE_PLUGIN_ROOT}/scripts/paper_to_tool/tool_hash.py compute
  Tools/P-XXXX/<method>` and record the printed `validation_hash`. Frozen:
  from now on only `TOOL.md` `## Enmiendas` may change (append-only); a
  changed tool is a new folder (`<method>-v2`) with its own validation.
- **Budget spent (6 attempts) or no usable reference** → `status: rejected`,
  with the reason in one line in frontmatter and in full in
  `## Motivo de rechazo` (which comparison failed, the last max error, what
  was tried). Keep the folder and hash it too — it stops the same failed
  extraction from being retried blindly.
- Record `cost:` — wall-clock per phase, CPU/GPU-h, and the Claude-session
  cost if it can be read (`/cost`); if it can't be measured from inside the
  session, write "not measured" rather than a guess.
- Commit in the vault: `Tool P-XXXX/<method>: validated` (or `rejected`).
  Stage only `Tools/…`. The sandbox stays outside the vault; delete it only
  if the researcher asks.

### 8. Report

One block: tool path, status, `validation_hash`, which reference it was
validated against and the max errors, the architecture/spec summary, time
and cost, and every flag by severity. Don't dump logs.

## Security — `crítico`

Third-party research code is untrusted code. The rules above in one place:

- Show repo, pinned commit, sandbox path, isolation level, network use, and
  the exact commands **before** executing anything; require explicit
  approval; run only approved commands (`sandbox_guard.py run --plan`).
- Never execute inside the vault or its git work tree; never give third-party
  code a `Papers/` or `Projects/` path; strip credentials from its
  environment.
- `pickle.load` / `torch.load` of repo-shipped or downloaded files executes
  code: list every such load in the approval, and use `weights_only=True`
  (or an equivalent safe loader) whenever the file is plain tensors/dicts.
- Nothing leaves this machine unchecked: a Kaggle bundle (step 4b) is
  uploaded only after `check_bundle.py` exits 0 on it. Exit 2 (a `.env`,
  key, token, `.git/`, or `Papers/`/`Projects/` content in the bundle) and
  exit 1 (check incomplete) both block the upload.
- Be honest about isolation: without a container, the guard is a set of
  mechanical refusals, not an OS sandbox — the approval is the real control.

## Flagging — severity vocabulary

Same table as `preregister-experiment`. Typical here:

| Tag | Examples |
|---|---|
| `crítico` | executing third-party code (always — the approval gate); a reference that needs a GPU with no CPU path and no Kaggle route; ground truth that is itself nondeterministic beyond the replay tolerance |
| `importante` | repo has no license; `code_repo` evidence is weak; upstream needed a patch to run; no `published` comparison possible; an upstream quirk that changes results (unseeded init, dtype contradicting its comment) |
| `menor` | dependency versions chosen by date because nothing was pinned; logging/plotting removed from the extraction |

## Common mistakes

- **Running anything before approval** — including a "harmless" `pip install`
  (setup.py executes code) or a `torch.load` of a shipped file.
- **Cloning into the vault** or into its parent repo's work tree.
- **Reimplementing instead of extracting.** If the tool's math isn't a copy
  of cited upstream lines, it is the E-0001 failure mode with extra steps.
- **"Fixing" upstream while extracting** (seeding an unseeded RNG by
  default, switching a dtype, removing a quirk). Preserve; parameterize;
  document.
- **Choosing the tolerance after seeing the error.** `## Tolerancia` is
  written before attempt 1.
- **Using the extraction to produce the ground truth**, or importing the
  upstream repo from the extraction.
- **A pass on an empty comparison.** `compare_outputs.py` fails when nothing
  was compared; every result field is compared.
- **Treating `rejected` as deletable.** Rejected attempts are the record that
  stops blind retries.
- **Extracting "the whole paper".** One method per tool.
- **Faking a GPU reference on CPU.** Different device, different reference.

## Related

- `${CLAUDE_PLUGIN_ROOT}/scripts/paper_to_tool/sandbox_guard.py` — location
  check, approved-command runner, credential scrubbing, run log.
- `${CLAUDE_PLUGIN_ROOT}/scripts/security/check_bundle.py` — the isolation
  check a Kaggle bundle passes before upload (step 4b); same gate as
  `run-experiment` step 2.
- `${CLAUDE_PLUGIN_ROOT}/scripts/paper_to_tool/compare_outputs.py` — the
  mechanical pass/fail (step 6).
- `${CLAUDE_PLUGIN_ROOT}/scripts/paper_to_tool/tool_hash.py` — the
  `validation_hash` (step 7); re-verified by `run-experiment` pre-flight.
- `${CLAUDE_PLUGIN_ROOT}/templates/tool-template.md` — `TOOL.md`.
- `${CLAUDE_PLUGIN_ROOT}/scripts/code_repo/find_code_repo.py` — fills
  `code_repo:` for papers ingested before the field existed.
- `preregister-experiment` — uses a validated tool, offers this skill, or
  flags `method_provenance: reimplemented_from_text`.
- `run-experiment` — verifies the tool's `validation_hash` in pre-flight.
