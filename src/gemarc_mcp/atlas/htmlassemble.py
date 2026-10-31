"""Fill the viewer page from the committed bundle (``viewer/dist``) and check the result (design 9.3, BD-001..BD-007).

Nothing here needs Node, the viewer sources or ``tools/bundle_viewer.py``: ``load_viewer`` reads ``dist/`` (the template, the
style sheet, the runtime and the three interface tables) and the gate self-test script, and refuses a ``dist/`` whose files
no longer match the hashes recorded in ``viewer/SOURCES.sha256``.

* ``fill_page(viewer, bundle, ...)`` writes ``index.html``: the six ``<!--ATLAS_*-->`` placeholders are filled in ONE pass with
  a function replacer, so the filled text (which carries author text) is never rescanned and no ``$`` / backslash pattern is
  ever interpreted. The data are ``JSON.parse('...')`` of the bundle (sorted keys, ``<``, ``>``, ``&``, U+2028 and U+2029 written as
  ``\\uXXXX`` escapes, backslash and quote escaped for the single-quoted literal), so an author text can neither end the script
  element nor open a comment nor inject a tag, and a key such as ``__proto__`` is an ordinary own property.
* ``check_page(html, viewer)`` runs the build checks BD-001..BD-007 on the final text (definitions below).
* ``breakdown(html, bundle)`` is the per-part size accounting printed by the build.

Build checks (never shown to the AI; ``ERROR`` fails the build, ``WARN`` is reported):

=======  ==========================================================================================================
BD-001   portability: the CODE parts (template, style sheet, self-test, runtime) contain no ``fetch(``, ``import(``,
         ``@import``, ``url(`` with a remote or protocol-relative address, ``XMLHttpRequest``, ``sendBeacon``, ``importScripts``,
         ``WebSocket``, ``EventSource``, ``eval(``, ``new Function``; no tag other than the expected skeleton, no element with a
         remote ``src`` / ``href`` / ``action``. The DATA are not scanned for words: evidence anchors and names quote code, and
         inert JSON text cannot fetch anything (BD-003 proves it is inert).
BD-002   placeholders: none of the six placeholders is left, none was filled twice, the template had each exactly once.
BD-003   data: the data script holds exactly the ten keys of the bundle contract, parses back to the embedded bundle, and
         contains no ``<``, ``>``, ``&``, U+2028, U+2029 or raw control character.
BD-004   structure: exactly three scripts (self-test first in ``head``, data, runtime), equal to the expected text; the runtime
         ends with the boot call; the title text equals the title; ``lang`` is one of the supported languages; the first element
         of ``head`` is the one Content-Security-Policy ``meta`` with exactly :data:`CSP_POLICY` (no passive load and no fetch-type request:
         only its own inline script and style, and ``data:`` images; no form submit, no ``<base>``; navigation is NOT blocked by a
         policy), and no other ``http-equiv`` meta exists.
BD-005   size: WARN above 3 MiB, ERROR above 8 MiB.
BD-006   encoding: valid UTF-8, LF only, no BOM, no NUL.
BD-007   reproducibility: two fills of the same inputs are byte-identical; no machine path (drive letter, ``/Users/``,
         ``/home/``) and no timestamp-shaped text in the code parts.
=======  ==========================================================================================================

Standard library only.
"""
from __future__ import annotations

import hashlib
import html
import html.parser
import json
import re
from dataclasses import dataclass
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
DIST = PACKAGE / "viewer" / "dist"
SELFTEST_PATH = PACKAGE / "gate" / "selftest.js"
SOURCES_NAME = "SOURCES.sha256"

