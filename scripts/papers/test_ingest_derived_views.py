"""attach-pdf, rebuild and reconvert fire the derived views (SOTA staleness, Smart
Connections re-index) that add fires, when they change a note's source text
(invented ids and text, no network)."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_ingest_paper as tip  # noqa: E402

ip = tip.ip


class DerivedViews(tip.Base):
    def setUp(self):
        super().setUp()
        self.fired: list[list[str]] = []
        orig = ip.derived_views

        def record(vault, paths):
            self.fired.append(sorted(Path(p).name for p in paths))
            return {"sota_stale": "PROJ-001 (invented)"}
        ip.derived_views = record
        self.addCleanup(setattr, ip, "derived_views", orig)

    def test_attach_pdf_fires_them_for_the_note_it_filled(self):
        self.add("--doi", "10.0000/toy.2031.7")
        self.fired.clear()
        pdfs = Path(tempfile.mkdtemp(prefix="kairo-pdfs-"))
        self.addCleanup(shutil.rmtree, pdfs, True)
        (pdfs / "P-0001.pdf").write_bytes(b"%PDF-1.4 the invented paper")
        saved = ip.vf.pdftotext_available, ip.vf.pdf_bytes_to_text
        ip.vf.pdftotext_available = lambda: True
        ip.vf.pdf_bytes_to_text = lambda data: tip.PDF_TEXT
        try:
            code, out = self.run_cli("attach-pdf", "--vault", str(self.vault), "--pdf-dir", str(pdfs))
            self.assertEqual((code, out["attached"]), (0, 1), out)
            self.assertEqual(self.fired, [[self.note().name]])
            self.assertEqual(out["sota_stale"], "PROJ-001 (invented)")
            self.fired.clear()
            code, out = self.run_cli("attach-pdf", "--vault", str(self.vault), "--pdf-dir", str(pdfs), "--dry-run")
            self.assertEqual(self.fired, [])                       # nothing written, nothing fired
        finally:
            ip.vf.pdftotext_available, ip.vf.pdf_bytes_to_text = saved

    def test_reconvert_fires_them_only_when_the_text_changed(self):
        self.add("--arxiv", "0000.11111")
        self.fired.clear()
        self.run_cli("reconvert", "--vault", str(self.vault))
        self.assertEqual(self.fired, [])                           # same text: nothing to refresh
        m = self.vault / "Papers/_fuentes/P-0001/fuentes.json"
        man = json.loads(m.read_text(encoding="utf-8"))
        man["converter"] = "kairo/verbatim_fulltext@0.9.0"
        m.write_text(json.dumps(man), encoding="utf-8")
        p = self.note()
        p.write_text(p.read_text(encoding="utf-8").replace("1,200 steps", "1,300 steps"), encoding="utf-8")
        code, out = self.run_cli("reconvert", "--vault", str(self.vault))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.fired, [[p.name]])

    def test_rebuild_fires_them(self):
        p = self.vault / "Papers" / "P-0007 Toy Codes.md"
        p.write_text("---\nid: P-0007\ntitle: Toy Codes for Invented Qubits\narxiv: 0000.11111\n"
                     "projects: [PROJ-001]\n---\n\n## Referencia\n\nx\n\n## Resumen\n\ny\n\n## Texto completo\n\nz\n",
                     encoding="utf-8")
        code, out = self.run_cli("rebuild", "--vault", str(self.vault), "--only", "P-0007")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.fired, [[p.name]])


if __name__ == "__main__":
    unittest.main()
