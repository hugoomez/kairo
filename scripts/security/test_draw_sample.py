"""Tests for draw_sample.py. Invented vault, datasets and material, all under tmp_path."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import data_guard  # noqa: E402
import draw_sample as dsm  # noqa: E402
import fake_material as fm  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "draw_sample.py"


def _write(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        p.write_bytes(data)
    else:
        p.write_text(data, encoding="utf-8", newline="")


def _file_sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _app_dir_sha(root: Path) -> str:
    """The app's folder fingerprint, written independently of draw_sample: sorted
    `<sha256>  <relative path>` lines of every file, entries named .git skipped."""
    lines = []
    pending = [root]
    while pending:
        d = pending.pop()
        for e in d.iterdir():
            if e.name == ".git":
                continue
            if e.is_dir():
                pending.append(e)
            elif e.is_file():
                lines.append(f"{_file_sha(e)}  {e.relative_to(root).as_posix()}")
    lines.sort()
    return hashlib.sha256(("\n".join(lines) + "\n").encode()).hexdigest()


@pytest.fixture
def env(tmp_path):
    vault = tmp_path / "vault"
    datos = vault / "Projects" / "demo" / "Datos"
    datos.mkdir(parents=True)
    fuera = tmp_path / "fuera"
    secrets: list[str] = []

    key = fm.block("RSA PRIVATE KEY")
    secrets += key.splitlines()[1:-1]
    age = fm.age_secret()
    secrets.append(age)
    rows = []
    for i in range(12):
        rows.append({"id": f"u-{i:03d}", "repo": f"acme-inventada/repo-{i % 3}",
                     "html_url": f"https://github.com/acme-inventada/repo-{i % 3}",
                     "email": f"persona{i}@example.org", "path": f"acme-inventada/repo-{i % 3}/k8s/app.yaml",
                     "herramienta": ["sops", "sealed", "ninguna"][i % 3], "anio": 2030 + i % 2,
                     "texto": f"clave de prueba {i}\n" + (key if i == 4 else "") + (age if i == 7 else "")})
    units = fuera / "unidades.jsonl"
    _write(units, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows) + "{roto\n")
    secrets += [f"persona{i}@example.org" for i in range(12)]

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["clave", "grupo", "valor"], lineterminator="\n")
    w.writeheader()
    for i in range(20):
        w.writerow({"clave": f"c{i}", "grupo": "a" if i < 3 else ("b" if i < 10 else "c"), "valor": str(i)})
    tabla = fuera / "tabla.csv"
    _write(tabla, buf.getvalue())

    root = fuera / "repos"
    _write(root / "acme-inventada/repo-a/k8s/app.yaml", "apiVersion: v1\nkind: ConfigMap\n")
    _write(root / "acme-inventada/repo-a/key.pem", "certificado de prueba\n" + key + "\n")
    _write(root / "acme-inventada/repo-a/README.md", "ver https://github.com/acme-inventada/repo-a\n")
    _write(root / "org-x/repo/notas.txt", "texto inventado " * 20000)  # > 256 KiB
    _write(root / "org-x/repo/secret.age", fm.age_binary(fm.b64(32).rstrip("=")))
    _write(root / "org-x/repo/imagen.bin", b"\xff\xfe\x00\x01binario")
    _write(root / "org-x/repo/.git/config", "[core]\n")

    entries = [
        {"name": "unidades", "path": str(units), "kind": "file", "sha256": _file_sha(units), "sensitive": True},
        {"name": "tabla", "path": str(tabla), "kind": "file", "sha256": _file_sha(tabla), "sensitive": False},
        {"name": "repos", "path": str(root), "kind": "dir", "sha256": _app_dir_sha(root), "sensitive": True},
    ]
    reg = datos / "registro.json"
    reg.write_text(json.dumps(entries), encoding="utf-8")
    data_guard._MEMO.clear()
    return {"vault": vault, "tmp": tmp_path, "units": units, "tabla": tabla, "root": root, "reg": reg,
            "entries": entries, "secrets": secrets, "key": key, "age": age, "outdir": tmp_path / "muestras"}


def _read(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]


def _draw(env, name, n, seed, out="m.jsonl", **kw):
    return dsm.draw(env["vault"], "demo", name, n, seed, str(env["outdir"] / out), **kw)


def test_dir_fingerprint_matches_the_app(env):
    files = dsm._walk(env["root"])
    assert ".git" not in "/".join(files) and len(files) == 6
    assert dsm._dir_sha(env["root"], files) == env["entries"][2]["sha256"]


def test_jsonl_rows_redacted_ids_and_manifest(env):
    m = _draw(env, "unidades", 100, 3)
    assert m["n"] == 12 and m["frame_size"] == 12 and m["strata"] is None
    assert m["dropped"] == {"fila_ilegible": 1}
    assert m["redactions"]["private_key"] == 1 and m["redactions"]["age_secret_key"] == 1
    assert m["dataset_sha256"] == env["entries"][0]["sha256"]
    out = env["outdir"] / "m.jsonl"
    text = out.read_text(encoding="utf-8")
    for s in env["secrets"]:
        assert s not in text
    items = _read(out)
    assert all(len(it["id"]) == 16 and int(it["id"], 16) >= 0 for it in items)
    assert len({it["id"] for it in items}) == 12
    assert all(it["id_original"].startswith("u-") for it in items)       # the row's own id is kept aside
    assert {it["repo"] for it in items} == {f"acme-inventada/repo-{k}" for k in range(3)}
    assert all(it["html_url"].startswith("https://github.com/acme-inventada/") for it in items)
    assert all(it["email"].startswith("[REDACTADO email") for it in items)
    assert all(it["path"].startswith("d-") and it["path"].endswith("/app.yaml") for it in items)
    manifest = json.loads((env["outdir"] / "m.jsonl.manifest.json").read_text(encoding="utf-8"))
    assert manifest == m and m["out"] == str(env["outdir"] / "m.jsonl")
    assert not dsm.redact.has_secret(text)


def test_pseudonymise_repos_option(env):
    _draw(env, "unidades", 100, 3, pseudonymise_repos=True)
    text = (env["outdir"] / "m.jsonl").read_text(encoding="utf-8")
    assert "acme-inventada" not in text


def test_id_field_used_when_unique_else_hashed(env):
    _draw(env, "unidades", 5, 1, id_field="id")
    items = _read(env["outdir"] / "m.jsonl")
    assert all(it["id"].startswith("u-") and "id_original" not in it for it in items)
    _draw(env, "unidades", 5, 1, out="h.jsonl", id_field="repo")       # repeated values: hashed ids
    assert all(len(it["id"]) == 16 and not it["id"].startswith("acme") for it in _read(env["outdir"] / "h.jsonl"))


def test_csv_stratified_weights(env):
    m = _draw(env, "tabla", 20, 9, stratify_by="grupo", per_stratum=4)
    assert m["strata"] == {"a": {"frame": 3, "drawn": 3}, "b": {"frame": 7, "drawn": 4},
                           "c": {"frame": 10, "drawn": 4}}
    assert m["n"] == 11 and m["frame_size"] == 20
    items = _read(env["outdir"] / "m.jsonl")
    w = {it["_stratum"]: it["_weight"] for it in items}
    assert w == {"a": 1.0, "b": 7 / 4, "c": 10 / 4}
    assert all(it["grupo"] == it["_stratum"] for it in items)
    assert [it["_stratum"] for it in items] != sorted(it["_stratum"] for it in items)  # order is shuffled


def test_stratified_total_over_n_refused(env):
    with pytest.raises(dsm.Refused, match="más que --n"):
        _draw(env, "tabla", 5, 9, stratify_by="grupo", per_stratum=4)
    with pytest.raises(dsm.Refused, match="van juntos"):
        _draw(env, "tabla", 5, 9, stratify_by="grupo")


def test_too_many_strata_refused(env, monkeypatch):
    monkeypatch.setattr(dsm, "MAX_STRATA", 5)
    with pytest.raises(dsm.Refused, match="más de 5 valores"):
        _draw(env, "tabla", 100, 1, stratify_by="clave", per_stratum=1)


def test_filters(env):
    m = _draw(env, "tabla", 100, 2, filters=["grupo=b"])
    assert m["frame_size"] == 7 and m["n"] == 7
    assert {it["grupo"] for it in _read(env["outdir"] / "m.jsonl")} == {"b"}
    m = _draw(env, "unidades", 100, 2, out="u.jsonl", filters=["herramienta=sops", "anio=2030"])
    assert m["frame_size"] == 2
    with pytest.raises(dsm.Refused, match="no está en el dataset"):
        _draw(env, "tabla", 100, 2, out="x.jsonl", filters=["inexistente=1"])
    with pytest.raises(dsm.Refused, match="COLUMNA=VALOR"):
        _draw(env, "tabla", 100, 2, out="x.jsonl", filters=["sin-igual"])


def test_same_args_byte_identical_and_seed_matters(env):
    kw = {"stratify_by": "grupo", "per_stratum": 2}
    _draw(env, "tabla", 20, 42, out="a.jsonl", **kw)
    _draw(env, "tabla", 20, 42, out="b.jsonl", **kw)
    _draw(env, "tabla", 20, 43, out="c.jsonl", **kw)
    a, b, c = ((env["outdir"] / f).read_bytes() for f in ("a.jsonl", "b.jsonl", "c.jsonl"))
    assert a == b and a != c
    _draw(env, "repos", 3, 42, out="d1.jsonl")
    _draw(env, "repos", 3, 42, out="d2.jsonl")
    assert (env["outdir"] / "d1.jsonl").read_bytes() == (env["outdir"] / "d2.jsonl").read_bytes()
    # rerunning onto a previous sample replaces it with the same bytes
    _draw(env, "tabla", 20, 42, out="a.jsonl", **kw)
    assert (env["outdir"] / "a.jsonl").read_bytes() == a


def test_dir_items(env):
    m = _draw(env, "repos", 100, 5)
    assert m["frame_size"] == 6 and m["n"] == 6 and m["dropped"] == {}
    out = env["outdir"] / "m.jsonl"
    text = out.read_text(encoding="utf-8")
    for s in env["secrets"][:len(env["key"].splitlines()) - 2]:
        assert s not in text
    items = {Path(it["path"]).name: it for it in _read(out)}
    assert set(items) == {"app.yaml", "key.pem", "README.md", "notas.txt", "secret.age", "imagen.bin"}
    assert all(it["path"].startswith("d-") for it in items.values())
    assert "acme-inventada" not in "".join(it["path"] for it in items.values())
    assert "https://github.com/acme-inventada/repo-a" in items["README.md"]["content"]
    assert "[REDACTADO private_key" in items["key.pem"]["content"]
    notas = items["notas.txt"]["content"]
    assert "[TRUNCADO: el fichero tiene" in notas and len(notas.encode()) < dsm.MAX_CONTENT + 200
    assert items["secret.age"]["content"].startswith("age-encryption.org/v1\n-> X25519 ")
    assert "[BINARIO 300 bytes]" in items["secret.age"]["content"]
    assert items["imagen.bin"]["content"] == "[BINARIO 11 bytes]"
    assert m["redactions"] == {"private_key": 1}


def test_dir_glob_and_stratify_by_ext(env):
    m = _draw(env, "repos", 100, 5, glob="acme-inventada/*")
    assert m["frame_size"] == 3
    m = _draw(env, "repos", 100, 5, out="e.jsonl", stratify_by="ext", per_stratum=1, filters=["carpeta=org-x"])
    assert m["strata"] == {".age": {"frame": 1, "drawn": 1}, ".bin": {"frame": 1, "drawn": 1},
                           ".txt": {"frame": 1, "drawn": 1}}
    with pytest.raises(dsm.Refused, match="ext o carpeta"):
        _draw(env, "repos", 100, 5, out="x.jsonl", stratify_by="repo", per_stratum=1)
    with pytest.raises(dsm.Refused, match="--id-field"):
        _draw(env, "repos", 100, 5, out="x.jsonl", id_field="id")
    with pytest.raises(dsm.Refused, match="--glob"):
        _draw(env, "tabla", 100, 5, out="x.jsonl", glob="*")


def test_item_that_cannot_be_cleaned_is_dropped(env, monkeypatch):
    real = dsm.redact.has_secret
    monkeypatch.setattr(dsm.redact, "has_secret", lambda t: "ConfigMap" in t or real(t))
    m = _draw(env, "repos", 100, 5)
    assert m["dropped"] == {"residuo_tras_redactar": 1} and m["n"] == 5
    assert "ConfigMap" not in (env["outdir"] / "m.jsonl").read_text(encoding="utf-8")


def test_stratum_values_that_look_private_are_hidden(env):
    m = _draw(env, "unidades", 100, 1, stratify_by="email", per_stratum=1)
    assert all(k.startswith("estrato-") for k in m["strata"])
    assert "@" not in json.dumps(m)


@pytest.mark.parametrize("out, why", [
    ("relativa.jsonl", "absoluta"),
    ("{tmp}/muestras/m.csv", ".jsonl"),
    ("{vault}/Projects/demo/m.jsonl", "dentro del vault"),
    ("{root}/m.jsonl", "datos protegidos"),
    ("{tabla_dir}/anotar/x.jsonl", None),
])
def test_out_path_refusals(env, out, why):
    out = out.format(tmp=env["tmp"], vault=env["vault"], root=env["root"], tabla_dir=env["tabla"].parent)
    if why is None:          # a folder next to a non-sensitive dataset is fine
        assert dsm.draw(env["vault"], "demo", "tabla", 2, 1, out)["n"] == 2
        return
    with pytest.raises(dsm.Refused, match=why):
        dsm.draw(env["vault"], "demo", "tabla", 2, 1, out)


def test_out_inside_the_drawn_dataset_or_a_foreign_file_refused(env):
    entries = [dict(e, sensitive=False) for e in env["entries"]]
    env["reg"].write_text(json.dumps(entries), encoding="utf-8")
    data_guard._MEMO.clear()
    with pytest.raises(dsm.Refused, match="datos protegidos"):
        dsm.draw(env["vault"], "demo", "repos", 2, 1, str(env["root"] / "x.jsonl"))
    foreign = env["outdir"] / "mio.jsonl"
    _write(foreign, "mío\n")
    with pytest.raises(dsm.Refused, match="no lo toco"):
        dsm.draw(env["vault"], "demo", "tabla", 2, 1, str(foreign))
    assert foreign.read_text(encoding="utf-8") == "mío\n"


def test_drift_refused(env):
    with open(env["tabla"], "a", encoding="utf-8", newline="") as fh:
        fh.write("c99,a,99\n")
    with pytest.raises(dsm.Refused, match="huella"):
        _draw(env, "tabla", 2, 1)
    _write(env["root"] / "org-x/repo/nuevo.txt", "otro\n")
    with pytest.raises(dsm.Refused, match="huella"):
        _draw(env, "repos", 2, 1)
    assert not (env["outdir"] / "m.jsonl").exists()


def test_registry_without_sha_refused(env):
    entries = [dict(e, sha256="") for e in env["entries"]]
    env["reg"].write_text(json.dumps(entries), encoding="utf-8")
    data_guard._MEMO.clear()
    with pytest.raises(dsm.Refused, match="no tiene huella"):
        _draw(env, "tabla", 2, 1)


def test_cli_stdout_is_aggregates_only(env):
    out = env["outdir"] / "cli.jsonl"
    r = subprocess.run([sys.executable, str(SCRIPT), "--vault", str(env["vault"]), "--project", "demo",
                        "--dataset", "unidades", "--n", "6", "--seed", "1", "--out", str(out),
                        "--stratify-by", "herramienta", "--per-stratum", "2", "--filter", "anio=2030"],
                       capture_output=True)
    assert r.returncode == 0, r.stdout
    text = r.stdout.decode("utf-8")
    m = json.loads(text)
    assert set(m) == {"out", "n", "seed", "dataset", "dataset_sha256", "frame_size", "strata", "dropped",
                      "redactions"}
    assert m["frame_size"] == 6 and m["n"] == 6 and set(m["strata"]) == {"sops", "sealed", "ninguna"}
    for s in [*env["secrets"], "clave de prueba", str(env["units"])]:
        assert s not in text
    assert json.loads((env["outdir"] / "cli.jsonl.manifest.json").read_text(encoding="utf-8")) == m
    chk = subprocess.run([sys.executable, str(SCRIPT.parent / "redact.py"), "check"], input=out.read_bytes(),
                         capture_output=True)
    assert chk.returncode == 0, chk.stdout


def test_cli_refusal_exit_3(env):
    r = subprocess.run([sys.executable, str(SCRIPT), "--vault", str(env["vault"]), "--project", "demo",
                        "--dataset", "nada", "--n", "1", "--seed", "1", "--out", str(env["outdir"] / "x.jsonl")],
                       capture_output=True)
    assert r.returncode == 3 and "error" in json.loads(r.stdout.decode("utf-8"))


def test_data_guard_allows_the_draw(env):
    cmd = (f'python "{SCRIPT}" --vault "{env["vault"]}" --project demo --dataset repos --n 5 --seed 1 '
           f'--out "{env["outdir"] / "m.jsonl"}"')
    ev = {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(env["vault"])}
    assert data_guard.decide(ev, env["vault"]) is None
