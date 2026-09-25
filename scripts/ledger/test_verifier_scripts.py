"""Unit tests for verifier_packet.py and verifications.py (synthetic fixtures)."""

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import verifications as vf
import verifier_packet as vp

PAPER = """---
id: P-0901
title: A synthetic paper on toy dynamics
---

## Referencia

Doe, J. (2030). *Synthetic*. arXiv:0000.00000.

## Resumen

We study toy dynamics and report an interventional speedup.

## Texto completo

### 3. Experiments

#### 3.1 Lead time
- The precursor rises before the transition; lead time follows a power law (Fig 2).

#### 3.2 Interventions
- Boosting the precursor speeds up the transition by ~30% (Table 1).

### Appendices

Appendix paragraph. Sharpness anti-correlates with accuracy (App. B.2, Fig 7).
"""

HYP = """---
id: H-0901
project: PROJ-900
status: propuesta
confidence: 0.4  # kind: frequentist_heuristic
generated_by:
  origin: agent
  model: synthetic-model
needs_human_review: false
history:
  - date: 2030-01-01
    status: propuesta
    by: tester
---

## Claim

The precursor predicts the transition with AUROC >= 0.9.

## Justificación (evidencia citada)

- P-0901 §3.1 — the precursor rises before the transition with a power-law
  lead time; P-0901 §3.2 — boosting it speeds the transition up.
- P-0901 App B.2 Fig 7 — sharpness anti-correlates with accuracy.
- Hueco §H-x del Estado del arte — nobody compared them head to head.

## Hipótesis rival descartada

RIVALTEXT the validation curve alone is as good.

## Revisión del ciclo

SECRETCRITIQUE round 1: the critic said the claim was too broad.

## Lección

LESSONTEXT pending.

## Verificación independiente

PRIORVERIFICATION errors were found before.
"""

EXP = """---
id: E-0901
hypothesis: H-0901
tier: ligero
analysis_plan: frequentist
status: completed
needs_human_review: false
cost_estimated: 3 GPU-h
result:
  effect: 0.02
  p_value: 0.4
  verdict: apoyada
---

## Predicción

Treatment beats control by at least T_apoyo = 0.10.

## Plan de análisis

two_proportion_test.py, alpha 0.05, min-effect 0.10.

## Manifiesto de entorno

MANIFESTTEXT seeds and hashes.

## Resultado

risk difference +0.02, p = 0.4; verdict apoyada.

## Enmiendas

### 2030-02-01 — changed logging cadence
"""


def send_never(text: str) -> str:
    """The same note with `send: never` as its first frontmatter line (A3)."""
    return text.replace("---\n", "---\nsend: never\n", 1)


def build_fixture(root: Path) -> dict:
    vault = root / "vault"
    (vault / "Papers").mkdir(parents=True)
    (vault / "Papers" / "P-0901 Doe 2030 Synthetic.md").write_text(PAPER, encoding="utf-8")
    hyp = root / "H-0901.md"
    hyp.write_text(HYP, encoding="utf-8")
    exp = root / "E-0901.md"
    exp.write_text(EXP, encoding="utf-8")
    out = root / "analysis.txt"
    out.write_text("RESULT_JSON: {\"p_value\": 0.4}\n", encoding="utf-8")
    return {"vault": vault, "hyp": hyp, "exp": exp, "out": out}


