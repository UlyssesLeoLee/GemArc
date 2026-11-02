"""Test support for the PNG step of the screens (``protoui/png.py``, ``protoui/pngread.py``; design A3 4.6, U3): a PNG encoder with all five row
filters, a small SVG drawing routine that stands in for the browser (it paints the rectangles of an SVG with their CSS fill, nothing else), a launcher double
that records its calls and can misbehave the way a browser does, a clock the tests move by hand, and a few synthetic SVGs. Test helper only, standard
library only; it never starts a process (the thin launcher for real Edge lives in ``test_gate_png.py``).

``encode_png(width, height, rows, color, filters)``   a PNG; ``filters`` is a list of filter numbers used for the rows in turn
``paint(svg, scale)``                                  the stand-in screenshot of an SVG as PNG bytes (white where nothing is painted)
``FakeBrowser``                                        the launcher double; ``mode`` says how it behaves
``make_dist(root, name)``                              writes the SVGs of one fixed build of ``screens_fixtures`` into ``root/screens``
``mini_svg(width, canvas_height, groups)``             a valid screen SVG of hand-written widget groups; ``box(...)`` makes one group
"""
from __future__ import annotations

import re
import struct
import threading
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass
from pathlib import Path

from gemarc_mcp.atlas.protoui import svg as svgmod

from . import screens_fixtures as fx

SVGNS = "{http://www.w3.org/2000/svg}"
SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
WHITE = (255, 255, 255)


# ---------------------------------------------------------------------------------------------------------------
# PNG encoder
# ---------------------------------------------------------------------------------------------------------------
def chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else b if pb <= pc else c


def filter_row(kind: int, row: bytes, prev: bytes, bpp: int) -> bytes:
    """The forward filter of the PNG specification, byte by byte (slow, for small images and for rows of the stand-in)."""
    out = bytearray(len(row))
    for i, x in enumerate(row):
        a = row[i - bpp] if i >= bpp else 0
        b = prev[i]
        c = prev[i - bpp] if i >= bpp else 0
        if kind == 0:
            out[i] = x
        elif kind == 1:
            out[i] = (x - a) & 255
        elif kind == 2:
            out[i] = (x - b) & 255
        elif kind == 3:
            out[i] = (x - ((a + b) >> 1)) & 255
        elif kind == 4:
            out[i] = (x - _paeth(a, b, c)) & 255
        else:
            raise ValueError(kind)
    return bytes(out)


def encode_png(width: int, height: int, rows, color: int = 2, filters=None, depth: int = 8, interlace: int = 0, palette: bytes = b"", level: int = 1) -> bytes:
    """A PNG of the given rows of raw pixel bytes. ``filters`` (a sequence) gives the filter number of row ``y`` as ``filters[y % len(filters)]``;
    without it every row is stored with filter 0 (fast); the other filters are computed byte by byte."""
    bpp = CHANNELS[color]
    raw = bytearray()
    prev = bytes(width * bpp)
    for y, row in enumerate(rows):
        row = bytes(row)
        kind = filters[y % len(filters)] if filters else 0
        if kind == 0:
            raw += b"\x00" + row
        elif kind == 2:
            raw += b"" + bytes((x - y) & 255 for x, y in zip(row, prev))
        else:
            raw += bytes([kind]) + filter_row(kind, row, prev, bpp)
        prev = row
    out = SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, color, 0, 0, interlace))
    if palette:
        out += chunk(b"PLTE", palette)
    return out + chunk(b"IDAT", zlib.compress(bytes(raw), level)) + chunk(b"IEND", b"")


