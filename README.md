# GemArc MCP 源码 v0.3.0 — 可运行 MVP

> 2026-10-25；面向《GemArc MCP IPA 设计资料包 v1.0》的首期实现。**不代表全量 SRS 已完成，也不宣称 IPA 官方认证。**

## 已实现和未实现

| 能力 | 状态 | 说明 |
|---|---|---|
| 17 个 MCP 工具（两个工具集） | 实现 | 基于 MCP Python SDK v2 的 `MCPServer`。`full` 17 个（本地 stdio，含工作区工具）；`stateless` 9 个（远程，模型随调用传入）。工具名只含 `[a-z0-9_]`，每个带 `title` 与安全 `annotations`，失败置 `isError`。**已用官方客户端完成真实 stdio 与公网 Streamable HTTP 握手核验** |
| 2 个 MCP Resources + 1 Prompt | 实现 | Registry、简化 Schema 和标准设计策略 |
| 统一模型和安全验证 | 实现 | 稳定 ID、证据等级、Node–Edge、View、Case 约束 |
| 组件注册中心 | MVP | JSON 检索；无独立 UI 与组件包签名 |
| 1D / 2D / UI 原型 | 实现 MVP | 文本、SVG Node–Edge、数据驱动多标签页；暂无 Konva 编辑器 |
| 3D | **实现** | 自带 Three.js 适配器（Building–Floor–Room 真实渲染 + 节点/关系各自可拾取 + 跨维选择同步）；亦可复用用户自行构建的原仓库 `dist/` |
| 4D | 实现 MVP | 离散事件、步进时间轴、确定性规则传播；无动画流体场景 |
| 5D | 实现 MVP | 显式规则与模块屏蔽反事实对照；非一般性因果识别引擎 |
| 增量编译与缓存 | 未完成 | `diff` 提供影响分析，build 仍全量重建 |
| Graphology / Dagre / Dexie | 可选适配器 | 运行时**零第三方硬依赖**：这些库一律工厂函数注入 + 内置兜底；不装也能跑 |
| 浏览器自动化、视觉回归 | 部分 | 有 view-model 结构快照门禁（跑在**构建产物**上）；**像素级视觉回归仍未实现** |
| 远程 MCP 端点（Vercel） | 实现，**已部署并公网实测** | 无状态：模型随调用传入，服务端不保存项目；可选 Bearer 令牌（`GEMARC_MCP_TOKEN`）；`GET /mcp` 返回 405；固定件经 `/kit/<profile>.zip` 下载。`scripts/verify_remote.py` 对公网域名 25/25 通过。详见 `docs/09_部署/01_Vercel部署.md` |
| 安装时完全离线 | 不保证 | Python MCP SDK 需 pip 获取；生成的基础站点不使用 CDN |

## Windows 11 安装 / PowerShell

```powershell
# 解压源码包后进入根目录（包含 pyproject.toml）
cd .\GemArc_MCP_Source_v0.1.0
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .

# 用户明确授权的工作区（示例；可自行更改）
New-Item -ItemType Directory -Force E:\GemArcWork | Out-Null
$env:GEMARC_WORKSPACE_ROOT = 'E:\GemArcWork'
$env:GEMARC_ALLOW_WRITE = '1'  # 显式打开写工具；默认只读

# 无需 MCP Host，即可直接测试核心构建流程
.\.venv\Scripts\python.exe -m gemarc_mcp.cli init demo
.\.venv\Scripts\python.exe -m gemarc_mcp.cli validate demo
.\.venv\Scripts\python.exe -m gemarc_mcp.cli simulate demo normal --disable queue --compare
.\.venv\Scripts\python.exe -m gemarc_mcp.cli build demo
.\.venv\Scripts\python.exe -m gemarc_mcp.cli verify demo
.\.venv\Scripts\python.exe -m gemarc_mcp.cli export demo

# 预览：从生成的站点目录启动 HTTP 服务；不建议直接双击 file://
cd E:\GemArcWork\demo\site
py -3.11 -m http.server 8000 --bind 127.0.0.1
# 浏览器：http://127.0.0.1:8000
```

