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
CONSOLE_CSS = REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "templates" / "console" / "console.css"
ACCOUNT_PAGES = REPO_ROOT / "src" / "gemarc_mcp" / "accounts" / "pages.py"
CONSOLE_HTML = REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "templates" / "console" / "index.html"
CONSOLE_JS = REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "templates" / "console" / "console.js"
CONSOLE_SPACE = REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "runtime" / "space" / "console-space.js"
TOON_MATERIAL = REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "runtime" / "space" / "toon-material.js"
AXIS_VIEWER = REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "runtime" / "space" / "axis-viewer-three.js"
SPACE_VIEWER = REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "runtime" / "space" / "space-viewer-three.js"
UNITY_CONTROLS = REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "runtime" / "space" / "unity-controls.js"
SHARED_SHELL = REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "templates" / "shared" / "app.js"
PREVIEW_SHELL = REPO_ROOT / "src" / "gemarc_mcp" / "web" / "app.js"
PREVIEW_CSS = REPO_ROOT / "src" / "gemarc_mcp" / "web" / "style.css"
SKILL_MD = REPO_ROOT / "skills" / "gemarc-media-doc" / "SKILL.md"
PACKED_SKILL_MD = (REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "skills"
                   / "gemarc-media-doc" / "SKILL.md")
SERVICE_PY = REPO_ROOT / "src" / "gemarc_mcp" / "service.py"
SERVER_PY = REPO_ROOT / "src" / "gemarc_mcp" / "server.py"


def _palette_section(policy: str, section: str) -> str:
    """The palette table of one section of the style policy.

    The policy carries two tables: the paper and the ink (3.1) and the
    pigments (3.2). A caller asks for the one it means.
    """
    table = re.search(rf"### {re.escape(section)} .*?\n(\|.*?)\n\n", policy, re.S)
    if table is None:
        return ""
    return table.group(1)


def _rgb(hex_colour: str):
    """The three channels of a hex colour."""
    value = hex_colour.lstrip("#")
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))


def _hsl(hex_colour: str):
    """The HSL triple of a hex colour: hue in degrees, saturation and lightness."""
    import colorsys
    r, g, b = (channel / 255.0 for channel in _rgb(hex_colour))
    hue, lightness, saturation = colorsys.rgb_to_hls(r, g, b)
    return hue * 360.0, saturation, lightness


def _lab(hex_colour: str):
    """The CIELAB triple of a hex colour (D65)."""
    def linear(channel):
        value = channel / 255.0
        if value <= 0.04045:
            return value / 12.92
        return ((value + 0.055) / 1.055) ** 2.4
    r, g, b = (linear(channel) for channel in _rgb(hex_colour))
    x = (r * 0.4124564 + g * 0.3575761 + b * 0.1804375) / 0.95047
    y = r * 0.2126729 + g * 0.7151522 + b * 0.0721750
    z = (r * 0.0193339 + g * 0.1191920 + b * 0.9503041) / 1.08883
    def f(value):
        if value > 0.008856:
            return value ** (1.0 / 3.0)
        return 7.787 * value + 16.0 / 116.0
    fx, fy, fz = f(x), f(y), f(z)
    return 116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)


def _chroma(hex_colour: str) -> float:
    """The CIELAB chroma C* of a hex colour."""
    _, a, b = _lab(hex_colour)
    return (a * a + b * b) ** 0.5


def _lightness(hex_colour: str) -> float:
    """The CIELAB lightness L* of a hex colour."""
    return _lab(hex_colour)[0]


