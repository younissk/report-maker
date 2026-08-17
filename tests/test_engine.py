"""Engine unit tests.

They cover the parts that fail quietly: vault discovery, theme generation, and
the citation linter. A linter that misses a violation is worse than no linter,
because the build goes green and the rule stops being true.

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

from engine import brand, check, library, scaffold, vault  # noqa: E402
from engine.config import Config, load  # noqa: E402
from engine.workspace import Report, reports  # noqa: E402


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
        if sources:
            (folder / "sources.yml").write_text(sources, encoding="utf-8")
        return Report(id=rid, folder=folder, cfg=self.cfg)

    def codes(self, report: Report) -> list[str]:
        return [f.code for f in check.check_report(self.cfg, report)]


class TestTemplates(Vault):
    def test_builtins_are_visible_in_a_fresh_vault(self) -> None:
        found = vault.templates(self.cfg)
        self.assertIn("base", found)
        self.assertIn("brief", found)
        self.assertTrue(found["base"].builtin)

    def test_nesting_is_grouping(self) -> None:
        self.quiet(scaffold.new_template, self.cfg, "audits/company", source="base")
        tpl = vault.template(self.cfg, "audits/company")
        self.assertEqual(tpl.group, "audits")
        self.assertEqual(tpl.name, "company")
        self.assertIn("audits", vault.groups(self.cfg))

    def test_a_vault_template_shadows_a_builtin_of_the_same_id(self) -> None:
        folder = self.cfg.templates / "brief"
        folder.mkdir(parents=True)
        (folder / "template.toml").write_text('title = "Ours"\n', encoding="utf-8")
        tpl = vault.template(self.cfg, "brief")
        self.assertFalse(tpl.builtin)
        self.assertEqual(tpl.title, "Ours")

    def test_lineage_runs_oldest_first(self) -> None:
        self.quiet(scaffold.new_template, self.cfg, "audits/quick", source="brief", copy_design=False)
        chain = [t.id for t in vault.lineage(self.cfg, vault.template(self.cfg, "audits/quick"))]
        self.assertEqual(chain, ["base", "brief", "audits/quick"])

    def test_a_thin_template_inherits_every_design_file(self) -> None:
        self.quiet(scaffold.new_template, self.cfg, "audits/quick", source="brief", copy_design=False)
        self.quiet(library.stage, self.cfg)
        staged = library.design_dir(self.cfg, "audits/quick")
        for name in ("report.typ", "theme.typ", "components.typ", "tokens.typ"):
            self.assertTrue((staged / name).is_file(), name)
        # The design it extends replaces only report.typ, so that is the copy
        # that must win over the base one.
        self.assertIn("letterhead", (staged / "report.typ").read_text())

    def test_bare_name_resolves_when_unambiguous(self) -> None:
        self.quiet(scaffold.new_template, self.cfg, "audits/company", source="base")
        self.assertEqual(vault.template(self.cfg, "company").id, "audits/company")

    def test_ambiguous_bare_name_is_an_error(self) -> None:
        for group in ("audits", "proposals"):
            self.quiet(scaffold.new_template, self.cfg, f"{group}/company", source="base")
        with self.assertRaises(vault.VaultError):
            vault.template(self.cfg, "company")


class TestBrand(Vault):
    def test_tokens_are_valid_typst_values(self) -> None:
        self.quiet(library.stage, self.cfg)
        tokens = (library.design_dir(self.cfg, "base") / "tokens.typ").read_text()
        self.assertIn('rgb("#2E5A88")', tokens)   # colour, quoted
        self.assertIn("body: 9.8pt", tokens)      # length, unquoted
        self.assertIn("top: 26mm", tokens)

    def test_vault_brand_overrides_default(self) -> None:
        (self.cfg.brand / "brand.json").write_text(
            json.dumps({"colors": {"accent": "#123456"}}), encoding="utf-8"
        )
        self.quiet(library.stage, self.cfg)
        theme = brand.sync_mermaid(self.cfg, "default")
        tokens = (library.design_dir(self.cfg, "base") / "tokens.typ").read_text()
        mermaid = json.loads((theme / "config.json").read_text())
        # One value, three artefacts: this is the drift the generator exists to stop.
        self.assertIn('accent: rgb("#123456")', tokens)
        self.assertEqual(mermaid["themeVariables"]["nodeBorder"], "#123456")
        self.assertIn("#123456", (theme / "style.css").read_text())

    def test_a_design_can_sit_on_its_own_brand_pack(self) -> None:
        (self.cfg.brand / "mono").mkdir()
        (self.cfg.brand / "mono" / "brand.json").write_text(
            json.dumps({"colors": {"accent": "#111111"}}), encoding="utf-8"
        )
        self.quiet(scaffold.new_template, self.cfg, "mono-brief", source="brief", copy_design=False)
        toml = self.cfg.templates / "mono-brief" / "template.toml"
        toml.write_text(toml.read_text().replace('brand = "default"', 'brand = "mono"'), encoding="utf-8")
        self.quiet(library.stage, self.cfg)
        self.assertIn(
            'accent: rgb("#111111")',
            (library.design_dir(self.cfg, "mono-brief") / "tokens.typ").read_text(),
        )
        self.assertIn(
            'accent: rgb("#2E5A88")',
            (library.design_dir(self.cfg, "base") / "tokens.typ").read_text(),
        )

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


class TestReports(Vault):
    STUB = '#import "/.build/design/{tpl}/report.typ": report\n#show: report.with(title: "T")\n'

    def test_reports_nest_and_the_path_is_the_id(self) -> None:
        self.write_report("clients/acme/2026-01-01-audit", self.STUB.format(tpl="base"))
        self.write_report("2026-01-02-loose", self.STUB.format(tpl="base"))
        found = {r.id: r for r in reports(self.cfg)}
        self.assertEqual(set(found), {"clients/acme/2026-01-01-audit", "2026-01-02-loose"})
        self.assertEqual(found["clients/acme/2026-01-01-audit"].group, "clients/acme")
        self.assertEqual(found["2026-01-02-loose"].group, "")

    def test_output_mirrors_the_folder_shape(self) -> None:
        report = self.write_report("clients/acme/2026-01-01-audit", self.STUB.format(tpl="base"))
        self.assertTrue(str(report.pdf).endswith("out/clients/acme/2026-01-01-audit.pdf"))

    def test_a_folder_target_selects_everything_under_it(self) -> None:
        self.write_report("clients/acme/2026-01-01-audit", self.STUB.format(tpl="base"))
        self.write_report("clients/beta/2026-01-01-audit", self.STUB.format(tpl="base"))
        self.assertEqual(len(reports(self.cfg, "clients")), 2)
        self.assertEqual(len(reports(self.cfg, "clients/acme")), 1)

    def test_an_ambiguous_slug_is_an_error(self) -> None:
        self.write_report("clients/acme/2026-01-01-audit", self.STUB.format(tpl="base"))
        self.write_report("clients/beta/2026-01-01-audit", self.STUB.format(tpl="base"))
        with self.assertRaises(SystemExit):
            reports(self.cfg, "2026-01-01-audit")

    def test_the_report_records_its_own_design(self) -> None:
        report = self.write_report("r", self.STUB.format(tpl="audits/company"))
        self.assertEqual(report.template_id(), "audits/company")


class TestCheck(Vault):
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
        report = self.write_report("r", '#show: report.with(title: "T")\n')
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

    def test_a_cross_reference_is_not_a_citation(self) -> None:
        # Typst spells both `@key`. A reference to a label this document defines
        # points at a figure, not at the bibliography.
        report = self.write_report(
            "r",
            self.HEAD + "See @fig-one.\n#srcfig(table([a]), caption: [c], source: [@example-page]) <fig-one>\n",
            self.BIB,
        )
        self.assertNotIn("E006", self.codes(report))

    def test_an_escaped_at_sign_is_not_a_citation(self) -> None:
        # `\@djeed` is the literal text of a handle.
        report = self.write_report("r", self.HEAD + "The handle \\@djeed does not exist.\n", self.BIB)
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


class TestScaffold(Vault):
    def test_new_report_paths_are_project_absolute(self) -> None:
        folder = self.quiet(
            scaffold.new_report, self.cfg, "My Report", slug="2026-01-01-my-report"
        )
        main = (folder / "main.typ").read_text()
        self.assertIn('sources: "/reports/2026-01-01-my-report/sources.yml"', main)
        self.assertIn('#import "/.build/design/base/report.typ"', main)
        self.assertNotIn("{{", main)

    def test_new_report_files_into_a_folder_and_names_its_design(self) -> None:
        folder = self.quiet(
            scaffold.new_report,
            self.cfg,
            "Acme audit",
            slug="2026-01-01-acme",
            into="clients/acme",
            template="brief",
        )
        self.assertEqual(folder, self.cfg.reports / "clients/acme/2026-01-01-acme")
        main = (folder / "main.typ").read_text()
        self.assertIn('#import "/.build/design/brief/report.typ"', main)
        self.assertIn('sources: "/reports/clients/acme/2026-01-01-acme/sources.yml"', main)

    def test_a_new_design_is_editable_in_the_vault(self) -> None:
        folder = self.quiet(scaffold.new_template, self.cfg, "audits/company", source="base")
        self.assertTrue((folder / "report.typ").is_file())
        self.assertTrue((folder / "starter" / "main.typ").is_file())
        self.assertFalse(vault.template(self.cfg, "audits/company").builtin)

    def test_slugify(self) -> None:
        self.assertEqual(scaffold.slugify("Company Audit — Djeed!"), "company-audit-djeed")


if __name__ == "__main__":
    unittest.main()
