"""Tests for study_status.py, study_gates.py, Q nodes in build_graph and the
--question packet of verifier_packet. Invented vault only (PROJ-901, Q-9001,
M-9002), written under tmp_path."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "analysis"))

import build_graph  # noqa: E402
import estimate  # noqa: E402
import study_gates  # noqa: E402
import study_status as ss  # noqa: E402
import verifications as vf  # noqa: E402
import verifier_packet  # noqa: E402

CLEAN = ("verifications:\n  - date: 2031-02-01\n    verifier: kairo/fresh-verifier@1.1.0\n"
         "    model: t\n    verdict: no_errors_found\n    scope: note\n")

QUESTION = """---
id: Q-9001
project: PROJ-901
design: medicion
status: {status}
updated: 2031-01-01
linea_publicacion: true
linked_plan: M-9002
units_dataset: unidades-demo
{verif}history: []
---

# Q-9001 — Pregunta inventada

## Pregunta

¿Qué proporción de las unidades inventadas tiene la marca X?

## Población y marco

Unidades inventadas de prueba.

## Estimandos

Una proporción.

## Resultado

{resultado}

## Historia

Nada.
"""

PLAN = """---
id: M-9002
project: PROJ-901
question: {question}
status: congelado
frozen_at: {frozen_at}
frozen_commit: {frozen_commit}
---

# M-9002 — Plan inventado

## Población y marco muestral

Unidades inventadas.

## Estimandos

```json
{{"estimands": [{{"id": "est-1", "type": "proporcion", "column": "marca", "filter": {{}}, "cluster": null}}]}}
```

## Clasificadores

```json
{{"classifiers": [{{"name": "clasif-demo", "sample": "muestra-demo", "label": "x", "positive": true, "min_precision_lo": {min_p}, "min_recall_lo": 0.70}}]}}
```

## Intervalos

```json
{{"ci": 0.95, "bootstrap": 200, "seed": 11}}
```

## Ética

