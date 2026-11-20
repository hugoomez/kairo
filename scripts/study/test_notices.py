"""Tests for notices.py. Run: python -m pytest scripts/study/test_notices.py

An invented vault, invented repositories (`org-inventada/…`) and invented findings in a
temp dir; secrets come from fake_material (assembled at run time). No network. The
agent markers are passed explicitly, so the tests behave the same inside and outside a
Claude Code session."""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "security"))
import data_guard  # noqa: E402
import fake_material as fm  # noqa: E402
import notices as nt  # noqa: E402

TODAY = date(2031, 3, 1)
CLEAN_ENV: dict[str, str] = {}

FINDINGS = [
    {"repo": "org-inventada/uno", "files": ["config/a.yaml"], "kind": "clave_age",
     "private_vulnerability_reporting": True},
    {"repo": "org-inventada/uno", "files": "config/b.yaml", "kind": "clave_age"},
    {"repo": "org-inventada/dos", "files": ["secrets/c.env"], "kind": "dotenvx_privada",
     "security_email": "seguridad@dos.example.org"},
    {"repo": "org-inventada/tres", "files": ["k8s/d.yaml"], "kind": "sealed_key"},
    {"repo": "org-inventada/cuatro", "files": ["x.yaml"], "kind": "otra",
     "security_policy_url": "https://example.org/politica-inventada"},
]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-notices-")).resolve()
        self.vault = self.tmp / "vault"
        self.pdir = self.vault / "Projects" / "demo"
        (self.pdir / "Datos").mkdir(parents=True)
        (self.vault / ".git").mkdir()
        (self.pdir / "_hub.md").write_text("---\nid: PROJ-901\nname: Demo inventado\ntemplate: medicion\n---\n",
                                           encoding="utf-8")
        self.data = self.tmp / "datos"
        self.data.mkdir()
        self.findings_file = self.data / "hallazgos.jsonl"
        self.write_findings(FINDINGS)
        self.out = self.tmp / "borradores"
        self.registry([{"name": "hallazgos", "path": str(self.findings_file), "kind": "file", "sensitive": True}])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def registry(self, entries):
        (self.pdir / "Datos" / "registro.json").write_text(json.dumps(entries), encoding="utf-8")

    def write_findings(self, rows, extra=""):
        self.findings_file.write_text("\n".join(json.dumps(r) for r in rows) + "\n" + extra, encoding="utf-8")

    def run_cli(self, *argv, environ=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = nt.main(list(argv), environ=CLEAN_ENV if environ is None else environ, today=TODAY)
        text = out.getvalue()
        try:
            data = json.loads(text) if text.strip() else None
        except ValueError:
            data = None
        return code, data, text, err.getvalue()

    def new_template(self, name="aviso", kind="afectado"):
        return self.run_cli("template", "--vault", str(self.vault), "--project", "demo", "--name", name,
                            "--kind", kind, "--new")

    def draft(self, *extra, environ=None):
        return self.run_cli("draft", "--vault", str(self.vault), "--project", "demo", "--dataset", "hallazgos",
                            "--template", "aviso", "--out", str(self.out),
                            "--var", "contacto_investigador=investigador@example.org", *extra, environ=environ)

    def avisos(self):
        p = self.pdir / "Avisos" / "registro.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


class TemplateTests(Base):
    def test_new_skeleton_passes_and_lists_its_variables(self):
        code, data, _, _ = self.new_template()
        self.assertEqual(code, 0, data)
        self.assertTrue(data["ok"])
        self.assertTrue(data["creada"])
        self.assertEqual(data["kind"], "afectado")
        self.assertEqual(data["variables"], ["contacto_investigador"])
        self.assertTrue((self.pdir / "Plantillas-avisos" / "aviso.md").is_file())
        code, data, _, _ = self.new_template()
        self.assertEqual(code, 3)
        self.assertIn("ya existe", data["error"])

    def test_vendor_skeleton(self):
        code, data, _, _ = self.new_template("mantenedores", "vendor")
        self.assertEqual(code, 0, data)
        self.assertEqual(sorted(data["variables"]), ["contacto_investigador", "resumen"])

    def test_check_flags_real_data_and_finding_placeholders_in_first_contact(self):
        self.new_template()
        p = self.pdir / "Plantillas-avisos" / "aviso.md"
        text = p.read_text(encoding="utf-8")
        text = text.replace("Contact: {{contacto_investigador}} — reference",
                            "Contact: alguien@universidad-inventada.edu — reference")
        text = text.replace("Reference: {{id_aviso}}.", "Reference: {{id_aviso}}. Files: {{files}}. "
                            "See https://github.com/org-inventada/uno and key " + fm.age_secret())
        p.write_text(text, encoding="utf-8")
        code, data, raw, _ = self.run_cli("template", "--vault", str(self.vault), "--project", "demo",
                                          "--name", "aviso")
        self.assertEqual(code, 3)
        joined = " ".join(data["problemas"])
        for needle in ("Primer contacto", "{{files}}", "correo", "GitHub", "age_secret_key"):
            self.assertIn(needle, joined)
        self.assertNotIn("universidad-inventada", raw)
        self.assertNotIn("AGE-SECRET-KEY", raw)

    def test_bad_name_is_an_input_error(self):
        code, _, _, err = self.run_cli("template", "--vault", str(self.vault), "--project", "demo",
                                       "--name", "../fuera", "--new")
        self.assertEqual(code, 1)
        self.assertIn("--name", err)

    def test_data_guard_lets_agents_into_templates_but_not_avisos(self):
        self.new_template()
        tpl = self.pdir / "Plantillas-avisos" / "aviso.md"
        reg = self.pdir / "Avisos" / "registro.json"
        reg.parent.mkdir()
        reg.write_text("[]", encoding="utf-8")
        ok = data_guard.decide({"tool_name": "Read", "tool_input": {"file_path": str(tpl)}, "cwd": str(self.vault)},
                               self.vault)
        blocked = data_guard.decide({"tool_name": "Read", "tool_input": {"file_path": str(reg)},
                                     "cwd": str(self.vault)}, self.vault)
        self.assertIsNone(ok)
        self.assertIsNotNone(blocked)


class DraftTests(Base):
    def setUp(self):
        super().setUp()
        self.new_template()

    def test_refused_inside_an_agent_session(self):
        for marker in nt.AGENT_MARKERS:
            code, data, _, _ = self.draft(environ={marker: "1"})
            self.assertEqual(code, 3)
            self.assertIn("sesión de agente", data["error"])
        self.assertFalse(self.out.exists())
        self.assertIsNone(self.avisos())

    def test_drafts_one_per_repo_with_channels_and_appends_pending_entries(self):
        (self.pdir / "Avisos").mkdir()
        (self.pdir / "Avisos" / "registro.json").write_text(json.dumps([
            {"id": "AV-007", "destinatario": "org-inventada/vieja", "canal": "correo", "enviado": "2031-01-01",
             "embargo_hasta": "2031-04-01", "respuesta": "", "estado": "enviado"}]), encoding="utf-8")
        self.write_findings(FINDINGS, extra="esto no es json\n" + json.dumps({"files": ["sin-repo"]}) + "\n")
        code, data, raw, _ = self.draft("--embargo-days", "30")
        self.assertEqual(code, 0, raw)
        # stdout: counts only, never who or what
        for secret in ("org-inventada", "config/a.yaml", "clave_age", "seguridad@", str(self.findings_file)):
            self.assertNotIn(secret, raw)
        self.assertEqual(data["borradores"], 4)
        self.assertEqual(data["repositorios"], 4)
        self.assertEqual(data["hallazgos"], 5)
        self.assertEqual(data["lineas_invalidas"], 2)
        self.assertEqual(data["canales"], {"github_pvr": 1, "correo": 1, "issue_sin_detalles": 1,
                                           "politica_seguridad": 1})
        self.assertEqual(data["avisos"], "AV-008..AV-011")
        self.assertEqual(data["embargo_hasta"], "2031-03-31")
        self.assertFalse(data["out_registrado_sensible"])
        reg = self.avisos()
        self.assertEqual([e["id"] for e in reg], ["AV-007", "AV-008", "AV-009", "AV-010", "AV-011"])
        new = {e["destinatario"]: e for e in reg[1:]}
        self.assertEqual(new["org-inventada/uno"]["canal"], "github_pvr")
        self.assertEqual(new["org-inventada/dos"]["canal"], "correo: seguridad@dos.example.org")
        self.assertEqual(new["org-inventada/tres"]["canal"], "issue_sin_detalles")
        self.assertEqual(new["org-inventada/cuatro"]["canal"], "politica_seguridad")
        for e in reg[1:]:
            self.assertEqual(e["estado"], "pendiente")
            self.assertIsNone(e["enviado"])
            self.assertEqual(e["embargo_hasta"], "2031-03-31")
            self.assertEqual(set(e), {"id", "destinatario", "canal", "enviado", "embargo_hasta", "respuesta", "estado"})
        files = sorted(p.name for p in self.out.iterdir())
        self.assertEqual(len(files), 4)
        uno = (self.out / "AV-008-org-inventada-uno.md").read_text(encoding="utf-8")
        self.assertIn("- config/a.yaml\n- config/b.yaml", uno)
        self.assertIn("- clave_age (2)", uno)
        self.assertIn("https://github.com/org-inventada/uno/security/advisories/new", uno)
        self.assertIn("investigador@example.org", uno)
        self.assertIn("2031-03-31", uno)
        self.assertNotIn("{{", uno)
        tres = (self.out / "AV-010-org-inventada-tres.md").read_text(encoding="utf-8")
        first = tres.split("## Primer contacto", 1)[1].split("## Mensaje", 1)[0]
        self.assertNotIn("k8s/d.yaml", first)
        self.assertNotIn("sealed_key", first)
        self.assertIn("k8s/d.yaml", tres.split("## Mensaje", 1)[1])
        cuatro = (self.out / "AV-011-org-inventada-cuatro.md").read_text(encoding="utf-8")
        self.assertIn("https://example.org/politica-inventada", cuatro)

    def test_open_entry_for_same_repo_is_skipped(self):
        (self.pdir / "Avisos").mkdir()
        (self.pdir / "Avisos" / "registro.json").write_text(json.dumps([
            {"id": "AV-001", "destinatario": "org-inventada/dos", "canal": "correo", "enviado": None,
             "embargo_hasta": "", "respuesta": "", "estado": "pendiente"},
            {"id": "AV-002", "destinatario": "org-inventada/tres", "canal": "correo", "enviado": None,
             "embargo_hasta": "", "respuesta": "", "estado": "resuelto"}]), encoding="utf-8")
        code, data, raw, _ = self.draft()
        self.assertEqual(code, 0, raw)
        self.assertEqual(data["ya_abiertos"], 1)
        self.assertEqual(data["borradores"], 3)
        self.assertEqual(data["avisos"], "AV-003..AV-005")

    def test_registered_out_dir_is_reported(self):
        self.registry([{"name": "hallazgos", "path": str(self.findings_file), "kind": "file", "sensitive": True},
                       {"name": "borradores", "path": str(self.out), "kind": "dir", "sensitive": True}])
        code, data, raw, _ = self.draft()
        self.assertEqual(code, 0, raw)
        self.assertTrue(data["out_registrado_sensible"])

    def test_missing_variable_refuses_and_writes_nothing(self):
        code, data, raw, _ = self.run_cli("draft", "--vault", str(self.vault), "--project", "demo",
                                          "--dataset", "hallazgos", "--template", "aviso", "--out", str(self.out))
        self.assertEqual(code, 3)
        self.assertIn("{{contacto_investigador}}", data["error"])
        self.assertNotIn("org-inventada", raw)
        self.assertFalse(self.out.exists())
        self.assertIsNone(self.avisos())

    def test_secret_in_a_finding_refuses_the_whole_run(self):
        rows = [dict(r) for r in FINDINGS]
        rows[2]["kind"] = "clave " + fm.age_secret()
        self.write_findings(rows)
        code, data, raw, _ = self.draft()
        self.assertEqual(code, 3)
        self.assertNotIn("AGE-SECRET-KEY", raw)
        self.assertNotIn("org-inventada", raw)
        self.assertEqual([p.name for p in self.out.iterdir()], ["_problemas.txt"])
        problems = (self.out / "_problemas.txt").read_text(encoding="utf-8")
        self.assertIn("org-inventada/dos", problems)
        self.assertNotIn("AGE-SECRET-KEY", problems)
        self.assertIsNone(self.avisos())

    def test_out_dir_rules(self):
        for bad in (str(self.pdir / "borradores"), "relativo/borradores"):
            code, data, _, _ = self.run_cli("draft", "--vault", str(self.vault), "--project", "demo", "--dataset",
                                            "hallazgos", "--template", "aviso", "--out", bad,
                                            "--var", "contacto_investigador=x@example.org")
            self.assertEqual(code, 3, bad)
        repo = self.tmp / "repo"
        (repo / ".git").mkdir(parents=True)
        code, data, _, _ = self.run_cli("draft", "--vault", str(self.vault), "--project", "demo", "--dataset",
                                        "hallazgos", "--template", "aviso", "--out", str(repo / "b"),
                                        "--var", "contacto_investigador=x@example.org")
        self.assertEqual(code, 3)
        self.assertIn("git", data["error"])
        self.out.mkdir()
        (self.out / "otro.txt").write_text("x", encoding="utf-8")
        code, data, _, _ = self.draft()
        self.assertEqual(code, 3)
        self.assertIn("vacía", data["error"])
        self.assertIsNone(self.avisos())

    def test_unknown_dataset_and_builtin_override(self):
        code, _, _, err = self.run_cli("draft", "--vault", str(self.vault), "--project", "demo", "--dataset",
                                       "otro", "--template", "aviso", "--out", str(self.out),
                                       "--var", "contacto_investigador=x@example.org")
        self.assertEqual(code, 1)
        self.assertIn("otro", err)
        code, data, _, _ = self.draft("--var", "repo=falso")
        self.assertEqual(code, 3)
        self.assertIn("{{repo}}", data["error"])

    def test_vendor_template_is_refused_by_draft(self):
        self.new_template("mantenedores", "vendor")
        code, data, _, _ = self.run_cli("draft", "--vault", str(self.vault), "--project", "demo", "--dataset",
                                        "hallazgos", "--template", "mantenedores", "--out", str(self.out))
        self.assertEqual(code, 3)
        self.assertIn("vendor", data["error"])


class VendorTests(Base):
    def setUp(self):
        super().setUp()
        self.new_template("mantenedores", "vendor")

    def vendor(self, environ=None):
        return self.run_cli("vendor", "--vault", str(self.vault), "--project", "demo", "--template", "mantenedores",
                            "--destinatario", "Mantenedores de herramienta-inventada", "--canal",
                            "correo: mantenedores@example.org", "--out", str(self.out),
                            "--var", "contacto_investigador=investigador@example.org",
                            "--var", "resumen=un fallo inventado", environ=environ)

    def test_vendor_draft_and_entry(self):
        code, data, raw, _ = self.vendor()
        self.assertEqual(code, 0, raw)
        self.assertEqual(data["avisos"], "AV-001")
        self.assertEqual(data["embargo_hasta"], "2031-05-30")
        reg = self.avisos()
        self.assertEqual(reg[0]["destinatario"], "Mantenedores de herramienta-inventada")
        self.assertEqual(reg[0]["estado"], "pendiente")
        text = next(self.out.iterdir()).read_text(encoding="utf-8")
        self.assertIn("Hello Mantenedores de herramienta-inventada", text)
        self.assertIn("un fallo inventado", text)
        code, data, _, _ = self.vendor()
        self.assertEqual(code, 3)  # out not empty any more / open entry

    def test_vendor_refused_in_agent_session(self):
        code, data, _, _ = self.vendor(environ={"KAIRO_AGENT_SESSION": "1"})
        self.assertEqual(code, 3)
        self.assertIsNone(self.avisos())


if __name__ == "__main__":
    unittest.main()
