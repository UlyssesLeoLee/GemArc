# Atlas 测试说明

本目录是 GemArc Atlas 的测试、合成数据与出处检查。所有测试只依赖标准库（和仓库自带的 `gemarc_mcp`），不联网。

## 1. 怎么运行

以下命令都在仓库根目录执行，使用项目虚拟环境：

```
# 单个通道（按文件名模式）
PYTHONPATH=src ./.venv/Scripts/python.exe -m unittest discover -s tests/atlas -t tests -p "test_synth*.py"

# 全部 atlas 测试
PYTHONPATH=src ./.venv/Scripts/python.exe -m unittest discover -s tests/atlas -t tests

# 整个套件（v1 的测试加上 atlas 的测试）
PYTHONPATH=src ./.venv/Scripts/python.exe -m unittest discover -s tests
```

Node 部分（v1 的脚手架运行层，atlas 通道不改它）：

```
node --test src/gemarc_mcp/scaffold/runtime/tests/*.test.mjs
```

### 基线数字（提交 `dad6f73`，在 `git archive` 副本里复核）

| 套件 | 结果 |
|---|---|
| Python `unittest discover -s tests` | 192 个测试，1 个跳过 |
| Node `node --test src/gemarc_mcp/scaffold/runtime/tests/*.test.mjs` | 125 个通过 |

atlas 的新测试都是新增文件，不改任何已有测试文件：`git diff --name-only -- tests` 只能列出新增文件。完整套件的总数随各通道加入的测试增长，所以只对照上表的基线，不对照某次运行的总数。

## 2. 合成数据：`synth.py`

`synth.py` 生成展开后的 `gemarc-atlas/1` 模型。所有派生字段都调用 `src/gemarc_mcp/atlas/model.py` 的 `build_*` / `derive_*` 函数，生成器自己不重复推导任何字段，生成结果在返回前经过 `contract_issues`，不合规就抛 `AssertionError`。

| 入口 | 含义 |
|---|---|
| `generate("tiny")` | 设计 3.4 的借阅台示例（4 个节点、3 条边、3 条证据、1 个用例共 6 个事件）。固定 family 时与设计文本逐字一致 |
| `generate("small", seed)` | 约 40 个节点、88 条边、46 条证据、5 个用例 |
| `generate("reference", seed)` | 参考规模：169 节点、374 边、242 证据、23 用例共 322 事件、21 干预、16 需求、23 TBD、5 台状态机；估计的 `.atl` 编码大小约 166 KB（各段与设计 3.5 的数字在约 5% 内） |
| `edge_case(name, seed)` | 边界模型，名字见 `synth.EDGE_CASES`：`big400`、`unknown-values`、`no-cases`、`no-interventions`、`empty-strings`、`cjk`、`odd-ids`、`machine`、`insert-element`、`element-ports`、`rich-fields`、`record-crossings`（几条故意的消息型到记录型的边，每条一个 PT-003 WARN，恰好 `synth.RECORD_CROSSINGS` = 4 条，各有一个用例事件沿着它走）|
| `Family.fixed()` / `Family.random(seed)` | id 家族。固定家族是 `svc.` `usr.` `tbl.` `rm.` `zone.` `lvl.`（加上 `evt.` `st.` 等补充前缀）；随机家族每个种子换一套前缀：**两个字母加一个数字**（如 `kb7`；结构和节点前缀小写，证据、用例、需求等记录类前缀大写，如 `KB7`），永远不等于固定家族。它是种子的纯函数，不读任何文件。前缀空间只有 6,760 个词，`test_synth.py` 的 `RandomPrefixTests` 把每一个可能的前缀（含大写写法）逐个与拒绝名单比对，所以任何种子都不可能产生名单里的词（不是只试了几个种子）；另有一个测试扫描 0 到 4999 号种子。重建拒绝名单后这两个测试若变红，应缩小 `synth.PREFIX_LETTERS` / `PREFIX_DIGITS` 的空间，而不是在运行时读名单 |
| `estimate_atl(model)` | 估计 `.atl` 作者文本的字节数（按段）。用设计 3.4 的 2175 字节示例校准，逐段一致 |

