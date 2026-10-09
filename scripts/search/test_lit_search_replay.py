"""lit_search replay: a run's identification rebuilt from its kept raw/ files with
the current parsers, no network — the same candidates, or a parser fix applied
to the same responses (invented works only)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lit_search as ls  # noqa: E402
from test_lit_search import Base, FakeWeb  # noqa: E402


def no_network(url, headers):
    raise AssertionError(f"replay went to the network: {url}")


class Replay(Base):
    def replay(self, out):
        return self.cli("replay", "--run", str(self.run_dir), "--out", str(out), web=no_network)

    def test_replay_rebuilds_the_same_run_without_network(self):
        self.run_search()
        out = self.tmp / "again"
        code, res = self.replay(out)
        self.assertEqual(code, 0, res)
        a, b = ls.load(self.run_dir / "candidates.json"), ls.load(out / "candidates.json")
        self.assertEqual([c["key"] for c in a], [c["key"] for c in b])
        self.assertEqual(a, b)
        qa, qb = ls.load(self.run_dir / "queries.json"), ls.load(out / "queries.json")
        self.assertEqual([(q["id"], q["fetched"], q.get("error")) for q in qa],
                         [(q["id"], q["fetched"], q.get("error")) for q in qb])
        self.assertEqual([r["sha256"] for q in qa for r in q["raw"]], [r["sha256"] for q in qb for r in q["raw"]])
        self.assertEqual((self.run_dir / "plan.json").read_bytes(), (out / "plan.json").read_bytes())
        self.assertEqual(ls.load(self.run_dir / "vocabulario.json"), ls.load(out / "vocabulario.json"))
        self.assertEqual(ls.load(out / "replay.json")["from"], self.run_dir.as_posix())

    def test_a_parser_fix_is_reapplied_to_the_kept_responses(self):
        self.run_search()
        orig = ls.PARSERS["crossref"]

        def fixed(raw):
            recs, total = orig(raw)
            return [r for r in recs if "hardware" not in r["title"]], total
        with mock.patch.dict(ls.PARSERS, {"crossref": fixed}):
            code, res = self.replay(self.tmp / "fixed")
        self.assertEqual(code, 0, res)
        before = [c for c in ls.load(self.run_dir / "candidates.json") if "hardware" in c["title"]]
        after = [c for c in ls.load(self.tmp / "fixed" / "candidates.json") if "hardware" in c["title"]]
        self.assertTrue(before)
        self.assertEqual(after, [])

    def test_a_lost_query_stays_lost_with_its_error(self):
        self.run_search(FakeWeb(fail_hosts=("api.crossref.org",)))
        code, res = self.replay(self.tmp / "again")
        self.assertEqual(code, 0, res)
        lost = [q for q in ls.load(self.tmp / "again" / "queries.json") if q["source"] == "crossref"]
        self.assertTrue(lost and all("429" in q["error"] for q in lost))

    def test_snowball_queries_are_named_not_replayed(self):
        self.run_search()
        key = next(c["key"] for c in ls.load(self.run_dir / "candidates.json") if c.get("arxiv") == "0000.00003")
        self.cli("snowball", "--run", str(self.run_dir), "--keys", key, "--direction", "references")
        code, res = self.replay(self.tmp / "again")
        self.assertEqual(code, 0, res)
        self.assertTrue(res["snowball_no_reproducido"])

    def test_replay_refuses_a_used_directory(self):
        self.run_search()
        (self.tmp / "used").mkdir()
        (self.tmp / "used" / "x").write_text("x", encoding="utf-8")
        self.assertEqual(self.replay(self.tmp / "used")[0], 2)
        self.assertTrue(json.dumps(ls.load(self.run_dir / "plan.json")))
