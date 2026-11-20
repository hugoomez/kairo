#!/usr/bin/env python3
"""The figures of a study project: each one rendered from a study question's estimates.

    figures.py register --vault V --project <slug> --file <abs .pdf|.png> --question Q-XXXX
                        --script <path in the code repo> --commit <code sha> --caption "..."
                        [--wide] [--id F-001] [--by WHO] [--no-commit]
    figures.py check    --vault V --project <slug> [--id F-001]
    figures.py list     --vault V --project <slug>
    figures.py extract-json --vault V --question Q-XXXX --out <file.json> [--project <slug>]

The registry is `Projects/<slug>/Figuras/registro.json`, a JSON array of

    {"id": "F-001", "path": "Figuras/F-001-<name>.pdf", "sha256": <of the file>,
     "caption": "...", "source": {"question": "Q-XXXX", "estimate_sha256": <sha256>},
     "script": <path in the code repo>, "commit": <code repo sha>, "wide": false,
     "registered_at": <UTC ISO>, "by": <who registered it>}

`path` is relative to the project folder and always inside its `Figuras/`; only .pdf and
.png are accepted (the LaTeX export takes those). `estimate_sha256` is the sha256 of the
UTF-8 text of the question's ```estimate-json block in `## Resultado`, as stored (what is
between the fences, the text study_gates.estimates_reproducible compares): the exact
numbers the figure was drawn from.

How a figure is made. A figure script consumes ONLY estimate.py's JSON: aggregates, never
a units file or a data row. Write the script in the project's code repository; get its
input with `extract-json`, which writes the question's estimate-json block to a file
byte for byte (so the file's sha256 is the `estimate_sha256` register will record); render
the figure with that script; commit the script; then `register` it with the script's
repo-relative path and that commit. `register` copies the rendered file into `Figuras/`,
records both sha256s and commits only the registry and the figure file (`--no-commit` to
leave them uncommitted). It refuses a question without an estimate-json block, a question
of a non-citable (exploratory) design, a question of another project and a file inside a
sensitive dataset. `--id` of an existing figure replaces it (new file, new sha256s); a
figure is never removed by this script.

`check` re-hashes every file (or `--id`) and recomputes its question's current
estimate-json sha256. A figure whose source estimate changed is `obsoleta`: re-extract,
re-render and register it again with the same `--id`. Problems: `obsoleta` (the estimate
changed or is gone), `fichero_cambiado`, `fichero_ausente`, `fuera_de_figuras`.

stdout is JSON. Exit 0 ok · 3 refused ({"error": ...}) · 4 `check` found problems (listed).
Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEDGER = HERE.parent / "ledger"
SECURITY = HERE.parent / "security"
for _p in (LEDGER, SECURITY):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import designs as _designs  # noqa: E402
from notes import read_note, section  # noqa: E402

FIG_ID = re.compile(r"^F-\d{3,4}$")
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA = re.compile(r"^[0-9a-f]{7,40}$")
_EST_BLOCK = re.compile(r"```estimate-json[ \t]*\n(.*?)\n[ \t]*```", re.S)  # = study_gates._EST_BLOCK
SUFFIXES = (".pdf", ".png")
FOLDER = "Figuras"


class Refused(Exception):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def project_dir(vault: Path, slug: str) -> Path:
    if not _SLUG.match(slug or ""):
        raise Refused("--project debe ser el nombre de la carpeta del proyecto (Projects/<slug>)")
    proj = Path(vault) / "Projects" / slug
    if not proj.is_dir():
        raise Refused(f"no existe Projects/{slug}")
    return proj


# ---- the question's estimate-json block ---------------------------------------

def find_question(vault: Path, qid: str, proj: Path | None = None) -> Path:
    from study_gates import find_node  # noqa: PLC0415
    design = _designs.design_for_id(_designs.load(), qid)
    if design is None:
        raise Refused(f"{qid}: ningún diseño de config/designs.toml usa ese prefijo")
    if not design.get("citable", True):
        raise Refused(f"{qid} es exploratorio (diseño no citable): no se hacen figuras del manuscrito con él")
    node = find_node(Path(vault), qid, design)
    if node is None:
        raise Refused(f"{qid}: no existe (o hay varias notas) en Projects/*/{design['folder']}/")
    if proj is not None and node.parents[1].resolve() != Path(proj).resolve():
        raise Refused(f"{qid} es de otro proyecto")
    return node


def estimate_block(node: Path) -> str | None:
    """The text between the fences of `## Resultado`'s estimate-json block, or None."""
    parsed = read_note(Path(node))
    if not parsed:
        return None
    m = _EST_BLOCK.search(section(parsed[1], "Resultado"))
    return m.group(1) if m else None


