"""Kairo model policy: read, validate and apply config/models.toml.

The policy maps every Kairo task to a tier and every tier to an exact, pinned
model id. Aliases ("opus", "sonnet", "...-latest") are refused: a task always
runs on the model the policy names, never on an account default.

    model_policy.py show                       the resolved policy (JSON)
    model_policy.py model --task T             one task's pinned model id
    model_policy.py check                      agents/ frontmatter and tools agree with the policy
    model_policy.py sync                       rewrite each agent's `model:` line from the policy
    model_policy.py set --task T --tier X      change one task's tier (edits the toml, then sync)

Exit codes: 0 ok · 1 the repo disagrees with the policy · 3 refused (bad policy
or bad request). Standard library only (Python ≥ 3.11 for tomllib).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

__version__ = "1.0.0"

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "config" / "models.toml"
KINDS = ("job", "agent", "tool")
# A pinned Claude model id: family + version digits, no alias, no "latest".
PINNED = re.compile(r"^claude-(opus|sonnet|haiku)-\d+(-\d+)*$")
# An `isolated = true` agent must see only its packet: no file, shell or
# network tool. Claude Code cannot launch an agent with zero tools, and an
# empty `tools:` inherits all of them, so isolated agents list only these
# session-bookkeeping tools, which read no file and reach no network.
ISOLATED_TOOLS = frozenset({"CronList", "TaskList"})


class Refused(Exception):
    pass


def load(path: Path = POLICY) -> dict:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise Refused(f"cannot read the model policy {path}: {e}") from e
    tiers = data.get("tiers") or {}
    tasks = data.get("tasks") or {}
    if not tiers or not tasks:
        raise Refused("the model policy needs [tiers] and [tasks]")
    for name, model in tiers.items():
        if not isinstance(model, str) or not PINNED.match(model):
            raise Refused(f"tier {name!r} must name a pinned model id (e.g. claude-opus-5-5), not {model!r}")
    for tid, t in tasks.items():
        if t.get("kind") not in KINDS:
            raise Refused(f"task {tid!r}: kind must be one of {', '.join(KINDS)}")
        if t.get("tier") not in tiers:
            raise Refused(f"task {tid!r}: tier {t.get('tier')!r} is not one of {', '.join(tiers)}")
        if t["kind"] == "agent" and not t.get("file"):
            raise Refused(f"task {tid!r}: an agent task needs `file`")
        if "isolated" in t and (t["kind"] != "agent" or not isinstance(t["isolated"], bool)):
            raise Refused(f"task {tid!r}: `isolated` is a true/false flag of agent tasks only")
    return data


def resolve(data: dict) -> dict:
    tiers = data["tiers"]
    return {
        "tiers": dict(tiers),
        "tasks": {
            tid: {"kind": t["kind"], "tier": t["tier"], "model": tiers[t["tier"]], "label": t.get("label", tid),
                  **({"file": t["file"]} if t.get("file") else {})}
            for tid, t in data["tasks"].items()
        },
    }


def model_for(task: str, path: Path = POLICY) -> str:
    r = resolve(load(path))
    if task not in r["tasks"]:
        raise Refused(f"task {task!r} is not in the model policy")
    return r["tasks"][task]["model"]


def _frontmatter_model(text: str) -> str | None:
    m = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.S)
    if not m:
        return None
    hit = re.search(r"^model:\s*(\S+)\s*$", m.group(1), re.M)
    return hit.group(1) if hit else None


def _frontmatter_tools(text: str) -> list[str] | None:
    """The agent's `tools:` entries; None when the line is missing. An empty
    `tools:` (or `tools: ""`) gives [] — which Claude Code reads as "inherit
    every tool", the opposite of what an empty list looks like."""
    m = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.S)
    if not m:
        return None
    hit = re.search(r"^tools:[ \t]*(.*?)[ \t]*$", m.group(1), re.M)
    if not hit:
        return None
    raw = hit.group(1).strip().strip("[]").strip("\"'")
    return [t.strip().strip("\"'") for t in raw.split(",") if t.strip().strip("\"'")]


