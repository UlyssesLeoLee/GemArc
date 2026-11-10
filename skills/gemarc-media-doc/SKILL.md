---
name: gemarc-media-doc
description: Use when the user wants to turn the facts of a software system (their repository, design documents, ADRs) into a reviewable 0D-6D architecture model and a multimedia architecture document (a static site) through the GemArc MCP server (tools named gemarc_*), or asks to validate, simulate, diff or render such a model. Chinese triggers: 制作多媒体文档、做多媒体文档、生成多媒体架构文档、架构模型校验；旧说法同样受理（制作5D设计、5D架构模型）。 The MCP server ships this workflow itself (tool gemarc_media_doc_guide, resource gemarc://skill/gemarc-media-doc), so a client that only imports the MCP already has it; this skill is the standalone copy for offline reading. Covers the remote stateless workflow and the local full workflow. Not for drawing diagrams by hand or for the Three.js generator that does not go through this MCP server (see ipa-architecture-3d).
compatibility: Needs the GemArc MCP server connected, either remote over Streamable HTTP (stateless toolset, 9 tools) or local over stdio (full toolset, 17 tools). Assembling a site needs curl or PowerShell plus Python 3 (static profile) or Node 20+ (vite profile).
metadata:
  version: "2.0.0"
  default_language: "zh-CN"
  server: "gemarc-mcp"
---

# GemArc 多媒体文档 — 用 MCP 工具生成可审查的多媒体架构文档

GemArc 把「可被写死的东西」（字段名、词表、校验规则、模拟引擎、站点外壳）固定在服务端，只让你填**事实**。你的工作是从用户的仓库里读出真实证据、填进统一模型、让校验通过、再取回站点。**不要重新发明引擎、字段名或算法。**

下文用裸工具名。客户端可能显示为 `mcp__gemarc__gemarc_validate`（Claude Code、Hermes）或命名空间 `mcp__gemarc` 下的 `gemarc_validate`（Codex）；按客户端显示的名字调用即可。

## 何时用这份流程

用户说「制作多媒体文档」「做多媒体文档」「生成多媒体架构文档」「多媒体架构站点」，或者要校验、模拟、对比、渲染一个架构模型时，按本文执行。旧的说法（「制作5D设计」「5D架构模型」「做5D架构站点」）同样受理：模型已覆盖 0D–6D，命令名不再绑定维度。

**这份流程由服务器自己发出。** MCP 服务器随包发布同一份文本：客户端连上之后调 `gemarc_media_doc_guide` 就能取到（也可读资源 `gemarc://skill/gemarc-media-doc`），所以「导入 MCP」这一步就已经具备制作多媒体文档的能力，不必再单独安装本 skill。服务器返回的文本与本文一致；两者不一致时以 `gemarc_media_doc_guide` 返回的为准（服务器那份才是与工具面同步的那份）。

## 先判断模式

看你**实际拿到**的工具列表，不要靠猜：

| 看到 | 模式 | 特点 |
|---|---|---|
| 只有 9 个工具：`gemarc_media_doc_guide` `gemarc_brief` `gemarc_contracts` `gemarc_assets` `gemarc_new_model` `gemarc_validate` `gemarc_simulate` `gemarc_diff` `gemarc_render` | **远程 / 无状态**（流程 A） | 没有工作区，服务端不保存任何东西；模型每次随调用以 `model` 参数内联传入；服务端看不到用户的仓库 |
| 17 个工具（另有 `gemarc_inspect` `gemarc_plan` `gemarc_scaffold` `gemarc_init` `gemarc_model` `gemarc_build` `gemarc_verify` `gemarc_export`） | **本地 / 完整**（流程 B） | 项目存在工作区目录里；写工具需要用户开启 `GEMARC_ALLOW_WRITE=1` |

## 硬规则（两种模式都适用）

1. **事实不可臆造。** 每个节点、每条关系都要指回一个 evidence 实体；指不回去就标 `Unknown`，写进 `missing`，不要补全、不要「合理推断」成事实。
2. **证据等级、实体种类、边类型、图类型一律以服务端为准。** 需要时调用 `gemarc_brief(section="vocabulary")`，不要凭记忆写，也不要在回复里重述一份词表。其他可取的分段：`slots`、`rules`、`procedure`、`commands`、`alreadyDone`（`gemarc_brief()` 不带 `section` 约 19 KB，一次会话读一次就够）。
3. **模拟是基于规则的 what-if，不是因果识别。** `gemarc_simulate` 的结论强度不会超过场景自身的证据等级；`hypothetical` 场景的结果不得写成「实际运行结果」。汇报时说明这一点。
4. **写操作是用户的决定。** 本地写工具（`gemarc_scaffold` `gemarc_init` `gemarc_model` `gemarc_build` `gemarc_export`）只有在用户的 MCP 配置里设了 `GEMARC_ALLOW_WRITE=1` 时才会执行；收到 `GEMARC_AUTH_002` 就告诉用户如何开启，**不要**自己改配置或换办法绕过。路径越出 `GEMARC_WORKSPACE_ROOT` 会得到 `GEMARC_AUTH_001`，同样不要绕。
5. **不夸大验证范围。** `gemarc_verify` 只检查生成站点的文件完整性和 schema；浏览器、WebGL、性能行为没有测过，必须在最终说明里写出来。
6. **不改固定件。** 站点外壳与共用运行时（kit 或 `scaffold/` 下的固定文件）不要手改；定制放进模型的槽位里。**但要会用外壳已经提供的能力**（见「制作时的 UI/UX 基线」）。
7. **一个模型是唯一事实源。** 所有视图、模拟、站点数据都从同一个模型派生，不要在站点文件里另写一份事实。
8. **流程以服务端那份为准。** `gemarc_media_doc_guide`（或资源 `gemarc://skill/gemarc-media-doc`）返回的文本与本文相同；两者不一致时用服务器那份，字段名与词表仍按硬规则 2 取自 `gemarc_brief`。
9. **每一份产出都要做一轮 UI/UX 优化。** 见下一节，这是硬规则，不是可选项。

## 制作时的 UI/UX 基线（硬规则 9）

产出多媒体文档时，**必须**做一轮 UI/UX 复核并把结果落到站点里。这不是审美偏好，是可核对的要求：

| 要求 | 怎么落地 | 怎么核对 |
|---|---|---|
| **主要显示内容重点突出、占比大** | 一屏里最先看到的必须是主内容（结构图 / 时间轴 / 工作流图），而不是导航或说明文字；次要信息收进次级面板；长列表折叠 | 打开站点第一屏，主内容占主栏主体；导航与说明不抢主视觉 |
| **主要内容显示区域不能过小** | 主栏是主内容区；两侧窗格宽度可调，但主栏有最小宽度下限，拖到极限也不会被压到不可用 | 把两侧窗格拉到最宽，主栏仍可用 |
| **各个窗格可调宽高** | 两侧窗格之间有可拖拽分隔条（宽度）；每个内容面板右下角有拖拽手柄（宽与高）；尺寸写入浏览器本地存储 | 拖动分隔条与面板手柄，尺寸改变且刷新后保留 |
| **专注模式** | 顶栏有「专注」开关，收起两侧窗格后主内容占满窗口 | 打开后主栏占满窗口宽度 |

固定件站点外壳已经实现了上表里的窗格能力（分隔条、面板手柄、专注开关、最小宽度下限），**不需要改固定件**。你要做的是：选对主内容、把次要信息放进次级面板、用模型的槽位表达层次，并在最终说明里写这一轮做了什么。

## 流程 A：远程（无状态）

1. `gemarc_media_doc_guide()`：取服务端自带的完整流程（本文就是它的一份拷贝；已经读过就跳过）。
2. `gemarc_brief()`：取得工作指令、槽位清单和词表（必要时只取 `section="slots"` / `"vocabulary"`）。
3. `gemarc_new_model(system="<系统名>")`：返回空模型 `model` 和槽位说明 `slots`。系统名 ≤ 80 字符。
4. **用你自己的文件访问能力**读用户的仓库、文档、ADR，按槽位逐项填写。每个事实记下出处；缺的写 `Unknown`。服务端读不到仓库，也不会替你读。
5. 校验循环：`gemarc_validate(model=<对象>, strict=false)` → 按 `errors[].id` 与 `entityId` 逐条修 → 再校验，直到 0 个 error。需要宣称「已确认」时再用 `strict=true`。strict 要求节点和边有「已实现或已观测」的证据：一个全是 `Hypothetical` 的模型在 strict 下必然失败，正确的做法是补真实证据，**不是改标签**。同一批 id 连续三轮改不掉，就停下来问用户，不要硬凑。
6. 4D/5D：`gemarc_simulate(model=…, scenario_id="…")`；要做对比用 `compare_baseline=true` 加 `disabled_nodes=["<节点 id>", …]`。场景只用真实事件或明确标注的假设。
7. `gemarc_render(model=…, profile="static")`（或 `"vite"`）：校验通过后返回随模型变化的 `data/*.json`（`files[]`）和固定件 `kit` 的下载信息。
8. 组装站点：下载 `kit`、核对 `kit.archiveSha256`、解压、把 `files[]` 逐个写进同一目录、核对每个文件的 `sha256`，再用 `kit.run` 的命令本地预览。逐步命令（bash 与 PowerShell）见 [references/assemble-site.md](references/assemble-site.md)。
9. **UI/UX 一轮优化**：按上面的基线复核一遍站点，把调整落到模型与内容上（主内容优先、次级信息折叠、必要时用外壳的专注开关核对主区域是否够大）。
10. 汇报：站点位置、校验结果（含 warnings）、哪些事实是 `Unknown`/`Hypothetical`、哪些浏览器能力没测过、这一轮 UI/UX 做了什么。

`gemarc_diff(base_model=…, target_model=…)` 比较两个版本的模型（新增、删除、修改的节点与边，以及相邻节点）。

## 流程 B：本地（完整）

`GEMARC_WORKSPACE_ROOT` 必须**包含**要扫描的仓库，生成物也写在这个目录里。

1. `gemarc_media_doc_guide()`（已读过本文就跳过），然后 `gemarc_brief()`，必要时 `gemarc_contracts()`、`gemarc_assets(query="…")`。
2. `gemarc_inspect(path="<工作区内的仓库路径>")`：只读扫描。返回的候选模块**都未确认**，必须对照源码核实后才能当事实。
3. `gemarc_plan(project="…")`：看哪些维度（1D–6D）现有证据撑得住、缺什么。
4. `gemarc_scaffold(project="…", profile="static")`：落下固定件和空模型 `<project>/model/architecture.json`。已有的脚手架不会被覆盖。（只想看一个带示例数据的项目用 `gemarc_init`；示例不是真实事实。）
5. 填模型：直接编辑 `<project>/model/architecture.json`，或用 `gemarc_model(project, replacement_model, expected_sha256)`。后者的 `expected_sha256` 必须取自**最近一次** `gemarc_validate(project=…)` 的 `modelSha256`；不一致或模型无效时整体拒绝、不写入。
6. 校验循环：`gemarc_validate(project="…")`，修到 0 个 error；宣称「已确认」时再加 `strict=true`（同流程 A 第 5 步：strict 下 `Unknown`/`Hypothetical` 必然不过，补真实证据，**不是改标签**）。
7. `gemarc_simulate(project="…", scenario_id="…")`，同流程 A 第 6 步。
8. `gemarc_build(project="…", profile="static")`（`mvp` 小型查看器；`static` 零构建 ESM；`vite` npm 工程）→ `gemarc_verify(project="…")` → `gemarc_export(project="…")`（ZIP 与其 sha256 在项目目录里）。`gemarc_build` 会替换上一次生成的站点。
9. **UI/UX 一轮优化**：同流程 A 第 9 步。

`validate`、`simulate`、`diff` 里 `project` 与 `model` **二选一**，两个都给或都不给得到 `GEMARC_INPUT_003`。

## 规格字段（能填就填，缺就留空）

模型层支持 v5.0 与 v6.0 规格逐条列出的字段。它们**全部可选**，但填了才能让文档表达得更完整：

| 位置 | 字段 | 要点 |
|---|---|---|
| 节点 | `qualifiedName`、`sourceLocation`（`repository`/`commit`/`path`/`startLine`/`endLine`）、`version` | `sourceLocation` 指向真实文件与行号；指不回去就留空 |
| 边 | `version` | 关系语义变更时留版本 |
| 证据 | `source.path` / `source.startLine` / `source.endLine` | 让证据能定位到一段而不是一行 |
| 视图 | `diagramType` 可取 `data-dependency`、`event-flow` | 用 `gemarc_brief(section="vocabulary")` 里的 `diagramTypes` 确认允许的实体与关系 |
| Case | `caseType`（14 种分析类型）、`reality`（`Observed`/`ImplementedModel`/`DesignedModel`/`Hypothetical`）、`initialState`、`inputEvents`、`preconditions`、`executionRules`、`expectedOutcomes`、`terminationConditions`、`assertions`、`assumptions` | `category` 是分诊轴（5 类），`caseType` 是分析类型，两者互补；没有真实运行证据时 `reality` 不要写 `Observed` |
| 工作流 | `version`、`entryPoints`、`dataSchemas`、`executionPolicy`、`errorPolicy`、`permissions`；节点侧的 `inputs`/`outputs`/`preconditions`/`postconditions`/`stateTransitions`/`timeoutPolicy`/`retryPolicy`/`errorHandlers`/`sideEffects`/`idempotency`/`implementationRef`；边侧的 `dataMapping`/`priority`/`errorRoute`；端口侧的 `direction`/`cardinality`/`required`/`acceptedTypes` | 条件流必须给可被程序判断的 `condition`；没有真实执行器的节点（agent、human-approval、manual-review）会被标成 Design-only，不要假装它能跑 |
| 6D 执行图（工作流级） | `executionGraph`、`executionMode`、`stateSchemaVersion`、`checkpointPolicy`、`approvalPolicies`、`executionCapabilities`、`runSecurityPolicy` | 这些字段一填，这个工作流就**声明自己是执行图**，12 条 6D 质量门开始对它生效 |
| 6D 执行图（节点级） | `executorKind`、`authorityScope`、`inputStateSchema`、`outputStateSchema`、`stateWritePolicy`、`checkpointBefore`/`checkpointAfter`、`budgetPolicy`、`iterationLimit`、`approvalPolicyRef`、`toolContractRef` | agent-loop 必须给 `iterationLimit`（否则是无界循环）；声明真实执行要同时给权限范围与执行器引用 |
| 6D 执行图（边级） | `stateSchemaRef`、`stateProjection`、`stateMergePolicy`、`routingExpression`、`deliverySemantics`、`resumeBehavior`、`policyRef` | 带 `policyRef` 的边必须给可判断的路由条件 |
| 6D 工作分解（`executionGraph.workPackages`） | `packageId`、`parentPackageId`、`kind`（`phase`/`work-package`/`task`）、`title`、`deliverable`、`completionCriteria`、`nodeIds`、`dependsOn` | 6D = 工作分解 × 执行图：每个包要写清**交付什么**与**何时算完成**，每个执行节点要归属至少一个包；叶子包必须挂执行节点。填了 `workPackages` 就多 4 条 WBS 门（`GEMARC_EXEC_009`…`012`）生效 |

规格的字段拼写与正本拼写都接受（节点的 `type`/`label`、边的 `direction`、工作流的 `nodeType`/`edgeType`/`guard`/`portId`、`caseId`），服务端会归一到正本拼写。写入时用正本拼写更稳。

6D 的责任边界要讲清楚：MCP 负责**模型、契约、校验与派生**；执行器、检查点持久化、审批界面、适配器属于目标工程。声明 `executionMode: "real"` 不等于会真的执行——真实执行需要用户授权的后端。6D 的两半是**工作分解**（做什么、交付什么、何时算完成）与**执行图层**（谁来做、怎么路由、状态怎么交接），两者靠 `workPackages[].nodeIds` 绑定：只写执行图就回答不了「交付什么」，只写工作包就只是甘特图。

## 结果与错误

- 每个工具的结果是一段 JSON 文本，没有 `structuredContent`。
- `status` 为 `invalid` 或 `error` 时，MCP 结果的 `isError` 为 true，正文 JSON 不变。**读正文**，不要只看 `isError`：
  - 校验失败：`errors[]` 和 `warnings[]`，每项有 `id`、`severity`、`message`、`entityId`；`summary` 给出数量。
  - 工具拒绝（越权、参数不合法、未知分段等）：`diagnostics[]`，每项同样有 `id`、`message`。
- 常见 id：`GEMARC_INPUT_003`（`project`/`model` 必须二选一）、`GEMARC_AUTH_001`（越出工作区）、`GEMARC_AUTH_002`（写操作关闭）、`GEMARC_MODEL_002`（模型超过 4 MiB）、`GEMARC_EVIDENCE_00x`（证据不足）、`GEMARC_EXEC_00x`（6D 质量门）、`GEMARC_GUIDE_001`（`gemarc_media_doc_guide` 的分段名未知）。完整含义看 `gemarc_contracts()` 与仓库的 `docs/03_详细设计/02_MCP工具详细契约.md`。
- `status: "error"` 与 `GEMARC_INTERNAL_001` 表示服务端异常，原样报告给用户，不要重试 5 次。

## 大小与限额

- 模型内联传入，**每次**调用都会占用你的上下文；硬上限 4 MiB（`GEMARC_MODEL_002`）。节点 id 用短而稳定的名字，别在模型里塞大段原文。
- `gemarc_render` 的内联上限约 35 KiB（35840 字节；发布示例模型带 6D 工作流，响应实测约 11.7k token），按**累计字节逐文件**判定：放得下的文件给内容，放不下的只给清单。**任何一项缺 `content` 就不能组装**，这时改走本地流程 B，用 `gemarc build <project> --profile <profile>`。
- 客户端对单次工具输出有上限（Claude Code 在 10k token 告警、默认 25k 截断；Codex 约 10k token）。少用不带 `section` 的 `gemarc_brief()`，不要把整个模型反复打印回聊天里。
- 远程端点可能要求 Bearer token：这是用户的连接配置，不是你要处理的东西。收到 401 就告诉用户检查 `GEMARC_MCP_TOKEN`。

## 最终说明里必须有

站点怎么打开；校验结果；每一类证据等级各有多少节点/边（以 `gemarc_brief` 的词表为准）；模拟只是规则 what-if；浏览器/WebGL/性能未测；`Unknown` 清单；**这一轮 UI/UX 优化做了什么**（主内容如何突出、窗格与面板尺寸是否可调、专注模式是否可用）。