def decode_png_naive(data: bytes, limit: "int | None" = None) -> list:
    """The first ``limit`` rows (all by default) of an 8-bit non-interlaced PNG, unfiltered with textbook loops that share no code with ``pngread``
    (an independent second reader for the tests)."""
    pos, header, idat = 8, None, b""
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        pos += 12 + length
    width, height, _, color = header[:4]
    bpp = CHANNELS[color]
    raw = zlib.decompress(idat)
    stride = width * bpp
    rows, prev = [], bytes(stride)
    for y in range(height if limit is None else min(limit, height)):
        kind, line = raw[y * (stride + 1)], raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)]
        out = bytearray(line)
        for i in range(stride):
            a = out[i - bpp] if i >= bpp else 0
            if kind == 0:
                continue
            if kind == 1:
                out[i] = (line[i] + a) & 255
            elif kind == 2:
                out[i] = (line[i] + prev[i]) & 255
            elif kind == 3:
                out[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
            else:
                out[i] = (line[i] + _paeth(a, prev[i], prev[i - bpp] if i >= bpp else 0)) & 255
        rows.append(bytes(out))
        prev = bytes(out)
    return rows


# ---------------------------------------------------------------------------------------------------------------
# The stand-in for the browser
# ---------------------------------------------------------------------------------------------------------------
def _hex(text: str):
    text = text.strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", text):
        return (int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16))
    if re.fullmatch(r"#[0-9a-fA-F]{3}", text):
        return tuple(int(c * 2, 16) for c in text[1:])
    return None


def css_fills(root) -> dict:
    """``{class: (order, rgb or None)}`` of the class rules that set a fill (written independently of ``png.py``)."""
    fills, order = {}, 0
    for style in root.iter(SVGNS + "style"):
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", style.text or ""):
            m = re.search(r"fill:\s*([^;]+)", body)
            if m:
                for name in selector.split(","):
                    order += 1
                    fills[name.strip().lstrip(".")] = (order, _hex(m.group(1)))
    return fills


_PAINTED: dict = {}


def paint(svg: str, scale: float = 1.0, base=WHITE, filters=None) -> bytes:
    """The stand-in screenshot: the rectangles of the SVG painted in document order with the fill their class gives (a class without a hex fill paints
    nothing), on a page of colour ``base`` of the size of the SVG times ``scale``. Text, circles, paths and strokes are not drawn."""
    key = (hash_text(svg), scale, tuple(base), tuple(filters or ()))
    if key in _PAINTED:
        return _PAINTED[key]
    root = ET.fromstring(svg)
    width, height = int(float(root.get("width"))), int(float(root.get("height")))
    ow, oh = max(1, round(width * scale)), max(1, round(height * scale))
    rows = [bytearray(bytes(base) * ow) for _ in range(oh)]
    fills = css_fills(root)

    def colour(classes: str):
        best = None
        for name in classes.split():
            if name in fills and (best is None or fills[name][0] > best[0]):
                best = fills[name]
        return best[1] if best else None

    def walk(element, dy: int) -> None:
        for child in element:
            tag = child.tag[len(SVGNS):]
            if tag == "g":
                m = re.fullmatch(r"translate\(\s*(-?\d+)\s+(-?\d+)\s*\)", child.get("transform", ""))
                walk(child, dy + (int(m.group(2)) if m else 0))
            elif tag == "rect":
                rgb = colour(child.get("class", ""))
                if rgb is None:
                    continue
                x, y, w, h = (float(child.get(k)) for k in ("x", "y", "width", "height"))
                x0, x1 = max(0, round(x * scale)), min(ow, round((x + w) * scale))
                y0, y1 = max(0, round((y + dy) * scale)), min(oh, round((y + dy + h) * scale))
                if x1 > x0:
                    piece = bytes(rgb) * (x1 - x0)
                    for yy in range(y0, y1):
                        rows[yy][x0 * 3:x1 * 3] = piece

    walk(root, 0)
    data = encode_png(ow, oh, rows, 2, filters)
    _PAINTED[key] = data
    return data


