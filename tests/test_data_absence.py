"""Absence, and the three ways a failed source arrives dressed as a measurement.

`test_data.py` holds up one property: a report's numbers must not be able to
change without the build saying so. This file holds up the one next to it, which
is harder and worse — a report's numbers must not be able to be *missing*
without the build saying so.

The failure being tested is a real one, and it is worth stating in full because
every assertion below is a piece of it. An exporter read a metric as
`sig.get("ams_course_count", 0) or 0` and derived a label from the result: zero
meant "white space, absent from the corpus". Its collector returned 0 for every
row when the source database was missing. The database in fact held 421 courses
for the category the published report named as untapped, and the report's own
provenance line listed five sources while omitting the one that had failed.
Every layer did what it said. The defect was that a missing source and a
measured zero were spelled the same way.

So three groups of tests:

- **Rendering** — an empty cell must reach the page as an explicit mark and
  never as a blank or a zero. Asserted on the Typst source, and, when Typst is
  installed, by compiling a table with holes in it and reading the caption back
  out of the compiled document with `typst query`. That last one is the only
  test in the suite that checks what a reader would actually see here.
- **Degenerate columns** — W007, W008 and W009, each firing and each silent,
  because a warning that only ever fires is the half of a rule that costs
  somebody an afternoon.
- **Absence as a source** — a search that found nothing is a measurement, and
  has to be filable, citable and re-runnable like any other.

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

from engine import data, library, sources  # noqa: E402
from engine.config import load  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_TYP = ROOT / "engine/templates/base/data.typ"

TYPST = shutil.which(os.environ.get("TYPST_BIN") or "typst")

VAULT_TOML = """[vault]
reports = "reports"
"""

# The shape the failure above produced: a category with real courses behind it,
# and two whose collector came back with nothing. Read as zeros, the second and
# third are "white space"; read as absent, they are a question.
HOLES = """Skill,Courses,Share
Excel,,
Generative AI,214,0.31
RAG,,0.02
"""

# One column entirely empty — a source that failed, arriving as data.
EMPTY_COLUMN = """Skill,Courses,Notes
Excel,421,
Python,214,
RAG,7,
"""

# One column with the same value in every row — a join that matched nothing.
CONSTANT_COLUMN = """Skill,Courses,Provider
Excel,421,AMS
Python,214,AMS
RAG,7,AMS
"""

# The burgwiss shape exactly: every count zero, because the database was absent.
ZERO_COLUMN = """Skill,Courses,Share
Excel,0,0.00
Python,0,0.00
RAG,0,0.00
"""

# Nothing wrong with it. The silent half of every rule below.
HEALTHY = """Skill,Courses,Share
Excel,421,0.42
Python,214,0.21
RAG,7,0.01
"""

MAIN = """#import "/.build/design/base/report.typ": report
#import "/.build/design/base/data.typ": srctable

#show: report.with(
  title: "Numbers",
  sources: "/reports/2026-08-18-numbers/sources.yml",
)

= Skills

