"""The three site shells must offer the same navigation.

The site is delivered in three shells: the ``static`` kit, the ``vite`` kit and
the ``mvp`` preview. They used to disagree - the ``static`` shell numbered the
view registry ``06`` and put the workflow in the ``UI`` slot, while the ``mvp``
preview numbered the workflow ``06``. A reader who learned one shell could not
find the same page in the other, and the dimension numbering did not match the
dimensions the model declares.

The rule the shells share now:

* the workflow is the sixth dimension (``06`` / 6D),
* the view registry follows it (``07``),
* the prototype is the ``UI`` slot, because it is not a dimension.

The guard below is a *parity* check, so it needs the existence checks as well:
three shells that agree on an empty navigation would pass a parity test alone.
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "templates"
SHARED = TEMPLATES / "shared"
WEB = REPO_ROOT / "src" / "gemarc_mcp" / "web"

NAV_BUTTON = re.compile(
    r'<button\s+data-tab="(?P<tab>[a-z0-9]+)"(?P<rest>[^>]*)>\s*'
    r'<span>(?P<slot>[^<]*)</span>\s*(?P<label>[^<]*)</button>')

SHELLS = {
    "static": TEMPLATES / "static" / "index.html",
    "vite": TEMPLATES / "vite" / "index.html",
    "mvp": WEB / "index.html",
}


def navigation(path: Path) -> list:
    """The navigation of a shell as ``(tab, slot, label)`` triples."""
    text = path.read_text(encoding="utf-8")
    return [
        (match.group("tab"), match.group("slot").strip(), match.group("label").strip())
        for match in NAV_BUTTON.finditer(text)
    ]


class NavigationTests(unittest.TestCase):
    def test_the_three_shells_offer_the_same_navigation(self):
        shells = {name: navigation(path) for name, path in SHELLS.items()}
        reference = shells["static"]
        self.assertTrue(reference, "the static shell has no navigation at all")
        for name, nav in shells.items():
            self.assertEqual(nav, reference, f"the {name} shell drifted from the static one")

    def test_the_workflow_is_the_sixth_dimension(self):
        nav = {tab: (slot, label) for tab, slot, label in navigation(SHELLS["static"])}
        self.assertEqual(nav["workflow"][0], "06")
        self.assertIn("6D", nav["workflow"][1])
        self.assertEqual(nav["views"][0], "07")
        self.assertEqual(nav["prototype"][0], "UI")
        self.assertIn("原型", nav["prototype"][1])

    def test_every_tab_of_the_kit_has_a_view(self):
        """A navigation button without a view model is a dead button."""
        app = (SHARED / "app.js").read_text(encoding="utf-8")
        for tab, _, _ in navigation(SHELLS["static"]):
            self.assertIn(f"case '{tab}'", app, f"the kit has no view for the tab {tab}")

    def test_every_tab_of_the_preview_has_a_renderer(self):
        app = (WEB / "app.js").read_text(encoding="utf-8")
        for tab, _, _ in navigation(SHELLS["mvp"]):
            self.assertIn(f"tab==='{tab}'", app, f"the preview has no renderer for the tab {tab}")

    def test_the_kit_knows_the_prototype_view(self):
        model = (SHARED / "view-model.js").read_text(encoding="utf-8")
        self.assertIn("export function buildPrototypeView", model)


if __name__ == "__main__":
    unittest.main()
