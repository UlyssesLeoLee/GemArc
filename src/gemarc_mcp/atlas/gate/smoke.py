"""Browser gate runner for GemArc Atlas pages (Edge or Chrome, headless, no CDP, no Node).

One launch loads ``file:///.../page.html?gate=1`` in a headless Chromium-family browser with software
WebGL, waits for the page's timer-driven self-test (gate/selftest.js) and reads the result from the
dumped DOM (``--dump-dom``). The page only measures; every verdict is computed here from raw numbers.

Fail-closed rules (design section 9.3):
  * no browser found, or an explicitly pinned browser that is not a file -> SKIP (loud), never PASS
  * no / garbled / unfinished result element, wrong schema, wrong per-launch nonce -> FAIL
  * reported innerWidth != target width                    -> FAIL (calibration drift)
  * process-level failures (crash, timeout, truncated output, non-zero exit, browser cannot be
    started) are the only thing retried (a start failure is not); an assertion FAIL is never retried
  * console errors: in-page capture (kept live after the walker finished) plus page-origin
    ``CONSOLE`` lines on stderr (see README for why the stderr channel matches on message text)
  * canvases are judged from what the self-test read itself (pixels, getError, renderer, draw-call
    count), never from numbers the page reports about itself

Trust boundary: the page controls its own DOM and JavaScript, so a page that is written to deceive
the gate can. The checks stop accidents and laziness (stale or hard-coded result, no-op render, a
blank canvas, a self-reported ratio), not an adversary. README states the details.

Standard library only.
"""
from __future__ import annotations

import argparse
import html.parser
import json
import os
import pathlib
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

GATE_DIR = pathlib.Path(__file__).resolve().parent
CALIBRATION_PATH = GATE_DIR / "calibration.json"
SELFTEST_PATH = GATE_DIR / "selftest.js"
SELFTEST_MARKER = "<!--ATLAS_SELFTEST-->"
RESULT_ID = "atlas-gate"
RESULT_SCHEMA = 2
NONCE_PARAM = "gn"
BROWSER_ENV = "GEMARC_ATLAS_BROWSER"

CODES = {
    "NO_BROWSER": "GEMARC_GATE_001",
    "PROCESS": "GEMARC_GATE_002",
    "NO_RESULT": "GEMARC_GATE_003",
    "BAD_RESULT": "GEMARC_GATE_004",
    "WIDTH": "GEMARC_GATE_005",
    "PAGE_ERRORS": "GEMARC_GATE_006",
    "CONSOLE_STDERR": "GEMARC_GATE_007",
    "HEADER_OVERLAP": "GEMARC_GATE_008",
    "HEADER_OUTSIDE": "GEMARC_GATE_009",
    "OVERFLOW_X": "GEMARC_GATE_010",
    "FIT_CLIPPED": "GEMARC_GATE_011",
    "TEXT_BAD": "GEMARC_GATE_012",
    "HEARTBEAT": "GEMARC_GATE_013",
    "RENDER": "GEMARC_GATE_014",
    "STEP_FAILED": "GEMARC_GATE_015",
    "NO_TABS": "GEMARC_GATE_016",
    "NON_BG": "GEMARC_GATE_017",
    "RENDERER": "GEMARC_GATE_018",
    "HEADER_MISSING": "GEMARC_GATE_019",
    "GL_ERROR": "GEMARC_GATE_020",
    "NO_DRAW": "GEMARC_GATE_021",
    "TAB_STATE": "GEMARC_GATE_022",
    "FRAME_CLIPPED": "GEMARC_GATE_023",
    "NODE_CLIPPED": "GEMARC_GATE_024",
    "SCENE_SPARSE": "GEMARC_GATE_025",
}

# Minimal page used by calibrate(): reports the viewport and nothing else.
CALIBRATION_PAGE = (
    '<!doctype html><html><head><meta charset="utf-8"><title>gate calibration</title></head><body>'
    "<script>setTimeout(function(){var p=document.createElement('pre');p.id='atlas-gate';"
    "p.setAttribute('data-done','1');p.textContent=JSON.stringify({v:2,tabs:[],steps:[],errors:[],"
    "missingHooks:[],innerWidth:window.innerWidth,innerHeight:window.innerHeight,"
    "ua:navigator.userAgent});document.body.appendChild(p);},10);</script></body></html>"
)


# --------------------------------------------------------------------------------------------------
# Data classes
# --------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Browser:
    path: str
    family: str  # "edge" or "chrome" (chrome also covers chromium)
    version: str | None = None


@dataclass(frozen=True)
class Finding:
    name: str
    detail: str = ""
    tab: str | None = None

    @property
    def code(self) -> str:
        return CODES[self.name]

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "name": self.name, "detail": self.detail}
        if self.tab is not None:
            out["tab"] = self.tab
        return out

    def __str__(self) -> str:
        where = f" [{self.tab}]" if self.tab is not None else ""
        return f"{self.code} {self.name}{where}: {self.detail}".rstrip(": ")


@dataclass
class GateResult:
    status: str  # PASS | FAIL | SKIP
    page: str
    width: int
    window: tuple[int, int] | None = None
    browser: Browser | None = None
    findings: list[Finding] = field(default_factory=list)
    result: dict[str, Any] | None = None
    console: list[dict[str, Any]] = field(default_factory=list)
    attempts: int = 0
    process_failures: list[str] = field(default_factory=list)
    seconds: float = 0.0  # the LAST launch attempt
    total_seconds: float = 0.0  # every attempt together (what the caller actually waited)
    spawn_seconds: float = 0.0
    leftovers: list[str] = field(default_factory=list)
    message: str = ""

    @property
    def codes(self) -> list[str]:
        """Sorted finding names, the value a calibration fixture pins."""
        return sorted({f.name for f in self.findings})

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "page": self.page,
            "width": self.width,
            "window": list(self.window) if self.window else None,
            "browser": {"path": self.browser.path, "family": self.browser.family, "version": self.browser.version}
            if self.browser
            else None,
            "findings": [f.as_dict() for f in self.findings],
            "attempts": self.attempts,
            "processFailures": self.process_failures,
            "seconds": round(self.seconds, 3),
            "totalSeconds": round(self.total_seconds, 3),
            "spawnSeconds": round(self.spawn_seconds, 3),
            "leftovers": self.leftovers,
            "message": self.message,
            "innerWidth": (self.result or {}).get("innerWidth"),
        }


