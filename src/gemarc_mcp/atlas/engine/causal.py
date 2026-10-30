"""5D causal chain: from a baseline/intervention pair to an traceable chain.

    cause (intervention target) -> mechanism steps (block, starve, timeout, bypass ...) -> terminal effects

Every step is linked to the step that made it happen (``parents`` / ``via``) and to the requirements, design ids,
evidence and verification levels of the nodes and edges it touches. Cause and effect hold only to the rules of the model
(the marker is "model-defined").

Semantic port of the reference causal module with the recorded divergences:

1. ``align_traces`` pairs events of one scripted step by ``(seq, attempt)``. The reference split the event id at
   ``#`` and treated the text after it as a number, which is ``NaN`` for the ids of retried, timed-out and failed
   deliveries (``C#6@1``, ``C#6!``): such rows were mis-sorted and could not pair with the event they replaced.
2. ``cause`` is the structured ``{code, args}`` of ``types``; the human sentences are rendered by the functions below
   for a locale (``zh-CN``, ``ja-JP`` or ``en-US``). ``via``, the root ``facts`` and the prediction ``mismatches``
   are structured in the same way (``{code, args}``); ``why``, ``question``, ``headline`` and the element ``name`` are
   rendered text.
3. ``confidence.weakest`` follows the contract rank (``model.weakest``): ``Implemented`` beats ``Observed``.

Colours and the playback overlay belong to the viewer and are not part of this module.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any, Mapping

from .. import model as am
from .deltas import Index, flatten_deltas, index_model
from .sim import event_key
from .types import DEFAULT_POLICY, UNDEF, get, js_string, nullish, truthy
from . import types as T

DEFAULT_LOCALE = "en-US"
EPISTEMIC = "model-defined"

# Roles of a differing event in the chain; ``True`` marks a failure-like role.
ROLE = {
    "cause": True, "block": True, "deny": True, "fail": True, "timeout": True, "reject": True, "starve": True,
    "degrade": True, "retry": False, "recovery": False, "compensation": False, "bypass": False, "gain": False,
    "lost": False, "effect": True,
}
OUTCOME_CLASSES = ("NO_DIFFERENCE", "CONVERGED", "DIVERGED", "DRAFT", "REJECTED")
ASSUMPTION_CODES = ("declared-latencies", "model-defined-scope")

# Codes of ``via`` (why a step is attached to its parent), ``facts`` and prediction ``mismatches``.
VIA_CODES = ("direct-effect", "start-lost", "source-state", "section-missing", "section-answered", "state-changed",
             "previous-change", "when-switch", "touches-root", "no-other-difference")
FACT_CODES = ("timeout-change", "retry-change", "delay-added", "guard-change", "element-inserted")
MISMATCH_CODES = ("status", "outcomeClass", "terminal")


def _code(code: str, **args: Any) -> dict:
    return {"code": code, "args": args}


# --------------------------------------------------------------------------------------------------------
# Text (three locales; the structured data above is what machines read)
# --------------------------------------------------------------------------------------------------------

_TEXT: dict[str, dict[str, Any]] = {
    "en-US": {
        "role": {"cause": "Cause (intervention)", "block": "Blocked", "deny": "Denied (identity)", "fail": "Failed",
                 "timeout": "Timeout", "reject": "Guard rejected", "starve": "Starved (never started)", "degrade": "Degraded",
                 "retry": "Retry", "recovery": "Recovery path", "compensation": "Compensation", "bypass": "Bypassed",
                 "gain": "Newly happens", "lost": "Lost (baseline only)", "effect": "Final state differs"},
        "outcome": {"NO_DIFFERENCE": "No difference", "CONVERGED": "Converged (path changed only)",
                    "DIVERGED": "Diverged (final state changed)", "DRAFT": "Draft (not computable)",
                    "REJECTED": "Rejected (invalid intervention)"},
        "cause": {
            "module_off": "{nodeId} is unavailable", "edge_off": "the connection is disabled",
            "no_open_request": "no matching request has arrived", "source_state": "the sender is {state} and cannot send",
            "duplicate": "treated as a duplicate", "declared_fault": "a fault declared in the case",
            "resource_failure": "resource exhaustion (injected)", "identity_failure": "identity check failure (injected)",
            "guard": "the guard condition is not met", "no_such_transition": "the state machine has no such transition",
            "transition_guard": "guard {transitionId} is not met", "latency_over_timeout": "latency {latencyMs} ms > timeout {timeoutMs} ms",
            "retries_used_up": "retries are used up", "failure_reported": "a failure was reported",
            "sections_missing": "sections missing: {sections}", "condition_false": "the run condition does not hold",
            "no_runtime_edge": "no formal edge carries this event", "unsupported_type": "unsupported event type {eventType}",
            "element_answered": "the inserted element answered instead", "element_dropped": "the inserted element dropped it",
            "element_failed": "the inserted element failed it", "already_answered": "the inserted element had already answered"},
        "via": {
            "direct-effect": "direct effect of the intervention", "start-lost": "{nodeId} no longer starts",
            "source-state": "{nodeId} is {state}", "section-missing": "section {nodeId} is missing",
            "section-answered": "section {nodeId} answered", "state-changed": "the state of {nodeId} changed",
            "previous-change": "the preceding state change", "when-switch": "the run condition (when) flipped",
            "touches-root": "touches the target of the intervention", "no-other-difference": "no other difference exists"},
        "fact": {
            "timeout-change": "timeoutMs {from} -> {to}", "retry-change": "retryMax {from} -> {to}",
            "delay-added": "+{ms} ms on {edgeId}", "guard-change": "guard of {transitionId} -> {guard}",
            "element-inserted": "{edgeId} replaced by a {kind} element"},
        "guard-none": "none", "no-intervention": "(no intervention)",
        "mismatch": {"status": "status: expected {expected} / computed {got}",
                     "outcomeClass": "outcome class: expected {expected} / computed {got}",
                     "terminal": "{nodeId}: expected {expected} / computed {got}"},
        "question": 'If "{description}", how does the result of "{title}" change?',
        "headline": {
            "draft": "The element contract (ports, state machine) is incomplete, so it takes no part in the causal computation.",
            "rejected": "The intervention is invalid and is not computed ({errors}).",
            "off-path": "The intervention target ({targets}) is not on the causal path of this case; by design the case does not depend on it.",
            "converged": "The path changed in {steps} place(s) but the final state equals the baseline ({counts}).",
            "diverged": "The final state changed on {effects} node(s): {list}{more}",
            "truncated": "A run stopped at the event limit ({events} entries), so this comparison covers only the first part of the case and is not conclusive."},
        "summary": {"NO_DIFFERENCE": "The intervention changed no observable result (no difference).",
                    "CONVERGED": "The path changed but the final state equals the baseline (converged).",
                    "DIVERGED": "The final state differs from the baseline (diverged)."},
        "assumption": {"declared-latencies": "Latencies and timeouts are values declared in the case, not production measurements",
                       "model-defined-scope": "Cause and effect hold only within the rules of this model (model-defined)"},
        "lines": {"question": "Question", "result": "Result", "reason": "Cause", "effect": "Effect", "prediction": "Design intent check",
                  "match": "match", "mismatch": "mismatch"},
        "more": " ...", "join": ", ", "none": "-",
    },
    "zh-CN": {
        "role": {"cause": "原因（干预）", "block": "阻断", "deny": "拒绝（身份）", "fail": "失败", "timeout": "超时",
                 "reject": "守卫拒绝", "starve": "连锁（未启动）", "degrade": "降级", "retry": "重试", "recovery": "恢复路径",
                 "compensation": "补偿", "bypass": "绕过", "gain": "新出现", "lost": "消失（仅基线）", "effect": "终态差异"},
        "outcome": {"NO_DIFFERENCE": "无差异", "CONVERGED": "收敛（仅路径变化）", "DIVERGED": "分叉（终态变化）",
                    "DRAFT": "草稿（无法计算）", "REJECTED": "驳回（干预无效）"},
        "cause": {
            "module_off": "{nodeId} 不可用", "edge_off": "该连接已被断开", "no_open_request": "没有对应的请求到达",
            "source_state": "发送方处于 {state}，无法发送", "duplicate": "被当作重复请求", "declared_fault": "用例中声明的故障",
            "resource_failure": "资源耗尽（注入）", "identity_failure": "身份校验失败（注入）", "guard": "守卫条件不成立",
            "no_such_transition": "状态机没有这个转移", "transition_guard": "守卫 {transitionId} 不成立",
            "latency_over_timeout": "延迟 {latencyMs} ms > 超时 {timeoutMs} ms", "retries_used_up": "重试次数用尽",
            "failure_reported": "已报告失败", "sections_missing": "缺少的区段：{sections}", "condition_false": "执行条件不成立",
            "no_runtime_edge": "没有正式的边承载该事件", "unsupported_type": "不支持的事件类型 {eventType}",
            "element_answered": "插入的元素代为应答", "element_dropped": "插入的元素丢弃了它", "element_failed": "插入的元素让它失败",
            "already_answered": "插入的元素已经应答"},
        "via": {
            "direct-effect": "干预的直接作用", "start-lost": "{nodeId} 不再启动", "source-state": "{nodeId} 处于 {state}",
            "section-missing": "区段 {nodeId} 缺失", "section-answered": "区段 {nodeId} 已应答",
            "state-changed": "{nodeId} 的状态变了", "previous-change": "紧邻的上一次状态变化",
            "when-switch": "执行条件（when）翻转了", "touches-root": "与干预的对象相连", "no-other-difference": "除干预外没有别的差异"},
        "fact": {
            "timeout-change": "timeoutMs {from} -> {to}", "retry-change": "retryMax {from} -> {to}",
            "delay-added": "{edgeId} 增加 {ms} ms", "guard-change": "{transitionId} 的守卫 -> {guard}",
            "element-inserted": "{edgeId} 改为经过 {kind} 元素"},
        "guard-none": "无", "no-intervention": "（无干预）",
        "mismatch": {"status": "状态：预期 {expected} / 计算 {got}", "outcomeClass": "结果分类：预期 {expected} / 计算 {got}",
                     "terminal": "{nodeId}：预期 {expected} / 计算 {got}"},
        "question": "如果“{description}”，“{title}”的结果会怎样变化？",
        "headline": {
            "draft": "元素的契约（端口、状态机）不完整，不参与因果计算。",
            "rejected": "干预无效，不做计算（{errors}）。",
            "off-path": "干预对象（{targets}）不在该用例的因果路径上；按设计，该用例不依赖它。",
            "converged": "路径有 {steps} 处变化，但终态与基线相同（{counts}）。",
            "diverged": "终态在 {effects} 个节点上变化：{list}{more}",
            "truncated": "有一次运行在事件数上限（{events} 条）处被截断，这次对比只覆盖用例的前半部分，不能作为结论。"},
        "summary": {"NO_DIFFERENCE": "干预没有改变任何可观察的结果（无差异）。", "CONVERGED": "路径变了，但终态与基线相同（收敛）。",
                    "DIVERGED": "终态与基线不同（分叉）。"},
        "assumption": {"declared-latencies": "延迟与超时是用例中声明的值，不是生产环境的观测值",
                       "model-defined-scope": "因果只在本模型的规则范围内成立（model-defined）"},
        "lines": {"question": "问题", "result": "结果", "reason": "原因", "effect": "效果", "prediction": "设计意图核对",
                  "match": "一致", "mismatch": "不一致"},
        "more": " ……", "join": "、", "none": "-",
    },
    "ja-JP": {
        "role": {"cause": "原因（介入）", "block": "遮断", "deny": "拒否（認証）", "fail": "失敗", "timeout": "タイムアウト",
                 "reject": "ガード拒否", "starve": "玉突き（始動せず）", "degrade": "縮退", "retry": "再試行", "recovery": "復旧の道",
                 "compensation": "補償", "bypass": "迂回", "gain": "新たに発生", "lost": "消失（対照のみ）", "effect": "終了時の状態の差"},
        "outcome": {"NO_DIFFERENCE": "差なし", "CONVERGED": "合流（道筋だけ変わった）", "DIVERGED": "食い違い（終了時の状態が変わった）",
                    "DRAFT": "下書き（実行できない）", "REJECTED": "却下（介入が不正）"},
        "cause": {
            "module_off": "{nodeId} が利用できない", "edge_off": "接続が切られている",
            "no_open_request": "対応する要求が届いていない", "source_state": "送り手が {state} のため送れない",
            "duplicate": "二重の要求として扱われた", "declared_fault": "ケースで宣言された障害",
            "resource_failure": "リソース不足（注入）", "identity_failure": "認証の失敗（注入）", "guard": "ガードを満たさない",
            "no_such_transition": "遷移表にその遷移がない", "transition_guard": "ガード {transitionId} が成り立たない",
            "latency_over_timeout": "遅延 {latencyMs} ms > タイムアウト {timeoutMs} ms", "retries_used_up": "再試行を使い切った",
            "failure_reported": "失敗の通知があった", "sections_missing": "欠けたセクション：{sections}", "condition_false": "実行の前提が成り立たない",
            "no_runtime_edge": "このイベントを運ぶ正式な辺がない", "unsupported_type": "未対応のイベント種別 {eventType}",
            "element_answered": "挿入要素が代わりに返事した", "element_dropped": "挿入要素が捨てた", "element_failed": "挿入要素が失敗させた",
            "already_answered": "挿入要素がすでに返事済み"},
        "via": {
            "direct-effect": "介入の直接の作用", "start-lost": "{nodeId} が起動しなくなった", "source-state": "{nodeId} が {state}",
            "section-missing": "セクション {nodeId} が欠けた", "section-answered": "セクション {nodeId} が返事した",
            "state-changed": "{nodeId} の状態が変わった", "previous-change": "一つ前の状態の変わり目",
            "when-switch": "実行の前提（when）が切り替わった", "touches-root": "介入の対象に接している",
            "no-other-difference": "介入以外に差はない"},
        "fact": {
            "timeout-change": "timeoutMs {from} -> {to}", "retry-change": "retryMax {from} -> {to}",
            "delay-added": "{edgeId} に +{ms} ms", "guard-change": "{transitionId} のガード -> {guard}",
            "element-inserted": "{edgeId} を {kind} 要素を通る道に置き換え"},
        "guard-none": "なし", "no-intervention": "（介入なし）",
        "mismatch": {"status": "状態：予測 {expected} / 算出 {got}", "outcomeClass": "結果分類：予測 {expected} / 算出 {got}",
                     "terminal": "{nodeId}：予測 {expected} / 算出 {got}"},
        "question": "「{description}」としたら、「{title}」の結果はどう変わるか。",
        "headline": {
            "draft": "ポートや遷移表が足りない要素は、因果の算出に加わらない。",
            "rejected": "介入が不正なので実行しない（{errors}）。",
            "off-path": "介入の対象（{targets}）は、このケースの因果の道筋にない。このケースはその対象に依存しない設計である。",
            "converged": "道筋は {steps} か所で変わったが、終了時の状態は対照と同じ（{counts}）。",
            "diverged": "終了時の状態が {effects} 個のノードで変わった：{list}{more}",
            "truncated": "片方の実行がイベント数の上限（{events} 件）で打ち切られたため、ここでの違いはケースの前半だけを対象とし、結論にはならない。"},
        "summary": {"NO_DIFFERENCE": "介入は観測できる結果を変えなかった（差なし）。", "CONVERGED": "道筋は変わったが、終了時の状態は対照と同じ（合流）。",
                    "DIVERGED": "終了時の状態が対照と異なる（食い違い）。"},
        "assumption": {"declared-latencies": "遅延とタイムアウトはケースで宣言した値で、実運用で測った値ではない",
                       "model-defined-scope": "因果はこのモデルが定めた範囲でのみ成り立つ（model-defined）"},
        "lines": {"question": "問い", "result": "結果", "reason": "原因", "effect": "成果", "prediction": "狙いとの照合",
                  "match": "一致", "mismatch": "不一致"},
        "more": " …", "join": "、", "none": "-",
    },
}
LOCALES = tuple(_TEXT)


def _table(locale: str | None) -> Mapping[str, Any]:
    return _TEXT.get(locale or DEFAULT_LOCALE) or _TEXT[DEFAULT_LOCALE]


def _fmt(template: str, args: Mapping) -> str:
    try:
        return template.format(**{k: ("" if v is None else v) for k, v in args.items()})
    except (KeyError, IndexError, ValueError):
        return template


def role_label(role: str, locale: str | None = None) -> str:
    return _table(locale)["role"].get(role, role)


def outcome_label(outcome_class: str, locale: str | None = None) -> str:
    return _table(locale)["outcome"].get(outcome_class, outcome_class)


def cause_text(cause: Mapping | None, locale: str | None = None) -> str:
    """Readable sentence of a structured cause; an unknown code is returned as the code itself."""
    if not cause:
        return ""
    template = _table(locale)["cause"].get(cause.get("code"))
    if template is None:
        return str(cause.get("code", ""))
    args = dict(cause.get("args") or {})
    if isinstance(args.get("sections"), list):
        args["sections"] = _table(locale)["join"].join(str(x) for x in args["sections"])
    return _fmt(template, args)


def via_text(via: Mapping, locale: str | None = None) -> str:
    template = _table(locale)["via"].get(via.get("code"))
    return str(via.get("code", "")) if template is None else _fmt(template, via.get("args") or {})


def fact_text(fact: Mapping, locale: str | None = None) -> str:
    table = _table(locale)
    args = dict(fact.get("args") or {})
    if fact.get("code") == "guard-change":
        args["guard"] = table["guard-none"] if args.get("guard") is None else json.dumps(args["guard"], ensure_ascii=False, sort_keys=True)
    template = table["fact"].get(fact.get("code"))
    return str(fact.get("code", "")) if template is None else _fmt(template, args)


def mismatch_text(item: Mapping, locale: str | None = None) -> str:
    args = {k: ("-" if v is None else v) for k, v in (item.get("args") or {}).items()}
    template = _table(locale)["mismatch"].get(item.get("code"))
    return str(item.get("code", "")) if template is None else _fmt(template, args)


def summary_text(outcome_class: str, locale: str | None = None) -> str:
    return _table(locale)["summary"].get(outcome_class, outcome_class)


def assumption_text(code: str, locale: str | None = None) -> str:
    return _table(locale)["assumption"].get(code, code)


# --------------------------------------------------------------------------------------------------------
# Alignment (recorded divergence 1)
# --------------------------------------------------------------------------------------------------------

_SEQ_RE = re.compile(r"#(\d+)")


def event_seq(entry: Mapping) -> int:
    """Scripted step of a trace entry: the integer right after the first ``#`` of its event id (``C#6@1`` -> 6)."""
    found = _SEQ_RE.search(entry["eventId"])
    return int(found.group(1)) if found else -1


