#!/usr/bin/env python3
"""Keep every agent away from sensitive datasets and from `Projects/*/Avisos/`.

A project's datasets are registered in `Projects/<slug>/Datos/registro.json`, a
JSON array of `{name, path, kind: "file"|"dir", files, bytes, sha256,
registered_at, by, sensitive?}`; `path` is absolute and lies outside the vault.
A dataset with `sensitive: true` must never reach a model, and neither must the
project's notices (`Projects/<slug>/Avisos/`, whose files describe that data).
This module is the mechanical barrier, called by `kairo_hook.py pre-tool` after
send_guard, for the main session and every subagent alike.

    sensitive_roots(vault) -> list[Path]   resolved paths of every protected root
    decide(event, vault)   -> str | None    a block reason, or None to allow

A protected root is a sensitive dataset's path (a `file` dataset protects that
file, a `dir` dataset everything under it) or a project's `Avisos/` folder. A
tool call is refused when it would reach one:

  Read                    `file_path` is a root or lies under one.
  Edit / Write / MultiEdit / NotebookEdit
                          `file_path` / `notebook_path` is a root or lies under one.
  Grep / Glob             the search scope is a root, lies under one, or lies
                          ABOVE one (any output_mode: file names are data too).
                          Above a root, the call passes only when its filter
                          (Grep `glob`/`type`, Glob `pattern`) matches no file
                          that is in the root now.
  Bash / PowerShell       a word of the command (send_guard's tokenizer; also
                          its `--opt=value` / `-Opt:value` part) resolves, from
                          the cwd or from any folder the command names (`cd ..;
                          cat DS/f`), to a root or below it; a glob can match
                          one or descend into one; a recursive command (`-r`,
                          `-R`, `-Recurse`, `/s`, rg, find, tree, du, …) runs on
                          a folder above one (the cwd counts); the command text
                          holds a root's absolute path (`python -c "open(…)"`);
                          the cwd is inside a root; or a git command that
                          prints file contents (`git show`, `diff`, `log -p`,
                          `grep`, …; isolation.py's rules) runs in a folder
                          holding a root, unless its pathspec after `--` stays
                          clear of every root.
  mcp__* (Smart Connections get_note, search_similar, …)
                          `notePath` / `path` (relative to the vault) is a root
                          or lies under one.

Paths are cleaned as the tools clean them, `~` and environment variables are
expanded, backslashes count as separators, everything is `resolve()`d (so `..`
and symlinks are followed), and the comparison ignores case on every OS
(Windows paths do; elsewhere refusing a differently-cased twin costs nothing).
Windows spellings of one path are folded first: extended and device paths
(`//?/C:/x`, `//./C:/x`), `//?/UNC/host/share/x`, and this machine's admin
share (`//localhost/C$/x`, `//127.0.0.1/C$/x`), each also with backslashes.
For paths that exist, file identity (st_dev, st_ino) is the backstop: a hard
link, an 8.3 short name or another share of the same disk still counts.
Quotes and backticks inside a shell word are dropped too (`D""S`, `D"S"` and
PowerShell's escaped D`S all mean DS), and the command text is scanned for
each root's resolved path and for its path as registered.
`Avisos` is also matched by name (`Projects/<any>/Avisos/…`), so a notice folder
that does not exist yet is covered too.

Fail closed: a Projects/ (or project) folder that cannot be listed, or a
registry that exists but cannot be read or parsed, is not an array, or has a
sensitive entry without an absolute `path` refuses every
guarded tool call in that vault with «registro de datos ilegible» and the
file's path. Block reasons name the dataset (`name`, project slug) or the
Avisos folder, never file contents.

CLI, for the app (it guards the agent sessions it runs in code worktrees,
where this hook is not loaded):

    python data_guard.py roots --vault <vault>
      exit 0  {"roots": [{"path", "registered", "dataset", "project",
                          "kind": "file"|"dir"|"avisos"}]}
      exit 3  {"error": "registro de datos ilegible", "file", "detail"}
      exit 1  the vault folder does not exist

Limits (stated, not hidden): like send_guard's shell check this is best
effort; a program can open a path no command line shows (a script that builds
it). The registry is re-read only when its mtime or size changes
(`_MEMO`, per process). The guard acts only when the hook finds a vault (the
session folder, its `vault/` subfolder or `$KAIRO_VAULT`): in a Claude Code
session opened in the code repository, outside the vault, it does nothing
unless KAIRO_VAULT names the vault («En las sesiones de Claude Code abiertas en
el repositorio de código (fuera del vault), define KAIRO_VAULT con la ruta del
vault para que el guardia de datos actúe»). A git-tracked copy of a sensitive
dataset inside the code repository is not a registered root and is not
covered. Standard library only.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from send_guard import SHELL_READER, _clean, shell_is_recursive, shell_tokens

__version__ = "1.2.0"

REGISTRY = ("Datos", "registro.json")
AVISOS = "avisos"
GUARDED = ("Read", "Grep", "Glob", "Bash", "PowerShell", "Edit", "Write", "MultiEdit", "NotebookEdit")
EDITING = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
MAX_SCAN = 20000           # files enumerated under a root before an ancestor filter just refuses


class RegistryError(Exception):
    def __init__(self, path: Path, why: str):
        super().__init__(f"{path}: {why}")
        self.path, self.why = path, why


@dataclass(frozen=True)
class Root:
    path: Path
    label: str             # what a block reason names
    registered: str = ""   # the path as the registry wrote it (Avisos: Projects/<slug>/Avisos)
    dataset: str | None = None
    project: str = ""
    kind: str = "dir"      # "file" | "dir" | "avisos"


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

# Windows spellings of one path: \\?\C:\x and \\.\C:\x (extended / device),
# \\?\UNC\host\share\x, and the admin share of this machine \\localhost\C$\x.
_EXT_UNC = re.compile(r"^//[?.]/unc/", re.IGNORECASE)
_EXT = re.compile(r"^//[?.]/(?=[a-z]:)", re.IGNORECASE)
_ADMIN_SHARE = re.compile(r"^//(?:localhost|127\.0\.0\.1|\[?::1\]?)/([a-z])\$(?=/|$)", re.IGNORECASE)


# The same forms found anywhere in a command text (not only at its start).
_EXT_UNC_ANY = re.compile(r"(?<![\w:/])//[?.]/unc/", re.IGNORECASE)
_EXT_ANY = re.compile(r"(?<![\w:/])//[?.]/(?=[a-z]:)", re.IGNORECASE)
_ADMIN_SHARE_ANY = re.compile(r"(?<![\w:/])//(?:localhost|127\.0\.0\.1|\[?::1\]?)/([a-z])\$(?=/|$|[^\w$])", re.IGNORECASE)


def _plain(s: str, anywhere: bool = False) -> str:
    """One spelling for the forms above, backslashes as `/`: `C:/x`. `anywhere`
    folds them wherever they occur in the text, for scanning a command line."""
    s = s.replace("\\", "/")
    s = (_EXT_UNC_ANY if anywhere else _EXT_UNC).sub("//", s)
    s = (_EXT_ANY if anywhere else _EXT).sub("", s)
    return (_ADMIN_SHARE_ANY if anywhere else _ADMIN_SHARE).sub(lambda m: m.group(1) + ":", s)


def _key(p: Path | str) -> str:
    return _plain(str(p)).rstrip("/").casefold()


def _resolve(raw: str, base: Path) -> Path:
    s = _plain(os.path.expandvars(os.path.expanduser(_clean(raw))))
    q = Path(s)
    q = q if q.is_absolute() else base / q
    try:
        return Path(_plain(str(q.resolve())))
    except (OSError, RuntimeError, ValueError):
        return Path(_plain(os.path.abspath(str(q))))


_IDS: dict[str, tuple[int, int] | None] = {}


def _file_id(p: Path) -> tuple[int, int] | None:
    """(st_dev, st_ino) of an existing path, None when unknown -- memoised per process."""
    k = str(p)
    if k not in _IDS:
        try:
            st = os.stat(p)
            _IDS[k] = (st.st_dev, st.st_ino) if st.st_ino else None
        except (OSError, ValueError):
            _IDS[k] = None
    return _IDS[k]


def _inside(p: Path, r: Path) -> bool:
    """p is r or lies under r: by spelling (case-insensitive), and as a backstop by
    file identity (a hard link, a short 8.3 name, a share of this disk)."""
    kp, kr = _key(p), _key(r)
    if kp == kr or kp.startswith(kr + "/"):
        return True
    rid = _file_id(r)
    if rid is None:
        return False
    for a in (p, *p.parents):
        if _file_id(a) == rid:
            return True
    return False


def _above(p: Path, r: Path) -> bool:
    """p is a strict ancestor of r."""
    return _inside(r, p) and _key(p) != _key(r)


# --------------------------------------------------------------------------- #
# Registry and roots
# --------------------------------------------------------------------------- #

# (registry path, mtime_ns, size) -> [(dataset path as registered, name, kind)]
_MEMO: dict[tuple[str, int, int], list[tuple[str, str, str]]] = {}


def _load_registry(path: Path) -> list[tuple[str, str, str]]:
    """The (path, name, kind) of every sensitive dataset in one registry; RegistryError when unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise RegistryError(path, type(exc).__name__) from None
    if not isinstance(data, list):
        raise RegistryError(path, "no es una lista")
    out = []
    for i, d in enumerate(data):
        if not isinstance(d, dict):
            raise RegistryError(path, f"la entrada {i} no es un objeto")
        if d.get("sensitive") in (None, False):
            continue                         # missing means false; anything else counts as sensitive
        p = d.get("path")
        if not isinstance(p, str) or not p.strip() or not Path(p.strip()).is_absolute():
            raise RegistryError(path, f"la entrada sensible {i} no tiene una ruta absoluta")
        kind = d.get("kind") if d.get("kind") in ("file", "dir") else "dir"
        out.append((p.strip(), str(d.get("name") or f"#{i}"), kind))
    return out


