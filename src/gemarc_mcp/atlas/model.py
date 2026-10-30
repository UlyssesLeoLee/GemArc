"""The expanded atlas model ``gemarc-atlas/1``: schema constants, id grammar, derivation helpers, canonical JSON.

This module is the single home of every derivation rule (ports, edge fields, evidence kind and level,
default verification, machine states, default policy, ...). The synthetic generator and the expander both
call the ``build_*`` helpers, so a derived field can never be computed in two places.
The normative description is ``docs/atlas/spec/model-schema.md``. Standard library only, no file I/O.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from . import vocab

SCHEMA = "gemarc-atlas/1"

#: Top-level model keys, in documentation order (``schema`` comes first, then these).
COLLECTIONS = (
    "meta", "space", "nodes", "edges", "ports", "evidences", "machines", "cases", "interventions",
    "requirements", "tests", "tbds", "policies",
)
SPACE_PARTS = ("buildings", "floors", "rooms")

# --------------------------------------------------------------------------------------------------------
# Id grammar
# --------------------------------------------------------------------------------------------------------

#: Identical to ``gemarc_mcp.model.ID_RE`` (a test asserts the pattern strings are equal).
ID_RE = re.compile(r"^[A-Za-z0-9][\w.:-]{0,127}$", re.UNICODE)
#: Nodes, machines and inserted elements: ID_RE without ':' and at most 124 characters, so that
#: ``<id>:in`` / ``<id>:out`` / ``<id>:<n>`` and ``src:rel:tgt`` stay injective and inside ID_RE. ASCII only (a strict
#: subset of ID_RE), so two node ids that look identical but differ in script or normalisation form cannot exist.
AUTHORED_ID_RE = re.compile(r"^[A-Za-z0-9][\w.-]{0,123}$", re.ASCII)
#: Machine state names (transition ids are built from them).
STATE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
#: Short tokens: repository alias, ``group``, ``area``.
TOKEN_RE = re.compile(r"^[A-Za-z0-9][\w.-]{0,39}$", re.UNICODE)
#: ``meta.projectId`` (same shape as a workspace project name).
PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
#: ``cardinality`` of a ``references`` edge, for example ``N:1`` or ``1:0..1``.
CARDINALITY_RE = re.compile(r"^[0-9N*]+(?:\.\.[0-9N*]+)?:[0-9N*]+(?:\.\.[0-9N*]+)?$")
#: ``policies[].scope``: ``all`` or ``kind:<Kind>`` or ``node:<id>`` or ``req:<id>``.
SCOPE_RE = re.compile(r"^(?:all|kind:[A-Za-z][A-Za-z0-9]*|node:[A-Za-z0-9][\w.-]{0,123}|req:[A-Za-z0-9][\w.:-]{0,127})$", re.UNICODE)

HASH_ID_PREFIX = "h:"
HASH_HEX_LEN = 32
#: A plain derived id longer than this is replaced by a hash id.
MAX_PLAIN_ID = 120
MAX_CHAIN_ORDINAL = 98
USER_PREFIX = "user:"
#: Derived verification never exceeds this in ``tobe`` mode (nothing exists to be Implemented yet).
TOBE_CEILING = "Designed"
#: Verification ceiling of an edge whose evidence was inherited from its endpoints (``ev=^``).
INHERITED_CEILING = "Designed"

#: One constant, used by the generator, the expander and the engine (the reference duplicated it).
DEFAULT_POLICY = MappingProxyType({"timeoutMs": 2000, "retryMax": 0, "retryBackoffMs": 0})
POLICY_KEYS = ("timeoutMs", "retryMax", "retryBackoffMs")

#: Expected state of a bare node id in an ``expect`` row (written out explicitly in the expanded model).
DEFAULT_EXPECTED_STATE = "Completed"
#: Separator of an event id ``<caseId>#<seq>`` (``#`` is outside ID_RE, so a case id can never contain it).
EVENT_ID_SEP = "#"
#: Nesting limit of any value in a model (props, params, guards ...); deeper or cyclic values are a shape issue.
MAX_NESTING = 64
#: Nesting limit of ``combined_intervention`` inside ``combined_intervention``, and of ``all``/``any``/``not`` in a guard.
MAX_PART_DEPTH = 6
MAX_GUARD_DEPTH = 16

EVENT_OPTION_KEYS = (
    "ruleId", "edge", "latencyMs", "ack", "section", "group", "dedupKey", "role", "params", "when", "guard",
    "fault", "errorCode", "ms",
)
ATOMIC_PARAM_KEYS = (
    "targetNodeId", "targetEdgeId", "targetEventId", "targetRuleId", "machineId", "transitionId", "guard",
    "timeoutMs", "retryMax", "retryBackoffMs", "addLatencyMs", "element", "rewrite", "parts",
)

# Error codes of ``contract_issues`` (GEMARC_ATLAS_MODEL_NNN).
E_BAD_PART = "GEMARC_ATLAS_MODEL_001"
E_BAD_ARG = "GEMARC_ATLAS_MODEL_002"
E_SHAPE = "GEMARC_ATLAS_MODEL_010"
E_FIELD = "GEMARC_ATLAS_MODEL_011"
E_ID = "GEMARC_ATLAS_MODEL_020"
E_DUP = "GEMARC_ATLAS_MODEL_021"
E_ENUM = "GEMARC_ATLAS_MODEL_030"
E_REF = "GEMARC_ATLAS_MODEL_040"
E_DERIVED = "GEMARC_ATLAS_MODEL_050"
E_CLAIM = "GEMARC_ATLAS_MODEL_051"
E_MACHINE = "GEMARC_ATLAS_MODEL_060"


class AtlasModelError(ValueError):
    """A helper was called with arguments that break the contract."""

    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(f"{code}: {message}")


def _profile(profile: "vocab.Profile | None") -> "vocab.Profile":
    return profile if profile is not None else vocab.default_profile()


def is_id(value) -> bool:
    return isinstance(value, str) and ID_RE.fullmatch(value) is not None


def is_authored_id(value) -> bool:
    return isinstance(value, str) and AUTHORED_ID_RE.fullmatch(value) is not None


def is_state_name(value) -> bool:
    return isinstance(value, str) and STATE_RE.fullmatch(value) is not None


# --------------------------------------------------------------------------------------------------------
# Derived ids
# --------------------------------------------------------------------------------------------------------


def hash_id(domain: str, *parts: str) -> str:
    """``h:`` + first 32 hex digits of sha256 over ``domain\\npart\\npart...`` (UTF-8, LF joined)."""
    if not isinstance(domain, str) or any(not isinstance(part, str) for part in parts):
        raise AtlasModelError(E_BAD_ARG, "hash_id needs string arguments")
    material = "\n".join((domain,) + tuple(parts)).encode("utf-8")
    return HASH_ID_PREFIX + hashlib.sha256(material).hexdigest()[:HASH_HEX_LEN]


def _plain_part(value, what: str) -> str:
    if not isinstance(value, str) or not value or ":" in value or "\n" in value:
        raise AtlasModelError(E_BAD_PART, f"{what} must be a non-empty string without ':' or newline, got {value!r}")
    return value


def _need_keys(record, keys: Sequence[str], what: str) -> None:
    """Raise ``AtlasModelError`` unless ``record`` is a mapping that has all ``keys``."""
    if not isinstance(record, Mapping):
        raise AtlasModelError(E_BAD_ARG, f"{what} must be an object, got {type(record).__name__}")
    missing = [key for key in keys if key not in record]
    if missing:
        raise AtlasModelError(E_BAD_ARG, f"{what} is missing {missing}")


def edge_id(source_id: str, relation: str, target_id: str) -> str:
    """``src:rel:tgt``; above ``MAX_PLAIN_ID`` characters the hash fallback ``h:<hex32>`` (domain ``edge``)."""
    parts = (_plain_part(source_id, "source id"), _plain_part(relation, "relation"), _plain_part(target_id, "target id"))
    plain = ":".join(parts)
    return plain if len(plain) <= MAX_PLAIN_ID else hash_id("edge", *parts)


def edge_triple_key(source_id: str, relation: str, target_id: str) -> str:
    """The plain ``src:rel:tgt`` string, never shortened (used only as an exact lookup key)."""
    return f"{source_id}:{relation}:{target_id}"


def edge_ref_index(edges: Iterable[Mapping]) -> dict[str, str]:
    """Exact reference resolution for edge references: edge id -> id, plain triple -> id.

    A plain triple resolves only when exactly one edge has that triple, and never shadows a real edge id.
    Nothing is ever split on ':'.
    """
    edges = list(edges)
    for edge in edges:
        _need_keys(edge, ("id", "sourceId", "relation", "targetId"), "edge")
    index = {edge["id"]: edge["id"] for edge in edges}
    counts = Counter(edge_triple_key(e["sourceId"], e["relation"], e["targetId"]) for e in edges)
    for edge in edges:
        key = edge_triple_key(edge["sourceId"], edge["relation"], edge["targetId"])
        if counts[key] == 1 and key not in index:
            index[key] = edge["id"]
    return index


def port_id(node_id: str, direction: str) -> str:
    if direction not in ("in", "out"):
        raise AtlasModelError(E_BAD_ARG, f"port direction must be 'in' or 'out', got {direction!r}")
    return f"{_plain_part(node_id, 'node id')}:{direction}"


def port_ids(node_id: str, is_machine: bool = False) -> list[str]:
    """``[<id>:in, <id>:out]``; ``[]`` for a machine node."""
    return [] if is_machine else [port_id(node_id, "in"), port_id(node_id, "out")]


node_port_ids = port_ids  # alias kept for readability at call sites that also handle edge ports


def port_type_for_kind(kind: str, record_kinds: Iterable[str] | None = None, *, profile=None) -> str:
    records = _profile(profile).record_kinds if record_kinds is None else record_kinds
    return "Record" if kind in records else "Message"


def derive_ports(nodes: Iterable[Mapping], machine_ids: Iterable[str] = (), record_kinds: Iterable[str] | None = None,
                 *, profile=None) -> list[dict]:
    """Ports of every non-machine node, in node order, ``in`` then ``out``."""
    machines = set(machine_ids)
    records = _profile(profile).record_kinds if record_kinds is None else frozenset(record_kinds)
    ports: list[dict] = []
    for node in nodes:
        _need_keys(node, ("id", "kind"), "node")
        if node["id"] in machines:
            continue
        kind_type = port_type_for_kind(node["kind"], records)
        for direction in ("in", "out"):
            ports.append({"id": port_id(node["id"], direction), "nodeId": node["id"], "direction": direction, "type": kind_type})
    return ports


def edge_ports(source_id: str, target_id: str, machine_ids: Iterable[str] = ()) -> tuple[str | None, str | None]:
    """``(<src>:out, <tgt>:in)``; a side is ``None`` when that endpoint is a machine node."""
    machines = set(machine_ids)
    _plain_part(source_id, "source id")
    _plain_part(target_id, "target id")
    source_port = None if source_id in machines else port_id(source_id, "out")
    target_port = None if target_id in machines else port_id(target_id, "in")
    return source_port, target_port


def transition_id(machine_id: str, source_state: str, target_state: str) -> str:
    """``<machine>.<from>_to_<to>``; hash fallback ``h:<hex32>`` (domain ``transition``) above ``MAX_PLAIN_ID``."""
    _plain_part(machine_id, "machine id")
    for state in (source_state, target_state):
        if not is_state_name(state):
            raise AtlasModelError(E_BAD_PART, f"transition state must match {STATE_RE.pattern}, got {state!r}")
    plain = f"{machine_id}.{source_state}_to_{target_state}"
    return plain if len(plain) <= MAX_PLAIN_ID else hash_id("transition", machine_id, source_state, target_state)


def assign_transition_ids(machine_id: str, transitions: Sequence[Mapping]) -> tuple[list[str], list[str]]:
    """Transition ids (explicit ``id`` wins) and the ids that occur more than once.

    ``_`` is legal inside a state name, so two different pairs can derive the same id (``a_to``->``b`` and
    ``a``->``to_b``): duplicates are detected on ids, not on pairs, and must be repaired with an explicit id.
    """
    for t in transitions:
        _need_keys(t, ("from", "to"), "transition")
    ids = [t["id"] if t.get("id") else transition_id(machine_id, t["from"], t["to"]) for t in transitions]
    counts = Counter(ids)
    return ids, [i for i in dict.fromkeys(ids) if counts[i] > 1]


def chain_edge_id(element_id: str, ordinal: int) -> str:
    """Rewrite chain edge ``<element>:<ordinal>``; ordinal 1-based over all chain edges of the element."""
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or not 1 <= ordinal <= MAX_CHAIN_ORDINAL:
        raise AtlasModelError(E_BAD_ARG, f"chain ordinal must be 1..{MAX_CHAIN_ORDINAL}, got {ordinal!r}")
    return f"{_plain_part(element_id, 'element id')}:{ordinal}"


def derive_rewrite_chain(element_id: str, rewrite_index: int, replaced_edge: Mapping) -> list[dict]:
    """The two chain edges that route a replaced edge through an inserted element.

    ``rewrite_index`` is the 0-based position of the rewrite inside the element's ``rewrite`` list; its chain
    edges are ``<element>:<2i+1>`` and ``<element>:<2i+2>`` (a single rewrite gives ``:1`` and ``:2``).
    """
    first = chain_edge_id(element_id, 2 * rewrite_index + 1)
    second = chain_edge_id(element_id, 2 * rewrite_index + 2)
    return [
        {"id": first, "sourceId": replaced_edge["sourceId"], "targetId": element_id,
         "sourcePortId": replaced_edge["sourcePortId"], "targetPortId": port_id(element_id, "in")},
        {"id": second, "sourceId": element_id, "targetId": replaced_edge["targetId"],
         "sourcePortId": port_id(element_id, "out"), "targetPortId": replaced_edge["targetPortId"]},
    ]


def build_rewrite_entry(element_id: str, rewrite_index: int, replaced_edge: Mapping, disposition: str = "replace") -> dict:
    """One ``rewrite[]`` entry; ``chain`` holds two edges iff the disposition is ``replace``."""
    chain = derive_rewrite_chain(element_id, rewrite_index, replaced_edge) if disposition == "replace" else []
    return {"replaceEdgeId": replaced_edge["id"], "disposition": disposition, "chain": chain}


# --------------------------------------------------------------------------------------------------------
# Event ids, expectations, combined parts, inserted elements
# --------------------------------------------------------------------------------------------------------


def event_id(case_id: str, seq: int) -> str:
    """``<caseId>#<seq>``: how an intervention (``targetEventId``) and a causal comparison name one event of a case."""
    if not is_id(case_id):
        raise AtlasModelError(E_BAD_PART, f"case id {case_id!r} is not an id")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
        raise AtlasModelError(E_BAD_ARG, f"event seq must be an integer >= 1, got {seq!r}")
    return f"{case_id}{EVENT_ID_SEP}{seq}"


