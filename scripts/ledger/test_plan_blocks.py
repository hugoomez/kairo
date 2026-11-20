"""Tests for plan_blocks (invented plans only)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

from plan_blocks import PlanError, read_plan  # noqa: E402

TEMPLATE = ROOT / "templates" / "measurement-plan-template.md"


def plan(tmp_path, est=None, cls=None, ivl=None, raw_est=None):
    est = est if est is not None else [
        {"id": "est-1", "type": "proporcion", "column": "c", "filter": {}, "cluster": None}]
    cls = cls if cls is not None else []
    ivl = ivl if ivl is not None else {"ci": 0.95, "bootstrap": 2000, "seed": 7}
    e = raw_est if raw_est is not None else json.dumps({"estimands": est})
    text = ("---\nid: M-9002\nfrozen_at: 2026-01-01T00:00:00Z\nfrozen_commit: abc123\n---\n\n"
            f"## Estimandos\n\n```json\n{e}\n```\n\n"
            f"## Clasificadores\n\n```json\n{json.dumps({'classifiers': cls})}\n```\n\n"
            f"## Intervalos\n\n```json\n{json.dumps(ivl)}\n```\n\n## Ética\n\nnada\n")
    p = tmp_path / "M-9002.md"
    p.write_text(text, encoding="utf-8")
    return p


def test_reads_template_example():
    r = read_plan(TEMPLATE)
    assert [e["id"] for e in r["estimands"]] == [f"est-{i}" for i in range(1, 12)]
    assert r["estimands"][2]["family"] == {"id": "fam-1", "method": "holm", "estimands": ["est-3", "est-7"]}
    assert r["estimands"][0]["filter"] == {"grupo": "a"}
    assert r["classifiers"][0]["min_recall_lo"] == 0.80
    assert r["intervals"] == {"ci": 0.95, "bootstrap": 2000, "seed": 12345}
    assert r["frozen_at"] and r["frozen_commit"]


def test_missing_block(tmp_path):
    p = plan(tmp_path)
    p.write_text(p.read_text(encoding="utf-8").replace("## Intervalos", "## Intervalos\n\nsin bloque\n\n## Otra", 1)
                 .replace("```json\n" + json.dumps({"ci": 0.95, "bootstrap": 2000, "seed": 7}) + "\n```", ""),
                 encoding="utf-8")
    with pytest.raises(PlanError, match="Intervalos"):
        read_plan(p)


def test_invalid_json_names_section(tmp_path):
    with pytest.raises(PlanError, match="Estimandos.*JSON inválido"):
        read_plan(plan(tmp_path, raw_est='{"estimands": [oops]}'))


def test_bad_type_rejected(tmp_path):
    with pytest.raises(PlanError, match="media"):
        read_plan(plan(tmp_path, est=[{"id": "a", "type": "media", "column": "c"}]))


def test_duplicate_estimand_id_rejected(tmp_path):
    e = {"id": "a", "type": "conteo", "column": "c"}
    with pytest.raises(PlanError, match="repetido"):
        read_plan(plan(tmp_path, est=[e, dict(e)]))


def test_null_cluster_and_empty_filter_ok(tmp_path):
    r = read_plan(plan(tmp_path, est=[{"id": "a", "type": "mediana", "column": "c", "filter": {}, "cluster": None}]))
    assert r["estimands"][0]["cluster"] is None and r["estimands"][0]["filter"] == {}


def test_classifier_bounds(tmp_path):
    bad = {"name": "k", "sample": "s", "min_precision_lo": 1.5, "min_recall_lo": 0.5, "label": "x", "positive": True}
    with pytest.raises(PlanError, match="Clasificadores"):
        read_plan(plan(tmp_path, cls=[bad]))
    with pytest.raises(PlanError, match="min_recall_lo"):
        read_plan(plan(tmp_path, cls=[{"name": "k", "sample": "s", "min_precision_lo": 0.5}]))


def test_classifier_pins_label_and_positive(tmp_path):
    ok = {"name": "k", "sample": "s", "min_precision_lo": 0.5, "min_recall_lo": 0.5, "label": "x", "positive": True}
    assert read_plan(plan(tmp_path, cls=[ok]))["classifiers"][0]["ci"] == 0.95
    assert read_plan(plan(tmp_path, cls=[{**ok, "ci": 0.9}]))["classifiers"][0]["ci"] == 0.9
    for drop in ("label", "positive"):
        bad = {k: v for k, v in ok.items() if k != drop}
        with pytest.raises(PlanError, match=f"k.*{drop}"):
            read_plan(plan(tmp_path, cls=[bad]))
    with pytest.raises(PlanError, match="ci"):
        read_plan(plan(tmp_path, cls=[{**ok, "ci": 1.2}]))


def test_cli(tmp_path):
    ok = subprocess.run([sys.executable, str(HERE / "plan_blocks.py"), "show", str(TEMPLATE)],
                        capture_output=True, text=True)
    assert ok.returncode == 0 and "estimands" in json.loads(ok.stdout)
    bad = subprocess.run([sys.executable, str(HERE / "plan_blocks.py"), "show", str(tmp_path / "no.md")],
                         capture_output=True, text=True)
    assert bad.returncode == 3 and "error" in json.loads(bad.stdout)


def test_bootstrap_must_be_positive(tmp_path):
    p = plan(tmp_path, ivl={"ci": 0.95, "bootstrap": 0, "seed": 7})
    with pytest.raises(PlanError, match="bootstrap"):
        read_plan(p)


@pytest.mark.parametrize("typ", ["conteo", "mediana"])
def test_cluster_only_on_proportions(tmp_path, typ):
    with pytest.raises(PlanError, match="Estimandos: a: cluster solo se admite en proporcion, diferencia e interseccion"):
        read_plan(plan(tmp_path, est=[{"id": "a", "type": typ, "column": "c", "filter": {}, "cluster": "g"}]))


def test_template_passes_plan_blocks_show():
    """FW-7/FW-10: the template's example goes through `plan_blocks show`, and its
    conteo counts a truthy 0/1 column, never an id column."""
    r = subprocess.run([sys.executable, str(HERE / "plan_blocks.py"), "show", str(TEMPLATE)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    est = {e["id"]: e for e in json.loads(r.stdout)["estimands"]}
    assert est["est-2"]["type"] == "conteo" and not est["est-2"]["column"].startswith("id")
    assert all(e["cluster"] is None for e in est.values()
               if e["type"] not in ("proporcion", "diferencia", "interseccion"))


def test_pilot_template_passes_plan_blocks_show():
    r = subprocess.run([sys.executable, str(HERE / "plan_blocks.py"), "show",
                        str(ROOT / "templates" / "pilot-plan-template.md")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


@pytest.mark.parametrize("extra, msg", [
    ({"type": "tabla", "column": None}, "'rows'"),
    ({"type": "tabla", "rows": "a", "cols": "a"}, "misma columna"),
    ({"type": "interseccion", "columns": ["a"]}, "entre 2 y 6"),
    ({"type": "interseccion", "columns": ["a", "a"]}, "entre 2 y 6"),
    ({"type": "tendencia"}, "'by'"),
    ({"type": "tendencia", "by": "y", "order": ["2020"]}, "'order'"),
    ({"type": "tendencia", "by": "y", "cluster": "g"}, "cluster solo"),
    ({"type": "cuantiles"}, "'q'"),
    ({"type": "cuantiles", "q": [0.5, 1]}, "'q'"),
    ({"type": "supervivencia", "event": "ev", "curve": "sí"}, "'curve'"),
    ({"units": "default"}, "omite 'units'"),
    ({"units": "con espacio"}, "'units'"),
    ({"variants": []}, "'variants'"),
    ({"variants": [{"id": "v", "filter": {}}]}, "'filter'"),
    ({"variants": [{"id": "v", "filter": {"a": 1}, "cluster": "g"}]}, "nada más"),
    ({"variants": [{"id": "v", "filter": {"a": 1}}, {"id": "v", "filter": {"b": 1}}]}, "repetida"),
    ({"family": {"id": "x"}}, "families"),
])
def test_extended_fields_are_validated(tmp_path, extra, msg):
    with pytest.raises(PlanError, match=msg):
        read_plan(plan(tmp_path, est=[_est(**extra)]))


def test_tabla_and_interseccion_need_no_column(tmp_path):
    r = read_plan(plan(tmp_path, est=[
        {"id": "t", "type": "tabla", "rows": "a", "cols": "b", "filter": {}, "cluster": None},
        {"id": "i", "type": "interseccion", "columns": ["a", "b"], "filter": {}, "cluster": "g"}]))
    assert [e["id"] for e in r["estimands"]] == ["t", "i"]


def _fam_plan(tmp_path, fams, extra=()):
    d = [_est(id="d1", type="diferencia", group="t", a="x", b="y"),
         _est(id="d2", type="diferencia", group="t", a="x", b="z"), *extra]
    return plan(tmp_path, raw_est=json.dumps({"estimands": d, "families": fams}))


@pytest.mark.parametrize("fams, msg", [
    ([], "families"),
    ([{"id": "f", "estimands": ["d1", "d2"], "method": "fdr"}], "method"),
    ([{"id": "f", "estimands": ["d1"], "method": "holm"}], "al menos dos"),
    ([{"id": "f", "estimands": ["d1", "nope"], "method": "holm"}], "no existe"),
    ([{"id": "f", "estimands": ["d1", "p"], "method": "holm"}], "solo agrupan"),
    ([{"id": "f", "estimands": ["d1", "d2"], "method": "holm"},
      {"id": "g", "estimands": ["d1", "d2"], "method": "holm"}], "dos familias"),
])
def test_families_are_validated(tmp_path, fams, msg):
    with pytest.raises(PlanError, match=msg):
        read_plan(_fam_plan(tmp_path, fams, extra=[_est(id="p")]))


def test_family_marks_its_members(tmp_path):
    r = read_plan(_fam_plan(tmp_path, [{"id": "f", "estimands": ["d1", "d2"], "method": "bonferroni"}]))
    assert [e.get("family", {}).get("method") for e in r["estimands"]] == ["bonferroni", "bonferroni"]


def _est(**kw):
    e = {"id": "e", "type": "proporcion", "column": "c", "filter": {}, "cluster": None}
    e.update(kw)
    return e


@pytest.mark.parametrize("extra, msg", [
    ({"type": "diferencia"}, "'group'"),
    ({"type": "diferencia", "group": "t", "a": "x"}, "'b'"),
    ({"type": "diferencia", "group": "t", "a": "x", "b": "x"}, "distintos"),
    ({"type": "reutilizacion"}, "'across'"),
    ({"type": "reutilizacion", "across": "c"}, "misma columna"),
    ({"type": "reutilizacion", "across": "r", "min_groups": 1}, "min_groups"),
    ({"type": "supervivencia"}, "'event'"),
    ({"type": "supervivencia", "event": "ev", "at": [-1]}, "'at'"),
    ({"type": "supervivencia", "event": "ev", "cluster": "g"}, "cluster solo"),
    ({"type": "diferencia", "group": "t", "a": "x", "b": "y", "by": "z"}, "'by' solo"),
    ({"by": "g", "cluster": "g"}, "misma columna"),
])
def test_new_types_need_their_fields(tmp_path, extra, msg):
    with pytest.raises(PlanError, match=msg):
        read_plan(plan(tmp_path, est=[_est(**extra)]))


def test_new_types_defaults(tmp_path):
    r = read_plan(plan(tmp_path, est=[
        _est(id="d", type="diferencia", group="t", a="x", b="y", cluster="org"),
        _est(id="r", type="reutilizacion", across="repo"),
        _est(id="s", type="supervivencia", event="ev", by="tool")]))
    d, ru, s = r["estimands"]
    assert ru["min_groups"] == 2 and s["at"] == [] and d["cluster"] == "org"
