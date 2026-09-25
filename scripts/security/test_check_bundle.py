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

    def test_large_file_over_window_is_clean_without_warn(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "b"
            write(root, "big.txt", "a" * 5000)
            rc, out, _ = run_cli(root, "--max-bytes", "1024")
            self.assertEqual((rc, json.loads(out)), (0, {"status": "clean", "findings": []}))


class TestV3NoteTypes(unittest.TestCase):
    """Block B's v3 note types (Claims/ C-XXXX, Evolucion/ EVO-XXXX, _ledger.md)."""

    def test_claim_evo_and_ledger_notes_block(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "b"
            make_clean_bundle(root)
            (root / ".env.example").unlink()
            write(root, "notes/claim.md", "---\nid: C-0004\nproject: PROJ-001\nstatus: pendiente\n---\n")
            write(root, "notes/evo.md", "---\nid: EVO-0001\nproject: PROJ-001\n---\n")
            write(root, "notes/_ledger.md", "# ledger\n")
            write(root, "notes/F-012 tarea.md", "---\nid: F-012\nproject: PROJ-001\nstatus: backlog\n---\n")
            rc, out, _ = run_cli(root)
            doc = json.loads(out.decode("utf-8"))
            self.assertEqual(rc, 2)
            self.assertEqual({(f["path"], f["kind"]) for f in doc["findings"]},
                             {("notes/claim.md", "vault_note"), ("notes/evo.md", "vault_note"),
                              ("notes/_ledger.md", "vault_note"), ("notes/F-012 tarea.md", "vault_note")})


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


# ---- I1: fail closed on content that cannot be scanned -------------------------

HEX = "0123456789abcdef"
GLPAT = "gl" + "pat-" + fake(20, ALNUM + "_-", 20)
MIXED_SNAKE = fake(5, ALNUM, 21) + "_" + fake(8, ALNUM, 22) + "_" + fake(5, ALNUM, 23)
PUNCT_PW = fake(7, ALNUM, 24) + "!" + fake(6, ALNUM, 25) + "#" + fake(4, ALNUM, 26)
KAGGLE_HEX = fake(32, HEX, 27)
PPK_HEAD = "PuTTY-User-" + "Key-File-3: ssh-ed25519\nEncryption: none\n"
ALL_SECRETS += [GLPAT, MIXED_SNAKE, PUNCT_PW, KAGGLE_HEX]


def nested_zip(levels: int, payload: dict) -> bytes:
    blob = zip_bytes(payload)
    for i in range(levels - 1):
        blob = zip_bytes({f"l{i}.zip": blob})
    return blob


def scan(root: Path, *args):
    rc, out, err = run_cli(root, *args)
    doc = json.loads(out.decode("utf-8"))
    return rc, doc, {(f["path"], f["kind"]): f["severity"] for f in doc["findings"]}, out + err


def scan_inproc(root: Path, *args):
    buf, old = io.BytesIO(), sys.stdout
    try:
        sys.stdout = _Out(buf)
        rc = cb.main([str(root), *map(str, args)])
    finally:
        sys.stdout = old
    doc = json.loads(buf.getvalue().decode("utf-8"))
    return rc, {(f["path"], f["kind"]): f["severity"] for f in doc["findings"]}


class TestFailClosed(unittest.TestCase):
    """I1: a transfer gate must not pass what it did not read."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "b"
        self.root.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_secret_at_end_of_file_bigger_than_default_cap(self):
        rows = "id,value,label\n" + "1,2,label_a\n" * (9 * 1024 * 1024 // 12)
        self.assertGreater(len(rows), cb.DEFAULT_MAX_BYTES)
        write(self.root, "data/big.csv", rows + "99,note," + ANT + "\n")
        rc, _, by, text = scan(self.root)
        self.assertEqual(rc, 2)
        self.assertEqual(by.get(("data/big.csv", "api_key")), "block")
        self.assertNotIn(("data/big.csv", "truncated_scan"), by)
        self.assertNotIn(ANT.encode(), text)

    def test_secret_straddling_window_boundary(self):
        write(self.root, "f.txt", "a" * 4080 + " " + ANT + " " + "b" * 3000)
        rc, _, by, _ = scan(self.root, "--max-bytes", "4096")
        self.assertEqual(by.get(("f.txt", "api_key")), "block")

    def test_archive_member_bigger_than_window_fully_scanned(self):
        write(self.root, "a.zip", zip_bytes({"m.txt": "z" * 20000 + "\n" + GH + "\n"}), binary=True)
        rc, _, by, _ = scan(self.root, "--max-bytes", "4096")
        self.assertEqual(by.get(("a.zip!m.txt", "api_key")), "block")

    def test_7z_blocks(self):
        write(self.root, "x.7z", b"7z\xbc\xaf\x27\x1c\x00\x04" + os.urandom(64), binary=True)
        rc, _, by, _ = scan(self.root)
        self.assertEqual(rc, 2)
        self.assertEqual(by.get(("x.7z", "archive_unscanned")), "block")

    def test_rar_by_magic_without_extension_blocks(self):
        write(self.root, "payload.bin", b"Rar!\x1a\x07\x01\x00" + os.urandom(64), binary=True)
        rc, _, by, _ = scan(self.root)
        self.assertEqual(by.get(("payload.bin", "archive_unscanned")), "block")

    def test_corrupt_zip_blocks(self):
        write(self.root, "broken.zip", b"PK\x03\x04 not really", binary=True)
        rc, doc, by, _ = scan(self.root)
        self.assertEqual(rc, 2)
        self.assertEqual(doc["findings"],
                         [{"path": "broken.zip", "kind": "archive_unreadable", "severity": "block"}])

    def test_empty_file_with_archive_name_is_not_a_finding(self):
        write(self.root, "empty.whl", b"", binary=True)
        rc, doc, _, _ = scan(self.root)
        self.assertEqual((rc, doc), (0, {"status": "clean", "findings": []}))

    def test_encrypted_zip_member_blocks(self):
        blob = bytearray(zip_bytes({"secret.txt": os.urandom(40)}))
        # zipfile clears the "encrypted" bit on write: set it in both headers by hand
        blob[blob.index(b"PK\x03\x04") + 6] |= 0x1
        blob[blob.index(b"PK\x01\x02") + 8] |= 0x1
        write(self.root, "enc.zip", bytes(blob), binary=True)
        rc, _, by, _ = scan(self.root)
        self.assertEqual(rc, 2)
        self.assertEqual(by.get(("enc.zip", "archive_unreadable")), "block")

    def test_truncated_gzip_blocks(self):
        import gzip
        blob = gzip.compress(os.urandom(20000))
        write(self.root, "t.txt.gz", blob[: len(blob) // 2], binary=True)
        rc, _, by, _ = scan(self.root)
        self.assertEqual(by.get(("t.txt.gz", "archive_unreadable")), "block")

    def test_gzip_by_magic_without_extension_is_opened(self):
        import gzip
        write(self.root, "blob.dat", gzip.compress(("k = 1\n" + HF + "\n").encode()), binary=True)
        rc, _, by, _ = scan(self.root)
        self.assertEqual(by.get(("blob.dat!blob.dat", "api_key")), "block", sorted(by))

    def test_nesting_within_limit_scanned_beyond_blocks(self):
        write(self.root, "ok.zip", nested_zip(cb.MAX_ARCHIVE_DEPTH, {".env": "A=1\n"}), binary=True)
        write(self.root, "deep.zip", nested_zip(cb.MAX_ARCHIVE_DEPTH + 1, {"a.txt": "x\n"}), binary=True)
        rc, _, by, _ = scan(self.root)
        self.assertTrue(any(k == "env_file" and p.startswith("ok.zip!") for p, k in by), by)
        self.assertTrue(any(k == "archive_unscanned" and s == "block" and p.startswith("deep.zip")
                            for (p, k), s in by.items()), by)

    def test_expanded_size_ceiling_blocks(self):
        write(self.root, "big.zip", zip_bytes({"m.bin": b"\x00" * 50000}), binary=True)
        old = getattr(cb, "EXPANDED_CAP", None)
        cb.EXPANDED_CAP = 10000
        try:
            rc, by = scan_inproc(self.root)
        finally:
            if old is None:
                del cb.EXPANDED_CAP
            else:
                cb.EXPANDED_CAP = old
        self.assertEqual(rc, 2)
        self.assertEqual(by.get(("big.zip", "truncated_scan")), "block")


# ---- I2: false negatives ------------------------------------------------------

class TestFalseNegatives(unittest.TestCase):
    CASES = {
        "environ_subscript.py": ("api_key", 'os.environ["OPENALEX_API_KEY"] = "' + fake(24, ALNUM, 28) + '"\n'),
        "kaggle.sh": ("api_key", "export KAGGLE_USERNAME=someone\nKAGGLE_KEY=" + KAGGLE_HEX + "\n"),
        "kaggle_cfg.json": ("api_key", json.dumps({"username": "someone", "key": KAGGLE_HEX})),
        "mixed_snake.py": ("token", 'DEEPINFRA_TOKEN = "' + MIXED_SNAKE + '"\n'),
        "pw.sh": ("token", "PASSWORD=" + PUNCT_PW + "\n"),
        "db.yaml": ("token", "db:\n  passwd: '" + PUNCT_PW + "'\n"),
        "gitlab.txt": ("api_key", "token " + GLPAT + "\n"),
        "notes_ppk.txt": ("private_key", PPK_HEAD + "Public-Lines: 2\n" + fake(60, ALNUM, 29) + "\n"),
    }

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "b"
        for rel, (_, content) in cls.CASES.items():
            write(root, rel, content)
        write(root, "wide.txt", ("OPENAI_API_KEY=" + GENERIC + "\n" + ANT + "\n").encode("utf-16-le"),
              binary=True)
        write(root, "id_work.ppk", PPK_HEAD + "Private-Lines: 1\n" + fake(40, ALNUM, 30) + "\n")
        write(root, "blob.bin", b"\x00\x01" + PEM.encode() + b"\x00", binary=True)
        write(root, "sa.bin", b"\x00\x01" + json.dumps({"private_key": PEM}).encode() + b"\x00", binary=True)
        cls.rc, cls.doc, cls.by, cls.text = scan(root)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_each_case_blocks(self):
        for rel, (kind, _) in self.CASES.items():
            with self.subTest(rel=rel):
                self.assertEqual(self.by.get((rel, kind)), "block", sorted(self.by))

    def test_utf16le_without_bom(self):
        self.assertEqual(self.by.get(("wide.txt", "api_key")), "block", sorted(self.by))

    def test_putty_ppk_file(self):
        self.assertEqual(self.by.get(("id_work.ppk", "private_key")), "block", sorted(self.by))

    def test_private_key_with_body_in_binary(self):
        self.assertEqual(self.by.get(("blob.bin", "private_key")), "block", sorted(self.by))
        self.assertEqual(self.by.get(("sa.bin", "private_key")), "block", sorted(self.by))

    def test_no_leak(self):
        for s in ALL_SECRETS:
            self.assertNotIn(s.encode(), self.text)


class TestStillNotSecrets(unittest.TestCase):
    def test_new_name_rules_do_not_overreach(self):
        hi = fake(28, ALNUM, 31)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "b"
            write(root, "code.py", "\n".join([
                'keys = "' + hi + '"',
                'key_path = "' + hi + '"',
                'monkey = "' + hi + '"',
                'hotkey = "' + hi + '"',
                'public_key = "' + hi + '"',
                "items = sorted(items, key=operator.itemgetter(1))",
                "if api_key == expected_api_key_value_long: pass",
                "EQ_TOKEN = default_token_name_v2",
                "PAD_TOKEN = SPECIAL_PAD_TOKEN_V2",
                "password = get_password(user_name_value)",
                "password = self.config.password",
                "password = request.form['password']",
                'password = "<your-password-here!>"',
                "PASSWORD=${DB_PASSWORD}",
                "password: str = field(default_factory=str)",
                "        password: _PasswordType | None = None,",
                "password: Optional[Callable_Type] = None",
                "os.environ['OPENAI_API_KEY'] = os.environ.get('BACKUP_KEY', '')",
                'db_pass = "%(password)s"',
                "bypass = 'Zq8!rT2#mK9$wL4@'",
                # shapes from the site-packages false-positive sweep
                "    key: _ArrayLikeInt_co | None = ...,",
                "def __init__(self, key: QuadraticTermKey, coefficient: float) -> None:",
                "    key: PositionalIndexer2D,",
                "key = 'arrow-datasets/nyc-taxi/year=2019/month=6/part-0.parquet'",
            ]) + "\n")
            write(root, "bundle.min.js",
                  "return Ct(t,[{key:`componentWillUnmount`,value:function(){}}]);"
                  "Ot(t,[{key:`_isTransitionInProgress`,value:function(){}}]);"
                  "tz=zoneinfo.ZoneInfo(key='America/Los_Angeles');"
                  "for(let W of m)V[W.key]=this.nextStencilID++;"
                  "this.fail=r,this.depthFail=i,this.pass=a}}Ln.disabled=new Ln({func:1});\n")
            write(root, "lib.dll", b"MZ\x00\x00" + ("-----BEGIN " + "RSA PRIVATE" + " KEY-----").encode()
                  + b"\x00-----END\x00" + os.urandom(64).replace(b"-", b"."), binary=True)
            write(root, "wide_clean.txt", "hello world\nnothing here\n".encode("utf-16-le"), binary=True)
            rc, out, _ = run_cli(root)
            self.assertEqual((rc, json.loads(out)), (0, {"status": "clean", "findings": []}))

    def test_identifier_shapes(self):
        self.assertFalse(cb.looks_secret("default_token_name_v2"))
        self.assertFalse(cb.looks_secret("SPECIAL_PAD_TOKEN_V2_X"))
        self.assertTrue(cb.looks_secret(MIXED_SNAKE))


if __name__ == "__main__":
    unittest.main()
