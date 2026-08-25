"""E015 — a symlink in a report folder that resolves outside the vault.

Typst is the sandbox this engine relies on. It cannot reach the network or the
shell, and it reads only under `--root`, which is always the vault: it refuses
`read("../../etc/passwd")` and `read("/../etc/passwd")` outright. It does not
refuse a symlink. A link named `leakdir` pointing at `/etc` is not a `..`, so
`read("/leakdir/passwd")` compiles, and the file is typeset into the PDF the
reader then downloads.

That was never reachable — the engine creates no links, the web layer will not
resolve a request path through one, and its `git` calls carry
`core.symlinks=false`. Three separate promises holding one property, which is
the shape a bug arrives in later: add an upload route or an archive extractor,
lose one promise, and the failure does not look like a path bug. It looks like a
report with somebody's private file set in it.

So these tests are about the property rather than about any one of the promises.
The distinction that carries the rule is between *escaping* and *internal*: a
link from one report to another's diagram is a legitimate thing somebody does,
and refusing it would cost a working vault to buy nothing, since typst can read
the target through its real path anyway.

    python3 -m unittest tests.test_check_symlink
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import check, scaffold  # noqa: E402
from engine.config import Config, load  # noqa: E402
from engine.workspace import Report  # noqa: E402

#: A report that breaks no other rule, so a fixture only ever carries the one
#: thing it is about and `codes()` can be asserted whole.
CLEAN_MAIN = """#import "/.build/design/base/report.typ": report

#show: report.with(
  title: "Acme pricing",
{status})

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


