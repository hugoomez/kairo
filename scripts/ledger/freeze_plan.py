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
   existing registry is never opened (only its existence is checked) and never
   committed;
4. `study_status.apply --to plan_congelado` on the question;
5. commit the plan and the question again, plus the registry if step 3 created it
   in this same run.

So `frozen_commit` holds the plan whose Estimandos, Clasificadores and Intervalos the
gate (`study_gates.plan_frozen`) compares with the current file, and a units dataset
registered in a LATER commit passes the gate's «congelar antes de los datos».

Resuming: if step 5 (or 4) failed, the plan already carries `frozen_at` and
`frozen_commit` and the files are left changed. Running the script again finishes
the freeze: when `frozen_commit` is a commit whose tree holds this plan and the
question is `abierta`, or `plan_congelado` with the plan or question uncommitted, it
does steps 3-5 with that same `frozen_commit` (`reanudado: true`). A plan frozen and
fully committed is refused as «ya está congelado».

Refused (exit 3): the vault is not a git repository; the question is not `abierta`
(and there is no freeze to finish); no plan or several plans; the plan names another
question; the plan is already frozen; the plan's JSON blocks are invalid
(plan_blocks.read_plan). A refusal writes and commits nothing. A git failure (e.g.
a pre-commit hook that refuses) exits 1 with git's message, and the hook is never
skipped. If it was the first commit, at most `linked_plan` was written; if it was
the second, run the script again once the cause is fixed.

Prints JSON: {question, plan, frozen_commit, frozen_at, to, reanudado, avisos_creado,
aviso, commits}. Exit codes: 0 ok · 3 refused · 1 error. Standard library only.

Amending a frozen plan
----------------------

    freeze_plan.py amend --vault V --plan M-XXXX --by <who> --motivo "<why>" \\
        --datos-vistos si|no --from <file>
    freeze_plan.py amend --vault V --plan M-XXXX --reanudar

A frozen plan is never edited: a change is appended to `## Enmiendas` (format in
plan_blocks.py) and the plan's own blocks stay as frozen. `--from` is either a JSON change
({"estimands": [...], "retire_estimands": [...], "classifiers": [...], "retire_classifiers":
[...], "intervals": {...}}) or a copy of the plan (frontmatter optional) with the blocks as
they should now be; then the change is computed against the current effective blocks
(plan_blocks.diff_blocks). `--datos-vistos` is required and has no default: `si` when the
units data (or any result) had been seen. `--motivo` is one line. Agents may run it; `--by`
names who decided.

In order: (1) appends entry En (fecha UTC, por, datos_vistos, motivo, `commit: pendiente`,
the change) and commits the plan alone; (2) writes that commit's sha into the entry's
`commit` line and commits the plan alone again. The gate (`study_gates.plan_frozen`)
compares each entry, without its `commit` line, with the commit it names. If (2) fails,
`--reanudar` finishes it (also (1) when the entry was never committed).

Refused (exit 3): a design whose plans are not frozen (`plan_frozen` not in its gate),
a plan not frozen yet (edit it directly), the question already terminal, uncommitted
changes in the plan, an unfinished amendment (use --reanudar), a change that changes
nothing, or an effective plan that would be invalid. Prints JSON: {plan, question,
enmienda, fecha, datos_vistos, commit, cambios, commits}.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import designs as _designs  # noqa: E402
import plan_blocks as pb  # noqa: E402
import study_status as ss  # noqa: E402
from notes import read_note, split_note  # noqa: E402
from plan_blocks import PlanError, read_plan  # noqa: E402
from study_gates import _blank, find_plan  # noqa: E402
from verifications import InputError, _set_scalar, load, write_raw  # noqa: E402

TOOL = "kairo/freeze_plan@1.1.0"
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


def _resumable(top: Path, rel: list[str], pfm: dict, status: str) -> bool:
    """A freeze whose second half did not finish: both frozen fields set, `frozen_commit`
    is a commit whose tree holds this plan, and the question is still `abierta` or its
    `plan_congelado` change is not committed yet."""
    sha = str(pfm.get("frozen_commit") or "").strip()
    if _blank(pfm.get("frozen_at")) or _blank(sha) or status not in (FROM, TO):
        return False
    if _git(top, "cat-file", "-e", f"{sha}:{rel[0]}").returncode != 0:
        return False
    return status == FROM or bool(_git_ok(top, "status", "--porcelain", "--", *rel))


