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
#:
#: The authoritative list lives in the vocabulary
#: (``workflow-kinds.json`` -> ``gwfIr.designOnlyNodeTypes``); this tuple is
#: only the fallback for a vocabulary that does not declare one. A hard-coded
#: copy drifted once already: the 6D types were added to the vocabulary and
#: the GWF-IR still reported them as executable.
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
    # Every entity that can cite evidence counts: nodes, edges, workflow nodes
    # and edges, and cases. Leaving the last two out made the file claim that
    # evidence used by a workflow or a case was "not cited by anything".
    citing_entities = list(_as_array(model.get("nodes"))) + list(_as_array(model.get("edges")))
    for workflow in _as_array(model.get("workflows")):
        if not isinstance(workflow, dict):
            continue
        citing_entities.extend(_as_array(workflow.get("nodes")))
        citing_entities.extend(_as_array(workflow.get("edges")))
    citing_entities.extend(_as_array(model.get("cases")))
    for entity in citing_entities:
        if not isinstance(entity, dict):
            continue
        ident = entity.get("id")
        if not isinstance(ident, str):
            continue
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
        if not isinstance(level, str):
            level = "Unknown"
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
            "citedBy": sorted(cites.get(ident, [])) if isinstance(ident, str) else [],
        })
    return {
        "schemaVersion": DERIVED_SCHEMA_VERSION,
        "count": len(items),
        "groupedByLevel": by_level,
        "groupedByKind": by_kind,
        "items": items,
        "uncited": sorted(item["id"] for item in items
                          if isinstance(item["id"], str) and not item["citedBy"]),
        "note": "uncited 表示没有实体引用它；未删除，但界面不得把它当作某项事实的依据。",
    }


# --- diagram catalogue ------------------------------------------------------


#: How to decide whether the model can supply one fidelity element of a diagram
#: type. This table is the **mirror** of ``FIDELITY_RULES`` in
#: ``runtime/view/view-router.js``: same tokens, same scope, same paths, same
#: predicates. The browser runtime is the authority (it is what actually draws
#: the diagram); this table only predicts it, so a mismatch would make the
#: catalogue say "present" while the site says "missing".
#:
#: A rule reads a dotted path on the node or the edge (``paths``), or uses a
#: predicate for the cases that need to look inside a collection (``check``).
#: ``scope`` says which side of the selection is inspected.
def _attr_check(field, truthy=False):
    """A predicate over the node's attributes.

    The predicate is called as ``check(entity, context)``, like every other
    rule in the table, so it takes the context as an optional second
    argument. A one-argument predicate raised ``TypeError`` here, the call
    site swallowed it, and the rule silently answered "not satisfied" for
    every node - the catalogue then claimed a view was missing a primary key
    the site was about to draw.
    """
    def check(node, _context=None):
        for item in _as_array(node.get("attributes")):
            if not isinstance(item, dict):
                continue
            value = item.get(field)
            if value is None:
                continue
            if truthy and not value:
                continue
            return True
        return False
    return check


def _method_check(field):
    """A predicate over the node's methods."""
    def check(node, _context=None):
        for item in _as_array(node.get("methods")):
            if isinstance(item, dict) and item.get(field) is not None:
                return True
        return False
    return check


def _any_item_check(field):
    """A predicate over the node's attributes *and* methods."""
    def check(node, _context=None):
        items = list(_as_array(node.get("attributes"))) + list(_as_array(node.get("methods")))
        return any(isinstance(item, dict) and item.get(field) for item in items)
    return check


