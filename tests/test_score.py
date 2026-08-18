"""Evidence density tests.

Two properties carry the whole module.

The first is that the classification is honest about the middle. A sentence with
a `@key` is cited, a sentence with `#assess` is a judgement, and a sentence with
neither is the thing the house rule exists to catch — so the interesting tests
are the ones where those meet: a line holding two sentences of different classes,
a sentence spanning three lines, a `@key` that is really a cross-reference.

The second is that `lines` covers the file. The app paints a rail from it, one
entry per editor line, and a rail with a hole in it silently mis-colours every
line below the hole. So the coverage assertions below are not book-keeping; they
are the contract with the editor.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import score  # noqa: E402
from engine.config import Config, load  # noqa: E402
from engine.workspace import Report, reports  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "examples/demo-vault"

BIB = {"alpha", "beta"}


def kinds(text: str, keys: set[str] | None = None) -> list[str]:
    return [s.kind for s in score.statements(text, keys=keys if keys is not None else BIB)]


def rail(text: str, keys: set[str] | None = None) -> list[str]:
    found = score.statements(text, keys=keys if keys is not None else BIB)
    return [lc.kind for lc in score.line_classes(text, found)]


class Classification(unittest.TestCase):
    def test_the_three_classes(self) -> None:
        self.assertEqual(kinds("A fact about the world @alpha.\n"), ["cited"])
        self.assertEqual(kinds("A judgement about it.#assess\n"), ["assessed"])
        self.assertEqual(kinds("A sentence that is neither one.\n"), ["unmarked"])

    def test_a_key_that_is_not_in_the_bibliography_is_not_a_citation(self) -> None:
        # `check` raises E006 for it; scoring must not quietly count it as
        # evidence, or a typo would improve the density.
        self.assertEqual(kinds("A fact about the world @nope.\n"), ["unmarked"])

    def test_an_empty_bibliography_accuses_nobody(self) -> None:
        # Same leniency the linter applies: with no sources.yml to check against,
        # a citation is taken at its word.
        self.assertEqual(kinds("A fact @anything.\n", keys=set()), ["cited"])

    def test_a_cross_reference_is_not_a_citation(self) -> None:
        text = "See the diagram above @fig-one for the shape of it.\n" "#metadata(none) <fig-one>\n"
        self.assertEqual(kinds(text), ["unmarked"])

    def test_assessment_blocks_mark_everything_inside_them(self) -> None:
        text = "#assessment[\n  A paragraph of interpretation, with no marker of its own.\n]\n"
        self.assertEqual(kinds(text), ["assessed"])

    def test_assess_after_the_full_stop_still_belongs_to_its_sentence(self) -> None:
        # `#assess` is written after the terminating period. Splitting sentences
        # on the period alone would hand the marker to the *next* sentence.
        text = "First, a judgement.#assess Then a plain sentence about nothing.\n"
        self.assertEqual(kinds(text), ["assessed", "unmarked"])

    def test_a_citation_wins_over_a_marker_in_the_same_sentence(self) -> None:
        self.assertEqual(kinds("A fact @alpha, and our read of it.#assess\n"), ["cited"])


class Structure(unittest.TestCase):
    """What is not prose, and therefore not a statement."""

    def test_helper_calls_carry_their_provenance_structurally(self) -> None:
        text = (
            "#srcfig(\n"
            "  table([a], [b]),\n"
            "  caption: [A table of two things.],\n"
            "  source: [@alpha],\n"
            ")\n"
        )
        self.assertEqual(kinds(text), [])
        self.assertEqual(rail(text), [score.NEUTRAL] * 6)

    def test_directives_and_the_show_rule_are_not_prose(self) -> None:
        text = (
            '#import "/.build/design/base/report.typ": report\n'
            "\n"
            "#show: report.with(\n"
            '  title: "A title that is not a statement",\n'
            '  sources: "/reports/x/sources.yml",\n'
            ")\n"
            "\n"
            "A real sentence, at last @alpha.\n"
        )
        self.assertEqual(kinds(text), ["cited"])
        self.assertEqual(rail(text)[:7], [score.NEUTRAL] * 7)

    def test_comments_are_scrubbed_before_anything_else(self) -> None:
        self.assertEqual(kinds("// A comment about the report, not in it.\n"), [])

    def test_pure_markup_is_neutral(self) -> None:
        self.assertEqual(kinds("#metadata(none) <references>\n"), [])
        self.assertEqual(kinds("]\n"), [])

    def test_a_parenthetical_is_prose_not_a_call(self) -> None:
        # In Typst markup only `#name(…)` is a call, so an aside in the middle of
        # a sentence must survive: blanking it would erase half the report.
        self.assertEqual(kinds("Revenue (see the appendix) grew last year @alpha.\n"), ["cited"])

    def test_prose_inside_a_content_block_still_counts(self) -> None:
        # The arguments of `#callout(…)` are structure; the body it is applied to
        # is prose a person wrote and has to be classified.
        text = '#callout(kind: "method")[\n  How the evidence was gathered.\n]\n'
        self.assertEqual(kinds(text), ["unmarked"])


class Lines(unittest.TestCase):
    def test_a_sentence_marks_every_line_it_spans(self) -> None:
        text = "A single fact\nspread over three\nlines of prose @alpha.\n"
        found = score.statements(text, keys=BIB)
        self.assertEqual([(s.line, s.end_line) for s in found], [(1, 3)])
        self.assertEqual(rail(text)[:3], ["cited"] * 3)

    def test_the_worse_class_wins_a_shared_line(self) -> None:
        # The rail is a warning device: a line that is half unmarked reads as
        # unmarked, or the half a reader has to fix disappears.
        self.assertEqual(rail("A fact @alpha. A bare claim.\n")[0], "unmarked")
        self.assertEqual(rail("A fact @alpha. A judgement.#assess\n")[0], "assessed")

    def test_every_line_of_the_file_gets_exactly_one_class(self) -> None:
        text = (
            "// header comment\n"
            "\n"
            "= A heading\n"
            "\n"
            "A fact @alpha.\n"
            "\n"
            "#srcfig(table([a]), caption: [c], source: [@beta])\n"
        )
        classes = score.line_classes(text, score.statements(text, keys=BIB))
        # One more than the newline count: the empty last line is a line the
        # cursor can sit on, so the rail has to have a block for it.
        self.assertEqual(len(classes), text.count("\n") + 1)
        self.assertEqual([lc.line for lc in classes], list(range(1, len(classes) + 1)))
        self.assertEqual(classes[4].kind, "cited")
        self.assertEqual(classes[0].kind, "neutral")

    def test_an_empty_report_still_has_a_line(self) -> None:
        self.assertEqual(score.line_classes("", []), [score.LineClass(1, "neutral")])


class HalfWritten(unittest.TestCase):
    """A report is scored while it is being typed, so nothing here may raise."""

    def test_every_prefix_of_a_real_report_scores(self) -> None:
        raw = (DEMO / "reports/examples/2026-08-16-example/main.typ").read_text(
            encoding="utf-8"
        )
        for cut in range(0, len(raw), 7):
            text = raw[:cut]
            found = score.statements(text, keys={"example-page", "own-measurement"})
            classes = score.line_classes(text, found)
            self.assertEqual(
                [lc.line for lc in classes], list(range(1, text.count("\n") + 2))
            )
            score.sections(text, found)

    def test_an_unterminated_call_degrades_instead_of_raising(self) -> None:
        # `check.call_span` runs to the end of the file when the paren never
        # closes, so the tail reads as structure — quiet, and never a traceback.
        self.assertEqual(kinds("#srcfig(table([a]), caption: [c\n"), [])

    def test_carriage_returns_do_not_shift_the_rail(self) -> None:
        text = "A fact @alpha.\r\n\r\nA judgement.#assess\r\n"
        self.assertEqual(kinds(text), ["cited", "assessed"])
        self.assertEqual(len(rail(text)), text.count("\n") + 1)


class Sections(unittest.TestCase):
    def test_headings_partition_the_report(self) -> None:
        text = (
            "= Scope\n"
            "\n"
            "A fact @alpha.\n"
            "\n"
            "== Findings\n"
            "\n"
            "A judgement.#assess\n"
            "A bare sentence about things.\n"
        )
        found = score.sections(text, score.statements(text, keys=BIB))
        self.assertEqual(
            [(s["title"], s["level"], s["line"]) for s in found],
            [("Scope", 1, 1), ("Findings", 2, 5)],
        )
        self.assertEqual(found[0]["cited"], 1)
        self.assertEqual((found[1]["assessed"], found[1]["unmarked"]), (1, 1))
        self.assertEqual(found[1]["density"], 0.5)

    def test_the_function_spelling_of_a_heading_counts_too(self) -> None:
        text = "#heading(level: 3)[Late findings]\n\nA bare sentence about things.\n"
        found = score.sections(text, score.statements(text, keys=BIB))
        self.assertEqual([(s["title"], s["level"]) for s in found], [("Late findings", 3)])

    def test_an_equals_sign_inside_a_call_is_not_a_heading(self) -> None:
        text = "#srcfig(table([= not a heading]), caption: [c], source: [@alpha])\n"
        self.assertEqual(score.sections(text, []), [])


class Vault(unittest.TestCase):
    """Against the demo vault, which is the vault the app opens on first run."""

    def setUp(self) -> None:
        self.cfg = load(DEMO)
        self.scores = score.score(self.cfg)

    def test_every_report_is_scored(self) -> None:
        self.assertEqual(
            [s.id for s in self.scores], [r.id for r in reports(self.cfg)]
        )
        self.assertTrue(self.scores)

    def test_the_rail_covers_each_file_end_to_end(self) -> None:
        for report, scored in zip(reports(self.cfg), self.scores):
            raw = report.main.read_text(encoding="utf-8")
            self.assertEqual(
                [lc.line for lc in scored.lines],
                list(range(1, raw.count("\n") + 2)),
                f"{report.id} rail does not cover the file",
            )
            self.assertTrue(
                all(lc.kind in score.SEVERITY for lc in scored.lines), report.id
            )

    def test_the_demo_reports_cite_and_assess(self) -> None:
        for scored in self.scores:
            self.assertGreater(scored.cited, 0, scored.id)
            self.assertGreater(scored.assessed, 0, scored.id)
            self.assertGreaterEqual(scored.density, 0.0)
            self.assertLessEqual(scored.density, 1.0)
            # Every entry in the demo bibliographies is cited somewhere.
            self.assertEqual(scored.sources_cited, scored.sources_total)
            self.assertGreater(scored.sources_total, 0, scored.id)

    def test_json_is_serialisable_and_named_the_way_the_app_expects(self) -> None:
        payload = json.loads(json.dumps(score.to_json(self.scores)))
        self.assertEqual(len(payload["reports"]), len(self.scores))
        first = payload["reports"][0]
        self.assertEqual(
            sorted(first),
            [
                "assessed",
                "blocks",
                "cited",
                "density",
                "families",
                "familyCounts",
                "id",
                "lines",
                "sections",
                "sourcesCited",
                "sourcesTotal",
                "unmarked",
            ],
        )
        self.assertEqual(sorted(first["lines"][0]), ["kind", "line"])
        # The depth columns ride alongside the density ones: how many distinct
        # source families a section rests on, and which one when it rests on
        # exactly one. See tests/test_score_depth.py for what they mean.
        self.assertEqual(
            sorted(first["sections"][0]),
            [
                "assessed",
                "citations",
                "cited",
                "density",
                "families",
                "family",
                "level",
                "line",
                "title",
                "unmarked",
            ],
        )
        self.assertEqual(payload["cited"], sum(s.cited for s in self.scores))

    def test_the_table_always_exits_zero(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            code = score.report_scores(self.cfg, self.scores)
        self.assertEqual(code, 0)
        self.assertIn("density", out.getvalue())
        for scored in self.scores:
            self.assertIn(scored.id, out.getvalue())

    def test_an_unmarked_report_is_reported_not_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "r"
            folder.mkdir()
            (folder / "main.typ").write_text(
                "Everything here is unmarked prose.\n", encoding="utf-8"
            )
            report = Report(id="r", folder=folder, cfg=Config(root=Path(tmp)))
            scored = score.score_report(report.cfg, report)
            self.assertEqual((scored.cited, scored.assessed, scored.unmarked), (0, 0, 1))
            self.assertEqual(scored.density, 0.0)
            self.assertEqual(scored.sources_total, 0)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(score.report_scores(report.cfg, [scored]), 0)

    def test_a_report_with_no_main_typ_is_empty_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = Report(id="r", folder=Path(tmp), cfg=Config(root=Path(tmp)))
            scored = score.score_report(report.cfg, report)
            self.assertEqual(scored.total, 0)
            self.assertEqual(scored.lines, [score.LineClass(1, "neutral")])

    def test_no_reports_is_not_an_error(self) -> None:
        with redirect_stdout(io.StringIO()):
            self.assertEqual(score.report_scores(self.cfg, []), 0)


if __name__ == "__main__":
    unittest.main()
