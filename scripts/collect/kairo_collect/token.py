"""Where the GitHub token may live, and reading it without ever repeating it.

The token is read from the file named by ``KAIRO_GITHUB_TOKEN_FILE`` (default
``~/.kairo/github-token``). The file is refused (``TokenRefused``, exit 3 in the
CLI) when it lies

- inside a git working tree (a ``.git`` folder or file in it or any parent):
  one ``git add .`` away from being committed;
- inside an Obsidian vault (a parent holding both ``Projects/`` and
  ``.obsidian/``): the vault is synced and read by models;
- inside a synced cloud folder (OneDrive, Dropbox, Google Drive, iCloud, or
  macOS ``CloudStorage``): copied to servers the researcher does not control.

A missing file means unauthenticated mode, with a warning (code search needs a
token). The token's value never appears in a message, a log, an archive or an
exception: callers get a ``Token`` whose ``repr`` hides it.
"""
from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path

ENV_FILE = "KAIRO_GITHUB_TOKEN_FILE"
DEFAULT_FILE = Path("~/.kairo/github-token")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.\-]{20,255}$")
# Folder names of synced cloud storage (case-insensitive, whole path component or prefix).
_CLOUD_PREFIXES = ("onedrive", "dropbox", "google drive", "googledrive", "my drive",
                   "icloud drive", "iclouddrive", "mobile documents", "cloudstorage")


class TokenRefused(Exception):
    """The token file is somewhere it must not be (the message is in Spanish)."""


class Token:
    """A token value that does not print itself."""

    __slots__ = ("_value", "path")

    def __init__(self, value: str, path: Path):
        self._value = value
        self.path = path

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"Token(path={str(self.path)!r}, value=<oculto>)"

    __str__ = __repr__


def token_path(env: dict | None = None) -> Path:
    env = os.environ if env is None else env
    raw = env.get(ENV_FILE) or str(DEFAULT_FILE)
    return Path(os.path.expanduser(raw))


def _cloud_roots(env: dict) -> list[Path]:
    roots = []
    for name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        v = env.get(name)
        if v:
            roots.append(Path(v))
    return roots


def _inside(p: Path, root: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def location_problem(path: Path, env: dict | None = None) -> str | None:
    """Why the token file at `path` must not be used, or None if its place is fine."""
    env = os.environ if env is None else env
    p = Path(os.path.abspath(os.path.expanduser(str(path))))
    try:
        p = p.resolve()
    except OSError:
        pass
    for part in p.parts:
        # "OneDrive - Org", "Dropbox (Personal)", "GoogleDrive-<account>", "My Drive"
        if part.lower().startswith(_CLOUD_PREFIXES):
            return (f"el fichero del token está en una carpeta sincronizada en la nube ({part}); "
                    "muévelo fuera (por ejemplo a ~/.kairo/github-token)")
    for root in _cloud_roots(env):
        if _inside(p, root):
            return ("el fichero del token está dentro de OneDrive; muévelo fuera "
                    "(por ejemplo a ~/.kairo/github-token)")
    for parent in p.parents:
        if (parent / ".git").exists():
            return (f"el fichero del token está dentro de un repositorio git ({parent}); "
                    "un `git add` lo publicaría. Muévelo fuera (por ejemplo a ~/.kairo/github-token)")
        if (parent / "Projects").is_dir() and (parent / ".obsidian").is_dir():
            return (f"el fichero del token está dentro de una bóveda de Obsidian ({parent}); "
                    "muévelo fuera (por ejemplo a ~/.kairo/github-token)")
    return None


def load_token(env: dict | None = None, warn=None) -> Token | None:
    """The token, None for unauthenticated mode (missing file, with a warning),
    or TokenRefused when the file's place or content is not acceptable."""
    env = os.environ if env is None else env
    warn = warn or (lambda m: sys.stderr.write(f"aviso: {m}\n"))
    path = token_path(env)
    problem = location_problem(path, env)
    if problem:
        raise TokenRefused(problem)
    if not path.exists():
        warn(f"no hay token de GitHub en {path}: modo sin autenticar (60 peticiones/hora; "
             "la búsqueda de código exige token). Define KAIRO_GITHUB_TOKEN_FILE o crea ese fichero.")
        return None
    if not path.is_file():
        raise TokenRefused(f"{path} no es un fichero")
    try:
        mode = path.stat().st_mode
        if os.name == "posix" and mode & (stat.S_IRWXG | stat.S_IRWXO):
            warn(f"{path} es legible por otros usuarios; haz `chmod 600 {path}`")
    except OSError:
        pass
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        raise TokenRefused(f"no se puede leer {path} como texto") from None
    if not value:
        raise TokenRefused(f"{path} está vacío")
    if not _TOKEN_RE.match(value):
        # never echo the content: it may be the token with a typo
        raise TokenRefused(f"{path} no contiene un token con forma válida (una sola línea, sin espacios)")
    return Token(value, path)
