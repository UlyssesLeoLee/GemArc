"""``screens scan``: literal probes of a prototype against a frontend source tree (design A3 4.8, 5.8).

The scan answers a narrow question for the check points that carry a literal: is the string of the prototype anywhere in the frontend source?

* ``route`` the route of the screen, ``label`` the text of a widget, ``bind`` the field name of a widget, ``goto`` the route of the screen a widget
  navigates to (looked for near the place where the widget was found, not in the whole tree).
* ``data-atlas-id="<widget id>"`` attributes locate an element exactly (the recommended convention for a generated frontend); an id of the screen that the
  prototype does not have is listed as ``extra``, a widget without its id as ``absent``.
* ``kind`` is a hint only: the tag around the place where a widget was found is compared with the DOM table of ``ui-schema.md`` and a tag that does not fit
  the kind is listed as ``kind?``. Nothing is ever reported as right: a found literal says the string exists, not that the element is the right one.

Not checked, by design: ``parent``, ``order``, ``emph``, ``in``, ``state`` and ``api`` have no literal; the AI reads the code for them (the card has the
table). A frontend whose texts are i18n keys, whose routes are in a configuration or whose components are built at run time gives many ``ABSENT``: the scan is
meant for static source (design 4.8, appendix B).

Every answer is ``FOUND <alias>:<path>:<line>`` or ``ABSENT near:<the closest string of the source>``. Source text is read through ``evidence.read_text_file``,
files named like secrets (``.env*``, key stores, ``secrets.*``) are never read, a source line leaves this module only through ``echo_source_line`` (a line that
looks like a credential is shown as withheld), and a path is only ever written relative to the root: no host path is in an output. The result of each screen
is written by the command (and only by it) to ``atlas/.atlas/ui/scan-<enc>.json`` for the rule ``UV-006`` (``store.write_scan``); the scan is not a verdict.

Command line: ``python -m gemarc_mcp.atlas.protoui.scan <project> --repo <alias> [--glob G] [--screen S ...] [--all] [--dry-run] [--root WORKSPACE]``;
the evidence root is an ALIAS of ``GEMARC_ATLAS_ROOTS`` (a path is refused). Standard library only.
"""
from __future__ import annotations

import argparse
import bisect
import difflib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .. import evidence as ev
from .. import project as pj
from .. import roots as rt
from ...paths import GemArcError
from ..reading import api as reading
from . import model as um
from . import store

SCHEMA = store.SCAN_SCHEMA
DEFAULT_GLOB = "**/*.{html,htm,jsx,tsx,vue,svelte,js,ts}"
#: Directories that never hold the source of an interface (dependencies, build output, version control, caches).
SKIP_DIRS = frozenset({"node_modules", ".git", "dist", "build", "coverage", ".next", ".nuxt", ".svelte-kit", ".cache", "__pycache__", "vendor"})
MAX_FILES = 3000
MAX_FILE_BYTES = 1 << 20          # a larger file is a bundle, not source
MAX_TOTAL_BYTES = 48 << 20
MAX_GLOB = 200
#: Lines around the place where a widget was found in which the target route of its navigation is looked for.
GOTO_WINDOW = 5
NEAR_CANDIDATES = 4000
SHOWN_NEAR = 40
LITERAL_COLUMNS = ("route", "label", "bind", "goto")


# ---------------------------------------------------------------------------------------------------------------
# Glob (``**/``, ``*``, ``?`` and ``{a,b}`` alternatives; the standard library has no brace expansion)
# ---------------------------------------------------------------------------------------------------------------


def compile_glob(pattern: str) -> "re.Pattern":
    """A regular expression for a ``/``-separated relative path: ``**/`` is any number of directories, ``*`` and ``?`` stay inside one name, ``{a,b}`` is a choice."""
    if not isinstance(pattern, str) or not pattern.strip() or len(pattern) > MAX_GLOB or pattern.count("**") > 3 or "\\" in pattern:
        raise ValueError("a glob is a short pattern with at most three '**' and no backslash, for example **/*.{html,js}")

    def alternatives(text: str) -> str:
        return "(?:" + "|".join(translate(part) for part in text.split(",")) + ")"

    def translate(text: str) -> str:
        out, i = [], 0
        while i < len(text):
            if text.startswith("**/", i):
                out.append("(?:.*/)?")
                i += 3
            elif text.startswith("**", i):
                out.append(".*")
                i += 2
            elif text[i] == "*":
                out.append("[^/]*")
                i += 1
            elif text[i] == "?":
                out.append("[^/]")
                i += 1
            elif text[i] == "{":
                end = text.find("}", i)
                if end < 0:
                    raise ValueError("a glob has a '{' without its '}'")
                out.append(alternatives(text[i + 1:end]))
                i = end + 1
            else:
                out.append(re.escape(text[i]))
                i += 1
        return "".join(out)

    return re.compile("^" + translate(pattern.strip()) + "$")


