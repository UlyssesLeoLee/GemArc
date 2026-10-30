"""EV-001, EV-002, EV-006 .. EV-011: evidence and the honesty of the verification claim.

* EV-001 ``Implemented`` needs a source-code evidence (nodes and edges). In to-be mode EV-010 covers the claim instead.
* EV-002 (WARN) an **authored** ``verif=Designed`` on an item that has code evidence. A derived value (an inherited-evidence
  cap, a to-be ceiling) is never reported: the claim is only checked where the author made it.
* EV-006 credential-looking text in an evidence anchor or topic, or in ``props`` (the one credential lint of the evidence
  lane; the text itself is never echoed).
* EV-007 an evidence repository alias that the project did not declare (needs ``meta.repos``, so local only).
* EV-008 (INFO) an evidence nothing cites.
* EV-009 (WARN) a node with no evidence whose ``Hypothetical`` level was defaulted, not stated: ``verif=Hypothetical`` on
  purpose silences it.
* EV-010 in to-be mode ``Implemented`` and ``Observed`` are illegal (the ceiling is ``Designed``).
* EV-011 (INFO) how many edges inherit their evidence from their endpoints (``ev=^``).

Standard library only.
"""
from __future__ import annotations

import re
from typing import Mapping

from .. import evidence as evidence_lane
from .context import Context
from .registry import check

CODE = "source-code"
TOBE_ILLEGAL = ("Implemented", "Observed")
_SECRET_PATH = re.compile(r"^(?P<coll>evidences|nodes|edges|space\.(?:buildings|floors|rooms))\[(?P<index>\d+)\](?P<rest>.*)$")
_NAMES = re.compile(r"\(([^)]*)\)")


def _kinds(ctx: Context) -> dict:
    return {e["evidenceId"]: e["sourceKind"] for e in ctx.records("evidences")}


def _items(ctx: Context):
    for n in ctx.records("nodes"):
        yield "nodes", n["id"], n
    for e in ctx.records("edges"):
        yield "edges", e["id"], e


@check("EV", rules=("EV-001", "EV-002", "EV-006", "EV-007", "EV-008", "EV-009", "EV-010", "EV-011"))
def evidence(ctx: Context) -> None:
    kinds = _kinds(ctx)
    tobe = ctx.tobe
    node_ids = {n["id"] for n in ctx.records("nodes")}
    for collection, identifier, item in _items(ctx):
        key = f"{collection}[{identifier}]"
        at = ctx.at(key)
        level = item.get("verification")
        has_code = any(kinds.get(x) == CODE for x in item.get("evidenceIds", ()))
        what = "node" if collection == "nodes" else "edge"
        if tobe and level in TOBE_ILLEGAL:
            ctx.add("EV-010", path=key + ".verification", at=at, got=level, expected="Designed or lower in to-be mode", entity=key)
        elif level == "Implemented" and not has_code:
            if (collection == "edges" and ctx.has_prov and ctx.derived_field(key + ".evidenceIds")
                    and not (item.get("sourceId") in node_ids and item.get("targetId") in node_ids)):
                continue  # ev=^ with an endpoint that is not written yet: its evidence is unknown (pending); RF-002 names the endpoint
            ctx.add("EV-001", path=key + ".verification", at=at, got=level, expected="a source-code evidence", entity=key)
        elif (level == "Designed" and has_code and not tobe and ctx.has_prov and not ctx.derived_field(key + ".verification")
              and not ctx.derived_field(key + ".evidenceIds")):
            ctx.add("EV-002", path=key + ".verification", at=at, got=level, expected=f"Implemented: the {what} has a source-code evidence",
                    entity=key)
    for n in ctx.records("nodes"):
        key = f"nodes[{n['id']}]"
        defaulted = ctx.has_prov and ctx.derived_field(key + ".verification")  # without provenance "stated" cannot be told
        if not n.get("evidenceIds") and n.get("verification") == "Hypothetical" and defaulted:
            ctx.add("EV-009", path=key + ".evidenceIds", at=ctx.at(key), got="none", expected=">= 1 evidence, or verif=Hypothetical stated",
                    entity=key)
    _unused(ctx)
    _repositories(ctx)
    _secrets(ctx)
    _inherited(ctx)


def _unused(ctx: Context) -> None:
    cited: set = set()
    for collection in ("nodes", "edges", "cases", "machines", "tbds", "requirements"):
        for r in ctx.records(collection):
            cited.update(x for x in r.get("evidenceIds", ()) if isinstance(x, str))
    for e in ctx.records("evidences"):
        if e["evidenceId"] not in cited:
            key = f"evidences[{e['evidenceId']}]"
            ctx.add("EV-008", path=key, at=ctx.at(key), got=e["evidenceId"], expected="cited by a node, edge, case, machine, requirement or TBD")


def _repositories(ctx: Context) -> None:
    meta = ctx.meta
    if not isinstance(meta, Mapping) or "repos" not in meta:
        return  # without ``init`` there is nothing to compare with (the remote check)
    declared = set(meta.get("repos") or ())
    for e in ctx.records("evidences"):
        repo = e.get("repository")
        if repo and repo not in declared:
            key = f"evidences[{e['evidenceId']}]"
            ctx.add("EV-007", path=key + ".repository", at=ctx.at(key), got=repo,
                    expected="one of the project's repos: " + (", ".join(sorted(declared)) or "none declared"), repo=repo)


def _secrets(ctx: Context) -> None:
    """EV-006 from the evidence lane's credential lint, relocated from model indexes to ids."""
    spaces = {part: (ctx.model.get("space") or {}).get(part, ()) for part in ("buildings", "floors", "rooms")}
    for issue in evidence_lane.secret_lint_model(ctx.model):
        m = _SECRET_PATH.match(issue.get("path", ""))
        if not m:
            continue
        coll, index = m.group("coll"), int(m.group("index"))
        if coll.startswith("space."):
            records = spaces.get(coll[6:], ())
            ident = records[index]["id"]
        else:
            records = ctx.model.get(coll, ())
            ident = records[index]["evidenceId" if coll == "evidences" else "id"]
        key = f"{coll}[{ident}]"
        found = _NAMES.search(issue.get("message", ""))
        ctx.add("EV-006", path=f"{key}{m.group('rest')}", at=ctx.at(key), got=f"credential-like text ({found.group(1) if found else 'secret'})",
                expected="no credential text; the value is not echoed")


def _inherited(ctx: Context) -> None:
    first: dict[str, str] = {}
    count: dict[str, int] = {}
    for e in ctx.records("edges"):
        key = f"edges[{e['id']}]"
        if ctx.derived_field(key + ".evidenceIds"):
            at = ctx.at(key)
            file = at.rpartition(":")[0]
            first.setdefault(file, key)
            count[file] = count.get(file, 0) + 1
    for file, key in first.items():
        ctx.add("EV-011", path="edges.evidenceIds", at=ctx.at(key), got=f"{count[file]} edge" + ("" if count[file] == 1 else "s"), expected="edges with own evidence", count=count[file])
