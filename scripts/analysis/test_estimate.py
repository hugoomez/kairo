"""Tests for estimate.py (invented data only)."""
import csv
import hashlib
import json
import statistics
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


# ---- backward compatibility: a plan with only the original features --------

def legacy_rows():
    return [{"tipo": "org" if i % 3 else "per", "tiene_x": "1" if (i * 7) % 5 < 2 else "0",
             "sitio": f"s{i // 6}", "formato": "f1" if i % 2 else "f2", "huella": f"h{(i * 5) % 17}",
             "dias": str(3 + (i * 11) % 40), "cambio_visto": "1" if i % 4 else "0",
             "valor": str((i * 13) % 29)} for i in range(48)]


LEGACY_ESTIMANDS = [
    {"id": "est-1", "type": "proporcion", "column": "tiene_x", "filter": {"tipo": "org"}, "cluster": "sitio"},
    {"id": "est-2", "type": "conteo", "column": "tiene_x", "filter": {}, "cluster": None, "by": "formato"},
    {"id": "est-3", "type": "diferencia", "column": "tiene_x", "group": "formato", "a": "f1", "b": "f2",
     "filter": {}, "cluster": "sitio"},
    {"id": "est-3b", "type": "diferencia", "column": "tiene_x", "group": "formato", "a": "f1", "b": "f2",
     "filter": {}, "cluster": None},
    {"id": "est-4", "type": "reutilizacion", "column": "huella", "across": "sitio", "min_groups": 2,
     "filter": {}, "cluster": None},
    {"id": "est-5", "type": "supervivencia", "column": "dias", "event": "cambio_visto", "at": [10, 30],
     "by": "formato", "filter": {}, "cluster": None},
    {"id": "est-6", "type": "mediana", "column": "valor", "filter": {"tipo": "per"}, "cluster": None, "by": "formato"},
    {"id": "est-7", "type": "proporcion", "column": "tiene_x", "filter": {}, "cluster": None, "by": "tipo"},
]


def write_plan(tmp_path, estimands, ivl=None, families=None):
    block = {"estimands": estimands}
    if families is not None:
        block["families"] = families
    (tmp_path / "Projects" / "demo" / "Mediciones" / "M-9002.md").write_text(
        "---\nid: M-9002\nfrozen_at: 2026-01-01T00:00:00Z\nfrozen_commit: abc\n---\n\n"
        f"## Estimandos\n\n```json\n{json.dumps(block)}\n```\n\n"
        '## Clasificadores\n\n```json\n{"classifiers": []}\n```\n\n'
        f"## Intervalos\n\n```json\n{json.dumps(ivl or IVL)}\n```\n", encoding="utf-8")


def add_dataset(tmp_path, name, rows):
    """Writes Datos/<name>.csv and registers it (appending to the registry)."""
    datos = tmp_path / "Projects" / "demo" / "Datos"
    f = datos / f"{name}.csv"
    with f.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    reg_path = datos / "registro.json"
    reg = json.loads(reg_path.read_text(encoding="utf-8")) if reg_path.exists() else []
    reg.append({"name": name, "path": str(f), "kind": "file", "files": 1, "bytes": 1,
                "sha256": hashlib.sha256(f.read_bytes()).hexdigest()})
    reg_path.write_text(json.dumps(reg), encoding="utf-8")
    return reg[-1]["sha256"]


def new_vault(tmp_path):
    (tmp_path / "Projects" / "demo" / "Mediciones").mkdir(parents=True)
    (tmp_path / "Projects" / "demo" / "Datos").mkdir()


def run_cli(tmp_path, *args):
    p = subprocess.run([sys.executable, str(SCRIPT), "--vault", str(tmp_path), "--plan", "M-9002", *args],
                       capture_output=True, text=True, encoding="utf-8")
    return p.returncode, p.stdout


def test_legacy_plan_output_is_byte_identical(tmp_path):
    """The gate compares a fresh run with the stored estimate-json block: for a plan that uses
    only the original features the stdout must never change (pinned before the extension)."""
    new_vault(tmp_path)
    add_dataset(tmp_path, "u", legacy_rows())
    write_plan(tmp_path, LEGACY_ESTIMANDS, ivl={"ci": 0.95, "bootstrap": 300, "seed": 11})
    code, out = run_cli(tmp_path, "--units", "u")
    assert code == 0, out
    expected = (HERE / "testdata" / "estimate_legacy.json").read_text(encoding="utf-8")
    assert out == expected


