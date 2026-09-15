# Citation verification — hallazgo #10

Date: 2026-09-15. Scope: every citation in every `## Justificación (evidencia
citada)` section across H-0001–H-0006, and every citation in
`Estado-del-arte.md` (all sections), in
`Projects/early-stopping-tareas-algoritmicas/`. This is a verification-only
pass — nothing was changed in any note.

**Method.** Read the full `## Texto completo` of all 14 `Papers/*.md` notes
(these carry explicit section/figure/equation attributions from ingestion,
not raw PDF text). Cross-checked every citation against that structure. Where
a specific factual claim was genuinely ambiguous against the ingested digest
(one case: P-0003's epoch-wise vs. model-wise double descent and its
relationship to label noise), fetched the real paper's HTML
(`ar5iv.labs.arxiv.org/html/1912.02292`) directly rather than guessing from
the digest. No citation was left unresolved — every one below got an actual
verdict, not a "couldn't check."

---

## Headline finding

Two clear, repeated patterns emerged, not just scattered one-offs:

1. **Wrong-section-within-the-same-paper is not random — it clusters on
   papers whose real content is split across many closely-numbered
   subsections that make similar-sounding claims.** P-0010 (Prieto,
   Softmax Collapse) is the worst offender: it has five candidate locations
   for four related-but-distinct claims (§3.1 SC definition, §3.2 float32
   vs. float64 timing, §3.3 StableMax/precision fixes, §4.2 NLM mechanism,
   §5.1 ⟂Grad/⟂AdamW), and **four separate citations across the vault each
   get one of these five wrong or incomplete** — while a *different* citation
   elsewhere in the very same `Estado-del-arte.md` gets the full, correct
   five-part locator. This means the correct mapping was known and written
   down somewhere in the corpus, but not applied consistently every time the
   same paper was cited again. P-0013 (Xu, commutator defect) shows the same
   thing once: its causal-intervention evidence lives in §5.4/§9.2/§9.3, but
   two separate citations (H-0002 and "Línea de evolución") attribute
   "causal role confirmed" to §5.1/§5.2/§6 instead — while a third citation
   ("Papers ancla vs. de frontera") correctly cites §9.3 for the same claim.
2. **A specific number was attributed to the wrong paper entirely, not just
   the wrong section.** "Línea de evolución" states weight decay "reduce el
   retardo de >10⁴ a <10³ pasos" and cites P-0001 §3.1, §3.3. That exact
   figure (`>10^4 to <10^3 steps`) is a **P-0006 §4.2** result, not
   anything in P-0001 — P-0001's own text only ever says weight decay "cuts
   the amount of data needed... by more than half," never gives this
   step-count range. Tellingly, a different citation two sections later
   ("Lo establecido," the same underlying claim) correctly includes P-0006
   §4.2 in its source list — so, again, the correct attribution existed
   in the document and simply wasn't used the second time the same fact was
   restated.

Both patterns point at the same root cause, addressed in the final
assessment below: **citations are not being re-checked against source text
each time a fact is restated — they're being regenerated from
memory/pattern-matching against a similar earlier citation, which drifts.**

The good news, stated up front so it doesn't get lost under the above: **the
abstract-only discipline is perfect.** Every single citation to P-0011 and
P-0012 (grepped across both documents, ~13 real citation instances) uses
`§Resumen`, with zero fabricated section/figure numbers. Rule #4 has no
violations at all.

---

## Summary count

| Category | Count |
|---|---|
| Total individual citations checked | ≈184 |
| **Exacta** | 170 |
| **Imprecisa** | 11 |
| **Incorrecta** | 3 |
| Genuinely unverifiable | 0 |

(≈184 is a manual count of distinct `P-XXXX §…` locator strings — 27 across
H-0001–H-0006, ≈157 across `Estado-del-arte.md`'s eight cited sections. A
compound bullet citing the same paper with two section numbers, e.g. `P-0009
§2, §4.2`, is counted once; a bullet citing two different papers is counted
twice.)

No citation required abandoning verification — the ingested `Texto completo`
notes were specific enough (explicit section/figure/equation numbers from
ingestion) to check directly in all but one case, where I fetched the actual
arXiv HTML to resolve the ambiguity (P-0003, detailed below).

---

## Full detail — every imprecisa / incorrecta, by note

### H-0001 — regret de early stopping por patience

