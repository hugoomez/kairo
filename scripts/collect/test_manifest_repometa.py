"""Tests for the manifest's population statement / manifest.md and kairo_collect.repometa.
No network (FakeGitHub of test_collect.py); invented repositories and files under tmp_path."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import collect as cli  # noqa: E402
from kairo_collect.repometa import demo_score, repo_flags  # noqa: E402
from test_collect import TERM, FakeClock, FakeGitHub, collection, make_files  # noqa: E402


def test_manifest_population_and_markdown(tmp_path):
    clock = FakeClock()
    extra = [{**f, "path": "lib/" + f["path"], "sha": f["sha"][::-1]} for f in make_files(30)]
    files = make_files(1500, sizes=lambda i: 4096) + extra
    out = tmp_path / "out"
    qs = [{"name": "base", "q": TERM}, {"name": "narrow", "q": f"{TERM} extension:py"}]
    with collection(out, FakeGitHub(clock, files), clock, collection_id="REC-9003") as col:
        col.search(qs)
    man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert man["snapshot_started_at"] and man["snapshot_ended_at"] >= man["snapshot_started_at"]
    for q in man["queries"]:
        ex = q["executed_at"]
        assert ex["started_at"] and ex["ended_at"] >= ex["started_at"]
    pop = man["population"]
    base = next(q for q in pop["queries"] if q["name"] == "base")
    assert base["total_count"] == 1530 and base["items_fetched"] == 1030
    assert base["coverage"] == round(1030 / 1530, 4)
    assert len(pop["truncated_shards"]) == 1
    t = pop["truncated_shards"][0]
    assert t["total_count"] == 1500 and t["reachable"] == 1000 and t["fetched"] == 1000
    assert all(s["coverage"] is None or s["coverage"] <= 1 for s in pop["shards"])
    assert pop["totals"]["truncated_shards"] == 1 and pop["totals"]["queries"] == 2
    md = (out / "manifest.md").read_text(encoding="utf-8")
    assert "REC-9003" in md and "Truncated shards" in md and TERM in md and "67.3%" in md
    for f in files[:50]:
        assert f["path"] not in md and f["repo"] not in md and f["sha"] not in md
    (out / "manifest.md").unlink()
    assert cli.main(["manifest", "--out", str(out)]) == 0
    assert (out / "manifest.md").read_text(encoding="utf-8") == md


def test_repo_flags_read_both_spellings():
    raw = {"full_name": "acme-lab/demo", "fork": True, "archived": False, "is_template": False,
           "owner": {"type": "User"}, "stargazers_count": 0, "created_at": "2030-01-01T00:00:00Z",
           "pushed_at": "2030-01-02T00:00:00Z", "size": 12}
    facts = {"fork": True, "archived": False, "is_template": False, "owner_type": "User", "stargazers_count": 0,
             "created_at": "2030-01-01T00:00:00Z", "pushed_at": "2030-01-02T00:00:00Z", "size_kb": 12}
    want = {"fork": True, "archived": False, "is_template": False, "owner_type": "User", "stars": 0,
            "created_at": "2030-01-01T00:00:00Z", "pushed_at": "2030-01-02T00:00:00Z", "size": 12}
    assert repo_flags(raw) == want == repo_flags(facts)


def test_demo_score_gives_reasons_never_a_decision():
    demo = {"name": "sops-demo-inventada", "description": "A tutorial example for the invented course",
            "topics": ["workshop"], "is_template": True, "stargazers_count": 0, "size_kb": 3,
            "created_at": "2030-01-01T00:00:00Z", "pushed_at": "2030-01-02T00:00:00Z"}
    s = demo_score(demo, readme=False)
    assert s["decision"] is None and s["score"] == 1.0
    assert set(s["signals"]) == {"name_keyword", "description_keyword", "topic_keyword", "is_template",
                                 "no_readme", "short_life", "tiny", "no_stars"}
    assert "invented course" not in json.dumps(s)
    real = {"name": "billing-service", "description": "Internal service of an invented company",
            "topics": ["payments"], "is_template": False, "stargazers_count": 40, "size_kb": 9000,
            "created_at": "2028-01-01T00:00:00Z", "pushed_at": "2030-01-02T00:00:00Z"}
    r = demo_score(real, readme=True)
    assert r["score"] == 0 and r["reasons"] == [] and r["decision"] is None
    assert demo_score(real, readme=None, weights={"no_stars": 0.9})["score"] == 0


def test_collection_demo_signals_and_flags(tmp_path):
    clock = FakeClock()
    fake = FakeGitHub(clock, make_files(5))
    with collection(tmp_path / "out", fake, clock) as col:
        flags = col.repo_flags("acme-lab/demo-1")
        assert flags["archived"] is True and flags["owner_type"] == "Organization" and flags["stars"] == 3
        sig = col.demo_signals("acme-lab/demo-1")
        assert "name_keyword" in sig["signals"] and "no_readme" in sig["signals"] and sig["decision"] is None
        assert any(u.endswith("/repos/acme-lab/demo-1/readme") for u in fake.urls("/repos/"))
        assert col.demo_signals("acme-lab/missing") is None
