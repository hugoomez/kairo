"""Kairo model policy: read, validate and apply config/models.toml.

The policy maps every Kairo task to a tier and every tier to an exact, pinned
model id. Aliases ("opus", "sonnet", "...-latest") are refused: a task always
runs on the model the policy names, never on an account default.

    model_policy.py show                       the resolved policy (JSON)
    model_policy.py model --task T             one task's pinned model id
    model_policy.py check                      agents/ frontmatter and tools agree with the policy
                                               (isolated agents: Read only; reads_papers agents:
                                               read and search tools only)
    model_policy.py sync                       rewrite each agent's `model:` line from the policy
    model_policy.py set --task T [--tier X] [--effort E]
                                               change one task's tier and/or effort (edits the toml, then sync)
    model_policy.py set --task T --recommended apply the task's recommended tier and effort
    model_policy.py set --all --recommended    apply every recommendation
    model_policy.py override --task T [--tier X] [--effort E]
                                               one run's model and effort (validated, nothing written)

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

__version__ = "1.2.0"

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "config" / "models.toml"
KINDS = ("job", "agent", "tool")
# A pinned Claude model id: family + version digits, no alias, no "latest".
PINNED = re.compile(r"^claude-(fable|opus|sonnet|haiku)-\d+(-\d+)*$")
# Reasoning effort levels (Claude Code `--effort` and agent frontmatter `effort:`).
EFFORTS = ("low", "medium", "high", "xhigh", "max")
# An `isolated = true` agent must see only its packet: no shell or network
# tool, and one file tool, `Read`, which the vault hook
# (scripts/security/isolation.py) limits to its packet in the packet store —
# so the orchestrator hands over a path, never retypes the packet. An empty
# `tools:` inherits every tool, so the list must be exactly this.
ISOLATED_TOOLS = frozenset({"Read"})
# A `reads_papers = true` agent reads third-party paper text (notes, cards,
# abstracts), which may carry instructions written for a model. It may only
# read and search: no shell, network, write, edit, subagent or index tool.
PAPER_READER_TOOLS = frozenset({"Read", "Grep", "Glob", "mcp__smart-connections__search_by_text",
                                "mcp__smart-connections__search_similar", "mcp__smart-connections__get_note"})


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
        for key in ("effort", "recommended_effort"):
            if t.get(key) not in EFFORTS:
                raise Refused(f"task {tid!r}: {key} must be one of {', '.join(EFFORTS)}, not {t.get(key)!r}")
        if t.get("recommended_tier") not in tiers:
            raise Refused(f"task {tid!r}: recommended_tier {t.get('recommended_tier')!r} is not one of "
                          f"{', '.join(tiers)}")
        if t["kind"] == "agent" and not t.get("file"):
            raise Refused(f"task {tid!r}: an agent task needs `file`")
        for flag in ("isolated", "reads_papers"):
            if flag in t and (t["kind"] != "agent" or not isinstance(t[flag], bool)):
                raise Refused(f"task {tid!r}: `{flag}` is a true/false flag of agent tasks only")
    return data


def resolve(data: dict) -> dict:
    tiers = data["tiers"]
    return {
        "tiers": dict(tiers),
        "efforts": list(EFFORTS),
        "tasks": {
            tid: {"kind": t["kind"], "tier": t["tier"], "model": tiers[t["tier"]], "effort": t["effort"],
                  "recommended": {"tier": t["recommended_tier"], "model": tiers[t["recommended_tier"]],
                                  "effort": t["recommended_effort"], "why": t.get("why", "")},
                  "label": t.get("label", tid),
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


def _frontmatter_effort(text: str) -> str | None:
    m = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.S)
    if not m:
        return None
    hit = re.search(r"^effort:\s*(\S+)\s*$", m.group(1), re.M)
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
        eff = _frontmatter_effort(text)
        if eff != t["effort"]:
            problems.append(f"{tid}: {t['file']} has effort {eff!r}, the policy says {t['effort']!r}")
        tools = _frontmatter_tools(text)
        if not tools:
            problems.append(f"{tid}: {t['file']} has no explicit `tools:` list — an absent or empty "
                            "list inherits every tool (shell, files, network)")
        elif raw_tasks[tid].get("isolated"):
            extra = [x for x in tools if x not in ISOLATED_TOOLS]
            if extra:
                problems.append(f"{tid}: {t['file']} is isolated but has {', '.join(extra)}; "
                                f"only {', '.join(sorted(ISOLATED_TOOLS))} are allowed")
        elif raw_tasks[tid].get("reads_papers"):
            extra = [x for x in tools if x not in PAPER_READER_TOOLS]
            if extra:
                problems.append(f"{tid}: {t['file']} reads paper text but has {', '.join(extra)}; "
                                f"only {', '.join(sorted(PAPER_READER_TOOLS))} are allowed")
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
        if re.search(r"^effort:", new_fm, re.M):
            new_fm = re.sub(r"^effort:.*$", f"effort: {t['effort']}", new_fm, count=1, flags=re.M)
        else:
            new_fm = re.sub(r"^(model:.*)$", lambda h: f"{h.group(1)}\neffort: {t['effort']}", new_fm,
                            count=1, flags=re.M)
        if new_fm != fm:
            f.write_text(m.group(1) + new_fm + m.group(3) + text[m.end():], encoding="utf-8", newline="")
            changed.append(t["file"])
    return changed


def _validate_choice(data: dict, task: str, tier: str | None, effort: str | None) -> None:
    if task not in data["tasks"]:
        raise Refused(f"task {task!r} is not in the model policy")
    if tier is not None and tier not in data["tiers"]:
        raise Refused(f"tier must be one of {', '.join(data['tiers'])}")
    if effort is not None and effort not in EFFORTS:
        raise Refused(f"effort must be one of {', '.join(EFFORTS)}")


def _set_line(body: str, key: str, value: str, task: str) -> str:
    new_body, n = re.subn(rf'^{key}\s*=\s*"[^"]*"', f'{key} = "{value}"', body, count=1, flags=re.M)
    if n != 1:
        raise Refused(f"[tasks.{task}] has no {key} line")
    return new_body


def _write_choices(p: Path, choices: dict[str, tuple[str | None, str | None]]) -> None:
    """Rewrite the tier / effort lines of the named tasks in one pass."""
    text = p.read_text(encoding="utf-8")
    for task, (tier, effort) in choices.items():
        block = re.search(rf"^\[tasks\.{re.escape(task)}\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
        if not block:
            raise Refused(f"[tasks.{task}] not found as a table in {p.name}")
        body = block.group(1)
        if tier is not None:
            body = _set_line(body, "tier", tier, task)
        if effort is not None:
            body = _set_line(body, "effort", effort, task)
        text = text[:block.start(1)] + body + text[block.end(1):]
    p.write_text(text, encoding="utf-8", newline="")


def set_choice(task: str, tier: str | None = None, effort: str | None = None, *, recommended: bool = False,
               root: Path = ROOT, path: Path | None = None) -> dict:
    """Change one task's tier and/or effort (or apply its recommendation)."""
    p = path or root / "config" / "models.toml"
    data = load(p)
    _validate_choice(data, task, tier, effort)
    t = data["tasks"][task]
    if recommended:
        if tier is not None or effort is not None:
            raise Refused("--recommended takes no --tier or --effort")
        tier, effort = t["recommended_tier"], t["recommended_effort"]
    if tier is None and effort is None:
        raise Refused("nothing to change: give --tier, --effort or --recommended")
    before = {"tier": t["tier"], "effort": t["effort"]}
    _write_choices(p, {task: (tier, effort)})
    after = load(p)["tasks"][task]  # still valid
    changed = sync(root, p)
    return {"task": task, "from": before, "to": {"tier": after["tier"], "effort": after["effort"]},
            "model": data["tiers"][after["tier"]], "agents_changed": changed}


