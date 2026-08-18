"""The rules about truth, as opposed to the rules about form.

Every other check test asks whether a report is *shaped* correctly: is there a
`source:`, does the `@key` resolve, is the figure wrapped. This file covers the
three rules that exist because a report can be perfectly shaped and still not be
true.

    E012  the report is still the starter's — including its example citation
    E013  a URL in the prose that never became a source
    E014  a report that calls itself `final` while errors stand

The E012 tests scaffold a *real* report rather than hand-writing a lookalike,
because the whole point of the rule is that it is a diff against a known file
and not a heuristic. A fixture that merely resembles a starter would prove the
rule fires on something; only the genuine article proves it fires on the thing
one `report-maker new` actually produces.

    python3 -m unittest tests.test_check_truth
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import check, scaffold  # noqa: E402
from engine.config import Config, load  # noqa: E402
from engine.workspace import Report  # noqa: E402

#: A report that breaks no rule at all, so a fixture only ever carries the one
#: violation it is about. Nothing in it echoes a starter value.
CLEAN_MAIN = """#import "/.build/design/base/report.typ": report

#show: report.with(
  title: "Acme pricing",
  sources: "/reports/{rid}/sources.yml",
)

= Pricing

Acme charges forty dollars a seat @acme-pricing.
"""

CLEAN_SOURCES = """acme-pricing:
  type: Web
  title: "Acme pricing, as published"
  url:
    value: https://acme.example/pricing
    date: 2026-01-01
"""

# A second party, for the cases where one is not enough. W010 asks a passage
# carrying real weight to rest on more than the audited party's own account, so a
# fixture that needs to come back with no findings at all needs somebody else in
# it — a different registrable domain, which is what makes it a second family.
CORROBORATING_SOURCE = """
registry-filing:
  type: Web
  title: "Acme Ltd, annual return"
  url:
    value: https://companies-house.gov.uk/acme/filing
    date: 2026-01-02
"""


class Vault(unittest.TestCase):
    """A scratch vault, torn down after each test."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        with redirect_stdout(io.StringIO()):
            scaffold.init(self.root)
        self.cfg: Config = load(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def quiet(self, fn, *args, **kwargs):
        with redirect_stdout(io.StringIO()):
            return fn(*args, **kwargs)

    def write_report(self, rid: str, main: str, sources: str = "") -> Report:
        folder = self.cfg.reports / rid
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "main.typ").write_text(main, encoding="utf-8")
        (folder / "sources.yml").write_text(sources, encoding="utf-8")
        return Report(id=rid, folder=folder, cfg=self.cfg)

    def clean_report(self, rid: str = "r", body: str = "", sources: str | None = None) -> Report:
        """A report with no findings, plus whatever `body` adds to it."""
        return self.write_report(
            rid,
            CLEAN_MAIN.format(rid=rid) + body,
            CLEAN_SOURCES if sources is None else sources,
        )

    def findings(self, report: Report) -> list[check.Finding]:
        return check.check_report(self.cfg, report)

    def codes(self, report: Report) -> list[str]:
        return [f.code for f in self.findings(report)]

    def messages(self, report: Report, code: str) -> list[str]:
        return [f.message for f in self.findings(report) if f.code == code]


# ── E012: the starter is not a report ────────────────────────────────────────


