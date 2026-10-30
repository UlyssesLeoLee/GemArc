---
name: gemarc-atlas
description: Use when the user asks to "创建5D设计", "做5D设计资料", or "create 5D design" for a system (as-is from code and documents, or to-be from a requirement), or to continue, repair or review an existing GemArc Atlas project. Drives the gemarc_atlas_* MCP tools (toolset atlas) from one sentence to a validated, self-reviewed model without inventing facts.
---

# GemArc Atlas：从一句话到经校验的 5D 设计资料

你只写**事实**（紧凑的 `.atl` 文本切片）；固定且经过测试的代码负责推导、检查和渲染其余一切（边 id、端口、状态、文档、图、提示词、模拟）。不要自己写 HTML 或文档，不要凭空设想事实：不确定就标 TBD 或降低证据等级（`verif=`）。

## 触发

用户说"创建5D设计"、"做5D设计资料"、"create 5D design"，或要求继续、修复、自审一个已有的 Atlas 项目。先确认是 as-is（根据真实代码和文档还原现状）还是 to-be（根据用户给的需求设计新系统）；用户没说时：给了需求原文就按 to-be，没给就按 as-is；需求原文没给的 to-be 先问用户要。

## 入口：一个提示，一条流程

MCP 提示 `gemarc-atlas-create`（参数 `requirement`、`mode`、`project`、`lang`）就是这条流程的驱动文本：Claude Code 里是斜杠命令 `/mcp__gemarc_atlas__gemarc-atlas-create`；Codex 和 Hermes 用 `python scripts/atlas_mcp.py get-prompt gemarc-atlas-create mode=asis project=<名字>` 取出文本照着做。没有这个提示时按下面的流程做，效果相同。

## 工具

| 工具 | 用途 |
|---|---|
| `gemarc_atlas_brief` | 工作指令，开始时读一次（`section=all`），需要细节再取单节 |
| `gemarc_atlas_init` | 建项目（只一次，已有项目就不要再建） |
| `gemarc_atlas_put` | 保存一个切片：无 ERROR 提交，有 ERROR 存为红稿，不丢任何文本 |
| `gemarc_atlas_check` | 只校验不写入；`mode=full` 是整个项目的最终检查 |
| `gemarc_atlas_status` | 现在到哪一步、下一步写什么；丢了上下文就用它续上 |
| `gemarc_atlas_index` | 看已经有什么（节点、房间、需求、用例），代替重读切片 |
| `gemarc_atlas_simulate` | 对已登记的期望做盲测判定（先封存）：只说通过与否和不符的**个数**；`detail=ids`、`trace` 是被记账的揭示 |
| `gemarc_atlas_doc` | 派生文档的结构大纲（每章一位数字，2 表示有 NOT-AUTHORED 空位）或某章正文；从不显示引擎结果 |
| `gemarc_atlas_outline` | as-is 读源码的辅助：一个文件的 def、class、标题行，带行号和唯一锚点（`root` 是别名，不是路径） |
| `gemarc_atlas_evidence` | 把证据行对着真实文件核验并盖印；失败行给修复建议，建议从不自动应用 |
| `gemarc_atlas_build` | 写 `dist/`：页面、文档、图、提示词，和交给用户的 `oracle-sheet.md` |
| `gemarc_atlas_verify` | 对已构建的页面跑浏览器门，结论绑定这一页；SKIPPED（没有浏览器）不算通过 |
| `gemarc_atlas_review` | 自审，恰好一次：`action=report` 只读，`action=record` 写一次记录（见下） |

写入类工具（`gemarc_atlas_init`、`gemarc_atlas_put`、`gemarc_atlas_simulate`、`gemarc_atlas_evidence`、`gemarc_atlas_build`、`gemarc_atlas_verify`，以及 `gemarc_atlas_review` 的 `record`）需要用户在服务器配置里设置 `GEMARC_ALLOW_WRITE=1`；没有写权限时它们返回 `GEMARC_AUTH_002`，把这件事告诉用户，不要绕过。完整契约见 `docs/atlas/ATLAS_TOOLS.md`。

## 流程

1. `gemarc_atlas_brief(section="all")` 读一次。
2. `gemarc_atlas_init`（项目名小写、数字、连字符），把需求原文原样保存到回复里 `path` 下的 `user/requirement.txt`（to-be 必须；as-is 也保存，供引用）。
3. 循环：`gemarc_atlas_status`（`view=next`）-> 只写 `next` 第 1 条点名的那一个切片 -> `gemarc_atlas_put`。每个切片约 11 个节点连同它们的边和证据（4 到 7 KB）；put 的回复自带 `next` 的第一行，照它做；`status` 只在红稿之后、续接、大约每 5 次 put 时再看。
4. put 返回 `invalid`、你的文本被存为红稿时，用 `put mode=patch` 只发送出错的行（先改列出的第一条错误，后面的往往是它的后果），不要整片重发，不要重读切片。细节见 [references/flow.md](references/flow.md)。
5. as-is 先读真实代码与文档取证：每个已实现的事实都要引用真实的锚点（源码里的一行原文）；读不到的标 TBD 或降低 `verif=`，绝不猜测。`gemarc_atlas_outline` 列出文件的 def、class、标题行和唯一锚点，直接抄锚点而不是重读。to-be 的证据锚点是 `user:<名字>` 加需求原文里的那句话。