def _projects(vault: Path) -> list[Path]:
    proj = vault / "Projects"
    try:
        return sorted(p for p in proj.iterdir() if p.is_dir())
    except FileNotFoundError:
        return []                            # no Projects/: not a vault, nothing registered
    except OSError:
        raise RegistryError(proj, "no se puede listar") from None


def _roots(vault: Path) -> list[Root]:
    roots: list[Root] = []
    for proj in _projects(vault):
        reg = proj.joinpath(*REGISTRY)
        try:
            st = reg.stat()
        except FileNotFoundError:
            reg_entries = []
        except OSError:
            raise RegistryError(reg, "no se puede leer") from None
        else:
            k = (str(reg), st.st_mtime_ns, st.st_size)
            if k not in _MEMO:
                _MEMO[k] = _load_registry(reg)
            reg_entries = _MEMO[k]
        for p, name, kind in reg_entries:
            roots.append(Root(_resolve(p, vault), f"el dataset sensible «{name}» del proyecto {proj.name}",
                              p, name, proj.name, kind))
        try:
            children = list(proj.iterdir())
        except OSError:
            raise RegistryError(proj, "no se puede listar") from None
        for child in children:
            if child.name.casefold() == AVISOS and child.is_dir():
                roots.append(Root(_resolve(str(child), vault), f"Projects/{proj.name}/{child.name}/ (avisos del proyecto)",
                                  f"Projects/{proj.name}/{child.name}", None, proj.name, "avisos"))
    return roots


