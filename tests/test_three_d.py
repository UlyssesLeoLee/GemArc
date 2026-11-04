"""
The semantic-axis derivation, and the binding between the style policy and
the code it governs.

Two things are checked here:

1. The derivation turns a canonical model into the dataset the three
   dimensional scene draws, without inventing anything: an axis the model
   does not carry stays empty, an entity without a value is parked in the
   explicit unset slot, and a type that is not registered is reported.
2. The style policy (BD-010) and the code agree. The policy is a design
   document; if the colours drift away from it, the document is a lie. The
   same rule applies to the palette of the site and of the scene.
"""
import json
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for candidate in (str(REPO_ROOT / "src"), str(REPO_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from gemarc_mcp.scaffold import derive, three_d  # noqa: E402

STYLE_POLICY = REPO_ROOT / "docs" / "02_基本设计" / "10_UI风格方针.md"
SHARED_CSS = REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "templates" / "shared" / "style.css"
AXIS_VIEWER = REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "runtime" / "space" / "axis-viewer-three.js"
SPACE_VIEWER = REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "runtime" / "space" / "space-viewer-three.js"
UNITY_CONTROLS = REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "runtime" / "space" / "unity-controls.js"
SHARED_SHELL = REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "templates" / "shared" / "app.js"
PREVIEW_SHELL = REPO_ROOT / "src" / "gemarc_mcp" / "web" / "app.js"


def _without_comments(source: str) -> str:
    """The code of a module, without its comments.

    A comment may talk about ``Math.random`` to say that the module does not
    use it; the guard has to look at the code, not at the prose.
    """
    without_block = re.sub(r"/\*[\s\S]*?\*/", "", source)
    return re.sub(r"//[^\n]*", "", without_block)

MODEL = {    "meta": {"system": "demo", "evidenceState": "Observed", "language": "zh-CN",
             "version": "1.0.0", "description": "测试模型",
             "source": {"repository": "r", "branch": "b", "commit": "c"}},
    "nodes": [
        {"id": "svc-a", "name": "Alpha", "kind": "Service", "domainId": "billing",
         "properties": {"layer": "应用层", "trustZone": "内部域", "environment": "生产环境"},
         "verification": "Observed", "evidenceIds": ["e-1"]},
        {"id": "mod-b", "name": "Beta", "kind": "Module",
         "properties": {"layer": "数据层", "trustZone": "内部域", "environment": "生产环境"},
         "verification": "ImplementedModel"},
        {"id": "odd-c", "name": "Gamma", "kind": "Unregistered",
         "properties": {"layer": "应用层"},
         "verification": "Hypothetical"},
    ],
    "edges": [
        {"id": "r-1", "sourceId": "svc-a", "targetId": "mod-b", "type": "calls",
         "description": "调用"},
        {"id": "r-2", "sourceId": "mod-b", "targetId": "odd-c", "type": "sends"},
    ],
    "views": [
        {"id": "v-3d", "title": "语义视图", "dimension": "3D",
         "axes": {
             "x": {"field": "properties.layer", "label": "部署层", "values": ["应用层", "数据层"]},
             "y": {"field": "properties.trustZone", "label": "信任域"},
             "z": {"field": "properties.environment", "label": "环境"},
         },
         "nodeIds": ["svc-a", "mod-b", "odd-c"]},
    ],
    "scenarios": [],
    "cases": [],
    "workflows": [],
}


class ThreeDDerivationTests(unittest.TestCase):
    """The dataset the crystal scene draws."""

    def _dataset(self, model=None):
        return three_d.three_d_dataset(model if model is not None else MODEL)

    def test_the_axes_come_from_the_view(self):
        dataset = self._dataset()
        self.assertTrue(dataset["present"])
        view = dataset["views"][0]
        self.assertEqual(view["viewId"], "v-3d")
        self.assertEqual(view["axes"]["x"]["field"], "properties.layer")
        self.assertEqual(view["axes"]["x"]["values"], ["应用层", "数据层"])
        # A field the model does not carry everywhere keeps only the values it
        # really has.
        self.assertEqual(view["axes"]["y"]["values"], ["内部域"])
        self.assertEqual(view["axes"]["z"]["values"], ["生产环境"])

    def test_every_entity_gets_its_value_on_every_axis(self):
        view = self._dataset()["views"][0]
        alpha = next(node for node in view["nodes"] if node["id"] == "svc-a")
        self.assertEqual(alpha["values"]["x"], "应用层")
        self.assertEqual(alpha["values"]["y"], "内部域")
        self.assertEqual(alpha["values"]["z"], "生产环境")
        self.assertEqual(alpha["threeDKind"], "service")
        self.assertEqual(alpha["mcpKind"], "Service")
        self.assertEqual(alpha["domain"], "billing")

    def test_an_entity_without_a_value_is_reported_not_invented(self):
        view = self._dataset()["views"][0]
        gamma = next(node for node in view["nodes"] if node["id"] == "odd-c")
        self.assertEqual(gamma["values"]["y"], None)
        self.assertEqual(sorted(gamma["unmappedAxes"]), ["y", "z"])
        self.assertEqual(sorted(view["axes"]["y"]["unmappedNodeIds"]), ["odd-c"])

    def test_an_unregistered_type_is_reported(self):
        dataset = self._dataset()
        unmapped = dataset["unmappedKinds"]
        self.assertEqual([item["mcp"] for item in unmapped], ["Unregistered"])
        self.assertEqual(unmapped[0]["nodeIds"], ["odd-c"])

    def test_an_axis_that_separates_nothing_is_reported(self):
        view = self._dataset()["views"][0]
        # Every entity of the view shares one trust zone, so that axis does not
        # add a distinguishable combination.
        self.assertFalse(view["independent"]["y"])
        self.assertTrue(view["independent"]["x"])

    def test_a_model_without_axes_says_so(self):
        model = dict(MODEL, views=[])
        dataset = self._dataset(model)
        self.assertFalse(dataset["present"])
        self.assertEqual(dataset["missing"], ["view"])

    def test_the_same_model_always_yields_the_same_dataset(self):
        self.assertEqual(self._dataset(), self._dataset())

    def test_the_relations_of_the_view_are_listed_with_their_mapped_type(self):
        view = self._dataset()["views"][0]
        kinds = {edge["id"]: edge["threeDType"] for edge in view["edges"]}
        self.assertEqual(kinds["r-1"], "calls")
        self.assertEqual(kinds["r-2"], "exchanges")

    def test_the_dataset_travels_with_the_site(self):
        files = derive.derived_files(MODEL)
        self.assertIn("data/three-d.json", files)
        self.assertEqual(files["data/three-d.json"], self._dataset())


class IpaContractTests(unittest.TestCase):
    """The same facts in the shape the standalone builder consumes."""

    def test_the_contract_carries_the_facts(self):
        contract = three_d.ipa_architecture(MODEL)
        self.assertEqual(contract["meta"]["system"], "demo")
        # Observed is the only level that may be called confirmed.
        self.assertEqual(contract["meta"]["evidenceState"], "confirmed")
        kinds = {node["id"]: node["kind"] for node in contract["nodes"]}
        self.assertEqual(kinds["svc-a"], "service")
        self.assertEqual(kinds["mod-b"], "module")
        edges = {(edge["source"], edge["target"]): edge["kind"] for edge in contract["edges"]}
        self.assertEqual(edges[("svc-a", "mod-b")], "calls")

    def test_the_axes_are_bound_to_fields_the_builder_knows(self):
        contract = three_d.ipa_architecture(MODEL)
        view = contract["views"][0]
        self.assertEqual(view["dimensions"]["x"]["field"], "layer")
        self.assertEqual(view["dimensions"]["y"]["field"], "trustZone")
        self.assertEqual(view["dimensions"]["z"]["field"], "environment")

    def test_what_is_missing_is_reported(self):
        contract = three_d.ipa_architecture(MODEL)
        self.assertTrue(any("Unregistered" in note for note in contract["missing"]))
        self.assertTrue(any("owner" in note for note in contract["missing"]))


class StylePolicyBindingTests(unittest.TestCase):
    """The policy document and the code it governs must not drift apart."""

    def _policy_text(self):
        return STYLE_POLICY.read_text(encoding="utf-8")

    def test_the_policy_document_exists(self):
        self.assertTrue(STYLE_POLICY.is_file(), "the style policy document is missing")

    def test_the_site_uses_every_token_of_the_policy(self):
        policy = self._policy_text()
        css = SHARED_CSS.read_text(encoding="utf-8")
        tokens = set(re.findall(r"\| `(--gem-[a-z]+)` \| `#[0-9a-fA-F]{6}`", policy))
        self.assertTrue(tokens, "the policy does not list any token")
        for token in sorted(tokens):
            with self.subTest(token=token):
                self.assertIn(f"{token}:", css, f"{token} is in the policy but not in the site")

    def test_the_scene_uses_the_axis_colours_of_the_policy(self):
        policy = self._policy_text()
        viewer = AXIS_VIEWER.read_text(encoding="utf-8")
        for token in ("--gem-aqua", "--gem-violet", "--gem-topaz"):
            with self.subTest(token=token):
                self.assertIn(token, policy)
        for key, value in (("x", "0x6fe3ff"), ("y", "0xb78cff"), ("z", "0xffc46b")):
            with self.subTest(axis=key):
                self.assertRegex(viewer, rf"{key}: {value}")

    def test_the_type_colours_of_the_policy_are_the_colours_of_the_scene(self):
        policy = self._policy_text()
        viewer = AXIS_VIEWER.read_text(encoding="utf-8")
        table = re.search(r"### 4\.3 类型色板(.*?)(?:\n### |\Z)", policy, re.S)
        self.assertIsNotNone(table, "the policy does not carry the type colour table")
        rows = re.findall(r"\|\s*([a-z]+)\s*\|\s*`(#[0-9a-fA-F]{6})`", table.group(1))
        self.assertTrue(rows, "the type colour table is empty")
        for kind, colour in rows:
            with self.subTest(kind=kind):
                self.assertIn(f"{kind}: 0x{colour[1:].lower()}", viewer)

    def test_the_scene_does_not_animate_itself(self):
        """A crystal that moves on its own is not a crystal (policy 2.4)."""
        viewer = AXIS_VIEWER.read_text(encoding="utf-8")
        space = (REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "runtime" / "space"
                 / "space-viewer-three.js").read_text(encoding="utf-8")
        for name, source in (("axis viewer", viewer), ("space viewer", space)):
            with self.subTest(renderer=name):
                self.assertNotIn("Math.random", _without_comments(source),
                                 f"{name} randomises the scene: the same model must "
                                 "always produce the same picture")


class UnityGesturePolicyTests(unittest.TestCase):
    """The Unity gestures of the three-dimensional scenes.

    The policy (BD-010 section 9) says that every operation on a three
    dimensional model follows Unity. The mapping lives in one module; this
    test makes sure the document, the module and the two scenes keep telling
    the same story.
    """

    #: The row of the gesture table, as the module writes it.
    _ENTRY = re.compile(
        r"id:\s*'(?P<id>[^']+)',\s*"
        r"action:\s*'(?P<action>[^']+)',\s*"
        r"shortcut:\s*'(?P<shortcut>[^']+)',\s*"
        r"effect:\s*'(?P<effect>[^']+)',\s*"
        r"unity:\s*'(?P<unity>[^']+)'",
        re.S,
    )

    def _module_rows(self):
        source = UNITY_CONTROLS.read_text(encoding="utf-8")
        rows = []
        for match in self._ENTRY.finditer(source):
            row = match.groupdict()
            rows.append((row["id"], row["action"], row["shortcut"], row["effect"], row["unity"]))
        return rows

    def _policy_rows(self):
        policy = STYLE_POLICY.read_text(encoding="utf-8")
        table = re.search(r"### 9\.1 映射表（唯一口径）(.*?)(?:\n### |\n## |\Z)", policy, re.S)
        self.assertIsNotNone(table, "the policy does not carry the Unity gesture table")
        rows = []
        for line in table.group(1).splitlines():
            if not line.strip().startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) != 4:
                continue
            if set(cells[0]) <= {"-", ":"} or cells[0] == "操作":
                continue
            rows.append(tuple(cells))
        return rows

    def test_the_policy_and_the_module_state_the_same_gestures(self):
        module = self._module_rows()
        policy = self._policy_rows()
        self.assertEqual(len(module), 9, "the gesture table must carry the nine gestures")
        self.assertEqual(len(policy), len(module),
                         "the policy and the module disagree about how many gestures there are")
        for (id_, action, shortcut, effect, unity), cells in zip(module, policy):
            with self.subTest(gesture=id_):
                self.assertEqual(cells, (action, shortcut, effect, unity))

    def test_the_policy_names_the_unity_shortcuts(self):
        mouse_gestures = {"orbit", "pan", "fly", "dolly", "select", "add", "marquee", "clear"}
        for row in self._module_rows():
            with self.subTest(gesture=row[0]):
                self.assertTrue(row[4].strip(), f"{row[0]} names no Unity shortcut")
                if row[0] in mouse_gestures:
                    self.assertIn("Mouse", row[4], f"{row[0]} names no Unity mouse shortcut")
                else:
                    self.assertIn("Frame Selected", row[4],
                                  f"{row[0]} names no Unity keyboard shortcut")

    def test_both_scenes_use_the_one_shared_controller(self):
        for name, path in (("axis viewer", AXIS_VIEWER), ("space viewer", SPACE_VIEWER)):
            with self.subTest(renderer=name):
                source = path.read_text(encoding="utf-8")
                self.assertIn("unity-controls.js", source,
                              f"{name} does not use the shared gesture mapping")
                self.assertIn("UnityControls", source)

    def test_neither_scene_keeps_a_second_gesture_mapping(self):
        """One mapping, or none at all: a second one would drift."""
        for name, path in (("axis viewer", AXIS_VIEWER), ("space viewer", SPACE_VIEWER)):
            with self.subTest(renderer=name):
                code = _without_comments(path.read_text(encoding="utf-8"))
                self.assertNotIn("event.button ===", code,
                                 f"{name} reads the mouse button itself: the gestures "
                                 "live in one module")
                self.assertNotIn("class AxisCameraController", code)
                self.assertNotIn("class SpaceCameraController", code)

    def test_the_shells_take_the_gesture_list_from_the_module(self):
        for name, path in (("shared shell", SHARED_SHELL), ("preview shell", PREVIEW_SHELL)):
            with self.subTest(shell=name):
                source = path.read_text(encoding="utf-8")
                self.assertIn("unity-controls.js", source,
                              f"{name} does not read the gesture list from the module")
                self.assertIn("UNITY_CONTROL_MAP", source)

    def test_the_shells_know_the_three_selection_modes(self):
        for name, path in (("shared shell", SHARED_SHELL), ("preview shell", PREVIEW_SHELL)):
            with self.subTest(shell=name):
                source = path.read_text(encoding="utf-8")
                for mode in ("replace", "toggle", "add"):
                    self.assertIn(f"'{mode}'", source,
                                  f"{name} does not handle the {mode} selection mode")

    def test_the_drag_threshold_of_the_policy_is_the_one_of_the_code(self):
        policy = STYLE_POLICY.read_text(encoding="utf-8")
        source = UNITY_CONTROLS.read_text(encoding="utf-8")
        self.assertIn("5 像素", policy, "the policy does not state the drag threshold")
        self.assertIn("dragThresholdPx: 5", source,
                      "the drag threshold of the code is not the one of the policy")


if __name__ == "__main__":
    unittest.main()