_FIDELITY_RULES = {
    "attributes": {"scope": "node", "paths": ["attributes"]},
    "attribute": {"scope": "node", "paths": ["attributes"]},
    "methods": {"scope": "node", "paths": ["methods"]},
    "visibility": {"scope": "node", "check": _any_item_check("visibility")},
    "multiplicity": {"scope": "edge", "paths": ["multiplicity", "properties.multiplicity"]},
    "cardinality": {"scope": "edge", "paths": ["cardinality", "properties.cardinality"]},
    "primaryKey": {"scope": "node", "check": _attr_check("primaryKey", truthy=True)},
    "foreignKey": {"scope": "node", "check": _attr_check("foreignKey", truthy=True)},
    "ports": {"scope": "node", "paths": ["ports", "properties.ports"]},
    "interfaces": {"scope": "node", "paths": ["interfaces", "properties.interfaces"]},
    "nesting": {"scope": "node", "check": lambda node, ctx: node.get("parentId") in ctx["nodeIds"]},
    "lifeline": {"scope": "node", "paths": ["lifeline", "properties.lifeline"]},
    "message": {"scope": "edge", "paths": ["message", "properties.message"]},
    "activation": {"scope": "edge", "paths": ["activation", "properties.activation"]},
    "guard": {"scope": "edge", "paths": ["guard", "properties.guard"],
              "check": lambda edge, ctx: bool(edge.get("condition"))},
    "initial": {"scope": "node", "paths": ["initial", "properties.initial"]},
    "final": {"scope": "node", "paths": ["final", "properties.final"]},
    "state": {"scope": "node", "paths": ["state", "properties.state"]},
    "decision": {"scope": "node", "paths": ["decision", "properties.decision"]},
    "merge": {"scope": "node", "paths": ["merge", "properties.merge"]},
    "fork": {"scope": "node", "paths": ["fork", "properties.fork"]},
    "join": {"scope": "node", "paths": ["join", "properties.join"]},
    "systemBoundary": {"scope": "node", "paths": ["systemBoundary", "properties.systemBoundary"]},
    "include": {"scope": "edge", "paths": ["include", "properties.include"]},
    "extend": {"scope": "edge", "paths": ["extend", "properties.extend"]},
    "externalEntity": {"scope": "node", "paths": ["externalEntity", "properties.externalEntity"]},
    "dataStore": {"scope": "node", "paths": ["dataStore", "properties.dataStore"]},
    "process": {"scope": "node", "paths": ["process", "properties.process"]},
    "node": {"scope": "node", "paths": ["node", "properties.node"]},
    "executionEnvironment": {"scope": "node", "paths": ["executionEnvironment", "properties.executionEnvironment"]},
    "artifact": {"scope": "node", "paths": ["artifact", "properties.artifact"]},
    "building": {"scope": "node", "paths": ["spaceRef.buildingId"]},
    "floor": {"scope": "node", "paths": ["spaceRef.floorId"]},
    "room": {"scope": "node", "paths": ["spaceRef.roomId"]},
    "trustBoundary": {"scope": "edge",
                      "check": lambda edge, ctx: edge.get("trustBoundary") is not None},
    "principal": {"scope": "node", "paths": ["principal", "properties.principal"]},
    "slo": {"scope": "node", "paths": ["slo", "properties.slo"]},
    "healthCheck": {"scope": "node", "paths": ["healthCheck", "properties.healthCheck"]},
    "counterfactual": {"scope": "node", "paths": ["counterfactual", "properties.counterfactual"]},
    "column": {"scope": "node", "check": _attr_check("name")},
    "dataType": {"scope": "node", "check": _attr_check("type")},
    "eventPayload": {"scope": "both", "paths": ["payload", "properties.payload"]},
    "deliveryGuarantee": {"scope": "edge", "paths": ["deliveryGuarantee", "properties.deliveryGuarantee"]},
    "ordering": {"scope": "edge", "paths": ["ordering", "properties.ordering"]},
}

#: Backwards-compatible alias: the rules *are* the probes.
_FIDELITY_PROBES = _FIDELITY_RULES


def _read_model_path(entity, path):
    """Read a dotted path from a plain object (``None`` when absent)."""
    cursor = entity
    for part in path.split("."):
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(part)
        if cursor is None:
            return None
    return cursor


def _satisfies_fidelity(entity, rule) -> bool:
    """Whether one entity carries what the rule asks for.

    Mirrors ``satisfiesFidelity`` in the runtime: ``undefined``/``null``/``false``
    and empty collections do not count; anything else does.
    """
    for path in rule.get("paths") or []:
        value = _read_model_path(entity, path)
        if value is None or value is False:
            continue
        if isinstance(value, (list, tuple, dict, set)):
            if len(value) == 0:
                continue
            return True
        return True
    return False


def _check_fidelity(token, context) -> bool:
    """Whether the selection satisfies one fidelity element.

    Mirrors ``checkFidelity`` in ``runtime/view/view-router.js``: the predicate
    is tried first, then the paths; nodes are inspected before edges.
    """
    rule = _FIDELITY_RULES.get(token)
    if rule is None:
        return False
    check = rule.get("check")
    scope = rule.get("scope", "both")

    def matches(entity) -> bool:
        if check is not None:
            try:
                if check(entity, context):
                    return True
            except Exception:  # a predicate must never break the derivation
                pass
        return _satisfies_fidelity(entity, rule)

    if scope in ("node", "both"):
        for node in context.get("nodes") or []:
            if isinstance(node, dict) and matches(node):
                return True
    if scope in ("edge", "both"):
        for edge in context.get("edges") or []:
            if isinstance(edge, dict) and matches(edge):
                return True
    return False


