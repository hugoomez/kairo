"""lit_search.stem: a word ending in -ss or in the Greek -is shares its stem with
its plural ("class" ~ "classes", "analysis" ~ "analyses")."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lit_search as ls  # noqa: E402


class Stem(unittest.TestCase):
    def test_ss_and_is_plurals_share_a_stem(self):
        for fam in [("class", "classes"), ("process", "processes"), ("analysis", "analyses"),
                    ("hypothesis", "hypotheses"), ("basis", "bases"), ("synthesis", "syntheses"),
                    ("access", "accesses")]:
            self.assertEqual(len({ls.stem(w) for w in fam}), 1, {w: ls.stem(w) for w in fam})

    def test_the_older_families_hold(self):
        self.assertEqual(ls.stem("precision"), ls.stem("precise"))
        self.assertEqual(ls.stem("codes"), ls.stem("code"))
        self.assertNotEqual(ls.stem("class"), ls.stem("clash"))

    def test_a_facet_term_finds_the_plural(self):
        plan = {"facets": [{"id": "A", "term": "error analysis", "synonyms": []}]}
        self.assertEqual(ls.facet_matches({"title": "Invented error analyses of toys", "abstract": ""}, plan),
                         {"A": "error analysis"})


if __name__ == "__main__":
    unittest.main()