def freeze(vault: Path, qid: str, by: str) -> dict:
    vault = Path(vault).resolve()
    if not by.strip():
        raise Refused("--by es obligatorio")
    design, q = ss._locate(vault, qid)
    qfm = _fm(q)
    status = str(qfm.get("status") or "").strip()
    if (FROM, TO) not in set(design["transitions"]):
        raise Refused(f"el diseño de {qid} no tiene la transición {FROM} → {TO}")
    proj = q.parents[1]
    pid, plan = _find_plan(proj, design, qid, str(qfm.get("linked_plan") or "").strip())
    pfm = _fm(plan)
    named = str(pfm.get("question") or "").strip()
    if named != qid:
        raise Refused(f"el plan {pid} es el plan de {named or '(question vacío)'}, no de {qid}")
    top_r = _git(proj, "rev-parse", "--show-toplevel")
    if top_r.returncode != 0:
        raise Refused("el vault no es un repositorio git: congelar exige un commit (git init y un primer commit)")
    top = Path(top_r.stdout.strip()).resolve()
    rel = [plan.resolve().relative_to(top).as_posix(), q.resolve().relative_to(top).as_posix()]
    commits: list[str] = []

    resumed = not _blank(pfm.get("frozen_at")) or not _blank(pfm.get("frozen_commit"))
    if resumed:
        if not _resumable(top, rel, pfm, status):
            raise Refused(f"el plan {pid} ya está congelado (frozen_at/frozen_commit); un cambio va en un plan nuevo")
        sha, frozen_at = str(pfm["frozen_commit"]).strip(), str(pfm["frozen_at"]).strip()
    else:
        if status != FROM:
            raise Refused(f"{qid} está en «{status}»: el plan solo se congela desde «{FROM}»")
        try:
            read_plan(plan)
        except PlanError as e:
            raise Refused(f"plan {pid}: {e}; corrígelo antes de congelar") from None
        if pb.amendment_contents(plan.read_text(encoding="utf-8-sig")):
            raise Refused(f"plan {pid}: un plan sin congelar no tiene ## Enmiendas; edita sus bloques "
                          "y quita esa sección antes de congelar")
        if _blank(qfm.get("linked_plan")):
            _set(q, [("linked_plan", pid)])
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

    if status == FROM:
        ss.apply(vault, qid, TO, by.strip(), f"plan {pid} congelado en el commit {sha[:12]}")
    # Only a registry created by THIS run is committed; an existing one never is.
    paths = rel + ([reg.resolve().relative_to(top).as_posix()] if created else [])
    second = _commit(top, paths, f"Plan {pid} congelado (frozen_commit {sha[:12]}); {qid} → {TO}")
    if second:
        commits.append(second)
    return {"tool": TOOL, "question": qid, "plan": pid, "frozen_commit": sha, "frozen_at": frozen_at,
            "to": TO, "reanudado": resumed, "avisos_creado": created, "aviso": AVISO if created else "",
            "commits": commits}


# ---- amend -------------------------------------------------------------------

AMEND_INTRO = ("Registro append-only de los cambios del plan después del congelado (lo escribe "
               "freeze_plan.py amend; no se edita a mano). Cada estimando que una enmienda cambia o "
               "añade es una desviación declarada de esa enmienda.")


def _read_raw(path: Path) -> str:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return fh.read()


