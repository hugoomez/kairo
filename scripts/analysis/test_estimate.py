"""Tests for estimate.py (invented data only)."""
import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import estimate  # noqa: E402
from estimate import wilson  # noqa: E402

SCRIPT = HERE / "estimate.py"
IVL = {"ci": 0.95, "bootstrap": 500, "seed": 7}


def test_wilson_known_values():
    lo, hi = wilson(0, 10, 0.95)
    assert lo == 0 and hi == pytest.approx(0.2775, abs=1e-4)
    lo, hi = wilson(5, 10, 0.95)
    assert lo == pytest.approx(0.2366, abs=1e-4) and hi == pytest.approx(0.7634, abs=1e-4)


ROWS = [
    {"tipo": "org", "tiene_x": "1", "g": "a"}, {"tipo": "org", "tiene_x": "sí", "g": "a"},
    {"tipo": "org", "tiene_x": "0", "g": "b"}, {"tipo": "org", "tiene_x": "true", "g": "b"},
    {"tipo": "org", "tiene_x": "", "g": "c"}, {"tipo": "per", "tiene_x": "1", "g": "c"},
    {"tipo": "per", "tiene_x": "0", "g": "d"}, {"tipo": "per", "tiene_x": "1", "g": "d"},
]


def est(**kw):
    e = {"id": "est-1", "type": "proporcion", "column": "tiene_x", "filter": {}, "cluster": None}
    e.update(kw)
    return e


def test_proportion_with_filter():
    r = estimate.compute(est(filter={"tipo": "org"}), ROWS, IVL)
    assert r["value"] == pytest.approx(3 / 5) and r["n"] == 5 and r["method"] == "wilson"
    assert list(r) == ["id", "type", "value", "lo", "hi", "n", "method"]


def test_cluster_bootstrap_deterministic():
    rows = [{"x": "1" if i < 10 else "0", "g": f"g{i // 5}"} for i in range(20)]
    e = est(column="x", cluster="g")
    a = estimate.compute(e, rows, IVL)
    b = estimate.compute(e, rows, IVL)
    assert (a["lo"], a["hi"]) == (b["lo"], b["hi"]) and a["method"] == "cluster-bootstrap"
    plain = estimate.compute(est(column="x"), rows, IVL)
    assert a["hi"] - a["lo"] >= plain["hi"] - plain["lo"]


def test_conteo():
    r = estimate.compute(est(type="conteo", filter={"tipo": "org"}), ROWS, IVL)
    assert (r["value"], r["lo"], r["hi"], r["method"]) == (3, 3, 3, "exacto")


def test_median_bootstrap():
    rows = [{"v": str(v)} for v in [1, 2, 3, 4, 100]] + [{"v": "x"}, {"v": ""}]
    r = estimate.compute(est(type="mediana", column="v"), rows, IVL)
    assert r["value"] == 3 and r["skipped"] == 2 and r["lo"] <= 3 <= r["hi"]


def setup_vault(tmp_path, sha=None, kind="file", rows=ROWS, ext="csv", plan_col="tiene_x"):
    proj = tmp_path / "Projects" / "demo"
    (proj / "Mediciones").mkdir(parents=True)
    (proj / "Datos").mkdir()
    f = proj / "Datos" / f"u.{ext}"
    if ext == "csv":
        with f.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    else:
        f.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    real = hashlib.sha256(f.read_bytes()).hexdigest()
    reg = [{"name": "u", "path": str(f), "kind": kind, "files": 1, "bytes": 1, "sha256": sha or real}]
    (proj / "Datos" / "registro.json").write_text(json.dumps(reg), encoding="utf-8")
    e = json.dumps({"estimands": [est(column=plan_col, filter={"tipo": "org"})]})
    (proj / "Mediciones" / "M-9002.md").write_text(
        "---\nid: M-9002\nfrozen_at: 2026-01-01T00:00:00Z\nfrozen_commit: abc\n---\n\n"
        f"## Estimandos\n\n```json\n{e}\n```\n\n"
        '## Clasificadores\n\n```json\n{"classifiers": []}\n```\n\n'
        f"## Intervalos\n\n```json\n{json.dumps(IVL)}\n```\n", encoding="utf-8")
    return real


