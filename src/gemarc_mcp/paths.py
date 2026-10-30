"""Workspace boundary, size limits and safe file operations."""
from __future__ import annotations
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

MAX_MODEL_BYTES = 4 * 1024 * 1024
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")

class GemArcError(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(f"{code}: {message}")

class Workspace:
    def __init__(self, root: str | Path):
        try:
            self.root = Path(root).expanduser().resolve(strict=True)
        except OSError as ex:
            raise GemArcError("GEMARC_AUTH_001", "GEMARC_WORKSPACE_ROOT 必须是现有目录") from ex
        if not self.root.is_dir():
            raise GemArcError("GEMARC_AUTH_001", "GEMARC_WORKSPACE_ROOT 必须是现有目录")

    def path(self, supplied: str | Path) -> Path:
        candidate = Path(supplied).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        candidate = candidate.resolve(strict=False)
        if candidate != self.root and self.root not in candidate.parents:
            raise GemArcError("GEMARC_AUTH_001", "目标位于工作区之外")
        return candidate

    def project(self, name: str) -> Path:
        if not NAME_RE.fullmatch(name) or name in (".", ".."):
            raise GemArcError("GEMARC_INPUT_001", "项目名必须是安全的 ASCII 名称")
        return self.path(name)

    def writable(self):
        if os.environ.get("GEMARC_ALLOW_WRITE") != "1":
            raise GemArcError("GEMARC_AUTH_002", "写操作关闭；需由用户在 MCP 配置中设置 GEMARC_ALLOW_WRITE=1")


def read_json(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size > MAX_MODEL_BYTES:
        raise GemArcError("GEMARC_IO_001", "文件不存在或超过 4MiB 限额")
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        raise GemArcError("GEMARC_MODEL_001", "JSON 读取失败") from ex
    if not isinstance(obj, dict):
        raise GemArcError("GEMARC_MODEL_001", "JSON 顶层必须是对象")
    return obj


def encode_json(value: dict) -> bytes:
    # allow_nan=False: NaN / Infinity are not JSON; a file or a tool result holding them breaks strict parsers.
    raw = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    if len(raw) > MAX_MODEL_BYTES:
        raise GemArcError("GEMARC_MODEL_002", "模型大小超过 4MiB")
    return raw


def atomic_json(path: Path, value: dict):
    payload = encode_json(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".gemarc-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def diagnostic(code: str, severity: str, message: str, entity: str = "") -> dict:
    return {"id": code, "severity": severity, "message": message, "entityId": entity}


def safe_response(action):
    try:
        return action()
    except GemArcError as ex:
        return {"status": "invalid", "diagnostics": [diagnostic(ex.code, "ERROR", ex.message)], "artifactRefs": []}
    except (OSError, ValueError, KeyError, TypeError) as ex:
        # Avoid exposing host filesystem locations or secrets in MCP tool errors.
        return {"status": "error", "diagnostics": [diagnostic("GEMARC_INTERNAL_001", "ERROR", type(ex).__name__)], "artifactRefs": []}