def _attempt(entry: Mapping) -> int:
    return entry["attempt"] if isinstance(entry.get("attempt"), int) else 1


def align_traces(A: Mapping, B: Mapping) -> list[dict]:
    """Longest-common-subsequence alignment of two traces by ``event_key``.

    Index alignment would call every event after an inserted hop "different"; the LCS keeps shared events shared.
    Inside one gap, an A-only and a B-only entry of the same scripted step pair up as ``changed``; of several A-only
    candidates the one with the same attempt number wins (an entry without an attempt counts as attempt 1), else the
    first unused one. Rows: ``same`` / ``changed`` / ``aOnly`` / ``bOnly`` with the trace indexes ``a`` and ``b``.
    """
    a, b = A["trace"], B["trace"]
    ka = [event_key(e) for e in a]
    kb = [event_key(e) for e in b]
    n, m = len(a), len(b)
    lcs = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            lcs[i][j] = lcs[i + 1][j + 1] + 1 if ka[i] == kb[j] else max(lcs[i + 1][j], lcs[i][j + 1])
    rows: list[dict] = []
    gap_a: list[int] = []
    gap_b: list[int] = []

    def flush() -> None:
        used: set = set()
        out: list[dict] = []
        for j in gap_b:
            same_step = [i for i in gap_a if i not in used and event_seq(a[i]) == event_seq(b[j])]
            pick = next((i for i in same_step if _attempt(a[i]) == _attempt(b[j])), same_step[0] if same_step else None)
            if pick is not None:
                used.add(pick)
                out.append({"kind": "changed", "a": pick, "b": j})
            else:
                out.append({"kind": "bOnly", "a": None, "b": j})
        for i in gap_a:
            if i not in used:
                out.append({"kind": "aOnly", "a": i, "b": None})

        def order(row):
            # keep script order: an A-only event sits just after the B event of the same scripted step
            position = event_seq(b[row["b"]]) if row["b"] is not None else event_seq(a[row["a"]]) + 0.5
            return (position, row["b"] if row["b"] is not None else row["a"])

        out.sort(key=order)
        rows.extend(out)
        gap_a.clear()
        gap_b.clear()

    i = j = 0
    while i < n and j < m:
        if ka[i] == kb[j]:
            flush()
            rows.append({"kind": "same", "a": i, "b": j})
            i += 1
            j += 1
        elif lcs[i + 1][j] >= lcs[i][j + 1]:
            gap_a.append(i)
            i += 1
        else:
            gap_b.append(j)
            j += 1
    gap_a.extend(range(i, n))
    gap_b.extend(range(j, m))
    flush()
    return rows


