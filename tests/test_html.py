"""HTML export tests.

The export exists to be handed to somebody — attached to an email, dropped on a
share, opened months later on a laptop with no network. So the properties worth
asserting are the ones that make that true and are easy to lose:

* **Nothing is fetched.** One stray `src="https://…"` and the file silently
  becomes a document that only renders while somebody else's server is up.
* **Nothing is trusted.** A report may quote a source whose title or body is
  markup. Everything that came from a report or off the web is escaped, and the
  test uses a title that would execute if it were not.
* **Every source is present**, cited or not — an orphan entry is still part of
  what was reviewed, and dropping it from the evidence tab would quietly narrow
  the record.

Most of it runs without Typst: page images are only ever base64-encoded, never
decoded, so a placeholder file exercises the same code path. The demo-vault case
does need a real build and skips when `typst` is not on PATH.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import config, html, pages, scaffold  # noqa: E402
from engine.config import Config  # noqa: E402
from engine.workspace import Report, reports  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "examples/demo-vault"

TYPST = shutil.which(os.environ.get("TYPST_BIN") or "typst")

FAKE_PNG = b"\x89PNG\r\n\x1a\n not a real image, only ever base64-encoded"

# A title that executes if anything on the way to the page forgets to escape it,
# and a URL with a query string, which is where naive quoting breaks.
HOSTILE = """\
scripted:
  type: Web
  title: "<script>alert('pwned')</script> & \\"quoted\\""
  author: "Ada <b>Lovelace</b>"
  url:
    value: https://example.com/page?a=1&b=2
    date: 2026-02-02

orphan:
  type: Misc
  title: "Reviewed, never cited"
"""

MAIN = """\
#import "/.build/design/base/report.typ": report

#show: report.with(
  title: "Escaping",
  sources: "/reports/probe/sources.yml",
)

= Findings