# ---------------------------------------------------------------------------------------------------------------
# The source tree
# ---------------------------------------------------------------------------------------------------------------


_OPENERS_OF_SPANS = re.compile(r"<!--|/\*|<title\b|^[ \t]*//[^\n]*", re.M | re.I)
_CLOSER_OF = {"<!--": "-->", "/*": "*/"}
_TITLE_END = re.compile(r"</title\s*>", re.I)


def ignored_spans(text: str) -> list:
    """``[(start, end)]`` of the comments (``<!-- -->``, ``/* */``, a line that starts with ``//``) and of the ``<title>`` element, sorted and apart.
    Linear: when the closer of a kind is not in the rest of the text, that kind is not tried again."""
    spans, dead, pos = [], set(), 0
    while True:
        m = _OPENERS_OF_SPANS.search(text, pos)
        if m is None:
            return spans
        token = m.group().lower()
        key = "<!--" if token.startswith("<!--") else "/*" if token == "/*" else "<title" if token.startswith("<title") else ""
        if not key:                                   # a whole line comment
            spans.append((m.start(), m.end()))
            pos = m.end()
            continue
        if key in dead:
            pos = m.end()
            continue
        if key == "<title":
            found = _TITLE_END.search(text, m.end())
            end, stop = (found.start(), found.end()) if found else (-1, -1)
        else:
            end = text.find(_CLOSER_OF[key], m.end())
            stop = end + len(_CLOSER_OF[key])
        if end < 0:
            dead.add(key)
            pos = m.end()
            continue
        spans.append((m.start(), stop))
        pos = stop


@dataclass
class Doc:
    rel: str
    src: "ev.SourceText"
    _spans: "list | None" = field(default=None, repr=False)

    @property
    def text(self) -> str:
        return self.src.text

    def ignored(self, offset: int) -> bool:
        """The position is inside a comment or the ``<title>`` of the page: a string there is not the widget (it would hide a wrong text)."""
        if self._spans is None:
            self._spans = ignored_spans(self.src.text)
        i = bisect.bisect_right(self._spans, (offset, float("inf"))) - 1
        return i >= 0 and self._spans[i][0] <= offset < self._spans[i][1]


@dataclass
class Tree:
    docs: list = field(default_factory=list)
    skipped: dict = field(default_factory=lambda: {"sensitive": 0, "large": 0, "unreadable": 0, "limit": 0})


def collect(root: "Path | str", glob: str = DEFAULT_GLOB) -> Tree:
    """The files of ``root`` selected by ``glob``, sorted by relative path, read as text. Links and junctions are not followed, directories of
    ``SKIP_DIRS`` and dot directories are skipped, a sensitive file is counted and never read, a file over 1 MiB or not UTF-8 text is counted and skipped."""
    rx = compile_glob(glob)
    tree = Tree()
    total = 0
    stack = [(Path(root), "")]
    while stack:
        directory, prefix = stack.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda e: e.name)
        except OSError:
            continue
        subdirs = []
        for entry in entries:
            rel = prefix + entry.name
            try:
                if pj.is_link(entry.path):
                    continue
                is_dir = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if entry.name not in SKIP_DIRS and not entry.name.startswith("."):
                    subdirs.append((Path(entry.path), rel + "/"))
                continue
            if not is_file or not rx.match(rel):
                continue
            if ev.is_sensitive_path(rel):
                tree.skipped["sensitive"] += 1
                continue
            if len(tree.docs) >= MAX_FILES or total >= MAX_TOTAL_BYTES:
                tree.skipped["limit"] += 1
                continue
            try:
                if entry.stat(follow_symlinks=False).st_size > MAX_FILE_BYTES:
                    tree.skipped["large"] += 1
                    continue
                src = ev.read_text_file(entry.path, max_bytes=MAX_FILE_BYTES)
            except (ev.TextError, OSError):
                tree.skipped["unreadable"] += 1
                continue
            total += len(src.text)
            tree.docs.append(Doc(rel, src))
        stack.extend(reversed(subdirs))
    tree.docs.sort(key=lambda d: d.rel)
    return tree