def sensitive_roots(vault: Path) -> list[Path]:
    """Resolved paths of every `sensitive: true` dataset of every project, plus
    every existing `Projects/*/Avisos`. Raises RegistryError on an unreadable registry."""
    return [r.path for r in _roots(vault)]


def _avisos_by_name(p: Path, vault: Path) -> Root | None:
    """`Projects/<any>/Avisos[/…]` inside the vault, whether or not it exists."""
    kv, kp = _key(vault.resolve()), _key(p)
    if not kp.startswith(kv + "/"):
        return None
    parts = kp[len(kv) + 1:].split("/")
    if len(parts) >= 3 and parts[0] == "projects" and parts[2] == AVISOS:
        return Root(p, f"Projects/{parts[1]}/Avisos/ (avisos del proyecto)", f"Projects/{parts[1]}/Avisos",
                    None, parts[1], "avisos")
    return None


# --------------------------------------------------------------------------- #
# Glob matching (case-insensitive, `**`, braces)
# --------------------------------------------------------------------------- #

def _glob_re(pat: str) -> re.Pattern:
    out, i, depth = [], 0, 0
    pat = pat.replace("\\", "/")
    while i < len(pat):
        c = pat[i]
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pat.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = pat.find("]", i + 1)
            if j == -1:
                out.append(re.escape(c))
            else:
                body = pat[i + 1:j].replace("\\", "\\\\")
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append(f"[{body}]")
                i = j
        elif c == "{":
            out.append("(?:")
            depth += 1
        elif c == "}" and depth:
            out.append(")")
            depth -= 1
        elif c == "," and depth:
            out.append("|")
        else:
            out.append(re.escape(c))
        i += 1
    out.append(")" * depth)
    return re.compile("".join(out), re.IGNORECASE | re.DOTALL)


