"""The interface model ``gemarc-atlas-ui/1``: id grammar, id builders, canonical bytes, the two hashes and the check points.

The model is what ``expand.expand_ui`` returns: the facts of the prototype (screens, variants, widgets, nav) with the derived
fields filled in. It never holds a pixel position (the layout is derived by the builder and carries its own
``layoutVersion``), never holds a verdict (``ui-verdict-*`` rows are hashed on their own, ``verdict_sha8``) and never refers to a
5D model except through optional link fields (``actorId``, ``apiId``, ``reqIds``, ``caseId``, ``ruleId``, ``machineId``).

``ui_sha8`` is the first 8 hex digits of the SHA-256 of the compact canonical bytes of the model. It does not depend on the 5D
model, and the 5D ``model_sha`` does not depend on it. Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Mapping

from . import vocab

SCHEMA = "gemarc-atlas-ui/1"
VERDICT_SCHEMA = "gemarc-atlas-ui-verdict/1"
BUILD_SCHEMA = "gemarc-atlas-ui-build/1"
#: Integer layout version: any change of the box coordinates adds one (the builder lane owns the layout; the model never holds it).
LAYOUT_VERSION = 1
COLLECTIONS = ("screens", "variants", "widgets", "nav")
#: Longest derived nav id before the hash form ``h:<32 hex>`` replaces it.
NAV_ID_LIMIT = 120

# ---------------------------------------------------------------------------------------------------------------
# Id grammar
# ---------------------------------------------------------------------------------------------------------------

#: A screen id: no ``. : # ~`` (``.`` separates a widget, ``#`` a variant, ``:`` is the nav infix, ``~`` the file-name variant mark).
SID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
#: A widget name local to its screen; the full id is ``<screen>.<local>``.
LID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
#: A variant name; ``default`` is derived and never authored.
VNAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
ROUTE_RE = re.compile(r"^/[A-Za-z0-9._~/:{}-]{0,119}$")
FIELD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{0,39}$")
SIZE_RE = re.compile(r"^(?:fill|hug|[1-9][0-9]{0,3}|[1-9][0-9]?fr)$")
SHA8_RE = re.compile(r"^[0-9a-f]{8}$")
#: The ``ref`` of a verdict ``diffs`` row: a screen, a widget, a variant, optionally followed by ``@<column>``.
REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}(?:[.#][A-Za-z0-9_-]{1,32})?(?:@[a-z]{2,8})?$")
MAX_COLS = 12


def widget_id(screen: str, local: str) -> str:
    return f"{screen}.{local}"


def root_id(screen: str) -> str:
    """The id of the root group of a screen (``root`` is a reserved local name)."""
    return f"{screen}.{vocab.ROOT_LOCAL}"


def variant_id(screen: str, name: str) -> str:
    return f"{screen}#{name}"


def split_widget(identifier: str) -> "tuple[str, str] | None":
    """``(screen, local)`` of a widget id, or ``None``. The first ``.`` is always the separator (a screen id has none)."""
    screen, dot, local = identifier.partition(".")
    return (screen, local) if dot and screen and local else None


def split_variant(identifier: str) -> "tuple[str, str] | None":
    screen, hash_, name = identifier.partition("#")
    return (screen, name) if hash_ and screen and name else None


def nav_id(source: str, target: str, alias: "str | None" = None) -> str:
    """The id of a nav row: its alias, else ``<from>:go:<to>``, else (past 120 characters) ``h:`` plus 32 hex digits."""
    if alias:
        return alias
    plain = f"{source}:go:{target}"
    if len(plain) <= NAV_ID_LIMIT:
        return plain
    return "h:" + hashlib.sha256(f"uinav\n{source}\n{target}".encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------------------------------------------
# Canonical bytes and the two hashes
# ---------------------------------------------------------------------------------------------------------------


def canonical_bytes(value: Mapping, indent: "int | None" = None) -> bytes:
    """UTF-8 bytes of the canonical JSON (keys sorted at every depth, ``ensure_ascii=False``, no NaN) plus one LF."""
    if indent is None:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=indent, allow_nan=False)
    return (text + "\n").encode("utf-8")


def ui_sha(model: Mapping) -> str:
    return hashlib.sha256(canonical_bytes(model)).hexdigest()


def ui_sha8(model: Mapping) -> str:
    """The version stamp of the prototype: first 8 hex digits of ``ui_sha``."""
    return ui_sha(model)[:8]


def verdict_sha8(verdict: Mapping) -> str:
    """First 8 hex digits of the SHA-256 of the canonical bytes of ``{"schema", "conform", "diffs"}`` (the rows of every verdict slice)."""
    body = {"schema": VERDICT_SCHEMA, "conform": list(verdict.get("conform", ())), "diffs": list(verdict.get("diffs", ()))}
    return hashlib.sha256(canonical_bytes(body)).hexdigest()[:8]


def screen_hashes(model: Mapping) -> dict:
    """``{screen id: screen_sha8}`` for every screen of the model, computed in one pass over ``checkpoints``.

    ``screen_sha8`` is the stamp of one screen's claims: the first 8 hex digits of the SHA-256 of the canonical bytes of
    ``{"schema", "screen", "points": [[ref, value], ...]}`` over the check points of that screen. A verdict (``conform`` row) quotes it as
    ``sha=``; it moves only when a claim of that screen changes (title, purpose, ``req``, ``data``, sizes do not move it), so a change to
    one screen leaves the verdicts of the others current. It does not depend on ``ui_sha8`` or on the 5D model.
    """
    points: dict = {}
    for c in checkpoints(model):
        points.setdefault(c.screen, []).append([c.ref, c.value])
    out: dict = {}
    for s in model.get("screens", ()):
        body = {"schema": SCHEMA, "screen": s["id"], "points": points.get(s["id"], [])}
        out[s["id"]] = hashlib.sha256(canonical_bytes(body)).hexdigest()[:8]
    return out


def screen_sha8(model: Mapping, screen_id: str) -> str:
    """The ``screen_sha8`` of one screen (``""`` for an id the model does not have); see ``screen_hashes``."""
    return screen_hashes(model).get(screen_id, "")


def empty_model(meta: "Mapping | None" = None) -> dict:
    """A model without records; ``meta`` takes ``projectId``, ``title`` and ``languages`` (the rest of the project config is not UI)."""
    meta = meta or {}
    return {"schema": SCHEMA,
            "meta": {"projectId": str(meta.get("projectId") or "project"), "title": str(meta.get("title") or ""),
                     "languages": list(meta.get("languages") or ["zh-CN"]), "profile": "ui"},
            "screens": [], "variants": [], "widgets": [], "nav": []}


def contract_issues(model: Mapping) -> list:
    """Shape problems of a model (a list of short texts; empty when the model has the shape ``expand_ui`` produces)."""
    issues = []
    if not isinstance(model, Mapping):
        return ["the model is not an object"]
    if model.get("schema") != SCHEMA:
        issues.append(f"schema is {model.get('schema')!r}, expected {SCHEMA!r}")
    expected = ["schema", "meta", *COLLECTIONS]
    if sorted(model) != sorted(expected):
        issues.append(f"keys are {sorted(model)}, expected {sorted(expected)}")
    for name in COLLECTIONS:
        if not isinstance(model.get(name), list):
            issues.append(f"{name} is not a list")
    return issues


# ---------------------------------------------------------------------------------------------------------------
# Check points (design 4.5): the claims a frontend check can be made against
# ---------------------------------------------------------------------------------------------------------------

#: Columns of the card's widget table that are claims (``req``, ``case``, ``rule``, ``data`` are links to 5D, not interface facts).
CHECKPOINT_COLUMNS = ("kind", "label", "parent", "bind", "api", "emph", "in", "goto")


@dataclass(frozen=True)
class Checkpoint:
    """One claim: ``ref`` is ``<widget id>@<column>``, ``<container id>@order``, ``<screen>@route`` or ``<screen>#<name>@state``."""

    ref: str
    screen: str
    value: str


