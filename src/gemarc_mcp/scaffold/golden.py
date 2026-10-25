"""
Golden simulation cases: the contract between the Python and JavaScript engines.

The 4D/5D simulator exists twice on purpose - once in Python (the authority,
used by the MCP tools) and once in JavaScript (so the browser can really
compute the animation instead of replaying a precomputed trace). Two
implementations of the same semantics will drift unless something pins them
down. These golden cases are that pin: each case stores a model, an
intervention and the exact expected trace, and both engines must reproduce it.

Regenerate with:
    python -m gemarc_mcp.scaffold.golden --write
"""
from __future__ import annotations

import json
from pathlib import Path

from ..simulation import compare, simulate

CASES_DIR = Path(__file__).resolve().parent / "cases"


def _node(ident: str, name: str) -> dict:
    return {"id": ident, "name": name, "kind": "Service", "layer": "应用层",
            "verification": "illustrative", "evidence": "示例"}


def _edge(ident: str, source: str, target: str) -> dict:
    return {"id": ident, "sourceId": source, "targetId": target, "type": "sends",
            "description": "事件投递", "verification": "illustrative", "evidence": "示例"}


def _model(nodes, edges, scenarios) -> dict:
    return {
        "meta": {"system": "golden", "schemaVersion": "0.2.0", "language": "zh-CN",
                 "evidenceState": "Hypothetical", "description": "黄金用例模型"},
        "nodes": nodes, "edges": edges,
        "views": [{"id": "logical", "title": "逻辑架构", "diagramType": "logical-view",
                   "concerns": ["事件依赖"], "nodeIds": [n["id"] for n in nodes],
                   "edgeKinds": ["sends"]}],
        "scenarios": scenarios, "cases": [], "workflows": [], "prototypes": [],
    }


