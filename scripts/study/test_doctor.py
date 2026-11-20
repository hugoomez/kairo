"""Tests for doctor.py. Run: python -m pytest scripts/study

An invented vault and invented datasets in a temp dir. The environment, the
commands (git, manage-bde, fdesetup, lsblk), free space and stat are injected:
nothing depends on this machine, nothing touches the network, no dataset is read."""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import doctor as dr  # noqa: E402

GB = 1024 ** 3


class FakeRun:
    """Answers by program name (and git subcommand); records every call."""

    def __init__(self, answers: dict | None = None):
        self.answers = answers or {}
        self.calls: list[list[str]] = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        key = argv[0]
        if key == "git":
            key = "git " + next(a for a in argv[3:] if not a.startswith("-"))
        ans = self.answers.get(key, (127, f"{argv[0]}: no encontrado"))
        return ans(argv) if callable(ans) else ans


class FakeStat:
    def __init__(self, mode):
        self.st_mode = mode


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-doctor-")).resolve()
        self.vault = self.tmp / "vault"
        self.home = self.tmp / "home"
        self.data = self.tmp / "datos"
        self.pdir = self.vault / "Projects" / "demo"
        (self.pdir / "Datos").mkdir(parents=True)
        (self.vault / ".git").mkdir()
        self.home.mkdir()
        self.data.mkdir()
        self.hub()
        self.registry([])
        self.environ = {"KAIRO_VAULT": str(self.vault)}
        self.runner = FakeRun()
        self.usage = (1000 * GB, 100 * GB, 900 * GB)
        self.platform = "linux"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def hub(self, **extra):
        lines = ["---", "id: PROJ-901", "template: medicion"] + [f"{k}: {v}" for k, v in extra.items()] + ["---", ""]
        (self.pdir / "_hub.md").write_text("\n".join(lines) + "# Proyecto inventado\n", encoding="utf-8")

    def registry(self, entries):
        (self.pdir / "Datos" / "registro.json").write_text(json.dumps(entries), encoding="utf-8")

    def dataset(self, where: Path, name="muestra-inventada", sensitive=True, kind="dir", create=True):
        if create:
            if kind == "dir":
                where.mkdir(parents=True, exist_ok=True)
            else:
                where.parent.mkdir(parents=True, exist_ok=True)
                where.write_text("x", encoding="utf-8")
        return {"name": name, "path": str(where), "kind": kind, "files": 1, "bytes": 1, "sha256": "0" * 64,
                "registered_at": "2031-01-01T00:00:00Z", "by": "tester", "sensitive": sensitive}

    def ctx(self, **over):
        kw = dict(environ=self.environ, home=self.home, platform=self.platform, run=self.runner,
                  disk_usage=lambda p: self.usage, mount_of=lambda p: Path("/data"))
        kw.update(over)
        return dr.Ctx(**kw)

    def result(self, **over):
        return dr.run(self.vault, "demo", self.ctx(**over))

    def of(self, res, cid):
        return [c for c in res["checks"] if c["id"] == cid]

    def sev(self, res, cid):
        return sorted({c["severity"] for c in self.of(res, cid)})


