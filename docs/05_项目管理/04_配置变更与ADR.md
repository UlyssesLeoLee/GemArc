# 变更控制、配置管理与 ADR

> 文档编号：PM-003 ｜版本：1.2 ｜日期：2026-11-02 ｜状态：设计基线（待评审）  
> 产品：GemArc MCP — 5D Design Scaffolding & Architecture Compiler  
> 说明：ADR-001～007 为目标设计，不代表目标功能已实现或已测试；ADR-008～012 记录已经实现的接口决策，仍待评审批准。

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
| ADR-009 | 远程部署使用无状态工具集（9 个只读工具，模型内联传入） | 建议采纳（已实现） | Vercel 无持久文件系统、无调用者身份 |
| ADR-010 | 远程端点：可选 Bearer token，未设置则无认证；GET `/mcp` 返回 405 | 建议采纳（实现见 `api/index.py`，本文未随该文件验证） | claude.ai / Desktop 无法携带静态 Bearer；Codex 把 404 当作会话过期 |
| ADR-011 | 工具结果不返回 `structuredContent`，不声明 `outputSchema` | 建议采纳（已实现） | Codex 用 `structuredContent` 替换 `content`；Hermes 去重并忽略 `outputSchema` |
| ADR-012 | 多媒体文档制作流程随服务器发布（工具 `gemarc_media_doc_guide` + 2 个资源），连上 MCP 即具备该能力 | 建议采纳（已实现） | 「导入 MCP」就该能用，不该再要求单独装 skill；工具是所有客户端都会交给模型的那条通道 |
| ADR-013 | 采纳 v5.0 规格的模型层要求（词表、Schema 字段、GWF-IR、§25 输出文件），新增字段一律可选增量 | 建议采纳（已实现） | 规格逐条列出的字段与枚举必须能被表达，否则「按规格实现」无从谈起 |
| ADR-014 | 命令改名：制作多媒体文档（原「5D 设计」），并采纳 v6.0 规格的 6D 执行图层；制作流程新增 UI/UX 一轮优化的硬规则 | 建议采纳（已实现） | 模型已覆盖 0D–6D，命令名再绑维度就是过期信息；「能妥善实现」必须包含做出来好不好读 |

### ADR-008 工具名：`gemarc_` 前缀 + `[a-z0-9_]`

- **决策**：把所有工具名从点号形式改为下划线形式：`gemarc.inspect` → `gemarc_inspect`，`gemarc.plan` → `gemarc_plan`，`gemarc.assets` → `gemarc_assets`，`gemarc.contracts` → `gemarc_contracts`，`gemarc.brief` → `gemarc_brief`，`gemarc.scaffold` → `gemarc_scaffold`，`gemarc.init` → `gemarc_init`，`gemarc.model` → `gemarc_model`，`gemarc.validate` → `gemarc_validate`，`gemarc.build` → `gemarc_build`，`gemarc.simulate` → `gemarc_simulate`，`gemarc.verify` → `gemarc_verify`，`gemarc.diff` → `gemarc_diff`，`gemarc.export` → `gemarc_export`；新增的 `gemarc_new_model`、`gemarc_render` 同样遵循。
- **原因**：（1）Claude API 与 OpenAI 函数调用要求函数名匹配 `[a-zA-Z0-9_-]{1,64}`，点号在 MCP 规范里合法，但这些接口会拒绝或改写；（2）Codex 把不属于 `[A-Za-z0-9_]` 的字符改成 `_`（冲突时加 sha1 后缀），Hermes 同样改成 `_`，且规范化后重名的工具**两个都被丢弃**；（3）保留 `gemarc_` 前缀：三个客户端虽然都按服务器名加命名空间（`mcp__<服务器>__<工具>`），但不加命名空间的客户端、把 MCP 工具直接转成函数的其他 agent 框架、以及同一会话里挂多个服务器的场景，仍需要前缀来避免撞名并标明归属；代价是名字长 7 个字符，加上最长的客户端前缀仍只有 29 字符（≤ 64）。
- **后果**：这是破坏性改名，**不保留旧名别名**：若同时暴露点号别名和下划线名，Hermes 规范化后两者重名，会让两个都消失。文档、skill、示例与测试同步更新；用户配置里按旧名写的白名单（Claude 的 `permissions.allow`、Codex 的 `enabled_tools`、Hermes 的 `tools.include`）需手动改。`src/gemarc_mcp/registry.json` 里的 `gemarc.scaffold*` 是组件 id 而非工具名，不受影响。`tests/test_tool_surface_docs.py` 禁止旧名在本节之外再出现。

### ADR-009 远程使用无状态工具集

- **决策**：`server.create_server(toolset)` 提供 `full`（17 个，本地默认）与 `stateless`（9 个，远程默认，由 `GEMARC_TOOLSET` 选择）。`stateless` 只含不依赖工作区的工具，模型作为参数内联传入，新增 `gemarc_new_model` 与 `gemarc_render`。站点的固定件（约 620 KB）不进工具结果，由 `/kit/<profile>.zip` 提供；`gemarc_render` 只返回随模型变化的 `data/*.json`，合计不超过内联上限时内联（初版 24 KiB，后由 ADR-015 抬到 35 KiB）。（工具数原为 16/8，ADR-012 之后为 17/9。）
- **原因**：Vercel 没有持久文件系统，也没有调用者身份；把工作区放进 `/tmp` 既不跨实例共享，也无法把项目归属到调用者。把完整站点放进工具结果没有任何客户端接受得了（Claude Code 在 10k token 告警，Codex 约 10k token 截断）。
- **后果**：服务端不保存任何项目，没有写工具，因此不需要写授权；填写事实靠调用方 agent 用自己的文件访问能力读用户的仓库；模型大于内联上限时改走本地工具集。

### ADR-010 远程端点的访问控制

