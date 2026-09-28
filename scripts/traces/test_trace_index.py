"""Tests for trace_index.py. Run: python -m unittest scripts/traces/test_trace_index.py

Invented ids and data only (E-0901, run ids r00…)."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trace_index as ti  # noqa: E402


def backfilled(run_id, **kw):
    e = {"event": "end", "run_id": run_id, "experiment": "E-0901", "role": "confirmatory",
         "rung": None, "attempt": 1, "retry_of": None, "seeds": [7], "cell": "a",
         "plan_index": 0, "config_hash": "unknown", "started_at": "unknown",
         "ended_at": "unknown", "outcome": "completed", "entered_analysis": True,
         "provenance": {"written_by": "test", "backfilled": True, "sources": ["invented"]}}
    e.update(kw)
    return e


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-trace-"))
        self.idx = self.tmp / "Experimentos" / "trazas" / "index.jsonl"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = ti.main(list(args))
        return code, buf.getvalue()


class AppendAndVerify(Base):
    def test_start_end_chain_verifies(self):
        ti.append(self.idx, ti.start_entry("E-0901", "r00", "confirmatory", "test", seeds=[1, 2],
                                           cell="a", plan_index=0))
        ti.append(self.idx, ti.end_entry(ti.load(self.idx), "r00", "completed", True, "test"))
        entries = ti.load(self.idx)
        self.assertEqual([e["seq"] for e in entries], [0, 1])
        self.assertEqual(entries[0]["prev_sha256"], ti.GENESIS)
        self.assertEqual(entries[1]["seeds"], [1, 2])          # carried over from start
        code, out = self.run_cli("verify", "--index", str(self.idx))
        self.assertEqual(code, 0, out)

    def test_every_outcome_is_recordable(self):
        for i, outcome in enumerate(["completed", "aborted", "crashed", "invalid", "not_launched"]):
            ti.append(self.idx, backfilled(f"r{i:02d}", outcome=outcome, entered_analysis=False,
                                           exclusion_reason="invented reason"))
        self.assertEqual(len(ti.load(self.idx)), 5)

    def test_edit_of_earlier_line_detected(self):
        for i in range(3):
            ti.append(self.idx, backfilled(f"r{i:02d}"))
        lines = self.idx.read_text(encoding="utf-8").splitlines()
        lines[0] = lines[0].replace('"seeds":[7]', '"seeds":[8]')
        self.idx.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        code, out = self.run_cli("verify", "--index", str(self.idx))
        self.assertEqual(code, 3)
        self.assertIn("line 0: entry_sha256", out)
        self.assertIn("line 1: prev_sha256", out)

    def test_edit_of_last_line_detected(self):
        ti.append(self.idx, backfilled("r00"))
        text = self.idx.read_text(encoding="utf-8").replace('"entered_analysis":true', '"entered_analysis":false')
        self.idx.write_text(text, encoding="utf-8", newline="\n")
        self.assertEqual(self.run_cli("verify", "--index", str(self.idx))[0], 3)

    def test_deleted_line_detected(self):
        for i in range(3):
            ti.append(self.idx, backfilled(f"r{i:02d}"))
        lines = self.idx.read_text(encoding="utf-8").splitlines()
        del lines[1]
        self.idx.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        code, out = self.run_cli("verify", "--index", str(self.idx))
        self.assertEqual(code, 3)
        self.assertIn("seq", out)

    def test_reordered_lines_detected(self):
        for i in range(3):
            ti.append(self.idx, backfilled(f"r{i:02d}"))
        lines = self.idx.read_text(encoding="utf-8").splitlines()
        lines[1], lines[2] = lines[2], lines[1]
        self.idx.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        self.assertEqual(self.run_cli("verify", "--index", str(self.idx))[0], 3)

    def test_append_refused_on_broken_chain(self):
        ti.append(self.idx, backfilled("r00"))
        ti.append(self.idx, backfilled("r01"))
        lines = self.idx.read_text(encoding="utf-8").splitlines()
        self.idx.write_text(lines[1] + "\n", encoding="utf-8", newline="\n")   # first line deleted
        with self.assertRaises(ti.TraceError):
            ti.append(self.idx, backfilled("r02"))

    def test_crlf_checkout_still_verifies_and_appends(self):
        ti.append(self.idx, backfilled("r00"))
        self.idx.write_bytes(self.idx.read_bytes().replace(b"\n", b"\r\n"))   # git autocrlf
        self.assertEqual(self.run_cli("verify", "--index", str(self.idx))[0], 0)
        ti.append(self.idx, backfilled("r01"))
        self.assertNotIn(b"}\n{", self.idx.read_bytes())   # the file's style is kept
        self.assertEqual(self.run_cli("verify", "--index", str(self.idx))[0], 0)

    def test_stray_carriage_return_detected(self):
        ti.append(self.idx, backfilled("r00"))
        self.idx.write_bytes(self.idx.read_bytes().replace(b'"cell"', b'"ce\rll"', 1))
        self.assertEqual(self.run_cli("verify", "--index", str(self.idx))[0], 3)


class Validation(Base):
    def test_excluded_needs_reason(self):
        with self.assertRaises(ti.TraceError):
            ti.append(self.idx, backfilled("r00", entered_analysis=False))

    def test_backfilled_needs_sources(self):
        e = backfilled("r00")
        e["provenance"] = {"written_by": "t", "backfilled": True}
        with self.assertRaises(ti.TraceError):
            ti.append(self.idx, e)

    def test_live_end_must_state_analysis(self):
        ti.append(self.idx, ti.start_entry("E-0901", "r00", "confirmatory", "test"))
        with self.assertRaises(ti.TraceError):
            ti.append(self.idx, ti.end_entry(ti.load(self.idx), "r00", "completed", None, "test"))

    def test_unknown_key_refused(self):
        with self.assertRaises(ti.TraceError):
            ti.append(self.idx, backfilled("r00", seed=[1]))

    def test_end_without_start_refused_when_live(self):
        e = backfilled("r00")
        e["provenance"] = {"written_by": "t", "backfilled": False}
        with self.assertRaises(ti.TraceError):
            ti.append(self.idx, e)

    def test_not_launched_needs_no_start(self):
        e = backfilled("r00", outcome="not_launched", entered_analysis=False, started_at=None,
                       ended_at=None, exclusion_reason="budget stop",
                       exclusion_rule_source="E-0901.md ## Plan de análisis")
        e["provenance"] = {"written_by": "t", "backfilled": False}
        ti.append(self.idx, e)
        self.assertEqual(ti.load(self.idx)[0]["outcome"], "not_launched")

    def test_duplicate_start_refused_retry_is_new_run(self):
        ti.append(self.idx, ti.start_entry("E-0901", "r00", "confirmatory", "test"))
        with self.assertRaises(ti.TraceError):
            ti.append(self.idx, ti.start_entry("E-0901", "r00", "confirmatory", "test"))
        ti.append(self.idx, ti.end_entry(ti.load(self.idx), "r00", "crashed", False, "test",
                                         exclusion_reason="OOM", exclusion_rule_source="E-0901.md ## Umbral de invalidez"))
        ti.append(self.idx, ti.start_entry("E-0901", "r00-retry1", "confirmatory", "test",
                                           attempt=2, retry_of="r00"))
        with self.assertRaises(ti.TraceError):
            ti.append(self.idx, ti.start_entry("E-0901", "r09", "confirmatory", "test", retry_of="nope"))

    def test_bad_time_refused(self):
        with self.assertRaises(ti.TraceError):
            ti.append(self.idx, backfilled("r00", started_at="2026-01-01 10:00"))


class Corrections(Base):
    def test_correction_is_a_new_entry_and_old_stays(self):
        ti.append(self.idx, backfilled("r00", seeds=[1]))
        entries = ti.load(self.idx)
        ti.append(self.idx, ti.correction_entry(entries, 0, "seed mis-typed", "test", {"seeds": [2]}))
        entries = ti.load(self.idx)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["seeds"], [1])
        self.assertEqual(ti.latest_by_run(entries)["r00"]["seeds"], [2])
        self.assertEqual(entries[1]["corrects"]["entry_sha256"], entries[0]["entry_sha256"])

    def test_correction_with_wrong_hash_refused(self):
        ti.append(self.idx, backfilled("r00"))
        e = ti.correction_entry(ti.load(self.idx), 0, "x", "test", {"seeds": [2]})
        e["corrects"]["entry_sha256"] = "0" * 64
        with self.assertRaises(ti.TraceError):
            ti.append(self.idx, e)

    def test_correction_cannot_change_run_id(self):
        ti.append(self.idx, backfilled("r00"))
        with self.assertRaises(ti.TraceError):
            ti.correction_entry(ti.load(self.idx), 0, "x", "test", {"run_id": "r99"})

    def test_cli_correct(self):
        ti.append(self.idx, backfilled("r00"))
        code, out = self.run_cli("correct", "--index", str(self.idx), "--seq", "0", "--reason", "fix",
                                 "--by", "test", "--set", 'entered_analysis=false',
                                 "--set", 'exclusion_reason="invented"')
        self.assertEqual(code, 0, out)
        self.assertFalse(ti.latest_by_run(ti.load(self.idx))["r00"]["entered_analysis"])


class Cli(Base):
    def test_start_end_list_show(self):
        self.assertEqual(self.run_cli("start", "--index", str(self.idx), "--experiment", "E-0901",
                                      "--run-id", "r00", "--role", "exploratory", "--rung", "1",
                                      "--seeds", "3,4", "--by", "test")[0], 0)
        code, out = self.run_cli("end", "--index", str(self.idx), "--run-id", "r00", "--outcome",
                                 "aborted", "--entered-analysis", "false", "--exclusion-reason",
                                 "operator stop", "--artifact", "logs/r00.log", "--by", "test")
        self.assertEqual(code, 0, out)
        code, out = self.run_cli("list", "--index", str(self.idx), "--experiment", "E-0901")
        self.assertIn("EXCLUDED", out)
        code, out = self.run_cli("show", "--index", str(self.idx), "--run-id", "r00", "--json")
        self.assertEqual(len(json.loads(out)), 2)

    def test_refusal_exit_3(self):
        code, out = self.run_cli("end", "--index", str(self.idx), "--run-id", "r00", "--outcome",
                                 "completed", "--entered-analysis", "true", "--by", "test")
        self.assertEqual(code, 3)
        self.assertIn("refused", out)

    def test_config_hash_stable(self):
        f = self.tmp / "c.json"
        f.write_text('{"b": 1, "a": [1, 2]}', encoding="utf-8")
        code, out = self.run_cli("config-hash", "--file", str(f))
        self.assertEqual(out.strip(), ti.config_hash({"a": [1, 2], "b": 1}))


@unittest.skipIf(shutil.which("git") is None, "git not available")
class GitAnchor(Base):
    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.tmp, check=True, capture_output=True)

    def test_rewrite_after_commit_detected_even_with_recomputed_chain(self):
        self.git("init", "-q")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        ti.append(self.idx, backfilled("r00"))
        ti.append(self.idx, backfilled("r01"))
        self.git("add", "-A")
        self.git("commit", "-qm", "trace")
        # rebuild the file from scratch without r00: the chain is valid again, git is not
        self.idx.unlink()
        ti.append(self.idx, backfilled("r01"))
        self.assertEqual(self.run_cli("verify", "--index", str(self.idx))[0], 0)
        code, out = self.run_cli("verify", "--index", str(self.idx), "--git")
        self.assertEqual(code, 3)
        self.assertIn("append-only", out)
        # a plain append after the commit is fine
        self.git("checkout", "--", ".")
        ti.append(self.idx, backfilled("r02"))
        self.assertEqual(self.run_cli("verify", "--index", str(self.idx), "--git")[0], 0)


if __name__ == "__main__":
    unittest.main()
