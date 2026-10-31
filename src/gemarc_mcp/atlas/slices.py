"""The slice store: put (replace | patch), red drafts, row-level patch, history, lock, ownership (design section 6).

State is the directory (``project.Paths``): ``slices/<name>.atl|.jsonl`` are the committed slices, ``.atlas/red/<name>.*`` the red
drafts, ``.atlas/history/<name>.<sha8>.*`` the previous versions (newest ten), ``.atlas/lock`` the project lock. There is no
server session: every call reads the directory under the lock, so two processes can write two slices at the same time.

``put_slice``
    Validates the incoming text merged with the committed rest of the project (slice mode, wave 1 via ``slicecheck.check``).
    No ERROR: the previous version is archived, the new text replaces the slice atomically and any red draft of the slice is
    cleared. Any ERROR: **nothing is committed** and the whole submitted text is stored as the slice's red draft (never lost,
    never missing a valid row). ``mode="patch"`` applies row-level changes to the red draft when there is one, else to the
    committed slice; ``base=<sha8>`` is optimistic concurrency (a mismatch writes nothing); the same text gives the same
    ``sha8`` and ``unchanged``.

Row-level patch (``apply_patch``), by identity, at the text level so no row (valid or not) of the base is rewritten:
    * a row with the identity of a base row replaces it (the new row is appended; the old line is removed); a new identity is
      appended; ``-<key cells>`` deletes (``-svc.old`` under ``## nodes``, ``-a | calls | b`` under ``## edges``);
    * identity: the first cell (``id``; ``case`` for ``expect``), edges ``from|rel|to`` (+ ``alias``), transitions
      ``machine`` + ``from|to``; an events block (``@set case=<id>`` or ``case=`` on the rows) replaces **all** events of that
      case and an empty block deletes them;
    * when several base rows share an identity (the duplicate that ST-002 reports) an operation takes the **last** one, so the
      first, valid definition stays;
    * appended rows go under a fresh canonical section header (which resets ``@set`` defaults), or into the last section when
      that section has no ``@set`` line and the patch brings none. A row that replaces a base row and brings no ``@set`` line of
      its own takes over the ``@set`` lines the old row was written under (so ``room=`` and ``kind=`` need not be repeated). Everything else of the base is kept verbatim.
    * ``.jsonl``: the same operations, a delete line is ``-{"_section":"nodes","id":"svc.old"}`` and an events block is
      deleted with ``-{"_section":"events","case":"<id>"}``.

Writes are atomic (temp file in the same directory, fsync, ``os.replace`` with retries for Windows), the lock is an exclusive
create (it holds the writer's process id) that goes stale after 60 s or at once when that process no longer exists, ``commit hooks`` (the oracle lane's seal) run inside the lock after a commit.
Standard library only.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import random
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from . import diag as dg
from . import expand as ex
from . import slicecheck
from .format import atl as atl_fmt
from .format import rows as rw
from .format.rows import SECTIONS
from .project import Paths

SLICE_NAME_RE = re.compile(r"^[a-z0-9-]{1,40}$")
FORMATS = ("atl", "jsonl")
MODES = ("replace", "patch")
MAX_SLICE_BYTES = rw.MAX_SLICE_BYTES            # 64 KiB: a larger slice is ST-008 (red draft)
HARD_TEXT_BYTES = 2 * MAX_SLICE_BYTES           # a text above this is refused without being stored
MAX_PROJECT_BYTES = 8 * 1024 * 1024
COUNTER_MAX_BYTES = 64 * 1024                   # the tool's own small state files (put counters, the ledger anchor): a larger one was not written by the tool
RECORD_MAX_BYTES = 8 * 1024 * 1024              # the tool's own records that grow with the project (evidence stamp, build info): a larger one is unreadable
HISTORY_CAP = 10
LOCK_STALE_S = 60.0
LOCK_WAIT_S = 20.0

E_NAME = "GEMARC_ATLAS_SLICE_001"
E_LIMIT = "GEMARC_ATLAS_SLICE_002"
E_CONFLICT = "GEMARC_ATLAS_SLICE_003"
E_LOCK = "GEMARC_ATLAS_SLICE_004"
E_PATCH = "GEMARC_ATLAS_SLICE_005"
E_ARG = "GEMARC_ATLAS_SLICE_006"
E_NOTHING = "GEMARC_ATLAS_SLICE_007"

_FILE_RE = re.compile(r"^[^.\\/][^\\/]*\.(atl|jsonl)$")
_RESERVED = frozenset({"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)})

# the two functions below are module attributes so that a test can interpose between the temp write and the replace
_replace = os.replace


class SliceError(Exception):
    """A refused request. ``code`` is a ``GEMARC_ATLAS_SLICE_NNN`` code; ``extra`` is merged into the tool reply."""

    def __init__(self, code: str, message: str, **extra):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.extra = code, message, extra


# ---------------------------------------------------------------------------------------------------------------
# Names, text normalisation, hashes
# ---------------------------------------------------------------------------------------------------------------


def check_name(name: str) -> str:
    """The slice name if it is ``[a-z0-9-]{1,40}`` (no separators, no dots: nothing can leave the slices directory)."""
    if not isinstance(name, str) or not SLICE_NAME_RE.fullmatch(name) or name in _RESERVED:
        raise SliceError(E_NAME, "a slice name is 1-40 characters of [a-z0-9-], for example 10-structure")
    return name


def check_format(fmt: str) -> str:
    if fmt not in FORMATS:
        raise SliceError(E_ARG, f"format must be one of {', '.join(FORMATS)}")
    return fmt


def normalise(text: str) -> str:
    """Stored form: no BOM, LF newlines, no trailing blank lines, exactly one final LF (empty text stays empty)."""
    if text.startswith("﻿"):
        text = text[1:]
    text = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    return text + "\n" if text else ""


def sha8(text: str) -> str:
    """First 8 hex digits of the SHA-256 of the stored form of ``text``."""
    return hashlib.sha256(normalise(text).encode("utf-8", errors="replace")).hexdigest()[:8]


def _encode(text: str) -> bytes:
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError as ex:
        raise SliceError(E_ARG, "the text contains characters that are not valid Unicode") from ex


# ---------------------------------------------------------------------------------------------------------------
# Files: atomic writes, retrying reads, the lock
# ---------------------------------------------------------------------------------------------------------------


def replace_with_retry(source: Path, target: Path, attempts: int = 60) -> None:
    """``os.replace`` that survives a reader holding the target open (``PermissionError`` on Windows)."""
    delay = 0.002
    for attempt in range(attempts):
        try:
            _replace(source, target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise _held_open() from None
            time.sleep(delay)
            delay = min(delay * 1.6, 0.05)


def _held_open() -> "SliceError":
    return SliceError(E_LOCK, "a file of the project is held open by another program (an indexer or virus scanner?); "
                      "nothing was lost, try again in a moment", retry=True)


def atomic_write(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` so that a reader (or a crash) sees the old file or the new one, never a mixture."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".tmp-{os.getpid()}-{secrets.token_hex(4)}-{path.name}")
    try:
        with open(temp, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        replace_with_retry(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise


def read_bytes(path: Path, attempts: int = 40) -> bytes:
    """``path.read_bytes()`` that waits out a replace in flight (``PermissionError`` on Windows)."""
    delay = 0.002
    for attempt in range(attempts):
        try:
            return path.read_bytes()
        except PermissionError:
            if attempt == attempts - 1:
                raise _held_open() from None
            time.sleep(delay)
            delay = min(delay * 1.6, 0.05)
    raise AssertionError("unreachable")


class TooBig(ValueError):
    """A file past its size limit (a ``ValueError`` so that every reader that treats a damaged file as unreadable treats this the same way)."""


def read_prefix(path: Path, limit: int, attempts: int = 40) -> "tuple[bytes, bool]":
    """``(first limit bytes, truncated)`` of a file, read through ``open(...).read(limit + 1)``: a file larger than ``limit`` (a sparse
    file of a gigabyte included) is never loaded whole, and the caller decides what a cut file means. Waits out a replace in flight."""
    delay = 0.002
    for attempt in range(attempts):
        try:
            with open(path, "rb") as handle:
                data = handle.read(limit + 1)
            return data[:limit], len(data) > limit
        except PermissionError:
            if attempt == attempts - 1:
                raise _held_open() from None
            time.sleep(delay)
            delay = min(delay * 1.6, 0.05)
    raise AssertionError("unreachable")


def read_limited(path: Path, limit: int) -> bytes:
    """The whole file, or ``TooBig`` when it is larger than ``limit`` bytes (nothing past ``limit + 1`` bytes is read)."""
    data, truncated = read_prefix(path, limit)
    if truncated:
        raise TooBig(f"{path.name} is larger than {limit} bytes")
    return data


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


def _pid_alive(pid: int) -> bool:
    """Whether a process with this id exists (conservative: ``True`` when it cannot be told)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
            kernel.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
            kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
            handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return ctypes.get_last_error() != 87  # 87 = ERROR_INVALID_PARAMETER: no such process
            try:
                code = ctypes.c_ulong()
                if kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return code.value == 259  # STILL_ACTIVE
                return True
            finally:
                kernel.CloseHandle(handle)
        except Exception:  # noqa: BLE001 - never decide "dead" on a failed probe
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _lock_pid(content: bytes) -> "int | None":
    """The process id written into a lock (``<pid>:<token>``), or ``None`` when the lock has none (still being written)."""
    head = content.split(b":", 1)[0]
    return int(head) if head.isdigit() and len(head) < 10 else None


