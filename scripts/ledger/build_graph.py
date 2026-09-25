#!/usr/bin/env python3
"""Kairo state ledger — dependency graph over Hipotesis/ and Claims/.

Builds one graph over every ``Projects/*/Hipotesis/*.md`` (``H-XXXX``) and
``Projects/*/Claims/*.md`` (``C-XXXX``) note in a vault, from their
``depends_on:`` lists (and a claim's ``about:`` link), then:

- **crítico** — dependency cycles; dangling references (an id in
  ``depends_on`` / ``about`` that no note carries); duplicate ids; a status
  outside the note type's enum.
- **importante** — failure propagation: every node that depends, directly or
  transitively, on a ``refutada`` hypothesis or a ``fallido`` / ``refutado``
  claim is FLAGGED, with the path. Flags live only in ``_ledger.md`` and in this
  script's output. No note is ever rewritten — the adr-check pattern: flag,
  never rewrite. A flag is a prompt for a human, not a status change.

and writes a compact, derived ``Projects/<slug>/_ledger.md`` per project that
``hypothesis-cycle`` reads at the start of each cycle instead of re-reading the
whole project. The ledger is a view; the notes are the source of truth.

Usage:
    build_graph.py --vault <vault root> [--project <slug>] [--write] [--json]
    build_graph.py --hook            # PostToolUse hook: stdin JSON, always exit 0

Exit codes (non-hook): 0 = no crítico finding, 2 = at least one crítico
finding, 1 = error (bad vault path). Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notes import as_list, first_line, read_note, section  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "security"))
from send_guard import is_flagged  # noqa: E402  (A3's single definition of the flag)

SEND_NEVER_TITLE = "(send: never — contenido omitido)"

__version__ = "1.0.0"
TOOL = f"kairo/build_graph@{__version__}"

H_STATUSES = {"propuesta", "en_cola", "preregistrada", "en_experimento",
              "apoyada", "refutada", "inconclusa", "evidencia_mixta"}
C_STATUSES = {"pendiente", "probado", "fallido", "refutado"}
FAILED = {("H", "refutada"), ("C", "fallido"), ("C", "refutado")}


@dataclass
class Node:
    id: str
    kind: str                 # "H" | "C"
    project_dir: str          # Projects/<slug>
    path: str                 # vault-relative, forward slashes
    status: str
    depends_on: list[str]
    about: list[str] = field(default_factory=list)
    claim_kind: str = ""      # lema | resultado_intermedio | rung | linaje
    rung: str = ""
    role: str = ""
    source: str = ""
    title: str = ""


@dataclass
class Finding:
    severity: str             # crítico | importante | menor
    kind: str                 # cycle | dangling | duplicate_id | bad_status | depends_on_failed
    node: str
    project_dir: str
    detail: str

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _rel(p: Path, root: Path) -> str:
    return p.relative_to(root).as_posix()


def load_nodes(vault: Path) -> tuple[dict[str, Node], list[Finding]]:
    nodes: dict[str, Node] = {}
    findings: list[Finding] = []
    for sub, kind in (("Hipotesis", "H"), ("Claims", "C")):
        for path in sorted(vault.glob(f"Projects/*/{sub}/*.md")):
            parsed = read_note(path)
            if parsed is None:
                continue
            fm, body = parsed
            nid = str(fm.get("id", "")).strip()
            if not nid.startswith(f"{kind}-"):
                continue
            proj = _rel(path.parent.parent, vault)
            status = str(fm.get("status", "")).strip()
            title_src = section(body, "Claim") if kind == "H" else section(body, "Enunciado")
            node = Node(
                id=nid, kind=kind, project_dir=proj, path=_rel(path, vault),
                status=status, depends_on=as_list(fm.get("depends_on")),
                about=as_list(fm.get("about")) if kind == "C" else [],
                claim_kind=str(fm.get("kind", "")) if kind == "C" else "",
                rung=str(fm.get("rung", "")) if kind == "C" else "",
                role=str(fm.get("role", "")) if kind == "C" else "",
                source=" ".join(re.findall(r"\b(?:EVO|E)-\d{4}\b", str(fm.get("source", ""))))
                if kind == "C" else "",
                # a send: never note keeps its id / status / edges (the graph needs
                # them) but none of its text reaches _ledger.md, which models read
                title=SEND_NEVER_TITLE if is_flagged(path) else first_line(title_src),
            )
            if nid in nodes:
                findings.append(Finding("crítico", "duplicate_id", nid, proj,
                                        f"id {nid} aparece en {nodes[nid].path} y en {node.path}"))
                continue
            nodes[nid] = node
            allowed = H_STATUSES if kind == "H" else C_STATUSES
            if status not in allowed:
                findings.append(Finding("crítico", "bad_status", nid, proj,
                                        f"status '{status}' fuera del enum ({' | '.join(sorted(allowed))})"))
    return nodes, findings


def find_cycles(nodes: dict[str, Node]) -> list[list[str]]:
    """Tarjan SCC over depends_on edges (dangling ids ignored). Returns every
    strongly connected component that is a real cycle, each sorted."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    out: list[list[str]] = []
    counter = [0]

    def strong(v: str) -> None:
        # iterative to survive long chains
        work = [(v, iter(nodes[v].depends_on))]
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        while work:
            u, it = work[-1]
            advanced = False
            for w in it:
                if w not in nodes:
                    continue
                if w not in index:
                    index[w] = low[w] = counter[0]
                    counter[0] += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, iter(nodes[w].depends_on)))
                    advanced = True
                    break
                if w in on_stack:
                    low[u] = min(low[u], index[w])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[u])
            if low[u] == index[u]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == u:
                        break
                if len(comp) > 1 or u in nodes[u].depends_on:
                    out.append(sorted(comp))

    for v in sorted(nodes):
        if v not in index:
            strong(v)
    return sorted(out)


