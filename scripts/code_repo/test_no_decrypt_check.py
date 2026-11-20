"""Tests for no_decrypt_check.py. Run: python -m pytest scripts/code_repo/test_no_decrypt_check.py

Invented repositories in a temp dir; no network. Key material comes from fake_material."""

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
import fake_material as fm  # noqa: E402
import no_decrypt_check as nd  # noqa: E402

SHOULD_HIT = {
    "sops_decrypt": ["sops -d secrets.yaml > plain.yaml", "sops --decrypt --output x.json in.json",
                     "sops decrypt in.yaml", "sops exec-env secrets.env 'make run'",
                     "sops --config .sops.yaml exec-file in.yaml 'cat {}'",
                     'subprocess.run(["sops", "-d", path], check=True)'],
    "age_decrypt": ["age -d -i key.txt secret.age", "age --decrypt -o out.txt in.age", "rage -d -i k in.age",
                    "execFile('age', ['--decrypt', '-i', keyFile, input])"],
    "agenix_decrypt": ["agenix -d secret.age"],
    "kubeseal_recovery": ["kubeseal --recovery-unseal --recovery-private-key k.pem < s.yaml",
                          "kubeseal --recovery-private-key key.pem"],
    "dotenvx_decrypt": ["dotenvx decrypt -f .env.production", "dotenvx get API_KEY", "dotenvx run -- node app.js",
                        "subprocess.check_output(['dotenvx', 'get', 'X'])"],
    "git_crypt_unlock": ["git-crypt unlock ../key"],
    "pyrage_decrypt": ["plain = pyrage.decrypt(data, [ident])", "pyrage.passphrase.decrypt(blob, pw)",
                       "from pyrage import decrypt, x25519"],
    "age_lib_decrypt": ["from age.file import Decryptor", "out = age.decrypt(blob)"],
    "ecies_decrypt": ["ecies.decrypt(sk_hex, data)", "from ecies import encrypt, decrypt"],
    "nacl_box_decrypt": ["msg = Box(sk, pk).decrypt(ct)", "SealedBox(sk).decrypt(ct)"],
    "nacl_private_key": ["sk = PrivateKey(raw_bytes)"],
    "go_sops_decrypt": ['cleartext, err := decrypt.File(path, "yaml")'],
    "js_age_decrypter": ["const d = new Decrypter()"],
}

SHOULD_NOT_HIT = [
    "sops -e -i secrets.yaml", "sops --encrypt in.yaml", "age -r age1xyz -o out.age in.txt", "age-keygen -o k.txt",
    "page -d 3", "storage -d x", "dotenvx encrypt -f .env", "kubeseal --fetch-cert > cert.pem",
    "git-crypt status", "sk = PrivateKey.generate()", "ciphertext = Box(sk, pk).encrypt(msg)",
    "# the age of the file -- days", "decrypted = False", "def decrypt_count(rows): return 0",
    "We never decrypt anything.",
]


def git(cwd, *a):
    r = subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r


class ScanText(unittest.TestCase):
    def test_every_pattern_hits(self):
        for rule, lines in SHOULD_HIT.items():
            for line in lines:
                rules = {r for _, r in nd.scan_text(line)}
                self.assertIn(rule, rules, line)

    def test_near_misses_do_not_hit(self):
        for line in SHOULD_NOT_HIT:
            self.assertEqual(nd.scan_text(line), [], line)

    def test_decrypt_after_a_private_key_loader(self):
        src = ("from cryptography.hazmat.primitives import serialization\n"
               "key = serialization.load_pem_private_key(blob, None)\n"
               "plain = key.decrypt(ct, padding)\n")
        self.assertIn((3, "private_key_decrypt"), nd.scan_text(src))
        enc_only = "pub = serialization.load_pem_public_key(blob)\nct = pub.encrypt(m, padding)\nx = f.decrypt(y)\n"
        self.assertEqual(nd.scan_text(enc_only), [])
        x = "from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey\n" \
            "sk = X25519PrivateKey.from_private_bytes(raw)\nshared = sk.exchange(peer)\n"
        rules = nd.scan_text(x)
        self.assertIn((3, "private_key_decrypt"), rules)


