"""Engine unit tests.

They cover the parts that fail quietly: vault discovery, theme generation, and
the citation linter. A linter that misses a violation is worse than no linter,
because the build goes green and the rule stops being true.

The second half covers the seam between the modules and the command line. Every
other test file here calls an engine function directly, which means a module can
be perfectly correct and still be unreachable: a subcommand wired to the wrong
handler, a flag that parses but is never read, a `--json` path whose exit code
has quietly stopped agreeing with the human one. Those failures do not show up
in a module's own tests, and they are exactly what the app and an agent depend
on. So the CLI tests drive `engine.cli.parser()` and `engine.cli.main()` with
argv — in-process rather than through a subprocess, so a traceback survives and
a patched module can prove the call was actually made.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import brand, check, cli, library, scaffold, snapshot, vault  # noqa: E402
from engine.config import Config, load  # noqa: E402
from engine.workspace import Report, reports  # noqa: E402

#: The sample vault this repository ships. Read, never written: these tests use
#: it as the one piece of report content that a person actually wrote.
DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo-vault"


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


# ── the command line ─────────────────────────────────────────────────────────
#
# Everything below drives argv. A module that works and a command that reaches
# it are two different facts, and only the second one is what a person, the app
# and an agent actually use.


REPORT_HEAD = '#show: report.with(\n  title: "T",\n  sources: "/reports/r/sources.yml",\n)\n'

BIB_ENTRY = 'example-page:\n  type: Web\n  title: "The example page"\n'


class Cli(Vault):
    """A scratch vault, driven the way a person drives it: through argv."""

    def invoke(self, *argv: str) -> tuple[int, str, str]:
        """`report-maker -C <vault> …` in-process. Returns (code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["-C", str(self.root), *argv])
        return code, out.getvalue(), err.getvalue()

    def payload(self, *argv: str) -> tuple[dict, int]:
        """The parsed document a `--json` command printed, and its exit code.

        Parses the *whole* of stdout rather than looking for a JSON-shaped line
        in it, because `--json` implies quiet: one stray progress line and the
        app's `JSON.parse` fails on output a human would call fine.
        """
        code, out, err = self.invoke(*argv)
        self.assertEqual(err, "", f"{argv} wrote to stderr")
        try:
            return json.loads(out), code
        except json.JSONDecodeError as exc:
            self.fail(f"{argv} did not print one JSON document: {exc}\n{out}")

    def report(self, body: str, sources: str = BIB_ENTRY) -> Report:
        return self.write_report("r", REPORT_HEAD + body, sources)


def leaf_commands(parser: argparse.ArgumentParser, path: tuple[str, ...] = ()):
    """Every subcommand of the CLI, as (path, parser) pairs.

    Argparse exposes no public way to walk its own tree, so this reaches into
    `_actions`. The alternative is a hand-maintained list of commands, which is
    the very thing that goes stale without anyone noticing.
    """
    groups = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    if not groups:
        yield path, parser
        return
    for group in groups:
        for name, sub in group.choices.items():
            yield from leaf_commands(sub, (*path, name))


