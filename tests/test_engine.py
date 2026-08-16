"""Engine unit tests.

They cover the parts that fail quietly: theme generation, and the citation
linter. A linter that misses a violation is worse than no linter, because the
build goes green and the rule stops being true.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import brand, check, scaffold  # noqa: E402
from engine.config import Config, load  # noqa: E402
from engine.workspace import Report  # noqa: E402


class Workspace(unittest.TestCase):
    """A scratch workspace, torn down after each test."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        scaffold.init(self.root)
        self.cfg: Config = load(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_report(self, slug: str, main: str, sources: str = "") -> Report:
        folder = self.cfg.reports / slug
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "main.typ").write_text(main, encoding="utf-8")
        if sources:
            (folder / "sources.yml").write_text(sources, encoding="utf-8")
        return Report(slug=slug, folder=folder, cfg=self.cfg)

    def codes(self, report: Report) -> list[str]:
        return [f.code for f in check.check_report(self.cfg, report)]


class TestBrand(Workspace):
    def test_tokens_are_valid_typst_values(self) -> None:
        brand.sync(self.cfg)
        tokens = (self.cfg.build / "brand" / "tokens.typ").read_text()
        self.assertIn('rgb("#2E5A88")', tokens)   # colour, quoted
        self.assertIn("body: 9.8pt", tokens)      # length, unquoted
        self.assertIn("top: 26mm", tokens)

    def test_workspace_brand_overrides_default(self) -> None:
        (self.cfg.brand / "brand.json").write_text(
            json.dumps({"colors": {"accent": "#123456"}}), encoding="utf-8"
        )
        brand.sync(self.cfg)
        tokens = (self.cfg.build / "brand" / "tokens.typ").read_text()
        mermaid = json.loads((self.cfg.build / "brand" / "mermaid" / "config.json").read_text())
        # One value, three artefacts: this is the drift the generator exists to stop.
        self.assertIn('accent: rgb("#123456")', tokens)
        self.assertEqual(mermaid["themeVariables"]["nodeBorder"], "#123456")
        self.assertIn("#123456", (self.cfg.build / "brand" / "mermaid" / "style.css").read_text())

    def test_unkeyed_default_still_present_after_partial_override(self) -> None:
        (self.cfg.brand / "brand.json").write_text(
            json.dumps({"org": {"name": "Acme"}}), encoding="utf-8"
        )
        merged = brand.load(self.cfg)
        self.assertEqual(merged["org"]["name"], "Acme")
        self.assertEqual(merged["org"]["logo-width"], "44mm")

    def test_mermaid_html_labels_stay_off(self) -> None:
        # With HTML labels the diagram reaches the PDF as wordless boxes.
        cfgjson = brand.mermaid_config(brand.load(self.cfg))
        self.assertFalse(cfgjson["htmlLabels"])
        self.assertFalse(cfgjson["flowchart"]["htmlLabels"])


class TestCheck(Workspace):
    HEAD = '#show: report.with(\n  title: "T",\n  sources: "/reports/r/sources.yml",\n)\n'
    BIB = 'example-page:\n  type: Web\n  title: "x"\n'

    def test_clean_report_has_no_findings(self) -> None:
        report = self.write_report(
            "r",
            self.HEAD + "A fact @example-page. A judgement#assess\n"
            '#srcfig(table([a]), caption: [c], source: [@example-page])\n',
            self.BIB,
        )
        self.assertEqual(self.codes(report), [])

    def test_missing_bibliography(self) -> None:
        report = self.write_report("r", "#show: report.with(title: \"T\")\n")
        self.assertIn("E001", self.codes(report))

    def test_bare_image_and_figure(self) -> None:
        report = self.write_report(
            "r", self.HEAD + '#image("a.png")\n#figure([x], caption: [c])\n', self.BIB
        )
        codes = self.codes(report)
        self.assertIn("E002", codes)
        self.assertIn("E003", codes)

    def test_figure_helper_without_source(self) -> None:
        report = self.write_report(
            "r", self.HEAD + "#srcfig(table([a]), caption: [c])\n", self.BIB
        )
        self.assertIn("E004", self.codes(report))

    def test_citation_with_no_entry(self) -> None:
        report = self.write_report("r", self.HEAD + "A fact @ghost.\n", self.BIB)
        self.assertIn("E006", self.codes(report))

    def test_trailing_punctuation_is_not_part_of_the_key(self) -> None:
        # Typst ends the reference before the full stop; the linter must agree,
        # or every sentence-final citation reads as undefined.
        report = self.write_report("r", self.HEAD + "A fact @example-page.\n", self.BIB)
        self.assertNotIn("E006", self.codes(report))

    def test_commented_and_raw_code_is_not_scanned(self) -> None:
        report = self.write_report(
            "r",
            self.HEAD
            + '// #image("commented.png")\n`image("inline.png")`\n'
            + '```\n#image("block.png")\n```\n',
            self.BIB,
        )
        self.assertNotIn("E002", self.codes(report))

    def test_uncited_source_is_a_warning_not_an_error(self) -> None:
        report = self.write_report("r", self.HEAD + "No citations here.#assess\n", self.BIB)
        findings = check.check_report(self.cfg, report)
        self.assertEqual([f.level for f in findings if f.code == "W001"], ["warning"])


class TestScaffold(Workspace):
    def test_new_report_paths_are_project_absolute(self) -> None:
        folder = scaffold.new_report(self.cfg, "My Report", slug="2026-01-01-my-report")
        main = (folder / "main.typ").read_text()
        self.assertIn('sources: "/reports/2026-01-01-my-report/sources.yml"', main)
        self.assertNotIn("{{", main)

    def test_slugify(self) -> None:
        self.assertEqual(scaffold.slugify("Company Audit — Djeed!"), "company-audit-djeed")


if __name__ == "__main__":
    unittest.main()