要点：

- 同一个种子在任何平台和 Python 版本上得到同样的字节（自带 SplitMix64，不用 `random`，不依赖 `hash()` 或集合顺序）；测试用不同的 `PYTHONHASHSEED` 起子进程验证。
- `unknown-values` 故意只破坏词表成员关系（未知信任值、层、层级、节点种类、关系），`contract_issues` 只返回这 7 个 `GEMARC_ATLAS_MODEL_030`；其余模型必须返回 `[]`。
- `empty-strings` 里 `sub` 为空串：`.atl` 的位置列不允许空，所以这个模型不能做 `.atl` 往返，只给查看器和文档构建检查空串。
- 重新写出示例 fixture：`PYTHONPATH=src ./.venv/Scripts/python.exe tests/atlas/synth.py write-fixture`；查看某个模型的规模：`... synth.py stats reference 7`。
- 可选字段全由生成器覆盖：`reference` 预设按参考的比例带上 `when`、`guard`、`errorCode`、`Compensation` 事件、`props.dedup`（以及落到它上面的 `dedupKey` 事件，部分重复两次）、`props.owner` / `store` / `enums`、带基数的 `references` 边、`keep` / `disable` 重写项、含元素与守卫修改的 `combined` 干预、`<用例>#<序号>` 形式的 `targetEventId`；用例长度服从 4 到 48 的重尾分布（总数仍是 322），每台机器 3 到 10 个转移。`rich-fields` 边界模型以很高的比例同时带上所有这些字段（还有已盖章的 `lineRange`），对每个种子都成立；`test_synth.py` 的 `FeatureCoverageTests` 断言预设与边界模型的并集覆盖 `REQUIRED_FEATURES` 的每一项。
- `delay_message` 的 `targetRuleId` 只取自被应用用例里的事件，`targetEventId` 只指向被应用用例里存在的事件；每个有用例的生成模型至少有一个事件显式写出 `edge`。
- 默认全量检查里的干净度（debt-b4）：`tiny`、`small`、`reference` 三个预设各取任何种子，在默认全量检查（第一波加第二波）里没有 ERROR 也没有 WARN，只有 INFO（CS-008、OR-005、EV-008 一类）；`test_wave2_default.py` 对每个预设五个种子（切片上与真实项目上）以及 `small`、`reference` 的种子 6 到 25 断言这一点。为此生成器：只在引擎真的这样编译时才写干预的 `expect.status`（生成的元素总是 OK，所以写 `OK` 或不写；`element-ports` 边界模型故意把元素端口类型写成与边不符，引擎拒绝它，所以写 `REJECTED`）；不画记录型端口与消息型端口之间的边（PT-003；真实的 atlas 有这样的边，所以边界模型 `record-crossings` 故意保留 4 条，默认检查在它上面恰好 4 条 PT-003 WARN，0 个 ERROR，查看器、文档、5D 和浏览器门里的跨类型路径靠它覆盖），`Event` 节点只与 `Event` 节点相连，第一个 `Event` 节点是自己那条链的起点；用例数从 6 起时六个类别都出现（CS-005），多出来的同类用例从末尾起改成缺的类别，不多抽随机数；`combination` 用例和 `abnormal`、`recovery` 一样断言一个结果或一个不是 Completed 的终态（OR-002，`combination` 的随机数来自它自己的分支）；`props.dedup` 优先落在入口（Actor、API）的直接目标上，让带 `dedupKey` 的事件真的进得去。因此同一个种子的模型与批 3 之前不同（边与用例不同），依赖具体种子的断言（建筑总览的边数、5D 画布）已按新模型重新测量。
- 示例 fixture 是 `fixtures/lending_desk.model.json`（canonical 形式，无 BOM，只有一个结尾 LF）。其他通道的往返测试可以直接逐字节比较。它的 meta 等同一次 `init` 的结果：`projectId=lending-desk`、`title=Library lending desk`、`languages=["en-US"]`、`repos=["main"]`；设计示例里的 `RQ-1` 引用了 `TS-1`，所以 `tests` 注册表里带有 `TS-1`。