class Vault(unittest.TestCase):
    """A scratch vault, and a second directory standing in for the rest of the
    disk — a real one rather than `/etc`, so the tests say what they mean and
    still pass on a machine that keeps its secrets somewhere else."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        with redirect_stdout(io.StringIO()):
            scaffold.init(self.root)
        self.cfg: Config = load(self.root)

        self.outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.outside, True)
        (self.outside / "passwd").write_text("root:x:0:0\n", encoding="utf-8")
        (self.outside / "deeper").mkdir()
        (self.outside / "deeper" / "id_rsa").write_text("PRIVATE KEY\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def report(self, rid: str = "2026-01-01-pricing", status: str | None = None) -> Report:
        declared = f'  status: "{status}",\n' if status else ""
        folder = self.cfg.reports / rid
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "main.typ").write_text(
            CLEAN_MAIN.format(
                status=f'  sources: "/reports/{rid}/sources.yml",\n{declared}'
            ),
            encoding="utf-8",
        )
        (folder / "sources.yml").write_text(CLEAN_SOURCES, encoding="utf-8")
        return Report(id=rid, folder=folder, cfg=self.cfg)

    def findings(self, report: Report) -> list[check.Finding]:
        return check.check_report(self.cfg, report)

    def codes(self, report: Report) -> list[str]:
        return [f.code for f in self.findings(report)]

    def leaks(self, report: Report) -> list[check.Finding]:
        return [f for f in self.findings(report) if f.code == "E015"]


# ── the finding ──────────────────────────────────────────────────────────────


class AnEscapingLink(Vault):
    def test_a_link_to_a_file_outside_the_vault_is_e015(self) -> None:
        report = self.report()
        (report.folder / "passwd").symlink_to(self.outside / "passwd")
        self.assertEqual([f.code for f in self.leaks(report)], ["E015"])

    def test_a_link_to_a_directory_outside_the_vault_is_e015(self) -> None:
        """The shape the probe used: `leakdir -> /etc`, then
        `read("/reports/…/leakdir/passwd")`. A directory link is the dangerous
        one, because it does not have to name the file it exposes."""
        report = self.report()
        (report.folder / "leakdir").symlink_to(self.outside, target_is_directory=True)
        self.assertEqual([f.code for f in self.leaks(report)], ["E015"])

    def test_a_directory_link_is_reported_once_and_never_walked_into(self) -> None:
        """`os.walk(followlinks=False)` is load-bearing twice over: it keeps the
        finding about the link rather than about the four files behind it, and a
        link to `/` does not walk the disk."""
        report = self.report()
        (report.folder / "leakdir").symlink_to(self.outside, target_is_directory=True)
        found = self.leaks(report)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].path.name, "leakdir")

    def test_it_is_an_error_and_not_a_warning(self) -> None:
        """A warning does not fail a build, and a build that does not fail ships
        the PDF with the file in it."""
        report = self.report()
        (report.folder / "passwd").symlink_to(self.outside / "passwd")
        self.assertEqual([f.level for f in self.leaks(report)], ["error"])

    def test_a_relative_link_escaping_upwards_is_caught(self) -> None:
        """`read("../../x")` is refused by typst; a link spelled the same way is
        not, which is the whole gap."""
        report = self.report()
        depth = len(report.folder.resolve().relative_to(self.root.resolve()).parts)
        (report.folder / "up").symlink_to(Path(*[".."] * (depth + 1)) / "escaped")
        (self.root.parent / "escaped").mkdir(exist_ok=True)
        self.addCleanup(shutil.rmtree, self.root.parent / "escaped", True)
        self.assertEqual([f.code for f in self.leaks(report)], ["E015"])

    def test_a_link_to_a_link_is_followed_all_the_way_out(self) -> None:
        """One hop inside the vault does not launder the second hop out of it."""
        report = self.report()
        (self.outside / "hop").symlink_to(self.outside / "passwd")
        (report.folder / "innocent").symlink_to(self.outside / "hop")
        self.assertEqual([f.code for f in self.leaks(report)], ["E015"])

    def test_a_link_in_a_subfolder_is_caught(self) -> None:
        """The walk is the whole folder, not its top level. `diagrams/`,
        `snapshots/` and `data/` are part of the report and travel with it."""
        report = self.report()
        (report.folder / "diagrams").mkdir()
        (report.folder / "diagrams" / "shared").symlink_to(self.outside / "deeper" / "id_rsa")
        found = self.leaks(report)
        self.assertEqual([f.code for f in found], ["E015"])
        self.assertEqual(found[0].path.name, "shared")

    def test_a_sibling_directory_sharing_a_prefix_is_outside(self) -> None:
        """Containment is decided by path components, never by string prefix, so
        a vault at `…/vault` does not contain `…/vault-evil`."""
        report = self.report()
        evil = self.root.parent / f"{self.root.name}-evil"
        evil.mkdir(exist_ok=True)
        self.addCleanup(shutil.rmtree, evil, True)
        (evil / "secret").write_text("x\n", encoding="utf-8")
        (report.folder / "next-door").symlink_to(evil / "secret")
        self.assertEqual([f.code for f in self.leaks(report)], ["E015"])


# ── what is deliberately not a finding ───────────────────────────────────────


class AnInternalLink(Vault):
    """Somebody sharing one diagram between two reports is doing a reasonable
    thing, and typst could read the target through its real path anyway."""

    def test_a_link_to_another_report_in_the_vault_is_not_a_finding(self) -> None:
        other = self.report("2026-01-02-other")
        (other.folder / "diagrams").mkdir()
        shared = other.folder / "diagrams" / "flow.svg"
        shared.write_text("<svg/>", encoding="utf-8")

        report = self.report()
        (report.folder / "diagrams").mkdir()
        (report.folder / "diagrams" / "flow.svg").symlink_to(shared)
        self.assertEqual(self.leaks(report), [])

    def test_a_link_to_a_sibling_file_in_the_same_folder_is_not_a_finding(self) -> None:
        report = self.report()
        (report.folder / "notes.md").write_text("thinking\n", encoding="utf-8")
        (report.folder / "alias.md").symlink_to(report.folder / "notes.md")
        self.assertEqual(self.leaks(report), [])

    def test_a_link_to_a_directory_in_the_vault_is_not_a_finding(self) -> None:
        report = self.report()
        (report.folder / "brand").symlink_to(self.cfg.brand, target_is_directory=True)
        self.assertEqual(self.leaks(report), [])

    def test_a_report_with_no_links_at_all_is_clean(self) -> None:
        self.assertEqual(self.codes(self.report()), [])


class ABrokenLink(Vault):
    """A dangling link must not crash the walk. Where it *points* still decides
    whether it is a finding: the target of an escaping one is one `mkdir` away
    from existing, and the folder travels to machines where it already does."""

    def test_a_dangling_link_inside_the_vault_does_not_crash_and_is_not_a_finding(self) -> None:
        report = self.report()
        (report.folder / "dangling").symlink_to(report.folder / "never-written.csv")
        self.assertFalse((report.folder / "dangling").exists())
        self.assertEqual(self.codes(report), [])

    def test_a_dangling_link_out_of_the_vault_is_still_a_finding(self) -> None:
        report = self.report()
        gone = self.outside / "not-here-yet" / "passwd"
        (report.folder / "gone").symlink_to(gone)
        self.assertFalse((report.folder / "gone").exists())
        self.assertEqual([f.code for f in self.leaks(report)], ["E015"])

    def test_a_self_referential_link_does_not_crash_the_walk(self) -> None:
        """A loop resolves to nothing on any platform. Whatever it is judged to
        be, `check` has to come back rather than raise."""
        report = self.report()
        loop = report.folder / "loop"
        os.symlink("loop", loop)
        self.assertEqual(
            [f.code for f in self.findings(report) if f.code != "E015"], []
        )


# ── what the finding says, and what it does to a build ───────────────────────


class TheMessage(Vault):
    def test_it_names_the_target_and_the_read_that_would_work(self) -> None:
        report = self.report()
        (report.folder / "leakdir").symlink_to(self.outside, target_is_directory=True)
        message = self.leaks(report)[0].message
        self.assertIn(str(self.outside.resolve()), message)
        self.assertIn("typst", message)
        self.assertIn('read("/reports/2026-01-01-pricing/leakdir/…")', message)
        self.assertIn("PDF", message)

    def test_a_file_link_names_the_read_without_a_trailing_segment(self) -> None:
        """A directory link exposes everything under it, so the message shows
        `…/…`; a file link exposes one file, and showing a path that would not
        compile teaches the reader the rule is approximate."""
        report = self.report()
        (report.folder / "passwd").symlink_to(self.outside / "passwd")
        message = self.leaks(report)[0].message
        self.assertIn('read("/reports/2026-01-01-pricing/passwd")', message)
        self.assertNotIn("passwd/…", message)

    def test_the_finding_is_routable_back_to_its_report(self) -> None:
        report = self.report()
        (report.folder / "passwd").symlink_to(self.outside / "passwd")
        self.assertEqual([f.report for f in self.leaks(report)], [report.id])

    def test_the_path_prints_relative_to_the_vault(self) -> None:
        """It resolves outside the root, so the naive `resolve().relative_to()`
        would print this machine's absolute layout instead."""
        report = self.report()
        (report.folder / "passwd").symlink_to(self.outside / "passwd")
        printed = check.relative(self.leaks(report)[0].path, self.cfg.root)
        self.assertEqual(printed, "reports/2026-01-01-pricing/passwd")


