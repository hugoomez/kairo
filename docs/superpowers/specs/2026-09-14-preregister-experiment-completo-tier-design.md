# preregister-experiment: `completo` tier + frequentist/Bayesian analysis-plan choice

Date: 2026-09-14
Status: approved for implementation planning

## Problem

`preregister-experiment` v1 only implements the `ligero` tier: decision thresholds
(`T_apoyo` / `T_refuta`) are picked by domain judgement, and the mechanical
analysis is always the frequentist `two_proportion_test.py`. The skill's own
"Not in v1" section already flags two deferred pieces:

1. A `completo` tier with a **formal sample-size justification** — required once
   a hypothesis is on a publication line (`linea_publicacion: true`) or the
   experiment is expensive enough that domain-judgement thresholds are too risky
   a way to plan it.
2. A **Bayesian** analysis-plan option, alongside the existing frequentist one —
   the experiment template already anticipates this (`result.bayes_factor`
   alongside `result.p_value`), but nothing computes it.

This spec extends `preregister-experiment` (and the two downstream skills it
hands off to, `run-experiment` and — only as a documented gap, not a fix —
`update-confidence`) to implement both.

## Non-goals

- Combining two Bayesian-plan experiments' evidence for the replication gate
  (`update-confidence` / `combine_effects.py`). Flagged as a known gap (§F),
  not solved here.