class StarterResidue(Vault):
    """The rule that closes the worst hole this linter ever had.

    Before it, `report-maker new` produced a document that passed `check`
    clean and built to a branded PDF carrying invented KPIs and a citation to
    example.com. Every one of those was correctly formed — which is exactly why
    form was not enough.
    """

    def scaffold(self, title: str = "Acme audit", **kwargs) -> Report:
        folder = self.quiet(
            scaffold.new_report, self.cfg, title, slug="2026-01-01-acme", **kwargs
        )
        return Report(id="2026-01-01-acme", folder=folder, cfg=self.cfg)

    def test_a_freshly_scaffolded_report_is_refused(self) -> None:
        # The regression this whole rule exists for: untouched, this used to be
        # a clean bill of health.
        report = self.scaffold()
        findings = self.findings(report)
        self.assertTrue(any(f.code == "E012" for f in findings))
        self.assertTrue(any(f.level == "error" for f in findings))

    def test_the_fabricated_citation_is_named_and_not_merely_counted(self) -> None:
        # The single most damaging residue: a citation that resolves perfectly
        # and points at a page nobody read. The message has to name the URL, or
        # the writer has to go looking for what is wrong.
        report = self.scaffold()
        named = [
            f
            for f in self.findings(report)
            if f.code == "E012" and "example.com/page" in f.message
        ]
        self.assertEqual(len(named), 1, self.messages(report, "E012"))
        # Pointed at the bibliography, which is the file that has to change.
        self.assertEqual(named[0].path.name, "sources.yml")

    def test_the_invented_cover_numbers_are_named(self) -> None:
        report = self.scaffold()
        kpis = [m for m in self.messages(report, "E012") if m.startswith("kpis(")]
        self.assertEqual(len(kpis), 1, self.messages(report, "E012"))
        self.assertIn("Findings raised", kpis[0])

    def test_the_message_says_which_field_and_what_to_do(self) -> None:
        # "placeholder found" sends a writer hunting. Naming the field and the
        # two ways out is the difference between a rule and a nuisance.
        report = self.scaffold()
        subtitle = [m for m in self.messages(report, "E012") if m.startswith("subtitle:")]
        self.assertEqual(
            subtitle,
            ["subtitle: is still the starter's text — replace it or delete the field"],
        )

    def test_a_report_that_has_actually_been_written_passes(self) -> None:
        report = self.scaffold()
        main = report.main.read_text(encoding="utf-8")
        for was, now in (
            ("A one-line description of what this document establishes, and for whom.",
             "What Acme charges, and whether the pricing page still says so"),
            ('role: "Role"', 'role: "Research"'),
            ('subject: "Subject of the report"', 'subject: "acme.example"'),
            ('version: "0.1 — Draft"', 'version: "1.0"'),
            ('classification: "Internal"', 'classification: "Client confidential"'),
        ):
            self.assertIn(was, main)
            main = main.replace(was, now)
        # Everything from the first example block down is the starter's content,
        # and a writer replaces it wholesale rather than field by field.
        body = main[: main.index("#verdict(")] + (
            "Acme charges forty dollars a seat @acme-pricing.\n\n"
            "#metadata(none) <references>\n"
        )
        report.main.write_text(body, encoding="utf-8")
        report.sources.write_text(CLEAN_SOURCES, encoding="utf-8")
        self.assertEqual(self.codes(report), [])

    def test_prose_the_starter_shipped_is_not_residue(self) -> None:
        # A heading called "Scope and method" is a heading, not a fabrication.
        # A rule that flags it is a rule people learn to ignore, and a rule
        # people ignore protects nothing.
        report = self.clean_report(
            body="\n= Scope and method\n\nHow the evidence was gathered @acme-pricing.\n"
        )
        self.assertEqual(self.codes(report), [])

    def test_vocabulary_kept_from_the_starter_is_not_residue(self) -> None:
        # `severity: "high"` is a word from a fixed list, not an invented claim,
        # and `id: "F-11"` is a filing decision — every audit numbers its first
        # finding F-01. Only the title and the prose of a finding assert anything.
        #
        # The finding rests on two independent parties, because a finding citing
        # one is W010 and this test is about E012 having nothing to say. That is
        # the rule asking for what it exists to ask for, not an obstacle: a
        # severity-high finding standing on the audited party's own page alone is
        # exactly the shape W010 was written for.
        report = self.clean_report(
            body=(
                '\n#finding(\n'
                '  id: "F-11",\n'
                '  title: "Pricing page contradicts the sales deck",\n'
                '  severity: "high",\n'
                '  area: "Area",\n'
                '  confidence: "High",\n'
                '  evidence: [What the page says @acme-pricing, and what the '
                'regulator published @registry-filing.],\n'
                '  impact: [Why it matters.#assess],\n'
                '  action: [What to do.#assess],\n'
                ')\n'
            ),
            sources=CLEAN_SOURCES + CORROBORATING_SOURCE,
        )
        self.assertEqual(self.codes(report), [])

    def test_a_source_repointed_at_something_real_passes_under_the_starter_key(self) -> None:
        # The key is the writer's filing decision. `example-page` is a perfectly
        # good name for a page that was actually read; what may not survive is
        # the starter's URL.
        report = self.write_report(
            "r",
            CLEAN_MAIN.format(rid="r").replace("@acme-pricing", "@example-page"),
            "example-page:\n"
            "  type: Web\n"
            '  title: "Acme pricing, as published"\n'
            "  url:\n"
            "    value: https://acme.example/pricing\n",
        )
        self.assertEqual(self.codes(report), [])

    def test_the_starter_url_is_residue_whatever_it_is_filed_under(self) -> None:
        report = self.write_report(
            "r",
            CLEAN_MAIN.format(rid="r").replace("@acme-pricing", "@renamed"),
            "renamed:\n"
            "  type: Web\n"
            '  title: "A title we wrote ourselves"\n'
            "  url:\n"
            "    value: https://example.com/page\n",
        )
        self.assertEqual(self.codes(report), ["E012"])
        self.assertIn("url:", self.messages(report, "E012")[0])

    def test_one_finding_per_entry_however_much_of_it_survived(self) -> None:
        report = self.write_report(
            "r",
            CLEAN_MAIN.format(rid="r").replace("@acme-pricing", "@example-page"),
            "example-page:\n"
            "  type: Web\n"
            '  title: "Example — the page title as published"\n'
            "  url:\n"
            "    value: https://example.com/page\n",
        )
        self.assertEqual(self.codes(report), ["E012"])

    def test_an_unfilled_placeholder_is_an_error(self) -> None:
        report = self.clean_report(body="\nWritten by {{author}} in a hurry.\n")
        self.assertEqual(self.codes(report), ["E012"])
        self.assertIn("{{author}}", self.messages(report, "E012")[0])

    def test_an_angled_placeholder_is_an_error_but_a_typst_label_is_not(self) -> None:
        # `<references>` is how every report in the vault ends. A rule that reads
        # it as a placeholder would fire on all of them at once.
        labelled = self.clean_report("labelled", body="\n#metadata(none) <references>\n")
        self.assertEqual(self.codes(labelled), [])
        shouted = self.clean_report("shouted", body="\nPrepared for <CLIENT NAME>.\n")
        self.assertEqual(self.codes(shouted), ["E012"])

    def test_a_report_whose_design_cannot_be_resolved_says_nothing(self) -> None:
        # A deleted or renamed design leaves the rule with no baseline. It says
        # nothing rather than raising: a linter that crashes when a template
        # moves is a linter nobody keeps running.
        report = self.write_report(
            "r",
            CLEAN_MAIN.format(rid="r").replace("/base/", "/gone-away/"),
            CLEAN_SOURCES,
        )
        self.assertEqual(self.codes(report), [])