def analyse(nodes: dict[str, Node], findings: list[Finding]) -> list[Finding]:
    findings = list(findings)
    for n in sorted(nodes.values(), key=lambda x: x.id):
        for ref, field_name in [(d, "depends_on") for d in n.depends_on] + \
                               [(a, "about") for a in n.about]:
            if ref not in nodes:
                findings.append(Finding("crítico", "dangling", n.id, n.project_dir,
                                        f"{field_name} → {ref}: no existe ninguna nota con ese id"))
    for comp in find_cycles(nodes):
        chain = " → ".join(comp + [comp[0]])
        for nid in comp:
            findings.append(Finding("crítico", "cycle", nid, nodes[nid].project_dir,
                                    f"ciclo de dependencias: {chain}"))

    # reverse edges: dependency -> dependents
    dependents: dict[str, list[str]] = {k: [] for k in nodes}
    for n in nodes.values():
        for d in n.depends_on:
            if d in dependents:
                dependents[d].append(n.id)

    for src in sorted(nodes):
        s = nodes[src]
        if (s.kind, s.status) not in FAILED:
            continue
        # BFS from the failed node over dependents, recording one shortest path
        prev: dict[str, str] = {}
        queue = [src]
        seen = {src}
        while queue:
            cur = queue.pop(0)
            for dep in sorted(dependents[cur]):
                if dep in seen:
                    continue
                seen.add(dep)
                prev[dep] = cur
                queue.append(dep)
        for tgt in sorted(prev):
            path = [tgt]
            while path[-1] != src:
                path.append(prev[path[-1]])
            via = " ← ".join(path)
            findings.append(Finding(
                "importante", "depends_on_failed", tgt, nodes[tgt].project_dir,
                f"depende de {src} (`{s.status}`) vía {via} — revisar la premisa; "
                f"este flag no cambia el status de {tgt}"))
    return findings


