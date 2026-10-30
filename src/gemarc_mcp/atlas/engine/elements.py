"""Element templates (``use=<kind>``): complete bodies for inserted elements, wired into the ``insert_element`` path.

An ``insert_element`` intervention needs a full element body (state machine, handlers, resource limits ...); writing that
by hand for every what-if is expensive and error-prone. The data file ``derive/templates/elements.json`` (clean-room, one
generic behaviour per element kind of the vocabulary profile) holds the bodies. This module

* loads it (``load_templates``, ``template_for``: the callable the expander takes as ``element_templates``),
* builds a complete element (``build_element``: template + author overrides + ports derived by ``model``),
* completes a partial element in place of a hand-written one (``complete_element``), and
* completes every ``insert_element`` of a list of interventions, including parts of a ``combined_intervention``
  (``complete_deltas``), so that ``compile_with_templates`` / ``simulate_with_templates`` accept ``{elementId, kind, role}``.

Rules: a template never carries ``elementId``, ``kind`` or ports; authored keys always win over template keys; port ids are
``<element>:in`` / ``<element>:out`` with the types ``model.element_port_types`` derives from the replaced edges; nothing is
random and nothing is written. The result is validated by the engine's ``validate_element`` exactly like a hand-written body.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .. import model as am
from .. import vocab
from .deltas import COMBINED, Compiled, Index, compile_deltas
from .sim import simulate

__all__ = ["TEMPLATE_SCHEMA", "TEMPLATE_PATH", "load_templates", "kinds", "template_for", "build_element",
           "complete_element", "complete_deltas", "profile_kinds_covered", "compile_with_templates", "simulate_with_templates"]

TEMPLATE_SCHEMA = "gemarc-atlas-element-templates/1"
TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "derive" / "templates" / "elements.json"

_cache: dict | None = None


def load_templates() -> dict:
    """The template document as read from disk (cached; treat as read-only, ``template_for`` returns copies)."""
    global _cache
    if _cache is None:
        data = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
        if data.get("schema") != TEMPLATE_SCHEMA or not isinstance(data.get("kinds"), dict):
            raise am.AtlasModelError(am.E_SHAPE, f"{TEMPLATE_PATH.name}: not a {TEMPLATE_SCHEMA} document")
        _cache = data
    return _cache


def kinds() -> tuple[str, ...]:
    """Element kinds that have a template, in file order."""
    return tuple(load_templates()["kinds"])


def template_for(kind: str) -> dict | None:
    """A fresh copy of the template body of ``kind``; ``None`` for a kind without one."""
    body = load_templates()["kinds"].get(kind) if isinstance(kind, str) else None
    return copy.deepcopy(body) if body is not None else None


def build_element(kind: str, element_id: str, *, input_type: str = "Any", output_type: str = "Any",
                  overrides: Mapping | None = None, profile=None) -> dict:
    """A complete element body: the template of ``kind``, then ``overrides``, then the derived ports.

    ``input_type`` / ``output_type`` come from ``model.element_port_types`` (``Any`` for a draft that is not connected yet).
    Raises ``AtlasModelError`` for an unknown kind, a bad id or a bad port type. An override may replace any template key
    (including ``role``) but not ``elementId`` or ``kind``.
    """
    if not am.is_authored_id(element_id):
        raise am.AtlasModelError(am.E_ID, f"element id {element_id!r} must match the authored id grammar")
    body = template_for(kind)
    if body is None:
        raise am.AtlasModelError(am.E_ENUM, f"no element template for kind {kind!r}; kinds: {list(kinds())}")
    for key, value in (overrides or {}).items():
        if key not in ("elementId", "kind"):
            body[key] = copy.deepcopy(value)
    inputs, outputs = am.derive_element_ports(element_id, input_type, output_type, profile=profile)
    body.setdefault("inputPorts", inputs)
    body.setdefault("outputPorts", outputs)
    return {"elementId": element_id, "kind": kind, **body}


def complete_element(element: Mapping, *, input_type: str = "Any", output_type: str = "Any", profile=None) -> dict:
    """``element`` with every missing key filled from the template of its ``kind`` (a copy; explicit keys win).

    An element without a usable ``elementId`` or with a kind that has no template is returned unchanged (as a copy): the
    engine then reports it the way it reports any incomplete element.
    """
    out = copy.deepcopy(dict(element))
    kind, element_id = out.get("kind"), out.get("elementId")
    if not am.is_authored_id(element_id) or template_for(kind) is None:
        return out
    full = build_element(kind, element_id, input_type=input_type, output_type=output_type, profile=profile)
    for key, value in full.items():
        out.setdefault(key, value)
    return out


def _rewrite_types(model: Mapping, rewrites: Any) -> tuple[str, str]:
    edges = {e["id"]: e for e in model.get("edges", []) if isinstance(e, Mapping) and "id" in e}
    replaced = [edges[r["replaceEdgeId"]] for r in rewrites if isinstance(r, Mapping) and r.get("disposition", "replace") == "replace"
                and r.get("replaceEdgeId") in edges] if isinstance(rewrites, list) else []
    return am.element_port_types(replaced, model.get("ports", []))


def _complete_atom(model: Mapping, atom: Mapping) -> dict:
    out = copy.deepcopy(dict(atom))
    element = out.get("element")
    if out.get("type") != "insert_element" or not isinstance(element, Mapping):
        return out
    rewrites = out.get("rewrite")
    in_type, out_type = _rewrite_types(model, rewrites)
    out["element"] = complete_element(element, input_type=in_type, output_type=out_type)
    if isinstance(rewrites, list) and am.is_authored_id(element.get("elementId")):
        edges = {e["id"]: e for e in model.get("edges", []) if isinstance(e, Mapping) and "id" in e}
        filled = []
        for index, rw in enumerate(rewrites):
            if (isinstance(rw, Mapping) and "chain" not in rw and rw.get("replaceEdgeId") in edges
                    and rw.get("disposition", "replace") in ("replace", "keep", "disable") and index < 49):
                filled.append(am.build_rewrite_entry(element["elementId"], index, edges[rw["replaceEdgeId"]], rw.get("disposition", "replace")))
            else:
                filled.append(rw)
        out["rewrite"] = filled
    return out


def _complete(model: Mapping, record: Any, depth: int = 0) -> Any:
    if not isinstance(record, Mapping):
        return record
    if record.get("type") == COMBINED and isinstance(record.get("parts"), list) and depth < am.MAX_PART_DEPTH:
        out = copy.deepcopy(dict(record))
        out["parts"] = [_complete(model, part, depth + 1) for part in record["parts"]]
        return out
    return _complete_atom(model, record)


def complete_deltas(model: Mapping, deltas: Iterable | None) -> list:
    """Copies of ``deltas`` in which every ``insert_element`` (also inside combined parts) has a complete element body.

    Ports follow the replaced edges, a ``rewrite`` entry without ``chain`` gets the derived chain, every authored key is
    kept. Anything that is not a recognisable ``insert_element`` is copied unchanged.
    """
    return [_complete(model, d) for d in deltas or []]


def compile_with_templates(ix: Index, deltas: Iterable | None) -> Compiled:
    """``compile_deltas`` after ``complete_deltas``: the template-aware ``insert_element`` path."""
    return compile_deltas(ix, complete_deltas(ix.model, deltas))


def simulate_with_templates(model: Mapping, case: Mapping, deltas=None, opts: Mapping | None = None) -> dict:
    """``simulate`` with template completion of the interventions (``opts`` as for ``simulate``)."""
    return simulate(model, case, complete_deltas(model, deltas), opts)


def profile_kinds_covered(profile=None) -> list[str]:
    """Element kinds of the profile that have no template (an empty list means full coverage)."""
    p = profile or vocab.default_profile()
    return [kind for kind in p.ids("elementKinds") if kind not in load_templates()["kinds"]]