def estimate_sha(node: Path) -> str | None:
    text = estimate_block(node)
    return None if text is None else _sha(text.encode("utf-8"))


# ---- registry -------------------------------------------------------------------

def registry_path(proj: Path) -> Path:
    return proj / FOLDER / "registro.json"


def load_registry(proj: Path) -> list[dict]:
    reg = registry_path(proj)
    if not reg.is_file():
        return []
    try:
        data = json.loads(reg.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Refused("Figuras/registro.json ilegible; corrígelo antes de registrar") from None
    if not isinstance(data, list) or not all(isinstance(e, dict) for e in data):
        raise Refused("Figuras/registro.json no es una lista de figuras")
    return data


def _write_registry(proj: Path, entries: list[dict]) -> None:
    reg = registry_path(proj)
    reg.parent.mkdir(parents=True, exist_ok=True)
    entries = sorted(entries, key=lambda e: (len(str(e.get("id"))), str(e.get("id"))))
    tmp = reg.with_name(f".{reg.name}.{os.getpid()}.tmp")
    tmp.write_bytes((json.dumps(entries, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    os.replace(tmp, reg)


def resolve_figure(vault: Path, proj: Path, raw: str) -> Path | None:
    """The file of a registry `path` (project-relative or vault-relative), or None when it
    would fall outside the project's Figuras/."""
    p = Path(str(raw or ""))
    if not raw or p.is_absolute():
        return None
    folder = (proj / FOLDER).resolve()
    for cand in (proj / p, Path(vault) / p):
        try:
            cand.resolve().relative_to(folder)
        except ValueError:
            continue
        return cand
    return None


def _next_id(entries: list[dict]) -> str:
    nums = [int(e["id"][2:]) for e in entries if FIG_ID.match(str(e.get("id", "")))]
    return f"F-{(max(nums) + 1 if nums else 1):03d}"


def _name(fid: str, src: Path) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", src.stem.lower()).strip("-")[:40]
    return f"{fid}-{stem}{src.suffix.lower()}" if stem else f"{fid}{src.suffix.lower()}"


def _not_sensitive(vault: Path, f: Path) -> None:
    import data_guard  # noqa: PLC0415
    try:
        roots = data_guard.sensitive_roots(Path(vault))
    except data_guard.RegistryError:
        raise Refused("registro de datos ilegible; corrígelo antes de registrar una figura") from None
    f = Path(data_guard._plain(str(f.resolve())))
    if any(data_guard._inside(f, r) for r in roots):
        raise Refused("--file está dentro de un dataset sensible: una figura se dibuja solo desde estimate-json")


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def register(vault: Path, slug: str, file: str, question: str, script: str, commit: str, caption: str,
             wide: bool = False, fid: str | None = None, by: str = "investigador",
             do_commit: bool = True) -> dict:
    vault = Path(vault)
    proj = project_dir(vault, slug)
    src = Path(file)
    if not src.is_absolute():
        raise Refused("--file debe ser una ruta absoluta")
    if src.suffix.lower() not in SUFFIXES:
        raise Refused("--file debe ser .pdf o .png (la exportación a LaTeX solo admite esos)")
    if not src.is_file():
        raise Refused("--file no existe")
    _not_sensitive(vault, src)
    if not caption.strip():
        raise Refused("--caption vacío: toda figura lleva su pie")
    script = script.strip().replace("\\", "/")
    if not script or Path(script).is_absolute() or re.match(r"^[A-Za-z]:", script) or ".." in script.split("/"):
        raise Refused("--script debe ser la ruta del script relativa al repositorio de código")
    commit = commit.strip().lower()
    if not _SHA.match(commit):
        raise Refused("--commit debe ser el sha (7 a 40 hex) del commit del repositorio de código con el script")
    if fid is not None and not FIG_ID.match(fid):
        raise Refused("--id debe ser F-NNN (tres o cuatro cifras)")
    node = find_question(vault, question, proj)
    est = estimate_sha(node)
    if est is None:
        raise Refused(f"{question} no tiene bloque ```estimate-json en ## Resultado: estima la pregunta "
                      "antes de dibujar su figura")
    entries = load_registry(proj)
    top = None
    if do_commit:
        r = _git(["rev-parse", "--show-toplevel"], proj)
        if r.returncode != 0:
            raise Refused("el vault no es un repositorio git; usa --no-commit o inicializa el repositorio")
        top = Path(r.stdout.strip()).resolve()
    fid = fid or _next_id(entries)
    old = next((e for e in entries if e.get("id") == fid), None)
    data = src.read_bytes()
    rel = f"{FOLDER}/{_name(fid, src)}"
    dest = proj / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    removed = None
    if old is not None:
        prev = resolve_figure(vault, proj, str(old.get("path", "")))
        if prev is not None and prev.resolve() != dest.resolve() and prev.is_file():
            prev.unlink()
            removed = prev
    entry = {"id": fid, "path": rel, "sha256": _sha(data), "caption": caption.strip(),
             "source": {"question": question, "estimate_sha256": est},
             "script": script, "commit": commit, "wide": bool(wide),
             "registered_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "by": by}
    entries = [e for e in entries if e.get("id") != fid] + [entry]
    _write_registry(proj, entries)
    out = {"figure": entry, "replaced": old is not None, "commit": None}
    if do_commit:
        paths = [registry_path(proj), dest] + ([removed] if removed else [])
        rels = [p.resolve().relative_to(top).as_posix() for p in paths]
        add = _git(["add", "-A", "--", *rels], top)
        msg = f"{fid}: figura de {question}" + (" (actualizada)" if old else "")
        done = _git(["commit", "-q", "-m", msg, "--", *rels], top) if add.returncode == 0 else add
        if done.returncode != 0:
            raise Refused(f"git no pudo hacer el commit de la figura: {(done.stderr or done.stdout).strip()[:300]}")
        out["commit"] = _git(["rev-parse", "HEAD"], top).stdout.strip()
    return out


def check(vault: Path, slug: str, fid: str | None = None) -> dict:
    vault = Path(vault)
    proj = project_dir(vault, slug)
    entries = load_registry(proj)
    if fid is not None:
        entries = [e for e in entries if e.get("id") == fid]
        if not entries:
            raise Refused(f"{fid} no está en Figuras/registro.json")
    results = []
    for e in entries:
        problems = []
        f = resolve_figure(vault, proj, str(e.get("path", "")))
        if f is None:
            problems.append("fuera_de_figuras")
        elif not f.is_file():
            problems.append("fichero_ausente")
        elif _sha(f.read_bytes()) != e.get("sha256"):
            problems.append("fichero_cambiado")
        src = e.get("source") if isinstance(e.get("source"), dict) else {}
        q = str(src.get("question", ""))
        try:
            now = estimate_sha(find_question(vault, q, proj))
        except Refused:
            now = None
        if now is None or now != src.get("estimate_sha256"):
            problems.insert(0, "obsoleta")
        results.append({"id": e.get("id"), "question": q, "status": problems[0] if problems else "ok",
                        "problems": problems})
    bad = [r["id"] for r in results if r["problems"]]
    return {"figures": results, "obsoletas": [r["id"] for r in results if "obsoleta" in r["problems"]],
            "con_problemas": bad}


def extract_json(vault: Path, question: str, out: str, slug: str | None = None) -> dict:
    vault = Path(vault)
    proj = project_dir(vault, slug) if slug else None
    node = find_question(vault, question, proj)
    text = estimate_block(node)
    if text is None:
        raise Refused(f"{question} no tiene bloque ```estimate-json en ## Resultado")
    dest = Path(out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    dest.write_bytes(data)
    return {"out": str(dest), "question": question, "estimate_sha256": _sha(data)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("register")
    c = sub.add_parser("check")
    li = sub.add_parser("list")
    x = sub.add_parser("extract-json")
    for p in (r, c, li, x):
        p.add_argument("--vault", required=True)
    for p in (r, c, li):
        p.add_argument("--project", required=True)
    x.add_argument("--project")
    r.add_argument("--file", required=True)
    r.add_argument("--question", required=True)
    r.add_argument("--script", required=True)
    r.add_argument("--commit", required=True)
    r.add_argument("--caption", required=True)
    r.add_argument("--wide", action="store_true")
    r.add_argument("--id")
    r.add_argument("--by", default="investigador")
    r.add_argument("--no-commit", action="store_true")
    c.add_argument("--id")
    x.add_argument("--question", required=True)
    x.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    code = 0
    try:
        if a.cmd == "register":
            res = register(Path(a.vault), a.project, a.file, a.question, a.script, a.commit, a.caption,
                           a.wide, a.id, a.by, not a.no_commit)
        elif a.cmd == "check":
            res = check(Path(a.vault), a.project, a.id)
            code = 4 if res["con_problemas"] else 0
        elif a.cmd == "list":
            res = {"figures": load_registry(project_dir(Path(a.vault), a.project))}
        else:
            res = extract_json(Path(a.vault), a.question, a.out, a.project)
    except Refused as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 3
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
