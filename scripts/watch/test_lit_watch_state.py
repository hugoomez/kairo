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


class AFailureIsVisible(Base):
    def status(self) -> dict:
        return json.loads((self.p / "_vigilancia" / lit_watch.STATUS).read_text(encoding="utf-8"))

    def test_every_query_lost_exits_4_with_a_page_and_a_failed_status(self):
        code, res = self.delta(base.FakeNet(fail={"api.semanticscholar.org", "export.arxiv.org"}))
        self.assertEqual(code, lit_watch.EXIT_LOST_ALL)
        self.assertIsNone(res["run"])
        page = (self.vault / res["digest"]).read_text(encoding="utf-8")
        self.assertIn("La vigilancia falló", page)
        self.assertIn("down", page)                           # the error of each query
        st = self.status()
        self.assertEqual((st["status"], st["exit_code"], st["run"], st["page"]),
                         ("failed", lit_watch.EXIT_LOST_ALL, None, Path(res["digest"]).name))
        self.assertIn("todas las consultas", st["error"])
        self.assertEqual(lit_watch.load_cursors(self.p), {})
        self.assertIn("last_watch: 2031-02-01", (self.p / "_hub.md").read_text(encoding="utf-8"))
        # the next run on the same day gets its own name: the failure page stays
        code, res2 = self.delta()
        self.assertEqual(code, 0, res2)
        self.assertNotEqual(res2["digest"], res["digest"])
        self.assertEqual(self.status()["status"], "ok")

    def test_status_says_ok_or_degraded_with_counts_and_page(self):
        _, res = self.delta()
        st = self.status()
        self.assertEqual((st["status"], st["exit_code"], st["date"]), ("ok", 0, TODAY.isoformat()))
        self.assertEqual(st["page"], Path(res["digest"]).name)
        self.assertEqual(st["counts"]["strong"], res["strong"])
        self.delta(base.FakeNet(fail={"api.semanticscholar.org"}))
        st = self.status()
        self.assertEqual(st["status"], "degraded")
        self.assertEqual(len(st["lost_queries"]), 1)

    def test_a_refused_watch_leaves_a_failed_status(self):
        self.delta()
        (self.p / "_vigilancia" / "cursores.json").write_text("{not json", encoding="utf-8")
        code, _ = self.delta()
        self.assertEqual(code, 2)
        self.assertEqual(self.status()["status"], "failed")
        self.assertIn("cursores.json", self.status()["error"])

    def test_missing_keys_are_said_on_the_page(self):
        with mock.patch.dict(lit_watch.os.environ, {}, clear=False):
            lit_watch.os.environ.pop("SEMANTIC_SCHOLAR_API_KEY", None)
            _, res = self.delta()
        page = (self.vault / res["digest"]).read_text(encoding="utf-8")
        self.assertIn("SEMANTIC_SCHOLAR_API_KEY", page)


class OfferedMeansRead(Base):
    def keys(self, res) -> dict:
        return {c["key"]: c for c in json.loads(self.run_file(res).read_text(encoding="utf-8"))["candidates"]}

    def test_picked_for_triage_but_never_read_comes_back(self):
        _, first = self.delta()
        self.assertTrue(self.keys(first)["arxiv:2031.00002"]["triage"])
        _, second = self.delta()                               # nobody wrote its line
        again = self.keys(second)
        self.assertIn("arxiv:2031.00002", again)
        self.assertEqual(again["arxiv:2031.00002"]["pendiente_desde"], Path(first["run"]).name)
        code, _ = self.cli("triage", "--project-dir", str(self.p), "--run", str(self.run_file(second)),
                           "--key", "arxiv:2031.00002", "--why", "Línea inventada.")
        self.assertEqual(code, 0)
        _, third = self.delta()
        self.assertNotIn("arxiv:2031.00002", self.keys(third))

    def test_this_weeks_strong_papers_take_the_slots_before_the_backlog(self):
        week = {"n": 1}

        def fetch(url, headers):
            if "arxiv" in url:
                n = week["n"]
                return base.atom([(f"2031.0{n}01{i}", f"Widgets week {n} paper {i}",
                                   "Fictional widgets under synthetic spin.") for i in range(2)]
                                 ).replace(b"2031-02-10", b"2031-03-05" if n == 2 else b"2031-02-10")
            return json.dumps({"data": []}).encode()
        self.plan(sources=("arxiv",))
        _, first = self.delta_on(TODAY, "--top", "1", fetch=fetch)
        k1 = self.keys(first)
        picked = [k for k, c in k1.items() if c["triage"]]
        left = [k for k, c in k1.items() if c["strong"] and not c["triage"]]
        code, _ = self.cli("triage", "--project-dir", str(self.p), "--run", str(self.run_file(first)),
                           "--key", picked[0], "--why", "Línea inventada.")
        week["n"] = 2
        _, second = self.delta_on(date(2031, 3, 8), "--top", "1", fetch=fetch)
        k2 = self.keys(second)
        self.assertTrue(k2[left[0]]["pendiente_desde"])
        self.assertFalse(k2[left[0]]["triage"])                # the backlog waits behind this week's
        self.assertTrue(any(c["triage"] and not c.get("pendiente_desde") for c in k2.values()))

    def test_a_run_whose_page_was_not_written_offers_nothing(self):
        with mock.patch.object(lit_watch, "write_digest", side_effect=OSError("disco inventado lleno")):
            code, out = self.delta()
        self.assertEqual(code, 1, out)
        self.assertFalse(list((self.p / "_vigilancia").glob("vigilancia-*.json")))
        self.assertEqual(lit_watch.load_cursors(self.p), {})
        _, res = self.delta()
        self.assertIn("arxiv:2031.00002", self.keys(res))


if __name__ == "__main__":
    unittest.main()
