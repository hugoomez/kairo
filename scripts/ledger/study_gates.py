"""Puertas de comprobación de los diseños de estudio.

Cada una es `check(vault, node_path, fm) -> list[str]`: lo que falta (vacía = pasa).
`config/designs.toml` nombra, por diseño, cuáles debe pasar un nodo antes de llegar a su
estado `publishable`; `study_status.gate` las ejecuta. Todas fallan cerradas: si algo no se
puede comprobar (sin git, git o un script fallan o tardan más de TIMEOUT s), falta.

- `plan_frozen` (como `evidence_gate.frozen_check` para los preregistros):
  - el plan de `linked_plan` existe en la carpeta de planes del diseño, en el mismo proyecto,
    nombra su pregunta (`question:` no vacío, igual al id del nodo) y tiene `frozen_at` y
    `frozen_commit`;
  - `frozen_commit` existe en el repositorio git del vault y es antecesor de HEAD;
  - la versión del plan en `frozen_commit` (`git show`) tiene los mismos Estimandos,
    Clasificadores e Intervalos que el fichero actual («el plan cambió después de
    congelarlo: <sección>»);
  - congelar antes de los datos: el primer commit en que `Datos/registro.json` del proyecto
    contiene el dataset `units_dataset` del nodo no es `frozen_commit` ni un antecesor suyo
    («los datos se registraron antes de congelar el plan»). Sin `units_dataset`, falta
    «sin units_dataset».
- `estimates_reproducible`: volver a correr `scripts/analysis/estimate.py --plan <linked_plan>
  --units <units_dataset>` da exactamente el JSON del bloque ```estimate-json de
  `## Resultado`. El nombre del dataset de unidades se guarda en el frontmatter del nodo
  (`units_dataset`), porque la salida de estimate.py no lo lleva.
- `classifiers_validated`: cada clasificador del plan tiene
  `Datos/anotacion/<sample>/validacion-<name>.json` (de `agreement.py validate`) con sus
  `inputs`; los ficheros gold y pred conservan su sha256; volver a correr `agreement.py
  validate` con esas entradas da exactamente las mismas cifras; y `precision_lo >=
  min_precision_lo`, `recall_lo >= min_recall_lo`.
- `verifier_no_errors`: la verificación independiente más reciente (scope note) es
  `no_errors_found` y se hizo sobre el paquete que `verifier_packet.py --question` construye
  ahora: su sha256 es el `- paquete sha256:` de la última entrada `(note)` de
  `## Verificación independiente`, que escribe `verifications.py append --report …
  --packet-sha256 …` (el mismo registro que usan los lemas, `claim_gate.recorded_packet_sha`).
- `avisos_cerrados`: cada aviso de `Avisos/registro.json` está `respondido`, `resuelto` o
  `sin_respuesta`, o `enviado` con `embargo_hasta` (fecha ISO) anterior a hoy. Sin registro,
  falla con «sin registro de avisos»; `[]` pasa. Solo se informa de cuántos avisos y por qué,
  nunca de a quién.

Ningún mensaje lleva filas ni valores de celdas de los ficheros de datos. Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import designs as _designs  # noqa: E402
import verifications as vf  # noqa: E402
from notes import read_note, section  # noqa: E402
from plan_blocks import PlanError, read_plan, read_plan_text  # noqa: E402

ANALYSIS = HERE.parent / "analysis"
ESTIMATE = ANALYSIS / "estimate.py"
AGREEMENT = ANALYSIS / "agreement.py"
TIMEOUT = 600
_ID = re.compile(r"^[A-Z]+-\d{4}$")
_EST_BLOCK = re.compile(r"```estimate-json[ \t]*\n(.*?)\n[ \t]*```", re.S)
PLAN_PARTS = (("estimands", "Estimandos"), ("classifiers", "Clasificadores"), ("intervals", "Intervalos"))
CLOSED = {"respondido", "resuelto", "sin_respuesta"}


class _Fail(Exception):
    """A step that cannot be checked: its message goes into `missing`."""


def _blank(v) -> bool:
    s = str(v or "").strip()
    return not s or (s.startswith("<") and s.endswith(">"))


def _run(cmd: list[str], what: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=TIMEOUT,
                              env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except subprocess.TimeoutExpired:
        raise _Fail(f"{what}: se agotó el tiempo ({TIMEOUT} s); vuelve a intentarlo") from None
    except OSError as e:
        raise _Fail(f"{what}: no se pudo ejecutar ({e.strerror or e})") from None


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return _run(["git", *args], "git", cwd)


def find_node(vault: Path, nid: str, design: dict, plan: bool = False) -> Path | None:
    """`Projects/*/<folder>/<nid>.md` (or `<nid> <título>.md`) of a design's node or plan."""
    folder = design["plan_folder" if plan else "folder"]
    hits = [p for p in sorted(Path(vault).glob(f"Projects/*/{folder}/{nid}*.md"))
            if p.name == f"{nid}.md" or p.name.startswith(f"{nid} ")]
    return hits[0] if len(hits) == 1 else None


