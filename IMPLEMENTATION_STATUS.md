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
| FR-007 | `web/app.js` | 部分 | 文件完整性校验 | 3D 运行时及跨维状态同步 |
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

**状态说明**：已运行 23 项核心 Python UT/服务集成测试和浏览器 Mock 数据 DOM/交互冒烟测试才算对应能力得到初步验证。**SDK 端到端 MCP 协议已完成核验**——用官方 MCP 客户端对 stdio 与 Streamable HTTP 两种传输各跑一遍 `initialize` → `list_tools` → `call_tool`，11 个 tool / 2 个 resource / 1 个 prompt 全部调通（`tests/test_mcp_contract.py` 只用 FakeServer 替换了 SDK，不构成握手证据）。Node/npm、Three.js/WebGL 浏览器、视觉回归和真实项目事实提取**仍未在本次环境中完成**。符合标准导向的文档化 ≠ 完成相应正式验证。

## 远程端点（Vercel）

| 能力 | 状态 | 证据 | 后续工作 |
|---|---|---|---|
| MCP Streamable HTTP 端点 | 实现 | 官方客户端真实握手，11 tool 可见 | 增加按调用鉴权 |
| 5D 静态站同域托管 | 实现 | `/`、`/app.js`、`/data/*.json` 返回 200 | 如需 CDN 边缘缓存则拆独立静态项目 |
| 写工具门禁 | 实现，默认关闭 | 被拒调用返回 `GEMARC_AUTH_002` 且无副作用 | 多租户隔离 |
| 路径归一化 | 实现 | `/mcp` 与 `/api/index` 均返回 11 tool | 无 |
| 无状态限制说明 | 已文档化 | 新容器 + 空工作区 → `GEMARC_IO_001` | 引入外部持久化后可支持编辑工作流 |
| Vercel 侧真实构建 | **未验证** | 本机 `vercel build` / `vercel dev` 因缺凭据无法运行 | 取得凭据后补跑构建与域名下握手 |

详见 `docs/09_部署/01_Vercel部署.md`。
