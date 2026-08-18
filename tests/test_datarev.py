"""Data revision tests.

One property carries this feature, and it is the mirror of the one `test_data`
carries. There, a report's numbers must not be able to change without the build
saying so. Here, a report's numbers must not be able to change *at all* without
the version they are replacing surviving on disk — because the sanctioned way
through E011 has to be safer than the unsanctioned one, or people will take the
unsanctioned one.

So the tests below are mostly about loss. A revision overwritten by a second edit
on the same day, a `sources.yml` reformatted around the entry that moved, a
neighbouring entry's comment eaten by the rewriter, a dated revision read back as
a second data file and dragged into the bibliography — each of those is silent,
and each of them makes the archive a worse record than the git history it was
supposed to be more precise than.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import data, datarev, sources  # noqa: E402
from engine.config import load  # noqa: E402

VAULT_TOML = """[vault]
reports = "reports"
"""

PRICES = """Plan,Monthly,Annual,Seats
Starter,19,190,3
Team,49,490,10
Business,149,1490,50
"""

# One row more than PRICES, so a delta is a number and not a shrug.
PRICES_EDITED = """Plan,Monthly,Annual,Seats
Starter,19,190,3
Team,49,490,10
Business,149,1490,50
Enterprise,499,4990,250
"""

PRICES_AGAIN = """Plan,Monthly,Annual,Seats
Starter,21,210,3
"""

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
    """A real vault on disk. Every assertion here is about files."""

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

    def register(self) -> data.DataFile:
        return data.add(self.cfg, self.report_id, str(self.prices))

    def edit(self, text: str) -> None:
        self.prices.write_text(text, encoding="utf-8")

    def dated(self, path: Path, date: str) -> None:
        """Force a file's modification date, so a test can talk about days.

        The revision filename is taken from the mtime, so this is the only way to
        write a test about "yesterday" that does not involve waiting.
        """
        stamp = dt.datetime.fromisoformat(f"{date}T12:00:00").timestamp()
        os.utime(path, (stamp, stamp))

    def names(self) -> list[str]:
        return sorted(p.name for p in (self.folder / "data").iterdir())


# ── the archive ──────────────────────────────────────────────────────────────


class Archiving(VaultCase):
    def test_archive_then_edit_keeps_the_old_bytes(self) -> None:
        kept = datarev.archive(self.report, self.prices)
        self.assertIsNotNone(kept)
        self.edit(PRICES_EDITED)

        self.assertEqual(kept.read_text(encoding="utf-8"), PRICES)
        self.assertEqual(self.prices.read_text(encoding="utf-8"), PRICES_EDITED)

        found = datarev.revisions(self.report, self.prices)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].sha256, data.sha_of(kept))
        self.assertEqual(found[0].rows, 3)
        self.assertEqual(found[0].columns, 4)
        self.assertEqual(found[0].rel, f"data/{kept.name}")

    def test_the_revision_is_named_for_the_date_the_file_was_written(self) -> None:
        self.dated(self.prices, "2026-03-04")
        kept = datarev.archive(self.report, self.prices)
        self.assertEqual(kept.name, "prices.2026-03-04.csv")
        self.assertEqual(datarev.revisions(self.report, self.prices)[0].date, "2026-03-04")

    def test_identical_content_does_not_archive_twice(self) -> None:
        first = datarev.archive(self.report, self.prices)
        self.assertIsNotNone(first)
        self.assertIsNone(datarev.archive(self.report, self.prices))
        self.assertEqual(len(datarev.revisions(self.report, self.prices)), 1)

    def test_two_edits_in_one_day_both_survive(self) -> None:
        self.dated(self.prices, "2026-03-04")
        first = datarev.archive(self.report, self.prices)

        self.edit(PRICES_EDITED)
        self.dated(self.prices, "2026-03-04")
        second = datarev.archive(self.report, self.prices)

        self.assertEqual(first.name, "prices.2026-03-04.csv")
        self.assertEqual(second.name, "prices.2026-03-04-2.csv")
        self.assertEqual(first.read_text(encoding="utf-8"), PRICES)
        self.assertEqual(second.read_text(encoding="utf-8"), PRICES_EDITED)

        self.edit(PRICES_AGAIN)
        self.dated(self.prices, "2026-03-04")
        third = datarev.archive(self.report, self.prices)
        self.assertEqual(third.name, "prices.2026-03-04-3.csv")
        # Nothing was clobbered on the way.
        self.assertEqual(first.read_text(encoding="utf-8"), PRICES)
        self.assertEqual(second.read_text(encoding="utf-8"), PRICES_EDITED)

    def test_revisions_come_back_newest_first(self) -> None:
        self.dated(self.prices, "2026-01-02")
        datarev.archive(self.report, self.prices)
        self.edit(PRICES_EDITED)
        self.dated(self.prices, "2026-05-06")
        datarev.archive(self.report, self.prices)
        self.edit(PRICES_AGAIN)
        self.dated(self.prices, "2026-05-06")
        datarev.archive(self.report, self.prices)

        found = datarev.revisions(self.report, self.prices)
        self.assertEqual(
            [r.path.name for r in found],
            ["prices.2026-05-06-2.csv", "prices.2026-05-06.csv", "prices.2026-01-02.csv"],
        )

    def test_a_file_outside_the_report_is_refused(self) -> None:
        stray = self.root / "elsewhere.csv"
        stray.write_text(PRICES, encoding="utf-8")
        with self.assertRaises(data.DataError) as caught:
            datarev.archive(self.report, stray)
        self.assertIn("data add", str(caught.exception))

    def test_a_missing_file_is_refused_by_name(self) -> None:
        with self.assertRaises(data.DataError):
            datarev.archive(self.report, "no-such.csv")

    def test_revisions_of_a_deleted_file_are_still_listed(self) -> None:
        self.dated(self.prices, "2026-03-04")
        datarev.archive(self.report, self.prices)
        self.prices.unlink()
        found = datarev.revisions(self.report, "prices.csv")
        self.assertEqual([r.date for r in found], ["2026-03-04"])


class Naming(VaultCase):
    def test_is_revision_recognises_the_dated_form(self) -> None:
        for name in ("prices.2026-08-18.csv", "prices.2026-08-18-2.tsv", "q1.2026-01-01-11.tab"):
            self.assertTrue(datarev.is_revision(name), name)

    def test_is_revision_leaves_ordinary_names_alone(self) -> None:
        for name in ("prices.csv", "2026-08-18.csv", "prices.v2.csv", "prices.2026-08.csv"):
            self.assertFalse(datarev.is_revision(name), name)


# ── the bibliography ─────────────────────────────────────────────────────────


NEIGHBOURS = '''# The pricing page, archived on the day of the audit.
acme-pricing:
  type: Web
  title: "Acme — Pricing"
  author: "Acme Ltd"
  url:
    value: "https://acme.example/pricing"
    date: "2026-08-01"

data-prices:
  type: Misc
  title: "Prices — data file"
  author: "own data"
  date: "{date}"
  note: "{note}"

# Kept because it was reviewed, not because anything rests on it.
own-measurement:
  type: Misc
  title: "Latency measurement"
  author: "own data"
  date: "2026-08-02"
'''


class Reregistering(VaultCase):
    def seed(self) -> str:
        """Register the file, then hand-write a `sources.yml` around it.

        Hand-written on purpose: the entry this module rewrites has to survive
        sitting between a comment, a nested `url:` mapping and another entry,
        which is what a real bibliography looks like and what a naive rewriter
        eats.
        """
        described = data.describe(self.prices, report=self.report)
        text = NEIGHBOURS.format(date=described.date, note=described.note)
        (self.folder / "sources.yml").write_text(text, encoding="utf-8")
        return text

    def test_summary_reports_the_row_delta(self) -> None:
        self.register()
        before = data.describe(self.prices, report=self.report).sha256
        datarev.archive(self.report, self.prices)
        self.edit(PRICES_EDITED)

        summary = datarev.reregister(self.report, self.prices)
        self.assertEqual(summary["key"], "data-prices")
        self.assertEqual(summary["old_sha"], before)
        self.assertEqual(summary["new_sha"], data.sha_of(self.prices))
        self.assertEqual(summary["rows_before"], 3)
        self.assertEqual(summary["rows_after"], 4)
        self.assertEqual(summary["delta"], 1)
        self.assertEqual(summary["headline"], "3 rows → 4 rows, +1")

    def test_a_shrinking_file_reports_a_negative_delta(self) -> None:
        self.register()
        self.edit(PRICES_AGAIN)
        summary = datarev.reregister(self.report, self.prices)
        self.assertEqual(summary["delta"], -2)
        self.assertEqual(summary["headline"], "3 rows → 1 rows, -2")

    def test_it_clears_e011(self) -> None:
        self.register()
        self.edit(PRICES_EDITED)
        self.assertIn("E011", [record[1] for record in data.findings(self.report)])
        datarev.reregister(self.report, self.prices)
        self.assertNotIn("E011", [record[1] for record in data.findings(self.report)])

    def test_it_registers_a_file_nobody_registered(self) -> None:
        summary = datarev.reregister(self.report, self.prices)
        self.assertIsNone(summary["old_sha"])
        self.assertIsNone(summary["rows_before"])
        self.assertIsNone(summary["delta"])
        self.assertEqual(summary["headline"], "registered at 3 rows")
        self.assertEqual(
            data.registry(self.report)["data/prices.csv"].sha256,
            data.sha_of(self.prices),
        )

    def test_it_leaves_neighbouring_entries_byte_for_byte(self) -> None:
        text = self.seed()
        head = text.split("data-prices:")[0]
        tail = text.split('  date: "2026-08-02"\n')[-1]

        self.edit(PRICES_EDITED)
        datarev.reregister(self.report, self.prices)

        after = (self.folder / "sources.yml").read_text(encoding="utf-8")
        self.assertTrue(after.startswith(head), "the comment and the entry above it moved")
        self.assertIn("# Kept because it was reviewed", after)
        self.assertIn('  url:\n    value: "https://acme.example/pricing"', after)
        self.assertTrue(after.endswith(tail))
        # The entry that moved is the only one that moved.
        self.assertEqual(
            [source.key for source in sources.parse(self.folder / "sources.yml")],
            ["acme-pricing", "data-prices", "own-measurement"],
        )

    def test_it_updates_the_entry_in_place_rather_than_appending(self) -> None:
        self.seed()
        self.edit(PRICES_EDITED)
        datarev.reregister(self.report, self.prices)
        parsed = sources.parse(self.folder / "sources.yml")
        self.assertEqual(len([s for s in parsed if s.key == "data-prices"]), 1)
        entry = next(s for s in parsed if s.key == "data-prices")
        self.assertIn(f"sha256:{data.sha_of(self.prices)}", entry.fields["note"])
        self.assertTrue(entry.fields["note"].endswith("data/prices.csv"))

    def test_it_keeps_a_title_somebody_chose(self) -> None:
        self.seed()
        text = (self.folder / "sources.yml").read_text(encoding="utf-8")
        (self.folder / "sources.yml").write_text(
            text.replace('"Prices — data file"', '"Published list prices, Q3"'),
            encoding="utf-8",
        )
        self.edit(PRICES_EDITED)
        datarev.reregister(self.report, self.prices)
        entry = next(
            s for s in sources.parse(self.folder / "sources.yml") if s.key == "data-prices"
        )
        self.assertEqual(entry.title, "Published list prices, Q3")

    def test_a_note_is_spliced_in_ahead_of_the_path(self) -> None:
        self.register()
        self.edit(PRICES_EDITED)
        summary = datarev.reregister(
            self.report, self.prices, note="Added the enterprise tier."
        )
        entry = next(
            s for s in sources.parse(self.folder / "sources.yml") if s.key == "data-prices"
        )
        note = entry.fields["note"]
        self.assertIn("Added the enterprise tier.", note)
        # The path stays last and the checksum stays first, or `data.registry`
        # and E011 stop seeing this entry at all.
        self.assertTrue(note.endswith("data/prices.csv"))
        self.assertTrue(note.startswith("sha256:"))
        self.assertEqual(
            data.registry(self.report)["data/prices.csv"].sha256, summary["new_sha"]
        )

    def test_a_multiline_note_is_flattened(self) -> None:
        self.register()
        datarev.reregister(self.report, self.prices, note="one\n  two\n")
        entry = next(
            s for s in sources.parse(self.folder / "sources.yml") if s.key == "data-prices"
        )
        self.assertIn("one two", entry.fields["note"])
        self.assertTrue(entry.fields["note"].endswith("data/prices.csv"))

    def test_it_bumps_the_date(self) -> None:
        self.register()
        self.dated(self.prices, "2026-01-02")
        datarev.reregister(self.report, self.prices)
        entry = next(
            s for s in sources.parse(self.folder / "sources.yml") if s.key == "data-prices"
        )
        self.assertEqual(entry.fields["date"], "2026-01-02")

    def test_it_leaves_a_dated_copy_of_what_it_registered(self) -> None:
        self.register()
        self.dated(self.prices, "2026-01-02")
        summary = datarev.reregister(self.report, self.prices)
        self.assertEqual(summary["archived"], "data/prices.2026-01-02.csv")
        self.assertTrue((self.folder / "data" / "prices.2026-01-02.csv").is_file())

    def test_reregistering_twice_with_no_edit_keeps_one_revision(self) -> None:
        self.register()
        datarev.reregister(self.report, self.prices)
        datarev.reregister(self.report, self.prices)
        self.assertEqual(len(datarev.revisions(self.report, self.prices)), 1)

    def test_main_typ_is_never_touched(self) -> None:
        self.register()
        before = (self.folder / "main.typ").read_bytes()
        self.edit(PRICES_EDITED)
        datarev.reregister(self.report, self.prices, note="the numbers moved")
        self.assertEqual((self.folder / "main.typ").read_bytes(), before)


# ── status ───────────────────────────────────────────────────────────────────


class Status(VaultCase):
    def test_before_and_after_an_edit(self) -> None:
        self.register()
        before = datarev.status(self.report, self.prices)
        self.assertTrue(before["registered"])
        self.assertTrue(before["matches"])
        self.assertEqual(before["current_sha"], before["recorded_sha"])
        self.assertEqual(before["revisions"], [])

        datarev.archive(self.report, self.prices)
        self.edit(PRICES_EDITED)
        during = datarev.status(self.report, self.prices)
        self.assertTrue(during["registered"])
        self.assertFalse(during["matches"])
        self.assertNotEqual(during["current_sha"], during["recorded_sha"])
        self.assertEqual(len(during["revisions"]), 1)

        datarev.reregister(self.report, self.prices)
        after = datarev.status(self.report, self.prices)
        self.assertTrue(after["matches"])
        self.assertEqual(after["rows"], 4)
        self.assertEqual(len(after["revisions"]), 2)

    def test_an_unregistered_file_does_not_read_as_matching(self) -> None:
        found = datarev.status(self.report, self.prices)
        self.assertFalse(found["registered"])
        self.assertFalse(found["matches"])
        self.assertIsNone(found["recorded_sha"])
        self.assertIsNotNone(found["current_sha"])

    def test_a_deleted_file_reports_its_history(self) -> None:
        self.register()
        self.dated(self.prices, "2026-03-04")
        datarev.archive(self.report, self.prices)
        self.prices.unlink()
        found = datarev.status(self.report, "prices.csv")
        self.assertFalse(found["exists"])
        self.assertIsNone(found["current_sha"])
        self.assertFalse(found["matches"])
        self.assertEqual(len(found["revisions"]), 1)


# ── the rest of the engine must not see revisions ────────────────────────────


class Invisible(VaultCase):
    """A revision is history, not a second data file.

    If `data.scan` picks one up, every archived version becomes a W005 ("no
    srctable reads this"), an unregistered file the linter nags about, and a
    candidate for its own bibliography entry. The archive would then make the
    report *harder* to check the more carefully it was kept, which is the exact
    inversion this module must not cause.
    """

    def test_dated_revisions_are_invisible_to_data_scan(self) -> None:
        self.register()
        self.dated(self.prices, "2026-03-04")
        datarev.archive(self.report, self.prices)
        self.edit(PRICES_EDITED)
        datarev.reregister(self.report, self.prices)

        seen = [datafile.rel for datafile in data.scan(self.report)]
        self.assertEqual(
            seen,
            ["data/prices.csv"],
            "data.paths must skip dated revisions — filter it with "
            "`datarev.is_revision(path)`",
        )

    def test_no_data_rule_fires_on_a_revision(self) -> None:
        self.register()
        datarev.archive(self.report, self.prices)
        self.edit(PRICES_EDITED)
        datarev.reregister(self.report, self.prices)
        paths = {Path(record[2]).name for record in data.findings(self.report)}
        self.assertFalse(
            {name for name in paths if datarev.is_revision(name)},
            "a dated revision must never be the subject of a data finding",
        )


if __name__ == "__main__":
    unittest.main()