## 3. 规则：只用合成 id

- fixture、示例、测试里只能出现合成 id（固定家族或随机家族）。不得出现参考实现的 id、名称、文案、路径或常量。
- 允许移植通用的引擎和查看器代码（用户已授权，2026-10-30），但必须去除领域内容；模型数据一律不得带入。
- 参考实现的字段名保留在契约里是有意为之（见 `docs/atlas/spec/model-schema.md` 第 1 节），不算泄漏。
- 哈希拒绝名单（下一节）是自动防线；写新测试数据时仍要自觉使用随机家族。

## 4. 哈希拒绝名单

- `denylist.sha256`：参考实现里有区分度的词的 SHA-256，一行一个，加一行 `canary`。内容：领域名词、公司/产品/仓库名、**整串的 id**（`word.word-x`、`a:b:c` 一类的字符串字面量，整体作为一个词）、文件和目录名（含带扩展名的复合名）、日文和中文术语。仓库里没有任何参考字符串。
- **覆盖范围**（`test_denylist.py` 的 `SCAN_ROOTS` 加 git 增量）：`src/gemarc_mcp/atlas/`、整个 `tests/`（包括 `tests/atlas/` 之外的新测试）、`docs/atlas/`、`skills/gemarc-atlas/`、`tools/`、`api/`，**再加上** `git ls-files -mo --exclude-standard` 列出的所有未跟踪或已修改文件（仓库任何位置，包括根目录）。判定为文本的标准是：没有 NUL 字节且能按 UTF-8 解码，不看后缀（`.svg .sh .ps1 .xml .tsx .cjs .j2` 和无后缀文件都读）。`.git`、`.venv`、`node_modules`、`.vercel`、`__pycache__` 不扫。git 不可用时只跳过基于 git 的两个测试。
- **根目录守卫**：`test_no_untracked_file_sits_directly_in_the_repository_root` 要求仓库根目录没有未跟踪文件（`ROOT_UNTRACKED_ALLOWED` 目前为空）。曾有一个 43 KB 的参考派生文件 `props_unknown.json` 落在根目录、在所有扫描根之外、会被 `git add -A` 提交；现在两个测试都会让它变红。**提交前仍要自己跑 `git status`**，未跟踪集合应当恰好是 `docs/atlas`、`src/gemarc_mcp/atlas`、`tests/atlas`、`tools` 加上之后获授权的文件。
- **分词**（`tokenize`）：先做 NFKC 归一并去掉零宽格式字符，再取小写的 ASCII 字母数字串、camelCase 片段、字母与相邻数字拼合的片段（`Ab123` 也给出 `ab123`）、整串的 id 复合词（`word.word-x`、`a:b:c`）、汉字与片假名连续串的 2 到 4 元组。命中只报告 `path:line` 和摘要前缀，不报告词本身。
- **完整性固定**：`test_denylist.py` 里的 `PIN` 固定了摘要文件的 sha256 和条目数，`PROBE_DIGESTS` 是参考里两个词的摘要（只写十六进制）。整个换成随机摘要、截断或改动文件，`DigestFileIntegrityTests` 都会变红。重建后运行 `python tests/atlas/test_denylist.py --print-pin`，把打印出的 `PIN` 粘进测试文件，与重建同一次提交；`PIN` 只在维护者重建名单时更新。
- 金丝雀自检：测试在临时副本里种下金丝雀词并断言扫描失败。金丝雀词的字面量不得写进任何仓库文件（测试里用字符串拼接）。
- 摘要文件由仓库外的维护者脚本 `make_denylist.py` 生成，位置记在交接记录里。不要手改 `denylist.sha256`。重建步骤：

  1. 在仓库外运行 `python make_denylist.py --scan`（先用 `--dry-run` 看计数），它读取参考目录（只读），过滤通用词，写出摘要文件，并把当前树里的命中（词与位置）打印在维护者控制台上；`scan_live.py`（同目录）还会把 git 增量一起扫。
  2. 命中的如果是正当的通用词（例如 ja-JP/zh-CN 标签里的标准设计文档用词、`rev-parse` 这样的通用命令），逐个审阅后加进脚本旁的 `exempt.txt`（或 `generic_locale_terms.txt`）再运行；真正来自参考实现的内容必须从仓库里删掉，或者改写成通用说法。
  3. 控制台列出的 `UNREVIEWED` 契约词要逐个审阅，确认是字段名或通用词后才用 `--freeze-contract` 冻结（它一次冻结全部当前候选，所以必须先看完名单）。
  4. 更新 `PIN`，提交前再跑一遍 `test_denylist.py`，确认零命中。

