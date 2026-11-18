"""Tests for critique_note.py. Run: python -m pytest scripts/critique

Invented vault: PROJ-900 with H-9001 (citing P-9001), ADR-901."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import critique_note as cn  # noqa: E402

PAPER = """---
id: P-9001
title: Un paper inventado
projects: [PROJ-900]
---

## Resumen

Resumen inventado.

## Texto completo

### 3 Resultados

Los widgets azules giran un 5% más rápido en las dos condiciones medidas.
"""

HYP = """---
id: H-9001
project: PROJ-900
status: propuesta
---

## Claim

Los widgets azules siempre giran más rápido que los rojos.

## Justificación (evidencia citada)

- P-9001 §3 — los azules giran más rápido.

## Génesis

GENESISTEXT de dónde salió.

## Revisión del ciclo

CYCLETEXT lo que dijo el crítico.
"""

ADR = """---
id: ADR-901
status: accepted
---

## Contexto

Hay que elegir motor.

## Decisión

Usamos el motor azul.

## Críticas

PRIORCRITIQUE otra crítica.
"""

RESULT = {
    "critic": "kairo/devils-advocate@1.0.0",
    "target": "H-9001",
    "objections": [{"where": "Claim: «siempre»", "objection": "P-9001 §3 solo mide dos condiciones.",
                    "would_settle_it": "Medir una tercera condición.", "severity": "crítico"}],
    "alternative_explanations": ["Los azules son más ligeros."],
    "auxiliary_assumptions": ["Mismo motor en ambos."],
    "weakest_link": "La generalización a «siempre».",
    "embarrassing_result": "Un rojo más rápido en una tercera condición.",
    "cannot_assess": [],
}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-crit-"))
        self.vault = self.tmp / "vault"
        proj = self.vault / "Projects" / "demo"
        (proj / "Hipotesis").mkdir(parents=True)
        (proj / "Producto").mkdir()
        (self.vault / "Papers").mkdir()
        (proj / "_hub.md").write_text("---\nid: PROJ-900\n---\n", encoding="utf-8")
        (self.vault / "Papers" / "P-9001 inventado.md").write_text(PAPER, encoding="utf-8")
        (proj / "Hipotesis" / "H-9001 azules.md").write_text(HYP, encoding="utf-8")
        (proj / "Producto" / "ADR-901.md").write_text(ADR, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestPacket(Base):
    def test_hypothesis_packet_is_the_verifier_allow_list_with_cited_text(self):
        text = cn.build_packet(self.vault, "H-9001", None, None)
        self.assertTrue(text.startswith("# Paquete de crítica"))
        self.assertIn("siempre giran", text)
        self.assertIn("5% más rápido", text)
        for secret in ("GENESISTEXT", "CYCLETEXT", "propuesta"):
            self.assertNotIn(secret, text)

    def test_other_notes_drop_reasoning_sections_and_frontmatter(self):
        text = cn.build_packet(self.vault, "ADR-901", None, None)
        self.assertIn("Usamos el motor azul.", text)
        self.assertNotIn("PRIORCRITIQUE", text)
        self.assertNotIn("accepted", text)

    def test_decision_text_alone(self):
        f = self.tmp / "d.txt"
        f.write_text("Decido priorizar H-9001 porque es barata.", encoding="utf-8")
        text = cn.build_packet(self.vault, "decision", None, f)
        self.assertIn("priorizar H-9001", text)

    def test_refusals(self):
        hyp = next((self.vault / "Projects/demo/Hipotesis").glob("H-9001*"))
        hyp.write_text(HYP.replace("---\n", "---\nsend: never\n", 1), encoding="utf-8")
        with self.assertRaises(cn.Refused):
            cn.build_packet(self.vault, "H-9001", None, None)
        with self.assertRaises(cn.Refused):
            cn.build_packet(self.vault, "H-0404", None, None)
        with self.assertRaises(cn.Refused):
            cn.build_packet(self.vault, "../outside.md", None, None)


QUESTION = """---
id: Q-9001
project: PROJ-900
design: medicion
status: abierta
linked_plan:
units_dataset:
history: []
---

