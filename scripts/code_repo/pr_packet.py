#!/usr/bin/env python3
"""Build the packet an isolated agent reads to describe merged pull requests.

    pr_packet.py --repo R --estado F --prs 12,13 [--max-chars 12000]

`--estado` is Projects/<slug>/Repositorio/estado.json (its `prs` list, written
by repo_sync.py). The packet goes to stdout: one block per PR with its number,
title, author, body, commit messages, changed files, the diff of
`merge_commit^1..merge_commit` (clipped) and `ids_en_el_texto`.

The packet is data: it takes no vault path and never reads the vault. The
characters are shared per PR (max_chars / number of PRs), so one huge PR
never pushes another out. `ids_en_el_texto` is computed here, from the title,
body and commit messages, with trace_code.py's id pattern; the agent never
deduces ids.

Exit codes: 0 ok · 3 refused (unreadable estado.json, unknown PR number).
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from trace_code import REF  # noqa: E402  (one id pattern for the trace and the packet)

DEFAULT_MAX_CHARS = 12000
MAX_FILES = 60


class Refused(Exception):
    pass


def _git(repo: Path, *args: str) -> tuple[int, str]:
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout


def clip(text: str, limit: int, what: str) -> str:
    if len(text) <= limit:
        return text
    return text[:max(limit, 0)].rstrip() + f"\n[{what} recortado: {len(text)} caracteres, se muestran {max(limit, 0)}]"


def ids_en_el_texto(pr: dict, commit_messages: list[str]) -> list[str]:
    text = "\n".join([pr.get("title") or "", pr.get("body") or "", *commit_messages])
    return list(dict.fromkeys(REF.findall(text)))


def git_material(repo: Path, merge_commit: str | None) -> dict | None:
    """Commit messages, files and diff of the merge; None when the commit is gone."""
    if not merge_commit:
        return None
    rc, _ = _git(repo, "cat-file", "-e", f"{merge_commit}^{{commit}}")
    if rc != 0:
        return None
    rc, _ = _git(repo, "rev-parse", "--verify", "-q", f"{merge_commit}^1")
    base = f"{merge_commit}^1" if rc == 0 else "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # empty tree
    rng = f"{merge_commit}^1..{merge_commit}" if rc == 0 else merge_commit
    _, log = _git(repo, "log", "--format=%B%x1e", rng)
    messages = [m.strip() for m in log.split("\x1e") if m.strip()]
    _, numstat = _git(repo, "diff", "--numstat", base, merge_commit)
    files, binary = [], []
    for line in numstat.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            files.append(parts[2])
            if parts[0] == "-" and parts[1] == "-":
                binary.append(parts[2])
    _, diff = _git(repo, "diff", "--no-color", base, merge_commit)
    return {"messages": messages, "files": files, "binary": binary, "diff": diff}


def block(repo: Path, pr: dict, budget: int) -> str:
    mat = git_material(repo, pr.get("merge_commit"))
    messages = mat["messages"] if mat else []
    body = (pr.get("body") or "").strip()
    out = [f"## PR #{pr['number']}", f"título: {pr.get('title') or ''}", f"autor: {pr.get('author') or ''}",
           f"fusionado: {pr.get('merged_at') or ''}", "", "### Descripción",
           clip(body, budget // 4, "cuerpo") if body else "(sin descripción)", "", "### Mensajes de commit"]
    if mat is None:
        out += ["(commit no disponible: no se puede leer el diff ni los mensajes)"]
        files_txt = diff_txt = None
    else:
        out += [clip("\n\n".join(messages), budget // 4, "mensajes") or "(sin mensajes)"]
        shown = mat["files"][:MAX_FILES]
        files_txt = "\n".join(f"- {f}" + (" (binario)" if f in mat["binary"] else "") for f in shown)
        if len(mat["files"]) > MAX_FILES:
            files_txt += f"\n[+{len(mat['files']) - MAX_FILES} ficheros más]"
        diff_txt = mat["diff"]
    out += ["", "### Ficheros", files_txt or "(no disponible)"]
    ids = json.dumps(ids_en_el_texto(pr, messages), ensure_ascii=False)
    tail = ["", f"ids_en_el_texto: {ids}"]
    used = sum(len(x) + 1 for x in out) + sum(len(x) + 1 for x in tail)
    out += ["", "### Diff"]
    if diff_txt is None:
        out += ["(commit no disponible)"]
    elif not diff_txt.strip():
        out += ["(diff vacío)"]
    else:
        room = max(budget - used - 200, 0)
        if len(diff_txt) > room:
            out += [diff_txt[:room].rstrip(),
                    f"[diff recortado: {len(diff_txt)} caracteres, se muestran {room}]"]
        else:
            out += [diff_txt.rstrip()]
    return "\n".join(out + tail) + "\n"


def build(repo: Path, estado: Path, numbers: list[int], max_chars: int) -> str:
    try:
        prs = json.loads(estado.read_text(encoding="utf-8")).get("prs") or []
    except (OSError, ValueError, AttributeError) as e:
        raise Refused(f"no se puede leer {estado}: {e}")
    by_number = {p.get("number"): p for p in prs if isinstance(p, dict)}
    missing = [n for n in numbers if n not in by_number]
    if missing:
        raise Refused("PR que no están en estado.json: " + ", ".join(f"#{n}" for n in missing))
    budget = max(max_chars // max(len(numbers), 1), 1000)
    return "\n".join(block(repo, by_number[n], budget) for n in numbers)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--estado", required=True)
    ap.add_argument("--prs", required=True, help="comma-separated PR numbers")
    ap.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        numbers = [int(x) for x in a.prs.split(",") if x.strip()]
        sys.stdout.write(build(Path(a.repo), Path(a.estado), numbers, a.max_chars))
    except ValueError:
        print("--prs: números separados por comas", file=sys.stderr)
        return 3
    except Refused as e:
        print(str(e), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
