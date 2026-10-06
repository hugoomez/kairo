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
        self.assertIn("of the data [nota al pie: A footnote.] and", self.body)   # marked once
        self.assertNotIn("[nota al pie: [nota al pie:", self.body)
        self.assertIn("and [extracción dañada] here.", self.body)       # math with no LaTeX
        self.assertIn("$$ $t\\sim 1/\\lambda_{3}$ $$ (4)", self.body)

    def test_figures_tables_lists_runin_titles(self):
        self.assertIn("**Subfigura:** (a) Panel A.", self.body)
        self.assertIn("**Figure 2:** Lead time curves.", self.body)
        self.assertIn("**Table 1:** Results.\n\n| r | delay |\n| 0.3 | 1200 |", self.body)
        self.assertIn("- A bullet point.", self.body)
        self.assertIn("**Setup.**\n\nThe precursor rises 1,200 steps early.", self.body)

    def test_spanned_cells_keep_every_column_aligned(self):
        """A multi-level header (colspan / rowspan) must not shift a number into
        the wrong column: a spanned cell is repeated in every column / row it covers."""
        html = HTML.replace(
            '<table class="ltx_tabular"><tr><td>r</td><td>delay</td></tr><tr><td>0.3</td><td>1200</td></tr></table>',
            '<table class="ltx_tabular">'
            '<tr><th rowspan="2">Method</th><th colspan="2">Throughput</th></tr>'
            '<tr><th>8 GPUs</th><th>64 GPUs</th></tr>'
            '<tr><td>Toy</td><td>1.5</td><td>9.8</td></tr>'
            '<tr><td colspan="3">invented footnote row</td></tr></table>')
        body = vf.html_to_body(html)
        self.assertIn("| Method | Throughput | Throughput |\n| Method | 8 GPUs | 64 GPUs |\n| Toy | 1.5 | 9.8 |\n"
                      "| invented footnote row | invented footnote row | invented footnote row |", body)

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


# Physics / IEEE layout (invented text): Roman sections, lettered subsections,
# "FIG." / "TABLE II." captions, upper-case REFERENCES, a table caption above its rows.
PDF_ROMAN = """Invented Title For A Toy Code
Some Author
I. INTRODUCTION
Toy stabilizer codes are introduced here in a sentence that is long enough to count as prose.
II. TOY MODEL
A. Definitions
We define the toy check matrix with a sentence that is long enough to count as prose text.
B. Decoder
The toy decoder is a sentence-length description that counts as prose for this extractor.
TABLE I. Logical error rates of the toy decoder.
distance rate
3 0.012
5 0.004
The table shows that the toy rate falls with the distance in this invented example text.
FIG. 2. Threshold crossing of the toy curves.
C. Not a heading because it ends with a period.
III. CONCLUSION
The toy conclusion is written as a full sentence that counts as prose in the extractor.
REFERENCES
[1] BIBENTRY.
"""


class PdfPhysicsStyle(unittest.TestCase):
    def setUp(self):
        self.body = vf.pdftext_to_body(PDF_ROMAN)

    def test_roman_sections_and_lettered_subsections(self):
        heads = [l for l in self.body.splitlines() if l.startswith("#")]
        self.assertEqual(heads, ["### I. INTRODUCTION", "### II. TOY MODEL", "#### A. Definitions",
                                 "#### B. Decoder", "### III. CONCLUSION"])
        self.assertNotIn("Invented Title", self.body)            # front matter left out
        self.assertNotIn("BIBENTRY", self.body)                  # upper-case REFERENCES cut

    def test_table_rows_stay_under_their_caption(self):
        self.assertIn("**Table 1 (TABLE I):** Logical error rates of the toy decoder.\n"
                      + vf._PDF_TABLE_NOTE + "\ndistance rate\n3 0.012\n5 0.004", self.body)
        self.assertIn("\n\nThe table shows that the toy rate falls", self.body)   # prose not glued on
        self.assertIn("**Figure 2 (FIG. 2):** Threshold crossing of the toy curves.", self.body)

    def test_the_resolver_reaches_roman_subsections_and_table_numbers(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ledger"))
        import verifier_packet as vp
        units = vp.source_units(self.body)
        dec = [u for u in units if vp.unit_matches(u, ("sec", "2.B"))]
        self.assertTrue(dec and "toy decoder is a sentence" in dec[0]["text"])
        tab = [u for u in units if vp.unit_matches(u, ("table", "1"))]
        self.assertTrue(tab and "5 0.004" in tab[0]["text"])

    def test_dotted_numbering_and_enumerations(self):
        body = vf.pdftext_to_body("1. Introduction\nAn invented opening sentence that is long enough to be "
                                  "prose.\n2. Methods\nAn invented methods sentence that is long enough to "
                                  "be prose.\n2.1. Setup\nAn invented setup sentence that is long enough to "
                                  "be prose here.\n7. Not a heading\n")
        heads = [l for l in body.splitlines() if l.startswith("#")]
        self.assertEqual(heads, ["### 1 Introduction", "### 2 Methods", "#### 2.1 Setup"])


class Fetch(unittest.TestCase):
    def test_ar5iv_text_is_never_given_the_latest_arxiv_version(self):
        html = HTML.encode("utf-8")
        calls = []

        def fake(url):
            calls.append(url)
            if "/abs/" in url:
                return 200, b"<a>[v1]</a> <a>[v3]</a>"
            if "ar5iv" in url:
                return 200, html
            return 404, b""
        orig = vf._get
        vf._get = fake
        try:
            got = vf.fetch_arxiv("0000.00000", pause=0)
            self.assertEqual(got["kind"], "ar5iv")
            self.assertTrue(got["version"].startswith("desconocida"))
            self.assertIn("v3", got["version"])
            pinned = vf.fetch_arxiv("0000.00000", pause=0, version="v2")
            self.assertIsNone(pinned)                 # v2 has no HTML/PDF here; ar5iv is skipped
            self.assertTrue(all("ar5iv" not in u for u in calls[-2:]))
        finally:
            vf._get = orig


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

    def test_a_long_letter_without_sections_is_kept_whole(self):
        # a letter-format paper (invented text): no numbered sections, but real prose
        prose = " ".join(f"Invented letter sentence number {i} about toy atoms." for i in range(200))
        block = vf.build_from_text(prose.encode("utf-8"), "https://arxiv.org/pdf/0000.00000v1", "v1", "2030-01-01")
        self.assertIsNotNone(block)
        self.assertIn(f"### {vf.UNSECTIONED_HEADING}", block)
        self.assertIn("La fuente no numera secciones", block)
        self.assertIn("Invented letter sentence number 7 about toy atoms.", block)


if __name__ == "__main__":
    unittest.main()
