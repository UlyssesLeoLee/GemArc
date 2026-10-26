# GemArc MCP 源码 v0.1.0 — 可运行 MVP

> 2026-10-25；面向《GemArc MCP IPA 设计资料包 v1.0》的首期实现。**不代表全量 SRS 已完成，也不宣称 IPA 官方认证。**

## 已实现和未实现

| 能力 | 状态 | 说明 |
|---|---|---|
| 14 个 MCP 工具 | 实现 | 基于 MCP Python SDK v2 的 `MCPServer`。**已用官方客户端完成真实 stdio 与 Streamable HTTP 握手核验**（14 tool / 2 resource / 1 prompt 全部调通） |
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
| 远程 MCP 端点（Vercel） | 实现，**受限形态** | Streamable HTTP + 静态站同在一个函数；写工具默认关闭；文件系统临时，跨调用状态会丢。详见 `docs/09_部署/01_Vercel部署.md` |
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
      "args": ["-m", "gemarc_mcp.server"],
      "env": {
        "GEMARC_WORKSPACE_ROOT": "E:\\GemArcWork",
        "GEMARC_ALLOW_WRITE": "1"
      }
    }
  }
}
```

Claude Code、Codex、Cursor 和其他 MCP Host 的配置容器键名可能不同，请以对应客户端当前文档为准；本示例仅说明服务器 command/args/env。

**安全提示**：写权限仅应由本地用户在可信配置中授权。设置 `GEMARC_ALLOW_WRITE=1` 后，允许当前 MCP 客户端调用写入工具；生产环境应增加按调用授权、审计和隔离策略。不要将服务无认证直接暴露到公网。

## 5D 脚手架（固定格式 + 共用代码）

本 MCP 的主要用途是**提供制作 5D 设计的脚手架**：把「可被写死的东西」从大模型手里拿走，
模型只填事实。脚手架包含三层固定件：

| 层 | 内容 | 位置 |
|---|---|---|
| 契约层 | 8 份 JSON Schema（Canonical Model / Node / Edge / Evidence / Space / View / Scenario / Workflow） | `src/gemarc_mcp/scaffold/contracts/` |
| 词表层 | 6 级证据等级、21 种实体类型、21 种关系类型、19 种图示类型、相关性分级、空间层级、Case 分类 | `src/gemarc_mcp/scaffold/vocab/` |
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

对应的 MCP 工具是 `gemarc.brief`（精简工作指令）、`gemarc.scaffold`（落固定件与槽位）
与 `gemarc.contracts`（读取契约与词表内容）。

`gemarc.brief` 一次性返回：目标、五步流程、六条硬规则、12 个槽位（带 JSON Pointer 与规则）、
全部允许词值（6 级证据 / 21 实体类型 / 21 关系类型 / 19 图示类型 / Case 分类 / 空间层级）、
构建命令，以及「哪些固定件已经写好、不要重写」的清单。**实测 8858 字节**，
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

设计细节与决策理由见 `docs/02_基本设计/09_脚手架与共用运行时设计.md` §6.4。

### 4D/5D 仿真内核：两端同构

仿真存在两份实现：Python（`simulation.py`，MCP 工具的权威）与 JavaScript（浏览器侧，
让动画真的由计算驱动）。两份实现会漂移，所以有**黄金用例**：
`src/gemarc_mcp/scaffold/cases/simulation-golden.json` 保存 8 个场景的逐字预期输出，
`tests/test_sim_consistency.py` 用 Node 跑同一批用例并要求逐字相等。

设计细节见 `docs/02_基本设计/09_脚手架与共用运行时设计.md`。

## 远程端点（Vercel）

仓库自带一个 Vercel 部署形态：单个 Python Function 同时提供 MCP Streamable HTTP 端点与 5D 静态站。

```powershell
cd E:\GemArc
npx vercel@65.2.0 login
npx vercel@65.2.0 link
npx vercel@65.2.0 deploy --prod
```

部署后：

| 路径 | 内容 |
|---|---|
| `https://<domain>/mcp` | MCP Streamable HTTP 端点 |
| `https://<domain>/healthz` | 健康检查与部署状态 |
| `https://<domain>/` | 5D 架构静态站（冷启动时从包内源码物化，附 3D 适配器） |

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

**远程形态是受限的，不是本地模式的等价物**：Vercel 函数文件系统临时，跨调用的项目状态会丢；
`gemarc.export` 返回的是本地路径而非下载链接；写工具默认关闭（`GEMARC_ALLOW_WRITE` 未设为 `1` 时拒绝执行）。
完整说明、已实测/待验证清单见 `docs/09_部署/01_Vercel部署.md`。

## 复用现有 GemArc Three.js Viewer

现有 GemArc 仓库：`https://github.com/UlyssesLeoLee/GemArc`。本包**未复制**原仓库源文件，避免造成隐含版本漂移。3D 集成流程：

```powershell
git clone https://github.com/UlyssesLeoLee/GemArc.git E:\GemArcWork\upstream
cd E:\GemArcWork\upstream\skills\ipa-architecture-3d
python .\scripts\build.py .\assets\examples\illustrative-architecture.json --out E:\GemArcWork\legacy-build
cd E:\GemArcWork\legacy-build
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

测试包含 **77 项 Python UT/集成检查**（`unittest discover`），另有一批 **125 项 Node 运行时测试**
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

完整的 5D 脚手架站（含 3D 适配器）请用 `gemarc build --profile static` 生成；
远程端点提供的也是这一档（冷启动时从包内源码物化，见部署文档）。
