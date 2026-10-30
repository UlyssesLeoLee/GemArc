"""
Scaffold builder: hand the model to the language model as fixed parts plus
explicit fill-in slots.

The point of this module is to shrink what a language model has to invent.
Everything that can be written once - the contracts, the vocabulary, the
shared engines, the site shell - is copied out as a fixed part. What remains
is a manifest that names every slot the model still has to fill, with the
exact JSON pointer, the expected shape and the rule that governs it.

Two delivery profiles share one runtime:

``static``
    Zero build step. Native ESM, an import map for the optional third-party
    libraries, served by any static file server.

``vite``
    A normal npm project. Same runtime sources, bundler resolves the optional
    libraries from node_modules.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import sync

SCAFFOLD_ROOT = Path(__file__).resolve().parent
PROFILES = ("static", "vite")
RUNTIME_DIR = SCAFFOLD_ROOT / "runtime"
VOCAB_DIR = SCAFFOLD_ROOT / "vocab"
CONTRACTS_DIR = SCAFFOLD_ROOT / "contracts"
TEMPLATES_DIR = SCAFFOLD_ROOT / "templates"

#: The slots a language model still has to fill. Each one names the file, the
#: JSON pointer inside it, what belongs there and the rule that constrains it.
SLOTS = [
    {
        "id": "meta.system",
        "pointer": "/meta/system",
        "what": "系统名称",
        "required": True,
        "rule": "非空字符串。与仓库、分支、Commit 一起构成事实来源。",
    },
    {
        "id": "meta.description",
        "pointer": "/meta/description",
        "what": "系统说明",
        "required": False,
        "rule": "一句话说明系统边界。不得包含未在证据中出现的功能。",
    },
    {
        "id": "meta.source",
        "pointer": "/meta/source",
        "what": "分析来源：repository / branch / commit / localPath / analyzedAt",
        "required": True,
        "rule": "必须记录实际分析的分支与 Commit。仓库不可访问时不得伪造分析结果。",
    },
    {
        "id": "nodes",
        "pointer": "/nodes",
        "what": "架构实体",
        "required": True,
        "rule": "kind 取自 entity-kinds 词表；verification 取自 evidence-levels 词表；"
                "每个节点必须有稳定 ID、名称与证据。实际项目不存在的实体类型不必生成。",
    },
    {
        "id": "edges",
        "pointer": "/edges",
        "what": "关系",
        "required": True,
        "rule": "sourceId / targetId 必须指向已存在的节点；type 取自 edge-types 词表；"
                "多条不同关系不得被错误合并。",
    },
    {
        "id": "evidence",
        "pointer": "/evidence",
        "what": "证据实体",
        "required": True,
        "rule": "每条证据必须有可定位的 source.locator。无法定位时标为 Unknown，而不是编造引用。",
    },
    {
        "id": "space",
        "pointer": "/space",
        "what": "Building–Floor–Room 空间结构",
        "required": False,
        "rule": "每个容器必须写 semanticSource。虚拟房间不得冒充真实代码模块或物理部署设施。",
    },
    {
        "id": "views",
        "pointer": "/views",
        "what": "图示定义",
        "required": True,
        "rule": "diagramType 取自 diagram-types 词表；只允许引用该类型允许的实体与关系；"
                "不得为了凑数量而生成虚假图示。保真要求无法满足时写进 missing 字段，不要伪造。",
    },
    {
        "id": "scenarios",
        "pointer": "/scenarios",
        "what": "4D 场景：事件与因果规则",
        "required": False,
        "rule": "evidenceClass 决定该场景能否被称为实际运行结果；Hypothetical 不得如此表述。",
    },
    {
        "id": "cases",
        "pointer": "/cases",
        "what": "5D 用例库",
        "required": False,
        "rule": "category 取自 case-categories 词表；scenarioId 必须存在；"
                "需要干预的分类必须给出 disabledNodeIds。",
    },
    {
        "id": "workflows",
        "pointer": "/workflows",
        "what": "工作流语义模型",
        "required": False,
        "rule": "每个节点、端口、条件与执行约束都必须机器可读；条件流必须给出可判断的 condition。",
    },
    {
        "id": "prototypes",
        "pointer": "/prototypes",
        "what": "页面原型",
        "required": False,
        "rule": "数据驱动的多标签页原型；不得嵌入与模型无关的虚构流程。",
    },
]


def _copy_tree(source: Path, target: Path, skip: set[str] | None = None) -> list[str]:
    """Copy a directory tree and return the list of relative file paths."""
    written = []
    if not source.is_dir():
        return written
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if skip and str(relative) in skip:
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        written.append(relative.as_posix())
    return written


def _write_json(path: Path, payload) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n": text mode would write CRLF on Windows, and render + kit must equal a build byte for byte.
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")
    return path.name


def build_scaffold(target: Path, project: str, profile: str = "static") -> dict:
    """Materialise the scaffold into ``target`` and return its manifest."""
    if profile not in PROFILES:
        raise ValueError(f"unknown profile: {profile}")
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)

    fixed: dict[str, list[str]] = {"contracts": [], "vocab": [], "runtime": [], "template": []}

    fixed["contracts"] = _copy_tree(CONTRACTS_DIR, target / "contracts")
    fixed["vocab"] = _copy_tree(VOCAB_DIR, target / "vocab")

    # The runtime is copied as source, and the generated vocabulary module is
    # refreshed from the copied JSON so the two can never disagree.
    fixed["runtime"] = _copy_tree(RUNTIME_DIR, target / "runtime")
    sync.write_module(target / "runtime" / "core" / "vocab-data.js")

    # The site shell is shared by both profiles; only the module resolution
    # differs (an import map for static, a Vite alias for vite).
    template_source = TEMPLATES_DIR / profile
    fixed["template"] = _copy_tree(TEMPLATES_DIR / "shared", target)
    fixed["template"] += _copy_tree(template_source, target)

    # The pristine template lives with the fixed parts. The live model lives at
    # the project level; ``scaffold_project`` writes it from the same template,
    # so there is exactly one definition of an empty model.
    template_model = target / "model" / "architecture.template.json"
    if not template_model.exists():
        _write_json(template_model, model_template(project))

    manifest = {
        "schemaVersion": "0.2.0",
        "project": project,
        "profile": profile,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "vocabDigest": sync.vocab_digest(),
        "fixed": fixed,
        "slots": SLOTS,
        "commands": _commands(profile),
        "rules": [
            "事实不可臆造：节点与关系必须能指回 evidence 实体。",
            "证据等级按 evidence-levels 词表标注，Implemented 不等于 Observed。",
            "表现模型与执行模型分离：画布坐标不决定业务语义。",
            "缺失信息写进 missing 字段，不要伪造。",
            "所有新增实现必须模块化，避免覆盖无关业务文件。",
        ],
    }
    _write_json(target / "scaffold-manifest.json", manifest)
    (target / "FILL-IN.md").write_text(fill_in_document(project, profile), encoding="utf-8")
    return manifest


def _commands(profile: str) -> dict:
    if profile == "vite":
        return {
            "dev": "npm install && npm run dev",
            "build": "npm install && npm run build",
            "preview": "npm run preview",
            "note": "需要 npm 访问以安装 three / graphology / dagre / dexie / sigma。",
        }
    return {
        "dev": "python -m http.server 8000 --bind 127.0.0.1",
        "build": "无需构建，直接静态托管本目录",
        "note": "零构建：原生 ESM + import map。可选第三方库通过 import map 指向 CDN，"
                "正式离线分发需改为本地 vendor。",
    }


def build_site(target: Path, model: dict, profile: str, scenarios: dict | None = None,
               capabilities: dict | None = None) -> list[str]:
    """Materialise a runnable 5D site into ``target``.

    Used by ``gemarc_build`` when the caller asks for the ``static`` or the
    ``vite`` profile. The output is a self-contained project: the site shell,
    the shared runtime and the model data.
    """
    if profile not in ("static", "vite"):
        raise ValueError(f"profile {profile} does not build a standalone site")
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)

    written = _copy_tree(TEMPLATES_DIR / "shared", target)
    written += _copy_tree(TEMPLATES_DIR / profile, target)
    written += _copy_tree(RUNTIME_DIR, target / "runtime")
    sync.write_module(target / "runtime" / "core" / "vocab-data.js")

    _write_json(target / "data" / "architecture.json", model)
    if scenarios is not None:
        _write_json(target / "data" / "scenarios.json", scenarios)
    if capabilities is not None:
        _write_json(target / "data" / "capabilities.json", capabilities)
    return written


def model_template(project: str) -> dict:
    """A skeleton canonical model with every slot present but unfilled."""
    return {
        "meta": {
            "system": project,
            "schemaVersion": "0.2.0",
            "version": None,
            "language": "zh-CN",
            "evidenceState": "Unknown",
            "description": None,
            "source": {"repository": None, "branch": None, "commit": None,
                       "localPath": None, "analyzedAt": None},
        },
        "nodes": [],
        "edges": [],
        "evidence": [],
        "space": None,
        "views": [],
        "scenarios": [],
        "cases": [],
        "workflows": [],
        "prototypes": [],
    }


def fill_in_document(project: str, profile: str) -> str:
    lines = [
        f"# {project} — 脚手架填空说明",
        "",
        "本目录由 GemArc MCP 生成。**固定件**（契约、词表、共通代码、站点外壳）不要改动；",
        "你只需要填下面列出的槽位。填完运行校验：`gemarc validate <project>`；要宣称“已确认”时再加 `--strict`（strict 要求已实现或已观测的证据）。",
        "",
        "## 槽位",
        "",
        "| 槽位 | JSON Pointer | 填什么 | 规则 |",
        "|---|---|---|---|",
    ]
    for slot in SLOTS:
        lines.append(f"| `{slot['id']}` | `{slot['pointer']}` | {slot['what']} | {slot['rule']} |")
    lines += [
        "",
        "## 固定件",
        "",
        "- `contracts/` — Canonical Model / Node / Edge / Evidence / Space / View / Scenario / Workflow 的 JSON Schema",
        "- `vocab/` — 证据等级、实体类型、关系类型、图示类型、相关性分级、空间层级、Case 分类",
        "- `runtime/` — 共通引擎：图存储与查询、Focus–Context 退避、Building–Floor–Room 空间、"
        "4D/5D 仿真内核、跨视图状态、工作流语义、互操作适配器",
        "- `scaffold-manifest.json` — 机读清单：固定件、槽位、命令",
        "",
        "## 启动",
        "",
        "```bash",
        _commands(profile)["dev"],
        "```",
        "",
        "## 硬规则",
        "",
    ]
    for rule in (
        "事实不可臆造：节点与关系必须能指回 evidence 实体。",
        "证据等级按 evidence-levels 词表标注；Implemented 不等于 Observed。",
        "表现模型与执行模型分离：画布坐标不决定业务语义。",
        "缺失信息写进 missing 字段，不要伪造。",
    ):
        lines.append(f"- {rule}")
    return "\n".join(lines) + "\n"
