"""Tests for the study items of next_steps.py and for study_brief.py.

Invented vault under tmp_path (PROJ-900, Q-91xx, M-91xx, QP-92xx, MP-92xx), invented
collections dir; no network. Run: python -m pytest scripts/ledger/test_study_brief.py"""
from __future__ import annotations

import io
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "bitacora"))

import action_log  # noqa: E402
import next_steps as ns  # noqa: E402
import study_brief as sb  # noqa: E402
import study_status as ss  # noqa: E402
from vault_fixture import Vault  # noqa: E402

TODAY = date(2031, 6, 1)

QUESTION = """---
id: {qid}
project: PROJ-900
design: {design}
status: {status}
linea_publicacion: {pub}
linked_plan: {linked}
units_dataset: {units}
history: []
---

# {qid} — Pregunta inventada

## Pregunta

¿Qué proporción de unidades inventadas tiene la marca X?

## Resultado

{resultado}
"""

PLAN = """---
id: {mid}
project: PROJ-900
question: {qid}
status: {status}
frozen_at: {frozen_at}
frozen_commit: {frozen_commit}
---

# {mid} — Plan inventado

## Estimandos

```json
{estimands}
```

## Clasificadores

```json
{{"classifiers": [{{"name": "clasif-inv", "sample": "muestra-inv", "label": "x", "positive": true, "min_precision_lo": 0.8, "min_recall_lo": 0.7}}]}}
```

## Intervalos

```json
{{"ci": 0.95, "bootstrap": 200, "seed": 3}}
```

## Decisión

{decision}
"""

GOOD_EST = '{"estimands": [{"id": "est-1", "type": "proporcion", "column": "marca", "filter": {}, "cluster": null}]}'


class Study:
    def __init__(self, root: Path):
        self.root = root
        self.v = Vault(root)
        self.p = self.v.project()
        self.cols = root.parent / "colecciones"

    def question(self, qid, status="abierta", linked="", units="", pub="false", design="medicion",
                 folder="Preguntas", resultado="<vacío>"):
        d = self.p / folder
        d.mkdir(exist_ok=True)
        path = d / f"{qid}.md"
        path.write_text(QUESTION.format(qid=qid, status=status, linked=linked, units=units, pub=pub,
                                        design=design, resultado=resultado), encoding="utf-8")
        return path

    def plan(self, mid, qid, frozen=False, estimands=GOOD_EST, folder="Mediciones",
             decision="<Qué decide el piloto.>", frozen_commit="abc1234"):
        d = self.p / folder
        d.mkdir(exist_ok=True)
        path = d / f"{mid}.md"
        path.write_text(PLAN.format(
            mid=mid, qid=qid, status="congelado" if frozen else "borrador",
            frozen_at="2031-01-02T10:00:00Z" if frozen else "<YYYY-MM-DDTHH:MM:SSZ>",
            frozen_commit=frozen_commit if frozen else "<git-sha>", estimands=estimands, decision=decision),
            encoding="utf-8")
        return path

    def avisos(self, *estados):
        d = self.p / "Avisos"
        d.mkdir(exist_ok=True)
        rows = [{"id": f"AV-{i}", "destinatario": "Persona Inventada", "canal": "correo", "estado": e,
                 "embargo_hasta": ""} for i, e in enumerate(estados)]
        (d / "registro.json").write_text(json.dumps(rows), encoding="utf-8")

    def deadline(self, iso):
        hub = self.p / "_hub.md"
        hub.write_text(hub.read_text(encoding="utf-8").replace("deadline: <YYYY-MM-DD>", f"deadline: {iso}"),
                       encoding="utf-8")

    def collection(self, rid, state, project="PROJ-900", progress=None, exit_code=None, attempts=1):
        d = self.cols / rid
        d.mkdir(parents=True, exist_ok=True)
        out = self.root.parent / "salidas" / rid
        out.mkdir(parents=True, exist_ok=True)
        if progress is not None:
            (out / "progress.json").write_text(json.dumps(progress), encoding="utf-8")
        (d / "log.txt").write_text("TOKEN-SECRETO-INVENTADO\n", encoding="utf-8")
        (d / "state.json").write_text(json.dumps({
            "id": rid, "projectId": project, "argv": ["python", "recoge.py", "--clave", "ARGV-SECRETO"],
            "commit": "0" * 40, "cwd": str(d / "repo"), "outDir": str(out), "retries": 3, "attempts": attempts,
            "pid": None, "state": state, "exitCode": exit_code, "startedAt": "2031-05-30T08:00:00.000Z",
            "endedAt": None}), encoding="utf-8")
        return d

    def run(self, today=TODAY):
        return ns.run(self.root, "PROJ-900", today=today, collections=self.cols)


