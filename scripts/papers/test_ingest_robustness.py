"""A malformed fetched record is a refusal or a lost source, never a traceback;
a note and its manifest are written atomically (invented ids and text)."""

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_ingest_paper as tip  # noqa: E402

ip = tip.ip


def broken(kind: str, body: bytes):
    base = tip.make_fetch()

    def fetch(url, headers):
        if kind in url:
            return body
        return base(url, headers)
    return fetch


class MalformedRecords(tip.Base):
    def test_a_garbled_arxiv_feed_is_a_refusal(self):
        code, out = self.add("--arxiv", "0000.11111", fetch=broken("export.arxiv.org", b"<feed><entry>"))
        self.assertEqual(code, 2, out)
        self.assertIn("ilegible", out["refused"])

    def test_a_garbled_or_odd_crossref_record_is_a_refusal(self):
        for body in (b"{not json", b"[]", json.dumps({"message": {"title": "x", "issued": [2031]}}).encode()):
            code, out = self.add("--doi", "10.0000/toy.2031.7", fetch=broken("api.crossref.org", body))
            self.assertEqual(code, 2, (body, out))
            self.assertIn("ilegible", out["refused"])

    def test_a_garbled_openalex_record_is_a_lost_source(self):
        code, out = self.add("--arxiv", "0000.11111", fetch=broken("api.openalex.org", b"[1, 2]"))
        self.assertEqual(code, 0, out)
        self.assertTrue(any("OpenAlex" in w for w in out["warnings"]), out["warnings"])

    def test_verify_reports_an_unreadable_kept_record(self):
        self.add("--arxiv", "0000.11111")
        m = self.vault / "Papers/_fuentes/P-0001/fuentes.json"
        man = json.loads(m.read_text(encoding="utf-8"))
        raw = self.vault / "Papers/_fuentes/P-0001/metadata-arxiv.xml"
        raw.write_bytes(b"<feed><entry>")
        for f in man["files"]:
            if f["file"] == "metadata-arxiv.xml":
                f["sha256"] = ip.sha256(raw.read_bytes())
        m.write_text(json.dumps(man), encoding="utf-8")
        code, out = self.run_cli("verify", "--vault", str(self.vault))
        self.assertEqual(code, 3, out)
        self.assertTrue(any("ilegible" in p for p in out["notes"][0]["problems"]), out)

    def test_verify_reports_an_unreadable_manifest(self):
        self.add("--arxiv", "0000.11111")
        (self.vault / "Papers/_fuentes/P-0001/fuentes.json").write_text("{oops", encoding="utf-8")
        code, out = self.run_cli("verify", "--vault", str(self.vault))
        self.assertEqual(code, 3, out)
        self.assertEqual(out["notes"][0]["status"], "integrity_problem")


class AtomicWrites(tip.Base):
    def test_a_failed_write_leaves_the_note_whole(self):
        self.add("--arxiv", "0000.11111")
        p = self.note()
        before = p.read_text(encoding="utf-8")
        orig = ip.os.replace

        def fail(src, dst):
            raise OSError("disk full (invented)")
        ip.os.replace = fail
        try:
            code, out = self.run_cli("zotero-key", "--vault", str(self.vault), "--id", "P-0001", "--key", "doe2031")
        finally:
            ip.os.replace = orig
        self.assertEqual(code, 1, out)
        self.assertEqual(p.read_text(encoding="utf-8"), before)
        self.assertEqual([x.name for x in p.parent.iterdir() if ".tmp" in x.name], [])

    def test_the_manifest_is_written_atomically_too(self):
        calls = []
        orig = ip.os.replace

        def spy(src, dst):
            calls.append(Path(dst).name)
            return orig(src, dst)
        ip.os.replace = spy
        try:
            self.add("--arxiv", "0000.11111")
        finally:
            ip.os.replace = orig
        self.assertIn("fuentes.json", calls)
        self.assertIn(self.note().name, calls)


if __name__ == "__main__":
    unittest.main()
