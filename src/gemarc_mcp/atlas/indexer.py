"""``index``: compact, paged lines about the committed model, so the AI never has to re-read a slice to resume.

Scopes: ``nodes`` (all nodes), ``room:<id>``, ``req:<id>``, ``case:<id>``, ``ids:<prefix>`` (every authored id that starts with
the prefix, by kind). ``with_edges`` adds the edges of the selected nodes, ``ids`` narrows ``nodes`` to the named ids.
``detail="line"`` is one short line per item (``id kind name``); ``detail="expanded"`` is one compact JSON record per item with
the ``file:line`` that defines it (the provenance map of the expander). ``offset`` pages through the lines.

The functions return plain lists of lines; ``tools.index`` applies the byte budget and the reply envelope.
Standard library only.
"""
from __future__ import annotations

import json
from typing import Mapping

from . import expand as ex
from .project import Config, Paths
from .slices import list_slices, read_bytes
from .status import element_templates

SCOPES = ("nodes", "room:", "req:", "case:", "ids:")


class IndexError_(ValueError):
    """A bad scope or an unknown id; the message is shown to the AI."""


def load_model(paths: Paths, config: Config):
    """``(model, provenance)`` of the committed slices (parse and expand only; no rule runs)."""
    files = list_slices(paths)
    parsed = [ex.parse_source(f.fname, read_bytes(f.path)) for f in files]
    expansion = ex.expand_slices(parsed, config.meta(), element_templates=element_templates())
    return expansion.model, expansion.provenance


