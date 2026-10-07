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

    def __call__(self, path, body):
        self.calls.append((path, body))
        if not self.up:
            raise OSError("connection refused")
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

    def test_send_never_is_not_sent(self):
        self.note.write_text(NOTE.replace("projects:", "send: never\nprojects:"), encoding="utf-8")
        z = FakeZotero()
        self.assertEqual(zs.sync(self.tmp, "P-0981", z)["status"], "skipped_send_never")
        self.assertEqual(z.calls, [])


if __name__ == "__main__":
    unittest.main()
