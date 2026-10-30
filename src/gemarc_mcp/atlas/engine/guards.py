"""Guard interpreter and ``when`` evaluator (closed interpreter: no ``eval``, no code is ever executed).

Semantic port of the reference guard rules (spec 7.5):

* guard: ``{param, op, [value]}`` with ``op`` in ``present absent eq neq in``; combinators ``all`` / ``any`` / ``not``;
  ``value`` may be ``"$ctx.<key>"`` and then comes from the case ``context``. An unknown operator is false.
* ``when``: ``{node, is}``, ``{node, not_in}`` (``not`` with an array on a node condition means "none of these"),
  ``all`` / ``any`` / ``not``, ``{elementState}`` or a parameter guard.

Both functions follow JavaScript truthiness and strict equality (see ``types``) so that unvalidated input behaves as
it did in the original; contract-valid input is unaffected.
"""
from __future__ import annotations

from typing import Any, Mapping

from .types import UNDEF, get, includes, js_string, strict_eq, truthy

CTX_PREFIX = "$ctx."


def _items(value):
    """The list a combinator iterates; anything that is not an array contributes nothing and the combinator is false."""
    return value if isinstance(value, list) else None


def eval_guard(guard, ctx: Mapping | None = None) -> bool:
    """``ctx`` keys: ``params`` (event parameters) and ``context`` (case context)."""
    ctx = ctx or {}
    if guard is None or guard is UNDEF:
        return True
    if not isinstance(guard, Mapping):
        return False
    if truthy(guard.get("all")):
        items = _items(guard["all"])
        return items is not None and all(eval_guard(x, ctx) for x in items)
    if truthy(guard.get("any")):
        items = _items(guard["any"])
        return items is not None and any(eval_guard(x, ctx) for x in items)
    if truthy(guard.get("not")):
        return not eval_guard(guard["not"], ctx)
    params = ctx.get("params")
    params = params if truthy(params) and isinstance(params, Mapping) else {}
    name = guard.get("param", UNDEF)
    v = params.get(name if isinstance(name, str) else js_string(name), UNDEF)
    raw = guard.get("value", UNDEF)
    if isinstance(raw, str) and raw.startswith(CTX_PREFIX):
        context = ctx.get("context")
        context = context if truthy(context) and isinstance(context, Mapping) else {}
        expect = context.get(raw[len(CTX_PREFIX):], UNDEF)
    else:
        expect = raw
    op = guard.get("op", UNDEF)
    if op == "present":
        return v is not UNDEF and v is not None and len(js_string(v)) > 0
    if op == "absent":
        return v is UNDEF or v is None or len(js_string(v)) == 0
    if op == "eq":
        return strict_eq(v, expect)
    if op == "neq":
        return not strict_eq(v, expect)
    if op == "in":
        return includes(expect, v)
    return False


def eval_when(when, ctx: Mapping) -> bool:
    """``ctx`` keys: ``state`` (live node states), ``params``, ``context`` and optionally ``elementState``."""
    if when is None or when is UNDEF:
        return True
    if not isinstance(when, Mapping):
        return False
    if truthy(when.get("all")):
        items = _items(when["all"])
        return items is not None and all(eval_when(x, ctx) for x in items)
    if truthy(when.get("any")):
        items = _items(when["any"])
        return items is not None and any(eval_when(x, ctx) for x in items)
    if truthy(when.get("not")):
        return not eval_when(when["not"], ctx)
    if truthy(when.get("node")):
        state = get(ctx.get("state"), when["node"] if isinstance(when["node"], str) else js_string(when["node"]))
        if truthy(when.get("is")):
            return includes(when["is"], state)
        if truthy(when.get("not_in")):
            return not includes(when["not_in"], state)
        return True
    if when.get("elementState", UNDEF) is not UNDEF:
        return strict_eq(ctx.get("elementState", UNDEF), when["elementState"])
    if truthy(when.get("param")):
        return eval_guard(when, ctx)
    return False


def normalize_when(when: Any) -> Any:
    """``not`` as an array on a node condition means "state is none of these" (rewritten to ``not_in``).

    Returns a new structure; the input is never modified.
    """
    if not isinstance(when, Mapping):
        return when
    if truthy(when.get("node")) and isinstance(when.get("not"), list):
        return {"node": when["node"], "not_in": when["not"]}
    out = dict(when)
    if truthy(when.get("all")) and isinstance(when["all"], list):
        out["all"] = [normalize_when(x) for x in when["all"]]
    if truthy(when.get("any")) and isinstance(when["any"], list):
        out["any"] = [normalize_when(x) for x in when["any"]]
    if truthy(when.get("not")) and not truthy(when.get("node")) and not isinstance(when["not"], list):
        out["not"] = normalize_when(when["not"])
    return out