> 上方 HTTP 命令最后一行需替换为实际安装的 Python 完整路径；或者执行 `py -3.11 -m http.server 8000 --bind 127.0.0.1`。

## MCP Host 配置（stdio）

把 `command` 修改为虚拟环境内的 Python **绝对路径**，`args` 指向模块：

```json
{
  "mcpServers": {
    "gemarc": {
      "command": "E:\\GemArcMCP\\.venv\\Scripts\\python.exe",
      "args": ["-m", "gemarc_mcp"],
      "env": {
        "GEMARC_WORKSPACE_ROOT": "E:\\GemArcWork",
        "GEMARC_ALLOW_WRITE": "1"
      }
    }
  }
}
```

`GEMARC_WORKSPACE_ROOT` 必须是**已存在**的目录；不设置时默认用 `~/.gemarc/workspace`（自动创建）。
Claude Code、Codex、Hermes 的配置容器键名各不相同，下一节与 `integration-examples/` 给出逐个客户端、可直接复制的配置。

## 接入 Claude / Codex / Hermes

本地 stdio（17 个工具，写工具默认关）或远程 `/mcp`（9 个无状态工具）。完整说明、兼容矩阵和每项的验证状态见
`integration-examples/README.md`；先运行 `python scripts/verify_stdio.py` 自检本地服务器，
运行 `python scripts/verify_remote.py https://<project>.vercel.app` 自检远程端点。

- Claude Code：`claude mcp add --transport http --scope project gemarc 'https://<project>.vercel.app/mcp' --header 'Authorization: Bearer ${GEMARC_MCP_TOKEN}'`。**用单引号**，否则令牌会明文写进 `.mcp.json`。
- Codex：`codex mcp add gemarc --url 'https://<project>.vercel.app/mcp' --bearer-token-env-var GEMARC_MCP_TOKEN`。
- Hermes：`hermes mcp add gemarc --url https://<project>.vercel.app/mcp`。
- claude.ai / Claude Desktop 的远程 connector 只支持 OAuth 或无认证（本仓库未实现 OAuth）：要用就得让部署不设 `GEMARC_MCP_TOKEN`，代价是端点对知道 URL 的人开放。
- **连上 MCP 就已经具备「制作多媒体文档」的能力**：多媒体文档制作流程随服务器发布，工具 `gemarc_media_doc_guide`（或资源 `gemarc://skill/gemarc-media-doc`）就是它，模型按 brief → new_model → 填写 → validate → simulate → render 的顺序执行即可，**不必再单独装 skill**（ADR-012）。
- `skills/gemarc-media-doc/` 是同一份工作流的独立副本（SKILL.md），用于离线查阅或给不读 MCP 资源的客户端；它与服务器发的那份由 `tests/test_skill_pack.py` 断言不会漂移。

> 上述客户端命令中，只有 Claude Code 加载 `.mcp.json` 并调用本地工具、Codex 的 `mcp add/list/get` 与 `--strict-config`
> 被实际运行过；Codex 真正调用工具、Hermes、Claude Desktop 未验证（见 `integration-examples/README.md` 矩阵）。

**安全提示**：写权限仅应由本地用户在可信配置中授权。设置 `GEMARC_ALLOW_WRITE=1` 后，允许当前 MCP 客户端调用写入工具；生产环境应增加按调用授权、审计和隔离策略。不要将服务无认证直接暴露到公网。

## 脚手架（固定格式 + 共用代码）

本 MCP 的主要用途是**提供制作多媒体文档的脚手架**：把「可被写死的东西」从大模型手里拿走，
模型只填事实。脚手架包含三层固定件：