- **决策**：环境变量 `GEMARC_MCP_TOKEN` 已设置（可逗号分隔以便轮换）时，`POST /mcp` 要求 `Authorization: Bearer <token>`；**未设置时端点无认证**。GET `/mcp` 一律返回 405（带 `Allow: POST`）。`/healthz` 与首页公开。
- **原因**：claude.ai 与 Claude Desktop 的远程 connector 只支持 OAuth、无认证（或部分组织的 beta 静态 header），不能带静态 Bearer；Claude Code、Codex、Hermes 都能带 Bearer。要让所有客户端都能连，必须有「无认证」这一档，OAuth 未实现。GET 返回 405 是因为 MCP 允许服务器拒绝可选的 GET 流，而 Codex 把 404 映射成「会话过期」。
- **后果**：无认证档位下，知道 URL 的任何人都能调用 9 个纯计算工具（没有工作区、不存储任何数据，但消耗部署者的额度）；使用 claude.ai / Desktop 的部署者必须自己接受这个取舍。这些客户端行为来自对文档与源码的调研，未真机验证。

### ADR-011 不返回 `structuredContent`

- **决策**：每个工具只返回一个 JSON `TextContent`；`status` 为 `invalid` 或 `error` 时设置 `isError=true`，正文不变；不返回 `structuredContent`，也不声明 `outputSchema`。
- **原因**：Codex 一旦看到 `structuredContent` 就用它**替换** `content` 给模型；Hermes 对内容相同的 `content` 与 `structuredContent` 去重并忽略 `outputSchema`。只给 `content` 在三者上表现一致，同一份 JSON 无需发两份。
- **后果**：没有机器可校验的输出 schema，输出形状由 `docs/03_详细设计/02_MCP工具详细契约.md` 与测试约束；将来若引入 `structuredContent`，必须先在 Codex 上验证模型看到的内容。

### ADR-012 多媒体文档制作流程由服务器随包发布

- **决策**：工作流的正本仍是仓库里的 `skills/gemarc-media-doc/`，但它会**生成一份副本**进包内 `src/gemarc_mcp/scaffold/skills/gemarc-media-doc/`（`python -m gemarc_mcp.scaffold.skillpack`，`tests/test_skill_pack.py` 断言两者不会漂移、且 package-data 覆盖每个打包文件）。服务器以三条通道把它发出：工具 `gemarc_media_doc_guide`（`full` 与 `stateless` 都有）、资源 `gemarc://skill/gemarc-media-doc` 与 `gemarc://skill/gemarc-media-doc/references/assemble-site.md`；服务器 instructions 与提示词 `gemarc-media-doc` 都指向它，`gemarc_brief` 的 procedure 第 0 步也是它。
- **原因**：客户端在「连上 MCP」这一步就该拿到模型需要的一切，不该再要求用户去装一个 skill。而**工具**是唯一所有客户端都会交给模型的通道：Codex 忽略 prompt，Claude API 的 MCP connector 只支持 tools，部分客户端（例如 MCode）根本不暴露 resources——只放资源的话，这些客户端读不到那份工作流。
- **后果**：v1 工具面从 16/8 变成 17/9，`tests/atlas/test_atlas_v1_frozen.py` 的 pin 按该文件自述的「v1 刻意变更」通道在同一提交内更新（ADR 与 pin 同提交）。文档、集成示例与用户侧白名单（Claude 的 `permissions.allow`、Codex 的 `enabled_tools`）同步更新。`.vercelignore` 仍排除 `/skills`，但打包副本在 `src/` 下面，随部署上传。工作流以服务器那份为准；单独安装的 skill 只是同一份文本的离线拷贝。

### ADR-013 采纳 v5.0 规格的模型层要求

- **决策**：把 v5.0 提示词（`Universal_5D_GemArc_Web_v5.0`）逐条列出的模型层要求补进 MCP，且**一律按可选增量**做：
  - 词表：`diagram-types` 补 `data-dependency` 与 `event-flow`（§9 的 Data 与 Architecture 家族各缺一种），并登记 `specFamilies`；新增 `case-types.json` 承载 §13 的 14 种分析类型与 §14 的 18 个动画原语；`workflow-kinds` 扩到 §17 的 19 种节点类型、7 种边类型，并登记 `gwfIr` 字段目录。
  - Schema：`node` 补 `qualifiedName` / `sourceLocation` / `version`，`edge` 补 `version`，`evidence.source` 补 `path` / `startLine` / `endLine`，`workflow` 补齐 §17 的工作流、节点、边、端口字段，`case` 补齐 §13 的 case_contract 字段。**全部可选**，既有模型继续有效。
  - 字段别名：§3 用 `type` / `label`，§17 用 `nodeType` / `edgeType` / `guard` / `portId`。这些拼写被**接受并归一**到正本拼写（`kind` / `name` / `condition` / `id`），Python 与浏览器运行时两侧同步。
  - 产物：`gemarc_render` 除既有三件外再派生 `evidence-index.json`、`diagram-catalog.json`、`spatial-layout.json`、`workflow-ir.json`、`case-library.json`；`workflow-schema.json`（GWF-IR 的 JSON Schema）只依赖词表，随**固定件（kit）**发布，不随模型重复传输。
