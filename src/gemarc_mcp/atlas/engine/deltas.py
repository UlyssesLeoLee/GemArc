"""Model index, intervention compilation, element validation and rewrite validation.

Semantic port of the reference delta compiler (nine atomic intervention types plus ``combined_intervention``).
An intervention is validated BEFORE any computation:

* ``OK``      every part is consistent with the model;
* ``DRAFT``   only inserted elements are incomplete (no ports, no state machine ...) or not connected yet (a
              complete body with an empty ``rewrite``, spec 4.9): not computable, not an error;
* ``INVALID`` anything else (unknown target, bad number, broken rewrite chain, unknown type).

Error strings are the contract of this module: the rules layer and the tests read them verbatim.

Contract-driven differences from the reference (all recorded in ``tests/atlas/golden/deviations.json``):

* a rewrite entry with disposition ``keep`` or ``disable`` has ``chain == []`` and only the edge checks run; the chain
  checks run for ``replace`` only (spec 4.9);
* element kinds are the profile ids (``NewService``), edges say whether they are ``static`` instead of the engine
  looking up relation names;
* an inserted element with a complete body and an empty ``rewrite`` is a ``DRAFT`` (spec 4.9: "a draft element is not
  connected yet"); the reference reported ``INVALID`` (deviation C8);
* ``change_retry.retryMax`` above ``MAX_RETRY`` and a non-numeric element ``latencyMs`` are ``INVALID`` (engine
  hardening, the retry loop is also bounded by ``maxEvents``);
* a ``combined_intervention`` is flattened with ``model.flatten_parts`` (depth-first, nesting at most
  ``MAX_PART_DEPTH``); a combined record the contract would reject becomes an ``INVALID`` error instead of being
  silently dropped or crashing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .. import model as am
from .. import vocab
from .types import (HANDLER_ACTIONS, MAX_RETRY, UNDEF, as_number, contract_guard, get, is_number, js_string, nullish, num_ge, num_gt, truthy)

ATOMIC_TYPES = ("disable_module", "disable_edge", "resource_failure", "identity_failure", "change_timeout",
                "change_retry", "delay_message", "change_guard", "insert_element")
COMBINED = "combined_intervention"
DISPOSITIONS = ("replace", "keep", "disable")


# --------------------------------------------------------------------------------------------------------
# Index
# --------------------------------------------------------------------------------------------------------


class Index:
    """Lookup tables over an expanded model (the model itself is never modified)."""

    def __init__(self, model: Mapping, profile: "vocab.Profile | None" = None):
        self.model = model
        self.profile = profile or vocab.default_profile()
        self.node_list = list(model.get("nodes", []))
        self.edge_list = list(model.get("edges", []))
        self.nodes = {n["id"]: n for n in self.node_list}
        self.edges = {e["id"]: e for e in self.edge_list}
        self.ports = {p["id"]: p for p in model.get("ports", [])}
        self.machines = {m["id"]: m for m in model.get("machines", [])}
        self.cases = {c["caseId"]: c for c in model.get("cases", [])}
        self._version: tuple | None = None
        self._runtime_edges: list | None = None

    @property
    def version(self) -> str | None:
        """Version stamp of the run results: ``model_sha8`` (the contract has no hand-written revision fields)."""
        if self._version is None:
            try:
                self._version = (am.model_sha8(self.model),)
            except (am.AtlasModelError, UnicodeError, ValueError, TypeError):
                # a model that cannot be hashed (a lone surrogate in a string, a non-JSON value) has no stamp
                self._version = (None,)
        return self._version[0]

    def is_static(self, edge: Mapping) -> bool:
        """Static edges never carry runtime events; ``edge.static`` decides, the profile is only a fallback."""
        flag = edge.get("static")
        if isinstance(flag, bool):
            return flag
        return edge.get("relation") in self.profile.static_relations

    @property
    def runtime_edges(self) -> list:
        if self._runtime_edges is None:
            self._runtime_edges = [e for e in self.edge_list if not self.is_static(e)]
        return self._runtime_edges


@contract_guard
def index_model(model: Mapping, profile: "vocab.Profile | None" = None) -> Index:
    return Index(model, profile)


# --------------------------------------------------------------------------------------------------------
# Elements and rewrites
# --------------------------------------------------------------------------------------------------------


def validate_element(el, ix: Index) -> list[str]:
    """Why an inserted element body is incomplete or inconsistent (empty list = valid)."""
    errors: list[str] = []

    def miss(key: str) -> None:
        errors.append(f"missing {key}")

    if not truthy(el) or not isinstance(el, (Mapping, list)):
        return ["element is not an object"]
    if isinstance(el, list):
        el = {}
    element_id = el.get("elementId")
    if not truthy(element_id) or not isinstance(element_id, str):
        miss("elementId")
    elif element_id in ix.nodes:
        errors.append(f"elementId {element_id} collides with a model node")
    kind = el.get("kind", UNDEF)
    if not (isinstance(kind, str) and ix.profile.has("elementKinds", kind)):
        errors.append(f"kind {js_string(kind)} is not an allowed element kind")
    if not truthy(el.get("role")):
        miss("role")
    for key in ("inputPorts", "outputPorts"):
        ports = el.get(key)
        if not isinstance(ports, list) or len(ports) == 0:
            miss(key)
        else:
            for p in ports:
                if not (isinstance(p, Mapping) and truthy(p.get("id")) and truthy(p.get("type"))):
                    errors.append(f"{key} entry needs id and type")
    sm = el.get("stateMachine")
    if not (isinstance(sm, Mapping) and isinstance(sm.get("states"), list) and len(sm["states"]) > 0
            and any(s == sm.get("initial") and isinstance(s, str) for s in sm["states"])
            and isinstance(sm.get("transitions"), list)):
        miss("stateMachine")
    handlers = el.get("eventHandlers")
    if not isinstance(handlers, list) or len(handlers) == 0:
        miss("eventHandlers")
    else:
        for h in handlers:
            action = get(h, "do")
            if not (isinstance(action, str) and action in HANDLER_ACTIONS):
                errors.append(f"handler action {js_string(action)} not allowed")
    if not isinstance(el.get("guardRules"), list):
        miss("guardRules")
    if not isinstance(el.get("errorRules"), list):
        miss("errorRules")
    limits = el.get("resourceLimits")
    if not (truthy(limits) and isinstance(limits, Mapping) and is_number(limits.get("maxEvents"))):
        miss("resourceLimits")
    conditions = el.get("terminationConditions")
    if not isinstance(conditions, list) or len(conditions) == 0:
        miss("terminationConditions")
    return errors


def _port_compatible(a, b) -> bool:
    return a == b or a == "Any" or b == "Any"


def validate_rewrite(rw: Mapping, el: Mapping, ix: Index, errors: list[str]) -> None:
    """Check one ``rewrite[]`` entry of an ``insert_element``; appends to ``errors``.

    The chain checks (two contiguous edges through the element, port existence, direction, type compatibility, no
    cycle) run for ``replace`` only. ``keep`` and ``disable`` carry no chain (spec 4.9) and are only checked for the
    target edge (it must exist and must not be an ``owns`` edge).
    """
    edge_id = get(rw, "replaceEdgeId")
    edge = ix.edges.get(edge_id) if isinstance(edge_id, str) else None
    if edge is None:
        errors.append(f"rewrite target edge {js_string(edge_id)} unknown")
        return
    if edge.get("relation") == "owns":
        errors.append(f"rewrite of ownership edge {edge['id']} is not allowed")
        return
    disposition = get(rw, "disposition")
    if not (isinstance(disposition, str) and disposition in DISPOSITIONS):
        errors.append(f"rewrite {edge['id']} needs disposition replace|keep|disable")
        return
    if disposition != "replace":
        return
    ch = rw.get("chain")
    ch = ch if truthy(ch) else []
    if not isinstance(ch, list) or len(ch) != 2:
        errors.append(f"rewrite {edge['id']} chain must have 2 edges")
        return
    if not el:
        errors.append(f"rewrite {edge['id']} has no element")
        return
    first, second = ch
    eid = el.get("elementId")
    if get(first, "sourceId") != edge.get("sourceId") or get(second, "targetId") != edge.get("targetId"):
        errors.append(f"rewrite {edge['id']} chain is not contiguous with the original endpoints")
    if get(first, "targetId") != eid or get(second, "sourceId") != eid:
        errors.append(f"rewrite {edge['id']} chain does not pass through {js_string(eid)}")
    src_port_id, tgt_port_id = get(first, "sourcePortId"), get(second, "targetPortId")
    src_out = ix.ports.get(src_port_id) if isinstance(src_port_id, str) else None
    tgt_in = ix.ports.get(tgt_port_id) if isinstance(tgt_port_id, str) else None
    in_port_id, out_port_id = get(first, "targetPortId"), get(second, "sourcePortId")
    el_in = next((p for p in (el.get("inputPorts") or []) if get(p, "id") == in_port_id), None)
    el_out = next((p for p in (el.get("outputPorts") or []) if get(p, "id") == out_port_id), None)
    if not src_out or src_out.get("direction") != "out" or src_out.get("nodeId") != get(first, "sourceId"):
        errors.append(f"rewrite {edge['id']}: {js_string(src_port_id)} is not an out port of {js_string(get(first, 'sourceId'))}")
    if not tgt_in or tgt_in.get("direction") != "in" or tgt_in.get("nodeId") != get(second, "targetId"):
        errors.append(f"rewrite {edge['id']}: {js_string(tgt_port_id)} is not an in port of {js_string(get(second, 'targetId'))}")
    if not el_in:
        errors.append(f"rewrite {edge['id']}: element has no input port {js_string(in_port_id)}")
    if not el_out:
        errors.append(f"rewrite {edge['id']}: element has no output port {js_string(out_port_id)}")
    if src_out and el_in and not _port_compatible(src_out.get("type"), el_in.get("type")):
        errors.append(f"rewrite {edge['id']}: port type mismatch {js_string(src_out.get('type', UNDEF))} -> {js_string(el_in.get('type', UNDEF))}")
    if el_out and tgt_in and not _port_compatible(el_out.get("type"), tgt_in.get("type")):
        errors.append(f"rewrite {edge['id']}: port type mismatch {js_string(el_out.get('type', UNDEF))} -> {js_string(tgt_in.get('type', UNDEF))}")
    if get(first, "sourceId") == get(second, "targetId"):
        errors.append(f"rewrite {edge['id']}: chain forms a cycle without termination")


# --------------------------------------------------------------------------------------------------------
# Compilation
# --------------------------------------------------------------------------------------------------------


def flatten_deltas(deltas: Iterable | None) -> list:
    """Atomic interventions of ``deltas`` in order, ``combined_intervention`` expanded depth-first.

    A combined record that ``model.flatten_parts`` refuses (empty or malformed parts, nesting beyond
    ``MAX_PART_DEPTH``) stays in the list as ``{"type": "combined_intervention", "invalid": <reason>}`` so that
    ``compile_deltas`` reports it instead of dropping it.
    """
    out: list = []
    for d in deltas or []:
        if isinstance(d, Mapping) and d.get("type") == COMBINED:
            try:
                out.extend(am.flatten_parts(d))
            except am.AtlasModelError as ex:
                out.append({"type": COMBINED, "invalid": ex.message})
        else:
            out.append(d)
    return out


@dataclass
class Compiled:
    status: str = "OK"
    errors: list = field(default_factory=list)
    flat: list = field(default_factory=list)
    disabled_nodes: set = field(default_factory=set)
    disabled_edges: set = field(default_factory=set)
    resource_nodes: set = field(default_factory=set)
    identity_nodes: set = field(default_factory=set)
    policy: dict = field(default_factory=dict)
    guard_overrides: dict = field(default_factory=dict)
    delays: list = field(default_factory=list)
    elements: dict = field(default_factory=dict)
    replaced: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """JSON-safe form (sets become sorted lists, maps keep their insertion order)."""
        return {
            "status": self.status, "errors": list(self.errors), "flat": self.flat,
            "disabledNodes": sorted(self.disabled_nodes), "disabledEdges": sorted(self.disabled_edges),
            "resourceNodes": sorted(self.resource_nodes), "identityNodes": sorted(self.identity_nodes),
            "policy": dict(self.policy), "guardOverrides": dict(self.guard_overrides), "delays": list(self.delays),
            "elements": dict(self.elements), "replaced": dict(self.replaced),
        }


def compile_deltas(ix: Index, deltas: Iterable | None) -> Compiled:
    flat = flatten_deltas(deltas)
    c = Compiled(flat=flat)
    draft = False
    for d in flat:
        if not isinstance(d, Mapping):
            c.errors.append(f"unknown intervention type {js_string(get(d, 'type'))}")
            continue
        kind = d.get("type", UNDEF)
        if kind == "disable_module":
            _target_node(c, ix, d, "disable_module", c.disabled_nodes)
        elif kind == "disable_edge":
            target = d.get("targetEdgeId", UNDEF)
            if not (isinstance(target, str) and target in ix.edges):
                c.errors.append(f"disable_edge: unknown edge {js_string(target)}")
            else:
                c.disabled_edges.add(target)
        elif kind == "resource_failure":
            _target_node(c, ix, d, "resource_failure", c.resource_nodes)
        elif kind == "identity_failure":
            _target_node(c, ix, d, "identity_failure", c.identity_nodes)
        elif kind == "change_timeout":
            if not num_gt(d.get("timeoutMs", UNDEF), 0):
                c.errors.append("change_timeout: timeoutMs must be > 0")
            else:
                c.policy["timeoutMs"] = d["timeoutMs"]
        elif kind == "change_retry":
            if not num_ge(d.get("retryMax", UNDEF), 0):
                c.errors.append("change_retry: retryMax must be >= 0")
            elif d["retryMax"] > MAX_RETRY:
                c.errors.append(f"change_retry: retryMax must be <= {MAX_RETRY}")
            else:
                c.policy["retryMax"] = d["retryMax"]
                if d.get("retryBackoffMs", UNDEF) is not UNDEF:
                    c.policy["retryBackoffMs"] = d["retryBackoffMs"]
        elif kind == "delay_message":
            edge_id, event_id, rule_id = d.get("targetEdgeId"), d.get("targetEventId"), d.get("targetRuleId")
            if not num_ge(d.get("addLatencyMs", UNDEF), 0) or (not truthy(edge_id) and not truthy(event_id) and not truthy(rule_id)):
                c.errors.append("delay_message: needs addLatencyMs and a target")
            elif truthy(edge_id) and not (isinstance(edge_id, str) and edge_id in ix.edges):
                c.errors.append(f"delay_message: unknown edge {js_string(edge_id)}")
            else:
                # keys of a missing target are left out (undefined in the original), never written as null
                delay = {key: d[source] for key, source in (("edgeId", "targetEdgeId"), ("ruleId", "targetRuleId"),
                                                            ("eventId", "targetEventId")) if source in d}
                delay["ms"] = d["addLatencyMs"]
                c.delays.append(delay)
        elif kind == "change_guard":
            machine = ix.machines.get(d.get("machineId")) if isinstance(d.get("machineId"), str) else None
            transition_id = d.get("transitionId", UNDEF)
            if machine is None or not any(get(t, "id") == transition_id for t in machine.get("transitions", [])):
                c.errors.append(f"change_guard: unknown transition {js_string(transition_id)}")
            else:
                c.guard_overrides[transition_id] = nullish(d.get("guard", UNDEF), None)
        elif kind == "insert_element":
            draft = _insert_element(c, ix, d) or draft
        elif kind == COMBINED and "invalid" in d:
            c.errors.append(f"combined_intervention: {d['invalid']}")
        else:
            c.errors.append(f"unknown intervention type {js_string(kind)}")
    if c.errors:
        c.status = "DRAFT" if draft and not any(not e.startswith("element ") for e in c.errors) else "INVALID"
    return c


def _target_node(c: Compiled, ix: Index, d: Mapping, label: str, bucket: set) -> None:
    target = d.get("targetNodeId", UNDEF)
    if not (isinstance(target, str) and target in ix.nodes):
        c.errors.append(f"{label}: unknown node {js_string(target)}")
    else:
        bucket.add(target)


def _hard_element_errors(el) -> list[str]:
    """Problems of an element body that make it invalid rather than a draft (the body is complete enough to be wrong)."""
    if not isinstance(el, Mapping) or "latencyMs" not in el:
        return []
    latency = el["latencyMs"]
    if latency is None or (is_number(latency) and math.isfinite(latency) and latency >= 0):
        return []
    return [f"latencyMs must be a finite number >= 0, got {js_string(latency) if not isinstance(latency, (Mapping, list)) else type(latency).__name__}"]


def _insert_element(c: Compiled, ix: Index, d: Mapping) -> bool:
    """Compile one ``insert_element``; returns True when the element body is incomplete (a draft)."""
    element = d.get("element")
    hard = _hard_element_errors(element)
    if hard:
        name = js_string(nullish(get(element, "elementId", None), "?"))
        c.errors.extend(f"insert_element {name}: {e}" for e in hard)  # not "element ...": INVALID, not a draft
        return False
    el_errors = validate_element(element, ix)
    if el_errors:
        name = js_string(nullish(get(element, "elementId", None), "?"))
        c.errors.extend(f"element {name}: {e}" for e in el_errors)
        return True
    eid = element["elementId"]
    if eid in c.elements:
        c.errors.append(f"duplicate element {eid}")
        return False
    rewrites = d.get("rewrite")
    if not truthy(rewrites) or not isinstance(rewrites, list) or len(rewrites) == 0:
        c.errors.append(f"element {eid} is not connected to any formal edge")
        return True  # a complete body that is not connected yet is a draft (spec 4.9)
    before = len(c.errors)
    for rw in rewrites:
        validate_rewrite(rw, element, ix, c.errors)
        target = get(rw, "replaceEdgeId")
        if isinstance(target, str) and target in c.replaced:
            c.errors.append(f"edge {target} is rewritten twice")
    if len(c.errors) == before:
        c.elements[eid] = element
        for rw in rewrites:
            c.replaced[rw["replaceEdgeId"]] = {**rw, "elementId": eid}
    return False
