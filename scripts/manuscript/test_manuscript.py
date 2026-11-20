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


class TestStudyDesign(Base):
    def question(self, status):
        d = self.tmp / "Projects" / "demo" / "Preguntas"
        d.mkdir(parents=True, exist_ok=True)
        (d / "Q-9001.md").write_text(
            f"---\nid: Q-9001\nproject: PROJ-900\ndesign: medicion\nstatus: {status}\nhistory: []\n---\n\n# Q-9001\n",
            encoding="utf-8")

    def test_init_measurement_sections(self):
        o, m = ms.init(self.proj, "med", "Estudio inventado", "acm-sigconf", design="medicion", lang="en")
        meta, sections, _ = ms.parse_outline(o)
        self.assertEqual([s["id"] for s in sections], [
            "abstract", "introduction", "background", "methodology", "ethics", "results", "discussion",
            "related-work", "open-science"])
        place = {s["id"]: s["placement"] for s in sections}
        self.assertEqual((place["abstract"], place["ethics"], place["open-science"], place["results"]),
                         ("front", "appendix", "appendix", "body"))
        self.assertEqual((meta["lang"], meta["venue"], meta["design"]), ("en", "acm-sigconf", "medicion"))
        head = o.read_text(encoding="utf-8")
        self.assertIn("\nlang: en\n", head)
        self.assertIn("\nvenue: acm-sigconf\n", head)
        self.assertIn("## Related work", m.read_text(encoding="utf-8"))

    def test_default_lang_and_no_design_keep_old_sections(self):
        o, _ = ms.init(self.proj, "old", "T", None)
        meta, sections, _ = ms.parse_outline(o)
        self.assertEqual((meta["lang"], len(sections)), ("es", 5))
        self.assertNotIn("design", meta)

    def test_unknown_venue_refused(self):
        with self.assertRaises(ms.Refused):
            ms.init(self.proj, "v", "T", "nope")
        rc = ms.main(["init", "--project-dir", str(self.proj), "--thread", "v", "--title", "T", "--venue", "nope"])
        self.assertEqual(rc, 3)
        self.assertEqual(ms.main(["init", "--project-dir", str(self.proj), "--thread", "v", "--title", "T",
                                  "--design", "medicion", "--lang", "en", "--venue", "generic"]), 0)

    def test_question_dep_blocks_until_estimada(self):
        ms.init(self.proj, "med", "T", None, design="medicion")
        self.question("en_recoleccion")
        self.assertEqual(ms.bind(self.proj, "med", "results", "Q-9001"), ["Q-9001"])
        cov = ms.coverage(self.tmp, self.proj, "med")
        sec = next(s for s in cov["sections"] if s["id"] == "results")
        self.assertFalse(sec["ready"])
        self.assertNotEqual(sec["state"], "respaldada")
        self.assertTrue(any("en_recoleccion" in m for m in sec["deps"][0]["missing"]))
        with self.assertRaises(ms.Refused):
            ms.write_section(self.tmp, self.proj, "med", "results", "texto")
        self.assertFalse(ms.dep_state(self.tmp, "Q-9001")["ok"])


class TestBind(Base):
    def test_binds_existing_notes_once_and_keeps_the_rest(self):
        o, _ = ms.init(self.proj, "t", "T", "usenix")
        self.assertEqual(ms.bind(self.proj, "t", "resultados", "C-9001"), ["C-9001"])
        self.assertEqual(ms.bind(self.proj, "t", "resultados", "C-9001"), ["C-9001"])
        meta, sections, body = ms.parse_outline(o)
        self.assertEqual(meta["venue"], "usenix")
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


def _real_pandoc():
    import shutil
    return shutil.which("pandoc")


REAL_PANDOC = _real_pandoc()