def _seg_match(name: str, seg: str) -> bool:
    return bool(_glob_re(seg).fullmatch(name))


def _files_under(root: Path):
    """Every file in root (root itself for a file); None when there are too many to look at."""
    if root.is_file():
        return [root]
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            out.append(Path(dirpath) / f)
            if len(out) > MAX_SCAN:
                return None
    return out


def _filter_hits(scope: Path, root: Path, match) -> bool:
    """Above a root: does `match(path relative to scope)` hold for any file in the root now?"""
    files = _files_under(root)
    if files is None:
        return True
    for f in files:
        rel = _key(f)[len(_key(scope)) + 1:]
        if match(rel):
            return True
    return False


_RG_TYPES = {"md": (".md", ".markdown", ".mdx", ".mkd"), "markdown": (".md", ".markdown", ".mdx", ".mkd"),
             "py": (".py", ".pyi"), "js": (".js", ".mjs", ".cjs", ".jsx"), "ts": (".ts", ".tsx", ".mts", ".cts"),
             "json": (".json",), "yaml": (".yaml", ".yml"), "toml": (".toml",), "txt": (".txt",),
             "csv": (".csv",), "html": (".html", ".htm"), "css": (".css",), "tex": (".tex",)}


def _grep_match(glob: str, gtype: str):
    pats = []
    neg = False
    if glob:
        g = glob.strip()
        neg = g.startswith("!")
        g = g.lstrip("!")
        rx = _glob_re(g)
        pats.append((rx, "/" in g))
    exts = _RG_TYPES.get(gtype.casefold()) if gtype else None

    def match(rel: str) -> bool:
        if gtype and exts is None:
            return True                      # a type we cannot evaluate counts as matching
        if exts is not None and not rel.endswith(exts):
            return False
        if not pats:
            return True
        rx, anchored = pats[0]
        name = rel.rsplit("/", 1)[-1]
        hit = bool(rx.fullmatch(name)) or bool(rx.fullmatch(rel)) or \
            (anchored and any(rx.fullmatch(rel.split("/", k)[-1]) for k in range(rel.count("/") + 1)))
        return not hit if neg else hit
    return match


# --------------------------------------------------------------------------- #
# Decision
# --------------------------------------------------------------------------- #

def _hit_path(p: Path, roots: list[Root], vault: Path) -> Root | None:
    for r in roots:
        if _inside(p, r.path):
            return r
    return _avisos_by_name(p, vault)


def _scope_reason(tool: str, scope: Path, roots: list[Root], vault: Path, match) -> str | None:
    r = _hit_path(scope, roots, vault)
    if r:
        return f"{tool} busca dentro de {r.label}; no se lee."
    for r in roots:
        if _above(scope, r.path) and (match is None or _filter_hits(scope, r.path, match)):
            return (f"{tool} sobre «{scope.name or scope}» alcanzaría {r.label}; acota la ruta "
                    "o el filtro para dejarlo fuera.")
    return None


def _split_glob(raw: str) -> tuple[str, list[str]]:
    """('fixed/prefix', ['glob', 'segments']) of a path pattern."""
    segs = raw.replace("\\", "/").split("/")
    for i, s in enumerate(segs):
        if any(c in s for c in "*?[{"):
            prefix = "/".join(segs[:i])
            if not prefix and raw.replace("\\", "/").startswith("/"):
                prefix = "/"
            return prefix, segs[i:]
    return raw, []