# Q-9001 — widgets

## Pregunta

¿Qué proporción de los widgets inventados gira al revés?

## Población y marco

Widgets del almacén inventado.

## Resultado

QRESULTTEXT

## Historia

QHISTORYTEXT
"""

PLAN = """---
id: M-9002
project: PROJ-900
question: Q-9001
status: borrador
frozen_at: 2026-01-01T00:00:00Z
frozen_commit: abcdef1234567
---

# M-9002 — Plan de medición de Q-9001

## Población y marco muestral

Todos los widgets del almacén, muestreo aleatorio simple.

## Estimandos

```json
{"estimands": [{"id": "est-1", "type": "proporcion", "column": "al_reves", "filter": {}, "cluster": null}]}
```

## Ética

Ningún widget sufre.

## Críticas

PRIORCRITIQUE otra crítica.
"""


class TestMeasurementPlan(Base):
    def setUp(self):
        super().setUp()
        proj = self.vault / "Projects" / "demo"
        (proj / "Preguntas").mkdir()
        (proj / "Mediciones").mkdir()
        (proj / "Preguntas" / "Q-9001.md").write_text(QUESTION, encoding="utf-8")
        (proj / "Mediciones" / "M-9002.md").write_text(PLAN, encoding="utf-8")

    def test_plan_packet_has_the_plan_and_its_question_only(self):
        text = cn.build_packet(self.vault, "M-9002", None, None)
        self.assertTrue(text.startswith("# Paquete de crítica"))
        for s in ("muestreo aleatorio simple", '"al_reves"', "Ningún widget sufre.",
                  "gira al revés", "Widgets del almacén inventado."):
            self.assertIn(s, text)
        for s in ("PRIORCRITIQUE", "abcdef1234567", "borrador", "QRESULTTEXT", "QHISTORYTEXT", "abierta"):
            self.assertNotIn(s, text)

    def test_plan_without_a_resolvable_question_is_still_critiqued(self):
        plan = self.vault / "Projects/demo/Mediciones/M-9002.md"
        plan.write_text(PLAN.replace("question: Q-9001", "question:"), encoding="utf-8")
        text = cn.build_packet(self.vault, "M-9002", None, None)
        self.assertIn("muestreo aleatorio simple", text)
        self.assertNotIn("gira al revés", text)

    def test_send_never_question_is_left_out(self):
        q = self.vault / "Projects/demo/Preguntas/Q-9001.md"
        q.write_text(QUESTION.replace("---\n", "---\nsend: never\n", 1), encoding="utf-8")
        text = cn.build_packet(self.vault, "M-9002", None, None)
        self.assertNotIn("gira al revés", text)

    def test_writes_the_critique_in_the_plans_project(self):
        p = self.tmp / "packet.md"
        p.write_text(cn.build_packet(self.vault, "M-9002", None, None), encoding="utf-8")
        out = cn.write_note(self.vault, "M-9002", p, RESULT, None)
        self.assertEqual(out.relative_to(self.vault).as_posix(), "Projects/demo/Criticas/CR-0001.md")
        self.assertIn("target: M-9002", out.read_text(encoding="utf-8"))


class TestWrite(Base):
    def packet(self):
        p = self.tmp / "packet.md"
        p.write_text(cn.build_packet(self.vault, "H-9001", None, None), encoding="utf-8")
        return p

    def test_writes_a_non_citable_note_with_every_objection(self):
        out = cn.write_note(self.vault, "H-9001", self.packet(), RESULT, "claude-test")
        self.assertEqual(out.relative_to(self.vault).as_posix(), "Projects/demo/Criticas/CR-0001.md")
        text = out.read_text(encoding="utf-8")
        for s in ("citable: false", "escrito_por: modelo", "target: H-9001", "**[crítico]**",
                  "*Dónde:* Claim: «siempre»", "Los azules son más ligeros.", "packet_sha256: "):
            self.assertIn(s, text)
        self.assertEqual(cn.write_note(self.vault, "H-9001", self.packet(), RESULT, None).stem, "CR-0002")

    def test_second_critic_off_is_recorded_as_no_disponible_not_an_error(self):
        from unittest import mock
        with mock.patch.dict("os.environ", {"KAIRO_SECOND_CRITIC": "", "DEEPINFRA_TOKEN": ""}):
            text = cn.write_note(self.vault, "H-9001", self.packet(), RESULT, None).read_text(encoding="utf-8")
        self.assertIn("second_critic: no disponible — desactivado", text)
        self.assertIn("Segundo crítico (otra familia de modelos, DeepInfra): no disponible", text)

    def test_old_scale_is_written_on_kairos_scale(self):
        old = {**RESULT, "objections": [{**RESULT["objections"][0], "severity": "alta"}]}
        text = cn.write_note(self.vault, "H-9001", self.packet(), old, None).read_text(encoding="utf-8")
        self.assertIn("**[crítico]**", text)
        self.assertNotIn("[alta]", text)

    def test_target_note_is_untouched(self):
        hyp = next((self.vault / "Projects/demo/Hipotesis").glob("H-9001*"))
        before = hyp.read_bytes()
        cn.write_note(self.vault, "H-9001", self.packet(), RESULT, None)
        self.assertEqual(hyp.read_bytes(), before)

    def test_invalid_results_are_refused(self):
        bad = [
            {**RESULT, "objections": []},
            {**RESULT, "objections": [{"where": "", "objection": "x", "severity": "crítico"}]},
            {**RESULT, "objections": [{"where": "x", "objection": "x", "severity": "grave"}]},
            {**RESULT, "verdict": "refutada"},
            {**RESULT, "alternative_explanations": "una"},
        ]
        for r in bad:
            with self.subTest(r=r), self.assertRaises(cn.Refused):
                cn.write_note(self.vault, "H-9001", self.packet(), r, None)

    def test_decision_needs_a_project(self):
        with self.assertRaises(cn.Refused):
            cn.write_note(self.vault, "decision", self.packet(), RESULT, None)
        out = cn.write_note(self.vault, "decision", self.packet(), RESULT, None, project="demo")
        self.assertTrue(out.exists())

    def cli_write(self, packet, *extra):
        import os
        from unittest import mock
        res = self.tmp / "r.md"
        res.write_text("Aquí va:\n```json\n" + json.dumps(RESULT, ensure_ascii=False) + "\n```\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"KAIRO_STATE_DIR": str(self.tmp / "state")}):
            return cn.main(["write", "--vault", str(self.vault), "--target", "H-9001",
                            "--packet", str(packet), "--result", str(res), *extra])

    def test_cli_parses_the_fenced_block(self):
        self.assertEqual(self.cli_write(self.packet(), "--allow-unread"), 0)

    def test_cli_write_needs_proof_the_critic_read_the_packet(self):
        import hashlib
        import os
        from unittest import mock
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "security"))
        import isolation
        packet = self.packet()
        self.assertEqual(self.cli_write(packet), 3)
        sha = hashlib.sha256(packet.read_bytes()).hexdigest()
        with mock.patch.dict(os.environ, {"KAIRO_STATE_DIR": str(self.tmp / "state")}):
            isolation.record_receipt({"sha256": sha, "agent_type": "devils-advocate", "agent_id": "d-1"})
        self.assertEqual(self.cli_write(packet), 0)
        note = next((self.vault / "Projects" / "demo" / "Criticas").glob("CR-*.md")).read_text(encoding="utf-8")
        self.assertIn("packet_read: true", note)


if __name__ == "__main__":
    unittest.main()
