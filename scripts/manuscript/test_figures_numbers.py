"""Tests for figures.py and numbers_check.py. Invented vault only (PROJ-901, Q-9001,
M-9002), written under tmp_path; no network."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import figures  # noqa: E402
import manuscript as ms  # noqa: E402
import numbers_check as nc  # noqa: E402

EST = {"plan": "M-9002", "units_sha256": "0" * 64, "estimates": [
    {"id": "est-1", "type": "proporcion", "value": 0.1234, "lo": 0.0981, "hi": 0.1529, "n": 1234,
     "method": "wilson"},
    {"id": "est-2", "type": "diferencia", "value": -0.052, "lo": -0.101, "hi": 0.004, "n": 48,
     "method": "newcombe", "a": {"group": "f1", "n": 20, "value": 0.3}, "b": {"group": "f2", "n": 28, "value": 0.352}},
]}

QUESTION = """---
id: Q-9001
project: PROJ-901
design: medicion
status: estimada
linea_publicacion: true
linked_plan: M-9002
units_dataset: unidades-demo
history: []
---

# Q-9001 — Pregunta inventada

## Pregunta

¿Qué proporción de las unidades inventadas tiene la marca X?

## Resultado

Tabla inventada.

```estimate-json
{est}
```

## Historia

Nada.
"""

PLAN = """---
id: M-9002
project: PROJ-901
question: Q-9001
status: congelado
frozen_at: 2031-01-02T10:00:00Z
frozen_commit: abc1234
---

# M-9002 — Plan inventado

## Estimandos

```json
{"estimands": [{"id": "est-1", "type": "proporcion", "column": "marca", "filter": {}, "cluster": null}]}
```

## Clasificadores

```json
{"classifiers": []}
```

## Intervalos

```json
{"ci": 0.95, "bootstrap": 2000, "seed": 11}
```

## Tamaño de muestra