def _glob_reaches(prefix: Path, segs: list[str], root: Path, recursive: bool) -> bool:
    """Can a shell glob `prefix/segs…` name the root, something in it, or (recursive) above it?"""
    if _inside(prefix, root):
        return True
    if not _above(prefix, root):
        return False
    rest = _key(root)[len(_key(prefix)) + 1:].split("/")
    for i, seg in enumerate(segs):
        if "**" in seg:
            return True
        if i >= len(rest):
            return True                      # the pattern goes on inside the root
        if seg in ("", ".") or not _seg_match(rest[i], seg):
            return False
    return len(segs) >= len(rest) or recursive


_ALWAYS_RECURSIVE = re.compile(
    r"(?:^|[\s;|&(`\"'])(?:rg|ag|ack|fd|find|tree|du|robocopy|xcopy|tar|zip|7z|rsync)(?:\.exe)?(?=\s|$|[;|&)])",
    re.IGNORECASE)
_LISTER = re.compile(r"(?:^|[\s;|&(`\"'])(?:ls|dir|gci|get-childitem|copy-item|cpi|select-string|sls)(?:\.exe)?\b",
                     re.IGNORECASE)
_GIT = re.compile(r"(?:^|[\s;|&(`\"'])git(?:\.exe)?\s+((?:(?:-C|-c|--git-dir|--work-tree)\s+(?:\"[^\"]*\"|'[^']*'|\S+)"
                  r"\s+|--?[\w-]+(?:=\S+)?\s+)*)(show|diff|difftool|cat-file|grep|blame|annotate|log|whatchanged|"
                  r"format-patch|archive)\b([^;|&]*)", re.IGNORECASE)
_GIT_NAMES_ONLY = re.compile(r"(?:^|\s)--(?:stat|name-only|name-status|shortstat|numstat|summary|raw)\b")
_GIT_PATCH = re.compile(r"(?:^|\s)(?:-[a-zA-Z0-9]*[pu][a-zA-Z0-9]*|--patch|--full-diff|-L\S*|--cc|-c)(?=\s|$)")
_PUNCT = ";|&<>(){}`,"
_SEGMENT = re.compile(r"[;\n|&]+")
_LS_RECURSIVE = re.compile(r"(?:^|\s)(?:-[a-zA-Z]*R[a-zA-Z]*|--recursive)(?=\s|$)")   # ls -r is reverse order
_GREP_LIKE = {"rg", "grep", "egrep", "fgrep", "zgrep", "ag", "ack", "findstr"}
_PATTERN_FLAG = re.compile(r"^(?:-e|-f|--regexp|--file)(?:=|$)|^--regexp=|^--file=")


def _program(words: list[str]) -> tuple[str, int]:
    """The program a shell segment runs (lowercase basename, no .exe) and its index."""
    for i, w in enumerate(words):
        if w in ("&", "sudo", "time", "nohup", "env", "command", "exec") or re.match(r"^\w+=", w):
            continue
        name = w.replace("\\", "/").rsplit("/", 1)[-1].casefold()
        return (name[:-4] if name.endswith(".exe") else name), i
    return "", len(words)


def _segment_recursive(seg: str) -> bool:
    prog, _ = _program(shell_tokens(seg))
    if prog == "ls":
        return bool(_LS_RECURSIVE.search(seg))
    return shell_is_recursive(seg) or bool(_ALWAYS_RECURSIVE.search(seg))


def _segment_walks(seg: str) -> bool:
    """A recursive command that lists or reads what it walks."""
    if _ALWAYS_RECURSIVE.search(seg):
        return True
    return _segment_recursive(seg) and bool(SHELL_READER.search(seg) or _LISTER.search(seg))


def _names_a_scope(seg: str, cwd: Path) -> bool:
    """Whether a walking segment names a folder or file of its own (else it walks the cwd).
    The first positional word of a grep-like program is its pattern, not a path."""
    words = shell_tokens(seg)
    prog, i = _program(words)
    rest = words[i + 1:]
    positional = [w for w in rest if not w.startswith("-") or w == "-"]
    if prog in _GREP_LIKE and positional and not any(_PATTERN_FLAG.match(w) for w in rest):
        positional = positional[1:]
    for w in positional:
        try:
            if any(ch in w for ch in "*?[") or _resolve(w, cwd).exists():
                return True
        except OSError:
            continue
    return False


