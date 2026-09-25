"""Tests for verbatim_fulltext.py (synthetic fixtures, no network).

Run: python -m unittest test_verbatim_fulltext   (from scripts/papers/)
"""

import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verbatim_fulltext as vf  # noqa: E402

HTML = """<html><body><div class="ltx_page_main"><article class="ltx_document">
<h1 class="ltx_title ltx_title_document">A Title</h1>
<div class="ltx_authors">Jane Doe, jane@x.org</div>
<div class="ltx_abstract"><p class="ltx_p">ABSTRACTTEXT should not appear.</p></div>
<section class="ltx_section"><h2 class="ltx_title ltx_title_section"><span class="ltx_tag ltx_tag_section">1 </span>Introduction</h2>
<div class="ltx_para"><p class="ltx_p">We train on <math alttext="50\\%">x</math> of the data
<span class="ltx_note ltx_role_footnote"><sup class="ltx_note_mark">1</sup><span class="ltx_note_outer"><span class="ltx_note_content"><sup class="ltx_note_mark">1</sup><span class="ltx_note_type">footnote: </span>A footnote.</span></span></span>
and <math>broken</math> here.</p></div>
<ul class="ltx_itemize"><li class="ltx_item"><span class="ltx_tag">•</span><div class="ltx_para"><p class="ltx_p">A bullet point.</p></div></li></ul>
</section>
<section class="ltx_section"><h2 class="ltx_title ltx_title_section"><span class="ltx_tag ltx_tag_section">3 </span>Experiments</h2>
<section class="ltx_subsection"><h3 class="ltx_title ltx_title_subsection"><span class="ltx_tag ltx_tag_subsection">3.1 </span>Lead time</h3>
<section class="ltx_paragraph"><h4 class="ltx_title ltx_title_paragraph">Setup.</h4>
<div class="ltx_para"><p class="ltx_p">The precursor rises 1,200 steps early.</p></div></section>
<figure class="ltx_figure"><figure class="ltx_figure ltx_figure_panel"><figcaption class="ltx_caption"><span class="ltx_tag ltx_tag_figure">(a) </span>Panel A.</figcaption></figure>
<figcaption class="ltx_caption"><span class="ltx_tag ltx_tag_figure">Figure 2: </span>Lead time curves.</figcaption></figure>
<figure class="ltx_table"><figcaption class="ltx_caption"><span class="ltx_tag ltx_tag_table">Table 1: </span>Results.</figcaption>
<table class="ltx_tabular"><tr><td>r</td><td>delay</td></tr><tr><td>0.3</td><td>1200</td></tr></table></figure>
<table class="ltx_equation"><tr><td class="ltx_eqn_cell"><math alttext="t\\sim 1/\\lambda_{3}">t</math></td><td class="ltx_eqn_eqno">(4)</td></tr></table>
</section></section>
<section class="ltx_appendix"><h2 class="ltx_title ltx_title_appendix"><span class="ltx_tag ltx_tag_appendix">Appendix A </span>Extra</h2>
<section class="ltx_subsection"><h3 class="ltx_title ltx_title_subsection"><span class="ltx_tag ltx_tag_subsection">A.5 </span>Sharpness</h3>
<div class="ltx_para"><p class="ltx_p">Sharpness anti-correlates with accuracy.</p></div></section></section>
<section class="ltx_bibliography"><h2 class="ltx_title ltx_title_bibliography">References</h2><p class="ltx_p">BIBENTRY</p></section>
</article></div></body></html>"""

PDFTXT = """arXiv:0000.00000v2 [cs.LG] 1 Jan 2030
A Title
Abstract
ABSTRACTTEXT should not appear.
1 Introduction
Classical machine learning theory says that the test error as a function of the size of the class is U-shaped and
continues on the next line of the same paragraph.
2 Theory
Here, the matrix X ∈ Rn×d contains the scaled training feature vectors √1n x1, . . . , √1n xn as rows and y = √1n [y1, . . .].
θ˜t = (I − ηXT X)t θ0
∥x∥2 ≤ 1
Figure 1: Left: The test error of a 5-layer network on
noisy CIFAR-10 as a function of training epochs.
0.4 0.3 0.2
7
References
[Bel+19a] BIBENTRY.
A Proofs of the results
The proof follows from a standard argument that we now spell out in complete detail for the reader.
C Later appendix
J (W0, v0)J T (W0, v0) = ν2 E relu′(Xw0)
"""


