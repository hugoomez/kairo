"""Tests for resolve_refs.py -- matcher, decision, CLI; network mocked, synthetic notes.

Run: python -m unittest discover -s scripts/citations -p "test_*.py"
"""

import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import net  # noqa: E402
import resolve_refs as rr  # noqa: E402
import vaultnotes as vn  # noqa: E402

# Two synthetic "real" papers used to build a chimeric citation.
X = {"title": "Sparse Widgets Improve Gadget Learning", "authors": ["Alvarez, Marta", "Chen, Li"], "year": 2021}
Y = {"title": "A Theory of Recurrent Doohickeys", "authors": ["Okafor, Ngozi"], "year": 2018}


def rec(src, title, names, year, **kw):
    return rr.SourceRecord(src, title=title, authors=[{"name": n} for n in names], year=year, **kw)


class TestNormalization(unittest.TestCase):
    def test_fold(self):
        self.assertEqual(rr.fold("Kramár, János"), "kramar janos")
        self.assertEqual(rr.fold("Straße—Ønsker"), "strasse onsker")

    def test_normalize_title(self):
        self.assertEqual(rr.normalize_title("Early Stopping — But When?"), rr.normalize_title("Early Stopping - But When"))
        self.assertEqual(rr.normalize_title("RETRACTED: Foo <i>via</i> bar"), "foo via bar")
        self.assertEqual(rr.normalize_title(r"On \emph{grokking} in $\mathbb{Z}_p$"), "on grokking in z p")


class TestTitleLevel(unittest.TestCase):
    def test_exact(self):
        self.assertEqual(rr.title_level("Deep Things: A Study", "deep things a study")[0], "exact")

    def test_typo_is_close(self):
        self.assertEqual(rr.title_level("Towards understanding grokking", "Toward understanding grokking")[0], "close")

    def test_dropped_subtitle_is_close(self):
        self.assertEqual(rr.title_level("Deep Double Descent",
                                        "Deep Double Descent: Where Bigger Models and More Data Hurt")[0], "close")

    def test_different_paper_differs(self):
        self.assertEqual(rr.title_level(X["title"], Y["title"])[0], "differs")

    def test_short_prefix_not_enough(self):
        # a 2-word prefix of a longer title is not accepted as "subtitle dropped"
        self.assertEqual(rr.title_level("Deep Learning", "Deep Learning for Protein Folding at Scale")[0], "differs")

    def test_missing(self):
        self.assertEqual(rr.title_level("", "x")[0], "missing")


class TestAuthorYear(unittest.TestCase):
    def test_surname_of(self):
        self.assertEqual(rr.surname_of("Power, Alethea"), "Power")
        self.assertEqual(rr.surname_of("Alethea Power"), "Power")

    def test_accent_insensitive(self):
        self.assertTrue(rr.surname_matches("Kramár", "Janos Kramar"))
        self.assertTrue(rr.surname_matches("Muller", "Klaus-Robert Müller"))

    def test_multiword_and_hyphen(self):
        self.assertTrue(rr.surname_matches("van den Oord", "Aaron van den Oord"))
        self.assertTrue(rr.surname_matches("Ben-David", "Shai Ben David"))
        self.assertTrue(rr.surname_matches("Garcia", "", family="García"))

    def test_different_surname(self):
        self.assertFalse(rr.surname_matches("Okafor", "Marta Alvarez"))
        self.assertFalse(rr.surname_matches("Li", "Lin Wang"))   # short token must match whole word

    def test_author_levels(self):
        auth = [{"name": "Marta Alvarez"}, {"name": "Li Chen"}]
        self.assertEqual(rr.author_level("Alvarez", auth), "exact")
        self.assertEqual(rr.author_level("Chen", auth), "close")       # order differs
        self.assertEqual(rr.author_level("Okafor", auth), "differs")
        self.assertEqual(rr.author_level("Alvarez", []), "missing")

    def test_year_levels(self):
        self.assertEqual(rr.year_level(2020, 2020), "exact")
        self.assertEqual(rr.year_level(2020, 2021), "close")
        self.assertEqual(rr.year_level(2019, 2021), "differs")
        self.assertEqual(rr.year_level(None, 2021), "missing")


