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
