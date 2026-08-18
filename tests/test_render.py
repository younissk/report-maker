"""The golden render: does the demo vault still come out looking the same?

Every other test in this suite reads source files. None of them can see the one
failure that matters most to a document tool — the design broke. A brand token
renamed, an inherited `report.typ` that stopped being inherited, a margin that
Typst now resolves differently: the engine is happy, `check` is green, and the
cover page has moved two centimetres.

So this test renders page 1 of each example report and compares its perceptual
hash against the one recorded in `tests/golden/`. The hash is a dHash over a
box-filtered downsample (see `engine/imagehash.py`), which is deliberately
blunt: it ignores antialiasing, font hinting and resolution, and it notices
layout. The tolerance below is set from measurement — rerendering the demo
vault at 150 ppi instead of 110 moves the hash by a single bit — so anything
approaching six bits is a real change in what the page looks like.

That cuts both ways: **a failure here is not automatically a bug.** If the
design genuinely changed, look at the page, decide the new one is right, and
re-record:

    REPORT_MAKER_UPDATE_GOLDEN=1 python3 -m unittest tests.test_render

The recorded hashes are committed; the pages they came from land in the demo
vault's `.build/`, which is generated and disposable, so this test never touches
the `out/` a person is reading from. The whole module skips when Typst is not
installed — a machine that cannot render is not a machine that fails.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import config, imagehash, pages  # noqa: E402
from engine.config import Config  # noqa: E402
from engine.workspace import reports  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "examples/demo-vault"
GOLDEN = Path(__file__).resolve().parent / "golden"

# Pages are rendered into the vault's `.build/`, not its `out/`. Rendering into
# `out/` would wipe and rebuild images somebody may be reading, and would race
# with any other build running against the demo vault at the same time — the
# renderer clears a report's page directory before it writes to it. `.build/` is
# generated and disposable by definition.
GOLDEN_OUT = ".build/golden-pages"

TYPST = shutil.which(os.environ.get("TYPST_BIN") or "typst")

# Set this and the test records what it sees instead of judging it.
UPDATE = os.environ.get("REPORT_MAKER_UPDATE_GOLDEN", "") not in ("", "0", "false")

# Bits of difference allowed between the rendered page and the recorded one.
TOLERANCE = 6


def golden_for(report_id: str) -> Path:
    """`tests/golden/<report id>.hash` — mirroring the report tree, the way
    `out/` does, so a nested report does not collide with a loose one."""
    return GOLDEN / f"{report_id}.hash"


def demo_config() -> Config:
    """The demo vault, with its output redirected to `GOLDEN_OUT`."""
    cfg = config.load(DEMO)
    vault = {**cfg.data["vault"], "out": GOLDEN_OUT}
    return replace(cfg, data={**cfg.data, "vault": vault})


@unittest.skipUnless(TYPST, "typst is not on PATH")
class GoldenRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cfg = demo_config()
        # `force` on purpose: page images are otherwise only rebuilt when they
        # are older than the PDF, and this test is meaningless if it is allowed
        # to grade an image left over from a previous design.
        with redirect_stdout(io.StringIO()):
            pages.build(cls.cfg, force=True)

    def test_the_vault_has_something_to_render(self) -> None:
        # Without this, an empty reports/ turns the test below into a loop over
        # nothing, which passes.
        self.assertTrue(reports(self.cfg), "examples/demo-vault has no reports")

    def test_page_one_still_looks_the_way_it_was_approved(self) -> None:
        recorded = []
        for report in reports(self.cfg):
            with self.subTest(report=report.id):
                page = report.pages_dir / "page-1.png"
                self.assertTrue(page.is_file(), f"{report.id} rendered no page 1")
                rendered = imagehash.dhash(page)
                golden = golden_for(report.id)

                if UPDATE:
                    golden.parent.mkdir(parents=True, exist_ok=True)
                    golden.write_text(rendered + "\n", encoding="utf-8")
                    recorded.append(f"{report.id} {rendered}")
                    continue

                self.assertTrue(
                    golden.is_file(),
                    f"no golden hash for {report.id} — record one with "
                    "REPORT_MAKER_UPDATE_GOLDEN=1",
                )
                distance = imagehash.hamming(
                    rendered, golden.read_text(encoding="utf-8").strip()
                )
                self.assertLessEqual(
                    distance,
                    TOLERANCE,
                    f"{report.id} renders {distance} bits away from its golden hash "
                    f"(tolerance {TOLERANCE}). Look at "
                    f"{page.relative_to(self.cfg.root)}; if the design changed on "
                    "purpose, re-record with REPORT_MAKER_UPDATE_GOLDEN=1.",
                )
        if recorded:
            print("recorded golden hashes:\n  " + "\n  ".join(recorded))


if __name__ == "__main__":
    unittest.main()
