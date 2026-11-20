#!/usr/bin/env python3
"""Environment check for a measurement study whose datasets may hold third-party
sensitive material (credentials others published by accident, personal data).

    python doctor.py --vault <vault> --project <slug | PROJ-XXX> [--json]

Read-only, and it never reads a dataset: it only looks at paths (stat, parents,
free space) and at the registry `Projects/<slug>/Datos/registro.json`. It never
opens the GitHub token file either (stat only) and never prints an environment
variable's value. Each check is `{id, severity, target, message, fix}`, with
severity `critico | importante | menor | ok`; messages and fixes are Spanish.

  registro        the registry exists and can be read (unreadable: critico; the
                  data guard then refuses every tool call in the vault).
  datos_ubicacion every registered dataset exists (importante), lies outside
                  the vault (sensitive: critico, else importante) and outside
                  any git working tree, the project's `code_repo` included
                  (walks the parents for `.git`).
  gitignore       a dataset inside a git working tree: is it ignored there
                  (`git check-ignore`) and has nothing of it been committed
                  (`git ls-files`, counted, never printed)? Sensitive and not
                  ignored or tracked: critico. Ignored but still inside the
                  tree: importante for sensitive data, menor otherwise.
  nube            no sensitive dataset under a cloud-synced folder: OneDrive
                  (env OneDrive / OneDriveConsumer / OneDriveCommercial and the
                  folder name), Dropbox (folder name, ~/.dropbox/info.json),
                  Google Drive (My Drive, Mi unidad, GoogleDrive-…), iCloud
                  (iCloud Drive, Mobile Documents) and macOS CloudStorage.  critico
  cifrado         the volume of each sensitive dataset is encrypted. Windows:
                  `manage-bde -status <drive>`; macOS: `fdesetup status`
                  (`diskutil info` for /Volumes/…); Linux: `lsblk -J` and a
                  `crypt` device above the mount point. A definite «not
                  encrypted» is critico on Windows and macOS, importante on
                  Linux (fscrypt or ZFS native encryption are not seen); a
                  check that cannot run is importante «no se pudo comprobar»,
                  never critico.
  token_github    the token file (KAIRO_GITHUB_TOKEN_FILE, default
                  ~/.kairo/github-token): missing (menor, importante when the
                  variable names it), inside the vault or a git tree (critico),
                  in a synced folder (importante), readable by others on POSIX
                  (importante). GITHUB_TOKEN / GH_TOKEN (and the enterprise
                  variants) set in this environment: critico, since every
                  agent session started from it inherits them.
  kairo_vault     KAIRO_VAULT is set and names this vault, so the data guard
                  acts in sessions opened in the code repository (importante
                  with sensitive datasets, menor without).
  disco           free space of each dataset volume: under the hub's
                  `presupuesto_disco_gb` importante, under 10 % menor.
  etica           Etica.md in the project (or the hub's `etica:` path) — importante.
  avisos          a plan in Mediciones/ is `status: congelado` but
                  Avisos/registro.json does not exist — menor (existence only).

Exit codes: 0 no critico · 4 at least one critico · 3 refused (KAIRO_PROFILE
`literatura`: studies belong to the research programme) · 1 error (no vault,
unknown project, bad arguments). Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat as statmod
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ledger"))
sys.path.insert(0, str(HERE.parent / "security"))
import project_scan as ps  # noqa: E402
from notes import read_note  # noqa: E402

__version__ = "1.0.0"
TOOL = f"kairo/doctor@{__version__}"
SEVERITIES = ("critico", "importante", "menor", "ok")
ORDER = {s: i for i, s in enumerate(SEVERITIES)}
TOKEN_ENV = ("GITHUB_TOKEN", "GH_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
ONEDRIVE_ENV = ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")
LOW_FREE = 0.10
GB = 1024 ** 3


class InputError(Exception):
    """No vault, unknown project (exit 1)."""


# --------------------------------------------------------------------------- #
# The machine, injectable
# --------------------------------------------------------------------------- #

def run_command(argv: list[str], timeout: float = 30.0) -> tuple[int, str]:
    """(exit code, stdout + stderr). 127 when the program is missing, 124 on timeout."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout)
    except FileNotFoundError:
        return 127, f"{argv[0]}: no encontrado"
    except subprocess.TimeoutExpired:
        return 124, f"{argv[0]}: tiempo agotado"
    except OSError as exc:
        return 126, f"{argv[0]}: {exc}"
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def mount_point(p: Path) -> Path:
    """The mount point holding p (POSIX)."""
    p = Path(os.path.abspath(p))
    while not os.path.ismount(p) and p.parent != p:
        p = p.parent
    return p


