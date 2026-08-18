"""Search tests.

Three things have to hold, and the rest is detail.

Ranking has to put the obvious answer first — a report *named* for the word
above one that merely mentions it — because a search whose first screen is
noise is a search nobody uses twice. The query language has to mean what it
says: a quoted phrase is adjacency, `-word` is exclusion, `kind:` is a filter,
and each of them is asserted against a document that would match without it.

And the incremental index has to be provably incremental. The test for it
rewrites a file *behind* the index — same size, same mtime — and asserts the
stale answer, because that is the only way to prove a file was not reopened;
asserting that the results are still right would pass just as happily on a
full rebuild.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import scaffold, search  # noqa: E402
from engine.config import Config, load  # noqa: E402

# A report whose prose says "pricing" twice and whose title does not. Written
# with the machinery a real report carries — the import, the metadata header,
# a helper call, a citation — so the scrub is exercised rather than assumed.
BODY_REPORT = """// A comment mentioning kumquat, which is not prose.
#import "/.build/design/base/report.typ": report
#import "/.build/design/base/components.typ": *

#show: report.with(
  title: "Market landscape",
  author: "Youniss Kandah",
  sources: "/reports/market/sources.yml",
)

= Findings

The published pricing page lists three tiers @vendor-pricing. Enterprise
pricing is quoted on request only @vendor-pricing.

#srcfig(
  caption: [Tier comparison],
  source: [@vendor-pricing],
)[Nothing to see]

#srcimage(
  "/reports/market/figures/tiers.png",
  caption: [Tier chart],
  source: [@vendor-pricing],
  alt: [A chart],
)
"""

# The same shape, but the word is in the title and nowhere in the body.
TITLE_REPORT = """#import "/.build/design/base/report.typ": report

#show: report.with(
  title: "Pricing strategy",
  sources: "/reports/strategy/sources.yml",
)

= Findings

The vendor publishes three tiers and a quoted tier @vendor-pricing.
"""

SOURCES = """vendor-pricing:
  type: Web
  title: "Vendor — plans and pricing"
  author: "Vendor Ltd"
  url:
    value: https://vendor.example/pricing
    date: 2026-08-01
  note: "Reviewed on desktop; no enterprise figures published."
"""

SNAPSHOT = (
    "Vendor plans and pricing. Starter, Team and Business tiers are listed "
    "with monthly prices. Enterprise pricing is available on request and no "
    "figure appears anywhere on this page.\n"
)

DIAGRAM = """flowchart LR
  A["Collect pricing evidence"] --> B{Published?}
  B -->|yes| C[Record the figure]
  B -->|no| D[Report the absence]
  class A em-accent
  classDef em-accent fill:#2E5A88,stroke:#1B3A5C,color:#FFFFFF
"""


class Vault(unittest.TestCase):
    """A scratch vault with two reports, a bibliography, a snapshot, a diagram."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        with redirect_stdout(io.StringIO()):
            scaffold.init(self.root)
        self.cfg: Config = load(self.root)

        self.market = self.write("market", BODY_REPORT, SOURCES)
        self.strategy = self.write("strategy", TITLE_REPORT)

        snapshots = self.market / "snapshots"
        snapshots.mkdir()
        (snapshots / "vendor-pricing.txt").write_text(SNAPSHOT, encoding="utf-8")
        (snapshots / "vendor-pricing.json").write_text(
            json.dumps(
                {
                    "key": "vendor-pricing",
                    "url": "https://vendor.example/pricing",
                    "fetched": "2026-08-01T09:00:00+00:00",
                    "sha256": "0" * 64,
                    "title": "Vendor — plans and pricing",
                }
            ),
            encoding="utf-8",
        )

        diagrams = self.market / "diagrams"
        diagrams.mkdir()
        (diagrams / "evidence-flow.mmd").write_text(DIAGRAM, encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, rid: str, main: str, sources: str = "") -> Path:
        folder = self.cfg.reports / rid
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "main.typ").write_text(main, encoding="utf-8")
        if sources:
            (folder / "sources.yml").write_text(sources, encoding="utf-8")
        return folder

    def find(self, query: str, **kwargs) -> list[search.Hit]:
        return search.find(self.cfg, query, **kwargs)

    def kinds(self, hits) -> set[str]:
        return {hit.kind for hit in hits}


