"""The PNG reader and the perceptual hash.

`engine/imagehash.py` is a decoder written from the specification rather than
taken from a library, and a decoder that reads bytes *nearly* right is the worst
kind: it produces a plausible number, the golden test fails, and the failure
points at a report design that never changed. So these tests do not check that
the module agrees with itself — they check it against pixels this file put in.

The fixtures are PNGs synthesised here with `zlib`, which lets the awkward cases
be constructed on purpose:

* the same picture written with each of the five scanline filters, encoded by an
  independent forward implementation, so a matching bug in both directions has
  nowhere to hide;
* the same picture written as greyscale, RGB, RGBA and palette;
* headers claiming 16-bit samples, interlacing and colour types nothing here
  emits, which must raise rather than return a number.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import imagehash  # noqa: E402

GREY, RGB, PALETTE, GREY_ALPHA, RGBA = 0, 2, 3, 4, 6
FILTERS = (0, 1, 2, 3, 4)


# ── fixtures ─────────────────────────────────────────────────────────────────


def solid(width: int, height: int, value: int) -> list[list[int]]:
    return [[value] * width for _ in range(height)]


def ramp(width: int, height: int, *, reverse: bool = False) -> list[list[int]]:
    """A left-to-right brightness ramp, or its mirror image."""
    row = [x * 255 // max(width - 1, 1) for x in range(width)]
    if reverse:
        row.reverse()
    return [list(row) for _ in range(height)]


def blocks(width: int, height: int, across: int = 8) -> list[list[int]]:
    """A blocky, asymmetric picture that scales with the image.

    Low frequency on purpose: a box filter averages a checkerboard back into
    flat grey, which would make every assertion below trivially true.
    """
    shades = (17, 208, 96, 250, 40, 142, 74, 190)
    tile_w, tile_h = max(width // across, 1), max(height // across, 1)
    return [
        [
            shades[((x // tile_w) * 5 + (y // tile_h) * 3) % len(shades)]
            for x in range(width)
        ]
        for y in range(height)
    ]


# ── a PNG encoder, written forwards ──────────────────────────────────────────


def _paeth(left: int, above: int, corner: int) -> int:
    estimate = left + above - corner
    to_left, to_above, to_corner = (
        abs(estimate - left),
        abs(estimate - above),
        abs(estimate - corner),
    )
    if to_left <= to_above and to_left <= to_corner:
        return left
    return above if to_above <= to_corner else corner


def _apply_filter(line: bytes, previous: bytes, kind: int, step: int) -> bytes:
    """Encode one scanline. The predictors are spelled out again here rather
    than shared with the decoder, so a wrong predictor cannot cancel itself."""
    if kind == 0:
        return bytes(line)
    out = bytearray(len(line))
    for index in range(len(line)):
        left = line[index - step] if index >= step else 0
        above = previous[index]
        corner = previous[index - step] if index >= step else 0
        if kind == 1:
            predicted = left
        elif kind == 2:
            predicted = above
        elif kind == 3:
            predicted = (left + above) // 2
        else:
            predicted = _paeth(left, above, corner)
        out[index] = (line[index] - predicted) & 0xFF
    return bytes(out)


def _chunk(kind: bytes, body: bytes) -> bytes:
    return (
        struct.pack(">I", len(body))
        + kind
        + body
        + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    )


def encode(
    pixels: list[list[int]],
    *,
    colour: int = RGB,
    filter_type: int = 0,
    alpha: int = 255,
    depth: int = 8,
    interlace: int = 0,
) -> bytes:
    """A PNG carrying `pixels` (greyscale values) in the requested colour type.

    `depth` and `interlace` only reach the header — they exist so a test can
    hand the reader a file it must refuse, and refusal happens before the image
    data is ever looked at.
    """
    height, width = len(pixels), len(pixels[0])
    step = {GREY: 1, RGB: 3, PALETTE: 1, GREY_ALPHA: 2, RGBA: 4}[colour]

    raw = bytearray()
    previous = bytes(width * step)
    for row in pixels:
        if colour in (GREY, PALETTE):
            line = bytes(row)
        elif colour == RGB:
            line = bytes(value for pixel in row for value in (pixel, pixel, pixel))
        elif colour == GREY_ALPHA:
            line = bytes(value for pixel in row for value in (pixel, alpha))
        else:
            line = bytes(
                value for pixel in row for value in (pixel, pixel, pixel, alpha)
            )
        raw.append(filter_type)
        raw += _apply_filter(line, previous, filter_type, step)
        previous = line

    header = struct.pack(">IIBBBBB", width, height, depth, colour, 0, 0, interlace)
    out = imagehash.SIGNATURE + _chunk(b"IHDR", header)
    if colour == PALETTE:
        # 256 grey entries, so the index *is* the brightness and the palette
        # picture and the greyscale one are the same page.
        out += _chunk(b"PLTE", bytes(v for i in range(256) for v in (i, i, i)))
    return out + _chunk(b"IDAT", zlib.compress(bytes(raw))) + _chunk(b"IEND", b"")


class Fixtures(unittest.TestCase):
    """Writes PNGs into a scratch directory and hands back their paths."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.counter = 0

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def png(self, pixels: list[list[int]], **options) -> Path:
        self.counter += 1
        path = self.dir / f"fixture-{self.counter}.png"
        path.write_bytes(encode(pixels, **options))
        return path


# ── the decoder ──────────────────────────────────────────────────────────────


