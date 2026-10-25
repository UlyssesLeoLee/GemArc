"""Application services, usable without an MCP host or extra SDK dependency."""
from __future__ import annotations
import json
import os
import re
import shutil
import tempfile
import zipfile
import hashlib
from importlib import resources
from pathlib import Path
from .paths import Workspace, GemArcError, read_json, atomic_json, digest
from .model import validate, normalized_model
from .simulation import simulate, compare

SKIP_DIRS = {".git", "node_modules", "target", "dist", "build", ".venv", ".next"}
EXTS = {".py", ".rs", ".cs", ".java", ".ts", ".tsx", ".js", ".json", ".md", ".sql"}
MAX_SCAN_FILES = 1500
MAX_SCAN_BYTES = 12 * 1024 * 1024


def sample_model(name: str) -> dict:
    return {"meta": {"system": name, "schemaVersion": "0.1.0", "version": "0.1.0", "language": "zh-CN",
                     "evidenceState": "illustrative", "description": "示例模型，非真实工程事实"},
            "nodes": [
                {"id": "sensor", "name": "事件源", "kind": "adapter", "layer": "设备层", "verification": "illustrative", "evidence": "示例"},
                {"id": "queue", "name": "消息队列", "kind": "service", "layer": "中间件层", "verification": "illustrative", "evidence": "示例"},
                {"id": "rule", "name": "规则引擎", "kind": "service", "layer": "应用层", "verification": "illustrative", "evidence": "示例"},
                {"id": "notify", "name": "通知服务", "kind": "service", "layer": "应用层", "verification": "illustrative", "evidence": "示例"}
            ],
            "edges": [
                {"id": "e1", "source": "sensor", "target": "queue", "kind": "event", "description": "上传事件", "verification": "illustrative", "evidence": "示例"},
                {"id": "e2", "source": "queue", "target": "rule", "kind": "event", "description": "传递事件", "verification": "illustrative", "evidence": "示例"},
                {"id": "e3", "source": "rule", "target": "notify", "kind": "event", "description": "规则触发通知", "verification": "illustrative", "evidence": "示例"}
            ],
            "views": [{"id": "logical", "title": "逻辑架构", "stakeholders": ["架构师"], "concerns": ["事件依赖"],
                       "nodeIds": ["sensor", "queue", "rule", "notify"], "edgeKinds": ["event"], "reviewIntent": "识别单点故障"}],
            "scenarios": [{"id": "normal", "name": "示例：正常事件传播", "evidenceClass": "hypothetical",
                           "events": [{"module": "sensor", "event": "emit", "timeMs": 0}],
                           "rules": [
                               {"source": "sensor", "whenEvent": "emit", "target": "queue", "emit": "ingest", "delayMs": 10},
                               {"source": "queue", "whenEvent": "ingest", "target": "rule", "emit": "evaluate", "delayMs": 20},
                               {"source": "rule", "whenEvent": "evaluate", "target": "notify", "emit": "send", "delayMs": 10}
                           ]}],
            "prototypes": [{"id": "overview", "title": "系统概览", "description": "组件状态查看示例", "actions": ["查看流程", "查看节点"]},
                           {"id": "settings", "title": "设置", "description": "示例原型数据", "actions": ["保存设置"]}]}


def load_model(ws: Workspace, name: str) -> tuple[Path, dict]:
    project = ws.project(name)
    file = ws.path(project / "model" / "architecture.json")
    return file, read_json(file)


def init_project(ws: Workspace, name: str) -> dict:
    ws.writable()
    path = ws.project(name)
    if path.exists():
        raise GemArcError("GEMARC_IO_002", "项目目录已经存在；拒绝覆盖")
    path.mkdir()
    try:
        atomic_json(path / "model" / "architecture.json", sample_model(name))
        atomic_json(path / "gemarc.lock.json", {"schemaVersion": "0.1.0", "registry": "0.1.0", "generator": "0.1.0"})
        (path / "README.md").write_text("# " + name + "\n\n这是示例模型；将 model/architecture.json 替换为有证据的真实工程事实再正式交付。\n", encoding="utf-8")
    except Exception:
        shutil.rmtree(path)
        raise
    return {"status": "ok", "project": name, "projectPath": str(path), "model": str(path / "model" / "architecture.json"),
            "notice": "已创建演示样例，所有业务事实必须重新核验"}


