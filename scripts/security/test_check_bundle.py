"""Tests for check_bundle.py -- run with `python -m unittest` from this directory.

Every fake secret below is assembled AT RUNTIME (a provider prefix + a
deterministic pseudo-random body from `fake()`), never written as a literal
key-shaped string: committing a literal `AKIA...` / `ghp_...` / PEM header would
trip GitHub push protection and secret scanners on this repo. The values are
random noise, valid for nothing.
"""

import io
import json
import os
import random
import string
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "check_bundle.py"
sys.path.insert(0, str(HERE))

import check_bundle as cb  # noqa: E402

UPPER_DIGITS = string.ascii_uppercase + string.digits
ALNUM = string.ascii_letters + string.digits


def fake(n: int, alphabet: str = ALNUM, seed: int = 7) -> str:
    rng = random.Random(seed * 1000 + n)
    return "".join(rng.choice(alphabet) for _ in range(n))


# runtime-built secret material (see module docstring)
AWS = "AK" + "IA" + fake(16, UPPER_DIGITS, 1)
GH = "gh" + "p_" + fake(36, ALNUM, 2)
ANT = "sk-" + "ant-" + fake(40, ALNUM, 3)
HF = "h" + "f_" + fake(34, ALNUM, 4)
GOOG = "AI" + "za" + fake(35, ALNUM, 5)
GENERIC = fake(32, ALNUM, 6)
TOKVAL = fake(40, ALNUM, 8)
JWT = "ey" + "J" + fake(20, ALNUM, 9) + "." + "ey" + "J" + fake(24, ALNUM, 10) + "." + fake(30, ALNUM, 11)
URLPW = fake(24, ALNUM, 12)
PEM = ("-----BEGIN " + "OPENSSH PRIVATE" + " KEY-----\n" + fake(64, ALNUM, 13)
       + "\n-----END " + "OPENSSH PRIVATE" + " KEY-----\n")
ALL_SECRETS = [AWS, GH, ANT, HF, GOOG, GENERIC, TOKVAL, JWT, URLPW, PEM.splitlines()[1]]


def run_cli(*args):
    r = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True)
    return r.returncode, r.stdout, r.stderr


def write(root: Path, rel: str, content, binary=False):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if binary:
        p.write_bytes(content)
    else:
        p.write_text(content, encoding="utf-8")
    return p


def make_clean_bundle(root: Path):
    """A realistic clean experiment bundle (shape of Scripts/experiments/E-XXXX/)."""
    write(root, "E-9999.data.json", json.dumps({
        "experiment": "E-9999", "hypothesis": "H-9999",
        "files": ["train.csv", "data/val.csv"],
        "runs": [{"run_seed": 1, "train_idx_sha256": "ab" * 32}]}))
    write(root, "E-9999.deps.txt", "numpy==2.0.2\ntorch==2.10.0\nkaggle-environments==1.29.3\n")
    write(root, "spec.py", (
        '"""Frozen spec. Constants copied from the frozen preregistration."""\n'
        "EQ_TOKEN = 97\nMAX_TOKENS = 4096\nSEED = 20260908\n"
        "_RECORDED_SHA = \"" + "cd" * 32 + "\"\n"))
    write(root, "run.py", (
        "import os\nfrom pathlib import Path\n"
        "HERE = Path(__file__).resolve().parent\n"
        "api_key = os.environ['OPENAI_API_KEY']\n"
        "HF_TOKEN = os.getenv('HF_TOKEN')\n"
        "password = '${DB_PASSWORD}'\n"
        "secret_key = '<your-secret-key>'\n"
        "token = tokenizer_config.default_token\n"
        "access_key = 'changeme-changeme-changeme'\n"
        "URL = 'https://user:<token>@github.com/org/repo.git'\n"
        "# Authorization: Bearer $TOKEN\n"))
    write(root, ".env.example", "OPENAI_API_KEY=your-key-here\nHF_TOKEN=\n")
    write(root, "Tools/P-0002/method/tool/method.py", "def f(x):\n    return x + 1\n")
    write(root, "Tools/P-0002/method/TOOL.md", "---\npaper: P-0002\nmethod: method\nstatus: validated\n---\n# Tool\n")
    write(root, "README.md", "# E-9999\n\nRun `python run.py` from this directory.\n")
    write(root, "MANIFEST.sha256", "\n".join(f"{'0' * 64}  {n}" for n in
                                             ("E-9999.data.json", "E-9999.deps.txt", "run.py", "spec.py")) + "\n")


