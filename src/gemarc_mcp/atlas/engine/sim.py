"""Deterministic scripted discrete-event simulation (4D) shared by the baseline run and the 5D what-if run.

Semantic port of the reference ``simulate``. A case is a SCRIPT: its events are replayed in ``seq`` order over the
formal runtime edges, with a logical clock (no wall clock, no randomness, no I/O). The engine never generates
events; ``ruleId`` is only an annotation.

Pure function: ``simulate(model, case, deltas, ...)`` never modifies its arguments and returns a JSON-safe dict
(see ``engine/README.md`` for the shape). Recorded divergences from the reference that touch this module:

* ``cause`` of a trace entry is a structured ``{code, args}`` (see ``types``) instead of free text;
* the run carries no ``seed`` (the reference recorded it and never used it);
* the timeout default is the single constant ``DEFAULT_POLICY`` of ``model.py``;
* ``modelVersion`` is ``model_sha8(model)`` and the run carries no revision stamp (the contract has no hand-written version fields).
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from .deltas import Compiled, Index, compile_deltas, index_model
from .guards import eval_guard, eval_when, normalize_when
from .types import (ACTIVE_STATES, DEFAULT_MAX_EVENTS, DEFAULT_POLICY, DELIVERY, UNDEF, as_number, cause, check_run_options,
                    contract_guard, get, js_string, nullish, truthy)
from . import types as T

__all__ = ["simulate", "seek", "fold_state", "event_key", "trace_hash", "fnv32", "canonical", "DEFAULT_POLICY"]


# --------------------------------------------------------------------------------------------------------
# Canonical form and hash of a trace
# --------------------------------------------------------------------------------------------------------


def canonical(value) -> str:
    """Key-sorted JSON text of a value (the input of the run hash). ``None`` stands for JavaScript null/undefined."""
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical(v) for v in value) + "]"
    if isinstance(value, Mapping):
        return "{" + ",".join(f"{json.dumps(k, ensure_ascii=False)}:{canonical(value[k])}" for k in sorted(value)) + "}"
    if isinstance(value, float) and value == int(value) and abs(value) < 1e21:
        value = int(value)
    return json.dumps(None if value is UNDEF else value, ensure_ascii=False)


def fnv32(text: str) -> str:
    """32-bit FNV hash (variant a) over the UTF-16 code units of ``text`` (8 lower-case hex digits). Change detection only."""
    h = 2166136261
    data = text.encode("utf-16-le", "surrogatepass")
    for i in range(0, len(data), 2):
        h ^= data[i] | (data[i + 1] << 8)
        h = (h * 16777619) & 4294967295
    return f"{h:08x}"


def trace_hash(run: Mapping) -> str:
    return fnv32(canonical(run["trace"]))


# --------------------------------------------------------------------------------------------------------
# simulate
# --------------------------------------------------------------------------------------------------------


@contract_guard
def simulate(model: Mapping, case: Mapping, deltas=None, opts: Mapping | None = None) -> dict:
    """Run ``case`` on ``model`` with the interventions ``deltas`` applied.

    Precondition: ``model.contract_issues(model)`` is empty (the engine does not re-check the contract; input that
    breaks it raises ``AtlasEngineError`` ``GEMARC_ATLAS_ENGINE_003`` or, for a bad option, ``GEMARC_ATLAS_ENGINE_002``).

    ``opts`` (all optional): ``index`` (a prebuilt ``Index``), ``compiled`` (a prebuilt ``Compiled``),
    ``interventionTime`` (the delta applies only to events that start at or after this logical time; default 0; a finite
    number), ``maxEvents`` (trace length limit; default ``DEFAULT_MAX_EVENTS``; a run that reaches it, also in the
    middle of a retry loop, stops with status ``LIMIT_EXCEEDED``).
    """
    opts = opts or {}
    check_run_options(opts)
    ix: Index = opts.get("index") or index_model(model)
    cd: Compiled = opts.get("compiled") or compile_deltas(ix, deltas)
    case_id = case["caseId"]
    base = {"caseId": case_id, "modelVersion": ix.version}
    if cd.status != "OK":
        return {**base, "status": "DRAFT" if cd.status == "DRAFT" else "REJECTED", "errors": list(cd.errors), "trace": [],
                "initialState": {}, "finalState": {}, "snapshots": [], "hash": None}
    empty = compile_deltas(ix, [])
    t_star = nullish(opts.get("interventionTime"), 0)
    max_events = nullish(opts.get("maxEvents"), DEFAULT_MAX_EVENTS)
    policy_base = {**DEFAULT_POLICY, **(case.get("policy") or {})}
    context = case.get("context")

    state: dict[str, Any] = {n["id"]: "Idle" for n in ix.node_list}
    initial_machines = case.get("initialMachines") or {}
    for m in model.get("machines", []):
        state[m["nodeId"]] = nullish(initial_machines.get(m["nodeId"]), m["initial"])
    el_machine: dict[str, Any] = {}
    for eid, el in cd.elements.items():
        state[eid] = "Idle"
        el_machine[eid] = el["stateMachine"]["initial"]
    initial_state = dict(state)

    events = sorted(case.get("events", []), key=lambda e: e["seq"])
    entry = events[0]["from"] if events else None
    if truthy(entry):
        state[entry] = "Running"
        initial_state[entry] = state[entry]

    trace: list[dict] = []
    snapshots: list[dict] = [{"index": -1, "state": dict(state)}]
    pending: dict[str, int] = {}
    seen_dedup: set = set()
    agg_failed: dict[str, list] = {}  # ordered, duplicate-free (a JavaScript Set)
    answered: dict[str, int] = {}
    clock = 0
    cur_group = None
    group_start = 0
    group_end = 0
    status = "OK"
    truncated = False  # a retry loop stopped at max_events

    def node_props(node_id) -> Mapping:
        node = ix.nodes.get(node_id)
        return (node.get("props") if node else None) or {}

    def set_state(changes: list, node_id, nxt) -> None:
        prev = state.get(node_id)
        if prev != nxt:
            changes.append({"nodeId": node_id, "from": prev, "to": nxt})
            state[node_id] = nxt

    def push(e: Mapping) -> dict:
        index = len(trace)
        rec = {
            "index": index, "sequence": index + 1, "caseId": case_id, "eventId": e["eventId"], "logicalTime": e["time"],
            "sourceNodeId": e["from"], "targetNodeId": e["to"], "edgeId": nullish(e.get("edgeId"), None),
            "eventType": e["type"], "outcome": e["outcome"], "previousState": e.get("prev"), "nextState": e.get("next"),
            "stateChanges": e["changes"], "ruleId": nullish(e.get("ruleId"), None),
            "evidenceIds": list(nullish(e.get("evidenceIds"), nullish(case.get("evidenceIds"), []))),
            "errorCode": nullish(e.get("errorCode"), None), "cause": nullish(e.get("cause"), None),
            "attempt": nullish(e.get("attempt"), None), "synthetic": truthy(e.get("synthetic")),
            "role": nullish(e.get("role"), None), "httpStatus": nullish(e.get("httpStatus"), None),
        }
        trace.append(rec)
        if (index + 1) % 4 == 0:
            snapshots.append({"index": index, "state": dict(state)})
        return rec

    def mk(ev: Mapping, **over) -> dict:
        return {"eventId": f"{case_id}#{js_string(ev.get('seq', UNDEF))}{nullish(over.get('suffix'), '')}",
                "ruleId": ev.get("ruleId"), "role": ev.get("role"), **over}

    def mark_agg_fail(agg, child, ev: Mapping) -> None:
        if truthy(ev.get("section")) and truthy(node_props(agg).get("aggregator")):
            members = agg_failed.setdefault(agg, [])
            if child not in members:
                members.append(child)

    def is_active(node_id, c: Compiled) -> bool:
        if node_id in c.elements:
            return state.get(node_id) not in ("Idle", "Failed", "Blocked")
        return state.get(node_id) in ACTIVE_STATES

    # ---- routing -------------------------------------------------------------------------------------

    def resolve(ev: Mapping, c: Compiled) -> dict:
        if ev.get("from") == ev.get("to"):
            return {"kind": "self"}
        if truthy(ev.get("edge")):
            edge = ix.edges.get(ev["edge"]) if isinstance(ev["edge"], str) else None
            candidates = [edge] if edge else []
        else:
            src, dst, returning = ev.get("from"), ev.get("to"), ev.get("type") == "Return"
            runtime = ix.runtime_edges
            # prefer the edge whose direction matches the event (a Return travels back along the edge of its request)
            if returning:
                candidates = [e for e in runtime if e["sourceId"] == dst and e["targetId"] == src]
                if not candidates:
                    candidates = [e for e in runtime if e["sourceId"] == src and e["targetId"] == dst]
            else:
                candidates = [e for e in runtime if e["sourceId"] == src and e["targetId"] == dst]
                if not candidates:
                    candidates = [e for e in runtime if e["sourceId"] == dst and e["targetId"] == src]
        if not candidates:
            return {"kind": "unbound"}
        live = [e for e in candidates if e["id"] not in c.disabled_edges
                and get(c.replaced.get(e["id"]), "disposition") != "disable"]
        if not live:
            return {"kind": "blocked", "edgeId": candidates[0]["id"], "cause": cause(T.C_EDGE_DISABLED)}
        rep = next((e for e in live if get(c.replaced.get(e["id"]), "disposition") == "replace"), None)
        if rep is not None:
            return {"kind": "chain", "edge": rep, "rewrite": c.replaced[rep["id"]]}
        return {"kind": "edge", "edge": live[0]}

    def latency_for(ev: Mapping, edge_id, c: Compiled, attempt: int):
        raw = ev.get("latencyMs")
        arr = raw if isinstance(raw, list) else [nullish(raw, 0)]
        lat = arr[min(attempt, len(arr) - 1)] if arr else UNDEF
        own_id = f"{case_id}#{js_string(ev.get('seq', UNDEF))}"
        for d in c.delays:
            if ((truthy(d.get("edgeId")) and d["edgeId"] == edge_id) or (truthy(d.get("ruleId")) and d["ruleId"] == ev.get("ruleId"))
                    or (truthy(d.get("eventId")) and d["eventId"] == own_id)):
                lat = lat + d["ms"]
        return lat

    # ---- delivery of one (possibly synthetic) hop ----------------------------------------------------

    def deliver(ev: Mapping, o: Mapping) -> Any:
        nonlocal truncated
        frm, to, edge_id, start, c = o["from"], o["to"], o.get("edgeId"), o["start"], o["c"]
        typ = ev.get("type")
        changes: list = []
        suffix = o.get("suffix")
        base_ = {"from": frm, "to": to, "type": typ, "edgeId": edge_id, "time": start, "suffix": suffix,
                 "synthetic": truthy(suffix)}

        def prev_to():
            return state.get(to)

        # availability
        if frm in c.disabled_nodes or to in c.disabled_nodes:
            dead = to if to in c.disabled_nodes else frm
            set_state(changes, dead, "Blocked")
            push(mk(ev, **{**base_, "outcome": "Blocked", "cause": cause(T.C_MODULE_DISABLED, nodeId=dead), "prev": prev_to(),
                           "next": state.get(to), "changes": changes}))
            if typ in DELIVERY:
                mark_agg_fail(frm, to, ev)
            return start
        # an idle source: a source that was never reached (or has failed) cannot emit
        why = None
        if typ == "Return" and not truthy(o.get("relay")):
            if not pending.get(frm, 0) > 0:
                why = cause(T.C_NO_PENDING)
            elif state.get(frm) in ("Failed", "Blocked"):
                why = cause(T.C_SOURCE_STATE, state=state.get(frm))
        elif typ == "Failure":
            if state.get(frm) in ("Idle", "Blocked"):
                why = cause(T.C_SOURCE_STATE, state=state.get(frm))  # a failed node can still report its failure
        elif not is_active(frm, c):
            why = cause(T.C_SOURCE_STATE, state=state.get(frm))
        if why:
            push(mk(ev, **{**base_, "outcome": "Starved", "cause": why, "prev": prev_to(), "next": prev_to(), "changes": changes}))
            if typ == "Return":
                mark_agg_fail(to, frm, ev)
            return start
        # dedup
        tgt_props = node_props(to)
        if truthy(ev.get("dedupKey")) and truthy(tgt_props.get("dedup")) and typ in DELIVERY:
            key = f"{to}|{js_string(ev['dedupKey'])}"
            if key in seen_dedup:
                d = tgt_props["dedup"]
                reject = get(d, "mode") == "reject"
                push(mk(ev, **{**base_, "outcome": "Rejected" if reject else "Ignored", "cause": cause(T.C_DUPLICATE),
                               "errorCode": get(d, "errorCode", None) if reject else None,
                               "httpStatus": nullish(get(d, "httpStatus", None), None), "prev": prev_to(), "next": prev_to(),
                               "changes": changes}))
                return start
            seen_dedup.add(key)
        # faults (case-authored or injected)
        fault = ev.get("fault") if truthy(ev.get("fault")) else None
        if fault is None and (typ in DELIVERY or typ == "Transition"):
            if to in c.resource_nodes:
                fault = {"mode": "fail", "errorCode": "RESOURCE_EXHAUSTED", "httpStatus": 503, "injected": T.C_RESOURCE_FAILURE}
            elif to in c.identity_nodes:
                fault = {"mode": "deny", "errorCode": "IDENTITY_FAILURE", "httpStatus": 403, "injected": T.C_IDENTITY_FAILURE}
        if fault is not None:
            fault_cause = cause(fault["injected"] if truthy(fault.get("injected")) else T.C_AUTHORED_FAULT)
            if fault.get("mode") == "deny":
                set_state(changes, frm, "Failed")
                push(mk(ev, **{**base_, "outcome": "Denied", "cause": fault_cause, "errorCode": fault.get("errorCode"),
                               "httpStatus": fault.get("httpStatus"), "prev": prev_to(), "next": prev_to(), "changes": changes}))
                mark_agg_fail(frm, to, ev)
                return start
            if typ == "Compensation":
                set_state(changes, frm, "Failed")
            set_state(changes, to, "Failed")
            changed_to = next((x for x in changes if x["nodeId"] == to), None)
            push(mk(ev, **{**base_, "outcome": "Failed", "cause": fault_cause, "errorCode": fault.get("errorCode"),
                           "httpStatus": fault.get("httpStatus"), "prev": changed_to["from"] if changed_to else prev_to(),
                           "next": "Failed", "changes": changes}))
            mark_agg_fail(frm, to, ev)
            return start
        # event-level guard
        if truthy(ev.get("guard")) and not eval_guard(ev["guard"], {"params": ev.get("params"), "context": context}):
            push(mk(ev, **{**base_, "outcome": "Rejected", "cause": cause(T.C_GUARD),
                           "errorCode": nullish(ev.get("errorCode"), "GUARD_REJECTED"), "httpStatus": 422, "prev": prev_to(),
                           "next": prev_to(), "changes": changes}))
            return start
        if typ == "Transition":
            machine = ix.machines.get(to)
            cur = state.get(to)
            params = ev.get("params")
            target = params.get("target", UNDEF) if isinstance(params, Mapping) else UNDEF
            tr = next((x for x in machine["transitions"] if x["from"] == cur and x["to"] == target), None) if machine else None
            if tr is None:
                push(mk(ev, **{**base_, "outcome": "Rejected", "cause": cause(T.C_NO_TRANSITION),
                               "errorCode": nullish(machine.get("errorCode") if machine else None, "INVALID_TRANSITION"),
                               "httpStatus": 422, "prev": cur, "next": cur, "changes": changes}))
                return start
            tid = tr.get("id")
            guard = c.guard_overrides[tid] if tid in c.guard_overrides else tr.get("guard")
            if not eval_guard(guard, {"params": ev.get("params"), "context": context}):
                push(mk(ev, **{**base_, "outcome": "Rejected", "cause": cause(T.C_TRANSITION_GUARD, transitionId=tid),
                               "errorCode": nullish(tr.get("errorCode"), machine.get("errorCode")), "httpStatus": 422,
                               "prev": cur, "next": cur, "changes": changes}))
                return start
            set_state(changes, to, target)
            push(mk(ev, **{**base_, "outcome": "OK", "prev": cur, "next": target, "changes": changes}))
            return start
        if typ in DELIVERY:
            policy = {**policy_base, **c.policy}
            retry_max = nullish(o.get("retry_override"), policy["retryMax"])
            t = start
            attempt = 0
            while attempt <= retry_max:
                if attempt > 0 and len(trace) >= max_events:
                    truncated = True  # an unbounded retry loop would never reach the per-event limit check
                    return t
                lat = latency_for(ev, edge_id, c, attempt)
                ch: list = []
                if as_number(lat) > as_number(policy["timeoutMs"]):
                    t += policy["timeoutMs"]
                    set_state(ch, to, "Waiting")
                    push(mk(ev, **{**base_, "suffix": f"{nullish(suffix, '')}@{attempt + 1}", "type": "Timeout", "time": t,
                                   "outcome": "Timeout", "attempt": attempt + 1,
                                   "cause": cause(T.C_LATENCY_TIMEOUT, latencyMs=lat, timeoutMs=policy["timeoutMs"]),
                                   "prev": state.get(to), "next": "Waiting", "changes": ch}))
                    if attempt < retry_max:
                        t += policy["retryBackoffMs"]
                    attempt += 1
                    continue
                t += lat
                nxt = "Completed" if truthy(ev.get("ack")) else "Running"
                prev = state.get(to)
                set_state(ch, to, nxt)
                if not truthy(ev.get("ack")):
                    pending[to] = pending.get(to, 0) + 1
                push(mk(ev, **{**base_, "suffix": f"{nullish(suffix, '')}@{attempt + 1}" if attempt else suffix,
                               "type": typ if attempt == 0 else "Retry", "time": t, "outcome": "OK", "attempt": attempt + 1,
                               "prev": prev, "next": nxt, "changes": ch}))
                return t
            ch = []
            prev = state.get(to)
            set_state(ch, to, "Failed")
            push(mk(ev, **{**base_, "suffix": f"{nullish(suffix, '')}!", "type": "Failure", "time": t, "outcome": "Failed",
                           "cause": cause(T.C_TIMEOUT_EXHAUSTED), "attempt": retry_max + 1, "prev": prev, "next": "Failed",
                           "changes": ch}))
            mark_agg_fail(frm, to, ev)
            return t
        if typ == "Return":
            nxt = "Completed"
            degraded_by = agg_failed.get(frm)
            if truthy(node_props(frm).get("aggregator")) and degraded_by:
                nxt = "Degraded"
            prev = state.get(frm)
            set_state(changes, frm, nxt)
            if not truthy(o.get("relay")):
                pending[frm] = max(0, pending.get(frm, 0) - 1)
            if to == entry:
                set_state(changes, to, "Completed")
            elif to in c.elements and state.get(to) == "Idle":
                set_state(changes, to, "Running")
            push(mk(ev, **{**base_, "outcome": "OK", "prev": prev, "next": nxt, "changes": changes,
                           "cause": cause(T.C_DEGRADED, sections=list(degraded_by)) if nxt == "Degraded" else None}))
            return start
        if typ == "Failure":
            prev = state.get(to)
            set_state(changes, to, "Failed")
            push(mk(ev, **{**base_, "outcome": "OK", "prev": prev, "next": "Failed", "changes": changes,
                           "cause": cause(T.C_REPORTED_FAILURE)}))
            return start
        if typ in ("Compensation", "Recovery"):
            prev = state.get(frm)
            set_state(changes, frm, "Recovered")
            push(mk(ev, **{**base_, "outcome": "OK", "prev": prev, "next": "Recovered", "changes": changes}))
            return start
        if typ == "Wait":
            prev = state.get(frm)
            ms = nullish(ev.get("ms"), 0)
            set_state(changes, frm, "Waiting")
            push(mk(ev, **{**base_, "time": start + ms, "outcome": "OK", "prev": prev, "next": "Waiting", "changes": changes}))
            return start + ms
        push(mk(ev, **{**base_, "outcome": "Unbound", "cause": cause(T.C_UNSUPPORTED_TYPE, eventType=typ),
                       "prev": prev_to(), "next": prev_to(), "changes": changes}))
        return start

    # ---- one scripted event --------------------------------------------------------------------------

    def execute_event(ev: Mapping, start) -> Any:
        c = cd if as_number(start) >= as_number(t_star) else empty
        when = normalize_when(ev.get("when"))
        base_ = {"from": ev.get("from"), "to": ev.get("to"), "type": ev.get("type"), "time": start}
        # `when` is evaluated first so skipped events never claim routing problems
        if truthy(when) and not eval_when(when, {"state": state, "params": ev.get("params"), "context": context}):
            push(mk(ev, **{**base_, "outcome": "Skipped", "cause": cause(T.C_WHEN_FALSE), "prev": state.get(ev.get("to")),
                           "next": state.get(ev.get("to")), "changes": []}))
            return start
        r = resolve(ev, c)
        if r["kind"] == "unbound":
            push(mk(ev, **{**base_, "outcome": "Unbound", "cause": cause(T.C_NO_FORMAL_EDGE), "prev": state.get(ev.get("to")),
                           "next": state.get(ev.get("to")), "changes": []}))
            return start
        if r["kind"] == "blocked":
            push(mk(ev, **{**base_, "edgeId": r["edgeId"], "outcome": "Blocked", "cause": r["cause"],
                           "prev": state.get(ev.get("to")), "next": state.get(ev.get("to")), "changes": []}))
            if ev.get("type") in DELIVERY:
                mark_agg_fail(ev.get("from"), ev.get("to"), ev)
            return start
        if r["kind"] == "chain":
            return execute_chain(ev, start, r, c)
        edge = r.get("edge")
        return deliver(ev, {"from": ev.get("from"), "to": ev.get("to"), "edgeId": edge["id"] if edge else None,
                            "start": start, "c": c})

    def execute_chain(ev: Mapping, start, r: Mapping, c: Compiled) -> Any:
        el = c.elements[r["rewrite"]["elementId"]]
        eid = el["elementId"]
        ch = r["rewrite"]["chain"]
        forward = ev.get("from") == r["edge"]["sourceId"]
        first_hop_edge = ch[0] if forward else ch[1]
        second_hop_edge = ch[1] if forward else ch[0]
        relay = ev.get("type") == "Return"
        # the element already returned this request itself: the callee's scripted Return never happens, and the
        # section is not lost
        ans_key = f"{ev.get('from')}|{ev.get('to')}"
        if relay and answered.get(ans_key, 0) > 0:
            answered[ans_key] -= 1
            push(mk(ev, **{"from": ev.get("from"), "to": ev.get("to"), "type": ev.get("type"), "edgeId": first_hop_edge["id"],
                           "time": start, "outcome": "Skipped", "cause": cause(T.C_ANSWERED_BY_ELEMENT), "suffix": "#h1",
                           "synthetic": True, "prev": state.get(ev.get("to")), "next": state.get(ev.get("to")), "changes": []}))
            return start
        first_hop = {**ev, "to": eid, "latencyMs": nullish(el.get("latencyMs"), 0), "ack": False, "dedupKey": None, "when": None,
                "guard": None, "fault": None, "ruleId": ev.get("ruleId"), "role": ev.get("role")}
        t = deliver(first_hop, {"from": ev.get("from"), "to": eid, "edgeId": first_hop_edge["id"], "start": start, "c": c,
                           "suffix": "#h1", "relay": False})
        if truncated or state.get(eid) in ("Failed", "Blocked"):
            return t
        last = trace[-1] if trace else None
        if not last or last["outcome"] != "OK" or last["targetNodeId"] != eid:
            if ev.get("type") == "Return":
                mark_agg_fail(ev.get("to"), ev.get("from"), ev)
            return t
        # element behaviour (machine state is kept apart from the lifecycle state shown in 3D)
        action = "forward"
        handler = None
        if not relay:
            handler = next((h for h in (el.get("eventHandlers") or [])
                            if get(h, "on") == "receive" and eval_when(normalize_when(h.get("when")), {
                                "state": state, "params": ev.get("params"), "context": context,
                                "elementState": el_machine.get(eid)})), None)
            action = handler["do"] if handler else "forward"
        if handler and truthy(handler.get("setState")):
            el_machine[eid] = handler["setState"]
        trace[-1]["elementState"] = el_machine.get(eid)
        if action == "forward":
            second_hop = {**ev, "from": eid}
            return deliver(second_hop, {"from": eid, "to": ev.get("to"), "edgeId": second_hop_edge["id"], "start": t, "c": c,
                                  "suffix": "#h2", "relay": relay, "retry_override": handler.get("retry") if handler else None})
        # respond / drop / fail: the original target is never invoked
        if action == "respond":
            key = f"{ev.get('to')}|{ev.get('from')}"
            answered[key] = answered.get(key, 0) + 1
            resp = {**ev, "type": "Return", "from": eid, "to": ev.get("from")}
            rt = deliver({**resp, "when": None, "dedupKey": None, "guard": None, "fault": None, "ack": False},
                         {"from": eid, "to": ev.get("from"), "edgeId": first_hop_edge["id"], "start": t, "c": c,
                          "suffix": "#resp", "relay": True})
            push(mk(ev, **{"from": eid, "to": ev.get("to"), "type": ev.get("type"), "edgeId": second_hop_edge["id"], "time": rt,
                           "outcome": "Skipped", "cause": cause(T.C_ELEMENT_RESPOND), "suffix": "#h2", "synthetic": True,
                           "prev": state.get(ev.get("to")), "next": state.get(ev.get("to")), "changes": []}))
            return rt
        lifecycle = "Failed" if action == "fail" else "Completed"
        if ev.get("type") in DELIVERY:
            mark_agg_fail(ev.get("from"), ev.get("to"), ev)
        lc: list = []
        set_state(lc, eid, lifecycle)
        trace[-1]["stateChanges"].extend(lc)
        push(mk(ev, **{"from": eid, "to": ev.get("to"), "type": ev.get("type"), "edgeId": second_hop_edge["id"], "time": t,
                       "outcome": "Skipped", "cause": cause(T.C_ELEMENT_DROP if action == "drop" else T.C_ELEMENT_FAIL),
                       "suffix": "#h2", "synthetic": True, "prev": state.get(ev.get("to")), "next": state.get(ev.get("to")),
                       "changes": []}))
        return t

    for ev in events:
        if len(trace) >= max_events:
            status = "LIMIT_EXCEEDED"
            break
        start = clock
        if truthy(ev.get("group")):
            if ev["group"] != cur_group:
                cur_group = ev["group"]
                group_start = clock
                group_end = clock
            start = group_start
        else:
            cur_group = None
        end = execute_event(ev, start)
        if truncated:
            status = "LIMIT_EXCEEDED"
            break
        if truthy(ev.get("group")):
            group_end = max(group_end, end)
            clock = group_end
        else:
            clock = max(clock, end)

    run = {**base, "status": status, "errors": [], "trace": trace, "initialState": initial_state, "finalState": dict(state),
           "elementStates": dict(el_machine), "snapshots": snapshots, "entryNodeId": entry,
           "aggregatorFailures": {k: sorted(v) for k, v in agg_failed.items()}}
    run["hash"] = trace_hash(run)
    return run


# --------------------------------------------------------------------------------------------------------
# Replay helpers and comparison key
# --------------------------------------------------------------------------------------------------------


def seek(run: Mapping, idx: int) -> dict:
    """Node states after trace entry ``idx`` (``-1`` = before the first), rebuilt from the nearest snapshot at or
    before ``idx`` plus the later ``stateChanges`` (a replay, not animation progress)."""
    trace = run["trace"]
    if idx < -1 or idx >= len(trace):
        raise IndexError(f"seek index {idx} out of range")
    snap = run["snapshots"][0]
    for s in run["snapshots"]:
        if s["index"] <= idx:
            snap = s
    st = dict(snap["state"])
    for i in range(snap["index"] + 1, idx + 1):
        for ch in trace[i]["stateChanges"]:
            st[ch["nodeId"]] = ch["to"]
    return st


def fold_state(run: Mapping, idx: int) -> dict:
    """Node states after entry ``idx`` folded from ``initialState`` over every ``stateChanges`` (the slow oracle)."""
    st = dict(run["initialState"])
    for i in range(0, idx + 1):
        for ch in run["trace"][i]["stateChanges"]:
            st[ch["nodeId"]] = ch["to"]
    return st


def event_key(e: Mapping) -> str:
    """What makes two trace entries "the same event" when two runs are compared."""
    parts = (e["sourceNodeId"], e["targetNodeId"], e["edgeId"], e["eventType"], e["outcome"], e["nextState"], e["errorCode"])
    return "|".join("" if p is None else js_string(p) for p in parts)
