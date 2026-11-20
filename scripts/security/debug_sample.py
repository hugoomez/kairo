#!/usr/bin/env python3
"""A strictly redacted debug sample of a (sensitive) dataset, for writing and debugging parsers.

    python debug_sample.py --vault V --project <slug> --dataset NAME --n N --seed S
                           [--glob PAT] [--replace]

The dataset is looked up by NAME in `Projects/<slug>/Datos/registro.json` ({name, path
(absolute), kind file|dir, sha256, sensitive}), so the command line never names the
dataset's path and data_guard lets it run. The sample goes to
`Projects/<slug>/Datos/depuracion/<NAME>/`, outside every sensitive root:

- kind dir: N files drawn with random.Random(seed) from the files under the dataset
  (optionally those whose relative path matches --glob, fnmatch on the posix path),
  each strict-redacted (strict_redact.redact_strict, the file name as hint) and written
  under a pseudonymised relative path (folders hashed, file name kept). A binary age file
  (`age-encryption.org/v1`) keeps its text header up to the `---` line and its payload
  becomes `[BINARIO <n> bytes]`; other binaries are dropped; files over 1 MiB are dropped.
- kind file (.jsonl / .csv): N rows (reservoir sample, same seed) to `muestra.jsonl`;
  identity fields (repo, repository, owner, org, organization, url, html_url, author,
  email) are pseudonymised, `path`/`file` fields get a pseudonymised path, every other
  string is strict-redacted with the row's `path`/`file`/`name` as hint.

From a parse report (kind dir only):

    python debug_sample.py --vault V --project <slug> --dataset NAME --n N --seed S
                           --from-report REPORT --error-class C [--glob PAT] [--replace]

draws the N files only among the items whose LAST record in the parse report
(kairo_collect.parsereport's `parse-report.jsonl`) is `"status": "fail"` with
`"error_class": C`. A report item is the file's path relative to the dataset's
registered folder (posix, no `..`); items that are absolute, climb out, are symlinks or
no longer exist are skipped and counted (`no_encontrados`). REPORT is either the NAME
of a `file` dataset in the same registry (register the report as sensitive: its item
ids are real paths, owner/repo included, and the command line then names no path) or
an absolute path to the report. The sample goes to
`Projects/<slug>/Datos/depuracion/<NAME>__fallo-<C>/` (`/` in C becomes `_`), with
the same strict redaction and the same guarantees; its manifest adds
`informe: {clase_de_error, fallos_de_la_clase, no_encontrados}`, never an item id.

Fail closed: after redaction each item is checked again (strict_redact.residual); an item
with anything left is dropped whole and only counted. `manifest.json` and stdout hold
aggregates only (counts by reason and by redaction type): never contents, dropped paths
or material. Exit 0 ok, 3 refused ({"error": ...}), 1 unexpected error.
Standard library only.

Limits: redaction is pattern-based. A secret with no recognisable shape (a short
password in a free-text field of an unknown format) can survive; the sample is for
reading formats, not for publishing.
"""
from __future__ import annotations

import argparse
import csv
import fnmatch
import hashlib
import io
import json
import os
import random
import re
import shutil
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import data_guard  # noqa: E402
import strict_redact as sr  # noqa: E402

MAX_BYTES = 1024 * 1024
AGE_MAGIC = b"age-encryption.org/v1"
IDENTITY = {"repo", "repository", "owner", "org", "organization", "url", "html_url", "author", "email"}
PATHISH = {"path", "file"}
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*$")


class Refused(Exception):
    pass


_ERROR_CLASS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+\-/]{0,63}$")


def _report_path(vault: Path, slug: str, ref: str) -> Path:
    """A registered `file` dataset's path (ref is its name) or an absolute report path."""
    if _SAFE_NAME.match(ref) and not Path(ref).is_absolute():
        d = _dataset(vault, slug, ref)
        if d["kind"] != "file":
            raise Refused(f"el informe {ref!r} del registro debe ser de tipo file")
        return d["path"]
    p = Path(ref)
    if not p.is_absolute() or not p.is_file():
        raise Refused("--from-report debe ser el nombre de un dataset de tipo file del registro o una ruta "
                      "absoluta a parse-report.jsonl")
    return p


