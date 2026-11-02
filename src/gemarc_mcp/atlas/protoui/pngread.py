"""A small PNG reader for the pixel probes of ``png.py`` (design A3 4.6, SV-208): standard library only, lazy by row.

Only what a browser screenshot needs is understood: non-interlaced images of 8 bits per channel in the colour types gray (0), RGB (2), palette (3),
gray with alpha (4) and RGBA (6). Anything else raises ``PngError`` with ``unsupported`` set (the probe cannot be applied, which is not a verdict
about the picture); a damaged file (signature, chunk checksum, missing end chunk, wrong amount of image data) raises ``PngError`` with
``unsupported`` unset.

The probes read a handful of pixels, so rows are unfiltered on demand: a row only needs the row above it when its filter is Up, Average or Paeth, so
the walk back from the wanted row stops at the nearest row of filter None or Sub (every row of a screenshot of a flat drawing is mostly Up). Up is done
with big-integer arithmetic on the whole row, Sub with a running sum per channel; Average and Paeth are per byte (measured numbers: see the handoff
of task u3). The reader keeps every row it unfilters, so reading many pixels of one image costs one pass over the rows up to the lowest one asked for.
"""
from __future__ import annotations

import itertools
import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_BYTES = 8 * 1024 * 1024        # a screenshot of a screen is tens of KB; this only bounds a hostile or broken file
MAX_PIXELS = 4_000_000             # the longest edge of a rendering is capped at 1600 px, so 1600 x 1600 = 2.56 M is the most a render can give
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
_SWAR: dict = {}


class PngError(ValueError):
    """The file cannot be read as an image. ``unsupported`` is true for a valid file of a kind this reader does not handle."""

    def __init__(self, message: str, unsupported: bool = False):
        super().__init__(message)
        self.unsupported = unsupported


def _swar_masks(n: int) -> tuple:
    masks = _SWAR.get(n)
    if masks is None:
        low = int.from_bytes(b"\x7f" * n, "little")
        masks = _SWAR[n] = (low, low ^ int.from_bytes(b"\xff" * n, "little"))
    return masks


def _add_rows(cur: bytes, prev: bytes) -> bytes:
    """Byte-wise ``(cur + prev) mod 256`` of two equally long rows: the lower seven bits are added in one big-integer sum (no carry leaves a byte),
    the top bit is the exclusive or of the three top bits."""
    n = len(cur)
    low, high = _swar_masks(n)
    a, b = int.from_bytes(cur, "little"), int.from_bytes(prev, "little")
    return (((a & low) + (b & low)) ^ ((a ^ b) & high)).to_bytes(n, "little")


def _sub(cur: bytes, bpp: int) -> bytearray:
    out = bytearray(cur)
    for c in range(bpp):
        out[c::bpp] = bytes(itertools.accumulate(cur[c::bpp], lambda x, y: (x + y) & 255))
    return out


def _average(cur: bytes, prev: bytes, bpp: int) -> bytearray:
    out = bytearray(cur)
    for i in range(min(bpp, len(cur))):
        out[i] = (cur[i] + (prev[i] >> 1)) & 255
    for i in range(bpp, len(cur)):
        out[i] = (cur[i] + ((out[i - bpp] + prev[i]) >> 1)) & 255
    return out


