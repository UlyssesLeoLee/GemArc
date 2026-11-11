"""HTML twins of the Markdown pages (design A3 2.5, 2.6, D-2; milestone R2).

A twin is the pure post-processing of one finished Markdown page: ``site/<path without .md>.html`` for ``START.md``, ``read/**``,
``screens/INDEX*.md`` and ``screens/NAV*.md`` (``names.twin_path``). It is for a browser and for ``WebFetch``, never for a shell (``cat``
of a 17 KB HTML is cut in the middle); an AI reads the Markdown. The twin carries the same visible text as the page, no script, no external
reference, one ``<style>``. Cutting the pages never looks at a twin, so ``html=False`` changes no Markdown byte.

The one exception of D-2 is the screen card: its twin is the gallery page ``site/screens/<enc>.html``, written by lane U (``protoui.gallery``).
``render_site`` therefore skips card pages unless the caller hands in a ``gallery`` hook ``hook(card_path, card_text) -> bytes | None``; the
hook is how lane U fills the exception. Link rewriting still points to the gallery for a link to a card.

Structure (one Markdown physical line becomes one HTML block, so the two atom sequences line up by construction):

====================  ====================================================================================================================
``# ..`` ``######``   ``<h1>`` .. ``<h6>``; a heading with a level of 2 or more that is directly followed by a table header becomes that
                      table's ``<caption>`` (the heading text is not repeated). A table inside a block (a case's events, the transitions of a
                      machine, the columns of an entity) has no heading line of its own, so it has no caption: it lies inside the ``<section>``
                      of its block (RS-107 scopes its heading rule the same way, RS-110 follows it)
``### x`` + `` `id` ``  a block: ``<section id="enc(id)">`` holding ``<h3>``, the identity line and what follows, until the next heading of level
                      1 to 3
``> ..``              one ``<aside>`` with a ``<p>`` per line (the scope card)
``- ..``              one ``<ul>`` with a ``<li>`` per line
table                 ``<div class="w"><table>`` with ``<thead>``, ``<tbody>``; ``<tr id="enc(id)">`` when the first cell is one code span (the
                      first use of an id in the page wins, so every ``id`` of a twin is unique by construction)
fence                 ``<pre><code class="language-..">`` (the Mermaid block of NAV)
other line            ``<p>``; a ``（续）`` continuation line stays its own ``<p>`` (the twin does not unfold what the Markdown folded)
====================  ====================================================================================================================

Inline text (one function, ``tokenize``, serves the renderer and ``visible_md``): a code span is taken raw (inside a table ``\\|`` is ``|``),
a backslash before an ASCII punctuation character is that character, ``&amp; &lt; &gt;`` are the three characters, ``[text](href)`` is a link,
everything else is literal. Author text can contain ``*``, ``**`` or ``_``: they stay literal on both sides (design 2.5 says ``visible``
strips ``**``; doing that on the Markdown side alone would let author text turn an I-class check red, so neither side does).

``visible_md(text)`` and ``visible_html(data)`` give the atom lists RS-114 compares: the first line and the sentinel are not compared (they
differ by design: the twin counts blocks and tables where the page counts lines); every other line of the page is one atom, a table row is one
atom per cell, whitespace is collapsed. ``analyse(html)`` is the one structural reading of a twin (ids, tables, scripts, meta, sizes) that the
checks and the tests use. Standard library only.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from .. import htmlassemble as H
from . import blocks as B
from . import names as N
from .budgets import BUDGETS
from .envelope import first_line_regex, sentinel_regex
from .parse import parse_page
from .text import tx

#: the policy of every twin: the constant of the viewer page, imported (never copied), so one change moves both
CSP_POLICY = H.CSP_POLICY
SCHEMA = "gemarc-atlas-read/1"
LINE_LIMIT = BUDGETS["wrap_chars"]

#: the style of every twin: one rule per line, the same bytes on every page (RS-110)
STYLE_LINES = (
    "body{font:15px/1.6 system-ui,'Microsoft YaHei','PingFang SC',sans-serif;max-width:64rem;margin:0 auto;padding:8px 16px;color:#1f2328}",
    "header,footer{color:#57606a;font-size:13px}",
    "h1,h2,h3,h4{margin:.8em 0 .3em}",
    "h2{border-bottom:1px solid #d0d7de}",
    "aside{background:#f6f8fa;border-left:3px solid #d0d7de;padding:2px 12px}",
    ".w{overflow-x:auto}",
    "table{border-collapse:collapse;margin:8px 0}",
    "caption{text-align:left;font-weight:600}",
    "th,td{border:1px solid #d0d7de;padding:2px 8px;text-align:left;vertical-align:top}",
    "th{background:#f6f8fa}",
    "code{font-family:Consolas,monospace;background:#f6f8fa}",
    "pre{background:#f6f8fa;overflow-x:auto}",
)

PUNCT = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
ENTITY = re.compile(r"&(amp|lt|gt);")
ENTITY_CHAR = {"amp": "&", "lt": "<", "gt": ">"}
HEADING = re.compile(r"^(#{1,6}) (.*)$")
CODE_LEAD = re.compile(r"^`([^`\n]+)`")
PAGE_TOKEN = re.compile(N.SECTION_SIGN + "(" + N.PAGE_ID_BODY + ")")
CARD_PATH = re.compile(r"^screens/[^/]+\.card(?:-\d+)?\.md$")
FIRST_PAGE = re.compile(N.SECTION_SIGN + r"([^\s·]+)")
CLASS_SAFE = re.compile(r"^[A-Za-z0-9_-]+$")
BASE64_RUN = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")


# ---------------------------------------------------------------------------------------------------- inline text

def _link_at(text: str, i: int):
    """``(end, label, href)`` of the link that starts at ``text[i] == "["``, or ``None``: the label ends at the first unescaped ``]``, the
    address follows at once in parentheses and holds no white space and no ``)``."""
    j = i + 1
    n = len(text)
    while j < n:
        ch = text[j]
        if ch == "\\":
            j += 2
            continue
        if ch == "]":
            break
        j += 1
    if j >= n or text[j] != "]" or j + 1 >= n or text[j + 1] != "(":
        return None
    k = j + 2
    while k < n and text[k] not in ") \t\n":
        k += 1
    if k >= n or text[k] != ")":
        return None
    return k + 1, text[i + 1:j], text[j + 2:k]


def tokenize(text: str, table: bool = False) -> list:
    """``[("t", text) | ("c", code) | ("a", tokens, href)]`` of one line of inline Markdown (the module docstring says what is recognised)."""
    out: list = []
    buf: list = []

    def flush() -> None:
        if buf:
            out.append(("t", "".join(buf)))
            buf.clear()

    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "`":
            j = text.find("`", i + 1)
            if j > i + 1:
                flush()
                content = text[i + 1:j]
                out.append(("c", content.replace("\\|", "|") if table else content))
                i = j + 1
                continue
        elif ch == "\\" and i + 1 < n and text[i + 1] in PUNCT:
            buf.append(text[i + 1])
            i += 2
            continue
        elif ch == "[":
            link = _link_at(text, i)
            if link is not None:
                flush()
                end, label, href = link
                out.append(("a", tokenize(label, table), href))
                i = end
                continue
        elif ch == "&":
            m = ENTITY.match(text, i)
            if m:
                buf.append(ENTITY_CHAR[m.group(1)])
                i = m.end()
                continue
        buf.append(ch)
        i += 1
    flush()
    return out


def tokens_text(tokens) -> str:
    return "".join(tok[1] if tok[0] in ("t", "c") else tokens_text(tok[1]) for tok in tokens)


def norm(text: str) -> str:
    return " ".join(text.split())


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def attr(text: str) -> str:
    return esc(text).replace('"', "&quot;")


# ---------------------------------------------------------------------------------------------------- blocks

@dataclass
class Blk:
    kind: str                       # h | quote | list | table | p | fence
    level: int = 0
    text: str = ""
    lines: list = field(default_factory=list)       # quote and list lines (prefix removed), fence lines
    header: list = field(default_factory=list)      # table: raw cells of the header row
    rows: list = field(default_factory=list)        # table: raw cells per row
    caption: str | None = None                      # table: the raw text of the heading it absorbed
    caption_level: int = 0
    info: str = ""                                  # fence info string


def parse_blocks(lines) -> list:
    """The blocks of Markdown lines (the grammar the generators write; see the module docstring). Pure, never raises."""
    lines = list(lines)
    n = len(lines)
    out: list = []
    i = 0

    def table_at(k: int, caption=None, level: int = 0):
        header = B.split_cells(lines[k])
        j = k + 2
        rows = []
        while j < n and B.is_row(lines[j]):
            rows.append(B.split_cells(lines[j]))
            j += 1
        return Blk("table", header=header, rows=rows, caption=caption, caption_level=level), j

    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith("```"):
            j = i + 1
            body = []
            while j < n and lines[j].strip() != "```":
                body.append(lines[j])
                j += 1
            out.append(Blk("fence", lines=body, info=line[3:].strip()))
            i = j + 1
            continue
        m = HEADING.match(line)
        if m:
            level = len(m.group(1))
            if level >= 2 and i + 2 < n and B.is_row(lines[i + 1]) and B.is_separator(lines[i + 2]):
                blk, i = table_at(i + 1, m.group(2), level)
                out.append(blk)
            else:
                out.append(Blk("h", level=level, text=m.group(2)))
                i += 1
            continue
        if B.is_row(line) and i + 1 < n and B.is_separator(lines[i + 1]):
            blk, i = table_at(i)
            out.append(blk)
            continue
        if line == ">" or line.startswith("> "):
            items = []
            while i < n and (lines[i] == ">" or lines[i].startswith("> ")):
                items.append(lines[i][2:] if lines[i].startswith("> ") else "")
                i += 1
            out.append(Blk("quote", lines=items))
            continue
        if line.startswith("- "):
            items = []
            while i < n and lines[i].startswith("- "):
                items.append(lines[i][2:])
                i += 1
            out.append(Blk("list", lines=items))
            continue
        out.append(Blk("p", text=line))
        i += 1
    return out


def block_atoms(blocks, content: bool = False) -> list:
    """The visible atoms of parsed blocks, in page order. With ``content`` only what a page *says* is kept: table cells (not the caption), list
    items, plain lines and code-block lines, never a heading or a scope-card line."""
    atoms: list = []

    def inline(text: str, table: bool = False) -> str:
        return norm(tokens_text(tokenize(text, table)))

    for blk in blocks:
        if blk.kind == "h":
            if not content:
                atoms.append(inline(blk.text))
        elif blk.kind in ("quote", "list"):
            if blk.kind == "list" or not content:
                atoms.extend(inline(item) for item in blk.lines)
        elif blk.kind == "table":
            if blk.caption is not None and not content:
                atoms.append(inline(blk.caption))
            atoms.extend(inline(cell, True) for cell in blk.header)
            for row in blk.rows:
                atoms.extend(inline(cell, True) for cell in row)
        elif blk.kind == "p":
            atoms.append(inline(blk.text))
        else:
            atoms.extend(norm(line) for line in blk.lines if line.strip())
    return atoms


def _body_lines(text: str) -> list:
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    return lines


def visible_md(text: str) -> list:
    """The atoms of a Markdown page without its first line and its sentinel (the two lines a twin words differently)."""
    lines = _body_lines(text)
    return block_atoms(parse_blocks(lines[1:-1]))


def content_atoms(path: str, text: str, lang: str) -> list:
    """The atoms of the body of a page that are its content (``block_atoms(content=True)`` of the lines between the frame): what a gallery
    page of lane U must still hold of a screen card (RS-114, the exception of D-2)."""
    page = parse_page(lang, path, text)
    if page is None:
        return []
    none = tx(lang, "q.none")        # the explicit empty state under a heading that counts 0: the heading says it, the gallery need not repeat it
    return [atom for atom in block_atoms(parse_blocks(page.body), content=True) if atom and atom != none]


def row_id(cells) -> str | None:
    """The id a table row carries: the first cell when it is exactly one code span."""
    if not cells:
        return None
    m = re.fullmatch(r"`([^`\n]+)`", cells[0])
    return m.group(1).replace("\\|", "|") if m else None


def block_ids(blocks) -> list:
    """The ``id`` attributes of a twin in document order: the identity line of a block (``### x`` followed by a line that starts with a code
    span) and the first cell of a row; the first use of an id wins, a later one carries none. ``assign_ids`` and the renderer agree because
    the renderer calls this function."""
    return [identifier for _slot, identifier in _id_slots(blocks)]


def _id_slots(blocks) -> list:
    used: set = set()
    out: list = []

    def take(slot, raw):
        if raw is None:
            return
        name = N.enc(raw)
        if name not in used:
            used.add(name)
            out.append((slot, name))

    for bi, blk in enumerate(blocks):
        if blk.kind == "h" and blk.level == 3 and bi + 1 < len(blocks) and blocks[bi + 1].kind == "p":
            m = CODE_LEAD.match(blocks[bi + 1].text)
            if m:
                take(("sec", bi), m.group(1))
        elif blk.kind == "table":
            for ri, row in enumerate(blk.rows):
                take(("row", bi, ri), row_id(row))
    return out


# ---------------------------------------------------------------------------------------------------- site index and links

class SiteIndex:
    """What a twin needs to know about the other pages of the site: the page id of every Markdown file (so ``§S07`` can be a link) and the
    set of Markdown files that have a twin (so a link to one points to its ``.html``)."""

    def __init__(self, texts: dict):
        self.pages: dict = {}
        self.twins: dict = {}
        for path, text in sorted(texts.items()):
            first = text.split("\n", 1)[0]
            m = FIRST_PAGE.search(first)
            if m and N.is_page_id(m.group(1)):
                self.pages.setdefault(m.group(1), path)
            tp = N.twin_path(path)
            if tp is not None:
                self.twins[path] = tp


def relative(target: str, start_dir: str) -> str:
    return posixpath.relpath(target, start_dir or ".")


class _Links:
    def __init__(self, md_path: str, index: SiteIndex):
        self.index = index
        self.md_dir = posixpath.dirname(md_path)
        self.twin_dir = posixpath.dirname(N.twin_path(md_path) or md_path)

    def href(self, href: str) -> str:
        """The address of a link of the page, as written in the twin."""
        if "://" in href or href.startswith(("#", "mailto:")) or not href:
            return href
        base, hash_, frag = href.partition("#")
        target = posixpath.normpath(posixpath.join(self.md_dir, base)) if self.md_dir else posixpath.normpath(base)
        if target.startswith("..") or target.startswith("/"):
            return href
        dest = self.index.twins.get(target) or target
        return relative(dest, self.twin_dir) + (hash_ + frag if hash_ else "")

    def page(self, pid: str) -> str | None:
        path = self.index.pages.get(pid)
        if path is None:
            return None
        dest = self.index.twins.get(path)
        return relative(dest, self.twin_dir) if dest else None


def render_tokens(tokens, links: _Links, linked: bool = False) -> str:
    out: list = []
    for tok in tokens:
        kind = tok[0]
        if kind == "c":
            out.append("<code>" + esc(tok[1]) + "</code>")
        elif kind == "a":
            out.append('<a href="' + attr(links.href(tok[2])) + '">' + render_tokens(tok[1], links, True) + "</a>")
        elif linked:
            out.append(esc(tok[1]))
        else:
            out.append(_text_with_refs(tok[1], links))
    return "".join(out)


def _text_with_refs(text: str, links: _Links) -> str:
    """Escaped text in which every ``§<page id>`` of a page of the site is a link to its twin (a plain text for an unknown page)."""
    out: list = []
    pos = 0
    for m in PAGE_TOKEN.finditer(text):
        href = links.page(m.group(1))
        if href is None:
            continue
        out.append(esc(text[pos:m.start()]))
        out.append('<a href="' + attr(href) + '">' + esc(m.group(0)) + "</a>")
        pos = m.end()
    out.append(esc(text[pos:]))
    return "".join(out)


# ---------------------------------------------------------------------------------------------------- physical lines

_TAG = re.compile(r"<[^>]*>")


def fold_html(line: str, limit: int = LINE_LIMIT) -> list:
    """``line`` as physical lines of at most ``limit`` characters where the markup allows it, without changing a single visible character:
    a break goes after a cell (``</td>``, ``</th>``), in place of a space of a text, or inside a tag before its ``>`` (``<code\\n>`` is the same
    element). Never inside a word and never between two characters of a text (a line feed there is a space for the browser)."""
    if len(line) <= limit:
        return [line]
    breaks: list = []                  # (position, kind): "cut" after a cell, "space" a text space, "tag" before the > of a tag
    pos = 0
    for m in _TAG.finditer(line):
        for k in range(pos, m.start()):
            if line[k] == " ":
                breaks.append((k, "space"))
        tag = m.group(0)
        breaks.append((m.end() - 1, "tag"))
        if tag in ("</td>", "</th>"):
            breaks.append((m.end(), "cut"))
        pos = m.end()
    for k in range(pos, len(line)):
        if line[k] == " ":
            breaks.append((k, "space"))
    out: list = []
    start = 0
    breaks.sort()
    while len(line) - start > limit:
        window = [b for b in breaks if start < b[0] <= start + limit]
        if not window:
            return [line]
        position, kind = window[-1]
        out.append(line[start:position])
        start = position + 1 if kind == "space" else position
    out.append(line[start:])
    return out if max(len(piece) for piece in out) <= limit else [line]    # a fold that fixes nothing is not made


# ---------------------------------------------------------------------------------------------------- rendering

def _render_main(blocks, links: _Links) -> tuple:
    """``(lines, sections, tables)``: the body of ``<main>`` for the blocks of a page."""
    slots = dict(_id_slots(blocks))
    out: list = []
    sections = tables = 0
    open_section = False

    def close() -> None:
        nonlocal open_section
        if open_section:
            out.append("</section>")
            open_section = False

    def inline(text: str, table: bool = False) -> str:
        return render_tokens(tokenize(text, table), links)

    for bi, blk in enumerate(blocks):
        kind = blk.kind
        if kind == "h":
            if blk.level <= 3:
                close()
            ident = slots.get(("sec", bi))
            if blk.level == 3 and bi + 1 < len(blocks) and blocks[bi + 1].kind == "p" and CODE_LEAD.match(blocks[bi + 1].text):
                out.append("<section" + (f' id="{attr(ident)}"' if ident else "") + ">")
                open_section = True
                sections += 1
            out.extend(fold_html(f"<h{blk.level}>{inline(blk.text)}</h{blk.level}>"))
        elif kind == "quote":
            out.append("<aside>")
            for item in blk.lines:
                out.extend(fold_html("<p>" + inline(item) + "</p>"))
            out.append("</aside>")
        elif kind == "list":
            out.append("<ul>")
            for item in blk.lines:
                out.extend(fold_html("<li>" + inline(item) + "</li>"))
            out.append("</ul>")
        elif kind == "table":
            if blk.caption is not None and blk.caption_level <= 3:
                close()
            tables += 1
            out.append('<div class="w"><table>')
            if blk.caption is not None:
                out.extend(fold_html("<caption>" + inline(blk.caption) + "</caption>"))
            out.extend(fold_html("<thead><tr>" + "".join(f"<th>{inline(cell, True)}</th>" for cell in blk.header) + "</tr></thead>"))
            out.append("<tbody>")
            for ri, row in enumerate(blk.rows):
                ident = slots.get(("row", bi, ri))
                out.extend(fold_html("<tr" + (f' id="{attr(ident)}"' if ident else "") + ">" + "".join(f"<td>{inline(cell, True)}</td>" for cell in row) + "</tr>"))
            out.append("</tbody></table></div>")
        elif kind == "fence":
            cls = f' class="language-{blk.info}"' if blk.info and CLASS_SAFE.match(blk.info) else ""
            body = "\n".join(esc(line) for line in blk.lines)
            out.append("<pre>")
            out.append(f"<code{cls}>{body}</code></pre>")
        else:
            out.extend(fold_html("<p>" + inline(blk.text) + "</p>"))
    close()
    return out, sections, tables


def detect_lang(text: str) -> str | None:
    """The language a finished page says it is in: the second ``·``-separated token of its second line (``project · lang · stamps ...``), ``None``
    when that is not one of the languages. The pages of the screens are in the language of the interface model, which may differ from the
    primary language of the project, so a twin is made in the language of its own page, not in the one the caller passes."""
    parts = text.split("\n", 2)[1].split(" · ") if text.count("\n") >= 1 else []
    return parts[1] if len(parts) > 2 and parts[1] in N.LANGS else None


def render_twin(path: str, text: str, lang: str, index: SiteIndex | None = None) -> bytes:
    """The twin of one finished Markdown page (``path`` relative to ``dist/``); ``ValueError`` when the page has no readable frame (a generator
    bug, RS-103 reports it). ``lang`` is the language of the fixed texts unless the page names its own (``detect_lang``)."""
    twin_path = N.twin_path(path)
    if twin_path is None:
        raise ValueError(f"{path!r} has no HTML twin")
    lang = detect_lang(text) or lang
    page = parse_page(lang, path, text)
    if page is None:
        raise ValueError(f"{path!r}: the frame of the page cannot be read")
    index = index or SiteIndex({path: text})
    links = _Links(path, index)
    lines = page.lines
    sentinel = sentinel_regex(lang).match(lines[-1])
    if sentinel is None:
        raise ValueError(f"{path!r}: the last line is not a sentinel")
    main, sections, tables = _render_main(parse_blocks(lines[2:page.foot_start]), links)
    counts = tx(lang, "twin.counts", blocks=sections, tables=tables)
    stamps = " · ".join(f"{k}={v}" for k, v in page.stamps.items())
    shown = first_line_regex(lang).match(lines[0]).group("title")
    first = tx(lang, "twin.head", page=page.pid, title=shown, i=page.index, n=page.count, counts=counts, cls=page.cls)
    last = tx(lang, "twin.sentinel", page=page.pid, counts=counts, sha8=sentinel.group("sha8"))
    foot: list = []
    neighbours_prefix = tx(lang, "foot.neighbours", links="")
    for line in lines[page.foot_start:-1]:
        if not line.strip():
            continue
        body = "<p>" + render_tokens(tokenize(line), links) + "</p>"
        if line.startswith(neighbours_prefix):
            foot.append("<nav>")
            foot.extend(fold_html(body))
            foot.append("</nav>")
        else:
            foot.extend(fold_html(body))
    title = f"{N.SECTION_SIGN}{page.pid} · {shown}" + (f" · {page.index}/{page.count}" if page.count > 1 else "")
    doc = ["<!doctype html>", f'<html lang="{attr(page.lang)}">', "<head>",
           f'<meta http-equiv="Content-Security-Policy" content="{attr(CSP_POLICY)}">', '<meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width,initial-scale=1">', f"<title>{esc(title)}</title>",
           f'<meta name="atlas-page" content="{attr(page.pid)}">', f'<meta name="atlas-stamp" content="{attr(stamps)}">',
           f'<meta name="atlas-class" content="{attr(page.cls)}">', f'<meta name="atlas-lang" content="{attr(page.lang)}">',
           f'<meta name="atlas-schema" content="{SCHEMA}">',
           f'<link rel="alternate" type="text/markdown" href="{attr(relative(path, posixpath.dirname(twin_path)))}">',
           "<style>", *STYLE_LINES, "</style>", "</head>", "<body>", "<header>",
           *fold_html("<p>" + esc(first) + "</p>"), *fold_html("<p>" + render_tokens(tokenize(lines[1]), links) + "</p>"), "</header>",
           "<main>", *main, "</main>", "<footer>", *foot, *fold_html("<p>" + esc(last) + "</p>"), "</footer>", "</body>", "</html>"]
    return ("\n".join(doc) + "\n").encode("utf-8")


def render_site(files, lang: str, gallery=None, context=None, problems=None) -> dict:
    """``{twin path: bytes}`` for every Markdown file of ``files`` (``{path: bytes | str}``) that has a twin. Screen cards are skipped: their twin
    is the gallery page of lane U (``gallery(card_path, card_text)`` may return its bytes, the first part of a card only); links to a card point
    to the gallery in any case. ``context`` holds more Markdown files of the same site (``{path: bytes | str}``) whose twins are not rendered
    here but which the pages of ``files`` link to and name by page id: lane I renders the screens INDEX and NAV of lane U with the files of
    ``reading.build`` as ``context``, so that their links to START and to the pages are links to twins. ``lang`` is the language of the pages that do
    not name their own. A page whose frame cannot be read has no
    twin: with a ``problems`` list the failure is appended to it as ``(path, message)`` and the other pages are rendered, without one it raises
    ``ValueError`` (``reading.build`` passes a list and turns each entry into an I-class finding)."""
    def text_of(data):
        return data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else data

    wanted = {p: text_of(d) for p, d in files.items() if p.endswith(".md") and N.twin_path(p) is not None}
    known = {p: text_of(d) for p, d in (context or {}).items() if p.endswith(".md") and N.twin_path(p) is not None}
    known.update(wanted)
    index = SiteIndex(known)
    out: dict = {}
    for path in sorted(wanted):
        twin = N.twin_path(path)
        if CARD_PATH.match(path):
            if gallery is not None and twin not in out:
                data = gallery(path, wanted[path])
                if data is not None:
                    out[twin] = data
            continue
        try:
            out[twin] = render_twin(path, wanted[path], lang, index)
        except ValueError as error:
            if problems is None:
                raise
            problems.append((path, str(error)))
    return out


# ---------------------------------------------------------------------------------------------------- reading a twin

BLOCK_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th", "caption", "pre", "figcaption", "dt", "dd"})
FORBIDDEN_TAGS = frozenset({"script", "iframe", "frame", "object", "embed", "form", "base", "img", "audio", "video", "input", "button", "textarea"})
URL_ATTRS = frozenset({"href", "src", "action", "data", "poster", "srcset", "formaction", "xlink:href", "background", "cite", "longdesc"})


@dataclass
class Analysis:
    """One structural reading of a twin (``analyse``)."""

    atoms: list = field(default_factory=list)        # (zone, text): zone "header" | "main" | "footer"
    ids: list = field(default_factory=list)
    sections: int = 0
    tables: int = 0
    rows: int = 0
    table_problems: list = field(default_factory=list)   # a table without caption outside a section
    hrefs: list = field(default_factory=list)
    forbidden: list = field(default_factory=list)        # tags that must not occur
    event_attrs: list = field(default_factory=list)
    external: list = field(default_factory=list)         # addresses with a scheme or a network path
    data_uris: int = 0
    base64: int = 0
    style_bytes: int = 0
    metas: list = field(default_factory=list)            # attribute dicts of every <meta>
    links: list = field(default_factory=list)            # attribute dicts of every <link>
    title: str = ""
    lang: str = ""
    doctype: bool = False
    charset_at: int = -1
    styles: int = 0
    style_text: str = ""
    scripts: int = 0

    @property
    def header_first(self) -> str:
        texts = [t for z, t in self.atoms if z == "header"]
        return texts[0] if texts else ""

    @property
    def footer_last(self) -> str:
        texts = [t for z, t in self.atoms if z == "footer"]
        return texts[-1] if texts else ""

    def texts(self) -> set:
        """Every atom of the page, wherever it sits (a gallery holds more than the card it mirrors)."""
        return {text for _zone, text in self.atoms}

    def visible(self) -> list:
        head = [t for z, t in self.atoms if z == "header"][1:]
        main = [t for z, t in self.atoms if z == "main"]
        foot = [t for z, t in self.atoms if z == "footer"][:-1]
        return head + main + foot


class _Reader(HTMLParser):
    def __init__(self, text: str):
        super().__init__(convert_charrefs=True)
        self.a = Analysis()
        self.stack: list = []
        self.stray: list = []
        self.zone = "main"
        self.in_head = False
        self.in_style = False
        self.in_script = False
        self.in_title = False
        self.tables: list = []
        self.section_depth = 0
        self.text = text

    def _flush_stray(self) -> None:
        text = norm("".join(self.stray))
        self.stray.clear()
        if text and not self.in_head:
            self.a.atoms.append((self.zone, text))

    def _attrs(self, tag: str, attrs) -> None:
        for name, value in attrs:
            name = (name or "").lower()
            value = value or ""
            if name.startswith("on"):
                self.a.event_attrs.append(f"{tag}[{name}]")
            if name == "id":
                self.a.ids.append(value)
            if name in URL_ATTRS and value.strip():
                low = value.strip().lower()
                if "://" in low or low.startswith("//"):
                    self.a.external.append(value)
                if low.startswith("data:"):
                    self.a.data_uris += 1
                if tag == "a" and name == "href":
                    self.a.hrefs.append(value)
            if "url(" in value.lower() and "data:" in value.lower():
                self.a.data_uris += 1
            if BASE64_RUN.search(value):
                self.a.base64 += 1

    def handle_decl(self, decl: str) -> None:
        if decl.lower().replace("  ", " ").startswith("doctype html"):
            self.a.doctype = True

    def handle_starttag(self, tag, attrs):
        self._flush_stray()
        attrs = list(attrs)
        self._attrs(tag, attrs)
        if tag == "html":
            self.a.lang = dict(attrs).get("lang") or ""
        elif tag == "head":
            self.in_head = True
        elif tag == "body":
            self.in_head = False
        elif tag == "title":
            self.in_title = True
        elif tag == "style":
            self.in_style = True
            self.a.styles += 1
        elif tag == "script":
            self.in_script = True
            self.a.scripts += 1
        elif tag == "meta":
            d = {k: (v or "") for k, v in attrs}
            self.a.metas.append(d)
        elif tag == "link":
            self.a.links.append({k: (v or "") for k, v in attrs})
        elif tag == "header":
            self.zone = "header"
        elif tag == "footer":
            self.zone = "footer"
        elif tag == "section":
            self.a.sections += 1
            self.section_depth += 1
        elif tag == "table":
            self.a.tables += 1
            self.tables.append({"caption": False, "in_section": self.section_depth > 0})
        elif tag == "caption" and self.tables:
            self.tables[-1]["caption"] = True
        elif tag == "tr" and self.tables:
            self.a.rows += 1
        if tag in FORBIDDEN_TAGS or (tag == "link" and dict(attrs).get("rel", "").lower() != "alternate"):
            self.a.forbidden.append(tag)
        if tag in BLOCK_TAGS:
            self.stack.append([tag, [], self.zone])

    def handle_endtag(self, tag):
        self._flush_stray()
        if tag == "head":
            self.in_head = False
        elif tag == "title":
            self.in_title = False
        elif tag == "style":
            self.in_style = False
        elif tag == "script":
            self.in_script = False
        elif tag in ("header", "footer"):
            self.zone = "main"
        elif tag == "section":
            self.section_depth = max(0, self.section_depth - 1)
        elif tag == "table" and self.tables:
            info = self.tables.pop()
            if not info["caption"] and not info["in_section"]:
                self.a.table_problems.append("a table without caption outside a block")
        if tag in BLOCK_TAGS:
            for k in range(len(self.stack) - 1, -1, -1):
                if self.stack[k][0] == tag:
                    _tag, buf, zone = self.stack[k]
                    del self.stack[k:]
                    text = "".join(buf)
                    if tag == "pre":
                        self.a.atoms.extend((zone, norm(line)) for line in text.split("\n") if line.strip())
                    else:
                        self.a.atoms.append((zone, norm(text)))
                    break

    def handle_data(self, data):
        if self.in_script:
            return
        if self.in_style:
            self.a.style_text += data
            self.a.style_bytes += len(data.encode("utf-8"))
            if BASE64_RUN.search(data):
                self.a.base64 += 1
            if "url(" in data.lower() and "data:" in data.lower():
                self.a.data_uris += 1
            return
        if self.in_title:
            self.a.title += data
            return
        if self.in_head:
            return
        if self.stack:
            self.stack[-1][1].append(data)
        else:
            self.stray.append(data)

    def close(self):
        super().close()
        self._flush_stray()


def analyse(data) -> Analysis:
    """The structural reading of a twin (bytes or text). Never raises on broken HTML: the parser is tolerant, the checks look at the result."""
    text = data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else data
    reader = _Reader(text)
    reader.feed(text)
    reader.close()
    reader.a.charset_at = text.find("<meta charset") if "<meta charset" in text else -1
    return reader.a


def visible_html(data) -> list:
    """The atoms of a twin (the first paragraph of ``<header>`` and the last of ``<footer>`` are not compared)."""
    return analyse(data).visible()


def expected_ids(path: str, text: str, lang: str) -> list:
    """The ``id`` attributes the twin of a page must carry, in document order (RS-114): a function of the Markdown alone."""
    page = parse_page(lang, path, text)
    if page is None:
        return []
    return block_ids(parse_blocks(page.lines[2:page.foot_start]))


def policy_directives(policy: str) -> dict:
    out: dict = {}
    for part in policy.split(";"):
        words = part.split()
        if words:
            out[words[0]] = words[1:]
    return out


def counts_of(lang: str, blocks: int, tables: int) -> str:
    return tx(lang, "twin.counts", blocks=blocks, tables=tables)
