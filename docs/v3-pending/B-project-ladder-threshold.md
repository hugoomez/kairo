# B-project-ladder-threshold — `ladder_cost_threshold` in the project hub

B1's simplification ladder is **offered** (never forced) when a confirmatory
design's estimated cost exceeds a per-project threshold, or when the design
reimplements a paper's method from its text. The threshold belongs in the
project hub, whose template is Block A's file. Until this lands,
`preregister-experiment` step 0 and `hypothesis-cycle` read
`ladder_cost_threshold` from the hub if present and otherwise use the fallback
stated there (offer whenever the design needs a GPU or `cost_estimated` ≥ 1
GPU-h / is unknown) — so nothing blocks on this.

## 1. Target

`templates/project-template.md` (owner: A).

## 2. Change

Insert immediately after the `completo_cost_threshold: <value>` line:

```yaml
# ladder_cost_threshold — optional, same unit as compute_budget. When a
# confirmatory design's cost_estimated exceeds it, preregister-experiment step 0
# OFFERS a simplification ladder (2-3 cheap exploratory rungs, role:
# exploratory, rung 0-2) before the confirmatory freeze. Never forced. Unset:
# offered whenever the design needs a GPU or its cost is >= 1 GPU-h or unknown.
# Also offered regardless of cost when method_provenance would be
# reimplemented_from_text.
ladder_cost_threshold: <value>
```

## 3. Verify

1. `grep -n "ladder_cost_threshold" templates/project-template.md` → one field,
   directly under `completo_cost_threshold`.
2. `grep -rn "ladder_cost_threshold" skills/` → `preregister-experiment` and
   `hypothesis-cycle` read it with the same fallback text as the template
   comment (keep them in sync).
3. `claude plugin validate .` still passes.
