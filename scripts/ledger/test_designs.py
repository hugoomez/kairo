"""Tests for the study-design registry (invented designs only)."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import designs  # noqa: E402

GOOD = """
[disenio]
prefix = "{prefix}"
folder = "Cosas"
statuses = ["a", "b", "c"]
terminal = ["c"]
transitions = [["a", "b"], {extra}["b", "c"]]
researcher_only = [["a", "b"]]
publishable = "c"
gate = [{gate}]
plan_prefix = "M"
plan_folder = "Mediciones"
plan_template = "templates/measurement-plan-template.md"
manuscript_sections = [{{id = "results", kind = "resultado"}}]
"""


def make_root(tmp_path, prefix="Z", extra="", gate='"plan_frozen"'):
    (tmp_path / "config").mkdir()
    (tmp_path / "templates").mkdir()
    shutil.copy(ROOT / "templates" / "measurement-plan-template.md", tmp_path / "templates")
    (tmp_path / "config" / "designs.toml").write_text(
        GOOD.format(prefix=prefix, extra=extra, gate=gate), encoding="utf-8")
    return tmp_path


def run_check(root):
    return subprocess.run([sys.executable, str(HERE / "designs.py"), "check", "--root", str(root)],
                          capture_output=True, text=True)


def test_load_medicion():
    reg = designs.load(ROOT)
    d = reg["medicion"]
    assert d["publishable"] == "estimada"
    assert ("en_recoleccion", "estimada") in d["transitions"]
    # starting the collection is the researcher's action (R12)
    assert ("plan_congelado", "en_recoleccion") in d["researcher_only"]
    # «sin respuesta» is the researcher's conclusion, never the agent's (FW-9)
    assert ("en_recoleccion", "sin_respuesta") in d["researcher_only"]
    assert d["prefix"] == "Q" and d["plan_prefix"] == "M"
    assert designs.design_for_plan_id(reg, "M-9002") is d
    assert designs.design_for_plan_id(reg, "Q-9001") is None
    assert designs.design_for_id(reg, "Q-9001") is d
    assert designs.design_for_id(reg, "H-0001") is None


def test_real_registry_checks_clean():
    r = subprocess.run([sys.executable, str(HERE / "designs.py"), "check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_valid_temp_registry_ok(tmp_path):
    assert run_check(make_root(tmp_path)).returncode == 0


def test_unknown_gate_and_state_rejected(tmp_path):
    root = make_root(tmp_path, extra='["b", "fantasma"], ', gate='"no_existe"')
    r = run_check(root)
    assert r.returncode == 3
    assert "no_existe" in r.stdout and "fantasma" in r.stdout


def test_prefix_collision_rejected(tmp_path):
    for p in ("H", "C", "E", "P"):
        sub = tmp_path / p
        sub.mkdir()
        r = run_check(make_root(sub, prefix=p))
        assert r.returncode == 3, p
        assert "prefijo" in r.stdout


def test_duplicate_prefix_and_missing_template(tmp_path):
    root = make_root(tmp_path)
    toml = root / "config" / "designs.toml"
    text = toml.read_text(encoding="utf-8")
    second = text.replace("[disenio]", "[otro]").replace("templates/measurement-plan-template.md", "templates/nada.md")
    toml.write_text(text + second, encoding="utf-8")
    r = run_check(root)
    assert r.returncode == 3
    assert "repetido" in r.stdout and "nada.md" in r.stdout


def test_templates_exist():
    for name, d in designs.load(ROOT).items():
        p = ROOT / d["plan_template"]
        assert p.is_file(), name
        fm = p.read_text(encoding="utf-8").split("---")[1]
        assert re.search(r"^frozen_at:", fm, re.M) and re.search(r"^frozen_commit:", fm, re.M)
    q = (ROOT / "templates" / "question-template.md").read_text(encoding="utf-8")
    for h in ("## Pregunta", "## Población y marco", "## Estimandos", "## Resultado", "## Historia"):
        assert h in q


def test_show_is_json():
    r = subprocess.run([sys.executable, str(HERE / "designs.py"), "show"], capture_output=True, text=True)
    assert r.returncode == 0 and "medicion" in json.loads(r.stdout)