# ── E013: a link is not a citation ───────────────────────────────────────────


class BareLinks(Vault):
    """The exact inverse of W001.

    W001 catches a key nobody cited. Nothing caught a citation that never became
    a key — and a footnoted URL looks cited to a reader while being invisible to
    References, to the snapshot archive, to `verify` and to the density score.
    """

    def test_a_bare_url_in_prose_is_an_error(self) -> None:
        report = self.clean_report(
            body="\nThey published the change at https://acme.example/changelog.\n"
        )
        self.assertEqual(self.codes(report), ["E013"])

    def test_the_message_names_the_command_that_fixes_it(self) -> None:
        report = self.clean_report(
            body='\n#link("https://acme.example/changelog")[the changelog]\n'
        )
        self.assertEqual(self.codes(report), ["E013"])
        message = self.messages(report, "E013")[0]
        self.assertIn("report-maker cite r https://acme.example/changelog", message)

    def test_a_url_that_is_registered_as_a_source_passes(self) -> None:
        report = self.clean_report(
            body='\n#link("https://acme.example/pricing")[the pricing page] @acme-pricing.\n'
        )
        self.assertEqual(self.codes(report), [])

    def test_the_match_survives_a_different_spelling_of_the_same_page(self) -> None:
        # http vs https, www or not, a trailing slash: three ways to write one
        # page. An error rule that fires on any of them is an error rule people
        # switch off, so the comparison is on the normalised form.
        report = self.clean_report(
            body='\n#link("http://www.acme.example/pricing/")[pricing] @acme-pricing.\n'
        )
        self.assertEqual(self.codes(report), [])

    def test_a_cross_reference_is_not_a_citation_and_not_a_link(self) -> None:
        # Commit 03323bb drew this distinction once already: `#link(<label>)`
        # points inside the document and has no page behind it to archive.
        report = self.clean_report(
            body="\n#link(<references>)[References]\n\n#metadata(none) <references>\n"
        )
        self.assertEqual(self.codes(report), [])

    def test_a_url_in_a_comment_or_a_code_block_is_not_in_the_report(self) -> None:
        report = self.clean_report(
            body=(
                "\n// see https://acme.example/internal-notes\n"
                "\n```\ncurl https://acme.example/api\n```\n"
            )
        )
        self.assertEqual(self.codes(report), [])

    def test_the_brand_pack_url_is_page_furniture(self) -> None:
        # The organisation's own address is the logo's neighbour, not evidence.
        # `brand/brand.json` in a fresh vault sets org.url to example.com.
        report = self.clean_report(body="\nWritten at https://example.com.\n")
        self.assertEqual(self.codes(report), [])

    def test_the_same_unregistered_url_is_reported_once(self) -> None:
        report = self.clean_report(
            body=(
                "\nFirst https://acme.example/changelog and then "
                "https://acme.example/changelog again.\n"
            )
        )
        self.assertEqual(self.codes(report), ["E013"])