@dataclass
class Ctx:
    environ: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    home: Path = field(default_factory=Path.home)
    platform: str = sys.platform
    run: Callable[[list[str]], tuple[int, str]] = run_command
    disk_usage: Callable[[Path], tuple[int, int, int]] = shutil.disk_usage
    stat: Callable[[Path], os.stat_result] = os.stat
    mount_of: Callable[[Path], Path] = mount_point

    @property
    def windows(self) -> bool:
        return self.platform.startswith("win")


def check(cid: str, severity: str, message: str, fix: str = "", target: str = "") -> dict:
    return {"id": cid, "severity": severity, "target": target, "message": message, "fix": fix}


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

def _abs(p: Path | str) -> Path:
    q = Path(os.path.expandvars(os.path.expanduser(str(p))))
    try:
        return q.resolve()
    except (OSError, RuntimeError, ValueError):
        return Path(os.path.abspath(q))


def _key(p: Path) -> str:
    return str(p).replace("\\", "/").rstrip("/").casefold()


def inside(p: Path, root: Path) -> bool:
    """p is root or lies under it (case-insensitive, as data_guard compares)."""
    kp, kr = _key(p), _key(root)
    return kp == kr or kp.startswith(kr + "/")


def _exists(p: Path) -> bool:
    try:
        return p.exists()
    except OSError:
        return False


def git_root(p: Path) -> Path | None:
    """The nearest folder at or above p holding `.git` (a folder or a worktree's file)."""
    for a in (p, *p.parents):
        if _exists(a / ".git"):
            return a
    return None


def existing_anchor(p: Path) -> Path:
    """p, or its nearest existing ancestor."""
    for a in (p, *p.parents):
        if _exists(a):
            return a
    return p


def cloud_folder(p: Path, ctx: Ctx) -> str | None:
    """Which sync service, if p lies under a cloud-synced folder."""
    for var in ONEDRIVE_ENV:
        v = (ctx.environ.get(var) or "").strip()
        if v and inside(p, _abs(v)):
            return f"OneDrive (%{var}%)"
    for root in _dropbox_roots(ctx):
        if inside(p, root):
            return "Dropbox"
    for part in p.parts:
        n = part.casefold().strip("/\\")
        if n.startswith("onedrive"):
            return "OneDrive"
        if n == "dropbox" or n.startswith("dropbox ("):
            return "Dropbox"
        if n in ("google drive", "googledrive", "my drive", "mi unidad", "shared drives",
                 "unidades compartidas") or n.startswith("googledrive-"):
            return "Google Drive"
        if n in ("icloud drive", "iclouddrive", "mobile documents", "com~apple~clouddocs"):
            return "iCloud"
        if n == "cloudstorage":
            return "una carpeta de CloudStorage (macOS)"
    return None


def _dropbox_roots(ctx: Ctx) -> list[Path]:
    """Dropbox's own record of its folders (~/.dropbox/info.json), when present."""
    info = ctx.home / ".dropbox" / "info.json"
    try:
        data = json.loads(info.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, dict) and isinstance(v.get("path"), str) and v["path"].strip():
                out.append(_abs(v["path"]))
    return out


# --------------------------------------------------------------------------- #
# Project
# --------------------------------------------------------------------------- #

@dataclass
class Dataset:
    name: str
    raw: str
    path: Path | None
    sensitive: bool
    kind: str


@dataclass
class Project:
    vault: Path
    pdir: Path
    pid: str
    hub: dict
    code_repo: Path | None
    datasets: list[Dataset]
    registry_problem: str | None
    registry_exists: bool

    @property
    def sensitive(self) -> list[Dataset]:
        return [d for d in self.datasets if d.sensitive and d.path is not None]