```json
{"n_objetivo": 1500}
```
"""


def _git(root: Path, *args) -> str:
    return subprocess.run(["git", "-c", "user.name=tester", "-c", "user.email=t@example.invalid", *args],
                          cwd=root, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def vault(tmp_path, monkeypatch):
    for k, v in (("GIT_AUTHOR_NAME", "tester"), ("GIT_AUTHOR_EMAIL", "t@example.invalid"),
                 ("GIT_COMMITTER_NAME", "tester"), ("GIT_COMMITTER_EMAIL", "t@example.invalid")):
        monkeypatch.setenv(k, v)
    root = tmp_path / "vault"
    proj = root / "Projects" / "demo"
    for d in ("Preguntas", "Mediciones", "Datos"):
        (proj / d).mkdir(parents=True)
    (proj / "_hub.md").write_text("---\nid: PROJ-901\n---\n# Demo\n", encoding="utf-8")
    (proj / "Datos" / "registro.json").write_text("[]", encoding="utf-8")
    write_question(proj, EST)
    (proj / "Mediciones" / "M-9002.md").write_text(PLAN, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


def write_question(proj: Path, est: dict) -> None:
    (proj / "Preguntas" / "Q-9001.md").write_text(
        QUESTION.format(est=json.dumps(est, ensure_ascii=False, indent=2)), encoding="utf-8")


def fig(tmp_path, name="barras.png", data=b"\x89PNG\r\n\x1a\nfigura inventada"):
    d = tmp_path / "render"
    d.mkdir(exist_ok=True)
    (d / name).write_bytes(data)
    return str(d / name)


def cli(capsys, *args):
    code = figures.main(list(args))
    return code, json.loads(capsys.readouterr().out)


def reg_args(vault, f, *extra):
    return ("register", "--vault", str(vault), "--project", "demo", "--file", f, "--question", "Q-9001",
            "--script", "figuras/barras.py", "--commit", "a1b2c3d", "--caption", "Proporción inventada.",
            *extra)


# ---- figures ---------------------------------------------------------------------

def test_register_check_and_obsolete(vault, tmp_path, capsys):
    proj = vault / "Projects" / "demo"
    out = tmp_path / "est.json"
    code, ex = cli(capsys, "extract-json", "--vault", str(vault), "--question", "Q-9001", "--out", str(out))
    assert code == 0 and json.loads(out.read_text(encoding="utf-8")) == EST
    assert ex["estimate_sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()

    f = fig(tmp_path)
    code, res = cli(capsys, *reg_args(vault, f, "--wide", "--by", "agente-x"))
    assert code == 0, res
    e = res["figure"]
    assert e["id"] == "F-001" and e["path"] == "Figuras/F-001-barras.png" and e["wide"] is True
    assert e["sha256"] == hashlib.sha256(Path(f).read_bytes()).hexdigest()
    assert e["source"] == {"question": "Q-9001", "estimate_sha256": ex["estimate_sha256"]}
    assert (e["script"], e["commit"], e["by"]) == ("figuras/barras.py", "a1b2c3d", "agente-x")
    assert (proj / e["path"]).read_bytes() == Path(f).read_bytes()
    # committed: only the registry and the figure
    files = _git(vault, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(files) == ["Projects/demo/Figuras/F-001-barras.png", "Projects/demo/Figuras/registro.json"]
    assert res["commit"] == _git(vault, "rev-parse", "HEAD")
    reg = json.loads((proj / "Figuras" / "registro.json").read_text(encoding="utf-8"))
    for k in ("id", "path", "sha256", "caption", "wide"):     # the exporter's fields
        assert k in reg[0]

    assert cli(capsys, "check", "--vault", str(vault), "--project", "demo")[0] == 0
    assert cli(capsys, "list", "--vault", str(vault), "--project", "demo")[1]["figures"] == reg

    # the estimate changes: the figure is obsolete
    est2 = json.loads(json.dumps(EST))
    est2["estimates"][0]["value"] = 0.2
    write_question(proj, est2)
    code, res = cli(capsys, "check", "--vault", str(vault), "--project", "demo")
    assert code == 4 and res["obsoletas"] == ["F-001"] and res["figures"][0]["status"] == "obsoleta"

    # re-rendered and registered again with the same id: ok again, one entry
    f2 = fig(tmp_path, data=b"\x89PNG\r\n\x1a\nfigura nueva")
    code, res = cli(capsys, *reg_args(vault, f2, "--id", "F-001"))
    assert code == 0 and res["replaced"] is True
    assert cli(capsys, "check", "--vault", str(vault), "--project", "demo", "--id", "F-001")[0] == 0
    assert len(json.loads((proj / "Figuras" / "registro.json").read_text(encoding="utf-8"))) == 1

    # a changed file is reported too
    (proj / "Figuras" / "F-001-barras.png").write_bytes(b"otra cosa")
    code, res = cli(capsys, "check", "--vault", str(vault), "--project", "demo")
    assert code == 4 and res["figures"][0]["problems"] == ["fichero_cambiado"]


def test_register_refusals(vault, tmp_path, capsys):
    proj = vault / "Projects" / "demo"
    assert cli(capsys, *reg_args(vault, fig(tmp_path, "a.svg")))[0] == 3            # svg refused
    assert cli(capsys, *reg_args(vault, "render/a.png"))[0] == 3                   # relative
    f = fig(tmp_path)
    bad = list(reg_args(vault, f))
    bad[bad.index("a1b2c3d")] = "no-es-un-sha"
    assert cli(capsys, *bad)[0] == 3
    assert cli(capsys, *reg_args(vault, f, "--id", "F-1"))[0] == 3
    bad = list(reg_args(vault, f))
    bad[bad.index("figuras/barras.py")] = str(tmp_path / "abs.py")
    assert cli(capsys, *bad)[0] == 3
    # no estimate-json block: refused, nothing written
    (proj / "Preguntas" / "Q-9001.md").write_text(QUESTION.replace("```estimate-json\n{est}\n```", "Nada."),
                                                  encoding="utf-8")
    code, res = cli(capsys, *reg_args(vault, f))
    assert code == 3 and "estimate-json" in res["error"]
    assert not (proj / "Figuras").exists()


def test_no_commit_and_sensitive_file(vault, tmp_path, capsys):
    proj = vault / "Projects" / "demo"
    code, res = cli(capsys, *reg_args(vault, fig(tmp_path), "--no-commit"))
    assert code == 0 and res["commit"] is None
    assert "Figuras" in _git(vault, "status", "--porcelain", "--untracked-files=all")
    secret = tmp_path / "sensible"
    secret.mkdir()
    (secret / "x.png").write_bytes(b"\x89PNG")
    (proj / "Datos" / "registro.json").write_text(json.dumps(
        [{"name": "s", "kind": "dir", "path": str(secret), "sha256": "0", "sensitive": True}]), encoding="utf-8")
    code, res = cli(capsys, *reg_args(vault, str(secret / "x.png"), "--no-commit"))
    assert code == 3 and "sensible" in res["error"]


# ---- numbers_check ----------------------------------------------------------------

RESULTS = ("En 2031 se midieron 1,234 unidades (Figura 1). La proporción con la marca fue "
           "12,3 % [9,8; 15,3 %], con un IC del 95 %; la diferencia entre formatos fue −0.05 "
           "[−0.10, 0.004] (n = 48; 20 y 28 por grupo). El objetivo era 1500 unidades (P-0001). {extra}")


def manuscript(vault: Path, results: str, discussion: str = "La cifra 77 sale de la nada.") -> Path:
    proj = vault / "Projects" / "demo"
    ms.init(proj, "hilo", "Título inventado", None, design="medicion", lang="es")
    ms.bind(proj, "hilo", "results", "Q-9001")
    ms.bind(proj, "hilo", "discussion", "Q-9001")
    m = proj / "Manuscritos" / "manuscript-hilo.md"
    text = m.read_text(encoding="utf-8")
    text = text.replace("<!-- kairo:section results -->\n" + ms.PLACEHOLDER["resultado"],
                        "<!-- kairo:section results -->\n" + results)
    text = text.replace("<!-- kairo:section discussion -->\n" + ms.PLACEHOLDER["cierre"],
                        "<!-- kairo:section discussion -->\n" + discussion)
    text = text.replace("<!-- kairo:section methodology -->\n" + ms.PLACEHOLDER["prosa"],
                        "<!-- kairo:section methodology -->\nTexto con 999 sin vincular.")
    m.write_text(text, encoding="utf-8")
    return proj


def test_numbers_traced_and_untraced(vault, capsys):
    proj = manuscript(vault, RESULTS.format(extra="Hubo 3,7 fallos."))
    res = nc.run(vault, proj, "hilo")
    by = {(i["section"], i["token"]): i for i in res["items"]}
    assert by[("results", "12,3 %")]["source"] == "estimate-json"
    assert by[("results", "9,8")]["source"] == "estimate-json"        # percent from its partner
    assert by[("results", "95 %")]["source"] == "plan"
    assert by[("results", "1500")]["source"] == "plan"
    assert by[("results", "2031")]["source"] == "año"
    assert by[("results", "1")]["source"] == "referencia"
    assert by[("results", "−0.05")]["source"] == "estimate-json"
    assert by[("results", "3,7")]["status"] == "untraced"
    assert by[("discussion", "77")]["status"] == "untraced"
    assert not any(i["token"] in ("0001", "999") for i in res["items"])   # ids and unbound sections
    assert [s["id"] for s in res["sections"] if s["checked"]] == ["results", "discussion"]
    assert (res["errors"], res["warnings"]) == (1, 1)
    assert nc.main(["--vault", str(vault), "--project-dir", str(proj), "--thread", "hilo", "--json"]) == 4
    out = json.loads(capsys.readouterr().out)
    assert out["errors"] == 1


def test_allowlist_needs_a_reason(vault, capsys):
    proj = manuscript(vault, RESULTS.format(extra="Hubo 3,7 fallos."))
    (proj / "Manuscritos" / "numeros-permitidos.txt").write_text(
        "# cifras que no salen de estimate-json\n3,7\tfallos contados a mano en el registro de incidencias\n77\n",
        encoding="utf-8")
    res = nc.run(vault, proj, "hilo")
    assert res["errors"] == 0 and res["warnings"] == 1          # 77 has no reason: still untraced
    assert res["problems"] == [{"allowlist_line": 3, "problem": "sin motivo: la entrada no cuenta"}]
    assert nc.main(["--vault", str(vault), "--project-dir", str(proj), "--thread", "hilo"]) == 0
    assert "aviso [discussion]" in capsys.readouterr().out


def test_question_without_block_makes_every_number_untraced(vault):
    proj = manuscript(vault, "La proporción fue 0.1234.", discussion="Nada que discutir.")
    (proj / "Preguntas" / "Q-9001.md").write_text(QUESTION.replace("```estimate-json\n{est}\n```", "Nada."),
                                                  encoding="utf-8")
    res = nc.run(vault, proj, "hilo")
    assert res["errors"] == 1
    assert {"question": "Q-9001", "problem": "sin bloque estimate-json en ## Resultado"} in res["problems"]


def test_readings_and_rounding():
    assert nc.readings("1,234") == [(1234.0, 0), (1.234, 3)]
    assert nc.readings("12.345,6") == [(12345.6, 1)]
    assert nc.readings("12 345") == [(12345.0, 0)]
    pool = {0.1234, 2.7}
    assert nc._matches(12.0, 0, True, pool)[0] and nc._matches(0.12, 2, False, pool)[0]
    assert not nc._matches(3.0, 0, False, pool)[0]                   # a small bare integer: exact only
    assert not nc._matches(0.13, 2, False, pool)[0]