def run(tmp_path):
    p = subprocess.run([sys.executable, str(SCRIPT), "--vault", str(tmp_path), "--plan", "M-9002",
                        "--units", "u"], capture_output=True, text=True)
    return p.returncode, p.stdout


@pytest.mark.parametrize("ext", ["csv", "jsonl"])
def test_cli_ok_and_no_rows_printed(tmp_path, ext):
    real = setup_vault(tmp_path, ext=ext)
    code, out = run(tmp_path)
    assert code == 0, out
    d = json.loads(out)
    assert list(d) == ["plan", "units_sha256", "estimates"] and d["units_sha256"] == real
    assert d["estimates"][0]["value"] == pytest.approx(0.6)


def test_refuses_changed_units(tmp_path):
    real = setup_vault(tmp_path, sha="0" * 64)
    code, out = run(tmp_path)
    assert code == 3
    d = json.loads(out)
    assert d["esperada"] == "0" * 64 and d["actual"] == real


def test_dir_dataset_refused(tmp_path):
    setup_vault(tmp_path, kind="dir")
    assert run(tmp_path)[0] == 3


def test_missing_column_names_estimand_and_column(tmp_path):
    setup_vault(tmp_path, plan_col="nope")
    code, out = run(tmp_path)
    assert code == 3 and "est-1" in out and "nope" in out


def test_non_utf8_refused_without_traceback(tmp_path):
    setup_vault(tmp_path)
    f = tmp_path / "Projects" / "demo" / "Datos" / "u.csv"
    f.write_bytes("tipo,tiene_x\norg,\xe9\n".encode("latin-1"))
    reg = tmp_path / "Projects" / "demo" / "Datos" / "registro.json"
    d = json.loads(reg.read_text(encoding="utf-8"))
    d[0]["sha256"] = hashlib.sha256(f.read_bytes()).hexdigest()
    reg.write_text(json.dumps(d), encoding="utf-8")
    p = subprocess.run([sys.executable, str(SCRIPT), "--vault", str(tmp_path), "--plan", "M-9002",
                        "--units", "u"], capture_output=True, text=True)
    assert p.returncode == 3 and "UTF-8" in p.stdout and "Traceback" not in p.stderr


def test_missing_cluster_refused(tmp_path):
    setup_vault(tmp_path)
    plan = tmp_path / "Projects" / "demo" / "Mediciones" / "M-9002.md"
    plan.write_text(plan.read_text(encoding="utf-8").replace('"cluster": null', '"cluster": "g"'),
                    encoding="utf-8")
    f = tmp_path / "Projects" / "demo" / "Datos" / "u.csv"
    f.write_text("tipo,tiene_x,g\norg,1,a\norg,0,\norg,1,\n", encoding="utf-8")
    reg = tmp_path / "Projects" / "demo" / "Datos" / "registro.json"
    d = json.loads(reg.read_text(encoding="utf-8"))
    d[0]["sha256"] = hashlib.sha256(f.read_bytes()).hexdigest()
    reg.write_text(json.dumps(d), encoding="utf-8")
    p = subprocess.run([sys.executable, str(SCRIPT), "--vault", str(tmp_path), "--plan", "M-9002",
                        "--units", "u"], capture_output=True, text=True)
    assert p.returncode == 3 and "est-1" in p.stdout and "2 filas" in p.stdout


def test_percentile_symmetric_indices():
    vals = list(range(11))  # B=11
    assert estimate._percentile(vals, 0.8) == (1, 9)  # a=0.1 -> floor(1.0)=1, ceil(9.0)=9
    assert estimate._percentile(list(range(5)), 0.5) == (1, 3)