## 收尾：各道门（phase 为 `build`）

结构写完后 `status` 的 phase 是 `build`，它的 `next` 每次点名还红的那一道门。顺序和要点：

1. `gemarc_atlas_simulate`：先封存期望再给盲测判定。不要为了凑通过去要 `ids` 或 `trace`，更不要照引擎结果改期望（改过的会变成 `revised`，要用户确认）；不符时先查用例事件有没有写错。
2. as-is：`gemarc_atlas_evidence`：逐条修复 FAIL（`put mode=patch` 改文件或锚点），读不到源码就降低 `verif=`；SKIP 表示证据根没有配置（操作者的 `GEMARC_ATLAS_ROOTS`）：这时 `status` 给出 `needs-user`，停下，把 `needsUser` 原样告诉用户（操作者设置证据根，或用户同意降低 `verif=`），不要反复调用 `evidence`。
3. `gemarc_atlas_build`：写 `dist/` 和 `oracle-sheet.md`。
4. `gemarc_atlas_verify`（用默认宽度，只传一个宽度不算通过）：浏览器门。FAIL 照它列出的问题修复，重新 build、verify。回复 SKIPPED 表示没有浏览器，页面没有被验证：停下，告诉用户装 Edge 或 Chrome，或由用户把理由写进 `atlas/waive-browser.txt`；你绝不写这个文件。
5. 可选：`gemarc_atlas_doc` 看大纲，`markers` 大于 0 说明还有 `[NOT-AUTHORED]` 空位。

## 停止条件

- `status` 的 phase 是 `needs-user`：停下，把 `needsUser` 原样问给用户并等待回答。**绝不替用户决定，绝不编造事实。**
- 同一个切片最多修复 3 轮；第 4 次仍红，停下并报告 `blocked`，附最后一次诊断。
- `put` 总次数预算 = 8 + 2 x ceil(节点数/11)（节点数先估计，估不出按 40，即 16 次）；用完预算还没到 review 阶段，停下并报告 `blocked`，附最后一次 status 与诊断。
- `done` 为 true：写最终报告，先读 `status` 回复里的 `openBlocks`（仍然打开的 BLOCK）、`waived`（被用户豁免的门：页面未验证）和 `next` 里的 `await` 行，逐条写进去。

## 自审，恰好一次

phase 到 `review`（上面各道门都绿，只差自审）时：

1. `gemarc_atlas_review`（`action="report"`）取机械报告和抽样行（没有先 `report`，`record` 会被拒绝）。
2. 用真实源码或需求原文核对抽样行和报告里的每一项；客户端支持子代理时，交给一个全新的子代理，只给它报告和抽样（它没有被你的推理带偏）。
3. 只修复报告里列出的问题（`put mode=patch`；BLOCK 先于 GAP，INFO 只有确实是错误才改）；抽样行里核对出的不符也是发现，同样修。然后 `gemarc_atlas_review`（`action="record"`，`notes` 必填，写 `fixed=<编号>; open=<编号>` 并说明查了什么）。`record` 只在 phase 为 `review` 时被接受，且只有一次；修复改了模型，门会变红，按 `status` 点名的顺序重做 simulate、evidence、build、verify 再 `record`（这不是第二轮自审）。
4. 自审只做这一次：`record` 之后只修已列出的发现，不得再开第二轮自审；仍然存活的 BLOCK 发现写进最终报告交给用户，不要循环。`report` 在 `record` 之后仍可读，它的 `record` 字段说明记录是否仍然有效。

## 最终报告

只写工具确认过的事实：`done` 与 phase；切片清单、`put` 次数、修复轮数；自审发现及处理结果；`needs-user` 与 `blocked` 项；各道门的结果（盲测判定、证据核验、文档、构建、浏览器门；浏览器门若是 SKIPPED 或被用户豁免（`status` 的 `waived`）要如实写出，页面未验证）；`openBlocks` 里每一条仍然打开的 BLOCK；已就绪的维度（1D-5D）；所有 TBD 与低证据等级的项；把 `atlas/dist/oracle-sheet.md` 交给用户，写明有几条期望等他确认（他勾选行或保留 `APPROVE-ALL` 行，写进 `atlas/oracle-confirm.txt`，这个文件只有用户写）。不要声称没有被工具确认的结论。

## 远程只读工具集

连接的是 `atlas_remote` 时只有 `gemarc_atlas_brief` 和只读的 `gemarc_atlas_check`，什么都不保存：切片写在你自己的本地文件里，每写完一个就 `gemarc_atlas_check(text=..., known=...)`（`known` 是其他切片已定义的 id，每行一个 `<kind> <id>`）。它的结论是 advisory，只覆盖语法和切片内部引用；权威检查在本地服务器的 `mode=full`。
