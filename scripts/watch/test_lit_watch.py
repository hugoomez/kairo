"""Tests for lit_watch.py. Run: python -m pytest scripts/watch

No network: a fake fetch answers arXiv (Atom) and Semantic Scholar (JSON).
Invented project, papers, ids and text only."""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
import urllib.parse
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lit_watch  # noqa: E402

TODAY = date(2031, 3, 1)

HUB = "---\nid: PROJ-960\nname: Vigilancia inventada\nlast_watch: 2031-02-01\n---\n\n# Hub\n"

SOTA = """---
title: Estado del arte
---

## Mapa

### Búsqueda ejecutada — 2031-01-15

**Facetas:** A = widgets ficticios; B = giro sintético

**Consultas (verbatim):**
| faceta | fuente | query | hits crudos |
|--------|--------|-------|-------------|
| A | arXiv | `abs:"fictional widgets"` | 12 |
| B | Semantic Scholar | `synthetic spin` | 9 |
| A | vault | `widgets` | 3 |

**Conteos** (enteros exactos, nunca aproximados):
- Identificados: 24
"""


def hyp(hid: str, status: str, claim: str, extra: str = "") -> str:
    return f"---\nid: {hid}\nproject: PROJ-960\nstatus: {status}\n{extra}---\n\n## Claim\n\n{claim}\n"


ABSTRACT = ("We show that fictional blue widgets rotate faster than red widgets under synthetic spin "
            "conditions, across every simulated configuration we tried.")


def atom(entries: list[tuple[str, str, str]]) -> bytes:
    body = "".join(
        f"<entry><id>http://arxiv.org/abs/{i}v1</id><title>{t}</title><summary>{s}</summary>"
        f"<published>2031-02-10T00:00:00Z</published><author><name>Autora Ficticia</name></author></entry>"
        for i, t, s in entries)
    return f'<feed xmlns="http://www.w3.org/2005/Atom">{body}</feed>'.encode()


class FakeNet:
    def __init__(self, fail: set[str] | None = None):
        self.urls: list[str] = []
        self.fail = fail or set()

    def __call__(self, url: str, headers: dict) -> bytes:
        self.urls.append(url)
        host = urllib.parse.urlparse(url).netloc
        if host in self.fail:
            raise lit_watch.net.HttpError(url, 503, "down")
        if "arxiv" in host:
            return atom([
                ("2031.00001", "An already ingested widget paper", "Old news about widgets and spin."),
                ("2031.00002", "Blue widgets spin faster", ABSTRACT),
                ("2031.00003", "Unrelated fictional topic", "Nothing about the hypothesis here at all."),
            ])
        return json.dumps({"data": [
            {"paperId": "s2a", "title": "Blue widgets spin faster", "abstract": ABSTRACT,
             "externalIds": {"ArXiv": "2031.00002"}, "publicationDate": "2031-02-10", "authors": [{"name": "A"}]},
            {"paperId": "s2b", "title": "A journal paper on synthetic spin", "abstract": "Spin, synthetically.",
             "externalIds": {"DOI": "10.9999/Fake.1"}, "publicationDate": "2031-02-20", "authors": []},
            {"paperId": "s2c", "title": "Too old", "abstract": "x", "externalIds": {},
             "publicationDate": "2030-01-01", "authors": []},
        ]}).encode()