def parse_event_id(value) -> tuple[str, int] | None:
    """``(case id, seq)`` of a well-formed event id, else ``None`` (never raises)."""
    if not isinstance(value, str) or value.count(EVENT_ID_SEP) != 1:
        return None
    case_id, _, seq = value.partition(EVENT_ID_SEP)
    if not is_id(case_id) or not re.fullmatch(r"[1-9][0-9]{0,8}", seq):
        return None
    return case_id, int(seq)


def expect_cell(cell: str) -> tuple[str, str]:
    """One entry of an ``expect`` row: ``node`` means ``(node, Completed)``, ``node=State`` means ``(node, State)``."""
    if not isinstance(cell, str):
        raise AtlasModelError(E_BAD_ARG, f"an expect entry is a string, got {type(cell).__name__}")
    node, separator, state = (part.strip() for part in cell.partition("="))
    if not node or (separator and (not state or "=" in state)):
        raise AtlasModelError(E_BAD_ARG, f"an expect entry is 'node' or 'node=State', got {cell!r}")
    return node, state if separator else DEFAULT_EXPECTED_STATE


def _walk_atoms(record, path: str, depth: int, problems: list):
    """``(path, atom, is_part)`` for every atomic intervention of ``record`` (recursing into combined parts).

    Structural trouble (parts that are not a non-empty array, nesting beyond ``MAX_PART_DEPTH``) is appended to
    ``problems`` as ``(path, message)`` instead of raising. A part that is not an object is still yielded.
    """
    if not isinstance(record, Mapping) or record.get("type") != "combined_intervention":
        yield path, record, depth > 0
        return
    parts = record.get("parts")
    if not isinstance(parts, list) or not parts:
        problems.append((f"{path}.parts", "parts must be a non-empty array of atomic interventions"))
        return
    for index, part in enumerate(parts):
        part_path = f"{path}.parts[{index}]"
        if isinstance(part, Mapping) and part.get("type") == "combined_intervention":
            if depth + 1 >= MAX_PART_DEPTH:
                problems.append((part_path, f"combined_intervention nested deeper than {MAX_PART_DEPTH} levels"))
            else:
                yield from _walk_atoms(part, part_path, depth + 1, problems)
        else:
            yield part_path, part, True


def flatten_parts(record: Mapping) -> list[dict]:
    """The atomic interventions of ``record`` in order (depth-first); an atomic record is its own single part.

    Equivalent of the reference engine's flatten of combined interventions; the contract checker validates every
    flattened part exactly like a top-level atomic intervention.
    """
    _need_keys(record, ("type",), "intervention")
    problems: list = []
    atoms = [atom for _, atom, _ in _walk_atoms(record, "$", 0, problems)]
    if problems or any(not isinstance(atom, Mapping) for atom in atoms):
        detail = "; ".join(f"{where}: {why}" for where, why in problems) or "a part is not an object"
        raise AtlasModelError(E_BAD_ARG, f"cannot flatten: {detail}")
    return list(atoms)


def element_port_types(replaced_edges: Iterable[Mapping], ports: Iterable[Mapping]) -> tuple[str, str]:
    """``(input type, output type)`` of an inserted element.

    The input type is the common type of the source out-ports of the replaced edges, the output type the common type
    of their target in-ports; ``Any`` when the types differ, when no edge has a port on that side, or when there is
    no replaced edge (a draft element).
    """
    by_id = {port["id"]: port["type"] for port in ports}
    edges = list(replaced_edges)

    def common(key: str) -> str:
        types = {by_id[edge[key]] for edge in edges if edge.get(key) in by_id}
        return next(iter(types)) if len(types) == 1 else "Any"

    return common("sourcePortId"), common("targetPortId")


def derive_element_ports(element_id: str, input_type: str = "Any", output_type: str = "Any", *, profile=None) -> tuple[list[dict], list[dict]]:
    """``inputPorts`` and ``outputPorts`` of an inserted element: one port each, ``<element>:in`` / ``<element>:out``."""
    allowed = _profile(profile).enums["elementPortTypes"]
    for port_type in (input_type, output_type):
        if port_type not in allowed:
            raise AtlasModelError(E_BAD_ARG, f"element port type must be one of {list(allowed)}, got {port_type!r}")
    return ([{"id": port_id(element_id, "in"), "type": input_type}], [{"id": port_id(element_id, "out"), "type": output_type}])


def element_home_room(rewrites: Iterable[Mapping], edges: Iterable[Mapping], nodes: Iterable[Mapping]) -> str | None:
    """Room in which a viewer places an inserted element: the room of the source node of the first replaced edge.

    ``None`` when no rewrite names a known edge; the viewer then falls back to its own default room. An inserted
    element has no room field of its own (it exists only inside one intervention).
    """
    edge_by_id = {e["id"]: e for e in edges}
    node_by_id = {n["id"]: n for n in nodes}
    for rewrite in rewrites:
        edge = edge_by_id.get(rewrite.get("replaceEdgeId")) if isinstance(rewrite, Mapping) else None
        node = node_by_id.get(edge["sourceId"]) if edge else None
        if node is not None:
            return node["roomId"]
    return None


def linked_machine_ids(node: Mapping, edges: Iterable[Mapping], machine_ids: Iterable[str]) -> list[str]:
    """Machine nodes a class diagram links to ``node``: targets of ``owns`` edges that leave the node's ``props.owner``.

    This replaces a fixed interface -> owner -> machine table; the link comes from data (``props.owner`` and the
    generated ``owns`` edge of ``owner=``), in edge order, without duplicates.
    """
    owner = (node.get("props") or {}).get("owner")
    machines = set(machine_ids)
    if not isinstance(owner, str):
        return []
    return list(dict.fromkeys(e["targetId"] for e in edges if e["relation"] == "owns" and e["sourceId"] == owner and e["targetId"] in machines))


# --------------------------------------------------------------------------------------------------------
# Verification and evidence
# --------------------------------------------------------------------------------------------------------


def rank(level, *, profile=None) -> int:
    return _profile(profile).rank(level)


def weakest(levels: Iterable[str], *, profile=None) -> str:
    """Lowest-ranked level; ``Unknown`` for an empty input. Uses GemArc rank (Implemented above Observed)."""
    p = _profile(profile)
    levels = [lv for lv in levels if p.rank(lv)]
    return min(levels, key=p.rank) if levels else "Unknown"


def cap_verification(level: str, ceiling: str, *, profile=None) -> str:
    """``level`` unless it is stronger than ``ceiling``. Does not validate: an unknown level is returned unchanged (the
    contract checker reports unknown levels); ``merge_policy`` behaves the same way for the values it merges."""
    p = _profile(profile)
    return level if p.rank(level) <= p.rank(ceiling) else ceiling


def evidence_kind(file_path: str, doc_ext: Iterable[str] | None = None, *, profile=None) -> str:
    """``user-requirement`` (starts with ``user:``, checked first), ``document`` (listed extension), else ``source-code``."""
    if not isinstance(file_path, str):
        raise AtlasModelError(E_BAD_ARG, f"file path must be a string, got {type(file_path).__name__}")
    if file_path.startswith(USER_PREFIX):
        return "user-requirement"
    extensions = _profile(profile).doc_ext if doc_ext is None else frozenset(e.lower() for e in doc_ext)
    name = file_path.replace("\\", "/").rsplit("/", 1)[-1]
    dot = name.rfind(".")
    ext = name[dot:].lower() if dot > 0 else ""
    return "document" if ext in extensions else "source-code"


