"""CSV-backed table tests.

One property carries the whole feature: a report's numbers must not be able to
change without the build saying so. Everything here exists to hold that up.

`describe` is tested against files a spreadsheet actually produces — a semicolon
export, a tab export, a column of prices with currency signs, a file with no
header row — because a dialect read wrongly is a table rendered wrongly, and it
renders wrongly *quietly*: one wide column instead of four, or a row of data
promoted to column labels.

The rules are tested twice each, firing and silent. A linter rule that only
fires is half a rule: the half that costs somebody an afternoon is the false
positive nobody wrote a test for. E011 gets the most attention, because it is
the one that turns a refreshed spreadsheet from a silently wrong number into a
failed build.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import data, sources  # noqa: E402
from engine.config import load  # noqa: E402

VAULT_TOML = """[vault]
reports = "reports"
"""

PRICES = """Plan,Monthly,Annual,Seats,Notes
Starter,"$19.00","$190.00",3,Entry tier
Team,"$49.00","$490.00",10,
Business,"$149.00","$1,490.00",50,Volume discount at 100
"""

# The shape a European spreadsheet exports: semicolons, and a `.csv` extension
# that says nothing about it.
SEMICOLONS = """region;units;share
North;1240;0.42
South;870;0.29
"""

TABS = "alpha\t1.5\t-2\nbeta\t3.25\t7\n"

MAIN = """#import "/.build/design/base/report.typ": report
#import "/.build/design/base/data.typ": srctable

#show: report.with(
  title: "Numbers",
  sources: "/reports/2026-08-18-numbers/sources.yml",
)

= Prices

