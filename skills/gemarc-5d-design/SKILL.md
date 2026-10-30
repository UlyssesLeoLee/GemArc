---
name: gemarc-5d-design
description: Use when the user wants to turn the facts of a software system (their repository, design documents, ADRs) into a reviewable 0D-5D architecture model and a static site through the GemArc MCP server (tools named gemarc_*), or asks to validate, simulate, diff or render such a model. Covers the remote stateless workflow (gemarc_brief, gemarc_new_model, gemarc_validate, gemarc_simulate, gemarc_render, then assemble the downloaded site kit) and the local full workflow (gemarc_inspect, gemarc_scaffold, gemarc_build, gemarc_verify, gemarc_export). Not for drawing diagrams by hand or for the Three.js generator that does not go through this MCP server (see ipa-architecture-3d).
compatibility: Needs the GemArc MCP server connected, either remote over Streamable HTTP (stateless toolset, 8 tools) or local over stdio (full toolset, 16 tools). Assembling a site needs curl or PowerShell plus Python 3 (static profile) or Node 20+ (vite profile).
metadata:
  version: "1.0.0"
  default_language: "zh-CN"
  server: "gemarc-mcp"
---

# GemArc 5D Design — 用 MCP 工具生成可审查的架构站点

GemArc 把「可被写死的东西」（字段名、词表、校验规则、模拟引擎、站点外壳）固定在服务端，只让你填**事实**。你的工作是从用户的仓库里读出真实证据、填进统一模型、让校验通过、再取回站点。**不要重新发明引擎、字段名或算法。**

下文用裸工具名。客户端可能显示为 `mcp__gemarc__gemarc_validate`（Claude Code、Hermes）或命名空间 `mcp__gemarc` 下的 `gemarc_validate`（Codex）；按客户端显示的名字调用即可。

## 先判断模式

看你**实际拿到**的工具列表，不要靠猜：

| 看到 | 模式 | 特点 |
|---|---|---|
| 只有 8 个工具：`gemarc_brief` `gemarc_contracts` `gemarc_assets` `gemarc_new_model` `gemarc_validate` `gemarc_simulate` `gemarc_diff` `gemarc_render` | **远程 / 无状态**（流程 A） | 没有工作区，服务端不保存任何东西；模型每次随调用以 `model` 参数内联传入；服务端看不到用户的仓库 |
| 16 个工具（另有 `gemarc_inspect` `gemarc_plan` `gemarc_scaffold` `gemarc_init` `gemarc_model` `gemarc_build` `gemarc_verify` `gemarc_export`） | **本地 / 完整**（流程 B） | 项目存在工作区目录里；写工具需要用户开启 `GEMARC_ALLOW_WRITE=1` |

## 硬规则（两种模式都适用）

1. **事实不可臆造。** 每个节点、每条关系都要指回一个 evidence 实体；指不回去就标 `Unknown`，写进 `missing`，不要补全、不要「合理推断」成事实。
2. **证据等级、实体种类、边类型、图类型一律以服务端为准。** 需要时调用 `gemarc_brief(section="vocabulary")`，不要凭记忆写，也不要在回复里重述一份词表。其他可取的分段：`slots`、`rules`、`procedure`、`commands`、`alreadyDone`（`gemarc_brief()` 不带 `section` 约 10 KB，一次会话读一次就够）。
3. **模拟是基于规则的 what-if，不是因果识别。** `gemarc_simulate` 的结论强度不会超过场景自身的证据等级；`hypothetical` 场景的结果不得写成「实际运行结果」。汇报时说明这一点。
4. **写操作是用户的决定。** 本地写工具（`gemarc_scaffold` `gemarc_init` `gemarc_model` `gemarc_build` `gemarc_export`）只有在用户的 MCP 配置里设了 `GEMARC_ALLOW_WRITE=1` 时才会执行；收到 `GEMARC_AUTH_002` 就告诉用户如何开启，**不要**自己改配置或换办法绕过。路径越出 `GEMARC_WORKSPACE_ROOT` 会得到 `GEMARC_AUTH_001`，同样不要绕。
5. **不夸大验证范围。** `gemarc_verify` 只检查生成站点的文件完整性和 schema；浏览器、WebGL、性能行为没有测过，必须在最终说明里写出来。
6. **不改固定件。** 站点外壳与共用运行时（kit 或 `scaffold/` 下的固定文件）不要手改；定制放进模型的槽位里。
7. **一个模型是唯一事实源。** 所有视图、模拟、站点数据都从同一个模型派生，不要在站点文件里另写一份事实。

## 流程 A：远程（无状态）

