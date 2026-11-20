#!/usr/bin/env python3
"""Add a score-bucket column to a file of classifier predictions, to stratify a validation sample.

    score_buckets.py --in preds.jsonl|preds.csv --score COL --cuts 0.2,0.5,0.9 --out <abs path>
                     [--column score_bucket]

Why: to validate a classifier whose positives are rare, a simple random sample holds almost
no positives and the recall interval is useless. Stratify the validation sample by the
classifier's score instead, over-sampling the high-score buckets, and weight the result back:

1. `score_buckets.py` writes a copy of the predictions with one more column (`--column`,
   default `score_bucket`): the bucket of `--score` between the increasing `--cuts`
   (`<0.2`, `0.2-0.5`, `0.5-0.9`, `>=0.9`; a bucket is closed below and open above). A row
   without a numeric score goes to `sin_puntuacion`.
2. Register the copy as a dataset in the interface (sensitive if the source was).
3. `draw_sample.py --dataset <that name> --stratify-by score_bucket --per-stratum K --n N`:
   every item carries `_stratum` and `_weight` (= stratum frame size / items drawn).
4. Annotate the gold labels on that sample and validate with
   `agreement.py validate ... --weights-from <the sample .jsonl>`.

The output has every row of the input, in the same order and with the same fields, plus
the bucket; .jsonl stays .jsonl and .csv stays .csv (the extension of --in decides). Only
aggregates go to stdout: {"out", "out_sha256", "in_sha256", "rows", "score", "cuts",
"column", "buckets": {label: count}}; never a row or a cell value. `--out` must be an
absolute path that does not exist yet and is not --in.

The predictions may be a sensitive dataset: then data_guard refuses this command in an
agent session, and the researcher runs it in their own terminal. Exit 0 ok, 3 refused
({"error": ...}). Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import sys
from collections import Counter
from pathlib import Path

NO_SCORE = "sin_puntuacion"


class Refused(Exception):
    pass


def parse_cuts(raw: str) -> list[float]:
    try:
        cuts = [float(x) for x in raw.split(",") if x.strip()]
    except ValueError:
        raise Refused("--cuts debe ser una lista de números separados por comas") from None
    if not cuts or any(not math.isfinite(c) for c in cuts):
        raise Refused("--cuts necesita al menos un número finito")
    if any(b <= a for a, b in zip(cuts, cuts[1:])):
        raise Refused("--cuts debe ser estrictamente creciente")
    return cuts


def _fmt(x: float) -> str:
    return str(int(x)) if x.is_integer() else repr(x)


def labels(cuts: list[float]) -> list[str]:
    out = [f"<{_fmt(cuts[0])}"]
    out += [f"{_fmt(a)}-{_fmt(b)}" for a, b in zip(cuts, cuts[1:])]
    return out + [f">={_fmt(cuts[-1])}"]


def bucket(value, cuts: list[float], names: list[str]) -> str:
    if isinstance(value, bool) or value is None:
        return NO_SCORE
    try:
        x = float(str(value).strip()) if not isinstance(value, (int, float)) else float(value)
    except ValueError:
        return NO_SCORE
    if not math.isfinite(x):
        return NO_SCORE
    i = sum(1 for c in cuts if x >= c)
    return names[i]


def run(src: Path, score: str, cuts: list[float], out: Path, column: str = "score_bucket") -> dict:
    if not out.is_absolute():
        raise Refused("--out debe ser una ruta absoluta")
    if out.exists():
        raise Refused("--out ya existe; elige otro nombre (no se sobrescribe nada)")
    ext = src.suffix.lower()
    if ext not in (".jsonl", ".ndjson", ".csv"):
        raise Refused("--in debe ser .jsonl o .csv")
    if out.suffix.lower() != ext:
        raise Refused("--out debe tener la misma extensión que --in")
    try:
        data = src.read_bytes()
    except OSError:
        raise Refused("no se pudo leer --in") from None
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise Refused("--in no es UTF-8: guarda el fichero en UTF-8") from None
    names = labels(cuts)
    counts: Counter = Counter()
    if ext == ".csv":
        reader = csv.DictReader(io.StringIO(text, newline=""))
        fields = list(reader.fieldnames or [])
        if score not in fields:
            raise Refused(f"la columna {score!r} no está en --in")
        if column in fields:
            raise Refused(f"--in ya tiene una columna {column!r}; elige otro --column")
        buf = io.StringIO(newline="")
        w = csv.DictWriter(buf, fieldnames=[*fields, column], lineterminator="\n")
        w.writeheader()
        rows = 0
        try:
            for r in reader:
                b = bucket(r.get(score), cuts, names)
                counts[b] += 1
                rows += 1
                w.writerow({**r, column: b})
        except csv.Error:
            raise Refused("el CSV de --in no se puede leer") from None
        body = buf.getvalue()
    else:
        lines, seen_score, rows = [], False, 0
        for i, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                raise Refused(f"la línea {i} de --in no es JSON válido") from None
            if not isinstance(r, dict):
                raise Refused(f"la línea {i} de --in no es un objeto")
            if column in r:
                raise Refused(f"--in ya tiene un campo {column!r}; elige otro --column")
            seen_score = seen_score or score in r
            b = bucket(r.get(score), cuts, names)
            counts[b] += 1
            rows += 1
            lines.append(json.dumps({**r, column: b}, ensure_ascii=False))
        if rows and not seen_score:
            raise Refused(f"ningún registro de --in tiene el campo {score!r}")
        body = "".join(x + "\n" for x in lines)
    raw = body.encode("utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw)
    return {"out": str(out), "out_sha256": hashlib.sha256(raw).hexdigest(),
            "in_sha256": hashlib.sha256(data).hexdigest(), "rows": rows, "score": score,
            "cuts": cuts, "column": column,
            "buckets": {n: counts[n] for n in [*names, NO_SCORE] if counts[n] or n != NO_SCORE}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--score", required=True)
    ap.add_argument("--cuts", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--column", default="score_bucket")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        src, out = Path(a.src), Path(a.out)
        if src.resolve() == out.resolve():
            raise Refused("--out no puede ser --in")
        res = run(src, a.score, parse_cuts(a.cuts), out, a.column)
    except Refused as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 3
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
