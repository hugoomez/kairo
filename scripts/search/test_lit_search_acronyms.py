"""lit_search: a facet term that is an acronym (two or more capitals as written:
ZeRO, DP, MoE, BP-OSD) matches only in its own case, as a whole word — the
sources' case-insensitive search brings "zero" and "dp" too (invented text)."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lit_search as ls  # noqa: E402

PLAN = ls.load_plan_dict({"description": "invented acronym question",
                          "facets": [{"id": "A", "term": "ZeRO", "synonyms": ["zero redundancy optimizer"]},
                                     {"id": "B", "term": "DP", "synonyms": ["MoE", "BP-OSD decoder"]}],
                          "sources": ["arxiv"]})


def c(title: str, abstract: str = "") -> dict:
    return {"title": title, "abstract": abstract}


class CaseSensitive(unittest.TestCase):
    def test_an_acronym_matches_in_its_own_case_only(self):
        self.assertEqual(ls.facet_matches(c("ZeRO-3 offload for invented toys"), PLAN), {"A": "ZeRO"})
        self.assertEqual(ls.facet_matches(c("Zero-shot invented toys at zero cost"), PLAN), {})
        self.assertEqual(ls.facet_matches(c("ZERO toys"), PLAN), {})

    def test_whole_word_with_a_plural(self):
        self.assertEqual(ls.facet_matches(c("Invented DPs and MoEs"), PLAN), {"B": "DP"})
        self.assertEqual(ls.facet_matches(c("An invented dp toy"), PLAN), {})
        self.assertEqual(ls.facet_matches(c("An invented ADP toy"), PLAN), {})
        self.assertEqual(ls.facet_matches(c("Invented moe toys"), PLAN), {})
        self.assertEqual(ls.facet_matches(c("Invented MoE toys"), PLAN), {"B": "MoE"})

    def test_a_hyphenated_acronym_in_a_phrase(self):
        self.assertEqual(ls.facet_matches(c("A BP-OSD decoder for toys"), PLAN), {"B": "BP-OSD decoder"})
        self.assertEqual(ls.facet_matches(c("A BP OSD decoders study"), PLAN), {"B": "BP-OSD decoder"})
        self.assertEqual(ls.facet_matches(c("A bp-osd decoder for toys"), PLAN), {})

    def test_a_long_form_still_matches_in_any_case(self):
        self.assertEqual(ls.facet_matches(c("The Zero Redundancy Optimizer, invented"), PLAN),
                         {"A": "zero redundancy optimizer"})

    def test_term_hits_count_the_exact_case(self):
        hits = ls.term_hits([c("zero toys"), c("ZeRO toys"), c("dp toys")], PLAN)
        self.assertEqual((hits["A"]["ZeRO"], hits["B"]["DP"]), (1, 0))


class QueryCredit(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_an_arxiv_hit_showing_the_acronym_only_case_folded_gets_no_credit(self):
        plan = ls.load_plan_dict({**PLAN, "facets": [{"id": "A", "term": "ZeRO"}], "cross": False})
        q = ls.build_queries(plan, "2030-12-31")[0]
        feed = ('<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
                "<opensearch:totalResults>2</opensearch:totalResults>"
                "<entry><id>http://arxiv.org/abs/3001.00001v1</id><published>2030-01-01T00:00:00Z</published>"
                "<title>Zero-shot invented toys</title><summary>At zero cost.</summary></entry>"
                "<entry><id>http://arxiv.org/abs/3001.00002v1</id><published>2030-01-01T00:00:00Z</published>"
                "<title>ZeRO for invented toys</title><summary>Offload.</summary></entry></feed>").encode()
        recs = ls.run_query(q, plan, self.tmp, lambda u, h: feed)
        self.assertEqual([(r["arxiv"], r["facet"]) for r in recs], [("3001.00001", None), ("3001.00002", "A")])


class Ambiguous(unittest.TestCase):
    def test_an_acronym_seen_mostly_in_another_case_is_listed(self):
        cands = [c(f"Zero-shot invented toy {i}") for i in range(6)] + [c("ZeRO invented toy")]
        amb = ls.ambiguous_terms(cands, PLAN)
        self.assertEqual([(a["facet"], a["term"]) for a in amb], [("A", "ZeRO")])
        self.assertEqual((amb[0]["forma_exacta"], amb[0]["otra_forma"]), (1, 6))

    def test_run_writes_them_in_the_vocabulary(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "plan.json").write_text(json.dumps({**PLAN, "facets": [{"id": "A", "term": "ZeRO"}],
                                                       "enrich": False}), encoding="utf-8")
            entries = "".join(
                f"<entry><id>http://arxiv.org/abs/3001.0000{i}v1</id><published>2030-01-01T00:00:00Z</published>"
                f"<title>Zero invented toy {i}</title><summary>zero</summary></entry>" for i in range(5))
            feed = ('<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/'
                    f'1.1/"><opensearch:totalResults>5</opensearch:totalResults>{entries}</feed>').encode()
            out = ls.cmd_run(tmp / "plan.json", tmp / "run", None, lambda u, h: feed, "2030-12-31")
            self.assertEqual([a["term"] for a in out["terminos_ambiguos"]], ["ZeRO"])
            vocab = ls.load(tmp / "run" / "vocabulario.json")
            self.assertEqual([a["term"] for a in vocab["terminos_ambiguos"]], ["ZeRO"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
