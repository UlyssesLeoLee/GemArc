"""Outline of a source file: def / class / heading lines with line numbers and a shortest-unique anchor each.

The point is cheap, verified anchors: the AI picks an anchor from the outline instead of reading the whole file, and the
anchor occurs exactly once in the file, so ``evidence.verify_evidence`` resolves it to the same line on first stamp.

* Languages: Python (``ast``, regex fallback for files that do not parse), JavaScript, TypeScript, Java, Go (regex on a
  comment-and-string-masked copy of the text), Markdown, YAML, JSON, TOML, SQL, and ``.env`` files (key names only).
* Honest limits: the regex finders miss constructs that span lines (multi-line signatures, arrow functions whose parameters
  wrap, interface method declarations) and can list a construct inside a template literal. Unsupported file types give an
  empty outline and say so. Files that never echo content (key stores, ``credentials*``, ``secrets.*``) give none at all.
* Anchors: the shortest prefix of the entry's own line, cut at a token boundary, that occurs once in the file, has at
  least 6 non-blank characters, contains no ``|`` and nothing that looks like an ``key=value`` option, and passes the
  credential lint. If no such single-line anchor exists (a duplicated line such as ``def __init__(self):``) the entry is
  listed WITHOUT an anchor and says why; a multi-line anchor would not fit an ``.atl`` cell.
* Redaction: lines that look like they carry a credential are listed without name or anchor.
* A page is at most 4 KiB of UTF-8; ``next`` continues it.

Pure functions over ``evidence.SourceText``; ``outline_file`` reads through the roots. Standard library only.
"""
from __future__ import annotations

import ast
import json
import re
import time
from pathlib import Path
from typing import Mapping, Sequence

from . import evidence as _evidence
from . import roots as _roots

OUTLINE_PAGE_BYTES = 4096
MIN_ANCHOR_CHARS = _evidence.MIN_ANCHOR_CHARS
MAX_ANCHOR_CHARS = 120
MAX_NAME_CHARS = 72
MAX_ENTRIES = 3000
#: a line longer than this (stripped) gets no anchor: minified text, data blobs, generated code
MAX_ANCHOR_LINE_CHARS = 600
#: wall-clock budget of the anchor search of one outline (entries after it are listed without anchors)
OUTLINE_DEADLINE_SECONDS = 2.0
#: files with more characters than this are not outlined at all (a listing of such a file is not an outline)
MAX_OUTLINE_CHARS = 3_000_000

LANGUAGE_BY_SUFFIX = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".java": "java", ".go": "go",
    ".md": "markdown", ".markdown": "markdown", ".mdx": "markdown",
    ".yaml": "yaml", ".yml": "yaml", ".json": "json", ".toml": "toml", ".sql": "sql",
}
SUPPORTED_LANGUAGES = tuple(sorted(set(LANGUAGE_BY_SUFFIX.values()))) + ("dotenv",)

_OPTION_LIKE_RE = re.compile(r"(?:^|\s)[A-Za-z_][\w.-]*=\S")
_CONTROL_WORDS = frozenset({"if", "for", "while", "switch", "catch", "return", "function", "else", "do", "try", "new", "throw",
                            "await", "yield", "typeof", "super", "this", "synchronized", "assert", "case", "delete", "void"})
_TOKEN_RE = re.compile(r"\w+|\s+|[^\w\s]", re.UNICODE)


def language_of(rel: str) -> str | None:
    """Outline language of a path, or None. ``.env*`` files are ``dotenv`` (key names only)."""
    text = str(rel).replace("\\", "/")
    if _evidence.is_dotenv_path(text):
        return "dotenv"
    name = text.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return LANGUAGE_BY_SUFFIX.get(name[dot:].lower()) if dot > 0 else None


# --------------------------------------------------------------------------------------------------------
# Anchors
# --------------------------------------------------------------------------------------------------------


def _nonblank(text: str) -> int:
    return sum(1 for ch in text if not ch.isspace())


def _clean(candidate: str) -> bool:
    return ("|" not in candidate and "\n" not in candidate and _OPTION_LIKE_RE.search(candidate) is None
            and not _evidence.secret_findings(candidate))


