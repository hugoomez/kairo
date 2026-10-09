#!/usr/bin/env python3
"""Kairo's usage profile: the whole research programme, or only the literature.

    kairo_profile.py show                 # the active profile and what it allows (JSON)
    kairo_profile.py check <skill>        # exit 0 allowed · 3 not in this profile
    kairo_profile.py check-template <t>   # create-project template: exit 0 allowed · 3 not

`KAIRO_PROFILE` (environment of the Claude Code session) selects it:

  - unset or `completo` — everything, as always;
  - `literatura` — the literature path only: search, screening, ingestion, the
    state-of-the-art map, the corpus questions and the watch
    (`create-project` with the `ligero`, `corpus` or `revision` template,
    `literature-search`, `lit-watch`, `ask-corpus`, `idea`). The programme's
    skills (hypotheses, preregistration, experiments, theorems, manuscripts,
    program evolution, code repositories, ADRs) refuse with one line, and the
    hooks that serve only the programme (the lab notebook's commit log) do
    nothing. The safety hooks (send_guard, isolation) always run.

A plugin cannot hide its own skills, so each programme skill calls `check`
first and stops when it exits 3. Standard library only.
"""

from __future__ import annotations

import json
import os
import sys

PROFILES = ("completo", "literatura")
LITERATURE_SKILLS = frozenset({"create-project", "literature-search", "lit-watch", "ask-corpus", "idea"})
LITERATURE_TEMPLATES = frozenset({"ligero", "corpus", "revision"})
PROGRAMME_SKILLS = frozenset({
    "hypothesis-cycle", "spawn-hypothesis", "preregister-experiment", "run-experiment", "update-confidence",
    "pitfall-audit", "evolve-program", "paper-to-tool", "theorem", "assemble-manuscript", "repo-steward",
    "adr-check", "critique", "serendipity-scan"})


def active() -> str:
    p = (os.environ.get("KAIRO_PROFILE") or "completo").strip().lower()
    return p if p in PROFILES else "completo"


def allowed(skill: str) -> bool:
    return active() == "completo" or skill.rsplit(":", 1)[-1] in LITERATURE_SKILLS


def template_allowed(template: str) -> bool:
    return active() == "completo" or template in LITERATURE_TEMPLATES


def programme_hooks() -> bool:
    """Whether the hooks that serve only the research programme should run."""
    return active() == "completo"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not argv or argv[0] not in ("show", "check", "check-template") or (argv[0] != "show" and len(argv) < 2):
        print(__doc__.split("\n\n")[1])
        return 2
    if argv[0] == "show":
        print(json.dumps({"profile": active(), "skills": "todas" if active() == "completo"
                          else sorted(LITERATURE_SKILLS), "create_project_templates": "todas"
                          if active() == "completo" else sorted(LITERATURE_TEMPLATES)}, ensure_ascii=False))
        return 0
    name = argv[1]
    ok = allowed(name) if argv[0] == "check" else template_allowed(name)
    what = f"la skill `{name}`" if argv[0] == "check" else f"la plantilla `{name}`"
    if ok:
        print(json.dumps({"profile": active(), "allowed": True}))
        return 0
    print(json.dumps({"profile": active(), "allowed": False,
                      "message": f"Perfil `literatura` activo (KAIRO_PROFILE): {what} pertenece al programa de "
                                 "investigación y no se usa en este perfil. Quita KAIRO_PROFILE (o ponlo a "
                                 "`completo`) para usarla."}, ensure_ascii=False))
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
