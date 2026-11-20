"""Tests for the venue-ready export: custom sections, abstract, appendices, figures, tables,
anonymous mode, page budgets, LaTeX as source, sync-check and check-venue.
Run: python -m pytest scripts/manuscript (invented project PROJ-900, invented text)."""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import latex_parts as lp  # noqa: E402
import manuscript as ms  # noqa: E402
from test_manuscript import REAL_PANDOC, Base, ExportBase  # noqa: E402

PANDOC_TABLE = """\\begin{longtable}[]{@{}lrl@{}}
\\caption{Una tabla}\\tabularnewline
\\toprule\\noalign{}
a & b & c \\\\
\\midrule\\noalign{}
\\endfirsthead
\\toprule\\noalign{}
a & b & c \\\\
\\midrule\\noalign{}
\\endhead
\\bottomrule\\noalign{}
\\endlastfoot
1 & 2 & 3 \\\\
\\end{longtable}

{\\def\\LTcaptype{none} % do not increment counter
\\begin{longtable}[]{@{}
  >{\\raggedright\\arraybackslash}p{(\\columnwidth - 2\\tabcolsep) * \\real{0.5}}
  >{\\raggedright\\arraybackslash}p{(\\columnwidth - 2\\tabcolsep) * \\real{0.5}}@{}}
\\toprule\\noalign{}
x & y \\\\
\\midrule\\noalign{}
\\endhead
\\bottomrule\\noalign{}
\\endlastfoot
5 & 6 \\\\
\\end{longtable}
}
"""