class Datasets(Base):
    def test_no_registry_entries_is_ok(self):
        self.assertEqual(self.sev(self.result(), "datos_ubicacion"), ["ok"])

    def test_good_dataset_ok(self):
        self.registry([self.dataset(self.data / "ds1")])
        self.assertEqual(self.sev(self.result(), "datos_ubicacion"), ["ok"])
        self.assertEqual(self.runner.calls[:1], [["lsblk", "-J", "-o", "NAME,TYPE,MOUNTPOINT"]])

    def test_missing_dataset(self):
        self.registry([self.dataset(self.data / "falta", create=False)])
        c = self.of(self.result(), "datos_ubicacion")
        self.assertEqual([x["severity"] for x in c], ["importante"])
        self.assertIn("no existe", c[0]["message"])

    def test_inside_vault(self):
        self.registry([self.dataset(self.vault / "crudo", name="s"), self.dataset(self.vault / "otro", name="n",
                                                                                    sensitive=False)])
        sev = {c["target"]: c["severity"] for c in self.of(self.result(), "datos_ubicacion")}
        self.assertEqual(sev, {"s": "critico", "n": "importante"})

    def test_unreadable_registry_is_critico(self):
        (self.pdir / "Datos" / "registro.json").write_text("{no es json", encoding="utf-8")
        res = self.result()
        self.assertEqual(self.sev(res, "registro"), ["critico"])
        self.assertEqual(self.of(res, "nube"), [])

    def test_relative_path(self):
        self.registry([{"name": "rel", "path": "datos/x", "sensitive": False}])
        self.assertEqual(self.sev(self.result(), "datos_ubicacion"), ["importante"])
        self.registry([{"name": "rel", "path": "datos/x", "sensitive": True}])
        self.assertEqual(self.sev(self.result(), "registro"), ["critico"])


class GitTrees(Base):
    def setUp(self):
        super().setUp()
        self.repo = self.tmp / "codigo"
        (self.repo / ".git").mkdir(parents=True)
        self.hub(code_repo=str(self.repo))

    def answer(self, ignored_rc, tracked=""):
        self.runner.answers["git check-ignore"] = (ignored_rc, "")
        self.runner.answers["git ls-files"] = (0, tracked)

    def test_under_code_repo_not_ignored_is_critico(self):
        self.registry([self.dataset(self.repo / "salida" / "ds")])
        self.answer(1)
        c = self.of(self.result(), "gitignore")
        self.assertEqual([x["severity"] for x in c], ["critico"])
        self.assertIn("repositorio de código del proyecto", c[0]["message"])
        self.assertIn(["git", "-C", str(self.repo), "check-ignore", "-q", "--", str(self.repo / "salida" / "ds")],
                      self.runner.calls)

    def test_under_code_repo_ignored_is_importante(self):
        self.registry([self.dataset(self.repo / "salida" / "ds")])
        self.answer(0)
        res = self.result()
        self.assertEqual(self.of(res, "gitignore"), [])
        self.assertEqual(self.sev(res, "datos_ubicacion"), ["importante"])

    def test_tracked_files_are_counted_never_printed(self):
        self.registry([self.dataset(self.repo / "ds", name="ds-inventado")])
        self.answer(1, "ds/nombre-secreto-inventado.csv\0ds/b.csv\0")
        c = self.of(self.result(), "gitignore")
        self.assertEqual(c[0]["severity"], "critico")
        self.assertIn("2 archivo(s)", c[0]["message"])
        self.assertNotIn("nombre-secreto-inventado", json.dumps(c, ensure_ascii=False))

    def test_other_git_tree_non_sensitive(self):
        other = self.tmp / "otro-repo"
        (other / ".git").mkdir(parents=True)
        self.registry([self.dataset(other / "ds", sensitive=False)])
        self.answer(1)
        c = self.of(self.result(), "gitignore")
        self.assertEqual(c[0]["severity"], "importante")
        self.assertIn("árbol de trabajo git", c[0]["message"])

    def test_git_unavailable_is_not_critico(self):
        self.registry([self.dataset(self.repo / "ds")])
        self.assertEqual(self.sev(self.result(), "gitignore"), ["importante"])

    def test_worktree_git_file_counts(self):
        wt = self.tmp / "wt"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: x", encoding="utf-8")
        self.registry([self.dataset(wt / "a" / "f.csv", kind="file")])
        self.answer(1)
        self.assertEqual(self.sev(self.result(), "gitignore"), ["critico"])