def widget_cells(model: Mapping) -> dict:
    """``{widget id: {column: text}}`` of the non-empty cells of the check-point columns (the cells of the card table)."""
    goto: dict = {}
    for n in model.get("nav", ()):
        if "." in n["fromId"]:
            goto.setdefault(n["fromId"], []).append(n["toId"])
    widgets = {w["id"]: w for w in model.get("widgets", ())}
    out: dict = {}
    for w in widgets.values():
        cells = {"kind": w["kind"], "label": w["label"]}
        if w.get("parentId"):
            cells["parent"] = w["parentId"].split(".", 1)[1]
        if w.get("bind"):
            cells["bind"] = w["bind"]
        if w.get("apiId"):
            cells["api"] = w["apiId"]
        if w.get("emph"):
            cells["emph"] = w["emph"]
        if w.get("inVariants"):
            cells["in"] = ",".join(w["inVariants"])
        if w["id"] in goto:
            cells["goto"] = ",".join(goto[w["id"]])
        out[w["id"]] = cells
    return out


def checkpoints(model: Mapping) -> list:
    """The check points of a model in one deterministic order: per screen (model order) the route, the root order, then per widget
    (pre-order) its cells and, for a container with two or more children, its order, then the authored variants.

    A widget that is not reachable from the root (a cycle, a dangling parent) has no place in the screen and no check points.
    """
    cells = widget_cells(model)
    widgets = {w["id"]: w for w in model.get("widgets", ())}
    variants: dict = {}
    for v in model.get("variants", ()):
        variants.setdefault(v["screenId"], []).append(v)
    out: list = []
    for s in model.get("screens", ()):
        sid = s["id"]
        if s.get("route"):
            out.append(Checkpoint(f"{sid}@route", sid, s["route"]))
        roots = [wid for wid in s["widgetIds"] if widgets[wid].get("parentId") is None]
        if len(roots) >= 2:
            out.append(Checkpoint(f"{root_id(sid)}@order", sid, ",".join(widgets[w]["local"] for w in roots)))
        for wid in s["widgetIds"]:
            w = widgets[wid]
            row = cells[wid]
            for column in CHECKPOINT_COLUMNS:
                if column in row:
                    out.append(Checkpoint(f"{wid}@{column}", sid, row[column]))
            if len(w["childIds"]) >= 2:
                out.append(Checkpoint(f"{wid}@order", sid, ",".join(widgets[c]["local"] for c in w["childIds"])))
        for v in variants.get(sid, ()):
            out.append(Checkpoint(f"{v['id']}@state", sid, v["kind"]))
    return out
