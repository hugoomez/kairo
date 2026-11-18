#!/usr/bin/env python3
"""Freeze the measurement plan of a study question (kairo:plan-study, last step).

    freeze_plan.py --vault V --question Q-XXXX --by <who>

The plan is the one the question's `linked_plan` names, or, when that is empty, the
only plan in the design's plan folder of the same project whose `question:` is this
question (then `linked_plan` is set to it). In order:

1. commit the plan and the question as they are (`git commit -- <those two paths>`;
   nothing else in the vault is staged or committed). If both are already committed
   unchanged, HEAD is the freeze commit;
2. write `frozen_at` (UTC, `YYYY-MM-DDTHH:MM:SSZ`), `frozen_commit` (that commit's sha)
   and `status: congelado` in the plan's frontmatter; nothing else in it changes;
3. create `Projects/<slug>/Avisos/registro.json` as `[]` if it does not exist. An
   existing registry is never opened: only its existence is checked. It is not
   committed (the notices are the researcher's);
4. `study_status.apply --to plan_congelado` on the question;
5. commit the plan and the question again.

So `frozen_commit` holds the plan whose Estimandos, Clasificadores and Intervalos the
gate (`study_gates.plan_frozen`) compares with the current file, and a units dataset
registered in a LATER commit passes the gate's «congelar antes de los datos».

Refused (exit 3, nothing written or committed): the vault is not a git repository;
the question is not `abierta`; no plan or several plans; the plan names another
question; the plan is already frozen; the plan's JSON blocks are invalid
(plan_blocks.read_plan). A git failure (e.g. a pre-commit hook that refuses) exits 1
with git's message; the hook is never skipped.

Prints JSON: {question, plan, frozen_commit, frozen_at, to, avisos_creado, aviso,
commits}. Exit codes: 0 ok · 3 refused · 1 error. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import designs as _designs  # noqa: E402
import study_status as ss  # noqa: E402
from notes import read_note  # noqa: E402
from plan_blocks import PlanError, read_plan  # noqa: E402
from study_gates import _blank, find_node  # noqa: E402
from verifications import InputError, _set_scalar, load, write_raw  # noqa: E402

TOOL = "kairo/freeze_plan@1.0.0"
FROM, TO = "abierta", "plan_congelado"
AVISO = ("sin avisos registrados todavía; añade los que correspondan antes de estimar "
         "(panel Avisos; la puerta de estimada exige que todos estén cerrados)")


class Refused(Exception):
    pass


class GitError(Exception):
    pass


def _git(top: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=top, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _git_ok(top: Path, *args: str) -> str:
    r = _git(top, *args)
    if r.returncode != 0:
        raise GitError(f"git {args[0]} falló: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


def _fm(path: Path) -> dict:
    parsed = read_note(path)
    return parsed[0] if parsed else {}


def _find_plan(proj: Path, design: dict, qid: str, linked: str) -> tuple[str, Path]:
    folder = proj / design["plan_folder"]
    if not _blank(linked):
        hits = [p for p in sorted(folder.glob(f"{linked}*.md"))
                if p.name == f"{linked}.md" or p.name.startswith(f"{linked} ")]
        if len(hits) != 1:
            raise Refused(f"linked_plan {linked}: no hay exactamente una nota en {design['plan_folder']}/ del proyecto")
        return linked, hits[0]
    mine = [p for p in sorted(folder.glob(f"{design['plan_prefix']}-*.md"))
            if str(_fm(p).get("question") or "").strip() == qid]
    if not mine:
        raise Refused(f"ningún plan de {design['plan_folder']}/ nombra {qid} en question: redáctalo antes de congelar")
    if len(mine) > 1:
        raise Refused(f"varios planes nombran {qid}: " + ", ".join(p.name for p in mine)
                      + "; enlaza el bueno en linked_plan")
    return str(_fm(mine[0]).get("id") or mine[0].stem.split(" ")[0]), mine[0]


def _set(path: Path, values: list[tuple[str, str]]) -> None:
    _, lines, nl, _, _, _ = load(str(path))
    for k, v in values:
        _set_scalar(lines, k, v, nl)
    write_raw(str(path), "".join(lines))


def _commit(top: Path, paths: list[str], msg: str) -> str | None:
    """Commit exactly `paths`; None when they are already committed unchanged."""
    _git_ok(top, "add", "--", *paths)
    if _git(top, "diff", "--cached", "--quiet", "--", *paths).returncode == 0:
        return None
    _git_ok(top, "commit", "-q", "-m", msg, "--", *paths)
    return _git_ok(top, "rev-parse", "HEAD")


def freeze(vault: Path, qid: str, by: str) -> dict:
    vault = Path(vault).resolve()
    if not by.strip():
        raise Refused("--by es obligatorio")
    design, q = ss._locate(vault, qid)
    qfm = _fm(q)
    status = str(qfm.get("status") or "").strip()
    if status != FROM:
        raise Refused(f"{qid} está en «{status}»: el plan solo se congela desde «{FROM}»")
    if (FROM, TO) not in set(design["transitions"]):
        raise Refused(f"el diseño de {qid} no tiene la transición {FROM} → {TO}")
    proj = q.parents[1]
    pid, plan = _find_plan(proj, design, qid, str(qfm.get("linked_plan") or "").strip())
    pfm = _fm(plan)
    named = str(pfm.get("question") or "").strip()
    if named != qid:
        raise Refused(f"el plan {pid} es el plan de {named or '(question vacío)'}, no de {qid}")
    if not _blank(pfm.get("frozen_at")) or not _blank(pfm.get("frozen_commit")):
        raise Refused(f"el plan {pid} ya está congelado (frozen_at/frozen_commit); un cambio va en un plan nuevo")
    try:
        read_plan(plan)
    except PlanError as e:
        raise Refused(f"plan {pid}: {e}; corrígelo antes de congelar") from None
    top_r = _git(proj, "rev-parse", "--show-toplevel")
    if top_r.returncode != 0:
        raise Refused("el vault no es un repositorio git: congelar exige un commit (git init y un primer commit)")
    top = Path(top_r.stdout.strip()).resolve()
    rel = [plan.resolve().relative_to(top).as_posix(), q.resolve().relative_to(top).as_posix()]

    if _blank(qfm.get("linked_plan")):
        _set(q, [("linked_plan", pid)])
    commits = []
    first = _commit(top, rel, f"Plan {pid} de {qid}: contenido que se congela")
    sha = first or _git_ok(top, "rev-parse", "HEAD")
    if first:
        commits.append(first)
    frozen_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _set(plan, [("frozen_at", frozen_at), ("frozen_commit", sha), ("status", "congelado")])

    reg = proj / "Avisos" / "registro.json"
    created = not reg.exists()
    if created:
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text("[]\n", encoding="utf-8", newline="\n")

    ss.apply(vault, qid, TO, by.strip(), f"plan {pid} congelado en el commit {sha[:12]}")
    second = _commit(top, rel, f"Plan {pid} congelado (frozen_commit {sha[:12]}); {qid} → {TO}")
    if second:
        commits.append(second)
    return {"tool": TOOL, "question": qid, "plan": pid, "frozen_commit": sha, "frozen_at": frozen_at,
            "to": TO, "avisos_creado": created, "aviso": AVISO if created else "", "commits": commits}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vault", required=True, type=Path)
    ap.add_argument("--question", required=True)
    ap.add_argument("--by", required=True)
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        out = freeze(a.vault, a.question, a.by)
    except (Refused, ss.Refused) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    except (GitError, InputError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
