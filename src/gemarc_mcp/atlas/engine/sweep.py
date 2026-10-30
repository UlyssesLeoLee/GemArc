"""Generic guard scan (rule SM-006): synthesise values from a guard, find dead and always-true branches, probe the engine.

Clean-room. The reference carried a hard-coded table of satisfying values per guard; this module derives them from the
guard itself, so any guard of the contract grammar is handled the same way and no domain value appears in code.

How a value is synthesised. A guard is a tree of ``all`` / ``any`` / ``not`` over parameter tests
(``present absent eq neq in``). Every parameter (and every ``$ctx.<key>`` when the context is free) is a variable. For each
variable the scan builds a small candidate pool that is *complete* for the tests in the guard: every literal the guard
compares the variable with (``eq v`` gives ``v``, ``in [..]`` gives each member, so the first member comes first), the
empty string, ``null``, the missing value, and two fresh non-empty strings (so ``present`` gets a non-empty value and
``neq`` / ``not`` get a value that differs from every literal: the failing value of the child). Any two values outside the
literals behave alike for every test, so the pool decides satisfiability exactly. The search is the Cartesian product of
the pools in a fixed order, judged by the engine's own ``eval_guard``; the first assignment that gives the wanted result is
the witness. The product is capped (``MAX_COMBOS``): above it the verdict is ``undecided`` and nothing is reported.

What is reported (``scan_model``):

* a guard (or a branch of an ``all`` / ``any``) that no assignment can satisfy: ``never_satisfiable``;
* a guard (or a branch) that every assignment satisfies: ``always_true``;
* a probe disagreement: the engine accepts or rejects a transition for the synthesised values differently from the
  synthesis (``probe_mismatch``, an engine consistency error and never a design defect).

Sources of guards: machine transitions (free context), ``change_guard`` interventions (free context), and event guards of
cases (the case context is fixed). ``when`` conditions depend on live node states and are not swept.

Pure functions: no I/O, no clock, no randomness; the same model gives the same report.
"""
from __future__ import annotations

import itertools
import json
from typing import Any, Iterable, Mapping

from .. import model as am
from .deltas import Index, flatten_deltas, index_model
from .guards import CTX_PREFIX, eval_guard
from .sim import simulate
from .types import UNDEF, js_string, truthy

__all__ = ["MAX_COMBOS", "RULE_ID", "classify_guard", "guard_findings", "probe_transitions", "synthesise", "scan_model"]

RULE_ID = "SM-006"
#: Upper bound on the assignments tried per verdict; above it the verdict is ``undecided``.
MAX_COMBOS = 20000

NEVER = "never"
ALWAYS = "always"
CONTINGENT = "contingent"
UNDECIDED = "undecided"

_PROBE_NODE = "scan-probe"


# --------------------------------------------------------------------------------------------------------
# Variables and candidate pools
# --------------------------------------------------------------------------------------------------------


def _key(value) -> str:
    """Identity of a candidate value (type-aware: ``1`` and ``true`` differ), for de-duplication only."""
    if value is UNDEF:
        return "undefined"
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _param_name(guard: Mapping) -> str:
    name = guard.get("param", UNDEF)
    return name if isinstance(name, str) else js_string(name)


def _children(guard: Mapping):
    """``(combinator, children)`` of a guard node; ``(None, [])`` for a parameter test (mirrors ``eval_guard``)."""
    for op in ("all", "any"):
        if truthy(guard.get(op)):
            items = guard[op]
            return op, items if isinstance(items, list) else []
    if truthy(guard.get("not")):
        return "not", [guard["not"]]
    return None, []


