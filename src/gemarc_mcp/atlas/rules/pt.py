"""PT-003 (WARN): the port types at the two ends of an edge are compatible.

A port has the type ``Message`` or ``Record``; ``Any`` (inserted elements) matches both. An edge whose source out-port and
target in-port have different concrete types is reported, scoped by the relation (the hint says which relation carries the
mismatch). It is a WARN and never an error: a module that publishes a message into an event record legitimately crosses
the types, and the reference model has nine such edges by design. Ports are derived from the node kind, so the author
repairs it by changing a node kind or by accepting the warning. Standard library only.
"""
from __future__ import annotations

from .. import diag as dg
from .context import Context
from .registry import Rule, check, register

register(Rule("PT-003", 2, dg.WARN, "type {a} -> {b} crosses relation {rel}: accept it, or give a node the kind whose port type matches",
              "source out-port and target in-port have compatible types"))

WILDCARD = "Any"


def compatible(a, b) -> bool:
    return a == b or a == WILDCARD or b == WILDCARD or not isinstance(a, str) or not isinstance(b, str)


@check("PT", rules=("PT-003",))
def port_types(ctx: Context) -> None:
    ports = {p.get("id"): p.get("type") for p in ctx.records("ports")}
    for e in ctx.records("edges"):
        s, t = ports.get(e.get("sourcePortId")), ports.get(e.get("targetPortId"))
        if s is None or t is None or compatible(s, t):
            continue
        key = f"edges[{e['id']}]"
        ctx.add("PT-003", path=key + ".sourcePortId", at=ctx.at(key), got=f"{s} -> {t}", expected="the same port type (or Any)", entity=key,
                a=s, b=t, rel=e.get("relation"))