@pytest.fixture
def st(tmp_path, monkeypatch):
    for m in ss.AGENT_MARKERS:
        monkeypatch.delenv(m, raising=False)
    monkeypatch.setenv("KAIRO_COLLECTIONS_DIR", str(tmp_path / "no-existe"))
    return Study(tmp_path / "vault")


def kinds(res, kind):
    return [i for i in res["items"] if i["kind"] == kind]


def all_text(res) -> str:
    return json.dumps(res, ensure_ascii=False)


# ---- questions ---------------------------------------------------------------

def test_open_questions_without_plan_and_with_draft(st):
    st.question("Q-9101")
    st.question("Q-9102")
    st.plan("M-9102", "Q-9102", estimands='{"estimands": []}')
    st.question("Q-9103", linked="M-9103")
    st.plan("M-9103", "Q-9103")
    r = st.run()
    assert [i["target"] for i in kinds(r, "pregunta_sin_plan")] == ["Q-9101"]
    drafts = {i["target"]: i for i in kinds(r, "plan_sin_congelar")}
    assert set(drafts) == {"Q-9102", "Q-9103"}
    assert "no son válidos" in drafts["Q-9102"]["message"] and "Estimandos" in drafts["Q-9102"]["message"]
    assert "no son válidos" not in drafts["Q-9103"]["message"]
    assert "plan-study" in drafts["Q-9103"]["action"]
    for i in r["items"]:
        assert set(i) == {"kind", "severity", "target", "message", "action", "refs"}


def test_frozen_plan_waits_for_the_researcher_to_start_collection(st):
    st.question("Q-9104", status="plan_congelado", linked="M-9104")
    st.plan("M-9104", "Q-9104", frozen=True)
    st.avisos()
    r = st.run()
    [i] = kinds(r, "plan_congelado_sin_recoleccion")
    assert i["severity"] == "importante" and "Empezar la recolección" in i["action"]
    [c] = kinds(r, "clasificador_sin_validar")
    assert c["severity"] == "menor" and "validacion-clasif-inv.json" in c["message"]
    assert kinds(r, "gate_pendiente") == []          # the gate is only run one step before estimada
    assert kinds(r, "avisos_sin_cerrar") == []       # an empty registry is closed


def test_collecting_question(st):
    st.question("Q-9105", status="en_recoleccion", linked="M-9105", pub="true")
    st.plan("M-9105", "Q-9105", frozen=True)
    r = st.run()
    assert [i["target"] for i in kinds(r, "en_recoleccion_sin_units_dataset")] == ["Q-9105"]
    [c] = kinds(r, "clasificador_sin_validar")
    assert c["severity"] == "importante"
    gate = kinds(r, "gate_pendiente")
    assert gate and all(i["target"] == "Q-9105" for i in gate)
    text = " ".join(i["message"] for i in gate)
    assert "verifier_no_errors" in text
    assert "units_dataset" not in text                # said once, by its own item
    assert "clasificador clasif-inv: falta" not in text
    assert "avisos_cerrados" not in text              # said by avisos_sin_cerrar
    [a] = kinds(r, "avisos_sin_cerrar")
    assert "sin registro de avisos" in a["message"] and a["severity"] == "importante"
    sev = [ns.SEVERITY_ORDER[i["severity"]] for i in r["items"]]
    assert sev == sorted(sev)


def test_units_map_in_block_form_is_not_missing(st):
    q = st.question("Q-9107", status="en_recoleccion", linked="M-9107",
                    units="\n  default: unidades-inv\n  repos: repos-inv")
    st.plan("M-9107", "Q-9107", frozen=True)
    r = st.run()
    assert kinds(r, "en_recoleccion_sin_units_dataset") == []
    assert ns._units_names(q, {"units_dataset": ""}) == "unidades-inv, repos-inv"


def test_notice_counts_only(st):
    st.question("Q-9106", status="en_recoleccion", linked="M-9106", units="unidades-inv")
    st.plan("M-9106", "Q-9106", frozen=True)
    st.avisos("pendiente", "resuelto", "enviado")
    r = st.run()
    [a] = kinds(r, "avisos_sin_cerrar")
    assert "1 pendiente(s)" in a["message"] and "1 enviado(s) sin fecha de embargo" in a["message"]
    assert "(de 3)" in a["message"]
    assert "Persona Inventada" not in all_text(r) and "AV-" not in all_text(r)


