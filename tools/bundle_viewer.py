#!/usr/bin/env python3
"""Release-time bundler of the Atlas viewer (design 9.3). Standard library only; Node is never needed.

Reads ``src/gemarc_mcp/atlas/viewer/`` (``src/*.js``, ``src/ui/*.js``, ``src/atlas.css``, ``src/atlas.template.html``,
``i18n/*.json``) and writes the committed outputs::

    viewer/dist/atlas-runtime.js        every runtime module, each in its own function scope, then the boot call
    viewer/dist/atlas.css               the style sheet (line endings normalised)
    viewer/dist/atlas.template.html     the page skeleton with the six ATLAS placeholders
    viewer/dist/atlas-i18n.json         the three interface tables (languages and tables), compact, sorted keys
    viewer/SOURCES.sha256               ``<sha256>  <path>`` for every input (paths relative to viewer/), for the bundler
                                        itself (``tools/bundle_viewer.py``) and for every output (``dist/...``)

The product path (``build.py`` and ``htmlassemble.py``) reads ``dist/`` only; it never needs the sources, the i18n files or
this script. A staleness test (``tests/atlas/test_bundle_sources.py``) recomputes everything and fails when a source changed
without a re-bundle: run ``python tools/bundle_viewer.py`` after editing a viewer source, ``--check`` only compares.

Module scope. Every module is wrapped in ``const <id> = (function () { 'use strict'; ... return { exports }; })();``. The module
text keeps its own top-level names private, so two modules may use the same private helper name; the conventions of
``docs/atlas/spec/bundle-contract.md`` section 7 still apply because they let the unit tests concatenate the sources instead:

* one single-line import per statement, ``import { a, b } from './x.js';`` or ``'../x.js'``, no default, no ``* as``, no ``as``;
* exports only as ``export const|function|class|async function NAME`` at column 0 (no export list, no re-export);
* sources are ASCII (a non-ASCII character is written as ``\\uXXXX``), no ``</script``, ``<!--`` or ``</style`` text;
* the import graph has no cycle.

Anything else is a hard error naming file and line. Every imported name is checked against the exports of its target (a
rename that missed a user is caught here, not in the browser). Output is deterministic: LF, no timestamps, no paths of this
machine, module order = dependency order (ties by path).

Size. The page budget is tight (design 9.1), so the runtime text drops what a browser does not need: whole-line comments, block
comments that start a line, blank lines and the indentation of every line (``strip_body``). Trailing comments stay. This is safe
because a source line with an odd number of backticks (a template literal that spans lines, where indentation and a leading ``//``
are text) is a hard error: sources keep each template literal on one line.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_VIEWER = REPO / "src" / "gemarc_mcp" / "atlas" / "viewer"
ENTRY_MODULE = "ui/main.js"
ENTRY_NAME = "bootAtlas"
LANGS = ("zh-CN", "ja-JP", "en-US")
RUNTIME_OUT = "dist/atlas-runtime.js"
CSS_OUT = "dist/atlas.css"
TEMPLATE_OUT = "dist/atlas.template.html"
I18N_OUT = "dist/atlas-i18n.json"
SOURCES_OUT = "SOURCES.sha256"
OUTPUTS = (RUNTIME_OUT, CSS_OUT, TEMPLATE_OUT, I18N_OUT)
TOOL_ENTRY = "tools/bundle_viewer.py"

_IMPORT = re.compile(r"^import \{ ([A-Za-z0-9_, ]+) \} from '(\.{1,2}/[a-z0-9\-/]+\.js)';$")
_EXPORT = re.compile(r"^export (?:async function|function|const|class) ([A-Za-z_$][\w$]*)")
_IDENT = re.compile(r"^[A-Za-z_$][\w$]*$")
_FORBIDDEN_TEXT = re.compile(r"</script|<!--|</style", re.IGNORECASE)


class BundleError(Exception):
    """A source that breaks the bundling conventions (the message names file and line)."""


@dataclass
class Module:
    rel: str
    imports: list = field(default_factory=list)   # [(names, resolved path)]
    exports: list = field(default_factory=list)
    body: list = field(default_factory=list)      # lines without import lines, with `export ` removed

    @property
    def ident(self) -> str:
        return "__mod_" + re.sub(r"[^A-Za-z0-9]", "_", self.rel[:-3])


def normalise(data: bytes) -> str:
    """Source text: UTF-8, BOM dropped, CRLF and lone CR to LF (the hash and the bundle are line-ending independent)."""
    text = data.decode("utf-8")
    if text.startswith("﻿"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_source(path: Path) -> str:
    return normalise(path.read_bytes())


def resolve(from_rel: str, spec: str) -> str:
    """Resolve an import specifier against the importing module's directory (paths relative to ``viewer/src``)."""
    base = from_rel.split("/")[:-1]
    for part in spec.split("/"):
        if part == ".":
            continue
        if part == "..":
            if not base:
                raise BundleError(f"{from_rel}: import leaves the source directory: {spec}")
            base.pop()
        else:
            base.append(part)
    return "/".join(base)


