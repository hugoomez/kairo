"""Tests for manuscript.py. Run: python -m pytest scripts/manuscript (invented project PROJ-900)."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import manuscript as ms  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-ms-"))
        self.proj = self.tmp / "Projects" / "demo"
        (self.proj / "Claims").mkdir(parents=True)
        (self.proj / "Hipotesis").mkdir()
        (self.proj / "_hub.md").write_text("---\nid: PROJ-900\n---\n", encoding="utf-8")
        (self.proj / "Claims" / "C-9001.md").write_text("---\nid: C-9001\n---\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestInit(Base):
    def test_writes_outline_and_skeleton_without_prose(self):
        o, m = ms.init(self.proj, "cotas-azules", 'Cotas para widgets "azules"', None)
        meta, sections, _ = ms.parse_outline(o)
        self.assertEqual((meta["project"], meta["title"], meta["venue"]), ("PROJ-900", 'Cotas para widgets "azules"', "por decidir"))
        self.assertEqual([s["kind"] for s in sections], ["prosa", "prosa", "prosa", "resultado", "cierre"])
        text = m.read_text(encoding="utf-8")
        for s in sections:
            self.assertIn(f"<!-- kairo:section {s['id']} -->", text)
        self.assertIn("status: esqueleto", text)
        self.assertIn("_(pendiente — resultados", text)

    def test_never_overwrites(self):
        ms.init(self.proj, "t1", "T", None)
        with self.assertRaises(ms.Refused):
            ms.init(self.proj, "t1", "Otra", None)

    def test_bad_thread_and_missing_hub(self):
        with self.assertRaises(ms.Refused):
            ms.init(self.proj, "Mal Hilo", "T", None)
        (self.proj / "_hub.md").unlink()
        with self.assertRaises(ms.Refused):
            ms.init(self.proj, "t2", "T", None)


class TestBind(Base):
    def test_binds_existing_notes_once_and_keeps_the_rest(self):
        o, _ = ms.init(self.proj, "t", "T", "COLT")
        self.assertEqual(ms.bind(self.proj, "t", "resultados", "C-9001"), ["C-9001"])
        self.assertEqual(ms.bind(self.proj, "t", "resultados", "C-9001"), ["C-9001"])
        meta, sections, body = ms.parse_outline(o)
        self.assertEqual(meta["venue"], "COLT")
        self.assertIn("## Contribución", body)
        self.assertEqual(next(s for s in sections if s["id"] == "resultados")["depends_on"], ["C-9001"])

    def test_refusals(self):
        ms.init(self.proj, "t", "T", None)
        for section, nid in (("resultados", "C-0404"), ("resultados", "X-1"), ("nope", "C-9001")):
            with self.subTest(section=section, nid=nid), self.assertRaises(ms.Refused):
                ms.bind(self.proj, "t", section, nid)

    def test_show_is_json(self):
        import io
        from contextlib import redirect_stdout
        ms.init(self.proj, "t", "T", None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(ms.main(["show", "--project-dir", str(self.proj), "--thread", "t"]), 0)
        self.assertEqual(len(json.loads(buf.getvalue())["sections"]), 5)


if __name__ == "__main__":
    unittest.main()