def _write_raw_text(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _body(text: str) -> str:
    parts = split_note(text)
    return parts[1] if parts else text


def _entry(eid: str, fecha: str, by: str, dv: str, motivo: str, delta: dict, nl: str) -> str:
    lines = [f"### {eid}", "", f"- fecha: {fecha}", f"- por: {by}", f"- datos_vistos: {dv}",
             f"- motivo: {motivo}", f"- commit: {pb.PENDING}", "", "```json",
             json.dumps(delta, ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines).replace("\n", nl)


def _append_entry(text: str, entry: str, nl: str) -> str:
    """`entry` at the end of `## Enmiendas` (created at the end of the note if absent)."""
    m = re.search(rf"^##[ \t]+{pb.AMENDMENTS}[ \t]*\r?$", text, re.M)
    if m is None:
        return text.rstrip("\r\n") + nl + nl + f"## {pb.AMENDMENTS}" + nl + nl + AMEND_INTRO + nl + nl + entry
    nxt = re.search(r"^##[ \t]", text[m.end():], re.M)
    end = m.end() + nxt.start() if nxt else len(text)
    head, tail = text[:end].rstrip("\r\n"), text[end:]
    return head + nl + nl + entry + (nl + tail if tail else "")


def _set_entry_commit(text: str, eid: str, sha: str) -> str:
    m = re.search(rf"^###[ \t]+{re.escape(eid)}[ \t]*\r?$", text, re.M)
    if m is None:
        raise Refused(f"la entrada {eid} no está en el plan")
    line = re.compile(rf"^(-[ \t]+commit:[ \t]*){pb.PENDING}([ \t]*\r?)$", re.M)
    hit = line.search(text, m.end())
    nxt = re.search(r"^###?[ \t]", text[m.end():], re.M)
    if hit is None or (nxt and hit.start() > m.end() + nxt.start()):
        raise Refused(f"la entrada {eid} no tiene «commit: {pb.PENDING}»")
    return text[:hit.start()] + hit.group(1) + sha + hit.group(2) + text[hit.end():]


def _delta_from(source: Path, eff: dict) -> dict:
    try:
        raw = source.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise Refused(f"--from {source}: no se pudo leer ({e})") from None
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if isinstance(data, dict):
        return data
    try:
        new = pb.blocks_of_body(_body(raw))
    except PlanError as e:
        raise Refused(f"--from {source}: no es un cambio JSON ni un plan con bloques válidos ({e})") from None
    return pb.diff_blocks(eff, new)


def _plan_and_question(vault: Path, pid: str) -> tuple[dict, Path, dict, str]:
    design = _designs.design_for_plan_id(_designs.load(), pid)
    path, why = find_plan(vault, pid)
    if path is None or design is None:
        raise Refused(why or f"{pid}: plan desconocido")
    if "plan_frozen" not in design["gate"]:
        raise Refused(f"{pid}: los planes de su diseño no se congelan; se corrigen editándolos (lo que miden "
                      "ya lleva su etiqueta)")
    pfm = _fm(path)
    if _blank(pfm.get("frozen_at")) or _blank(pfm.get("frozen_commit")):
        raise Refused(f"{pid} no está congelado: un plan en borrador se edita directamente")
    qid = str(pfm.get("question") or "").strip()
    _, q = ss._locate(vault, qid)
    status = str(_fm(q).get("status") or "").strip()
    if status in design["terminal"]:
        raise Refused(f"{qid} ya está «{status}»: su plan no se enmienda")
    return design, path, pfm, qid


def _first_commit_with(top: Path, rel: str, eid: str) -> str | None:
    for c in _git_ok(top, "log", "--reverse", "--format=%H", "--", rel).split():
        shown = _git(top, "show", f"{c}:{rel}")
        if shown.returncode == 0 and eid in pb.amendment_contents(shown.stdout):
            return c
    return None


def _finish(top: Path, rel: str, path: Path, pid: str, eid: str, dv: str, commits: list[str],
            resume: bool = False) -> str:
    """Steps (1) if still needed and (2): the entry committed, then its sha written and committed."""
    c1 = _first_commit_with(top, rel, eid) if resume else None
    if c1 is None:
        c1 = _commit(top, [rel], f"Plan {pid}: enmienda {eid} (datos_vistos: {dv})")
        if c1 is None:
            raise GitError(f"la enmienda {eid} no se pudo registrar en un commit")
        commits.append(c1)
    text = _read_raw(path)
    try:
        text = _set_entry_commit(text, eid, c1)
    except Refused:
        if not resume:
            raise
        named = next((a["commit"] for a in pb.read_amendments(_body(text)) if a["id"] == eid), None)
        if named is None or not c1.startswith(named):
            raise Refused(f"la entrada {eid} nombra el commit {named}, pero se registró en {c1[:12]}; "
                          "devuélvela a «commit: pendiente» o a ese commit") from None
    _write_raw_text(path, text)
    c2 = _commit(top, [rel], f"Plan {pid}: enmienda {eid} registrada en {c1[:12]}")
    if c2:
        commits.append(c2)
    return c1


def amend(vault: Path, pid: str, by: str, motivo: str, datos_vistos: str, source: Path | None,
          resume: bool = False) -> dict:
    vault = Path(vault).resolve()
    design, path, pfm, qid = _plan_and_question(vault, pid)
    top_r = _git(path.parent, "rev-parse", "--show-toplevel")
    if top_r.returncode != 0:
        raise Refused("el vault no es un repositorio git")
    top = Path(top_r.stdout.strip()).resolve()
    rel = path.resolve().relative_to(top).as_posix()
    text = _read_raw(path)
    try:
        entries = pb.read_amendments(_body(text))
    except PlanError as e:
        raise Refused(f"plan {pid}: {e}") from None
    pending = [a for a in entries if a["commit"] == pb.PENDING]
    commits: list[str] = []
    dirty = bool(_git_ok(top, "status", "--porcelain", "--", rel))
    if resume:
        # unfinished: an entry still `pendiente`, or its sha written but not committed
        if not entries or (not pending and not dirty):
            raise Refused(f"{pid}: no hay ninguna enmienda por terminar")
        a = pending[-1] if pending else entries[-1]
        sha = _finish(top, rel, path, pid, a["id"], a["datos_vistos"], commits, resume=True)
        return {"tool": TOOL, "plan": pid, "question": qid, "enmienda": a["id"], "fecha": a["fecha"],
                "datos_vistos": a["datos_vistos"], "commit": sha, "reanudado": True, "commits": commits}
    if pending:
        raise Refused(f"la enmienda {pending[-1]['id']} de {pid} no terminó: corre "
                      f"freeze_plan.py amend --vault <vault> --plan {pid} --reanudar")
    by = " ".join((by or "").split())
    motivo = " ".join((motivo or "").split())
    if not by:
        raise Refused("--by es obligatorio")
    if len(motivo) < 5 or "```" in motivo:
        raise Refused("--motivo es obligatorio: una línea (al menos 5 caracteres, sin ```)")
    if datos_vistos not in ("si", "no"):
        raise Refused("--datos-vistos debe ser si o no (sin valor por defecto)")
    if source is None:
        raise Refused("--from es obligatorio: un cambio JSON o una copia del plan con los bloques nuevos")
    if dirty:
        raise Refused(f"el plan {pid} tiene cambios sin commit: un plan congelado no se edita; deshazlos "
                      "(git restore) y enmiéndalo con este comando (si una enmienda no terminó, "
                      f"freeze_plan.py amend --vault <vault> --plan {pid} --reanudar)")
    try:
        eff = pb.read_effective_text(text)
    except PlanError as e:
        raise Refused(f"plan {pid}: {e}") from None
    delta = _delta_from(source, eff)
    eid = f"E{len(entries) + 1}"
    nl = "\r\n" if "\r\n" in text else "\n"
    fecha = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_text = _append_entry(text, _entry(eid, fecha, by, datos_vistos, motivo, delta, nl), nl)
    try:
        if not delta:
            raise PlanError("el cambio está vacío")
        new_eff = pb.read_effective_text(new_text)
    except PlanError as e:
        raise Refused(f"la enmienda no es válida: {e}") from None
    if all(new_eff[k] == eff[k] for k in ("estimands", "classifiers", "intervals")):
        raise Refused("la enmienda no cambia nada del plan efectivo")
    _write_raw_text(path, new_text)
    sha = _finish(top, rel, path, pid, eid, datos_vistos, commits)
    return {"tool": TOOL, "plan": pid, "question": qid, "enmienda": eid, "fecha": fecha,
            "datos_vistos": datos_vistos, "commit": sha, "cambios": new_eff["summary"][-1],
            "reanudado": False, "commits": commits}


def _amend_main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="freeze_plan.py amend", description="Enmienda un plan congelado.")
    ap.add_argument("--vault", required=True, type=Path)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--reanudar", action="store_true")
    ap.add_argument("--by")
    ap.add_argument("--motivo")
    ap.add_argument("--datos-vistos", choices=["si", "no"])
    ap.add_argument("--from", dest="source", type=Path)
    a = ap.parse_args(argv)
    if not a.reanudar:
        miss = [f for f, v in (("--by", a.by), ("--motivo", a.motivo), ("--datos-vistos", a.datos_vistos),
                               ("--from", a.source)) if v is None]
        if miss:
            ap.error("faltan " + ", ".join(miss))
    try:
        out = amend(a.vault, a.plan, a.by or "", a.motivo or "", a.datos_vistos or "", a.source, a.reanudar)
    except (Refused, ss.Refused) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    except (GitError, InputError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    if argv[:1] == ["amend"]:
        return _amend_main(argv[1:])
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vault", required=True, type=Path)
    ap.add_argument("--question", required=True)
    ap.add_argument("--by", required=True)
    a = ap.parse_args(argv)
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