def _lock_is_stale(mtime: float, content: bytes, stale: float) -> bool:
    """A lock is stale when it is older than ``stale`` seconds or when the process that wrote it is gone."""
    if time.time() - mtime > stale:
        return True
    pid = _lock_pid(content)
    return pid is not None and pid != os.getpid() and not _pid_alive(pid)


def _break_if_stale(lock: Path, stale: float) -> bool:
    """Remove a stale lock. ``True`` when the caller should simply try to take the lock again.

    Staleness is decided twice: on the lock as it was seen, and again on the file that the atomic rename really removed, so a
    live lock that replaced the stale one in between is put back (``os.link``) instead of being deleted.
    """
    try:
        stat = lock.stat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    try:
        seen = lock.read_bytes()
    except OSError:
        seen = b""
    if not _lock_is_stale(stat.st_mtime, seen, stale):
        return False
    grave = lock.with_name(f"lock.stale-{secrets.token_hex(4)}")
    try:
        os.replace(lock, grave)  # atomic: of several processes that found the same stale lock only one gets here
    except OSError:
        return True
    try:
        taken = grave.stat()
        content = grave.read_bytes()
        if not _lock_is_stale(taken.st_mtime, content, stale):  # a live lock took its place between stat and rename: give it back
            with contextlib.suppress(OSError):
                os.link(grave, lock)
    except OSError:
        pass
    with contextlib.suppress(OSError):
        grave.unlink()
    return True


