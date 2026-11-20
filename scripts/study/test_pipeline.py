"""Tests for pipeline.py. Invented vault, code repository and data, all under tmp_path."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "ledger"))
sys.path.insert(0, str(HERE.parent / "security"))

import data_guard  # noqa: E402
import lineage  # noqa: E402
import pipeline  # noqa: E402

SCRIPT = HERE / "pipeline.py"
GIT_ENV = {"GIT_AUTHOR_NAME": "tester", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "tester", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
MARK = "SALIDA-DE-LA-ETAPA-INVENTADA"

PARSE = f"""import json, os, sys
src, out = sys.argv[1], sys.argv[2]
rows = []
for d, _dirs, files in os.walk(src):
    for f in sorted(files):
        with open(os.path.join(d, f), encoding="utf-8") as fh:
            rows += [json.loads(x) for x in fh if x.strip()]
rows.sort(key=lambda r: r["uid"])
with open(out, "w", encoding="utf-8", newline="\\n") as fh:
    fh.writelines(json.dumps(r) + "\\n" for r in rows)
print("{MARK}", rows[0])
"""

CLASSIFY = """import json, sys
src, out, params = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
with open(src, encoding="utf-8") as fh:
    rows = [json.loads(x) for x in fh if x.strip()]
with open(out, "w", encoding="utf-8", newline="\\n") as fh:
    fh.write("uid,marca\\n")
    for r in rows:
        fh.write(f"{r['uid']},{int(r['valor'] >= params['umbral'])}\\n")
"""


def _git(cwd: Path, *args) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _toml(env, *, parse_out=None, extra="", timeout_line="") -> str:
    py = sys.executable.replace("\\", "/")
    fuera = str(env["fuera"]).replace("\\", "/")
    parse_out = parse_out or f"{fuera}/parseado.jsonl"
    return f"""
[[stage]]
name = "parseado"
script = ["{py}", "src/parse.py", "{{input:crudo}}", "{{output}}"]
inputs = ["crudo"]
output = "{parse_out}"
{timeout_line}

