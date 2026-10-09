"""lit_watch: a watch reads the newest papers first, starts near today, and a busy
query never stops new weeks being covered — the older part it could not read
stays open, said so (invented ids and text only)."""

from __future__ import annotations

import json
import re
import sys
import unittest
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lit_watch  # noqa: E402
import test_lit_watch as base  # noqa: E402
import test_lit_watch_state as st  # noqa: E402


class Busy(base.FakeNet):
    """An arXiv query with 200 matches a day, newest first: any window longer than
    two days is capped at 500."""

    def __call__(self, url, headers):
        self.urls.append(url)
        if "arxiv" not in url:
            return json.dumps({"total": 0, "data": []}).encode()
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        q = qs["search_query"][0]
        start = int(qs["start"][0])
        a, b = re.search(r"submittedDate:\[(\d{8})0000 TO (\d{8})2359\]", q).groups()
        hi = date(int(b[:4]), int(b[4:6]), int(b[6:]))
        lo = date(int(a[:4]), int(a[4:6]), int(a[6:]))
        ents = []
        for i in range(start, start + 100):
            d = hi - timedelta(days=i // 200)             # 200 papers a day, newest first
            if d < lo:
                break
            ents.append(f"<entry><id>http://arxiv.org/abs/2031.{d.toordinal() % 100:02d}{i % 200:03d}v1</id>"
                        f"<title>Busy widget paper {d} {i}</title><summary>Fictional widgets under synthetic spin."
                        f"</summary><published>{d}T00:00:00Z</published><author><name>A B</name></author></entry>")
        return ('<feed xmlns="http://www.w3.org/2005/Atom" xmlns:o="http://a9.com/-/spec/opensearch/1.1/">'
                f"<o:totalResults>{200 * ((hi - lo).days + 1)}</o:totalResults>{''.join(ents)}</feed>").encode()


def arxiv_windows(net) -> list[tuple[str, str]]:
    out = []
    for u in net.urls:
        if "arxiv" in u and "search_query" in u and "start=0&" in u:
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(u).query)["search_query"][0]
            out.append(re.search(r"submittedDate:\[(\d{8})0000 TO (\d{8})2359\]", q).groups())
    return out


class NewestFirst(st.Base):
    def test_a_watch_pages_by_date_newest_first(self):
        self.plan(sources=("arxiv", "openalex"))
        net = base.FakeNet()
        self.delta(net)
        self.assertTrue(all("sortBy=submittedDate" in u for u in net.urls if "export.arxiv.org" in u
                            and "search_query" in u))
        self.assertTrue(all("sort=publication_date:desc" in u for u in net.urls if "works?search=" in u))

    def test_a_busy_query_is_covered_up_to_today_and_its_older_part_stays_open(self):
        self.plan(sources=("arxiv",))
        code, res = self.delta(Busy())
        self.assertEqual(code, 0, res)
        cursors = lit_watch.load_cursors(self.p)
        self.assertTrue(cursors and all(v == st.TODAY.isoformat() for v in cursors.values()), cursors)
        gaps = lit_watch.load_gaps(self.p)
        self.assertTrue(gaps)
        lo, hi = next(iter(gaps.values()))
        self.assertLess(lo, hi)
        self.assertIn(lo, res["open_windows"])
        page = (self.vault / res["digest"]).read_text(encoding="utf-8")
        self.assertIn("más recientes; quedan sin leer los anteriores a", page)
        self.assertNotIn("sigue abierta", page)
        # next week: this week's window from the cursor (14 days' overlap), and the old gap re-read
        net = Busy()
        code, nxt = self.delta_on(date(2031, 3, 8), fetch=net)
        self.assertEqual(code, 0, nxt)
        wins = arxiv_windows(net)
        self.assertIn(("20310215", "20310308"), wins)
        self.assertTrue(any(w[0] == lo.replace("-", "") for w in wins), wins)
        self.assertTrue(all(v == "2031-03-08" for v in lit_watch.load_cursors(self.p).values()))
        lo2, _ = next(iter(lit_watch.load_gaps(self.p).values()))
        self.assertEqual(lo2, lo)
        data = json.loads(self.run_file(nxt).read_text(encoding="utf-8"))
        reread = [x for x in data["queries"] if x.get("relectura_hueco")]
        self.assertTrue(reread)
        self.assertTrue(all(x["gap"][0] == lo and x["gap"][1] < hi for x in reread))  # read from its newest end


class AWatchStartsNearToday(unittest.TestCase):
    setUp = base.TestTopicWatch.setUp
    tearDown = base.TestTopicWatch.tearDown
    cli = base.TestTopicWatch.cli

    def test_init_starts_the_watch_a_lookback_ago_not_at_the_plans_history(self):
        self.plan.write_text(self.plan.read_text(encoding="utf-8").replace('"2031-02-01"', '"2019-01-01"'),
                             encoding="utf-8")
        code, out = self.cli("init", "--vault", str(self.vault), "--slug", "w", "--plan", str(self.plan))
        self.assertEqual(code, 0, out)
        start = (base.TODAY - timedelta(days=lit_watch.DEFAULT_LOOKBACK_DAYS)).isoformat()
        self.assertEqual(out["last_watch"], start)
        hub = (self.vault / "Projects" / "w" / "_hub.md").read_text(encoding="utf-8")
        self.assertIn(f"last_watch: {start}", hub)

    def test_init_since_sets_it_explicitly(self):
        code, out = self.cli("init", "--vault", str(self.vault), "--slug", "w", "--plan", str(self.plan),
                             "--since", "2030-12-01")
        self.assertEqual((code, out["last_watch"]), (0, "2030-12-01"), out)


if __name__ == "__main__":
    unittest.main()