class Indexing(Vault):
    def test_an_empty_vault_has_no_index_until_one_is_built(self) -> None:
        self.assertIsNone(search.load_index(self.cfg))
        built = search.build_index(self.cfg)
        self.assertEqual(built["version"], search.INDEX_VERSION)
        self.assertTrue(search.index_path(self.cfg).is_file())
        self.assertEqual(search.load_index(self.cfg)["docs"].keys(), built["docs"].keys())

    def test_every_kind_is_indexed(self) -> None:
        docs = search.build_index(self.cfg)["docs"].values()
        self.assertEqual({doc["kind"] for doc in docs}, set(search.KINDS))

    def test_typst_machinery_is_not_indexed(self) -> None:
        # The import path, the helper names, the argument names, the image path,
        # the citation key and the comment are all in main.typ; not one of them
        # is something the author wrote.
        for noise in ("import", "srcfig", "caption", "figures", "png", "kumquat", "datetime"):
            self.assertEqual(
                self.find(noise, kinds=["report"]), [], f"{noise} reached the index"
            )
        # …while the prose around them survives intact, captions included.
        self.assertTrue(self.find("published tiers", kinds=["report"]))
        self.assertTrue(self.find('"tier chart"', kinds=["report"]))

    def test_a_diagram_contributes_labels_and_not_styling(self) -> None:
        hits = self.find("evidence", kinds=["diagram"])
        self.assertEqual([hit.key for hit in hits], ["evidence-flow"])
        self.assertIn("Collect pricing evidence", hits[0].excerpt)
        for noise in ("classDef", "flowchart", "stroke"):
            self.assertEqual(self.find(noise, kinds=["diagram"]), [], noise)

    def test_a_source_is_searchable_by_every_field_it_carries(self) -> None:
        for term, description in (
            ("plans", "title"),
            ("vendor", "author"),
            ("desktop", "note"),
            ("vendor.example", "url"),
        ):
            hits = self.find(f"{term} kind:source")
            self.assertEqual([hit.key for hit in hits], ["vendor-pricing"], description)


class Ranking(Vault):
    def test_a_title_match_outranks_a_body_match(self) -> None:
        hits = [hit for hit in self.find("pricing") if hit.kind == "report"]
        self.assertEqual([hit.report for hit in hits], ["strategy", "market"])
        self.assertGreater(hits[0].score, hits[1].score)

    def test_every_term_must_be_present(self) -> None:
        # "tiers" is in both reports; "enterprise" only in the one.
        self.assertEqual(
            {hit.report for hit in self.find("tiers", kinds=["report"])},
            {"market", "strategy"},
        )
        self.assertEqual(
            [hit.report for hit in self.find("tiers enterprise", kinds=["report"])],
            ["market"],
        )

    def test_a_quoted_phrase_requires_adjacency(self) -> None:
        loose = self.find("pricing page kind:report")
        self.assertEqual([hit.report for hit in loose], ["market"])
        self.assertEqual([hit.report for hit in self.find('"pricing page" kind:report')], ["market"])
        # Both words are in the snapshot, but never next to each other.
        self.assertTrue(self.find("pricing page kind:snapshot"))
        self.assertEqual(self.find('"pricing page" kind:snapshot'), [])

    def test_a_phrase_match_scores_above_the_same_words_apart(self) -> None:
        phrase = self.find('"pricing page"')[0]
        loose = next(hit for hit in self.find("pricing page") if hit.path == phrase.path)
        self.assertGreater(phrase.score, loose.score)

    def test_a_leading_minus_excludes(self) -> None:
        self.assertIn("market", {hit.report for hit in self.find("pricing kind:report")})
        self.assertEqual(
            [hit.report for hit in self.find("pricing -enterprise kind:report")],
            ["strategy"],
        )

    def test_an_excluded_phrase_only_excludes_the_adjacent_form(self) -> None:
        self.assertEqual(self.find('pricing -"pricing page" kind:report')[0].report, "strategy")
        self.assertTrue(self.find('pricing -"page pricing" kind:report'))

    def test_limit_caps_the_result_list(self) -> None:
        self.assertGreater(len(self.find("pricing")), 1)
        self.assertEqual(len(self.find("pricing", limit=1)), 1)


