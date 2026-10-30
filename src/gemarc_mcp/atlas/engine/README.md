# Atlas 引擎（`gemarc_mcp.atlas.engine`）

确定性的、脚本驱动的离散事件引擎：4D（按用例脚本回放）与 5D（干预后的 A/B 重算与因果链）。输入是展开后的 `gemarc-atlas/1` 模型（契约见 `docs/atlas/spec/model-schema.md`），输出是可直接 `json.dumps` 的 dict，形状与参考实现的运行结果一致，使移植的查看器可以播放它们（差异见第 7 节）。

纯函数：不读时钟、不用随机数、不做 I/O，只用标准库；同样的输入永远得到同样的输出。引擎从不修改传入的模型、用例或干预。

## 1. 模块

| 文件 | 内容 |
|---|---|
| `types.py` | 常量（`DEFAULT_POLICY` 即 `model.DEFAULT_POLICY`，唯一一份）、结构化 `cause` 的代码表、`AtlasEngineError`、JavaScript 语义辅助（`truthy`、`strict_eq`、`js_string`、`UNDEF`） |
| `guards.py` | `eval_guard`、`eval_when`、`normalize_when`（封闭的守卫解释器，不执行任何代码） |
| `deltas.py` | `index_model`、`compile_deltas`、`validate_element`、`validate_rewrite`、`flatten_deltas` |
| `sim.py` | `simulate`、`seek`、`fold_state`、`event_key`、`canonical`、`fnv32`、`trace_hash` |
| `compare.py` | `compare_runs`、`run_causal`、`explain` |
| `causal.py` | `align_traces`、`causal_chain`、`check_prediction`、`classify`、`chain_lines`、各语言的文本渲染 |

## 2. 调用

```python
from gemarc_mcp.atlas import engine as E

ix = E.index_model(model)                                     # 建一次，反复传给 opts["index"]
run = E.simulate(model, ix.cases[case_id], [], {"index": ix}) # 4D：基线
result = E.run_causal(model, case_id, [intervention], {"index": ix, "locale": "zh-CN"})   # A、B 与比较
chain = E.causal_chain(model, result, intervention, ix, "zh-CN")                          # 5D 因果链
lines = E.chain_lines(chain, "zh-CN")                                                     # 纯文本形式
```

`opts`：`index`（预建的 `Index`）、`compiled`（预编译的 `Compiled`）、`interventionTime`（干预只对起跑时刻 `>=` 它的事件生效，默认 0）、`maxEvents`（默认 1000，到达即以 `LIMIT_EXCEEDED` 停止）、`locale`（只用于 `run_causal` 解释里的句子）。没有 `seed`。

**前提：模型必须先通过 `model.contract_issues`（结果为空）。** 引擎不重新检查契约，也不防御恶意输入；MCP 工具层与预言机必须先过这道关。违反契约的输入会让 `simulate`/`run_causal` 抛 `AtlasEngineError`（`GEMARC_ATLAS_ENGINE_003`，消息带原始异常类型），不会冒出裸的 `TypeError`/`KeyError`/`RecursionError`。选项类型不对（`interventionTime` 不是有限数、`maxEvents` 不是有限的 >= 0 的数（无穷大会让重试循环失去上界）、布尔值都不行）抛 `GEMARC_ATLAS_ENGINE_002`；`'5'` 不会再被悄悄当成不生效的干预时刻。

`run_causal` 对未知用例抛 `AtlasEngineError`（`GEMARC_ATLAS_ENGINE_001`）。结果是**只读视图**：它与输入共享部分子对象（两次运行的轨迹、模型与干预里的片段）；要在结果上加字段的调用方（预言机、查看器装配、规则）先自己拷贝。解释里的 `policy`、`context`、预测里的 `expected`、根上的 `facts` 已经是深拷贝。干预无法计算时不抛：返回 `status` 为 `DRAFT` 或 `REJECTED` 的结果，`diff` 与 `explanation` 为 `None`。

## 3. 干预的编译

`compile_deltas(ix, deltas)` 返回 `Compiled`，`status` 为：

* `OK`：每个部分都与模型一致；
* `DRAFT`：只有插入元素的主体不完整（缺端口、状态机……），或元素主体完整但 `rewrite` 为空（草稿元素尚未连边，规范 4.9），不可计算，不算错误；
* `INVALID`：其他任何问题（未知目标、数值不合法、重写链断裂、未知类型）。

