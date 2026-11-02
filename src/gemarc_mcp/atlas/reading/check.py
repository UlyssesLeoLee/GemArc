"""The build-time checks of the reading surface, RS-101 ... RS-133 (design A3 section 6.0 and 6.1).

Classes (design D-18): ``I`` integrity (only a generator bug can break it; an ERROR makes the tree invalid), ``L`` input limit (never fails
a build: the renderer degraded and the finding says where, for the ``repair`` line of the ``build`` reply), ``T`` test only (never run by a
build; ``forbidden_hits`` and the determinism and tool checks live in the tests). A check takes the written files, the manifest rows and the
bundle: it reads the finished text, never the planner's memory, so a test can edit a written tree and must see exactly the check of
the edited part turn red.

Milestone R1 implements RS-101..107, 109, 111..113, 116, 117, 120..125, 128, 129, 131, 132; milestone R2 adds the HTML twins: RS-110 and
RS-114, the twin halves of RS-104, RS-106 and RS-109, the size rule of a twin in RS-132, and the gallery exceptions of design D-2 (the twin of a
screen card is the gallery page of lane U: its style, its ``<tr id>`` rows, its CSP text and its inlined SVG lines are not held to the rules of
a twin the reading surface renders; what it must still satisfy is spelled out in ``rs110`` and ``rs114``). Not applicable before their
milestone (each is a registered no-op with the reason): none is left; milestone R3 adds RS-108 (verdict lines), RS-115 (no result outside the ledger)
and RS-127 (diagram headers).
RS-118/119/126/130/133 are test-class checks outside ``run``.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import Iterable

from ..derive import i18n as T
from ..derive import policy as P
from . import blocks as B
from . import names as N
from . import render_html as RH
from . import results as RES
from ..derive import diagram_header as DH
from .budgets import BUDGETS, envelope_cap
from .envelope import bucket_of, first_line_regex, sentinel_regex, verify_sentinel
from .families import card_label
from .model import Finding
from .parse import headings, parse_page, tables, unfold_lines
from .source import CODE, EXCLUDED_GROUP, STRUCTURAL, Source
from .text import pattern, raw, tx, tx_list

CHECKS = {
    "RS-101": ("I", "page size and class"), "RS-102": ("I", "line length"), "RS-103": ("I", "frame, scope card and envelope size"),
    "RS-104": ("I", "sentinel"), "RS-105": ("I", "one stamp"), "RS-106": ("I", "links"), "RS-107": ("I", "table captions and counts"),
    "RS-108": ("I", "verdict lines"), "RS-109": ("I", "manifest and tree"), "RS-110": ("I", "twin hygiene"),
    "RS-111": ("I", "continuation parts"), "RS-112": ("L", "page size distribution"), "RS-113": ("I", "one set of numbers"),
    "RS-114": ("I", "twin equals page"), "RS-115": ("I", "no result outside the ledger"), "RS-116": ("I", "one home per id"),
    "RS-117": ("I", "file names"), "RS-118": ("T", "forbidden words"), "RS-119": ("T", "determinism"), "RS-120": ("I", "inbound connections"),
    "RS-121": ("I", "reachability"), "RS-122": ("I", "scope card counts"), "RS-123": ("I", "legends and grade ruler"), "RS-124": ("I", "evidence columns"),
    "RS-125": ("I", "hand-over files"), "RS-126": ("T", "index.html unchanged"), "RS-127": ("I", "diagram headers"), "RS-128": ("I", "conservation"),
    "RS-129": ("I", "START dimension table"), "RS-130": ("T", "doc tool paging"), "RS-131": ("I", "refs.tsv"), "RS-132": ("L", "degradation buckets"),
    "RS-133": ("T", "stamp tools"),
}
NOT_APPLICABLE: dict = {}
ACTIVE_I = tuple(i for i, (k, _t) in sorted(CHECKS.items()) if k == "I" and i not in NOT_APPLICABLE)
GENERIC_LINK_TEXT = frozenset(w.lower() for _lang in ("zh-CN", "en-US") for w in tx_list(_lang, "link.generic"))
LINK = re.compile(r"(?<!\\)\[([^\]]*)(?<!\\)\]\(([^)\s]*)\)")
SECTION = N.SECTION_SIGN
PAGE_TOKEN = re.compile(SECTION + "(" + N.PAGE_ID_BODY + ")")
#: ``site/screens/<enc>.html`` that is not the twin of the screens INDEX or NAV: the gallery page of lane U, which is the twin of a screen card (D-2)
GALLERY = re.compile(r"^site/screens/(?!(?:INDEX|NAV)(?:-\d+)?\.html$)[^/]+\.html$")
PART_SUFFIX = re.compile(r"-\d+\.md$")


def is_gallery(path: str) -> bool:
    return GALLERY.match(path) is not None


def first_part(paths) -> str:
    """The Markdown file that stands for a twin: the first part of a screen card (``<enc>.card.md`` before ``<enc>.card-2.md``)."""
    return min(paths, key=lambda p: (bool(PART_SUFFIX.search(p)), p))


def twin_regexes(lang: str) -> tuple:
    """``(first line, sentinel)`` of a twin, from the templates ``read.twin.*`` (the check writes no sentence of its own)."""
    first = re.compile("^" + pattern(lang, "twin.head", page=r"[^\s·]+", title=r".+?", i=r"\d+", n=r"\d+", counts=r".+?", cls=r"card|page") + "$")
    last = re.compile("^" + pattern(lang, "twin.sentinel", page=r"[^\s·]+", counts=r".+?", sha8=r"[0-9a-f]{8}") + "$")
    return first, last


@dataclass
class Surface:
    """What a check reads: the written files, the manifest rows of the reading build, the header facts, the files other code writes."""

    files: dict                                  # path -> bytes
    records: list                                # manifest rows (dicts) of the files of the reading build
    header: dict = field(default_factory=dict)
    extra_paths: set = field(default_factory=set)    # paths that exist in dist but are not in ``files`` (diagrams, docs, index.html ...)
    exclusions: list = field(default_factory=list)
    build_info: dict | None = None
    manifest_rows: list | None = None            # the rows of a manifest.jsonl read from disk (dist-level checks)
    manifest_header: dict | None = None
    strangers: list = field(default_factory=list)

    def text(self, path: str) -> str:
        return self.files[path].decode("utf-8")


class Ctx:
    def __init__(self, src: Source, surface: Surface):
        self.src = src
        self.s = surface
        self.lang = src.lang
        self.pages: dict = {}
        self.unparsed: list = []
        for path in sorted(surface.files):
            if path.endswith(".md") and (path.startswith("read/") or path == "START.md"):
                parsed = parse_page(self.lang, path, surface.text(path))
                if parsed is None:
                    self.unparsed.append(path)
                else:
                    self.pages[path] = parsed
        self.records = {r["p"]: r for r in surface.records}
        self.findings: list = []
        self._md: dict | None = None
        self._twins: dict | None = None
        self._analysis: dict = {}

    def add(self, rule: str, path: str, message: str, hint: str = "", severity: str | None = None, klass: str | None = None) -> None:
        """A finding. ``klass`` overrides the class of the rule for the parts of a mixed rule that are L (RS-101 for the files that are not planned,
        RS-117 part b): they never fail a build and appear in ``ReadResult.degraded``."""
        klass = klass or CHECKS[rule][0]
        sev = severity or ("ERROR" if klass == "I" else "WARN")
        self.findings.append(Finding(rule, klass, sev, path, message, hint))

    def md_texts(self) -> dict:
        """``{path: text}`` of the Markdown files of the surface that have an HTML twin by D-2 (pages, START, screens INDEX, NAV and cards)."""
        if self._md is None:
            self._md = {path: self.s.text(path) for path in sorted(self.s.files) if path.endswith(".md") and N.twin_path(path) is not None}
        return self._md

    def twin_files(self) -> dict:
        """``{path: bytes}`` of every ``site/**.html`` of the surface."""
        if self._twins is None:
            self._twins = {path: data for path, data in sorted(self.s.files.items()) if path.startswith("site/") and path.endswith(".html")}
        return self._twins

    def analysis(self, twin: str):
        """The structural reading of a twin (``render_html.analyse``), made once for all the checks."""
        if twin not in self._analysis:
            self._analysis[twin] = RH.analyse(self.twin_files()[twin])
        return self._analysis[twin]

    def html_on(self) -> bool:
        """Twins exist (some ``site/`` file or some manifest row names a twin): with ``html = false`` neither does, and the twin rules do not apply."""
        return bool(self.twin_files()) or any(rec.get("twin") is not None for rec in self.records.values())

    def pairs(self) -> dict:
        """``{twin path: [Markdown paths]}``: the Markdown pages a twin stands for (several parts of one screen card share a gallery)."""
        out: dict = {}
        for path in self.md_texts():
            out.setdefault(N.twin_path(path), []).append(path)
        return out

    def family_pages(self) -> list:
        """Parsed pages of the families of facts (not START, G01, INDEX), in path order."""
        return [p for path, p in sorted(self.pages.items()) if path.startswith("read/") and not re.search(r"/(INDEX[^/]*|G01)\.md$", path)]


# ---------------------------------------------------------------------------------------------------- RS-128 pre-step

def _cells(row: str) -> list:
    return B.split_cells(row)


def ifc_exclusions(src: Source) -> tuple:
    """``(exclusions, findings)``: the groups the pages do not print, each with the reason and the proof. ``ifc`` rows are left out of the pages
    only when every cell of every interface row is a cell of the connection row of the same id (``rebuildable-from:e.DD``); a row that is not
    makes RS-128 ERROR (nothing else prints it)."""
    rows = src.by_group(EXCLUDED_GROUP)
    if not rows:
        return [], []
    dd = {h.key: set(_cells(h.md)) for h in src.by_group("e.DD")}
    bad = []
    for h in rows:
        cells = [c for c in _cells(h.md) if c]
        if h.key not in dd or not set(cells) <= dd[h.key]:
            bad.append(h.key)
    if bad:
        return [], [Finding("RS-128", "I", "ERROR", "", f"{len(bad)} interface rows are not rebuildable from the connection table (first: {bad[0]!r}); nothing prints them")]
    return [("ifc", len(rows), "rebuildable-from:e.DD")], []


# ---------------------------------------------------------------------------------------------------- the checks

def rs101(ctx: Ctx) -> None:
    caps = BUDGETS["cap_md"]
    for path, page in ctx.pages.items():
        rec = ctx.records.get(path)
        size = len(ctx.s.files[path])
        planner = path.startswith("read/")
        if planner and page.cls in caps and size > caps[page.cls]:
            ctx.add("RS-101", path, f"{size} bytes, the class {page.cls} allows {caps[page.cls]}")
        if planner and rec is not None and rec.get("cls") != page.cls:
            ctx.add("RS-101", path, f"the page says class {page.cls}, the manifest {rec.get('cls')}")
        if not planner and page.cls in caps and size > caps[page.cls]:
            ctx.add("RS-101", path, f"{size} bytes above the card cap (degraded: the client hint moved to the glossary)", "shorten the long text of the elements this page shows", klass="L")


def rs102(ctx: Ctx) -> None:
    limit, tsv = BUDGETS["line_chars"], BUDGETS["tsv_line_chars"]
    for path, data in sorted(ctx.s.files.items()):
        if not (path.endswith((".md", ".tsv", ".json", ".jsonl", ".html"))):
            continue
        rec = ctx.records.get(path)
        if rec is not None and not rec.get("ai", True) and rec.get("cls") == "handoff":
            continue
        cap = tsv if path.endswith(".tsv") else limit
        for n, line in enumerate(data.decode("utf-8").split("\n"), start=1):
            if len(line) > cap:
                if is_gallery(path):         # an inlined SVG line must stay verbatim (SV-213): a degradation, never a failure
                    ctx.add("RS-102", path, f"line {n} has {len(line)} characters (limit {cap}): inlined SVG text", klass="L")
                else:
                    ctx.add("RS-102", path, f"line {n} has {len(line)} characters (limit {cap})")
                break
    if ctx.s.manifest_rows is not None:
        pass


def rs103(ctx: Ctx) -> None:
    for path in ctx.unparsed:
        ctx.add("RS-103", path, "the frame of the page cannot be read back (locator lines or scope card)")
    for path, page in ctx.pages.items():
        rec = ctx.records.get(path)
        if rec is not None:
            if rec.get("id") != N.page_ref(page.pid):
                ctx.add("RS-103", path, f"the page id {page.pid} differs from the manifest id {rec.get('id')}")
            if (rec.get("i"), rec.get("of")) != (page.index, page.count):
                ctx.add("RS-103", path, f"{page.index}/{page.count} differs from the manifest {rec.get('i')}/{rec.get('of')}")
        info = N.parse_page_id(page.pid)
        if info is None:
            ctx.add("RS-103", path, "the page id does not follow the grammar")
            continue
        family = info.family
        cont = page.continuation is not None
        limit = envelope_cap(bucket_of(family, cont), ctx.lang)
        if page.envelope_bytes > limit:
            ctx.add("RS-103", path, f"the frame takes {page.envelope_bytes} bytes (limit {limit})")


def rs104(ctx: Ctx) -> None:
    for path in ctx.pages:
        problems = verify_sentinel(ctx.s.text(path))
        if problems:
            ctx.add("RS-104", path, problems[0])
    if not ctx.html_on():
        return
    lang = ctx.lang
    first_re, last_re = twin_regexes(lang)
    pairs = ctx.pairs()
    for tp, data in sorted(ctx.twin_files().items()):
        mds = pairs.get(tp)
        if not mds:
            continue                                  # a twin of no page is RS-109's
        md = first_part(mds)
        lines = ctx.md_texts()[md].rstrip("\n").split("\n")
        md_first, md_last = first_line_regex(lang).match(lines[0]), sentinel_regex(lang).match(lines[-1])
        if md_first is None or md_last is None:
            continue                                  # the page's own frame is RS-103 and the md half of this rule
        a = ctx.analysis(tp)
        if is_gallery(tp):                            # lane U words the counts of a gallery itself: only the seal is ours
            found = re.search(r"sha8 ([0-9a-f]{8})", a.footer_last)
            if found is None or found.group(1) != md_last.group("sha8"):
                ctx.add("RS-104", tp, "the sentinel of the gallery does not carry the sha8 of the card it mirrors")
            continue
        f, l = first_re.match(a.header_first), last_re.match(a.footer_last)
        if f is None:
            ctx.add("RS-104", tp, "the first line of the twin is not the twin locator line")
            continue
        if l is None:
            ctx.add("RS-104", tp, "the last line of the twin is not a twin sentinel")
            continue
        counts = RH.counts_of(lang, a.sections, a.tables)
        for what, text in (("first line", f.group("counts")), ("sentinel", l.group("counts"))):
            if text != counts:
                ctx.add("RS-104", tp, f"the {what} counts {text!r}, the twin has {counts!r}")
        if l.group("sha8") != md_last.group("sha8"):
            ctx.add("RS-104", tp, "the sha8 of the sentinel is not the sha8 of the page it mirrors")
        if l.group("page") != md_last.group("page"):
            ctx.add("RS-104", tp, "the page id of the sentinel differs from the page")
        if (f.group("page"), f.group("i"), f.group("n"), f.group("cls")) != (md_first.group("page"), md_first.group("i"), md_first.group("n"), md_first.group("cls")):
            ctx.add("RS-104", tp, "the page id, the part number or the class of the first line differs from the page")


def rs105(ctx: Ctx) -> None:
    stamp = ctx.src.stamp
    wanted = {stamp}
    if ctx.s.header.get("m"):
        wanted.add(ctx.s.header["m"])
    if ctx.s.manifest_header and ctx.s.manifest_header.get("m"):
        wanted.add(ctx.s.manifest_header["m"])
    if ctx.s.build_info and ctx.s.build_info.get("modelSha8"):
        wanted.add(ctx.s.build_info["modelSha8"])
    if len(wanted) > 1:
        ctx.add("RS-105", "", f"the stamps of the build disagree: {sorted(wanted)}")
    for path, page in ctx.pages.items():
        if page.stamps.get("m") != stamp:
            ctx.add("RS-105", path, f"the page stamp m={page.stamps.get('m')} differs from the model stamp {stamp}")
    for path, rec in ctx.records.items():
        if rec.get("m") not in (None, stamp):
            ctx.add("RS-105", path, f"the manifest row says m={rec.get('m')}")


def _known_paths(ctx: Ctx) -> set:
    return set(ctx.s.files) | set(ctx.s.extra_paths)


def rs106(ctx: Ctx) -> None:
    rs106_twins(ctx)
    known = _known_paths(ctx)
    page_ids = {p.pid for p in ctx.pages.values()}
    for path, page in sorted(ctx.pages.items()):
        base = path.rsplit("/", 1)[0] if "/" in path else ""
        for n, line in enumerate(page.body, start=1):
            stripped = CODE.sub("", line)
            for text, href in LINK.findall(stripped):
                if href.startswith("dg:"):
                    ctx.add("RS-106", path, "a dg: link", "")
                    continue
                if not text.strip() or text.strip().lower() in GENERIC_LINK_TEXT:
                    ctx.add("RS-106", path, f"the link {href!r} has the text {text!r}")
                if "://" in href or href.startswith("#"):
                    continue
                parts = [] if not base else base.split("/")
                for piece in href.split("/"):
                    if piece == "..":
                        if parts:
                            parts.pop()
                    elif piece not in ("", "."):
                        parts.append(piece)
                target = "/".join(parts)
                if target not in known:
                    ctx.add("RS-106", path, f"the link {href!r} does not resolve to a file of dist")
            if path == "START.md" or re.search(r"/(INDEX[^/]*|G01)\.md$", path):      # generated text: every page reference is one of ours
                for token in PAGE_TOKEN.findall(stripped):
                    if token not in page_ids and token not in ("START", "G01"):
                        ctx.add("RS-106", path, f"the page reference {SECTION}{token} names no page (line {n})")
        page_columns = {T.md_text(tx_list_one(ctx.lang, key)) for key in ("th.inbound", "th.elements", "th.rooms")}
        for _s, header, rows, _e in tables(page.body):
            if B.split_cells(header[0])[-1] in page_columns:
                for row in rows:
                    for token in PAGE_TOKEN.findall(B.split_cells(row)[-1]):
                        if token not in page_ids:
                            ctx.add("RS-106", path, f"the page reference {SECTION}{token} names no page")
        for link in page.neighbours:
            for text, href in LINK.findall(link):
                parts = [] if not base else base.split("/")
                for piece in href.split("/"):
                    if piece == "..":
                        if parts:
                            parts.pop()
                    elif piece not in ("", "."):
                        parts.append(piece)
                if "/".join(parts) not in known:
                    ctx.add("RS-106", path, f"the neighbour link {href!r} does not resolve")


def rs106_twins(ctx: Ctx) -> None:
    """The addresses of every twin resolve to a file of the surface; a twin the reading surface renders points to the twin of a page that has
    one, never to the Markdown (a gallery may link to its card: that is the alternate form of the same page)."""
    known = _known_paths(ctx)
    for tp, data in sorted(ctx.twin_files().items()):
        a = ctx.analysis(tp)
        base = posixpath.dirname(tp)
        for kind, hrefs in (("a", a.hrefs), ("link", [d.get("href", "") for d in a.links])):
            for href in hrefs:
                if not href or "://" in href or href.startswith("#"):
                    continue
                target = posixpath.normpath(posixpath.join(base, href.split("#")[0]))
                if target.startswith("..") or target not in known:
                    ctx.add("RS-106", tp, f"the address {href!r} does not resolve to a file of dist")
                elif kind == "a" and not is_gallery(tp) and target.endswith(".md") and N.twin_path(target) is not None:
                    ctx.add("RS-106", tp, f"the address {href!r} names a Markdown page that has a twin: link to the twin")


def rs107(ctx: Ctx) -> None:
    for path, page in ctx.pages.items():
        body = page.body
        heads = {h[0]: h for h in headings(ctx.lang, body)}
        for start, _header, rows, _end in tables(body):
            h = heads.get(start - 1)
            if h is None:
                # a table inside a block fragment (a case with its events) belongs to the heading of its block
                above = next((i for i in range(start - 1, -1, -1) if body[i].startswith("#")), None)
                if above is not None and body[above].startswith("### ") and above + 1 < len(body) and CODE.match(body[above + 1]):
                    continue
                ctx.add("RS-107", path, f"the table at line {start + 1} has no counted heading above it")
                continue
            _i, _level, _label, n, total, a, b = h
            if total is None and n != len(rows):
                ctx.add("RS-107", path, f"the heading says {n} rows, the table has {len(rows)}")
            if total is not None and not (n == len(rows) and b - a + 1 == n and n <= total):
                ctx.add("RS-107", path, f"the heading '{n} / {total}, rows {a}-{b}' does not fit the {len(rows)} rows")


def rs109_twins(ctx: Ctx) -> None:
    """With twins on, every Markdown page that has a twin by D-2 has exactly one, the manifest rows say so both ways, and no twin stands alone;
    with ``html = false`` there is no ``site/`` and no row names a twin (``html_on`` is then False and nothing here applies)."""
    if not ctx.html_on():
        return
    pairs, twins = ctx.pairs(), ctx.twin_files()
    for tp, mds in sorted(pairs.items()):
        if tp not in twins:
            for md in mds:
                ctx.add("RS-109", md, f"the page has no HTML twin ({tp}) although twins are written")
    for md in ctx.md_texts():
        rec = ctx.records.get(md)
        if rec is not None and rec.get("twin") != N.twin_path(md):
            ctx.add("RS-109", md, f"the manifest row names the twin {rec.get('twin')!r}, the rule says {N.twin_path(md)!r}")
    for tp in sorted(twins):
        if tp not in pairs:
            ctx.add("RS-109", tp, "a twin of no page")
        rec = ctx.records.get(tp)
        if rec is None:
            continue
        if rec.get("cls") != "twin" or rec.get("ai") is not False:
            ctx.add("RS-109", tp, "the manifest row of a twin is class twin and ai false")
        if tp in pairs and rec.get("twinOf") not in pairs[tp]:
            ctx.add("RS-109", tp, f"the manifest row says twinOf {rec.get('twinOf')!r}")


def rs109(ctx: Ctx) -> None:
    s = ctx.s
    for path, rec in ctx.records.items():
        if rec.get("cls") == "handoff" and rec.get("k") == "sheet":
            continue
        if path not in s.files and path not in s.extra_paths:
            ctx.add("RS-109", path, "the manifest names a file that is not there")
            continue
        if path in s.files:
            data = s.files[path]
            import hashlib

            if rec.get("b") != len(data) or rec.get("sha8") != hashlib.sha256(data).hexdigest()[:8]:
                ctx.add("RS-109", path, "bytes or sha8 of the manifest row differ from the file")
            elif rec.get("l") != data.count(b"\n") + (0 if data.endswith(b"\n") or not data else 1):
                ctx.add("RS-109", path, "the line count of the manifest row differs from the file")
        if rec.get("twin") is not None and rec.get("cls") not in ("page", "card"):
            ctx.add("RS-109", path, "only a page or a card can have a twin")
        if rec.get("k") in ("doc", "prompt", "sheet", "diagram") and rec.get("twin") is not None:
            ctx.add("RS-109", path, "a hand-over file or a diagram has no twin")
    for stranger in s.strangers:
        ctx.add("RS-109", stranger, "a file of dist that the manifest does not register", severity="WARN")
    rs109_twins(ctx)
    # every dimension INDEX lists its pages once, each shard within the card cap
    listed: dict = {}
    for path, page in ctx.pages.items():
        info = N.parse_page_id(page.pid)
        if info is None or info.family != "INDEX":
            continue
        if len(s.files[path]) > BUDGETS["cap_md"]["card"]:
            ctx.add("RS-109", path, "an INDEX shard is larger than a card")
        for line in page.body:
            m = re.match(r"^\| \[" + SECTION + r"([A-Z][A-Za-z0-9-]*)\]", line)
            if m:
                listed.setdefault((info.dim, m.group(1)), []).append(path)
    expected: dict = {}
    for path, rec in ctx.records.items():
        if rec.get("k") == "page" and rec.get("dim") and rec.get("id", "").startswith(SECTION):
            expected.setdefault(rec["dim"], []).append(rec["id"][len(SECTION):])
    for dim, pages in expected.items():
        have_index = any(key[0] == dim for key in listed) or any(rec.get("fam") == "INDEX" and rec.get("dim") == dim for rec in ctx.records.values())
        if not have_index:
            if len(pages) >= 4:
                ctx.add("RS-109", "", f"the dimension {dim} has {len(pages)} pages and no INDEX")
            continue
        for pid in pages:
            n = len(listed.get((dim, pid), []))
            if n != 1:
                ctx.add("RS-109", f"read/{dim}/INDEX.md", f"the page {pid} is listed {n} times")
    if s.manifest_header is not None:
        h = s.manifest_header
        rows = s.manifest_rows or []
        if h.get("files") != len(rows):
            ctx.add("RS-109", "manifest.jsonl", f"the header counts {h.get('files')} files, the manifest has {len(rows)} rows")
        if sum(h.get("classes", {}).values()) != h.get("files"):
            ctx.add("RS-109", "manifest.jsonl", "the classes of the header do not add up to the files")
        ai = [r for r in rows if r.get("ai") and r.get("cls") in ("card", "page", "svg")]
        if h.get("aiFiles") != len(ai) or h.get("aiBytes") != sum(r.get("b", 0) for r in ai):
            ctx.add("RS-109", "manifest.jsonl", "aiFiles or aiBytes of the header do not match the rows")
        for n, row in enumerate([h] + rows):
            import json

            if len(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))) > BUDGETS["line_chars"]:
                ctx.add("RS-109", "manifest.jsonl", f"line {n + 1} has more than {BUDGETS['line_chars']} characters")
                break


def rs110(ctx: Ctx) -> None:
    """Twin hygiene: no script, no handler, no external reference, no ``data:`` address, no embedded blob, the one policy, one fixed style, every
    table has a caption or lies inside a block (RS-107's scoping), every ``id`` is unique. A gallery (the twin of a screen card, lane U) is held
    to the hygiene of the first group and to a policy that only narrows the viewer's, not to the style, the id and the caption rules (it inlines
    one SVG with its own style per state; design D-2, requests U2-6)."""
    policy = RH.policy_directives(RH.CSP_POLICY)
    lang = ctx.lang
    pairs = ctx.pairs()
    for tp, data in sorted(ctx.twin_files().items()):
        a = ctx.analysis(tp)
        gallery = is_gallery(tp)
        if not a.doctype:
            ctx.add("RS-110", tp, "no doctype")
        for tag in sorted(set(a.forbidden)):
            ctx.add("RS-110", tp, f"the element <{tag}> must not occur")
        if a.event_attrs:
            ctx.add("RS-110", tp, f"an event handler attribute: {a.event_attrs[0]}")
        if a.external:
            ctx.add("RS-110", tp, f"an external address: {a.external[0][:60]}")
        if a.data_uris:
            ctx.add("RS-110", tp, "a data: address")
        if a.base64:
            ctx.add("RS-110", tp, "a long base64 run in an attribute or in the style")
        csp = [m for m in a.metas if m.get("http-equiv", "").lower() == "content-security-policy"]
        if len(csp) != 1 or any(m.get("http-equiv", "").lower() not in ("content-security-policy",) for m in a.metas if "http-equiv" in m):
            ctx.add("RS-110", tp, "a twin has exactly one http-equiv meta: the Content-Security-Policy")
        elif not gallery and (csp[0].get("content") != RH.CSP_POLICY or set(csp[0]) != {"http-equiv", "content"}):
            ctx.add("RS-110", tp, "the policy is not the policy of the viewer page (htmlassemble.CSP_POLICY)")
        elif gallery:
            got = RH.policy_directives(csp[0].get("content", ""))
            if got.get("default-src") != ["'none'"] or any(got[k] != policy.get(k) for k in got):
                ctx.add("RS-110", tp, "the policy of a gallery must only narrow the policy of the viewer page")
        md_lang = RH.detect_lang(ctx.md_texts()[first_part(pairs[tp])]) if pairs.get(tp) else None
        if a.lang != (md_lang or lang):
            ctx.add("RS-110", tp, f"the language of the twin is {a.lang!r}, the page's {md_lang or lang!r}")
        if not 0 <= a.charset_at < 1024:
            ctx.add("RS-110", tp, "the charset declaration is not inside the first 1,024 bytes")
        if not a.title.strip():
            ctx.add("RS-110", tp, "no title")
        if gallery:
            continue
        if a.styles != 1 or a.style_text.strip() != "\n".join(RH.STYLE_LINES) or a.style_bytes > BUDGETS["style_bytes"]:
            ctx.add("RS-110", tp, f"the style is not the one fixed style ({a.styles} elements, {a.style_bytes} bytes, limit {BUDGETS['style_bytes']})")
        if a.table_problems:
            ctx.add("RS-110", tp, f"{len(a.table_problems)} {a.table_problems[0]}")
        seen: set = set()
        for ident in a.ids:
            if ident in seen:
                ctx.add("RS-110", tp, f"the id {ident!r} occurs twice")
                break
            seen.add(ident)
        if not any(m.get("name") == "viewport" for m in a.metas):
            ctx.add("RS-110", tp, "no viewport meta")


def first_difference(want: list, got: list) -> str:
    for k, (x, y) in enumerate(zip(want, got)):
        if x != y:
            return f"atom {k}: page {x[:50]!r}, twin {y[:50]!r}"
    return f"the page has {len(want)} atoms, the twin {len(got)}"


def rs114(ctx: Ctx) -> None:
    """The twin says what the page says: the same atoms in the same order (``visible_md`` against ``visible_html``), the same ids, the same
    stamp, page id and class in the meta tags. A gallery stands for several card parts and holds more than the card: what the cards say (table
    cells, list items and plain lines, not their frames, headings or captions) is a subset of what the gallery holds. With the documents on disk every fragment of a page is a verbatim piece of the document of the project's language."""
    if not ctx.html_on():
        return
    lang = ctx.lang
    pairs, texts = ctx.pairs(), ctx.md_texts()
    for tp, data in sorted(ctx.twin_files().items()):
        mds = pairs.get(tp)
        if not mds:
            continue
        a = ctx.analysis(tp)
        if is_gallery(tp):
            want = {atom for md in mds for atom in RH.content_atoms(md, texts[md], lang)}
            missing = want - a.texts()
            if missing:
                ctx.add("RS-114", tp, f"{len(missing)} contents of the card are not in the gallery, first: {sorted(missing)[0][:60]!r}")
            continue
        md = mds[0]
        text = texts[md]
        want, got = RH.visible_md(text), a.visible()
        if want != got:
            ctx.add("RS-114", tp, first_difference(want, got))
        page = parse_page(lang, md, text)
        if page is None:
            continue
        meta = {m["name"]: m.get("content", "") for m in a.metas if "name" in m}
        stamps = " · ".join(f"{k}={v}" for k, v in page.stamps.items())
        expected = {"atlas-page": page.pid, "atlas-stamp": stamps, "atlas-class": page.cls, "atlas-lang": page.lang, "atlas-schema": RH.SCHEMA}
        for name, value in expected.items():
            if meta.get(name) != value:
                ctx.add("RS-114", tp, f"the meta {name} is {meta.get(name)!r}, the page says {value!r}")
        want_ids = RH.expected_ids(md, text, lang)
        if a.ids != want_ids:
            ctx.add("RS-114", tp, f"the ids of the twin ({len(a.ids)}) are not the ids of the page ({len(want_ids)})")
        alternate = [d.get("href") for d in a.links if d.get("rel") == "alternate"]
        if alternate != [RH.relative(md, posixpath.dirname(tp))]:
            ctx.add("RS-114", tp, "the alternate link does not point to the page")
    docs = [p for p in (f"docs/{lang}/REQ.md", f"docs/{lang}/BD.md", f"docs/{lang}/DD.md") if p in ctx.s.files]
    if docs:
        corpus = "\n".join(ctx.s.text(p) for p in docs)
        lost = [h for h in ctx.src.homes if h.family is not None and h.md not in corpus]
        if lost:
            ctx.add("RS-114", docs[0], f"{len(lost)} fragments of the pages are not verbatim in the documents of {lang} (first: group {lost[0].grp}, key {lost[0].key!r})")


def rs111(ctx: Ctx) -> None:
    lang = ctx.lang
    parts: dict = {}
    for path, page in ctx.pages.items():
        if not path.startswith("read/") or not page.body:
            continue
        first = page.body[0]
        if not first.startswith("#"):
            ctx.add("RS-111", path, "the first line after the frame is not a heading")
        if page.continuation is not None:
            c = page.continuation
            if c["part"] > 1 and not first.startswith("### "):
                ctx.add("RS-111", path, "a continuation part must start with the identity line")
            parts.setdefault((N.parse_page_id(page.pid).family, c["unit"]), []).append((path, page))
    for (_family, unit), items in parts.items():
        numbers = [p.continuation["part"] for _path, p in items]
        total = items[0][1].continuation["parts"]
        if numbers != list(range(1, total + 1)) and numbers != list(range(2, total + 1)) and numbers != list(range(1, len(numbers) + 1)):
            ctx.add("RS-111", items[0][0], f"the parts of {unit!r} are numbered {numbers}, not 1..{total}")
        seen: dict = {}
        for path, page in items:
            heads = [h for h in headings(lang, page.body) if h[4] is not None]
            for _i, _lv, label, n, tot, a, b in heads:
                prev = seen.get(label)
                if prev is not None and (a != prev[1] + 1 or tot != prev[2]):
                    ctx.add("RS-111", path, f"the rows {a}-{b} of {label.strip()!r} do not continue the rows before them")
                seen[label] = (a, b, tot)
            for _start, header, _rows, _end in tables(page.body):
                pass
        for label, (_a, b, tot) in seen.items():
            if b != tot:
                ctx.add("RS-111", items[-1][0], f"the parts of {unit!r} stop at row {b} of {tot} for {label.strip()!r}")


def rs112(ctx: Ctx) -> None:
    pages = [(path, len(ctx.s.files[path])) for path, p in ctx.pages.items() if path.startswith("read/") and p.cls == "page"
             and not re.search(r"/(A01|P1D|P2D|P3Q)\.md$", path)]
    if pages:
        small = [x for x in pages if x[1] < 1500]
        if len(small) / len(pages) > 0.10:
            ctx.add("RS-112", "", f"{len(small)} of {len(pages)} pages are below 1,500 bytes", "merge the small families by folding the dimension")
    if len(pages) >= 10:
        sizes = sorted(size for _p, size in pages)
        if sizes[len(sizes) // 2] < 5000:
            ctx.add("RS-112", "", f"the median page has {sizes[len(sizes) // 2]} bytes (at least 5,000 expected from 10 pages)")


def rs113(ctx: Ctx) -> None:
    qa = ctx.src.meta.get("qa") if isinstance(ctx.src.meta.get("qa"), dict) else {}
    lines: list = []
    for path, page in ctx.pages.items():
        if path.startswith("read/") and re.search(r"/(Q\d+|A01|P3Q)\.md$", path):
            lines.extend(B.unfold_text(page.body, tx(ctx.lang, "fold.cont")))
    import json

    for key, value in sorted(qa.items()):
        want = f"- {key}: " + json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if want not in lines:
            ctx.add("RS-113", "", f"the quality field {key!r} is not printed as it is in the bundle")
    header = ctx.s.manifest_header or ctx.s.header
    gate = qa.get("gate") if isinstance(qa.get("gate"), dict) else {}
    if header.get("blockers") is not None and header.get("blockers") != gate.get("blockers", 0):
        ctx.add("RS-113", "", "blockers of the manifest header differ from qa.gate.blockers")
    info = ctx.s.build_info
    if info:
        for key in qa:
            if key in info and info[key] != qa[key]:
                ctx.add("RS-113", "build-info.json", f"the field {key!r} of build-info differs from the bundle")
        if info.get("release", {}).get("wouldBlock") is not None and info["release"]["wouldBlock"] != gate.get("blockers", 0):
            ctx.add("RS-113", "build-info.json", "release.wouldBlock differs from qa.gate.blockers")


KIND_LINE = {
    # kind -> how the line of ids.tsv must look: ("second", id) = the second line of a block, ("cell", first cell is the code span of the id)
    "node": "second", "entity": "second", "machine": "second", "case": "second",
    "edge": "cell", "transition": "cell", "requirement": "cell", "test": "cell", "tbd": "cell", "evidence": "cell", "room": "cell", "floor": "cell", "building": "cell",
    "event": "event", "intervention": "second",
}


def expected_homes(src: Source) -> set:
    ix = src.ix
    out: set = set()
    for nid in src.node_order:
        out.add((nid, "node"))
    for eid in ix.edge:
        out.add((eid, "edge"))
    for iv in ix.interventions:
        if P.sval(iv.get("interventionId")):
            out.add((P.sval(iv.get("interventionId")), "intervention"))
    for cid, case in ix.case.items():
        out.add((cid, "case"))
        for ev in case.get("events", []) if isinstance(case.get("events"), list) else []:
            if isinstance(ev, dict) and isinstance(ev.get("seq"), int):
                out.add((f"{cid}#{ev['seq']}", "event"))
    for rid in ix.requirement:
        out.add((rid, "requirement"))
    for tid in ix.test:
        out.add((tid, "test"))
    for tid in ix.tbd:
        out.add((tid, "tbd"))
    for eid in ix.evidence:
        out.add((eid, "evidence"))
    for mid, machine in ix.machine.items():
        out.add((mid, "machine"))
        for tr in machine.get("transitions", []) if isinstance(machine.get("transitions"), list) else []:
            if isinstance(tr, dict) and isinstance(tr.get("id"), str):
                out.add((tr["id"], "transition"))
    for h in src.by_group("ent.DD"):
        out.add((h.key, "entity"))
    for table in (ix.room, ix.floor, ix.building):
        pass
    for rid in ix.room:
        out.add((rid, "room"))
    for fid in ix.floor:
        out.add((fid, "floor"))
    for bid in ix.building:
        out.add((bid, "building"))
    for unit in src.bundle.get("diagrams") or []:
        if isinstance(unit, dict) and isinstance(unit.get("id"), str):
            out.add((unit["id"], "diagram"))
    return out


def parse_ids(text: str) -> list:
    rows = []
    for n, line in enumerate(text.split("\n")):
        if not line or line.startswith("#"):
            continue
        cols = line.split("\t")
        rows.append(cols)
    return rows


def rs116(ctx: Ctx) -> None:
    s = ctx.s
    if "ids.tsv" not in s.files:
        ctx.add("RS-116", "ids.tsv", "ids.tsv is missing")
        return
    rows = parse_ids(s.text("ids.tsv"))
    homes: dict = {}
    for cols in rows:
        if len(cols) != 7:
            ctx.add("RS-116", "ids.tsv", f"a line has {len(cols)} columns instead of 7")
            continue
        ident, kind, role, path, line, hdr, _name = cols
        if role == "home":
            homes.setdefault((ident, kind), []).append(cols)
        elif role != "view:inbound":
            ctx.add("RS-116", "ids.tsv", f"the role {role!r} of {ident!r} is not home or view:inbound")
    expected = expected_homes(ctx.src)
    for key in sorted(expected):
        if len(homes.get(key, [])) != 1:
            ctx.add("RS-116", "ids.tsv", f"{key[1]} {key[0]!r} has {len(homes.get(key, []))} homes")
    for key in sorted(set(homes) - expected):
        ctx.add("RS-116", "ids.tsv", f"{key[1]} {key[0]!r} has a home but is not in the model")
    page_lines: dict = {}
    for ident, kind, role, path, line, hdr, _name in (c for c in rows if len(c) == 7):
        if kind == "diagram":
            if path not in _known_paths(ctx):
                ctx.add("RS-116", "ids.tsv", f"the diagram file {path!r} does not exist")
            continue
        page = ctx.pages.get(path)
        if page is None:
            ctx.add("RS-116", "ids.tsv", f"{ident!r}: the page {path!r} is not in the surface")
            continue
        lines = page_lines.setdefault(path, unfold_lines(ctx.lang, page.lines))
        raw_lines = page.lines
        try:
            at, h = int(line), int(hdr)
        except ValueError:
            ctx.add("RS-116", "ids.tsv", f"{ident!r}: line or hdr is not a number")
            continue
        if not (1 <= at <= len(raw_lines) and 1 <= h <= len(raw_lines)):
            ctx.add("RS-116", "ids.tsv", f"{ident!r}: line {at} or hdr {h} is outside {path}")
            continue
        text = raw_lines[at - 1]
        want = "`" + ident.replace("|", "\\|") + "`"
        mode = KIND_LINE.get(kind)
        if kind == "edge" and role == "view:inbound":
            ok = want in text
        elif mode == "second":
            ok = text.startswith(want + " ") or text == want or text.startswith(want + " ·")
        elif mode == "cell":
            ok = B.is_row(text) and B.split_cells(text)[0] == want
        elif mode == "event":
            case, _sep, seq = ident.rpartition("#")
            ok = B.is_row(text) and B.split_cells(text)[0] == seq
        else:
            ok = True
        if not ok:
            ctx.add("RS-116", "ids.tsv", f"{kind} {ident!r}: line {at} of {path} is not its home line")
        if not raw_lines[h - 1].startswith("#"):
            ctx.add("RS-116", "ids.tsv", f"{ident!r}: hdr {h} of {path} is not a heading line")
    for cols in rows:
        if len("\t".join(cols)) > BUDGETS["tsv_line_chars"]:
            ctx.add("RS-116", "ids.tsv", "a line of ids.tsv has more than 400 characters")
            break


WINDOWS_DEVICES = N.WINDOWS_DEVICES
PATH_OK = re.compile(r"^[A-Za-z0-9._~+/-]+$")


def rs117(ctx: Ctx) -> None:
    seen: dict = {}
    for path in sorted(set(ctx.s.files) | set(ctx.s.extra_paths) | set(ctx.records)):
        if not PATH_OK.match(path):
            ctx.add("RS-117", path, "the path has a character outside [A-Za-z0-9._~+-/]")
        for part in path.split("/"):
            stem = part.split(".", 1)[0].upper()
            if stem in WINDOWS_DEVICES:
                ctx.add("RS-117", path, f"{part!r} is a Windows device name")
            if part.endswith((".", " ")):
                ctx.add("RS-117", path, "a path part ends with a dot or a space")
        folded = path.lower()
        if folded in seen and seen[folded] != path:
            ctx.add("RS-117", path, f"the path equals {seen[folded]!r} ignoring case", "the file would be overwritten on NTFS", klass="L")
        seen[folded] = path
        if len(path) > 100:
            ctx.add("RS-117", path, f"the path has {len(path)} characters (limit 100)", "a hashed name would be used", klass="L")
    from ..model import ID_RE

    if ID_RE.match(SECTION + "S07"):
        ctx.add("RS-117", "", "the section sign can start a model id: a page reference could be mistaken for an id")


def _node_pages(ctx: Ctx) -> dict:
    """``{node id: (path, line index of its block)}`` from the second lines of node blocks of the S pages."""
    out: dict = {}
    for path, page in ctx.pages.items():
        if not re.search(r"/(S\d+|A01|P2D)\.md$", path):
            continue
        for n, line in enumerate(page.body):
            if n and page.body[n - 1].startswith("### ") and CODE.match(line):
                out.setdefault(CODE.match(line).group(1).replace("\\|", "|"), (path, n))
    return out


def rs120(ctx: Ctx) -> None:
    ix = ctx.src.ix
    lang = ctx.lang
    expected: dict = {}
    for edge in ix.edges:
        eid, target = P.sval(edge.get("id")), P.sval(edge.get("targetId"))
        if eid and target in ix.node:
            expected.setdefault(target, set()).add(eid)
    rows_by_target: dict = {}
    page_of_node = _node_pages(ctx)
    inbound_re = re.compile("^- " + pattern(lang, "s.inbound", k=r"\d+") + "$")
    for path, page in ctx.pages.items():
        if not re.search(r"/(S\d+|A01|P2D)\.md$", path):
            continue
        for _start, header, rows, _end in tables(page.body):
            if header[0] != B.join_cells(list(tx_list_cells(lang, "th.inbound"))):
                continue
            for row in rows:
                cells = B.split_cells(row)
                if len(cells) != 5:
                    ctx.add("RS-120", path, "an inbound row does not have 5 cells")
                    continue
                target = cells[0].strip("`")
                eid = cells[1].strip("`")
                rows_by_target.setdefault(target, []).append((eid, cells[2].strip("`"), cells[4], path))
        for n, line in enumerate(page.body):
            m = inbound_re.match(line)
            if not m:
                continue
            block = page.body[n - 1] if n else ""
            # walk back to the id line of this block
            k = n - 1
            while k > 0 and not (page.body[k - 1].startswith("### ") and CODE.match(page.body[k])):
                k -= 1
            ident = CODE.match(page.body[k]).group(1).replace("\\|", "|") if k >= 0 and CODE.match(page.body[k]) else None
            if ident is None:
                continue
            if int(m.group(1) if m.groups() else -1) != len(expected.get(ident, ())):
                ctx.add("RS-120", path, f"the block of {ident!r} says {m.group(1)} inbound connections, the model has {len(expected.get(ident, ()))}")
    for target, eids in expected.items():
        got = rows_by_target.get(target, [])
        if sorted(r[0] for r in got) != sorted(eids):
            ctx.add("RS-120", "", f"the inbound table of {target!r} has {len(got)} rows, the model has {len(eids)} edges ending there")
        for eid, source, where, path in got:
            edge = ix.edge.get(eid)
            if edge is None or P.sval(edge.get("targetId")) != target or P.sval(edge.get("sourceId")) != source:
                ctx.add("RS-120", path, f"the inbound row of {eid!r} names a wrong edge, target or source")
                continue
            if source in page_of_node:
                owner = page_of_node[source][0]
                pid = ctx.pages[owner].pid
                if where != SECTION + pid:
                    ctx.add("RS-120", path, f"the inbound row of {eid!r} gives the page {where}, the source is on {SECTION}{pid}")


def tx_list_one(lang: str, key: str) -> str:
    """The last header name of a table whose columns are the list ``read.<key>`` (the column that holds page references)."""
    from .text import tx_list

    return tx_list(lang, key)[-1]


def tx_list_cells(lang: str, key: str) -> list:
    from .text import tx_list

    return [T.md_text(h) for h in tx_list(lang, key)]


LINK_HREF = re.compile(r"(?<!\\)\]\(([^)\s]*)\)")


def rs121(ctx: Ctx) -> None:
    s = ctx.s
    if "START.md" not in ctx.pages:
        ctx.add("RS-121", "START.md", "START.md is missing or cannot be read")
        return
    graph: dict = {}
    for path, page in ctx.pages.items():
        base = path.rsplit("/", 1)[0] if "/" in path else ""
        targets = set()
        for line in page.body + page.neighbours:
            for href in LINK_HREF.findall(CODE.sub("", line)):
                if "://" in href:
                    continue
                parts = [] if not base else base.split("/")
                for piece in href.split("/"):
                    if piece == "..":
                        if parts:
                            parts.pop()
                    elif piece not in ("", "."):
                        parts.append(piece)
                targets.add("/".join(parts))
        graph[path] = targets
    dist = {"START.md": 0}
    frontier = ["START.md"]
    while frontier:
        nxt = []
        for node in frontier:
            for target in sorted(graph.get(node, ())):
                if target in graph and target not in dist:
                    dist[target] = dist[node] + 1
                    nxt.append(target)
        frontier = nxt
    for path, rec in sorted(ctx.records.items()):
        if rec.get("ai") and rec.get("cls") in ("card", "page") and path in ctx.pages:
            if dist.get(path, 99) > 3:
                ctx.add("RS-121", path, f"it is {dist.get(path, 'not')} links away from START (at most 3)")
    start = ctx.s.text("START.md") if "START.md" in s.files else ""
    for path, rec in sorted(ctx.records.items()):
        if not rec.get("ai") and rec.get("b", 0) >= 20000:
            top = path.split("/")[0] + "/" if "/" in path else path
            if path not in start and top not in start:
                ctx.add("RS-121", "START.md", f"the large file {path} is not named in the 'do not read whole' section")
    for target in graph.get("START.md", ()):
        rec = ctx.records.get(target)
        if rec is not None and not rec.get("ai"):
            ctx.add("RS-121", "START.md", f"START links the ai:false file {target}")


def rs122(ctx: Ctx) -> None:
    lang = ctx.lang
    ids = {p.pid for p in ctx.pages.values()}
    sep = tx(lang, "covers.sep")
    item_re = re.compile("^" + pattern(lang, "covers.item", label=r".+?", n=r"\d+") + "$")
    line_re = re.compile("^" + pattern(lang, "covers.line", items=r".+", rest=r".+") + "$")
    for path, page in ctx.pages.items():
        info = N.parse_page_id(page.pid)
        if info is None or info.family in ("START", "INDEX") or page.pid == "G01":
            continue
        for token in PAGE_TOKEN.findall(page.excludes):
            if token not in ids:
                ctx.add("RS-122", path, f"the 'does not cover' line names the missing page {SECTION}{token}")
        m = line_re.match(page.covers)
        if not m:
            ctx.add("RS-122", path, "the covers line does not follow the template")
            continue
        wanted: dict = {}
        for part in m.group("items").split(sep):
            mm = item_re.match(part.strip())
            if not mm:
                ctx.add("RS-122", path, f"the covers item {part!r} does not follow the template")
                continue
            wanted[mm.group("label")] = wanted.get(mm.group("label"), 0) + int(mm.group("n"))
        got: dict = {}
        for _i, _lv, label, n, _t, _a, _b in headings(lang, page.body):
            got[card_label(label.strip())] = got.get(card_label(label.strip()), 0) + n
        for label, n in wanted.items():
            if got.get(label, 0) != n:
                ctx.add("RS-122", path, f"the card says {label} {n}, the page has {got.get(label, 0)}")
        part = re.search(pattern(lang, "covers.partial", n=r"\d+", m=r"\d+"), m.group("rest"))
        if part and page.continuation is None:
            ctx.add("RS-122", path, "a page that is not a continuation part says 'part'")


def rs123(ctx: Ctx) -> None:
    lang = ctx.lang
    ruler = ctx.src.grade_ruler()
    na_text = tx(lang, "howto.na")
    for path, page in ctx.pages.items():
        info = N.parse_page_id(page.pid)
        if info is None or info.family in ("START", "INDEX") or page.pid == "G01":
            continue
        text = "\n".join(page.body)
        howto = page.howto
        if T.NA in text and "[N/A]" not in howto:
            ctx.add("RS-123", path, "the page has [N/A] and the reading line does not explain it")
        if T.NOT_AUTHORED_RE.search(text) and "[NOT-AUTHORED" not in howto:
            ctx.add("RS-123", path, "the page has a NOT-AUTHORED marker and the reading line does not explain it")
        if any(RES.is_verdict_like(lang, line) for line in page.body) and tx(lang, "howto.verdict") not in howto:
            ctx.add("RS-123", path, "the page has a verdict line and the reading line does not explain what a verdict compares")
        words = [w for w in ruler if re.search(r"(?<![A-Za-z])" + re.escape(w) + r"(?![A-Za-z])", text)]
        if words:
            pointer = tx(lang, "howto.ruler_ref")
            at = [howto.find(w) for w in ruler]
            if pointer not in howto and (min(at) < 0 or at != sorted(at)):
                ctx.add("RS-123", path, "a grade word occurs on the page and the reading line has no complete ordered ruler")


def rs124(ctx: Ctx) -> None:
    lang = ctx.lang
    ix = ctx.src.ix
    none_text, unstamped = T.tr(ctx.src.lang, "skeleton.evidence_cell.no_repo"), T.tr(ctx.src.lang, "skeleton.evidence_cell.unstamped")
    rows: dict = {}
    for path, page in ctx.pages.items():
        if not re.search(r"/(E\d+|A01|P1D)\.md$", path):
            continue
        for _s, header, trs, _e in tables(unfold_lines(lang, page.body)):
            if len(B.split_cells(header[0])) != 6 or B.split_cells(header[0])[1] != T.md_text(T.tr_list(lang, "skeleton.th.evidence_ref")[1]):
                continue
            for row in trs:
                cells = B.split_cells(row)
                rows[cells[0].strip("`")] = (cells, path)
    if len(rows) != len(ix.evidence):
        ctx.add("RS-124", "", f"the evidence pages have {len(rows)} rows, the model has {len(ix.evidence)} evidences")
    for eid, rec in ix.evidence.items():
        got = rows.get(eid)
        if got is None:
            ctx.add("RS-124", "", f"the evidence {eid!r} has no row")
            continue
        cells, path = got
        if len(cells) != 6:
            ctx.add("RS-124", path, f"the row of {eid!r} has {len(cells)} cells instead of 6")
            continue
        repo = P.sval(rec.get("repository"))
        if repo and cells[1] != T.code(repo):
            ctx.add("RS-124", path, f"the repository alias of {eid!r} is not on its row")
        if not repo and cells[1] != none_text:
            ctx.add("RS-124", path, f"the empty alias of {eid!r} is not written as the fixed text")
        span = rec.get("lineRange")
        if isinstance(span, list) and len(span) == 2:
            if cells[3] != f"{span[0]}-{span[1]}":
                ctx.add("RS-124", path, f"the stamped line range of {eid!r} is not on its row")
        elif cells[3] != unstamped:
            ctx.add("RS-124", path, f"the unstamped range of {eid!r} is not written as the fixed text")
        anchor = P.sval(rec.get("anchor"))
        if cells[2].endswith("...") and len(anchor) <= 120:
            ctx.add("RS-124", path, f"the location of {eid!r} is cut although the anchor has {len(anchor)} characters")


def rs125(ctx: Ctx) -> None:
    docs = [p for p in ctx.s.extra_paths | set(ctx.s.files) if p.startswith("docs/")]
    if docs and "docs/README.md" not in docs:
        ctx.add("RS-125", "docs/README.md", "docs/ exists and docs/README.md does not")
    for path, rec in ctx.records.items():
        if rec.get("k") in ("doc", "prompt", "sheet") and rec.get("ai"):
            ctx.add("RS-125", path, "a hand-over file must be ai:false")


# ---------------------------------------------------------------------------------------------------- RS-128 conservation

def _page_runs(ctx: Ctx) -> dict:
    """``{path: unfolded body lines}`` of every page of facts."""
    return {path: unfold_lines(ctx.lang, page.body) for path, page in ctx.pages.items() if path.startswith("read/")
            and not re.search(r"/(INDEX[^/]*|G01)\.md$", path)}


def _occurrences(runs: dict, lines: list) -> list:
    """Where ``lines`` occurs as a contiguous run: ``[(path, index)]``."""
    out = []
    first = lines[0]
    n = len(lines)
    for path, body in runs.items():
        i = -1
        while True:
            try:
                i = body.index(first, i + 1)
            except ValueError:
                break
            if body[i:i + n] == lines:
                out.append((path, i))
    return out


def exclusions_of(ctx: Ctx) -> list:
    """The exclusions of the surface: the list the build passed, else the rows of the exclusion table of the quality page."""
    if ctx.s.exclusions:
        return list(ctx.s.exclusions)
    head = B.join_cells(tx_list_cells(ctx.lang, "th.exclusions"))
    out = []
    for path, page in ctx.pages.items():
        if re.search(r"/(Q\d+|A01|P3Q)\.md$", path):
            for _s, header, rows, _e in tables(page.body):
                if header[0] == head:
                    for row in rows:
                        cells = B.split_cells(row)
                        if len(cells) == 3 and cells[1].isdigit():
                            out.append((cells[0], int(cells[1]), cells[2]))
    return out


def rs128(ctx: Ctx) -> None:
    src = ctx.src
    runs = _page_runs(ctx)
    quality = {p for p in runs if re.search(r"/Q\d+\.md$", p)}
    exclusions = exclusions_of(ctx)
    excluded = {name for name, _n, _r in exclusions}
    # multiplicity of identical fragment texts
    expected: dict = {}
    for h in src.homes:
        if h.family is None:
            if h.grp not in excluded:
                ctx.add("RS-128", "", f"the fragment group {h.grp!r} has no home and no exclusion")
            continue
        expected.setdefault(tuple(h.lines), []).append(h)
    for lines, homes in expected.items():
        lines = list(lines)
        found = _occurrences({p: b for p, b in runs.items() if p not in quality}, lines)
        if len(found) == len(homes):
            continue
        if not found and _rows_in_order(runs, lines, quality):
            continue
        ctx.add("RS-128", "", f"a fragment of {homes[0].grp} ({homes[0].key!r}) occurs {len(found)} times, expected {len(homes)}")
    # explicit empty states of groups without members
    for gid, group in src.groups.items():
        if gid in STRUCTURAL or not group.get("empty"):
            continue
        if any(h.grp == gid for h in src.homes):
            continue
        fam_home = gid in ("tbd", "con", "nfr") or gid.startswith("pol:")
        if not fam_home:
            continue
        empty = group["empty"]
        count = sum(body.count(empty) for p, body in runs.items() if p not in quality)
        if count != 1:
            ctx.add("RS-128", "", f"the empty state of {gid!r} occurs {count} times, expected once")
    # marker lines: the home fragments, the group empty states, the evidence rows the model prints itself
    na_re, none_re = T.NOT_AUTHORED_RE, re.compile(r"\[NONE-")
    want_na = want_none = 0
    for h in src.homes:
        if h.family is None:
            continue
        for line in h.lines:
            want_na += na_re.search(line) is not None
            want_none += none_re.search(line) is not None
    cited = {h.key for h in src.by_group("ev")}
    from ..derive import docs as X

    for eid in src.ix.evidence:
        if eid not in cited:
            row = X.evidence_row(src.xctx(), eid)
            want_na += na_re.search(row) is not None
    for gid, group in src.groups.items():
        if gid in STRUCTURAL or not group.get("empty") or any(h.grp == gid for h in src.homes):
            continue
        if gid in ("tbd", "con", "nfr") or gid.startswith("pol:"):
            want_na += na_re.search(group["empty"]) is not None
            want_none += none_re.search(group["empty"]) is not None
    got_na = got_none = 0
    for path, body in runs.items():
        if path in quality:
            continue
        for line in body:
            got_na += na_re.search(line) is not None
            got_none += none_re.search(line) is not None
    if got_na != want_na:
        ctx.add("RS-128", "", f"NOT-AUTHORED lines: the pages have {got_na}, the fragments have {want_na}")
    if got_none != want_none:
        ctx.add("RS-128", "", f"NONE lines: the pages have {got_none}, the fragments have {want_none}")
    # the exclusions are mechanically true
    if excluded:
        _n, bad = None, [h.key for h in src.by_group(EXCLUDED_GROUP)
                         if not set(c for c in _cells(h.md) if c) <= set(next((_cells(d.md) for d in src.by_group("e.DD") if d.key == h.key), []))]
        if bad:
            ctx.add("RS-128", "", f"{len(bad)} excluded interface rows are not rebuildable from the connection table")
    for name, count, reason in exclusions:
        listed = any(name in line and str(count) in line and reason in line for p in quality for line in runs[p])
        if not listed and not any(re.search(r"/(A01|P3Q)\.md$", p) for p in runs):
            ctx.add("RS-128", "", f"the exclusion of {name!r} is not listed in the quality page")


def _rows_in_order(runs: dict, lines: list, quality: set) -> bool:
    """A fragment cut into parts at row seams: its lines (a repeated header apart) occur in order on the pages of its parts."""
    flat: list = []
    for path in sorted(p for p in runs if p not in quality):
        flat.extend(runs[path])
    at = 0
    for line in lines:
        if not line.strip() or B.is_separator(line):
            continue
        try:
            at = flat.index(line, at) + 1
        except ValueError:
            if B.is_row(line) and line in flat:
                continue
            return False
    return True


# ---------------------------------------------------------------------------------------------------- RS-129, RS-131, RS-132

def rs129(ctx: Ctx) -> None:
    lang = ctx.lang
    page = ctx.pages.get("START.md")
    if page is None:
        return
    head = tx_list_cells(lang, "start.dim_head")
    rows: dict = {}
    for _s, header, trs, _e in tables(page.body):
        if header[0] == B.join_cells(head):
            for row in trs:
                cells = B.split_cells(row)
                rows[cells[0]] = cells
    count: dict = {}
    folded: dict = {}
    for path, rec in ctx.records.items():
        if rec.get("k") == "page" and rec.get("fam"):
            if rec["fam"] in ("A", "P"):
                folded[rec["id"]] = rec
            else:
                count[rec["fam"]] = count.get(rec["fam"], 0) + 1
    from .specials import FAMILY_ORDER

    for key in FAMILY_ORDER:
        title = T.md_text(tx(lang, "family." + key))
        cells = rows.get(title)
        if cells is None:
            ctx.add("RS-129", "START.md", f"the dimension table has no row for the family {key}")
            continue
        n = count.get(key, 0)
        if cells[1] != str(n):
            ctx.add("RS-129", "START.md", f"the row of {key} says {cells[1]} pages, the manifest has {n}")
        if n == 0:
            none = tx(lang, "start.dim_none")
            prefix = raw(lang, "start.dim_in").split("{page}")[0]
            folded_ids = {rec["id"][len(SECTION):] for rec in folded.values()}
            named = {t for t in PAGE_TOKEN.findall(cells[2])}
            if none in cells[2]:
                if not cells[3].strip():
                    ctx.add("RS-129", "START.md", f"the row of {key} gives no reason")
            elif not (prefix and cells[2].startswith(prefix) and named and named <= folded_ids):
                ctx.add("RS-129", "START.md", f"the row of {key} has no pages and neither says 'none' nor names the folded page")


def rs131(ctx: Ctx) -> None:
    s = ctx.s
    if "refs.tsv" not in s.files:
        ctx.add("RS-131", "refs.tsv", "refs.tsv is missing")
        return
    got = set()
    for cols in parse_ids(s.text("refs.tsv")):
        if len(cols) != 5:
            ctx.add("RS-131", "refs.tsv", f"a line has {len(cols)} columns instead of 5")
            continue
        got.add((cols[0], cols[1], cols[2], cols[3], int(cols[4])))
        target, field_, owner, path, line = cols[0], cols[1], cols[2], cols[3], int(cols[4])
        page = ctx.pages.get(path)
        if page is None or not (1 <= line <= len(page.lines)):
            ctx.add("RS-131", "refs.tsv", f"{target!r}: the line {line} of {path} does not exist")
            continue
        text = page.lines[line - 1]
        if "`" + target.replace("|", "\\|") + "`" not in text:
            ctx.add("RS-131", "refs.tsv", f"the line {line} of {path} does not contain {target!r}")
        k = line - 1
        if B.is_row(text):
            ok = B.split_cells(text)[0] == "`" + owner + "`"
        else:
            while k > 0 and not page.lines[k - 1].startswith("### "):
                k -= 1
            ok = k >= 1 and ("`" + owner + "`") in page.lines[k]
        if not ok:
            ctx.add("RS-131", "refs.tsv", f"the line {line} of {path} is not in the block or row of {owner!r}")
    from .tables import refs_rows

    class Fake:
        pass

    pages = []
    ids_rows = [c for c in parse_ids(s.text("ids.tsv"))] if "ids.tsv" in s.files else []
    by_path: dict = {}
    for c in ids_rows:
        if len(c) == 7:
            by_path.setdefault(c[3], []).append((c[0], c[1], c[2], int(c[4]), int(c[5])))
    for path, marks in by_path.items():
        page = ctx.pages.get(path)
        if page is None:
            continue
        r = Fake()
        r.path, r.lines, r.marks = path, page.lines, marks
        pages.append(r)
    want, _unprinted = refs_rows(ctx.src, pages)
    if set(want) != got:
        missing, extra = sorted(set(want) - got), sorted(got - set(want))
        ctx.add("RS-131", "refs.tsv", f"refs.tsv differs from the references of the model: {len(missing)} missing (first {missing[:1]}), {len(extra)} extra (first {extra[:1]})")


def rs132(ctx: Ctx) -> None:
    caps = BUDGETS["cap_html"]
    for tp, mds in sorted(ctx.pairs().items() if ctx.html_on() else ()):
        data = ctx.twin_files().get(tp)
        if data is None or is_gallery(tp):
            continue                                  # the size of a gallery is lane U's (SV-213)
        first = first_line_regex(ctx.lang).match(ctx.md_texts()[mds[0]].split("\n", 1)[0])
        cap = caps.get(first.group("cls")) if first else None
        if cap is not None and len(data) > cap:
            ctx.add("RS-132", tp, f"the twin has {len(data)} bytes (cap {cap})", "shorten the page or the long text of the elements it shows")
    for path, rec in sorted(ctx.records.items()):
        if rec.get("fit"):
            ctx.add("RS-132", path, f"{rec['fit']} long lines or cells were folded", "shorten the long text of the elements on this page")
    for path, data in sorted(ctx.s.files.items()):
        if path.endswith(".mmd") and len(data) > BUDGETS["cap_mmd"]:
            ctx.add("RS-132", path, f"the diagram has {len(data)} bytes (cap {BUDGETS['cap_mmd']})")
    for path, page in ctx.pages.items():
        if tx(ctx.lang, "howto.ruler_ref") in page.howto:
            ctx.add("RS-132", path, "the grade ruler did not fit the reading line: replaced by the pointer to the glossary")


# ---------------------------------------------------------------------------------------------------- RS-108, RS-115, RS-127 (milestone R3)

RESULT_PAGE = re.compile(r"/(C\d+|V\d+|A01|P3Q)\.md$")
RESULT_TABLES = ("final", "steps", "effects")


def _result_pages(ctx: Ctx) -> dict:
    """Pages that may carry result blocks: the case and intervention pages, the single page and the merged page of 3D to QA."""
    return {path: page for path, page in ctx.pages.items() if RESULT_PAGE.search(path)}


def _unfolded(ctx: Ctx, page) -> list:
    return unfold_lines(ctx.lang, page.body)


def _label_of(heads: list, at: int) -> str:
    """The card label of the nearest heading with a count above line ``at``."""
    label = ""
    for index, _lv, text, _n, _t, _a, _b in heads:
        if index < at:
            label = card_label(text.strip())
    return label


def _ref_ids(ref: str) -> tuple:
    """``(id,)`` of a head line key (a case id in backticks) or ``(iid, cid)`` of the key of a pair (two ids in backticks joined by a times sign)."""
    inner = ref.strip()[1:-1]
    return tuple(inner.split("`×`")) if "`×`" in inner else (inner,)


def _result_blocks(ctx: Ctx) -> dict:
    """What the result pages print, found again in the written text (folded lines are joined first)."""
    lang = ctx.lang
    rx = RES.regexes(lang)
    out = {"cases": {}, "pairs": {}, "unsealed_cases": {}, "unsealed_ivs": {}, "unsealed_pairs": {}, "final": [], "steps": [], "effects": []}
    un_case = re.compile("^- " + pattern(lang, "c.unsealed", id=r"`.+`") + "$")
    un_iv = re.compile("^- " + pattern(lang, "v.unsealed", id=r"`.+`") + "$")
    un_pair = re.compile("^- " + pattern(lang, "v.pair_unsealed", ref=r"`.+`×`.+`") + "$")
    heads = {name: tx_list_cells(lang, "th." + name) for name in RESULT_TABLES}
    for path, page in _result_pages(ctx).items():
        lines = _unfolded(ctx, page)
        for n, line in enumerate(lines):
            m = rx["case_head"].match(line)
            if m:
                out["cases"].setdefault(_ref_ids(m.group("ref"))[0], []).append((path, m.group("ledger"), n))
                continue
            m = rx["pair_head"].match(line)
            if m:
                out["pairs"].setdefault(_ref_ids(m.group("ref")), []).append((path, m.group("ledger"), n))
                continue
            m = un_case.match(line)
            if m:
                out["unsealed_cases"].setdefault(m.group("id").strip("`"), []).append(path)
                continue
            m = un_iv.match(line)
            if m:
                out["unsealed_ivs"].setdefault(m.group("id").strip("`"), []).append(path)
                continue
            m = un_pair.match(line)
            if m:
                out["unsealed_pairs"].setdefault(_ref_ids(m.group("ref")), []).append(path)
        for _s, header, rows, _e in tables(lines):
            for name in RESULT_TABLES:
                if header[0] == B.join_cells(heads[name]):
                    out[name].extend((path, B.split_cells(row)) for row in rows)
    return out


def rs115(ctx: Ctx) -> None:
    """No result outside the ledger (design 2.10, AM-08, AM-20): results only for items that are in ``bundle.runs`` AND that the ledger calls sealed or
    confirmed; every other case and intervention has the fixed sentence and its id in the quality page; no result line outside the result sections; the
    frame says ``sealed`` exactly when the page has a result block. The check reads the written pages against the bundle and ``sealed_meta``; it
    runs no engine and compares no state."""
    src, lang = ctx.src, ctx.lang
    for cid in src.stray_cases:
        ctx.add("RS-115", "", f"bundle.runs holds a result of the case {cid!r} and the ledger does not call it sealed or confirmed (or withholds it): it is not printed")
    for iid, cid in src.stray_pairs:
        ctx.add("RS-115", "", f"bundle.runs holds a result of the pair {iid!r} x {cid!r} and the ledger does not call it sealed or confirmed: it is not printed")
    if src.withheld_known:
        ran_ivs = {p.get("interventionId") for p in src.pairs}
        for cid in src.case_ids():
            if (cid in src.baselines) == (cid in src.withheld_cases):
                ctx.add("RS-115", "", f"the case {cid!r} is {'both in bundle.runs and' if cid in src.baselines else 'neither in bundle.runs nor'} in the withheld list")
        for iid in src.intervention_ids():
            if (iid in ran_ivs) == (iid in src.withheld_ivs):
                ctx.add("RS-115", "", f"the intervention {iid!r} is {'both in bundle.runs and' if iid in ran_ivs else 'neither in bundle.runs nor'} in the withheld list")
    found = _result_blocks(ctx)
    words = {tx(lang, "res.ledger.sealed"): "sealed", tx(lang, "res.ledger.confirmed"): "confirmed"}
    for cid, state in sorted(src.shown_cases.items()):
        got = found["cases"].get(cid, [])
        if len(got) != 1:
            ctx.add("RS-115", "", f"the shown case {cid!r} has {len(got)} result blocks")
        elif words.get(got[0][1]) != state:
            ctx.add("RS-115", got[0][0], f"the ledger word of the result block of {cid!r} is not the state of the ledger ({state})")
    model_cases = set(src.case_ids())
    for cid in sorted(model_cases):
        if cid not in src.shown_cases:
            if cid in found["cases"]:
                ctx.add("RS-115", found["cases"][cid][0][0], f"the case {cid!r} is not sealed or confirmed and has a result block")
            if len(found["unsealed_cases"].get(cid, [])) != 1:
                ctx.add("RS-115", "", f"the case {cid!r} is not shown and has {len(found['unsealed_cases'].get(cid, []))} 'not sealed' sentences")
    for key in sorted(found["cases"]):
        if key not in src.shown_cases and key not in model_cases:
            ctx.add("RS-115", found["cases"][key][0][0], f"a result block names {key!r}, which is not a case of the model")
    for (iid, cid), state in sorted(src.shown_pairs.items()):
        got = found["pairs"].get((iid, cid), [])
        if len(got) != 1:
            ctx.add("RS-115", "", f"the shown pair {iid!r} x {cid!r} has {len(got)} result blocks")
        elif words.get(got[0][1]) != state:
            ctx.add("RS-115", got[0][0], f"the ledger word of the result block of {iid!r} x {cid!r} is not the state of the ledger ({state})")
    for key in sorted(found["pairs"]):
        if key not in src.shown_pairs:
            ctx.add("RS-115", found["pairs"][key][0][0], f"the pair {key[0]!r} x {key[-1]!r} is not sealed or confirmed and has a result block")
    shown_ivs = {iid for iid, _cid in src.shown_pairs}
    for iv in src.ix.interventions:
        iid = P.sval(iv.get("interventionId"))
        if not iid:
            continue
        if iid not in shown_ivs and len(found["unsealed_ivs"].get(iid, [])) != 1:
            ctx.add("RS-115", "", f"the intervention {iid!r} has no pair to show and has {len(found['unsealed_ivs'].get(iid, []))} 'not sealed' sentences")
        if iid in shown_ivs:
            for cid in dict.fromkeys(P.str_list(iv.get("appliesTo"))):
                if (iid, cid) not in src.shown_pairs and len(found["unsealed_pairs"].get((iid, cid), [])) != 1:
                    ctx.add("RS-115", "", f"the pair {iid!r} x {cid!r} is not shown and has no 'not sealed' sentence")
    # the quality page names every item without a result (ids and counts only)
    from . import quality as Q

    listed: set = set()
    head = B.join_cells(tx_list_cells(lang, "th.withheld"))
    for path, page in ctx.pages.items():
        if re.search(r"/(Q\d+|A01|P3Q)\.md$", path):
            for _s, header, rows, _e in tables(_unfolded(ctx, page)):
                if header[0] == head:
                    listed |= {B.split_cells(row)[0].strip("`") for row in rows}
    want = {key for key, _kind in Q.withheld_rows(src)}
    if listed != want:
        ctx.add("RS-115", "", f"the withheld table of the quality page differs from the items without a result: {len(want - listed)} missing, {len(listed - want)} extra")
    # no result line outside a result section; the frame says what the page holds
    holders = _result_pages(ctx)
    allowed = {card_label(tx(lang, "sec.results")), card_label(tx(lang, "sec.pairs"))}
    for path, page in ctx.pages.items():
        info = N.parse_page_id(page.pid)
        lines = _unfolded(ctx, page)
        heads = headings(lang, lines)
        has = RES.has_results(lang, lines)
        for n, line in enumerate(lines):
            if RES.line_kind(lang, line) is None and not RES.is_verdict_like(lang, line):
                continue
            if path not in holders:
                ctx.add("RS-115", path, "a result line is on a page that holds no results (only case and intervention pages may)")
                break
            label = _label_of(heads, n)
            if label not in allowed:
                ctx.add("RS-115", path, f"a result line is under the heading {label!r}, not under a result section")
                break
        want_content = tx(lang, "content.sealed") if has and path in holders else tx(lang, "content.structure")
        if info is not None and info.family not in ("START", "INDEX") and page.pid != "G01" and page.content != want_content:
            ctx.add("RS-115", path, f"the frame says {page.content!r} and the page {'has' if has else 'has no'} result block")
    for name in RESULT_TABLES:
        for path, cells in found[name]:
            ref = cells[0].strip("`")
            if name == "final" and ref not in src.shown_cases:
                ctx.add("RS-115", path, f"a side-by-side row names the case {ref!r}, which is not shown")
                break
            if name != "final" and _ref_ids(cells[0]) not in src.shown_pairs:
                ctx.add("RS-115", path, f"a {name} row names the pair {ref!r}, which is not shown")
                break
    # the result of an item that is not shown is on no page: no row of a stray case names it in its first cell
    strays = set(src.stray_cases)
    if strays:
        for path, page in ctx.pages.items():
            for line in page.body:
                if B.is_row(line) and B.split_cells(line)[0].startswith("`") and B.split_cells(line)[0].strip("`") in strays:
                    ctx.add("RS-115", path, "a row of a case that is not sealed names it in its first cell")
                    break


def rs108(ctx: Ctx) -> None:
    """Verdict lines (design D-32, AM-07): the grammar, the scope (``k`` of ``n`` nodes), the basis, the weakest grade and the nature are all on the
    line; every number is the one ``sealed_meta`` stored; without ``verdicts`` the one fixed line says so and no verdict line exists."""
    lang = ctx.lang
    rx = RES.regexes(lang)
    for path, page in _result_pages(ctx).items():
        lines = _unfolded(ctx, page)
        for n, line in enumerate(lines):
            kind = RES.line_kind(lang, line)
            if RES.is_verdict_like(lang, line) and (kind is None or not kind.startswith("verdict")):
                ctx.add("RS-108", path, f"a verdict line does not follow the grammar: {line[:80]!r}")
            if kind in ("case_head", "pair_head"):
                stop = next((i for i, x in enumerate(lines[n + 1:n + 8]) if RES.line_kind(lang, x) in ("case_head", "pair_head") or x.startswith("#")), 7)
                after = [x for x in lines[n + 1:n + 1 + stop] if RES.is_verdict_like(lang, x)]
                if len(after) != 1:
                    ctx.add("RS-108", path, f"a result block has {len(after)} verdict lines (one is required)")
                    continue
                m = rx[kind].match(line)
                _check_verdict(ctx, path, after[0], kind, _ref_ids(m.group("ref")), m.group("status"))


def _check_verdict(ctx: Ctx, path: str, line: str, head_kind: str, ref: tuple, status: str) -> None:
    src, lang = ctx.src, ctx.lang
    rx = RES.regexes(lang)
    nature = tx(lang, "res.nature")
    word_pass, word_fail = tx(lang, "res.word.pass"), tx(lang, "res.word.fail")
    none_re = re.compile("^" + pattern(lang, "res.none", reason=r".+") + "$")
    ruler = src.grade_ruler()
    kind = RES.line_kind(lang, line)
    if kind == "verdict_none":
        if src.verdicts_known:
            ctx.add("RS-108", path, "a verdict line says there is no verdict data and sealed_meta has verdicts")
        return
    if not src.verdicts_known:
        ctx.add("RS-108", path, "a verdict line exists and sealed_meta has no verdicts: the page computed it")
        return
    m = rx[kind].match(line)
    is_case = head_kind == "case_head"
    if kind != "verdict_undecided" and (kind == "verdict_case") != is_case:
        ctx.add("RS-108", path, "the verdict line is of the other kind (case or pair) than its block")
        return
    if m.group("nature") != nature:
        ctx.add("RS-108", path, f"the nature of a verdict line is not the fixed text: {m.group('nature')!r}")
    grade, basis = m.group("grade"), m.group("basis")
    if not none_re.match(grade) and not any(grade == w or grade.startswith(raw(lang, "res.grade_counts").split("{counts}")[0].replace("{weakest}", w)) for w in ruler):
        ctx.add("RS-108", path, f"the weakest grade {grade!r} is neither a grade of the profile nor an explicit none")
    if not none_re.match(basis):
        ids = [x.strip().strip("`") for x in re.sub(r" \+\d+$", "", basis).split(",")]
        if is_case:
            if not set(ids) <= set(P.str_list((src.ix.case.get(ref[0]) or {}).get("evidenceIds"))):
                ctx.add("RS-108", path, f"the basis of {ref[0]!r} names an evidence the case does not cite")
        elif not all(i in src.ix.evidence for i in ids):
            ctx.add("RS-108", path, f"the basis of {ref[0]!r} x {ref[-1]!r} names an evidence the model does not have")
    run = src.baselines.get(ref[0]) if is_case else None
    decision = RES.case_decision(src, ref[0], run if isinstance(run, dict) else {}) if is_case else RES.pair_decision(src, ref[0], ref[-1], status == "OK")
    if kind == "verdict_undecided":
        if decision[0] != "undecided":
            ctx.add("RS-108", path, "a verdict line says undecided and sealed_meta has a decided verdict for it")
        elif m.group("reason") != tx(lang, "res.why." + decision[1]):
            ctx.add("RS-108", path, "the reason of an undecided verdict is not the one sealed_meta implies")
        return
    if decision[0] != "decided":
        ctx.add("RS-108", path, "a verdict line has a pass or fail word and sealed_meta has no decided verdict for it")
        return
    entry, terminal = decision[1], decision[2]
    if m.group("word") != (word_pass if entry["pass"] else word_fail):
        ctx.add("RS-108", path, f"the verdict word {m.group('word')!r} is not what sealed_meta stored")
    k, n, j = int(m.group("k")), int(m.group("n")), int(m.group("j"))
    if (j, k) != terminal:
        ctx.add("RS-108", path, f"the line says {j} of {k} nodes differ, sealed_meta says {terminal[0]} of {terminal[1]}")
    if n != len(src.node_order) or k > n or j > k:
        ctx.add("RS-108", path, f"the scope 'k of n' ({k} of {n}) is not inside the nodes of the model ({len(src.node_order)})")
    if is_case:
        expected = (src.ix.case.get(ref[0]) or {}).get("expectedTerminalState")
        if isinstance(expected, dict) and len(expected) != k:
            ctx.add("RS-108", path, f"the line asserts {k} nodes, the case lists {len(expected)}")
        if (int(m.group("rj")), int(m.group("ra"))) != decision[3]:
            ctx.add("RS-108", path, "the rule counts of the line are not the ones sealed_meta stored")
    else:
        pair = next((p for p in src.pairs if p.get("interventionId") == ref[0] and p.get("caseId") == ref[-1]), {})
        klass = (pair.get("diff") or {}).get("outcomeClass") if isinstance(pair.get("diff"), dict) else None
        if isinstance(klass, str) and m.group("klass") != T.md_text(klass):
            ctx.add("RS-108", path, "the outcome class of a pair verdict is not the one of its result block")


def rs127(ctx: Ctx) -> None:
    """Diagram headers (design 2.12, AM-11): every diagram of the bundle, as the file text ``diagram_header.apply`` makes (build time) or as the file
    on disk when the surface has it (``read --check``, ``verify``), satisfies ``diagram_header.problems``: version line, the keys, the id and type of
    the unit, the stamp, the body byte for byte, an alias line for every short name, the cut note, the pair lines, the fixed legend."""
    src = ctx.src
    hctx = DH.HeaderContext.from_model(src.model, src.lang, src.stamp)
    for unit in src.bundle.get("diagrams") or []:
        if not isinstance(unit, dict) or not isinstance(unit.get("id"), str):
            continue
        path = f"diagrams/{T.dg_id(unit['id'])}.mmd"
        on_disk = ctx.s.files.get(path)
        text = on_disk.decode("utf-8", "replace") if on_disk is not None else DH.apply(unit, hctx)
        for problem in DH.problems(text, unit, hctx):
            ctx.add("RS-127", path, problem)


def _noop(ctx: Ctx) -> None:
    return None


ACTIVE = (("RS-101", rs101), ("RS-102", rs102), ("RS-103", rs103), ("RS-104", rs104), ("RS-105", rs105), ("RS-106", rs106), ("RS-107", rs107),
          ("RS-108", rs108), ("RS-109", rs109), ("RS-110", rs110), ("RS-111", rs111), ("RS-112", rs112), ("RS-113", rs113), ("RS-114", rs114), ("RS-115", rs115), ("RS-116", rs116), ("RS-117", rs117), ("RS-120", rs120),
          ("RS-121", rs121), ("RS-122", rs122), ("RS-123", rs123), ("RS-124", rs124), ("RS-125", rs125), ("RS-127", rs127), ("RS-128", rs128), ("RS-129", rs129),
          ("RS-131", rs131), ("RS-132", rs132))


def check_surface(src: Source, surface: Surface, only: Iterable[str] | None = None) -> list:
    """Every I and L check on a written surface; ``only`` limits it to some rule ids."""
    ctx = Ctx(src, surface)
    wanted = set(only) if only is not None else None
    for rule, function in ACTIVE:
        if wanted is None or rule in wanted:
            function(ctx)
    return ctx.findings


def run(src: Source, bundle: dict, result, plan, rendered: dict, index_pages: dict, cfg) -> list:
    """The checks of a build: the files of ``result`` (the manifest is not written yet, so RS-109 compares the rows with the bytes)."""
    extra = {f"diagrams/{T.dg_id(u['id'])}.mmd" for u in bundle.get("diagrams") or [] if isinstance(u, dict) and isinstance(u.get("id"), str)}
    surface = Surface(dict(result.files), list(result.records), dict(result.header), extra, list(result.exclusions))
    return check_surface(src, surface)


#: the keys that name the states of the ledger (``sealed``, ``confirmed`` and the counts line of the quality page) are exempt from the forbidden words:
#: the ledger word is a fact about the ledger (the user approved the expectation), not a claim that anything was verified (design RS-118)
EXEMPT_KEYS = ("read.res.ledger",)


def forbidden_hits(lang: str, tables: dict, words: Iterable[str]) -> list:
    """RS-118 (test class): the values of ``tables`` (nested) that contain a forbidden word; ``[(key path, word)]``."""
    out: list = []

    def walk(node, path):
        if isinstance(node, str):
            if any(".".join(path).startswith(prefix) for prefix in EXEMPT_KEYS):
                return
            low = node.lower()
            for word in words:
                if word.lower() in low:
                    out.append((".".join(path), word))
        elif hasattr(node, "items"):
            for k, v in node.items():
                walk(v, path + [str(k)])
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                walk(v, path + [str(i)])

    walk(tables, [])
    return out


def surface_from_dist(dist) -> Surface:
    """A surface read from a written ``dist/`` (after ``finalize``): every file, the rows and the header of ``manifest.jsonl``. The checks of a
    tree on disk (``read --check``, the mutation tests) use this one."""
    import json
    from pathlib import Path

    from .. import slices as sl
    from . import manifest as M

    root = Path(dist)
    files: dict = {}
    for rel in M.scan(root):
        path = root / rel
        if path.stat().st_size <= M.HARD_LIMIT:
            files[rel] = sl.read_limited(path, M.HARD_LIMIT)
    manifest_path = root / "manifest.jsonl"
    header, rows = None, []
    if manifest_path.is_file():
        for n, line in enumerate(sl.read_limited(manifest_path, M.HARD_LIMIT).decode("utf-8").split("\n")):
            if line.strip():
                obj = json.loads(line)
                if n == 0:
                    header = obj
                else:
                    rows.append(obj)
    registered = {r["p"] for r in rows}
    strangers = sorted(p for p in files if p not in registered)
    extra = {rel for rel in M.scan(root) if rel not in files}
    return Surface(files, list(rows), {k: v for k, v in (header or {}).items() if k in ("m", "lang", "project", "blockers", "qaSha8")}, extra, [], None, rows,
                   header, strangers)
