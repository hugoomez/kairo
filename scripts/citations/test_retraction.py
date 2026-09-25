"""Tests for retraction.py and check_retraction.py -- synthetic payloads only.

Run: python -m unittest discover -s scripts/citations -p "test_*.py"
"""

import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_retraction  # noqa: E402
import net  # noqa: E402
import retraction as R  # noqa: E402


def upd(doi, typ, src="publisher", date=(2021, 12, 15)):
    return {"DOI": doi, "type": typ, "label": typ.title(), "source": src,
            "updated": {"date-parts": [list(date)]}}


ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/9999.00001v3</id>
    <published>2020-01-02T00:00:00Z</published>
    <updated>2021-05-06T00:00:00Z</updated>
    <title>A Synthetic   Paper
      On Things</title>
    <summary>We study things.</summary>
    <author><name>Ada Example</name></author>
    <author><name>Bob Sample</name></author>
    <arxiv:comment>12 pages, v3 fixes a typo</arxiv:comment>
    <arxiv:doi>10.1234/SYN.1</arxiv:doi>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/9999.00002v2</id>
    <published>2019-01-01T00:00:00Z</published>
    <title>Another Paper</title>
    <summary>This paper has been withdrawn by the author due to an error in Lemma 2.</summary>
    <author><name>Cy Author</name></author>
    <arxiv:comment>Withdrawn</arxiv:comment>
  </entry>
  <entry>
    <id>http://arxiv.org/api/errors#incorrect_id_format_for_bad</id>
    <title>Error</title>
    <summary>incorrect id format for bad</summary>
  </entry>
