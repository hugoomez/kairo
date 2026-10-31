"""Tests for similar_work.py. Run: python -m pytest scripts/ledger

Invented vault in a temp dir (vault_fixture.py)."""

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
import similar_work as sw  # noqa: E402
from vault_fixture import Vault  # noqa: E402


class SimilarWork(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-similar-"))
        self.v = Vault(self.tmp)
        self.p = self.v.project()
        v, p = self.v, self.p
        v.hypothesis(p, "H-9001", "El regulador inventado acelera la convergencia del solver sintético.",
                     cells=["M1:fila_a|col_x"])
        v.hypothesis(p, "H-9002", "Otra afirmación sobre colores de cometas lejanos.", cells=["M1:fila_a|col_x"])
        v.hypothesis(p, "H-9003", "El regulador inventado acelera la convergencia del solver sintético grande.",
                     status="refutada")
        v.hypothesis(p, "H-9004", "Nada que ver con nada: tortugas y paraguas.")
        v.hypothesis(p, "H-9005", "El regulador inventado acelera la convergencia del solver sintético.",
                     send_never=True)
        v.experiment(p, "E-9001", "H-9001", metric="continuous")
        v.experiment(p, "E-9002", "H-9003", verdict="refutada", metric="proportion")
        v.experiment(p, "E-9003", "H-9004", metric="continuous", secondary=["H-9001"])
        v.experiment(p, "E-9004", "H-9004", metric="logistic_contrast",
                     prediction="Las tortugas prefieren paraguas azules.")
        v.digest(p, ["| H-9001 | x | propuesta |  |",
                     "| (sin nota) | El regulador inventado acelera la convergencia del solver sintético siempre "
                     "| descartada | check 2: inventado |",
                     "| (sin nota) | Algo sin relación | descartada | check 1 |"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sw.main(["--vault", str(self.tmp), *args])
        return code, out.getvalue(), err.getvalue()

    def test_hypothesis_query_finds_cells_wording_experiments_and_discards(self):
        code, out, _ = self.cli("--project", "PROJ-900", "--hypothesis", "H-9001", "--json")
        self.assertEqual(code, 0)
        r = json.loads(out)
        hyps = {h["id"]: h for h in r["hypotheses"]}
        self.assertIn("H-9002", hyps)                              # shared cell
        self.assertEqual(hyps["H-9002"]["casillas_compartidas"], ["M1:fila_a|col_x"])
        self.assertIn("H-9003", hyps)                              # wording
        self.assertGreaterEqual(hyps["H-9003"]["solapamiento_lexico"], 0.2)
        self.assertNotIn("H-9004", hyps)
        self.assertNotIn("H-9001", hyps)                           # never itself
        self.assertNotIn("H-9005", hyps)                           # send: never
        self.assertEqual(r["omitidas_send_never"], 1)
        exps = {e["id"]: e for e in r["experiments"]}
        self.assertEqual(r["experiments"][0]["id"], "E-9001")      # its own experiment first
        self.assertIn("E-9002", exps)                              # via H-9003
        self.assertEqual(exps["E-9002"]["verdict"], "refutada")
        self.assertIn("E-9003", exps)                              # secondary
        self.assertNotIn("E-9004", exps)
        self.assertEqual(len(r["descartes"]), 1)
        self.assertIn("léxico", r["metodo"])

    def test_slug_and_cells_only(self):
        code, out, _ = self.cli("--project", "proyecto-inventado", "--cells", "M1:fila_a|col_x", "--json")
        self.assertEqual(code, 0)
        r = json.loads(out)
        self.assertEqual({h["id"] for h in r["hypotheses"]}, {"H-9001", "H-9002"})
        self.assertEqual({e["id"] for e in r["experiments"]}, {"E-9001"})

    def test_keywords(self):
        code, out, _ = self.cli("--project", "PROJ-900", "--keywords", "tortugas paraguas", "--json")
        r = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual({h["id"] for h in r["hypotheses"]}, {"H-9004"})
        self.assertIn("E-9004", {e["id"] for e in r["experiments"]})

    def test_metric_only(self):
        code, out, _ = self.cli("--project", "PROJ-900", "--metric", "logistic_contrast", "--json")
        self.assertEqual(code, 0)
        self.assertEqual([e["id"] for e in json.loads(out)["experiments"]], ["E-9004"])

    def test_human_output_and_nothing_found(self):
        code, out, _ = self.cli("--project", "PROJ-900", "--keywords", "zzzzinexistente")
        self.assertEqual(code, 0)
        self.assertIn("Sin solapamientos", out)

    def test_bad_input(self):
        self.assertEqual(self.cli("--project", "PROJ-900")[0], 2)
        self.assertEqual(self.cli("--project", "PROJ-999", "--keywords", "x")[0], 2)
        self.assertEqual(self.cli("--project", "PROJ-900", "--hypothesis", "H-12")[0], 2)
        self.assertEqual(self.cli("--project", "PROJ-900", "--hypothesis", "H-9999")[0], 2)
        self.assertEqual(self.cli("--project", "PROJ-900", "--cells", "fila_a|col_x")[0], 2)

    def test_without_digest_or_frontier(self):
        (self.p / "_digest.md").unlink()
        code, out, _ = self.cli("--project", "PROJ-900", "--hypothesis", "H-9001", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["descartes"], [])


if __name__ == "__main__":
    unittest.main()
