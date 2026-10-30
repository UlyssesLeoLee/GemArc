"""The rule context: the expanded model plus everything a check needs, and the one place diagnostics are built.

``Context`` carries the model, the provenance map (model path -> ``file:line``), the mode (``slice`` or ``full``), the
``@final`` and ``@pending`` declarations, the incoming slice names, ``known`` ids (remote check: ids of slices the server
does not hold) and the bookkeeping of the phases: which entities are **tainted** by an unresolved reference (cascade
suppression), which references are **pending**, how many binding findings are **pending-bind**.

``Context.add`` is the only constructor of rule diagnostics: it takes severity and hint from the registry, marks a finding
``derived`` when its entity is tainted, defers binding rules in slice mode and drops findings outside the incoming slices.
``Context.ref`` implements the reference rules of design 5.3 (pending, one-edit typo WARN, full-mode ERROR).
Standard library only.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from .. import diag as dg
from .. import model as am
from . import registry as reg

_SPACE = ("buildings", "floors", "rooms")
_RESERVED = {"path", "at", "got", "expected", "entity", "severity", "derived"}

# kind -> (collection, id key); "space" kinds live under model["space"]
_KINDS = {
    "building": ("space.buildings", "id"), "floor": ("space.floors", "id"), "room": ("space.rooms", "id"),
    "node": ("nodes", "id"), "edge": ("edges", "id"), "evidence": ("evidences", "evidenceId"), "machine": ("machines", "id"),
    "case": ("cases", "caseId"), "requirement": ("requirements", "id"), "test": ("tests", "id"), "tbd": ("tbds", "id"),
}


def file_of(at: str) -> str:
    """The file part of a ``file:line`` location."""
    return at.rpartition(":")[0] if ":" in at else at


class Context:
    def __init__(self, model: Mapping, provenance: "Mapping[str, str] | None" = None, *, mode: str = "full", meta: "Mapping | None" = None,
                 finals: Iterable[str] = (), pendings: Iterable[str] = (), slices: Iterable = (), incoming: "Iterable[str] | None" = None,
                 red_drafts: Iterable[str] = (), known: "Mapping[str, Iterable[str]] | None" = None, structural: Iterable = (),
                 wave_set=(1,)):
        if mode not in ("slice", "full"):
            raise ValueError("mode must be 'slice' or 'full'")
        self.model = model
        self.prov: Mapping[str, str] = provenance or {}
        self.has_prov = bool(provenance)
        self.mode = mode
        self.meta = meta
        self.finals = set(finals)
        self.pendings = set(pendings)
        self.slices = list(slices)  # (name, size) pairs
        self.incoming = None if incoming is None else set(incoming)
        self.red_drafts = list(red_drafts)
        self.known = {k: set(v) for k, v in (known or {}).items()}
        self.wave_set = tuple(wave_set)
        self.phase = ""
        self.out: list[dg.Diag] = []
        self.tainted: set[tuple] = set()    # (entity, reference kind) pairs with an unresolved reference (cascade suppression)
        self.opaque: set[tuple] = set()     # (entity, kind) pairs that resolve only through ``known`` ids
        self.pending: list[tuple] = []      # (kind, value, at) forward references (slice mode)
        self.pending_bind = 0
        self.unplaced: list[tuple] = []     # (rule, kind, value, path, at): rows the expander could not place
        self.dropped_ids = {d.got for d in structural if d.severity == dg.ERROR and isinstance(d.got, str)
                            and d.path in ("nodes.id", "machines.id", "edges.alias", "edges.from", "edges.to", "cases.id", "transitions.machine")}
        self._model_ids: dict[str, set] = {}
        self._pools: dict[str, set] = {}
        self._look: dict[Any, dg.LookAlike] = {}
        self._intended: "set | None" = None

    # -- locations --------------------------------------------------------------------------------------------

    def at(self, key: str, *fallbacks: str) -> str:
        for k in (key,) + fallbacks:
            if k in self.prov:
                return self.prov[k]
        return "model:0"

    def derived_field(self, path: str) -> bool:
        """``True`` when the field at ``path`` was derived or defaulted by the expander (the author did not write it)."""
        return path in self.prov

    def in_scope(self, at: str) -> bool:
        return self.incoming is None or at.startswith("model:") or file_of(at) in self.incoming

    @property
    def binding_open(self) -> bool:
        """Binding rules report in full mode, or once the edges section is declared ``@final``."""
        return self.mode == "full" or "edges" in self.finals

    # -- pools ------------------------------------------------------------------------------------------------

    def model_ids(self, kind: str) -> set:
        if kind not in self._model_ids:
            self._model_ids[kind] = self._collect(kind)
        return self._model_ids[kind]

    def _collect(self, kind: str) -> set:
        m = self.model
        if kind in _KINDS:
            collection, key = _KINDS[kind]
            records = (m.get("space") or {}).get(collection[6:], ()) if collection.startswith("space.") else m.get(collection, ())
            return {r[key] for r in records if isinstance(r, Mapping) and isinstance(r.get(key), str)}
        if kind == "requirement or tbd":
            return self.model_ids("requirement") | self.model_ids("tbd")
        if kind == "event":
            from .. import model as am

            return {am.event_id(c["caseId"], e["seq"]) for c in m.get("cases", ()) for e in c.get("events", ())
                    if isinstance(e, Mapping) and isinstance(e.get("seq"), int)}
        raise KeyError(f"unknown reference kind {kind!r}")

    def pool(self, kind: str) -> set:
        if kind not in self._pools:
            extra = set()
            for part in kind.split(" or "):
                extra |= self.known.get(part, set())
            self._pools[kind] = self.model_ids(kind) | extra
        return self._pools[kind]

    def lookalike(self, kind: str, pool: "set | None" = None) -> dg.LookAlike:
        key = kind if pool is None else (kind, id(pool))
        if key not in self._look:
            self._look[key] = dg.LookAlike(self.pool(kind) if pool is None else pool)
        return self._look[key]

    # -- diagnostics ------------------------------------------------------------------------------------------

    def add(self, rule_id: str, *, path: str, at: str, got: Any, expected: str, entity: "str | None" = None,
            severity: "str | None" = None, derived: "bool | None" = None, **hint) -> "dg.Diag | None":
        """Report a finding of ``rule_id``. Returns the diagnostic, or ``None`` when it was filtered or deferred.

        Filtered: the rule does not run in this mode, ``at`` is outside the incoming slices, the entity only resolves
        through ``known`` ids (a cascade rule cannot judge it), or a binding rule runs in slice mode before ``@final edges``
        (counted in ``pending_bind``). ``derived`` (default: ``entity`` is tainted for one of the rule's cascade kinds) marks a
        consequence of an earlier diagnostic: counted, not printed.
        """
        rule = reg.rule(rule_id)
        if self.mode not in rule.modes or not self.in_scope(at):
            return None
        if entity is not None and any((entity, k) in self.opaque and (entity, k) not in self.tainted for k in rule.cascade):
            return None
        if rule.binding and not self.binding_open:
            self.pending_bind += 1
            return None
        if derived is None:
            derived = entity is not None and any((entity, k) in self.tainted for k in rule.cascade)
        text = reg.hint_of(rule_id, **hint)
        d = dg.Diag(rule_id, severity or rule.severity, path, at, got, expected, text, bool(derived))
        self.out.append(d)
        return d

    # -- references -------------------------------------------------------------------------------------------

    def ref(self, kind: str, value: Any, *, rule: str, holder: "str | None", path: str, at: str, pool: "set | None" = None,
            label: "str | None" = None, advice: "str | None" = None) -> bool:
        """Check one reference. Returns ``True`` when it resolves.

        Unresolved, slice mode: declared with ``@pending`` or no look-alike -> **pending** (INFO, grouped by kind);
        exactly one defined id one edit away -> ``WARN probable typo, one edit from: <id>``. Unresolved, full mode:
        ``ERROR`` with the nearest id (or some defined ids) as the hint. The holder entity is tainted either way.
        ``advice`` replaces the full-mode hint when the reference is a missing row rather than a mistyped id.
        """
        if not isinstance(value, str):
            return True  # not an id at all (the contract reports a wrong type); nothing to resolve
        label = label or kind
        ids = self.pool(kind) if pool is None else pool
        if value in ids:
            if holder and pool is None and value not in self.model_ids(kind):
                self.opaque.add((holder, kind))
            return True
        if holder:
            self.tainted.add((holder, kind))
        if value in self.dropped_ids:  # the defining row was refused (ST-...): this is its consequence
            self.add(rule, path=path, at=at, got=value, expected=f"a defined {label} id", derived=True,
                     detail="the row that defines it was refused; fix that first")
            return False
        if kind == "edge" and value in self.intended_edge_ids():  # the edge it names exists, written with a mistyped endpoint
            self.add(rule, path=path, at=at, got=value, expected=f"a defined {label} id", derived=True,
                     detail="that edge was written with a mistyped endpoint; fix the endpoint first")
            return False
        look = self.lookalike(kind, pool)
        if self.mode == "slice":
            if value in self.pendings:  # a deliberate forward reference: neither listed nor warned about
                return False
            found = look.find(value)
            if found:
                self.add(rule, path=path, at=at, got=value, expected=f"a defined {label} id", severity=dg.WARN,
                         detail=f"probable typo, one edit from: {found}")
            elif self.in_scope(at):
                self.pending.append((label, value, at))
            return False
        near = None if advice else (look.find(value) or look.nearest(value))
        if advice:
            detail = advice
        elif near:
            detail = f"probable typo, nearest: {near}"
        else:
            same = sorted(x for x in ids if dg.digit_mask(x) == dg.digit_mask(str(value)))
            if same:
                detail = "define it first, or use one of: " + ", ".join(same[:4]) + (" ..." if len(same) > 4 else "")
            elif len(ids) <= 6:
                detail = "define it first, or use one of: " + (", ".join(sorted(ids)) or "(none is defined yet)")
            else:
                detail = f"define it first, or fix the spelling ({len(ids)} {label} ids are defined)"
        self.add(rule, path=path, at=at, got=value, expected=f"a defined {label} id", detail=detail)
        return False

    def intended_edge_ids(self) -> set:
        """Ids the edges with an unresolved endpoint were meant to have (the look-alike applied to the unresolved endpoint
        **only**: a valid endpoint is never replaced by its neighbour). A reference to such an id is a consequence of the
        endpoint error, not a second defect."""
        if self._intended is None:
            nodes, look = self.pool("node"), self.lookalike("node")
            out: set = set()

            def fix(x):
                if x in nodes:
                    return x
                return look.find(x) or (look.nearest(x) if self.mode == "full" else None) or x

            for e in self.records("edges"):
                s, t = e.get("sourceId"), e.get("targetId")
                if s in nodes and t in nodes:
                    continue
                try:
                    out.add(am.edge_id(fix(s), e["relation"], fix(t)))
                except (am.AtlasModelError, KeyError, TypeError):
                    continue
            self._intended = out
        return self._intended

    def refs(self, kind: str, values: Iterable, **kw) -> None:
        for value in values or ():
            self.ref(kind, value, **kw)

    # -- convenience for checks -------------------------------------------------------------------------------

    def records(self, collection: str) -> list:
        return [r for r in self.model.get(collection, ()) if isinstance(r, Mapping)]

    @property
    def tobe(self) -> bool:
        return (self.model.get("meta") or {}).get("mode") == "tobe"