</feed>"""


class TestNormalize(unittest.TestCase):
    def test_doi(self):
        self.assertEqual(R.normalize_doi("https://doi.org/10.1007/ABC_3"), "10.1007/abc_3")
        self.assertEqual(R.normalize_doi("doi: 10.1/x."), "10.1/x")
        self.assertIsNone(R.normalize_doi(""))

    def test_arxiv(self):
        self.assertEqual(R.normalize_arxiv("arXiv:2201.02177v3"), "2201.02177")
        self.assertEqual(R.normalize_arxiv("https://arxiv.org/abs/2201.02177"), "2201.02177")
        self.assertEqual(R.normalize_arxiv("math/9911108v6"), "math/9911108")

    def test_arxiv_doi(self):
        self.assertTrue(R.is_arxiv_doi("10.48550/arxiv.2201.02177"))
        self.assertEqual(R.arxiv_from_doi("10.48550/arxiv.2201.02177"), "2201.02177")
        self.assertIsNone(R.arxiv_from_doi("10.1007/x"))


class TestCrossrefFlags(unittest.TestCase):
    def test_clean_record(self):
        c = R.crossref_flags({"DOI": "10.1/ok", "title": ["Fine paper"]})
        self.assertEqual(R.verdict([c])[0], "clear")

    def test_updated_by_retraction_deduplicated(self):
        # shape verified live 2026-09-24: publisher + retraction-watch entries for one notice
        msg = {"DOI": "10.1/bad", "title": ["Bad paper"],
               "updated-by": [upd("10.1/notice", "retraction", "retraction-watch"),
                              upd("10.1/notice", "retraction", "publisher")]}
        c = R.crossref_flags(msg)
        self.assertEqual(R.verdict([c])[0], "retracted")
        self.assertEqual(len(c.signals), 1)
        self.assertIn("10.1/notice", c.signals[0].evidence)
        self.assertIn("2021-12-15", c.signals[0].evidence)

    def test_self_withdrawal(self):
        msg = {"DOI": "10.1/w", "title": ["WITHDRAWN: A paper"],
               "update-to": [upd("10.1/w", "withdrawal")], "updated-by": [upd("10.1/w", "withdrawal")]}
        c = R.crossref_flags(msg)
        self.assertEqual(R.verdict([c])[0], "withdrawn")
        self.assertEqual(c.notice_for, [])

    def test_removal_maps_to_withdrawn(self):
        c = R.crossref_flags({"DOI": "10.1/r", "update-to": [upd("10.1/r", "removal")]})
        self.assertEqual(R.verdict([c])[0], "withdrawn")

    def test_expression_of_concern(self):
        c = R.crossref_flags({"DOI": "10.1/c", "updated-by": [upd("10.1/eoc", "expression_of_concern")]})
        self.assertEqual(R.verdict([c])[0], "concern")

    def test_correction_ignored(self):
        c = R.crossref_flags({"DOI": "10.1/c", "updated-by": [upd("10.1/err", "correction")]})
        self.assertEqual(R.verdict([c])[0], "clear")

    def test_notice_for_other_doi(self):
        msg = {"DOI": "10.1/notice", "title": ["Retraction notice: X"],
               "update-to": [upd("10.1/bad", "retraction")]}
        c = R.crossref_flags(msg)
        self.assertEqual(c.notice_for, ["10.1/bad"])
        self.assertEqual(R.verdict([c])[0], "retracted")

    def test_is_retracted_field_and_title_prefix(self):
        self.assertEqual(R.verdict([R.crossref_flags({"DOI": "10.1/a", "is-retracted": True})])[0], "retracted")
        self.assertEqual(R.verdict([R.crossref_flags({"DOI": "10.1/a", "title": ["RETRACTED: foo"]})])[0],
                         "retracted")

    def test_precedence(self):
        msg = {"DOI": "10.1/x", "updated-by": [upd("10.1/e", "expression_of_concern"),
                                               upd("10.1/r", "retraction")]}
        self.assertEqual(R.verdict([R.crossref_flags(msg)])[0], "retracted")


class TestArxiv(unittest.TestCase):
    def test_parse_feed(self):
        f = R.parse_arxiv_feed(ATOM.encode())
        self.assertEqual(set(f), {"9999.00001", "9999.00002"})   # error entry skipped
        e = f["9999.00001"]
        self.assertEqual(e["title"], "A Synthetic Paper On Things")
        self.assertEqual(e["version"], 3)
        self.assertEqual(e["authors"], ["Ada Example", "Bob Sample"])
        self.assertEqual(e["doi"], "10.1234/syn.1")

    def test_later_version_is_not_withdrawal(self):
        f = R.parse_arxiv_feed(ATOM.encode())
        self.assertEqual(R.arxiv_check(f["9999.00001"]).signals, [])

    def test_withdrawn_entry(self):
        f = R.parse_arxiv_feed(ATOM.encode())
        sig = R.arxiv_check(f["9999.00002"]).signals
        self.assertEqual({s.kind for s in sig}, {"withdrawn"})
        self.assertEqual(len(sig), 2)   # comment + abstract notice

    def test_title_prefix(self):
        self.assertTrue(R.arxiv_withdrawn("Withdrawn: Something", "", "Normal abstract."))

    def test_summary_notice_variants(self):
        for s in ("This submission has been withdrawn by arXiv administrators.",
                  "The authors have withdrawn this paper.",
                  "Paper withdrawn"):
            self.assertTrue(R.arxiv_withdrawn("T", "", s), s)

    def test_plain_abstract_mentioning_withdrawal_elsewhere(self):
        long_abs = "We model drug withdrawal effects in rats. " * 20
        self.assertEqual(R.arxiv_withdrawn("Drug study", "", long_abs), [])

    def test_comment_mentioning_another_withdrawn_paper(self):
        # M5: "withdrawn" about ANOTHER submission is not a withdrawal of this one
        for cm in ("v2: supersedes the withdrawn arXiv:1901.00001",
                   "Withdrawn arXiv:1901.00001 is superseded by this paper",
                   "Replaces a withdrawn version of 1901.00001; 12 pages",
                   "Extends our earlier (withdrawn) work, see 1901.00001",
                   "12 pages; discusses the withdrawal of drug X"):
            self.assertEqual(R.arxiv_withdrawn("T", cm, "Normal abstract."), [], cm)

    def test_comment_withdrawal_notice_variants(self):
        for cm in ("Withdrawn", "withdrawn.", "Withdrawn: error in proof",
                   "This paper has been withdrawn by the author due to a crucial error",
                   "This submission is withdrawn", "Paper withdrawn",
                   "withdrawn by the authors", "12 pages; withdrawn due to an error in Lemma 3",
                   "The authors withdraw this paper"):
            self.assertTrue(R.arxiv_withdrawn("T", cm, "Normal abstract."), cm)

    def test_missing_entry(self):
        self.assertEqual(R.arxiv_check(None).state, "not_found")


class TestOpenAlex(unittest.TestCase):
    def test_flag(self):
        self.assertEqual(R.verdict([R.openalex_check({"id": "https://openalex.org/W1", "is_retracted": True})])[0],
                         "retracted")
        self.assertEqual(R.verdict([R.openalex_check({"id": "W1", "is_retracted": False})])[0], "clear")


class TestCheckOneAndCli(unittest.TestCase):
    def setUp(self):
        self.feed = R.parse_arxiv_feed(ATOM.encode())

    def fake_get(self, url, headers=None, **kw):
        if "api.crossref.org" in url:
            if "10.1%2Fbad" in url or "10.1/bad" in url:
                return json.dumps({"message": {"DOI": "10.1/bad",
                                               "updated-by": [upd("10.1/n", "retraction")]}}).encode()
            if "lost" in url:
                raise net.HttpError(url, 503, "unavailable")
            raise net.HttpError(url, 404, "not found")
        if "export.arxiv.org" in url:
            return ATOM.encode()
        raise AssertionError(url)

    def test_check_one_arxiv_doi_skips_crossref(self):
        r = R.check_one("10.48550/arXiv.9999.00002", None, self.feed, {})
        self.assertEqual(r["checks"]["crossref"]["state"], "not_applicable")
        self.assertEqual(r["arxiv"], "9999.00002")
        self.assertTrue(r["withdrawn"])

    def test_cli_counts(self):
        cands = [{"id": "a", "doi": "10.1/bad", "title": "SECRET TITLE"},
                 {"id": "b", "arxiv": "9999.00002"},
                 {"id": "c", "arxiv": "9999.00001", "doi": "10.1/missing"},
                 {"id": "d", "doi": "10.1/lost"}]
        with mock.patch.object(net, "get", side_effect=self.fake_get):
            out = check_retraction.run(cands)
        by = {r["id"]: r for r in out["results"]}
        self.assertTrue(by["a"]["retracted"])
        self.assertTrue(by["b"]["withdrawn"])
        self.assertEqual(by["c"]["status"], "clear")
        self.assertEqual(by["d"]["checks"]["crossref"]["state"], "lost")
        self.assertEqual(out["counts"]["crossref"], {"checked": 2, "removed": 1, "lost": 1})
        self.assertEqual(out["counts"]["arxiv"], {"checked": 2, "removed": 1, "lost": 0})
        self.assertNotIn("SECRET TITLE", json.dumps(out))   # extra keys never echoed

    def test_cli_invalid_input(self):
        with mock.patch.object(sys, "stdin", io.StringIO("{not json")), \
             mock.patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(check_retraction.main(["-"]), 2)
        with mock.patch.object(sys, "stdin", io.StringIO('{"a": 1}')), \
             mock.patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(check_retraction.main(["-"]), 2)

    def test_cli_error_exit(self):
        with mock.patch.object(sys, "stdin", io.StringIO('[{"doi": "10.1/x"}]')), \
             mock.patch.object(check_retraction, "run", side_effect=RuntimeError("boom")), \
             mock.patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(check_retraction.main(["-"]), 1)


if __name__ == "__main__":
    unittest.main()
