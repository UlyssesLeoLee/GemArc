"""The multimedia document workflow, shipped inside the package and served by the server.

The workflow used to live only in ``skills/gemarc-media-doc/`` at the repository
root, so a client had to install a separate skill before it could make a
multimedia architecture document. The server now carries the same text inside
the wheel (``scaffold/skills/gemarc-media-doc/``) and hands it out three ways:

* the ``gemarc_media_doc_guide`` tool - tools are the one channel every client shows
  to the model, so this is what makes the capability appear on connect;
* the MCP resource ``gemarc://skill/gemarc-media-doc``;
* the server instructions and the ``gemarc-media-doc`` prompt, which point at it.

``skills/gemarc-media-doc/`` stays the single source of truth. The copy inside
the package is generated from it (``python -m gemarc_mcp.scaffold.skillpack``)
and ``tests/test_skill_pack.py`` fails when the two drift apart.

Line endings are normalised (BOM stripped, CRLF folded to LF) on the way in, so
a Windows checkout and a Linux wheel carry byte-identical text.
"""
from __future__ import annotations

import hashlib
import re
import shutil
from importlib import resources
from pathlib import Path

from ..paths import GemArcError

SCAFFOLD_ROOT = Path(__file__).resolve().parent
#: Where the generated copy lives inside the package.
PACKED_ROOT = SCAFFOLD_ROOT / "skills"
#: The one skill the server publishes.
SKILL_NAME = "gemarc-media-doc"

#: The repository root of an editable checkout (``src/gemarc_mcp/scaffold`` -> repo).
REPO_ROOT = SCAFFOLD_ROOT.parents[2]
#: The editable source of truth. Absent in an installed wheel.
SOURCE_DIR = REPO_ROOT / "skills" / SKILL_NAME

#: The sections the guide can return. ``id`` is what a caller passes as ``topic``.
SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("workflow", "完整工作流：模式判断、硬规则、流程 A/B、结果与限额", "SKILL.md"),
    ("assemble", "组装站点：取固定件、核对 sha256、写入 data/*.json", "references/assemble-site.md"),
)

#: The default section, used when the caller does not name one.
DEFAULT_SECTION = "workflow"

#: MCP resource URI -> file inside the packed skill.
RESOURCE_FILES: tuple[tuple[str, str], ...] = (
    ("gemarc://skill/gemarc-media-doc", "SKILL.md"),
    ("gemarc://skill/gemarc-media-doc/references/assemble-site.md", "references/assemble-site.md"),
)

_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)


def normalised_bytes(raw: bytes) -> bytes:
    """BOM stripped, CRLF and CR folded to LF: the same text on every platform."""
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def read_bytes(relative: str) -> bytes:
    """Read a file of the packed skill, normalised to UTF-8 without BOM."""
    try:
        raw = resources.files("gemarc_mcp").joinpath(
            "scaffold", "skills", SKILL_NAME, relative).read_bytes()
    except (FileNotFoundError, OSError, ModuleNotFoundError) as ex:
        raise GemArcError("GEMARC_GUIDE_002", f"流程正文缺失: {relative}") from ex
    return normalised_bytes(raw)


def read_text(relative: str) -> str:
    """The text of a file of the packed skill."""
    return read_bytes(relative).decode("utf-8")


def split_frontmatter(text: str) -> tuple[str, str]:
    """``(front matter, body)`` of a skill file. The front matter is empty when absent."""
    match = _FRONTMATTER.match(text)
    if not match:
        return "", text
    return match.group(1), text[match.end():]


def digest(raw: bytes) -> str:
    """sha256 over the normalised bytes."""
    return hashlib.sha256(raw).hexdigest()


def section_ids() -> list[str]:
    """The topic ids the guide understands."""
    return [item[0] for item in SECTIONS]


def resolve(topic: str = "") -> tuple[str, str, str]:
    """``(id, title, relative file)`` for a topic. An empty topic is the default one."""
    key = (topic or "").strip().lower()
    if not key:
        key = DEFAULT_SECTION
    for section_id, title, relative in SECTIONS:
        if section_id == key:
            return section_id, title, relative
    raise GemArcError("GEMARC_GUIDE_001",
                      f"未知的工作流分段: {topic}；可用分段: {', '.join(section_ids())}")


def manifest() -> list[dict]:
    """Every section with its size and digest, so a client can check what it got."""
    entries = []
    for section_id, title, relative in SECTIONS:
        raw = read_bytes(relative)
        entries.append({"id": section_id, "title": title, "file": relative,
                        "bytes": len(raw), "sha256": digest(raw)})
    return entries


def workflow_body() -> str:
    """The workflow without the YAML front matter (that part only matters to installers)."""
    return split_frontmatter(read_text("SKILL.md"))[1]


def source_dir() -> Path | None:
    """The editable source of the skill, or None outside a checkout."""
    if SOURCE_DIR.is_dir() and (SOURCE_DIR / "SKILL.md").is_file():
        return SOURCE_DIR
    return None


def source_files() -> list[Path]:
    """Every file of the editable source, relative to the skill folder."""
    source = source_dir()
    if source is None:
        return []
    return sorted(p for p in source.rglob("*") if p.is_file() and "__pycache__" not in p.parts)


def packed_files() -> list[Path]:
    """Every file of the generated copy, relative to the skill folder."""
    root = PACKED_ROOT / SKILL_NAME
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts)


def is_in_sync() -> bool:
    """True when the generated copy matches the editable source.

    Outside a checkout there is no source to compare against, and the packaged
    copy is the only truth there - so the answer is True.
    """
    source = source_dir()
    if source is None:
        return True
    expected = {p.relative_to(source).as_posix(): normalised_bytes(p.read_bytes())
                for p in source_files()}
    actual = {p.relative_to(PACKED_ROOT / SKILL_NAME).as_posix(): normalised_bytes(p.read_bytes())
              for p in packed_files()}
    return expected == actual


def sync() -> list[Path]:
    """Copy the editable source into the package. Returns the written files."""
    source = source_dir()
    if source is None:
        raise GemArcError("GEMARC_GUIDE_003", "找不到 skills/gemarc-media-doc 源目录")
    target = PACKED_ROOT / SKILL_NAME
    if target.exists():
        shutil.rmtree(target)
    written = []
    for path in source_files():
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(normalised_bytes(path.read_bytes()))
        written.append(destination)
    return written


if __name__ == "__main__":
    for written in sync():
        print(f"wrote {written}")
