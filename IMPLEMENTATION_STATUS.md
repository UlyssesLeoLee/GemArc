# GemArc MCP 实施状态 / 需求追溯摘要

2026-10-25；原始设计资料：GemArc MCP IPA Design Dossier v1.0。

| SRS ID | 本包源码位置 | 实现状态 | 证据 / 测试 | 后续工作 |
|---|---|---|---|---|
| FR-001 | `service.inspect_path` | MVP | `test_scan_no_symlinks` | Git URL 直接分析、AST 深度提取 |
| FR-002 | `model.validate` | MVP | `test_model` | 自动来源行号定位、仓库证据校验 |
| FR-003 | `model.normalized_model` | MVP | `test_model` | Schema 迁移、严格架构 IR |
| FR-004 | `web/*` | MVP | `test_e2e_build_verify_export` | 注册中心模板版本迁移 |
| FR-005 | `registry.json` | MVP | `assets()` | 完整组件加载和动态注册 |
| FR-006 | `service.build` | MVP | `test_e2e_build_verify_export` | 抽象组件组装器，真正增量构建 |
| FR-007 | `scaffold/runtime/space/space-viewer-three.js`, `templates/shared/app.js` | 实现 | 浏览器实测：3D 渲染 + 节点/关系各自可拾取 + 跨维选择同步（设计文档 §7） | 像素级视觉回归 |
| FR-008 | `model.py`, `web/app.js` | MVP | 模型/服务 UT | Graphology + Dagre 适配 |
| FR-009 | `web/app.js` | MVP | 未运行浏览器测试 | Konva 画布与模型绑定 |
| FR-010 | `simulation.py` | MVP | `test_normal`, `test_cyclic_rule_bound` | 状态机、复杂并发和反馈语义 |
| FR-011 | `simulation.compare` | MVP | `test_counterfactual` | 结构因果模型/严格因果证据模型 |
| FR-012 | `service.verify` | 部分 | `test_e2e_build_verify_export` | 浏览器视觉/交互/性能验证 |
| FR-013 | `service.diff` | 部分 | `test_diff` | 增量构建缓存 |
| FR-014 | 静态中文站点 | 部分 | 无 i18n 测试 | ja-JP/en-US 全量词典与溢出测试 |
| FR-015 | `service.export_zip` | 部分 | `test_e2e_build_verify_export` | 分模块 Markdown/Mermaid/LLM 输出 |
| FR-016 | `registry.json` | 未实现 | 无 | 插件安装、动态组件注册与隔离 |
| FR-017 | 无 | 未实现 | 无 | 多 Agent 冲突治理 |
| FR-018 | `service.diff` | 部分 | `test_diff` | 动画及场景差异分析 |
| FR-019 | 无 | 未实现 | 无 | MCP Apps 嵌入式预览 |

**状态说明**：已运行核心 Python UT/服务集成测试和浏览器 Mock 数据 DOM/交互冒烟测试才算对应能力得到初步验证。**SDK 端到端 MCP 协议已完成核验**——用官方 MCP 客户端对 stdio 与 Streamable HTTP 两种传输各跑一遍 `initialize` → `list_tools` → `call_tool`，16 个 tool（full）/ 8 个 tool（stateless 远程）、2 个 resource、1 个 prompt 全部调通（`tests/test_mcp_contract.py` 只用 FakeServer 替换了 SDK，不构成握手证据）。Node 运行时测试（125 项）与 **Three.js 真实 WebGL 浏览器实测**已完成；**像素级视觉回归**与真实项目事实提取仍未在本次环境中完成。符合标准导向的文档化 ≠ 完成相应正式验证。

## 5D 脚手架（固定格式 + 共用代码）

> 目标：把「可被写死的东西」从大模型手里拿走，模型只填事实。设计见 `docs/02_基本设计/09_脚手架与共用运行时设计.md`。