def evidence_verification(kind: str, mode: str = "asis", *, profile=None) -> str:
    p = _profile(profile)
    item = p.item("sourceKinds", kind)
    level = item["verification"] if item else "Unknown"
    return cap_verification(level, TOBE_CEILING, profile=p) if mode == "tobe" else level


def _default_verification(evidence_kinds: Iterable[str], mode: str, inherited: bool, profile) -> str:
    kinds = list(evidence_kinds)
    if "source-code" in kinds:
        level = "Implemented"
    elif kinds:
        level = "Designed"
    else:
        level = "Hypothetical"
    if mode == "tobe":
        level = cap_verification(level, TOBE_CEILING, profile=profile)
    if inherited:
        level = cap_verification(level, INHERITED_CEILING, profile=profile)
    return level


def default_verification_for_node(evidence_kinds: Iterable[str], mode: str = "asis", *, profile=None) -> str:
    """Implemented if any source-code evidence, else Designed if any evidence, else Hypothetical (tobe: capped at Designed)."""
    return _default_verification(evidence_kinds, mode, False, _profile(profile))


def default_verification_for_edge(evidence_kinds: Iterable[str], mode: str = "asis", inherited: bool = False, *,
                                  profile=None) -> str:
    """Same rule on the edge's OWN evidence; inherited evidence (``ev=^``) is capped at Designed."""
    return _default_verification(evidence_kinds, mode, inherited, _profile(profile))


def evidence_kinds_of(evidence_ids: Iterable[str], evidence_index: Mapping[str, Mapping]) -> list[str]:
    """Source kinds of the resolvable evidence ids (unresolved ids are skipped; RF-003 reports them)."""
    return [evidence_index[i]["sourceKind"] for i in evidence_ids if i in evidence_index]


def inherit_edge_evidence(source_evidence_ids: Iterable[str], target_evidence_ids: Iterable[str]) -> list[str]:
    """``ev=^``: union of the endpoints' evidence, source first, de-duplicated in order."""
    return list(dict.fromkeys([*source_evidence_ids, *target_evidence_ids]))


def build_evidence(evidence_id: str, file_path: str, anchor: str, topic: str, repository: str = "", *,
                   mode: str = "asis", line_range: Sequence[int] | None = None, doc_ext: Iterable[str] | None = None,
                   profile=None) -> dict:
    kind = evidence_kind(file_path, doc_ext, profile=profile)
    return {
        "evidenceId": evidence_id, "sourceKind": kind, "repository": "" if repository is None else repository,
        "filePath": file_path, "lineRange": None if line_range is None else [int(line_range[0]), int(line_range[1])],
        "anchor": anchor, "topic": topic, "verification": evidence_verification(kind, mode, profile=profile),
    }


# --------------------------------------------------------------------------------------------------------
# Space, nodes, edges
# --------------------------------------------------------------------------------------------------------


def inherit_props(props: Mapping | None, room: Mapping | None = None, building: Mapping | None = None) -> dict:
    """Authored ``props`` plus room ``tier``/``area`` and building ``trust``; an authored key always wins."""
    for what, value in (("props", props), ("room", room), ("building", building)):
        if value is not None and not isinstance(value, Mapping):
            raise AtlasModelError(E_BAD_ARG, f"{what} must be an object, got {type(value).__name__}")
    out = copy.deepcopy(dict(props or {}))
    if room:
        for key in ("tier", "area"):
            if key in room and key not in out:
                out[key] = room[key]
    if building and "trust" in building and "trust" not in out:
        out["trust"] = building["trust"]
    return out


def build_building(building_id: str, name: str, trust: str | None = None, *, profile=None) -> dict:
    return {"id": building_id, "name": name, "trust": trust or _profile(profile).default_trust}


def build_floor(floor_id: str, building_id: str, name: str, level: int) -> dict:
    return {"id": floor_id, "buildingId": building_id, "name": name, "level": level}


def build_room(room_id: str, floor_id: str, name: str, layer: str | None = None, tier: str | None = None,
               area: str | None = None) -> dict:
    room = {"id": room_id, "floorId": floor_id, "name": name}
    for key, value in (("layer", layer), ("tier", tier), ("area", area)):
        if value:
            room[key] = value
    return room


def build_node(node_id: str, kind: str, name: str, room_id: str, sub: str = "", evidence_ids: Iterable[str] = (),
               verification: str | None = None, props: Mapping | None = None, group: str | None = None,
               level: str | None = None, *, evidence_kinds: Iterable[str] = (), is_machine: bool = False,
               mode: str = "asis", room: Mapping | None = None, building: Mapping | None = None, profile=None) -> dict:
    if not is_authored_id(node_id):
        raise AtlasModelError(E_ID, f"node id {node_id!r} must match the authored id grammar (ASCII, no ':', at most 124 characters)")
    node = {
        "id": node_id, "kind": kind, "name": name, "roomId": room_id, "sub": sub, "evidenceIds": list(evidence_ids),
        "verification": verification or default_verification_for_node(evidence_kinds, mode, profile=profile),
        "props": inherit_props(props, room, building), "portIds": port_ids(node_id, is_machine),
    }
    if group:
        node["group"] = group
    if level:
        node["level"] = level
    return node


def build_edge(source_id: str, relation: str, target_id: str, label: str, evidence_ids: Iterable[str] = (),
               verification: str | None = None, cardinality: str | None = None, group: str | None = None,
               alias: str | None = None, *, evidence_kinds: Iterable[str] = (), inherited: bool = False,
               machine_ids: Iterable[str] = (), mode: str = "asis", profile=None) -> dict:
    """An edge with every derived field. ``alias`` (when given) becomes the id verbatim."""
    p = _profile(profile)
    relation_item = p.item("relations", relation)
    if relation_item is None:
        raise AtlasModelError(E_ENUM, f"unknown relation {relation!r}; known: {', '.join(p.ids('relations'))}")
    for what, value in (("source", source_id), ("target", target_id)):
        if not is_authored_id(value):
            raise AtlasModelError(E_ID, f"edge {what} {value!r} must match the authored id grammar (ASCII, no ':', at most 124 characters)")
    if alias is not None and not is_authored_id(alias):
        raise AtlasModelError(E_ID, f"edge alias {alias!r} must match the authored id grammar (no ':', at most 124 characters)")
    source_port, target_port = edge_ports(source_id, target_id, machine_ids)
    edge = {
        "id": alias or edge_id(source_id, relation, target_id), "sourceId": source_id, "targetId": target_id,
        "type": "directed", "directed": True, "relation": relation, "static": relation_item["static"], "label": label,
        "verification": verification or default_verification_for_edge(evidence_kinds, mode, inherited, profile=p),
        "evidenceIds": list(evidence_ids), "sourcePortId": source_port, "targetPortId": target_port, "guardId": None,
    }
    if cardinality:
        edge["cardinality"] = cardinality
    if group:
        edge["group"] = group
    return edge


def build_owns_edge(owner_id: str, machine: Mapping, machine_node: Mapping, *, evidence_kinds: Iterable[str] = (),
                    machine_ids: Iterable[str] = (), mode: str = "asis", profile=None) -> dict:
    """The generated ``owner -> machine node`` edge: label = machine node name, evidence = the machine's ``ev``."""
    return build_edge(owner_id, "owns", machine["nodeId"], machine_node["name"], machine["evidenceIds"],
                      evidence_kinds=evidence_kinds, machine_ids=machine_ids, mode=mode, profile=profile)


def default_requirement_edges(node_ids: Iterable[str], edges: Iterable[Mapping]) -> list[str]:
    """Ids of the edges whose two endpoints are both in ``node_ids``, in edge order."""
    wanted = set(node_ids)
    return [e["id"] for e in edges if e["sourceId"] in wanted and e["targetId"] in wanted]


def node_level(node: Mapping, room: Mapping | None = None, *, profile=None) -> str:
    """Shallowest detail level (REQ/BD/DD) at which the node is shown: authored override, room layer, kind default."""
    p = _profile(profile)
    if node.get("level"):
        return node["level"]
    layer = p.item("layers", room.get("layer")) if room else None
    if layer and node["kind"] in layer["levelKinds"]:
        return layer["level"]
    kind = p.item("kinds", node["kind"])
    return kind["level"] if kind else "DD"


def relation_level(relation: str, *, profile=None) -> str:
    item = _profile(profile).item("relations", relation)
    return item["level"] if item else "DD"


def node_domain(node: Mapping, room: Mapping | None = None, building: Mapping | None = None, *, profile=None) -> str:
    """Colour domain: kind domain, else room layer domain, else ``props.trust``, else building trust, else default."""
    p = _profile(profile)
    kind = p.item("kinds", node["kind"])
    if kind and kind["domain"]:
        return kind["domain"]
    layer = p.item("layers", room.get("layer")) if room else None
    if layer:
        return layer["domain"]
    trust = (node.get("props") or {}).get("trust") or (building or {}).get("trust")
    domain = p.item("trust", trust)
    return domain["domain"] if domain else p.default_trust


# --------------------------------------------------------------------------------------------------------
# Machines, policy, cases
# --------------------------------------------------------------------------------------------------------


def _state_pair(item) -> tuple[str, str]:
    """``(from, to)`` of a transition given as a mapping or a 2-sequence (anything else is a contract violation)."""
    if isinstance(item, Mapping):
        _need_keys(item, ("from", "to"), "transition")
        return item["from"], item["to"]
    if isinstance(item, (tuple, list)) and len(item) == 2:
        return item[0], item[1]
    raise AtlasModelError(E_BAD_ARG, f"a transition is {{from, to}} or a (from, to) pair, got {item!r}")


def machine_states(initial: str, transitions: Iterable[Mapping | tuple]) -> list[str]:
    """``[initial]`` followed by every state in first-seen order over ``(from, to)`` of each transition.

    First-seen order is the contract (the authored order of a state list is not recoverable from the transitions);
    the set of states equals what an author would list, only the order can differ.
    """
    states = [initial]
    for item in transitions:
        source, target = _state_pair(item)
        for state in (source, target):
            if state not in states:
                states.append(state)
    return states


def machine_terminal(states: Sequence[str], transitions: Iterable[Mapping | tuple]) -> list[str]:
    """States (in ``states`` order) that no transition leaves."""
    sources = {_state_pair(t)[0] for t in transitions}
    return [s for s in states if s not in sources]