class ExportBase(CoverageBase):
    """export --format latex: pandoc and latexmk are fake executables on a temp PATH."""

    def setUp(self):
        super().setUp()
        import os
        self.old_path = os.environ.get("PATH", "")
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        os.environ["PATH"] = str(self.bin)
        (self.vault / "Papers").mkdir()
        (self.vault / "Papers" / "P-9001 demo.md").write_text(
            '---\nid: P-9001\ntitle: "Widgets azules"\nauthors: ["Zeta, Ana"]\nyear: 2020\n---\n', encoding="utf-8")
        for k in ("trabajo-relacionado", "planteamiento", "discusion"):
            self.fill(k)
        self.fill("introduccion", "Texto citado en P-9001 y otra vez P-9001.")
        r = self.claim("resultado_intermedio", "Un resultado inventado.")
        self.cs.main(["set", "--note", str(r), "--status", "probado", "--by", "t", "--evidence", "x"])
        ms.bind(self.proj, "t", "resultados", r.stem)
        self.fill("resultados", "Resultado 1.")

    def tearDown(self):
        import os
        os.environ["PATH"] = self.old_path
        super().tearDown()

    def fill(self, sec, text="Texto."):
        import re
        _, m = ms.paths(self.proj, "t")
        raw = m.read_text(encoding="utf-8")
        raw = re.sub(r"(<!-- kairo:section %s -->\n).*?(?=\n## |\Z)" % sec,
                     lambda x: x.group(1) + text + "\n", raw, flags=re.S)
        m.write_text(raw, encoding="utf-8", newline="\n")

    def fake(self, name, body):
        script = self.bin / f"{name}_fake.py"
        script.write_text(body, encoding="utf-8")
        if sys.platform == "win32":
            (self.bin / f"{name}.cmd").write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        else:
            exe = self.bin / name
            exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
            exe.chmod(0o755)

    def pandoc(self):
        self.fake("pandoc", "import sys\nsys.stdout.write(sys.stdin.read())\n")

    def latexmk(self, refs=12, rc=0, label=True, log=""):
        aux = "\\\\newlabel{kairo:refs-start}{{}{%d}{x}{}{}}" % refs if label else ""
        self.fake("latexmk", "import sys\n"
                  f"open('main.aux', 'w').write('{aux}' + chr(10))\n"
                  f"open('main.log', 'w').write({log!r} + chr(10))\n"
                  f"sys.exit({rc})\n")

    def run_cmd(self, cmd, *extra):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = ms.main([cmd, "--vault", str(self.vault), "--project-dir", str(self.proj), "--thread", "t", *extra])
        return rc, out.getvalue(), err.getvalue()

    def run_export(self, *extra):
        return self.run_cmd("export", "--format", "latex", *extra)

    def set_venue(self, venue):
        import re
        o, _ = ms.paths(self.proj, "t")
        raw = o.read_text(encoding="utf-8")
        o.write_text(re.sub(r"^venue:.*$", f"venue: {venue}", raw, flags=re.M), encoding="utf-8", newline="\n")


