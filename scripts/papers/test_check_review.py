"""Tests for check_review.py. Run: python -m pytest scripts/papers

Invented fixture papers only (P-0901 from the verifier tests, P-0902 made here)."""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "ledger"))
import check_review  # noqa: E402
from test_verifier_scripts import build_fixture  # noqa: E402

CONFLICT = """---
id: P-0902
title: An invented paper whose identifiers disagree
projects: [PROJ-900]
resolution_status: mismatch
---

## Resumen

An invented abstract about toy embeddings of small graphs.
"""

REVIEW = """## Qué es el problema

Un precursor es una señal que aparece antes de una transición. El efecto crece con el tiempo de anticipación [P-0901 §3.1].

> The precursor rises before the transition; lead time follows a power law
> — P-0901 §3.1

## Métodos

| Método | Ventaja | Inconveniente |
|---|---|---|
| Intervenir el precursor | acelera la transición [P-0901 §3.2] | solo en dinámicas de juguete |

> Boosting the precursor slows the transition down
> — P-0901 §3.2

Un trabajo sobre grafos pequeños lo discute [P-0902 §Resumen]. Otro sin localizador [P-0901]. Uno ajeno [P-0999 §1].
"""


class TestCheckReview(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-review-"))
        self.f = build_fixture(self.tmp)
        self.vault = str(self.f["vault"])
        paper = next(Path(self.vault, "Papers").glob("P-0901*.md"))
        paper.write_text(paper.read_text(encoding="utf-8").replace("title:", "projects: [PROJ-900]\ntitle:", 1),
                         encoding="utf-8")
        Path(self.vault, "Papers", "P-0902 Roe 2031 Conflict.md").write_text(CONFLICT, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_list_marks_conflicts(self):
        papers = {p["id"]: p for p in check_review.papers_for(self.vault, "PROJ-900")}
        self.assertEqual(set(papers), {"P-0901", "P-0902"})
        self.assertTrue(papers["P-0902"]["conflict"])
        self.assertFalse(papers["P-0901"]["conflict"])
        self.assertTrue(papers["P-0901"]["fulltext"])

    def test_quotes_and_inline_citations(self):
        r = check_review.check(self.vault, REVIEW, "PROJ-900")
        self.assertEqual((r["quotes"], r["quotes_ok"]), (2, 1))
        self.assertEqual(r["retired"][0]["locator"], "§3.2")
        self.assertIn("⚠ Cita retirada por la comprobación (P-0901 §3.2)", r["text"])
        self.assertNotIn("slows the transition down", r["text"])
        # explanations without a citation stay: the review is allowed to explain
        self.assertIn("Un precursor es una señal", r["text"])
        self.assertEqual((r["citations"], r["citations_ok"]), (5, 2))
        reasons = {f["paper"]: f["reason"] for f in r["flagged"]}
        self.assertIn("conflicto", reasons["P-0902"])
        self.assertEqual(reasons["P-0999"], "no es un paper del proyecto")
        self.assertIn("[P-0901 ⚠ sin localizador]", r["text"])
        self.assertIn("[P-0902 §Resumen ⚠ referencia en conflicto", r["text"])
        self.assertIn("| acelera la transición [P-0901 §3.2] |", r["text"])

    def test_wrong_locator_is_flagged(self):
        r = check_review.check(self.vault, "Algo [P-0901 §9.9].", "PROJ-900")
        self.assertEqual(r["flagged"][0]["reason"], "localizador no encontrado en la nota")

    def test_quote_from_a_conflicting_paper_is_retired(self):
        text = "Claim.\n\n> An invented abstract about toy embeddings of small graphs.\n> — P-0902 §Resumen\n"
        r = check_review.check(self.vault, text, "PROJ-900")
        self.assertEqual(r["quotes_ok"], 0)
        self.assertIn("conflicto", r["retired"][0]["reason"])

    def test_uncited_papers_are_listed(self):
        r = check_review.check(self.vault, "Solo uno [P-0901 §3.1].", "PROJ-900")
        self.assertEqual((r["cited"], r["uncited"]), (["P-0901"], ["P-0902"]))

    def test_a_readable_paper_left_out_without_a_reason_is_unaccounted(self):
        r = check_review.check(self.vault, "Solo uno [P-0901 §3.1].", "PROJ-900")
        self.assertEqual(r["unaccounted"], ["P-0902"])          # it has an abstract
        text = "Solo uno [P-0901 §3.1].\n\n## Papers no tratados\n\n- P-0902: referencia en conflicto.\n"
        self.assertEqual(check_review.check(self.vault, text, "PROJ-900")["unaccounted"], [])

    def test_out_writes_a_non_citable_note(self):
        draft = self.tmp / "borrador.md"
        draft.write_text(REVIEW, encoding="utf-8")
        out = self.tmp / "Informes" / "revision.md"
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = check_review.main(["--vault", self.vault, "--project", "PROJ-900", "--review", str(draft),
                                      "--out", str(out), "--title", "Revisión de prueba"])
        self.assertEqual(code, 0)
        rep = json.loads(buf.getvalue())
        self.assertNotIn("text", rep)
        note = out.read_text(encoding="utf-8")
        for line in ("tipo: revision-literatura", "escrito_por: modelo", "citable: false",
                     "citas_literales: 1/2", "referencias_comprobadas: 2/5", "papers_citados: 2/2",
                     "# Revisión de prueba", "## Comprobación"):
            self.assertIn(line, note)


HUB = """---
id: PROJ-900
---

# Proyecto inventado
"""

HYP = """---
id: H-9001
project: PROJ-900
status: propuesta
vale_la_pena: dudoso
---

## Claim

Una afirmación inventada sobre precursores.

## Evaluación

Texto inventado.
"""

EXPLAIN = """## Resumen en una página

El precursor anticipa la transición [P-0901 §3.1]. Nuestro proyecto lo pone a prueba [H-9001 §Claim].

## Esquema conceptual

```mermaid
mindmap
  root((Precursor))
    Anticipación
    Intervención
```

## Cómo funciona el método

```mermaid
flowchart LR
  A[Medir la señal] --> B{¿Sube?}
  B -->|sí| C["Avisar (antes)"]
  D[f(x) roto] --> C
```

> The precursor rises before the transition; lead time follows a power law
> — P-0901 §3.1

## Ejemplo trabajado

Un ejemplo inventado de 4 pasos. Ver la nota [[H-9001]] y la que no existe [H-9099].

## Comprueba que lo has entendido

1. ¿Qué anticipa el precursor?
<details><summary>Respuesta</summary>

La transición [P-0901 §3.1].

</details>

2. ¿Qué pasa al intervenir?
<details><summary>Respuesta</summary>
Se acelera [P-0901 §3.2].
</details>

## Glosario

- **Precursor**: señal previa.

## Cómo encaja en el proyecto

La hipótesis [H-9001] ataca este hueco; su sección inventada [H-9001 §No existe] no está. Un paper fuera de la petición [P-0902 §Resumen].
"""


class TestCheckExplanation(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-explain-"))
        self.f = build_fixture(self.tmp)
        self.vault = str(self.f["vault"])
        paper = next(Path(self.vault, "Papers").glob("P-0901*.md"))
        paper.write_text(paper.read_text(encoding="utf-8").replace("title:", "projects: [PROJ-900]\ntitle:", 1),
                         encoding="utf-8")
        Path(self.vault, "Papers", "P-0902 Roe 2031 Conflict.md").write_text(
            CONFLICT.replace("resolution_status: mismatch\n", ""), encoding="utf-8")
        pdir = Path(self.vault, "Projects", "proyecto-inventado")
        (pdir / "Hipotesis").mkdir(parents=True)
        (pdir / "_hub.md").write_text(HUB, encoding="utf-8")
        (pdir / "Hipotesis" / "H-9001 Precursor.md").write_text(HYP, encoding="utf-8")
        (pdir / "Hipotesis" / "H-9002 Secreta.md").write_text(HYP.replace("H-9001", "H-9002")
                                                              .replace("---\n\n", "send: never\n---\n\n", 1),
                                                              encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_check(self, text=EXPLAIN, **kw):
        kw.setdefault("only", ["P-0901"])
        kw.setdefault("kind", "explicacion")
        return check_review.check(self.vault, text, "PROJ-900", **kw)

    def test_restricted_papers(self):
        r = self.run_check()
        self.assertEqual(r["papers"], 1)
        reasons = {f.get("paper") or f.get("note"): f["reason"] for f in r["flagged"]}
        self.assertEqual(reasons["P-0902"], "no está entre los papers pedidos")
        self.assertEqual(r["uncited"], [])
        r2 = check_review.check(self.vault, "> An invented abstract about toy embeddings of small graphs.\n"
                                "> — P-0902 §Resumen\n", "PROJ-900", only=["P-0901"])
        self.assertEqual(r2["retired"][0]["reason"], "no está entre los papers pedidos")
        r3 = self.run_check(only=["P-0901", "P-0999"])
        self.assertEqual(r3["requested_missing"], ["P-0999"])

    def test_project_note_citations(self):
        r = self.run_check()
        self.assertEqual((r["note_citations"], r["note_citations_ok"]), (4, 2))
        reasons = [f["reason"] for f in r["flagged"] if f.get("note")]
        self.assertIn("no es una nota de este proyecto", reasons)
        self.assertIn("encabezado no encontrado en la nota", reasons)
        self.assertEqual([(n["id"], n["status"], n["vale_la_pena"]) for n in r["notes_cited"]],
                         [("H-9001", "propuesta", "dudoso")])
        self.assertIn("[[H-9001]]", r["text"])                    # a wiki link is not a citation
        self.assertIn("[H-9099 ⚠ no es una nota de este proyecto]", r["text"])

    def test_send_never_note_is_not_citable_nor_listed(self):
        r = self.run_check("Algo [H-9002].")
        self.assertEqual(r["note_citations_ok"], 0)
        listed = [n["id"] for n in check_review.project_notes(self.vault, "PROJ-900")]
        self.assertIn("H-9001", listed)
        self.assertNotIn("H-9002", listed)

    def test_mermaid_and_details(self):
        r = self.run_check()
        self.assertEqual((r["diagrams"]["blocks"], r["diagrams"]["ok"]), (2, 1))
        self.assertIn("paréntesis", r["diagrams"]["problems"][0]["reason"])
        self.assertIn("> ⚠ Diagrama con un problema de sintaxis", r["text"])
        self.assertIn("```mermaid\nmindmap\n  root((Precursor))", r["text"])        # diagrams pass untouched
        self.assertEqual(r["details"], {"blocks": 2, "balanced": True})
        self.assertEqual(r["questions"], 2)
        # the quote right after a diagram and the citations inside <details> are checked
        self.assertEqual((r["quotes"], r["quotes_ok"]), (1, 1))
        self.assertIn("Se acelera [P-0901 §3.2].", r["text"])

    def test_missing_sections_and_comparison_rule(self):
        r = self.run_check()
        self.assertEqual(r["missing_sections"], [])               # one paper: no Comparación needed
        r2 = self.run_check(only=["P-0901", "P-0902"])
        self.assertEqual(r2["missing_sections"], ["Comparación"])
        r3 = self.run_check(only=None, mode="proyecto")
        self.assertEqual((r3["missing_sections"], r3["unaccounted"]), ([], []))

    def test_citation_inside_a_diagram_is_flagged_not_rewritten(self):
        text = "Texto.\n\n```mermaid\nflowchart TD\n  A[\"P-0901\"] --> B[P-0901 §9]\n```\n"
        r = check_review.check(self.vault, text, "PROJ-900")
        self.assertIn("B[P-0901 §9]", r["text"])
        self.assertIn("dentro de un diagrama", r["flagged"][0]["reason"])
        self.assertIn("> ⚠ El bloque anterior cita P-0901", r["text"])

    def test_quote_glued_to_prose_is_still_checked(self):
        text = "Una frase.\n> Boosting the precursor slows the transition down\n> — P-0901 §3.2\n"
        r = check_review.check(self.vault, text, "PROJ-900")
        self.assertEqual((r["quotes"], r["quotes_ok"]), (1, 0))

    def test_list_and_out(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = check_review.main(["--vault", self.vault, "--project", "PROJ-900", "--list",
                                      "--papers", "P-0901,P-0999", "--mode", "proyecto"])
        self.assertEqual(code, 0)
        listing = json.loads(buf.getvalue())
        self.assertEqual([p["id"] for p in listing["papers"]], ["P-0901"])
        self.assertEqual(listing["requested_missing"], ["P-0999"])
        self.assertEqual(listing["project_dir"], "Projects/proyecto-inventado")
        self.assertIn("Projects/proyecto-inventado/_hub.md", [n["path"] for n in listing["project_notes"]])

        draft = self.tmp / "borrador.md"
        draft.write_text(EXPLAIN, encoding="utf-8")
        out = self.tmp / "Informes" / "explicacion.md"
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = check_review.main(["--vault", self.vault, "--project", "PROJ-900", "--review", str(draft),
                                      "--kind", "explicacion", "--mode", "celda", "--papers", "P-0901",
                                      "--cell", "M1:fila|col", "--out", str(out), "--title", "Explicación de prueba"])
        self.assertEqual(code, 0)
        note = out.read_text(encoding="utf-8")
        for line in ("tipo: explicacion", "modo: celda", "papers: [P-0901]", "celda: M1:fila|col",
                     "escrito_por: modelo", "citable: false", "notas_comprobadas: 2/4", "diagramas_ok: 1/2",
                     "preguntas: 2", "# Explicación de prueba", "| H-9001 | propuesta | dudoso |",
                     "⚠ «Comprueba que lo has entendido» tiene 2 preguntas"):
            self.assertIn(line, note)

    def test_bad_papers_argument(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = check_review.main(["--vault", self.vault, "--project", "PROJ-900", "--list", "--papers", "H-1"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