- **原因**：规格逐条列出的字段与枚举必须能被表达，否则「按规格实现」无从谈起；而 MCP 的职责是**模型与计算权威**，不是前端工作台（见 `08_附录/03_v5.0规格能力对照.md` 的职责边界）。全部新增字段可选，是为了不破坏既有模型与既有套件。
- **后果**：
  - `cases[].category` 仍是 5 类分诊轴（它与 atlas 的 profile 词表锁步，改动会影响 atlas 的生成器与覆盖规则）；§13 的 14 种分析类型改由新增的 `cases[].caseType` 承载，两者互补。§13 的现实标记由 `cases[].reality` 承载（`Observed` / `ImplementedModel` / `DesignedModel` / `Hypothetical`）。
  - `gemarc_render` 的内联改为**逐文件**判定：按累计字节内联，放不下的文件只给清单，整体 `inline` 标志表示是否全部内联。
  - 词表变动 ⇒ `tests/atlas/test_atlas_v1_frozen.py` 的 `VOCAB_FILE_SHA256` / `VOCAB_BYTES_DIGEST` / `VOCAB_SYNC_DIGEST` 按同文件自述的通道在同一提交内更新；`case-types.json` 是新增文件。
  - 覆盖由 `tests/test_v5_spec_coverage.py` 钉住：规格里的字段与枚举被原样抄进测试，规格变了而 MCP 没跟上就红。
  - 派生文件一律「缺了就标 missing，不伪造」：图示保真元素缺失写进 `diagram-catalog.json` 的 `missing`，空间缺失写进 `spatial-layout.json` 的 `missing`，GWF-IR 不合法写进 `workflow-ir.json` 的 `issues`。
  - 规格 §25 给目标工程定的图示清单文件名属于参考系统，在本仓库的哈希禁词表上，因此 MCP 侧改用 `diagram-catalog.json`；详见 `08_附录/04_v5.0实施缺陷提交单.md` 的 D-05。

### ADR-014 命令改名「制作多媒体文档」，并采纳 v6.0 的 6D 执行图层

- **决策（一）改名**：命令面不再叫「5D 设计」。模型覆盖 0D–6D 之后，把命令名绑在某一个维度上就是过期信息。
  - 工具 `gemarc_5d_guide` → `gemarc_media_doc_guide`（标题 `Multimedia document workflow`）。
  - 资源 `gemarc://skill/gemarc-5d-design` → `gemarc://skill/gemarc-media-doc`（两条资源同步）。
  - 提示词 `gemarc-design` → `gemarc-media-doc`；技能目录 `skills/gemarc-5d-design/` → `skills/gemarc-media-doc/`（包内副本同名，`python -m gemarc_mcp.scaffold.skillpack` 生成）。
  - 站点品牌行与标题：`5D Architecture` → `多媒体架构文档`（`static` 与 `vite` 两套外壳，以及随包发布的 `mvp` 预览）。
  - 服务器 `INSTRUCTIONS` 与工具描述：`0D-5D architecture site` / `make a 5D design` → `0D-6D multimedia architecture document` / `make a multimedia document`，并把新触发词与旧触发词并列写进同一段。
  - 随包产物里的命令面命名：`templates/vite/package.json` 的 `name` → `gemarc-media-doc-site`；本地工作流的输出目录示例（`docs/08_附录/gemarc.config.example.yaml`、`docs/08_附录/examples/output-layout.txt`）→ `gemarc-media-doc-output`；`gemarc_plan` 的默认 `goals` → `多媒体文档资料`。
  - **旧说法仍然受理**：用户说「制作5D设计」「5D 架构模型」时照样走这条命令，触发词写进了技能描述。改名改的是命令名，不是能力。
- **决策（二）v6.0 的 6D 执行图层**：按 v6.0 规格补齐模型层要求，同样**一律可选增量**：
  - 词表：`workflow-kinds` 的节点类型 19 → 28、边类型 7 → 13，新增 `executionKinds` / `executionModes` / `executionGraph`（字段目录 + `state_contract` + `compiler_gates`）与 `qualityGates`；`case-types` 新增 `compositionModes` 与 `illustrativeCompositions`。
  - 契约：新增 `execution-graph.schema.json`（§25 的执行图节点/边契约）与 `execution-graph.example.json`（示例数据，验证等级全为 Hypothetical、证据为空），两者随固定件发布。
  - 派生：`data/workflow-ir.json` 输出 §18 的 6D 字段、每工作流的 `gates` 与顶层 `gates`。
  - 校验：8 条 6D 质量门（`GEMARC_EXEC_001`…`008`）**只对声明了执行图的工作流生效**（有 6D 专属字段或 6D 专属节点/边种类）；纯 5D 设计画布不受约束。
- **决策（三）制作流程新增 UI/UX 一轮优化的硬规则**：产出多媒体文档时必须做一轮 UI/UX 复核——主要显示内容重点突出、占比大；主要内容显示区域不得被挤小；各个窗格可调宽高。固定件外壳据此实现：两侧窗格之间有可拖拽分隔条（宽度）、内容面板右下角有拖拽手柄（宽与高）、顶栏有「专注」开关收起两侧窗格，主栏有最小宽度下限；尺寸写入浏览器本地存储。能力由固定件提供，流程只要求**用它**，不改固定件。
- **原因**：命令名是用户看到的第一眼信息，绑在一个已经走完的维度上会持续误导；6D 的执行图必须能被表达、被校验，否则「按 v6.0 实现」只是口号；而「做得出来」不等于「读得下去」——主内容被两侧窗格挤成一条窄缝时，再准的事实也没人看。
- **后果**：
  - 工具面与词表变动 ⇒ `tests/atlas/test_atlas_v1_frozen.py` 的 pin（工具名、词表哈希与摘要）按同文件自述的通道在同一提交内更新。
  - 覆盖由 `tests/test_v5_spec_coverage.py` 钉住：6D 字段、质量门（ADR-014 时 8 条，ADR-015 后 12 条）、Python 与浏览器运行时的对拍、文档数字绑定各有断言。
  - 随包发布的预览站按真实构建路径重新生成（`gemarc build --profile mvp`），`tests/test_generated_artifacts.py` 逐字节比对。
  - ADR-008 的约定继续适用：旧名只允许出现在记录改名的这一条里。

### ADR-015 6D 的定义是「WBS × 执行图」，不是只有执行图

