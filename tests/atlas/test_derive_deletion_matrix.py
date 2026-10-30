"""The field-deletion matrix: delete (or blank, or garble) each required field in turn.

The reference implementation crashes its diagram builders on a state node without an owner edge, an entity without
columns and a node in an unknown room, and prints the literal ``undefined`` for a missing category, status, port type or
decision. Here every row of the matrix mutates one field on every record of its collection and asserts, for diagrams,
prompts, the policy sections and the test-only skeleton document:

* nothing raises, and the last-resort ``DG-099`` / ``PR-099`` / ``DC-L-099`` codes never appear (so the "never throws"
  result is not produced by a blanket ``except``);
* none of ``undefined|NaN|None|[object`` is printed;
* where the field feeds an output, an issue is reported (builder issue, policy issue or a lint rule over the produced
  text) AND an explicit ``[NOT-AUTHORED:slot]`` marker, or the documented explicit state (the unplaced group), is rendered.

The matrix is table-driven: ``ROWS`` below. A row with no expectation says so (the field feeds no derive output); the
test counts the rows with expectations so the table cannot decay into "no crash only".

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_derive_deletion_matrix*.py"
"""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from gemarc_mcp.atlas import model as am
from gemarc_mcp.atlas.derive import diagrams as D
from gemarc_mcp.atlas.derive import i18n as T
from gemarc_mcp.atlas.derive import lint as L
from gemarc_mcp.atlas.derive import policy as P
from gemarc_mcp.atlas.derive import prompts as R

from .test_derive_diagrams import mermaid_problems
from .test_derive_lint import render_skeleton_doc

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "lending_desk.model.json"
LANGS = T.LANGS
INTERNAL = {"DG-099", "PR-099", "DC-L-099"}


def build_base() -> dict:
    """A small model that has every feature the derive layer reads (tables with columns, a class with methods and
    enumerations, a state machine with an owner, policies of the three kinds, TBDs, outcomes)."""
    m = json.loads(FIXTURE.read_text(encoding="utf-8"))
    room = "rm.desk"
    room_rec, building_rec = m["space"]["rooms"][0], m["space"]["buildings"][0]

    def node_of(*args, **kwargs):
        return am.build_node(*args, room=room_rec, building=building_rec, **kwargs)

    def add(node):
        m["nodes"].append(node)
        if node["id"] not in {x["id"] for x in m["machines"]}:
            m["ports"] += am.derive_ports([node])

    add(node_of("tbl.member", "Table", "Member table", room, "One row per member.", evidence_ids=["EVD-1"],
                      props={"columns": [{"name": "id", "pk": True, "type": "int"}, {"name": "email", "pii": True, "unique": True}]}))
    add(node_of("tbl.copy", "Entity", "Copy entity", room, "One row per copy.", evidence_ids=["EVD-1"],
                      props={"columns": ["id", {"name": "member_id", "fk": "tbl.member"}], "owner": "svc.stock", "store": "tbl.loan"}))
    add(node_of("cls.loan", "Class", "Loan class", room, "Holds a loan.", evidence_ids=["EVD-1"], props={"methods": ["close()", "renew(days)"]}))
    state_node = node_of("st.loan", "State", "Loan status", room, "Where a loan is.", evidence_ids=["EVD-1"], is_machine=True)
    m["nodes"].append(state_node)
    machine = am.build_machine("st.loan", "Open", [{"from": "Open", "to": "Due", "guard": {"param": "late", "op": "present"}, "errorCode": "E_LATE"},
                                                  {"from": "Due", "to": "Closed"}], error_code="E_LOAN", evidence_ids=["EVD-1"])
    m["machines"].append(machine)
    add(node_of("if.status", "Interface", "Status port", room, "Exposes the status.", evidence_ids=["EVD-1"],
                      props={"owner": "svc.loan", "methods": ["status()"], "enums": {"LoanStatus": list(machine["states"])}}))
    m["edges"].append(am.build_edge("tbl.copy", "references", "tbl.member", "belongs to a member", evidence_ids=["EVD-1"], cardinality="N:1"))
    m["edges"].append(am.build_edge("svc.loan", "dependsOn", "svc.stock", "needs stock", evidence_ids=["EVD-2"]))
    m["edges"].append(am.build_edge("svc.loan", "implements", "if.status", "implements the port", evidence_ids=["EVD-1"]))
    m["edges"].append(am.build_owns_edge("svc.loan", machine, state_node, machine_ids=["st.loan"]))
    m["cases"].append(am.build_case("CS-LATE", "abnormal", "A late loan is flagged", [("Transition", "svc.loan", "st.loan", {"ruleId": "R-LATE", "params": {"target": "Due"}})],
                                    requirement_ids=["RQ-1"], evidence_ids=["EVD-1"], initial_machines={"st.loan": "Open"},
                                    expected_terminal_state={"st.loan": "Due"}, expected_outcomes=[{"ruleId": "R-LATE", "outcome": "OK"}]))
    m["requirements"][0]["tbdIds"] = ["TBD-1"]
    m["requirements"][0]["nodeIds"] += ["tbl.copy", "st.loan"]
    m["tbds"] = [am.build_tbd("TBD-1", "Retention period", "Seven years", ["EVD-3"]), am.build_tbd("TBD-2", "Locale", "English first")]
    m["policies"] = [am.build_policy("PL-N", "A borrow answers within two seconds.", "RQ-1", kind="nfr", by="user"),
                     am.build_policy("PL-C", "Only members may borrow.", "RQ-1", kind="constraint", level="BD", by="ai"),
                     am.build_policy("PL-D", "Member rows keep an audit trail.", "RQ-1", scope="kind:Table", kind="data", level="DD", by="ai")]
    issues = am.contract_issues(m)
    assert issues == [], issues[:3]
    return m