def check(root: Path = ROOT, path: Path | None = None) -> list[str]:
    """Everything that disagrees with the policy, as one line each."""
    r = resolve(load(path or root / "config" / "models.toml"))
    raw_tasks = load(path or root / "config" / "models.toml")["tasks"]
    problems: list[str] = []
    listed = set()
    for tid, t in r["tasks"].items():
        if t["kind"] != "agent":
            continue
        f = root / t["file"]
        listed.add(f.resolve())
        if not f.exists():
            problems.append(f"{tid}: {t['file']} does not exist")
            continue
        text = f.read_text(encoding="utf-8")
        have = _frontmatter_model(text)
        if have != t["model"]:
            problems.append(f"{tid}: {t['file']} has model {have!r}, the policy says {t['model']!r}")
        tools = _frontmatter_tools(text)
        if not tools:
            problems.append(f"{tid}: {t['file']} has no explicit `tools:` list — an absent or empty "
                            "list inherits every tool (shell, files, network)")
        elif raw_tasks[tid].get("isolated"):
            extra = [x for x in tools if x not in ISOLATED_TOOLS]
            if extra:
                problems.append(f"{tid}: {t['file']} is isolated but has {', '.join(extra)}; "
                                f"only {', '.join(sorted(ISOLATED_TOOLS))} are allowed")
    for f in sorted((root / "agents").glob("*.md")):
        if f.resolve() not in listed:
            problems.append(f"{f.relative_to(root).as_posix()}: a subagent with no task in the model policy")
    return problems


def sync(root: Path = ROOT, path: Path | None = None) -> list[str]:
    """Rewrite each agent's `model:` line from the policy. Returns the files changed."""
    r = resolve(load(path or root / "config" / "models.toml"))
    changed = []
    for t in r["tasks"].values():
        if t["kind"] != "agent":
            continue
        f = root / t["file"]
        text = f.read_text(encoding="utf-8")
        m = re.match(r"^(---\r?\n)(.*?)(\r?\n---)", text, re.S)
        if not m:
            raise Refused(f"{t['file']} has no frontmatter")
        fm = m.group(2)
        if re.search(r"^model:", fm, re.M):
            new_fm = re.sub(r"^model:.*$", f"model: {t['model']}", fm, count=1, flags=re.M)
        else:
            new_fm = fm + f"\nmodel: {t['model']}"
        if new_fm != fm:
            f.write_text(m.group(1) + new_fm + m.group(3) + text[m.end():], encoding="utf-8", newline="")
            changed.append(t["file"])
    return changed


def set_tier(task: str, tier: str, root: Path = ROOT, path: Path | None = None) -> dict:
    p = path or root / "config" / "models.toml"
    data = load(p)
    if task not in data["tasks"]:
        raise Refused(f"task {task!r} is not in the model policy")
    if tier not in data["tiers"]:
        raise Refused(f"tier must be one of {', '.join(data['tiers'])}")
    text = p.read_text(encoding="utf-8")
    block = re.search(rf"^\[tasks\.{re.escape(task)}\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    if not block:
        raise Refused(f"[tasks.{task}] not found as a table in {p.name}")
    body = block.group(1)
    new_body, n = re.subn(r'^tier\s*=\s*"[^"]*"', f'tier = "{tier}"', body, count=1, flags=re.M)
    if n != 1:
        raise Refused(f"[tasks.{task}] has no tier line")
    before = data["tasks"][task]["tier"]
    p.write_text(text[:block.start(1)] + new_body + text[block.end(1):], encoding="utf-8", newline="")
    load(p)  # still valid
    changed = sync(root, p)
    return {"task": task, "from": before, "to": tier, "model": data["tiers"][tier], "agents_changed": changed}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=ROOT, help="plugin checkout (default: this one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    p = sub.add_parser("model")
    p.add_argument("--task", required=True)
    sub.add_parser("check")
    sub.add_parser("sync")
    p = sub.add_parser("set")
    p.add_argument("--task", required=True)
    p.add_argument("--tier", required=True)
    a = ap.parse_args(argv)
    path = a.root / "config" / "models.toml"
    try:
        if a.cmd == "show":
            out = {"policy": path.as_posix(), "version": __version__, **resolve(load(path))}
        elif a.cmd == "model":
            out = {"task": a.task, "model": model_for(a.task, path)}
        elif a.cmd == "check":
            problems = check(a.root, path)
            print(json.dumps({"ok": not problems, "problems": problems}, ensure_ascii=False))
            return 0 if not problems else 1
        elif a.cmd == "sync":
            out = {"changed": sync(a.root, path)}
        else:
            out = set_tier(a.task, a.tier, a.root, path)
    except Refused as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 3
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
