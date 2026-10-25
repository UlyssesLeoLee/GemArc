"""Bounded deterministic rule-driven 4D timeline / 5D intervention simulator."""
from __future__ import annotations
import heapq
import itertools
from .paths import GemArcError
from .model import validate

MAX_EVENTS = 512
MAX_TIME_MS = 3_600_000


def simulate(model: dict, scenario_id: str, disabled_nodes: list[str] | None = None) -> dict:
    check = validate(model)
    if check["status"] != "ok":
        raise GemArcError("GEMARC_MODEL_020", "模型校验失败，不可模拟")
    scenarios = {x["id"]: x for x in model.get("scenarios", [])}
    if scenario_id not in scenarios:
        raise GemArcError("GEMARC_DYNAMIC_006", "场景不存在")
    scenario = scenarios[scenario_id]
    ids = {node["id"] for node in model["nodes"]}
    disabled = set(disabled_nodes or [])
    if disabled - ids:
        raise GemArcError("GEMARC_DYNAMIC_007", "屏蔽的模块不存在")
    rules = scenario.get("rules", [])
    queue, counter = [], itertools.count()
    for event in scenario.get("events", []):
        heapq.heappush(queue, (event["timeMs"], next(counter), {
            "module": event["module"], "event": event["event"],
            "timeMs": event["timeMs"], "triggeredBy": None
        }))
    trace = []
    truncation = None
    while queue:
        if len(trace) >= MAX_EVENTS:
            truncation = "MAX_EVENTS"
            break
        tick, _, event = heapq.heappop(queue)
        if tick > MAX_TIME_MS:
            truncation = "MAX_TIME_MS"
            break
        event["status"] = "blocked" if event["module"] in disabled else "delivered"
        event["traceId"] = f"trace-{len(trace):04d}"
        trace.append(event)
        if event["status"] == "blocked":
            continue
        for rule in rules:
            if rule["source"] != event["module"] or rule["whenEvent"] != event["event"]:
                continue
            new_time = tick + rule.get("delayMs", 0)
            heapq.heappush(queue, (new_time, next(counter), {
                "module": rule["target"], "event": rule["emit"],
                "timeMs": new_time, "triggeredBy": event["traceId"]
            }))
    return {"status": "ok" if truncation is None else "truncated", "scenarioId": scenario_id,
            "evidenceClass": scenario.get("evidenceClass", "hypothetical"),
            "disabledNodes": sorted(disabled), "trace": trace, "truncation": truncation,
            "summary": {"delivered": sum(e["status"] == "delivered" for e in trace),
                        "blocked": sum(e["status"] == "blocked" for e in trace)}}


def compare(model: dict, scenario_id: str, disabled_nodes: list[str]) -> dict:
    baseline = simulate(model, scenario_id)
    counterfactual = simulate(model, scenario_id, disabled_nodes)
    # Derive comparison from actual execution traces rather than visual guesses.
    def observed(trace):
        return sorted({(e["module"], e["event"]) for e in trace if e["status"] == "delivered"})
    before, after = set(observed(baseline["trace"])), set(observed(counterfactual["trace"]))
    return {"status": "ok", "scenarioId": scenario_id,
            "baseline": baseline, "intervention": counterfactual,
            "lostEvents": [{"module": a, "event": b} for a, b in sorted(before - after)],
            "gainedEvents": [{"module": a, "event": b} for a, b in sorted(after - before)],
            "notice": "仅代表指定模型规则的干预计算结果；不是现实因果识别证明"}
