"""RF-001 .. RF-005: references.

The rule is chosen by what the reference points into (design 5.1), the check walks the expanded model:

* RF-001 the spatial chain (floor -> building, room -> floor, node -> room) and one floor per level in a building.
* RF-002 edge endpoints, and a machine that has no node row.
* RF-003 every ``evidenceIds`` entry (nodes, edges, cases, machines, TBDs, requirements).
* RF-004 the scenario layer: event and expectation rows of a case, event endpoints and ``edge``, expected-state nodes,
  initial machines, ``appliesTo`` of an intervention.
* RF-005 the registry layer: what requirements, tests, TBDs, policies and interventions point at (requirement, test, TBD,
  policy ``src``, targets, rewrite edges, machine and transition of a guard change, expected-terminal nodes), and the
  requirement and test ids a case cites.

Unplaceable rows (an event of an unknown case, a transition of an unknown machine) are not in the model: the slice
check hands them over as ``ctx.unplaced`` and they go through the same pending / typo / error logic. Standard library only.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Mapping

from .. import model as am
from .registry import check
from .context import Context


@check("RF", rules=("RF-001", "RF-002", "RF-003", "RF-004", "RF-005"))
def references(ctx: Context) -> None:
    _spatial(ctx)
    _nodes(ctx)
    _edges(ctx)
    _machines(ctx)
    _cases(ctx)
    _requirements(ctx)
    _interventions(ctx)
    _policies(ctx)
    _tbds(ctx)
    _unplaced(ctx)


def _spatial(ctx: Context) -> None:
    space = ctx.model.get("space") or {}
    floors = [f for f in space.get("floors", ()) if isinstance(f, Mapping)]
    for f in floors:
        key = f"space.floors[{f['id']}]"
        ctx.ref("building", f["buildingId"], rule="RF-001", holder=key, path=key + ".buildingId", at=ctx.at(key))
    for r in space.get("rooms", ()):
        key = f"space.rooms[{r['id']}]"
        ctx.ref("floor", r["floorId"], rule="RF-001", holder=key, path=key + ".floorId", at=ctx.at(key))
    seen: dict[tuple, str] = {}
    for f in floors:
        slot = (f["buildingId"], f["level"])
        key = f"space.floors[{f['id']}]"
        if slot in seen:
            ctx.add("RF-001", path=key + ".level", at=ctx.at(key), got=f["level"], expected="a level used once per building",
                    detail=f"same level as {seen[slot]} in {f['buildingId']}: give every floor its own level")
        else:
            seen[slot] = f["id"]


def _nodes(ctx: Context) -> None:
    for n in ctx.records("nodes"):
        key = f"nodes[{n['id']}]"
        at = ctx.at(key)
        ctx.ref("room", n["roomId"], rule="RF-001", holder=key, path=key + ".roomId", at=at)
        ctx.refs("evidence", n.get("evidenceIds", ()), rule="RF-003", holder=key, path=key + ".evidenceIds", at=at)


def _edges(ctx: Context) -> None:
    for e in ctx.records("edges"):
        key = f"edges[{e['id']}]"
        at = ctx.at(key)
        ctx.ref("node", e["sourceId"], rule="RF-002", holder=key, path=key + ".sourceId", at=at)
        ctx.ref("node", e["targetId"], rule="RF-002", holder=key, path=key + ".targetId", at=at)
        ctx.refs("evidence", e.get("evidenceIds", ()), rule="RF-003", holder=key, path=key + ".evidenceIds", at=at)


def _machines(ctx: Context) -> None:
    for m in ctx.records("machines"):
        key = f"machines[{m['id']}]"
        at = ctx.at(key)
        ctx.ref("node", m["id"], rule="RF-002", holder=key, path=key + ".id", at=at, label="node",
            advice="add a nodes row for the machine (kind=State is usual)")
        ctx.refs("evidence", m.get("evidenceIds", ()), rule="RF-003", holder=key, path=key + ".evidenceIds", at=at)


def _cases(ctx: Context) -> None:
    for c in ctx.records("cases"):
        cid = c["caseId"]
        key = f"cases[{cid}]"
        at = ctx.at(key)
        ctx.refs("requirement", c.get("requirementIds", ()), rule="RF-005", holder=key, path=key + ".requirementIds", at=at)
        ctx.refs("evidence", c.get("evidenceIds", ()), rule="RF-003", holder=key, path=key + ".evidenceIds", at=at)
        if not ctx.derived_field(key + ".linkedTestIds"):  # a derived default is the union of the requirements' tests
            ctx.refs("test", c.get("linkedTestIds", ()), rule="RF-005", holder=key, path=key + ".linkedTestIds", at=at)
        for e in c.get("events", ()):
            if not isinstance(e, Mapping):
                continue
            ekey = f"{key}.events[{e.get('seq')}]"
            eat = ctx.at(ekey, key)
            ctx.ref("node", e.get("from"), rule="RF-004", holder=key, path=ekey + ".from", at=eat)
            ctx.ref("node", e.get("to"), rule="RF-004", holder=key, path=ekey + ".to", at=eat)
            if "edge" in e:
                ctx.ref("edge", e["edge"], rule="RF-004", holder=key, path=ekey + ".edge", at=eat)
        xat = ctx.at(key + ".expect", key)
        for node in (c.get("expectedTerminalState") or {}):
            ctx.ref("node", node, rule="RF-004", holder=key, path=f"{key}.expectedTerminalState.{node}", at=xat)
        for node in (c.get("initialMachines") or {}):
            ctx.ref("machine", node, rule="RF-004", holder=key, path=f"{key}.initialMachines.{node}", at=at, label="machine")


def _requirements(ctx: Context) -> None:
    for r in ctx.records("requirements"):
        key = f"requirements[{r['id']}]"
        at = ctx.at(key)
        ctx.refs("node", r.get("nodeIds", ()), rule="RF-005", holder=key, path=key + ".nodeIds", at=at)
        if not ctx.derived_field(key + ".edgeIds"):  # a derived default only lists edges that exist
            ctx.refs("edge", r.get("edgeIds", ()), rule="RF-005", holder=key, path=key + ".edgeIds", at=at)
        ctx.refs("test", r.get("tstIds", ()), rule="RF-005", holder=key, path=key + ".tstIds", at=at)
        ctx.refs("tbd", r.get("tbdIds", ()), rule="RF-005", holder=key, path=key + ".tbdIds", at=at)
        ctx.refs("evidence", r.get("evidenceIds", ()), rule="RF-003", holder=key, path=key + ".evidenceIds", at=at)


def _tbds(ctx: Context) -> None:
    for t in ctx.records("tbds"):
        key = f"tbds[{t['id']}]"
        ctx.refs("evidence", t.get("evidenceIds", ()), rule="RF-003", holder=key, path=key + ".evidenceIds", at=ctx.at(key))


def _policies(ctx: Context) -> None:
    for p in ctx.records("policies"):
        key = f"policies[{p['id']}]"
        at = ctx.at(key)
        ctx.ref("requirement or tbd", p["src"], rule="RF-005", holder=key, path=key + ".src", at=at, label="requirement or tbd")
        scope = p.get("scope", "all")
        if isinstance(scope, str) and scope.startswith("node:"):
            ctx.ref("node", scope[5:], rule="RF-005", holder=key, path=key + ".scope", at=at)
        elif isinstance(scope, str) and scope.startswith("req:"):
            ctx.ref("requirement", scope[4:], rule="RF-005", holder=key, path=key + ".scope", at=at)


def _interventions(ctx: Context) -> None:
    for r in ctx.records("interventions"):
        iid = r["interventionId"]
        key = f"interventions[{iid}]"
        at = ctx.at(key)
        ctx.refs("case", r.get("appliesTo", ()), rule="RF-004", holder=key, path=key + ".appliesTo", at=at)
        try:
            atoms = am.flatten_parts(r)
        except am.AtlasModelError:
            atoms = []  # a malformed combined record is the contract's business (E_FIELD), not a reference problem
        elements = {}
        for atom in atoms:
            if isinstance(atom.get("element"), Mapping) and isinstance(atom["element"].get("elementId"), str):
                elements[atom["element"]["elementId"]] = True
        many = len(atoms) != 1 or atoms[0] is not r
        for index, atom in enumerate(atoms):
            where = f"{key}.parts[{index}]" if many else key
            if "targetNodeId" in atom:
                ctx.ref("node", atom["targetNodeId"], rule="RF-005", holder=key, path=where + ".targetNodeId", at=at)
            if "targetEdgeId" in atom:
                ctx.ref("edge", atom["targetEdgeId"], rule="RF-005", holder=key, path=where + ".targetEdgeId", at=at)
            if "targetEventId" in atom:
                parsed = am.parse_event_id(atom["targetEventId"])
                if parsed is not None:
                    ctx.ref("event", atom["targetEventId"], rule="RF-005", holder=key, path=where + ".targetEventId", at=at,
                            label="event")
            if atom.get("type") == "change_guard" and isinstance(atom.get("machineId"), str):
                if ctx.ref("machine", atom["machineId"], rule="RF-005", holder=key, path=where + ".machineId", at=at) and "transitionId" in atom:
                    machine = next((m for m in ctx.records("machines") if m["id"] == atom["machineId"]), None)
                    if machine is not None:
                        transitions = {t.get("id") for t in machine.get("transitions", ()) if isinstance(t, Mapping)}
                        ctx.ref("transition", atom["transitionId"], rule="RF-005", holder=key, path=where + ".transitionId", at=at,
                                pool=transitions, label=f"transition of {atom['machineId']}")
            for j, entry in enumerate(atom.get("rewrite", ()) if isinstance(atom.get("rewrite"), list) else ()):
                if isinstance(entry, Mapping) and isinstance(entry.get("replaceEdgeId"), str):
                    ctx.ref("edge", entry["replaceEdgeId"], rule="RF-005", holder=key, path=f"{where}.rewrite[{j}].replaceEdgeId", at=at)
        terminal = (r.get("expect") or {}).get("terminal")
        if isinstance(terminal, Mapping):
            pool = ctx.pool("node") | set(elements)
            for node in terminal:
                ctx.ref("node", node, rule="RF-005", holder=key, path=f"{key}.expect.terminal.{node}", at=at, pool=pool,
                        label="node or inserted element")


def _unplaced(ctx: Context) -> None:
    """Rows the expander could not place: ``(rule, kind, value, path, at)`` tuples handed over in ``ctx.unplaced``."""
    for rule, kind, value, path, at in getattr(ctx, "unplaced", ()):
        ctx.ref(kind, value, rule=rule, holder=None, path=path, at=at)


def pending_summary(ctx: Context) -> dict:
    """Pending references grouped by kind: ``{kind: sorted unique ids}`` (slice mode, incoming slices only)."""
    grouped: dict[str, set] = defaultdict(set)
    for kind, value, _ in ctx.pending:
        grouped[kind].add(value)
    return {kind: sorted(values) for kind, values in sorted(grouped.items())}