def _git(root, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=root,
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.mark.parametrize("status", ["plan_congelado", "en_recoleccion"])
def test_dataset_registered_before_freeze_is_critical(st, status):
    root = st.root
    _git(root, "init", "-q")
    datos = st.p / "Datos"
    datos.mkdir()
    (datos / "registro.json").write_text(json.dumps([{"name": "unidades-inv", "kind": "file",
                                                       "path": "Datos/u.csv", "sha256": "0" * 64}]),
                                         encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "datos primero")
    st.question("Q-9107", status=status, linked="M-9107", units="unidades-inv")
    st.plan("M-9107", "Q-9107")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "plan")
    sha = _git(root, "rev-parse", "HEAD")
    st.plan("M-9107", "Q-9107", frozen=True, frozen_commit=sha)
    st.avisos()
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "congela")
    r = st.run()
    [i] = kinds(r, "dataset_registrado_antes_del_congelado")
    assert i["severity"] == "crítico" and r["items"][0]["kind"] == "dataset_registrado_antes_del_congelado"
    assert not any("antes de congelar" in g["message"] for g in kinds(r, "gate_pendiente"))


# ---- pilots ------------------------------------------------------------------

def test_pilots(st):
    st.question("QP-9201", design="piloto", folder="Pilotos")
    st.question("QP-9202", design="piloto", folder="Pilotos", linked="MP-9202")
    st.plan("MP-9202", "QP-9202", folder="Pilotos")
    st.question("QP-9203", design="piloto", folder="Pilotos", linked="MP-9203",
                resultado='```estimate-json\n{"plan": "MP-9203", "estimates": []}\n```')
    st.plan("MP-9203", "QP-9203", folder="Pilotos", decision="Seguir si la tasa inventada supera el 1 %.")
    st.question("QP-9204", status="explorada", design="piloto", folder="Pilotos", linked="MP-9204")
    st.plan("MP-9204", "QP-9204", folder="Pilotos", decision="Seguir si algo inventado.")
    r = st.run()
    assert [i["target"] for i in kinds(r, "piloto_sin_plan")] == ["QP-9201"]
    assert [i["target"] for i in kinds(r, "piloto_sin_decision")] == ["QP-9202"]
    assert [i["target"] for i in kinds(r, "piloto_sin_estimar")] == ["QP-9202"]
    assert [i["target"] for i in kinds(r, "piloto_explorado_sin_estudio")] == ["QP-9204"]
    gate = kinds(r, "gate_pendiente")
    assert gate and all(i["target"] == "QP-9203" for i in gate)   # hand-made result: gate fails
    assert any("estimates_reproducible" in i["message"] for i in gate)
    # once a study question exists, the explored pilot asks for nothing
    st.question("Q-9101")
    assert kinds(st.run(), "piloto_explorado_sin_estudio") == []


# ---- deadline ----------------------------------------------------------------

@pytest.mark.parametrize("days,severity", [(5, "crítico"), (15, "importante"), (30, None), (-2, "crítico")])
def test_deadline(st, days, severity):
    st.deadline((TODAY + timedelta(days=days)).isoformat())
    st.question("Q-9101", pub="true")
    st.question("Q-9102", status="estimada", pub="true")
    st.question("Q-9103")
    items = kinds(st.run(), "plazo")
    if severity is None:
        assert items == []
    else:
        [i] = items
        assert i["severity"] == severity and i["refs"] == ["Q-9101"]


def test_deadline_without_pending_publication_question(st):
    st.deadline((TODAY + timedelta(days=3)).isoformat())
    st.question("Q-9101")
    assert kinds(st.run(), "plazo") == []
    hub = st.p / "_hub.md"
    hub.write_text(hub.read_text(encoding="utf-8").replace((TODAY + timedelta(days=3)).isoformat(), "pronto"),
                   encoding="utf-8")
    assert [i["severity"] for i in kinds(st.run(), "plazo")] == ["menor"]


# ---- collections -------------------------------------------------------------

def test_collections(st):
    st.collection("rec-a", "en_marcha", progress={"hechas": 10, "total": 50, "nota": "TEXTO-LIBRE", "ok": True})
    st.collection("rec-b", "fallida", exit_code=2, attempts=4)
    st.collection("rec-c", "en_marcha", project="PROJ-901")
    st.collection("rec-d", "terminada", exit_code=0)
    r = st.run()
    [a] = kinds(r, "recoleccion_activa")
    assert a["target"] == "rec-a" and "hechas=10" in a["message"] and "total=50" in a["message"]
    [f] = kinds(r, "recoleccion_fallida")
    assert f["target"] == "rec-b" and "código de salida 2" in f["message"] and f["severity"] == "importante"
    out = all_text(r)
    for secret in ("TOKEN-SECRETO", "ARGV-SECRETO", "TEXTO-LIBRE", "rec-c", "salidas"):
        assert secret not in out


def test_absent_collections_dir_gives_no_items(st):
    r = st.run()
    assert kinds(r, "recoleccion_activa") + kinds(r, "recoleccion_fallida") == []


# ---- study_brief -------------------------------------------------------------

