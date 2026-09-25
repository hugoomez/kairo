"""Tests for ai_disclosure.py -- synthetic temp vault only (never the real vault).

Run: python -m unittest test_ai_disclosure -v   (from this directory)
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_disclosure as ad  # noqa: E402

SLUG = "proj-sintetico"


def w(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")


def build_vault(root: Path) -> None:
    P = f"Projects/{SLUG}"
    w(root, f"{P}/_hub.md", """
        ---
        id: PROJ-900
        name: Proyecto sintético
        autonomy_defaults:
          paper_ingestion: manual
          experiments: autonomo
          hypothesis_promotion: manual
        related_projects: []
        ---
        """)
    w(root, f"{P}/Estado-del-arte.md", """
        ---
        project: PROJ-900
        last_updated: 2026-09-01
        source_papers: [P-0001, P-0002]
        ---
        # Estado del arte
        Mapa construido por `create-project` (map-reduce sobre 2 facetas).
        ## Huecos identificados
        - hueco
        ### Búsqueda ejecutada — 2026-09-01
        **Consultas (verbatim):** ...
        """)
    # H-0001: agent origin, full history, three verifications on two scopes
    w(root, f"{P}/Hipotesis/H-0001 claim agente.md", """
        ---
        id: H-0001
        project: PROJ-900
        status: apoyada
        confidence: 0.8  # kind: frequentist_heuristic
        linked_papers: [P-0001]
        linked_experiment: [E-0001, E-0002]   # adjudicating
        experiment_validity: valid
        generated_by:
          origin: agent
          model: claude-sonnet-5
          skill_version: kairo/hypothesis-cycle@1.2.0
          pipeline_config: Projects/proj-sintetico/_hub.md
        linea_publicacion: true
        paper_thread: hilo-uno
        history:
          - date: 2026-09-02
            status: propuesta
            by: claude-sonnet-5 (create-project -> hypothesis-cycle)
            experiments: []
            combination: n/a
            evidence: "passed dedup/falsifiability # not a comment"
          - date: 2026-09-03
            status: preregistrada
            by: claude-sonnet-5 (preregister-experiment -> update-confidence)
            experiments: [E-0001]
            combination: n/a
            evidence: prereg frozen
          - date: 2026-09-10
            status: apoyada
            by: claude-sonnet-5 (run-experiment -> update-confidence)
            experiments: [E-0001, E-0002]
            combination: combine_effects.py@2.0.0 (random-effects DerSimonian-Laird)
            evidence: 'pooled 0.12 [0.05, 0.19]'
        verifications:
          - verifier: kairo/fresh-verifier@0.1.0
            model: claude-opus-5-5
            date: 2026-09-11
            verdict: no_errors_found
            scope: note
          - verifier: kairo/fresh-verifier@0.1.0
            model: claude-opus-5-5
            date: 2026-09-12
            verdict: errors_found
            scope: "section:Justificación (evidencia citada)"
          - verifier: kairo/fresh-verifier@0.1.1
            model: claude-opus-5-5
            date: 2026-09-14
            verdict: no_errors_found
            scope: section:Justificación (evidencia citada)
        ---

        ## Claim

        Claim del agente.

        ## Justificación (evidencia citada)

        - P-0001 §3
        """)
    # H-0002: human origin, verifications: [], manual override recorded
    w(root, f"{P}/Hipotesis/H-0002 claim humano.md", """
        ---
        id: H-0002
        project: PROJ-900
        status: propuesta
        linked_papers: []
        linked_experiment:
        generated_by:
          origin: human
        paper_thread: hilo-uno
        history:
          - date: 2026-09-04
            status: propuesta
            by: Ana Pérez
            experiments: []
            combination: n/a
            evidence: human-submitted
        verifications: []
        ---

        ## Claim

        Claim humano.

        ## Revisión del ciclo

        Clear fail en Check 3. Override manual: el investigador decidió mantenerla por un motivo declarado.
        """)
    # H-0003: no generated_by, verifications absent
    w(root, f"{P}/Hipotesis/H-0003 sin origen.md", """
        ---
        id: H-0003
        project: PROJ-900
        status: propuesta
        paper_thread: hilo-uno
        ---

        ## Claim

        Sin origen.
        """)
    # H-0004: send: never -- nothing of it may be output
    w(root, f"{P}/Hipotesis/H-0004 privada.md", """
        ---
        id: H-0004
        send: "Never"   # private
        project: PROJ-900
        status: propuesta
        paper_thread: hilo-uno
        linked_experiment: [E-0099]
        generated_by:
          origin: agent
          model: secret-model-q
        history:
          - date: 2026-09-05
            status: propuesta
            by: SecretPerson
            evidence: SECRET-HISTORY-XYZ
        verifications:
          - verifier: kairo/fresh-verifier@0.1.0
            model: secret-verifier-model
            date: 2026-09-06
            verdict: errors_found
            scope: note
        ---

        ## Claim

        SECRET-CLAIM-XYZ
        """)
    # H-0005: other thread
    w(root, f"{P}/Hipotesis/H-0005 otro hilo.md", """
        ---
        id: H-0005
        status: propuesta
        paper_thread: otro-hilo
        generated_by: {origin: agent, model: other-model}
        ---
        """)
    # H-0006: agent origin with no model
    w(root, f"{P}/Hipotesis/H-0006 sin modelo.md", """
        ---
        id: H-0006
        status: propuesta
        paper_thread: hilo-uno
        generated_by:
          origin: agent
          model: <model id, e.g. claude-sonnet-5>
        ---
        """)
    # E-0001: role/rung present, tool, amendments, errors_found (latest) + invalid verdict
    w(root, f"{P}/Experimentos/E-0001.md", """
        ---
        id: E-0001
        hypothesis: H-0001
        project: PROJ-900
        tier: completo
        analysis_plan: frequentist
        role: confirmatory
        rung: 2
        status: completed
        frozen_at: 2026-09-03T10:00:00Z
        frozen_commit: abcdef1234567890
        environment:
          seed: 1
          hardware: "1x T4"
          tools: [{path: Tools/P-0001/metodo-x, validation_hash: 0123456789abcdef0123456789abcdef}]
        method_provenance: validated_tool
        experiment_validity: valid
        cost_actual: "2 GPU-h"
        result:
          effect: "0.1 [0.02, 0.18]"
          p_value: 0.01
          verdict: apoyada
        verifications:
          - verifier: kairo/fresh-verifier@0.1.0
            model: claude-opus-5-5
            date: 2026-09-12
            verdict: errors_found
            scope: section:Plan de análisis
          - verifier: kairo/fresh-verifier@0.1.0
            model: claude-opus-5-5
            date: 2026-09-12
            verdict: correct
            scope: note
        ---

        ## Plan de análisis

        Test.

        ## Resultado

        Comando: `python scripts/analysis/two_proportion_test.py 30 100 18 100 --alpha 0.05`
        Salida: p = 0.01, verdict apoyada.

        ## Enmiendas

        ### 2026-09-04 — Activación ReLU
        **Motivo:** hueco. Aprobado por el investigador antes de ejecutar código.

        ### 2026-09-04 — Código de entrenamiento (sha del commit)
        **Motivo:** registrar el commit.
        """)
    # E-0002: role/rung absent, reimplemented_from_text, errors_found -> cannot_assess
    w(root, f"{P}/Experimentos/E-0002.md", """
        ---
        id: E-0002
        hypothesis: H-0001
        tier: completo
        analysis_plan: bayesian
        status: completed
        frozen_at: 2026-09-05T10:00:00Z
        frozen_commit: 1111111
        environment:
          seed: 2
          tools: []
        method_provenance: reimplemented_from_text
        experiment_validity: valid
        result:
          bayes_factor: 12
          verdict: apoyada
        verifications:
          - verifier: kairo/fresh-verifier@0.1.0
            model: claude-opus-5-5
            date: 2026-09-12
            verdict: errors_found
            scope: section:Diseño
          - verifier: kairo/fresh-verifier@0.1.0
            model: claude-opus-5-5
            date: 2026-09-13
            verdict: cannot_assess
            scope: section:Diseño
        ---

        ## Diseño

        Diseño.
        """)
    w(root, "Tools/P-0001/metodo-x/TOOL.md", """
        ---
        paper: P-0001
        method: metodo-x
        status: validated
        repo: https://github.com/example/repo
        commit: 0123456789012345678901234567890123456789
        validation:
          attempts: 1
          validation_hash: 0123456789abcdef0123456789abcdef
        ---
        """)
    w(root, "Papers/P-0001 Autor 2020 Titulo.md", """
        ---
        id: P-0001
        title: "Un título: con dos puntos"
        authors: ["Autor, Uno", "Otro, Dos"]
        year: 2020
        resolved: true
        openalex_id: W123
        resolution_checked: 2026-09-01
        ---
        """)
    w(root, f"{P}/Manuscritos/manuscript-hilo-uno.md", """
        ---
        project: PROJ-900
        paper_thread: hilo-uno
        status: draft
        generated: 2026-09-15
        generated_by:
          origin: agent
          model: claude-opus-5-5
          skill_version: kairo/assemble-manuscript@1.0.0
        hypotheses_included: [H-0001]
        verifications:
          - verifier: kairo/fresh-verifier@0.1.0
            model: claude-opus-5-5
            date: 2026-09-16
            verdict: no_errors_found
            scope: section:Discusión y limitaciones
        ---

        ## Discusión y limitaciones

        Texto (Autor & Otro, 2020). Trazabilidad: P-0001.
        """)


def run(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        code = ad.main(argv)
    return code, buf.getvalue()


def statement_lines(md: str) -> list[str]:
    return [l for l in md.split("\n") if l.startswith("- [")]


class YamlSubsetTests(unittest.TestCase):
    def test_shapes(self):
        y = textwrap.dedent("""
            a: 1   # comment
            q: "has # hash and: colon"
            sq: 'it''s'
            inline: [E-0001, "E-0002", 'x, y']
            flow: {path: Tools/P-1/m, validation_hash: abc}
            empty:
            nested:
              k: v
              deeper:
                z: true
            items:
              - date: 2026-09-01
                by: Ana
                experiments: []
              - date: 2026-09-02
                by: claude (x -> y)
            scalars:
            - one
            - two
            block: |
              line1
              # not a comment
            # full-line comment
            last: ~
            """)
        d = ad.parse_yaml(y)
        self.assertEqual(d["a"], 1)
        self.assertEqual(d["q"], "has # hash and: colon")
        self.assertEqual(d["sq"], "it's")
        self.assertEqual(d["inline"], ["E-0001", "E-0002", "x, y"])
        self.assertEqual(d["flow"], {"path": "Tools/P-1/m", "validation_hash": "abc"})
        self.assertIsNone(d["empty"])
        self.assertEqual(d["nested"], {"k": "v", "deeper": {"z": True}})
        self.assertEqual(len(d["items"]), 2)
        self.assertEqual(d["items"][1]["by"], "claude (x -> y)")
        self.assertEqual(d["items"][0]["experiments"], [])
        self.assertEqual(d["scalars"], ["one", "two"])
        self.assertEqual(d["block"], "line1\n# not a comment")
        self.assertIsNone(d["last"])

    def test_send_never_detection(self):
        for fm in ("send: never", 'SEND: "Never"   # x', "send: 'NEVER'", "Send:never"):
            self.assertTrue(ad.raw_send_never(fm), fm)
        for fm in ("send: sometimes", "nested:\n  send: never", "resend: never"):
            self.assertFalse(ad.raw_send_never(fm), fm)

    def test_classify_by(self):
        self.assertEqual(ad.classify_by("Ana Pérez", set(), ["Ana Pérez"]), "human")
        self.assertEqual(ad.classify_by("claude-sonnet-5 (a -> b)", set()), "agent")
        self.assertEqual(ad.classify_by("", set()), "unknown")
        self.assertEqual(ad.classify_by(None, set()), "unknown")


class DisclosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="kairo-a2-"))
        cls.vault = cls.tmp / "vault"
        build_vault(cls.vault)
        cls.base = ["--vault", str(cls.vault), "--project", SLUG, "--thread", "hilo-uno",
                    "--researcher", "Ana Pérez",
                    "--manuscript", f"Projects/{SLUG}/Manuscritos/manuscript-hilo-uno.md"]
        code, cls.md = run(cls.base)
        assert code == 0, cls.md
        code, out = run(cls.base + ["--format", "json"])
        assert code == 0
        cls.js = json.loads(out)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def flags(self, sev=None):
        return [f["message"] for f in self.js["flags"] if sev is None or f["severity"] == sev]

    def stage(self, key):
        return [s["es"] for s in self.js["statements"] if s["stage"] == key]

    # --- collection
    def test_collects_thread(self):
        self.assertEqual(self.js["hypotheses"], ["H-0001", "H-0002", "H-0003", "H-0004", "H-0006"])
        self.assertEqual(self.js["experiments"], ["E-0001", "E-0002"])
        self.assertEqual(self.js["tools"], ["Tools/P-0001/metodo-x"])
        self.assertEqual(self.js["papers"], ["P-0001"])
        self.assertIn("## Declaración de uso de IA", self.md)
        self.assertIn("### English version (for submission)", self.md)

    def test_explicit_hypotheses(self):
        code, out = run(self.base[:-2] + ["--hypotheses", "H-0001", "--format", "json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["hypotheses"], ["H-0001"])

    # --- verifications
    def test_every_verification_entry_reported(self):
        recs = self.js["verifications"]
        # H-0001: 3, E-0001: 2, E-0002: 2, manuscript: 1 (H-0004 is send: never)
        self.assertEqual(len(recs), 8)
        ver_sts = [s for s in self.js["statements"]
                   if s["stage"] == "verificacion" and any("verifications[" in x for x in s["sources"])]
        self.assertEqual(len(ver_sts), 8)
        self.assertIn("kairo/fresh-verifier@0.1.0 (modelo claude-opus-5-5), 2026-09-11: no encontró errores en "
                      "la nota completa H-0001.", self.md)
        self.assertIn("encontró errores en la sección «Justificación (evidencia citada)» de H-0001", self.md)
        self.assertIn("no pudo evaluar la sección «Diseño» de E-0002", self.md)
        self.assertIn("no encontró errores en la sección «Discusión y limitaciones» de "
                      f"Projects/{SLUG}/Manuscritos/manuscript-hilo-uno.md", self.md)
        self.assertIn("found no errors in the whole note H-0001", self.md)

    def test_no_errors_found_never_rendered_as_correct(self):
        lines = "\n".join(statement_lines(self.md))
        # the out-of-contract verdict value is quoted verbatim; everything else must not say "correct"
        self.assertIn("verdict «correct»", lines)
        scrubbed = lines.replace("verdict «correct»", "").replace("verdict “correct”", "")
        for rx in (r"\bcorrect[oa]s?\b", r"\bcorrect\b", r"\bverificad[oa]s?\b", r"\bverified\b",
                   r"\bcorrectly\b", r"\bvalidad[oa] como correct"):
            self.assertIsNone(re.search(rx, scrubbed, re.IGNORECASE), rx)
        # ... and that value is flagged, not interpreted
        self.assertTrue(any("fuera del contrato" in m for m in self.flags("importante")))

    def test_absent_vs_empty(self):
        nv = {x["note"]: x["field"] for x in self.js["never_verified"]}
        self.assertEqual(nv["H-0002"], "[]")
        self.assertEqual(nv["H-0003"], "absent")
        self.assertNotIn("H-0001", nv)

    def test_errors_found_flags(self):
        crit = self.flags("crítico")
        self.assertEqual(len(crit), 1, crit)
        self.assertIn("E-0001", crit[0])
        self.assertIn("«Plan de análisis»", crit[0])
        # H-0001 section errors were followed by a later no_errors_found -> no crítico
        self.assertFalse(any("H-0001" in m for m in crit))
        self.assertTrue(any("E-0002" in m and "no pudo evaluarlo" in m for m in self.flags("importante")))

    # --- stages / no invention
    def test_unrecorded_stages_say_so(self):
        self.assertTrue(any("H-0003" in s and "no consta" in s for s in self.stage("hipotesis")))
        self.assertTrue(all("no consta" in s for s in self.stage("codigo") if "quién escribió" in s))
        self.assertTrue(any("E-0001" in s and "quién escribió el código" in s for s in self.stage("codigo")))
        self.assertTrue(any("modelo de IA que ejecutó la búsqueda" in s for s in self.stage("literatura")))

    def test_human_evidence_only_from_records(self):
        human = self.stage("humano")
        joined = "\n".join(human)
        self.assertIn("H-0002: propuesta por una persona", joined)
        self.assertIn("«Ana Pérez»", joined)
        self.assertIn("Aprobado por el investigador", joined)
        self.assertIn("Override manual", joined)
        self.assertNotIn("H-0001", joined)   # agent-only records
        self.assertNotIn("H-0003", joined)   # absence is not evidence
        self.assertTrue(any(s.startswith("[PENDIENTE") for s in human))
        # autonomy config is labelled as configuration, not as an act
        self.assertTrue(any("Es una configuración, no un registro" in s for s in self.stage("literatura")))

    def test_no_human_evidence_says_so(self):
        code, out = run(self.base[:-2] + ["--hypotheses", "H-0003", "--format", "json"])
        self.assertEqual(code, 0)
        hum = [s["es"] for s in json.loads(out)["statements"] if s["stage"] == "humano"]
        self.assertTrue(any("ninguna acción atribuida al investigador" in s for s in hum))

    def test_execution_mechanical_and_roles(self):
        ex = "\n".join(self.stage("ejecucion"))
        self.assertIn("scripts/analysis/two_proportion_test.py", ex)
        self.assertIn("scripts/analysis/bayes_factor_proportions.py", ex)
        self.assertIn("la IA no juzgó la significación", ex)
        self.assertIn("combine_effects.py@2.0.0", ex)
        pre = "\n".join(self.stage("preregistro"))
        self.assertIn("E-0001: preregistro congelado el 2026-09-03T10:00:00Z", pre)
        self.assertIn("rol confirmatory, peldaño 2", pre)
        self.assertIn("rol confirmatory (campo ausente; se lee como confirmatorio), peldaño desconocido", pre)
        self.assertIn("«2026-09-04 — Activación ReLU»", pre)

    def test_tools_and_provenance(self):
        code_st = "\n".join(self.stage("codigo"))
        self.assertIn("Tools/P-0001/metodo-x", code_st)
        self.assertIn("https://github.com/example/repo", code_st)
        self.assertTrue(any("reimplementado desde el texto" in m for m in self.flags("importante")))

    def test_models_verbatim(self):
        models = {m["model"] for m in self.js["models"]}
        self.assertEqual(models, {"claude-sonnet-5", "claude-opus-5-5"})
        comps = {c["id"] for c in self.js["versioned_components"]}
        self.assertTrue({"kairo/hypothesis-cycle@1.2.0", "kairo/fresh-verifier@0.1.0",
                         "kairo/fresh-verifier@0.1.1", "kairo/assemble-manuscript@1.0.0"} <= comps)
        self.assertIn("El borrador del manuscrito lo redactó IA: claude-opus-5-5 con "
                      "kairo/assemble-manuscript@1.0.0", self.md)

    def test_gap_flags(self):
        imp = self.flags("importante")
        self.assertTrue(any("H-0003 no tiene generated_by" in m for m in imp))
        self.assertTrue(any("H-0006" in m and "sin modelo" in m for m in imp))
        self.assertTrue(any("responsabilidad humana" in m for m in imp))

    def test_send_never(self):
        for out in (self.md, json.dumps(self.js, ensure_ascii=False)):
            for secret in ("SECRET-CLAIM-XYZ", "SECRET-HISTORY-XYZ", "secret-model-q", "SecretPerson",
                           "secret-verifier-model", "E-0099"):
                self.assertNotIn(secret, out)
        expected = ("H-0004 marcado send: never; su contribución no puede declararse automáticamente, "
                    "el investigador debe completarla a mano")
        self.assertIn(expected, self.flags("importante"))
        self.assertIn(f"| importante | {expected} |", self.md)
        self.assertFalse(any(s for s in self.js["statements"] if "H-0004" in s["es"]))

    def test_traceability_table(self):
        self.assertIn("### Trazabilidad (interno — eliminar antes de enviar)", self.md)
        n = len(self.js["statements"])
        self.assertEqual([s["n"] for s in self.js["statements"]], list(range(1, n + 1)))
        self.assertIn("| 1 | ", self.md)
        self.assertIn("H-0001 generated_by", self.md)
        self.assertIn("H-0001 history 2026-09-10", self.md)

    def test_lang_es_only(self):
        code, out = run(self.base + ["--lang", "es"])
        self.assertEqual(code, 0)
        self.assertNotIn("English version", out)

    # --- exit codes
    def test_exit_codes(self):
        v = str(self.vault)
        self.assertEqual(run(["--vault", v, "--project", "nope", "--thread", "hilo-uno"])[0], 2)
        self.assertEqual(run(["--vault", v, "--project", "PROJ-900", "--thread", "no-such"])[0], 2)
        self.assertEqual(run(["--vault", v, "--project", "PROJ-900", "--thread", "hilo-uno",
                              "--hypotheses", "H-9999"])[0], 2)
        self.assertEqual(run(["--vault", v, "--project", SLUG, "--thread", "hilo-uno",
                              "--manuscript", "missing.md"])[0], 2)
        self.assertEqual(run(["--vault", str(self.tmp / "none"), "--project", SLUG, "--thread", "x"])[0], 2)
        self.assertEqual(run(["--vault", v, "--project", "PROJ-900", "--thread", "hilo-uno"])[0], 0)
        with mock.patch.object(ad, "analyze", side_effect=RuntimeError("boom")):
            self.assertEqual(run(self.base)[0], 1)


class HistoryModelTests(unittest.TestCase):
    def test_models_from_history_by_are_listed_with_provenance(self):
        tmp = Path(tempfile.mkdtemp(prefix="kairo-a2h-"))
        try:
            w(tmp, "Projects/p/Hipotesis/H-0001 x.md", """
                ---
                id: H-0001
                paper_thread: t
                generated_by: {origin: human}
                history:
                  - date: 2026-09-01
                    status: propuesta
                    by: Ana Pérez
                  - date: 2026-09-02
                    status: preregistrada
                    by: claude-sonnet-5 (preregister-experiment -> update-confidence)
                    experiments: [E-0001]
                ---
                """)
            argv = ["--vault", str(tmp), "--project", "p", "--thread", "t"]
            code, md = run(argv)
            self.assertEqual(code, 0)
            self.assertIn("Modelos que constan en los registros: claude-sonnet-5 (historial: transición a "
                          "preregistrada; fuente: H-0001 history 2026-09-02 by)", md)
            self.assertNotIn("ninguno", md.split("\n")[2])
            self.assertIn("Models named in the records: claude-sonnet-5", md)
            self.assertIn("Etapas sin modelo registrado: 1. Búsqueda de literatura", md)
            js = json.loads(run(argv + ["--format", "json"])[1])
            self.assertEqual([m["model"] for m in js["models"]], ["claude-sonnet-5"])
            self.assertEqual(js["models"][0]["stages"], ["preregistro"])
            self.assertIn("hipotesis", js["stages_without_model"])   # human-origin, no model
            self.assertNotIn("preregistro", js["stages_without_model"])
            self.assertNotIn("Ana", json.dumps(js["models"], ensure_ascii=False))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_models_in_text(self):
        self.assertEqual(ad.models_in_text("claude-opus-5-5 (x -> y); gpt-4o."), ["claude-opus-5-5", "gpt-4o"])
        self.assertEqual(ad.models_in_text("Ana Pérez"), [])


class IntegrationReviewTests(unittest.TestCase):
    """Cross-block cases found in the v3 integration review."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-a2i-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_file_order_governs_not_date(self):
        # contract §3a: the LAST entry in file order governs, as in verifications.py
        w(self.tmp, "Projects/p/Hipotesis/H-0001 x.md", """
            ---
            id: H-0001
            paper_thread: t
            verifications:
              - verifier: kairo/fresh-verifier@1.0.0
                model: claude-opus-5-5
                date: 2026-09-20
                verdict: errors_found
                scope: note
              - verifier: kairo/fresh-verifier@1.0.0
                model: claude-opus-5-5
                date: 2026-09-10
                verdict: no_errors_found
                scope: note
            ---
            """)
        js = json.loads(run(["--vault", str(self.tmp), "--project", "p", "--thread", "t",
                             "--format", "json"])[1])
        self.assertFalse([f for f in js["flags"] if f["severity"] == "crítico"])

    def code_author(self, block):
        w(self.tmp, "Projects/p/Hipotesis/H-0001 x.md", """
            ---
            id: H-0001
            paper_thread: t
            linked_experiment: [E-0001]
            ---
            """)
        w(self.tmp, "Projects/p/Experimentos/E-0001.md", f"""
            ---
            id: E-0001
            hypothesis: H-0001
            {block}
            ---
            """)
        return json.loads(run(["--vault", str(self.tmp), "--project", "p", "--thread", "t",
                               "--format", "json"])[1])

    def codigo(self, js):
        return [st for st in js["statements"] if st["stage"] == "codigo"]

    def test_code_generated_by_human_without_model_is_recorded(self):
        js = self.code_author("code_generated_by: {origin: human}")
        self.assertEqual([st["kind"] for st in self.codigo(js)], ["human"])
        self.assertFalse([f for f in js["flags"] if "autoría del código" in f["message"]])

    def test_code_generated_by_agent_without_model_is_ai_with_flag(self):
        js = self.code_author("code_generated_by: {origin: agent}")
        self.assertEqual([st["kind"] for st in self.codigo(js)], ["ai"])
        self.assertTrue([f for f in js["flags"] if "sin modelo registrado" in f["message"]])

    def test_code_generated_by_absent_is_still_unknown(self):
        js = self.code_author("status: preregistered")
        self.assertEqual([st["kind"] for st in self.codigo(js)], ["none"])