def _reregister(tmp_path):
    f = next((tmp_path / "Projects" / "demo" / "Datos").glob("u.*"))
    reg = tmp_path / "Projects" / "demo" / "Datos" / "registro.json"
    d = json.loads(reg.read_text(encoding="utf-8"))
    d[0]["sha256"] = hashlib.sha256(f.read_bytes()).hexdigest()
    reg.write_text(json.dumps(d), encoding="utf-8")


def test_null_cluster_in_jsonl_counts_as_missing(tmp_path):
    rows = [{"tipo": "org", "tiene_x": 1, "g": "a"}, {"tipo": "org", "tiene_x": 0, "g": None},
            {"tipo": "org", "tiene_x": 1, "g": "b"}]
    setup_vault(tmp_path, rows=rows, ext="jsonl")
    plan = tmp_path / "Projects" / "demo" / "Mediciones" / "M-9002.md"
    plan.write_text(plan.read_text(encoding="utf-8").replace('"cluster": null', '"cluster": "g"'),
                    encoding="utf-8")
    code, out = run(tmp_path)
    assert code == 3 and "est-1" in out and "1 filas" in out


def test_plan_with_a_title_in_its_file_name(tmp_path):
    setup_vault(tmp_path)
    folder = tmp_path / "Projects" / "demo" / "Mediciones"
    (folder / "M-9002.md").rename(folder / "M-9002 Plan inventado.md")
    code, out = run(tmp_path)
    assert code == 0, out
    assert json.loads(out)["plan"] == "M-9002"


def test_plan_outside_the_design_plan_folder_is_not_found(tmp_path):
    setup_vault(tmp_path)
    proj = tmp_path / "Projects" / "demo"
    (proj / "Otra").mkdir()
    (proj / "Mediciones" / "M-9002.md").rename(proj / "Otra" / "M-9002.md")
    code, out = run(tmp_path)
    assert code == 3 and "M-9002" in json.loads(out)["error"]


def test_plan_lookalike_id_is_not_taken(tmp_path):
    setup_vault(tmp_path)
    folder = tmp_path / "Projects" / "demo" / "Mediciones"
    (folder / "M-9002.md").rename(folder / "M-90021.md")
    assert run(tmp_path)[0] == 3


# ---- by, diferencia, reutilizacion, supervivencia, tamano ------------------

def test_by_adds_one_estimate_per_group():
    r = estimate.compute(est(by="tipo"), ROWS, IVL)
    assert r["value"] == pytest.approx(5 / 8)
    assert [g["group"] for g in r["groups"]] == ["org", "per"]
    org = r["groups"][0]
    assert org["value"] == pytest.approx(3 / 5) and org["n"] == 5 and "id" not in org


def test_by_refuses_identifier_like_columns():
    rows = [{"x": "1", "repo": f"r{i}"} for i in range(60)]
    with pytest.raises(estimate.Refused, match="máximo 50"):
        estimate.compute(est(column="x", by="repo"), rows, IVL)


def test_difference_newcombe_known_value():
    # Newcombe (1998) example: 56/70 vs 48/80 -> 0.2, 95% CI 0.0524 to 0.3339
    rows = ([{"x": "1", "t": "a"}] * 56 + [{"x": "0", "t": "a"}] * 14
            + [{"x": "1", "t": "b"}] * 48 + [{"x": "0", "t": "b"}] * 32)
    r = estimate.compute(est(type="diferencia", column="x", group="t", a="a", b="b"), rows, IVL)
    assert r["value"] == pytest.approx(0.2)
    assert r["lo"] == pytest.approx(0.0524, abs=1e-3) and r["hi"] == pytest.approx(0.3339, abs=1e-3)
    assert r["a"] == {"group": "a", "n": 70, "value": pytest.approx(0.8)} and r["method"] == "newcombe"


def test_difference_cluster_bootstrap_is_deterministic():
    rows = [{"x": str(i % 3 == 0), "t": "a" if i % 2 else "b", "org": f"o{i // 4}"} for i in range(80)]
    e = est(type="diferencia", column="x", group="t", a="a", b="b", cluster="org")
    one, two = estimate.compute(e, rows, IVL), estimate.compute(e, rows, IVL)
    assert one == two and one["method"] == "cluster-bootstrap" and one["lo"] <= one["value"] <= one["hi"]


