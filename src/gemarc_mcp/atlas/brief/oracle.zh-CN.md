# 期望（oracle）：期望是意图

- `expect` 行（以及干预的 `class` 和 `terminal`）写的是应该发生什么。依据需求或你读到的代码，在任何仿真之前写。绝不要把引擎结果抄进去。
- **终态怎么由事件推出**（引擎规则，不是结果）。每个节点从 `Idle` 开始，第一个事件的 `from` 从 `Running` 开始。`Call` 或 `Message` 让接收方变 `Running`（带 `ack` 则 `Completed`），没有 `Return` 它就一直是 `Running`。`Return` 让发送方 `Completed`；`Failure` 让接收方 `Failed`；重试用尽让接收方 `Failed`；`Compensation`、`Recovery` 让发送方 `Recovered`。从未被触达的源不能发事件（被跳过）。没有事件碰过的节点保持 `Idle`。
- **封存。** 写入类调用（put、simulate、build）在期望所指的每个节点都已定义、`outcomes` 里的每个 ruleId 都属于该用例的某个事件时给它盖章，否则它等着：put 回复写 `oracle: waiting`，`simulate` 回答 `OR-000 not registered` 且不给判定，`check` 打印 OR-000。
- **判定是盲的。** `simulate`（默认 `detail=count`）只给通过或失败，以及有多少个被断言的节点不一致，不说是哪些。每个失败的判定都会记为不一致，即使回复列不下。`detail=ids`、`detail=trace` 要指明用例或干预，属于揭示，展示时才记录。已修订条目的判定会注明：它不算已封存。
- **状态。** `sealed`：在它的任何结果出现之前登记；出结果之前改它，原位重新封存。`revised`：在不一致报告、揭示、嵌入了轨迹的构建之后改过（或删掉又加回）。副本共享：事件相同的用例（步骤名不算）是同一个单元，其中一个有不一致或揭示的记录，其余副本立刻 `revised`；增量相同的干预同理。只改用例的事件，保持 `sealed`。`confirmed`：用户批准过。`by=user` 只有确认后才算数。
- **防空转。** 被断言的节点至少 max(2, 参与者的 35%) 个（OR-003）；对事件从未碰过的节点断言 Idle 不算；有期望却没有事件的用例是空转。abnormal、recovery、combination 类用例只断言 Completed 会得到 OR-002。OR-004：停用一个参与者或删掉一个事件，断言必须被打破，否则什么也没约束；发布闸门要求做过这次突变体运行（`simulate --bite`，发布构建会做）。
- **完成。** 每条期望都 sealed 或 confirmed，且针对当前模型的判定通过（改过模型要重跑 `simulate`）；`revised` 要用户处理。构建会写 `atlas/dist/oracle-sheet.md`（每条未确认的期望一行，外加 APPROVE-ALL）。由用户在 `atlas/oracle-confirm.txt` 里确认；你永远不要写这个文件，也不要改 `.atlas/oracle.json`（账本被动过，项目会标成来源未验证）。
- `check mode=full rule=OR` 还会从账本给出 OR-001、OR-004、OR-005。
