"""Quote-level provenance tests.

A citation says a page exists. A quotation says the page *said this*, and until
`check` compares it against the archived copy that is a claim about the world
resting on nothing but the writer's copy-and-paste. These tests are therefore
about the one rule in the tool that can catch a sentence which already looks
sourced.

Three properties, in order of how badly they would hurt if they broke:

  - a quote the snapshot does not contain is an error (E009), and the error is
    useful — it shows the nearest thing the page did say, so the fix is a
    correction rather than an investigation;
  - a quote the snapshot does contain is *not* an error, however differently the
    page typeset its quotes, dashes, capitals and line breaks;
  - a report with no archive at all is silent. Every existing vault is in that
    state, and a linter that lights all of them up is a linter people turn off.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import check  # noqa: E402
from engine.config import Config, load  # noqa: E402
from engine.workspace import Report  # noqa: E402

HEAD = '#show: report.with(\n  title: "T",\n  sources: "/reports/r/sources.yml",\n)\n'

BIB = 'example-page:\n  type: Web\n  title: "The example page"\n'

# What the archived page says, with the typography a real page has: curly
# quotes, an em dash, and a line break in the middle of the sentence.
PAGE = """Example Ltd — pricing

The team said, “we cut onboarding time by 40 per cent — measured
across the first quarter”, and gave no further detail.

Contact us.
"""

# The same words, typed into a report the way a keyboard produces them.
VERBATIM = "We cut onboarding time by 40 per cent - measured across the first quarter"

# Close enough that the writer will recognise the mistake the moment the linter
# shows them the page's own wording.
NEAR_MISS = "We cut onboarding times by 40 per cent - measured over the first quarter"


class QuoteVault(unittest.TestCase):
    """A scratch vault holding one report, with an archive when a test wants one."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "report-maker.toml").write_text("[vault]\n", encoding="utf-8")
        self.cfg: Config = load(self.root)
        self.folder = self.cfg.reports / "r"
        self.folder.mkdir(parents=True)
        (self.folder / "sources.yml").write_text(BIB, encoding="utf-8")
        self.report = Report(id="r", folder=self.folder, cfg=self.cfg)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, body: str) -> Report:
        (self.folder / "main.typ").write_text(HEAD + body, encoding="utf-8")
        return self.report

    def archive(self, key: str = "example-page", text: str = PAGE) -> None:
        folder = self.folder / "snapshots"
        folder.mkdir(exist_ok=True)
        (folder / f"{key}.txt").write_text(text, encoding="utf-8")
        (folder / f"{key}.html").write_text(f"<p>{text}</p>", encoding="utf-8")
        (folder / f"{key}.json").write_text(
            json.dumps(
                {
                    "key": key,
                    "url": "https://example.com/pricing",
                    "fetched": "2026-08-18T09:00:00Z",
                    "sha256": "a" * 64,
                    "content_type": "text/html",
                    "status": 200,
                    "title": "Example Ltd — pricing",
                    "bytes": len(text),
                }
            ),
            encoding="utf-8",
        )

    def findings(self, report: Report | None = None) -> list[check.Finding]:
        return check.check_report(self.cfg, report or self.report)

    def codes(self, report: Report | None = None) -> list[str]:
        return [finding.code for finding in self.findings(report)]

    def message(self, code: str) -> str:
        found = [f.message for f in self.findings() if f.code == code]
        self.assertEqual(len(found), 1, f"expected exactly one {code}: {found}")
        return found[0]


def srcquote(quote: str, *, locator: str | None = "Pricing, paragraph 2",
             source: str | None = "[@example-page]") -> str:
    args = [f'"{quote}"']
    if source is not None:
        args.append(f"source: {source}")
    if locator is not None:
        args.append(f'locator: "{locator}"')
    return "#srcquote(\n  " + ",\n  ".join(args) + ",\n)\n"


# ── the archive is optional until it exists ──────────────────────────────────


class WithoutSnapshots(QuoteVault):
    def test_a_report_with_no_snapshots_folder_is_silent(self) -> None:
        # Every vault written before quote checking existed is in this state.
        self.write(srcquote("words that appear on no page anywhere"))
        self.assertEqual(self.codes(), [])

    def test_the_first_snapshot_turns_the_rules_on(self) -> None:
        # Not even for this key: once a report has an archive, a quote that
        # cannot be checked against it is a quote nobody can check.
        self.write(srcquote("words that appear on no page anywhere"))
        self.archive(key="another-source", text="Some other page entirely.")
        self.assertIn("E008", self.codes())


# ── E008: a locator with nothing behind it ───────────────────────────────────


