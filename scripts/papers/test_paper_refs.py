"""Tests for paper_refs.py: a paper's bibliography from the kept bytes, no network.
Invented vault, ids and text only."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paper_refs as pr  # noqa: E402

HTML = """<html><body><article class="ltx_document">
<section class="ltx_section"><h2 class="ltx_title ltx_title_section"><span class="ltx_tag">1 </span>Intro</h2>
<p class="ltx_p">Toy decoders <cite class="ltx_cite">[<a href="#bib.bib1">1</a>, <a href="#bib.bib2">2</a>]</cite>.</p>
</section>
<section class="ltx_bibliography"><ul class="ltx_biblist">
<li id="bib.bib1" class="ltx_bibitem"><span class="ltx_tag ltx_tag_bibitem">[1]</span>
<span class="ltx_bibblock">A. Doe. Toy codes with $k$ qubits. arXiv:2401.00001, 2024.</span></li>
<li id="bib.bib2" class="ltx_bibitem"><span class="ltx_tag ltx_tag_bibitem">[2]</span>
<span class="ltx_bibblock">B. Roe. Invented decoders. <a href="https://doi.org/10.9999/inv.2">Journal</a>.</span></li>
<li id="bib.bib3" class="ltx_bibitem"><span class="ltx_tag ltx_tag_bibitem">[3]</span>
<span class="ltx_bibblock">C. Poe. A book with no identifier. Invented Press, 2020.</span></li>
</ul></section></article></body></html>"""

PDF_TEXT = """1 Introduction
Toy text [1].
References
[1] A. Doe. Toy codes. arXiv:2401.00001 (2024).
[2] D. Lee. Another invented paper,
continued on a second line. doi:10.9999/inv.4.
12
"""


def note(pid, project, arxiv="", extra=""):
    return (f"---\nid: {pid}\ntitle: Toy\narxiv: {arxiv}\nprojects: [{project}]\n"
            f"fuentes: Papers/_fuentes/{pid}/fuentes.json\n{extra}---\n\n## Texto completo\n\nx\n")


class PaperRefs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.v = Path(self.tmp.name)
        (self.v / "Papers").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, pid, project, data: bytes, kind, arxiv="", extra=""):
        (self.v / "Papers" / f"{pid} toy.md").write_text(note(pid, project, arxiv, extra), encoding="utf-8")
        d = self.v / "Papers" / "_fuentes" / pid
        d.mkdir(parents=True)
        name = "texto.txt" if kind == "pdf-text" else "texto.html"
        (d / name).write_bytes(data)
        (d / "fuentes.json").write_text(json.dumps({"files": [
            {"file": name, "role": "fulltext", "kind": kind, "url": "https://example.invalid/x",
             "sha256": hashlib.sha256(data).hexdigest()}]}), encoding="utf-8")

    def cli(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = pr.main(list(args))
        return code, json.loads(buf.getvalue())

    def test_html_bibliography_with_labels_anchors_and_stated_ids(self):
        refs = pr.from_html(HTML)
        self.assertEqual([r["label"] for r in refs], ["[1]", "[2]", "[3]"])
        self.assertEqual(refs[0]["anchor"], "bib.bib1")
        self.assertEqual(refs[0]["arxiv"], ["2401.00001"])
        self.assertIn("$k$ qubits", refs[0]["text"])
        self.assertEqual(refs[1]["doi"], ["10.9999/inv.2"])                  # from the link
        self.assertEqual((refs[2]["arxiv"], refs[2]["doi"]), ([], []))       # never guessed

    def test_pdf_references_are_split_at_their_labels(self):
        refs = pr.from_pdf_text(PDF_TEXT)
        self.assertEqual([r["label"] for r in refs], ["[1]", "[2]"])
        self.assertIn("continued on a second line", refs[1]["text"])
        self.assertEqual(refs[1]["doi"], ["10.9999/inv.4"])

    def test_extract_writes_beside_the_bytes_and_never_touches_the_note(self):
        self.add("P-0981", "PROJ-980", HTML.encode(), "arxiv-html")
        before = (self.v / "Papers" / "P-0981 toy.md").read_bytes()
        code, out = self.cli("extract", "--vault", str(self.v), "--only", "P-0981")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["notes"][0]["references"], 3)
        self.assertTrue((self.v / "Papers" / "_fuentes" / "P-0981" / "referencias.json").is_file())
        self.assertEqual((self.v / "Papers" / "P-0981 toy.md").read_bytes(), before)

    def test_list_marks_what_the_vault_holds_and_hides_text_unless_asked(self):
        self.add("P-0981", "PROJ-980", HTML.encode(), "arxiv-html")
        (self.v / "Papers" / "P-0982 held.md").write_text(note("P-0982", "PROJ-980", "2401.00001"),
                                                          encoding="utf-8")
        self.cli("extract", "--vault", str(self.v), "--only", "P-0981")
        code, out = self.cli("list", "--vault", str(self.v), "--id", "P-0981")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["entries"][0]["in_vault"], "P-0982")
        self.assertNotIn("text", out["entries"][0])
        _, out = self.cli("list", "--vault", str(self.v), "--id", "P-0981", "--text")
        self.assertIn("text", out["entries"][0])

    def test_corpus_counts_the_works_the_project_cites_and_lacks(self):
        self.add("P-0981", "PROJ-980", HTML.encode(), "arxiv-html")
        self.add("P-0983", "PROJ-980", PDF_TEXT.encode(), "pdf-text")
        self.add("P-0984", "PROJ-9800", HTML.encode(), "arxiv-html")             # another project
        self.cli("extract", "--vault", str(self.v), "--all")
        code, out = self.cli("corpus", "--vault", str(self.v), "--project", "PROJ-980")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["not_in_vault"], [{"id": "arxiv:2401.00001", "cited_by": ["P-0981", "P-0983"],
                                                "count": 2}])
        self.assertEqual(out["snowball_seeds"], "arXiv:2401.00001")

    def test_send_never_and_legacy_notes(self):
        self.add("P-0985", "PROJ-980", HTML.encode(), "arxiv-html", extra="send: never\n")
        (self.v / "Papers" / "P-0986 old.md").write_text("---\nid: P-0986\nprojects: [PROJ-980]\n---\n",
                                                         encoding="utf-8")
        code, out = self.cli("extract", "--vault", str(self.v), "--project", "PROJ-980")
        self.assertEqual(code, 1)
        self.assertEqual({r["id"]: r["status"] for r in out["notes"]}, {"P-0985": "send_never", "P-0986": "legacy"})
        self.assertFalse((self.v / "Papers" / "_fuentes" / "P-0985" / "referencias.json").exists())


if __name__ == "__main__":
    unittest.main()
