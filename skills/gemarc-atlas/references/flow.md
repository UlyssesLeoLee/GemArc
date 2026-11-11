# 撰写循环的细节

## 切片怎么分

按阅读顺序编号，名字 `[a-z0-9-]{1,40}`：

- `00-space`：楼栋、楼层、房间。
- `10-structure`、`11-structure` ...：节点、边、证据、状态机和转移；每片约 11 个节点。
- `20-scenarios` ...：测试、用例、事件、`expect`、干预、需求、待定项、策略。

先写被别人引用的，后写引用别人的没有关系：引用了还没定义的 id 只是"待定"（INFO），写定义它的切片时自然解决，不要为它停下来改。

## 读 `gemarc_atlas_status` 的结果

`next` 至多 10 行，行首数字是优先级：1 红稿或错误，2 BLOCK 槽位，3 未定义的 id，4 需求缺口，5 无期望的用例，6 不可达状态，7 覆盖；最后一行是 1D-5D 的就绪情况。`blockers` 是让 `done` 为 false 的原因（完整的短记号，如 `gate-evidence:5/5 unverified`），`needsUser` 是只有用户（或操作者）能决定的事项，写成完整的句子，原样交给用户；你接下来要做什么（保存他的原话、写 policies 行、交出 oracle 表）在 `next` 里。`waived` 列出被用户豁免的门：豁免不是验证。phase 取值：`collect`（还没写）、`repair`（有红稿或错误）、`author`（有 BLOCK 槽位或维度没就绪）、`needs-user`、`build`（某个适用的门是红的：simulate、evidence、build 或 verify，`next` 点名是哪一道）、`review`、`done`。

## 修复

- 红稿不丢：`gemarc_atlas_put` 失败后你的整段文本在 `.atlas/red/` 里，补丁打在红稿上。
- 补丁只带改动的行：同一身份的行替换（身份：id；边 `from|rel|to`；`expect` 的用例；转移的状态机加 `from|to`），`-id` 删除一行，事件块 `@set case=X` 替换该用例的全部事件。
- 想看更多诊断用 `gemarc_atlas_check`（`slice=名字`，`offset=N`）；它不写入。
- 带 `base=<sha8>` 的 put 在切片中途被别人改过时返回冲突，什么都不写：先用 `gemarc_atlas_index` 看现状再决定。

## 省 token

不要重发有效的行，不要重读切片，用 `gemarc_atlas_index` 看已有内容；put 的回复自带下一步（`next` 一行），不必每次都调 `gemarc_atlas_status`；引用唯一的锚点而不是粘贴代码；sub、label、title 各写一行短句，文档由代码生成，你只提供事实。

## 最终报告的模板

- 结论：`done` 或 `blocked` 或 `needs-user`，phase。
- 过程：切片 N 个，put M 次，修复 K 轮，每个切片最多几轮。
- 自审：发现几条、改了几条、仍存活的 BLOCK（`status` 的 `openBlocks`）。
- 要用户处理：needs-user 的问题、blocked 时的最后一次诊断。
- 各道门：盲测判定、证据核验、文档、构建、浏览器门（SKIPPED 或被用户豁免要写出，`waived` 字段）。
- 要交给用户的：`atlas/dist/oracle-sheet.md`（几条期望等确认）。
- 没有覆盖的部分：所有 TBD 和低证据等级的项。
