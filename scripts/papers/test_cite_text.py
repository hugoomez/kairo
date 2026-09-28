"""Tests for cite_text.py. Run: python -m pytest scripts/papers

Reuses the invented fixture of the verifier tests (P-0901, P-0902)."""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "ledger"))
import cite_text  # noqa: E402
from test_verifier_scripts import build_fixture, send_never  # noqa: E402


class TestCiteText(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-cite-"))
        self.f = build_fixture(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cite_text.main(list(args))
        return code, json.loads(buf.getvalue())

    def test_returns_the_verbatim_units_for_a_locator(self):
        code, res = self.run_cli("--vault", str(self.f["vault"]), "--paper", "P-0901", "--locator", "§3.1")
        self.assertEqual(code, 0)
        self.assertTrue(res["units"])
        self.assertIn("P-0901", res["source"])

    def test_no_match_is_said_not_invented(self):
        code, res = self.run_cli("--vault", str(self.f["vault"]), "--paper", "P-0901", "--locator", "§99.9")
        self.assertEqual((code, res["units"]), (0, []))
        self.assertIn("ningún fragmento", res["note"])

    def test_send_never_paper_gives_no_text(self):
        paper = next(Path(self.f["vault"], "Papers").glob("P-0901*.md"))
        paper.write_text(send_never(paper.read_text(encoding="utf-8")), encoding="utf-8")
        _, res = self.run_cli("--vault", str(self.f["vault"]), "--paper", "P-0901", "--locator", "§3.1")
        self.assertEqual(res["units"], [])
        self.assertIn("send: never", res["note"])

    def test_bad_paper_id_is_refused(self):
        code, res = self.run_cli("--vault", str(self.f["vault"]), "--paper", "../x", "--locator", "§1")
        self.assertEqual(code, 1)
        self.assertIn("error", res)


if __name__ == "__main__":
    unittest.main()
