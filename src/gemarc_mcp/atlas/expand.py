"""The expander: rows from any number of slices -> an expanded ``gemarc-atlas/1`` model, a provenance map, diagnostics.

Every derivation (ports, edge fields, evidence kind and level, default verification, machine states, transition ids,
default policy, linked tests, requirement edges, rewrite chains, ``owns`` edges) is a call into ``atlas/model.py``; this
module never computes one itself. It only maps authored options to the arguments of the ``build_*`` helpers, resolves
references written as ``src:rel:tgt`` or an alias to canonical edge ids, and reports structure problems (ST-00x).

Guarantees:

* Never raises on hostile rows or hostile text: problems become diagnostics.
* A row with a syntax problem was already dropped by the front-end. A row whose *value* is outside its vocabulary
  (ST-003) is **kept verbatim** where the builders allow it, so the model shows what was written; an id that breaks the
  id grammar cannot be built and is dropped.
* Deterministic: the same rows give the same model, provenance map and diagnostics.

The provenance map has keys such as ``nodes[svc.loan]``, ``edges[svc.loan:calls:svc.stock]``, ``cases[CS-1].events[2]``,
``cases[CS-1].expect`` and, for derived or defaulted fields, ``nodes[svc.loan].verification``; values are ``file:line``.
Standard library only.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import diag as dg
from . import model as am
from . import vocab
from .format import atl as atl_fmt
from .format import jsonl as jsonl_fmt
from .format import rows as rw
from .format.rows import ParsedSlice, Row

DEFAULT_META = {"projectId": "project", "title": "", "languages": ["zh-CN"], "profile": vocab.DEFAULT_PROFILE, "repos": []}
#: Fields whose value must satisfy an id grammar for the row to be buildable (the builders raise otherwise).
_FATAL = {("nodes", "id"), ("edges", "from"), ("edges", "to"), ("edges", "alias"), ("machines", "id"), ("machines", "initial"),
          ("transitions", "from"), ("transitions", "to")}


@dataclass
class Expansion:
    """Result of ``expand_slices``: the model, ``model path -> file:line`` and every structural diagnostic."""

    model: dict
    provenance: dict
    diagnostics: list
    finals: list = field(default_factory=list)
    pendings: list = field(default_factory=list)
    mode: str = "asis"
    #: Unresolved references (RF-001..RF-005), filled only by ``expand_slices(..., closed_world=True)``.
    reference_diagnostics: list = field(default_factory=list)

    @property
    def all_diagnostics(self) -> list:
        return list(self.diagnostics) + list(self.reference_diagnostics)

    @property
    def errors(self) -> list:
        return [d for d in self.all_diagnostics if d.severity == dg.ERROR]

    @property
    def ok(self) -> bool:
        return not self.errors


def parse_source(name: str, data: "str | bytes") -> ParsedSlice:
    """Parse one slice by its file extension: ``.atl`` or ``.jsonl`` (anything else is ST-008)."""
    lowered = name.lower()
    if lowered.endswith(".jsonl"):
        return jsonl_fmt.parse_jsonl(data, name) if isinstance(data, str) else jsonl_fmt.parse_jsonl_bytes(data, name)
    if lowered.endswith(".atl"):
        return atl_fmt.parse_atl(data, name) if isinstance(data, str) else atl_fmt.parse_atl_bytes(data, name)
    out = ParsedSlice(name=name, fmt="unknown")
    out.diags.append(dg.st008("file", f"{name}:0", name, "a .atl or .jsonl file", "slices are .atl text or .jsonl text"))
    return out


def expand_sources(sources: "Mapping[str, str | bytes] | Iterable[tuple[str, str | bytes]]", meta: Mapping | None = None,
                   **options) -> Expansion:
    """Parse and expand slices. A mapping is processed in file-name order; a sequence of pairs keeps its order."""
    pairs = sorted(sources.items()) if isinstance(sources, Mapping) else list(sources)
    return expand_slices([parse_source(name, data) for name, data in pairs], meta, **options)


def expand_slices(slices: Sequence[ParsedSlice], meta: Mapping | None = None, *, stamps: Mapping[str, Sequence[int]] | None = None,
                  element_templates: "Mapping[str, Mapping] | Callable[[str], Mapping | None] | None" = None,
                  profile: "vocab.Profile | None" = None, closed_world: bool = False) -> Expansion:
    """Expand parsed slices into one model.

    ``meta`` is what ``init`` knows (``projectId``, ``title``, ``languages``, ``profile``, ``repos``, optionally ``mode``);
    the ``#atlas 1 mode=...`` header of the slices wins over ``meta['mode']``. ``stamps`` maps evidence ids to the
    ``[from, to]`` line range stamped by the evidence verifier. ``element_templates`` supplies the body of an inserted
    element for ``use=<kind>`` (the engine lane's templates); without it an element is just ``{elementId, kind, ports}``.

    ``closed_world=False`` (slice mode: a reference may point at a slice that is not written yet) reports rows that cannot be
    placed (an event of an unknown case, a transition of an unknown machine ...) as WARN and leaves every other reference
    alone. ``closed_world=True`` (full mode: every id must exist) additionally fills ``reference_diagnostics`` (RF-001..RF-005,
    ERROR, one per unresolved reference, ``at`` = the row that holds it) and raises the unplaceable-row diagnostics to ERROR.
    """
    return _Expander(meta, stamps, element_templates, profile, closed_world).run(list(slices))


# ---------------------------------------------------------------------------------------------------------------


#: Per (rule, severity), at most this many diagnostics are kept; the rest is summed up in one INFO line (a hostile or broken
#: input can otherwise produce a diagnostic for every line). Keeping the first ones never hides that a rule fired.
MAX_DIAGS_PER_RULE = 10_000


def cap_diagnostics(diags: list) -> list:
    """The diagnostics with at most ``MAX_DIAGS_PER_RULE`` per (rule, severity) plus one INFO line naming what was dropped."""
    if len(diags) <= MAX_DIAGS_PER_RULE:
        return diags
    counts: dict = {}
    kept, dropped = [], {}
    for d in diags:
        key = (d.rule, d.severity)
        counts[key] = counts.get(key, 0) + 1
        if counts[key] <= MAX_DIAGS_PER_RULE:
            kept.append(d)
        else:
            dropped[key] = dropped.get(key, 0) + 1
    if dropped:
        parts = ", ".join(f"{rule} {sev} x{n}" for (rule, sev), n in sorted(dropped.items()))
        kept.append(dg.diag("ST-008", "diagnostics", "diagnostics:0", sum(dropped.values()), f"at most {MAX_DIAGS_PER_RULE} per rule",
                            f"not listed: {parts}; repair the listed ones first", severity=dg.INFO))
    return kept


def _short(items, limit: int = 8) -> str:
    items = list(items)
    return ", ".join(items[:limit]) + (" ..." if len(items) > limit else "")


class _Expander:
    def __init__(self, meta, stamps, templates, profile, closed_world=False):
        self.closed_world = closed_world
        self.unplaced = dg.ERROR if closed_world else dg.WARN
        base = dict(DEFAULT_META)
        base["languages"] = list(base["languages"])
        base["repos"] = list(base["repos"])
        base.update({k: v for k, v in dict(meta or {}).items()})
        self.meta_in = base
        self.profile = profile or _load_profile(base.get("profile"))
        self.stamps = dict(stamps or {})
        self.templates = templates
        self.diags: list = []
        self.prov: dict[str, str] = {}
        self.owners: dict[str, tuple[str, str]] = {}
        self.mode = "asis"

    # -- small helpers --------------------------------------------------------------------------------------

    def add(self, d: dg.Diag) -> None:
        self.diags.append(d)

    def note(self, path: str, at: str) -> None:
        self.prov[path] = at

    def claim(self, identifier: str, path: str, at: str, hint: str = "") -> bool:
        first = self.owners.get(identifier)
        if first is not None:
            self.add(dg.st002(path, at, identifier, first[1], first[0], hint=hint))
            return False
        self.owners[identifier] = (path, at)
        return True

    def enum_hint(self, value: Any, group: str, ids: Sequence[str]) -> str:
        alias = self.profile.alias(group, value) if group in vocab.LABEL_GROUPS and isinstance(value, str) else None
        if alias:
            return f"write {alias}"
        near = rw.closest(value, ids) if isinstance(value, str) else ""
        return (f"did you mean {near}? " if near else "") + "ids are case-sensitive"

    # -- value checks (roles of the schema) -----------------------------------------------------------------

    def check_row(self, row: Row) -> bool:
        """ST-003 checks of one row; returns ``False`` when the row cannot be built."""
        spec = rw.SECTIONS[row.section]
        usable = True
        for f in spec.columns + spec.options:
            if f.name not in row.values or f.role == "free":
                continue
            value = row.values[f.name]
            problem = self._role_problem(f, value)
            if problem is None:
                continue
            got, expected, hint = problem
            self.add(dg.st003(f"{row.section}.{f.name}", row.at, got, expected, hint))
            if (row.section, f.name) in _FATAL:
                usable = False
        return usable

    def _role_problem(self, f: rw.Field, value: Any):
        role, p = f.role, self.profile
        if value == "^" and f.type == "edgeev":
            return None
        if f.type in ("ids", "edgeev", "expects", "rewrite", "strs") and isinstance(value, list):
            if role in ("ids", "aids"):
                check = am.is_authored_id if role == "aids" else am.is_id
                for item in value:
                    if not check(item):
                        return (item, "an id: letters, digits, '.', '_', '-' (no spaces)", "ids are written exactly as defined")
            return None
        if role == "id":
            return None if am.is_id(value) else (value, "an id: letters, digits, '.', ':', '_', '-' (no spaces, at most 128 characters)", "write the id exactly as defined")
        if role == "aid":
            return None if am.is_authored_id(value) else (
                value, "an ASCII id without ':' (letters, digits, '.', '_', '-'; at most 124 characters)", "node, machine and alias ids are ASCII and have no ':'")
        if role == "state":
            return None if am.is_state_name(value) else (value, "a state name: letter then letters, digits, '_' (at most 40)", "state names have no spaces or '-'")
        if role == "token":
            return None if isinstance(value, str) and am.TOKEN_RE.fullmatch(value) else (value, "a short token: letters, digits, '.', '_', '-' (at most 40)", "no spaces")
        if role == "card":
            return None if am.CARDINALITY_RE.fullmatch(value) else (value, "a cardinality such as N:1 or 1:0..1", "write cardinality as <from>:<to>")
        if role == "scope":
            if not am.SCOPE_RE.fullmatch(value):
                return (value, "all, kind:<Kind>, node:<id> or req:<id>", "write scope=all or scope=kind:Module")
            if value.startswith("kind:") and not p.has("kinds", value[5:]):
                return (value, "kind:<a known kind>", f"known kinds: {_short(p.ids('kinds'))}")
            return None
        if role == "chapter":
            return None if 1 <= value <= 20 else (value, "a chapter number 1..20", "chapter is 1..20, or leave it out")
        if role.startswith("enum:"):
            group = role[5:]
            if not p.has(group, value):
                return (value, f"one of: {_short(p.ids(group))}", self.enum_hint(value, group, p.ids(group)))
            if group == "kinds" and not p.item("kinds", value)["authorable"]:
                return (value, "an authorable kind", f"{value} is scenario-only and cannot be a node")
            return None
        if role.startswith("enumplain:"):
            name = role[10:]
            allowed = p.enums[name]
            if value not in allowed:
                return (value, f"one of: {_short(allowed)}", ("did you mean " + rw.closest(value, allowed) + "? " if rw.closest(value, allowed) else "") + "ids are case-sensitive")
        return None

    # -- the run --------------------------------------------------------------------------------------------

    def run(self, slices: list[ParsedSlice]) -> Expansion:
        for s in slices:
            self.diags.extend(s.diags)
        self._mode(slices)
        rows: dict[str, list[Row]] = {name: [] for name in rw.SECTIONS}
        for s in slices:
            for r in s.rows:
                rows[r.section].append(r)
        order = [r for s in slices for r in s.rows]
        usable = {id(r): self.check_row(r) for r in order}
        dropped = self._claim_pass(order, usable)
        live = {name: [r for r in rs if usable[id(r)] and id(r) not in dropped] for name, rs in rows.items()}

        model = am.empty_model(self.meta_in["projectId"], self.meta_in["title"], self.mode, self.meta_in["languages"],
                               self.meta_in["profile"], self.meta_in["repos"])
        profile = self.profile
        self._space(live, model)
        ev_index = self._evidences(live, model)
        machine_rows = self._machine_rows(live)
        machine_ids = set(machine_rows)
        node_by_id = self._nodes(live, model, ev_index, machine_ids)
        model["ports"] = am.derive_ports(model["nodes"], machine_ids, profile.record_kinds, profile=profile)
        for node in model["nodes"]:
            at = self.prov.get(f"nodes[{node['id']}]", "")
            if at:
                for port in node["portIds"]:
                    self.note(f"ports[{port}]", at)
        self._claim_ports(model)
        self._edges(live, model, ev_index, node_by_id, machine_ids)
        machine_records = self._machines(live, model, machine_rows, ev_index, node_by_id)
        edge_index = am.edge_ref_index(model["edges"])
        self._tests_tbds(live, model)
        self._requirements(live, model, edge_index)
        self._cases(live, model, machine_records, node_by_id, edge_index)
        self._interventions(live, model, edge_index)
        self._policies(live, model)
        self._stamps(model)
        refs = reference_diagnostics(model, self.prov) if self.closed_world else []
        self.diags = cap_diagnostics(self.diags)
        refs = cap_diagnostics(refs)
        return Expansion(model, dict(self.prov), self.diags, [f for s in slices for f in s.finals],
                         [p for s in slices for p in s.pendings], self.mode, refs)

    def _mode(self, slices: list[ParsedSlice]) -> None:
        modes = [(s.mode, s.name) for s in slices if s.mode]
        meta_mode = self.meta_in.get("mode")
        mode = modes[0][0] if modes else (meta_mode or "asis")
        for other, name in modes[1:]:
            if other != mode:
                self.add(dg.st007("header", f"{name}:1", other, f"mode={mode} (the first header wins)", "all slices of a project use the same mode"))
        # One slice header must not flip the project mode silently: the mode changes every derived verification.
        if meta_mode and isinstance(meta_mode, str):
            for other, name in modes:
                if other != meta_mode:
                    self.add(dg.st007("header", f"{name}:1", other, f"mode={meta_mode} (the project mode)",
                                      f"the project was initialised with mode={meta_mode}; change the header back, or start a new project for the other mode"))
        if mode not in self.profile.enums["modes"]:
            at = f"{modes[0][1]}:1" if modes else "meta:0"
            self.add(dg.st003("header.mode", at, mode, f"one of: {_short(self.profile.enums['modes'])}", "write mode=asis or mode=tobe"))
        self.mode = mode

    # -- ids ------------------------------------------------------------------------------------------------

    def _claim_pass(self, order: list[Row], usable: Mapping[int, bool]) -> set[int]:
        """Global id uniqueness in file order (ST-002); returns the ids of the duplicate rows (which are dropped)."""
        dropped: set[int] = set()
        seen_machines: dict[str, str] = {}
        seen_expect: dict[str, str] = {}
        for r in order:
            if not usable[id(r)]:
                continue
            v = r.values
            if r.section in ("buildings", "floors", "rooms"):
                key, path = v["id"], f"space.{r.section}[{v['id']}]"
            elif r.section == "edges":
                try:
                    key = v["alias"] if "alias" in v else am.edge_id(v["from"], v["rel"], v["to"])
                except am.AtlasModelError:
                    continue
                path = f"edges[{key}]"
            elif r.section in ("nodes", "evidences", "cases", "interventions", "requirements", "tests", "tbds", "policies"):
                key, path = v["id"], f"{r.section}[{v['id']}]"
            elif r.section == "machines":
                first = seen_machines.get(v["id"])
                if first is not None:
                    self.add(dg.st002(f"machines[{v['id']}]", r.at, v["id"], first, "machines[" + v["id"] + "]"))
                    dropped.add(id(r))
                else:
                    seen_machines[v["id"]] = r.at
                continue
            elif r.section == "expect":
                first = seen_expect.get(v["case"])
                if first is not None:
                    self.add(dg.st002(f"expect[{v['case']}]", r.at, v["case"], first, "expect[" + v["case"] + "]",
                                      hint="one expect row per case"))
                    dropped.add(id(r))
                else:
                    seen_expect[v["case"]] = r.at
                continue
            else:
                continue
            hint = "(a repeated src/rel/tgt needs alias=<id> on the second edge)" if r.section == "edges" and "alias" not in v else ""
            if not self.claim(key, path, r.at, hint):
                dropped.add(id(r))
        return dropped

    def _claim_ports(self, model: dict) -> None:
        for port in model["ports"]:
            first = self.owners.get(port["id"])
            at = self.prov.get(f"nodes[{port['nodeId']}]", "")
            if first is not None:
                self.add(dg.st002(f"ports[{port['id']}]", at, port["id"], first[1], first[0], hint="(a derived port id equals an authored id)"))
            else:
                self.owners[port["id"]] = (f"ports[{port['id']}]", at)

    # -- space ----------------------------------------------------------------------------------------------

    def _space(self, live, model) -> None:
        space = model["space"]
        for r in live["buildings"]:
            v = r.values
            space["buildings"].append(am.build_building(v["id"], v["name"], v.get("trust"), profile=self.profile))
            self.note(f"space.buildings[{v['id']}]", r.at)
            if "trust" not in v:
                self.note(f"space.buildings[{v['id']}].trust", r.at)
        for r in live["floors"]:
            v = r.values
            space["floors"].append(am.build_floor(v["id"], v["building"], v["name"], v["level"]))
            self.note(f"space.floors[{v['id']}]", r.at)
        for r in live["rooms"]:
            v = r.values
            space["rooms"].append(am.build_room(v["id"], v["floor"], v["name"], v.get("layer"), v.get("tier"), v.get("area")))
            self.note(f"space.rooms[{v['id']}]", r.at)

    # -- evidences ------------------------------------------------------------------------------------------

    def _evidences(self, live, model) -> dict:
        index: dict[str, dict] = {}
        for r in live["evidences"]:
            v = r.values
            kind = am.evidence_kind(v["file"], profile=self.profile)
            repo = v.get("repo", "")
            if kind == "user-requirement":
                if "repo" in r.explicit:
                    self.add(dg.dv001("evidences.repo", r.at, "repo", "derived; a user: evidence has no repository, delete repo="))
                repo = ""
            ev = am.build_evidence(v["id"], v["file"], v["anchor"], v["topic"], repo, mode=self.mode, profile=self.profile)
            model["evidences"].append(ev)
            index[v["id"]] = ev
            self.note(f"evidences[{v['id']}]", r.at)
            self.note(f"evidences[{v['id']}].sourceKind", r.at)
            self.note(f"evidences[{v['id']}].verification", r.at)
            self.note(f"evidences[{v['id']}].lineRange", r.at)
        return index

    def _stamps(self, model: dict) -> None:
        for ev in model["evidences"]:
            stamp = self.stamps.get(ev["evidenceId"])
            if stamp is None:
                continue
            ok = (isinstance(stamp, (list, tuple)) and len(stamp) == 2 and all(isinstance(x, int) and not isinstance(x, bool) and x >= 1 for x in stamp)
                  and stamp[0] <= stamp[1])
            if ok:
                ev["lineRange"] = [int(stamp[0]), int(stamp[1])]
            else:
                self.add(dg.st003(f"evidences[{ev['evidenceId']}].lineRange", self.prov.get(f"evidences[{ev['evidenceId']}]", "stamp:0"), stamp,
                                  "[from, to] with 1 <= from <= to", "the verifier stamps a line range"))

    # -- nodes ----------------------------------------------------------------------------------------------

    def _machine_rows(self, live) -> dict[str, Row]:
        return {r.values["id"]: r for r in live["machines"]}

    def _nodes(self, live, model, ev_index, machine_ids) -> dict:
        room_by_id = {r["id"]: r for r in model["space"]["rooms"]}
        floor_by_id = {f["id"]: f for f in model["space"]["floors"]}
        building_by_id = {b["id"]: b for b in model["space"]["buildings"]}
        out: dict[str, dict] = {}
        for r in live["nodes"]:
            v = r.values
            room = room_by_id.get(v["room"])
            floor = floor_by_id.get(room["floorId"]) if room else None
            building = building_by_id.get(floor["buildingId"]) if floor else None
            ev_ids = list(v.get("ev", []))
            try:
                node = am.build_node(v["id"], v["kind"], v["name"], v["room"], v["sub"], ev_ids, v.get("verif"), v.get("props"),
                                     v.get("group"), v.get("level"), evidence_kinds=am.evidence_kinds_of(ev_ids, ev_index),
                                     is_machine=v["id"] in machine_ids, mode=self.mode, room=room, building=building, profile=self.profile)
            except am.AtlasModelError as ex:
                self.add(dg.st003(f"nodes.id", r.at, v["id"], "a valid node id", ex.message))
                continue
            model["nodes"].append(node)
            out[node["id"]] = node
            pid = f"nodes[{node['id']}]"
            self.note(pid, r.at)
            self.note(pid + ".portIds", r.at)
            if "verif" not in v:
                self.note(pid + ".verification", r.at)
            for key in ("tier", "area", "trust"):
                if key in node["props"] and key not in (v.get("props") or {}):
                    self.note(f"{pid}.props.{key}", r.at)
        return out

    # -- edges ----------------------------------------------------------------------------------------------

    def _build_edge(self, r: Row, kinds_of, node_by_id, machine_ids) -> dict | None:
        v = r.values
        raw_ev = v.get("ev", [])
        inherited = raw_ev == "^"
        if inherited:
            src, tgt = node_by_id.get(v["from"]), node_by_id.get(v["to"])
            ev_ids = am.inherit_edge_evidence((src or {}).get("evidenceIds", []), (tgt or {}).get("evidenceIds", []))
        else:
            ev_ids = list(raw_ev)
        kwargs = dict(evidence_kinds=kinds_of(ev_ids), inherited=inherited, machine_ids=machine_ids, mode=self.mode, profile=self.profile)
        args = (v["from"], v["rel"], v["to"], v["label"], ev_ids, v.get("verif"), v.get("card"), v.get("group"), v.get("alias"))
        try:
            return am.build_edge(*args, **kwargs)
        except am.AtlasModelError as ex:
            if ex.code != am.E_ENUM:
                self.add(dg.st003("edges.from", r.at, f"{v['from']} {v['rel']} {v['to']}", "ASCII node ids without ':' and a relation without ':'", ex.message))
                return None
        # unknown relation (already reported as ST-003): keep it verbatim; ``static`` is not derivable, use the generic default
        try:
            edge = am.build_edge(*args[:1], "calls", *args[2:], **kwargs)
            edge["relation"] = v["rel"]
            edge["static"] = False
            edge["id"] = v.get("alias") or am.edge_id(v["from"], v["rel"], v["to"])
            return edge
        except am.AtlasModelError as ex:
            self.add(dg.st003("edges.rel", r.at, v["rel"], "a relation id without ':'", ex.message))
            return None

    def _edges(self, live, model, ev_index, node_by_id, machine_ids) -> None:
        def kinds_of(ids):
            return am.evidence_kinds_of(ids, ev_index)

        for r in live["edges"]:
            edge = self._build_edge(r, kinds_of, node_by_id, machine_ids)
            if edge is None:
                continue
            model["edges"].append(edge)
            pid = f"edges[{edge['id']}]"
            self.note(pid, r.at)
            self.note(pid + ".sourcePortId", r.at)
            self.note(pid + ".targetPortId", r.at)
            if "verif" not in r.values:
                self.note(pid + ".verification", r.at)
            if r.values.get("ev") == "^":
                self.note(pid + ".evidenceIds", r.at)

    # -- machines -------------------------------------------------------------------------------------------

    def _rejected(self, prefix: str) -> set:
        """Ids named by diagnostics whose path starts with ``prefix``: rows of that kind that were dropped or refused."""
        return {d.got for d in self.diags if isinstance(d.got, str) and d.path.startswith(prefix)}

    def _machines(self, live, model, machine_rows, ev_index, node_by_id) -> dict:
        transitions: dict[str, list[Row]] = {mid: [] for mid in machine_rows}
        rejected_machines = self._rejected("machines")
        for r in live["transitions"]:
            mid = r.values["machine"]
            if mid in transitions:
                transitions[mid].append(r)
            else:
                self.add(dg.diag("SM-001", "transitions.machine", r.at, mid, "a machine defined in a machines row",
                                 "define the machine (machines section) or fix machine=", severity=self.unplaced,
                                 derived=mid in rejected_machines))
        records: dict[str, dict] = {}
        owns: list[tuple[Row, dict]] = []
        for mid, mrow in machine_rows.items():
            v = mrow.values
            trs, rows = [], []
            seen: dict[str, str] = {}
            for tr in transitions[mid]:
                t = {"from": tr.values["from"], "to": tr.values["to"]}
                if "id" in tr.values:
                    t["id"] = tr.values["id"]
                if "guard" in tr.values:
                    t["guard"] = tr.values["guard"]
                if "err" in tr.values:
                    t["errorCode"] = tr.values["err"]
                tid = t.get("id") or am.transition_id(mid, t["from"], t["to"])
                if tid in seen:
                    self.add(dg.st002(f"machines[{mid}].transitions[{tid}]", tr.at, tid, seen[tid], f"machines[{mid}].transitions[{tid}]",
                                      hint="(give one of the transitions an explicit id=)"))
                    continue
                seen[tid] = tr.at
                trs.append(t)
                rows.append(tr)
            try:
                record = am.build_machine(mid, v["initial"], trs, v.get("err"), v.get("ev", []))
            except am.AtlasModelError as ex:
                self.add(dg.st003("machines.id", mrow.at, mid, "a valid machine", ex.message))
                continue
            model["machines"].append(record)
            records[mid] = record
            pid = f"machines[{mid}]"
            self.note(pid, mrow.at)
            for key in ("states", "terminal", "nodeId"):
                self.note(f"{pid}.{key}", mrow.at)
            for tr, rec in zip(rows, record["transitions"]):
                self.note(f"{pid}.transitions[{rec['id']}]", tr.at)
            if "owner" in v:
                owns.append((mrow, record))
        machine_ids = set(records)
        for mrow, record in owns:
            mid = record["id"]
            node = node_by_id.get(mid)
            if node is None:
                self.add(dg.diag("RF-002", "machines.id", mrow.at, mid, "a node row with the machine's id",
                                 "add the node of this machine to the nodes section (kind=State is usual)", severity=self.unplaced))
                continue
            edge = am.build_owns_edge(mrow.values["owner"], record, node, evidence_kinds=am.evidence_kinds_of(record["evidenceIds"], ev_index),
                                      machine_ids=machine_ids, mode=self.mode, profile=self.profile) if am.is_authored_id(mrow.values["owner"]) else None
            if edge is None:
                continue
            if not self.claim(edge["id"], f"edges[{edge['id']}]", mrow.at):
                continue
            model["edges"].append(edge)
            self.note(f"edges[{edge['id']}]", mrow.at)
        return records

    # -- tests, tbds, requirements, policies ------------------------------------------------------------------

    def _tests_tbds(self, live, model) -> None:
        for r in live["tests"]:
            model["tests"].append(am.build_test(r.values["id"], r.values["title"]))
            self.note(f"tests[{r.values['id']}]", r.at)
        for r in live["tbds"]:
            v = r.values
            model["tbds"].append(am.build_tbd(v["id"], v["title"], v["decision"], v.get("ev", [])))
            self.note(f"tbds[{v['id']}]", r.at)

    def _resolve_edges(self, refs: Iterable[str], edge_index: Mapping[str, str]) -> list[str]:
        return [edge_index.get(x, x) for x in refs]

    def _requirements(self, live, model, edge_index) -> None:
        for r in live["requirements"]:
            v = r.values
            nodes = list(v.get("nodes", []))
            edge_ids = self._resolve_edges(v["edges"], edge_index) if "edges" in v else None
            model["requirements"].append(am.build_requirement(
                v["id"], v["title"], v["source"], nodes, edge_ids, v.get("tests", []), v.get("ev", []), v.get("tbd", []),
                v.get("design", []), edges=model["edges"]))
            self.note(f"requirements[{v['id']}]", r.at)
            if "edges" not in v:
                self.note(f"requirements[{v['id']}].edgeIds", r.at)

    def _policies(self, live, model) -> None:
        for r in live["policies"]:
            v = r.values
            model["policies"].append(am.build_policy(v["id"], v["text"], v["src"], v.get("scope", "all"), v.get("level", "REQ"),
                                                     v.get("chapter"), v.get("kind", "constraint"), v.get("by", "ai")))
            self.note(f"policies[{v['id']}]", r.at)

    # -- cases ------------------------------------------------------------------------------------------------

    def _state_set(self, node_id: str, machine_records: Mapping, node_by_id: Mapping) -> "set[str] | None":
        if node_id in machine_records:
            return set(machine_records[node_id]["states"])
        if node_id in node_by_id:
            return set(self.profile.ids("nodeStates"))
        return None

    def _cases(self, live, model, machine_records, node_by_id, edge_index) -> None:
        by_case: dict[str, list[Row]] = {}
        case_rows = {r.values["id"]: r for r in live["cases"]}
        rejected_cases = self._rejected("cases")
        for r in live["events"]:
            cid = r.values["case"]
            if cid in case_rows:
                by_case.setdefault(cid, []).append(r)
            else:
                self.add(dg.diag("RF-004", "events.case", r.at, cid, "a case defined in a cases row", "define the case first or fix case=",
                                 severity=self.unplaced, derived=cid in rejected_cases))
        expects: dict[str, Row] = {}
        for r in live["expect"]:
            cid = r.values["case"]
            if cid in case_rows:
                expects[cid] = r
            else:
                self.add(dg.diag("RF-004", "expect.case", r.at, cid, "a case defined in a cases row", "define the case first or fix the first cell",
                                 severity=self.unplaced, derived=cid in rejected_cases))
        for cid, r in case_rows.items():
            v = r.values
            events = []
            for er in by_case.get(cid, []):
                ev = {"type": er.values["type"], "from": er.values["from"], "to": er.values["to"]}
                for key in ("ruleId", "latencyMs", "ack", "group", "section", "when", "params", "fault", "edge", "guard", "dedupKey",
                            "role", "errorCode", "ms"):
                    if key in er.values:
                        ev[key] = copy.deepcopy(er.values[key])
                if "edge" in ev:
                    ev["edge"] = edge_index.get(ev["edge"], ev["edge"])
                self._check_event_json(ev, er)
                events.append(ev)
            policy = self._policy(v.get("policy"), r)
            terminal, outcomes, by = {}, None, None
            exp = expects.get(cid)
            if exp is not None:
                terminal = self._expect_cells(exp, machine_records, node_by_id)
                outcomes = self._outcomes(exp)
                by = exp.values.get("by")
            init = v.get("init")
            if init:
                self._check_init(init, r, machine_records)
            case = am.build_case(cid, v["cat"], v["title"], events, v.get("req", []), v.get("ev", []), policy, v.get("ctx"), init,
                                 terminal, outcomes, by, v.get("tests"), model["requirements"])
            model["cases"].append(case)
            self.note(f"cases[{cid}]", r.at)
            self.note(f"cases[{cid}].policy", r.at)
            if "tests" not in v:
                self.note(f"cases[{cid}].linkedTestIds", r.at)
            for er, e in zip(by_case.get(cid, []), case["events"]):
                self.note(f"cases[{cid}].events[{e['seq']}]", er.at)
            if exp is not None:
                self.note(f"cases[{cid}].expect", exp.at)

    def _check_event_json(self, ev: dict, row: Row) -> None:
        fault = ev.get("fault")
        if isinstance(fault, dict) and "mode" in fault and fault["mode"] not in self.profile.enums["faultModes"]:
            self.add(dg.st003("events.fault", row.at, fault["mode"], f"one of: {_short(self.profile.enums['faultModes'])}", "fault.mode is case-sensitive"))

    def _policy(self, partial, row: Row):
        if partial is None:
            return None
        clean: dict = {}
        for key, value in partial.items():
            if key not in am.POLICY_KEYS:
                self.add(dg.st004("cases.policy", row.at, key, am.POLICY_KEYS, "the case policy has exactly these keys"))
            elif isinstance(value, bool) or not isinstance(value, int) or value < (1 if key == "timeoutMs" else 0):
                self.add(dg.st003("cases.policy", row.at, value, "a whole number (timeoutMs >= 1, others >= 0)", f"policy.{key}"))
                clean[key] = value
            else:
                clean[key] = value
        return clean

    def _expect_cells(self, row: Row, machine_records, node_by_id) -> dict:
        out: dict[str, str] = {}
        for cell in row.values["nodes"]:
            try:
                node, state = am.expect_cell(cell)
            except am.AtlasModelError as ex:
                self.add(dg.st007("expect.nodes", row.at, cell, "node or node=State", ex.message))
                continue
            if node in out:
                self.add(dg.st007("expect.nodes", row.at, node, "each node once per case", "duplicate node in expect"))
                continue
            states = self._state_set(node, machine_records, node_by_id)
            if states is not None and state not in states:
                self.add(dg.st003("expect.nodes", row.at, f"{node}={state}", f"a state of the node: {_short(sorted(states))}", "states are case-sensitive"))
            out[node] = state
        return out

    def _outcomes(self, row: Row):
        raw = row.values.get("outcomes")
        if raw is None:
            return None
        outcomes = []
        for item in raw:
            if not isinstance(item, dict) or not isinstance(item.get("ruleId"), str) or set(item) - {"ruleId", "outcome", "errorCode"}:
                self.add(dg.st007("expect.outcomes", row.at, item, "{ruleId, outcome, [errorCode]}", "each outcome entry is an object with these keys"))
                continue
            if not self.profile.has("eventOutcomes", item.get("outcome")):
                self.add(dg.st003("expect.outcomes", row.at, item.get("outcome"), f"one of: {_short(self.profile.ids('eventOutcomes'))}", "outcomes are case-sensitive"))
            outcomes.append(copy.deepcopy(item))
        return outcomes

    def _check_init(self, init: Mapping, row: Row, machine_records) -> None:
        for node, state in init.items():
            record = machine_records.get(node)
            if record is not None and state not in record["states"]:
                self.add(dg.st003("cases.init", row.at, f"{node}={state}", f"a state of the machine: {_short(record['states'])}", "states are case-sensitive"))

    # -- interventions ----------------------------------------------------------------------------------------

    def _interventions(self, live, model, edge_index) -> None:
        edge_by_id = {e["id"]: e for e in model["edges"]}
        for r in live["interventions"]:
            record = self._intervention(r, model, edge_index, edge_by_id)
            if record is not None:
                model["interventions"].append(record)
                self.note(f"interventions[{r.values['id']}]", r.at)

    def _template(self, kind: str):
        if self.templates is None:
            return None
        found = self.templates(kind) if callable(self.templates) else self.templates.get(kind)
        return copy.deepcopy(dict(found)) if found else None

    def _intervention(self, r: Row, model, edge_index, edge_by_id):
        v, p = r.values, self.profile
        itype = v["type"]
        item = p.item("interventionTypes", itype)
        if item is None:
            return None  # ST-003 already reported for the type; the parameters cannot be mapped
        allowed = set(item["required"]) | set(item["optional"])
        params: dict[str, Any] = {}
        failed = False
        if "target" in v:
            key = "targetNodeId" if "targetNodeId" in allowed else ("targetEdgeId" if "targetEdgeId" in allowed else None)
            if key is None:
                self.add(dg.st004("interventions.target", r.at, "target", f"options valid for {itype}: " + (_short(sorted(allowed)) or "none"),
                                  f"{itype} has no node or edge target; remove target="))
                failed = True
            else:
                params[key] = edge_index.get(v["target"], v["target"]) if key == "targetEdgeId" else v["target"]
        for key, value in (v.get("params") or {}).items():
            if key not in am.ATOMIC_PARAM_KEYS or key not in allowed or key in ("element", "rewrite", "parts"):
                self.add(dg.st004("interventions.params", r.at, key, "parameters of " + itype + ": " + (_short(sorted(allowed - {"element", "rewrite", "parts"})) or "none"),
                                  "put each parameter under its type's name"))
                failed = True
            elif key in params:
                self.add(dg.st007("interventions.params", r.at, key, "each parameter once", "target= and params= both set it"))
                failed = True
            else:
                params[key] = edge_index.get(value, value) if key == "targetEdgeId" and isinstance(value, str) else copy.deepcopy(value)
        if "parts" in v:
            if "parts" in allowed:
                params["parts"] = copy.deepcopy(v["parts"])
            else:
                self.add(dg.st004("interventions.parts", r.at, "parts", "options valid for " + itype, "parts= belongs to combined_intervention"))
                failed = True
        has_element = "element" in v
        if itype == "insert_element":
            if not has_element:
                self.add(dg.st001("interventions.element", r.at, "missing", "element=<id> use=<kind> (or element={json})", "insert_element needs an element"))
                failed = True
            else:
                built = self._element(r, edge_index, edge_by_id, model)
                if built is None:
                    failed = True
                else:
                    params["element"], params["rewrite"] = built
        else:
            for key in ("element", "use", "rewrite"):
                if key in v:
                    self.add(dg.st004(f"interventions.{key}", r.at, key, "options valid for " + itype, f"{key}= belongs to insert_element"))
                    failed = True
        for key in item["required"]:
            if key not in params and not failed:
                self.add(dg.st001(f"interventions.{key}", r.at, "missing", f"{itype} needs {key}",
                                  "add target=<id>" if key in ("targetNodeId", "targetEdgeId") else f"add params={{\"{key}\":...}}"))
                failed = True
        if itype == "delay_message" and not failed and not any(k in params for k in ("targetEdgeId", "targetEventId", "targetRuleId")):
            self.add(dg.st001("interventions.target", r.at, "missing", "target=<edge>, or params with targetEventId or targetRuleId",
                              "delay_message needs something to delay"))
            failed = True
        if failed:
            return None
        expect: dict[str, Any] = {}
        for src, dst in (("class", "outcomeClass"), ("terminal", "terminal"), ("status", "status")):
            if src in v:
                expect[dst] = copy.deepcopy(v[src])
        return am.build_intervention(v["id"], itype, v["description"], v["applies"], expect, v.get("note", ""), v.get("why"), **params)

    def _element(self, r: Row, edge_index, edge_by_id, model):
        v, p = r.values, self.profile
        given = v["element"]
        body = {"elementId": given} if isinstance(given, str) else copy.deepcopy(given)
        element_id = body.get("elementId")
        if not isinstance(element_id, str) or not element_id:
            self.add(dg.st001("interventions.element", r.at, "no elementId", "element={\"elementId\":...} or element=<id>", "an element needs an id"))
            return None
        if not am.is_authored_id(element_id):
            self.add(dg.st003("interventions.element", r.at, element_id, "an ASCII id without ':'", "element ids follow the node id grammar"))
            return None
        kind = v.get("use", body.get("kind"))
        if "use" in v and "kind" in body and body["kind"] != v["use"]:
            self.add(dg.st007("interventions.use", r.at, v["use"], f"the element's own kind {body['kind']}", "use= and element.kind disagree"))
            return None
        if kind is None:
            self.add(dg.st001("interventions.use", r.at, "missing", "use=<element kind>", "a bare element id needs use=<kind>"))
            return None
        template = self._template(kind) if isinstance(kind, str) else None
        if template:
            template.update(body)
            body = template
        body["kind"] = kind
        entries, replaced = [], []
        for index, item in enumerate(v.get("rewrite", [])):
            ref, _, disposition = item.partition("=")
            disposition = disposition or "replace"
            if disposition not in p.enums["dispositions"]:
                self.add(dg.st003("interventions.rewrite", r.at, item, f"edge or edge=<{'|'.join(p.enums['dispositions'])}>", "dispositions are case-sensitive"))
                return None
            edge = edge_by_id.get(edge_index.get(ref, ref))
            if edge is None:
                self.add(dg.diag("RF-002", "interventions.rewrite", r.at, ref, "an edge defined in the edges section", "write the edge id, alias or src:rel:tgt of an existing edge", severity=self.unplaced))
                entries.append({"replaceEdgeId": ref, "disposition": disposition, "chain": []})
                continue
            try:
                entries.append(am.build_rewrite_entry(element_id, index, edge, disposition))
            except am.AtlasModelError as ex:
                self.add(dg.st003("interventions.rewrite", r.at, item, "at most 49 rewrite entries per element", ex.message))
                return None
            if disposition == "replace":
                replaced.append(edge)
        in_type, out_type = am.element_port_types(replaced, model["ports"])
        inputs, outputs = am.derive_element_ports(element_id, in_type, out_type, profile=p)
        body.setdefault("inputPorts", inputs)
        body.setdefault("outputPorts", outputs)
        return body, entries


_REF_PATH = re.compile(r"^\$\.(?:space\.)?(\w+)\[(\d+)\](.*)$")
_EVENT_PATH = re.compile(r"^\.events\[(\d+)\]")
_ID_KEY = {"buildings": "id", "floors": "id", "rooms": "id", "nodes": "id", "edges": "id", "evidences": "evidenceId", "machines": "id",
           "cases": "caseId", "interventions": "interventionId", "requirements": "id", "tests": "id", "tbds": "id", "policies": "id"}
_QUOTED = re.compile(r"'([^']*)'")


def reference_diagnostics(model: Mapping, provenance: Mapping[str, str]) -> list:
    """RF-001..RF-005: one ERROR per unresolved reference of ``model``, located with the provenance map.

    The references checked are the ones of the contract (``model.contract_issues`` code ``040``), so this adapter and the
    contract can never disagree; the rules lane may replace it with its own slice-aware rules. RF-001 spatial chain
    (floor -> building, room -> floor, node -> room), RF-002 edge endpoints, RF-003 evidence references, RF-004 case and
    event references, RF-005 requirement, test, TBD, policy and intervention references.
    """
    out = []
    for issue in am.contract_issues(model):
        if issue["code"] != am.E_REF:
            continue
        path, message = issue["path"], issue["message"]
        m = _REF_PATH.match(path)
        collection, rest, at = (m.group(1), m.group(3), "model:0") if m else ("", "", "model:0")
        if m:
            space = collection in ("buildings", "floors", "rooms")
            records = (model.get("space") or {}).get(collection) if space else model.get(collection)
            try:
                key = ("space." if space else "") + collection + "[" + records[int(m.group(2))][_ID_KEY[collection]] + "]"
                at = provenance.get(key, "model:0")
                event = _EVENT_PATH.match(rest)
                if event and collection == "cases":
                    at = provenance.get(f"{key}.events[{int(event.group(1)) + 1}]", at)
                elif rest.startswith(".expectedTerminalState"):
                    at = provenance.get(f"{key}.expect", at)
            except (TypeError, IndexError, KeyError):
                pass
        if collection in ("floors", "rooms") or (collection == "nodes" and rest.startswith(".roomId")):
            rule = "RF-001"
        elif (collection == "edges" and (".sourceId" in rest or ".targetId" in rest)) or (collection == "machines" and rest == ".id"):
            rule = "RF-002"
        elif ".evidenceIds" in rest:
            rule = "RF-003"
        elif collection == "cases":
            rule = "RF-004"
        else:
            rule = "RF-005"
        quoted = _QUOTED.search(message)
        out.append(dg.diag(rule, path, at, quoted.group(1) if quoted else path, "an id defined in the project",
                           message[:140] + "; define it first or fix the reference"))
    return out


def _load_profile(name) -> "vocab.Profile":
    try:
        return vocab.load_profile(name if isinstance(name, str) else vocab.DEFAULT_PROFILE)
    except vocab.VocabError:
        return vocab.default_profile()
