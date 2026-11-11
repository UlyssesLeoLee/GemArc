"""Project layout of an atlas under a workspace root, and the tiny TOML file that describes it.

::

    <root>/<project>/atlas/
        atlas.toml                      [project] id title mode languages profile, [roots] aliases (never paths)
        .gitignore                      .atlas/ dist/ out/
        slices/<name>.atl|.jsonl        the committed slices (state = the directory)
        user/<name>.txt                 saved requirement text and the user's own answers (quotes are checked against them)
        .atlas/red/<name>.atl|.jsonl    red drafts (a rejected slice is kept here, never discarded)
        .atlas/history/<name>.<sha8>.*  previous versions of a slice, newest ten (see slices.py)
        .atlas/{oracle.json,evidence-stamp.json,review.json,lock}
        out/model.json                  ``check --emit-model``

The workspace root comes from ``GEMARC_WORKSPACE_ROOT`` (default ``~/.gemarc/workspace``) exactly as ``server.py`` resolves
it; this module mirrors that logic because ``server.py`` imports ``mcp``. Writes need ``GEMARC_ALLOW_WRITE=1``
(``Workspace.writable``). The reader of ``atlas.toml`` uses ``tomllib`` (Python 3.11+); the writer is plain text.
Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from ..paths import GemArcError, Workspace
from . import model as am
from . import vocab

MODES = ("asis", "tobe")
CONFIG_NAME = "atlas.toml"
#: Windows device names: ``con.atl`` is not a file there.
_RESERVED = frozenset({"con", "prn", "aux", "nul", "clock$"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)})
_ALIAS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
GITIGNORE = ".atlas/\ndist/\nout/\n"
MAX_REPOS = 32

E_NOT_FOUND = "GEMARC_ATLAS_PROJ_001"
E_EXISTS = "GEMARC_ATLAS_PROJ_002"
E_ARG = "GEMARC_ATLAS_PROJ_003"
E_CONFIG = "GEMARC_ATLAS_PROJ_004"


def default_workspace_root() -> Path:
    """Where a local server keeps projects when the user configured nothing (same as ``server.default_workspace_root``)."""
    return Path.home() / ".gemarc" / "workspace"


def get_workspace(root: "str | Path | None" = None, *, create: bool = True) -> Workspace:
    """The workspace: ``root`` if given, else ``GEMARC_WORKSPACE_ROOT``, else the default directory.

    ``create=True`` (a call that writes) creates the default directory; ``create=False`` (a read: status, index, check, review
    report) never creates anything and says ``GEMARC_ATLAS_PROJ_001`` when there is no workspace yet."""
    chosen = str(root).strip() if root else os.environ.get("GEMARC_WORKSPACE_ROOT", "").strip()
    if not chosen:
        fallback = default_workspace_root()
        if not create:
            if not fallback.is_dir():
                raise GemArcError(E_NOT_FOUND, "no workspace exists yet, so there is no project to read; init a project first")
        else:
            try:
                fallback.mkdir(parents=True, exist_ok=True)
            except OSError as ex:
                raise GemArcError("GEMARC_AUTH_000", "未配置 GEMARC_WORKSPACE_ROOT，且默认工作区无法创建") from ex
        chosen = str(fallback)
    return Workspace(chosen)


@dataclass(frozen=True)
class Paths:
    """Every path of one project (nothing is created by building this object)."""

    name: str
    project: Path       # <root>/<project>
    atlas: Path         # <root>/<project>/atlas
    config: Path
    slices: Path
    dot: Path           # .atlas
    red: Path
    history: Path
    lock: Path
    oracle: Path
    stamp: Path
    review: Path
    out: Path
    user: Path          # atlas/user: saved requirement texts (to-be)


def project_paths(ws: Workspace, project: str) -> Paths:
    """Paths of ``project`` inside the workspace; the name must be a safe project name (``paths.NAME_RE``)."""
    if not isinstance(project, str) or project.lower().split(".")[0] in _RESERVED or project.endswith((".", " ")):
        raise GemArcError("GEMARC_INPUT_001", "项目名必须是安全的 ASCII 名称")  # a trailing dot names the same directory on Windows
    base = ws.project(project)
    atlas = base / "atlas"
    dot = atlas / ".atlas"
    return Paths(project, base, atlas, atlas / CONFIG_NAME, atlas / "slices", dot, dot / "red", dot / "history", dot / "lock",
                 dot / "oracle.json", dot / "evidence-stamp.json", dot / "review.json", atlas / "out", atlas / "user")


# ---------------------------------------------------------------------------------------------------------------
# atlas.toml
# ---------------------------------------------------------------------------------------------------------------


def _q(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)  # a JSON string is a valid TOML basic string


def render_config(project: str, title: str, mode: str, languages: Sequence[str], profile: str, repos: Sequence[str]) -> str:
    """The text of ``atlas.toml`` (LF, no timestamp, deterministic)."""
    return "\n".join([
        "# GemArc Atlas project. Roots are named here, never located: the paths come from the server environment.",
        "[project]",
        f"id = {_q(project)}",
        f"title = {_q(title)}",
        f"mode = {_q(mode)}",
        "languages = [" + ", ".join(_q(x) for x in languages) + "]",
        f"profile = {_q(profile)}",
        "",
        "[roots]",
        "aliases = [" + ", ".join(_q(x) for x in repos) + "]",
        "",
    ])


def read_toml(text: str) -> dict:
    """Parse TOML text; raises ``GemArcError`` (code ``E_CONFIG``) when it is not valid."""
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as ex:
        raise GemArcError(E_CONFIG, f"atlas.toml is not valid TOML: {str(ex)[:80]}") from ex


@dataclass(frozen=True)
class Config:
    project: str
    title: str
    mode: str
    languages: tuple
    profile: str
    repos: tuple

    def meta(self) -> dict:
        """What ``expand`` / ``slicecheck`` take as ``meta``. ``repos`` is present only when the project names root aliases:
        without it EV-007 is skipped (an undeclared alias is only an error where the project declared its aliases)."""
        out = {"projectId": self.project, "title": self.title, "languages": list(self.languages), "profile": self.profile,
               "mode": self.mode}
        if self.repos:
            out["repos"] = list(self.repos)
        return out


def load_config(paths: Paths) -> Config:
    """Read ``atlas.toml``; ``E_NOT_FOUND`` when the project was never initialised."""
    if not paths.config.is_file():
        raise GemArcError(E_NOT_FOUND, f"项目 {paths.name} 尚未初始化（缺少 atlas/atlas.toml）；先调用 init")
    if paths.config.stat().st_size > 64 * 1024:
        raise GemArcError(E_CONFIG, "atlas.toml is larger than 64 KiB")
    try:
        data = read_toml(paths.config.read_bytes().decode("utf-8-sig"))
    except UnicodeDecodeError as ex:
        raise GemArcError(E_CONFIG, "atlas.toml is not UTF-8") from ex
    proj, roots = data.get("project") or {}, data.get("roots") or {}
    if not isinstance(proj, Mapping) or not isinstance(roots, Mapping):
        raise GemArcError(E_CONFIG, "atlas.toml needs [project] and [roots] tables")
    mode = proj.get("mode", "asis")
    languages = proj.get("languages", ["zh-CN"])
    if mode not in MODES:
        raise GemArcError(E_CONFIG, f"atlas.toml mode must be one of {', '.join(MODES)}")
    if not isinstance(languages, list) or not languages or any(x not in vocab.LOCALES for x in languages):
        raise GemArcError(E_CONFIG, f"atlas.toml languages must be a non-empty list of {', '.join(vocab.LOCALES)}")
    repos = roots.get("aliases", [])
    if not isinstance(repos, list) or any(not isinstance(x, str) or not _ALIAS_RE.match(x) for x in repos):
        raise GemArcError(E_CONFIG, "atlas.toml [roots] aliases must be a list of short names")
    return Config(str(proj.get("id") or paths.name), str(proj.get("title") or ""), mode, tuple(languages),
                  str(proj.get("profile") or vocab.DEFAULT_PROFILE), tuple(repos))


def validate_init_args(project: str, title: str, mode: str, languages: Sequence[str], profile: str, repos: Sequence[str]) -> None:
    """Refuse bad ``init`` arguments with one specific message each (``E_ARG``)."""
    if mode not in MODES:
        raise GemArcError(E_ARG, f"mode must be one of {', '.join(MODES)}, got {mode!r}")
    langs = list(languages)
    if not langs or any(x not in vocab.LOCALES for x in langs) or len(set(langs)) != len(langs):
        raise GemArcError(E_ARG, f"languages must be a non-empty list without repeats from {', '.join(vocab.LOCALES)}")
    try:
        vocab.load_profile(profile)
    except vocab.VocabError as ex:
        raise GemArcError(E_ARG, f"unknown profile {profile!r}") from ex
    if not isinstance(title, str) or len(title) > 200 or "\n" in title or "\r" in title:
        raise GemArcError(E_ARG, "title must be one line of at most 200 characters")
    try:
        title.encode("utf-8")
    except UnicodeEncodeError:
        raise GemArcError(E_ARG, "title must be valid Unicode text") from None
    if len(list(repos)) > MAX_REPOS:
        raise GemArcError(E_ARG, f"at most {MAX_REPOS} repos aliases")
    if not am.PROJECT_ID_RE.fullmatch(project):
        raise GemArcError(E_ARG, "the project name must match the project id grammar")
    bad = [x for x in repos if not isinstance(x, str) or not _ALIAS_RE.match(x)]
    if bad or len(set(repos)) != len(list(repos)):
        raise GemArcError(E_ARG, "repos must be distinct short aliases such as main or web")


def create_layout(paths: Paths, config_text: str) -> None:
    """Create the directories, ``atlas.toml`` and ``.gitignore``. The caller has checked ``writable`` and that none exists."""
    data = config_text.encode("utf-8")  # first: a text that cannot be encoded must not leave a half-made layout
    if len(data) > 64 * 1024:
        raise GemArcError(E_ARG, "atlas.toml would be larger than 64 KiB")
    for directory in (paths.slices, paths.red, paths.history, paths.user):
        directory.mkdir(parents=True, exist_ok=True)
    paths.config.write_bytes(data)
    (paths.atlas / ".gitignore").write_bytes(GITIGNORE.encode("utf-8"))


#: ``FILE_ATTRIBUTE_REPARSE_POINT``: a symbolic link, a junction, or any other reparse point of Windows.
_REPARSE_POINT = 0x400
E_CONFINED = "GEMARC_AUTH_001"


def is_link(path: "Path | str") -> bool:
    """True for a symbolic link, a Windows junction or any other reparse point. ``Path.is_symlink`` misses a junction on Python 3.12, so the
    three tests are made: ``is_symlink``, ``os.path.isjunction`` (3.12, Windows) and the reparse-point file attribute. A path that does not
    exist, or cannot be examined, is not a link."""
    try:
        if Path(path).is_symlink():
            return True
        isjunction = getattr(os.path, "isjunction", None)
        if isjunction is not None and isjunction(path):
            return True
        return bool(getattr(os.lstat(path), "st_file_attributes", 0) & _REPARSE_POINT)
    except OSError:
        return False


def refuse_link(path: "Path | str", what: str = "") -> None:
    """Raise ``GemArcError(GEMARC_AUTH_001)`` when ``path`` is a link, a junction or a reparse point: nothing is written, deleted or followed."""
    if is_link(path):
        raise GemArcError(E_CONFINED, f"{what or Path(path).name} is a link or junction: refusing to write or delete through it; nothing was written")


def check_confined(ws: Workspace, paths: Paths) -> None:
    """Refuse a write when a directory of the project is a link, a junction or a reparse point, or resolves outside the workspace (a
    junction or a planted link inside the project directory is followed by ``open``; ``Workspace.project`` only vets the project directory
    itself). The directories are the ones the project writes to: ``atlas``, ``slices``, ``.atlas`` (with ``red`` and ``history``), ``user``,
    ``out`` and ``dist`` (the build deletes and rewrites most of ``dist``; ``build.py`` checks what is inside it)."""
    root = ws.root
    for directory in (paths.atlas, paths.slices, paths.dot, paths.red, paths.history, paths.user, paths.out, paths.atlas / "dist"):
        refuse_link(directory)
        try:
            resolved = directory.resolve(strict=False)
        except OSError:
            raise GemArcError(E_CONFINED, "a project directory cannot be resolved") from None
        if resolved != root and root not in resolved.parents:
            raise GemArcError(E_CONFINED, "a project directory points outside the workspace; nothing was written")
