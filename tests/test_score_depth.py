"""Evidence depth: how many parties a report actually rests on.

`test_score.py` covers density — is a statement cited, assessed or unmarked.
This file covers the failure density cannot see. A section can be 100% cited and
every `@key` in it can resolve to the same domain, and for an audit that is a
materially weaker document than one resting on three independent sources: a
`severity: "high"` finding whose only evidence is a page the audited party
controls is that party's own account of itself.

Three properties carry the module.

The first is that a family is coarse enough to be true. `docs.acme.com`,
`www.acme.com` and `acme.com` are one party, `shop.example.co.uk` is
`example.co.uk` and not `co.uk`, and a source with no URL is named by whoever
published it. A family test that split a company in two would let a
single-sourced section pass by citing the same company twice.

The second is what counts as load-bearing. A short section citing one page must
stay silent — a linter that flags every introduction is a linter people switch
off — while a section carrying three citations and a finding carrying one are
both claims about the world, and W010 has to fire on them.

The third is that the counts reach the outside: `to_json` carries them for the
app, and the table prints a families column.

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

from engine import score, sources  # noqa: E402
from engine.config import Config  # noqa: E402
from engine.workspace import Report  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "examples/demo-vault"

# Three entries, three hostnames, one party. This is the shape the whole module
# exists for: nothing about it looks single-sourced until the domains are read.
ONE_DOMAIN = """
acme-pricing:
  type: Web
  title: "Pricing"
  url: https://acme.com/pricing

acme-docs:
  type: Web
  title: "Documentation"
  url: https://docs.acme.com/start

acme-blog:
  type: Web
  title: "Blog"
  url:
    value: https://www.acme.com/blog
    date: 2026-01-01
"""

# The same three claims, from three parties: the company, the registrar, and a
# measurement of our own with no URL at all.
THREE_DOMAINS = """
acme-pricing:
  type: Web
  title: "Pricing"
  url: https://acme.com/pricing

registry-filing:
  type: Web
  title: "Filing history"
  url: https://find.companies-house.gov.uk/company/1

own-probe:
  type: Misc
  title: "Direct measurement of response times"
  author: "Youniss Kandah"
  note: "Own measurement. Command in the appendix."
"""


def resolve(main: str, bib: str) -> list[score.Citation]:
    """Every citation in `main`, resolved against `bib` — the real path."""
    entries = sources.parse_text(bib)
    return score.citations(
        main,
        families=score.key_families(entries),
        keys={entry.key for entry in entries},
    )


def families_of(main: str, bib: str) -> dict[str, int]:
    return score.family_counts(resolve(main, bib))


def entry(bib: str, key: str) -> sources.Source:
    return next(source for source in sources.parse_text(bib) if source.key == key)


class VaultCase(unittest.TestCase):
    """A one-report vault on disk, because W010 reads files the way `check` does."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def report(self, main: str, bib: str = "") -> Report:
        folder = self.root / "reports" / "r"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "main.typ").write_text(main, encoding="utf-8")
        (folder / "sources.yml").write_text(bib, encoding="utf-8")
        return Report(id="r", folder=folder, cfg=Config(root=self.root))

    def warnings(self, main: str, bib: str = "") -> list:
        return score.family_findings(self.report(main, bib))

    def messages(self, main: str, bib: str = "") -> list[str]:
        return [finding.message for finding in self.warnings(main, bib)]


# ── what a family is ─────────────────────────────────────────────────────────