| 能力 | 状态 | 证据 | 后续工作 |
|---|---|---|---|
| 契约层（8 份 JSON Schema） | 实现 | `test_scaffold` | 增加更多 UML 变体 |
| 词表层（7 份，三方唯一事实源） | 实现 | `test_scaffold_vocab` 断言生成物与源文件一致 | 无 |
| 运行层（core/graph/focus/space/sim/view/workflow/interop） | 实现 | Node `--test` 125 项 | 无 |
| 3D 空间适配器（Three.js） | 实现 | 27 项 Node 桩测试 + 浏览器真实 WebGL 实测（设计文档 §6.4 / §7） | 像素级视觉回归 |
| 0.2.0 归一化（sourceId/targetId/type、6 级证据） | 实现，旧模型不破 | `test_model_compat` | 无 |
| 4D/5D 双端同构 | 实现 | `test_sim_consistency` 逐字比对 8 个黄金用例 | 结构因果模型 |
| 脚手架生成器 + 槽位清单 | 实现 | `test_scaffold`、`test_scaffold_flow` | 槽位收敛策略 |
| `gemarc_scaffold` / `gemarc_contracts` / `gemarc_brief` 工具 | 实现 | MCP 工具注册数 16（full）/ 8（stateless）；brief 实测约 10 KB | 无 |
| 表现层纯函数化（view-model） | 实现 | `test_view_model_structure` 在构建产物上比对基线 | 像素级基线仍缺 |
| `static` / `vite` 双 profile 共用一份源码 | 实现 | `test_scaffold_flow`（含 Node 实际 import 站点 runtime） | 离线 vendor 流程 |
| 随附预览站与生成代码同步 | 实现 | `test_generated_artifacts` 逐字节比对真实构建产物 | 无 |
| MCP 功能微调控制台（主页） | 实现 | `test_http_transport`：主页占位符全部替换、写工具名单与 `server.py` 同源；浏览器实测调用/资源读取/写工具上锁 | 像素级视觉回归 |
| 浏览器端视觉回归 | **未实现** | 只有结构快照，非像素比对 | 需要确定基线环境与容差 |

## 远程端点（Vercel）

| 能力 | 状态 | 证据 | 后续工作 |
|---|---|---|---|
| MCP Streamable HTTP 端点 | 实现 | 官方客户端公网真实握手，8 tool 可见；可选 Bearer 令牌（`GEMARC_MCP_TOKEN`）| OAuth（claude.ai connector） |
| 5D 静态站同域托管 | 实现 | 脚手架站挂在 `/site/`；根路径 `/` 是 MCP 控制台，其余未知路径（`/app.js`、`/data/*.json` 等）返回 404 JSON（`test_http_transport`） | 如需 CDN 边缘缓存则拆独立静态项目 |
| 静态站内容与包内源码同步 | 实现 | 首次访问 `/site/` 时物化 `static` 档站点（懒构建）；`/healthz` 报 `staticSiteSource`；回归测试 `test_http_transport` | 无 |
| 写工具门禁 | 实现，默认关闭 | 被拒调用返回 `GEMARC_AUTH_002` 且无副作用 | 多租户隔离 |
| 路径归一化 | 实现 | `/mcp` 与 `/api/index` 均返回 8 tool（stateless） | 无 |
| 无状态工具集（远程默认） | 实现 | `gemarc_new_model → validate → simulate → render` 往返；`render` + kit 与 `build` 产物逐字节一致（`tests/test_stateless.py`） | 引入外部持久化后可支持编辑工作流 |
| Vercel 侧真实构建与公网握手 | **已验证（2026-10-30）** | 暂存生产部署 → `vercel curl` 冒烟 → `promote`；`scripts/verify_remote.py https://gem-arc.vercel.app` 25/25 通过（官方客户端握手、405、kit 校验和、`/healthz` 无路径泄露）；运行时执行了 ASGI lifespan | 带令牌的部署验证；浏览器端控制台点击实测 |

详见 `docs/09_部署/01_Vercel部署.md`。