#srctable(
  "/reports/2026-08-18-numbers/data/prices.csv",
  caption: [Published list prices.],
  source: [@data-prices],
)
"""


class VaultCase(unittest.TestCase):
    """A real vault on disk, because every rule here reads the filesystem."""

    report_id = "2026-08-18-numbers"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "report-maker.toml").write_text(VAULT_TOML, encoding="utf-8")
        self.folder = self.root / "reports" / self.report_id
        (self.folder / "data").mkdir(parents=True)
        (self.folder / "main.typ").write_text(MAIN, encoding="utf-8")
        (self.folder / "sources.yml").write_text("", encoding="utf-8")
        self.prices = self.folder / "data" / "prices.csv"
        self.prices.write_text(PRICES, encoding="utf-8")
        self.cfg = load(self.root)
        self.report = data.one(self.cfg, self.report_id)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ── helpers ──────────────────────────────────────────────────────────────

    def codes(self) -> list[str]:
        return [record[1] for record in data.findings(self.report)]

    def message(self, code: str) -> str:
        for record in data.findings(self.report):
            if record[1] == code:
                return record[4]
        raise AssertionError(f"{code} did not fire; got {self.codes()}")

    def register(self, path: Path | None = None) -> data.DataFile:
        return data.add(self.cfg, self.report_id, str(path or self.prices))

    def append_to_main(self, text: str) -> None:
        main = self.folder / "main.typ"
        main.write_text(main.read_text(encoding="utf-8") + text, encoding="utf-8")


class Describing(VaultCase):
    def test_counts_rows_below_the_header(self) -> None:
        found = data.describe(self.prices, report=self.report)
        self.assertEqual(found.rows, 3)
        self.assertEqual(found.columns, 5)
        self.assertEqual(found.headers, ["Plan", "Monthly", "Annual", "Seats", "Notes"])
        self.assertEqual(found.rel, "data/prices.csv")
        self.assertEqual(found.key, "data-prices")
        self.assertEqual(found.delimiter, ",")

    def test_sniffs_semicolons_in_a_csv(self) -> None:
        path = self.folder / "data" / "regions.csv"
        path.write_text(SEMICOLONS, encoding="utf-8")
        found = data.describe(path, report=self.report)
        self.assertEqual(found.delimiter, ";")
        # Read with the wrong delimiter this would be one column, not three —
        # which is exactly how a tab-separated table renders as a single smear.
        self.assertEqual(found.columns, 3)
        self.assertEqual(found.rows, 2)

    def test_a_tsv_with_no_header_keeps_every_row(self) -> None:
        path = self.folder / "data" / "series.tsv"
        path.write_text(TABS, encoding="utf-8")
        found = data.describe(path, report=self.report)
        self.assertEqual(found.delimiter, "\t")
        self.assertEqual(found.headers, [])
        self.assertEqual((found.rows, found.columns), (2, 3))

    def test_nested_files_get_distinguishable_keys(self) -> None:
        path = self.folder / "data" / "2026" / "q1.csv"
        path.parent.mkdir()
        path.write_text("a,b\n1,2\n", encoding="utf-8")
        self.assertEqual(data.describe(path, report=self.report).key, "data-2026-q1")

    def test_a_missing_file_is_an_error_not_a_traceback(self) -> None:
        with self.assertRaises(data.DataError):
            data.describe(self.folder / "data" / "nope.csv", report=self.report)

    def test_scan_finds_every_file_and_skips_the_hidden_ones(self) -> None:
        (self.folder / "data" / "_draft.csv").write_text("a\n1\n", encoding="utf-8")
        (self.folder / "data" / "notes.txt").write_text("not data", encoding="utf-8")
        self.assertEqual([f.rel for f in data.scan(self.report)], ["data/prices.csv"])


class Checksums(VaultCase):
    def test_the_sha_moves_when_the_file_does(self) -> None:
        before = data.sha_of(self.prices)
        self.prices.write_text(PRICES.replace("$19.00", "$21.00"), encoding="utf-8")
        after = data.sha_of(self.prices)
        self.assertNotEqual(before, after)
        self.assertEqual(len(after), 64)

    def test_rewriting_identical_bytes_does_not(self) -> None:
        before = data.sha_of(self.prices)
        self.prices.write_text(PRICES, encoding="utf-8")
        self.assertEqual(before, data.sha_of(self.prices))


class Registering(VaultCase):
    def test_the_entry_round_trips_through_sources(self) -> None:
        found = self.register()
        parsed = sources.parse(self.report.sources)
        self.assertEqual([s.key for s in parsed], ["data-prices"])

        entry = parsed[0]
        self.assertEqual(entry.type, "Misc")
        self.assertEqual(entry.author, "own data")
        self.assertEqual(entry.title, "Prices — data file")
        self.assertIn(f"sha256:{found.sha256}", entry.fields["note"])
        self.assertIn("3 rows × 5 columns", entry.fields["note"])
        self.assertTrue(entry.fields["note"].endswith("data/prices.csv"))

    def test_the_registry_reads_its_own_notes_back(self) -> None:
        found = self.register()
        registered = data.registry(self.report)["data/prices.csv"]
        self.assertEqual(registered.key, "data-prices")
        self.assertEqual(registered.sha256, found.sha256)
        self.assertGreater(registered.line, 0)

    def test_a_hand_written_key_survives_a_rescan(self) -> None:
        data.add(self.cfg, self.report_id, str(self.prices), key="list-prices")
        self.assertEqual([f.key for f in data.scan(self.report)], ["list-prices"])

    def test_re_registering_refreshes_the_checksum_in_place(self) -> None:
        self.register()
        self.prices.write_text(PRICES.replace("$19.00", "$21.00"), encoding="utf-8")
        self.assertIn("E011", self.codes())
        self.register()
        self.assertNotIn("E011", self.codes())
        # One entry, not two: `add` upserts, so the fix for a stale checksum is
        # to run the same command again.
        self.assertEqual(len(sources.parse(self.report.sources)), 1)

    def test_a_file_from_elsewhere_is_copied_into_the_report(self) -> None:
        outside = self.root / "exported.csv"
        outside.write_text(SEMICOLONS, encoding="utf-8")
        # `add` says on stdout that it copied the file; that line is part of the
        # command, but it is not part of the test run's output.
        with redirect_stdout(io.StringIO()):
            found = data.add(self.cfg, self.report_id, str(outside))
        self.assertEqual(found.rel, "data/exported.csv")
        self.assertTrue((self.folder / "data" / "exported.csv").is_file())

    def test_the_paste_line_is_project_absolute(self) -> None:
        line = data.srctable_call(self.cfg, self.register())
        self.assertIn(f'"/reports/{self.report_id}/data/prices.csv"', line)
        self.assertIn("source: [@data-prices]", line)


class Referencing(VaultCase):
    def test_calls_are_found_with_the_path_as_written(self) -> None:
        self.assertEqual(
            data.referenced(self.report),
            {f"/reports/{self.report_id}/data/prices.csv": [11]},
        )

    def test_a_commented_out_call_is_not_a_call(self) -> None:
        self.append_to_main('\n// #srctable("/reports/x/data/old.csv")\n')
        self.assertEqual(len(data.referenced(self.report)), 1)


class Rules(VaultCase):
    """Each code, firing and silent."""

    def test_a_registered_and_cited_table_raises_nothing(self) -> None:
        self.register()
        self.assertEqual(self.codes(), [])

    # ── E010

    def test_e010_fires_on_a_table_built_from_nothing(self) -> None:
        self.register()
        self.append_to_main(
            '\n#srctable("/reports/2026-08-18-numbers/data/gone.csv",'
            " source: [@data-prices])\n"
        )
        self.assertIn("E010", self.codes())
        self.assertIn("gone.csv", self.message("E010"))

    def test_e010_stays_silent_for_a_relative_path_that_resolves(self) -> None:
        self.register()
        self.append_to_main('\n#srctable("data/prices.csv", source: [@data-prices])\n')
        self.assertNotIn("E010", self.codes())

    def test_e010_ignores_a_path_it_cannot_read(self) -> None:
        # A computed path is not a missing file — claiming it is would be a
        # confident lie about code the rule cannot follow.
        self.register()
        self.append_to_main("\n#srctable(chosen-path, source: [@data-prices])\n")
        self.assertNotIn("E010", self.codes())

    # ── E011

    def test_e011_fires_when_the_file_moved_under_the_report(self) -> None:
        found = self.register()
        self.prices.write_text(PRICES.replace("$19.00", "$21.00"), encoding="utf-8")
        message = self.message("E011")
        self.assertIn(found.sha256, message)  # what was recorded
        self.assertIn(data.sha_of(self.prices), message)  # what is there now
        self.assertIn("may have moved under it", message)

    def test_e011_is_an_error_not_a_warning(self) -> None:
        self.register()
        self.prices.write_text(PRICES.replace("$19.00", "$21.00"), encoding="utf-8")
        levels = {r[0] for r in data.findings(self.report) if r[1] == "E011"}
        self.assertEqual(levels, {"error"})

    def test_e011_points_at_the_bibliography_line(self) -> None:
        self.register()
        self.prices.write_text(PRICES.replace("$19.00", "$21.00"), encoding="utf-8")
        record = next(r for r in data.findings(self.report) if r[1] == "E011")
        self.assertEqual(record[2], self.report.sources)
        self.assertEqual(record[3], data.registry(self.report)["data/prices.csv"].line)

    def test_e011_stays_silent_when_only_the_mtime_moved(self) -> None:
        self.register()
        self.prices.write_text(PRICES, encoding="utf-8")  # same bytes, new mtime
        self.assertNotIn("E011", self.codes())

    def test_e011_stays_silent_for_a_file_nobody_registered(self) -> None:
        # Nothing was promised about this file, so nothing was broken.
        self.assertNotIn("E011", self.codes())

    # ── W005

    def test_w005_fires_on_data_no_table_reads(self) -> None:
        self.register()
        spare = self.folder / "data" / "spare.csv"
        spare.write_text(SEMICOLONS, encoding="utf-8")
        self.assertIn("W005", self.codes())
        self.assertIn("spare.csv", self.message("W005"))

    def test_w005_stays_silent_once_a_table_reads_it(self) -> None:
        self.register()
        spare = self.folder / "data" / "spare.csv"
        spare.write_text(SEMICOLONS, encoding="utf-8")
        data.add(self.cfg, self.report_id, str(spare))
        self.append_to_main(
            '\n#srctable("/reports/2026-08-18-numbers/data/spare.csv",'
            " source: [@data-spare])\n"
        )
        self.assertNotIn("W005", self.codes())

    # ── W006

    def test_w006_fires_when_the_table_cites_the_wrong_entry(self) -> None:
        self.register()
        sources.append(
            self.report.sources,
            sources.Source(key="some-page", fields={"type": "Web", "title": "A page"}),
        )
        main = self.folder / "main.typ"
        main.write_text(
            main.read_text(encoding="utf-8").replace("@data-prices", "@some-page"),
            encoding="utf-8",
        )
        self.assertIn("W006", self.codes())
        self.assertIn("@data-prices", self.message("W006"))

    def test_w006_fires_when_the_file_was_never_registered(self) -> None:
        main = self.folder / "main.typ"
        main.write_text(
            main.read_text(encoding="utf-8").replace("@data-prices", "@some-page"),
            encoding="utf-8",
        )
        self.assertIn("W006", self.codes())
        self.assertIn("data add", self.message("W006"))

    def test_w006_leaves_an_undefined_key_to_the_citation_rule(self) -> None:
        # Nothing is registered, but the table cites the key the file would get.
        # That is a bibliography with a missing entry, which is check.py's E006 —
        # saying it twice in two different words helps nobody.
        self.assertNotIn("W006", self.codes())

    def test_w006_stays_silent_when_the_table_cites_its_own_data(self) -> None:
        self.register()
        self.assertNotIn("W006", self.codes())

    def test_w006_stays_silent_when_the_data_is_cited_alongside_a_page(self) -> None:
        self.register()
        main = self.folder / "main.typ"
        main.write_text(
            main.read_text(encoding="utf-8").replace(
                "source: [@data-prices]", "source: [@data-prices @some-page]"
            ),
            encoding="utf-8",
        )
        self.assertNotIn("W006", self.codes())


class Reporting(VaultCase):
    def test_findings_become_whatever_check_findings_are(self) -> None:
        from engine import check

        self.register()
        self.prices.write_text(PRICES.replace("$19.00", "$21.00"), encoding="utf-8")
        converted = data.to_findings(data.check(self.cfg))
        self.assertEqual([f.code for f in converted], ["E011"])
        for finding in converted:
            self.assertIsInstance(finding, check.Finding)
            self.assertIn(finding.level, ("error", "warning"))
            self.assertTrue(finding.message)

    def test_json_paths_are_vault_relative(self) -> None:
        self.register()
        rows = data.to_json(data.scan(self.report), root=self.root)
        self.assertEqual(rows[0]["path"], f"reports/{self.report_id}/data/prices.csv")
        self.assertEqual(rows[0]["rows"], 3)
        self.assertEqual(rows[0]["headers"][0], "Plan")

        records = data.findings_json(data.check(self.cfg), root=self.root)
        self.assertTrue(all(not row["path"].startswith("/") for row in records))

    def test_check_puts_errors_first(self) -> None:
        self.register()
        self.prices.write_text(PRICES.replace("$19.00", "$21.00"), encoding="utf-8")
        (self.folder / "data" / "spare.csv").write_text(SEMICOLONS, encoding="utf-8")
        levels = [record[0] for record in data.check(self.cfg)]
        self.assertEqual(levels, sorted(levels, key=lambda l: l != "error"))


if __name__ == "__main__":
    unittest.main()