def _design(fm: dict) -> dict | None:
    return _designs.design_for_id(_designs.load(), str(fm.get("id", "")))


def _plan_path(node_path: Path, fm: dict) -> tuple[Path | None, str, list[str]]:
    """The linked plan in the node's own project, or what is wrong."""
    pid = str(fm.get("linked_plan") or "").strip()
    if _blank(pid):
        return None, "", ["linked_plan vacío: congela un plan y enlázalo en la pregunta"]
    design = _design(fm)
    if design is None:
        return None, pid, [f"{fm.get('id')}: ningún diseño de config/designs.toml usa ese prefijo"]
    if not _ID.match(pid) or not pid.startswith(design["plan_prefix"] + "-"):
        return None, pid, [f"linked_plan {pid!r} no es un id {design['plan_prefix']}-XXXX"]
    proj = Path(node_path).parents[1]
    folder = proj / design["plan_folder"]
    hits = [p for p in sorted(folder.glob(f"{pid}*.md")) if p.name == f"{pid}.md" or p.name.startswith(f"{pid} ")]
    if len(hits) != 1:
        what = "no existe" if not hits else "hay varias notas"
        return None, pid, [f"plan {pid}: {what} en {design['plan_folder']}/ del proyecto"]
    return hits[0], pid, []


# ---- plan_frozen -----------------------------------------------------------

def _first_registration(top: Path, reg_rel: str, name: str) -> str | None:
    log = _git(["log", "--reverse", "--format=%H", "--", reg_rel], top)
    if log.returncode != 0:
        raise _Fail(f"git log de {reg_rel} falló: no se puede comprobar cuándo se registraron los datos")
    for c in log.stdout.split():
        shown = _git(["show", f"{c}:{reg_rel}"], top)
        if shown.returncode != 0:
            continue
        try:
            reg = json.loads(shown.stdout)
        except ValueError:
            continue
        if isinstance(reg, list) and any(isinstance(d, dict) and d.get("name") == name for d in reg):
            return c
    return None


