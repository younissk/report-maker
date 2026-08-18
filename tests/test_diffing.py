"""Semantic diff tests.

The claim about this module worth testing is a single sentence: a reworded
citation reads as one changed claim. Everything a line diff already does well is
uninteresting here; what matters is that rewriting "A cited fact looks like
this @alpha." does not come back as a deletion plus an unrelated insertion, and
that a genuinely new claim is not quietly paired with a deleted one just because
both happen to be prose.

So these tests build a real git repository — init, commit, edit, commit — rather
than stubbing `git show`. The subprocess boundary is where the interesting
failures live (a vault nested inside a larger repository, a revision that does
not exist, a report younger than the revision), and a fake would assert only
that the fake behaves.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import diffing  # noqa: E402
from engine.config import Config, load  # noqa: E402

# Committing needs an identity, and a machine running the suite may not have one
# configured. Supplying it here keeps the tests independent of ~/.gitconfig.
GIT_ENV = {
    "GIT_AUTHOR_NAME": "report-maker tests",
    "GIT_AUTHOR_EMAIL": "tests@example.invalid",
    "GIT_COMMITTER_NAME": "report-maker tests",
    "GIT_COMMITTER_EMAIL": "tests@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}

MAIN_V1 = """#import "/.build/design/base/report.typ": report
#import "/.build/design/base/components.typ": *

#show: report.with(
  title: "Example report",
  version: "0.1 — Draft",
  date: datetime(year: 2026, month: 8, day: 16),
  sources: "/reports/r/sources.yml",
)

= Findings

A cited fact looks like this @alpha.

Pricing starts at ten dollars a month for the smallest plan @beta.

#assessment[
  Our reading is that the pricing page has not been maintained.
]

#srcfig(
  table([a]),
  caption: [A table that will not survive.],
  source: [@alpha],
)
"""

MAIN_V2 = """#import "/.build/design/base/report.typ": report
#import "/.build/design/base/components.typ": *

#show: report.with(
  title: "Example report",
  version: "0.2 — Final",
  date: datetime(year: 2026, month: 8, day: 16),
  sources: "/reports/r/sources.yml",
)

= Findings

A cited fact now looks a little different, like this @alpha.

The vendor published a security advisory in March @gamma.

#assessment[
  We think the whole billing section needs rewriting before it is shown.
]

#srcfig(
  table([b]),
  caption: [A different table entirely.],
  source: [@alpha],
)
"""

BIB_V1 = """alpha:
  type: Web
  title: "Alpha"
  url:
    value: https://example.com/alpha
    date: 2026-01-01

beta:
  type: Web
  title: "Beta"
"""

BIB_V2 = """alpha:
  type: Web
  title: "Alpha, revised"
  url:
    value: https://example.com/alpha
    date: 2026-01-01

gamma:
  type: Report
  title: "Gamma"
