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


def _w(tmp_path, name, recs):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")
    return str(p)


def _cli(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


def test_ids_all_or_none(tmp_path):
    a = _w(tmp_path, "a.jsonl", [{"id": 1, "l": "x"}, {"id": 2, "l": "y"}])
    b = _w(tmp_path, "b.jsonl", [{"l": "x"}, {"l": "y"}])
    c = _w(tmp_path, "c.jsonl", [{"id": 1, "l": "x"}, {"l": "y"}])
    d = _w(tmp_path, "d.jsonl", [{"id": 1, "l": "x"}, {"id": 3, "l": "y"}])
    assert _cli("kappa", "--a", a, "--b", b, "--label", "l").returncode == 3
    assert _cli("kappa", "--a", a, "--b", c, "--label", "l").returncode == 3
    p = _cli("kappa", "--a", a, "--b", d, "--label", "l")
    assert p.returncode == 3 and "1 solo en el primer" in p.stdout
    assert _cli("kappa", "--a", b, "--b", b, "--label", "l").returncode == 0


def test_positive_normalisation_and_refusal(tmp_path):
    g = _w(tmp_path, "g.jsonl", [{"l": True}, {"l": False}, {"l": True}])
    p = _w(tmp_path, "p.jsonl", [{"l": True}, {"l": True}, {"l": False}])
    r = _cli("validate", "--gold", g, "--pred", p, "--label", "l", "--positive", "true")
    assert r.returncode == 0 and json.loads(r.stdout)["precision"] == 0.5
    assert _cli("validate", "--gold", g, "--pred", p, "--label", "l", "--positive", "zzz").returncode == 3
    assert agreement.norm(1.0) == "1" and agreement.norm(True) == "true" and agreement.norm(" a ") == "a"
    # True and 1 are no longer merged in kappa
    a = _w(tmp_path, "a.jsonl", [{"l": True}, {"l": 1}])
    assert json.loads(_cli("kappa", "--a", a, "--b", a, "--label", "l").stdout)["expected"] == 0.5


def test_kappa_undefined_single_value():
    r = agreement.kappa(["x", "x"], ["x", "x"])
    assert r["kappa"] is None and "indefinido" in r["note"]


def test_validate_records_inputs(tmp_path):
    import hashlib
    vault = tmp_path / "v"
    vault.mkdir()
    g = _w(vault, "g.jsonl", [{"l": "x"}, {"l": "y"}, {"l": "x"}])
    p = _w(tmp_path, "p.jsonl", [{"l": "x"}, {"l": "x"}, {"l": "x"}])
    r = _cli("validate", "--gold", g, "--pred", p, "--label", "l", "--positive", " x ", "--ci", "0.9",
             "--vault", str(vault))
    assert r.returncode == 0
    inp = json.loads(r.stdout)["inputs"]
    assert inp["gold"] == {"path": "g.jsonl", "sha256": hashlib.sha256(Path(g).read_bytes()).hexdigest()}
    assert inp["pred"]["path"] == p  # outside the vault: as given
    assert (inp["label"], inp["positive"], inp["ci"]) == ("l", "x", 0.9)



def test_non_utf8_file_says_save_as_utf8(tmp_path):
    a = _w(tmp_path, "a.jsonl", [{"l": "x"}, {"l": "y"}])
    b = tmp_path / "b.jsonl"
    b.write_bytes('{"l": "s\u00ed"}\n{"l": "y"}\n'.encode("latin-1"))
    p = _cli("kappa", "--a", a, "--b", str(b), "--label", "l")
    assert p.returncode == 3
    assert "guarda el fichero en UTF-8" in json.loads(p.stdout)["error"]
    assert "Traceback" not in p.stderr
