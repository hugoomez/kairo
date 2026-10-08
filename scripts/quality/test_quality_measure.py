"""quality_report: a gold set from identifiers, a blind human screening sheet and
its agreement with the screeners; lit_search screen reports the gold set's
recall itself. Invented ids and text only."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "search"))
import lit_search as ls  # noqa: E402
import quality_report as qr  # noqa: E402

BIB = """@article{doe2030toy, title={Toy codes}, author={Doe, Jane}, year={2030}, doi={10.9999/toy.1}}
@misc{roe2031, title={Invented decoders}, author={Roe, Rui}, eprint={2031.00002}, archivePrefix={arXiv}}
@book{poe2029, title={A book with no identifier}, author={Poe, Al}, year={2029}}
"""


def cand(key, title, decision, doi=None, arxiv=None):
    return {"key": key, "title": title, "abstract": f"Abstract of {title}.", "year": 2031, "doi": doi,
            "arxiv": arxiv, "sources": ["arxiv"], "screen": {"decision": decision, "why": "x"}}


class Measure(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-q-"))
        self.proj = self.tmp / "Projects" / "demo"
        self.run = self.proj / "_busquedas" / "2031-06-01"
        self.run.mkdir(parents=True)
        (self.run / "plan.json").write_text(json.dumps({"description": "invented toy decoders"}), encoding="utf-8")
        cands = [cand(f"arxiv:2031.{i:05d}", f"Toy paper {i}", "include" if i % 2 else "exclude", arxiv=f"2031.{i:05d}")
                 for i in range(1, 21)] + [cand("doi:10.9999/toy.1", "Toy codes", "exclude", doi="10.9999/toy.1")]
        (self.run / "screened.json").write_text(json.dumps(cands), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = qr.main(list(args))
        return code, json.loads(buf.getvalue())

    def test_gold_init_from_a_bib_and_ids_never_duplicates(self):
        bib = self.tmp / "survey.bib"
        bib.write_text(BIB, encoding="utf-8")
        gold = self.proj / "_eval" / "gold.json"
        code, out = self.cli("gold-init", "--out", str(gold), "--bib", str(bib))
        self.assertEqual((code, out["added"]), (0, 3), out)
        rows = json.loads(gold.read_text(encoding="utf-8"))
        self.assertEqual([next(k for k in ("doi", "arxiv", "title") if k in r) for r in rows], ["doi", "arxiv", "title"])
        code, out = self.cli("gold-init", "--out", str(gold), "--ids", "arXiv:2031.00002", "DOI:10.9999/new.2")
        self.assertEqual((out["added"], out["total"]), (1, 4))

    def test_the_human_sheet_is_blind_and_reproducible_and_agreement_is_computed(self):
        sheet = self.proj / "_eval" / "humano.md"
        code, out = self.cli("human-sheet", "--run", str(self.run), "--n", "10", "--out", str(sheet))
        self.assertEqual((code, out["sample"]), (0, 10))
        text = sheet.read_text(encoding="utf-8")
        self.assertNotIn("include |", text.split("## Candidatos")[0].replace("`include` o `exclude`", ""))
        again = self.tmp / "again.md"
        self.cli("human-sheet", "--run", str(self.run), "--n", "10", "--out", str(again))
        self.assertEqual(again.read_text(encoding="utf-8"), text)                         # reproducible
        keys = [ln.split("`")[1] for ln in text.splitlines() if ln.startswith("| `")]
        screened = {c["key"]: c["screen"]["decision"] for c in
                    json.loads((self.run / "screened.json").read_text(encoding="utf-8"))}
        flipped = next(k for k in keys if screened[k] == "exclude")              # the researcher includes one
        filled = text
        for k in keys:
            d = "include" if k == flipped else screened[k]
            filled = filled.replace(f"| `{k}` |  |", f"| `{k}` | {d} |")
        sheet.write_text(filled, encoding="utf-8")
        code, out = self.cli("human-agree", "--run", str(self.run), "--sheet", str(sheet))
        self.assertEqual(code, 0, out)
        self.assertEqual((out["compared"], out["screen_missed"]), (10, [flipped]))
        self.assertEqual(out["agreement"], 0.9)
        self.assertIsNotNone(out["kappa"])

    def test_screen_reports_the_gold_sets_recall_when_the_project_has_one(self):
        gold = self.proj / "_eval" / "gold.json"
        gold.parent.mkdir(parents=True)
        gold.write_text(json.dumps([{"arxiv": "2031.00001"}, {"doi": "10.9999/toy.1"}, {"arxiv": "2031.99999"}]),
                        encoding="utf-8")
        g = ls.gold_recall(self.run)
        self.assertEqual((g["gold"], g["recall_identified"], g["recall_included"]), (3, 0.667, 0.333))
        md = ls.gold_md(g)
        self.assertIn("No encontrados: `2031.99999`", md)
        self.assertIn("excluidos en el cribado: `10.9999/toy.1`", md)
        self.assertIsNone(ls.gold_recall(self.tmp))                              # not a project run


if __name__ == "__main__":
    unittest.main()