class Locators(QuoteVault):
    def test_a_locator_without_a_snapshot_is_an_error(self) -> None:
        self.write(srcquote(VERBATIM))
        self.archive(key="another-source", text="Some other page entirely.")
        self.assertIn("E008", self.codes())
        self.assertIn("run report-maker cite --refresh", self.message("E008"))

    def test_claim_carries_the_same_promise_as_srcquote(self) -> None:
        self.write(
            "#claim(\n  [A paraphrase of the pricing page.],\n"
            "  source: [@example-page],\n  locator: \"Pricing\",\n)\n"
        )
        self.archive(key="another-source", text="Some other page entirely.")
        self.assertEqual(self.codes(), ["E008"])

    def test_a_snapshotted_key_raises_nothing(self) -> None:
        self.write(srcquote(VERBATIM))
        self.archive()
        self.assertEqual(self.codes(), [])

    def test_a_quote_with_no_locator_is_a_warning(self) -> None:
        self.write(srcquote(VERBATIM, locator=None))
        self.archive()
        self.assertEqual(self.codes(), ["W004"])
        self.assertIn("`locator:`", self.message("W004"))

    def test_a_missing_locator_is_only_ever_a_warning(self) -> None:
        # No locator means no promise about *where*, so E008 has nothing to
        # object to even though this key was never archived.
        self.write(srcquote(VERBATIM, locator=None))
        self.archive(key="another-source", text="Some other page entirely.")
        self.assertEqual(self.codes(), ["W004"])

    def test_a_quote_with_no_source_at_all(self) -> None:
        self.write(srcquote(VERBATIM, source=None))
        self.archive()
        self.assertIn("E004", self.codes())


# ── E009: the archive has to say it ──────────────────────────────────────────


class Quotations(QuoteVault):
    def test_typography_is_not_a_difference(self) -> None:
        # The page sets curly quotes, an em dash and a line break; the report
        # types straight quotes, a hyphen and one line. Same words.
        self.write(srcquote(VERBATIM))
        self.archive()
        self.assertEqual(self.codes(), [])

    def test_case_is_not_a_difference(self) -> None:
        self.write(srcquote(VERBATIM.upper()))
        self.archive()
        self.assertEqual(self.codes(), [])

    def test_a_quote_the_page_does_not_carry_is_an_error(self) -> None:
        self.write(srcquote("we cut onboarding time by 90 per cent"))
        self.archive()
        self.assertIn("E009", self.codes())
        self.assertIn("snapshots/example-page.txt", self.message("E009"))

    def test_a_near_miss_is_shown_the_page_s_own_words(self) -> None:
        # This is the point of the rule: a failure that hands back the fix.
        self.write(srcquote(NEAR_MISS))
        self.archive()
        # The page's own quotation marks and the comma after them are trimmed
        # off the hint: they are not part of what was said, and printing them
        # inside the message's own quotes reads as a bug.
        self.assertIn(
            'closest text in the snapshot: "we cut onboarding time by 40 per '
            'cent - measured across the first quarter"',
            self.message("E009"),
        )

    def test_a_quote_with_nothing_in_common_carries_no_suggestion(self) -> None:
        self.write(srcquote("the quarterly dividend was suspended in March"))
        self.archive()
        self.assertNotIn("closest text in the snapshot:", self.message("E009"))

    def test_a_second_cited_key_can_be_the_one_that_carries_it(self) -> None:
        (self.folder / "sources.yml").write_text(
            BIB + '\nanother-source:\n  type: Web\n  title: "Another"\n',
            encoding="utf-8",
        )
        self.write(srcquote(VERBATIM, source="[@another-source] [@example-page]"))
        self.archive()
        self.archive(key="another-source", text="Some other page entirely.")
        self.assertEqual(self.codes(), [])

    def test_the_quote_can_also_be_passed_by_name(self) -> None:
        # Typst takes a positional parameter by name too, and a rule with a
        # spelling that gets past it is not a rule.
        self.write(
            '#srcquote(\n  quote: "we cut onboarding time by 90 per cent",\n'
            '  source: [@example-page],\n  locator: "Pricing",\n)\n'
        )
        self.archive()
        self.assertIn("E009", self.codes())

    def test_an_empty_snapshot_has_nothing_to_check_against(self) -> None:
        # A PDF or an image archives fine and extracts to nothing. The bytes are
        # still evidence; there is simply no text to match, which is not a
        # violation of anything.
        self.write(srcquote(VERBATIM))
        self.archive(text="   \n")
        self.assertEqual(self.codes(), [])

    def test_content_instead_of_a_string_is_left_to_typst(self) -> None:
        # `srcquote` asserts on this at compile time. The linter cannot extract
        # words from content, so it says nothing rather than guessing.
        self.write(
            "#srcquote(\n  [Not a string literal],\n  source: [@example-page],\n"
            '  locator: "Pricing",\n)\n'
        )
        self.archive()
        self.assertEqual(self.codes(), [])

    def test_a_quoted_call_is_not_a_string_literal(self) -> None:
        self.write(
            "#srcquote(\n  read(\"quote.txt\"),\n  source: [@example-page],\n"
            '  locator: "Pricing",\n)\n'
        )
        self.archive()
        self.assertEqual(self.codes(), [])

    def test_a_commented_out_quote_is_not_checked(self) -> None:
        # W001 is expected — the only citation in the file is commented out, so
        # nothing cites the entry any more. E009 and W004 are not.
        self.write("// " + srcquote("nothing like the page").replace("\n", " ") + "\n")
        self.archive()
        self.assertEqual(self.codes(), ["W001"])

    def test_escapes_in_the_literal_are_resolved_before_comparing(self) -> None:
        self.archive(text='The notice reads "closing at 5pm" on every door.')
        self.write(srcquote('closing at 5pm'))
        self.assertEqual(self.codes(), [])
        self.write(srcquote('The notice reads \\"closing at 5pm\\" on every door'))
        self.assertEqual(self.codes(), [])