# --------------------------------------------------------------------------------------------- mutation engine

def records(model, collection):
    if collection.startswith("space."):
        part = model.get("space", {}).get(collection.split(".", 1)[1], [])
        return [r for r in part if isinstance(r, dict)]
    value = model.get(collection, [])
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def mutate(model, collection, path, op, where=None):
    m = copy.deepcopy(model)
    for record in records(m, collection):
        if where and not where(record):
            continue
        holder = record
        parts = path.split(".")
        for key in parts[:-1]:
            holder = holder.get(key) if isinstance(holder, dict) else None
            if holder is None:
                break
        if not isinstance(holder, dict):
            continue
        last = parts[-1]
        old = holder.get(last, "")
        if op == "delete":
            holder.pop(last, None)
        elif op == "blank":
            holder[last] = [] if isinstance(old, list) else {} if isinstance(old, dict) else 0 if isinstance(old, int) and not isinstance(old, bool) else ""
        elif op == "garbage":
            holder[last] = 12345 if isinstance(old, str) else "garbage-text"
        elif op == "garbage_items":
            holder[last] = [None, 5, {"k": [1]}]
        elif op == "unknown":
            holder[last] = "ghost-id"
        elif op == "null":
            holder[last] = None
        else:
            raise AssertionError(op)
    return m


def drop_owns(model):
    m = copy.deepcopy(model)
    m["edges"] = [e for e in m["edges"] if e.get("relation") != "owns"]
    return m


def drop_policies(model, kind):
    m = copy.deepcopy(model)
    m["policies"] = [p for p in m["policies"] if p.get("kind") != kind]
    return m


def unlink_machine(model):
    m = copy.deepcopy(model)
    m["machines"][0]["id"] = "st.ghost"
    return m


def delete_key(key):
    def run(model):
        m = copy.deepcopy(model)
        m.pop(key, None)
        return m
    return run


def replace_all(value):
    return lambda model: value


