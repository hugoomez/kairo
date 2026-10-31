"""Builds a small invented vault in a temp dir, for the tests of similar_work.py,
next_steps.py and scripts/estimate/hypothesis_cost.py.

Invented ids (PROJ-900, H-9001…, E-9001…, P-9001…) and invented text only."""

from __future__ import annotations

import json
from pathlib import Path

HUB = """---
id: {pid}
name: Proyecto inventado
type: ciencia
compute_budget: {budget}
completo_cost_threshold: <value>
ladder_cost_threshold: <value>
deadline: <YYYY-MM-DD>
---

## Propósito

Algo inventado.
"""

HYP = """---
id: {hid}
project: {pid}
status: {status}
frontier_cells: [{cells}]
{worth}linked_experiment: [{linked}]
history:
  - date: 2031-01-01
    status: propuesta
    by: tester
{history}---

## Claim

{claim}
"""

EXP = """---
id: {eid}
hypothesis: {hid}
secondary_hypotheses: [{secondary}]
project: {pid}
role: {role}
rung: {rung}
metric_kind: {metric}
status: {status}
experiment_validity: {validity}
environment:
  seed: 1
  hardware: {hardware}
cost_estimated: {cost_est}
cost_actual: {cost_act}
result:
  effect: <value>
  verdict: {verdict}
---

## Predicción

{prediction}

## Resultado

{resultado}
"""

WORTH = """vale_la_pena: {v}
vale_la_pena_razon: una razón inventada
importancia: media
dificultad: baja
coste_computo: CPU, ~1 h
coste_tiempo: una tarde
coste_base: estimado: inventado
evaluado: 2031-01-02
"""


class Vault:
    def __init__(self, root: Path):
        self.root = root
        (root / "Projects").mkdir(parents=True, exist_ok=True)
        (root / "Papers").mkdir(exist_ok=True)

    def project(self, slug="proyecto-inventado", pid="PROJ-900", budget="<value>") -> Path:
        p = self.root / "Projects" / slug
        (p / "Hipotesis").mkdir(parents=True, exist_ok=True)
        (p / "Experimentos").mkdir(exist_ok=True)
        (p / "_hub.md").write_text(HUB.format(pid=pid, budget=budget), encoding="utf-8")
        return p

    def hypothesis(self, pdir: Path, hid: str, claim: str, status="propuesta", cells=(), linked=(),
                   worth: str | None = None, history_exps=(), send_never=False) -> Path:
        hist = ""
        if history_exps:
            hist = ("  - date: 2031-02-01\n    status: en_experimento\n    by: tester\n"
                    f"    experiments: [{', '.join(history_exps)}]\n")
        w = WORTH.format(v=worth) if worth else ""
        text = HYP.format(hid=hid, pid=self.pid(pdir), status=status, cells=", ".join(cells), worth=w,
                          linked=", ".join(linked), history=hist, claim=claim)
        if send_never:
            text = text.replace("---\n", "---\nsend: never\n", 1)
        path = pdir / "Hipotesis" / f"{hid}.md"
        path.write_text(text, encoding="utf-8")
        return path

    def experiment(self, pdir: Path, eid: str, hid: str, prediction="Una predicción inventada.",
                   status="completed", role="confirmatory", rung=3, metric="proportion", validity="valid",
                   verdict="<apoyada | refutada | inconclusa | evidencia_mixta>", hardware="<e.g. 1x A100 80GB>",
                   secondary=(), cost_est="<value>", cost_act="<value>",
                   resultado="<Sección nueva, escrita por run-experiment.>") -> Path:
        path = pdir / "Experimentos" / f"{eid}.md"
        path.write_text(EXP.format(eid=eid, hid=hid, pid=self.pid(pdir), role=role, rung=rung, metric=metric,
                                   status=status, validity=validity, hardware=hardware, verdict=verdict,
                                   secondary=", ".join(secondary), cost_est=cost_est, cost_act=cost_act,
                                   prediction=prediction, resultado=resultado), encoding="utf-8")
        return path

    def digest(self, pdir: Path, rows: list[str]) -> None:
        text = "| id | claim | estado | lección |\n|----|-------|--------|---------|\n" + "\n".join(rows) + "\n"
        (pdir / "_digest.md").write_text(text, encoding="utf-8")

    def paper(self, pid_paper: str, projects: str, added: str) -> None:
        (self.root / "Papers" / f"{pid_paper}.md").write_text(
            f"---\nid: {pid_paper}\ntitle: Un paper inventado\nprojects: [{projects}]\nadded: {added}\n---\n\n## Resumen\n\nInventado.\n",
            encoding="utf-8")

    def mapa(self, pdir: Path, data: dict, yaml: bool = True) -> Path:
        f = pdir / "Frontera"
        f.mkdir(exist_ok=True)
        if yaml:
            (f / "mapa.yaml").write_text("meta:\n  proyecto: PROJ-900\n", encoding="utf-8")
        (f / "mapa.json").write_text(json.dumps(data), encoding="utf-8")
        return f / "mapa.json"

    @staticmethod
    def pid(pdir: Path) -> str:
        for line in (pdir / "_hub.md").read_text(encoding="utf-8").splitlines():
            if line.startswith("id:"):
                return line.split(":", 1)[1].strip()
        return "PROJ-900"


def mapa_example(generado="2031-03-01T00:00:00Z", nuestros=None, papers=("P-9001",), sin_extraer=(),
                 marca_cubierta=False, atacado_por=()) -> dict:
    nuestros = nuestros or []
    return {
        "proyecto": "PROJ-900", "generado": generado,
        "obsoleto": {"papers_sin_extraer": list(sin_extraer), "taxonomia_cambiada": []},
        "papers": [{"id": p} for p in papers],
        "matrices": [{
            "id": "M1", "titulo": "Matriz inventada", "pregunta": "¿?",
            "filas": [{"v": "fila_a", "desc": ""}, {"v": "fila_b", "desc": ""}],
            "cols": [{"v": "col_x", "desc": ""}, {"v": "col_y", "desc": ""}],
            "celdas": [
                [{"papers": ["P-9001"], "nuestros": nuestros, "marca": None, "por_que": None},
                 {"papers": ["P-9001"] if marca_cubierta else [], "nuestros": [], "marca": "hueco", "por_que": "inventado"}],
                [{"papers": [], "nuestros": [], "marca": "no_buscado", "por_que": "inventado"},
                 {"papers": [], "nuestros": [], "marca": "sin_sentido", "por_que": "inventado"}],
            ]}],
        "huecos": [{"id": "G1", "prioridad": 1, "titulo": "Hueco inventado", "celdas": ["M1:fila_a|col_y"],
                    "atacado_por": [{"id": a, "status": "propuesta"} for a in atacado_por]}],
    }
