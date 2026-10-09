"""A retitled camera-ready: the publisher's own title is recorded as
`published_title`, verbatim from its fetched Crossref record (invented ids and text)."""

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_ingest_paper as tip  # noqa: E402

ip = tip.ip
EXTRA = "<arxiv:doi>10.0000/toy.pub.9</arxiv:doi>"


def fetch_titled(title):
    base = tip.make_fetch(EXTRA)

    def fetch(url, headers):
        if "api.crossref.org" in url:
            return json.dumps({"message": {**tip.CROSSREF["message"], "DOI": "10.0000/toy.pub.9",
                                           "title": [title]}}).encode()
        return base(url, headers)
    return fetch


class PublishedTitle(tip.Base):
    def fm(self):
        return ip.vn.split_frontmatter(self.note().read_text(encoding="utf-8"))[0]

    def test_a_retitled_published_version_records_the_publishers_title(self):
        code, out = self.add("--arxiv", "0000.11111", fetch=fetch_titled("Invented  Toy Codes: The Final Version"))
        self.assertEqual(code, 0, out)
        self.assertEqual(ip.vn.fm_get(self.fm(), "published_title"), "Invented Toy Codes: The Final Version")
        self.assertEqual(self.run_cli("verify", "--vault", str(self.vault))[1]["counts"], {"ok": 1})

    def test_the_same_title_records_nothing(self):
        code, out = self.add("--arxiv", "0000.11111", fetch=fetch_titled("Toy codes for invented qubits"))
        self.assertEqual(code, 0, out)
        self.assertIsNone(ip.vn.fm_get(self.fm(), "published_title"))

    def test_without_a_publisher_record_nothing_is_recorded(self):
        def no_crossref(url, headers):
            if "api.crossref.org" in url:
                raise ip.net.HttpError(url, 404, "not found")
            return tip.make_fetch(EXTRA)(url, headers)
        code, out = self.add("--arxiv", "0000.11111", fetch=no_crossref)
        self.assertEqual(code, 0, out)
        self.assertIsNone(ip.vn.fm_get(self.fm(), "published_title"))

    def test_a_doi_anchored_on_its_preprint_gets_the_publishers_title(self):
        work = {**tip.OPENALEX, "locations": [{"landing_page_url": "https://arxiv.org/abs/0000.11111"}]}

        def fetch(url, headers):
            if "api.openalex.org" in url:
                return json.dumps(work).encode()
            return fetch_titled("Invented Toy Codes, Retitled")(url, headers)
        code, out = self.add("--doi", "10.0000/toy.pub.9", fetch=fetch)
        self.assertEqual(code, 0, out)
        self.assertEqual(ip.vn.fm_get(self.fm(), "published_title"), "Invented Toy Codes, Retitled")
        self.assertEqual(ip.vn.fm_get(self.fm(), "published_venue"), "Journal of Invented Results")


if __name__ == "__main__":
    unittest.main()