is_table = lambda r: r.get("kind") in ("Table", "Entity")  # noqa: E731
is_class = lambda r: r.get("kind") in ("Class", "Interface")  # noqa: E731
is_component = lambda r: r.get("kind") in ("Module", "Service", "Component", "Class", "Interface")  # noqa: E731

# (name, mutator-or-(collection, path, op[, where]), expected rules (any of), expected marker slot prefix, extra check name)
ROWS = [
    # nodes
    ("node.name delete", ("nodes", "name", "delete"), {"DG-011"}, "node.name@", None),
    ("node.name blank", ("nodes", "name", "blank"), {"DG-011"}, "node.name@", None),
    ("node.name garbage", ("nodes", "name", "garbage"), {"DG-011"}, "node.name@", None),
    ("node.sub delete", ("nodes", "sub", "delete"), {"DC-L-004"}, "node.sub@", None),
    ("node.sub blank", ("nodes", "sub", "blank"), {"DC-L-004"}, "node.sub@", None),
    ("node.kind delete", ("nodes", "kind", "delete"), {"DC-L-004"}, "node.kind@", None),
    ("node.kind garbage", ("nodes", "kind", "garbage"), {"DC-L-004"}, "node.kind@", None),
    ("node.roomId delete", ("nodes", "roomId", "delete"), {"DG-011"}, None, "unplaced"),
    ("node.roomId unknown", ("nodes", "roomId", "unknown"), {"DG-011"}, None, "unplaced"),
    ("node.roomId garbage", ("nodes", "roomId", "garbage"), {"DG-011"}, None, "unplaced"),
    ("node.evidenceIds delete", ("nodes", "evidenceIds", "delete"), set(), None, None),
    ("node.evidenceIds garbage", ("nodes", "evidenceIds", "garbage"), set(), None, None),
    ("node.verification delete", ("nodes", "verification", "delete"), set(), None, None),
    ("node.props garbage", ("nodes", "props", "garbage"), set(), None, None),
    ("node.props delete", ("nodes", "props", "delete"), {"DG-011", "PR-002"}, "props.columns@", None),
    ("node.id delete", ("nodes", "id", "delete"), set(), None, None),
    ("node.id garbage", ("nodes", "id", "garbage"), set(), None, None),
    ("entity.columns delete", ("nodes", "props.columns", "delete", is_table), {"DG-011"}, "props.columns@", None),
    ("entity.columns blank", ("nodes", "props.columns", "blank", is_table), {"DG-011"}, "props.columns@", None),
    ("entity.columns garbage", ("nodes", "props.columns", "garbage", is_table), {"DG-011"}, "props.columns@", None),
    ("entity.columns null", ("nodes", "props.columns", "null", is_table), {"DG-011"}, "props.columns@", None),
    ("entity.owner delete", ("nodes", "props.owner", "delete", is_table), set(), None, None),
    ("entity.store delete", ("nodes", "props.store", "delete", is_table), set(), None, None),
    ("class.methods delete", ("nodes", "props.methods", "delete", is_class), {"DG-011"}, "props.methods@", None),
    ("class.methods garbage", ("nodes", "props.methods", "garbage", is_class), {"DG-011"}, "props.methods@", None),
    ("class.owner delete", ("nodes", "props.owner", "delete", is_class), {"DG-002"}, None, None),
    ("class.owner unknown", ("nodes", "props.owner", "unknown", is_class), {"DG-002"}, None, None),
    ("class.enums delete", ("nodes", "props.enums", "delete", is_class), {"DG-002"}, None, None),
    ("class.enums garbage", ("nodes", "props.enums", "garbage", is_class), {"DG-002"}, None, None),
    ("node.props.dedup garbage", ("nodes", "props.dedup", "garbage"), set(), None, None),
    # space
    ("room.floorId unknown", ("space.rooms", "floorId", "unknown"), {"DG-011"}, None, "unplaced"),
    ("room.floorId delete", ("space.rooms", "floorId", "delete"), {"DG-011"}, None, "unplaced"),
    ("floor.buildingId unknown", ("space.floors", "buildingId", "unknown"), {"DG-011"}, None, "unplaced"),
    ("floor.buildingId delete", ("space.floors", "buildingId", "delete"), {"DG-011"}, None, "unplaced"),
    ("floor.level garbage", ("space.floors", "level", "garbage"), set(), None, None),
    ("floor.level delete", ("space.floors", "level", "delete"), set(), None, None),
    ("floor.name delete", ("space.floors", "name", "delete"), {"DG-011", "DC-L-004"}, "group.name@", None),
    ("floor.name blank", ("space.floors", "name", "blank"), {"DG-011", "DC-L-004"}, "group.name@", None),
    ("building.name delete", ("space.buildings", "name", "delete"), {"DG-011"}, None, None),
    ("building.id delete", ("space.buildings", "id", "delete"), {"DG-011"}, None, "unplaced"),
    ("space deleted", delete_key("space"), {"DG-011"}, None, "unplaced"),
    ("space garbage", replace_all(None), None, None, None),
    # edges
    ("edge.label delete", ("edges", "label", "delete"), {"DG-011", "DC-L-004"}, "edge.label@", None),
    ("edge.label blank", ("edges", "label", "blank"), {"DG-011", "DC-L-004"}, "edge.label@", None),
    ("edge.relation delete", ("edges", "relation", "delete"), {"DC-L-004"}, "edge.relation@", None),
    ("edge.relation unknown", ("edges", "relation", "unknown"), set(), None, None),
    ("edge.sourceId unknown", ("edges", "sourceId", "unknown"), set(), None, None),
    ("edge.targetId delete", ("edges", "targetId", "delete"), {"DC-L-004"}, "edge.targetId", None),
    ("edge.cardinality garbage", ("edges", "cardinality", "garbage"), set(), None, None),
    ("edge.static garbage", ("edges", "static", "garbage"), set(), None, None),
    ("edge.id delete", ("edges", "id", "delete"), set(), None, None),
    # machines
    ("machine.initial delete", ("machines", "initial", "delete"), {"DG-011"}, "machine.initial", None),
    ("machine.initial blank", ("machines", "initial", "blank"), {"DG-011"}, "machine.initial", None),
    ("machine.initial+states+transitions blank", lambda m: mutate(mutate(mutate(m, "machines", "states", "blank"), "machines", "transitions", "blank"), "machines", "initial", "blank"), {"DG-011"}, "machine.states", None),
    ("machine.states+transitions blank (single state)", lambda m: mutate(mutate(m, "machines", "states", "blank"), "machines", "transitions", "blank"), None, None, None),
    ("entity.columns garbage items", ("nodes", "props.columns", "garbage_items", is_table), {"DG-011"}, "props.columns@", None),
    ("class.methods garbage items", ("nodes", "props.methods", "garbage_items", is_class), {"DG-011"}, "props.methods@", None),
    ("requirement.tstIds garbage items", ("requirements", "tstIds", "garbage_items"), {"DC-L-004"}, "requirement.tstIds@", None),
    ("machine.states delete", ("machines", "states", "delete"), set(), None, None),
    ("machine.terminal garbage", ("machines", "terminal", "garbage"), set(), None, None),
    ("machine.errorCode delete", ("machines", "errorCode", "delete"), set(), None, None),
    ("machine.transitions garbage", ("machines", "transitions", "garbage"), set(), None, None),
    ("machine.transitions delete", ("machines", "transitions", "delete"), set(), None, None),
    ("machine.id delete", ("machines", "id", "delete"), set(), None, None),
    ("machine without owns edge", drop_owns, {"SM-003"}, None, None),
    ("machine id is no node", unlink_machine, {"DG-011"}, None, None),
    # cases
    ("case.category delete", ("cases", "category", "delete"), {"DC-L-004"}, "case.category@", None),
    ("case.category blank", ("cases", "category", "blank"), {"DC-L-004"}, "case.category@", None),
    ("case.title delete", ("cases", "title", "delete"), {"DC-L-004"}, "case.title@", None),
    ("case.expectedTerminalState delete", ("cases", "expectedTerminalState", "delete"), {"PR-002"}, "case.expectedTerminalState@", None),
    ("case.expectedTerminalState garbage", ("cases", "expectedTerminalState", "garbage"), {"PR-002"}, "case.expectedTerminalState@", None),
    ("case.expectedTerminalState blank", ("cases", "expectedTerminalState", "blank"), {"PR-002"}, "case.expectedTerminalState@", None),
    ("case.expectedOutcomes garbage", ("cases", "expectedOutcomes", "garbage"), set(), None, None),
    ("case.requirementIds garbage", ("cases", "requirementIds", "garbage"), set(), None, None),
    ("case.events delete", ("cases", "events", "delete"), set(), None, None),
    ("case.events garbage", ("cases", "events", "garbage"), set(), None, None),
    ("case.policy garbage", ("cases", "policy", "garbage"), set(), None, None),
    ("case.evidenceIds garbage", ("cases", "evidenceIds", "garbage"), set(), None, None),
    ("case.id delete", ("cases", "caseId", "delete"), set(), None, None),
    # requirements
    ("requirement.title delete", ("requirements", "title", "delete"), {"DC-L-004"}, "requirement.title@", None),
    ("requirement.source delete", ("requirements", "source", "delete"), {"DC-L-004"}, "requirement.source@", None),
    ("requirement.tstIds delete", ("requirements", "tstIds", "delete"), {"DC-L-004"}, "requirement.tstIds@", None),
    ("requirement.tstIds garbage", ("requirements", "tstIds", "garbage"), {"DC-L-004"}, "requirement.tstIds@", None),
    ("requirement.nodeIds garbage", ("requirements", "nodeIds", "garbage"), set(), None, None),
    ("requirement.edgeIds delete", ("requirements", "edgeIds", "delete"), set(), None, None),
    ("requirement.tbdIds garbage", ("requirements", "tbdIds", "garbage"), set(), None, None),
    ("requirement.tbdIds unknown", ("requirements", "tbdIds", "unknown"), set(), None, None),
    # tbds, evidences, tests
    ("tbd.decision delete", ("tbds", "decision", "delete"), {"DC-L-004"}, "tbd.decision@", None),
    ("tbd.decision blank", ("tbds", "decision", "blank"), {"DC-L-004"}, "tbd.decision@", None),
    ("tbd.title delete", ("tbds", "title", "delete"), {"DC-L-004"}, "tbd.title@", None),
    ("tbd.id delete", ("tbds", "id", "delete"), set(), None, None),
    ("evidence.topic delete", ("evidences", "topic", "delete"), {"DC-L-004"}, "evidence.topic@", None),
    ("evidence.filePath delete", ("evidences", "filePath", "delete"), {"DC-L-004"}, "evidence.filePath@", None),
    ("evidence.lineRange garbage", ("evidences", "lineRange", "garbage"), set(), None, None),
    ("evidence.anchor delete", ("evidences", "anchor", "delete"), set(), None, None),
    ("evidence.id delete", ("evidences", "evidenceId", "delete"), set(), None, None),
    ("tests deleted", delete_key("tests"), set(), None, None),
    # policies
    ("policy.src delete", ("policies", "src", "delete"), {"DC-P-001", "DC-L-004"}, "policy.src@", None),
    ("policy.src unknown", ("policies", "src", "unknown"), {"DC-P-001"}, None, None),
    ("policy.text blank", ("policies", "text", "blank"), {"DC-P-003", "DC-L-004"}, "policy.text@", None),
    ("policy.text delete", ("policies", "text", "delete"), {"DC-P-003", "DC-L-004"}, "policy.text@", None),
    ("policy.scope garbage", ("policies", "scope", "garbage"), {"DC-P-002"}, None, None),
    ("policy.scope unknown", ("policies", "scope", "unknown"), {"DC-P-002"}, None, None),
    ("policy.by garbage", ("policies", "by", "garbage"), {"DC-P-004", "DC-L-004"}, "policy.by@", None),
    ("policy.level garbage", ("policies", "level", "garbage"), {"DC-P-004"}, None, None),
    ("policy.kind garbage", ("policies", "kind", "garbage"), {"DC-P-004"}, None, None),
    ("policy.chapter garbage", ("policies", "chapter", "garbage"), set(), None, None),
    ("policy.id delete", ("policies", "id", "delete"), set(), None, None),
    ("no nfr policy", lambda m: drop_policies(m, "nfr"), {"DC-L-004"}, "ch9.nfr", None),
    ("policies deleted", delete_key("policies"), {"DC-L-004"}, "ch9.nfr", None),
    ("policies garbage", lambda m: {**m, "policies": "x"}, {"DC-L-004"}, "ch9.nfr", None),
    # whole-model damage
    ("nodes deleted", delete_key("nodes"), None, None, None),
    ("edges deleted", delete_key("edges"), None, None, None),
    ("cases garbage", lambda m: {**m, "cases": {"x": 1}}, None, None, None),
    ("model is a list", replace_all([1, 2, 3]), None, None, None),
    ("model is None", replace_all(None), None, None, None),
    ("model is a string", replace_all("model"), None, None, None),
    ("model is empty", replace_all({}), None, None, None),
]