def _contrast(first: str, second: str) -> float:
    """The WCAG contrast ratio of two hex colours."""
    def luminance(hex_colour):
        def linear(channel):
            value = channel / 255.0
            if value <= 0.04045:
                return value / 12.92
            return ((value + 0.055) / 1.055) ** 2.4
        r, g, b = (linear(channel) for channel in _rgb(hex_colour))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


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
        """The site is the paper and the ink: it uses the tokens of section 3.1."""
        policy = self._policy_text()
        css = SHARED_CSS.read_text(encoding="utf-8")
        tokens = set(re.findall(r"\| `(--gem-[a-z0-9-]+)` \| `#[0-9a-fA-F]{6}`",
                                _palette_section(policy, "3.1")))
        self.assertTrue(tokens, "the policy does not list any token")
        for token in sorted(tokens):
            with self.subTest(token=token):
                self.assertIn(f"{token}:", css, f"{token} is in the policy but not in the site")

    def test_the_scene_uses_the_axis_colours_of_the_policy(self):
        """Section 4.4: X is blue, Y is wisteria, Z is persimmon."""
        policy = self._policy_text()
        viewer = AXIS_VIEWER.read_text(encoding="utf-8")
        for token in ("--gem-ai", "--gem-fuji", "--gem-kaki"):
            with self.subTest(token=token):
                self.assertIn(token, policy)
        for key, value in (("x", "0x3d5a72"), ("y", "0x5f5573"), ("z", "0x96501f")):
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


