# 架构 Schema 与数据字典

> 文档编号：DD-001 ｜版本：1.0 ｜日期：2026-10-25 ｜状态：设计基线（待评审）  
> 产品：GemArc MCP — 5D Design Scaffolding & Architecture Compiler  
> 说明：本文件为目标设计，不代表目标功能已实现或已测试。

## Schema 主结构（契约）

`meta`, `sources[]`, `evidence[]`, `nodes[]`, `edges[]`, `interfaces[]`, `constraints[]`, `viewpoints[]`, `views[]`, `scenarios[]`, `dynamicModels[]`, `causalModels[]`, `renderHints`, `lock`。

| 字段 | 类型 | 必须 | 规则 |
|---|---|---|---|
| `meta.schemaVersion` | SemVer | 是 | 用于迁移；不得默默降级 |
| `meta.projectId` | string | 是 | 仓库/项目范围唯一 |
| `nodes[].id` | string | 是 | 不可用展示名称作为键 |
| `nodes[].verification` | enum | 是 | confirmed/assumed/illustrative/unknown |
| `edges[].source/target` | string | 是 | 必须指向已有节点 |
| `evidence[].sourceRef` | object | 条件 | confirmed 必须关联可检查位置 |
| `views[].viewpointId` | string | 是 | 指向已定义视点 |
| `views[].entityIds` | string[] | 是 | 引用已知实体 |
| `scenarios[].events` | array | 按 4D | 每条 event 序号唯一且排序稳定 |
| `causalModels[].rules` | array | 按 5D | 需前提、结果、依据、模型状态 |
| `lock.components` | object | 是 | 精确版本/完整性哈希 |

## 外键与一致性

- 节点与边 ID 跨维共享，视图不得复制或重定义事实对象。
- 所有字符串 ID 使用 UTF-8 NFC 规范化，比较精确区分大小写。
- 版本迁移保留 `originId`；改 ID 要产生映射，绝不可偷偷重建导致追溯断裂。
- Edge 至少有 `id, source, target, kind, label, verification`；与 Node 相同的证据要求。
- `evidenceState` 与 `simulationResultType` 独立：模型假设不能冒充观测日志。

## 结构示例

参见 `../08_附录/schema/architecture.schema.json` 和 `../08_附录/examples/architecture.example.json`。示例仅为最小样例；真实验证器需校验全部语义引用和跨对象规则。