- 过滤规则：词在 GemArc 自己的已跟踪文件（HEAD，任何 UTF-8 文本文件）里出现过、在 Python 标准库与已安装包、npm 文档、TypeScript 平台声明里出现过，或太短（ASCII 少于 4 个字符；来自路径名的少于 3 个）、是纯数字或十六进制，都不进名单；CJK 的 n 元组要求只含汉字与片假名、在参考里至少出现 8 次，并去掉维护者审阅过的通用词。**id 复合词只收整串字面量**（JSON 值、字符串字面量、文件名），出现在散文或代码里的同形字符串（`res.status`、`non-machine`）不收，否则会误伤其它文件里的通用代码。字段名（契约）与契约文档里反引号包住的标识符不算泄漏，但这个豁免不直接信任当前树：脚本从 `model.py` 的字符串常量、夹具与词表的 JSON 键、契约文档的反引号片段里推出候选，只有维护者人工审阅后冻结进 `contract_exempt.txt` 的那几十个词才被豁免。新出现的契约候选词会在控制台列为 `UNREVIEWED` 并留在名单里（对应的文件行让 `test_denylist.py` 变红）。
- **实测覆盖率**（维护者脚本的聚合计数，不含任何词）：参考里 1,812 个 id 形字符串，现在 1,781 个（98.3%）会被名单标出，改动前是 510 个（28%）；其余 31 个里 19 个整串已在 GemArc 自己的已跟踪文件里出现、6 个短于 4 个字符，所以不能收。散文行的命中率：`.md` 93%、`.json` 值 90%、`.html` 63%、`.mjs` 63%。
- **已知局限**：哈希只比对整个词。与其它小写字母直接粘在一起的参考词（没有分隔符也没有大小写变化）是一整个串，只有整串在名单里才会命中；改写、翻译、换说法都抓不住（设计里的相似片段检查在之后处理）；字面量以外的任意复合写法不收。现实里的威胁是意外复制，名单的作用是让这种复制立刻变红，不是防御有意规避。

## 5. 出处（provenance）

