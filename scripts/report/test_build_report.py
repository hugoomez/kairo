"""Tests for build_report.py. Run: python -m pytest scripts/report

Invented project, ids and text only."""

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
import build_report  # noqa: E402

sys.path.insert(0, str(HERE.parent.parent / "skills" / "assemble-manuscript" / "scripts"))
import test_ai_disclosure  # noqa: E402

HUB = "---\nid: PROJ-950\nname: Proyecto inventado de informes\ntype: ciencia\nstatus: active\n---\n\n# Hub\n"


def hyp(hid: str, claim: str, extra: str = "") -> str:
    return (f"---\nid: {hid}\nproject: PROJ-950\nstatus: apoyada\nconfidence: 0.7  # kind: frequentist_heuristic\n"
            f"{extra}history:\n  - date: 2031-01-02\n    status: propuesta\n    by: tester\n"
            f"  - date: 2031-02-03\n    status: apoyada\n    by: tester\n---\n\n## Claim\n\n{claim}\n\n"
            "## Génesis\n\nTEXTO-DE-GENESIS-PRIVADO\n")


EXP = """---
id: E-0951
hypothesis: {h}
project: PROJ-950
role: confirmatory
tier: completo
analysis_plan: frequentist
status: completed
frozen_at: 2031-01-10T10:00:00Z
experiment_validity: valid
result:
  effect: 0.12 [0.05, 0.19]
  p_value: 0.003
  verdict: apoyada
---

## Predicción

El efecto inventado es positivo.

## Resultado

Se observó el efecto inventado.
"""


