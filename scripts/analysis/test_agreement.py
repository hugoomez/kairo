"""Tests for agreement.py (invented data only)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import agreement  # noqa: E402
from estimate import wilson  # noqa: E402

SCRIPT = HERE / "agreement.py"


def test_kappa_perfect_and_chance():
    a = ["x", "y", "x", "y"]
    assert agreement.kappa(a, a)["kappa"] == 1
    # observed 0.5 == expected 0.5
    r = agreement.kappa(["x", "x", "y", "y"], ["x", "y", "x", "y"])
    assert r["kappa"] == 0 and r["observed"] == 0.5 and r["expected"] == 0.5 and r["n"] == 4


def test_validate_precision_recall():
    gold = ["p"] * 12 + ["n"] * 5
    pred = ["p"] * 8 + ["n"] * 4 + ["p"] * 2 + ["n"] * 3
    r = agreement.validate(gold, pred, "p", 0.95)
    assert r["precision"] == pytest.approx(0.8) and r["recall"] == pytest.approx(8 / 12)
    assert (r["precision_lo"], r["precision_hi"]) == wilson(8, 10, 0.95)
    assert (r["recall_lo"], r["recall_hi"]) == wilson(8, 12, 0.95)
    assert r["n"] == 17


def test_cli(tmp_path):
    def w(name, labs):
        p = tmp_path / name
        p.write_text("\n".join(json.dumps({"id": i, "lab": v}) for i, v in enumerate(labs)),
                     encoding="utf-8")
        return str(p)
    a, b = w("a.jsonl", ["x", "y", "x", "y"]), w("b.jsonl", ["x", "y", "x", "y"])
    p = subprocess.run([sys.executable, str(SCRIPT), "kappa", "--a", a, "--b", b, "--label", "lab"],
                       capture_output=True, text=True)
    assert p.returncode == 0 and json.loads(p.stdout)["kappa"] == 1
    p = subprocess.run([sys.executable, str(SCRIPT), "validate", "--gold", a, "--pred", b,
                        "--label", "lab", "--positive", "x"], capture_output=True, text=True)
    assert p.returncode == 0 and json.loads(p.stdout)["precision"] == 1
