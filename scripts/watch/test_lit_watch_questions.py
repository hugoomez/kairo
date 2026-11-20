"""Novelty watch for study questions (lit_watch.py). Run: python -m pytest scripts/watch

A measurement study's novelty is a question («has anyone measured X in population
Y?»): the watch matches open questions too, builds a judge packet with only the
question and its population, and records a threat in the question's own
`## Revisión de vigencia`. No network; invented project, papers, ids and text."""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lit_watch  # noqa: E402
from test_lit_watch import ABSTRACT, HUB, SOTA, TODAY, FakeNet, hyp  # noqa: E402

sys.path.insert(0, str(HERE.parent / "security"))
import isolation  # noqa: E402

SENTENCE = "fictional blue widgets rotate faster than red widgets"


def question(qid: str, status: str, pregunta: str, poblacion: str, extra: str = "") -> str:
    return (f"---\nid: {qid}\nproject: PROJ-960\ndesign: medicion\nstatus: {status}\nhistory: []\n---\n\n"
            f"# {qid} — pregunta inventada\n\n## Pregunta\n\n{pregunta}\n\n## Población y marco\n\n{poblacion}\n\n"
            "## Estimandos\n\nPLAN-SECRETO proporción inventada.\n\n## Resultado\n\nRESULTADO-SECRETO 0.42.\n\n"
            f"## Historia\n\n{extra}")


Q_TEXT = ("¿Qué proporción de los widgets azules ficticios gira más deprisa que los rojos bajo un spin "
          "sintético?")
Q_POP = "Widgets ficticios de toda configuración simulada del catálogo inventado."


