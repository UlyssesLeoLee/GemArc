"""``manifest.jsonl``: one line per file of ``dist/`` plus a header line, written last (design A3 2.7.1, D-7, D-34, D-35).

``finalize(dist_dir, result)`` is called after the build has written every other file. It scans the directory, completes the rows of the files
``reading.build`` produced (bytes, lines and sha8 are taken from the bytes on disk, not from the build), classifies what the build did not
produce (``docs/``, ``prompts/``, ``diagrams/``, ``index.html``, ``oracle-sheet.md``, the files of ``screens/`` and ``site/``), builds the
header and writes the manifest. Not registered: ``build-info.json`` and ``screens/build-info.json`` (the trust roots: the manifest and they
cannot contain each other's hash), the manifest itself, ``.verify/``. ``oracle-sheet.md`` is registered as present, without bytes (it is
written after the build and rewritten once more). A stranger file is reported, never registered.

Every line is at most 500 characters, by construction: ``sum`` is cut at 100 characters, the ids of ``range`` at 60, ``p`` is at most 120;
a line that is still too long loses its ``sum`` and then its ``range``.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import slices as sl
from .budgets import BUDGETS
from . import names as N
from .model import READ_VERSION, SCHEMA_MANIFEST, SCHEMA_READ
from .pages import tok_estimate

LINE_MAX = BUDGETS["line_chars"]
SKIP_NAMES = {"manifest.jsonl", "build-info.json"}
SKIP_DIRS = {".verify"}
HARD_LIMIT = 8 * 1024 * 1024        # a file larger than this is registered without being read (a hostile or broken tree)
HTML_BYTES_PER_TOKEN = 1.7          # the token estimate of an HTML file (study-reading: the lowest ratio of HTML)
PART = re.compile(r"-\d+\.md$")
LANG_DOC = re.compile(r"^docs/[^/]+/(REQ|BD|DD)\.md$")


@dataclass
class Manifest:
    header: dict
    rows: list
    text: str
    sha8: str
    unregistered: list = field(default_factory=list)


def dumps(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fit_row(row: dict) -> str:
    """The JSON line of a row, at most ``LINE_MAX`` characters: drop ``sum``, then ``range``, then cut ``p``."""
    line = dumps(row)
    for key in ("sum", "range"):
        if len(line) <= LINE_MAX:
            break
        row.pop(key, None)
        line = dumps(row)
    return line


def _sha8(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:8]


def classify(path: str) -> dict | None:
    """The row of a file the build did not produce, by its path; ``None`` for a file that has no place in ``dist/``."""
    if LANG_DOC.match(path):
        return {"k": "doc", "cls": "handoff", "ai": False, "twin": None}
    if path.startswith("prompts/") and path.endswith(".md"):
        return {"k": "prompt", "cls": "handoff", "ai": False, "twin": None}
    if path == "oracle-sheet.md":
        return {"k": "sheet", "cls": "handoff", "ai": False, "twin": None}
    if path == "index.html":
        return {"k": "viewer", "cls": "viewer", "ai": False, "twin": None}
    if path.startswith("diagrams/") and path.endswith(".mmd"):
        return {"k": "diagram", "cls": "diagram", "ai": True, "twin": None}
    if path.startswith("screens/"):
        name = path[len("screens/"):]
        if name.endswith(".svg"):
            return {"k": "screen-svg", "cls": "svg", "ai": True, "twin": None}
        if name == "INDEX.md" or re.fullmatch(r"INDEX-\d+\.md", name):
            return {"k": "screen-index", "cls": "card", "ai": True}
        if name == "NAV.md" or re.fullmatch(r"NAV-\d+\.md", name):
            return {"k": "nav", "cls": "card", "ai": True}
        if re.fullmatch(r"[^/]+\.card(?:-\d+)?\.md", name):
            return {"k": "screen-card", "cls": "page", "ai": True}
        return None
    if path.startswith("site/") and path.endswith(".html"):
        return {"k": "twin", "cls": "twin", "ai": False, "twin": None}
    return None


def scan(dist: Path) -> list:
    """Relative POSIX paths of every file of ``dist`` that is a candidate for the manifest (``.verify``, the manifest and the trust roots excluded)."""
    out: list = []
    root = dist.resolve()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        parts = rel.split("/")
        if parts[0] in SKIP_DIRS or parts[-1] in SKIP_NAMES or parts[-1].startswith(".tmp-"):
            continue
        out.append(rel)
    return out


def build_header(rows: list, facts: dict) -> dict:
    classes: dict = {}
    for row in rows:
        classes[row["cls"]] = classes.get(row["cls"], 0) + 1
    ai = [r for r in rows if r.get("ai") and r["cls"] in ("card", "page", "svg")]
    header = {
        "schema": SCHEMA_MANIFEST, "readSchema": SCHEMA_READ, "build": {"readVersion": READ_VERSION}, "classes": dict(sorted(classes.items())),
        "files": len(rows), "aiFiles": len(ai), "aiBytes": sum(r.get("b", 0) for r in ai), "diagramFiles": classes.get("diagram", 0),
    }
    if any(r["k"] in ("screen-svg", "screen-card") for r in rows):
        header["build"]["layoutVersion"] = facts.get("layoutVersion", 1)
    for key in ("m", "u", "project", "lang", "mode", "content", "blockers", "qaSha8", "withheldCount", "ui"):
        if facts.get(key) is not None:
            header[key] = facts[key]
    return header


def link_twins(rows: list) -> None:
    """Fill ``twin`` of every Markdown row whose twin is on disk and ``twinOf`` of every twin row (the files of lane U and the twins of the
    screens INDEX and NAV are known to the manifest only by their paths; the first part of a screen card stands for its gallery)."""
    present = {r["p"] for r in rows}
    owner: dict = {}
    md = sorted((r for r in rows if r["p"].endswith(".md")), key=lambda r: (bool(PART.search(r["p"])), r["p"]))
    for row in md:
        twin = N.twin_path(row["p"])
        if twin is not None and twin in present:
            row["twin"] = twin
            owner.setdefault(twin, row["p"])
    for row in rows:
        if row["cls"] == "twin" and "twinOf" not in row and row["p"] in owner:
            row["twinOf"] = owner[row["p"]]


def finalize(dist_dir, result) -> Manifest:
    """Complete the rows, write ``manifest.jsonl`` last. ``result`` is the ``ReadResult`` of ``reading.build``."""
    dist = Path(dist_dir)
    known = {rec["p"]: dict(rec) for rec in result.records}
    rows: list = []
    unregistered: list = []
    for rel in scan(dist):
        path = dist / rel
        row = known.get(rel)
        if row is None:
            row = classify(rel)
            if row is None:
                unregistered.append(rel)
                continue
            row = {"p": rel, **row}
            if "twin" not in row:
                row["twin"] = None
        if rel == "oracle-sheet.md":
            rows.append({"p": rel, "k": "sheet", "cls": "handoff", "ai": False, "twin": None})
            continue
        size = path.stat().st_size
        if size > HARD_LIMIT:
            row.update({"b": size})
            rows.append(row)
            continue
        data = sl.read_limited(path, HARD_LIMIT)
        row["b"], row["sha8"] = len(data), _sha8(data)
        row["l"] = data.count(b"\n") + (0 if data.endswith(b"\n") or not data else 1)
        if row["cls"] in ("handoff", "viewer"):
            row.setdefault("tok", 0)
        elif "tok" not in row:
            if row["cls"] == "twin":
                row["tok"] = math.ceil(len(data) / HTML_BYTES_PER_TOKEN)
            else:
                row["tok"] = tok_estimate(data.decode("utf-8", "replace"), result.header.get("lang") or "en-US")
        rows.append(row)
    for rel in known:
        if rel not in {r["p"] for r in rows}:
            rows.append({**known[rel], "missing": True})        # produced by the build and not on disk: RS-109 reports it
    rows.sort(key=lambda r: r["p"])
    link_twins(rows)
    header = build_header(rows, result.header)
    lines = [dumps(header)] + [fit_row(r) for r in rows]
    text = "\n".join(lines) + "\n"
    data = text.encode("utf-8")
    sl.atomic_write(dist / "manifest.jsonl", data)
    return Manifest(header, rows, text, _sha8(data), unregistered)
