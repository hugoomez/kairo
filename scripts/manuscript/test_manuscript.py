"""Tests for manuscript.py. Run: python -m pytest scripts/manuscript (invented project PROJ-900)."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import manuscript as ms  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-ms-"))
        self.proj = self.tmp / "Projects" / "demo"
        (self.proj / "Claims").mkdir(parents=True)
        (self.proj / "Hipotesis").mkdir()
        (self.proj / "_hub.md").write_text("---\nid: PROJ-900\n---\n", encoding="utf-8")
        (self.proj / "Claims" / "C-9001.md").write_text("---\nid: C-9001\n---\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestInit(Base):
    def test_writes_outline_and_skeleton_without_prose(self):
        o, m = ms.init(self.proj, "cotas-azules", 'Cotas para widgets "azules"', None)
        meta, sections, _ = ms.parse_outline(o)
        self.assertEqual((meta["project"], meta["title"], meta["venue"]), ("PROJ-900", 'Cotas para widgets "azules"', "por decidir"))
        self.assertEqual([s["kind"] for s in sections], ["prosa", "prosa", "prosa", "resultado", "cierre"])
        text = m.read_text(encoding="utf-8")
        for s in sections:
            self.assertIn(f"<!-- kairo:section {s['id']} -->", text)
        self.assertIn("status: esqueleto", text)
        self.assertIn("_(pendiente — resultados", text)

    def test_never_overwrites(self):
        ms.init(self.proj, "t1", "T", None)
        with self.assertRaises(ms.Refused):
            ms.init(self.proj, "t1", "Otra", None)

    def test_bad_thread_and_missing_hub(self):
        with self.assertRaises(ms.Refused):
            ms.init(self.proj, "Mal Hilo", "T", None)
        (self.proj / "_hub.md").unlink()
        with self.assertRaises(ms.Refused):
            ms.init(self.proj, "t2", "T", None)


class TestBind(Base):
    def test_binds_existing_notes_once_and_keeps_the_rest(self):
        o, _ = ms.init(self.proj, "t", "T", "COLT")
        self.assertEqual(ms.bind(self.proj, "t", "resultados", "C-9001"), ["C-9001"])
        self.assertEqual(ms.bind(self.proj, "t", "resultados", "C-9001"), ["C-9001"])
        meta, sections, body = ms.parse_outline(o)
        self.assertEqual(meta["venue"], "COLT")
        self.assertIn("## Contribución", body)
        self.assertEqual(next(s for s in sections if s["id"] == "resultados")["depends_on"], ["C-9001"])

    def test_refusals(self):
        ms.init(self.proj, "t", "T", None)
        for section, nid in (("resultados", "C-0404"), ("resultados", "X-1"), ("nope", "C-9001")):
            with self.subTest(section=section, nid=nid), self.assertRaises(ms.Refused):
                ms.bind(self.proj, "t", section, nid)

    def test_show_is_json(self):
        import io
        from contextlib import redirect_stdout
        ms.init(self.proj, "t", "T", None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(ms.main(["show", "--project-dir", str(self.proj), "--thread", "t"]), 0)
        self.assertEqual(len(json.loads(buf.getvalue())["sections"]), 5)


HYP = """---
id: {hid}
project: PROJ-900
status: {status}
linea_publicacion: true
linked_experiment:
  - E-9001
verifications:
  - verifier: kairo/fresh-verifier@1.1.0
    model: test
    date: 2026-01-01
    verdict: no_errors_found
    scope: note
---

## Claim

