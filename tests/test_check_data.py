"""The data rules, reached the way a build reaches them.

`tests/test_data.py` covers `data.findings` directly: given a report and a
registered CSV, does E011 fire when the bytes move. This file asks a different
and, on the evidence, more important question — does anything *call* it.

For most of this project's life the answer was no. `data.py` calls E011 "the
load-bearing one — the moment a stale spreadsheet would otherwise slip into a
signed-off report", and it was reachable from exactly one place: the `data
check` subcommand, which nothing runs by default. `check`, `all`, the app's
Problems panel and the MCP server all went round it. A vault could register a
CSV, have a table read it, have somebody edit a cell in a spreadsheet, and
rebuild to a green tick and a PDF carrying the new number under the old
citation. Every unit test for the rule passed the whole time, because every one
of them called `data.findings` itself.

So these tests deliberately go through `check.check` and nothing else. A test
that reached for `data.findings` would pass whether or not the wiring exists,
which is the failure mode that let this ship decorative in the first place.

    python3 -m unittest tests.test_check_data
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import check, data, scaffold  # noqa: E402
from engine.config import Config, load  # noqa: E402
from engine.workspace import Report  # noqa: E402

#: The codes `data.py` owns. Restated here rather than imported because the
#: guard test's whole claim is "none of these appear", and a constant the module
#: could quietly shrink would make that claim quietly weaker.
DATA_CODES = {"E010", "E011", "W005", "W006", "W007", "W008", "W009"}

MAIN = """#import "/.build/design/base/report.typ": report
#import "/.build/design/base/data.typ": *

#show: report.with(
  title: "Coverage",
  sources: "/reports/{rid}/sources.yml",
)

= Coverage

The counted export is below @{key}.

#srctable(
  "/reports/{rid}/data/coverage.csv",
  caption: [What the reader should take from these numbers.],
  source: [@{key}],
)
"""

CSV = "Subsystem,Rules\nCitations,9\nQuotations,3\nData files,7\n"


class DataRulesFromCheck(unittest.TestCase):
    """A scratch vault with one report and one registered CSV."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        with redirect_stdout(io.StringIO()):
            scaffold.init(self.root)
        self.cfg: Config = load(self.root)

        self.rid = "2026-01-01-coverage"
        self.folder = self.cfg.reports / self.rid
        self.folder.mkdir(parents=True)
        (self.folder / "sources.yml").write_text("", encoding="utf-8")
        # A report is a folder holding main.typ, so it has to exist before
        # `data add` can be pointed at it. The srctable is written in once the
        # key is known, which is the order a person does it in too.
        (self.folder / "main.typ").write_text("", encoding="utf-8")

        loose = self.root / "coverage.csv"
        loose.write_text(CSV, encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            registered = data.add(self.cfg, self.rid, loose)
        self.key = registered.key
        self.csv = self.folder / "data" / "coverage.csv"

        (self.folder / "main.typ").write_text(
            MAIN.format(rid=self.rid, key=self.key), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def codes(self) -> list[str]:
        return [finding.code for finding in check.check(self.cfg)]

    # ── the regression ───────────────────────────────────────────────────────

    def test_a_registered_csv_that_still_matches_is_clean(self) -> None:
        self.assertEqual(self.codes(), [])

    def test_editing_one_byte_makes_check_itself_report_e011(self) -> None:
        """The whole point. Not `data check` — `check`."""
        self.csv.write_text(CSV.replace("Citations,9", "Citations,999"), encoding="utf-8")
        self.assertIn("E011", self.codes())

    def test_the_e011_finding_carries_the_report_id(self) -> None:
        """`data.to_findings` cannot stamp one — the record tuple has no report
        field — so the app's Problems panel gets a finding it can route only if
        `check` adds it on the way past."""
        self.csv.write_text(CSV.replace("Citations,9", "Citations,999"), encoding="utf-8")
        stale = [f for f in check.check(self.cfg) if f.code == "E011"]
        self.assertEqual([f.report for f in stale], [self.rid])

    def test_a_stale_csv_is_an_error_and_not_a_warning(self) -> None:
        """E011 has to stop a build. A warning would let the PDF ship."""
        self.csv.write_text(CSV.replace("Citations,9", "Citations,999"), encoding="utf-8")
        stale = [f for f in check.check(self.cfg) if f.code == "E011"]
        self.assertTrue(stale and all(f.level == "error" for f in stale))

    def test_re_registering_the_new_bytes_clears_it(self) -> None:
        """The sanctioned way through the rule still works from `check`'s side."""
        self.csv.write_text(CSV.replace("Citations,9", "Citations,999"), encoding="utf-8")
        self.assertIn("E011", self.codes())
        with redirect_stdout(io.StringIO()):
            data.add(self.cfg, self.rid, self.csv)
        self.assertNotIn("E011", self.codes())

    # ── the guard, which is the other half of the promise ────────────────────

    def test_a_report_with_no_data_folder_and_no_srctable_is_never_scanned(self) -> None:
        """README promises a vault with no CSV never pays for scanning one."""
        plain = self.cfg.reports / "2026-01-02-plain"
        plain.mkdir(parents=True)
        (plain / "main.typ").write_text(
            '#import "/.build/design/base/report.typ": report\n\n'
            "#show: report.with(\n"
            '  title: "Plain",\n'
            '  sources: "/reports/2026-01-02-plain/sources.yml",\n'
            ")\n\n= Plain\n\nNothing here reads a number.\n",
            encoding="utf-8",
        )
        (plain / "sources.yml").write_text("", encoding="utf-8")
        report = Report(id="2026-01-02-plain", folder=plain, cfg=self.cfg)
        codes = [f.code for f in check.check_report(self.cfg, report)]
        self.assertEqual([c for c in codes if c in DATA_CODES], [])


if __name__ == "__main__":
    unittest.main()