class TestLitWatch(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-watch-"))
        self.vault = self.tmp / "vault"
        self.p = self.vault / "Projects" / "vigilancia-demo"
        (self.p / "Hipotesis").mkdir(parents=True)
        (self.vault / "Papers").mkdir()
        (self.p / "_hub.md").write_text(HUB, encoding="utf-8")
        (self.p / "Estado-del-arte.md").write_text(SOTA, encoding="utf-8")
        (self.vault / "Papers" / "P-0961 viejo.md").write_text(
            "---\nid: P-0961\ntitle: An already ingested widget paper\narxiv: 2031.00001\nprojects: [PROJ-960]\n---\n",
            encoding="utf-8")
        (self.p / "Hipotesis" / "H-0961.md").write_text(
            hyp("H-0961", "apoyada", "Fictional blue widgets rotate faster than red widgets under synthetic spin."),
            encoding="utf-8")
        (self.p / "Hipotesis" / "H-0962.md").write_text(
            hyp("H-0962", "refutada", "Fictional blue widgets rotate faster than red widgets under synthetic spin."),
            encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args, fetch=None):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = lit_watch.main(list(args), fetch=fetch or FakeNet(), today=TODAY)
        return code, json.loads(buf.getvalue())

    def delta(self, fetch=None):
        return self.cli("delta", "--vault", str(self.vault), "--project-dir", str(self.p), fetch=fetch)

    def run_file(self, res) -> Path:
        return self.vault / res["run"]

    def test_recorded_queries_only_watchable_sources(self):
        qs = lit_watch.recorded_queries(self.p)
        self.assertEqual([(q["facet"], q["source"], q["query"]) for q in qs],
                         [("A", "arxiv", 'abs:"fictional widgets"'), ("B", "s2", "synthetic spin")])

    def test_delta_since_last_watch_dedups_and_ranks(self):
        net = FakeNet()
        code, res = self.delta(net)
        self.assertEqual(code, 0, res)
        arxiv_url = urllib.parse.unquote(next(u for u in net.urls if "arxiv" in u))
        self.assertIn("submittedDate:[203102010000 TO 203103012359]", arxiv_url)
        self.assertIn("publicationDateOrYear=2031-02-01:", next(u for u in net.urls if "semanticscholar" in u))
        run = json.loads(self.run_file(res).read_text(encoding="utf-8"))
        keys = [c["key"] for c in run["candidates"]]
        self.assertNotIn("arxiv:2031.00001", keys)          # already in Papers/
        self.assertNotIn("s2:s2c", keys)                    # before the window
        self.assertEqual(keys[0], "arxiv:2031.00002")        # found by both facets
        top = run["candidates"][0]
        self.assertEqual((top["strong"], top["triage"], sorted(top["facets"])), (True, True, ["A", "B"]))
        self.assertIn("doi:10.9999/fake.1", keys)
        self.assertEqual([n["hypothesis"] for n in top["novelty"]], ["H-0961"])  # refuted one skipped
        self.assertIn("last_watch: 2031-03-01", (self.p / "_hub.md").read_text(encoding="utf-8"))

    def test_a_second_watch_does_not_offer_the_same_papers(self):
        self.delta()
        _, res = self.delta()
        run = json.loads(self.run_file(res).read_text(encoding="utf-8"))
        self.assertEqual(run["candidates"], [])

    def test_partial_and_total_outages(self):
        code, res = self.delta(FakeNet(fail={"api.semanticscholar.org"}))
        self.assertEqual((code, res["lost"], res["lost_all"]), (0, 1, False))
        # a window with lost queries was not covered: the run is saved, last_watch stays
        self.assertFalse(res["last_watch_moved"])
        self.assertIsNotNone(res["run"])
        self.assertIn("last_watch: 2031-02-01", (self.p / "_hub.md").read_text(encoding="utf-8"))
        (self.p / "_hub.md").write_text(HUB, encoding="utf-8")
        for f in (self.p / "_vigilancia").glob("*.json"):
            f.unlink()
        code, res = self.delta(FakeNet(fail={"api.semanticscholar.org", "export.arxiv.org"}))
        self.assertEqual((code, res["lost_all"], res["run"]), (0, True, None))
        self.assertIn("last_watch: 2031-02-01", (self.p / "_hub.md").read_text(encoding="utf-8"))

    def test_no_recorded_queries_is_refused(self):
        (self.p / "Estado-del-arte.md").unlink()
        code, res = self.delta()
        self.assertEqual(code, 2)
        self.assertIn("no recorded queries", res["error"])

    def test_threat_needs_a_verbatim_sentence_and_never_touches_status(self):
        _, res = self.delta()
        run = str(self.run_file(res))
        base = ["threat", "--vault", str(self.vault), "--project-dir", str(self.p), "--run", run,
                "--key", "arxiv:2031.00002", "--hypothesis", "H-0961", "--judgement", "Mismo resultado, ya publicado.",
                "--severity", "crítico"]
        code, out = self.cli(*base, "--sentence", "fictional blue widgets are known to rotate faster than red")
        self.assertEqual(code, 2)
        self.assertIn("not verbatim", out["error"])
        before = (self.p / "Hipotesis" / "H-0961.md").read_text(encoding="utf-8")
        code, out = self.cli(*base, "--sentence", "fictional blue widgets rotate faster than red widgets", "--model", "m-1")
        self.assertEqual((code, out["written"]), (0, True))
        after = (self.p / "Hipotesis" / "H-0961.md").read_text(encoding="utf-8")
        self.assertIn("## Revisión de vigencia", after)
        self.assertIn("juicio de un modelo, m-1; no es evidencia", after)
        self.assertIn("gravedad crítico", after)
        self.assertIn("«fictional blue widgets rotate faster than red widgets»", after)
        self.assertEqual(before.split("## Claim")[0], after.split("## Claim")[0])  # frontmatter untouched
        code, out = self.cli(*base, "--sentence", "fictional blue widgets rotate faster than red widgets")
        self.assertFalse(out["written"])  # idempotent
        self.assertEqual(after, (self.p / "Hipotesis" / "H-0961.md").read_text(encoding="utf-8"))

        code, out = self.cli("threat-decide", "--vault", str(self.vault), "--project-dir", str(self.p), "--run", run,
                             "--key", "arxiv:2031.00002", "--hypothesis", "H-0961", "--decision", "no_afecta",
                             "--reason", "Otro régimen inventado.", "--by", "Investigadora")
        self.assertEqual(code, 0, out)
        final = (self.p / "Hipotesis" / "H-0961.md").read_text(encoding="utf-8")
        self.assertIn("decisión de Investigadora sobre arxiv:2031.00002: no afecta", final)
        self.assertIn("status: apoyada", final)
        data = json.loads(Path(run).read_text(encoding="utf-8"))
        self.assertEqual(data["threats"][0]["decision"]["decision"], "no_afecta")

    def test_triage_and_decide(self):
        _, res = self.delta()
        run = str(self.run_file(res))
        common = ["--project-dir", str(self.p), "--run", run, "--key", "arxiv:2031.00002"]
        self.assertEqual(self.cli("triage", *common, "--why", "Mide lo mismo que H-0961.")[0], 0)
        self.assertEqual(self.cli("decide", *common, "--decision", "ingerir", "--by", "Investigadora")[0], 0)
        c = json.loads(Path(run).read_text(encoding="utf-8"))["candidates"][0]
        self.assertEqual((c["why"], c["decision"]["decision"]), ("Mide lo mismo que H-0961.", "ingerir"))
        code, out = self.cli("triage", "--project-dir", str(self.p), "--run", str(self.tmp / "elsewhere.json"),
                             "--key", "x", "--why", "y")
        self.assertEqual(code, 2)


    # --- severity, verbatim abstract, active hypotheses, completeness ------------

    def threat(self, run: str, sentence: str, severity: str = "importante", key: str = "arxiv:2031.00002"):
        return self.cli("threat", "--vault", str(self.vault), "--project-dir", str(self.p), "--run", run,
                        "--key", key, "--hypothesis", "H-0961", "--judgement", "Juicio inventado.",
                        "--sentence", sentence, "--severity", severity)

    def test_threat_severity_is_required_and_recorded(self):
        _, res = self.delta()
        run = str(self.run_file(res))
        code, out = self.threat(run, "fictional blue widgets rotate faster than red widgets", "gravísimo")
        self.assertEqual(code, 2)
        self.assertIn("severity", out["error"])
        code, out = self.threat(run, "fictional blue widgets rotate faster than red widgets", "critico")
        self.assertEqual((code, out["severity"]), (0, "crítico"))
        data = json.loads(Path(run).read_text(encoding="utf-8"))
        self.assertEqual(data["threats"][0]["severity"], "crítico")

    def test_threat_quotes_the_abstract_not_the_title_and_not_an_edited_abstract(self):
        _, res = self.delta()
        run = Path(self.run_file(res))
        data = json.loads(run.read_text(encoding="utf-8"))
        data["candidates"][0]["title"] = "Fictional blue widgets rotate faster in every invented case"
        run.write_text(json.dumps(data), encoding="utf-8")
        code, out = self.threat(str(run), "Fictional blue widgets rotate faster in every invented case")
        self.assertEqual(code, 2)
        self.assertIn("not verbatim in the candidate's abstract", out["error"])
        data["candidates"][0]["abstract"] += " A sentence the model added to make its paraphrase pass."
        run.write_text(json.dumps(data), encoding="utf-8")
        code, out = self.threat(str(run), "A sentence the model added to make its paraphrase pass.")
        self.assertEqual(code, 2)
        self.assertIn("edited", out["error"])

    def test_no_threat_on_a_discarded_or_refuted_hypothesis(self):
        _, res = self.delta()
        run = str(self.run_file(res))
        h = self.p / "Hipotesis" / "H-0961.md"
        before = h.read_text(encoding="utf-8")
        for status in ("descartada", "refutada"):
            h.write_text(before.replace("status: apoyada", f"status: {status}"), encoding="utf-8")
            code, out = self.threat(run, "fictional blue widgets rotate faster than red widgets")
            self.assertEqual(code, 2)
            self.assertIn(status, out["error"])
            self.assertNotIn("Revisión de vigencia", h.read_text(encoding="utf-8"))

    def test_no_abstract_no_threat(self):
        _, res = self.delta()
        run = Path(self.run_file(res))
        data = json.loads(run.read_text(encoding="utf-8"))
        c = data["candidates"][0]
        c["abstract"], c["abstract_sha256"] = "", None
        run.write_text(json.dumps(data), encoding="utf-8")
        code, out = self.threat(str(run), "fictional blue widgets rotate faster than red widgets")
        self.assertEqual(code, 2)
        self.assertIn("no abstract", out["error"])

    def test_discarded_hypotheses_are_not_watched(self):
        (self.p / "Hipotesis" / "H-0961.md").write_text(
            hyp("H-0961", "descartada", "Fictional blue widgets rotate faster than red widgets under synthetic spin."),
            encoding="utf-8")
        _, res = self.delta()
        run = json.loads(self.run_file(res).read_text(encoding="utf-8"))
        self.assertTrue(all(not c["novelty"] for c in run["candidates"]))

    def test_check_lists_what_a_run_lacks(self):
        _, res = self.delta()
        run = str(self.run_file(res))
        common = ["--project-dir", str(self.p), "--run", run]
        code, out = self.cli("check", *common)
        self.assertEqual(code, 3)
        self.assertFalse(out["complete"])
        self.assertTrue(any("por qué" in m for m in out["missing"]))
        for c in json.loads(Path(run).read_text(encoding="utf-8"))["candidates"]:
            if c["triage"]:
                self.cli("triage", *common, "--key", c["key"], "--why", "Línea inventada.")
        self.threat(run, "fictional blue widgets rotate faster than red widgets", "menor")
        code, out = self.cli("check", *common)
        self.assertEqual((code, out["complete"], out["threats"]), (0, True, 1), out)
        data = json.loads(Path(run).read_text(encoding="utf-8"))
        del data["threats"][0]["severity"]  # a run written before severities existed
        Path(run).write_text(json.dumps(data), encoding="utf-8")
        code, out = self.cli("check", *common)
        self.assertEqual(code, 3)
        self.assertTrue(any("gravedad" in m for m in out["missing"]))


if __name__ == "__main__":
    unittest.main()
