import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from find_code_repo import (
    Candidate,
    abs_page_fields,
    anchored_repo_urls,
    classified_repo_urls,
    code_context_urls,
    decide,
    normalize_repo_url,
    repo_urls_in,
    set_code_repo,
    split_frontmatter,
    fm_get,
)


class TestNormalize(unittest.TestCase):
    def test_strips_tree_path_git_suffix_and_www(self):
        self.assertEqual(
            normalize_repo_url("https://www.github.com/openai/grok.git"),
            "https://github.com/openai/grok",
        )
        self.assertEqual(
            normalize_repo_url("https://github.com/owner/repo/tree/main/src)."),
            "https://github.com/owner/repo",
        )

    def test_rejects_non_repositories(self):
        self.assertIsNone(normalize_repo_url("https://github.com/openai"))       # profile
        self.assertIsNone(normalize_repo_url("https://neelnanda.io/grokking-paper"))
        self.assertIsNone(normalize_repo_url("https://github.com/orgs/foo"))


class TestExtraction(unittest.TestCase):
    def test_latex_url_and_escaped_underscore(self):
        tex = r"Code: \url{https://github.com/a/my\_repo}. See \href{https://x.org}{site}."
        self.assertEqual(repo_urls_in(tex), ["https://github.com/a/my_repo"])

    def test_arxiv_comment_style(self):
        c = "Correspondence to alethea@openai.com. Code available at: https://github.com/openai/grok"
        self.assertEqual(repo_urls_in(c), ["https://github.com/openai/grok"])

    def test_code_context_url_is_a_one_hop_candidate(self):
        tex = "the code to reproduce our results, are available at https://neelnanda.io/grokking-paper."
        self.assertEqual(code_context_urls(tex), ["https://neelnanda.io/grokking-paper"])

    def test_url_without_code_context_is_not_followed(self):
        tex = "We thank the reviewers. " + "x " * 200 + "https://example.org/about"
        self.assertEqual(code_context_urls(tex), [])


class TestOwnershipVsDependency(unittest.TestCase):
    """Sentences taken from the live backfill run over this vault's papers."""

    def conf(self, tex, neutral="media"):
        return {u: c for u, c, _ in classified_repo_urls(tex, neutral)}

    def test_author_stated_code_is_alta(self):
        tex = (r"\section*{Code} Code to reproduce the experiments is available at "
               r"\url{https://github.com/MLI-lab/early_stopping_double_descent}.  \section*{Ack}")
        self.assertEqual(self.conf(tex), {"https://github.com/MLI-lab/early_stopping_double_descent": "alta"})

    def test_adapted_from_is_a_dependency(self):
        tex = (r"We define a family of ResNet18s. Our implementation is adapted from "
               r"\url{https://github.com/kuangliu/pytorch-cifar}.  \paragraph{CNNs.}")
        self.assertEqual(self.conf(tex), {"https://github.com/kuangliu/pytorch-cifar": "baja"})

    def test_we_use_x_from_repo_is_a_dependency(self):
        tex = (r"\footnote{We use the VGG11 architecture without batch normalization~\cite{x} from "
               r"\url{https://github.com/kuangliu/pytorch-cifar} in this experiment.}")
        self.assertEqual(self.conf(tex), {"https://github.com/kuangliu/pytorch-cifar": "baja"})

    def test_same_repo_keeps_its_strongest_mention(self):
        tex = (r"\footnote{The raw data from our experiments are available at: "
               r"\url{https://gitlab.com/hml/double-descent/tree/master}}. We consider. "
               r"The specification of the nets is at \url{https://gitlab.com/hml/double-descent}. Next.")
        self.assertEqual(self.conf(tex), {"https://gitlab.com/hml/double-descent": "alta"})

    def test_bare_url_uses_neutral_level(self):
        self.assertEqual(self.conf("see https://github.com/a/b", neutral="alta"), {"https://github.com/a/b": "alta"})
        self.assertEqual(self.conf("see https://github.com/a/b"), {"https://github.com/a/b": "media"})