- A continuous-outcome (means / Cohen's d) mechanical test, frequentist or
  Bayesian. Neither exists yet even for `ligero`; out of scope until one is
  needed. Both new scripts in this spec mirror the existing two-proportion
  shape only.
- Any change to `hypothesis-cycle`, `create-project`, or `spawn-hypothesis`.

## A. Tier determination

New step in `preregister-experiment`, run before the protocol is fixed (current
step 2b):

```
completo required  <=>  hypothesis.linea_publicacion == true
                        OR (cost_estimated is set AND project.completo_cost_threshold is set
                            AND cost_estimated > completo_cost_threshold)
```

- New optional project field `completo_cost_threshold: <value>` in
  `templates/project-template.md`, same units as the existing
  `compute_budget` field, placed next to it.
- If `cost_estimated` is set but the project has no `completo_cost_threshold`,
  cost alone cannot force `completo` — flag `importante`: the project has no
  cost-based tier trigger defined, so only `linea_publicacion` is being checked.
- Both fields are free-form (`"500 GPU-h"` or a currency figure, per
  `compute_budget`'s existing comment) — the numeric comparison only applies
  when `cost_estimated` and `completo_cost_threshold` share the same unit. A
  unit mismatch (or either value not being a bare number) is treated the same
  as an unset threshold: flag `importante`, cost cannot force `completo`.
- **Hard floor, no override.** Once `completo` is required, the freeze blocks
  (`crítico`) until §B is satisfied. Unlike hypothesis-cycle's logged
  clear-fail override, there is no in-spec way to freeze at `ligero` anyway —
  this mirrors the "binding rule" language already used for principle 3
  (preregistration itself).
- `ligero` stays available whenever `completo` isn't triggered; a researcher
  may still *opt into* `completo` voluntarily for a smaller experiment.

Frontmatter: `tier: <ligero | completo>` (field already exists in the
template — this spec defines who sets it and when).

## B. Sample-size justification (`completo` tier only)

Replaces `ligero`'s domain-judgement thresholds with a formal a-priori power
analysis, per current best-practice guidance (Lakens, *Sample Size
Justification*): state the smallest effect size of interest (SESOI), target
power, and alpha; the required N is derived mechanically from those three —
never picked first and rationalized after.

**New script:** `scripts/analysis/sample_size.py`, same conventions as the two
existing analysis scripts (stdlib-only, `argparse`, `--version`,
`--json`/`RESULT_JSON:` line).

Method: Cohen's h (arcsine-transformed effect size — the same transform
`two_proportion_test.py` already uses for its own `cohens_h` output), normal
approximation:

```
h = 2*asin(sqrt(p1)) - 2*asin(sqrt(p2))        # or passed directly via --h
n_per_group = ceil( ((z_(alpha/2) + z_power) / h) ** 2 )
```

`z_(alpha/2)` and `z_power` via `statistics.NormalDist().inv_cdf(...)` — the
same stdlib facility `combine_effects.py` already uses for its CI z-values.

CLI:

```
python sample_size.py --p1 P1 --p2 P2 --alpha 0.05 --power 0.8 [--json]
python sample_size.py --h H --alpha 0.05 --power 0.8 [--json]      # effect given directly
python sample_size.py --version
```

(`--p1`/`--p2` and `--h` are mutually exclusive; `--p2` is the smallest
treatment rate that would still count as meaningful — baseline ± SESOI, not
the effect you expect to actually find.)

Output: `h`, `z_alpha2`, `z_power`, `n_per_group`, `n_total`, plus an echo of
every input (`p1`, `p2` or `h`, `alpha`, `power`). Docstring documents the
normal-approximation nature explicitly (not exact noncentral-t/binomial),
same transparency style as `combine_effects.py`'s documented DerSimonian–Laird
choice. Exit codes: 0 ok, 2 invalid input (e.g. `p1 == p2`, alpha/power
outside `(0, 1)`).

At preregistration time, `completo`'s `## Plan de análisis` must state the
SESOI, alpha, target power, and the script's `n_per_group`/`n_total` output —
and the stopping rule (already required in `ligero`) becomes exactly that
fixed N, not a separately chosen figure.

## C. Analysis plan: frequentist vs. Bayesian — independent of tier

New frontmatter field on the experiment note, `templates/experiment-template.md`:

```yaml
tier: <ligero | completo>
# analysis_plan — which mechanical test computes the verdict (run-experiment
# step 5). frequentist -> two_proportion_test.py; bayesian ->
# bayes_factor_proportions.py. Chosen at preregistration time, frozen with
# everything else.
analysis_plan: <frequentist | bayesian>
```

Any tier × analysis_plan combination is valid (`ligero`+`bayesian` and
`completo`+`frequentist` are both fine) — the two axes are orthogonal.

**New script:** `scripts/analysis/bayes_factor_proportions.py`, same input
shape as `two_proportion_test.py` (`X1 N1 X2 N2`), stdlib-only.

Method: Gunel & Dickey (1974) independent-binomials Bayes factor — H1 (rates
independent, `p1 ≠ p2`) vs. H0 (shared rate, pooled data), each with a
`Beta(a, b)` prior on the rate(s), default `a = b = 1` (uniform), overridable.
Fully closed-form via `math.lgamma` (log Beta-Binomial marginal likelihood),
no numerical integration needed:

```
log_BetaBinom(x, n, a, b) = lgamma(n+1) - lgamma(x+1) - lgamma(n-x+1)
                           + lgamma(x+a) + lgamma(n-x+b) - lgamma(n+a+b)
                           - (lgamma(a) + lgamma(b) - lgamma(a+b))

log_m_H1 = log_BetaBinom(x1, n1, a, b) + log_BetaBinom(x2, n2, a, b)
log_m_H0 = log_BetaBinom(x1+x2, n1+n2, a, b)
BF10 = exp(log_m_H1 - log_m_H0)
BF01 = 1 / BF10
```

CLI:

```
python bayes_factor_proportions.py X1 N1 X2 N2 \
    [--prior-a 1.0] [--prior-b 1.0] [--bf-threshold 3.0] \
    [--direction increase|decrease] [--labels treatment,control] [--json]
python bayes_factor_proportions.py --version
```

Verdict (three-way, parallel to `two_proportion_test.py`'s `classify()`):

- Without `--direction`: report `BF10`/`BF01` plus a Jeffreys/Kass–Raftery
  qualitative label (anecdotal / substantial / strong / very strong /
  decisive) only — no apoya/refuta/inconcluso, "do NOT infer support" (mirrors
  the frequentist script's no-flags behavior).
- With `--direction` and `--bf-threshold` (default 3.0, "substantial
  evidence," Kass & Raftery 1995 — documented, researcher-overridable like
  alpha):
  - `BF10 >= threshold` AND effect direction matches prediction → `apoya`
  - `BF01 >= threshold` (i.e. `BF10 <= 1/threshold`) → `refuta` (evidence for
    the shared-rate/no-difference model — direction-independent, since this
    is evidence *for the null*, not evidence of a wrong-direction effect)
  - otherwise → `inconcluso`

Exit codes: 0 ok, 2 invalid input. `--version` prints
`bayes_factor_proportions.py 1.0.0 (Gunel-Dickey independent-binomials Bayes factor, Beta(a,b) prior)`.

At preregistration time, a `bayesian` `## Plan de análisis` must state the
prior (default Beta(1,1) unless justified otherwise) and the BF decision
threshold — the Bayesian equivalent of stating alpha for a frequentist plan.

## D. `confidence` field semantics — reinforced at the point of choice

`hypothesis-template.md` already documents `confidence` as either a
`frequentist_heuristic` (subjective ordering aid, not a probability) or a
`bayesian_posterior` (an actual posterior). `preregister-experiment` currently
never surfaces this. This spec adds an explicit statement **at the moment the
researcher picks `analysis_plan`**: `confidence` on the resulting hypothesis
is a real posterior only when *every* adjudicating experiment for that
hypothesis used `analysis_plan: bayesian`; if any adjudicating experiment was
frequentist, `confidence` stays a `frequentist_heuristic` — never silently
upgraded to "posterior" just because one of several experiments happened to
compute a Bayes factor.

## E. Mixed bayesian + linea_publicacion — flagged gap

`update-confidence`'s replication combiner (`combine_effects.py`) works on
effect-size + standard error (frequentist CIs). It has no defined way to
combine two Bayes factors. So a `linea_publicacion: true` hypothesis (which
*needs* a second independent experiment combined into one verdict) choosing
`analysis_plan: bayesian` walks toward a combination step that doesn't exist.

`preregister-experiment` flags this explicitly and blocks the freeze
(`crítico`) **only** when both `linea_publicacion: true` and
`analysis_plan: bayesian` coincide — the researcher must acknowledge the gap
(e.g. by choosing `frequentist` instead, or by explicitly accepting that a
second Bayesian replication will need a follow-up combination method not yet
built). This is a surfaced limitation, not a fix — actually combining
Bayesian replications is out of scope for this change (see Non-goals).

## F. Necessary touch to `run-experiment`

Step 5 ("Mechanical analysis") currently only documents calling
`two_proportion_test.py`. It must branch on the frozen `analysis_plan`:

| `analysis_plan` | Script | Result field |
|---|---|---|
| `frequentist` | `two_proportion_test.py` (unchanged call) | `result.p_value` |
| `bayesian` | `bayes_factor_proportions.py` (frozen `prior-a`/`prior-b`/`bf-threshold`/`direction`) | `result.bayes_factor` |

Same verdict-string mapping (`apoya`/`refuta`/`inconcluso` →
`apoyada`/`refutada`/`inconclusa`) applies regardless of which script produced
it. "If the frozen plan names a test with no script available, stop and
report" (existing rule) is unchanged — now there are two scripts it can
legitimately name.

## File-level footprint (`kairo-plugin`)

- `skills/preregister-experiment/SKILL.md` — rewrite: frontmatter description,
  "Scope" section (`ligero` **and** `completo`, drop the "Not in v1" deferral
  for both pieces this spec implements), new tier-determination step, §B/§C/§D/§E
  woven into steps 2b/3, updated "Frontmatter at freeze" list (`analysis_plan`),
  new Common mistakes entries, updated "Related".
- `scripts/analysis/sample_size.py` — new.
- `scripts/analysis/bayes_factor_proportions.py` — new.
- `templates/project-template.md` — add `completo_cost_threshold`.
- `templates/experiment-template.md` — add `analysis_plan:` frontmatter field
  + a line in `## Plan de análisis`'s placeholder text prompting for
  SESOI/alpha/power/N (completo) or prior/BF-threshold (bayesian).
- `skills/run-experiment/SKILL.md` — step 5 branch (table above), updated
  Common mistakes entry (wrong script for frozen `analysis_plan`), updated
  "Related".
- `README.md` — one-line update to the `preregister-experiment` row
  (mentions `completo` + analysis-plan choice) — this file already has
  unrelated uncommitted changes from other work; edit additively, don't touch
  unrelated sections.

`update-confidence` and `hypothesis-template.md` need **no** changes — the
confidence-kind distinction already exists there; this spec only makes
`preregister-experiment` state it explicitly at the decision point.

## Open items for the implementation plan

- Exact wording/placement of the new tier-determination step number inside
  `preregister-experiment`'s existing step sequence (insert before current
  step 2, renumber, or fold into step 2b as a sub-step) — an implementation
  detail, not a design fork.
- Whether `sample_size.py` and `bayes_factor_proportions.py` need dedicated
  correctness tests (e.g. checked against known reference values — G*Power-style
  tables for `sample_size.py`; a hand-computed small-N Beta-Binomial BF for
  `bayes_factor_proportions.py`) — recommended, left to the implementation plan.