Nada sensible: datos inventados.
"""

UNITS = "uid,marca\nu1,1\nu2,0\nu3,1\nu4,1\nu5,0\nu6,1\n"


AGREEMENT = HERE.parent / "analysis" / "agreement.py"
SAMPLE = Path("Datos") / "anotacion" / "muestra-demo"
VERIF_BODY = ("\n## Verificación independiente\n\n### 2031-02-01 — kairo/fresh-verifier@1.1.0 (note)\n\n"
              "- modelo: t\n- veredicto: no_errors_found\n- alcance: note\n- paquete sha256: {sha}\n"
              "- hallazgos: ninguno\n")


class Vault:
    """An invented vault in a real git repo: the plan is committed, then frozen
    (frozen_commit = that commit), then the units dataset is registered."""

    def __init__(self, root: Path, git: bool = True, data_first: bool = False):
        self.root = root
        self.proj = root / "Projects" / "demo"
        self.has_git = git
        for d in ("Preguntas", "Mediciones", "Datos", "Avisos"):
            (self.proj / d).mkdir(parents=True, exist_ok=True)
        if git:
            self.git("init", "-q")
        self.registry([])
        if data_first:
            self.register_units()
            self.commit("datos")
        self.plan(frozen_at="", frozen_commit="")
        self.frozen = self.commit("plan") if git else "abc1234"
        self.plan()
        self.commit("congela")
        if not data_first:
            self.register_units()
            self.commit("datos")
        self.annotate()
        self.notices("resuelto")

    def git(self, *args) -> str:
        r = subprocess.run(["git", "-c", "user.name=tester", "-c", "user.email=t@example.invalid", *args],
                           cwd=self.root, capture_output=True, text=True, check=True)
        return r.stdout.strip()

    def commit(self, msg: str) -> str | None:
        if not self.has_git:
            return None
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD")

    def registry(self, entries):
        (self.proj / "Datos" / "registro.json").write_text(json.dumps(entries), encoding="utf-8")

    def register_units(self):
        (self.proj / "Datos" / "u.csv").write_text(UNITS, encoding="utf-8", newline="")
        sha = hashlib.sha256(UNITS.encode("utf-8")).hexdigest()
        self.registry([{"name": "unidades-demo", "kind": "file", "path": "Datos/u.csv", "sha256": sha}])

    def plan(self, frozen_at="2031-01-02T10:00:00Z", frozen_commit=None, min_p="0.80", question="Q-9001"):
        frozen_commit = self.frozen if frozen_commit is None else frozen_commit
        (self.proj / "Mediciones" / "M-9002.md").write_text(
            PLAN.format(frozen_at=frozen_at, frozen_commit=frozen_commit, min_p=min_p, question=question),
            encoding="utf-8")

    def annotate(self, fp=0, fn=0, same_file=False, gold_dir=None):
        """100 invented gold labels (60 positive) and predictions with fp false
        positives and fn false negatives; the validation file from agreement.py.
        Predictions carry a `fuente` field, so the two files differ even when the
        labels agree. same_file: pred is a byte copy of gold. gold_dir: where the
        gold file goes (default: the sample's folder)."""
        d = self.proj / SAMPLE
        d.mkdir(parents=True, exist_ok=True)
        gd = gold_dir or d
        gd.mkdir(parents=True, exist_ok=True)
        gold = [i < 60 for i in range(100)]
        pred = [(g and i >= fn) or (not g and i - 60 < fp) for i, g in enumerate(gold)]
        (gd / "gold.jsonl").write_text(
            "\n".join(json.dumps({"id": f"u{i}", "x": v}) for i, v in enumerate(gold)), encoding="utf-8")
        if same_file:
            (d / "pred.jsonl").write_bytes((gd / "gold.jsonl").read_bytes())
        else:
            (d / "pred.jsonl").write_text(
                "\n".join(json.dumps({"id": f"u{i}", "x": v, "fuente": "clasif-demo"}) for i, v in enumerate(pred)),
                encoding="utf-8")
        r = subprocess.run([sys.executable, str(AGREEMENT), "validate", "--gold", str(gd / "gold.jsonl"),
                            "--pred", str(d / "pred.jsonl"), "--label", "x", "--positive", "true",
                            "--vault", str(self.root)], capture_output=True, text=True, check=True)
        (d / "validacion-clasif-demo.json").write_text(r.stdout, encoding="utf-8")

    def validation_file(self) -> Path:
        return self.proj / SAMPLE / "validacion-clasif-demo.json"

    def notices(self, *items):
        rows = []
        for i, it in enumerate(items, 1):
            it = {"estado": it} if isinstance(it, str) else it
            rows.append({"id": f"AV-{i}", "destinatario": "nadie", "canal": "correo", "enviado": "",
                         "embargo_hasta": "", "respuesta": "", **it})
        (self.proj / "Avisos" / "registro.json").write_text(json.dumps(rows), encoding="utf-8")

    def estimate_json(self) -> str:
        return json.dumps(estimate.run(self.root, "M-9002", "unidades-demo"), ensure_ascii=False, indent=2)

    def question(self, status="en_recoleccion", verified=True, resultado=None):
        """verified: a no_errors_found verification bound to the packet built now."""
        if resultado is None:
            resultado = f"```estimate-json\n{self.estimate_json()}\n```"
        p = self.proj / "Preguntas" / "Q-9001.md"
        p.write_text(QUESTION.format(status=status, verif="", resultado=resultado), encoding="utf-8")
        if verified:
            packet, _ = verifier_packet.build_question(str(self.root), "Q-9001")
            sha = hashlib.sha256(packet.encode("utf-8")).hexdigest()
            p.write_text(QUESTION.format(status=status, verif=CLEAN, resultado=resultado)
                         + VERIF_BODY.format(sha=sha), encoding="utf-8")
        return p

@pytest.fixture
def vault(tmp_path, monkeypatch):
    for m in ss.AGENT_MARKERS:
        monkeypatch.delenv(m, raising=False)
    return Vault(tmp_path / "vault")


def cli(capsys, *args):
    code = ss.main(list(args))
    out, err = capsys.readouterr()
    return code, (json.loads(out) if out.strip() else None), err


def fm(path: Path) -> str:
    return path.read_text(encoding="utf-8").split("\n---", 1)[0]


# ---- study_status --------------------------------------------------------

def test_apply_allowed_edge(vault, capsys):
    p = vault.question(status="abierta", verified=False, resultado="")
    code, out, err = cli(capsys, "apply", "--vault", str(vault.root), "--question", "Q-9001",
                         "--to", "plan_congelado", "--by", "tester", "--evidence", "plan M-9002 congelado")
    assert code == 0, err
    assert out["from"] == "abierta" and out["to"] == "plan_congelado"
    head = fm(p)
    assert "status: plan_congelado" in head
    assert "updated: 2031-01-01" not in head and "updated: " in head
    assert "  - date: " in head and "    status: plan_congelado" in head and "    by: tester" in head
    assert "history: []" not in head


def test_apply_refuses_unknown_edge(vault, capsys):
    vault.question(status="abierta", verified=False, resultado="")
    code, _, err = cli(capsys, "apply", "--vault", str(vault.root), "--question", "Q-9001",
                       "--to", "estimada", "--by", "tester", "--evidence", "x")
    assert code == 3
    assert "abierta" in err and "estimada" in err


def test_estimada_requires_gate(vault, capsys):
    vault.question(status="en_recoleccion")
    vault.annotate(fp=30)
    code, _, err = cli(capsys, "apply", "--vault", str(vault.root), "--question", "Q-9001",
                       "--to", "estimada", "--by", "tester", "--evidence", "estimado")
    assert code == 3
    assert "clasif-demo" in err
    assert "status: en_recoleccion" in fm(vault.proj / "Preguntas" / "Q-9001.md")


def test_estimada_passes_with_full_gate(vault, capsys):
    p = vault.question(status="en_recoleccion")
    g = ss.gate(vault.root, "Q-9001")
    assert (g["applies"], g["ok"], g["missing"]) == (True, True, [])
    code, out, err = cli(capsys, "apply", "--vault", str(vault.root), "--question", "Q-9001",
                         "--to", "estimada", "--by", "tester", "--evidence", "puerta superada")
    assert code == 0, err
    assert out["terminal"] is True
    assert "status: estimada" in fm(p)


def test_researcher_only_edge_refused_in_apply(vault, capsys):
    vault.question(status="abierta", verified=False, resultado="")
    code, _, err = cli(capsys, "apply", "--vault", str(vault.root), "--question", "Q-9001",
                       "--to", "descartada", "--by", "tester", "--evidence", "x")
    assert code == 3 and "discard" in err


@pytest.mark.parametrize("marker", ss.AGENT_MARKERS)
def test_start_collection_refused_in_agent_session(vault, capsys, monkeypatch, marker):
    vault.question(status="plan_congelado", verified=False, resultado="")
    monkeypatch.setenv(marker, "1")
    code, _, err = cli(capsys, "apply", "--vault", str(vault.root), "--question", "Q-9001",
                       "--to", "en_recoleccion", "--by", "agente", "--evidence", "datos registrados")
    assert code == 3 and marker in err and "investigador" in err
    assert "status: plan_congelado" in fm(vault.proj / "Preguntas" / "Q-9001.md")


def test_start_collection_by_researcher(vault, capsys):
    p = vault.question(status="plan_congelado", verified=False, resultado="")
    code, out, err = cli(capsys, "apply", "--vault", str(vault.root), "--question", "Q-9001",
                         "--to", "en_recoleccion", "--by", "investigador", "--evidence", "recolección lanzada")
    assert code == 0, err
    assert out["to"] == "en_recoleccion" and "status: en_recoleccion" in fm(p)


def test_propose_marks_start_collection_as_researcher_only(vault, capsys):
    vault.question(status="plan_congelado", verified=False, resultado="")
    code, out, _ = cli(capsys, "propose", "--vault", str(vault.root), "--question", "Q-9001")
    opt = next(o for o in out["options"] if o["to"] == "en_recoleccion")
    assert code == 0 and opt["researcher_only"] is True and out["to"] is None


def test_discard_refused_in_agent(vault, capsys, monkeypatch):
    vault.question(status="abierta", verified=False, resultado="")
    monkeypatch.setenv("CLAUDECODE", "1")
    code, _, err = cli(capsys, "discard", "--vault", str(vault.root), "--question", "Q-9001",
                       "--reason", "ya no interesa", "--by", "tester")
    assert code == 3 and "CLAUDECODE" in err
    assert "status: abierta" in fm(vault.proj / "Preguntas" / "Q-9001.md")


def test_discard_by_researcher(vault, capsys):
    p = vault.question(status="plan_congelado", verified=False, resultado="")
    code, out, err = cli(capsys, "discard", "--vault", str(vault.root), "--question", "Q-9001",
                         "--reason", "ya no interesa", "--by", "tester")
    assert code == 0, err
    assert out["to"] == "descartada" and "status: descartada" in fm(p)


def test_propose_reports_gate(vault, capsys):
    vault.question(status="en_recoleccion")
    vault.notices("resuelto", "pendiente")
    code, out, _ = cli(capsys, "propose", "--vault", str(vault.root), "--question", "Q-9001")
    assert code == 0
    assert out["to"] is None
    est = next(o for o in out["options"] if o["to"] == "estimada")
    assert est["gate"]["ok"] is False
    assert any("pendiente" in m for m in est["gate"]["missing"])
    assert "nadie" not in json.dumps(out)  # never the recipient of a notice
    vault.notices("resuelto")
    _, out, _ = cli(capsys, "propose", "--vault", str(vault.root), "--question", "Q-9001")
    assert out["to"] == "estimada"


def test_unknown_question_refused(vault, capsys):
    code, _, err = cli(capsys, "propose", "--vault", str(vault.root), "--question", "Q-9999")
    assert code == 3 and "Q-9999" in err


# ---- gates ---------------------------------------------------------------

def missing(vault):
    return ss.gate(vault.root, "Q-9001")["missing"]


def test_gate_estimates_reproducible_detects_edit(vault):
    stored = vault.estimate_json()
    data = json.loads(stored)
    data["estimates"][0]["value"] = 0.9
    vault.question(resultado=f"```estimate-json\n{json.dumps(data, indent=2)}\n```")
    miss = missing(vault)
    assert any("est-1" in m and "estimate" in m for m in miss), miss


def test_gate_estimates_missing_block(vault):
    vault.question(resultado="Sin bloque.")
    assert any("estimate-json" in m for m in missing(vault))


def test_gate_estimates_refused_units_changed(vault):
    vault.question()
    (vault.proj / "Datos" / "u.csv").write_text(UNITS + "u7,1\n", encoding="utf-8", newline="")
    miss = missing(vault)
    assert any("huella" in m for m in miss), miss
    assert not any("u7" in m for m in miss)


def test_gate_plan_not_frozen(vault):
    vault.plan(frozen_at="", frozen_commit="<git-sha>")
    vault.question()
    miss = missing(vault)
    assert any("frozen_at" in m for m in miss) and any("frozen_commit" in m for m in miss)


def test_gate_plan_missing(vault):
    (vault.proj / "Mediciones" / "M-9002.md").unlink()
    vault.question(resultado="", verified=False)
    assert any("M-9002" in m and "Mediciones" in m for m in missing(vault))


def test_gate_classifier_validation_missing(vault):
    vault.question()
    (vault.proj / "Datos" / "anotacion" / "muestra-demo" / "validacion-clasif-demo.json").unlink()
    assert any("clasif-demo" in m and "validacion-clasif-demo.json" in m for m in missing(vault))


def _edit_validation(vault, **inputs):
    f = vault.validation_file()
    v = json.loads(f.read_text(encoding="utf-8"))
    v["inputs"].update(inputs)
    f.write_text(json.dumps(v), encoding="utf-8")


@pytest.mark.parametrize("change", [{"label": "otra"}, {"positive": "false"}, {"ci": 0.9}])
def test_gate_validation_must_match_plan(vault, change):
    vault.question()
    assert not any("distintos de los del plan" in m for m in missing(vault))
    _edit_validation(vault, **change)
    assert any("clasif-demo" in m and "label/positive/ci distintos de los del plan" in m for m in missing(vault))


def test_gate_recall_below(vault):
    vault.question()
    vault.annotate(fn=30)
    assert any("clasif-demo" in m and "recall_lo" in m for m in missing(vault))


def test_gate_verifier(vault):
    vault.question(verified=False)
    assert any("verific" in m for m in missing(vault))


def test_gate_no_notice_registry(vault):
    vault.question()
    (vault.proj / "Avisos" / "registro.json").unlink()
    assert "sin registro de avisos" in missing(vault)


def test_plan_frozen_commit_must_exist(vault):
    vault.plan(frozen_commit="0" * 40)
    vault.question()
    assert any("frozen_commit" in m and "no existe" in m for m in missing(vault))


def test_plan_changed_after_freeze_fails(vault):
    vault.plan(min_p="0.50")
    vault.commit("baja la cota")
    vault.question()
    assert "el plan cambió después de congelarlo: Clasificadores" in missing(vault)


def test_data_registered_before_freeze_fails(tmp_path, monkeypatch):
    for m in ss.AGENT_MARKERS:
        monkeypatch.delenv(m, raising=False)
    v = Vault(tmp_path / "vault", data_first=True)
    v.question()
    assert "los datos se registraron antes de congelar el plan" in missing(v)


def test_data_registered_before_freeze_under_another_name_fails(tmp_path, monkeypatch):
    """Renaming the dataset after the freeze does not hide that its file (same sha256)
    was already registered before it."""
    for m in ss.AGENT_MARKERS:
        monkeypatch.delenv(m, raising=False)
    v = Vault(tmp_path / "vault", data_first=True)
    reg_path = v.proj / "Datos" / "registro.json"
    reg = json.loads(reg_path.read_text(encoding="utf-8"))
    v.registry([{**reg[0], "name": "nombre-nuevo"}])
    v.commit("mismo fichero, otro nombre")
    p = v.question(verified=False, resultado="")
    p.write_text(p.read_text(encoding="utf-8").replace("units_dataset: unidades-demo", "units_dataset: nombre-nuevo"),
                 encoding="utf-8")
    assert "los datos se registraron antes de congelar el plan" in missing(v)


def test_data_never_committed_fails(vault):
    p = vault.question(verified=False)
    reg_path = vault.proj / "Datos" / "registro.json"
    reg = json.loads(reg_path.read_text(encoding="utf-8"))
    reg_path.write_text(json.dumps(reg + [{**reg[0], "name": "otro-demo", "sha256": "0" * 64}]), encoding="utf-8")  # not committed
    p.write_text(p.read_text(encoding="utf-8").replace("units_dataset: unidades-demo", "units_dataset: otro-demo"),
                 encoding="utf-8")
    assert any("otro-demo" in m and "commit" in m for m in missing(vault))


def test_plan_frozen_needs_units_dataset(vault):
    p = vault.question()
    p.write_text(p.read_text(encoding="utf-8").replace("units_dataset: unidades-demo", "units_dataset:"),
                 encoding="utf-8")
    assert "sin units_dataset" in missing(vault)


def test_plan_blank_question_refused(vault):
    vault.plan(question="")
    vault.commit("sin pregunta")
    vault.question(verified=False)
    assert any("question" in m and "M-9002" in m for m in missing(vault))


def test_no_git_fails_closed(tmp_path, monkeypatch):
    for m in ss.AGENT_MARKERS:
        monkeypatch.delenv(m, raising=False)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))  # no enclosing repo either
    v = Vault(tmp_path / "vault", git=False)
    v.question()
    g = ss.gate(v.root, "Q-9001")
    assert g["ok"] is False and any("git" in m for m in g["missing"])