# ---- variants (sensitivity) ------------------------------------------------

def test_variants_merge_their_filter_and_the_variant_wins():
    e = est(filter={"tipo": "org"}, by="g", variants=[{"id": "per", "filter": {"tipo": "per"}},
                                                      {"id": "org-a", "filter": {"g": "a"}}])
    r = estimate.compute(e, ROWS, IVL)
    assert r["value"] == pytest.approx(3 / 5) and [v["variant"] for v in r["variants"]] == ["per", "org-a"]
    per, org_a = r["variants"]
    assert per["value"] == pytest.approx(2 / 3) and per["n"] == 3 and "id" not in per
    assert org_a["value"] == 1.0 and org_a["n"] == 2 and [g["group"] for g in org_a["groups"]] == ["a"]
    plain = estimate.compute(est(filter={"tipo": "org"}, by="g"), ROWS, IVL)
    assert {k: v for k, v in r.items() if k != "variants"} == plain


# ---- several unit tables ---------------------------------------------------

def tables_vault(tmp_path):
    new_vault(tmp_path)
    sha_f = add_dataset(tmp_path, "unidades-ficheros", ROWS)
    repos = [{"repo_tipo": "org" if i % 2 else "per", "activo": "1" if i % 3 else "0"} for i in range(9)]
    sha_r = add_dataset(tmp_path, "unidades-repos", repos)
    write_plan(tmp_path, [est(), est(id="est-2", column="activo", units="repos")])
    return sha_f, sha_r


def test_several_tables_each_checked_and_listed(tmp_path):
    sha_f, sha_r = tables_vault(tmp_path)
    code, out = run_cli(tmp_path, "--units", "unidades-ficheros", "--units-for", "repos=unidades-repos")
    assert code == 0, out
    d = json.loads(out)
    assert list(d) == ["plan", "units_sha256", "tables", "estimates"] and d["units_sha256"] == sha_f
    assert d["tables"] == {"repos": {"dataset": "unidades-repos", "sha256": sha_r}}
    assert d["estimates"][1]["n"] == 9 and d["estimates"][1]["value"] == pytest.approx(6 / 9)


def test_tables_missing_unused_or_changed_are_refused(tmp_path):
    tables_vault(tmp_path)
    code, out = run_cli(tmp_path, "--units", "unidades-ficheros")
    assert code == 3 and "--units-for repos=" in json.loads(out)["error"]
    code, out = run_cli(tmp_path, "--units", "unidades-ficheros", "--units-for", "repos=unidades-repos",
                        "--units-for", "otra=unidades-repos")
    assert code == 3 and "'otra'" in json.loads(out)["error"]
    code, out = run_cli(tmp_path, "--units", "unidades-ficheros", "--units-for", "default=x")
    assert code == 3
    f = tmp_path / "Projects" / "demo" / "Datos" / "unidades-repos.csv"
    f.write_text(f.read_text(encoding="utf-8") + "org,1\n", encoding="utf-8")
    code, out = run_cli(tmp_path, "--units", "unidades-ficheros", "--units-for", "repos=unidades-repos")
    d = json.loads(out)
    assert code == 3 and d["tabla"] == "repos" and d["error"].startswith("tabla 'repos'")


def test_only_named_tables_needs_no_default(tmp_path):
    tables_vault(tmp_path)
    write_plan(tmp_path, [est(id="est-2", column="activo", units="repos")])
    code, out = run_cli(tmp_path, "--units-for", "repos=unidades-repos")
    assert code == 0, out
    assert list(json.loads(out)) == ["plan", "tables", "estimates"]
    code, out = run_cli(tmp_path, "--units", "unidades-ficheros", "--units-for", "repos=unidades-repos")
    assert code == 3 and "tabla por defecto" in json.loads(out)["error"]