def _boundaries(text: str) -> list[int]:
    """End offsets of tokens at which an anchor may stop (never after whitespace)."""
    ends, position = [], 0
    for match in _TOKEN_RE.finditer(text):
        position = match.end()
        if not match.group().isspace():
            ends.append(position)
    return ends


def _first_unique(src: _evidence.SourceText, text: str, start: int, ends: Sequence[int], *, cap: int = MAX_ANCHOR_CHARS) -> str | None:
    """Smallest prefix text[start:end] (end from ``ends``) with enough characters that occurs once in the file."""
    lo, hi, found = 0, len(ends) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = text[start:ends[mid]]
        if _nonblank(candidate) >= MIN_ANCHOR_CHARS and src.text.count(candidate) == 1:
            found, hi = mid, mid - 1
        else:
            lo = mid + 1
    if found is None:
        return None
    candidate = text[start:ends[found]]
    return candidate if len(candidate) <= cap and _clean(candidate) else None


def choose_anchor(src: _evidence.SourceText, rel: str, line_no: int, *, stop_at: str | None = None,
                  cache: dict | None = None) -> tuple[str | None, str]:
    """``(anchor, "")`` or ``(None, reason)`` for the entry on ``line_no``.

    ``stop_at`` limits the anchor to the part of the line before (and including) that character (used for ``.env``
    files, where the value after ``=`` must never be part of an anchor). ``cache`` (a dict the caller keeps for one file)
    makes identical lines cost one search: the result depends on the line text and the file, not on the position.
    """
    if _evidence.is_sensitive_path(rel) and not _evidence.is_dotenv_path(rel):
        return None, "sensitive-file"
    raw = src.lines[line_no - 1] if 1 <= line_no <= len(src.lines) else ""
    text = raw.strip()
    if not text:
        return None, "blank-line"
    if len(text) > MAX_ANCHOR_LINE_CHARS:
        return None, "line-too-long"
    dotenv = _evidence.is_dotenv_path(rel)
    if not dotenv and (line_no in src.private_key_lines() or _evidence.line_is_sensitive(raw)):
        return None, "sensitive-line"
    key = (text, stop_at)
    if cache is not None and key in cache:
        return cache[key]
    verdict = _search_anchor(src, text, stop_at)
    if cache is not None:
        cache[key] = verdict
    return verdict


def _search_anchor(src: _evidence.SourceText, text: str, stop_at: str | None) -> tuple[str | None, str]:
    whole = text
    if stop_at is not None:
        cut = text.find(stop_at)
        text = text[:cut + len(stop_at)] if cut >= 0 else text
    elif src.line_frequency().get(text, 0) > 1:
        return None, "duplicate-line"          # every substring of a repeated line occurs at least twice
    ends = _boundaries(text)
    if not ends:
        return None, "no-token"
    anchor = _first_unique(src, text, 0, ends)
    if anchor is not None:
        return anchor, ""
    # the prefix is taken, too long or unclean: try substrings that start at a later token
    starts = [m.start() for m in re.finditer(r"\w+", text)][1:12]
    for start in starts:
        later = [e for e in ends if e > start]
        anchor = _first_unique(src, text, start, later)
        if anchor is not None:
            return anchor, ""
    if src.text.count(text) > 1:
        return None, "duplicate-line"
    return None, "no-clean-anchor"


# --------------------------------------------------------------------------------------------------------
# Masking (comments and strings blanked, newlines and offsets kept)
# --------------------------------------------------------------------------------------------------------


