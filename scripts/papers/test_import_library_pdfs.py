"""import_library: the researcher's own PDFs (a Zotero / Better BibTeX `file`
field, or --pdf-dir) give a paywalled paper its full text, and an entry with
no identifier is looked up by its exact title — never guessed (invented ids,
titles and files only)."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import import_library as il  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "vault" / "Papers").mkdir(parents=True)
        (self.dir / "files").mkdir()
        self.pdf = self.dir / "files" / "inventa2031.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 invented")
        self.calls: list[list[str]] = []

        def fake_ingest(argv):
            self.calls.append(argv)
            return 0, {"status": "created", "id": f"P-{len(self.calls):04d}", "fulltext": "pdf-text"}
        self.patches = [mock.patch.object(il, "ingest", fake_ingest),
                        mock.patch.object(il, "pdf_to_text", lambda pdf, out_dir: out_dir / (pdf.stem + ".txt"))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def cli(self, bib: str, *extra, fetch=None):
        path = self.dir / "refs.bib"
        path.write_text(bib, encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = il.main(["--vault", str(self.dir / "vault"), "--project", "PROJ-001", "--bib", str(path), *extra],
                           fetch=fetch or (lambda url, headers: b'{"results": []}'))
        return code, json.loads(buf.getvalue())


class ZoteroPdfs(Base):
    def test_a_doi_entry_with_an_attached_pdf_is_ingested_from_that_pdf(self):
        bib = ("@inproceedings{inventa2031, title={Invented toy kernels}, doi={10.9999/sc.2031.1},\n"
               "  file={Full Text PDF:files/inventa2031.pdf:application/pdf}}\n")
        code, out = self.cli(bib)
        self.assertEqual(code, 0, out)
        argv = self.calls[0]
        self.assertIn("--doi", argv)
        self.assertEqual(Path(argv[argv.index("--pdf-text") + 1]).name, "inventa2031.txt")
        # the note records the file's name, never a local path
        self.assertEqual(argv[argv.index("--source-url") + 1], "file:inventa2031.pdf")
        self.assertEqual(out["plan"][0]["pdf"], "inventa2031.pdf")

    def test_an_arxiv_entry_keeps_the_open_arxiv_text(self):
        bib = ("@article{inventa2030, title={Invented}, eprint={2999.00021}, archiveprefix={arXiv},\n"
               "  file={files/inventa2031.pdf}}\n")
        self.cli(bib)
        self.assertNotIn("--pdf-text", self.calls[0])
        self.assertIn("--arxiv", self.calls[0])

    def test_pdf_dir_by_citation_key(self):
        bib = "@article{inventa2031, title={Invented toy kernels}, doi={10.9999/sc.2031.1}}\n"
        self.cli(bib, "--pdf-dir", str(self.dir / "files"))
        self.assertIn("--pdf-text", self.calls[0])


class ExactTitle(Base):
    def oa(self, results):
        return lambda url, headers: json.dumps({"results": results}).encode()

    def test_an_entry_without_identifier_is_found_by_its_exact_title(self):
        bib = "@inproceedings{fremd2031, title={An Invented Scheduler for Toy Clusters}}\n"
        fetch = self.oa([{"id": "https://openalex.org/W99", "doi": "https://doi.org/10.9999/atc.2031.5",
                          "title": "An invented scheduler for toy clusters", "publication_year": 2031}])
        code, out = self.cli(bib, "--title-lookup", fetch=fetch)
        self.assertEqual(out["no_identifier"], [])
        self.assertEqual((out["plan"][0]["kind"], out["plan"][0]["id"]), ("doi", "10.9999/atc.2031.5"))
        self.assertIn("título exacto", out["plan"][0]["found_by"])
        self.assertIn("10.9999/atc.2031.5", self.calls[0])

    def test_an_ambiguous_title_is_listed_never_guessed(self):
        bib = "@inproceedings{fremd2031, title={An Invented Scheduler}}\n"
        fetch = self.oa([{"id": "https://openalex.org/W1", "doi": "https://doi.org/10.9999/a", "title": "An invented scheduler"},
                         {"id": "https://openalex.org/W2", "doi": "https://doi.org/10.9999/b", "title": "An invented scheduler"}])
        code, out = self.cli(bib, "--title-lookup", fetch=fetch)
        self.assertEqual(self.calls, [])
        self.assertEqual(out["no_identifier"][0]["key"], "fremd2031")
        self.assertIn("ambiguo", out["no_identifier"][0]["why"])


if __name__ == "__main__":
    unittest.main()
