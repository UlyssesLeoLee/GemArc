# Atlas 测试说明

本目录是 GemArc Atlas 的测试、合成数据与出处检查。所有测试只依赖标准库（和仓库自带的 `gemarc_mcp`），不联网。

## 1. 怎么运行

以下命令都在仓库根目录 `D:/GemArc` 执行，使用项目虚拟环境：

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
| `edge_case(name, seed)` | 边界模型，名字见 `synth.EDGE_CASES`：`big400`、`unknown-values`、`no-cases`、`no-interventions`、`empty-strings`、`cjk`、`odd-ids`、`machine`、`insert-element`、`element-ports`、`rich-fields` |
| `Family.fixed()` / `Family.random(seed)` | id 家族。固定家族是 `svc.` `usr.` `tbl.` `rm.` `zone.` `lvl.`（加上 `evt.` `st.` 等补充前缀）；随机家族每个种子换一套前缀：**两个字母加一个数字**（如 `kb7`；结构和节点前缀小写，证据、用例、需求等记录类前缀大写，如 `KB7`），永远不等于固定家族。它是种子的纯函数，不读任何文件。前缀空间只有 6,760 个词，`test_synth.py` 的 `RandomPrefixTests` 把每一个可能的前缀（含大写写法）逐个与拒绝名单比对，所以任何种子都不可能产生名单里的词（不是只试了几个种子）；另有一个测试扫描 0 到 4999 号种子。重建拒绝名单后这两个测试若变红，应缩小 `synth.PREFIX_LETTERS` / `PREFIX_DIGITS` 的空间，而不是在运行时读名单 |
| `estimate_atl(model)` | 估计 `.atl` 作者文本的字节数（按段）。用设计 3.4 的 2175 字节示例校准，逐段一致 |

要点：

- 同一个种子在任何平台和 Python 版本上得到同样的字节（自带 SplitMix64，不用 `random`，不依赖 `hash()` 或集合顺序）；测试用不同的 `PYTHONHASHSEED` 起子进程验证。
- `unknown-values` 故意只破坏词表成员关系（未知信任值、层、层级、节点种类、关系），`contract_issues` 只返回这 7 个 `GEMARC_ATLAS_MODEL_030`；其余模型必须返回 `[]`。
- `empty-strings` 里 `sub` 为空串：`.atl` 的位置列不允许空，所以这个模型不能做 `.atl` 往返，只给查看器和文档构建检查空串。
- 重新写出示例 fixture：`PYTHONPATH=src ./.venv/Scripts/python.exe tests/atlas/synth.py write-fixture`；查看某个模型的规模：`... synth.py stats reference 7`。
- 可选字段全由生成器覆盖：`reference` 预设按参考的比例带上 `when`、`guard`、`errorCode`、`Compensation` 事件、`props.dedup`（以及落到它上面的 `dedupKey` 事件，部分重复两次）、`props.owner` / `store` / `enums`、带基数的 `references` 边、`keep` / `disable` 重写项、含元素与守卫修改的 `combined` 干预、`<用例>#<序号>` 形式的 `targetEventId`；用例长度服从 4 到 48 的重尾分布（总数仍是 322），每台机器 3 到 10 个转移。`rich-fields` 边界模型以很高的比例同时带上所有这些字段（还有已盖章的 `lineRange`），对每个种子都成立；`test_synth.py` 的 `FeatureCoverageTests` 断言预设与边界模型的并集覆盖 `REQUIRED_FEATURES` 的每一项。
- `delay_message` 的 `targetRuleId` 只取自被应用用例里的事件，`targetEventId` 只指向被应用用例里存在的事件；每个有用例的生成模型至少有一个事件显式写出 `edge`。
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
- 授权记录：移植通用代码已于 2026-10-30 获用户授权；发布（push、部署、托管路由、kit 下载）尚未授权，`PROVENANCE.md` 里写着 `PUBLICATION: PENDING`，`test_atlas_publication_gate.py` 据此断言 `.vercelignore` 排除 `/src/gemarc_mcp/atlas`、`api/index.py` 没有 atlas 路由、默认工具集不变。把 `authorisation.tsv` 的 `publication` 行改成 GRANTED 是用户的编辑。

## 6. v1 冻结测试

`test_atlas_v1_frozen.py` 固定：16 个完整工具集名称与顺序、8 个无状态工具、5 个写工具、`scaffold/cases/simulation-golden.json` 的哈希、`scaffold/vocab/*.json` 的逐文件哈希和两个摘要。哈希按 LF 归一化后计算（去 BOM，CRLF 转 LF），所以 Windows 与 Linux 检出一致。

只有 v1 维护者有意改动 v1 时才更新：运行 `PYTHONPATH=src ./.venv/Scripts/python.exe tests/atlas/test_atlas_v1_frozen.py --print`，把打印出的常量粘进测试文件，与 v1 改动同一次提交。atlas 的工作不得导致这个测试变红。

## 7. 其他测试文件

| 文件 | 内容 |
|---|---|
| `test_model_contract.py` | `gemarc-atlas/1` 契约、id 语法、推导函数、词表 |
| `test_synth.py` | 生成器：确定性、规模、一致性、家族、边界模型、估计器校准 |
| `test_strict_evidence_regression.py` | 规范校验器 `strict=True` 读 `evidenceIds` 的回归测试 |
| `test_gate_smoke.py`、`gate_spike.py`、`gate_fixtures/` | 浏览器闸门（另一通道） |