def test_missing_column_names_the_table(tmp_path):
    tables_vault(tmp_path)
    write_plan(tmp_path, [est(), est(id="est-2", column="nope", units="repos")])
    code, out = run_cli(tmp_path, "--units", "unidades-ficheros", "--units-for", "repos=unidades-repos")
    assert code == 3 and "nope" in out and "'repos'" in out


@pytest.mark.parametrize("lines, spec", [
    (["units_dataset: unidades-a"], {"default": "unidades-a", "tables": {}}),
    (["units_dataset: <nombre>"], None),
    (["units_dataset:"], None),
    (["units_dataset: {default: unidades-a, repos: 'unidades-b'}"],
     {"default": "unidades-a", "tables": {"repos": "unidades-b"}}),
    (["units_dataset:", "  repos: unidades-b  # comentario", "  claves: unidades-c", "history: []"],
     {"default": None, "tables": {"repos": "unidades-b", "claves": "unidades-c"}}),
])
def test_parse_units_spec(lines, spec):
    assert estimate.parse_units_spec(["id: Q-9001", *lines]) == spec


def test_units_spec_helpers(tmp_path):
    q = tmp_path / "Q-9001.md"
    q.write_text("---\nid: Q-9001\nunits_dataset:\n  default: unidades-a\n  repos: unidades-b\n---\n\n# Q\n",
                 encoding="utf-8")
    spec = estimate.units_map_from_question(q)
    assert estimate.units_argv(spec) == ["--units", "unidades-a", "--units-for", "repos=unidades-b"]
    assert estimate.units_datasets(spec) == ["unidades-a", "unidades-b"]
    with pytest.raises(ValueError):
        estimate.parse_units_spec(["units_dataset: {repos: }"])


# ---- tabla, interseccion, tendencia, cuantiles, curve ----------------------

def test_tabla_counts_and_row_proportions():
    rows = [{"f": "a", "t": "x"}] * 3 + [{"f": "a", "t": "y"}] + [{"f": "b", "t": "y"}] * 2
    r = estimate.compute({"id": "t", "type": "tabla", "rows": "f", "cols": "t", "filter": {}}, rows, IVL)
    assert r["value"] is None and r["n"] == 6 and r["row_levels"] == ["a", "b"] and r["col_levels"] == ["x", "y"]
    assert r["row_totals"] == [4, 2] and r["col_totals"] == [3, 3]
    ax = r["cells"][0]
    assert (ax["row"], ax["col"], ax["count"], ax["p"]) == ("a", "x", 3, 0.75)
    assert (ax["lo"], ax["hi"]) == wilson(3, 4, 0.95)
    assert r["cells"][2]["count"] == 0 and r["cells"][2]["p"] == 0


@pytest.mark.parametrize("values, why", [
    ([f"v{i}" for i in range(60)], "máximo 50"),
    ([f"v{i}" for i in range(25)], "identificador"),
    (["0123456789abcdef0123"] * 4, "forma de identificador"),
    (["ana@example.org"] * 4, "forma de identificador"),
])
def test_tabla_refuses_identifier_columns_without_printing_them(values, why):
    rows = [{"f": v, "t": "x"} for v in values]
    with pytest.raises(estimate.Refused, match=why) as exc:
        estimate.compute({"id": "t", "type": "tabla", "rows": "f", "cols": "t", "filter": {}}, rows, IVL)
    assert values[-1] not in str(exc.value)


def test_interseccion_all_true_and_combinations():
    rows = ([{"a": "1", "b": "1", "c": "1"}] * 3 + [{"a": "1", "b": "1", "c": "0"}] * 2
            + [{"a": "0", "b": "0", "c": "0"}] * 5)
    e = {"id": "i", "type": "interseccion", "columns": ["a", "b", "c"], "filter": {}, "cluster": None}
    r = estimate.compute(e, rows, IVL)
    assert r["value"] == pytest.approx(0.3) and (r["lo"], r["hi"]) == wilson(3, 10, 0.95)
    assert r["marginals"] == {"a": 5, "b": 5, "c": 3}
    assert len(r["combinations"]) == 8 and sum(c["count"] for c in r["combinations"]) == 10
    assert r["combinations"][:3] == [{"true": [], "count": 5}, {"true": ["a", "b", "c"], "count": 3},
                                     {"true": ["a", "b"], "count": 2}]
    rows_c = [{**r_, "g": f"g{i % 4}"} for i, r_ in enumerate(rows)]
    one = estimate.compute({**e, "cluster": "g"}, rows_c, IVL)
    assert one == estimate.compute({**e, "cluster": "g"}, rows_c, IVL) and one["method"] == "cluster-bootstrap"


