"""Shared 1D-5D fact model validation; no unverifiable facts are synthesized.

The vocabulary in ``scaffold/vocab/*.json`` is the single source of truth for
evidence levels, entity kinds, edge types, diagram types and case categories.
The browser runtime reads the same data through the generated
``scaffold/runtime/core/vocab-data.js``, so the prompt, this validator and the
site cannot drift apart.

Normalisation is mirrored by ``scaffold/runtime/core/model.js``. The two
implementations must stay in step: the golden simulation cases are compared
across the Python and JavaScript engines.
"""
from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from pathlib import Path

from .paths import diagnostic

ID_RE = re.compile(r"^[A-Za-z0-9][\w.:-]{0,127}$", re.UNICODE)

SCAFFOLD_VOCAB_DIR = Path(__file__).resolve().parent / "scaffold" / "vocab"
SCHEMA_VERSION = "0.2.0"


@lru_cache(maxsize=None)
def _vocab(name: str) -> dict:
    path = SCAFFOLD_VOCAB_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def evidence_levels() -> list[dict]:
    return _vocab("evidence-levels")["canonical"]


CANONICAL_EVIDENCE_LEVELS = frozenset(item["id"] for item in evidence_levels())
LEGACY_EVIDENCE_ALIASES = dict(_vocab("evidence-levels")["legacyAliases"])
EVIDENCE_RANKS = {item["id"]: item["rank"] for item in evidence_levels()}
ENTITY_KINDS = frozenset(item["id"] for item in _vocab("entity-kinds")["kinds"])
EDGE_TYPES = frozenset(item["id"] for item in _vocab("edge-types")["types"])
DIAGRAM_TYPES = {item["id"]: item for item in _vocab("diagram-types")["types"]}
CASE_CATEGORIES = frozenset(item["id"] for item in _vocab("case-categories")["categories"])
WORKFLOW_NODE_KINDS = frozenset(item["id"] for item in _vocab("workflow-kinds")["nodeKinds"])
WORKFLOW_EDGE_KINDS = frozenset(item["id"] for item in _vocab("workflow-kinds")["edgeKinds"])
WORKFLOW_CONSTRAINTS = frozenset(item["id"] for item in _vocab("workflow-kinds")["executionConstraints"])

#: A strict model must be backed by implemented or observed facts.
STRICT_MINIMUM_RANK = 5


def canonical_evidence_level(value) -> str:
    """Map a legacy or canonical spelling onto the canonical evidence level.

    The comparison is case insensitive: a language model filling the model may
    write "Illustrative" or "illustrative" and both mean the same level. The
    closed vocabulary of fourteen spellings cannot collide under folding.
    """
    if not isinstance(value, str):
        return value
    if value in CANONICAL_EVIDENCE_LEVELS:
        return value
    lowered = value.lower()
    for canonical in CANONICAL_EVIDENCE_LEVELS:
        if canonical.lower() == lowered:
            return canonical
    return LEGACY_EVIDENCE_ALIASES.get(lowered, value)


def evidence_rank(value) -> int:
    """Strength of an evidence level, 1 (Unknown) to 6 (Implemented). 0 = invalid."""
    return EVIDENCE_RANKS.get(canonical_evidence_level(value), 0)


def _as_array(value) -> list:
    return value if isinstance(value, list) else []


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) and not isinstance(value, bool) else {}


def normalized_model(model: dict) -> dict:
    """Return a canonical copy of the model.

    Mirrors ``scaffold/runtime/core/model.js``:

    1. edge ``source`` / ``target`` are rewritten to ``sourceId`` / ``targetId``
    2. evidence levels are canonicalised
    3. every collection gets a default empty array
    4. meta gets schemaVersion / language / evidenceState defaults
    """
    copied = copy.deepcopy(model if isinstance(model, dict) else {})
    meta = _as_dict(copied.get("meta"))
    meta.setdefault("schemaVersion", SCHEMA_VERSION)
    meta.setdefault("language", "zh-CN")
    meta["evidenceState"] = canonical_evidence_level(meta.get("evidenceState", "Unknown"))
    copied["meta"] = meta

    copied["nodes"] = [_normalize_node(item) for item in _as_array(copied.get("nodes"))]
    copied["edges"] = [_normalize_edge(item) for item in _as_array(copied.get("edges"))]
    copied["evidence"] = [_normalize_evidence(item) for item in _as_array(copied.get("evidence"))]
    for key in ("views", "scenarios", "cases", "workflows", "prototypes"):
        copied[key] = _as_array(copied.get(key))
    copied["space"] = _as_dict(copied.get("space")) if copied.get("space") else None
    return copied


