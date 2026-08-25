"""A cite must never leave a report that cannot build.

`cite` is a convenience command: it fetches a page, extracts what the page says
about itself, and writes a bibliography entry. A page announces its publication
date in whatever shape its CMS favours, and Hayagriva accepts only `YYYY`,
`YYYY-MM` and `YYYY-MM-DD`. Writing the raw string through means a successful
cite can hand back a `sources.yml` that Hayagriva refuses, and the report stops
compiling for a reason the author did not cause and cannot easily see.

Found by citing an MDN page through the web version: the entry carried
`date: "2026-03-22T23:36:38.000Z"` and the next build died with
`date format unknown at line 34 column 9`.
"""

from __future__ import annotations

import unittest

from engine.cite import _fields, _iso_date


class NormalisingADate(unittest.TestCase):
    def test_an_iso_datetime_becomes_a_date(self):
        # The shape MDN and most modern CMSs emit, and the one that broke a build.
        self.assertEqual(_iso_date("2026-03-22T23:36:38.000Z"), "2026-03-22")

    def test_a_plain_date_is_left_alone(self):
        self.assertEqual(_iso_date("2026-03-22"), "2026-03-22")

    def test_a_year_and_month_survive(self):
        self.assertEqual(_iso_date("2026-03"), "2026-03")

    def test_a_bare_year_survives(self):
        self.assertEqual(_iso_date("2026"), "2026")

    def test_an_rfc_2822_line_is_parsed(self):
        # What an RSS-descended feed hands over.
        self.assertEqual(_iso_date("Tue, 22 Mar 2026 23:36:38 GMT"), "2026-03-22")

    def test_what_cannot_be_read_is_dropped_rather_than_guessed(self):
        # A missing date costs a reader nothing. An invented one is the small
        # fabrication this tool exists to refuse.
        for value in ("", "garbage", "sometime last spring", "n/a"):
            with self.subTest(value=value):
                self.assertIsNone(_iso_date(value))


class TheEntryItWrites(unittest.TestCase):
    def _entry(self, published):
        return _fields(
            "https://example.com/page",
            {"title": "A page", "published": published},
            "text/html",
            None,
            "2026-08-25",
        )

    def test_an_unparseable_date_leaves_no_date_field(self):
        # The entry must still be written — losing the citation because its date
        # was odd would be a worse failure than losing the date.
        entry = self._entry("sometime last spring")
        self.assertNotIn("date", entry)
        self.assertEqual(entry["title"], "A page")

    def test_a_datetime_reaches_the_entry_as_a_date(self):
        self.assertEqual(self._entry("2026-03-22T23:36:38.000Z")["date"], "2026-03-22")


if __name__ == "__main__":
    unittest.main()