def module_files(src: Path) -> list:
    """Relative path of every runtime module: ``src/*.js`` then ``src/ui/*.js``, sorted (split on ``/``, never on text)."""
    top = sorted(p.name for p in src.glob("*.js"))
    ui = sorted("ui/" + p.name for p in (src / "ui").glob("*.js")) if (src / "ui").is_dir() else []
    return top + ui


def parse_module(rel: str, text: str) -> Module:
    mod = Module(rel)
    for number, line in enumerate(text.split("\n"), 1):
        where = f"{rel}:{number}"
        for ch in line:
            if ord(ch) > 126 or (ord(ch) < 32 and ch != "\t"):
                raise BundleError(f"{where}: non-ASCII or control character U+{ord(ch):04X}; write it as a \\uXXXX escape")
        if _FORBIDDEN_TEXT.search(line):
            raise BundleError(f"{where}: the text '</script', '<!--' or '</style' cannot appear in a runtime source")
        if re.match(r"^\s*import\b", line) and not line.startswith("import "):
            raise BundleError(f"{where}: an import must start at column 0")
        if line.startswith("import ") or line.startswith("import{"):
            m = _IMPORT.match(line)
            if not m:
                raise BundleError(f"{where}: unsupported import statement (single-line `import {{ a, b }} from './x.js';` only): {line}")
            names = [n.strip() for n in m.group(1).split(",") if n.strip()]
            bad = [n for n in names if not _IDENT.match(n)]
            if bad or not names:
                raise BundleError(f"{where}: unsupported import names {bad or names}: aliases are not supported")
            mod.imports.append((names, resolve(rel, m.group(2))))
            continue
        if line.startswith("export"):
            m = _EXPORT.match(line)
            if not m:
                raise BundleError(f"{where}: unsupported export (only `export const|function|class|async function NAME`): {line}")
            mod.exports.append(m.group(1))
            line = line[len("export "):]
        mod.body.append(line)
    seen = set()
    for name in mod.exports:
        if name in seen:
            raise BundleError(f"{rel}: {name} is exported twice")
        seen.add(name)
    return mod


def _template_open_at_end(line: str) -> bool:
    """True when a template literal starts on this line and is still open at its end (a quote inside a template is text)."""
    quote = None
    prev = ""
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if quote in ("'", '"'):
            if c == "\\":
                i += 1
            elif c == quote:
                quote = None
        elif quote == "`":
            if c == "\\":
                i += 1
            elif c == "`":
                quote = None
        elif c in ("'", '"', "`"):
            quote = c
        elif c == "/" and line[i + 1:i + 2] == "/":
            return False
        elif c == "/" and prev in "(,=:[!&|?{};+-*%<>~^":  # a regular expression literal: its quotes and backticks are not strings
            j, in_class = i + 1, False
            while j < n:
                if line[j] == "\\":
                    j += 1
                elif line[j] == "[":
                    in_class = True
                elif line[j] == "]":
                    in_class = False
                elif line[j] == "/" and not in_class:
                    break
                j += 1
            i = j
        if not c.isspace():
            prev = c
        i += 1
    return quote == "`"


