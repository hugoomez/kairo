"""Tests for lineage.py and the lineage rule of study_gates.plan_frozen. Invented vault,
datasets and code (PROJ-901 «demo», Q-9001, M-9002), all under tmp_path."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "security"))

import data_guard  # noqa: E402
import lineage  # noqa: E402
import study_gates  # noqa: E402
import study_status as ss  # noqa: E402
from notes import read_note  # noqa: E402
from test_study_status import Vault  # noqa: E402

SCRIPT = HERE / "lineage.py"
GIT_ENV = {"GIT_AUTHOR_NAME": "tester", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "tester", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _app_dir_sha(root: Path) -> str:
    """The app's folder fingerprint, written independently of lineage.py."""
    lines = []
    for p in root.rglob("*"):
        if p.is_file() and ".git" not in p.relative_to(root).parts:
            lines.append(f"{_sha(p.read_bytes())}  {p.relative_to(root).as_posix()}")
    return _sha(("\n".join(sorted(lines)) + "\n").encode())


def _git(cwd: Path, *args) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


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
    for i in range(3):
        (crudo / f"lote-{i}").mkdir(parents=True)
        (crudo / f"lote-{i}" / "items.jsonl").write_text(f'{{"uid": "u{i}", "valor": {i}}}\n', encoding="utf-8")
    (crudo / ".git").mkdir()
    (crudo / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    otro = fuera / "otro.csv"
    otro.write_text("a,b\n1,2\n", encoding="utf-8")
    entries = [
        {"name": "crudo", "path": str(crudo).replace("\\", "/"), "kind": "dir", "files": 3, "bytes": 0,
         "sha256": _app_dir_sha(crudo), "registered_at": "2031-01-01T00:00:00.000Z", "by": "tester",
         "sensitive": True},
        {"name": "otro", "path": str(otro).replace("\\", "/"), "kind": "file", "files": 1, "bytes": 8,
         "sha256": _sha(otro.read_bytes()), "registered_at": "2031-01-01T00:00:00.000Z", "by": "tester",
         "sensitive": False},
    ]
    reg = datos / "registro.json"
    reg.write_text(json.dumps(entries, indent=1), encoding="utf-8")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-q", "-m", "registro")
    data_guard._MEMO.clear()
    parsed = fuera / "parseado.jsonl"
    parsed.write_text('{"uid": "u0"}\n{"uid": "u1"}\n', encoding="utf-8")
    return {"vault": vault, "reg": reg, "crudo": crudo, "otro": otro, "parsed": parsed, "fuera": fuera,
            "entries": entries}


def _registry(env) -> list[dict]:
    return json.loads(env["reg"].read_text(encoding="utf-8"))


def _derive(env, name="parseado", path=None, sources=("crudo",), **kw):
    path = str(env["parsed"]) if path is None and not kw.get("rederive") else path
    return lineage.register_derived(env["vault"], "demo", name, path, list(sources), "src/parse.py",
                                    "a" * 40, **kw)


# ---- register-derived ----------------------------------------------------------

def test_register_appends_entry_with_lineage(env):
    before = _registry(env)
    res = _derive(env, params='{"umbral": 3}', produced_at="2031-02-01T10:00:00Z")
    reg = _registry(env)
    assert reg[:2] == before                                   # never rewrites another entry
    e = reg[2]
    assert e["name"] == "parseado" and e["kind"] == "file" and e["files"] == 1
    assert e["sha256"] == _sha(env["parsed"].read_bytes()) == res["sha256"]
    assert e["derived_from"] == [{"name": "crudo", "sha256": before[0]["sha256"]}]
    assert e["script"] == "src/parse.py" and e["commit"] == "a" * 40
    assert e["params"] == {"umbral": 3} and e["produced_at"] == "2031-02-01T10:00:00Z"
    assert e["sensitive"] is True                              # inherited from a sensitive source
    assert e["path"] == str(env["parsed"]).replace("\\", "/")
    assert e["registered_at"].endswith("Z") and e["by"]
    assert res["vault_commit"] == _git(env["vault"], "rev-parse", "HEAD")
    assert _git(env["vault"], "show", "--name-only", "--format=", "HEAD") == "Projects/demo/Datos/registro.json"


def test_commit_takes_only_the_registry(env):
    other = env["vault"] / "nota.md"
    other.write_text("borrador inventado\n", encoding="utf-8")
    _git(env["vault"], "add", "nota.md")
    _derive(env)
    assert _git(env["vault"], "show", "--name-only", "--format=", "HEAD") == "Projects/demo/Datos/registro.json"
    assert "nota.md" in _git(env["vault"], "diff", "--cached", "--name-only")


def test_no_commit(env):
    head = _git(env["vault"], "rev-parse", "HEAD")
    res = _derive(env, do_commit=False)
    assert res["vault_commit"] is None and _git(env["vault"], "rev-parse", "HEAD") == head
    assert lineage.find(_registry(env), "parseado")


def test_folder_output_fingerprint_like_the_app(env):
    out = env["fuera"] / "clasificado"
    (out / "a").mkdir(parents=True)
    (out / "a" / "x.jsonl").write_text("{}\n", encoding="utf-8")
    (out / "y.csv").write_text("k\n1\n", encoding="utf-8")
    res = _derive(env, name="clasificado", path=str(out))
    assert res["kind"] == "dir" and res["files"] == 2 and res["sha256"] == _app_dir_sha(out)


def test_existing_name_needs_rederive(env):
    _derive(env)
    with pytest.raises(lineage.Refused, match="--rederive"):
        _derive(env)
    with pytest.raises(lineage.Refused, match="--rederive"):
        _derive(env, name="PARSEADO")
    with pytest.raises(lineage.Refused, match="no es un dataset derivado"):
        _derive(env, name="otro", rederive=True)
    with pytest.raises(lineage.Refused, match="no hay ningún dataset derivado"):
        _derive(env, name="nuevo", rederive=True)


def test_rederive_replaces_in_place_and_reuses_path(env):
    _derive(env, sensitive=True)
    env["parsed"].write_text('{"uid": "u0"}\n{"uid": "u1"}\n{"uid": "u2"}\n', encoding="utf-8")
    res = lineage.register_derived(env["vault"], "demo", "parseado", None, ["crudo"], "src/parse.py", "b" * 40,
                                   rederive=True)
    reg = _registry(env)
    assert [e["name"] for e in reg] == ["crudo", "otro", "parseado"]
    assert reg[2]["sha256"] == _sha(env["parsed"].read_bytes()) == res["sha256"]
    assert reg[2]["commit"] == "b" * 40 and reg[2]["sensitive"] is True and res["replaced"] is True


def test_rederive_unchanged_writes_nothing(env):
    _derive(env)
    head = _git(env["vault"], "rev-parse", "HEAD")
    text = env["reg"].read_text(encoding="utf-8")
    res = _derive(env, path=str(env["parsed"]), rederive=True)
    assert res["changed"] is False and env["reg"].read_text(encoding="utf-8") == text
    assert _git(env["vault"], "rev-parse", "HEAD") == head


def test_source_drift_refused(env):
    (env["crudo"] / "lote-0" / "items.jsonl").write_text('{"uid": "cambiado"}\n', encoding="utf-8")
    with pytest.raises(lineage.Refused, match="no coincide con el registro"):
        _derive(env)
    assert lineage.find(_registry(env), "parseado") is None


def test_no_sensitive_from_sensitive_source_warns(env):
    res = _derive(env, sensitive=False)
    assert res["sensitive"] is False and res["avisos"]
    x = env["fuera"] / "x.csv"
    x.write_text("k\n", encoding="utf-8")
    res2 = _derive(env, name="de-otro", path=str(x), sources=("otro",))
    assert res2["sensitive"] is False and "avisos" not in res2


@pytest.mark.parametrize("kw,match", [
    ({"sources": ("nada",)}, "no está en el registro"),
    ({"sources": ()}, "al menos un dataset"),
    ({"sources": ("parseado",)}, "sí mismo"),
    ({"name": "mal/nombre"}, "--name"),
])
def test_bad_arguments_refused(env, kw, match):
    with pytest.raises(lineage.Refused, match=match):
        _derive(env, **kw)


def test_bad_script_commit_params(env):
    for script in ("../fuera.py", "/abs/x.py", "C:/x.py"):
        with pytest.raises(lineage.Refused, match="--script"):
            lineage.register_derived(env["vault"], "demo", "p", str(env["parsed"]), ["crudo"], script, "a" * 40)
    with pytest.raises(lineage.Refused, match="--commit"):
        lineage.register_derived(env["vault"], "demo", "p", str(env["parsed"]), ["crudo"], "s.py", "HEAD")
    with pytest.raises(lineage.Refused, match="--params"):
        _derive(env, params="[1, 2]")


def test_output_location_refused(env):
    inside = env["vault"] / "dentro.jsonl"
    inside.write_text("{}\n", encoding="utf-8")
    with pytest.raises(lineage.Refused, match="fuera del vault"):
        _derive(env, path=str(inside))
    with pytest.raises(lineage.Refused, match="se solapa"):
        _derive(env, path=str(env["crudo"] / "lote-0" / "items.jsonl"))
    with pytest.raises(lineage.Refused, match="se solapa"):
        _derive(env, path=str(env["fuera"]))
    with pytest.raises(lineage.Refused, match="no existe"):
        _derive(env, path=str(env["fuera"] / "nada.jsonl"))


def test_cycle_refused(env):
    _derive(env)
    units = env["fuera"] / "unidades.csv"
    units.write_text("uid\nu0\n", encoding="utf-8")
    _derive(env, name="unidades", path=str(units), sources=("parseado",))
    with pytest.raises(lineage.Refused, match="ciclo"):
        _derive(env, path=str(env["parsed"]), sources=("unidades",), rederive=True)


def test_repo_check(env, tmp_path):
    repo = tmp_path / "codigo"
    (repo / "src").mkdir(parents=True)
    _git(repo, "init", "-q")
    (repo / "src" / "parse.py").write_text("print('inventado')\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "c")
    c = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(lineage.Refused, match="no está en el commit"):
        lineage.register_derived(env["vault"], "demo", "p", str(env["parsed"]), ["crudo"], "src/otro.py", c,
                                 repo=repo)
    res = lineage.register_derived(env["vault"], "demo", "p", str(env["parsed"]), ["crudo"], "src/parse.py", c,
                                   repo=repo)
    assert res["commit"] == c


# ---- roots, show, verify ---------------------------------------------------------

def _chain(env):
    _derive(env)
    units = env["fuera"] / "unidades.csv"
    units.write_text("uid,marca\nu0,1\n", encoding="utf-8")
    _derive(env, name="unidades", path=str(units), sources=("parseado", "otro"))
    return units


def test_roots_and_broken_link(env):
    _chain(env)
    roots, problems = lineage.lineage_roots(_registry(env), "unidades")
    assert sorted(r["name"] for r in roots) == ["crudo", "otro"] and problems == []
    env["parsed"].write_text('{"uid": "u9"}\n', encoding="utf-8")
    _derive(env, rederive=True)                       # parseado re-derived, unidades not
    _, problems = lineage.lineage_roots(_registry(env), "unidades")
    assert any("vuelve a derivar «unidades»" in p for p in problems)


def test_roots_cycle_and_missing():
    entries = [{"name": "a", "sha256": "1", "derived_from": [{"name": "b", "sha256": "2"}]},
               {"name": "b", "sha256": "2", "derived_from": [{"name": "a", "sha256": "1"}]},
               {"name": "c", "sha256": "3", "derived_from": [{"name": "nada", "sha256": "4"}]}]
    assert any("ciclo" in p for p in lineage.lineage_roots(entries, "a")[1])
    assert any("«nada» no está" in p for p in lineage.lineage_roots(entries, "c")[1])
    assert lineage.lineage_roots([{"name": "r", "sha256": "5"}], "r") == ([{"name": "r", "sha256": "5"}], [])


def test_show_prints_names_and_shas_not_paths(env, capsys):
    _chain(env)
    res = lineage.show(env["vault"], "demo", "unidades")
    text = json.dumps(res, ensure_ascii=False)
    for p in (env["crudo"], env["parsed"], env["otro"]):
        assert str(p).replace("\\", "/") not in text and str(p) not in text.replace("\\\\", "\\")
    assert res["cadena"]["script"] == "src/parse.py"
    assert res["cadena"]["derived_from"][0]["origen"]["derived_from"][0]["origen"]["raiz"] is True
    assert sorted(r["name"] for r in res["raices"]) == ["crudo", "otro"]
    assert lineage.main(["show", "--vault", str(env["vault"]), "--project", "demo", "--name", "unidades",
                         "--text"]) == 0
    out = capsys.readouterr().out
    assert "unidades" in out and "(raíz)" in out and "[sensible]" in out and str(env["fuera"]) not in out


def test_verify_reports_drift(env, capsys):
    units = _chain(env)
    assert lineage.verify(env["vault"], "demo", "unidades")["ok"] is True
    units.write_text("uid,marca\nu0,0\n", encoding="utf-8")
    res = lineage.verify(env["vault"], "demo", "unidades")
    assert res["ok"] is False
    assert {d["name"]: d["estado"] for d in res["datasets"]}["unidades"] == "cambió"
    assert lineage.main(["verify", "--vault", str(env["vault"]), "--project", "demo"]) == 2
    assert str(env["fuera"]) not in capsys.readouterr().out.replace("\\\\", "\\")


def test_cli_exit_codes(env):
    base = [sys.executable, str(SCRIPT), "register-derived", "--vault", str(env["vault"]), "--project", "demo",
            "--name", "parseado", "--from", "crudo", "--script", "src/parse.py", "--commit", "c" * 40]
    r = subprocess.run([*base, "--path", str(env["parsed"]), "--params", '{"k": 1}'], capture_output=True)
    assert r.returncode == 0, r.stdout
    assert json.loads(r.stdout.decode("utf-8"))["derived_from"][0]["name"] == "crudo"
    r = subprocess.run(base, capture_output=True)
    assert r.returncode == 3 and "error" in json.loads(r.stdout.decode("utf-8"))


# ---- data guard ----------------------------------------------------------------

def test_data_guard_allows_commands_by_name(env):
    _derive(env, sensitive=True)
    data_guard._MEMO.clear()
    v = env["vault"]
    for args in ('register-derived --project demo --name parseado --from crudo --script src/parse.py '
                 f'--commit {"a" * 40} --rederive',
                 "show --project demo --name parseado --text", "verify --project demo --name parseado"):
        cmd = f'python "{SCRIPT}" {args.split(" ", 1)[0]} --vault "{v}" {args.split(" ", 1)[1]}'
        ev = {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(v)}
        assert data_guard.decide(ev, v) is None, cmd
    # naming the sensitive output's path is what the guard stops: leave --path out on --rederive
    cmd = (f'python "{SCRIPT}" register-derived --vault "{v}" --project demo --name parseado --from crudo '
           f'--path "{env["parsed"]}" --script s.py --commit {"a" * 40} --rederive')
    assert data_guard.decide({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(v)}, v)


# ---- plan_frozen with lineage -----------------------------------------------------

@pytest.fixture
def gate_env(monkeypatch):
    for m in ss.AGENT_MARKERS:
        monkeypatch.delenv(m, raising=False)


def _plan_frozen(v: Vault, units: str | None = None) -> list[str]:
    p = v.question(verified=False, resultado="")
    if units:
        p.write_text(p.read_text(encoding="utf-8").replace("units_dataset: unidades-demo", f"units_dataset: {units}"),
                     encoding="utf-8")
    fm = read_note(p)[0]
    return study_gates.plan_frozen(v.root, p, fm)


def _raw(name: str, sha: str) -> dict:
    return {"name": name, "kind": "dir", "path": "C:/fuera/crudo", "sha256": sha}


def _derived(name: str, sha: str, *srcs: tuple[str, str]) -> dict:
    return {"name": name, "kind": "file", "path": "C:/fuera/u.csv", "sha256": sha,
            "derived_from": [{"name": n, "sha256": s} for n, s in srcs], "script": "src/x.py", "commit": "a" * 40}


def test_lineage_root_after_freeze_passes_after_rederiving(tmp_path, gate_env):
    v = Vault(tmp_path / "vault")
    raw = _raw("crudo", "1" * 64)
    v.registry([raw, _derived("parseado", "2" * 64, ("crudo", "1" * 64)),
                _derived("tabla", "3" * 64, ("parseado", "2" * 64))])
    v.commit("linaje")
    assert study_gates.DATA_BEFORE_FREEZE not in _plan_frozen(v, "tabla")
    for k in "45":                                     # the parser is fixed twice: new shas, same raw data
        v.registry([raw, _derived("parseado", k * 64, ("crudo", "1" * 64)),
                    _derived("tabla", "9" * 63 + k, ("parseado", k * 64))])
        v.commit(f"vuelve a derivar {k}")
        assert _plan_frozen(v, "tabla") == []


def test_lineage_root_before_freeze_fails(tmp_path, gate_env):
    """The units table is new after the freeze, but its raw data was registered before it
    (under the old name, with the same sha256): today's rule alone would pass."""
    v = Vault(tmp_path / "vault", data_first=True)
    old = json.loads((v.proj / "Datos" / "registro.json").read_text(encoding="utf-8"))[0]
    v.registry([_raw("crudo", old["sha256"]), _derived("tabla", "3" * 64, ("crudo", old["sha256"]))])
    v.commit("linaje")
    assert study_gates.DATA_BEFORE_FREEZE in _plan_frozen(v, "tabla")


def test_lineage_broken_link_fails(tmp_path, gate_env):
    v = Vault(tmp_path / "vault")
    v.registry([_raw("crudo", "1" * 64), _derived("tabla", "3" * 64, ("crudo", "0" * 64))])
    v.commit("linaje")
    miss = _plan_frozen(v, "tabla")
    assert any(m.startswith("linaje de 'tabla':") and "vuelve a derivar" in m for m in miss)


def test_lineage_root_never_committed_fails(tmp_path, gate_env):
    v = Vault(tmp_path / "vault")
    v.registry([_derived("tabla", "3" * 64, ("crudo", "1" * 64))])
    v.commit("tabla sin su origen")
    v.registry([_raw("crudo", "1" * 64), _derived("tabla", "3" * 64, ("crudo", "1" * 64))])   # not committed
    miss = _plan_frozen(v, "tabla")
    assert any("'crudo' (origen de 'tabla')" in m and "commit" in m for m in miss)


def test_units_without_lineage_keep_todays_rule(tmp_path, gate_env):
    v = Vault(tmp_path / "vault", data_first=True)
    assert study_gates.DATA_BEFORE_FREEZE in _plan_frozen(v)
    v2 = Vault(tmp_path / "vault2")
    assert _plan_frozen(v2) == []