class Families(unittest.TestCase):
    def test_a_web_source_is_its_registrable_domain(self) -> None:
        for key in ("acme-pricing", "acme-docs", "acme-blog"):
            with self.subTest(key=key):
                self.assertEqual(score.family(entry(ONE_DOMAIN, key)), "acme.com")

    def test_a_country_domain_keeps_the_label_that_carries_the_identity(self) -> None:
        # `example.co.uk`, never `co.uk` — which is the case `sources._host_label`
        # already handles, and the reason this module borrows it rather than
        # deriving a second answer.
        bib = 'shop:\n  type: Web\n  url: https://shop.example.co.uk/prices\n'
        self.assertEqual(score.family(entry(bib, "shop")), "example.co.uk")
        filing = entry(THREE_DOMAINS, "registry-filing")
        self.assertEqual(score.family(filing), "companies-house.gov.uk")

    def test_a_source_with_no_url_is_named_by_its_author(self) -> None:
        self.assertEqual(
            score.family(entry(THREE_DOMAINS, "own-probe")), "Youniss Kandah"
        )

    def test_an_author_written_as_a_person_flattens_the_same_way_the_panel_does(
        self,
    ) -> None:
        bib = "interview:\n  type: Misc\n  author:\n    name: Kandah\n    given-name: Youniss\n"
        self.assertEqual(score.family(entry(bib, "interview")), "Youniss Kandah")

    def test_a_publisher_outranks_an_author(self) -> None:
        # Two papers by different people from one institute are one account of
        # the world, not two.
        bib = (
            "paper:\n"
            "  type: Article\n"
            '  author: "A Researcher"\n'
            '  publisher: "Institute of Things"\n'
        )
        self.assertEqual(score.family(entry(bib, "paper")), "Institute of Things")

    def test_a_source_that_names_nobody_is_its_own_family(self) -> None:
        # The alternative — one shared "unknown" bucket — would let three
        # anonymous entries trip a rule about single-sourcing, which is a warning
        # fired because the tooling could not tell.
        bib = 'mystery:\n  type: Misc\n  title: "Something we were sent"\n'
        self.assertEqual(score.family(entry(bib, "mystery")), "mystery")

    def test_a_key_with_no_entry_behind_it_rests_on_nobody(self) -> None:
        # `check` raises E006 for it, and it points at no page, so it supports
        # nothing. The same answer `statements` gives: not evidence.
        self.assertEqual(resolve("A claim @nowhere.\n", ONE_DOMAIN), [])

    def test_an_empty_bibliography_leaves_every_key_standing_for_itself(self) -> None:
        # The leniency the linter applies with nothing to check against. Merging
        # the keys into one "unknown" family would report two unrelated parties
        # as one, and fire W010 because the tooling could not tell.
        cites = score.citations("One @alpha. Two @beta. Three @gamma.\n")
        self.assertEqual(len(score.family_counts(cites)), 3)


# ── counting them ────────────────────────────────────────────────────────────


class Counting(unittest.TestCase):
    def test_three_keys_on_one_domain_are_one_family(self) -> None:
        main = (
            "The vendor lists three tiers @acme-pricing.\n"
            "The docs describe the same three @acme-docs.\n"
            "The blog announced them in June @acme-blog.\n"
        )
        self.assertEqual(families_of(main, ONE_DOMAIN), {"acme.com": 3})

    def test_three_keys_on_three_domains_are_three_families(self) -> None:
        main = (
            "The vendor lists three tiers @acme-pricing.\n"
            "The registrar records one director @registry-filing.\n"
            "We measured the response ourselves @own-probe.\n"
        )
        counts = families_of(main, THREE_DOMAINS)
        self.assertEqual(len(counts), 3)
        self.assertEqual(set(counts), {"acme.com", "companies-house.gov.uk", "Youniss Kandah"})

    def test_the_heaviest_family_comes_first(self) -> None:
        main = (
            "One @registry-filing. Two @acme-pricing. Three @acme-pricing.\n"
        )
        self.assertEqual(
            list(families_of(main, THREE_DOMAINS)), ["acme.com", "companies-house.gov.uk"]
        )

    def test_a_citation_inside_a_helper_still_counts(self) -> None:
        # `statements` treats a helper call as structure; a figure sourced to the
        # audited party's own site is still the document resting on that party.
        main = "#srcfig(table([a]), caption: [c], source: [@acme-pricing])\n"
        self.assertEqual(families_of(main, ONE_DOMAIN), {"acme.com": 1})
        self.assertEqual(score.statements(main, keys={"acme-pricing"}), [])

    def test_a_cross_reference_is_not_a_citation(self) -> None:
        main = "See the diagram @fig-one for the shape of it.\n#metadata(none) <fig-one>\n"
        self.assertEqual(families_of(main, ONE_DOMAIN), {})

    def test_sections_carry_their_own_families(self) -> None:
        main = (
            "= Scope\n\nOne page @acme-pricing.\n\n"
            "= Findings\n\nTwo @acme-docs, and three @registry-filing.\n"
        )
        bib = ONE_DOMAIN + THREE_DOMAINS.replace("acme-pricing:", "acme-pricing-2:")
        cites = resolve(main, bib)
        found = score.sections(main, score.statements(main), cites)
        self.assertEqual(
            [(s["title"], s["citations"], s["families"], s["family"]) for s in found],
            [
                ("Scope", 1, 1, "acme.com"),
                ("Findings", 2, 2, None),
            ],
        )

    def test_sections_called_without_citations_say_nothing_rather_than_zero_one(
        self,
    ) -> None:
        # The two-argument spelling is what a caller with no bibliography in hand
        # uses; it must not claim the section rests on one family.
        main = "= Scope\n\nOne page @acme-pricing.\n"
        found = score.sections(main, score.statements(main))
        self.assertEqual((found[0]["citations"], found[0]["families"]), (0, 0))
        self.assertIsNone(found[0]["family"])