class TestCliParsing(unittest.TestCase):
    """Argv reaches the handler it names, with the flags it was given."""

    def setUp(self) -> None:
        self.ap = cli.parser()

    def parse(self, *argv: str) -> argparse.Namespace:
        return self.ap.parse_args(["-C", "/nowhere", *argv])

    def test_every_subcommand_is_wired_to_a_handler(self) -> None:
        # A subcommand added without `set_defaults(func=…)` parses happily and
        # then dies in main() with an AttributeError, which is a crash rather
        # than a usage message.
        found = list(leaf_commands(self.ap))
        # An empty walk would pass the assertion below without looking at
        # anything, which is the one way this test could stop being a test.
        self.assertGreater(len(found), 20)
        unwired = [
            "/".join(path) for path, sub in found if sub.get_default("func") is None
        ]
        self.assertEqual(unwired, [])

    def test_the_new_commands_reach_their_own_handler(self) -> None:
        # The eight commands this build added, plus the ones that grew a
        # subcommand tree. A command wired to the neighbouring handler still
        # parses, still exits 0, and does the wrong thing in silence.
        expected = {
            ("cite", "r", "https://example.com"): "cmd_cite",
            ("verify",): "cmd_verify",
            ("score",): "cmd_score",
            ("diff", "r"): "cmd_diff",
            ("html",): "cmd_html",
            ("sync",): "cmd_sync",
            ("brand", "list"): "cmd_brand_list",
            ("brand", "show"): "cmd_brand_show",
            ("brand", "new", "mono"): "cmd_brand_new",
            ("brand", "set", "colors.accent", "#123456"): "cmd_brand_set",
            ("brand", "preview"): "cmd_brand_preview",
            ("mcp",): "cmd_mcp",
            ("sources", "r"): "cmd_sources",
            ("find", "q"): "cmd_find",
            ("index",): "cmd_index",
            ("data", "add", "r", "n.csv"): "cmd_data_add",
            ("data", "list"): "cmd_data_list",
            ("data", "check"): "cmd_data_check",
            ("template", "install", "git@example.com:d.git"): "cmd_template_install",
            ("template", "update"): "cmd_template_update",
            ("template", "uninstall", "d"): "cmd_template_uninstall",
        }
        for argv, handler in expected.items():
            with self.subTest(command=" ".join(argv)):
                self.assertEqual(self.parse(*argv).func.__name__, handler)

    def test_check_takes_json_score_and_warn_only(self) -> None:
        args = self.parse("check", "clients/acme", "--json", "--score", "--warn-only")
        self.assertEqual(args.target, "clients/acme")
        self.assertTrue(args.json and args.score and args.warn_only)

    def test_all_takes_html(self) -> None:
        self.assertFalse(self.parse("all").html)
        self.assertTrue(self.parse("all", "--html").html)

    def test_status_and_state_are_the_same_flag(self) -> None:
        # The desktop shell asks for `--status`; the docs say `--state`. Both
        # have to reach the same attribute or one of the two is dead.
        self.assertTrue(self.parse("sync", "--status").state)
        self.assertTrue(self.parse("sync", "--state").state)

    def test_a_target_is_optional_where_the_command_can_take_the_vault(self) -> None:
        for command in ("check", "score", "verify", "html", "list"):
            with self.subTest(command=command):
                self.assertIsNone(self.parse(command).target)

    def test_a_target_is_required_where_one_report_is_meant(self) -> None:
        # `sources`, `diff` and `watch` answer about a single report. Defaulting
        # them to the whole vault would be a different question, quietly.
        for command in ("sources", "diff", "watch"):
            with self.subTest(command=command):
                # argparse prints its usage message to stderr on the way out.
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    self.parse(command)


class TestCliDispatch(Cli):
    """main() hands the parsed argv to the module, unchanged.

    Each of these patches the module function so the test says nothing about
    what the module does — only that the command reached it, carrying the
    values that were typed. Nothing here touches the network, git or Typst.
    """

    def test_cite_passes_every_option_through(self) -> None:
        with mock.patch.object(cli.cite_mod, "cite") as cited:
            code, _, _ = self.invoke(
                "cite", "r", "https://example.com/p",
                "--key", "example-page", "--type", "Report", "--no-snapshot",
            )
        self.assertEqual(code, 0)
        args, kwargs = cited.call_args
        self.assertEqual(args[1:], ("r", "https://example.com/p"))
        self.assertEqual(kwargs["key"], "example-page")
        self.assertEqual(kwargs["type_"], "Report")
        self.assertTrue(kwargs["no_snapshot"])

    def test_verify_passes_offline_and_refresh(self) -> None:
        with mock.patch.object(cli.verify_mod, "verify", return_value=[]) as verified:
            payload, code = self.payload("verify", "r", "--offline", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(verified.call_args.args[1], "r")
        self.assertEqual(verified.call_args.kwargs, {"offline": True, "refresh": False})
        self.assertEqual(payload["drifts"], [])

    def test_diff_defaults_to_the_previous_revision(self) -> None:
        with mock.patch.object(cli.diffing_mod, "diff", return_value=[]) as diffed:
            self.payload("diff", "r", "--json")
            self.assertEqual(diffed.call_args.kwargs["rev"], "HEAD~1")
            self.payload("diff", "r", "--rev", "v1.0", "--json")
            self.assertEqual(diffed.call_args.kwargs["rev"], "v1.0")

    def test_html_exports_the_named_target(self) -> None:
        with mock.patch.object(cli.html_mod, "export") as exported:
            code, _, _ = self.invoke("html", "clients/acme")
        self.assertEqual(code, 0)
        self.assertEqual(exported.call_args.args[1], "clients/acme")

    def test_sync_only_pushes_when_asked(self) -> None:
        result = {"committed": None, "pushed": False, "detail": "", "refused": None}
        with mock.patch.object(cli.gitsync_mod, "sync", return_value=result) as synced:
            self.payload("sync", "--json")
            self.assertFalse(synced.call_args.kwargs["do_push"])
            self.payload("sync", "--push", "-m", "note", "--json")
            self.assertTrue(synced.call_args.kwargs["do_push"])
            self.assertEqual(synced.call_args.kwargs["message"], "note")

    def test_mcp_serves_the_vault_it_was_started_on(self) -> None:
        # The server has no other way to learn which vault it is speaking for,
        # and it may not print a word to ask.
        with mock.patch.object(cli.mcp_mod, "serve", return_value=0) as served:
            code, out, _ = self.invoke("mcp")
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertEqual(served.call_args.args[0], str(self.root))

    def test_a_module_error_becomes_a_message_and_exit_1(self) -> None:
        # Every engine exception type has to be named in main()'s except clause;
        # one that is missing reaches the user as a traceback.
        self.report("Nothing here.#assess\n")
        code, out, err = self.invoke("html", "r")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("error: "), err)
        self.assertIn("report-maker pages", err)