#srctable(
  "/reports/2026-08-18-numbers/data/skills.csv",
  caption: [What the reader should take from these numbers.],
  source: [@data-skills],
)
"""


class VaultCase(unittest.TestCase):
    """A real vault on disk, with one data file the tests rewrite."""

    report_id = "2026-08-18-numbers"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "report-maker.toml").write_text(VAULT_TOML, encoding="utf-8")
        self.folder = self.root / "reports" / self.report_id
        (self.folder / "data").mkdir(parents=True)
        (self.folder / "main.typ").write_text(MAIN, encoding="utf-8")
        (self.folder / "sources.yml").write_text("", encoding="utf-8")
        self.skills = self.folder / "data" / "skills.csv"
        self.skills.write_text(HEALTHY, encoding="utf-8")
        self.cfg = load(self.root)
        self.report = data.one(self.cfg, self.report_id)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ── helpers ──────────────────────────────────────────────────────────────

    def given(self, text: str) -> data.DataFile:
        """Rewrite the data file, register it, and describe what is there now."""
        self.skills.write_text(text, encoding="utf-8")
        return data.add(self.cfg, self.report_id, str(self.skills))

    def codes(self) -> list[str]:
        return [record[1] for record in data.findings(self.report)]

    def message(self, code: str) -> str:
        for record in data.findings(self.report):
            if record[1] == code:
                return record[4]
        raise AssertionError(f"{code} did not fire; got {self.codes()}")


# ─────────────────────────────────────────────────────────────────────────────
# Reading a file column-wise


class Columns(VaultCase):
    def test_headers_name_the_columns(self) -> None:
        self.skills.write_text(HEALTHY, encoding="utf-8")
        found = data.columns_of(self.skills)
        self.assertEqual([column.name for column in found], ["Skill", "Courses", "Share"])
        self.assertEqual(found[1].cells, ("421", "214", "7"))

    def test_a_headerless_file_names_columns_by_position(self) -> None:
        # Counted the way a person counts columns, not the way Python does: a
        # message about "column 0" sends somebody to the wrong place.
        self.skills.write_text("alpha,1\nbeta,2\n", encoding="utf-8")
        found = data.columns_of(self.skills)
        self.assertEqual([c.name for c in found], ["column 1", "column 2"])

    def test_a_short_row_contributes_an_empty_cell(self) -> None:
        # The alternative is a column that quietly gets shorter, and a rule about
        # "every row" that silently stopped being about every row.
        self.skills.write_text("a,b,c\n1,2,3\n4,5\n", encoding="utf-8")
        self.assertEqual(data.columns_of(self.skills)[2].cells, ("3", ""))

    def test_ornament_does_not_stop_a_zero_being_zero(self) -> None:
        self.assertEqual(data._number("€0.00"), 0.0)
        self.assertEqual(data._number("(0)"), 0.0)
        self.assertEqual(data._number("1,240"), 1240.0)
        self.assertIsNone(data._number(""))
        self.assertIsNone(data._number("absent"))


# ─────────────────────────────────────────────────────────────────────────────
# The degenerate-derivation rules


class DegenerateColumns(VaultCase):
    """Each code, firing and silent."""

    def test_a_healthy_table_raises_nothing(self) -> None:
        self.given(HEALTHY)
        self.assertEqual(self.codes(), [])

    # ── W007

    def test_w007_fires_on_a_column_that_arrived_empty(self) -> None:
        self.given(EMPTY_COLUMN)
        self.assertIn("W007", self.codes())
        message = self.message("W007")
        self.assertIn('"Notes"', message)  # which column
        self.assertIn("a source that failed", message)  # why it matters
        self.assertIn("data absence", message)  # what to do instead

    def test_w007_is_a_warning_pointed_at_the_file(self) -> None:
        self.given(EMPTY_COLUMN)
        record = next(r for r in data.findings(self.report) if r[1] == "W007")
        self.assertEqual(record[0], "warning")
        self.assertEqual(record[2].resolve(), self.skills.resolve())

    def test_w007_stays_silent_for_a_half_filled_column(self) -> None:
        # Some of the cells are there, so the source did not fail — the render
        # side marks the holes, and there is nothing here to warn about.
        self.given(HOLES)
        self.assertNotIn("W007", self.codes())

    # ── W008

    def test_w008_fires_on_a_constant_column(self) -> None:
        self.given(CONSTANT_COLUMN)
        self.assertIn("W008", self.codes())
        message = self.message("W008")
        self.assertIn('"Provider"', message)
        self.assertIn("AMS", message)  # the value it repeats
        self.assertIn("join that matched nothing", message)

    def test_w008_stays_silent_when_one_row_differs(self) -> None:
        self.given(CONSTANT_COLUMN.replace("RAG,7,AMS", "RAG,7,WIFI"))
        self.assertNotIn("W008", self.codes())

    def test_w008_stays_silent_on_a_single_row(self) -> None:
        # Every column of a one-row file is constant by arithmetic, and a warning
        # that is true of every such file tells nobody anything.
        self.given("Skill,Courses\nExcel,421\n")
        self.assertNotIn("W008", self.codes())

    # ── W009

    def test_w009_fires_on_a_column_of_exact_zeros(self) -> None:
        self.given(ZERO_COLUMN)
        self.assertIn("W009", self.codes())
        message = self.message("W009")
        self.assertIn('"Courses"', message)
        self.assertIn("when its source is missing", message)
        self.assertIn("derived", message)  # the label is the real damage

    def test_w009_fires_on_formatted_zeros_too(self) -> None:
        # "€0.00" is a zero. A rule that only recognised the bare glyph would
        # miss every export that formats its currency.
        self.given("Skill,Spend\nExcel,€0.00\nPython,€0.00\nRAG,€0.00\n")
        self.assertIn("W009", self.codes())

    def test_w009_stays_silent_when_one_value_is_not_zero(self) -> None:
        self.given(ZERO_COLUMN.replace("Excel,0,0.00", "Excel,421,0.42"))
        self.assertNotIn("W009", self.codes())

    def test_a_column_of_zeros_is_reported_once_not_twice(self) -> None:
        # It is constant as well as zero. Saying so in two different codes trains
        # people to skim both.
        self.given(ZERO_COLUMN)
        for_courses = [
            code
            for code, message in data.degenerate(data.scan(self.report)[0])
            if '"Courses"' in message
        ]
        self.assertEqual(for_courses, ["W009"])

    # ── living with the rest of the linter

    def test_the_new_codes_do_not_disturb_the_old_ones(self) -> None:
        # E011 still has to be the thing that fails the build; the additions are
        # warnings and must not have quietly become errors.
        self.given(ZERO_COLUMN)
        self.skills.write_text(ZERO_COLUMN.replace("RAG,0", "Rag,0"), encoding="utf-8")
        records = data.findings(self.report)
        levels = {record[1]: record[0] for record in records}
        self.assertEqual(levels["E011"], "error")
        self.assertTrue(all(levels[code] == "warning" for code in levels if code[0] == "W"))

    def test_they_survive_the_conversion_to_check_findings(self) -> None:
        from engine import check

        self.given(EMPTY_COLUMN)
        converted = data.to_findings(data.check(self.cfg))
        self.assertIn("W007", [finding.code for finding in converted])
        for finding in converted:
            self.assertIsInstance(finding, check.Finding)
            self.assertTrue(finding.message)


# ─────────────────────────────────────────────────────────────────────────────
# A search that found nothing


class AbsenceSources(VaultCase):
    corpus = "AMS course catalogue"
    query = "prompt engineering"

    def test_the_entry_records_what_would_let_somebody_re_run_it(self) -> None:
        source = data.absence_source(self.corpus, self.query, date="2026-08-18")
        self.assertEqual(source.fields["type"], "Misc")
        self.assertEqual(source.fields["author"], data.ABSENCE_AUTHOR)
        self.assertEqual(source.fields["date"], "2026-08-18")
        self.assertIn(self.corpus, source.fields["title"])
        self.assertIn(self.query, source.fields["title"])
        note = source.fields["note"]
        self.assertIn(f"searched: {self.corpus}", note)
        self.assertIn(f'query: "{self.query}"', note)
        self.assertTrue(note.endswith(data.ABSENCE_RESULT))

    def test_the_free_form_note_never_displaces_the_result(self) -> None:
        # The result marker is last on purpose: `registry` identifies a data file
        # by a note ending in a path, so a note ending in "prices.csv" would
        # otherwise turn this entry into a phantom CSV.
        source = data.absence_source(
            self.corpus, self.query, note="ran against the export in data/prices.csv"
        )
        self.assertTrue(source.fields["note"].endswith(data.ABSENCE_RESULT))
        self.assertIn("prices.csv", source.fields["note"])

    def test_the_key_names_both_the_corpus_and_the_query(self) -> None:
        key = data.absence_key(self.corpus, self.query)
        self.assertTrue(key.startswith(data.ABSENCE_PREFIX))
        self.assertIn("ams", key)
        self.assertIn("prompt", key)

    def test_a_second_search_of_the_same_corpus_gets_its_own_key(self) -> None:
        first = data.absence_key(self.corpus, self.query)
        self.assertNotEqual(first, data.absence_key(self.corpus, self.query, {first}))

    def test_a_search_with_nothing_in_it_is_refused(self) -> None:
        # An entry that records a search without recording what was searched is a
        # citation nobody can check, which is the failure this whole file is about.
        with self.assertRaises(data.DataError):
            data.absence_source("", self.query)
        with self.assertRaises(data.DataError):
            data.absence_source(self.corpus, "   ")

    def test_it_lands_in_the_bibliography_and_parses_back(self) -> None:
        _report, source = data.add_absence(
            self.cfg, self.report_id, self.corpus, self.query, date="2026-08-18"
        )
        parsed = sources.parse(self.report.sources)
        self.assertEqual([entry.key for entry in parsed], [source.key])
        self.assertEqual(parsed[0].author, data.ABSENCE_AUTHOR)
        self.assertTrue(parsed[0].fields["note"].endswith(data.ABSENCE_RESULT))

    def test_running_the_search_again_files_it_rather_than_overwriting(self) -> None:
        # The earlier search is still true of the date it was run, and a report
        # that cited it is still standing on something.
        for when in ("2026-05-01", "2026-08-18"):
            data.add_absence(
                self.cfg, self.report_id, self.corpus, self.query, date=when
            )
        parsed = sources.parse(self.report.sources)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(
            {entry.fields["date"] for entry in parsed}, {"2026-05-01", "2026-08-18"}
        )

    def test_a_named_key_is_rewritten_in_place(self) -> None:
        data.add_absence(
            self.cfg, self.report_id, self.corpus, self.query, key="no-prompt-courses",
            date="2026-05-01",
        )
        data.add_absence(
            self.cfg, self.report_id, self.corpus, self.query, key="no-prompt-courses",
            date="2026-08-18",
        )
        parsed = sources.parse(self.report.sources)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].fields["date"], "2026-08-18")

    def test_an_absence_entry_is_not_mistaken_for_a_data_file(self) -> None:
        data.add_absence(
            self.cfg, self.report_id, self.corpus, self.query,
            note="checked against data/skills.csv",
        )
        self.assertEqual(data.registry(self.report), {})

    def test_the_command_hands_back_a_sentence_that_makes_the_claim(self) -> None:
        report, source = data.add_absence(
            self.cfg, self.report_id, self.corpus, self.query
        )
        line = data.absence_line(source, self.corpus, self.query)
        self.assertIn(f"@{source.key}", line)
        self.assertIn(self.query, line)
        printed = io.StringIO()
        with redirect_stdout(printed):
            self.assertEqual(data.report_absence(report, source, self.corpus, self.query), 0)
        self.assertIn(source.key, printed.getvalue())


# ─────────────────────────────────────────────────────────────────────────────
# What reaches the page


class RenderedMarkSource(unittest.TestCase):
    """The Typst side, read as source.

    These are shallow assertions and they are here for one deep reason: the two
    ways a hole in a data file can reach a branded page are as a blank and as a
    zero, and both are indistinguishable from a measurement. This checks the
    component never learns to do either.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.src = DATA_TYP.read_text(encoding="utf-8")

    def test_the_mark_is_a_typographic_dash_in_a_brand_colour(self) -> None:
        self.assertIn("sym.dash.fig", self.src)
        # Never a hex code in a .typ: colours live in the brand pack.
        self.assertIn("colors.ink-faint)[#sym.dash.fig]", self.src)

    def test_an_empty_cell_is_substituted_before_anything_else(self) -> None:
        # `_blank` first in the cell chain, so no branch can render a hole as the
        # bare value it came in as.
        self.assertIn("let cell(value, index) = if _blank(value) {", self.src)

    def test_the_author_can_say_what_absence_looks_like(self) -> None:
        self.assertIn("missing: auto,", self.src)
        self.assertIn("missing-label: _MISSING_LABEL,", self.src)
        self.assertIn('#let _MISSING_LABEL = "not measured"', self.src)

    def test_nothing_substitutes_a_zero(self) -> None:
        # The one thing this component must never do. Written as a scan of the
        # cell logic rather than a grep for "0", because a zero appearing as a
        # default anywhere in here is the exact defect.
        cell_logic = self.src.split("let cell(value, index)")[1].split("srcfig(")[0]
        self.assertNotIn('"0"', cell_logic)
        self.assertNotIn("[0]", cell_logic)