class TheBuildGate(Vault):
    def test_a_whole_vault_check_reaches_it(self) -> None:
        """A rule only `check_report` can reach is a rule nothing runs."""
        report = self.report()
        (report.folder / "passwd").symlink_to(self.outside / "passwd")
        self.assertIn("E015", [f.code for f in check.check(self.cfg)])

    def test_it_fails_the_build(self) -> None:
        report = self.report()
        (report.folder / "passwd").symlink_to(self.outside / "passwd")
        with redirect_stdout(io.StringIO()):
            code = check.report_findings(self.cfg, self.findings(report))
        self.assertEqual(code, 1)

    def test_draft_does_not_soften_it(self) -> None:
        """`draft` is a writer saying "I know" about their own argument. This is
        a property of a folder that gets handed over, and the `status:` beside it
        may be the sender's word rather than the reader's."""
        report = self.report(status="draft")
        (report.folder / "passwd").symlink_to(self.outside / "passwd")
        found = self.leaks(report)
        self.assertEqual([f.level for f in found], ["error"])
        self.assertNotIn("draft", found[0].message)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(check.report_findings(self.cfg, self.findings(report)), 1)

    def test_draft_still_softens_everything_else(self) -> None:
        """The carve-out is one code wide. Nothing else moved."""
        report = self.report(status="draft")
        (report.folder / "main.typ").write_text(
            (report.folder / "main.typ").read_text(encoding="utf-8")
            + '\n#image("chart.png")\n',
            encoding="utf-8",
        )
        (report.folder / "passwd").symlink_to(self.outside / "passwd")
        levels = {f.code: f.level for f in self.findings(report)}
        self.assertEqual(levels["E002"], "warning")
        self.assertEqual(levels["E015"], "error")

    def test_final_is_refused_while_a_link_escapes(self) -> None:
        """E014 counts it, which only holds if E015 is added before the gate."""
        report = self.report(status="final")
        (report.folder / "passwd").symlink_to(self.outside / "passwd")
        codes = self.codes(report)
        self.assertIn("E015", codes)
        self.assertIn("E014", codes)


if __name__ == "__main__":
    unittest.main()