`DRAFT` 的判据是：所有错误信息都以 `element ` 开头。`errors` 的措辞是模块的契约，规则层与测试逐字读取，例如 `disable_module: unknown node <id>`、`change_timeout: timeoutMs must be > 0`、`rewrite <edge>: port type mismatch Message -> Record`、`edge <id> is rewritten twice`。

原子类型：`disable_module`、`disable_edge`、`resource_failure`、`identity_failure`、`change_timeout`、`change_retry`、`delay_message`、`change_guard`、`insert_element`；`combined_intervention` 先被 `model.flatten_parts` 深度优先展平。`insert_element` 的元素主体现在必须是完整的显式对象（`use=<kind>` 模板由下一通道提供）；端口 id 固定为 `<元素>:in` / `<元素>:out`，缺端口的元素是 `DRAFT`，引擎不替它推导。

数值硬化：`change_retry.retryMax` 大于 `MAX_RETRY`（1000）是 `INVALID`；元素的 `latencyMs` 存在时必须是有限且 >= 0 的数（否则 `INVALID`，错误以 `insert_element ` 开头，不会被当成草稿）；`rewrite[].replaceEdgeId` 不是字符串是 `INVALID`。重试循环本身也以 `maxEvents` 为界：达到上限时运行以 `LIMIT_EXCEEDED` 停止（不会因为巨大的 `retryMax` 空转）。

`rewrite[]`：`replace` 的链边检查（两条边、与原边端点衔接、经过元素、端口存在且方向对、类型兼容、不成环）；`keep` 与 `disable` 没有链，只检查目标边存在且不是 `owns` 边。

## 4. 仿真语义（要点）

* 事件按 `seq` 排序后依次执行；数组顺序与键顺序无关。入口节点是第一个事件的 `from`，从 `Running` 开始。
* 路由：显式 `edge`；否则取运行时边（`edge.static` 为假），`Return` 沿请求边反向走；找不到边是 `Unbound`；候选边全被禁用是 `Blocked`（`edge_off`）；被 `replace` 重写的边经过插入元素逐跳执行（事件 id 后缀 `#h1`、`#h2`、`#resp`）。
* 逻辑时钟：`Call`/`Message` 的尝试延迟取 `latencyMs`（数组按尝试次序，用完取最后一项）加上 `delay_message`；延迟严格大于 `timeoutMs` 才超时（`>`），超时记录 `Timeout`，随后加 `retryBackoffMs`（只在还有尝试时），重试成功的记录类型是 `Retry`，用尽重试得到 `Failure`/`Failed`（`retries_used_up`）。`group` 内的事件同一时刻起跑，时钟取各成员结束时刻的最大值。
* 起点状态：`Idle`；机器节点取 `initialMachines` 或 `initial`。`Starved`：从未被触达（或已 `Failed`/`Blocked`）的源不能发事件；`Return` 需要未决请求。
* 聚合：带 `section` 的事件失败后，`props.aggregator` 节点在返回时变 `Degraded`（`sections_missing`）。去重：`props.dedup` 与事件 `dedupKey`；模式 `reject` 得到 `Rejected`，`ignore` 得到 `Ignored`。
* 10 种事件结果：`OK Blocked Starved Rejected Ignored Denied Failed Timeout Skipped Unbound`；运行状态 `OK DRAFT REJECTED LIMIT_EXCEEDED`。

## 5. 结果形状

### 5.1 `simulate` 的运行结果

```
{caseId, modelVersion, status, errors, trace[], initialState, finalState, elementStates, snapshots[], entryNodeId,
 aggregatorFailures, hash}
```

`DRAFT`/`REJECTED` 时只有 `{caseId, modelVersion, status, errors, trace: [], initialState: {}, finalState: {}, snapshots: [], hash: None}`。`modelVersion` 是 `model_sha8(model)`。`hash` 是 `trace` 的 canonical 文本的 32 位 FNV 哈希（变体 a）（只用于发现变化，不防对手）。

`trace[i]` 恰有 21 个键，外加元素行才有的 `elementState`：

```
index, sequence, caseId, eventId, logicalTime, sourceNodeId, targetNodeId, edgeId, eventType, outcome, previousState,
nextState, stateChanges[{nodeId, from, to}], ruleId, evidenceIds, errorCode, cause, attempt, synthetic, role, httpStatus
```