def _annotation(st):
    d = st.p / "Datos" / "anotacion" / "muestra-inv"
    d.mkdir(parents=True)
    (d / "muestra.jsonl").write_text("\n".join(json.dumps({"id": f"u{i}", "campo": "VALOR-OCULTO"})
                                               for i in range(4)) + "\n", encoding="utf-8")
    (d / "etiquetas-anotadora-inventada.jsonl").write_text(
        "\n".join(json.dumps({"id": i, "labels": {"x": "ETIQUETA-OCULTA"}}) for i in ("u0", "u1", "u0")) + "\n",
        encoding="utf-8")
    (d / "referencia.jsonl").write_text(json.dumps({"id": "u0", "x": True}) + "\n", encoding="utf-8")


def test_brief(st):
    st.deadline((TODAY + timedelta(days=10)).isoformat())
    st.question("Q-9105", status="en_recoleccion", linked="M-9105", pub="true")
    st.plan("M-9105", "Q-9105", frozen=True)
    st.question("QP-9204", status="explorada", design="piloto", folder="Pilotos", linked="MP-9204")
    st.plan("MP-9204", "QP-9204", folder="Pilotos", decision="Seguir si algo inventado.")
    st.avisos("pendiente", "resuelto")
    st.collection("rec-a", "en_marcha", progress={"hechas": 3})
    _annotation(st)
    b = sb.brief(st.root, "PROJ-900", today=TODAY, collections=st.cols, top=3)
    assert b["plazo"]["days_left"] == 10
    q = next(n for n in b["nodos"] if n["id"] == "Q-9105")
    assert q["puerta"] and not q["puerta"]["ok"] and q["puerta"]["total"] == 5
    assert next(n for n in b["nodos"] if n["id"] == "QP-9204")["puerta"] is None
    assert b["anotacion"] == [{"muestra": "muestra-inv", "items": 4, "etiquetados": [2], "referencia": 1,
                               "validaciones": 0}]
    assert b["avisos"]["abiertos"] == 1 and b["avisos"]["pendiente"] == 1
    assert len(b["siguientes"]) == 3 and b["pendientes"] > 3
    assert all(i["severity"] != "crítico" for i in b["siguientes"])
    assert any(i["kind"] == "plazo" and i["severity"] == "importante" for i in b["siguientes"])
    t = sb.text(b)
    for head in ("Plazo:", "Preguntas y pilotos:", "Recolecciones:", "Anotación:", "Avisos:", "Siguientes pasos"):
        assert head in t
    for hidden in ("VALOR-OCULTO", "ETIQUETA-OCULTA", "anotadora-inventada", "Persona Inventada", "TOKEN-SECRETO"):
        assert hidden not in t and hidden not in json.dumps(b, ensure_ascii=False, default=str)


def test_write_bitacora(st, capsys):
    st.question("Q-9101")
    _annotation(st)
    code = sb.main(["--vault", str(st.root), "--project", "PROJ-900", "--json", "--write-bitacora",
                    "--collections-dir", str(st.cols)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["bitacora"] == 0
    log = st.p / "Bitacora" / "acciones.jsonl"
    [e] = action_log.load(log)
    assert e["action"] == "study_brief" and e["actor"] == "agent" and e["refs"] == ["Q-9101"]
    assert e["summary"].startswith("Parte del estudio: 1 pregunta(s)")
    assert "muestra-inv" not in e["summary"]
    assert (log.parent / f"{e['date']}.md").is_file()


def test_watch(st):
    d = st.collection("rec-a", "en_marcha", progress={"hechas": 1})
    out_dir = Path(json.loads((d / "state.json").read_text(encoding="utf-8"))["outDir"])
    steps = iter([
        lambda: None,                                                                  # unchanged: no line
        lambda: (out_dir / "progress.json").write_text('{"hechas": 2}', encoding="utf-8"),
        lambda: (d / "state.json").write_text(
            (d / "state.json").read_text(encoding="utf-8").replace('"en_marcha"', '"fallida"')
            .replace('"exitCode": null', '"exitCode": 1'), encoding="utf-8"),
    ])
    buf = io.StringIO()
    code = sb.watch("PROJ-900", "proyecto-inventado", "rec-a", st.cols, 0, 0, out=buf, sleep=lambda _s: next(steps)())
    lines = buf.getvalue().splitlines()
    assert code == 0
    assert lines == ["rec-a · en_marcha · intentos 1 · progreso: hechas=1",
                     "rec-a · en_marcha · intentos 1 · progreso: hechas=2",
                     "FIN rec-a · fallida · intentos 1 · código 1 · progreso: hechas=2"]
    buf = io.StringIO()
    assert sb.watch("PROJ-900", "proyecto-inventado", "rec-zz", st.cols, 0, 0, out=buf) == 3


def test_cli_bad_project(st, capsys):
    assert sb.main(["--vault", str(st.root), "--project", "PROJ-999"]) == 2