# ---------------------------------------------------------------------------------------------------------------
# Finding strings
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Hit:
    rel: str
    line: int
    how: str
    offset: int = 0

    def at(self, repo: str) -> str:
        return f"{repo}:{self.rel}:{self.line}" if repo else f"{self.rel}:{self.line}"


_CLOSERS = "<\"'`"
_OPENERS = ">\"'`"
_NOT_A_NAME = re.compile(r"(?:type|autocomplete|inputmode|class|className|href|src|alt|title|placeholder|aria-label|value|label|for|rel|role)\s*=\s*\{?\s*[\"'`]?$",
                         re.IGNORECASE)
_ESCAPES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ('"', "&quot;"), ("'", "&#39;"))


def _html_escaped(value: str) -> str:
    for plain, esc in _ESCAPES:
        value = value.replace(plain, esc)
    return value


def _bounded(text: str, a: int, b: int) -> bool:
    """The string at ``text[a:b]`` stands alone: a tag, a quote or a backtick on both sides (white space aside)."""
    before = text[max(0, a - 80):a].rstrip()[-1:]
    after = text[b:b + 80].lstrip()[:1]
    return before in _OPENERS and after in _CLOSERS and before != "" and after != ""


def route_regex(route: str) -> "re.Pattern":
    """The route as a token of source text: not inside a longer word or path, ``{id}`` also written ``:id``, ``${id}``, ``[id]``."""
    parts = re.split(r"\{[^}]*\}", route)
    params = r"(?::\w+|\{[^}\n]*\}|\$\{[^}\n]*\}|\[\w+\]|<\w+>)"
    body = params.join(re.escape(p) for p in parts)
    if len(parts) == 1 and route != "/":
        body += "/?"
    return re.compile(r"(?<![\w.-])" + body + r"(?![\w-])(?!/[\w])")


def label_variants(label: str) -> list:
    escaped = _html_escaped(label)
    return [label] + ([escaped] if escaped != label else [])


