# MCP 工具与资源详细设计

> 文档编号：DD-002 ｜版本：1.1 ｜日期：2026-10-30 ｜状态：与当前实现一致（待评审）  
> 产品：GemArc MCP — 5D Design Scaffolding & Architecture Compiler  
> 说明：本文件描述**已实现**的 MCP 接口面，以运行中服务器的 `list_tools()`（`src/gemarc_mcp/server.py`）为准，`tests/test_tool_surface_docs.py` 校验工具名与本文一致。各客户端（Claude Code、Codex、Hermes Agent 等）的实际行为未经真机验证的部分，见 `integration-examples/` 中的逐项标注。

## 调用模式

- 传输：stdio（`python -m gemarc_mcp` 或控制台脚本 `gemarc-mcp`）与 Streamable HTTP（`api/index.py` 的 `/mcp`）。适配层是官方 MCP Python SDK 2.3.0 的 `MCPServer`，业务逻辑全部在 SDK 之外。业务工具无会话状态：每次调用自带全部输入，项目类工具通过工作区目录持久化。
- 工具有输入 JSON Schema（由函数签名生成，SDK 在调用前校验），**没有 `outputSchema`，也不返回 `structuredContent`**（见 ADR-011）。不信任工具描述中的模型推测参数。
- 写作用只有一道服务端强制的开关：环境变量 `GEMARC_ALLOW_WRITE=1`（缺省即关闭，写工具返回 `GEMARC_AUTH_002`）；路径一律限制在 `GEMARC_WORKSPACE_ROOT`（缺省 `~/.gemarc/workspace`）之内，越界返回 `GEMARC_AUTH_001`。曾设想的 `authorizedRoot`/`dryRun`/`overwritePolicy` 参数**未实现**：覆盖策略固定为「拒绝覆盖」（`GEMARC_IO_002`、`GEMARC_SCAFFOLD_002`），授权由用户的 MCP 配置而不是调用参数给出。
- 工具名只用 `gemarc_` 加 `[a-z0-9_]`（ADR-008），任何客户端加上前缀后仍 ≤ 64 字符（最长 `mcp__gemarc__gemarc_new_model` 为 29 字符）。

## 两个工具集

`server.create_server(toolset)` 选择工具集，环境变量 `GEMARC_TOOLSET` 在未显式指定时生效。

| 工具集 | 工具数 | 默认用于 | 特点 |
|---|---|---|---|
| `full` | 16 | 本地 stdio | 项目工具读写工作区；写工具需 `GEMARC_ALLOW_WRITE=1` |
| `stateless` | 8 | 远程（Vercel） | 模型随调用内联传入，服务端不保存任何东西，没有写工具（ADR-009） |

`stateless` 的 8 个工具是 `gemarc_brief`、`gemarc_contracts`、`gemarc_assets`、`gemarc_new_model`、`gemarc_validate`、`gemarc_simulate`、`gemarc_diff`、`gemarc_render`；其余 8 个只在 `full` 中注册。`gemarc_brief` 在 `stateless` 下返回的 `procedure` 与 `commands` 只会提到该工具集里存在的工具。

## `project` 与 `model` 二选一

`gemarc_validate`、`gemarc_simulate` 接受 `project`（工作区内的项目名）**或** `model`（内联的架构模型 JSON 对象）；`gemarc_diff` 对「基准」与「目标」各自二选一：`base_project` / `base_model`、`target_project` / `target_model`。两个都给、或都不给、或 `model` 为空对象，一律返回 `GEMARC_INPUT_003`（`isError` 为 true）。`stateless` 工具集里 `project` 参数仍然出现在 schema 中，但远程部署只有一个预置的示例项目 `demo`（服务启动时从包内复制进临时工作区），其它项目名得到 `GEMARC_IO_001`；真正的用法是只传 `model`。内联模型受 4 MiB 上限约束（`GEMARC_MODEL_002`）。

## 16 个工具详细契约

参数名与默认值取自服务器注册的函数签名；`*` 表示必填，其余都有默认值。「集」列：S = `stateless` 与 `full` 都有，F = 只在 `full`。

