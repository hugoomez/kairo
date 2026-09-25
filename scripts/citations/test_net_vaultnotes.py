"""Tests for net.py (retry convention, redaction) and vaultnotes.py (frontmatter).

Run: python -m unittest discover -s scripts/citations -p "test_*.py"
"""

import io
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import net  # noqa: E402
import vaultnotes as vn  # noqa: E402


class FakeResp(io.BytesIO):
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, retry_after=None):
    hdrs = {"Retry-After": retry_after} if retry_after else {}
    return urllib.error.HTTPError("https://x.test/", code, "err", hdrs, None)


class TestNet(unittest.TestCase):
    def setUp(self):
        self.sleeps = []
        p = mock.patch.object(net, "sleep", side_effect=self.sleeps.append)
        p.start()
        self.addCleanup(p.stop)
        net._last_call.clear()

    def test_redact(self):
        self.assertEqual(net.redact("https://api.openalex.org/works?api_key=SECRET123&x=1"),
                         "https://api.openalex.org/works?api_key=***&x=1")

    def test_error_message_never_contains_key(self):
        with mock.patch("urllib.request.urlopen", side_effect=http_error(403)):
            with self.assertRaises(net.HttpError) as cm:
                net.get("https://api.openalex.org/works/doi:10.1/x?api_key=SECRET123")
        self.assertNotIn("SECRET123", str(cm.exception))

    def test_retries_429_then_succeeds(self):
        seq = [http_error(429), http_error(503), FakeResp(b"ok")]
        with mock.patch("urllib.request.urlopen", side_effect=seq):
            self.assertEqual(net.get("https://x.test/a"), b"ok")
        self.assertEqual(self.sleeps, [5.0, 15.0])

    def test_three_attempts_then_raise(self):
        with mock.patch("urllib.request.urlopen", side_effect=[http_error(500)] * 3) as m:
            with self.assertRaises(net.HttpError) as cm:
                net.get("https://x.test/a")
        self.assertEqual(m.call_count, 3)
        self.assertEqual(cm.exception.code, 500)

    def test_404_not_retried(self):
        with mock.patch("urllib.request.urlopen", side_effect=[http_error(404)]) as m:
            with self.assertRaises(net.HttpError) as cm:
                net.get("https://x.test/a")
        self.assertEqual(m.call_count, 1)
        self.assertTrue(cm.exception.not_found)

    def test_retry_after_honoured_and_capped(self):
        seq = [http_error(429, "2"), http_error(429, "999"), FakeResp(b"ok")]
        with mock.patch("urllib.request.urlopen", side_effect=seq):
            net.get("https://x.test/a")
        self.assertEqual(self.sleeps, [2.0, 60.0])

    def test_host_spacing(self):
        clock = iter([100.0, 100.5, 103.0])
        with mock.patch.object(net, "monotonic", side_effect=lambda: next(clock)), \
             mock.patch("urllib.request.urlopen", side_effect=[FakeResp(b"1"), FakeResp(b"2")]):
            net.get("https://export.arxiv.org/api/query?id_list=1")
            net.get("https://export.arxiv.org/api/query?id_list=2")
        self.assertEqual(self.sleeps, [2.5])


class TestVaultNotes(unittest.TestCase):
    NOTE = ('---\nid: P-0001\ntitle: "Hello: world"\nauthors: ["Doe, Jane", "Roe, R."]\n'
            'doi:\nlinked_papers: [P-0001, P-0002]\n---\n\n## Body\ntext\n')

    def test_split_and_get(self):
        fm, body = vn.split_frontmatter(self.NOTE)
        self.assertEqual(vn.fm_get(fm, "title"), "Hello: world")
        self.assertIsNone(vn.fm_get(fm, "doi"))
        self.assertIsNone(vn.fm_get(fm, "absent"))
        self.assertIn("## Body", body)

    def test_flow_list(self):
        self.assertEqual(vn.parse_flow_list('["Doe, Jane", "Roe, R."]'), ["Doe, Jane", "Roe, R."])
        self.assertEqual(vn.parse_flow_list("[P-0001, P-0002]"), ["P-0001", "P-0002"])
        self.assertEqual(vn.parse_flow_list("[]"), [])
        self.assertEqual(vn.parse_flow_list(None), [])

    def test_send_never_variants(self):
        for ln in ("send: never", "send: NEVER", 'send: "never"', "send: 'Never'  # private",
                   "Send : never", "send:never"):
            self.assertTrue(vn.is_send_never([ln]), ln)
        for ln in ("send: always", "send:", "send: never-ever", "resend: never", "# send: never"):
            self.assertFalse(vn.is_send_never([ln]), ln)

    def test_set_fields_replaces_and_appends(self):
        text = vn.set_fields(self.NOTE, {"resolved": True, "openalex_id": "W1", "doi": ""})
        fm, body = vn.split_frontmatter(text)
        self.assertIn("resolved: true", fm)
        self.assertIn("openalex_id: W1", fm)
        self.assertIn("doi:", fm)
        self.assertEqual(sum(1 for ln in fm if ln.startswith("doi")), 1)
        self.assertEqual(body, vn.split_frontmatter(self.NOTE)[1])
        again = vn.set_fields(text, {"resolved": False, "openalex_id": ""})
        fm2, _ = vn.split_frontmatter(again)
        self.assertIn("resolved: false", fm2)
        self.assertIn("openalex_id:", fm2)
        self.assertEqual(sum(1 for ln in fm2 if ln.startswith("resolved")), 1)

    def test_set_fields_quotes(self):
        text = vn.set_fields(self.NOTE, {"resolution_evidence": 'a "b": c'})
        self.assertIn("resolution_evidence: \"a 'b': c\"", text)

    def test_append_revision_creates_section(self):
        out = vn.append_revision_line(self.NOTE, "- 2026-01-01 — x")
        self.assertTrue(out.startswith(self.NOTE.rstrip("\n")))
        self.assertTrue(out.rstrip().endswith("## Revisión de vigencia\n\n- 2026-01-01 — x"))

    def test_append_revision_existing_section_is_append_only(self):
        base = self.NOTE + "\n## Revisión de vigencia\n\n- 2025-01-01 — old\n\n## Otra\nzzz\n"
        out = vn.append_revision_line(base, "- 2026-01-01 — new")
        self.assertIn("- 2025-01-01 — old\n- 2026-01-01 — new\n\n## Otra\nzzz", out)
        self.assertEqual(out.replace("- 2026-01-01 — new\n", ""), base)


if __name__ == "__main__":
    unittest.main()
