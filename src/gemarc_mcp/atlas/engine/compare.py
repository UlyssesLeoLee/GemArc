"""A/B comparison of two runs, the explanation record and the one-call 5D second run ``run_causal``.

Semantic port of the reference run comparison, second run and explanation functions. ``compare_runs`` classifies the
difference between a baseline run A and an intervention run B as ``NO_DIFFERENCE`` (same trace and same final
state), ``CONVERGED`` (different path, same final state) or ``DIVERGED`` (different final state) and records the
first divergence, the terminal differences, the touched nodes and edges, blocking, propagation, recovery,
compensation and feedback (retry) evidence.

Recorded divergence: no ``seed`` in ``explanation.baselineConditions`` (the reference echoed a value it never used).
The human sentences of ``explanation`` (``summary``, ``assumptions``) are rendered for a locale
(``zh-CN``, ``ja-JP`` or ``en-US``), see ``causal.py``.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from .causal import ASSUMPTION_CODES, EPISTEMIC, summary_text, assumption_text, DEFAULT_LOCALE
from .deltas import Index, compile_deltas, flatten_deltas, index_model
from .sim import event_key, simulate
from .types import (ENGINE_ERROR_UNKNOWN_CASE, FAILURE_OUTCOMES, AtlasEngineError, UNDEF, contract_guard, get, nullish, truthy)

__all__ = ["compare_runs", "run_causal", "explain"]


@contract_guard
def compare_runs(A: Mapping, B: Mapping) -> dict:
    ta, tb = A["trace"], B["trace"]
    na, nb = len(ta), len(tb)
    idx = -1
    for i in range(max(na, nb)):
        if i >= na or i >= nb or event_key(ta[i]) != event_key(tb[i]):
            idx = i
            break
    node_ids = sorted(set(A["finalState"]) | set(B["finalState"]))
    terminal_diff: list[dict] = []
    added_nodes: list[dict] = []
    for node_id in node_ids:
        a_state = A["finalState"].get(node_id, UNDEF)
        if a_state is UNDEF:
            added_nodes.append({"nodeId": node_id, "b": B["finalState"].get(node_id)})  # scenario-only element
        elif a_state != B["finalState"].get(node_id, UNDEF):
            terminal_diff.append({"nodeId": node_id, "a": a_state, "b": nullish(B["finalState"].get(node_id), None)})
    if idx == -1 and not terminal_diff:
        outcome_class = "NO_DIFFERENCE"
    elif not terminal_diff:
        outcome_class = "CONVERGED"
    else:
        outcome_class = "DIVERGED"
    tail = [] if idx == -1 else tb[idx:]
    a_keys = {event_key(e) for e in ta}
    affected_nodes = {d["nodeId"] for d in terminal_diff}
    affected_edges: set = set()
    for e in tail:
        if event_key(e) not in a_keys:
            affected_nodes.add(e["sourceNodeId"])
            affected_nodes.add(e["targetNodeId"])
            if e["edgeId"]:
                affected_edges.add(e["edgeId"])
    b_keys = {event_key(e) for e in tb}
    for e in ([] if idx == -1 else ta[idx:]):
        if event_key(e) not in b_keys and e["edgeId"]:
            affected_edges.add(e["edgeId"])
    first_block = next((i for i, e in enumerate(tb) if i >= max(idx, 0) and e["outcome"] in FAILURE_OUTCOMES), -1)
    blocking = [{"index": e["index"], "edgeId": e["edgeId"], "nodeId": e["targetNodeId"], "cause": e["cause"]}
                for e in tail if e["outcome"] in ("Blocked", "Denied")]
    propagation = [] if first_block == -1 else [
        {"index": e["index"], "nodeId": e["sourceNodeId"], "cause": e["cause"]} for e in tb[first_block:] if e["outcome"] == "Starved"]
    recovery = [e["index"] for e in tail if e["role"] == "recovery" and e["outcome"] == "OK"]
    compensation = [{"index": e["index"], "outcome": e["outcome"]} for e in tail if e["eventType"] == "Compensation"]
    feedback = [{"index": e["index"], "attempt": e["attempt"], "outcome": e["outcome"]} for e in tail if e["eventType"] == "Retry"]
    if idx == -1:
        first_divergence = None
    else:
        shown = (tb[idx] if idx < nb else None) or (ta[idx] if idx < na else None)
        first_divergence = {"index": idx, "logicalTime": shown["logicalTime"],
                            "a": event_key(ta[idx]) if idx < na else None, "b": event_key(tb[idx]) if idx < nb else None}
    return {
        "outcomeClass": outcome_class, "firstDivergence": first_divergence, "terminalDiff": terminal_diff,
        "addedNodes": added_nodes, "affectedNodes": sorted(affected_nodes), "affectedEdges": sorted(affected_edges),
        "blocking": blocking, "propagation": propagation, "recovery": recovery, "compensation": compensation,
        "feedback": feedback, "recovered": len(recovery) > 0 and any(d["b"] == "Completed" for d in terminal_diff),
        "epistemic": EPISTEMIC,
    }


@contract_guard
def run_causal(model: Mapping, case_id: str, deltas, opts: Mapping | None = None) -> dict:
    """Baseline run A, intervention run B and their comparison, in one call.

    ``opts``: as for ``simulate`` plus ``locale`` for the sentences of the explanation. Raises ``AtlasEngineError``
    for an unknown case. An intervention that cannot be computed returns ``{caseId, status, errors, A, B, diff: None,
    explanation: None}`` (status ``DRAFT`` or ``REJECTED``). When either run stopped at ``maxEvents`` the comparison
    covers only a prefix: the status is ``LIMIT_EXCEEDED`` (not ``OK``) and ``diff`` and ``explanation`` carry
    ``truncated: true`` (the key is absent otherwise); the outcome class is then not a verdict.

    The result is a read-only view: it shares the traces of the two runs and some sub-objects of the model and the
    interventions with its inputs. A caller that decorates it must copy first.
    """
    opts = dict(opts or {})
    ix: Index = opts.get("index") or index_model(model)
    case = ix.cases.get(case_id)
    if case is None:
        raise AtlasEngineError(ENGINE_ERROR_UNKNOWN_CASE, f"unknown case {case_id}")
    run_opts = {**{k: v for k, v in opts.items() if k != "locale"}, "index": ix}
    A = simulate(model, case, [], run_opts)
    B = simulate(model, case, deltas, run_opts)
    if B["status"] != "OK" and B["status"] != "LIMIT_EXCEEDED":
        return {"caseId": case_id, "status": B["status"], "errors": B["errors"], "A": A, "B": B, "diff": None, "explanation": None}
    diff = compare_runs(A, B)
    truncated = A["status"] == "LIMIT_EXCEEDED" or B["status"] == "LIMIT_EXCEEDED"
    if truncated:
        diff["truncated"] = True
    return {"caseId": case_id, "status": "LIMIT_EXCEEDED" if truncated else "OK", "errors": [], "A": A, "B": B, "diff": diff,
            "explanation": explain(model, case, deltas, A, B, diff, opts)}


def explain(model: Mapping, case: Mapping, deltas, A: Mapping, B: Mapping, diff: Mapping, opts: Mapping | None = None) -> dict:
    opts = opts or {}
    locale = opts.get("locale") or DEFAULT_LOCALE
    flat = flatten_deltas(deltas)
    first = diff["firstDivergence"]
    version = A.get("modelVersion")
    conditions: dict[str, Any] = {"caseId": case["caseId"]}
    if "policy" in case:
        conditions["policy"] = copy.deepcopy(case["policy"])
    conditions.update({"initialMachines": copy.deepcopy(case.get("initialMachines") or {}),
                       "context": copy.deepcopy(case.get("context") or {}), "modelVersion": version})
    out = {
        "baselineConditions": conditions,
        "intervention": [{"type": get(d, "type", None), "interventionId": nullish(get(d, "interventionId", None), None),
                          "target": _first_target(d)} for d in flat],
        "interventionTime": nullish(opts.get("interventionTime"), 0),
        "firstDivergence": first, "affectedNodes": diff["affectedNodes"], "affectedEdges": diff["affectedEdges"],
        "appliedRules": list(dict.fromkeys(e["ruleId"] for e in B["trace"]
                                           if e["ruleId"] and (first is None or e["index"] >= first["index"]))),
        "propagation": diff["propagation"], "blocking": diff["blocking"], "recovery": diff["recovery"],
        "compensation": diff["compensation"], "finalOutcomeDiff": diff["terminalDiff"], "outcomeClass": diff["outcomeClass"],
        "summary": summary_text(diff["outcomeClass"], locale),
        "evidence": list(case.get("evidenceIds") or []),
        "assumptions": [assumption_text(code, locale) for code in ASSUMPTION_CODES],
        "epistemic": diff["epistemic"],
    }
    if diff.get("truncated"):
        out["truncated"] = True
    return out


def _first_target(d) -> Any:
    """``targetNodeId ?? targetEdgeId ?? machineId ?? element.elementId ?? null`` of an atomic intervention."""
    for key in ("targetNodeId", "targetEdgeId", "machineId"):
        value = get(d, key, None)
        if value is not None:
            return value
    element = get(d, "element", None)
    value = get(element, "elementId", None)
    return value if value is not None else None
