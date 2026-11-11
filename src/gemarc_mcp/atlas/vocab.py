"""Atlas-owned vocabulary: loads ``profiles/<name>.json`` and exposes read-only indexes.

The atlas vocabulary is deliberately NOT stored under ``scaffold/vocab``: that directory is pinned by the v1
contract (digest, generated JS, byte-compared demo site). Ids are shared with the canonical GemArc vocabulary
wherever a canonical id exists; ``tests/atlas/test_model_contract.py`` enforces this and lists the additive
ids explicitly. Standard library only.
"""
from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

PROFILE_DIR = Path(__file__).resolve().parent / "profiles"
PROFILE_SCHEMA = "gemarc-atlas-profile/1"
DEFAULT_PROFILE = "software"
LOCALES = ("zh-CN", "ja-JP", "en-US")
FALLBACK_LOCALE = "en-US"

#: Groups whose items are ``{"id": ..., "label": {locale: text}}`` (all locales required).
LABEL_GROUPS = (
    "verification", "sourceKinds", "trust", "domains", "tiers", "layers", "levels", "kinds", "relations",
    "views", "caseCategories", "eventTypes", "interventionTypes", "outcomeClasses", "nodeStates",
    "eventOutcomes", "elementKinds", "policyKinds",
)
SHAPES = ("hex", "tri", "box", "diamond", "octa", "round", "crown")
RELATION_CATEGORIES = ("structure", "behavior", "data", "flow", "constraint")
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class VocabError(ValueError):
    """The profile file is missing or malformed."""


def _blocked(name: str):
    def method(self, *args, **kwargs):
        raise TypeError(f"the shared vocabulary profile is read-only ({name}); take a copy with copy.deepcopy first")

    method.__name__ = name
    return method


class FrozenDict(dict):
    """A ``dict`` (so ``json.dumps`` and ``==`` behave as usual) that refuses every mutation.

    ``copy.copy`` and ``copy.deepcopy`` return plain mutable dicts, so ``copy.deepcopy(profile.item(...))`` is the way
    to get a private, editable copy.
    """

    __slots__ = ()

    def __copy__(self):
        return dict(self)

    def __deepcopy__(self, memo):
        return {key: copy.deepcopy(value, memo) for key, value in self.items()}

    def __reduce__(self):
        return (dict, (dict(self),))


class FrozenList(list):
    """The list counterpart of ``FrozenDict`` (equal to a plain list with the same items)."""

    __slots__ = ()

    def __copy__(self):
        return list(self)

    def __deepcopy__(self, memo):
        return [copy.deepcopy(value, memo) for value in self]

    def __reduce__(self):
        return (list, (list(self),))


for _name in ("__setitem__", "__delitem__", "update", "pop", "popitem", "clear", "setdefault", "__ior__"):
    setattr(FrozenDict, _name, _blocked(_name))
for _name in ("__setitem__", "__delitem__", "append", "extend", "insert", "remove", "pop", "clear", "sort", "reverse", "__iadd__", "__imul__"):
    setattr(FrozenList, _name, _blocked(_name))


def freeze(value):
    """Recursive read-only copy: dicts become ``FrozenDict``, lists ``FrozenList``; everything else is returned as is."""
    if isinstance(value, dict):
        return FrozenDict((key, freeze(item)) for key, item in value.items())
    if isinstance(value, list):
        return FrozenList(freeze(item) for item in value)
    return value


