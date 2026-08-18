"""Drift tests.

`verify` is the one command whose whole job is to tell you something you would
otherwise never find out, so the ways it can be wrong are all quiet ones:
reporting `ok` for a page that was rewritten, reporting `gone` for a host that
was merely rate-limiting us, or — worst — overwriting the archived copy that was
the evidence in the first place.

Every test here therefore drives a fake fetcher. Nothing in this module touches
the network, which is also the point of the injectable fetcher in the first
place: a verification pass that only works online is one nobody runs.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import snapshot, verify  # noqa: E402
from engine.config import DEFAULTS, Config  # noqa: E402
from engine.workspace import Report  # noqa: E402

# Long enough that a wholesale replacement is unmistakable by length alone, and
# structured so that a one-word edit is unmistakably a small one.
PARAGRAPH = (
    "The pricing page lists three plans. Starter is billed monthly with no "
    "commitment, Team adds shared workspaces and audit history, and Enterprise "
    "is quoted on request. Every plan includes unlimited reports, and the "
    "published limits apply per organisation rather than per seat. "
) * 6


def page(body: str, *, title: str = "Pricing") -> bytes:
    return (
        f"<html><head><title>{title}</title></head>"
        f"<body><p>{body}</p></body></html>"
    ).encode("utf-8")


def fetched(url: str, body: bytes, status: int = 200) -> snapshot.Fetched:
    return snapshot.Fetched(
        url=url,
        status=status,
        content_type="text/html; charset=utf-8",
        body=body,
        final_url=url,
    )


def fetcher(pages: dict[str, object]):
    """A fetcher over a fixed map. A value that is an exception is raised, which
    is how a dead host and a urllib failure are staged."""

    def fetch(url: str):
        answer = pages[url]  # a KeyError here is a bug in the test, not the code
        if isinstance(answer, BaseException):
            raise answer
        return answer

    return fetch


def never_called(url: str):
    raise AssertionError(f"offline mode fetched {url}")


class NotFound(Exception):
    """Stands in for `urllib.error.HTTPError`, which carries its status on
    `.code` — the shape `verify` reads without importing urllib."""

    code = 404


class Drift(unittest.TestCase):
    URL = "https://example.com/pricing"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = Config(root=self.root, data=DEFAULTS)
        self.report = self.write_report(
            "examples/2026-08-16-pricing",
            sources_yml=(
                "pricing:\n"
                "  type: Web\n"
                '  title: "Pricing"\n'
                "  url:\n"
                f"    value: {self.URL}\n"
                "    date: 2026-01-04\n"
                "\n"
                "own-measurement:\n"
                "  type: Misc\n"
                '  title: "Timings we took ourselves"\n'
            ),
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_report(self, rid: str, *, sources_yml: str) -> Report:
        folder = self.cfg.reports / rid
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "main.typ").write_text("= Pricing\n\nA claim @pricing.\n", encoding="utf-8")
        (folder / "sources.yml").write_text(sources_yml, encoding="utf-8")
        return Report(id=rid, folder=folder, cfg=self.cfg)

    def snapshot_original(self, body: bytes | None = None) -> dict:
        return snapshot.write(
            self.report, "pricing", fetched(self.URL, body or page(PARAGRAPH))
        )

    def run_verify(self, pages: dict[str, object], **kwargs) -> list[verify.Drift]:
        return verify.verify(self.cfg, fetch=fetcher(pages), **kwargs)

    def only(self, drifts: list[verify.Drift]) -> verify.Drift:
        # A source with no URL is not drift — it is a measurement we took, and
        # there is nothing on the web to re-read. It must not appear at all.
        self.assertEqual([d.key for d in drifts], ["pricing"])
        return drifts[0]

    def printed(self, drifts: list[verify.Drift]) -> tuple[str, int]:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = verify.report_drift(self.cfg, drifts)
        return buffer.getvalue(), code

    # ── the states ───────────────────────────────────────────────────────────

    def test_an_unchanged_page_is_ok(self) -> None:
        self.snapshot_original()
        drift = self.only(self.run_verify({self.URL: fetched(self.URL, page(PARAGRAPH))}))
        self.assertEqual(drift.state, "ok")
        self.assertEqual(drift.similarity, 1.0)
        self.assertEqual(drift.fetched, snapshot.read_record(self.report, "pricing")["fetched"])
        self.assertEqual(self.printed([drift])[1], 0)

    def test_a_small_edit_is_changed_and_still_reads_as_the_same_page(self) -> None:
        self.snapshot_original()
        edited = PARAGRAPH.replace("quoted on request", "quoted on enquiry", 1)
        drift = self.only(self.run_verify({self.URL: fetched(self.URL, page(edited))}))

        self.assertEqual(drift.state, "changed")
        self.assertGreater(drift.similarity, 0.9)
        # The percentage is what tells a reader not to bother re-reading it, so
        # it has to survive into the human output.
        output, code = self.printed([drift])
        self.assertIn("%", output)
        self.assertIn("changed", output)
        self.assertEqual(code, 0)  # a page changing is normal — the snapshot holds

    def test_a_rewritten_page_is_changed_with_low_similarity(self) -> None:
        self.snapshot_original()
        stub = page("We have moved. See our new home.", title="Moved")
        drift = self.only(self.run_verify({self.URL: fetched(self.URL, stub)}))

        self.assertEqual(drift.state, "changed")
        self.assertLess(drift.similarity, 0.5)

    def test_a_404_is_gone_and_that_is_the_only_thing_that_fails(self) -> None:
        self.snapshot_original()
        drift = self.only(self.run_verify({self.URL: fetched(self.URL, b"", status=404)}))
        self.assertEqual(drift.state, "gone")
        self.assertIsNone(drift.similarity)

        output, code = self.printed([drift])
        self.assertEqual(code, 1)
        # The advice matters: the instinct on a dead link is to delete the
        # citation, which is the one response that makes the report less true.
        self.assertIn("dead link", output)
        self.assertIn("snapshot", output)

    def test_a_fetcher_that_raises_its_status_is_read_the_same_way(self) -> None:
        # urllib raises for a 4xx rather than returning it, so both shapes have
        # to reach the same verdict.
        self.snapshot_original()
        drift = self.only(self.run_verify({self.URL: NotFound("gone")}))
        self.assertEqual(drift.state, "gone")

    def test_a_broken_host_is_an_error_not_a_dead_link(self) -> None:
        self.snapshot_original()
        drift = self.only(self.run_verify({self.URL: OSError("nodename nor servname provided")}))
        self.assertEqual(drift.state, "error")
        self.assertIn("nodename", drift.detail)
        self.assertEqual(self.printed([drift])[1], 0)  # we learned nothing — do not fail

    def test_a_bot_wall_is_an_error_not_a_dead_link(self) -> None:
        self.snapshot_original()
        drift = self.only(self.run_verify({self.URL: fetched(self.URL, b"nope", status=403)}))
        self.assertEqual(drift.state, "error")
        self.assertEqual(self.printed([drift])[1], 0)

    def test_a_source_with_no_snapshot_is_reported_without_fetching(self) -> None:
        drift = self.only(self.run_verify({}))  # an empty map — a fetch would KeyError
        self.assertEqual(drift.state, "unsnapshotted")
        self.assertIsNone(drift.fetched)
        self.assertIn("cite", drift.detail)
        self.assertEqual(self.printed([drift])[1], 0)

    def test_offline_reports_every_archived_source_without_dialling(self) -> None:
        self.snapshot_original()
        drifts = verify.verify(self.cfg, fetch=never_called, offline=True)
        drift = self.only(drifts)
        self.assertEqual(drift.state, "offline")
        self.assertIsNone(drift.similarity)
        self.assertIn("2026", drift.fetched or "")
        self.assertEqual(self.printed(drifts)[1], 0)

    def test_offline_still_knows_what_was_never_archived(self) -> None:
        # No network is needed to see a gap in your own vault.
        drift = self.only(verify.verify(self.cfg, fetch=never_called, offline=True))
        self.assertEqual(drift.state, "unsnapshotted")

    # ── the archive ──────────────────────────────────────────────────────────

    def test_refresh_keeps_the_old_snapshot_and_writes_a_new_one(self) -> None:
        original = self.snapshot_original()
        folder = snapshot.dir_for(self.report)
        before = (folder / "pricing.html").read_bytes()

        replacement = page("We have moved. See our new home.", title="Moved")
        drift = self.only(
            self.run_verify({self.URL: fetched(self.URL, replacement)}, refresh=True)
        )
        self.assertEqual(drift.state, "changed")

        kept = folder / f"pricing.{original['fetched'][:10]}.html"
        self.assertTrue(kept.is_file(), sorted(p.name for p in folder.iterdir()))
        self.assertEqual(kept.read_bytes(), before)  # evidence, byte for byte
        self.assertEqual((folder / "pricing.html").read_bytes(), replacement)

        # The archived record keeps its own sha, so the old fetch stays citable.
        fresh = snapshot.read_record(self.report, "pricing")
        self.assertNotEqual(fresh["sha256"], original["sha256"])
        self.assertIn("re-archived", drift.detail)

    def test_refresh_twice_in_a_day_never_overwrites_an_archived_copy(self) -> None:
        self.snapshot_original()
        folder = snapshot.dir_for(self.report)
        first = page("First rewrite of the page.")
        second = page("Second rewrite of the page, different again.")

        self.run_verify({self.URL: fetched(self.URL, first)}, refresh=True)
        self.run_verify({self.URL: fetched(self.URL, second)}, refresh=True)

        archived = sorted(
            path.read_bytes()
            for path in folder.glob("pricing.*.html")
            if path.name != "pricing.html"
        )
        self.assertEqual(len(archived), 2)
        self.assertIn(page(PARAGRAPH), archived)
        self.assertIn(first, archived)
        self.assertEqual((folder / "pricing.html").read_bytes(), second)

    def test_a_key_a_filesystem_will_not_take_is_still_preserved(self) -> None:
        # A hayagriva key may contain `:` and `+`, which snapshot.py maps out of
        # the filename. Deciding that mapping a second time here would mean
        # looking for a file that was never written, concluding there was
        # nothing to keep, and letting the refresh overwrite the evidence.
        url = "https://example.com/iso"
        report = self.write_report(
            "examples/2026-08-16-standards",
            sources_yml=f'iso:9001+2015:\n  type: Web\n  url: {url}\n',
        )
        original = snapshot.write(report, "iso:9001+2015", fetched(url, page(PARAGRAPH)))
        folder = snapshot.dir_for(report)

        verify.verify(
            self.cfg,
            report.id,
            fetch=fetcher({url: fetched(url, page("Withdrawn."))}),
            refresh=True,
        )

        kept = [p for p in folder.glob("*.html") if p.name != "iso-9001-2015.html"]
        self.assertEqual(len(kept), 1, sorted(p.name for p in folder.iterdir()))
        self.assertEqual(kept[0].read_bytes(), page(PARAGRAPH))
        self.assertIn(original["fetched"][:10], kept[0].name)

    def test_without_refresh_the_snapshot_is_left_exactly_as_it_was(self) -> None:
        self.snapshot_original()
        folder = snapshot.dir_for(self.report)
        before = {path.name: path.read_bytes() for path in folder.iterdir()}

        self.run_verify({self.URL: fetched(self.URL, page("Something else entirely."))})

        after = {path.name: path.read_bytes() for path in folder.iterdir()}
        self.assertEqual(before, after)

    # ── output ───────────────────────────────────────────────────────────────

    def test_json_carries_every_state_including_the_zeros(self) -> None:
        self.snapshot_original()
        payload = verify.to_json(
            self.run_verify({self.URL: fetched(self.URL, page(PARAGRAPH))})
        )
        self.assertEqual(set(payload["counts"]), set(verify.STATES))
        self.assertEqual(payload["counts"]["ok"], 1)
        self.assertEqual(payload["counts"]["gone"], 0)
        row = payload["drifts"][0]
        self.assertEqual(
            set(row),
            {"report", "key", "url", "state", "detail", "fetched", "similarity"},
        )
        self.assertEqual(row["report"], self.report.id)
        self.assertEqual(row["url"], self.URL)

    def test_a_vault_with_nothing_to_verify_says_so_and_passes(self) -> None:
        output, code = self.printed([])
        self.assertIn("nothing to verify", output)
        self.assertEqual(code, 0)


class Similarity(unittest.TestCase):
    def test_identical_text_is_one(self) -> None:
        self.assertEqual(verify.text_similarity(PARAGRAPH, PARAGRAPH), 1.0)

    def test_a_short_replacement_of_a_long_page_scores_low(self) -> None:
        self.assertLess(verify.text_similarity(PARAGRAPH, "Moved."), 0.1)

    def test_a_page_that_changed_is_never_printed_as_100_percent_similar(self) -> None:
        # "changed — 100% similar" reads as a contradiction, and a number that
        # contradicts the word beside it is a number people learn to ignore.
        self.assertEqual(verify._percent(0.999), ">99%")
        self.assertEqual(verify._percent(1.0), "100%")
        self.assertEqual(verify._percent(0.0001), "<1%")
        self.assertEqual(verify._percent(0.0), "0%")
        self.assertEqual(verify._percent(None), "—")


if __name__ == "__main__":
    unittest.main()