| Tool | 集 | 参数 | 主要输出字段 | 执行约束 |
|---|---|---|---|---|
| `gemarc_brief` | S | `project=""`, `section=""` | purpose, procedure, rules, slots, vocabulary, commands, alreadyDone, vocabDigest；带 `section` 时为 `{status, section, content, vocabDigest}` | 只读；`section` 取上述任一键，未知值 `GEMARC_BRIEF_001`；`stateless` 下 procedure/commands 只含该工具集里的工具 |
| `gemarc_contracts` | S | `query=""` | contracts[], vocabularies[], profiles[], slots[]；带 `query`（文件名）时多一个 `document` | 只读；`query` 只能是 `contracts[]` / `vocabularies[]` 里出现的文件名，路径、`..` 与不在列表里的名字一律 `GEMARC_CONTRACT_001` |
| `gemarc_assets` | S | `query=""` | name, licensePolicy, components[] | 只读；`planned` 状态的组件尚未实现 |
| `gemarc_new_model` | S | `system`* | model（空骨架）, slots[], next[] | 只读；`system` 非空且 ≤ 80 字符（`GEMARC_INPUT_001`） |
| `gemarc_validate` | S | `project=""`, `model={}`, `strict=false` | status, errors[], warnings[], summary, modelSha256 | 只读；`project` 与 `model` 二选一（`GEMARC_INPUT_003`）；不改变模型 |
| `gemarc_simulate` | S | `scenario_id`*, `project=""`, `model={}`, `disabled_nodes=[]`, `compare_baseline=false` | trace[], summary, truncation, evidenceClass；`compare_baseline=true` 时为 baseline, intervention, lostEvents, gainedEvents, notice | 只读；事件/循环有上限（`truncation`）；场景不存在 `GEMARC_DYNAMIC_006`，屏蔽模块不存在 `GEMARC_DYNAMIC_007`；规则式 what-if，不是因果识别 |
| `gemarc_diff` | S | `base_project=""`, `target_project=""`, `base_model={}`, `target_model={}` | nodes, edges, affectedNodes, baseSha256, targetSha256 | 只读；两侧各自二选一 |
| `gemarc_render` | S | `model`*, `profile="static"` | profile, validation（warning 最多回显 20 条，其余只给 `warningsOmitted`，总数在 `summary.warnings`）, inline, files[]（path, bytes, sha256, content）, kit{}, assemble, notice | 只读；`profile` 为 `static` 或 `vite`（`GEMARC_BUILD_005`）；校验未通过时直接返回校验结果；派生文件合计超过 24 KiB 时 `inline=false`，只给清单 |
| `gemarc_inspect` | F | `path`* | scannedFiles, scannedBytes, extensions, candidateFacts[], evidenceState | 只读；路径必须在工作区内；候选事实一律未确认 |
| `gemarc_plan` | F | `project`*, `goals=[]` | goals, views, dimensions, gaps | 只读；不修改事实 |
| `gemarc_scaffold` | F | `project`*, `profile="static"` | scaffold, manifest, model, slots, fixedFileCount, commands | 写；需 `GEMARC_ALLOW_WRITE=1`；已存在则拒绝（`GEMARC_SCAFFOLD_002`） |
| `gemarc_init` | F | `project`* | projectPath, model, notice | 写；需 `GEMARC_ALLOW_WRITE=1`；已存在则拒绝（`GEMARC_IO_002`）；示例模型不是真实事实 |
| `gemarc_model` | F | `project`*, `replacement_model`*, `expected_sha256`* | previousSha256, newSha256, validation | 写；乐观并发：`expected_sha256` 须取自最近一次 `gemarc_validate` 的 `modelSha256`，不一致 `GEMARC_CONFLICT_001`；无效模型整体拒绝、不写入 |
| `gemarc_build` | F | `project`*, `legacy_dist=""`, `profile="mvp"` | site, index, profile, viewer3D, validation, run, notice | 写；`profile` 为 `mvp`/`static`/`vite`；替换上一次生成的站点，拒绝覆盖非 GemArc 生成的目录（`GEMARC_BUILD_003`） |
| `gemarc_verify` | F | `project`* | missing[], model, profile, check, notExecuted | 只读；只检查文件完整性与 schema，浏览器/WebGL/性能不在范围内（`notExecuted`） |
| `gemarc_export` | F | `project`* | archive, sha256, notice | 写；未构建或静态验证失败时拒绝（`GEMARC_EXPORT_001`）；含符号链接时拒绝（`GEMARC_EXPORT_002`） |

### 与远程部署相关的两个工具

- `gemarc_new_model` 返回空模型与槽位清单，模型自己填写；服务端不读用户的仓库，填写事实用的是调用方 agent 自己的文件访问能力。
- `gemarc_render` 返回随模型变化的 `data/architecture.json`、`data/scenarios.json`、`data/capabilities.json`，以及固定件的描述 `kit`（`path` 为 `/kit/<profile>.zip`，`url` 仅在服务端知道自己的公开地址时有值，另有 `archiveSha256` 与 `run`）。固定件 + 这三个文件即完整站点；固定件体积约 620 KB，不会放进任何工具结果。内联上限为 `stateless.RENDER_INLINE_LIMIT`（24 KiB，约 8k token），低于 Claude Code 在 10k token 处的告警线和 Codex 约 12k token 的上限。

