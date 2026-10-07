"""lit_search: what a candidate says about where its data came from survives a
snowball (invented ids and text only)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lit_search as ls  # noqa: E402

PLAN = {"description": "invented request", "facets": [{"id": "A", "term": "toy code"},
                                                     {"id": "B", "term": "toy decoder"}],
        "sources": ["arxiv"], "from": "2030-01-01"}


def candidate() -> dict:
    return {"key": "doi:10.9999/toy.1", "title": "A toy decoder for toy codes, retitled", "authors": ["Ada Inventa"],
            "year": 2030, "years": [2030], "date": "2030-02-01", "doi": "10.9999/toy.1", "arxiv": "2999.00002",
            "venues": ["Invented Journal"], "abstract": "We give a toy decoder for toy codes.",
            "abstract_fuente": "openalex W1 (completado por DOI)",
            "merged_by": "título aproximado (0.90) + primer autor",
            "citations": 1, "url": "https://doi.org/10.9999/toy.1", "facets": {"A": "toy code", "B": "toy decoder"},
            "sources": ["arxiv", "crossref"], "queries": ["Q001"], "anchor": False, "best_rank": 1}


class SnowballKeepsProvenance(unittest.TestCase):
    def test_abstract_source_and_merge_reason_survive_a_snowball(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            (run / "raw").mkdir()
            ls.save(run / "plan.json", ls.load_plan_dict(PLAN))
            ls.save(run / "queries.json", [])
            ls.save(run / "candidates.json", [candidate()])

            def fetch(url, headers):
                return json.dumps({"data": [], "next": None}).encode()
            ls.cmd_snowball(run, ["doi:10.9999/toy.1"], "references", fetch)
            got = {c["key"]: c for c in ls.load(run / "candidates.json")}["doi:10.9999/toy.1"]
            self.assertEqual(got.get("abstract_fuente"), "openalex W1 (completado por DOI)")
            self.assertEqual(got.get("merged_by"), "título aproximado (0.90) + primer autor")


if __name__ == "__main__":
    unittest.main()