The archived page says something specific about hydraulic dampers @scripted.
"""


def _attribute_urls(document: str) -> list[str]:
    return [match.group(1) for match in re.finditer(r'(?:src|href)="([^"]*)"', document)]


class ExportedFile(unittest.TestCase):
    """A scratch vault with placeholder page images — no Typst required."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        with redirect_stdout(io.StringIO()):
            scaffold.init(self.root)
        self.cfg: Config = config.load(self.root)
        self.report = self.write_report()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_report(self, main: str = MAIN, bibliography: str = HOSTILE) -> Report:
        folder = self.cfg.reports / "probe"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "main.typ").write_text(main, encoding="utf-8")
        (folder / "sources.yml").write_text(bibliography, encoding="utf-8")
        return Report(id="probe", folder=folder, cfg=self.cfg)

    def write_pages(self, *numbers: int) -> None:
        """Placeholder page images, each with distinct bytes so the order they
        are inlined in can be asserted."""
        self.report.pages_dir.mkdir(parents=True, exist_ok=True)
        for number in numbers:
            (self.report.pages_dir / f"page-{number}.png").write_bytes(
                FAKE_PNG + str(number).encode()
            )

    def write_snapshot(self, key: str, text: str, **record) -> None:
        folder = self.report.folder / "snapshots"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{key}.txt").write_text(text, encoding="utf-8")
        (folder / f"{key}.json").write_text(
            json.dumps(
                {
                    "key": key,
                    "url": "https://example.com/page",
                    "fetched": "2026-02-02T09:30:00Z",
                    "sha256": "abc123def456789000",
                    **record,
                }
            ),
            encoding="utf-8",
        )

    def render(self) -> str:
        return html.render(self.cfg, self.report)

    # ── the file has to stand alone ──────────────────────────────────────────

    def test_missing_pages_say_what_to_run(self) -> None:
        with self.assertRaises(html.HtmlError) as caught:
            self.render()
        self.assertIn("report-maker pages", str(caught.exception))

    def test_page_images_are_inlined_in_numeric_order(self) -> None:
        self.write_pages(1, 2, 10)
        document = self.render()

        inlined = re.findall(r'<img src="data:image/png;base64,([^"]+)"', document)
        decoded = [base64.b64decode(chunk) for chunk in inlined]
        # page-10 sorts before page-2 lexically; the reader must get 1, 2, 10.
        self.assertEqual(
            decoded, [FAKE_PNG + str(n).encode() for n in (1, 2, 10)]
        )
        self.assertEqual(
            re.findall(r"<figcaption>Page (\d+)</figcaption>", document),
            ["1", "2", "3"],
        )

    def test_nothing_is_fetched_but_the_source_urls_are_linkable(self) -> None:
        self.write_pages(1)
        document = self.render()

        allowed = {"https://example.com/page?a=1&amp;b=2"}
        remote = [
            url for url in _attribute_urls(document) if url.startswith(("http://", "https://"))
        ]
        self.assertTrue(remote, "the source URL should still be a link")
        self.assertEqual(set(remote) - allowed, set())
        # No stylesheet, script or font is pulled in either.
        self.assertNotIn("<link", document)
        self.assertNotIn("//cdn", document)

    # ── nothing from a report or a page is trusted ───────────────────────────

    def test_a_source_title_containing_markup_is_escaped(self) -> None:
        self.write_pages(1)
        document = self.render()

        self.assertNotIn("<script>alert", document)
        self.assertIn("&lt;script&gt;alert(&#x27;pwned&#x27;)&lt;/script&gt;", document)
        self.assertNotIn("Ada <b>Lovelace</b>", document)
        self.assertIn("Ada &lt;b&gt;Lovelace&lt;/b&gt;", document)

    def test_a_snapshot_excerpt_is_escaped_too(self) -> None:
        self.write_pages(1)
        self.write_snapshot("scripted", "Intro. <img src=x onerror=alert(1)> Outro.")
        document = self.render()

        self.assertNotIn("<img src=x", document)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", document)

    # ── the evidence tab ─────────────────────────────────────────────────────

    def test_every_source_gets_a_card_cited_or_not(self) -> None:
        self.write_pages(1)
        document = self.render()

        self.assertIn('id="src-scripted"', document)
        self.assertIn('id="src-orphan"', document)
        self.assertIn("Never cited", document)  # the orphan says so plainly

    def test_a_claim_is_listed_under_the_source_it_cites(self) -> None:
        self.write_pages(1)
        document = self.render()

        self.assertIn("hydraulic dampers", document)
        self.assertIn("line 10", document)  # where the sentence sits in main.typ

    def test_the_citation_is_a_keyboard_reachable_button(self) -> None:
        self.write_pages(1)
        document = self.render()

        button = re.search(
            r'<button type="button" class="cite" aria-expanded="false" '
            r'aria-controls="(pop-\d+)">@scripted</button>',
            document,
        )
        self.assertIsNotNone(button, "the @key should render as a button, not a hover target")
        self.assertIn(f'id="{button.group(1)}"', document)

    def test_snapshot_state_reads_on_the_card(self) -> None:
        self.write_pages(1)
        self.assertIn("not archived", self.render())

        self.write_snapshot("scripted", "Nothing in particular.")
        self.assertIn("archived 2026-02-02 · sha256 abc123def456…", self.render())

    def test_the_excerpt_is_centred_on_the_matching_passage(self) -> None:
        self.write_pages(1)
        noise = "Filler about unrelated matters. " * 60
        self.write_snapshot(
            "scripted",
            noise + "The hydraulic dampers were replaced in March. " + noise,
        )
        document = self.render()

        window = re.search(r'<p class="quote">(.*?)</p>', document, re.S)
        self.assertIsNotNone(window)
        self.assertIn("hydraulic dampers were replaced", window.group(1))
        self.assertLessEqual(len(window.group(1)), html.EXCERPT + 8)  # plus ellipses

    # ── colour comes from the pack, never from this module ───────────────────

    def test_the_palette_is_the_brand_packs_own(self) -> None:
        self.write_pages(1)
        document = self.render()

        pack = html.brand.load(self.cfg, "default")
        self.assertIn(f"--accent: {pack['colors']['accent']};", document)
        self.assertIn(f"--bg: {pack['colors']['surface']};", document)
        self.assertIn("@media (prefers-color-scheme: dark)", document)

    def test_the_dark_variant_is_derived_and_readable(self) -> None:
        pack = html.brand.load(self.cfg, "default")
        light, dark = html.palette(pack)

        self.assertNotEqual(light["bg"], dark["bg"])
        self.assertLess(html._luminance(dark["bg"]), html._luminance(light["bg"]))
        for token in ("ink", "accent"):
            self.assertGreaterEqual(
                html._contrast(dark[token], dark["bg"]),
                4.5,
                f"dark {token} must clear AA on the dark background",
            )


@unittest.skipUnless(TYPST, "typst is not on PATH")
class DemoVaultExport(unittest.TestCase):
    """The real thing: a built vault, real page images, the shipped bibliography."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cfg = config.load(DEMO)
        with redirect_stdout(io.StringIO()):
            pages.build(cls.cfg)

    def test_every_report_exports_a_self_contained_file(self) -> None:
        for report in reports(self.cfg):
            with self.subTest(report=report.id):
                with redirect_stdout(io.StringIO()):
                    exported = html.export_one(self.cfg, report)
                self.assertEqual(exported, self.cfg.out / f"{report.id}.html")
                document = exported.read_text(encoding="utf-8")

                urls = {source.url for source in html.sources.parse(report.sources)}
                shown = {url.replace("&", "&amp;") for url in urls if url}
                remote = [
                    url
                    for url in _attribute_urls(document)
                    if url.startswith(("http://", "https://"))
                ]
                self.assertEqual(set(remote) - shown, set())

                self.assertEqual(
                    document.count("<img src=\"data:image/png;base64,"),
                    len(html.page_images(report)),
                )
                for source in html.sources.parse(report.sources):
                    self.assertIn(f'id="src-{source.key}"', document)


if __name__ == "__main__":
    unittest.main()
