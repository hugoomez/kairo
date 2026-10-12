"""regen_digest rebuilds the H-XXXX rows of _digest.md from the notes and keeps
everything else: discard rows (an append-only ledger) and the ## Meta-revisión
section that the next hypothesis-cycle reads."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import regen_digest  # noqa: E402

NOTE = """---
id: {hid}
status: {status}
---

## Claim

{claim}

## Lección
"""

DIGEST = """| id | claim | estado | lección |
|----|-------|--------|---------|
| H-0001 | Old claim text. | propuesta |  |
| (sin nota) | Widgets glow under blue light. | descartada | check 2: not falsifiable |

## Meta-revisión

### 2026-01-02 — first cycle

- Two candidates needed a Check 3 round.
"""


class RegenDigestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self.tmp.name) / "Projects" / "demo"
        (self.proj / "Hipotesis").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def note(self, hid, status, claim):
        p = self.proj / "Hipotesis" / f"{hid} demo.md"
        p.write_text(NOTE.format(hid=hid, status=status, claim=claim), encoding="utf-8")
        return p

    def run_hook(self, path):
        stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps({"tool_input": {"file_path": str(path)}}))
        try:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(regen_digest.main(), 0)
        finally:
            sys.stdin = stdin
        return (self.proj / "_digest.md").read_text(encoding="utf-8")

    def test_keeps_discard_rows_and_meta_review(self):
        (self.proj / "_digest.md").write_text(DIGEST, encoding="utf-8")
        self.note("H-0001", "preregistrada", "New claim text.")
        p = self.note("H-0002", "propuesta", "Second claim.")
        out = self.run_hook(p)
        self.assertIn("| H-0001 | New claim text. | preregistrada |  |", out)
        self.assertNotIn("Old claim text.", out)
        self.assertIn("| H-0002 | Second claim. | propuesta |  |", out)
        self.assertIn("| (sin nota) | Widgets glow under blue light. | descartada | check 2: not falsifiable |", out)
        self.assertIn("## Meta-revisión\n\n### 2026-01-02 — first cycle\n\n- Two candidates needed a Check 3 round.\n", out)
        # hypothesis rows first, then the discard rows, then the rest
        self.assertLess(out.index("H-0002"), out.index("(sin nota)"))
        self.assertLess(out.index("(sin nota)"), out.index("## Meta-revisión"))

    def test_idempotent(self):
        (self.proj / "_digest.md").write_text(DIGEST, encoding="utf-8")
        p = self.note("H-0001", "propuesta", "New claim text.")
        first = self.run_hook(p)
        self.assertEqual(self.run_hook(p), first)

    def test_no_digest_yet(self):
        p = self.note("H-0001", "propuesta", "A claim.")
        out = self.run_hook(p)
        self.assertEqual(out, "| id | claim | estado | lección |\n|----|-------|--------|---------|\n"
                              "| H-0001 | A claim. | propuesta |  |\n")


if __name__ == "__main__":
    unittest.main()