class TestBuildReport(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-report-t-"))
        self.vault = self.tmp / "vault"
        self.p = self.vault / "Projects" / "informes-demo"
        (self.p / "Hipotesis").mkdir(parents=True)
        (self.p / "Experimentos").mkdir()
        (self.vault / "Papers").mkdir()
        (self.p / "_hub.md").write_text(HUB, encoding="utf-8")
        (self.p / "Hipotesis" / "H-0951.md").write_text(hyp("H-0951", "La cota inventada vale en el caso ficticio."), encoding="utf-8")
        (self.p / "Hipotesis" / "H-0952.md").write_text(hyp("H-0952", "OTRA-HIPOTESIS-NO-ELEGIDA."), encoding="utf-8")
        (self.p / "Hipotesis" / "H-0953.md").write_text(
            hyp("H-0953", "SECRETO-DE-SEND-NEVER.", "send: never\ntitle: Tema reservado del laboratorio\n"), encoding="utf-8")
        (self.p / "Experimentos" / "E-0951.md").write_text(EXP.format(h="H-0951"), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, sel: dict, *extra: str, vault: Path | None = None, pdir: Path | None = None):
        f = self.tmp / "sel.json"
        f.write_text(json.dumps(sel), encoding="utf-8")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = build_report.main(["--vault", str(vault or self.vault), "--project-dir", str(pdir or self.p),
                                      "--selection", str(f), *extra])
        return code, json.loads(buf.getvalue())

    def written(self, res):
        return (self.vault / res["md"]).read_text(encoding="utf-8"), (self.vault / res["html"]).read_text(encoding="utf-8")

    def test_empty_selection_is_an_empty_report(self):
        code, res = self.run_cli({})
        self.assertEqual(code, 0, res)
        md, _ = self.written(res)
        self.assertNotRegex(md, r"[HE]-09\d\d")

    def test_only_what_was_selected(self):
        code, res = self.run_cli({"sections": ["estado", "hipotesis", "experimentos", "cronologia", "siguiente"],
                                  "hypotheses": ["H-0951"], "experiments": ["E-0951"],
                                  "next_step": "Replicar con otra semilla inventada."})
        self.assertEqual(code, 0, res)
        md, page = self.written(res)
        for s in ("La cota inventada vale", "E-0951", "0.003", "Se observó el efecto inventado",
                  "2031-02-03", "estado → apoyada", "Replicar con otra semilla", "Proyecto inventado de informes"):
            self.assertIn(s, md)
        for s in ("H-0952", "OTRA-HIPOTESIS", "H-0953", "SECRETO", "TEXTO-DE-GENESIS"):
            self.assertNotIn(s, md)
            self.assertNotIn(s, page)
        self.assertTrue(res["html"].endswith(".html"))
        self.assertIn("<table>", page)

    def test_an_unselected_section_is_absent(self):
        _, res = self.run_cli({"sections": ["hipotesis"], "hypotheses": ["H-0951"], "experiments": ["E-0951"]})
        md, _ = self.written(res)
        self.assertNotIn("E-0951", md)
        self.assertNotIn("Cronología", md)

    def test_send_never_is_never_included(self):
        code, res = self.run_cli({"sections": ["hipotesis"], "hypotheses": ["H-0953"]})
        self.assertEqual(code, 0)
        self.assertEqual(res["excluded"][0]["id"], "H-0953")
        md, _ = self.written(res)
        self.assertNotIn("SECRETO", md)

    def test_unselected_id_in_selected_text_blocks(self):
        (self.p / "Hipotesis" / "H-0951.md").write_text(hyp("H-0951", "Refina H-0952 en el caso ficticio."), encoding="utf-8")
        code, res = self.run_cli({"sections": ["hipotesis"], "hypotheses": ["H-0951"]})
        self.assertEqual(code, 3)
        self.assertEqual(res["blocking"][0]["kind"], "unselected_id")
        self.assertNotIn("md", res)
        self.assertFalse((self.p / "Informes").exists())

    def test_experiment_without_its_hypothesis(self):
        (self.p / "Experimentos" / "E-0951.md").write_text(EXP.format(h="H-0952"), encoding="utf-8")
        code, res = self.run_cli({"sections": ["experimentos"], "experiments": ["E-0951"]})
        self.assertEqual(code, 0, res)
        md, _ = self.written(res)
        self.assertIn("hipótesis no incluida", md)
        self.assertNotIn("H-0952", md)

    def test_scan_blocks_titles_secrets_and_vault_paths(self):
        cases = {
            "send_never": "Como en Tema reservado del laboratorio, vemos que…",
            "api_key": "clave " + "sk-" + "ant-" + "x7Kp2QvL9mZr4TbW8nYc3HdF6sJa1GeUo5RiNk0Xw",
            "vault_path_reference": "Ver Projects/informes-demo/_hub.md para más.",
        }
        for kind, intro in cases.items():
            code, res = self.run_cli({"intro": intro})
            self.assertEqual(code, 3, kind)
            self.assertIn(kind, [b["kind"] for b in res["blocking"]], kind)
        self.assertFalse((self.p / "Informes").exists())

    def test_dry_run_writes_nothing(self):
        code, res = self.run_cli({"sections": ["hipotesis"], "hypotheses": ["H-0951"]}, "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("La cota inventada", res["preview"])
        self.assertFalse((self.p / "Informes").exists())

    def test_html_escapes_and_second_report_gets_a_new_name(self):
        _, a = self.run_cli({"intro": "<script>alert(1)</script> **negrita**"})
        _, b = self.run_cli({})
        self.assertNotEqual(a["md"], b["md"])
        _, page = self.written(a)
        self.assertNotIn("<script>alert", page)
        self.assertIn("<strong>negrita</strong>", page)

    def test_bad_selection(self):
        code, res = self.run_cli({"sections": ["todo"]})
        self.assertEqual(code, 1)
        code, res = self.run_cli({"hypotheses": ["../x"]})
        self.assertEqual(code, 1)

    def test_disclosure_on_the_selected_hypotheses(self):
        vault = self.tmp / "v2"
        test_ai_disclosure.build_vault(vault)
        pdir = vault / "Projects" / test_ai_disclosure.SLUG
        hid = sorted(p.stem.split(" ")[0] for p in (pdir / "Hipotesis").glob("H-*.md"))[0]
        code, res = self.run_cli({"sections": ["divulgacion"], "hypotheses": [hid]}, "--dry-run",
                                 vault=vault, pdir=pdir)
        self.assertIn("Declaración de uso de IA", res["preview"])
        self.assertNotIn("Projects/", res["preview"])
        self.assertNotIn("unselected_id", [b["kind"] for b in res["blocking"]], res["blocking"])


if __name__ == "__main__":
    unittest.main()
