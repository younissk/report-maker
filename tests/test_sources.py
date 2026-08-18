"""Bibliography tests.

Two properties matter here and nothing else really does.

The first is that reading never fails. Every downstream feature — the linter,
the sources panel, snapshots, the evidence rail — starts by parsing
`sources.yml`, so a parser that throws on a half-written file takes the whole
tool down at exactly the moment a person is editing. The malformed cases below
are therefore not edge cases; they are the normal state of a file being written.

The second is that writing keeps its hands to itself. `upsert` and `remove` are
asserted against the exact bytes either side of the block they touch, because
"preserves comments" is the kind of claim that quietly stops being true.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import check, sources  # noqa: E402
from engine.config import Config  # noqa: E402
from engine.workspace import Report  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "examples/demo-vault/reports/examples/2026-08-16-example/sources.yml"

# Three entries, a header comment, a comment introducing one of them, and blank
# lines between all of it — the shape a hand-maintained bibliography actually has.
ANNOTATED = """# Hayagriva bibliography for this report.
#
# Add the key before writing the sentence that needs it.

alpha:
  type: Web
  title: "Alpha"

# beta is the one the whole section rests on — do not remove it
beta:
  type: Web
  title: "Beta"
  url:
    value: https://example.com/beta
    date: 2026-02-02

gamma:
  type: Misc
  title: "Gamma"
"""


class Parsing(unittest.TestCase):
    def test_the_demo_vault_bibliography(self) -> None:
        # The fixture is the real demo bibliography rather than a literal, so the
        # parser is exercised against a file somebody actually maintains — header
        # comments, blank lines between entries, a nested url/date pair, and an
        # entry `data add` wrote rather than a human.
        parsed = sources.parse(DEMO)
        self.assertEqual(
            [s.key for s in parsed],
            ["repository", "house-rule", "rule-inventory", "data-rule-coverage"],
        )

        page, measurement = parsed[0], parsed[1]
        self.assertEqual(page.line, 15)
        self.assertEqual(page.type, "Web")
        self.assertEqual(page.title, "report-maker — the engine this vault is built by")
        self.assertEqual(page.author, "Youniss Kandah")
        self.assertEqual(page.url, "https://github.com/younissk/report-maker")
        self.assertEqual(page.accessed, "2026-08-16")

        # Nesting survives: `url` is the value/date pair, not a flattened string.
        self.assertEqual(
            page.fields["url"],
            {
                "value": "https://github.com/younissk/report-maker",
                "date": "2026-08-16",
            },
        )

        self.assertEqual(measurement.line, 23)
        self.assertEqual(measurement.type, "Misc")
        self.assertIsNone(measurement.url)
        self.assertEqual(measurement.accessed, "2026-08-16")  # from the top-level date

    def test_the_key_set_agrees_with_the_linter(self) -> None:
        # check.py may delegate bib_keys to this module, so the two must see the
        # same bibliography or the linter starts inventing E006s.
        self.assertEqual(sources.keys(DEMO), check.bib_keys(DEMO))

    def test_a_missing_file_is_an_empty_bibliography(self) -> None:
        self.assertEqual(sources.parse(DEMO.parent / "nope.yml"), [])
        self.assertEqual(sources.keys(DEMO.parent / "nope.yml"), set())

    def test_type_defaults_to_misc(self) -> None:
        source = sources.parse_text('k:\n  title: "T"\n')[0]
        self.assertEqual(source.type, "Misc")
        self.assertEqual(source.author, "")

    def test_quoting_styles_and_comments(self) -> None:
        source = sources.parse_text(
            "k:\n"
            "  type: Web            # the kind\n"
            "  # a note about the title\n"
            "  title: \"Has a # hash inside\"\n"
            "  note: 'It''s quoted'\n"
            "  url: https://example.com/p#frag\n"
        )[0]
        self.assertEqual(source.type, "Web")
        self.assertEqual(source.title, "Has a # hash inside")
        self.assertEqual(source.fields["note"], "It's quoted")
        # A `#` with no space before it is part of the URL, not a comment.
        self.assertEqual(source.url, "https://example.com/p#frag")

    def test_sequences_of_scalars_and_of_mappings(self) -> None:
        source = sources.parse_text(
            "k:\n"
            "  author:\n"
            "    - name: Lovelace\n"
            "      given-name: Ada\n"
            "    - Institute of Things\n"
        )[0]
        self.assertEqual(
            source.fields["author"],
            [{"name": "Lovelace", "given-name": "Ada"}, "Institute of Things"],
        )
        self.assertEqual(source.author, "Ada Lovelace, Institute of Things")

    def test_nesting_to_any_depth(self) -> None:
        source = sources.parse_text(
            "k:\n  parent:\n    title: \"P\"\n    issue:\n      volume: 3\n"
        )[0]
        self.assertEqual(source.fields["parent"]["issue"], {"volume": "3"})


class Degradation(unittest.TestCase):
    """A file being edited is a file that does not parse. It must still load."""

    MALFORMED = """good:
  type: Web

