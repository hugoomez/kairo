"""Builds a small invented vault in a temp dir, for the tests of similar_work.py,
next_steps.py and scripts/estimate/hypothesis_cost.py; `StudyVault` builds an invented
study (questions, plans, a units dataset, notices) in a real git repo, for the tests of
plan amendments, exploratory questions and the notices gate.

Invented ids (PROJ-900, H-9001…, E-9001…, P-9001…, Q-9101…) and invented text only."""

from __future__ import annotations

import hashlib
import json
import subprocess
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


STUDY_QUESTION = """---
id: {qid}
project: PROJ-901
design: {design}
status: {status}
updated: 2031-01-01
linea_publicacion: true
linked_plan: {plan}
units_dataset: {units}
{verif}history: []
---

# {qid} — Pregunta inventada

## Pregunta

¿Qué proporción de las unidades inventadas tiene la marca X?

## Población y marco

Unidades inventadas.

## Estimandos

Una proporción.

## Resultado

{resultado}
"""

STUDY_PLAN = """---
id: {pid}
project: PROJ-901
question: {qid}
status: borrador
frozen_at: <YYYY-MM-DDTHH:MM:SSZ>
frozen_commit: <git-sha>
---

# {pid} — Plan inventado

## Población y marco muestral

Unidades inventadas.

## Estimandos

```json
{estimands}
```

## Clasificadores

```json
{{"classifiers": []}}
```

## Intervalos

```json
{intervals}
```

## Ética

Nada sensible: datos inventados.
"""

STUDY_UNITS = "uid,marca,grupo\nu1,1,a\nu2,0,a\nu3,1,b\nu4,1,b\nu5,0,a\nu6,1,b\nu7,0,b\nu8,1,a\n"
STUDY_ESTIMANDS = [{"id": "est-1", "type": "proporcion", "column": "marca", "filter": {}, "cluster": None}]
STUDY_INTERVALS = {"ci": 0.95, "bootstrap": 200, "seed": 11}


class StudyVault:
    """An invented study project (`Projects/demo`, PROJ-901) in a real git repo."""

    def __init__(self, root: Path):
        self.root = root
        self.proj = root / "Projects" / "demo"
        for d in ("Preguntas", "Mediciones", "Exploratorias", "Datos", "Avisos"):
            (self.proj / d).mkdir(parents=True, exist_ok=True)
        (self.proj / "_hub.md").write_text("---\nid: PROJ-901\nname: Demo inventado\ntype: ciencia\n---\n",
                                           encoding="utf-8")
        (self.proj / "Datos" / "registro.json").write_text("[]", encoding="utf-8")
        self.git("init", "-q")
        self.commit("hub")

    def git(self, *args) -> str:
        r = subprocess.run(["git", "-c", "user.name=tester", "-c", "user.email=t@example.invalid", *args],
                           cwd=self.root, capture_output=True, text=True, check=True)
        return r.stdout.strip()

    def commit(self, msg: str, *paths: str) -> str:
        """Commit everything (or only `paths`, relative to the vault)."""
        if paths:
            self.git("add", "--", *paths)
            self.git("commit", "-q", "-m", msg, "--", *paths)
        else:
            self.git("add", "-A")
            self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD")

    def register_units(self, name: str = "unidades-demo", text: str = STUDY_UNITS) -> str:
        """Writes Datos/u.csv and adds `name` to Datos/registro.json (not committed)."""
        (self.proj / "Datos" / "u.csv").write_text(text, encoding="utf-8", newline="")
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        reg_path = self.proj / "Datos" / "registro.json"
        reg = [d for d in json.loads(reg_path.read_text(encoding="utf-8")) if d.get("name") != name]
        reg.append({"name": name, "kind": "file", "path": "Datos/u.csv", "sha256": sha})
        reg_path.write_text(json.dumps(reg), encoding="utf-8")
        return sha

    def notices(self, *items) -> None:
        rows = []
        for i, it in enumerate(items, 1):
            it = {"estado": it} if isinstance(it, str) else it
            rows.append({"id": f"AV-{i}", "destinatario": "nadie", "canal": "correo", "enviado": "",
                         "embargo_hasta": "", "respuesta": "", **it})
        (self.proj / "Avisos" / "registro.json").write_text(json.dumps(rows), encoding="utf-8")

    def question(self, qid: str = "Q-9101", design: str = "medicion", status: str = "abierta", plan: str = "",
                 units: str = "", resultado: str = "<vacío>", verif: str = "", folder: str | None = None,
                 extra_body: str = "") -> Path:
        folder = folder or ("Exploratorias" if qid.startswith("QE-") else "Preguntas")
        p = self.proj / folder / f"{qid}.md"
        p.write_text(STUDY_QUESTION.format(qid=qid, design=design, status=status, plan=plan, units=units,
                                           resultado=resultado, verif=verif) + extra_body, encoding="utf-8")
        return p

    def plan(self, pid: str = "M-9102", qid: str = "Q-9101", estimands=None, intervals=None,
             folder: str | None = None) -> Path:
        folder = folder or ("Exploratorias" if pid.startswith("ME-") else "Mediciones")
        p = self.proj / folder / f"{pid}.md"
        p.write_text(STUDY_PLAN.format(
            pid=pid, qid=qid, estimands=json.dumps({"estimands": estimands or STUDY_ESTIMANDS}),
            intervals=json.dumps(intervals or STUDY_INTERVALS)), encoding="utf-8")
        return p

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()


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