class Cloud(Base):
    def test_onedrive_env(self):
        od = self.tmp / "Nube"
        self.environ["OneDrive"] = str(od)
        self.registry([self.dataset(od / "ds")])
        c = self.of(self.result(), "nube")
        self.assertEqual(c[0]["severity"], "critico")
        self.assertIn("OneDrive", c[0]["message"])

    def test_folder_names(self):
        for folder, svc in (("OneDrive - Org Inventada", "OneDrive"), ("Dropbox", "Dropbox"),
                            ("My Drive", "Google Drive"), ("iCloud Drive", "iCloud")):
            with self.subTest(folder=folder):
                self.registry([self.dataset(self.tmp / folder / "ds")])
                c = self.of(self.result(), "nube")
                self.assertEqual(c[0]["severity"], "critico")
                self.assertIn(svc, c[0]["message"])

    def test_dropbox_info_json(self):
        box = self.tmp / "cajita"
        (self.home / ".dropbox").mkdir()
        (self.home / ".dropbox" / "info.json").write_text(json.dumps({"personal": {"path": str(box)}}),
                                                           encoding="utf-8")
        self.registry([self.dataset(box / "ds")])
        self.assertEqual(self.sev(self.result(), "nube"), ["critico"])

    def test_non_sensitive_in_cloud_and_local_sensitive_ok(self):
        self.registry([self.dataset(self.tmp / "Dropbox" / "ds", sensitive=False),
                       self.dataset(self.data / "ds", name="s")])
        self.assertEqual(self.sev(self.result(), "nube"), ["ok"])


LSBLK_CRYPT = json.dumps({"blockdevices": [{"name": "sda", "type": "disk", "mountpoint": None, "children": [
    {"name": "sda1", "type": "part", "mountpoint": "/boot"},
    {"name": "sda2", "type": "part", "mountpoint": None, "children": [
        {"name": "luks-inv", "type": "crypt", "mountpoint": None, "children": [
            {"name": "vg-data", "type": "lvm", "mountpoint": "/data"}]}]}]}]})
LSBLK_PLAIN = json.dumps({"blockdevices": [{"name": "sdb", "type": "disk", "mountpoint": None, "children": [
    {"name": "sdb1", "type": "part", "mountpoints": ["/data"]}]}]})