- 每个 `src/gemarc_mcp/atlas/` 下的文件在 `src/gemarc_mcp/atlas/provenance/<lane>.tsv` 里有一行：`path, origin, reference_unit, treatment, deviations`。`origin` 取 `ported-semantic`、`ported-structural`、`clean-room`、`new`；`reference_unit` 只写通用模块名（如 `engine core`），`new` 写 `-`。
- 空的或只有文档字符串的 `__init__.py`、`provenance/*.tsv`、`PROVENANCE.md`、`__pycache__` 不需要行。
- `python tools/gen_provenance.py` 由这些表和 `provenance/authorisation.tsv` 生成 `PROVENANCE.md`；`--check` 只检查不写。`test_provenance.py` 断言：每个文件有行、没有指向缺失文件的行、`PROVENANCE.md` 与生成结果逐字相同。
- 任何通道改了 tsv 之后都要重新生成 `PROVENANCE.md`，否则 `test_provenance.py` 会红。
- 授权记录：移植通用代码已于 2026-10-30 获用户授权；发布（push、部署、托管路由、kit 下载）尚未授权，`PROVENANCE.md` 里写着 `PUBLICATION: PENDING`，`test_atlas_publication_gate.py` 据此断言 `.vercelignore` 排除 `/src/gemarc_mcp/atlas`、`api/index.py` 没有 atlas 路由（唯一的例外是只在 `GEMARC_ATLAS_KIT` 恰好为 `1` 时才有的本地路由 `/kit/atlas.zip`：默认关，别的值都不开，atlas 缺席时 503 加错误码，上传集里它开不了）、默认工具集不变。把 `authorisation.tsv` 的 `publication` 行改成 GRANTED 是用户的编辑。

## 6. v1 冻结测试

`test_atlas_v1_frozen.py` 固定：16 个完整工具集名称与顺序、8 个无状态工具、5 个写工具、`scaffold/cases/simulation-golden.json` 的哈希、`scaffold/vocab/*.json` 的逐文件哈希和两个摘要。哈希按 LF 归一化后计算（去 BOM，CRLF 转 LF），所以 Windows 与 Linux 检出一致。

只有 v1 维护者有意改动 v1 时才更新：运行 `PYTHONPATH=src ./.venv/Scripts/python.exe tests/atlas/test_atlas_v1_frozen.py --print`，把打印出的常量粘进测试文件，与 v1 改动同一次提交。atlas 的工作不得导致这个测试变红。

## 7. 测试文件一览

Python 测试（`tests/atlas/test_*.py`；运行方式见第 1 节）。按通道分组，每个文件一行。

**契约、合成数据、防线**

| 文件 | 内容 |
|---|---|
| `test_model_contract.py` | `gemarc-atlas/1` 契约、id 语法、推导函数、词表 |
| `test_synth.py` | 生成器：确定性、规模、一致性、家族、边界模型、估计器校准 |
| `test_strict_evidence_regression.py` | 规范校验器 `strict=True` 读 `evidenceIds` 的回归测试 |
| `test_denylist.py` | 哈希拒绝名单：仓库里不得出现参考系统的字符串（第 4 节） |
| `test_provenance.py` | 出处表：每个 `src/gemarc_mcp/atlas/` 文件一行，没有悬空行，`PROVENANCE.md` 与生成结果一致（第 5 节） |
| `test_atlas_v1_frozen.py`、`test_atlas_publication_gate.py` | v1 冻结（第 6 节）与发布门（`.vercelignore`、`api/index.py`、默认工具集不变） |

**格式、切片、规则、诊断**

| 文件 | 内容 |
|---|---|
| `test_format.py`、`test_format_fuzz.py`、`test_format_hardening.py` | `.atl` 与 `.jsonl` 两个前端、展开器、编码器；变异行不崩溃也不悄悄改模型；敌意输入只给诊断 |
| `test_slices.py`、`test_slice_mode.py` | 切片库（put、红草稿、行级补丁、sha8、锁、两个进程同时写）与切片模式校验（待定引用、一字之差的拼写、级联） |
| `test_rules.py` | 第一波规则：零误报 |
| `test_rules_w2.py` | 第二波规则：每条一个变异夹具只触发自己的 id；合成模型除预期发现外没有第二波发现；OR-002 不会被 `OK` 结果或未触及节点上的 `Idle` 躲过 |
| `test_wave2_default.py` | 第二波在默认全量检查里（`status.FULL_WAVE_SET == (1, 2)`）：三个预设各五个种子在切片上与在真实项目上都没有 ERROR 与 WARN，生成器的声明与引擎一致，参考规模的检查在设计 5.5 的时间预算内；`record-crossings` 边界模型恰好 4 条 PT-003 且能构建、过浏览器门；done 行在还有 WARN 时写出数量；判定失败的项目被 `build` 拒绝（BUILD_001，带规则提示） |
| `test_diag_budget.py` | 诊断的渲染与字节预算 |