# --------------------------------------------------------------------------------------------------------
# Roles, root, design links, prediction
# --------------------------------------------------------------------------------------------------------


def classify(e: Mapping, side: str) -> str:
    """Mechanism role of a differing event (an A-only event is ``lost``)."""
    if side == "A":
        return "lost"
    outcome = e["outcome"]
    mapped = {"Blocked": "block", "Denied": "deny", "Failed": "fail", "Timeout": "timeout", "Rejected": "reject",
              "Starved": "starve", "Skipped": "bypass", "Ignored": "bypass"}.get(outcome)
    if mapped:
        return mapped
    if e["eventType"] == "Failure":
        return "fail"
    if e["eventType"] == "Retry":
        return "retry"
    if e["eventType"] == "Compensation":
        return "compensation"
    if e["role"] == "recovery":
        return "recovery"
    if get(e["cause"], "code", None) == T.C_DEGRADED:
        return "degrade"
    return "gain"


def _root_of(ix: Index, case: Mapping, iv: Mapping | None, locale: str | None) -> dict:
    parts = flatten_deltas([iv] if iv else [])
    node_ids: list = []
    edge_ids: list = []
    facts: list[dict] = []
    element = None
    policy = {**DEFAULT_POLICY, **(case.get("policy") or {})}

    def add(bucket: list, value) -> None:
        if value not in bucket:
            bucket.append(value)

    for d in parts:
        if not isinstance(d, Mapping):
            continue
        if truthy(d.get("targetNodeId")):
            add(node_ids, d["targetNodeId"])
        if truthy(d.get("targetEdgeId")):
            add(edge_ids, d["targetEdgeId"])
        if truthy(d.get("machineId")):
            add(node_ids, d["machineId"])
        kind = d.get("type")
        if kind == "change_timeout":
            facts.append(_code("timeout-change", **{"from": policy["timeoutMs"], "to": d.get("timeoutMs")}))
        if kind == "change_retry":
            facts.append(_code("retry-change", **{"from": policy["retryMax"], "to": d.get("retryMax")}))
        if kind == "delay_message":
            facts.append(_code("delay-added", edgeId=d.get("targetEdgeId"), ms=d.get("addLatencyMs")))
        if kind == "change_guard":
            facts.append(_code("guard-change", transitionId=d.get("transitionId"), guard=d.get("guard")))
        if kind == "insert_element" and isinstance(d.get("element"), Mapping):
            el = d["element"]
            rewrites = d.get("rewrite") or []
            rw = rewrites[0] if rewrites else None
            replaced = ix.edges.get(get(rw, "replaceEdgeId")) if rw else None
            chain = (get(rw, "chain", None) or []) if rw else []
            element = {
                "id": el.get("elementId"), "kind": el.get("kind"),
                "name": f"{js_string(el.get('kind', UNDEF))} ({js_string(nullish(el.get('role'), ''))})",
                "between": [replaced["sourceId"], replaced["targetId"]] if replaced else None,
                "replacedEdgeId": get(rw, "replaceEdgeId", None) if rw else None,
                "edges": [{"id": c["id"], "sourceId": c["sourceId"], "targetId": c["targetId"]} for c in chain]}
            add(node_ids, element["id"])
            if rw:
                add(edge_ids, rw["replaceEdgeId"])
            for c in element["edges"]:
                add(edge_ids, c["id"])
            facts.append(_code("element-inserted", edgeId=element["replacedEdgeId"], kind=el.get("kind")))
    label = iv.get("description") if iv and iv.get("description") is not None else _table(locale)["no-intervention"]
    return {"id": "root", "role": "cause", "type": iv.get("type") if iv else None, "label": label, "nodeIds": node_ids,
            "edgeIds": edge_ids, "facts": copy.deepcopy(facts), "element": element}