def strip_body(rel: str, lines: list) -> list:
    """The module body without whole-line comments, block comments that start a line, blank lines and indentation."""
    out, block = [], False
    for number, line in enumerate(lines, 1):
        text = line.strip()
        if block:
            block = "*/" not in text
            continue
        if not text or text.startswith("//"):
            continue
        if text.startswith("/*"):
            if "*/" not in text:
                block = True
                continue
            if text.endswith("*/") and text.count("/*") == 1:
                continue
        if _template_open_at_end(line):
            raise BundleError(f"{rel}:{number}: a template literal must not span lines (the bundler drops indentation and comment lines)")
        out.append(text)
    if block:
        raise BundleError(f"{rel}: a block comment is not closed")
    return out


def load_modules(src: Path) -> dict:
    return {rel: parse_module(rel, read_source(src / rel)) for rel in module_files(src)}


def order_modules(mods: dict) -> list:
    """Dependency order (every module after the modules it imports); ties by path. Cycles and missing targets raise."""
    order: list = []
    state: dict = {}

    def visit(rel: str, stack: list) -> None:
        if state.get(rel) == 2:
            return
        if state.get(rel) == 1:
            raise BundleError("import cycle: " + " -> ".join(stack + [rel]))
        if rel not in mods:
            raise BundleError(f"{stack[-1]}: imports {rel}, which is not a runtime module")
        state[rel] = 1
        for _names, target in mods[rel].imports:
            visit(target, stack + [rel])
        state[rel] = 2
        order.append(rel)

    for rel in sorted(mods):
        visit(rel, [])
    return order


def check_imports(mods: dict) -> None:
    for mod in mods.values():
        for names, target in mod.imports:
            if target not in mods:
                raise BundleError(f"{mod.rel}: imports {target}, which is not a runtime module")
            missing = [n for n in names if n not in mods[target].exports]
            if missing:
                raise BundleError(f"{mod.rel}: imports {missing} from {target}, which does not export them")


def bundle_runtime(mods: dict) -> str:
    check_imports(mods)
    order = order_modules(mods)
    if ENTRY_MODULE not in mods or ENTRY_NAME not in mods[ENTRY_MODULE].exports:
        raise BundleError(f"the entry module {ENTRY_MODULE} must export {ENTRY_NAME}")
    parts = ["(function () {", "'use strict';"]
    for rel in order:
        mod = mods[rel]
        parts.append(f"// ---- {rel}")
        parts.append(f"const {mod.ident} = (function () {{")
        parts.append("'use strict';")
        for names, target in mod.imports:
            parts.append(f"const {{ {', '.join(names)} }} = {mods[target].ident};")
        parts.append("\n".join(strip_body(rel, mod.body)))
        parts.append(f"return {{ {', '.join(mod.exports)} }};" if mod.exports else "return {};")
        parts.append("})();")
    parts.append(f"{mods[ENTRY_MODULE].ident}.{ENTRY_NAME}();")
    parts.append("})();")
    return "\n".join(parts) + "\n"