def apply_row(base, spec):
    if callable(spec):
        return spec(base)
    collection, path, op, *rest = spec
    return mutate(base, collection, path, op, rest[0] if rest else None)


def exercise(model, lang):
    """Run every derive output on ``model``; return ``(texts, issues)`` (everything produced, everything reported)."""
    texts: list = []
    issues: list = []
    built = D.build_units(model, lang)
    units = built["units"]
    issues += built["issues"]
    for unit in units:
        texts.append(unit["mermaid"])
        issues += unit["issues"]
    issues += L.lint_diagrams(model, units)
    ix = P.Index(model)
    issues += P.policy_issues(ix)
    for ptype in R.TYPES:
        result = R.generate_prompt(model, ptype, None, lang, sealed_case_ids=[c for c in ix.case], chain_lines=["step one"], units=units)
        issues += result["issues"]
        if result["markdown"]:
            texts.append(result["markdown"])
    for level in T.LEVELS:
        doc = render_skeleton_doc(model, lang, level, units=units)
        texts.append(doc)
        issues += L.lint_document(doc, lang=lang, level=level, ix=ix, units=units)
    for text in texts:
        issues += L.lint_free_text(text)
    return texts, units, issues


class MatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = build_base()

    def test_base_is_clean(self):
        for lang in LANGS:
            texts, units, issues = exercise(self.base, lang)
            blocking = [i for i in issues if i["severity"] in ("BLOCK", "ERROR")]
            self.assertEqual(blocking, [], lang)
            self.assertEqual({u["type"] for u in units}, {"component", "class", "state", "er"})
            for text in texts:
                self.assertEqual(T.JUNK_RE.findall(text), [])
                self.assertEqual(T.NOT_AUTHORED_RE.findall(text), [], lang)

    def test_every_row(self):
        checked = 0
        for name, spec, rules, slot, extra in ROWS:
            mutated = apply_row(self.base, spec)
            for lang in LANGS:
                with self.subTest(row=name, lang=lang):
                    texts, units, issues = exercise(mutated, lang)
                    got = {i["rule"] for i in issues}
                    self.assertFalse(got & INTERNAL, (name, lang, [i for i in issues if i["rule"] in INTERNAL]))
                    for text in texts:
                        self.assertEqual(T.JUNK_RE.findall(text), [], (name, lang))
                    for unit in units:
                        self.assertEqual(mermaid_problems(unit["mermaid"]), [], (name, lang, unit["id"]))
                    for item in issues:
                        self.assertEqual(sorted(item), sorted(set(item) & {"rule", "severity", "path", "hint", "got", "expected"}))
                        self.assertNotRegex(item.get("got", ""), r"[\n\r]")
                    if rules:
                        self.assertTrue(got & rules, f"{name}/{lang}: expected one of {sorted(rules)}, got {sorted(got)}")
                    if slot:
                        joined = "\n".join(texts)
                        self.assertIn(f"[NOT-AUTHORED:{slot}", joined, f"{name}/{lang}: no marker for {slot}")
                    if extra == "unplaced":
                        self.assertTrue(any(u["id"] == "component-unplaced" for u in units), f"{name}/{lang}: no unplaced group")
            if rules or slot or extra:
                checked += 1
        self.assertGreaterEqual(checked, 60)

    def test_matrix_covers_the_fields_of_note_08(self):
        """Every field class of the reference analysis has at least one row (name match, so the table cannot lose one silently)."""
        names = " | ".join(r[0] for r in ROWS)
        for needle in ("node.name", "node.sub", "node.roomId", "entity.columns", "entity.owner", "entity.store", "class.methods",
                       "edge.label", "edge.relation", "case.category", "case.expectedTerminalState", "case.title", "machine.initial",
                       "machine.errorCode", "machine.transitions", "machine without owns edge", "tbd.decision", "evidence.topic",
                       "requirement.title", "requirement.tstIds", "policy.src", "no nfr policy", "floor.buildingId", "room.floorId"):
            self.assertIn(needle, names)

    def test_the_three_reference_crashes_are_issues_with_markers(self):
        # State node without owner edge
        m = drop_owns(self.base)
        state = D.build_units(m, "en-US", kinds=["state"])
        self.assertEqual([i["rule"] for i in state["issues"] if i["rule"] == "SM-003"], ["SM-003"])
        self.assertTrue(state["units"][0]["mermaid"].startswith("stateDiagram-v2"))
        # Entity without columns
        m = mutate(self.base, "nodes", "props.columns", "delete", is_table)
        er = D.build_units(m, "en-US", kinds=["er"])
        self.assertIn("DG-011", {i["rule"] for i in er["issues"]})
        self.assertIn("[NOT-AUTHORED:props.columns@tbl.member]", er["units"][0]["mermaid"])
        # Node in an unknown room
        m = mutate(self.base, "nodes", "roomId", "unknown")
        comp = D.build_units(m, "en-US", kinds=["component"])
        self.assertIn("component-unplaced", [u["id"] for u in comp["units"]])
        self.assertIn("DG-011", {i["rule"] for i in comp["issues"]})

    def test_unexpected_exception_is_reported_not_raised_and_has_its_own_code(self):
        class Hostile(dict):
            def get(self, *args, **kwargs):
                raise RuntimeError("hostile")

        built = D.build_units(Hostile(nodes=[]), "en-US")
        self.assertTrue(built["issues"])
        self.assertTrue({i["rule"] for i in built["issues"]} <= INTERNAL | {"DG-011"})
        result = R.generate_prompt(Hostile(), "BD", None, "en-US")
        self.assertEqual([i["rule"] for i in result["issues"]], ["PR-099"])

    def test_reference_scale_models_survive_blanket_damage(self):
        from . import synth
        for seed in (1, 2):
            model = synth.generate("reference", seed)
            for spec in (("nodes", "name", "delete"), ("nodes", "props", "garbage"), ("machines", "initial", "delete"),
                         ("space.rooms", "floorId", "unknown"), ("cases", "expectedTerminalState", "delete"), ("policies", "src", "delete")):
                mutated = apply_row(model, spec)
                for lang in LANGS:
                    texts, units, issues = exercise(mutated, lang)
                    self.assertFalse({i["rule"] for i in issues} & INTERNAL, (seed, spec, lang))
                    for text in texts:
                        self.assertEqual(T.JUNK_RE.findall(text), [], (seed, spec, lang))


if __name__ == "__main__":
    unittest.main()
