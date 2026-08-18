"""Build-facts tests.

Two properties carry the module, and they pull in opposite directions.

The first is that the numbers must be *true*. A colophon is a trust device: it
tells a reader who cannot open the vault how much of the evidence was really
there when the build ran. A colophon that overstates — counting a source as
archived because a stale snapshot file exists, or a quotation as verified
because nothing checked it — is worse than no colophon at all, because it
launders an absence into a reassurance. So the counting tests are all written
around the cases where a lazy implementation would round upwards.

The second is that nothing here may ever fail a build. A missing git, an
unreadable bibliography, a typst binary that is not on PATH: each degrades to
`unknown` and is named in `gaps`, and the file is still written, because the
design reads it with Typst's `json()` and a missing path is a compile error.
Those two properties meet in `gather`, which is why it is tested by making each
group blow up in turn.

The last test compiles the colophon with typst. Everything else here reads
Python; that one is the only thing that can catch a Typst expression which
parses on one line and not on three, and the component is nothing but expressions.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import build as build_mod  # noqa: E402
from engine import facts, library, snapshot  # noqa: E402
from engine.config import load  # noqa: E402
from engine.workspace import reports  # noqa: E402

VAULT_TOML = """[vault]
reports = "reports"
"""

REPORT_ID = "audits/2026-08-18-colophon"

MAIN = """#import "/.build/design/base/report.typ": report
#import "/.build/design/base/components.typ": *

#show: report.with(
  title: "Colophon",
  sources: "/reports/audits/2026-08-18-colophon/sources.yml",
)

= Findings

A fact about the world @archived-page.

#srcquote(
  "Pricing is unchanged for existing customers through 2027.",
  source: [@archived-page],
  locator: "Pricing FAQ, question 4",
)
"""

SOURCES = """archived-page:
  type: Web
  title: "A page we archived"
  url: "https://example.invalid/pricing"

own-measurement:
  type: Misc
  title: "Something we measured ourselves"
  author: "own data"

never-fetched:
  type: Web
  title: "A page we cited and never kept"
  url: "https://example.invalid/press"
"""

# What the archived page says. The quotation above is in here word for word;
# the second test rewrites it so it is not.
ARCHIVED_TEXT = (
    "Acme pricing\n"
    "Pricing is unchanged for existing customers through 2027.\n"
    "Contact sales for volume terms.\n"
)


def has_typst() -> bool:
    return shutil.which(os.environ.get("TYPST_BIN") or "typst") is not None


class VaultCase(unittest.TestCase):
    """A real vault on disk: every fact here is read off the filesystem."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "report-maker.toml").write_text(VAULT_TOML, encoding="utf-8")
        self.folder = self.root / "reports" / REPORT_ID
        self.folder.mkdir(parents=True)
        (self.folder / "main.typ").write_text(MAIN, encoding="utf-8")
        (self.folder / "sources.yml").write_text(SOURCES, encoding="utf-8")
        self.cfg = load(self.root)
        self.report = reports(self.cfg, REPORT_ID)[0]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ── fixtures ─────────────────────────────────────────────────────────────

    def archive(self, key: str, text: str = ARCHIVED_TEXT, fetched: str = "2026-08-01") -> None:
        """Write a snapshot the way `cite` would, without a network in sight."""
        folder = snapshot.dir_for(self.report)
        folder.mkdir(parents=True, exist_ok=True)
        snapshot.text_path(self.report, key).write_text(text, encoding="utf-8")
        snapshot.raw_path(self.report, key).write_bytes(text.encode("utf-8"))
        snapshot.record_path(self.report, key).write_text(
            json.dumps(
                {
                    "key": key,
                    "url": "https://example.invalid/pricing",
                    "fetched": f"{fetched}T09:00:00+02:00",
                    "sha256": "0" * 64,
                    "status": 200,
                }
            ),
            encoding="utf-8",
        )

    def csv(self, name: str, text: str) -> Path:
        path = self.folder / "data" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def evidence(self):
        return facts.evidence(self.cfg, self.report)


# ── toolchain ────────────────────────────────────────────────────────────────


class Toolchain(unittest.TestCase):
    def test_an_absent_binary_is_unknown_and_not_an_error(self) -> None:
        # The whole point of recording the version is to name the cause of a
        # rendering that moved. Not finding it is a gap in the record, never a
        # reason to stop the build that would have filled it in.
        self.assertEqual(facts.typst_version("typst-that-is-not-installed"), facts.UNKNOWN)

    def test_a_binary_that_is_not_typst_is_unknown(self) -> None:
        # `false` exits non-zero and prints nothing; anything that is not a
        # version string has to read as no answer rather than as an answer.
        if shutil.which("false") is None:
            self.skipTest("no `false` on PATH")
        self.assertEqual(facts.typst_version("false"), facts.UNKNOWN)

    def test_the_real_typst_reports_its_version(self) -> None:
        if not has_typst():
            self.skipTest("typst is not installed")
        version = facts.typst_version(os.environ.get("TYPST_BIN") or "typst")
        self.assertNotEqual(version, facts.UNKNOWN)
        self.assertIn("typst", version.lower())