def test_tendencia_cochran_armitage():
    ks = {"2019": 1, "2020": 3, "2021": 6}
    rows = [{"y": yr, "x": "1" if i < k else "0"} for yr, k in ks.items() for i in range(10)] + [{"y": "", "x": "1"}]
    r = estimate.compute({"id": "tr", "type": "tendencia", "column": "x", "by": "y", "filter": {}}, rows, IVL)
    assert [g["group"] for g in r["groups"]] == ["2019", "2020", "2021"] and r["groups"][0]["score"] == 2019
    assert r["skipped"] == 1 and r["n"] == 30 and r["value"] == pytest.approx(0.25)
    # z^2 of Cochran-Armitage = N r^2 (Pearson r between score and outcome over the units)
    xs = [s for s in (0, 1, 2) for _ in range(10)]
    ys = [1 if i < k else 0 for k in ks.values() for i in range(10)]
    rr = statistics.correlation(xs, ys)
    assert r["z"] ** 2 == pytest.approx(30 * rr ** 2) and r["z"] > 0
    assert r["p_value"] == pytest.approx(2 * statistics.NormalDist().cdf(-r["z"]))
    assert r["lo"] < r["value"] < r["hi"]
    ordered = estimate.compute({"id": "tr", "type": "tendencia", "column": "x", "by": "y", "filter": {},
                                "order": ["2021", "2020", "2019"]}, rows, IVL)
    assert ordered["z"] == pytest.approx(-r["z"]) and ordered["groups"][0]["score"] == 0


def test_cuantiles_type7_with_bootstrap():
    rows = [{"v": str(v)} for v in range(1, 10)] + [{"v": "x"}]
    r = estimate.compute({"id": "q", "type": "cuantiles", "column": "v", "q": [0.25, 0.5], "filter": {}}, rows, IVL)
    assert r["value"] is None and r["n"] == 9 and r["skipped"] == 1
    q25, q50 = r["quantiles"]
    assert (q25["q"], q25["value"], q50["value"]) == (0.25, 3.0, 5.0)
    assert q50["lo"] <= 5 <= q50["hi"]
    assert estimate._quantile([1.0, 2.0], 0.5) == 1.5


def test_survival_curve_suppresses_small_risk_sets():
    rows = [{"d": str(t), "ev": "1" if t % 2 else "0"} for t in range(1, 11)]
    e = est(type="supervivencia", column="d", event="ev", at=[], curve=True)
    r = estimate.compute(e, rows, IVL)
    steps = estimate._km(sorted((float(t), t % 2 == 1) for t in range(1, 11)))
    assert [(c["t"], c["s"]) for c in r["curve"]] == steps[:3]
    assert [c["at_risk"] for c in r["curve"]] == [10, 8, 6]
    assert r["curve_suppressed"] == 2 and r["curve_min_at_risk"] == 5
    assert "curve" not in estimate.compute({**e, "curve": False}, rows, IVL)


# ---- families --------------------------------------------------------------

def test_newcombe_p_inverts_the_interval():
    p = estimate.newcombe_p(56, 70, 48, 80)
    lo, _hi = estimate.newcombe(56, 70, 48, 80, 1 - p)
    assert 0 < p < 0.05 and lo == pytest.approx(0, abs=1e-9)
    assert estimate.newcombe_p(5, 10, 10, 20) == 1.0


def test_holm_and_bonferroni():
    assert estimate._adjust([0.01, 0.04, 0.03], "holm") == pytest.approx([0.03, 0.06, 0.06])
    assert estimate._adjust([0.01, 0.04, 0.6], "bonferroni") == pytest.approx([0.03, 0.12, 1.0])


