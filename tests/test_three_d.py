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
GEM_MATERIAL = REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "runtime" / "space" / "gem-material.js"
AXIS_VIEWER = REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "runtime" / "space" / "axis-viewer-three.js"
SPACE_VIEWER = REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "runtime" / "space" / "space-viewer-three.js"
UNITY_CONTROLS = REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "runtime" / "space" / "unity-controls.js"
FLOW_ARROWS = REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "runtime" / "space" / "flow-arrows.js"
PRODUCT_ARCHITECTURE = (REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "runtime" / "space"
                        / "product-architecture.js")
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


_FUNCTIONAL_COLOUR = re.compile(
    r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})"
    r"|hsla?\(\s*(\d{1,3})\s*,\s*(\d{1,3})%\s*,\s*(\d{1,3})%"
)


def _functional_colours(source: str):
    """Every colour written as a function: rgb(), rgba(), hsl(), hsla().

    A hex scan cannot see these. A colour written this way is still a
    colour, and if the policy does not know it, it is a second skin.
    """
    for match in _FUNCTIONAL_COLOUR.finditer(source):
        if match.group(1) is not None:
            yield match.group(0), (int(match.group(1)), int(match.group(2)),
                                   int(match.group(3)))
        else:
            hue = int(match.group(4)) % 360
            saturation = int(match.group(5)) / 100.0
            lightness = int(match.group(6)) / 100.0
            chroma = (1 - abs(2 * lightness - 1)) * saturation
            second = chroma * (1 - abs(hue % 60 // 30 - 1))
            offset = lightness - chroma / 2
            table = [(chroma, second, 0), (second, chroma, 0), (0, chroma, second),
                     (0, second, chroma), (second, 0, chroma), (chroma, 0, second)]
            yield match.group(0), tuple(round(channel * 255 + offset)
                                        for channel in table[hue // 60])


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
        # The table of the kinds is a fact of the product, so it lives in the
        # shared gem module and the scenes read it from there (section 13.4).
        viewer = GEM_MATERIAL.read_text(encoding="utf-8")
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
    gem material and the gesture mapping of the two public scenes.
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

    def test_the_console_scene_shares_the_gem_material_and_the_gestures(self):
        source = CONSOLE_SPACE.read_text(encoding="utf-8")
        self.assertIn("gem-material.js", source,
                      "the console scene does not use the shared gem material")
        self.assertIn("unity-controls.js", source,
                      "the console scene does not use the shared gesture mapping")
        self.assertIn("UnityControls", source)
        # A second gesture mapping would drift from the nine rows of section 9.1.
        self.assertNotIn("event.button ===", _without_comments(source))

    def test_the_gem_material_has_exactly_one_implementation(self):
        """Section 13.4: the material lives in one module and everybody uses it."""
        root = REPO_ROOT / "src" / "gemarc_mcp"
        makers = [path for path in root.rglob("*.js")
                  if "createGemMaterial" in path.read_text(encoding="utf-8")
                  and "export function createGemMaterial" in path.read_text(encoding="utf-8")]
        self.assertEqual([path.name for path in makers], ["gem-material.js"],
                         "the gem material must exist in one module only")
        for name, path in (("axis viewer", AXIS_VIEWER), ("space viewer", SPACE_VIEWER),
                           ("console scene", CONSOLE_SPACE)):
            with self.subTest(renderer=name):
                self.assertIn("gem-material.js", path.read_text(encoding="utf-8"))

    def test_the_gem_material_is_the_one_the_policy_describes(self):
        """Section 13.1: a real stone - refraction, dispersion and cut faces."""
        policy = self._policy_text()
        source = GEM_MATERIAL.read_text(encoding="utf-8")
        self.assertIn("MeshPhysicalMaterial", source)
        self.assertIn("transmission", source)
        self.assertIn("dispersion", source)
        self.assertIn("flatShading: true", source)
        # The parameters are constants: the same model renders the same pixels.
        self.assertNotIn("Math.random", source)
        for marker in ("transmission", "ior", "dispersion", "flatShading: true"):
            with self.subTest(marker=marker):
                self.assertIn(marker, policy,
                              f"the policy does not write the stone down: {marker}")
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
        source = GEM_MATERIAL.read_text(encoding="utf-8")
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


class OneSkinTests(unittest.TestCase):
    """Section 16.4: the site, the scene, the backend and the produced file
    share one skin.

    Two kinds of second skin slip past a hex scan. A colour can be written
    as a function - rgb(), rgba(), hsl() - and a page can still declare the
    dark stage of the old theme. Both are checked here, over every surface
    the reader can see.
    """

    SURFACES = (
        ("site shell", REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "templates" / "shared" / "style.css"),
        ("preview shell", REPO_ROOT / "src" / "gemarc_mcp" / "web" / "style.css"),
        ("console", REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "templates" / "console" / "console.css"),
        ("account pages", REPO_ROOT / "src" / "gemarc_mcp" / "accounts" / "pages.py"),
        ("gem material", GEM_MATERIAL),
        ("axis viewer", AXIS_VIEWER),
        ("space viewer", SPACE_VIEWER),
        ("console scene", CONSOLE_SPACE),
        ("unity controls", UNITY_CONTROLS),
    )

    PAGES = (
        ("site shell", REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "templates" / "static" / "index.html"),
        ("vite shell", REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "templates" / "vite" / "index.html"),
        ("preview shell", REPO_ROOT / "src" / "gemarc_mcp" / "web" / "index.html"),
        ("console", CONSOLE_HTML),
    )

    def _palette(self):
        policy = STYLE_POLICY.read_text(encoding="utf-8")
        tokens = {}
        for section in ("3.1", "3.2"):
            tokens.update(dict(re.findall(r"\| `(--gem-[a-z0-9-]+)` \| `(#[0-9a-fA-F]{6})`",
                                          _palette_section(policy, section))))
        self.assertTrue(tokens, "the policy does not list the palette")
        return tokens

    def test_no_surface_names_a_colour_the_policy_does_not_know(self):
        """A colour written as a function is still a colour."""
        known = [_rgb(colour) for colour in self._palette().values()]
        for name, path in self.SURFACES:
            source = path.read_text(encoding="utf-8")
            for notation, channels in _functional_colours(source):
                with self.subTest(surface=name, colour=notation):
                    self.assertTrue(
                        any(all(abs(a - b) <= 2 for a, b in zip(channels, candidate))
                            for candidate in known),
                        f"{name} writes {notation}: the policy knows no such colour")

    def test_no_page_declares_the_dark_stage(self):
        """The paper is the only theme: a page may not ask for the dark one."""
        for name, path in self.PAGES:
            html = path.read_text(encoding="utf-8")
            with self.subTest(page=name):
                self.assertNotIn('name="color-scheme" content="dark"', html,
                                 "the page still declares the dark stage")
                self.assertNotIn("prefers-color-scheme", html,
                                 "the page follows the system: that would be a second theme")
                self.assertIn('name="color-scheme" content="light"', html,
                              "the page does not declare the paper")

    def test_the_paper_carries_no_light_on_it(self):
        """Section 16.3: the layers are paper depth and a rule, not a highlight."""
        for name, path in self.SURFACES:
            if path.suffix != ".css":
                continue
            source = path.read_text(encoding="utf-8")
            for value in re.findall(r"box-shadow:\s*([^;}]+)", source):
                with self.subTest(surface=name, shadow=value):
                    self.assertNotIn("inset", value,
                                     "an inner highlight is light on the paper")


class GemMaterialBindingTests(unittest.TestCase):
    """Sections 4.2, 13.1 and 13.2: the one material, drawn the way the policy
    says - a real stone with cut faces, and an ink line that is ink."""

    def _source(self):
        return GEM_MATERIAL.read_text(encoding="utf-8")

    def _policy(self):
        return STYLE_POLICY.read_text(encoding="utf-8")

    def test_the_body_is_a_real_stone(self):
        """Section 4.2 and 13.1: the stone lets the light through and bends it."""
        source = self._source()
        self.assertIn("MeshPhysicalMaterial", source,
                      "the stone is not a physical material: it cannot refract")
        match = re.search(r"GEM_TRANSMISSION = ([0-9.]+);", source)
        self.assertIsNotNone(match, "the material does not export the transmission")
        transmission = float(match.group(1))
        self.assertTrue(0.8 <= transmission <= 0.95,
                        f"the transmission {transmission} is outside the range of the policy: "
                        "a fully transmissive stone reads as a tinted pane, an opaque one as plastic")
        self.assertIn("clearcoat: GEM_CLEARCOAT", source,
                      "the stone has no polished coat: the cut faces lose their glint")

    def test_the_stone_is_polished_and_not_a_metal(self):
        """Section 13.1: a cut face is smooth; a stone is not a metal."""
        source = self._source()
        match = re.search(r"GEM_ROUGHNESS = ([0-9.]+);", source)
        self.assertIsNotNone(match, "the material does not export the roughness")
        roughness = float(match.group(1))
        self.assertTrue(0 <= roughness <= 0.05,
                        f"the stone is too rough ({roughness}): it reads as plastic")
        self.assertIn("metalness: 0", source,
                      "a stone is not a metal")

    def test_the_refraction_carries_the_gem_index_and_the_colour_spread(self):
        """Section 4.7: the light bends, and it splits into colours."""
        source = self._source()
        match = re.search(r"GEM_IOR = ([0-9.]+);", source)
        self.assertIsNotNone(match, "the material does not export the index of refraction")
        ior = float(match.group(1))
        self.assertTrue(2.0 <= ior <= 2.4,
                        f"the index of refraction {ior} is outside the range of the policy")
        match = re.search(r"GEM_DISPERSION = ([0-9.]+);", source)
        self.assertIsNotNone(match, "the material does not export the dispersion")
        self.assertGreater(float(match.group(1)), 0,
                           "the stone does not split the light: there is no colour at its edges")
        self.assertIn("attenuationColor", source,
                      "the type colour is not in the stone")
        self.assertIn("attenuationDistance", source,
                      "the stone has no depth of colour")

    def test_the_cut_faces_are_hard_and_the_inner_faces_are_seen(self):
        """Section 13.1: one normal per cut face, and both sides of the stone."""
        source = self._source()
        self.assertIn("flatShading: true", source,
                      "the stone is smooth: the border between two cut faces would blur")
        self.assertIn("side: THREE.DoubleSide", source,
                      "the stone is drawn on one side only: it reads as a pane")

    def test_the_surface_of_the_stone_is_not_painted(self):
        """Section 4.7: the colour is in the stone, not on it."""
        source = self._source()
        self.assertIn("color: 0xffffff", source,
                      "the surface of the stone carries a colour of its own")

    def test_the_policy_writes_the_same_parameters_down(self):
        """Section 13.1: the document and the code agree."""
        policy = self._policy()
        for marker in ("`transmission`", "`ior`", "`dispersion`", "`attenuationColor`",
                       "`flatShading`", "`roughness`"):
            with self.subTest(marker=marker):
                self.assertIn(marker, policy,
                              f"the policy does not write the stone down: {marker}")
        self.assertIn("roughness ≤ 0.05", policy,
                      "the policy does not put a ceiling on the roughness")

    def test_the_policy_and_the_code_agree_on_the_numbers(self):
        """Section 13.1: the table of the policy is the table of the code.

        The table is written down once, but it is written down twice - once
        in the document and once in the module. A stone that does not match
        the document is a stone nobody can check, so the two are compared
        number by number.
        """
        source = self._source()
        policy = self._policy()
        rows = (
            ("GEM_TRANSMISSION", "0.9", "`transmission` | `0.9`"),
            ("GEM_ROUGHNESS", "0.03", "`roughness` | `0.03`"),
            ("GEM_IOR", "2.2", "`ior` | `2.2`"),
            ("GEM_DISPERSION", "1.5", "`dispersion` | `1.5`"),
            ("GEM_THICKNESS_RATIO", "0.85", "`thickness` | 石头半径 × 0.85"),
            ("GEM_ATTENUATION_RATIO", "2.2", "`attenuationDistance` | 石头半径 × 2.2"),
            ("GEM_ENV_INTENSITY", "4.5", "`envMapIntensity` | `4.5`"),
            ("GEM_CLEARCOAT", "1", "`clearcoat` | `1`"),
            ("GEM_CLEARCOAT_ROUGHNESS", "0.03", "`clearcoatRoughness` | `0.03`"),
            ("GEM_TONE_MAPPING_EXPOSURE", "1.6", "`toneMappingExposure` | `1.6`"),
        )
        for constant, value, cell in rows:
            with self.subTest(constant=constant):
                match = re.search(rf"{constant} = ([0-9.]+);", source)
                self.assertIsNotNone(match,
                                     f"the material does not export {constant}")
                self.assertEqual(match.group(1), value,
                                 f"the code says {constant} = {match.group(1)}, "
                                 f"the policy says {value}")
                self.assertIn(cell, policy,
                              f"the policy does not write down {cell}")

    def test_the_studio_is_the_key_light(self):
        """Section 4.5: the room lights the stone, the three lamps fill it.

        The scene lamps add a white diffuse that washes the type colour out
        of the body, so they stay weak; the studio environment is what a
        cut face actually reflects.
        """
        source = self._source()
        match = re.search(r"GEM_LIGHT_SCALE = ([0-9.]+);", source)
        self.assertIsNotNone(match, "the material does not export the light scale")
        scale = float(match.group(1))
        self.assertTrue(0.3 <= scale <= 0.8,
                        f"the light scale {scale} is outside the range of the policy: "
                        "too bright and the type colour is washed out")
        for scene in (AXIS_VIEWER, SPACE_VIEWER, CONSOLE_SPACE):
            with self.subTest(scene=scene.name):
                text = scene.read_text(encoding="utf-8")
                self.assertIn("GEM_LIGHT_SCALE", text,
                              f"{scene.name} does not use the shared light scale")
                self.assertNotIn("* 2.1", text,
                                 f"{scene.name} still turns the fill lights up by hand")

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


class SilhouetteInkTests(unittest.TestCase):
    """Section 4.8: the ink is a hairline around the outside of the stone.

    The inside edges of a cut stone are already visible in the refraction, so
    they are not drawn a second time. The line is the ink of the palette, it
    is counted in pixels on the screen, and it never takes part in picking.
    """

    SCENES = (("axis viewer", AXIS_VIEWER),
              ("space viewer", SPACE_VIEWER),
              ("console scene", CONSOLE_SPACE))

    def _material(self):
        return GEM_MATERIAL.read_text(encoding="utf-8")

    def _shell_source(self):
        """The body of the shell factory, and nothing else."""
        source = self._material()
        match = re.search(r"export function createSilhouetteOutline\(.*?\n\}\n", source, re.S)
        self.assertIsNotNone(match, "the material module has no ink line factory")
        return match.group(0)

    def test_the_ink_line_is_one_material_for_the_three_scenes(self):
        """Section 13.4: the ink line lives in one module."""
        for name, path in self.SCENES:
            with self.subTest(scene=name):
                source = path.read_text(encoding="utf-8")
                self.assertIn("createSilhouetteOutline", source,
                              f"{name} does not take the ink line from the shared material")
                self.assertNotIn("BackSide", source,
                                 f"{name} writes its own ink line: the three would drift")

    def test_the_ink_line_is_only_the_outside(self):
        """Section 4.8: the shell is drawn from the back, so it cannot cover the stone."""
        source = self._shell_source()
        self.assertIn("side: THREE.BackSide", source,
                      "the ink line is drawn over the stone")
        self.assertIn("depthWrite: false", source,
                      "the ink line writes depth: what is behind the stone would be hidden")

    def test_the_ink_line_is_the_ink_of_the_palette(self):
        """Section 13.2 and 4.8: the line is ink, and it is not tone mapped."""
        source = self._shell_source()
        self.assertIn("PALETTE.ink", source,
                      "the ink line does not use the ink of the palette")
        self.assertIn("toneMapped: false", source,
                      "the ink line is tone mapped: the ink would not be the ink")

    def test_the_ink_line_never_takes_part_in_picking(self):
        """Section 4.8: clicking next to a stone is still clicking on nothing."""
        source = self._shell_source()
        self.assertIn("raycast", source,
                      "the ink line takes part in picking: the stone grows a dead zone")

    def test_the_width_of_the_ink_line_is_counted_in_pixels(self):
        """Section 4.8: the line is one pixel wide, near and far."""
        source = self._material()
        match = re.search(r"GEM_OUTLINE_PIXELS = ([0-9.]+);", source)
        self.assertIsNotNone(match, "the material does not export the width of the ink line")
        self.assertAlmostEqual(float(match.group(1)), 1.0, places=3)
        match = re.search(r"GEM_OUTLINE_PIXELS_SELECTED = ([0-9.]+);", source)
        self.assertIsNotNone(match, "the material does not export the width of the selected ink line")
        self.assertGreater(float(match.group(1)), float(re.search(r"GEM_OUTLINE_PIXELS = ([0-9.]+);", source).group(1)),
                           "the selected stone does not carry a heavier ink line")
        self.assertIn("updateSilhouetteWidth", source,
                      "the ink line is not measured against the camera")

    def test_the_policy_writes_the_ink_line_down(self):
        """Section 4.8: the document and the code agree."""
        policy = STYLE_POLICY.read_text(encoding="utf-8")
        section = re.search(r"### 4\.8 .*?(?:\n## |\Z)", policy, re.S)
        self.assertIsNotNone(section, "the policy has no section for the ink line")
        text = section.group(0)
        for marker in ("外轮廓", "1 像素", "BackSide", "拾取"):
            with self.subTest(marker=marker):
                self.assertIn(marker, text,
                              f"the policy does not write the ink line down: {marker}")


class CutFormTests(unittest.TestCase):
    """Sections 4.3 and 13.5: the cut of a stone says what it is.

    The colour says which kind an entity is; the cut says which role it
    plays. The table lives in the shared module, so the three scenes cannot
    give the same kind two different shapes.
    """

    SCENES = (("axis viewer", AXIS_VIEWER),
              ("space viewer", SPACE_VIEWER),
              ("console scene", CONSOLE_SPACE))

    def _material(self):
        return GEM_MATERIAL.read_text(encoding="utf-8")

    def _policy(self):
        return STYLE_POLICY.read_text(encoding="utf-8")

    def test_every_kind_of_the_policy_has_a_cut(self):
        """Section 4.3: the cut table of the policy and the table of the code agree."""
        policy = self._policy()
        block = re.search(r"\*\*切工表（形状编码语义族）\*\*.*?\n\n(.*?)\n\n", policy, re.S)
        self.assertIsNotNone(block, "the policy has no table of the cut works")
        rows = re.findall(r"^\| (明亮式|柱式|棱式|阶式|锥式|橄榄式|盾式|素石) ", block.group(1), re.M)
        self.assertTrue(rows, "the table of the cut works is empty")
        names = {"明亮式": "brilliant", "柱式": "column", "棱式": "wedge", "阶式": "step",
                 "锥式": "pyramid", "橄榄式": "marquise", "盾式": "shield", "素石": "plain"}
        source = self._material()
        for chinese, english in names.items():
            with self.subTest(cut=chinese):
                self.assertIn(chinese, rows, f"the policy does not list the cut {chinese}")
                self.assertIn(f"{english}:", source,
                              f"the material has no cut work named {english}")

    def test_the_cut_works_are_different_shapes(self):
        """Section 4.3: a cut that looks like another cut says nothing."""
        source = self._material()
        block = re.search(r"const CUT_SHAPES = Object\.freeze\(\{(.*?)\n\}\);", source, re.S)
        self.assertIsNotNone(block, "the material has no table of the cut works")
        builds = re.findall(r"build\(THREE\) \{", block.group(1))
        self.assertGreaterEqual(len(builds), 8,
                                "there are not enough cut works: every role needs one")

    def test_every_cut_is_a_cut_and_not_a_vessel(self):
        """Section 4.3: a cut has a table on top and a pavilion below.

        A stone whose profile starts or ends wide is a vessel, not a gem: on
        the paper it reads as a tin. The two cuts that are not turned - the
        raw stone and the long double pyramid - are the exception, and they
        say so in their own shape.
        """
        source = self._material()
        block = re.search(r"const CUT_SHAPES = Object\.freeze\(\{(.*?)\n\}\);", source, re.S)
        self.assertIsNotNone(block, "the material has no table of the cut works")
        profiles = re.findall(r"latheCut\(THREE, \[(.*?)\], (\d+)\)", block.group(1), re.S)
        self.assertGreaterEqual(len(profiles), 6,
                                "the cuts are not built from profiles: they cannot be cut stones")
        sides = []
        for points, segments in profiles:
            rows = re.findall(r"\[([0-9.]+),\s*(-?[0-9.]+)\]", points)
            with self.subTest(sides=segments):
                self.assertGreaterEqual(len(rows), 5,
                                        "a cut needs more than a table and a body")
                self.assertLess(float(rows[0][0]), 0.1,
                                "the cut starts wide: it is a vessel, not a stone")
                self.assertLess(float(rows[-1][0]), 0.1,
                                "the cut ends wide: it is a vessel, not a stone")
            sides.append(int(segments))
        self.assertEqual(len(set(sides)), len(sides),
                         "two cuts have the same number of sides: they look alike")

    def test_the_three_scenes_take_the_cut_from_the_shared_table(self):
        """Section 13.5: the same kind, the same cut, in every scene."""
        for name, path in self.SCENES:
            with self.subTest(scene=name):
                source = path.read_text(encoding="utf-8")
                self.assertIn("createCrystalGeometry", source,
                              f"{name} cuts its own stones: the three would drift")
                self.assertNotIn("new THREE.OctahedronGeometry", source,
                                 f"{name} cuts every stone the same way")
        # The two public scenes read the cut of a kind from the shared table.
        for name, path in (("axis viewer", AXIS_VIEWER), ("space viewer", SPACE_VIEWER)):
            with self.subTest(scene=name):
                self.assertIn("cutFormForKind", path.read_text(encoding="utf-8"),
                              f"{name} does not read the cut of a kind from the shared table")
        # The console cuts by the stage the tool belongs to: the stages of the
        # product's own workflow, each with its own cut (BD-010 section 12.4
        # and 13.6).
        console_source = CONSOLE_SPACE.read_text(encoding="utf-8")
        self.assertIn("product-architecture.js", console_source,
                      "the console scene does not cut its tools by their stage")
        self.assertIn("plan.cut", console_source,
                      "the console scene does not use the cut of the stage")

    def test_the_policy_says_that_the_cut_carries_a_meaning(self):
        """Section 4.3: the cut is not decoration."""
        policy = self._policy()
        self.assertIn("切工表（形状编码语义族）", policy,
                      "the policy does not write the cut table down")
        self.assertIn("颜色说「是哪一类」", policy,
                      "the policy does not say which fact the cut carries")


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


class ArchitectureArrowBindingTests(unittest.TestCase):
    """Sections 4.9, 12.4, 13.6 and 13.7: the arrows and the architecture of the
    product.

    A scene of cut stones says what the stones are; it does not say which way
    the work flows. The arrows say that, and the console's surface draws the
    architecture of the product itself: the stages of the workflow, one cut per
    stage, and the arrows between them. The same modules have to travel with
    the artefacts a user generates, or the produced site would not be the same
    product as the console.
    """

    def _policy(self):
        return STYLE_POLICY.read_text(encoding="utf-8")

    def _source(self):
        return FLOW_ARROWS.read_text(encoding="utf-8")

    def _architecture(self):
        return PRODUCT_ARCHITECTURE.read_text(encoding="utf-8")

    def test_the_arrow_parameters_are_the_ones_the_policy_writes(self):
        """Section 13.7: the table of the policy is the table of the code."""
        source = self._source()
        policy = self._policy()
        rows = (
            ("shaftRadius", "0.2", "`0.2`"),
            ("headRadius", "0.8", "`0.8`"),
            ("headLength", "2.2", "`2.2`"),
            ("pulseRadius", "0.5", "`0.5`"),
            ("pulsePeriod", "2.6", "`2.6`"),
            ("bow", "3.0", "`3.0`"),
            ("tubularSegments", "40", "`40`"),
            ("radialSegments", "8", "`8`"),
        )
        for name, value, cell in rows:
            with self.subTest(parameter=name):
                match = re.search(rf"{name}: ([0-9.]+),", source)
                self.assertIsNotNone(match, f"the arrow module does not export {name}")
                self.assertEqual(match.group(1), value,
                                 f"the code says {name} = {match.group(1)}, "
                                 f"the policy says {value}")
                self.assertIn(cell, policy,
                              f"the policy does not write the arrow parameter down: {name}")

    def test_the_bead_is_geometry_and_not_a_light(self):
        """Section 4.9: the bead that walks the shaft is a solid little ball."""
        source = self._source()
        self.assertIn("SphereGeometry", source,
                      "the bead is not a piece of geometry")
        self.assertIn("toneMapped: false", source,
                      "the bead is tone mapped: the arrow would change its colour")
        self.assertNotIn("emissive:", source,
                         "the bead shines: the policy forbids a light on the arrow")
        self.assertNotIn("emissiveIntensity", source,
                         "the bead shines: the policy forbids a light on the arrow")
        self.assertNotIn("AdditiveBlending", source,
                         "the arrow is additively blended: it would burn into the paper")

    def test_the_stage_table_is_the_one_the_policy_writes(self):
        """Section 13.6: the stages of the product, stage by stage."""
        source = self._architecture()
        policy = self._policy()
        rows = (
            ("entry", "wedge", ("gemarc_media_doc_guide", "gemarc_brief")),
            ("rules", "column", ("gemarc_contracts", "gemarc_assets",
                                 "gemarc_inspect", "gemarc_plan")),
            ("model", "brilliant", ("gemarc_new_model", "gemarc_scaffold", "gemarc_init")),
            ("verify", "shield", ("gemarc_validate", "gemarc_simulate",
                                  "gemarc_diff", "gemarc_verify")),
            ("produce", "pyramid", ("gemarc_render", "gemarc_build",
                                    "gemarc_export", "gemarc_model")),
        )
        for stage, cut, tools in rows:
            with self.subTest(stage=stage):
                self.assertIn(f"id: '{stage}'", source,
                              f"the stage table does not carry {stage}")
                self.assertIn(f"cut: '{cut}'", source,
                              f"the stage {stage} is not cut {cut}")
                for tool in tools:
                    self.assertIn(f"'{tool}'", source,
                                  f"the stage {stage} does not carry {tool}")
                    self.assertIn(f"`{tool}`", policy,
                                  f"the policy does not list {tool} in the stage table")

    def test_the_stage_table_covers_every_tool_the_server_registers(self):
        """Section 13.6: the table knows the tools of both modes."""
        source = self._architecture()
        for tool in ("gemarc_media_doc_guide", "gemarc_brief", "gemarc_contracts",
                     "gemarc_assets", "gemarc_inspect", "gemarc_plan",
                     "gemarc_new_model", "gemarc_scaffold", "gemarc_init",
                     "gemarc_validate", "gemarc_simulate", "gemarc_diff",
                     "gemarc_verify", "gemarc_render", "gemarc_build",
                     "gemarc_export", "gemarc_model"):
            with self.subTest(tool=tool):
                self.assertIn(f"'{tool}'", source,
                              f"the stage table does not know {tool}")

    def test_the_console_draws_the_architecture_and_the_arrows(self):
        """Section 12.4: the surface of the console is the architecture."""
        scene = CONSOLE_SPACE.read_text(encoding="utf-8")
        self.assertIn("product-architecture.js", scene,
                      "the console does not read the stage table")
        self.assertIn("flow-arrows.js", scene,
                      "the console does not read the shared arrows")
        self.assertIn("layoutProductArchitecture", scene,
                      "the console does not lay the tools out in stages")
        self.assertIn("createFlowArrows", scene,
                      "the console draws no arrow between the stages")
        self.assertNotIn("goldenAngle", scene,
                         "the console still lays the tools out on a spiral")

    def test_the_console_keeps_the_unity_gestures(self):
        """Section 9.1: the console drives the scene the way Unity does."""
        scene = CONSOLE_SPACE.read_text(encoding="utf-8")
        self.assertIn("unity-controls.js", scene,
                      "the console does not use the shared gesture mapping")
        self.assertIn("new UnityControls", scene,
                      "the console does not drive the camera with the shared controller")
        self.assertNotIn("addEventListener('keydown'", scene,
                         "the console listens to the keys itself: the mapping is "
                         "the one of Unity and it lives in one module")

    def test_the_artefacts_ship_the_arrows_and_the_cuts(self):
        """Section 13.4: the produced file carries the same preset."""
        service = SERVICE_PY.read_text(encoding="utf-8")
        for name in ("flow-arrows.js", "product-architecture.js"):
            with self.subTest(name=name):
                self.assertIn(name, service,
                              f"the produced file does not carry {name}")
        self.assertTrue(FLOW_ARROWS.is_file(),
                        "the shared arrow module is missing from the runtime")
        self.assertTrue(PRODUCT_ARCHITECTURE.is_file(),
                        "the shared stage table is missing from the runtime")

    def test_the_console_route_carries_the_same_modules(self):
        """Section 13.4: the console serves the shared modules, not its own."""
        api = (REPO_ROOT / "api" / "index.py").read_text(encoding="utf-8")
        for name in ("flow-arrows.js", "product-architecture.js"):
            with self.subTest(name=name):
                self.assertIn(name, api,
                              f"the console route does not serve {name}")

    def test_the_arrow_head_is_the_ink_and_not_the_paper(self):
        """Section 4.9: the cone is ink.

        The scene is drawn on paper, so a paper-white cone would be the same
        colour as the ground: the direction of the flow would be lost.
        """
        source = self._source()
        policy = self._policy()
        self.assertIn("headColor === undefined ? PALETTE.ink", source,
                      "the head of the arrow is not the ink of the palette")
        self.assertIn("pulseColor === undefined ? PALETTE.ink", source,
                      "the bead that walks the shaft is not the ink of the palette")
        self.assertNotIn("headColor === undefined ? PALETTE.panel", source,
                         "the head of the arrow is paper white: it would vanish "
                         "into the paper")
        self.assertIn("锥头取 `--gem-ink`", policy,
                      "the policy does not say that the cone is the ink")
        self.assertIn("锥头不得用纸白", policy,
                      "the policy does not forbid the paper-white cone")

    def test_the_whole_arrow_is_a_transparent_line(self):
        """Section 4.9: the arrow is a line, so it is drawn transparent.

        An opaque object is drawn into the transmission buffer of the scene,
        and a stone would then refract the head of its own arrow: the gem turns
        black. The shaft, the head and the bead are all transparent.
        """
        source = self._source()
        policy = self._policy()
        self.assertEqual(source.count("transparent: true"), 3,
                         "the shaft, the head and the bead are all drawn as "
                         "transparent lines")
        self.assertIn("整个箭头都是线", policy,
                      "the policy does not say that the arrow is a line")
        self.assertIn("折射缓冲", policy,
                      "the policy does not say why the arrow may not be opaque")

    def test_the_arrow_stops_at_the_surface_of_the_stone(self):
        """Section 4.9: the cone is not buried inside the stone."""
        scene = CONSOLE_SPACE.read_text(encoding="utf-8")
        axis = AXIS_VIEWER.read_text(encoding="utf-8")
        self.assertIn("surfacePoint", axis,
                      "the public scene does not stop the arrow at the stone")
        policy = self._policy()
        self.assertIn("箭头停在石头表面", policy,
                      "the policy does not say that the arrow stops at the stone")

    def test_the_policy_writes_the_architecture_down(self):
        """Sections 4.9 and 12.4: the document says what the code does."""
        policy = self._policy()
        for marker in ("### 4.9 架构箭头",
                       "### 12.4 后台三维场景（控制台）：画出产品自己的架构",
                       "### 13.6 产品架构阶段表", "### 13.7 箭头参数表"):
            with self.subTest(marker=marker):
                self.assertIn(marker, policy,
                              f"the policy does not have the section: {marker}")
        self.assertIn("动态箭头", policy,
                      "the policy does not say that the arrows move")
        self.assertIn("Unity 的那九行", policy,
                      "the policy does not bind the gestures to Unity")


if __name__ == "__main__":
    unittest.main()