| 层 | 内容 | 位置 |
|---|---|---|
| 契约层 | 9 份 JSON Schema（Canonical Model / Node / Edge / Evidence / Space / View / Scenario / Workflow / 6D 执行图）+ 1 份执行图示例 | `src/gemarc_mcp/scaffold/contracts/` |
| 词表层 | 6 级证据等级、21 种实体类型、21 种关系类型、21 种图示类型、相关性分级、空间层级、Case 分类（5 类分诊轴 + 14 种分析类型）、工作流语义（GWF-IR） | `src/gemarc_mcp/scaffold/vocab/` |
| 运行层 | 图引擎、Focus 退避、3D 空间、4D/5D 仿真、跨视图状态、工作流语义、互操作适配器 | `src/gemarc_mcp/scaffold/runtime/` |

词表是**提示词、Python 校验器、浏览器运行时三方共用的唯一事实源**；
浏览器侧读取由同一份 JSON 生成的模块，漂移会被 `tests/test_scaffold_vocab.py` 直接拦下。

### 使用流程

```powershell
# 0. 先取工作指令：模型不必自己读文件推导该做什么
E:\GemArc\.venv\Scripts\python.exe -m gemarc_mcp.cli brief demo

# 1. 生成脚手架：固定件 + 待填槽位
E:\GemArc\.venv\Scripts\python.exe -m gemarc_mcp.cli scaffold demo --profile static

# 2. 填写 <workspace>/demo/model/architecture.json 的槽位
#    槽位清单在 demo/scaffold/scaffold-manifest.json 与 FILL-IN.md 里
#    每个槽位都写明了 JSON Pointer、填什么、以及约束它的规则

# 3. 校验
E:\GemArc\.venv\Scripts\python.exe -m gemarc_mcp.cli validate demo --strict

# 4. 产出站点
E:\GemArc\.venv\Scripts\python.exe -m gemarc_mcp.cli build demo --profile static
```

对应的 MCP 工具是 `gemarc_brief`（精简工作指令）、`gemarc_scaffold`（落固定件与槽位）
与 `gemarc_contracts`（读取契约与词表内容）。

`gemarc_brief` 一次性返回：目标、五步流程、六条硬规则、12 个槽位（带 JSON Pointer 与规则）、
全部允许词值（6 级证据 / 21 实体类型 / 21 关系类型 / 21 图示类型 / Case 分类 / 空间层级）、
构建命令，以及「哪些固定件已经写好、不要重写」的清单。**实测约 10 KB**，
用来替掉原先 13323 字节的提示词规格。

### 两种交付 profile，共用一份源码

| profile | 模块解析 | 依赖 | 启动 |
|---|---|---|---|
| `static` | import map | 无（可选库走 CDN 或本地 vendor） | `python -m http.server 8000` |
| `vite` | Vite alias | npm 安装 three / graphology / dagre / dexie / sigma | `npm install && npm run dev` |

应用源码用与 profile 无关的 `@gemarc/runtime/...` 导入，因此不会出现两份实现漂移。

### 3D 空间适配器（Three.js）

`runtime/space/space-viewer-three.js` 是随脚手架一起下发的 3D 渲染器，**不硬依赖 three**：

```js
const renderer = createThreeSpaceRenderer(THREE, { container, model, space, store, onSelectNode, onSelectEdge, onHover });
```

`static` profile 走 import map 指向 CDN 的 three，`vite` profile 走 `node_modules`，测试注入行为对齐的桩。

- **空间语义落到三维**：Building 与 Room 画线框，Floor 是唯一实心板；Room 有垂直体量，节点站在房间地板上。
- **拾取按屏幕像素度量**：节点用射线 + 10px 容差，关系用投影线段 + 12px 容差；节点重叠时节点胜出。
  世界半径不是目标大小——同一条边近处 1px、远处 10px，细管在斜视角下几乎打不中。