def build_machine(machine_id: str, initial: str, transitions: Sequence[Mapping] = (), error_code: str | None = None,
                  evidence_ids: Iterable[str] = ()) -> dict:
    """A machine record; ``transitions`` are ``{from, to, [id], [guard], [errorCode]}`` in authoring order."""
    if not is_authored_id(machine_id):
        raise AtlasModelError(E_ID, f"machine id {machine_id!r} must match the authored id grammar")
    if not is_state_name(initial):
        raise AtlasModelError(E_BAD_PART, f"initial state must match {STATE_RE.pattern}, got {initial!r}")
    ids, duplicates = assign_transition_ids(machine_id, transitions)
    if duplicates:
        raise AtlasModelError(E_DUP, f"machine {machine_id}: duplicate transition ids {duplicates}; give them explicit ids")
    records = []
    for tid, source in zip(ids, transitions):
        record = {"id": tid, "from": source["from"], "to": source["to"]}
        if source.get("guard") is not None:
            record["guard"] = source["guard"]
        if source.get("errorCode"):
            record["errorCode"] = source["errorCode"]
        records.append(record)
    states = machine_states(initial, records)
    return {
        "id": machine_id, "nodeId": machine_id, "initial": initial, "errorCode": error_code,
        "evidenceIds": list(evidence_ids), "states": states, "terminal": machine_terminal(states, records),
        "transitions": records,
    }


def default_policy() -> dict:
    return dict(DEFAULT_POLICY)


def merge_policy(partial: Mapping | None = None) -> dict:
    """The full 3-key policy: defaults overridden by the authored (possibly partial) policy."""
    policy = default_policy()
    for key in POLICY_KEYS:
        if partial and partial.get(key) is not None:
            policy[key] = partial[key]
    return policy


def default_linked_tests(requirement_ids: Iterable[str], requirements: Iterable[Mapping]) -> list[str]:
    """Union of ``tstIds`` of the cited requirements, in citation order, de-duplicated."""
    by_id = {r["id"]: r for r in requirements}
    out: list[str] = []
    for rid in requirement_ids:
        for tid in by_id.get(rid, {}).get("tstIds", []):
            if tid not in out:
                out.append(tid)
    return out


def cases_of_requirement(cases: Iterable[Mapping], requirement_id: str) -> list[str]:
    """Inverse of ``req=``: ids of the cases that cite the requirement."""
    return [c["caseId"] for c in cases if requirement_id in c.get("requirementIds", [])]


def build_event(seq: int, event_type: str, source: str, target: str, **options) -> dict:
    """Event ``{seq, type, from, to}`` plus the given options (``None`` values dropped)."""
    unknown = set(options) - set(EVENT_OPTION_KEYS)
    if unknown:
        raise AtlasModelError(E_BAD_ARG, f"unknown event options {sorted(unknown)}; allowed: {list(EVENT_OPTION_KEYS)}")
    event = {"seq": seq, "type": event_type, "from": source, "to": target}
    event.update({k: v for k, v in options.items() if v is not None})
    return event


def number_events(events: Iterable[Mapping | tuple]) -> list[dict]:
    """Assign ``seq`` 1..n in row order. Rows are mappings ``{type, from, to, ...}`` or tuples ``(type, from, to[, options])``."""
    out = []
    for index, row in enumerate(events, start=1):
        if isinstance(row, Mapping):
            _need_keys(row, ("type", "from", "to"), f"event row {index}")
            options = {k: v for k, v in row.items() if k not in ("seq", "type", "from", "to")}
            out.append(build_event(index, row["type"], row["from"], row["to"], **options))
        elif isinstance(row, (tuple, list)) and 3 <= len(row) <= 4 and (len(row) == 3 or isinstance(row[3], Mapping)):
            options = row[3] if len(row) > 3 else {}
            out.append(build_event(index, row[0], row[1], row[2], **options))
        else:
            raise AtlasModelError(E_BAD_ARG, f"event row {index} must be an object or (type, from, to[, options]), got {row!r}")
    return out


def build_case(case_id: str, category: str, title: str, events: Iterable[Mapping | tuple], requirement_ids: Iterable[str] = (),
               evidence_ids: Iterable[str] = (), policy: Mapping | None = None, context: Mapping | None = None,
               initial_machines: Mapping | None = None, expected_terminal_state: Mapping | None = None,
               expected_outcomes: Sequence[Mapping] | None = None, expected_by: str | None = None,
               linked_tests: Iterable[str] | None = None, requirements: Iterable[Mapping] = ()) -> dict:
    requirement_ids = list(requirement_ids)
    case = {
        "caseId": case_id, "category": category, "title": title, "requirementIds": requirement_ids,
        "evidenceIds": list(evidence_ids), "policy": merge_policy(policy), "context": copy.deepcopy(dict(context or {})),
        "expectedTerminalState": dict(expected_terminal_state or {}),
        "linkedTestIds": list(linked_tests) if linked_tests is not None else default_linked_tests(requirement_ids, requirements),
        "events": number_events(events),
    }
    if initial_machines:
        case["initialMachines"] = dict(initial_machines)
    if expected_outcomes:
        case["expectedOutcomes"] = [dict(o) for o in expected_outcomes]
    if expected_by:
        case["expectedBy"] = expected_by
    return case


def build_requirement(requirement_id: str, title: str, source: str, node_ids: Iterable[str] = (),
                      edge_ids: Iterable[str] | None = None, test_ids: Iterable[str] = (), evidence_ids: Iterable[str] = (),
                      tbd_ids: Iterable[str] = (), design_ids: Iterable[str] = (), *, edges: Iterable[Mapping] = ()) -> dict:
    node_ids = list(node_ids)
    return {
        "id": requirement_id, "title": title, "source": source, "designIds": list(design_ids), "nodeIds": node_ids,
        "edgeIds": list(edge_ids) if edge_ids is not None else default_requirement_edges(node_ids, edges),
        "tstIds": list(test_ids), "evidenceIds": list(evidence_ids), "tbdIds": list(tbd_ids),
    }


def build_test(test_id: str, title: str) -> dict:
    return {"id": test_id, "title": title}


def build_tbd(tbd_id: str, title: str, decision: str, evidence_ids: Iterable[str] = ()) -> dict:
    return {"id": tbd_id, "title": title, "decision": decision, "evidenceIds": list(evidence_ids)}


def build_policy(policy_id: str, text: str, src: str, scope: str = "all", level: str = "REQ", chapter: int | None = None,
                 kind: str = "constraint", by: str = "ai") -> dict:
    return {"id": policy_id, "text": text, "src": src, "scope": scope, "level": level, "chapter": chapter, "kind": kind, "by": by}


def build_intervention(intervention_id: str, intervention_type: str, description: str, applies_to: Iterable[str],
                       expect: Mapping, note: str = "", why: str | None = None, **params) -> dict:
    """Intervention record; ``params`` are the type-specific keys (``targetNodeId``, ``timeoutMs``, ``element`` ...)."""
    unknown = set(params) - set(ATOMIC_PARAM_KEYS)
    if unknown:
        raise AtlasModelError(E_BAD_ARG, f"unknown intervention params {sorted(unknown)}; allowed: {list(ATOMIC_PARAM_KEYS)}")
    record: dict[str, Any] = {"interventionId": intervention_id, "type": intervention_type}
    record.update({k: v for k, v in params.items() if v is not None or k == "guard"})
    record.update({"description": description, "appliesTo": list(applies_to), "expect": copy.deepcopy(dict(expect)), "note": note})
    if why:
        record["why"] = why
    return record


def empty_model(project_id: str = "project", title: str = "", mode: str = "asis", languages: Sequence[str] = ("zh-CN",),
                profile: str = vocab.DEFAULT_PROFILE, repos: Sequence[str] = ()) -> dict:
    """A valid, empty ``gemarc-atlas/1`` model."""
    return {
        "schema": SCHEMA,
        "meta": {"projectId": project_id, "title": title, "mode": mode, "languages": list(languages), "profile": profile,
                 "repos": list(repos)},
        "space": {part: [] for part in SPACE_PARTS},
        "nodes": [], "edges": [], "ports": [], "evidences": [], "machines": [], "cases": [], "interventions": [],
        "requirements": [], "tests": [], "tbds": [], "policies": [],
    }


# --------------------------------------------------------------------------------------------------------
# Canonical JSON
# --------------------------------------------------------------------------------------------------------