class Html(unittest.TestCase):
    def setUp(self):
        self.body = vf.html_to_body(HTML)

    def test_structure_uses_the_papers_numbering(self):
        heads = [l for l in self.body.splitlines() if l.startswith("#")]
        self.assertEqual(heads, ["### 1 Introduction", "### 3 Experiments", "#### 3.1 Lead time",
                                 "### Appendix A: Extra", "#### A.5 Sharpness"])

    def test_verbatim_math_footnote_and_damage(self):
        self.assertIn("We train on $50\\%$ of the data", self.body)
        self.assertIn("[nota al pie: A footnote.]", self.body)
        self.assertIn("and [extracción dañada] here.", self.body)       # math with no LaTeX
        self.assertIn("$$ $t\\sim 1/\\lambda_{3}$ $$ (4)", self.body)

    def test_figures_tables_lists_runin_titles(self):
        self.assertIn("**Subfigura:** (a) Panel A.", self.body)
        self.assertIn("**Figure 2:** Lead time curves.", self.body)
        self.assertIn("**Table 1:** Results.\n\n| r | delay |\n| 0.3 | 1200 |", self.body)
        self.assertIn("- A bullet point.", self.body)
        self.assertIn("**Setup.**\n\nThe precursor rises 1,200 steps early.", self.body)

    def test_front_matter_abstract_and_bibliography_left_out(self):
        for gone in ("ABSTRACTTEXT", "BIBENTRY", "jane@x.org", "A Title"):
            self.assertNotIn(gone, self.body)


class Pdf(unittest.TestCase):
    def setUp(self):
        self.body = vf.pdftext_to_body(PDFTXT)

    def test_headings_and_forward_only_appendices(self):
        heads = [l for l in self.body.splitlines() if l.startswith("#")]
        self.assertEqual(heads, ["### 1 Introduction", "### 2 Theory",
                                 "### Appendix A: Proofs of the results",
                                 "### Appendix C: Later appendix"])   # "J (W0…" is not a heading

    def test_prose_is_verbatim_and_wrapped_lines_join(self):
        self.assertIn("is U-shaped and continues on the next line of the same paragraph.", self.body)
        self.assertIn("**Figure 1:** Left: The test error of a 5-layer network on noisy CIFAR-10 "
                      "as a function of training epochs.", self.body)

    def test_garbled_math_is_marked_never_rebuilt(self):
        self.assertIn("√1n [y1, . . .]. [extracción dañada: fórmulas en línea]", self.body)
        self.assertIn("[extracción dañada] (ecuación, tabla o texto interno de figura)", self.body)
        self.assertNotIn("θ˜t", self.body)
        self.assertNotIn("0.4 0.3 0.2", self.body)

    def test_front_matter_references_page_numbers_left_out(self):
        for gone in ("ABSTRACTTEXT", "BIBENTRY", "arXiv:0000"):
            self.assertNotIn(gone, self.body)
        self.assertNotIn("\n\n7\n\n", self.body)


class Build(unittest.TestCase):
    def test_fuente_line_has_url_version_date_and_sha256(self):
        data = HTML.encode("utf-8")
        block = vf.build("arxiv-html", "https://arxiv.org/html/0000.00000v2", "v2", data, "2030-01-01")
        first = block.split("\n\n", 1)[0]
        self.assertIn("https://arxiv.org/html/0000.00000v2", first)
        self.assertIn("versión v2", first)
        self.assertIn("obtenido 2030-01-01", first)
        self.assertIn(hashlib.sha256(data).hexdigest(), first)

    def test_unstructured_source_is_refused(self):
        self.assertIsNone(vf.build("arxiv-html", "u", "v1", b"<html><p>no sections</p></html>",
                                   "2030-01-01"))


if __name__ == "__main__":
    unittest.main()
