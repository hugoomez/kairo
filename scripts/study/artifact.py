#!/usr/bin/env python3
"""Open-science artifact package of a measurement study, and the leak scan that
decides whether it can be published.

    artifact.py build --vault V --project P --repo R --out DIR
                      [--include-aggregates] [--anonymous] [--allow-urls FILE]
                      [--authors FILE] [--fixtures-cmd "…"]
    artifact.py check --out DIR --vault V --project P [--repo R]
                      [--anonymous] [--allow-urls FILE] [--authors FILE]

`build` writes into DIR (absolute; new or empty; outside the vault and the code
repository, and not containing them):

  codigo/              the code repository at HEAD (`git archive HEAD`: committed files
                       only, `export-ignore` honoured, no `.git`, no working-tree changes)
  planes/<M-id>.md     each frozen plan of a citable design (Mediciones/), as committed
                       in the vault at its `frozen_commit` (`git show`); the plan's
                       current `## Enmiendas`, if any, as planes/<M-id>.enmiendas.md
  agregados/<Q-id>.json  with --include-aggregates: the ```estimate-json block of the
                       `## Resultado` of every question in its design's publishable state
                       (estimada) — aggregates only, as estimate.py wrote them
  fixtures/GENERAR.md  the command that generates the synthetic fixtures (default:
                       Kairo's fixtures_corpus.py; --fixtures-cmd replaces it). Never the
                       fixtures themselves, never real data
  figuras/             the figures listed in Projects/<slug>/Figuras/registro.json (a JSON
                       array whose entries name a file in `path` / `archivo` / `file`,
                       relative to Figuras/), when that registry exists; only files inside
                       the project folder (never Datos/ or Avisos/), with a rewritten
                       registro.json
  README.md            a skeleton: what is in the package, how to reproduce on synthetic
                       data, what is withheld and why (`<…>` marks what the researcher fills)
  MANIFEST.json        every file with its sha256, the options and the sources

It never copies Projects/<slug>/Avisos/, Datos/ or Datos/anotacion/, the questions'
notes, or any registered dataset. With --anonymous, README and MANIFEST leave out the
code HEAD and the vault commits (a public commit id names its author).

Then the leak scan, the same one `check` runs alone over an existing DIR:

  git           a `.git` file or folder anywhere
  enlace        a symlink (nothing outside DIR is followed or shipped)
  avisos / anotacion
                a path with an `Avisos` segment or `Datos/anotacion`
  dataset       a file whose sha256 equals a file of any dataset registered in
                Datos/registro.json (sensitive or not; the registry's own `sha256` for
                `file` datasets, and every same-size file under the dataset's path;
                files under 32 bytes are not compared), or text that holds a dataset's
                registered path
  secreto       anything redact.py would redact (keys, tokens, credential URLs)
  github        a `github.com/<owner>/<repo>` link (also `git@github.com:`,
                raw.githubusercontent.com, api.github.com/repos) not in --allow-urls
                (one `owner/repo` or `owner/*` per line, `#` comments: the tools studied)
  autor         with --anonymous: an author's name, e-mail or handle, from the hub's
                `autores:` list, KAIRO_AUTHORS (`;`-separated), --authors FILE (one per
                line) and the code repository's git `user.name` / `user.email`.
                --anonymous with no name at all is refused (nothing to check against)
  bundle:<kind> every `block` finding of check_bundle.py (key files, credential files,
                keystores, vault notes, absolute vault paths…)

A leak is reported by kind, package path and line, never with the matched text (a
studied repository or a secret must not reach the output). Exit codes: 0 clean ·
4 leaks (the package stays on disk to be fixed; do not publish it) · 3 refused (DIR
not allowed, code repository not git, --anonymous without names) · 1 error (no vault,
unknown project). Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ledger"))
sys.path.insert(0, str(HERE.parent / "security"))
import check_bundle as cb  # noqa: E402
import designs as _designs  # noqa: E402
import project_scan as ps  # noqa: E402
import redact  # noqa: E402
from notes import read_note, section  # noqa: E402

__version__ = "1.0.0"
TOOL = f"kairo/artifact@{__version__}"
MIN_HASH_BYTES = 32
MAX_DATASET_FILES = 200_000
MAX_TEXT = redact.MAX_FILE_BYTES
DEFAULT_FIXTURES = "python scripts/security/fixtures_corpus.py --out fixtures --seed 1   # Kairo plugin"
_EST_BLOCK = re.compile(r"```estimate-json[ \t]*\n(.*?)\n[ \t]*```", re.S)
_GH = [re.compile(r"(?:https?://)?(?:www\.)?github\.com[/:]([A-Za-z0-9-]+)/([A-Za-z0-9._-]+)", re.I),
       re.compile(r"git@github\.com:([A-Za-z0-9-]+)/([A-Za-z0-9._-]+)", re.I),
       re.compile(r"raw\.githubusercontent\.com/([A-Za-z0-9-]+)/([A-Za-z0-9._-]+)", re.I),
       re.compile(r"api\.github\.com/repos/([A-Za-z0-9-]+)/([A-Za-z0-9._-]+)", re.I)]
_GH_RESERVED = {"about", "apps", "collections", "contact", "customer-stories", "enterprise", "explore",
                "features", "login", "marketplace", "orgs", "pricing", "readme", "security", "settings",
                "site", "sponsors", "topics", "advisories", "join", "new", "notifications", "search"}


class Refused(Exception):
    """exit 3"""


class InputError(Exception):
    """exit 1"""


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

def _key(p: Path | str) -> str:
    return str(p).replace("\\", "/").rstrip("/").casefold()


def inside(p: Path, root: Path) -> bool:
    kp, kr = _key(p), _key(root)
    return kp == kr or kp.startswith(kr + "/")


def _resolve(p: Path | str) -> Path:
    try:
        return Path(p).expanduser().resolve()
    except (OSError, RuntimeError):
        return Path(os.path.abspath(Path(p).expanduser()))


def _git(args: list[str], cwd: Path, binary: bool = False) -> subprocess.CompletedProcess:
    if binary:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True)
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


# --------------------------------------------------------------------------- #
# Context: project, datasets, allow-list, authors
# --------------------------------------------------------------------------- #

def project_dir(vault: Path, project: str) -> Path:
    if not vault.is_dir():
        raise InputError(f"no existe el vault {vault}")
    try:
        return ps.resolve_project(vault, project)[0]
    except ps.InputError as exc:
        raise InputError(str(exc)) from None


def datasets(pdir: Path) -> list[dict]:
    reg = pdir / "Datos" / "registro.json"
    if not reg.is_file():
        return []
    try:
        data = json.loads(reg.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        raise Refused("Datos/registro.json ilegible: el escaneo de fugas necesita el registro de datos") from None
    if not isinstance(data, list):
        raise Refused("Datos/registro.json no es una lista")
    out = []
    for i, d in enumerate(data):
        if isinstance(d, dict) and isinstance(d.get("path"), str) and Path(d["path"].strip()).is_absolute():
            out.append({"name": str(d.get("name") or f"#{i}"), "path": d["path"].strip(),
                        "kind": d.get("kind") if d.get("kind") in ("file", "dir") else "dir",
                        "sha256": str(d.get("sha256") or "").lower()})
    return out


def load_allow_urls(path: str | None) -> set[str]:
    if not path:
        return set()
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        raise InputError(f"--allow-urls ilegible: {path}") from None
    out = set()
    for ln in lines:
        ln = ln.split("#", 1)[0].strip().strip("/")
        ln = re.sub(r"^(?:https?://)?(?:www\.)?github\.com/", "", ln, flags=re.I)
        if ln:
            out.add(re.sub(r"\.git$", "", ln).casefold())
    return out


def author_terms(pdir: Path, environ: Mapping[str, str], authors_file: str | None, repo: Path | None) -> list[str]:
    terms: list[str] = []
    hub = ps.hub_fm(pdir)
    raw = hub.get("autores")
    if isinstance(raw, list):
        terms += [str(x) for x in raw]
    elif isinstance(raw, str) and not ps.placeholder(raw):
        terms += raw.split(";")
    terms += (environ.get("KAIRO_AUTHORS") or "").split(";")
    if authors_file:
        try:
            terms += Path(authors_file).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            raise InputError(f"--authors ilegible: {authors_file}") from None
    if repo is not None:
        for k in ("user.name", "user.email"):
            r = _git(["config", "--get", k], repo)
            if r.returncode == 0:
                terms.append(r.stdout.strip())
    clean = []
    for t in terms:
        t = t.split("#", 1)[0].strip().strip("\"'")
        if len(t) >= 3 and not t.startswith("<"):
            clean.append(t)
    return list(dict.fromkeys(clean))


# --------------------------------------------------------------------------- #
# Leak scan
# --------------------------------------------------------------------------- #

def _package_files(out: Path) -> tuple[list[Path], list[dict]]:
    files, leaks = [], []
    for dirpath, dirnames, filenames in os.walk(out, followlinks=False):
        d = Path(dirpath)
        for name in list(dirnames):
            p = d / name
            rel = p.relative_to(out).as_posix()
            if name == ".git":
                leaks.append({"kind": "git", "path": rel})
                dirnames.remove(name)
            elif p.is_symlink() or getattr(os.path, "isjunction", lambda _: False)(p):
                leaks.append({"kind": "enlace", "path": rel})
                dirnames.remove(name)
        for name in filenames:
            p = d / name
            rel = p.relative_to(out).as_posix()
            if p.is_symlink():
                leaks.append({"kind": "enlace", "path": rel})
                continue
            if name == ".git":
                leaks.append({"kind": "git", "path": rel})
            files.append(p)
    return files, leaks


def _segments_leak(rel: str) -> str | None:
    parts = [s.casefold() for s in rel.split("/")]
    if "avisos" in parts[:-1]:
        return "avisos"
    for i in range(len(parts) - 1):
        if parts[i] == "datos" and parts[i + 1] == "anotacion":
            return "anotacion"
    return None


def _dataset_hashes(ds: list[dict], sizes: set[int]) -> dict[str, str]:
    """sha256 -> dataset name, for every dataset file whose size is one of `sizes`."""
    out: dict[str, str] = {}
    for d in ds:
        if d["kind"] == "file" and re.fullmatch(r"[0-9a-f]{64}", d["sha256"]):
            out.setdefault(d["sha256"], d["name"])
        root = Path(d["path"])
        try:
            walker = [root] if root.is_file() else (p for p in root.rglob("*") if p.is_file()) if root.is_dir() else []
            for n, p in enumerate(walker):
                if n >= MAX_DATASET_FILES:
                    break
                try:
                    size = p.stat().st_size
                    if size >= MIN_HASH_BYTES and size in sizes:
                        out.setdefault(sha256_file(p), d["name"])
                except OSError:
                    continue
        except OSError:
            continue
    return out


def _path_needles(ds: list[dict]) -> list[tuple[str, str]]:
    out = []
    for d in ds:
        p = d["path"].rstrip("/\\")
        for v in {p, p.replace("\\", "/"), p.replace("/", "\\")}:
            if len(v) >= 6:
                out.append((v.casefold(), d["name"]))
    return out


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def scan(out: Path, ds: list[dict], allow_urls: set[str], authors: list[str] | None) -> list[dict]:
    out = Path(out)
    files, leaks = _package_files(out)
    rels = {p: p.relative_to(out).as_posix() for p in files}
    for rel in rels.values():
        kind = _segments_leak(rel)
        if kind:
            leaks.append({"kind": kind, "path": rel})
    sizes = set()
    for p in files:
        try:
            sizes.add(p.stat().st_size)
        except OSError:
            pass
    known = _dataset_hashes(ds, sizes)
    needles = _path_needles(ds)
    author_rx = [re.compile(r"(?<![\w.@-])" + re.escape(t) + r"(?![\w@-])", re.I) for t in authors or []]
    for p in files:
        rel = rels[p]
        try:
            size = p.stat().st_size
            if known and size >= MIN_HASH_BYTES:
                name = known.get(sha256_file(p))
                if name:
                    leaks.append({"kind": "dataset", "path": rel, "detail": f"copia de un fichero del dataset «{name}»"})
            raw = p.read_bytes() if size <= MAX_TEXT else b""
        except OSError:
            leaks.append({"kind": "ilegible", "path": rel})
            continue
        text = redact.decode_text(raw, MAX_TEXT) if raw else None
        if text is None:
            continue
        low = text.casefold()
        for needle, name in needles:
            i = low.find(needle)
            if i >= 0:
                leaks.append({"kind": "dataset", "path": rel, "line": _line(text, i),
                              "detail": f"ruta del dataset «{name}»"})
        for f in redact.redact_text(text)[1]:
            leaks.append({"kind": "secreto", "path": rel, "detail": f["type"]})
        for rx in _GH:
            for m in rx.finditer(text):
                owner, repo = m.group(1), re.sub(r"\.git$", "", m.group(2).rstrip("."))
                if owner.casefold() in _GH_RESERVED or not repo:
                    continue
                if f"{owner}/{repo}".casefold() in allow_urls or f"{owner}/*".casefold() in allow_urls:
                    continue
                leaks.append({"kind": "github", "path": rel, "line": _line(text, m.start())})
        for rx in author_rx:
            for m in rx.finditer(text):
                leaks.append({"kind": "autor", "path": rel, "line": _line(text, m.start())})
    sc = cb.Scanner(out, cb.DEFAULT_MAX_BYTES, False)
    try:
        sc.walk()
    except cb.CheckError as exc:
        leaks.append({"kind": "bundle:error", "path": "", "detail": str(exc)})
    leaks += [{"kind": f"bundle:{f['kind']}", "path": f["path"]} for f in sc.result() if f["severity"] == cb.BLOCK]
    uniq, seen = [], set()
    for lk in leaks:
        k = (lk["kind"], lk["path"], lk.get("line"), lk.get("detail"))
        if k not in seen:
            seen.add(k)
            uniq.append(lk)
    return sorted(uniq, key=lambda x: (x["path"], x.get("line") or 0, x["kind"]))


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #

def check_out(out: str, vault: Path, repo: Path) -> Path:
    if not out or not Path(out).is_absolute():
        raise Refused("--out debe ser una ruta absoluta")
    p = _resolve(out)
    for name, root in (("vault", _resolve(vault)), ("repositorio de código", _resolve(repo))):
        if inside(p, root) or inside(root, p):
            raise Refused(f"--out no puede estar dentro del {name} ni contenerlo")
    if p.exists() and (not p.is_dir() or any(p.iterdir())):
        raise Refused("--out existe y no es una carpeta vacía")
    return p


def archive_code(repo: Path, dest: Path) -> str:
    sha = _git(["rev-parse", "HEAD"], repo)
    if sha.returncode != 0:
        raise Refused("el repositorio de código no es git o no tiene commits")
    with tempfile.TemporaryFile() as tmp:
        r = subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=repo, stdout=tmp,
                           stderr=subprocess.PIPE)
        if r.returncode != 0:
            raise Refused("git archive HEAD falló en el repositorio de código")
        tmp.seek(0)
        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=tmp, mode="r:") as tar:
            members = [m for m in tar.getmembers() if m.isfile() or m.isdir()]
            for m in members:
                target = (dest / m.name).resolve()
                if not inside(target, dest.resolve()):
                    raise Refused("git archive devolvió una ruta fuera del destino")
            if hasattr(tarfile, "data_filter"):
                tar.extractall(dest, members=members, filter="data")
            else:  # pragma: no cover - Python < 3.11.4
                tar.extractall(dest, members=members)
    return sha.stdout.strip()


def _vault_top(vault: Path) -> Path | None:
    r = _git(["rev-parse", "--show-toplevel"], vault)
    return Path(r.stdout.strip()).resolve() if r.returncode == 0 else None


def plans(vault: Path, pdir: Path, dest: Path, warnings: list[str]) -> list[dict]:
    top = _vault_top(vault)
    out = []
    for name, d in _designs.load().items():
        if d.get("citable", True) is False:
            continue
        folder = pdir / d["plan_folder"]
        for path in sorted(folder.glob(f"{d['plan_prefix']}-*.md")) if folder.is_dir() else []:
            parsed = read_note(path)
            if not parsed:
                continue
            fm, body = parsed
            pid = str(fm.get("id") or path.stem)
            sha = str(fm.get("frozen_commit") or "").strip()
            if not sha or sha.startswith("<"):
                continue
            if top is None:
                warnings.append(f"{pid}: el vault no tiene git; el plan congelado no se puede leer")
                continue
            rel = path.resolve().relative_to(top).as_posix()
            shown = _git(["show", f"{sha}:{rel}"], top)
            if shown.returncode != 0:
                warnings.append(f"{pid}: no está en su frozen_commit; no se incluye")
                continue
            _write(dest / f"{pid}.md", shown.stdout)
            entry = {"id": pid, "design": name, "frozen_commit": sha, "frozen_at": str(fm.get("frozen_at") or "")}
            amend = section(body, "Enmiendas")
            if amend and not amend.lstrip().startswith("<"):
                _write(dest / f"{pid}.enmiendas.md", f"# {pid} — Enmiendas (posteriores al congelado)\n\n{amend}\n")
                entry["enmiendas"] = True
            out.append(entry)
    return out


def aggregates(pdir: Path, dest: Path, warnings: list[str]) -> list[str]:
    out = []
    for d in _designs.load().values():
        if d.get("citable", True) is False:
            continue
        folder = pdir / d["folder"]
        for path in sorted(folder.glob(f"{d['prefix']}-*.md")) if folder.is_dir() else []:
            parsed = read_note(path)
            if not parsed or str(parsed[0].get("status") or "") != d["publishable"]:
                continue
            qid = str(parsed[0].get("id") or path.stem)
            m = _EST_BLOCK.search(section(parsed[1], "Resultado"))
            if not m:
                warnings.append(f"{qid}: {d['publishable']} sin bloque estimate-json")
                continue
            try:
                data = json.loads(m.group(1))
            except ValueError:
                warnings.append(f"{qid}: el bloque estimate-json no es JSON válido")
                continue
            _write(dest / f"{qid}.json", json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            out.append(qid)
    return out


def figures(pdir: Path, dest: Path, warnings: list[str]) -> int:
    fig = pdir / "Figuras"
    reg = fig / "registro.json"
    if not reg.is_file():
        return 0
    try:
        data = json.loads(reg.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        warnings.append("Figuras/registro.json ilegible; no se incluyen figuras")
        return 0
    if not isinstance(data, list):
        warnings.append("Figuras/registro.json no es una lista; no se incluyen figuras")
        return 0
    kept = []
    for e in data:
        if not isinstance(e, dict):
            continue
        raw = next((e[k] for k in ("path", "archivo", "file") if isinstance(e.get(k), str) and e[k].strip()), None)
        if raw is None or Path(raw).is_absolute():
            warnings.append("una figura sin ruta relativa a Figuras/; no se incluye")
            continue
        # figures.py writes `Figuras/<file>` (project-relative); a bare name is relative to Figuras/
        src = _resolve(pdir / raw if Path(raw).parts[:1] == ("Figuras",) else fig / raw)
        rel_parts = [s.casefold() for s in Path(raw).parts]
        if (not inside(src, _resolve(pdir)) or not src.is_file() or "avisos" in rel_parts or "datos" in rel_parts
                or inside(src, _resolve(pdir / "Datos")) or inside(src, _resolve(pdir / "Avisos"))):
            warnings.append(f"figura {Path(raw).name}: no existe o está fuera de la carpeta del proyecto; no se incluye")
            continue
        target = dest / src.name
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        kept.append({**{k: v for k, v in e.items() if k not in ("path", "archivo", "file")}, "path": src.name})
    if kept:
        _write(dest / "registro.json", json.dumps(kept, ensure_ascii=False, indent=2) + "\n")
    return len(kept)


def readme(slug_title: str, head: str | None, plan_rows: list[dict], aggs: list[str], n_figs: int,
           fixtures_cmd: str, anonymous: bool) -> str:
    code = f"`codigo/` — the code repository at commit `{head}`." if head else \
        "`codigo/` — the code repository at the commit the study used (commit id withheld for anonymous review)."
    plan_lines = [f"- `planes/{p['id']}.md` — frozen plan"
                  + ("" if anonymous else f" (vault commit `{p['frozen_commit'][:12]}`, {p['frozen_at'] or 'date in the file'})")
                  + ("; amendments in `planes/" + p["id"] + ".enmiendas.md`" if p.get("enmiendas") else "")
                  for p in plan_rows] or ["- (no frozen plan)"]
    agg_lines = [f"- `agregados/{q}.json` — aggregate estimates (estimate.py output)" for q in aggs] or \
        ["- (aggregates not included)"]
    return "\n".join([
        f"# Artifact — {slug_title}", "",
        "<One paragraph: what the study measures and which paper this artifact accompanies.>", "",
        "## Contents", "", f"- {code}", *plan_lines, *agg_lines,
        f"- `figuras/` — {n_figs} registered figure(s)." if n_figs else "- (no registered figures)",
        "- `fixtures/GENERAR.md` — how to generate the synthetic fixtures.",
        "- `MANIFEST.json` — every file with its sha256.", "",
        "## Reproduce on synthetic data", "",
        "1. Generate the synthetic fixtures (invented material only; nothing in them is or was real):", "",
        "   ```", f"   {fixtures_cmd}", "   ```", "",
        "2. <Install the code's dependencies: the command from codigo/'s README or pyproject.>",
        "3. <Run the pipeline / tests on the fixtures: the exact command and the expected output.>",
        "4. <What matches the paper (the method, the classifiers' behaviour) and what cannot (the "
        "real figures, which need the withheld data).>", "",
        "## What is withheld, and why", "",
        "- **The collected data.** It holds material third parties published by accident (credentials, "
        "keys, personal data). Releasing it would expose them again; the study's ethics plan forbids it.",
        "- **Notices to affected parties.** Who was notified, how and when stays private: naming them "
        "would point at what they exposed.",
        "- **Annotation samples and labels.** They are drawn from the collected data.",
        "- <Anything else, and the condition under which other researchers may request it, if any.>", "",
        "## Ethics", "", "<Link or summary of the ethics plan; the committee decision, if any.>", "",
        "## License", "", "<Code license; documentation license.>", ""])


def build(vault: Path, project: str, repo: Path, out: str, include_aggregates: bool = False,
          anonymous: bool = False, allow_urls: str | None = None, authors_file: str | None = None,
          fixtures_cmd: str | None = None, environ: Mapping[str, str] | None = None) -> tuple[int, dict]:
    environ = os.environ if environ is None else environ
    vault, repo = Path(vault), Path(repo)
    pdir = project_dir(vault, project)
    if not repo.is_dir():
        raise InputError(f"no existe el repositorio de código {repo}")
    ds = datasets(pdir)
    allow = load_allow_urls(allow_urls)
    authors = author_terms(pdir, environ, authors_file, repo) if anonymous else None
    if anonymous and not authors:
        raise Refused("--anonymous sin nombres que buscar: añade `autores:` al hub, KAIRO_AUTHORS o --authors FILE")
    dest = check_out(out, vault, repo)
    dest.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    head = archive_code(repo, dest / "codigo")
    plan_rows = plans(vault, pdir, dest / "planes", warnings)
    aggs = aggregates(pdir, dest / "agregados", warnings) if include_aggregates else []
    n_figs = figures(pdir, dest / "figuras", warnings)
    fx = " ".join((fixtures_cmd or DEFAULT_FIXTURES).split())
    _write(dest / "fixtures" / "GENERAR.md",
           "# Synthetic fixtures\n\nGenerate them; they are not shipped and hold no real data:\n\n"
           f"```\n{fx}\n```\n")
    _write(dest / "README.md", readme(pdir.name, None if anonymous else head, plan_rows, aggs, n_figs, fx, anonymous))
    files = sorted(p for p in dest.rglob("*") if p.is_file() and not p.is_symlink())
    manifest = {"tool": TOOL, "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "anonymous": anonymous, "code_head": None if anonymous else head,
                "plans": [{k: (None if anonymous and k == "frozen_commit" else v) for k, v in p.items()}
                          for p in plan_rows],
                "aggregates": aggs, "figures": n_figs, "fixtures_cmd": fx,
                "files": [{"path": p.relative_to(dest).as_posix(), "sha256": sha256_file(p)} for p in files]}
    _write(dest / "MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    leaks = scan(dest, ds, allow, authors)
    res = {"tool": TOOL, "out": str(dest), "publicable": not leaks, "fugas": leaks, "n_fugas": len(leaks),
           "planes": [p["id"] for p in plan_rows], "agregados": aggs, "figuras": n_figs,
           "ficheros": len(manifest["files"]) + 1, "avisos": warnings}
    return (4 if leaks else 0), res


def check(out: str, vault: Path, project: str, repo: Path | None = None, anonymous: bool = False,
          allow_urls: str | None = None, authors_file: str | None = None,
          environ: Mapping[str, str] | None = None) -> tuple[int, dict]:
    environ = os.environ if environ is None else environ
    pdir = project_dir(Path(vault), project)
    dest = Path(out)
    if not dest.is_absolute() or not dest.is_dir():
        raise Refused("--out debe ser la carpeta absoluta de un paquete existente")
    authors = author_terms(pdir, environ, authors_file, Path(repo) if repo else None) if anonymous else None
    if anonymous and not authors:
        raise Refused("--anonymous sin nombres que buscar: añade `autores:` al hub, KAIRO_AUTHORS o --authors FILE")
    leaks = scan(dest, datasets(pdir), load_allow_urls(allow_urls), authors)
    return (4 if leaks else 0), {"tool": TOOL, "out": str(dest), "publicable": not leaks, "fugas": leaks,
                                 "n_fugas": len(leaks)}


def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="artifact.py", description=__doc__.splitlines()[0])
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    c = sub.add_parser("check")
    for s in (b, c):
        s.add_argument("--vault", required=True)
        s.add_argument("--project", required=True)
        s.add_argument("--out", required=True)
        s.add_argument("--anonymous", action="store_true")
        s.add_argument("--allow-urls")
        s.add_argument("--authors")
    b.add_argument("--repo", required=True)
    b.add_argument("--include-aggregates", action="store_true")
    b.add_argument("--fixtures-cmd")
    c.add_argument("--repo")
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code else 0
    try:
        if a.cmd == "build":
            code, res = build(Path(a.vault), a.project, Path(a.repo), a.out, a.include_aggregates, a.anonymous,
                              a.allow_urls, a.authors, a.fixtures_cmd, environ)
        else:
            code, res = check(a.out, Path(a.vault), a.project, Path(a.repo) if a.repo else None, a.anonymous,
                              a.allow_urls, a.authors, environ)
    except Refused as exc:
        print(json.dumps({"tool": TOOL, "refused": True, "error": str(exc)}, ensure_ascii=False))
        return 3
    except InputError as exc:
        print(f"artifact: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
