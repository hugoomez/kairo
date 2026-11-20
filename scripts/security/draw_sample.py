#!/usr/bin/env python3
"""A reproducible, redacted sample of a registered dataset, for the annotation panel.

    python draw_sample.py --vault V --project <slug> --dataset NAME --n N --seed S --out <abs .jsonl>
                          [--stratify-by COL --per-stratum K] [--filter COL=VAL ...] [--glob PAT]
                          [--id-field FIELD] [--pseudonymise-repos]

The dataset is looked up by NAME in `Projects/<slug>/Datos/registro.json` (debug_sample's
lookup), so the command line never names the dataset's path and data_guard lets it run.
Its sha256 is recomputed the way the app registers it (a file: its sha256; a folder: the
sha256 of the sorted `<sha256>  <relative path>` lines of its files, entries named `.git`
skipped) and the draw is refused when it differs from the registry.

Frame: the units that pass every `--filter COL=VAL` (all must hold; values compared as
text, booleans as true/false) and, for a folder, `--glob` (fnmatch on the relative posix
path). A file dataset (.jsonl / .csv) is sampled by rows; a folder by files, whose only
columns are `ext` (the lowercase suffix) and `carpeta` (the first folder).

Draw: reservoir sampling with random.Random(seed) over the frame in file order (rows) or
sorted path order (files); `--stratify-by COL --per-stratum K` keeps one reservoir per
value of COL (at most 50 values; every stratum gets K items, or all it has), and `--n` is
then a ceiling on the total. The items are written in an order shuffled by the same
generator, so the same arguments on the same dataset sha256 give a byte-identical file.

Each item has a stable `id`: the `--id-field` value when every row of the frame has one,
unique and clean, else sha256(<dataset sha256>:<row index | relative path>)[:16].
Stratified items also carry `_stratum` and `_weight` (= stratum frame / items written in
the stratum), so precision and recall can be weighted back to the frame.

- rows: every field, its values (and keys) through redact.redact_text; `author` / `email`
  pseudonymised and `path` / `file` given a pseudonymised path (as debug_sample); a row
  field named id, _stratum or _weight is kept as `<name>_original`.
- files: {"id", "path" (relative, folders hashed as debug_sample), "content"}: text through
  redact.redact_text, cut at 256 KiB with a `[TRUNCADO …]` line; a binary age file keeps
  its text header and the payload becomes `[BINARIO <n> bytes]`; any other binary is
  `[BINARIO <n> bytes]`.

Unlike debug_sample, `github.com/<owner>/<repo>` URLs and the repository identity fields
(repo, repository, owner, org, organization, url, html_url) are kept by default: a human
annotator has to open the public repository to judge the item, and the sample is for that
person, not for a model. `--pseudonymise-repos` pseudonymises them as debug_sample does.
Redaction is the normal one (redact.py), not the strict one: high-entropy strings that are
not secrets by shape (hashes, ciphertext, ids) stay readable for the annotator.

Fail closed: an item with anything `redact.py check` would still find (in any of its
strings or in its JSON line) is dropped whole and counted under `dropped`; the whole file is
checked again before it is written. `--out` must be an absolute `.jsonl` path outside the
vault, the dataset and every sensitive root (the app imports it afterwards into
`Datos/anotacion/<sample>/` and runs `redact.py check`); an existing file is replaced only
when it is a previous sample (its `<out>.manifest.json` exists).

stdout and `<out>.manifest.json` hold {"out", "n", "seed", "dataset", "dataset_sha256",
"frame_size", "strata": {value: {"frame", "drawn"}} | null, "dropped": {reason: count},
"redactions": {type: count}}: aggregates only, never rows or cell values (a stratum value
that looks like a secret, an address or a repository is shown as `estrato-<6 hex>`).
`dropped` also counts the unreadable rows of the file (`fila_ilegible`), which are outside
the frame. Exit 0 ok, 3 refused ({"error": ...}), 1 unexpected error. Standard library only.

Limits: redaction is pattern-based; a password with no recognisable shape in a free-text
field can survive. E-mail addresses inside free text are not redacted (only the
author / email fields are).
"""
from __future__ import annotations