class TestLatexParts(unittest.TestCase):
    def test_longtables_become_booktabs_tabulars(self):
        tex, n = lp.convert_longtables(PANDOC_TABLE, 4)
        self.assertEqual(n, 2)
        for gone in ("longtable", "\\endhead", "\\endfirsthead", "\\endlastfoot", "\\noalign", "LTcaptype"):
            self.assertNotIn(gone, tex)
        self.assertEqual(tex.count("\\begin{table}[t]"), 2)
        self.assertEqual(tex.count("\\toprule"), 2)          # the repeated header is dropped
        self.assertIn("\\caption{Una tabla}\n\\begin{tabular}{@{}lrl@{}}", tex)
        self.assertIn("\\linewidth - 2\\tabcolsep", tex)
        self.assertLess(tex.index("a & b & c"), tex.index("1 & 2 & 3"))
        self.assertLess(tex.index("1 & 2 & 3"), tex.index("\\bottomrule"))
        self.assertFalse(tex.rstrip().endswith("}\n}"))
        wide, _ = lp.convert_longtables(PANDOC_TABLE, 2)          # 3 columns > 2: two columns wide
        self.assertEqual(wide.count("\\begin{table*}"), 1)
        marked, _ = lp.convert_longtables(PANDOC_TABLE, 0, {1})   # marked [[tabla ancha]]
        self.assertIn("\\begin{table*}[t]\n\\centering\n\\begin{tabular}{@{}\n  >{", marked)

    def test_column_count(self):
        for spec, n in (("@{}lrl@{}", 3), ("|c|c|", 2), (">{\\raggedright\\arraybackslash}p{(a) * \\real{0.5}}"
                                                              ">{\\raggedright}p{2cm}", 2), ("lX", 2)):
            self.assertEqual(lp.column_count(spec), n, spec)

    def test_generated_table_allow_list(self):
        ok = ("\\begin{tabular}{lr}\n\\toprule\nHerramienta & $p$ \\\\\n\\midrule\nA & 41.2\\% "
              "$[0.31, 0.45] \\pm 0.1$ \\\\\n\\cmidrule(lr){1-2}\n\\multicolumn{2}{c}{\\textbf{x}} \\\\\n"
              "\\bottomrule\n\\end{tabular}")
        self.assertIsNone(lp.table_file_not_allowed(ok, ms._MATH_CS, ms._SYMBOLS))
        for bad, tok in (("\\input{x}", "\\input"), ("\\begin{verbatim}", "\\begin{verbatim}"),
                         ("a } b {", "}"), ("x^^5c", "^^"), ("a % hidden", "%"), ("$\\write$", "\\write"),
                         ("\\caption{\\immediate}", "\\immediate"), ("$x", "$")):
            self.assertEqual(lp.table_file_not_allowed(bad, ms._MATH_CS, ms._SYMBOLS), tok, bad)

    def test_labels_pages_and_budgets(self):
        aux = ("\\newlabel{kairo:sec:intro}{{1}{1}{Intro}{section.1}{}}\n"
               "\\newlabel{kairo:sec:results}{{2}{3}{Results}{section.2}{}}\n"
               "\\newlabel{kairo:sec:results-a}{{2.1}{3}{A}{subsection.2.1}{}}\n"
               "\\newlabel{kairo:sec:results-b}{{2.2}{6}{B}{subsection.2.2}{}}\n"
               "\\newlabel{kairo:body-end}{{2.2}{9}}\n\\newlabel{kairo:refs-start}{{2.2}{9}}\n"
               "\\newlabel{kairo:sec:ethics}{{A}{10}{Ethics}{appendix.A}{}}\n\\newlabel{kairo:end}{{A}{12}}\n")
        labels = lp.aux_labels(aux)
        self.assertEqual(lp.body_pages(labels), 9)
        order = ms.page_order({"body": [{"id": "intro"}, {"id": "results", "budget_pages": 5},
                                        {"id": "results-a", "parent": "results", "budget_pages": 2},
                                        {"id": "results-b", "parent": "results"}],
                               "pre_refs": [], "appendix": [{"id": "ethics", "budget_pages": 1}]})
        got = {x["section"]: (x["pages"], x["over"]) for x in lp.section_pages(labels, order)}
        self.assertEqual(got, {"intro": (2, False), "results": (6, True), "results-a": (3, True),
                               "results-b": (3, False), "ethics": (2, True)})
        self.assertEqual(lp.body_pages(lp.aux_labels("\\newlabel{kairo:refs-start}{{}{7}{x}{}{}}")), 7)

    def test_numbers_from_estimates(self):
        values = lp.estimate_numbers({"estimates": [{"id": "e1", "value": 0.41234, "lo": 0.3, "hi": 0.52,
                                                     "n": 1234, "ok": True, "method": "wilson"}]})
        self.assertEqual(values, [0.41234, 0.3, 0.52, 1234.0])
        for tok in ("41.2", "0.41", "41", "41,2", "1,234", "1234", "0.30"):
            self.assertTrue(lp.from_estimate(tok, values), tok)
        for tok in ("41.3", "0.42", "7"):
            self.assertFalse(lp.from_estimate(tok, values), tok)
        md = lp.numbers_md("Un 41.2% (IC 30.0–52.0) de 1234 widgets inventados, Q-9001, [[figura F-001]].")
        self.assertEqual(md, ["41.2", "30.0", "52.0", "1234"])
        tex = lp.numbers_tex("Un 43.0\\% (IC 30.0--52.0) de 1234, véase \\ref{fig:F-001} y \\cite{x2020a} "
                             "\\label{kairo:sec:r1}\n% 41.2 en un comentario\n\\begin{tabular}{@{}lr@{}} 9 \\\\")
        self.assertEqual(tex, ["43.0", "30.0", "52.0", "1234", "9"])
        cmp = lp.compare_numbers(md, tex, values)
        self.assertEqual(cmp, {"changed": [{"vault": "41.2", "tex": "43.0"}], "removed": [], "added": ["9"]})

    def test_cites_bib_and_graphics(self):
        self.assertEqual(lp.cite_keys("\\cite{a,b} \\citep[p.~3]{c} % \\cite{d}\n\\nocite{a}"), ["a", "b", "c"])
        bib = "@article{zeta2020widgets,\n  title = {X},\n  keywords = {kairo:P-9001}\n}\n\n@misc{otro,\n  title = {Y}\n}\n"
        self.assertEqual(lp.bib_entries(bib), {"zeta2020widgets": "P-9001", "otro": None})
        self.assertEqual(lp.included_graphics("\\includegraphics[width=\\linewidth]{figuras/F-001.pdf}"),
                         ["figuras/F-001.pdf"])


SECTIONS = {
    "keywords": ["widgets", "medición"],
    "sections": [
        {"id": "abstract", "kind": "prosa", "placement": "front"},
        {"id": "introduction", "kind": "prosa", "budget_pages": 1.5},
        {"id": "results", "kind": "resultado", "subsections": [
            {"id": "results-q1", "title": "Pregunta uno", "kind": "resultado"},
            {"id": "results-q2", "title": "Pregunta dos", "kind": "resultado"}]},
        {"id": "discussion", "kind": "cierre"},
        {"id": "ethics", "kind": "prosa", "placement": "appendix"},
        {"id": "open-science", "kind": "prosa", "placement": "appendix"},
        {"id": "acknowledgments", "kind": "prosa"},
        {"id": "ai-disclosure", "kind": "prosa", "placement": "appendix"},
    ]}


