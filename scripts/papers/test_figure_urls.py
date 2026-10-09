"""verbatim_fulltext.figure_images: figure URLs resolve as a browser resolves
them on arXiv's HTML page (invented ids and file names only)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import verbatim_fulltext as vf  # noqa: E402


def page(src: str) -> str:
    return (f'<article class="ltx_document"><section class="ltx_section"><figure class="ltx_figure">'
            f'<img src="{src}" class="ltx_graphics"><figcaption>Figure 1: Invented.</figcaption></figure>'
            f"</section></article>")


class ArxivFigureUrls(unittest.TestCase):
    def test_the_id_prefixed_path_arxiv_writes_is_not_doubled(self):
        # arXiv serves https://arxiv.org/html/<id>v<N> and writes src="<id>v<N>/<file>"
        got = vf.figure_images(page("2999.00001v2/x1.png"), "https://arxiv.org/html/2999.00001v2")
        self.assertEqual(got, [("2999.00001v2_x1.png", "https://arxiv.org/html/2999.00001v2/x1.png")])

    def test_a_bare_file_name_is_inside_the_paper(self):
        got = vf.figure_images(page("x1.png"), "https://arxiv.org/html/2999.00001v2")
        self.assertEqual(got[0][1], "https://arxiv.org/html/2999.00001v2/x1.png")

    def test_an_absolute_path_stays_absolute(self):
        got = vf.figure_images(page("/html/2999.00001v2/x1.png"), "https://arxiv.org/html/2999.00001v2")
        self.assertEqual(got[0][1], "https://arxiv.org/html/2999.00001v2/x1.png")


if __name__ == "__main__":
    unittest.main()
