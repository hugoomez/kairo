"""Tests for freeze_plan.py. Invented vault only (PROJ-901, Q-9001, M-9002),
written under tmp_path, in a real git repo."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import freeze_plan as fp  # noqa: E402
import study_gates  # noqa: E402
from notes import read_note  # noqa: E402

QUESTION = """---
id: Q-9001
project: PROJ-901
design: medicion
status: {status}
linea_publicacion: false
linked_plan: {linked}
units_dataset:
history: []
---

# Q-9001 — Pregunta inventada

## Pregunta

¿Qué proporción de las unidades inventadas tiene la marca X?
"""

PLAN = """---
id: M-9002
project: PROJ-901
# question — la pregunta cuyo plan es éste. Exactamente una.
question: {question}
status: borrador
# frozen_at — momento en que se congeló el plan.
frozen_at: {frozen_at}
frozen_commit: <git-sha>
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
{{"classifiers": []}}
```

## Intervalos

```json
{{"ci": 0.95, "bootstrap": 200, "seed": 11}}
```

## Ética

Nada sensible: datos inventados.
"""


def git(root: Path, *args) -> str:
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
    return r.stdout.strip()


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    proj = root / "Projects" / "demo"
    for d in ("Preguntas", "Mediciones", "Datos"):
        (proj / d).mkdir(parents=True)
    (proj / "_hub.md").write_text("---\nid: PROJ-901\n---\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "config", "user.name", "tester")
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "hub")
    write(root)
    return root


def write(root: Path, status="abierta", linked="", question="Q-9001", frozen_at="<YYYY-MM-DDTHH:MM:SSZ>"):
    proj = root / "Projects" / "demo"
    (proj / "Preguntas" / "Q-9001.md").write_text(QUESTION.format(status=status, linked=linked), encoding="utf-8")
    (proj / "Mediciones" / "M-9002.md").write_text(PLAN.format(question=question, frozen_at=frozen_at),
                                                   encoding="utf-8")


def fm(path: Path) -> dict:
    return read_note(path)[0]


def test_freezes_in_two_commits_and_moves_the_question(vault):
    out = fp.freeze(vault, "Q-9001", "tester")
    proj = vault / "Projects" / "demo"
    plan, q = proj / "Mediciones" / "M-9002.md", proj / "Preguntas" / "Q-9001.md"
    pfm, qfm = fm(plan), fm(q)
    assert pfm["frozen_commit"] == out["frozen_commit"]
    assert str(pfm["frozen_at"]).endswith("Z") and pfm["status"] == "congelado"
    assert qfm["status"] == "plan_congelado" and qfm["linked_plan"] == "M-9002"
    # frozen_commit holds the plan before its frozen fields were written, and is HEAD~1
    assert git(vault, "rev-parse", "HEAD~1") == out["frozen_commit"]
    assert "frozen_commit: <git-sha>" in git(vault, "show", f"{out['frozen_commit']}:Projects/demo/Mediciones/M-9002.md")
    # everything the freeze wrote is committed; comments in the frontmatter survive
    assert git(vault, "status", "--porcelain", "--", "Projects/demo/Preguntas", "Projects/demo/Mediciones") == ""
    assert "# question — la pregunta" in plan.read_text(encoding="utf-8")
    # the gate's own freeze check agrees, apart from the units dataset that comes later
    assert study_gates.plan_frozen(vault, q, qfm) == ["sin units_dataset"]


def test_creates_an_empty_notice_registry_once_and_never_reads_it(vault):
    out = fp.freeze(vault, "Q-9001", "tester")
    reg = vault / "Projects" / "demo" / "Avisos" / "registro.json"
    assert out["avisos_creado"] is True and json.loads(reg.read_text(encoding="utf-8")) == []
    assert "sin avisos registrados todavía" in out["aviso"]


def test_an_existing_notice_registry_is_left_untouched(vault):
    reg = vault / "Projects" / "demo" / "Avisos" / "registro.json"
    reg.parent.mkdir()
    reg.write_text("NO-ES-JSON-Y-NO-SE-LEE", encoding="utf-8")
    out = fp.freeze(vault, "Q-9001", "tester")
    assert out["avisos_creado"] is False and out["aviso"] == ""
    assert reg.read_text(encoding="utf-8") == "NO-ES-JSON-Y-NO-SE-LEE"


def test_registry_is_not_committed(vault):
    fp.freeze(vault, "Q-9001", "tester")
    assert git(vault, "ls-files", "Projects/demo/Avisos") == ""


def test_other_uncommitted_files_stay_out_of_the_freeze_commits(vault):
    reg = vault / "Projects" / "demo" / "Datos" / "registro.json"
    reg.write_text('[{"name": "unidades-demo"}]', encoding="utf-8")
    fp.freeze(vault, "Q-9001", "tester")
    assert git(vault, "ls-files", "Projects/demo/Datos") == ""


def test_already_committed_plan_freezes_at_head(vault):
    write(vault, linked="M-9002")
    git(vault, "add", "-A")
    git(vault, "commit", "-q", "-m", "borrador")
    head = git(vault, "rev-parse", "HEAD")
    out = fp.freeze(vault, "Q-9001", "tester")
    assert out["frozen_commit"] == head


@pytest.mark.parametrize("kw, why", [
    ({"status": "plan_congelado"}, "abierta"),
    ({"linked": "M-9003"}, "M-9003"),
    ({"linked": "M-9002", "question": "Q-9004"}, "Q-9004"),
    ({"frozen_at": "2031-01-01T00:00:00Z"}, "ya está congelado"),
])
def test_refusals_change_nothing(vault, kw, why):
    write(vault, **kw)
    before = git(vault, "rev-parse", "HEAD")
    with pytest.raises(fp.Refused, match=why):
        fp.freeze(vault, "Q-9001", "tester")
    assert git(vault, "rev-parse", "HEAD") == before
    assert not (vault / "Projects" / "demo" / "Avisos").exists()


def test_invalid_plan_is_refused(vault):
    plan = vault / "Projects" / "demo" / "Mediciones" / "M-9002.md"
    plan.write_text(plan.read_text(encoding="utf-8").replace('"type": "proporcion"', '"type": "media"'),
                    encoding="utf-8")
    with pytest.raises(fp.Refused, match="plan M-9002"):
        fp.freeze(vault, "Q-9001", "tester")


def test_no_plan_or_several_plans(vault):
    proj = vault / "Projects" / "demo" / "Mediciones"
    (proj / "M-9002.md").unlink()
    with pytest.raises(fp.Refused, match="ningún plan"):
        fp.freeze(vault, "Q-9001", "tester")
    write(vault)
    (proj / "M-9003.md").write_text(PLAN.format(question="Q-9001", frozen_at="").replace("M-9002", "M-9003"),
                                    encoding="utf-8")
    with pytest.raises(fp.Refused, match="varios planes"):
        fp.freeze(vault, "Q-9001", "tester")


def test_vault_without_git_is_refused(tmp_path):
    root = tmp_path / "v"
    (root / "Projects" / "demo" / "Preguntas").mkdir(parents=True)
    (root / "Projects" / "demo" / "Mediciones").mkdir(parents=True)
    write(root)
    with pytest.raises(fp.Refused, match="git"):
        fp.freeze(root, "Q-9001", "tester")


def test_cli_exit_codes(vault, capsys):
    assert fp.main(["--vault", str(vault), "--question", "Q-9001", "--by", "tester"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["plan"] == "M-9002" and out["to"] == "plan_congelado"
    assert fp.main(["--vault", str(vault), "--question", "Q-9001", "--by", "tester"]) == 3