class TestCheckJson(Cli):
    """`check --json`: the same verdict as the human path, in the app's shape."""

    CLEAN = "A fact @example-page.\nA judgement#assess\n"
    WARNED = "Nothing is cited here.#assess\n"       # W001: the entry is orphaned
    BROKEN = 'A fact @example-page.\n#image("a.png")\n'  # E002

    def codes_of(self, payload: dict) -> list[str]:
        return [finding["code"] for finding in payload["findings"]]

    def test_the_documented_shape(self) -> None:
        self.report(self.BROKEN)
        payload, code = self.payload("check", "--json")
        self.assertEqual(code, 1)
        self.assertEqual(set(payload), {"vault", "errors", "warnings", "findings"})
        self.assertEqual(payload["vault"], str(self.cfg.root))
        self.assertEqual(payload["errors"], 1)
        finding = payload["findings"][0]
        self.assertEqual(
            set(finding), {"level", "code", "path", "line", "message", "report"}
        )
        self.assertEqual(finding["code"], "E002")
        self.assertEqual(finding["report"], "r")
        # Vault-relative POSIX: the app hands this straight back to a file
        # channel that refuses anything outside the vault.
        self.assertEqual(finding["path"], "reports/r/main.typ")

    def test_the_exit_code_matches_the_human_path(self) -> None:
        for name, body, expected in (
            ("clean", self.CLEAN, 0),
            ("warning only", self.WARNED, 0),
            ("error", self.BROKEN, 1),
        ):
            with self.subTest(state=name):
                self.report(body)
                human, _, _ = self.invoke("check")
                machine, _, _ = self.invoke("check", "--json")
                self.assertEqual(human, expected)
                self.assertEqual(machine, expected)

    def test_warn_only_disarms_both_paths_alike(self) -> None:
        self.report(self.BROKEN)
        self.assertEqual(self.invoke("check", "--warn-only")[0], 0)
        payload, code = self.payload("check", "--json", "--warn-only")
        self.assertEqual(code, 0)
        # Disarmed, not silenced: the finding is still reported.
        self.assertEqual(self.codes_of(payload), ["E002"])

    def test_json_implies_quiet(self) -> None:
        # The human reporter's "1 error(s), 0 warning(s)" line is not JSON, and
        # `payload` would have failed to parse the output if it had been printed.
        self.report(self.BROKEN)
        payload, _ = self.payload("check", "--json")
        self.assertEqual(payload["errors"], 1)

    def test_score_rides_along_only_when_asked(self) -> None:
        self.report(self.CLEAN)
        self.assertNotIn("score", self.payload("check", "--json")[0])
        payload, _ = self.payload("check", "--json", "--score")
        self.assertEqual(payload["score"]["reports"][0]["id"], "r")

    def test_a_clean_vault_prints_an_empty_list_not_an_absent_one(self) -> None:
        self.report(self.CLEAN)
        payload, code = self.payload("check", "--json")
        self.assertEqual((payload["findings"], payload["errors"]), ([], 0))
        self.assertEqual(code, 0)


