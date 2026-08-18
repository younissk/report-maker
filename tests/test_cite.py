"""Citing and archiving, with the network unplugged.

Every test here passes a fake fetcher. That is not only for speed: a test suite
that reaches the internet is a test suite that fails on a train, and one that
silently exercises a different page each time it runs. The canned responses below
are the pages, and they never change.

Three properties are load-bearing.

The first is the scheme guard. `url:` in a bibliography is untrusted input, and
`http_fetch` refusing everything but http and https is what stops a vault turning
`report-maker cite` into a command that reads local files. It is asserted twice —
once at the fetcher, once at the command — because `--no-snapshot` skips the
fetch and would otherwise skip the check with it.

The second is idempotence. Citing the same URL twice must leave the bibliography
byte-identical and must not fetch again, because half-finished commands get
re-run and a duplicated source is a duplicated entry in References.

The third is that the archive really is the bytes. The sha in the record is
asserted against `hashlib` over the response body, not against itself.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import cite as cite_mod  # noqa: E402
from engine import scaffold, snapshot, sources  # noqa: E402
from engine.config import Config, load  # noqa: E402
from engine.snapshot import Fetched, SnapshotError  # noqa: E402
from engine.workspace import Report  # noqa: E402

PRICING_URL = "https://acme.example/pricing"

PRICING_HTML = b"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Acme \xe2\x80\x94 Pricing</title>
  <meta name="author" content="Ada   Lovelace">
  <meta property="og:site_name" content="Acme Ltd">
  <meta property="article:published_time" content="2026-01-05">
  <script type="application/ld+json">
    {"@type": "WebPage", "datePublished": "2026-01-05", "author": {"name": "Ignored"}}
  </script>
</head>
<body>
  <nav><a href="/">Home</a> <a href="/pricing">Pricing</a></nav>
  <script>var plans = ["secret"];</script>
  <style>body { color: red }</style>
  <h1>Pricing</h1>
  <p>The   Standard plan is
  $49 per seat per month.</p>
  <ul><li>Unlimited seats</li><li>Priority support</li></ul>
  <footer>&copy; Acme Ltd</footer>
</body>
</html>
"""

MAIN_TYP = """#import "/.build/design/base/report.typ": *
#show: report.with(
  title: "Demo",
  sources: "/reports/examples/2026-08-18-demo/sources.yml",
)

Body text.
"""


