# 变更控制、配置管理与 ADR

> 文档编号：PM-003 ｜版本：1.1 ｜日期：2026-10-30 ｜状态：设计基线（待评审）  
> 产品：GemArc MCP — 5D Design Scaffolding & Architecture Compiler  
> 说明：ADR-001～007 为目标设计，不代表目标功能已实现或已测试；ADR-008～011 记录已经实现的接口决策，仍待评审批准。

## 版本控制原则

源代码、Schema、设计文档、组件清单、示例与测试一起纳入版本控制；生成的构建产物如独立发布，另有 checksum 和 manifest。

## ADR（架构决策记录）

| ADR | 决策 | 决策状态 | 理由 |
|---|---|---|---|
| ADR-001 | MCP Adapter 不承载领域模型 | 建议采纳 | 便于 CLI 与未来 API 复用 |
| ADR-002 | 统一模型为唯一事实来源 | 建议采纳 | 防止跨维事实冲突 |
| ADR-003 | Component Registry 独立于 Viewer | 建议采纳 | 固化可复用资产和许可 |
| ADR-004 | 编译前锁定组件版本 | 建议采纳 | 保证可重复产物 |
| ADR-005 | 4D/5D 使用声明式规则 | 建议采纳 | 防止把用户输入当可执行脚本 |
| ADR-006 | 现有 Python/JS 资产渐进迁移 | 建议采纳 | 降低对仓库的回归风险 |
| ADR-007 | MCP 2026 版本优先，适配旧版 | 待验证 | Host 支持矩阵因产品版本不同 |
| ADR-008 | 工具名使用 `gemarc_` + `[a-z0-9_]`（由点号形式改名，不保留别名） | 建议采纳（已实现） | Claude API / OpenAI 函数名字符集；Codex、Hermes 改写点号，Hermes 对重名工具两个都丢弃 |
| ADR-009 | 远程部署使用无状态工具集（8 个只读工具，模型内联传入） | 建议采纳（已实现） | Vercel 无持久文件系统、无调用者身份 |
| ADR-010 | 远程端点：可选 Bearer token，未设置则无认证；GET `/mcp` 返回 405 | 建议采纳（实现见 `api/index.py`，本文未随该文件验证） | claude.ai / Desktop 无法携带静态 Bearer；Codex 把 404 当作会话过期 |
| ADR-011 | 工具结果不返回 `structuredContent`，不声明 `outputSchema` | 建议采纳（已实现） | Codex 用 `structuredContent` 替换 `content`；Hermes 去重并忽略 `outputSchema` |

### ADR-008 工具名：`gemarc_` 前缀 + `[a-z0-9_]`

- **决策**：把所有工具名从点号形式改为下划线形式：`gemarc.inspect` → `gemarc_inspect`，`gemarc.plan` → `gemarc_plan`，`gemarc.assets` → `gemarc_assets`，`gemarc.contracts` → `gemarc_contracts`，`gemarc.brief` → `gemarc_brief`，`gemarc.scaffold` → `gemarc_scaffold`，`gemarc.init` → `gemarc_init`，`gemarc.model` → `gemarc_model`，`gemarc.validate` → `gemarc_validate`，`gemarc.build` → `gemarc_build`，`gemarc.simulate` → `gemarc_simulate`，`gemarc.verify` → `gemarc_verify`，`gemarc.diff` → `gemarc_diff`，`gemarc.export` → `gemarc_export`；新增的 `gemarc_new_model`、`gemarc_render` 同样遵循。
- **原因**：（1）Claude API 与 OpenAI 函数调用要求函数名匹配 `[a-zA-Z0-9_-]{1,64}`，点号在 MCP 规范里合法，但这些接口会拒绝或改写；（2）Codex 把不属于 `[A-Za-z0-9_]` 的字符改成 `_`（冲突时加 sha1 后缀），Hermes 同样改成 `_`，且规范化后重名的工具**两个都被丢弃**；（3）保留 `gemarc_` 前缀：三个客户端虽然都按服务器名加命名空间（`mcp__<服务器>__<工具>`），但不加命名空间的客户端、把 MCP 工具直接转成函数的其他 agent 框架、以及同一会话里挂多个服务器的场景，仍需要前缀来避免撞名并标明归属；代价是名字长 7 个字符，加上最长的客户端前缀仍只有 29 字符（≤ 64）。
- **后果**：这是破坏性改名，**不保留旧名别名**：若同时暴露点号别名和下划线名，Hermes 规范化后两者重名，会让两个都消失。文档、skill、示例与测试同步更新；用户配置里按旧名写的白名单（Claude 的 `permissions.allow`、Codex 的 `enabled_tools`、Hermes 的 `tools.include`）需手动改。`src/gemarc_mcp/registry.json` 里的 `gemarc.scaffold*` 是组件 id 而非工具名，不受影响。`tests/test_tool_surface_docs.py` 禁止旧名在本节之外再出现。