class TestAllRunsTheHtmlStep(Cli):
    """`all --html` exports the bundle, in the right place in the order."""

    STEPS = (
        ("library_mod", "stage", "stage"),
        ("diagrams_mod", "build", "diagrams"),
        ("build_mod", "build", "build"),
        ("pages_mod", "build", "pages"),
        ("html_mod", "export", "html"),
        ("manifest_mod", "build", "manifest"),
        ("check_mod", "check", "check"),
    )

    def order(self, *argv: str) -> list[str]:
        """The pipeline steps `all` ran, in the order it ran them.

        Each step is replaced by a recorder, because the question here is the
        order and the set — which is what `--html` changes — and Typst has
        nothing to say about either. Everything else in `cmd_all`, including
        the reporter that turns findings into an exit code, still runs.
        """
        seen: list[str] = []

        def recorder(name: str):
            def step(*args, **kwargs) -> list:
                seen.append(name)
                return []  # every step here returns a list or is ignored

            return step

        with ExitStack() as stack:
            for module, function, name in self.STEPS:
                stack.enter_context(
                    mock.patch.object(getattr(cli, module), function, recorder(name))
                )
            code, _, _ = self.invoke(*argv)
        self.assertEqual(code, 0)
        return seen

    def test_html_is_off_by_default(self) -> None:
        self.assertEqual(
            self.order("all"),
            ["stage", "diagrams", "build", "pages", "manifest", "check"],
        )

    def test_html_runs_after_the_pages_it_inlines_and_before_check(self) -> None:
        # After pages, because the bundle inlines the page PNGs; before check,
        # because check is the gate and stays last.
        self.assertEqual(
            self.order("all", "--html"),
            ["stage", "diagrams", "build", "pages", "html", "manifest", "check"],
        )

    @unittest.skipUnless(shutil.which("typst"), "typst is not installed")
    def test_the_whole_pipeline_end_to_end(self) -> None:
        self.quiet(scaffold.new_report, self.cfg, "Demo", slug="2026-01-01-demo")
        code, out, err = self.invoke("all", "--html")
        # A scaffold nobody has written yet is refused by E012 — its cover KPIs
        # and its `@example-page` citation are the starter's, not this report's.
        # Every step before `check` still ran, which is the point of this test:
        # the gate is last, so the artefacts exist either way.
        self.assertEqual(code, 1, err)
        self.assertIn("E012", out)
        self.assertIn("html", out)
        html = self.cfg.out / "2026-01-01-demo.html"
        self.assertTrue(html.is_file(), out)
        # Self-contained by contract: it has to work from file://, so a page
        # image is inlined rather than linked.
        self.assertIn("data:image/png;base64,", html.read_text(encoding="utf-8"))


class TestQuotedEvidence(Cli):
    """A quote, its archive, and the one word that breaks the pair.

    This is the rule that can catch a sentence which already looks sourced, and
    the only one whose failure mode is a plausible-looking report. Exercised
    through the CLI because that is where the app and CI meet it.
    """

    PAGE = b"<html><body><p>We cut onboarding time by 40 per cent.</p></body></html>"
    QUOTE = "We cut onboarding time by 40 per cent"

    def archived_report(self, quote: str) -> None:
        report = self.report(
            f'#srcquote(\n  "{quote}",\n  source: [@example-page],\n'
            '  locator: "Pricing, paragraph 2",\n)\n'
        )
        snapshot.write(
            report,
            "example-page",
            snapshot.Fetched(
                url="https://example.com/pricing",
                status=200,
                content_type="text/html; charset=utf-8",
                body=self.PAGE,
            ),
        )

    def test_a_quote_the_archive_carries_passes(self) -> None:
        self.archived_report(self.QUOTE)
        payload, code = self.payload("check", "--json")
        self.assertEqual(payload["findings"], [])
        self.assertEqual(code, 0)

    def test_one_changed_word_fails_with_e009(self) -> None:
        self.archived_report(self.QUOTE.replace("40", "90"))
        payload, code = self.payload("check", "--json")
        self.assertEqual(code, 1)
        finding = payload["findings"][0]
        self.assertEqual(finding["code"], "E009")
        self.assertEqual(finding["report"], "r")
        # The message hands back the fix rather than an investigation.
        self.assertIn("snapshots/example-page.txt", finding["message"])
        self.assertIn(self.QUOTE.casefold(), finding["message"].casefold())

    def test_the_snapshot_is_what_moves_with_the_report(self) -> None:
        # Evidence lives beside the report that cites it, so moving the folder
        # moves the archive. A vault-level cache would break on the first move.
        self.archived_report(self.QUOTE)
        archive = self.cfg.reports / "r" / "snapshots"
        self.assertEqual(
            sorted(p.name for p in archive.iterdir()),
            ["example-page.html", "example-page.json", "example-page.txt"],
        )