def test_hand_edited_validation_fails(vault):
    vault.question()
    f = vault.validation_file()
    data = json.loads(f.read_text(encoding="utf-8"))
    data["precision_lo"] = 0.99
    f.write_text(json.dumps(data), encoding="utf-8")
    assert any("clasif-demo" in m and "no coincide" in m for m in missing(vault))


def test_gold_changed_after_validation_fails(vault):
    vault.question()
    gold = vault.proj / SAMPLE / "gold.jsonl"
    gold.write_text(gold.read_text(encoding="utf-8").replace('"x": false', '"x": true', 1), encoding="utf-8")
    assert any("clasif-demo" in m and "cambió" in m for m in missing(vault))


def test_gold_equal_to_pred_fails(vault):
    vault.question()
    vault.annotate(same_file=True)
    assert any("clasif-demo" in m and "mismo fichero" in m for m in missing(vault))


def test_gold_outside_sample_folder_fails(vault):
    vault.question()
    vault.annotate(gold_dir=vault.proj / "Datos" / "anotacion" / "otra-muestra")
    assert any("clasif-demo" in m and "muestra-demo" in m and "gold" in m for m in missing(vault))


def test_unreadable_input_is_reported(vault, monkeypatch):
    vault.question()
    real = Path.read_bytes

    def broken(self):
        if self.name == "gold.jsonl":
            raise PermissionError("bloqueado")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", broken)
    assert any("clasif-demo" in m and "no se pudo leer" in m for m in missing(vault))