def _failed_items(report: Path, error_class: str) -> list[str]:
    """Items whose last record is a fail with `error_class` (parsereport's format)."""
    last: dict[str, dict] = {}
    try:
        with open(report, encoding="utf-8") as fh:
            for ln in fh:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(r, dict) and isinstance(r.get("item"), str):
                    last[r["item"]] = r
    except (OSError, UnicodeDecodeError):
        raise Refused("no se puede leer el informe de parseo") from None
    return sorted(i for i, r in last.items() if r.get("status") == "fail" and r.get("error_class") == error_class)


def _report_rel(item: str) -> str | None:
    p = item.replace("\\", "/")
    parts = [x for x in p.split("/") if x not in ("", ".")]
    if not parts or p.startswith("/") or ".." in parts or re.match(r"^[A-Za-z]:", p) or "\x00" in p:
        return None
    return "/".join(parts)


def _dataset(vault: Path, slug: str, name: str) -> dict:
    if not _SAFE_NAME.match(slug) or not _SAFE_NAME.match(name):
        raise Refused("el proyecto y el dataset deben ser nombres simples (letras, cifras, . _ -)")
    proj = vault / "Projects" / slug
    if not proj.is_dir():
        raise Refused(f"no existe el proyecto Projects/{slug}")
    reg = proj / "Datos" / "registro.json"
    try:
        data = json.loads(reg.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        raise Refused(f"falta o es ilegible Projects/{slug}/Datos/registro.json") from None
    if not isinstance(data, list):
        raise Refused(f"Projects/{slug}/Datos/registro.json no es una lista de datasets")
    hits = [d for d in data if isinstance(d, dict) and d.get("name") == name]
    if len(hits) != 1:
        raise Refused(f"el dataset {name!r} {'no está' if not hits else 'está repetido'} en el registro del proyecto")
    d = hits[0]
    p = d.get("path")
    if not isinstance(p, str) or not Path(p).is_absolute():
        raise Refused(f"el dataset {name!r} no tiene una ruta absoluta en el registro")
    if d.get("kind") not in ("file", "dir"):
        raise Refused(f"el dataset {name!r} no es de tipo file ni dir")
    path = Path(p)
    if d["kind"] == "dir" and not path.is_dir() or d["kind"] == "file" and not path.is_file():
        raise Refused(f"no se encuentra el contenido del dataset {name!r}")
    return {"kind": d["kind"], "path": path, "sha256": d.get("sha256")}


def _inside(a: Path, b: Path) -> bool:
    try:
        a.resolve().relative_to(b.resolve())
        return True
    except ValueError:
        return False


def _check_out(vault: Path, out: Path, src: Path, replace: bool) -> None:
    try:
        roots = data_guard.sensitive_roots(vault)
    except data_guard.RegistryError:
        raise Refused("registro de datos ilegible; corrígelo antes de sacar una muestra") from None
    for r in [*roots, src]:
        if _inside(out, r) or _inside(r, out):
            raise Refused("la carpeta de salida coincide con datos protegidos; revisa el registro del proyecto")
    if out.exists():
        if not replace:
            raise Refused("ya hay una muestra de depuración de este dataset; usa --replace para rehacerla")
        if not (out / "manifest.json").is_file():
            raise Refused("la carpeta de salida existe y no es una muestra de depuración; no la toco")


# ---- items -----------------------------------------------------------------

def _age_binary(raw: bytes) -> str | None:
    end = raw.find(b"\n---")
    if end < 0:
        return None
    nl = raw.find(b"\n", end + 1)
    nl = len(raw) if nl < 0 else nl + 1
    try:
        header = raw[:nl].decode("ascii")
    except UnicodeDecodeError:
        return None
    return header + f"[BINARIO {len(raw) - nl} bytes]\n"


def _file_item(path: Path, rel: str, stats: Counter, kinds: Counter) -> tuple[str, str] | None:
    try:
        if path.stat().st_size > MAX_BYTES:
            stats["grande"] += 1
            return None
        raw = path.read_bytes()
    except OSError:
        stats["ilegible"] += 1
        return None
    if raw.startswith(AGE_MAGIC):
        text = _age_binary(raw)
        if text is None:
            stats["binario"] += 1
            return None
    else:
        if b"\x00" in raw:
            stats["binario"] += 1
            return None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            stats["binario"] += 1
            return None
    hint = Path(rel).name
    out, findings = sr.redact_strict(text, hint)
    if sr.residual(out, hint):
        stats["residuo_tras_redactar"] += 1
        return None
    kinds.update(f["type"] for f in findings)
    return sr.pseudo_path(rel), out


def _pseudo_identity(key: str, v):
    if not isinstance(v, str) or not v:
        return v
    if "email" in key.lower() or "@" in v:
        return f"[REDACTADO email sha256:{hashlib.sha256(v.lower().encode()).hexdigest()[:12]}]"
    found: list[dict] = []
    out = sr._pseudonymise(v, found)
    if found:
        return out
    return f"{key.lower()}-{hashlib.sha256((key.lower() + ':' + v.lower()).encode()).hexdigest()[:6]}"


def _redact_value(v, hint: str, kinds: Counter):
    if isinstance(v, str):
        out, f = sr.redact_strict(v, hint)
        kinds.update(x["type"] for x in f)
        return out
    if isinstance(v, list):
        return [_redact_value(x, hint, kinds) for x in v]
    if isinstance(v, dict):
        return {k: _redact_value(x, hint, kinds) for k, x in v.items()}
    return v


def _strings(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, list):
        for x in v:
            yield from _strings(x)
    elif isinstance(v, dict):
        for x in v.values():
            yield from _strings(x)


def _row_item(row: dict, stats: Counter, kinds: Counter) -> dict | None:
    hint = next((str(row[k]) for k in ("path", "file", "name") if isinstance(row.get(k), str)), "")
    hint = Path(hint.replace("\\", "/")).name if hint else ""
    out = {}
    for k, v in row.items():
        lk = str(k).lower()
        if lk in IDENTITY:
            out[k] = _pseudo_identity(lk, v)
        elif lk in PATHISH and isinstance(v, str):
            out[k] = sr.pseudo_path(v)
        else:
            out[k] = _redact_value(v, hint, kinds)
    if any(sr.residual(s, hint) for s in _strings(out)) or sr.residual(json.dumps(out, ensure_ascii=False)):
        stats["residuo_tras_redactar"] += 1
        return None
    return out


def _rows(path: Path):
    suffix = path.suffix.lower()
    with open(path, encoding="utf-8-sig", errors="strict", newline="") as fh:
        if suffix == ".csv":
            yield from csv.DictReader(fh)
            return
        if suffix not in (".jsonl", ".ndjson"):
            raise Refused("un dataset de tipo file debe ser .jsonl o .csv")
        for line in fh:
            if line.strip():
                try:
                    r = json.loads(line)
                except ValueError:
                    yield None
                    continue
                yield r if isinstance(r, dict) else None


# ---- main ------------------------------------------------------------------

def sample(vault: Path, slug: str, name: str, n: int, seed: int, glob: str | None, replace: bool,
           from_report: str | None = None, error_class: str | None = None) -> dict:
    if n < 1:
        raise Refused("--n debe ser al menos 1")
    if (from_report is None) != (error_class is None):
        raise Refused("--from-report y --error-class van juntos")
    if error_class is not None and not _ERROR_CLASS.match(error_class):
        raise Refused("--error-class debe ser un identificador corto (letras, cifras, _ . + - /)")
    ds = _dataset(vault, slug, name)
    report_items: list[str] | None = None
    if from_report is not None:
        if ds["kind"] != "dir":
            raise Refused("--from-report solo vale para un dataset de tipo dir")
        report_items = _failed_items(_report_path(vault, slug, from_report), error_class)
    folder = name if error_class is None else f"{name}__fallo-{error_class.replace('/', '_')}"
    out = vault / "Projects" / slug / "Datos" / "depuracion" / folder
    _check_out(vault, out, ds["path"], replace)
    rng = random.Random(seed)
    stats: Counter = Counter()
    kinds: Counter = Counter()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".depuracion-", dir=out.parent))
    stats_report: dict = {}
    try:
        if ds["kind"] == "dir":
            root = ds["path"]
            files = []
            if report_items is not None:
                missing = 0
                for item in report_items:
                    rel = _report_rel(item)
                    p = root / rel if rel else None
                    if p is None or p.is_symlink() or not p.is_file() or not _inside(p, root):
                        missing += 1
                        continue
                    if glob and not fnmatch.fnmatch(rel, glob):
                        continue
                    files.append(rel)
                stats_report = {"clase_de_error": error_class, "fallos_de_la_clase": len(report_items),
                                "no_encontrados": missing}
            else:
                for d, dirs, names in os.walk(root, followlinks=False):
                    dirs.sort()
                    for fn in sorted(names):
                        p = Path(d) / fn
                        if p.is_symlink():
                            continue
                        rel = p.relative_to(root).as_posix()
                        if glob and not fnmatch.fnmatch(rel, glob):
                            continue
                        files.append(rel)
            avail = len(files)
            chosen = rng.sample(files, min(n, avail))
            kept = 0
            for rel in chosen:
                item = _file_item(root / rel, rel, stats, kinds)
                if item is None:
                    continue
                dest = tmp / item[0]
                if dest.exists():
                    stats["nombre_repetido"] += 1
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(item[1], encoding="utf-8", newline="")
                kept += 1
            unit = "fichero"
        else:
            reservoir: list = []
            avail = 0
            try:
                for i, r in enumerate(_rows(ds["path"])):
                    avail += 1
                    if len(reservoir) < n:
                        reservoir.append(r)
                    else:
                        j = rng.randrange(i + 1)
                        if j < n:
                            reservoir[j] = r
            except UnicodeDecodeError:
                raise Refused("el fichero del dataset no es UTF-8") from None
            buf = io.StringIO()
            kept = 0
            for r in reservoir:
                if r is None:
                    stats["fila_ilegible"] += 1
                    continue
                item = _row_item(r, stats, kinds)
                if item is None:
                    continue
                buf.write(json.dumps(item, ensure_ascii=False) + "\n")
                kept += 1
            (tmp / "muestra.jsonl").write_text(buf.getvalue(), encoding="utf-8")
            unit = "fila"
        manifest = {
            "dataset": name, "proyecto": slug, "unidad": unit, "seed": seed, "n_solicitado": n,
            "disponibles": avail, "conservados": kept, "descartados": dict(sorted(stats.items())),
            "redacciones": dict(sorted(kinds.items())),
            "salida": f"Projects/{slug}/Datos/depuracion/{folder}",
            "creado": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if report_items is not None:
            manifest["informe"] = stats_report
        (tmp / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")
        if out.exists():
            shutil.rmtree(out)
        os.replace(tmp, out)
        return manifest
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--glob")
    ap.add_argument("--replace", action="store_true")
    ap.add_argument("--from-report")
    ap.add_argument("--error-class")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        m = sample(Path(a.vault), a.project, a.dataset, a.n, a.seed, a.glob, a.replace,
                   from_report=a.from_report, error_class=a.error_class)
    except Refused as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 3
    except Exception as e:  # noqa: BLE001  never echo material: only the exception's type
        print(json.dumps({"error": f"error inesperado ({type(e).__name__})"}, ensure_ascii=False))
        return 1
    print(json.dumps(m, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