### ADR-009 远程使用无状态工具集

- **决策**：`server.create_server(toolset)` 提供 `full`（16 个，本地默认）与 `stateless`（8 个，远程默认，由 `GEMARC_TOOLSET` 选择）。`stateless` 只含不依赖工作区的工具，模型作为参数内联传入，新增 `gemarc_new_model` 与 `gemarc_render`。站点的固定件（约 620 KB）不进工具结果，由 `/kit/<profile>.zip` 提供；`gemarc_render` 只返回随模型变化的 `data/*.json`，合计不超过 24 KiB 时内联。
- **原因**：Vercel 没有持久文件系统，也没有调用者身份；把工作区放进 `/tmp` 既不跨实例共享，也无法把项目归属到调用者。把完整站点放进工具结果没有任何客户端接受得了（Claude Code 在 10k token 告警，Codex 约 10k token 截断）。
- **后果**：服务端不保存任何项目，没有写工具，因此不需要写授权；填写事实靠调用方 agent 用自己的文件访问能力读用户的仓库；模型大于内联上限时改走本地工具集。

### ADR-010 远程端点的访问控制

- **决策**：环境变量 `GEMARC_MCP_TOKEN` 已设置（可逗号分隔以便轮换）时，`POST /mcp` 要求 `Authorization: Bearer <token>`；**未设置时端点无认证**。GET `/mcp` 一律返回 405（带 `Allow: POST`）。`/healthz` 与首页公开。
- **原因**：claude.ai 与 Claude Desktop 的远程 connector 只支持 OAuth、无认证（或部分组织的 beta 静态 header），不能带静态 Bearer；Claude Code、Codex、Hermes 都能带 Bearer。要让所有客户端都能连，必须有「无认证」这一档，OAuth 未实现。GET 返回 405 是因为 MCP 允许服务器拒绝可选的 GET 流，而 Codex 把 404 映射成「会话过期」。
- **后果**：无认证档位下，知道 URL 的任何人都能调用 8 个纯计算工具（没有工作区、不存储任何数据，但消耗部署者的额度）；使用 claude.ai / Desktop 的部署者必须自己接受这个取舍。这些客户端行为来自对文档与源码的调研，未真机验证。

### ADR-011 不返回 `structuredContent`

- **决策**：每个工具只返回一个 JSON `TextContent`；`status` 为 `invalid` 或 `error` 时设置 `isError=true`，正文不变；不返回 `structuredContent`，也不声明 `outputSchema`。
- **原因**：Codex 一旦看到 `structuredContent` 就用它**替换** `content` 给模型；Hermes 对内容相同的 `content` 与 `structuredContent` 去重并忽略 `outputSchema`。只给 `content` 在三者上表现一致，同一份 JSON 无需发两份。
- **后果**：没有机器可校验的输出 schema，输出形状由 `docs/03_详细设计/02_MCP工具详细契约.md` 与测试约束；将来若引入 `structuredContent`，必须先在 Codex 上验证模型看到的内容。

## 变更流程

提交 CR→影响分析 FR/NFR/Schema/View/测试→设计变更评审→实现和测试→QA 审查→基线发布。凡变动稳定 ID 与 Schema major，需要迁移方案与回滚计划。变更决议与审批记在台账，未经审批不改成“已采用”。