class ZeroVerificationTests(unittest.TestCase):
    def test_zero_records_flag(self):
        tmp = Path(tempfile.mkdtemp(prefix="kairo-a2z-"))
        try:
            w(tmp, "Projects/p/Hipotesis/H-0001 x.md", """
                ---
                id: H-0001
                paper_thread: t
                generated_by: {origin: human}
                ---
                """)
            code, out = run(["--vault", str(tmp), "--project", "p", "--thread", "t", "--format", "json"])
            self.assertEqual(code, 0)
            js = json.loads(out)
            self.assertEqual(js["verifications"], [])
            self.assertTrue(any(f["severity"] == "importante" and "Ninguna nota tiene registros de verifications"
                                in f["message"] for f in js["flags"]))
            lit = [s["es"] for s in js["statements"] if s["stage"] == "literatura"]
            self.assertTrue(any("No consta en los registros de Kairo" in s for s in lit))
            self.assertTrue(any("No consta en los registros de Kairo ningún preregistro" in s["es"]
                                for s in js["statements"]))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class ClassifyByTests(unittest.TestCase):
    """I6: three-way agent / human / unknown; human only from a declared researcher."""

    AGENTS = ("spawn-hypothesis", "second-critic", "evolve-program", "paper-to-tool", "literature-search",
              "serendipity-scan", "adr-check", "o3", "kimi-k2", "fresh-verifier", "facet-searcher",
              "kairo/run-experiment@1.0.0", "x -> y", "bot@host")

    def test_kairo_names_and_models_are_agents(self):
        for by in self.AGENTS:
            self.assertEqual(ad.classify_by(by, set()), "agent", by)
            self.assertEqual(ad.classify_by(by, set(), ["Ana Pérez"]), "agent", by)

    def test_unlisted_name_is_unknown_not_human(self):
        for by in ("Ana Pérez", "Bob", "someone", "equipo"):
            self.assertEqual(ad.classify_by(by, set()), "unknown", by)
        self.assertEqual(ad.classify_by("Bob", set(), ["Ana Pérez"]), "unknown")

    def test_declared_researcher_is_human(self):
        self.assertEqual(ad.classify_by("ana  pérez", set(), ["Ana Pérez"]), "human")
        self.assertEqual(ad.classify_by("Ana Pérez (manual)", set(), ["Ana Pérez"]), "human")
        self.assertEqual(ad.classify_by("Anastasia", set(), ["Ana"]), "unknown")

    def test_component_names_dynamic_plus_static(self):
        names = ad.kairo_component_names()
        for n in ("paper-to-tool", "adr-check", "second-critic", "facet-searcher", "facet-summarizer",
                  "evolve-program", "fresh-verifier", "assemble-manuscript"):
            self.assertIn(n, names)
        tmp = Path(tempfile.mkdtemp(prefix="kairo-a2n-"))
        try:
            w(tmp, "skills/brand-new-skill/SKILL.md", "x\n")
            w(tmp, "agents/brand-new-agent.md", "x\n")
            (tmp / "skills" / "not-a-skill").mkdir()
            names = ad.kairo_component_names(tmp)
            self.assertIn("brand-new-skill", names)
            self.assertIn("brand-new-agent", names)
            self.assertNotIn("not-a-skill", names)
            self.assertIn("evolve-program", names)   # static fallback still present
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_unknown_by_rendered_as_unknown_never_human(self):
        tmp = Path(tempfile.mkdtemp(prefix="kairo-a2c-"))
        try:
            w(tmp, "Projects/p/Hipotesis/H-0001 x.md", """
                ---
                id: H-0001
                paper_thread: t
                generated_by: {origin: agent, model: claude-sonnet-5}
                history:
                  - date: 2026-09-01
                    status: propuesta
                    by: spawn-hypothesis
                  - date: 2026-09-02
                    status: preregistrada
                    by: Bob Desconocido
                    experiments: [E-0001]
                  - date: 2026-09-03
                    status: en_ejecucion
                    by: kimi-k2
                ---
                """)
            argv = ["--vault", str(tmp), "--project", "p", "--thread", "t", "--format", "json"]
            js = json.loads(run(argv)[1])
            hum = "\n".join(s["es"] for s in js["statements"] if s["stage"] == "humano")
            for name in ("Bob Desconocido", "spawn-hypothesis", "kimi-k2"):
                self.assertNotIn(name, hum)
            self.assertIn("ninguna acción atribuida al investigador", hum)
            bob = [s["es"] for s in js["statements"] if "Bob Desconocido" in s["es"]]
            self.assertTrue(bob and all("no consta si fue una persona o un agente" in s for s in bob), bob)
            self.assertTrue(any("Bob Desconocido" in f["message"] and "--researcher" in f["message"]
                                for f in js["flags"]))
            # declared researcher -> human evidence
            js = json.loads(run(argv + ["--researcher", "Bob Desconocido"])[1])
            hum = "\n".join(s["es"] for s in js["statements"] if s["stage"] == "humano")
            self.assertIn("«Bob Desconocido»", hum)
            self.assertNotIn("spawn-hypothesis", hum)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class ApprovalNegationTests(unittest.TestCase):
    """M1: negated / modal / pending approval sentences are not evidence."""

    def test_negated_or_modal_excluded(self):
        for txt in ("Debe ser aprobado por el investigador.",
                    "No consta que el investigador aprobó el cambio.",
                    "Pendiente: aprobado por el investigador.",
                    "Falta que el investigador confirmó.",
                    "El investigador nunca aprobó esto.",
                    "Debería ser decidido por el investigador.",
                    "Sin aprobar por el investigador.",
                    "To be approved by the researcher.",
                    "This must be approved by the researcher.",
                    "Should be approved by the researcher.",
                    "Pending: approved by the researcher.",
                    "Not approved by the researcher."):
            self.assertEqual(ad._approval_sentences(txt), [], txt)

    def test_positive_kept(self):
        self.assertEqual(ad._approval_sentences("Aprobado por el investigador antes de ejecutar código."),
                         ["Aprobado por el investigador antes de ejecutar código."])
        self.assertEqual(len(ad._approval_sentences("Motivo X. The researcher approved it.")), 1)


