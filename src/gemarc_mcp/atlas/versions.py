"""Fingerprints and versions of what the atlas produced (design: ``docs/atlas/spec/versions.md``).

Three words, one rule each:

* a **fingerprint** says *what* the content is: the first eight hex digits of the SHA-256 of
  the bytes (the house rule; the slice fingerprint and the page sentinel use the same one);
* a **version label** says *when* that content was produced: the fingerprint plus the minute,
  ``<sha8>-<YYYYMMDD-HHMM>`` in UTC;
* the **ledger** says *how* it got there: one entry per build, with the fingerprint of every
  document and the label it carries.

A label never goes into the build output. The output must stay byte-identical between two
builds of one project (the determinism rule of ``build.py``), and a minute is not
reproducible. So the fingerprints live in ``dist/build-info.json`` (the trust root that
already carries the model stamp and the page hash) and the labels live in the ledger
(``.atlas/versions.json``), which is a tool state file, never a deliverable.

A document whose bytes did not change keeps its label: a label names a version of the
content, not a build. The label therefore changes exactly when the content does.

Standard library only. Writes need ``GEMARC_ALLOW_WRITE=1`` (the caller holds the lock).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..paths import GemArcError
from . import slices as sl

#: The schema of the ledger.
SCHEMA = "gemarc-atlas-versions/1"
#: The ledger file inside ``.atlas/``.
LEDGER_NAME = "versions.json"
#: How many builds the ledger keeps (the oldest entries are dropped).
LEDGER_CAP = 50
#: The minute format of a label.
LABEL_TIME = "%Y%m%d-%H%M"
#: The files that carry a fingerprint and must therefore not be inside it.
TRUST_ROOTS = ("build-info.json", "manifest.jsonl")
#: Files written by another module than the build (the oracle ledger owns the sheet).
SKIP_NAMES = ("oracle-sheet.md",)
#: Directories that are scratch, not deliverable.
SKIP_DIRS = (".verify",)
#: The largest file that is read to fingerprint it (a bigger one is refused, not read).
MAX_FILE_BYTES = 8 * 1024 * 1024
#: The dimension of a reading page (``read/1D/R01.md`` is the 1D layer).
DIM_RE = re.compile(r"^\d[A-Z]$")
#: The name of an archived slice version (``<slice>.<sha8>.<ext>``).
ARCHIVE_RE = re.compile(r"^([a-z0-9-]{1,40})\.([0-9a-f]{8})\.(?:atl|jsonl)$")

#: The kinds of file the inventory knows.
KINDS = ("doc", "read", "diagram", "prompt", "viewer", "twin", "data")


def sha8_of(data: bytes) -> str:
    """The first eight hex digits of the SHA-256 of ``data``."""
    return hashlib.sha256(data).hexdigest()[:8]


def label_of(sha8: str, when: datetime) -> str:
    """The version label of one fingerprint: ``<sha8>-<YYYYMMDD-HHMM>`` (the minute, UTC)."""
    return f"{sha8}-{when.strftime(LABEL_TIME)}"


def label_time(text: str) -> str:
    """The minute of a label, or ``""`` when the text is not one.

    A label is ``<sha8>-<YYYYMMDD>-<HHMM>``: the minute part is the last two pieces.
    """
    parts = text.split("-")
    if len(parts) != 3:
        return ""
    tail = f"{parts[1]}-{parts[2]}"
    if len(tail) != 13:
        return ""
    try:
        datetime.strptime(tail, LABEL_TIME)
    except ValueError:
        return ""
    return tail


def classify(rel: str) -> tuple[str, str]:
    """The kind and the dimension of one file of the output tree.

    The derived documents (``docs/<lang>/<LEVEL>.md`` and the per-requirement ones) are the
    1D layer; the reading pages carry their dimension in their path (``read/1D/`` ...).
    """
    parts = rel.split("/")
    if parts[0] == "docs":
        return ("doc", "1D")
    if parts[0] == "read":
        if len(parts) > 1 and DIM_RE.match(parts[1]):
            return ("read", parts[1])
        return ("read", "root")
    if parts[0] == "diagrams":
        return ("diagram", "")
    if parts[0] == "prompts":
        return ("prompt", "")
    if parts[0] == "screens":
        return ("read", "ui")
    if parts[0] == "site":
        return ("twin", "")
    if rel == "index.html":
        return ("viewer", "")
    return ("data", "")


def _walk(root: Path) -> Iterable[str]:
    """Every file of ``root`` that belongs to the deliverable, as a relative path."""
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel in TRUST_ROOTS or rel in SKIP_NAMES:
            continue
        if any(part in SKIP_DIRS for part in rel.split("/")):
            continue
        yield rel


def inventory(root: Path) -> list[dict]:
    """The fingerprint of every file of the output tree: ``[{p, k, dim, sha8, b}]``.

    Sorted by path. A file bigger than :data:`MAX_FILE_BYTES` is not read: it is listed
    without a fingerprint rather than swallowed (a hostile tree must not stall the build).
    """
    root = Path(root)
    out: list[dict] = []
    if not root.is_dir():
        return out
    for rel in _walk(root):
        path = root / rel
        try:
            size = path.stat().st_size
            if size > MAX_FILE_BYTES:
                out.append({"p": rel, "k": classify(rel)[0], "dim": classify(rel)[1], "sha8": "", "b": size})
                continue
            data = path.read_bytes()
        except OSError:
            continue
        kind, dim = classify(rel)
        out.append({"p": rel, "k": kind, "dim": dim, "sha8": sha8_of(data), "b": len(data)})
    out.sort(key=lambda row: row["p"])
    return out


def site_of(rows: Iterable[Mapping]) -> dict:
    """The fingerprint of the tree described by ``rows`` (an inventory): ``{"sha256", "sha8"}``.

    It is the SHA-256 over the sorted ``path fingerprint`` pairs, so two builds of one
    project give the same value and two different sites never do. The files that carry a
    fingerprint (the trust roots) and the scratch directory are not inside it.
    """
    pairs = [f"{row.get('p', '')} {row.get('sha8', '')}" for row in rows if row.get("sha8")]
    digest = hashlib.sha256("\n".join(pairs).encode("utf-8")).hexdigest()
    return {"sha256": digest, "sha8": digest[:8], "files": len(pairs)}


def site_fingerprint(root: Path) -> dict:
    """The fingerprint of the whole output tree: ``{"sha256", "sha8"}``."""
    return site_of(inventory(root))


# -- the ledger ---------------------------------------------------------------------------
def ledger_file(paths: Any) -> Path:
    """Where the ledger of one project lives."""
    return Path(paths.dot) / LEDGER_NAME


def read_ledger(paths: Any) -> dict:
    """The ledger of one project; an unreadable or foreign ledger reads as empty.

    A ledger is a tool state file, never a deliverable: a damaged one must not stop the
    reader, it only means the history is unknown.
    """
    empty = {"schema": SCHEMA, "entries": []}
    try:
        raw = sl.read_limited(ledger_file(paths), sl.RECORD_MAX_BYTES)
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, RecursionError, GemArcError):
        return empty
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return empty
    entries = data.get("entries")
    if not isinstance(entries, list):
        return empty
    return {"schema": SCHEMA, "entries": [e for e in entries if isinstance(e, dict)]}


def write_ledger(paths: Any, ledger: Mapping) -> None:
    """Write the ledger (the caller holds the project lock)."""
    path = ledger_file(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    sl.atomic_write(path, (json.dumps(ledger, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8"))


def record(paths: Any, site: Mapping, docs: Iterable[Mapping], model_sha8: str,
           when: datetime | None = None) -> dict:
    """Append one build to the ledger and return the entry.

    A document whose fingerprint did not change keeps the label and the minute it had, so a
    label names a version of the content and not a build. The site is treated the same way:
    a site whose fingerprint did not change keeps its label too. The ledger keeps the newest
    :data:`LEDGER_CAP` entries.
    """
    when = when or datetime.now(timezone.utc)
    ledger = read_ledger(paths)
    previous = ledger["entries"][-1] if ledger["entries"] else {}
    old_docs = previous.get("docs") if isinstance(previous.get("docs"), dict) else {}
    old_site = previous.get("site") if isinstance(previous.get("site"), dict) else {}
    rows: dict = {}
    for row in docs:
        path = row.get("p")
        sha8 = row.get("sha8")
        if not isinstance(path, str) or not isinstance(sha8, str) or not sha8:
            continue
        before = old_docs.get(path) if isinstance(old_docs.get(path), dict) else {}
        if before.get("sha8") == sha8 and isinstance(before.get("label"), str):
            rows[path] = {"sha8": sha8, "label": before["label"], "at": before.get("at", "")}
        else:
            rows[path] = {"sha8": sha8, "label": label_of(sha8, when), "at": when.strftime("%Y-%m-%dT%H:%M:%SZ")}
    site_sha8 = site.get("sha8", "")
    site_row = {"sha256": site.get("sha256", ""), "sha8": site_sha8}
    if old_site.get("sha8") == site_sha8 and isinstance(old_site.get("label"), str) and old_site.get("label"):
        site_row["label"] = old_site["label"]
        site_row["at"] = old_site.get("at", "")
    else:
        site_row["label"] = label_of(site_sha8, when)
        site_row["at"] = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = {"at": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "modelSha8": model_sha8, "site": site_row, "docs": rows}
    ledger["entries"].append(entry)
    del ledger["entries"][:-LEDGER_CAP]
    write_ledger(paths, ledger)
    return entry


def document_versions(ledger: Mapping) -> dict:
    """The version list of every document of the ledger: ``{path: [{sha8, label, at}]}``.

    Newest first. The order inside one entry is the order of the builds.
    """
    out: dict = {}
    for entry in ledger.get("entries", []) if isinstance(ledger, Mapping) else []:
        docs = entry.get("docs") if isinstance(entry.get("docs"), dict) else {}
        for path, row in docs.items():
            if not isinstance(row, dict):
                continue
            out.setdefault(path, []).append({"sha8": row.get("sha8", ""), "label": row.get("label", ""),
                                             "at": row.get("at", "")})
    for rows in out.values():
        rows.reverse()
    return out


def slice_versions(paths: Any) -> list[dict]:
    """The archived versions of every slice: ``[{slice, versions: [{sha8, at}]}]``.

    The archives live in ``.atlas/history/`` (the previous ten versions of every slice).
    They are on disk but no tool showed them; this is the reader for them.
    """
    out: list[dict] = []
    history = Path(paths.history)
    if not history.is_dir():
        return out
    rows: dict = {}
    for path in sorted(history.iterdir()):
        if not path.is_file():
            continue
        match = ARCHIVE_RE.match(path.name)
        if match is None:
            continue
        rows.setdefault(match.group(1), []).append({"sha8": match.group(2), "at": _mtime(path)})
    for name, versions in sorted(rows.items()):
        versions.sort(key=lambda row: row["at"])
        out.append({"slice": name, "versions": versions})
    return out


def _mtime(path: Path) -> str:
    """The minute of a file's last change, in UTC."""
    try:
        stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return ""
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")