def _flags_for(nid: str, findings: list[Finding]) -> str:
    tags = []
    for f in findings:
        if f.node == nid:
            short = {"cycle": "ciclo", "dangling": "ref. colgante",
                     "duplicate_id": "id duplicado", "bad_status": "status inválido",
                     "depends_on_failed": "premisa caída"}[f.kind]
            tags.append(f"{f.severity}: {short}")
    return "; ".join(sorted(set(tags)))


def render_ledger(project_dir: str, nodes: dict[str, Node], findings: list[Finding],
                  hub: dict) -> str:
    mine = sorted((n for n in nodes.values() if n.project_dir == project_dir),
                  key=lambda n: n.id)
    pf = [f for f in findings if f.project_dir == project_dir]
    proj_id = str(hub.get("id", "")) if hub else ""
    lines = [
        "---",
        f"generated_by: {TOOL}",
        f"project: {proj_id or project_dir}",
        f"hypotheses: {sum(1 for n in mine if n.kind == 'H')}",
        f"claims: {sum(1 for n in mine if n.kind == 'C')}",
        f"criticos: {sum(1 for f in pf if f.severity == 'crítico')}",
        f"importantes: {sum(1 for f in pf if f.severity == 'importante')}",
        "---",
        "",
        f"# Ledger de estado — {project_dir.split('/')[-1]}",
        "",
        "> Vista derivada, no editar a mano: la regenera "
        "`scripts/ledger/build_graph.py` (hook PostToolUse o a mano). Las notas son la "
        "fuente de verdad. Los flags **no** cambian ningún status: el de las hipótesis "
        "solo lo escribe `update-confidence`; el de los claims, `claim_status.py`.",
        "",
        "## Problemas",
        "",
    ]
    if not pf:
        lines.append("Ninguno.")
    for sev in ("crítico", "importante", "menor"):
        group = [f for f in pf if f.severity == sev]
        if group:
            lines.append(f"### {sev}")
            lines.append("")
            for f in sorted(group, key=lambda x: (x.node, x.kind, x.detail)):
                lines.append(f"- **{f.node}** — {f.detail}")
            lines.append("")
    lines += ["", "## Hipótesis", "",
              "| id | estado | depende de | flags | claim |",
              "|----|--------|------------|-------|-------|"]
    for n in (x for x in mine if x.kind == "H"):
        lines.append(f"| {n.id} | {n.status} | {', '.join(n.depends_on) or '—'} | "
                     f"{_flags_for(n.id, findings) or '—'} | {n.title} |")
    claims = [x for x in mine if x.kind == "C"]
    lines += ["", "## Claims", ""]
    if claims:
        lines += ["| id | tipo | estado | sobre | fuente | depende de | flags | enunciado |",
                  "|----|------|--------|-------|--------|------------|-------|-----------|"]
        for n in claims:
            lines.append(f"| {n.id} | {n.claim_kind or '—'} | {n.status} | "
                         f"{', '.join(n.about) or '—'} | {n.source or '—'} | "
                         f"{', '.join(n.depends_on) or '—'} | "
                         f"{_flags_for(n.id, findings) or '—'} | {n.title} |")
    else:
        lines.append("Ninguno.")
    rungs = [n for n in claims if n.claim_kind == "rung"]
    lines += ["", "## Escaleras de simplificación", "",
              "Rungs exploratorios: informan el diseño confirmatorio, **nunca** cuentan "
              "como evidencia. Los fallidos se listan igual que los demás.", ""]
    if rungs:
        lines += ["| hipótesis | claim | rung | experimento | estado |",
                  "|-----------|-------|------|-------------|--------|"]
        for n in sorted(rungs, key=lambda x: (",".join(x.about), x.rung, x.id)):
            lines.append(f"| {', '.join(n.about) or '—'} | {n.id} | {n.rung or '?'} | "
                         f"{n.source or '—'} | {n.status} |")
    else:
        lines.append("Ninguna.")
    return "\n".join(lines).rstrip() + "\n"


