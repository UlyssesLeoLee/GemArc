# GemArc MCP 源码 v0.1.0 — 可运行 MVP

> 2026-10-25；面向《GemArc MCP IPA 设计资料包 v1.0》的首期实现。**不代表全量 SRS 已完成，也不宣称 IPA 官方认证。**

## 已实现和未实现

| 能力 | 状态 | 说明 |
|---|---|---|
| 11 个 MCP 工具 | 实现 | 基于 MCP Python SDK v2 的 `MCPServer`。代码静态审查；SDK 端到端握手需在安装后核验 |
| 2 个 MCP Resources + 1 Prompt | 实现 | Registry、简化 Schema 和标准设计策略 |
| 统一模型和安全验证 | 实现 | 稳定 ID、证据等级、Node–Edge、View、Case 约束 |
| 组件注册中心 | MVP | JSON 检索；无独立 UI 与组件包签名 |
| 1D / 2D / UI 原型 | 实现 MVP | 文本、SVG Node–Edge、数据驱动多标签页；暂无 Konva 编辑器 |
| 3D | **复用适配器** | 接入用户自行构建的原仓库 Three.js `dist/`；不伪造 3D 页面 |
| 4D | 实现 MVP | 离散事件、步进时间轴、确定性规则传播；无动画流体场景 |
| 5D | 实现 MVP | 显式规则与模块屏蔽反事实对照；非一般性因果识别引擎 |
| 增量编译与缓存 | 未完成 | `diff` 提供影响分析，build 仍全量重建 |
| Graphology / Dagre / Dexie | 未完成 | 独立适配器尚待实现，不宣称已集成 |
| 浏览器自动化、视觉回归 | 未完成 | `verify` 仅静态文件和模型检查 |
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

> 上述命令借用原仓库演示数据；**不能将演示 3D 架构错当成当前项目真实架构**。正式使用时，须将目标工程转成原仓库支持的 Architecture Model，严格核对适配和事实证据。3D 的跨维双向选择同步仍未完成。

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

测试包含 23 项 Python UT/集成检查；另使用无网络 Mock JSON 的浏览器 DOM/交互冒烟测试验证过 1D/2D/4D/5D/原型切换，不含真实 HTTP/WebGL 测试。测试覆盖现有核心代码的关键路径，但不等同于 90% 覆盖率证明或 UT/IT/ST 全部完成。请根据 IPA 资料包中的 Traceability 和测试计划继续完善。

## 许可证

本次新增源代码以 MIT 授权（见 `LICENSE`）。如将 GemArc 原有 Three.js 产物或其他依赖一起再发布，须重新检查对应的 LICENSE、第三方 NOTICE 和版本约束；本包不代表外部依赖全部完成许可审查。


## 随附预览

`examples/demo-site/` 为预先生成的离线静态站点（示例事实）；在目录内运行 `py -3.11 -m http.server 8000 --bind 127.0.0.1` 后访问 http://127.0.0.1:8000 。
