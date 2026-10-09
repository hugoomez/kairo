"""lit_watch: the watch follows the plan it recorded, never a later one silently,
and a legacy arXiv row with its own date clause is still windowed (invented
ids and text only)."""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lit_watch  # noqa: E402
import test_lit_watch as base  # noqa: E402
import test_lit_watch_state as st  # noqa: E402


class FollowsItsPlan(st.Base):
    def newer(self, term: str = "toy gizmos") -> Path:
        run = self.p / "_busquedas" / "2031-02-20"
        run.mkdir(parents=True)
        (run / "plan.json").write_text(json.dumps({
            "description": "una búsqueda puntual inventada", "facets": [{"id": "Z", "term": term, "synonyms": []}],
            "sources": ["arxiv"], "from": "2020-01-01", "per_query": 100, "anchors": 0, "cross": False,
            "arxiv_categories": [], "include": [], "exclude": [], "scope_out": []}), encoding="utf-8")
        return run / "plan.json"

    def followed(self) -> dict:
        return json.loads((self.p / "_vigilancia" / lit_watch.PLAN_FOLLOWED).read_text(encoding="utf-8"))

    def test_the_first_watch_records_its_plan(self):
        plan = self.plan()
        code, res = self.delta()
        self.assertEqual(code, 0, res)
        rec = self.followed()
        self.assertEqual(rec["plan"], "_busquedas/2031-01-15/plan.json")
        self.assertEqual(rec["sha256"], hashlib.sha256(plan.read_bytes()).hexdigest())

    def test_a_later_one_off_search_is_not_followed_silently(self):
        self.plan()
        self.delta()
        self.newer()
        net = base.FakeNet()
        code, res = self.delta(net)
        self.assertEqual(code, 0, res)
        data = json.loads(self.run_file(res).read_text(encoding="utf-8"))
        self.assertEqual(data["queries_from"], "_busquedas/2031-01-15")
        self.assertEqual(res["newer_plan"], "_busquedas/2031-02-20/plan.json")
        self.assertFalse(any("gizmos" in urllib.parse.unquote(u) for u in net.urls))
        page = (self.vault / res["digest"]).read_text(encoding="utf-8")
        self.assertIn("plan más reciente", page)
        self.assertIn("--adopt-plan", page)

    def test_adopting_the_newer_plan_is_explicit(self):
        self.plan()
        self.delta()
        self.newer()
        net = base.FakeNet()
        code, res = self.cli("delta", "--vault", str(self.vault), "--project-dir", str(self.p),
                             "--adopt-plan", "_busquedas/2031-02-20", fetch=net)
        self.assertEqual(code, 0, res)
        self.assertTrue(any("gizmos" in urllib.parse.unquote(u) for u in net.urls))
        self.assertEqual(self.followed()["plan"], "_busquedas/2031-02-20/plan.json")
        self.assertIsNone(res["newer_plan"])

    def test_an_edited_followed_plan_is_refused(self):
        plan = self.plan()
        self.delta()
        plan.write_text(plan.read_text(encoding="utf-8").replace("fictional widgets", "edited widgets"),
                        encoding="utf-8")
        code, res = self.delta()
        self.assertEqual(code, 2, res)
        self.assertIn("plan.json", res["error"])
        self.assertIn("--adopt-plan", res["error"])


class LegacyRowWithItsOwnDates(st.Base):
    def test_a_row_with_its_own_submitted_date_is_windowed_by_the_watch(self):
        sota = base.SOTA.replace('`abs:"fictional widgets"`',
                                 '`abs:"fictional widgets" AND submittedDate:[202001010000 TO 202012312359]`')
        (self.p / "Estado-del-arte.md").write_text(sota, encoding="utf-8")
        qs = lit_watch.recorded_queries(self.p)
        self.assertEqual(qs[0]["query"], 'abs:"fictional widgets"')
        self.assertIn("submittedDate", qs[0]["fecha_quitada"])
        net = base.FakeNet()
        code, res = self.delta(net)
        self.assertEqual(code, 0, res)
        url = urllib.parse.unquote(next(u for u in net.urls if "arxiv" in u))
        self.assertEqual(url.count("submittedDate:["), 1)
        page = (self.vault / res["digest"]).read_text(encoding="utf-8")
        self.assertIn("submittedDate", page)

    def test_a_row_that_is_only_a_date_clause_is_not_run(self):
        sota = base.SOTA.replace('`abs:"fictional widgets"`', '`submittedDate:[202001010000 TO 202012312359]`')
        (self.p / "Estado-del-arte.md").write_text(sota, encoding="utf-8")
        self.assertEqual([q["source"] for q in lit_watch.recorded_queries(self.p)], ["s2"])


if __name__ == "__main__":
    unittest.main()
