# WBS 与开发阶段交付

> 文档编号：PM-001 ｜版本：1.0 ｜日期：2026-10-25 ｜状态：设计基线（待评审）  
> 产品：GemArc MCP — 5D Design Scaffolding & Architecture Compiler  
> 说明：本文件为目标设计，不代表目标功能已实现或已测试。

## 开发工作包（推荐实施顺序）

| WBS | 包 | 产物 | 完成条件 |
|---|---|---|---|
| WBS-01 | 现状基线 | 代码调查报告、兼容资产目录 | 有路径与 commit 证据 |
| WBS-02 | Schema 定稿 | Architecture Schema + 迁移策略 | 合法/非法样例全部通过校验 |
| WBS-03 | Core 拆分 | Model/Source/Validator/Compiler APIs | CLI 无 MCP 可用 |
| WBS-04 | Asset Registry | Manifests/Version Lock/License | 组件索引与冲突验证通过 |
| WBS-05 | MCP Interface | tools/resources/prompts | 两种传输契约通过测试 |
| WBS-06 | Viewer 复用 | 2D/3D 基础组件与统一选中 | 现有功能回归不退化 |
| WBS-07 | Prototype | Konva 多标签页/热点/绑定 | 交互脚本可自动回归 |
| WBS-08 | Dynamics | 4D State/Event/Timeline | 可重复事件回放 |
| WBS-09 | Causality | 5D 规则组合/干预/解释 | 正常、异常与反事实测试 |
| WBS-10 | Compiler/Exporter | 完整静态站和模块包 | 可离线使用并有 SHA Manifest |
| WBS-11 | QA Pipeline | Schema/UT/IT/ST/Visual/Security | P0 质量闸门全部通过 |
| WBS-12 | 发布与迁移 | 文档、实例、回滚、许可审查 | 用户 UAT 签核 |

## 开发顺序

每个 WBS 先评审相关基本设计与详细设计，再提交代码与对应 UT，最后跑相关 IT/ST。需求变化需同步修订追溯表，不得只修代码。

## 估算

本包不虚构工期、人员配置或项目预算；任务负责人、日期和工时均待正式计划会议审定。WBS 明细在 XLSX 中按计划字段填报。
