"""Inter-rater agreement and classifier validation over JSONL label files.

    agreement.py kappa --a A.jsonl --b B.jsonl --label L
    agreement.py validate --gold ref.jsonl --pred clf.jsonl --label L --positive V [--ci 0.95]
                          [--vault V]

Records are aligned by their "id" field when every record has one, else by line order.
Only aggregates are printed. Exit 0 ok, 3 refused. Standard library only.

`validate` also prints `inputs`: {"gold": {"path", "sha256"}, "pred": {"path", "sha256"},
"label", "positive", "ci"}, so the validation can be recomputed and its files checked
(study_gates.classifiers_validated). A path inside --vault is recorded relative to it
(forward slashes); otherwise as given.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from estimate import wilson  # noqa: E402


class Refused(Exception):
    pass


def kappa(a: list, b: list) -> dict:
    n = len(a)
    if n == 0 or n != len(b):
        raise Refused("las dos listas de etiquetas deben tener la misma longitud y no estar vacías")
    obs = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    exp = sum(ca[k] * cb[k] for k in ca) / (n * n)
    if exp == 1:
        return {"kappa": None, "n": n, "observed": obs, "expected": exp,
                "note": "kappa indefinido: un solo valor en ambos anotadores"}
    return {"kappa": (obs - exp) / (1 - exp), "n": n, "observed": obs, "expected": exp}


def norm(v) -> str:
    """One label normalisation for every subcommand."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def validate(gold: list, pred: list, positive, conf: float = 0.95) -> dict:
    if len(gold) != len(pred) or not gold:
        raise Refused("referencia y predicción deben tener la misma longitud y no estar vacías")
    if positive not in gold and positive not in pred:
        raise Refused("el valor --positive no aparece ni en la referencia ni en la predicción; "
                      "revisa cómo está escrito (las etiquetas booleanas son true/false)")
    tp = sum(g == positive and p == positive for g, p in zip(gold, pred))
    fp = sum(g != positive and p == positive for g, p in zip(gold, pred))
    fn = sum(g == positive and p != positive for g, p in zip(gold, pred))
    pn, rn = tp + fp, tp + fn
    prec = tp / pn if pn else None
    rec = tp / rn if rn else None
    plo, phi = wilson(tp, pn, conf) if pn else (None, None)
    rlo, rhi = wilson(tp, rn, conf) if rn else (None, None)
    return {"precision": prec, "precision_lo": plo, "precision_hi": phi,
            "recall": rec, "recall_lo": rlo, "recall_hi": rhi, "n": len(gold)}


def _load(path: str, label: str) -> tuple[list | None, list]:
    recs = []
    try:
        for i, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
            if line.strip():
                r = json.loads(line)
                if not isinstance(r, dict) or label not in r:
                    raise Refused(f"la línea {i} de {Path(path).name} no tiene el campo {label!r}")
                recs.append(r)
    except UnicodeDecodeError:
        raise Refused(f"{Path(path).name} no es UTF-8: guarda el fichero en UTF-8") from None
    except (OSError, ValueError) as e:
        if isinstance(e, Refused):
            raise
        raise Refused(f"no se pudo leer {Path(path).name}: JSON inválido o fichero ausente") from None
    ids = [r.get("id") for r in recs]
    some = sum(i is not None for i in ids)
    if 0 < some < len(ids):
        raise Refused(f"{Path(path).name}: {len(ids) - some} de {len(ids)} registros no tienen id; "
                      "todos o ninguno")
    return (ids if some else None), [norm(r[label]) for r in recs]


def _pair(p1: str, p2: str, label: str) -> tuple[list, list]:
    i1, l1 = _load(p1, label)
    i2, l2 = _load(p2, label)
    if (i1 is None) != (i2 is None):
        raise Refused("solo uno de los dos ficheros tiene ids; añade ids a ambos o a ninguno")
    if i1 is not None:
        if len(set(i1)) != len(i1) or len(set(i2)) != len(i2):
            raise Refused("hay ids repetidos")
        if set(i1) != set(i2):
            raise Refused(f"los ids no coinciden: {len(set(i1) - set(i2))} solo en el primer fichero, "
                          f"{len(set(i2) - set(i1))} solo en el segundo")
        m2 = dict(zip(i2, l2))
        return l1, [m2[i] for i in i1]
    return l1, l2


def _input(path: str, vault: str | None) -> dict:
    p = Path(path)
    shown = path
    if vault:
        try:
            shown = p.resolve().relative_to(Path(vault).resolve()).as_posix()
        except ValueError:
            pass
    try:
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        raise Refused(f"no se pudo leer {p.name}") from None
    return {"path": shown, "sha256": sha}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("kappa")
    k.add_argument("--a", required=True)
    k.add_argument("--b", required=True)
    k.add_argument("--label", required=True)
    v = sub.add_parser("validate")
    v.add_argument("--gold", required=True)
    v.add_argument("--pred", required=True)
    v.add_argument("--label", required=True)
    v.add_argument("--positive", required=True)
    v.add_argument("--ci", type=float, default=0.95)
    v.add_argument("--vault", default=None, help="record input paths relative to this vault")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "kappa":
            out = kappa(*_pair(a.a, a.b, a.label))
        else:
            g, p = _pair(a.gold, a.pred, a.label)
            out = validate(g, p, norm(a.positive), a.ci)
            out["inputs"] = {"gold": _input(a.gold, a.vault), "pred": _input(a.pred, a.vault),
                             "label": a.label, "positive": norm(a.positive), "ci": a.ci}
    except Refused as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 3
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