# ── evidence ─────────────────────────────────────────────────────────────────


class EvidenceCounts(VaultCase):
    def test_a_source_with_no_url_is_not_counted_as_unarchived(self) -> None:
        # Three entries: one archived, one web page nobody kept, and one own
        # measurement that could never be archived at all. Counting the third as
        # a failure would invent a finding out of the bibliography's own shape.
        self.archive("archived-page")
        found = self.evidence()
        self.assertEqual(found.sources, 3)
        self.assertEqual(found.archived, 1)
        self.assertEqual(found.unarchived, 1)

    def test_no_archive_at_all(self) -> None:
        found = self.evidence()
        self.assertEqual((found.archived, found.unarchived), (0, 2))
        self.assertEqual((found.archived_from, found.archived_to), ("", ""))

    def test_the_archive_window_spans_the_dates_actually_fetched(self) -> None:
        self.archive("archived-page", fetched="2026-08-01")
        self.archive("never-fetched", fetched="2026-07-14")
        found = self.evidence()
        self.assertEqual(found.archived, 2)
        self.assertEqual((found.archived_from, found.archived_to), ("2026-07-14", "2026-08-01"))

    def test_a_snapshot_for_a_key_no_longer_in_the_bibliography_is_not_counted(self) -> None:
        # A withdrawn source leaves its snapshot behind — nothing overwrites an
        # archive. Counting the leftovers would let a report's evidence grow by
        # deleting entries from its bibliography.
        self.archive("archived-page")
        self.archive("deleted-last-week")
        self.assertEqual(self.evidence().archived, 1)

    def test_density_comes_from_score(self) -> None:
        found = self.evidence()
        self.assertEqual(found.cited, 1)
        self.assertEqual(found.cited + found.assessed + found.unmarked, 1)
        self.assertEqual(found.density, 1.0)


class Quotations(VaultCase):
    def test_a_quotation_the_archive_carries_is_verified(self) -> None:
        self.archive("archived-page")
        self.assertEqual(facts.quotations(self.report), (1, 1))

    def test_a_quotation_the_archive_does_not_carry_is_not(self) -> None:
        # The E009 case. It must count as written and not as verified: the
        # colophon is the one place a reader learns that the words were checked,
        # so it may never report a check that failed as a check that passed.
        self.archive("archived-page", text="Prices rise for everyone in 2027.\n")
        self.assertEqual(facts.quotations(self.report), (1, 0))

    def test_a_quotation_with_nothing_archived_behind_it_is_not_verified(self) -> None:
        # Nothing checked it, nothing raised, and nothing stands behind it but
        # the writer's typing. Silence is not verification.
        self.assertEqual(facts.quotations(self.report), (1, 0))

    def test_a_report_with_no_quotations(self) -> None:
        (self.folder / "main.typ").write_text("= Findings\n\nProse.\n", encoding="utf-8")
        self.assertEqual(facts.quotations(self.report), (0, 0))


class DataInputs(VaultCase):
    def test_a_declared_file_that_produced_no_rows_is_named(self) -> None:
        # The failure this module exists for: the pipeline ran, the file was
        # written, the table was placed, and there was nothing in it. It has to
        # be visible in the document rather than in a log beside it.
        #
        # The file is genuinely empty rather than header-only, because
        # `data._has_header` cannot call a single row a header — a header-only
        # export therefore reads as one row here. That is a gap in `data.py`, not
        # a decision taken in this module.
        self.csv("filings.csv", "")
        found = facts.inputs(self.report)
        self.assertEqual((found.declared, found.with_rows), (1, 0))
        self.assertEqual(found.empty, ("data/filings.csv",))

    def test_a_file_with_rows_is_not(self) -> None:
        self.csv("filings.csv", "region,filings,late\nnorth,12,3\n")
        found = facts.inputs(self.report)
        self.assertEqual((found.declared, found.with_rows), (1, 1))
        self.assertEqual(found.empty, ())
        self.assertEqual(found.files[0].rows, 1)

    def test_a_report_with_no_data_at_all(self) -> None:
        found = facts.inputs(self.report)
        self.assertEqual((found.declared, found.with_rows, found.empty), (0, 0, ()))


