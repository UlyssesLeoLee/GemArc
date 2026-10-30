# 派生项：不要写（写了就是 DV-001）

格式拒绝的键，按段列出：
{{derived}}

展开器会推导下列内容，每一项都有出处记录：
- 空间：每栋楼内楼层 level 唯一；房间的 tier 和 area、楼的 trust 由其节点继承。
- 证据：sourceKind、verification 和 lineRange（验证器根据 `anchor` 盖章；你只写 id | file | anchor | topic，仓库名用 `@set repo`）。
- 节点：verification 默认值（有代码证据为 Implemented，有其他证据为 Designed，否则 Hypothetical），端口 `<id>:in` 和 `<id>:out`。
- 状态机：states、terminal、转移 id、对应的 `owns` 边（写 `owner=`）。
- 边：id 为 `src:rel:tgt`，以及 type、directed、端口、static、默认 verification；`ev=^` 表示取两端节点的证据。
- 用例：事件 seq 等于行序，默认 policy，linked tests（来自需求）。
- 干预：insert_element 的链和元素端口（写 `rewrite=` 与 `use=<kind>`）；combined 的各部分会被展平。
- 需求：edges 默认取其节点之间的边。
- 下游产物，永远不手写：图、轨迹、因果链、文档、提示词、布局、颜色、标签。

仍由你写：`verif=` 覆盖、期望、ruleId 步骤名、说明文字、topic。