class _Spec:
    """Variables of one guard: parameters with their literals, and the context keys it references."""

    def __init__(self, guard, context: Mapping | None):
        self.params: dict[str, list] = {}
        self.ctx_keys: list[str] = []
        self.links: dict[str, list[str]] = {}
        self.fixed_context = context
        self._walk(guard, 0)

    def _walk(self, node, depth: int) -> None:
        if not isinstance(node, Mapping) or depth > am.MAX_NESTING:
            return
        op, kids = _children(node)
        if op is not None:
            for kid in kids:
                self._walk(kid, depth + 1)
            return
        name = _param_name(node)
        literals = self.params.setdefault(name, [])
        raw = node.get("value", UNDEF)
        if isinstance(raw, str) and raw.startswith(CTX_PREFIX):
            key = raw[len(CTX_PREFIX):]
            if self.fixed_context is not None:
                value = self.fixed_context.get(key, UNDEF)
                self._add(literals, value)
                if isinstance(value, list):
                    for member in value:
                        self._add(literals, member)
            else:
                if key not in self.ctx_keys:
                    self.ctx_keys.append(key)
                self.links.setdefault(name, [])
                if key not in self.links[name]:
                    self.links[name].append(key)
        elif isinstance(raw, list):
            for member in raw:
                self._add(literals, member)
            self._add(literals, raw)
        elif raw is not UNDEF:
            self._add(literals, raw)

    @staticmethod
    def _add(pool: list, value) -> None:
        if all(_key(value) != _key(x) for x in pool):
            pool.append(value)

    def all_literals(self) -> list:
        out: list = []
        for literals in self.params.values():
            for value in literals:
                if not isinstance(value, list):
                    self._add(out, value)
        return out


def _fresh(avoid: Iterable) -> list[str]:
    """Two non-empty strings that differ from every literal in ``avoid`` (deterministic)."""
    taken = {x for x in avoid if isinstance(x, str)}
    out: list[str] = []
    n = 1
    while len(out) < 2:
        candidate = f"sample{n}"
        if candidate not in taken:
            out.append(candidate)
        n += 1
    return out


def _pools(spec: _Spec, fixed_params: Mapping) -> tuple[list, list]:
    """``(variables, pools)``: variables are ``("p", name)`` and ``("c", key)`` in a fixed order."""
    literals = spec.all_literals()
    fresh = _fresh(literals)
    base: list = []
    for value in (*fresh[:1], UNDEF, "", None, *fresh[1:]):
        _Spec._add(base, value)
    variables: list = []
    pools: list = []
    for name in sorted(spec.params):
        variables.append(("p", name))
        if name in fixed_params:
            pools.append([fixed_params[name]])
            continue
        pool: list = []
        for value in spec.params[name]:
            _Spec._add(pool, value)
        for value in base:
            _Spec._add(pool, value)
        for key in spec.links.get(name, ()):
            for value in literals:
                _Spec._add(pool, value)
        pools.append(pool)
    for key in sorted(spec.ctx_keys):
        variables.append(("c", key))
        pool = []
        for value in literals:
            _Spec._add(pool, value)
        for value in base:
            _Spec._add(pool, value)
        for value in ([], [fresh[0]], list(literals)):
            _Spec._add(pool, value)
        pools.append(pool)
    return variables, pools


def _assignment(variables: list, combo: tuple, fixed_context: Mapping | None) -> dict:
    params: dict = {}
    ctx: dict = dict(fixed_context) if fixed_context is not None else {}
    for (kind, name), value in zip(variables, combo):
        if value is UNDEF:
            continue
        (params if kind == "p" else ctx)[name] = value
    return {"params": params, "context": ctx}


def _search(guard, want: bool, context: Mapping | None, fixed_params: Mapping, max_combos: int):
    """The first assignment whose evaluation equals ``want``; ``None`` if none exists; ``UNDECIDED`` above the cap."""
    spec = _Spec(guard, context)
    variables, pools = _pools(spec, fixed_params)
    total = 1
    for pool in pools:
        total *= len(pool)
        if total > max_combos:
            return UNDECIDED
    for combo in itertools.product(*pools):
        assignment = _assignment(variables, combo, context)
        if eval_guard(guard, assignment) is want:
            return assignment
    return None


# --------------------------------------------------------------------------------------------------------
# Public: synthesis and classification
# --------------------------------------------------------------------------------------------------------


def synthesise(guard, context: Mapping | None = None, *, satisfy: bool = True, fixed_params: Mapping | None = None,
               max_combos: int = MAX_COMBOS) -> dict | None:
    """Values that make ``guard`` true (``satisfy``) or false: ``{"params": {...}, "context": {...}}``.

    ``context`` fixes the case context (``$ctx`` references read it); ``None`` leaves the referenced keys free and they
    appear in the returned ``context``. ``fixed_params`` pins parameters. ``None`` means no such assignment exists, or
    the search was too large; use ``classify_guard`` to tell the two apart. A missing guard (``None``) is always true.
    """
    found = _search(guard, bool(satisfy), context, fixed_params or {}, max_combos)
    return None if found is UNDECIDED else found


