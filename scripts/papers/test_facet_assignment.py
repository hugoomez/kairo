"""Tests for facet_assignment.py (invented notes, no network).

Run: python -m unittest test_facet_assignment   (from scripts/papers/)
"""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import facet_assignment as fa  # noqa: E402


def note(pid, projects, facets=None, extra=""):
    fm = ["---", f"id: {pid}", "title: Synthetic", f"projects: [{', '.join(projects)}]"]
    if facets is not None:
        fm += ["facets:"] + [f"  - {f}" for f in facets]
    fm += [extra] if extra else []
    return "\n".join(fm + ["added: 2030-01-01", "---", "", "## Resumen", "", "x", ""])


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = fa.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


class TestFacets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.v = Path(self.tmp.name)
        pap = self.v / "Papers"
        (pap / "_notas").mkdir(parents=True)
        (pap / "P-0901 One.md").write_text(note("P-0901", ["PROJ-900", "PROJ-901"], [
            '{project: PROJ-900, facet: A, matched: "toy term"}',
            '{project: PROJ-900, facet: C, matched: "other, with comma"}',
            '{project: PROJ-901, facet: A, matched: "elsewhere"}']), encoding="utf-8")
        (pap / "P-0902 Two.md").write_text(note("P-0902", ["PROJ-900"], [
            '{project: PROJ-900, facet: B, matched: null, unrecovered: "not in the log"}']),
            encoding="utf-8")
        (pap / "P-0903 Three.md").write_text(note("P-0903", ["PROJ-900"]), encoding="utf-8")
        (pap / "P-0904 Other.md").write_text(note("P-0904", ["PROJ-901"]), encoding="utf-8")
        (pap / "P-0905 Secret.md").write_text(note("P-0905", ["PROJ-900"],
                                                   extra="send: never"), encoding="utf-8")
        # a reading note that claims facets must never count
        (pap / "_notas" / "P-0906.md").write_text(note("P-0906", ["PROJ-900"], [
            '{project: PROJ-900, facet: D, matched: "x"}']), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_flow_map(self):
        self.assertEqual(fa.parse_flow_map('{project: P, facet: A, matched: "a, b"}'),
                         {"project": "P", "facet": "A", "matched": "a, b"})
        self.assertEqual(fa.parse_flow_map("{facet: A, matched: null}")["matched"], None)
        self.assertIsNone(fa.parse_flow_map("not a map"))

    def test_assignment(self):
        res = fa.assignment(self.v, "PROJ-900")
        self.assertEqual(sorted(res["facets"]), ["A", "B", "C"])
        self.assertEqual([e["id"] for e in res["facets"]["A"]], ["P-0901"])
        self.assertEqual(res["facets"]["A"][0]["matched"], "toy term")
        self.assertEqual(res["facets"]["C"][0]["matched"], "other, with comma")
        self.assertEqual(res["facets"]["B"][0],
                         {"id": "P-0902", "path": "Papers/P-0902 Two.md", "matched": None,
                          "unrecovered": "not in the log"})
        self.assertEqual(res["sin_facetas"], ["P-0903"])
        self.assertEqual(res["send_never"], ["P-0905"])
        self.assertNotIn("_notas", json.dumps(res))
        self.assertNotIn("D", res["facets"])

    def test_cli_exit_codes(self):
        rc, out, _ = run("--vault", str(self.v), "--project", "PROJ-900")
        self.assertEqual(rc, 1)                               # P-0903 has none
        self.assertIn("P-0903", out)
        rc, out, _ = run("--vault", str(self.v), "--project", "PROJ-901", "--json")
        self.assertEqual(rc, 1)                               # P-0904 has none
        self.assertEqual(json.loads(out)["facets"]["A"][0]["matched"], "elsewhere")

    def test_chunks_read_every_paper_once_with_all_its_facets(self):
        res = {"facets": {
            "A": [{"id": f"P-09{i:02d}", "path": f"Papers/P-09{i:02d}.md", "matched": "a"} for i in range(10, 18)],
            "B": [{"id": "P-0910", "path": "Papers/P-0910.md", "matched": "b"},
                  {"id": "P-0911", "path": "Papers/P-0911.md", "matched": "b"},
                  {"id": "P-0920", "path": "Papers/P-0920.md", "matched": "b"}]}}
        cs = fa.chunks(res, size=3)
        ids = [p["id"] for c in cs for p in c["papers"]]
        self.assertEqual(sorted(ids), sorted(set(ids)))                     # each paper once
        self.assertEqual(set(ids), {f"P-09{i:02d}" for i in range(10, 18)} | {"P-0920"})
        self.assertTrue(all(len(c["papers"]) <= 3 for c in cs))
        shared = next(p for c in cs for p in c["papers"] if p["id"] == "P-0910")
        self.assertEqual(shared["facets"], ["A", "B"])                       # both facets go with it
        # the shared papers balance the load: B (3 papers) takes some, A keeps the rest
        homes = {p["id"]: c["facet"] for c in cs for p in c["papers"]}
        self.assertEqual(homes["P-0910"], "A")      # tie on load 0 → letter
        self.assertEqual(homes["P-0911"], "B")      # A already has one, B none
        rc, out, _ = run("--vault", str(self.v), "--project", "PROJ-900", "--chunks")
        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertEqual(data["papers"], 2)                                  # P-0901 once, P-0902
        self.assertEqual(sum(1 for c in data["chunks"] for p in c["papers"] if p["id"] == "P-0901"), 1)

    def test_add_entry(self):
        p = self.v / "Papers" / "P-0903 Three.md"
        self.assertEqual(run("--vault", str(self.v), "--add", "P-0903", "--project",
                             "PROJ-900", "--facet", "B", "--matched", 'say "hi"')[0], 0)
        self.assertEqual(run("--vault", str(self.v), "--add", "P-0903", "--project",
                             "PROJ-900", "--facet", "D", "--unrecovered", "why")[0], 0)
        # same project + facet again replaces, not duplicates
        self.assertEqual(run("--vault", str(self.v), "--add", "P-0903", "--project",
                             "PROJ-900", "--facet", "B", "--matched", "term")[0], 0)
        text = p.read_text(encoding="utf-8")
        self.assertEqual(text.count("facet: B"), 1)
        self.assertIn("added: 2030-01-01", text)               # rest of frontmatter kept
        self.assertTrue(text.endswith("## Resumen\n\nx\n"))
        res = fa.assignment(self.v, "PROJ-900")
        self.assertEqual(res["sin_facetas"], [])
        self.assertEqual(res["facets"]["B"][-1], {"id": "P-0903", "path": "Papers/P-0903 Three.md",
                                                   "matched": "term"})
        self.assertEqual(res["facets"]["D"][0]["unrecovered"], "why")
        # existing block: other projects' entries survive
        run("--vault", str(self.v), "--add", "P-0901", "--project", "PROJ-900",
            "--facet", "A", "--matched", "new")
        res1 = fa.assignment(self.v, "PROJ-901")
        self.assertEqual(res1["facets"]["A"][0]["matched"], "elsewhere")
        self.assertEqual(fa.assignment(self.v, "PROJ-900")["facets"]["A"][0]["matched"], "new")

    def test_add_refuses(self):
        for argv in (["--add", "P-0905", "--facet", "A", "--matched", "x"],   # send: never
                     ["--add", "P-0999", "--facet", "A", "--matched", "x"],   # no such note
                     ["--add", "P-0906", "--facet", "A", "--matched", "x"],   # only in _notas
                     ["--add", "P-0901", "--facet", "A"]):                    # no term
            rc, _, _ = run("--vault", str(self.v), "--project", "PROJ-900", *argv)
            self.assertEqual(rc, 2, argv)
        self.assertNotIn("facets", (self.v / "Papers" / "P-0905 Secret.md").read_text(
            encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