def _normalize_node(node) -> dict:
    item = _as_dict(node)
    item["properties"] = _as_dict(item.get("properties"))
    item["parentId"] = item.get("parentId")
    item["role"] = item.get("role")
    item["domainId"] = item.get("domainId")
    item["evidenceIds"] = _as_array(item.get("evidenceIds"))
    item["verification"] = canonical_evidence_level(item.get("verification", "Unknown"))
    item["spaceRef"] = item.get("spaceRef")
    item["attributes"] = _as_array(item.get("attributes"))
    item["methods"] = _as_array(item.get("methods"))
    return item


def _normalize_edge(edge) -> dict:
    item = _as_dict(edge)
    source_id = item.get("sourceId")
    if source_id is None:
        source_id = item.get("source")
    target_id = item.get("targetId")
    if target_id is None:
        target_id = item.get("target")
    item["sourceId"] = source_id
    item["targetId"] = target_id
    item.pop("source", None)
    item.pop("target", None)
    edge_type = item.get("type")
    if edge_type is None:
        edge_type = item.get("kind")
    item["type"] = edge_type
    item.pop("kind", None)
    item["directed"] = bool(item.get("directed", True))
    condition = item.get("condition")
    item["condition"] = None if condition == "" else condition
    item["weight"] = item.get("weight")
    item["properties"] = _as_dict(item.get("properties"))
    item["evidenceIds"] = _as_array(item.get("evidenceIds"))
    item["verification"] = canonical_evidence_level(item.get("verification", "Unknown"))
    item["trustBoundary"] = item.get("trustBoundary")
    item["spaceSpan"] = item.get("spaceSpan")
    return item


def _normalize_evidence(item) -> dict:
    record = _as_dict(item)
    level = canonical_evidence_level(record.get("level") or record.get("verification") or "Unknown")
    record["level"] = level
    record["verification"] = level
    record["source"] = _as_dict(record.get("source"))
    return record