def zip_bytes(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def tar_gz_bytes(members: dict, links: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in members.items():
            b = data.encode() if isinstance(data, str) else data
            ti = tarfile.TarInfo(name)
            ti.size = len(b)
            tf.addfile(ti, io.BytesIO(b))
        for name, target in (links or {}).items():
            ti = tarfile.TarInfo(name)
            ti.type = tarfile.SYMTYPE
            ti.linkname = target
            tf.addfile(ti)
    return buf.getvalue()


# One (path, kind) per contamination; every one is asserted to be present, block.
CONTAMINATED = {
    ".env": ("env_file", "OPENAI_API_KEY=" + GENERIC + "\n"),
    "conf/.env.production": ("env_file", "MODE=prod\n"),
    "keys/deploy.pem": ("private_key", PEM),
    "home/id_ed25519": ("ssh_key_file", "opaque\n"),
    "home/.ssh/deploy_key": ("ssh_key_file", "opaque\n"),
    "certs/client.p12": ("keystore", b"\x30\x82\x00\x00binary"),
    ".git-credentials": ("git_credentials", "https://x:" + URLPW + "@example.org\n"),
    "cfg/.gitconfig": ("git_credentials", "[user]\n  name = a\n[credential]\n  helper = store\n"),
    "scripts/push.sh": ("credential_url", "git push https://bot:" + URLPW + "@github.com/o/r.git\n"),
    "aws.py": ("api_key", "KEY_ID = '" + AWS + "'\n"),
    "nested/deep/gh.txt": ("api_key", "token " + GH + "\n"),
    "anthropic.cfg": ("api_key", "value: " + ANT + "\n"),
    "hf.ipynb": ("api_key", json.dumps({"cells": [{"source": ["login('" + HF + "')"]}]})),
    "google.js": ("api_key", "const k = \"" + GOOG + "\";\n"),
    "settings.yaml": ("api_key", "service:\n  api_key: \"" + GENERIC + "\"\n"),
    "auth.py": ("token", "DEEPINFRA_TOKEN = '" + TOKVAL + "'\n"),
    "session.txt": ("token", JWT + "\n"),
    "http.sh": ("token", "curl -H 'Authorization: Bearer " + TOKVAL + "' https://api.example.org\n"),
    ".netrc": ("netrc", "machine example.org login a password b\n"),
    "kaggle.json": ("credentials_file", json.dumps({"username": "a", "key": GENERIC})),
    "Projects/demo/Experimentos/E-0001.md": ("vault_path", "copied note\n"),
    "extra/Papers/P-0001 note.md": ("vault_path", "copied note\n"),
    "paths.py": ("vault_absolute_path",
                 "DATA = r'C:\\Users\\someone\\Kairo\\vault\\Scripts\\experiments\\E-0001\\x.json'\n"),
    "notes/hyp.md": ("vault_note", "---\nid: H-0006\nproject: PROJ-001\nstatus: propuesta\n---\n# H\n"),
    "notes/_digest.md": ("vault_note", "# digest\n"),
    "bin/blob.bin": ("api_key", b"\x00\x01\x02" + AWS.encode() + b"\x00\xff"),
}


class TestUnits(unittest.TestCase):
    def test_placeholders_are_not_secrets(self):
        for v in ("<your-key>", "xxxxxxxxxxxxxxxxxxxx", "changeme", "${OPENAI_API_KEY}",
                  "os.environ[OPENAI_API_KEY]", "%(token)s", "your_api_key_goes_here",
                  "self.config.api_key_value", "default_token_name_v2", "/kaggle/working/out",
                  "short", "aaaaaaaaaaaaaaaaaaaaaaaa", "results_final.json"):
            self.assertFalse(cb.looks_secret(v), v)

    def test_high_entropy_values_are_secrets(self):
        self.assertTrue(cb.looks_secret(GENERIC))
        self.assertTrue(cb.looks_secret(TOKVAL))

    def test_documented_example_keys_are_ignored(self):
        self.assertFalse(cb.provider_ok("AKIA" + "IOSFODNN7" + "EXAMPLE"))


class TestContaminatedBundle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "bundle"
        make_clean_bundle(root)
        for rel, (_, content) in CONTAMINATED.items():
            write(root, rel, content, binary=isinstance(content, bytes))
        (root / ".git" / "objects").mkdir(parents=True)
        write(root, ".git/config", "[core]\n")
        write(root, "extras.zip", zip_bytes({"inner/.env": "A=1\n",
                                             "inner/tok.py": "GITHUB_TOKEN = '" + GH + "'\n"}), binary=True)
        write(root, "more.tar.gz", tar_gz_bytes({"deep/hf.txt": HF + "\n"},
                                                links={"deep/escape": "../../../etc/passwd"}), binary=True)
        outside = Path(cls.tmp.name) / "outside"
        write(outside, "secret.env", "A=1\n")
        cls.symlink_ok = True
        try:
            os.symlink(str(outside), str(root / "outside_link"), target_is_directory=True)
        except (OSError, NotImplementedError):
            cls.symlink_ok = False
            if os.name == "nt":  # a directory junction needs no symlink privilege
                r = subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "outside_link"),
                                    str(outside)], capture_output=True)
                cls.symlink_ok = r.returncode == 0
        cls.rc, cls.out, cls.err = run_cli(root)
        cls.doc = json.loads(cls.out.decode("utf-8"))
        cls.by = {(f["path"], f["kind"]): f["severity"] for f in cls.doc["findings"]}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def assertBlock(self, path, kind):
        self.assertEqual(self.by.get((path, kind)), "block",
                         f"missing block {kind} at {path}; got {sorted(self.by)}")

    def test_exit_and_status(self):
        self.assertEqual(self.rc, 2)
        self.assertEqual(self.doc["status"], "contaminated")

    def test_every_planted_contamination_is_found(self):
        for rel, (kind, _) in CONTAMINATED.items():
            if kind == "vault_path":
                seg = "Projects" if "Projects/" in rel else "Papers"
                rel = rel[: rel.index(seg) + len(seg)]
            with self.subTest(rel=rel, kind=kind):
                self.assertBlock(rel, kind)

    def test_git_directory(self):
        self.assertBlock(".git", "git_directory")
        self.assertFalse(any(p.startswith(".git/") for p, _ in self.by), "must not descend into .git")

    def test_archive_members(self):
        self.assertBlock("extras.zip!inner/.env", "env_file")
        self.assertBlock("extras.zip!inner/tok.py", "api_key")
        self.assertBlock("more.tar.gz!deep/hf.txt", "api_key")
        self.assertBlock("more.tar.gz!deep/escape", "symlink_escape")

    def test_symlink_escape(self):
        if not self.symlink_ok:
            self.skipTest("no symlink privilege on this OS/account")
        self.assertBlock("outside_link", "symlink_escape")

    def test_clean_files_in_same_bundle_not_flagged(self):
        flagged = {p for p, _ in self.by}
        for p in ("run.py", "spec.py", "E-9999.data.json", "E-9999.deps.txt", "MANIFEST.sha256",
                  "README.md", "Tools/P-0002/method/TOOL.md"):
            self.assertNotIn(p, flagged)

    def test_findings_have_exactly_three_keys(self):
        for f in self.doc["findings"]:
            self.assertEqual(set(f), {"path", "kind", "severity"})
            self.assertIn(f["severity"], ("block", "warn"))
            self.assertNotIn("\\", f["path"])
            self.assertRegex(f["kind"], r"^[a-z][a-z0-9_]*$")

    def test_no_secret_value_leaks(self):
        both = self.out.decode("utf-8") + self.err.decode("utf-8", "replace")
        for s in ALL_SECRETS:
            self.assertNotIn(s, both)

    def test_verbose_also_never_leaks(self):
        root = Path(self.tmp.name) / "bundle"
        rc, out, err = run_cli(root, "--verbose")
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(out.decode("utf-8")), self.doc)
        text = out.decode("utf-8") + err.decode("utf-8", "replace")
        self.assertIn("redacted", err.decode("utf-8", "replace"))
        for s in ALL_SECRETS:
            self.assertNotIn(s, text)


