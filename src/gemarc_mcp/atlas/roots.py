"""Evidence roots: where cited source files live on the machine that runs the server.

Roots come from ONE place, the server environment variable ``GEMARC_ATLAS_ROOTS``::

    GEMARC_ATLAS_ROOTS="main=D:/src/app;web=D:/src/web"

``;`` separates entries, the first ``=`` of an entry separates the alias from the path (so Windows drive letters are
safe). Evidence rows say ``repo=<alias>``; a root is never a tool argument: a path where an alias is expected is an
error (``require_alias`` / ``forbid_root_arguments``), so an AI cannot point the verifier at an arbitrary directory.

``resolve_in_root`` turns an alias-relative file path into a real path and enforces the sandbox: absolute paths, drive
letters, ``..``, device names and symlink / junction escapes are errors; a path whose letter case differs from the name
on disk is returned with ``case_mismatch`` set (a warning for the caller: it works on Windows and macOS, fails on Linux).

Known limits (stated, not hidden): a HARD LINK inside a root to a file outside it cannot be detected (a hard link is an
ordinary directory entry of the same file); only symlinks and junctions are. The roots table is the only authority:
the tool binding must accept ONLY its declared arguments (``require_declared_arguments``) and never forward an argument
into the roots mapping or into the directory that holds ``.atlas/evidence-stamp.json``.

Standard library only. Never imports ``mcp``.
"""
from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

ENV_NAME = "GEMARC_ATLAS_ROOTS"

E_SPEC = "GEMARC_ATLAS_ROOT_001"
E_ALIAS = "GEMARC_ATLAS_ROOT_002"
E_DUP = "GEMARC_ATLAS_ROOT_003"
E_RELATIVE = "GEMARC_ATLAS_ROOT_004"
E_ARGUMENT = "GEMARC_ATLAS_ROOT_005"
E_PATH_TYPE = "GEMARC_ATLAS_ROOT_010"
E_ABSOLUTE = "GEMARC_ATLAS_ROOT_011"
E_DRIVE = "GEMARC_ATLAS_ROOT_012"
E_DOTDOT = "GEMARC_ATLAS_ROOT_013"
E_NAME = "GEMARC_ATLAS_ROOT_014"
E_ESCAPE = "GEMARC_ATLAS_ROOT_015"

#: same shape as ``model.TOKEN_RE`` (repository aliases are tokens)
ALIAS_RE = re.compile(r"^[A-Za-z0-9][\w.-]{0,39}$", re.UNICODE)
#: argument names that would smuggle a root into a tool call
ROOT_ARGUMENT_NAMES = frozenset(
    {"root", "roots", "rootpath", "rootdir", "rootdirectory", "repopath", "repodir", "repodirectory", "reporoot",
     "basedir", "basedirectory", "basepath", "baseroot", "workspace", "workdir", "workingdir", "workingdirectory", "cwd",
     "directory", "dir", "folder", "projectdir", "projectdirectory", "projectroot", "projectpath", "srcdir", "sourcedir",
     "sourceroot", "checkout", "checkoutdir", "mount"})
