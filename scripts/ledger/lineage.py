#!/usr/bin/env python3
"""Lineage of derived datasets in a project's registry.

    lineage.py register-derived --vault V --project <slug> --name N [--path ABS] --from A[,B]
                                --script S --commit C [--repo R] [--params JSON]
                                [--sensitive | --no-sensitive] [--rederive]
                                [--produced-at ISO] [--by WHO] [--no-commit]
    lineage.py show   --vault V --project <slug> --name N [--text]
    lineage.py verify --vault V --project <slug> [--name N]

A dataset of `Projects/<slug>/Datos/registro.json` (the app's entry: name, path, kind,
files, bytes, sha256, registered_at, by, sensitive) may carry its lineage:

    derived_from  [{"name", "sha256"}]  the registered datasets it was computed from, with
                                        the sha256 each had when it was derived
    script        path of the code inside the code repository (posix, relative)
    commit        the code repository's commit that ran
    params        JSON object (optional)
    produced_at   when the output was produced (ISO 8601)

An entry without `derived_from` (or with an empty one) is a root: raw data registered as
it is, such as a collection. `study_gates.plan_frozen` applies its data-before-freeze rule
to the roots of the units dataset's lineage.

register-derived fingerprints the output exactly as the app does (a file: its sha256; a
folder: the sha256 of the sorted `<sha256>  <relative path>` lines of its files, entries
named `.git` and symbolic links skipped), re-fingerprints every `--from` dataset and refuses
when one no longer matches the registry, and appends the entry. It never rewrites another
entry: an existing name is refused, unless `--rederive` and that entry is itself derived,
which is then replaced in place (a re-derivation after fixing a parser). With `--rederive`,
`--path` may be left out and the registered path is reused, so the command line never names
a sensitive dataset's path and the data guard lets it run. `--sensitive` / `--no-sensitive`
set the flag; by default the output is sensitive when any source is (or when the entry it
replaces was). The output must lie outside the vault and must not coincide with, contain or
lie inside another registered dataset. `--repo` checks that `--script` exists in `--commit`.
Then the registry alone is committed in the vault (`git add` + `git commit --only --
<registry>`, like the app), unless `--no-commit`.

show prints the chain: names, sha256s, scripts, commits, params, never paths. verify
re-fingerprints every dataset of the chain (or of the whole registry) and checks every link
(`derived_from` sha256 against the source's registered one); it prints names and sha256s
only. Contents are hashed, never printed.

Exit 0 ok · 2 verify found drift · 3 refused ({"error": ...}) · 1 unexpected error.
Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
SECURITY = HERE.parent / "security"

REGISTRY = ("Datos", "registro.json")
NAME = re.compile(r"^[^\W_][\w .-]{0,79}$")          # the app's rule: letters, digits, space . _ -
SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*$")
COMMIT = re.compile(r"^[0-9a-f]{7,64}$")
MAX_FILES = 1_000_000


class Refused(Exception):
    pass


# ---- registry ---------------------------------------------------------------

def registry_path(vault: Path, slug: str) -> Path:
    if not SLUG.match(slug or ""):
        raise Refused("el proyecto debe ser un nombre simple (letras, cifras, . _ -)")
    proj = Path(vault) / "Projects" / slug
    if not proj.is_dir():
        raise Refused(f"no existe el proyecto Projects/{slug}")
    return proj.joinpath(*REGISTRY)


def read_registry(reg: Path) -> list:
    if not reg.exists():
        return []
    try:
        data = json.loads(reg.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        raise Refused("Datos/registro.json es ilegible; corrígelo desde la interfaz") from None
    if not isinstance(data, list):
        raise Refused("Datos/registro.json no es una lista de datasets")
    return data


def write_registry(reg: Path, entries: list) -> None:
    """As the app writes it: JSON.stringify(list, null, 1) + newline, atomically."""
    reg.parent.mkdir(parents=True, exist_ok=True)
    tmp = reg.with_name(f"{reg.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes((json.dumps(entries, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
        os.replace(tmp, reg)
    finally:
        if tmp.exists():
            tmp.unlink()


def find(entries: list, name: str) -> dict | None:
    """The first entry named `name` (exactly), as estimate.py looks it up."""
    return next((e for e in entries if isinstance(e, dict) and e.get("name") == name), None)


def is_derived(entry: dict | None) -> bool:
    return isinstance(entry, dict) and bool(entry.get("derived_from"))


def lineage_roots(entries: list, name: str) -> tuple[list[dict], list[str]]:
    """The roots reached from `name` through `derived_from` (entries without lineage;
    `name` itself when it has none) and the broken links met on the way: a source missing
    from the registry, a source whose registered sha256 is not the one it was derived from,
    a malformed `derived_from`, a cycle. Only the registry is read."""
    by: dict[str, dict] = {}
    for e in entries:
        if isinstance(e, dict) and isinstance(e.get("name"), str):
            by.setdefault(e["name"], e)
    roots: list[dict] = []
    problems: list[str] = []
    seen: set[str] = set()

    def walk(n: str, trail: list[str]) -> None:
        if n in trail:
            problems.append(f"hay un ciclo en derived_from ({' → '.join([*trail, n])})")
            return
        e = by.get(n)
        if e is None:
            problems.append(f"«{n}» no está en el registro")
            return
        if n in seen:
            return
        seen.add(n)
        srcs = e.get("derived_from")
        if not srcs:
            roots.append(e)
            return
        if not isinstance(srcs, list):
            problems.append(f"«{n}»: derived_from no es una lista")
            return
        for s in srcs:
            if not isinstance(s, dict) or not isinstance(s.get("name"), str):
                problems.append(f"«{n}»: una entrada de derived_from no tiene name")
                continue
            src = by.get(s["name"])
            if src is not None and str(src.get("sha256") or "").strip().lower() != \
                    str(s.get("sha256") or "").strip().lower():
                problems.append(f"«{n}» se derivó de «{s['name']}» con otra sha256 que la registrada ahora; "
                                f"vuelve a derivar «{n}»")
            walk(s["name"], [*trail, n])

    walk(name, [])
    return roots, problems


def _ancestors(entries: list, name: str) -> set[str]:
    by = {e["name"]: e for e in entries if isinstance(e, dict) and isinstance(e.get("name"), str)}
    out: set[str] = set()
    todo = [name]
    while todo:
        e = by.get(todo.pop())
        for s in (e.get("derived_from") if isinstance(e, dict) and isinstance(e.get("derived_from"), list) else []):
            n = s.get("name") if isinstance(s, dict) else None
            if isinstance(n, str) and n not in out:
                out.add(n)
                todo.append(n)
    return out


# ---- fingerprint ------------------------------------------------------------

def _security():
    if str(SECURITY) not in sys.path:
        sys.path.insert(0, str(SECURITY))
    import draw_sample  # noqa: PLC0415  the app's folder fingerprint lives there
    return draw_sample


def fingerprint(path: Path) -> dict:
    """{kind, files, bytes, sha256} as the app registers it. Contents are hashed, never kept."""
    ds = _security()
    path = Path(path)
    if path.is_file():
        return {"kind": "file", "files": 1, "bytes": path.stat().st_size, "sha256": ds._sha_file(path)}
    if not path.is_dir():
        raise Refused("la ruta no es un fichero ni una carpeta")
    files = ds._walk(path)
    if len(files) > MAX_FILES:
        raise Refused(f"más de {MAX_FILES} ficheros: registra una subcarpeta o un fichero comprimido")
    if not files:
        raise Refused("la carpeta no tiene ficheros")
    size = sum((path / f).stat().st_size for f in files)
    return {"kind": "dir", "files": len(files), "bytes": size, "sha256": ds._dir_sha(path, files)}


def _key(p) -> str:
    return os.path.normcase(os.path.abspath(str(p))).replace("\\", "/").rstrip("/")


def within(a, b) -> bool:
    """a is b or lies under b (spelling, case-folded where the OS folds it)."""
    ka, kb = _key(a), _key(b)
    return ka == kb or ka.startswith(kb + "/")


def entry_path(entry: dict, what: str) -> Path:
    p = entry.get("path")
    if not isinstance(p, str) or not Path(p).is_absolute():
        raise Refused(f"el dataset «{entry.get('name')}» no tiene una ruta absoluta en el registro ({what})")
    return Path(p)


def check_registered(entry: dict) -> str:
    """Re-fingerprint a registered dataset; its sha256, or Refused when it drifted."""
    name = entry.get("name")
    path = entry_path(entry, "origen")
    want = str(entry.get("sha256") or "").strip().lower()
    if not want:
        raise Refused(f"el dataset «{name}» no tiene huella sha256 en el registro; vuelve a registrarlo")
    if not path.exists():
        raise Refused(f"no se encuentra el contenido del dataset «{name}»")
    fp = fingerprint(path)
    if entry.get("kind") in ("file", "dir") and fp["kind"] != entry["kind"]:
        raise Refused(f"el dataset «{name}» está registrado como {entry['kind']} y ahora es {fp['kind']}")
    if fp["sha256"] != want:
        raise Refused(f"la huella del dataset «{name}» no coincide con el registro: cambió después de "
                      "registrarse; restaura sus ficheros o vuelve a registrarlo")
    return want


# ---- git --------------------------------------------------------------------

def _git(args: list[str], cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Refused(f"git no se pudo ejecutar ({type(e).__name__})") from None


def commit_only(vault: Path, paths: list[Path], message: str) -> str | None:
    """`git add -- paths` and `git commit --only -- paths` in the vault, like the app's
    commitOnly: nothing else that is staged goes in. The new HEAD, or None when nothing changed."""
    vault = Path(vault)
    top_r = _git(["rev-parse", "--show-toplevel"], vault)
    if top_r.returncode != 0:
        raise Refused("el vault no es un repositorio git; usa --no-commit o inicializa el repositorio")
    top = Path(top_r.stdout.strip())
    rels = []
    for p in paths:
        if Path(p).exists():
            rels.append(os.path.relpath(Path(p).resolve(), top.resolve()).replace("\\", "/"))
    if not rels:
        return None
    for attempt in range(5):
        add = _git(["add", "--", *rels], top)
        if add.returncode == 0:
            staged = _git(["diff", "--cached", "--name-only", "--", *rels], top)
            if not staged.stdout.strip():
                return None
            c = _git(["commit", "--quiet", "--only", "-m", message, "--", *rels], top)
            if c.returncode == 0:
                return _git(["rev-parse", "HEAD"], top).stdout.strip()
            err = c.stderr
        else:
            err = add.stderr
        if attempt >= 4 or not re.search(r"index\.lock|Unable to create", err, re.I):
            raise Refused(f"git commit del registro falló: {err.strip()[:200]}")
        time.sleep(0.25 * 2 ** attempt)
    return None


def _git_user(vault: Path) -> str:
    try:
        r = _git(["config", "user.name"], Path(vault), timeout=20)
        return r.stdout.strip() or "lineage.py"
    except Refused:
        return "lineage.py"


# ---- register-derived -------------------------------------------------------

def now_iso() -> str:
    """Like JavaScript's Date.toISOString(): 2031-01-02T10:00:00.000Z."""
    t = datetime.now(timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def _script(raw: str) -> str:
    s = str(raw or "").strip().replace("\\", "/")
    p = PurePosixPath(s)
    if not s or p.is_absolute() or re.match(r"^[A-Za-z]:", s) or ".." in p.parts:
        raise Refused("--script debe ser una ruta relativa dentro del repositorio de código (sin ..)")
    return p.as_posix()


def _params(raw) -> dict | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raise Refused("--params debe ser un objeto JSON") from None
    if not isinstance(raw, dict):
        raise Refused("--params debe ser un objeto JSON")
    return raw


def _iso(raw: str | None) -> str:
    if not raw:
        return now_iso()
    try:
        datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        raise Refused("--produced-at debe ser una fecha ISO 8601") from None
    return str(raw)


def _same(a: dict, b: dict) -> bool:
    keys = ("path", "kind", "files", "bytes", "sha256", "sensitive", "derived_from", "script", "commit", "params")
    return all(a.get(k) == b.get(k) for k in keys)


def register_derived(vault: Path, slug: str, name: str, path: str | None, sources: list[str], script: str,
                     commit: str, *, repo: Path | None = None, params=None, sensitive: bool | None = None,
                     rederive: bool = False, produced_at: str | None = None, by: str | None = None,
                     do_commit: bool = True) -> dict:
    vault = Path(vault)
    if not vault.is_dir():
        raise Refused("no existe la carpeta del vault")
    reg = registry_path(vault, slug)
    if not isinstance(name, str) or name != name.strip() or not NAME.match(name):
        raise Refused("--name: de 1 a 80 letras, cifras, espacios, . _ -")
    entries = read_registry(reg)

    same = [e for e in entries if isinstance(e, dict) and str(e.get("name", "")).casefold() == name.casefold()]
    old = None
    if same:
        if not rederive:
            raise Refused(f"ya hay un dataset llamado «{same[0].get('name')}»; usa --rederive para volver a derivarlo")
        if len(same) > 1 or same[0].get("name") != name:
            raise Refused(f"el nombre «{name}» está repetido o escrito de otra forma en el registro; corrígelo antes")
        old = same[0]
        if not is_derived(old):
            raise Refused(f"«{name}» no es un dataset derivado (no tiene derived_from); no lo reescribo")
    elif rederive:
        raise Refused(f"no hay ningún dataset derivado llamado «{name}» que volver a derivar")

    if path is None or str(path).strip() == "":
        if old is None:
            raise Refused("--path es obligatorio la primera vez que se registra un dataset derivado")
        path = old.get("path")
    out = Path(str(path).strip())
    if not out.is_absolute():
        raise Refused("--path debe ser una ruta absoluta")
    out = Path(os.path.abspath(out))
    if not out.exists():
        raise Refused("no existe la salida que se quiere registrar")
    if within(out, vault) or within(vault, out):
        raise Refused("los datos van fuera del vault (el vault es un repositorio git de notas)")

    srcs: list[str] = []
    for s in sources:
        s = str(s).strip()
        if s and s not in srcs:
            srcs.append(s)
    if not srcs:
        raise Refused("--from debe nombrar al menos un dataset registrado")
    if name in srcs:
        raise Refused("un dataset no puede derivarse de sí mismo")
    src_entries = []
    for s in srcs:
        e = find(entries, s)
        if e is None:
            raise Refused(f"el dataset de origen «{s}» no está en el registro")
        if name in _ancestors(entries, s):
            raise Refused(f"«{s}» ya deriva de «{name}»: el linaje tendría un ciclo")
        src_entries.append(e)
    for e in entries:
        if e is old or not isinstance(e, dict):
            continue
        ep = e.get("path")
        if isinstance(ep, str) and Path(ep).is_absolute() and (within(out, ep) or within(ep, out)):
            raise Refused(f"la salida coincide o se solapa con el dataset «{e.get('name')}»; "
                          "escríbela en una ruta propia")

    script = _script(script)
    commit = str(commit or "").strip().lower()
    if not COMMIT.match(commit):
        raise Refused("--commit debe ser el sha de un commit del repositorio de código")
    if repo is not None and _git(["cat-file", "-e", f"{commit}:{script}"], Path(repo)).returncode != 0:
        raise Refused(f"{script} no está en el commit {commit[:10]} del repositorio de código")
    params = _params(params)
    produced_at = _iso(produced_at)

    derived = [{"name": e["name"], "sha256": check_registered(e)} for e in src_entries]
    if sensitive is None:
        sensitive = any(e.get("sensitive") not in (None, False) for e in src_entries) or \
            (old is not None and old.get("sensitive") not in (None, False))
    notes = []
    if not sensitive and any(e.get("sensitive") not in (None, False) for e in src_entries):
        notes.append("se deriva de datos sensibles y se registra como no sensible (--no-sensitive): "
                      "asegúrate de que no lleva material sensible")
    fp = fingerprint(out)
    entry = {"name": name, "path": str(out).replace("\\", "/"), **fp, "registered_at": now_iso(),
             "by": by or _git_user(vault), "sensitive": bool(sensitive), "derived_from": derived,
             "script": script, "commit": commit}
    if params is not None:
        entry["params"] = params
    entry["produced_at"] = produced_at

    result = {"dataset": name, "kind": fp["kind"], "files": fp["files"], "bytes": fp["bytes"],
              "sha256": fp["sha256"], "sensitive": bool(sensitive), "derived_from": derived,
              "script": script, "commit": commit, "replaced": old is not None, "changed": True,
              "vault_commit": None}
    if notes:
        result["avisos"] = notes
    if old is not None and _same(old, entry):
        result["changed"] = False
        return result

    # The registry may have changed during a long fingerprint (the app writes it too): read it
    # again and check, with no slow step, up to the write.
    now = read_registry(reg)
    for d in derived:
        e = find(now, d["name"])
        if e is None or str(e.get("sha256") or "").strip().lower() != d["sha256"]:
            raise Refused(f"el registro cambió mientras se calculaba la huella («{d['name']}»); vuelve a intentarlo")
    clash = [i for i, e in enumerate(now) if isinstance(e, dict)
             and str(e.get("name", "")).casefold() == name.casefold()]
    if old is None and clash:
        raise Refused(f"ya hay un dataset llamado «{name}»")
    if old is not None:
        if len(clash) != 1 or not is_derived(now[clash[0]]):
            raise Refused(f"el registro cambió mientras se calculaba la huella («{name}»); vuelve a intentarlo")
        now[clash[0]] = entry
    else:
        now.append(entry)
    write_registry(reg, now)
    if do_commit:
        verb = "volver a derivar" if old is not None else "registrar"
        tag = " (sensible)" if sensitive else ""
        result["vault_commit"] = commit_only(
            vault, [reg], f"Datos {slug}: {verb} «{name}»{tag}, derivado de {', '.join(srcs)}")
    return result


# ---- show / verify ----------------------------------------------------------

def _node(by: dict, name: str, trail: tuple = ()) -> dict:
    e = by.get(name)
    if e is None:
        return {"name": name, "error": "no está en el registro"}
    out = {"name": name, "sha256": e.get("sha256"), "kind": e.get("kind"),
           "sensitive": e.get("sensitive") not in (None, False), "registered_at": e.get("registered_at")}
    srcs = e.get("derived_from")
    if not srcs:
        out["raiz"] = True
        return out
    for k in ("script", "commit", "params", "produced_at"):
        if k in e:
            out[k] = e[k]
    out["derived_from"] = []
    for s in srcs if isinstance(srcs, list) else []:
        sn = s.get("name") if isinstance(s, dict) else None
        src = by.get(sn)
        link = {"name": sn, "sha256": s.get("sha256") if isinstance(s, dict) else None,
                "registrada": src.get("sha256") if src else None}
        link["al_dia"] = src is not None and str(link["registrada"] or "").lower() == str(link["sha256"] or "").lower()
        link["origen"] = ({"name": sn, "error": "ciclo"} if sn in trail or sn == name
                          else _node(by, sn, (*trail, name)))
        out["derived_from"].append(link)
    return out


def show(vault: Path, slug: str, name: str) -> dict:
    entries = read_registry(registry_path(Path(vault), slug))
    if find(entries, name) is None:
        raise Refused(f"el dataset «{name}» no está en el registro del proyecto")
    by: dict[str, dict] = {}
    for e in entries:
        if isinstance(e, dict) and isinstance(e.get("name"), str):
            by.setdefault(e["name"], e)
    roots, problems = lineage_roots(entries, name)
    return {"dataset": name, "cadena": _node(by, name),
            "raices": [{"name": r.get("name"), "sha256": r.get("sha256")} for r in roots],
            "problemas": problems}


def _text(node: dict, depth: int = 0, via: str = "") -> list[str]:
    pad = "  " * depth
    sha = str(node.get("sha256") or "")[:12] or "?"
    head = f"{pad}{via}{node.get('name')}  sha256 {sha}"
    if node.get("error"):
        return [f"{pad}{via}{node.get('name')}  ({node['error']})"]
    if node.get("sensitive"):
        head += "  [sensible]"
    if node.get("raiz"):
        return [head + "  (raíz)"]
    head += f"  ← {node.get('script')} @ {str(node.get('commit') or '')[:10]}"
    if node.get("params"):
        head += f"  params {json.dumps(node['params'], ensure_ascii=False, sort_keys=True)}"
    lines = [head]
    for link in node.get("derived_from", []):
        mark = "" if link.get("al_dia") else f"  [derivado de sha256 {str(link.get('sha256') or '')[:12]}, desactualizado]"
        sub = _text(link["origen"], depth + 1, "↳ ")
        lines += [sub[0] + mark, *sub[1:]]
    return lines


def verify(vault: Path, slug: str, name: str | None = None) -> dict:
    entries = read_registry(registry_path(Path(vault), slug))
    if name is not None:
        if find(entries, name) is None:
            raise Refused(f"el dataset «{name}» no está en el registro del proyecto")
        names = [name, *sorted(_ancestors(entries, name))]
    else:
        names = [e["name"] for e in entries if isinstance(e, dict) and isinstance(e.get("name"), str)]
    datasets, links = [], []
    for n in names:
        e = find(entries, n)
        if e is None:
            datasets.append({"name": n, "sha256": None, "actual": None, "estado": "no está en el registro"})
            continue
        want = str(e.get("sha256") or "").strip().lower()
        row = {"name": n, "sha256": want or None, "actual": None}
        p = e.get("path")
        if not isinstance(p, str) or not Path(p).is_absolute():
            row["estado"] = "sin ruta absoluta"
        elif not Path(p).exists():
            row["estado"] = "no existe"
        else:
            try:
                row["actual"] = fingerprint(Path(p))["sha256"]
                row["estado"] = "ok" if row["actual"] == want else "cambió"
            except (OSError, Refused) as ex:
                row["estado"] = f"ilegible ({ex if isinstance(ex, Refused) else type(ex).__name__})"
        datasets.append(row)
        srcs = e.get("derived_from")
        for s in srcs if isinstance(srcs, list) else []:
            sn = s.get("name") if isinstance(s, dict) else None
            src = find(entries, sn) if isinstance(sn, str) else None
            rec = str(s.get("sha256") or "").lower() if isinstance(s, dict) else ""
            links.append({"dataset": n, "origen": sn, "sha256": rec or None,
                          "registrada": src.get("sha256") if src else None,
                          "estado": ("origen ausente" if src is None else
                                     "ok" if str(src.get("sha256") or "").lower() == rec else "desactualizado")})
    ok = all(d["estado"] == "ok" for d in datasets) and all(x["estado"] == "ok" for x in links)
    return {"ok": ok, "datasets": datasets, "enlaces": links}


# ---- CLI --------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lineage.py", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    r = sub.add_parser("register-derived")
    for p in (r, sub.add_parser("show"), sub.add_parser("verify")):
        p.add_argument("--vault", required=True)
        p.add_argument("--project", required=True)
    r.add_argument("--name", required=True)
    r.add_argument("--path")
    r.add_argument("--from", dest="sources", required=True)
    r.add_argument("--script", required=True)
    r.add_argument("--commit", required=True)
    r.add_argument("--repo")
    r.add_argument("--params")
    g = r.add_mutually_exclusive_group()
    g.add_argument("--sensitive", dest="sensitive", action="store_const", const=True)
    g.add_argument("--no-sensitive", dest="sensitive", action="store_const", const=False)
    r.add_argument("--rederive", action="store_true")
    r.add_argument("--produced-at")
    r.add_argument("--by")
    r.add_argument("--no-commit", action="store_true")
    sub.choices["show"].add_argument("--name", required=True)
    sub.choices["show"].add_argument("--text", action="store_true")
    sub.choices["verify"].add_argument("--name")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    try:
        if a.mode == "register-derived":
            res = register_derived(Path(a.vault), a.project, a.name, a.path, a.sources.split(","), a.script,
                                   a.commit, repo=Path(a.repo) if a.repo else None, params=a.params,
                                   sensitive=a.sensitive, rederive=a.rederive, produced_at=a.produced_at,
                                   by=a.by, do_commit=not a.no_commit)
        elif a.mode == "show":
            res = show(Path(a.vault), a.project, a.name)
            if a.text:
                print("\n".join(_text(res["cadena"]) + [f"problema: {p}" for p in res["problemas"]]))
                return 0
        else:
            res = verify(Path(a.vault), a.project, a.name)
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return 0 if res["ok"] else 2
    except Refused as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 3
    except Exception as e:  # noqa: BLE001  never echo material: only the exception's type
        print(json.dumps({"error": f"error inesperado ({type(e).__name__})"}, ensure_ascii=False))
        return 1
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