- **取景按视锥精确贴合包围盒**（`fitDistanceToBox()`），不用包围球半径——工程空间通常扁长。
- **WebGL 不可用时显式降级并说明原因**（`GEMARC_SPACE_010`），绝不用静态图或 2D 伪装三维。
- **三维操作与 Unity 一致**：Alt + 左键环绕、中键平移、右键飞行（W/A/S/D 与 Q/E）、滚轮推拉、
  左键选择、Ctrl + 左键加选、空白处拖动框选、Esc 清空、F 聚焦。映射表只有一份，在
  `runtime/space/unity-controls.js`；两个三维场景共用它，不会各说各话。

设计细节与决策理由见 `docs/02_基本设计/09_脚手架与共用运行时设计.md` §6.4。

### 4D/5D 仿真内核：两端同构

仿真存在两份实现：Python（`simulation.py`，MCP 工具的权威）与 JavaScript（浏览器侧，
让动画真的由计算驱动）。两份实现会漂移，所以有**黄金用例**：
`src/gemarc_mcp/scaffold/cases/simulation-golden.json` 保存 8 个场景的逐字预期输出，
`tests/test_sim_consistency.py` 用 Node 跑同一批用例并要求逐字相等。

设计细节见 `docs/02_基本设计/09_脚手架与共用运行时设计.md`。

## 远程端点（Vercel）

仓库自带一个 Vercel 部署形态：单个 Python Function 同时提供 MCP Streamable HTTP 端点与多媒体架构文档站。

```powershell
cd E:\GemArc
npx vercel@65.8.0 login
npx vercel@65.8.0 link
npx vercel@65.8.0 deploy --prod
```

部署后：

| 路径 | 内容 |
|---|---|
| `https://<domain>/mcp` | MCP Streamable HTTP 端点 |
| `https://<domain>/healthz` | 健康检查与部署状态 |
| `https://<domain>/` | **MCP 功能微调控制台**（Web 面板：选工具 → 调参数 → 看真实返回；设了令牌时需先粘贴令牌） |
| `https://<domain>/site/` | 脚手架产出的多媒体架构文档（含 3D 适配器，首次访问时从包内源码物化） |
| `https://<domain>/kit/static.zip` | 站点**固定件**（外壳 + 共用运行时 + 词表）的确定性压缩包；与 `gemarc_render` 返回的 `data/*.json` 合并即是完整站点 |

主页是**控制台**而不是设计图：远程端点的主角是 MCP 本身。面板里列出的工具、参数表单、
输入契约全部来自 `tools/list` 的真实返回，写工具由服务端下发的名单上锁，
所以它不会声称服务器没有的能力。

远程客户端接入：

```json
{
  "mcpServers": {
    "gemarc-remote": {
      "type": "http",
      "url": "https://<domain>/mcp"
    }
  }
}
```

**远程形态与本地的区别**：远程默认是 `stateless` 工具集（`gemarc_brief / contracts / assets / new_model / validate / simulate / diff / render`），
模型随每次调用传入，服务端不保存任何项目，因此没有跨调用状态丢失的问题，也没有写工具。
`gemarc_render` 只返回随模型变化的 `data/*.json`（内联上限 24 KB，按累计字节逐文件判定：放不下的文件只给清单与摘要）；固定件通过 `/kit/<profile>.zip` 下载。
需要工作区、扫描本地仓库、`build/export` 时用本地 stdio 模式。

**鉴权**：设置环境变量 `GEMARC_MCP_TOKEN`（可逗号分隔多个，便于轮换）后，`POST /mcp` 必须带 `Authorization: Bearer <token>`；
不设置则端点对知道 URL 的人开放（claude.ai 自定义 connector 需要这种形态）。`/healthz`、控制台、`/site/`、`/kit/` 始终公开且不含密钥或路径。
完整说明、已实测/待验证清单见 `docs/09_部署/01_Vercel部署.md`。
**账号（可选，默认关闭）**：Google 登录、个人访问令牌（PAT）与数据库存储的设计、运营手册见 `docs/09_部署/02_账号与安全.md`。
开启后本部署变成「登录才能用」：控制台 `/` 需要登录会话；`POST /mcp` 同时认个人访问令牌与登录会话（会话通道要求同源）；
`/account/admin` 是**仅管理员**的设置区（用户管理、审计日志、站点与工具集、令牌策略）。管理员 = 环境变量
`GEMARC_ACCOUNTS_ADMIN_EMAILS` 白名单**或**数据库表 `admin_users` 里的运行时授予（两者取并集，收权要两边都做）。