`eventId` 是 `<caseId>#<seq>` 加后缀：`@<n>`（第 n 次尝试）、`!`（用尽重试）、`#h1` `#h2` `#resp`（插入元素的逐跳）。`eventType` 可能是 `Timeout` 或 `Retry`（由 `Call`/`Message` 改写的记录）。`synthetic` 只看这次投递是否经过元素。`snapshots` 是 `{index, state}`，首项 `index == -1`，之后每 4 条记录一个。

`seek(run, i)` 从最近的快照回放；`fold_state(run, i)` 从 `initialState` 折叠。二者应相等，唯一已知的例外见第 7 节 P1。

### 5.2 `compare_runs(A, B)` 的 `diff`

```
{outcomeClass: NO_DIFFERENCE|CONVERGED|DIVERGED, firstDivergence: {index, logicalTime, a, b}|None,
 terminalDiff[{nodeId, a, b}], addedNodes[{nodeId, b}], affectedNodes[], affectedEdges[], blocking[{index, edgeId, nodeId, cause}],
 propagation[{index, nodeId, cause}], recovery[], compensation[{index, outcome}], feedback[{index, attempt, outcome}],
 recovered, epistemic}
```

### 5.3 `run_causal` 的结果

`{caseId, status, errors, A, B, diff, explanation}`。`explanation`：`baselineConditions{caseId, policy, initialMachines, context, modelVersion}`、`intervention[{type, interventionId, target}]`、`interventionTime`、`firstDivergence`、`affectedNodes`、`affectedEdges`、`appliedRules`、`propagation`、`blocking`、`recovery`、`compensation`、`finalOutcomeDiff`、`outcomeClass`、`summary`（文本）、`evidence`、`assumptions`（文本）、`epistemic`。

任一次运行在 `maxEvents` 处被截断时（`A.status` 或 `B.status` 是 `LIMIT_EXCEEDED`），结果的 `status` 是 `LIMIT_EXCEEDED`（不是 `OK`），`diff`、`explanation` 与 `causal_chain` 都带 `truncated: true`（完整的对没有这个键）。这时 `outcomeClass` 只描述被截断的前缀，**不是结论**：预言机应当把它当作“未决定”。因果链的 `headline` 在句首说明这一点。

### 5.4 `causal_chain` 的因果链

```
{caseId, interventionId, question, root, prediction, epistemic, status, outcomeClass, rows[], steps[], effects[], counts{},
 design[], rootDesign, confidence, headline, firstDivergence}
```

* `rows`：LCS 对齐，`{kind: same|changed|aOnly|bOnly, a, b}`（`a`、`b` 是两条轨迹里的下标）。
* `steps[]`：`{id, side, role, kind, index, at, aIndex, bIndex, eventType, sourceNodeId, targetNodeId, edgeId, outcome, aOutcome, cause, why, logicalTime, stateChanges, parents[], via[], design, isFirstDivergence}`；`role` 取 `ROLE` 的 15 个值之一（`block deny fail timeout reject starve degrade retry recovery compensation bypass gain lost` 加 `cause`、`effect`）。`via` 与 `parents` 一一对应（同一父级可出现多次 `via`）。
* `effects[]`：终态差，`{nodeId, a, b, name, parents[], design}`。
* `root`：`{id, role, type, label, nodeIds, edgeIds, facts[], element}`，`facts` 是结构化的（第 6 节）。
* `prediction`：`{expected, ok, mismatches[], note}`，没有 `expect` 时为 `None`。
* 不可计算（`DRAFT`/`REJECTED`）时只有头部字段、空的 `steps`/`effects` 与一句说明的 `headline`。

颜色、播放覆盖层（播放覆盖层）属于查看器，引擎不提供。`revealed_steps(chain, idx)` 提供按播放位置揭示步骤。

## 6. 结构化的原因与文本

`cause` 是 `{code, args}` 或 `None`；`args` 永远是对象。代码与参数：

| code | args |
|---|---|
| `module_off` | `nodeId` |
| `edge_off` | |
| `no_open_request` | |
| `source_state` | `state` |
| `duplicate` | |
| `declared_fault` | |
| `resource_failure`、`identity_failure` | |
| `guard` | |
| `no_such_transition` | |
| `transition_guard` | `transitionId` |
| `latency_over_timeout` | `latencyMs`、`timeoutMs` |
| `retries_used_up` | |
| `failure_reported` | |
| `sections_missing` | `sections[]`（失败区段，按先后） |
| `condition_false` | |
| `no_runtime_edge` | |
| `unsupported_type` | `eventType` |
| `element_answered`、`element_dropped`、`element_failed`、`already_answered` | |