def _clip(text, limit: int = 60) -> str:
    """One printable line of at most ``limit`` characters: control, format, line-separator and bidi characters become blanks
    (stored text is data the AI reads; it must not carry an escape sequence or pose as a line of its own)."""
    text = " ".join("".join(ch if ch.isprintable() else " " for ch in str(text)).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _safe(value, limit: int = 120):
    """``value`` with every string inside it cleaned and cut (used for the ``detail=expanded`` records)."""
    if isinstance(value, str):
        return _clip(value, limit)
    if isinstance(value, list):
        return [_safe(x, limit) for x in value[:40]]
    if isinstance(value, dict):
        return {_clip(k, 40): _safe(v, limit) for k, v in list(value.items())[:40]}
    return value


def node_line(n: Mapping) -> str:
    return f"{n['id']} {n.get('kind', '?')} {_clip(n.get('name', ''))}"


def edge_line(e: Mapping) -> str:
    label = f" {_clip(e['label'], 40)}" if e.get("label") else ""
    return f"{e['sourceId']} {e['relation']} {e['targetId']}{label}"


def _expanded(record: Mapping, prov: Mapping, key: str, fields) -> str:
    out = {"at": prov.get(key, "")}
    for name in fields:
        value = record.get(name)
        if value not in (None, "", [], {}):
            out[name] = _safe(value)
    return json.dumps(out, ensure_ascii=False, separators=(",", ":"), sort_keys=False)


_NODE_FIELDS = ("id", "kind", "name", "sub", "roomId", "evidenceIds", "verification")
_EDGE_FIELDS = ("id", "label", "evidenceIds", "verification")
_CASE_FIELDS = ("caseId", "category", "title", "requirementIds", "expectedTerminalState", "expectedBy")
_REQ_FIELDS = ("id", "title", "source", "nodeIds", "tstIds", "tbdIds")


def _node_items(nodes, edges, prov, detail, with_edges: bool) -> list:
    out = []
    ids = {n["id"] for n in nodes}
    shown: set = set()

    def edge_item(e) -> str:
        return _expanded(e, prov, f"edges[{e['id']}]", _EDGE_FIELDS) if detail == "expanded" else edge_line(e)

    by_source: dict = {}
    if with_edges:
        for e in edges:
            if e["sourceId"] in ids or e["targetId"] in ids:
                by_source.setdefault(e["sourceId"], []).append(e)
    for n in nodes:  # each node is followed by its own outgoing edges, so one page holds a node and what it does
        out.append(_expanded(n, prov, f"nodes[{n['id']}]", _NODE_FIELDS) if detail == "expanded" else node_line(n))
        for e in by_source.get(n["id"], ()):
            out.append(edge_item(e))
            shown.add(e["id"])
    if with_edges:  # edges that come from outside the selection and point into it
        for e in edges:
            if e["id"] not in shown and (e["sourceId"] in ids or e["targetId"] in ids):
                out.append(edge_item(e))
    return out


def index_lines(model: Mapping, prov: Mapping, scope: str = "nodes", *, with_edges: bool = False, ids=(), detail: str = "line") -> list:
    """The lines of one scope (see the module doc). Raises ``IndexError_`` for a bad scope or an unknown id."""
    if detail not in ("line", "expanded"):
        raise IndexError_("detail is 'line' or 'expanded'")
    scope = (scope or "nodes").strip()
    nodes, edges = model.get("nodes", []), model.get("edges", [])
    by_id = {n["id"]: n for n in nodes}
    if scope == "nodes":
        chosen = nodes
        wanted = [str(x) for x in ids or ()]
        if wanted:
            missing = [x for x in wanted if x not in by_id]
            if missing:
                raise IndexError_("no such node: " + ", ".join(missing[:5]))
            chosen = [by_id[x] for x in wanted]
        return _node_items(chosen, edges, prov, detail, with_edges)
    kind, _, value = scope.partition(":")
    if not value:
        raise IndexError_("scope is nodes, room:<id>, req:<id>, case:<id> or ids:<prefix>")
    if kind == "room":
        rooms = {r["id"]: r for r in (model.get("space") or {}).get("rooms", ())}
        if value not in rooms:
            raise IndexError_(f"no such room: {value}")
        room = rooms[value]
        head = (f"room {value} {_clip(room.get('name', ''))} floor={room.get('floorId')}"
                if detail == "line" else _expanded(room, prov, f"space.rooms[{value}]", ("id", "name", "floorId", "layer", "tier", "area")))
        return [head] + _node_items([n for n in nodes if n.get("roomId") == value], edges, prov, detail, with_edges)
    if kind == "req":
        reqs = {r["id"]: r for r in model.get("requirements", ())}
        if value not in reqs:
            raise IndexError_(f"no such requirement: {value}")
        r = reqs[value]
        cases = [c["caseId"] for c in model.get("cases", ()) if value in c.get("requirementIds", ())]
        if detail == "expanded":
            head = _expanded(r, prov, f"requirements[{value}]", _REQ_FIELDS)
        else:
            head = f"req {value} {_clip(r.get('title', ''), 70)} | source: {_clip(r.get('source', ''), 50)}"
        extra = [] if detail == "expanded" else [f"cases: {', '.join(cases) or '-'}", f"tests: {', '.join(r.get('tstIds', ())) or '-'}"]
        chosen = [by_id[x] for x in r.get("nodeIds", ()) if x in by_id]
        return [head] + extra + _node_items(chosen, edges, prov, detail, with_edges)
    if kind == "case":
        cases = {c["caseId"]: c for c in model.get("cases", ())}
        if value not in cases:
            raise IndexError_(f"no such case: {value}")
        c = cases[value]
        if detail == "expanded":
            out = [_expanded(c, prov, f"cases[{value}]", _CASE_FIELDS)]
            out.extend(json.dumps({"at": prov.get(f"cases[{value}].events[{e.get('seq')}]", ""), **{k: v for k, v in e.items()}},
                                  ensure_ascii=False, separators=(",", ":")) for e in c.get("events", ()))
            return out
        out = [f"case {value} {c.get('category')} {_clip(c.get('title', ''), 70)} | req: {', '.join(c.get('requirementIds', ())) or '-'}"]
        out.extend(f"{e.get('seq')} {e.get('type')} {e.get('from')} -> {e.get('to')}" + (f" {e['ruleId']}" if e.get("ruleId") else "")
                   for e in c.get("events", ()))
        expect = c.get("expectedTerminalState") or {}
        out.append("expect: " + (", ".join(f"{k}={v}" for k, v in expect.items()) or "-"))
        return out
    if kind == "ids":
        out = []
        for label, items, key in (("building", (model.get("space") or {}).get("buildings", ()), "id"), ("floor", (model.get("space") or {}).get("floors", ()), "id"),
                                  ("room", (model.get("space") or {}).get("rooms", ()), "id"), ("node", nodes, "id"), ("edge", edges, "id"),
                                  ("evidence", model.get("evidences", ()), "evidenceId"), ("case", model.get("cases", ()), "caseId"),
                                  ("requirement", model.get("requirements", ()), "id"), ("test", model.get("tests", ()), "id"),
                                  ("tbd", model.get("tbds", ()), "id"), ("intervention", model.get("interventions", ()), "interventionId"),
                                  ("policy", model.get("policies", ()), "id")):
            out.extend(f"{label} {r[key]}" for r in items if isinstance(r.get(key), str) and r[key].startswith(value))
        return out
    raise IndexError_("scope is nodes, room:<id>, req:<id>, case:<id> or ids:<prefix>")