## 复用现有 GemArc Three.js Viewer

现有 GemArc 仓库：`https://github.com/UlyssesLeoLee/GemArc`。本包**未复制**原仓库源文件，避免造成隐含版本漂移。3D 集成流程：

```powershell
<<<<<<< HEAD
git clone https://github.com/UlyssesLeoLee/GemArc.git E:\GemArcWork\upstream
cd E:\GemArcWork\upstream\skills\ipa-architecture-3d
python .\scripts\build.py .\assets\examples\illustrative-architecture.json --out E:\GemArcWork\legacy-build
cd E:\GemArcWork\legacy-build
=======
hermes plugins install https://github.com/UlyssesLeoLee/AAArchitect.git --force
```

当前实测只有 **1 项** finding，逐条核对为启发式误报：

| 判据 | 位置 | 实际情况 |
| --- | --- | --- |
| `inline_shell_exec` | `assets/viewer/src/main.js`（`root.replaceChildren()`） | DOM 元素清空方法，被 shell 执行启发式误判。全文件不调用 `exec`/`eval`/`spawn`，也无任何进程创建；所有文本均通过 `textContent` 写入，不存在 HTML 注入 |

早期的 6 项 finding 中，README 里的 `git clone` 文案、`i18n.js` 的中日文字面量、SKILL.md 的 npm 构建步骤，随文档改写已不再触发。该拦截由启发式规则驱动，finding 数量会随源码变化，**以你本机 `hermes plugins validate .` 的实际输出为准**。

| 判据 | 位置 | 实际情况 |
| --- | --- | --- |
| `execution` (HIGH) | `assets/viewer/src/main.js:241` | 搜索过滤用的模板字符串，渲染走 `textContent`，不执行任何 shell |
| `supply_chain` (MEDIUM) | `README.md` 的安装说明 | 文档里的 `git clone` 命令被当成供应链风险 |
| `supply_chain` (MEDIUM) | `assets/viewer/src/i18n.js:3` / `:6` | 中/日文界面文案被当成可疑非 ASCII 字符串 |
| `supply_chain` (MEDIUM) | `SKILL.md:28` | `npm install && npm run test && npm run build` 构建步骤被当成包管理器调用 |

这些由文档与多语言资源本身触发，不应为绕过扫描而删改。也可在 `config.yaml` 里设 `plugins.scan_on_install` 关闭拦截。技能通道 `hermes skills install <raw URL>` 不受此拦截影响。

Skill 通道（不经过 PM）：

```powershell
hermes skills list
```

