"""Tests for export_bib.py (invented notes)."""

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import export_bib as eb  # noqa: E402

NOTES = {
    "P-0201 pre.md": '---\nid: P-0201\ntitle: "Fast BP-OSD for Toy qLDPC Codes"\nauthors: ["Jane Doe", "Rui Roe"]\n'
                     'year: 2031\nvenue: "arXiv preprint"\ndoi:\narxiv: 0000.22222\narxiv_version: v3\n'
                     'projects: [PROJ-201]\npublished_doi: "10.0000/jour.9"\npublished_venue: "Invented Journal of Codes"\n'
                     'published_year: 2032\n---\n',
    "P-0202 conf.md": '---\nid: P-0202\ntitle: "Scaling Toy Pipelines & 50% Less Memory"\nauthors: ["Poe, Ana"]\n'
                      'year: 2030\nvenue: "Proceedings of the Invented SC Conference"\ndoi: 10.0000/sc.2\n'
                      'projects: [PROJ-201]\nzotero_key: poe2030scaling\nresolution_status: retracted\n---\n',
    "P-0203 only.md": '---\nid: P-0203\ntitle: "An Unpublished Toy Preprint"\nauthors: ["Jane Doe"]\nyear: 2031\n'
                      'venue: "arXiv preprint"\narxiv: 0000.33333\nprojects: [PROJ-201]\n---\n',
    "P-0206 noyear.md": '---\nid: P-0206\ntitle: "A Toy Preprint Published Somewhere"\nauthors: ["Jane Doe"]\n'
                        'year: 2031\nvenue: "arXiv preprint"\narxiv: 0000.66666\nprojects: [PROJ-201]\n'
                        'published_venue: "Invented Systems Conf"\n---\n',
    "P-0204 secret.md": "---\nid: P-0204\nsend: never\nprojects: [PROJ-201]\n---\nPRIVATE TITLE\n",
    "P-0205 other.md": '---\nid: P-0205\ntitle: "Another Project Paper"\nauthors: ["Jane Doe"]\nyear: 2031\n'
                       'projects: [PROJ-999]\n---\n',
}


