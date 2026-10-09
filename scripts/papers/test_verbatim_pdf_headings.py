"""PDF headings follow the paper's own numbering (synthetic pdftotext text, invented content).

A table cell ("64 GPUs"), a wrapped two-column line ("A GPU node hosts …") or a
supplement that restarts its numbering after the references must not open a
section of the main text.
"""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "ledger"))
import verbatim_fulltext as vf  # noqa: E402

P = "is an invented sentence that is long enough to count as prose for this extractor."

HPC = f"""1 Introduction
The toy scheduler {P}
2 Background
The toy background {P}
3 System Design
The toy design {P}
3.1 Overview
The toy overview {P}
Table 1: Throughput of the invented scheduler.
Nodes Throughput
64 GPUs
1.9
2 GPUs
0.1
128 GPUs
3.7
3.2 Partitioning
The toy partitioning {P}
A GPU node hosts eight accelerators
4 Evaluation
The toy evaluation {P}
16 Nodes
5 Results
5.1 Weak scaling
The toy weak scaling {P}
5.2 Strong scaling
The first strong scaling paragraph {P}
12 Workers
The second strong scaling paragraph {P}
7 Not a section because it skips numbers
The third strong scaling paragraph {P}
References
[1] BIBENTRY.
1 Supplementary experiments
The toy supplement {P}
1.1 Extra runs
The toy extra runs {P}
2 More tables
The toy more tables {P}
"""

IEEE = f"""I. INTRODUCTION
The toy introduction {P}
II. SYSTEM
A. Data Parallelism
The toy data parallelism {P}
A GPU node hosts eight accelerators
and the toy wrapped line continues the paragraph that started above it here.
B. Tensor Parallelism
The toy tensor parallelism {P}
III. EVALUATION
The toy evaluation {P}
REFERENCES
[1] BIBENTRY.
"""


def heads(body: str) -> list[str]:
    return [ln for ln in body.splitlines() if ln.startswith("#")]


class NumberedHeadings(unittest.TestCase):
    def setUp(self):
        self.body = vf.pdftext_to_body(HPC)

    def test_only_headings_in_sequence_open_a_section(self):
        self.assertEqual(heads(self.body), [
            "### 1 Introduction", "### 2 Background", "### 3 System Design", "#### 3.1 Overview",
            "#### 3.2 Partitioning", "### 4 Evaluation", "### 5 Results", "#### 5.1 Weak scaling",
            "#### 5.2 Strong scaling", "### Material suplementario", "#### S1 Supplementary experiments",
            "##### S1.1 Extra runs", "#### S2 More tables"])

    def test_table_rows_stay_under_their_caption(self):
        import verifier_packet as vp
        units = vp.source_units(self.body)
        tab = [u for u in units if vp.unit_matches(u, ("table", "1"))]
        self.assertTrue(tab and "128 GPUs" in tab[0]["text"] and "64 GPUs" in tab[0]["text"], tab)

    def test_a_subsection_keeps_all_its_paragraphs(self):
        import verifier_packet as vp
        units = [u for u in vp.source_units(self.body) if vp.unit_matches(u, ("sec", "5.2"), inline_ok=False)]
        text = "\n".join(u["text"] for u in units)
        for part in ("first strong", "second strong", "third strong"):
            self.assertIn(part, text)
        self.assertNotIn("toy supplement", text)

    def test_the_supplement_does_not_attach_to_the_main_text(self):
        import verifier_packet as vp
        units = vp.source_units(self.body)
        sec1 = "\n".join(u["text"] for u in units if vp.unit_matches(u, ("sec", "1"), inline_ok=False))
        self.assertIn("toy scheduler", sec1)
        self.assertNotIn("toy supplement", sec1)
        self.assertNotIn("BIBENTRY", self.body)

    def test_the_older_rules_are_kept_for_notes_they_wrote(self):
        old = vf.pdftext_to_body(HPC, legacy=True)
        self.assertIn("### 64 GPUs", old)                       # what 1.5.0 wrote, reproduced exactly


class RomanHeadings(unittest.TestCase):
    def test_a_wrapped_line_is_not_an_appendix(self):
        body = vf.pdftext_to_body(IEEE)
        self.assertEqual(heads(body), ["### I. INTRODUCTION", "### II. SYSTEM", "#### A. Data Parallelism",
                                       "#### B. Tensor Parallelism", "### III. EVALUATION"])
        self.assertIn("### Appendix A", vf.pdftext_to_body(IEEE, legacy=True))

    def test_appendices_after_the_references_still_open(self):
        body = vf.pdftext_to_body(IEEE + "A Proofs of the toy claims\nThe toy proof " + P + "\n"
                                  "B Further toy results\nThe further toy results " + P + "\n")
        self.assertEqual(heads(body)[-2:], ["### Appendix A: Proofs of the toy claims",
                                            "### Appendix B: Further toy results"])


if __name__ == "__main__":
    unittest.main()