class TestMatchRecord(unittest.TestCase):
    def test_exact(self):
        lvl, diff = rr.match_record(X, rec("openalex", X["title"], ["Marta Alvarez", "Li Chen"], 2021))
        self.assertEqual((lvl, diff), ("exact", []))

    def test_preprint_vs_proceedings_year_is_close(self):
        lvl, diff = rr.match_record(X, rec("openalex", X["title"], ["Marta Alvarez"], 2022))
        self.assertEqual(lvl, "close")
        self.assertEqual([(d["field"], d["note"], d["source"]) for d in diff], [("year", 2021, 2022)])

    def test_chimeric_title_of_x_authors_year_of_y(self):
        # the note carries X's title but Y's author and year; the DOI resolves to X
        chimera = {"title": X["title"], "authors": Y["authors"], "year": Y["year"]}
        lvl, diff = rr.match_record(chimera, rec("openalex", X["title"], ["Marta Alvarez", "Li Chen"], 2021))
        self.assertEqual(lvl, "mismatch")
        self.assertEqual({d["field"] for d in diff if d["level"] == "differs"}, {"first_author", "year"})

    def test_chimeric_resolved_to_y(self):
        # same chimera, but the id in the note points at Y: the title differs
        chimera = {"title": X["title"], "authors": Y["authors"], "year": Y["year"]}
        lvl, diff = rr.match_record(chimera, rec("crossref", Y["title"], ["Ngozi Okafor"], 2018))
        self.assertEqual(lvl, "mismatch")
        self.assertEqual([d["field"] for d in diff], ["title"])

    def test_wrong_year_only_is_mismatch(self):
        lvl, _ = rr.match_record(X, rec("arxiv", X["title"], ["Marta Alvarez"], 2015))
        self.assertEqual(lvl, "mismatch")

    def test_source_without_authors_is_close(self):
        lvl, diff = rr.match_record(X, rec("crossref", X["title"], [], 2021))
        self.assertEqual(lvl, "close")
        self.assertEqual(diff[0]["level"], "missing")

    def test_worst(self):
        self.assertEqual(rr.worst(["exact", "close"]), "close")
        self.assertEqual(rr.worst(["exact", "mismatch", "close"]), "mismatch")
        self.assertIsNone(rr.worst([]))


class TestAdapters(unittest.TestCase):
    def test_openalex(self):
        w = {"id": "https://openalex.org/W123", "display_name": "T", "publication_year": 2020,
             "authorships": [{"author": {"display_name": "A B"}}], "is_retracted": False}
        r = rr.from_openalex(w, "doi")
        self.assertEqual((r.openalex_id, r.title, r.year, r.authors[0]["name"]), ("W123", "T", 2020, "A B"))

    def test_crossref(self):
        m = {"title": ["T"], "author": [{"given": "Lutz", "family": "Example"}], "issued": {"date-parts": [[1998]]}}
        r = rr.from_crossref(m)
        self.assertEqual((r.title, r.year, r.authors[0]["family"]), ("T", 1998, "Example"))

    def test_openalex_short_id(self):
        self.assertEqual(rr.openalex_short_id("https://openalex.org/W42"), "W42")
        self.assertIsNone(rr.openalex_short_id("https://openalex.org/A42"))


def trace(*names):
    return [rr.SourceTrace(n, "found") for n in names]


