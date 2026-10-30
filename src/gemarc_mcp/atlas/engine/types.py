"""Constants, structured causes and the small JavaScript-semantics helpers of the Atlas engine.

The engine is a semantic port of a scripted discrete-event simulator whose original ran on JavaScript. Where the
original relied on JavaScript truthiness, ``===`` or ``String(v)`` the port uses the helpers below instead of the
Python operators, so that a value which behaves one way in JavaScript does not silently behave another way here
(``[]`` is truthy, ``true === 1`` is false, ``String([])`` is the empty string).

Everything here is pure: no I/O, no clock, no randomness.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from .. import model as am

#: The single policy default of the engine (the reference carried two copies); it IS the contract constant.
DEFAULT_POLICY = am.DEFAULT_POLICY
#: Engine limit on trace length; a run that reaches it stops with status ``LIMIT_EXCEEDED``.
DEFAULT_MAX_EVENTS = 1000
#: Largest ``change_retry.retryMax`` the engine compiles (a hard bound; the retry loop also stops at ``maxEvents``).
MAX_RETRY = 1000

# Lifecycle vocabulary (checked against ``profiles/software.json`` by the behaviour tests).
ACTIVE_STATES = frozenset({"Running", "Waiting", "Completed", "Recovered", "Degraded"})
DELIVERY = frozenset({"Call", "Message"})
HANDLER_ACTIONS = ("forward", "respond", "drop", "fail")
FAILURE_OUTCOMES = ("Blocked", "Denied", "Failed", "Timeout")

ENGINE_ERROR_UNKNOWN_CASE = "GEMARC_ATLAS_ENGINE_001"
ENGINE_ERROR_BAD_ARGUMENT = "GEMARC_ATLAS_ENGINE_002"
ENGINE_ERROR_CONTRACT = "GEMARC_ATLAS_ENGINE_003"


class AtlasEngineError(ValueError):
    """The engine was called with something it cannot run (an unknown case id, a bad option)."""

    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(f"{code}: {message}")


def contract_guard(fn):
    """Public entry point wrapper: an unexpected error caused by input that violates the model contract becomes a coded
    ``AtlasEngineError`` (``GEMARC_ATLAS_ENGINE_003``). The engine assumes ``model.contract_issues(model) == []``; callers
    must gate on it first. ``AtlasEngineError`` itself passes through unchanged."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except AtlasEngineError:
            raise
        except (TypeError, KeyError, ValueError, AttributeError, IndexError, OverflowError, RecursionError) as ex:
            raise AtlasEngineError(
                ENGINE_ERROR_CONTRACT,
                f"input violates the model contract (run model.contract_issues first): {type(ex).__name__}: {ex}") from ex
    return wrapper


def check_run_options(opts: Mapping) -> None:
    """Validate the numeric options of a run (``interventionTime``: finite number; ``maxEvents``: finite number >= 0; an
    infinite ``maxEvents`` would leave the retry loop unbounded)."""
    value = opts.get("interventionTime")
    if value is not None and not (is_number(value) and math.isfinite(value)):
        raise AtlasEngineError(ENGINE_ERROR_BAD_ARGUMENT, "opts.interventionTime must be a finite number")
    value = opts.get("maxEvents")
    if value is not None and not (is_number(value) and math.isfinite(value) and value >= 0):
        raise AtlasEngineError(ENGINE_ERROR_BAD_ARGUMENT, "opts.maxEvents must be a finite number >= 0")


# --------------------------------------------------------------------------------------------------------
# Structured causes (recorded divergence 2)
# --------------------------------------------------------------------------------------------------------

# Cause codes recorded on a trace entry. ``args`` carries exactly what the code needs; nothing is parsed from text.
C_MODULE_DISABLED = "module_off"            # args: nodeId
C_EDGE_DISABLED = "edge_off"
C_NO_PENDING = "no_open_request"
C_SOURCE_STATE = "source_state"                  # args: state (the state that stops the source from emitting)
C_DUPLICATE = "duplicate"
C_AUTHORED_FAULT = "declared_fault"
C_RESOURCE_FAILURE = "resource_failure"          # injected by a resource_failure intervention
C_IDENTITY_FAILURE = "identity_failure"          # injected by an identity_failure intervention
C_GUARD = "guard"                                # event-level guard rejected the event
C_NO_TRANSITION = "no_such_transition"
C_TRANSITION_GUARD = "transition_guard"          # args: transitionId
C_LATENCY_TIMEOUT = "latency_over_timeout"    # args: latencyMs, timeoutMs
C_TIMEOUT_EXHAUSTED = "retries_used_up"
C_REPORTED_FAILURE = "failure_reported"
C_DEGRADED = "sections_missing"                          # args: sections (sorted by arrival, ids of the failed sections)
C_WHEN_FALSE = "condition_false"
C_NO_FORMAL_EDGE = "no_runtime_edge"
C_UNSUPPORTED_TYPE = "unsupported_type"          # args: eventType
C_ELEMENT_RESPOND = "element_answered"
C_ELEMENT_DROP = "element_dropped"
C_ELEMENT_FAIL = "element_failed"
C_ANSWERED_BY_ELEMENT = "already_answered"

