"""Tests for next_steps.py. Run: python -m pytest scripts/ledger

Invented vault in a temp dir (vault_fixture.py)."""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import next_steps as ns  # noqa: E402
from vault_fixture import Vault, mapa_example  # noqa: E402


class NextSteps(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-next-"))
        self.v = Vault(self.tmp)
        self.p = self.v.project()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_json(self, project="PROJ-900"):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = ns.main(["--vault", str(self.tmp), "--project", project, "--json"])
        self.assertEqual(code, 0)
        return json.loads(out.getvalue())

    def kinds(self, r, kind):
        return [i for i in r["items"] if i["kind"] == kind]

    def test_completed_confirmatory_not_adjudicated(self):
        v, p = self.v, self.p
        v.hypothesis(p, "H-9001", "Claim inventado.", status="preregistrada", linked=["E-9001"], worth="si")
        v.experiment(p, "E-9001", "H-9001", verdict="apoyada")
        v.hypothesis(p, "H-9002", "Claim inventado dos.", status="en_experimento", linked=["E-9002"],
                     worth="si", history_exps=["E-9002"])
        v.experiment(p, "E-9002", "H-9002", verdict="apoyada")           # already in history
        v.hypothesis(p, "H-9003", "Claim tres.", status="en_experimento", worth="si")
        v.experiment(p, "E-9003", "H-9003", role="exploratory", rung=1)  # exploratory never
        v.experiment(p, "E-9004", "H-9003", validity="invalid")           # invalid never
        v.experiment(p, "E-9005", "H-9003", status="running")             # not completed
        r = self.run_json()
        items = self.kinds(r, "experimento_sin_adjudicar")
        self.assertEqual([i["refs"] for i in items], [["H-9001", "E-9001"]])
        self.assertEqual(items[0]["severity"], "importante")
        self.assertIn("update-confidence", items[0]["action"])

    def test_hub_placeholders(self):
        r = self.run_json()
        items = {i["target"]: i for i in self.kinds(r, "hub_sin_valor")}
        self.assertEqual(set(items), {"_hub.md:compute_budget", "_hub.md:completo_cost_threshold",
                                      "_hub.md:ladder_cost_threshold"})   # `<YYYY-MM-DD>` is not `<value>`
        self.assertTrue(all(i["severity"] == "importante" for i in items.values()))
        shutil.rmtree(self.p)
        self.p = self.v.project(budget="100 CPU-h")
        r = self.run_json()
        self.assertNotIn("_hub.md:compute_budget", {i["target"] for i in self.kinds(r, "hub_sin_valor")})

    def test_worth_fields(self):
        v, p = self.v, self.p
        v.hypothesis(p, "H-9001", "Sin evaluar.")
        v.hypothesis(p, "H-9002", "Evaluada sí.", worth="si")
        v.hypothesis(p, "H-9003", "Evaluada no.", worth="no")
        v.hypothesis(p, "H-9004", "Cerrada sin evaluar.", status="refutada")
        r = self.run_json()
        self.assertEqual([i["target"] for i in self.kinds(r, "hipotesis_sin_evaluar")], ["H-9001"])
        self.assertEqual([i["target"] for i in self.kinds(r, "hipotesis_no_merece")], ["H-9003"])

    def test_no_frontier_no_frontier_items(self):
        self.v.hypothesis(self.p, "H-9001", "x", cells=["M1:fila_a|col_y"], worth="si")
        r = self.run_json("proyecto-inventado")
        self.assertFalse(r["has_frontier"])
        self.assertEqual(self.kinds(r, "frontera_obsoleta") + self.kinds(r, "hueco_sin_hipotesis"), [])

    def test_frontier_stale_and_gaps(self):
        v, p = self.v, self.p
        v.hypothesis(p, "H-9001", "x", cells=["M1:fila_a|col_x"], worth="si")
        v.paper("P-9001", "PROJ-900", "2031-01-01")
        v.paper("P-9002", "PROJ-900", "2031-01-02")         # not in the map
        v.paper("P-9003", "PROJ-901", "2031-01-02")         # other project
        v.paper("P-9004", "PROJ-900", "2031-01-02")         # flagged as sin extraer
        v.mapa(p, mapa_example(papers=("P-9001",), sin_extraer=("P-9004",), marca_cubierta=True))
        r = self.run_json()
        msgs = [i for i in self.kinds(r, "frontera_obsoleta")]
        refs = [i["refs"] for i in msgs]
        self.assertIn(["P-9004"], refs)
        self.assertIn(["P-9002"], refs)
        self.assertTrue(any(i["target"] == "M1:fila_a|col_y" and "hueco" in i["message"] for i in msgs))
        self.assertTrue(any("nuestro" in i["message"] for i in msgs))   # H-9001 absent from the layer
        gaps = self.kinds(r, "hueco_sin_hipotesis")
        self.assertEqual([g["target"] for g in gaps], ["G1"])

    def test_gap_attacked_by_note_or_map(self):
        v, p = self.v, self.p
        v.hypothesis(p, "H-9001", "x", cells=["M1:fila_a|col_y"], worth="si")
        v.mapa(p, mapa_example())
        self.assertEqual(self.kinds(self.run_json(), "hueco_sin_hipotesis"), [])
        (p / "Hipotesis" / "H-9001.md").unlink()
        v.mapa(p, mapa_example(atacado_por=("H-9001",)))
        self.assertEqual(self.kinds(self.run_json(), "hueco_sin_hipotesis"), [])

    def test_gap_cells_as_objects_from_frontier_py(self):
        # frontier.py writes huecos[].celdas as [{celda, cubierta_por, marca}], not strings.
        v, p = self.v, self.p
        m = mapa_example()
        for g in m["huecos"]:
            g["celdas"] = [{"celda": c, "cubierta_por": [], "marca": "hueco"} for c in g["celdas"]]
        v.mapa(p, m)
        gaps = self.kinds(self.run_json(), "hueco_sin_hipotesis")
        self.assertEqual([g["target"] for g in gaps], ["G1"])
        self.assertEqual(gaps[0]["refs"], ["M1:fila_a|col_y"])
        v.hypothesis(p, "H-9001", "x", cells=["M1:fila_a|col_y"], worth="si")
        self.assertEqual(self.kinds(self.run_json(), "hueco_sin_hipotesis"), [])

    def test_layer_in_sync_and_newer_paper(self):
        v, p = self.v, self.p
        v.hypothesis(p, "H-9001", "x", cells=["M1:fila_a|col_x"], worth="si")
        v.paper("P-9001", "PROJ-900", "2031-05-01")
        v.mapa(p, mapa_example(nuestros=[{"id": "H-9001", "tipo": "hipotesis", "status": "propuesta"}],
                               atacado_por=("H-9001",)))
        r = self.run_json()
        msgs = self.kinds(r, "frontera_obsoleta")
        self.assertFalse(any("nuestro" in i["message"] for i in msgs))
        self.assertTrue(any(i["refs"] == ["P-9001"] and i["severity"] == "menor" for i in msgs))

    def test_yaml_newer_or_json_missing(self):
        mp = self.v.mapa(self.p, mapa_example())
        old = time.time() - 100
        os.utime(mp, (old, old))
        r = self.run_json()
        self.assertTrue(any("mapa.yaml se editó" in i["message"] for i in self.kinds(r, "frontera_obsoleta")))
        mp.unlink()
        r = self.run_json()
        self.assertTrue(any("no mapa.json" in i["message"] for i in self.kinds(r, "frontera_obsoleta")))

    def test_broken_mapa_json(self):
        f = self.p / "Frontera"
        f.mkdir()
        (f / "mapa.json").write_text("{no es json", encoding="utf-8")
        r = self.run_json()
        self.assertEqual(len(self.kinds(r, "frontera_obsoleta")), 1)

    def test_shape_and_order(self):
        self.v.hypothesis(self.p, "H-9001", "x")
        r = self.run_json()
        for k in ("tool", "project", "slug", "generated", "has_frontier", "counts", "items", "resumen"):
            self.assertIn(k, r)
        for i in r["items"]:
            self.assertEqual(set(i), {"kind", "severity", "target", "message", "action", "refs"})
        sev = [ns.SEVERITY_ORDER[i["severity"]] for i in r["items"]]
        self.assertEqual(sev, sorted(sev))

    def test_bad_project(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(ns.main(["--vault", str(self.tmp), "--project", "PROJ-999"]), 2)
            self.assertEqual(ns.main(["--vault", str(self.tmp / "nope"), "--project", "PROJ-900"]), 2)


if __name__ == "__main__":
    unittest.main()