### 注解（annotations）

下表由 `server.create_server("full")` 的 `list_tools()` 读出，`—` 表示该字段未设置。`readOnlyHint=true` 的工具，MCP 规范里 `destructiveHint`/`idempotentHint` 没有意义，故不设置。`openWorldHint` 全部为 false（不访问外部世界）。

| Tool | title | readOnlyHint | destructiveHint | idempotentHint | openWorldHint | `stateless` |
|---|---|---|---|---|---|---|
| `gemarc_brief` | Work order for the model | true | — | — | false | 是 |
| `gemarc_contracts` | Contracts and vocabularies | true | — | — | false | 是 |
| `gemarc_assets` | Reusable components | true | — | — | false | 是 |
| `gemarc_new_model` | Empty architecture model | true | — | — | false | 是 |
| `gemarc_validate` | Validate a model | true | — | — | false | 是 |
| `gemarc_simulate` | Simulate a scenario | true | — | — | false | 是 |
| `gemarc_diff` | Diff two models | true | — | — | false | 是 |
| `gemarc_render` | Render site data from a model | true | — | — | false | 是 |
| `gemarc_inspect` | Scan a local repository | true | — | — | false | 否 |
| `gemarc_plan` | Plan dimensions and evidence gaps | true | — | — | false | 否 |
| `gemarc_scaffold` | Create the project scaffold | false | false | false | false | 否 |
| `gemarc_init` | Create an example project | false | false | false | false | 否 |
| `gemarc_model` | Replace a project model | false | true | false | false | 否 |
| `gemarc_build` | Build the site | false | true | false | false | 否 |
| `gemarc_verify` | Verify the built site | true | — | — | false | 否 |
| `gemarc_export` | Export the site as a ZIP | false | false | true | false | 否 |

注解直接驱动客户端的审批：Codex 在默认的 `auto` 模式下，只有 `readOnlyHint=true`，或 `destructiveHint=false` 且 `openWorldHint=false` 的工具不提示，所以 `gemarc_scaffold`、`gemarc_init`、`gemarc_export` 会**不经确认**执行（开启 `GEMARC_ALLOW_WRITE=1` 之后），只有 `gemarc_model`、`gemarc_build` 会提示。这一规则来自对 Codex 源码的调研，未在真机上运行验证；需要逐次确认的用户在客户端侧为这些工具配置 `approval_mode`（见 `integration-examples/codex/README.md`）。11 个只读工具带 `readOnlyHint`，Claude Code 允许并行调用它们。

## 结果结构与 `isError`

每个工具返回**一段 JSON 文本**（`json.dumps(..., ensure_ascii=False, separators=(",", ":"))`），放在 `content[0].text`。规则只有一条：

- `status` 为 `invalid` 或 `error` ⇒ MCP 结果的 `isError=true`；其余（`ok` 等）⇒ `isError=false`。**正文 JSON 不变**，调用方和模型读正文，不必只看 `isError`。
- 不返回 `structuredContent`，也不声明 `outputSchema`（ADR-011）：Codex 一旦看到 `structuredContent` 就用它**替换** `content` 给模型看，Hermes 会把相同内容去重；只返回 `content` 对三类客户端行为一致。

失败有两种形状：

1. **工具拒绝或异常**（越权、参数不合法、未知分段、内部错误）：

```json
{"status":"invalid","diagnostics":[{"id":"GEMARC_INPUT_003","severity":"ERROR","message":"必须二选一：传 project（工作区内的项目）或 model（内联模型）","entityId":""}],"artifactRefs":[]}
```

   业务异常（`GemArcError`）得到 `status: "invalid"`；`OSError/ValueError/KeyError/TypeError` 得到 `status: "error"` 与 `GEMARC_INTERNAL_001`，诊断里只有异常类名，不含宿主路径或密钥。

2. **模型校验失败**（`gemarc_validate`，以及 `gemarc_model`、`gemarc_render`、`gemarc_build` 内部的校验）：

```json
{"status":"invalid","errors":[{"id":"GEMARC_MODEL_012","severity":"ERROR","message":"targetId 节点不存在","entityId":"e1"}],"warnings":[],"summary":{"errors":1,"warnings":0}}
```

   `gemarc_validate` 另带 `modelSha256`。注意这里的数组叫 `errors`/`warnings`，不是 `diagnostics`；读取诊断的代码两个键都要看（`scripts/verify_stdio.py` 与 skill 都是这样做的）。

### 错误码