def build(vault: Path) -> tuple[dict[str, Node], list[Finding]]:
    nodes, findings = load_nodes(vault)
    return nodes, analyse(nodes, findings)


def project_dirs(vault: Path, only: str | None) -> list[str]:
    dirs = sorted(_rel(p, vault) for p in vault.glob("Projects/*") if p.is_dir())
    if only:
        want = only if only.startswith("Projects/") else f"Projects/{only}"
        dirs = [d for d in dirs if d == want]
    return dirs


def write_ledgers(vault: Path, nodes, findings, dirs: list[str]) -> list[Path]:
    written = []
    for d in dirs:
        pdir = vault / d
        if not ((pdir / "Hipotesis").is_dir() or (pdir / "Claims").is_dir()):
            continue
        hub = read_note(pdir / "_hub.md")
        text = render_ledger(d, nodes, findings, hub[0] if hub else {})
        target = pdir / "_ledger.md"
        old = target.read_text(encoding="utf-8") if target.exists() else None
        if old != text:
            target.write_text(text, encoding="utf-8", newline="\n")
        written.append(target)
    return written


def hook_main() -> int:
    """PostToolUse: rebuild the ledger of the project containing the edited note."""
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    try:
        payload = json.load(sys.stdin)
        fp = Path((payload.get("tool_input") or {}).get("file_path") or "")
        proj = next((p for p in fp.parents
                     if p.parent.name == "Projects" and p != fp), None)
        if proj is None or fp.parent.name not in ("Hipotesis", "Claims"):
            return 0
        vault = proj.parent.parent
        nodes, findings = build(vault)
        # every project: a refutation here can flag dependents in other projects
        write_ledgers(vault, nodes, findings, project_dirs(vault, None))
        crit = [f for f in findings if f.severity == "crítico"
                and f.project_dir == _rel(proj, vault)]
        if crit:
            print(f"ledger: {len(crit)} hallazgo(s) crítico(s) en {proj.name}/_ledger.md",
                  file=sys.stderr)
    except Exception as exc:  # a ledger failure must never break the session
        print(f"ledger hook error (ignored): {exc}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap =argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--vault", type=Path, help="vault root (contains Projects/)")
    ap.add_argument("--project", help="only this project slug (default: all)")
    ap.add_argument("--write", action="store_true", help="write Projects/<slug>/_ledger.md")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--hook", action="store_true", help="PostToolUse hook mode (stdin JSON)")
    ap.add_argument("--version", action="version", version=TOOL)
    args = ap.parse_args(argv)
    if args.hook:
        return hook_main()
    if not args.vault or not (args.vault / "Projects").is_dir():
        print("error: --vault must point at a directory containing Projects/", file=sys.stderr)
        return 1
    vault = args.vault.resolve()
    nodes, findings = build(vault)
    dirs = project_dirs(vault, args.project)
    if args.project and not dirs:
        print(f"error: no project {args.project}", file=sys.stderr)
        return 1
    scoped = [f for f in findings if f.project_dir in dirs]
    written = write_ledgers(vault, nodes, findings, dirs) if args.write else []
    if args.json:
        print(json.dumps({
            "tool": TOOL,
            "nodes": {k: v.__dict__ for k, v in sorted(nodes.items()) if v.project_dir in dirs},
            "findings": [f.as_dict() for f in scoped],
            "written": [_rel(p, vault) for p in written],
        }, ensure_ascii=False, indent=2))
    else:
        n_in = sum(1 for v in nodes.values() if v.project_dir in dirs)
        print(f"{TOOL}: {n_in} nodos, "
              f"{sum(f.severity == 'crítico' for f in scoped)} crítico, "
              f"{sum(f.severity == 'importante' for f in scoped)} importante")
        for f in scoped:
            print(f"  [{f.severity}] {f.node}: {f.detail}")
        for p in written:
            print(f"  escrito {_rel(p, vault)}")
    return 2 if any(f.severity == "crítico" for f in scoped) else 0


if __name__ == "__main__":
    sys.exit(main())
