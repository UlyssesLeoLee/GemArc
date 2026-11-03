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

## 已实现的契约与词表（ADR-013）

上面的章节是目标设计。**实际生效**的契约与词表是随包发布的那一套，Python 校验器、浏览器运行时与站点共用同一份事实：

| 类别 | 位置 | 内容 |
|---|---|---|
| 契约（JSON Schema） | `src/gemarc_mcp/scaffold/contracts/` | `canonical-model` / `node` / `edge` / `evidence` / `space` / `view` / `scenario` / `workflow` |
| 词表 | `src/gemarc_mcp/scaffold/vocab/` | `entity-kinds`(20) / `evidence-levels`(6) / `edge-types`(20) / `diagram-types`(21) / `relation-classes`(4) / `space-levels`(3) / `case-categories`(5) / **`case-types`(14)** / **`workflow-kinds`(22 节点类型 / 11 边类型 / GWF-IR 字段目录)** |
| 生成物 | `runtime/core/vocab-data.js` | 由 `python -m gemarc_mcp.scaffold.sync` 从词表生成；`tests/test_scaffold_vocab.py` 断言两者不会漂移 |

### v5.0 规格带来的增量（全部可选，既有模型继续有效）

- **节点**：`qualifiedName`、`sourceLocation`（repository / commit / path / startLine / endLine）、`version`。
- **边**：`version`。
- **证据**：`source.path` / `source.startLine` / `source.endLine`。
- **工作流**：§17 的 `version`、`entryPoints`、`dataSchemas`、`executionPolicy`、`errorPolicy`、`permissions`、`metadata`；节点侧的 `inputs` / `outputs` / `preconditions` / `postconditions` / `stateTransitions` / `timeoutPolicy` / `retryPolicy` / `errorHandlers` / `sideEffects` / `idempotency` / `implementationRef`；边侧的 `dataMapping` / `priority` / `errorRoute`；端口侧的 `direction` / `cardinality` / `required` / `acceptedTypes`。
- **Case**：§13 case_contract 的 `initialState` / `inputEvents` / `preconditions` / `executionRules` / `expectedOutcomes` / `terminationConditions` / `assertions` / `evidenceIds` / `assumptions`，加 `caseType`（14 种分析类型）与 `reality`（Observed / ImplementedModel / DesignedModel / Hypothetical）。

### 字段别名

规格 §3 用 `type` / `label`，§17 用 `nodeType` / `edgeType` / `guard` / `portId` / `caseId`。这些拼写**被接受并归一**到正本拼写：

| 别名 | 归一到 | 适用范围 |
|---|---|---|
| `type` | `kind` | 节点 |
| `label` | `name` | 节点 |
| `direction` | `directed` | 边（`undirected` → false） |
| `nodeType` / `nodeId` | `kind` / `id` | 工作流节点 |
| `edgeType` / `edgeId` / `sourceNodeId` / `targetNodeId` / `guard` | `kind` / `id` / `sourceId` / `targetId` / `condition` | 工作流边 |
| `portId` | `id` | 端口 |
| `workflowId` | `id` | 工作流 |
| `caseId` / `name` | `id` / `title` | Case |

§17 的 PascalCase 类型名（`Task`、`ControlFlow`…）会先映射回正本 id 再校验。Python 与浏览器运行时两侧规则一致（`src/gemarc_mcp/model.py` 与 `runtime/core/model.js`、`runtime/workflow/workflow-model.js`）。