"""


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        env={**os.environ, **GIT_ENV},
        capture_output=True,
        text=True,
        check=True,
    )


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class Diffing(unittest.TestCase):
    """A vault two commits deep, nested one folder inside its repository.

    The nesting is deliberate: `git show <rev>:<path>` takes a path from the
    repository root, and a vault is far more often a folder inside a larger
    repository than the whole of it. A test that put the vault at the root would
    pass with the prefix handling removed entirely.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name).resolve()
        self.root = self.repo / "vault"
        (self.root / "reports" / "r").mkdir(parents=True)
        (self.root / "report-maker.toml").write_text("[vault]\n", encoding="utf-8")

        git(self.repo, "init", "-q")
        self.write(MAIN_V1, BIB_V1)
        self.commit("first")
        self.write(MAIN_V2, BIB_V2)
        self.commit("second")

        self.cfg: Config = load(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # ── fixtures

    def write(self, main: str, bibliography: str, rid: str = "r") -> None:
        folder = self.root / "reports" / rid
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "main.typ").write_text(main, encoding="utf-8")
        (folder / "sources.yml").write_text(bibliography, encoding="utf-8")

    def commit(self, message: str) -> None:
        git(self.repo, "add", "-A")
        # --allow-empty so a test may commit an unchanged tree deliberately.
        git(self.repo, "commit", "-q", "--allow-empty", "-m", message)

    def changes(self, kind: str, diffs=None) -> list[diffing.Change]:
        diffs = diffs if diffs is not None else diffing.diff(self.cfg, "r")
        return [c for d in diffs for c in d.changes if c.kind == kind]

    def only(self, kind: str, diffs=None) -> diffing.Change:
        found = self.changes(kind, diffs)
        self.assertEqual(len(found), 1, f"expected exactly one {kind}, got {found}")
        return found[0]

    # ── claims

    def test_a_reworded_citation_is_one_changed_claim(self) -> None:
        # The reason the module exists. Below the match threshold this same edit
        # would arrive as claim-removed plus claim-added, and a reader would be
        # told a fact had been withdrawn when it had only been rephrased.
        change = self.only("claim-changed")
        self.assertEqual(change.key, "alpha")
        self.assertEqual(change.before, "A cited fact looks like this @alpha.")
        self.assertEqual(change.after, "A cited fact now looks a little different, like this @alpha.")
        self.assertEqual(change.line, 13)

    def test_an_unrelated_claim_is_a_removal_and_an_addition(self) -> None:
        # Two claims that share nothing but their grammar must not be paired,
        # or a withdrawn fact would be reported as an edit to a new one.
        gone = self.only("claim-removed")
        self.assertEqual(gone.key, "beta")
        self.assertIn("ten dollars", gone.before)
        self.assertIsNone(gone.after)
        self.assertIsNone(gone.line, "a removal has no line in the file as it stands")

        added = self.only("claim-added")
        self.assertEqual(added.key, "gamma")
        self.assertEqual(added.after, "The vendor published a security advisory in March @gamma.")
        self.assertIsNone(added.before)
        self.assertEqual(added.line, 15)

    def test_an_untouched_report_has_no_changes(self) -> None:
        self.write(MAIN_V2, BIB_V2)
        self.commit("third, identical")
        diffs = diffing.diff(self.cfg, "r", rev="HEAD~1")
        self.assertEqual(diffs[0].changes, [])
        self.assertEqual(diffs[0].counts["claims"], {"added": 0, "removed": 0, "changed": 0})

    def test_reflowing_a_paragraph_changes_nothing(self) -> None:
        # The failure a line diff cannot avoid: rewrapping churns every line and
        # says nothing. A claim spans lines, so its text must survive the wrap.
        self.write(MAIN_V2.replace("like this @alpha.", "like\nthis @alpha."), BIB_V2)
        self.commit("reflow")
        self.assertEqual(self.changes("claim-changed", diffing.diff(self.cfg, "r", "HEAD~1")), [])

    def test_a_claim_inside_a_helper_reads_as_a_sentence(self) -> None:
        # `#finding(…)` puts prose in named arguments next to severities and
        # ids. A changelog that quotes `"F-01", severity: "high"` back at a
        # client is not a changelog.
        self.write(
            MAIN_V2
            + '\n#finding(\n  id: "F-01",\n  severity: "high",\n'
            "  evidence: [The login page served no HSTS header @alpha.],\n"
            "  impact: [That is exploitable on a shared network.#assess],\n)\n",
            BIB_V2,
        )
        self.commit("a finding")
        diffs = diffing.diff(self.cfg, "r", "HEAD~1")
        self.assertEqual(
            [c.after for c in self.changes("claim-added", diffs)],
            ["The login page served no HSTS header @alpha."],
        )
        self.assertEqual(
            [c.after for c in self.changes("assessment-added", diffs)],
            ["That is exploitable on a shared network.#assess"],
        )

    def test_a_quotation_keeps_the_citation_from_its_source_argument(self) -> None:
        # `claim(…)` carries its citation in `source:`, which the prose scan
        # blanks. Lose that and the most claim-like thing in a report stops
        # counting as a claim at all.
        self.write(
            MAIN_V2 + '\n#claim(\n  [We do not store card numbers.],\n'
            '  attribution: "example.com/security",\n  source: [@alpha],\n)\n',
            BIB_V2,
        )
        self.commit("a quotation")
        change = self.only("claim-added", diffing.diff(self.cfg, "r", "HEAD~1"))
        self.assertEqual(change.key, "alpha")
        self.assertEqual(change.after, "We do not store card numbers.")

    def test_a_cross_reference_is_not_a_claim(self) -> None:
        # `@fig-one` points at a figure in this document. Counting it would put
        # a claim in the changelog that cites nothing.
        self.write(
            MAIN_V2.replace("  source: [@alpha],\n)\n", "  source: [@alpha],\n) <fig-one>\n")
            + "\nSee @fig-one for the shape of it.\n",
            BIB_V2,
        )
        self.commit("a cross-reference")
        self.assertEqual(self.changes("claim-added", diffing.diff(self.cfg, "r", "HEAD~1")), [])

    # ── sources, assessments, figures, metadata

    def test_a_changed_source_names_the_field(self) -> None:
        change = self.only("source-changed")
        self.assertEqual(change.key, "alpha")
        self.assertEqual(change.before, 'title: Alpha')
        self.assertEqual(change.after, 'title: Alpha, revised')
        self.assertEqual(change.line, 1)

    def test_sources_added_and_removed(self) -> None:
        self.assertEqual(self.only("source-added").key, "gamma")
        removed = self.only("source-removed")
        self.assertEqual(removed.key, "beta")
        self.assertEqual(removed.before, "Web — Beta")

    def test_a_rewritten_assessment_is_a_removal_and_an_addition(self) -> None:
        # There is no assessment-changed kind, on purpose: a judgement that
        # reads differently is a different judgement, and it should be visible
        # as one.
        self.assertIn("pricing page has not been maintained", self.only("assessment-removed").before)
        self.assertIn("billing section needs rewriting", self.only("assessment-added").after)

    def test_a_replaced_figure_is_a_removal_and_an_addition(self) -> None:
        self.assertEqual(self.only("figure-removed").key, "A table that will not survive.")
        added = self.only("figure-added")
        self.assertEqual(added.key, "A different table entirely.")
        self.assertEqual(added.line, 21)

    def test_metadata_changes_name_the_field(self) -> None:
        change = self.only("meta-changed")
        self.assertEqual((change.key, change.before, change.after), ("version", "0.1 — Draft", "0.2 — Final"))

    # ── shape and output

    def test_counts_cover_every_group(self) -> None:
        counts = diffing.diff(self.cfg, "r")[0].counts
        self.assertEqual(set(counts), {"metadata", "sources", "claims", "assessments", "figures"})
        self.assertEqual(counts["claims"], {"added": 1, "removed": 1, "changed": 1})
        self.assertEqual(counts["sources"], {"added": 1, "removed": 1, "changed": 1})
        self.assertEqual(counts["figures"], {"added": 1, "removed": 1, "changed": 0})
        self.assertEqual(counts["metadata"], {"added": 0, "removed": 0, "changed": 1})

    def test_the_json_shape_survives_json_dumps(self) -> None:
        data = json.loads(json.dumps(diffing.to_json(diffing.diff(self.cfg, "r"))))
        self.assertEqual(data["rev"], "HEAD~1")
        self.assertEqual(data["count"], len(data["diffs"][0]["changes"]))
        row = data["diffs"][0]["changes"][0]
        self.assertEqual(set(row), {"kind", "key", "before", "after", "line"})
        self.assertEqual(data["diffs"][0]["id"], "r")

    def test_the_human_output_leads_with_the_counts(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = diffing.report_diffs(self.cfg, diffing.diff(self.cfg, "r"))
        printed = buffer.getvalue()
        self.assertEqual(code, 0, "a diff is news, not a failure")
        self.assertIn("claims 3 (1 added, 1 removed, 1 changed)", printed)
        for group in ("metadata", "sources", "claims", "assessments", "figures"):
            self.assertIn(f"\n    {group}\n", printed)
        self.assertIn("                 was  A cited fact looks like this @alpha.", printed)
        # A judgement is labelled by its own sentence, not by the digest that
        # gives it machine identity — a hex string in a client-facing changelog
        # is the kind of leak that makes people stop showing it to clients.
        self.assertIn(
            "      added    We think the whole billing section needs rewriting", printed
        )
        for change in self.changes("assessment-added"):
            self.assertNotIn(change.key, printed)
        # Nor does a figure need its Typst call read back at anyone.
        self.assertNotIn("srcfig(", printed)

    # ── the failures a person has to be able to act on

    def test_a_vault_outside_git_says_so(self) -> None:
        # Its own temporary directory, because anything under self.repo would
        # inherit that repository and the check would pass for the wrong reason.
        with tempfile.TemporaryDirectory() as name:
            outside = Path(name).resolve()
            (outside / "report-maker.toml").write_text("[vault]\n", encoding="utf-8")
            with self.assertRaises(diffing.DiffError) as caught:
                diffing.diff(load(outside), "r")
        message = str(caught.exception)
        self.assertIn("not inside a git repository", message)
        self.assertIn("init` and commit the vault", message)

    def test_an_unknown_revision_says_which_one(self) -> None:
        with self.assertRaises(diffing.DiffError) as caught:
            diffing.diff(self.cfg, "r", rev="v9.9.9")
        self.assertIn("v9.9.9", str(caught.exception))
        self.assertIn("log --oneline", str(caught.exception))

    def test_a_report_younger_than_the_revision_says_so(self) -> None:
        self.write(MAIN_V2, BIB_V2, rid="new")
        self.commit("a second report")
        with self.assertRaises(diffing.DiffError) as caught:
            diffing.diff(self.cfg, "new", rev="HEAD~1")
        message = str(caught.exception)
        self.assertIn("did not exist at HEAD~1", message)
        self.assertIn("reports/new/main.typ", message)

    def test_a_bibliography_that_did_not_exist_yet_is_an_empty_one(self) -> None:
        # Not an error: a report may perfectly well have gained its sources.yml
        # since the revision, and every entry in it is then a real addition.
        (self.root / "reports" / "later").mkdir()
        (self.root / "reports" / "later" / "main.typ").write_text("A start.\n", encoding="utf-8")
        self.commit("a report with no bibliography")
        (self.root / "reports" / "later" / "sources.yml").write_text(BIB_V1, encoding="utf-8")
        self.commit("its bibliography")
        added = self.changes("source-added", diffing.diff(self.cfg, "later"))
        self.assertEqual([c.key for c in added], ["alpha", "beta"])


class Quotations(unittest.TestCase):
    """`claim(…)` and `srcquote(…)`, which keep their citation in an argument."""

    def test_the_machinery_goes_and_the_words_stay(self) -> None:
        # `Note:` opens the quotation rather than an argument, so it stays —
        # trimming it would put words in the subject's mouth they did not say.
        self.assertEqual(
            diffing._quotation(
                '([Note: we do not store card numbers.], '
                'attribution: "example.com/security", source: [@alpha])'
            ),
            "Note: we do not store card numbers.",
        )

    def test_a_quotation_with_no_citation_is_left_to_the_prose_scan(self) -> None:
        # It is not evidence, whatever helper it was written with, so it is not
        # lifted out — the prose scan will judge it on its own merits.
        self.assertEqual(
            diffing._quotations("#claim([Something unsourced.])", set()), ([], [])
        )


class Matching(unittest.TestCase):
    """The pairing rule on its own, without git in the way."""

    def statements(self, *texts: str) -> list[diffing._Statement]:
        return [diffing._Statement(text=text, line=index + 1) for index, text in enumerate(texts)]

    def test_identical_text_pairs_before_anything_fuzzy(self) -> None:
        # An unchanged statement must never be stolen by a fuzzy match
        # elsewhere, or an edit somewhere else in the document reports as two.
        old = self.statements("The rate was 4 percent @a.", "The rate was 5 percent @a.")
        new = self.statements("The rate was 5 percent @a.", "The rate was 6 percent @a.")
        pairs, gone, fresh = diffing._pair(old, new, diffing.CLAIM_MATCH)
        self.assertIn((1, 0), pairs)  # the exact match, taken first
        self.assertIn((0, 1), pairs)  # what is left, paired by ratio
        self.assertEqual((gone, fresh), ([], []))

    def test_nothing_pairs_below_the_threshold(self) -> None:
        old = self.statements("Pricing starts at ten dollars a month @a.")
        new = self.statements("The vendor published a security advisory in March @b.")
        pairs, gone, fresh = diffing._pair(old, new, diffing.CLAIM_MATCH)
        self.assertEqual((pairs, gone, fresh), ([], [0], [0]))

    def test_no_threshold_means_exact_or_nothing(self) -> None:
        # How figures and assessments are compared: a caption that reads
        # differently is a different figure.
        old = self.statements("A table that will not survive.")
        new = self.statements("A table that will not survive!")
        self.assertEqual(diffing._pair(old, new, None), ([], [0], [0]))


if __name__ == "__main__":
    unittest.main()