def _missing_fidelity(fidelity, context) -> list[str]:
    """The fidelity elements the model cannot supply for this view."""
    missing = []
    for token in fidelity or []:
        if not _check_fidelity(token, context):
            missing.append(token)  # unknown token or unsatisfied: never claim it is present
    return missing


def _resolve_view_selection(view, spec, model, nodes_by_id, edges_by_id):
    """Resolve one view into a selection, with the runtime's rules.

    This is the Python mirror of ``ViewRouter.resolve`` in
    ``runtime/view/view-router.js``. The rules, in order:

    1. a node is kept only if the diagram type allows its kind;
    2. without explicit ``edgeIds`` the candidates are the edges whose *both*
       endpoints are in the view's node set - not every edge of the type in the
       whole model;
    3. an edge is kept only if the diagram type allows its kind, the view's
       ``edgeKinds`` does not exclude it, and both endpoints are in the view.

    Keeping this identical to the runtime matters: the catalogue tells the front
    end what the diagram will contain, and a second, looser rule would make it
    list edges the site refuses to draw.
    """
    requested_node_ids = [item for item in _as_array(view.get("nodeIds")) if isinstance(item, str)]
    requested_edge_ids = [item for item in _as_array(view.get("edgeIds")) if isinstance(item, str)]
    view_edge_kinds = {item for item in _as_array(view.get("edgeKinds")) if isinstance(item, str)}
    allowed_node_kinds = set(spec.get("nodeKinds") or [])
    allowed_edge_kinds = set(spec.get("edgeKinds") or [])

    node_ids = []
    for ident in requested_node_ids:
        node = nodes_by_id.get(ident)
        if node is None:
            continue  # the view references a node that does not exist
        if node.get("kind") not in allowed_node_kinds:
            continue  # the diagram type does not allow this kind of node
        node_ids.append(ident)
    node_set = set(node_ids)

    candidates = []
    for ident in requested_edge_ids:
        edge = edges_by_id.get(ident)
        if edge is not None:
            candidates.append(edge)
    if not requested_edge_ids:
        candidates = [edge for edge in _as_array(model.get("edges"))
                      if isinstance(edge, dict)
                      and edge.get("sourceId") in node_set and edge.get("targetId") in node_set]

    selected_edges = []
    for edge in candidates:
        if edge.get("type") not in allowed_edge_kinds:
            continue
        if view_edge_kinds and edge.get("type") not in view_edge_kinds:
            continue
        if edge.get("sourceId") not in node_set or edge.get("targetId") not in node_set:
            continue
        selected_edges.append(edge)

    selected_nodes = [nodes_by_id[ident] for ident in node_ids]
    return node_ids, selected_nodes, selected_edges


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
    undeclared = []
    for view in _as_array(model.get("views")):
        if not isinstance(view, dict):
            continue
        diagram_type = view.get("diagramType")
        if not diagram_type:
            # A view without a diagram type is not an *unregistered* type: it
            # simply never declared one. The two cases must not share a bucket.
            undeclared.append(view.get("id"))
            continue
        spec = specs.get(diagram_type)
        if spec is None:
            unregistered.append(view.get("id"))
            continue
        node_ids, selected_nodes, selected_edges = _resolve_view_selection(
            view, spec, model, nodes_by_id, edges_by_id)
        context = {"nodes": selected_nodes, "edges": selected_edges,
                   "nodeIds": node_ids, "space": space, "scenarios": scenarios}
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
        "undeclared": undeclared,
        "families": vocabulary.get("specFamilies", {}),
        "note": ("missing 列出该图示类型要求、但模型无法提供的保真元素；界面必须标注为缺失，不得伪造。"
                 "nodeIds / edgeIds 是按运行时同一套规则解析出来的实际选择。"
                 "unregistered 是声明了未登记的图示类型；undeclared 是没有声明图示类型。"),
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


def _node_kind_meta() -> dict:
    """The canonical id -> the vocabulary entry, so executorClass comes from one place."""
    return {item["id"]: item for item in _workflow_vocab().get("nodeKinds", [])}


def _design_only_types() -> tuple:
    """The §17 node types that have no real executor.

    Read from the vocabulary so the GWF-IR cannot drift away from it: a type
    the vocabulary calls Design-only must not be reported as executable.
    """
    declared = _workflow_vocab().get("gwfIr", {}).get("designOnlyNodeTypes")
    if isinstance(declared, list) and declared:
        return tuple(str(item) for item in declared)
    return DESIGN_ONLY_NODE_TYPES