class Finder:
    """Literal searches over the documents of a tree (each document is scanned once per probe, with a cheap substring test first)."""

    def __init__(self, docs: Sequence):
        self.docs = list(docs)
        self._ids: "dict | None" = None
        self._near: dict = {}
        self._by_rel = {d.rel: d for d in self.docs}

    # -- exact searches ----------------------------------------------------------------------------------------
    def label(self, label: str) -> "Hit | None":
        """The label as a whole string of the source: a text node, an attribute value or a quoted string (a tag or a quote on both sides). A label that is only
        a part of a longer string (``Sign in`` inside ``Sign in form``) is not found: the answer is ABSENT with the longer string as the closest one."""
        for doc in self.docs:
            for needle in label_variants(label):
                start, seen = 0, 0
                while seen < 50:
                    i = doc.text.find(needle, start)
                    if i < 0:
                        break
                    seen += 1
                    if not doc.ignored(i) and _bounded(doc.text, i, i + len(needle)):
                        return Hit(doc.rel, doc.src.line_of(i), "text", i)
                    start = i + 1
        return None

    def route(self, route: str, docs: "Sequence | None" = None, lines: "tuple | None" = None) -> "Hit | None":
        rx = route_regex(route)
        needle = route.split("{", 1)[0]
        for doc in (self.docs if docs is None else docs):
            if needle not in doc.text:
                continue
            text, base = doc.text, 0
            if lines is not None:
                text = "\n".join(doc.src.lines[max(0, lines[0] - 1):lines[1]])
                base = doc.src.starts[max(0, lines[0] - 1)] if doc.src.starts else 0
            m = rx.search(text)
            while m is not None and doc.ignored(base + m.start()):
                m = rx.search(text, m.end())
            if m:
                at = base + m.start()
                quoted = _bounded(doc.text, at, base + m.end())
                return Hit(doc.rel, doc.src.line_of(at), "quoted" if quoted else "token", at)
        return None

    def bind(self, name: str) -> "Hit | None":
        attr = re.compile(r"(?:\bname|\bid|data-[\w-]+|v-model(?:\.\w+)*|formControlName|ngModel|register)\s*[=(]\s*\{?\s*[\"'`]" + re.escape(name) + r"[\"'`]")
        quoted = re.compile(r"[\"'`]" + re.escape(name) + r"[\"'`]")
        word = re.compile(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])")
        for how, rx in (("attr", attr), ("quoted", quoted), ("word", word)):
            for doc in self.docs:
                if name not in doc.text:
                    continue
                for m in rx.finditer(doc.text):
                    if doc.ignored(m.start()) or (how != "attr" and _NOT_A_NAME.search(doc.text[max(0, m.start() - 24):m.start()])):
                        continue
                    offset = m.end() - len(name) - (1 if how in ("attr", "quoted") else 0)
                    return Hit(doc.rel, doc.src.line_of(offset), how, offset)
        return None

    # -- data-atlas-id --------------------------------------------------------------------------------------------
    def ids(self) -> dict:
        """``{id: [Hit, ...]}`` for every ``data-atlas-id`` attribute of the tree."""
        if self._ids is None:
            rx = re.compile(r"data-atlas-id\s*=\s*(?:\"([^\"\n]*)\"|'([^'\n]*)'|\{\s*[\"']([^\"'\n]*)[\"']\s*\})")
            found: dict = {}
            for doc in self.docs:
                if "data-atlas-id" not in doc.text:
                    continue
                for m in rx.finditer(doc.text):
                    if doc.ignored(m.start()):
                        continue
                    ident = next(g for g in m.groups() if g is not None)
                    found.setdefault(ident, []).append(Hit(doc.rel, doc.src.line_of(m.start()), "id", m.start()))
            self._ids = found
        return self._ids

    def doc(self, rel: str) -> "Doc | None":
        return self._by_rel.get(rel)

    # -- the closest string, for the ABSENT answer ------------------------------------------------------------------
    _PATTERNS = {
        "text": re.compile(r">([^<>{}\n]{1,60})<|[\"'`]([^\"'`\n<>]{1,60})[\"'`]"),
        "route": re.compile(r"[\"'`](/[A-Za-z0-9_./:{}$\[\]-]{0,80})[\"'`]"),
        "name": re.compile(r"(?:\bname|\bid|data-bind|v-model(?:\.\w+)*|formControlName|ngModel)\s*[=(]\s*\{?\s*[\"'`]([^\"'`\n]{1,60})[\"'`]"),
    }

    def _candidates(self, kind: str, docs: "Sequence | None" = None, window: "tuple | None" = None) -> list:
        """``[(string, (doc, line))]`` of the strings of a kind in the documents (outside comments), or only in the lines of ``window`` of the one document."""
        key = (kind, None if docs is None else tuple(d.rel for d in docs), window)
        if key in self._near:
            return self._near[key]
        rx = self._PATTERNS[kind]
        seen: dict = {}
        for doc in (self.docs if docs is None else docs):
            text, base, first = doc.text, 0, 1
            if window is not None:
                first = max(1, window[0])
                text = "\n".join(doc.src.lines[first - 1:window[1]])
                base = doc.src.starts[first - 1] if doc.src.starts else 0
            for m in rx.finditer(text):
                if doc.ignored(base + m.start()):
                    continue
                value = next((g for g in m.groups() if g is not None), "").strip()
                if value and value not in seen:
                    seen[value] = (doc, doc.src.line_of(base + m.start()))
                if len(seen) >= NEAR_CANDIDATES:
                    break
        out = sorted(seen.items(), key=lambda kv: (kv[1][0].rel, kv[1][1], kv[0]))
        self._near[key] = out
        return out

    def near(self, kind: str, wanted: str, docs: "Sequence | None" = None, window: "tuple | None" = None, cutoff: float = 0.5) -> "tuple | None":
        """``(string, rel, line)`` of the source string closest to ``wanted``, or ``None``. The string is shown only when its line passes ``echo_source_line``."""
        pool = self._candidates(kind, docs, window)
        by_text = {text: where for text, where in pool}
        pick = difflib.get_close_matches(wanted, list(by_text), n=1, cutoff=cutoff)
        if not pick:
            return None
        doc, line = by_text[pick[0]]
        shown = ev.echo_source_line(doc.rel, doc.src, line, 10_000)
        text = "[withheld]" if shown.startswith("[withheld") else (pick[0] if len(pick[0]) <= SHOWN_NEAR else pick[0][:SHOWN_NEAR - 1] + "\u2026")
        return text, doc.rel, line


