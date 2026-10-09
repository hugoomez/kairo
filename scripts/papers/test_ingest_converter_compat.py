"""A converter change never turns a note an older converter wrote into an
integrity problem (invented ids and text, no network)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_ingest_paper as tip  # noqa: E402
import test_verbatim_pdf_headings as tph  # noqa: E402

ip = tip.ip
OLD = "kairo/verbatim_fulltext@1.5.0"


class OlderConverterNote(tip.Base):
    def setUp(self):
        super().setUp()
        d = Path(tempfile.mkdtemp(prefix="kairo-compat-"))
        self.addCleanup(tip.shutil.rmtree, d, True)
        self.txt = d / "paper.txt"
        self.txt.write_text(tph.HPC, encoding="utf-8")
        code, out = self.add("--arxiv", "0000.11111", "--pdf-text", str(self.txt),
                             "--source-url", "https://example.invalid/paper.pdf")
        self.assertEqual(code, 0, out)
        # what converter 1.5.0 wrote for these bytes: its own headings ("### 64 GPUs")
        self.manifest = self.vault / "Papers/_fuentes/P-0001/fuentes.json"
        man = json.loads(self.manifest.read_text(encoding="utf-8"))
        man["converter"] = OLD
        self.manifest.write_text(json.dumps(man), encoding="utf-8")
        f = next(x for x in man["files"] if x["role"] == "fulltext")
        old_block = ip.convert(f, self.txt.read_bytes(), "", ip.vf.rules_of(OLD)).replace(ip.vf.TOOL_ID, OLD)
        self.assertIn("### 64 GPUs", old_block)
        p = self.note()
        p.write_text(ip.replace_section(p.read_text(encoding="utf-8"), "Texto completo", old_block),
                     encoding="utf-8", newline="\n")

    def verify(self):
        return self.run_cli("verify", "--vault", str(self.vault))

    def test_it_still_verifies_after_the_converter_changed(self):
        code, out = self.verify()
        self.assertEqual((code, out["counts"]), (0, {"ok": 1}), out)

    def test_an_edit_to_it_is_still_caught(self):
        p = self.note()
        p.write_text(p.read_text(encoding="utf-8").replace("toy evaluation", "toy evaluations"), encoding="utf-8")
        code, out = self.verify()
        self.assertEqual(code, 3, out)

    def test_reconvert_upgrades_it_to_the_current_rules(self):
        code, out = self.run_cli("reconvert", "--vault", str(self.vault), "--only", "P-0001")
        self.assertEqual(code, 0, out)
        text = self.note().read_text(encoding="utf-8")
        self.assertNotIn("### 64 GPUs", text)
        self.assertIn("### Material suplementario", text)
        self.assertEqual(self.verify()[1]["counts"], {"ok": 1})

    def test_rules_never_newer_than_this_converter(self):
        self.assertEqual(ip.vf.rules_of("kairo/verbatim_fulltext@1.4.2"), (1, 4, 2))
        self.assertEqual(ip.vf.rules_of("kairo/verbatim_fulltext@99.0.0"), ip.vf.VERSION)
        self.assertEqual(ip.vf.rules_of(None), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