#: Node kinds that only exist in the 6D execution graph (v6.0 §17 / §25).
_EXECUTION_NODE_KINDS = frozenset({
    "deterministic-program", "agent-loop", "tool-call", "policy-gate",
    "checkpoint", "resume", "manual-review", "escalation", "audit-event",
})
#: Edge kinds that only exist in the 6D execution graph.
_EXECUTION_EDGE_KINDS = frozenset({
    "state-transition", "approval-transition", "tool-result",
    "retry-transition", "timeout-transition", "resume-transition",
})
#: Workflow-level fields only the 6D contract declares.
_EXECUTION_WORKFLOW_FIELDS = (
    "executionGraph", "stateSchemaVersion", "checkpointPolicy",
    "approvalPolicies", "executionCapabilities", "runSecurityPolicy",
)
#: Node-level fields only the 6D contract declares.
_EXECUTION_NODE_FIELDS = (
    "executorKind", "authorityScope", "executionMode", "inputStateSchema",
    "outputStateSchema", "stateWritePolicy", "checkpointBefore", "checkpointAfter",
    "budgetPolicy", "iterationLimit", "approvalPolicyRef", "toolContractRef",
)
#: Edge-level fields only the 6D contract declares.
_EXECUTION_EDGE_FIELDS = (
    "stateSchemaRef", "stateProjection", "stateMergePolicy", "routingExpression",
    "deliverySemantics", "resumeBehavior", "policyRef",
)


def _declares_execution(workflow: dict) -> bool:
    """Whether this workflow claims to be an execution graph rather than a canvas.

    The 6D gates only apply to execution graphs. A design-only workflow (no
    6D field, no 6D node or edge kind) is not one, and rejecting it would
    break every model that predates the 6D contract.
    """
    if any(workflow.get(field) is not None for field in _EXECUTION_WORKFLOW_FIELDS):
        return True
    for node in _as_array(workflow.get("nodes")):
        if not isinstance(node, dict):
            continue
        if node.get("kind") in _EXECUTION_NODE_KINDS:
            return True
        if any(node.get(field) is not None for field in _EXECUTION_NODE_FIELDS):
            return True
    for edge in _as_array(workflow.get("edges")):
        if not isinstance(edge, dict):
            continue
        if edge.get("kind") in _EXECUTION_EDGE_KINDS:
            return True
        if any(edge.get(field) is not None for field in _EXECUTION_EDGE_FIELDS):
            return True
    return False


def _execution_gates(workflow: dict) -> list[dict]:
    """The v6.0 ``quality_gates_6d`` verdict for one workflow.

    One entry per hit: ``{"gate", "targets", "message"}``. An empty list means
    the workflow declares an execution graph and passes every gate - or that it
    is not an execution graph at all (see :func:`_declares_execution`).
    """
    if not isinstance(workflow, dict) or not _declares_execution(workflow):
        return []
    nodes = [item for item in _as_array(workflow.get("nodes")) if isinstance(item, dict)]
    edges = [item for item in _as_array(workflow.get("edges")) if isinstance(item, dict)]
    state_version = workflow.get("stateSchemaVersion")
    hits: list[dict] = []

    def hit(gate, targets, message):
        hits.append({"gate": gate, "targets": sorted(targets), "message": message})

    unbounded = [str(node.get("id")) for node in nodes
                 if node.get("kind") == "agent-loop" and not node.get("iterationLimit")]
    if unbounded:
        hit("unbounded_agent_loop", unbounded,
            "Agent 循环节点必须声明 iterationLimit；没有上限就是无界循环。")

    bypassed = [str(node.get("id")) for node in nodes
                if node.get("kind") in ("human-approval", "manual-review")
                and not node.get("approvalPolicyRef")]
    if bypassed:
        hit("human_approval_bypass", bypassed,
            "人工审批节点必须引用 approvalPolicyRef；没有策略就没有可审计的审批边界。")

    uses_state = [str(node.get("id")) for node in nodes
                  if node.get("inputStateSchema") or node.get("outputStateSchema")]
    uses_state += [str(edge.get("id")) for edge in edges if edge.get("stateSchemaRef")]
    if uses_state and not state_version:
        hit("unvalidated_state_schema", uses_state,
            "工作流传递状态却没有 stateSchemaVersion：状态不可校验，也不可恢复。")

    unguarded = [str(edge.get("id")) for edge in edges
                 if edge.get("policyRef")
                 and not (edge.get("condition") or edge.get("routingExpression"))]
    if unguarded:
        hit("missing_route_guard", unguarded,
            "带策略的边必须声明可判断的路由条件；否则路由无法被静态校验。")

    replayable = [str(node.get("id")) for node in nodes
                  if _as_array(node.get("sideEffects"))
                  and not (node.get("idempotency") or node.get("idempotencyKeyStrategy"))]
    if replayable:
        hit("unsafe_side_effect_replay", replayable,
            "有副作用的节点必须声明幂等要求；否则重放可能重复产生外部效果。")

    checkpoints = [str(node.get("id")) for node in nodes
                   if node.get("kind") == "checkpoint"
                   or node.get("checkpointBefore") or node.get("checkpointAfter")]
    if checkpoints and not state_version:
        hit("checkpoint_without_state_integrity", checkpoints,
            "检查点没有 stateSchemaVersion：恢复时无法校验状态完整性。")

    fake = [str(node.get("id")) for node in nodes
            if node.get("executionMode") == "real"
            and not (node.get("authorityScope") and (node.get("implementationRef")
                                                      or node.get("executorRef")))]
    if fake:
        hit("fake_production_execution", fake,
            "声明真实执行的节点必须给出权限范围与执行器引用；否则不得声称会真实执行。")

    untraceable = [str(node.get("id")) for node in nodes
                   if node.get("kind") == "policy-gate" and not node.get("policyRef")]
    untraceable += [str(node.get("id")) for node in nodes
                    if node.get("kind") == "decision"
                    and not node.get("policyRef")
                    and not _as_array(node.get("evidenceIds"))]
    if untraceable:
        hit("untraceable_decision", untraceable,
            "策略闸门必须引用 policyRef；判定节点必须能追溯到策略或证据。")

    hits.extend(_wbs_gates(workflow))
    return hits


