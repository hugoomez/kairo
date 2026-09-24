"""Tests for retraction_sweep.py on a synthetic temp vault; network mocked.

Run: python -m unittest discover -s scripts/citations -p "test_*.py"
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import net  # noqa: E402
import retraction_sweep as sw  # noqa: E402
import vaultnotes as vn  # noqa: E402


def paper(pid, title, author, year, doi="", arxiv="", extra=""):
    return (f'---\nid: {pid}\ntitle: "{title}"\nauthors: ["{author}"]\nyear: {year}\n'
            f"doi: {doi}\narxiv: {arxiv}\n{extra}---\n\n## Referencia\n\nsynthetic\n")


def hyp(hid, linked, body="", status="apoyada", extra=""):
    return (f"---\nid: {hid}\nproject: PROJ-001\nstatus: {status}\nlinked_papers: [{', '.join(linked)}]\n"
            f"{extra}---\n\n## Claim\n\nSynthetic claim.\n\n## Justificación\n\n{body}\n")


ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry><id>http://arxiv.org/abs/9999.00002v2</id><published>2019-01-01T00:00:00Z</published>
<title>Withdrawn Gizmo Result</title><summary>This paper has been withdrawn by the authors.</summary>
<author><name>Wen Wu</name></author><arxiv:comment>Withdrawn: error in proof</arxiv:comment></entry>
<entry><id>http://arxiv.org/abs/9999.00003v1</id><published>2020-01-01T00:00:00Z</published>
<title>Healthy Paper</title><summary>Fine.</summary><author><name>Hal Healthy</name></author></entry>
</feed>"""


def fake_get(url, headers=None, **kw):
    if "export.arxiv.org" in url:
        return ATOM.encode()
    if "api.crossref.org/works/10.9/retr" in url:
        return json.dumps({"message": {"DOI": "10.9/retr", "title": ["Retracted Widget Study"],
                                       "author": [{"given": "Pat", "family": "Doe"}],
                                       "issued": {"date-parts": [[2019]]},
                                       "updated-by": [{"DOI": "10.9/notice", "type": "retraction",
                                                       "source": "retraction-watch",
                                                       "updated": {"date-parts": [[2020, 1, 2]]}}]}}).encode()
    if "api.crossref.org/works/10.9/eoc" in url:
        return json.dumps({"message": {"DOI": "10.9/eoc", "title": ["Concerning Study"],
                                       "updated-by": [{"DOI": "10.9/eocn", "type": "expression_of_concern",
                                                       "updated": {"date-parts": [[2024, 5, 1]]}}]}}).encode()
    if "api.openalex.org/works/doi:10.9/retr" in url:
        return json.dumps({"id": "https://openalex.org/W222", "display_name": "Retracted Widget Study",
                           "publication_year": 2019, "is_retracted": True,
                           "authorships": [{"author": {"display_name": "Pat Doe"}}]}).encode()
    if "api.openalex.org/works/doi:10.48550/arxiv.9999.00002" in url:
        return json.dumps({"id": "https://openalex.org/W333", "display_name": "Withdrawn Gizmo Result",
                           "publication_year": 2019, "is_retracted": False,
                           "authorships": [{"author": {"display_name": "Wen Wu"}}]}).encode()
    raise net.HttpError(url, 404, "not found")


class TestHelpers(unittest.TestCase):
    def test_severity(self):
        self.assertEqual(sw.severity("retracted", "linked_papers"), "crítico")
        self.assertEqual(sw.severity("withdrawn", "cites"), "crítico")
        self.assertEqual(sw.severity("retracted", "body"), "importante")
        self.assertEqual(sw.severity("concern", "cites"), "importante")
        self.assertEqual(sw.severity("concern", "body"), "menor")

    def test_warning_line_shape(self):
        p = sw.PaperFlag("P-0001", "retracted", evidence=["Crossref updated-by: retraction notice 10.9/n"])
        c = sw.Citer("ADR-004", "adr", "cites", "crítico")
        line = sw.warning_line(c, p)
        self.assertTrue(line.startswith("⚠️ ADR-004 cita P-0001 (cites) — P-0001 RETRACTADO"))
        self.assertTrue(line.endswith("[crítico]"))