# ---------------------------------------------------------------------------------------------------------------
# kind -> DOM hint
# ---------------------------------------------------------------------------------------------------------------

_TAG_RE = re.compile(r"<([A-Za-z][\w:-]*)((?:\"[^\"]*\"|'[^']*'|[^<>\"'])*)>")


def enclosing_tag(text: str, offset: int) -> "tuple | None":
    """``(tag name, attribute text)`` of the element a position belongs to: the tag the position is inside, or the opening tag right before a text node.
    ``None`` when it cannot be told (the text follows a closing tag, or there is no tag)."""
    lo = max(0, offset - 700)
    start = text.rfind("<", lo, offset)
    if start >= 0:
        m = _TAG_RE.match(text, start)
        if m and m.end() > offset:
            return m.group(1).lower(), m.group(2)
        if m is None and text.startswith("</", start):
            return None
    end = text.rfind(">", lo, offset)
    if end < 0:
        return None
    begin = text.rfind("<", lo, end)
    if begin < 0 or text.startswith("</", begin):
        return None
    m = _TAG_RE.match(text, begin)
    if m and m.end() == end + 1:
        return m.group(1).lower(), m.group(2)
    return None


def _attr(attrs: str, name: str) -> str:
    m = re.search(r"(?:^|\s)" + re.escape(name) + r"\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))", attrs, re.IGNORECASE)
    return next((g for g in m.groups() if g is not None), "").lower() if m else ""


def _fits(kind: str, tag: str, attrs: str) -> "bool | None":
    """Whether the tag can be a ``kind`` of the DOM table of ``ui-schema.md``; ``None`` for a kind the table cannot judge (any tag may carry it)."""
    role, typ = _attr(attrs, "role"), _attr(attrs, "type")
    if kind == "button":
        return tag == "button" or role == "button" or (tag == "input" and typ in ("submit", "button", "reset", "image"))
    if kind == "link":
        return tag == "a" or role == "link"
    if kind == "input":
        return (tag == "input" and typ not in ("checkbox", "radio")) or role in ("textbox", "searchbox", "spinbutton")
    if kind == "textarea":
        return tag == "textarea" or role == "textbox"
    if kind == "select":
        return tag == "select" or role in ("combobox", "listbox")
    if kind == "checkbox":
        return (tag == "input" and typ == "checkbox") or role == "checkbox"
    if kind == "radio":
        return (tag == "input" and typ == "radio") or role == "radio"
    if kind == "switch":
        return role == "switch" or (tag == "input" and typ == "checkbox")
    if kind == "heading":
        return tag in ("h1", "h2", "h3", "h4", "h5", "h6") or role == "heading"
    if kind == "table":
        return tag == "table" or role in ("grid", "table")
    if kind == "list":
        return tag in ("ul", "ol", "menu") or role == "list"
    if kind == "image":
        return tag in ("img", "svg", "picture") or role == "img"
    if kind == "form":
        return tag == "form" or role == "form"
    if kind == "header":
        return tag == "header" or role == "banner"
    if kind == "footer":
        return tag == "footer" or role == "contentinfo"
    if kind == "divider":
        return tag == "hr" or role == "separator"
    return None


#: Kinds whose label sits in a neighbouring element (``<label>``, a placeholder): the tag is taken from the field name or the id, never from the label.
FIELD_KINDS = frozenset({"input", "textarea", "select", "checkbox", "radio", "switch"})


# ---------------------------------------------------------------------------------------------------------------
# One scan
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class ScreenScan:
    screen: str
    screen_sha8: str
    ui_sha8: str
    literals: dict = field(default_factory=dict)      # ref -> {"state": FOUND|ABSENT, "at", "how", "near", "why"}
    hints: list = field(default_factory=list)         # {"ref", "dom", "want", "at"}
    ids: dict = field(default_factory=lambda: {"found": [], "absent": [], "extra": []})

    def counts(self) -> dict:
        found = sum(1 for v in self.literals.values() if v["state"] == "FOUND")
        return {"literals": len(self.literals), "found": found, "absent": len(self.literals) - found, "hints": len(self.hints),
                "ids": {k: len(v) for k, v in self.ids.items()}}