def _design_of(model: Mapping, ix: Index, node_ids: list, edge_ids: list) -> dict:
    """Requirements, design ids, evidence and verification levels behind a set of nodes and edges."""
    reqs = [r for r in model.get("requirements", [])
            if any(n in r.get("nodeIds", []) for n in node_ids) or any(e in r.get("edgeIds", []) for e in edge_ids)]
    levels = [g for g in [ix.nodes[n]["verification"] if n in ix.nodes else None for n in node_ids]
              + [ix.edges[e]["verification"] if e in ix.edges else None for e in edge_ids] if g]
    evidence = list(dict.fromkeys([x for n in node_ids for x in (ix.nodes[n].get("evidenceIds") or [])] +
                                  [x for e in edge_ids for x in (ix.edges[e].get("evidenceIds") or [])]))
    return {"requirementIds": [r["id"] for r in reqs],
            "designIds": list(dict.fromkeys(x for r in reqs for x in r.get("designIds", []))),
            "evidenceIds": evidence, "grades": levels}


def weakest(levels) -> str:
    """Weakest grade by the contract rank (``Implemented`` is the strongest); ``Unknown`` for no grade."""
    levels = list(levels)
    return am.weakest(levels) if levels else "Unknown"


def check_prediction(iv: Mapping | None, result: Mapping) -> dict | None:
    """Design intent (``expect``) against the computed result; ``None`` when the intervention states none."""
    exp = iv.get("expect") if iv else None
    if not truthy(exp):
        return None
    mismatches: list[dict] = []
    if truthy(exp.get("status")) and exp["status"] != result["status"]:
        mismatches.append(_code("status", expected=exp["status"], got=result["status"]))
    if truthy(exp.get("outcomeClass")):
        got_class = (result.get("diff") or {}).get("outcomeClass")
        if exp["outcomeClass"] != got_class:
            mismatches.append(_code("outcomeClass", expected=exp["outcomeClass"], got=got_class if got_class is not None else result["status"]))
    final = (result.get("B") or {}).get("finalState") or {}
    for node, expected in (exp.get("terminal") or {}).items():
        got = final.get(node)
        if got != expected:
            mismatches.append(_code("terminal", nodeId=node, expected=expected, got=got))
    return {"expected": copy.deepcopy(exp), "ok": not mismatches, "mismatches": mismatches, "note": nullish(iv.get("note"), "")}