def validate(model: dict, strict: bool = False) -> dict:
    issues: list[dict] = []

    def err(code, message, entity=""):
        issues.append(diagnostic(code, "ERROR", message, entity))

    def warn(code, message, entity=""):
        issues.append(diagnostic(code, "WARNING", message, entity))

    if not isinstance(model, dict):
        err("GEMARC_MODEL_001", "模型必须是对象")
        return _result(issues)

    canonical = normalized_model(model)
    meta = canonical["meta"]
    if not isinstance(meta.get("system"), str) or not meta.get("system"):
        err("GEMARC_MODEL_003", "meta.system 为必填字符串")

    evidence_state = meta.get("evidenceState", "Unknown")
    if evidence_state not in CANONICAL_EVIDENCE_LEVELS:
        err("GEMARC_MODEL_004", "无效的 meta.evidenceState")
    elif strict and evidence_rank(evidence_state) < STRICT_MINIMUM_RANK:
        err("GEMARC_EVIDENCE_001", "严格模式要求已实现或已观测的架构事实")

    evidence_ids = set()
    for record in canonical["evidence"]:
        ident = record.get("id")
        if not isinstance(ident, str) or not ID_RE.fullmatch(ident):
            err("GEMARC_MODEL_030", "证据 ID 无效", str(ident))
            continue
        if ident in evidence_ids:
            err("GEMARC_MODEL_031", "证据 ID 重复", ident)
        evidence_ids.add(ident)
        if record.get("level") not in CANONICAL_EVIDENCE_LEVELS:
            err("GEMARC_MODEL_021", "未登记的证据等级", ident)
        source = record.get("source") or {}
        if not isinstance(source, dict) or not source.get("locator"):
            warn("GEMARC_EVIDENCE_006", "证据缺少可定位的来源", ident)

    nodes = canonical["nodes"]
    edges = canonical["edges"]
    views = canonical["views"]
    ids = set()
    for node in nodes:
        if not isinstance(node, dict):
            err("GEMARC_MODEL_006", "节点必须是对象")
            continue
        ident = node.get("id")
        if not isinstance(ident, str) or not ID_RE.fullmatch(ident):
            err("GEMARC_MODEL_007", "节点 ID 无效", str(ident))
            continue
        if ident in ids:
            err("GEMARC_MODEL_008", "节点 ID 重复", ident)
        ids.add(ident)
        if not isinstance(node.get("name"), str) or not node.get("name"):
            err("GEMARC_MODEL_009", "节点缺少名称", ident)
        if node.get("kind") not in ENTITY_KINDS:
            if strict:
                err("GEMARC_MODEL_022", "未登记的实体类型", ident)
            else:
                warn("GEMARC_MODEL_022", "未登记的实体类型", ident)
        _check_evidence_refs(node, evidence_ids, err, warn)
        if strict and (evidence_rank(node.get("verification")) < STRICT_MINIMUM_RANK or not node.get("evidence")):
            err("GEMARC_EVIDENCE_002", "节点缺少可确认的证据", ident)
        elif not node.get("evidence"):
            warn("GEMARC_EVIDENCE_003", "节点无证据，必须在页面标注", ident)

    edge_ids = set()
    for edge in edges:
        if not isinstance(edge, dict):
            err("GEMARC_MODEL_010", "边必须是对象")
            continue
        eid = edge.get("id")
        if not isinstance(eid, str) or not ID_RE.fullmatch(eid) or eid in edge_ids:
            err("GEMARC_MODEL_011", "边 ID 无效或重复", str(eid))
        else:
            edge_ids.add(eid)
        if edge.get("type") not in EDGE_TYPES:
            if strict:
                err("GEMARC_MODEL_023", "未登记的关系类型", str(eid))
            else:
                warn("GEMARC_MODEL_023", "未登记的关系类型", str(eid))
        for key in ("sourceId", "targetId"):
            if not isinstance(edge.get(key), str) or edge.get(key) not in ids:
                err("GEMARC_MODEL_012", f"{key} 节点不存在", str(eid))
        _check_evidence_refs(edge, evidence_ids, err, warn)
        if strict and (evidence_rank(edge.get("verification")) < STRICT_MINIMUM_RANK or not edge.get("evidence")):
            err("GEMARC_EVIDENCE_004", "边缺少可确认的证据", str(eid))
        elif not edge.get("evidence"):
            warn("GEMARC_EVIDENCE_005", "关系无证据", str(eid))

    view_ids = set()
    for view in views:
        if not isinstance(view, dict) or not isinstance(view.get("id"), str):
            err("GEMARC_VIEW_001", "视图必须拥有 ID")
            continue
        vid = view["id"]
        if vid in view_ids:
            err("GEMARC_VIEW_002", "重复 View ID", vid)
        view_ids.add(vid)
        _validate_view(view, ids, err, warn)

    _validate_space(canonical.get("space"), ids, err, warn)
    _validate_cases(canonical.get("cases"), canonical.get("scenarios"), ids, err, warn)
    _validate_workflows(canonical.get("workflows"), ids, err, warn)
    _validate_scenarios(canonical.get("scenarios"), ids, err, warn, strict)
    return _result(issues)


def _check_evidence_refs(entity, evidence_ids, err, warn) -> None:
    ident = entity.get("id", "")
    for ref in _as_array(entity.get("evidenceIds")):
        if not isinstance(ref, str):
            err("GEMARC_EVIDENCE_007", "证据引用必须是字符串", str(ident))
        elif ref not in evidence_ids:
            err("GEMARC_EVIDENCE_008", "证据引用不存在", str(ident))


def _validate_view(view, node_ids, err, warn) -> None:
    vid = view.get("id", "")
    diagram_type = view.get("diagramType")
    spec = DIAGRAM_TYPES.get(diagram_type)
    if spec is None:
        if diagram_type is None:
            # 0.1.x views did not carry a diagram type. Not an error yet.
            warn("GEMARC_VIEW_006", "视图未声明 diagramType", str(vid))
        else:
            err("GEMARC_VIEW_006", "未登记的 diagramType", str(vid))
    view_nodes = view.get("nodeIds", [])
    if not isinstance(view_nodes, list):
        err("GEMARC_VIEW_005", "视图 nodeIds 必须是数组", str(vid))
        view_nodes = []
    for node_id in view_nodes:
        if not isinstance(node_id, str) or node_id not in node_ids:
            err("GEMARC_VIEW_003", "视图引用不存在节点", str(vid))
    if not view.get("concerns"):
        warn("GEMARC_VIEW_004", "缺少架构关注点", str(vid))
    if spec is None:
        return
    allowed_edges = set(spec.get("edgeKinds") or [])
    for edge_kind in _as_array(view.get("edgeKinds")):
        if edge_kind not in allowed_edges and edge_kind not in EDGE_TYPES:
            err("GEMARC_VIEW_008", "视图引用了未登记的关系类型", str(vid))


