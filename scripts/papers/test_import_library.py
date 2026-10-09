"""Tests for import_library.py (invented references; ingestion is stubbed).
Run: python -m pytest scripts/papers"""

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
import import_library as il  # noqa: E402

BIB = r"""
% a comment line
@article{doe2031toy,
  title = {Toy {qLDPC} Codes for {Invented} Qubits},
  author = {Doe, Jane and Roe, Rui},
  year = {2031},
  eprint = {0000.11111},
  archivePrefix = {arXiv},
}
@inproceedings{poe2030decoders,
  title = "Fast decoders, with a comma",
  author = "Poe, Ana",
  booktitle = {Proceedings of the Invented Conference},
  doi = {10.0000/Inv.Conf.7},
  year = 2030
}
@misc{wu2029note,
  title = {A note with no identifier},
  author = {Wu, Li},
  howpublished = {\url{https://arxiv.org/abs/0000.22222v3}},
}
@book{nobody2001,
  title = {A book without any identifier},
}
@string{foo = "bar"}
"""

CSL = [{"id": "k1", "title": "From CSL with a DOI", "DOI": "10.0000/csl.1", "type": "article-journal"},
       {"id": "k2", "title": "From CSL on arXiv", "URL": "http://arxiv.org/abs/0000.33333", "type": "article"},
       {"id": "k3", "title": "From CSL, nothing", "type": "book"}]


class TestImport(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-import-"))
        (self.tmp / "vault" / "Papers").mkdir(parents=True)
        self.calls = []
        self.orig = il.ingest

        def fake(argv):
            self.calls.append(argv)
            pid = f"P-{len(self.calls):04d}"
            return 0, {"status": "created", "id": pid, "path": f"Papers/{pid} x.md"}
        il.ingest = fake

    def tearDown(self):
        il.ingest = self.orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = il.main(list(args))
        return code, json.loads(buf.getvalue())

    def test_bibtex_entries_become_identifiers(self):
        got = il.parse_bibtex(BIB)
        by = {e["key"]: e for e in got}
        self.assertEqual(set(by), {"doe2031toy", "poe2030decoders", "wu2029note", "nobody2001"})
        self.assertEqual(il.identifier(by["doe2031toy"]), ("arxiv", "0000.11111"))
        self.assertEqual(il.identifier(by["poe2030decoders"]), ("doi", "10.0000/inv.conf.7"))
        self.assertEqual(il.identifier(by["wu2029note"]), ("arxiv", "0000.22222"))
        self.assertEqual(il.identifier(by["nobody2001"]), (None, None))
        self.assertEqual(by["poe2030decoders"]["title"], "Fast decoders, with a comma")

    def test_dry_run_plans_and_ingests_nothing(self):
        bib = self.tmp / "refs.bib"
        bib.write_text(BIB, encoding="utf-8")
        code, out = self.cli("--vault", str(self.tmp / "vault"), "--project", "PROJ-001", "--bib", str(bib),
                             "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.calls, [])
        self.assertEqual(len(out["plan"]), 3)
        self.assertEqual([x["key"] for x in out["no_identifier"]], ["nobody2001"])

    def test_import_ingests_each_through_ingest_paper_and_records_zotero_keys(self):
        bib = self.tmp / "refs.bib"
        bib.write_text(BIB, encoding="utf-8")
        code, out = self.cli("--vault", str(self.tmp / "vault"), "--project", "PROJ-001", "--bib", str(bib),
                             "--zotero-keys")
        self.assertEqual(code, 0, out)
        adds = [c for c in self.calls if c[0] == "add"]
        self.assertEqual(len(adds), 3)
        self.assertIn("--arxiv", adds[0])
        self.assertIn("--doi", adds[1])
        self.assertIn("manual", adds[0])                                    # --source
        keys = [c for c in self.calls if c[0] == "zotero-key"]
        self.assertEqual([c[c.index("--key") + 1] for c in keys], ["doe2031toy", "poe2030decoders", "wu2029note"])
        self.assertEqual(out["counts"], {"created": 3, "exists": 0, "failed": 0, "no_identifier": 1})

    def test_csl_json(self):
        f = self.tmp / "refs.json"
        f.write_text(json.dumps(CSL), encoding="utf-8")
        code, out = self.cli("--vault", str(self.tmp / "vault"), "--project", "PROJ-001", "--csl", str(f),
                             "--dry-run")
        self.assertEqual([(x["kind"], x["id"]) for x in out["plan"]], [("doi", "10.0000/csl.1"),
                                                                       ("arxiv", "0000.33333")])


if __name__ == "__main__":
    unittest.main()
