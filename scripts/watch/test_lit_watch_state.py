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

    def plan(self, sources=("arxiv", "s2"), **extra):
        run = self.p / "_busquedas" / "2031-01-15"
        run.mkdir(parents=True, exist_ok=True)
        (run / "plan.json").write_text(json.dumps({
            "description": "x", "facets": [{"id": "A", "term": "fictional widgets", "synonyms": []},
                                           {"id": "B", "term": "synthetic spin", "synonyms": []}],
            "sources": list(sources), "from": "2020-01-01", "per_query": 100, "anchors": 10, "cross": False,
            "arxiv_categories": [], "include": [], "exclude": [], "scope_out": [], **extra}), encoding="utf-8")
        return run / "plan.json"

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


class OneRunAtATime(Base):
    def test_two_runs_on_one_day_keep_their_own_raw_files(self):
        self.plan()
        _, first = self.delta()
        raw1 = sorted((self.p / "_vigilancia").glob("raw-*"))
        self.assertEqual(len(raw1), 1)
        before = {f.name: f.read_bytes() for f in raw1[0].iterdir()}
        self.assertTrue(before)
        _, second = self.delta()
        raws = sorted((self.p / "_vigilancia").glob("raw-*"))
        self.assertEqual(len(raws), 2)
        self.assertEqual({f.name: f.read_bytes() for f in raw1[0].iterdir()}, before)
        data = json.loads(self.run_file(second).read_text(encoding="utf-8"))
        self.assertTrue(data["raw_dir"].endswith(raws[1].name))

    def test_a_second_watch_of_the_same_project_refuses_while_one_runs(self):
        (self.p / "_vigilancia").mkdir(exist_ok=True)
        lock = self.p / "_vigilancia" / lit_watch.LOCK
        lock.write_text(json.dumps({"pid": 1, "started": "2031-03-01T08:00:00"}), encoding="utf-8")
        code, out = self.delta()
        self.assertEqual(code, lit_watch.EXIT_BUSY, out)
        self.assertIn("otra vigilancia", out["error"])
        self.assertTrue(lock.exists())                         # never taken from the run that holds it
        self.assertFalse(list((self.p / "_vigilancia").glob("vigilancia-*")))

    def test_a_stale_lock_is_taken_over_and_said_so(self):
        (self.p / "_vigilancia").mkdir(exist_ok=True)
        lock = self.p / "_vigilancia" / lit_watch.LOCK
        lock.write_text("{}", encoding="utf-8")
        old = lock.stat().st_mtime - (lit_watch.LOCK_STALE_HOURS + 1) * 3600
        import os
        os.utime(lock, (old, old))
        code, out = self.delta()
        self.assertEqual(code, 0, out)
        self.assertTrue(out["stale_lock_removed"])
        self.assertFalse(lock.exists())                        # released at the end


if __name__ == "__main__":
    unittest.main()
