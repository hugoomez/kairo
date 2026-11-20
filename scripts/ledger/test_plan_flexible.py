"""Tests for plan amendments (freeze_plan.py amend, plan_blocks effective blocks, the
plan_frozen gate, estimate.py deviations, the --question packet), the `exploratoria` design
(QE/ME, citable with the label «exploratorio») and the `avisos_enviados` gate variant chosen
by the plan. Invented vault only (PROJ-901, Q-9101, M-9102, QE-9103, ME-9104), written
under tmp_path in a real git repo (vault_fixture.StudyVault)."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "analysis"))
sys.path.insert(0, str(HERE.parent / "manuscript"))

import designs  # noqa: E402
import estimate  # noqa: E402
import freeze_plan as fp  # noqa: E402
import manuscript as ms  # noqa: E402
import next_steps as ns  # noqa: E402
import plan_blocks as pb  # noqa: E402
import study_gates as sg  # noqa: E402
import study_status as ss  # noqa: E402
import verifier_packet  # noqa: E402
from vault_fixture import STUDY_ESTIMANDS, STUDY_INTERVALS, StudyVault  # noqa: E402

CLEAN = ("verifications:\n  - date: 2031-02-01\n    verifier: kairo/fresh-verifier@1.1.0\n"
         "    model: t\n    verdict: no_errors_found\n    scope: note\n")
VERIF_BODY = ("\n## Verificación independiente\n\n### 2031-02-01 — kairo/fresh-verifier@1.1.0 (note)\n\n"
              "- modelo: t\n- veredicto: no_errors_found\n- alcance: note\n- paquete sha256: {sha}\n"
              "- hallazgos: ninguno\n")
PLAN = "Projects/demo/Mediciones/M-9102.md"
EST2 = {"id": "est-2", "type": "conteo", "column": "marca", "filter": {"grupo": "b"}, "cluster": None}


@pytest.fixture(autouse=True)
def _no_agent(monkeypatch):
    for m in ss.AGENT_MARKERS:
        monkeypatch.delenv(m, raising=False)


def set_units(path: Path, units: str = "unidades-demo") -> None:
    text = path.read_text(encoding="utf-8")
    path.write_text(re.sub(r"^units_dataset:.*$", f"units_dataset: {units}", text, count=1, flags=re.M),
                    encoding="utf-8")


def frozen_study(tmp_path, intervals=None) -> StudyVault:
    """Q-9101 frozen with M-9102, then the units registered (and committed) after the freeze."""
    sv = StudyVault(tmp_path / "vault")
    sv.plan(intervals=intervals)
    sv.question()
    fp.freeze(sv.root, "Q-9101", "tester")
    sv.register_units()
    sv.commit("datos", "Projects/demo/Datos/registro.json", "Projects/demo/Datos/u.csv")
    q = sv.proj / "Preguntas" / "Q-9101.md"
    set_units(q)
    sv.commit("units_dataset", sv.rel(q))
    return sv


def change(tmp_path, data, name="cambio.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
    return p


def amend(sv, tmp_path, data, dv="si", motivo="motivo inventado de la enmienda"):
    return fp.amend(sv.root, "M-9102", "tester", motivo, dv, change(tmp_path, data))


def q_path(sv) -> Path:
    return sv.proj / "Preguntas" / "Q-9101.md"


def plan_path(sv) -> Path:
    return sv.root / PLAN


# ---- plan_blocks: effective blocks -----------------------------------------------

def _entry(eid, delta, dv="si", commit="abc1234"):
    return (f"### {eid}\n\n- fecha: 2031-02-03T10:00:00Z\n- por: tester\n- datos_vistos: {dv}\n"
            f"- motivo: inventado\n- commit: {commit}\n\n```json\n{json.dumps(delta)}\n```\n\n")


def _plan_text(*entries) -> str:
    sv_plan = (HERE.parent.parent / "templates" / "measurement-plan-template.md").read_text(encoding="utf-8")
    return sv_plan + ("\n## Enmiendas\n\nRegistro.\n\n" + "".join(entries) if entries else "")


def test_effective_blocks_apply_amendments_in_order():
    text = _plan_text(_entry("E1", {"estimands": [EST2], "retire_estimands": ["est-4"]}, dv="no"),
                      _entry("E2", {"intervals": {"seed": 7}, "retire_classifiers": ["clasif-1"]}))
    base = pb.read_plan_text(text)
    eff = pb.read_effective_text(text)
    ids = [e["id"] for e in base["estimands"]]
    assert ids[:5] == ["est-1", "est-2", "est-3", "est-4", "est-5"]   # the template may list more
    assert [e["id"] for e in eff["estimands"]] == [i for i in ids if i != "est-4"]
    assert next(e for e in eff["estimands"] if e["id"] == "est-2") == EST2
    assert eff["intervals"]["seed"] == 7 and eff["classifiers"] == []
    # the seed change touches every estimand; est-2 was changed by both
    assert eff["deviations"]["est-2"] == [{"enmienda": "E1", "datos_vistos": "no"},
                                          {"enmienda": "E2", "datos_vistos": "si"}]
    assert eff["deviations"]["est-1"] == [{"enmienda": "E2", "datos_vistos": "si"}]
    assert eff["summary"][0]["estimandos_retirados"] == ["est-4"]
    assert eff["summary"][1]["clasificadores_cambiados"] == ["clasif-1"]


def test_unamended_effective_equals_own_blocks():
    text = _plan_text()
    eff = pb.read_effective_text(text)
    assert {k: eff[k] for k in ("estimands", "classifiers", "intervals")} == \
        {k: v for k, v in pb.read_plan_text(text).items() if k in ("estimands", "classifiers", "intervals")}
    assert eff["amendments"] == [] and eff["deviations"] == {}


@pytest.mark.parametrize("entries, msg", [
    ((_entry("E2", {"intervals": {"seed": 1}}),), "se esperaba la entrada E1"),
    ((_entry("E1", {"retire_estimands": ["nada"]}),), "no existe"),
    ((_entry("E1", {"otra": 1}),), "claves desconocidas"),
    ((_entry("E1", {"intervals": {"ci": 3}}),), "con E1 el plan no es válido"),
    ((_entry("E1", {"intervals": {"seed": 1}}, dv="quizas"),), "datos_vistos"),
])
def test_bad_amendments_are_rejected(entries, msg):
    with pytest.raises(pb.PlanError, match=msg):
        pb.read_effective_text(_plan_text(*entries))


def test_avisos_choice_is_validated():
    text = _plan_text().replace('{"ci": 0.95, "bootstrap": 2000, "seed": 12345}',
                                '{"ci": 0.95, "bootstrap": 2000, "seed": 12345, "avisos": "nunca"}')
    with pytest.raises(pb.PlanError, match="avisos"):
        pb.read_plan_text(text)
    ok = pb.read_plan_text(text.replace('"nunca"', '"enviados"'))
    assert pb.avisos_mode(ok) == "enviados"
    assert pb.avisos_mode(pb.read_plan_text(_plan_text())) == "cerrados"


def test_diff_blocks():
    cur = pb.blocks_of_body(_plan_text().split("\n---\n", 1)[1])
    new = json.loads(json.dumps(cur))
    new["estimands"] = [e for e in new["estimands"] if e["id"] != "est-4"]
    new["estimands"][0]["filter"] = {"grupo": "b"}
    new["intervals"]["seed"] = 99
    d = pb.diff_blocks(cur, new)
    assert d["retire_estimands"] == ["est-4"] and [e["id"] for e in d["estimands"]] == ["est-1"]
    assert d["intervals"] == {"seed": 99} and "classifiers" not in d
    assert pb.diff_blocks(cur, cur) == {}


# ---- freeze_plan.py amend + plan_frozen ------------------------------------------

def test_amend_appends_an_entry_in_two_commits_and_the_gate_passes(tmp_path):
    sv = frozen_study(tmp_path)
    before = pb.read_plan(plan_path(sv))
    out = amend(sv, tmp_path, {"estimands": [EST2]})
    assert out["enmienda"] == "E1" and out["datos_vistos"] == "si"
    assert out["cambios"]["estimandos_cambiados"] == ["est-2"]
    # the frozen blocks are untouched; the amendment is appended
    assert pb.read_plan(plan_path(sv)) == before
    eff = pb.read_effective(plan_path(sv))
    assert [e["id"] for e in eff["estimands"]] == ["est-1", "est-2"]
    assert eff["amendments"][0]["commit"] == out["commit"]
    # commit 1 holds the entry (commit: pendiente), commit 2 writes its sha; both touch only the plan
    assert sv.git("rev-parse", "HEAD~1") == out["commit"]
    assert "- commit: pendiente" in sv.git("show", f"{out['commit']}:{PLAN}")
    for c in out["commits"]:
        assert sv.git("show", "--name-only", "--format=", c).splitlines() == [PLAN]
    assert sv.git("status", "--porcelain") == ""
    assert sg.plan_frozen(sv.root, q_path(sv), sg.read_note(q_path(sv))[0]) == []


def test_amend_from_a_plan_copy_computes_the_change(tmp_path):
    sv = frozen_study(tmp_path)
    copy = plan_path(sv).read_text(encoding="utf-8").replace('"seed": 11', '"seed": 5')
    out = fp.amend(sv.root, "M-9102", "tester", "semilla corregida", "no", change(tmp_path, copy, "plan.md"))
    entry = pb.read_effective(plan_path(sv))["amendments"][0]
    assert entry["delta"] == {"intervals": {"seed": 5}} and entry["datos_vistos"] == "no"
    assert out["cambios"]["estimandos_cambiados"] == ["est-1"]


def test_cli_amend_and_required_flags(tmp_path, capsys):
    sv = frozen_study(tmp_path)
    src = change(tmp_path, {"estimands": [EST2]})
    with pytest.raises(SystemExit):
        fp.main(["amend", "--vault", str(sv.root), "--plan", "M-9102", "--by", "tester",
                 "--motivo", "un motivo", "--from", str(src)])          # no --datos-vistos
    capsys.readouterr()
    code = fp.main(["amend", "--vault", str(sv.root), "--plan", "M-9102", "--by", "tester", "--motivo",
                    "un motivo inventado", "--datos-vistos", "si", "--from", str(src)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["enmienda"] == "E1"


def test_editing_a_past_amendment_fails_the_gate(tmp_path):
    sv = frozen_study(tmp_path)
    amend(sv, tmp_path, {"estimands": [EST2]})
    p = plan_path(sv)
    p.write_text(p.read_text(encoding="utf-8").replace('"grupo": "b"', '"grupo": "a"'), encoding="utf-8")
    sv.commit("retoque", PLAN)
    lines = sg.plan_frozen(sv.root, q_path(sv), sg.read_note(q_path(sv))[0])
    assert any("la enmienda E1 de M-9102 cambió después de registrarla" in x for x in lines)


def test_editing_datos_vistos_of_a_past_amendment_fails_the_gate(tmp_path):
    sv = frozen_study(tmp_path)
    amend(sv, tmp_path, {"estimands": [EST2]})
    p = plan_path(sv)
    p.write_text(p.read_text(encoding="utf-8").replace("datos_vistos: si", "datos_vistos: no"), encoding="utf-8")
    lines = sg.plan_frozen(sv.root, q_path(sv), sg.read_note(q_path(sv))[0])
    assert any("cambió después de registrarla" in x for x in lines)


def test_removing_an_amendment_fails_the_gate(tmp_path):
    sv = frozen_study(tmp_path)
    amend(sv, tmp_path, {"estimands": [EST2]})
    p = plan_path(sv)
    p.write_text(p.read_text(encoding="utf-8").split("\n## Enmiendas")[0] + "\n", encoding="utf-8")
    sv.commit("sin enmiendas", PLAN)
    lines = sg.plan_frozen(sv.root, q_path(sv), sg.read_note(q_path(sv))[0])
    assert any("la enmienda E1 del plan M-9102 se borró" in x for x in lines)


def test_repointing_an_amendment_to_a_later_commit_fails(tmp_path):
    sv = frozen_study(tmp_path)
    out = amend(sv, tmp_path, {"estimands": [EST2]})
    p = plan_path(sv)
    head = sv.git("rev-parse", "HEAD")      # the second commit: E1 already there
    p.write_text(p.read_text(encoding="utf-8").replace(out["commit"], head), encoding="utf-8")
    lines = sg.plan_frozen(sv.root, q_path(sv), sg.read_note(q_path(sv))[0])
    assert any("ya estaba antes de" in x for x in lines)


def test_datos_vistos_no_after_the_data_fails_the_gate(tmp_path):
    sv = frozen_study(tmp_path)
    amend(sv, tmp_path, {"estimands": [EST2]}, dv="no")
    lines = sg.plan_frozen(sv.root, q_path(sv), sg.read_note(q_path(sv))[0])
    assert any("declara datos_vistos: no, pero el dataset de unidades ya estaba registrado" in x for x in lines)


def test_datos_vistos_no_before_the_data_passes(tmp_path):
    sv = StudyVault(tmp_path / "vault")
    sv.plan()
    sv.question()
    fp.freeze(sv.root, "Q-9101", "tester")
    amend(sv, tmp_path, {"estimands": [EST2]}, dv="no")
    sv.register_units()
    sv.commit("datos", "Projects/demo/Datos/registro.json", "Projects/demo/Datos/u.csv")
    set_units(q_path(sv))
    assert sg.plan_frozen(sv.root, q_path(sv), sg.read_note(q_path(sv))[0]) == []


def test_a_second_amendment_is_e2_and_both_verify(tmp_path):
    sv = frozen_study(tmp_path)
    amend(sv, tmp_path, {"estimands": [EST2]})
    out = amend(sv, tmp_path, {"intervals": {"seed": 3}}, motivo="otra semilla inventada")
    assert out["enmienda"] == "E2"
    assert sg.plan_frozen(sv.root, q_path(sv), sg.read_note(q_path(sv))[0]) == []


@pytest.mark.parametrize("what, msg", [
    ("noop", "no cambia nada"),
    ("empty", "vacío"),
    ("short", "--motivo"),
    ("dv", "--datos-vistos"),
    ("dirty", "cambios sin commit"),
    ("retire", "no existe"),
])
def test_amend_refusals_write_nothing(tmp_path, what, msg):
    sv = frozen_study(tmp_path)
    p = plan_path(sv)
    if what == "dirty":
        p.write_text(p.read_text(encoding="utf-8").replace('"seed": 11', '"seed": 12'), encoding="utf-8")
    before = p.read_text(encoding="utf-8")
    head = sv.git("rev-parse", "HEAD")
    data = {"noop": {"intervals": {"seed": 11}}, "empty": "{}", "retire": {"retire_estimands": ["nada"]}}.get(
        what, {"estimands": [EST2]})
    with pytest.raises(fp.Refused, match=re.escape(msg)):
        fp.amend(sv.root, "M-9102", "tester", "ab" if what == "short" else "motivo inventado",
                 "tal vez" if what == "dv" else "si", change(tmp_path, data))
    assert p.read_text(encoding="utf-8") == before and sv.git("rev-parse", "HEAD") == head


def test_amend_refuses_an_unfrozen_plan(tmp_path):
    sv = StudyVault(tmp_path / "vault")
    sv.plan()
    sv.question()
    with pytest.raises(fp.Refused, match="no está congelado"):
        amend(sv, tmp_path, {"estimands": [EST2]})


def test_amend_refuses_a_terminal_question(tmp_path):
    sv = frozen_study(tmp_path)
    q = q_path(sv)
    q.write_text(q.read_text(encoding="utf-8").replace("status: plan_congelado", "status: descartada"),
                 encoding="utf-8")
    with pytest.raises(fp.Refused, match="descartada"):
        amend(sv, tmp_path, {"estimands": [EST2]})


def test_freeze_refuses_a_plan_with_amendments(tmp_path):
    sv = StudyVault(tmp_path / "vault")
    p = sv.plan()
    p.write_text(p.read_text(encoding="utf-8") + "\n## Enmiendas\n\n" + _entry("E1", {"intervals": {"seed": 1}}),
                 encoding="utf-8")
    sv.question()
    with pytest.raises(fp.Refused, match="Enmiendas"):
        fp.freeze(sv.root, "Q-9101", "tester")


def test_a_failed_second_commit_is_finished_with_reanudar(tmp_path, monkeypatch):
    sv = frozen_study(tmp_path)
    real, calls = fp._commit, []

    def flaky(top, paths, msg):
        calls.append(msg)
        if len(calls) == 2:
            raise fp.GitError("git commit falló: hook de prueba")
        return real(top, paths, msg)

    monkeypatch.setattr(fp, "_commit", flaky)
    with pytest.raises(fp.GitError):
        amend(sv, tmp_path, {"estimands": [EST2]})
    monkeypatch.setattr(fp, "_commit", real)
    with pytest.raises(fp.Refused, match="cambios sin commit|no terminó"):
        amend(sv, tmp_path, {"intervals": {"seed": 4}})
    out = fp.amend(sv.root, "M-9102", "", "", "", None, resume=True)
    assert out["reanudado"] is True and out["enmienda"] == "E1"
    assert sv.git("status", "--porcelain") == ""
    assert sg.plan_frozen(sv.root, q_path(sv), sg.read_note(q_path(sv))[0]) == []


def test_a_pending_amendment_fails_the_gate(tmp_path):
    sv = frozen_study(tmp_path)
    out = amend(sv, tmp_path, {"estimands": [EST2]})
    p = plan_path(sv)
    p.write_text(p.read_text(encoding="utf-8").replace(f"commit: {out['commit']}", "commit: pendiente"),
                 encoding="utf-8")
    lines = sg.plan_frozen(sv.root, q_path(sv), sg.read_note(q_path(sv))[0])
    assert any("pendiente" in x and "--reanudar" in x for x in lines)


# ---- estimate.py labels, gate output, packet --------------------------------------

def test_estimate_labels_changed_estimands_and_keeps_unamended_output(tmp_path):
    sv = frozen_study(tmp_path)
    before = estimate.run(sv.root, "M-9102", "unidades-demo")
    assert set(before) == {"plan", "units_sha256", "estimates"}
    assert all("desviacion" not in e for e in before["estimates"])
    amend(sv, tmp_path, {"estimands": [EST2]})
    after = estimate.run(sv.root, "M-9102", "unidades-demo")
    assert after["estimates"][0] == before["estimates"][0]          # est-1 untouched, byte for byte
    assert after["estimates"][1]["desviacion"] == [{"enmienda": "E1", "datos_vistos": "si"}]
    assert after["enmiendas"][0]["estimandos_cambiados"] == ["est-2"]
    assert json.dumps(after["estimates"][0]) == json.dumps(before["estimates"][0])


def test_gate_output_and_packet_list_the_amendments(tmp_path):
    sv = frozen_study(tmp_path)
    p0, _ = verifier_packet.build_question(str(sv.root), "Q-9101")
    g0 = ss.gate(sv.root, "Q-9101")
    assert "Enmiendas" not in p0 and "enmiendas" not in g0 and "etiqueta" not in g0
    out = amend(sv, tmp_path, {"estimands": [EST2]})
    g = ss.gate(sv.root, "Q-9101")
    e1 = g["enmiendas"][0]
    assert e1["id"] == "E1" and e1["datos_vistos"] == "si" and e1["commit"] == out["commit"]
    assert e1["estimandos_cambiados"] == ["est-2"] and e1["motivo"] == "motivo inventado de la enmienda"
    packet, manifest = verifier_packet.build_question(str(sv.root), "Q-9101")
    assert "### Enmiendas (registro append-only" in packet and "desviación declarada" in packet
    assert "- datos_vistos: si" in packet and out["commit"] in packet
    plan_src = next(s for s in manifest["sources"] if s["role"] == "plan")
    assert "Enmiendas" in plan_src["included_sections"]


# ---- exploratoria (QE / ME) --------------------------------------------------------

def exploratory(tmp_path, notices=("resuelto",), verified=True, intervals=None) -> StudyVault:
    """QE-9103 with plan ME-9104 over a dataset registered BEFORE its plan existed (and that a
    confirmatory question could also use)."""
    sv = StudyVault(tmp_path / "vault")
    sv.register_units()
    sv.commit("datos primero")
    sv.plan("ME-9104", "QE-9103", estimands=STUDY_ESTIMANDS + [EST2], intervals=intervals)
    sv.notices(*notices)
    out = json.dumps(estimate.run(sv.root, "ME-9104", "unidades-demo"), ensure_ascii=False, indent=2)
    res = f"```estimate-json\n{out}\n```"
    kw = dict(qid="QE-9103", design="exploratoria", plan="ME-9104", units="unidades-demo", resultado=res)
    p = sv.question(**kw)
    if verified:
        packet, _ = verifier_packet.build_question(str(sv.root), "QE-9103")
        sha = hashlib.sha256(packet.encode("utf-8")).hexdigest()
        sv.question(**kw, verif=CLEAN, extra_body=VERIF_BODY.format(sha=sha))
    sv.commit("exploratoria")
    assert p.is_file()
    return sv


def test_exploratory_design_is_registered():
    reg = designs.load()
    d = reg["exploratoria"]
    assert (d["prefix"], d["plan_prefix"], d["folder"], d["plan_folder"]) == ("QE", "ME", "Exploratorias", "Exploratorias")
    assert d["citable"] is True and d["etiqueta"] == "exploratorio"
    assert "plan_frozen" not in d["gate"] and ("abierta", "plan_congelado") not in set(d["transitions"])
    assert designs.problems() == []


def test_etiqueta_needs_a_citable_design(tmp_path):
    root = tmp_path / "plugin"
    (root / "config").mkdir(parents=True)
    src = (HERE.parents[1] / "config" / "designs.toml").read_text(encoding="utf-8")
    (root / "config" / "designs.toml").write_text(
        src.replace("citable = false\n", 'citable = false\netiqueta = "x"\n'), encoding="utf-8")
    shutil.copytree(HERE.parents[1] / "templates", root / "templates")
    assert any("etiqueta solo tiene sentido" in p for p in designs.problems(root))


def test_manuscript_shows_a_declared_deviation(tmp_path):
    sv = frozen_study(tmp_path)
    amend(sv, tmp_path, {"estimands": [EST2]})
    st = ms.dep_state(sv.root, "Q-9101")
    assert "desviación E1 (datos_vistos: si)" in st["labels"] and "etiqueta" not in st


def test_exploratory_reaches_estimada_from_abierta_with_data_registered_first(tmp_path):
    sv = exploratory(tmp_path)
    g = ss.gate(sv.root, "QE-9103")
    assert g["ok"], g["missing"]
    assert [c["name"] for c in g["checks"]] == ["estimates_reproducible", "classifiers_validated",
                                                "verifier_no_errors", "avisos_cerrados"]
    assert g["etiqueta"] == "exploratorio"
    packet, _ = verifier_packet.build_question(str(sv.root), "QE-9103")
    assert "Etiqueta del diseño: exploratorio" in packet and "Plan exploratorio (sin congelar)" in packet
    out = ss.apply(sv.root, "QE-9103", "estimada", "tester", "puerta exploratoria")
    assert out["to"] == "estimada"


def test_exploratory_cannot_be_frozen(tmp_path):
    sv = exploratory(tmp_path, verified=False)
    with pytest.raises(fp.Refused, match="transición"):
        fp.freeze(sv.root, "QE-9103", "tester")
    with pytest.raises(fp.Refused, match="no se congelan"):
        fp.amend(sv.root, "ME-9104", "tester", "motivo inventado", "si", change(tmp_path, {"intervals": {"seed": 1}}))


def test_exploratory_hand_edited_numbers_fail(tmp_path):
    sv = exploratory(tmp_path, verified=False)
    q = sv.proj / "Exploratorias" / "QE-9103.md"
    q.write_text(re.sub(r'"value": [0-9.]+', '"value": 0.99', q.read_text(encoding="utf-8"), count=1),
                 encoding="utf-8")
    g = ss.gate(sv.root, "QE-9103")
    assert not g["ok"] and any("no coincide" in x for x in g["missing"])


def test_next_steps_for_an_exploratory_question(tmp_path):
    sv = exploratory(tmp_path)
    res = ns.analyse(sv.root, "demo", collections=tmp_path / "sin-recolecciones")[0]
    kinds = {i["kind"]: i for i in res["items"]}
    assert "exploratoria_por_cerrar" in kinds and "exploratorio" in kinds["exploratoria_por_cerrar"]["action"]
    assert "plan_sin_congelar" not in kinds
    q = sv.proj / "Exploratorias" / "QE-9103.md"
    q.write_text(q.read_text(encoding="utf-8").split("## Resultado")[0] + "## Resultado\n\n<vacío>\n",
                 encoding="utf-8")
    kinds = {i["kind"] for i in ns.analyse(sv.root, "demo", collections=tmp_path / "sin-recolecciones")[0]["items"]}
    assert "exploratoria_sin_estimar" in kinds


# ---- manuscript: the label is mechanical -----------------------------------------

def test_manuscript_binds_an_exploratory_question_only_with_its_label(tmp_path):
    sv = exploratory(tmp_path)
    ss.apply(sv.root, "QE-9103", "estimada", "tester", "puerta exploratoria")
    ms.init(sv.proj, "t1", "Un paper inventado", None, design="medicion")
    with pytest.raises(ms.Refused, match="exploratorio"):
        ms.bind(sv.proj, "t1", "results", "QE-9103")
    ms.init(sv.proj, "t2", "Otro paper inventado", None, design="exploratoria")
    assert ms.bind(sv.proj, "t2", "exploratory-results", "QE-9103") == ["QE-9103"]
    cov = ms.coverage(sv.root, sv.proj, "t2")
    sec = next(s for s in cov["sections"] if s["id"] == "exploratory-results")
    assert sec["ready"] and sec["deps"][0]["etiqueta"] == "exploratorio"
    assert "QE-9103 ✓ «exploratorio»" in ms.render_coverage(cov)
    with pytest.raises(ms.Refused, match="must say so"):
        ms.write_section(sv.root, sv.proj, "t2", "exploratory-results", "La proporción es 0,6.")
    ms.write_section(sv.root, sv.proj, "t2", "exploratory-results", "Resultado exploratorio: la proporción es 0,6.")


# ---- avisos_enviados and the plan's choice -----------------------------------------

def _embargo(days: int) -> dict:
    return {"estado": "enviado", "embargo_hasta": (date.today() + timedelta(days=days)).isoformat()}


@pytest.mark.parametrize("notices, closed_ok, sent_ok", [
    (("resuelto", "respondido"), True, True),
    ((_embargo(30),), False, True),
    ((_embargo(-1),), True, True),
    (("pendiente",), False, False),
    (({"estado": "enviado", "embargo_hasta": ""},), False, False),
    (("raro",), False, False),
])
def test_avisos_enviados_vs_cerrados(tmp_path, notices, closed_ok, sent_ok):
    sv = StudyVault(tmp_path / "vault")
    sv.notices(*notices)
    q = sv.question()
    assert (sg.avisos_cerrados(sv.root, q, {}) == []) is closed_ok
    assert (sg.avisos_enviados(sv.root, q, {}) == []) is sent_ok


def test_the_plan_chooses_avisos_enviados(tmp_path):
    sv = exploratory(tmp_path, notices=(_embargo(30),), intervals={**STUDY_INTERVALS, "avisos": "enviados"})
    g = ss.gate(sv.root, "QE-9103")
    assert g["ok"], g["missing"]
    assert g["checks"][-1]["name"] == "avisos_enviados"
    sv2 = exploratory(tmp_path / "b", notices=(_embargo(30),))
    g2 = ss.gate(sv2.root, "QE-9103")
    assert g2["checks"][-1]["name"] == "avisos_cerrados" and not g2["checks"][-1]["ok"]


def test_avisos_choice_changes_only_by_amendment(tmp_path):
    sv = frozen_study(tmp_path)
    q = q_path(sv)
    p = plan_path(sv)
    frozen = p.read_text(encoding="utf-8")
    p.write_text(frozen.replace('"seed": 11}', '"seed": 11, "avisos": "enviados"}'), encoding="utf-8")
    assert sg.resolve_check("avisos_cerrados", q, sg.read_note(q)[0]) == "avisos_enviados"
    assert "el plan cambió después de congelarlo: Intervalos" in sg.plan_frozen(sv.root, q, sg.read_note(q)[0])
    p.write_text(frozen, encoding="utf-8")
    assert sg.resolve_check("avisos_cerrados", q, sg.read_note(q)[0]) == "avisos_cerrados"
    out = amend(sv, tmp_path, {"intervals": {"avisos": "enviados"}},
                motivo="la publicación solo da agregados")
    assert out["cambios"]["intervalos_cambiados"] is True and out["cambios"]["estimandos_cambiados"] == []
    assert sg.resolve_check("avisos_cerrados", q, sg.read_note(q)[0]) == "avisos_enviados"
    assert sg.plan_frozen(sv.root, q, sg.read_note(q)[0]) == []