@dataclass
class ScanResult:
    repo: str
    glob: str
    files: int
    skipped: dict
    screens: dict                                      # screen id -> ScreenScan (model order)
    tagged: bool = False                               # the tree has at least one data-atlas-id

    def document(self, screen: str) -> dict:
        s = self.screens[screen]
        data = {"schema": SCHEMA, "screen": screen, "screenSha8": s.screen_sha8, "uiSha8": s.ui_sha8, "repo": self.repo, "glob": self.glob,
                "files": self.files, "skipped": dict(self.skipped), "counts": s.counts(), "literals": s.literals, "hints": s.hints, "ids": s.ids}
        return data

    def to_json(self, screen: str) -> bytes:
        return um.canonical_bytes(self.document(screen), indent=1)


def _located(finder: Finder, widget: Mapping, hits: dict) -> "tuple | None":
    """Where a widget was found: ``(Hit, how)`` by its ``data-atlas-id``, else by its label, else (a field) by its name; ``None`` when nowhere."""
    ids = finder.ids().get(widget["id"])
    if ids:
        return ids[0], "id"
    for ref, how in ((f"{widget['id']}@label", "label"), (f"{widget['id']}@bind", "bind")):
        if ref in hits:
            return hits[ref], how
    return None


def scan_screen(model: Mapping, screen_id: str, finder: Finder, repo: str, *, stamps: "Mapping | None" = None, points: "Sequence | None" = None,
                ui_sha8: "str | None" = None) -> ScreenScan:
    """The literal probes of one screen against ``finder`` (module doc)."""
    stamps = stamps if stamps is not None else um.screen_hashes(model)
    points = points if points is not None else um.checkpoints(model)
    screens = {s["id"]: s for s in model["screens"]}
    widgets = {w["id"]: w for w in model["widgets"]}
    out = ScreenScan(screen_id, stamps.get(screen_id, ""), um.ui_sha8(model) if ui_sha8 is None else ui_sha8)
    mine = [c for c in points if c.screen == screen_id]
    hits: dict = {}                                       # ref -> Hit of a FOUND label or bind
    goto_later: list = []
    for c in mine:
        column = c.ref.partition("@")[2]
        if column not in LITERAL_COLUMNS:
            continue
        if column == "goto":
            goto_later.append(c)
            continue
        hit = finder.route(c.value) if column == "route" else finder.label(c.value) if column == "label" else finder.bind(c.value)
        if hit is not None:
            hits[c.ref] = hit
            out.literals[c.ref] = {"state": "FOUND", "at": hit.at(repo), "how": hit.how}
            continue
        entry = {"state": "ABSENT"}
        near = finder.near("route" if column == "route" else "text" if column == "label" else "name", c.value)
        if near:
            entry["near"] = f"{near[0]} ({repo + ':' if repo else ''}{near[1]}:{near[2]})"
        out.literals[c.ref] = entry
    # data-atlas-id: found, absent, extra
    tagged = finder.ids()
    prefix = screen_id + "."
    mine_widgets = [wid for wid in screens[screen_id]["widgetIds"] if wid in widgets] if screen_id in screens else []
    if tagged:
        out.ids["found"] = [wid for wid in mine_widgets if wid in tagged]
        out.ids["absent"] = [wid for wid in mine_widgets if wid not in tagged]
        out.ids["extra"] = sorted(i for i in tagged if i.startswith(prefix) and i not in widgets)
    # goto: the route of every target near the place where the widget was found
    for c in goto_later:
        wid = c.ref.partition("@")[0]
        widget = widgets.get(wid)
        place = _located(finder, widget, hits) if widget else None
        targets = [t for t in c.value.split(",") if t]
        routes = []
        for t in targets:
            target = screens.get(t.split("#", 1)[0])
            if target is not None and target.get("route"):
                routes.append(target["route"])
        if not routes:
            continue                                      # nothing literal to look for: not a probe, not counted
        if place is None:
            out.literals[c.ref] = {"state": "ABSENT", "why": "the widget was not found, so there is no place to look for its target"}
            continue
        hit = place[0]
        doc = finder.doc(hit.rel)
        window = (max(1, hit.line - GOTO_WINDOW), hit.line + GOTO_WINDOW)
        missing, last = [], None
        for route in routes:
            found = finder.route(route, [doc], window) if doc is not None else None
            if found is None:
                missing.append(route)
            else:
                last = found
        if not missing and last is not None:
            out.literals[c.ref] = {"state": "FOUND", "at": last.at(repo), "how": "window"}
        else:
            entry = {"state": "ABSENT", "why": "no route of " + ", ".join(missing) + f" within {GOTO_WINDOW} lines of the widget"}
            near = finder.near("route", missing[0], [doc], window, 0.0) if doc is not None else None       # any route written near the widget tells where it leads
            if near:
                entry["near"] = f"{near[0]} ({repo + ':' if repo else ''}{near[1]}:{near[2]})"
            out.literals[c.ref] = entry
    # kind -> DOM hints (never a statement that the tag is right)
    for wid in mine_widgets:
        widget = widgets[wid]
        kind = widget["kind"]
        place = None
        ids = finder.ids().get(wid)
        if ids:
            place = ids[0]
        elif kind in FIELD_KINDS:
            place = hits.get(f"{wid}@bind") if (out.literals.get(f"{wid}@bind") or {}).get("how") == "attr" else None
        elif (out.literals.get(f"{wid}@label") or {}).get("how") == "text":
            place = hits.get(f"{wid}@label")
        if place is None:
            continue
        doc = finder.doc(place.rel)
        tag = enclosing_tag(doc.text, place.offset) if doc is not None else None
        if tag is None:
            continue
        verdict = _fits(kind, tag[0], tag[1])
        if verdict is False:
            out.hints.append({"ref": f"{wid}@kind", "dom": tag[0], "want": kind, "at": place.at(repo)})
    out.hints.sort(key=lambda h: h["ref"])
    return out


