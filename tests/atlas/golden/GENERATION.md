# 引擎黄金的生成记录

`engine.json` 不是这个仓库里的代码产生的：维护者在仓库外用**参考引擎的副本**对 `engine_fixtures/` 里的合成夹具跑了一遍，再把结果转换成端口的形状写出。仓库里只有夹具输入、冻结的黄金、`deviations.json`、对拍测试和本文件；生成器与交叉核对脚本不进仓库（设计决定 R4），只在下面记录它们的摘要。

## 1. 输入与输出的 sha256

下列文件由 `test_engine_golden.py` 的 `GenerationRecordTests` 逐个核对。

| 文件（相对 tests/atlas） | sha256 | 角色 |
|---|---|---|
| engine_fixtures/align-vectors.json | 7db02bae0c8d857fef8a08a813b7195e34dae4fa6b9912d5635853e9930519c1 | 夹具输入 |
| engine_fixtures/core.json | 3058c3bf2d2d0fe65b1bc60fb9665699a856fca93a00a9ef1e2370121875ac68 | 夹具输入 |
| engine_fixtures/elements.json | 46360ba2e8e34dc1aebfa8e55d2a83f0eeaf63a6a94e03b1f944db33412dee09 | 夹具输入 |
| engine_fixtures/guard-vectors.json | efd4de87eb59fab7aa6fea58c375e0fd1c95d568e839a2cf2304feb57724a75e | 夹具输入 |
| golden/engine.json | dc20335077f98a301f1e4ef5da50257e5bdcdbf5cb894e8c91db5d8d783ef31c | 冻结的黄金 |
| golden/deviations.json | 87ecab96cad85526c425d487820ae0e570b5f7282a8b0658bd6a3bb73662c2a9 | 差异清单 |

仓库外的材料（只记摘要）：

| 材料 | sha256 |
|---|---|
| reference module 1 (simulation and delta compiler) | 9e44cc99d320ed56e6f62737b74cd3690e6a44c32610a5df553ce217cf5e9b9e |
| reference module 2 (causal view) | 44c7df9cb07a87b104898e7c838d19ef85d7992afa562bd973d1d51b5d3d862b |
| reference module 3 (graph index) | 098ffb0147c02acc88d913f13032ad55477845c00313bca799f92e3d10351496 |
| module 3 with the contract verification order | b137d35a29d879d37e91b7b23b34b0005b72e1839a66dbcc15a585a4faa667ce |
| module 2 with the alignment fix (diverged layer only) | d61f2569255d0f35c0d2032da04fbbce7262206adaaa6a57e200428ee076d99f |

archived generator (maintainer/engine-e1-generator.zip，含生成脚本、转换器、运行器、交叉核对与突变脚本及三份引擎副本)：sha256 14fdbca747bfdfd81e5675ae02805fadb1963ae8769f85764e0c10386a2373b1

## 2. 生成步骤

1. `make_fixtures.py` 用 `model.py` 的构造函数建出合成模型（每个夹具一个随机 id 家族），写入 `engine_fixtures/`；每个模型必须 `contract_issues == []`。
2. `gen_golden.py` 把契约模型转换成参考形状（见第 3 节），按运行分组交给运行器 `run_ref.mjs`，分别在三份引擎副本上执行：引擎原样（词表顺序已按契约调整）、对齐修复版（只用于 `diverged` 层）、原版（只用于核对词表顺序这一项差异）。
3. 参考输出经转换器变成端口的形状：自由文本的原因、`via`、`facts`、`mismatches` 用一张维护者保存的映射表还原成结构化形式，渲染出来的句子与运行哈希不进 `shared` 层。
4. 运行哈希用参考的哈希函数对**结构化后的轨迹**重新计算，放进 `diverged` 层；`shared` 层不含哈希。
5. **`diverged` 层的来源要说清楚**：对齐行与因果链来自仅修了对齐规则的参考副本再经转换器；哈希来自参考的哈希函数；只有一个对齐向量（`suffix-timeout-to-ok`）的行与对齐规则本身由人工手算核对（`DeviationTests.test_d1_*`），`confidenceWeakest` 的 `reviewed` 值由测试用引擎本身重算确认。所以 `diverged` 不等于“每个值都人工看过”，它是“由参考副本加受控修改产生、再抽样人工核对”的值。
6. 写出规范 JSON（键排序、紧凑、一个结尾 LF）。

## 3. 转换器的规则

