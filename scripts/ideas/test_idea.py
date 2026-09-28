"""Tests for idea.py. Run: python -m pytest scripts/ideas  (invented text only)."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import idea  # noqa: E402


class TestIdeas(unittest.TestCase):
    def setUp(self):
        self.vault = Path(tempfile.mkdtemp(prefix="kairo-ideas-"))

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    def test_add_keeps_the_text_verbatim_and_numbers_vault_wide(self):
        p = idea.add(self.vault, "  probar: widgets «azules» en frío  ", "cli", None, "de una charla", now="2026-01-02T10:00:00Z")
        q = idea.add(self.vault, "otra", "ui", "PROJ-900", None)
        self.assertEqual((p.stem, q.stem), ("I-0001", "I-0002"))
        i = idea.parse(p)
        self.assertEqual(i["text"], "probar: widgets «azules» en frío")
        self.assertEqual((i["status"], i["source"], i["matches"], i["context"]), ("nueva", "cli", [], "de una charla"))
        self.assertEqual(idea.parse(q)["project"], "PROJ-900")

    def test_refusals(self):
        for args in (("   ", "cli", None), ("x", "email", None), ("x", "cli", "proyecto")):
            with self.subTest(args=args), self.assertRaises(idea.Refused):
                idea.add(self.vault, *args, None)
        idea.add(self.vault, "x", "cli", None, None)
        for kw in ({"status": "hecha"}, {"hypothesis": "H9"}, {"matches": [{"p": 1}]}):
            with self.subTest(kw=kw), self.assertRaises(idea.Refused):
                idea.set_fields(self.vault, "I-0001", kw.get("status"), kw.get("matches"), None, kw.get("hypothesis"))
        with self.assertRaises(idea.Refused):
            idea.set_fields(self.vault, "I-0404", "usada", None, None, None)

    def test_set_updates_only_metadata(self):
        p = idea.add(self.vault, "texto intacto", "cli", None, None)
        idea.set_fields(self.vault, "I-0001", "usada", [{"project": "PROJ-900", "score": 0.8, "via": "semantic"}],
                        "job-1", "H-9001")
        i = idea.parse(p)
        self.assertEqual((i["status"], i["became"], i["text"]), ("usada", "H-9001", "texto intacto"))
        self.assertEqual(i["matches"][0]["project"], "PROJ-900")
        self.assertEqual([x["id"] for x in idea.list_ideas(self.vault, "usada")], ["I-0001"])
        self.assertEqual(idea.list_ideas(self.vault, "nueva"), [])

    def test_cli_add_commits_only_the_idea(self):
        for a in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            subprocess.run(["git", *a], cwd=self.vault, check=True, capture_output=True)
        (self.vault / "otra-cosa.md").write_text("sin commitear", encoding="utf-8")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = idea.main(["add", "--vault", str(self.vault), "--commit", "una", "idea", "rápida"])
        out = json.loads(buf.getvalue())
        self.assertEqual((code, out["id"], out["committed"]), (0, "I-0001", True))
        status = subprocess.run(["git", "status", "--porcelain"], cwd=self.vault, capture_output=True, text=True).stdout
        self.assertIn("otra-cosa.md", status)
        self.assertNotIn("Ideas", status)
        self.assertEqual(idea.parse(Path(out["path"]))["text"], "una idea rápida")


if __name__ == "__main__":
    unittest.main()
