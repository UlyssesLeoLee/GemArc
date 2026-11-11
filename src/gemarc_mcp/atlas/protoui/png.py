"""PNG renderings of the screens for readers that can look at pictures (design A3 4.6, 5.5, D-14; check ``SV-208``).

The SVG and the screen card are the source of a screen; a PNG is a cache beside them: it is made from the default state of each screen by a headless
browser, checked against the picture it must show, and kept under ``dist/.verify/screens/`` (the write area of ``verify``) together with ``png.json``.
It never decides whether a project is done.

``render(dist, cfg, launcher) -> PngResult``   renders what is not cached, verifies every picture, writes the files and ``png.json``.
``probe(png_bytes, probes) -> bool``           the pixel check on its own (``probes_for`` makes the probes of one SVG).

This module never starts a process. The browser is started by ``launcher(args, timeout, scratch)``, a callable the caller injects (the integration binds
it to the hardened launch of ``gate/smoke.py``: the whole process tree is killed on a timeout, the temporary directory of the browser is redirected,
the output is capped). ``args`` is the full command line, ``args[0]`` the browser; the launcher adds the flags that belong to its own hardening (the
profile directory, ``--disable-gpu``) and keeps every file it creates inside ``scratch``, an existing directory that is private to this launch and that
this module removes afterwards. The launcher returns an object with the attributes ``returncode``, ``timed_out``, ``seconds`` and ``error`` (and, when it
has them, ``overflow`` and ``read_error``): the ``_Launch`` of ``gate/smoke.py`` is one. Without a launcher or without ``cfg.browser`` nothing is rendered
and the status says so (``skipped:no-launcher``, ``skipped:no-browser``): never a pass.

How a picture is accepted (SV-208). The exit code of the browser, the size of the file and the share of non-background pixels prove nothing (the "file
not found" page of the browser is a full-size picture full of text, exit code 0, and its background, #f6f6f6 as measured with Edge 152, is within the
tolerance of the page colour #f6f7f9, so page-colour points alone would accept it). A picture is accepted only when (1) the PNG has the size of the SVG (or
the scaled size: the long side is capped at ``max_edge`` by a device scale factor, never below ``MIN_SCALE``), (2) the key of the SVG (its sha8 without
``data-model-sha8``) is the one recorded, and (3) every probe hits: probes are points the SVG itself defines, read from its own geometry and style: two
points of the state band, up to four points of the page background that no widget covers, and a point inside each of up to eight widget boxes that have
a plain fill and that nothing painted later covers. The expected colour of a probe is the fill the SVG declares, the tolerance is +-3 per channel. The
probes of a screen must also contain two colours that no single flat colour can match within the tolerance (the band and the page differ by 13), so a blank
page of any colour never passes and neither does a page that has only the page colour right.

Cache. The key of a picture is the sha8 of the SVG text without the attribute ``data-model-sha8`` (a change of the 5D model does not redraw anything). A
cached picture is used only after it passes size and probes again; the file ``png.json`` lists, for every screen, the key, the status and the size, and
never a duration or a path, so it is the same bytes for the same SVGs. Statuses of an entry: ``ok``, ``FAIL`` (the launch failed, no picture was
written, the size or a probe is wrong), ``skipped:no-launcher``, ``skipped:no-browser``, ``skipped:budget`` (the wall-clock budget was spent),
``skipped:probe`` (no sound probe set can be made from the SVG, or the PNG is a kind the reader does not decode), ``skipped:link`` (a link or junction
is not followed). The status of a whole run is ``FAIL`` when any entry failed, else the first skip, else ``ok``; ``absent`` when there is nothing to
render and ``skipped:none`` when ``cfg.mode`` is ``none``.

Concurrency: ``cfg.jobs`` launches at a time (4), each with its own scratch directory; the entries are sorted by file name, so the result does not depend
on the order in which the launches finish. Nothing here depends on a hash order, a clock (but the budget) or a random number.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence

from . import pngread
from . import vocab
from .build import Finding

SCHEMA = "gemarc-atlas-png/1"
INDEX_FILE = "png.json"
SCREENS_DIR = ".verify/screens"
WORK_DIR = ".verify/work"
SCRATCH_PREFIX = "atlas-png-"
SVG_NS = "{http://www.w3.org/2000/svg}"

OK, FAIL, ABSENT = "ok", "FAIL", "absent"
SKIP_NONE, SKIP_NO_LAUNCHER, SKIP_NO_BROWSER = "skipped:none", "skipped:no-launcher", "skipped:no-browser"
SKIP_BUDGET, SKIP_PROBE, SKIP_LINK = "skipped:budget", "skipped:probe", "skipped:link"
SKIP_ORDER = (SKIP_NO_LAUNCHER, SKIP_NO_BROWSER, SKIP_BUDGET, SKIP_PROBE, SKIP_LINK)

BACKGROUND = (0xF6, 0xF7, 0xF9)   # the page background of every screen (class ``p`` of svg.py); the probe reads it from the SVG, tests pin it here
TOLERANCE = 3                    # per channel
MAX_EDGE = 1600                  # longest side of a picture in pixels; a larger screen is rendered with a device scale factor below 1
MIN_SCALE = 0.5                  # but never below this: a picture at a smaller scale cannot be read, and the margins of the probes stop being safe
MAX_BACKGROUND_PROBES = 4
MAX_BOX_PROBES = 8
MARGIN = 2                       # a probe point keeps this many pixels away from every other painted shape (strokes and anti-aliasing)
MIN_PROBES = 4
_MARK_ATTR = re.compile(rb' data-model-sha8="[0-9a-f]{8}"')
_TRANSLATE = re.compile(r"translate\(\s*(-?\d+)\s+(-?\d+)\s*\)")
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_FILL = re.compile(r"(?:^|;)\s*fill\s*:\s*([^;}]+)")
_HEX = re.compile(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})\Z")


@dataclass(frozen=True)
class PngConfig:
    """``mode``: ``default`` (the default state of each screen), ``all`` (every state) or ``none``. ``browser``: the path of the browser, the first
    word of the command line. ``jobs``: launches at a time. ``timeout``: seconds of one launch. ``budget_seconds``: wall-clock budget of the whole call
    (the caller passes what is left of the budget of its own operation); a picture that cannot start with ``min_launch_seconds`` left is skipped.
    ``retries``: further tries of a launch that failed for a reason other than a timeout or a browser that cannot start. ``scratch_root``: where the
    private directories of the launches are made (default ``dist/.verify/work``). ``refresh``: ignore the cache. ``label``: text for ``png.json`` that
    names the renderer (for example ``edge 152.0.4191.53``). ``clock``: the time source (a test injects one)."""

    mode: str = "default"
    browser: "str | None" = None
    jobs: int = 4
    timeout: float = 20.0
    budget_seconds: float = 30.0
    min_launch_seconds: float = 3.0
    retries: int = 1
    max_edge: int = MAX_EDGE
    scratch_root: "Path | None" = None
    refresh: bool = False
    label: str = ""
    clock: Callable = time.monotonic


@dataclass(frozen=True)
class Probe:
    """One pixel to read: ``kind`` is ``band``, ``background`` or ``box``; ``ref`` names the widget of a box; ``rgb`` is the colour the SVG declares."""

    x: int
    y: int
    rgb: tuple
    kind: str
    ref: str = ""


@dataclass(frozen=True)
class ProbeSet:
    probes: tuple
    width: int          # size of the SVG in px
    height: int
    scale: float
    problem: str = ""   # non-empty: no sound probe set can be made (the reason is a stable text)

    @property
    def size(self) -> tuple:
        return expected_size(self.width, self.height, self.scale)


@dataclass
class ProbeReport:
    ok: bool
    probes: int = 0
    hits: int = 0
    misses: list = field(default_factory=list)   # (Probe, (r, g, b, a)) of the first misses
    error: str = ""
    unsupported: bool = False


@dataclass
class PngEntry:
    svg: str                  # path of the SVG relative to dist
    screen: str = ""
    variant: str = ""
    key: str = ""             # sha8 of the SVG without data-model-sha8 (png.json: svgSha8)
    ui_sha8: str = ""
    status: str = ""
    reason: str = ""          # stable text, no number that changes from run to run, no path
    file: str = ""            # name inside .verify/screens, only when ok
    width: int = 0
    height: int = 0
    probes: int = 0
    scale: float = 1.0        # device scale factor of the picture
    cached: bool = False
    attempts: int = 0         # runtime only
    seconds: float = 0.0      # runtime only
    detail: str = ""          # runtime only: durations and codes of the launch

    def as_json(self) -> dict:
        out = {"svg": self.svg, "screen": self.screen, "variant": self.variant, "svgSha8": self.key, "uiSha8": self.ui_sha8, "status": self.status}
        if self.status == OK:
            out.update({"file": self.file, "width": self.width, "height": self.height, "scale": self.scale, "probes": self.probes})
        elif self.reason:
            out["reason"] = self.reason
        return out


@dataclass
class PngResult:
    status: str
    entries: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    message: str = ""
    seconds: float = 0.0
    index: bytes = b""          # the text of png.json as written ("" when nothing was written)
    leftovers: list = field(default_factory=list)   # scratch directories that could not be removed (names only)

    @property
    def rendered(self) -> int:
        return sum(1 for e in self.entries if e.status == OK and not e.cached)

    @property
    def cached(self) -> int:
        return sum(1 for e in self.entries if e.status == OK and e.cached)

    @property
    def failed(self) -> list:
        return [e for e in self.entries if e.status == FAIL]

    def summary(self) -> dict:
        """The small object a reply carries (``verify``): the status, the counts, at most five failures and one line that says what to do."""
        out: dict = {"png": self.status, "screens": len(self.entries), "rendered": self.rendered, "cached": self.cached}
        failed = self.failed
        if failed:
            out["failed"] = [{"screen": e.screen, "variant": e.variant, "reason": e.reason} for e in failed[:5]]
        skipped = [e for e in self.entries if e.status.startswith("skipped:")]
        if skipped:
            out["skipped"] = len(skipped)
        if self.status != OK and self.message:
            out["next"] = self.message
        return out


# --------------------------------------------------------------------------------------------------
# Keys, sizes
# --------------------------------------------------------------------------------------------------
def svg_key(svg: "bytes | str") -> str:
    """sha8 of an SVG without its ``data-model-sha8`` attribute (the cache key and the ``svgSha8`` of ``png.json``): the attribute only names the 5D model
    a link was checked against, it draws nothing."""
    data = svg.encode("utf-8") if isinstance(svg, str) else bytes(svg)
    head, sep, tail = data.partition(b">")        # the attribute is in the start tag of the root element
    return hashlib.sha256(_MARK_ATTR.sub(b"", head, count=1) + sep + tail).hexdigest()[:8]


def scale_for(width: int, height: int, max_edge: int = MAX_EDGE) -> float:
    """Device scale factor of a rendering: 1 up to ``max_edge`` px on the long side, below 1 (three decimals, rounded down) above, never below ``MIN_SCALE``
    (a very tall screen then gives a picture longer than ``max_edge``)."""
    long = max(int(width), int(height))
    return 1.0 if long <= max_edge else max(MIN_SCALE, math.floor(max_edge / long * 1000) / 1000)


def expected_size(width: int, height: int, scale: float = 1.0) -> tuple:
    return (max(1, round(width * scale)), max(1, round(height * scale)))


def screenshot_args(browser: str, svg: Path, shot: Path, width: int, height: int, scale: float) -> list:
    """The command line of one rendering: the screenshot of the SVG file opened by its ``file://`` URL (``Path.resolve().as_uri()``: percent-encoded, no
    MSYS paths), in a window of the size of the SVG, with the device scale factor pinned (so the size of the picture does not depend on the display
    scaling of the machine). The launcher adds the profile directory and ``--disable-gpu``."""
    factor = f"{scale:.3f}".rstrip("0").rstrip(".")
    return [browser, "--headless=new", "--hide-scrollbars", f"--window-size={int(width)},{int(height)}", f"--force-device-scale-factor={factor}",
            f"--screenshot={shot}", Path(svg).resolve().as_uri()]


# --------------------------------------------------------------------------------------------------
# The probes
# --------------------------------------------------------------------------------------------------
def _rgb(text: str) -> "tuple | None":
    m = _HEX.match(text.strip())
    if not m:
        return None
    h = m.group(1)
    h = "".join(c * 2 for c in h) if len(h) == 3 else h
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _fills(css: str) -> dict:
    """``{class: (order, rgb or None)}`` of the class rules that declare a ``fill``; a later rule wins (same specificity), ``fill:none`` is ``None``."""
    out: dict = {}
    order = 0
    for selector, body in _RULE.findall(css):
        m = _FILL.search(body)
        if not m:
            continue
        for sel in selector.split(","):
            sel = sel.strip()
            if re.fullmatch(r"\.[A-Za-z0-9_-]+", sel):
                order += 1
                out[sel[1:]] = (order, _rgb(m.group(1)))
    return out


def _fill_of(classes: str, fills: Mapping) -> "tuple | None":
    best = None
    for name in classes.split():
        if name in fills and (best is None or fills[name][0] > best[0]):
            best = fills[name]
    return best[1] if best else None


def _parse_svg(svg: "bytes | str"):
    """The root element of an SVG text. A document type declaration or an entity is refused (``ET.ParseError``): the files are generated, and their text
    is escaped, so a raw one is not ours and an entity could be made to expand."""
    data = svg.encode("utf-8") if isinstance(svg, str) else bytes(svg)
    low = data.lower()
    if b"<!doctype" in low or b"<!entity" in low:
        raise ET.ParseError("a document type declaration is not accepted")
    return ET.fromstring(data)


def _int(value) -> int:
    number = float(value)
    if number != int(number):
        raise ValueError(value)
    return int(number)


def _box4(text: "str | None") -> tuple:
    parts = tuple(_int(p) for p in (text or "").split())
    if len(parts) != 4:
        raise ValueError(text)
    return parts


def _covers(shapes: Sequence, after: int, x: int, y: int) -> bool:
    """Whether a shape painted after index ``after`` can touch the pixel ``(x, y)`` (every shape counts by its bounding box, widened by ``MARGIN``)."""
    for bx, by, bw, bh in shapes[after + 1:]:
        if bx - MARGIN <= x < bx + bw + MARGIN and by - MARGIN <= y < by + bh + MARGIN:
            return True
    return False


def _discriminates(probes: Sequence) -> bool:
    """True when two probes expect colours that differ by more than twice the tolerance in some channel: no single colour can then match all of them."""
    colours = {p.rgb for p in probes}
    return any(max(abs(a[i] - b[i]) for i in range(3)) > 2 * TOLERANCE for a in colours for b in colours)


def probes_for(svg: "bytes | str", scale: float = 1.0) -> ProbeSet:
    """The probes of the picture of one screen SVG at device scale ``scale`` (pixel coordinates of the picture). A ``problem`` text (and no probes) when
    the SVG cannot be read, has no state band or page background, or gives fewer than ``MIN_PROBES`` points or a set that a flat colour could pass."""
    def fail(text: str, w: int = 0, h: int = 0) -> ProbeSet:
        return ProbeSet((), w, h, scale, text)

    try:
        root = _parse_svg(svg)
        if root.tag != SVG_NS + "svg":
            return fail("svg: the root element is not svg")
        vb = [_int(p) for p in (root.get("viewBox") or "").split()]
        width, height = vb[2], vb[3]
        if vb[:2] != [0, 0] or _int(root.get("width")) != width or _int(root.get("height")) != height:
            return fail("svg: viewBox, width and height disagree")
    except (ET.ParseError, ValueError, TypeError, IndexError):
        return fail("svg: not readable")
    try:
        fills = _fills("\n".join(el.text or "" for el in root.iter(SVG_NS + "style")))
        band = content = None
        for g in root.findall(SVG_NS + "g"):
            if g.get("data-kind") == "state-label":
                band = g
            elif g.get("data-kind") == "screen":
                content = g
        if band is None or content is None:
            return fail("svg: no state band or no content group", width, height)
        band_h = _box4(band.get("data-box"))[3]
        band_rect = band.find(SVG_NS + "rect")
        band_rgb = _fill_of(band_rect.get("class", ""), fills) if band_rect is not None else None
        m = _TRANSLATE.fullmatch((content.get("transform") or "").strip())
        dy = int(m.group(2)) if m else 0
        page = content.find(SVG_NS + "rect")
        page_rgb = _fill_of(page.get("class", ""), fills) if page is not None else None
        if band_rgb is None or page_rgb is None or band_rect is None:
            return fail("svg: the band or the page has no plain fill", width, height)
        canvas_h = _int(page.get("height"))
        # Everything painted inside the content, in order: the bounding boxes of its rectangles, circles and paths (a path counts as the box of its widget).
        shapes: list = []
        candidates: list = []     # (index in shapes of the widget rectangle, widget id, box, rgb, rx, leaf)
        parents = {g.get("data-parent") for g in content.findall(SVG_NS + "g")}
        for g in content.findall(SVG_NS + "g"):
            box = _box4(g.get("data-box"))
            rects = g.findall(SVG_NS + "rect")
            paths = g.findall(SVG_NS + "path")
            first = None
            for el in list(g):
                tag = el.tag[len(SVG_NS):] if el.tag.startswith(SVG_NS) else el.tag
                if tag == "rect":
                    rect = (_int(el.get("x")), _int(el.get("y")), _int(el.get("width")), _int(el.get("height")))
                    shapes.append(rect)
                    if first is None:
                        first = (len(shapes) - 1, el, rect)
                elif tag == "circle":
                    cx, cy, r = _int(el.get("cx")), _int(el.get("cy")), _int(el.get("r"))
                    shapes.append((cx - r, cy - r, 2 * r, 2 * r))
                elif tag == "path":
                    shapes.append(box)
            plain = len(rects) == 1 and not paths and g.get("data-n") is not None and first is not None
            rgb = _fill_of(first[1].get("class", ""), fills) if plain else None
            if plain and rgb is not None and first[1].get("data-derived") is None:
                candidates.append((first[0], g.get("id", ""), first[2], rgb, _int(first[1].get("rx", "0")), g.get("id") not in parents))
    except (ValueError, TypeError, AttributeError):
        return fail("svg: geometry not understood", width, height)

    def pixel(x: int, y: int) -> tuple:
        return (min(int((x + 0.5) * scale), max(1, round(width * scale)) - 1), min(int((y + 0.5) * scale), max(1, round(height * scale)) - 1))

    if expected_size(width, height, scale)[0] * expected_size(width, height, scale)[1] > pngread.MAX_PIXELS:
        return fail("size: the picture would have more pixels than the probe reader accepts", width, height)
    inset = math.ceil(3 / scale)
    probes: list = []
    for x in (3, width - 4):       # the band: left of the first letter, right of the last, below the lowest line (the descenders end 6 px above the bottom)
        px, py = pixel(x, band_h - 4)
        probes.append(Probe(px, py, band_rgb, "band", "state"))
    # The page background: the four corners, then the middles of the sides, the centre and two more points, keeping those no widget can touch.
    grid = [(inset, inset), (width - 1 - inset, inset), (inset, canvas_h - 1 - inset), (width - 1 - inset, canvas_h - 1 - inset),
            (width // 2, inset), (width // 2, canvas_h - 1 - inset), (inset, canvas_h // 2), (width - 1 - inset, canvas_h // 2),
            (width // 2, canvas_h // 2), (width // 4, canvas_h // 4), (3 * width // 4, 3 * canvas_h // 4)]
    background = [(x, y) for x, y in grid if 0 <= x < width and 0 <= y < canvas_h and not _covers(shapes, -1, x, y)]
    for x, y in background[:MAX_BACKGROUND_PROBES]:
        px, py = pixel(x, y + dy)
        probes.append(Probe(px, py, page_rgb, "background", "page"))
    # Widget boxes: leaves first, then containers; the corner of the box, inset from the edge and from the rounded corner.
    usable = []
    for index, ident, (x, y, w, h), rgb, rx, leaf in candidates:
        gap = max(3, math.ceil(rx * 0.3) + 2, inset)
        if w >= 2 * gap + 2 and h >= 2 * gap + 2 and not _covers(shapes, index, x + gap, y + gap):
            usable.append((not leaf, ident, x + gap, y + gap, rgb))
    usable.sort(key=lambda u: (u[0], u[3], u[2], u[1]))
    pick = usable if len(usable) <= MAX_BOX_PROBES else [usable[i * len(usable) // MAX_BOX_PROBES] for i in range(MAX_BOX_PROBES)]
    for _, ident, x, y, rgb in pick:
        px, py = pixel(x, y + dy)
        probes.append(Probe(px, py, rgb, "box", ident))
    probes.sort(key=lambda p: (p.y, p.x, p.kind, p.ref))
    if len(probes) < MIN_PROBES or not _discriminates(probes):
        return fail("probe: the SVG gives too few distinct probe points", width, height)
    return ProbeSet(tuple(probes), width, height, scale)


def _close(got: tuple, want: tuple) -> bool:
    return got[3] == 255 and all(abs(got[i] - want[i]) <= TOLERANCE for i in range(3))


def probe_report(png: bytes, probes: Sequence, size: "tuple | None" = None) -> ProbeReport:
    """Read every probe of ``probes`` from the PNG. ``size``, when given, is the size the picture must have. A file that is not a readable PNG is a
    report with ``ok`` false and ``error`` set (``unsupported`` when it is a valid kind of PNG this reader does not decode); it never raises."""
    probes = tuple(probes)
    if not probes or not _discriminates(probes):
        return ProbeReport(False, len(probes), 0, error="probe: the probe set proves nothing")
    try:
        image = pngread.Image(png)
        if size is not None and (image.width, image.height) != tuple(size):
            return ProbeReport(False, len(probes), 0, error=f"size: expected {size[0]}x{size[1]}, got {image.width}x{image.height}")
        misses = []
        hits = 0
        for p in probes:
            if not (0 <= p.x < image.width and 0 <= p.y < image.height):
                misses.append((p, ()))
                continue
            got = image.pixel(p.x, p.y)
            if _close(got, p.rgb):
                hits += 1
            else:
                misses.append((p, got))
        return ProbeReport(not misses, len(probes), hits, misses[:5])
    except pngread.PngError as exc:
        return ProbeReport(False, len(probes), 0, error=f"png: {exc}", unsupported=exc.unsupported)


def probe(png_bytes: bytes, probes: Sequence) -> bool:
    """True only when ``probes`` is a set that proves something (two colours no flat colour can match) and every probe hits within the tolerance of
    +-3 per channel. A damaged or unreadable PNG is False."""
    return probe_report(png_bytes, probes).ok


# --------------------------------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------------------------------
_REPARSE_POINT = 0x400


def _is_link(path: Path) -> bool:
    """A symbolic link, a Windows junction or any other reparse point (``Path.is_symlink`` misses a junction): never followed."""
    try:
        if path.is_symlink():
            return True
        junction = getattr(os.path, "isjunction", None)
        if junction is not None and junction(path):
            return True
        return bool(getattr(os.lstat(path), "st_file_attributes", 0) & _REPARSE_POINT)
    except OSError:
        return False


def _remove_tree(path: Path, attempts: int = 25, delay: float = 0.2) -> bool:
    """Remove a directory; Windows holds the handles of a browser profile for a moment after the process ended. True when it is gone."""
    for _ in range(attempts):
        if not path.exists():
            return True
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return True
        time.sleep(delay)
    return not path.exists()


@dataclass
class _Svg:
    path: str
    file: Path
    text: bytes
    key: str
    screen: str = ""
    variant: str = ""
    ui_sha8: str = ""
    width: int = 0
    height: int = 0
    problem: str = ""


def _read_svgs(dist: Path, mode: str) -> list:
    out = []
    folder = dist / "screens"
    for file in sorted(folder.iterdir(), key=lambda p: p.name):
        if file.suffix != ".svg" or (not file.is_file() and not _is_link(file)):
            continue
        item = _Svg(f"screens/{file.name}", file, b"", "")
        if _is_link(file):
            item.problem = "link: not followed"
            out.append(item)
            continue
        try:
            item.text = file.read_bytes()
        except OSError:
            item.problem = "svg: not readable"
            out.append(item)
            continue
        item.key = svg_key(item.text)
        try:
            root = _parse_svg(item.text)
            item.screen, item.variant, item.ui_sha8 = root.get("data-screen", ""), root.get("data-variant", ""), root.get("data-ui-sha8", "")
            vb = [_int(p) for p in (root.get("viewBox") or "").split()]
            item.width, item.height = vb[2], vb[3]
        except (ET.ParseError, ValueError, IndexError):
            item.problem = "svg: not readable"
            out.append(item)
            continue
        if mode == "all" or item.variant == vocab.DEFAULT_VARIANT:
            out.append(item)
    return out


def read_index(dist: "Path | str") -> "dict | None":
    """``png.json`` of a dist as a dict, ``None`` when it is missing, unreadable or of another schema."""
    try:
        data = json.loads((Path(dist) / SCREENS_DIR / INDEX_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("schema") == SCHEMA and isinstance(data.get("entries"), list) else None


def state_of(index: "Mapping | None", svg_path: str, svg: "bytes | str", dist: "Path | str | None" = None) -> str:
    """What the reading side says about the picture of one SVG (design 2.9, item 6): ``ok``, ``stale`` (the SVG is not the one that was drawn),
    ``absent`` (never rendered, no ``png.json``, or, when ``dist`` is given, the picture file of an ``ok`` entry is gone), ``FAIL`` or one of the
    ``skipped:*`` statuses of the last render."""
    if not index:
        return ABSENT
    for entry in index.get("entries", ()):
        if isinstance(entry, dict) and entry.get("svg") == svg_path:
            if entry.get("svgSha8") != svg_key(svg):
                return "stale"
            status = str(entry.get("status") or ABSENT)
            if status == OK and dist is not None and not (Path(dist) / SCREENS_DIR / str(entry.get("file"))).is_file():
                return ABSENT
            return status
    return ABSENT


def _index_bytes(entries: Sequence, status: str, cfg: PngConfig) -> bytes:
    counts: dict = {}
    for e in entries:
        counts[e.status] = counts.get(e.status, 0) + 1
    doc = {"schema": SCHEMA, "status": status, "mode": cfg.mode, "counts": {k: counts[k] for k in sorted(counts)}, "entries": [e.as_json() for e in entries]}
    if cfg.label:
        doc["renderer"] = cfg.label
    return (json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _put_in_place(source: Path, target: Path) -> None:
    """Move a finished picture to its final name: one atomic rename, or (when the scratch directory is on another volume) a copy beside the target and
    then the rename, so a reader never sees half a file."""
    try:
        os.replace(source, target)
    except OSError:
        copy = target.with_name(target.name + ".tmp")
        shutil.copyfile(source, copy)
        os.replace(copy, target)


# --------------------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------------------
def _launch_problem(outcome) -> "tuple | None":
    """``(stable reason, detail, retryable)`` when the launch itself failed, else ``None``."""
    seconds = getattr(outcome, "seconds", 0.0) or 0.0
    if getattr(outcome, "error", None):
        return ("launch: the browser could not be started", "could not start", False)
    if getattr(outcome, "timed_out", False):
        return ("launch: timeout (process tree killed)", f"timeout after {seconds:.1f} s", False)
    if getattr(outcome, "overflow", None):
        return ("launch: browser output cut at its cap", "output over the cap", True)
    if getattr(outcome, "read_error", None):
        return ("launch: browser output could not be read", "pipe read error", True)
    code = getattr(outcome, "returncode", None)
    if code != 0:
        return (f"launch: browser exit code {code}", f"exit code {code}", True)
    return None


def _entry_of(item: _Svg, status: str, reason: str = "") -> PngEntry:
    return PngEntry(item.path, item.screen, item.variant, item.key, item.ui_sha8, status, reason, width=item.width, height=item.height)


def _verify(data: bytes, probes: ProbeSet) -> ProbeReport:
    return probe_report(data, probes.probes, probes.size)


def _cached(entry_json: "Mapping | None", item: _Svg, probes: ProbeSet, out_dir: Path) -> "PngEntry | None":
    """The entry of a picture that is still good: recorded as ``ok`` for this key, the file is there, and it passes size and probes again."""
    if not entry_json or entry_json.get("status") != OK or entry_json.get("svgSha8") != item.key or not entry_json.get("file"):
        return None
    name = str(entry_json["file"])
    if name != item.file.stem + ".png":
        return None
    path = out_dir / name
    try:
        if _is_link(path) or not path.is_file():
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if not _verify(data, probes).ok:
        return None
    entry = _entry_of(item, OK)
    entry.file, entry.cached = name, True
    entry.width, entry.height, entry.scale = (*probes.size, probes.scale)
    entry.probes = len(probes.probes)
    return entry


def _job(item: _Svg, probes: ProbeSet, number: int, cfg: PngConfig, launcher: Callable, root: Path, out_dir: Path, deadline: float, leftovers: list) -> PngEntry:
    """One picture: launch, wait, verify, move into place, remove the private directory. Never raises; the entry says what happened."""
    entry = _entry_of(item, FAIL)
    clock = cfg.clock
    job_dir = root / f"job-{number:04d}"
    try:
        job_dir.mkdir()
        shot = job_dir / "shot.png"
        args = screenshot_args(str(cfg.browser), item.file, shot, item.width, item.height, probes.scale)
        for attempt in range(max(0, cfg.retries) + 1):
            left = deadline - clock()
            if left < cfg.min_launch_seconds:
                if attempt == 0:
                    return _entry_of(item, SKIP_BUDGET, "budget: the wall-clock budget was spent before this picture started")
                break
            entry.attempts = attempt + 1
            shot.unlink(missing_ok=True)       # a picture left by an earlier try is never taken for the result of this one
            started = clock()
            try:
                outcome = launcher(list(args), min(cfg.timeout, left), job_dir)
            except Exception as exc:           # a launcher that raises is a failed launch, not a crash of the run
                entry.reason, entry.detail = f"launch: the launcher failed ({type(exc).__name__})", type(exc).__name__
                break
            entry.seconds = clock() - started
            problem = _launch_problem(outcome)
            if problem:
                entry.reason, entry.detail = problem[0], problem[1]
                if problem[2]:
                    continue
                break
            try:
                data = shot.read_bytes() if shot.is_file() and not _is_link(shot) else b""
            except OSError:
                data = b""
            if not data:
                entry.reason, entry.detail = "launch: the browser wrote no picture", "no file"
                continue
            report = _verify(data, probes)
            if report.ok:
                final = out_dir / (item.file.stem + ".png")
                try:
                    _put_in_place(shot, final)
                except OSError:
                    entry.reason, entry.detail = "write: the picture could not be put in place", "replace failed"
                    break
                entry.status, entry.reason, entry.file, entry.detail = OK, "", final.name, ""
                entry.width, entry.height, entry.scale = (*probes.size, probes.scale)
                entry.probes = report.probes
                return entry
            if report.unsupported:
                return _entry_of(item, SKIP_PROBE, "probe: the picture is of a kind the reader does not decode")
            if report.error:
                entry.reason = entry.detail = report.error
                continue
            first = report.misses[0][0] if report.misses else None
            entry.reason = f"probe: {len(report.misses)} of {report.probes} probes missed" + (f" (first: {first.kind} {first.ref})" if first and first.ref else "")
            entry.detail = entry.reason
        return entry
    except Exception as exc:                   # fail closed: an unexpected error is a failed picture, never a pass
        entry.status, entry.reason, entry.detail = FAIL, f"runner: unexpected error ({type(exc).__name__})", type(exc).__name__
        return entry
    finally:                                   # inside the worker, so that the removal of one profile overlaps the launches of the others
        if not _remove_tree(job_dir):
            leftovers.append(job_dir.name)


def _status_of(entries: Sequence) -> str:
    if not entries:
        return ABSENT
    if any(e.status == FAIL for e in entries):
        return FAIL
    for skip in SKIP_ORDER:
        if any(e.status == skip for e in entries):
            return skip
    return OK


_MESSAGES = {
    FAIL: "PNG check failed for at least one screen; the SVG and the screen card are unaffected and remain the source; run verify again, or set [ui] png to none",
    SKIP_NO_LAUNCHER: "PNG SKIPPED, NOT PASSED: no browser launcher was given, so no picture was rendered; the SVG and the screen card are unaffected",
    SKIP_NO_BROWSER: "PNG SKIPPED, NOT PASSED: no Edge or Chrome found; install one or set GEMARC_ATLAS_BROWSER; the SVG and the screen card are unaffected",
    SKIP_BUDGET: "PNG SKIPPED, NOT PASSED: the wall-clock budget was spent before every picture was rendered; run verify again when the machine is idle",
    SKIP_PROBE: "PNG SKIPPED, NOT PASSED: no sound pixel probe could be made for at least one screen or its picture is not decodable; the SVG and the card are unaffected",
    SKIP_LINK: "PNG SKIPPED, NOT PASSED: a link or junction in the output tree was not followed",
    SKIP_NONE: "PNG rendering is switched off ([ui] png = none)",
    ABSENT: "no screen SVG to render",
}


def _result(entries: list, cfg: PngConfig, begun: float, index: bytes = b"", leftovers: "list | None" = None, status: "str | None" = None) -> PngResult:
    status = status or _status_of(entries)
    findings = []
    for e in entries:
        if e.status == FAIL:
            findings.append(Finding("SV-208", "I", "ERROR", e.svg, f"screen {e.screen} ({e.variant}): the picture failed its check: {e.reason}"))
        elif e.status.startswith("skipped:"):
            findings.append(Finding("SV-208", "I", "INFO", e.svg, f"screen {e.screen} ({e.variant}): no verified picture ({e.status}){': ' + e.reason if e.reason else ''}"))
    return PngResult(status, entries, findings, "" if status == OK else _MESSAGES.get(status, ""), cfg.clock() - begun, index, list(leftovers or ()))


def render(dist: "Path | str", cfg: "PngConfig | None" = None, launcher: "Callable | None" = None) -> PngResult:
    """Render the pictures of ``dist/screens/*.svg`` that are not cached, verify them and write ``dist/.verify/screens/`` (the module doc has the whole
    contract). Never raises for a problem of the pictures or of the browser; it writes only inside ``dist/.verify``."""
    cfg = cfg or PngConfig()
    begun = cfg.clock()
    dist = Path(dist)
    if cfg.mode == "none":
        return _result([], cfg, begun, status=SKIP_NONE)
    if cfg.mode not in ("default", "all"):
        raise ValueError(f"png mode must be default, all or none, not {cfg.mode!r}")
    screens = dist / "screens"
    if not screens.is_dir():
        return _result([], cfg, begun)
    verify, out_dir = dist / ".verify", dist / SCREENS_DIR
    work = Path(cfg.scratch_root) if cfg.scratch_root is not None else dist / WORK_DIR
    if any(_is_link(p) for p in (dist, screens, verify, out_dir, dist / WORK_DIR, work)):
        return _result([], cfg, begun, status=SKIP_LINK)
    svgs = _read_svgs(dist, cfg.mode)
    if not svgs:
        return _result([], cfg, begun)
    out_dir.mkdir(parents=True, exist_ok=True)
    previous = {e.get("svg"): e for e in (read_index(dist) or {}).get("entries", ()) if isinstance(e, dict)}
    entries: dict = {}
    pending: list = []
    for item in svgs:
        if item.problem:
            entries[item.path] = _entry_of(item, SKIP_LINK if item.problem.startswith("link") else SKIP_PROBE, item.problem)
            continue
        probes = probes_for(item.text, scale_for(item.width, item.height, cfg.max_edge))
        if probes.problem:
            entries[item.path] = _entry_of(item, SKIP_PROBE, probes.problem)
            continue
        hit = None if cfg.refresh else _cached(previous.get(item.path), item, probes, out_dir)
        if hit is not None:
            entries[item.path] = hit
        else:
            pending.append((item, probes))
    leftovers: list = []
    if pending and launcher is None:
        entries.update({item.path: _entry_of(item, SKIP_NO_LAUNCHER, "launcher: none was given") for item, _ in pending})
    elif pending and not cfg.browser:
        entries.update({item.path: _entry_of(item, SKIP_NO_BROWSER, "browser: none was given") for item, _ in pending})
    elif pending:
        work.mkdir(parents=True, exist_ok=True)
        _drop_old_scratch(work)
        root = Path(tempfile.mkdtemp(prefix=SCRATCH_PREFIX, dir=work))
        deadline = begun + float(cfg.budget_seconds)
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, int(cfg.jobs))) as pool:
                futures = [pool.submit(_job, item, probes, number, cfg, launcher, root, out_dir, deadline, leftovers) for number, (item, probes) in enumerate(pending)]
                for (item, _), future in zip(pending, futures):
                    entries[item.path] = future.result()
        finally:
            if not _remove_tree(root):
                leftovers.append(root.name)
    ordered = [entries[path] for path in sorted(entries)]
    keep = {e.file for e in ordered if e.status == OK}
    for file in out_dir.iterdir():       # a picture that is not in the index (an older screen, a failed redraw) is not kept
        if file.name != INDEX_FILE and file.name not in keep and file.suffix in (".png", ".tmp") and (file.is_file() or _is_link(file)):
            try:
                file.unlink()
            except OSError:
                pass
    status = _status_of(ordered)
    index = _index_bytes(ordered, status, cfg)
    _write_atomic(out_dir / INDEX_FILE, index)
    return _result(ordered, cfg, begun, index, leftovers, status)


def _drop_old_scratch(work: Path, older_than: float = 600.0) -> None:
    """Remove scratch directories of earlier runs that were killed before they could clean up (only our own prefix, only when old)."""
    now = time.time()
    try:
        names = [p for p in work.iterdir() if p.name.startswith(SCRATCH_PREFIX) and p.is_dir() and not _is_link(p)]
    except OSError:
        return
    for p in names:
        try:
            if now - p.stat().st_mtime > older_than:
                _remove_tree(p, attempts=3)
        except OSError:
            pass


def check_files(dist: "Path | str") -> list:
    """``SV-208`` over the pictures that exist, with no browser: every ``ok`` entry of ``png.json`` has its file, the file has the size of the SVG (scaled),
    the recorded key is the key of the SVG as it is now (a mismatch is a ``stale`` warning, not an error), and every probe of the SVG hits."""
    dist = Path(dist)
    index = read_index(dist)
    if index is None:
        return []
    out, folder = [], dist / SCREENS_DIR
    for entry in index["entries"]:
        if not isinstance(entry, dict) or entry.get("status") != OK:
            continue
        svg_path = str(entry.get("svg"))
        file = dist / svg_path
        png = folder / str(entry.get("file"))
        try:
            text, data = file.read_bytes(), png.read_bytes()
        except OSError:
            out.append(Finding("SV-208", "I", "ERROR", svg_path, "the SVG or the picture of an ok entry is missing"))
            continue
        if entry.get("svgSha8") != svg_key(text):
            out.append(Finding("SV-208", "I", "WARN", svg_path, "the picture is stale: the SVG changed after it was rendered"))
            continue
        try:
            pngread.size_of(data)
        except pngread.PngError as exc:
            out.append(Finding("SV-208", "I", "ERROR", svg_path, f"the picture is not a PNG: {exc}"))
            continue
        scale = entry.get("scale")
        probes = probes_for(text, float(scale) if isinstance(scale, (int, float)) and 0 < scale <= 1 else 1.0)
        if probes.problem:
            out.append(Finding("SV-208", "I", "INFO", svg_path, probes.problem))
            continue
        report = _verify(data, probes)
        if not report.ok and not report.unsupported:
            out.append(Finding("SV-208", "I", "ERROR", svg_path, report.error or f"{len(report.misses)} of {report.probes} probes missed"))
    return out
