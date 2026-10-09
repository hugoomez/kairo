"""lit_search.enrich_abstracts: a lost OpenAlex lookup (a spent keyless budget
answers 429) stops the lookups instead of retrying every chunk, and says how
many were not looked up; a caller can restrict it to the candidates that
matter (invented records only)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "citations"))
import lit_search as ls  # noqa: E402
import net  # noqa: E402


def cands(n: int) -> list[dict]:
    return [{"key": f"doi:10.9999/x.{i}", "doi": f"10.9999/x.{i}", "title": f"Invented {i}", "abstract": ""}
            for i in range(n)]


class Enrich(unittest.TestCase):
    def test_a_lost_chunk_stops_the_lookups(self):
        calls = []

        def fetch(url, headers):
            calls.append(url)
            raise net.HttpError(url, 429, "Too Many Requests")
        queries: list[dict] = []
        with tempfile.TemporaryDirectory() as d:
            ls.enrich_abstracts(cands(160), {}, Path(d), queries, fetch)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(queries), 1)
        self.assertTrue(queries[0]["error"])
        self.assertEqual(queries[0]["not_looked_up"], 110)

    def test_only_the_selected_candidates_are_looked_up(self):
        calls = []

        def fetch(url, headers):
            calls.append(url)
            return b'{"results": []}'
        cs = cands(120)
        with tempfile.TemporaryDirectory() as d:
            ls.enrich_abstracts(cs, {}, Path(d), [], fetch, only=lambda c: c["doi"].endswith(".7"))
        self.assertEqual(len(calls), 1)
        self.assertIn("10.9999/x.7", calls[0])
        self.assertNotIn("10.9999/x.8", calls[0])


if __name__ == "__main__":
    unittest.main()
