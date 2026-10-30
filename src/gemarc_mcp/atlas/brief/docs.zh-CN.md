# 文档：由你写的事实推导出来

- REQ、BD、DD 文档（Markdown）、Mermaid 图和提示词都由模型生成，你不要自己写。没有事实的空位会打印 `[NOT-AUTHORED:slot]`；`status view=report` 列出未填的空位（BLOCK 表示文档不完整）。
- 填空位的事实：节点的 `sub`（一行）、边的 `label`、用例的 `title`、需求的 `title` 与 `source`、证据的 `topic`。
- 第 9 章需要一条政策：`## policies: id | text`，带 `src=<需求或 tbd 的 id> kind=nfr`；第 10 章用 `kind=constraint`。`by=user` 只用于用户本人说过的话。
- Entity 和 Table 节点需要 `props={"columns":[...]}`（DD 层级）。
- 每个房间都要有楼层和楼；每个节点都要有房间（3D）。
- `doc(project, unit, level, lang, chapter, page)` 只给结构，不显示任何引擎结果。默认返回一个 `unit`（`system`、`req:<id>`、`case:<id>`、`node:<id>`）在 `level`（REQ、BD、DD）上的大纲：`ch` 每章一位数字（0 空状态，1 有内容，2 有内容但含 NOT-AUTHORED 空位），`markers` 是挡住 `done` 的空位个数。给 `chapter`（1-12）返回该章正文（每个 `page` 约 15 KiB）。
