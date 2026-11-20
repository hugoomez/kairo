"""Repository facts for cleaning a sample, and a tutorial/demo score that decides nothing.

    repo_flags(meta)  -> {fork, archived, is_template, owner_type, stars, created_at,
                          pushed_at, size}
    demo_score(meta, readme=None, weights=None)
                      -> {"score": 0..1, "reasons": [...], "signals": [...], "decision": None}

`meta` is what ``Collection.repo_metadata`` returns (github.repo_facts) or the raw
``GET /repos/{owner}/{repo}`` JSON; both spellings are read. ``size`` is in KB, as
the API gives it.

``demo_score`` adds weighted signals that a repository is a tutorial, demo, example,
course exercise or template rather than a project in use: keywords in the NAME,
the DESCRIPTION and the TOPICS (DEMO_WORDS), being a template, having no README
(``readme`` from ``Collection.readme_present``; None = unknown, not counted), a short
life (pushed within LIFE_DAYS of creation), tiny size, no stars. Each reason names
the signal and the keyword from this module's list, never the description's text.

It is a heuristic score with reasons, never an exclusion decision: the weights and
the threshold are the study's to set and to VALIDATE as a classifier (label a
random sample by hand, report precision / recall at the chosen threshold, and say
in the methodology how many repositories it removed). The score is deterministic:
the life signal uses pushed_at - created_at, never the current clock. `decision`
is always None, on purpose.
"""
from __future__ import annotations

import re
from datetime import datetime

DEMO_WORDS = ("demo", "example", "examples", "sample", "samples", "tutorial", "tutorials", "test", "tests",
              "testing", "playground", "workshop", "course", "learn", "learning", "lesson", "training",
              "hello-world", "helloworld", "starter", "template", "boilerplate", "poc", "sandbox", "exercise",
              "exercises", "kata", "homework", "assignment", "lab", "toy", "scratch", "experiment",
              "experiments", "dummy", "fake", "mock", "showcase", "walkthrough", "guide", "howto")
WEIGHTS = {"name_keyword": 0.35, "description_keyword": 0.2, "topic_keyword": 0.2, "is_template": 0.25,
           "no_readme": 0.1, "short_life": 0.1, "tiny": 0.05, "no_stars": 0.05}
LIFE_DAYS = 7
TINY_KB = 50
_SPLIT = re.compile(r"[^a-z0-9]+")


def _get(meta: dict, *keys, default=None):
    for k in keys:
        cur = meta
        for part in k.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        if cur is not None:
            return cur
    return default


def repo_flags(meta: dict) -> dict:
    return {"fork": _get(meta, "fork"), "archived": _get(meta, "archived"),
            "is_template": _get(meta, "is_template"), "owner_type": _get(meta, "owner_type", "owner.type"),
            "stars": _get(meta, "stargazers_count", "stars"), "created_at": _get(meta, "created_at"),
            "pushed_at": _get(meta, "pushed_at"), "size": _get(meta, "size_kb", "size")}


def _words(text: str) -> set[str]:
    t = (text or "").lower()
    ws = {w for w in _SPLIT.split(t) if w}
    # joined forms ("hello-world") are matched on the hyphenated text too
    return ws | {m for m in DEMO_WORDS if "-" in m and m in t}


def _hits(text) -> list[str]:
    ws = _words(text if isinstance(text, str) else " ".join(map(str, text or [])))
    return [w for w in DEMO_WORDS if w in ws]


def _date(s):
    if not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def demo_score(meta: dict, readme: bool | None = None, weights: dict | None = None) -> dict:
    """Score in [0, 1] and the reasons; see the module docstring. Never a decision."""
    w = {**WEIGHTS, **(weights or {})}
    reasons: list[dict] = []

    def add(signal: str, detail=None):
        r = {"signal": signal, "weight": w[signal]}
        if detail is not None:
            r["detail"] = detail
        reasons.append(r)

    name = _get(meta, "name") or (str(_get(meta, "full_name") or "").split("/")[-1])
    if (h := _hits(name)):
        add("name_keyword", h)
    if (h := _hits(_get(meta, "description") or "")):
        add("description_keyword", h)
    if (h := _hits(_get(meta, "topics") or [])):
        add("topic_keyword", h)
    if _get(meta, "is_template") is True:
        add("is_template")
    if readme is False:
        add("no_readme")
    c, p = _date(_get(meta, "created_at")), _date(_get(meta, "pushed_at"))
    if c and p and (p - c).total_seconds() < LIFE_DAYS * 86400:
        add("short_life", f"<{LIFE_DAYS}d")
    size = _get(meta, "size_kb", "size")
    if isinstance(size, (int, float)) and size < TINY_KB:
        add("tiny", f"<{TINY_KB}KB")
    if _get(meta, "stargazers_count", "stars") == 0:
        add("no_stars")
    score = min(1.0, round(sum(r["weight"] for r in reasons), 4))
    return {"score": score, "reasons": reasons, "signals": sorted(r["signal"] for r in reasons),
            "decision": None}
