# MCP 工具与资源详细设计

> 文档编号：DD-002 ｜版本：1.0 ｜日期：2026-10-25 ｜状态：设计基线（待评审）  
> 产品：GemArc MCP — 5D Design Scaffolding & Architecture Compiler  
> 说明：本文件为目标设计，不代表目标功能已实现或已测试。

## 调用模式

- MCP 规范目标 `2026-07-28`：请求 metadata 与能力发现由适配层负责，业务工具无会话状态；兼容旧协议需单独适配并测试。
- 工具须有 JSON Schema input/output，服务端一定执行校验；不信任工具描述中的模型推测参数。
- 对有写作用工具，应要求 `authorizedRoot`/`dryRun`/`overwritePolicy`，且仅实际可信授权可以批准越权写入。

## 11 个工具详细契约

| Tool | 核心必填参数 | 主要输出字段 | 执行约束 |
|---|---|---|---|
| `gemarc.inspect` | sources[] | evidenceIndex, candidateFacts, diagnostics | 只读、限大小 |
| `gemarc.plan` | projectId, goals[] | viewPlan, componentNeeds, gaps | 不修改事实 |
| `gemarc.assets` | query | assets[], versions, licenses | 分页与缓存 |
| `gemarc.init` | authorizedRoot, templateRef | projectPath, lockFile | 禁止覆盖非空目录 |
| `gemarc.model` | modelPatch, baseVersion | newVersion, conflicts, diagnostics | optimistic concurrency |
| `gemarc.validate` | modelRef, strict | errors[], warnings[], summary | 不改变模型 |
| `gemarc.build` | modelRef, viewPlan, outputRoot | buildId, status, outputs | 输出暂存、原子发布 |
| `gemarc.simulate` | scenarioId, interventions[] | trace[], result, confidence | 事件/循环上限 |
| `gemarc.verify` | buildId, suites[] | junitRef, snapshotRef, result | 环境可追溯 |
| `gemarc.diff` | baseRef, targetRef | changedIds, impactSet | 只读比较 |
| `gemarc.export` | buildId, formats[] | archives[], checksums | 资产许可拦截 |

## Tool 错误结构

```json
{"status":"invalid","diagnostics":[{"id":"GEMARC_MODEL_001","severity":"ERROR","entityId":"edge.auth-db","message":"source 不存在","suggestion":"检查边的实体 ID"}],"artifactRefs":[]}
```

## 访问控制示例

- 任何请求 `../../private` 或绝对路径超出根目录 → `GEMARC_AUTH_001`。
- 自定义组件中包含系统命令脚本但未获准许 → `GEMARC_COMPONENT_004`。
- 模型依赖未知因果规则却要求输出“已验证结论” → `GEMARC_CAUSAL_003`。

## Resources / Prompts

Read-only Resources 由精确 URI 引用，Prompt 是设计任务引导，不隐式写文件。Resource 与工具错误均应返回结构化信息且不泄露内部绝对路径。

[工具调用序列图](../06_图表/03_mcp_operation_sequence.mmd)