All 4 citations: **exacta**. No issues.

### H-0002 — señal de plateau predice grokking

**1. `P-0013 §5.1–§5.2` — "y las intervenciones causales confirman su papel"**
— **incorrecta** (the causal-intervention half of the claim; the power-law
lead-time half is fine).

Source, §5.1/§5.2 (SCAN/Dyck results): "The lead time between defect onset
and grokking obeys Delta_t = 0.149 * t_grok^1.180... (Sec. 5.1, Eq. 9)" /
"Delta_t = 0.239 * t_grok^1.132... (Sec. 5.2, Eq. 10)" — this supports the
power-law claim. But causal interventions are a *different* section: "Causal
interventions. Mild curvature boosting gives ~32% speedup on SCAN...
(Sec. 5.4.1)... Dyck shows ~50% speedup... (Sec. 5.4.2)... Across all three
task families, suppression... delays or prevents grokking... (Sec. 9.3,
Table 6; Sec. 9.2)." §5.1–§5.2 do not contain the causal-intervention
evidence at all.

**Proposed fix:** split into two citations — `P-0013 §5.1–§5.2` for the
power-law lead time, `P-0013 §5.4, §9.2–§9.3` for the causal-intervention
claim.

**2. `P-0002 §5.1` — "decrecen de forma continua durante la fase de
'formación del circuito' en la que la accuracy de validación está plana"**
— **imprecisa**.

§5.1 only *defines* restricted/excluded loss as progress measures. The
timing claim (they fall during circuit formation while test accuracy stays
flat) is §5.2: "circuit formation (~1.4k–9.4k, restricted loss falls while
test accuracy still low, driven by weight decay)."

**Proposed fix:** `P-0002 §5.1–§5.2` (or `§5.2` alone for this specific
timing claim).

**3. `P-0007 §4.1` — "existen réplicas 'nunca grokkean' que dan la clase
negativa necesaria para medir AUROC"**
— **imprecisa**.