class TestDecide(unittest.TestCase):
    note = {**X, "id": "P-0001", "prev_status": None}

    def oa(self, year=2021, title=None):
        return rec("openalex", title or X["title"], ["Marta Alvarez"], year, openalex_id="W9", found_by="doi")

    def test_resolved(self):
        r = rr.decide(self.note, [self.oa()], self.oa(), False, "clear", [], False, trace("openalex"))
        self.assertEqual((r.status, r.resolved, r.openalex_id, r.match), ("resolved", True, "W9", "exact"))

    def test_close_gets_menor_flag(self):
        o = self.oa(year=2022)
        r = rr.decide(self.note, [o], o, False, "clear", [], False, trace("openalex"))
        self.assertEqual(r.status, "resolved")
        self.assertEqual([f["severity"] for f in r.flags], ["menor"])

    def test_mismatch_not_resolved_and_critical(self):
        o = self.oa(title=Y["title"])
        r = rr.decide(self.note, [o], o, False, "clear", [], False, trace("openalex"))
        self.assertEqual((r.status, r.resolved, r.openalex_id), ("mismatch", False, None))
        self.assertEqual(r.flags[0]["severity"], "crítico")

    def test_mismatch_in_any_source_wins(self):
        o = self.oa()
        bad = rec("crossref", Y["title"], ["Ngozi Okafor"], 2018)
        r = rr.decide(self.note, [o, bad], o, False, "clear", [], False, trace("openalex", "crossref"))
        self.assertEqual(r.status, "mismatch")

    def test_openalex_wrong_title_but_registrar_agrees_is_source_error(self):
        # OpenAlex record for the note's DOI: right authors/year, corrupted title;
        # the arXiv record (the id's registrar) matches the note exactly
        o = self.oa(title="Certified Widgets: a derivative work about something else")
        ax = rec("arxiv", X["title"], ["Marta Alvarez", "Li Chen"], 2021)
        r = rr.decide(self.note, [o, ax], o, False, "clear", [], False, trace("openalex", "arxiv"))
        self.assertEqual((r.status, r.match, r.resolved, r.openalex_id), ("resolved", "close", True, "W9"))
        self.assertIn("source_error", [d["level"] for d in r.diff])
        self.assertIn("importante", [f["severity"] for f in r.flags])

    def test_chimera_cannot_use_source_error_rule(self):
        # chimera: X's title, Y's author/year; OpenAlex and arXiv both hold X -> both disagree
        chimera = {"title": X["title"], "authors": Y["authors"], "year": Y["year"], "id": "P-9", "prev_status": None}
        o = rec("openalex", X["title"], ["Marta Alvarez"], 2021, openalex_id="W9", found_by="doi")
        ax = rec("arxiv", X["title"], ["Marta Alvarez"], 2021)
        r = rr.decide(chimera, [o, ax], o, False, "clear", [], False, trace("openalex", "arxiv"))
        self.assertEqual(r.status, "mismatch")
        # chimera whose OpenAlex title differs too, registrar disagrees on author -> still mismatch
        o2 = rec("openalex", Y["title"], ["Ngozi Okafor"], 2018, openalex_id="W8", found_by="doi")
        ax2 = rec("arxiv", Y["title"], ["Ngozi Okafor"], 2018)
        r2 = rr.decide(chimera, [o2, ax2], o2, False, "clear", [], False, trace("openalex", "arxiv"))
        self.assertEqual(r2.status, "mismatch")

    def test_retracted_still_resolved(self):
        o = self.oa()
        r = rr.decide(self.note, [o], o, False, "retracted", ["Crossref updated-by: retraction"], False,
                      trace("openalex"))
        self.assertEqual((r.status, r.resolved, r.openalex_id), ("retracted", True, "W9"))
        self.assertEqual(r.flags[0]["severity"], "crítico")
        self.assertTrue(r.newly_flagged)

    def test_fallback_only_is_unresolved_importante(self):
        ax = rec("arxiv", X["title"], ["Marta Alvarez"], 2021)
        r = rr.decide(self.note, [ax], None, False, "clear", [], False, trace("arxiv"))
        self.assertEqual((r.status, r.resolved, r.openalex_id), ("unresolved", False, None))
        self.assertEqual(r.flags[0]["severity"], "importante")
        self.assertTrue(any("fallback: arxiv" in e for e in r.evidence))

    def test_not_found_anywhere_is_critico(self):
        r = rr.decide(self.note, [], None, False, "clear", [], False, [])
        self.assertEqual((r.status, r.flags[0]["severity"]), ("unresolved", "crítico"))

    def test_openalex_lost(self):
        r = rr.decide(self.note, [], None, True, "clear", [], False, [])
        self.assertEqual(r.status, "unresolved")
        self.assertTrue(r.openalex_lost)
        self.assertEqual(r.flags[0]["severity"], "importante")

    def test_invariant_resolved_iff_openalex_id(self):
        for args in ((self.oa(), False, "clear"), (None, False, "clear"), (None, True, "withdrawn"),
                     (self.oa(), False, "withdrawn")):
            o, lost, st = args
            r = rr.decide(self.note, [o] if o else [], o, lost, st, [], False, [])
            self.assertEqual(r.resolved, bool(r.openalex_id and re.fullmatch(r"W\d+", r.openalex_id)), args)

    def test_gate_exit(self):
        ok = rr.Result("P-1", "resolved", True, "exact", "W1")
        lost = rr.Result("P-2", "unresolved", openalex_lost=True)
        mis = rr.Result("P-3", "mismatch")
        rlost = rr.Result("P-4", "resolved", True, "exact", "W4", retraction_lost=["arxiv"])
        self.assertEqual(rr.gate_exit([ok]), 0)
        self.assertEqual(rr.gate_exit([ok, lost]), 1)
        self.assertEqual(rr.gate_exit([ok, rlost]), 1)
        self.assertEqual(rr.gate_exit([ok, lost, mis]), 2)
        self.assertEqual(rr.gate_exit([rr.Result("P-5", "skipped_send_never")]), 2)


