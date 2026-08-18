"""The prepared mermaid input, and who else may render it.

The app previews diagrams in Chromium while the build renders them with
mermaid-cli, and the two agree only for as long as they are handed the same
bytes. mermaid writes presentation into inline `style` attributes and Typst's
SVG renderer honours those over any stylesheet, which is why the engine injects
brand `classDef`s rather than trusting the CSS — and why a preview built from
`style.css` alone would look right on screen and wrong in the PDF.

So the load-bearing assertion here is byte equality: what `prepared_json`
publishes is character for character what `render` puts on mermaid's command
line. Everything else guards a way that equality could quietly stop holding —
a classDef injected that the author never asked for, a second copy stacked on
top of the author's own, htmlLabels drifting back on, or the whole thing
starting to need mermaid-cli installed and so becoming unavailable exactly on
the machines the preview was written for.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import brand, diagrams, scaffold  # noqa: E402
from engine.config import Config, load  # noqa: E402
from engine.workspace import Report  # noqa: E402

REPORT = (
    '#import "/.build/design/{tpl}/report.typ": report\n'
    '#show: report.with(title: "T")\n'
)

#: Two of the four emphasis classes, reached by both spellings mermaid allows.
FLOW = """flowchart LR
  a[Evidence] --> b[Claim]
  b --> c[Judgement]
  class a em-accent
  c:::em-ghost
"""

#: A diagram kind that has no classDefs at all — nothing may be appended to it.
SEQUENCE = """sequenceDiagram
  Auditor->>Vendor: request pricing
  Vendor-->>Auditor: the published page
"""

#: The author has defined the class themselves. Theirs wins, untouched.
DEFINED = """flowchart LR
  a[One] --> b[Two]
  class a em-accent
  classDef em-accent stroke-width:4px
"""


class Vault(unittest.TestCase):
    """A scratch vault with one report and one diagram in it."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        with redirect_stdout(io.StringIO()):
            scaffold.init(self.root)
        self.cfg: Config = load(self.root)
        self.report = self.write_report("clients/acme/2026-01-01-audit")
        self.flow = self.write_diagram(self.report, "flow.mmd", FLOW)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def quiet(self, fn, *args, **kwargs):
        with redirect_stdout(io.StringIO()):
            return fn(*args, **kwargs)

    def write_report(self, rid: str, tpl: str = "base") -> Report:
        folder = self.cfg.reports / rid
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "main.typ").write_text(REPORT.format(tpl=tpl), encoding="utf-8")
        return Report(id=rid, folder=folder, cfg=self.cfg)

    def write_diagram(self, report: Report, name: str, text: str) -> Path:
        report.diagrams.mkdir(parents=True, exist_ok=True)
        path = report.diagrams / name
        path.write_text(text, encoding="utf-8")
        return path