class OverclaimTests(unittest.TestCase):
    """M2: protocol statements / 'no consta' instead of unrecorded facts."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="kairo-a2o-"))
        w(cls.tmp, "Projects/p/Hipotesis/H-0001 x.md", """
            ---
            id: H-0001
            paper_thread: t
            generated_by: {origin: agent, model: claude-sonnet-5}
            linked_experiment: [E-0001, E-0002]
            ---
            """)
        w(cls.tmp, "Projects/p/Experimentos/E-0001.md", """
            ---
            id: E-0001
            hypothesis: H-0001
            analysis_plan: frequentist
            status: completed
            frozen_at: 2026-09-03T10:00:00Z
            result: {verdict: apoyada}
            ---

            ## Resultado

            Veredicto apoyada (analizado a mano).
            """)
        w(cls.tmp, "Projects/p/Experimentos/E-0002.md", """
            ---
            id: E-0002
            hypothesis: H-0001
            analysis_plan: bayesian
            status: completed
            ---

            ## Resultado

            `python scripts/analysis/bayes_factor_proportions.py 30 100 18 100` -> BF 12
            """)
        w(cls.tmp, "Projects/p/Manuscritos/m.md", """
            ---
            status: draft
            generated: 2026-09-15
            ---
            Texto.
            """)
        cls.argv = ["--vault", str(cls.tmp), "--project", "p", "--thread", "t", "--format", "json"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def js(self, extra=()):
        code, out = run(self.argv + list(extra))
        self.assertEqual(code, 0)
        return json.loads(out)

    def st(self, js, stage):
        return [s["es"] + "\n" + s["en"] for s in js["statements"] if s["stage"] == stage]

    def test_prereg_freeze_is_protocol_not_fact(self):
        pre = "\n".join(self.st(self.js(), "preregistro"))
        self.assertIn("E-0001: preregistro congelado el 2026-09-03T10:00:00Z", pre)
        self.assertIn("según el protocolo de Kairo", pre)
        self.assertNotIn(", antes de ejecutar código;", pre)
        self.assertNotIn("before any code ran;", pre)

    def test_mechanical_verdict_only_when_resultado_records_script(self):
        js = self.js()
        ex = self.st(js, "ejecucion")
        e1 = "\n".join(s for s in ex if s.startswith("E-0001"))
        e2 = "\n".join(s for s in ex if s.startswith("E-0002"))
        self.assertNotIn("la IA no juzgó", e1)
        self.assertNotIn("the AI did not judge", e1)
        self.assertIn("no consta", e1)
        self.assertIn("la IA no juzgó la significación", e2)
        self.assertIn("bayes_factor_proportions.py", e2)
        kinds = {s["es"][:6]: s["kind"] for s in js["statements"] if s["stage"] == "ejecucion"}
        self.assertEqual(kinds["E-0001"], "record")
        self.assertEqual(kinds["E-0002"], "mechanical")

    def test_drafting_not_attributed_without_generated_by(self):
        for extra in ((), ("--manuscript", "Projects/p/Manuscritos/m.md")):
            js = self.js(extra)
            red = self.st(js, "redaccion")
            joined = "\n".join(red)
            self.assertNotIn("lo generó la skill assemble-manuscript", joined)
            self.assertNotIn("drafts manuscripts with AI", joined)
            self.assertNotIn("was generated by Kairo's assemble-manuscript", joined)
            self.assertTrue(any("no consta" in s.lower() for s in red), red)
            self.assertNotIn("ai", [s["kind"] for s in js["statements"] if s["stage"] == "redaccion"])


class SendNeverLeakTests(unittest.TestCase):
    """M3: send: never notes output only their id (no path, no title)."""

    def test_no_path_or_title_for_send_never(self):
        tmp = Path(tempfile.mkdtemp(prefix="kairo-a2s-"))
        try:
            w(tmp, "Projects/p/Hipotesis/H-0001 x.md", """
                ---
                id: H-0001
                paper_thread: t
                generated_by: {origin: human}
                linked_papers: [P-0007]
                ---
                """)
            w(tmp, "Papers/P-0007 Titulo Secreto Muy Revelador.md", """
                ---
                id: P-0007
                send: never
                title: Titulo Secreto Muy Revelador
                ---
                """)
            w(tmp, "Projects/p/Hipotesis/Hipotesis Confidencial.md", """
                ---
                send: never
                paper_thread: t
                ---
                """)
            argv = ["--vault", str(tmp), "--project", "p", "--thread", "t"]
            code, md = run(argv)
            self.assertEqual(code, 0)
            code, out = run(argv + ["--format", "json"])
            js = json.loads(out)
            for text in (md, out):
                for secret in ("Titulo Secreto", "Revelador", "Confidencial"):
                    self.assertNotIn(secret, text)
            sn = [n for n in js["notes"] if n["send_never"]]
            self.assertEqual(len(sn), 2)
            self.assertTrue(any(n["id"] == "P-0007" for n in sn))
            for n in sn:
                self.assertNotIn("path", n)
                self.assertNotIn("parse_error", n)
            self.assertTrue(all("path" in n for n in js["notes"] if not n["send_never"]))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