def classify_guard(guard, context: Mapping | None = None, *, fixed_params: Mapping | None = None,
                   max_combos: int = MAX_COMBOS) -> dict:
    """``{verdict, satisfying, falsifying}``; verdict is ``never``, ``always``, ``contingent`` or ``undecided``."""
    fixed = fixed_params or {}
    sat = _search(guard, True, context, fixed, max_combos)
    fal = _search(guard, False, context, fixed, max_combos)
    if sat is UNDECIDED or fal is UNDECIDED:
        verdict = UNDECIDED
    elif sat is None:
        verdict = NEVER
    elif fal is None:
        verdict = ALWAYS
    else:
        verdict = CONTINGENT
    return {"verdict": verdict, "satisfying": None if sat is UNDECIDED else sat,
            "falsifying": None if fal is UNDECIDED else fal}


def _walk(guard, path: str, context, fixed, max_combos: int, depth: int = 0):
    """``(path, verdict)`` of the root and of the never / always branches below a contingent combinator.

    A branch inside an already reported subtree is not reported again.
    """
    verdict = classify_guard(guard, context, fixed_params=fixed, max_combos=max_combos)["verdict"]
    if verdict in (NEVER, ALWAYS, UNDECIDED):
        yield path, verdict
        return
    if not isinstance(guard, Mapping) or depth > am.MAX_NESTING:
        return
    op, kids = _children(guard)
    for index, kid in enumerate(kids):
        child_path = f"{path}.{op}[{index}]" if op in ("all", "any") else f"{path}.not"
        yield from _walk(kid, child_path, context, fixed, max_combos, depth + 1)


_KIND = {NEVER: "never_satisfiable", ALWAYS: "always_true", UNDECIDED: "undecided"}
_MESSAGE = {
    NEVER: "no values satisfy this guard, so it can never pass",
    ALWAYS: "every value satisfies this guard, so it constrains nothing",
    UNDECIDED: "the guard has too many variables to decide exhaustively",
}


def guard_findings(guard, where: Mapping, *, context: Mapping | None = None, fixed_params: Mapping | None = None,
                   never_severity: str = "WARN", max_combos: int = MAX_COMBOS) -> list[dict]:
    """SM-006 findings of one guard (empty for a contingent guard whose branches are all contingent)."""
    if guard is None:
        return []
    out = []
    for path, verdict in _walk(guard, "$", context, fixed_params or {}, max_combos):
        severity = never_severity if verdict == NEVER else "INFO" if verdict == UNDECIDED else "WARN"
        out.append({"code": RULE_ID, "severity": severity, "kind": _KIND[verdict], "where": dict(where), "path": path,
                    "message": _MESSAGE[verdict]})
    return out


# --------------------------------------------------------------------------------------------------------
# Engine probe of machine transitions
# --------------------------------------------------------------------------------------------------------


def _probe_model(model: Mapping) -> tuple[dict, str]:
    """A copy of ``model`` with one synthetic source node that reaches every machine node over a runtime edge."""
    taken = {n["id"] for n in model.get("nodes", [])}
    probe = _PROBE_NODE
    while probe in taken:
        probe += "x"
    nodes = list(model.get("nodes", [])) + [{"id": probe, "name": probe, "kind": "Module", "props": {}}]
    edges = list(model.get("edges", []))
    for machine in model.get("machines", []):
        edges.append({"id": f"{probe}:owns:{machine['nodeId']}", "sourceId": probe, "targetId": machine["nodeId"],
                      "relation": "owns", "static": False, "sourcePortId": None, "targetPortId": None})
    return {**model, "nodes": nodes, "edges": edges, "cases": []}, probe


def _shadowed(machine: Mapping, position: int) -> bool:
    transitions = machine["transitions"]
    t = transitions[position]
    return any(u["from"] == t["from"] and u["to"] == t["to"] for u in transitions[:position])