class TestCustomSections(Base):
    def write_json(self, data, name="secciones.json"):
        p = self.tmp / name
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return p

    def test_init_with_sections_file_round_trips(self):
        o, m = ms.init(self.proj, "s", "T", "usenix", lang="en", sections_file=self.write_json(SECTIONS))
        meta, sections, _ = ms.parse_outline(o)
        self.assertEqual(meta["keywords"], ["widgets", "medición"])
        self.assertEqual([s["id"] for s in sections][:5],
                         ["abstract", "introduction", "results", "results-q1", "results-q2"])
        q1 = next(s for s in sections if s["id"] == "results-q1")
        self.assertEqual((q1["parent"], q1["placement"], q1["title"]), ("results", "body", "Pregunta uno"))
        self.assertEqual(next(s for s in sections if s["id"] == "introduction")["budget_pages"], 1.5)
        self.assertEqual(next(s for s in sections if s["id"] == "ethics")["title"], "Ethics")
        text = m.read_text(encoding="utf-8")
        self.assertIn("### Pregunta uno\n\n<!-- kairo:section results-q1 -->", text)
        bodies = ms.section_bodies(m)
        self.assertEqual(bodies["results"], "")                     # a parent's own text is optional
        self.assertTrue(bodies["results-q1"].startswith("_(pendiente"))

    def test_bad_sections_are_refused(self):
        for bad in ([], [{"id": "Mal", "kind": "prosa"}], [{"id": "a", "kind": "nope"}],
                    [{"id": "a", "kind": "prosa"}, {"id": "a", "kind": "prosa"}],
                    [{"id": "a", "kind": "prosa", "placement": "lateral"}],
                    [{"id": "a", "kind": "prosa", "placement": "front"}, {"id": "b", "kind": "prosa", "placement": "front"}],
                    [{"id": "a", "kind": "resultado", "placement": "front"}],
                    [{"id": "a", "kind": "prosa", "subsections": [{"id": "b", "kind": "prosa", "placement": "appendix"}]}],
                    [{"id": "a", "kind": "prosa", "subsections": [{"id": "b", "kind": "prosa", "subsections": [
                        {"id": "c", "kind": "prosa"}]}]}],
                    [{"id": "a", "kind": "prosa", "budget_pages": -1}],
                    [{"id": "a", "kind": "prosa", "depends_on": ["X-1"]}],
                    [{"id": "a", "kind": "prosa", "extra": 1}]):
            with self.subTest(bad=bad), self.assertRaises(ms.Refused):
                ms.normalize_sections(bad, "es")

    def test_subsection_coverage_and_writing(self):
        (self.proj / "Claims" / "C-9001.md").unlink()
        sys.path.insert(0, str(HERE.parent / "ledger"))
        import io
        from contextlib import redirect_stderr, redirect_stdout

        import claim_status
        ms.init(self.proj, "s", "T", None, sections_file=self.write_json(SECTIONS))
        buf = io.StringIO()
        with redirect_stdout(buf):
            claim_status.main(["new", "--project-dir", str(self.proj), "--kind", "resultado_intermedio",
                               "--statement", "Un resultado inventado.", "--how", "…", "--by", "t"])
        c = Path(buf.getvalue().strip())
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            claim_status.main(["set", "--note", str(c), "--status", "probado", "--by", "t", "--evidence", "x"])
        ms.bind(self.proj, "s", "results-q1", c.stem)
        cov = {s["id"]: s for s in ms.coverage(self.tmp, self.proj, "s")["sections"]}
        self.assertEqual(cov["results-q1"]["state"], "respaldada")
        self.assertEqual(cov["results"]["state"], "pendiente")       # results-q2 has nothing bound
        self.assertFalse(cov["results"]["ready"])
        self.assertEqual(cov["results"]["children"], ["results-q1", "results-q2"])
        ms.write_section(self.tmp, self.proj, "s", "results-q1", "Resultado uno inventado.")
        _, m = ms.paths(self.proj, "s")
        bodies = ms.section_bodies(m)
        self.assertEqual(bodies["results-q1"], "Resultado uno inventado.")
        self.assertTrue(bodies["results-q2"].startswith("_(pendiente"))
        self.assertEqual(bodies["results"], "")
        ms.bind(self.proj, "s", "results-q2", c.stem)
        cov = {s["id"]: s for s in ms.coverage(self.tmp, self.proj, "s")["sections"]}
        self.assertEqual((cov["results"]["state"], cov["results"]["ready"]), ("respaldada", True))
        ms.write_section(self.tmp, self.proj, "s", "results", "Texto común a las dos preguntas.")
        bodies = ms.section_bodies(m)
        self.assertEqual(bodies["results"], "Texto común a las dos preguntas.")
        self.assertEqual(bodies["results-q1"], "Resultado uno inventado.")

    def test_sections_command_restructures_and_keeps_text(self):
        ms.init(self.proj, "s", "T", None)
        ms.bind(self.proj, "s", "resultados", "C-9001")
        _, m = ms.paths(self.proj, "s")
        raw = m.read_text(encoding="utf-8").replace(
            "<!-- kairo:section introduccion -->\n" + ms.PLACEHOLDER["prosa"],
            "<!-- kairo:section introduccion -->\nIntro inventada.\n\n### Un apartado propio\n\nMás texto.")
        m.write_text(raw + "\n## Declaración de uso de IA\n\nTexto generado inventado.\n\n### Trazabilidad\n\nx\n",
                     encoding="utf-8", newline="\n")
        new = [{"id": "abstract", "kind": "prosa", "placement": "front"},
               {"id": "introduccion", "title": "Introducción", "kind": "prosa"},
               {"id": "resultados", "title": "Resultados", "kind": "resultado"},
               {"id": "discusion", "title": "Discusión", "kind": "cierre"}]
        rc = ms.main(["sections", "--project-dir", str(self.proj), "--thread", "s",
                      "--from", str(TestCustomSections.write_json(self, new))])
        self.assertEqual(rc, 0)
        text = m.read_text(encoding="utf-8")
        bodies = ms.section_bodies(m)
        self.assertEqual(bodies["introduccion"], "Intro inventada.\n\n### Un apartado propio\n\nMás texto.")
        self.assertIn("## Declaración de uso de IA\n\nTexto generado inventado.\n\n### Trazabilidad", text)
        self.assertNotIn("trabajo-relacionado", text)
        self.assertLess(text.index("kairo:section abstract"), text.index("kairo:section introduccion"))
        _, sections, _ = ms.parse_outline(ms.paths(self.proj, "s")[0])
        self.assertEqual(next(s for s in sections if s["id"] == "resultados")["depends_on"], ["C-9001"])
        with self.assertRaises(ms.Refused):            # introduccion is written: never dropped
            ms.set_sections(self.proj, "s", [{"id": "resultados", "kind": "resultado"}])


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class VenueBase(ExportBase):
    """The default test thread with an abstract, two appendices and acknowledgments."""

    def setUp(self):
        super().setUp()
        self.pandoc()
        new = [{"id": "abstract", "kind": "prosa", "placement": "front"},
               {"id": "introduccion", "title": "Introducción", "kind": "prosa", "budget_pages": 1},
               {"id": "trabajo-relacionado", "title": "Trabajo relacionado", "kind": "prosa"},
               {"id": "planteamiento", "title": "Planteamiento", "kind": "prosa"},
               {"id": "resultados", "title": "Resultados", "kind": "resultado"},
               {"id": "discusion", "title": "Discusión", "kind": "cierre"},
               {"id": "acknowledgments", "kind": "prosa"},
               {"id": "ethics", "kind": "prosa", "placement": "appendix"},
               {"id": "open-science", "kind": "prosa", "placement": "appendix"},
               {"id": "anexo-datos", "title": "Datos", "kind": "prosa", "placement": "appendix"}]
        ms.set_sections(self.proj, "t", {"keywords": ["widgets azules"], "sections": new})
        for k, text in (("abstract", "Un resumen inventado."), ("acknowledgments", "Gracias a Ana Inventada."),
                        ("ethics", "Ética inventada."), ("open-science", "Ciencia abierta inventada."),
                        ("anexo-datos", "Datos inventados.")):
            self.fill(k, text)
        (self.proj / "Figuras").mkdir()
        self.fig = self.proj / "Figuras" / "f1.pdf"
        self.fig.write_bytes(b"%PDF-1.4 figura inventada\n")
        self.registry([{"id": "F-001", "path": "Figuras/f1.pdf", "sha256": _sha(self.fig),
                        "caption": "Tasa de widgets & 50% azules", "wide": False,
                        "source": {"question": "Q-9001", "estimate_sha256": "0" * 64}, "script": "x.py",
                        "commit": "abc"}])

    def registry(self, entries):
        (self.proj / "Figuras" / "registro.json").write_text(json.dumps(entries), encoding="utf-8")

    def tex(self, out):
        return Path(json.loads(out)["tex"]).read_text(encoding="utf-8")