@contextlib.contextmanager
def project_lock(paths: Paths, wait: float = LOCK_WAIT_S, stale: float = LOCK_STALE_S):
    """Exclusive project lock (``.atlas/lock``: exclusive create, stale after ``stale`` seconds). Waits up to ``wait`` seconds."""
    paths.dot.mkdir(parents=True, exist_ok=True)
    token = f"{os.getpid()}:{secrets.token_hex(8)}".encode()
    deadline = time.monotonic() + wait
    while True:
        try:
            fd = os.open(paths.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, PermissionError):  # PermissionError: a lock that is being deleted (Windows)
            if _break_if_stale(paths.lock, stale):
                continue
            if time.monotonic() > deadline:
                raise SliceError(E_LOCK, f"the project is locked by another write (waited {wait:g} s); try again", retry=True) from None
            time.sleep(0.01 + random.random() * 0.02)
            continue
        with os.fdopen(fd, "wb") as stream:
            stream.write(token)
        break
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            if paths.lock.read_bytes() == token:
                _unlink(paths.lock)


# ---------------------------------------------------------------------------------------------------------------
# Reading the store
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SliceFile:
    name: str       # file stem: the slice name
    fmt: str        # atl | jsonl
    path: Path

    @property
    def fname(self) -> str:
        return f"{self.name}.{self.fmt}"

    def data(self) -> bytes:
        return read_bytes(self.path)

    def text(self) -> str:
        return self.data().decode("utf-8", errors="replace")


def _files(directory: Path) -> list:
    if not directory.is_dir():
        return []
    out = []
    for entry in sorted(directory.iterdir(), key=lambda p: p.name):
        if _FILE_RE.match(entry.name) and entry.is_file() and not entry.is_symlink():
            stem, _, ext = entry.name.rpartition(".")
            out.append(SliceFile(stem, ext, entry))
    return out


def list_slices(paths: Paths) -> list:
    """Committed slices in name order (any ``.atl`` / ``.jsonl`` file of ``slices/``; temp files and dot files are ignored)."""
    return _files(paths.slices)


def list_red(paths: Paths) -> list:
    return _files(paths.red)


def find_slice(paths: Paths, name: str) -> "SliceFile | None":
    return next((s for s in list_slices(paths) if s.name == name), None)


def find_red(paths: Paths, name: str) -> "SliceFile | None":
    return next((s for s in list_red(paths) if s.name == name), None)


def project_bytes(paths: Paths, exclude: str = "") -> int:
    """Bytes of the committed slices and red drafts (``exclude`` names a slice whose files are not counted)."""
    return sum(s.path.stat().st_size for s in list_slices(paths) + list_red(paths) if s.name != exclude)