`via` 的代码：`direct-effect start-lost source-state section-missing section-answered state-changed previous-change when-switch touches-root no-other-difference`；`facts`：`timeout-change retry-change delay-added guard-change element-inserted`；`prediction.mismatches`：`status outcomeClass terminal`，各带 `args`。

文本渲染：`cause_text`、`via_text`、`fact_text`、`mismatch_text`、`role_label`、`outcome_label`、`summary_text`、`assumption_text`，语言 `zh-CN`、`ja-JP`、`en-US`（未知语言退回 `en-US`）。`causal_chain` 与 `run_causal` 里的 `why`、`question`、`headline`、`summary`、`assumptions` 与元素的 `name`（`<kind> (<role>)`）是渲染后的文本；其余都是结构化数据，机器只读结构化部分。

## 7. 与参考实现的差异

记录在 `tests/atlas/golden/deviations.json`，每一项都有测试和黄金里的证据。

**四个设计中的修复**

* D1 轨迹对齐按（步骤，尝试次数）配对后缀事件 id（参考实现把 `#` 后的文本当数字，对 `C#6@1` 得到 `NaN`）。
* D2 `cause` 结构化（带文本渲染器）；`via`、`facts`、`mismatches` 同样。
* D3 去掉 `seed`。
* D4 只有一个 `DEFAULT_POLICY`。

**契约要求的差异（C1 至 C7）**：`keep`/`disable` 重写不带链且只对 `replace` 做链检查；元素种类用词表 id；`edge.static` 取代关系名查表；验证等级按契约的 rank 比较（`Implemented` 强于 `Observed`）；契约不接受的 `combined_intervention` 报 `INVALID` 而不是悄悄变空；`epistemic` 常量与元素显示名是渲染文本；版本戳是 `model_sha8`，没有修订号字段。

**修复轮新增**：C8 完整元素体加空 `rewrite` 是 `DRAFT`（参考给 `INVALID`，规范 4.9 要求草稿）；H1 截断的因果对是 `LIMIT_EXCEEDED` 并带 `truncated`（参考给 `OK`）；H2 硬化：重试循环以 `maxEvents` 为界、`retryMax` <= 1000、数值与选项类型检查、`GEMARC_ATLAS_ENGINE_002/003`。对应黄金只改了 3 个运行的 `shared` 条目的比较方式（见 `GENERATION.md` 第 8 节）。

**刻意保留的参考行为（P1）**：元素 `drop`/`fail` 请求时，元素自身的运行状态在 hop 记录写入之后才改变；若该记录恰好关闭一个快照区间，`seek()` 在该下标起（直到下一个快照）缺少这次改变，而 `fold_state()` 有。已用测试钉住，是候选的第五个修复，由主线决定。

## 8. 测试与黄金

* `tests/atlas/test_engine_golden.py`：对 `tests/atlas/golden/engine.json` 的逐值对拍（`shared` 与 `diverged` 两层），夹具在 `tests/atlas/engine_fixtures/`（合成模型，随机 id 家族）。生成方式与每个输入输出的 sha256 见 `tests/atlas/golden/GENERATION.md`；生成器与交叉核对脚本在仓库外。
* `tests/atlas/test_engine_behaviour.py`：手算的期望、不变量（确定性、事件顺序无关、不改输入、无时钟与 I/O、seek 等于 fold）、每个差异的测试。
* 单跑：`PYTHONPATH=src ./.venv/Scripts/python.exe -m unittest discover -s tests/atlas -t tests -p "test_engine_*.py"`。

## 9. 给后续通道的说明

* 元素主体（`use=<kind>` 模板）与完整的 `validate_element` 之前的输入都是显式对象；缺端口的元素是 `DRAFT`。
* 预言机、突变体、守卫扫描（设计 M5 的其余部分）建立在 `simulate`/`compile_deltas` 之上，不需要改引擎。
* 引擎对结果里的字段名沿用参考实现（`stateChanges`、`sourceNodeId`、`firstDivergence` 等），查看器依赖这些名字。