class TestVenueExport(VenueBase):
    def test_acm_abstract_keywords_appendices_and_labels(self):
        self.set_venue("acm-sigconf")
        rc, out, err = self.run_export()
        self.assertEqual(rc, 0, err)
        tex = self.tex(out)
        self.assertLess(tex.index("\\begin{abstract}\nUn resumen inventado."), tex.index("\\maketitle"))
        self.assertLess(tex.index("\\keywords{widgets azules}"), tex.index("\\maketitle"))
        self.assertIn("% CCS concepts", tex)
        self.assertNotIn("\\section{Resumen}", tex)
        self.assertIn("\\section{Introducción}\\label{kairo:sec:introduccion}", tex)
        self.assertIn("\\section{Agradecimientos}", tex)          # not anonymous: kept
        order = [tex.index(x) for x in ("kairo:sec:discusion", "\\label{kairo:body-end}", "\\label{kairo:refs-start}",
                                        "\\bibliography{refs}", "\\appendix", "kairo:sec:ethics",
                                        "kairo:sec:anexo-datos", "\\label{kairo:end}")]
        self.assertEqual(order, sorted(order))
        for pkg in ("graphicx", "booktabs"):
            self.assertIn(f"\\usepackage{{{pkg}}}", tex)

    def test_usenix_required_appendices_go_before_the_references(self):
        self.set_venue("usenix")
        rc, out, err = self.run_export()
        self.assertEqual(rc, 0, err)
        tex = self.tex(out)
        order = [tex.index(x) for x in ("\\label{kairo:body-end}", "\\appendix",
                                        "\\section{Ethical Considerations}\\label{kairo:sec:ethics}",
                                        "\\section{Open Science}", "\\label{kairo:refs-start}", "\\bibliography{refs}",
                                        "\\section{Datos}")]
        self.assertEqual(order, sorted(order))
        self.assertEqual(tex.count("\\appendix"), 1)
        self.assertLess(tex.index("\\maketitle"), tex.index("\\begin{abstract}"))
        self.assertIn("\\textbf{Palabras clave:} widgets azules", tex)
        self.assertIn("\\author{Autores por decidir}", tex)

    def test_anonymous_mode(self):
        (self.proj / "anonimato.txt").write_text("# nombres y repos\nAna Inventada\nzeta-lab/widgets\n", encoding="utf-8")
        self.fill("discusion", "En nuestro trabajo previo [P-9001] medimos lo mismo; código en github.com/zeta-lab/widgets.")
        self.set_venue("usenix")
        rc, out, err = self.run_export("--anonymous")
        self.assertEqual(rc, 0, err)
        res = json.loads(out)
        tex = self.tex(out)
        self.assertNotIn("\\author", tex)
        self.assertNotIn("Ana Inventada", tex)                     # acknowledgments omitted
        self.assertIn("se omite la sección acknowledgments", res["warning"])
        sev = [(f["severity"], f["detail"]) for f in res["anonymity"]]
        self.assertTrue(any(s == "problema" and "zeta-lab/widgets" in d for s, d in sev), sev)
        self.assertTrue(any(f["severity"] == "aviso" and "autocita" in f["detail"] for f in res["anonymity"]))
        self.set_venue("acm-sigconf")
        rc, out, _ = self.run_export("--anonymous")
        self.assertIn("\\documentclass[sigconf,review,anonymous]{acmart}", self.tex(out))

    def test_registered_figure_and_reference(self):
        self.fill("discusion", "Como muestra la figura [[ref F-001]], el resultado es claro.\n\n[[figura F-001]]\n")
        rc, out, err = self.run_export()
        self.assertEqual(rc, 0, err)
        res = json.loads(out)
        tex = self.tex(out)
        self.assertIn("\\begin{figure}[t]\n\\centering\n\\includegraphics[width=\\linewidth]{figuras/F-001.pdf}\n"
                      "\\caption{Tasa de widgets \\& 50\\% azules}\n\\label{fig:F-001}\n\\end{figure}", tex)
        self.assertIn("la figura \\ref{fig:F-001}, el", tex)
        self.assertNotIn("KAIRO", tex)
        copied = Path(res["tex"]).parent / "figuras" / "F-001.pdf"
        self.assertEqual(copied.read_bytes(), self.fig.read_bytes())
        self.assertEqual(res["figures"], ["F-001"])
        reg = json.loads((self.proj / "Figuras" / "registro.json").read_text(encoding="utf-8"))
        reg[0]["wide"] = True
        self.registry(reg)
        rc, out, _ = self.run_export()
        self.assertIn("\\begin{figure*}[t]", self.tex(out))
        self.assertNotIn("\\Description", self.tex(out))
        self.set_venue("acm-sigconf")                 # acmart asks for an image description
        rc, out, _ = self.run_export()
        self.assertIn("\\caption{Tasa de widgets \\& 50\\% azules}\n\\Description{Tasa de widgets \\& 50\\% azules}\n",
                      self.tex(out))

    def test_figure_refusals(self):
        good = json.loads((self.proj / "Figuras" / "registro.json").read_text(encoding="utf-8"))[0]
        cases = [("[[figura F-002]]", None, "F-002"),
                 ("[[figura F-001]]", dict(good, sha256="1" * 64), "sha256"),
                 ("[[figura F-001]]", dict(good, path="Figuras/nada.pdf"), "falta el fichero"),
                 ("[[figura F-001]]", dict(good, path="Datos/f1.pdf"), "Figuras/"),
                 ("[[figura F-001]]", dict(good, path="Figuras/../_hub.md"), "ruta"),
                 ("Ver [[ref F-009]].", None, "F-009"),
                 ("[[figura F-1]]", None, "marcador no reconocido"),
                 ("Texto [[figura F-001]] en línea.", None, "marcador no reconocido")]
        for body, entry, why in cases:
            with self.subTest(body=body, why=why):
                self.registry([entry or good])
                self.fill("discusion", body)
                rc, _, err = self.run_export()
                self.assertEqual(rc, 3, err)
                self.assertIn(why, err)
                self.assertIn("discusion", err)

    def test_generated_table_is_inlined_and_checked(self):
        tabs = self.proj / "Manuscritos" / "tablas"
        tabs.mkdir()
        (tabs / "t1.tex").write_text("\\begin{tabular}{lrrrrr}\n\\toprule\nA & 1 & 2 & 3 & 4 & 5 \\\\\n"
                                     "\\bottomrule\n\\end{tabular}\n", encoding="utf-8")
        self.set_venue("acm-sigconf")
        self.fill("discusion", "Antes.\n\n[[tabla-latex t1.tex]]\n\nDespués.")
        rc, out, err = self.run_export()
        self.assertEqual(rc, 0, err)
        tex = self.tex(out)
        self.assertIn("\\begin{table*}[t]\n\\centering\n\\begin{tabular}{lrrrrr}", tex)   # 6 columns > 4
        self.assertNotIn("\\input", tex)
        (tabs / "t2.tex").write_text("\\begin{tabular}{l}\nA \\\\\n\\input{secreto}\n\\end{tabular}\n", encoding="utf-8")
        for body, why in (("[[tabla-latex t2.tex]]", "\\input"), ("[[tabla-latex ../x.tex]]", "tablas/"),
                          ("[[tabla-latex t9.tex]]", "falta la tabla")):
            self.fill("discusion", body)
            rc, _, err = self.run_export()
            self.assertEqual(rc, 3, body)
            self.assertIn(why, err)

    def test_wide_marker_needs_a_table(self):
        self.fill("discusion", "[[tabla ancha]]\n\nSin tabla.")
        rc, _, err = self.run_export()
        self.assertEqual(rc, 3)
        self.assertIn("tabla ancha", err)

    def test_pandoc_tables_with_real_pandoc(self):
        if REAL_PANDOC is None:
            self.skipTest("pandoc no está instalado")
        import shutil
        for f in self.bin.iterdir():
            f.unlink()
        shutil.copy(REAL_PANDOC, self.bin / Path(REAL_PANDOC).name)
        self.set_venue("acm-sigconf")
        self.fill("discusion", "| a | b |\n|---|--:|\n| 1 | 2 |\n\n: Tabla inventada\n\n[[tabla ancha]]\n\n"
                               "| x | y |\n|---|---|\n| 3 | 4 |\n\n[[figura F-001]]\n")
        rc, out, err = self.run_export()
        self.assertEqual(rc, 0, err)
        tex = self.tex(out)
        self.assertNotIn("longtable", tex)
        self.assertIn("\\begin{table}[t]\n\\centering\n\\caption{Tabla inventada}\n\\begin{tabular}{@{}lr@{}}", tex)
        self.assertEqual(tex.count("\\begin{table*}"), 1)
        self.assertIn("\\includegraphics[width=\\linewidth]{figuras/F-001.pdf}", tex)