- **背景**：ADR-014 采纳的 6D 执行图层只有**执行图**这一半——节点承担能力、边规定路由与依赖、结构化状态沿边传递、关键边界持久化。2026 年 7 月起业界用 **Graph Engineering** 描述这一层（见 `00_管理/03_引用标准及适用声明.md` 第 6 条）：把 Agent 循环、确定性程序与人工审批组织成显式的执行图，其中「工作图」是一次任务如何完成，「改进图」是系统如何长期调整。Graph Engineering 的工作图只讲**怎么跑**，不讲**做什么工作、分解到哪一级、交付什么**——后者正是 WBS 的职责。
- **决策**：**6D = WBS 分解层 × 执行图层**。两者在同一份执行图里，靠 `nodeIds` 绑定：
  - **WBS 层**（新增，`executionGraph.workPackages[]`）：`kind` 为 `phase` / `work-package` / `task` 三级；每个包必须声明 `deliverable`（交付物）与 `completionCriteria`（完成条件）；`parentPackageId` 构成**有且只有一个根**的树，不得成环；`dependsOn` 声明跨包依赖。
  - **执行图层**（既有）：节点承担具体能力（`agent-loop` / `deterministic-program` / `human-approval` / `tool-call` / `policy-gate` / `checkpoint` …），边规定路由与依赖，状态沿边传递并在检查点持久化。
  - **绑定规则**：执行节点必须归属至少一个工作包；**叶子**工作包必须至少归属一个执行节点（有子包的层级节点只做汇总，不要求自己挂节点）。
- **原因**：只做执行图，6D 就只能回答「怎么跑」，回答不了「这次要交付什么、做到哪一步算完」；而交付物与完成条件是 WBS 的定义要素，没有它们的工作包不构成 WBS。把 WBS 放进同一个契约，派生层才能算出**覆盖率**（多少执行节点被工作分解覆盖），读者也才能一眼看出分解与执行是否对得上。
- **后果**：
  - 词表 `workflow-kinds` 增 `workPackageKinds` / `workPackageFields` / `wbsContract`；`qualityGates.rejectOn` 由 8 条增至 12 条（新增 `wbs_orphan_node` / `wbs_broken_hierarchy` / `wbs_missing_deliverable` / `wbs_unbound_package`，错误码 `GEMARC_EXEC_009`…`012`）。
  - 契约 `execution-graph.schema.json` 增 `workPackages`；示例 `execution-graph.example.json` 增一棵三层工作分解。
  - 派生 `data/workflow-ir.json` 增 `workPackages`（含 `childPackageIds` / `nodeCount`）与 `wbs`（`rootPackageId` / `coverageRatio` / `kinds`）。
  - 站点两个外壳都读这一层：`static` / `vite` 的工作流视图渲染 WBS 树。
  - **导航口径统一**：三个外壳（`static` / `vite` / `mvp`）的导航现在一致：**01–05 维度 → 06 工作流（WBS × 执行图层）→ 07 图示登记 → UI 原型**。工作流是第六维度，所以占 `06`；原型不是维度，占 `UI` 槽；图示登记让位到 `07`，功能不丢。此前 `static` 把图示登记编为 `06`、工作流放 `UI` 槽，`mvp` 则把工作流编为 `06` 而没有图示页——同一份模型在两个站点上的入口位置不一样。`tests/test_site_navigation.py` 钉住三壳一致。
  - **两个外壳各补一页**：共享外壳新增**原型页**（`buildPrototypeView`，显示模型登记的原型页面、说明与可点动作；动作为演示，不提交业务数据），`mvp` 预览补上**图示页**（`renderViews`）。两边的页面集合从此相同。
  - **执行节点口径统一**：执行节点 = 画布上承担执行能力的节点 ∪ 执行图契约自带的节点（按 9 种 6D 执行节点种类与 13 个执行字段认定）。站点的「执行节点」列表与「执行节点归属 X / Y」覆盖率都按这一条算，与派生层的 `wbs` 同口径；同一模型在卡片上和在产物里只有一个分母。
  - **发布示例模型带 6D**：`examples/demo-site/data/architecture.json` 的示例工作流声明一棵三层工作分解（阶段 → 工作包 → 任务），示例站点因此能展示 WBS 树。
  - **内联上限由 24 KiB 抬到 35 KiB**（`stateless.RENDER_INLINE_LIMIT`）：GWF-IR 逐字段陈述 §18 的每个节点字段，一个工作流因此要几 KB 派生数据；发布示例带 6D 后实测响应 40,865 字节 / 11,732 token（cl100k_base），仍在 Codex 约 12k token 的上限之内。**代价**：示例模型已经越过 Claude Code 在 10k token 处的告警线；`tests/test_stateless.py` 钉住这个上限，超了就红。
  - 词表变动 ⇒ `tests/atlas/test_atlas_v1_frozen.py` 的 pin 按同文件自述的通道在同一提交内更新。
  - **不在本轮范围**：Graph Engineering 的「改进图」（指标、评估、策略、审计、人工决策构成的系统长期调整层）。GemArc 的 6D 仍然只做工作图。

### ADR-016 按账号的个性化设置：语言、工具清单、默认渲染形态

- **背景**：账号制部署下，`/mcp` 的认证结果只用来判断「放行还是拒绝」；认证通过之后，调用者的身份没有进入工具层，因此**所有用户看到的工具清单、工具说明与默认行为完全相同**。令牌其实早就真实存在数据库里（生产是 Neon Postgres，`api_tokens` 存 `secret_hash`，可吊销、可审计），缺的不是存储，是**这份身份用在哪里**。
- **决策**：新增 `user_settings` 表（迁移 `0003_user_settings`）与三项设置，并让它们在 MCP 协议层真的生效：
  - **语言**（`zh-CN` / `ja-JP` / `en-US`）：账号自己保存过才作数；没保存过就仍是「读者的语言说了算」，工具说明也仍是英文的正式文本。
  - **工具清单**（白名单）：空＝不限制；非空＝`tools/list` 只给这些工具，且**点名调用清单外的工具一律被拒**（`GEMARC_ACCT_036`）。
  - **默认渲染形态**（`mvp` / `static` / `vite`）：`gemarc_render` 的 `profile` 没传值时用它。
  - 身份通过 `gemarc_mcp.userctx` 的**请求上下文**从 HTTP 层传到 MCP 层：核心包不导入账号包，账号包也不被核心包反向依赖。