def _mask(text: str, *, line_comments: Sequence[str], block: tuple[str, str] | None, quotes: str = "'\"",
          triple: bool = False, backtick: bool = False, sql_quote: bool = False) -> str:
    out = list(text)
    i, n = 0, len(text)

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        ch = text[i]
        matched = next((c for c in line_comments if text.startswith(c, i)), None)
        if matched:
            end = text.find("\n", i)
            end = n if end < 0 else end
            blank(i, end)
            i = end
            continue
        if block and text.startswith(block[0], i):
            end = text.find(block[1], i + len(block[0]))
            end = n if end < 0 else end + len(block[1])
            blank(i, end)
            i = end
            continue
        if triple and ch in "\"'" and text.startswith(ch * 3, i):
            end = text.find(ch * 3, i + 3)
            end = n if end < 0 else end + 3
            blank(i, end)
            i = end
            continue
        if ch in quotes or (backtick and ch == "`"):
            j = i + 1
            multiline = ch == "`"
            while j < n:
                cj = text[j]
                if cj == "\\" and not sql_quote:
                    j += 2
                    continue
                if cj == ch:
                    if sql_quote and text.startswith(ch * 2, j):
                        j += 2
                        continue
                    break
                if cj == "\n" and not multiline:
                    break
                j += 1
            blank(i + 1, j)
            i = j + 1
            continue
        i += 1
    return "".join(out)


# --------------------------------------------------------------------------------------------------------
# Per-language entry finders. Each returns [{"line", "kind", "name", ["stop_at"]}] in line order.
# --------------------------------------------------------------------------------------------------------


def _python_ast(text: str) -> list[dict] | None:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return None
    found: list[dict] = []

    def visit(body: Sequence[ast.stmt], path: str, depth: int, in_function: bool) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{path}.{node.name}" if path else node.name
                kind = "class" if isinstance(node, ast.ClassDef) else ("async def" if isinstance(node, ast.AsyncFunctionDef) else "def")
                if depth <= 3:
                    found.append({"line": node.lineno, "kind": kind, "name": name})
                    visit(node.body, name, depth + 1, in_function or kind != "class")
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and not in_function:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if len(targets) == 1 and isinstance(targets[0], ast.Name) and re.fullmatch(r"[A-Z][A-Z0-9_]*", targets[0].id):
                    found.append({"line": node.lineno, "kind": "const", "name": f"{path}.{targets[0].id}" if path else targets[0].id})
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.AsyncWith, ast.For, ast.AsyncFor, ast.While)):
                for field in ("body", "orelse", "finalbody"):
                    visit(getattr(node, field, None) or [], path, depth, in_function)
                for handler in getattr(node, "handlers", None) or []:
                    visit(handler.body, path, depth, in_function)

    visit(tree.body, "", 1, False)
    return found


def _python_regex(text: str) -> list[dict]:
    masked = _mask(text, line_comments=("#",), block=None, triple=True)
    found, stack = [], []
    for number, line in enumerate(masked.split("\n"), start=1):
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        while stack and indent <= stack[-1][0]:
            stack.pop()
        match = re.match(r"\s*(async\s+def|def|class)\s+(\w+)", line)
        if match:
            name = ".".join([s[1] for s in stack] + [match.group(2)])
            found.append({"line": number, "kind": "class" if match.group(1) == "class" else match.group(1).replace("  ", " "), "name": name})
            stack.append((indent, match.group(2)))
            continue
        const = re.match(r"([A-Z][A-Z0-9_]*)\s*(?::[^=]+)?=[^=]", line)
        if const and indent == 0:
            found.append({"line": number, "kind": "const", "name": const.group(1)})
    return found


def _brace_scan(masked_lines: Sequence[str], class_re: re.Pattern, member_re: re.Pattern, func_res: Sequence[tuple[str, re.Pattern]],
                class_kind_group: int, class_name_group: int, member_name_group: int, member_kind: str = "method") -> list[dict]:
    found, depth, stack = [], 0, []
    for number, line in enumerate(masked_lines, start=1):
        while stack and depth <= stack[-1][0] and number > stack[-1][2]:
            stack.pop()
        match = class_re.match(line)
        if match:
            name = ".".join([s[1] for s in stack] + [match.group(class_name_group)])
            found.append({"line": number, "kind": match.group(class_kind_group).strip(), "name": name})
            stack.append((depth, match.group(class_name_group), number))
        else:
            hit = False
            for kind, pattern in func_res:
                fm = pattern.match(line)
                if fm and fm.group("name") not in _CONTROL_WORDS:
                    prefix = ".".join(s[1] for s in stack)
                    found.append({"line": number, "kind": kind, "name": f"{prefix}.{fm.group('name')}" if prefix else fm.group("name")})
                    hit = True
                    break
            if not hit and stack and depth == stack[-1][0] + 1:
                mm = member_re.match(line)
                if mm and mm.group(member_name_group) not in _CONTROL_WORDS:
                    found.append({"line": number, "kind": member_kind, "name": ".".join(s[1] for s in stack) + "." + mm.group(member_name_group)})
        depth += line.count("{") - line.count("}")
    return found