# ── argument parsing, which the two rules above rest on ──────────────────────


class Arguments(unittest.TestCase):
    def test_a_comma_inside_a_string_does_not_split_the_list(self) -> None:
        positional, named = check.arguments(
            '("One thing, then another", source: [@k], locator: "p. 4, note 2")'
        )
        self.assertEqual(positional, ['"One thing, then another"'])
        self.assertEqual(named["locator"], '"p. 4, note 2"')

    def test_content_and_nested_calls_stay_whole(self) -> None:
        _, named = check.arguments('(x, source: [@a, see also #cite("b")], alt: "c")')
        self.assertEqual(named["source"], '[@a, see also #cite("b")]')
        self.assertEqual(named["alt"], '"c"')

    def test_string_literals(self) -> None:
        self.assertEqual(check.string_literal('"plain"'), "plain")
        self.assertEqual(check.string_literal(r'"a \"quoted\" word"'), 'a "quoted" word')
        self.assertIsNone(check.string_literal("[content]"))
        self.assertIsNone(check.string_literal('"a" + name'))
        self.assertIsNone(check.string_literal('"unterminated'))


# ── check --json ─────────────────────────────────────────────────────────────


class Json(QuoteVault):
    def test_the_shape_the_app_consumes(self) -> None:
        self.write(srcquote("we cut onboarding time by 90 per cent"))
        self.archive()
        payload = check.findings_json(self.cfg, self.findings())

        self.assertEqual(payload["vault"], str(self.cfg.root))
        self.assertEqual(payload["errors"], 1)
        self.assertEqual(payload["warnings"], 0)
        self.assertNotIn("score", payload)

        finding = payload["findings"][0]
        self.assertEqual(
            set(finding), {"level", "code", "path", "line", "message", "report"}
        )
        self.assertEqual(finding["level"], "error")
        self.assertEqual(finding["code"], "E009")
        self.assertEqual(finding["report"], "r")
        # Vault-relative POSIX, because the app hands it straight back to a file
        # channel that refuses anything outside the vault.
        self.assertEqual(finding["path"], "reports/r/main.typ")
        self.assertGreater(finding["line"], 1)

    def test_a_finding_about_the_bibliography_still_names_its_report(self) -> None:
        self.write("Nothing is cited here.#assess\n")
        payload = check.findings_json(self.cfg, self.findings())
        warning = payload["findings"][0]
        self.assertEqual(warning["code"], "W001")
        self.assertEqual(warning["path"], "reports/r/sources.yml")
        self.assertEqual(warning["report"], "r")

    def test_counts_and_score_ride_along(self) -> None:
        self.write("#image(\"a.png\")\n" + srcquote(VERBATIM, locator=None))
        self.archive()
        payload = check.findings_json(
            self.cfg, self.findings(), score={"reports": [], "density": 1.0}
        )
        self.assertEqual(payload["errors"], 1)
        self.assertEqual(payload["warnings"], 1)
        self.assertEqual(payload["score"]["density"], 1.0)

    def test_a_clean_vault_is_an_empty_list_not_an_absent_one(self) -> None:
        self.write(srcquote(VERBATIM))
        self.archive()
        payload = check.findings_json(self.cfg, self.findings())
        self.assertEqual(payload["findings"], [])
        self.assertEqual(payload["errors"], 0)


if __name__ == "__main__":
    unittest.main()
