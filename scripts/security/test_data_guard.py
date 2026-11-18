"""Tests for data_guard.py: no agent reads a sensitive dataset or Projects/*/Avisos/.
Run: python -m pytest scripts/security/test_data_guard.py -q

The vault and the datasets are invented under tmp_path; the datasets live in a
folder outside the vault, as registered datasets do. Invented names and text only."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import data_guard  # noqa: E402
from data_guard import decide, sensitive_roots  # noqa: E402

SECRET = "CONTENIDO-SENSIBLE-INVENTADO"


def dataset(name: str, path: Path, kind: str = "dir", sensitive=True) -> dict:
    d = {"name": name, "path": str(path), "kind": kind, "files": 1, "bytes": len(SECRET),
         "sha256": "0" * 64, "registered_at": "2031-01-01T00:00:00Z", "by": "tester"}
    if sensitive is not None:
        d["sensitive"] = sensitive
    return d


@pytest.fixture
def env(tmp_path):
    vault = tmp_path / "vault"
    (vault / "Papers").mkdir(parents=True)
    (vault / "Papers" / "P-9001 Inventado.md").write_text("---\nid: P-9001\n---\n\nTexto.\n", encoding="utf-8")
    datos = vault / "Projects" / "demo" / "Datos"
    datos.mkdir(parents=True)
    outside = tmp_path / "datos-fuera"
    ds = outside / "DS"
    (ds / "sub").mkdir(parents=True)
    (ds / "sub" / "f.txt").write_text(SECRET, encoding="utf-8")
    (outside / "otro").mkdir()
    (outside / "otro" / "libre.txt").write_text("nada", encoding="utf-8")
    single = outside / "unidades.csv"
    single.write_text(SECRET, encoding="utf-8")
    reg = datos / "registro.json"

    def register(*entries):
        reg.write_text(json.dumps(list(entries)), encoding="utf-8")

    register(dataset("encuesta-inventada", ds))
    data_guard._MEMO.clear()
    return {"vault": vault, "ds": ds, "outside": outside, "single": single, "reg": reg, "register": register,
            "tmp": tmp_path}


def ev(tool: str, cwd: Path, **tin) -> dict:
    return {"tool_name": tool, "tool_input": tin, "cwd": str(cwd)}


def test_read_inside_sensitive_blocked(env):
    r = decide(ev("Read", env["vault"], file_path=str(env["ds"] / "sub" / "f.txt")), env["vault"])
    assert r and "encuesta-inventada" in r and "demo" in r
    assert SECRET not in r
    # the dataset folder itself, a file-like path that does not exist yet, and a symlink-free alias via ..
    assert decide(ev("Read", env["vault"], file_path=str(env["ds"])), env["vault"])
    assert decide(ev("Read", env["vault"], file_path=str(env["ds"] / "sub" / ".." / "sub" / "f.txt")), env["vault"])
    # a normal note and a file next to the dataset pass
    assert decide(ev("Read", env["vault"], file_path=str(env["vault"] / "Papers" / "P-9001 Inventado.md")),
                  env["vault"]) is None
    assert decide(ev("Read", env["vault"], file_path=str(env["outside"] / "otro" / "libre.txt")), env["vault"]) is None


def test_read_non_sensitive_allowed(env):
    env["register"](dataset("encuesta-inventada", env["ds"], sensitive=False))
    assert decide(ev("Read", env["vault"], file_path=str(env["ds"] / "sub" / "f.txt")), env["vault"]) is None
    env["register"](dataset("encuesta-inventada", env["ds"], sensitive=None))          # missing means false
    assert decide(ev("Read", env["vault"], file_path=str(env["ds"] / "sub" / "f.txt")), env["vault"]) is None


def test_file_dataset_protects_exactly_that_file(env):
    env["register"](dataset("unidades-inventadas", env["single"], kind="file"))
    assert decide(ev("Read", env["vault"], file_path=str(env["single"])), env["vault"])
    assert decide(ev("Read", env["vault"], file_path=str(env["ds"] / "sub" / "f.txt")), env["vault"]) is None
    # a Grep over its folder reaches it
    assert decide(ev("Grep", env["vault"], pattern="x", path=str(env["outside"])), env["vault"])


def test_grep_and_glob_scopes(env):
    v, ds = env["vault"], env["ds"]
    for mode in ("content", "files_with_matches", "count"):
        for scope in (ds / "sub", ds, ds.parent):
            r = decide(ev("Grep", v, pattern="x", path=str(scope), output_mode=mode), v)
            assert r and "encuesta-inventada" in r, (mode, scope)
    # relative scope from a neighbouring cwd, and `paths`
    assert decide(ev("Grep", env["outside"] / "otro", pattern="x", path=".."), v)
    assert decide(ev("Grep", v, pattern="x", paths=[str(v), str(ds)]), v)
    # a scope that does not hold it passes; so does an ancestor whose glob cannot match a file in it
    assert decide(ev("Grep", v, pattern="x", path=str(env["outside"] / "otro")), v) is None
    assert decide(ev("Grep", v, pattern="x", path=str(ds.parent), glob="*.md"), v) is None
    assert decide(ev("Grep", v, pattern="x", path=str(ds.parent), glob="*.txt"), v)
    assert decide(ev("Grep", v, pattern="x", path=str(ds.parent), glob="*.{md,txt}"), v)
    assert decide(ev("Grep", v, pattern="x", path=str(ds.parent), glob="!*.md"), v)
    assert decide(ev("Grep", v, pattern="x", path=str(ds.parent), type="md"), v) is None
    assert decide(ev("Grep", v, pattern="x", path=str(ds.parent), type="txt"), v)
    # Glob: inside, the dataset itself, an ancestor whose pattern descends into it, an absolute pattern
    for tin in ({"pattern": "*", "path": str(ds / "sub")}, {"pattern": "**/*", "path": str(ds)},
                {"pattern": "**/*", "path": str(ds.parent)}, {"pattern": "DS/*/*.txt", "path": str(ds.parent)},
                {"pattern": str(ds / "**" / "*.txt")}, {"pattern": "../DS/**"}):
        r = decide({"tool_name": "Glob", "tool_input": tin, "cwd": str(env["outside"] / "otro")}, v)
        assert r and "encuesta-inventada" in r, tin
    for tin in ({"pattern": "*.txt", "path": str(ds.parent)}, {"pattern": "otro/**", "path": str(ds.parent)},
                {"pattern": "**/*.md", "path": str(ds.parent)}, {"pattern": "**/*.md", "path": str(v)}):
        assert decide({"tool_name": "Glob", "tool_input": tin, "cwd": str(v)}, v) is None, tin


def test_shell_relative_and_case(env):
    v, ds, here = env["vault"], env["ds"], env["outside"] / "otro"
    blocked = [
        "cat ..\\DS\\sub\\f.txt",
        f"type {str(ds).upper()}\\SUB\\F.TXT",
        f"type {str(ds).swapcase()}/sub/f.txt",
        "cd ..; cat DS/sub/f.txt",
        "cd ..\\DS && cat sub/f.txt",
        "rg secreto ..",
        "grep -r secreto ..",
        "ls -R ..",
        "cat ../*/sub/*",
        "cat ../D?/sub/f.txt",
        f"python -c \"print(open(r'{ds}/sub/f.txt').read())\"",
        "Get-Content -Path:..\\DS\\sub\\f.txt",
        "python x.py --input=../DS/sub/f.txt",
        "cp ../DS/sub/f.txt ./copia.txt",
        "pushd ..; type DS\\sub\\f.txt",
        "cat $(echo ../DS/sub/f.txt)",
        "cd ..; rg secreto",
        "Get-ChildItem -Recurse ..",
        "dir /s ..",
        "find .. -name f.txt",
        "tree ..",
    ]
    for cmd in blocked:
        for tool in ("Bash", "PowerShell"):
            r = decide(ev(tool, here, command=cmd), v)
            assert r and "encuesta-inventada" in r and SECRET not in r, (tool, cmd)
    # a shell whose cwd is inside the dataset reaches it with any command
    assert decide(ev("Bash", ds / "sub", command="ls"), v)
    allowed = ["ls ..", "cat libre.txt", "rg secreto .", "cat ../otro/*", "git status", "echo DS"]
    for cmd in allowed:
        assert decide(ev("Bash", here, command=cmd), v) is None, cmd


def test_avisos_blocked(env):
    v = env["vault"]
    avisos = v / "Projects" / "x" / "Avisos"
    avisos.mkdir(parents=True)
    (avisos / "registro.json").write_text("[]", encoding="utf-8")
    for fp in ("Projects/x/Avisos/registro.json", "projects/X/avisos/registro.json",
               str(avisos / "registro.json"), "Projects/y/Avisos/nota.md"):
        r = decide(ev("Read", v, file_path=fp), v)
        assert r and "Avisos" in r, fp
    assert decide(ev("Grep", v, pattern="x", path="Projects"), v)
    assert decide(ev("Grep", v, pattern="x", path=".", glob="*.json"), v)
    assert decide(ev("Grep", v, pattern="x", path=".", glob="*.md"), v) is None
    assert decide(ev("Glob", v, pattern="Projects/*/Avisos/*"), v)
    assert decide(ev("Glob", v, pattern="**/*.md"), v) is None
    assert decide(ev("Bash", v, command="cat Projects/x/Avisos/registro.json"), v)
    assert decide(ev("Bash", v, command="rg -n aviso Projects"), v)
    assert decide(ev("Bash", v, command="git show HEAD"), v)
    assert decide(ev("Bash", v, command="git diff -- Papers"), v) is None
    assert decide(ev("Bash", v, command="git status"), v) is None
    assert decide(ev("Bash", v, command="git log -p"), v)
    assert decide(ev("Bash", v, command="ls -R"), v)
    assert decide(ev("Bash", v, command="tar czf a.tgz ."), v)
    # everyday commands in the vault still run: ls -r is reverse order; rg/tar with a folder of their own
    for cmd in ("cat Papers/x.md", "ls -la", "ls -lart", "rg foo Papers", "rg -e foo Papers",
                "tar czf a.tgz Papers", "git log --oneline", "git diff --stat", "python -m pytest -q"):
        assert decide(ev("Bash", v, command=cmd), v) is None, cmd


def test_smart_connections_blocked(env):
    v = env["vault"]
    (v / "Projects" / "x" / "Avisos").mkdir(parents=True)
    for tool in ("mcp__smart-connections__get_note", "mcp__smart-connections__search_similar"):
        r = decide({"tool_name": tool, "tool_input": {"notePath": str(env["ds"] / "sub" / "f.txt")}}, v)
        assert r and "encuesta-inventada" in r, tool
        assert decide({"tool_name": tool, "tool_input": {"notePath": "Projects/x/Avisos/a.md"}}, v), tool
        assert decide({"tool_name": tool, "tool_input": {"notePath": "Papers/P-9001 Inventado.md"}}, v) is None


def test_unreadable_registry_fails_closed(env):
    v = env["vault"]
    note = ev("Read", v, file_path=str(v / "Papers" / "P-9001 Inventado.md"))
    for bad in ("{no es json", json.dumps({"name": "x"}), json.dumps(["x"]),
                json.dumps([{"name": "x", "sensitive": True}]),
                json.dumps([{"name": "x", "path": "relativa/DS", "sensitive": True}])):
        env["reg"].write_text(bad, encoding="utf-8")
        data_guard._MEMO.clear()
        r = decide(note, v)
        assert r and "registro de datos ilegible" in r and "Projects/demo/Datos/registro.json" in r, bad
        for tool, tin in (("Bash", {"command": "ls"}), ("Glob", {"pattern": "*"}),
                          ("mcp__smart-connections__get_note", {"notePath": "Papers/P-9001 Inventado.md"})):
            assert decide({"tool_name": tool, "tool_input": tin, "cwd": str(v)}, v), (bad, tool)
    # a tool the guard does not cover is not its business
    assert decide({"tool_name": "WebFetch", "tool_input": {"url": "x"}, "cwd": str(v)}, v) is None


def test_sensitive_roots_and_cache(env, monkeypatch):
    v = env["vault"]
    (v / "Projects" / "x" / "Avisos").mkdir(parents=True)
    roots = sensitive_roots(v)
    assert env["ds"].resolve() in roots and (v / "Projects" / "x" / "Avisos").resolve() in roots
    reads = []
    real = data_guard._load_registry
    monkeypatch.setattr(data_guard, "_load_registry", lambda p: reads.append(p) or real(p))
    sensitive_roots(v)
    assert reads == []                                     # cached by the registry's mtime
    env["register"](dataset("encuesta-inventada", env["ds"], sensitive=False), dataset("otra", env["single"], "file"))
    import os
    st = env["reg"].stat()
    os.utime(env["reg"], ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    roots = sensitive_roots(v)
    assert len(reads) == 1 and env["single"].resolve() in roots and env["ds"].resolve() not in roots


# --------------------------------------------------------------------------- #
# Fix round 1: other spellings of the same path, fail-closed listing, the CLI
# --------------------------------------------------------------------------- #

BS = chr(92)   # a backslash, spelled so no shell edit can eat it


def _all_tools_block(env, raw: str):
    v = env["vault"]
    assert decide(ev("Read", v, file_path=raw), v), ("Read", raw)
    assert decide(ev("Grep", v, pattern="x", path=raw), v), ("Grep", raw)
    assert decide(ev("Glob", v, pattern="*", path=raw), v), ("Glob", raw)
    assert decide({"tool_name": "mcp__smart-connections__get_note", "tool_input": {"notePath": raw}}, v), ("mcp", raw)


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="extended-length paths are a Windows form")
def test_extended_and_device_path_forms(env):
    f = env["ds"] / "sub" / "f.txt"
    win = str(f)
    for raw in (BS * 2 + "?" + BS + win, "//?/" + f.as_posix(), BS * 2 + "." + BS + win, "//./" + f.as_posix(),
                BS * 2 + "?" + BS + win.upper()):
        _all_tools_block(env, raw)


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="UNC admin shares are a Windows form")
def test_unc_forms(env):
    f = env["ds"] / "sub" / "f.txt"
    drive, rest = str(f)[0], str(f)[2:]
    for raw in (BS * 2 + "localhost" + BS + drive + "$" + rest, BS * 2 + "127.0.0.1" + BS + drive + "$" + rest,
                BS * 2 + "?" + BS + "UNC" + BS + "localhost" + BS + drive + "$" + rest,
                "//localhost/" + drive.lower() + "$" + rest.replace(BS, "/")):
        _all_tools_block(env, raw)


def test_same_file_by_another_name(env):
    import os
    env["register"](dataset("unidades-inventadas", env["single"], kind="file"))
    alias = env["outside"] / "otro" / "alias.csv"
    try:
        os.link(env["single"], alias)
    except (OSError, NotImplementedError):
        pytest.skip("no hard links here")
    r = decide(ev("Read", env["vault"], file_path=str(alias)), env["vault"])
    assert r and "unidades-inventadas" in r


def test_unlistable_projects_fails_closed(env, monkeypatch):
    real = Path.iterdir

    def iterdir(self):
        if self.name == "Projects":
            raise PermissionError("denied")
        return real(self)
    monkeypatch.setattr(Path, "iterdir", iterdir)
    r = decide(ev("Read", env["vault"], file_path="Papers/P-9001 Inventado.md"), env["vault"])
    assert r and "registro de datos ilegible" in r


def test_quote_splicing(env):
    here = env["outside"] / "otro"
    for cmd in ('cat ../D""S/sub/f.txt', "cat ../D''S/sub/f.txt", 'cat ../D"S"/sub/f.txt',
                "Get-Content ..\\D`S\\sub\\f.txt"):
        r = decide(ev("Bash", here, command=cmd), env["vault"])
        assert r and "encuesta-inventada" in r, cmd


def test_registered_spelling_in_inline_code(env):
    registered = str(env["outside"] / "otro" / ".." / "DS")
    env["register"](dataset("encuesta-inventada", registered))
    cmd = f"python -c \"print(open(r'{registered}' + '/sub/f.txt').read())\""
    r = decide(ev("Bash", env["vault"], command=cmd), env["vault"])
    assert r and "encuesta-inventada" in r


def test_cli_roots(env):
    import subprocess
    (env["vault"] / "Projects" / "x" / "Avisos").mkdir(parents=True)
    script = Path(data_guard.__file__)
    r = subprocess.run([sys.executable, str(script), "roots", "--vault", str(env["vault"])],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    roots = json.loads(r.stdout)["roots"]
    ds = next(x for x in roots if x["kind"] == "dir")
    assert ds == {"path": str(env["ds"].resolve()), "registered": str(env["ds"]), "dataset": "encuesta-inventada",
                  "project": "demo", "kind": "dir"}
    av = next(x for x in roots if x["kind"] == "avisos")
    assert av["project"] == "x" and av["path"] == str((env["vault"] / "Projects" / "x" / "Avisos").resolve())
    env["reg"].write_text("{roto", encoding="utf-8")
    r = subprocess.run([sys.executable, str(script), "roots", "--vault", str(env["vault"])],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 3
    out = json.loads(r.stdout)
    assert out["error"] == "registro de datos ilegible" and out["file"].endswith("Projects/demo/Datos/registro.json")
    assert SECRET not in r.stdout


def test_editing_tools_blocked(env):
    v = env["vault"]
    f = str(env["ds"] / "sub" / "f.txt")
    free = str(env["outside"] / "otro" / "libre.txt")
    for tool in ("Edit", "Write", "MultiEdit"):
        r = decide(ev(tool, v, file_path=f), v)
        assert r and "encuesta-inventada" in r, tool
        assert decide(ev(tool, v, file_path=free), v) is None, tool
    assert decide(ev("NotebookEdit", v, notebook_path=f), v)
    assert decide(ev("NotebookEdit", v, notebook_path=free), v) is None
    # Avisos/ too, even when the file does not exist yet
    assert decide(ev("Write", v, file_path=str(v / "Projects" / "demo" / "Avisos" / "N-9001.md")), v)


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="UNC admin shares are a Windows form")
def test_admin_share_in_inline_code(env):
    f = env["ds"] / "sub" / "f.txt"
    drive, rest = str(f)[0], str(f)[2:]
    for raw in (BS * 2 + "localhost" + BS + drive + "$" + rest, BS * 2 + "127.0.0.1" + BS + drive + "$" + rest,
                BS * 2 + "?" + BS + "UNC" + BS + "localhost" + BS + drive + "$" + rest):
        cmd = f"python -c \"open(r'{raw}')\""
        r = decide(ev("Bash", env["vault"], command=cmd), env["vault"])
        assert r and "encuesta-inventada" in r, cmd