模型校验码按领域命名（`GEMARC_MODEL_*`、`GEMARC_EVIDENCE_*`、`GEMARC_VIEW_*`、`GEMARC_CAUSAL_*` 等，见 `model.py`）。工具层的码：

| 码 | 含义 |
|---|---|
| `GEMARC_INPUT_001` | 项目名或 `system` 不合法 |
| `GEMARC_INPUT_002` | `gemarc_inspect` 的路径需先克隆到授权工作区 |
| `GEMARC_INPUT_003` | **新增**：`project` 与 `model` 必须二选一，或内联 `model` 不是非空对象 |
| `GEMARC_INPUT_004` | 内联 `model` 含 `NaN` / `Infinity`（JSON 不允许），在入口就被拒绝 |
| `GEMARC_AUTH_000` | 工作区未配置且默认工作区无法创建 |
| `GEMARC_AUTH_001` | 路径位于工作区之外；或 `GEMARC_WORKSPACE_ROOT` 不是现有目录（不存在、是文件、或仍是示例里的 `<workspace>` 占位符），此时所有项目类工具都返回它，状态为 `invalid` |
| `GEMARC_AUTH_002` | 写操作关闭（未设 `GEMARC_ALLOW_WRITE=1`） |
| `GEMARC_IO_001` / `GEMARC_IO_002` | 文件不存在或超过 4 MiB / 项目目录已存在，拒绝覆盖 |
| `GEMARC_MODEL_001` / `GEMARC_MODEL_002` / `GEMARC_MODEL_020` | JSON 读取失败或模型不是对象 / 模型超过 4 MiB / 模型校验失败，不可模拟 |
| `GEMARC_BRIEF_001`、`GEMARC_CONTRACT_001` | 未知分段、未找到契约文件 |
| `GEMARC_SCAFFOLD_001` / `GEMARC_SCAFFOLD_002` | 未知 profile / 脚手架已存在 |
| `GEMARC_CONFLICT_001` | `expected_sha256` 与当前模型不一致 |
| `GEMARC_BUILD_001`–`005` | `legacy_dist` 不合法 / 超 50 MiB / 目标目录非 GemArc 生成 / 含符号链接 / 未知 profile |
| `GEMARC_EXPORT_001` / `GEMARC_EXPORT_002` | 未构建或验证失败 / 导出内容含符号链接 |
| `GEMARC_DYNAMIC_006` / `GEMARC_DYNAMIC_007` | 场景不存在 / 屏蔽的模块不存在 |
| `GEMARC_INTERNAL_001` | 内部异常（只暴露异常类名），状态为 `error`；原样报告，不要重试 |

## 访问控制示例

- 任何请求 `../../private` 或绝对路径超出根目录 → `GEMARC_AUTH_001`（`gemarc_inspect('..')` 在 `scripts/verify_stdio.py` 里逐次验证）。
- 写工具在未设 `GEMARC_ALLOW_WRITE=1` 时 → `GEMARC_AUTH_002`；该开关只能由用户在 MCP 配置里设置，调用参数改变不了它。
- 自定义组件中包含系统命令脚本但未获准许 → `GEMARC_COMPONENT_004`（目标设计，当前未实现）。
- 模型依赖未知因果规则却要求输出“已验证结论” → `GEMARC_CAUSAL_003`（`gemarc_validate` 的警告）。
- 远程端点的访问控制是可选的 Bearer token（ADR-010）；它在传输层，不在工具层，工具本身不认识调用者。

## Resources / Prompts

当前注册了 2 个只读资源和 1 个提示词（两个工具集相同）：

| 类型 | 名称 | 内容 |
|---|---|---|
| Resource | `gemarc://registry` | 可复用组件注册表（同 `gemarc_assets()`） |
| Resource | `gemarc://schema/model` | 规范架构模型的 JSON Schema（语义规则另由 `gemarc_validate` 检查） |
| Prompt | `gemarc-design`（参数 `project=""`） | 设计任务引导；`full` 下引导 inspect → validate → build → verify，`stateless` 下引导 new_model → validate → simulate → render；两个版本都只引用该工具集里存在的工具名 |

提示词是引导，不隐式写文件，操作仍经工具授权。不是每个客户端都用得上 resources 与 prompts（以下是对客户端文档与源码的调研结论，未真机验证）：Codex 忽略 prompt，只能通过内置的 list/read resource 工具读资源；Claude API 的 MCP connector 只支持 tools。因此**核心流程不依赖它们**，由 `gemarc_brief` 提供完整工作指令。资源读取不允许任意文件访问，只返回上表的固定内容。

[工具调用序列图](../06_图表/03_mcp_operation_sequence.mmd)
