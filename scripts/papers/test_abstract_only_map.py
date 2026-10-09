"""Abstract-only papers in the state-of-the-art map (2026-10-09 audit, H4):
facet_assignment lists them apart (still assigned), the map may cite them only
as `P-XXXX Resumen` marked «(solo abstract)», and check_sota warns on every
project paper the map neither cites nor lists in «Cobertura de lectura».
Invented notes only."""

from __future__ import annotations

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
import check_sota as cs  # noqa: E402
import facet_assignment as fa  # noqa: E402


def note(pid, fulltext, body="The invented method reaches 0.91 on the toy benchmark."):
    return (f"---\nid: {pid}\ntitle: Toy {pid}\nprojects: [PROJ-301]\nfulltext: {fulltext}\n"
            f"facets:\n  - {{project: PROJ-301, facet: A, matched: \"toy\"}}\n---\n\n"
            f"## Resumen\n\n> Fuente: invented\n\nAn invented abstract: the toy scheme gains 12% on toy data.\n\n"
            + (f"## Texto completo\n\n> Fuente: invented\n\n### 3 Results\n\n{body}\n" if fulltext == "full" else ""))


class AbstractOnly(unittest.TestCase):
    def setUp(self):
        self.vault = Path(tempfile.mkdtemp(prefix="kairo-abs-"))
        pap = self.vault / "Papers"
        pap.mkdir()
        (pap / "P-0301 full.md").write_text(note("P-0301", "full"), encoding="utf-8")
        (pap / "P-0302 abs.md").write_text(note("P-0302", "abstract-only"), encoding="utf-8")
        (pap / "P-0303 forgotten.md").write_text(note("P-0303", "full"), encoding="utf-8")
        self.p = self.vault / "Projects" / "toy"
        self.p.mkdir(parents=True)
        (self.p / "_hub.md").write_text("---\nid: PROJ-301\n---\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    def check(self, text):
        (self.p / "Estado-del-arte.md").write_text(text, encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cs.main(["--vault", str(self.vault), "--project-dir", "Projects/toy"])
        return code, json.loads(buf.getvalue())

    def test_chunks_list_abstract_only_papers_apart_and_still_assign_them(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fa.main(["--vault", str(self.vault), "--project", "PROJ-301", "--chunks"])
        out = json.loads(buf.getvalue())
        self.assertEqual(out["solo_abstract"], ["P-0302"])
        papers = {p["id"]: p for c in out["chunks"] for p in c["papers"]}
        self.assertEqual(sorted(papers), ["P-0301", "P-0302", "P-0303"])
        self.assertTrue(papers["P-0302"]["solo_abstract"])
        self.assertNotIn("solo_abstract", papers["P-0301"])

    def test_an_abstract_only_paper_is_cited_by_its_resumen_and_marked(self):
        code, out = self.check(
            "## Lo establecido vs. lo debatido\n\n"
            "- The toy method reaches 0.91 — P-0301 §3\n"
            "- The toy scheme gains 12% «(solo abstract)» — P-0302 Resumen\n\n"
            "## Cobertura de lectura\n\n- lectura: P-0301 completo\n- solo abstract: P-0302\n"
            "- no citado: P-0303 (fuera de alcance)\n")
        self.assertEqual((code, out["problems"]), (0, []), out)
        self.assertNotIn("warnings", out)

    def test_resumen_of_a_full_text_paper_is_a_warning_and_the_mark_is_required(self):
        # the abstract is the paper's own verbatim text: citing it is checkable, never an
        # error — but a paper with full text is better cited at the section that says it
        code, out = self.check(
            "## X\n\n- The toy scheme gains 12% — P-0301 Resumen\n"
            "- The toy scheme gains 12% — P-0302 Resumen\n\n"
            "## Cobertura de lectura\n\n- solo abstract: P-0302\n- no citado: P-0303\n")
        self.assertFalse(any(p["citation"] == "P-0301 Resumen" for p in out["problems"]), out["problems"])
        self.assertTrue(any("P-0301" in w and "texto completo" in w for w in out["warnings"]), out.get("warnings"))
        self.assertTrue(any("(solo abstract)" in w for w in out["warnings"]), out.get("warnings"))

    def test_every_project_paper_is_cited_or_declared(self):
        code, out = self.check("## X\n\n- The toy method reaches 0.91 — P-0301 §3\n\n"
                               "## Cobertura de lectura\n\n- Todas las secciones citables se leyeron.\n")
        self.assertEqual(out["sin_cubrir"], ["P-0303"])
        self.assertEqual(out["solo_abstract_sin_declarar"], ["P-0302"])
        self.assertTrue(any("P-0303" in w for w in out["warnings"]))


if __name__ == "__main__":
    unittest.main()