import argparse
import csv
import fnmatch
import hashlib
import json
import os
import random
import sys
from collections import Counter
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import data_guard  # noqa: E402
import debug_sample as dbg  # noqa: E402
import redact  # noqa: E402
import strict_redact as sr  # noqa: E402

Refused = dbg.Refused

MAX_CONTENT = 256 * 1024
MAX_STRATA = 50
PEOPLE = {"author", "email"}
REPO_IDENTITY = dbg.IDENTITY - PEOPLE
PATHISH = dbg.PATHISH
RESERVED = ("id", "_stratum", "_weight")
DIR_COLUMNS = ("ext", "carpeta")
EMPTY = "(sin valor)"


# ---- fingerprint ------------------------------------------------------------

def _sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _walk(root: Path) -> list[str]:
    """Relative posix paths of the files under root, as the app's fingerprint walks them:
    entries named `.git` skipped, symbolic links not followed."""
    out = []
    for d, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(x for x in dirs if x != ".git" and not (Path(d) / x).is_symlink())
        for fn in names:
            p = Path(d) / fn
            if fn == ".git" or p.is_symlink() or not p.is_file():
                continue
            out.append(p.relative_to(root).as_posix())
    return sorted(out)


def _dir_sha(root: Path, files: list[str]) -> str:
    lines = sorted(f"{_sha_file(root / rel)}  {rel}" for rel in files)
    return hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()


def _check_sha(expected, actual: str) -> str:
    if not isinstance(expected, str) or not expected.strip():
        raise Refused("el dataset no tiene huella sha256 en el registro; vuelve a registrarlo")
    if actual != expected.strip().lower():
        raise Refused("la huella del dataset no coincide con el registro; "
                      "vuelve a registrar el dataset o restaura sus ficheros")
    return actual


# ---- arguments --------------------------------------------------------------