def _frozen_in_git(plan_path: Path, pid: str, sha: str, node_path: Path, fm: dict) -> list[str]:
    top_r = _git(["rev-parse", "--show-toplevel"], plan_path.parent)
    if top_r.returncode != 0:
        return [f"plan {pid}: sin repositorio git en el vault; el plan no está congelado en git "
                "(haz commit del vault y anota ese commit en frozen_commit)"]
    top = Path(top_r.stdout.strip()).resolve()
    if _git(["cat-file", "-e", f"{sha}^{{commit}}"], top).returncode != 0:
        return [f"plan {pid}: frozen_commit {sha} no existe en el repositorio del vault"]
    anc = _git(["merge-base", "--is-ancestor", sha, "HEAD"], top)
    if anc.returncode == 1:
        return [f"plan {pid}: frozen_commit {sha} no es antecesor de HEAD"]
    if anc.returncode != 0:
        return [f"plan {pid}: git merge-base falló; no se puede comprobar frozen_commit"]
    out: list[str] = []
    rel = plan_path.resolve().relative_to(top).as_posix()
    shown = _git(["show", f"{sha}:{rel}"], top)
    if shown.returncode != 0:
        out.append(f"plan {pid}: no está en frozen_commit {sha[:10]}; congélalo con un commit que lo contenga")
    else:
        try:
            frozen, current = read_plan_text(shown.stdout), read_plan(plan_path)
            out += [f"el plan cambió después de congelarlo: {label}"
                    for key, label in PLAN_PARTS if frozen[key] != current[key]]
        except PlanError as e:
            out.append(f"plan {pid} ilegible (congelado o actual): {e}")
    units = str(fm.get("units_dataset") or "").strip()
    if _blank(units):
        return out + ["sin units_dataset"]
    reg = Path(node_path).parents[1] / "Datos" / "registro.json"
    try:
        reg_rel = reg.resolve().relative_to(top).as_posix()
    except ValueError:
        return out + ["Datos/registro.json está fuera del repositorio del vault"]
    first = _first_registration(top, reg_rel, units)
    if first is None:
        return out + [f"el dataset {units!r} no está registrado en ningún commit del vault: "
                      "haz commit de Datos/registro.json (después de congelar el plan)"]
    before = _git(["merge-base", "--is-ancestor", first, sha], top)
    if before.returncode == 0:
        out.append("los datos se registraron antes de congelar el plan")
    elif before.returncode != 1:
        out.append("git merge-base falló; no se puede comprobar que el plan se congeló antes que los datos")
    return out


def plan_frozen(vault: Path, node_path: Path, fm: dict) -> list[str]:
    path, pid, miss = _plan_path(node_path, fm)
    if path is None:
        return miss
    parsed = read_note(path)
    pfm = parsed[0] if parsed else {}
    out = [f"plan {pid}: falta {k}; congela el plan antes de recolectar"
           for k in ("frozen_at", "frozen_commit") if _blank(pfm.get(k))]
    q = str(pfm.get("question") or "").strip()
    if _blank(q):
        out.append(f"plan {pid}: question vacío; el plan debe nombrar su pregunta ({fm.get('id')})")
    elif q != str(fm.get("id")):
        out.append(f"plan {pid}: es el plan de {q}, no de {fm.get('id')}")
    sha = str(pfm.get("frozen_commit") or "").strip()
    if _blank(sha):
        return out
    try:
        out += _frozen_in_git(path, pid, sha, Path(node_path), fm)
    except _Fail as e:
        out.append(f"plan {pid}: {e}")
    return out


# ---- estimates_reproducible ------------------------------------------------

def _stored_estimate(node_path: Path) -> tuple[dict | None, str | None]:
    parsed = read_note(Path(node_path))
    body = parsed[1] if parsed else ""
    m = _EST_BLOCK.search(section(body, "Resultado"))
    if not m:
        return None, "## Resultado no tiene bloque ```estimate-json: corre estimate.py y guarda su salida"
    try:
        return json.loads(m.group(1)), None
    except json.JSONDecodeError as e:
        return None, f"el bloque estimate-json de ## Resultado no es JSON válido ({e.msg}, línea {e.lineno})"


def _diff(stored: dict, fresh: dict) -> list[str]:
    out = []
    for k in ("plan", "units_sha256"):
        if stored.get(k) != fresh.get(k):
            out.append(k)
    s = {e.get("id"): e for e in stored.get("estimates") or [] if isinstance(e, dict)}
    f = {e.get("id"): e for e in fresh.get("estimates") or [] if isinstance(e, dict)}
    for i in sorted(set(s) | set(f), key=str):
        if s.get(i) != f.get(i):
            out.append(f"estimando {i}")
    if not out and stored != fresh:
        out.append("estructura")
    return out