def set_tier(task: str, tier: str, root: Path = ROOT, path: Path | None = None) -> dict:
    """Kept for callers of the 1.1 interface: change one task's tier only."""
    out = set_choice(task, tier=tier, root=root, path=path)
    return {**out, "from": out["from"]["tier"], "to": out["to"]["tier"]}


def set_all_recommended(root: Path = ROOT, path: Path | None = None) -> dict:
    """Apply every task's recommended tier and effort."""
    p = path or root / "config" / "models.toml"
    data = load(p)
    changes = {tid: (t["recommended_tier"], t["recommended_effort"]) for tid, t in data["tasks"].items()
               if (t["tier"], t["effort"]) != (t["recommended_tier"], t["recommended_effort"])}
    if changes:
        _write_choices(p, changes)
        load(p)
    changed = sync(root, p)
    return {"tasks_changed": sorted(changes), "agents_changed": changed}


def override(task: str, tier: str | None = None, effort: str | None = None, path: Path = POLICY) -> dict:
    """One run's model and effort: the task's own, with the given tier / effort in their place."""
    data = load(path)
    _validate_choice(data, task, tier, effort)
    t = data["tasks"][task]
    tier = tier or t["tier"]
    return {"task": task, "tier": tier, "model": data["tiers"][tier], "effort": effort or t["effort"],
            "overridden": tier != t["tier"] or (effort or t["effort"]) != t["effort"]}


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
    p.add_argument("--task")
    p.add_argument("--all", action="store_true", help="with --recommended: every task")
    p.add_argument("--tier")
    p.add_argument("--effort")
    p.add_argument("--recommended", action="store_true")
    p = sub.add_parser("override")
    p.add_argument("--task", required=True)
    p.add_argument("--tier")
    p.add_argument("--effort")
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
        elif a.cmd == "override":
            out = override(a.task, a.tier, a.effort, path)
        elif a.all:
            if not a.recommended or a.task or a.tier or a.effort:
                raise Refused("--all goes only with --recommended")
            out = set_all_recommended(a.root, path)
        else:
            if not a.task:
                raise Refused("set needs --task (or --all --recommended)")
            out = set_choice(a.task, a.tier, a.effort, recommended=a.recommended, root=a.root, path=path)
    except Refused as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 3
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
