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
from .stateless import precompute_scenarios, site_capabilities

SKIP_DIRS = {".git", "node_modules", "target", "dist", "build", ".venv", ".next"}
EXTS = {".py", ".rs", ".cs", ".java", ".ts", ".tsx", ".js", ".json", ".md", ".sql"}
MAX_SCAN_FILES = 1500
MAX_SCAN_BYTES = 12 * 1024 * 1024


def sample_model(name: str) -> dict:
    return {"meta": {"system": name, "schemaVersion": "0.1.0", "version": "0.1.0", "language": "zh-CN",
                     "evidenceState": "illustrative", "description": "示例模型，非真实工程事实"},
            "nodes": [
                {"id": "sensor", "name": "事件源", "kind": "Component", "layer": "设备层", "verification": "illustrative", "evidence": "示例"},
                {"id": "queue", "name": "消息队列", "kind": "Service", "layer": "中间件层", "verification": "illustrative", "evidence": "示例"},
                {"id": "rule", "name": "规则引擎", "kind": "Service", "layer": "应用层", "verification": "illustrative", "evidence": "示例"},
                {"id": "notify", "name": "通知服务", "kind": "Service", "layer": "应用层", "verification": "illustrative", "evidence": "示例"}
            ],
            "edges": [
                {"id": "e1", "sourceId": "sensor", "targetId": "queue", "type": "sends", "description": "上传事件", "verification": "illustrative", "evidence": "示例"},
                {"id": "e2", "sourceId": "queue", "targetId": "rule", "type": "sends", "description": "传递事件", "verification": "illustrative", "evidence": "示例"},
                {"id": "e3", "sourceId": "rule", "targetId": "notify", "type": "sends", "description": "规则触发通知", "verification": "illustrative", "evidence": "示例"}
            ],
            "views": [{"id": "logical", "title": "逻辑架构", "diagramType": "logical-view", "stakeholders": ["架构师"], "concerns": ["事件依赖"],
                       "nodeIds": ["sensor", "queue", "rule", "notify"], "edgeKinds": ["sends"], "reviewIntent": "识别单点故障"}],
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


def brief(project: str = "", section: str = "", stateless: bool = False) -> dict:
    """The compact work order handed to a language model.

    A long requirement document spends most of its length restating things
    that are already written down somewhere: field names, allowed values,
    engine behaviour. This returns the part a model actually needs - the slots
    to fill, the rules constraining them, the vocabulary it may use, the
    commands to run, and the inventory of parts that are already done.
    """
    from .scaffold import builder, sync

    vocabulary = sync.load_vocab()
    payload = {
        "status": "ok",
        "schemaVersion": "0.2.0",
        "vocabDigest": sync.vocab_digest(),
        "project": project or None,
        "purpose": (
            "用固定的架构模型与既成的引擎，产出可审查的 0D–5D 架构站点。"
            "不要重新发明引擎、字段名或算法；只填事实，并把缺失如实标注为缺失。"
        ),
        "procedure": [
            "1. 读 scaffold-manifest.json 与 FILL-IN.md，确认槽位清单。",
            "2. 读 contracts/*.schema.json 确定每个对象该长什么样；读 vocab/*.json 确认允许哪些词。",
            "3. 填写 model/architecture.json 的槽位。证据必须能指回真实来源；指不回去就标 Unknown。",
            "4. 运行 gemarc_validate <project>，修掉所有 error；要宣称“已确认”时再加 --strict"
            "（strict 要求节点和边有已实现或已观测的证据，标 Unknown/Hypothetical 的事实必然不过，不要为此改证据等级）。",
            "5. 运行 gemarc_build <project> --profile static（或 vite），产出站点。",
        ],
        "rules": [
            "事实不可臆造：节点与关系必须能指回 evidence 实体。",
            "证据等级按 evidence-levels 词表标注；Implemented 不等于 Observed。",
            "表现模型与执行模型分离：画布坐标不决定业务语义。",
            "缺失信息写进 missing 字段，不要伪造。",
            "实际项目不存在的实体类型不必生成；不要为了凑数量造图示。",
            "所有新增实现必须模块化，不要覆盖固定件。",
        ],
        "slots": [
            {"id": slot["id"], "pointer": slot["pointer"], "what": slot["what"],
             "required": slot["required"], "rule": slot["rule"]}
            for slot in builder.SLOTS
        ],
        "vocabulary": {
            "evidenceLevels": [
                {"id": item["id"], "meaning": item["meaning"], "assertion": item["assertion"]}
                for item in vocabulary["evidence-levels"]["canonical"]
            ],
            "entityKinds": [item["id"] for item in vocabulary["entity-kinds"]["kinds"]],
            "edgeTypes": [item["id"] for item in vocabulary["edge-types"]["types"]],
            "diagramTypes": [
                {"id": item["id"], "dimension": item["dimension"],
                 "nodeKinds": item["nodeKinds"], "edgeKinds": item["edgeKinds"],
                 "fidelity": item["fidelity"]}
                for item in vocabulary["diagram-types"]["types"]
            ],
            "caseCategories": [
                {"id": item["id"], "assertion": item["assertion"]}
                for item in vocabulary["case-categories"]["categories"]
            ],
            "workflowNodeKinds": [item["id"] for item in vocabulary["workflow-kinds"]["nodeKinds"]],
            "workflowEdgeKinds": [item["id"] for item in vocabulary["workflow-kinds"]["edgeKinds"]],
            "spaceLevels": [
                {"id": item["id"], "represents": item["represents"]}
                for item in vocabulary["space-levels"]["levels"]
            ],
        },
        "commands": builder._commands("static"),
        "alreadyDone": {
            "contracts": sorted(p.name for p in builder.CONTRACTS_DIR.glob("*.json")),
            "vocabularies": sorted(p.name for p in builder.VOCAB_DIR.glob("*.json")),
            "runtime": "图引擎 / Focus 退避 / 3D 空间 / 4D-5D 仿真 / 跨视图状态 / 工作流语义 / 互操作适配器",
            "site": "两套交付 profile 共用一份应用源码：static 零构建、vite 为 npm 工程",
        },
    }
    if stateless:
        # A remote deployment has no workspace: the work order must only name
        # tools that exist there, and must not tell the model to run local CLIs.
        payload["procedure"] = [
            "1. gemarc_brief 取得规则与词表；需要完整契约时用 gemarc_contracts。",
            "2. gemarc_new_model 取得空骨架；用你自己对用户仓库的访问能力读取事实并填写槽位。",
            "3. 证据必须能指回真实来源；指不回去就标 Unknown，不要编造。",
            "4. gemarc_validate(model=…) 反复修正，直到 0 个 error；要宣称“已确认”时再加 strict=true"
            "（strict 要求节点和边有已实现或已观测的证据，标 Unknown/Hypothetical 的事实必然不过，不要为此改证据等级）。",
            "5. gemarc_simulate(model=…, scenario_id=…, compare_baseline=true) 核对 4D/5D 场景。",
            "6. gemarc_render(model=…) 取得 data/*.json 与固定件下载地址，在本地合并成站点。",
        ]
        payload["commands"] = {"render": "gemarc_render", "kit": "/kit/static.zip",
                               "note": "远程端点无状态：模型每次随调用传入，服务端不保存任何项目。"}
        payload["alreadyDone"]["site"] = "固定件由 kit 提供；render 只返回随模型变化的 data/*.json"
    if section:
        key = section.strip()
        if key not in payload:
            raise GemArcError("GEMARC_BRIEF_001", f"未知的分段: {section}")
        return {"status": "ok", "section": key, "content": payload[key],
                "vocabDigest": payload["vocabDigest"]}
    return payload


def contracts(query: str = "") -> dict:
    """Read-only view of the fixed contracts and vocabularies."""
    from .scaffold import builder, sync

    payload = {
        "status": "ok",
        "schemaVersion": "0.2.0",
        "vocabDigest": sync.vocab_digest(),
        "contracts": sorted(p.name for p in builder.CONTRACTS_DIR.glob("*.json")),
        "vocabularies": sorted(p.name for p in builder.VOCAB_DIR.glob("*.json")),
        "profiles": list(builder.PROFILES),
        "slots": [{"id": s["id"], "pointer": s["pointer"], "what": s["what"],
                   "required": s["required"], "rule": s["rule"]} for s in builder.SLOTS],
    }
    if query:
        # ``query`` is a key into the listing above, never a path: an absolute path or ``..`` would
        # otherwise read any JSON file the process can see (and tell which paths exist).
        known = {p.name: p for p in builder.VOCAB_DIR.glob("*.json")}
        known.update({p.name: p for p in builder.CONTRACTS_DIR.glob("*.json")})  # a contract wins a name clash
        target = known.get(query)
        if target is None:
            raise GemArcError("GEMARC_CONTRACT_001", f"未找到契约文件: {query}")
        payload["document"] = json.loads(target.read_text(encoding="utf-8"))
    return payload


def scaffold_project(ws: Workspace, name: str, profile: str = "static") -> dict:
    """Materialise the fixed parts plus the fill-in slots for a project.

    The scaffold owns the project directory: it creates the project, writes the
    empty model the caller has to fill, and drops the contracts, the vocabulary
    and the shared runtime next to it.
    """
    from .scaffold import builder

    ws.writable()
    if profile not in builder.PROFILES:
        raise GemArcError("GEMARC_SCAFFOLD_001", f"未知的 profile: {profile}")
    project = ws.project(name)
    scaffold_dir = ws.path(project / "scaffold")
    manifest_path = scaffold_dir / "scaffold-manifest.json"
    if manifest_path.exists():
        raise GemArcError("GEMARC_SCAFFOLD_002", "脚手架已存在；拒绝覆盖")
    project.mkdir(parents=True, exist_ok=True)

    model_path = ws.path(project / "model" / "architecture.json")
    if not model_path.exists():
        atomic_json(model_path, builder.model_template(name))
    if not ws.path(project / "gemarc.lock.json").exists():
        atomic_json(ws.path(project / "gemarc.lock.json"),
                    {"schemaVersion": "0.2.0", "registry": "0.2.0", "generator": "0.2.0"})

    manifest = builder.build_scaffold(scaffold_dir, name, profile)
    return {
        "status": "ok",
        "project": name,
        "profile": profile,
        "scaffold": str(scaffold_dir),
        "manifest": str(manifest_path),
        "model": str(model_path),
        "slots": [slot["id"] for slot in manifest["slots"]],
        "fixedFileCount": sum(len(files) for files in manifest["fixed"].values()),
        "commands": manifest["commands"],
        "notice": "固定件已就位；填写 model/architecture.json 的槽位后运行 gemarc_build",
    }


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
    return diff_models(b, a, digest(bfile), digest(afile))


def diff_models(b: dict, a: dict, base_sha: str, target_sha: str) -> dict:
    """Changed entities and their one-hop impact between two models."""
    b, a = normalized_model(b), normalized_model(a)
    def changed(kind):
        old = {e["id"]: e for e in b.get(kind, []) if isinstance(e, dict) and "id" in e}
        new = {e["id"]: e for e in a.get(kind, []) if isinstance(e, dict) and "id" in e}
        return {"added": sorted(new.keys() - old.keys()), "removed": sorted(old.keys() - new.keys()),
                "modified": sorted(k for k in new.keys() & old.keys() if new[k] != old[k])}
    n = changed("nodes")
    e = changed("edges")
    changed_nodes = set(n["added"] + n["removed"] + n["modified"])
    impacted = set(changed_nodes)
    for edge in a.get("edges", []) + b.get("edges", []):
        # One hop: test against the changed nodes only. Testing against ``impacted`` while adding to it would
        # spread along chains of edges and make the answer depend on the order the edges are listed in.
        if edge.get("sourceId") in changed_nodes or edge.get("targetId") in changed_nodes \
                or edge.get("id") in e["modified"]:
            impacted.update([edge.get("sourceId"), edge.get("targetId")])
    return {"status": "ok", "baseSha256": base_sha, "targetSha256": target_sha,
            "nodes": n, "edges": e, "affectedNodes": sorted(impacted - {None})}


BUILD_PROFILES = ("mvp", "static", "vite")


def build(ws: Workspace, name: str, legacy_dist: str | None = None, profile: str = "mvp") -> dict:
    ws.writable()
    if profile not in BUILD_PROFILES:
        raise GemArcError("GEMARC_BUILD_005", f"未知的 build profile: {profile}")
    folder, model = load_model(ws, name)
    result = validate(model)
    if result["status"] != "ok":
        return result
    project = ws.project(name)
    dist = ws.path(project / "site")
    stage = Path(tempfile.mkdtemp(prefix=".gemarc-build-", dir=project))
    try:
        canonical = normalized_model(model)
        precomputed = precompute_scenarios(model)
        if profile == "mvp":
            source = resources.files("gemarc_mcp").joinpath("web")
            for filename in ("index.html", "app.js", "style.css"):
                stage.joinpath(filename).write_bytes(source.joinpath(filename).read_bytes())
            (stage / "data").mkdir()
            atomic_json(stage / "data" / "architecture.json", canonical)
            atomic_json(stage / "data" / "scenarios.json", precomputed)
        else:
            from .scaffold import builder
            builder.build_site(stage, canonical, profile, precomputed, None)
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
        atomic_json(stage / "data" / "capabilities.json", site_capabilities(model, profile, has_3d))
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
        commands = {"mvp": "python -m http.server 8000 --bind 127.0.0.1",
                    "static": "python -m http.server 8000 --bind 127.0.0.1",
                    "vite": "npm install && npm run dev"}[profile]
        return {"status": "ok", "site": str(dist), "index": str(dist / "index.html"),
                "profile": profile, "viewer3D": has_3d, "validation": result,
                "run": commands,
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
    capabilities = read_json(site/"data/capabilities.json")
    profile = capabilities.get("profile", "mvp")
    if profile in ("static", "vite"):
        # The full profiles ship the shared runtime; without it the site cannot
        # resolve a single module, so a missing runtime is a hard failure.
        for extra in ("runtime/core/model.js", "runtime/core/vocab-data.js",
                      "runtime/graph/graph-store.js", "runtime/sim/index.js"):
            if not (site/extra).is_file():
                missing.append(extra)
        if profile == "vite":
            for extra in ("package.json", "vite.config.js", "src/main.js"):
                if not (site/extra).is_file():
                    missing.append(extra)
    model = read_json(site/"data/architecture.json")
    data = validate(model)
    content = (site/"index.html").read_text(encoding="utf-8")
    if profile == "vite":
        # The vite entry point pulls the shared app and styles in from src/main.js.
        expected_refs = ("src/main.js", "GemArc")
    else:
        expected_refs = ("app.js", "style.css", "GemArc")
    if not all(word in content for word in expected_refs):
        missing.append("HTML 静态资源引用")
    return {"status": "ok" if not missing and data["status"] == "ok" else "invalid",
            "missing": missing, "model": data, "profile": profile,
            "check": "filesystem_and_schema_only",
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
