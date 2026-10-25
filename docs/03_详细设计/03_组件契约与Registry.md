# 组件接口、Registry 与版本管理详细设计

> 文档编号：DD-003 ｜版本：1.0 ｜日期：2026-10-25 ｜状态：设计基线（待评审）  
> 产品：GemArc MCP — 5D Design Scaffolding & Architecture Compiler  
> 说明：本文件为目标设计，不代表目标功能已实现或已测试。

## 组件登记结构

```json
{
  "id":"gemarc.node.gem",
  "version":"1.0.0",
  "dimensions":["3D","4D","5D"],
  "capabilities":["focus","autoLabel","relations"],
  "entry":"runtime/scene-3d/GemNode",
  "inputSchema":"gemarc://schemas/component/gem-node/1.0.0",
  "events":["onSelect","onEdgeFocus"],
  "dependencies":{"three":"locked"},
  "license":"REVIEW_REQUIRED",
  "integrity":"sha256:<build-generated>"
}
```

登记文件示例中 `REVIEW_REQUIRED` 是**尚未核对**，不得作为可发布许可。真实组件须记录 License SPDX ID 与第三方 Notice 路径。

## Registry 选择算法

1. 根据 View 所需能力和维度检索候选组件。
2. 检查 Schema major、运行时/资源版本、客户端能力。
3. 检查许可证策略和散列完整性，未知许可直接阻止正式发布。
4. 根据固定优先级（标准内置 > 经审核扩展 > 临时自定义）选择。
5. 在 lock 中记录精确版本和哈希，build 可复现。
6. 未找到合格组件：返回 `MISSING_COMPONENT`，向 Agent 提供扩展接口契约；禁止静默生成相似但不可测试的全新标准组件。

## 事件边界

公共事件：`entitySelected(entityId, originDimension)`、`relationFocused(edgeId)`、`caseChanged(caseId)`、`interventionChanged(id, value)`。事件路由通过 ID 幂等订阅并在卸载时释放。Vue/React 等框架并非必要预设，Viewer 保持平台中立。

## 命名与标签

节点/关系名称可随角度调整面向与位置，但文字不改语义；长文字折叠为短名，详情面板显示全称；远距 LOD 仅显示关键节点与关系摘要。