class ComicLayerBindingTests(unittest.TestCase):
    """Sections 14 and 15: the comic flat layer and the one overall pass.

    A style policy is a design document. These two sections are only worth
    something if the code carries them: the site must draw the ink line and
    the hard shadow (a blurred one would break section 14.3), the palette
    must not grow a second set of colours (section 14.6), and the flow text
    that tells an agent what to do must say what the policy says.
    """

    def _policy_text(self):
        return STYLE_POLICY.read_text(encoding="utf-8")

    def _palette(self):
        """The whole palette of section 3: the paper, the ink and the pigments."""
        policy = self._policy_text()
        tokens = {}
        for section in ("3.1", "3.2"):
            tokens.update(dict(re.findall(r"\| `(--gem-[a-z0-9-]+)` \| `(#[0-9a-fA-F]{6})`",
                                          _palette_section(policy, section))))
        self.assertTrue(tokens, "the policy does not list the palette")
        return tokens

    def _rule(self, css, selector):
        """The body of one CSS rule, or a failure that says the rule is gone."""
        found = re.search(rf"^{re.escape(selector)} \{{(.*?)\}}", css, re.S | re.M)
        self.assertIsNotNone(found, f"the site has no rule for {selector}")
        return found.group(1)

    def test_the_policy_carries_the_comic_layer_and_the_overall_pass(self):
        policy = self._policy_text()
        for heading in ("## 14. 平面层的和紙墨描渲染",
                        "### 14.1 墨线",
                        "### 14.3 硬边阴影",
                        "### 14.5 和紙纤维",
                        "## 15. 产出必须经过一次整体 UI/UX 优化（硬约束）",
                        "### 15.1 硬约束",
                        "### 15.2 固定件预置主题，降低成本",
                        "### 15.3 必须写进最终说明"):
            with self.subTest(heading=heading):
                self.assertIn(heading, policy,
                              "the style policy does not carry this section")

    def test_the_site_draws_the_ink_line(self):
        """Section 14.1 and 14.4: the line is on the element, and the title
        of a panel is a caption box."""
        css = SHARED_CSS.read_text(encoding="utf-8")
        panel = self._rule(css, ".panel")
        self.assertRegex(panel, r"border:\s*2px solid var\(--gem-muted\)",
                         "the panel has no ink line")
        main = self._rule(css, "#content > .panel:first-child")
        self.assertRegex(main, r"border-color:\s*var\(--gem-ink\)")
        self.assertRegex(main, r"border-width:\s*3px",
                         "the main panel must carry the heaviest line")
        caption = self._rule(css, ".panel > h2")
        self.assertRegex(caption, r"background:\s*var\(--gem-facet\)")
        self.assertRegex(caption, r"border:\s*2px solid var\(--gem-muted\)")
        self.assertRegex(caption, r"box-shadow:[^;]*var\(--gem-void\)")

    def test_the_shadows_are_hard_and_never_blurred(self):
        """Section 14.3: a hard offset shadow, one layer, blur radius zero."""
        css = SHARED_CSS.read_text(encoding="utf-8")
        values = re.findall(r"box-shadow:\s*([^;]+);", css)
        self.assertTrue(values, "the site casts no shadow at all")
        for value in values:
            with self.subTest(shadow=value):
                self.assertNotIn("blur(", value, "a blurred shadow is not a hard edge")
        hard = [value for value in values if "var(--gem-void)" in value]
        self.assertTrue(hard, "no shadow uses the void colour")
        for value in hard:
            with self.subTest(shadow=value):
                self.assertRegex(value, r"\d+px \d+px 0 var\(--gem-void\)",
                                 "the hard shadow must have a zero blur radius")

    def test_the_site_carries_the_paper_texture(self):
        """Section 14.5: the fibre of washi, and never the dots of print."""
        css = SHARED_CSS.read_text(encoding="utf-8")
        self.assertIn("repeating-linear-gradient(", css,
                      "the page has no fibre texture")
        self.assertNotIn("radial-gradient(", css,
                         "the page still carries a halftone dot grid")
        self.assertNotIn("background-size: 7px 7px", css,
                         "the dot grid of the printed page is still there")

    def test_the_site_does_not_grow_a_second_palette(self):
        """Section 14.6: the palette does not change with the drawing style."""
        palette = {colour.lower() for colour in self._palette().values()}
        css = SHARED_CSS.read_text(encoding="utf-8")
        for colour in sorted(set(re.findall(r"#[0-9a-fA-F]{6}", css))):
            with self.subTest(colour=colour):
                self.assertIn(colour.lower(), palette,
                              "the site uses a colour the policy does not know")

    def test_the_preview_shell_speaks_the_same_language(self):
        """The small viewer is the same product: the same ink, the same shadow."""
        css = PREVIEW_CSS.read_text(encoding="utf-8")
        for token in ("--gem-void", "--gem-muted", "--gem-ink"):
            with self.subTest(token=token):
                self.assertIn(f"{token}:", css,
                              "the preview shell does not use the tokens of the policy")
        self.assertIn("repeating-linear-gradient(", css,
                      "the preview shell has no fibre texture")
        self.assertNotIn("radial-gradient(", css,
                         "the preview shell still carries a halftone dot grid")
        self.assertRegex(css, r"\.panel\{[^}]*border:2px solid var\(--gem-muted\)",
                         "the preview shell draws no ink line")
        self.assertRegex(css, r"box-shadow:[^;]*var\(--gem-void\)",
                         "the preview shell casts no hard shadow")

    def test_the_flow_text_requires_the_overall_pass(self):
        """Section 15: the rule reaches the agent that builds the document."""
        skill = SKILL_MD.read_text(encoding="utf-8")
        for phrase in ("每一份产出都要做一次整体 UI/UX 优化",
                       "风格基准：和紙墨描的宝石主题",
                       "这一轮不能省"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, skill,
                              "the flow text does not require the overall pass")
        service = SERVICE_PY.read_text(encoding="utf-8")
        self.assertIn("UI/UX 优化", service,
                      "the brief does not carry the overall pass")
        self.assertIn("和紙墨描的宝石主题", service,
                      "the brief does not name the style baseline")
        server = SERVER_PY.read_text(encoding="utf-8")
        self.assertIn("整体 UI/UX 优化", server,
                      "the tool instructions do not carry the overall pass")
        self.assertIn("和紙墨描的宝石主题", server,
                      "the tool instructions do not name the style baseline")

    def test_the_packed_flow_text_is_the_same_text(self):
        """The copy that ships inside the package must not drift from the source."""
        source = SKILL_MD.read_bytes().replace(b"\r\n", b"\n")
        packed = PACKED_SKILL_MD.read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(
            source, packed,
            "the packed flow text differs from skills/gemarc-media-doc/SKILL.md",
        )