class ExportBib(unittest.TestCase):
    def setUp(self):
        self.vault = Path(tempfile.mkdtemp())
        (self.vault / "Papers").mkdir()
        for n, t in NOTES.items():
            (self.vault / "Papers" / n).write_text(t, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    def test_entry_type_follows_the_registered_type_then_the_venue(self):
        base = {"title": "T", "authors": ["Jane Doe"], "year": "2031", "doi": "10.0000/x"}
        cases = [({"venue": "Proceedings of the National Academy of Invented Sciences"}, "article"),
                 ({"venue": "Proceedings of the IEEE"}, "article"),
                 ({"venue": "Proceedings of the IEEE International Conference on Invented Computing (QCE)"},
                  "inproceedings"),
                 ({"venue": "Proceedings of the IEEE Symposium on Invented Systems"}, "inproceedings"),
                 ({"venue": "IEEE Transactions on Invented Codes"}, "article"),
                 ({"venue": "PNAS Nexus"}, "article"),
                 ({"venue": "Advances in Neural Information Processing Systems"}, "inproceedings"),
                 ({"venue": "Invented Letters", "venue_type": "proceedings-article"}, "inproceedings"),
                 ({"venue": "Proceedings of the Invented SC Conference", "venue_type": "journal-article"}, "article"),
                 ({"venue": "Invented Book of Toys", "venue_type": "book-chapter"}, "incollection")]
        for extra, kind in cases:
            self.assertEqual(eb.entry_type({**base, **extra})[0], kind, extra)

    def test_math_titles_volume_and_pages(self):
        m = {"title": "A $[[144,12,12]]$ toy code & a \\textit-free decoder", "authors": ["Jane Doe"],
             "year": "2031", "venue": "Invented Journal", "venue_type": "journal-article", "doi": "10.0000/y",
             "volume": "7", "issue": "2", "pages": "11--19"}
        bib = eb.bibtex_entry(m, "doe2031toy")
        self.assertIn("{$[[144,12,12]]$}", bib)               # math untouched
        self.assertIn("\\&", bib)                              # text still escaped
        self.assertIn("volume = {7}", bib)
        self.assertIn("number = {2}", bib)
        self.assertIn("pages = {11--19}", bib)
        self.assertEqual(eb.csl_item({**m, "id": "P-1"}, "k")["page"], "11-19")

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = eb.main(["--vault", str(self.vault), *args])
        return code, out.getvalue(), err.getvalue()

    def test_bibtex_for_a_project(self):
        code, bib, err = self.run_cli("--project", "PROJ-201")
        self.assertEqual(code, 0)
        rep = json.loads(err)
        self.assertEqual((rep["entries"], rep["skipped_send_never"]), (4, ["P-0204"]))
        self.assertNotIn("PRIVATE", bib)
        self.assertNotIn("Another Project", bib)
        # a published preprint is exported as the published version, eprint kept
        self.assertIn("@article{doe2032fast,", bib)
        self.assertIn("year = {2032}", bib)
        self.assertIn("journal = {Invented Journal of Codes}", bib)
        self.assertIn("doi = {10.0000/jour.9}", bib)
        self.assertIn("eprint = {0000.22222}", bib)
        self.assertIn("title = {Fast {BP}-{OSD} for Toy {qLDPC} Codes}", bib)   # acronyms keep their case
        # proceedings, Better BibTeX key, LaTeX escaping, retraction warning
        self.assertIn("@inproceedings{poe2030scaling,", bib)
        self.assertIn("50\\% Less Memory", bib)
        self.assertIn("\\&", bib)
        self.assertIn("note = {ATENCIÓN: paper RETRACTADO}", bib)
        self.assertEqual(rep["flagged"], [{"id": "P-0202", "status": "retracted"}])
        # an unpublished preprint is @misc with the arXiv line
        self.assertIn("@misc{doe2031unpublished,", bib)
        self.assertIn("howpublished = {arXiv preprint arXiv:0000.33333}", bib)
        self.assertIn("keywords = {kairo:P-0203}", bib)

    def test_a_published_venue_without_its_year_is_cited_as_the_preprint(self):
        code, bib, _ = self.run_cli("--only", "P-0206")
        self.assertEqual(code, 0)
        self.assertIn("@misc{doe2031preprint,", bib)                 # never booktitle + the preprint's year
        self.assertNotIn("booktitle", bib)
        self.assertIn("year = {2031}", bib)
        self.assertIn("Invented Systems Conf", bib)
        self.assertIn("año de la versión publicada no consta", bib)

    def test_csl_json_and_out_file(self):
        out = self.vault / "refs.json"
        code, stdout, _ = self.run_cli("--only", "P-0201", "--format", "csl-json", "--out", str(out))
        self.assertEqual(code, 0)
        items = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(items[0]["DOI"], "10.0000/jour.9")
        self.assertEqual(items[0]["author"][0], {"family": "Doe", "given": "Jane"})
        self.assertEqual(json.loads(stdout)["entries"], 1)

    def test_keys_are_unique(self):
        ms = [{"authors": ["Jane Doe"], "year": "2031", "title": "Toy codes"}] * 3
        self.assertEqual(eb.keys_for(ms), ["doe2031codes", "doe2031codesa", "doe2031codesb"])

    def test_a_published_version_names_the_preprint_version_its_locators_come_from(self):
        """Kairo's locators (§4.2, Tabla 3) point at the preprint text that was ingested;
        the entry cites the published version and says which text was read, in a field
        bibliography styles do not print, and the report lists it."""
        code, bib, err = self.run_cli("--only", "P-0201")
        self.assertEqual(code, 0)
        self.assertIn("kairoread = {arXiv:0000.22222v3}", bib)
        self.assertNotIn("note =", bib)                                  # nothing printed in the bibliography
        rep = json.loads(err)
        self.assertEqual(rep["locators_from_preprint"],
                         [{"id": "P-0201", "read": "arXiv:0000.22222v3", "cited": "10.0000/jour.9"}])
        out = self.vault / "refs.json"
        self.run_cli("--only", "P-0201", "--format", "csl-json", "--out", str(out))
        item = json.loads(out.read_text(encoding="utf-8"))[0]
        self.assertEqual(item["custom"]["kairo-read-version"], "arXiv:0000.22222v3")

    def test_a_key_does_not_change_with_the_subset_exported(self):
        twin = ('---\nid: P-0200\ntitle: "An Unpublished Toy Preprint"\nauthors: ["Jane Doe"]\nyear: 2031\n'
                'venue: "arXiv preprint"\narxiv: 0000.11111\nprojects: [PROJ-201]\n---\n')
        (self.vault / "Papers" / "P-0200 twin.md").write_text(twin, encoding="utf-8")
        _, whole, _ = self.run_cli("--project", "PROJ-201")
        _, alone, _ = self.run_cli("--only", "P-0203")
        self.assertIn("@misc{doe2031unpublished,", whole)                  # P-0200 came first: the bare key
        self.assertIn("@misc{doe2031unpublisheda,", whole)                 # P-0203 its twin
        self.assertIn("@misc{doe2031unpublisheda,", alone)                 # same key when exported alone


if __name__ == "__main__":
    unittest.main()