#: The WBS levels of the 6D execution graph: 阶段 → 工作包 → 任务.
WORK_PACKAGE_KINDS = ("phase", "work-package", "task")


def _work_packages(workflow: dict) -> list[dict]:
    """The normalised WBS packages of one workflow (empty when it declares none).

    The WBS layer belongs to the execution graph: the work graph says *how* a
    run is wired, the work breakdown says *what* work that run performs, what
    each level delivers and when it counts as done.
    """
    graph = workflow.get("executionGraph")
    if not isinstance(graph, dict):
        return []
    packages = []
    for item in _as_array(graph.get("workPackages")):
        if not isinstance(item, dict):
            continue
        packages.append({
            "packageId": item.get("packageId"),
            "parentPackageId": item.get("parentPackageId"),
            "kind": item.get("kind"),
            "title": item.get("title"),
            "deliverable": item.get("deliverable"),
            "completionCriteria": item.get("completionCriteria"),
            "nodeIds": [value for value in _as_array(item.get("nodeIds")) if isinstance(value, str)],
            "dependsOn": [value for value in _as_array(item.get("dependsOn")) if isinstance(value, str)],
            "evidenceIds": [value for value in _as_array(item.get("evidenceIds")) if isinstance(value, str)],
            "verification": item.get("verification"),
        })
    return packages


def _execution_nodes(workflow: dict) -> list[dict]:
    """Every node that plays an execution role in this workflow.

    The model layer keeps the execution nodes inside ``workflows[].nodes`` (the
    canvas the site draws); the standalone contract keeps its own
    ``executionGraph.nodes``. Both are nodes that carry a capability, and both
    are valid targets of a work package.
    """
    nodes = []
    for node in _as_array(workflow.get("nodes")):
        if not isinstance(node, dict):
            continue
        if node.get("kind") in _EXECUTION_NODE_KINDS or any(
                node.get(field) is not None for field in _EXECUTION_NODE_FIELDS):
            nodes.append(node)
    graph = workflow.get("executionGraph")
    if isinstance(graph, dict):
        for node in _as_array(graph.get("nodes")):
            if isinstance(node, dict):
                nodes.append(node)
    return nodes


