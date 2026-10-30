# 查看器纯模块的单元测试

本目录是 Atlas 查看器纯模块（`src/gemarc_mcp/atlas/viewer/src/*.js`）的 Node 测试。纯模块没有 DOM、没有 WebGL、没有网络，所以这些测试只需要 Node，不需要浏览器。Node 只在 CI 与开发时使用，不是产品运行时的依赖。

## 1. 怎么运行

在仓库根目录 `D:/GemArc` 执行：

```
node --test "tests/atlas/js/*.test.mjs"
```

- **不要写成 `node --test tests/atlas/js/`**：在 Node 22.12 上，把目录当参数会被当成模块路径而报 `Cannot find module`（已实测）。引号里的通配由 Node 自己展开，Windows 的 PowerShell 与 Git Bash 都适用；不带引号时由 shell 展开，也可以。
- 只跑一个文件：`node --test tests/atlas/js/view-layout.test.mjs`。
- 只跑查看器的文件（不含文档通道的 `assemble.test.mjs`）：`node --test "tests/atlas/js/view-*.test.mjs"`。
- 基线（通配形式，含 `assemble.test.mjs`）：12 个测试文件，138 个测试，约 5 秒。

## 2. 文件

| 文件 | 覆盖 |
|---|---|
| `view-helpers.mjs` | 不是测试：带种子的 `gemarc-atlas/1` 合成模型生成器（每个种子一套随机 id 家族：两个字母加一个数字）、一次运行和一条因果链的构造器、真实词表 `profiles/software.json` 的加载 |
| `view-model-access.test.mjs` | 词表查找与警告、详细度、域、聚焦相关性、回退积分器、索引 |
| `view-layout.test.mjs` | 六个视图、建筑顺序即数组顺序、楼层高度、未分配区、详细度过滤、2D 布局与聚焦、宝石几何 |
| `view-coords.test.mjs` | 左手系、相机、拾取、投影、按场景边界取景 |
| `view-scene-buffers.test.mjs` | 缓冲区内容、聚焦与散开、覆盖层、词表驱动的形状与颜色、虚线与淡化、实例化数组 |
| `view-label-fit.test.mjs` | 标签缩放、省略、避让 |
| `view-search.test.mjs` | 索引、排序、每个维度可达 |
| `view-playback.test.mjs` | `seek` 等于 `fold`、舞台范围、4D 与 5D 的每一步不变量、因果覆盖层、空态 |
| `view-genericity.test.mjs` | 源码约定扫描（打包器依赖的写法、无网络与 DOM、无业务字面量，对七个视图模块与 `assemble.js` 都生效）与泛化探针 |
| `view-bundle-contract.test.mjs` | `docs/atlas/spec/bundle-contract.md` 与代码的一致性，以及按文档形状构造的整包端到端测试 |
| `view-hardening.test.mjs` | 验证轮的回归：与 `Object.prototype` 成员同名的 id、机器状态、数据视图成员规则、过大的楼层 `level`、播放位置归一化、被拒绝的干预对、参数默认值，以及“去掉 import/export 后拼接八个模块”的打包作用域 |
| `view-behaviour.test.mjs` | 变异检查找出的行为钉子：边的方向、需求带来的上下文、平行边、取景边距、搜索权重与线的降权、标签避让方向、布局与动画常量 |
| `assemble.test.mjs` | 不属于查看器通道：文档通道的 `assemble.js` 与 Python 孪生逐字节对照 |

## 3. 泛化探针

`view-genericity.test.mjs` 的探针是设计里的验收（M4(c)）：随机 id 家族、400 个节点、未知的建筑 / 种类 / 信任值 / 关系、孤儿节点、重复的楼层 `level`、没有用例、空模型和故意畸形的模型，各跑六个视图（含详细度过滤和未知视图 id）、2D 布局、缓冲区、相机取景、搜索和播放，要求：不抛错、所有坐标有限、没有 `NaN`、可见节点都有位置、取景后所有点落在画布内。

## 4. 规则

- 只用合成 id（`view-helpers.mjs` 生成的家族，或 `tests/atlas/fixtures/lending_desk.model.json`）；不出现参考实现的任何名称、id、文案或常量（`test_denylist.py` 会扫描本目录）。
- 测试必须确定：用 `view-helpers.mjs` 的 `rng(seed)`，不用 `Math.random`。
- 纯模块的源码约定（单行相对 `import`、只用 `export const|function|class`、纯 ASCII、模块依赖无环、不含 `fetch(` / `import(` 等 BD-001 会扫描的串）由 `view-genericity.test.mjs` 强制，打包通道依赖它们。
- 参考实现的同输入对照（同一合成输入喂参考与移植后的模块）是**一次性的维护者检查**，脚本与参考副本留在仓库之外，不在本目录；结果记在交接记录里。