class ConsoleStyleBindingTests(unittest.TestCase):
    """The console is part of the same product: the policy binds it too.

    The console used to carry its own set of near-duplicate colours
    (``--bg``/``--panel``/``--accent``). Section 12.1 forbids that: the
    background shares the palette of section 3, the crystal scene shares the
    toon material and the gesture mapping of the two public scenes.
    """

    def _policy_text(self):
        return STYLE_POLICY.read_text(encoding="utf-8")

    def _palette(self, sections=("3.1", "3.2")):
        """The palette of section 3: token -> colour (the dark band and the light band)."""
        policy = self._policy_text()
        tokens = {}
        for section in sections:
            block = _palette_section(policy, section)
            self.assertTrue(block, f"the policy does not carry the palette table of section {section}")
            tokens.update(dict(re.findall(r"\| `(--gem-[a-z0-9-]+)` \| `(#[0-9a-fA-F]{6})`", block)))
        self.assertTrue(tokens, "the policy does not list any token")
        return tokens

    def test_the_console_uses_every_token_of_the_policy_with_the_same_colour(self):
        palette = self._palette()
        self.assertTrue(palette, "the policy does not list any token")
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        for token, colour in sorted(palette.items()):
            with self.subTest(token=token):
                self.assertRegex(css, rf"{token}:\s*{colour}\s*;",
                                 f"{token} is {colour} in the policy but not in the console")

    def test_the_console_does_not_start_a_second_palette(self):
        """A second set of near-duplicate colours would drift from the site."""
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        for token in ("--bg", "--panel", "--accent", "--line", "--ink", "--muted",
                      "--warn", "--bad", "--good", "--panel-2"):
            with self.subTest(token=token):
                self.assertNotRegex(css, rf"^\s*{token}\s*:", f"the console defines {token} of its own")

    def test_the_console_colours_are_the_tokens_of_the_policy(self):
        """No raw colour may sneak into the console: only the tokens."""
        css = CONSOLE_CSS.read_text(encoding="utf-8")
        body = css.split(":root", 1)[-1].split("}", 1)[0]
        for hex_colour in re.findall(r"#[0-9a-fA-F]{6}", body):
            with self.subTest(colour=hex_colour):
                self.assertIn(hex_colour.lower(), [c.lower() for c in self._palette().values()],
                              "the console root declares a colour the policy does not know")

    def test_the_console_scene_shares_the_toon_material_and_the_gestures(self):
        source = CONSOLE_SPACE.read_text(encoding="utf-8")
        self.assertIn("toon-material.js", source,
                      "the console scene does not use the shared toon material")
        self.assertIn("unity-controls.js", source,
                      "the console scene does not use the shared gesture mapping")
        self.assertIn("UnityControls", source)
        # A second gesture mapping would drift from the nine rows of section 9.1.
        self.assertNotIn("event.button ===", _without_comments(source))

    def test_the_toon_material_has_exactly_one_implementation(self):
        """Section 13.4: the material lives in one module and everybody uses it."""
        root = REPO_ROOT / "src" / "gemarc_mcp"
        makers = [path for path in root.rglob("*.js")
                  if "createToonMaterial" in path.read_text(encoding="utf-8")
                  and "export function createToonMaterial" in path.read_text(encoding="utf-8")]
        self.assertEqual([path.name for path in makers], ["toon-material.js"],
                         "the toon material must exist in one module only")
        for name, path in (("axis viewer", AXIS_VIEWER), ("space viewer", SPACE_VIEWER),
                           ("console scene", CONSOLE_SPACE)):
            with self.subTest(renderer=name):
                self.assertIn("toon-material.js", path.read_text(encoding="utf-8"))

    def test_the_toon_material_is_the_one_the_policy_describes(self):
        """Section 13.1: three bands, a toon material, a constant gradient map."""
        policy = self._policy_text()
        source = TOON_MATERIAL.read_text(encoding="utf-8")
        self.assertIn("3 阶", policy)
        self.assertIn("TOON_BAND_COUNT = 3", source)
        self.assertIn("MeshToonMaterial", source)
        self.assertIn("DataTexture", source)
        self.assertIn("NearestFilter", source)
        # The gradient is a constant: it never depends on the entity or the frame.
        self.assertNotIn("Math.random", _without_comments(source))

    def test_the_console_scene_does_not_animate_itself(self):
        source = CONSOLE_SPACE.read_text(encoding="utf-8")
        self.assertNotIn("Math.random", _without_comments(source),
                         "the console scene randomises itself: the same tool list must "
                         "always produce the same picture")

    def test_the_console_scene_uses_the_colours_of_the_policy(self):
        """Section 12.4: a read tool is blue, a write tool is persimmon."""
        source = CONSOLE_SPACE.read_text(encoding="utf-8")
        for name in ("PALETTE.ai", "PALETTE.kaki", "PALETTE.bg"):
            with self.subTest(colour=name):
                self.assertIn(name, source,
                              "the console scene does not take its colours from the one palette")

    def test_the_scenes_share_the_one_palette(self):
        """Section 16.4: the scenes may not grow a second skin."""
        expected = {token.replace("--gem-", ""): colour for token, colour in self._palette().items()}
        source = TOON_MATERIAL.read_text(encoding="utf-8")
        block = re.search(r"export const PALETTE = Object\.freeze\(\{(.*?)\}\);", source, re.S)
        self.assertIsNotNone(block, "the shared palette module is missing")
        found = dict(re.findall(r"(\w+):\s*(0x[0-9a-fA-F]{6})", block.group(1)))
        self.assertTrue(found, "the shared palette module carries no colour")
        self.assertEqual(set(found), set(expected),
                         "the shared palette and the policy disagree on the colours")
        for name, hex_value in sorted(found.items()):
            with self.subTest(colour=name):
                self.assertEqual(hex_value.lower().replace("0x", "#"),
                                 expected[name].lower(),
                                 f"{name} is not the colour of the policy")

    def test_the_console_has_a_scene_and_says_so_when_it_is_missing(self):
        html = CONSOLE_HTML.read_text(encoding="utf-8")
        self.assertIn('id="space-host"', html)
        self.assertIn('id="space-hint"', html)
        # The library is optional: the page resolves it through an import map.
        self.assertIn("importmap", html)
        self.assertIn('"three"', html)

    def test_the_account_pages_share_the_palette_and_the_theme(self):
        """The account page and the operator area are the same product."""
        palette = self._palette()
        source = ACCOUNT_PAGES.read_text(encoding="utf-8")
        css = re.search(r'CSS = """(.*?)"""', source, re.S)
        self.assertIsNotNone(css, "the account pages carry no stylesheet")
        block = css.group(1)
        for token, colour in sorted(palette.items()):
            with self.subTest(token=token):
                self.assertRegex(block, rf"{token}:\s*{colour}\s*[;}}]",
                                 f"{token} is {colour} in the policy but not in the account pages")
        # A second palette is forbidden, and so is a second theme.
        for token in ("--bg", "--fg", "--mute", "--line", "--card", "--acc", "--bad", "--ok"):
            with self.subTest(token=token):
                self.assertNotRegex(block, rf"{token}\s*:", f"the account pages define {token} of their own")
        self.assertNotIn("prefers-color-scheme", block,
                         "the backend is one light theme: a dark one would be a second palette")
        self.assertIn("color-scheme:light", block)

    # --- section 12.2: the backend is the same sheet of paper ---------------

    def test_the_backend_is_the_washi_paper(self):
        """Section 12.2: the backend is one sheet of paper - and only one."""
        policy = self._policy_text()
        self.assertIn("浅色单主题", policy)
        for name, path in (("console", CONSOLE_CSS), ("account pages", ACCOUNT_PAGES)):
            with self.subTest(surface=name):
                css = path.read_text(encoding="utf-8")
                self.assertNotIn("prefers-color-scheme", css,
                                 "the backend does not follow the system preference: "
                                 "a second theme would be a second palette")
                self.assertRegex(css, r"color-scheme:\s*light",
                                 "the backend declares one light theme")
                self.assertIn("repeating-linear-gradient(", css,
                              "the backend has no fibre texture")

    def test_the_backend_has_no_translucent_shell_and_no_halo(self):
        """Sections 8 and 12.2: wabi-sabi is paper, not a translucent shell."""
        policy = self._policy_text()
        self.assertIn("没有玻璃层、没有背景光晕、没有背景模糊", policy)
        for name, path in (("console", CONSOLE_CSS), ("account pages", ACCOUNT_PAGES)):
            with self.subTest(surface=name):
                css = path.read_text(encoding="utf-8")
                self.assertNotIn("backdrop-filter", css,
                                 "the backend has a translucent shell; the policy forbids it")
                self.assertNotIn("radial-gradient", css,
                                 "the backend still carries a halo behind the page")
                self.assertNotIn("@keyframes", css, "the page must stay still")

    # --- section 12.6: the first screen says a few phrases ---------------

    def test_the_status_line_says_a_few_phrases_and_folds_the_values(self):
        """Section 12.6: at most five phrases, the values in the details."""
        policy = self._policy_text()
        self.assertIn("不超过 5 个要点", policy)
        source = CONSOLE_JS.read_text(encoding="utf-8")
        body = re.search(r"function renderStatus\(health\) \{(.*?)\n\}", source, re.S)
        self.assertIsNotNone(body, "the console has no renderStatus")
        phrases = re.findall(r"bar\.append\(phrase\(", body.group(1))
        self.assertLessEqual(len(phrases), 5,
                             "the first screen may carry at most five phrases")
        self.assertGreaterEqual(len(phrases), 3)
        # The protocol values are folded away, not shown on the first screen.
        self.assertIn("details", source, "the technical values have no folded details")
        html = CONSOLE_HTML.read_text(encoding="utf-8")
        self.assertIn("<details", html)

    # --- section 12.4: the tool surface moves, the public scenes do not --

    def test_the_tool_surface_breathes_and_the_public_scenes_stay_still(self):
        """Section 12.4: the motion is the tool surface's alone."""
        policy = self._policy_text()
        self.assertIn("缓慢自转", policy)
        self.assertIn("呼吸辉光", policy)
        scene = CONSOLE_SPACE.read_text(encoding="utf-8")
        self.assertIn("rotation.y", _without_comments(scene), "the crystals do not turn")
        self.assertIn("emissiveIntensity", _without_comments(scene), "the crystals do not breathe")
        for name, path in (("axis viewer", AXIS_VIEWER), ("space viewer", SPACE_VIEWER)):
            with self.subTest(renderer=name):
                code = _without_comments(path.read_text(encoding="utf-8"))
                self.assertNotIn("rotation.y", code, f"{name} turns by itself")
                self.assertNotIn("emissiveIntensity", code, f"{name} breathes by itself")

    def test_the_motion_answers_the_reader_who_asks_for_less(self):
        """Section 7: the motion is off when the reader asked for less of it."""
        policy = self._policy_text()
        self.assertIn("prefers-reduced-motion", policy)
        scene = CONSOLE_SPACE.read_text(encoding="utf-8")
        self.assertIn("prefers-reduced-motion", scene)
        self.assertIn("lessMotion", scene)