CAUSE_CODES = (
    C_MODULE_DISABLED, C_EDGE_DISABLED, C_NO_PENDING, C_SOURCE_STATE, C_DUPLICATE, C_AUTHORED_FAULT,
    C_RESOURCE_FAILURE, C_IDENTITY_FAILURE, C_GUARD, C_NO_TRANSITION, C_TRANSITION_GUARD, C_LATENCY_TIMEOUT,
    C_TIMEOUT_EXHAUSTED, C_REPORTED_FAILURE, C_DEGRADED, C_WHEN_FALSE, C_NO_FORMAL_EDGE, C_UNSUPPORTED_TYPE,
    C_ELEMENT_RESPOND, C_ELEMENT_DROP, C_ELEMENT_FAIL, C_ANSWERED_BY_ELEMENT,
)


def cause(code: str, **args: Any) -> dict:
    """A structured cause ``{code, args}``; ``args`` is always an object (empty when the code needs none)."""
    return {"code": code, "args": args}


# --------------------------------------------------------------------------------------------------------
# JavaScript semantics
# --------------------------------------------------------------------------------------------------------


class _Undefined:
    """The JavaScript ``undefined`` (a missing key); distinct from ``None`` (JSON ``null``)."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNDEF"

    def __bool__(self) -> bool:  # defensive: UNDEF is never a usable truth value in the engine
        return False

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


UNDEF = _Undefined()


def is_number(value) -> bool:
    """``typeof value === 'number'`` for JSON data: ints and floats, not booleans."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def truthy(value) -> bool:
    """JavaScript truthiness: ``[]`` and ``{}`` are truthy; ``0``, ``""``, ``null``, ``undefined``, ``NaN`` are not."""
    if value is UNDEF or value is None or value is False:
        return False
    if value is True:
        return True
    if is_number(value):
        return value != 0 and not (isinstance(value, float) and math.isnan(value))
    if isinstance(value, str):
        return len(value) > 0
    return True


def get(obj, key, default=UNDEF):
    """``obj[key]`` of a JSON object, ``UNDEF`` when ``obj`` is not an object or lacks the key."""
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return default


def nullish(value, fallback):
    """``value ?? fallback``: only ``null`` and ``undefined`` fall through (``0`` and ``""`` do not)."""
    return fallback if value is None or value is UNDEF else value


def strict_eq(a, b) -> bool:
    """``a === b`` for JSON values: no coercion, ``true !== 1``, arrays and objects are never equal."""
    if isinstance(a, (list, dict)) or isinstance(b, (list, dict)):
        return False
    if a is UNDEF or b is UNDEF:
        return a is b
    if a is None or b is None:
        return a is b
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if is_number(a) and is_number(b):
        return a == b
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    return False


def includes(sequence, value) -> bool:
    """``Array.prototype.includes`` with strict equality; a non-array never includes anything."""
    return isinstance(sequence, list) and any(strict_eq(item, value) for item in sequence)


def js_string(value) -> str:
    """``String(value)`` for JSON values (also what a template literal prints)."""
    if value is UNDEF:
        return "undefined"
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        if value == int(value) and abs(value) < 1e21:
            return str(int(value))
        return repr(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ",".join("" if item is None or item is UNDEF else js_string(item) for item in value)
    return "[object Object]"


def as_number(value) -> float:
    """A number for ordering comparisons; anything that is not a JSON number compares false (``NaN``)."""
    return value if is_number(value) else math.nan


def num_gt(value, bound) -> bool:
    return as_number(value) > bound


def num_ge(value, bound) -> bool:
    return as_number(value) >= bound


def plain(value):
    """A JSON-safe deep copy (tuples become lists, mappings become dicts, floats with integer values stay as they are)."""
    if isinstance(value, Mapping):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value