class TestPagesAndSource(VenueBase):
    AUX = ("\\newlabel{kairo:sec:introduccion}{{1}{1}{x}{section.1}{}}\n"
           "\\newlabel{kairo:sec:trabajo-relacionado}{{2}{3}{x}{section.2}{}}\n"
           "\\newlabel{kairo:sec:planteamiento}{{3}{4}{x}{section.3}{}}\n"
           "\\newlabel{kairo:body-end}{{3}{%d}}\n\\newlabel{kairo:refs-start}{{3}{%d}}\n"
           "\\newlabel{kairo:sec:ethics}{{A}{%d}{x}{appendix.A}{}}\n")

    def latexmk_aux(self, body):
        aux = self.AUX % (body, body, body + 1)
        self.fake("latexmk", f"open('main.aux', 'w').write({aux!r})\nopen('main.log', 'w').write('')\n")

    def test_pages_per_section_after_build(self):
        self.latexmk_aux(14)
        self.set_venue("usenix")
        rc, out, err = self.run_export()
        res = json.loads(out)
        self.assertEqual((res["pages"], res["over"], res["limit"]), (14, True, 13))
        per = {x["section"]: x for x in res["sections_pages"]}
        self.assertEqual((per["introduccion"]["pages"], per["introduccion"]["budget"], per["introduccion"]["over"]),
                         (2, 1, True))
        rc, out, _ = self.run_cmd("pages", "--no-build")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["pages"], 14)

    def test_latex_source_is_never_overwritten_without_force(self):
        rc, out, err = self.run_export("--to-latex-source")
        self.assertEqual(rc, 0, err)
        folder = Path(json.loads(out)["tex"]).parent
        mark = json.loads((folder / "kairo-source.json").read_text(encoding="utf-8"))
        self.assertEqual(mark["main_tex_sha256"], _sha(folder / "main.tex"))
        (folder / "main.tex").write_text("pulido a mano", encoding="utf-8")
        rc, _, err = self.run_export()
        self.assertEqual(rc, 3)
        self.assertIn("sync-check", err)
        self.assertEqual((folder / "main.tex").read_text(encoding="utf-8"), "pulido a mano")
        rc, _, err = self.run_export("--force")
        self.assertEqual(rc, 0, err)
        self.assertFalse((folder / "kairo-source.json").exists())
        backups = list(folder.glob("main.*.tex.bak"))
        self.assertEqual([b.read_text(encoding="utf-8") for b in backups], ["pulido a mano"])