broken:
  this line has no colon
  - a dangling sequence item
  "just a string"

after:
  type: Misc
  title: "unterminated
"""

    def test_a_broken_block_keeps_its_key_and_line(self) -> None:
        parsed = sources.parse_text(self.MALFORMED)
        found = {s.key: s for s in parsed}
        self.assertEqual([s.key for s in parsed], ["good", "broken", "after"])
        self.assertEqual(found["broken"].line, 4)
        self.assertEqual(found["broken"].fields, {})
        self.assertEqual(found["broken"].type, "Misc")

    def test_a_broken_block_does_not_swallow_the_next_one(self) -> None:
        # The failure that matters: one bad entry hiding every entry after it,
        # so the linter reports E006 on citations that are perfectly fine.
        found = {s.key: s for s in sources.parse_text(self.MALFORMED)}
        self.assertEqual(found["after"].line, 9)
        self.assertEqual(found["after"].type, "Misc")
        self.assertEqual(found["after"].title, "unterminated")  # ran off the quote

    def test_nothing_raises_on_anything(self) -> None:
        for text in (
            "",
            "\n\n\n",
            "# only a comment\n",
            "k:\n\tbroken: tab indent\n",
            "k:\n      deep: 1\n  shallow: 2\n",
            "k:\n  a: [flow, sequence]\n  b: {flow: mapping}\n",
            "k:",
            ":::\n",
            "k:\n  url:\n",
        ):
            with self.subTest(text=text):
                sources.parse_text(text)  # the assertion is that this returns


class Emitting(unittest.TestCase):
    def test_every_entry_survives_a_round_trip_through_yaml(self) -> None:
        for source in sources.parse(DEMO) + sources.parse_text(ANNOTATED):
            with self.subTest(key=source.key):
                back = sources.parse_text(source.to_yaml())
                self.assertEqual([s.key for s in back], [source.key])
                self.assertEqual(back[0].fields, source.fields)

    def test_strings_are_double_quoted_at_a_two_space_indent(self) -> None:
        emitted = sources.Source(
            "k",
            {
                "type": "Web",
                "title": 'A "quoted" title',
                "url": sources.url_field("https://example.com/p", "2026-08-18"),
            },
        ).to_yaml()
        self.assertEqual(
            emitted,
            "k:\n"
            '  type: "Web"\n'
            '  title: "A \\"quoted\\" title"\n'
            "  url:\n"
            '    value: "https://example.com/p"\n'
            '    date: "2026-08-18"\n',
        )

    def test_a_url_with_no_access_date_stays_a_bare_string(self) -> None:
        self.assertEqual(sources.url_field("https://example.com"), "https://example.com")

    def test_a_field_with_nothing_in_it_is_left_out(self) -> None:
        # `cite` builds fields from whatever the page happened to expose, so
        # every one of these is a real shape reaching the emitter.
        emitted = sources.Source(
            "k",
            {
                "type": "Web",
                "author": None,
                "note": [],
                "parent": {},
                "editor": [{"name": None}],
            },
        ).to_yaml()
        self.assertEqual(emitted, 'k:\n  type: "Web"\n')
        self.assertEqual(sources.parse_text(emitted)[0].fields, {"type": "Web"})

    def test_an_unreadable_block_is_written_back_verbatim(self) -> None:
        # There is nothing to regenerate from, so the bytes it arrived with are
        # the only honest thing to write.
        source = sources.parse_text("k:\n  not yaml at all\n")[0]
        self.assertEqual(source.fields, {})
        self.assertEqual(source.to_yaml(), "k:\n  not yaml at all\n")


class Writing(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "sources.yml"
        self.path.write_text(ANNOTATED, encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def around(self, key: str) -> tuple[str, str]:
        """The bytes before and after one block — what must not change."""
        text = self.path.read_text(encoding="utf-8")
        block = next(s for s in sources.parse_text(text) if s.key == key)
        start = text.index(block.raw)
        return text[:start], text[start + len(block.raw) :]

    def test_append_is_idempotent(self) -> None:
        # `cite` on the same URL twice must not grow a duplicate entry.
        sources.append(self.path, sources.Source("beta", {"type": "Report"}))
        self.assertEqual(self.path.read_text(encoding="utf-8"), ANNOTATED)

    def test_append_adds_at_the_end_after_a_blank_line(self) -> None:
        sources.append(self.path, sources.Source("delta", {"type": "Web"}))
        text = self.path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(ANNOTATED))
        self.assertEqual(text[len(ANNOTATED) :], '\ndelta:\n  type: "Web"\n')
        self.assertEqual([s.key for s in sources.parse(self.path)],
                         ["alpha", "beta", "gamma", "delta"])

    def test_append_creates_the_file_when_there_is_none(self) -> None:
        fresh = self.path.parent / "new" / "sources.yml"
        sources.append(fresh, sources.Source("k", {"type": "Web"}))
        self.assertEqual(fresh.read_text(encoding="utf-8"), 'k:\n  type: "Web"\n')

    def test_upsert_replaces_in_place_and_touches_nothing_else(self) -> None:
        prefix, suffix = self.around("beta")
        replacement = sources.Source("beta", {"type": "Report", "title": "Beta II"})
        sources.upsert(self.path, replacement)

        after = self.path.read_text(encoding="utf-8")
        self.assertEqual(after, prefix + replacement.to_yaml() + suffix)
        # Named explicitly, because "preserves comments" is the claim that rots.
        self.assertIn(
            "# beta is the one the whole section rests on — do not remove it", after
        )
        self.assertIn("# Hayagriva bibliography for this report.", after)
        self.assertEqual([s.key for s in sources.parse(self.path)],
                         ["alpha", "beta", "gamma"])
        self.assertEqual(sources.parse(self.path)[1].title, "Beta II")

    def test_upsert_of_an_unknown_key_appends(self) -> None:
        sources.upsert(self.path, sources.Source("delta", {"type": "Web"}))
        self.assertEqual([s.key for s in sources.parse(self.path)],
                         ["alpha", "beta", "gamma", "delta"])

    def test_remove_deletes_the_block_and_only_the_block(self) -> None:
        prefix, suffix = self.around("beta")
        self.assertTrue(sources.remove(self.path, "beta"))
        self.assertEqual(self.path.read_text(encoding="utf-8"), prefix + suffix)
        self.assertEqual([s.key for s in sources.parse(self.path)], ["alpha", "gamma"])
        # The comment above it stays: guessing which comments belong to an entry
        # is how a rewriter starts deleting other people's prose.
        self.assertIn("do not remove it", self.path.read_text(encoding="utf-8"))

    def test_remove_of_an_absent_key_changes_nothing(self) -> None:
        self.assertFalse(sources.remove(self.path, "nope"))
        self.assertEqual(self.path.read_text(encoding="utf-8"), ANNOTATED)

    def test_the_last_block_in_a_file_is_bounded_correctly(self) -> None:
        # No trailing newline, nothing following it — the case an off-by-one in
        # the block scanner would take out.
        self.path.write_text("alpha:\n  type: Web\n\nomega:\n  type: Misc", encoding="utf-8")
        self.assertTrue(sources.remove(self.path, "omega"))
        self.assertEqual(self.path.read_text(encoding="utf-8"), "alpha:\n  type: Web\n\n")


class Keys(unittest.TestCase):
    def test_the_site_leads_and_the_title_follows(self) -> None:
        self.assertEqual(
            sources.slugify_key("Pricing", "https://docs.stripe.com/billing", set()),
            "stripe-pricing",
        )
        self.assertEqual(
            sources.slugify_key("The state of the market in 2026", None, set()),
            "state-market-2026",
        )

    def test_collisions_number_upwards(self) -> None:
        taken: set[str] = set()
        for expected in ("stripe-pricing", "stripe-pricing-2", "stripe-pricing-3"):
            key = sources.slugify_key("Pricing", "https://stripe.com/p", taken)
            self.assertEqual(key, expected)
            taken.add(key)
        # A gap in the numbering is filled, not skipped past.
        taken.discard("stripe-pricing-2")
        self.assertEqual(
            sources.slugify_key("Pricing", "https://stripe.com/p", taken),
            "stripe-pricing-2",
        )

    def test_a_key_is_always_a_usable_typst_reference(self) -> None:
        for title, url in [
            ("2026 annual review", None),
            ("", None),
            ("!!!", "https://example.com"),
            ("", "not a url"),
            ("Ünïcödé — dashes & things", "https://example.co.uk/a"),
        ]:
            key = sources.slugify_key(title, url, set())
            with self.subTest(key=key):
                self.assertRegex(key, r"^[A-Za-z][\w.:+-]*$")
                # `check.cited_keys` must read the whole key back out of `@key`.
                self.assertEqual(check.cited_keys(f"a fact @{key} here"), [(key, 7)])

    def test_a_second_level_domain_is_not_an_identity(self) -> None:
        self.assertEqual(sources.slugify_key("", "https://example.co.uk/a", set()), "example")
        self.assertEqual(sources.slugify_key("", "https://www.example.com", set()), "example")


class Json(unittest.TestCase):
    def test_the_source_row_shape(self) -> None:
        rows = sources.to_json(
            sources.parse(DEMO),
            uses={"repository": 3},
            snapshots={
                "repository": {
                    "sha256": "abc123",
                    "fetched": "2026-08-18T10:00:00Z",
                    "bytes": 4096,  # the caller's record carries more; only two survive
                }
            },
        )
        self.assertEqual(
            rows[0],
            {
                "key": "repository",
                "type": "Web",
                "title": "report-maker — the engine this vault is built by",
                "author": "Youniss Kandah",
                "url": "https://github.com/younissk/report-maker",
                "accessed": "2026-08-16",
                "line": 15,
                "snapshot": {"sha256": "abc123", "fetched": "2026-08-18T10:00:00Z"},
                "uses": 3,
            },
        )
        # An orphan: in References because it was reviewed, cited by nothing.
        self.assertEqual(rows[1]["uses"], 0)
        self.assertIsNone(rows[1]["snapshot"])
        self.assertIsNone(rows[1]["url"])

    def test_use_counts_ignore_cross_references(self) -> None:
        # `@fig-one` points at a figure in this document. Counting it as a use of
        # a source would hide the orphan the W001 warning exists to surface.
        report = self.write_report(
            "r",
            'A fact @alpha and a fact @alpha again.\n'
            "See @fig-one.\n"
            "#srcfig(table([a]), caption: [c], source: [@alpha]) <fig-one>\n"
            "// @alpha in a comment does not count\n",
            'alpha:\n  type: Web\n\nbeta:\n  type: Web\n',
        )
        self.assertEqual(sources.use_counts(report), {"alpha": 3, "beta": 0})
        self.assertEqual(
            [(row["key"], row["uses"]) for row in sources.rows(report)],
            [("alpha", 3), ("beta", 0)],
        )

    def write_report(self, rid: str, main: str, bibliography: str) -> Report:
        folder = Path(self.tmp.name) / rid
        folder.mkdir(parents=True)
        (folder / "main.typ").write_text(main, encoding="utf-8")
        (folder / "sources.yml").write_text(bibliography, encoding="utf-8")
        return Report(id=rid, folder=folder, cfg=Config(root=Path(self.tmp.name)))

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