def profile_issues(data) -> list[str]:
    """Return human-readable problems of a profile dict (empty list = well-formed)."""
    issues: list[str] = []
    if not isinstance(data, dict):
        return ["profile is not an object"]
    if data.get("schema") != PROFILE_SCHEMA:
        issues.append(f"schema must be {PROFILE_SCHEMA!r}")
    if not isinstance(data.get("id"), str) or not data.get("id"):
        issues.append("id must be a non-empty string")
    if tuple(data.get("locales") or ()) != LOCALES:
        issues.append(f"locales must be exactly {list(LOCALES)}")
    ids: dict[str, list[str]] = {}
    for group in LABEL_GROUPS:
        items = data.get(group)
        if not isinstance(items, list) or not items:
            issues.append(f"{group}: must be a non-empty list")
            ids[group] = []
            continue
        seen: list[str] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                issues.append(f"{group}[{index}]: needs a string id")
                continue
            if item["id"] in seen:
                issues.append(f"{group}: duplicate id {item['id']!r}")
            seen.append(item["id"])
            label = item.get("label")
            if not isinstance(label, dict):
                issues.append(f"{group}[{item['id']}]: label must be an object")
                continue
            for locale in LOCALES:
                text = label.get(locale)
                if not isinstance(text, str) or not text.strip():
                    issues.append(f"{group}[{item['id']}]: label.{locale} missing")
            for extra in set(label) - set(LOCALES):
                issues.append(f"{group}[{item['id']}]: unknown locale {extra!r}")
        ids[group] = seen

    levels = ids.get("levels", [])
    kinds = ids.get("kinds", [])
    domains = ids.get("domains", [])
    views = ids.get("views", [])
    verification = data.get("verification") or []
    ranks = [item.get("rank") for item in verification if isinstance(item, dict)]
    if ranks != sorted(ranks, reverse=True) or len(set(ranks)) != len(ranks) or ranks != list(range(len(ranks), 0, -1)):
        issues.append("verification: ranks must be len..1 in strictly descending (GemArc rank) order")
    for item in data.get("sourceKinds") or []:
        if isinstance(item, dict) and item.get("verification") not in ids.get("verification", []):
            issues.append(f"sourceKinds[{item.get('id')}]: unknown verification")
    doc_ext = data.get("docExt")
    if not isinstance(doc_ext, list) or not doc_ext or any(
        not isinstance(x, str) or not x.startswith(".") or x != x.lower() or len(x) < 2 for x in doc_ext
    ) or len(set(doc_ext)) != len(doc_ext):
        issues.append("docExt: must be unique lower-case extensions starting with '.'")
    for item in data.get("trust") or []:
        if isinstance(item, dict) and item.get("domain") not in domains:
            issues.append(f"trust[{item.get('id')}]: unknown domain")
    for item in data.get("domains") or []:
        if isinstance(item, dict) and not (isinstance(item.get("color"), str) and _HEX_COLOR_RE.match(item["color"])):
            issues.append(f"domains[{item.get('id')}]: color must be #rrggbb")
    for item in data.get("tiers") or []:
        if isinstance(item, dict) and not isinstance(item.get("rank"), int):
            issues.append(f"tiers[{item.get('id')}]: rank must be an int")
    for item in data.get("levels") or []:
        if isinstance(item, dict) and not isinstance(item.get("rank"), int):
            issues.append(f"levels[{item.get('id')}]: rank must be an int")
    for item in data.get("layers") or []:
        if not isinstance(item, dict):
            continue
        if item.get("domain") not in domains:
            issues.append(f"layers[{item.get('id')}]: unknown domain")
        if item.get("level") not in levels:
            issues.append(f"layers[{item.get('id')}]: unknown level")
        if not isinstance(item.get("levelKinds"), list) or not set(item["levelKinds"]) <= set(kinds):
            issues.append(f"layers[{item.get('id')}]: levelKinds must be known kinds")
    for item in data.get("kinds") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("id")
        if item.get("shape") not in SHAPES:
            issues.append(f"kinds[{name}]: shape must be one of {list(SHAPES)}")
        scale = item.get("scale")
        if isinstance(scale, bool) or not isinstance(scale, (int, float)) or scale <= 0:
            issues.append(f"kinds[{name}]: scale must be a positive number")
        if item.get("level") not in levels:
            issues.append(f"kinds[{name}]: unknown level")
        if item.get("domain") is not None and item.get("domain") not in domains:
            issues.append(f"kinds[{name}]: unknown domain")
        for flag in ("record", "authorable"):
            if not isinstance(item.get(flag), bool):
                issues.append(f"kinds[{name}]: {flag} must be a bool")
        if isinstance(item.get("lane"), bool) or not isinstance(item.get("lane"), int):
            issues.append(f"kinds[{name}]: lane must be an int")
    for item in data.get("relations") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("id")
        if item.get("category") not in RELATION_CATEGORIES:
            issues.append(f"relations[{name}]: category must be one of {list(RELATION_CATEGORIES)}")
        if not isinstance(item.get("runtime"), bool) or not isinstance(item.get("static"), bool):
            issues.append(f"relations[{name}]: runtime and static must be bools")
        elif item["runtime"] == item["static"]:
            issues.append(f"relations[{name}]: static must be the complement of runtime")
        if not isinstance(item.get("trustBoundary"), bool):
            issues.append(f"relations[{name}]: trustBoundary must be a bool")
        if item.get("level") not in levels:
            issues.append(f"relations[{name}]: unknown level")
        rel_views = item.get("views")
        if not isinstance(rel_views, list) or not rel_views or not set(rel_views) <= set(views):
            issues.append(f"relations[{name}]: views must be a non-empty subset of the view ids")
        if not (isinstance(item.get("color"), str) and _HEX_COLOR_RE.match(item["color"])):
            issues.append(f"relations[{name}]: color must be #rrggbb")
    for item in data.get("views") or []:
        if isinstance(item, dict) and item.get("kinds") is not None:
            if not isinstance(item["kinds"], list) or not set(item["kinds"]) <= set(kinds):
                issues.append(f"views[{item.get('id')}]: kinds must be null or known kinds")
    for item in data.get("eventTypes") or []:
        if isinstance(item, dict) and not isinstance(item.get("delivery"), bool):
            issues.append(f"eventTypes[{item.get('id')}]: delivery must be a bool")
    for item in data.get("nodeStates") or []:
        if isinstance(item, dict) and not isinstance(item.get("active"), bool):
            issues.append(f"nodeStates[{item.get('id')}]: active must be a bool")
    for item in data.get("interventionTypes") or []:
        if not isinstance(item, dict):
            continue
        for key in ("required", "optional"):
            value = item.get(key)
            if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
                issues.append(f"interventionTypes[{item.get('id')}]: {key} must be a list of strings")
        if not isinstance(item.get("atomic"), bool):
            issues.append(f"interventionTypes[{item.get('id')}]: atomic must be a bool")
    enums = data.get("enums")
    if not isinstance(enums, dict) or not enums:
        issues.append("enums: must be a non-empty object")
    else:
        for key, values in enums.items():
            if not isinstance(values, list) or not values or any(not isinstance(x, str) for x in values) or len(set(values)) != len(values):
                issues.append(f"enums.{key}: must be a non-empty list of unique strings")
    if not isinstance(data.get("knownProps"), dict):
        issues.append("knownProps: must be an object")
    aliases = data.get("aliases")
    if not isinstance(aliases, dict):
        issues.append("aliases: must be an object")
    else:
        for group, mapping in aliases.items():
            if group not in LABEL_GROUPS or not isinstance(mapping, dict):
                issues.append(f"aliases.{group}: must map onto a label group")
                continue
            for old, new in mapping.items():
                if new not in ids.get(group, []) or old in ids.get(group, []):
                    issues.append(f"aliases.{group}.{old}: must point at a known id and not shadow one")
    defaults = data.get("defaults")
    if not isinstance(defaults, dict) or defaults.get("trust") not in ids.get("trust", []):
        issues.append("defaults.trust must be a known trust id")
    return issues