QUESTION = """---
id: Q-9001
project: PROJ-900
design: medicion
status: estimada
history: []
---

# Q-9001

## Resultado

```estimate-json
{"plan": "M-9001", "units_sha256": "%s", "estimates": [{"id": "e1", "type": "proporcion", "value": 0.41234, "lo": 0.30, "hi": 0.52, "n": 1234}]}
```
""" % ("0" * 64)


class TestSyncCheck(VenueBase):
    def setUp(self):
        super().setUp()
        (self.proj / "Preguntas").mkdir()
        (self.proj / "Preguntas" / "Q-9001.md").write_text(QUESTION, encoding="utf-8")
        o, _ = ms.paths(self.proj, "t")
        o.write_text(o.read_text(encoding="utf-8").replace(
            "  - id: resultados\n    title: \"Resultados\"\n    kind: resultado\n    depends_on: [",
            "  - id: resultados\n    title: \"Resultados\"\n    kind: resultado\n    depends_on: [Q-9001, "),
            encoding="utf-8", newline="\n")
        self.fill("resultados", "Un 41.2% de los 1234 widgets inventados (IC 30.0–52.0) son azules [[ref F-001]].")
        self.out = self.proj / "Manuscritos" / "latex-t"
        self.out.mkdir(parents=True)
        (self.out / "figuras").mkdir()
        (self.out / "figuras" / "F-001.pdf").write_bytes(self.fig.read_bytes())
        key = ms._cite_keys(self.vault)["P-9001"]
        (self.out / "refs.bib").write_text("@article{%s,\n  title = {X},\n  keywords = {kairo:P-9001}\n}\n" % key,
                                           encoding="utf-8")
        self.key = key

    def main_tex(self, results):
        (self.out / "main.tex").write_text(
            "\\begin{document}\n\\section{Intro}\\label{kairo:sec:introduccion}\nVer \\cite{%s}.\n"
            "\\section{Resultados}\\label{kairo:sec:resultados}\n%s\n"
            "\\begin{figure}[t]\n\\includegraphics[width=\\linewidth]{figuras/F-001.pdf}\n\\caption{x 99}\n\\end{figure}\n"
            "\\section{Discusión}\\label{kairo:sec:discusion}\n12 widgets.\n\\label{kairo:body-end}\n"
            "\\end{document}\n" % (self.key, results), encoding="utf-8")

    def test_clean_main_tex_passes(self):
        self.main_tex("Un 41.2\\% de los 1234 widgets inventados (IC 30.0--52.0) son azules \\ref{fig:F-001}.")
        rc, out, err = self.run_cmd("sync-check")
        res = json.loads(out)
        self.assertEqual(rc, 0, res)
        self.assertEqual(res["sections"], [{"section": "resultados", "changed": [], "removed": [], "added": []}])
        self.assertEqual(res["figures"], [{"file": "figuras/F-001.pdf", "figure": "F-001"}])

    def test_changed_numbers_cites_and_figures_fail(self):
        self.main_tex("Un 43.0\\% de los widgets (IC 30.0--52.0), en 7 tipos, \\cite{nadie} \\cite{otro}.")
        with open(self.out / "refs.bib", "a", encoding="utf-8") as fh:
            fh.write("\n@misc{otro,\n  title = {Y}\n}\n")
        (self.out / "figuras" / "F-001.pdf").write_bytes(b"otra figura")
        rc, out, _ = self.run_cmd("sync-check")
        self.assertEqual(rc, 4)
        res = json.loads(out)
        sec = res["sections"][0]
        self.assertEqual(sec["changed"], [{"vault": "41.2", "tex": "43.0"}])
        self.assertEqual(sec["removed"], ["1234"])
        self.assertEqual(sec["added"], ["7"])
        self.assertEqual(res["cites"], {"unknown": ["nadie"], "no_note": ["otro"]})
        self.assertIn("problem", res["figures"][0])

    def test_lost_label_is_a_problem(self):
        self.main_tex("Un 41.2\\% de los 1234 (IC 30.0--52.0).")
        tex = (self.out / "main.tex").read_text(encoding="utf-8").replace("\\label{kairo:sec:resultados}", "")
        (self.out / "main.tex").write_text(tex, encoding="utf-8")
        rc, out, _ = self.run_cmd("sync-check")
        self.assertEqual(rc, 4)
        self.assertTrue(any("kairo:sec:resultados" in p for p in json.loads(out)["problems"]))