def test_validation_without_inputs_fails(vault):
    vault.question()
    f = vault.validation_file()
    data = json.loads(f.read_text(encoding="utf-8"))
    del data["inputs"]
    f.write_text(json.dumps(data), encoding="utf-8")
    assert any("validación sin entradas registradas: vuelve a generarla con agreement.py" in m
               for m in missing(vault))


def _avisos(vault):
    return [m for m in missing(vault) if "aviso" in m.lower()]


def test_avisos_closed_states_pass(vault):
    past = (date.today() - timedelta(days=1)).isoformat()
    vault.question()
    vault.notices("respondido", "resuelto", "sin_respuesta", {"estado": "enviado", "embargo_hasta": past})
    assert _avisos(vault) == []
    vault.notices()
    assert _avisos(vault) == []  # an empty registry passes


def test_avisos_open_states_fail(vault):
    today = date.today().isoformat()
    future = (date.today() + timedelta(days=30)).isoformat()
    vault.question()
    vault.notices("pendiente", "pendiente", {"estado": "enviado"}, {"estado": "enviado", "embargo_hasta": today},
                  {"estado": "enviado", "embargo_hasta": future}, "raro", {"estado": None})
    msgs = " | ".join(_avisos(vault))
    assert "2 pendiente" in msgs
    assert "1 enviado(s) sin fecha de embargo" in msgs
    assert "2 enviado(s) con el embargo sin vencer" in msgs
    assert "2 con estado desconocido" in msgs
    assert "nadie" not in msgs and "AV-" not in msgs


