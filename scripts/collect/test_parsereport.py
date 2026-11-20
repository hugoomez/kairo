"""Tests for kairo_collect.parsereport (skeleton, report, summary) and parse_summary.py.

Inputs are invented text and the synthetic corpus of scripts/security/fixtures_corpus.py;
everything is written under tmp_path."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "security"))
import fixtures_corpus as fc  # noqa: E402
import parse_summary  # noqa: E402
from kairo_collect.parsereport import ParseReport, skeleton, skeleton_groups, value_shape  # noqa: E402

SCRIPT = HERE / "parse_summary.py"
SAMPLE = """# nota inventada con clave-de-comentario-77
apiVersion: v1
kind: Secret
metadata:
    name: app-inventada
data:
    password: aHVudGVyMi1pbnZlbnRhZGE=
stringData:
    usuario: persona-inventada
sops:
    mac: ENC[AES256_GCM,data:QUJDREVGR0hJSktMTU5PUA==,iv:QUJDREVGR0hJSktMTU5PUA==,tag:QUJDREVGR0g=,type:str]
    lastmodified: "2031-01-01T00:00:00Z"
    list: [uno-inventado, "dos-inventado", 3]
{"clave": "valor-json-inventado", "n": 4, "o": {"x": null}}
DB_PASSWORD=clave-en-claro-inventada
hunter-inventado-99
"""
VALUES = ["clave-de-comentario-77", "app-inventada", "aHVudGVyMi1pbnZlbnRhZGE=", "persona-inventada",
          "uno-inventado", "dos-inventado", "valor-json-inventado", "clave-en-claro-inventada", "hunter-inventado-99",
          "2031-01-01", "QUJDREVGR0hJSktMTU5PUA"]


def test_skeleton_keeps_structure_and_no_value():
    s = skeleton(SAMPLE)
    for v in VALUES:
        assert v not in s
    lines = s.splitlines()
    assert lines[0] == "# <comment>"
    assert "apiVersion: v1" in lines and "kind: Secret" in lines
    assert "    mac: <sops-enc:AES256_GCM:str>" in lines
    assert '    lastmodified: "<datetime>"' in lines
    assert "    list: [<str>, \"<str>\", <int>]" in lines
    assert '{"clave": "<str>", "n": <int>, "o": {"x": null}}' in lines
    assert any(ln.startswith("DB_PASSWORD=<") for ln in lines)
    assert not re.search(r"sha256:[0-9a-f]", s)            # redaction markers' hashes dropped


def test_skeleton_is_idempotent_and_never_holds_corpus_values():
    fixtures = fc.build(5, 1)
    run = re.compile(r"[A-Za-z0-9+/=_]{12,}")
    for f in fixtures:
        s = skeleton(f.data)
        assert skeleton(s) == s, f.path
        try:
            text = f.data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for w in set(run.findall(text)):
            if w in s:
                # what survives is a key name or a format word, never base64 / hex / a secret
                assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.\-/]*=?", w) and not re.search(r"\d{4,}", w), (f.path, w)


def test_skeleton_of_binary_and_age():
    assert skeleton(b"\x00\x01\x02") == "<binary:3>"
    s = skeleton(b"age-encryption.org/v1\n-> X25519 QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo\n--- QUJDREVG\n\x00\xff" * 1)
    assert s.startswith("age-encryption.org/v1\n-> X25519 ") and s.endswith("<binary:2>")
    assert "QUJDREVG" not in s


def test_value_shape_tokens():
    assert value_shape("123") == "<int>" and value_shape("yes") == "<bool>"
    assert value_shape("age1" + "q" * 58) == "<age-recipient>"
    assert value_shape("encrypted:QUJD") == "<dotenvx-enc>"
    assert value_shape("ab" * 32) == "<hex:64>"
    assert value_shape("<sops-enc:HUNTER2:pass>") == "<str>"      # a crafted token is not trusted


def _report(tmp_path) -> Path:
    out = tmp_path / "parse"
    with ParseReport(out, dataset="repos", expected=10) as r:
        r.ok("acme-inventada/repo-a/app.enc.yaml", "sops-yaml", "sops")
        r.ok("acme-inventada/repo-a/x.age", "age-binary", "age")
        r.fail("acme-inventada/repo-b/a.yaml", "sops-yaml", "missing_mac", SAMPLE, tool="sops")
        r.fail("acme-inventada/repo-b/b.yaml", "sops-yaml", "missing_mac", skeleton(SAMPLE), tool="sops")
        r.fail("acme-inventada/repo-c/c.json", "sops-json", "bad_json", "{\"k\": \"valor-json-inventado\"")
        r.unknown("acme-inventada/repo-d/notes.txt", "texto")
        r.fail("acme-inventada/repo-a/x.age", "age-binary", "truncated", b"age-encryption.org/v1\n")
    return out


def test_report_and_summary(tmp_path):
    out = _report(tmp_path)
    lines = [json.loads(x) for x in (out / "parse-report.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 7 and lines[2]["skeleton"] == lines[3]["skeleton"]
    s = json.loads((out / "parse-summary.json").read_text(encoding="utf-8"))
    assert s["items"] == 6 and s["by_status"] == {"ok": 1, "fail": 4, "unknown": 1}   # x.age: last wins
    assert s["by_error_class"] == {"bad_json": 1, "missing_mac": 2, "truncated": 1}
    assert s["by_tool"]["sops"] == {"fail": 2, "ok": 1}
    assert s["coverage"] == {"ok": 1, "seen": 6, "pct_of_seen": 16.67, "expected": 10, "pct_of_expected": 10.0}
    text = (out / "parse-summary.json").read_text(encoding="utf-8")
    assert "acme" not in text and "repo-" not in text
    for v in VALUES:
        assert v not in (out / "parse-report.jsonl").read_text(encoding="utf-8")


@pytest.mark.parametrize("call", [
    lambda r: r.fail("a/b.yaml", "sops-yaml", "Error: could not parse 'clave-secreta'", "x"),
    lambda r: r.ok("../fuera.yaml", "sops-yaml", "sops"),
    lambda r: r.ok("/abs/x.yaml", "sops-yaml", "sops"),
    lambda r: r.unknown("a/b.yaml", "texto con espacios y valor"),
    lambda r: r.ok("a/b.yaml", "", "sops"),
])
def test_identifiers_and_items_are_checked(tmp_path, call):
    with ParseReport(tmp_path / "p") as r:
        with pytest.raises(ValueError):
            call(r)


def test_skeleton_groups_and_cli(tmp_path, capsys):
    out = _report(tmp_path)
    g = skeleton_groups(out / "parse-report.jsonl", "missing_mac")
    assert len(g) == 1 and g[0]["count"] == 2 and g[0]["formats"] == ["sops-yaml"]
    assert parse_summary.main([str(out)]) == 0
    printed = capsys.readouterr().out
    assert json.loads(printed)["by_status"]["fail"] == 4 and "acme" not in printed
    assert parse_summary.main([str(out), "--skeletons", "missing_mac"]) == 0
    printed = capsys.readouterr().out
    assert "acme" not in printed and "repo-b" not in printed and "<sops-enc:AES256_GCM:str>" in printed
    (out / "parse-summary.json").unlink()
    assert parse_summary.main([str(out), "--rebuild"]) == 0
    assert json.loads(capsys.readouterr().out)["items"] == 6
    r = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path / "nada")], capture_output=True)
    assert r.returncode == 1 and json.loads(r.stdout)["ok"] is False