class Filtering(Vault):
    def test_kind_in_the_query_narrows(self) -> None:
        self.assertEqual(self.kinds(self.find("pricing kind:snapshot")), {"snapshot"})
        self.assertGreater(len(self.kinds(self.find("pricing"))), 1)

    def test_kind_in_the_call_narrows(self) -> None:
        self.assertEqual(self.kinds(self.find("pricing", kinds=["source"])), {"source"})

    def test_the_two_filters_intersect(self) -> None:
        self.assertEqual(self.find("pricing kind:snapshot", kinds=["source"]), [])

    def test_a_plural_kind_is_accepted_and_a_wrong_one_is_an_error(self) -> None:
        self.assertEqual(self.kinds(self.find("pricing kind:snapshots")), {"snapshot"})
        with self.assertRaises(search.SearchError):
            self.find("pricing kind:pdf")
        with self.assertRaises(search.SearchError):
            self.find("pricing", kinds=["pdf"])
        # Negating a kind would filter the opposite way round if it were quietly
        # accepted, so it is refused instead.
        with self.assertRaises(search.SearchError):
            self.find("pricing -kind:source")

    def test_a_query_with_nothing_to_match_is_an_error(self) -> None:
        for empty in ("", "   ", "-pricing", "kind:report"):
            with self.assertRaises(search.SearchError):
                self.find(empty)


class Locations(Vault):
    def test_a_report_hit_carries_its_line_in_main_typ(self) -> None:
        hit = next(hit for hit in self.find("pricing") if hit.report == "market" and hit.kind == "report")
        lines = (self.market / "main.typ").read_text(encoding="utf-8").splitlines()
        self.assertIn("pricing", lines[hit.line - 1].lower())
        self.assertIsNone(hit.offset)
        self.assertEqual(hit.path, "reports/market/main.typ")

    def test_a_snapshot_hit_carries_an_offset_and_no_line(self) -> None:
        hit = self.find("pricing kind:snapshot")[0]
        self.assertIsNone(hit.line)
        self.assertIsNotNone(hit.offset)
        text = (self.market / "snapshots/vendor-pricing.txt").read_text(encoding="utf-8")
        self.assertTrue(text[hit.offset :].lower().startswith("pricing"))
        # The record beside the .txt names the page and dates the capture; both
        # travel with the hit, because "as of when" is half of what it says.
        self.assertEqual(hit.title, "Vendor — plans and pricing")
        self.assertEqual(hit.fetched, "2026-08-01T09:00:00+00:00")

    def test_a_snapshot_with_no_record_is_still_indexed(self) -> None:
        (self.market / "snapshots/vendor-pricing.json").unlink()
        hit = self.find("pricing kind:snapshot")[0]
        self.assertEqual(hit.title, "vendor-pricing")
        self.assertIsNone(hit.fetched)

    def test_a_source_hit_carries_the_line_of_its_key(self) -> None:
        hit = self.find("desktop kind:source")[0]
        self.assertEqual(hit.line, 1)  # `vendor-pricing:` is the first line
        self.assertEqual(hit.key, "vendor-pricing")

    def test_marks_are_offsets_into_the_excerpt(self) -> None:
        hit = self.find("enterprise kind:snapshot")[0]
        self.assertTrue(hit.marks)
        for start, end in hit.marks:
            self.assertEqual(hit.excerpt[start:end].casefold(), "enterprise")

    def test_an_excerpt_prefers_the_window_covering_both_terms(self) -> None:
        hit = self.find("enterprise pricing kind:snapshot")[0]
        self.assertIn("Enterprise pricing", hit.excerpt)
        self.assertEqual(len({hit.excerpt[s:e].casefold() for s, e in hit.marks}), 2)

    def test_a_match_deep_in_a_long_page_is_still_located_exactly(self) -> None:
        # Only the winning window is tokenised, so the arithmetic that maps a
        # window back onto the whole document is the part worth pinning down.
        filler = "The vendor publishes tiers and prices. " * 400
        page = self.market / "snapshots/long-page.txt"
        page.write_text(f"{filler}Kumquat futures are quoted separately.\n", encoding="utf-8")

        hit = self.find("kumquat kind:snapshot")[0]
        self.assertEqual(hit.key, "long-page")
        self.assertTrue(page.read_text(encoding="utf-8")[hit.offset :].startswith("Kumquat"))
        self.assertTrue(hit.excerpt.startswith("…"))
        self.assertIn("Kumquat futures", hit.excerpt)
        self.assertEqual(
            [hit.excerpt[start:end] for start, end in hit.marks], ["Kumquat"]
        )