# ── blocks: findings and assessments ─────────────────────────────────────────


class Blocks(unittest.TestCase):
    FINDING = (
        "#finding(\n"
        '  id: "F-01",\n'
        '  title: "The pricing page contradicts the contract",\n'
        '  severity: "high",\n'
        "  evidence: [What was observed @acme-pricing.],\n"
        "  impact: [Why it matters.#assess],\n"
        ")\n"
    )

    def test_a_finding_is_a_block_with_the_families_behind_it(self) -> None:
        found = score.blocks(self.FINDING, resolve(self.FINDING, ONE_DOMAIN))
        self.assertEqual(len(found), 1)
        self.assertEqual(
            (found[0]["kind"], found[0]["name"], found[0]["citations"]),
            ("finding", "F-01", 1),
        )
        self.assertEqual(found[0]["family"], "acme.com")

    def test_a_finding_with_no_id_is_named_by_its_title(self) -> None:
        text = '#finding(\n  title: "A concise problem",\n  evidence: [Seen @acme-docs.],\n)\n'
        self.assertEqual(score.blocks(text, resolve(text, ONE_DOMAIN))[0]["name"], "A concise problem")

    def test_an_assessment_block_is_counted_too(self) -> None:
        text = "#assessment[\n  Our reading of the pricing page @acme-pricing.\n]\n"
        found = score.blocks(text, resolve(text, ONE_DOMAIN))
        self.assertEqual((found[0]["kind"], found[0]["citations"]), ("assessment", 1))
        self.assertEqual(found[0]["family"], "acme.com")

    def test_blocks_come_back_in_document_order(self) -> None:
        text = "#assessment[\n  A judgement @acme-docs.\n]\n\n" + self.FINDING
        kinds = [b["kind"] for b in score.blocks(text, resolve(text, ONE_DOMAIN))]
        self.assertEqual(kinds, ["assessment", "finding"])

    def test_the_word_finding_in_prose_is_not_a_block(self) -> None:
        # Only `#finding(` is a call. In Typst markup `finding(x)` is literal
        # text, and treating it as a block would invent claims out of prose.
        text = "Every finding (see the appendix) is numbered @acme-pricing.\n"
        self.assertEqual(score.blocks(text, resolve(text, ONE_DOMAIN)), [])


# ── W010 ─────────────────────────────────────────────────────────────────────


