#!/usr/bin/env python3
"""A declared pipeline for a measurement study, run from the researcher's code repository.

    pipeline.py run    --vault V --project <slug> --file <repo>/pipeline.toml
                       [--stage S | --from S] [--timeout SEC] [--no-commit]
    pipeline.py status --vault V --project <slug> --file <repo>/pipeline.toml [--rehash]

pipeline.toml (in the code repository, committed with the code):

    [[stage]]
    name = "parseado"                       # its output is registered under this name
    script = ["python", "src/parse.py", "--in", "{input:recoleccion}", "--out", "{output}"]
    inputs = ["recoleccion"]                # registered datasets, or earlier stages
    output = "D:/estudio/parseado.jsonl"    # absolute, outside the vault
    sensitive = true                        # optional; default: sensitive when an input is
    timeout = 3600                          # optional, seconds
    params = { umbral = 3 }                 # optional; recorded in the lineage

`script` is an argv list, run with no shell, cwd = the repository's top folder. In it
`{input:<name>}` becomes the registered path of that input, `{output}` the stage's output
and `{params}` the JSON of `params`; nothing else is replaced. The first word of the argv
that is a file of the repository is the lineage's `script`. Stages run in file order; an
input that names a stage must name an earlier one.

run refuses when the code repository has uncommitted changes and records its HEAD. For
each stage it re-fingerprints every input against the registry (lineage.py), runs the
argv with stdout and stderr to `<output>.log` (next to the output, outside the vault),
then registers the output with its lineage (`lineage.register_derived`, `--rederive` when
it is already registered) and appends one line to `Projects/<slug>/Datos/pipeline-runs.jsonl`:
{stage, dataset, commit, script, argv (as written in pipeline.toml), inputs [{name,
sha256}], output_sha256, start, end, exit_code, estado, log}. The stage's output text is
never copied into the vault, into the record or onto stdout. A stage that fails (exit code
not 0, timeout, no output, or it changed the code repository) stops the run; its record is
kept. After each stage the registry and the runs file are committed in the vault (git add +
commit --only), unless --no-commit. The output may lie inside the code repository only
where git ignores it (`git check-ignore`), log included.

status compares each stage with its last successful run: never run; the code commit
changed; an input's registered sha256 is not the one it ran on; the output's registered
sha256 is not the one it produced; or an upstream stage is stale. `--rehash` also
re-fingerprints inputs and outputs against the registry.

Datasets are named by name; no path of a dataset ever appears on the command line, so the
data guard lets both commands run when the data are registered sensitive. Contents are
hashed and handed to the stage's own code, never read or printed here. Limit: the data
guard protects a sensitive output, not its `.log`; a stage on sensitive data should not
print rows, and nobody (no model) should read that log.

Exit 0 ok · 2 a stage failed · 3 refused ({"error": ...}) · 1 unexpected error.
Python 3.11+ (tomllib). Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEDGER = HERE.parent / "ledger"
if str(LEDGER) not in sys.path:
    sys.path.insert(0, str(LEDGER))
import lineage  # noqa: E402

Refused = lineage.Refused
RUNS = ("Datos", "pipeline-runs.jsonl")
STAGE_KEYS = {"name", "script", "inputs", "output", "sensitive", "timeout", "params"}
PLACEHOLDER = re.compile(r"\{(output|params|input:[^{}]+)\}")


# ---- pipeline.toml ------------------------------------------------------------

def load(file: Path) -> list[dict]:
    try:
        data = tomllib.loads(Path(file).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise Refused("no existe el fichero del pipeline") from None
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise Refused(f"el pipeline no se puede leer ({type(e).__name__}: {str(e)[:120]})") from None
    extra = set(data) - {"stage"}
    if extra:
        raise Refused(f"el pipeline solo admite tablas [[stage]] (sobra: {', '.join(sorted(extra))})")
    stages = data.get("stage")
    if not isinstance(stages, list) or not stages:
        raise Refused("el pipeline no declara ninguna etapa [[stage]]")
    seen: dict[str, int] = {}
    outputs: list[str] = []
    out = []
    for i, st in enumerate(stages):
        where = f"etapa {i + 1}"
        if not isinstance(st, dict):
            raise Refused(f"{where}: no es una tabla")
        bad = set(st) - STAGE_KEYS
        if bad:
            raise Refused(f"{where}: claves desconocidas {', '.join(sorted(bad))}")
        name = st.get("name")
        if not isinstance(name, str) or not lineage.NAME.match(name):
            raise Refused(f"{where}: name debe tener de 1 a 80 letras, cifras, espacios, . _ -")
        where = f"etapa «{name}»"
        if name.casefold() in seen:
            raise Refused(f"{where}: el nombre está repetido")
        argv = st.get("script")
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
            raise Refused(f"{where}: script debe ser una lista de palabras (argv), sin shell")
        inputs = st.get("inputs")
        if not isinstance(inputs, list) or not inputs or not all(isinstance(x, str) and x.strip() for x in inputs):
            raise Refused(f"{where}: inputs debe nombrar al menos un dataset o una etapa anterior")
        inputs = [x.strip() for x in inputs]
        names = {s["name"] for s in out}
        later = {s.get("name") for s in stages[i + 1:] if isinstance(s, dict)}
        for x in inputs:
            if x == name:
                raise Refused(f"{where}: no puede usarse a sí misma como entrada")
            if x not in names and x in later:
                raise Refused(f"{where}: usa la etapa «{x}» antes de que se declare; ordena las etapas")
        for m in PLACEHOLDER.finditer(" ".join(argv)):
            ref = m.group(1)
            if ref.startswith("input:") and ref[6:] not in inputs:
                raise Refused(f"{where}: {{{ref}}} no es una de sus entradas")
        output = st.get("output")
        if not isinstance(output, str) or not Path(output).is_absolute():
            raise Refused(f"{where}: output debe ser una ruta absoluta")
        if any(lineage.within(output, o) or lineage.within(o, output) for o in outputs):
            raise Refused(f"{where}: su salida coincide o se solapa con la de otra etapa")
        sens = st.get("sensitive")
        if sens is not None and not isinstance(sens, bool):
            raise Refused(f"{where}: sensitive debe ser true o false")
        tmo = st.get("timeout")
        if tmo is not None and (isinstance(tmo, bool) or not isinstance(tmo, (int, float)) or tmo <= 0):
            raise Refused(f"{where}: timeout debe ser un número de segundos positivo")
        params = st.get("params")
        if params is not None and not isinstance(params, dict):
            raise Refused(f"{where}: params debe ser una tabla")
        if params and "argv" in params:
            raise Refused(f"{where}: params no puede tener la clave argv (se registra aparte)")
        seen[name.casefold()] = i
        outputs.append(output)
        out.append({"name": name, "argv": list(argv), "inputs": inputs, "output": output, "sensitive": sens,
                    "timeout": tmo, "params": params or {}})
    return out


def _select(stages: list[dict], stage: str | None, start: str | None) -> list[dict]:
    names = [s["name"] for s in stages]
    for x in (stage, start):
        if x is not None and x not in names:
            raise Refused(f"no hay ninguna etapa «{x}» en el pipeline")
    if stage is not None:
        return [s for s in stages if s["name"] == stage]
    if start is not None:
        return stages[names.index(start):]
    return stages


# ---- the code repository -------------------------------------------------------

def repo_of(file: Path) -> Path:
    r = lineage._git(["rev-parse", "--show-toplevel"], Path(file).resolve().parent)
    if r.returncode != 0:
        raise Refused("el pipeline no está en un repositorio git: la etapa debe correr código versionado")
    return Path(r.stdout.strip()).resolve()


def head(repo: Path) -> str:
    r = lineage._git(["rev-parse", "HEAD"], repo)
    if r.returncode != 0:
        raise Refused("el repositorio de código no tiene ningún commit")
    return r.stdout.strip().lower()


def dirty(repo: Path) -> int:
    r = lineage._git(["status", "--porcelain", "--untracked-files=normal"], repo)
    if r.returncode != 0:
        raise Refused("git status falló en el repositorio de código")
    return len([x for x in r.stdout.splitlines() if x.strip()])


def _ignored(repo: Path, p: Path) -> bool:
    return lineage._git(["check-ignore", "-q", "--no-index", str(p)], repo).returncode == 0


def _script_rel(repo: Path, argv: list[str], where: str) -> str:
    for a in argv:
        if PLACEHOLDER.search(a):
            continue
        p = Path(a)
        p = p if p.is_absolute() else repo / p
        try:
            if p.is_file() and lineage.within(p.resolve(), repo):
                return p.resolve().relative_to(repo).as_posix()
        except (OSError, ValueError):
            continue
    raise Refused(f"{where}: ninguna palabra de script es un fichero del repositorio de código")


# ---- run ---------------------------------------------------------------------

def _context(vault: Path, slug: str, file: Path) -> tuple[Path, Path, list[dict]]:
    vault = Path(vault)
    if not vault.is_dir():
        raise Refused("no existe la carpeta del vault")
    reg = lineage.registry_path(vault, slug)
    stages = load(file)
    repo = repo_of(file)
    if lineage.within(repo, vault) or lineage.within(vault, repo):
        raise Refused("el repositorio de código va fuera del vault, y el vault fuera de él")
    return reg, repo, stages


def _append_run(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _expand(argv: list[str], paths: dict[str, str], output: str, params: dict) -> list[str]:
    def sub(m: re.Match) -> str:
        ref = m.group(1)
        if ref == "output":
            return output
        if ref == "params":
            return json.dumps(params, ensure_ascii=False, sort_keys=True)
        return paths[ref[6:]]
    return [PLACEHOLDER.sub(sub, a) for a in argv]


def _check_output(vault: Path, repo: Path, entries: list, st: dict) -> Path:
    where = f"etapa «{st['name']}»"
    out = Path(os.path.abspath(st["output"]))
    log = Path(str(out) + ".log")
    if lineage.within(out, vault) or lineage.within(vault, out):
        raise Refused(f"{where}: la salida va fuera del vault")
    for e in entries:
        if not isinstance(e, dict) or e.get("name") == st["name"]:
            continue
        ep = e.get("path")
        if isinstance(ep, str) and Path(ep).is_absolute() and (lineage.within(out, ep) or lineage.within(ep, out)
                                                               or lineage.within(log, ep)):
            raise Refused(f"{where}: la salida coincide o se solapa con el dataset «{e.get('name')}»")
    for p in (out, log):
        if lineage.within(p, repo) and not _ignored(repo, p):
            raise Refused(f"{where}: la salida y su log caen dentro del repositorio de código sin que git los "
                          "ignore; escríbelos fuera del repositorio o añádelos a .gitignore")
    return out


def run_stage(vault: Path, slug: str, reg: Path, repo: Path, commit: str, st: dict, *, timeout: float | None,
              by: str, do_commit: bool) -> dict:
    where = f"etapa «{st['name']}»"
    entries = lineage.read_registry(reg)
    inputs, paths = [], {}
    for x in st["inputs"]:
        e = lineage.find(entries, x)
        if e is None:
            raise Refused(f"{where}: la entrada «{x}» no está en el registro (si es una etapa, córrela antes)")
        inputs.append({"name": x, "sha256": lineage.check_registered(e)})
        paths[x] = str(lineage.entry_path(e, "entrada"))
    out = _check_output(vault, repo, entries, st)
    script = _script_rel(repo, st["argv"], where)
    log = Path(str(out) + ".log")
    log.parent.mkdir(parents=True, exist_ok=True)
    argv = _expand(st["argv"], paths, str(out), st["params"])
    tmo = st["timeout"] if st["timeout"] is not None else timeout
    rec = {"stage": st["name"], "dataset": st["name"], "commit": commit, "script": script, "argv": st["argv"],
           "inputs": inputs, "output_sha256": None, "start": lineage.now_iso(), "end": None, "exit_code": None,
           "estado": None, "log": str(log).replace("\\", "/")}
    with open(log, "wb") as fh:
        try:
            proc = subprocess.run(argv, cwd=repo, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                                  timeout=tmo)
            rec["exit_code"] = proc.returncode
            rec["estado"] = "ok" if proc.returncode == 0 else "fallo"
        except subprocess.TimeoutExpired:
            rec["estado"] = "tiempo agotado"
        except OSError as e:
            fh.write(f"[pipeline.py] no se pudo ejecutar: {type(e).__name__}\n".encode("utf-8"))
            rec["estado"] = "no se pudo ejecutar"
    rec["end"] = lineage.now_iso()
    result = {"stage": st["name"], "estado": rec["estado"], "exit_code": rec["exit_code"], "log": rec["log"]}
    if rec["estado"] == "ok" and not out.exists():
        rec["estado"] = result["estado"] = "sin salida"
    if rec["estado"] == "ok" and dirty(repo):
        rec["estado"] = result["estado"] = "repositorio modificado"
    if rec["estado"] == "ok":
        try:
            sens = st["sensitive"]
            res = lineage.register_derived(
                vault, slug, st["name"], str(out), st["inputs"], script, commit, repo=repo,
                params={"argv": st["argv"], **st["params"]}, sensitive=sens,
                rederive=lineage.find(lineage.read_registry(reg), st["name"]) is not None,
                produced_at=rec["end"], by=by, do_commit=False)
        except Refused as e:
            rec["estado"] = result["estado"] = "no registrada"
            result["error"] = str(e)
        else:
            rec["output_sha256"] = result["output_sha256"] = res["sha256"]
            result["sensitive"] = res["sensitive"]
            result["registro_cambia"] = res["changed"]
            if res.get("avisos"):
                result["avisos"] = res["avisos"]
    runs = reg.parent / RUNS[1]
    _append_run(runs, rec)
    if do_commit:
        verdict = "ok" if rec["estado"] == "ok" else rec["estado"]
        result["vault_commit"] = lineage.commit_only(
            vault, [reg, runs], f"Datos {slug}: pipeline, etapa «{st['name']}» ({verdict}, código {commit[:10]})")
    return result


def run(vault: Path, slug: str, file: Path, *, stage: str | None = None, start: str | None = None,
        timeout: float | None = None, by: str | None = None, do_commit: bool = True) -> tuple[int, dict]:
    if stage is not None and start is not None:
        raise Refused("--stage y --from no van juntos")
    vault = Path(vault)
    reg, repo, stages = _context(vault, slug, file)
    chosen = _select(stages, stage, start)
    n = dirty(repo)
    if n:
        raise Refused(f"el repositorio de código tiene {n} fichero(s) con cambios sin commit; haz commit antes "
                      "de correr el pipeline (el linaje registra el commit)")
    commit = head(repo)
    by = by or lineage._git_user(vault)
    done = []
    for st in chosen:
        r = run_stage(vault, slug, reg, repo, commit, st, timeout=timeout, by=by, do_commit=do_commit)
        done.append(r)
        if r["estado"] != "ok":
            return 2, {"commit": commit, "etapas": done, "detenido_en": st["name"]}
    return 0, {"commit": commit, "etapas": done}


# ---- status ------------------------------------------------------------------

def _runs(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def status(vault: Path, slug: str, file: Path, *, rehash: bool = False) -> dict:
    vault = Path(vault)
    reg, repo, stages = _context(vault, slug, file)
    entries = lineage.read_registry(reg)
    runs = _runs(reg.parent / RUNS[1])
    now = head(repo)
    stale: set[str] = set()
    rows = []
    for st in stages:
        name = st["name"]
        ok_runs = [r for r in runs if r.get("stage") == name and r.get("estado") == "ok"]
        last = ok_runs[-1] if ok_runs else None
        tried = [r for r in runs if r.get("stage") == name]
        row: dict = {"stage": name}
        reasons: list[str] = []
        if last is None:
            row["estado"] = "sin correr"
            stale.add(name)
        else:
            row["ultima"] = {"commit": last.get("commit"), "end": last.get("end"),
                             "output_sha256": last.get("output_sha256")}
            if str(last.get("commit") or "").lower() != now:
                reasons.append(f"el código cambió (commit {str(last.get('commit'))[:10]} → {now[:10]})")
            ran = {i.get("name"): i.get("sha256") for i in last.get("inputs") or [] if isinstance(i, dict)}
            for x in st["inputs"]:
                e = lineage.find(entries, x)
                if e is None:
                    reasons.append(f"la entrada «{x}» ya no está en el registro")
                elif x not in ran or str(e.get("sha256") or "").lower() != str(ran[x] or "").lower():
                    reasons.append(f"la entrada «{x}» cambió desde la última ejecución")
                elif rehash:
                    try:
                        lineage.check_registered(e)
                    except Refused:
                        reasons.append(f"la entrada «{x}» ya no coincide con su huella registrada")
                if x in stale:
                    reasons.append(f"aguas arriba: la etapa «{x}» no está al día")
            e = lineage.find(entries, name)
            if e is None:
                reasons.append("la salida ya no está en el registro")
            elif str(e.get("sha256") or "").lower() != str(last.get("output_sha256") or "").lower():
                reasons.append("la salida registrada no es la que produjo la última ejecución")
            elif rehash:
                try:
                    lineage.check_registered(e)
                except Refused:
                    reasons.append("la salida ya no coincide con su huella registrada")
            row["estado"] = "desactualizada" if reasons else "al día"
            if reasons:
                stale.add(name)
        if reasons:
            row["motivos"] = reasons
        if tried and tried[-1] is not last:
            row["ultimo_intento"] = {"estado": tried[-1].get("estado"), "end": tried[-1].get("end"),
                                     "commit": tried[-1].get("commit")}
        rows.append(row)
    return {"commit": now, "cambios_sin_commit": dirty(repo), "al_dia": not stale, "etapas": rows}


# ---- CLI ---------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline.py", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    r = sub.add_parser("run")
    s = sub.add_parser("status")
    for p in (r, s):
        p.add_argument("--vault", required=True)
        p.add_argument("--project", required=True)
        p.add_argument("--file", required=True)
    g = r.add_mutually_exclusive_group()
    g.add_argument("--stage")
    g.add_argument("--from", dest="start")
    r.add_argument("--timeout", type=float)
    r.add_argument("--by")
    r.add_argument("--no-commit", action="store_true")
    s.add_argument("--rehash", action="store_true")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    code = 0
    try:
        if a.mode == "run":
            if a.timeout is not None and a.timeout <= 0:
                raise Refused("--timeout debe ser positivo")
            code, res = run(Path(a.vault), a.project, Path(a.file), stage=a.stage, start=a.start,
                            timeout=a.timeout, by=a.by, do_commit=not a.no_commit)
        else:
            res = status(Path(a.vault), a.project, Path(a.file), rehash=a.rehash)
    except Refused as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 3
    except Exception as e:  # noqa: BLE001  never echo material: only the exception's type
        print(json.dumps({"error": f"error inesperado ({type(e).__name__})"}, ensure_ascii=False))
        return 1
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