def load_registry(path: Path) -> tuple[list[Dataset], str | None, list[dict]]:
    """(datasets, problem that makes the whole registry unreadable, per-entry problems)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], None, []
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return [], type(exc).__name__, []
    if not isinstance(data, list):
        return [], "no es una lista", []
    out, problems = [], []
    for i, d in enumerate(data):
        if not isinstance(d, dict):
            return [], f"la entrada {i} no es un objeto", []
        name = str(d.get("name") or f"#{i}")
        sensitive = d.get("sensitive") not in (None, False)
        kind = d.get("kind") if d.get("kind") in ("file", "dir") else "dir"
        raw = d.get("path")
        if not isinstance(raw, str) or not raw.strip() or not Path(raw.strip()).is_absolute():
            if sensitive:
                return [], f"la entrada sensible {i} no tiene una ruta absoluta", []
            problems.append(check("datos_ubicacion", "importante",
                                  f"El dataset «{name}» no tiene una ruta absoluta en el registro.",
                                  "Corrige su `path` en Datos/registro.json desde el panel Datos.", name))
            out.append(Dataset(name, str(raw or ""), None, False, kind))
            continue
        out.append(Dataset(name, raw.strip(), _abs(raw.strip()), sensitive, kind))
    return out, None, problems


def load_project(vault: Path, project: str) -> tuple[Project, list[dict]]:
    if not vault.is_dir():
        raise InputError(f"no existe el vault {vault}")
    try:
        pdir, pid = ps.resolve_project(vault, project)
    except ps.InputError as exc:
        raise InputError(str(exc)) from None
    hub = ps.hub_fm(pdir)
    repo = ps.scalar(hub, "code_repo")
    reg = pdir / "Datos" / "registro.json"
    datasets, problem, entry_problems = load_registry(reg)
    return Project(_abs(vault), pdir, pid, hub, _abs(repo) if repo else None, datasets, problem,
                   reg.is_file()), entry_problems


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #

def _git_ignore_state(root: Path, p: Path, ctx: Ctx) -> tuple[bool | None, int]:
    """(ignored? None when unknown, number of tracked files under p). File names are never kept."""
    rc, _ = ctx.run(["git", "-C", str(root), "check-ignore", "-q", "--", str(p)])
    ignored = True if rc == 0 else False if rc == 1 else None
    rc2, out = ctx.run(["git", "-C", str(root), "ls-files", "-z", "--", str(p)])
    tracked = len([x for x in out.split("\0") if x.strip()]) if rc2 == 0 else 0
    return ignored, tracked


def check_datasets(proj: Project, ctx: Ctx) -> list[dict]:
    out: list[dict] = []
    if proj.registry_problem:
        rel = (proj.pdir / "Datos" / "registro.json").relative_to(proj.vault).as_posix() \
            if inside(proj.pdir, proj.vault) else "Datos/registro.json"
        return [check("registro", "critico",
                      f"El registro de datos {rel} es ilegible ({proj.registry_problem}); el guardia de datos "
                      "rechaza toda llamada en el vault hasta que se arregle y este diagnóstico no puede "
                      "comprobar los datasets.",
                      "Corrígelo desde el panel Datos de la interfaz o a mano (una lista JSON de "
                      "{name, path, kind, sensitive, …} con rutas absolutas).", "Datos/registro.json")]
    if not proj.datasets:
        return [check("datos_ubicacion", "ok", "No hay datasets registrados en Datos/registro.json todavía.",
                      "", "Datos/registro.json")]
    bad = False
    for d in proj.datasets:
        if d.path is None:                   # reported by load_registry
            bad = True
            continue
        p, s = d.path, d.sensitive
        label = f"El dataset{' sensible' if s else ''} «{d.name}»"
        if not _exists(p):
            bad = True
            out.append(check("datos_ubicacion", "importante",
                             f"{label} no existe en {d.raw}.",
                             "Comprueba la ruta registrada (¿disco externo sin montar, carpeta movida?) y "
                             "corrígela en Datos/registro.json.", d.name))
        if inside(p, proj.vault):
            bad = True
            out.append(check("datos_ubicacion", "critico" if s else "importante",
                             f"{label} está dentro del vault ({d.raw}).",
                             "Muévelo fuera del vault (p. ej. a una carpeta de datos local, no sincronizada) "
                             "y actualiza su ruta en Datos/registro.json.", d.name))
            continue
        root = git_root(p if (d.kind == "dir" or not _exists(p) or p.is_dir()) else p.parent)
        if root is None:
            continue
        bad = True
        where = ("el repositorio de código del proyecto" if proj.code_repo and _key(root) == _key(proj.code_repo)
                 else f"un árbol de trabajo git ({root})")
        ignored, tracked = _git_ignore_state(root, p, ctx)
        if tracked:
            out.append(check("gitignore", "critico" if s else "importante",
                             f"{label} está dentro de {where} y git ya sigue {tracked} archivo(s) suyos.",
                             f"Sácalos del índice (`git rm -r --cached` sobre esa ruta), añade la ruta a "
                             f".gitignore y mueve el dataset fuera de {root}; si llegaron a un remoto, "
                             "trátalo como una fuga.", d.name))
        elif ignored is False:
            out.append(check("gitignore", "critico" if s else "importante",
                             f"{label} está dentro de {where} y el .gitignore no lo cubre: un `git add .` "
                             "lo subiría.",
                             f"Mueve el dataset fuera de {root} (lo mejor), o añade su ruta al .gitignore "
                             "de ese repositorio ya.", d.name))
        elif ignored is None:
            out.append(check("gitignore", "importante",
                             f"{label} está dentro de {where} y no se pudo comprobar si git lo ignora.",
                             f"Mueve el dataset fuera de {root}, o comprueba a mano con "
                             f"`git -C \"{root}\" check-ignore -v -- <ruta>`.", d.name))
        else:
            out.append(check("datos_ubicacion", "importante" if s else "menor",
                             f"{label} está dentro de {where}; el .gitignore lo cubre, pero una sesión de "
                             "agente abierta en ese árbol lo tiene a mano.",
                             f"Mueve el dataset fuera de {root} y actualiza su ruta en Datos/registro.json.",
                             d.name))
    if not bad:
        out.append(check("datos_ubicacion", "ok",
                         f"Los {len(proj.datasets)} dataset(s) registrados existen y están fuera del vault y de "
                         "todo árbol git.", "", "Datos/registro.json"))
    return out


def check_cloud(proj: Project, ctx: Ctx) -> list[dict]:
    if proj.registry_problem:
        return []
    out = []
    for d in proj.sensitive:
        svc = cloud_folder(d.path, ctx)
        if svc:
            out.append(check("nube", "critico",
                             f"El dataset sensible «{d.name}» está en una carpeta sincronizada con {svc}: "
                             "se copia a la nube de un tercero.",
                             "Muévelo a una carpeta local fuera de toda sincronización (o excluye esa carpeta "
                             "de la sincronización y espera a que se borre de la nube) y actualiza su ruta en "
                             "Datos/registro.json.", d.name))
    if not out:
        msg = ("Ningún dataset sensible está en una carpeta sincronizada (OneDrive, Dropbox, Google Drive, iCloud)."
               if proj.sensitive else "No hay datasets sensibles registrados.")
        out.append(check("nube", "ok", msg))
    return out


_BDE_ON = ("protection on", "protección activada", "protección: activada", "protection status: on")
_BDE_OFF = ("protection off", "protección desactivada")
_BDE_ENCRYPTED = ("fully encrypted", "cifrado completo", "totalmente cifrado", "completamente cifrado")
_BDE_DECRYPTED = ("fully decrypted", "descifrado completo", "totalmente descifrado", "completamente descifrado")


def _bitlocker(drive: str, ctx: Ctx) -> tuple[str, str, str]:
    manual = (f"Compruébalo a mano en una consola de administrador: `manage-bde -status {drive}` "
              "(o Configuración → Privacidad y seguridad → Cifrado de dispositivo / BitLocker).")
    rc, out = ctx.run(["manage-bde", "-status", drive])
    low = out.casefold()
    if rc != 0:
        return "importante", f"No se pudo comprobar el cifrado de {drive} (manage-bde suele pedir permisos de " \
                             "administrador).", manual
    if any(m in low for m in _BDE_ON):
        return "ok", f"{drive} está cifrado con BitLocker y la protección está activa.", ""
    if any(m in low for m in _BDE_OFF) and any(m in low for m in _BDE_ENCRYPTED):
        return "importante", f"{drive} está cifrado con BitLocker pero la protección está suspendida.", \
            f"Reanúdala: `manage-bde -protectors -enable {drive}` en una consola de administrador."
    if any(m in low for m in _BDE_DECRYPTED):
        return "critico", f"{drive} no está cifrado (BitLocker: totalmente descifrado).", \
            "Activa BitLocker o el cifrado de dispositivo en ese volumen, o mueve los datasets sensibles a un " \
            "volumen cifrado."
    return "importante", f"No se pudo interpretar la salida de manage-bde para {drive}.", manual


def _filevault(p: Path, ctx: Ctx) -> tuple[str, str, str]:
    parts = p.parts
    if len(parts) > 2 and parts[1] == "Volumes":
        vol = f"/Volumes/{parts[2]}"
        manual = f"Compruébalo a mano: `diskutil info \"{vol}\"` (línea FileVault) o `diskutil apfs list`."
        rc, out = ctx.run(["diskutil", "info", vol])
        m = re.search(r"^\s*FileVault:\s*(\w+)", out, re.M | re.I)
        if rc != 0 or not m:
            return "importante", f"No se pudo comprobar el cifrado del volumen {vol}.", manual
        if m.group(1).casefold() == "yes":
            return "ok", f"El volumen {vol} está cifrado (FileVault).", ""
        return "critico", f"El volumen {vol} no está cifrado.", \
            "Cífralo (Utilidad de Discos → Cifrar) o mueve los datasets sensibles a un volumen cifrado."
    rc, out = ctx.run(["fdesetup", "status"])
    low = out.casefold()
    manual = "Compruébalo a mano: `fdesetup status` o Ajustes del Sistema → Privacidad y seguridad → FileVault."
    if rc == 0 and "filevault is on" in low:
        return "ok", "El disco de arranque está cifrado (FileVault activado).", ""
    if rc == 0 and "filevault is off" in low:
        return "critico", "El disco de arranque no está cifrado (FileVault desactivado).", \
            "Activa FileVault (Ajustes del Sistema → Privacidad y seguridad → FileVault)."
    return "importante", "No se pudo comprobar FileVault.", manual


def _lsblk_crypt(nodes: list, mount: str, above_crypt: bool = False) -> bool | None:
    """Whether the device mounted at `mount` has a crypt device at or above it; None when not found."""
    for n in nodes or []:
        if not isinstance(n, dict):
            continue
        crypt = above_crypt or str(n.get("type") or "").casefold() == "crypt"
        mps = n.get("mountpoints") if isinstance(n.get("mountpoints"), list) else [n.get("mountpoint")]
        if mount in [m for m in mps if m]:
            return crypt
        found = _lsblk_crypt(n.get("children") or [], mount, crypt)
        if found is not None:
            return found
    return None


def _dmcrypt(p: Path, ctx: Ctx) -> tuple[str, str, str]:
    mount = ctx.mount_of(p).as_posix()
    manual = "Compruébalo a mano: `lsblk -o NAME,TYPE,MOUNTPOINT` (busca un dispositivo `crypt` por encima " \
             f"de {mount}) o `cryptsetup status <dispositivo>`."
    rc, out = ctx.run(["lsblk", "-J", "-o", "NAME,TYPE,MOUNTPOINT"])
    try:
        tree = json.loads(out).get("blockdevices") if rc == 0 else None
    except (ValueError, AttributeError):
        tree = None
    if not isinstance(tree, list):
        return "importante", f"No se pudo comprobar el cifrado del volumen montado en {mount}.", manual
    found = _lsblk_crypt(tree, mount)
    if found is None:
        return "importante", f"No se encontró el dispositivo montado en {mount} en lsblk (¿red, contenedor, " \
                             "overlay?); no se pudo comprobar el cifrado.", manual
    if found:
        return "ok", f"El volumen montado en {mount} está sobre dm-crypt (LUKS).", ""
    return "importante", f"El volumen montado en {mount} no está sobre dm-crypt (LUKS).", \
        "Mueve los datasets sensibles a un volumen cifrado (LUKS), o confirma a mano que otro cifrado " \
        "(fscrypt, ZFS nativo, ecryptfs) lo protege."


def check_encryption(proj: Project, ctx: Ctx) -> list[dict]:
    if proj.registry_problem:
        return []
    if not proj.sensitive:
        return [check("cifrado", "ok", "No hay datasets sensibles registrados; no hace falta comprobar el cifrado.")]
    volumes: dict[str, tuple[Path, list[str]]] = {}
    for d in proj.sensitive:
        anchor = existing_anchor(d.path)
        if ctx.windows:
            key = anchor.drive or str(anchor)
        elif ctx.platform == "darwin":
            parts = anchor.parts
            key = f"/Volumes/{parts[2]}" if len(parts) > 2 and parts[1] == "Volumes" else "/"
        else:
            key = ctx.mount_of(anchor).as_posix()
        volumes.setdefault(key, (anchor, []))[1].append(d.name)
    out = []
    for key, (anchor, names) in volumes.items():
        if ctx.windows:
            if not re.fullmatch(r"[A-Za-z]:", key):
                sev, msg, fix = ("importante", f"{key} no es una unidad local con letra; no se pudo comprobar su "
                                 "cifrado.", "Comprueba a mano que ese recurso está cifrado, o mueve los datos a "
                                 "una unidad local cifrada.")
            else:
                sev, msg, fix = _bitlocker(key.upper(), ctx)
        elif ctx.platform == "darwin":
            sev, msg, fix = _filevault(anchor, ctx)
        elif ctx.platform.startswith("linux"):
            sev, msg, fix = _dmcrypt(anchor, ctx)
        else:
            sev, msg, fix = ("importante", f"No se sabe comprobar el cifrado en {ctx.platform}.",
                             "Comprueba a mano que el volumen de los datasets sensibles está cifrado.")
        out.append(check("cifrado", sev, f"{msg} Datasets sensibles en ese volumen: {', '.join(names)}.", fix,
                         key))
    return out


def token_path(ctx: Ctx) -> tuple[Path, bool]:
    """(token file path, named explicitly by KAIRO_GITHUB_TOKEN_FILE)."""
    raw = (ctx.environ.get("KAIRO_GITHUB_TOKEN_FILE") or "").strip()
    if raw:
        if raw.startswith("~"):
            raw = str(ctx.home) + raw[1:]
        return _abs(raw), True
    return _abs(ctx.home / ".kairo" / "github-token"), False


def check_token(proj: Project, ctx: Ctx) -> list[dict]:
    out = []
    for var in TOKEN_ENV:
        if (ctx.environ.get(var) or "").strip():
            out.append(check("token_github", "critico",
                             f"La variable {var} está definida en este entorno: toda sesión de agente que se "
                             "abra desde él la hereda y puede usarla.",
                             "Quítala del perfil de la shell / variables de usuario y guarda el token en un "
                             "archivo (KAIRO_GITHUB_TOKEN_FILE, por defecto ~/.kairo/github-token); "
                             "abre después una terminal nueva.", var))
    if not out:
        out.append(check("token_github", "ok", "Ni GITHUB_TOKEN ni GH_TOKEN están definidas en este entorno."))
    p, explicit = token_path(ctx)
    try:
        st = ctx.stat(p)
    except FileNotFoundError:
        st = None
    except OSError as exc:
        out.append(check("token_github", "importante", f"No se pudo consultar el archivo del token ({p}): "
                         f"{type(exc).__name__}.", "Comprueba sus permisos.", str(p)))
        return out
    if st is None:
        out.append(check("token_github", "importante" if explicit else "menor",
                         f"No existe el archivo del token de GitHub ({p})"
                         + (", aunque KAIRO_GITHUB_TOKEN_FILE lo nombra." if explicit else "."),
                         "Si la recolección usa la API de GitHub, guarda ahí un token de solo lectura "
                         "(fuera del vault, de todo repositorio y de carpetas sincronizadas)"
                         + (", o corrige KAIRO_GITHUB_TOKEN_FILE." if explicit else "."), str(p)))
        return out
    bad = False
    if not statmod.S_ISREG(st.st_mode):
        bad = True
        out.append(check("token_github", "importante", f"{p} no es un archivo normal.",
                         "Guarda el token en un archivo de texto con solo el token.", str(p)))
    if inside(p, proj.vault):
        bad = True
        out.append(check("token_github", "critico", f"El archivo del token de GitHub ({p}) está dentro del vault.",
                         "Muévelo fuera (por defecto ~/.kairo/github-token), apunta KAIRO_GITHUB_TOKEN_FILE ahí "
                         "y revoca el token si el vault llegó a copiarse.", str(p)))
    else:
        root = git_root(p.parent)
        if root is not None:
            bad = True
            ignored, tracked = _git_ignore_state(root, p, ctx)
            sev = "critico" if tracked or ignored is False else "importante"
            extra = (" y git ya lo sigue" if tracked else "; el .gitignore lo cubre" if ignored
                     else " y el .gitignore no lo cubre" if ignored is False
                     else "; no se pudo comprobar si git lo ignora")
            out.append(check("token_github", sev,
                             f"El archivo del token de GitHub ({p}) está dentro de un árbol git ({root}){extra}.",
                             "Muévelo fuera de todo repositorio (por defecto ~/.kairo/github-token)"
                             + ("; como ya estuvo en git, revoca el token y crea otro." if tracked else "."),
                             str(p)))
    svc = cloud_folder(p, ctx)
    if svc:
        bad = True
        out.append(check("token_github", "importante",
                         f"El archivo del token de GitHub ({p}) está en una carpeta sincronizada con {svc}.",
                         "Muévelo a una carpeta local no sincronizada (por defecto ~/.kairo/github-token).",
                         str(p)))
    if not ctx.windows and statmod.S_ISREG(st.st_mode) and st.st_mode & 0o077:
        bad = True
        out.append(check("token_github", "importante",
                         f"El archivo del token de GitHub ({p}) es legible por otros usuarios "
                         f"(permisos {oct(st.st_mode & 0o777)}).",
                         f"`chmod 600 \"{p}\"`.", str(p)))
    if not bad:
        out.append(check("token_github", "ok",
                         f"El archivo del token ({p}) existe fuera del vault, de los repositorios y de la nube"
                         + ("" if ctx.windows else ", con permisos solo para su dueño") + ".", "", str(p)))
    return out


def check_kairo_vault(proj: Project, ctx: Ctx) -> list[dict]:
    raw = (ctx.environ.get("KAIRO_VAULT") or "").strip()
    sev = "importante" if proj.sensitive else "menor"
    if not raw:
        return [check("kairo_vault", sev,
                      "KAIRO_VAULT no está definida: en las sesiones de Claude Code abiertas en el repositorio de "
                      "código (fuera del vault) el guardia de datos no actúa.",
                      f"Define KAIRO_VAULT={proj.vault} en las variables de usuario (Windows) o en el perfil de la "
                      "shell, y abre una terminal nueva.")]
    v = _abs(raw)
    if _key(v) != _key(proj.vault):
        return [check("kairo_vault", sev,
                      f"KAIRO_VAULT apunta a {raw}, no a este vault ({proj.vault}): en las sesiones abiertas en el "
                      "repositorio de código el guardia protegería otros datos.",
                      f"Cambia KAIRO_VAULT a {proj.vault} y abre una terminal nueva.")]
    return [check("kairo_vault", "ok", "KAIRO_VAULT nombra este vault.")]


def _budget(proj: Project) -> tuple[float | None, dict | None]:
    raw = proj.hub.get("presupuesto_disco_gb")
    if ps.placeholder(raw):
        return None, None
    try:
        v = float(str(raw).strip().replace(",", "."))
    except ValueError:
        return None, check("disco", "menor", f"`presupuesto_disco_gb` del hub no es un número ({raw}).",
                           "Pon los GB que necesita la recolección (p. ej. `presupuesto_disco_gb: 200`) o borra "
                           "la línea.", "_hub.md:presupuesto_disco_gb")
    return v, None


def check_disk(proj: Project, ctx: Ctx) -> list[dict]:
    if proj.registry_problem:
        return []
    budget, problem = _budget(proj)
    out = [problem] if problem else []
    targets = [d for d in proj.datasets if d.path is not None]
    if not targets:
        return out + [check("disco", "ok", "No hay datasets registrados; no se comprueba el espacio libre.")]
    seen: set[str] = set()
    for d in targets:
        anchor = existing_anchor(d.path)
        key = (anchor.drive or str(anchor)) if ctx.windows else ctx.mount_of(anchor).as_posix()
        if _key(Path(key)) in seen:
            continue
        seen.add(_key(Path(key)))
        try:
            total, _used, free = ctx.disk_usage(anchor)
        except OSError:
            out.append(check("disco", "menor", f"No se pudo consultar el espacio libre junto a «{d.name}».",
                             "Compruébalo a mano.", d.name))
            continue
        free_gb = free / GB
        if budget is not None and free_gb < budget:
            out.append(check("disco", "importante",
                             f"Quedan {free_gb:.1f} GB libres en el volumen de «{d.name}», menos que el "
                             f"presupuesto del hub ({budget:g} GB).",
                             "Libera espacio o mueve la salida de la recolección a un volumen con sitio "
                             "(fuera de la nube y cifrado).", d.name))
        elif total and free / total < LOW_FREE:
            out.append(check("disco", "menor",
                             f"Queda menos del 10 % libre ({free_gb:.1f} GB de {total / GB:.1f} GB) en el "
                             f"volumen de «{d.name}».",
                             "Libera espacio antes de recolectar o fija `presupuesto_disco_gb` en el hub.", d.name))
        else:
            out.append(check("disco", "ok", f"{free_gb:.1f} GB libres en el volumen de «{d.name}»"
                             + (f" (presupuesto {budget:g} GB)." if budget is not None else "."), "", d.name))
    return out


def _etica_path(proj: Project) -> Path | None:
    raw = ps.scalar(proj.hub, "etica")
    if not raw:
        return None
    raw = raw.strip().strip("[]").split("|", 1)[0].strip()
    cands = [Path(raw)] if Path(raw).is_absolute() else [proj.pdir / raw, proj.vault / raw]
    for c in list(cands):
        if c.suffix.lower() != ".md":
            cands.append(c.with_name(c.name + ".md"))
    for c in cands:
        if c.is_file():
            return c
    return cands[0]


def check_ethics(proj: Project, ctx: Ctx) -> list[dict]:
    out = []
    hub_path = _etica_path(proj)
    local = proj.pdir / "Etica.md"
    if local.is_file() or (hub_path is not None and hub_path.is_file()):
        out.append(check("etica", "ok", "El proyecto tiene su nota de ética.", "",
                         "Etica.md" if local.is_file() else str(hub_path)))
    else:
        named = f" (el hub dice `etica: {ps.scalar(proj.hub, 'etica')}`, que no existe)" if hub_path else ""
        out.append(check("etica", "importante",
                         f"El proyecto no tiene nota de ética{named}: con material sensible de terceros hace "
                         "falta escribir cómo se recoge, guarda, minimiza, notifica y borra.",
                         f"Escribe Projects/{proj.pdir.name}/Etica.md (o apunta `etica:` del hub a la nota) antes "
                         "de recolectar.", "Etica.md"))
    frozen = []
    for m in sorted((proj.pdir / "Mediciones").glob("*.md")):
        parsed = read_note(m)
        if parsed and str(parsed[0].get("status") or "").strip() == "congelado":
            frozen.append(m.stem.split(" ")[0])
    if frozen and not (proj.pdir / "Avisos" / "registro.json").is_file():
        out.append(check("avisos", "menor",
                         f"Hay plan(es) congelado(s) ({', '.join(frozen)}) pero no existe Avisos/registro.json.",
                         "Créalo desde el panel Avisos de la interfaz (freeze_plan.py lo crea al congelar; "
                         "no se escribe a mano desde una sesión de agente).", "Avisos/registro.json"))
    elif frozen:
        out.append(check("avisos", "ok", "Existe Avisos/registro.json para los planes congelados.", "",
                         "Avisos/registro.json"))
    return out


CHECKS = (check_datasets, check_cloud, check_encryption, check_token, check_kairo_vault, check_disk, check_ethics)


def run(vault: Path, project: str, ctx: Ctx | None = None) -> dict:
    ctx = ctx or Ctx()
    proj, entry_problems = load_project(vault, project)
    checks = list(entry_problems)
    for fn in CHECKS:
        checks += fn(proj, ctx)
    checks.sort(key=lambda c: ORDER.get(c["severity"], 9))
    counts = {s: sum(1 for c in checks if c["severity"] == s) for s in SEVERITIES}
    pending = len(checks) - counts["ok"]
    summary = [f"Diagnóstico del entorno de {proj.pid} ({proj.pdir.name}): {pending} aviso(s)"
               + (f" — {', '.join(f'{n} {s}' for s, n in counts.items() if n and s != 'ok')}" if pending else ".")]
    for c in checks:
        summary.append(f"  [{c['severity']}] {c['message']}" + (f"\n      → {c['fix']}" if c["fix"] else ""))
    return {"tool": TOOL, "project": proj.pid, "slug": proj.pdir.name,
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "platform": ctx.platform, "counts": counts, "checks": checks, "resumen": summary}


def main(argv: list[str] | None = None, ctx: Ctx | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code else 0
    ctx = ctx or Ctx()
    if (ctx.environ.get("KAIRO_PROFILE") or "").strip().lower() == "literatura":
        msg = ("Perfil `literatura` activo (KAIRO_PROFILE): el diagnóstico de un estudio de medición pertenece al "
               "programa de investigación. Quita KAIRO_PROFILE (o ponlo a `completo`) para usarlo.")
        print(json.dumps({"refused": True, "message": msg}, ensure_ascii=False) if a.json else msg)
        return 3
    try:
        res = run(Path(a.vault), a.project, ctx)
    except InputError as exc:
        print(f"doctor: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(res, ensure_ascii=False, indent=2) if a.json else "\n".join(res["resumen"]))
    return 4 if res["counts"]["critico"] else 0


if __name__ == "__main__":
    sys.exit(main())