* 集合改名：`nodes` 到 `entities`，`machines` 到 `stateMachines`，`space` 到 `spatial`；`ports`、`evidences`、`requirements` 原样。
* 静态边（`edge.static` 为真）的关系改成参考的静态关系名；非静态边的关系名不得落在参考的静态集合里（脚本断言）。
* 元素种类：词表 id 与参考名一一对应（脚本断言 14 个全对得上）。
* `keep` 重写：给参考补上推导出来的链，并在整个元素只有 `keep` 项时把元素端口类型改成 `Any`，使参考的链检查通过；结果里这些链边从 `root.edgeIds` 与 `root.element.edges` 中去掉。元素混用 `keep` 与其他处置的情况无法中和，只在交叉自检里按契约差异归类。
* 版本戳：参考模型的版本字段设成 `model_sha8(model)`，不设修订号。
* 标记句常量与元素显示名按渲染文本的差异映射（`deviations.json` 的 C6）。

## 4. 运行记录（聚合数字）

* 夹具运行共 749 次（核心与元素夹具的仿真、因果、编译，以及守卫、when、哈希与对齐向量）。
* `shared` 层：全部 749 个运行都有条目；其中 6 个因果链与 3 个对齐向量因 D1 改为只出现在 `diverged` 层（键见 `deviations.json`）。
* 词表顺序差异（C4）：3 条因果链的 `confidence.weakest` 与原版不同；除此之外，原版与调整后的副本在所有因果链上结果相同（其余差异 0 条）。
* `seek` 与完整折叠不一致的运行：2 个（P1，元素在已快照的记录之后改变自己的状态）。

## 5. 交叉自检（仓库外，一次性）

* 随机小模型 200 个（跳过 0 个），共 3926 个运行，引擎端口与参考副本逐值比较。
* 结果：{"equal": 3882, "fix1": 36, "contract-keep": 8}。
* `fix1` 表示与未修复的参考不同、但与修复版的参考相同（D1）；`contract-keep` 表示由契约的 `keep` 规则造成（C1）；`mismatch` 与 `crash` 为没有解释的差异。
* 对每个 `contract-keep` 运行单独核对（`keepcheck.py`）：参考编译失败，且每条错误都以 `rewrite <边>` 开头、该边的处置是 `keep`，并且端口的编译错误恰等于参考错误去掉这些条目。共 8 例，合理 8 例，说不清来由 0 例。

## 6. 突变检查（仓库外，一次性）

* 在端口副本上做了 36 处人为改动（超时阈值、退避、分组起跑、去重模式、聚合降级、快照间隔、对齐、守卫解释器等），本目录的黄金与行为测试杀死了其中 36 处；存活：[]。
* 第一轮检查有 7 处存活（成功于第二次尝试的 `Retry` 类型、延迟数组末项复用、未启动的区段返回、元素已应答计数、`change_retry` 的退避、只有成功的回复算恢复），据此补了 `SemanticsDetailTests`；上面是补测之后的结果。

## 7. 重新生成

改了夹具或引擎语义后，在维护者目录运行 `make_fixtures.py` 与 `gen_golden.py`，核对 `deviations.json`，重新运行 `write_generation.py` 更新本文件的摘要，再运行两个引擎测试文件。黄金只在有意的语义变化时重写，并与引擎改动同一次提交。

## 8. 修订记录（修复轮，手工修改）

引擎修复轮改了两处语义，黄金由此**手工**修改（没有重新跑维护者的生成器，因为生成器里的参考引擎副本不含这两处改动）；其余 747 个运行的条目一字未动，摘要按新文件重写。

* C8（`deviations.json`）：完整元素体加空 `rewrite` 现在是 `DRAFT`（规范 4.9），参考给 `INVALID`。`elements::compile-no-rewrite` 与 `elements::compile-missing-rewrite-key` 两条的 `shared` 条目去掉 `status` 并写入 `withheld: ["status"]`，`diverged` 层记录 `{"status": "DRAFT"}`；`errors` 等其余字段仍与参考逐值相同。
* H1（`deviations.json`）：任一次运行在 `maxEvents` 处被截断的因果对，状态是 `LIMIT_EXCEEDED`，`diff`、`explanation`、链上带 `truncated: true`；参考给 `OK` 且不带标记。`core::causal-limit` 的 `shared` 条目不变（对拍前 `strip_for_shared` 把端口输出映射回参考的形状），`diverged` 层新增 `truncation` 记录。
* H2 是硬化项（重试循环以 `maxEvents` 为界等），不改变任何黄金。

