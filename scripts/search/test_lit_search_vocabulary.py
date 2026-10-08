"""lit_search: compounds written as one word or two, and the synonyms a plan is
missing, found in the candidates' own abstracts (invented text only)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lit_search as ls  # noqa: E402


def plan(*facets):
    return ls.load_plan_dict({"description": "invented",
                              "facets": [{"id": chr(65 + i), "term": t, "synonyms": list(s)}
                                         for i, (t, *s) in enumerate(facets)]})


class Compounds(unittest.TestCase):
    def test_one_word_and_two_words_are_the_same_term(self):
        p = plan(("state vector simulation",), ("dataset",))
        c = {"title": "Distributed statevector simulations of toy circuits", "abstract": "We use a new data set."}
        self.assertEqual(set(ls.facet_matches(c, p)), {"A", "B"})
        p2 = plan(("statevector",))
        self.assertEqual(ls.facet_matches({"title": "A state-vector toy simulator", "abstract": ""}, p2),
                         {"A": "statevector"})

    def test_unrelated_words_still_do_not_match(self):
        p = plan(("statevector",), ("tensor",))
        self.assertEqual(ls.facet_matches({"title": "A stated vectorial tension", "abstract": ""}, p), {})


class Suggestions(unittest.TestCase):
    def test_an_acronym_defined_in_abstracts_is_suggested_for_its_facet(self):
        p = plan(("MoE",), ("toy cluster",))
        cands = [{"title": "Toy routing", "abstract": "We train a mixture-of-experts (MoE) toy model on a toy cluster."},
                 {"title": "More toys", "abstract": "Sparse Mixture of Experts (MoEs) scale on a toy cluster."},
                 {"title": "Other", "abstract": "We use the Message Passing Interface (MPI)."}]
        [s] = ls.suggest_synonyms(cands, p)
        self.assertEqual((s["facet"], ls.norm_title(s["suggest"]), s["abstracts"]), ("A", "mixture of experts", 2))

    def test_an_expansion_listed_suggests_its_acronym_and_nothing_when_both_are(self):
        p = plan(("tensor network", "toy"))
        cands = [{"title": "x", "abstract": "Contracting a tensor network (TN) of toys."}]
        self.assertEqual([s["suggest"] for s in ls.suggest_synonyms(cands, p)], ["TN"])
        self.assertEqual(ls.suggest_synonyms(cands, plan(("tensor network", "TN"))), [])

    def test_term_hits_name_the_terms_no_candidate_shows(self):
        p = plan(("toy code", "invented code"))
        hits = ls.term_hits([{"title": "A toy code", "abstract": ""}], p)
        self.assertEqual(hits, {"A": {"toy code": 1, "invented code": 0}})


if __name__ == "__main__":
    unittest.main()
