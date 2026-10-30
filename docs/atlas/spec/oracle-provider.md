# 预言机提供者：`sealed_runs` 的返回形状

本文件由预言机通道（`src/gemarc_mcp/atlas/oracle.py`）维护，查看器通道与构建通道只读它。查看器的数据契约在 `bundle-contract.md`，不在这里改。

## 1. 原则（ORACLE_FIRST）

构建产物（HTML、文档、提示词）里出现的任何仿真结果，都只能来自本提供者。提供者**只返回已封存或已确认**的条目：

- 没有登记期望的用例，不返回基线、轨迹、因果链、时序图。
- 没有登记期望的干预，不返回对比（干预 x 用例）。
- 状态为 `revised`（改过）的条目也不返回，直到用户确认它。
- 一对（干预 x 用例）要求干预与用例**都**是已封存或已确认：对比结果里带着基线的轨迹。

结果是副作用：调用它本身是写操作（需要 `GEMARC_ALLOW_WRITE=1`），先登记能登记的期望，再给每个被返回轨迹的条目在账本里记一次 `build` 暴露。此后若这条期望被改动，状态就是 `revised`。没有写权限时不会有任何结果，发布闸门也过不了。

## 2. 调用

```python
from gemarc_mcp.atlas import oracle
runs = oracle.sealed_runs(ws, project, lang="")   # lang 为空则用项目的第一个语言
```

模型不满足契约（`model.contract_issues` 非空）时抛 `oracle.OracleError`（代码 `GEMARC_ATLAS_ORACLE_003`），不返回任何内容。写入被关闭时抛 `GemArcError`（`GEMARC_AUTH_002`）。

## 3. 返回值

```
{
  "schema":     "gemarc-atlas-sealed-runs/1",
  "modelSha8":  "<8 位十六进制，model_sha8>",
  "lang":       "zh-CN" | "ja-JP" | "en-US",
  "baselines":  { "<caseId>": { "state": "sealed" | "confirmed", "run": <引擎的运行结果> } },
  "pairs":      [ { "interventionId", "caseId", "state": "sealed" | "confirmed",
                    "result": <run_causal 的结果>, "chain": <causal_chain 的因果链> } ],
  "diagrams":   [ <图单元，见第 4 节> ],
  "issues":     [ <图单元的问题记录> ],
  "withheld":   { "cases": ["<caseId>", ...], "interventions": ["<interventionId>", ...] },
  "provenance": { "ledger": "ok" | "unverified", "reason": "" | "edited" | "unreadable" | "deleted" | "rolled-back" }
}
```

- `run`、`result`、`chain` 的字段名与含义见 `src/gemarc_mcp/atlas/engine/README.md` 第 5 节；查看器按原样播放。
- `baselines` 的键与 `pairs` 都按 `caseId`、`interventionId` 的字典序排列。同样的输入得到同样的字节。
- `withheld` 只列出 id，没有任何结果。查看器对 `withheld` 里的条目（以及 `baselines` 里没有的用例）显示空状态：「先写期望」。`interventions` 里列出的干预，要么本身没有封存，要么它适用的用例都没有封存基线。
- `provenance.ledger` 为 `unverified` 时，账本被改过、删过、被旧副本替换或读不出来（见 `oracle.py`）；页面应把徽标写成「来源未验证」。徽标文字由 `oracle.attestation` 给出，三种语言各一份。

## 4. 图单元

`diagrams` 里的单元与 `derive/diagrams.py` 的结构单元同形（`id`、`type`、`title`、`covers`、`requirementIds`、`evidenceLevel`、`evidenceIds`、`data`、`mermaid`、`issues`），新增两种类型，只由已封存的运行画出：

| `type` | `id` | 内容 |
|---|---|---|
| `sequence` | `sequence-<caseId>`，对比为 `sequence-<interventionId>-<caseId>` | 轨迹条目的 Mermaid `sequenceDiagram`（最多 60 条，被截断时有 INFO 问题记录）；失败的结果画成带叉的箭头 |
| `staterun` | `staterun-<caseId>` | 活动最多的 6 个节点的状态变化，Mermaid `stateDiagram-v2` |

`staterun` 故意不叫 `state`：`state` 是文档通道的状态机结构图（`data.states` 等字段），两者不能混。`data.source` 为 `run` 表示图来自引擎结果。

## 5. 发布闸门里的预言机部分

```python
gate = oracle.release_gate(ws, project)      # 只读
```

```
{ "ok": bool,
  "blockers": [ { "kind", "id", "rule", "message" } ],
  "counts":   { "total", "sealed", "revised", "confirmed", "unregistered", "claimed", "honoured" },
  "attestation": { "class": "attested" | "unverified-provenance" | "none", "badge": "<项目语言的徽标文字>" },
  "modelSha8": "..." }
```

`kind`：`unsealed`（OR-000，未登记，或改过但还没有被写操作重新登记）、`revised`（OR-005，需要用户确认）、`no-verdict`（这份模型还没有判定，先运行 `simulate`；闸门自己不跑引擎）、`mismatch`（OR-001 或 IV-002，最近一次判定失败）、`provenance`（账本被改过、删过或被旧副本替换，只有用户确认每一条才能清除）、`vacuous`（OR-003：被断言的节点里只有参与者和非 Idle 的断言才计入下限，有期望却没有事件的用例也算空转，默认的完整检查同样会报；或 OR-004：针对这份模型跑过的突变体一个也没打破，或者根本造不出突变体，说明已封存的期望什么也没约束）、`no-bite`（断言成立的用例，针对这份模型还没有突变体运行，OR-004 无从判断；`oracle.bite_run` 即 `simulate --bite` 会存下结果，发布构建先做这一步）。OR-003 由闸门自己强制执行，不依赖调用者先跑检查；OR-004 依赖一次针对当前模型的突变体运行，所以闸门要求它存在（`no-bite`）。

发布构建的其余条件（Implemented 的证据逐条通过、没有未写完的标记、浏览器闸门）属于构建通道。预言机部分是「已封存或已确认」，不要求用户确认每一条；`oracle-sheet.md` 列出尚未确认的条目，用户在 `atlas/oracle-confirm.txt` 里确认，工具从不写这个文件。

## 6. 这是证明，不是认证

账本记录的是顺序：期望在任何结果出现之前登记，登记之后是否看过结果、是否改过。每一个失败的判定都记为不一致（回复装不下的也记，因为计数和通过清单已经泄露了哪些条目失败）；事件相同的用例（步骤名和位置不算）或增量相同的干预是同一个暴露单元，其中一个有了不一致或揭示，其余副本立刻变成 `revised`。账本旁边有一个锚点文件 `.atlas/oracle-anchor.json`（最近一次写入的 tick 与校验值）：整份账本被换成旧副本会得到 `rolled-back`，只重算校验值而不动锚点会得到 `edited`；任何一次 put 之后账本文件都存在，所以此后账本缺失就是 `deleted`。它不能阻止一个有 shell 的写入者重算校验值和锚点、把整个 `.atlas/` 换成旧版本、读取印出来的确认单再抄一遍，或者直接运行引擎。这些情况与能留下的痕迹见 `tests/atlas/test_oracle_cheaters.py` 的开头。
