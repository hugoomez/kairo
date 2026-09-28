#!/usr/bin/env python3
"""Create or link an applied project's code repository, and guard it.

    repo_setup.py create --repo PATH --vault V --project-dir D --name "..." [--language python]
    repo_setup.py link   --repo PATH --vault V --project-dir D

Both refuse a path inside the vault (or one containing it), and both install
the pre-push guard (repo_guard.py install). `create` makes a new git
repository with a CONVENTIONS.md (the standards the repo-steward skill holds
the code to), a .gitignore that keeps secrets and local data out, and a README
that says where the science lives (the vault, which is never pushed). Neither
creates a remote: that is a separate, approved step.

Exit codes: 0 ok · 3 refused · 1 error. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import repo_guard  # noqa: E402

CONVENTIONS = {
    "python": """# Convenciones del repositorio

Lo que el skill `kairo:repo-steward` comprueba en cada revisión de salud.

- **Lenguaje:** Python 3.11+, con `pyproject.toml`.
- **Formato y lint:** `ruff format` y `ruff check` sin errores.
- **Tipos:** `mypy --strict` en `src/` (o lo que este archivo diga cuando lo cambies).
- **Tests:** `pytest`; cada módulo nuevo con sus tests. Cobertura mínima: 80 %.
- **CI:** GitHub Actions ejecuta lint, tipos y tests en cada push y PR.
- **Commits:** cada cambio motivado por una hipótesis o experimento lo dice en un
  trailer `Motivated-By: H-XXXX` (o `E-XXXX`); así se construye la traza código↔ciencia.
- **Decisiones de arquitectura:** como ADR en el vault (`Producto/ADR-XXX.md`), nunca aquí.
- **Nunca** en este repositorio: texto de papers, hipótesis, notas, bitácora ni datos del
  vault. El guardia `pre-push` lo bloquea.
""",
}

GITIGNORE = """# secrets and local config — never committed
.env
.env.*
*.pem
*.key
kaggle.json
# local data and outputs
data/
outputs/
.venv/
__pycache__/
.pytest_cache/
.mypy_cache/
.ruff_cache/
"""


class Refused(Exception):
    pass


def check_location(repo: Path, vault: Path) -> None:
    if repo_guard.inside(repo, vault):
        raise Refused(f"the code repository must be outside the vault: {repo}")
    if repo_guard.inside(vault, repo):
        raise Refused("the code repository must not contain the vault")


def install_guard(repo: Path, vault: Path, project_dir: Path) -> dict:
    code = repo_guard.main(["install", "--repo", str(repo), "--vault", str(vault), "--project-dir", str(project_dir)])
    if code != 0:
        raise Refused("could not install the pre-push guard")
    return {"hook": str(repo / ".git" / "hooks" / "pre-push")}


def create(repo: Path, vault: Path, project_dir: Path, name: str, language: str) -> dict:
    repo = repo.resolve()
    check_location(repo, vault.resolve())
    if repo.exists() and any(repo.iterdir()):
        raise Refused(f"{repo} exists and is not empty — use link for an existing repository")
    if language not in CONVENTIONS:
        raise Refused(f"language must be one of {sorted(CONVENTIONS)}")
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "CONVENTIONS.md").write_text(CONVENTIONS[language], encoding="utf-8", newline="\n")
    (repo / ".gitignore").write_text(GITIGNORE, encoding="utf-8", newline="\n")
    (repo / "README.md").write_text(
        f"# {name}\n\nCódigo del proyecto aplicado. Las preguntas científicas, hipótesis y experimentos viven en el "
        "vault de Kairo (privado, nunca publicado); aquí solo hay código. Ver `CONVENTIONS.md`.\n",
        encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "Repositorio inicial (Kairo, plantilla aplicado)"], cwd=repo, check=True)
    return {"repo": str(repo), "created": True, **install_guard(repo, vault.resolve(), project_dir.resolve())}


def link(repo: Path, vault: Path, project_dir: Path) -> dict:
    repo = repo.resolve()
    check_location(repo, vault.resolve())
    if not (repo / ".git").is_dir():
        raise Refused(f"{repo} is not a git repository")
    return {"repo": str(repo), "created": False, **install_guard(repo, vault.resolve(), project_dir.resolve())}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("create", "link"):
        p = sub.add_parser(name)
        p.add_argument("--repo", required=True, type=Path)
        p.add_argument("--vault", required=True, type=Path)
        p.add_argument("--project-dir", required=True, type=Path)
        if name == "create":
            p.add_argument("--name", required=True)
            p.add_argument("--language", default="python")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        r = (create(a.repo, a.vault, a.project_dir, a.name, a.language) if a.cmd == "create"
             else link(a.repo, a.vault, a.project_dir))
        print(json.dumps(r, ensure_ascii=False))
        return 0
    except (Refused, repo_guard.Refused) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