**引擎、预言机、模拟**

| 文件 | 内容 |
|---|---|
| `test_engine_behaviour.py`、`test_engine_elements.py`、`test_engine_golden.py`、`test_engine_mutants.py`、`test_engine_perf.py`、`test_engine_sweep.py` | 引擎的行为、元素模板、与冻结参考值的对照、确定性变异体、参考规模的时间预算、保护条件扫描 |
| `test_oracle.py` | 预言机账本（先封存后揭示、`revised` 规则、确认通道、发布门） |
| `test_oracle_cheaters.py` | 脚本化的作弊者：作弊的运行到不了发布门 |
| `test_oracle_support.py` | 预言机测试共用的夹具（借阅台、引擎推出的期望 `consistent`、确认辅助），自带一个测试 |
| `test_simulate.py` | `simulate` 与参考规模上的反空洞（变异体）运行 |

**文档通道（`derive/`）**

| 文件 | 内容 |
|---|---|
| `test_derive_docs.py`、`test_derive_fragments.py`、`test_derive_doc_outline.py` | REQ、BD、DD 文档链与不变量；片段、闭包与 `assemble`；`doc` 工具的核心与确定性的文档构建 |
| `test_derive_diagrams.py` | 骨架表、文本卫生、Mermaid 构建器与图单元；建筑总览每条跨建筑边只计一次（debt-b4 的 21 号项），到未放置组的边不进总览、只有这类边时不生成总览 |
| `test_derive_lint.py`、`test_derive_prompts.py`、`test_derive_deletion_matrix.py`、`test_derive_hardening.py` | 文档规则 DC-L 与 DG；选择集闭包与六种提示；逐个删除必填字段；验证轮后的加固 |
| `test_outline.py`、`test_evidence.py`、`test_roots.py` | 源码大纲；证据验证器（SKIP 语义、脱敏、漂移、印记、EV-012）；根目录只从环境读取与路径沙箱 |
| `test_size_before_read.py` | 超过大小上限的源文件与用户文本只看大小、不读内容（`os.stat` 在前；38 号项）；已保存的用户文本（`atlas/user/*.txt`）在 `status`、自审报告、`put` 里也只读上限以内（`open().read(上限+1)`），超过的在自审报告里有警告 |
| `test_deep_json.py` | 嵌套过深的 JSON（超过解码器递归上限，或能解析但过深）出现在账本、锚点、计数文件、自审记录、证据印记、`build-info.json`、切片单元格、`.jsonl` 补丁行里：一律是「不可读」，从不抛 `RecursionError` |

**工具、状态、命令行、MCP**

| 文件 | 内容 |
|---|---|
| `test_tools_core.py`、`test_authoring_fixes.py`、`test_cli.py` | 纯工具函数（init、put、check、status、index、brief）与回复上限；写作通道的修复；命令行 |
| `test_tools_b3_tools.py`、`test_tools_b3_gates.py`、`test_tools_b3_fixes.py` | 第三批的 `doc`、`outline`、`evidence`、`build`、`verify`，真实的完成门，验证后的修复（`dist` 的链接与交接点、状态行、写保护） |
| `test_confinement_links.py` | 项目目录里的符号链接、Windows 联接点与重解析点（真实的 `mklink /J`，哨兵文件；37 号项）；链接在第二、第三个自有目录里时第一个目录也不删；`init` 遇到悬空的 `atlas` 联接点是干净的 AUTH_001 |
| `test_review.py`、`test_review_tool.py` | 一次性的自审（报告、抽样、记录，`status` 才说 done）；经官方客户端走 stdio |
| `test_atlas_tool_surface.py`、`test_atlas_mcp_e2e.py` | MCP 表面（两套工具集、入口提示、文档一致）；官方客户端的端到端会话（`StdioFullFlow` 走到 done） |

