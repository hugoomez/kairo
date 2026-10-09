"""lit_watch: the novelty prefilter keeps short acronyms written in capitals and
meets a Spanish claim with an English abstract (invented words and text only)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lit_watch  # noqa: E402
import test_lit_watch as base  # noqa: E402
import test_lit_watch_state as st  # noqa: E402


class Words(unittest.TestCase):
    def test_short_acronyms_in_capitals_are_kept(self):
        w = lit_watch.words("Toy kernels on HPC nodes with GPUs and MPI")
        self.assertTrue({"hpc", "gpu", "mpi"} <= w, w)
        self.assertNotIn("and", w)
        self.assertNotIn("on", lit_watch.words("ON AND OFF toy kernels"))

    def test_spanish_and_english_forms_share_a_stem(self):
        pairs = [("circuitos", "circuits"), ("tensoriales", "tensors"), ("simulaciones", "simulations"),
                 ("electrónicos", "electronic"), ("computacional", "computational"), ("widgets", "widget")]
        for es, en in pairs:
            with self.subTest(es=es):
                self.assertEqual(lit_watch.words(es), lit_watch.words(en))

    def test_a_spanish_claim_meets_an_english_abstract(self):
        claim = lit_watch.words("Los circuitos tensoriales ficticios se simulan en GPU sin MPI.")
        abstract = lit_watch.words("We run invented tensor circuits on GPUs without MPI.")
        self.assertGreaterEqual(len(claim & abstract), 3, (claim, abstract))


class PrefilterInADelta(st.Base):
    def test_the_judge_sees_a_pair_that_only_shares_spanish_forms_and_acronyms(self):
        (self.p / "Hipotesis" / "H-0964.md").write_text(
            base.hyp("H-0964", "propuesta", "Los circuitos tensoriales inventados escalan en GPU con MPI."),
            encoding="utf-8")

        class Net(base.FakeNet):
            def __call__(self, url, headers):
                self.urls.append(url)
                if "arxiv" in url:
                    return base.atom([("2031.00777", "Invented tensor circuits at scale",
                                       "Invented tensor circuits scale on GPUs with MPI.")])
                return json.dumps({"data": []}).encode()
        _, res = self.delta(Net())
        run = json.loads(self.run_file(res).read_text(encoding="utf-8"))
        c = next(c for c in run["candidates"] if c["key"] == "arxiv:2031.00777")
        self.assertIn("H-0964", [n["hypothesis"] for n in c["novelty"]])


if __name__ == "__main__":
    unittest.main()