- **原因**：用户要的是「自己的 MCP」，不是所有人共用一口大锅。工具清单是模型每一轮都要读的东西：窄一点既省上下文又少误调用；工具说明按账号语言下发，模型不必在三种语言之间猜。设置放数据库而不是浏览器本机，才换机器、换 agent 客户端也一致。
- **后果**：
  - `gemarc_render` 的 `profile` 缺省值由 `"static"` 改成**空串**（空串＝用我的缺省值，调用方显式传值仍然优先），因此 `tools/list` 的 `inputSchema` 有一处**已记录**的差异；《MCP 工具详细契约》同步改写。
  - 新增：新表与新迁移、`/account/settings`（GET 回 JSON / POST 存表单）、账号页的无脚本设置表单、控制台的设置面板与顶部语言切换器（三语文案全部走语言表）。
  - **令牌存储不变**：仍然只存无密钥哈希，明文只在签发那一刻出现一次（既有政策 D110）。「随时重新签发」走账号页本来就有的创建令牌表单；把明文也存进数据库等于让密钥环泄露变成全部令牌泄露。
  - 设置里**没有任何密钥**：语言、工具名、渲染形态都是公开信息。
  - 设置**只减不增**：白名单变不出写工具，令牌作用域与 `can_write` 两道关照旧。
  - 设计全文见 `docs/03_详细设计/10_用户个性化设置详细设计.md`（DD-010）。

### ADR-017 `Origin: null` 是「没报出源 origin」，不是「源 origin 不匹配」

- **背景**：账号页上的表单 POST 在 Chromium 系浏览器（Edge / Chrome）里一律 403 `GEMARC_ACCT_021`，而同一个表单在 Firefox/Safari 正常、在控制台里用 `fetch()` 提交也正常。真实浏览器抓到的请求头是 `Origin: null` + `Sec-Fetch-Site: same-origin`；用 Python 客户端带上正确的 `Origin` 提交则 200。对照实验（真实 Edge headless 加载与账号页同响应头的合成页）定位到：**Chromium 导航式请求的 `Origin` 是从 referrer 推导的**——账号页的 `Referrer-Policy: no-referrer`（响应头与 `<meta>` 两处都有）把 referrer 剥掉，浏览器就没有源 origin 可报，只能发 `Origin: null`；把 `Referrer-Policy` 换成 `same-origin` / `strict-origin-when-cross-origin` / 去掉，浏览器就会报出真实 origin。其余响应头（COOP / CORP / `X-Frame-Options`）与此无关。
- **决策**：**判定规则改一行**——`Origin` 缺失**或为 `null`** 时，一律回落用 `Sec-Fetch-Site: same-origin` 判定；非 `null` 的 `Origin` 仍必须逐字等于配置基址。`null` 与「没有 `Origin` 头」是同一类信息（浏览器没报出源 origin），必须用同一个信号判定。
- **原因**：原规则把「有 `Origin` 头」当成「浏览器一定报出了真实源 origin」，于是 `null` 被当成「不匹配」而拒——这比规则自己写的「缺 `Origin` 时用 `Sec-Fetch-Site`」**更严**，结果把自家账号页的表单在 Chromium 里全部挡死：改设置、建令牌、登出、登出全部、删号全都提交不了。**不放宽的部分**：非 `null` 的 `Origin` 仍逐字比对；`null` 且 `Sec-Fetch-Site` 不是 `same-origin`（`cross-site` / `none` / 缺失）一律拒绝。不透明源（沙箱 iframe、`data:` 文档、`file:` 页面）的 `Sec-Fetch-Site` 必然不是 `same-origin`，进不来；`SameSite=Lax` 的会话 Cookie 本来就不随跨站 POST 送出，CSRF 令牌又与会话绑定，三道锁互相独立，改规则没有减少任何一道。
- **后果**：
  - **响应头不动**：不把 `Referrer-Policy` 改成 `same-origin`。那确实能让 Chromium 报出真实 `Origin`，但登录回调 `/auth/google/callback?code=…` 的 303 会把授权码放进下一次同源请求的 `Referer`，与第 2.4 节 T 行「授权码出现在 URL、Referer、日志」冲突。为了保住授权码不进 `Referer`，保住头集合里的 `no-referrer`；改为让判定规则理解 `null`。
  - 实现：`security.origin_ok()` 把 `Origin: null` 与「没有 `Origin` 头」合并为同一分支；`routes.py` 的 POST 管线与 `mcpauth.py` 的会话通道共用这一个函数，两处同时受益。
  - 绑定测试：`tests/accounts/test_routes.py` 的 `SessionsAndCsrf` 增加 `Origin: null` 的四种组合（`same-origin` 放行 / `cross-site` / `none` / 缺失全拒；令牌错仍拒）。既有断言（`Origin: null` 且无 `Sec-Fetch-Site` → 拒）**未改动且仍为绿**。
  - 规则全文与证据记在 `docs/09_部署/02_账号与安全.md` 第 3.2 节「`Origin` 判定与 `Referrer-Policy` 的关系」。

### ADR-018 账号模式的 `/mcp` 401 带一句 `hint`：所有失败原因同一句