PLACEHOLDERS = ("<!--ATLAS_LANG-->", "<!--ATLAS_SELFTEST-->", "<!--ATLAS_TITLE-->", "<!--ATLAS_STYLE-->", "<!--ATLAS_DATA-->", "<!--ATLAS_RUNTIME-->")
BUNDLE_KEYS = ("calibrationHooks", "closures", "diagrams", "fragments", "i18n", "meta", "model", "profile", "runs", "schema")
BUNDLE_SCHEMA = "gemarc-atlas-bundle/1"
LANGS = ("zh-CN", "ja-JP", "en-US")
WARN_BYTES = 3 * 1024 * 1024
FAIL_BYTES = 8 * 1024 * 1024
DATA_PREFIX = "window.__ATLAS__ = JSON.parse('"
DATA_SUFFIX = "');"
BOOT_TAIL = "__mod_ui_main.bootAtlas();\n})();"

#: The Content-Security-Policy of every page (a ``meta`` that is the first element of ``head``, before the self-test script). Nothing is
#: fetched, framed, embedded or sent: only the page's own inline script and style run, and ``data:`` images may load. connect-src,
#: font-src, media-src and worker-src fall back to ``default-src 'none'`` because the page uses none of them (checked on built pages in
#: headless Edge by ``tests/atlas/test_gate_csp.py``: zero violations, WebGL tabs included).
CSP_POLICY = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; form-action 'none'; base-uri 'none'"

E_VIEWER = "GEMARC_ATLAS_BUILD_010"


class ViewerError(Exception):
    """The committed viewer bundle is missing, unreadable or stale; ``code`` is a ``GEMARC_ATLAS_BUILD_NNN`` code."""

    def __init__(self, message: str, code: str = E_VIEWER):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Viewer:
    template: str
    css: str
    runtime: str
    i18n: dict
    selftest: str
    hashes: dict   # {"dist/atlas-runtime.js": sha256, ...} as recorded in SOURCES.sha256


def _lf(data: bytes) -> str:
    text = data.decode("utf-8")
    if text.startswith("﻿"):
        text = text[1:]
    return text.replace("\r\n", "\n")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_viewer(dist: "Path | str | None" = None, selftest: "Path | str | None" = None) -> Viewer:
    """The committed viewer bundle. Raises ``ViewerError`` when a file is missing or differs from ``SOURCES.sha256`` (the
    ``dist/`` records only: whether the SOURCES are stale is the job of ``tools/bundle_viewer.py --check`` and its test)."""
    dist = Path(dist) if dist else DIST
    try:
        texts = {name: _lf((dist / name).read_bytes()) for name in ("atlas-runtime.js", "atlas.css", "atlas.template.html", "atlas-i18n.json")}
        recorded_text = _lf((dist.parent / SOURCES_NAME).read_bytes())
        script = _lf(Path(selftest if selftest else SELFTEST_PATH).read_bytes())
    except OSError as ex:
        raise ViewerError(f"the viewer bundle is missing or unreadable ({ex.__class__.__name__}: {ex.filename}); run python tools/bundle_viewer.py") from ex
    hashes = {}
    for line in recorded_text.split("\n"):
        digest, _sep, path = line.partition("  ")
        if re.fullmatch(r"[0-9a-f]{64}", digest) and path.startswith("dist/"):
            hashes[path] = digest
    for name, text in texts.items():
        want = hashes.get(f"dist/{name}")
        if want is None or want != sha256_text(text):
            raise ViewerError(f"dist/{name} does not match {SOURCES_NAME}: the viewer bundle is stale or edited; run python tools/bundle_viewer.py")
    try:
        tables = json.loads(texts["atlas-i18n.json"])
    except ValueError as ex:
        raise ViewerError("dist/atlas-i18n.json is not valid JSON") from ex
    for name, text in (("runtime", texts["atlas-runtime.js"]), ("style sheet", texts["atlas.css"]), ("self-test", script)):
        if re.search(r"</script|<!--|</style", text, re.IGNORECASE):
            raise ViewerError(f"the {name} contains text that could end or confuse its element ('</script', '<!--', '</style')")
    template = texts["atlas.template.html"]
    for marker in PLACEHOLDERS:
        if template.count(marker) != 1:
            raise ViewerError(f"the template must contain {marker} exactly once (found {template.count(marker)})")
    return Viewer(template, texts["atlas.css"], texts["atlas-runtime.js"], tables, script, hashes)