def inspect_path(ws: Workspace, supplied: str) -> dict:
    root = ws.path(supplied)
    if not root.is_dir():
        raise GemArcError("GEMARC_INPUT_002", "请先将目标仓库克隆到授权工作区，然后传入本地路径")
    visited = 0
    total = 0
    by_ext = {}
    candidates = []
    excluded = 0
    for here, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted([x for x in dirs if x not in SKIP_DIRS and not (Path(here)/x).is_symlink()])
        for filename in sorted(files):
            p = Path(here) / filename
            if p.is_symlink() or p.suffix.lower() not in EXTS:
                continue
            if visited >= MAX_SCAN_FILES or total >= MAX_SCAN_BYTES:
                excluded += 1
                continue
            size = p.stat().st_size
            if size > 256 * 1024 or total + size > MAX_SCAN_BYTES:
                excluded += 1
                continue
            total += size
            visited += 1
            ext = p.suffix.lower()
            by_ext[ext] = by_ext.get(ext, 0) + 1
            if len(candidates) < 60 and ext in {".py", ".rs", ".cs", ".java", ".ts", ".tsx"}:
                candidates.append({"name": p.stem, "file": str(p.relative_to(root)).replace('\\','/'),
                                   "verification": "candidate", "evidence": "仅根据文件名推断，未确认模块职责"})
    return {"status": "ok", "scannedFiles": visited, "scannedBytes": total, "excludedFiles": excluded,
            "extensions": by_ext, "candidateFacts": candidates, "evidenceState": "candidate",
            "warning": "静态文件列表不代表真实架构，不自动将推断标为 confirmed"}


def plan(ws: Workspace, name: str, goals: list[str] | None = None) -> dict:
    _, model = load_model(ws, name)
    gaps = []
    if not model.get("views"):
        gaps.append("缺少架构 View")
    if not model.get("scenarios"):
        gaps.append("缺少动态/因果场景，4D/5D 只能标示不可用")
    if model.get("meta", {}).get("evidenceState") != "confirmed":
        gaps.append("存在未确认的事实，严禁标记为已验证架构")
    return {"status": "ok", "goals": goals or ["5D设计资料"], "views": [x.get("id") for x in model.get("views", [])],
            "dimensions": {"1D": True, "2D": True, "3D": "legacy adapter optional", "4D": bool(model.get("scenarios")),
                           "5D": bool(model.get("scenarios"))}, "gaps": gaps}


def assets(query: str = "") -> dict:
    with resources.files("gemarc_mcp").joinpath("registry.json").open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    data["components"] = [c for c in data["components"] if query.lower() in json.dumps(c, ensure_ascii=False).lower()]
    data["status"] = "ok"
    return data


def replace_model(ws: Workspace, name: str, value: dict, expected_sha256: str) -> dict:
    ws.writable()
    filename, previous = load_model(ws, name)
    current_sha = digest(filename)
    if current_sha != expected_sha256:
        raise GemArcError("GEMARC_CONFLICT_001", "模型已发生变化；先查询最新 SHA 再提交")
    normalized = normalized_model(value)
    result = validate(normalized)
    if result["status"] != "ok":
        return result
    atomic_json(filename, normalized)
    return {"status": "ok", "previousSha256": current_sha, "newSha256": digest(filename),
            "validation": result}


def diff(ws: Workspace, before: str, after: str) -> dict:
    bfile, b = load_model(ws, before)
    afile, a = load_model(ws, after)
    def changed(kind):
        old = {e["id"]: e for e in b.get(kind, []) if isinstance(e, dict) and "id" in e}
        new = {e["id"]: e for e in a.get(kind, []) if isinstance(e, dict) and "id" in e}
        return {"added": sorted(new.keys() - old.keys()), "removed": sorted(old.keys() - new.keys()),
                "modified": sorted(k for k in new.keys() & old.keys() if new[k] != old[k])}
    n = changed("nodes")
    e = changed("edges")
    impacted = set(n["added"] + n["removed"] + n["modified"])
    for edge in a.get("edges", []) + b.get("edges", []):
        if edge.get("source") in impacted or edge.get("target") in impacted or edge.get("id") in e["modified"]:
            impacted.update([edge.get("source"), edge.get("target")])
    return {"status": "ok", "baseSha256": digest(bfile), "targetSha256": digest(afile),
            "nodes": n, "edges": e, "affectedNodes": sorted(impacted - {None})}


