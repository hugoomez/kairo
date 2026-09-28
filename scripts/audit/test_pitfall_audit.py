"""Tests for pitfall_audit.py. Run: python -m unittest scripts/audit/test_pitfall_audit.py

Every id, number and sentence here is invented (E-0901 / H-0901, a toy
"xor-parity" style task); nothing is copied from a vault note. One synthetic case
per pitfall at least, plus a clean case that passes."""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "traces"))
import pitfall_audit as pa  # noqa: E402
import trace_index as ti  # noqa: E402

HYP = """---
id: H-0901
project: PROJ-901
status: en_experimento
linea_publicacion: false
needs_human_review: false   # invented comment kept on write
linked_experiment: [E-0901]
---

## Claim

En la tarea inventada de paridad de 12 bits, una red de 3 capas con fracción de
entrenamiento 0.6 alcanza 0.9 de accuracy de test con al menos 4 semillas.
"""

EXP = """---
id: E-0901
hypothesis: H-0901
project: PROJ-901
role: confirmatory
rung: 3
tier: ligero
analysis_plan: frequentist
status: completed
frozen_at: 2031-03-01T10:00:00Z
experiment_validity: valid
sanity_checks:
  baseline_reproduces: true
  loss_decreases: true
  no_data_leakage: true   # invented
  seed_controls_variance: true
---

## Predicción

La proporción de semillas que superan 0.9 de accuracy en la celda `frac = 0.6` es mayor que en `frac = 0.3`.

## Variables

- **Independientes:** fracción de entrenamiento `frac ∈ {0.3, 0.6}`.
- **Primarias (deciden el veredicto):** proporción de semillas con accuracy ≥ 0.9 en la celda `frac = 0.6`, brazo `adam`.
- **Secundarias / exploratorias:** la misma proporción en la celda `frac = 0.45`; curvas de pérdida.

## Diseño

4 semillas por celda, brazo `adam`, 3 capas.

## Plan de análisis

**Métrica primaria:** proporción de semillas con accuracy ≥ 0.9 en `frac = 0.6`; estimador: proporción muestral.
Test de dos proporciones, alpha 0.05, T_apoyo 0.25, T_refuta 0.05.

**Regla de parada:** 4 semillas fijas por celda; una corrida que no arranca por falta de cuota no se relanza.

## Umbral de invalidez

Solape de ids entre train y test.

## Resultado

Inventado.

## Enmiendas

### 2031-03-01 — detalle de logging

Frecuencia de logging fijada en 100 pasos (no toca el análisis).
"""

PLAN = {"experiment": "E-0901", "task": {"name": "parity_12bit", "bits": 12},
        "runs": [{"run_index": i, "run_seed": 500 + i, "frac": 0.6 if i < 4 else 0.3}
                 for i in range(8)]}

CLAIM_SETUP = {"task": {"value": "parity_12bit", "source": "H-0901 ## Claim"},
               "layers": {"value": 3, "source": "H-0901 ## Claim"},
               "seeds_per_cell": {"value": {"min": 4}, "source": "H-0901 ## Claim"}}
DESIGN_SETUP = {"layers": {"value": 3, "source": "E-0901 ## Diseño"},
                "seeds_per_cell": {"value": 4, "source": "E-0901 ## Diseño"}}
JUDGEMENT_OK = [{"check": "benchmark", "severity": "menor", "location": "E-0901 ## Diseño",
                 "message": "invented: the design is the claim's own setup", "by": "test"}]


def analysis_record(**over):
    rec = {"schema": "kairo/analysis@1", "experiment": "E-0901",
           "script": "two_proportion_test.py@1.0.0",
           "primary_metric": {"name": "prop_acc_ge_0.9", "cell": {"frac": 0.6, "arm": "adam"},
                              "estimator": "proporción", "thresholds": {"T_apoyo": 0.25, "T_refuta": 0.05},
                              "interval": {}},
           "runs_in_analysis": [f"r{i}" for i in range(8)],
           "cells": {"frac0.6": {"n_completed": 4}, "frac0.3": {"n_completed": 4}},
           "parameters_used": {"alpha": 0.05, "accuracy_cut": 0.9}}
    rec.update(over)
    return rec