class TestEvidenceRail(Cli):
    """`score --json` paints the app's rail, so `lines` may not have holes.

    The rail is drawn one block per editor line. A missing entry is not a
    smaller rail — it is a rail that has slipped, and every line below the gap
    is then labelled with its neighbour's evidence.
    """

    def assert_covers_every_line(self, raw: str, lines: list[dict]) -> None:
        # An editor shows one more line than the file has newlines when it ends
        # in one: the empty last line the cursor can sit on.
        expected = len(raw.splitlines()) + (1 if raw.endswith("\n") else 0)
        self.assertEqual([entry["line"] for entry in lines], list(range(1, expected + 1)))
        self.assertLessEqual(
            {entry["kind"] for entry in lines},
            {"cited", "assessed", "unmarked", "neutral"},
        )

    @unittest.skipUnless(DEMO.is_dir(), "the demo vault is not present")
    def test_a_real_report_is_covered_line_for_line(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["-C", str(DEMO), "score", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertTrue(payload["reports"], "the demo vault has no reports to score")
        cfg = load(DEMO)
        for entry in payload["reports"]:
            with self.subTest(report=entry["id"]):
                raw = (cfg.reports / entry["id"] / "main.typ").read_text(encoding="utf-8")
                self.assert_covers_every_line(raw, entry["lines"])

    def test_a_file_that_does_not_end_in_a_newline(self) -> None:
        # The off-by-one that would otherwise only show up on a file somebody
        # saved from an editor that does not add the trailing newline.
        self.report("A fact @example-page. A judgement#assess")
        payload, _ = self.payload("score", "--json")
        raw = (self.cfg.reports / "r" / "main.typ").read_text(encoding="utf-8")
        self.assertFalse(raw.endswith("\n"))
        self.assert_covers_every_line(raw, payload["reports"][0]["lines"])

    def test_a_trailing_blank_line_is_still_a_line(self) -> None:
        self.report("A fact @example-page.\n\n\n")
        payload, _ = self.payload("score", "--json")
        raw = (self.cfg.reports / "r" / "main.typ").read_text(encoding="utf-8")
        self.assert_covers_every_line(raw, payload["reports"][0]["lines"])

    def test_the_class_of_a_line_is_the_worst_on_it(self) -> None:
        # A rail that shows the cited half of a line and hides the unmarked half
        # is a rail that lies about the sentence a reader has to check.
        self.report("A fact @example-page. And a bare assertion.\n")
        payload, _ = self.payload("score", "--json")
        raw = (self.cfg.reports / "r" / "main.typ").read_text(encoding="utf-8")
        wanted = next(
            number
            for number, line in enumerate(raw.splitlines(), 1)
            if "bare assertion" in line
        )
        lines = payload["reports"][0]["lines"]
        self.assertEqual(lines[wanted - 1]["kind"], "unmarked")


class TestVaultGuard(Cli):
    """A target names a report in this vault, or it names nothing.

    Report ids are resolved against the reports actually found on disk, never
    joined onto a path — which is what makes `../../etc` a lookup miss rather
    than a traversal. The app hands user input straight to these commands, so
    this is the boundary that keeps a vault a vault.
    """

    ESCAPES = ("..", "../..", "../../etc", "/etc", "reports/../../etc")

    def test_a_target_that_climbs_out_of_the_vault_is_refused(self) -> None:
        self.report("A fact @example-page.\n")
        for target in self.ESCAPES:
            with self.subTest(target=target):
                with self.assertRaises(SystemExit) as caught:
                    reports(self.cfg, target)
                self.assertIn("no such report", str(caught.exception))

    def test_the_command_line_refuses_it_too(self) -> None:
        self.report("A fact @example-page.\n")
        for command in ("check", "score", "build"):
            with self.subTest(command=command):
                with self.assertRaises(SystemExit):
                    self.invoke(command, "../../etc")

    def test_an_absolute_target_does_not_reach_outside(self) -> None:
        # `/etc` is read as the id "etc" — a report that does not exist here —
        # rather than as a path on this machine.
        (self.cfg.reports / "etc").mkdir(parents=True)
        (self.cfg.reports / "etc" / "main.typ").write_text(REPORT_HEAD, encoding="utf-8")
        found = reports(self.cfg, "/etc")
        self.assertEqual([r.id for r in found], ["etc"])
        self.assertTrue(found[0].folder.is_relative_to(self.cfg.reports))

    def test_a_hidden_folder_is_not_a_report(self) -> None:
        # `_` and `.` folders are drafts and scratch space. They are not built,
        # so naming one has to miss rather than half-work.
        for prefix in ("_drafts", ".trash"):
            self.write_report(f"{prefix}/2026-01-01-x", REPORT_HEAD)
        self.assertEqual(reports(self.cfg), [])
        with self.assertRaises(SystemExit):
            reports(self.cfg, "_drafts/2026-01-01-x")


if __name__ == "__main__":
    unittest.main()
