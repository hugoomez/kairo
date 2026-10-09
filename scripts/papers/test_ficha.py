"""Tests for ficha.py: a paper's reading card keeps only items whose quote is the
paper's own text under its locator. Invented fixture paper P-0901 only."""

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
sys.path.insert(0, str(HERE.parent / "ledger"))
import ficha  # noqa: E402
from test_verifier_scripts import build_fixture  # noqa: E402

REPLY = {"paper": "P-0901", "items": [
    {"kind": "resultado", "claim": "Boosting the precursor speeds the transition by about 30%.",
     "quote": "Boosting the precursor speeds up the transition by ~30% (Table 1).", "locator": "§3.2"},
    {"kind": "resultado", "claim": "The lead time follows a power law.",
     "quote": "The precursor rises before the transition; lead time follows a power law", "locator": "§3.1"},
    {"kind": "resultado", "claim": "It speeds the transition by 45%.",
     "quote": "Boosting the precursor speeds up the transition by ~30% (Table 1).", "locator": "§3.2"},
    {"kind": "método", "claim": "Invented method.", "quote": "We invented a decoder that nobody wrote here",
     "locator": "§3.1"},
    {"kind": "resultado", "claim": "Wrong place.",
     "quote": "Boosting the precursor speeds up the transition by ~30% (Table 1).", "locator": "§3.1"},
    {"kind": "opinión", "claim": "x", "quote": "y y y y y y", "locator": "§3.1"}]}


class Ficha(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-ficha-"))
        self.vault = build_fixture(self.tmp)["vault"]
        self.note = next((self.vault / "Papers").glob("P-0901*.md"))
        self.note.write_text(self.note.read_text(encoding="utf-8").replace(
            "title: A synthetic", "fulltext: full\nprojects: [PROJ-900]\ntitle: A synthetic"), encoding="utf-8")
        self.reply = self.tmp / "reply.txt"
        self.reply.write_text("Card.\n```json\n" + json.dumps(REPLY) + "\n```\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = ficha.main(list(args))
        return code, json.loads(buf.getvalue())

    def test_only_items_with_the_papers_own_words_under_their_locator_are_kept(self):
        code, out = self.cli("write", "--vault", str(self.vault), "--id", "P-0901", "--reply", str(self.reply),
                             "--model", "claude-sonnet-5-5")
        self.assertEqual((code, out["kept"]), (0, 2), out)
        reasons = " | ".join(d["why"] for d in out["dropped"])
        self.assertIn("45", reasons)                                  # a number the quote does not hold
        self.assertIn("no aparece literalmente", reasons)             # invented / wrong locator
        self.assertIn("tipo desconocido", reasons)
        card = (self.vault / "Papers" / "_fichas" / "P-0901.md").read_text(encoding="utf-8")
        self.assertIn("citable: false", card)
        self.assertIn("— P-0901 §3.2", card)
        self.assertIn("## Descartados por el script", card)
        self.assertNotIn("We invented a decoder", card.split("## Descartados")[0])

    def test_status_finds_missing_current_and_stale_cards(self):
        code, out = self.cli("status", "--vault", str(self.vault), "--project", "PROJ-900")
        self.assertEqual([x["state"] for x in out["to_card"]], ["falta"])
        self.cli("write", "--vault", str(self.vault), "--id", "P-0901", "--reply", str(self.reply),
                 "--model", "m")
        self.assertEqual(self.cli("status", "--vault", str(self.vault), "--ids", "P-0901")[1]["vigentes"], 1)
        self.note.write_text(self.note.read_text(encoding="utf-8").replace("Appendix paragraph.", "Rebuilt text."),
                             encoding="utf-8")
        self.assertEqual(self.cli("status", "--vault", str(self.vault), "--ids", "P-0901")[1]["to_card"][0]["state"],
                         "caducada")

    def test_abstract_only_and_send_never_notes_get_no_card(self):
        self.note.write_text(self.note.read_text(encoding="utf-8").replace("fulltext: full", "fulltext: abstract-only"),
                             encoding="utf-8")
        code, out = self.cli("write", "--vault", str(self.vault), "--id", "P-0901", "--reply", str(self.reply),
                             "--model", "m")
        self.assertEqual(code, 2)
        self.note.write_text(self.note.read_text(encoding="utf-8").replace("fulltext: abstract-only",
                                                                           "fulltext: full\nsend: never"),
                             encoding="utf-8")
        code, out = self.cli("write", "--vault", str(self.vault), "--id", "P-0901", "--reply", str(self.reply),
                             "--model", "m")
        self.assertEqual(code, 2)
        self.assertIn("send: never", out["error"])


class Suspicious(unittest.TestCase):
    """A flagged passage reaches the card as a locator and a description, never
    as its text (2026-10-09 audit, F8)."""

    def test_a_description_is_kept_and_cut_at_twelve_words(self):
        self.assertEqual(ficha.suspicious_line({"locator": "§4", "what": "asks the reader to run a toy command"}),
                         "§4: asks the reader to run a toy command")
        long = ficha.suspicious_line({"locator": "§4", "what": " ".join(["word"] * 20)})
        self.assertEqual(len(long.split(": ", 1)[1].split()), 13)          # 12 words and the ellipsis

    def test_a_passage_returned_as_text_is_never_written(self):
        line = ficha.suspicious_line("§5.1 «Invented instruction text that must not travel»")
        self.assertTrue(line.startswith("§5.1: "))
        self.assertNotIn("Invented instruction", line)


if __name__ == "__main__":
    unittest.main()