class TestCheckVenue(VenueBase):
    def findings(self, *extra):
        rc, out, err = self.run_cmd("check-venue", *extra)
        return rc, json.loads(out) if out else err

    def test_usenix_rules(self):
        self.set_venue("usenix")
        (self.proj / "anonimato.txt").write_text("Ana Inventada\n", encoding="utf-8")
        self.fill("open-science", "_(pendiente — x)_")
        self.fill("discusion", "As we previously showed in our prior work, see https://github.com/zeta-lab/widgets.")
        rc, f = self.findings()
        self.assertEqual(rc, 4)
        got = {(x["check"], x["severity"]) for x in f}
        self.assertIn(("apendices", "problema"), got)          # open-science unwritten
        self.assertIn(("declaracion_ia", "problema"), got)     # no disclosure
        self.assertIn(("paginas", "aviso"), got)               # not exported yet
        details = " ".join(x["detail"] for x in f)
        self.assertIn("Agradecimientos", details)              # acknowledgments with text
        self.assertIn("github.com/zeta-lab/widgets", details)
        self.assertIn("autocita", details)
        self.assertNotIn("Ana Inventada»", details)             # only in acknowledgments, which is not sent

    def test_clean_paper_passes_and_pages_are_checked(self):
        self.set_venue("usenix")
        self.fill("acknowledgments", "_(pendiente — x)_")
        ms.set_sections(self.proj, "t", [s for s in ms.parse_outline(ms.paths(self.proj, "t")[0])[1]
                                         if not s.get("parent")] + [{"id": "ai-disclosure", "kind": "prosa",
                                                                     "placement": "appendix"}])
        self.fill("ai-disclosure", "Declaración inventada.")
        rc, f = self.findings()
        self.assertEqual([x for x in f if x["severity"] == "problema"], [])
        self.assertEqual(rc, 0)
        folder = self.proj / "Manuscritos" / "latex-t"
        folder.mkdir(parents=True)
        (folder / "main.tex").write_text("x", encoding="utf-8")
        (folder / "main.aux").write_text("\\newlabel{kairo:body-end}{{3}{15}}\n"
                                         "\\newlabel{kairo:sec:ethics}{{A}{15}{x}{a}{}}\n"
                                         "\\newlabel{kairo:sec:open-science}{{B}{18}{x}{a}{}}\n"
                                         "\\newlabel{kairo:refs-start}{{B}{19}}\n", encoding="utf-8")
        rc, f = self.findings()
        self.assertEqual(rc, 4)
        details = [x["detail"] for x in f if x["severity"] == "problema"]
        self.assertTrue(any("15 páginas" in d for d in details), details)
        self.assertTrue(any("apéndice ethics" in d for d in details), details)
        rc, f = self.findings("--venue", "generic")
        self.assertEqual(rc, 0, f)

    def test_internal_disclosure_parts_are_a_problem(self):
        self.fill("discusion", "### Trazabilidad (interno — eliminar antes de enviar)\n\nx")
        _, f = self.findings()
        self.assertTrue(any("eliminar antes de enviar" in x["detail"] for x in f))


