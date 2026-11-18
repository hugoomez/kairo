"""Tests for study_status.py, study_gates.py, Q nodes in build_graph and the
--question packet of verifier_packet. Invented vault only (PROJ-901, Q-9001,
M-9002), written under tmp_path."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "analysis"))

import build_graph  # noqa: E402
import estimate  # noqa: E402
import study_gates  # noqa: E402
import study_status as ss  # noqa: E402
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
question: Q-9001
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
{{"classifiers": [{{"name": "clasif-demo", "sample": "muestra-demo", "min_precision_lo": 0.80, "min_recall_lo": 0.70}}]}}
```

## Intervalos

```json
{{"ci": 0.95, "bootstrap": 200, "seed": 11}}
```

## Ética

Nada sensible: datos inventados.
"""

UNITS = "uid,marca\nu1,1\nu2,0\nu3,1\nu4,1\nu5,0\nu6,1\n"


class Vault:
    def __init__(self, root: Path):
        self.root = root
        self.proj = root / "Projects" / "demo"
        for d in ("Preguntas", "Mediciones", "Datos", "Avisos"):
            (self.proj / d).mkdir(parents=True, exist_ok=True)
        (self.proj / "Datos" / "u.csv").write_text(UNITS, encoding="utf-8", newline="")
        sha = hashlib.sha256(UNITS.encode("utf-8")).hexdigest()
        (self.proj / "Datos" / "registro.json").write_text(json.dumps(
            [{"name": "unidades-demo", "kind": "file", "path": "Datos/u.csv", "sha256": sha}]), encoding="utf-8")
        self.plan()
        self.validation(0.91, 0.85)
        self.notices("resuelto")

    def plan(self, frozen_at="2031-01-02T10:00:00Z", frozen_commit="abc1234"):
        (self.proj / "Mediciones" / "M-9002.md").write_text(
            PLAN.format(frozen_at=frozen_at, frozen_commit=frozen_commit), encoding="utf-8")

    def validation(self, p_lo, r_lo):
        d = self.proj / "Datos" / "anotacion" / "muestra-demo"
        d.mkdir(parents=True, exist_ok=True)
        (d / "validacion-clasif-demo.json").write_text(json.dumps(
            {"precision": 0.95, "precision_lo": p_lo, "precision_hi": 0.99,
             "recall": 0.9, "recall_lo": r_lo, "recall_hi": 0.97, "n": 120}), encoding="utf-8")

    def notices(self, *estados):
        (self.proj / "Avisos" / "registro.json").write_text(json.dumps(
            [{"id": f"AV-{i}", "destinatario": "nadie", "canal": "correo", "enviado": "",
              "embargo_hasta": "", "respuesta": "", "estado": e} for i, e in enumerate(estados, 1)]),
            encoding="utf-8")

    def estimate_json(self) -> str:
        return json.dumps(estimate.run(self.root, "M-9002", "unidades-demo"), ensure_ascii=False, indent=2)

    def question(self, status="en_recoleccion", verified=True, resultado=None):
        if resultado is None:
            resultado = f"```estimate-json\n{self.estimate_json()}\n```"
        p = self.proj / "Preguntas" / "Q-9001.md"
        p.write_text(QUESTION.format(status=status, verif=CLEAN if verified else "", resultado=resultado),
                     encoding="utf-8")
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
    vault.validation(0.62, 0.85)
    code, _, err = cli(capsys, "apply", "--vault", str(vault.root), "--question", "Q-9001",
                       "--to", "estimada", "--by", "tester", "--evidence", "estimado")
    assert code == 3
    assert "clasif-demo" in err
    assert "status: en_recoleccion" in fm(vault.proj / "Preguntas" / "Q-9001.md")


def test_estimada_passes_with_full_gate(vault, capsys):
    p = vault.question(status="en_recoleccion")
    g = ss.gate(vault.root, "Q-9001")
    assert g == {"applies": True, "ok": True, "missing": []}
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
    vault.question(resultado="")
    assert any("M-9002" in m and "Mediciones" in m for m in missing(vault))


def test_gate_classifier_validation_missing(vault):
    vault.question()
    (vault.proj / "Datos" / "anotacion" / "muestra-demo" / "validacion-clasif-demo.json").unlink()
    assert any("clasif-demo" in m and "validacion-clasif-demo.json" in m for m in missing(vault))


def test_gate_recall_below(vault):
    vault.question()
    vault.validation(0.95, 0.5)
    assert any("clasif-demo" in m and "recall_lo" in m for m in missing(vault))


def test_gate_verifier(vault):
    vault.question(verified=False)
    assert any("verific" in m for m in missing(vault))


def test_gate_no_notice_registry(vault):
    vault.question()
    (vault.proj / "Avisos" / "registro.json").unlink()
    assert "sin registro de avisos" in missing(vault)


def test_gate_not_applicable_to_hypothesis(vault):
    assert ss.gate(vault.root, "H-0001") == {"applies": False, "ok": True, "missing": []}


def test_gate_names_match_registry():
    assert set(study_gates.GATES) == {"plan_frozen", "estimates_reproducible", "classifiers_validated",
                                      "verifier_no_errors", "avisos_cerrados"}


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
    assert '"min_precision_lo": 0.80' in packet and "frozen_commit: abc1234" in packet
    assert "## Historia" not in packet and "Nada sensible" not in packet
    assert "verifications" not in packet and "status: en_recoleccion" not in packet
    assert "u1" not in packet


def test_question_packet_needs_one_mode(vault, capsys):
    vault.question()
    assert verifier_packet.main(["--vault", str(vault.root)]) == 2
    assert verifier_packet.main(["--vault", str(vault.root), "--question", "Q-9001",
                                 "--note", str(vault.proj / "Preguntas" / "Q-9001.md")]) == 2
