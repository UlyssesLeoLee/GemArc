"""The interface store: the write path of ``ui-*`` slices, independent of the 5D slice store (design A3 D-10, 4.1).

State is the directory: ``atlas/ui/<name>.atl`` are the committed slices, ``atlas/.atlas/ui/red/<name>.atl`` the red drafts,
``atlas/.atlas/ui/history/<name>.<sha8>.atl`` the previous versions (newest ten), ``atlas/.atlas/ui/puts.json`` the counters and
``atlas/.atlas/ui/lock`` the lock. **Nothing is created or touched outside ``atlas/ui`` and ``atlas/.atlas/ui``**: not the 5D slices, not
the 5D lock, not ``puts.json``, ``oracle.json`` or ``review.json`` of the 5D side, and no 5D commit hook (the interface has its own
``UI_COMMIT_HOOKS``). The 5D model is only *read*, by the caller, and handed in as ``five`` (an expanded model or a ``FiveIndex``).

``put_slice``
    Validates the incoming text merged with the committed rest of the prototype (slice mode, wave 1 and the deferred wave 3 rules).
    No ERROR: the previous version is archived, the new text replaces the slice atomically and any red draft of the slice is cleared.
    Any ERROR: **nothing is committed** and the whole submitted text is stored as the slice's red draft. ``mode="patch"`` applies row-level
    changes (``patch.py``) to the red draft when there is one, else to the committed slice; ``base=<sha8>`` is optimistic concurrency.
    A slice named ``ui-verdict-*`` holds only ``conform`` and ``diffs`` rows: writing one never changes ``ui_sha8`` (it has its own
    ``verdict_sha8``) and counts against its own put budget.

``route(name, text)``
    The first line decides which family a slice belongs to (``#atlas-ui 1`` interface, ``#atlas 1`` 5D); the ``ui-`` name prefix is only
    the second check, so a name with the prefix and a 5D header is a coded error, and 5D slices that happen to be named ``ui-layer`` stay 5D.

``ui_present(paths)``
    True only when ``atlas/ui`` or ``atlas/.atlas/ui/red`` holds a ``ui-*`` slice: a 5D slice named ``ui-layer`` in ``atlas/slices`` does not count.

``scan-<enc(screen)>.json`` (``atlas/.atlas/ui``) is the output of the ``screens scan`` command (``scan.py``): ``write_scan`` is the only writer, ``read_scans``
hands the files to the verdict rules (``verdict.py``, UV-006). A put of a ``ui-verdict-*`` slice also runs the verdict rules ``UV-001`` .. ``UV-008``
(``verdict.py``) over the incoming rows; ``check_store`` runs them over every committed verdict. The evidence roots they read come from
``GEMARC_ATLAS_ROOTS`` unless the caller passes ``verdict_roots``.

Writes are atomic and the lock is the 5D lock code (``slices.project_lock``) pointed at ``atlas/.atlas/ui``. Standard library only.
"""
from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from .. import project as pj
from .. import roots as rt
from .. import slices as sl
from ...paths import GemArcError
from . import atl, expand as ex, patch as pt, rules
from . import model as um
from . import verdict as vd

NAME_RE = re.compile(r"^ui-[a-z0-9-]{1,37}$")
MODES = ("replace", "patch")
MAX_SLICE_BYTES = sl.MAX_SLICE_BYTES            # 64 KiB: a larger slice is ST-008 (red draft)
HARD_TEXT_BYTES = sl.HARD_TEXT_BYTES            # a text above this is refused without being stored
MAX_UI_BYTES = 2 * 1024 * 1024                  # the committed slices and the red drafts of the interface together
HISTORY_CAP = sl.HISTORY_CAP

E_NAME = "GEMARC_ATLAS_UI_001"
E_LIMIT = "GEMARC_ATLAS_UI_002"
E_CONFLICT = "GEMARC_ATLAS_UI_003"
E_PATCH = "GEMARC_ATLAS_UI_005"
E_ARG = "GEMARC_ATLAS_UI_006"
E_NOTHING = "GEMARC_ATLAS_UI_007"
E_ROUTE_FIVE = "GEMARC_ATLAS_UI_008"
E_ROUTE_UI = "GEMARC_ATLAS_UI_009"