- **背景**：账号制部署下，MCP 使用者（Claude Code / Codex / Hermes 这类客户端，或任何 MCP 客户端）第一次连 `/mcp`，拿到的是 401 + `{"error":"unauthorized"}`。这句话没有下文：它不说是「没带令牌」还是「令牌不被接受」，更**不说令牌该从哪拿、填到哪**。使用者能看到的令牌说明都在**网页**里（控制台与账号页），而连不上线的那个界面——客户端——没有任何指引。令牌链（`docs/09_部署/02_账号与安全.md` 第 3.15 节）因此只有「网站签发 → 复制 → 填进 agent」的前半段，缺了后半段：agent 填不进去时怎么办。
- **决策**：**账号模式**（`pat` / `pat-or-env`）的 `/mcp` 401 响应体加一个 `hint` 字段：
  - 形状是 `{"error":"unauthorized","hint":"<一句提示>"}`；`hint` 说的**只有两件事**：令牌在账号页拿（带本部署的地址）、填进客户端的环境变量 `GEMARC_MCP_PAT` 或客户端的 `Authorization: Bearer` 头。
  - **所有失败原因同一句**：没带令牌、带错、已过期、已撤销、账号已停用、主机不匹配——响应体**逐字节相同**。原判据的安全核心（不区分失败原因）**不动**：`hint` 是给使用者的操作指引，**不是**新的判断依据。
  - `hint` 的语言按请求判定（`?lang=` → Cookie → `Accept-Language` → 默认 `zh-CN`），文案走语言表新增的 `mcp` 段，三语同键、同一套占位符。
  - **未开账号的部署逐字节不变**：仍是 `{"error":"unauthorized"}`（不变量 I1、`scripts/verify_remote.py`、`tests/test_remote_auth.py`）。响应头 `WWW-Authenticate: Bearer` 与 `Cache-Control: no-store` 不变。
- **原因**：401 是使用者**唯一**能拿到的反馈。一个不说明下一步的 401 会让人去猜（换令牌？改路径？加请求头？），而正确答案只有一个动作：去账号页拿令牌，填进客户端。把它写进响应体，等于把「链的回路」放在使用者真正会看到的地方，不必先打开网页去找文档。反过来说，**不**这么做有两个坏处：一是「令牌怎么设置」这个问题在客户端里无处可答；二是各处各写各的，几份接入示例里的变量名很快会互相矛盾（本轮就发现客户端侧与服务端侧都用 `GEMARC_MCP_TOKEN` 指不同的东西）。**不**做的还有一种更省事的做法——把 401 的失败原因写细（缺令牌 / 令牌无效 / 令牌过期）——那会变成一个免费的判别器，本轮明确否决。
- **后果**：
  - **客户端侧的环境变量名统一为 `GEMARC_MCP_PAT`**（个人访问令牌，与 `api_tokens` 表、`pat` 模式同名）：账号页签发后给出的配置里，环境变量写法用的就是它（第 3.15 节）。`integration-examples/` 里**客户端**侧的变量名同步改成它，并写清 `GEMARC_MCP_TOKEN` 是**服务端**（未开账号部署的共享令牌）的名字——两个名字不再混用；旧示例里已经写好的 `${GEMARC_MCP_TOKEN}` 仍然可用（变量名由用户自己的配置决定，服务端不校验它）。
  - 实现：`mcpauth.unauthorized_response()` 接受一个可选的 `hint`；`PatAuthenticator` 在**一次请求内**算出**一个** `hint`（语言 + 配置基址），四个失败分支共用它，因此同一请求的各条失败路径逐字节相同。语言判定与账号页同一套顺序（DD-009）：`?lang=`（`/mcp` 的查询串）→ Cookie → `Accept-Language` → 默认；写不出三种语言之一的值不算「选择」，只回落。
  - 绑定测试：`tests/accounts/test_mcpauth.py` 断言「同一请求下所有失败原因逐字节相同」「响应体含 `error` 与 `hint`」「`hint` 随请求语言变化、且带本部署的账号页地址与变量名」「`?lang=` 优先于 Cookie 与 `Accept-Language`」「不回显任何令牌」；`tests/test_remote_auth.py` 与 `tests/test_http_transport.py` 里**未开账号**的既有断言**未改动且仍为绿**。
  - 语言表新增 `mcp` 段（三语同键），`tests/test_i18n.py` 的「三段同键」检查把它纳入范围。

### ADR-019 指纹、版本标记与更新历史：标记只进台账，核对只报告

- **背景**：Atlas 产物（1D 派生文档、阅读面页面、整站页面）没有版本身份：构建出来的 `dist/` 里只有内容，没有「这是哪一版、什么时候产生的」，也没有「这次更新动了哪些文档」。`slice` 换版时旧版本被归档到 `.atlas/history/`，但**没有任何出口**能看它；`.atlas/history/` 里躺着十个旧版本，工具却一个都列不出来。用户要求：给 1D 每个文档一个指纹、标记版本、预备更新时核对、能通过 MCP 命令查看更新历史、整站一个指纹，指纹附带**精确到分钟**的日期后缀。
- **决策**：规则全文写在 `docs/atlas/spec/versions.md`（新增规格），核心是三个词与八条规则：
  - **指纹**（`sha8`）：文件字节的 SHA-256 前 8 位。沿用house rule：切片戳（`slices.sha8`）与阅读面页尾哨兵是同一条。
  - **版本标记**（`<sha8>-<YYYYMMDD-HHMM>`，UTC）：指纹 + 产生的分钟。
  - **台账**（`.atlas/versions.json`）：一次构建一条，最多 50 条；损坏的读成空。
  - **V-1 标记不进产物**：产物必须逐字节可复现（`build.py` 的确定性规则、BD-007 拒时间戳），而分钟不可复现。所以产物里只有**内容指纹**（`dist/build-info.json` 的 `fingerprints` 段），带分钟的标记只在台账与 MCP 回复里。反过来也不成立：产物里不许有任何依赖当前时刻的东西。
  - **V-2 带指纹的文件不进指纹**：`build-info.json`（它自己装指纹）、`manifest.jsonl`（清单登记每个文件的 sha8，不能登记自己）、`.verify/**`（门的工作目录）、`oracle-sheet.md`（清单只登记它的存在、不登记字节：它在构建之后写，还会再写一次）。这四条与 `reading/manifest.py` 已有的 `SKIP_NAMES` / `SKIP_DIRS` 是同一条规则。
  - **V-3 站点指纹**：对「路径 指纹」逐行拼接求 SHA-256。同一模型的两次构建同值；不同的站点不同值；一个文件变了值就变。
  - **V-4 1D 文档指纹**：1D 层 = 派生文档 `docs/<lang>/{REQ,BD,DD}.md` 与阅读面页面 `read/1D/**`，每个文件一个指纹。指纹清单覆盖产出目录里的每一个文件，所以阅读面页面只要写进了 `dist/` 就自动有一个指纹，不必再改规则。派生文档的指纹是模型的纯函数，与磁盘上文件的指纹相等。
  - **V-5 标记是内容的一个版本，不是一次构建**：内容没变 → 标记与分钟沿用旧的（文档与整站一视同仁）。判据是 **sha8 相等**，不是分钟相等。
  - **V-6 台账**：工具的状态文件，不是产物，不发布。
  - **V-7 预备更新时的核对只报告**：给出每个 1D 文档**现在**的指纹与「是否同版」，**不拒绝、不写任何东西、不改任何判据**。核对的对象是**能由模型直接得出**的派生文档；台账里记着但**当前模型给不出指纹**的（阅读面页面由阅读面那条线单独构建）单列为未核对。
  - **V-8 历史**：构建历史、逐文档版本、切片归档版本（`.atlas/history/` 第一次有了出口）。
