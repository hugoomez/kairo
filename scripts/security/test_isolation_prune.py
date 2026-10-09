"""isolation.py prune: packets and stored replies older than the threshold are
deleted, younger ones never; SessionEnd runs it at most once a day (2c).
Invented text only; the state dir is a temp dir."""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "hooks"))
import isolation  # noqa: E402
import kairo_hook  # noqa: E402

DAY = 86400


class TestPrune(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-prune-"))
        self.env = mock.patch.dict(os.environ, {"KAIRO_STATE_DIR": str(self.tmp)})
        self.env.start()
        os.environ.pop("KAIRO_PACKETS_DIR", None)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def aged(self, p: Path, days: float) -> Path:
        t = time.time() - days * DAY
        os.utime(p, (t, t))
        return p

    def make(self):
        old = self.aged(Path(isolation.store("paquete viejo inventado")["path"]), 120)
        young = self.aged(Path(isolation.store("paquete reciente inventado")["path"]), 10)
        isolation.replies_dir().mkdir(parents=True, exist_ok=True)
        return old, young

    def test_old_packets_and_replies_go_young_ones_stay(self):
        old, young = self.make()
        old_r = isolation.replies_dir() / ("a" * 64 + ".txt")
        old_r.write_text("{}", encoding="utf-8")
        self.aged(old_r, 200)
        young_r = isolation.replies_dir() / ("b" * 64 + ".txt")
        young_r.write_text("{}", encoding="utf-8")
        other = isolation.packets_dir() / "notes.txt"            # not a store file: left alone
        other.write_text("x", encoding="utf-8")
        self.aged(other, 400)
        got = isolation.prune(90)
        self.assertEqual(got["deleted"], 2)
        self.assertFalse(old.exists())
        self.assertFalse(old_r.exists())
        self.assertTrue(young.exists() and young_r.exists() and other.exists())

    def test_the_threshold_is_respected_and_cli_defaults_to_90_days(self):
        old, young = self.make()
        self.assertEqual(isolation.prune(365)["deleted"], 0)
        with self.assertRaises(ValueError):
            isolation.prune(0)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(isolation.main(["prune"]), 0)
        self.assertEqual(json.loads(out.getvalue())["older_than_days"], 90)
        self.assertFalse(old.exists())
        self.assertTrue(young.exists())

    def test_session_end_prunes_at_most_once_a_day_and_never_fails(self):
        old, _ = self.make()
        kairo_hook.prune_state()
        self.assertFalse(old.exists())
        old2 = self.aged(Path(isolation.store("otro paquete viejo inventado")["path"]), 120)
        kairo_hook.prune_state()                                  # same day: not again
        self.assertTrue(old2.exists())
        with mock.patch.object(isolation, "prune", side_effect=OSError("disk")):
            (self.tmp / "prune.last").unlink()
            kairo_hook.prune_state()                              # best effort: no exception
        self.assertEqual(kairo_hook.session_end({"cwd": str(self.tmp / "nowhere")}), 0)


if __name__ == "__main__":
    unittest.main()