class UiError(sl.SliceError):
    """A refused request. A ``SliceError`` (a handler for the 5D store handles it too); ``code`` is a ``GEMARC_ATLAS_UI_NNN`` code."""


# ---------------------------------------------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class UiPaths:
    """Every path of the interface store of one project (nothing is created by building this object)."""

    atlas: Path         # <project>/atlas
    dot: Path           # <project>/atlas/.atlas
    ui: Path            # atlas/ui: the committed slices
    state: Path         # atlas/.atlas/ui
    red: Path
    history: Path
    lock: Path
    puts: Path
    lock_paths: object  # the 5D ``Paths`` with ``dot`` and ``lock`` pointed at ``state`` (only the lock helper reads it)

    @classmethod
    def of(cls, paths: "pj.Paths") -> "UiPaths":
        state = paths.dot / "ui"
        return cls(paths.atlas, paths.dot, paths.atlas / "ui", state, state / "red", state / "history", state / "lock", state / "puts.json",
                   dataclasses.replace(paths, dot=state, lock=state / "lock"))

    def directories(self) -> tuple:
        return (self.atlas, self.dot, self.ui, self.state, self.red, self.history)


def check_confined(root: "Path | None", upaths: UiPaths) -> None:
    """Refuse a write when a directory of the interface store is a link, a junction or a reparse point, or (with ``root``) resolves outside
    the workspace. The 5D ``check_confined`` does not know these directories."""
    for directory in upaths.directories():
        pj.refuse_link(directory)
        if root is None:
            continue
        try:
            resolved = directory.resolve(strict=False)
        except OSError:
            raise GemArcError(pj.E_CONFINED, "a project directory cannot be resolved") from None
        if resolved != root and root not in resolved.parents:
            raise GemArcError(pj.E_CONFINED, "a project directory points outside the workspace; nothing was written")


# ---------------------------------------------------------------------------------------------------------------
# Names and routing
# ---------------------------------------------------------------------------------------------------------------


def check_name(name: str) -> str:
    """The slice name if it is ``ui-`` plus 1-37 characters of ``[a-z0-9-]`` (at most 40 in all): nothing can leave the directory."""
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise UiError(E_NAME, "an interface slice name is ui- followed by 1-37 characters of [a-z0-9-], for example ui-10-login")
    return name


@dataclass(frozen=True)
class Route:
    """Where a ``put`` of ``(name, text)`` goes: ``family`` is ``ui``, ``five`` or ``none`` (no header and no ``ui-`` name: 5D by default);
    ``code`` and ``message`` are set when the pair is refused."""

    family: str
    code: str = ""
    message: str = ""

    @property
    def ok(self) -> bool:
        return not self.code


def first_line(text: str) -> str:
    """The first line that is neither blank nor a ``//`` comment, stripped (``""`` when there is none)."""
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip(" \t\r﻿")
        if line and not line.startswith("//"):
            return line
    return ""


def route(name: str, text: str) -> Route:
    """The first line decides (``#atlas-ui`` interface, ``#atlas`` 5D); the ``ui-`` prefix of the name is the second check."""
    head = first_line(text)
    prefixed = isinstance(name, str) and name.startswith("ui-")
    if atl.HEADER_RE.match(head):
        if not prefixed or not NAME_RE.fullmatch(name):
            return Route("ui", E_ROUTE_UI, f"the text starts with '{atl.HEADER_LINE}': an interface slice is named ui-<name> (got {str(name)[:40]!r})")
        return Route("ui")
    if atl.FIVE_D_HEADER_RE.match(head):
        if prefixed:
            return Route("five", E_ROUTE_FIVE, f"the slice name {str(name)[:40]!r} starts with ui-, which names interface slices, "
                         f"but the text starts with '#atlas 1' (a 5D slice): rename the slice, or write '{atl.HEADER_LINE}'")
        return Route("five")
    return Route("ui" if prefixed else "none")