def estimates_reproducible(vault: Path, node_path: Path, fm: dict) -> list[str]:
    path, pid, miss = _plan_path(node_path, fm)
    if path is None:
        return miss
    units = str(fm.get("units_dataset") or "").strip()
    if _blank(units):
        return ["units_dataset vacío: anota en la pregunta el dataset de unidades con el que se estimó"]
    stored, err = _stored_estimate(node_path)
    if err:
        return [err]
    try:
        r = _run([sys.executable, str(ESTIMATE), "--vault", str(vault), "--plan", pid, "--units", units],
                 "estimate.py")
    except _Fail as e:
        return [str(e)]
    try:
        fresh = json.loads(r.stdout)
    except json.JSONDecodeError:
        return [f"estimate.py falló (exit {r.returncode}); revisa el plan y el dataset {units!r}"]
    if r.returncode != 0 or "error" in fresh:
        why = str(fresh.get("error", ""))
        exp = f" (huella esperada {fresh['esperada']})" if fresh.get("esperada") else ""
        return [f"estimate.py se niega: {why}{exp}"]
    diff = _diff(stored, fresh)
    if diff:
        return [f"el bloque estimate-json de ## Resultado no coincide con volver a correr estimate.py "
                f"({', '.join(diff)}); no edites cifras a mano: vuelve a correr estimate.py y guarda su salida"]
    return []


# ---- classifiers_validated -------------------------------------------------

def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _input_file(vault: Path, proj: Path, raw: str) -> Path | None:
    p = Path(raw)
    cands = [p] if p.is_absolute() else [Path(vault) / p, proj / p]
    return next((c for c in cands if c.is_file()), None)


def _recompute(vault: Path, proj: Path, name: str, rel: str, v: dict) -> list[str]:
    """[] when the inputs are unchanged and agreement.py gives the same numbers."""
    inp = v.get("inputs")
    if not isinstance(inp, dict):
        return [f"clasificador {name}: validación sin entradas registradas: vuelve a generarla con agreement.py"]
    files = {}
    for role in ("gold", "pred"):
        meta = inp.get(role) if isinstance(inp.get(role), dict) else {}
        f = _input_file(vault, proj, str(meta.get("path") or ""))
        if f is None:
            return [f"clasificador {name}: no se encuentra el fichero {role} de {rel}; vuelve a validar con agreement.py"]
        if hashlib.sha256(f.read_bytes()).hexdigest() != meta.get("sha256"):
            return [f"clasificador {name}: el fichero {role} ({meta.get('path')}) cambió después de validar; "
                    "vuelve a validar con agreement.py"]
        files[role] = f
    cmd = [sys.executable, str(AGREEMENT), "validate", "--gold", str(files["gold"]), "--pred", str(files["pred"]),
           "--label", str(inp.get("label", "")), "--positive", str(inp.get("positive", "")),
           "--ci", str(inp.get("ci", 0.95)), "--vault", str(vault)]
    try:
        r = _run(cmd, "agreement.py")
    except _Fail as e:
        return [f"clasificador {name}: {e}"]
    try:
        fresh = json.loads(r.stdout)
    except json.JSONDecodeError:
        fresh = {}
    if r.returncode != 0 or "error" in fresh:
        return [f"clasificador {name}: agreement.py se niega ({fresh.get('error', f'exit {r.returncode}')})"]
    strip = lambda d: {k: x for k, x in d.items() if k != "inputs"}  # noqa: E731
    if strip(fresh) != strip(v):
        return [f"clasificador {name}: {rel} no coincide con volver a correr agreement.py; "
                "no edites cifras a mano: vuelve a generarlo"]
    return []


def classifiers_validated(vault: Path, node_path: Path, fm: dict) -> list[str]:
    path, pid, miss = _plan_path(node_path, fm)
    if path is None:
        return miss
    try:
        plan = read_plan(path)
    except PlanError as e:
        return [f"plan {pid} inválido: {e}"]
    proj = Path(node_path).parents[1]
    out = []
    for c in plan["classifiers"]:
        name, sample = c["name"], c["sample"]
        rel = f"Datos/anotacion/{sample}/validacion-{name}.json"
        try:
            v = json.loads((proj / rel).read_text(encoding="utf-8"))
        except FileNotFoundError:
            out.append(f"clasificador {name}: falta {rel}; valídalo con agreement.py validate")
            continue
        except (OSError, ValueError):
            out.append(f"clasificador {name}: {rel} ilegible; vuelve a generarlo con agreement.py validate")
            continue
        v = v if isinstance(v, dict) else {}
        problems = _recompute(vault, proj, name, rel, v)
        if problems:
            out += problems
            continue
        for key, floor in (("precision_lo", c["min_precision_lo"]), ("recall_lo", c["min_recall_lo"])):
            got = _num(v.get(key))
            if got is None:
                out.append(f"clasificador {name}: {rel} no tiene {key} numérico")
            elif got < floor:
                out.append(f"clasificador {name}: {key} {got:.3f} < {floor} exigido por el plan")
    return out