- **原因**：分钟级后缀是用户明确要的（「后缀精确到分钟」），但它与产物可复现这一条直接冲突——把时间戳写进 `dist/`，两次构建就不再逐字节相同，BD-007 的门也就名存实亡。分成「指纹（进产物）」与「标记（进台账）」两个词之后，两个要求同时成立：产物仍然可复现，而用户要的「带日期的版本号」在台账与 MCP 回复里逐字可见。**核对只报告**是另一条边界：核对若能拒绝，就成了新的门，而发布门、浏览器门、证据门、切片校验的规则都是设计的一部分，不该由一个新工具悄悄改变；只报告则让 AI 在报告里能说清「这一轮改了 BD 文档，指纹从 A 变成 B」，把决定权留给用户。
- **后果**：
  - **工具集从 13 个变成 14 个**：新增 `gemarc_atlas_versions`（只读，只在 `atlas` 工具集；`atlas_remote` 无状态、不读本地文件，所以远程工具集仍是 `brief` + `check` 两个）。`ATLAS_TOOLS.md` 的工具数、章节与注解表同步更新；`tests/atlas/test_atlas_tool_surface.py` 的「本地工具集」断言从 13 改为 14，并新增一条钉住新工具的输入、注解、章节、技能与提示词引用。
  - **`tools/list` 的预算因此被挤满**（Atlas 的 `tools/list` 上限 10 KB，测试另留 600 B 余量）。新工具进来后超预算 499 B，**按 `ATLAS_TOOLS.md` 自己写的意图收紧描述**（细节归 `brief` 与 `ATLAS_TOOLS.md`，描述只留操作要点）：14 条描述从 161–367 B 收到 124–241 B，`tools/list` 从 10,139 B 降到 9,625 B（硬上限 10,240 B，余量 615 B；距测试的 9,640 B 预算只剩 15 B，下一个工具进来之前要先处理预算）。**没有改预算常量，也没有改 10 KB 的上限。**
  - 实现：`src/gemarc_mcp/atlas/versions.py`（新增，指纹与台账）、`src/gemarc_mcp/atlas/build.py`（构建时算指纹并登记台账；发布路径与普通路径都登记）、`src/gemarc_mcp/atlas/tools.py`（`versions()` 三个视图）、`src/gemarc_mcp/atlas_tools.py`（注册 + 入口提示第 6 步 f 引用）、`src/gemarc_mcp/atlas/cli.py`（同名命令行子命令，`python -m gemarc_mcp.atlas versions <项目> --view check`）、`skills/gemarc-atlas/SKILL.md`（工具表 + 收尾第 6 步）。
  - 联动的既有产物：`docs/atlas/examples/` 的两份无头设置与 `--allowedTools` 清单（工具数 13→14）、`docs/atlas/examples/README.md` 与 `docs/atlas/README.md` 的工具数、`tests/atlas/README.md` 的用例说明、`tests/atlas/kit_names.txt`（工具包的固定文件清单，此前已经陈旧——`protoui/`、`reading/` 两条线的文件没进清单，重新生成后 `test_kit` 少一条红）、`docs/atlas/TOKEN_LEDGER.md` 的实测数字（tools/list 9,452→9,625、brief all 5,958→5,963、入口提示 7,084→7,338；前者本来就与代码差 5 字节，这次一并改正）。
  - 绑定测试：`tests/atlas/test_versions.py`（新增 40 条）：指纹与标记的形状、清单与信任根、站点指纹、台账（损坏读成空、50 条上限、内容未变则沿用标记）、构建（`build-info.json` 带指纹、台账被写、产物里没有标记也没有时间戳、两次构建同值）、工具三视图（历史、指纹、核对）与「核对不写任何东西」。
  - 出处登记：`src/gemarc_mcp/atlas/provenance/versions.tsv` 新增一行，`PROVENANCE.md` 重新生成（顺带修回了它此前陈旧的状态）。
  - **没有改动的**：发布门、浏览器门、证据门、切片校验、`verify_remote` 的既有判据、`test_disabled_unchanged` 的逐字节不变断言，全部原样。台账与 `.atlas/history/` 的读取都是只读的。

### ADR-020 OAuth 2.1：让「只会说 OAuth 的客户端」也能连上 `/mcp`