def test_difference_with_an_empty_side():
    r = estimate.compute(est(type="diferencia", column="x", group="t", a="a", b="z"),
                         [{"x": "1", "t": "a"}], IVL)
    assert r["value"] is None and r["b"]["n"] == 0


def test_reuse_counts_keys_never_prints_them():
    rows = ([{"k": "K1", "repo": "r1"}, {"k": "K1", "repo": "r2"}, {"k": "K1", "repo": "r3"},
             {"k": "K2", "repo": "r1"}, {"k": "K2", "repo": "r1"}, {"k": "K3", "repo": "r4"},
             {"k": "", "repo": "r5"}])
    r = estimate.compute(est(type="reutilizacion", column="k", across="repo", min_groups=2), rows, IVL)
    assert r["n"] == 3 and r["value"] == pytest.approx(1 / 3)
    assert r["histogram"] == {"1": 2, "2": 0, "3-5": 1, "6-10": 0, ">10": 0}
    assert r["max_groups"] == 3 and r["rows"] == 6 and r["rows_with_shared_key"] == 3
    text = json.dumps(r)
    assert "K1" not in text and "r1" not in text


def test_survival_kaplan_meier_median_with_censoring():
    # times 1..10, every second one censored: S(5) = .9 * 7/8 * 5/6, median 7
    rows = [{"d": str(t), "ev": "1" if t % 2 else "0"} for t in range(1, 11)]
    e = est(type="supervivencia", column="d", event="ev", at=[5])
    r = estimate.compute(e, rows, IVL)
    assert r["value"] == 7 and r["events"] == 5 and r["median_reached"] is True
    assert r["survival_at"][0]["t"] == 5 and r["survival_at"][0]["s"] == pytest.approx(0.65625)
    assert r == estimate.compute(e, rows, IVL)


def test_survival_median_not_reached():
    rows = [{"d": str(t), "ev": "1" if t == 1 else "0"} for t in range(1, 11)]
    r = estimate.compute(est(type="supervivencia", column="d", event="ev", at=[]), rows, IVL)
    assert r["value"] is None and r["median_reached"] is False and r["hi"] is None


def test_survival_by_group():
    rows = [{"d": str(t), "ev": "1", "tool": "x" if t < 6 else "y"} for t in range(1, 11)]
    r = estimate.compute(est(type="supervivencia", column="d", event="ev", at=[], by="tool"), rows, IVL)
    assert [g["value"] for g in r["groups"]] == [3, 8]


def test_sample_size_matches_wilson():
    n = estimate.sample_size(0.5, 0.05)
    lo, hi = wilson(round(0.5 * n), n, 0.95)
    assert (hi - lo) / 2 <= 0.05 and 370 <= n <= 390
    lo, hi = wilson(round(0.5 * (n - 1)), n - 1, 0.95)
    assert (hi - lo) / 2 > 0.05


def test_tamano_cli():
    p = subprocess.run([sys.executable, str(SCRIPT), "tamano", "--p", "0.1", "--half-width", "0.03"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout
    assert json.loads(p.stdout)["n"] > 300
    bad = subprocess.run([sys.executable, str(SCRIPT), "tamano", "--p", "2", "--half-width", "0.03"],
                         capture_output=True, text=True)
    assert bad.returncode == 3


def test_cli_checks_new_columns(tmp_path):
    setup_vault(tmp_path)
    plan = tmp_path / "Projects" / "demo" / "Mediciones" / "M-9002.md"
    e = json.dumps({"estimands": [{"id": "r", "type": "reutilizacion", "column": "tiene_x",
                                   "across": "no_existe", "filter": {}, "cluster": None}]})
    text = plan.read_text(encoding="utf-8")
    start = text.index("```json\n") + len("```json\n")
    plan.write_text(text[:start] + e + text[text.index("\n```", start):], encoding="utf-8")
    code, out = run(tmp_path)
    assert code == 3 and "no_existe" in out
