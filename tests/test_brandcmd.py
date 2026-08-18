"""The brand commands, on a scratch vault.

Three properties are load-bearing here, and each one is a thing that goes wrong
quietly rather than loudly.

The first is that a pack file stays a **delta**. `brand.load` fills the engine
default in underneath every pack, so a file that restates a default value looks
like a decision and is not one. If `set_key` ever wrote a full copy, the studio
would show every field as chosen, the "default" chip would never appear, and a
change to the engine default would stop reaching the vaults that never asked to
be pinned. So the tests assert what is *absent* from the file, not only what is
present.

The second is that a value keeps its type. `defaults.version = 1.0` typed at a
shell must stay the string `"1.0"`: a bare number reaches Typst unquoted and the
template fails on a version that is not content.

The third is that a preview is not a report. `reports/` must be untouched after
one, whatever the pack is called.

The rendering test is skipped when typst is not installed, but the *failure* path
is not — a missing typst has to produce a sentence a person can act on, and that
is asserted on every machine.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import brand, brandcmd, scaffold, vault  # noqa: E402
from engine.brand import BrandError  # noqa: E402
from engine.brandcmd import BrandPreviewError  # noqa: E402
from engine.config import Config, load  # noqa: E402

HAS_TYPST = shutil.which("typst") is not None


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

    def pack_file(self, name: str) -> dict:
        path = brandcmd._pack_file(self.cfg, name)
        return json.loads(path.read_text(encoding="utf-8"))


class TestList(Vault):
    def test_a_scaffolded_vault_lists_its_own_default(self) -> None:
        packs = brandcmd.list_packs(self.cfg)
        self.assertEqual([row["name"] for row in packs], ["default"])
        self.assertFalse(packs[0]["builtin"])
        self.assertEqual(packs[0]["path"], "brand/brand.json")
        self.assertEqual(packs[0]["accent"], "#2E5A88")
        self.assertEqual(packs[0]["org"], "Your Organisation")

    def test_a_vault_with_no_brand_file_still_has_a_default(self) -> None:
        """It is what every report there is rendered with — leaving it out of
        the listing would say the vault has no brand, which is false."""
        (self.cfg.brand / "brand.json").unlink()
        packs = brandcmd.list_packs(self.cfg)
        self.assertEqual([row["name"] for row in packs], ["default"])
        self.assertTrue(packs[0]["builtin"])
        self.assertEqual(packs[0]["accent"], "#2E5A88")

    def test_a_new_pack_joins_the_listing_with_its_own_accent(self) -> None:
        self.quiet(brandcmd.new_pack, self.cfg, "acme")
        self.quiet(brandcmd.set_key, self.cfg, "colors.accent", "#B4232A", "acme")
        packs = {row["name"]: row for row in brandcmd.list_packs(self.cfg)}
        self.assertEqual(sorted(packs), ["acme", "default"])
        self.assertEqual(packs["acme"]["accent"], "#B4232A")
        self.assertEqual(packs["acme"]["path"], "brand/acme/brand.json")


class TestShow(Vault):
    def origins(self, pack: str = "default") -> dict[str, str]:
        shown = brandcmd.show_pack(self.cfg, pack)
        return {row["key"]: row["origin"] for row in shown["keys"]}

    def test_every_resolved_key_is_tagged_with_where_it_came_from(self) -> None:
        origins = self.origins()
        # The scaffolded brand.json writes the accent family and nothing else.
        self.assertEqual(origins["colors.accent"], "pack")
        self.assertEqual(origins["sizes.body"], "default")
        self.assertEqual(origins["page-margin.top"], "default")

    def test_the_resolved_pack_carries_every_key_the_engine_knows(self) -> None:
        shown = brandcmd.show_pack(self.cfg)
        keys = {row["key"] for row in shown["keys"]}
        self.assertIn("fonts.display", keys)
        self.assertIn("defaults.bib-style", keys)
        self.assertNotIn("$comment", keys)  # notes to a human are not keys
        self.assertEqual(shown["path"], "brand/brand.json")
        self.assertFalse(shown["builtin"])

    def test_a_font_stack_is_one_value_not_three(self) -> None:
        values = {row["key"]: row["value"] for row in brandcmd.show_pack(self.cfg)["keys"]}
        self.assertIsInstance(values["fonts.display"], list)

    def test_the_nested_answer_keeps_the_shape_of_a_brand_file(self) -> None:
        """The studio builds its sections from `values` and reads provenance off
        the leaf, so a leaf has to be a tagged value and not a bare one."""
        values = brandcmd.show_pack(self.cfg)["values"]
        self.assertEqual(values["colors"]["accent"], {"value": "#2E5A88", "origin": "pack"})
        self.assertEqual(values["sizes"]["body"], {"value": "9.8pt", "origin": "default"})
        self.assertEqual(values["fonts"]["display"]["value"][0], "Didot")
        self.assertNotIn("$comment", values)

    def test_setting_a_key_moves_it_from_default_to_pack(self) -> None:
        self.assertEqual(self.origins()["sizes.body"], "default")
        self.quiet(brandcmd.set_key, self.cfg, "sizes.body", "10.4pt")
        self.assertEqual(self.origins()["sizes.body"], "pack")

    def test_an_unknown_pack_is_refused(self) -> None:
        with self.assertRaises(vault.VaultError):
            brandcmd.show_pack(self.cfg, "nope")


class TestNew(Vault):
    def test_a_pack_seeded_from_the_engine_default_decides_nothing_yet(self) -> None:
        path = self.quiet(brandcmd.new_pack, self.cfg, "acme")
        self.assertEqual(path, self.cfg.brand / "acme" / "brand.json")
        data = self.pack_file("acme")
        self.assertEqual([key for key in data if not key.startswith("$")], [])
        # …and it still resolves to the full brand, through the default.
        self.assertEqual(brand.load(self.cfg, "acme")["sizes"]["body"], "9.8pt")
        self.assertTrue(all(
            row["origin"] == "default" for row in brandcmd.show_pack(self.cfg, "acme")["keys"]
        ))

    def test_a_duplicate_carries_only_the_source_pack_decisions(self) -> None:
        self.quiet(brandcmd.new_pack, self.cfg, "acme")
        self.quiet(brandcmd.set_key, self.cfg, "colors.accent", "#B4232A", "acme")
        self.quiet(brandcmd.set_key, self.cfg, "org.name", "Acme Ltd", "acme")
        self.quiet(brandcmd.new_pack, self.cfg, "acme-dark", source="acme")

        data = self.pack_file("acme-dark")
        self.assertEqual(data["colors"], {"accent": "#B4232A"})
        self.assertEqual(data["org"], {"name": "Acme Ltd"})
        self.assertNotIn("sizes", data)

    def test_an_existing_pack_is_never_overwritten(self) -> None:
        self.quiet(brandcmd.new_pack, self.cfg, "acme")
        with self.assertRaises(BrandError):
            self.quiet(brandcmd.new_pack, self.cfg, "acme")

    def test_a_name_that_is_not_a_folder_name_is_refused(self) -> None:
        for name in ("../escape", "with space", "", "a/b"):
            with self.assertRaises(BrandError):
                self.quiet(brandcmd.new_pack, self.cfg, name)

    def test_an_unknown_source_pack_is_refused(self) -> None:
        with self.assertRaises(vault.VaultError):
            self.quiet(brandcmd.new_pack, self.cfg, "acme", source="nope")


class TestSet(Vault):
    def test_the_file_holds_the_change_and_not_a_copy_of_the_default(self) -> None:
        self.quiet(brandcmd.new_pack, self.cfg, "acme")
        self.quiet(brandcmd.set_key, self.cfg, "colors.accent", "#B4232A", "acme")

        data = self.pack_file("acme")
        self.assertEqual(data["colors"], {"accent": "#B4232A"})
        for section in ("sizes", "space", "fonts", "page-margin", "defaults"):
            self.assertNotIn(section, data)
        self.assertEqual(brand.load(self.cfg, "acme")["colors"]["accent"], "#B4232A")
        # Everything it did not set still comes through the default.
        self.assertEqual(brand.load(self.cfg, "acme")["colors"]["ink"], "#000000")

    def test_a_value_equal_to_the_default_is_not_a_decision(self) -> None:
        """Including one that was already in the file: a restated default hides
        what the pack actually changes, and shows an inherited field as chosen."""
        self.quiet(brandcmd.set_key, self.cfg, "sizes.body", "10.4pt")
        self.assertEqual(self.pack_file("default")["sizes"], {"body": "10.4pt"})

        self.quiet(brandcmd.set_key, self.cfg, "sizes.body", "9.8pt")
        data = self.pack_file("default")
        self.assertNotIn("sizes", data)
        # The scaffolded stub restated the engine's accent; that goes too.
        self.assertNotIn("colors", data)
        self.assertIn("$comment", data)

    def test_a_string_valued_key_stays_a_string(self) -> None:
        """`1.0` is a version, not a number — unquoted it reaches Typst as a
        float and the template fails on a version that is not content."""
        self.quiet(brandcmd.set_key, self.cfg, "defaults.version", "2.0")
        written = self.pack_file("default")["defaults"]["version"]
        self.assertIsInstance(written, str)
        self.assertEqual(written, "2.0")
        self.assertIn('version: "2.0"', brand.tokens_typ(brand.load(self.cfg, "default")))

    def test_a_font_stack_is_set_as_json(self) -> None:
        self.quiet(brandcmd.set_key, self.cfg, "fonts.text", '["Inter", "Helvetica"]')
        self.assertEqual(brand.load(self.cfg, "default")["fonts"]["text"], ["Inter", "Helvetica"])

    def test_a_malformed_list_says_so(self) -> None:
        with self.assertRaises(BrandError):
            self.quiet(brandcmd.set_key, self.cfg, "fonts.text", "[Inter, Helvetica")

    def test_an_unknown_section_is_refused_with_the_known_ones(self) -> None:
        with self.assertRaises(BrandError) as caught:
            self.quiet(brandcmd.set_key, self.cfg, "colours.accent", "#B4232A")
        self.assertIn("colors", str(caught.exception))

    def test_the_result_is_still_valid_typst_tokens(self) -> None:
        self.quiet(brandcmd.set_key, self.cfg, "colors.accent", "#B4232A")
        self.quiet(brandcmd.set_key, self.cfg, "page-margin.x", "18mm")
        tokens = brand.tokens_typ(brand.load(self.cfg, "default"))
        self.assertIn('accent: rgb("#B4232A")', tokens)
        self.assertIn("x: 18mm", tokens)


class TestPreview(Vault):
    def test_it_stages_the_pack_being_previewed_not_the_template_default(self) -> None:
        """The design under .build/design/ carries the tokens of the pack its
        *template* names, which is exactly the pack we are not looking at."""
        self.quiet(brandcmd.new_pack, self.cfg, "acme")
        self.quiet(brandcmd.set_key, self.cfg, "colors.accent", "#B4232A", "acme")

        specimen = self.quiet(brandcmd.stage_preview, self.cfg, "acme")
        tokens = (specimen.parent / "design" / "tokens.typ").read_text(encoding="utf-8")
        self.assertIn('accent: rgb("#B4232A")', tokens)
        for name in ("report.typ", "components.typ", "theme.typ"):
            self.assertTrue((specimen.parent / "design" / name).is_file())

    def test_the_specimen_imports_its_design_the_way_a_report_does(self) -> None:
        specimen = self.quiet(brandcmd.stage_preview, self.cfg, "default")
        text = specimen.read_text(encoding="utf-8")
        self.assertIn(
            '#import "/.build/brand-preview/default/design/report.typ": report', text
        )
        self.assertIn('sources: "/.build/brand-preview/default/sources.yml"', text)
        self.assertNotIn("{{", text)
        sources = (specimen.parent / "sources.yml").read_text(encoding="utf-8")
        self.assertNotIn("{{", sources)

    def test_a_preview_never_writes_into_reports(self) -> None:
        before = sorted(p.name for p in self.cfg.reports.rglob("*"))
        self.quiet(brandcmd.stage_preview, self.cfg, "default")
        self.assertEqual(sorted(p.name for p in self.cfg.reports.rglob("*")), before)
        self.assertTrue(brandcmd.preview_dir(self.cfg, "default").is_dir())

    def test_a_missing_typst_is_a_sentence_a_person_can_act_on(self) -> None:
        with mock.patch.dict(os.environ, {"TYPST_BIN": "typst-not-installed-here"}):
            with self.assertRaises(BrandPreviewError) as caught:
                self.quiet(brandcmd.preview, self.cfg, "default")
        message = str(caught.exception)
        self.assertIn("typst", message)
        self.assertIn("brew install typst", message)

    @unittest.skipUnless(HAS_TYPST, "typst is not installed")
    def test_it_renders_a_pdf_and_page_images(self) -> None:
        pages = self.quiet(brandcmd.preview, self.cfg, "default", ppi=60)
        root = brandcmd.preview_dir(self.cfg, "default")
        self.assertTrue((root / "preview.pdf").is_file())
        self.assertGreaterEqual(len(pages), 2)
        self.assertTrue(all(page.is_file() for page in pages))
        # Beside the PDF, indexed the way pages.py indexes a report: the studio
        # falls back to reading this when it cannot parse the printed paths.
        self.assertEqual(pages[0], root / "page-1.png")
        index = json.loads((root / "pages.json").read_text(encoding="utf-8"))
        self.assertEqual(index["count"], len(pages))
        self.assertEqual(index["pages"][0], "page-1.png")
        self.assertEqual(index["pack"], "default")

    @unittest.skipUnless(HAS_TYPST, "typst is not installed")
    def test_a_shorter_render_does_not_leave_the_old_pages_behind(self) -> None:
        pages = self.quiet(brandcmd.preview, self.cfg, "default", ppi=40)
        stale = pages[-1].with_name(f"page-{len(pages) + 1}.png")
        stale.write_bytes(b"not a page")
        again = self.quiet(brandcmd.preview, self.cfg, "default", ppi=40)
        self.assertFalse(stale.exists())
        self.assertEqual(len(again), len(pages))

    @unittest.skipUnless(HAS_TYPST, "typst is not installed")
    def test_a_token_change_changes_the_render(self) -> None:
        """The whole point of the command: if a colour can move without the
        specimen moving, the preview is not previewing anything."""
        first = self.quiet(brandcmd.preview, self.cfg, "default", ppi=40)[0].read_bytes()
        self.quiet(brandcmd.set_key, self.cfg, "colors.accent", "#B4232A")
        second = self.quiet(brandcmd.preview, self.cfg, "default", ppi=40)[0].read_bytes()
        self.assertNotEqual(first, second)


class TestJson(Vault):
    def test_the_payloads_are_json_serialisable(self) -> None:
        packs = brandcmd.packs_json(self.cfg, brandcmd.list_packs(self.cfg))
        shown = brandcmd.show_json(self.cfg, brandcmd.show_pack(self.cfg))
        preview = brandcmd.preview_json(self.cfg, "default", [Path("/tmp/page-1.png")])
        for payload in (packs, shown, preview):
            json.loads(json.dumps(payload))
        self.assertEqual(packs["packs"][0]["name"], "default")
        self.assertEqual(shown["pack"], "default")
        self.assertEqual(preview["pages"], ["/tmp/page-1.png"])


if __name__ == "__main__":
    unittest.main()
