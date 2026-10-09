"""lit_watch: the watch's own state (runs, cursors, lock, status, pages) is never
guessed, lost or stranded. Invented project, ids and text only."""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lit_watch  # noqa: E402
import test_lit_watch as base  # noqa: E402

TODAY = base.TODAY


class Base(unittest.TestCase):
    setUp = base.TestLitWatch.setUp
    tearDown = base.TestLitWatch.tearDown
    cli = base.TestLitWatch.cli
    delta = base.TestLitWatch.delta
    run_file = base.TestLitWatch.run_file

    def cli_on(self, day: date, *args, fetch=None):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = lit_watch.main(list(args), fetch=fetch or base.FakeNet(), today=day)
        return code, json.loads(buf.getvalue())

    def delta_on(self, day: date, *extra, fetch=None):
        return self.cli_on(day, "delta", "--vault", str(self.vault), "--project-dir", str(self.p), *extra,
                           fetch=fetch)


class CorruptStateIsRefused(Base):
    def test_a_corrupt_run_file_stops_the_watch_and_is_named(self):
        self.delta()
        f = next((self.p / "_vigilancia").glob("vigilancia-*.json"))
        f.write_text('{"candidates": [', encoding="utf-8")
        code, out = self.delta()
        self.assertEqual(code, 2, out)
        self.assertIn(f.name, out["error"])

    def test_corrupt_cursors_stop_the_watch_and_are_named(self):
        self.delta()
        (self.p / "_vigilancia" / "cursores.json").write_text("{not json", encoding="utf-8")
        code, out = self.delta()
        self.assertEqual(code, 2, out)
        self.assertIn("cursores.json", out["error"])

    def test_saves_are_atomic(self):
        f = self.tmp / "estado-inventado.json"
        lit_watch.save_run(f, {"candidates": [], "n": 1})
        with mock.patch.object(lit_watch.os, "replace", side_effect=OSError("disco lleno inventado")):
            with self.assertRaises(OSError):
                lit_watch.save_run(f, {"candidates": [], "n": 2})
        self.assertEqual(json.loads(f.read_text(encoding="utf-8"))["n"], 1)
        self.assertEqual([p.name for p in f.parent.glob("estado-inventado*")], [f.name])


if __name__ == "__main__":
    unittest.main()