def history_of(paths: Paths, name: str) -> list:
    """History files of a slice, oldest first (order = file modification time, strictly increasing by construction)."""
    pattern = re.compile(rf"^{re.escape(name)}\.([0-9a-f]{{8}})\.(atl|jsonl)$")
    found = [p for p in (paths.history.iterdir() if paths.history.is_dir() else ()) if pattern.match(p.name) and p.is_file()]
    return sorted(found, key=lambda p: (p.stat().st_mtime_ns, p.name))


def _archive(paths: Paths, old: SliceFile, data: bytes) -> None:
    """Keep ``data`` (the previous version) in the history; the newest ``HISTORY_CAP`` files of the slice survive."""
    digest = hashlib.sha256(data).hexdigest()[:8]
    target = paths.history / f"{old.name}.{digest}.{old.fmt}"
    existing = history_of(paths, old.name)
    atomic_write(target, data)
    newest = max((p.stat().st_mtime_ns for p in existing), default=0)
    stamp = max(time.time_ns(), newest + 1)
    os.utime(target, ns=(stamp, stamp))  # a strictly newer mtime than every sibling: age order never ties
    ordered = history_of(paths, old.name)
    for stale in ordered[:-HISTORY_CAP]:
        _unlink(stale)


def sweep_temps(paths: Paths) -> int:
    """Remove temp files that a crashed write left behind. Only called under the lock (every writer holds it)."""
    removed = 0
    for directory in (paths.slices, paths.red, paths.history, paths.dot):
        if directory.is_dir():
            for entry in directory.iterdir():
                if entry.name.startswith(".tmp-") and entry.is_file():
                    _unlink(entry)
                    removed += 1
    return removed


# ---------------------------------------------------------------------------------------------------------------
# Ownership: which slice defines which id
# ---------------------------------------------------------------------------------------------------------------

_DEFINES = ("buildings", "floors", "rooms", "nodes", "evidences", "machines", "tests", "cases", "interventions", "requirements", "tbds",
            "policies")


def ownership_map(parsed: Iterable[rw.ParsedSlice]) -> dict:
    """``{id: "file:line"}`` of the first definition of every authored id (edges by alias or ``from:rel:to``)."""
    owners: dict[str, str] = {}
    for sl in parsed:
        for row in sl.rows:
            if row.section in _DEFINES:
                owners.setdefault(row.values["id"], row.at)
            elif row.section == "edges":
                ident = row.values.get("alias") or f"{row.values['from']}:{row.values['rel']}:{row.values['to']}"
                owners.setdefault(ident, row.at)
    return owners


# ---------------------------------------------------------------------------------------------------------------
# Patch: scanning
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class _U:
    kind: str                   # blank comment header section set final pending row other orphan
    text: str
    section: str = ""
    inst: int = 0               # section instance number (a ``## name`` line starts a new one)
    key: "tuple | None" = None
    delete: bool = False
    sets: tuple = ()            # the @set lines in force (.atl), cumulative within the instance
    case: str = ""              # events: the case of the row, or of the ``@set case=`` line
    arg: tuple = ()             # final: (section,) / pending: ids


_ID_COLUMN = {name: "id" for name in _DEFINES}
_ID_COLUMN["expect"] = "case"


