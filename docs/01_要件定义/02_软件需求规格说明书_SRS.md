# 软件需求规格说明书（SRS）

> 文档编号：SRS-001 ｜版本：1.0 ｜日期：2026-10-25 ｜状态：设计基线（待评审）  
> 产品：GemArc MCP — 5D Design Scaffolding & Architecture Compiler  
> 说明：本文件为目标设计，不代表目标功能已实现或已测试。

## 1. 目的与系统边界

GemArc MCP 将目标仓库/设计文档转换成带证据的统一架构模型，再使用版本化的 1D–5D Viewer 与组件库编译标准交付站点。MCP 为入口，Core 与 Runtime 不依赖 MCP 才能运行。

## 2. 功能需求一览（機能要件）

| ID | 功能 | 可验收描述 | 优先级 | 工具/领域 | 测试映射 |
|---|---|---|---|---|---|
| FR-001 | 工程输入识别 | 支持 Git URL、本地目录、设计文档和架构 JSON；记录输入清单及状态 | P0 | inspect | UT-001,IT-001,ST-001 |
| FR-002 | 事实提取与证据 | 提取节点、边、接口、运行约束与来源；区分确认/假设/演示/未知 | P0 | inspect,model | UT-002,IT-002,ST-002 |
| FR-003 | 统一事实模型 | 全部维度共享稳定实体 ID、Schema 版本与唯一事实模型 | P0 | model | UT-003,IT-003,ST-003 |
| FR-004 | 标准模板管理 | 按版本锁定页面模板、布局、样式和结构，支持兼容校验 | P0 | assets,init | UT-004,IT-004,ST-004 |
| FR-005 | 组件注册中心 | 按能力/维度/版本/许可证检索组件及其输入输出契约 | P0 | assets | UT-005,IT-005,ST-005 |
| FR-006 | 标准页面组装 | 以项目模型与组件参数组装 5D 站点，不重写标准 Runtime | P0 | build | UT-006,IT-006,ST-006 |
| FR-007 | 维度隔离与同步 | 1D–5D 共用对象身份，独立保存各 View 相机/过滤/时间状态 | P0 | build | UT-007,IT-007,ST-007 |
| FR-008 | 图论展示 | 2D 支持节点、关系、聚类、布局、标签避让、聚焦与点击详情 | P0 | build | UT-008,IT-008,ST-008 |
| FR-009 | 多标签页原型 | Konva 交互原型可切换标签、编辑热点，绑定模型与需求证据 | P0 | build | UT-009,IT-009,ST-009 |
| FR-010 | 3D 模型场景 | Three.js 节点边、楼层/房间、正交/透视、左手逻辑坐标映射、退避 | P0 | build | UT-010,IT-010,ST-010 |
| FR-011 | 4D 状态事件 | 通过事件日志、状态机和时间轴回放正常与异常 Case | P0 | simulate | UT-011,IT-011,ST-011 |
| FR-012 | 5D 因果干预 | 按明确规则组合模块、注入条件、屏蔽模块并对比结果和证据级别 | P0 | simulate | UT-012,IT-012,ST-012 |
| FR-013 | 质量闸门 | 校验 Schema、事实溯源、引用完整性、UI、模拟规则及交付清单 | P0 | validate,verify | UT-013,IT-013,ST-013 |
| FR-014 | 国际化 | 默认简体中文，支持 ja-JP/en-US，稳定 ID 禁止翻译 | P0 | build | UT-014,IT-014,ST-014 |
| FR-015 | 交付包导出 | 生成 index.html、源模型、资源、文档、报告及模块级 ZIP | P0 | export | UT-015,IT-015,ST-015 |
| FR-016 | 增量构建 | 依赖变化时精确识别受影响 View、组件和 Case；保留不相关结果 | P1 | diff,build | UT-016,IT-016,ST-016 |
| FR-017 | 自定义扩展 | 第三方组件按能力清单、Schema、沙盒和签名/信任策略注册 | P1 | assets | UT-017,IT-017,ST-017 |
| FR-018 | 版本比较 | 比较模型事实、组件、视图、模拟结果，生成可追踪差异 | P1 | diff | UT-018,IT-018,ST-018 |
| FR-019 | 多 Agent 协作 | 支持工件锁与冲突检查，多 Agent 不覆盖彼此模型事实 | P1 | model,diff | UT-019,IT-019,ST-019 |
| FR-020 | 客户端内预览 | 兼容 MCP Apps 时提供预览，不兼容仍交付独立 HTML | P2 | preview | UT-020,IT-020,ST-020 |
| FR-021 | 授权与输出隔离 | 只读默认；文件写入限制在允许根目录；高风险操作显式批准 | P0 | all | UT-021,IT-021,ST-021 |
| FR-022 | 断点续处理 | 长任务写入构建清单与可重试检查点，失败不留下伪成功标识 | P1 | build,export | UT-022,IT-022,ST-022 |
| FR-023 | MCP 发现与兼容 | 提供按协议版本协商的能力发现、工具目录和向后兼容适配 | P0 | mcp | UT-023,IT-023,ST-023 |
| FR-024 | 构件来源与许可 | 构建清单登记代码/素材版本、SHA、许可证和分发方式 | P0 | assets,export | UT-024,IT-024,ST-024 |

## 3. 输入输出

- 输入：Git URL/ref、经过许可的本地目录、Markdown/YAML/JSON、配置文件。HTTP 文件从白名单、安全限制取回；禁止把网页文本当执行指令。
- 中间：Evidence Index、Architecture Model、View Spec、Component Lock、Build Graph、Simulation Case。
- 输出：`index.html` + pages/1d...5d + prototype + assets + model + reports + docs + source-data + standalone ZIP。
- 无足够证据时返回 `unknown` / `assumed` 状态以及缺口清单，不得编造完整因果。

## 4. 异常与授权

- 不可信来源、非法路径、未知组件版本、模型 ID 冲突、悬空 Edge、循环无界传播、缺失许可证 → 有 ID 的可读错误。
- 写文件需授权输出根目录；仓库默认只读，远端发布/删除不能由模型自行执行。

## 5. 优先级与验收

- P0 = 正式版本门槛；P1/P2 = 后续扩展，不允许误标已交付。
- 所有 FR 至少有一个 UT、IT、ST 追溯；UT 用例编号不是测试执行证据。
- 所有主要业务目标均由 UAT 复核，实际签核待实施阶段完成。
