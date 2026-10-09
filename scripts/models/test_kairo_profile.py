"""Tests for kairo_profile.py: the literature profile allows the literature path only."""

from __future__ import annotations

import contextlib
import io
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import kairo_profile as kp  # noqa: E402

SKILLS = HERE.parents[1] / "skills"


def run(*args):
    with contextlib.redirect_stdout(io.StringIO()):
        return kp.main(list(args))


class Profile(unittest.TestCase):
    def test_default_allows_everything(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(run("check", "hypothesis-cycle"), 0)
            self.assertEqual(run("check-template", "teorico"), 0)
            self.assertTrue(kp.programme_hooks())

    def test_literature_profile(self):
        with mock.patch.dict(os.environ, {"KAIRO_PROFILE": "literatura"}):
            self.assertEqual(run("check", "kairo:literature-search"), 0)
            self.assertEqual(run("check", "hypothesis-cycle"), 3)
            self.assertEqual(run("check-template", "revision"), 0)
            self.assertEqual(run("check-template", "ciencia"), 3)
            self.assertFalse(kp.programme_hooks())

    def test_every_skill_is_classified_and_every_programme_skill_checks_first(self):
        names = {p.parent.name for p in SKILLS.glob("*/SKILL.md")}
        self.assertEqual(names, kp.LITERATURE_SKILLS | kp.PROGRAMME_SKILLS)
        for name in kp.PROGRAMME_SKILLS:
            body = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
            head = body.split("\n## ", 2)[:2]
            self.assertTrue(re.search(rf"profile\.py\" check {re.escape(name)}\b", "\n## ".join(head)),
                            f"{name}: no profile check before its first section")


if __name__ == "__main__":
    unittest.main()
