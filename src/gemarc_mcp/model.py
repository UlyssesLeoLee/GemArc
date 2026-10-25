"""Shared 1D-5D fact model validation; no unverifiable facts are synthesized."""
from __future__ import annotations
import copy
import re
from .paths import diagnostic

ID_RE = re.compile(r"^[A-Za-z0-9][\w.:-]{0,127}$", re.UNICODE)
EVIDENCE_STATES = {"confirmed", "assumed", "illustrative", "unknown"}


def validate(model: dict, strict: bool = False) -> dict:
    issues: list[dict] = []
    def err(code, message, entity=""):
        issues.append(diagnostic(code, "ERROR", message, entity))
    def warn(code, message, entity=""):
        issues.append(diagnostic(code, "WARNING", message, entity))
    if not isinstance(model, dict):
        err("GEMARC_MODEL_001", "模型必须是对象")
        return _result(issues)
    meta = model.get("meta")
    if not isinstance(meta, dict) or not isinstance(meta.get("system"), str) or not meta.get("system"):
        err("GEMARC_MODEL_003", "meta.system 为必填字符串")
    evidence_state = meta.get("evidenceState", "unknown") if isinstance(meta, dict) else "unknown"
    if evidence_state not in EVIDENCE_STATES:
        err("GEMARC_MODEL_004", "无效的 meta.evidenceState")
    if strict and evidence_state != "confirmed":
        err("GEMARC_EVIDENCE_001", "严格模式要求已确认的架构事实")
    nodes = model.get("nodes")
    edges = model.get("edges")
    views = model.get("views", [])
    if not isinstance(nodes, list) or not isinstance(edges, list) or not isinstance(views, list):
        err("GEMARC_MODEL_005", "nodes/edges/views 必须是数组")
        return _result(issues)
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
        if strict and (node.get("verification") != "confirmed" or not node.get("evidence")):
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
        for key in ("source", "target"):
            if not isinstance(edge.get(key), str) or edge.get(key) not in ids:
                err("GEMARC_MODEL_012", f"{key} 节点不存在", str(eid))
        if strict and (edge.get("verification") != "confirmed" or not edge.get("evidence")):
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
        view_nodes = view.get("nodeIds", [])
        if not isinstance(view_nodes, list):
            err("GEMARC_VIEW_005", "视图 nodeIds 必须是数组", vid)
            view_nodes = []
        for node_id in view_nodes:
            if not isinstance(node_id, str) or node_id not in ids:
                err("GEMARC_VIEW_003", "视图引用不存在节点", vid)
        if not view.get("concerns"):
            warn("GEMARC_VIEW_004", "缺少架构关注点", vid)
    scenarios = model.get("scenarios", [])
    if not isinstance(scenarios, list):
        err("GEMARC_DYNAMIC_001", "scenarios 必须是数组")
    else:
        scenario_ids = set()
        for scenario in scenarios:
            if not isinstance(scenario, dict):
                err("GEMARC_DYNAMIC_002", "场景必须是对象")
                continue
            sid = scenario.get("id")
            if not isinstance(sid, str) or not ID_RE.fullmatch(sid) or sid in scenario_ids:
                err("GEMARC_DYNAMIC_003", "场景 ID 无效或重复", str(sid))
            scenario_ids.add(sid)
            if not isinstance(scenario.get("events", []), list) or not isinstance(scenario.get("rules", []), list):
                err("GEMARC_DYNAMIC_008", "场景 events/rules 必须为数组", str(sid))
                continue
            for evt in scenario.get("events", []):
                if not isinstance(evt, dict):
                    err("GEMARC_DYNAMIC_004", "事件必须是对象", str(sid))
                    continue
                if not isinstance(evt.get("module"), str) or evt.get("module") not in ids:
                    err("GEMARC_DYNAMIC_004", "事件引用不存在的模块", str(sid))
                time_ms = evt.get("timeMs")
                if (not isinstance(evt.get("event"), str) or not evt.get("event") or
                        isinstance(time_ms, bool) or not isinstance(time_ms, (int, float)) or time_ms < 0 or time_ms > 3600000):
                    err("GEMARC_DYNAMIC_005", "事件必须具有 event 和 0～3600000 的 timeMs", str(sid))
            for rule in scenario.get("rules", []):
                if not isinstance(rule, dict):
                    err("GEMARC_CAUSAL_001", "规则必须是对象", str(sid))
                    continue
                if (not isinstance(rule.get("source"), str) or rule.get("source") not in ids or
                        not isinstance(rule.get("target"), str) or rule.get("target") not in ids):
                    err("GEMARC_CAUSAL_001", "因果规则的模块端点不存在", str(sid))
                if not isinstance(rule.get("whenEvent"), str) or not isinstance(rule.get("emit"), str):
                    err("GEMARC_CAUSAL_002", "因果规则缺少 whenEvent 或 emit", str(sid))
                delay = rule.get("delayMs", 0)
                if isinstance(delay, bool) or not isinstance(delay, (int, float)) or delay < 0 or delay > 3600000:
                    err("GEMARC_CAUSAL_004", "因果规则 delayMs 必须在 0～3600000", str(sid))
            if scenario.get("evidenceClass", "hypothetical") not in ("observed", "model_derived", "hypothetical"):
                err("GEMARC_CAUSAL_005", "无效的证据等级", str(sid))
            if strict and scenario.get("evidenceClass") == "hypothetical":
                warn("GEMARC_CAUSAL_003", "假设场景不得被描述为实际运行结果", str(sid))
    return _result(issues)


def _result(issues):
    errors = [d for d in issues if d["severity"] == "ERROR"]
    return {"status": "invalid" if errors else "ok", "errors": errors,
            "warnings": [d for d in issues if d["severity"] == "WARNING"],
            "summary": {"errors": len(errors), "warnings": len(issues) - len(errors)}}


def normalized_model(model):
    copied = copy.deepcopy(model)
    copied.setdefault("views", [])
    copied.setdefault("scenarios", [])
    copied.setdefault("meta", {}).setdefault("schemaVersion", "0.1.0")
    return copied