Hermes 的 Skill 根目录是 `$env:LOCALAPPDATA\hermes\skills\<分类>\`，不是 `~/.hermes/skills`。

### 4. MiniMax Code —— 本地插件包

```powershell
.\skills\ipa-architecture-3d\scripts\build_minimax_plugin.ps1
```

脚本从仓库内容组装 MiniMax 插件包（`.minimax-plugin/plugin.json` + `skills/<name>/SKILL.md` + 明暗图标），写入 `%USERPROFILE%\.minimax\plugins\ipa-architecture-3d\`，MiniMax Code 自动重扫识别。图标优先取本机 MiniMax 自带的分类图标池，取不到时自动生成占位图，因此在没装 MiniMax 的机器上也能构建。版本号与描述直接读仓库根目录 `plugin.json`，不会与源文件脱节。

注意：包根目录**不要**放 Agent Plugins v1 的 `plugin.json`，否则会覆盖 MiniMax 格式；只用 `.minimax-plugin/plugin.json`。

### 只装 Skill（不装插件包）

```powershell
cd .\skills\ipa-architecture-3d
.\scripts\install_windows.ps1 -Agents claude,codex,hermes,minimax
.\scripts\install_windows.ps1 -Agents claude,codex,hermes,minimax -Force   # 覆盖前自动备份
```

这条路径只做 Skill 目录投递：`hermes` 和 `minimax` 在这里是 **Skill 回退通道**（`$env:LOCALAPPDATA\hermes\skills\<分类>\` 和 `~/.minimax/skills/`），不是它们各自的插件包机制。需要完整插件包请用仓库根目录的 `install.ps1`。

> Windows PowerShell 5.1 注意事项：仓库内 `.ps1` 文件带 UTF-8 BOM。手动编辑后若删掉 BOM，脚本里的中文字面量会被按 ANSI 解析并导致语法错误。


## 生成真正的 Three.js 网站

```powershell
cd .\skills\ipa-architecture-3d
python .\scripts\validate.py .\assets\examples\illustrative-architecture.json
python .\scripts\build.py .\assets\examples\illustrative-architecture.json --out E:\arch3d-cn
# 需要日语时：追加 --lang ja-JP
cd E:\arch3d-cn
>>>>>>> d3cb55f3320c45ed462dfd182f817e14446ece8c
npm install
npm run build

# 使用本项目 CLI；替换此处为源码包根目录运行环境
$env:GEMARC_WORKSPACE_ROOT = 'E:\GemArcWork'
$env:GEMARC_ALLOW_WRITE = '1'
python -m gemarc_mcp.cli build demo --legacy-dist E:\GemArcWork\legacy-build\dist
```

> 上述命令借用原仓库演示数据；**不能将演示 3D 架构错当成当前项目真实架构**。正式使用时，须将目标工程转成原仓库支持的 Architecture Model，严格核对适配和事实证据。
> 自带的 Three.js 适配器已实现 3D 与 2D 之间的**跨维双向选择同步**（浏览器实测通过）；
> 复用原仓库 Viewer 的路径仍然保留，作为「已有产物直接接入」的手段。

## GemArc Atlas（仅本地，未部署）

`src/gemarc_mcp/atlas/` 是 Atlas 工具集：AI 只写事实（紧凑的 `.atl` 切片），固定、经过测试的代码负责校验、推导和渲染，产出 5D 设计资料（查看器页面、三层文档、图、提示词、oracle 表）。它**只在本地使用**：`.vercelignore` 把它挡在 Vercel 上传之外，远程默认工具集仍是 `stateless`，默认工具集和远程端点的行为都没有改变。

启用（本地 stdio）：命令仍是 `python -m gemarc_mcp`，在 MCP 配置的环境里设置 `GEMARC_TOOLSET=atlas`、`GEMARC_WORKSPACE_ROOT=<工作区>`、`GEMARC_ALLOW_WRITE=1`、`GEMARC_ATLAS_ROOTS=main=<被分析的仓库>`。Claude Code、Codex、Hermes 的配置示例、怎样说「创建5D设计」、输出目录和限制见 `docs/atlas/README.md`，工具契约见 `docs/atlas/ATLAS_TOOLS.md`。没有 MCP 接入的客户端可以用 `python tools/make_kit.py` 生成 `dist-kit/atlas-kit.zip`（纯标准库命令行工具包，不提交）。

## 代码模块

- `src/gemarc_mcp/server.py`：MCP v2 工具、资源与提示词接口。
- `src/gemarc_mcp/service.py`：扫描、构建、导出、复用适配器。
- `src/gemarc_mcp/model.py`：统一事实模型与证据校验。
- `src/gemarc_mcp/simulation.py`：确定性离散事件与因果干预。
- `src/gemarc_mcp/paths.py`：授权工作区和安全文件 I/O。
- `src/gemarc_mcp/web/`：可独立运行的标准页面。
- `tests/`：UT 和集成层核心测试（无需联网、无需 MCP SDK）。

## 测试

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -v
```