def scan(ui_model: Mapping, root: "Path | str", *, repo: str = "", glob: str = DEFAULT_GLOB, screens: "Iterable[str] | None" = None) -> ScanResult:
    """Scan the source tree at ``root`` for the literals of the prototype ``ui_model`` (every screen, or only ``screens``)."""
    tree = collect(root, glob)
    finder = Finder(tree.docs)
    stamps, points, sha = um.screen_hashes(ui_model), um.checkpoints(ui_model), um.ui_sha8(ui_model)
    wanted = [s["id"] for s in ui_model.get("screens", ()) if screens is None or s["id"] in set(screens)]
    result = ScanResult(repo, glob, len(tree.docs), dict(tree.skipped), {}, bool(finder.ids()))
    for sid in wanted:
        result.screens[sid] = scan_screen(ui_model, sid, finder, repo, stamps=stamps, points=points, ui_sha8=sha)
    return result


# ---------------------------------------------------------------------------------------------------------------
# The report (plain text for the shell; no word of it says that something is right)
# ---------------------------------------------------------------------------------------------------------------


def report_lines(result: ScanResult, *, show_all: bool = False) -> list:
    lines = [f"scan {result.repo}: {result.files} files, glob {result.glob}"
             + (f"; not read: {', '.join(f'{k} {v}' for k, v in sorted(result.skipped.items()) if v)}" if any(result.skipped.values()) else "")]
    for sid, s in result.screens.items():
        c = s.counts()
        lines.append(f"{sid} sha={s.screen_sha8} literals {c['literals']}: FOUND {c['found']}, ABSENT {c['absent']}"
                     + (f"; ids: {c['ids']['found']} found, {c['ids']['absent']} absent, {c['ids']['extra']} extra" if result.tagged else "; no data-atlas-id in the tree"))
        for ref, v in s.literals.items():
            if v["state"] == "FOUND" and show_all:
                lines.append(f"  FOUND {ref} {v['at']}")
            elif v["state"] == "ABSENT":
                tail = f" near:{v['near']}" if v.get("near") else ""
                tail += f" ({v['why']})" if v.get("why") else ""
                lines.append(f"  ABSENT {ref}{tail}")
        for h in s.hints:
            lines.append(f"  kind? {h['ref']} {h['want']} is <{h['dom']}> ({h['at']})")
        if result.tagged:
            for key in ("absent", "extra"):
                if s.ids[key]:
                    shown = ", ".join(s.ids[key][:5]) + (f" +{len(s.ids[key]) - 5}" if len(s.ids[key]) > 5 else "")
                    lines.append(f"  ids {key}: {shown}")
    lines.append("hints only: a FOUND literal says the string exists in the source, not that its element is the right one; parent, order, emph, in, state and api need the code read against the card")
    return lines


# ---------------------------------------------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class ScanRun:
    exit_code: int
    text: str
    written: list = field(default_factory=list)       # paths relative to the project's atlas directory, posix
    result: "ScanResult | None" = None