@unittest.skipUnless(TYPST, "typst is not on PATH")
class RenderedMarkCompiled(unittest.TestCase):
    """The same rule, checked against a compiled document.

    Every other assertion in this file reads source. This one renders a table
    with holes in it and reads the caption back out with `typst query`, which is
    the only way to see what a reader would see. It is the difference between
    believing the component substitutes an absence and knowing it.
    """

    report_id = "2026-08-18-numbers"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "report-maker.toml").write_text(VAULT_TOML, encoding="utf-8")
        self.folder = self.root / "reports" / self.report_id
        (self.folder / "data").mkdir(parents=True)
        (self.folder / "main.typ").write_text(MAIN, encoding="utf-8")
        (self.folder / "sources.yml").write_text(
            'data-skills:\n  type: Misc\n  title: "Skills — data file"\n'
            '  author: "own data"\n',
            encoding="utf-8",
        )
        self.skills = self.folder / "data" / "skills.csv"
        self.cfg = load(self.root)
        with redirect_stdout(io.StringIO()):
            library.stage(self.cfg)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ── helpers ──────────────────────────────────────────────────────────────

    def caption(self, csv_text: str) -> str:
        """Compile the report and return its table caption as plain text."""
        self.skills.write_text(csv_text, encoding="utf-8")
        main = self.folder / "main.typ"
        built = subprocess.run(
            [TYPST, "compile", "--root", str(self.root), str(main),
             str(self.root / "o.pdf")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(built.returncode, 0, built.stderr)
        queried = subprocess.run(
            [TYPST, "query", "--root", str(self.root), str(main),
             "figure", "--field", "caption"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(queried.returncode, 0, queried.stderr)
        return "".join(_text_of(json.loads(queried.stdout)))

    # ── the tests ────────────────────────────────────────────────────────────

    def test_a_table_with_holes_still_compiles(self) -> None:
        self.assertIn("Cap", self.caption(HOLES) + "Cap")  # compilation is the assertion

    def test_the_caption_says_what_the_mark_means(self) -> None:
        # A glyph with no stated meaning is a convention, and a convention is
        # something the reader has to already know.
        caption = self.caption(HOLES)
        self.assertIn("not measured", caption)
        self.assertIn("‒", caption)  # the figure dash reached the page
        self.assertIn("absence and not a zero", caption)
        self.assertIn("3 cells", caption)  # both holes, and the short row's

    def test_a_complete_table_carries_no_legend(self) -> None:
        # A report that has nothing missing must not apologise for a problem it
        # does not have, or the legend stops meaning anything when it appears.
        caption = self.caption(HEALTHY)
        self.assertNotIn("not measured", caption)
        self.assertIn("What the reader should take", caption)

    def test_the_author_can_choose_the_mark_and_still_gets_a_legend(self) -> None:
        main = self.folder / "main.typ"
        main.write_text(
            main.read_text(encoding="utf-8").replace(
                "source: [@data-skills],",
                'source: [@data-skills],\n  missing: [n/a],\n'
                '  missing-label: "not applicable",',
            ),
            encoding="utf-8",
        )
        caption = self.caption(HOLES)
        self.assertIn("not applicable", caption)
        self.assertIn("n/a", caption)


def _text_of(node) -> list[str]:
    """Every string in a `typst query` content tree, in reading order.

    Typst's JSON form of content is a tree of `func`/`children`; the text lives
    in the leaves and the spaces are their own nodes, so a naive `str(json)`
    would run words together and a naive substring test would then fail on
    something that is on the page.
    """
    found: list[str] = []
    if isinstance(node, dict):
        if node.get("func") in ("text", "symbol"):
            found.append(str(node.get("text", "")))
        elif node.get("func") == "space":
            found.append(" ")
        for value in node.values():
            found += _text_of(value)
    elif isinstance(node, list):
        for value in node:
            found += _text_of(value)
    return found


if __name__ == "__main__":
    unittest.main()