测试包含 **192 项 Python UT/集成检查**（`unittest discover`；另有 `tests/test_tool_surface_docs.py` 等守卫工具名与文档不漂移），另有一批 **125 项 Node 运行时测试**
（`node --test src/gemarc_mcp/scaffold/runtime/tests/*.test.mjs`）覆盖脚手架运行层：图引擎、Focus 退避、
3D 空间与 **Three.js 适配器**、仿真内核、工作流语义、互操作适配器。

3D 适配器的自动化测试用的是**行为对齐真实 three 的桩**（`runtime/tests/helpers/three-stub.mjs`：
`Vector3` / `Quaternion` / `Object3D` 矩阵语义、射线-盒、射线-开口圆柱、`Vector3.project`）。
真实 WebGL 渲染另有一次**浏览器实测**记录（见设计文档 §7），但**像素级视觉回归尚未实现**，
自动化测试也不构成「像素正确」的证据。

其中 `tests/test_http_transport.py` 会**真实启动 ASGI 应用并用官方 MCP 客户端完成一次 Streamable HTTP 握手**（`initialize` → `list_tools` → `call_tool`），覆盖工具面完整性、只读工具可用、模拟确定性、写工具门禁、工作区越界拒绝、健康检查与静态站托管。`tests/test_mcp_contract.py` 只用 FakeServer 替换 SDK，验证装饰器接线，**不构成握手证据**——两者缺一不可。未安装 MCP SDK 时 HTTP 传输测试自动跳过。

测试覆盖现有核心代码的关键路径，但不等同于 90% 覆盖率证明或 UT/IT/ST 全部完成。请根据 IPA 资料包中的 Traceability 和测试计划继续完善。

## 许可证

本次新增源代码以 MIT 授权（见 `LICENSE`）。如将 GemArc 原有 Three.js 产物或其他依赖一起再发布，须重新检查对应的 LICENSE、第三方 NOTICE 和版本约束；本包不代表外部依赖全部完成许可审查。


## 随附预览

`examples/demo-site/` 是**随包发布的离线预览**（`mvp` 档，示例事实，不是真实工程）：
在目录内运行 `py -3.11 -m http.server 8000 --bind 127.0.0.1` 后访问 http://127.0.0.1:8000 。

它是**生成物**，必须与生成它的代码同步。`tests/test_generated_artifacts.py` 会用真实构建路径
重新生成一份并与仓库里的这份**逐字节比对**，防止再次出现「仓库里有新代码、随附站点还是旧的」。

> 这道门禁是被迫加上的：上一次模型字段从 `source`/`target`/`kind` 迁到
> `sourceId`/`targetId`/`type` 时，随附站点的 `app.js` 没有一起更新，
> 浏览器实测显示它**画出 4 个节点、0 条关系**——仓库里的一切测试都是绿的，站点却是坏的。

完整的多媒体脚手架站（含 3D 适配器）请用 `gemarc build --profile static` 生成；
远程端点提供的也是这一档（首次访问 `/site/` 时从包内源码物化，见部署文档）。

### 站点外壳的 UI/UX 基线

产出的站点外壳自带下面这些能力，制作多媒体文档时**直接用**，不要手改固定件：

| 能力 | 表现 |
|---|---|
| 主要显示内容占比大 | 主栏是主内容区，结构图/时间轴/工作流图是首屏主体 |
| 窗格可调宽度 | 两侧窗格之间有可拖拽分隔条（双击复位、方向键微调），尺寸存浏览器本地存储 |
| 窗格可调高度 | 每个内容面板右下角有拖拽手柄，可单独调整宽与高 |
| 专注模式 | 顶栏「专注」把两侧窗格收成一条窄导航条，主内容占满窗口，页签仍然可达 |
| 主内容不被挤小 | 主栏有最小宽度下限，两侧窗格各有最小宽度 |

服务器随包发布的那份流程正文把「一轮 UI/UX 优化」列为**硬规则**：每份产出都要按上面这套基线
复核一遍，并把这一轮做了什么写进最终说明。
