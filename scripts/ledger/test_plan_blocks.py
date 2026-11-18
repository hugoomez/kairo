"""Tests for plan_blocks (invented plans only)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import plan_blocks  # noqa: E402
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
    assert [e["id"] for e in r["estimands"]] == ["est-1", "est-2"]
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
    bad = {"name": "k", "sample": "s", "min_precision_lo": 1.5, "min_recall_lo": 0.5}
    with pytest.raises(PlanError, match="Clasificadores"):
        read_plan(plan(tmp_path, cls=[bad]))
    with pytest.raises(PlanError, match="min_recall_lo"):
        read_plan(plan(tmp_path, cls=[{"name": "k", "sample": "s", "min_precision_lo": 0.5}]))


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