_JS_CLASS = re.compile(r"\s*(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?(class|interface|enum|namespace)\s+(\w+)")
_JS_MEMBER = re.compile(r"\s+(?:(?:public|private|protected|static|async|readonly|override|abstract|get|set)\s+)*(\w+)\s*(?:<[^>]*>)?\s*\([^)]*\)?[^;={]*\{\s*$")
_JS_FUNCS = (
    ("function", re.compile(r"\s*(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:async\s+)?function\s*\*?\s*(?P<name>\w+)")),
    ("arrow", re.compile(r"\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>\w+)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*(?::[^=]+)?=>|\w+\s*=>)")),
    ("type", re.compile(r"\s*(?:export\s+)?(?:declare\s+)?type\s+(?P<name>\w+)\s*(?:<[^>]*>)?\s*=")),
    ("const", re.compile(r"\s*(?:export\s+)?const\s+(?P<name>[A-Z][A-Z0-9_]*)\s*(?::[^=]+)?=[^=>]")),
)
_JAVA_CLASS = re.compile(r"\s*(?:(?:public|protected|private|abstract|final|static|sealed|non-sealed|strictfp)\s+)*(class|interface|enum|record|@interface)\s+(\w+)")
_JAVA_MEMBER = re.compile(r"\s*(?:(?:public|protected|private|static|final|abstract|synchronized|native|default)\s+)*(?:<[^>]+>\s+)?(?:[\w<>\[\]?,.]+\s+)?(\w+)\s*\([^;]*$")
_GO_FUNCS = (
    ("func", re.compile(r"func\s+(?:\((?:\w+\s+)?\*?(?P<recv>[\w.\[\]]+)\)\s*)?(?P<name>\w+)")),
)


def _c_like(text: str, language: str) -> list[dict]:
    masked = _mask(text, line_comments=("//",), block=("/*", "*/"), quotes="'\"", backtick=True, triple=language == "java")
    lines = masked.split("\n")
    if language in ("javascript", "typescript"):
        return _brace_scan(lines, _JS_CLASS, _JS_MEMBER, _JS_FUNCS, 1, 2, 1)
    if language == "java":
        found = _brace_scan(lines, _JAVA_CLASS, _JAVA_MEMBER, (), 1, 2, 1)
        return [f for f in found if f["kind"] != "method" or not f["name"].endswith(".")]
    # go
    found = []
    for number, line in enumerate(lines, start=1):
        fm = re.match(r"func\s+(?:\((?:\w+\s+)?\*?(?P<recv>[\w.\[\]]+)\)\s*)?(?P<name>\w+)", line)
        if fm:
            name = f"{fm.group('recv')}.{fm.group('name')}" if fm.group("recv") else fm.group("name")
            found.append({"line": number, "kind": "func", "name": name})
            continue
        tm = re.match(r"type\s+(\w+)\s+(struct|interface|\w)", line)
        if tm:
            found.append({"line": number, "kind": "type", "name": tm.group(1)})
            continue
        vm = re.match(r"(var|const)\s+(\w+)", line)
        if vm:
            found.append({"line": number, "kind": vm.group(1), "name": vm.group(2)})
    return found