[[stage]]
name = "unidades"
script = ["{py}", "src/classify.py", "{{input:parseado}}", "{{output}}", "{{params}}"]
inputs = ["parseado"]
output = "{fuera}/unidades.csv"
params = {{ umbral = 2 }}
{extra}
"""


def _commit_repo(env, msg="código"):
    _git(env["repo"], "add", "-A")
    _git(env["repo"], "commit", "-q", "-m", msg)
    return _git(env["repo"], "rev-parse", "HEAD")


@pytest.fixture
def env(tmp_path, monkeypatch):
    for k, v in GIT_ENV.items():
        monkeypatch.setenv(k, v)
    vault = tmp_path / "vault"
    datos = vault / "Projects" / "demo" / "Datos"
    datos.mkdir(parents=True)
    _git(vault, "init", "-q")
    fuera = tmp_path / "fuera"
    crudo = fuera / "crudo"
    for i in range(4):
        (crudo / f"lote-{i}").mkdir(parents=True)
        (crudo / f"lote-{i}" / "items.jsonl").write_text(json.dumps({"uid": f"u{i}", "valor": i}) + "\n",
                                                          encoding="utf-8")
    fp = lineage.fingerprint(crudo)
    reg = datos / "registro.json"
    reg.write_text(json.dumps([{"name": "crudo", "path": str(crudo).replace("\\", "/"), **fp,
                                "registered_at": "2031-01-01T00:00:00.000Z", "by": "tester",
                                "sensitive": True}], indent=1), encoding="utf-8")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-q", "-m", "registro")
    repo = tmp_path / "codigo"
    (repo / "src").mkdir(parents=True)
    _git(repo, "init", "-q")
    (repo / "src" / "parse.py").write_text(PARSE, encoding="utf-8")
    (repo / "src" / "classify.py").write_text(CLASSIFY, encoding="utf-8")
    e = {"vault": vault, "reg": reg, "fuera": fuera, "crudo": crudo, "repo": repo, "file": repo / "pipeline.toml",
         "runs": datos / "pipeline-runs.jsonl"}
    e["file"].write_text(_toml(e), encoding="utf-8")
    e["commit"] = _commit_repo(e)
    data_guard._MEMO.clear()
    return e


def _run(env, **kw):
    return pipeline.run(env["vault"], "demo", env["file"], **kw)


def _registry(env):
    return json.loads(env["reg"].read_text(encoding="utf-8"))


def _records(env):
    return [json.loads(x) for x in env["runs"].read_text(encoding="utf-8").splitlines()]


def test_run_registers_lineage_and_records(env):
    code, res = _run(env)
    assert code == 0, res
    reg = {e["name"]: e for e in _registry(env)}
    p, u = reg["parseado"], reg["unidades"]
    assert p["derived_from"] == [{"name": "crudo", "sha256": reg["crudo"]["sha256"]}]
    assert u["derived_from"] == [{"name": "parseado", "sha256": p["sha256"]}]
    assert p["script"] == "src/parse.py" and u["script"] == "src/classify.py"
    assert p["commit"] == u["commit"] == env["commit"]
    assert u["params"]["umbral"] == 2 and u["params"]["argv"][1] == "src/classify.py"
    assert p["sensitive"] is True and u["sensitive"] is True          # inherited
    units = env["fuera"] / "unidades.csv"
    assert u["sha256"] == _sha(units.read_bytes())
    assert units.read_text(encoding="utf-8") == "uid,marca\nu0,0\nu1,0\nu2,1\nu3,1\n"
    recs = _records(env)
    assert [r["stage"] for r in recs] == ["parseado", "unidades"]
    assert all(r["estado"] == "ok" and r["exit_code"] == 0 and r["commit"] == env["commit"] for r in recs)
    assert recs[1]["inputs"] == [{"name": "parseado", "sha256": p["sha256"]}]
    assert recs[1]["output_sha256"] == u["sha256"]
    # the stage's output goes to its log, never into the vault or onto stdout
    log = Path(str(env["fuera"] / "parseado.jsonl") + ".log")
    assert MARK in log.read_text(encoding="utf-8")
    for f in (env["reg"], env["runs"]):
        assert MARK not in f.read_text(encoding="utf-8")
    assert MARK not in json.dumps(res)
    # one vault commit per stage, registry and runs file only
    files = _git(env["vault"], "show", "--name-only", "--format=", "HEAD").splitlines()
    assert sorted(files) == ["Projects/demo/Datos/pipeline-runs.jsonl", "Projects/demo/Datos/registro.json"]
    assert "unidades" in _git(env["vault"], "log", "-1", "--format=%s")


def test_status_up_to_date_then_stale(env):
    assert [s["estado"] for s in pipeline.status(env["vault"], "demo", env["file"])["etapas"]] == \
        ["sin correr", "sin correr"]
    _run(env)
    st = pipeline.status(env["vault"], "demo", env["file"], rehash=True)
    assert st["al_dia"] is True and [s["estado"] for s in st["etapas"]] == ["al día", "al día"]
    (env["repo"] / "src" / "parse.py").write_text(PARSE.replace("rows.sort(", "rows.sort(reverse=True, "),
                                                  encoding="utf-8")
    _commit_repo(env, "arregla el parser")
    st = pipeline.status(env["vault"], "demo", env["file"])
    assert st["al_dia"] is False
    assert any("el código cambió" in m for m in st["etapas"][0]["motivos"])
    code, _ = _run(env, stage="parseado")
    assert code == 0
    st = pipeline.status(env["vault"], "demo", env["file"])
    assert st["etapas"][0]["estado"] == "al día"
    assert any("«parseado» cambió" in m for m in st["etapas"][1]["motivos"])
    code, _ = _run(env, start="unidades")
    assert code == 0
    assert pipeline.status(env["vault"], "demo", env["file"])["al_dia"] is True
    # the re-derived table replaced its own entry; nothing else was rewritten
    assert [e["name"] for e in _registry(env)] == ["crudo", "parseado", "unidades"]
    assert lineage.verify(env["vault"], "demo", "unidades")["ok"] is True


def test_dirty_repo_refused(env):
    (env["repo"] / "src" / "parse.py").write_text(PARSE + "\n# cambio\n", encoding="utf-8")
    with pytest.raises(lineage.Refused, match="cambios sin commit"):
        _run(env)
    assert not env["runs"].exists()


def test_failing_stage_stops_and_is_recorded(env):
    (env["repo"] / "src" / "parse.py").write_text("import sys\nprint('roto')\nsys.exit(5)\n", encoding="utf-8")
    _commit_repo(env, "rompe")
    code, res = _run(env)
    assert code == 2 and res["detenido_en"] == "parseado"
    recs = _records(env)
    assert len(recs) == 1 and recs[0]["estado"] == "fallo" and recs[0]["exit_code"] == 5
    assert recs[0]["output_sha256"] is None
    assert [e["name"] for e in _registry(env)] == ["crudo"]
    assert "pipeline-runs.jsonl" in _git(env["vault"], "show", "--name-only", "--format=", "HEAD")


def test_timeout(env):
    (env["repo"] / "src" / "parse.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    env["file"].write_text(_toml(env, timeout_line="timeout = 1"), encoding="utf-8")
    _commit_repo(env, "lento")
    code, res = _run(env, do_commit=False)
    assert code == 2 and res["etapas"][0]["estado"] == "tiempo agotado"


def test_input_drift_refused(env):
    (env["crudo"] / "lote-0" / "items.jsonl").write_text('{"uid": "u0", "valor": 99}\n', encoding="utf-8")
    with pytest.raises(lineage.Refused, match="no coincide con el registro"):
        _run(env)


def test_stage_that_modifies_repo_fails(env):
    (env["repo"] / "src" / "parse.py").write_text(
        PARSE + "\nopen('src/nuevo.py', 'w').write('x = 1\\n')\n", encoding="utf-8")
    _commit_repo(env, "escribe en el repo")
    code, res = _run(env, do_commit=False)
    assert code == 2 and res["etapas"][0]["estado"] == "repositorio modificado"
    assert [e["name"] for e in _registry(env)] == ["crudo"]


def test_output_in_repo_must_be_ignored(env):
    out = str(env["repo"] / "datos" / "parseado.jsonl").replace("\\", "/")
    env["file"].write_text(_toml(env, parse_out=out), encoding="utf-8")
    _commit_repo(env, "salida en el repo")
    with pytest.raises(lineage.Refused, match="ignore"):
        _run(env)
    (env["repo"] / ".gitignore").write_text("datos/\n", encoding="utf-8")
    _commit_repo(env, "ignora datos")
    code, _ = _run(env, do_commit=False)
    assert code == 0


@pytest.mark.parametrize("toml,match", [
    ('[[stage]]\nname = "a"\nscript = "python a.py"\ninputs = ["crudo"]\noutput = "/x/a"\n', "argv"),
    ('[[stage]]\nname = "a"\nscript = ["p"]\ninputs = ["b"]\noutput = "/x/a"\n'
     '[[stage]]\nname = "b"\nscript = ["p"]\ninputs = ["crudo"]\noutput = "/x/b"\n', "antes de que se declare"),
    ('[[stage]]\nname = "a"\nscript = ["p", "{input:otro}"]\ninputs = ["crudo"]\noutput = "/x/a"\n',
     "no es una de sus entradas"),
    ('[[stage]]\nname = "a"\nscript = ["p"]\ninputs = ["crudo"]\noutput = "relativa"\n', "absoluta"),
    ('[[stage]]\nname = "a"\nscript = ["p"]\ninputs = ["crudo"]\noutput = "/x/a"\nshell = true\n', "desconocidas"),
    ('[[stage]]\nname = "a"\nscript = ["p"]\ninputs = ["crudo"]\noutput = "/x/a"\n'
     '[[stage]]\nname = "A"\nscript = ["p"]\ninputs = ["crudo"]\noutput = "/x/b"\n', "repetido"),
    ('[[stage]]\nname = "a"\nscript = ["p"]\ninputs = ["crudo"]\noutput = "/x/a"\n'
     '[[stage]]\nname = "b"\nscript = ["p"]\ninputs = ["crudo"]\noutput = "/x/a/b"\n', "se solapa"),
])
def test_bad_pipeline_refused(tmp_path, toml, match):
    f = tmp_path / "p.toml"
    f.write_text(toml.replace('"/x/', f'"{tmp_path.as_posix()}/x/'), encoding="utf-8")
    with pytest.raises(lineage.Refused, match=match):
        pipeline.load(f)


def test_cli(env):
    r = subprocess.run([sys.executable, str(SCRIPT), "run", "--vault", str(env["vault"]), "--project", "demo",
                        "--file", str(env["file"]), "--no-commit"], capture_output=True)
    assert r.returncode == 0, r.stdout
    out = r.stdout.decode("utf-8")
    assert MARK not in out and json.loads(out)["commit"] == env["commit"]
    r = subprocess.run([sys.executable, str(SCRIPT), "status", "--vault", str(env["vault"]), "--project", "demo",
                        "--file", str(env["file"])], capture_output=True)
    assert r.returncode == 0 and json.loads(r.stdout.decode("utf-8"))["al_dia"] is True
    r = subprocess.run([sys.executable, str(SCRIPT), "run", "--vault", str(env["vault"]), "--project", "demo",
                        "--file", str(env["file"]), "--stage", "nada"], capture_output=True)
    assert r.returncode == 3 and "error" in json.loads(r.stdout.decode("utf-8"))


def test_data_guard_allows_the_pipeline_on_sensitive_data(env):
    _run(env)                                   # crudo, parseado and unidades are now all sensitive
    data_guard._MEMO.clear()
    v = env["vault"]
    fuera = [r for r in data_guard.sensitive_roots(v) if "fuera" in str(r)]
    assert len([r for r in fuera if r.suffix != ".log"]) == 3   # each sensitive output …
    assert len([r for r in fuera if r.suffix == ".log"]) == 3   # … and its stage log
    for mode in ("run --from unidades", "status --rehash"):
        m, rest = mode.split(" ", 1)
        cmd = f'python "{SCRIPT}" {m} --vault "{v}" --project demo --file "{env["file"]}" {rest}'
        for cwd in (env["repo"], v):
            ev = {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(cwd)}
            assert data_guard.decide(ev, v) is None, (cmd, cwd)
    # reading a registered output stays blocked
    ev = {"tool_name": "Read", "tool_input": {"file_path": str(env["fuera"] / "unidades.csv")}, "cwd": str(v)}
    assert data_guard.decide(ev, v)
