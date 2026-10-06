"""Tests for version_check.py on a synthetic temp vault; network mocked.

Run: python -m pytest scripts/citations
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import net  # noqa: E402
import version_check as vc  # noqa: E402


def paper(pid, arxiv, version, extra=""):
    return (f'---\nid: {pid}\ntitle: "Invented paper {pid}"\narxiv: {arxiv}\narxiv_version: {version}\n'
            f"{extra}---\n\n## Referencia\n\nsynthetic\n")


ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry><id>http://arxiv.org/abs/3001.00001v3</id><published>2030-01-01T00:00:00Z</published>
<updated>2031-04-02T00:00:00Z</updated><title>Invented paper one</title><summary>s</summary>
<author><name>Ana Poe</name></author><arxiv:doi>10.0000/journal.1</arxiv:doi>
<arxiv:journal_ref>Invented J. 7, 11 (2031)</arxiv:journal_ref></entry>
<entry><id>http://arxiv.org/abs/3001.00002v1</id><published>2030-02-01T00:00:00Z</published>
<updated>2030-02-01T00:00:00Z</updated><title>Invented paper two</title><summary>s</summary>
<author><name>Rui Roe</name></author></entry>
</feed>"""


class VersionCheck(unittest.TestCase):
    def setUp(self):
        self.vault = Path(tempfile.mkdtemp())
        (self.vault / "Papers").mkdir()
        (self.vault / "Papers" / "P-0001 one.md").write_text(paper("P-0001", "3001.00001", "v1"), encoding="utf-8")
        (self.vault / "Papers" / "P-0002 two.md").write_text(paper("P-0002", "3001.00002", "v1"), encoding="utf-8")
        (self.vault / "Papers" / "P-0003 secret.md").write_text(
            paper("P-0003", "3001.00003", "v1", "send: never\n"), encoding="utf-8")
        h = self.vault / "Projects" / "demo" / "Hipotesis"
        h.mkdir(parents=True)
        (h / "H-0001.md").write_text("---\nid: H-0001\nlinked_papers: [P-0001]\n---\n\n## Claim\n\nx\n",
                                     encoding="utf-8")
        (self.vault / "Projects" / "demo" / "Estado-del-arte.md").write_text(
            "---\ntitle: x\n---\n\nAlgo — P-0001 §3.2\n", encoding="utf-8")
        self.urls = []

    def fetch(self, url, headers=None, **kw):
        self.urls.append(url)
        return ATOM.encode()

    def run_cli(self, *args):
        buf = io.StringIO()
        with mock.patch.object(net, "get", side_effect=self.fetch), contextlib.redirect_stdout(buf):
            code = vc.main(["--vault", str(self.vault), "--json", *args], today="2031-05-01")
        return code, json.loads(buf.getvalue())

    def test_a_newer_version_and_a_declared_publication_are_reported_with_who_cites_them(self):
        code, out = self.run_cli()
        self.assertEqual(code, 3, out)                       # something changed
        (p,) = out["changed"]
        self.assertEqual((p["id"], p["stored"], p["latest"], p["latest_date"]), ("P-0001", "v1", "v3", "2031-04-02"))
        self.assertEqual(p["declared_published"], {"doi": "10.0000/journal.1", "journal_ref": "Invented J. 7, 11 (2031)"})
        self.assertEqual(sorted(p["cited_by"]), ["Estado-del-arte (demo)", "H-0001"])
        self.assertEqual(out["unchanged"], 1)
        self.assertEqual(out["skipped_send_never"], ["P-0003"])
        self.assertFalse(any("3001.00003" in u for u in self.urls))     # a send: never id is never sent

    def test_report_only_by_default_and_write_records_the_check_never_the_text(self):
        before = (self.vault / "Papers" / "P-0001 one.md").read_text(encoding="utf-8")
        self.run_cli()
        self.assertEqual(before, (self.vault / "Papers" / "P-0001 one.md").read_text(encoding="utf-8"))
        self.run_cli("--write")
        after = (self.vault / "Papers" / "P-0001 one.md").read_text(encoding="utf-8")
        self.assertIn("arxiv_latest_version: v3", after)
        self.assertIn("arxiv_version_checked: 2031-05-01", after)
        self.assertIn("arxiv_version: v1", after)                       # the anchored text is not moved
        self.assertTrue(after.endswith("## Referencia\n\nsynthetic\n"))

    def test_a_lost_batch_is_reported_never_taken_as_unchanged(self):
        def down(url, headers=None, **kw):
            raise net.HttpError(url, 503, "down")
        buf = io.StringIO()
        with mock.patch.object(net, "get", side_effect=down), contextlib.redirect_stdout(buf):
            code = vc.main(["--vault", str(self.vault), "--json"], today="2031-05-01")
        out = json.loads(buf.getvalue())
        self.assertEqual((code, out["unchanged"], sorted(out["lost"])), (1, 0, ["P-0001", "P-0002"]))


if __name__ == "__main__":
    unittest.main()
