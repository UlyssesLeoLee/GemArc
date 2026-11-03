"""
Derive the v5.0 deliverable files from one canonical model.

The canonical model is the single source of truth. Everything the front end
needs beyond it - an evidence index, the diagram catalogue, the spatial layout,
the versioned GWF-IR, the workflow schema and the case library - is *derived*
here, so the same model always yields the same files.

The derivations follow v5.0 §3 (canonical model and its output files), §9
(diagram families and fidelity), §13 (case contract), §17 (GWF-IR), §22
(spatial collections) and §25 (output directory):

``evidence-index.json``    every evidence record plus the reverse index of who cites it
``diagram-catalog.json``   the diagram catalogue with the fidelity the model cannot supply
``spatial-layout.json``    Building-Floor-Room with the node placement it supports
``workflow-schema.json``   the machine-readable GWF-IR field catalogue and its JSON Schema
``workflow-ir.json``       the versioned GWF-IR of every workflow in the model
``case-library.json``      the 5D case library in the §13 case contract shape

Two rules hold everywhere: a fact that the model does not contain is reported
as *missing*, never invented, and nothing here writes a file - the caller
decides where the bytes go.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

SCAFFOLD_ROOT = Path(__file__).resolve().parent
VOCAB_DIR = SCAFFOLD_ROOT / "vocab"
CONTRACTS_DIR = SCAFFOLD_ROOT / "contracts"

#: Schema version of every derived file.
DERIVED_SCHEMA_VERSION = "0.2.0"

#: §17 node types that have no real executor in this delivery. They must be
#: marked Design-only: claiming they run would be a false statement.
DESIGN_ONLY_NODE_TYPES = ("Agent", "HumanApproval")

#: Legacy node kinds that have no unambiguous §17 counterpart. The GWF-IR
#: reports them instead of guessing: a wrong nodeType would be a lie the front
#: end cannot detect.
LEGACY_NODE_KIND_NOTES = {
    "gateway": "旧写法 gateway 没有 §17 对应类型：并行分流用 ParallelFork，并行汇聚用 ParallelJoin。",
    "data": "旧写法 data 没有 §17 对应类型：数据对象用端口与 dataContract 表达。",
}

#: Legacy edge kinds and their §17 counterpart. "message" has none: it could
#: be an EventFlow or a DataFlow, and guessing would silently change the
#: meaning of the workflow.
LEGACY_EDGE_KIND_TYPES = {
    "sequence": "ControlFlow",
    "conditional": "ConditionalFlow",
    "association": "Dependency",
}

LEGACY_EDGE_KIND_NOTES = {
    "message": "旧写法 message 没有 §17 对应类型：按语义改写为 EventFlow 或 DataFlow。",
}


@lru_cache(maxsize=None)
def _vocab() -> dict:
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(VOCAB_DIR.glob("*.json"))
    }


@lru_cache(maxsize=None)
def _workflow_vocab() -> dict:
    return _vocab()["workflow-kinds"]


@lru_cache(maxsize=None)
def _case_vocab() -> dict:
    return _vocab()["case-categories"]


def _case_type_vocab() -> dict:
    return _vocab()["case-types"]


@lru_cache(maxsize=None)
def _diagram_vocab() -> dict:
    return _vocab()["diagram-types"]


def _as_array(value) -> list:
    return value if isinstance(value, list) else []


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) and not isinstance(value, bool) else {}


def _by_id(items) -> dict:
    return {item.get("id"): item for item in _as_array(items) if isinstance(item, dict) and item.get("id")}


# --- evidence index ---------------------------------------------------------

def evidence_index(model: dict) -> dict:
    """Every evidence record plus the reverse index of the entities citing it.

    §3 requires the evidence to be traceable from the node or the edge that
    depends on it; this file is the lookup table that makes that trace one read.
    """
    evidence = [item for item in _as_array(model.get("evidence")) if isinstance(item, dict)]
    cites: dict[str, list[str]] = {}
    for entity in _as_array(model.get("nodes")) + _as_array(model.get("edges")):
        if not isinstance(entity, dict):
            continue
        ident = entity.get("id")
        for ref in _as_array(entity.get("evidenceIds")):
            if isinstance(ref, str):
                cites.setdefault(ref, []).append(ident)

    by_level: dict[str, list[str]] = {}
    by_kind: dict[str, list[str]] = {}
    items = []
    for record in evidence:
        ident = record.get("id")
        level = record.get("level") or record.get("verification") or "Unknown"
        kind = record.get("kind")
        if isinstance(ident, str):
            by_level.setdefault(level, []).append(ident)
            if isinstance(kind, str):
                by_kind.setdefault(kind, []).append(ident)
        source = _as_dict(record.get("source"))
        items.append({
            "id": ident,
            "kind": kind,
            "level": level,
            "locator": source.get("locator"),
            "repository": source.get("repository"),
            "commit": source.get("commit"),
            "path": source.get("path") or source.get("file"),
            "startLine": source.get("startLine") if source.get("startLine") is not None else source.get("line"),
            "endLine": source.get("endLine"),
            "symbol": source.get("symbol"),
            "observedAt": record.get("observedAt"),
            "citedBy": sorted(cites.get(ident, [])),
        })
    return {
        "schemaVersion": DERIVED_SCHEMA_VERSION,
        "count": len(items),
        "groupedByLevel": by_level,
        "groupedByKind": by_kind,
        "items": items,
        "uncited": sorted(item["id"] for item in items if not item["citedBy"]),
        "note": "uncited 表示没有实体引用它；未删除，但界面不得把它当作某项事实的依据。",
    }


# --- diagram catalogue ------------------------------------------------------

def _has_attr_field(nodes, field, truthy=False) -> bool:
    for node in nodes:
        for attribute in _as_array(node.get("attributes")):
            if not isinstance(attribute, dict):
                continue
            value = attribute.get(field)
            if value is None:
                continue
            if truthy and not value:
                continue
            return True
    return False


def _has_method_field(nodes, field) -> bool:
    for node in nodes:
        for method in _as_array(node.get("methods")):
            if isinstance(method, dict) and method.get(field) is not None:
                return True
    return False


def _node_prop(nodes, key) -> bool:
    return any(isinstance(node.get("properties"), dict) and key in node["properties"] for node in nodes)


def _edge_prop(edges, key) -> bool:
    return any(isinstance(edge.get("properties"), dict) and key in edge["properties"] for edge in edges)


def _node_kind(nodes, kind) -> bool:
    return any(node.get("kind") == kind for node in nodes)


def _edge_type(edges, *types) -> bool:
    return any(edge.get("type") in types for edge in edges)


#: How to decide whether the model can supply one fidelity element of a diagram
#: type. A probe that cannot answer says "missing": the front end then shows
#: the gap instead of a fabricated diagram element.
_FIDELITY_PROBES = {
    "attributes": lambda ctx: _has_attr_field(ctx["nodes"], "name"),
    "attribute": lambda ctx: _has_attr_field(ctx["nodes"], "name"),
    "column": lambda ctx: _has_attr_field(ctx["nodes"], "name"),
    "methods": lambda ctx: _has_method_field(ctx["nodes"], "name"),
    "visibility": lambda ctx: (_has_attr_field(ctx["nodes"], "visibility")
                               or _has_method_field(ctx["nodes"], "visibility")),
    "multiplicity": lambda ctx: _edge_prop(ctx["edges"], "multiplicity"),
    "cardinality": lambda ctx: _edge_prop(ctx["edges"], "cardinality"),
    "primaryKey": lambda ctx: _has_attr_field(ctx["nodes"], "primaryKey", truthy=True),
    "foreignKey": lambda ctx: _has_attr_field(ctx["nodes"], "foreignKey"),
    "dataType": lambda ctx: _has_attr_field(ctx["nodes"], "type"),
    "ports": lambda ctx: any(_as_array(node.get("ports")) for node in ctx["nodes"]),
    "interfaces": lambda ctx: (_node_kind(ctx["nodes"], "Interface")
                               or _node_prop(ctx["nodes"], "interfaces")),
    "nesting": lambda ctx: any(node.get("parentId") for node in ctx["nodes"]),
    "lifeline": lambda ctx: _node_prop(ctx["nodes"], "lifeline"),
    "message": lambda ctx: (_edge_prop(ctx["edges"], "message")
                             or _edge_type(ctx["edges"], "sends", "receives", "calls", "publishes")),
    "activation": lambda ctx: _node_prop(ctx["nodes"], "activation"),
    "guard": lambda ctx: any(edge.get("condition") for edge in ctx["edges"]),
    "initial": lambda ctx: _node_prop(ctx["nodes"], "initial"),
    "final": lambda ctx: _node_prop(ctx["nodes"], "final"),
    "state": lambda ctx: (_node_kind(ctx["nodes"], "State")
                          or any(node.get("state") for node in ctx["nodes"])),
    "decision": lambda ctx: _node_kind(ctx["nodes"], "Decision"),
    "merge": lambda ctx: _node_prop(ctx["nodes"], "merge"),
    "fork": lambda ctx: _node_prop(ctx["nodes"], "fork"),
    "join": lambda ctx: _node_prop(ctx["nodes"], "join"),
    "node": lambda ctx: _node_prop(ctx["nodes"], "node"),
    "executionEnvironment": lambda ctx: _node_prop(ctx["nodes"], "executionEnvironment"),
    "artifact": lambda ctx: _node_prop(ctx["nodes"], "artifact"),
    "systemBoundary": lambda ctx: _node_prop(ctx["nodes"], "systemBoundary"),
    "include": lambda ctx: _edge_prop(ctx["edges"], "include"),
    "extend": lambda ctx: _edge_prop(ctx["edges"], "extend"),
    "externalEntity": lambda ctx: (_node_kind(ctx["nodes"], "Actor")
                                   or _node_prop(ctx["nodes"], "externalEntity")),
    "dataStore": lambda ctx: (_node_kind(ctx["nodes"], "Table")
                              or _node_kind(ctx["nodes"], "Entity")
                              or _node_prop(ctx["nodes"], "dataStore")),
    "process": lambda ctx: (_node_kind(ctx["nodes"], "Service")
                            or _node_prop(ctx["nodes"], "process")),
    "building": lambda ctx: bool(ctx["space"].get("buildings")),
    "floor": lambda ctx: bool(ctx["space"].get("floors")),
    "room": lambda ctx: bool(ctx["space"].get("rooms")),
    "trustBoundary": lambda ctx: any(edge.get("trustBoundary") is True for edge in ctx["edges"]),
    "principal": lambda ctx: _node_prop(ctx["nodes"], "principal"),
    "slo": lambda ctx: _node_prop(ctx["nodes"], "slo"),
    "healthCheck": lambda ctx: _node_prop(ctx["nodes"], "healthCheck"),
    "counterfactual": lambda ctx: bool(ctx["scenarios"]),
    "eventPayload": lambda ctx: _node_prop(ctx["nodes"], "payload"),
    "deliveryGuarantee": lambda ctx: _edge_prop(ctx["edges"], "deliveryGuarantee"),
    "ordering": lambda ctx: _edge_prop(ctx["edges"], "ordering"),
}


def _missing_fidelity(fidelity, context) -> list[str]:
    """The fidelity elements the model cannot supply for this view."""
    missing = []
    for token in fidelity or []:
        probe = _FIDELITY_PROBES.get(token)
        if probe is None:
            missing.append(token)  # unknown probe: never claim it is satisfied
            continue
        try:
            satisfied = bool(probe(context))
        except Exception:  # a probe must never break the derivation
            satisfied = False
        if not satisfied:
            missing.append(token)
    return missing


def diagrams(model: dict) -> dict:
    """The diagram catalogue: what each view shows and what it cannot show."""
    vocabulary = _diagram_vocab()
    specs = {item["id"]: item for item in vocabulary.get("types", [])}
    nodes_by_id = _by_id(model.get("nodes"))
    edges_by_id = _by_id(model.get("edges"))
    space = _as_dict(model.get("space"))
    scenarios = _as_array(model.get("scenarios"))

    catalog = []
    unregistered = []
    for view in _as_array(model.get("views")):
        if not isinstance(view, dict):
            continue
        diagram_type = view.get("diagramType")
        spec = specs.get(diagram_type)
        if spec is None:
            unregistered.append(view.get("id"))
            continue
        node_ids = [item for item in _as_array(view.get("nodeIds")) if isinstance(item, str)]
        edge_ids = [item for item in _as_array(view.get("edgeIds")) if isinstance(item, str)]
        selected_nodes = [nodes_by_id[item] for item in node_ids if item in nodes_by_id]
        selected_edges = [edges_by_id[item] for item in edge_ids if item in edges_by_id]
        if not selected_edges:
            allowed = set(spec.get("edgeKinds") or [])
            selected_edges = [edge for edge in _as_array(model.get("edges"))
                              if isinstance(edge, dict) and edge.get("type") in allowed]
        context = {"nodes": selected_nodes, "edges": selected_edges,
                   "space": space, "scenarios": scenarios}
        fidelity = list(spec.get("fidelity") or [])
        missing = _missing_fidelity(fidelity, context)
        catalog.append({
            "id": view.get("id"),
            "title": view.get("title"),
            "diagramType": diagram_type,
            "label": spec.get("label"),
            "dimension": view.get("dimension") or spec.get("dimension"),
            "notation": spec.get("notation"),
            "allowedNodeKinds": list(spec.get("nodeKinds") or []),
            "allowedEdgeKinds": list(spec.get("edgeKinds") or []),
            "fidelity": fidelity,
            "missing": missing,
            "nodeIds": node_ids,
            "edgeIds": [edge.get("id") for edge in selected_edges],
            "edgeKinds": [kind for kind in _as_array(view.get("edgeKinds")) if isinstance(kind, str)],
            "stakeholders": [item for item in _as_array(view.get("stakeholders")) if isinstance(item, str)],
            "concerns": [item for item in _as_array(view.get("concerns")) if isinstance(item, str)],
            "scenarioId": view.get("scenarioId"),
            "evidenceIds": sorted({ref for node in selected_nodes
                                   for ref in _as_array(node.get("evidenceIds"))
                                   if isinstance(ref, str)}),
        })
    return {
        "schemaVersion": DERIVED_SCHEMA_VERSION,
        "count": len(catalog),
        "catalog": catalog,
        "unregistered": unregistered,
        "families": vocabulary.get("specFamilies", {}),
        "note": "missing 列出该图示类型要求、但模型无法提供的保真元素；界面必须标注为缺失，不得伪造。",
    }


# --- spatial layout ---------------------------------------------------------

def spatial_layout(model: dict) -> dict:
    """Building-Floor-Room plus the node placement the model actually supports."""
    space = _as_dict(model.get("space"))
    buildings = [item for item in _as_array(space.get("buildings")) if isinstance(item, dict)]
    floors = [item for item in _as_array(space.get("floors")) if isinstance(item, dict)]
    rooms = [item for item in _as_array(space.get("rooms")) if isinstance(item, dict)]
    nodes_by_id = _by_id(model.get("nodes"))

    placements = []
    unplaced = []
    for node in _as_array(model.get("nodes")):
        if not isinstance(node, dict):
            continue
        ref = _as_dict(node.get("spaceRef"))
        if not ref:
            unplaced.append(node.get("id"))
            continue
        placements.append({
            "nodeId": node.get("id"),
            "buildingId": ref.get("buildingId"),
            "floorId": ref.get("floorId"),
            "roomId": ref.get("roomId"),
        })

    cross_space = []
    for edge in _as_array(model.get("edges")):
        if not isinstance(edge, dict):
            continue
        span = _as_dict(edge.get("spaceSpan"))
        if span.get("kind"):
            cross_space.append({
                "edgeId": edge.get("id"),
                "kind": span.get("kind"),
                "sourceId": edge.get("sourceId"),
                "targetId": edge.get("targetId"),
            })

    missing = []
    if not buildings:
        missing.append("building")
    if not floors:
        missing.append("floor")
    if not rooms:
        missing.append("room")
    return {
        "schemaVersion": DERIVED_SCHEMA_VERSION,
        "present": bool(space),
        "buildings": [{"id": item.get("id"), "name": item.get("name"),
                       "semanticSource": item.get("semanticSource"),
                       "floorIds": [floor.get("id") for floor in floors
                                    if floor.get("buildingId") == item.get("id")]}
                      for item in buildings],
        "floors": [{"id": item.get("id"), "name": item.get("name"),
                    "buildingId": item.get("buildingId"),
                    "semanticSource": item.get("semanticSource"),
                    "roomIds": [room.get("id") for room in rooms
                                if room.get("floorId") == item.get("id")]}
                   for item in floors],
        "rooms": [{"id": item.get("id"), "name": item.get("name"),
                   "floorId": item.get("floorId"),
                   "semanticSource": item.get("semanticSource"),
                   "nodeIds": [entry["nodeId"] for entry in placements
                               if entry.get("roomId") == item.get("id")]}
                  for item in rooms],
        "placements": placements,
        "unplacedNodeIds": sorted(item for item in unplaced if isinstance(item, str)),
        "crossSpaceEdges": cross_space,
        "missing": missing,
        "note": ("模型没有空间结构时 present=false：界面必须显示未建立空间语义，"
                 "不得虚构楼层或房间。"),
    }


# --- workflow IR ------------------------------------------------------------

def _node_type_map() -> dict:
    mapping = {item["id"]: item.get("specType") for item in _workflow_vocab().get("nodeKinds", [])}
    # "subprocess" is the old spelling of "subflow": one counterpart, no guess.
    mapping["subprocess"] = mapping.get("subflow")
    return mapping


def _edge_type_map() -> dict:
    mapping = {item["id"]: item.get("specType") for item in _workflow_vocab().get("edgeKinds", [])}
    mapping.update(LEGACY_EDGE_KIND_TYPES)
    return mapping


def workflow_ir(model: dict) -> dict:
    """The versioned GWF-IR of every workflow, in the §17 field names."""
    node_types = _node_type_map()
    edge_types = _edge_type_map()
    workflows = []
    for workflow in _as_array(model.get("workflows")):
        if not isinstance(workflow, dict):
            continue
        nodes = []
        for node in _as_array(workflow.get("nodes")):
            if not isinstance(node, dict):
                continue
            kind = node.get("kind")
            spec_type = node_types.get(kind)
            ports = []
            for port in _as_array(node.get("ports")):
                if not isinstance(port, dict):
                    continue
                ports.append({
                    "portId": port.get("id"),
                    "kind": port.get("kind"),
                    "direction": port.get("direction"),
                    "schema": port.get("schema") or port.get("schemaRef"),
                    "cardinality": port.get("cardinality"),
                    "required": port.get("required"),
                    "acceptedTypes": [item for item in _as_array(port.get("acceptedTypes"))
                                      if isinstance(item, str)],
                    "name": port.get("name"),
                })
            design_only = spec_type in DESIGN_ONLY_NODE_TYPES
            implementation = node.get("implementationRef")
            legacy_note = LEGACY_NODE_KIND_NOTES.get(kind)
            nodes.append({
                "nodeId": node.get("id"),
                "nodeType": spec_type,
                "name": node.get("name"),
                "description": node.get("description"),
                "inputs": _as_array(node.get("inputs")),
                "outputs": _as_array(node.get("outputs")),
                "ports": ports,
                "preconditions": [item for item in _as_array(node.get("preconditions"))
                                  if isinstance(item, str)],
                "postconditions": [item for item in _as_array(node.get("postconditions"))
                                   if isinstance(item, str)],
                "stateTransitions": _as_array(node.get("stateTransitions")),
                "timeoutPolicy": node.get("timeoutPolicy"),
                "retryPolicy": node.get("retryPolicy"),
                "errorHandlers": _as_array(node.get("errorHandlers")),
                "sideEffects": [item for item in _as_array(node.get("sideEffects"))
                                if isinstance(item, str)],
                "idempotency": node.get("idempotency"),
                "implementationRef": implementation,
                "evidenceIds": [item for item in _as_array(node.get("evidenceIds"))
                                if isinstance(item, str)],
                "verification": node.get("verification"),
                "canonicalNodeId": node.get("canonicalNodeId"),
                "designOnly": design_only,
                "executable": bool(implementation) and not design_only,
                **({"legacyKind": kind, "note": legacy_note} if legacy_note else {}),
            })
        edges = []
        for edge in _as_array(workflow.get("edges")):
            if not isinstance(edge, dict):
                continue
            kind = edge.get("kind")
            legacy_note = LEGACY_EDGE_KIND_NOTES.get(kind)
            edges.append({
                "edgeId": edge.get("id"),
                "sourceNodeId": edge.get("sourceId"),
                "sourcePortId": edge.get("sourcePortId"),
                "targetNodeId": edge.get("targetId"),
                "targetPortId": edge.get("targetPortId"),
                "edgeType": edge_types.get(kind),
                "direction": "source-to-target" if edge.get("directed", True) else "both",
                "guard": edge.get("condition"),
                "dataMapping": edge.get("dataMapping"),
                "priority": edge.get("priority"),
                "errorRoute": edge.get("errorRoute"),
                "verification": edge.get("verification"),
                "evidenceIds": [item for item in _as_array(edge.get("evidenceIds"))
                                if isinstance(item, str)],
                **({"legacyKind": kind, "note": legacy_note} if legacy_note else {}),
            })
        entry_points = [item for item in _as_array(workflow.get("entryPoints"))
                        if isinstance(item, str)]
        if not entry_points:
            entry_points = [node.get("id") for node in nodes if node.get("nodeType") == "Start"]
        missing = []
        if not workflow.get("version"):
            missing.append("version")
        if not entry_points:
            missing.append("entryPoints")
        if not _as_array(workflow.get("dataSchemas")) and not any(node["ports"] for node in nodes):
            missing.append("dataSchemas")
        issues = []
        for node in nodes:
            if node.get("nodeType") is None:
                issues.append({"target": node.get("nodeId"), "kind": node.get("legacyKind"),
                               "message": node.get("note", "未登记的 §17 节点类型")})
        for edge in edges:
            if edge.get("edgeType") is None:
                issues.append({"target": edge.get("edgeId"), "kind": edge.get("legacyKind"),
                               "message": edge.get("note", "未登记的 §17 边类型")})
        workflows.append({
            "workflowId": workflow.get("id"),
            "name": workflow.get("name"),
            "version": workflow.get("version"),
            "entryPoints": entry_points,
            "nodes": nodes,
            "edges": edges,
            "dataSchemas": _as_array(workflow.get("dataSchemas")),
            "executionPolicy": workflow.get("executionPolicy"),
            "errorPolicy": workflow.get("errorPolicy"),
            "permissions": [item for item in _as_array(workflow.get("permissions"))
                            if isinstance(item, str)],
            "evidenceIds": [item for item in _as_array(workflow.get("evidenceIds"))
                            if isinstance(item, str)],
            "metadata": _as_dict(workflow.get("metadata")),
            "missing": missing,
            "issues": issues,
            "gwfIrValid": not issues and not missing,
            "designOnlyNodeIds": [node["nodeId"] for node in nodes if node["designOnly"]],
        })
    return {
        "schemaVersion": DERIVED_SCHEMA_VERSION,
        "gwfVersion": _workflow_vocab().get("gwfIr", {}).get("version", DERIVED_SCHEMA_VERSION),
        "count": len(workflows),
        "workflows": workflows,
        "note": ("designOnly 的节点没有真实执行器；executable=false 表示未声明实现引用。"
                 "missing 列出 GWF-IR 必填但未给出的字段。"),
    }


def _ir_property(kind: str, extra: dict | None = None) -> dict:
    """One compact property definition.

    The schema stays a complete JSON Schema but carries no per-property prose:
    the field catalogue next to it says what each field means, and the prose
    would double the size of a file that every model has to ship.
    """
    schema: dict = {"type": kind}
    if extra:
        schema.update(extra)
    return schema


def workflow_schema() -> dict:
    """The machine-readable GWF-IR catalogue plus a JSON Schema for it.

    The schema is generated from the §17 field catalogue, so the file a front
    end validates against can never drift from the vocabulary the MCP uses.
    """
    gwf = _workflow_vocab().get("gwfIr", {})
    node_types = list(gwf.get("nodeTypes") or [])
    edge_types = list(gwf.get("edgeTypes") or [])
    string_array = {"type": "array", "items": {"type": "string"}}
    object_array = {"type": "array", "items": {"type": "object"}}
    nullable_object = {"type": ["object", "null"]}
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://gemarc.dev/gwf-ir/{gwf.get('version', DERIVED_SCHEMA_VERSION)}/workflow-ir.schema.json",
        "title": "GWF-IR",
        "type": "object",
        "required": ["workflowId", "name", "nodes", "edges"],
        "additionalProperties": True,
        "properties": {
            "workflowId": _ir_property("string"),
            "name": _ir_property("string", {"minLength": 1}),
            "version": _ir_property(["string", "null"]),
            "entryPoints": string_array,
            "nodes": {"type": "array", "items": {"$ref": "#/$defs/node"}},
            "edges": {"type": "array", "items": {"$ref": "#/$defs/edge"}},
            "dataSchemas": object_array,
            "executionPolicy": nullable_object,
            "errorPolicy": nullable_object,
            "permissions": string_array,
            "evidenceIds": string_array,
            "metadata": {"type": "object"},
        },
        "$defs": {
            "node": {
                "type": "object",
                "required": ["nodeId", "nodeType", "name"],
                "additionalProperties": True,
                "properties": {
                    "nodeId": _ir_property("string"),
                    "nodeType": _ir_property("string", {"enum": node_types}),
                    "name": _ir_property("string"),
                    "description": _ir_property(["string", "null"]),
                    "inputs": object_array,
                    "outputs": object_array,
                    "ports": {"type": "array", "items": {"$ref": "#/$defs/port"}},
                    "preconditions": string_array,
                    "postconditions": string_array,
                    "stateTransitions": object_array,
                    "timeoutPolicy": nullable_object,
                    "retryPolicy": nullable_object,
                    "errorHandlers": object_array,
                    "sideEffects": string_array,
                    "idempotency": _ir_property(["string", "null"]),
                    "implementationRef": _ir_property(["string", "null"]),
                    "evidenceIds": string_array,
                    "verification": _ir_property(["string", "null"]),
                    "designOnly": _ir_property("boolean"),
                    "executable": _ir_property("boolean"),
                },
            },
            "edge": {
                "type": "object",
                "required": ["edgeId", "sourceNodeId", "targetNodeId", "edgeType"],
                "additionalProperties": True,
                "properties": {
                    "edgeId": _ir_property("string"),
                    "sourceNodeId": _ir_property("string"),
                    "sourcePortId": _ir_property(["string", "null"]),
                    "targetNodeId": _ir_property("string"),
                    "targetPortId": _ir_property(["string", "null"]),
                    "edgeType": _ir_property("string", {"enum": edge_types}),
                    "direction": _ir_property("string", {"enum": ["source-to-target", "both"]}),
                    "guard": _ir_property(["string", "null"]),
                    "dataMapping": nullable_object,
                    "priority": _ir_property(["number", "null"]),
                    "errorRoute": _ir_property(["string", "null"]),
                    "verification": _ir_property(["string", "null"]),
                    "evidenceIds": string_array,
                },
            },
            "port": {
                "type": "object",
                "required": ["portId", "kind"],
                "additionalProperties": True,
                "properties": {
                    "portId": _ir_property("string"),
                    "kind": _ir_property("string", {"enum": ["input", "output", "control", "error"]}),
                    "direction": _ir_property(["string", "null"], {"enum": ["in", "out", "inout", None]}),
                    "schema": nullable_object,
                    "cardinality": _ir_property(["string", "null"],
                                                {"enum": ["1", "0..1", "0..N", "1..N", "N", None]}),
                    "required": _ir_property("boolean"),
                    "acceptedTypes": string_array,
                    "name": _ir_property(["string", "null"]),
                },
            },
        },
    }
    return {
        "schemaVersion": DERIVED_SCHEMA_VERSION,
        "gwfVersion": gwf.get("version", DERIVED_SCHEMA_VERSION),
        "title": gwf.get("title", "GWF-IR"),
        "workflowFields": list(gwf.get("workflowFields") or []),
        "nodeFields": list(gwf.get("nodeFields") or []),
        "edgeFields": list(gwf.get("edgeFields") or []),
        "portFields": list(gwf.get("portFields") or []),
        "nodeTypes": node_types,
        "edgeTypes": edge_types,
        "designOnlyNodeTypes": list(gwf.get("designOnlyNodeTypes") or []),
        "validity": list(gwf.get("validity") or []),
        "jsonSchema": schema,
        "note": "GWF-IR 是规范化中间格式；第三方画布快照不得当作唯一的工作流真相。",
    }


# --- case library -----------------------------------------------------------

def case_library(model: dict) -> dict:
    """The 5D case library in the §13 case contract shape."""
    vocabulary = _case_vocab()
    types_vocabulary = _case_type_vocab()
    labels = {item["id"]: item.get("label") for item in vocabulary.get("categories", [])}
    spec_types = {item["id"]: item.get("specType") for item in vocabulary.get("categories", [])}
    type_labels = {item["id"]: item.get("label") for item in types_vocabulary.get("types", [])}
    type_primitives = {item["id"]: list(item.get("primitives") or [])
                       for item in types_vocabulary.get("types", [])}
    scenarios = {item.get("id"): item for item in _as_array(model.get("scenarios"))
                 if isinstance(item, dict)}

    cases = []
    counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    for case in _as_array(model.get("cases")):
        if not isinstance(case, dict):
            continue
        category = case.get("category")
        case_type = case.get("caseType")
        if isinstance(category, str):
            counts[category] = counts.get(category, 0) + 1
        if isinstance(case_type, str):
            type_counts[case_type] = type_counts.get(case_type, 0) + 1
        cases.append({
            "caseId": case.get("id"),
            "name": case.get("title"),
            "description": case.get("description"),
            "category": category,
            "categoryLabel": labels.get(category),
            "specType": spec_types.get(category),
            "caseType": case_type,
            "caseTypeLabel": type_labels.get(case_type),
            "animationPrimitives": type_primitives.get(case_type, []),
            "reality": case.get("reality"),
            "scenarioId": case.get("scenarioId"),
            "initialState": case.get("initialState"),
            "inputEvents": _as_array(case.get("inputEvents")),
            "preconditions": [item for item in _as_array(case.get("preconditions"))
                              if isinstance(item, str)],
            "executionRules": _as_array(case.get("executionRules")),
            "expectedOutcomes": _as_array(case.get("expectedOutcomes")),
            "terminationConditions": [item for item in _as_array(case.get("terminationConditions"))
                                      if isinstance(item, str)],
            "assertions": [item for item in _as_array(case.get("assertions"))
                           if isinstance(item, str)],
            "evidenceIds": [item for item in _as_array(case.get("evidenceIds"))
                            if isinstance(item, str)],
            "assumptions": [item for item in _as_array(case.get("assumptions"))
                            if isinstance(item, str)],
            "disabledNodeIds": [item for item in _as_array(case.get("disabledNodeIds"))
                                if isinstance(item, str)],
            "expected": case.get("expected"),
            "evidenceClass": case.get("evidenceClass"),
        })

    categories = []
    for item in vocabulary.get("categories", []):
        categories.append({
            "id": item.get("id"),
            "label": item.get("label"),
            "specType": item.get("specType"),
            "count": counts.get(item.get("id"), 0),
            "requiresIntervention": item.get("requiresIntervention"),
        })

    case_types = []
    for item in types_vocabulary.get("types", []):
        case_types.append({
            "id": item.get("id"),
            "label": item.get("label"),
            "category": item.get("category"),
            "count": type_counts.get(item.get("id"), 0),
            "requiresIntervention": item.get("requiresIntervention"),
            "animationPrimitives": list(item.get("primitives") or []),
        })

    by_scenario: dict[str, int] = {}
    for case in cases:
        key = case.get("scenarioId")
        if isinstance(key, str):
            by_scenario[key] = by_scenario.get(key, 0) + 1

    missing_interventions = []
    for case in cases:
        category = next((item for item in vocabulary.get("categories", [])
                         if item.get("id") == case.get("category")), None)
        if category and category.get("requiresIntervention") and not case["disabledNodeIds"]:
            missing_interventions.append(case.get("caseId"))

    return {
        "schemaVersion": DERIVED_SCHEMA_VERSION,
        "count": len(cases),
        "categories": categories,
        "caseTypes": case_types,
        "cases": cases,
        "scenarios": [{"scenarioId": ident, "name": item.get("name"),
                       "evidenceClass": item.get("evidenceClass"),
                       "eventCount": len(_as_array(item.get("events"))),
                       "ruleCount": len(_as_array(item.get("rules")))}
                      for ident, item in scenarios.items()],
        "coverage": {
            "byScenario": by_scenario,
            "scenariosWithoutCase": sorted(ident for ident in scenarios if ident not in by_scenario),
            "missingInterventions": missing_interventions,
        },
        "caseContract": list(vocabulary.get("specCaseContract", {}).get("fields") or []),
        "realityValues": list(types_vocabulary.get("reality", {}).get("values") or []),
        "note": ("缺证据的 Case 不得被描述为真实运行事实；missingInterventions 列出需要干预"
                 "却没有给出 disabledNodeIds 的 Case。animationPrimitives 只是动画语法提示，"
                 "不是该 Case 实际发生过的轨迹。"),
    }


# --- the whole set ----------------------------------------------------------

def derived_files(model: dict) -> dict:
    """Every model-dependent derived file, keyed by its path under ``data/``.

    ``data/workflow-schema.json`` is deliberately not here: it depends on the
    vocabulary, not on the model, so it belongs to the fixed site kit.
    """
    return {
        "data/evidence-index.json": evidence_index(model),
        "data/diagram-catalog.json": diagrams(model),
        "data/spatial-layout.json": spatial_layout(model),
        "data/workflow-ir.json": workflow_ir(model),
        "data/case-library.json": case_library(model),
    }
