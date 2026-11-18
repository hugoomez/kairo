"""Puertas de comprobación de los diseños de estudio.

Cada una es `check(vault, node_path, fm) -> list[str]`: lo que falta (vacía = pasa).
`config/designs.toml` nombra, por diseño, cuáles debe pasar un nodo antes de llegar a su
estado `publishable`; `study_status.gate` las ejecuta.

- `plan_frozen`: el plan de `linked_plan` existe en la carpeta de planes del diseño, en el
  mismo proyecto, y tiene `frozen_at` y `frozen_commit`.
- `estimates_reproducible`: volver a correr `scripts/analysis/estimate.py --plan <linked_plan>
  --units <units_dataset>` da exactamente el JSON del bloque ```estimate-json de
  `## Resultado`. El nombre del dataset de unidades se guarda en el frontmatter del nodo
  (`units_dataset`), porque la salida de estimate.py no lo lleva.
- `classifiers_validated`: cada clasificador del plan tiene
  `Datos/anotacion/<sample>/validacion-<name>.json` (de `agreement.py validate`) con
  `precision_lo >= min_precision_lo` y `recall_lo >= min_recall_lo`.
- `verifier_no_errors`: la verificación independiente más reciente (scope note) es
  `no_errors_found`.
- `avisos_cerrados`: ningún aviso de `Avisos/registro.json` está `pendiente`. Sin registro,
  falla con «sin registro de avisos». Solo se informa del número de avisos, nunca de a quién.

Ningún mensaje lleva filas ni valores de celdas del fichero de unidades. Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import designs as _designs  # noqa: E402
import verifications as vf  # noqa: E402
from notes import read_note, section  # noqa: E402
from plan_blocks import PlanError, read_plan  # noqa: E402

ESTIMATE = HERE.parent / "analysis" / "estimate.py"
_ID = re.compile(r"^[A-Z]+-\d{4}$")
_EST_BLOCK = re.compile(r"```estimate-json[ \t]*\n(.*?)\n[ \t]*```", re.S)


def _blank(v) -> bool:
    s = str(v or "").strip()
    return not s or (s.startswith("<") and s.endswith(">"))


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


def plan_frozen(vault: Path, node_path: Path, fm: dict) -> list[str]:
    path, pid, miss = _plan_path(node_path, fm)
    if path is None:
        return miss
    parsed = read_note(path)
    pfm = parsed[0] if parsed else {}
    out = [f"plan {pid}: falta {k}; congela el plan antes de recolectar"
           for k in ("frozen_at", "frozen_commit") if _blank(pfm.get(k))]
    q = str(pfm.get("question") or "").strip()
    if q and not _blank(q) and q != str(fm.get("id")):
        out.append(f"plan {pid}: es el plan de {q}, no de {fm.get('id')}")
    return out


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
    r = subprocess.run([sys.executable, str(ESTIMATE), "--vault", str(vault), "--plan", pid, "--units", units],
                       capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
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


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


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
        for key, floor in (("precision_lo", c["min_precision_lo"]), ("recall_lo", c["min_recall_lo"])):
            got = _num(v.get(key))
            if got is None:
                out.append(f"clasificador {name}: {rel} no tiene {key} numérico")
            elif got < floor:
                out.append(f"clasificador {name}: {key} {got:.3f} < {floor} exigido por el plan")
    return out


def verifier_no_errors(vault: Path, node_path: Path, fm: dict) -> list[str]:
    try:
        last = vf.latest(vf.load(str(node_path))[5], "note")
    except vf.InputError as e:
        return [f"verificación ilegible: {e}"]
    if not last or last.get("verdict") != "no_errors_found":
        return [f"verificación: {last.get('verdict') if last else 'nunca verificada'}; "
                "pasa el verificador independiente con verifier_packet.py --question"]
    return []


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
    n = sum(1 for a in data if isinstance(a, dict) and a.get("estado") == "pendiente")
    return [f"{n} aviso(s) pendiente(s) en Avisos/registro.json: envíalos o ciérralos"] if n else []


GATES = {
    "plan_frozen": plan_frozen,
    "estimates_reproducible": estimates_reproducible,
    "classifiers_validated": classifiers_validated,
    "verifier_no_errors": verifier_no_errors,
    "avisos_cerrados": avisos_cerrados,
}
