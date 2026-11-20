"""The project templates are documented in templates/project-template.md and in
the create-project skill's table; there is no script that validates a hub per
template, so these tests pin the two documents to the same rules."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = (ROOT / "templates" / "project-template.md").read_text(encoding="utf-8")
SKILL = (ROOT / "skills" / "create-project" / "SKILL.md").read_text(encoding="utf-8")


def skill_row(name: str) -> list[str]:
    for line in SKILL.splitlines():
        if line.startswith("| `" + name + "`"):
            return [c.strip() for c in line.strip().strip("|").split("|")]
    raise AssertionError(f"no row for template {name} in create-project SKILL.md")


class TestMedicionTemplate(unittest.TestCase):
    def test_medicion_template_fields(self):
        row = skill_row("medicion")
        self.assertEqual(row[1], "`ciencia`")
        self.assertIn("`seed_hypotheses: false`", row[2])
        self.assertIn("`code_repo`", row[2])
        self.assertNotIn("sota_map: false", row[2])  # the literature check still applies
        self.assertIn("`sota_map: diferido`", row[2])  # light start: novelty check, map later
        self.assertIn("`etica: Projects/<slug>/Etica.md`", row[2])
        self.assertIn("`Etica.md`", row[3])
        self.assertIn("6b", row[3])

    def test_template_file_lists_medicion(self):
        m = re.search(r"^template: <(.*)>$", TEMPLATE, re.M)
        self.assertIsNotNone(m)
        self.assertIn("medicion", [v.strip() for v in m.group(1).split("|")])
        self.assertRegex(TEMPLATE, r"#\s+medicion\s+— type ciencia")
        self.assertRegex(TEMPLATE, r"code_repo — .*medicion")

    def test_skill_offers_plan_study_and_names_hub_fields(self):
        m = re.search(r"\*\*`medicion`\*\*(.*?)(?=\n\*\*|\n#{2,3} )", SKILL, re.S)
        self.assertIsNotNone(m, "no medicion section in SKILL.md")
        span = m.group(1)
        for needle in ("code_repo", "default_linea_publicacion", "deadline",
                       "sota_map", "plan-study"):
            self.assertIn(needle, span)
        self.assertRegex(SKILL, r"`seed_hypotheses: false` \(.*`medicion`")
        self.assertRegex(SKILL, r"For `medicion` end instead by pointing to the next\s+step: .{0,5}planifica la primera pregunta \(plan-study")
        self.assertIn("`aplicado` and `medicion` templates", SKILL)


class TestSotaMapDiferido(unittest.TestCase):
    def test_template_documents_the_three_values(self):
        m = re.search(r"# sota_map —(.*?)\nsota_map: (\w+)\n", TEMPLATE, re.S)
        self.assertIsNotNone(m)
        comment, default = m.group(1), m.group(2)
        self.assertEqual(default, "true")
        for v in ("true", "false", "diferido"):
            self.assertRegex(comment, rf"#\s+{v}\s+—")
        self.assertIn("Novedad.md", comment)
        self.assertIn("next_steps.py", comment)

    def test_skill_has_the_novelty_step_and_defers_step_7(self):
        m = re.search(r"### 6b\. Novelty check(.*?)\n### 7\. ", SKILL, re.S)
        self.assertIsNotNone(m, "no step 6b before step 7")
        step = m.group(1)
        for needle in ("Novedad.md", "facet-summarizer", "check_sota.py", "--file Novedad.md",
                       "fresh-verifier", "## Medidas previas", "## Cobertura de lectura",
                       "## Búsqueda ejecutada", "plan-study", "pilot"):
            self.assertIn(needle, step)
        s7 = SKILL.split("### 7. ", 1)[1].split("### 8. ", 1)[0]
        self.assertIn("`sota_map: diferido`", s7.split("**Full text before the map.**")[0])
        self.assertIn("construye el estado del arte", s7)
        self.assertIn("`sota_map: true`", s7)
        s4 = SKILL.split("### 4. ", 1)[1].split("### 5. ", 1)[0]
        self.assertIn("novelty-check search", s4)
        self.assertIn("web pass", s4)


class TestEthicsTemplate(unittest.TestCase):
    ETHICS = (ROOT / "templates" / "ethics-plan-template.md").read_text(encoding="utf-8")
    SECTIONS = ("Partes afectadas", "Beneficios y daños", "Qué se recoge y qué nunca",
                "Material sensible encontrado y su tratamiento", "Condiciones de uso de la plataforma o API",
                "Comité de ética", "Divulgación a los afectados", "Conservación y borrado", "Publicación",
                "Riesgos residuales")

    def test_sections_in_order_each_with_an_invented_example(self):
        heads = re.findall(r"^## (.+)$", self.ETHICS, re.M)
        self.assertEqual(tuple(heads), self.SECTIONS)
        for h in self.SECTIONS:
            body = self.ETHICS.split(f"## {h}\n", 1)[1].split("\n## ", 1)[0]
            self.assertIn("Ejemplo inventado", body, h)

    def test_menlo_principles_and_frontmatter(self):
        for p in ("Respeto a las personas", "Beneficencia", "Justicia", "Respeto a la ley y al interés público"):
            self.assertIn(p, self.ETHICS)
        fm = self.ETHICS.split("---\n", 2)[1]
        for key in ("project:", "tipo: plan-etico", "status:", "comite:", "revisado:"):
            self.assertIn(key, fm)
        self.assertNotRegex(fm, r"(?m)^id:")   # not a ledger node: no id scanners pick it up

    def test_plans_and_hub_point_to_it(self):
        for name in ("measurement-plan-template.md", "pilot-plan-template.md"):
            text = (ROOT / "templates" / name).read_text(encoding="utf-8")
            etica = text.split("## Ética\n", 1)[1]
            self.assertIn("templates/ethics-plan-template.md", etica, name)
            self.assertIn("`etica:`", etica, name)
        self.assertRegex(TEMPLATE, r"(?m)^etica: Projects/<slug>/Etica\.md$")
        self.assertIn("templates/ethics-plan-template.md", SKILL)
        self.assertIn("Projects/<slug>/Etica.md", SKILL)


if __name__ == "__main__":
    unittest.main()