class Encryption(Base):
    def setUp(self):
        super().setUp()
        self.registry([self.dataset(self.data / "ds")])

    def test_no_sensitive_ok(self):
        self.registry([self.dataset(self.data / "ds", sensitive=False)])
        self.assertEqual(self.sev(self.result(), "cifrado"), ["ok"])

    def test_linux_crypt(self):
        self.runner.answers["lsblk"] = (0, LSBLK_CRYPT)
        self.assertEqual(self.sev(self.result(), "cifrado"), ["ok"])

    def test_linux_plain_is_importante(self):
        self.runner.answers["lsblk"] = (0, LSBLK_PLAIN)
        c = self.of(self.result(), "cifrado")
        self.assertEqual(c[0]["severity"], "importante")
        self.assertIn("no está sobre dm-crypt", c[0]["message"])

    def test_linux_lsblk_missing_or_mount_unknown(self):
        self.assertEqual(self.sev(self.result(), "cifrado"), ["importante"])
        self.runner.answers["lsblk"] = (0, LSBLK_CRYPT)
        self.assertEqual(self.sev(self.result(mount_of=lambda p: Path("/otro")), "cifrado"), ["importante"])

    def test_windows_bitlocker(self):
        drive = (self.data / "ds").drive or "C:"
        if not (self.data / "ds").drive:
            self.skipTest("las rutas temporales de este sistema no llevan letra de unidad")
        cases = [((0, "Conversion Status: Fully Encrypted\nProtection Status: Protection On"), "ok"),
                 ((0, "Estado de conversión: Cifrado completo\nEstado de protección: Protección activada"), "ok"),
                 ((0, "Conversion Status: Fully Encrypted\nProtection Status: Protection Off"), "importante"),
                 ((0, "Conversion Status: Fully Decrypted\nProtection Status: Protection Off"), "critico"),
                 ((-1, "ERROR: An attempt to access a required resource was denied."), "importante"),
                 ((0, "salida inesperada"), "importante")]
        for ans, sev in cases:
            with self.subTest(ans=ans):
                self.runner.answers["manage-bde"] = ans
                self.assertEqual(self.sev(self.result(platform="win32"), "cifrado"), [sev])
        self.assertIn(["manage-bde", "-status", drive.upper()], self.runner.calls)

    def test_windows_failure_explains_manual_check(self):
        if not (self.data / "ds").drive:
            self.skipTest("las rutas temporales de este sistema no llevan letra de unidad")
        c = self.of(self.result(platform="win32"), "cifrado")[0]
        self.assertEqual(c["severity"], "importante")
        self.assertIn("no se pudo comprobar", c["message"].casefold())
        self.assertIn("manage-bde -status", c["fix"])

    def test_macos(self):
        for ans, sev in (((0, "FileVault is On."), "ok"), ((0, "FileVault is Off."), "critico"),
                         ((1, "error"), "importante")):
            with self.subTest(ans=ans):
                self.runner.answers["fdesetup"] = ans
                self.assertEqual(self.sev(self.result(platform="darwin"), "cifrado"), [sev])

    def test_macos_external_volume(self):
        ctx = self.ctx(platform="darwin")
        p = Path("/Volumes/ExtInventado/ds")
        self.runner.answers["diskutil"] = (0, "   FileVault:   Yes\n")
        self.assertEqual(dr._filevault(p, ctx)[0], "ok")
        self.runner.answers["diskutil"] = (0, "   FileVault:   No\n")
        self.assertEqual(dr._filevault(p, ctx)[0], "critico")
        self.runner.answers["diskutil"] = (1, "")
        self.assertEqual(dr._filevault(p, ctx)[0], "importante")

    def test_one_check_per_volume(self):
        self.registry([self.dataset(self.data / "a", name="a"), self.dataset(self.data / "b", name="b")])
        self.runner.answers["lsblk"] = (0, LSBLK_CRYPT)
        c = self.of(self.result(), "cifrado")
        self.assertEqual(len(c), 1)
        self.assertIn("a, b", c[0]["message"])


class Token(Base):
    def token(self, where: Path):
        where.parent.mkdir(parents=True, exist_ok=True)
        where.write_text("tok-inventado-no-real", encoding="utf-8")
        return where

    def test_default_missing_is_menor(self):
        c = [x for x in self.of(self.result(), "token_github") if x["severity"] != "ok"]
        self.assertEqual([x["severity"] for x in c], ["menor"])

    def test_explicit_missing_is_importante(self):
        self.environ["KAIRO_GITHUB_TOKEN_FILE"] = str(self.tmp / "no-existe")
        self.assertIn("importante", self.sev(self.result(), "token_github"))

    def test_good_token_ok_and_never_read(self):
        p = self.token(self.home / ".kairo" / "github-token")
        res = self.result(stat=lambda q: FakeStat(0o100600))
        self.assertEqual(self.sev(res, "token_github"), ["ok"])
        self.assertNotIn("tok-inventado", json.dumps(res, ensure_ascii=False))
        self.assertTrue(p.is_file())

    def test_world_readable_posix(self):
        self.token(self.home / ".kairo" / "github-token")
        c = [x for x in self.of(self.result(stat=lambda q: FakeStat(0o100644)), "token_github")
             if x["severity"] != "ok"]
        self.assertEqual(len(c), 1)
        self.assertIn("chmod 600", c[0]["fix"])
        # Windows has no POSIX bits to check
        res = self.result(platform="win32", stat=lambda q: FakeStat(0o100666))
        self.assertEqual(self.sev(res, "token_github"), ["ok"])

    def test_inside_vault_is_critico(self):
        self.environ["KAIRO_GITHUB_TOKEN_FILE"] = str(self.token(self.vault / "token"))
        self.assertIn("critico", self.sev(self.result(stat=lambda q: FakeStat(0o100600)), "token_github"))

    def test_inside_git_tree(self):
        repo = self.tmp / "dotfiles"
        (repo / ".git").mkdir(parents=True)
        self.environ["KAIRO_GITHUB_TOKEN_FILE"] = str(self.token(repo / "token"))
        self.runner.answers["git check-ignore"] = (1, "")
        self.runner.answers["git ls-files"] = (0, "")
        self.assertIn("critico", self.sev(self.result(stat=lambda q: FakeStat(0o100600)), "token_github"))
        self.runner.answers["git check-ignore"] = (0, "")
        self.assertEqual(self.sev(self.result(stat=lambda q: FakeStat(0o100600)), "token_github"),
                         ["importante", "ok"])

    def test_tilde_and_synced(self):
        self.environ["KAIRO_GITHUB_TOKEN_FILE"] = "~/OneDrive/token"
        self.token(self.home / "OneDrive" / "token")
        c = [x for x in self.of(self.result(stat=lambda q: FakeStat(0o100600)), "token_github")
             if x["severity"] != "ok"]
        self.assertEqual([x["severity"] for x in c], ["importante"])
        self.assertIn("sincronizada", c[0]["message"])

    def test_env_tokens_are_critico_and_never_printed(self):
        self.environ["GITHUB_TOKEN"] = "ghp_inventado123"
        self.environ["GH_TOKEN"] = "ghp_inventado456"
        res = self.result()
        crit = [c for c in self.of(res, "token_github") if c["severity"] == "critico"]
        self.assertEqual(sorted(c["target"] for c in crit), ["GH_TOKEN", "GITHUB_TOKEN"])
        self.assertNotIn("inventado123", json.dumps(res))
        self.assertNotIn("inventado456", json.dumps(res))


