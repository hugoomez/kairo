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

def sample(vault: Path, slug: str, name: str, n: int, seed: int, glob: str | None, replace: bool) -> dict:
    if n < 1:
        raise Refused("--n debe ser al menos 1")
    ds = _dataset(vault, slug, name)
    out = vault / "Projects" / slug / "Datos" / "depuracion" / name
    _check_out(vault, out, ds["path"], replace)
    rng = random.Random(seed)
    stats: Counter = Counter()
    kinds: Counter = Counter()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".depuracion-", dir=out.parent))
    try:
        if ds["kind"] == "dir":
            root = ds["path"]
            files = []
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
            "salida": f"Projects/{slug}/Datos/depuracion/{name}",
            "creado": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
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
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        m = sample(Path(a.vault), a.project, a.dataset, a.n, a.seed, a.glob, a.replace)
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