# --------------------------------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------------------------------
def load_calibration(path: pathlib.Path | None = None) -> dict[str, Any]:
    with open(path or CALIBRATION_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def width_offset(calibration: Mapping[str, Any], family: str, width: int) -> int:
    """Window width minus innerWidth for one browser family at one target width.

    A value measured at exactly this width wins; otherwise the family constant. The offset is not
    portable across families (Edge 24 px, Chrome 16 px here) and clamps for very small windows, so
    the runner always verifies the reported innerWidth afterwards.
    """
    browsers = calibration.get("browsers", {})
    entry = browsers.get(family) or browsers.get("chrome") or {}
    measured = entry.get("measured", {})
    if str(width) in measured:
        return int(measured[str(width)])
    return int(entry.get("widthOffset", 24))


# --------------------------------------------------------------------------------------------------
# Browser discovery
# --------------------------------------------------------------------------------------------------
def _family_of(path: str) -> str:
    name = os.path.basename(path).lower()
    return "edge" if "edge" in name else "chrome"


def _windows_roots(environ: Mapping[str, str]) -> list[str]:
    roots = []
    for key in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        value = environ.get(key)
        if value and value not in roots:
            roots.append(value)
    return roots


def candidate_paths(
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    platform: str | None = None,
) -> list[str]:
    """Ordered browser candidates: explicit override, PATH, default installs; Edge before Chrome."""
    environ = os.environ if environ is None else environ
    platform = platform or sys.platform
    out: list[str] = []

    def add(path: str | None) -> None:
        if path and path not in out:
            out.append(path)

    add(environ.get(BROWSER_ENV) or None)
    for name in ("msedge", "microsoft-edge", "microsoft-edge-stable"):
        add(which(name))
    if platform.startswith("win"):
        for root in _windows_roots(environ):
            add(os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe"))
    elif platform == "darwin":
        add("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")
    for name in ("chrome", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        add(which(name))
    if platform.startswith("win"):
        for root in _windows_roots(environ):
            add(os.path.join(root, "Google", "Chrome", "Application", "chrome.exe"))
    elif platform == "darwin":
        add("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        add("/Applications/Chromium.app/Contents/MacOS/Chromium")
    return out


def detect_version(path: str) -> str | None:
    """Best effort. On Windows the install directory holds a folder named by the version."""
    try:
        parent = pathlib.Path(path).parent
        versions = [p.name for p in parent.iterdir() if p.is_dir() and re.fullmatch(r"\d+(\.\d+){3}", p.name)]
        if versions:
            return max(versions, key=lambda v: tuple(int(x) for x in v.split(".")))
    except OSError:
        pass
    return None


def find_browser(
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    isfile: Callable[[str], bool] = os.path.isfile,
    platform: str | None = None,
) -> Browser | None:
    """First usable browser. An explicit pin (env GEMARC_ATLAS_BROWSER) is never replaced by another
    browser: if the pinned path is not a file the result is None (a loud SKIP), because a gate that
    quietly ran on a browser nobody chose would be measured against the wrong calibration."""
    environ = os.environ if environ is None else environ
    pinned = environ.get(BROWSER_ENV)
    if pinned:
        if isfile(pinned):
            return Browser(path=pinned, family=_family_of(pinned), version=detect_version(pinned))
        return None
    for path in candidate_paths(environ, which, platform):
        if isfile(path):
            return Browser(path=path, family=_family_of(path), version=detect_version(path))
    return None


def no_browser_message(environ: Mapping[str, str] | None = None, pinned: str | None = None) -> str:
    environ = os.environ if environ is None else environ
    pinned = pinned if pinned is not None else (environ.get(BROWSER_ENV) or None)
    if pinned:
        what = (
            f"the pinned browser {pinned!r} is not a file (the gate does not fall back to a browser "
            "you did not choose)"
        )
    else:
        what = "no Edge or Chrome found"
    return (
        f"{CODES['NO_BROWSER']} SKIPPED, NOT PASSED: {what}. The browser gate did not run, so the "
        f"rendered page is UNVERIFIED. Install Edge or Chrome or set {BROWSER_ENV} to the executable."
    )


# --------------------------------------------------------------------------------------------------
# Command line, URLs, page assembly
# --------------------------------------------------------------------------------------------------
def page_url(page: pathlib.Path | str, query: str = "gate=1") -> str:
    """Percent-encoded file:// URL (safe for non-ASCII and space-containing paths) plus query."""
    uri = pathlib.Path(page).resolve().as_uri()
    return f"{uri}?{query}" if query else uri


def build_args(
    browser_path: str,
    user_data_dir: str,
    window: tuple[int, int],
    url: str,
    budget_ms: int,
) -> list[str]:
    """The design's runner flags. Each one is load-bearing (measured, see README):

    * --virtual-time-budget: without it the DOM is dumped at load, before any timer ran
    * --disable-gpu + --enable-unsafe-swiftshader + --use-angle=swiftshader: pins the WebGL
      renderer to SwiftShader; the real GPU (or WARP) would make pixels machine-dependent
    """
    return [
        browser_path,
        "--headless=new",
        "--disable-gpu",
        "--enable-unsafe-swiftshader",
        "--use-angle=swiftshader",
        f"--user-data-dir={user_data_dir}",
        f"--window-size={window[0]},{window[1]}",
        f"--virtual-time-budget={int(budget_ms)}",
        "--enable-logging=stderr",
        "--v=0",
        "--dump-dom",
        url,
    ]


def with_nonce(query: str, nonce: str) -> str:
    """Append the per-launch nonce to the query string (the self-test echoes it in its result)."""
    return f"{query}&{NONCE_PARAM}={nonce}" if query else f"{NONCE_PARAM}={nonce}"


def assemble_page(html_text: str, selftest_js: str | None = None) -> str:
    """Replace the fixture placeholder with the inlined self-test (mirrors what build.py will do).

    Test and fixture convenience only: the assembled page is written to a temporary directory, so a
    page with relative assets (src/href) would not find them there. Such assets 404 and show up as
    PAGE_ERRORS (fail-closed), but the right way to gate a real page is a build that already embeds
    the self-test, run in place without inline_selftest."""
    if SELFTEST_MARKER not in html_text:
        raise ValueError(f"page has no {SELFTEST_MARKER} placeholder")
    if selftest_js is None:
        selftest_js = SELFTEST_PATH.read_text(encoding="utf-8")
    safe = selftest_js.replace("</script", "<\\/script")
    # str.replace takes a literal replacement (re.sub would interpret backslashes in the JS)
    return html_text.replace(SELFTEST_MARKER, "<script>\n" + safe + "</script>", 1)


def write_text_lf(path: pathlib.Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


# --------------------------------------------------------------------------------------------------
# Result extraction (dump-dom) and console scraping (stderr)
# --------------------------------------------------------------------------------------------------
class _GateParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[dict[str, Any]] = []
        self._cur: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if attributes.get("id") == RESULT_ID:
            self._cur = {"tag": tag, "attrs": attributes, "text": [], "depth": 1}
            self.found.append(self._cur)
        elif self._cur is not None:
            self._cur["depth"] += 1

    def handle_endtag(self, tag: str) -> None:
        if self._cur is not None:
            self._cur["depth"] -= 1
            if self._cur["depth"] <= 0:
                self._cur = None

    def handle_data(self, data: str) -> None:
        if self._cur is not None:
            self._cur["text"].append(data)


def extract_result(dom: bytes | str) -> tuple[dict[str, Any] | None, Finding | None]:
    """Parse the gate result out of a dumped DOM. Exactly one outcome: (result, None) or (None, finding)."""
    if isinstance(dom, bytes):
        try:
            text = dom.decode("utf-8")  # strict: a decode error means the result is untrustworthy
        except UnicodeDecodeError as exc:
            return None, Finding("BAD_RESULT", f"dumped DOM is not valid UTF-8 ({exc.reason} at byte {exc.start})")
    else:
        text = dom
    parser = _GateParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:  # html.parser is lenient, but never let a parser bug read as PASS
        return None, Finding("BAD_RESULT", f"HTML parse error: {exc}")
    if not parser.found:
        return None, Finding("NO_RESULT", f'no element with id="{RESULT_ID}" in the dumped DOM')
    if len(parser.found) > 1:
        return None, Finding("BAD_RESULT", f'{len(parser.found)} elements with id="{RESULT_ID}" (expected exactly 1)')
    element = parser.found[0]
    if element["tag"] != "pre":
        return None, Finding("BAD_RESULT", f"result element is <{element['tag']}>, expected <pre>")
    if element["attrs"].get("data-done") != "1":
        return None, Finding("BAD_RESULT", 'result element has no data-done="1" (walker did not finish)')
    raw = "".join(element["text"]).strip()
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        return None, Finding("BAD_RESULT", f"result is not valid JSON: {exc} (first 60 chars: {raw[:60]!r})")
    if not isinstance(parsed, dict):
        return None, Finding("BAD_RESULT", f"result JSON is {type(parsed).__name__}, expected an object")
    if parsed.get("v") != RESULT_SCHEMA:
        return None, Finding("BAD_RESULT", f"unsupported result schema v={parsed.get('v')!r} (expected {RESULT_SCHEMA})")
    # Errors after the walker finished are mirrored by the self-test into data-late-errors. Only the
    # attribute is accepted as their source, never a same-named key inside the JSON body.
    parsed.pop("lateErrors", None)
    raw_late = element["attrs"].get("data-late-errors")
    if raw_late is not None:
        try:
            late = json.loads(raw_late)
        except ValueError as exc:
            return None, Finding("BAD_RESULT", f"data-late-errors is not valid JSON: {exc}")
        if not (
            isinstance(late, dict)
            and isinstance(late.get("count"), int)
            and not isinstance(late.get("count"), bool)
            and isinstance(late.get("errors"), list)
        ):
            return None, Finding("BAD_RESULT", "data-late-errors has the wrong shape (expected {count, errors[]})")
        parsed["lateErrors"] = late
    return parsed, None


_CONSOLE_RE = re.compile(
    r"^\[\d+:\d+:\d+/\d+\.\d+:(?P<level>[A-Z]+):CONSOLE[:(](?P<line>\d*)\)?\] "
    r'"(?P<msg>(?:(?!\n\[\d+:\d+:\d+/\d+\.\d+:)[\s\S])*?)", source: (?P<src>\S+) \((?P<srcline>\d+)\)[ \t]*$',
    re.M,
)


def parse_console_lines(stderr: bytes | str) -> list[dict[str, Any]]:
    """Every ``CONSOLE`` record on stderr as {level, message, source, line}. Never raises."""
    text = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else stderr
    text = text.replace("\r\n", "\n")
    out = []
    for match in _CONSOLE_RE.finditer(text):
        out.append(
            {
                "level": match.group("level"),
                "message": match.group("msg"),
                "source": match.group("src"),
                "line": int(match.group("srcline")),
            }
        )
    return out


def _norm_url(url: str) -> str:
    url = urllib.parse.unquote(url)
    return url.lower() if os.name == "nt" else url


def console_errors_from_origin(
    records: Sequence[Mapping[str, Any]],
    origin_prefix: str,
    calibration: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Error-class console records whose source lies under the page's own directory.

    Edge and Chrome log every console message (including console.error) at INFO on stderr, so the
    level alone never matches; uncaught exceptions, syntax errors, unhandled rejections and WebGL
    errors are recognised by message prefix (calibration.json) as well. Records from other origins
    (browser extensions, internal pages) are ignored.
    """
    conf = calibration.get("console", {})
    levels = set(conf.get("errorLevels", ["ERROR", "FATAL"]))
    patterns = [re.compile(p) for p in conf.get("errorMessagePatterns", [])]
    prefix = _norm_url(origin_prefix)
    out = []
    for record in records:
        if not _norm_url(str(record.get("source", ""))).startswith(prefix):
            continue
        message = str(record.get("message", ""))
        if record.get("level") in levels or any(p.search(message) for p in patterns):
            out.append(dict(record))
    return out


# --------------------------------------------------------------------------------------------------
# Verdict
# --------------------------------------------------------------------------------------------------
def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


GL_KINDS = ("webgl", "webgl2", "experimental-webgl")
# Context kinds whose draw calls the self-test counts. Other kinds (webgpu, bitmaprenderer) cannot be
# counted, so they are judged by their pixels only and never fail NO_DRAW.
DRAW_COUNTED_KINDS = ("2d",) + GL_KINDS


def _late_errors(result: Mapping[str, Any]) -> tuple[int, list[Any]]:
    late = result.get("lateErrors")
    if isinstance(late, Mapping) and isinstance(late.get("count"), int):
        errors = late.get("errors")
        return int(late["count"]), list(errors) if isinstance(errors, list) else []
    return 0, []


def _canvas_findings(
    canvases: Any,
    draws: Any,
    tab: str,
    floor: float,
    renderer_needle: str,
) -> list[Finding]:
    """Verdicts for the canvases the self-test read itself (never for page-reported numbers)."""
    out: list[Finding] = []
    if not isinstance(canvases, list):
        return [Finding("BAD_RESULT", "tab record has no canvases list", tab)]
    with_context = 0
    for canvas in canvases:
        if not isinstance(canvas, dict):
            out.append(Finding("BAD_RESULT", "a canvas record is not an object", tab))
            continue
        name = str(canvas.get("el", "canvas"))
        visible = bool(canvas.get("visible"))
        if canvas.get("declared") and canvas.get("isCanvas") is False:
            out.append(Finding("NON_BG", f"{name}: data-gate=canvas is on an element that is not a <canvas>", tab))
            continue
        if not visible:
            if canvas.get("declared"):
                out.append(Finding("NON_BG", f"{name}: declared with data-gate=canvas but not visible", tab))
            continue
        kind = canvas.get("kind")
        if kind in DRAW_COUNTED_KINDS:
            with_context += 1
        if canvas.get("readError"):
            out.append(Finding("NON_BG", f"{name}: pixels could not be read back ({canvas.get('readError')})", tab))
        else:
            ratio = _num(canvas.get("nonBgRatio"))
            if ratio is None or not ratio > floor:
                why = "no rendering context was ever created" if not kind else f"{kind} context"
                out.append(
                    Finding("NON_BG", f"{name}: nonBgRatio {ratio!r} is not > {floor:g} (blank canvas; {why})", tab)
                )
        glerrors = canvas.get("glErrors")
        if canvas.get("contextLost") or (isinstance(glerrors, list) and glerrors):
            out.append(
                Finding(
                    "GL_ERROR",
                    f"{name}: WebGL getError {glerrors!r}{' and the context is lost' if canvas.get('contextLost') else ''}",
                    tab,
                )
            )
        if kind in GL_KINDS and renderer_needle:
            renderer = str(canvas.get("renderer", ""))
            if renderer_needle.lower() not in renderer.lower():
                out.append(Finding("RENDERER", f"{name}: renderer {renderer!r} does not contain {renderer_needle!r}", tab))
    if with_context:
        count = _num(draws)
        if count is None or count <= 0:
            out.append(
                Finding(
                    "NO_DRAW",
                    "a visible canvas has a context but the page issued no draw call during renderOnce "
                    f"(draw calls counted: {draws!r}); the scene is not being drawn",
                    tab,
                )
            )
    return out


def _scene_findings(rec: Mapping[str, Any], tab: str, clip_px: float, occupancy_min: float) -> list[Finding]:
    """Verdicts on the projected scene geometry of one tab (3D, 4D, 5D): frames and nodes inside the canvas, and the occupancy.

    ``rec["scene"]`` is what the page's ``sceneBounds(tab)`` hook returned (frames ``{id, x, y, w, h}`` and nodes ``{id, x, y}`` in CSS
    pixels from the canvas corner; a null ``w``, ``h``, ``x`` or ``y`` means "behind the camera or not finite") and
    ``rec["sceneCanvas"]`` the CSS size of the visible ``data-gate=canvas`` element, which the self-test measured itself. A tab whose hook
    returned nothing has no scene and nothing is judged. Occupancy is the area of the bounding box of the nodes over the area of the
    bounding box of the frames; a scene that fills less than ``occupancy_min`` of its frames is "nodes in huge frames".
    A hook may say ``"occupancy": false`` for a tab whose frames are not meant to be filled (the 4D and 5D stages frame whole rooms): only
    the occupancy is skipped, the inside-the-canvas tests always run."""
    scene = rec.get("scene")
    if scene is None:
        return []
    if rec.get("sceneError"):
        return [Finding("RENDER", f"sceneBounds hook threw: {rec.get('sceneError')}", tab)]
    if not isinstance(scene, dict) or not isinstance(scene.get("frames"), list) or not isinstance(scene.get("nodes"), list):
        return [Finding("BAD_RESULT", "sceneBounds did not return {frames: [...], nodes: [...]}", tab)]
    box = rec.get("sceneCanvas")
    width, height = (_num(box.get("w")), _num(box.get("h"))) if isinstance(box, dict) else (None, None)
    if width is None or height is None or not (width > 0 and height > 0):
        return [Finding("FRAME_CLIPPED", "scene bounds were reported but no visible canvas marked data-gate=canvas could be measured", tab)]
    out: list[Finding] = []

    def inside(x: float, y: float) -> bool:
        return -clip_px <= x <= width + clip_px and -clip_px <= y <= height + clip_px

    bad_frames = []
    frame_boxes = []
    for frame in scene["frames"]:
        x, y, w, h = (_num(frame.get("x")), _num(frame.get("y")), _num(frame.get("w")), _num(frame.get("h"))) if isinstance(frame, dict) else (None,) * 4
        if None in (x, y, w, h) or not (inside(x, y) and inside(x + w, y + h)):
            bad_frames.append(str(frame.get("id")) if isinstance(frame, dict) else "?")
        else:
            frame_boxes.append((x, y, x + w, y + h))
    if bad_frames:
        out.append(Finding("FRAME_CLIPPED", f"{len(bad_frames)} projected frame(s) leave the {width:g} x {height:g} canvas by more than {clip_px:g}px, first: {bad_frames[:3]}", tab))
    points = []  # (x0, y0, x1, y1) of each node: a node is not a point, it has the projected radius `r` the page reports (missing: 0)
    bad_nodes = []
    for node in scene["nodes"]:
        x, y = (_num(node.get("x")), _num(node.get("y"))) if isinstance(node, dict) else (None, None)
        if x is None or y is None or not inside(x, y):
            bad_nodes.append(str(node.get("id")) if isinstance(node, dict) else "?")
        else:
            r = max(0.0, _num(node.get("r")) or 0.0)
            points.append((x - r, y - r, x + r, y + r))
    if bad_nodes:
        out.append(Finding("NODE_CLIPPED", f"{len(bad_nodes)} projected node(s) leave the {width:g} x {height:g} canvas by more than {clip_px:g}px, first: {bad_nodes[:3]}", tab))
    if frame_boxes and not bad_frames:
        frame_area = (max(b[2] for b in frame_boxes) - min(b[0] for b in frame_boxes)) * (max(b[3] for b in frame_boxes) - min(b[1] for b in frame_boxes))
        if not points:
            out.append(Finding("SCENE_SPARSE", "frames are projected but no node is", tab))
        elif frame_area > 0 and occupancy_min > 0 and scene.get("occupancy") is not False:
            node_area = (max(p[2] for p in points) - min(p[0] for p in points)) * (max(p[3] for p in points) - min(p[1] for p in points))
            if node_area / frame_area < occupancy_min:
                out.append(Finding("SCENE_SPARSE", f"the nodes fill {node_area / frame_area:.3f} of the area of their frames (floor {occupancy_min:g}): nodes in huge frames", tab))
    return out


def evaluate(
    result: Mapping[str, Any],
    target_width: int,
    calibration: Mapping[str, Any],
    *,
    console_errors: Sequence[Mapping[str, Any]] = (),
    non_bg_floor: float | None = None,
    require_renderer: str | None = None,
    require_header: bool = True,
    expected_nonce: str | None = None,
    scan_text: bool = True,
) -> list[Finding]:
    """All assertions, from the raw measurements. An empty list is the only PASS.

    non_bg_floor: None takes ``thresholds.nonBgFloor`` from calibration.json (a negative value turns
        the pixel floor off). Applies to every visible or declared canvas, automatically.
    require_renderer: None takes ``gl.rendererSubstring`` from calibration.json; "" turns it off.
    require_header: every tab must show at least one control inside a ``data-gate=header`` element.
        False is the explicit opt-out for pages that have no header.
    expected_nonce: when given, a result whose nonce differs was not produced by this launch's
        self-test and nothing else in it is trusted.
    scan_text: False is the explicit opt-out of the undefined/NaN/[object scan (a page whose visible prose must
        say those words and cannot list them through the textAllow hook).
    """
    thresholds = calibration.get("thresholds", {})
    overlap_max = float(thresholds.get("headerOverlapPx", 2))
    overflow_max = float(thresholds.get("scrollOverflowPx", 0))
    floor = float(non_bg_floor if non_bg_floor is not None else thresholds.get("nonBgFloor", 0.0005))
    renderer_needle = (
        require_renderer if require_renderer is not None else str(calibration.get("gl", {}).get("rendererSubstring", ""))
    )
    clip_px = float(thresholds.get("sceneClipPx", 2))
    occupancy_min = float(thresholds.get("sceneOccupancyMin", 0))
    out: list[Finding] = []

    if expected_nonce is not None and result.get("nonce") != expected_nonce:
        return [
            Finding(
                "BAD_RESULT",
                f"result nonce {result.get('nonce')!r} does not match this launch's nonce: the result was not "
                "produced by this launch's self-test (stale, hard-coded or from another page)",
            )
        ]

    inner = _num(result.get("innerWidth"))
    if inner is None or int(inner) != int(target_width):
        out.append(
            Finding(
                "WIDTH",
                f"page reported innerWidth={result.get('innerWidth')!r}, target {target_width}; the window "
                "offset in calibration.json no longer matches this browser (run smoke.py --calibrate)",
            )
        )

    errors = result.get("errors")
    late_count, late_errors = _late_errors(result)
    if not isinstance(errors, list):
        out.append(Finding("BAD_RESULT", "result.errors is missing or not a list"))
    elif errors or late_count:
        parts = []
        if errors:
            parts.append(f"{len(errors)} in-page error(s), first: {errors[0]}")
        if late_count:
            first_late = late_errors[0] if late_errors else "?"
            parts.append(f"{late_count} error(s) after the walker finished (late), first: {first_late}")
        out.append(Finding("PAGE_ERRORS", "; ".join(parts)))

    if console_errors:
        first = console_errors[0]
        out.append(
            Finding(
                "CONSOLE_STDERR",
                f"{len(console_errors)} console error(s) from the page origin on stderr, first: "
                f"{str(first.get('message', ''))[:120]!r} ({first.get('level')})",
            )
        )

    missing = result.get("missingHooks")
    if isinstance(missing, list) and missing:
        out.append(Finding("RENDER", f"page did not provide hook(s): {', '.join(map(str, missing))}"))

    tabs = result.get("tabs")
    if not isinstance(tabs, list) or not tabs:
        out.append(Finding("NO_TABS", "the walker measured no tab, so nothing was checked"))
        tabs = []

    previous_state: Any = None
    check_state = bool(result.get("tabStateChecked"))
    for rec in tabs:
        if not isinstance(rec, dict):
            out.append(Finding("BAD_RESULT", "a tab record is not an object"))
            continue
        tab = str(rec.get("tab"))
        if rec.get("threw") or rec.get("walkerError"):
            out.append(Finding("RENDER", f"renderOnce/walker threw: {rec.get('threw')}", tab))
        frames = rec.get("frames")
        f1, f2 = (_num(frames[1]), _num(frames[2])) if isinstance(frames, list) and len(frames) == 3 else (None, None)
        if f1 is None or f2 is None:
            out.append(Finding("HEARTBEAT", f"frame counter unreadable (frames={frames!r})", tab))
        elif not f2 > f1:
            out.append(Finding("HEARTBEAT", f"frame counter did not advance across the second renderOnce ({f1:g} -> {f2:g})", tab))
        reads = rec.get("frameReads")
        if not (isinstance(reads, list) and len(reads) == 2):
            out.append(Finding("BAD_RESULT", "tab record has no frameReads", tab))
        else:
            r0, r1 = _num(reads[0]), _num(reads[1])
            if r0 is not None and r1 is not None and r0 != r1:
                out.append(
                    Finding(
                        "HEARTBEAT",
                        f"frame counter changed between two consecutive reads ({r0:g} -> {r1:g}) with nothing rendered "
                        "in between: frame() counts its own calls, not rendered frames",
                        tab,
                    )
                )
        header = rec.get("header")
        if isinstance(header, dict):
            roots, controls = _num(header.get("roots")), _num(header.get("n"))
            if require_header and (roots is None or controls is None or roots < 1 or controls < 1):
                out.append(
                    Finding(
                        "HEADER_MISSING",
                        f"no visible control inside a [data-gate=header] element (roots={header.get('roots')!r}, "
                        f"controls={header.get('n')!r}); mark the header, or opt out explicitly "
                        "(require_header=False / --no-header) for a page that has none",
                        tab,
                    )
                )
            overlap = _num(header.get("maxOverlapPx")) or 0.0
            if overlap > overlap_max:
                out.append(Finding("HEADER_OVERLAP", f"header controls overlap by {overlap:g}px (limit {overlap_max:g}): {header.get('pair')}", tab))
            if header.get("outside"):
                out.append(Finding("HEADER_OUTSIDE", f"header controls outside the viewport: {header.get('outside')}", tab))
        else:
            out.append(Finding("BAD_RESULT", "tab record has no header measurement", tab))
        scroll, client = _num(rec.get("scrollWidth")), _num(rec.get("clientWidth"))
        if scroll is None or client is None:
            out.append(Finding("BAD_RESULT", "tab record has no scrollWidth/clientWidth", tab))
        elif scroll > client + overflow_max or (inner is not None and scroll > inner + overflow_max):
            out.append(
                Finding(
                    "OVERFLOW_X",
                    f"scrollWidth {scroll:g} > clientWidth {client:g} (viewport width without the scrollbar; "
                    f"innerWidth {inner if inner is None else format(inner, 'g')})",
                    tab,
                )
            )
        fit = rec.get("fit")
        if isinstance(fit, dict) and fit.get("clipped"):
            out.append(Finding("FIT_CLIPPED", f"[data-gate=fit] elements clipped: {fit.get('clipped')}", tab))
        if scan_text and rec.get("textBad"):
            out.append(Finding("TEXT_BAD", f"text or title/aria-label/alt contains undefined/NaN/[object: {rec.get('textBad')}", tab))
        if not rec.get("walkerError"):
            out.extend(_canvas_findings(rec.get("canvases"), rec.get("draws"), tab, floor, renderer_needle))
            out.extend(_scene_findings(rec, tab, clip_px, occupancy_min))
        if check_state:
            state = rec.get("state")
            if state is None:
                out.append(Finding("TAB_STATE", "tab state hook returned nothing or threw", tab))
            elif previous_state is not None and state == previous_state:
                out.append(Finding("TAB_STATE", f"tab left the page state unchanged ({state!r}): setTab did nothing", tab))
            previous_state = state if state is not None else previous_state

    for step in result.get("steps") or []:
        if isinstance(step, dict) and not step.get("ok"):
            out.append(Finding("STEP_FAILED", f"scripted step {step.get('name')!r} failed: {step.get('msg')}"))
    return out


# --------------------------------------------------------------------------------------------------
# Process handling
# --------------------------------------------------------------------------------------------------
@dataclass
class _Launch:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    seconds: float
    spawn_seconds: float = 0.0  # time spent inside Popen itself (process creation can stall under AV scanning)
    error: str | None = None  # the browser could not be started at all (missing file, not an executable)


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the browser and everything it spawned (renderer, GPU and crashpad helpers)."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            # _launch starts the child in its own session, so its pid is the process-group id.
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def _browser_env(scratch: pathlib.Path) -> dict[str, str]:
    """Environment for the browser child with its temp directory inside the gate's own work directory.

    Edge writes outside --user-data-dir, into the temp directory: an updater log (cv_debug.log), a first-run
    importer folder (Importer_0_4) and url-fetcher / BITS .tmp files. A launch that lives long enough to run
    those tasks (one killed on timeout) would leave them in the user's real temp directory. Measured: 0 of
    800 normal launches left anything, a timeout-killed launch left three entries, and with this redirect the
    real temp directory stayed empty. Everything under ``scratch`` goes away with the work directory."""
    scratch.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    for key in ("TEMP", "TMP", "TMPDIR"):
        env[key] = str(scratch)
    return env


def _launch(args: list[str], timeout: float, env: Mapping[str, str] | None = None) -> _Launch:
    kwargs: dict[str, Any] = {}
    if env is not None:
        kwargs["env"] = dict(env)
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True  # own process group, so _kill_tree can signal the whole tree
    started = time.perf_counter()
    try:
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    except OSError as exc:  # FileNotFoundError, PermissionError, WinError 193 (not a valid executable) ...
        elapsed = time.perf_counter() - started
        return _Launch(None, b"", b"", False, elapsed, elapsed, error=f"{type(exc).__name__}: {exc}")
    spawn = time.perf_counter() - started
    timed_out = False
    try:
        out, err = proc.communicate(timeout=max(1.0, timeout - spawn))  # `timeout` bounds the whole launch
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=15)
        except Exception:
            out, err = b"", b""
    return _Launch(proc.returncode, out or b"", err or b"", timed_out, time.perf_counter() - started, spawn)


def _rmtree_retry(path: pathlib.Path, attempts: int = 25, delay: float = 0.2) -> bool:
    """Remove a browser profile; Windows keeps handles for a moment after exit. True when gone."""
    for _ in range(attempts):
        if not path.exists():
            return True
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return True
        time.sleep(delay)
    return not path.exists()


def process_failure_reason(launch: _Launch) -> str | None:
    """Process-level failure (retryable) or None. A complete DOM without a result is NOT one."""
    if launch.error:
        return f"could not start the browser: {launch.error}"
    if launch.timed_out:
        return f"timeout after {launch.seconds:.1f}s (process tree killed)"
    if launch.returncode != 0:
        return f"browser exited with code {launch.returncode}"
    if b"</html>" not in launch.stdout.lower():
        return f"truncated or empty DOM dump ({len(launch.stdout)} bytes, no </html>)"
    return None


# --------------------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------------------
def run_gate(
    page: pathlib.Path | str,
    width: int,
    *,
    height: int = 800,
    browser: Browser | None = None,
    tmp_root: pathlib.Path | str | None = None,
    timeout: float | None = None,
    retries: int = 1,
    budget_ms: int | None = None,
    query: str = "gate=1",
    inline_selftest: bool = False,
    non_bg_floor: float | None = None,
    require_renderer: str | None = None,
    require_header: bool = True,
    calibration: Mapping[str, Any] | None = None,
    scan_text: bool = True,
    retry_on_timeout: bool = True,
) -> GateResult:
    """Run the gate once at one target innerWidth. Never raises for page problems.

    Canvas floor and renderer substring come from calibration.json unless overridden (see evaluate);
    the header check is on unless require_header=False. inline_selftest is for self-contained
    fixtures only (the assembled copy lives in a temporary directory, relative assets would not be
    found there). Every launch attempt gets its own random nonce, so a retry can never accept the
    DOM of an earlier attempt. ``scan_text=False`` turns the undefined/NaN/[object scan off; ``retry_on_timeout=False`` makes a timed-out
    launch final (a second launch on a loaded machine only repeats it)."""
    page = pathlib.Path(page)
    calibration = calibration if calibration is not None else load_calibration()
    limits = calibration.get("limits", {})
    timeout = float(timeout if timeout is not None else limits.get("timeoutSeconds", 30))
    budget_ms = int(budget_ms if budget_ms is not None else limits.get("virtualTimeBudgetMs", 20000))
    result = GateResult(status="FAIL", page=str(page), width=int(width))

    browser = browser or find_browser()
    if browser is None:
        result.status = "SKIP"
        result.findings = [Finding("NO_BROWSER", "no Edge or Chrome found")]
        result.message = no_browser_message()
        return result
    result.browser = browser
    offset = width_offset(calibration, browser.family, width)
    result.window = (int(width) + offset, int(height))

    root = pathlib.Path(tmp_root) if tmp_root is not None else None
    if root is not None:
        root.mkdir(parents=True, exist_ok=True)
    if not page.is_file():
        result.findings = [Finding("NO_RESULT", f"page not found: {page}")]
        result.message = result.findings[0].detail
        return result
    work = pathlib.Path(tempfile.mkdtemp(prefix="atlas-gate-", dir=str(root) if root else None))
    try:
        target = page
        if inline_selftest:
            target = work / "page.html"
            write_text_lf(target, assemble_page(page.read_text(encoding="utf-8")))
        origin = target.resolve().parent.as_uri() + "/"

        launch: _Launch | None = None
        reason: str | None = None
        nonce = ""
        for attempt in range(retries + 1):
            nonce = secrets.token_hex(8)  # per attempt: the result must come from THIS launch
            url = page_url(target, with_nonce(query, nonce))
            profile = work / f"profile-{attempt}"
            profile.mkdir()
            try:
                launch = _launch(
                    build_args(browser.path, str(profile), result.window, url, budget_ms),
                    timeout,
                    env=_browser_env(work / f"browser-tmp-{attempt}"),
                )
            finally:
                if not _rmtree_retry(profile):
                    result.leftovers.append(str(profile))
            result.attempts = attempt + 1
            result.seconds = launch.seconds
            result.total_seconds += launch.seconds
            result.spawn_seconds = launch.spawn_seconds
            reason = process_failure_reason(launch)
            if reason is None:
                break
            result.process_failures.append(reason)
            if launch.error:
                break  # the browser cannot be started; another attempt would fail the same way
            if launch.timed_out and not retry_on_timeout:
                break
        if reason is not None or launch is None:
            result.findings = [Finding("PROCESS", f"{len(result.process_failures)} launch(es) failed: {'; '.join(result.process_failures)}")]
            result.message = str(result.findings[0])
            return result

        records = parse_console_lines(launch.stderr)
        result.console = console_errors_from_origin(records, origin, calibration)
        parsed, bad = extract_result(launch.stdout)
        if bad is not None:
            result.findings = [bad]
            if result.console:
                result.findings.append(Finding("CONSOLE_STDERR", f"first: {result.console[0]['message'][:120]!r}"))
            result.message = str(bad)
            return result
        result.result = parsed
        result.findings = evaluate(
            parsed,
            width,
            calibration,
            console_errors=result.console,
            non_bg_floor=non_bg_floor,
            require_renderer=require_renderer,
            require_header=require_header,
            expected_nonce=nonce,
            scan_text=scan_text,
        )
        result.status = "PASS" if not result.findings else "FAIL"
        result.message = "; ".join(str(f) for f in result.findings) if result.findings else "all assertions passed"
        return result
    finally:
        if not _rmtree_retry(work):
            result.leftovers.append(str(work))


def run_widths(
    page: pathlib.Path | str,
    widths: Sequence[int],
    **kwargs: Any,
) -> list[GateResult]:
    return [run_gate(page, int(w), **kwargs) for w in widths]


DEFAULT_WIDTHS = (1024, 1366, 1600)
#: Wall-clock limit per launch on the tool path (build gate=smoke, verify). A good launch is 2 to 3 s on an idle machine and about 23 s at
#: worst with eight launches in parallel; a machine with as many busy processes as cores goes past 30 s (a cliff, measured). The page's own
#: time is bounded by the virtual-time budget, so a larger wall limit costs nothing on a good page.
VERIFY_TIMEOUT_SECONDS = 90.0


def summarize_result(result: GateResult) -> dict[str, Any]:
    """One width of a verdict as plain data (what the build and verify tools report)."""
    raw = result.result or {}
    tabs = [str(t.get("tab")) for t in raw.get("tabs", []) if isinstance(t, dict)]
    steps = [s for s in raw.get("steps", []) if isinstance(s, dict)]
    status, message = result.status, result.message
    if is_inconclusive(result):
        status = "INCONCLUSIVE"
        message = (f"INCONCLUSIVE, not a pass and not a page defect: the browser did not finish within {result.total_seconds / max(1, result.attempts):.0f} s "
                   "(a loaded machine does this); run it again when the machine is idle; if it times out again on an idle machine, the page itself hangs. " + result.message)
    return {
        "width": result.width,
        "status": status,
        "codes": result.codes,
        "findings": [f.as_dict() for f in result.findings],
        "innerWidth": raw.get("innerWidth"),
        "tabs": tabs,
        "steps": {"ok": sum(1 for s in steps if s.get("ok")), "total": len(steps)},
        "stepNames": [str(s.get("name")) for s in steps],
        "seconds": round(result.total_seconds, 2),
        "attempts": result.attempts,
        "message": message,
    }


def is_inconclusive(result: GateResult) -> bool:
    """A width whose ONLY failure is that every launch timed out: the page was never judged (load on the machine, not a measurement of
    the page). It is still not a PASS."""
    return (result.status == "FAIL" and result.codes == ["PROCESS"] and bool(result.process_failures)
            and all(reason.startswith("timeout") for reason in result.process_failures))


def verify_gate(
    page: pathlib.Path | str,
    widths: Sequence[int] = DEFAULT_WIDTHS,
    *,
    browser: Browser | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """The gate of a built page at several widths (one launch per width), as one verdict. Fail-closed.

    ``status``: ``PASS`` only when every width passed; ``SKIP`` when no browser was found (loud: ``message`` says it was NOT
    verified, and a SKIP is never counted as a pass); ``FAIL`` otherwise, including an unexpected exception at any width.
    ``INCONCLUSIVE`` (when nothing FAILED and no width was SKIPPED) means a launch timed out on a loaded machine: not a pass, not a page defect.
    The browser is looked up once. ``kwargs`` go to ``run_gate`` (``tmp_root``, ``retries``, ``require_header`` ...); the wall-clock limit per
    launch is :data:`VERIFY_TIMEOUT_SECONDS` unless ``timeout`` is given (virtual time, not wall time, bounds a good page), and a timeout is
    not retried."""
    widths = tuple(int(w) for w in widths)
    if not widths:
        raise ValueError("verify_gate needs at least one width")
    browser = browser or find_browser()
    kwargs.setdefault("timeout", VERIFY_TIMEOUT_SECONDS)
    kwargs.setdefault("retry_on_timeout", False)
    rows: list[dict[str, Any]] = []
    if browser is None:
        message = no_browser_message()
        rows = [{"width": w, "status": "SKIP", "codes": ["NO_BROWSER"], "findings": [Finding("NO_BROWSER", "no Edge or Chrome found").as_dict()],
                 "innerWidth": None, "tabs": [], "steps": {"ok": 0, "total": 0}, "seconds": 0.0, "attempts": 0, "message": message} for w in widths]
        return {"status": "SKIP", "widths": rows, "lines": [f"SKIP {w}: no browser; the gate was NOT run (a skip is not a pass)" for w in widths],
                "browser": None, "message": message}
    for w in widths:
        try:
            rows.append(summarize_result(run_gate(page, w, browser=browser, **kwargs)))
        except Exception as exc:  # fail closed: a crash of the runner is a failed gate, never a pass
            rows.append({"width": w, "status": "FAIL", "codes": ["PROCESS"], "findings": [Finding("PROCESS", f"runner error {type(exc).__name__}: {exc}"[:200]).as_dict()],
                         "innerWidth": None, "tabs": [], "steps": {"ok": 0, "total": 0}, "seconds": 0.0, "attempts": 0, "message": f"runner error {type(exc).__name__}"})
    if any(r["status"] == "SKIP" for r in rows):
        status = "SKIP"
    elif any(r["status"] == "FAIL" for r in rows):
        status = "FAIL"
    elif any(r["status"] == "INCONCLUSIVE" for r in rows):
        status = "INCONCLUSIVE"
    else:
        status = "PASS"
    lines = []
    for r in rows:
        head = f"{r['status']} {r['width']}"
        if r["status"] == "PASS":
            lines.append(f"{head}: {len(r['tabs'])} tabs, {r['steps']['ok']}/{r['steps']['total']} steps, {r['seconds']} s")
        elif r["status"] == "INCONCLUSIVE":
            lines.append(f"{head}: the browser timed out after {r['seconds']} s: NOT verified, no page defect found; run again when the machine is idle")
        else:
            lines.append(f"{head}: {', '.join(f['code'] + ' ' + f['name'] for f in r['findings'][:3]) or r['message']}"[:200])
    return {"status": status, "widths": rows, "lines": lines, "browser": {"family": browser.family, "version": browser.version}, "message": ""}


def calibrate(
    widths: Sequence[int] = (1024, 1256, 1366, 1576, 1600),
    *,
    height: int = 800,
    browser: Browser | None = None,
    tmp_root: pathlib.Path | str | None = None,
    guess: int | None = None,
    budget_ms: int = 20000,
    timeout: float = 30,
) -> dict[str, Any]:
    """Measure window-width minus innerWidth for a browser at each target width (two launches each:
    one to learn the offset, one to prove that window = target + offset yields exactly target)."""
    browser = browser or find_browser()
    if browser is None:
        raise RuntimeError(no_browser_message())
    guess = guess if guess is not None else 0  # neutral first probe: do not confirm the stored offset by itself
    root = pathlib.Path(tmp_root) if tmp_root is not None else None
    if root is not None:
        root.mkdir(parents=True, exist_ok=True)
    work = pathlib.Path(tempfile.mkdtemp(prefix="atlas-gate-cal-", dir=str(root) if root else None))
    measured: dict[str, int] = {}
    verified: dict[str, bool] = {}
    inner_height: int | None = None
    ua = ""
    try:
        page = work / "cal.html"
        write_text_lf(page, CALIBRATION_PAGE)

        def probe(window_w: int) -> dict[str, Any]:
            profile = work / f"p-{window_w}-{time.perf_counter_ns()}"
            profile.mkdir()
            try:
                launch = _launch(
                    build_args(browser.path, str(profile), (window_w, height), page_url(page), budget_ms),
                    timeout,
                    env=_browser_env(work / f"tmp-{profile.name}"),
                )
            finally:
                _rmtree_retry(profile)
            parsed, bad = extract_result(launch.stdout)
            if parsed is None:
                raise RuntimeError(f"calibration launch failed: {bad}")
            return parsed

        for width in widths:
            first = probe(width + guess)
            offset = width + guess - int(first["innerWidth"])
            second = probe(width + offset)
            measured[str(width)] = offset
            verified[str(width)] = int(second["innerWidth"]) == width
            inner_height = int(second["innerHeight"])
            ua = str(second.get("ua", ""))
    finally:
        _rmtree_retry(work)
    return {
        "family": browser.family,
        "version": browser.version,
        "ua": ua,
        "windowHeight": height,
        "innerHeight": inner_height,
        "measured": measured,
        "verified": verified,
    }


# --------------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------------
def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m gemarc_mcp.atlas.gate.smoke",
        description="Run the Atlas browser gate on one page (exit 0 PASS, 1 FAIL, 3 SKIP: no browser).",
    )
    parser.add_argument("page", nargs="?", help="HTML page to gate (file path)")
    parser.add_argument("--width", type=int, action="append", help="target innerWidth; repeatable (default 1024 1366 1600)")
    parser.add_argument("--height", type=int, default=800, help="window height (not an innerHeight target)")
    parser.add_argument("--timeout", type=float, default=None, help="seconds per launch before the process tree is killed")
    parser.add_argument("--retries", type=int, default=1, help="relaunches after a process-level failure only")
    parser.add_argument("--budget-ms", type=int, default=None, help="--virtual-time-budget")
    parser.add_argument("--browser", default=None, help=f"browser executable (default: discover; env {BROWSER_ENV})")
    parser.add_argument("--tmp-root", default=None, help="parent directory for profiles and assembled pages")
    parser.add_argument(
        "--inline-selftest",
        action="store_true",
        help=f"replace {SELFTEST_MARKER} with selftest.js first (self-contained fixtures only; relative assets are not found)",
    )
    parser.add_argument(
        "--non-bg-floor",
        type=float,
        default=None,
        help="override thresholds.nonBgFloor of calibration.json (a canvas must have nonBgRatio > this); negative = off",
    )
    parser.add_argument(
        "--require-renderer",
        default=None,
        help="override gl.rendererSubstring of calibration.json (WebGL canvases must report it); empty string = off",
    )
    parser.add_argument("--no-header", action="store_true", help="explicit opt-out: the page has no [data-gate=header] element")
    parser.add_argument("--no-text-scan", action="store_true", help="explicit opt-out of the undefined/NaN/[object text scan")
    parser.add_argument("--json", action="store_true", help="print one JSON object per width")
    parser.add_argument("--calibrate", action="store_true", help="measure window offsets for this browser and print JSON")
    return parser.parse_args(argv)


def _tolerant_output() -> None:
    """A page path or finding text the console codec cannot encode (a gbk console and a Japanese or
    emoji folder name) must not abort the CLI after the browser ran and before the verdict printed."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="backslashreplace")
            except Exception:
                pass


def main(argv: Sequence[str] | None = None) -> int:
    _tolerant_output()
    args = _parse_args(argv)
    browser = None
    if args.browser:
        if not os.path.isfile(args.browser):
            print(no_browser_message(pinned=args.browser), file=sys.stderr)
            return 3
        browser = Browser(path=args.browser, family=_family_of(args.browser), version=detect_version(args.browser))
    else:
        browser = find_browser()
    if browser is None:
        print(no_browser_message(), file=sys.stderr)
        return 3
    if args.calibrate:
        widths = args.width or [1024, 1256, 1366, 1576, 1600]
        print(json.dumps(calibrate(widths, height=args.height, browser=browser, tmp_root=args.tmp_root), indent=2, ensure_ascii=False))
        return 0
    if not args.page:
        print("error: a page is required unless --calibrate is given", file=sys.stderr)
        return 2
    widths = args.width or [1024, 1366, 1600]
    worst = 0
    for width in widths:
        res = run_gate(
            args.page,
            width,
            height=args.height,
            browser=browser,
            tmp_root=args.tmp_root,
            timeout=args.timeout,
            retries=args.retries,
            budget_ms=args.budget_ms,
            inline_selftest=args.inline_selftest,
            non_bg_floor=args.non_bg_floor,
            require_renderer=args.require_renderer,
            require_header=not args.no_header,
            scan_text=not args.no_text_scan,
        )
        if args.json:
            print(json.dumps(res.as_dict(), ensure_ascii=False))
        else:
            print(f"{res.status} width={width} window={res.window} t={res.total_seconds:.2f}s attempts={res.attempts}: {res.message}")
            for finding in res.findings:
                print(f"  - {finding}")
        worst = max(worst, {"PASS": 0, "FAIL": 1, "SKIP": 3}[res.status])
    return worst


if __name__ == "__main__":
    sys.exit(main())
