"""HTML text outside paragraphs, algorithms and SVG figures (synthetic LaTeXML, invented text)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verbatim_fulltext as vf  # noqa: E402

HEAD = ('<html><body><article class="ltx_document">'
        '<section class="ltx_section"><h2 class="ltx_title ltx_title_section">'
        '<span class="ltx_tag ltx_tag_section">1 </span>Method</h2>'
        '<div class="ltx_para"><p class="ltx_p">An invented opening paragraph.</p></div>')
TAIL = "</section></article></body></html>"

LOOSE = (HEAD + '<div class="ltx_block">Invented loose text <span class="ltx_text ltx_font_bold">in bold</span> '
         'with <math alttext="x^{2}">x</math> outside any paragraph.</div>'
         '<div class="ltx_para"><p class="ltx_p">An invented closing paragraph.</p></div>' + TAIL)

ALGO = (HEAD + '<figure class="ltx_float ltx_float_algorithm ltx_framed ltx_framed_top" id="alg1">'
        '<figcaption class="ltx_caption"><span class="ltx_tag ltx_tag_float">'
        '<span class="ltx_text ltx_font_bold">Algorithm 1</span> </span>Toy schedule</figcaption>'
        '<div class="ltx_listing ltx_listing">'
        '<div class="ltx_listing_data"><a href="data:text/plain;base64,AAAA">⬇</a></div>'
        '<div class="ltx_listingline"><span class="ltx_tag ltx_tag_listingline">1:</span>'
        '<span class="ltx_text ltx_font_bold">for</span> each toy job <math alttext="j">j</math> '
        '<span class="ltx_text ltx_font_bold">do</span></div>'
        '<div class="ltx_listingline"><span class="ltx_tag ltx_tag_listingline">2:</span>'
        '   assign <math alttext="j">j</math> to an invented node</div>'
        '</div></figure>'
        '<div class="ltx_listing ltx_lstlisting"><div class="ltx_listingline">toy = run(1)</div></div>'
        '<div class="ltx_algorithm"></div>' + TAIL)

SVG = (HEAD + '<figure class="ltx_figure" id="fig4"><svg class="ltx_picture" width="100" height="80">'
       '<g><text>0.5</text></g></svg>'
       '<figcaption class="ltx_caption"><span class="ltx_tag ltx_tag_figure">Figure 4: </span>'
       'Invented toy curve.</figcaption></figure>' + TAIL)


class LooseText(unittest.TestCase):
    def test_text_outside_a_paragraph_is_kept(self):
        body = vf.html_to_body(LOOSE)
        self.assertIn("Invented loose text in bold with $x^{2}$ outside any paragraph.", body)
        self.assertIn("An invented closing paragraph.", body)

    def test_the_older_rules_are_kept(self):
        self.assertNotIn("loose text", vf.html_to_body(LOOSE, legacy=True))


class Algorithms(unittest.TestCase):
    def setUp(self):
        self.body = vf.html_to_body(ALGO)

    def test_an_algorithm_is_kept_verbatim_under_its_caption(self):
        self.assertIn("**Algorithm 1:** Toy schedule\n\n```\n1: for each toy job $j$ do\n"
                      "2: assign $j$ to an invented node\n```", self.body)
        self.assertNotIn("⬇", self.body)

    def test_a_listing_outside_a_float_is_kept(self):
        self.assertIn("```\ntoy = run(1)\n```", self.body)

    def test_an_algorithm_that_cannot_be_kept_is_marked(self):
        self.assertIn("[extracción dañada: algoritmo]", self.body)

    def test_the_older_rules_drop_it_as_before(self):
        self.assertNotIn("toy job", vf.html_to_body(ALGO, legacy=True))


class SvgFigures(unittest.TestCase):
    def test_an_svg_plot_is_marked_under_its_caption(self):
        body = vf.html_to_body(SVG)
        self.assertIn("**Figure 4:** Invented toy curve.\n\n[figura SVG no extraída]", body)
        self.assertNotIn("0.5", body)                      # never read off the plot

    def test_the_older_rules_leave_only_the_caption(self):
        self.assertNotIn("SVG", vf.html_to_body(SVG, legacy=True))


class Build(unittest.TestCase):
    def test_build_takes_the_rules_of_the_version_that_wrote_a_note(self):
        new = vf.build("arxiv-html", "https://example.invalid/x", "v1", ALGO.encode(), "2031-01-01")
        old = vf.build("arxiv-html", "https://example.invalid/x", "v1", ALGO.encode(), "2031-01-01",
                       rules=(1, 5, 0))
        self.assertIn("toy job", new)
        self.assertNotIn("toy job", old)


if __name__ == "__main__":
    unittest.main()
