"""Study-design registry: load and check config/designs.toml.

    designs.py check [--root DIR]   exit 0 ok, 3 with the list of problems
    designs.py show  [--root DIR]   the registry as JSON

Standard library only (Python >= 3.11 for tomllib).
"""
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import TypedDict

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

PLUGIN_ROOT = HERE.parents[1]
RESERVED_PREFIXES = ("H", "C", "E", "P")
REQUIRED = ("prefix", "folder", "statuses", "terminal", "transitions", "researcher_only",
            "publishable", "gate", "plan_prefix", "plan_folder", "plan_template",
            "manuscript_sections")


class Design(TypedDict):
    prefix: str
    folder: str
    statuses: list[str]
    terminal: list[str]
    transitions: list[tuple[str, str]]
    researcher_only: list[tuple[str, str]]
    publishable: str
    gate: list[str]
    plan_prefix: str
    plan_folder: str
    plan_template: str
    manuscript_sections: list[dict]


def _read(plugin_root: Path) -> dict:
    with open(Path(plugin_root) / "config" / "designs.toml", "rb") as fh:
        return tomllib.load(fh)


def load(plugin_root: Path = PLUGIN_ROOT) -> dict[str, Design]:
    out: dict[str, Design] = {}
    for name, raw in _read(plugin_root).items():
        d = dict(raw)
        for key in ("transitions", "researcher_only"):
            d[key] = [tuple(p) for p in d.get(key, [])]
        out[name] = d  # type: ignore[assignment]
    return out


def design_for_id(designs: dict[str, Design], nid: str) -> Design | None:
    prefix = nid.split("-", 1)[0]
    for d in designs.values():
        if d["prefix"] == prefix:
            return d
    return None


def problems(plugin_root: Path = PLUGIN_ROOT) -> list[str]:
    from study_gates import GATES

    out: list[str] = []
    try:
        designs = load(plugin_root)
    except (OSError, tomllib.TOMLDecodeError) as e:
        return [f"designs.toml ilegible: {e}"]
    seen: dict[str, str] = {}
    for name, d in designs.items():
        miss = [k for k in REQUIRED if k not in d]
        if miss:
            out.append(f"{name}: faltan claves {', '.join(miss)}")
            continue
        statuses = set(d["statuses"])
        for p in (d["prefix"], d["plan_prefix"]):
            if p in RESERVED_PREFIXES:
                out.append(f"{name}: prefijo {p!r} ya está en uso (H, C, E, P); elige otro")
        for p in (d["prefix"], d["plan_prefix"]):
            if p in seen and seen[p] != name:
                out.append(f"{name}: prefijo {p!r} repetido (ya lo usa {seen[p]})")
            seen.setdefault(p, name)
        for a, b in d["transitions"]:
            for s in (a, b):
                if s not in statuses:
                    out.append(f"{name}: la transición {a}->{b} usa el estado no declarado {s!r}")
        pairs = set(d["transitions"])
        for s in d["terminal"]:
            if s not in statuses:
                out.append(f"{name}: terminal {s!r} no es un estado declarado")
        if d["publishable"] not in statuses:
            out.append(f"{name}: publishable {d['publishable']!r} no es un estado declarado")
        for pr in d["researcher_only"]:
            if pr not in pairs:
                out.append(f"{name}: researcher_only {pr[0]}->{pr[1]} no es una transición declarada")
        for g in d["gate"]:
            if g not in GATES:
                out.append(f"{name}: la comprobación de puerta {g!r} no existe en study_gates.GATES")
        if not (Path(plugin_root) / d["plan_template"]).is_file():
            out.append(f"{name}: plan_template {d['plan_template']} no existe")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["check", "show"])
    ap.add_argument("--root", type=Path, default=PLUGIN_ROOT)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "show":
            print(json.dumps(load(args.root), ensure_ascii=False, indent=2))
            return 0
        probs = problems(args.root)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1
    print(json.dumps({"ok": not probs, "problems": probs}, ensure_ascii=False, indent=2))
    return 3 if probs else 0


if __name__ == "__main__":
    sys.exit(main())