def hash_text(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pixel(png: bytes, x: int, y: int) -> tuple:
    """One pixel of a PNG through the naive reader (a test oracle: slow, whole image)."""
    return tuple(decode_png_naive(png)[y][x * 3:x * 3 + 3])


# ---------------------------------------------------------------------------------------------------------------
# Launcher double, clock
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class Launch:
    """What a launcher returns: the attributes ``png.py`` reads (the ``_Launch`` of ``gate/smoke.py`` has the same ones)."""

    returncode: "int | None" = 0
    timed_out: bool = False
    seconds: float = 0.0
    error: "str | None" = None
    overflow: "str | None" = None
    read_error: "str | None" = None


class FakeClock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def parse_args(args) -> dict:
    """The parts of a screenshot command line the double needs: the file the picture goes to, the page it is of, the window and the scale."""
    out = {"browser": args[0], "url": args[-1], "flags": list(args[1:-1])}
    for a in args:
        for flag, key in (("--screenshot=", "shot"), ("--window-size=", "window"), ("--force-device-scale-factor=", "scale")):
            if a.startswith(flag):
                out[key] = a[len(flag):]
    out["page"] = Path(urllib.request.url2pathname(urllib.parse.urlparse(args[-1]).path))
    return out


class FakeBrowser:
    """A launcher ``(args, timeout, scratch)`` that paints the page of the URL it is given with ``paint`` and writes it to the ``--screenshot=`` file.

    ``mode`` is a name, or a function ``f(call_number, parsed_args) -> name`` (call numbers start at 1 and are taken under a lock). Names: ``ok``;
    ``nofile`` (exit 0, nothing written); ``exit1`` (exit code 1, picture written); ``timeout``; ``start-error``; ``overflow``; ``raise``;
    ``blank`` (a white page of the right size); ``page:<rrggbb>`` (one flat colour); ``wrongsize`` (the right drawing on a canvas 1 px too narrow);
    ``corrupt`` (bytes that are no PNG); ``damaged`` (a valid file cut short); ``interlaced`` (a PNG header with the interlace bit); ``bytes:<attribute>``
    (the picture is ``getattr(self, attribute)``, for example a real screenshot of a not-found page)."""

    def __init__(self, mode="ok", delay: float = 0.0, clock: "FakeClock | None" = None, step: float = 0.0):
        self.mode = mode
        self.delay = delay
        self.clock = clock
        self.step = step
        self.calls: list = []
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.scratch_seen: list = []

    def __call__(self, args, timeout, scratch):
        with self.lock:
            self.calls.append((list(args), timeout, Path(scratch)))
            self.scratch_seen.append(Path(scratch).is_dir())
            number = len(self.calls)
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            parsed = parse_args(args)
            mode = self.mode(number, parsed) if callable(self.mode) else self.mode
            if self.delay:
                import time
                time.sleep(self.delay(number, parsed) if callable(self.delay) else self.delay)
            if self.clock is not None:
                self.clock.advance(self.step)
            if mode == "raise":
                raise RuntimeError("the launcher broke")
            if mode == "start-error":
                return Launch(None, error="FileNotFoundError: the browser is not there")
            if mode == "timeout":
                return Launch(None, timed_out=True, seconds=timeout)
            if mode == "overflow":
                return Launch(0, overflow="stderr 99 bytes over the 1 byte cap")
            svg = parsed["page"].read_text(encoding="utf-8") if parsed["page"].is_file() else None
            shot = Path(parsed["shot"])
            if mode == "nofile":
                return Launch(0)
            scale = float(parsed.get("scale", "1"))
            if mode.startswith("bytes:"):
                data = getattr(self, mode[6:])
            elif svg is None:
                data = paint(blank_svg(), 1.0)
            elif mode in ("ok", "exit1"):
                data = paint(svg, scale)
            elif mode == "blank":
                data = blank_like(svg, scale, WHITE)
            elif mode.startswith("page:"):
                data = blank_like(svg, scale, tuple(int(mode[5 + i:7 + i], 16) for i in (0, 2, 4)))
            elif mode == "wrongsize":
                data = chop_width(paint(svg, scale))
            elif mode == "corrupt":
                data = b"this is not a picture"
            elif mode == "damaged":
                data = paint(svg, scale)[:-40]
            elif mode == "interlaced":
                data = encode_png(8, 8, [bytes(24)] * 8, 2, interlace=1)
            else:
                raise ValueError(mode)
            shot.write_bytes(data)
            return Launch(1 if mode == "exit1" else 0)
        finally:
            with self.lock:
                self.active -= 1

    @property
    def urls(self) -> list:
        return [parse_args(c[0])["url"] for c in self.calls]

    @property
    def pages(self) -> list:
        return sorted(parse_args(c[0])["page"].name for c in self.calls)


def blank_like(svg: str, scale: float, rgb: tuple) -> bytes:
    root = ET.fromstring(svg)
    w, h = max(1, round(float(root.get("width")) * scale)), max(1, round(float(root.get("height")) * scale))
    return encode_png(w, h, [bytes(rgb) * w] * h, 2)


def chop_width(data: bytes) -> bytes:
    """The same drawing with the last column removed (one pixel narrower than the SVG)."""
    rows = decode_png_naive(data)
    width = len(rows[0]) // 3
    return encode_png(width - 1, len(rows), [r[:-3] for r in rows], 2)


# ---------------------------------------------------------------------------------------------------------------
# Distributions and synthetic SVGs
# ---------------------------------------------------------------------------------------------------------------
def make_dist(root: Path, name: str = "sample12", only=None) -> Path:
    """``root/dist/screens/*.svg`` from one of the fixed builds (every state); ``only`` keeps the files whose name is in it."""
    dist = Path(root) / "dist"
    (dist / "screens").mkdir(parents=True, exist_ok=True)
    for path, data in fx.built(name).files.items():
        if path.startswith("screens/") and path.endswith(".svg") and (only is None or Path(path).name in only):
            (dist / path).write_bytes(data)
    return dist


def box(ident: str, x: int, y: int, w: int, h: int, cls: str = "b", rx: int = 4, n: int = 1, kind: str = "input", parent: str = "S.root", extra: str = "",
        mark: bool = False, screen: str = "S") -> str:
    """One widget group of a synthetic screen: a rectangle of class ``cls`` (and, with ``extra``, more elements after it)."""
    rxa = f' rx="{rx}"' if rx else ""
    out = [f'<g id="{screen}.{ident}" data-kind="{kind}" data-box="{x} {y} {w} {h}" data-n="{n}" data-parent="{parent}">',
           f'<rect x="{x}" y="{y}" width="{w}" height="{h}"{rxa} class="{cls}"/>']
    if extra:
        out.append(extra)
    if mark:
        out.append(f'<circle cx="{max(x - 10, 10)}" cy="{y + 10}" r="9" class="m"/>')
    out.append("</g>")
    return "\n".join(out)


def mini_svg(width: int, canvas_height: int, groups=(), band: int = 28, page_class: str = "p", screen: str = "S", variant: str = "default",
             model_sha8: str = "") -> str:
    """A screen SVG with the structure of ``svg.render_state`` (root, style, state band, translated content group with the page rectangle) and the
    given widget groups. Not a rendering of any model."""
    total = canvas_height + band
    extra = f' data-model-sha8="{model_sha8}"' if model_sha8 else ""
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {total}" width="{width}" height="{total}" role="group" aria-label="{screen}" '
            f'data-schema="gemarc-atlas-screen/1" data-screen="{screen}" data-variant="{variant}" data-frame="desktop" data-layout="1" data-ui-sha8="0badf00d"{extra}>')
    lines = [head, f"<title>{screen}</title>", "<desc>synthetic</desc>", "<style>", *svgmod.CSS_LINES, "</style>",
             f'<g id="{screen}.state" data-kind="state-label" data-box="0 0 {width} {band}">', f'<rect x="0" y="0" width="{width}" height="{band}" class="f"/>',
             f'<text x="8" y="18" class="s2 g">{screen}</text>', "</g>",
             f'<g id="{screen}.root" data-kind="screen" data-box="0 0 {width} {canvas_height}" transform="translate(0 {band})">',
             f'<rect x="0" y="0" width="{width}" height="{canvas_height}" class="{page_class}"/>', *groups, "</g>", "</svg>"]
    return "\n".join(lines) + "\n"


def blank_svg() -> str:
    return mini_svg(64, 36)