§4.1 only defines D_crit conceptually ("critical dataset size D_crit where
[C_mem and C_gen] are equally efficient"). The empirical "never generalizes"
replicas are §5.2: "Ungrokking: (P2) sharp phase transition from ~100% to
near-0% test accuracy when D is reduced below D_crit... (Sec. 5.2, Fig. 4)."

**Proposed fix:** `P-0007 §4.1, §5.2`.

### H-0003 — seguir entrenando vs reasignar cómputo

All 4 citations: **exacta**. No issues.

### H-0004 — fallo de early stopping depende del optimizador

**1. `P-0010 §3.3, §4.2` — "⟂AdamW y StableMax lo acortan"**
— **imprecisa** (StableMax half is fine at §3.3; ⟂AdamW half is missing its
real location).

§3.3 covers StableMax correctly ("Preventing SC - via higher precision or
the StableMax Cross-Entropy (StCE) loss"). §4.2 covers NLM correctly. But
⟂AdamW is introduced and evidenced in §5.1: "⟂Grad / ⟂AdamW projects out the
NLM component; ⟂AdamW reaches 100% test accuracy within ~400 iterations..."
— not mentioned anywhere in §3.3 or §4.2.

**Proposed fix:** `P-0010 §3.3, §4.2, §5.1`.

Rest of H-0004's 4 citations: **exacta**.

### H-0005 — ruido de etiqueta y double descent en aritmética modular

**1. `P-0003 §5, §6 Fig 9` — "el epoch-wise double descent en el error de
test es más pronunciado y a veces solo aparece con ruido de etiqueta"**
— **imprecisa**.

This one needed a real source check — the ingested digest doesn't resolve
it cleanly. Fetched `ar5iv.labs.arxiv.org/html/1912.02292` directly:

> §6 ("Epoch-wise Double Descent"): "Our experiments (**Figure 10**) show
> that many settings of dataset and architecture exhibit epoch-wise double
> descent, in the presence of label noise."
>
> §5 ("Model-wise Double Descent"): "We observe all forms of double descent
> most strongly in settings with label noise in the train set... However,
> we also show several realistic settings with a test-error peak **even
> without label noise**."

Two problems: (a) §5 is the *model-wise* section — the general "strongest
with noise, but still present without it" statement lives there, not in the
epoch-wise section, so citing §5 for an epoch-wise-specific claim
conflates the two axes; (b) **Figure 9** (confirmed to exist, in §6) shows
"three complexity regimes" without an explicit noise framing — it's
**Figure 10**, not Figure 9, that explicitly ties epoch-wise double descent
to label noise across ResNet-18/CIFAR and the 5-layer CNN.

**Proposed fix:** `P-0003 §6 Fig 10` (drop §5, which is about the wrong
axis; cite Fig 10, not Fig 9, for the noise-dependence claim specifically).

Rest of H-0005's 3 citations: **exacta**.

### H-0006 — sub-régimen de datos altos donde early stopping es óptimo

**1. `P-0010 §3.3, §4.2` — "Softmax Collapse en punto flotante puede
suprimir una subida tardía... motiva el brazo de robustez en float64"**
— **imprecisa**.

The specific float32-vs-float64 timing evidence is §3.2, not §3.3 or §4.2:
"Across train splits (40/60/70%) generalization stalls exactly when SC
begins, and SC arrives earlier in float32 than float64 (Fig. 2); float64
delays SC onset by ~1000-2000 epochs (§3.2)." §3.3 is about *preventing* SC
via precision/StableMax (related but not the same claim), §4.2 is NLM.

**Proposed fix:** `P-0010 §3.2` (add it; keep §3.3/§4.2 only if the note
also wants the prevention/NLM framing).

Rest of H-0006's 4 citations: **exacta**.

---

### Estado-del-arte.md

**"Vocabulario y notación"**

**1. `P-0007 §3, §4.1` — "Circuito generalizador C_gen vs. memorizador
C_mem"** (term definition) — **imprecisa** (minor).

Per the paper's own structure as ingested, C_gen/C_mem are defined in
"1. Introduction / 2. Notation," not §3 ("Three ingredients for grokking,"
which *uses* the already-defined terms). D_crit at §4.1 is correctly cited.

**Proposed fix:** `P-0007 §2, §4.1`.

**"Línea de evolución de las ideas"**

**2. `P-0001 §3.1, §3.3` — "weight decay identificado como la intervención
más efectiva (reduce el retardo de >10⁴ a <10³ pasos)"**
— **incorrecta**.

P-0001 §3.3 supports "most effective intervention... cuts data needed by
more than half" — it never states a step-count figure. The `>10⁴ to <10³
steps` number is a **P-0006** result: "weight decay cuts the generalization
delay from >10^4 to <10^3 steps (Sec. 4.2, Fig. 7)." This is the more
serious kind of miss — not adjacent-section drift, but a specific number
lifted from one paper and re-attached to a different one. Notably, "Lo
establecido y lo debatido" restates the *same* underlying fact a few
sections later and correctly includes P-0006 §4.2 in its source list —
so the correct attribution exists elsewhere in this very document.

**Proposed fix:** `P-0001 §3.3; P-0006 §4.2` (P-0001 for the "most
effective, cuts data by half" claim, P-0006 for the specific step-count
figure — don't attribute the number to P-0001 alone).

**3. `P-0010 §4.2, §5.1` — "StableMax y ⟂Grad producen grokking sin weight
decay"** — **imprecisa** (StableMax half; same pattern as H-0004 finding #1,
run in reverse — here ⟂Grad's location, §5.1, is correctly included, but
StableMax's, §3.3, is missing).

**Proposed fix:** `P-0010 §3.3, §4.2, §5.1`.

**4. `P-0013 §5.1, §5.2, §6` — "rol causal confirmado"**
— **incorrecta**. Same underlying error as H-0002 finding #1 above,
independently repeated: causal-intervention evidence is in §5.4/§9.2/§9.3,
not §5.1/§5.2/§6. (A *third* citation, in "Papers ancla vs. de frontera,"
correctly cites `P-0013 §6, §9.3` for the same causal claim — confirming
this is a real, avoidable drift rather than an ambiguous case.)

**Proposed fix:** `P-0013 §5.1, §5.2, §5.4, §9.2–§9.3`.

**"Lo establecido vs. lo debatido"**

**5. `P-0002 §4.4` — "Memorización y generalización coexisten... las
neuronas/componentes memorizadoras pueden identificarse (IPR) y podarse"**
— **imprecisa**.

§4.4 is Fourier-*component* ablation ("ablating all ~95% non-key Fourier
components together improves loss by ~70%") — a real finding, and
topically adjacent to "identify and remove memorizing components," but it
is not IPR-based, and P-0002's own phases (memorization → circuit formation
→ cleanup) are sequential, not "coexisting" — that framing, and the actual
IPR method, belong to P-0014 alone: "The inverse participation ratio (IPR)
of neuron weight spectra is bimodal in the coexistence phase... (Sec. 2.1,
Fig. 4)... Pruning the low-IPR neurons... (Sec. 2.1, Fig. 6)."

**Proposed fix:** drop P-0002 from this claim, or split it: `P-0014 §2,
§2.1 Fig 6` for coexistence + IPR pruning; if P-0002 is kept at all, cite it
separately for the analogous-but-different Fourier-ablation finding
(`P-0002 §4.4`) without folding it into the same sentence as "IPR."

**"Huecos identificados"**

**6. `H-e`, `P-0003 §5`** — "el epoch-wise double descent se estudia con
ruido de etiqueta" — **imprecisa**. Same §5-vs-§6 conflation as H-0005
finding #1, a second independent instance of the identical mistake.

**Proposed fix:** `P-0003 §6 (Fig 10)`.

**"Herramientas/benchmarks/datasets estándar"**

**7. `P-0006 §4.2` — "Grupos de permutaciones S₅/S₃"**
— **imprecisa**.

§4.2 is "decoder-only transformer on modular addition (p=53)" — no S₃
content. S₃ is explicitly in the appendix: "Permutation group S_3: phase
transition near r_c ~ 0.5... (Appendix H.2, Fig. 18)."

**Proposed fix:** `P-0006 §4.2 (addition); Appendix H.2 (S₃)`.

**8. `P-0010 §3.2, §5.1` — "StableMax/StCE, ⟂Grad/⟂AdamW, comparación
float32 vs float64"** — **imprecisa**. §3.2 (float32/float64) and §5.1
(⟂Grad/⟂AdamW) are both correct this time — but StableMax/StCE is missing
its location, §3.3, yet again. Fourth and last instance of the same P-0010
sub-section-mixing pattern (see headline finding #1).

**Proposed fix:** `P-0010 §3.2, §3.3, §5.1`.

---

## Honest assessment

**No — not reliable enough to trust as-is, and the failure mode is
specific enough to name.** Every error found is the same shape: a citation
that's *directionally* right (correct paper, correct general fact) but
*positionally* wrong (adjacent section, or — twice — a specific number or
claim quietly reattributed to a different paper than the one that actually
contains it). Nothing here is a fabrication out of nothing; everything
traces to a real paper and a real nearby fact. That's actually the most
telling part: the errors cluster exactly where a paper has several
similarly-shaped claims in neighboring subsections (P-0010's five
subsections on Softmax Collapse, P-0013's several results sections), and —
critically — **the correct citation for the same fact exists somewhere else
in the very same document in three of the cases above** (P-0010's full
correct locator in "Escuelas de pensamiento," P-0006's correct attribution
for the step-count figure in "Lo establecido," P-0013's correct `§9.3` in
"Papers ancla vs. de frontera"). That is direct evidence that citations are
being restated from memory/pattern-similarity to an earlier mention rather
than re-verified against the source text each time the same fact is written
down again.

Given that diagnosis, the structural fix should be exactly what the
question proposes: **the generating skill (`literature-search`,
`create-project`'s map-reduce synthesis, and `hypothesis-cycle`'s citation
step) should re-open the specific `Papers/P-XXXX.md` `## Texto completo`
section it's about to cite and confirm the claim is actually there,
as a mandatory step before accepting the citation — not trust
generation-time recall, even when that recall is "this paper said something
like this somewhere nearby."** A cheap, concrete version of this: before
writing `P-XXXX §N`, grep/read that exact `§N` heading's bullet content and
require the citing sentence to be a paraphrase of what's actually under it,
not of the paper as a whole. That would have caught every single issue
above — none of them survive a "does §N itself, read on its own, say this"
check, even though the paper as a whole does support most of the claims.

One caveat in the other direction, so this doesn't read as harsher than the
evidence supports: **93% of citations (170/184) are exacta**, and the one
invariant that most needed discipline — never inventing precision for the
two abstract-only papers — is at **100%**, with zero exceptions found
across ~13 real citations to P-0011/P-0012. The mechanism isn't broken in
principle; it's just not self-checking, and self-checking is exactly what's
missing.