def build(ws: Workspace, name: str, legacy_dist: str | None = None) -> dict:
    ws.writable()
    folder, model = load_model(ws, name)
    result = validate(model)
    if result["status"] != "ok":
        return result
    project = ws.project(name)
    dist = ws.path(project / "site")
    stage = Path(tempfile.mkdtemp(prefix=".gemarc-build-", dir=project))
    try:
        source = resources.files("gemarc_mcp").joinpath("web")
        for filename in ("index.html", "app.js", "style.css"):
            stage.joinpath(filename).write_bytes(source.joinpath(filename).read_bytes())
        (stage / "data").mkdir()
        atomic_json(stage / "data" / "architecture.json", model)
        precomputed = {}
        for sc in model.get("scenarios", []):
            precomputed[sc["id"]] = {"normal": simulate(model, sc["id"]),
                                     "disabledSample": compare(model, sc["id"], [model["nodes"][0]["id"]]) if model["nodes"] else None}
        atomic_json(stage / "data" / "scenarios.json", precomputed)
        has_3d = False
        if legacy_dist:
            src = ws.path(legacy_dist)
            if not (src / "index.html").is_file() or src == dist or src == stage:
                raise GemArcError("GEMARC_BUILD_001", "legacy_dist 必须是授权工作区内包含 index.html 的已构建 Viewer 目录")
            # Existing bundle must not contain symlinks that escape the authorized boundary.
            entries = list(src.rglob('*'))
            if any(p.is_symlink() for p in entries):
                raise GemArcError("GEMARC_BUILD_004", "不允许复制包含符号链接的 3D 构建产物")
            # Asset copies are limited; no executable files are run by GemArc.
            if sum(p.stat().st_size for p in entries if p.is_file()) > 50*1024*1024:
                raise GemArcError("GEMARC_BUILD_002", "legacy_dist 超过 50MiB")
            shutil.copytree(src, stage / "legacy-3d", symlinks=False, ignore=shutil.ignore_patterns("node_modules", ".git"))
            has_3d = True
        atomic_json(stage / "data" / "capabilities.json", {
            "viewer2D": "implemented", "viewer3D": "supplied_compiled_legacy_viewer" if has_3d else "not_available",
            "dynamic4D": "model_driven_mvp" if model.get("scenarios") else "no_data",
            "causal5D": "explicit_rule_mvp" if model.get("scenarios") else "no_data",
            "prototype": "basic_tabs", "evidenceState": model.get("meta", {}).get("evidenceState", "unknown")})
        if dist.exists():
            # No destructive overwrite of a user-owned non-generated folder.
            if not (dist / "data" / "capabilities.json").is_file():
                raise GemArcError("GEMARC_BUILD_003", "目标 site 目录非 GemArc 生成，拒绝覆盖")
            backup = ws.path(project / ".gemarc-old-site")
            if backup.exists():
                shutil.rmtree(backup)
            dist.rename(backup)
            try:
                stage.rename(dist)
            except Exception:
                backup.rename(dist)
                raise
            shutil.rmtree(backup)
        else:
            stage.rename(dist)
        return {"status": "ok", "site": str(dist), "index": str(dist / "index.html"),
                "viewer3D": has_3d, "validation": result,
                "notice": "4D/5D 是显式规则模拟 MVP；未进行真实浏览器/WebGL 测试"}
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def verify(ws: Workspace, name: str) -> dict:
    site = ws.path(ws.project(name) / "site")
    required = ["index.html", "app.js", "style.css", "data/architecture.json", "data/capabilities.json", "data/scenarios.json"]
    missing = [p for p in required if not (site/p).is_file()]
    if missing:
        return {"status": "invalid", "missing": missing, "check": "filesystem_only"}
    model = read_json(site/"data/architecture.json")
    data = validate(model)
    content = (site/"index.html").read_text(encoding="utf-8")
    if not all(word in content for word in ("app.js", "style.css", "GemArc")):
        missing.append("HTML 静态资源引用")
    return {"status": "ok" if not missing and data["status"] == "ok" else "invalid",
            "missing": missing, "model": data, "check": "filesystem_and_schema_only",
            "notExecuted": ["WebGL 渲染", "浏览器交互", "帧率性能", "许可证复核"]}


def export_zip(ws: Workspace, name: str) -> dict:
    ws.writable()
    project = ws.project(name)
    site = ws.path(project / "site")
    result = verify(ws, name)
    if result["status"] != "ok":
        raise GemArcError("GEMARC_EXPORT_001", "请先构建并修复静态验证失败项")
    target = ws.path(project / f"{name}-gemarc-site.zip")
    fd, tmp = tempfile.mkstemp(dir=project, prefix=".gemarc-export-", suffix=".zip")
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for p in sorted(site.rglob("*")):
                if p.is_symlink():
                    raise GemArcError("GEMARC_EXPORT_002", "导出内容包含符号链接")
                if p.is_file():
                    archive.write(p, p.relative_to(site).as_posix())
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp): os.remove(tmp)
    return {"status": "ok", "archive": str(target), "sha256": digest(target),
            "notice": "导出内容为静态站点和数据；尚未通过浏览器端功能测试"}