class Decoding(Fixtures):
    def test_the_pixels_come_back_exactly(self) -> None:
        # The anchor for everything else: not "the module agrees with itself"
        # but "these are the bytes that went in".
        pixels = blocks(37, 23)  # odd dimensions — nothing lines up with a word
        for filter_type in FILTERS:
            for colour in (GREY, RGB, PALETTE, RGBA):
                with self.subTest(filter=filter_type, colour=colour):
                    path = self.png(pixels, colour=colour, filter_type=filter_type)
                    gray, width, height = imagehash.load_gray(path)
                    self.assertEqual((width, height), (37, 23))
                    self.assertEqual(gray, pixels)

    def test_transparency_is_composited_over_white(self) -> None:
        # A transparent pixel on a page is paper. Reading it as the black that
        # usually sits in the colour channels would invert the whole hash.
        path = self.png(solid(16, 16, 0), colour=RGBA, alpha=0)
        gray, _, _ = imagehash.load_gray(path)
        self.assertEqual(gray, solid(16, 16, 255))

    def test_unsupported_formats_raise_rather_than_guess(self) -> None:
        pixels = ramp(16, 16)
        cases = {
            "16-bit": dict(depth=16),
            "interlaced": dict(interlace=1),
            "greyscale+alpha": dict(colour=GREY_ALPHA),
        }
        for name, options in cases.items():
            with self.subTest(case=name):
                path = self.png(pixels, **options)
                with self.assertRaises(imagehash.ImageHashError):
                    imagehash.load_gray(path)

    def test_a_file_that_is_not_a_png_says_so(self) -> None:
        path = self.dir / "not-an-image.png"
        path.write_bytes(b"GIF89a and then some")
        with self.assertRaises(imagehash.ImageHashError) as caught:
            imagehash.load_gray(path)
        self.assertIn("signature", str(caught.exception))

    def test_a_truncated_file_raises(self) -> None:
        whole = encode(ramp(16, 16))
        path = self.dir / "cut-short.png"
        path.write_bytes(whole[: len(whole) // 2])
        with self.assertRaises(imagehash.ImageHashError):
            imagehash.load_gray(path)

    def test_a_missing_file_raises(self) -> None:
        with self.assertRaises(imagehash.ImageHashError):
            imagehash.load_gray(self.dir / "never-written.png")


# ── the hash ─────────────────────────────────────────────────────────────────


class Hashing(Fixtures):
    def test_identical_pictures_hash_identically(self) -> None:
        pixels = blocks(64, 64)
        first, second = self.png(pixels), self.png(pixels)
        self.assertEqual(imagehash.dhash(first), imagehash.dhash(second))

    def test_the_scanline_filter_is_invisible_to_the_hash(self) -> None:
        # Encoders pick filters per scanline by heuristic, and the heuristic
        # changes between versions. It must not reach the hash.
        pixels = blocks(64, 64)
        hashes = {
            filter_type: imagehash.dhash(self.png(pixels, filter_type=filter_type))
            for filter_type in FILTERS
        }
        self.assertEqual(len(set(hashes.values())), 1, hashes)

    def test_every_supported_colour_type_reads_the_same_page(self) -> None:
        pixels = blocks(64, 64)
        hashes = {
            colour: imagehash.dhash(self.png(pixels, colour=colour))
            for colour in (GREY, RGB, PALETTE, RGBA)
        }
        self.assertEqual(len(set(hashes.values())), 1, hashes)
        # …and the picture is not so flat that "all the same" is meaningless.
        self.assertNotEqual(set(hashes.pop(GREY)), {"0"})

    def test_a_page_with_nothing_on_it_records_no_differences(self) -> None:
        self.assertEqual(imagehash.dhash(self.png(solid(32, 32, 200))), "0" * 16)

    def test_an_inverted_page_is_as_far_away_as_a_page_can_be(self) -> None:
        forwards = imagehash.dhash(self.png(ramp(64, 64)))
        backwards = imagehash.dhash(self.png(ramp(64, 64, reverse=True)))
        self.assertEqual(imagehash.hamming(forwards, backwards), 64)

    def test_the_same_page_at_a_different_resolution_stays_close(self) -> None:
        # This is what the box filter buys, and why the golden test can afford a
        # tolerance instead of demanding an exact match: rerendering at a
        # different ppi is a rerender, not a redesign.
        small = imagehash.dhash(self.png(blocks(64, 64)))
        large = imagehash.dhash(self.png(blocks(96, 96)))
        self.assertLessEqual(imagehash.hamming(small, large), 6)

    def test_a_smaller_grid_gives_a_shorter_hash(self) -> None:
        self.assertEqual(len(imagehash.dhash(self.png(blocks(64, 64)), size=4)), 4)
        with self.assertRaises(imagehash.ImageHashError):
            imagehash.dhash(self.png(blocks(8, 8)), size=0)

    def test_a_picture_smaller_than_the_grid_still_hashes(self) -> None:
        # Not a real page, but a decoder that crashes on a 3x2 image is a
        # decoder nobody can debug with a tiny fixture.
        self.assertEqual(len(imagehash.dhash(self.png(blocks(3, 2)))), 16)


class Hamming(unittest.TestCase):
    def test_counts_differing_bits(self) -> None:
        self.assertEqual(imagehash.hamming("00", "ff"), 8)
        self.assertEqual(imagehash.hamming("f0f0", "f0f0"), 0)
        self.assertEqual(imagehash.hamming("0000", "0003"), 2)

    def test_two_hashes_of_different_sizes_cannot_be_compared(self) -> None:
        with self.assertRaises(imagehash.ImageHashError):
            imagehash.hamming("0000", "00")

    def test_something_that_is_not_a_hash_says_so(self) -> None:
        with self.assertRaises(imagehash.ImageHashError):
            imagehash.hamming("zzzz", "0000")


if __name__ == "__main__":
    unittest.main()
