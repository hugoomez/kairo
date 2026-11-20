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


# ---- unweighted output pinned; weighted validation (invented sample) ------------------

GOLDEN = """{
  "precision": 0.7142857142857143,
  "precision_lo": 0.3589344518326194,
  "precision_hi": 0.9177810759959433,
  "recall": 0.7142857142857143,
  "recall_lo": 0.3589344518326194,
  "recall_hi": 0.9177810759959433,
  "n": 20,
  "inputs": {
    "gold": {
      "path": "g.jsonl",
      "sha256": "GSHA"
    },
    "pred": {
      "path": "p.jsonl",
      "sha256": "PSHA"
    },
    "label": "l",
    "positive": "si",
    "ci": 0.95
  }
}
"""


def _golden_files(tmp_path):
    import hashlib
    g = tmp_path / "g.jsonl"
    p = tmp_path / "p.jsonl"
    g.write_bytes("\n".join(json.dumps({"id": "a%02d" % i, "l": "si" if i < 7 else "no"})
                            for i in range(20)).encode("utf-8"))
    p.write_bytes("\n".join(json.dumps({"id": "a%02d" % i, "l": "si" if (i < 5 or i in (9, 10)) else "no"})
                            for i in range(20)).encode("utf-8"))
    sha = {k: hashlib.sha256(f.read_bytes()).hexdigest() for k, f in (("G", g), ("P", p))}
    return g, p, GOLDEN.replace("GSHA", sha["G"]).replace("PSHA", sha["P"])


def test_unweighted_output_is_byte_identical(tmp_path):
    g, p, expected = _golden_files(tmp_path)
    r = _cli("validate", "--gold", str(g), "--pred", str(p), "--label", "l", "--positive", "si",
             "--vault", str(tmp_path))
    assert r.returncode == 0
    assert r.stdout == expected


def _sample(tmp_path, strata):
    """strata: list of (name, frame N, [(gold, pred), ...]); writes gold, pred and the
    draw_sample-style file with _stratum and _weight."""
    gold, pred, sample = [], [], []
    for name, big, items in strata:
        for j, (gv, pv) in enumerate(items):
            iid = f"{name}-{j}"
            gold.append({"id": iid, "l": gv})
            pred.append({"id": iid, "l": pv, "fuente": "clasif-x"})
            sample.append({"id": iid, "texto": "inventado", "_stratum": name, "_weight": big / len(items)})
    return (_w(tmp_path, "g.jsonl", gold), _w(tmp_path, "p.jsonl", pred),
            _w(tmp_path, "muestra.jsonl", sample))


def _cli8(*args):
    """_cli with UTF-8 on both ends (the gate runs agreement.py this way)."""
    import os
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                          encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def _reference(strata, which, conf=0.95):
    """Straight re-implementation of the module doc's formulas, for the test."""
    rows = []
    for name, big, items in strata:
        for gv, pv in items:
            y = float(gv and pv)
            x = float(pv if which == "precision" else gv)
            rows.append((name, big, len(items), y, x))
    ys = sum(big * y / n for _h, big, n, y, _x in rows)
    xs = sum(big * x / n for _h, big, n, _y, x in rows)
    r = ys / xs
    var = 0.0
    for _name, big, items in strata:
        d = [float(gv and pv) - r * float(pv if which == "precision" else gv) for gv, pv in items]
        n = len(d)
        m = sum(d) / n
        s2 = sum((v - m) ** 2 for v in d) / (n - 1)
        var += big * big * (1 - n / big) * s2 / n
    var /= xs * xs
    cap = sum(1 for *_a, x in rows if x)
    n_eff = cap if var == 0 else min(cap, r * (1 - r) / var)
    return r, wilson(r * n_eff, n_eff, conf), n_eff


def _labels(path):
    return [json.loads(x)["l"] for x in Path(path).read_text(encoding="utf-8").splitlines()]