class Repo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-nodecrypt-")).resolve()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "t@example.org")
        git(self.repo, "config", "user.name", "T")
        git(self.repo, "config", "commit.gpgsign", "false")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, rel, text, track=True):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        if track:
            git(self.repo, "add", rel)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = nd.main(["--repo", str(self.repo), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_clean_repo(self):
        self.add("collect.py", "import json\nprint(json.dumps({'n': 1}))\n")
        git(self.repo, "commit", "-qm", "init")
        code, out, _ = self.cli()
        self.assertEqual(code, 0, out)
        data = json.loads(out)
        self.assertEqual(data["status"], "limpio")
        self.assertEqual(data["files_scanned"], 1)
        self.assertEqual(len(data["head"]), 40)

    def test_hits_with_line_numbers_untracked_ignored_and_key_redacted(self):
        key = fm.age_secret()
        self.add("scripts/run.sh", "#!/bin/sh\nset -e\nage -d -i " + key + " in.age\n")
        self.add("notes/untracked.sh", "sops -d x.yaml\n", track=False)
        self.add("bin.dat", "")
        (self.repo / "bin.dat").write_bytes(b"\x00\x01sops -d x")
        git(self.repo, "add", "bin.dat")
        code, out, _ = self.cli()
        self.assertEqual(code, 4)
        data = json.loads(out)
        self.assertEqual([(h["path"], h["line"], h["rule"]) for h in data["hits"]],
                         [("scripts/run.sh", 3, "age_decrypt")])
        self.assertNotIn(key, out)
        self.assertIn("[REDACTADO", data["hits"][0]["text"])
        self.assertEqual(data["files_skipped"], 1)

    def test_allow_list_with_justification(self):
        self.add("README.md", "Never run `sops -d` on collected files.\n")
        self.add("collect.py", "subprocess.run(['dotenvx', 'get', 'X'])\n")
        allow = self.tmp / "allow.json"
        allow.write_text(json.dumps([
            {"path": "*.md", "rule": "sops_decrypt", "justificacion": "the README forbids the command; it does not run it"},
            {"path": "old.py", "justificacion": "entry that matches nothing at all"}]), encoding="utf-8")
        code, out, _ = self.cli("--allow", str(allow))
        self.assertEqual(code, 4)
        data = json.loads(out)
        self.assertEqual([h["path"] for h in data["hits"]], ["collect.py"])
        self.assertEqual(data["allowed"][0]["path"], "README.md")
        self.assertIn("forbids", data["allowed"][0]["justificacion"])
        self.assertEqual(data["allow_unused"][0]["path"], "old.py")

    def test_default_allow_file_and_markdown(self):
        self.add("README.md", "Never run `sops -d`.\n")
        self.add(".kairo/no-descifrar.json", json.dumps([
            {"path": "README.md", "line": 1, "justificacion": "documents the prohibition only"}]))
        code, out, _ = self.cli("--format", "md")
        self.assertEqual(code, 0, out)
        self.assertIn("## Nunca descifrar", out)
        self.assertIn("**limpio**", out)
        self.assertIn("documents the prohibition only", out)

    def test_allow_entry_without_justification_is_an_error(self):
        self.add("a.sh", "sops -d x\n")
        allow = self.tmp / "allow.json"
        allow.write_text(json.dumps([{"path": "a.sh", "justificacion": "ok"}]), encoding="utf-8")
        code, _, err = self.cli("--allow", str(allow))
        self.assertEqual(code, 1)
        self.assertIn("justificacion", err)

    def test_not_a_repository(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = nd.main(["--repo", str(plain)])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