class TestQuestionNovelty(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairo-watch-q-"))
        self.env = mock.patch.dict(os.environ, {"KAIRO_STATE_DIR": str(self.tmp / "state")})
        self.env.start()
        os.environ.pop("KAIRO_PACKETS_DIR", None)
        self.vault = self.tmp / "vault"
        self.p = self.vault / "Projects" / "medicion-demo"
        for d in ("Hipotesis", "Preguntas", "Pilotos"):
            (self.p / d).mkdir(parents=True)
        (self.vault / "Papers").mkdir()
        (self.p / "_hub.md").write_text(HUB, encoding="utf-8")
        (self.p / "Estado-del-arte.md").write_text(SOTA, encoding="utf-8")
        (self.p / "Preguntas" / "Q-0961.md").write_text(question("Q-0961", "abierta", Q_TEXT, Q_POP), encoding="utf-8")
        (self.p / "Preguntas" / "Q-0962.md").write_text(question("Q-0962", "descartada", Q_TEXT, Q_POP),
                                                        encoding="utf-8")
        (self.p / "Pilotos" / "QP-0961.md").write_text(question("QP-0961", "abierta", Q_TEXT, Q_POP),
                                                       encoding="utf-8")

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = lit_watch.main(list(args), fetch=FakeNet(), today=TODAY)
        return code, json.loads(buf.getvalue())

    def run_file(self) -> str:
        code, res = self.cli("delta", "--vault", str(self.vault), "--project-dir", str(self.p))
        self.assertEqual(code, 0, res)
        self.res = res
        return str(self.vault / res["run"])

    def packet(self, run: str, qid: str = "Q-0961", read: bool = True) -> dict:
        code, out = self.cli("judge-packet", "--vault", str(self.vault), "--project-dir", str(self.p),
                             "--run", run, "--key", "arxiv:2031.00002", "--question", qid)
        self.assertEqual(code, 0, out)
        if read:
            isolation.record_receipt({"sha256": out["sha256"], "agent_type": "novelty-judge", "agent_id": "j-q"})
        return out

    def threat(self, run: str, sha: str | None, sentence: str = SENTENCE, qid: str = "Q-0961", *extra):
        args = ["threat", "--vault", str(self.vault), "--project-dir", str(self.p), "--run", run,
                "--key", "arxiv:2031.00002", "--question", qid, "--sentence", sentence,
                "--severity", "importante", "--judgement", "Mide la misma cantidad en una población inventada.",
                "--model", "m-q"]
        if sha:
            args += ["--packet-sha256", sha]
        return self.cli(*args, *extra)

    def test_open_questions_are_prefiltered_in_their_own_list(self):
        run = json.loads(Path(self.run_file()).read_text(encoding="utf-8"))
        top = next(c for c in run["candidates"] if c["key"] == "arxiv:2031.00002")
        ids = [n["question"] for n in top["novelty_preguntas"]]
        self.assertEqual(ids, ["Q-0961"])                 # not the discarded one, never the pilot
        self.assertEqual(top["novelty"], [])              # no hypothesis in this project
        self.assertEqual(self.res["novelty_question_candidates"],
                         sum(1 for c in run["candidates"] if c["novelty_preguntas"]))
        self.assertGreaterEqual(self.res["novelty_question_candidates"], 1)
        # only ids and scores: the question's text never reaches the run
        self.assertNotIn("proporción", json.dumps(run, ensure_ascii=False))

    def test_exploratory_questions_are_watched_when_the_folder_exists(self):
        (self.p / "Exploratorias").mkdir()
        (self.p / "Exploratorias" / "QE-0961.md").write_text(question("QE-0961", "abierta", Q_TEXT, Q_POP),
                                                             encoding="utf-8")
        run = json.loads(Path(self.run_file()).read_text(encoding="utf-8"))
        top = next(c for c in run["candidates"] if c["key"] == "arxiv:2031.00002")
        self.assertEqual(sorted(n["question"] for n in top["novelty_preguntas"]), ["Q-0961", "QE-0961"])

    def test_hypotheses_keep_their_own_list_unchanged(self):
        (self.p / "Hipotesis" / "H-0961.md").write_text(
            hyp("H-0961", "apoyada", "Fictional blue widgets rotate faster than red widgets under synthetic spin."),
            encoding="utf-8")
        run = json.loads(Path(self.run_file()).read_text(encoding="utf-8"))
        top = next(c for c in run["candidates"] if c["key"] == "arxiv:2031.00002")
        self.assertEqual([n["hypothesis"] for n in top["novelty"]], ["H-0961"])
        self.assertEqual([n["question"] for n in top["novelty_preguntas"]], ["Q-0961"])

    def test_the_packet_holds_the_question_and_population_only(self):
        run = self.run_file()
        out = self.packet(run)
        self.assertEqual((out["question"], out["kind"]), ("Q-0961", "pregunta"))
        text = Path(out["packet"]).read_text(encoding="utf-8")
        self.assertIn("**Tipo:** pregunta", text)
        self.assertIn(Q_TEXT, text)
        self.assertIn(Q_POP, text)
        self.assertIn(ABSTRACT, text)
        for absent in ("PLAN-SECRETO", "RESULTADO-SECRETO", "status:", "design:"):
            self.assertNotIn(absent, text)
        # a pilot, a malformed id, or both targets at once are refused
        for args in (["--question", "QP-0961"], ["--question", "Q-12"],
                     ["--question", "Q-0961", "--hypothesis", "H-0001"], []):
            code, res = self.cli("judge-packet", "--vault", str(self.vault), "--project-dir", str(self.p),
                                 "--run", run, "--key", "arxiv:2031.00002", *args)
            self.assertEqual(code, 2, (args, res))

    def test_a_threat_goes_into_the_question_and_never_changes_its_status(self):
        run = self.run_file()
        f = self.p / "Preguntas" / "Q-0961.md"
        before = f.read_text(encoding="utf-8")
        code, out = self.threat(run, None)                          # no proof the judge read it
        self.assertEqual(code, 2)
        self.assertIn("novelty-judge", out["error"])
        sha = self.packet(run)["sha256"]
        code, out = self.threat(run, sha, "fictional blue widgets are already measured everywhere")
        self.assertEqual(code, 2)
        self.assertIn("not verbatim", out["error"])
        code, out = self.threat(run, sha)
        self.assertEqual((code, out["question"], out["written"]), (0, "Q-0961", True), out)
        after = f.read_text(encoding="utf-8")
        self.assertEqual(before.split("## Pregunta")[0], after.split("## Pregunta")[0])   # frontmatter untouched
        rev = after.split("## Revisión de vigencia", 1)[1]
        self.assertIn(f"«{SENTENCE}»", rev)
        self.assertIn("quizá ya medido", rev)
        self.assertIn("juicio de un modelo, m-q; no es evidencia", rev)
        self.assertEqual(self.threat(run, sha)[1]["written"], False)                        # idempotent
        data = json.loads(Path(run).read_text(encoding="utf-8"))
        t = data["threats"][0]
        self.assertEqual((t["question"], t["kind"], t["packet_read"]), ("Q-0961", "pregunta", True))
        self.assertNotIn("hypothesis", t)
        code, out = self.cli("check", "--project-dir", str(self.p), "--run", run)
        self.assertEqual(code, 3)                                   # the strong candidate still lacks its line
        self.assertFalse(any("Q-0961" in m for m in out["missing"]), out["missing"])
        code, out = self.cli("digest", "--project-dir", str(self.p), "--run", run)
        page = Path(run).with_suffix(".md").read_text(encoding="utf-8")
        self.assertIn("Posible amenaza de novedad para Q-0961", page)
        code, out = self.cli("threat-decide", "--vault", str(self.vault), "--project-dir", str(self.p), "--run", run,
                             "--key", "arxiv:2031.00002", "--question", "Q-0961", "--decision", "revisar",
                             "--reason", "Otra población inventada, pero cercana.", "--by", "Investigadora")
        self.assertEqual(code, 0, out)
        final = f.read_text(encoding="utf-8")
        self.assertIn("decisión de Investigadora sobre arxiv:2031.00002: hay que revisar la pregunta", final)
        self.assertIn("status: abierta", final)

    def test_no_threat_on_a_closed_question(self):
        run = self.run_file()
        sha = self.packet(run)["sha256"]
        f = self.p / "Preguntas" / "Q-0961.md"
        for status in ("descartada", "sin_respuesta"):
            f.write_text(question("Q-0961", status, Q_TEXT, Q_POP), encoding="utf-8")
            code, out = self.threat(run, sha)
            self.assertEqual(code, 2, out)
            self.assertIn(status, out["error"])
            self.assertNotIn("Revisión de vigencia", f.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