class KairoVault(Base):
    def test_set_and_equal(self):
        self.assertEqual(self.sev(self.result(), "kairo_vault"), ["ok"])

    def test_unset(self):
        del self.environ["KAIRO_VAULT"]
        self.assertEqual(self.sev(self.result(), "kairo_vault"), ["menor"])
        self.registry([self.dataset(self.data / "ds")])
        self.assertEqual(self.sev(self.result(), "kairo_vault"), ["importante"])

    def test_other_vault(self):
        self.environ["KAIRO_VAULT"] = str(self.tmp / "otro-vault")
        self.registry([self.dataset(self.data / "ds")])
        c = self.of(self.result(), "kairo_vault")
        self.assertEqual(c[0]["severity"], "importante")
        self.assertIn("no a este vault", c[0]["message"])


class Disk(Base):
    def setUp(self):
        super().setUp()
        self.registry([self.dataset(self.data / "ds")])

    def test_plenty_ok(self):
        self.assertEqual(self.sev(self.result(), "disco"), ["ok"])

    def test_under_ten_percent(self):
        self.usage = (1000 * GB, 950 * GB, 50 * GB)
        self.assertEqual(self.sev(self.result(), "disco"), ["menor"])

    def test_under_budget(self):
        self.hub(presupuesto_disco_gb=1000)
        c = self.of(self.result(), "disco")
        self.assertEqual(c[0]["severity"], "importante")
        self.assertIn("1000 GB", c[0]["message"])
        self.hub(presupuesto_disco_gb=500)
        self.assertEqual(self.sev(self.result(), "disco"), ["ok"])

    def test_budget_not_a_number_and_placeholder(self):
        self.hub(presupuesto_disco_gb="mucho")
        self.assertEqual(self.sev(self.result(), "disco"), ["menor", "ok"])
        self.hub(presupuesto_disco_gb="<value>")
        self.assertEqual(self.sev(self.result(), "disco"), ["ok"])

    def test_missing_dataset_uses_existing_parent(self):
        seen = []
        self.registry([self.dataset(self.data / "aun-no" / "ds", create=False)])
        self.result(disk_usage=lambda p: seen.append(p) or self.usage)
        self.assertEqual(seen, [self.data])