class FakeFetcher:
    """Canned responses, and a record of what was asked for."""

    def __init__(self, pages: dict[str, Fetched]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def __call__(self, url: str) -> Fetched:
        self.calls.append(url)
        if url not in self.pages:
            raise SnapshotError(f"the test has no canned response for {url}")
        return self.pages[url]


def html_page(url: str, body: bytes = PRICING_HTML, **over) -> Fetched:
    fields = {
        "url": url,
        "status": 200,
        "content_type": "text/html; charset=utf-8",
        "body": body,
        "final_url": url,
    }
    fields.update(over)
    return Fetched(**fields)


class CiteCase(unittest.TestCase):
    """A scratch vault holding one report, torn down after each test."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        with redirect_stdout(io.StringIO()):
            scaffold.init(self.root)
        self.cfg: Config = load(self.root)
        folder = self.cfg.reports / "examples/2026-08-18-demo"
        folder.mkdir(parents=True)
        (folder / "main.typ").write_text(MAIN_TYP, encoding="utf-8")
        self.report = Report(
            id="examples/2026-08-18-demo", folder=folder, cfg=self.cfg
        )
        self.fetcher = FakeFetcher({PRICING_URL: html_page(PRICING_URL)})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def cite(self, url: str = PRICING_URL, **kwargs):
        kwargs.setdefault("fetch", self.fetcher)
        with redirect_stdout(io.StringIO()) as out:
            source = cite_mod.cite(self.cfg, self.report.id, url, **kwargs)
        self.printed = out.getvalue()
        return source

    @property
    def bibliography(self) -> str:
        return self.report.sources.read_text(encoding="utf-8")


# ── the command ──────────────────────────────────────────────────────────────


class Citing(CiteCase):
    def test_it_writes_a_hayagriva_block_from_the_page(self) -> None:
        source = self.cite()
        self.assertEqual(source.key, "acme-pricing")
        accessed = snapshot.read_record(self.report, source.key)["fetched"][:10]
        self.assertEqual(
            self.bibliography,
            "acme-pricing:\n"
            '  type: "Web"\n'
            '  title: "Acme — Pricing"\n'
            '  author: "Ada Lovelace"\n'
            '  publisher: "Acme Ltd"\n'
            '  date: "2026-01-05"\n'
            "  url:\n"
            f'    value: "{PRICING_URL}"\n'
            f'    date: "{accessed}"\n',
        )
        # And it round-trips: what was written parses back to what was meant.
        parsed = sources.parse(self.report.sources)[0]
        self.assertEqual(parsed.title, "Acme — Pricing")
        self.assertEqual(parsed.url, PRICING_URL)
        self.assertEqual(parsed.accessed, accessed)

    def test_it_prints_the_line_to_write(self) -> None:
        self.cite()
        self.assertIn("Cite it with: @acme-pricing", self.printed)

    def test_the_key_can_be_given(self) -> None:
        source = self.cite(key="acme-prices-2026")
        self.assertEqual(source.key, "acme-prices-2026")
        self.assertIn("acme-prices-2026:", self.bibliography)

    def test_a_key_already_in_use_is_refused(self) -> None:
        self.report.sources.write_text("taken:\n  type: Misc\n", encoding="utf-8")
        with self.assertRaises(cite_mod.CiteError):
            self.cite(key="taken")

    def test_a_second_url_with_the_same_title_gets_a_distinct_key(self) -> None:
        other = "https://acme.example/pricing/enterprise"
        self.fetcher.pages[other] = html_page(other)
        self.assertEqual(self.cite().key, "acme-pricing")
        self.assertEqual(self.cite(other).key, "acme-pricing-2")

    def test_a_page_with_no_metadata_falls_back_to_the_url(self) -> None:
        url = "https://plain.example/notes"
        self.fetcher.pages[url] = html_page(url, body=b"<html><body>Hi</body></html>")
        source = self.cite(url)
        self.assertEqual(source.title, url)
        self.assertNotIn("author", source.fields)  # never invented

    def test_target_must_name_one_report(self) -> None:
        second = self.cfg.reports / "examples/2026-08-19-other"
        second.mkdir(parents=True)
        (second / "main.typ").write_text(MAIN_TYP, encoding="utf-8")
        with self.assertRaises(cite_mod.CiteError):
            with redirect_stdout(io.StringIO()):
                cite_mod.cite(self.cfg, "examples", PRICING_URL, fetch=self.fetcher)


class Idempotence(CiteCase):
    def test_citing_the_same_url_twice_changes_nothing(self) -> None:
        first = self.cite()
        before = self.bibliography
        again = self.cite()
        self.assertEqual(again.key, first.key)
        self.assertEqual(self.bibliography, before)
        self.assertEqual(self.fetcher.calls, [PRICING_URL])  # no second fetch

    def test_a_trailing_slash_or_www_is_the_same_page(self) -> None:
        self.cite()
        again = self.cite("https://www.acme.example/pricing/")
        self.assertEqual(again.key, "acme-pricing")
        self.assertEqual(len(sources.parse(self.report.sources)), 1)

    def test_an_entry_without_a_snapshot_gets_one(self) -> None:
        self.cite(no_snapshot=True)
        self.assertIsNone(snapshot.read_record(self.report, "acme-pricing"))
        before = self.bibliography
        self.cite()
        self.assertEqual(self.bibliography, before)  # the entry is left alone
        self.assertIsNotNone(snapshot.read_record(self.report, "acme-pricing"))


class Archiving(CiteCase):
    def test_the_bytes_the_text_and_the_record(self) -> None:
        self.cite()
        report, key = self.report, "acme-pricing"

        self.assertEqual(snapshot.raw_path(report, key).read_bytes(), PRICING_HTML)

        text = snapshot.read_text(report, key)
        self.assertIn("The Standard plan is $49 per seat per month.", text)
        self.assertNotIn("secret", text)  # <script> dropped
        self.assertNotIn("color: red", text)  # <style> dropped
        self.assertNotIn("Home", text)  # <nav> dropped
        self.assertNotIn("Acme Ltd", text)  # <footer> dropped

        record = snapshot.read_record(report, key)
        self.assertEqual(record["sha256"], hashlib.sha256(PRICING_HTML).hexdigest())
        self.assertEqual(record["bytes"], len(PRICING_HTML))
        self.assertEqual(record["status"], 200)
        self.assertEqual(record["url"], PRICING_URL)
        self.assertEqual(record["title"], "Acme — Pricing")
        self.assertEqual(record["key"], key)

    def test_no_snapshot_leaves_no_archive(self) -> None:
        self.cite(no_snapshot=True)
        self.assertFalse(snapshot.dir_for(self.report).exists())
        self.assertIn("acme-pricing:", self.bibliography)

    def test_a_404_is_archived_as_evidence_rather_than_raised(self) -> None:
        url = "https://acme.example/gone"
        self.fetcher.pages[url] = html_page(
            url, body=b"<html><title>Not found</title><body>Gone</body></html>", status=404
        )
        source = self.cite(url)
        record = snapshot.read_record(self.report, source.key)
        self.assertEqual(record["status"], 404)
        self.assertIn("404", record["note"])
        self.assertIn("404", self.printed)

    def test_a_redirect_is_recorded(self) -> None:
        asked = "https://acme.example/prices"
        self.fetcher.pages[asked] = html_page(asked, final_url=PRICING_URL)
        self.cite(asked)
        record = snapshot.read_record(self.report, "acme-pricing")
        self.assertEqual(record["url"], PRICING_URL)
        self.assertIn("redirected from", record["note"])
        # The bibliography cites where the page actually lives.
        self.assertEqual(sources.parse(self.report.sources)[0].url, PRICING_URL)

    def test_a_pdf_keeps_its_bytes_and_admits_it_has_no_text(self) -> None:
        url = "https://acme.example/report.pdf"
        body = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\ntrailer\n"
        self.fetcher.pages[url] = html_page(url, body=body, content_type="application/pdf")
        source = self.cite(url)
        self.assertEqual(source.type, "Report")
        record = snapshot.read_record(self.report, source.key)
        self.assertEqual(record["sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(snapshot.read_text(self.report, source.key), "")
        self.assertIn("no text extracted", record["note"])

    def test_records_indexes_the_folder_and_skips_rotated_copies(self) -> None:
        self.cite()
        moved = snapshot.rotate(self.report, "acme-pricing")
        self.assertEqual(len(moved), 3)
        self.assertEqual(snapshot.records(self.report), {})  # nothing current left
        self.cite()  # the entry exists, the archive does not — refilled
        self.assertEqual(list(snapshot.records(self.report)), ["acme-pricing"])
        # The old copy is still on disk, which is the whole point of rotating.
        self.assertTrue(all(path.exists() for path in moved))


# ── the security boundary ────────────────────────────────────────────────────


class Schemes(unittest.TestCase):
    def test_the_fetcher_refuses_anything_but_http_and_https(self) -> None:
        for url in (
            "file:///etc/passwd",
            "ftp://example.com/x",
            "data:text/html,<b>hi</b>",
            "gopher://example.com",
        ):
            with self.subTest(url=url), self.assertRaises(SnapshotError):
                snapshot.http_fetch(url)

    def test_the_command_refuses_them_too(self) -> None:
        # `--no-snapshot` never reaches the fetcher, so the guard cannot live
        # only there.
        for url in ("file:///etc/passwd", "ftp://example.com/x"):
            with self.subTest(url=url), self.assertRaises(cite_mod.CiteError):
                cite_mod.normalise(url)

    def test_a_redirect_off_the_web_is_refused_before_it_is_followed(self) -> None:
        # Reaching for the private handler on purpose: this is the boundary, and
        # the point of it is that it fires while the location is still a string,
        # before urllib has opened a connection to whatever it names.
        handler = snapshot._WebOnlyRedirects()
        with self.assertRaises(SnapshotError):
            handler.redirect_request(None, None, 302, "Found", {}, "ftp://evil.example/x")

    def test_a_redirect_urllib_refuses_is_not_archived_as_a_page(self) -> None:
        # urllib turns a `file://` Location into an HTTPError naming it. Without
        # a guard on that path, the empty redirect body would be archived as
        # though it were the page, with a 302 recorded as the status.
        class RefusingOpener:
            def open(self, request, timeout=None):
                raise urllib.error.HTTPError(
                    "file:///etc/passwd", 302, "Found", {}, None
                )

        original, snapshot._OPENER = snapshot._OPENER, RefusingOpener()
        try:
            with self.assertRaises(SnapshotError):
                snapshot.http_fetch("https://acme.example/redirects-badly")
        finally:
            snapshot._OPENER = original

    def test_a_bare_host_is_assumed_to_be_https(self) -> None:
        self.assertEqual(
            cite_mod.normalise("acme.example/pricing"), "https://acme.example/pricing"
        )
        self.assertEqual(
            cite_mod.normalise("acme.example:8443/x"), "https://acme.example:8443/x"
        )


# ── extraction ───────────────────────────────────────────────────────────────


class Extraction(unittest.TestCase):
    def test_text_keeps_block_boundaries_and_collapses_the_rest(self) -> None:
        text = snapshot.extract_text(PRICING_HTML, "text/html")
        self.assertEqual(
            text.splitlines(),
            ["Pricing", "The Standard plan is $49 per seat per month.",
             "Unlimited seats", "Priority support"],
        )

    def test_an_unclosed_dropped_element_does_not_swallow_the_page(self) -> None:
        body = b"<html><body><nav><div>Menu</nav><p>Real prose.</p></body></html>"
        self.assertEqual(snapshot.extract_text(body, "text/html"), "Real prose.\n")

    def test_the_declared_charset_wins(self) -> None:
        body = "<html><body><p>café</p></body></html>".encode("latin-1")
        self.assertIn("café", snapshot.extract_text(body, "text/html; charset=latin-1"))
        # An unknown charset falls back rather than raising.
        self.assertTrue(snapshot.extract_text(body, "text/html; charset=nonsense-8"))

    def test_meta_reports_only_what_it_found(self) -> None:
        meta = snapshot.extract_meta(PRICING_HTML, "text/html")
        self.assertEqual(
            meta,
            {
                "title": "Acme — Pricing",
                "author": "Ada Lovelace",  # the run of spaces is collapsed
                "site": "Acme Ltd",
                "published": "2026-01-05",
            },
        )
        self.assertEqual(snapshot.extract_meta(b"<html></html>", "text/html"), {})

    def test_json_ld_is_the_last_resort_for_author_and_date(self) -> None:
        body = b"""<html><head><title>T</title>
        <script type="application/ld+json">
        {"@graph": [{"@type": "Article", "author": [{"name": "Grace Hopper"}],
                     "datePublished": "2026-03-04T09:00:00Z"}]}
        </script></head><body>x</body></html>"""
        meta = snapshot.extract_meta(body, "text/html")
        self.assertEqual(meta["author"], "Grace Hopper")
        self.assertEqual(meta["published"], "2026-03-04T09:00:00Z")

    def test_broken_json_ld_does_not_lose_the_page(self) -> None:
        body = b"""<html><head><title>T</title>
        <script type="application/ld+json">{not json at all</script>
        </head><body>Prose.</body></html>"""
        self.assertEqual(snapshot.extract_meta(body, "text/html")["title"], "T")
        self.assertEqual(snapshot.extract_text(body, "text/html"), "Prose.\n")

    def test_plain_text_is_kept_and_binaries_are_not(self) -> None:
        self.assertEqual(snapshot.extract_text(b"one  two\n", "text/plain"), "one two\n")
        self.assertEqual(snapshot.extract_text(b"\x89PNG\r\n", "image/png"), "")

    def test_an_unlabelled_body_is_sniffed(self) -> None:
        self.assertEqual(
            snapshot.extract_text(b"<!DOCTYPE html><p>Hi</p>", ""), "Hi\n"
        )


class Records(unittest.TestCase):
    def test_a_corrupt_record_reads_as_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with redirect_stdout(io.StringIO()):
                scaffold.init(root)
            cfg = load(root)
            folder = cfg.reports / "x"
            folder.mkdir(parents=True)
            report = Report(id="x", folder=folder, cfg=cfg)
            self.assertIsNone(snapshot.read_record(report, "nope"))
            self.assertIsNone(snapshot.read_text(report, "nope"))
            self.assertEqual(snapshot.records(report), {})

            path = snapshot.record_path(report, "broken")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{ not json", encoding="utf-8")
            self.assertIsNone(snapshot.read_record(report, "broken"))
            self.assertEqual(snapshot.records(report), {})

    def test_a_key_with_awkward_characters_maps_to_one_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with redirect_stdout(io.StringIO()):
                scaffold.init(root)
            cfg = load(root)
            folder = cfg.reports / "x"
            folder.mkdir(parents=True)
            report = Report(id="x", folder=folder, cfg=cfg)
            written = snapshot.write(
                report,
                "iso:27001+a",
                Fetched(
                    url="https://a.example/",
                    status=200,
                    content_type="text/html",
                    body=b"<p>Hi</p>",
                ),
            )
            self.assertEqual(written["key"], "iso:27001+a")
            self.assertTrue(snapshot.record_path(report, "iso:27001+a").is_file())
            self.assertEqual(list(snapshot.records(report)), ["iso:27001+a"])
            self.assertNotIn(":", snapshot.record_path(report, "iso:27001+a").name)
            loaded = json.loads(
                snapshot.record_path(report, "iso:27001+a").read_text(encoding="utf-8")
            )
            self.assertEqual(loaded, written)


if __name__ == "__main__":
    unittest.main()
