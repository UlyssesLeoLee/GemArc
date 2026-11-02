---
name: ipa-architecture-3d
description: 将需求/基本设计/源码/架构JSON生成真实 Three.js WebGL 三维交互架构网站，以 X/Y/Z 可追溯语义属性动态重组 12 种 View，检测空间信息增益与跨信任域关系，按 IPA 参考要求自审。默认简体中文，可通过 --lang ja-JP/en-US 选择其他语言。
compatibility: Python 3.10+; Node.js 20+; npm access for Three.js/Vite; WebGL2 browser for actual 3D rendering.
metadata:
  version: "2.1.0"
  default_language: "zh-CN"
  renderer: "three.js"
---

# IPA Architecture 3D — 真实语义三维架构生成 Skill

## 必须实现的结果

输出**真实 Three.js/WebGL 网站**，不允许 CSS3D / 图片旋转 / 视频冒充。View 切换时以稳定节点 ID 重新计算 X/Y/Z 坐标、轴刻度、层面、关系和审查指标，并用三维插值动画移动对象。任何空间位置必须能追溯回架构属性与证据。

**语言优先级**：用户显式 `--lang` 参数 > 默认 `zh-CN`；支持 `ja-JP`、`en-US`，扩展语言需齐备资源并经过验证。所有文档、HTML、设计审查文案遵循所选语言。不可默认为日语。

## 工作流程

1. 获取需求/设计/配置/仓库，建立证据索引：区分 confirmed / assumed / illustrative；明确系统边界及待确认信息。**不得臆造**组件、运行位置、协议、时延、SLA 或安全措施。
2. 将节点、关系、非功能关注点和 View 定义整理为**唯一事实模型** `architecture.json`，可参考 `assets/examples/illustrative-architecture.json`。使用 `references/model-contract.md`。
3. 按 `references/viewpoint-catalog.md` 为每个 View 选择利益相关者、关注点、审查问题和 `nodeIds`/`edgeKinds`。优先少量高信息量视图，不为了凑满 12 个造内容。
4. 为 X/Y/Z 配置有证据的离散属性，计算 `X` / `(X,Y)` / `(X,Y,Z)` 分组数量信息增量。**Y/Z 必须对已显示的分类再提供额外区分**才计作有效维度。否则自动折叠、提示并优化三轴选择。详细策略见 `references/spatial-design.md`。
5. 做内容分层：L0 结构空间扫描、L1 关系交叉审查、L2 证据详情。推荐每 View 6–18 节点，优先小于 24 条同时显著关系；更大模型应拆出专用子 View、用轴类别聚焦，而不是无止境缩字。
6. 生成正式网站：Three.js WebGL Mesh（实体）、Sprite（标签）、Line/Cone（关系箭头）、OrbitControls 和 Raycaster。允许轴字段更换、按分类聚焦、检索、选择、跨视图保持对象 ID；WebGL 失败时明确报错而非悄悄换 CSS3D。
6a. **固定机位必须是审查视角，而不是"好看的角度"**：每个正交机位由"哪根模型轴成为视线轴"定义（俯视隐藏 Y 读作 X×Z 矩阵、正视隐藏 Z 读作 X×Y 层叠、侧视隐藏 X 读作 Z×Y 剖面），并在界面上写明它隐藏了哪根轴。**当某根屏幕轴没有区分能力时必须禁用该机位并说明原因**——否则读者会看到零高度却挂着真实类目标签的假图层；被隐藏的轴若是唯一有信息的轴同样禁用。斜视机位仅在有效维度 ≥2 时提供。实现见 `src/rigs.js`，闸门见 `rigAssessment()`。
6b. **坐标 gizmo**：右下角常驻右手坐标系三轴球（实心=正端、线框=负端），独立 scene/camera 渲染故不受主场景裁剪影响；点击轴端对齐到对应机位。它是导航控件，不是静态图例。
6c. **同时提供真实 2D 版，并让读者自行选择**：在 3D 场景旁提供 `3D 立体 / 2D 平面` 切换。2D 必须是原生 SVG（无 WebGL、无 CSS3D、无透视伪装），遵循 `references/spatial-design.md` 的二维映射约定：X=纵向分层带、Y=横向泳道、Z=虚线包含容器；每个带/泳道都要标注轴名、类别与节点数；空格子显式标注「无组件」而不是留白；关系用正交折线并区分跨信任域；附图例。平面没有纵深，Z 只能画成包含关系并在界面上说明降级，不得把 Z 伪装成第三根空间轴。两版必须共用同一份轴字段、节点 ID 与证据，切换不得改变任何架构结论。WebGL 不可用时 2D 仍然完整可用，但必须显式声明「3D 未能启动」，不能把降级说成正常渲染。
6d. **影响面分析（可选但推荐）**：选中节点后给出「3 跳内受影响的实体数」与「到最近信任边界的信任链及其控制是否已确认」，并可一键聚焦把范围外的实体变暗。遍历必须走**与渲染器同一套过滤**（当前 View 的 `nodeIds` 与 `edgeKinds`）——否则"改这个会影响什么"会引用读者根本看不到的链路。实现见 `src/graph.js`。这是**阅读辅助，不是编辑**：不得修改模型、View 定义或证据。