def _unq(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            return str(json.loads(value))
        except ValueError:
            return value[1:-1]
    return value


def _naive_cells(body: str, count: int) -> tuple:
    parts = re.split(r"(?<!\\)\|", body)
    cells = [_unq(p.replace("\\|", "|")) for p in parts[:count]]
    return cells, ("|".join(parts[count:]) if len(parts) > count else "")


def _cells(body: str, spec: rw.Section) -> tuple:
    need = len(spec.columns)
    try:
        cells, rest, problem = atl_fmt._split_row(body, spec)
    except Exception:  # noqa: BLE001 - a defective row must never stop a patch; fall back to the naive split
        cells, rest, problem = [], None, True
    if problem or len(cells) < need:
        cells, rest = _naive_cells(body, need)
    else:
        cells = [str(c) for c in cells]
    return cells, rest or ""


def _atl_values(section: str, body: str, defaults: Mapping) -> dict:
    """The values of the identity cells of one row: columns by position, options by name; ``~`` and absent options take ``@set``."""
    spec = SECTIONS[section]
    cells, rest = _cells(body, spec)
    options = {}
    if rest.strip():
        items, _problem = atl_fmt.tokenize_options(rest)
        options = {k: str(v) for k, v in items if isinstance(v, str)}
    values = {}
    for index, col in enumerate(spec.columns):
        value = cells[index] if index < len(cells) else None
        if value == "~":
            value = defaults.get(col.name)
        if value:
            values[col.name] = value
    for name, value in options.items():
        values.setdefault(name, value)
    for name, value in defaults.items():
        values.setdefault(name, value)
    return values


def _identity(section: str, values: Mapping) -> "tuple | None":
    """The identity of a row (``None`` when a cell of it is missing)."""
    if section in _ID_COLUMN:
        v = values.get(_ID_COLUMN[section])
        return (section, v) if v else None
    if section == "edges":
        parts = [values.get("from"), values.get("rel"), values.get("to")]
        return ("edges", *parts, values.get("alias") or "") if all(parts) else None
    if section == "transitions":
        parts = [values.get("machine"), values.get("from"), values.get("to")]
        return ("transitions", *parts) if all(parts) else None
    if section == "events":
        return ("events", values["case"]) if values.get("case") else None
    return None


def _scan_atl(text: str, *, patch: bool) -> tuple:
    units, errors = [], []
    section, inst, defaults, sets = "", 0, {}, []
    for number, raw in enumerate(text.split("\n"), start=1):
        line = raw.strip(" \t\r")
        if not line:
            units.append(_U("blank", raw))
        elif line.startswith("//"):
            units.append(_U("comment", raw))
        elif line.startswith("#atlas"):
            units.append(_U("header", raw))
        elif line.startswith("## ") or line == "##":
            name = line[2:].strip().partition(":")[0].strip()
            inst += 1
            defaults, sets = {}, []
            if name in SECTIONS:
                section = name
            else:
                section = ""
                if patch:
                    errors.append(f"line {number}: unknown section '{name or line[:20]}'")
            units.append(_U("section", raw, section, inst))
        elif line.startswith("@set") and (len(line) == 4 or line[4] in " \t"):
            sets.append(line)
            items, _problem = atl_fmt.tokenize_options(line[4:])
            case = ""
            for key, value in items:
                if isinstance(value, str) and not isinstance(value, bool):
                    if value == "":
                        defaults.pop(key, None)
                    else:
                        defaults[key] = str(value)
                        if key == "case" and section == "events":
                            case = str(value)
            units.append(_U("set", raw, section, inst, sets=tuple(sets), case=case))
        elif line.startswith("@final") and (len(line) == 6 or line[6] in " \t"):
            arg = line[6:].strip() or section
            units.append(_U("final", raw, section, inst, arg=(arg,)))
        elif line.startswith("@pending") and (len(line) == 8 or line[8] in " \t"):
            ids = tuple(x.strip() for x in line[8:].split(",") if x.strip())
            units.append(_U("pending", raw, section, inst, arg=ids))
        elif line.startswith("@"):
            units.append(_U("other", raw, section, inst))
        elif not section:
            units.append(_U("orphan", raw))
            if patch:
                errors.append(f"line {number}: a row outside a '## section:' block")
        else:
            delete = patch and line.startswith("-")
            body = line[1:].lstrip() if delete else line
            values = _atl_values(section, body, defaults)
            case = values.get("case", "") if section == "events" else ""
            units.append(_U("row", body if patch else raw, section, inst, _identity(section, values), delete, tuple(sets), case))
    return units, errors


def _scan_jsonl(text: str, *, patch: bool) -> tuple:
    units, errors = [], []
    for number, raw in enumerate(text.split("\n"), start=1):
        line = raw.strip(" \t\r")
        if not line:
            units.append(_U("blank", raw))
            continue
        delete = patch and line.startswith("-")
        body = line[1:].lstrip() if delete else line
        try:
            obj = json.loads(body)
        except (ValueError, RecursionError):   # RecursionError: a line nested past the decoder's limit is "not an object"
            obj = None
        if not isinstance(obj, dict):
            units.append(_U("other", raw))
            if patch:
                errors.append(f"line {number}: not a JSON object")
            continue
        if "_atlas" in obj and "_section" not in obj:
            units.append(_U("header", raw))
        elif "_final" in obj and len(obj) == 1:
            units.append(_U("final", raw, arg=(str(obj["_final"]),)))
        elif "_pending" in obj and len(obj) == 1 and isinstance(obj["_pending"], list):
            units.append(_U("pending", raw, arg=tuple(str(x) for x in obj["_pending"])))
        elif isinstance(obj.get("_section"), str) and obj["_section"] in SECTIONS:
            section = obj["_section"]
            values = {k: v for k, v in obj.items() if isinstance(v, str)}
            case = values.get("case", "") if section == "events" else ""
            units.append(_U("row", body if patch else raw, section, 0, _identity(section, values), delete, (), case))
        else:
            units.append(_U("other", raw))
            if patch:
                errors.append(f"line {number}: unknown or missing '_section'")
    return units, errors


# ---------------------------------------------------------------------------------------------------------------
# Patch: application
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class PatchReport:
    text: str
    replaced: int = 0
    added: int = 0
    deleted: int = 0
    blocks: list = field(default_factory=list)      # cases whose events were replaced
    missed: list = field(default_factory=list)      # deletes that matched nothing

    def as_dict(self) -> dict:
        out = {"replaced": self.replaced, "added": self.added, "deleted": self.deleted}
        if self.blocks:
            out["event_blocks"] = list(self.blocks)
        if self.missed:
            out["nothing_to_delete"] = self.missed[:8]
        return out


def _show(key: tuple) -> str:
    return " | ".join(str(x) for x in key[1:] if x != "")


def apply_patch(base_text: str, patch_text: str, fmt: str = "atl") -> PatchReport:
    """Apply row-level changes (see the module doc) to ``base_text`` and return the new text and what happened."""
    check_format(fmt)
    base_text, patch_text = normalise(base_text), normalise(patch_text)
    scan = _scan_atl if fmt == "atl" else _scan_jsonl
    base, _ = scan(base_text, patch=False)
    ops, errors = scan(patch_text, patch=True)
    if errors:
        raise SliceError(E_PATCH, "the patch cannot be read: " + "; ".join(errors[:3]), nothing_written=True)
    if not any(u.kind in ("row", "final", "pending") or (u.kind == "set" and u.case) for u in ops):
        raise SliceError(E_PATCH, "the patch has no rows, no events block and no @final/@pending line", nothing_written=True)
    removed: set = set()                 # indexes into ``base``
    touched: set = set()                 # section instances that lost a row
    added: list = []                     # patch units in order
    report = PatchReport("")
    seen_cases: set = set()

    def drop_base(match) -> list:
        return [i for i, u in enumerate(base) if i not in removed and u.kind == "row" and match(u)]

    def drop_added(match) -> int:
        n = len(added)
        added[:] = [u for u in added if not match(u)]
        return n - len(added)

    def take_block(case: str) -> None:
        if case in seen_cases:
            return
        seen_cases.add(case)
        report.blocks.append(case)
        for i in drop_base(lambda u: u.section == "events" and u.case == case):
            removed.add(i)
            touched.add(base[i].inst)

    for u in ops:
        if u.kind == "set" and u.case:
            take_block(u.case)
        elif u.kind == "row" and u.section == "events":
            if u.case:
                take_block(u.case)
            if u.delete:
                if not u.case:
                    raise SliceError(E_PATCH, "an events delete line names the case: -{\"_section\":\"events\",\"case\":\"<id>\"}", nothing_written=True)
                continue  # the block was removed by take_block; a delete line adds nothing
            if not u.case:
                raise SliceError(E_PATCH, "an events row needs case=<id> or a '@set case=<id>' line above it", nothing_written=True)
            added.append(u)
        elif u.kind == "row":
            if u.key is None:
                raise SliceError(E_PATCH, f"cannot tell which {u.section} row this is: {u.text[:40]}", nothing_written=True)
            hits = drop_base(lambda b, k=u.key: b.key == k)
            dropped = drop_added(lambda a, k=u.key: a.key == k)
            if hits:  # the last base row of that identity: the first definition stays (see the module doc)
                removed.add(hits[-1])
                touched.add(base[hits[-1]].inst)
                if not u.delete and not u.sets and base[hits[-1]].sets:
                    u.sets = base[hits[-1]].sets  # the replacing row keeps the @set defaults the old row relied on
            if u.delete:
                if hits:
                    report.deleted += 1
                elif not dropped:
                    report.missed.append(_show(u.key))
                continue
            report.replaced += 1 if hits else 0
            added.append(u)
    report.added = max(0, len(added) - report.replaced)
    # a section instance that lost its last row disappears with its @set lines (so that the same patch applied twice gives the same text)
    for inst in touched:
        members = [i for i, u in enumerate(base) if u.inst == inst and u.section and i not in removed]
        if inst and not any(base[i].kind in ("row", "final", "pending", "other") for i in members):
            removed.update(i for i in members if base[i].kind in ("section", "set"))
    last_row: dict = {}  # an events block that was replaced or deleted leaves no orphan '@set case=<id>' line behind
    for j, v in enumerate(base):
        if v.kind == "row" and j not in removed:
            last_row[(v.inst, v.case)] = j
    for i, u in enumerate(base):
        if i not in removed and u.kind == "set" and u.case and last_row.get((u.inst, u.case), -1) < i:
            removed.add(i)
    kept = [u.text for i, u in enumerate(base) if i not in removed]
    lines = list(kept)
    while lines and not lines[-1].strip():
        lines.pop()
    lines.extend(_emit(added, lines, fmt))
    have_final = {a for u in base for a in u.arg if u.kind == "final"}
    have_pending = {a for u in base for a in u.arg if u.kind == "pending"}
    new_final = [a for u in ops if u.kind == "final" for a in u.arg if a not in have_final]
    new_pending = [a for u in ops if u.kind == "pending" for a in u.arg if a not in have_pending]
    for name in dict.fromkeys(new_final):
        lines.append(f"@final {name}" if fmt == "atl" else json.dumps({"_final": name}, separators=(",", ":")))
    if new_pending:
        ids = list(dict.fromkeys(new_pending))
        lines.append("@pending " + ",".join(ids) if fmt == "atl" else json.dumps({"_pending": ids}, ensure_ascii=False, separators=(",", ":")))
    report.text = normalise("\n".join(lines))
    return report


def _emit(added: Sequence, lines: list, fmt: str) -> list:
    """The lines to append: each run of rows of one patch section instance under a section header (``.atl``)."""
    if fmt == "jsonl":
        return [u.text for u in added]
    tail_section, tail_sets = "", False
    for u in _scan_atl("\n".join(lines), patch=False)[0]:
        if u.kind == "section":
            tail_section, tail_sets = u.section, False
        elif u.kind == "set":
            tail_sets = True
    out: list = []
    previous_inst, emitted = None, ()
    for u in added:
        if u.inst != previous_inst:
            previous_inst, emitted = u.inst, ()
            if u.sets or u.section != tail_section or tail_sets:
                out.append(SECTIONS[u.section].header)
                tail_section, tail_sets = u.section, False
        if u.sets[: len(emitted)] == emitted:
            fresh = u.sets[len(emitted):]
        else:
            fresh, emitted = u.sets, ()
        out.extend(fresh)
        if fresh:
            tail_sets = True
        emitted = u.sets
        out.append(u.text)
    return out


# ---------------------------------------------------------------------------------------------------------------
# put
# ---------------------------------------------------------------------------------------------------------------

#: Callables ``hook(paths, outcome)`` run inside the lock after every commit: the oracle lane registers its sealing here.
COMMIT_HOOKS: list = []


def register_commit_hook(hook: Callable) -> None:
    if hook not in COMMIT_HOOKS:
        COMMIT_HOOKS.append(hook)


@dataclass
class PutOutcome:
    status: str                       # committed | invalid
    slice: str
    fmt: str
    sha8: str                         # of the text that was submitted / produced
    check: "slicecheck.CheckResult"
    unchanged: bool = False
    red: bool = False                 # a red draft exists for the slice after this call
    previous: str = ""                # sha8 of the committed version before this call ("" when there was none)
    patch: "PatchReport | None" = None
    counts: dict = field(default_factory=dict)
    text: str = ""
    cleared_red: bool = False
    hook_errors: list = field(default_factory=list)

    @property
    def committed(self) -> bool:
        return self.status == "committed"


def _count_put(paths: Paths, name: str, red: bool) -> None:
    """Keep ``.atlas/puts.json`` (inside the lock): the number of puts and the consecutive red puts per slice. A restarted
    session reads them through ``status`` (loop guards: 3 repair rounds per slice, the put budget)."""
    target = paths.dot / "puts.json"
    try:
        try:
            data = json.loads(read_limited(target, COUNTER_MAX_BYTES).decode("utf-8")) if target.is_file() else {}
        except (OSError, ValueError, RecursionError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        puts = data.get("puts") if isinstance(data.get("puts"), int) else 0
        reds = {k: v for k, v in (data.get("red") or {}).items() if isinstance(v, int)} if isinstance(data.get("red"), dict) else {}
        if red:
            reds[name] = reds.get(name, 0) + 1
        else:
            reds.pop(name, None)
        atomic_write(target, (json.dumps({"puts": puts + 1, "red": reds}, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
    except (OSError, SliceError):
        pass  # the counters are advice for the loop guards: a failed write never fails a put


_EMPTY_PATCH_LINE = re.compile(r"^\s*(#atlas\b.*|//.*)?$")


def _is_revalidation(text: str) -> bool:
    """A patch with nothing but blank lines, comments and the header line: it asks to check the red draft again."""
    return all(_EMPTY_PATCH_LINE.match(line) for line in normalise(text).split("\n"))


def count_rows(fname: str, text: str) -> dict:
    """Rows per section of a slice text (rows the front-end kept)."""
    out: dict[str, int] = {}
    for row in ex.parse_source(fname, text).rows:
        out[row.section] = out.get(row.section, 0) + 1
    return out


def put_slice(paths: Paths, meta: "Mapping | None", name: str, text: str, *, mode: str = "replace", fmt: str = "atl", base: str = "",
              check_options: "Mapping | None" = None, lock_wait: float = LOCK_WAIT_S, lock_stale: float = LOCK_STALE_S) -> PutOutcome:
    """Validate and store one slice (see the module doc). Raises ``SliceError`` for a refused request; nothing is written then."""
    check_name(name)
    check_format(fmt)
    if mode not in MODES:
        raise SliceError(E_ARG, f"mode must be one of {', '.join(MODES)}")
    if not isinstance(text, str) or not text.strip():
        raise SliceError(E_ARG, "the text is empty: nothing to put")
    if len(text.encode("utf-8", errors="replace")) > HARD_TEXT_BYTES:
        raise SliceError(E_LIMIT, f"the text is larger than {HARD_TEXT_BYTES // 1024} KiB; a slice is at most {MAX_SLICE_BYTES // 1024} KiB "
                         "(about 11 nodes with their edges and evidences)")
    if not isinstance(base, str) or (base and not re.fullmatch(r"[0-9a-f]{8}", base)):
        raise SliceError(E_ARG, "base is the 8 hex digits sha8 of the slice you read, or empty")
    options = dict(check_options or {})
    with project_lock(paths, lock_wait, lock_stale):
        sweep_temps(paths)
        committed, red = find_slice(paths, name), find_red(paths, name)
        committed_text = normalise(committed.text()) if committed else ""
        red_text = normalise(red.text()) if red else ""
        committed_sha = sha8(committed_text) if committed else ""
        if base:
            known = {committed_sha} if committed else set()
            if red:
                known.add(sha8(red_text))
            if base not in known:
                raise SliceError(E_CONFLICT, "base does not match the slice on disk; read it again (nothing was written)", conflict=True,
                                 current=committed_sha, **({"draft": sha8(red_text)} if red else {}))
        patch = None
        if mode == "patch":
            target = red if red else committed
            if target is None:
                raise SliceError(E_NOTHING, f"slice {name} has neither a committed version nor a red draft: put it first (mode=replace)")
            if target.fmt != fmt:
                raise SliceError(E_ARG, f"the slice is .{target.fmt}: send the patch with format={target.fmt}")
            if red and fmt == "atl" and _is_revalidation(text):
                new_text = red_text                      # an empty patch re-checks the red draft (the missing slice may exist now)
            else:
                patch = apply_patch(red_text if red else committed_text, text, fmt)
                new_text = patch.text
        else:
            new_text = normalise(text)
        if len(new_text.encode("utf-8", errors="replace")) > HARD_TEXT_BYTES:
            raise SliceError(E_LIMIT, f"the result is larger than {HARD_TEXT_BYTES // 1024} KiB: split the slice")
        data = _encode(new_text)
        if project_bytes(paths, exclude=name) + len(data) > MAX_PROJECT_BYTES:
            raise SliceError(E_LIMIT, f"the project would exceed {MAX_PROJECT_BYTES // (1024 * 1024)} MiB")
        fname = f"{name}.{fmt}"
        rest = [(s.fname, s.data()) for s in list_slices(paths) if s.name != name]
        result = slicecheck.check(rest + [(fname, new_text)], meta, mode="slice", incoming=[fname], **options)
        new_sha = sha8(new_text)
        counts = count_rows(fname, new_text)
        out = PutOutcome("invalid", name, fmt, new_sha, result, previous=committed_sha, patch=patch, counts=counts, text=new_text)
        _count_put(paths, name, not result.ok)
        if not result.ok:
            for stale in list_red(paths):
                if stale.name == name and stale.fmt != fmt:
                    _unlink(stale.path)
            if not (red and red.fmt == fmt and red_text == new_text):
                atomic_write(paths.red / fname, data)
            out.red = True
            return out
        out.status = "committed"
        out.unchanged = bool(committed and committed.fmt == fmt and committed_text == new_text)
        if not out.unchanged:
            if committed is not None:
                _archive(paths, committed, read_bytes(committed.path))
            atomic_write(paths.slices / fname, data)
            if committed is not None and committed.fmt != fmt:
                _unlink(committed.path)
        for stale in list_red(paths):
            if stale.name == name:
                _unlink(stale.path)
                out.cleared_red = True
        for hook in list(COMMIT_HOOKS):
            try:
                hook(paths, out)
            except Exception as ex:  # noqa: BLE001 - a failing hook must not undo a commit
                out.hook_errors.append(f"{type(ex).__name__}: {str(ex)[:80]}")
        return out
