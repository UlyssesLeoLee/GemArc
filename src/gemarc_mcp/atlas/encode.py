"""Encoder: an expanded model -> ``.atl`` text or ``.jsonl`` text (the inverse of ``expand``).

``expand_sources(encode_atl(m), meta_of(m), stamps=stamps_of(m))`` returns ``m`` for every contract-conformant model
whose positional text cells are non-empty (neither the ``.atl`` nor the ``.jsonl`` front-end accepts an empty positional
text cell, so a model with an empty ``sub`` or ``label`` raises ``EncodeError`` for both formats; the ``empty-strings``
preset of the synthetic generator is such a model). The encoder writes only what an author writes: derived
fields, defaults and inherited values are left out (``verif=`` only when it differs from the derived default, unless
``state_hypothetical=True``, which also writes ``verif=Hypothetical`` on a node without evidence so the claim is stated and
EV-009 stays quiet; ``ev=^``
when the evidence is the endpoint union, ``alias=`` only when the id is not derived, ``owner=`` for the generated
``owns`` edges, ``tests=`` / ``edges=`` only when they differ from their defaults, inherited ``props`` not repeated).
``lineRange`` is a verifier stamp and travels separately (``stamps_of``).

Output files are named ``NNN-<group>.atl`` (or ``.jsonl``) in reading order and never exceed ``max_bytes`` unless one
row alone does. The default ``max_bytes`` is the hard limit (64 KiB, ST-008); pass ``ADVISORY_BYTES`` to get files that stay
below the 8 KiB size warning (SZ-001). Deterministic: no timestamps, sorted JSON keys inside options. Standard library only.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import model as am
from . import vocab
from .format import atl as atl_fmt
from .format import jsonl as jsonl_fmt
from .format import rows as rw
from .format.rows import SECTIONS

#: Slice size at which the check starts to warn (SZ-001, 8 KiB); a little less, so a file never crosses it.
ADVISORY_BYTES = 8 * 1024 - 64

#: Option names that the ``.atl`` writer lifts into ``@set`` lines (``.jsonl`` repeats them on every row).
SET_KEYS = {"nodes": ("room", "kind"), "evidences": ("repo",), "events": ("case",), "transitions": ("machine",)}


class EncodeError(ValueError):
    """The model cannot be written in the author format (for example an empty positional text)."""


def meta_of(model: Mapping) -> dict:
    """The ``meta`` an ``init`` would hand to ``expand`` for this model."""
    return dict(model["meta"])


def stamps_of(model: Mapping) -> dict:
    """``{evidenceId: [from, to]}`` of every stamped evidence (the verifier's data, not authored text)."""
    return {e["evidenceId"]: list(e["lineRange"]) for e in model["evidences"] if e.get("lineRange") is not None}


# ---------------------------------------------------------------------------------------------------------------
# Model -> abstract rows
# ---------------------------------------------------------------------------------------------------------------


def _need_text(section: str, key: str, value: Any, ident: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EncodeError(f"{section}[{ident}].{key} is empty: a positional text cell is never empty in .atl/.jsonl")
    return value


def _owns_suffix(model: Mapping, profile, mode: str, ev_index, node_by_id, machine_ids) -> tuple[dict, int]:
    """``({machine id: owner}, n)``: the trailing edges that ``owner=`` regenerates, and how many edges are authored rows."""
    edges, machines = model["edges"], model["machines"]
    position = {m["id"]: i for i, m in enumerate(machines)}
    by_id = {m["id"]: m for m in machines}
    owners: dict[str, str] = {}
    last = len(machines)
    end = len(edges)
    while end > 0:
        edge = edges[end - 1]
        machine = by_id.get(edge["targetId"])
        if machine is None or edge["relation"] != "owns" or machine["id"] in owners or position[machine["id"]] >= last:
            break
        node = node_by_id.get(machine["id"])
        if node is None:
            break
        try:
            expected = am.build_owns_edge(edge["sourceId"], machine, node, evidence_kinds=am.evidence_kinds_of(machine["evidenceIds"], ev_index),
                                          machine_ids=machine_ids, mode=mode, profile=profile)
        except am.AtlasModelError:
            break
        if expected != edge:
            break
        owners[machine["id"]] = edge["sourceId"]
        last = position[machine["id"]]
        end -= 1
    return owners, end


def model_rows(model: Mapping, *, profile: "vocab.Profile | None" = None, state_hypothetical: bool = False) -> list[tuple[str, dict]]:
    """``[(section, values)]`` in reading order; ``values`` use the typed form of ``rows.Row.values``."""
    meta = model["meta"]
    p = profile or vocab.load_profile(meta.get("profile", vocab.DEFAULT_PROFILE))
    mode = meta.get("mode", "asis")
    ev_index = {e["evidenceId"]: e for e in model["evidences"]}
    node_by_id = {n["id"]: n for n in model["nodes"]}
    room_by_id = {r["id"]: r for r in model["space"]["rooms"]}
    floor_by_id = {f["id"]: f for f in model["space"]["floors"]}
    building_by_id = {b["id"]: b for b in model["space"]["buildings"]}
    machine_ids = {m["id"] for m in model["machines"]}
    out: list[tuple[str, dict]] = []

    def kinds(ids):
        return am.evidence_kinds_of(ids, ev_index)

    for b in model["space"]["buildings"]:
        out.append(("buildings", {"id": b["id"], "name": _need_text("buildings", "name", b["name"], b["id"]), "trust": b["trust"]}))
    for f in model["space"]["floors"]:
        out.append(("floors", {"id": f["id"], "building": f["buildingId"], "name": _need_text("floors", "name", f["name"], f["id"]), "level": f["level"]}))
    for r in model["space"]["rooms"]:
        v = {"id": r["id"], "floor": r["floorId"], "name": _need_text("rooms", "name", r["name"], r["id"])}
        for key in ("layer", "tier", "area"):
            if key in r:
                v[key] = r[key]
        out.append(("rooms", v))

    for n in model["nodes"]:
        room = room_by_id.get(n["roomId"])
        floor = floor_by_id.get(room["floorId"]) if room else None
        building = building_by_id.get(floor["buildingId"]) if floor else None
        inherited = am.inherit_props(None, room, building)
        authored = {k: val for k, val in n["props"].items() if not (k in inherited and inherited[k] == val)}
        v = {"id": n["id"], "name": _need_text("nodes", "name", n["name"], n["id"]), "sub": _need_text("nodes", "sub", n["sub"], n["id"]),
             "room": n["roomId"], "kind": n["kind"]}
        if n["evidenceIds"]:
            v["ev"] = list(n["evidenceIds"])
        if n["verification"] != am.default_verification_for_node(kinds(n["evidenceIds"]), mode, profile=p) or (
                state_hypothetical and not n["evidenceIds"] and n["verification"] == "Hypothetical"):
            v["verif"] = n["verification"]
        if authored:
            v["props"] = authored
        for key in ("group", "level"):
            if key in n:
                v[key] = n[key]
        out.append(("nodes", v))

    owners, authored_edges = _owns_suffix(model, p, mode, ev_index, node_by_id, machine_ids)
    for e in model["edges"][:authored_edges]:
        src, tgt = node_by_id.get(e["sourceId"], {}), node_by_id.get(e["targetId"], {})
        union = am.inherit_edge_evidence(src.get("evidenceIds", []), tgt.get("evidenceIds", []))
        inherited = bool(e["evidenceIds"]) and list(e["evidenceIds"]) == union
        v = {"from": e["sourceId"], "rel": e["relation"], "to": e["targetId"], "label": _need_text("edges", "label", e["label"], e["id"])}
        if inherited:
            v["ev"] = "^"
        elif e["evidenceIds"]:
            v["ev"] = list(e["evidenceIds"])
        if e["verification"] != am.default_verification_for_edge(kinds(e["evidenceIds"]), mode, inherited, profile=p):
            v["verif"] = e["verification"]
        if "cardinality" in e:
            v["card"] = e["cardinality"]
        try:
            derived = am.edge_id(e["sourceId"], e["relation"], e["targetId"])
        except am.AtlasModelError:
            derived = None
        if e["id"] != derived:
            v["alias"] = e["id"]
        if "group" in e:
            v["group"] = e["group"]
        out.append(("edges", v))

    for ev in model["evidences"]:
        v = {"id": ev["evidenceId"], "file": _need_text("evidences", "file", ev["filePath"], ev["evidenceId"]),
             "anchor": _need_text("evidences", "anchor", ev["anchor"], ev["evidenceId"]),
             "topic": _need_text("evidences", "topic", ev["topic"], ev["evidenceId"])}
        if ev["repository"] and ev["sourceKind"] != "user-requirement":
            v["repo"] = ev["repository"]
        out.append(("evidences", v))

    for m in model["machines"]:
        v = {"id": m["id"], "initial": m["initial"]}
        if m["id"] in owners:
            v["owner"] = owners[m["id"]]
        if m["errorCode"] is not None:
            v["err"] = m["errorCode"]
        if m["evidenceIds"]:
            v["ev"] = list(m["evidenceIds"])
        out.append(("machines", v))
    for m in model["machines"]:
        for t in m["transitions"]:
            v = {"from": t["from"], "to": t["to"], "machine": m["id"]}
            if "guard" in t:
                v["guard"] = t["guard"]
            if "errorCode" in t:
                v["err"] = t["errorCode"]
            if t["id"] != am.transition_id(m["id"], t["from"], t["to"]):
                v["id"] = t["id"]
            out.append(("transitions", v))

    for t in model["tests"]:
        out.append(("tests", {"id": t["id"], "title": _need_text("tests", "title", t["title"], t["id"])}))

    for c in model["cases"]:
        v = {"id": c["caseId"], "cat": c["category"], "title": _need_text("cases", "title", c["title"], c["caseId"])}
        if c["requirementIds"]:
            v["req"] = list(c["requirementIds"])
        if c["evidenceIds"]:
            v["ev"] = list(c["evidenceIds"])
        policy = {k: val for k, val in c["policy"].items() if val != am.DEFAULT_POLICY[k]}
        if policy:
            v["policy"] = policy
        if c["context"]:
            v["ctx"] = c["context"]
        if c.get("initialMachines"):
            v["init"] = c["initialMachines"]
        if list(c["linkedTestIds"]) != am.default_linked_tests(c["requirementIds"], model["requirements"]):
            v["tests"] = list(c["linkedTestIds"])
        out.append(("cases", v))
    for c in model["cases"]:
        for e in c["events"]:
            v = {"type": e["type"], "from": e["from"], "to": e["to"], "case": c["caseId"]}
            for key in am.EVENT_OPTION_KEYS:
                if key in e:
                    v[key] = e[key]
            out.append(("events", v))
    for c in model["cases"]:
        if c["expectedTerminalState"] or c.get("expectedOutcomes") or c.get("expectedBy"):
            items = [k if s == am.DEFAULT_EXPECTED_STATE else f"{k}={s}" for k, s in c["expectedTerminalState"].items()]
            v = {"case": c["caseId"], "nodes": items}
            if c.get("expectedBy"):
                v["by"] = c["expectedBy"]
            if c.get("expectedOutcomes"):
                v["outcomes"] = c["expectedOutcomes"]
            out.append(("expect", v))

    edges = model["edges"]
    for iv in model["interventions"]:
        out.append(("interventions", _intervention_values(iv, p, edges, model["ports"])))

    for r in model["requirements"]:
        v = {"id": r["id"], "title": _need_text("requirements", "title", r["title"], r["id"]),
             "source": _need_text("requirements", "source", r["source"], r["id"])}
        if r["nodeIds"]:
            v["nodes"] = list(r["nodeIds"])
        if list(r["edgeIds"]) != am.default_requirement_edges(r["nodeIds"], edges):
            v["edges"] = list(r["edgeIds"])
        for key, field_name in (("tests", "tstIds"), ("ev", "evidenceIds"), ("tbd", "tbdIds"), ("design", "designIds")):
            if r[field_name]:
                v[key] = list(r[field_name])
        out.append(("requirements", v))
    for t in model["tbds"]:
        v = {"id": t["id"], "title": _need_text("tbds", "title", t["title"], t["id"]), "decision": _need_text("tbds", "decision", t["decision"], t["id"])}
        if t["evidenceIds"]:
            v["ev"] = list(t["evidenceIds"])
        out.append(("tbds", v))
    for pol in model["policies"]:
        v = {"id": pol["id"], "text": _need_text("policies", "text", pol["text"], pol["id"]), "src": pol["src"]}
        if pol["scope"] != "all":
            v["scope"] = pol["scope"]
        if pol["level"] != "REQ":
            v["level"] = pol["level"]
        if pol["chapter"] is not None:
            v["chapter"] = pol["chapter"]
        if pol["kind"] != "constraint":
            v["kind"] = pol["kind"]
        if pol["by"] != "ai":
            v["by"] = pol["by"]
        out.append(("policies", v))
    return out


def _intervention_values(iv: Mapping, p, edges, ports) -> dict:
    v = {"id": iv["interventionId"], "type": iv["type"],
         "description": _need_text("interventions", "description", iv["description"], iv["interventionId"]),
         "applies": list(iv["appliesTo"])}
    item = p.item("interventionTypes", iv["type"])
    allowed = (set(item["required"]) | set(item["optional"])) if item else set()
    expect = iv["expect"]
    for src, dst in (("outcomeClass", "class"), ("terminal", "terminal"), ("status", "status")):
        if src in expect:
            v[dst] = expect[src]
    for key in ("why",):
        if iv.get(key):
            v[key] = iv[key]
    if iv.get("note"):
        v["note"] = iv["note"]
    params: dict[str, Any] = {}
    for key in am.ATOMIC_PARAM_KEYS:
        if key in iv and key not in ("element", "rewrite", "parts"):
            params[key] = iv[key]
    for key in ("targetNodeId", "targetEdgeId"):
        if key in params and key in allowed:
            v["target"] = params.pop(key)
            break
    if params:
        v["params"] = params
    if "parts" in iv:
        v["parts"] = iv["parts"]
    if "element" in iv:
        element = dict(iv["element"])
        rewrites = iv.get("rewrite", [])
        edge_by_id = {e["id"]: e for e in edges}
        replaced = [edge_by_id[r["replaceEdgeId"]] for r in rewrites if r.get("disposition") == "replace" and r.get("replaceEdgeId") in edge_by_id]
        derived_in, derived_out = am.element_port_types(replaced, ports)
        inputs, outputs = am.derive_element_ports(element["elementId"], derived_in, derived_out, profile=p)
        if element.get("inputPorts") == inputs:
            element.pop("inputPorts")
        if element.get("outputPorts") == outputs:
            element.pop("outputPorts")
        if set(element) == {"elementId", "kind"}:
            v["element"] = element["elementId"]
            v["use"] = element["kind"]
        else:
            v["element"] = element
        if rewrites:
            v["rewrite"] = [r["replaceEdgeId"] if r.get("disposition") == "replace" else f"{r['replaceEdgeId']}={r['disposition']}" for r in rewrites]
    return v


# ---------------------------------------------------------------------------------------------------------------
# Rows -> text
# ---------------------------------------------------------------------------------------------------------------


def _format_option(section: str, key: str, value: Any) -> str:
    f = SECTIONS[section].field(key)
    t = f.type if f else "text"
    if t == "text":
        return atl_fmt.format_text_value(value)
    if t == "int":
        return str(value)
    if t == "bool":
        return "true" if value else "false"
    if t == "intlist":
        return str(value) if isinstance(value, int) else atl_fmt.json_text(value)
    if t in ("ids", "strs", "rewrite", "edgeev"):
        if value == "^":
            return "^"
        return atl_fmt.format_list_value(value) if value else "[]"
    if t == "elem":
        return atl_fmt.format_text_value(value) if isinstance(value, str) else atl_fmt.json_text(value)
    return atl_fmt.json_text(value)  # obj, list


def _format_cell(section: str, f: rw.Field, value: Any) -> str:
    if f.type == "int":
        return str(value)
    if f.type == "expects":
        return atl_fmt.format_cell(section, ",".join(value)) if value else "-"
    return atl_fmt.format_cell(section, value)


def _atl_row(section: str, values: Mapping, set_keys: tuple) -> str:
    spec = SECTIONS[section]
    cells = [_format_cell(section, f, values[f.name]) for f in spec.columns]
    options = [(f.name, _format_option(section, f.name, values[f.name])) for f in spec.options
               if f.name in values and f.name not in set_keys]
    return atl_fmt.format_row(section, cells, options)


def atl_record(section: str, values: Mapping) -> str:
    """One row as ``.atl`` text, without ``@set`` (every value written on the row), for tools that show a record."""
    return _atl_row(section, values, ())


def jsonl_record(section: str, values: Mapping) -> str:
    """One row as a ``.jsonl`` line, for tools that show a record."""
    return jsonl_fmt.row_line(section, values)


def _set_line(section: str, changes: Mapping[str, str | None]) -> str:
    parts = []
    for key, value in changes.items():
        parts.append(f"{key}=" if not value else f"{key}={atl_fmt.format_text_value(value)}")
    return "@set " + " ".join(parts)


def _stream(model: Mapping, state_hypothetical: bool = False):
    """``(group, section, values)`` in reading order: groups in ``FILE_GROUPS`` order, rows in model order."""
    by_section: dict[str, list[dict]] = {}
    for section, values in model_rows(model, state_hypothetical=state_hypothetical):
        by_section.setdefault(section, []).append(values)
    for group, sections in rw.FILE_GROUPS:
        for section in sections:
            for values in by_section.get(section, []):
                yield group, section, values


def _pack(stream, header: str, max_bytes: int, extension: str, section_header, row_text) -> dict[str, str]:
    """Pack rows into files named ``NNN-<group><ext>``; a file is closed when the next row would pass ``max_bytes``."""
    files: list[tuple[str, list[str]]] = []
    name: str | None = None
    lines: list[str] = []
    size = 0
    section: str | None = None
    state: dict = {}

    def cost(block: list[str]) -> int:
        return sum(len(x.encode("utf-8")) + 1 for x in block)

    for group, sec, values in stream:
        if lines and group != name:
            files.append((name, lines))
            lines = []
        if not lines:
            lines, size, name, section, state = [header], cost([header]), group, None, {}
        head = [] if sec == section else [h for h in (section_header(sec),) if h]
        row_lines, new_state = row_text(sec, values, state if sec == section else {})
        block = head + row_lines
        if size + cost(block) > max_bytes and len(lines) > 1:
            files.append((name, lines))
            lines, size, section, state = [header], cost([header]), None, {}
            head = [h for h in (section_header(sec),) if h]
            row_lines, new_state = row_text(sec, values, {})
            block = head + row_lines
        lines.extend(block)
        size += cost(block)
        section, state = sec, new_state
    if lines:
        files.append((name, lines))
    return {f"{index:03d}-{group}{extension}": "\n".join(body) + "\n" for index, (group, body) in enumerate(files)}


def encode_atl(model: Mapping, *, max_bytes: int = rw.MAX_SLICE_BYTES, state_hypothetical: bool = False) -> dict[str, str]:
    """``{file name: .atl text}`` for the model; files are named ``NNN-<group>.atl`` in reading order."""
    mode = model["meta"].get("mode", "asis")
    header = "#atlas 1" + (f" mode={mode}" if mode != "asis" else "")

    def row_text(section, values, state):
        lines = []
        keys = SET_KEYS.get(section, ())
        state = dict(state)
        changes = {}
        for key in keys:
            if section == "evidences" and values.get("file", "").startswith(am.USER_PREFIX):
                continue  # a user: evidence has no repository whatever @set says
            want = values.get(key, "")
            if state.get(key, "") != want:
                changes[key] = want
        if changes:
            lines.append(_set_line(section, changes))
            state.update(changes)
        lines.append(_atl_row(section, values, keys))
        return lines, state

    return _pack(_stream(model, True) if state_hypothetical else _stream(model), header, max_bytes, ".atl", lambda s: SECTIONS[s].header, row_text) or {"000-empty.atl": header + "\n"}


def encode_jsonl(model: Mapping, *, max_bytes: int = rw.MAX_SLICE_BYTES, state_hypothetical: bool = False) -> dict[str, str]:
    """``{file name: .jsonl text}``; every row is one object with every value written out (no ``@set``)."""
    header = jsonl_fmt.header_line(model["meta"].get("mode", "asis"))

    def row_text(section, values, state):
        return [jsonl_fmt.row_line(section, values)], state

    return _pack(_stream(model, True) if state_hypothetical else _stream(model), header, max_bytes, ".jsonl", lambda s: None, row_text) or {"000-empty.jsonl": header + "\n"}