# --------------------------------------------------------------------------------------------------------
# The chain
# --------------------------------------------------------------------------------------------------------


def causal_chain(model: Mapping, result: Mapping, iv: Mapping | None, ix: Index | None = None, locale: str | None = None) -> dict:
    """The traceable chain of one ``run_causal`` result.

    Returns ``{caseId, interventionId, question, root, prediction, epistemic, status, outcomeClass, rows, steps,
    effects, counts, design, rootDesign, confidence, headline, firstDivergence}``; for a draft or rejected
    intervention only the head fields, empty ``steps`` and ``effects`` and an explanatory ``headline``.
    """
    ix = ix or index_model(model)
    locale = locale or DEFAULT_LOCALE
    text = _table(locale)
    case = ix.cases[result["caseId"]]
    root = _root_of(ix, case, iv, locale)
    description = iv.get("description") if iv and iv.get("description") is not None else text["no-intervention"]
    question = _fmt(text["question"], {"description": description, "title": case.get("title", "")})
    prediction = check_prediction(iv, result)
    base = {"caseId": result["caseId"], "interventionId": nullish(iv.get("interventionId"), None) if iv else None,
            "question": question, "root": root, "prediction": prediction, "epistemic": EPISTEMIC}
    if result["status"] not in ("OK", "LIMIT_EXCEEDED") or not result.get("diff"):
        if result["status"] == "DRAFT":
            headline = text["headline"]["draft"]
        else:
            headline = _fmt(text["headline"]["rejected"], {"errors": " / ".join(result.get("errors") or [])})
        return {**base, "status": result["status"], "outcomeClass": result["status"], "rows": [], "steps": [], "effects": [],
                "counts": {}, "design": [], "confidence": None, "headline": headline}
    A, B, diff = result["A"], result["B"], result["diff"]
    rows = align_traces(A, B)
    root_nodes, root_edges = set(root["nodeIds"]), set(root["edgeIds"])
    prov: dict = {}      # node -> last B-side step that changed its state
    lost_act: dict = {}  # node -> A-only step that would have changed its state (an activation that no longer happens)
    steps: list[dict] = []
    last_b = -1
    last_state = None
    for r in rows:
        if r["b"] is not None:
            last_b = r["b"]
        if r["kind"] == "same":
            for ch in B["trace"][r["b"]]["stateChanges"]:
                prov.pop(ch["nodeId"], None)
            continue
        side = "A" if r["kind"] == "aOnly" else "B"
        e = A["trace"][r["a"]] if side == "A" else B["trace"][r["b"]]
        role = classify(e, side)
        parents: list = []
        via: list[dict] = []

        def link(parent, why) -> None:
            if parent:
                if parent not in parents:
                    parents.append(parent)
                via.append(why)

        touches_root = (e["sourceNodeId"] in root_nodes or e["targetNodeId"] in root_nodes
                        or bool(e["edgeId"] and e["edgeId"] in root_edges))
        c_code = get(e["cause"], "code", "")
        c_args = get(e["cause"], "args", {}) or {}
        a_cause = A["trace"][r["a"]]["cause"] if r["a"] is not None else None
        src = e["sourceNodeId"]
        if role in ("block", "deny") or c_code in (T.C_MODULE_DISABLED, T.C_EDGE_DISABLED, T.C_IDENTITY_FAILURE, T.C_RESOURCE_FAILURE):
            link("root", _code("direct-effect"))
        elif role == "starve":
            if c_code == T.C_NO_PENDING or (c_code == T.C_SOURCE_STATE and c_args.get("state") == "Idle"):
                link(lost_act.get(src) or prov.get(src), _code("start-lost", nodeId=src))
            else:
                link(prov.get(src) or lost_act.get(src), _code("source-state", nodeId=src, state=c_args.get("state")))
        elif role == "degrade" or (r["kind"] == "changed" and get(a_cause, "code", None) == T.C_DEGRADED):
            # degraded in B: the missing sections explain it; degraded only in A: the sections that now answer explain it
            gone = role == "degrade"
            source_cause = e["cause"] if gone else a_cause
            for child in (get(source_cause, "args", {}) or {}).get("sections", []):
                link(prov.get(child) or lost_act.get(child),
                     _code("section-missing" if gone else "section-answered", nodeId=child))
        elif role == "lost":
            link(prov.get(src) or lost_act.get(src), _code("state-changed", nodeId=src))
        else:
            link(prov.get(src) or prov.get(e["targetNodeId"]), _code("previous-change"))
        # a `when` condition flipped: it reads node states, so the latest differing state change is what switched it
        if not parents and (c_code == T.C_WHEN_FALSE or (r["a"] is not None and get(a_cause, "code", None) == T.C_WHEN_FALSE)):
            link(last_state, _code("when-switch"))
        # no earlier differing step explains it: the intervention is the only remaining difference between A and B
        if not parents:
            link("root", _code("touches-root") if touches_root else _code("no-other-difference"))
        node_ids = list(dict.fromkeys([e["sourceNodeId"], e["targetNodeId"]]))
        index = r["a"] if side == "A" else r["b"]
        step = {
            "id": f"{side}{index}", "side": side, "role": role, "kind": r["kind"], "index": index,
            "at": r["b"] if side == "B" else last_b + 1, "aIndex": r["a"], "bIndex": r["b"], "eventType": e["eventType"],
            "sourceNodeId": e["sourceNodeId"], "targetNodeId": e["targetNodeId"], "edgeId": e["edgeId"],
            "outcome": e["outcome"], "aOutcome": A["trace"][r["a"]]["outcome"] if r["a"] is not None else None,
            "cause": e["cause"], "why": cause_text(e["cause"], locale), "logicalTime": e["logicalTime"],
            "stateChanges": e["stateChanges"], "parents": parents, "via": via,
            "design": _design_of(model, ix, [x for x in node_ids if x in ix.nodes],
                                 [e["edgeId"]] if e["edgeId"] and e["edgeId"] in ix.edges else []),
            "isFirstDivergence": diff["firstDivergence"] is not None
            and (r["b"] == diff["firstDivergence"]["index"] or r["a"] == diff["firstDivergence"]["index"])
            and not any(s["isFirstDivergence"] for s in steps),
        }
        steps.append(step)
        for ch in e["stateChanges"]:
            (lost_act if side == "A" else prov)[ch["nodeId"]] = step["id"]
        if side == "B" and e["stateChanges"]:
            last_state = step["id"]
        # a changed event also loses whatever its A version did and B does not (e.g. the activation of the callee)
        if r["kind"] == "changed":
            for ch in A["trace"][r["a"]]["stateChanges"]:
                if not any(x["nodeId"] == ch["nodeId"] and x["to"] == ch["to"] for x in e["stateChanges"]):
                    lost_act[ch["nodeId"]] = step["id"]
    effects = []
    for d in [*diff["terminalDiff"], *({"nodeId": x["nodeId"], "a": None, "b": x["b"]} for x in diff["addedNodes"])]:
        parent = prov.get(d["nodeId"]) or lost_act.get(d["nodeId"]) or "root"
        node = ix.nodes.get(d["nodeId"])
        name = node["name"] if node and node.get("name") is not None else ((root["element"] or {}).get("name") or d["nodeId"])
        effects.append({**d, "name": name, "parents": [parent],
                        "design": _design_of(model, ix, [d["nodeId"]] if d["nodeId"] in ix.nodes else [], [])})
    counts: dict = {}
    for s in steps:
        counts[s["role"]] = counts.get(s["role"], 0) + 1
    # load-bearing design: requirements whose nodes/edges carry the causal path, with how many steps pass through them
    req_count: dict = {}
    for s in [*steps, *effects]:
        for rid in s["design"]["requirementIds"]:
            req_count[rid] = req_count.get(rid, 0) + 1
    requirements = {r["id"]: r for r in model.get("requirements", [])}
    design = [{"id": rid, "title": requirements[rid]["title"], "designIds": requirements[rid]["designIds"], "steps": n}
              for rid, n in sorted(req_count.items(), key=lambda kv: (-kv[1], kv[0]))]
    root_design = _design_of(model, ix, [x for x in root["nodeIds"] if x in ix.nodes], [x for x in root["edgeIds"] if x in ix.edges])
    levels = [*root_design["grades"], *(g for s in steps for g in s["design"]["grades"]), *(g for s in effects for g in s["design"]["grades"])]
    if levels:
        order = list(ix.profile.verification_order)
        confidence = {"weakest": weakest(levels),
                      "counts": {g: levels.count(g) for g in order if levels.count(g)}}
    else:
        confidence = None
    off_path = diff["outcomeClass"] == "NO_DIFFERENCE"
    if off_path:
        targets = text["join"].join([*root["nodeIds"], *root["edgeIds"]]) or " / ".join(fact_text(f, locale) for f in root["facts"])
        headline = _fmt(text["headline"]["off-path"], {"targets": targets})
    elif diff["outcomeClass"] == "CONVERGED":
        by_role = text["join"].join(f"{role_label(k, locale)} {v}" for k, v in counts.items())
        headline = _fmt(text["headline"]["converged"], {"steps": len(steps), "counts": by_role})
    else:
        listed = text["join"].join(f"{x['nodeId']} {x['a'] if x['a'] is not None else text['none']}->{x['b']}" for x in effects[:4])
        headline = _fmt(text["headline"]["diverged"], {"effects": len(effects), "list": listed,
                                                      "more": text["more"] if len(effects) > 4 else ""})
    out = {**base, "status": result["status"], "outcomeClass": diff["outcomeClass"], "rows": rows, "steps": steps,
           "effects": effects, "counts": counts, "design": design, "rootDesign": root_design, "confidence": confidence,
           "headline": headline, "firstDivergence": diff["firstDivergence"]}
    if diff.get("truncated"):
        out["truncated"] = True
        out["headline"] = _fmt(text["headline"]["truncated"], {"events": max(len(A["trace"]), len(B["trace"]))}) + " " + headline
    return out