def _paeth(cur: bytes, prev: bytes, bpp: int) -> bytearray:
    out = bytearray(cur)
    for i in range(min(bpp, len(cur))):
        out[i] = (cur[i] + prev[i]) & 255            # the pixel before is 0, so the predictor is the byte above
    for i in range(bpp, len(cur)):
        a, b, c = out[i - bpp], prev[i], prev[i - bpp]
        pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - c - c)
        out[i] = (cur[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
    return out


class Image:
    """A decoded PNG. ``pixel(x, y)`` is ``(red, green, blue, alpha)``; ``width`` and ``height`` are read from the header without decoding anything."""

    def __init__(self, data: bytes):
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise PngError("not bytes")
        data = bytes(data)
        if len(data) > MAX_BYTES:
            raise PngError("file larger than the limit", unsupported=True)
        if data[:8] != SIGNATURE:
            raise PngError("not a PNG file")
        pos, header, palette, idat, end = 8, None, b"", [], False
        while pos + 12 <= len(data):
            (length,), kind = struct.unpack(">I", data[pos:pos + 4]), data[pos + 4:pos + 8]
            body = data[pos + 8:pos + 8 + length]
            tail = data[pos + 8 + length:pos + 12 + length]
            if len(body) != length or len(tail) != 4 or struct.unpack(">I", tail)[0] != zlib.crc32(kind + body) & 0xFFFFFFFF:
                raise PngError("damaged chunk " + kind.decode("latin-1", "replace")[:4])
            if kind == b"IHDR":
                header = body
            elif kind == b"PLTE":
                palette = body
            elif kind == b"IDAT":
                idat.append(body)
            elif kind == b"IEND":
                end = True
                break
            pos += 12 + length
        if header is None or len(header) != 13 or not end or not idat:
            raise PngError("the header, the image data or the end chunk is missing")
        self.width, self.height, depth, self.color, method, filtering, interlace = struct.unpack(">IIBBBBB", header)
        if method != 0 or filtering != 0 or self.color not in _CHANNELS:
            raise PngError("unknown method or colour type", unsupported=True)
        if depth != 8 or interlace != 0:
            raise PngError(f"bit depth {depth} or interlace {interlace}", unsupported=True)
        if not (0 < self.width and 0 < self.height and self.width * self.height <= MAX_PIXELS):
            raise PngError("image size out of range", unsupported=True)
        if self.color == 3:
            if not palette or len(palette) % 3:
                raise PngError("palette missing or damaged")
            self._palette = [tuple(palette[i:i + 3]) for i in range(0, len(palette), 3)]
        self.bpp = _CHANNELS[self.color]
        self._stride = self.width * self.bpp
        expected = self.height * (1 + self._stride)
        engine = zlib.decompressobj()
        try:
            self._raw = engine.decompress(b"".join(idat), expected + 1)
        except zlib.error:
            raise PngError("image data cannot be inflated") from None
        if len(self._raw) != expected or not engine.eof:
            raise PngError("image data has the wrong length")
        self._rows: dict = {}
        self._zero = bytes(self._stride)

    def _filter(self, y: int) -> int:
        return self._raw[y * (1 + self._stride)]

    def _decode(self, y: int, prev: bytes) -> bytes:
        start = y * (1 + self._stride)
        kind, cur = self._raw[start], self._raw[start + 1:start + 1 + self._stride]
        if kind == 0:
            return cur
        if kind == 1:
            return bytes(_sub(cur, self.bpp))
        if kind == 2:
            return _add_rows(cur, prev)
        if kind == 3:
            return bytes(_average(cur, prev, self.bpp))
        if kind == 4:
            return bytes(_paeth(cur, prev, self.bpp))
        raise PngError(f"unknown filter {kind} in row {y}")

    def row(self, y: int) -> bytes:
        """The unfiltered bytes of row ``y``."""
        if not 0 <= y < self.height:
            raise IndexError(y)
        top = y
        while top > 0 and top not in self._rows and self._filter(top) in (2, 3, 4):
            top -= 1
        for r in range(top, y + 1):
            if r not in self._rows:
                self._rows[r] = self._decode(r, self._rows.get(r - 1, self._zero))   # the row above is missing only where the filter does not read it
        return self._rows[y]

    def pixel(self, x: int, y: int) -> tuple:
        if not 0 <= x < self.width:
            raise IndexError(x)
        data, at = self.row(y), x * self.bpp
        if self.color == 6:
            return tuple(data[at:at + 4])
        if self.color == 2:
            return (data[at], data[at + 1], data[at + 2], 255)
        if self.color == 0:
            return (data[at], data[at], data[at], 255)
        if self.color == 4:
            return (data[at], data[at], data[at], data[at + 1])
        index = data[at]
        if index >= len(self._palette):
            raise PngError("palette index out of range")
        return (*self._palette[index], 255)


def size_of(data: bytes) -> tuple:
    """``(width, height)`` from the header chunk alone (the signature and the header are checked, nothing is inflated)."""
    if data[:8] != SIGNATURE or data[12:16] != b"IHDR" or len(data) < 24:
        raise PngError("not a PNG file")
    return struct.unpack(">II", data[16:24])
