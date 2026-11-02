"""The expander of the interface family: rows of any number of slices -> a ``gemarc-atlas-ui/1`` model, a provenance map, diagnostics.

``expand_ui(slices, five=None, meta=None)`` maps authored rows to the records of the model, fills the defaults (kind ``page``,
frame ``desktop``, trigger ``tap``, variant kind ``other``) and derives everything the author never writes: the ``default``
variant of every screen, the full ids (``<screen>.<local>``, ``<screen>#<name>``, ``<from>:go:<to>`` with the hash form past 120
characters or the alias), the tree fields of a widget (``parentId order depth childIds``) and the screen lists
(``variantNames widgetIds navOutIds navInIds``, the widget list in pre-order).

Guarantees:

* Never raises on hostile rows or text: problems become diagnostics. A row whose value breaks a role (id grammar, vocabulary, size
  grammar...) is ST-003 and is dropped; a second definition of an id is ST-002 and is dropped.
* Deterministic: the same rows give the same model, provenance map and diagnostics.
* ``five`` (an expanded 5D model, a ``FiveIndex`` or ``None``) is only indexed here: the links to 5D facts are validated by the rules
  (UX-206), and only when it is present. The 5D model is never read otherwise and never written.
* Verdict rows (``conform``, ``diffs``) go to ``UiExpansion.verdict``, never to the model, so ``ui_sha8`` does not depend on them.

Standard library only.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .. import diag as dg
from .. import model as am
from . import model as um
from . import vocab
from .rows import ParsedSlice

#: Rules of the front-end whose row is dropped; the id such a row would have defined is not an id any more (``refused_ids``).
_SYNTAX_RULES = ("ST-001", "ST-003", "ST-004", "ST-005", "ST-006", "ST-007", "DV-001")


# ---------------------------------------------------------------------------------------------------------------
# The 5D index (links are validated only when a 5D model is present)
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class FiveIndex:
    """What the links of the interface can point at, indexed once from an expanded 5D model (about 0.03 ms at reference size)."""

    nodes: dict = field(default_factory=dict)         # node id -> kind
    edges: set = field(default_factory=set)           # edge ids (canonical id, or the alias when one was given)
    requirements: set = field(default_factory=set)
    cases: dict = field(default_factory=dict)         # case id -> ruleIds of its events
    machines: dict = field(default_factory=dict)      # machine id -> state names

    @property
    def empty(self) -> bool:
        """A project with no 5D fact at all (an interface-first project): links are then not validated, only counted."""
        return not (self.nodes or self.edges or self.requirements or self.cases or self.machines)

    @property
    def ids(self) -> set:
        return set(self.nodes) | self.edges | self.requirements | set(self.cases) | set(self.machines)

    @classmethod
    def from_model(cls, model: Mapping) -> "FiveIndex":
        def records(name):
            return [r for r in (model.get(name) or ()) if isinstance(r, Mapping)]

        nodes = {n["id"]: n.get("kind") for n in records("nodes") if isinstance(n.get("id"), str)}
        edges = {e["id"] for e in records("edges") if isinstance(e.get("id"), str)}
        reqs = {r["id"] for r in records("requirements") if isinstance(r.get("id"), str)}
        cases = {c["caseId"]: {e["ruleId"] for e in (c.get("events") or ()) if isinstance(e, Mapping) and isinstance(e.get("ruleId"), str)}
                 for c in records("cases") if isinstance(c.get("caseId"), str)}
        machines = {m["id"]: set(m.get("states") or ()) for m in records("machines") if isinstance(m.get("id"), str)}
        return cls(nodes, edges, reqs, cases, machines)


def five_index(five: "Mapping | FiveIndex | None") -> "FiveIndex | None":
    """``five`` as an index: ``None`` stays ``None`` (no 5D: nothing is validated), a mapping is indexed, an index is returned as is."""
    if five is None or isinstance(five, FiveIndex):
        return five
    return FiveIndex.from_model(five)


# ---------------------------------------------------------------------------------------------------------------
# The expansion
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class UiExpansion:
    """Result of ``expand_ui``: the model, ``model path -> file:line`` and every structural diagnostic."""

    model: dict
    provenance: dict
    diagnostics: list
    finals: list = field(default_factory=list)
    pendings: list = field(default_factory=list)
    verdict: dict = field(default_factory=lambda: {"conform": [], "diffs": []})
    verdict_at: dict = field(default_factory=lambda: {"conform": [], "diffs": []})
    five: "FiveIndex | None" = None
    #: ``(slice name, size)`` in the order given.
    slices: list = field(default_factory=list)

    @property
    def structural_errors(self) -> list:
        return [d for d in self.diagnostics if d.severity == dg.ERROR]


def _hint_sid() -> str:
    return "a screen id: letter, then letters, digits, '_' or '-' (no '.', ':' or '#'; at most 40)"


def _role_problem(role: str, value: Any):
    """``None`` when ``value`` fits ``role``, else ``(got, expected, hint)``."""
    if role in ("free", "ids") and not isinstance(value, list):  # free text and scalar values need no role check
        return None
    if role == "sid":
        return None if isinstance(value, str) and um.SID_RE.fullmatch(value) else (value, _hint_sid(), "ids are case-sensitive")
    if role == "lid":
        return None if isinstance(value, str) and um.LID_RE.fullmatch(value) else (
            value, "a local name: lower-case letter, then lower-case letters, digits, '_' or '-' (at most 32)", "write the name without the screen id")
    if role == "vname":
        if value == vocab.DEFAULT_VARIANT:
            return (value, "a variant name other than 'default'", "the default variant is derived and always exists")
        return None if isinstance(value, str) and um.VNAME_RE.fullmatch(value) else (
            value, "a variant name: lower-case letter, then lower-case letters, digits, '_' (at most 24)", "for example loading, empty, error")
    if role == "vnames":
        for item in value:
            if item != vocab.DEFAULT_VARIANT and not (isinstance(item, str) and um.VNAME_RE.fullmatch(item)):
                return (item, "variant names: lower-case letter, then lower-case letters, digits, '_'", "in= lists variant names; default is allowed")
        return None
    if role == "route":
        return None if isinstance(value, str) and um.ROUTE_RE.fullmatch(value) else (value, "a route starting with '/'", "for example /login or /book/{id}")
    if role == "size":
        return None if isinstance(value, str) and um.SIZE_RE.fullmatch(value) else (
            value, "fill, hug, a pixel count such as 240, or a share such as 2fr", "w and h take one of these forms")
    if role == "field":
        return None if isinstance(value, str) and um.FIELD_RE.fullmatch(value) else (
            value, "a data field name such as email or address.city", "letters, digits, '_' and '.', starting with a letter")
    if role == "uref":
        if isinstance(value, str):
            screen, dot, local = value.partition(".")
            if um.SID_RE.fullmatch(screen) and (not dot or um.LID_RE.fullmatch(local)):
                return None
        return (value, "a screen id or <screen>.<widget>", "for example SCR-LOGIN or SCR-LOGIN.submit")
    if role == "vref":
        if isinstance(value, str):
            screen, hash_, name = value.partition("#")
            if um.SID_RE.fullmatch(screen) and (not hash_ or name == vocab.DEFAULT_VARIANT or um.VNAME_RE.fullmatch(name)):
                return None
        return (value, "a screen id or <screen>#<variant>", "for example SCR-HOME or SCR-HOME#empty")
    if role == "aid":
        return None if am.is_authored_id(value) else (value, "an ASCII id without ':' (the id of a 5D node, machine or alias)", "ids are case-sensitive")
    if role == "id":
        return None if am.is_id(value) else (value, "an id as defined in the 5D model", "ids are case-sensitive")
    if role == "state":
        return None if am.is_state_name(value) else (value, "a state name of the 5D machine", "letters, digits and '_', starting with a letter")
    if role == "token":
        return None if isinstance(value, str) and am.TOKEN_RE.fullmatch(value) else (value, "a short token such as fe or web", "letters, digits, '.', '_' or '-'")
    if role == "cols":
        return None if isinstance(value, int) and 1 <= value <= um.MAX_COLS else (value, f"a column count from 1 to {um.MAX_COLS}", "cols applies to a grid")
    if role == "count":
        return None if isinstance(value, int) and 0 <= value <= 100000 else (value, "a whole number from 0 to 100000", "the number of check points looked at")
    if role == "sha8":
        return None if isinstance(value, str) and um.SHA8_RE.fullmatch(value) else (value, "8 lower-case hex digits", "the ui_sha8 of the screen at the time of the check")
    if role == "ref":
        return None if isinstance(value, str) and um.REF_RE.fullmatch(value) else (value, "a check point: <widget id>@<column>, <screen>@route ...", "see the card of the screen")
    if role == "ids":
        for item in value:
            if not am.is_id(item):
                return (item, "ids as defined in the 5D model", "ids are case-sensitive")
        return None
    if role.startswith("enum:"):
        group = vocab.ENUMS[role[5:]]
        return None if value in group else (value, "one of: " + ", ".join(group), "use one of the allowed values")
    return None


def _claim(owners: dict, key: str, at: str, diags: list, path: str, hint: str = "") -> bool:
    if key in owners:
        first_at, first_path = owners[key]
        diags.append(dg.st002(path, at, key, first_at, first_path, hint=hint))
        return False
    owners[key] = (at, path)
    return True


def expand_ui(slices: Sequence[ParsedSlice], five: "Mapping | FiveIndex | None" = None, meta: "Mapping | None" = None) -> UiExpansion:
    """Rows -> the model, the provenance map and the structural diagnostics. See the module doc."""
    diags: list = [d for s in slices for d in s.diags]
    by_section: dict = {name: [] for name in um.COLLECTIONS + ("conform", "diffs")}
    for s in slices:
        for row in s.rows:
            by_section[row.section].append(row)
    from . import rows as rw

    live: dict = {name: [] for name in by_section}
    for name, rows in by_section.items():
        spec = rw.SECTIONS[name]
        for r in rows:
            ok = True
            for f in spec.columns + spec.options:
                if f.name in r.values:
                    problem = _role_problem(f.role, r.values[f.name])
                    if problem:
                        diags.append(dg.st003(f"{name}.{f.name}", r.at, problem[0], problem[1], problem[2]))
                        ok = False
            if ok:
                live[name].append(r)

    model = um.empty_model(meta)
    owners: dict = {}
    prov: dict = {}
    for r in live["screens"]:
        v = r.values
        if not _claim(owners, v["id"], r.at, diags, "screens.id"):
            continue
        rec = {"id": v["id"], "title": v["title"], "purpose": v["purpose"], "kind": v.get("kind", vocab.DEFAULT_SCREEN_KIND),
               "frame": v.get("frame", vocab.DEFAULT_FRAME), "start": bool(v.get("start", False)), "reqIds": list(v.get("req", [])),
               "variantNames": [vocab.DEFAULT_VARIANT], "widgetIds": [], "navOutIds": [], "navInIds": []}
        for src, dst in (("route", "route"), ("actor", "actorId"), ("group", "group"), ("align", "align")):
            if src in v:
                rec[dst] = v[src]
        model["screens"].append(rec)
        prov[f"screens[{v['id']}]"] = r.at
    screens = {s["id"]: s for s in model["screens"]}

    for r in live["variants"]:
        v = r.values
        vid = um.variant_id(v["screen"], v["name"])
        if not _claim(owners, vid, r.at, diags, "variants.name"):
            continue
        rec = {"id": vid, "screenId": v["screen"], "name": v["name"], "note": v["note"], "kind": v.get("kind", vocab.DEFAULT_VARIANT_KIND)}
        for src, dst in (("machine", "machineId"), ("state", "state"), ("text", "text")):
            if src in v:
                rec[dst] = v[src]
        model["variants"].append(rec)
        prov[f"variants[{vid}]"] = r.at
        if v["screen"] in screens:
            screens[v["screen"]]["variantNames"].append(v["name"])

    widgets: dict = {}
    for r in live["widgets"]:
        v = r.values
        wid = um.widget_id(v["screen"], v["local"])
        if not _claim(owners, wid, r.at, diags, "widgets.local"):
            continue
        rec = {"id": wid, "screenId": v["screen"], "local": v["local"], "label": v["label"], "kind": v["kind"],
               "parentId": um.widget_id(v["screen"], v["parent"]) if "parent" in v else None,
               "inVariants": list(v["in"]) if "in" in v else None, "reqIds": list(v.get("req", []))}
        for key in ("w", "h", "cols", "bind", "emph", "data", "align"):
            if key in v:
                rec[key] = v[key]
        if "api" in v:
            rec["apiId"] = v["api"]
        widgets[wid] = rec
        model["widgets"].append(rec)
        prov[f"widgets[{wid}]"] = r.at

    # derived tree fields: order among siblings, children, depth; widgetIds in pre-order (an iterative walk, no recursion limit)
    children: dict = {}
    for w in model["widgets"]:
        children.setdefault((w["screenId"], w["parentId"]), []).append(w["id"])
    for w in model["widgets"]:
        w["order"] = children[(w["screenId"], w["parentId"])].index(w["id"])
        w["childIds"] = list(children.get((w["screenId"], w["id"]), []))
        depth, cur, seen = 0, w, {w["id"]}
        while cur["parentId"] in widgets and cur["parentId"] not in seen:
            cur = widgets[cur["parentId"]]
            seen.add(cur["id"])
            depth += 1
        w["depth"] = depth
    for s in model["screens"]:
        order: list = []
        stack = list(reversed(children.get((s["id"], None), [])))
        while stack:
            cur_id = stack.pop()
            order.append(cur_id)
            stack.extend(reversed(children.get((s["id"], cur_id), [])))
        s["widgetIds"] = order

    for r in live["nav"]:
        v = r.values
        key = um.nav_id(v["from"], v["to"], v.get("alias"))
        hint = "a second nav row with the same from and to needs alias=<id>" if "alias" not in v else ""
        if not _claim(owners, key, r.at, diags, "nav.alias" if "alias" in v else "nav.to", hint):
            continue
        from_screen, to_screen = v["from"].split(".", 1)[0], v["to"].split("#", 1)[0]
        rec = {"id": key, "fromId": v["from"], "fromScreenId": from_screen, "toId": v["to"], "toScreenId": to_screen,
               "label": v["label"], "on": v.get("on", vocab.DEFAULT_TRIGGER), "reqIds": list(v.get("req", []))}
        for src, dst in (("guard", "guard"), ("api", "apiId"), ("case", "caseId"), ("rule", "ruleId")):
            if src in v:
                rec[dst] = v[src]
        model["nav"].append(rec)
        prov[f"nav[{key}]"] = r.at
        if from_screen in screens:
            screens[from_screen]["navOutIds"].append(key)
        if to_screen in screens:
            screens[to_screen]["navInIds"].append(key)

    verdict: dict = {"conform": [], "diffs": []}
    verdict_at: dict = {"conform": [], "diffs": []}
    for name in ("conform", "diffs"):
        for r in live[name]:
            verdict[name].append({k: v for k, v in sorted(r.values.items())})
            verdict_at[name].append(r.at)

    finals: list = []
    pendings: list = []
    for s in slices:
        finals.extend(x for x in s.finals if x not in finals)
        pendings.extend(x for x in s.pendings if x not in pendings)
    return UiExpansion(model, prov, diags, finals, pendings, verdict, verdict_at, five_index(five), [(s.name, s.size) for s in slices])


# ---------------------------------------------------------------------------------------------------------------
# Refused rows: ids that a syntax error took out of the model (their dangling references are consequences, not causes)
# ---------------------------------------------------------------------------------------------------------------

_SCREEN_OPT = re.compile(r"(?:^|[ \t|])screen=(\"[^\"]*\"|\S+)")


def _unquote(cell: str) -> str:
    cell = cell.strip()
    if len(cell) >= 2 and cell[0] == cell[-1] == '"':
        try:
            return str(json.loads(cell))
        except ValueError:
            return cell[1:-1]
    return cell


def refused_ids(texts: Mapping[str, str], diags: Iterable) -> dict:
    """``{"screen": ids, "widget": ids, "variant": ids}`` the refused rows would have defined, read back from the text.

    A row that carries a syntax diagnostic is dropped by the front-end, so a reference to the id it would have defined dangles.
    The id is recovered from the first cells of the line (a widget also needs its screen: the ``screen=`` option of the row or the
    ``@set screen=`` in force). The result is only consulted for references that are unresolved anyway.
    """
    out: dict = {"screen": set(), "widget": set(), "variant": set()}
    tables: dict = {}
    for d in diags:
        if d.severity != dg.ERROR or d.rule not in _SYNTAX_RULES:
            continue
        name, _, number = d.at.rpartition(":")
        if name not in texts or not number.isdigit() or int(number) < 1:
            continue
        if name not in tables:
            current, default, table = "", "", []
            for text in texts[name].replace("\r\n", "\n").split("\n"):
                head = text.strip()
                if head.startswith("## "):
                    current, default = head[3:].split(":")[0].strip(), ""
                elif head.startswith("@set") and current == "widgets":
                    m = _SCREEN_OPT.search(head[4:])
                    if m:
                        default = _unquote(m.group(1))
                table.append((current, default, head))
            tables[name] = table
        index = int(number) - 1
        if index >= len(tables[name]):
            continue
        section, default, line = tables[name][index]
        if not line or line.startswith(("#", "@", "//")):
            continue
        cells = [_unquote(c.replace("\\|", "|")) for c in re.split(r"(?<!\\)\|", line)]
        first = cells[0] if cells else ""
        if not first or " " in first:
            continue
        if section == "screens":
            out["screen"].add(first)
        elif section == "variants" and len(cells) > 1 and cells[1] and " " not in cells[1]:
            out["variant"].add(um.variant_id(first, cells[1]))
        elif section == "widgets":
            m = _SCREEN_OPT.search(line)
            screen = _unquote(m.group(1)) if m else default
            if screen:
                out["widget"].add(um.widget_id(screen, first))
    return out