_RESERVED_DEVICE_RE = re.compile(r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$", re.IGNORECASE)


class RootsError(ValueError):
    """A root specification, alias argument or file path breaks the sandbox rules."""

    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(f"{code}: {message}")


# --------------------------------------------------------------------------------------------------------
# The roots table
# --------------------------------------------------------------------------------------------------------


def _is_absolute_root(value: str) -> bool:
    if re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith(("\\\\", "//")):
        return True
    if os.name == "nt":
        return False        # '/tmp/x' alone is relative to the current drive on Windows: not a root
    return value.startswith("/") and not re.match(r"^/[A-Za-z]:", value)


def parse_roots(spec: str | None) -> dict[str, Path]:
    """``"alias=path;alias=path"`` -> ``{alias: Path}`` in written order.

    ``None`` and blank give ``{}``. Errors (all reported together): an entry without ``=``, an invalid alias, an empty
    or relative path, a duplicate alias. A path that does not exist is NOT an error here: the verifier reports such a
    repository as SKIP.
    """
    if spec is None or not str(spec).strip():
        return {}
    problems: list[tuple[str, str]] = []
    table: dict[str, Path] = {}
    for number, raw in enumerate(str(spec).split(";"), start=1):
        entry = raw.strip()
        if not entry:
            continue  # a trailing ';' is harmless
        alias, sep, path = entry.partition("=")
        alias, path = alias.strip(), path.strip().strip('"').strip("'")
        if not sep:
            problems.append((E_SPEC, f"entry {number} has no '=': expected alias=path"))
            continue
        if not ALIAS_RE.fullmatch(alias):
            problems.append((E_ALIAS, f"entry {number}: alias {alias!r} must be a token (letters, digits, '_', '.', '-')"))
            continue
        if not path:
            problems.append((E_SPEC, f"entry {number}: alias {alias!r} has an empty path"))
            continue
        if alias in table:
            problems.append((E_DUP, f"entry {number}: alias {alias!r} is declared twice"))
            continue
        if not _is_absolute_root(path):
            problems.append((E_RELATIVE, f"entry {number}: root of {alias!r} must be an absolute path, got {path!r}"))
            continue
        table[alias] = Path(os.path.normpath(path))
    if problems:
        code = problems[0][0]
        raise RootsError(code, f"{ENV_NAME} is invalid: " + "; ".join(f"[{c}] {m}" for c, m in problems))
    return table


def roots_from_env(environ: Mapping[str, str] | None = None) -> dict[str, Path]:
    """Roots from the server environment (``os.environ`` unless a mapping is injected)."""
    env = os.environ if environ is None else environ
    return parse_roots(env.get(ENV_NAME))


def root_available(path: Path) -> bool:
    """True when the root exists and is a directory (a missing root makes its rows SKIP)."""
    try:
        return path.is_dir()
    except OSError:
        return False


# --------------------------------------------------------------------------------------------------------
# Aliases in arguments
# --------------------------------------------------------------------------------------------------------


def looks_like_path(value) -> bool:
    return isinstance(value, str) and (
        "/" in value or "\\" in value or ":" in value or value.startswith(("~", ".")) or ".." in value)


def require_alias(value, what: str = "repo") -> str:
    """The value of an alias-typed argument. A path (or anything that is not a token) is an error."""
    if not isinstance(value, str) or not value:
        raise RootsError(E_ALIAS, f"{what} must be a repository alias (a token declared in {ENV_NAME}), got {value!r}")
    if looks_like_path(value):
        raise RootsError(E_ARGUMENT, f"{what} looks like a path ({value!r}); roots come only from the server environment "
                                     f"variable {ENV_NAME}, pass an alias instead")
    if not ALIAS_RE.fullmatch(value):
        raise RootsError(E_ALIAS, f"{what} {value!r} is not a valid repository alias")
    return value


_KEY_SEPARATORS_RE = re.compile(r"[-_ ]")


def forbid_root_arguments(arguments: Mapping) -> None:
    """Refuse tool arguments that carry a root (by name; a second line of defence). Values of alias-typed arguments go
    through ``require_alias``. A name list is never complete: the binding must also call ``require_declared_arguments``."""
    for key in arguments:
        if isinstance(key, str) and _KEY_SEPARATORS_RE.sub("", key).lower() in ROOT_ARGUMENT_NAMES:
            raise RootsError(E_ARGUMENT, f"argument {key!r} is not accepted: roots come only from the server environment "
                                         f"variable {ENV_NAME}, never from a tool argument")


def require_declared_arguments(arguments: Mapping, allowed) -> None:
    """Allowlist form: every key of ``arguments`` must be one of ``allowed`` (the tool's declared parameters), exactly
    spelled. An unknown key (``project_dir``, ``repo_root``, ...) is refused with ``GEMARC_ATLAS_ROOT_005``; the root-like
    names get the roots message."""
    forbid_root_arguments(arguments)
    declared = set(allowed)
    unknown = sorted(str(key) for key in arguments if key not in declared)
    if unknown:
        raise RootsError(E_ARGUMENT, f"argument(s) {', '.join(map(repr, unknown))} are not parameters of this tool "
                                     f"(declared: {', '.join(sorted(declared))}); roots and directories come only from the server")


# --------------------------------------------------------------------------------------------------------
# Paths inside a root
# --------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Resolved:
    """Result of ``resolve_in_root``. ``path`` is None when the file (or a directory on the way) does not exist."""

    rel: str                      # posix path relative to the root; the on-disk spelling when the file exists
    path: Path | None
    case_mismatch: str | None     # the on-disk spelling when it differs from the requested one in letter case only

    @property
    def exists(self) -> bool:
        return self.path is not None


def clean_relative(rel) -> list[str]:
    """Syntax check of a root-relative path; returns its segments. Raises ``RootsError``."""
    if not isinstance(rel, str) or not rel.strip():
        raise RootsError(E_PATH_TYPE, f"file path must be a non-empty string, got {rel!r}")
    if "\0" in rel or any(ord(c) < 32 for c in rel):
        raise RootsError(E_PATH_TYPE, "file path contains a control character")
    text = rel.replace("\\", "/")
    if text.startswith("/"):
        raise RootsError(E_ABSOLUTE, f"file path {rel!r} is absolute; cite it relative to the repository root")
    if re.match(r"^[A-Za-z]:", text):
        raise RootsError(E_DRIVE, f"file path {rel!r} starts with a drive letter; cite it relative to the repository root")
    if ":" in text:
        raise RootsError(E_DRIVE, f"file path {rel!r} contains ':' (drive or stream syntax is not allowed)")
    segments: list[str] = []
    for segment in text.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            raise RootsError(E_DOTDOT, f"file path {rel!r} contains '..'; cite it relative to the repository root")
        if segment.endswith((" ", ".")) or segment.startswith(" "):
            raise RootsError(E_NAME, f"file path {rel!r}: segment {segment!r} starts with a space or ends with a space or dot")
        if _RESERVED_DEVICE_RE.match(segment):
            raise RootsError(E_NAME, f"file path {rel!r}: segment {segment!r} is a reserved device name")
        if any(c in segment for c in '<>"|?*'):
            raise RootsError(E_NAME, f"file path {rel!r}: segment {segment!r} has a character that no repository path uses")
        segments.append(segment)
    if not segments:
        raise RootsError(E_PATH_TYPE, f"file path {rel!r} names no file")
    return segments


def _within(child: str, parent: str) -> bool:
    child, parent = os.path.normcase(child), os.path.normcase(parent)
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:  # different drives
        return False


def _actual_names(directory: str, cache: dict[str, list[str] | None]) -> list[str] | None:
    if directory not in cache:
        try:
            with os.scandir(directory) as entries:
                cache[directory] = [entry.name for entry in entries]
        except OSError:
            cache[directory] = None
    return cache[directory]


def resolve_in_root(root: Path | str, rel, *, dir_cache: dict | None = None) -> Resolved:
    """Resolve ``rel`` under ``root``. Raises ``RootsError`` for anything that leaves or abuses the sandbox.

    Letter-case differences are detected by listing each directory on the way (not by ``realpath``, whose casing rules
    differ by platform): the on-disk spelling is used and ``case_mismatch`` carries it. A final component that is a
    symlink or junction is followed and must still end up inside the root.
    """
    segments = clean_relative(rel)
    root_text = os.path.normpath(str(root))
    cache = {} if dir_cache is None else dir_cache
    try:
        real_root = os.path.realpath(root_text)
    except OSError as ex:
        raise RootsError(E_ESCAPE, f"root {root_text!r} cannot be resolved: {ex.strerror or ex}") from None
    current = root_text
    actual: list[str] = []
    mismatch = False
    for index, segment in enumerate(segments):
        names = _actual_names(current, cache)
        if names is None:
            return Resolved("/".join(segments), None, None)
        if segment in names:
            found = segment
        else:
            folded = [name for name in names if name.casefold() == segment.casefold()]
            if len(folded) != 1:
                return Resolved("/".join(segments), None, None)
            found, mismatch = folded[0], True
        actual.append(found)
        current = os.path.join(current, found)
    try:
        real = os.path.realpath(current)
    except OSError as ex:
        raise RootsError(E_ESCAPE, f"{rel!r} cannot be resolved: {ex.strerror or ex}") from None
    if not _within(real, real_root):
        raise RootsError(E_ESCAPE, f"{rel!r} resolves outside the repository root (symlink or junction); cite a file inside it")
    return Resolved("/".join(actual), Path(current), "/".join(actual) if mismatch else None)


def is_regular_file(path: Path) -> bool:
    """Regular file after following links (devices, sockets and directories are not)."""
    try:
        return stat.S_ISREG(os.stat(path).st_mode)
    except OSError:
        return False
