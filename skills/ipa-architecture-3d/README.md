# IPA Architecture 3D Skill v2.0

基于 **Three.js + WebGL + Vite** 的真实三维架构视图生成 Skill，**简体中文默认**，可使用 `--lang ja-JP` 或 `--lang en-US` 输出本地化版本。日本 IPA 公开资料用于设计审查参考，不代表取得官方认证。

## 核心特性

- **真实的语义三维**：X/Y/Z 映射具体架构属性，动态切换 View 时重排实体位置、轴刻度、Y 层平面及关系箭头。
- **信息增益校验**：只有能进一步区分实体组合的轴才算有效，冗余轴自动折叠并标记；不会用高度/深度随机散布冒充复杂度。
- **审查机位（review rig）**：每个固定机位回答一个明确的架构问题，而不是"好看的角度"。俯视读作 X×Z 矩阵（每格是一次放置决策，冲突一眼可见）、正视读作 X×Y 层叠、侧视读作 Z×Y 剖面；每个机位都会声明它**隐藏了哪根轴**。当某根轴没有区分能力时，该机位会被禁用并说明原因，而不是画出一张零高度却挂着真实类目标签的"假图层"。斜视机位仅在场景确实有 ≥2 根有效轴时提供。
- **坐标 gizmo**：右下角常驻右手坐标系三轴球（实心为正端、线框为负端），随相机实时镜像。点击某个轴端即对齐到对应机位——它是导航控件，不是装饰图例。
- **分析能力**：12 个示例 View、按 X/Y/Z 分类聚焦、字段重映射、跨信任域高亮、节点选择与稳定 ID、侧栏证据。
- **影响面分析**：选中节点后显示「3 跳内受影响的实体数」与「到最近信任边界的信任链及其控制是否已确认」，可一键聚焦把范围外实体变暗。遍历走与渲染器同一套过滤（当前 View 的 `nodeIds` + `edgeKinds`），因此永远不会引用读者看不到的链路。纯函数实现，无图库依赖。
- **设计意图可见**：`meta.scope / constraints / decisions / qualityAttributes / sources` 在页面上直接呈现。这些是空间无法表达的既定事实，IPA 审查要求写明，因此不值得让它们烂在 JSON 里。
- **可信来源**：唯一架构 JSON、显式事实状态、跨域关系与来源校验、无事实不推断。
- **多语言**：`zh-CN` 默认、`ja-JP`、`en-US`。如需其他语言，需增加架构文本和 UI 字典，避免不完整翻译。

## 安装到 Windows Agent

```powershell
cd E:\skills\ipa-architecture-3d
.\scripts\install_windows.ps1 -Agents claude,codex,hermes -Force
```

在本地对当前项目使用 Skill 时，参考 `SKILL.md` 的开发流程生成数据、运行建站器即可。

## 生成 Three.js 演示网页

```powershell
python .\scripts\validate.py .\assets\examples\illustrative-architecture.json
python .\scripts\build.py .\assets\examples\illustrative-architecture.json --out E:\arch-3d-demo
cd E:\arch-3d-demo
npm install
npm run test
npm run dev
```

`--lang ja-JP` 或 `--lang en-US` 将 HTML、Viewer UI 以及带翻译的架构文字一并换成目标语言。输出目录需要为空。命令仍然依赖在可联网或 npm 缓存完整的环境中安装前端包。

**发布**：`npm run build` 得到 `dist/`。建议用 WebGL2 浏览器查看，不能将 `index.html` 作为裸文件双击运行（Vite 前端有 ES 模块导入）。

## 技术构成

```
ipa-architecture-3d/
├── SKILL.md
├── README.md
├── references/
│   ├── spatial-design.md
│   ├── viewpoint-catalog.md
│   ├── model-contract.md
│   ├── localization.md
│   ├── ipa-quality.md
│   └── japanese-ui.md
├── scripts/
│   ├── install_windows.ps1
│   ├── validate.py
│   └── build.py
├── tests/
│   └── test_builder.py
└── assets/
    ├── examples/illustrative-architecture.json
    └── viewer/
        ├── index.html
        ├── package.json
        ├── src/main.js
        ├── src/layout.js
        ├── src/i18n.js
        ├── src/style.css
        └── tests/layout.test.mjs
```

## 质量验证与状态说明

Python 校验器和 Node 空间单元测试无需下载依赖，可以检查 12 个 View 的三轴信息增益、模型引用以及语言。

```powershell
python -m unittest discover -s tests
node --test .\assets\viewer\tests\*.test.mjs
python scripts/sync_version.py --check
```

版本号曾手写在五处，容易漂移成"清单对自己的内容撒谎"。现在根目录 `plugin.json` 是唯一来源，
`sync_version.py --check` 会在不一致时报错（去掉 `--check` 则直接同步）。

前端**渲染验证不能只看单测**：布局测试全部通过时，三个正交机位仍可能渲染出完全空白的画面
（相机 `far` 平面为 `NaN` 时 Three.js 不报任何 GL 错误，只是静默画空帧）。因此必须统计画布实际
着色的像素数量，逐个机位确认真的画出了内容。**浏览器端 Three.js 渲染依赖 npm 的 three/vite 包**；若当前运行环境无法访问 npm，则不得宣称 WebGL 浏览器端完整验证通过。

### 陷阱：`NODE_ENV=production` 会静默丢掉构建工具

Vite 是**构建期**依赖。若环境里设了 `NODE_ENV=production`（部分 CI 与生产机默认如此），
npm 会自动 `omit=dev`：`npm install` 显示成功，但 `vite` 根本没装，随后 `npm run build` 报

```
[vite]: Rolldown failed to resolve import "three" from .../src/main.js
```

这条报错**指错了包**——`three` 装得好好的，缺的是解析 import 的 `vite`。

生成的站点自带 `.npmrc`（`include=dev`）和 `preinstall` 检查，因此上表命令在任何环境都成立。
若你手动删掉了 `.npmrc`，可改用 `npm install --include=dev`。

## 设计依据

- [IPA 共通フレーム2013](https://www.ipa.go.jp/publish/secbooks20130304.html)
- [IPA 非機能要求グレード2018](https://www.ipa.go.jp/archive/digital/iot-en-ci/jyouryuu/hikinou/ent03-b.html)
- [ISO/IEC/IEEE 42010:2022](https://www.iso.org/standard/74393.html)
- [Agent Skills](https://agentskills.io/specification)

内置示例明确为虚构配置，不是任何真实项目的权威设计。