def probe_transitions(model: Mapping, machines: Iterable[Mapping] | None = None) -> dict:
    """Run every guarded transition through the engine with the synthesised values.

    For each guarded, non-shadowed transition the scan builds a one-event case in the machine's state ``from`` and asserts
    that the satisfying values are accepted (trace outcome ``OK``) and the falsifying values are rejected with cause
    ``transition_guard``. Returns ``{"probes": n, "skipped": n, "mismatches": [finding...]}``.
    """
    machines = list(model.get("machines", []) if machines is None else machines)
    probe_model, probe = _probe_model(model)
    ix = index_model(probe_model)
    out = {"probes": 0, "skipped": 0, "mismatches": []}
    for machine in machines:
        for position, tr in enumerate(machine.get("transitions", [])):
            guard = tr.get("guard")
            if guard is None:
                continue
            if _shadowed(machine, position):
                out["skipped"] += 1
                continue
            fixed = {"target": tr["to"]}
            verdict = classify_guard(guard, None, fixed_params=fixed)
            for label, assignment in (("accept", verdict["satisfying"]), ("reject", verdict["falsifying"])):
                if assignment is None:
                    out["skipped"] += 1
                    continue
                event = {"seq": 1, "type": "Transition", "from": probe, "to": machine["nodeId"],
                         "params": {**assignment["params"], "target": tr["to"]}}
                case = {"caseId": "scan", "category": "normal", "context": assignment["context"], "events": [event],
                        "initialMachines": {machine["nodeId"]: tr["from"]}, "policy": {}, "evidenceIds": []}
                run = simulate(probe_model, case, None, {"index": ix})
                entry = run["trace"][0] if run["trace"] else None
                accepted = bool(entry) and entry["outcome"] == "OK"
                cause = ((entry or {}).get("cause") or {}).get("code")
                out["probes"] += 1
                ok = accepted if label == "accept" else (not accepted and cause == "transition_guard")
                if not ok:
                    out["mismatches"].append({
                        "code": RULE_ID, "severity": "ERROR", "kind": "probe_mismatch",
                        "where": {"machineId": machine["id"], "transitionId": tr["id"]}, "path": "$",
                        "message": f"synthesised values for '{label}' disagree with the engine",
                        "args": {"expected": label, "outcome": (entry or {}).get("outcome"), "cause": cause}})
    return out


# --------------------------------------------------------------------------------------------------------
# Whole model
# --------------------------------------------------------------------------------------------------------


def scan_model(model: Mapping, *, index: Index | None = None, probe: bool = True, max_combos: int = MAX_COMBOS) -> dict:
    """SM-006 over a whole expanded model.

    Returns ``{"findings": [...], "stats": {...}}``. ``findings`` are ordered: machines, ``change_guard`` interventions,
    case events; each is ``{code, severity, kind, where, path, message[, args]}``. A never-satisfiable transition guard is
    an ERROR (the transition can never fire); every other finding is a WARN, and ``undecided`` is INFO.
    """
    ix = index or index_model(model)
    findings: list[dict] = []
    stats = {"guards": 0, "transitionGuards": 0, "interventionGuards": 0, "eventGuards": 0, "probes": 0, "probesSkipped": 0}
    for machine in ix.machines.values():
        for tr in machine.get("transitions", []):
            if tr.get("guard") is not None:
                stats["guards"] += 1
                stats["transitionGuards"] += 1
                findings.extend(guard_findings(tr["guard"], {"machineId": machine["id"], "transitionId": tr["id"]},
                                               never_severity="ERROR", max_combos=max_combos))
    for iv in model.get("interventions", []):
        for atom in flatten_deltas([iv]):
            if isinstance(atom, Mapping) and atom.get("type") == "change_guard" and atom.get("guard") is not None:
                stats["guards"] += 1
                stats["interventionGuards"] += 1
                findings.extend(guard_findings(atom["guard"], {"interventionId": iv.get("interventionId"),
                                                               "transitionId": atom.get("transitionId")}, max_combos=max_combos))
    for case in model.get("cases", []):
        context = case.get("context") if isinstance(case.get("context"), Mapping) else {}
        for event in case.get("events", []):
            if truthy(event.get("guard")):
                stats["guards"] += 1
                stats["eventGuards"] += 1
                findings.extend(guard_findings(event["guard"], {"caseId": case.get("caseId"), "seq": event.get("seq")},
                                               context=context, max_combos=max_combos))
    if probe:
        result = probe_transitions(model)
        stats["probes"], stats["probesSkipped"] = result["probes"], result["skipped"]
        findings.extend(result["mismatches"])
    return {"findings": findings, "stats": stats}