def _node_identity(node: dict) -> str:
    """The id of an execution node, whichever spelling it uses."""
    for key in ("id", "nodeId"):
        value = node.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _wbs_gates(workflow: dict) -> list[dict]:
    """The WBS gates of one workflow.

    They only fire when the workflow declares a work breakdown: a workflow
    without one is a plain 5D canvas and stays untouched.
    """
    packages = _work_packages(workflow)
    if not packages:
        return []
    execution_nodes = _execution_nodes(workflow)
    known_nodes = {_node_identity(node) for node in execution_nodes}
    known_nodes.discard("")
    hits: list[dict] = []

    def hit(gate, targets, message):
        hits.append({"gate": gate, "targets": sorted(targets), "message": message})

    ids = [str(package.get("packageId") or "") for package in packages]
    known_packages = set(ids)
    broken = []
    if len(set(ids)) != len(ids):
        broken.extend(sorted({value for value in ids if ids.count(value) > 1}))
    roots = [package for package in packages if not package.get("parentPackageId")]
    if len(roots) != 1:
        # More than one root, or none at all: the tree is not a tree.
        broken.extend(str(package.get("packageId") or "") for package in (roots or packages))
    for package in packages:
        parent = package.get("parentPackageId")
        if parent and parent not in known_packages:
            broken.append(str(package.get("packageId") or ""))
    for package in packages:
        seen = set()
        current = package
        while current is not None:
            current_id = str(current.get("packageId") or "")
            if current_id in seen:
                broken.append(str(package.get("packageId") or ""))
                break
            seen.add(current_id)
            parent = current.get("parentPackageId")
            current = next((item for item in packages if item.get("packageId") == parent), None) if parent else None
    dangling = []
    for package in packages:
        for node_id in package.get("nodeIds") or []:
            if node_id not in known_nodes:
                dangling.append(str(package.get("packageId") or ""))
    if broken or dangling:
        hit("wbs_broken_hierarchy",
            set(broken) | set(dangling),
            "工作包树必须恰好有一个根、父包必须存在且不得成环，工作包只能引用本图存在的执行节点。")

    incomplete = [str(package.get("packageId") or "") for package in packages
                  if not package.get("deliverable") or not package.get("completionCriteria")]
    if incomplete:
        hit("wbs_missing_deliverable", incomplete,
            "每个工作包必须声明 deliverable（交付物）与 completionCriteria（完成条件）；没有这两项就不构成 WBS。")

    empty = [str(package.get("packageId") or "") for package in packages
             if not package.get("nodeIds")
             and not any(child.get("parentPackageId") == package.get("packageId") for child in packages)]
    if empty:
        hit("wbs_unbound_package", empty,
            "叶子工作包必须至少归属一个执行节点；否则分解与执行各说各话。有子包的层级节点只做汇总，不要求自己挂节点。")

    assigned = set()
    for package in packages:
        for node_id in package.get("nodeIds") or []:
            if node_id in known_nodes:
                assigned.add(node_id)
    orphans = [identity for identity in sorted(known_nodes) if identity not in assigned]
    if orphans:
        hit("wbs_orphan_node", orphans,
            "执行节点必须归属至少一个工作包；没有归属的节点不在任何工作分解里。")

    return hits


def work_breakdown(workflow: dict) -> dict:
    """The WBS layer of one workflow: the packages, their rollup and the coverage.

    ``coverage`` is the number that says whether the two halves of the 6D
    contract actually meet: how many execution nodes a work package accounts
    for.
    """
    packages = _work_packages(workflow)
    execution_nodes = _execution_nodes(workflow)
    node_ids = {_node_identity(node) for node in execution_nodes}
    node_ids.discard("")
    children: dict[str, list[str]] = {}
    for package in packages:
        parent = package.get("parentPackageId")
        if parent:
            children.setdefault(str(parent), []).append(str(package.get("packageId") or ""))
    assigned: set[str] = set()
    for package in packages:
        for node_id in package.get("nodeIds") or []:
            if node_id in node_ids:
                assigned.add(node_id)
    roots = [package for package in packages if not package.get("parentPackageId")]
    kinds: dict[str, int] = {}
    rows = []
    for package in packages:
        package_id = str(package.get("packageId") or "")
        kind = str(package.get("kind") or "")
        kinds[kind] = kinds.get(kind, 0) + 1
        own_nodes = [node_id for node_id in package.get("nodeIds") or [] if node_id in node_ids]
        rows.append({
            "packageId": package_id,
            "parentPackageId": package.get("parentPackageId"),
            "kind": kind,
            "title": package.get("title"),
            "deliverable": package.get("deliverable"),
            "completionCriteria": package.get("completionCriteria"),
            "nodeIds": own_nodes,
            "childPackageIds": sorted(children.get(package_id, [])),
            "dependsOn": list(package.get("dependsOn") or []),
            "nodeCount": len(own_nodes),
            "evidenceIds": list(package.get("evidenceIds") or []),
            "verification": package.get("verification"),
        })
    return {
        "declared": bool(packages),
        "rootPackageId": str(roots[0].get("packageId") or "") if len(roots) == 1 else None,
        "packageCount": len(packages),
        "nodeCount": len(node_ids),
        "assignedNodeCount": len(assigned),
        "coverageRatio": (round(len(assigned) / len(node_ids), 4) if node_ids else None),
        "kinds": kinds,
        "packages": rows,
    }


