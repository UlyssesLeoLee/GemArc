"""gemarc_mcp.atlas.engine: the Atlas simulation and causal engine (pure functions, standard library only).

Public surface (details and result shapes in ``engine/README.md``)::

    index_model(model)                          lookup tables over an expanded gemarc-atlas/1 model
    compile_deltas(index, deltas)               validate and compile interventions (OK / DRAFT / INVALID)
    simulate(model, case, deltas, opts)         one deterministic run (4D); the same call with deltas is the 5D run B
    seek / fold_state                           node states at a trace index
    compare_runs(A, B)                          outcome class, first divergence, terminal diff, roles of evidence
    run_causal(model, case_id, deltas, opts)    baseline + intervention + comparison + explanation
    causal_chain(model, result, iv, ix)         the traceable chain (steps, parents, effects, design links)
    align_traces / chain_lines / check_prediction
    eval_guard / eval_when                      the closed guard interpreter
"""
from .causal import (LOCALES, align_traces, causal_chain, cause_text, chain_lines, check_prediction, classify, revealed_steps)
from .compare import compare_runs, explain, run_causal
from .deltas import Compiled, Index, compile_deltas, flatten_deltas, index_model, validate_element, validate_rewrite
from .guards import eval_guard, eval_when, normalize_when
from .sim import canonical, event_key, fnv32, fold_state, seek, simulate, trace_hash
from .types import DEFAULT_POLICY, AtlasEngineError, cause

__all__ = [
    "LOCALES", "align_traces", "causal_chain", "cause_text", "chain_lines", "check_prediction", "classify", "revealed_steps",
    "compare_runs", "explain", "run_causal", "Compiled", "Index", "compile_deltas", "flatten_deltas", "index_model",
    "validate_element", "validate_rewrite", "eval_guard", "eval_when", "normalize_when", "canonical", "event_key", "fnv32",
    "fold_state", "seek", "simulate", "trace_hash", "DEFAULT_POLICY", "AtlasEngineError", "cause",
]