class Profile:
    """Read-only view of a profile dict with id indexes.

    ``raw`` and every item returned by ``item`` are deep-frozen (``FrozenDict`` / ``FrozenList``): the profile is
    cached and shared by the whole process, so a caller that edits an item would change every later result.
    """

    def __init__(self, data: dict):
        data = freeze(data)
        self.raw = data
        self.id = data["id"]
        self.locales = tuple(data["locales"])
        self._index = {group: {item["id"]: item for item in data[group]} for group in LABEL_GROUPS}
        self.enums = MappingProxyType({key: tuple(values) for key, values in data["enums"].items()})
        self.known_props = MappingProxyType(dict(data["knownProps"]))
        self.default_trust = data["defaults"]["trust"]
        self.doc_ext = frozenset(data["docExt"])
        self.verification_order = tuple(item["id"] for item in data["verification"])  # strongest first
        self.verification_rank = MappingProxyType({item["id"]: item["rank"] for item in data["verification"]})
        self.record_kinds = frozenset(item["id"] for item in data["kinds"] if item["record"])
        self.static_relations = frozenset(item["id"] for item in data["relations"] if item["static"])
        self.runtime_relations = frozenset(item["id"] for item in data["relations"] if item["runtime"])
        self.trust_boundary_relations = frozenset(item["id"] for item in data["relations"] if item["trustBoundary"])
        self.trust_order = tuple(item["id"] for item in data["trust"])
        self.delivery_types = frozenset(item["id"] for item in data["eventTypes"] if item["delivery"])
        self.active_states = frozenset(item["id"] for item in data["nodeStates"] if item["active"])
        self.aliases = MappingProxyType({group: MappingProxyType(dict(m)) for group, m in data["aliases"].items()})

    def ids(self, group: str) -> tuple[str, ...]:
        return tuple(self._index[group])

    def has(self, group: str, value) -> bool:
        return isinstance(value, str) and value in self._index[group]

    def item(self, group: str, value):
        return self._index[group].get(value) if isinstance(value, str) else None

    def alias(self, group: str, value):
        """Canonical id for a legacy spelling (or None)."""
        return self.aliases.get(group, {}).get(value)

    def label(self, group: str, value: str, locale: str = FALLBACK_LOCALE) -> str:
        item = self.item(group, value)
        if item is None:
            return value
        labels = item["label"]
        return labels.get(locale) or labels.get(FALLBACK_LOCALE) or value

    def rank(self, level) -> int:
        """Strength of a verification level, 6 (Implemented) .. 1 (Unknown); 0 = not a level."""
        return self.verification_rank.get(level, 0) if isinstance(level, str) else 0

    def known_ids(self) -> dict[str, tuple[str, ...]]:
        return {group: tuple(self._index[group]) for group in LABEL_GROUPS}


@lru_cache(maxsize=None)
def load_profile(name: str = DEFAULT_PROFILE) -> Profile:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", name or ""):
        raise VocabError(f"invalid profile name {name!r}")
    path = PROFILE_DIR / f"{name}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        raise VocabError(f"cannot read profile {name!r}: {ex}") from ex
    problems = profile_issues(data)
    if problems:
        raise VocabError(f"profile {name!r} is malformed: " + "; ".join(problems[:5]))
    return Profile(data)


def default_profile() -> Profile:
    return load_profile(DEFAULT_PROFILE)