class TestPacket(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.f = build_fixture(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, **kw):
        return vp.build(str(self.f["vault"]), str(self.f["hyp"]),
                        kw.get("experiments", []), kw.get("outputs", []),
                        kw.get("section"))

    def test_excluded_sections_never_in_packet(self):
        packet, m = self.build(experiments=[str(self.f["exp"])],
                               outputs=[str(self.f["out"])])
        for secret in ("SECRETCRITIQUE", "RIVALTEXT", "LESSONTEXT",
                       "PRIORVERIFICATION", "MANIFESTTEXT", "frequentist_heuristic",
                       "synthetic-model", "needs_human_review", "propuesta"):
            self.assertNotIn(secret, packet, secret)
        src = m["sources"][0]
        self.assertEqual(src["included_sections"],
                         ["Claim", "Justificación (evidencia citada)"])
        for h in ("Revisión del ciclo", "Hipótesis rival descartada", "Lección",
                  "Verificación independiente"):
            self.assertIn(h, src["excluded_sections"])
        self.assertIn("history", src["excluded_frontmatter"])
        self.assertEqual(src["included_frontmatter"], ["id"])

    def test_send_never_note_or_experiment_refused(self):
        for key in ("hyp", "exp"):
            t = self.f[key].read_text(encoding="utf-8")
            self.f[key].write_text(send_never(t), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "send: never"):
                self.build(experiments=[str(self.f["exp"])])
            self.f[key].write_text(t, encoding="utf-8")

    def test_send_never_paper_sends_no_source_text(self):
        paper = next((self.f["vault"] / "Papers").glob("P-0901*.md"))
        paper.write_text(send_never(PAPER), encoding="utf-8")
        packet, m = self.build()
        for leaked in ("power law", "interventional speedup", "Appendix paragraph"):
            self.assertNotIn(leaked, packet, leaked)
        self.assertTrue(all("send: never" in (c["note"] or "") for c in m["citations"]
                            if c["paper"] == "P-0901"))
        self.assertNotIn("paper_abstracts", m)

    def test_model_written_source_text_is_flagged(self):
        # the P-0011 case: a "summary from general knowledge" standing in for the abstract
        paper = next((self.f["vault"] / "Papers").glob("P-0901*.md"))
        paper.write_text(PAPER.replace(
            "We study toy dynamics",
            "No abstract was returned. Summary from general knowledge: we study toy dynamics"),
            encoding="utf-8")
        packet, m = self.build()
        self.assertIn("ATENCIÓN — procedencia", packet)
        self.assertEqual(m["abstract_provenance"]["P-0901"],
                         ["summary from", "general knowledge"])

    def test_abstract_only_note_with_texto_completo_is_flagged(self):
        # bullets under ## Texto completo of an abstract-only note can't be paper text
        paper = next((self.f["vault"] / "Papers").glob("P-0901*.md"))
        paper.write_text(PAPER.replace("title: A synthetic", "fulltext: abstract-only\ntitle: A synthetic"),
                         encoding="utf-8")
        packet, m = self.build()
        flagged = [c for c in m["citations"] if c["provenance"]]
        self.assertTrue(flagged)
        self.assertIn("abstract-only", flagged[0]["provenance"][0])

    def test_subsection_packed_mid_bullet_is_matched(self):
        # found on the real vault: P-0010 packs "3.1 … 3.2 … 3.3 …" into one bullet
        unit = {"text": "- 3.1 SC = absorption errors. 3.2 Across train splits generalization "
                        "stalls when SC begins. 3.3 Preventing SC yields grokking.", "num": "3.1"}
        for v in ("3.1", "3.2", "3.3"):
            self.assertTrue(vp.unit_matches(unit, ("sec", v)), v)
        self.assertFalse(vp.unit_matches(unit, ("sec", "3.4")))
        # plain numbers inside prose are not section markers
        prose = {"text": "- learning rate 0.0001. 4 layers; loss 3.2 at step 10.", "num": None}
        self.assertFalse(vp.unit_matches(prose, ("sec", "3.2")))
        self.assertFalse(vp.unit_matches(prose, ("sec", "0.0001")))

    def test_real_source_text_is_not_flagged(self):
        packet, m = self.build()
        self.assertNotIn("ATENCIÓN — procedencia", packet)
        self.assertFalse(any(c["provenance"] for c in m["citations"]))

    def test_citation_source_text_is_included_per_locator(self):
        packet, m = self.build()
        self.assertIn("lead time follows a power law (Fig 2)", packet)
        self.assertIn("speeds up the transition by ~30% (Table 1)", packet)
        self.assertIn("Sharpness anti-correlates with accuracy (App. B.2, Fig 7)", packet)
        # the cited paper's abstract is present once, labelled as non-locator text
        self.assertEqual(packet.count("We study toy dynamics"), 1)
        self.assertIn("NO es el texto de ningún localizador", packet)
        self.assertEqual(m["paper_abstracts"], ["P-0901"])
        cits = {(c["paper"], c["locator"]): c for c in m["citations"]}
        self.assertEqual(cits[("P-0901", "§3.1")]["matched_units"], 1)
        self.assertEqual(cits[("P-0901", "§3.2")]["matched_units"], 1)

    def test_wrong_locator_does_not_pull_the_right_text(self):
        hyp = self.f["hyp"]
        hyp.write_text(HYP.replace("P-0901 §3.2 — boosting", "and boosting")
                       , encoding="utf-8")
        packet, m = self.build()
        # only §3.1 is cited now: the intervention text must NOT be shown
        self.assertNotIn("speeds up the transition by ~30%", packet)

    def test_experiment_allow_list_and_labelled_amendments(self):
        packet, m = self.build(experiments=[str(self.f["exp"])],
                               outputs=[str(self.f["out"])])
        self.assertIn("T_apoyo = 0.10", packet)
        self.assertIn("verdict: apoyada", packet)
        self.assertIn("Enmiendas (registro posterior al freeze", packet)
        self.assertIn('RESULT_JSON: {"p_value": 0.4}', packet)
        exp = m["sources"][1]
        self.assertIn("needs_human_review", exp["excluded_frontmatter"])
        self.assertIn("status", exp["excluded_frontmatter"])
        self.assertIn("result", exp["included_frontmatter"])
        self.assertIn("Manifiesto de entorno", exp["excluded_sections"])

    def test_sha256_matches_packet_bytes_and_is_deterministic(self):
        p1, m1 = self.build()
        p2, m2 = self.build()
        self.assertEqual(p1, p2)
        self.assertEqual(m1["sha256"], hashlib.sha256(p1.encode("utf-8")).hexdigest())

    def test_section_scope(self):
        packet, m = self.build(section="Claim")
        self.assertIn("AUROC >= 0.9", packet)
        self.assertNotIn("power law", packet)
        with self.assertRaises(ValueError):
            self.build(section="Nope")
        # review finding: --section must not reach excluded, critique-bearing sections
        for h in ("Revisión del ciclo", "Hipótesis rival descartada"):
            with self.assertRaises(ValueError):
                self.build(section=h)

    def test_cli_writes_packet_and_manifest(self):
        out = Path(self.tmp.name) / "p.md"
        man = Path(self.tmp.name) / "m.json"
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            rc = vp.main(["--vault", str(self.f["vault"]), "--note", str(self.f["hyp"]),
                          "--out", str(out), "--manifest", str(man)])
        self.assertEqual(rc, 0)
        sha = buf.getvalue().strip()
        self.assertEqual(sha, hashlib.sha256(out.read_bytes()).hexdigest())
        self.assertEqual(json.loads(man.read_text(encoding="utf-8"))["sha256"], sha)
        self.assertIn("excluded sections", err.getvalue())

    def test_locator_parsing(self):
        self.assertEqual(vp.locator_tokens("§5.1–§5.2"), [("sec", "5.1"), ("sec", "5.2")])
        self.assertEqual(vp.locator_tokens("§5.4, §9.2–§9.3"),
                         [("sec", "5.4"), ("sec", "9.2"), ("sec", "9.3")])
        self.assertEqual(vp.locator_tokens("App A.5 Fig 7"), [("app", "A.5"), ("fig", "7")])
        self.assertEqual(vp.locator_tokens("§Resumen"), [("named", "Resumen")])
        refs = vp.citation_refs("- P-0007 §4.1, §5.2 y P-0006 §3.2 — texto; "
                                "los experimentos de P-0007 son otros")
        self.assertEqual(refs, [("P-0007", "§4.1, §5.2"), ("P-0006", "§3.2")])


def write(path: Path, text: str, crlf=False):
    data = text.replace("\n", "\r\n") if crlf else text
    path.write_bytes(data.encode("utf-8"))


NOTE_NO_KEY = """---
id: H-0902
status: propuesta
needs_human_review: false  # cleared once a human looked
history:
  - date: 2030-01-01
    status: propuesta
---

## Claim

X.

## Resultados

Y.
"""

REPORT = """Some preamble.

```json
{"verdict": "errors_found", "scope": "note", "model": "claude-opus-5-5",
 "findings": [{"severity": "crítico", "location": "Justificación, afirmación 1",
               "why": "the cited section does not contain the claim"}],
 "cannot_assess_reason": null}
```
"""


class TestVerifications(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def fm(self, path):
        text = path.read_text(encoding="utf-8")
        return text.split("---", 2)[1]

    def do_append(self, note, verdict="no_errors_found", scope="note", **kw):
        return vf.append(str(note), "kairo/fresh-verifier@1.0.0", "claude-opus-5-5",
                         verdict, scope, kw.get("date", "2030-03-01"),
                         kw.get("report"), kw.get("sha"), kw.get("flag", False))

    def test_absent_key_is_created_and_rest_untouched(self):
        n = self.d / "n.md"
        write(n, NOTE_NO_KEY)
        before = n.read_bytes()
        self.do_append(n)
        after = n.read_bytes()
        added = (b"verifications:\n  - verifier: kairo/fresh-verifier@1.0.0\n"
                 b"    model: claude-opus-5-5\n    date: 2030-03-01\n"
                 b"    verdict: no_errors_found\n    scope: note\n")
        self.assertEqual(after, before.replace(b"---\n\n## Claim", added + b"---\n\n## Claim"))

    def test_empty_list_form(self):
        n = self.d / "n.md"
        write(n, NOTE_NO_KEY.replace("history:", "verifications: []\nhistory:"))
        self.do_append(n, "errors_found")
        _, lines, _, lo, hi, entries = vf.load(str(n))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["verdict"], "errors_found")
        self.assertIn("history:\n  - date: 2030-01-01", n.read_text(encoding="utf-8"))

    def test_existing_list_appends_and_never_edits(self):
        n = self.d / "n.md"
        write(n, NOTE_NO_KEY)
        self.do_append(n, "errors_found")
        first = n.read_text(encoding="utf-8")
        self.do_append(n, "no_errors_found", date="2030-03-02")
        second = n.read_text(encoding="utf-8")
        block1 = first.split("verifications:\n")[1].split("---")[0].split("verification_reviewed")[0]
        self.assertIn(block1, second)  # old entry byte-identical
        entries = vf.load(str(n))[5]
        self.assertEqual([e["verdict"] for e in entries],
                         ["errors_found", "no_errors_found"])
        self.assertEqual(vf.latest(entries, "note")["date"], "2030-03-02")
        self.assertEqual(set(entries[0]), set(vf.KEYS))

    def test_invalid_verdict_rejected(self):
        n = self.d / "n.md"
        write(n, NOTE_NO_KEY)
        before = n.read_bytes()
        with self.assertRaises(vf.InputError):
            self.do_append(n, "correct")
        self.assertEqual(n.read_bytes(), before)

    def test_bad_section_rejected_good_section_accepted(self):
        n = self.d / "n.md"
        write(n, NOTE_NO_KEY)
        with self.assertRaises(vf.InputError):
            self.do_append(n, scope="section:Resultado")
        self.do_append(n, scope="section:Resultados")
        entries = vf.load(str(n))[5]
        self.assertEqual(entries[-1]["scope"], "section:Resultados")
        self.assertIsNone(vf.latest(entries, "note"))

    def test_section_heading_with_colon_round_trips(self):
        n = self.d / "n.md"
        write(n, NOTE_NO_KEY + "\n## Método: diseño\n\nZ.\n")
        self.do_append(n, scope="section:Método: diseño")
        self.assertEqual(vf.load(str(n))[5][-1]["scope"], "section:Método: diseño")

    def test_crlf_preserved(self):
        n = self.d / "n.md"
        write(n, NOTE_NO_KEY, crlf=True)
        self.do_append(n)
        raw = n.read_bytes()
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        self.assertEqual(vf.load(str(n))[5][0]["scope"], "note")

    def test_report_writes_body_section_and_flag(self):
        n = self.d / "n.md"
        write(n, NOTE_NO_KEY)
        rep = self.d / "rep.txt"
        rep.write_text(REPORT, encoding="utf-8")
        self.do_append(n, "errors_found", report=str(rep), sha="ab" * 32, flag=True)
        text = n.read_text(encoding="utf-8")
        self.assertIn("needs_human_review: true  # cleared once a human looked", text)
        self.assertIn("## Verificación independiente", text)
        self.assertIn("**crítico** — Justificación, afirmación 1", text)
        self.assertIn("paquete sha256: " + "ab" * 32, text)
        # a second run appends inside the same section
        self.do_append(n, "errors_found", report=str(rep), date="2030-03-05")
        text2 = n.read_text(encoding="utf-8")
        self.assertEqual(text2.count("## Verificación independiente"), 1)
        self.assertTrue(text2.index("2030-03-01 —") < text2.index("2030-03-05 —"))
        # gate blocks while needs_human_review is still true
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(vf.main(["gate", "--note", str(n)]), 3)
        # clearing needs_human_review does NOT clear the verifier's findings
        text2 = text2.replace("needs_human_review: true", "needs_human_review: false")
        n.write_text(text2, encoding="utf-8")
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(vf.main(["gate", "--note", str(n)]), 3)
        self.assertIn("verification_reviewed", json.loads(buf.getvalue())["reasons"][0])
        # only reviewing the findings themselves does
        n.write_text(text2.replace("verification_reviewed: false", "verification_reviewed: true"),
                     encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(vf.main(["gate", "--note", str(n)]), 0)

    def test_new_bad_entry_resets_a_previous_review(self):
        n = self.d / "n.md"
        write(n, NOTE_NO_KEY)
        self.do_append(n, "errors_found")
        n.write_text(n.read_text(encoding="utf-8").replace(
            "verification_reviewed: false", "verification_reviewed: true"), encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(vf.main(["gate", "--note", str(n)]), 0)
        self.do_append(n, "errors_found", date="2030-03-07")           # new findings
        self.assertIn("verification_reviewed: false", n.read_text(encoding="utf-8"))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(vf.main(["gate", "--note", str(n)]), 3)

    def test_clean_append_does_not_touch_review_fields(self):
        n = self.d / "n.md"
        write(n, NOTE_NO_KEY)
        self.do_append(n, "no_errors_found")
        text = n.read_text(encoding="utf-8")
        self.assertNotIn("verification_reviewed", text)
        self.assertIn("needs_human_review: false", text)

    def test_report_verdict_mismatch_rejected(self):
        n = self.d / "n.md"
        write(n, NOTE_NO_KEY)
        rep = self.d / "rep.txt"
        rep.write_text(REPORT, encoding="utf-8")
        with self.assertRaises(vf.InputError):
            self.do_append(n, "no_errors_found", report=str(rep))

    def test_reverification_clears_gate(self):
        n = self.d / "n.md"
        write(n, NOTE_NO_KEY)
        self.do_append(n, "cannot_assess")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(vf.main(["gate", "--note", str(n)]), 3)
        self.do_append(n, "no_errors_found", date="2030-03-09")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(vf.main(["gate", "--note", str(n)]), 0)

    def test_pending_human_review_blocks_even_after_clean_reverification(self):
        # preregistration requires BOTH: verifier findings and other review causes
        n = self.d / "n.md"
        write(n, NOTE_NO_KEY.replace("needs_human_review: false", "needs_human_review: true"))
        self.do_append(n, "no_errors_found")
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(vf.main(["gate", "--note", str(n)]), 3)
        self.assertIn("needs_human_review", json.loads(buf.getvalue())["reasons"][0])

    def test_gate_blocks_out_of_enum_verdict(self):
        # append refuses it, so it only arises from a hand edit; never read as clean
        n = self.d / "n.md"
        write(n, NOTE_NO_KEY.replace("needs_human_review: false", "needs_human_review: true"))
        self.do_append(n, "no_errors_found")
        n.write_text(n.read_text(encoding="utf-8").replace(
            "verdict: no_errors_found", "verdict: looks_fine"), encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(vf.main(["gate", "--note", str(n)]), 3)

    def test_send_never_note_gets_no_entry(self):
        n = self.d / "n.md"
        write(n, send_never(NOTE_NO_KEY))
        before = n.read_bytes()
        with self.assertRaisesRegex(vf.InputError, "send: never"):
            self.do_append(n)
        self.assertEqual(n.read_bytes(), before)

    def test_latest_cli_idempotent_read_back(self):
        n = self.d / "n.md"
        write(n, NOTE_NO_KEY)
        buf = io.StringIO()
        with redirect_stdout(buf):
            vf.main(["latest", "--note", str(n)])
        self.assertEqual(buf.getvalue().strip(), "null")
        self.do_append(n, "errors_found")
        before = n.read_bytes()
        outs = []
        for _ in range(2):
            buf = io.StringIO()
            with redirect_stdout(buf):
                vf.main(["latest", "--note", str(n)])
            outs.append(json.loads(buf.getvalue()))
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[0]["verdict"], "errors_found")
        self.assertEqual(n.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