class WabiSabiConstraintTests(unittest.TestCase):
    """Section 16.2: the five hard constraints, measured with the same ruler.

    The policy writes them down with a method, a limit and a measured value.
    This class is that ruler: it measures the palette of the policy and fails
    when a colour drifts out of the bound.
    """

    PIGMENTS = ("--gem-ai", "--gem-fuji", "--gem-kaki", "--gem-koke", "--gem-sabi")
    NEUTRALS = ("--gem-void", "--gem-bg", "--gem-panel", "--gem-facet",
                "--gem-rim", "--gem-ink", "--gem-muted")
    PAPER_FACES = ("--gem-bg", "--gem-panel", "--gem-facet")

    def _palette(self):
        policy = STYLE_POLICY.read_text(encoding="utf-8")
        tokens = {}
        for section in ("3.1", "3.2"):
            tokens.update(dict(re.findall(r"\| `(--gem-[a-z0-9-]+)` \| `(#[0-9a-fA-F]{6})`",
                                          _palette_section(policy, section))))
        self.assertTrue(tokens, "the policy does not list any token")
        return tokens

    def test_item_1_no_pure_black_and_no_pure_white(self):
        palette = self._palette()
        for token, colour in sorted(palette.items()):
            with self.subTest(token=token):
                self.assertNotIn(colour.lower(), ("#000000", "#ffffff"),
                                 "the palette has no pure black and no pure white")

    def test_item_1_the_neutral_colours_are_warm(self):
        palette = self._palette()
        for token in self.NEUTRALS:
            with self.subTest(token=token):
                hue = _hsl(palette[token])[0]
                self.assertTrue(25.0 <= hue <= 45.0,
                                f"{token} has the hue {hue:.1f}: paper is not neutral")

    def test_item_2_the_chroma_of_the_pigments_and_of_the_paper(self):
        palette = self._palette()
        for token in self.PIGMENTS:
            with self.subTest(token=token):
                self.assertLessEqual(_chroma(palette[token]), 50.0,
                                     f"{token} is louder than a pigment may be")
        for token in self.PAPER_FACES:
            with self.subTest(token=token):
                self.assertLessEqual(_chroma(palette[token]), 12.0,
                                     f"{token} is more chromatic than paper may be")

    def test_item_2_the_type_palette_stays_under_the_ceiling(self):
        policy = STYLE_POLICY.read_text(encoding="utf-8")
        table = re.search(r"### 4\.3 类型色板(.*?)(?:\n### |\Z)", policy, re.S)
        self.assertIsNotNone(table, "the policy does not carry the type colour table")
        rows = re.findall(r"\|\s*([a-z]+)\s*\|\s*`(#[0-9a-fA-F]{6})`", table.group(1))
        self.assertTrue(rows, "the type colour table is empty")
        for kind, colour in rows:
            with self.subTest(kind=kind):
                self.assertLessEqual(_chroma(colour), 50.0)

    def test_item_3_the_three_paper_faces_are_close_in_lightness(self):
        palette = self._palette()
        faces = [_lightness(palette[token]) for token in self.PAPER_FACES]
        self.assertLessEqual(max(faces) - min(faces), 8.0,
                             "the layers of the paper come from the material, "
                             "not from a strong contrast")

    def test_item_5_the_text_and_the_control_boundaries_reach_the_floor(self):
        palette = self._palette()
        paper = palette["--gem-bg"]
        for token in ("--gem-ink", "--gem-muted"):
            with self.subTest(token=token):
                self.assertGreaterEqual(_contrast(palette[token], paper), 4.5,
                                        f"{token} does not carry body text on paper")
        for token in ("--gem-muted", "--gem-ai"):
            with self.subTest(token=token):
                self.assertGreaterEqual(_contrast(palette[token], paper), 3.0,
                                        f"{token} does not carry a control boundary")
        # The rim is the weak tier: it may not be the boundary of a control.
        self.assertLess(_contrast(palette["--gem-rim"], paper), 3.0)

    def test_the_pigments_all_reach_the_body_text_floor(self):
        palette = self._palette()
        paper = palette["--gem-bg"]
        for token in self.PIGMENTS:
            with self.subTest(token=token):
                self.assertGreaterEqual(_contrast(palette[token], paper), 4.5)