class SingleFamily(VaultCase):
    def test_a_section_resting_on_one_domain_warns_and_names_it(self) -> None:
        main = (
            "= Findings\n\n"
            "The vendor lists three tiers @acme-pricing.\n"
            "The docs describe the same three @acme-docs.\n"
            "The blog announced them in June @acme-blog.\n"
        )
        findings = self.warnings(main, ONE_DOMAIN)
        self.assertEqual([(f.level, f.code) for f in findings], [("warning", "W010")])
        self.assertEqual(
            findings[0].message,
            'all 3 citations in section "Findings" resolve to acme.com — '
            "the section rests on one party's own account",
        )
        self.assertEqual(findings[0].line, 1)
        self.assertEqual(findings[0].report, "r")
        self.assertEqual(findings[0].path.name, "main.typ")

    def test_the_same_section_on_three_domains_is_silent(self) -> None:
        main = (
            "= Findings\n\n"
            "The vendor lists three tiers @acme-pricing.\n"
            "The registrar records one director @registry-filing.\n"
            "We measured the response ourselves @own-probe.\n"
        )
        self.assertEqual(self.warnings(main, THREE_DOMAINS), [])

    def test_a_mixed_section_is_silent(self) -> None:
        # Four citations, two families. The rule is about the party behind the
        # evidence, not about how many keys were used.
        main = (
            "= Findings\n\n"
            "One @acme-pricing, two @acme-docs, three @acme-blog.\n"
            "And one that is not theirs @registry-filing.\n"
        )
        self.assertEqual(self.warnings(main, ONE_DOMAIN + THREE_DOMAINS), [])

    def test_a_short_section_citing_one_page_must_not_warn(self) -> None:
        # An introduction that names a page and moves on makes no claim whose
        # strength depends on corroboration. Warning here is how a linter gets
        # switched off.
        main = "= Scope\n\nThis review covers the published pricing @acme-pricing.\n"
        self.assertEqual(self.warnings(main, ONE_DOMAIN), [])

    def test_two_citations_are_still_below_the_threshold(self) -> None:
        main = "= Scope\n\nOne @acme-pricing. Two @acme-docs.\n"
        self.assertEqual(self.warnings(main, ONE_DOMAIN), [])
        self.assertEqual(score.LOAD_BEARING, 3)

    def test_a_finding_is_load_bearing_at_one_citation(self) -> None:
        main = "= Findings\n\n" + Blocks.FINDING
        messages = self.messages(main, ONE_DOMAIN)
        self.assertEqual(
            messages,
            [
                "the only citation in finding F-01 resolves to acme.com — "
                "the finding rests on one party's own account"
            ],
        )

    def test_a_finding_citing_two_parties_is_silent(self) -> None:
        main = (
            "= Findings\n\n"
            "#finding(\n"
            '  id: "F-02",\n'
            "  evidence: [What they say @acme-pricing, and what the registrar records "
            "@registry-filing.],\n"
            ")\n"
        )
        self.assertEqual(self.warnings(main, ONE_DOMAIN + THREE_DOMAINS), [])

    def test_a_finding_with_no_evidence_at_all_is_not_this_rule(self) -> None:
        # Zero citations is a different failure, and inventing a family for it
        # would put a name in the message that is in no bibliography.
        main = '= Findings\n\n#finding(\n  id: "F-03",\n  evidence: [Nothing cited.],\n)\n'
        self.assertEqual(self.warnings(main, ONE_DOMAIN), [])

    def test_an_assessment_block_is_never_warned_about(self) -> None:
        # It has already told the reader it is opinion. Its depth is worth
        # showing; it is not a defect.
        main = (
            "#assessment[\n"
            "  Our reading, resting on one party @acme-pricing, @acme-docs and "
            "@acme-blog.\n"
            "]\n"
        )
        self.assertEqual(self.warnings(main, ONE_DOMAIN), [])
        self.assertEqual(score.blocks(main, resolve(main, ONE_DOMAIN))[0]["families"], 1)

    def test_the_same_page_cited_three_times_is_still_one_family(self) -> None:
        main = (
            "= Findings\n\n"
            "One @acme-pricing. Two @acme-pricing. Three @acme-pricing.\n"
        )
        self.assertIn("acme.com", self.messages(main, ONE_DOMAIN)[0])

    def test_a_report_with_no_main_typ_is_silent_not_fatal(self) -> None:
        report = Report(id="r", folder=self.root, cfg=Config(root=self.root))
        self.assertEqual(score.family_findings(report), [])

    def test_an_untitled_section_is_described_rather_than_named(self) -> None:
        main = "#heading(level: 1)[]\n\nOne @acme-pricing. Two @acme-docs. Three @acme-blog.\n"
        self.assertIn("in this section", self.messages(main, ONE_DOMAIN)[0])