# --------------------------------------------------------------------------------------------- embedding

_SCRIPT_UNSAFE = "<>&  "


def dumps_bundle(value) -> str:
    """Compact JSON, sorted keys, no NaN/infinity, script-unsafe characters as ``\\uXXXX`` (a valid JSON text)."""
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    for raw in _SCRIPT_UNSAFE:
        text = text.replace(raw, "\\u%04x" % ord(raw))
    return text


def js_single_quoted(text: str) -> str:
    """The body of a single-quoted JS string literal whose value is exactly ``text`` (``text`` has no raw newline or U+2028/9)."""
    return text.replace("\\", "\\\\").replace("'", "\\'")


def embed_data(bundle) -> str:
    """The expression that fills ``<!--ATLAS_DATA-->``: ``JSON.parse('<escaped JSON>')``."""
    return "JSON.parse('" + js_single_quoted(dumps_bundle(bundle)) + "')"


def unescape_single_quoted(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            out.append(text[i + 1])
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def extract_data(page: str) -> dict:
    """The bundle embedded in a page, parsed back (raises ``ValueError`` when the data script is not in the expected form)."""
    start = page.find(DATA_PREFIX)
    if start < 0:
        raise ValueError("no data script")
    i = start + len(DATA_PREFIX)
    j = i
    while j < len(page):
        if page[j] == "\\":
            j += 2
            continue
        if page[j] == "'":
            break
        j += 1
    else:
        raise ValueError("unterminated data literal")
    return json.loads(unescape_single_quoted(page[i:j]))


def safe_lang(lang) -> str:
    return lang if lang in LANGS else "en-US"


def safe_title(title: str, fallback: str = "") -> str:
    text = re.sub(r"[\x00-\x1f\x7f  ]+", " ", str(title or "")).strip() or fallback
    return html.escape(text, quote=True)


def fill_page(viewer: Viewer, bundle: dict, *, lang: str = "en-US", title: str = "") -> str:
    """The page text. One pass over the template, function replacer: filled text is never rescanned or pattern-interpreted."""
    values = {
        "<!--ATLAS_LANG-->": safe_lang(lang),
        "<!--ATLAS_SELFTEST-->": "<script>\n" + viewer.selftest + ("" if viewer.selftest.endswith("\n") else "\n") + "</script>",
        "<!--ATLAS_TITLE-->": safe_title(title, "Atlas"),
        "<!--ATLAS_STYLE-->": viewer.css.rstrip("\n"),
        "<!--ATLAS_DATA-->": embed_data(bundle),
        "<!--ATLAS_RUNTIME-->": viewer.runtime.rstrip("\n"),
    }
    pattern = re.compile("|".join(re.escape(m) for m in PLACEHOLDERS))
    return pattern.sub(lambda m: values[m.group(0)], viewer.template)


# --------------------------------------------------------------------------------------------- parsing the final page

class _Skeleton(html.parser.HTMLParser):
    """Collects start tags and the text of script, style and title elements (script and style text is raw data)."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.tags: list = []
        self.scripts: list = []
        self.styles: list = []
        self.title = ""
        self._open: str | None = None
        self._buf: list = []
        self.in_head = False
        self.script_in_head: list = []
        self.comments = 0

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict((k, v or "") for k, v in attrs)))
        if tag == "head":
            self.in_head = True
        if tag in ("script", "style", "title"):
            self._open, self._buf = tag, []

    def handle_endtag(self, tag):
        if tag == "head":
            self.in_head = False
        if tag == self._open:
            text = "".join(self._buf)
            if tag == "script":
                self.scripts.append(text)
                self.script_in_head.append(self.in_head)
            elif tag == "style":
                self.styles.append(text)
            else:
                self.title = text
            self._open = None

    def handle_data(self, data):
        if self._open:
            self._buf.append(data)

    def handle_entityref(self, name):
        if self._open == "title":
            self._buf.append(f"&{name};")

    def handle_charref(self, name):
        if self._open == "title":
            self._buf.append(f"&#{name};")

    def handle_comment(self, data):
        self.comments += 1


def parse_skeleton(page: str) -> _Skeleton:
    parser = _Skeleton()
    parser.feed(page)
    parser.close()
    return parser


_ALLOWED_TAGS = frozenset({"html", "head", "meta", "script", "title", "style", "body", "div"})
_REMOTE = re.compile(r"^\s*(?:[a-z][a-z0-9+.-]*:|//)", re.IGNORECASE)
_URL_REMOTE = re.compile(r"url\(\s*['\"]?\s*(?:[a-z][a-z0-9+.-]*:|//)", re.IGNORECASE)
_CODE_PATTERNS = (
    ("fetch(", re.compile(r"\bfetch\s*\(")), ("import(", re.compile(r"\bimport\s*\(")), ("@import", re.compile(r"@import", re.IGNORECASE)),
    ("XMLHttpRequest", re.compile(r"XMLHttpRequest")), ("sendBeacon", re.compile(r"sendBeacon")), ("importScripts", re.compile(r"importScripts")),
    ("WebSocket", re.compile(r"WebSocket")), ("EventSource", re.compile(r"EventSource")), ("eval(", re.compile(r"\beval\s*\(")),
    ("new Function", re.compile(r"new\s+Function\b")),
    # Forms that are not a bare call of a known name. BD-001 is a token scan, not a proof: an obfuscated form (a computed property name, a
    # string built at run time) passes it; the page's offline behaviour is what the browser gate would show.
    ("Function(", re.compile(r"\bFunction\s*\(")), ("window.fetch", re.compile(r"\bwindow\s*\.\s*fetch\b")),
    ("document.write", re.compile(r"document\s*\.\s*write")), ("new Image", re.compile(r"new\s+Image\b")),
    ("setTimeout(string)", re.compile(r"\bset(?:Timeout|Interval)\s*\(\s*[\"'`]")),
)
_MACHINE = re.compile(r"[A-Za-z]:\\|[A-Za-z]:/(?:Users|Windows|Program)|/Users/|/home/[a-z]|\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")


def _issue(rule: str, severity: str, message: str) -> dict:
    return {"rule": rule, "severity": severity, "message": message}


def _csp_issues(skeleton: "_Skeleton") -> list:
    """BD-004, the policy part: the first element of ``head`` is the one policy ``meta`` with exactly :data:`CSP_POLICY`, and no other
    ``http-equiv`` meta (refresh, a second or a weaker policy) exists. Fail closed: a missing, moved, changed or doubled policy is an ERROR."""
    tags = skeleton.tags
    equiv = [attrs for tag, attrs in tags if tag == "meta" and any(key.lower() == "http-equiv" for key in attrs)]
    head = next((i for i, (tag, _attrs) in enumerate(tags) if tag == "head"), None)
    first = tags[head + 1] if head is not None and head + 1 < len(tags) else None
    problems = []
    if not (first and first[0] == "meta" and first[1].get("http-equiv", "").lower() == "content-security-policy"):
        problems.append("the first element of head is not the Content-Security-Policy meta")
    elif first[1].get("content") != CSP_POLICY or set(first[1]) != {"http-equiv", "content"}:
        problems.append("the Content-Security-Policy meta is not exactly the pinned policy")
    if len(equiv) != 1:
        problems.append(f"the page has {len(equiv)} http-equiv meta elements, expected exactly the one policy")
    return [_issue("BD-004", "ERROR", text) for text in problems]


def code_parts(viewer: Viewer) -> dict:
    """The parts that are code, not data: what BD-001 and BD-007 scan."""
    return {"template": viewer.template, "style": viewer.css, "selftest": viewer.selftest, "runtime": viewer.runtime}


def check_page(page: "str | bytes", viewer: Viewer, bundle: "dict | None" = None, *, lang: str = "en-US", title: str = "") -> list:
    """Run BD-001..BD-007 on the final page. ``bundle`` (the input) enables the data round-trip and the reproducibility check."""
    issues: list = []
    raw = page if isinstance(page, bytes) else page.encode("utf-8")
    # BD-006 encoding
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return [_issue("BD-006", "ERROR", "the page is not valid UTF-8")]
    if raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw or b"\x00" in raw:
        issues.append(_issue("BD-006", "ERROR", "the page has a BOM, a carriage return or a NUL byte (UTF-8, LF only)"))
    # BD-005 size
    if len(raw) > FAIL_BYTES:
        issues.append(_issue("BD-005", "ERROR", f"the page is {len(raw)} bytes, above the {FAIL_BYTES} byte limit"))
    elif len(raw) > WARN_BYTES:
        issues.append(_issue("BD-005", "WARN", f"the page is {len(raw)} bytes, above the {WARN_BYTES} byte warning line"))
    # BD-002 placeholders
    if "<!--ATLAS_" in text:  # the data holds no '<' (BD-003), so any remaining marker text is a leftover
        issues.append(_issue("BD-002", "ERROR", "a placeholder is left in the page"))
    for marker in PLACEHOLDERS:
        if viewer.template.count(marker) != 1:
            issues.append(_issue("BD-002", "ERROR", f"the template has {marker} {viewer.template.count(marker)} times"))
    # structure
    skeleton = parse_skeleton(text)
    # BD-001 portability: code parts by pattern, tags by name and attribute
    for part, body in code_parts(viewer).items():
        scan = body.replace("<!--ATLAS_", "") if part == "template" else body
        for name, pattern in _CODE_PATTERNS:
            if pattern.search(scan):
                issues.append(_issue("BD-001", "ERROR", f"the {part} contains {name}"))
        if _URL_REMOTE.search(scan):
            issues.append(_issue("BD-001", "ERROR", f"the {part} contains a remote url()"))
    for tag, attrs in skeleton.tags:
        if tag not in _ALLOWED_TAGS:
            issues.append(_issue("BD-001", "ERROR", f"unexpected element <{tag}> in the page"))
        for key, value in attrs.items():
            if key in ("src", "href", "action", "poster", "srcset", "data", "formaction") or (tag == "meta" and key == "content" and "url=" in value.lower()):
                issues.append(_issue("BD-001", "ERROR", f"<{tag}> has a {key} attribute: the page must reference nothing"))
            elif _REMOTE.match(value) and key not in ("lang", "charset", "name", "content", "id", "class", "data-tab", "data-layout"):
                issues.append(_issue("BD-001", "ERROR", f"<{tag} {key}> holds an address"))
    for style_text in skeleton.styles:
        if _URL_REMOTE.search(style_text) or re.search(r"@import", style_text, re.IGNORECASE):
            issues.append(_issue("BD-001", "ERROR", "the page style has a remote reference"))
    # BD-004 structure
    issues.extend(_csp_issues(skeleton))
    expected_self = viewer.selftest + ("" if viewer.selftest.endswith("\n") else "\n")
    if len(skeleton.scripts) != 3:
        issues.append(_issue("BD-004", "ERROR", f"the page has {len(skeleton.scripts)} script elements, expected 3 (self-test, data, runtime)"))
    else:
        selftest_text, data_text, runtime_text = skeleton.scripts
        if selftest_text != "\n" + expected_self or not skeleton.script_in_head[0] or skeleton.script_in_head[1:] != [False, False]:
            issues.append(_issue("BD-004", "ERROR", "the first script is not the gate self-test at the top of head, or another script sits in head"))
        if not data_text.strip().startswith(DATA_PREFIX) or not data_text.rstrip().endswith(DATA_SUFFIX):
            issues.append(_issue("BD-004", "ERROR", "the second script is not the data script"))
        if runtime_text.strip() != viewer.runtime.strip() or not runtime_text.rstrip().endswith(BOOT_TAIL):
            issues.append(_issue("BD-004", "ERROR", "the third script is not the viewer runtime ending in the boot call"))
        # BD-003 data
        body = data_text.strip()[len(DATA_PREFIX): -len(DATA_SUFFIX)] if data_text.strip().startswith(DATA_PREFIX) and data_text.strip().endswith(DATA_SUFFIX) else ""
        if any(ch in body for ch in "<>&  ") or re.search(r"[\x00-\x1f\x7f]", body):
            issues.append(_issue("BD-003", "ERROR", "the data contain a character that is not script-safe (< > & U+2028 U+2029 or a control character)"))
        try:
            data = extract_data(text)
        except ValueError as ex:
            issues.append(_issue("BD-003", "ERROR", f"the data script does not parse: {ex}"))
        else:
            if tuple(sorted(data)) != BUNDLE_KEYS:
                issues.append(_issue("BD-003", "ERROR", f"the bundle keys are {sorted(data)}, expected exactly {list(BUNDLE_KEYS)}"))
            if data.get("schema") != BUNDLE_SCHEMA:
                issues.append(_issue("BD-003", "ERROR", f"bundle schema is {data.get('schema')!r}"))
            if bundle is not None and data != json.loads(json.dumps(bundle, allow_nan=False)):
                issues.append(_issue("BD-003", "ERROR", "the embedded data do not equal the input bundle"))
    if not any(t == "html" and a.get("lang") in LANGS for t, a in skeleton.tags):
        issues.append(_issue("BD-004", "ERROR", "the html element has no supported lang"))
    expect_title = safe_title(title, "Atlas")
    if bundle is not None and html.unescape(skeleton.title) != html.unescape(expect_title):
        issues.append(_issue("BD-004", "ERROR", "the title text differs from the requested title"))
    # BD-007 reproducibility
    for part, body in code_parts(viewer).items():
        if _MACHINE.search(body):
            issues.append(_issue("BD-007", "ERROR", f"the {part} holds a machine path or a timestamp"))
    if bundle is not None and fill_page(viewer, bundle, lang=lang, title=title) != text:
        issues.append(_issue("BD-007", "ERROR", "a second fill of the same inputs gives a different page"))
    return issues


def errors_of(issues: list) -> list:
    return [i for i in issues if i["severity"] == "ERROR"]


# --------------------------------------------------------------------------------------------- size accounting

def breakdown(page: "str | bytes", bundle: dict, viewer: Viewer) -> dict:
    """``{"total", "parts": [(name, bytes)], "data": [(key, bytes)]}`` (largest first). ``data`` sizes are the JSON of each key
    on its own; ``data-escape`` is what escaping for the string literal and the wrapper add. ``parts["data"]`` is the whole embedded data
    script; ``skeleton`` is the REMAINDER (total minus the other parts: the template text), so the parts add up to the total by construction."""
    raw = page if isinstance(page, bytes) else page.encode("utf-8")
    sizes = {key: len(dumps_bundle(value).encode("utf-8")) for key, value in bundle.items()}
    embedded = len(embed_data(bundle).encode("utf-8"))
    other = max(0, embedded - sum(sizes.values()))
    parts = {
        "runtime": len(viewer.runtime.encode("utf-8")), "self-test": len(viewer.selftest.encode("utf-8")), "style": len(viewer.css.encode("utf-8")),
        "data": embedded, "skeleton": max(0, len(raw) - embedded - len(viewer.runtime.encode("utf-8")) - len(viewer.selftest.encode("utf-8")) - len(viewer.css.encode("utf-8"))),
    }
    data = dict(sizes)
    data["data-escape"] = other
    return {"total": len(raw), "parts": sorted(parts.items(), key=lambda kv: (-kv[1], kv[0])), "data": sorted(data.items(), key=lambda kv: (-kv[1], kv[0]))}


def format_breakdown(report: dict) -> str:
    lines = [f"page {report['total']} bytes"]
    lines += [f"  {name:<12} {size:>9}" for name, size in report["parts"]]
    lines.append("  data by key")
    lines += [f"    {name:<18} {size:>9}" for name, size in report["data"]]
    return "\n".join(lines)