ATOM_X = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry><id>http://arxiv.org/abs/9999.00001v1</id><published>2021-03-01T00:00:00Z</published>
<title>Sparse Widgets Improve Gadget Learning</title><summary>Abstract.</summary>
<author><name>Marta Alvarez</name></author><author><name>Li Chen</name></author></entry></feed>"""


def note_text(pid, title, authors, year, doi="", arxiv="", extra=""):
    a = ", ".join(f'"{x}"' for x in authors)
    return (f'---\nid: {pid}\ntitle: "{title}"\nauthors: [{a}]\nyear: {year}\ndoi: {doi}\n'
            f"arxiv: {arxiv}\n{extra}---\n\n## Referencia\n\nsynthetic\n")


class FakeWeb:
    """Mock for net.get: OpenAlex knows X by its arXiv DOI; Crossref knows a
    retracted paper; everything else 404s. Records every URL requested."""

    def __init__(self):
        self.urls = []

    def __call__(self, url, headers=None, **kw):
        self.urls.append(url)
        if "api.openalex.org/works/doi:10.48550/arxiv.9999.00001" in url:
            return json.dumps({"id": "https://openalex.org/W111", "display_name": X["title"],
                               "publication_year": 2021, "is_retracted": False,
                               "authorships": [{"author": {"display_name": "Marta Alvarez"}}]}).encode()
        if "api.openalex.org/works/doi:10.9%2Fretr" in url or "api.openalex.org/works/doi:10.9/retr" in url:
            return json.dumps({"id": "https://openalex.org/W222", "display_name": "Retracted Widget Study",
                               "publication_year": 2019, "is_retracted": True,
                               "authorships": [{"author": {"display_name": "Pat Doe"}}]}).encode()
        if "api.crossref.org/works/10.9/retr" in url:
            return json.dumps({"message": {"DOI": "10.9/retr", "title": ["RETRACTED: Retracted Widget Study"],
                                           "author": [{"given": "Pat", "family": "Doe"}],
                                           "issued": {"date-parts": [[2019]]},
                                           "updated-by": [{"DOI": "10.9/notice", "type": "retraction",
                                                           "source": "retraction-watch",
                                                           "updated": {"date-parts": [[2020, 1, 2]]}}]}}).encode()
        if "export.arxiv.org" in url:
            return ATOM_X.encode()
        if "api.openalex.org/works?" in url:
            return json.dumps({"results": []}).encode()
        raise net.HttpError(url, 404, "not found")


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.papers = Path(self.tmp.name) / "Papers"
        self.papers.mkdir()
        (self.papers / "P-0001 x.md").write_text(note_text("P-0001", X["title"], X["authors"], 2021,
                                                           arxiv="9999.00001"), encoding="utf-8")
        (self.papers / "P-0002 chimera.md").write_text(
            note_text("P-0002", X["title"], Y["authors"], 2018, arxiv="9999.00001"), encoding="utf-8")
        (self.papers / "P-0003 retr.md").write_text(
            note_text("P-0003", "Retracted Widget Study", ["Doe, Pat"], 2019, doi="10.9/retr"), encoding="utf-8")
        (self.papers / "P-0004 private.md").write_text(
            note_text("P-0004", "Confidential Unicorn Title", ["Secretperson, Q"], 2020, doi="10.9/private",
                      extra="send: \"never\"  # do not send\n"), encoding="utf-8")
        self.web = FakeWeb()
        env = {k: v for k, v in os.environ.items() if k not in ("OPENALEX_API_KEY",)}
        for p in (mock.patch.object(net, "get", side_effect=self.web),
                  mock.patch.dict(os.environ, env, clear=True)):
            p.start()
            self.addCleanup(p.stop)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = rr.main(["--papers", str(self.papers), *args])
        return code, out.getvalue(), err.getvalue()

    def test_report_json_statuses(self):
        code, out, _ = self.run_cli("--json")
        self.assertEqual(code, 0)
        d = json.loads(out)
        by = {r["id"]: r for r in d["results"]}
        self.assertEqual(by["P-0001"]["status"], "resolved")
        self.assertEqual(by["P-0001"]["openalex_id"], "W111")
        self.assertEqual(by["P-0002"]["status"], "mismatch")
        self.assertEqual(by["P-0003"]["status"], "retracted")
        self.assertTrue(by["P-0003"]["resolved"])
        self.assertEqual(by["P-0004"], {"id": "P-0004", "status": "skipped_send_never"})
        self.assertEqual(d["openalex_key"], "missing")

    def test_send_never_never_leaves_the_machine(self):
        code, out, err = self.run_cli()
        self.assertIn("P-0004: skipped (send: never)", out)
        blob = out + err + " ".join(self.web.urls)
        for secret in ("Confidential", "Unicorn", "Secretperson", "10.9/private", "10.9%2Fprivate"):
            self.assertNotIn(secret, blob)

    def test_report_only_writes_nothing(self):
        before = {p.name: p.read_text(encoding="utf-8") for p in self.papers.iterdir()}
        self.run_cli()
        self.assertEqual(before, {p.name: p.read_text(encoding="utf-8") for p in self.papers.iterdir()})

    def test_write_contract_fields(self):
        before = (self.papers / "P-0004 private.md").read_text(encoding="utf-8")
        code, _, _ = self.run_cli("--write")
        self.assertEqual(code, 0)
        fm1 = vn.split_frontmatter((self.papers / "P-0001 x.md").read_text(encoding="utf-8"))[0]
        self.assertEqual(vn.fm_get(fm1, "resolved"), "true")
        self.assertEqual(vn.fm_get(fm1, "openalex_id"), "W111")
        self.assertRegex(vn.fm_get(fm1, "resolution_checked"), r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(vn.fm_get(fm1, "resolution_status"), "resolved")
        self.assertEqual(vn.fm_get(fm1, "resolution_match"), "exact")
        fm2 = vn.split_frontmatter((self.papers / "P-0002 chimera.md").read_text(encoding="utf-8"))[0]
        self.assertEqual(vn.fm_get(fm2, "resolved"), "false")
        self.assertIsNone(vn.fm_get(fm2, "openalex_id"))
        self.assertEqual(vn.fm_get(fm2, "resolution_status"), "mismatch")
        # body untouched
        self.assertIn("## Referencia\n\nsynthetic", (self.papers / "P-0002 chimera.md").read_text(encoding="utf-8"))
        # send: never untouched
        self.assertEqual(before, (self.papers / "P-0004 private.md").read_text(encoding="utf-8"))

    def test_write_preserves_line_endings(self):
        lf = self.papers / "P-0001 x.md"
        crlf = self.papers / "P-0003 retr.md"
        lf.write_bytes(lf.read_bytes().replace(b"\r\n", b"\n"))
        crlf.write_bytes(crlf.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        self.run_cli("--write")
        self.assertNotIn(b"\r", lf.read_bytes())
        b = crlf.read_bytes()
        self.assertEqual(b.count(b"\n"), b.count(b"\r\n"))
        self.assertIn(b"resolution_status: retracted\r\n", b)

    def test_gate_pass_and_fail(self):
        self.assertEqual(self.run_cli("--gate", "--only", "P-0001")[0], 0)
        self.assertEqual(self.run_cli("--gate", "--only", "P-0001", "P-0002")[0], 2)
        self.assertEqual(self.run_cli("--gate", "--only", "P-0003")[0], 2)
        code, out, _ = self.run_cli("--gate", "--only", "P-0004")
        self.assertEqual(code, 2)
        self.assertIn("P-0004: skipped (send: never)", out)
        self.assertNotIn("Unicorn", out)

    def test_gate_ignores_stale_fields(self):
        # a note claiming resolved:true is still re-checked live
        p = self.papers / "P-0002 chimera.md"
        p.write_text(vn.set_fields(p.read_text(encoding="utf-8"),
                                   {"resolved": True, "openalex_id": "W111", "resolution_status": "resolved"}),
                     encoding="utf-8")
        self.assertEqual(self.run_cli("--gate", "--only", "P-0002")[0], 2)

    def test_gate_openalex_down_is_error(self):
        def down(url, headers=None, **kw):
            if "openalex" in url:
                raise net.HttpError(url, 429, "Too Many Requests")
            return self.web(url, headers)
        with mock.patch.object(net, "get", side_effect=down):
            code, out, _ = self.run_cli("--gate", "--only", "P-0001")
        self.assertEqual(code, 1)

    def test_gate_json(self):
        code, out, _ = self.run_cli("--gate", "--json", "--only", "P-0001")
        self.assertEqual(json.loads(out)["gate"], {"passed": True, "exit": 0})

    def test_invalid_input(self):
        self.assertEqual(self.run_cli("--only", "P-9999")[0], 2)
        self.assertEqual(self.run_cli("--gate", "--only", "P-9999")[0], 1)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(rr.main(["--papers", str(Path(self.tmp.name) / "nope")]), 2)

    def test_api_key_sent_but_never_printed(self):
        with mock.patch.dict(os.environ, {"OPENALEX_API_KEY": "SEKRITKEY"}):
            code, out, err = self.run_cli("--json", "--only", "P-0001")
        self.assertTrue(any("api_key=SEKRITKEY" in u for u in self.web.urls if "openalex" in u))
        self.assertNotIn("SEKRITKEY", out + err)
        self.assertEqual(json.loads(out)["openalex_key"], "set")


if __name__ == "__main__":
    unittest.main()
