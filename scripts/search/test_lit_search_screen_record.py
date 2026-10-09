"""lit_search: the plan stays frozen through `screen`, `merge --from-store`
takes each page's latest valid reply and names every unreadable block at once,
and the model a stored reply records is written next to the declared one
(invented works only)."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lit_search as ls  # noqa: E402
from test_lit_search import Base  # noqa: E402

sys.path.insert(0, str(HERE.parent / "security"))
import isolation  # noqa: E402


def store_reply(packet_sha: str, text: str, agent_id: str, model: str | None = None) -> None:
    """A reply as Kairo's SubagentStop hook keeps it (with the observed model when the hook records one)."""
    data = text.encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    isolation.replies_dir().mkdir(parents=True, exist_ok=True)
    (isolation.replies_dir() / f"{sha}.txt").write_bytes(data)
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "agent_type": "screener", "agent_id": agent_id,
           "packets": [packet_sha], "reply_sha256": sha, **({"model": model} if model is not None else {})}
    with open(isolation.state_dir() / isolation.REPLIES, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


class Screened(Base):
    def pages(self):
        self.run_search()
        self.cli("retraction", "--run", str(self.run_dir))
        out, off = [], 0
        while off is not None:
            _, shown = self.cli("show", "--run", str(self.run_dir), "--limit", "40", "--offset", str(off))
            isolation.record_receipt({"sha256": shown["packet"]["sha256"], "agent_type": "screener",
                                      "agent_id": f"s-{off}"})
            out.append(shown)
            off = shown["next_offset"]
        return out

    @staticmethod
    def reply(page, decision="exclude"):
        d = {c["key"]: ({"decision": "include", "relevance": "media", "why": "Reports a toy decoder on a toy code."}
                        if decision == "include" else
                        {"decision": "exclude", "reason": "relevancia baja", "why": "Invented, not relevant here."})
             for c in page["candidates"]}
        return "My decisions.\n```json\n" + json.dumps(d) + "\n```"

    def merge(self):
        return self.cli("merge", "--run", str(self.run_dir), "--from-store", "--out", str(self.tmp / "d.json"))


class FrozenPlan(Screened):
    def test_screen_never_rewrites_the_plan_and_refuses_a_changed_one(self):
        pages = self.pages()
        for p in pages:
            store_reply(p["packet"]["sha256"], self.reply(p), "a")
        self.assertEqual(self.merge()[0], 0)
        plan_bytes = (self.run_dir / "plan.json").read_bytes()
        self.assertEqual((self.run_dir / "plan.sha256").read_text(encoding="utf-8").split()[0],
                         hashlib.sha256(plan_bytes).hexdigest())
        code, res = self.cli("screen", "--run", str(self.run_dir), "--decisions", str(self.tmp / "d.json"))
        self.assertEqual(code, 0, res)
        self.assertEqual((self.run_dir / "plan.json").read_bytes(), plan_bytes)
        self.assertEqual(ls.load(self.run_dir / "screen.json")["screened_by"], res["screened_by"])
        plan = json.loads(plan_bytes)
        plan["include"] = ["an edited criterion"]
        (self.run_dir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        code, res = self.cli("screen", "--run", str(self.run_dir), "--decisions", str(self.tmp / "d.json"))
        self.assertEqual(code, 2)
        self.assertIn("plan.json", res["refused"])


class LatestReply(Screened):
    def test_merge_takes_the_latest_valid_reply_of_a_page(self):
        pages = self.pages()
        for p in pages:
            store_reply(p["packet"]["sha256"], self.reply(p, "exclude"), "a")
            store_reply(p["packet"]["sha256"], self.reply(p, "include"), "b")
            store_reply(p["packet"]["sha256"], "I could not finish.", "c")        # latest, not valid
        code, res = self.merge()
        self.assertEqual(code, 0, res)
        d = ls.load(self.tmp / "d.json")
        self.assertEqual({v["decision"] for v in d.values()}, {"include"})

    def test_every_unreadable_block_is_named_at_once(self):
        pages = self.pages()
        bad = []
        for i in range(len(pages)):
            f = self.tmp / f"bad{i}.txt"
            f.write_text("not json at all", encoding="utf-8")
            bad.append(str(f))
        extra = self.tmp / "bad-extra.txt"
        extra.write_text("{not json", encoding="utf-8")
        code, res = self.cli("merge", "--run", str(self.run_dir), "--blocks", *bad, str(extra),
                             "--out", str(self.tmp / "d.json"))
        self.assertEqual(code, 2)
        for f in bad + [str(extra)]:
            self.assertIn(Path(f).name, res["refused"])


class ObservedModel(Screened):
    def test_the_observed_model_is_recorded_next_to_the_declared_one(self):
        pages = self.pages()
        for p in pages:
            store_reply(p["packet"]["sha256"], self.reply(p), "a", model="claude-invented-7")
        self.assertEqual(self.merge()[0], 0)
        blocks = ls.load(self.run_dir / "screening.json")["blocks"]
        self.assertEqual({b["model"] for b in blocks}, {"claude-invented-7"})
        code, res = self.cli("screen", "--run", str(self.run_dir), "--decisions", str(self.tmp / "d.json"))
        self.assertEqual(code, 0, res)
        self.assertEqual(res["screened_by_observed"], ["claude-invented-7"])
        md = (self.run_dir / "busqueda.md").read_text(encoding="utf-8")
        self.assertIn("modelo observado en las respuestas guardadas: claude-invented-7", md)

    def test_no_model_in_the_store_still_works(self):
        pages = self.pages()
        for p in pages:
            store_reply(p["packet"]["sha256"], self.reply(p), "a")
        self.assertEqual(self.merge()[0], 0)
        code, res = self.cli("screen", "--run", str(self.run_dir), "--decisions", str(self.tmp / "d.json"))
        self.assertEqual(code, 0, res)
        self.assertIsNone(res["screened_by_observed"])
        self.assertIn("modelo observado en las respuestas guardadas: no consta",
                      (self.run_dir / "busqueda.md").read_text(encoding="utf-8"))
