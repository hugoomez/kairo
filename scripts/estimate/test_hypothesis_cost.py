"""Tests for hypothesis_cost.py. Run: python -m pytest scripts/estimate

Invented vault in a temp dir (scripts/ledger/vault_fixture.py) with an invented
trace index written through trace_index.py."""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "ledger"))
sys.path.insert(0, str(HERE.parent / "traces"))
import hypothesis_cost as hc  # noqa: E402
import trace_index as ti  # noqa: E402
from vault_fixture import Vault  # noqa: E402


def run_entry(eid, rid, start, end, seeds, cell="a", **kw):
    e = {"event": "end", "run_id": rid, "experiment": eid, "role": "confirmatory", "rung": None,
         "attempt": 1, "retry_of": None, "seeds": seeds, "cell": cell, "plan_index": 0,
         "config_hash": "unknown", "started_at": start, "ended_at": end, "outcome": "completed",
         "entered_analysis": True,
         "provenance": {"written_by": "test", "backfilled": True, "sources": ["invented"]}}
    e.update(kw)
    return e


class HypothesisCost(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-cost-"))
        self.v = Vault(self.tmp)
        self.p = self.v.project(budget="200 CPU-h")
        v, p = self.v, self.p
        v.hypothesis(p, "H-9001", "El solver inventado converge antes con el regulador sintético.",
                     cells=["M1:fila_a|col_x"])
        v.hypothesis(p, "H-9002", "El solver inventado converge antes con el regulador sintético grande.",
                     cells=["M1:fila_a|col_x"], status="apoyada")
        v.hypothesis(p, "H-9003", "Tortugas y paraguas.", status="refutada")
        v.experiment(p, "E-9001", "H-9002", metric="continuous", hardware="CPU 8 núcleos")
        v.experiment(p, "E-9002", "H-9003", metric="proportion", cost_act="0.5 CPU-h",
                     resultado="estimado 1 CPU-h → real 0.5 CPU-h (-50%)")
        v.experiment(p, "E-9003", "H-9001", metric="continuous", status="preregistered")
        idx = p / "Experimentos" / "trazas" / "index.jsonl"
        ti.append(idx, run_entry("E-9001", "r1", "2031-01-01T10:00:00Z", "2031-01-01T10:40:00Z", [1, 2]))
        ti.append(idx, run_entry("E-9001", "r2", "2031-01-01T11:00:00Z", "2031-01-01T11:20:00Z", [3], cell="b"))
        ti.append(idx, run_entry("E-9001", "r3", "unknown", "unknown", [4], outcome="crashed"))
        logs = p / "Experimentos" / "logs"
        logs.mkdir()
        (logs / "E-9001.log").write_text("log inventado\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = hc.main(["--vault", str(self.tmp), *args])
        return code, out.getvalue()

    def test_measured_bases(self):
        code, out = self.cli("--project", "PROJ-900", "--json")
        self.assertEqual(code, 0)
        r = json.loads(out)
        rows = {e["id"]: e for e in r["experiments"]}
        e1 = rows["E-9001"]
        self.assertEqual(e1["basis"], "medido")
        self.assertEqual((e1["runs"], e1["timed_runs"], e1["untimed_runs"]), (3, 2, 1))
        self.assertEqual(e1["wall_seconds_total"], 3600.0)
        self.assertEqual(e1["wall_seconds_median_run"], 1800.0)
        self.assertEqual(e1["seeds"], 4)
        self.assertEqual(e1["conditions"], ["a", "b"])
        self.assertEqual(e1["outcomes"], {"completed": 2, "crashed": 1})
        self.assertEqual(e1["hardware"], "CPU 8 núcleos")
        self.assertTrue(e1["log"]["path"].endswith("Experimentos/logs/E-9001.log"))
        self.assertEqual(rows["E-9002"]["basis"], "registrado")
        self.assertIn("real 0.5 CPU-h", rows["E-9002"]["estimado_real"])
        self.assertEqual(rows["E-9003"]["basis"], hc.NO_BASE)
        proj = r["projects"][0]
        self.assertEqual(proj["compute_budget"], "200 CPU-h")
        self.assertIsNone(proj["completo_cost_threshold"])
        self.assertIn("completo_cost_threshold", proj["sin_definir"])
        self.assertEqual(r["trace_problems"], [])

    def test_hypothesis_ranking_and_suggestion(self):
        code, out = self.cli("--hypothesis", "H-9001", "--json")
        self.assertEqual(code, 0)
        r = json.loads(out)
        ids = [s["id"] for s in r["similar"]]
        self.assertEqual(ids[0], "E-9001")              # same project + cell + metric + wording
        self.assertNotIn("E-9003", ids)                 # its own experiment
        self.assertTrue(r["coste_base_sugerido"].startswith("medido: E-9001"))
        self.assertIn("total 60 min", r["coste_base_sugerido"])

    def test_no_data_says_so(self):
        shutil.rmtree(self.p / "Experimentos")
        (self.p / "Experimentos").mkdir()
        code, out = self.cli("--hypothesis", "H-9001", "--json")
        self.assertEqual(code, 0)
        r = json.loads(out)
        self.assertEqual(r["coste_base_sugerido"], "sin base medida")
        self.assertEqual(r["experiments"], [])
        code, out = self.cli("--project", "proyecto-inventado")
        self.assertIn("sin base medida", out)

    def test_broken_chain_is_reported_not_hidden(self):
        idx = self.p / "Experimentos" / "trazas" / "index.jsonl"
        lines = idx.read_text(encoding="utf-8").splitlines()
        lines[0] = lines[0].replace('"cell":"a"', '"cell":"z"')
        idx.write_text("\n".join(lines) + "\n", encoding="utf-8")
        code, out = self.cli("--project", "PROJ-900", "--json")
        r = json.loads(out)
        self.assertEqual(code, 0)
        self.assertTrue(r["trace_problems"])
        self.assertEqual({e["id"]: e for e in r["experiments"]}["E-9001"]["timed_runs"], 2)

    def test_correction_of_start_does_not_hide_end(self):
        p2 = self.v.project(slug="otro", pid="PROJ-901")
        self.v.experiment(p2, "E-9101", "H-9101")
        idx = p2 / "Experimentos" / "trazas" / "index.jsonl"
        ti.append(idx, ti.start_entry("E-9101", "r1", "confirmatory", "test", seeds=[1]))
        ents = ti.load(idx)
        end = ti.end_entry(ents, "r1", "completed", True, "test")
        ti.append(idx, end)
        ents = ti.load(idx)
        ti.append(idx, ti.correction_entry(ents, 0, "invented", "test", {"cell": "c"}))
        code, out = self.cli("--project", "PROJ-901", "--json")
        e = json.loads(out)["experiments"][0]
        self.assertEqual(e["outcomes"], {"completed": 1})
        self.assertEqual(e["timed_runs"], 1)

    def test_bad_input(self):
        self.assertEqual(self.cli("--project", "PROJ-999")[0], 2)
        self.assertEqual(self.cli("--hypothesis", "H-9")[0], 2)
        self.assertEqual(self.cli("--hypothesis", "H-9999")[0], 2)


if __name__ == "__main__":
    unittest.main()