def test_weighted_rare_positives_matches_reference(tmp_path):
    import hashlib
    t, f = True, False
    strata = [
        ("alta", 40, [(t, t)] * 7 + [(f, t)] * 3 + [(t, f)] * 2),
        ("media", 300, [(t, t)] * 2 + [(f, t)] * 3 + [(t, f)] * 2 + [(f, f)] * 5),
        ("baja", 5000, [(t, f)] * 1 + [(f, f)] * 11),
    ]
    g, p, s = _sample(tmp_path, strata)
    args = ("validate", "--gold", g, "--pred", p, "--label", "l", "--positive", "true",
            "--weights-from", s, "--vault", str(tmp_path))
    r = _cli8(*args)
    assert r.returncode == 0, r.stdout
    out = json.loads(r.stdout)
    assert out["weighted"] is True and out["method"] == agreement.WEIGHTED_METHOD
    assert out["strata"] == 3 and out["weights_defaulted"] == 0 and out["n"] == 36
    for which in ("precision", "recall"):
        val, (lo, hi), n_eff = _reference(strata, which)
        assert out[which] == pytest.approx(val)
        assert out[f"{which}_lo"] == pytest.approx(lo) and out[f"{which}_hi"] == pytest.approx(hi)
        assert out["n_eff"][which] == pytest.approx(n_eff)
        assert out[f"{which}_lo"] <= out[which] <= out[f"{which}_hi"]
    # the over-sampled high-score stratum no longer dominates recall
    unweighted = agreement.validate([agreement.norm(x) for x in _labels(g)],
                                    [agreement.norm(x) for x in _labels(p)], "true")
    assert out["recall"] < unweighted["recall"]
    assert out["inputs"]["weights"] == {"path": "muestra.jsonl",
                                        "sha256": hashlib.sha256(Path(s).read_bytes()).hexdigest()}
    assert _cli8(*args).stdout == r.stdout  # deterministic: no resampling, no seed


def test_weighted_with_unit_weights_equals_unweighted(tmp_path):
    g, p, expected = _golden_files(tmp_path)
    s = _w(tmp_path, "s.jsonl", [{"id": "a%02d" % i} for i in range(20)])
    r = json.loads(_cli("validate", "--gold", str(g), "--pred", str(p), "--label", "l",
                        "--positive", "si", "--weights-from", s).stdout)
    base = json.loads(expected)
    for k in ("precision", "precision_lo", "precision_hi", "recall", "recall_lo", "recall_hi"):
        assert r[k] == pytest.approx(base[k])
    assert r["weights_defaulted"] == 0 and r["strata"] == 1


def test_weighted_ids_missing_from_sample_default_to_one(tmp_path):
    g, p, _ = _golden_files(tmp_path)
    s = _w(tmp_path, "s.jsonl", [{"id": "a%02d" % i, "_stratum": "x", "_weight": 1} for i in range(10)])
    r = json.loads(_cli("validate", "--gold", str(g), "--pred", str(p), "--label", "l",
                        "--positive", "si", "--weights-from", s).stdout)
    assert r["weights_defaulted"] == 10 and r["strata"] == 11


def test_weighted_refusals(tmp_path):
    g = _w(tmp_path, "g0.jsonl", [{"l": "x"}, {"l": "y"}])
    s = _w(tmp_path, "s0.jsonl", [{"id": 1, "_weight": 2}])
    r = _cli("validate", "--gold", g, "--pred", g, "--label", "l", "--positive", "x", "--weights-from", s)
    assert r.returncode == 3 and "ids" in json.loads(r.stdout)["error"]
    t, f = True, False
    g2, p2, s2 = _sample(tmp_path, [("a", 50, [(t, t)]), ("b", 50, [(t, f), (f, f)])])
    r = _cli("validate", "--gold", g2, "--pred", p2, "--label", "l", "--positive", "true", "--weights-from", s2)
    assert r.returncode == 3 and "un solo elemento" in json.loads(r.stdout)["error"]
    bad = _w(tmp_path, "bad.jsonl", [{"id": "a-0", "_weight": 0.5}])
    r = _cli("validate", "--gold", g2, "--pred", p2, "--label", "l", "--positive", "true", "--weights-from", bad)
    assert r.returncode == 3 and "_weight" in json.loads(r.stdout)["error"]