class TestSweep(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        v = self.vault = Path(self.tmp.name)
        (v / "Papers").mkdir()
        hdir = v / "Projects" / "demo" / "Hipotesis"
        adir = v / "Projects" / "demo" / "Producto"
        hdir.mkdir(parents=True)
        adir.mkdir(parents=True)
        (v / "Papers" / "P-0001 retr.md").write_text(paper("P-0001", "Retracted Widget Study", "Doe, Pat", 2019,
                                                           doi="10.9/retr"), encoding="utf-8")
        (v / "Papers" / "P-0002 wd.md").write_text(paper("P-0002", "Withdrawn Gizmo Result", "Wu, Wen", 2019,
                                                         arxiv="9999.00002"), encoding="utf-8")
        (v / "Papers" / "P-0003 ok.md").write_text(paper("P-0003", "Healthy Paper", "Healthy, Hal", 2020,
                                                         arxiv="9999.00003"), encoding="utf-8")
        (v / "Papers" / "P-0004 eoc.md").write_text(paper("P-0004", "Concerning Study", "Con, Cy", 2022,
                                                          doi="10.9/eoc"), encoding="utf-8")
        (v / "Papers" / "P-0005 private.md").write_text(
            paper("P-0005", "Hidden Aardvark Paper", "Secretauthor, Z", 2021, doi="10.9/private",
                  extra="send: never\n"), encoding="utf-8")
        self.h1 = hdir / "H-0001 synthetic one.md"
        self.h1.write_text(hyp("H-0001", ["P-0001", "P-0003"]), encoding="utf-8")
        self.h2 = hdir / "H-0002 synthetic two.md"
        self.h2.write_text(hyp("H-0002", ["P-0003"], body="Ver P-0002 §3 para el contexto."), encoding="utf-8")
        self.h3 = hdir / "H-0003 private hyp.md"
        self.h3.write_text(hyp("H-0003", ["P-0001"], body="PRIVATE BODY TEXT", extra="send: never\n"),
                           encoding="utf-8")
        self.adr = adir / "ADR-001 synthetic.md"
        self.adr.write_text("---\nid: ADR-001\nstatus: Accepted\ndate: 2026-01-01\ncites: [H-0001, P-0002, P-0004]\n"
                            "---\n\n## Decisión\n\nKeep it.\n", encoding="utf-8")
        for p in (mock.patch.object(net, "get", side_effect=fake_get),
                  mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "OPENALEX_API_KEY"},
                                  clear=True)):
            p.start()
            self.addCleanup(p.stop)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sw.main(["--vault", str(self.vault), *args])
        return code, out.getvalue(), err.getvalue()

    def snapshot(self):
        return {p: p.read_text(encoding="utf-8") for p in self.vault.rglob("*.md")}

    def test_report_only(self):
        before = self.snapshot()
        code, out, _ = self.run_cli("--json")
        self.assertEqual(code, 0)
        self.assertEqual(before, self.snapshot())
        d = json.loads(out)
        by = {p["id"]: p for p in d["papers"]}
        self.assertEqual(by["P-0001"]["status"], "retracted")
        self.assertEqual(by["P-0002"]["status"], "withdrawn")
        self.assertEqual(by["P-0003"]["status"], "clear")
        self.assertEqual(by["P-0004"]["status"], "concern")
        self.assertEqual(by["P-0005"], {"id": "P-0005", "status": "skipped_send_never"})
        c1 = {(c["id"], c["via"], c["severity"]) for c in by["P-0001"]["citers"]}
        self.assertEqual(c1, {("H-0001", "linked_papers", "crítico"), ("H-0003", "linked_papers", "crítico")})
        c2 = {(c["id"], c["via"], c["severity"]) for c in by["P-0002"]["citers"]}
        self.assertEqual(c2, {("H-0002", "body", "importante"), ("ADR-001", "cites", "crítico")})
        c4 = {(c["id"], c["via"], c["severity"]) for c in by["P-0004"]["citers"]}
        self.assertEqual(c4, {("ADR-001", "cites", "importante")})
        self.assertTrue(by["P-0001"]["newly"])

    def test_no_private_content_in_output(self):
        code, out, err = self.run_cli()
        self.assertIn("P-0005: skipped (send: never)", out)
        self.assertIn("H-0003", out)                   # flagged by id
        for s in ("PRIVATE BODY TEXT", "Hidden Aardvark", "Secretauthor", "10.9/private",
                  "synthetic one", "private hyp"):          # no content, no file names
            self.assertNotIn(s, out + err)

    def test_write_appends_and_never_touches_status(self):
        code, out, _ = self.run_cli("--write")
        self.assertEqual(code, 0)
        h1 = self.h1.read_text(encoding="utf-8")
        self.assertIn("## Revisión de vigencia", h1)
        self.assertIn("[crítico] P-0001 RETRACTADO", h1)
        self.assertIn("status: apoyada", h1)
        self.assertTrue(h1.startswith(hyp("H-0001", ["P-0001", "P-0003"]).rstrip("\n")))
        adr = self.adr.read_text(encoding="utf-8")
        self.assertIn("P-0002 RETIRADO", adr)
        self.assertIn("P-0004 CON EXPRESIÓN DE PREOCUPACIÓN", adr)
        self.assertIn("## Decisión\n\nKeep it.", adr)
        # the paper's resolution fields were written
        fm = vn.split_frontmatter((self.vault / "Papers" / "P-0001 retr.md").read_text(encoding="utf-8"))[0]
        self.assertEqual(vn.fm_get(fm, "resolution_status"), "retracted")
        self.assertEqual(vn.fm_get(fm, "resolved"), "true")
        self.assertEqual(vn.fm_get(fm, "openalex_id"), "W222")
        # healthy paper and private paper untouched
        self.assertNotIn("resolution_status", (self.vault / "Papers" / "P-0003 ok.md").read_text(encoding="utf-8"))
        self.assertNotIn("resolution_status",
                         (self.vault / "Papers" / "P-0005 private.md").read_text(encoding="utf-8"))

    def test_write_is_idempotent(self):
        self.run_cli("--write")
        first = self.snapshot()
        code, out, _ = self.run_cli("--json", "--write")
        second = self.snapshot()
        for p in (self.h1, self.h2, self.adr):
            self.assertEqual(first[p], second[p], p.name)
        by = {p["id"]: p for p in json.loads(out)["papers"]}
        self.assertFalse(by["P-0001"]["newly"])    # already recorded as retracted

    def test_invalid_vault(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(sw.main(["--vault", str(self.vault / "nope")]), 2)


if __name__ == "__main__":
    unittest.main()