def _candidates(tok: str) -> list[str]:
    """A word and the path-like pieces inside it (`--in=x`, `-Path:x`, `x;`, `<x`, `a,b`)."""
    out = {tok}
    spliced = re.sub(r"[\"'`]", "", tok)          # D""S, D"S", D`S: the shell joins them into DS
    if spliced != tok:
        out |= set(_candidates(spliced))
    t = tok.strip(_PUNCT)
    out.add(t)
    if "=" in t:
        out.add(t.split("=", 1)[1])
    m = re.match(r"^-[\w-]+:(.+)$", t)
    if m:
        out.add(m.group(1))
    for piece in re.split(r"[;|&<>(){}`,=]", t):
        out.add(piece)
    return [c.strip().strip("\"'") for c in out if c and c.strip().strip("\"'") and len(c) <= 400]


def _git_reason(cmd: str, cwd: Path, bases: list[Path], roots: list[Root]) -> str | None:
    for g in _GIT.finditer(cmd):
        opts, sub, rest = g.group(1) or "", g.group(2).lower(), g.group(3)
        names_only = bool(_GIT_NAMES_ONLY.search(rest)) and not _GIT_PATCH.search(rest)
        content = (sub in ("log", "whatchanged") and bool(_GIT_PATCH.search(rest))) or \
                  (sub == "cat-file" and not re.search(r"(?:^|\s)-[tse](?=\s|$)", rest)) or \
                  (sub in ("show", "diff", "difftool") and not names_only) or \
                  sub in ("grep", "blame", "annotate", "format-patch", "archive")
        if not content:
            continue
        m = re.search(r"-C\s+(\"[^\"]*\"|'[^']*'|\S+)", opts)
        where = _resolve(m.group(1), cwd) if m else cwd
        near = [r for r in roots if _inside(where, r.path) or _above(where, r.path)]
        if not near:
            continue
        if " -- " in f" {rest} ":
            specs = shell_tokens(f" {rest} ".split(" -- ", 1)[1])
            if specs and not any(_inside(_resolve(s, where), r.path) or _above(_resolve(s, where), r.path)
                                 for s in specs for r in near):
                continue
        return f"`git {sub}` imprimiría el contenido de {near[0].label}; acota con `-- <rutas>` que lo dejen fuera."
    return None


def _shell_reason(cmd: str, cwd: Path, roots: list[Root], vault: Path) -> str | None:
    for r in roots:
        if _inside(cwd, r.path):
            return f"El comando se ejecuta dentro de {r.label}; no se ejecuta."
    texts = {_plain(cmd, anywhere=True).casefold()}
    texts.add(re.sub(r"[\"'`]", "", next(iter(texts))))
    for r in roots:
        spellings = {_key(r.path)}
        if r.registered and Path(_plain(r.registered)).is_absolute():
            spellings.add(_key(r.registered))
        for norm in texts:
            for k in spellings:
                for m in re.finditer(re.escape(k), norm):
                    nxt = norm[m.end():m.end() + 1]
                    if not nxt or not re.match(r"[\w.-]", nxt):
                        return f"El comando nombra {r.label}; no se ejecuta."
    segments = [s for s in _SEGMENT.split(cmd) if s.strip()]
    recursive = any(_segment_recursive(s) for s in segments)
    walking = [s for s in segments if _segment_walks(s)]
    words = [c for tok in shell_tokens(cmd) for c in _candidates(tok)]
    bases = [cwd]
    for w in words:
        if not any(ch in w for ch in "*?[{") and not w.startswith("-"):
            d = _resolve(w, cwd)
            try:
                if d.is_dir() and d not in bases:
                    bases.append(d)
            except OSError:
                pass
    for w in words:
        if w.startswith("-") and len(w) > 1 and not re.match(r"^-[\w-]+:", w):
            continue
        globby = any(ch in w for ch in "*?[")
        for base in bases:
            if globby:
                prefix, segs = _split_glob(w)
                pdir = _resolve(prefix or ".", base)
                for r in roots:
                    if _glob_reaches(pdir, segs, r.path, recursive):
                        return f"El comando alcanza con «{w}» {r.label}; no se ejecuta."
                continue
            p = _resolve(w, base)
            r = _hit_path(p, roots, vault)
            if r:
                return f"El comando alcanza con «{w}» {r.label}; no se ejecuta."
            if walking:
                for r in roots:
                    if _above(p, r.path):
                        return f"El comando recorre «{w}», que contiene {r.label}; acota la ruta."
    # a recursive command with no folder of its own walks the current one (after any `cd`)
    if any(not _names_a_scope(s, cwd) for s in walking):
        for base in bases:
            for r in roots:
                if _above(base, r.path):
                    return f"El comando recorre «{base.name}», que contiene {r.label}; acota la ruta."
    return _git_reason(cmd, cwd, bases, roots)


