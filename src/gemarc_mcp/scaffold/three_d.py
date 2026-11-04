"""
Derive the semantic-axis 3D dataset from one canonical model.

A Three.js architecture view needs three axes, each bound to a *categorical*
field of the model. Deciding which field is which axis, reading that field off
every node, and mapping the model's twenty entity kinds onto the viewer's
fourteen kinds is bookkeeping - not invention. It is done here, in the fixed
part, so the language model only ever fills facts.

Two products come out of the same derivation:

``data/three-d.json``     the dataset the site shell renders: axes, the value
                          each node takes on each axis, the edges inside the
                          view, and what the model could not supply.
``ipa_architecture()``    the same facts in the shape the standalone Three.js
                          architecture builder (ipa contract) consumes, so a
                          caller can hand the contract to that generator
                          without writing a single line of mapping code.

The rules that hold everywhere:

1. A categorical field that the model does not carry is reported as
   *missing*, never replaced by a guess.
2. An axis whose values do not add information (every node shares one value)
   is reported as *not independent*; the caller decides whether to fold it.
3. An entity or relation type that is not in the mapping table is listed
   under *unmapped*. It is never silently folded into a neighbouring type.
4. Nothing here writes a file - the caller decides where the bytes go.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

SCAFFOLD_ROOT = Path(__file__).resolve().parent
VOCAB_DIR = SCAFFOLD_ROOT / "vocab"

#: Schema version of the derived 3D dataset.
THREE_D_SCHEMA_VERSION = "0.3.0"

#: The three axes a semantic 3D view must declare.
AXIS_KEYS = ("x", "y", "z")

#: The node kinds the standalone Three.js builder accepts.
VIEWER_NODE_KINDS = frozenset({
    "actor", "system", "service", "process", "module", "runtime", "database",
    "external", "gateway", "plugin", "security", "operations", "data", "storage",
})

#: The relation kinds the standalone Three.js builder accepts.
VIEWER_EDGE_KINDS = frozenset({
    "calls", "uses", "hosts", "deploys", "contains", "publishes", "reads",
    "writes", "authenticates", "monitors", "controls", "extends", "depends",
    "replicates", "recovers", "exchanges",
})

#: The categorical fields the standalone Three.js builder can bind to an axis.
VIEWER_AXES = frozenset({
    "boundary", "layer", "repo", "runtime", "environment", "dataZone",
    "trustZone", "opsLayer", "integrationLayer", "extensionLayer",
    "failureDomain", "domain", "owner",
})

#: The model's evidence levels mapped onto the viewer's three evidence states.
#: Observed means it was seen running, so it is the only level that may be
#: called *confirmed*. The other two are assumptions of a different strength,
#: and a hypothesis is never more than an illustration.
EVIDENCE_STATE_MAP = {
    "Observed": "confirmed",
    "ImplementedModel": "assumed",
    "DesignedModel": "assumed",
    "Hypothetical": "illustrative",
    "confirmed": "confirmed",
    "assumed": "assumed",
    "illustrative": "illustrative",
}


@lru_cache(maxsize=None)
def _contract() -> dict:
    """The mapping table between the model and the Three.js view."""
    return json.loads((VOCAB_DIR / "three-d-contract.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _node_kind_map() -> dict:
    return {item["mcp"]: item["threeD"] for item in _contract().get("nodeKinds", [])}


@lru_cache(maxsize=None)
def _edge_kind_map() -> dict:
    return {item["mcp"]: item["threeD"] for item in _contract().get("edgeKinds", [])}


@lru_cache(maxsize=None)
def _axis_source_map() -> dict:
    """``properties.layer`` -> ``layer``; ``domainId`` -> ``domain``.

    A bare field name that is itself an axis (``layer``) maps to itself: the
    model may keep the axis on the node itself instead of inside the free
    ``properties`` map, and both spellings must bind to the same axis.
    """
    mapping: dict[str, str] = {}
    for item in _contract().get("axisSources", []):
        path = item.get("path")
        axis = item.get("axis")
        if isinstance(path, str) and isinstance(axis, str):
            mapping[path] = axis
    for axis in VIEWER_AXES:
        mapping.setdefault(axis, axis)
    return mapping


def _as_array(value) -> list:
    return value if isinstance(value, list) else []


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _by_id(items) -> dict:
    result = {}
    for item in _as_array(items):
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            result[item["id"]] = item
    return result


def read_axis_value(node: dict, field: str):
    """Read a categorical field off a node.

    Returns ``(value, reason)``. The value is a string, or ``None`` with a
    reason that says why the node cannot be placed on this axis. A field that
    is missing, empty or not a category is never turned into a value.
    """
    if not isinstance(field, str) or not field.strip():
        return None, "轴没有声明字段"
    cursor: object = node
    for part in field.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return None, f"节点没有 {field}"
        cursor = cursor[part]
    if cursor is None or cursor == "":
        return None, f"节点的 {field} 没有取值"
    if isinstance(cursor, bool) or isinstance(cursor, (dict, list)):
        return None, f"节点的 {field} 不是类别值"
    return str(cursor), None


def _axis_values(nodes: list[dict], field: str) -> tuple[list[str], list[str]]:
    """The distinct values on one axis and the nodes that carry none.

    The order is deterministic: the order the values appear in the node list,
    with any value the view declares first.
    """
    values: list[str] = []
    unmapped: list[str] = []
    for node in nodes:
        value, _reason = read_axis_value(node, field)
        if value is None:
            if isinstance(node.get("id"), str):
                unmapped.append(node["id"])
            continue
        if value not in values:
            values.append(value)
    return values, unmapped


def _ordered_values(declared, present: list[str]) -> list[str]:
    """Declared order first, then the remaining values in sorted order."""
    ordered: list[str] = []
    for value in _as_array(declared):
        if isinstance(value, str) and value in present and value not in ordered:
            ordered.append(value)
    for value in sorted(present):
        if value not in ordered:
            ordered.append(value)
    return ordered


def _axis_entry(view_axis: dict, field: str, values: list[str], unmapped: list[str]) -> dict:
    """One axis of the derived dataset."""
    label = view_axis.get("label")
    source = _axis_source_map().get(field)
    return {
        "field": field,
        "label": label if isinstance(label, str) and label else None,
        "values": values,
        "valueCount": len(values),
        "unmappedNodeIds": unmapped,
        # The normalized field name of the standalone builder, or null when the
        # model uses a field that builder cannot bind.
        "source": source if source in VIEWER_AXES else None,
        "declaredValues": [v for v in _as_array(view_axis.get("values")) if isinstance(v, str)],
    }


def _gains(node_values: list[dict]) -> dict:
    """How much each axis adds to the number of distinguishable combinations.

    Only the entities that actually carry a value are counted: an entity
    without a value on an axis does not create a new combination, it only
    means that axis says nothing about that entity. An axis that never
    changes the combination count carries no information: the standalone
    builder folds such an axis away, and so must this derivation report it.
    """
    def combinations(keys):
        seen = set()
        for entry in node_values:
            values = entry.get("values", {})
            if any(values.get(key) is None for key in keys):
                continue
            seen.add(tuple(values.get(key) for key in keys))
        return len(seen)

    x_count = combinations(("x",))
    xy_count = combinations(("x", "y"))
    xyz_count = combinations(("x", "y", "z"))
    return {
        "x": x_count - 1,
        "y": xy_count - x_count,
        "z": xyz_count - xy_count,
    }


def _view_nodes(view: dict, nodes_by_id: dict) -> list[dict]:
    """The nodes a view selects. An empty selection means the whole model."""
    selected = [nid for nid in _as_array(view.get("nodeIds")) if isinstance(nid, str)]
    if not selected:
        return list(nodes_by_id.values())
    return [nodes_by_id[nid] for nid in selected if nid in nodes_by_id]


def three_d_dataset(model: dict) -> dict:
    """Derive the semantic-axis 3D dataset from the canonical model."""
    contract = _contract()
    node_kinds = _node_kind_map()
    edge_kinds = _edge_kind_map()
    nodes_by_id = _by_id(model.get("nodes"))
    edges = [e for e in _as_array(model.get("edges")) if isinstance(e, dict)]

    views: list[dict] = []
    skipped: list[dict] = []
    unmapped_kinds: dict[str, list[str]] = {}

    for view in _as_array(model.get("views")):
        if not isinstance(view, dict):
            continue
        axes = _as_dict(view.get("axes"))
        declared = [key for key in AXIS_KEYS if isinstance(axes.get(key), dict)
                    and isinstance(axes[key].get("field"), str) and axes[key]["field"].strip()]
        if not declared:
            continue
        if len(declared) != len(AXIS_KEYS):
            skipped.append({
                "viewId": view.get("id"),
                "reason": f"视图只声明了 {'/'.join(declared)} 三轴中的部分轴",
                "missingAxes": [key for key in AXIS_KEYS if key not in declared],
            })
            continue

        members = _view_nodes(view, nodes_by_id)
        member_ids = {node.get("id") for node in members}

        axis_entries: dict[str, dict] = {}
        for key in AXIS_KEYS:
            field = axes[key]["field"]
            values, unmapped = _axis_values(members, field)
            axis_entries[key] = _axis_entry(axes[key], field,
                                            _ordered_values(axes[key].get("values"), values), unmapped)

        node_entries: list[dict] = []
        node_values: list[dict] = []
        for node in members:
            node_id = node.get("id")
            values: dict[str, str | None] = {}
            unmapped_axes: list[str] = []
            for key in AXIS_KEYS:
                value, _reason = read_axis_value(node, axis_entries[key]["field"])
                values[key] = value
                if value is None:
                    unmapped_axes.append(key)
            mcp_kind = node.get("kind") or node.get("type")
            three_d_kind = node_kinds.get(mcp_kind)
            if three_d_kind is None:
                unmapped_kinds.setdefault(str(mcp_kind), []).append(str(node_id))
            entry = {
                "id": node_id,
                "name": node.get("name") or node.get("label") or node_id,
                "mcpKind": mcp_kind,
                "threeDKind": three_d_kind,
                "values": values,
                "unmappedAxes": unmapped_axes,
                "domain": node.get("domainId"),
                "role": node.get("role"),
                "verification": node.get("verification"),
                "evidenceIds": [eid for eid in _as_array(node.get("evidenceIds")) if isinstance(eid, str)],
            }
            node_entries.append(entry)
            node_values.append(entry)

        view_edges = []
        for edge in edges:
            source = edge.get("sourceId") or edge.get("source")
            target = edge.get("targetId") or edge.get("target")
            if source not in member_ids or target not in member_ids:
                continue
            mcp_type = edge.get("type") or edge.get("kind")
            view_edges.append({
                "id": edge.get("id"),
                "sourceId": source,
                "targetId": target,
                "mcpType": mcp_type,
                "threeDType": edge_kinds.get(mcp_type),
                "description": edge.get("description"),
            })

        gains = _gains(node_values)
        views.append({
            "viewId": view.get("id"),
            "title": view.get("title"),
            "dimension": view.get("dimension"),
            "axes": axis_entries,
            "gains": gains,
            "independent": {key: gains[key] > 0 for key in AXIS_KEYS},
            "nodes": node_entries,
            "edges": view_edges,
            "unmappedKinds": sorted({str(entry["mcpKind"]) for entry in node_entries
                                     if entry["threeDKind"] is None}),
        })

    missing: list[str] = []
    if not views:
        missing.append("view")
    return {
        "schemaVersion": THREE_D_SCHEMA_VERSION,
        "present": bool(views),
        "views": views,
        "skippedViews": skipped,
        "unmappedKinds": [{"mcp": kind, "nodeIds": sorted(ids)}
                          for kind, ids in sorted(unmapped_kinds.items())],
        "missing": missing,
        "rules": list(contract.get("rules", [])),
        "note": ("三轴字段是类别而不是度量：派生结果只登记模型里真实存在的取值。"
                 "视图没有声明三轴时 present=false，界面必须说明未建立轴语义，"
                 "不得用任意坐标伪造三维结构。"),
    }


def _ipa_axis(field: str) -> str | None:
    """The normalized axis field name, or None when it cannot be bound."""
    source = _axis_source_map().get(field)
    return source if source in VIEWER_AXES else None


def ipa_architecture(model: dict) -> dict:
    """The same facts in the shape the standalone Three.js builder consumes.

    Every field the builder requires is either filled from the model or
    reported in ``missing``. Nothing is invented to satisfy the builder: a
    contract that says "missing" is more useful than one that lies.
    """
    meta = _as_dict(model.get("meta"))
    node_kinds = _node_kind_map()
    edge_kinds = _edge_kind_map()
    nodes_by_id = _by_id(model.get("nodes"))

    evidence_state = EVIDENCE_STATE_MAP.get(str(meta.get("evidenceState") or ""), "illustrative")
    missing: list[str] = []

    ipa_nodes: list[dict] = []
    for node in nodes_by_id.values():
        mcp_kind = node.get("kind") or node.get("type")
        three_d_kind = node_kinds.get(mcp_kind)
        if three_d_kind is None:
            missing.append(f"实体类型 {mcp_kind} 没有登记到三维类型")
            continue
        evidence_ids = [eid for eid in _as_array(node.get("evidenceIds")) if isinstance(eid, str)]
        evidence = ",".join(evidence_ids) if evidence_ids else str(node.get("evidence") or "")
        entry = {
            "id": node.get("id"),
            "name": node.get("name") or node.get("label") or node.get("id"),
            "kind": three_d_kind,
            "verification": EVIDENCE_STATE_MAP.get(str(node.get("verification") or ""), "illustrative"),
            "evidence": evidence,
        }
        properties = _as_dict(node.get("properties"))
        for axis in sorted(VIEWER_AXES):
            value, _reason = read_axis_value(node, f"properties.{axis}")
            if value is None:
                value, _reason = read_axis_value(node, axis)
            if value is not None:
                entry[axis] = value
        if node.get("domainId") and "domain" not in entry:
            entry["domain"] = str(node["domainId"])
        if "description" not in entry:
            entry["description"] = entry["name"]
        ipa_nodes.append(entry)

    ipa_edges: list[dict] = []
    for edge in _as_array(model.get("edges")):
        if not isinstance(edge, dict):
            continue
        mcp_type = edge.get("type") or edge.get("kind")
        three_d_type = edge_kinds.get(mcp_type)
        source = edge.get("sourceId") or edge.get("source")
        target = edge.get("targetId") or edge.get("target")
        if three_d_type is None:
            missing.append(f"关系类型 {mcp_type} 没有登记到三维类型")
            continue
        if source not in nodes_by_id or target not in nodes_by_id:
            continue
        ipa_edges.append({
            "id": edge.get("id"),
            "source": source,
            "target": target,
            "kind": three_d_type,
            "description": edge.get("description") or f"{mcp_type}",
        })

    ipa_views: list[dict] = []
    for view in _as_array(model.get("views")):
        if not isinstance(view, dict):
            continue
        axes = _as_dict(view.get("axes"))
        fields = {}
        bindable = True
        for key in AXIS_KEYS:
            entry = _as_dict(axes.get(key))
            field = entry.get("field")
            normalized = _ipa_axis(field) if isinstance(field, str) else None
            if normalized is None:
                bindable = False
                missing.append(f"视图 {view.get('id')} 的 {key} 轴字段 {field} 不是可绑定的类别")
                break
            fields[key] = normalized
        if not bindable:
            continue
        selected = [nid for nid in _as_array(view.get("nodeIds")) if isinstance(nid, str)]
        ipa_views.append({
            "id": view.get("id"),
            "title": view.get("title"),
            "description": view.get("description"),
            "nodeIds": selected,
            "edgeKinds": sorted({edge_kinds.get(edge.get("type") or edge.get("kind"))
                                 for edge in _as_array(model.get("edges"))
                                 if isinstance(edge, dict)
                                 and (edge.get("type") or edge.get("kind")) in edge_kinds}),
            "dimensions": {key: {"field": fields[key]} for key in AXIS_KEYS},
        })

    owner = _as_dict(meta.get("source")).get("owner")
    if not owner:
        missing.append("meta 没有责任方（owner）")
    return {
        "meta": {
            "system": meta.get("system"),
            "version": meta.get("version") or meta.get("schemaVersion"),
            "owner": owner,
            "description": meta.get("description"),
            "evidenceState": evidence_state,
            "language": meta.get("language") or "zh-CN",
        },
        "nodes": ipa_nodes,
        "edges": ipa_edges,
        "views": ipa_views,
        "missing": missing,
        "note": ("这是由规范模型派生出的三维契约：字段全部来自模型本身，"
                 "没有登记的映射与缺失项都写在 missing 里，供审阅者逐条核对。"),
    }
