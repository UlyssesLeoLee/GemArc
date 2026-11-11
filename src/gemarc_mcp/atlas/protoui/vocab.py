"""The vocabulary of the interface family: widget kinds with their flags, screen kinds, frames, variant (state) kinds, emphasis,
triggers, alignments and the verdict words, plus the option names of every section.

Ids only: the zh-CN and en-US labels live in ``protoui/i18n/{zh-CN,en-US}.json`` (design A3 D-25), so this module holds no
natural-language text. ``UI_VOCAB_VERSION`` grows by one whenever an id is added or removed. The family has 55 vocabulary ids with
labels (31 widget kinds, 4 screen kinds, 3 frames, 6 variant kinds, 4 emphasis values, 7 triggers); alignments and verdict words
are ids only (a card shows them as written). Standard library only.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import rows

UI_VOCAB_VERSION = 1


@dataclass(frozen=True)
class WidgetKind:
    """One widget kind. ``container``: may be a parent; ``nav_source``: a navigation row may start from it; ``interactive``: takes input."""

    id: str
    container: bool = False
    nav_source: bool = False
    interactive: bool = False


_C, _N, _I = "C", "N", "I"
#: id -> flags. The order is the vocabulary order (containers first, then leaves).
_KIND_TABLE = (
    ("stack", _C), ("row", _C), ("grid", _C), ("card", _C + _N), ("form", _C), ("header", _C), ("footer", _C), ("sidebar", _C),
    ("toolbar", _C), ("tabs", _C), ("list", _C + _N),
    ("heading", ""), ("text", ""), ("image", _N), ("icon", _N), ("badge", ""), ("divider", ""), ("chart", ""), ("table", _N),
    ("input", _I), ("textarea", _I), ("select", _I), ("checkbox", _I), ("radio", _I), ("switch", _I), ("button", _N + _I),
    ("link", _N + _I), ("tab", _N + _I), ("menu", _N + _I), ("pagination", _I), ("custom", ""),
)
WIDGET_KINDS: dict[str, WidgetKind] = {k: WidgetKind(k, _C in f, _N in f, _I in f) for k, f in _KIND_TABLE}

CONTAINERS = frozenset(k for k, v in WIDGET_KINDS.items() if v.container)
NAV_SOURCES = frozenset(k for k, v in WIDGET_KINDS.items() if v.nav_source)
INTERACTIVE = frozenset(k for k, v in WIDGET_KINDS.items() if v.interactive)
#: Kinds that carry rows of data: a screen with one of them wants a loading or an empty variant (UX-203).
DATA_KINDS = frozenset({"table", "list", "chart"})

SCREEN_KINDS = ("page", "dialog", "sheet", "panel")
#: frame -> (width, height) of the canvas in pixels.
FRAMES = {"desktop": (1280, 800), "tablet": (834, 1112), "phone": (390, 844)}
VARIANT_KINDS = ("loading", "empty", "error", "success", "locked", "other")
EMPHASIS = ("primary", "secondary", "danger", "muted")
TRIGGERS = ("tap", "submit", "change", "auto", "back", "timeout", "key")
ALIGNS = ("start", "center", "end")
VERDICTS = ("conforms", "differs", "unchecked")
DIFF_KINDS = ("differs", "missing", "extra", "unverifiable")

DEFAULT_SCREEN_KIND = "page"
DEFAULT_FRAME = "desktop"
DEFAULT_VARIANT_KIND = "other"
DEFAULT_TRIGGER = "tap"
#: The variant every screen has; it is derived, an author never writes it.
DEFAULT_VARIANT = "default"

#: ``enum:<group>`` roles of the section table -> the ids of the group.
ENUMS: dict[str, tuple] = {
    "widgetKinds": tuple(WIDGET_KINDS), "screenKinds": SCREEN_KINDS, "frames": tuple(FRAMES), "variantKinds": VARIANT_KINDS,
    "emphasis": EMPHASIS, "triggers": TRIGGERS, "aligns": ALIGNS, "verdicts": VERDICTS, "diffKinds": DIFF_KINDS,
}
#: The groups whose ids carry a label in the i18n files (the 55 ids).
LABELLED_GROUPS = ("widgetKinds", "screenKinds", "frames", "variantKinds", "emphasis", "triggers")

#: Option names per section (one source: the section table of ``rows``).
OPTION_NAMES: dict[str, tuple] = {name: spec.option_names for name, spec in rows.SECTIONS.items()}

#: Names a screen id must not take (case-insensitive): Windows device names (a file of that name is not a file there) and the
#: names of the artifacts the build writes (``INDEX``, ``NAV``, ``START``, ``CARD``): UX-009.
DEVICE_NAMES = frozenset({"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)})
ARTIFACT_NAMES = frozenset({"index", "nav", "start", "card"})
#: The reserved local name of the root group of a screen (UX-010).
ROOT_LOCAL = "root"
#: Local names no widget may take (UX-010): ``root`` is the id of the root group of every SVG, ``state`` the id of its state band (``<screen>.state``).
RESERVED_LOCALS = (ROOT_LOCAL, "state")


def label_ids() -> tuple:
    """Every id that carries a label, as ``group.id`` keys (the keys of the ``vocab`` object of the i18n files)."""
    return tuple(f"{group}.{item}" for group in LABELLED_GROUPS for item in ENUMS[group])


def kind_flags(kind: str) -> WidgetKind:
    """The flags of a widget kind (a ``KeyError`` for an unknown kind: the expander has refused it before)."""
    return WIDGET_KINDS[kind]