class Incremental(Vault):
    def test_an_unchanged_file_is_never_reopened(self) -> None:
        first = search.build_index(self.cfg)
        path = self.market / "snapshots/vendor-pricing.txt"
        before = path.stat()

        # Same length, same mtime — the file changed but its stat did not, which
        # is exactly the case a stat-based index is entitled to miss. Asserting
        # the *stale* answer is the only proof that it never read the file.
        rewritten = SNAPSHOT.replace("Starter", "Kumquat")
        self.assertEqual(len(rewritten), len(SNAPSHOT))
        path.write_text(rewritten, encoding="utf-8")
        os.utime(path, (before.st_atime, before.st_mtime))

        second = search.build_index(self.cfg)
        self.assertEqual(second["built"], first["built"])  # not even rewritten
        self.assertEqual(self.find("kumquat kind:snapshot"), [])

        forced = search.build_index(self.cfg, force=True)
        self.assertNotEqual(forced["built"], first["built"])
        self.assertEqual(len(self.find("kumquat kind:snapshot")), 1)

    def test_a_changed_file_is_reindexed(self) -> None:
        search.build_index(self.cfg)
        self.assertEqual(self.find("kumquat kind:snapshot"), [])

        path = self.market / "snapshots/vendor-pricing.txt"
        path.write_text(SNAPSHOT + "Kumquat futures are also discussed.\n", encoding="utf-8")
        os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 2))

        hits = self.find("kumquat kind:snapshot")
        self.assertEqual([hit.key for hit in hits], ["vendor-pricing"])
        # The rest of the document is still there, and still findable.
        self.assertEqual(len(self.find("enterprise kind:snapshot")), 1)

    def test_a_refreshed_snapshot_record_reindexes_its_text(self) -> None:
        # `verify --refresh` rewrites the record without necessarily touching the
        # text. The pair is indexed as one unit, so the new capture date lands.
        search.build_index(self.cfg)
        record = self.market / "snapshots/vendor-pricing.json"
        stored = json.loads(record.read_text(encoding="utf-8"))
        stored["title"] = "Vendor — plans, pricing and kumquats"
        stored["fetched"] = "2026-09-09T09:00:00+00:00"
        record.write_text(json.dumps(stored), encoding="utf-8")

        hit = self.find("pricing kind:snapshot")[0]
        self.assertEqual(hit.title, "Vendor — plans, pricing and kumquats")
        self.assertEqual(hit.fetched, "2026-09-09T09:00:00+00:00")
        # The record's title is indexed too — and matched as written, since
        # nothing here stems: "kumquats" is findable, "kumquat" is not.
        self.assertEqual(len(self.find("kumquats kind:snapshot")), 1)
        self.assertEqual(self.find("kumquat kind:snapshot"), [])

    def test_a_deleted_file_leaves_the_index(self) -> None:
        search.build_index(self.cfg)
        self.assertTrue(self.find("kind:snapshot enterprise"))
        (self.market / "snapshots/vendor-pricing.txt").unlink()
        self.assertEqual(self.find("enterprise kind:snapshot"), [])

    def test_a_new_report_is_picked_up(self) -> None:
        search.build_index(self.cfg)
        self.write("late", TITLE_REPORT.replace("Pricing strategy", "Kumquat strategy"))
        self.assertEqual(
            [hit.report for hit in self.find("kumquat kind:report")], ["late"]
        )

    def test_an_index_from_another_vault_is_ignored(self) -> None:
        search.build_index(self.cfg)
        stored = json.loads(search.index_path(self.cfg).read_text(encoding="utf-8"))
        stored["vault"] = "/somewhere/else"
        search.index_path(self.cfg).write_text(json.dumps(stored), encoding="utf-8")
        self.assertIsNone(search.load_index(self.cfg))

    def test_a_corrupt_index_is_treated_as_absent(self) -> None:
        search.index_path(self.cfg).parent.mkdir(parents=True, exist_ok=True)
        search.index_path(self.cfg).write_text("{not json", encoding="utf-8")
        self.assertIsNone(search.load_index(self.cfg))
        self.assertTrue(self.find("pricing"))  # rebuilt rather than raised

    def test_rebuild_false_uses_the_stored_index(self) -> None:
        search.build_index(self.cfg)
        self.write("late", TITLE_REPORT.replace("Pricing strategy", "Kumquat strategy"))
        self.assertEqual(self.find("kumquat", rebuild=False), [])
        self.assertTrue(self.find("kumquat", rebuild=True))