def _validate_space(space, node_ids, err, warn) -> None:
    if not space:
        return
    buildings = {item.get("id") for item in _as_array(space.get("buildings")) if isinstance(item, dict)}
    floors = {item.get("id") for item in _as_array(space.get("floors")) if isinstance(item, dict)}
    rooms = {item.get("id") for item in _as_array(space.get("rooms")) if isinstance(item, dict)}
    for item in _as_array(space.get("buildings")):
        if not isinstance(item, dict):
            err("GEMARC_SPACE_003", "楼宇必须是对象")
            continue
        if not item.get("semanticSource"):
            err("GEMARC_SPACE_002", "空间容器必须保存语义来源", str(item.get("id", "")))
    for item in _as_array(space.get("floors")):
        if not isinstance(item, dict):
            err("GEMARC_SPACE_003", "楼层必须是对象")
            continue
        if item.get("buildingId") not in buildings:
            err("GEMARC_SPACE_001", "楼层引用的楼宇不存在", str(item.get("id", "")))
        if not item.get("semanticSource"):
            err("GEMARC_SPACE_002", "空间容器必须保存语义来源", str(item.get("id", "")))
    for item in _as_array(space.get("rooms")):
        if not isinstance(item, dict):
            err("GEMARC_SPACE_003", "房间必须是对象")
            continue
        if item.get("floorId") not in floors:
            err("GEMARC_SPACE_001", "房间引用的楼层不存在", str(item.get("id", "")))
        if not item.get("semanticSource"):
            err("GEMARC_SPACE_002", "空间容器必须保存语义来源", str(item.get("id", "")))


def _validate_cases(cases, scenarios, node_ids, err, warn) -> None:
    scenario_ids = {item.get("id") for item in _as_array(scenarios) if isinstance(item, dict)}
    seen = set()
    for case in _as_array(cases):
        if not isinstance(case, dict):
            err("GEMARC_CASE_004", "Case 必须是对象")
            continue
        cid = case.get("id")
        if not isinstance(cid, str) or not ID_RE.fullmatch(cid) or cid in seen:
            err("GEMARC_CASE_005", "Case ID 无效或重复", str(cid))
        else:
            seen.add(cid)
        if case.get("scenarioId") not in scenario_ids:
            err("GEMARC_CASE_001", "Case 引用的场景不存在", str(cid))
        if case.get("category") not in CASE_CATEGORIES:
            err("GEMARC_CASE_002", "无效的 Case 分类", str(cid))
        for node_id in _as_array(case.get("disabledNodeIds")):
            if node_id not in node_ids:
                err("GEMARC_CASE_003", "Case 屏蔽的节点不存在", str(cid))