class SameInput(Vault):
    """What the app renders is what mermaid rendered."""

    def rendered_input(self, src: Path) -> str:
        """Drive `render` with mermaid-cli stubbed out, and return the text of
        the file it actually passed as --input."""
        captured: dict[str, Path] = {}

        def fake_run(cmd, **kwargs):
            args = list(cmd)
            captured["input"] = Path(args[args.index("--input") + 1])
            out = Path(args[args.index("--output") + 1])
            out.write_text("<svg><text>drawn</text></svg>", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        theme = brand.sync_mermaid(self.cfg, "default")
        with mock.patch.object(diagrams.subprocess, "run", fake_run):
            diagrams.render(
                self.cfg,
                src,
                binary=Path("mmdc"),
                puppeteer=None,
                force=True,
                brand_data=brand.load(self.cfg, "default"),
                theme=theme,
            )
        return captured["input"].read_text(encoding="utf-8")

    def test_prepared_source_is_byte_identical_to_the_built_one(self) -> None:
        payload = diagrams.prepared_json(self.cfg, self.flow)
        self.assertEqual(payload["source"], self.rendered_input(self.flow))
        # And it is not merely the file on disk — the injection did happen.
        self.assertNotEqual(payload["source"], FLOW)

    def test_it_holds_for_a_diagram_that_needs_no_injection(self) -> None:
        # The other half of the equality: when nothing is appended, mermaid is
        # handed the author's file itself, and so is the preview.
        src = self.write_diagram(self.report, "seq.mmd", SEQUENCE)
        payload = diagrams.prepared_json(self.cfg, src)
        self.assertEqual(payload["source"], SEQUENCE)
        self.assertEqual(payload["source"], self.rendered_input(src))
        self.assertEqual(payload["classDefs"], {})

    def test_the_config_and_css_are_the_ones_render_passes(self) -> None:
        payload = diagrams.prepared_json(self.cfg, self.flow)
        theme = brand.sync_mermaid(self.cfg, "default")
        self.assertEqual(payload["config"], str(theme / "config.json"))
        self.assertEqual(payload["css"], str(theme / "style.css"))
        self.assertEqual(
            payload["cssText"], (theme / "style.css").read_text(encoding="utf-8")
        )
        self.assertEqual(
            payload["configJson"],
            json.loads((theme / "config.json").read_text(encoding="utf-8")),
        )


class ClassDefs(Vault):
    def test_only_the_classes_the_diagram_uses_are_injected(self) -> None:
        payload = diagrams.prepared_json(self.cfg, self.flow)
        self.assertEqual(set(payload["classDefs"]), {"em-accent", "em-ghost"})
        for used in ("em-accent", "em-ghost"):
            self.assertIn(f"classDef {used} ", payload["source"])
        for unused in ("em-muted", "em-good"):
            self.assertNotIn(f"classDef {unused} ", payload["source"])

    def test_the_injected_definition_carries_the_brand_colour(self) -> None:
        # The point of injecting at all: a diagram names a role, never a colour,
        # and the colour arrives from the same brand.json as the report's.
        accent = brand.load(self.cfg, "default")["colors"]["accent"]
        payload = diagrams.prepared_json(self.cfg, self.flow)
        self.assertIn(f"fill:{accent}", payload["classDefs"]["em-accent"])
        self.assertIn(f"fill:{accent}", payload["source"])

    def test_an_authors_own_classdef_is_not_doubled(self) -> None:
        src = self.write_diagram(self.report, "defined.mmd", DEFINED)
        payload = diagrams.prepared_json(self.cfg, src)
        self.assertEqual(payload["source"].count("classDef em-accent"), 1)
        self.assertEqual(payload["source"], DEFINED)
        self.assertEqual(payload["classDefs"], {})

    def test_nothing_is_appended_to_a_kind_that_has_no_classdefs(self) -> None:
        src = self.write_diagram(self.report, "seq.mmd", SEQUENCE + "  class a em-accent\n")
        payload = diagrams.prepared_json(self.cfg, src)
        self.assertNotIn("classDef", payload["source"])


class HtmlLabels(Vault):
    def test_the_emitted_config_turns_them_off(self) -> None:
        payload = diagrams.prepared_json(self.cfg, self.flow)
        self.assertIs(payload["configJson"]["htmlLabels"], False)
        self.assertIs(payload["configJson"]["flowchart"]["htmlLabels"], False)

    def test_a_config_with_them_on_is_refused(self) -> None:
        # Nothing in the vault can produce this today, and that is the reason to
        # assert it: with HTML labels the preview renders perfectly and the PDF
        # arrives with no words in the diagram, which nobody would trace back to
        # a setting in a generated file.
        theme = self.root / "bent-theme"
        theme.mkdir()
        bent = brand.mermaid_config(brand.load(self.cfg, "default"))
        bent["htmlLabels"] = True
        (theme / "config.json").write_text(json.dumps(bent), encoding="utf-8")
        (theme / "style.css").write_text("/* empty */\n", encoding="utf-8")

        with mock.patch.object(diagrams.brand, "sync_mermaid", return_value=theme):
            with self.assertRaises(diagrams.DiagramError) as caught:
                diagrams.prepared_json(self.cfg, self.flow)
        self.assertIn("htmlLabels", str(caught.exception))

    def test_the_flowchart_block_alone_is_enough_to_fail(self) -> None:
        theme = self.root / "bent-theme"
        theme.mkdir()
        bent = brand.mermaid_config(brand.load(self.cfg, "default"))
        bent["flowchart"]["htmlLabels"] = True
        (theme / "config.json").write_text(json.dumps(bent), encoding="utf-8")
        (theme / "style.css").write_text("/* empty */\n", encoding="utf-8")

        with mock.patch.object(diagrams.brand, "sync_mermaid", return_value=theme):
            with self.assertRaises(diagrams.DiagramError):
                diagrams.prepared_json(self.cfg, self.flow)


class WithoutMermaidCli(Vault):
    def test_preparing_never_installs_anything(self) -> None:
        self.assertFalse(diagrams.mmdc(self.cfg).exists())
        with mock.patch.object(
            diagrams, "ensure_cli", side_effect=AssertionError("installed mermaid-cli")
        ):
            payload = diagrams.prepared_json(self.cfg, self.flow)
        self.assertIn("classDef em-accent", payload["source"])
        self.assertFalse(diagrams.mmdc(self.cfg).exists())

    def test_the_theme_is_generated_on_demand(self) -> None:
        theme = brand.mermaid_theme_dir(self.cfg, "default")
        self.assertFalse(theme.exists())
        payload = diagrams.prepared_json(self.cfg, self.flow)
        self.assertTrue(Path(payload["config"]).is_file())
        self.assertTrue(Path(payload["css"]).is_file())

    def test_the_version_is_none_until_mermaid_cli_is_installed(self) -> None:
        self.assertIsNone(diagrams.prepared_json(self.cfg, self.flow)["mermaidVersion"])

    def test_the_version_is_the_installed_pin_not_the_configured_one(self) -> None:
        installed = diagrams.cli_dir(self.cfg)
        installed.mkdir(parents=True, exist_ok=True)
        (installed / "package.json").write_text(
            json.dumps({"devDependencies": {"@mermaid-js/mermaid-cli": "11.0.0"}}),
            encoding="utf-8",
        )
        self.assertEqual(
            diagrams.prepared_json(self.cfg, self.flow)["mermaidVersion"], "11.0.0"
        )

    def test_an_unreadable_package_json_is_not_a_crash(self) -> None:
        installed = diagrams.cli_dir(self.cfg)
        installed.mkdir(parents=True, exist_ok=True)
        (installed / "package.json").write_text("{ not json", encoding="utf-8")
        self.assertIsNone(diagrams.installed_mermaid_version(self.cfg))


class BrandPack(Vault):
    def test_a_diagram_is_styled_by_its_own_reports_pack(self) -> None:
        (self.cfg.brand / "mono").mkdir(parents=True, exist_ok=True)
        (self.cfg.brand / "mono" / "brand.json").write_text(
            json.dumps({"colors": {"accent": "#111111"}}), encoding="utf-8"
        )
        self.quiet(
            scaffold.new_template, self.cfg, "mono-flow", source="base", copy_design=False
        )
        toml = self.cfg.templates / "mono-flow" / "template.toml"
        toml.write_text(
            toml.read_text(encoding="utf-8").replace(
                'brand = "default"', 'brand = "mono"'
            ),
            encoding="utf-8",
        )
        mono = self.write_report("internal/2026-01-02-mono", tpl="mono-flow")
        src = self.write_diagram(mono, "flow.mmd", FLOW)

        payload = diagrams.prepared_json(self.cfg, src)
        self.assertEqual(payload["pack"], "mono")
        self.assertIn("fill:#111111", payload["classDefs"]["em-accent"])
        self.assertIn("#111111", payload["cssText"])
        self.assertEqual(payload["configJson"]["themeVariables"]["nodeBorder"], "#111111")

        # The report next door is untouched: the pack follows the design.
        self.assertEqual(diagrams.prepared_json(self.cfg, self.flow)["pack"], "default")

    def test_a_diagram_outside_any_report_falls_back_to_the_default(self) -> None:
        stray = self.cfg.templates / "scratch.mmd"
        stray.parent.mkdir(parents=True, exist_ok=True)
        stray.write_text(FLOW, encoding="utf-8")
        self.assertEqual(diagrams.prepared_json(self.cfg, stray)["pack"], "default")


class Resolving(Vault):
    def test_an_absolute_path(self) -> None:
        self.assertEqual(diagrams.resolve_source(self.cfg, str(self.flow)), self.flow)

    def test_a_vault_relative_path(self) -> None:
        rel = self.flow.relative_to(self.cfg.root).as_posix()
        self.assertEqual(diagrams.resolve_source(self.cfg, rel), self.flow)

    def test_a_bare_file_name_when_it_is_unambiguous(self) -> None:
        self.assertEqual(diagrams.resolve_source(self.cfg, "flow.mmd"), self.flow)

    def test_a_bare_file_name_two_reports_share_is_an_error(self) -> None:
        other = self.write_report("internal/2026-01-03-other")
        self.write_diagram(other, "flow.mmd", FLOW)
        with self.assertRaises(diagrams.DiagramError) as caught:
            diagrams.resolve_source(self.cfg, "flow.mmd")
        self.assertIn("ambiguous", str(caught.exception))

    def test_a_report_target_holding_one_diagram(self) -> None:
        self.assertEqual(
            diagrams.resolve_source(self.cfg, "clients/acme/2026-01-01-audit"), self.flow
        )

    def test_a_target_holding_several_names_them(self) -> None:
        self.write_diagram(self.report, "second.mmd", SEQUENCE)
        with self.assertRaises(diagrams.DiagramError) as caught:
            diagrams.resolve_source(self.cfg, "clients/acme/2026-01-01-audit")
        self.assertIn("second.mmd", str(caught.exception))

    def test_a_missing_file_says_so_rather_than_hunting_for_a_report(self) -> None:
        with self.assertRaises(diagrams.DiagramError) as caught:
            diagrams.resolve_source(self.cfg, "reports/nowhere/diagrams/gone.mmd")
        self.assertIn("no such diagram", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
