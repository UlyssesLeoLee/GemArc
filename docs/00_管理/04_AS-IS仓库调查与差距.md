# 当前仓库调查及差距分析

> 文档编号：CTRL-004 ｜版本：1.0 ｜日期：2026-10-25 ｜状态：现状记录（仅静态确认）  
> 产品：GemArc MCP — 5D Design Scaffolding & Architecture Compiler  
> 说明：本文件为目标设计，不代表目标功能已实现或已测试。

## 调查依据

- 仓库：`https://github.com/UlyssesLeoLee/GemArc`；分支 `main`；树 `777af2a7e3aeadc5e05742074097c851c8b72301`。
- 已读取：`README.md`、`skills/ipa-architecture-3d/SKILL.md`、`references/model-contract.md`、`references/viewpoint-catalog.md`、`scripts/build.py`、Viewer `package.json`；已枚举仓库树。
- 调查方法：**文件静态查看**，未克隆、编译、执行或浏览器验证；因此不得推断运行质量。

## 可从仓库文件确认的资产

| 路径 | 证据支持的能力 | 说明 |
|---|---|---|
| `skills/ipa-architecture-3d/assets/viewer/` | Vite + Three.js Viewer | `package.json` 中有 three 0.180.0、vite 7.1.10 |
| `assets/viewer/src/flat-view.js`、`flat.js` | SVG 2D 相关文件 | 未执行视图测试，行为以源码审核为准 |
| `assets/viewer/src/layout.js`、`main.js` | 布局与前端主程序 | 已存在，但不等于 Graphology/Dagre 已集成 |
| `scripts/validate.py` | 架构 JSON 校验 | 需补充新 Schema 兼容迁移 |
| `scripts/build.py` | 由 JSON 复制 Viewer 并生成项目 | 当前输出目录须为空，功能边界与目标 MCP 不同 |
| `references/model-contract.md` | 节点、边、视点契约 | 当前模型需扩展 Dynamics/Composition/Causality |
| `tests/test_builder.py` + Viewer `tests/` | 部分测试设计/实现 | 未运行，覆盖率未知 |
| `.claude-plugin`、`plugin.json`、`SKILL.md` | Agent 分发机制 | 不等于 MCP Server 已存在 |

## 差距（TO-BE）

| 编号 | 缺口 | 对应设计 | 处理方式 |
|---|---|---|---|
| GAP-01 | 独立 MCP Server 与工具/资源/提示词 | BD-001, DD-002 | 新建适配层 |
| GAP-02 | 标准组件 Registry/锁版本/许可证追踪 | BD-002, DD-003 | 新建资产层 |
| GAP-03 | 一致的 5D 内容 Schema | BD-003, DD-001 | 扩展并迁移现有 JSON |
| GAP-04 | Graphology/Dagre/Dexie/Konva | BD-005, DD-006 | 可选技术组件按阶段集成 |
| GAP-05 | 完整 4D 状态事件与 5D 组合因果 | BD-006, DD-005 | 新建独立计算引擎 |
| GAP-06 | 增量构建与模型 Diff | BD-007, DD-007 | 新增构建索引 |
| GAP-07 | 自动化验收与离线交付包 | BD-008, DD-008 | 统一 QA 和 Export |

**禁止**把“规划使用 Graphology / Dagre / Konva / Dexie”写成现有 Viewer 的已安装依赖。
