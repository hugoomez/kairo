"""Tests for move_reading_notes.py (invented notes, no network).

Run: python -m unittest test_move_reading_notes   (from scripts/papers/)
"""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import move_reading_notes as mrn  # noqa: E402

PAPER = "\n".join([
    "---", "id: P-0901", "title: A synthetic paper", "fulltext: full", "---", "",
    "## Referencia", "", "Doe, J. (2030). A synthetic paper.", "",
    "## Resumen", "", "We study toy dynamics.", "",
    "## Texto completo", "", "### 1 Intro", "", "Verbatim intro.", "",
    "```", "## Notas de lectura", "```", "",  # inside a fence: not a heading
    "## Notas de lectura", "",
    "> Escritas por un modelo; no se pueden citar.", "",
    "- NOTASONLY paraphrase of §1.", "",
])


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = mrn.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


class TestMove(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.v = Path(self.tmp.name)
        (self.v / "Papers").mkdir()
        self.paper = self.v / "Papers" / "P-0901 Doe 2030 Synthetic.md"
        self.paper.write_bytes(PAPER.replace("\n", "\r\n").encode("utf-8"))  # CRLF kept
        self.secret = self.v / "Papers" / "P-0902 Private.md"
        self.secret.write_text("---\nsend: never\n---\n\n## Notas de lectura\n\nSECRET\n",
                               encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_split_section(self):
        kept, body = mrn.split_section(PAPER)
        self.assertIn("NOTASONLY", body)
        self.assertNotIn("NOTASONLY", kept)
        self.assertIn("Verbatim intro.", kept)
        self.assertIn("```\n## Notas de lectura\n```", kept)  # the fenced one stays
        self.assertEqual(mrn.split_section("## Resumen\n\nx\n"), ("## Resumen\n\nx\n", None))

    def test_section_in_the_middle(self):
        text = "## A\n\na\n\n## Notas de lectura\n\nn\n\n## B\n\nb\n"
        kept, body = mrn.split_section(text)
        self.assertEqual(body, "n")
        self.assertEqual(kept, "## A\n\na\n\n## B\n\nb\n")

    def test_dry_run_changes_nothing(self):
        before = self.paper.read_bytes()
        rc, out, _ = run("--vault", str(self.v))
        self.assertEqual(rc, 0)
        self.assertIn("se movería: P-0901", out)
        self.assertEqual(self.paper.read_bytes(), before)
        self.assertFalse((self.v / "Papers" / "_notas").exists())

    def test_write_moves_and_marks(self):
        rc, out, _ = run("--vault", str(self.v), "--write", "--date", "2030-01-02")
        self.assertEqual(rc, 0)
        self.assertNotIn("NOTASONLY", out)                     # names only, never text
        paper = self.paper.read_bytes().decode("utf-8")
        self.assertNotIn("NOTASONLY", paper)
        self.assertNotIn("\n## Notas de lectura", paper.replace("```\r\n## Notas", ""))
        self.assertIn("\r\n", paper)                           # line endings preserved
        self.assertTrue(paper.endswith("```\r\n"))
        notes = (self.v / "Papers" / "_notas" / "P-0901.md").read_text(encoding="utf-8")
        for s in ("notas_de: P-0901", "escrito_por: modelo", "citable: false",
                  "movido: 2030-01-02", "NOTASONLY", "No se cita"):
            self.assertIn(s, notes)
        # send: never note untouched and not read into _notas
        self.assertIn("SECRET", self.secret.read_text(encoding="utf-8"))
        self.assertFalse((self.v / "Papers" / "_notas" / "P-0902.md").exists())
        # idempotent; --check is clean afterwards
        self.assertEqual(run("--vault", str(self.v), "--write")[0], 0)
        self.assertEqual(run("--vault", str(self.v), "--check")[0], 0)

    def test_check_and_conflict(self):
        self.assertEqual(run("--vault", str(self.v), "--check")[0], 1)
        (self.v / "Papers" / "_notas").mkdir()
        (self.v / "Papers" / "_notas" / "P-0901.md").write_text("---\nx: 1\n---\nother\n",
                                                                encoding="utf-8")
        rc, _, err = run("--vault", str(self.v), "--write")
        self.assertEqual(rc, 1)
        self.assertIn("conflicto", err)
        self.assertIn("NOTASONLY", self.paper.read_text(encoding="utf-8"))  # not removed

    def test_bad_vault(self):
        self.assertEqual(run("--vault", str(self.v / "nope"))[0], 2)


if __name__ == "__main__":
    unittest.main()
