"""isolation: the main thread does not print third-party abstracts into its own
context through lit_search.py show --with-abstracts (a screener reads its page
packet instead); subagents and the plain listing are unaffected."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import isolation  # noqa: E402

SHOW = 'python "/plugin/scripts/search/lit_search.py" show --run /v/Projects/x/_busquedas/2030-01-01'


def event(cmd: str, agent: str | None = None) -> dict:
    e = {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": "/v"}
    if agent:
        e["agent_id"] = agent
    return e


class AbstractsStayOutOfTheMainThread(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("KAIRO_ALLOW_MAIN_PAPER_READ", None)

    def tearDown(self):
        self.env.stop()

    def test_with_abstracts_is_refused_in_the_main_thread(self):
        self.assertIn("abstract", isolation.main_paper_read(event(SHOW + " --with-abstracts")) or "")

    def test_the_plain_listing_is_allowed(self):
        self.assertIsNone(isolation.main_paper_read(event(SHOW + " --limit 40")))

    def test_a_subagent_is_not_refused(self):
        self.assertIsNone(isolation.main_paper_read(event(SHOW + " --with-abstracts", agent="a1")))


if __name__ == "__main__":
    unittest.main()