def build_cases() -> list[dict]:
    cases: list[dict] = []

    # 1. A plain linear chain: sensor -> queue -> rule -> notify.
    chain_nodes = [_node("sensor", "事件源"), _node("queue", "消息队列"),
                   _node("rule", "规则引擎"), _node("notify", "通知服务")]
    chain_edges = [_edge("e1", "sensor", "queue"), _edge("e2", "queue", "rule"),
                   _edge("e3", "rule", "notify")]
    chain_rules = [
        {"source": "sensor", "whenEvent": "emit", "target": "queue", "emit": "ingest", "delayMs": 10},
        {"source": "queue", "whenEvent": "ingest", "target": "rule", "emit": "evaluate", "delayMs": 20},
        {"source": "rule", "whenEvent": "evaluate", "target": "notify", "emit": "send", "delayMs": 10},
    ]
    chain_model = _model(chain_nodes, chain_edges, [{
        "id": "normal", "name": "正常事件传播", "evidenceClass": "hypothetical",
        "events": [{"module": "sensor", "event": "emit", "timeMs": 0}],
        "rules": chain_rules,
    }])
    cases.append({"id": "linear", "description": "四段线性链，按 delayMs 依次送达",
                  "model": chain_model, "scenarioId": "normal", "disabledNodeIds": []})
    cases.append({"id": "linear-blocked-middle", "description": "屏蔽中间模块，下游全部阻断",
                  "model": chain_model, "scenarioId": "normal", "disabledNodeIds": ["queue"]})
    cases.append({"id": "linear-blocked-source", "description": "屏蔽事件源，全链阻断",
                  "model": chain_model, "scenarioId": "normal", "disabledNodeIds": ["sensor"]})

    # 2. Same timestamp: the insertion order must decide, not the node order.
    tie_nodes = [_node("a", "A"), _node("b", "B"), _node("c", "C")]
    tie_edges = [_edge("e1", "a", "b"), _edge("e2", "b", "c")]
    tie_model = _model(tie_nodes, tie_edges, [{
        "id": "tie", "name": "同刻事件", "evidenceClass": "hypothetical",
        "events": [{"module": "a", "event": "first", "timeMs": 0},
                   {"module": "b", "event": "second", "timeMs": 0}],
        "rules": [{"source": "b", "whenEvent": "second", "target": "c", "emit": "third", "delayMs": 0}],
    }])
    cases.append({"id": "tie-break", "description": "两个事件同一毫秒，按插入顺序处理",
                  "model": tie_model, "scenarioId": "tie", "disabledNodeIds": []})

    # 3. One event fanning out into several rules.
    fan_nodes = [_node("src", "源"), _node("x", "X"), _node("y", "Y"), _node("z", "Z")]
    fan_edges = [_edge("e1", "src", "x"), _edge("e2", "src", "y"), _edge("e3", "x", "z")]
    fan_model = _model(fan_nodes, fan_edges, [{
        "id": "fan", "name": "扇出", "evidenceClass": "hypothetical",
        "events": [{"module": "src", "event": "emit", "timeMs": 0}],
        "rules": [
            {"source": "src", "whenEvent": "emit", "target": "x", "emit": "goX", "delayMs": 5},
            {"source": "src", "whenEvent": "emit", "target": "y", "emit": "goY", "delayMs": 5},
            {"source": "x", "whenEvent": "goX", "target": "z", "emit": "done", "delayMs": 1},
        ],
    }])
    cases.append({"id": "fan-out", "description": "一个事件触发多条规则，顺序按规则数组",
                  "model": fan_model, "scenarioId": "fan", "disabledNodeIds": []})
    cases.append({"id": "fan-out-blocked", "description": "扇出中屏蔽一个分支",
                  "model": fan_model, "scenarioId": "fan", "disabledNodeIds": ["y"]})

    # 4. A cycle: bounded by MAX_EVENTS, never by an unbounded loop.
    loop_nodes = [_node("p", "P"), _node("q", "Q")]
    loop_edges = [_edge("e1", "p", "q"), _edge("e2", "q", "p")]
    loop_model = _model(loop_nodes, loop_edges, [{
        "id": "loop", "name": "循环", "evidenceClass": "hypothetical",
        "events": [{"module": "p", "event": "tick", "timeMs": 0}],
        "rules": [
            {"source": "p", "whenEvent": "tick", "target": "q", "emit": "tock", "delayMs": 1},
            {"source": "q", "whenEvent": "tock", "target": "p", "emit": "tick", "delayMs": 1},
        ],
    }])
    cases.append({"id": "cycle-bounded", "description": "因果环由 MAX_EVENTS 截断",
                  "model": loop_model, "scenarioId": "loop", "disabledNodeIds": []})

    # 5. A rule that pushes the clock past MAX_TIME_MS.
    late_nodes = [_node("m", "M"), _node("n", "N")]
    late_edges = [_edge("e1", "m", "n")]
    late_model = _model(late_nodes, late_edges, [{
        "id": "late", "name": "超时边界", "evidenceClass": "hypothetical",
        "events": [{"module": "m", "event": "emit", "timeMs": 3599000}],
        "rules": [{"source": "m", "whenEvent": "emit", "target": "n", "emit": "late", "delayMs": 2000}],
    }])
    cases.append({"id": "max-time", "description": "事件时间越过 MAX_TIME_MS 被截断",
                  "model": late_model, "scenarioId": "late", "disabledNodeIds": []})

    return cases


def expected_for(case: dict) -> dict:
    """The exact result both engines must produce for this case."""
    baseline = simulate(case["model"], case["scenarioId"], [])
    intervention = simulate(case["model"], case["scenarioId"], case["disabledNodeIds"])
    comparison = compare(case["model"], case["scenarioId"], case["disabledNodeIds"])
    return {
        "baseline": baseline,
        "intervention": intervention,
        "comparison": {
            "status": comparison["status"],
            "scenarioId": comparison["scenarioId"],
            "lostEvents": comparison["lostEvents"],
            "gainedEvents": comparison["gainedEvents"],
        },
    }


def write_cases() -> Path:
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "schemaVersion": "0.2.0",
        "description": "Python 与 JavaScript 仿真内核必须逐字复现的黄金用例。",
        "cases": [
            {**case, "expected": expected_for(case)}
            for case in build_cases()
        ],
    }
    target = CASES_DIR / "simulation-golden.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    return target


def load_cases() -> list[dict]:
    path = CASES_DIR / "simulation-golden.json"
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


if __name__ == "__main__":
    import sys

    if "--write" in sys.argv:
        print(f"wrote {write_cases()}")
    else:
        for case in load_cases():
            summary = case["expected"]["baseline"]["summary"]
            print(f"{case['id']:24} {summary['delivered']:4} delivered  {summary['blocked']:4} blocked  "
                  f"truncation={case['expected']['baseline']['truncation']}")