# ── status, and the final gate ───────────────────────────────────────────────


BROKEN = '\n#image("chart.png")\n'  # E002: a bare image bypasses the source contract


class Status(Vault):
    """Three words, and what each of them does to a finding.

    The two rules above make this linter strict enough that it needs a way for a
    writer to say "I know". `draft` is that. `final` is the other half, and the
    one that earns the field: a document asserting that it is finished while the
    rule is broken is asserting something untrue.
    """

    def broken(self, rid: str, status: str | None = None) -> Report:
        declared = f'  status: "{status}",\n' if status is not None else ""
        main = CLEAN_MAIN.format(rid=rid).replace(
            f'  sources: "/reports/{rid}/sources.yml",\n',
            f'  sources: "/reports/{rid}/sources.yml",\n{declared}',
        )
        return self.write_report(rid, main + BROKEN, CLEAN_SOURCES)

    def levels(self, report: Report, code: str) -> list[str]:
        return [f.level for f in self.findings(report) if f.code == code]

    def exit_code(self, report: Report) -> int:
        with redirect_stdout(io.StringIO()):
            return check.report_findings(self.cfg, self.findings(report))

    def test_a_report_with_no_status_behaves_exactly_as_before(self) -> None:
        report = self.broken("none")
        self.assertEqual(self.levels(report, "E002"), ["error"])
        self.assertEqual(self.exit_code(report), 1)

    def test_a_draft_reports_its_errors_as_warnings_and_does_not_fail(self) -> None:
        # A knowingly-unfinished report is not a broken build. The finding is
        # still found and still printed — only its severity moves.
        report = self.broken("draft", "draft")
        self.assertEqual(self.levels(report, "E002"), ["warning"])
        self.assertIn("draft", self.messages(report, "E002")[0])
        self.assertEqual(self.exit_code(report), 0)

    def test_review_behaves_as_an_unmarked_report_does(self) -> None:
        report = self.broken("review", "review")
        self.assertEqual(self.levels(report, "E002"), ["error"])
        self.assertEqual(self.exit_code(report), 1)

    def test_final_is_refused_while_an_error_stands(self) -> None:
        report = self.broken("final", "final")
        codes = self.codes(report)
        self.assertIn("E002", codes)
        self.assertIn("E014", codes)
        self.assertEqual(self.exit_code(report), 1)

    def test_final_never_suppresses_anything(self) -> None:
        # The one thing a status field must never be able to do.
        report = self.broken("final", "final")
        self.assertEqual(self.levels(report, "E002"), ["error"])
        self.assertNotIn("draft", " ".join(self.messages(report, "E002")))

    def test_a_final_report_with_nothing_wrong_is_simply_finished(self) -> None:
        report = self.write_report(
            "done",
            CLEAN_MAIN.format(rid="done").replace(
                '  sources: "/reports/done/sources.yml",\n',
                '  sources: "/reports/done/sources.yml",\n  status: "final",\n',
            ),
            CLEAN_SOURCES,
        )
        self.assertEqual(self.codes(report), [])
        self.assertEqual(self.exit_code(report), 0)

    def test_a_status_nobody_recognises_is_read_as_if_absent(self) -> None:
        # The safe direction. A typo must never quietly hand a report the
        # leniency of `draft`, so an unknown value keeps the strict behaviour
        # and says so.
        report = self.broken("odd", "published")
        self.assertEqual(self.levels(report, "E002"), ["error"])
        self.assertIn("W011", self.codes(report))
        self.assertEqual(self.exit_code(report), 1)

    def test_the_status_reaches_the_metadata_the_manifest_reads(self) -> None:
        report = self.broken("meta", "draft")
        self.assertEqual(report.meta().get("status"), "draft")
        self.assertEqual(report.status, "draft")

    def test_an_unknown_status_is_not_reported_as_the_report_status(self) -> None:
        report = self.broken("odd", "published")
        self.assertEqual(report.status, "")
        # `meta()` still reports what the file literally says: the manifest is a
        # record of the vault, not a judgement about it.
        self.assertEqual(report.meta().get("status"), "published")


if __name__ == "__main__":
    unittest.main()