# ---------------------------------------------------------------------------------------------------------------
# Reading the store (read-only: nothing here creates a file or a directory)
# ---------------------------------------------------------------------------------------------------------------

_FILE_RE = re.compile(r"^ui-[a-z0-9-]{1,37}\.atl$")


@dataclass(frozen=True)
class SliceFile:
    name: str       # file stem: the slice name
    path: Path

    @property
    def fname(self) -> str:
        return f"{self.name}.atl"

    @property
    def verdict(self) -> bool:
        return atl.is_verdict_name(self.name)

    def text(self) -> str:
        """The text (``TooBig`` past the hard limit: nothing larger is read)."""
        return sl.read_limited(self.path, HARD_TEXT_BYTES).decode("utf-8", errors="replace")


def _files(directory: Path) -> list:
    if not directory.is_dir():
        return []
    out = []
    for entry in sorted(directory.iterdir(), key=lambda p: p.name):
        if _FILE_RE.match(entry.name) and entry.is_file() and not entry.is_symlink():
            out.append(SliceFile(entry.name[:-4], entry))
    return out


def list_slices(upaths: UiPaths) -> list:
    """Committed interface slices in name order (temp files and dot files are ignored)."""
    return _files(upaths.ui)


def list_red(upaths: UiPaths) -> list:
    return _files(upaths.red)


def find_slice(upaths: UiPaths, name: str) -> "SliceFile | None":
    return next((s for s in list_slices(upaths) if s.name == name), None)


def find_red(upaths: UiPaths, name: str) -> "SliceFile | None":
    return next((s for s in list_red(upaths) if s.name == name), None)


def ui_present(upaths: UiPaths) -> bool:
    """True when the project has an interface: a committed ``ui-*`` slice in ``atlas/ui`` or a red draft in ``atlas/.atlas/ui/red``.

    Only these two directories are looked at, so a 5D slice named ``ui-layer`` (in ``atlas/slices``) does not make the interface present."""
    return bool(_files(upaths.ui) or _files(upaths.red))


def project_bytes(upaths: UiPaths, exclude: str = "") -> int:
    return sum(s.path.stat().st_size for s in list_slices(upaths) + list_red(upaths) if s.name != exclude)


def history_of(upaths: UiPaths, name: str) -> list:
    """History files of a slice, oldest first (order = file modification time, strictly increasing by construction)."""
    pattern = re.compile(rf"^{re.escape(name)}\.([0-9a-f]{{8}})\.atl$")
    found = [p for p in (upaths.history.iterdir() if upaths.history.is_dir() else ()) if pattern.match(p.name) and p.is_file()]
    return sorted(found, key=lambda p: (p.stat().st_mtime_ns, p.name))


def _read_texts(files: list) -> list:
    """``(fname, text)`` of the slices; a file larger than the hard limit (only a hand edit makes one) is read as an ST-008 stub, never loaded."""
    out = []
    for f in files:
        try:
            out.append((f.fname, f.text()))
        except sl.TooBig:
            out.append((f.fname, "#atlas-ui 1\n" + "x" * (MAX_SLICE_BYTES + 1)))
    return out


# ---------------------------------------------------------------------------------------------------------------
# Scan files (written by ``screens scan`` only) and the evidence roots of the verdict rules
# ---------------------------------------------------------------------------------------------------------------

SCAN_SCHEMA = "gemarc-atlas-ui-scan/1"
MAX_SCAN_BYTES = 2 * 1024 * 1024
_SCAN_FILE_RE = re.compile(r"^scan-[A-Za-z0-9._~+-]{1,120}\.json$")


def scan_path(upaths: UiPaths, enc: str) -> Path:
    """``atlas/.atlas/ui/scan-<enc>.json``; ``enc`` is the file-name encoding of the screen id (``reading.api.enc``)."""
    name = f"scan-{enc}.json"
    if not _SCAN_FILE_RE.match(name):
        raise UiError(E_ARG, "the scan file name is not a plain file name")
    return upaths.state / name


