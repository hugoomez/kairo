"""Tests for artifact.py. Run: python -m pytest scripts/study/test_artifact.py

An invented vault (with git), an invented code repository and invented datasets in a
temp dir; no network. Key material comes from fake_material."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "security"))
import artifact as ar  # noqa: E402
import fake_material as fm  # noqa: E402

DATASET_ROW = '{"repo": "org-inventada/uno", "path": "a.yaml", "kind": "clave"}\n' * 4


def git(cwd, *a):
    r = subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    git(path, "config", "user.email", "autora@example.org")
    git(path, "config", "user.name", "Autora Inventada")
    git(path, "config", "commit.gpgsign", "false")
    git(path, "config", "core.hooksPath", str(path / "no-hooks"))


PLAN = """---
id: M-0001
project: PROJ-901
question: Q-0001
status: congelado
frozen_at: {at}
frozen_commit: {sha}
---

# M-0001 — Plan de medición de Q-0001

## Estimandos

Proporción inventada de unidades con la característica X.
{extra}"""

QUESTION = """---
id: Q-{n}
project: PROJ-901
design: medicion
status: {status}
---

# Q-{n}

## Resultado

```estimate-json
{{"plan": "M-0001", "estimates": [{{"id": "est-1", "value": 0.25}}]}}
```
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-artifact-")).resolve()
        self.vault = self.tmp / "vault"
        self.pdir = self.vault / "Projects" / "demo"
        for sub in ("Datos", "Mediciones", "Preguntas", "Figuras", "Avisos"):
            (self.pdir / sub).mkdir(parents=True)
        init(self.vault)
        (self.pdir / "_hub.md").write_text("---\nid: PROJ-901\nname: Demo inventado\ntemplate: medicion\n"
                                           "autores: [Autora Inventada, handle-inventado]\n---\n", encoding="utf-8")
        plan = self.pdir / "Mediciones" / "M-0001.md"
        plan.write_text(PLAN.format(at="", sha="", extra=""), encoding="utf-8")
        git(self.vault, "add", "-A")
        git(self.vault, "commit", "-qm", "plan")
        self.sha = git(self.vault, "rev-parse", "HEAD")
        plan.write_text(PLAN.format(at="2031-01-01T00:00:00Z", sha=self.sha,
                                    extra="\n## Enmiendas\n\n- 2031-02-01: enmienda inventada.\n"), encoding="utf-8")
        (self.pdir / "Preguntas" / "Q-0001.md").write_text(QUESTION.format(n="0001", status="estimada"),
                                                           encoding="utf-8")
        (self.pdir / "Preguntas" / "Q-0002.md").write_text(QUESTION.format(n="0002", status="en_recoleccion"),
                                                           encoding="utf-8")
        (self.pdir / "Figuras" / "fig1.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>\n",
                                                        encoding="utf-8")
        (self.pdir / "Figuras" / "F-003-inventada.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        (self.pdir / "Figuras" / "registro.json").write_text(json.dumps([
            {"id": "F-1", "path": "fig1.svg", "titulo": "Figura inventada"},
            {"id": "F-003", "path": "Figuras/F-003-inventada.png", "caption": "como la escribe figures.py"},
            {"id": "F-2", "path": "../Datos/x.svg"}]), encoding="utf-8")
        (self.pdir / "Avisos" / "registro.json").write_text("[]", encoding="utf-8")
        self.data = self.tmp / "datos"
        (self.data / "dir").mkdir(parents=True)
        (self.data / "hallazgos.jsonl").write_text(DATASET_ROW, encoding="utf-8")
        (self.data / "dir" / "muestra.jsonl").write_text(DATASET_ROW.replace("uno", "dos"), encoding="utf-8")
        (self.pdir / "Datos" / "registro.json").write_text(json.dumps([
            {"name": "hallazgos", "path": str(self.data / "hallazgos.jsonl"), "kind": "file", "sensitive": True},
            {"name": "crudos", "path": str(self.data / "dir"), "kind": "dir"}]), encoding="utf-8")
        self.repo = self.tmp / "codigo"
        init(self.repo)
        self.commit_code({"collect.py": "import json\nprint(json.dumps({'n': 1}))\n",
                          "README.md": "Collection code. Tool studied: https://github.com/tool-inventada/herramienta\n"})
        self.out = self.tmp / "paquete"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def commit_code(self, files: dict[str, str]):
        for rel, text in files.items():
            p = self.repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "code")

    def allow(self, *lines):
        p = self.tmp / "allow.txt"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(p)

    def cli(self, *argv, environ=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = ar.main(list(argv), environ=environ or {})
        text = out.getvalue()
        return code, (json.loads(text) if text.strip().startswith("{") else None), text, err.getvalue()

    def build(self, *extra, environ=None):
        return self.cli("build", "--vault", str(self.vault), "--project", "demo", "--repo", str(self.repo),
                        "--out", str(self.out), *extra, environ=environ)


class BuildTests(Base):
    def test_clean_package(self):
        code, data, raw, err = self.build("--include-aggregates", "--allow-urls",
                                          self.allow("# tools studied", "tool-inventada/herramienta"))
        self.assertEqual(code, 0, raw + err)
        self.assertTrue(data["publicable"])
        self.assertEqual(data["planes"], ["M-0001"])
        self.assertEqual(data["agregados"], ["Q-0001"])
        self.assertEqual(data["figuras"], 2)
        self.assertTrue(any("fig" in w or "figura" in w for w in data["avisos"]))
        o = self.out
        self.assertTrue((o / "codigo" / "collect.py").is_file())
        self.assertFalse((o / "codigo" / ".git").exists())
        frozen = (o / "planes" / "M-0001.md").read_text(encoding="utf-8")
        self.assertNotIn("Enmiendas", frozen)
        self.assertIn("enmienda inventada", (o / "planes" / "M-0001.enmiendas.md").read_text(encoding="utf-8"))
        agg = json.loads((o / "agregados" / "Q-0001.json").read_text(encoding="utf-8"))
        self.assertEqual(agg["estimates"][0]["value"], 0.25)
        self.assertFalse((o / "agregados" / "Q-0002.json").exists())
        self.assertTrue((o / "figuras" / "fig1.svg").is_file())
        figs = json.loads((o / "figuras" / "registro.json").read_text(encoding="utf-8"))
        self.assertEqual(figs, [{"id": "F-1", "titulo": "Figura inventada", "path": "fig1.svg"},
                                {"id": "F-003", "caption": "como la escribe figures.py", "path": "F-003-inventada.png"}])
        self.assertTrue((o / "figuras" / "F-003-inventada.png").is_file())
        self.assertIn("fixtures_corpus.py", (o / "fixtures" / "GENERAR.md").read_text(encoding="utf-8"))
        readme = (o / "README.md").read_text(encoding="utf-8")
        head = git(self.repo, "rev-parse", "HEAD")
        self.assertIn(head, readme)
        self.assertIn("What is withheld", readme)
        manifest = json.loads((o / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["code_head"], head)
        self.assertEqual(manifest["plans"][0]["frozen_commit"], self.sha)
        names = {f["path"] for f in manifest["files"]}
        self.assertIn("codigo/collect.py", names)
        all_paths = [p.relative_to(o).as_posix().casefold() for p in o.rglob("*")]
        self.assertFalse(any("avisos" in p or "anotacion" in p or "datos" in p for p in all_paths), all_paths)
        self.assertNotIn(str(self.vault).casefold(), (o / "MANIFEST.json").read_text(encoding="utf-8").casefold())

    def test_aggregates_only_on_request_and_custom_fixtures(self):
        code, data, raw, _ = self.build("--allow-urls", self.allow("tool-inventada/*"),
                                        "--fixtures-cmd", "python tests/make_fixtures.py --seed 7")
        self.assertEqual(code, 0, raw)
        self.assertEqual(data["agregados"], [])
        self.assertFalse((self.out / "agregados").exists())
        self.assertIn("make_fixtures.py --seed 7", (self.out / "README.md").read_text(encoding="utf-8"))

    def test_leaks_are_reported_without_their_text(self):
        key = fm.age_secret()
        self.commit_code({
            "tests/data/copia.jsonl": DATASET_ROW,
            "tests/data/otra.jsonl": DATASET_ROW.replace("uno", "dos"),
            "config.py": f"DATA = r'{self.data / 'dir'}'\n",
            "repos.txt": "https://github.com/org-inventada/afectado\ngit@github.com:org-inventada/otro.git\n"
                         "https://github.com/features/actions\n",
            "key.txt": key + "\n"})
        code, data, raw, _ = self.build("--allow-urls", self.allow("tool-inventada/herramienta"))
        self.assertEqual(code, 4)
        self.assertFalse(data["publicable"])
        kinds = {(lk["kind"], lk["path"]) for lk in data["fugas"]}
        self.assertIn(("dataset", "codigo/tests/data/copia.jsonl"), kinds)
        self.assertIn(("dataset", "codigo/tests/data/otra.jsonl"), kinds)
        self.assertIn(("dataset", "codigo/config.py"), kinds)
        self.assertIn(("secreto", "codigo/key.txt"), kinds)
        gh = sorted(lk["line"] for lk in data["fugas"] if lk["kind"] == "github")
        self.assertEqual(gh, [1, 2])
        self.assertNotIn("org-inventada", raw)
        self.assertNotIn(key, raw)
        self.assertNotIn(str(self.data), raw)
        # the github link of the studied tool is a leak without the allow-list
        shutil.rmtree(self.out)
        self.commit_code({"tests/data/copia.jsonl": "x" * 10, "tests/data/otra.jsonl": "y",
                          "config.py": "DATA = None\n", "repos.txt": "", "key.txt": ""})
        code, data, _, _ = self.build()
        self.assertEqual(code, 4)
        self.assertEqual([(lk["kind"], lk["path"]) for lk in data["fugas"]], [("github", "codigo/README.md")])

    def test_anonymous(self):
        self.commit_code({"LICENSE": "Copyright 2031 Autora Inventada\n", "NOTES.md": "by handle-inventado\n"})
        code, data, raw, _ = self.build("--anonymous", "--allow-urls", self.allow("tool-inventada/herramienta"))
        self.assertEqual(code, 4)
        self.assertEqual(sorted({lk["path"] for lk in data["fugas"] if lk["kind"] == "autor"}),
                         ["codigo/LICENSE", "codigo/NOTES.md"])
        self.assertNotIn("Autora", raw)
        head = git(self.repo, "rev-parse", "HEAD")
        self.assertNotIn(head, (self.out / "README.md").read_text(encoding="utf-8"))
        manifest = json.loads((self.out / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertIsNone(manifest["code_head"])
        self.assertIsNone(manifest["plans"][0]["frozen_commit"])
        self.assertNotIn(self.sha, (self.out / "MANIFEST.json").read_text(encoding="utf-8"))

    def test_out_dir_rules(self):
        for bad in (self.pdir / "paquete", self.repo / "paquete", "relativo"):
            code, data, _, _ = self.cli("build", "--vault", str(self.vault), "--project", "demo",
                                        "--repo", str(self.repo), "--out", str(bad))
            self.assertEqual(code, 3, bad)
        self.out.mkdir()
        (self.out / "x").write_text("x", encoding="utf-8")
        code, data, _, _ = self.build()
        self.assertEqual(code, 3)
        self.assertIn("vacía", data["error"])


class CheckTests(Base):
    def make(self, files: dict[str, str]):
        for rel, text in files.items():
            p = self.out / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")

    def check(self, *extra, environ=None):
        return self.cli("check", "--vault", str(self.vault), "--project", "demo", "--out", str(self.out), *extra,
                        environ=environ)

    def test_check_flags_git_avisos_and_annotation(self):
        self.make({"README.md": "ok\n", "extra/Avisos/registro.json": "[]\n",
                   "copia/Datos/anotacion/m/muestra.jsonl": "{}\n", "sub/.git/HEAD": "ref: x\n"})
        code, data, _, _ = self.check()
        self.assertEqual(code, 4)
        kinds = {lk["kind"] for lk in data["fugas"]}
        self.assertTrue({"git", "avisos", "anotacion"} <= kinds, kinds)

    def test_check_clean_then_anonymous(self):
        self.make({"README.md": "Artifact by Autora Inventada\n"})
        code, data, _, _ = self.check()
        self.assertEqual(code, 0, data)
        code, data, _, _ = self.check("--anonymous")
        self.assertEqual(code, 4)
        self.assertEqual(data["fugas"][0]["kind"], "autor")
        code, data, _, _ = self.check("--anonymous", environ={"KAIRO_AUTHORS": "Otra Persona"})
        self.assertEqual(code, 4)

    def test_anonymous_without_names_is_refused(self):
        (self.pdir / "_hub.md").write_text("---\nid: PROJ-901\nname: Demo\n---\n", encoding="utf-8")
        self.make({"README.md": "x\n"})
        code, data, _, _ = self.check("--anonymous")
        self.assertEqual(code, 3)
        self.assertIn("autores", data["error"])
        code, data, _, _ = self.check("--anonymous", environ={"KAIRO_AUTHORS": "Otra Persona; otro-handle"})
        self.assertEqual(code, 0, data)


if __name__ == "__main__":
    unittest.main()