class TestExport(ExportBase):

    def test_export_refuses_without_pandoc(self):
        rc, _, err = self.run_export()
        self.assertEqual(rc, 3)
        self.assertIn("winget install --id JohnMacFarlane.Pandoc", err)

    def test_export_writes_tex_with_venue_class(self):
        self.pandoc()
        self.set_venue("acm-sigconf")
        rc, out, _ = self.run_export()
        self.assertEqual(rc, 0)
        res = json.loads(out)
        tex = Path(res["tex"]).read_text(encoding="utf-8")
        self.assertIn("\\documentclass[sigconf]{acmart}", tex)
        self.assertIn("\\bibliography{refs}", tex)
        self.assertIn("\\label{kairo:refs-start}", tex)
        self.assertNotIn("babel", tex)
        self.assertIn("\\cite{", tex)
        self.assertNotIn("P-9001", tex)
        self.assertIn("kairo:P-9001", Path(res["bib"]).read_text(encoding="utf-8"))
        self.assertEqual((res["pages"], res["over"], res["limit"]), (None, None, 12))
        self.assertNotIn("warning", res)

    def test_usenix_package_and_babel(self):
        self.pandoc()
        self.set_venue("usenix")
        rc, out, _ = self.run_export()
        tex = Path(json.loads(out)["tex"]).read_text(encoding="utf-8")
        self.assertIn("\\documentclass{article}", tex)
        self.assertIn("\\usepackage{usenix-2020-09}", tex)
        self.assertIn("\\usepackage[spanish]{babel}", tex)

    def test_unknown_venue_is_generic_with_warning(self):
        self.pandoc()
        self.set_venue("por decidir")
        rc, out, _ = self.run_export()
        res = json.loads(out)
        self.assertEqual((rc, res["limit"]), (0, 0))
        self.assertEqual(res["warning"], "venue desconocido: se usa generic")

    def test_page_limit_reported(self):
        self.pandoc()
        self.latexmk(12)
        self.set_venue("acm-sigconf")
        rc, out, _ = self.run_export()
        res = json.loads(out)
        self.assertEqual((rc, res["pages"], res["over"], res["limit"]), (0, 12, False, 12))

    def test_page_limit_exceeded(self):
        self.pandoc()
        self.latexmk(14)
        self.set_venue("acm-sigconf")
        res = json.loads(self.run_export()[1])
        self.assertEqual((res["pages"], res["over"]), (14, True))

    def test_latex_failure_nulls_pages_and_warns(self):
        self.pandoc()
        self.latexmk(12, rc=12, log="! Undefined control sequence.")
        self.set_venue("acm-sigconf")
        res = json.loads(self.run_export()[1])
        self.assertEqual((res["pages"], res["over"]), (None, None))
        self.assertEqual(res["warning"], "latex_failed: ! Undefined control sequence.")

    def test_missing_label_is_null_with_warning(self):
        self.pandoc()
        self.latexmk(label=False)
        self.set_venue("acm-sigconf")
        res = json.loads(self.run_export()[1])
        self.assertEqual((res["pages"], res["over"]), (None, None))
        self.assertIn("kairo:refs-start", res["warning"])

    def test_cites_skip_code_urls_and_report_uncited(self):
        self.pandoc()
        self.fill("introduccion", "Ver P-9001, `P-9001`, https://x.org/P-9001 y P-9002.\n\n```\nP-9001\n```\n")
        res = json.loads(self.run_export()[1])
        tex = Path(res["tex"]).read_text(encoding="utf-8")
        self.assertEqual(tex.count("\\cite{"), 1)
        self.assertNotIn("{=latex}", tex)
        self.assertNotIn("KAIROCITE", tex)
        self.assertIn("`P-9001`", tex)
        self.assertIn("https://x.org/P-9001", tex)
        self.assertEqual(res["uncited"], ["P-9002"])

    def test_escapes_and_author_and_no_bibliography_without_cites(self):
        self.pandoc()
        self.fill("introduccion", "Sin citas.")
        o, _ = ms.paths(self.proj, "t")
        o.write_text(o.read_text(encoding="utf-8").replace("Paper inventado", "A~B^C\\\\D"), encoding="utf-8", newline="\n")
        self.set_venue("acm-sigconf")
        res = json.loads(self.run_export()[1])
        tex = Path(res["tex"]).read_text(encoding="utf-8")
        self.assertIn("\\author{Autores por decidir}", tex)
        self.assertIn("\\textasciitilde{}", tex)
        self.assertIn("\\textasciicircum{}", tex)
        self.assertIn("\\textbackslash{}", tex)
        self.assertNotIn("\\bibliography{", tex)
        self.assertTrue(Path(res["bib"]).is_file())

    def test_pandoc_reads_markdown_without_raw_tex(self):
        """FW-8: pandoc runs with raw TeX and raw attributes off; citations reach
        it as opaque tokens and become \\cite in its LaTeX output."""
        self.fake("pandoc", "import sys\n"
                  "open(%r, 'w', encoding='utf-8').write(chr(10).join(sys.argv[1:]))\n"
                  "s = sys.stdin.read()\n"
                  "open(%r, 'a', encoding='utf-8').write(s)\n"
                  "sys.stdout.write(s)\n" % (str(self.bin / "argv.txt"), str(self.bin / "stdin.txt")))
        self.fill("introduccion", "Texto citado en P-9001 y otra vez P-9001; literal KAIROCITE0001.")
        res = json.loads(self.run_export()[1])
        argv = (self.bin / "argv.txt").read_text(encoding="utf-8").split("\n")
        self.assertIn("markdown-raw_tex-raw_attribute", argv)
        import re
        fed = (self.bin / "stdin.txt").read_text(encoding="utf-8")
        nonces = set(re.findall(r"KAIROCITE([0-9a-f]{8})N0001", fed))
        self.assertEqual(len(nonces), 1)   # a random per-export nonce, never the bare number
        tex = Path(res["tex"]).read_text(encoding="utf-8")
        self.assertEqual(tex.count("\\cite{"), 2)
        self.assertIn("literal KAIROCITE0001.", tex)   # literal text never collides with a token
        self.assertNotIn("KAIROCITE" + nonces.pop(), tex)
        self.assertNotIn("{=latex}", tex)

    def test_math_with_a_dangerous_command_refuses_naming_the_section(self):
        cases = {"Antes \\(\\input{x}\\) despues": "\\input", "Ver $\\openin1=x$.": "$",
                 "\\[\\csname input\\endcsname{x}\\]": "\\csname", "\\(x^^5cinput{y}\\)": "^^",
                 "\\(a \\) \\input{x} \\( b\\)": "\\input",            # the `\)` split
                 "\\(a \\( b\\)": "\\(", "\\(a\\]": "\\]",
                 "\\(\\InputIfFileExists{x}{}{}\\)": "\\InputIfFileExists",
                 "\\(\\newcommand{\\x}{y}\\)": "\\newcommand", "\\(\\IfFileExists{x}{}{}\\)": "\\IfFileExists",
                 "\\begin{verbatim}\nhola\n\\end{verbatim}\n": "\\begin{verbatim}",
                 "\\begin{filecontents}{x}\\end{filecontents}": "\\begin{filecontents}"}
        for body, bad in cases.items():
            self.fake("pandoc", "import sys\nsys.stdin.read()\nsys.stdout.write(%r)\n" % body)
            rc, _, err = self.run_export()
            self.assertEqual(rc, 3, body)
            self.assertIn("introduccion", err)
            self.assertIn("`" + bad + "`", err)
        ok = ("\\(\\frac{a}{b} \\le \\alpha\\) y \\[x^2 \\, \\{y\\}\\] \\emph{a} \\textbackslash{}input\n"
              "\\begin{enumerate}\n\\def\\labelenumi{(\\arabic{enumi})}\n\\tightlist\n\\item\n  a\n\\end{enumerate}\n")
        self.fake("pandoc", "import sys\nsys.stdin.read()\nsys.stdout.write(%r)\n" % ok)
        rc, _, err = self.run_export()
        self.assertEqual(rc, 0, err)

    def test_split_math_and_allowed_math_with_real_pandoc(self):
        if REAL_PANDOC is None:
            self.skipTest("pandoc no está instalado")
        import shutil
        shutil.copy(REAL_PANDOC, self.bin / Path(REAL_PANDOC).name)
        for body in ("Una $a \\) \\input{x} \\( b$ inventada.", "Una $\\InputIfFileExists{x}{}{}$.",
                     "Una $\\newcommand{\\x}{y}$."):
            self.fill("discusion", body)
            rc, _, err = self.run_export()
            self.assertEqual(rc, 3, body)
            self.assertIn("discusion", err)
        self.fill("discusion", "Segun P-9001, $\\frac{a}{b} \\le \\alpha$ y $$\\sum_i x_i$$.\n\n1. uno\n2. dos\n")
        rc, out, err = self.run_export()
        self.assertEqual(rc, 0, err)
        tex = Path(json.loads(out)["tex"]).read_text(encoding="utf-8")
        self.assertIn("\\frac{a}{b}", tex)
        self.assertEqual(tex.count("\\cite{"), 3)

    def test_math_input_refused_with_real_pandoc(self):
        if REAL_PANDOC is None:
            self.skipTest("pandoc no está instalado")
        import shutil
        shutil.copy(REAL_PANDOC, self.bin / Path(REAL_PANDOC).name)
        self.fill("discusion", "Una formula $\\input{x}$ inventada.")
        rc, _, err = self.run_export()
        self.assertEqual(rc, 3)
        self.assertIn("discusion", err)
        self.fill("discusion", "Una formula $x^2 + \\alpha$ inventada.")
        rc, _, err = self.run_export()
        self.assertEqual(rc, 0, err)

    def test_latexmk_runs_without_shell_escape_and_paranoid_file_access(self):
        import os
        self.pandoc()
        self.fake("latexmk", "import os, sys\n"
                  "open('argv.txt', 'w').write(chr(10).join(sys.argv[1:]))\n"
                  "open('env.txt', 'w').write(','.join(os.environ.get(k, '-') for k in "
                  "('openin_any', 'openout_any', 'TEXINPUTS', 'TEXMFHOME', 'BSTINPUTS', 'KAIRO_KEEP')))\n"
                  "open('main.aux', 'w').write('')\n")
        saved = {k: os.environ.get(k) for k in ("TEXINPUTS", "TEXMFHOME", "BSTINPUTS", "KAIRO_KEEP")}
        os.environ.update(TEXINPUTS="evil//", TEXMFHOME="evil", BSTINPUTS="evil", KAIRO_KEEP="si")
        try:
            res = json.loads(self.run_export()[1])
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        out = Path(res["tex"]).parent
        argv = (out / "argv.txt").read_text().split("\n")
        self.assertIn("-no-shell-escape", argv)
        self.assertEqual((out / "env.txt").read_text(), "p,p,-,-,-,si")

    def test_raw_latex_in_a_section_is_escaped_by_real_pandoc(self):
        if REAL_PANDOC is None:
            self.skipTest("pandoc no está instalado")
        import shutil
        shutil.copy(REAL_PANDOC, self.bin / Path(REAL_PANDOC).name)
        self.fill("introduccion", "Antes P-9001 y \\input{x} y `\\input{y}`{=latex} y P-9001.")
        rc, out, err = self.run_export()
        self.assertEqual(rc, 0, err)
        tex = Path(json.loads(out)["tex"]).read_text(encoding="utf-8")
        self.assertNotIn("\\input{", tex)
        self.assertIn("textbackslash", tex)
        self.assertEqual(tex.count("\\cite{"), 2)
        self.assertNotIn("KAIROCITE", tex)

    def test_placeholder_sections_refuse_naming_them(self):
        self.pandoc()
        self.fill("discusion", "_(pendiente — x)_")
        rc, _, err = self.run_export()
        self.assertEqual(rc, 3)
        self.assertIn("discusion", err)

    def test_unsupported_result_section_refuses(self):
        self.pandoc()
        ms.bind(self.proj, "t", "resultados", self.claim("lema", "Un lema sin probar.").stem)
        rc, _, err = self.run_export()
        self.assertEqual(rc, 3)
        self.assertIn("resultados", err)


if __name__ == "__main__":
    unittest.main()



def test_pilot_design_has_no_manuscript_and_is_not_citable():
    with tempfile.TemporaryDirectory() as tmp:
        try:
            ms.design_sections("piloto", "en")
        except ms.Refused as e:
            assert "exploratory" in str(e)
        else:
            raise AssertionError("design_sections accepted the pilot design")
        st = ms.dep_state(Path(tmp), "QP-9004")
        assert st["ok"] is False and "exploratorio" in st["missing"][0]