def test_verifier_must_match_current_packet(vault):
    p = vault.question()
    p.write_text(p.read_text(encoding="utf-8").replace("unidades inventadas tiene", "unidades tiene"),
                 encoding="utf-8")
    assert any("verific" in m and "paquete" in m for m in missing(vault))


def test_later_unbound_clean_verdict_does_not_pass(vault, tmp_path):
    """errors_found on the current packet, then a later no_errors_found with no
    packet sha: the latest entry is not bound to any packet, so the gate fails."""
    p = vault.question(verified=False)
    packet, _ = verifier_packet.build_question(str(vault.root), "Q-9001")
    sha = hashlib.sha256(packet.encode("utf-8")).hexdigest()
    rep = tmp_path / "rep.md"
    rep.write_text('```json\n{"verdict": "errors_found", "scope": "note", "findings": '
                   '[{"severity": "crítico", "location": "Resultado", "why": "inventado"}]}\n```\n', encoding="utf-8")
    vf.append(str(p), "kairo/fresh-verifier@1.1.0", "t", "errors_found", "note", "2031-02-01",
              report=str(rep), packet_sha=sha)
    vf.append(str(p), "kairo/fresh-verifier@1.1.0", "t", "no_errors_found", "note", "2031-02-02")
    miss = [m for m in missing(vault) if "verific" in m]
    assert miss, missing(vault)
    assert vf.latest_bound(str(p), "note")["packet_sha256"] is None


