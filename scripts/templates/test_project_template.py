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

    def test_template_file_lists_medicion(self):
        m = re.search(r"^template: <(.*)>$", TEMPLATE, re.M)
        self.assertIsNotNone(m)
        self.assertIn("medicion", [v.strip() for v in m.group(1).split("|")])
        self.assertRegex(TEMPLATE, r"#\s+medicion\s+— type ciencia")
        self.assertRegex(TEMPLATE, r"code_repo — .*medicion")

    def test_skill_offers_plan_study_and_names_hub_fields(self):
        self.assertIn("**`medicion`**", SKILL)
        for needle in ("kairo:plan-study", "default_linea_publicacion", "deadline",
                       "sota_map", "plan-study)", "`aplicado` and `medicion` templates"):
            self.assertIn(needle, SKILL)
        self.assertRegex(SKILL, r"`seed_hypotheses: false` \(.*`medicion`")


if __name__ == "__main__":
    unittest.main()
