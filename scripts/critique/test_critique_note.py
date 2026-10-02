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
                    "would_settle_it": "Medir una tercera condición.", "severity": "alta"}],
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


class TestWrite(Base):
    def packet(self):
        p = self.tmp / "packet.md"
        p.write_text(cn.build_packet(self.vault, "H-9001", None, None), encoding="utf-8")
        return p

    def test_writes_a_non_citable_note_with_every_objection(self):
        out = cn.write_note(self.vault, "H-9001", self.packet(), RESULT, "claude-test")
        self.assertEqual(out.relative_to(self.vault).as_posix(), "Projects/demo/Criticas/CR-0001.md")
        text = out.read_text(encoding="utf-8")
        for s in ("citable: false", "escrito_por: modelo", "target: H-9001", "**[alta]**",
                  "*Dónde:* Claim: «siempre»", "Los azules son más ligeros.", "packet_sha256: "):
            self.assertIn(s, text)
        self.assertEqual(cn.write_note(self.vault, "H-9001", self.packet(), RESULT, None).stem, "CR-0002")

    def test_second_critic_off_is_recorded_as_no_disponible_not_an_error(self):
        from unittest import mock
        with mock.patch.dict("os.environ", {"KAIRO_SECOND_CRITIC": "", "DEEPINFRA_TOKEN": ""}):
            text = cn.write_note(self.vault, "H-9001", self.packet(), RESULT, None).read_text(encoding="utf-8")
        self.assertIn("second_critic: no disponible — desactivado", text)
        self.assertIn("Segundo crítico (otra familia de modelos, DeepInfra): no disponible", text)

    def test_target_note_is_untouched(self):
        hyp = next((self.vault / "Projects/demo/Hipotesis").glob("H-9001*"))
        before = hyp.read_bytes()
        cn.write_note(self.vault, "H-9001", self.packet(), RESULT, None)
        self.assertEqual(hyp.read_bytes(), before)

    def test_invalid_results_are_refused(self):
        bad = [
            {**RESULT, "objections": []},
            {**RESULT, "objections": [{"where": "", "objection": "x", "severity": "alta"}]},
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

    def test_cli_parses_the_fenced_block(self):
        res = self.tmp / "r.md"
        res.write_text("Aquí va:\n```json\n" + json.dumps(RESULT, ensure_ascii=False) + "\n```\n", encoding="utf-8")
        code = cn.main(["write", "--vault", str(self.vault), "--target", "H-9001",
                        "--packet", str(self.packet()), "--result", str(res)])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