def trace_entry(i, **over):
    e = {"event": "end", "run_id": f"r{i}", "experiment": "E-0901", "role": "confirmatory",
         "rung": 3, "attempt": 1, "retry_of": None, "seeds": [500 + i],
         "cell": "frac0.6" if i < 4 else "frac0.3", "plan_index": i,
         "config_hash": "a" * 64, "started_at": "2031-03-02T08:00:00Z",
         "ended_at": "2031-03-02T09:00:00Z", "outcome": "completed", "entered_analysis": True,
         "artifacts": [{"path": f"results/r{i}.json", "sha256": "b" * 64, "archived": True}],
         "provenance": {"written_by": "test", "backfilled": True, "sources": ["invented"]}}
    e.update(over)
    return e


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-pitfall-"))
        proj = self.tmp / "Projects" / "p"
        (proj / "Hipotesis").mkdir(parents=True)
        self.exp_dir = proj / "Experimentos"
        (self.exp_dir / "trazas").mkdir(parents=True)
        self.hyp = proj / "Hipotesis" / "H-0901 invented.md"
        self.hyp.write_text(HYP, encoding="utf-8", newline="\n")
        self.exp = self.exp_dir / "E-0901.md"
        self.exp.write_text(EXP, encoding="utf-8", newline="\n")
        self.trace = self.exp_dir / "trazas" / "index.jsonl"
        self.write_json(self.exp_dir / "E-0901.data.json", PLAN)
        self.write_json(self.exp_dir / "trazas" / "E-0901.analysis.json", analysis_record())
        self.claim = self.write_json(self.tmp / "claim.json", CLAIM_SETUP)
        self.design = self.write_json(self.tmp / "design.json", DESIGN_SETUP)
        self.judge = self.write_json(self.tmp / "judge.json", JUDGEMENT_OK)
        self.splits = self.write_json(self.tmp / "splits.json", {"runs": {
            f"r{i}": {"train": list(range(0, 50)), "test": list(range(50, 80))} for i in range(8)}})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_json(self, path, obj):
        path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        return path

    def fill_trace(self, overrides=None, skip=()):
        overrides = overrides or {}
        for i in range(8):
            if i in skip:
                continue
            ti.append(self.trace, trace_entry(i, **overrides.get(i, {})))

    def run_audit(self, **kw):
        args = dict(claim_setup_path=self.claim, design_setup_path=self.design,
                    splits_path=self.splits, judgement_path=self.judge)
        args.update(kw)
        return pa.audit(self.exp, self.hyp, **args)

    def kinds(self, rep, check, severity=None):
        return {f["kind"] for f in rep["checks"][check]["findings"]
                if severity is None or f["severity"] == severity}