def test_estimate_timeout_counts_as_failed(vault, monkeypatch):
    vault.question()
    real = study_gates.subprocess.run

    def slow(cmd, *a, **kw):
        if any(str(c).endswith("estimate.py") for c in cmd):
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        return real(cmd, *a, **kw)

    monkeypatch.setattr(study_gates.subprocess, "run", slow)
    assert any("estimate.py" in m and "tiempo" in m for m in missing(vault))


def test_apply_refuses_non_study_id(vault, capsys):
    code, _, err = cli(capsys, "apply", "--vault", str(vault.root), "--question", "H-0001",
                       "--to", "estimada", "--by", "tester", "--evidence", "x")
    assert code == 3


def test_gate_not_applicable_to_hypothesis(vault):
    assert ss.gate(vault.root, "H-0001") == {"applies": False, "ok": True, "missing": [], "checks": []}


def test_gate_names_match_registry():
    assert set(study_gates.GATES) == {"plan_frozen", "estimates_reproducible", "classifiers_validated",
                                      "verifier_no_errors", "avisos_cerrados", "avisos_enviados"}


# ---- build_graph ---------------------------------------------------------

def test_graph_includes_questions(vault):
    vault.question(status="plan_congelado")
    nodes, findings = build_graph.build(vault.root)
    assert nodes["Q-9001"].status == "plan_congelado" and nodes["Q-9001"].kind == "Q"
    assert not [f for f in findings if f.node == "Q-9001"]
    build_graph.write_ledgers(vault.root, nodes, findings, ["Projects/demo"])
    ledger = (vault.proj / "_ledger.md").read_text(encoding="utf-8")
    assert "| Q-9001 | plan_congelado |" in ledger