# ── provenance ───────────────────────────────────────────────────────────────


class Provenance(VaultCase):
    def test_a_folder_that_is_not_a_repository_is_a_fact_not_an_error(self) -> None:
        # `dirty` is None rather than False: "nothing uncommitted" is a claim,
        # and there is nothing here to make it about.
        found = facts.provenance(self.cfg)
        self.assertFalse(found.repo)
        self.assertIsNone(found.dirty)
        self.assertEqual(found.revision, facts.UNKNOWN)
        self.assertNotEqual(found.built, facts.UNKNOWN)

    def test_a_real_repository_reports_its_revision_and_its_dirt(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        env = dict(
            os.environ,
            GIT_AUTHOR_NAME="Test Writer",
            GIT_AUTHOR_EMAIL="writer@example.invalid",
            GIT_COMMITTER_NAME="Test Writer",
            GIT_COMMITTER_EMAIL="writer@example.invalid",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_CONFIG_SYSTEM=os.devnull,
        )
        for args in (
            ("init", "--initial-branch=main"),
            ("add", "--all"),
            ("commit", "--message", "the vault"),
        ):
            subprocess.run(
                ["git", *args], cwd=self.root, env=env, capture_output=True, check=True
            )

        clean = facts.provenance(self.cfg)
        self.assertTrue(clean.repo)
        self.assertEqual(clean.branch, "main")
        self.assertFalse(clean.dirty)
        self.assertNotEqual(clean.revision, facts.UNKNOWN)

        (self.folder / "main.typ").write_text(MAIN + "\nMore prose.\n", encoding="utf-8")
        self.assertTrue(facts.provenance(self.cfg).dirty)


# ── gathering, and its refusal to fail ───────────────────────────────────────


class Gathering(VaultCase):
    def boom(self, name: str):
        """Replace one gatherer with one that raises, restoring it afterwards."""
        original = getattr(facts, name)

        def explode(*_args, **_kwargs):
            raise RuntimeError(f"{name} is broken")

        setattr(facts, name, explode)
        self.addCleanup(setattr, facts, name, original)

    def test_a_broken_group_is_named_and_the_rest_still_arrive(self) -> None:
        self.boom("evidence")
        found = facts.gather(self.cfg, self.report)
        self.assertEqual(found.gaps, ("evidence",))
        self.assertEqual(found.evidence.sources, 0)  # defaults, not garbage
        self.assertNotEqual(found.toolchain.python, facts.UNKNOWN)

    def test_every_group_broken_still_produces_a_record(self) -> None:
        for name in ("toolchain", "provenance", "evidence", "inputs"):
            self.boom(name)
        found = facts.gather(self.cfg, self.report)
        self.assertEqual(
            set(found.gaps), {"toolchain", "provenance", "evidence", "inputs"}
        )
        self.assertEqual(found.report, REPORT_ID)
        self.assertEqual(found.toolchain.typst, facts.UNKNOWN)

    def test_a_healthy_build_reports_no_gaps(self) -> None:
        self.assertEqual(facts.gather(self.cfg, self.report).gaps, ())


class Writing(VaultCase):
    def test_the_file_mirrors_the_report_tree(self) -> None:
        facts.write(self.cfg, self.report)
        path = self.root / ".build" / "facts" / f"{REPORT_ID}.json"
        self.assertTrue(path.is_file())
        self.assertEqual(facts.path_for(self.cfg, self.report), path)

    def test_the_path_a_report_passes_is_project_absolute(self) -> None:
        # Typst resolves a leading "/" against --root. Anything else breaks the
        # moment the report folder moves.
        self.assertEqual(
            facts.project_path(self.cfg, self.report),
            f"/.build/facts/{REPORT_ID}.json",
        )

    def test_the_json_is_the_shape_the_design_reads(self) -> None:
        facts.write(self.cfg, self.report)
        payload = json.loads(facts.path_for(self.cfg, self.report).read_text())
        self.assertEqual(
            set(payload),
            {"report", "toolchain", "provenance", "evidence", "inputs", "gaps"},
        )
        # Every key the Typst side reaches for, spelled the way it reaches for it.
        self.assertEqual(set(payload["toolchain"]), {"typst", "engine", "python"})
        self.assertEqual(
            set(payload["provenance"]), {"built", "repo", "revision", "branch", "dirty"}
        )
        for name in ("sources", "archived", "unarchived", "archived_from", "archived_to",
                     "quotations", "quotations_verified", "cited", "assessed",
                     "unmarked", "density"):
            self.assertIn(name, payload["evidence"])
        self.assertEqual(
            set(payload["inputs"]), {"declared", "with_rows", "empty", "files"}
        )

    def test_a_written_record_is_still_written_when_everything_failed(self) -> None:
        # The design reads this path unconditionally: no file is a compile error,
        # and an all-unknown colophon is the honest output for a build nobody
        # could describe.
        original = facts.gather

        def explode(*_args, **_kwargs):
            raise RuntimeError("nothing worked")

        facts.gather = lambda cfg, report: facts.Facts(
            report=report.id, gaps=("toolchain", "provenance", "evidence", "inputs")
        )
        self.addCleanup(setattr, facts, "gather", original)
        facts.write(self.cfg, self.report)
        self.assertTrue(facts.path_for(self.cfg, self.report).is_file())

    def test_build_never_fails_over_a_fact(self) -> None:
        # `build.write_facts` is the seam: a facts file we cannot write is worth
        # a line of output and nothing more.
        original = facts.write

        def explode(*_args, **_kwargs):
            raise OSError("read-only file system")

        facts.write = explode
        self.addCleanup(setattr, facts, "write", original)
        printed = io.StringIO()
        with redirect_stdout(printed):
            build_mod.write_facts(self.cfg, self.report)
        self.assertIn("no build facts", printed.getvalue())

    def test_the_facts_file_does_not_make_the_report_stale(self) -> None:
        # Otherwise every build would dirty its own input and the next one would
        # compile again, for ever.
        facts.write(self.cfg, self.report)
        path = facts.path_for(self.cfg, self.report)
        self.assertNotIn(path, self.report.inputs())


class Printing(VaultCase):
    def test_the_human_output_names_the_report_and_never_fails(self) -> None:
        printed = io.StringIO()
        with redirect_stdout(printed):
            code = facts.report_facts(self.cfg, [facts.gather(self.cfg, self.report)])
        self.assertEqual(code, 0)
        self.assertIn(REPORT_ID, printed.getvalue())
        self.assertIn("not under version control", printed.getvalue())

    def test_an_empty_vault_says_so(self) -> None:
        printed = io.StringIO()
        with redirect_stdout(printed):
            self.assertEqual(facts.report_facts(self.cfg, []), 0)
        self.assertIn("no reports", printed.getvalue())


# ── the Typst side ───────────────────────────────────────────────────────────


class Colophon(VaultCase):
    """The component itself, compiled.

    Typst is not Python: an expression continued on the next line with a leading
    `+` parses as a unary plus and fails at run time, in a document nobody
    compiled until the day somebody set `colophon:`. Nothing in Python can see
    that. So this compiles the component against real facts files — one from a
    healthy build, one where every group failed — and both must produce a page.
    """

    def compile_with(self, payload: dict) -> None:
        library.stage(self.cfg)
        (self.root / ".build" / "facts").mkdir(parents=True, exist_ok=True)
        target = self.root / ".build" / "facts" / "probe.json"
        target.write_text(json.dumps(payload), encoding="utf-8")
        probe = self.root / "probe.typ"
        probe.write_text(
            '#import "/.build/design/base/components.typ": colophon\n'
            '#colophon("/.build/facts/probe.json")\n',
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                os.environ.get("TYPST_BIN") or "typst",
                "compile",
                "--root",
                str(self.root),
                str(probe),
                str(self.root / "probe.pdf"),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"colophon did not compile:\n{result.stdout}{result.stderr}",
        )
        self.assertTrue((self.root / "probe.pdf").is_file())

    def setUp(self) -> None:
        super().setUp()
        if not has_typst():
            self.skipTest("typst is not installed")

    def test_a_full_record_compiles(self) -> None:
        self.archive("archived-page")
        self.csv("filings.csv", "")
        payload = facts.to_json(facts.gather(self.cfg, self.report))
        # The interesting rows only appear when there is something to say, so
        # assert the fixture really does exercise them before compiling it.
        self.assertEqual(payload["evidence"]["quotations"], 1)
        self.assertEqual(payload["inputs"]["empty"], ["data/filings.csv"])
        self.compile_with(payload)

    def test_a_record_of_a_build_nobody_could_describe_compiles(self) -> None:
        blank = facts.to_json(
            facts.Facts(
                report=REPORT_ID,
                gaps=("toolchain", "provenance", "evidence", "inputs"),
            )
        )
        self.compile_with(blank)

    def test_an_empty_object_compiles(self) -> None:
        # Every field read through `.at(…, default: …)`: a facts file from an
        # older engine must still print a colophon rather than fail the report.
        self.compile_with({})


if __name__ == "__main__":
    unittest.main()