def decide(event: dict, vault: Path) -> str | None:
    """Return a block reason, or None to allow."""
    tool = str(event.get("tool_name") or "")
    if tool not in GUARDED and not tool.startswith("mcp__"):
        return None
    tin = event.get("tool_input") or {}
    if not isinstance(tin, dict):
        tin = {}
    cwd = Path(event.get("cwd") or os.getcwd())
    vault = Path(vault)
    try:
        roots = _roots(vault)
    except RegistryError as exc:
        where = _registry_where(exc, vault)
        return (f"registro de datos ilegible ({where}: {exc.why}); hasta que se arregle no se lee nada del "
                "vault por precaución. Corrígelo desde la interfaz o a mano.")

    if tool == "Read":
        fp = _clean(tin.get("file_path") or "")
        r = _hit_path(_resolve(fp, cwd), roots, vault) if fp else None
        return f"{fp} pertenece a {r.label}; no se lee." if r else None

    if tool in EDITING:
        fp = _clean(tin.get(EDITING[tool]) or "")
        r = _hit_path(_resolve(fp, cwd), roots, vault) if fp else None
        return f"{fp} pertenece a {r.label}; {tool} no lo toca." if r else None

    if tool == "Grep":
        scopes = tin.get("paths") or [tin.get("path") or "."]
        scopes = [scopes] if isinstance(scopes, str) else scopes
        match = _grep_match(str(tin.get("glob") or ""), str(tin.get("type") or ""))
        for s in scopes:
            why = _scope_reason("Grep", _resolve(str(s), cwd), roots, vault, match)
            if why:
                return why
        return None

    if tool == "Glob":
        pattern = _clean(tin.get("pattern") or "")
        base = _resolve(str(tin.get("path") or "."), cwd)
        prefix, segs = _split_glob(pattern)
        if prefix:
            base = _resolve(prefix, base)
        rx = _glob_re("/".join(segs)) if segs else None
        return _scope_reason("Glob", base, roots, vault,
                             (lambda rel: bool(rx.fullmatch(rel))) if rx else None)

    if tool in ("Bash", "PowerShell"):
        cmd = str(tin.get("command") or "")
        return _shell_reason(cmd, cwd, roots, vault) if cmd.strip() else None

    # MCP tools that take a note path relative to the vault (Smart Connections get_note, search_similar, …)
    for key in ("notePath", "path", "file_path", "filePath"):
        v = tin.get(key)
        if isinstance(v, str) and _clean(v):
            r = _hit_path(_resolve(v, vault), roots, vault)
            if r:
                return f"{_clean(v)} pertenece a {r.label}; {tool.rsplit('__', 1)[-1]} bloqueado."
    return None


def _registry_where(exc: RegistryError, vault: Path) -> str:
    try:
        return exc.path.resolve().relative_to(vault.resolve()).as_posix()
    except ValueError:
        return exc.path.as_posix()


def main(argv: list[str] | None = None) -> int:
    """`roots --vault <vault>`: every protected root as JSON, for the app to guard the
    agent sessions it runs where this plugin's hook is not loaded. Paths only, never contents."""
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="data_guard.py", description="Protected roots of a Kairo vault.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="mode", required=True)
    r = sub.add_parser("roots", help="print the protected roots as JSON")
    r.add_argument("--vault", required=True)
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    vault = Path(args.vault)
    if not vault.is_dir():
        print(json.dumps({"error": "no existe el vault", "vault": str(vault)}, ensure_ascii=False))
        return 1
    try:
        roots = _roots(vault)
    except RegistryError as exc:
        print(json.dumps({"error": "registro de datos ilegible", "file": _registry_where(exc, vault),
                          "detail": exc.why}, ensure_ascii=False))
        return 3
    print(json.dumps({"roots": [{"path": str(x.path), "registered": x.registered, "dataset": x.dataset,
                                 "project": x.project, "kind": x.kind} for x in roots]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