1. `gemarc_brief()`：取得工作指令、槽位清单和词表（必要时只取 `section="slots"` / `"vocabulary"`）。
2. `gemarc_new_model(system="<系统名>")`：返回空模型 `model` 和槽位说明 `slots`。系统名 ≤ 80 字符。
3. **用你自己的文件访问能力**读用户的仓库、文档、ADR，按槽位逐项填写。每个事实记下出处；缺的写 `Unknown`。服务端读不到仓库，也不会替你读。
4. 校验循环：`gemarc_validate(model=<对象>, strict=false)` → 按 `errors[].id` 与 `entityId` 逐条修 → 再校验，直到 0 个 error。需要宣称「已确认」时再用 `strict=true`。strict 要求节点和边有「已实现或已观测」的证据：一个全是 `Hypothetical` 的模型在 strict 下必然失败，正确的做法是补真实证据，**不是改标签**。同一批 id 连续三轮改不掉，就停下来问用户，不要硬凑。
5. 4D/5D：`gemarc_simulate(model=…, scenario_id="…")`；要做对比用 `compare_baseline=true` 加 `disabled_nodes=["<节点 id>", …]`。场景只用真实事件或明确标注的假设。
6. `gemarc_render(model=…, profile="static")`（或 `"vite"`）：校验通过后返回随模型变化的 `data/*.json`（`files[]`）和固定件 `kit` 的下载信息。
7. 组装站点：下载 `kit`、核对 `kit.archiveSha256`、解压、把 `files[]` 逐个写进同一目录、核对每个文件的 `sha256`，再用 `kit.run` 的命令本地预览。逐步命令（bash 与 PowerShell）见 [references/assemble-site.md](references/assemble-site.md)。
8. 汇报：站点位置、校验结果（含 warnings）、哪些事实是 `Unknown`/`Hypothetical`、哪些浏览器能力没测过。

`gemarc_diff(base_model=…, target_model=…)` 比较两个版本的模型（新增、删除、修改的节点与边，以及相邻节点）。

## 流程 B：本地（完整）

`GEMARC_WORKSPACE_ROOT` 必须**包含**要扫描的仓库，生成物也写在这个目录里。

1. `gemarc_brief()`，必要时 `gemarc_contracts()`、`gemarc_assets(query="…")`。
2. `gemarc_inspect(path="<工作区内的仓库路径>")`：只读扫描。返回的候选模块**都未确认**，必须对照源码核实后才能当事实。
3. `gemarc_plan(project="…")`：看哪些维度（1D–5D）现有证据撑得住、缺什么。
4. `gemarc_scaffold(project="…", profile="static")`：落下固定件和空模型 `<project>/model/architecture.json`。已有的脚手架不会被覆盖。（只想看一个带示例数据的项目用 `gemarc_init`；示例不是真实事实。）
5. 填模型：直接编辑 `<project>/model/architecture.json`，或用 `gemarc_model(project, replacement_model, expected_sha256)`。后者的 `expected_sha256` 必须取自**最近一次** `gemarc_validate(project=…)` 的 `modelSha256`；不一致或模型无效时整体拒绝、不写入。
6. 校验循环：`gemarc_validate(project="…")`，修到 0 个 error；宣称「已确认」时再加 `strict=true`（同流程 A 第 4 步：strict 下 `Unknown`/`Hypothetical` 必然不过，补真实证据，**不是改标签**）。
7. `gemarc_simulate(project="…", scenario_id="…")`，同流程 A 第 5 步。
8. `gemarc_build(project="…", profile="static")`（`mvp` 小型查看器；`static` 零构建 ESM；`vite` npm 工程）→ `gemarc_verify(project="…")` → `gemarc_export(project="…")`（ZIP 与其 sha256 在项目目录里）。`gemarc_build` 会替换上一次生成的站点。

`validate`、`simulate`、`diff` 里 `project` 与 `model` **二选一**，两个都给或都不给得到 `GEMARC_INPUT_003`。

## 结果与错误

- 每个工具的结果是一段 JSON 文本，没有 `structuredContent`。
- `status` 为 `invalid` 或 `error` 时，MCP 结果的 `isError` 为 true，正文 JSON 不变。**读正文**，不要只看 `isError`：
  - 校验失败：`errors[]` 和 `warnings[]`，每项有 `id`、`severity`、`message`、`entityId`；`summary` 给出数量。
  - 工具拒绝（越权、参数不合法、未知分段等）：`diagnostics[]`，每项同样有 `id`、`message`。
- 常见 id：`GEMARC_INPUT_003`（`project`/`model` 必须二选一）、`GEMARC_AUTH_001`（越出工作区）、`GEMARC_AUTH_002`（写操作关闭）、`GEMARC_MODEL_002`（模型超过 4 MiB）、`GEMARC_EVIDENCE_00x`（证据不足）。完整含义看 `gemarc_contracts()` 与仓库的 `docs/03_详细设计/02_MCP工具详细契约.md`。
- `status: "error"` 与 `GEMARC_INTERNAL_001` 表示服务端异常，原样报告给用户，不要重试 5 次。

## 大小与限额

- 模型内联传入，**每次**调用都会占用你的上下文；硬上限 4 MiB（`GEMARC_MODEL_002`）。节点 id 用短而稳定的名字，别在模型里塞大段原文。
- `gemarc_render` 派生文件合计超过约 24 KiB（24576 字节，三十来个节点的模型就会到这个量级）时 `inline` 为 `false`：只给清单和摘要，不给内容，并提示在本地用 `gemarc build <project> --profile <profile>`。遇到这种规模的模型，改走本地流程 B。
- 客户端对单次工具输出有上限（Claude Code 在 10k token 告警、默认 25k 截断；Codex 约 10k token）。少用不带 `section` 的 `gemarc_brief()`，不要把整个模型反复打印回聊天里。
- 远程端点可能要求 Bearer token：这是用户的连接配置，不是你要处理的东西。收到 401 就告诉用户检查 `GEMARC_MCP_TOKEN`。

## 最终说明里必须有

站点怎么打开；校验结果；每一类证据等级各有多少节点/边（以 `gemarc_brief` 的词表为准）；模拟只是规则 what-if；浏览器/WebGL/性能未测；`Unknown` 清单。