**查看器、构建、浏览器门（需要 Edge 或 Chrome 的测试在没有浏览器时大声跳过，跳过不是通过）**

| 文件 | 内容 |
|---|---|
| `test_viewer_i18n.py` | 界面词表与“所有文字都在表里”的规则（XP-001） |
| `test_bundle_sources.py` | 发布时打包器 `tools/bundle_viewer.py` 与它的陈旧性测试（改了查看器源码而没重新打包就失败） |
| `test_build_page.py` | 页面装配与构建检查 BD-001 至 BD-007（内存里的模型） |
| `test_build_project.py` | 真实项目上的 `build` 与 `verify`（先封存后进页面） |
| `test_gate_smoke.py`、`gate_spike.py`、`gate_fixtures/` | 浏览器门本身：判定、发现、进程处理；合成桩页面与阈值探测 |
| `test_gate_html.py`、`test_gate_html_fixes.py` | 门在真实构建页面上（三个宽度、文档孪生核对、敌意与泛化探针、校准夹具矩阵）；验证后的修复（标签页与步骤名、文字扫描、超时不是判页） |
| `test_gate_3d.py` | 3D 断言（框越界、稀疏场景）与真实的 3D、4D、5D 标签页 |
| `test_gate_csp.py` | 页面的内容安全策略：BD-004 的策略部分，反向对照，真实 Edge 里零违规（40 号项） |
| `test_gate_tool_path.py` | 工具路径上的门：默认安装目录里找浏览器、SKIP 文字不带路径、有上限的管道、各宽度共用的挂钟预算，重试也计入（35、36、42 号项）；读管道出错是失败、已退出的浏览器的子孙握着管道时不多等十秒、页面级发现里没有机器路径 |
| `gate_html_matrix.py`（脚本，不是测试）、`gate_fixtures/MATRIX.json`、`gate_fixtures/scene/` | 在构建页面上量门的统计矩阵（`--runs 20`，要几分钟）；校准夹具的预期判定（宽度、判定、发现名）；场景边界的两个夹具 |

**集成、发布、工具包、客户端（M8）**