if __name__ == "__main__":
    unittest.main()


class TablaLatexIntegracion(unittest.TestCase):
    """estimate.py tabla-latex output passes the exporter's table allow-list as it is."""

    def test_generated_table_with_provenance_comment_is_allowed(self):
        import json as _json
        import subprocess as _sp
        import tempfile as _tf
        est = {"plan": "M-9001", "units_sha256": "0" * 64, "estimates": [
            {"id": "est-1", "type": "proporcion", "value": 0.125, "lo": 0.1, "hi": 0.15, "n": 800,
             "method": "wilson"}]}
        with _tf.TemporaryDirectory() as d:
            f = Path(d) / "e.json"
            f.write_text(_json.dumps(est), encoding="utf-8")
            script = Path(__file__).resolve().parents[1] / "analysis" / "estimate.py"
            r = _sp.run([sys.executable, str(script), "tabla-latex", "--json", str(f), "--id", "est-1"],
                        capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            tex = r.stdout.decode("utf-8")
        self.assertIn("Método", tex)
        self.assertTrue(tex.startswith("%"))
        self.assertIsNone(lp.table_file_not_allowed(tex, ms._MATH_CS, ms._SYMBOLS))

    def test_comment_inside_a_line_still_refused(self):
        self.assertEqual(lp.table_file_not_allowed("\begin{tabular}{l}\na % x\n\end{tabular}",
                                                   ms._MATH_CS, ms._SYMBOLS), "%")