def _norm(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    return str(v).strip()


def _filters(raw: list[str] | None) -> list[tuple[str, str]]:
    out = []
    for f in raw or []:
        col, sep, val = f.partition("=")
        if not sep or not col.strip():
            raise Refused("--filter debe tener la forma COLUMNA=VALOR")
        out.append((col.strip(), val.strip()))
    return out


def _out_path(vault: Path, raw: str, src: Path) -> Path:
    out = Path(raw)
    if not out.is_absolute():
        raise Refused("--out debe ser una ruta absoluta")
    if out.suffix.lower() != ".jsonl":
        raise Refused("--out debe ser un fichero .jsonl")
    out = Path(data_guard._plain(str(out.resolve())))
    manifest = out.with_name(out.name + ".manifest.json")
    try:
        roots = data_guard.sensitive_roots(vault)
    except data_guard.RegistryError:
        raise Refused("registro de datos ilegible; corrígelo antes de sacar una muestra") from None
    if data_guard._inside(out, Path(data_guard._plain(str(vault.resolve())))):
        raise Refused("--out no puede estar dentro del vault: la interfaz importa la muestra después")
    for r in [*roots, Path(data_guard._plain(str(src.resolve())))]:
        if data_guard._inside(out, r) or data_guard._inside(manifest, r):
            raise Refused("--out cae dentro de datos protegidos; elige una ruta fuera de los datasets")
    if out.exists() and not (out.is_file() and manifest.is_file()):
        raise Refused("--out ya existe y no es una muestra anterior; no lo toco")
    return out


# ---- draw -------------------------------------------------------------------

class _Reservoirs:
    """One reservoir for the whole frame (stratum None) or one per stratum, all drawn
    from the same generator, so the result depends only on the seed and the frame order."""

    def __init__(self, rng: random.Random, size: int, stratified: bool):
        self.rng, self.size, self.stratified = rng, size, stratified
        self.seen: Counter = Counter()
        self.kept: dict = {}

    def add(self, stratum, unit) -> None:
        i = self.seen[stratum]
        self.seen[stratum] += 1
        if self.stratified and len(self.seen) > MAX_STRATA:
            raise Refused(f"la columna de estratos tiene más de {MAX_STRATA} valores; "
                          "estratifica por una categoría, no por un identificador")
        r = self.kept.setdefault(stratum, [])
        if len(r) < self.size:
            r.append(unit)
        else:
            j = self.rng.randrange(i + 1)
            if j < self.size:
                r[j] = unit


def _label(value: str) -> str:
    """How a stratum value is shown: as it is, unless it looks like material to hide."""
    if value == "":
        return EMPTY
    if len(value) > 80 or redact.has_secret(value) or sr.residual(value):
        return "estrato-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:6]
    return value


def _hash_id(sha: str, key: str) -> str:
    return hashlib.sha256(f"{sha}:{key}".encode("utf-8")).hexdigest()[:16]


# ---- items ------------------------------------------------------------------

def _redact(v, kinds: Counter, repos: bool):
    if isinstance(v, str):
        if repos:
            found: list[dict] = []
            v = sr._pseudonymise(v, found)
            kinds.update(x["type"] for x in found)
        out, f = redact.redact_text(v)
        kinds.update(x["type"] for x in f)
        return out
    if isinstance(v, list):
        return [_redact(x, kinds, repos) for x in v]
    if isinstance(v, dict):
        return {(_redact(k, kinds, repos) if isinstance(k, str) else k): _redact(x, kinds, repos)
                for k, x in v.items()}
    return v


def _row_fields(row: dict, kinds: Counter, repos: bool) -> dict:
    out = {}
    for k, v in row.items():
        lk = str(k).lower()
        if (lk in PEOPLE or repos and lk in REPO_IDENTITY) and isinstance(v, str) and v:
            nv = dbg._pseudo_identity(lk, v)
            kinds["email" if nv.startswith("[REDACTADO email") else "seudonimo"] += 1
        elif lk in PATHISH and isinstance(v, str):
            nv = sr.pseudo_path(v)
        else:
            nv = _redact(v, kinds, repos)
        out[_redact(k, kinds, repos) if isinstance(k, str) else k] = nv
    return out


def _age_header(raw: bytes, size: int) -> str | None:
    end = raw.find(b"\n---")
    if end < 0:
        return None
    nl = raw.find(b"\n", end + 1)
    nl = len(raw) if nl < 0 else nl + 1
    try:
        header = raw[:nl].decode("ascii")
    except UnicodeDecodeError:
        return None
    return header + f"[BINARIO {size - nl} bytes]\n"


def _decode(raw: bytes, cut: bool) -> str | None:
    if b"\x00" in raw:
        return None
    for k in range(4 if cut else 1):        # a cut may split the last character
        try:
            return raw[:len(raw) - k].decode("utf-8")
        except UnicodeDecodeError:
            continue
    return None


def _file_content(path: Path, kinds: Counter, repos: bool) -> str | None:
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            raw = fh.read(MAX_CONTENT)
    except OSError:
        return None
    cut = size > MAX_CONTENT
    if raw.startswith(dbg.AGE_MAGIC):
        head = _age_header(raw, size)
        if head is not None:
            return _redact(head, kinds, repos)
    text = _decode(raw, cut)
    if text is None:
        return f"[BINARIO {size} bytes]"
    text = _redact(text, kinds, repos)
    if cut:
        text += f"\n[TRUNCADO: el fichero tiene {size} bytes; se muestran los primeros {MAX_CONTENT}]\n"
    return text


def _clean(item: dict) -> bool:
    """What `redact.py check` would find nowhere: not in a string, not in the JSON line."""
    if any(redact.has_secret(s) for s in dbg._strings(item)):
        return False
    return not redact.has_secret(json.dumps(item, ensure_ascii=False))


# ---- frames -----------------------------------------------------------------

def _file_frame(path: Path, filters, strat: str | None, id_field: str | None, res: _Reservoirs,
                dropped: Counter) -> tuple[int, dict | None]:
    """Streams the rows into the reservoirs; (frame size, {id value per row index} or None)."""
    seen_cols: set = set()
    frame = 0
    ids: dict[int, str] | None = {} if id_field else None
    id_values: set[str] = set()
    try:
        for i, row in enumerate(dbg._rows(path)):
            if row is None:
                dropped["fila_ilegible"] += 1
                continue
            seen_cols.update(row.keys())
            if not all(_norm(row.get(c)) == v for c, v in filters):
                continue
            frame += 1
            if ids is not None:
                v = row.get(id_field)
                nv = _norm(v)
                if v is None or isinstance(v, (dict, list)) or not nv or nv in id_values:
                    ids = None
                else:
                    id_values.add(nv)
                    ids[i] = nv
            res.add(_norm(row.get(strat)) if strat else None, (i, row))
    except UnicodeDecodeError:
        raise Refused("el fichero del dataset no es UTF-8") from None
    except csv.Error:
        raise Refused("el CSV del dataset no se puede leer") from None
    if seen_cols:
        for c in [*(c for c, _v in filters), strat, id_field]:
            if c and c not in seen_cols:
                raise Refused(f"la columna {c!r} no está en el dataset")
    return frame, ids


def _dir_value(rel: str, col: str) -> str:
    if col == "ext":
        return PurePosixPath(rel).suffix.lower()
    parts = rel.split("/")
    return parts[0] if len(parts) > 1 else ""


def _dir_frame(files: list[str], glob: str | None, filters, strat: str | None, res: _Reservoirs) -> int:
    for c in [*(c for c, _v in filters), strat]:
        if c and c not in DIR_COLUMNS:
            raise Refused("en un dataset de carpeta solo se filtra o estratifica por ext o carpeta")
    frame = 0
    for i, rel in enumerate(files):
        if glob and not fnmatch.fnmatch(rel, glob):
            continue
        if not all(_dir_value(rel, c) == v for c, v in filters):
            continue
        frame += 1
        res.add(_dir_value(rel, strat) if strat else None, (i, rel))
    return frame


# ---- main -------------------------------------------------------------------

def draw(vault: Path, slug: str, name: str, n: int, seed: int, out_raw: str, *,
         stratify_by: str | None = None, per_stratum: int | None = None, filters: list[str] | None = None,
         glob: str | None = None, id_field: str | None = None, pseudonymise_repos: bool = False) -> dict:
    if n < 1:
        raise Refused("--n debe ser al menos 1")
    if (stratify_by is None) != (per_stratum is None):
        raise Refused("--stratify-by y --per-stratum van juntos")
    if per_stratum is not None and per_stratum < 1:
        raise Refused("--per-stratum debe ser al menos 1")
    flt = _filters(filters)
    if not vault.is_dir():
        raise Refused("no existe la carpeta del vault")
    ds = dbg._dataset(vault, slug, name)
    if ds["kind"] == "file" and glob:
        raise Refused("--glob solo vale para un dataset de carpeta")
    if ds["kind"] == "dir" and id_field:
        raise Refused("--id-field solo vale para un dataset de filas (.jsonl o .csv)")
    if ds["kind"] == "file" and ds["path"].suffix.lower() not in (".jsonl", ".ndjson", ".csv"):
        raise Refused("un dataset de tipo file debe ser .jsonl o .csv")
    out = _out_path(vault, out_raw, ds["path"])

    rng = random.Random(seed)
    stratified = stratify_by is not None
    res = _Reservoirs(rng, per_stratum if stratified else n, stratified)
    dropped: Counter = Counter()
    kinds: Counter = Counter()
    ids = None
    if ds["kind"] == "file":
        sha = _check_sha(ds["sha256"], _sha_file(ds["path"]))
        frame, ids = _file_frame(ds["path"], flt, stratify_by, id_field, res, dropped)
    else:
        files = _walk(ds["path"])
        if not files:
            raise Refused("la carpeta del dataset no tiene ficheros")
        sha = _check_sha(ds["sha256"], _dir_sha(ds["path"], files))
        frame = _dir_frame(files, glob, flt, stratify_by, res)

    if stratified:
        planned = sum(len(v) for v in res.kept.values())
        if planned > n:
            raise Refused(f"la muestra estratificada tendría {planned} elementos ({len(res.kept)} estratos "
                          f"× hasta {per_stratum}), más que --n {n}; sube --n o baja --per-stratum")
    labels = {s: _label(s) for s in res.seen} if stratified else {}
    if stratified and len(set(labels.values())) != len(labels):
        raise Refused("dos valores de la columna de estratos se muestran igual; revisa la columna")

    chosen = sorted(((s, u) for s, units in res.kept.items() for u in units), key=lambda x: x[1][0])
    if ids is not None and any(redact.has_secret(ids[u[0]]) for _s, u in chosen):
        ids = None
    items: list[tuple[object, dict]] = []
    for stratum, (idx, payload) in chosen:
        if ds["kind"] == "file":
            iid = ids[idx] if ids is not None else _hash_id(sha, str(idx))
            item = {"id": iid}
            for k, v in _row_fields(payload, kinds, pseudonymise_repos).items():
                if k == "id" and ids is not None and id_field == "id":
                    continue
                item[f"{k}_original" if k in RESERVED else k] = v
        else:
            content = _file_content(ds["path"] / payload, kinds, pseudonymise_repos)
            if content is None:
                dropped["ilegible"] += 1
                continue
            item = {"id": _hash_id(sha, payload), "path": sr.pseudo_path(payload), "content": content}
        if stratified:
            item["_stratum"] = labels[stratum]
        if not _clean(item):
            dropped["residuo_tras_redactar"] += 1
            continue
        items.append((stratum, item))

    strata = None
    if stratified:
        written = Counter(s for s, _i in items)
        strata = {labels[s]: {"frame": res.seen[s], "drawn": written[s]}
                  for s in sorted(res.seen, key=lambda s: labels[s])}
        for s, item in items:
            item["_weight"] = res.seen[s] / written[s]
    rng.shuffle(items)
    body = "".join(json.dumps(item, ensure_ascii=False) + "\n" for _s, item in items)
    if redact.has_secret(body):
        raise Refused("la muestra no pasa la comprobación de secretos; no se escribe")

    m = {"out": out_raw, "n": len(items), "seed": seed, "dataset": name, "dataset_sha256": sha,
         "frame_size": frame, "strata": strata, "dropped": dict(sorted(dropped.items())),
         "redactions": dict(sorted(kinds.items()))}
    out.parent.mkdir(parents=True, exist_ok=True)
    manifest = out.with_name(out.name + ".manifest.json")
    for dest, data in ((out, body), (manifest, json.dumps(m, ensure_ascii=False, indent=2) + "\n")):
        tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
        try:
            tmp.write_bytes(data.encode("utf-8"))
            os.replace(tmp, dest)
        finally:
            if tmp.exists():
                tmp.unlink()
    return m


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--stratify-by")
    ap.add_argument("--per-stratum", type=int)
    ap.add_argument("--filter", action="append", default=[])
    ap.add_argument("--glob")
    ap.add_argument("--id-field")
    ap.add_argument("--pseudonymise-repos", action="store_true")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        m = draw(Path(a.vault), a.project, a.dataset, a.n, a.seed, a.out, stratify_by=a.stratify_by,
                 per_stratum=a.per_stratum, filters=a.filter, glob=a.glob, id_field=a.id_field,
                 pseudonymise_repos=a.pseudonymise_repos)
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
