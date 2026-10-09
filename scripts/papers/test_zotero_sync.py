"""Tests for zotero_sync.py (a fake local Zotero; invented papers)."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import zotero_sync as zs  # noqa: E402

NOTE = """---
id: P-0981
title: "Toy codes for invented qubits"
authors: ["Doe, Jane", "Roe, Rui"]
year: 2031
doi: ""
arxiv: 0000.11111
venue: arXiv preprint
url: https://arxiv.org/abs/0000.11111
projects: [PROJ-981]
---

## Resumen

Text that is never sent to Zotero by this script.
"""


class FakeZotero:
    def __init__(self, existing=None, up=True):
        self.items = list(existing or [])
        self.calls = []
        self.up = up
        self.no_bbt = False

    def __call__(self, path, body):
        self.calls.append((path, body))
        if not self.up:
            raise OSError("connection refused")
        if path == "/better-bibtex/json-rpc" and self.no_bbt:
            return 404, {}
        if path == "/connector/saveItems":
            it = body["items"][0]
            self.items.append({"citekey": "doe2031toy", "title": it["title"], "URL": it.get("url"),
                               "DOI": it.get("DOI")})
            return 201, {}
        if path == "/better-bibtex/json-rpc" and body["method"] == "item.search":
            term = body["params"][0].lower()
            hits = [i for i in self.items if term in json.dumps(i).lower()]
            return 200, {"jsonrpc": "2.0", "result": hits}
        return 404, {}


class TestZoteroSync(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-zot-"))
        (self.tmp / "Papers").mkdir()
        self.note = self.tmp / "Papers" / "P-0981 toy.md"
        self.note.write_text(NOTE, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_new_item_is_created_from_the_notes_metadata_and_its_key_recorded(self):
        z = FakeZotero()
        out = zs.sync(self.tmp, "P-0981", z)
        self.assertEqual((out["status"], out["zotero_key"]), ("created", "doe2031toy"))
        saved = next(b for p, b in z.calls if p == "/connector/saveItems")["items"][0]
        self.assertEqual(saved["itemType"], "preprint")
        self.assertEqual(saved["creators"][0], {"lastName": "Doe", "firstName": "Jane", "creatorType": "author"})
        self.assertEqual(saved["tags"], [{"tag": "PROJ-981"}])
        self.assertNotIn("never sent", json.dumps(z.calls))
        self.assertIn("zotero_key: doe2031toy", self.note.read_text(encoding="utf-8"))

    def test_an_existing_item_is_reused_never_duplicated(self):
        z = FakeZotero(existing=[{"citekey": "doeToy", "URL": "https://arxiv.org/abs/0000.11111"}])
        out = zs.sync(self.tmp, "P-0981", z)
        self.assertEqual((out["status"], out["zotero_key"]), ("exists", "doeToy"))
        self.assertFalse(any(p == "/connector/saveItems" for p, _ in z.calls))

    def test_zotero_down_is_said_once_and_writes_nothing(self):
        out = zs.sync(self.tmp, "P-0981", FakeZotero(up=False))
        self.assertEqual(out["status"], "unreachable")
        self.assertNotIn("zotero_key", self.note.read_text(encoding="utf-8"))

    def test_a_search_hit_for_another_paper_is_never_taken(self):
        others = [{"citekey": "smith2031toy", "title": "Toy codes for invented qubits",
                   "author": [{"family": "Smith", "given": "Ann"}]},                      # same title, another author
                  {"citekey": "roe2030x", "title": "Another paper", "URL": "https://arxiv.org/abs/0000.111112"}]
        z = FakeZotero(existing=others)
        out = zs.sync(self.tmp, "P-0981", z)
        self.assertEqual((out["status"], out["zotero_key"]), ("created", "doe2031toy"))

    def test_an_exact_title_and_first_author_is_the_same_item(self):
        z = FakeZotero(existing=[{"citekey": "doeToy2", "title": "Toy Codes for Invented Qubits",
                                  "creators": [{"lastName": "Doe", "firstName": "J."}]}])
        out = zs.sync(self.tmp, "P-0981", z)
        self.assertEqual((out["status"], out["zotero_key"]), ("exists", "doeToy2"))

    def test_without_better_bibtex_search_nothing_is_created(self):
        z = FakeZotero()
        z.no_bbt = True
        out = zs.sync(self.tmp, "P-0981", z)
        self.assertEqual(out["status"], "failed")
        self.assertIn("Better BibTeX no disponible: no se crea para no duplicar", out["reason"])
        self.assertFalse(any(p == "/connector/saveItems" for p, _ in z.calls))

    def saved(self, note):
        self.note.write_text(note, encoding="utf-8")
        z = FakeZotero()
        zs.sync(self.tmp, "P-0981", z)
        return next(b for p, b in z.calls if p == "/connector/saveItems")["items"][0]

    def test_a_published_version_carries_its_own_year_and_type(self):
        pub = NOTE.replace("projects:", 'published_doi: "10.9999/conf.98"\npublished_venue: "Invented Conf on Codes"\n'
                                        "published_year: 2032\nvenue_type: proceedings-article\nprojects:")
        it = self.saved(pub)
        self.assertEqual((it["itemType"], it["date"], it["DOI"]), ("conferencePaper", "2032", "10.9999/conf.98"))
        self.assertEqual(it["proceedingsTitle"], "Invented Conf on Codes")
        # without its year, the preprint is what the item is
        it = self.saved(pub.replace("published_year: 2032\n", ""))
        self.assertEqual((it["itemType"], it["date"]), ("preprint", "2031"))
        self.assertNotIn("DOI", it)

    def test_the_item_type_follows_the_registered_type_then_export_bibs_rule(self):
        base = NOTE.replace("arxiv: 0000.11111\n", "arxiv:\n").replace('doi: ""', 'doi: "10.9999/j.1"')
        it = self.saved(base.replace("venue: arXiv preprint", 'venue: "Proceedings of the National Academy of Toys"'))
        self.assertEqual(it["itemType"], "journalArticle")
        it = self.saved(base.replace("venue: arXiv preprint",
                                     'venue: "Advances in Neural Information Processing Systems"'))
        self.assertEqual(it["itemType"], "conferencePaper")
        it = self.saved(base.replace("venue: arXiv preprint", 'venue: "Invented Proceedings"\n'
                                                               "venue_type: journal-article"))
        self.assertEqual(it["itemType"], "journalArticle")

    def test_particles_and_collaborations_keep_their_names(self):
        it = self.saved(NOTE.replace('["Doe, Jane", "Roe, Rui"]',
                                     '["Laurens van der Maaten", "Ana de la Cruz", "TOY Collaboration"]'))
        self.assertEqual(it["creators"][0], {"lastName": "van der Maaten", "firstName": "Laurens",
                                             "creatorType": "author"})
        self.assertEqual(it["creators"][1]["lastName"], "de la Cruz")
        self.assertEqual(it["creators"][2], {"name": "TOY Collaboration", "creatorType": "author"})

    def test_send_never_is_not_sent(self):
        self.note.write_text(NOTE.replace("projects:", "send: never\nprojects:"), encoding="utf-8")
        z = FakeZotero()
        self.assertEqual(zs.sync(self.tmp, "P-0981", z)["status"], "skipped_send_never")
        self.assertEqual(z.calls, [])


if __name__ == "__main__":
    unittest.main()