class Clean(Base):
    def test_clean_case_passes_with_exit_0(self):
        self.fill_trace()
        rep = self.run_audit()
        for c in ("leakage", "metric", "selection"):
            self.assertEqual(rep["checks"][c]["findings"], [], (c, rep["checks"][c]))
        self.assertEqual(self.kinds(rep, "benchmark"), {"judgement"})   # the skill's own menor note
        self.assertFalse(rep["blocking"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = pa.main(["audit", "--experiment", str(self.exp), "--hypothesis", str(self.hyp),
                            "--claim-setup", str(self.claim), "--design-setup", str(self.design),
                            "--splits", str(self.splits), "--judgement", str(self.judge)])
        self.assertEqual(code, 0, buf.getvalue())
        self.assertIn("does not block", buf.getvalue())

    def test_preregistered_exclusion_is_not_a_finding(self):
        self.fill_trace({7: {"outcome": "not_launched", "entered_analysis": False,
                             "exclusion_reason": "quota ran out before launch",
                             "exclusion_rule_source": "E-0901.md ## Plan de análisis (Regla de parada)",
                             "started_at": None, "ended_at": None, "artifacts": []}})
        self.write_json(self.exp_dir / "trazas" / "E-0901.analysis.json",
                        analysis_record(runs_in_analysis=[f"r{i}" for i in range(7)],
                                        cells={"frac0.6": {"n_completed": 4}, "frac0.3": {"n_completed": 3}}))
        rep = self.run_audit()
        self.assertEqual(rep["checks"]["selection"]["findings"], [])
        self.assertFalse(rep["blocking"])


class Selection(Base):
    def test_dropped_seed_without_preregistered_reason_is_critico(self):
        self.fill_trace({2: {"entered_analysis": False, "exclusion_reason": "looked like an outlier"}})
        self.write_json(self.exp_dir / "trazas" / "E-0901.analysis.json",
                        analysis_record(runs_in_analysis=[f"r{i}" for i in range(8) if i != 2],
                                        cells={"frac0.6": {"n_completed": 3}, "frac0.3": {"n_completed": 4}}))
        rep = self.run_audit()
        self.assertIn("unexplained_exclusion", self.kinds(rep, "selection", "crítico"))
        self.assertTrue(rep["blocking"])

    def test_run_silently_missing_from_analysis_is_critico(self):
        self.fill_trace()
        self.write_json(self.exp_dir / "trazas" / "E-0901.analysis.json",
                        analysis_record(runs_in_analysis=[f"r{i}" for i in range(7)]))
        rep = self.run_audit()
        self.assertIn("dropped_from_analysis", self.kinds(rep, "selection", "crítico"))

    def test_cell_count_mismatch_is_critico(self):
        self.fill_trace()
        self.write_json(self.exp_dir / "trazas" / "E-0901.analysis.json",
                        analysis_record(runs_in_analysis=None,
                                        cells={"frac0.6": {"n_completed": 3}, "frac0.3": {"n_completed": 4}}))
        rep = self.run_audit()
        self.assertIn("cell_count_mismatch", self.kinds(rep, "selection", "crítico"))

    def test_planned_run_never_traced_is_critico(self):
        self.fill_trace(skip=(5,))
        self.write_json(self.exp_dir / "trazas" / "E-0901.analysis.json",
                        analysis_record(runs_in_analysis=[f"r{i}" for i in range(8) if i != 5],
                                        cells={"frac0.6": {"n_completed": 4}, "frac0.3": {"n_completed": 3}}))
        rep = self.run_audit()
        self.assertIn("planned_run_untraced", self.kinds(rep, "selection", "crítico"))

    def test_extra_seed_outside_plan_that_entered_is_critico(self):
        self.fill_trace()
        ti.append(self.trace, trace_entry(8, plan_index=None, seeds=[999]))
        rep = self.run_audit()
        self.assertIn("run_outside_plan", self.kinds(rep, "selection", "crítico"))

    def test_seed_differs_from_plan_is_critico(self):
        self.fill_trace({1: {"seeds": [42]}})
        rep = self.run_audit()
        self.assertIn("seed_mismatch", self.kinds(rep, "selection", "crítico"))

    def test_exclusion_rule_written_after_the_run_is_critico(self):
        self.fill_trace({3: {"entered_analysis": False, "exclusion_reason": "diverged",
                             "exclusion_rule_source": "E-0901.md ## Enmiendas 2031-03-05"}})
        rep = self.run_audit()
        self.assertIn("exclusion_rule_invalid", self.kinds(rep, "selection", "crítico"))

    def test_run_never_closed_is_critico(self):
        self.fill_trace(skip=(6,))
        ti.append(self.trace, ti.start_entry("E-0901", "r6", "confirmatory", "test", seeds=[506],
                                             cell="frac0.3", plan_index=6,
                                             started_at="2031-03-02T08:00:00Z"))
        rep = self.run_audit()
        self.assertIn("run_not_closed", self.kinds(rep, "selection", "crítico"))

    def test_missing_trace_is_critico(self):
        rep = self.run_audit()
        self.assertIn("trace_missing", self.kinds(rep, "selection", "crítico"))

    def test_tampered_trace_is_critico(self):
        self.fill_trace()
        lines = self.trace.read_text(encoding="utf-8").splitlines()
        lines[2] = lines[2].replace('"entered_analysis":true', '"entered_analysis":false')
        self.trace.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        rep = self.run_audit()
        self.assertIn("trace_broken", self.kinds(rep, "selection", "crítico"))

    def test_unarchived_outputs_are_importante(self):
        self.fill_trace({0: {"artifacts": [{"path": "remote/r0.json", "sha256": None, "archived": False}]}})
        rep = self.run_audit()
        self.assertIn("outputs_not_archived", self.kinds(rep, "selection", "importante"))
        self.assertFalse(rep["blocking"])


class Leakage(Base):
    def test_train_test_overlap_is_critico(self):
        self.fill_trace()
        self.write_json(self.splits, {"runs": {"r0": {"train": [1, 2, 3, 4], "test": [4, 5, 6]}}})
        rep = self.run_audit()
        self.assertIn("split_overlap", self.kinds(rep, "leakage", "crítico"))

    def test_threshold_chosen_on_test_data_after_the_fact_is_critico(self):
        self.fill_trace()
        self.write_json(self.exp_dir / "trazas" / "E-0901.analysis.json", analysis_record(
            parameters_used={"alpha": 0.05,
                             "stop_step": {"value": 1733, "chosen_on": "test",
                                           "chosen_at": "2031-03-03T12:00:00Z"}}))
        rep = self.run_audit()
        self.assertIn("param_fit_on_heldout", self.kinds(rep, "leakage", "crítico"))

    def test_threshold_absent_from_frozen_text_is_critico(self):
        self.fill_trace()
        self.write_json(self.exp_dir / "trazas" / "E-0901.analysis.json",
                        analysis_record(parameters_used={"r_star": 0.47}))
        rep = self.run_audit()
        self.assertIn("param_not_frozen", self.kinds(rep, "leakage", "crítico"))

    def test_failed_leakage_sanity_check_is_critico(self):
        self.fill_trace()
        self.exp.write_text(EXP.replace("no_data_leakage: true", "no_data_leakage: false"),
                            encoding="utf-8", newline="\n")
        rep = self.run_audit()
        self.assertIn("leakage_check_failed", self.kinds(rep, "leakage", "crítico"))

    def test_run_before_freeze_is_critico(self):
        self.fill_trace({0: {"started_at": "2031-02-27T08:00:00Z"}})
        rep = self.run_audit()
        self.assertIn("run_before_freeze", self.kinds(rep, "leakage", "crítico"))

    def test_same_day_backfill_is_importante_not_critico(self):
        self.fill_trace({i: {"started_at": "unknown", "date": "2031-03-01"} for i in range(8)})
        rep = self.run_audit()
        self.assertIn("run_freeze_order_unknown", self.kinds(rep, "leakage", "importante"))
        self.assertNotIn("run_before_freeze", self.kinds(rep, "leakage"))

    def test_analysis_amendment_after_runs_is_importante(self):
        self.fill_trace()
        self.exp.write_text(EXP + "\n### 2031-03-04 — cambio del umbral de análisis\n\nInventado.\n",
                            encoding="utf-8", newline="\n")
        rep = self.run_audit()
        self.assertIn("amendment_after_runs", self.kinds(rep, "leakage", "importante"))


class Metric(Base):
    def test_secondary_metric_reported_as_primary_is_critico(self):
        self.fill_trace()
        rec = analysis_record()
        rec["reported_metric"] = dict(rec["primary_metric"], cell={"frac": 0.45, "arm": "adam"})
        self.write_json(self.exp_dir / "trazas" / "E-0901.analysis.json", rec)
        rep = self.run_audit()
        kinds = self.kinds(rep, "metric", "crítico")
        self.assertIn("secondary_as_primary", kinds)
        self.assertIn("reported_not_primary", kinds)

    def test_primary_computed_with_other_estimator_is_critico(self):
        self.fill_trace()
        self.exp.write_text(EXP.replace("estimador: proporción muestral", "estimador: mediana"),
                            encoding="utf-8", newline="\n")
        rec = analysis_record()
        rec["primary_metric"]["estimator"] = "mean"
        self.write_json(self.exp_dir / "trazas" / "E-0901.analysis.json", rec)
        rep = self.run_audit()
        self.assertIn("estimator_mismatch", self.kinds(rep, "metric", "crítico"))

    def test_threshold_not_as_frozen_is_critico(self):
        self.fill_trace()
        rec = analysis_record()
        rec["primary_metric"]["thresholds"] = {"T_apoyo": 0.15, "T_refuta": 0.05}
        self.write_json(self.exp_dir / "trazas" / "E-0901.analysis.json", rec)
        rep = self.run_audit()
        self.assertIn("thresholds_not_frozen", self.kinds(rep, "metric", "crítico"))

    def test_wrong_script_for_analysis_plan_is_critico(self):
        self.fill_trace()
        self.write_json(self.exp_dir / "trazas" / "E-0901.analysis.json",
                        analysis_record(script="bayes_factor_proportions.py@1.0.0"))
        rep = self.run_audit()
        self.assertIn("wrong_analysis_script", self.kinds(rep, "metric", "crítico"))

    def test_reconstructed_record_blocks_only_a_live_transition(self):
        self.fill_trace()
        self.write_json(self.exp_dir / "trazas" / "E-0901.analysis.json",
                        analysis_record(reconstructed=True, source="invented"))
        self.assertIn("analysis_reconstructed", self.kinds(self.run_audit(), "metric", "menor"))
        rep = self.run_audit(apply=True)
        self.assertIn("analysis_reconstructed", self.kinds(rep, "metric", "crítico"))
        self.assertTrue(rep["needs_human_review_written"])

    def test_missing_analysis_record_is_critico(self):
        self.fill_trace()
        (self.exp_dir / "trazas" / "E-0901.analysis.json").unlink()
        rep = self.run_audit()
        self.assertIn("analysis_record_missing", self.kinds(rep, "metric", "crítico"))


class NumberReading(unittest.TestCase):
    def test_notations(self):
        text = "retardo ≥ 5×10³ pasos; 1.0×10⁵ pasos; 10 000 remuestreos; > ⅓ de las semillas"
        for v in (5000, 100000, 10000, 1 / 3):
            self.assertTrue(pa.has_number(text, v), v)
        self.assertFalse(pa.has_number(text, 4000))
        self.assertFalse(pa.has_number("E-0901 and P-0901", 901))


class Benchmark(Base):
    def test_claim_task_differs_from_tested_task_is_critico(self):
        self.fill_trace()
        plan = dict(PLAN, task={"name": "sorting_8_tokens"})
        self.write_json(self.exp_dir / "E-0901.data.json", plan)
        rep = self.run_audit()
        self.assertIn("setup_mismatch", self.kinds(rep, "benchmark", "crítico"))
        self.assertTrue(rep["blocking"])

    def test_too_few_seeds_for_the_claim_is_importante(self):
        self.fill_trace()
        self.write_json(self.design, dict(DESIGN_SETUP, seeds_per_cell={"value": 2, "source": "x"}))
        rep = self.run_audit()
        self.assertIn("setup_mismatch", self.kinds(rep, "benchmark", "importante"))

    def test_facet_the_design_does_not_state_is_importante(self):
        self.fill_trace()
        self.write_json(self.claim, dict(CLAIM_SETUP, precision={"value": {"includes": ["float64"]}}))
        rep = self.run_audit()
        self.assertIn("facet_not_in_design", self.kinds(rep, "benchmark", "importante"))

    def test_missing_judgement_is_never_silently_passed(self):
        self.fill_trace()
        rep = self.run_audit(judgement_path=None)
        self.assertIn("judgement_missing", self.kinds(rep, "benchmark", "importante"))

    def test_skill_judgement_critico_blocks(self):
        self.fill_trace()
        self.write_json(self.judge, [{"check": "benchmark", "severity": "crítico",
                                      "location": "E-0901 ## Diseño",
                                      "message": "invented: the arm cannot show the effect"}])
        rep = self.run_audit()
        self.assertTrue(rep["blocking"])


class Blocking(Base):
    def critico_setup(self):
        self.fill_trace({2: {"entered_analysis": False, "exclusion_reason": "invented"}})

    def test_dry_run_writes_nothing(self):
        self.critico_setup()
        before = self.hyp.read_bytes()
        rep = self.run_audit()
        self.assertTrue(rep["blocking"])
        self.assertFalse(rep["needs_human_review_written"])
        self.assertEqual(self.hyp.read_bytes(), before)

    def test_apply_sets_only_needs_human_review(self):
        self.critico_setup()
        before = self.hyp.read_text(encoding="utf-8").splitlines()
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = pa.main(["audit", "--experiment", str(self.exp), "--hypothesis", str(self.hyp),
                            "--claim-setup", str(self.claim), "--design-setup", str(self.design),
                            "--splits", str(self.splits), "--judgement", str(self.judge), "--apply"])
        self.assertEqual(code, 3)
        after = self.hyp.read_text(encoding="utf-8").splitlines()
        diff = [(a, b) for a, b in zip(before, after) if a != b]
        self.assertEqual(len(before), len(after))
        self.assertEqual(diff, [("needs_human_review: false   # invented comment kept on write",
                                 "needs_human_review: true   # invented comment kept on write")])
        self.assertIn("status: en_experimento", after)

    def test_apply_without_critico_writes_nothing(self):
        self.fill_trace()
        before = self.hyp.read_bytes()
        rep = self.run_audit(apply=True)
        self.assertFalse(rep["blocking"])
        self.assertEqual(self.hyp.read_bytes(), before)


class FlagHelper(Base):
    def test_inserts_when_absent_and_keeps_crlf(self):
        text = HYP.replace("needs_human_review: false   # invented comment kept on write\n", "")
        self.hyp.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
        self.assertTrue(pa.set_needs_human_review(self.hyp))
        raw = self.hyp.read_bytes()
        self.assertIn(b"needs_human_review: true\r\n---\r\n", raw)
        self.assertNotIn(b"\n\n\n", raw.replace(b"\r", b""))
        self.assertEqual(raw.replace(b"needs_human_review: true\r\n", b""), text.replace("\n", "\r\n").encode())

    def test_idempotent(self):
        self.assertTrue(pa.set_needs_human_review(self.hyp))
        before = self.hyp.read_bytes()
        self.assertFalse(pa.set_needs_human_review(self.hyp))
        self.assertEqual(self.hyp.read_bytes(), before)

    def test_cli_flag_review(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(pa.main(["flag-review", "--hypothesis", str(self.hyp)]), 0)
        self.assertIn("needs_human_review: true", self.hyp.read_text(encoding="utf-8"))

    def test_error_exit_1(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = pa.main(["audit", "--experiment", str(self.tmp / "nope.md"),
                            "--hypothesis", str(self.hyp)])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