def _markdown(text: str) -> list[dict]:
    found, stack, fence = [], [], None
    lines = text.split("\n")
    for number, line in enumerate(lines, start=1):
        fm = re.match(r"\s{0,3}(`{3,}|~{3,})", line)
        if fm:
            if fence is None:
                fence = fm.group(1)[0] * 3
            elif line.strip().startswith(fence):
                fence = None
            continue
        if fence is not None:
            continue
        match = re.match(r"\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        level, title = (len(match.group(1)), match.group(2)) if match else (0, "")
        if not match and number < len(lines) and line.strip() and not re.match(r"\s*([-*+>|]|\d+[.)])\s", line):
            under = lines[number]
            if re.fullmatch(r"=+\s*", under):
                level, title = 1, line.strip()
            elif re.fullmatch(r"-{2,}\s*", under):
                level, title = 2, line.strip()
        if level:
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            found.append({"line": number, "kind": f"h{level}", "name": " > ".join(t for _, t in stack[-3:])})
    return found


def _yaml(text: str) -> list[dict]:
    found, stack = [], []
    for number, line in enumerate(text.split("\n"), start=1):
        if not line.strip() or line.lstrip().startswith(("#", "---", "...")):
            continue
        match = re.match(r"(\s*)(?:-\s+)?(?P<key>[\w.@/-]+|\"[^\"]+\"|'[^']+')\s*:(?:\s|$)", line)
        if not match:
            continue
        indent = len(match.group(1)) + (2 if re.match(r"\s*-\s", line) else 0)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        key = match.group("key").strip("\"'")
        stack.append((indent, key))
        if len(stack) <= 3:
            found.append({"line": number, "kind": "key", "name": ".".join(k for _, k in stack)})
    return found


def _json(text: str) -> list[dict]:
    """Keys at object depth 1 and 2 (array elements are not entered)."""
    found: list[dict] = []
    stack: list[tuple[str, str | None]] = []   # ("obj"|"arr", current key)
    i, n, line = 0, len(text), 1
    expecting_key = False
    while i < n:
        ch = text[i]
        if ch == "\n":
            line += 1
        elif ch == '"':
            j, start_line = i + 1, line
            while j < n and text[j] != '"':
                if text[j] == "\\":
                    j += 1
                if j < n and text[j] == "\n":
                    line += 1
                j += 1
            token = text[i + 1:j]
            k = j + 1
            while k < n and text[k] in " \t\r\n":
                k += 1
            if stack and stack[-1][0] == "obj" and k < n and text[k] == ":" and expecting_key:
                stack[-1] = ("obj", token)
                depth = sum(1 for kind, _ in stack if kind == "obj")
                if all(kind == "obj" for kind, _ in stack) and depth <= 2:
                    found.append({"line": start_line, "kind": "key", "name": ".".join(k_ for _, k_ in stack if k_ is not None)})
                expecting_key = False
            i = j
        elif ch == "{":
            stack.append(("obj", None))
            expecting_key = True
        elif ch == "[":
            stack.append(("arr", None))
            expecting_key = False
        elif ch in "}]":
            if stack:
                stack.pop()
            expecting_key = False
        elif ch == ",":
            expecting_key = bool(stack) and stack[-1][0] == "obj"
        i += 1
    return found


def _toml(text: str) -> list[dict]:
    masked = _mask(text, line_comments=("#",), block=None, triple=True)
    found, table = [], ""
    for number, line in enumerate(masked.split("\n"), start=1):
        header = re.match(r"\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*$", line)
        if header:
            table = header.group(1)
            found.append({"line": number, "kind": "table", "name": table})
            continue
        key = re.match(r"([A-Za-z0-9_.\"'-]+)\s*=", line)
        if key:
            name = key.group(1).strip("\"'")
            found.append({"line": number, "kind": "key", "name": f"{table}.{name}" if table else name})
    return found


_SQL_KINDS = r"table|view|index|function|procedure|trigger|type|schema|sequence|extension|domain"
_SQL_CREATE = re.compile(
    r"\s*create\s+(?:or\s+replace\s+)?(?:(?:temp|temporary|unique|materialized|global|local|unlogged|recursive)\s+)*"
    r"(" + _SQL_KINDS + r")\s+(?:if\s+not\s+exists\s+)?([\w.\"`\[\]]+)", re.IGNORECASE)
_SQL_ALTER = re.compile(r"\s*alter\s+(table|view|sequence)\s+(?:only\s+)?(?:if\s+exists\s+)?([\w.\"`\[\]]+)", re.IGNORECASE)


def _sql(text: str) -> list[dict]:
    masked = _mask(text, line_comments=("--",), block=("/*", "*/"), quotes="'", sql_quote=True)
    found = []
    for number, line in enumerate(masked.split("\n"), start=1):
        match = _SQL_CREATE.match(line)
        if match:
            found.append({"line": number, "kind": match.group(1).lower(), "name": match.group(2).strip("\"`[]")})
            continue
        match = _SQL_ALTER.match(line)
        if match:
            found.append({"line": number, "kind": f"alter {match.group(1).lower()}", "name": match.group(2).strip("\"`[]")})
    return found


def _dotenv(text: str) -> list[dict]:
    found = []
    for number, line in enumerate(text.split("\n"), start=1):
        match = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match:
            found.append({"line": number, "kind": "key", "name": match.group(1), "stop_at": "="})
    return found


# --------------------------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------------------------


def outline(src: _evidence.SourceText, rel: str, *, language: str | None = None) -> dict:
    """Outline of decoded text.

    Returns ``{"file", "language", "lineCount", "entries", "anchorless", "note"}``; each entry is
    ``{"line", "kind", "name", "anchor", ["why"]}`` with ``anchor`` None (and ``why``) when no clean unique single-line
    anchor exists. Entries on credential-like lines carry no name.
    """
    language = language or language_of(rel)
    result = {"file": str(rel).replace("\\", "/"), "language": language, "lineCount": src.line_count, "entries": [],
              "anchorless": 0, "note": ""}
    if _evidence.is_sensitive_path(rel) and language != "dotenv":
        result["note"] = "withheld: sensitive file, no content is listed"
        return result
    if language is None:
        result["note"] = "no outline support for this file type; supported: " + ", ".join(SUPPORTED_LANGUAGES)
        return result
    if len(src.text) > MAX_OUTLINE_CHARS:
        result["note"] = f"file has {len(src.text)} characters (limit {MAX_OUTLINE_CHARS}): no outline; cite anchors you read yourself"
        return result
    raw: list[dict]
    if language == "python":
        raw = _python_ast(src.text)
        if raw is None:
            raw = _python_regex(src.text)
            result["note"] = "file does not parse as Python; regex outline"
    elif language in ("javascript", "typescript", "java", "go"):
        raw = _c_like(src.text, language)
    elif language == "markdown":
        raw = _markdown(src.text)
    elif language == "yaml":
        raw = _yaml(src.text)
    elif language == "json":
        raw = _json(src.text)
    elif language == "toml":
        raw = _toml(src.text)
    elif language == "sql":
        raw = _sql(src.text)
    else:
        raw = _dotenv(src.text)
    raw.sort(key=lambda e: e["line"])
    entries = []
    cache: dict = {}
    deadline = time.monotonic() + OUTLINE_DEADLINE_SECONDS
    out_of_time = 0
    for item in raw[:MAX_ENTRIES]:
        if time.monotonic() > deadline:
            anchor, why = None, "time-limit"
            out_of_time += 1
        else:
            anchor, why = choose_anchor(src, rel, item["line"], stop_at=item.get("stop_at"), cache=cache)
        name = item["name"]
        if language != "dotenv" and (why in ("sensitive-line", "sensitive-file") or _evidence.line_is_sensitive(name)):
            name = "[withheld]"
        entry = {"line": item["line"], "kind": item["kind"], "name": name if len(name) <= MAX_NAME_CHARS else name[:MAX_NAME_CHARS - 1] + "…",
                 "anchor": anchor}
        if anchor is None:
            entry["why"] = why
            result["anchorless"] += 1
        entries.append(entry)
    if len(raw) > MAX_ENTRIES:
        result["note"] = (result["note"] + "; " if result["note"] else "") + f"first {MAX_ENTRIES} of {len(raw)} entries"
    if out_of_time:
        result["note"] = (result["note"] + "; " if result["note"] else "") + (
            f"anchor search stopped after {OUTLINE_DEADLINE_SECONDS:g} s: {out_of_time} entries have no anchor (time-limit); "
            "cite them with an anchor you chose yourself or outline a smaller file")
    result["entries"] = entries
    return result


def outline_text(rel: str, text: str, *, language: str | None = None) -> dict:
    """Convenience for tests and callers that hold text (LF or CRLF)."""
    data = text.encode("utf-8")
    return outline(_evidence.decode_text(data), rel, language=language)


def render_entry(entry: Mapping) -> str:
    anchor = json.dumps(entry["anchor"], ensure_ascii=False) if entry.get("anchor") is not None else f"- ({entry.get('why', 'no anchor')})"
    return f"L{entry['line']} {entry['kind']} {entry['name']} | {anchor}"


def render_page(result: Mapping, start: int = 0, max_bytes: int = OUTLINE_PAGE_BYTES) -> dict:
    """One page of at most ``max_bytes`` UTF-8 bytes. ``next`` is the entry index to pass as ``start`` (None at the end).

    Anchors are printed as JSON string literals so that they can be copied exactly (quotes and backslashes escaped).
    """
    entries = result["entries"]
    head = f"# {result['file']} [{result['language'] or 'unsupported'}] {result['lineCount']} lines, {len(entries)} entries"
    if result.get("anchorless"):
        head += f", {result['anchorless']} without a unique single-line anchor"
    lines = [head]
    if result.get("note"):
        lines.append(f"# {result['note']}")
    if start < 0 or start > len(entries):
        start = 0
    reserve = 48
    used = sum(len(line.encode("utf-8")) + 1 for line in lines)
    shown = 0
    index = start
    while index < len(entries):
        line = render_entry(entries[index])
        size = len(line.encode("utf-8")) + 1
        if used + size > max_bytes - reserve and shown > 0:
            break
        if used + size > max_bytes - reserve:  # a single entry that cannot fit: trim the line, never overflow the page
            budget = max_bytes - reserve - used - 4
            line = line.encode("utf-8")[:max(budget, 0)].decode("utf-8", "ignore") + " ..."
            size = len(line.encode("utf-8")) + 1
        lines.append(line)
        used += size
        shown += 1
        index += 1
    nxt = index if index < len(entries) else None
    if nxt is not None:
        lines.append(f"-- {len(entries) - index} more entries: start={nxt}")
    return {"text": "\n".join(lines) + "\n", "start": start, "next": nxt, "shown": shown, "total": len(entries)}


def outline_file(roots: Mapping[str, Path | str], alias: str, rel: str, *, start: int = 0, max_bytes: int = OUTLINE_PAGE_BYTES) -> dict:
    """Outline of ``rel`` under the root of ``alias``, one page. Errors are returned, not raised.

    ``{"ok": True, "text", "next", "total", "language", ...}`` or ``{"ok": False, "code", "message"}``.
    """
    try:
        alias = _roots.require_alias(alias, "repo")
        root = Path(roots[alias]) if alias in roots else None
        if root is None:
            return {"ok": False, "code": _evidence._CODES["REPO_UNMAPPED"][0], "message": f"repository alias {alias!r} has no root in {_roots.ENV_NAME}"}
        if not _roots.root_available(root):
            return {"ok": False, "code": _evidence._CODES["ROOT_MISSING"][0], "message": f"root of {alias!r} does not exist on this machine"}
        resolved = _roots.resolve_in_root(root, rel)
    except _roots.RootsError as ex:
        return {"ok": False, "code": ex.code, "message": ex.message}
    if not resolved.exists or not _roots.is_regular_file(resolved.path):
        return {"ok": False, "code": _evidence._CODES["FILE_NOT_FOUND"][0], "message": f"no file {rel} under {alias}"}
    try:
        src = _evidence.read_text_file(resolved.path)   # os.stat first: an oversized file is refused without being read
    except (_evidence.TextError, OSError) as ex:
        return {"ok": False, "code": _evidence._CODES["NOT_TEXT"][0], "message": f"{resolved.rel}: {ex}"}
    result = outline(src, resolved.rel)
    page = render_page(result, start, max_bytes)
    return {"ok": True, **page, "language": result["language"], "anchorless": result["anchorless"], "file": result["file"],
            "caseMismatch": resolved.case_mismatch}
