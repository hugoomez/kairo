import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import status as sc  # noqa: E402


class TestSecondCriticSwitch(unittest.TestCase):
    def test_off_by_default_even_with_a_token(self):
        self.assertEqual(sc.status({})["state"], "desactivado")
        self.assertEqual(sc.status({"DEEPINFRA_TOKEN": "invented"})["state"], "desactivado")

    def test_on_needs_the_token(self):
        self.assertEqual(sc.status({"KAIRO_SECOND_CRITIC": "on"})["state"], "sin_token")
        st = sc.status({"KAIRO_SECOND_CRITIC": "ON", "DEEPINFRA_TOKEN": "invented"})
        self.assertTrue(st["available"])

    def test_label_says_no_disponible_with_the_reason(self):
        self.assertTrue(sc.label(sc.status({})).startswith("no disponible — desactivado"))


if __name__ == "__main__":
    unittest.main()