def write_scan(upaths: UiPaths, enc: str, data: bytes, *, root: "Path | None" = None, lock_wait: float = sl.LOCK_WAIT_S,
               lock_stale: float = sl.LOCK_STALE_S) -> Path:
    """Write one scan file (atomic, inside the interface lock, confined like a put). Returns the path; nothing else is touched."""
    if len(data) > MAX_SCAN_BYTES:
        raise UiError(E_LIMIT, f"a scan file is at most {MAX_SCAN_BYTES // (1024 * 1024)} MiB")
    target = scan_path(upaths, enc)
    check_confined(root, upaths)
    with sl.project_lock(upaths.lock_paths, lock_wait, lock_stale):
        sl.atomic_write(target, data)
    return target


def read_scans(upaths: UiPaths) -> dict:
    """``{screen id: content}`` of the valid ``scan-*.json`` files (read-only; a file that is too large, not JSON or not of this schema is skipped)."""
    out: dict = {}
    if not upaths.state.is_dir():
        return out
    for entry in sorted(upaths.state.iterdir(), key=lambda p: p.name):
        if not (_SCAN_FILE_RE.match(entry.name) and entry.is_file() and not entry.is_symlink()):
            continue
        try:
            data = json.loads(sl.read_limited(entry, MAX_SCAN_BYTES).decode("utf-8"))
        except (OSError, ValueError, RecursionError, sl.SliceError):
            continue
        if isinstance(data, dict) and data.get("schema") == SCAN_SCHEMA and isinstance(data.get("screen"), str):
            out[data["screen"]] = data
    return out


def verdict_roots_of(roots: "Mapping | None") -> dict:
    """The evidence roots the verdict rules read: ``roots`` as given, else ``GEMARC_ATLAS_ROOTS`` (an invalid variable means no root, never an error)."""
    if roots is not None:
        return dict(roots)
    try:
        return dict(rt.roots_from_env())
    except rt.RootsError:
        return {}


def _add_verdict_findings(upaths: UiPaths, result: "rules.UiCheckResult", *, mode: str, incoming: "list | None", roots: "Mapping | None",
                          wave_set=(1, 3)) -> None:
    """Run ``UV-001`` .. ``UV-008`` over the verdict rows of ``result.expansion`` and add the findings (only when there are rows)."""
    exp = result.expansion
    if exp is None or not (exp.verdict["conform"] or exp.verdict["diffs"]):
        return
    found = vd.check_expansion(exp, roots=verdict_roots_of(roots), scans=read_scans(upaths), mode=mode, incoming=incoming)
    keep = [d for d in found.diagnostics if vd.RULES.get(d.rule, (1,))[0] in wave_set]
    result.diagnostics = rules._dedupe(list(result.diagnostics) + keep)


# ---------------------------------------------------------------------------------------------------------------
# Counters and budgets
# ---------------------------------------------------------------------------------------------------------------


def put_budget(screens: int) -> int:
    """The put budget of the prototype slices: 4 plus 2 per started 4 screens (12 screens: 10 puts). Verdict slices have their own."""
    return 4 + 2 * math.ceil(max(int(screens), 0) / 4)


def verdict_budget(screens: int) -> int:
    """The put budget of the verdict slices: 2 plus 1 per started 2 screens."""
    return 2 + math.ceil(max(int(screens), 0) / 2)


def read_counters(upaths: UiPaths) -> dict:
    """``{"puts", "verdictPuts", "red": {slice: rounds}}`` as ``put`` recorded them (zero when the file is absent or unreadable)."""
    try:
        data = json.loads(sl.read_limited(upaths.puts, sl.COUNTER_MAX_BYTES).decode("utf-8")) if upaths.puts.is_file() else {}
    except (OSError, ValueError, RecursionError):
        data = {}
    if not isinstance(data, dict):
        data = {}

    def whole(key):
        value = data.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    red = data.get("red") if isinstance(data.get("red"), dict) else {}
    return {"puts": whole("puts"), "verdictPuts": whole("verdictPuts"),
            "red": {str(k): v for k, v in red.items() if isinstance(v, int) and not isinstance(v, bool) and v > 0}}