- **背景**：`/mcp` 只有**个人访问令牌**一条通道（`Authorization: Bearer gat_…`）。只会说 OAuth 的客户端因此连不上：MiniMax Code 的「授权」按钮走的是 OAuth 2.1 的发现与动态注册，服务器上没有 OAuth 元数据，点它必然报「未找到该服务器的授权配置」（**实测**：2026-11-12 从本机安装包里读到的错误文案是 OAuth 发现失败那一条，与令牌通道无关）；claude.ai 的连接器同理。这不是配置写错，是**协议不匹配**：第 3.6 节早就把 OAuth 2.1 列为下一个任务 `accounts-oauth`，并写明它会打破「`/.well-known/*` 必须 404」的约定（I5）——到那时必须先改这些测试。
- **决策**：
  - **新增六个公开路径**（账号模式才有；未开账号的部署仍然一律 404）：
    - `GET /.well-known/oauth-protected-resource` 与 `GET /.well-known/oauth-protected-resource/mcp`（RFC 9728，资源元数据）；
    - `GET /.well-known/oauth-authorization-server`（RFC 8414，授权服务器元数据）；
    - `POST /register`（RFC 7591，动态客户端注册，公共客户端、无密钥）；
    - `GET /authorize` 与 `POST /authorize`（授权码 + PKCE）；
    - `POST /token`（授权码换令牌、刷新令牌轮换）。
  - **只用授权码 + PKCE（S256）**：没有隐式流、没有密码流；公共客户端不持有密钥。
  - **三种令牌形态**（前缀各自独立，格式与校验沿用第 4.5/4.6 节的口径）：
    - 访问令牌 `goa_…`：短期（默认 15 分钟），**绑定受众**（`resource` 参数，RFC 8707），只存哈希；
    - 刷新令牌 `gor_…`：默认 30 天，**每次使用即轮换**，旧令牌立即作废；旧令牌被再次使用 = 重用，撤销整条链；
    - 授权码 `goc_…`：60 秒，一次性。
  - **四张新表**（ADDITIVE 迁移 `0004_oauth.sql`）：`oauth_clients`、`oauth_codes`、`oauth_refresh_tokens`、`oauth_access_tokens`。`api_tokens.kind = 'pat'` 一行不改。
  - **`/mcp` 按前缀分派**：`gat_` 走原有的令牌通道，`goa_` 走新的访问令牌通道；对外契约不变（仍然是 `Authorization: Bearer`），变的只是「这个 Bearer 可以是访问令牌」。两种凭据的失败原因**同一句**（沿用 ADR-018 的口径）。
  - **401 带 `resource_metadata`**：账号模式下 `/mcp` 的 401 头是 `WWW-Authenticate: Bearer resource_metadata="<基址>/.well-known/oauth-protected-resource"`（RFC 9728 §5.1）。**未开账号的部署仍然是裸 `Bearer`**，逐字节不变（`tests/test_remote_auth.py` 据此判断）。
  - **作用域**：`read`（默认）与 `write`；`write` 同时需要 `users.can_write` 与运营开关，同意页会把它写出来。
  - **同意页**：已登录会话下，`/authorize` 先显示一页（客户端名、作用域、受众、访问令牌与刷新令牌的到期），用户点「允许」才发码；未登录则先走 Google 登录再回来（`return_to` 带回原参数）。
  - **审计与限流**：新增 `oauth_client_registered`、`oauth_authorized`、`oauth_code_redeemed`、`oauth_token_issued`、`oauth_token_refreshed`、`oauth_token_reuse_detected` 六个事件（`ref` = 客户端 ID）；限流作用域 `oauth_register`、`oauth_authorize`、`oauth_token`（按 IP 哈希）与 `oauth_consent`（按用户，同意页的提交）。
- **原因**：OAuth 2.1 是 MCP 授权规范的走向（第 3.6 节已写明「支持 MCP 授权规范的客户端应当改走 OAuth 2.1，直接用 Google 登录并只持有短期、绑定受众的令牌」）。两条通道并存而不是替换：`Authorization: Bearer` 的对外形状不变，老客户端（Claude Code / Codex / Hermes 的令牌写法）一行配置都不用改；新客户端（只有 OAuth 的那些）多了第二条路。**受众绑定**（RFC 8707）保证一个令牌不能拿去访问别的部署；**刷新令牌轮换 + 重用检测**保证「短访问令牌 + 长刷新令牌」这个推荐组合的弱点（刷新令牌泄露）只暴露一次。不用隐式流是因为短访问令牌不该活在浏览器的 URL 里。
- **后果**：
  - **既有约定要改**：第 3.6 节与 I5 的「`/.well-known/*` 必须 404」在账号模式下不再成立；`tests/accounts/test_mcpauth.py` 里探测 404 的那组用例随之改成「账号模式返回元数据、未开账号仍 404、账号开但配置不全答 503」。这是第 3.6 节早就写明的前置条件，不是临时放宽。
  - **路由表要列全**：第 3.2 节的路由清单补上这六个路径（R-08 已登记「路由表少列路径」这个缺陷，这次一并补齐）。
  - **三语**：同意页文案进 `zh-CN` / `ja-JP` / `en-US` 三张表，键集合保持相同（`tests/test_i18n.py` 继续守这条）。
  - **绑定测试**：新增 `tests/accounts/test_oauth.py`（元数据形状、动态注册、授权码一次性与 PKCE 绑定、令牌轮换与重用检测、受众绑定、`/mcp` 双凭据分派、失败原因同一句），并把路由表与错误码的既有断言补到全量。
  - **实现**：`src/gemarc_mcp/accounts/oauth.py`（新增：令牌形态、元数据、授权码、访问令牌、刷新令牌轮换）、`src/gemarc_mcp/accounts/routes.py`（六个路径与同意页）、`src/gemarc_mcp/accounts/mcpauth.py`（按前缀分派）、`src/gemarc_mcp/accounts/migrations/0004_oauth.sql`（新表）、`src/gemarc_mcp/accounts/pages.py`（同意页）、`src/gemarc_mcp/i18n/*.json`（三语）。
  - **没有改动的**：令牌通道的格式、校验和、缓存与限流口径；`/mcp` 的工具集与作用域判定；未开账号的部署（仍然没有这些端点）。

## 变更流程

提交 CR→影响分析 FR/NFR/Schema/View/测试→设计变更评审→实现和测试→QA 审查→基线发布。凡变动稳定 ID 与 Schema major，需要迁移方案与回滚计划。变更决议与审批记在台账，未经审批不改成“已采用”。