class EthicsAndNotices(Base):
    def plan(self, status):
        (self.pdir / "Mediciones").mkdir(exist_ok=True)
        (self.pdir / "Mediciones" / "M-9001.md").write_text(
            f"---\nid: M-9001\nquestion: Q-9001\nstatus: {status}\n---\n\n# Plan inventado\n", encoding="utf-8")

    def test_missing_ethics(self):
        self.assertEqual(self.sev(self.result(), "etica"), ["importante"])

    def test_local_ethics(self):
        (self.pdir / "Etica.md").write_text("# Ética inventada\n", encoding="utf-8")
        self.assertEqual(self.sev(self.result(), "etica"), ["ok"])

    def test_hub_ethics_path(self):
        (self.vault / "Comun").mkdir()
        (self.vault / "Comun" / "etica-comun.md").write_text("# inventada\n", encoding="utf-8")
        self.hub(etica="Comun/etica-comun")
        self.assertEqual(self.sev(self.result(), "etica"), ["ok"])
        self.hub(etica="Comun/no-existe.md")
        c = self.of(self.result(), "etica")
        self.assertEqual(c[0]["severity"], "importante")
        self.assertIn("no existe", c[0]["message"])

    def test_avisos_after_freeze(self):
        self.plan("borrador")
        self.assertEqual(self.of(self.result(), "avisos"), [])
        self.plan("congelado")
        c = self.of(self.result(), "avisos")
        self.assertEqual(c[0]["severity"], "menor")
        self.assertIn("M-9001", c[0]["message"])
        (self.pdir / "Avisos").mkdir()
        (self.pdir / "Avisos" / "registro.json").write_text("[]", encoding="utf-8")
        self.assertEqual(self.sev(self.result(), "avisos"), ["ok"])


class Cli(Base):
    def main(self, *extra, **over):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = dr.main(["--vault", str(self.vault), "--project", "demo", *extra], ctx=self.ctx(**over))
        return code, out.getvalue(), err.getvalue()

    def test_exit_zero_without_critico(self):
        (self.pdir / "Etica.md").write_text("# inventada\n", encoding="utf-8")
        code, out, _ = self.main("--json")
        res = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(res["counts"]["critico"], 0)
        self.assertEqual(res["project"], "PROJ-901")
        for c in res["checks"]:
            self.assertEqual(set(c), {"id", "severity", "target", "message", "fix"})
            self.assertIn(c["severity"], dr.SEVERITIES)

    def test_exit_four_with_critico_and_text_output(self):
        self.environ["GH_TOKEN"] = "inventado"
        code, out, _ = self.main()
        self.assertEqual(code, 4)
        self.assertIn("[critico]", out)
        self.assertIn("→", out)

    def test_project_by_id(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = dr.main(["--vault", str(self.vault), "--project", "PROJ-901", "--json"], ctx=self.ctx())
        self.assertIn(code, (0, 4))
        self.assertEqual(json.loads(out.getvalue())["slug"], "demo")

    def test_refused_in_literature_profile(self):
        self.environ["KAIRO_PROFILE"] = "literatura"
        code, out, _ = self.main("--json")
        self.assertEqual(code, 3)
        self.assertTrue(json.loads(out)["refused"])

    def test_errors(self):
        code, _, err = self.main()
        self.assertIn(code, (0, 4))
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(dr.main(["--vault", str(self.tmp / "nada"), "--project", "demo"], ctx=self.ctx()), 1)
            self.assertEqual(dr.main(["--vault", str(self.vault), "--project", "otro"], ctx=self.ctx()), 1)
            self.assertEqual(dr.main(["--vault", str(self.vault)], ctx=self.ctx()), 1)

    def test_default_ctx_runs(self):
        """The real Ctx wires up without crashing (its findings depend on the machine and are not checked)."""
        env = {k: v for k, v in os.environ.items()}
        ctx = dr.Ctx(environ=env, home=self.home)
        res = dr.run(self.vault, "demo", ctx)
        self.assertEqual(res["slug"], "demo")


if __name__ == "__main__":
    unittest.main()