def test_graph_flags_bad_question_status(vault):
    vault.question(status="apoyada")
    _, findings = build_graph.build(vault.root)
    assert any(f.node == "Q-9001" and f.kind == "bad_status" for f in findings)


# ---- verifier_packet -----------------------------------------------------

def test_find_note_question_and_plan(vault):
    p = vault.question()
    assert Path(verifier_packet.find_note(str(vault.root), "Q-9001")) == p
    assert Path(verifier_packet.find_note(str(vault.root), "M-9002")).name == "M-9002.md"


def test_question_packet(vault, capsys):
    vault.question()
    code = verifier_packet.main(["--vault", str(vault.root), "--question", "Q-9001"])
    packet = capsys.readouterr().out
    assert code == 0
    assert "¿Qué proporción" in packet
    assert "```estimate-json" in packet and '"est-1"' in packet
    assert '"min_precision_lo": 0.80' in packet and f"frozen_commit: {vault.frozen}" in packet
    assert "## Historia" not in packet and "Nada sensible" not in packet
    assert "verifications" not in packet and "status: en_recoleccion" not in packet
    assert "u1" not in packet


def test_question_packet_needs_one_mode(vault, capsys):
    vault.question()
    assert verifier_packet.main(["--vault", str(vault.root)]) == 2
    assert verifier_packet.main(["--vault", str(vault.root), "--question", "Q-9001",
                                 "--note", str(vault.proj / "Preguntas" / "Q-9001.md")]) == 2


def test_gate_reports_checks_per_name(vault):
    vault.question(verified=False)
    res = ss.gate(vault.root, "Q-9001")
    design = ss._designs.design_for_id(ss._designs.load(), "Q-9001")
    assert [c["name"] for c in res["checks"]] == list(design["gate"])
    assert all(c["ok"] == (not c["missing"]) for c in res["checks"])
    assert [m for c in res["checks"] for m in c["missing"]] == res["missing"]
    assert any(not c["ok"] for c in res["checks"])


def test_gate_checks_on_exception(vault, monkeypatch):
    vault.question()
    design = ss._designs.design_for_id(ss._designs.load(), "Q-9001")
    name = design["gate"][0]

    def boom(*_a):
        raise ValueError("roto")

    monkeypatch.setitem(ss.GATES, name, boom)
    res = ss.gate(vault.root, "Q-9001")
    row = res["checks"][0]
    assert row["name"] == name and row["ok"] is False
    assert any("roto" in m for m in row["missing"])
    assert not res["ok"]


def test_question_packet_finds_a_plan_with_a_title(vault):
    """FW-11: the plan is found in the design's plan_folder as M-XXXX.md or
    M-XXXX <title>.md, the rule the gate uses."""
    vault.question(verified=False)
    plan = vault.proj / "Mediciones" / "M-9002.md"
    plan.rename(plan.with_name("M-9002 Plan inventado.md"))
    packet, _ = verifier_packet.build_question(str(vault.root), "Q-9001")
    assert "M-9002" in packet


# ---- piloto (QP / MP): exploratory, never citable ---------------------------