def strip_css(text: str) -> str:
    """The style sheet without comments and blank lines (no string in it contains a comment opener)."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return "\n".join(line.rstrip() for line in text.split("\n") if line.strip()) + "\n"


def bundle_i18n(viewer: Path) -> str:
    tables = {}
    for lang in LANGS:
        tables[lang] = json.loads(read_source(viewer / "i18n" / f"{lang}.json"))
    return json.dumps({"languages": list(LANGS), "tables": tables}, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_paths(viewer: Path) -> list:
    """Every input, relative to ``viewer/``, sorted."""
    out = [f"src/{rel}" for rel in module_files(viewer / "src")]
    out += ["src/atlas.css", "src/atlas.template.html"]
    out += [f"i18n/{lang}.json" for lang in LANGS]
    return sorted(out)


def bundle(viewer: "Path | str | None" = None, tool: "Path | None" = None) -> dict:
    """``{relative output path: bytes}`` for the four ``dist/`` files and ``SOURCES.sha256`` (nothing is written)."""
    viewer = Path(viewer) if viewer else DEFAULT_VIEWER
    tool = Path(tool) if tool else Path(__file__).resolve()
    src = viewer / "src"
    texts = {
        RUNTIME_OUT: bundle_runtime(load_modules(src)),
        CSS_OUT: strip_css(read_source(src / "atlas.css")),
        TEMPLATE_OUT: read_source(src / "atlas.template.html"),
        I18N_OUT: bundle_i18n(viewer),
    }
    if _FORBIDDEN_TEXT.search(texts[CSS_OUT]):
        raise BundleError(f"{CSS_OUT}: the text '</style', '</script' or '<!--' cannot appear in the style sheet")
    rows = {f"{p}": sha256_text(read_source(viewer / p)) for p in source_paths(viewer)}
    rows[TOOL_ENTRY] = sha256_text(normalise(tool.read_bytes()))
    for rel, text in texts.items():
        rows[rel] = sha256_text(text)
    sources = "".join(f"{rows[p]}  {p}\n" for p in sorted(rows))
    out = {rel: text.encode("utf-8") for rel, text in texts.items()}
    out[SOURCES_OUT] = sources.encode("utf-8")
    return out


def write(viewer: "Path | str | None" = None, tool: "Path | None" = None) -> list:
    """Bundle and write the outputs (LF, UTF-8, only when the bytes differ); returns the relative paths that changed."""
    viewer = Path(viewer) if viewer else DEFAULT_VIEWER
    changed = []
    for rel, data in sorted(bundle(viewer, tool).items()):
        path = viewer / rel
        if path.is_file() and path.read_bytes() == data:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        changed.append(rel)
    return changed


def parse_sources(text: str) -> dict:
    """``{path: sha256}`` of a ``SOURCES.sha256`` text."""
    out = {}
    for line in text.split("\n"):
        if not line.strip():
            continue
        digest, sep, path = line.partition("  ")
        if not sep or not re.fullmatch(r"[0-9a-f]{64}", digest) or not path:
            raise BundleError(f"bad line in {SOURCES_OUT}: {line!r}")
        out[path] = digest
    return out


def check(viewer: "Path | str | None" = None, tool: "Path | None" = None) -> list:
    """Problems found when the committed outputs are compared with a fresh bundle; ``[]`` means fresh.

    Messages name what is stale: a source whose hash differs from the record, an output that differs from a fresh bundle."""
    viewer = Path(viewer) if viewer else DEFAULT_VIEWER
    problems = []
    try:
        fresh = bundle(viewer, tool)
    except BundleError as ex:
        return [f"the sources do not bundle: {ex}"]
    recorded_path = viewer / SOURCES_OUT
    if not recorded_path.is_file():
        return [f"{SOURCES_OUT} is missing: run python tools/bundle_viewer.py"]
    recorded = parse_sources(read_source(recorded_path))
    now = parse_sources(fresh[SOURCES_OUT].decode("utf-8"))
    for path in sorted(set(recorded) | set(now)):
        if path not in now:
            problems.append(f"{path} is recorded but no longer an input")
        elif path not in recorded:
            problems.append(f"{path} is an input that {SOURCES_OUT} does not record")
        elif recorded[path] != now[path]:
            kind = "output" if path in OUTPUTS else "source"
            problems.append(f"{path}: {kind} hash differs from {SOURCES_OUT}")
    for rel in OUTPUTS:
        path = viewer / rel
        if not path.is_file():
            problems.append(f"{rel} is missing")
        elif normalise(path.read_bytes()).encode("utf-8") != fresh[rel]:       # a checkout with CRLF line endings is not stale
            problems.append(f"{rel} differs from a fresh bundle")
    return problems


def main(argv: "list | None" = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] not in ("--check", "--write"):
        print(__doc__)
        return 2
    try:
        if args and args[0] == "--check":
            problems = check()
            for line in problems:
                print("STALE:", line)
            print("viewer bundle is fresh" if not problems else f"viewer bundle is STALE ({len(problems)} problem(s)): run python tools/bundle_viewer.py")
            return 1 if problems else 0
        changed = write()
    except BundleError as ex:
        print("bundle error:", ex)
        return 1
    print("rewrote:" if changed else "nothing to rewrite (the bundle is fresh)", *changed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