def execution_gates(model: dict) -> dict:
    """The 6D quality-gate verdict of every workflow in the model."""
    workflows = [item for item in _as_array(model.get("workflows")) if isinstance(item, dict)]
    per_workflow = {str(item.get("id")): _execution_gates(item) for item in workflows}
    rejected = sorted(workflow_id for workflow_id, hits in per_workflow.items() if hits)
    return {
        "schemaVersion": DERIVED_SCHEMA_VERSION,
        "rejectOn": list(_workflow_vocab().get("qualityGates", {}).get("rejectOn") or []),
        "byWorkflow": per_workflow,
        "rejectedWorkflowIds": rejected,
        "note": ("命中的门表示该执行图不得放行。门只对声明了执行图的工作流生效："
                 "纯设计画布不是执行图，不受 6D 门禁约束。"),
    }


def workflow_ir(model: dict) -> dict:
    """The versioned GWF-IR of every workflow, in the §17 field names."""
    node_types = _node_type_map()
    edge_types = _edge_type_map()
    node_meta = _node_kind_meta()
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
            node_kind = node_meta.get(kind) or {}
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
            design_only = spec_type in _design_only_types()
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
                # --- v6.0 §17 GWF-IR 6D: the execution contract of the node ---
                "executorKind": node.get("executorKind") or node_kind.get("executorClass"),
                "authorityScope": node.get("authorityScope"),
                "executionMode": node.get("executionMode"),
                "inputStateSchema": node.get("inputStateSchema"),
                "outputStateSchema": node.get("outputStateSchema"),
                "stateWritePolicy": node.get("stateWritePolicy"),
                "checkpointBefore": node.get("checkpointBefore"),
                "checkpointAfter": node.get("checkpointAfter"),
                "budgetPolicy": node.get("budgetPolicy"),
                "iterationLimit": node.get("iterationLimit"),
                "approvalPolicyRef": node.get("approvalPolicyRef"),
                "toolContractRef": node.get("toolContractRef"),
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
                "direction": (edge.get("direction")
                              or ("source-to-target" if edge.get("directed", True) else "both")),
                "guard": edge.get("condition"),
                "dataMapping": edge.get("dataMapping"),
                "priority": edge.get("priority"),
                "errorRoute": edge.get("errorRoute"),
                "verification": edge.get("verification"),
                "evidenceIds": [item for item in _as_array(edge.get("evidenceIds"))
                                if isinstance(item, str)],
                # --- v6.0 §17 GWF-IR 6D: routing, state projection and constraints ---
                "stateSchemaRef": edge.get("stateSchemaRef"),
                "stateProjection": edge.get("stateProjection"),
                "stateMergePolicy": edge.get("stateMergePolicy"),
                "routingExpression": edge.get("routingExpression"),
                "deliverySemantics": edge.get("deliverySemantics"),
                "resumeBehavior": edge.get("resumeBehavior"),
                "policyRef": edge.get("policyRef"),
                **({"legacyKind": kind, "note": legacy_note} if legacy_note else {}),
            })
        entry_points = [item for item in _as_array(workflow.get("entryPoints"))
                        if isinstance(item, str)]
        if not entry_points:
            # Fall back to the Start node. The derived nodes carry "nodeId",
            # not "id": reading "id" here produced a list of nulls, which then
            # looked like a filled entry-point list and silenced the missing
            # signal below.
            entry_points = [node.get("nodeId") for node in nodes
                            if node.get("nodeType") == "Start" and node.get("nodeId")]
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
        breakdown = work_breakdown(workflow)
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
            # --- v6.0 §17 GWF-IR 6D: the execution contract of the workflow ---
            "executionGraph": workflow.get("executionGraph"),
            "stateSchemaVersion": workflow.get("stateSchemaVersion"),
            "checkpointPolicy": workflow.get("checkpointPolicy"),
            "approvalPolicies": _as_array(workflow.get("approvalPolicies")),
            "executionCapabilities": [item for item in _as_array(workflow.get("executionCapabilities"))
                                      if isinstance(item, str)],
            "runSecurityPolicy": workflow.get("runSecurityPolicy"),
            # --- 6D = WBS × 执行图: what work the graph performs, and how much of it is bound ---
            # Only a workflow that declares a breakdown carries the layer. An empty
            # block on every workflow would tax the inline payload of every model
            # that does not use it - the increment has to stay an increment.
            **({"workPackages": breakdown["packages"],
                "wbs": {key: value for key, value in breakdown.items() if key != "packages"}}
               if breakdown["declared"] else {}),
            "missing": missing,
            "issues": issues,
            "gates": _execution_gates(workflow),
            "gwfIrValid": not issues and not missing,
            "designOnlyNodeIds": [node["nodeId"] for node in nodes if node["designOnly"]],
        })
    return {
        "schemaVersion": DERIVED_SCHEMA_VERSION,
        "gwfVersion": _workflow_vocab().get("gwfIr", {}).get("version", DERIVED_SCHEMA_VERSION),
        "count": len(workflows),
        "workflows": workflows,
        "gates": execution_gates(model),
        "note": ("designOnly 的节点没有真实执行器；executable=false 表示未声明实现引用。"
                 "missing 列出 GWF-IR 必填但未给出的字段。"
                 "gates 是 v6.0 quality_gates_6d 的判定结果：命中的门表示该执行图不得放行。"),
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
            # --- v6.0 §17 GWF-IR 6D ---
            "executionGraph": nullable_object,
            "stateSchemaVersion": _ir_property(["string", "null"]),
            "checkpointPolicy": nullable_object,
            "approvalPolicies": object_array,
            "executionCapabilities": string_array,
            "runSecurityPolicy": nullable_object,
            # --- 6D = WBS × 执行图 ---
            "workPackages": {"type": "array", "items": {"$ref": "#/$defs/workPackage"}},
            "wbs": nullable_object,
        },
        "$defs": {
            "workPackage": {
                "type": "object",
                "required": ["packageId", "kind"],
                "additionalProperties": True,
                "properties": {
                    "packageId": _ir_property("string"),
                    "parentPackageId": _ir_property(["string", "null"]),
                    "kind": _ir_property("string", {"enum": list(WORK_PACKAGE_KINDS)}),
                    "title": _ir_property(["string", "null"]),
                    "deliverable": _ir_property(["string", "null"]),
                    "completionCriteria": _ir_property(["string", "null"]),
                    "nodeIds": string_array,
                    "childPackageIds": string_array,
                    "dependsOn": string_array,
                    "nodeCount": _ir_property("integer"),
                    "evidenceIds": string_array,
                    "verification": _ir_property(["string", "null"]),
                },
            },
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
                    "executorKind": _ir_property(["string", "null"]),
                    "authorityScope": _ir_property(["string", "null"]),
                    "executionMode": _ir_property(["string", "null"]),
                    "inputStateSchema": _ir_property(["string", "null"]),
                    "outputStateSchema": _ir_property(["string", "null"]),
                    "stateWritePolicy": _ir_property(["string", "null"]),
                    "checkpointBefore": _ir_property(["boolean", "null"]),
                    "checkpointAfter": _ir_property(["boolean", "null"]),
                    "budgetPolicy": nullable_object,
                    "iterationLimit": _ir_property(["integer", "null"]),
                    "approvalPolicyRef": _ir_property(["string", "null"]),
                    "toolContractRef": _ir_property(["string", "null"]),
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
                    "stateSchemaRef": _ir_property(["string", "null"]),
                    "stateProjection": nullable_object,
                    "stateMergePolicy": _ir_property(["string", "null"]),
                    "routingExpression": _ir_property(["string", "null"]),
                    "deliverySemantics": _ir_property(["string", "null"]),
                    "resumeBehavior": _ir_property(["string", "null"]),
                    "policyRef": _ir_property(["string", "null"]),
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


# --- 6D execution graph contract (v6.0 §25) ---------------------------------

def execution_graph_schema() -> dict:
    """The 6D execution graph IR contract.

    A contract file, not a derivation: it depends on the vocabulary and the
    specification, not on the model, so it travels inside the fixed site kit.
    """
    path = CONTRACTS_DIR / "execution-graph.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def execution_graph_example() -> dict:
    """A compliant example of the 6D execution graph.

    It is **example data**: every evidence list is empty and every
    verification level is Hypothetical. The example must never be presented as
    a fact about a target project.
    """
    path = CONTRACTS_DIR / "execution-graph.example.json"
    return json.loads(path.read_text(encoding="utf-8"))


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
            "caseId": case.get("id") or case.get("caseId"),
            "name": case.get("title") or case.get("name"),
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
    from . import three_d

    return {
        "data/evidence-index.json": evidence_index(model),
        "data/diagram-catalog.json": diagrams(model),
        "data/spatial-layout.json": spatial_layout(model),
        "data/workflow-ir.json": workflow_ir(model),
        "data/case-library.json": case_library(model),
        "data/three-d.json": three_d.three_d_dataset(model),
    }