| 文件 | 内容 |
|---|---|
| `pipeline.py`、`pipeline_flows.py`（不是测试） | 脚本流水线的驱动：`Session`（进程内，按真实 MCP 绑定的方式在完成门里调用已注册的工具函数）和 `StdioSession`（真实 stdio），记录每次调用的请求和回复字节；三个 fixture 的补全（列、TBD 回答、需求原文、证据根）、greenfield 的需求短文与事实切片、25 个模块的合成仓库与 as-is 的作者流程 |
| `test_pipeline_m8.py` | 三个 fixture（tiny、small、reference）、greenfield（采集顺序、`needs-user` 停止、不虚构需求、反向对照）、as-is（25 个模块、outline 取锚点、每个 Implemented 声明都盖了 EV-003 印记）都走到 `done`；同一条 as-is 流程走真实 stdio；参考规模页面不超过 2 MiB、重建逐字节相同、时间预算、回复上限、工作区之外什么都没写 |
| `test_release_build.py` | `build release=true`：干净项目放行；每种拒绝（没有门、红门、SKIP、INCONCLUSIVE、被改的期望、未封存、账本被篡改、证据没盖印、盖印对应别的锚点、源码移动、盖印块被改、锚点没了、没有证据根、NOT-AUTHORED、`docs=false`）都不动 `dist/` 里的页面和文档，并有正向对照；被拒绝的发布不记构建揭示、放行前对账本再判一次；证据门按声明而不按配置的模式；`evidence` 说明被改过的块没有重新盖印；暂存目录、换入与回滚、被文件锁住的换入是带码的拒绝（Windows 上用真实打开的文件）、暂存目录是链接；真实浏览器放行一次 |
| `test_kit.py` | 工具包：压缩包确定性且完整，只用标准库；解压后用不带 site-packages 的 Python（`-I -S`，基础解释器，`mcp` 被绊线绊住）跑 `check`、`status`、`evidence`、`build`，页面与仓库逐字节相同；压缩包里的文件清单由 `kit_names.txt` 逐个钉住（白名单：后缀、点开头的名字、`tests` 目录、链接都不进包，一个放了杂物的包副本证明了这一点）；启动器的 `--help` 叫 `atlas.py`；经工具包的 `build --release --no-docs` 被拒绝 |
| `test_verify_remote_atlas.py` | `scripts/verify_remote.py --atlas`：只对本地启动的 ASGI 应用（真实 HTTP）测试，`atlas_remote` 应用、有无 kit 标志、atlas 缺席时的 503（`--atlas-kit on` 下算失败，只有 `auto` 接受）、`stateless` 应用上故意失败、不带标志时输出不变 |
| `test_client_configs.py` | 示例配置（Claude Code、无头、Codex、Hermes）的 JSON、TOML、YAML 语法（没有 PyYAML 时用小的严格解析器）；无头设置恰好是 13 个工具加 Read、Grep、Glob，to-be 设置只多一条写 `atlas/user/` 的规则；按每份配置启动服务器经真实 stdio 列出 13 个工具；`scripts/atlas_mcp.py` 各子命令 |
| `test_asis_probe.py`、`test_ledger_tool.py` | `tools/pilot/asis_probe.py`（只读、不写字节码、数字已知）与 `tools/pilot/ledger.py` 和 `docs/atlas/TOKEN_LEDGER.md`（字节与估算的标注，待实测一节为空；模型数量来自模型而不是常量；报告在任何控制台编码下都是 UTF-8 加 LF；合计行等于各次调用之和） |

Node 测试（`tests/atlas/js/*.test.mjs`，只需要 Node；运行：`node --test tests/atlas/js/*.test.mjs`；纯模块部分的说明在 `tests/atlas/js/README.md`）。

| 文件 | 内容 |
|---|---|
| `view-*.test.mjs`（11 个） | 查看器纯模块：词表与布局、坐标与相机、场景缓冲、标签、搜索、回放、泛化探针、与 `bundle-contract.md` 的一致性、加固与行为钉子 |
| `assemble.test.mjs` | 文档通道的 `assemble.js` 与 Python 孪生逐字节对照 |
| `ui-logic.test.mjs`、`ui-structure.test.mjs`、`ui-shell.test.mjs`、`ui-bundle.test.mjs`、`ui-fix-b3.test.mjs` | 外壳与标签页：不需要页面的逻辑；边界约定（DOM 只经 `dom.js`、顶层名唯一、一个经典脚本）；在 DOM 桩上的整个外壳；真实数据包上的 1D 文字孪生、QA、2D 场景；验证后的修复 |
| `view3d-state.test.mjs`、`view3d-scene.test.mjs`、`view3d-gl.test.mjs`、`view3d-diagram.test.mjs`、`view3d-tabs.test.mjs`、`view3d-structure.test.mjs` | 舞台（3D、4D、5D）：只列已封存的项；场景与相机取景；WebGL 外壳对记录型上下文；序列图与状态图的几何；三个标签页端到端；模块边界 |
| `ui-helpers.mjs`、`ui-dom-stub.mjs`、`ui-concat.mjs`、`view-helpers.mjs`、`view3d-helpers.mjs` | 不是测试：数据包构造、DOM 桩、拼接顺序、合成模型与记录型 WebGL 上下文 |