def canonicalize(value, _depth: int = 0):
    """Plain-JSON copy of ``value``: tuples become lists, mappings become dicts with string keys.

    Nesting deeper than ``MAX_NESTING`` (which includes a cyclic value) raises ``AtlasModelError``.
    """
    if _depth >= MAX_NESTING and isinstance(value, (Mapping, list, tuple)):
        raise AtlasModelError(E_BAD_ARG, f"value is nested deeper than {MAX_NESTING} levels (or cyclic)")
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise AtlasModelError(E_BAD_ARG, f"object keys must be strings, got {key!r}")
            out[key] = canonicalize(item, _depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [canonicalize(item, _depth + 1) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise AtlasModelError(E_BAD_ARG, "NaN and Infinity are not JSON")
        return value
    raise AtlasModelError(E_BAD_ARG, f"{type(value).__name__} is not JSON serialisable")


def canonical_dumps(model: Mapping, indent: int | None = None) -> str:
    """Canonical text: keys sorted at every depth, ``ensure_ascii=False``, no NaN; compact unless ``indent``.

    Arrays keep authoring order and are never re-sorted. The result has no trailing newline.
    """
    data = canonicalize(model)
    if indent is None:
        return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return json.dumps(data, ensure_ascii=False, sort_keys=True, indent=indent, allow_nan=False)


def canonical_bytes(model: Mapping, indent: int | None = None) -> bytes:
    """UTF-8 bytes of ``canonical_dumps`` plus one LF (what is written to disk and hashed when compact)."""
    return (canonical_dumps(model, indent) + "\n").encode("utf-8")


def model_sha(model: Mapping) -> str:
    """sha256 hex of the compact canonical bytes."""
    return hashlib.sha256(canonical_bytes(model)).hexdigest()


def model_sha8(model: Mapping) -> str:
    """First 8 hex digits of ``model_sha``: the version stamp that replaces a hand-kept model version or revision."""
    return model_sha(model)[:8]


def nesting_exceeds(value, limit: int = MAX_NESTING) -> bool:
    """True when ``value`` is nested deeper than ``limit`` levels, is cyclic, or is absurdly large (iterative, no recursion)."""
    stack = [(value, 1)]
    visited = 0
    while stack:
        item, depth = stack.pop()
        if isinstance(item, Mapping):
            children = list(item.values())
        elif isinstance(item, (list, tuple)):
            children = list(item)
        else:
            continue
        visited += 1
        if depth > limit or visited > 5_000_000:
            return True
        stack.extend((child, depth + 1) for child in children)
    return False


# --------------------------------------------------------------------------------------------------------
# Contract checker
# --------------------------------------------------------------------------------------------------------

# type tags: id, aid (authored id), str, text (non-empty str), int, bool, obj, list, ids, strs, nullstr, range
_SPECS: dict[str, tuple[dict[str, str], dict[str, str]]] = {
    "building": ({"id": "id", "name": "text", "trust": "str"}, {}),
    "floor": ({"id": "id", "buildingId": "id", "name": "text", "level": "int"}, {}),
    "room": ({"id": "id", "floorId": "id", "name": "text"}, {"layer": "str", "tier": "str", "area": "token"}),
    "node": ({"id": "aid", "kind": "str", "name": "text", "roomId": "id", "sub": "str", "evidenceIds": "ids",
              "verification": "str", "props": "obj", "portIds": "strs"}, {"group": "token", "level": "str"}),
    "edge": ({"id": "id", "sourceId": "aid", "targetId": "aid", "type": "str", "directed": "bool", "relation": "str",
              "static": "bool", "label": "text", "verification": "str", "evidenceIds": "ids", "sourcePortId": "nullstr",
              "targetPortId": "nullstr", "guardId": "null"}, {"cardinality": "str", "group": "token"}),
    "port": ({"id": "id", "nodeId": "aid", "direction": "str", "type": "str"}, {}),
    "evidence": ({"evidenceId": "id", "sourceKind": "str", "repository": "str", "filePath": "text", "lineRange": "range",
                  "anchor": "text", "topic": "text", "verification": "str"}, {}),
    "machine": ({"id": "aid", "nodeId": "aid", "initial": "state", "errorCode": "nullstr", "evidenceIds": "ids",
                 "states": "strs", "terminal": "strs", "transitions": "list"}, {}),
    "transition": ({"id": "id", "from": "state", "to": "state"}, {"guard": "obj", "errorCode": "str"}),
    "case": ({"caseId": "id", "category": "str", "title": "text", "requirementIds": "ids", "evidenceIds": "ids",
              "policy": "obj", "context": "obj", "expectedTerminalState": "obj", "linkedTestIds": "ids", "events": "list"},
             {"initialMachines": "obj", "expectedOutcomes": "list", "expectedBy": "str"}),
    "intervention": ({"interventionId": "id", "type": "str", "description": "text", "appliesTo": "ids", "expect": "obj",
                      "note": "str"}, {k: "any" for k in ATOMIC_PARAM_KEYS} | {"why": "str"}),
    "requirement": ({"id": "id", "title": "text", "source": "text", "designIds": "strs", "nodeIds": "ids", "edgeIds": "ids",
                     "tstIds": "ids", "evidenceIds": "ids", "tbdIds": "ids"}, {}),
    "test": ({"id": "id", "title": "text"}, {}),
    "tbd": ({"id": "id", "title": "text", "decision": "text", "evidenceIds": "ids"}, {}),
    "policy": ({"id": "id", "text": "text", "src": "id", "scope": "str", "level": "str", "chapter": "nullint", "kind": "str",
                "by": "str"}, {}),
}
_EVENT_SPEC = ({"seq": "int", "type": "str", "from": "aid", "to": "aid"}, {k: "any" for k in EVENT_OPTION_KEYS})
_META_SPEC = ({"projectId": "str", "title": "str", "mode": "str", "languages": "strs", "profile": "str", "repos": "strs"}, {})


def _type_ok(tag: str, value) -> bool:
    if tag == "any":
        return True
    if tag in ("id", "aid", "token", "state", "text", "str", "nullstr") and value is None:
        return tag == "nullstr"
    if tag == "id":
        return is_id(value)
    if tag == "aid":
        return is_authored_id(value)
    if tag == "token":
        return isinstance(value, str) and TOKEN_RE.fullmatch(value) is not None
    if tag == "state":
        return is_state_name(value)
    if tag == "text":
        return isinstance(value, str) and bool(value.strip())
    if tag in ("str", "nullstr"):
        return isinstance(value, str)
    if tag == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if tag == "nullint":
        return value is None or (isinstance(value, int) and not isinstance(value, bool))
    if tag == "bool":
        return isinstance(value, bool)
    if tag == "null":
        return value is None
    if tag == "obj":
        return isinstance(value, dict)
    if tag == "list":
        return isinstance(value, list)
    if tag == "strs":
        return isinstance(value, list) and all(isinstance(x, str) for x in value)
    if tag == "ids":
        return isinstance(value, list) and all(is_id(x) for x in value)
    if tag == "range":
        return value is None or (isinstance(value, list) and len(value) == 2 and all(
            isinstance(x, int) and not isinstance(x, bool) and x >= 1 for x in value) and value[0] <= value[1])
    return False


class _Checker:
    def __init__(self, model: Mapping, profile):
        self.m = model
        self.p = profile
        self.issues: list[dict] = []
        self.node_ids: set[str] = set()
        self.node_states: frozenset[str] = frozenset(profile.ids("nodeStates"))
        self.machine_states: dict[str, list[str]] = {}

    def add(self, code: str, path: str, message: str) -> None:
        self.issues.append({"code": code, "path": path, "message": message})

    def keys(self, record, spec, path: str) -> bool:
        required, optional = spec
        if not isinstance(record, dict):
            self.add(E_SHAPE, path, "record must be an object")
            return False
        ok = True
        for key, tag in required.items():
            if key not in record:
                self.add(E_FIELD, f"{path}.{key}", "required field missing")
                ok = False
            elif not _type_ok(tag, record[key]):
                self.add(E_FIELD if tag not in ("id", "aid") else E_ID, f"{path}.{key}", f"expected {tag}, got {record[key]!r}")
                ok = False
        for key, value in record.items():
            if key in required:
                continue
            if key not in optional:
                self.add(E_FIELD, f"{path}.{key}", "unknown field (derived or foreign data must not be added)")
            elif not _type_ok(optional[key], value):
                self.add(E_FIELD, f"{path}.{key}", f"expected {optional[key]}, got {value!r}")
                ok = False
        return ok

    def enum(self, group: str, value, path: str) -> None:
        if not self.p.has(group, value):
            self.add(E_ENUM, path, f"{value!r} is not in vocab.{group}: {', '.join(self.p.ids(group))}")

    def enum_plain(self, name: str, value, path: str) -> None:
        if value not in self.p.enums[name]:
            self.add(E_ENUM, path, f"{value!r} is not in enums.{name}: {', '.join(self.p.enums[name])}")

    def ref(self, value, valid, path: str, what: str) -> None:
        if not isinstance(value, str) or value not in valid:
            self.add(E_REF, path, f"{what} {value!r} is not defined")

    def refs(self, values, valid, path: str, what: str) -> None:
        for index, value in enumerate(values):
            self.ref(value, valid, f"{path}[{index}]", what)


def _records(m: Mapping, key: str, path: str, checker: _Checker, spec_name: str) -> list[dict]:
    value = m.get(key)
    if not isinstance(value, list):
        checker.add(E_SHAPE, path, "must be an array")
        return []
    out = []
    for index, record in enumerate(value):
        if checker.keys(record, _SPECS[spec_name], f"{path}[{index}]"):
            out.append(record)
    return out


def contract_issues(model: Mapping, profile: "vocab.Profile | None" = None) -> list[dict]:
    """Check an expanded model against the contract; ``[]`` means conformant (never raises).

    A crash inside the checker is reported as one extra issue starting with ``internal checker error``
    (the fuzz test asserts that never happens), so callers always get a list.
    """
    try:
        return _contract_issues(model, profile)
    except Exception as ex:  # noqa: BLE001 - the contract checker must never raise on hostile input
        return [{"code": E_SHAPE, "path": "$", "message": f"internal checker error: {type(ex).__name__}: {ex}"}]


def _contract_issues(model: Mapping, profile: "vocab.Profile | None" = None) -> list[dict]:
    """Check an expanded model against the contract; ``[]`` means conformant.

    Each issue is ``{"code", "path", "message"}``. Covers shape, id grammar and uniqueness, vocabulary,
    referential integrity (the ported ``validateModel`` rules) and every derived field. It does not run any
    AI-facing rule (typo hints, coverage, oracle, evidence on disk).
    """
    p = _profile(profile)
    c = _Checker(model, p)
    if not isinstance(model, Mapping):
        return [{"code": E_SHAPE, "path": "$", "message": "model must be an object"}]
    if nesting_exceeds(model):
        return [{"code": E_SHAPE, "path": "$", "message": f"the model is nested deeper than {MAX_NESTING} levels, is cyclic or is too large"}]
    expected_keys = ("schema",) + COLLECTIONS
    for key in expected_keys:
        if key not in model:
            c.add(E_SHAPE, f"$.{key}", "top-level key missing")
    for key in model:
        if key not in expected_keys:
            c.add(E_SHAPE, f"$.{key}", "unknown top-level key")
    if model.get("schema") != SCHEMA:
        c.add(E_SHAPE, "$.schema", f"must be {SCHEMA!r}")
    meta = model.get("meta") if isinstance(model.get("meta"), dict) else {}
    c.keys(model.get("meta"), _META_SPEC, "$.meta")
    mode = meta.get("mode", "asis")
    if isinstance(mode, str):
        c.enum_plain("modes", mode, "$.meta.mode")
    else:
        mode = "asis"
    if isinstance(meta.get("projectId"), str) and not PROJECT_ID_RE.fullmatch(meta["projectId"]):
        c.add(E_ID, "$.meta.projectId", "must match the project id grammar")
    languages = meta.get("languages")
    if _type_ok("strs", languages):
        if not languages or len(set(languages)) != len(languages) or any(x not in p.locales for x in languages):
            c.add(E_ENUM, "$.meta.languages", f"non-empty, unique, subset of {list(p.locales)}")
    for repo in meta.get("repos", []) if isinstance(meta.get("repos"), list) else []:
        if not (isinstance(repo, str) and TOKEN_RE.fullmatch(repo)):
            c.add(E_ID, "$.meta.repos", f"bad repository alias {repo!r}")

    space = model.get("space")
    if not isinstance(space, dict) or tuple(space) != SPACE_PARTS:
        c.add(E_SHAPE, "$.space", f"must be an object with keys {list(SPACE_PARTS)} in this order")
        space = {part: [] for part in SPACE_PARTS}
    buildings = _records(space, "buildings", "$.space.buildings", c, "building")
    floors = _records(space, "floors", "$.space.floors", c, "floor")
    rooms = _records(space, "rooms", "$.space.rooms", c, "room")
    nodes = _records(model, "nodes", "$.nodes", c, "node")
    edges = _records(model, "edges", "$.edges", c, "edge")
    ports = _records(model, "ports", "$.ports", c, "port")
    evidences = _records(model, "evidences", "$.evidences", c, "evidence")
    machines = _records(model, "machines", "$.machines", c, "machine")
    cases = _records(model, "cases", "$.cases", c, "case")
    interventions = _records(model, "interventions", "$.interventions", c, "intervention")
    requirements = _records(model, "requirements", "$.requirements", c, "requirement")
    tests = _records(model, "tests", "$.tests", c, "test")
    tbds = _records(model, "tbds", "$.tbds", c, "tbd")
    policies = _records(model, "policies", "$.policies", c, "policy")

    # ---- ids: global uniqueness of authored ids, then derived ids must not collide with them
    owners: dict[str, str] = {}

    def claim(identifier: str, where: str) -> None:
        if identifier in owners:
            c.add(E_DUP, where, f"id {identifier!r} is already used at {owners[identifier]}")
        else:
            owners[identifier] = where

    for index, r in enumerate(buildings):
        claim(r["id"], f"$.space.buildings[{index}].id")
    for index, r in enumerate(floors):
        claim(r["id"], f"$.space.floors[{index}].id")
    for index, r in enumerate(rooms):
        claim(r["id"], f"$.space.rooms[{index}].id")
    for index, r in enumerate(nodes):
        claim(r["id"], f"$.nodes[{index}].id")
    for index, r in enumerate(edges):
        claim(r["id"], f"$.edges[{index}].id")
    for index, r in enumerate(evidences):
        claim(r["evidenceId"], f"$.evidences[{index}].evidenceId")
    for index, r in enumerate(cases):
        claim(r["caseId"], f"$.cases[{index}].caseId")
    for index, r in enumerate(interventions):
        claim(r["interventionId"], f"$.interventions[{index}].interventionId")
    for index, r in enumerate(requirements):
        claim(r["id"], f"$.requirements[{index}].id")
    for index, r in enumerate(tests):
        claim(r["id"], f"$.tests[{index}].id")
    for index, r in enumerate(tbds):
        claim(r["id"], f"$.tbds[{index}].id")
    for index, r in enumerate(policies):
        claim(r["id"], f"$.policies[{index}].id")
    for index, r in enumerate(ports):
        claim(r["id"], f"$.ports[{index}].id")

    building_ids = {r["id"] for r in buildings}
    floor_ids = {r["id"] for r in floors}
    room_by_id = {r["id"]: r for r in rooms}
    node_ids = {r["id"] for r in nodes}
    edge_ids = {r["id"] for r in edges}
    evidence_by_id = {r["evidenceId"]: r for r in evidences}
    machine_ids = {r["id"] for r in machines}
    case_ids = {r["caseId"] for r in cases}
    requirement_ids = {r["id"] for r in requirements}
    test_ids = {r["id"] for r in tests}
    tbd_ids = {r["id"] for r in tbds}

    ref, refs = c.ref, c.refs

    # ---- space
    for index, r in enumerate(buildings):
        c.enum("trust", r["trust"], f"$.space.buildings[{index}].trust")
    for index, r in enumerate(floors):
        ref(r["buildingId"], building_ids, f"$.space.floors[{index}].buildingId", "building")
    for index, r in enumerate(rooms):
        ref(r["floorId"], floor_ids, f"$.space.rooms[{index}].floorId", "floor")
        if "layer" in r:
            c.enum("layers", r["layer"], f"$.space.rooms[{index}].layer")
        if "tier" in r:
            c.enum("tiers", r["tier"], f"$.space.rooms[{index}].tier")

    # ---- nodes and ports
    floor_by_id = {r["id"]: r for r in floors}
    building_by_id = {r["id"]: r for r in buildings}
    for index, r in enumerate(nodes):
        path = f"$.nodes[{index}]"
        c.enum("kinds", r["kind"], f"{path}.kind")
        kind_item = p.item("kinds", r["kind"])
        if kind_item and not kind_item["authorable"]:
            c.add(E_ENUM, f"{path}.kind", f"kind {r['kind']!r} is scenario-only and cannot be a node")
        ref(r["roomId"], set(room_by_id), f"{path}.roomId", "room")
        refs(r["evidenceIds"], set(evidence_by_id), f"{path}.evidenceIds", "evidence")
        c.enum("verification", r["verification"], f"{path}.verification")
        if "level" in r:
            c.enum("levels", r["level"], f"{path}.level")
        if r["portIds"] != port_ids(r["id"], r["id"] in machine_ids):
            c.add(E_DERIVED, f"{path}.portIds", f"must be {port_ids(r['id'], r['id'] in machine_ids)}, got {r['portIds']}")
        _inheritance(c, r, room_by_id.get(r["roomId"]), floor_by_id, building_by_id, path)
        if r["verification"] == "Implemented" and "source-code" not in evidence_kinds_of(r["evidenceIds"], evidence_by_id):
            c.add(E_CLAIM, f"{path}.verification", "Implemented needs at least one source-code evidence")
    expected_ports = derive_ports([r for r in nodes], machine_ids, p.record_kinds)
    if ports != expected_ports:
        c.add(E_DERIVED, "$.ports", f"must equal the ports derived from nodes and machines ({len(expected_ports)} expected, {len(ports)} found)")

    # ---- edges
    for index, r in enumerate(edges):
        path = f"$.edges[{index}]"
        ref(r["sourceId"], node_ids, f"{path}.sourceId", "node")
        ref(r["targetId"], node_ids, f"{path}.targetId", "node")
        refs(r["evidenceIds"], set(evidence_by_id), f"{path}.evidenceIds", "evidence")
        c.enum("relations", r["relation"], f"{path}.relation")
        c.enum("verification", r["verification"], f"{path}.verification")
        if r["type"] != "directed" or r["directed"] is not True or r["guardId"] is not None:
            c.add(E_DERIVED, path, "type must be 'directed', directed true, guardId null")
        item = p.item("relations", r["relation"])
        if item and r["static"] != item["static"]:
            c.add(E_DERIVED, f"{path}.static", f"must be {item['static']} for relation {r['relation']!r}")
        expected = edge_ports(r["sourceId"], r["targetId"], machine_ids)
        if (r["sourcePortId"], r["targetPortId"]) != expected:
            c.add(E_DERIVED, path, f"ports must be {expected}, got {(r['sourcePortId'], r['targetPortId'])}")
        if "cardinality" in r and not CARDINALITY_RE.fullmatch(r["cardinality"]):
            c.add(E_FIELD, f"{path}.cardinality", f"bad cardinality {r['cardinality']!r}")
        if r["id"] != _safe_edge_id(r) and not is_authored_id(r["id"]):
            c.add(E_ID, f"{path}.id", "an edge id is the derived src:rel:tgt (or its hash id) or an alias without ':'")
        if r["verification"] == "Implemented" and "source-code" not in evidence_kinds_of(r["evidenceIds"], evidence_by_id):
            c.add(E_CLAIM, f"{path}.verification", "Implemented needs at least one source-code evidence")
        # A repeated triple derives the same id twice (global uniqueness above reports it); the second edge needs an alias.

    # ---- evidences
    for index, r in enumerate(evidences):
        path = f"$.evidences[{index}]"
        kind = evidence_kind(r["filePath"], profile=p)
        c.enum("sourceKinds", r["sourceKind"], f"{path}.sourceKind")
        if r["sourceKind"] != kind:
            c.add(E_DERIVED, f"{path}.sourceKind", f"must be {kind!r} for file {r['filePath']!r}")
        expected_level = evidence_verification(r["sourceKind"], mode if isinstance(mode, str) else "asis", profile=p)
        if r["verification"] != expected_level:
            c.add(E_DERIVED, f"{path}.verification", f"must be {expected_level!r} for kind {r['sourceKind']!r}")
        if r["sourceKind"] == "user-requirement" and r["repository"] != "":
            c.add(E_DERIVED, f"{path}.repository", "user-requirement evidence has no repository")
        if r["repository"] and not TOKEN_RE.fullmatch(r["repository"]):
            c.add(E_ID, f"{path}.repository", "bad repository alias")

    # ---- machines
    for index, r in enumerate(machines):
        path = f"$.machines[{index}]"
        if r["nodeId"] != r["id"]:
            c.add(E_DERIVED, f"{path}.nodeId", "must equal id")
        ref(r["id"], node_ids, f"{path}.id", "node")
        refs(r["evidenceIds"], set(evidence_by_id), f"{path}.evidenceIds", "evidence")
        transitions = []
        for t_index, t in enumerate(r["transitions"]):
            if c.keys(t, _SPECS["transition"], f"{path}.transitions[{t_index}]"):
                transitions.append(t)
                if "guard" in t:
                    _check_guard(c, t["guard"], f"{path}.transitions[{t_index}].guard")
        ids = [t["id"] for t in transitions]
        for dup in {i for i, n in Counter(ids).items() if n > 1}:
            c.add(E_MACHINE, f"{path}.transitions", f"duplicate transition id {dup!r}")
        states = machine_states(r["initial"], transitions)
        if r["states"] != states:
            c.add(E_DERIVED, f"{path}.states", f"must be {states}, got {r['states']}")
        terminal = machine_terminal(states, transitions)
        if r["terminal"] != terminal:
            c.add(E_DERIVED, f"{path}.terminal", f"must be {terminal}, got {r['terminal']}")
    # ---- cases
    machine_states_by_id = {r["id"]: r["states"] for r in machines}
    node_state_ids = set(p.ids("nodeStates"))
    c.node_ids, c.machine_states = node_ids, machine_states_by_id
    for index, r in enumerate(cases):
        path = f"$.cases[{index}]"
        c.enum("caseCategories", r["category"], f"{path}.category")
        refs(r["requirementIds"], requirement_ids, f"{path}.requirementIds", "requirement")
        refs(r["evidenceIds"], set(evidence_by_id), f"{path}.evidenceIds", "evidence")
        refs(r["linkedTestIds"], test_ids, f"{path}.linkedTestIds", "test")
        if set(r["policy"]) != set(POLICY_KEYS):
            c.add(E_DERIVED, f"{path}.policy", f"must have exactly the keys {list(POLICY_KEYS)}")
        else:
            for key in POLICY_KEYS:
                v = r["policy"].get(key)
                if isinstance(v, bool) or not isinstance(v, int) or v < (1 if key == "timeoutMs" else 0):
                    c.add(E_FIELD, f"{path}.policy.{key}", "must be a non-negative integer (timeoutMs >= 1)")
        for node_id, state in r["expectedTerminalState"].items():
            ref(node_id, node_ids, f"{path}.expectedTerminalState.{node_id}", "node")
            allowed = set(machine_states_by_id[node_id]) if node_id in machine_states_by_id else node_state_ids
            if not isinstance(state, str) or state not in allowed:
                c.add(E_ENUM, f"{path}.expectedTerminalState.{node_id}", f"{state!r} not in {sorted(allowed)}")
        for node_id, state in r.get("initialMachines", {}).items():
            if node_id not in machine_states_by_id:
                c.add(E_REF, f"{path}.initialMachines.{node_id}", "not a machine node")
            elif state not in machine_states_by_id[node_id]:
                c.add(E_ENUM, f"{path}.initialMachines.{node_id}", f"{state!r} is not a state of the machine")
        for o_index, outcome in enumerate(r.get("expectedOutcomes", [])):
            opath = f"{path}.expectedOutcomes[{o_index}]"
            if not isinstance(outcome, dict) or not isinstance(outcome.get("ruleId"), str) or set(outcome) - {"ruleId", "outcome", "errorCode"}:
                c.add(E_FIELD, opath, "expected {ruleId, outcome, [errorCode]}")
            else:
                c.enum("eventOutcomes", outcome.get("outcome"), f"{opath}.outcome")
        if "expectedBy" in r:
            c.enum_plain("expectBy", r["expectedBy"], f"{path}.expectedBy")
        for e_index, event in enumerate(r["events"]):
            epath = f"{path}.events[{e_index}]"
            if not c.keys(event, _EVENT_SPEC, epath):
                continue
            if event["seq"] != e_index + 1:
                c.add(E_DERIVED, f"{epath}.seq", f"must be {e_index + 1}")
            c.enum("eventTypes", event["type"], f"{epath}.type")
            ref(event["from"], node_ids, f"{epath}.from", "node")
            ref(event["to"], node_ids, f"{epath}.to", "node")
            _check_event_options(c, event, epath)
            if "edge" in event:
                ref(event["edge"], edge_ids, f"{epath}.edge", "edge")
            if "role" in event:
                c.enum_plain("eventRoles", event["role"], f"{epath}.role")
            if "fault" in event:
                fault = event["fault"]
                if not isinstance(fault, dict) or fault.get("mode") not in p.enums["faultModes"]:
                    c.add(E_ENUM, f"{epath}.fault.mode", f"must be one of {list(p.enums['faultModes'])}")
            if "latencyMs" in event:
                lat = event["latencyMs"]
                values = lat if isinstance(lat, list) else [lat]
                if not values or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in values):
                    c.add(E_FIELD, f"{epath}.latencyMs", "must be a non-negative integer or a non-empty list of them")

    # ---- interventions
    ctx = _Ctx(node_ids, edge_ids, edges, machines, cases)
    for index, r in enumerate(interventions):
        _check_intervention(c, r, f"$.interventions[{index}]", ctx)

    # ---- requirements, tbds, policies
    for index, r in enumerate(requirements):
        path = f"$.requirements[{index}]"
        refs(r["nodeIds"], node_ids, f"{path}.nodeIds", "node")
        refs(r["edgeIds"], edge_ids, f"{path}.edgeIds", "edge")
        refs(r["evidenceIds"], set(evidence_by_id), f"{path}.evidenceIds", "evidence")
        refs(r["tstIds"], test_ids, f"{path}.tstIds", "test")
        refs(r["tbdIds"], tbd_ids, f"{path}.tbdIds", "tbd")
    for index, r in enumerate(tbds):
        refs(r["evidenceIds"], set(evidence_by_id), f"$.tbds[{index}].evidenceIds", "evidence")
    for index, r in enumerate(policies):
        path = f"$.policies[{index}]"
        ref(r["src"], requirement_ids | tbd_ids, f"{path}.src", "requirement or tbd")
        if not SCOPE_RE.fullmatch(r["scope"]):
            c.add(E_FIELD, f"{path}.scope", "must be all, kind:<Kind>, node:<id> or req:<id>")
        elif r["scope"].startswith("kind:"):
            c.enum("kinds", r["scope"][5:], f"{path}.scope")
        elif r["scope"].startswith("node:"):
            ref(r["scope"][5:], node_ids, f"{path}.scope", "node")
        elif r["scope"].startswith("req:"):
            ref(r["scope"][4:], requirement_ids, f"{path}.scope", "requirement")
        c.enum("levels", r["level"], f"{path}.level")
        c.enum("policyKinds", r["kind"], f"{path}.kind")
        c.enum_plain("policyBy", r["by"], f"{path}.by")
        if r["chapter"] is not None and not 1 <= r["chapter"] <= 20:
            c.add(E_FIELD, f"{path}.chapter", "must be null or 1..20")
    return c.issues


def _safe_edge_id(edge: Mapping) -> str | None:
    try:
        return edge_id(edge["sourceId"], edge["relation"], edge["targetId"])
    except (AtlasModelError, KeyError):
        return None


def _inheritance(c: _Checker, node: Mapping, room, floor_by_id, building_by_id, path: str) -> None:
    if room is None:
        return
    props = node["props"]
    for key in ("tier", "area"):
        if key in room and key not in props:
            c.add(E_DERIVED, f"{path}.props.{key}", f"room {key} {room[key]!r} must be inherited into props")
    floor = floor_by_id.get(room["floorId"])
    building = building_by_id.get(floor["buildingId"]) if floor else None
    if building is not None and "trust" not in props:
        c.add(E_DERIVED, f"{path}.props.trust", f"building trust {building['trust']!r} must be inherited into props")


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _non_empty_str(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _check_guard(c: _Checker, g, path: str, depth: int = 0) -> None:
    """Shape of a guard: ``{param, op, [value]}`` or ``{all: [...]}``, ``{any: [...]}``, ``{not: guard}``."""
    p = c.p
    if depth > MAX_GUARD_DEPTH:
        c.add(E_SHAPE, path, f"guard nested deeper than {MAX_GUARD_DEPTH} levels")
        return
    if not isinstance(g, dict):
        c.add(E_FIELD, path, "guard must be an object")
        return
    combinators = [key for key in p.enums["guardCombinators"] if key in g]
    if combinators:
        key = combinators[0]
        if len(g) != 1:
            c.add(E_FIELD, path, f"a combinator guard has exactly one key ({key}), got {sorted(g)}")
        if key == "not":
            _check_guard(c, g["not"], f"{path}.not", depth + 1)
        elif not isinstance(g[key], list) or not g[key]:
            c.add(E_FIELD, f"{path}.{key}", "must be a non-empty array of guards")
        else:
            for index, item in enumerate(g[key]):
                _check_guard(c, item, f"{path}.{key}[{index}]", depth + 1)
        return
    extra = sorted(set(g) - {"param", "op", "value"})
    if extra:
        c.add(E_FIELD, path, f"unknown guard keys {extra}")
    if not _non_empty_str(g.get("param")):
        c.add(E_FIELD, f"{path}.param", "must be a non-empty string")
    op = g.get("op")
    if not isinstance(op, str) or op not in p.enums["guardOps"]:
        c.add(E_ENUM, f"{path}.op", f"{op!r} is not in enums.guardOps: {', '.join(p.enums['guardOps'])}")
        return
    if op in ("present", "absent"):
        if "value" in g:
            c.add(E_FIELD, f"{path}.value", f"op {op!r} takes no value")
        return
    if "value" not in g:
        c.add(E_FIELD, f"{path}.value", f"op {op!r} needs a value")
        return
    value = g["value"]
    context_ref = isinstance(value, str) and value.startswith("$ctx.")
    if context_ref and len(value) == 5:
        c.add(E_FIELD, f"{path}.value", "a $ctx reference needs a key after '$ctx.'")
    elif op == "in":
        if not (isinstance(value, list) or context_ref):
            c.add(E_FIELD, f"{path}.value", "op 'in' needs an array (or a $ctx.<key> reference)")
    elif not isinstance(value, (str, int, float, bool)):
        c.add(E_FIELD, f"{path}.value", f"op {op!r} needs a scalar value or a $ctx.<key> reference")


def _check_when(c: _Checker, w, path: str, depth: int = 0) -> None:
    """Shape of an event ``when``: node condition, ``all`` / ``any`` / ``not``, ``elementState`` or a parameter guard."""
    if depth > MAX_GUARD_DEPTH:
        c.add(E_SHAPE, path, f"when nested deeper than {MAX_GUARD_DEPTH} levels")
        return
    if not isinstance(w, dict):
        c.add(E_FIELD, path, "when must be an object")
        return
    if "all" in w or "any" in w:
        key = "all" if "all" in w else "any"
        if len(w) != 1:
            c.add(E_FIELD, path, f"a {key} condition has exactly one key, got {sorted(w)}")
        if not isinstance(w[key], list) or not w[key]:
            c.add(E_FIELD, f"{path}.{key}", "must be a non-empty array of conditions")
        else:
            for index, item in enumerate(w[key]):
                _check_when(c, item, f"{path}.{key}[{index}]", depth + 1)
        return
    if "node" in w:
        node = w["node"]
        c.ref(node, c.node_ids, f"{path}.node", "node")
        extra = sorted(set(w) - {"node", "is", "not"})
        if extra:
            c.add(E_FIELD, path, f"unknown condition keys {extra}")
        modes = [key for key in ("is", "not") if key in w]
        if len(modes) != 1:
            c.add(E_FIELD, path, "a node condition has exactly one of 'is' and 'not'")
            return
        states = w[modes[0]]
        if not isinstance(states, list) or not states:
            c.add(E_FIELD, f"{path}.{modes[0]}", "must be a non-empty array of states")
            return
        allowed = set(c.machine_states[node]) if isinstance(node, str) and node in c.machine_states else c.node_states
        for index, state in enumerate(states):
            if not isinstance(state, str) or state not in allowed:
                c.add(E_ENUM, f"{path}.{modes[0]}[{index}]", f"{state!r} is not one of {sorted(allowed)}")
        return
    if "not" in w:
        if len(w) != 1:
            c.add(E_FIELD, path, f"a not condition has exactly one key, got {sorted(w)}")
        _check_when(c, w["not"], f"{path}.not", depth + 1)
        return
    if "elementState" in w:
        if len(w) != 1 or not _non_empty_str(w["elementState"]):
            c.add(E_FIELD, path, "{elementState} takes exactly one non-empty string")
        return
    if "param" in w:
        _check_guard(c, w, path, depth)
        return
    c.add(E_FIELD, path, "when must be {node, is|not}, {all|any: [...]}, {not: when}, {elementState} or a parameter guard")


def _check_event_options(c: _Checker, event: Mapping, path: str) -> None:
    """Types and values of the optional event fields (``latencyMs``, ``fault.mode`` and refs are checked by the caller)."""
    for key in ("ack", "section"):
        if key in event and not isinstance(event[key], bool):
            c.add(E_FIELD, f"{path}.{key}", f"must be true or false, got {event[key]!r}")
    for key in ("ruleId", "group", "dedupKey", "errorCode"):
        if key in event and not _non_empty_str(event[key]):
            c.add(E_FIELD, f"{path}.{key}", f"must be a non-empty string, got {event[key]!r}")
    if "params" in event and not isinstance(event["params"], dict):
        c.add(E_FIELD, f"{path}.params", "must be an object")
    if "ms" in event and (not _is_int(event["ms"]) or event["ms"] < 0):
        c.add(E_FIELD, f"{path}.ms", "must be a non-negative integer")
    if "fault" in event and isinstance(event["fault"], dict):
        fault = event["fault"]
        for key in sorted(set(fault) - {"mode", "errorCode", "httpStatus"}):
            c.add(E_FIELD, f"{path}.fault.{key}", "unknown fault field (allowed: mode, errorCode, httpStatus)")
        if "errorCode" in fault and not _non_empty_str(fault["errorCode"]):
            c.add(E_FIELD, f"{path}.fault.errorCode", "must be a non-empty string")
        if "httpStatus" in fault and (not _is_int(fault["httpStatus"]) or not 100 <= fault["httpStatus"] <= 599):
            c.add(E_FIELD, f"{path}.fault.httpStatus", "must be an HTTP status 100..599")
    if "when" in event:
        _check_when(c, event["when"], f"{path}.when")
    if "guard" in event:
        _check_guard(c, event["guard"], f"{path}.guard")


class _Ctx:
    """Lookup tables shared by the intervention checks."""

    def __init__(self, node_ids, edge_ids, edges, machines, cases):
        self.node_ids = node_ids
        self.edge_ids = edge_ids
        self.edges = edges
        self.machine_by_id = {m["id"]: m for m in machines}
        self.case_by_id = {k["caseId"]: k for k in cases}


def _check_intervention(c: _Checker, r: Mapping, path: str, ctx: _Ctx) -> None:
    p = c.p
    c.enum("interventionTypes", r["type"], f"{path}.type")
    item = p.item("interventionTypes", r["type"])
    applies = r["appliesTo"]
    if not applies:
        c.add(E_FIELD, f"{path}.appliesTo", "must name at least one case (an intervention that applies to nothing cannot be run)")
    for index, case_id in enumerate(applies):
        if case_id not in ctx.case_by_id:
            c.add(E_REF, f"{path}.appliesTo[{index}]", f"case {case_id!r} is not defined")
    expect = r["expect"]
    extra = set(expect) - {"outcomeClass", "terminal", "status"}
    if extra:
        c.add(E_FIELD, f"{path}.expect", f"unknown keys {sorted(extra)}")
    if "outcomeClass" in expect:
        c.enum("outcomeClasses", expect["outcomeClass"], f"{path}.expect.outcomeClass")
    if "status" in expect:
        c.enum_plain("runStatuses", expect["status"], f"{path}.expect.status")
    elements: dict[str, Mapping] = {}
    if item is not None:
        allowed = set(item["required"]) | set(item["optional"])
        for key in r:
            if key in ATOMIC_PARAM_KEYS and key not in allowed:
                c.add(E_FIELD, f"{path}.{key}", f"not a parameter of {r['type']}")
        for key in item["required"]:
            if key not in r:
                c.add(E_FIELD, f"{path}.{key}", f"required by {r['type']}")
        problems: list[tuple[str, str]] = []
        applied = [ctx.case_by_id[i] for i in dict.fromkeys(applies) if i in ctx.case_by_id]
        rule_ids = {e["ruleId"] for k in applied for e in k["events"] if isinstance(e, dict) and isinstance(e.get("ruleId"), str)}
        for apath, atom, is_part in _walk_atoms(r, path, 0, problems):
            _check_atom(c, atom, apath, is_part, ctx, applies, rule_ids, elements)
        for ppath, message in problems:
            c.add(E_FIELD, ppath, message)
    if "terminal" in expect:
        _check_expected_terminal(c, expect["terminal"], f"{path}.expect.terminal", ctx, elements)


def _check_expected_terminal(c: _Checker, terminal, path: str, ctx: _Ctx, elements: Mapping[str, Mapping]) -> None:
    if not isinstance(terminal, dict):
        c.add(E_FIELD, path, "must be an object")
        return
    for node_id, state in terminal.items():
        if node_id in ctx.node_ids:
            allowed = set(c.machine_states[node_id]) if node_id in c.machine_states else c.node_states
        elif node_id in elements:
            machine = elements[node_id].get("stateMachine")
            own = machine.get("states") if isinstance(machine, dict) else None
            allowed = c.node_states | {s for s in own if isinstance(s, str)} if isinstance(own, list) else set(c.node_states)
        else:
            c.add(E_REF, f"{path}.{node_id}", "node or inserted element is not defined")
            continue
        if not isinstance(state, str):
            c.add(E_FIELD, f"{path}.{node_id}", f"state must be a string, got {state!r}")
        elif state not in allowed:
            c.add(E_ENUM, f"{path}.{node_id}", f"{state!r} is not one of {sorted(allowed)}")


def _check_atom(c: _Checker, atom, path: str, is_part: bool, ctx: _Ctx, applies, rule_ids, elements: dict) -> None:
    """Parameters of one atomic intervention (the top-level record or one flattened part of a combined one)."""
    p = c.p
    if not isinstance(atom, dict):
        c.add(E_ENUM, path, "part must be an object with a known atomic intervention type")
        return
    item = p.item("interventionTypes", atom.get("type"))
    if item is None or not item["atomic"]:
        c.add(E_ENUM, path, f"part needs a known atomic intervention type, got {atom.get('type')!r}")
        return
    kind = atom["type"]
    if is_part:
        allowed = set(item["required"]) | set(item["optional"]) | {"type"}
        for key in atom:
            if key not in allowed:
                c.add(E_FIELD, f"{path}.{key}", f"not a parameter of {kind}")
        for key in item["required"]:
            if key not in atom:
                c.add(E_FIELD, f"{path}.{key}", f"required by {kind}")
        if kind == "delay_message" and not any(k in atom for k in ("targetEdgeId", "targetEventId", "targetRuleId")):
            c.add(E_FIELD, path, "delay_message needs targetEdgeId, targetEventId or targetRuleId")
    elif kind == "delay_message" and not any(k in atom for k in ("targetEdgeId", "targetEventId", "targetRuleId")):
        c.add(E_FIELD, path, "delay_message needs targetEdgeId, targetEventId or targetRuleId")
    if "targetNodeId" in atom:
        c.ref(atom["targetNodeId"], ctx.node_ids, f"{path}.targetNodeId", "node")
    if "targetEdgeId" in atom:
        c.ref(atom["targetEdgeId"], ctx.edge_ids, f"{path}.targetEdgeId", "edge")
    bounds = (("timeoutMs", 1), ("retryMax", 0), ("retryBackoffMs", 0), ("addLatencyMs", 0))
    for key, low in bounds:
        if key in atom and (not _is_int(atom[key]) or atom[key] < low):
            c.add(E_FIELD, f"{path}.{key}", f"must be an integer >= {low}, got {atom[key]!r}")
    if "targetEventId" in atom:
        parsed = parse_event_id(atom["targetEventId"])
        if parsed is None:
            c.add(E_FIELD, f"{path}.targetEventId", f"must be '<caseId>{EVENT_ID_SEP}<seq>' with seq counting from 1, got {atom['targetEventId']!r}")
        else:
            case_id, seq = parsed
            case = ctx.case_by_id.get(case_id)
            if case is None:
                c.add(E_REF, f"{path}.targetEventId", f"case {case_id!r} is not defined")
            elif not 1 <= seq <= len(case["events"]):
                c.add(E_REF, f"{path}.targetEventId", f"case {case_id!r} has {len(case['events'])} events, there is no event {seq}")
            elif case_id not in applies:
                c.add(E_REF, f"{path}.targetEventId", f"case {case_id!r} is not in appliesTo, the delay would never apply")
    if "targetRuleId" in atom:
        rule = atom["targetRuleId"]
        if not _non_empty_str(rule):
            c.add(E_FIELD, f"{path}.targetRuleId", "must be a non-empty string")
        elif rule not in rule_ids:
            c.add(E_REF, f"{path}.targetRuleId", f"no event of the applied cases has ruleId {rule!r}")
    if kind == "change_guard":
        machine = ctx.machine_by_id.get(atom["machineId"]) if isinstance(atom.get("machineId"), str) else None
        if "machineId" in atom and machine is None:
            c.add(E_REF, f"{path}.machineId", f"machine {atom['machineId']!r} is not defined")
        elif machine is not None and "transitionId" in atom and atom["transitionId"] not in [
                t.get("id") for t in machine["transitions"] if isinstance(t, dict)]:
            c.add(E_REF, f"{path}.transitionId", f"transition {atom['transitionId']!r} is not defined")
        if "guard" in atom and atom["guard"] is not None:
            _check_guard(c, atom["guard"], f"{path}.guard")
    if kind == "insert_element":
        _check_element(c, atom, path, ctx, elements)


def _check_element(c: _Checker, r: Mapping, path: str, ctx: _Ctx, elements: dict) -> None:
    p = c.p
    element = r.get("element")
    if not isinstance(element, dict):
        c.add(E_FIELD, f"{path}.element", "must be an object")
        return
    element_id = element.get("elementId")
    if not is_authored_id(element_id):
        c.add(E_ID, f"{path}.element.elementId", "must match the authored id grammar")
        return
    if element_id in ctx.node_ids:
        c.add(E_DUP, f"{path}.element.elementId", "collides with a model node")
    elif element_id in elements:
        c.add(E_DUP, f"{path}.element.elementId", "this intervention already inserts an element with that id")
    else:
        elements[element_id] = element
    c.enum("elementKinds", element.get("kind"), f"{path}.element.kind")
    if "latencyMs" in element:
        lat = element["latencyMs"]
        if lat is not None and (isinstance(lat, bool) or not isinstance(lat, (int, float)) or lat != lat or lat in (float("inf"), float("-inf")) or lat < 0):
            c.add(E_FIELD, f"{path}.element.latencyMs", "must be a finite non-negative number")
    for handler in element.get("eventHandlers", []) if isinstance(element.get("eventHandlers"), list) else []:
        if isinstance(handler, dict) and handler.get("do") not in p.enums["handlerActions"]:
            c.add(E_ENUM, f"{path}.element.eventHandlers", f"action {handler.get('do')!r} not in enums.handlerActions")
    for key, direction in (("inputPorts", "in"), ("outputPorts", "out")):
        if key not in element:
            continue
        ports = element[key]
        if not isinstance(ports, list):
            c.add(E_FIELD, f"{path}.element.{key}", "must be an array of {id, type}")
            continue
        for index, port in enumerate(ports):
            ppath = f"{path}.element.{key}[{index}]"
            if not isinstance(port, dict) or port.get("id") != port_id(element_id, direction):
                c.add(E_DERIVED, ppath, f"an element port id is fixed: {port_id(element_id, direction)!r}")
            elif port.get("type") not in p.enums["elementPortTypes"] or set(port) - {"id", "type"}:
                c.add(E_ENUM, ppath, f"port type must be one of {list(p.enums['elementPortTypes'])} (keys: id, type)")
    edge_by_id = {e["id"]: e for e in ctx.edges}
    rewrites = r.get("rewrite")
    if not isinstance(rewrites, list):
        c.add(E_FIELD, f"{path}.rewrite", "must be an array (it may be empty for a draft element)")
        return
    for index, rewrite in enumerate(rewrites):
        rpath = f"{path}.rewrite[{index}]"
        if not isinstance(rewrite, dict) or not isinstance(rewrite.get("replaceEdgeId"), str) or rewrite["replaceEdgeId"] not in edge_by_id:
            c.add(E_REF, f"{rpath}.replaceEdgeId", "edge is not defined")
            continue
        c.enum_plain("dispositions", rewrite.get("disposition"), f"{rpath}.disposition")
        try:
            expected = build_rewrite_entry(element_id, index, edge_by_id[rewrite["replaceEdgeId"]], rewrite.get("disposition", "replace"))
        except AtlasModelError as ex:
            c.add(E_FIELD, rpath, f"cannot derive the chain: {ex.message}")
            continue
        if rewrite != expected:
            c.add(E_DERIVED, rpath, "chain must equal the derived chain " + canonical_dumps(expected["chain"]))


def check_contract(model: Mapping, profile=None) -> None:
    """Raise ``AtlasModelError`` listing the first issues when the model breaks the contract."""
    issues = contract_issues(model, profile)
    if issues:
        shown = "; ".join(f"{i['path']}: {i['message']}" for i in issues[:5])
        raise AtlasModelError(E_SHAPE, f"{len(issues)} contract issue(s): {shown}")
