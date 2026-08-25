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

## A golden is a recording of one machine, not a fact about the repository

A page is a rendering, and a rendering is a function of the font book of the
box that produced it. The brand names Didot, Helvetica Neue and Menlo; a Linux
runner has none of the three; Typst falls back to what it can find, and the
same commit legitimately hashes thirteen bits away from a hash recorded on a
Mac. That is not a regression, and the tolerance must never be widened to
swallow it — a tolerance wide enough to span two font stacks is wide enough to
miss the design change this test exists to catch.

Two rules follow, and between them they are the whole design of this module:

  * **Goldens are recorded per platform**, under `tests/golden/<sys.platform>/`.
    A platform is a bucket rather than a guarantee — two Linux boxes can have
    different font books — which is why the second rule is also needed. A
    platform with no folder here has no recording, and the module skips rather
    than grading a page against somebody else's machine.
  * **A render in fallback fonts is not graded at all.** If the first family of
    any brand role is missing from `typst fonts`, what this machine draws is
    not what the design says, and the module skips naming the font. The gate
    would otherwise be present but blind: with the families absent, editing
    `fonts.display` in the brand pack does not move a single bit of the hash,
    so the test would go on passing through exactly the change it is for.

Anyone re-recording should know what they are recording. The hash carries that
machine's fonts, its Typst build and its rasteriser, so record on a machine
whose font book matches the brand, commit the folder for that platform only,
and do not copy a hash from one platform's folder into another's — a golden
that was never rendered where it sits is a lie the test cannot detect.

That cuts both ways: **a failure here is not automatically a bug.** If the
design genuinely changed, look at the page, decide the new one is right, and
re-record — on every platform that has a folder, or the others start failing:

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
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import brand, config, imagehash, pages  # noqa: E402
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

# Bits of difference allowed between the rendered page and the recorded one, on
# the platform that recorded it. Never raise this to accommodate a second
# machine — record a golden for that machine instead. See the module docstring.
TOLERANCE = 6


def platform_key() -> str:
    """The folder this machine's goldens live under: `darwin`, `linux`, `win32`.

    `sys.platform` is the coarsest key that separates the font books that
    actually differ, and it is the finest one two people can share. Anything
    narrower — a hash of the installed families, say — would be more honest
    about what a golden really depends on and would also mean no two machines
    ever compared against the same recording, so nothing would ever be graded.
    The font check below covers the part of that risk that matters.
    """
    return sys.platform


def golden_for(report_id: str) -> Path:
    """`tests/golden/<platform>/<report id>.hash` — mirroring the report tree,
    the way `out/` does, so a nested report does not collide with a loose one."""
    return GOLDEN / platform_key() / f"{report_id}.hash"


def demo_config() -> Config:
    """The demo vault, with its output redirected to `GOLDEN_OUT`."""
    cfg = config.load(DEMO)
    vault = {**cfg.data["vault"], "out": GOLDEN_OUT}
    return replace(cfg, data={**cfg.data, "vault": vault})


def intended_families(cfg: Config) -> list[tuple[str, str]]:
    """(role, family) for the font each brand role actually asks for.

    A brand role is a fallback chain, and Typst reads it as one: the first name
    is what the design means and the rest are what it will settle for. So the
    first name is the one worth checking — a page set in the second is already
    a page nobody designed.
    """
    packs = brand.load(cfg)["fonts"]
    chains = {
        role: [names] if isinstance(names, str) else list(names)
        for role, names in packs.items()
    }
    return [(role, names[0]) for role, names in chains.items() if names]


def installed_families(binary: str) -> set[str] | None:
    """Every family `typst fonts` reports, or None if it could not be asked.

    Asking Typst rather than the operating system is the point: Typst carries
    its own embedded faces and its own name matching, so the only list that
    predicts what a compile will draw with is the one the compiler prints.
    """
    try:
        result = subprocess.run(
            [binary, "fonts"], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


@unittest.skipUnless(TYPST, "typst is not on PATH")
class GoldenRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cfg = demo_config()
        cls.check_the_render_is_the_intended_one()
        cls.check_this_platform_has_recordings()
        # `force` on purpose: page images are otherwise only rebuilt when they
        # are older than the PDF, and this test is meaningless if it is allowed
        # to grade an image left over from a previous design.
        with redirect_stdout(io.StringIO()):
            pages.build(cls.cfg, force=True)

    @classmethod
    def check_the_render_is_the_intended_one(cls) -> None:
        """Skip unless the fonts the brand names are the fonts Typst will use."""
        binary = shutil.which(cls.cfg.typst) or TYPST
        available = installed_families(binary)
        if available is None:
            raise unittest.SkipTest(
                f"`{binary} fonts` did not answer, so which families this "
                "machine would render with cannot be established"
            )
        missing = [
            f"{family!r} (brand fonts.{role})"
            for role, family in intended_families(cls.cfg)
            if family not in available
        ]
        if missing:
            raise unittest.SkipTest(
                "the demo vault's brand fonts are not installed, so this "
                "machine renders in Typst's fallbacks and the page is not the "
                "one the design describes: " + ", ".join(missing) + ". Install "
                "them, or point `typst --font-path` at them, to run this gate here."
            )

    @classmethod
    def check_this_platform_has_recordings(cls) -> None:
        """Skip unless somebody has recorded goldens on this kind of machine."""
        if UPDATE or (GOLDEN / platform_key()).is_dir():
            return
        raise unittest.SkipTest(
            f"no golden hashes recorded for {platform_key()} — a hash from "
            f"another platform is a different render, not a comparable one. "
            f"Record a set here with REPORT_MAKER_UPDATE_GOLDEN=1 and commit "
            f"tests/golden/{platform_key()}/."
        )

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

                # A missing golden here is a failure rather than a skip: the
                # class only got this far because this platform has recordings,
                # so a report without one is unrecorded, not unsupported — most
                # likely a report added to the demo vault and never approved.
                self.assertTrue(
                    golden.is_file(),
                    f"no golden hash for {report.id} on {platform_key()}, "
                    f"though other reports have one — look at the page and "
                    "record it with REPORT_MAKER_UPDATE_GOLDEN=1",
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
            print(
                f"recorded golden hashes for {platform_key()}:\n  "
                + "\n  ".join(recorded)
            )


if __name__ == "__main__":
    unittest.main()