Un claim inventado.
"""

EXP = """---
id: E-9001
hypothesis: H-9001
tier: {tier}
experiment_validity: valid
---
"""


class CoverageBase(Base):

    def setUp(self):
        super().setUp()
        self.vault = self.tmp
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ledger"))
        import claim_status  # noqa: PLC0415
        self.cs = claim_status
        (self.proj / "Claims" / "C-9001.md").unlink()
        (self.proj / "Experimentos").mkdir()
        ms.init(self.proj, "t", "Paper inventado", None)

    def claim(self, kind, statement):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(self.cs.main(["new", "--project-dir", str(self.proj), "--kind", kind,
                                           "--statement", statement, "--how", "…", "--by", "t"]), 0)
        return Path(buf.getvalue().strip())

    def cov(self):
        return {s["id"]: s for s in ms.coverage(self.vault, self.proj, "t")["sections"]}


class TestCoverage(CoverageBase):
    """Coverage reads the gates; write-section refuses what is not ready."""

    def test_prose_is_ready_early_and_results_wait_for_their_gate(self):
        c = self.cov()
        self.assertTrue(c["introduccion"]["ready"])
        self.assertFalse(c["resultados"]["ready"])
        self.assertIn("sin claims", c["resultados"]["why"])
        self.assertFalse(c["discusion"]["ready"])

        t = self.claim("teorema", "Un teorema inventado.")
        ms.bind(self.proj, "t", "resultados", t.stem)
        c = self.cov()
        self.assertFalse(c["resultados"]["ready"])
        self.assertTrue(any("visto bueno" in m for m in c["resultados"]["deps"][0]["missing"]))
        src = self.tmp / "sec.md"
        src.write_text("Teorema 1. …", encoding="utf-8")
        with self.assertRaises(ms.Refused):
            ms.write_section(self.vault, self.proj, "t", "resultados", src.read_text(encoding="utf-8"))

    def test_writing_a_ready_section_touches_only_that_section(self):
        _, manuscript = ms.paths(self.proj, "t")
        before = ms.section_bodies(manuscript)
        ms.write_section(self.vault, self.proj, "t", "introduccion", "Texto de la introducción.\n\nSegundo párrafo.")
        after = ms.section_bodies(manuscript)
        self.assertEqual(after["introduccion"], "Texto de la introducción.\n\nSegundo párrafo.")
        for k in before:
            if k != "introduccion":
                self.assertEqual(after[k], before[k])
        self.assertTrue(self.cov()["introduccion"]["drafted"])

    def test_an_established_result_opens_its_section_and_then_the_discussion(self):
        r = self.claim("resultado_intermedio", "Un resultado intermedio inventado.")
        import io
        from contextlib import redirect_stdout, redirect_stderr
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(self.cs.main(["set", "--note", str(r), "--status", "probado", "--by", "t", "--evidence", "x"]), 0)
        ms.bind(self.proj, "t", "resultados", r.stem)
        self.assertTrue(self.cov()["resultados"]["ready"])
        ms.write_section(self.vault, self.proj, "t", "resultados", "Resultado 1.")
        self.assertTrue(self.cov()["discusion"]["ready"])

    def test_hypotheses_need_the_publication_bar(self):
        (self.proj / "Hipotesis" / "H-9001 x.md").write_text(HYP.format(hid="H-9001", status="apoyada"), encoding="utf-8")
        (self.proj / "Experimentos" / "E-9001.md").write_text(EXP.format(tier="ligero"), encoding="utf-8")
        ms.bind(self.proj, "t", "resultados", "H-9001")
        dep = self.cov()["resultados"]["deps"][0]
        self.assertFalse(dep["ok"])
        self.assertIn("completo", " ".join(dep["missing"]))
        (self.proj / "Experimentos" / "E-9001.md").write_text(EXP.format(tier="completo"), encoding="utf-8")
        self.assertTrue(self.cov()["resultados"]["ready"])

    def test_coverage_cli_writes_the_view(self):
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            self.assertEqual(ms.main(["coverage", "--vault", str(self.vault), "--project-dir", str(self.proj), "--thread", "t"]), 0)
        text = (self.proj / "Manuscritos" / "coverage-t.md").read_text(encoding="utf-8")
        self.assertIn("| Resultados principales | resultado | pendiente | — | no | no |", text)


class TestSectionStates(CoverageBase):
    """respaldada | pendiente | bloqueada, with build_graph's propagation."""

    def quiet(self, fn, argv):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return fn(argv)

    def set_status(self, note, status):
        self.assertEqual(self.quiet(self.cs.main, ["set", "--note", str(note), "--status", status,
                                                   "--by", "t", "--evidence", "x"]), 0)

    def claim_on(self, kind, statement, deps):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(self.cs.main(["new", "--project-dir", str(self.proj), "--kind", kind,
                                           "--statement", statement, "--how", "…", "--by", "t",
                                           "--depends-on", *deps]), 0)
        return Path(buf.getvalue().strip())

    def test_default_states(self):
        c = self.cov()
        # nothing bound: pendiente, though prosa may be drafted early
        self.assertEqual((c["introduccion"]["state"], c["introduccion"]["ready"]), ("pendiente", True))
        self.assertIn("sin respaldo vinculado", c["introduccion"]["why"])
        self.assertEqual(c["resultados"]["state"], "pendiente")
        self.assertEqual(c["discusion"]["state"], "pendiente")
        self.assertEqual(c["resultados"]["blocked_by"], [])

    def test_a_proved_result_is_respaldada(self):
        r = self.claim("resultado_intermedio", "Un resultado inventado.")
        self.set_status(r, "probado")
        ms.bind(self.proj, "t", "resultados", r.stem)
        self.assertEqual(self.cov()["resultados"]["state"], "respaldada")

    def test_a_failed_lemma_blocks_its_section_and_names_it(self):
        lemma = self.claim("lema", "Un lema inventado.")
        self.set_status(lemma, "fallido")
        ms.bind(self.proj, "t", "resultados", lemma.stem)
        s = self.cov()["resultados"]
        self.assertEqual(s["state"], "bloqueada")
        self.assertFalse(s["ready"])
        self.assertIn(lemma.stem, s["why"])
        self.assertIn("fallido", s["why"])
        with self.assertRaises(ms.Refused):
            ms.write_section(self.vault, self.proj, "t", "resultados", "Teorema 1.")

    def test_transitive_failure_names_the_chain(self):
        base = self.claim("lema", "Un lema base inventado.")
        mid = self.claim_on("lema", "Un lema intermedio inventado.", [base.stem])
        top = self.claim_on("teorema", "Un teorema inventado.", [mid.stem])
        self.set_status(base, "refutado")
        ms.bind(self.proj, "t", "resultados", top.stem)
        s = self.cov()["resultados"]
        self.assertEqual(s["state"], "bloqueada")
        self.assertEqual(s["blocked_by"][0]["by"], base.stem)
        self.assertEqual(s["blocked_by"][0]["chain"], [top.stem, mid.stem, base.stem])
        self.assertIn(f"{top.stem} ← {mid.stem} ← {base.stem}", s["why"])

    def test_a_discarded_hypothesis_blocks_and_so_does_what_rests_on_it(self):
        (self.proj / "Hipotesis" / "H-9001 x.md").write_text(HYP.format(hid="H-9001", status="descartada"), encoding="utf-8")
        dep = self.claim_on("lema", "Un lema que usa la hipótesis inventada.", ["H-9001"])
        ms.bind(self.proj, "t", "planteamiento", "H-9001")
        ms.bind(self.proj, "t", "resultados", dep.stem)
        c = self.cov()
        self.assertEqual(c["planteamiento"]["state"], "bloqueada")
        self.assertIn("descartada", c["planteamiento"]["why"])
        self.assertEqual(c["resultados"]["state"], "bloqueada")
        self.assertEqual(c["resultados"]["blocked_by"][0]["chain"], [dep.stem, "H-9001"])

    def test_a_blocked_result_does_not_open_the_discussion(self):
        r = self.claim("resultado_intermedio", "Un resultado inventado.")
        self.set_status(r, "probado")
        ms.bind(self.proj, "t", "resultados", r.stem)
        ms.write_section(self.vault, self.proj, "t", "resultados", "Resultado 1.")
        self.assertTrue(self.cov()["discusion"]["ready"])
        self.set_status(r, "refutado")
        c = self.cov()
        self.assertEqual(c["resultados"]["state"], "bloqueada")
        self.assertFalse(c["discusion"]["ready"])

    def test_view_lists_blocked_sections(self):
        lemma = self.claim("lema", "Un lema inventado.")
        self.set_status(lemma, "fallido")
        ms.bind(self.proj, "t", "resultados", lemma.stem)
        text = ms.render_coverage(ms.coverage(self.vault, self.proj, "t"))
        self.assertIn("## Bloqueada: «Resultados principales»", text)
        self.assertIn("| bloqueada |", text)


if __name__ == "__main__":
    unittest.main()
