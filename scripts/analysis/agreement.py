"""Inter-rater agreement and classifier validation over JSONL label files.

    agreement.py kappa --a A.jsonl --b B.jsonl --label L
    agreement.py validate --gold ref.jsonl --pred clf.jsonl --label L --positive V [--ci 0.95]

Records are aligned by their "id" field when every record has one, else by line order.
Only aggregates are printed. Exit 0 ok, 3 refused. Standard library only.
"""
from __future__ import annotations

import argparse
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
    k = (1.0 if obs == 1 else 0.0) if exp == 1 else (obs - exp) / (1 - exp)
    return {"kappa": k, "n": n, "observed": obs, "expected": exp}


def validate(gold: list, pred: list, positive, conf: float = 0.95) -> dict:
    if len(gold) != len(pred) or not gold:
        raise Refused("referencia y predicción deben tener la misma longitud y no estar vacías")
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
    except (OSError, ValueError) as e:
        if isinstance(e, Refused):
            raise
        raise Refused(f"no se pudo leer {Path(path).name}: JSON inválido o fichero ausente") from None
    ids = [r.get("id") for r in recs]
    return (ids if all(i is not None for i in ids) else None), [r[label] for r in recs]


def _pair(p1: str, p2: str, label: str) -> tuple[list, list]:
    i1, l1 = _load(p1, label)
    i2, l2 = _load(p2, label)
    if i1 is not None and i2 is not None:
        if len(set(i1)) != len(i1) or len(set(i2)) != len(i2):
            raise Refused("hay ids repetidos")
        if set(i1) != set(i2):
            raise Refused("los dos ficheros no tienen los mismos ids")
        m2 = dict(zip(i2, l2))
        return l1, [m2[i] for i in i1]
    return l1, l2


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
    a = ap.parse_args(argv)
    try:
        if a.cmd == "kappa":
            out = kappa(*_pair(a.a, a.b, a.label))
        else:
            g, p = _pair(a.gold, a.pred, a.label)
            out = validate([str(x) for x in g], [str(x) for x in p], a.positive, a.ci)
    except Refused as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 3
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
