"""Tests for action_log.py. Run: python -m pytest scripts/bitacora

Invented ids and text only (PROJ-900, H-9001, job ids j-…)."""

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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import action_log as al  # noqa: E402


def entry(**kw):
    e = {"actor": "agent", "by": "kairo-backend", "action": "hypothesis_generation",
         "event": "started", "mode": "ask", "summary": "Generando hipótesis inventadas",
         "recorded_at": "2026-01-02T10:00:00Z", "date": "2026-01-02"}
    e.update(kw)
    return e


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-actions-"))
        self.log = self.tmp / "Projects" / "demo" / "Bitacora" / "acciones.jsonl"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args, stdin: str | None = None):
        out, err = io.StringIO(), io.StringIO()
        old_stdin = sys.stdin
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = al.main(list(args))
        finally:
            sys.stdin = old_stdin
        return code, out.getvalue(), err.getvalue()


class TestAppendAndVerify(Base):
    def test_chain_links_entries(self):
        a = al.append(self.log, entry())
        b = al.append(self.log, entry(event="completed", turns=12, duration_s=300, cost_usd=0.5))
        self.assertEqual((a["seq"], b["seq"]), (0, 1))
        self.assertEqual(a["prev_sha256"], al.GENESIS)
        self.assertEqual(b["prev_sha256"], al.sha256_text(al.canonical(a)))
        self.assertEqual(al.chain_problems(al.read_lines(self.log)), [])

    def test_defaults_are_filled(self):
        e = al.append(self.log, {"actor": "system", "by": "kairo-backend", "action": "digest",
                                 "event": "completed", "summary": "ok"})
        self.assertEqual((e["mode"], e["refs"]), ("n/a", []))
        self.assertRegex(e["recorded_at"], r"Z$")
        self.assertRegex(e["date"], r"^\d{4}-\d{2}-\d{2}$")

    def test_edit_is_detected(self):
        al.append(self.log, entry())
        al.append(self.log, entry(event="completed"))
        text = self.log.read_text(encoding="utf-8").replace("Generando", "Generado")
        self.log.write_text(text, encoding="utf-8")
        problems = al.chain_problems(al.read_lines(self.log))
        self.assertTrue(any("edited" in p for p in problems), problems)
        with self.assertRaises(al.ActionLogError):
            al.append(self.log, entry())

    def test_deleted_line_is_detected(self):
        for _ in range(3):
            al.append(self.log, entry())
        lines = self.log.read_text(encoding="utf-8").splitlines(keepends=True)
        self.log.write_text(lines[0] + lines[2], encoding="utf-8")
        self.assertTrue(al.chain_problems(al.read_lines(self.log)))

    def test_invalid_entries_are_refused(self):
        bad = [
            entry(actor="robot"),
            entry(action="Not Snake"),
            entry(event="exploded"),
            entry(summary="  "),
            entry(turns=-1),
            entry(cost_usd="cheap"),
            entry(refs="H-9001"),
            entry(actor="human", event="rejected"),  # decision without explicit reason key
        ]
        for e in bad:
            with self.subTest(e=e), self.assertRaises(al.ActionLogError):
                al.append(self.log, e)

    def test_human_decision_with_null_reason_is_accepted(self):
        e = al.append(self.log, entry(actor="human", by="researcher", event="rejected", reason=None))
        self.assertIsNone(e["reason"])

    def test_crlf_checkout_still_verifies_and_appends_in_style(self):
        al.append(self.log, entry())
        self.log.write_bytes(self.log.read_bytes().replace(b"\n", b"\r\n"))
        al.append(self.log, entry(event="completed"))
        self.assertEqual(self.log.read_bytes().count(b"\r\n"), 2)
        self.assertEqual(al.chain_problems(al.read_lines(self.log)), [])


class TestRender(Base):
    def test_page_splits_done_and_decided_and_keeps_notes(self):
        al.append(self.log, entry())
        al.append(self.log, entry(actor="human", by="researcher", action="run_local", event="rejected",
                                  reason="Sin GPU esta semana", refs=["E-9001"]))
        al.append(self.log, entry(actor="human", by="researcher", action="digest", event="toggled",
                                  reason=None, summary="digest: ask → auto"))
        al.append(self.log, entry(recorded_at="2026-01-03T10:00:00Z", date="2026-01-03",
                                  summary="Otro día"))
        page = self.tmp / "2026-01-02.md"
        page.write_text("# vieja\n\n## Notas\n\nMi nota a mano.\n", encoding="utf-8")
        text = al.render(al.load(self.log), "2026-01-02", page)
        hecho, rest = text.split("## Decidido (y por qué)")
        decided, notes = rest.split("## Notas")
        self.assertIn("Generando hipótesis inventadas", hecho)
        self.assertNotIn("Otro día", text)
        self.assertIn("**Por qué:** Sin GPU esta semana", decided)
        self.assertIn("[E-9001]", decided)
        self.assertIn("_(sin motivo registrado)_", decided)
        self.assertIn("Mi nota a mano.", notes)

    def test_empty_day(self):
        al.append(self.log, entry())
        text = al.render(al.load(self.log), "2026-02-01", self.tmp / "x.md")
        self.assertIn("(sin acciones registradas)", text)
        self.assertIn(al.NOTES_PLACEHOLDER, text)


class TestCli(Base):
    def test_append_recent_verify_render(self):
        code, out, _ = self.cli("append", "--log", str(self.log), "--entry", "-",
                                stdin=json.dumps(entry()))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["seq"], 0)
        self.cli("append", "--log", str(self.log), "--entry", "-",
                 stdin=json.dumps(entry(event="completed")))
        code, out, _ = self.cli("recent", "--log", str(self.log), "--n", "1", "--json")
        self.assertEqual([e["event"] for e in json.loads(out)], ["completed"])
        code, out, _ = self.cli("verify", "--log", str(self.log), "--json")
        self.assertEqual((code, json.loads(out)["ok"]), (0, True))
        page = self.tmp / "p.md"
        code, _, _ = self.cli("render", "--log", str(self.log), "--date", "2026-01-02", "--out", str(page))
        self.assertEqual(code, 0)
        self.assertIn("# Bitácora — 2026-01-02", page.read_text(encoding="utf-8"))

    def test_refusal_exit_code(self):
        code, _, err = self.cli("append", "--log", str(self.log), "--entry", "-",
                                stdin=json.dumps(entry(actor="robot")))
        self.assertEqual(code, 3)
        self.assertIn("refused", err)

    def test_recent_on_missing_log_is_empty(self):
        code, out, _ = self.cli("recent", "--log", str(self.log), "--json")
        self.assertEqual((code, json.loads(out)), (0, []))

    def test_verify_git_detects_rewrite_after_commit(self):
        repo = self.tmp
        def git(*a):
            return subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)
        git("init", "-q")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        al.append(self.log, entry())
        al.append(self.log, entry(event="completed"))
        git("add", "-A")
        git("commit", "-q", "-m", "log")
        code, _, _ = self.cli("verify", "--log", str(self.log), "--git")
        self.assertEqual(code, 0)
        # Recompute a whole new (valid) chain with one entry dropped: only git catches it.
        self.log.unlink()
        al.append(self.log, entry(event="completed"))
        code, out, _ = self.cli("verify", "--log", str(self.log), "--git", "--json")
        self.assertEqual(code, 3)
        self.assertIn("append-only", json.loads(out)["problems"][0])


if __name__ == "__main__":
    unittest.main()