def revealed_steps(chain: Mapping, idx: int) -> list[dict]:
    """Steps shown by B-playback position ``idx`` (an A-only step appears once playback passes where it used to happen)."""
    return [s for s in chain["steps"] if s["at"] <= idx]


def chain_lines(chain: Mapping, locale: str | None = None) -> list[str]:
    """Plain-text form of a chain (the compact hand-off to a prompt or a reviewer)."""
    text = _table(locale)
    lines_t = text["lines"]
    label = outcome_label(chain["outcomeClass"], locale)
    facts = text["join"].join(fact_text(f, locale) for f in chain["root"]["facts"])
    out = [f"{lines_t['question']}: {chain['question']}", f"{lines_t['result']}: {label} - {chain['headline']}",
           f"{lines_t['reason']}: {chain['root']['label']}{f' ({facts})' if facts else ''}"]
    for s in chain["steps"]:
        outcomes = f"A:{s['outcome']}" if s["side"] == "A" else (f"A:{s['aOutcome']}->B:{s['outcome']}" if s["aOutcome"] else f"B:{s['outcome']}")
        why = f" - {s['why']}" if s["why"] else ""
        out.append(f"{s['side']}#{s['index']} [{role_label(s['role'], locale)}] {s['sourceNodeId']}->{s['targetNodeId']} {outcomes}{why} <= {', '.join(s['parents'])}")
    for e in chain["effects"]:
        out.append(f"{lines_t['effect']} {e['nodeId']}: {e['a'] if e['a'] is not None else text['none']} -> {e['b']} <= {', '.join(e['parents'])}")
    if chain["prediction"]:
        p = chain["prediction"]
        verdict = lines_t["match"] if p["ok"] else f"{lines_t['mismatch']} ({' / '.join(mismatch_text(m, locale) for m in p['mismatches'])})"
        out.append(f"{lines_t['prediction']}: {verdict}")
    return out
