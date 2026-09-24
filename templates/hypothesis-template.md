---
id: H-XXXX
project: <PROJ-XXX>
# status enum — use exactly one:
# propuesta | en_cola | preregistrada | en_experimento | apoyada | refutada | inconclusa | evidencia_mixta
status: propuesta
created: <YYYY-MM-DD>
updated: <YYYY-MM-DD>
# parent — optional: the hypothesis this one refines or specialises
parent: <H-XXXX>
# confidence — number in [0, 1].
# Document which kind of number it is, chosen by the linked experiment's analysis plan:
#   - frequentist heuristic score: a subjective ordering aid, NOT a probability
#     (use when the analysis plan is frequentist / null-hypothesis testing)
#   - real bayesian posterior: an actual posterior probability of the claim
#     (use only when the analysis plan produces a posterior)
confidence: <0.0-1.0>  # kind: <frequentist_heuristic | bayesian_posterior>
linked_papers: []      # e.g. [P-0001, P-0002]
# linked_experiment — experiments that ADJUDICATE this hypothesis (it is their
# primary `hypothesis:`). List, even if just one. update-confidence combines only
# these; a byte-for-byte re-run does not count as an independent second entry.
linked_experiment: []  # e.g. [E-0001, E-0002]
# collateral_evidence — optional: experiments where this hypothesis appears under
# `secondary_hypotheses:` (non-adjudicating context only). NEVER an input to
# combine_effects.py and never moves this hypothesis's status.
collateral_evidence: []  # e.g. [E-0004]
experiment_validity: <valid | invalid>
generated_by:
  origin: <human | agent>
  # the fields below only when origin: agent
  model: <model id, e.g. claude-sonnet-5>
  skill_version: <e.g. kairo/hypothesis-cycle@1.0.0>
  pipeline_config: <path or identifier of the pipeline config used>
linea_publicacion: false
# needs_human_review — optional: set true by hypothesis-cycle (rounds exhausted)
# or run-experiment (invalid run). Cleared once a human has looked.
needs_human_review: false
# paper_thread — optional: the publication thread this hypothesis feeds
paper_thread: <slug-or-id>
# spawned_from — optional: the note that generated this one
spawned_from: <H-XXXX | E-XXXX | F-XXX>
# verifications — append-only, docs/v3-interfaces.md §1b. Written only by
# scripts/ledger/verifications.py after a fresh-verifier run (hypothesis-cycle
# before creation; update-confidence before apoyada). Never edit or remove an
# entry; the latest entry per scope governs gating, every entry is reported.
#   - verifier: kairo/fresh-verifier@<version>
#     model: <model id>
#     date: <YYYY-MM-DD>
#     verdict: no_errors_found | errors_found | cannot_assess
#     scope: note | section:<exact heading text>
verifications: []
# origin_flag — optional: set by hypothesis-cycle's budget-overflow steps.
#   wildcard  — seeded by a serendipity-scan lead (see hypothesis-cycle "Budget overflow")
#   evolution — combines two top-ranked candidates from a tournament round
# Omit entirely for an ordinary gap-derived or human-submitted candidate.
origin_flag: <wildcard | evolution>
# history — append-only. Never edit or remove past entries; only append.
# Written only by update-confidence for every post-creation status transition.
history:
  - date: <YYYY-MM-DD>
    status: propuesta
    by: <human name | agent id>
    # experiments / combination — optional, set by update-confidence on evidence edges
    experiments: []              # e.g. [E-0007, E-0011]
    combination: n/a             # e.g. combine_effects.py@2.0.0 (random-effects DerSimonian-Laird)
    evidence: <short note or link>
---

## Claim

<One-sentence falsifiable statement.>

## Justificación (evidencia citada)

- <P-XXXX §Sección / Tabla N / Figura N — qué muestra y cómo sostiene el claim>
- <P-XXXX §Sección / Tabla N / Figura N — ...>
<!-- Excepción origin: human sin respaldo: escribir exactamente
     "intuición del investigador, sin respaldo directo en la literatura" -->

## Hipótesis rival descartada

<La hipótesis rival plausible contra la que se contrastó (Check 4 de
hypothesis-cycle) y la predicción concreta que discrimina este claim de ella.>

## Revisión del ciclo

<Incluir siempre que hypothesis-cycle haya corrido ≥ 1 ronda de refinamiento,
sea cual sea el desenlace:
  - pasó tras refinar → una entrada breve por ronda (qué era *refinable*, el
    cambio al claim / sketch, reingreso en Check 1). Un re-escopado a mitad de
    ciclo debe verse aquí.
  - rondas agotadas sin veredicto → el intercambio completo ronda por ronda +
    needs_human_review: true.
  - override manual de un clear fail → el clear fail, el Check que lo mató y la
    razón declarada del override.
Omitir esta sección solo si pasó en la primera pasada sin ninguna ronda.>

## Verificación independiente

<Append-only, escrita por verifications.py: una entrada fechada por corrida de
fresh-verifier (veredicto, alcance, sha256 del paquete, hallazgos con severidad
y ubicación). Omitir hasta la primera verificación. El paquete del verificador
nunca incluye esta sección.>

## Lección

<Se completa una vez resuelta la hipótesis (apoyada / refutada / inconclusa /
evidencia_mixta): qué se aprendió y qué se traslada a trabajo futuro.>
