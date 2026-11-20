"""Inter-rater agreement and classifier validation over JSONL label files.

    agreement.py kappa --a A.jsonl --b B.jsonl --label L
    agreement.py validate --gold ref.jsonl --pred clf.jsonl --label L --positive V [--ci 0.95]
                          [--vault V] [--weights-from muestra.jsonl]

Records are aligned by their "id" field when every record has one, else by line order.
Only aggregates are printed. Exit 0 ok, 3 refused. Standard library only.

`validate` also prints `inputs`: {"gold": {"path", "sha256"}, "pred": {"path", "sha256"},
"label", "positive", "ci"}, so the validation can be recomputed and its files checked
(study_gates.classifiers_validated). A path inside --vault is recorded relative to it
(forward slashes); otherwise as given. Without --weights-from the output is exactly what it
always was (a test pins it byte for byte).

Weighted validation (--weights-from). When the validation sample was drawn stratified
(draw_sample.py --stratify-by, e.g. by a score bucket from score_buckets.py, to over-sample
the rare positives), plain precision and recall describe the sample, not the frame: the
over-sampled strata count too much. --weights-from names the sample file draw_sample wrote
(each item has `id`, `_stratum`, `_weight` = stratum frame size / items drawn); the gold
and pred records must have ids. Per id: the stratum is `_stratum` (else the weight's value);
a record without `_weight`, or an id the file does not hold, has weight 1 and is counted in
`weights_defaulted` (each such id is its own stratum of frame size 1, i.e. it stands only for
itself). The frame size of stratum h is N_h = the sum of the file's weights in h, and n_h the
validation items in h, so a validation that annotated only part of the drawn sample is
weighted N_h / n_h per item, not by the drawn sample's weight.

Method (deterministic, closed form, no resampling): precision = TP/PP and recall = TP/P are
ratios of stratified totals (combined ratio estimator, Cochran 1977 §6.11):
R = sum_h N_h ybar_h / sum_h N_h xbar_h, with y = 1[gold+ and pred+] and x = 1[pred+]
(precision) or 1[gold+] (recall). Its variance is the Taylor linearisation
V(R) = sum_h N_h^2 (1 - n_h/N_h) s2_h(d) / n_h / X^2, d_i = y_i - R x_i, s2_h the
within-stratum sample variance and (1 - n_h/N_h) the finite-population correction. The
interval is Wilson's at the effective sample size n_eff = R(1-R)/V(R) (Korn & Graubard 1998:
the design effect replaces the binomial variance), capped at the number of validation items
with x = 1 so it is never narrower than the unweighted interval on the same items would be
for that R; when V(R) = 0 (R is 0 or 1, or every stratum is constant) n_eff is that number.
Why not a weighted bootstrap: with rare positives many resamples of a small stratum hold no
positive at all, the percentile interval is then unstable in the seed, and the gate would
depend on a resampling count; the linearised interval is a pure function of the files.
A stratum with a single validation item and N_h > 1 has no within-stratum variance and is
refused (annotate at least two per stratum). The weighted output adds `weighted: true`,
`method`, `n_eff` {precision, recall}, `strata`, `weights_defaulted`, and `inputs.weights`
{"path", "sha256"} so the gate can recompute it with the same file.
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


WEIGHTED_METHOD = ("razón estratificada; varianza por linealización de Taylor con corrección de "
                   "población finita; Wilson con n efectivo (Korn-Graubard)")


def _ratio(rows: list[tuple[object, float, float]], sizes: dict, conf: float) -> tuple:
    """(R, lo, hi, n_eff) of sum N_h ybar_h / sum N_h xbar_h; rows are (stratum, y, x)."""
    by: dict = {}
    for h, y, x in rows:
        by.setdefault(h, []).append((y, x))
    ys = sum(sizes[h] * sum(y for y, _ in it) / len(it) for h, it in by.items())
    xs = sum(sizes[h] * sum(x for _, x in it) / len(it) for h, it in by.items())
    if xs == 0:
        return None, None, None, None
    r = ys / xs
    var = 0.0
    for h, it in by.items():
        n_h, big = len(it), sizes[h]
        fpc = max(0.0, 1 - n_h / big)
        if n_h < 2 or fpc == 0:
            continue
        d = [y - r * x for y, x in it]
        mean = sum(d) / n_h
        s2 = sum((v - mean) ** 2 for v in d) / (n_h - 1)
        var += big * big * fpc * s2 / n_h
    var /= xs * xs
    cap = sum(1 for _h, _y, x in rows if x)
    n_eff = float(cap) if var <= 0 else min(float(cap), r * (1 - r) / var)
    if n_eff <= 0:
        return r, None, None, n_eff
    lo, hi = wilson(r * n_eff, n_eff, conf)
    if r == 0:
        lo = 0.0
    if r == 1:
        hi = 1.0
    return r, lo, hi, n_eff


def validate_weighted(ids: list, gold: list, pred: list, positive, weights: dict,
                      conf: float = 0.95) -> dict:
    """Precision and recall weighted back to the frame of a stratified sample (module doc).
    weights: id -> (stratum, weight) from the sample file."""
    if len(gold) != len(pred) or not gold or len(ids) != len(gold):
        raise Refused("referencia y predicción deben tener la misma longitud y no estar vacías")
    if positive not in gold and positive not in pred:
        raise Refused("el valor --positive no aparece ni en la referencia ni en la predicción; "
                      "revisa cómo está escrito (las etiquetas booleanas son true/false)")
    strata, defaulted = [], 0
    for i in ids:
        if str(i) in weights:
            strata.append(("estrato", weights[str(i)][0]))
        else:
            strata.append(("sin_peso", str(i)))
            defaulted += 1
    # N_h: the sum of the file's weights in the stratum (the frame size for draw_sample's file)
    sizes: dict = {}
    for h_raw, w in weights.values():
        h = ("estrato", h_raw)
        sizes[h] = sizes.get(h, 0.0) + w
    counts: dict = {}
    for h in strata:
        counts[h] = counts.get(h, 0) + 1
    for h in counts:
        if h[0] == "sin_peso":
            sizes[h] = 1.0
        sizes[h] = max(sizes[h], float(counts[h]))
        if counts[h] < 2 and sizes[h] > 1:
            raise Refused("un estrato tiene un solo elemento en la validación: su varianza no se puede "
                          "estimar; anota al menos dos elementos por estrato")
    tp = [float(g == positive and p == positive) for g, p in zip(gold, pred)]
    pp = [float(p == positive) for p in pred]
    gp = [float(g == positive) for g in gold]
    prec, plo, phi, pe = _ratio(list(zip(strata, tp, pp)), sizes, conf)
    rec, rlo, rhi, re_ = _ratio(list(zip(strata, tp, gp)), sizes, conf)
    return {"precision": prec, "precision_lo": plo, "precision_hi": phi,
            "recall": rec, "recall_lo": rlo, "recall_hi": rhi, "n": len(gold),
            "weighted": True, "method": WEIGHTED_METHOD, "n_eff": {"precision": pe, "recall": re_},
            "strata": len(counts), "weights_defaulted": defaulted}


def _load_weights(path: str) -> dict:
    """id -> (stratum, weight) of a draw_sample file. A record without `_weight` weighs 1."""
    out: dict = {}
    try:
        lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    except UnicodeDecodeError:
        raise Refused(f"{Path(path).name} no es UTF-8: guarda el fichero en UTF-8") from None
    except OSError:
        raise Refused(f"no se pudo leer {Path(path).name}") from None
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            raise Refused(f"la línea {n} de {Path(path).name} no es JSON válido") from None
        if not isinstance(r, dict) or r.get("id") is None:
            raise Refused(f"la línea {n} de {Path(path).name} no tiene id")
        w = r.get("_weight", 1)
        if isinstance(w, bool) or not isinstance(w, (int, float)) or not w >= 1 or w == float("inf"):
            raise Refused(f"la línea {n} de {Path(path).name}: _weight debe ser un número >= 1")
        key = str(r["id"])
        if key in out:
            raise Refused(f"{Path(path).name}: hay ids repetidos")
        stratum = r.get("_stratum")
        out[key] = (str(stratum) if stratum is not None else f"peso={float(w)!r}", float(w))
    if not out:
        raise Refused(f"{Path(path).name} está vacío")
    return out


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
    return _pair_ids(p1, p2, label)[1:]


def _pair_ids(p1: str, p2: str, label: str) -> tuple[list | None, list, list]:
    """(ids of the first file or None, labels of the first, labels of the second aligned)."""
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
        return i1, l1, [m2[i] for i in i1]
    return None, l1, l2


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
    v.add_argument("--weights-from", default=None,
                   help="draw_sample file whose _stratum/_weight weight the validation to the frame")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "kappa":
            out = kappa(*_pair(a.a, a.b, a.label))
        elif a.weights_from is None:
            g, p = _pair(a.gold, a.pred, a.label)
            out = validate(g, p, norm(a.positive), a.ci)
            out["inputs"] = {"gold": _input(a.gold, a.vault), "pred": _input(a.pred, a.vault),
                             "label": a.label, "positive": norm(a.positive), "ci": a.ci}
        else:
            ids, g, p = _pair_ids(a.gold, a.pred, a.label)
            if ids is None:
                raise Refused("--weights-from necesita ids en la referencia y en la predicción "
                              "(los de la muestra de draw_sample)")
            out = validate_weighted(ids, g, p, norm(a.positive), _load_weights(a.weights_from), a.ci)
            out["inputs"] = {"gold": _input(a.gold, a.vault), "pred": _input(a.pred, a.vault),
                             "weights": _input(a.weights_from, a.vault),
                             "label": a.label, "positive": norm(a.positive), "ci": a.ci}
    except Refused as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 3
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