# ---- verifier_no_errors ----------------------------------------------------

def verifier_no_errors(vault: Path, node_path: Path, fm: dict) -> list[str]:
    from claim_gate import recorded_packet_sha  # noqa: PLC0415
    from verifier_packet import build_question  # noqa: PLC0415

    try:
        last = vf.latest(vf.load(str(node_path))[5], "note")
    except vf.InputError as e:
        return [f"verificación ilegible: {e}"]
    if not last or last.get("verdict") != "no_errors_found":
        return [f"verificación: {last.get('verdict') if last else 'nunca verificada'}; "
                "pasa el verificador independiente con verifier_packet.py --question"]
    try:
        packet, _ = build_question(str(vault), str(fm.get("id", "")))
    except ValueError as e:
        return [f"verificación: no se puede construir el paquete --question ({e})"]
    now = hashlib.sha256(packet.encode("utf-8")).hexdigest()
    recorded = recorded_packet_sha(Path(node_path))
    if recorded is None:
        return ["verificación sin sha256 de paquete registrado: vuelve a verificar y regístrala con "
                "verifications.py append --report … --packet-sha256 …"]
    if recorded != now:
        return ["la última verificación fue sobre otro paquete (pregunta, plan o resultado cambiaron): "
                "vuelve a verificar con verifier_packet.py --question"]
    return []


# ---- avisos_cerrados -------------------------------------------------------

def _iso(v) -> date | None:
    try:
        return date.fromisoformat(str(v or "").strip()[:10])
    except ValueError:
        return None


def avisos_cerrados(vault: Path, node_path: Path, fm: dict) -> list[str]:
    reg = Path(node_path).parents[1] / "Avisos" / "registro.json"
    if not reg.is_file():
        return ["sin registro de avisos"]
    try:
        data = json.loads(reg.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["Avisos/registro.json ilegible: ábrelo desde el panel Avisos y corrígelo"]
    if not isinstance(data, list):
        return ["Avisos/registro.json no es una lista de avisos"]
    today = date.today()
    counts = {"pendiente": 0, "sin_fecha": 0, "embargo": 0, "desconocido": 0}
    for a in data:
        estado = a.get("estado") if isinstance(a, dict) else None
        if estado in CLOSED:
            continue
        if estado == "pendiente":
            counts["pendiente"] += 1
        elif estado == "enviado":
            until = _iso(a.get("embargo_hasta"))
            if until is None:
                counts["sin_fecha"] += 1
            elif until >= today:
                counts["embargo"] += 1
        else:
            counts["desconocido"] += 1
    parts = [txt for key, txt in (
        ("pendiente", f"{counts['pendiente']} pendiente(s)"),
        ("sin_fecha", f"{counts['sin_fecha']} enviado(s) sin fecha de embargo"),
        ("embargo", f"{counts['embargo']} enviado(s) con el embargo sin vencer"),
        ("desconocido", f"{counts['desconocido']} con estado desconocido"),
    ) if counts[key]]
    if not parts:
        return []
    return [f"avisos sin cerrar en Avisos/registro.json: {', '.join(parts)}; "
            "envíalos, espera al fin del embargo o ciérralos"]


GATES = {
    "plan_frozen": plan_frozen,
    "estimates_reproducible": estimates_reproducible,
    "classifiers_validated": classifiers_validated,
    "verifier_no_errors": verifier_no_errors,
    "avisos_cerrados": avisos_cerrados,
}