PILOT_PLAN = """---
id: MP-9003
project: PROJ-901
question: QP-9004
status: borrador
---

# MP-9003 — Plan de piloto inventado

## Estimandos

```json
{"estimands": [{"id": "pil-1", "type": "proporcion", "column": "marca", "filter": {}, "cluster": null}]}
```

## Clasificadores

```json
{"classifiers": []}
```

## Intervalos

```json
{"ci": 0.95, "bootstrap": 200, "seed": 11}
```
"""

PILOT = """---
id: QP-9004
project: PROJ-901
design: piloto
status: {status}
updated: 2031-01-01
linked_plan: MP-9003
units_dataset: piloto-demo
history: []
---

# QP-9004 — Piloto inventado

## Pregunta

¿Merece la pena medir la marca X?

## Resultado

{resultado}
"""


def make_pilot(vault, status="abierta", resultado=None) -> Path:
    d = vault.proj / "Pilotos"
    d.mkdir(exist_ok=True)
    (d / "MP-9003.md").write_text(PILOT_PLAN, encoding="utf-8")
    reg = json.loads((vault.proj / "Datos" / "registro.json").read_text(encoding="utf-8"))
    sha = hashlib.sha256(UNITS.encode("utf-8")).hexdigest()
    reg.append({"name": "piloto-demo", "kind": "file", "path": "Datos/u.csv", "sha256": sha})
    vault.registry(reg)
    if resultado is None:
        out = json.dumps(estimate.run(vault.root, "MP-9003", "piloto-demo"), ensure_ascii=False, indent=2)
        resultado = f"```estimate-json\n{out}\n```"
    p = d / "QP-9004.md"
    p.write_text(PILOT.format(status=status, resultado=resultado), encoding="utf-8")
    return p


def test_pilot_reaches_explorada_on_reproducible_estimates(vault, capsys):
    p = make_pilot(vault)
    g = ss.gate(vault.root, "QP-9004")
    assert g["ok"], g
    assert [c["name"] for c in g["checks"]] == ["estimates_reproducible"]
    code, out, err = cli(capsys, "apply", "--vault", str(vault.root), "--question", "QP-9004",
                         "--to", "explorada", "--by", "tester", "--evidence", "piloto estimado")
    assert code == 0, err
    assert "status: explorada" in fm(p)


def test_pilot_with_hand_edited_numbers_fails(vault):
    make_pilot(vault, resultado='```estimate-json\n{"plan": "MP-9003", "estimates": []}\n```')
    g = ss.gate(vault.root, "QP-9004")
    assert not g["ok"] and "no coincide" in " ".join(g["missing"])


def test_pilot_cannot_be_frozen_into_plan_congelado(vault, capsys):
    make_pilot(vault)
    code, _, _ = cli(capsys, "apply", "--vault", str(vault.root), "--question", "QP-9004",
                     "--to", "plan_congelado", "--by", "tester", "--evidence", "x")
    assert code == 3



def _weighted_validation(vault):
    """Re-validate the demo classifier as a stratified sample: two strata with weights."""
    d = vault.proj / SAMPLE
    rows = [{"id": f"u{i}", "_stratum": "alta" if i < 60 else "baja", "_weight": 1.0 if i < 60 else 9.0}
            for i in range(100)]
    (d / "muestra.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    r = subprocess.run([sys.executable, str(AGREEMENT), "validate", "--gold", str(d / "gold.jsonl"),
                        "--pred", str(d / "pred.jsonl"), "--label", "x", "--positive", "true",
                        "--vault", str(vault.root), "--weights-from", str(d / "muestra.jsonl")],
                       capture_output=True, text=True, check=True)
    v = json.loads(r.stdout)
    assert v.get("weighted") is True and "weights" in v["inputs"]
    (d / "validacion-clasif-demo.json").write_text(r.stdout, encoding="utf-8")
    return d / "muestra.jsonl"


def test_gate_weighted_validation_reproduces(vault):
    vault.question()
    _weighted_validation(vault)
    assert not any("clasif-demo" in m for m in missing(vault))


def test_gate_weighted_validation_weights_changed(vault):
    vault.question()
    w = _weighted_validation(vault)
    w.write_text(w.read_text(encoding="utf-8").replace('"_weight": 9.0', '"_weight": 3.0'), encoding="utf-8")
    assert any("clasif-demo" in m and "weights" in m and "cambió" in m for m in missing(vault))