def _count_put(upaths: UiPaths, name: str, red: bool) -> None:
    """Keep ``atlas/.atlas/ui/puts.json`` (inside the lock): puts of the prototype, puts of the verdicts and the consecutive red puts per slice."""
    try:
        data = read_counters(upaths)
        key = "verdictPuts" if atl.is_verdict_name(name) else "puts"
        data[key] += 1
        if red:
            data["red"][name] = data["red"].get(name, 0) + 1
        else:
            data["red"].pop(name, None)
        sl.atomic_write(upaths.puts, (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
    except (OSError, sl.SliceError):
        pass  # the counters are advice for the loop guards: a failed write never fails a put


# ---------------------------------------------------------------------------------------------------------------
# Files: history, temps
# ---------------------------------------------------------------------------------------------------------------


def _unlink(path: Path) -> None:
    for attempt in range(30):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(0.01 * (attempt + 1))
    with contextlib.suppress(OSError):
        path.unlink()


def _archive(upaths: UiPaths, old: SliceFile, data: bytes) -> None:
    """Keep ``data`` (the previous version) in the history; the newest ``HISTORY_CAP`` files of the slice survive."""
    digest = hashlib.sha256(data).hexdigest()[:8]
    target = upaths.history / f"{old.name}.{digest}.atl"
    existing = history_of(upaths, old.name)
    sl.atomic_write(target, data)
    newest = max((p.stat().st_mtime_ns for p in existing), default=0)
    stamp = max(time.time_ns(), newest + 1)
    os.utime(target, ns=(stamp, stamp))  # a strictly newer mtime than every sibling: age order never ties
    for stale in history_of(upaths, old.name)[:-HISTORY_CAP]:
        _unlink(stale)


def sweep_temps(upaths: UiPaths) -> int:
    """Remove temp files that a crashed write left behind, in the interface directories only. Called under the lock."""
    removed = 0
    for directory in (upaths.ui, upaths.red, upaths.history, upaths.state):
        if directory.is_dir():
            for entry in directory.iterdir():
                if entry.name.startswith(".tmp-") and entry.is_file():
                    _unlink(entry)
                    removed += 1
    return removed


# ---------------------------------------------------------------------------------------------------------------
# put
# ---------------------------------------------------------------------------------------------------------------

#: Callables ``hook(upaths, outcome)`` run inside the lock after every commit of an interface slice. The 5D ``COMMIT_HOOKS`` never run for one.
UI_COMMIT_HOOKS: list = []


def register_commit_hook(hook: Callable) -> None:
    if hook not in UI_COMMIT_HOOKS:
        UI_COMMIT_HOOKS.append(hook)


@dataclass
class UiPutOutcome:
    status: str                       # committed | invalid
    slice: str
    sha8: str                         # of the text that was submitted / produced
    check: "rules.UiCheckResult"
    unchanged: bool = False
    red: bool = False                 # a red draft exists for the slice after this call
    previous: str = ""                # sha8 of the committed version before this call ("" when there was none)
    patch: "pt.PatchReport | None" = None
    counts: dict = field(default_factory=dict)
    text: str = ""
    cleared_red: bool = False
    hook_errors: list = field(default_factory=list)
    verdict: bool = False
    ui_sha8: str = ""                 # of the prototype after this call (a verdict slice never changes it)
    verdict_sha8: str = ""
    family: str = "ui"
    fmt: str = "atl"

    @property
    def committed(self) -> bool:
        return self.status == "committed"


def count_rows(fname: str, text: str) -> dict:
    """Rows per section of a slice text (rows the front-end kept)."""
    out: dict = {}
    for row in atl.parse_atl(text, fname).rows:
        out[row.section] = out.get(row.section, 0) + 1
    return out


def sha8(text: str) -> str:
    """First 8 hex digits of the SHA-256 of the stored form of ``text`` (the same function as the 5D store: ``base=`` works the same)."""
    return sl.sha8(text)


def put_slice(upaths: UiPaths, meta: "Mapping | None", name: str, text: str, *, mode: str = "replace", base: str = "",
              five: "Mapping | ex.FiveIndex | None" = None, root: "Path | None" = None, lock_wait: float = sl.LOCK_WAIT_S,
              lock_stale: float = sl.LOCK_STALE_S, verdict_roots: "Mapping | None" = None) -> UiPutOutcome:
    """Validate and store one interface slice (see the module doc). Raises ``UiError`` for a refused request; nothing is written then.

    ``five`` is the expanded 5D model (or its index) the optional links are checked against; ``None``: links are counted, not verified.
    ``root`` (the workspace root) additionally confines the write inside the workspace; links and junctions are always refused.
    A ``ui-verdict-*`` slice is also checked by the verdict rules (``verdict.py``) against the committed prototype; ``verdict_roots`` (alias -> directory)
    replaces ``GEMARC_ATLAS_ROOTS`` as the evidence roots of their ``basis`` cells (``{}``: no root).
    """
    check_name(name)
    if mode not in MODES:
        raise UiError(E_ARG, f"mode must be one of {', '.join(MODES)}")
    if not isinstance(text, str) or not text.strip():
        raise UiError(E_ARG, "the text is empty: nothing to put")
    if len(text.encode("utf-8", errors="replace")) > HARD_TEXT_BYTES:
        raise UiError(E_LIMIT, f"the text is larger than {HARD_TEXT_BYTES // 1024} KiB; a slice is at most {MAX_SLICE_BYTES // 1024} KiB (4-6 screens)")
    if not isinstance(base, str) or (base and not re.fullmatch(r"[0-9a-f]{8}", base)):
        raise UiError(E_ARG, "base is the 8 hex digits sha8 of the slice you read, or empty")
    if mode == "replace":
        routed = route(name, text)
        if not routed.ok:
            raise UiError(routed.code, routed.message)
    check_confined(root, upaths)
    verdict = atl.is_verdict_name(name)
    with sl.project_lock(upaths.lock_paths, lock_wait, lock_stale):
        sweep_temps(upaths)
        committed, red = find_slice(upaths, name), find_red(upaths, name)
        committed_text = _own_text(committed) if committed else ""
        red_text = _own_text(red) if red else ""
        committed_sha = sha8(committed_text) if committed else ""
        if base:
            known = {committed_sha} if committed else set()
            if red:
                known.add(sha8(red_text))
            if base not in known:
                raise UiError(E_CONFLICT, "base does not match the slice on disk; read it again (nothing was written)", conflict=True,
                              current=committed_sha, **({"draft": sha8(red_text)} if red else {}))
        patch = None
        if mode == "patch":
            target = red if red else committed
            if target is None:
                raise UiError(E_NOTHING, f"slice {name} has neither a committed version nor a red draft: put it first (mode=replace)")
            if red and _is_revalidation(text):
                new_text = red_text                      # an empty patch re-checks the red draft
            else:
                try:
                    patch = pt.apply_patch(red_text if red else committed_text, text)
                except pt.PatchError as ex_:
                    raise UiError(E_PATCH, str(ex_), nothing_written=True) from None
                new_text = patch.text
        else:
            new_text = sl.normalise(text)
        if len(new_text.encode("utf-8", errors="replace")) > HARD_TEXT_BYTES:
            raise UiError(E_LIMIT, f"the result is larger than {HARD_TEXT_BYTES // 1024} KiB: split the slice")
        data = new_text.encode("utf-8")
        if project_bytes(upaths, exclude=name) + len(data) > MAX_UI_BYTES:
            raise UiError(E_LIMIT, f"the interface would exceed {MAX_UI_BYTES // (1024 * 1024)} MiB")
        fname = f"{name}.atl"
        rest = _read_texts([s for s in list_slices(upaths) if s.name != name])
        result = rules.check_sources(rest + [(fname, new_text)], meta, five=five, mode="slice", incoming=[fname])
        if verdict:
            _add_verdict_findings(upaths, result, mode="slice", incoming=[fname], roots=verdict_roots)
        new_sha = sha8(new_text)
        counts = count_rows(fname, new_text)
        out = UiPutOutcome("invalid", name, new_sha, result, previous=committed_sha, patch=patch, counts=counts, text=new_text, verdict=verdict)
        _count_put(upaths, name, not result.ok)
        if not result.ok:
            if not (red and red_text == new_text):
                sl.atomic_write(upaths.red / fname, data)
            out.red = True
            _fill_hashes(out, result)
            return out
        out.status = "committed"
        out.unchanged = bool(committed and committed_text == new_text)
        if not out.unchanged:
            if committed is not None:
                _archive(upaths, committed, sl.read_bytes(committed.path))
            sl.atomic_write(upaths.ui / fname, data)
        for stale in list_red(upaths):
            if stale.name == name:
                _unlink(stale.path)
                out.cleared_red = True
        _fill_hashes(out, result)
        for hook in list(UI_COMMIT_HOOKS):
            try:
                hook(upaths, out)
            except Exception as ex_:  # noqa: BLE001 - a failing hook must not undo a commit
                out.hook_errors.append(f"{type(ex_).__name__}: {str(ex_)[:80]}")
        return out


def _own_text(f: SliceFile) -> str:
    """The stored form of a slice on disk; a file larger than the hard limit (only a hand edit makes one) is a refusal, never loaded."""
    try:
        return sl.normalise(f.text())
    except sl.TooBig:
        raise UiError(E_LIMIT, f"{f.fname} on disk is larger than {HARD_TEXT_BYTES // 1024} KiB (edited by hand?): nothing was written; "
                      "replace it with slices of at most 64 KiB") from None


def _fill_hashes(out: UiPutOutcome, result: "rules.UiCheckResult") -> None:
    exp = result.expansion
    if exp is not None:
        out.ui_sha8 = um.ui_sha8(exp.model)
        out.verdict_sha8 = um.verdict_sha8(exp.verdict)


_EMPTY_PATCH_LINE = re.compile(r"^\s*(#atlas\b.*|//.*)?$")


def _is_revalidation(text: str) -> bool:
    """A patch with nothing but blank lines, comments and the header line: it asks to check the red draft again."""
    return all(_EMPTY_PATCH_LINE.match(line) for line in sl.normalise(text).split("\n"))


# ---------------------------------------------------------------------------------------------------------------
# The committed prototype as a whole (read-only)
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class UiState:
    """The committed interface: the expansion of every committed slice, the hashes, the slice names and the red drafts."""

    expansion: "ex.UiExpansion"
    ui_sha8: str
    verdict_sha8: str
    slices: list
    red: list
    present: bool

    @property
    def model(self) -> dict:
        return self.expansion.model

    @property
    def screens(self) -> int:
        return len(self.expansion.model["screens"])


def load_state(upaths: UiPaths, meta: "Mapping | None" = None, five: "Mapping | ex.FiveIndex | None" = None) -> UiState:
    """Expand the committed slices (no rules). Creates nothing, even in a project without an interface."""
    files = list_slices(upaths)
    pairs = _read_texts(files)
    parsed = [atl.parse_atl(text, fname) for fname, text in pairs]
    expansion = ex.expand_ui(parsed, five, meta)
    red = [s.name for s in list_red(upaths)]
    return UiState(expansion, um.ui_sha8(expansion.model), um.verdict_sha8(expansion.verdict), [f.name for f in files], red, bool(files or red))


def check_store(upaths: UiPaths, meta: "Mapping | None" = None, *, five: "Mapping | ex.FiveIndex | None" = None, mode: str = "full",
                incoming: "list | None" = None, wave_set=(1, 3), verdict_roots: "Mapping | None" = None) -> "rules.UiCheckResult":
    """Check the committed interface (``mode="full"``: the closed world, every red draft is ST-009) or, in slice mode, only ``incoming`` slices.
    The verdict rules (``UV-*``) run over the verdict rows (every committed one in a full check). Read-only."""
    pairs = _read_texts(list_slices(upaths))
    red = [s.name for s in list_red(upaths)] if mode == "full" else []
    result = rules.check_sources(pairs, meta, five=five, mode=mode, incoming=incoming, red_drafts=red, wave_set=wave_set)
    _add_verdict_findings(upaths, result, mode=mode, incoming=incoming, roots=verdict_roots, wave_set=wave_set)
    return result