def test_family_adjusts_its_diferencias(tmp_path):
    new_vault(tmp_path)
    cut = {"a": 7, "b": 4, "c": 5}
    rows = [{"x": "1" if i % 10 < cut[f] else "0", "f": f, "s": f"s{i % 6}"} for f in "abc" for i in range(60)]
    add_dataset(tmp_path, "u", rows)
    d = {"type": "diferencia", "column": "x", "group": "f"}
    write_plan(tmp_path, [est(id="ab", a="a", b="b", **d), est(id="ac", a="a", b="c", cluster="s", **d),
                          est(id="solo", a="b", b="c", **d)],
               families=[{"id": "fam", "estimands": ["ab", "ac"], "method": "holm"}])
    code, out = run_cli(tmp_path, "--units", "u")
    assert code == 0, out
    ab, ac, solo = json.loads(out)["estimates"]
    assert "family" not in solo
    fa, fc = ab["family"], ac["family"]
    assert fa["p_method"] == "newcombe-inversion" and fc["p_method"] == "bootstrap-percentil"
    assert fa["m"] == 2 and fa["ci_adjusted"] == pytest.approx(0.975)
    assert [fa["p_adjusted"], fc["p_adjusted"]] == pytest.approx(estimate._adjust([fa["p"], fc["p"]], "holm"))
    assert fa["lo_adjusted"] <= ab["lo"] and fa["hi_adjusted"] >= ab["hi"]
    assert [fa["lo_adjusted"], fa["hi_adjusted"]] == pytest.approx(list(estimate.newcombe(42, 60, 24, 60, 0.975)))
    plain = estimate.compute(est(id="ab", a="a", b="b", **d), [{k: str(v) for k, v in r.items()} for r in rows], IVL)
    assert {k: v for k, v in ab.items() if k != "family"} == plain


# ---- tabla-latex -----------------------------------------------------------

def latex(tmp_path, result, *args):
    f = tmp_path / "salida.json"
    f.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    p = subprocess.run([sys.executable, str(SCRIPT), "tabla-latex", "--json", str(f), *args],
                       capture_output=True, text=True, encoding="utf-8")
    return p.returncode, p.stdout


def test_tabla_latex_prints_values_as_printed(tmp_path):
    r = estimate.compute(est(id="est_1", by="tipo", variants=[{"id": "per", "filter": {"tipo": "per"}}]), ROWS, IVL)
    code, out = latex(tmp_path, {"plan": "M-9002", "units_sha256": "0" * 64, "estimates": [r]}, "--id", "est_1")
    assert code == 0, out
    assert r"\toprule" in out and r"\bottomrule" in out and r"est\_1" in out and r"\label{tab:est_1}" in out
    assert f"[{r['lo']!r}, {r['hi']!r}]" in out and repr(r["value"]) in out
    assert r"est\_1 [per]" in out and "JSON sha256" in out
    code, out2 = latex(tmp_path, {"plan": "M-9002", "estimates": [r]}, "--id", "est_1", "--decimals", "2",
                       "--caption", "Tasa de x_1 (inventada)")
    assert code == 0 and f"{r['value']:.2f} & [{r['lo']:.2f}, {r['hi']:.2f}]" in out2 and r"x\_1" in out2


def test_tabla_latex_other_shapes_and_refusals(tmp_path):
    rows = [{"f": "a", "t": "x"}] * 3 + [{"f": "b", "t": "y"}]
    tab = estimate.compute({"id": "t", "type": "tabla", "rows": "f", "cols": "t", "filter": {}}, rows, IVL)
    q = estimate.compute({"id": "q", "type": "cuantiles", "column": "v", "q": [0.5], "filter": {}},
                         [{"v": str(v)} for v in range(9)], IVL)
    res = {"plan": "M-9002", "estimates": [tab, q]}
    code, out = latex(tmp_path, res, "--id", "t")
    assert code == 0 and "Total" in out and "3 (1.0 [" in out
    code, out = latex(tmp_path, res, "--id", "q")
    assert code == 0 and "q = 0.5 & 4.0" in out
    assert latex(tmp_path, res, "--id", "nope")[0] == 3
    assert latex(tmp_path, res, "--id", "t", "--variant", "v")[0] == 3
    assert latex(tmp_path, {"error": "x"}, "--id", "t")[0] == 3
