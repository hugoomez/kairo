"""Writes train/ and heldout/ splits (inputs.json, targets.json) for task.py.

    python make_data.py <out_dir>
Train: 40 x on a grid in [-2, 2]; held-out: 40 x offset by half a step (disjoint).
"""
import json
import sys
from pathlib import Path


def f(x):
    return 3 * x * x - 2 * x + 1


def main(out):
    out = Path(out)
    step = 4 / 40
    splits = {"train": [round(-2 + i * step, 6) for i in range(40)],
              "heldout": [round(-2 + (i + 0.5) * step, 6) for i in range(40)]}
    for name, xs in splits.items():
        d = out / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "inputs.json").write_text(json.dumps(xs))
        (d / "targets.json").write_text(json.dumps([f(x) for x in xs]))


if __name__ == "__main__":
    main(sys.argv[1])