def run(ws, project: str, *, repo: str, glob: str = DEFAULT_GLOB, screens: "Sequence[str] | None" = None, dry_run: bool = False,
        show_all: bool = False, roots: "Mapping | None" = None) -> ScanRun:
    """Scan the committed prototype of ``project`` against the root ``repo`` (an alias) and write ``scan-<enc>.json`` per screen unless ``dry_run``.

    Returns the exit code (0 scanned, 1 refused) and the text to print; never raises for a refused request."""
    try:
        try:
            rt.require_alias(repo, "--repo")
        except rt.RootsError:
            # the value is not echoed: it may be a path, and a path of the machine must not be in any output
            return ScanRun(1, "error: --repo takes the alias of an evidence root (a name declared in GEMARC_ATLAS_ROOTS), never a path")
        table = dict(rt.roots_from_env()) if roots is None else dict(roots)
        if repo not in table or not rt.root_available(table[repo]):
            return ScanRun(1, f"error: no evidence root for alias {repo!r}: the operator sets GEMARC_ATLAS_ROOTS {repo}=<dir> in the server environment")
        paths = pj.project_paths(ws, project)
        cfg = pj.load_config(paths)
        upaths = store.UiPaths.of(paths)
        state = store.load_state(upaths, cfg.meta())
        if not state.screens:
            return ScanRun(1, "error: the project has no screen yet: put the ui-* slices first")
        unknown = sorted(set(screens or ()) - {s["id"] for s in state.model["screens"]})
        if unknown:
            return ScanRun(1, f"error: not a screen of the prototype: {', '.join(unknown[:3])}")
        if not dry_run:
            ws.writable()
        result = scan(state.model, table[repo], repo=repo, glob=glob, screens=screens or None)
        written = []
        if not dry_run:
            enc_of = reading.enc_many([s["id"] for s in state.model["screens"]])
            for sid in result.screens:
                store.write_scan(upaths, enc_of[sid], result.to_json(sid), root=ws.root)
                written.append(f".atlas/ui/scan-{enc_of[sid]}.json")
        lines = report_lines(result, show_all=show_all)
        lines.extend(f"wrote {w}" for w in written)
        if dry_run:
            lines.append("dry run: no scan file was written")
        return ScanRun(0, "\n".join(lines), written, result)
    except GemArcError as ex_:
        if ex_.code == "GEMARC_AUTH_002":
            return ScanRun(1, "error: writes are off, so no scan file can be written: the operator sets GEMARC_ALLOW_WRITE=1, or use --dry-run to only print the report")
        return ScanRun(1, f"error: {ex_.code}: {str(ex_.message)[:300]}")
    except (rt.RootsError, store.UiError, ValueError) as ex_:
        message = getattr(ex_, "message", None) or str(ex_)
        return ScanRun(1, "error: " + str(message)[:300])


def build_parser(prog: str = "python -m gemarc_mcp.atlas.protoui.scan") -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=prog, description="Literal probes of a prototype against a frontend source tree (a hint, never a verdict).")
    p.add_argument("project")
    p.add_argument("--repo", required=True, help="alias of the frontend root in GEMARC_ATLAS_ROOTS (never a path)")
    p.add_argument("--glob", default=DEFAULT_GLOB, help="files to read, relative to the root (default %(default)s)")
    p.add_argument("--screen", action="append", default=None, help="scan only this screen (repeatable)")
    p.add_argument("--all", action="store_true", help="also list the literals that were found")
    p.add_argument("--dry-run", action="store_true", help="print the report, write no scan file")
    p.add_argument("--root", default="", help="workspace root (default: GEMARC_WORKSPACE_ROOT)")
    return p


def main(argv: "Sequence[str] | None" = None, prog: str = "python -m gemarc_mcp.atlas.protoui.scan") -> int:
    args = build_parser(prog).parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        ws = pj.get_workspace(args.root or None, create=False)
    except GemArcError as ex_:
        print("error: " + str(ex_.message)[:300], file=sys.stderr)
        return 1
    done = run(ws, args.project, repo=args.repo, glob=args.glob, screens=args.screen, dry_run=args.dry_run, show_all=args.all)
    sys.stdout.buffer.write((done.text + "\n").encode("utf-8"))
    sys.stdout.flush()
    return done.exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