class Postings(Vault):
    def test_a_document_over_the_text_budget_keeps_its_postings(self) -> None:
        # The budget is a size, not a policy: with it set to nothing, every
        # document falls back to postings and the answers must not move.
        original = search.TEXT_BUDGET
        search.TEXT_BUDGET = 0
        try:
            index = search.build_index(self.cfg, force=True)
            self.assertTrue(all(doc["text"] is None for doc in index["docs"].values()))
            hit = self.find("enterprise kind:snapshot", rebuild=False)[0]
        finally:
            search.TEXT_BUDGET = original
        self.assertEqual(hit.key, "vendor-pricing")
        self.assertIn("Enterprise", hit.excerpt)
        self.assertTrue(hit.marks)
        # Re-read from disk, so the offset still points at the word.
        text = (self.market / "snapshots/vendor-pricing.txt").read_text(encoding="utf-8")
        self.assertTrue(text[hit.offset :].lower().startswith("enterprise"))

    def test_phrases_still_work_without_stored_text(self) -> None:
        original = search.TEXT_BUDGET
        search.TEXT_BUDGET = 0
        try:
            search.build_index(self.cfg, force=True)
            self.assertTrue(self.find('"pricing page"', rebuild=False))
            self.assertEqual(self.find('"page pricing"', rebuild=False), [])
        finally:
            search.TEXT_BUDGET = original


class Output(Vault):
    def test_json_is_serialisable_and_shaped_for_the_app(self) -> None:
        payload = search.to_json(self.find("pricing"))
        round_tripped = json.loads(json.dumps(payload))
        self.assertEqual(round_tripped["count"], len(round_tripped["hits"]))
        row = round_tripped["hits"][0]
        self.assertEqual(
            set(row),
            {
                "kind",
                "report",
                "key",
                "path",
                "line",
                "offset",
                "score",
                "excerpt",
                "marks",
                "title",
                "fetched",
            },
        )
        self.assertTrue(all(len(mark) == 2 for mark in row["marks"]))

    def test_printing_hits_exits_zero_and_nothing_exits_one(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            code = search.report_hits(self.cfg, self.find("pricing kind:snapshot"))
        self.assertEqual(code, 0)
        self.assertIn("reports/market/snapshots/vendor-pricing.txt", out.getvalue())

        with redirect_stdout(io.StringIO()):
            self.assertEqual(search.report_hits(self.cfg, []), 1)


if __name__ == "__main__":
    unittest.main()
