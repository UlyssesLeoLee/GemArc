"""The atlas kit: one deterministic zip of the stdlib-only atlas core, for a client that has a shell and file tools but no MCP access.

``build_zip()`` returns the bytes of ``atlas-kit.zip``::

    atlas-kit/atlas.py                    launcher: ``python -I atlas.py <command>`` (the command line of ``gemarc_mcp.atlas.cli``)
    atlas-kit/README.md                   Chinese primary text with English and Japanese lines
    atlas-kit/gemarc_mcp/__init__.py      the two modules of the v1 package that the core imports (``paths`` only)
    atlas-kit/gemarc_mcp/paths.py
    atlas-kit/gemarc_mcp/atlas/**         the atlas package (profiles, brief, rules, engine, derive, viewer bundle, gate script)
    atlas-kit/docs/atlas/spec/*.md        the specifications

Nothing of the kit imports ``mcp`` or anything else outside the standard library (a test runs the kit with ``mcp`` made unimportable). The
archive is deterministic: sorted names, a fixed time stamp, fixed attributes, no host path, so two builds of one tree are byte-identical.

What goes in is an ALLOWLIST, not "everything under the package": a file whose suffix is one of ``KIT_SUFFIXES``, in no directory or file whose name
starts with a dot (``.env``, ``.atlas/``), in no ``tests`` directory, not a link, and not one of the few build-only parts. The route rebuilds the
archive from the live tree on every request, so a stray file next to the package (a note, a key, a project ledger) must not be able to ride along;
``tests/atlas/test_kit.py`` pins the exact list of names the real tree produces, so a new file in the package is a visible decision.

Two callers: ``tools/make_kit.py`` (maintainer, writes ``dist-kit/atlas-kit.zip``) and the ASGI route ``/kit/atlas.zip`` of ``api/index.py``,
which exists only behind ``GEMARC_ATLAS_KIT=1`` and answers a coded error when this package is absent. The module reads no environment variable.
Standard library only.
"""
from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

KIT_FILE = "atlas-kit.zip"
ROOT = "atlas-kit"
E_KIT = "GEMARC_ATLAS_KIT_001"
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
#: The only suffixes that go into the kit (every file of the package that the runtime reads has one of them).
KIT_SUFFIXES = (".py", ".md", ".json", ".js", ".css", ".html", ".tsv", ".sha256")
#: Directory and file names that never go into the kit (build artefacts; tests; this module; the viewer sources and the per-language tables, whose
#: result is the committed bundle in ``viewer/dist`` that the build reads). Any name that starts with a dot is skipped too.
SKIP_PARTS = ("__pycache__", "tests", "test")
SKIP_FILES = ("kit.py",)
SKIP_DIRS = ("viewer/src", "viewer/i18n")
SPEC_FILES = ("bundle-contract.md", "model-schema.md", "oracle-provider.md", "skeleton-spec.md")


class KitError(Exception):
    """The kit cannot be built here (a part of the repository is missing); ``code`` and ``message`` for a reply."""

    def __init__(self, message: str, code: str = E_KIT):
        self.code, self.message = code, message
        super().__init__(f"{code}: {message}")


LAUNCHER = '''\
#!/usr/bin/env python3
"""GemArc Atlas kit launcher: ``python -I atlas.py <command> ...`` (see README.md). Standard library only; no network."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))      # -I (isolated mode) does not put the script directory on the path

from gemarc_mcp.atlas.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(prog="atlas.py"))
'''