def _validate_workflows(workflows, node_ids, err, warn) -> None:
    seen = set()
    for workflow in _as_array(workflows):
        if not isinstance(workflow, dict):
            err("GEMARC_WORKFLOW_004", "工作流必须是对象")
            continue
        wid = workflow.get("id")
        if not isinstance(wid, str) or not ID_RE.fullmatch(wid) or wid in seen:
            err("GEMARC_WORKFLOW_005", "工作流 ID 无效或重复", str(wid))
        else:
            seen.add(wid)
        if workflow.get("kind") and workflow.get("kind") not in WORKFLOW_NODE_KINDS:
            err("GEMARC_WORKFLOW_006", "未登记的工作流类型", str(wid))
        local_ids = {item.get("id") for item in _as_array(workflow.get("nodes")) if isinstance(item, dict)}
        for node in _as_array(workflow.get("nodes")):
            if not isinstance(node, dict):
                err("GEMARC_WORKFLOW_007", "工作流节点必须是对象", str(wid))
                continue
            if node.get("kind") not in WORKFLOW_NODE_KINDS:
                err("GEMARC_WORKFLOW_008", "未登记的工作流节点类型", str(node.get("id", "")))
            bound = node.get("canonicalNodeId")
            if bound is not None and bound not in node_ids:
                err("GEMARC_WORKFLOW_001", "工作流节点绑定的架构节点不存在", str(node.get("id", "")))
        for edge in _as_array(workflow.get("edges")):
            if not isinstance(edge, dict):
                err("GEMARC_WORKFLOW_009", "工作流边必须是对象", str(wid))
                continue
            if edge.get("kind") not in WORKFLOW_EDGE_KINDS:
                err("GEMARC_WORKFLOW_010", "未登记的工作流边类型", str(edge.get("id", "")))
            for key in ("sourceId", "targetId"):
                if edge.get(key) not in local_ids:
                    err("GEMARC_WORKFLOW_002", "工作流边引用不存在的节点", str(edge.get("id", "")))
            if edge.get("kind") == "conditional" and not edge.get("condition"):
                err("GEMARC_WORKFLOW_003", "条件流必须声明可判断的条件", str(edge.get("id", "")))


def _validate_scenarios(scenarios, node_ids, err, warn, strict) -> None:
    scenario_ids = set()
    for scenario in _as_array(scenarios):
        if not isinstance(scenario, dict):
            err("GEMARC_DYNAMIC_002", "场景必须是对象")
            continue
        sid = scenario.get("id")
        if not isinstance(sid, str) or not ID_RE.fullmatch(sid) or sid in scenario_ids:
            err("GEMARC_DYNAMIC_003", "场景 ID 无效或重复", str(sid))
        else:
            scenario_ids.add(sid)
        if not isinstance(scenario.get("events", []), list) or not isinstance(scenario.get("rules", []), list):
            err("GEMARC_DYNAMIC_008", "场景 events/rules 必须为数组", str(sid))
            continue
        for evt in scenario.get("events", []):
            if not isinstance(evt, dict):
                err("GEMARC_DYNAMIC_004", "事件必须是对象", str(sid))
                continue
            if not isinstance(evt.get("module"), str) or evt.get("module") not in node_ids:
                err("GEMARC_DYNAMIC_004", "事件引用不存在的模块", str(sid))
            time_ms = evt.get("timeMs")
            if (not isinstance(evt.get("event"), str) or not evt.get("event") or
                    isinstance(time_ms, bool) or not isinstance(time_ms, (int, float)) or time_ms < 0 or time_ms > 3600000):
                err("GEMARC_DYNAMIC_005", "事件必须具有 event 和 0～3600000 的 timeMs", str(sid))
        for rule in scenario.get("rules", []):
            if not isinstance(rule, dict):
                err("GEMARC_CAUSAL_001", "规则必须是对象", str(sid))
                continue
            if (not isinstance(rule.get("source"), str) or rule.get("source") not in node_ids or
                    not isinstance(rule.get("target"), str) or rule.get("target") not in node_ids):
                err("GEMARC_CAUSAL_001", "因果规则的模块端点不存在", str(sid))
            if not isinstance(rule.get("whenEvent"), str) or not isinstance(rule.get("emit"), str):
                err("GEMARC_CAUSAL_002", "因果规则缺少 whenEvent 或 emit", str(sid))
            delay = rule.get("delayMs", 0)
            if isinstance(delay, bool) or not isinstance(delay, (int, float)) or delay < 0 or delay > 3600000:
                err("GEMARC_CAUSAL_004", "因果规则 delayMs 必须在 0～3600000", str(sid))
        evidence_class = scenario.get("evidenceClass", "hypothetical")
        if evidence_class not in ("observed", "model_derived", "hypothetical"):
            err("GEMARC_CAUSAL_005", "无效的证据等级", str(sid))
        if strict and evidence_class == "hypothetical":
            warn("GEMARC_CAUSAL_003", "假设场景不得被描述为实际运行结果", str(sid))


def _result(issues):
    errors = [d for d in issues if d["severity"] == "ERROR"]
    return {"status": "invalid" if errors else "ok", "errors": errors,
            "warnings": [d for d in issues if d["severity"] == "WARNING"],
            "summary": {"errors": len(errors), "warnings": len(issues) - len(errors)}}