7. 运行验证：`python scripts/validate.py architecture.json --lang zh-CN`。正式可信架构需 `--strict` 且每个节点/关系的 verification=confirmed、evidence 已填写。按照语言执行 `python scripts/build.py architecture.json --out output --lang zh-CN`。
8. 在可访问 npm 的环境执行 `npm install && npm run test && npm run build`，用浏览器实际检查 12 View、3D 透视、正交投影、2D 平面、类别聚焦、标签密度及多语言；2D 与 3D 都要逐个 View 看过，确认切换后轴字段、节点数、关系数完全一致。除非真正测试过 WebGL，不得声称已完成浏览器测试。
9. 审查 `references/ipa-quality.md` 及 `references/localization.md`，交付源码、可重建的前端项目、校验输出和必要的最终设计说明。文档仅呈现**最终状态**，不写修改历史。

## CLI 指令

```bash
# 中文默认
python scripts/validate.py assets/examples/illustrative-architecture.json
python scripts/build.py assets/examples/illustrative-architecture.json --out out/zh

# 显式输出日本市场版本
python scripts/validate.py assets/examples/illustrative-architecture.json --lang ja-JP
python scripts/build.py assets/examples/illustrative-architecture.json --out out/ja --lang ja-JP

# 英语版本
python scripts/build.py assets/examples/illustrative-architecture.json --out out/en --lang en-US

# 当前架构文档有完整原始事实依据时
python scripts/build.py architecture.json --out out/release --strict --lang zh-CN
```

生成的 `out/*/` 由 Vite/Three.js 驱动：`npm install` → `npm run dev`；`npm run build` 生成静态部署目录 `dist/`。不提供“无需依赖的假 3D 页面”作为交付替代品。

## 必须执行的质量闸门

- P0：3D 空间没有可解释的三轴事实映射；无效轴未提示；语言切换导致混用；把假设伪称真实；缺失证据却声明 IPA 认证；只产生静态图片。
- P0：**没有可选择的 2D 版**；2D 用 CSS3D/透视伪装成立体；把 Z 伪装成第三根空间轴；2D 与 3D 使用不同轴字段或丢弃节点。
- P0：**固定机位不回答任何架构问题**（只是四个"好看的角度"）；机位不声明自己隐藏了哪根轴；某根屏幕轴没有区分能力却仍提供该机位（读者会把零高度图层当成真实维度）；把唯一有信息的轴隐藏掉的机位仍然可用。
- P0：UI 用颜色承担装饰而非架构语义；调色板无节制扩张（颜色必须集中在设计令牌中，且轴色在三套渲染器里含义一致）。
- P1：视图切换不是实际三维重布局；层、部署、信任域关系互相矛盾；跨信任域关系无提示；标签遮挡使信息无法阅读；缺少正交视角验证。
- P1：**2D 只是把类别字符串罗列出来而不表达维度含义**；分层带/泳道缺轴名或缺节点计数；空格子留白而无标注；关系线交叉成乱麻；图例缺失导致颜色语义不明。
- P2：非关键信息密集、配色与标签重复、日语语体不统一。所有问题应审查，不应为增强 3D 视觉效果而忽视可读性。

采用日本 IPA「共通フレーム2013」「非機能要求グレード2018」公开资料和 ISO/IEC/IEEE 42010:2022 架构描述思想作设计质量参考；**不是 IPA 官方要求有 12 View，更不是官方认证**。详见 `references/ipa-quality.md`。
