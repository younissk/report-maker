"""A perceptual hash for a rendered page, with no image library.

The golden-render test asks one question: does page 1 of this report still look
the way it looked when somebody approved it? Byte comparison cannot answer it.
Typst rewrites the PNG stream between versions, font hinting and antialiasing
differ between machines, and a one-pixel change in glyph rasterisation changes
every compressed byte downstream of it. Pillow could answer it, but `engine/`
has no third-party dependencies and is not going to grow one for a test.

So this module reads the PNG itself. `zlib` inflates, `struct` reads the
headers, and the five PNG filters are undone here scanline by scanline — which
is the whole of a decoder for the subset Typst emits: 8-bit, non-interlaced,
greyscale or RGB or RGBA. Palette images come along too, because handmade
fixtures are the cheapest way to test a decoder. Everything else raises rather
than guessing: a hash computed from misread bytes is worse than no hash at all,
because it fails a test for a reason nobody can trace back to a cause.

The hash is a difference hash. The page is reduced to a 9x8 grid, each cell is
compared with its right-hand neighbour, and the 64 answers are the hash — a
record of *where the page gets lighter*, which survives a rerender and does not
survive a design change. The downsample is a box filter, every source pixel
inside a cell contributing to its average, and that part is load-bearing: with
nearest-neighbour sampling the single pixel a cell happens to land on may be
inside a glyph in one Typst version and beside it in the next, so the hash
drifts for reasons that have nothing to do with the design.

    dhash(page) -> "9c8e0e3a1c3c7e7e"      16 hex digits, 64 bits
    hamming(a, b) -> 0..64                 how far apart two pages are
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Colour type → samples per pixel. Type 4 (greyscale + alpha) is deliberately
# absent: nothing in this repository writes one, and an untested branch in a
# decoder is a bug waiting for the day it is finally reached.
CHANNELS = {0: 1, 2: 3, 3: 1, 6: 4}

# Rec. 601 luma weights, in thousandths, so the decoder never leaves integers.
LUMA_R, LUMA_G, LUMA_B = 299, 587, 114

# The grid is (SIZE + 1) x SIZE, because a difference hash compares each cell
# with the one to its right and so needs one spare column.
HASH_SIZE = 8


class ImageHashError(RuntimeError):
    pass


# ── the slice of PNG we read ─────────────────────────────────────────────────


@dataclass(frozen=True)
class _Header:
    """IHDR, validated. Anything this dataclass exists for is 8-bit and flat."""

    width: int
    height: int
    colour: int

    @property
    def channels(self) -> int:
        return CHANNELS[self.colour]

    @property
    def stride(self) -> int:
        """Bytes per unfiltered scanline — no filter byte, depth is always 8."""
        return self.width * self.channels


def _chunks(data: bytes):
    """Every chunk in the file, as (four-byte type, body).

    CRCs are not verified. The input is a file this process just asked Typst to
    write, or a fixture written a few lines earlier by a test; a checksum guards
    against a transport that is not in play here, and inflate will fail loudly
    on corruption anyway.
    """
    if not data.startswith(SIGNATURE):
        raise ImageHashError("not a PNG file (bad signature)")
    offset = len(SIGNATURE)
    while offset + 8 <= len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        kind = data[offset + 4 : offset + 8]
        body = data[offset + 8 : offset + 8 + length]
        if len(body) != length:
            raise ImageHashError(f"truncated {kind.decode('latin-1')} chunk")
        yield kind, body
        offset += 12 + length  # length + type + body + CRC


def _header(body: bytes) -> _Header:
    if len(body) != 13:
        raise ImageHashError("malformed IHDR")
    width, height, depth, colour, compression, filters, interlace = struct.unpack(
        ">IIBBBBB", body
    )
    if width < 1 or height < 1:
        raise ImageHashError(f"empty image ({width}x{height})")
    if depth != 8:
        raise ImageHashError(f"only 8-bit PNGs are supported, this one is {depth}-bit")
    if colour not in CHANNELS:
        raise ImageHashError(
            f"unsupported PNG colour type {colour} "
            "(supported: 0 greyscale, 2 RGB, 3 palette, 6 RGBA)"
        )
    if compression != 0 or filters != 0:
        raise ImageHashError("unsupported PNG compression or filter method")
    if interlace != 0:
        raise ImageHashError("interlaced PNGs are not supported")
    return _Header(width=width, height=height, colour=colour)


def _unfilter(raw: bytes, header: _Header) -> bytearray:
    """Undo the per-scanline filters, giving one flat block of samples.

    Every filter predicts a byte from its left neighbour (`a`), the byte above
    it (`b`) and the byte above-left (`c`), and stores the difference. Decoding
    is therefore strictly sequential — each byte needs the reconstructed one
    before it — which is why this is a byte loop and not a slice expression.
    """
    stride, height = header.stride, header.height
    step = header.channels  # bytes per pixel; depth is 8, so channels == step
    if len(raw) < (stride + 1) * height:
        raise ImageHashError("PNG image data is shorter than its header claims")

    out = bytearray(stride * height)
    previous = bytearray(stride)
    cursor = 0
    for row in range(height):
        kind = raw[cursor]
        line = bytearray(raw[cursor + 1 : cursor + 1 + stride])
        cursor += stride + 1

        if kind == 0:  # None
            pass
        elif kind == 1:  # Sub
            for index in range(step, stride):
                line[index] = (line[index] + line[index - step]) & 0xFF
        elif kind == 2:  # Up — the one filter with no left-hand dependency
            line = bytearray(
                (value + above) & 0xFF for value, above in zip(line, previous)
            )
        elif kind == 3:  # Average
            for index in range(step):
                line[index] = (line[index] + (previous[index] >> 1)) & 0xFF
            for index in range(step, stride):
                line[index] = (
                    line[index] + ((line[index - step] + previous[index]) >> 1)
                ) & 0xFF
        elif kind == 4:  # Paeth
            for index in range(step):
                line[index] = (line[index] + previous[index]) & 0xFF
            for index in range(step, stride):
                left = line[index - step]
                above = previous[index]
                corner = previous[index - step]
                estimate = left + above - corner
                to_left = abs(estimate - left)
                to_above = abs(estimate - above)
                to_corner = abs(estimate - corner)
                if to_left <= to_above and to_left <= to_corner:
                    predicted = left
                elif to_above <= to_corner:
                    predicted = above
                else:
                    predicted = corner
                line[index] = (line[index] + predicted) & 0xFF
        else:
            raise ImageHashError(f"unknown PNG scanline filter {kind}")

        out[row * stride : (row + 1) * stride] = line
        previous = line
    return out


# ── brightness ───────────────────────────────────────────────────────────────


def _palette_luma(plte: bytes, trns: bytes) -> list[int]:
    """A 256-entry index → brightness table for a palette image.

    `tRNS` gives per-entry alpha. It is composited against white here for the
    same reason RGBA is: a transparent pixel on a page is paper, and reading it
    as the black that usually sits underneath it would invert the hash.
    """
    if not plte or len(plte) % 3:
        raise ImageHashError("palette image with a missing or malformed PLTE chunk")
    table = [0] * 256
    for index in range(len(plte) // 3):
        red, green, blue = plte[index * 3 : index * 3 + 3]
        value = (LUMA_R * red + LUMA_G * green + LUMA_B * blue) // 1000
        alpha = trns[index] if index < len(trns) else 255
        if alpha != 255:
            value = (value * alpha + 255 * (255 - alpha)) // 255
        table[index] = value
    return table


def _rows(
    samples: bytearray, header: _Header, palette: list[int] | None
) -> list[list[int]]:
    """Flat samples → one list of 0–255 brightnesses per scanline."""
    stride, width = header.stride, header.width
    grid: list[list[int]] = []
    for row in range(header.height):
        line = samples[row * stride : (row + 1) * stride]
        if header.colour == 0:  # greyscale — the sample already is the value
            grid.append(list(line))
        elif header.colour == 3:
            grid.append([palette[index] for index in line])  # type: ignore[index]
        elif header.colour == 2:
            grid.append(
                [
                    (LUMA_R * red + LUMA_G * green + LUMA_B * blue) // 1000
                    for red, green, blue in zip(line[0::3], line[1::3], line[2::3])
                ]
            )
        else:  # RGBA
            alpha = line[3::4]
            triples = zip(line[0::4], line[1::4], line[2::4])
            if min(alpha) == 255:
                # The common case by far — a Typst page is opaque. Worth the one
                # C-level scan to skip a multiply and a divide per pixel.
                grid.append(
                    [
                        (LUMA_R * red + LUMA_G * green + LUMA_B * blue) // 1000
                        for red, green, blue in triples
                    ]
                )
            else:
                # Luma is linear in the channels, so compositing the luma over
                # white is the same as compositing each channel and then taking
                # the luma — one blend instead of three.
                grid.append(
                    [
                        (
                            ((LUMA_R * red + LUMA_G * green + LUMA_B * blue) // 1000)
                            * opacity
                            + 255 * (255 - opacity)
                        )
                        // 255
                        for (red, green, blue), opacity in zip(triples, alpha)
                    ]
                )
    return grid


def load_gray(path: Path | str) -> tuple[list[list[int]], int, int]:
    """One PNG as rows of 0–255 brightness, with its width and height.

    Public because a caller debugging a hash mismatch wants the pixels, not
    another hash.
    """
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as error:
        raise ImageHashError(f"cannot read {path}: {error}") from error

    header: _Header | None = None
    plte = b""
    trns = b""
    compressed: list[bytes] = []
    for kind, body in _chunks(data):
        if kind == b"IHDR":
            header = _header(body)
        elif kind == b"PLTE":
            plte = body
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IDAT":
            compressed.append(body)
        elif kind == b"IEND":
            break
    if header is None:
        raise ImageHashError(f"{path.name} has no IHDR chunk")
    if not compressed:
        raise ImageHashError(f"{path.name} has no image data")

    try:
        samples = _unfilter(zlib.decompress(b"".join(compressed)), header)
    except zlib.error as error:
        raise ImageHashError(f"{path.name}: corrupt image data ({error})") from error

    palette = _palette_luma(plte, trns) if header.colour == 3 else None
    return _rows(samples, header, palette), header.width, header.height


# ── the hash ─────────────────────────────────────────────────────────────────


def _bands(size: int, count: int) -> list[tuple[int, int]]:
    """`count` half-open ranges spanning `size`, none of them empty.

    Bands may overlap when the image is smaller than the grid, which is what the
    `start + 1` floor is for. That case does not arise for a rendered page; it
    arises for a fixture, and a fixture should get a hash rather than a crash.
    """
    bands = []
    for index in range(count):
        start = index * size // count
        stop = min(max((index + 1) * size // count, start + 1), size)
        bands.append((start, stop))
    return bands


def _cells(
    gray: list[list[int]], width: int, height: int, columns: int, rows: int
) -> list[list[float]]:
    """The box-filtered downsample: the mean brightness of each grid cell."""
    horizontal = _bands(width, columns)
    vertical = _bands(height, rows)
    grid: list[list[float]] = []
    for top, bottom in vertical:
        totals = [0] * columns
        for line in gray[top:bottom]:
            # One slice-and-sum per column rather than a per-pixel loop: the
            # arithmetic is the same, but it happens in C, and a page at 110 ppi
            # is well over a million pixels.
            for index, (left, right) in enumerate(horizontal):
                totals[index] += sum(line[left:right])
        depth = bottom - top
        grid.append(
            [
                total / (depth * (right - left))
                for total, (left, right) in zip(totals, horizontal)
            ]
        )
    return grid


def dhash(path: Path | str, size: int = HASH_SIZE) -> str:
    """The difference hash of a PNG, as `size * size / 4` hex digits.

    Bit set means "this cell is lighter than the one to its right". Read
    left-to-right, top-to-bottom, most significant bit first.
    """
    if size < 1:
        raise ImageHashError(f"hash size must be at least 1, got {size}")
    gray, width, height = load_gray(path)
    bits = 0
    for row in _cells(gray, width, height, size + 1, size):
        for left, right in zip(row, row[1:]):
            bits = (bits << 1) | int(left > right)
    return f"{bits:0{(size * size + 3) // 4}x}"


def hamming(a: str, b: str) -> int:
    """How many bits two hashes disagree on — 0 is identical, 64 is inverted."""
    if len(a) != len(b):
        raise ImageHashError(
            f"cannot compare hashes of different sizes: {len(a)} vs {len(b)} hex digits"
        )
    try:
        return (int(a, 16) ^ int(b, 16)).bit_count()
    except ValueError as error:
        raise ImageHashError(f"not a hexadecimal hash: {a!r} / {b!r}") from error