class ToonMaterialBindingTests(unittest.TestCase):
    """Sections 4.2, 13.1 and 13.2: the one material, drawn the way the policy
    says - banded light, an opaque body and an ink line that is ink."""

    def _source(self):
        return TOON_MATERIAL.read_text(encoding="utf-8")

    def test_the_body_is_opaque(self):
        """Section 4.2: paper does not let the light through."""
        source = self._source()
        self.assertIn("transparent: false", source,
                      "the crystal body is translucent; the policy forbids it")
        self.assertNotIn("transparent: true", source,
                         "the crystal body is translucent; the policy forbids it")

    def test_the_dark_band_is_never_a_hole(self):
        """Section 13.1: the dark band is 55% and never darker."""
        source = self._source()
        match = re.search(r"TOON_BAND_STEPS = Object\.freeze\(\[([^\]]+)\]\)", source)
        self.assertIsNotNone(match, "the toon material carries no band table")
        steps = [int(value.strip(), 16) for value in match.group(1).split(",")]
        self.assertEqual(len(steps), 3, "the light is banded into three steps")
        self.assertGreaterEqual(steps[0], 0x8c,
                                "the dark band is darker than 55%: it reads as a hole")
        self.assertEqual(steps[-1], 0xff, "the bright band is 100%")

    def test_the_ink_line_is_the_ink(self):
        """Section 13.2: the outline never takes a brighter step of the body."""
        for name, path in (("axis viewer", AXIS_VIEWER),
                           ("space viewer", SPACE_VIEWER),
                           ("console scene", CONSOLE_SPACE)):
            with self.subTest(renderer=name):
                source = path.read_text(encoding="utf-8")
                self.assertIn("PALETTE.ink", source,
                              f"{name} does not draw the ink line with the ink")
                self.assertNotIn("brighten(", source,
                                 f"{name} still brightens the body colour into an edge")


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