# ── the payload and the table ────────────────────────────────────────────────


class Payload(VaultCase):
    MAIN = (
        "= Findings\n\n"
        "The vendor lists three tiers @acme-pricing.\n"
        "The docs describe the same three @acme-docs.\n"
        "The blog announced them in June @acme-blog.\n"
    )

    def scored(self) -> score.ReportScore:
        report = self.report(self.MAIN, ONE_DOMAIN)
        return score.score_report(report.cfg, report)

    def test_the_report_score_carries_the_families(self) -> None:
        scored = self.scored()
        self.assertEqual(scored.families, 1)
        self.assertEqual(scored.family_counts, {"acme.com": 3})

    def test_json_is_serialisable_and_carries_depth(self) -> None:
        payload = json.loads(json.dumps(score.to_json([self.scored()])))
        first = payload["reports"][0]
        self.assertEqual(first["families"], 1)
        self.assertEqual(first["familyCounts"], {"acme.com": 3})
        self.assertEqual(first["sections"][0]["families"], 1)
        self.assertEqual(first["sections"][0]["family"], "acme.com")
        self.assertEqual(first["sections"][0]["citations"], 3)
        self.assertEqual(payload["families"], 1)
        self.assertEqual(payload["familyCounts"], {"acme.com": 3})

    def test_the_vault_total_counts_a_shared_family_once(self) -> None:
        # A family cited in six reports is one party the vault leans on, not six.
        scored = self.scored()
        payload = score.to_json([scored, scored])
        self.assertEqual(payload["families"], 1)
        self.assertEqual(payload["familyCounts"], {"acme.com": 6})

    def test_the_table_has_a_families_column(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            code = score.report_scores(self.report(self.MAIN, ONE_DOMAIN).cfg, [self.scored()])
        printed = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("families", printed)
        self.assertIn("acme.com", printed)

    def test_a_report_with_no_citations_prints_no_family_line(self) -> None:
        report = self.report("Everything here is unmarked prose.\n")
        scored = score.score_report(report.cfg, report)
        out = io.StringIO()
        with redirect_stdout(out):
            score.report_scores(report.cfg, [scored])
        self.assertEqual(scored.families, 0)
        self.assertNotIn("most cited", out.getvalue())


class HalfWritten(unittest.TestCase):
    """A report is scored while it is being typed, so nothing here may raise."""

    @unittest.skipUnless(DEMO.is_dir(), "the demo vault is not present")
    def test_every_prefix_of_a_real_report_resolves(self) -> None:
        raw = (DEMO / "reports/examples/2026-08-16-example/main.typ").read_text(
            encoding="utf-8"
        )
        bib = (DEMO / "reports/examples/2026-08-16-example/sources.yml").read_text(
            encoding="utf-8"
        )
        entries = sources.parse_text(bib)
        for cut in range(0, len(raw), 11):
            text = raw[:cut]
            cites = score.citations(
                text,
                families=score.key_families(entries),
                keys={entry.key for entry in entries},
            )
            score.blocks(text, cites)
            score.sections(text, score.statements(text), cites)

    def test_an_unterminated_finding_degrades_instead_of_raising(self) -> None:
        text = '#finding(\n  id: "F-01",\n  evidence: [Seen @acme-pricing.\n'
        found = score.blocks(text, resolve(text, ONE_DOMAIN))
        self.assertEqual([b["kind"] for b in found], ["finding"])
        self.assertEqual(found[0]["citations"], 1)


if __name__ == "__main__":
    unittest.main()