class TestOneHopAnchors(unittest.TestCase):
    def test_only_anchors_near_code_words(self):
        html = (
            '<script src="https://github.com/mapbox/mapbox-gl-js/x.js"></script>'
            '<p>We also provide the code used to train our models '
            '<a href="https://github.com/mech-grok/progress-measures-paper">here</a>.</p>'
            + " filler" * 100 +
            '<footer><a href="https://github.com/squarespace/theme">theme</a></footer>'
        )
        self.assertEqual(anchored_repo_urls(html), ["https://github.com/mech-grok/progress-measures-paper"])


class TestAbsPageFallback(unittest.TestCase):
    def test_comment_anchor_is_replaced_by_its_href(self):
        html = ('<blockquote class="abstract mathjax"><span class="descriptor">Abstract:</span>We study X.</blockquote>'
                '<td class="tablecell comments mathjax">Correspondence to a@b.com. Code available at: '
                '<a href="https://github.com/openai/grok" rel="external">this https URL</a></td>')
        comment, abstract = abs_page_fields(html)
        self.assertEqual(comment, "Correspondence to a@b.com. Code available at: https://github.com/openai/grok")
        self.assertIn("We study X.", abstract)
        self.assertEqual([u for u, c, _ in classified_repo_urls(comment, neutral="alta")],
                         ["https://github.com/openai/grok"])


class TestDecide(unittest.TestCase):
    def test_nothing(self):
        self.assertEqual(decide([])[0], "none")

    def test_single_author_stated_is_proposed(self):
        s, url, conf, _ = decide([Candidate("https://github.com/a/b", "alta", "arXiv comments")])
        self.assertEqual((s, url, conf), ("proposed", "https://github.com/a/b", "alta"))

    def test_auto_match_alone_is_weak_never_proposed(self):
        s, url, _, _ = decide([Candidate("https://github.com/a/b", "baja", "HF auto")])
        self.assertEqual((s, url), ("weak", None))

    def test_auto_match_corroborates_but_does_not_outrank(self):
        s, url, conf, ev = decide([
            Candidate("https://github.com/a/b", "media", "one hop"),
            Candidate("https://github.com/a/b", "baja", "HF auto"),
        ])
        self.assertEqual((s, url, conf), ("proposed", "https://github.com/a/b", "media"))
        self.assertIn("HF auto", ev)

    def test_two_distinct_repos_at_top_confidence_is_conflict(self):
        s, url, _, _ = decide([
            Candidate("https://github.com/a/b", "alta", "tex"),
            Candidate("https://github.com/c/d", "alta", "tex"),
        ])
        self.assertEqual((s, url), ("conflict", None))

    def test_weaker_different_repo_does_not_block_a_stronger_one(self):
        s, url, _, _ = decide([
            Candidate("https://github.com/a/b", "alta", "tex"),
            Candidate("https://github.com/fork/b", "baja", "HF auto"),
        ])
        self.assertEqual((s, url), ("proposed", "https://github.com/a/b"))


NOTE = """---
id: P-0002
title: "X"
arxiv: 2301.05217
pdf: https://arxiv.org/pdf/2301.05217
projects: [PROJ-001]
---

## Referencia
"""


class TestFrontmatter(unittest.TestCase):
    def test_insert_after_pdf(self):
        out = set_code_repo(NOTE, "https://github.com/a/b", "paper footnote 1")
        fm, body = split_frontmatter(out)
        self.assertEqual(fm_get(fm, "code_repo"), "https://github.com/a/b")
        self.assertEqual(fm.index("code_repo: https://github.com/a/b"), fm.index("pdf: https://arxiv.org/pdf/2301.05217") + 1)
        self.assertIn("## Referencia", body)

    def test_replace_is_idempotent(self):
        once = set_code_repo(NOTE, "https://github.com/a/b", "e1")
        twice = set_code_repo(once, "https://github.com/a/c", "e2")
        fm, _ = split_frontmatter(twice)
        self.assertEqual(sum(1 for ln in fm if ln.startswith("code_repo:")), 1)
        self.assertEqual(fm_get(fm, "code_repo"), "https://github.com/a/c")

    def test_empty_field_reads_as_none(self):
        fm, _ = split_frontmatter(NOTE.replace("projects:", "code_repo:\nprojects:"))
        self.assertIsNone(fm_get(fm, "code_repo"))


if __name__ == "__main__":
    unittest.main()