README = '''\
# GemArc Atlas 工具包（本地命令行）

English: the stdlib-only core of GemArc Atlas for a client that has a shell and file tools but no MCP. 日本語：MCP は使えないが、ターミナルとファイルの読み書きができるクライアント向けに、Python 標準機能だけで動く GemArc Atlas を用意しました。

## 这是什么

GemArc Atlas 让 AI 只写**事实**（紧凑的 `.atl` 文本切片），由固定、经过测试的代码做校验、推导、模拟和渲染，产出 5D 设计资料（查看器页面、三层文档、图、提示词、oracle 表）。本压缩包是它的纯标准库部分：没有 `mcp`，没有 Node，不联网；只有浏览器门（可选）需要本机装有 Edge 或 Chrome。

适用场景：客户端有 shell 和文件读写，但没有接入 MCP 服务器。AI 用自己的文件工具写切片，再用本工具检查、构建。

## 要求

- Python 3.11 或更新（在 3.12 上测试）。
- 解压到任意目录，不需要安装。建议用 `python -I atlas.py ...`（隔离模式，不读环境里的 `PYTHONPATH`）。

## 用法

```
python -I atlas.py brief --section all --lang zh-CN        # 工作指令：格式、词表、流程
python -I atlas.py init demo --title "我的系统" --mode asis --languages zh-CN --repos main
python -I atlas.py put demo 10-structure --file 10-structure.atl
python -I atlas.py check demo --mode full --human          # 校验，从不写文件
python -I atlas.py status demo --human                     # 下一步该做什么，续接时从这里开始
python -I atlas.py simulate demo                           # 盲测判定（先封存期望）
python -I atlas.py evidence demo                           # 把证据行对着源码核验（需要 GEMARC_ATLAS_ROOTS）
python -I atlas.py build demo --gate smoke                 # atlas/dist/index.html、文档、图、oracle 表
python -I atlas.py review demo --action report             # 自审恰好一次，然后 --action record --notes "fixed=...; open=..."
```

其余命令：`index`、`doc`、`outline`、`verify`。完整说明：`python -I atlas.py --help`，规格见 `docs/atlas/spec/`。

## 环境变量

| 变量 | 含义 |
|---|---|
| `GEMARC_WORKSPACE_ROOT` | 项目目录所在的根，默认 `~/.gemarc/workspace`（`--root` 只对一次调用生效） |
| `GEMARC_ALLOW_WRITE` | 必须是 `1`，写项目的命令（`init`、`put`、`simulate`、`evidence`、`build`、`verify`、`review --action record`）才工作；不设时只有 `check`、`status`、`index`、`brief`、`doc`、`outline` 可用 |
| `GEMARC_ATLAS_ROOTS` | 被分析源码的位置，`别名=路径;别名=路径`。模型只能写别名，从不写路径 |
| `GEMARC_ATLAS_BROWSER` | 可选：Edge 或 Chrome 可执行文件的完整路径（浏览器门用） |

## 限制

- 这个包**不是** MCP 服务器；没有 `gemarc_atlas_*` 工具，也没有入口提示。流程由 AI 自己驱动：读 `brief`，写切片，`put` 或 `check`，看 `status` 的下一步，直到 `done`。
- 回复和 `next` 行里写的 `gemarc_atlas_<名>`（例如 `gemarc_atlas_build`、`gemarc_atlas_review`）就是本包的 `python -I atlas.py <名>`（`build`、`review`）：名字一一对应，参数写法见 `python -I atlas.py <名> --help`。
- 需要用户决定的事项（TBD、改过的期望需要在 oracle 表上确认、没有浏览器、没有证据根）出现在 `status` 的 `needsUser` 里：停下来问用户，不要编造。用户的确认文件（`atlas/oracle-confirm.txt`、`atlas/waive-browser.txt`）只有用户自己写。
- 在没有任何证据根时，as-is 项目里 Implemented 的声明无法核验，`status` 不会到 `done`。
- 浏览器门没有浏览器就是 SKIPPED，不算通过。
- 来源与许可：见 `gemarc_mcp/atlas/PROVENANCE.md`。
'''


def _atlas_dir() -> Path:
    return Path(__file__).resolve().parent


def _repo_root(repo_root: "Path | str | None") -> Path:
    return Path(repo_root) if repo_root else _atlas_dir().parents[2]


def _skipped(relative: Path) -> bool:
    """True for a path of the package that the kit does not carry (see ``KIT_SUFFIXES``, ``SKIP_PARTS``, ``SKIP_FILES``, ``SKIP_DIRS``; a dot name is never carried)."""
    text = relative.as_posix()
    return (relative.suffix not in KIT_SUFFIXES or any(part in SKIP_PARTS or part.startswith(".") for part in relative.parts) or text in SKIP_FILES
            or any(text == d or text.startswith(d + "/") for d in SKIP_DIRS))


def entries(repo_root: "Path | str | None" = None) -> "list[tuple[str, bytes]]":
    """``[(name inside the zip, bytes)]`` in sorted order; ``KitError`` when a part of the repository is missing."""
    atlas = _atlas_dir()
    package = atlas.parent
    root = _repo_root(repo_root)
    spec = root / "docs" / "atlas" / "spec"
    out: dict = {f"{ROOT}/atlas.py": LAUNCHER.encode("utf-8"), f"{ROOT}/README.md": README.encode("utf-8")}
    for name in ("__init__.py", "paths.py"):
        source = package / name
        if not source.is_file():
            raise KitError(f"{name} of the v1 package is missing: the kit cannot be built from this tree")
        out[f"{ROOT}/gemarc_mcp/{name}"] = source.read_bytes()
    count = 0
    for path in sorted(atlas.rglob("*")):
        if path.is_symlink() or not path.is_file() or _skipped(path.relative_to(atlas)):
            continue
        out[f"{ROOT}/gemarc_mcp/atlas/{path.relative_to(atlas).as_posix()}"] = path.read_bytes()
        count += 1
    if not count:
        raise KitError("the atlas package has no files here")
    for name in SPEC_FILES:
        source = spec / name
        if not source.is_file():
            raise KitError(f"docs/atlas/spec/{name} is not present in this tree: the kit needs the specifications")
        out[f"{ROOT}/docs/atlas/spec/{name}"] = source.read_bytes()
    # line endings are the repository's (LF); a checkout with CRLF must not change the archive
    return [(name, _lf(name, data)) for name, data in sorted(out.items())]


_TEXT_SUFFIXES = (".py", ".md", ".json", ".js", ".css", ".html", ".txt", ".tsv", ".toml", ".sha256")


def _lf(name: str, data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n") if name.endswith(_TEXT_SUFFIXES) else data


def build_zip(repo_root: "Path | str | None" = None) -> bytes:
    """The kit archive. Deterministic: the same tree gives the same bytes on every call."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in entries(repo_root):
            info = zipfile.ZipInfo(name, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3          # the default depends on the host OS; the archive bytes must not
            archive.writestr(info, data)
    return buffer.getvalue()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