class TestCleanBundle(unittest.TestCase):
    def test_clean_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "b"
            make_clean_bundle(root)
            (root / ".env.example").unlink()
            rc, out, _ = run_cli(root)
            doc = json.loads(out.decode("utf-8"))
            self.assertEqual((rc, doc), (0, {"status": "clean", "findings": []}))

    def test_warn_only_is_still_clean(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "b"
            make_clean_bundle(root)
            write(root, "data.py", "# fallback: here.parents[2] / 'Projects/slug/Experimentos/E-9999.data.json'\n")
            rc, out, _ = run_cli(root)
            doc = json.loads(out.decode("utf-8"))
            self.assertEqual(rc, 0)
            self.assertEqual(doc["status"], "clean")
            self.assertEqual({(f["path"], f["kind"], f["severity"]) for f in doc["findings"]},
                             {(".env.example", "env_template", "warn"),
                              ("data.py", "vault_path_reference", "warn")})

    def test_truncated_scan_warns(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "b"
            write(root, "big.txt", "a" * 5000)
            rc, out, _ = run_cli(root, "--max-bytes", "1000")
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["findings"],
                             [{"path": "big.txt", "kind": "truncated_scan", "severity": "warn"}])

    def test_corrupt_archive_warns(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "b"
            write(root, "broken.zip", b"PK\x03\x04 not really", binary=True)
            rc, out, _ = run_cli(root)
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["findings"],
                             [{"path": "broken.zip", "kind": "archive_unreadable", "severity": "warn"}])


class _Out:
    """Stand-in for sys.stdout that exposes a bytes `.buffer`, as check_bundle writes to it."""

    def __init__(self, buf):
        self.buffer = buf

    def write(self, s):
        self.buffer.write(s.encode("utf-8"))

    def flush(self):
        pass


class TestErrors(unittest.TestCase):
    def assertError(self, rc, out):
        self.assertEqual(rc, 1)
        doc = json.loads(out.decode("utf-8"))  # the whole stdout is one JSON object
        self.assertEqual(doc["status"], "error")
        self.assertIsInstance(doc["findings"], list)

    def test_missing_path(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertError(*run_cli(Path(d) / "nope")[:2])

    def test_file_instead_of_dir(self):
        with tempfile.TemporaryDirectory() as d:
            f = write(Path(d), "f.txt", "x")
            self.assertError(*run_cli(f)[:2])

    def test_usage_error_is_exit_1_not_2(self):
        self.assertError(*run_cli("--no-such-flag")[:2])
        self.assertError(*run_cli()[:2])

    def test_unreadable_file_is_error(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "b"
            write(root, "ok.txt", "x")
            orig = Path.open

            def boom(self, *a, **k):
                if self.name == "ok.txt":
                    raise PermissionError(13, "Permission denied")
                return orig(self, *a, **k)

            Path.open = boom
            buf, old = io.BytesIO(), sys.stdout
            try:
                sys.stdout = _Out(buf)
                rc = cb.main([str(root)])
            finally:
                sys.stdout, Path.open = old, orig
            self.assertError(rc, buf.getvalue())

    def test_crash_still_yields_json_and_exit_1(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "b"
            write(root, "ok.txt", "x")
            orig = cb.Scanner.walk
            cb.Scanner.walk = lambda self: 1 / 0
            buf, old = io.BytesIO(), sys.stdout
            try:
                sys.stdout = _Out(buf)
                rc = cb.main([str(root)])
            finally:
                sys.stdout, cb.Scanner.walk = old, orig
            self.assertError(rc, buf.getvalue())

    def test_status_agrees_with_exit_code(self):
        expected = {0: "clean", 1: "error", 2: "contaminated"}
        with tempfile.TemporaryDirectory() as d:
            clean = Path(d) / "c"
            write(clean, "a.py", "print(1)\n")
            dirty = Path(d) / "x"
            write(dirty, ".env", "A=1\n")
            for target in (clean, dirty, Path(d) / "missing"):
                rc, out, _ = run_cli(target)
                self.assertEqual(json.loads(out)["status"], expected[rc])


if __name__ == "__main__":
    unittest.main()
