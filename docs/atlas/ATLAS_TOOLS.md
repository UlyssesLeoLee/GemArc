# GemArc Atlas：MCP 工具契约

本文是 Atlas 工具集的接口契约：每个工具的输入、输出、体积上限和安全注解。实现在 `src/gemarc_mcp/atlas_tools.py`（绑定）和 `src/gemarc_mcp/atlas/tools.py`（纯函数）。`tests/atlas/test_atlas_tool_surface.py` 逐项核对本文与真实的 `tools/list`：工具缺章节、注解对不上、引用了不存在的工具名，测试都会失败。

Atlas 是 v1 工具集旁边的另一套工具：AI 只写紧凑的、经校验的事实文本（切片），固定且经过测试的代码推导并检查其余一切。v1 的 16 个工具和 8 个无状态工具一个字节都没有变（`tests/atlas/test_atlas_v1_frozen.py` 钉住）。

## 1. 两个工具集

一个服务器进程只带一个工具集，由环境变量 `GEMARC_TOOLSET` 选择；要同时用 v1 和 Atlas，在客户端配置里注册两个服务器条目（并集不在 v1 范围内）。

| 工具集 | 用途 | 工具 | 状态 | 写入 |
|---|---|---|---|---|
| `atlas` | 本地 stdio，完整的撰写流程 | 见第 3 节 | 项目目录即状态 | `gemarc_atlas_init`、`gemarc_atlas_put`，需要 `GEMARC_ALLOW_WRITE=1` |
| `atlas_remote` | 远程（无状态、只读） | `gemarc_atlas_brief`、`gemarc_atlas_check` | 无，不保存任何东西 | 无 |

`atlas` 和 `atlas_remote` 各自还带一个 MCP 提示 `gemarc-atlas-create`（第 5 节），v1 的两个工具集没有它；Atlas 工具集不带 v1 的资源和 `gemarc-design` 提示。

远程部署说明：`atlas_remote` 经 `api/index.py` 用 `GEMARC_TOOLSET=atlas_remote` 即可启动（测试用 `build_app(toolset="atlas_remote")` 验证过）。当前 `.vercelignore` 排除了 `/src/gemarc_mcp/atlas`，所以现在的 Vercel 部署里没有 Atlas 代码，该工具集在 Vercel 上不可用，直到出处文件 `src/gemarc_mcp/atlas/PROVENANCE.md` 的发布授权被用户放行并去掉这条排除。

## 2. 通用规则（所有 Atlas 工具）

| 规则 | 内容 |
|---|---|
| 工具名 | 只用 `[a-z0-9_]`，前缀 `gemarc_atlas_`；`len("mcp__gemarc__") + len(名字) <= 64`（Codex 把名字拼成 `mcp__<服务器>__<工具>`） |
| 输入模式 | 不含 `anyOf`、`oneOf`、`null`；可选参数用 `""`、`[]`、`0`、`false` 作默认值（Codex 和 Hermes 会改写 null 分支） |
| 注解 | 每个工具有 `title`；`openWorldHint=false`；`readOnlyHint` 如实；`destructiveHint` 和 `idempotentHint` 只给写工具（与 v1 一致，读工具里没有这两项） |
| 描述 | 每个工具的描述不超过 800 字节（测试强制）；Atlas `tools/list` 总共不超过 10 KB（测试强制）。细节放在 `gemarc_atlas_brief` 的工作指令里，不放在描述里 |
| 回复 | 一段紧凑 JSON 文本（`separators=(",",":")`，`ensure_ascii=False`，中文不被转义），没有 `structuredContent`（Codex 看到 structuredContent 会隐藏 content） |
| 失败 | `status` 为 `invalid` 或 `error` 时回复带 `isError=true`，载荷原样保留：`code`、`message`、`diagnostics`。`invalid` 是请求被拒绝（对红稿的 put 也是 `invalid`，见 `gemarc_atlas_put`），`error` 是意外失败（只有异常类型名，没有路径；`GEMARC_ATLAS_DEBUG=1` 让它抛出） |
| 体积 | 每个工具有自己的上限（见各节），回复超出时由 `fit_result()` 明确截断：`truncated:true`，能分页的带 `more:"offset=N"`。服务器再用 20 KiB 的硬上限兜底，所以客户端 24 KiB 的限制永远不会被触到。体积按 UTF-8 字节数计 |
| 写入 | 写工具需要用户在服务器配置里设置 `GEMARC_ALLOW_WRITE=1`，否则返回 `GEMARC_AUTH_002`（`isError`）。读工具（含 `gemarc_atlas_check`）不需要，也从不创建默认工作区目录；写工具在项目的某个目录指向工作区之外（目录联接、符号链接）时拒绝写入 |
| 类型错误 | 参数类型不对（例如把数字传给字符串参数）由 MCP SDK 在进入工具之前拒绝，回复是纯文本的 pydantic 消息（仍然带 `isError=true`），不是紧凑 JSON；多余的未知参数被忽略 |
| 工作区 | `GEMARC_WORKSPACE_ROOT`（默认 `~/.gemarc/workspace`），在工具被调用时才解析，注册工具时不碰磁盘。项目在 `<工作区>/<项目>/atlas/` 下 |
| 证据根 | 环境变量 `GEMARC_ATLAS_ROOTS`（`别名=路径;别名=路径`）由用户配置；工具参数里只能写别名，不能写路径 |
| authority | 回复里的 `authority`：`local`（本地服务器的结论）或 `advisory`（远程或内联文本的参考性结论） |

## 3. 工具

下面每节先列输入（括号内是默认值），再列输出和上限。读工具的回复上限 4,500 字节（`brief` 的全文 9,000，单节 3,000 以内），`index` 的 `detail=expanded` 3,000。

### `gemarc_atlas_brief`

工作指令：一次读完就能开始写。覆盖目标、循环、要写什么、代码推导什么（你不要写）、修复方式、怎样读 `status`。两个工具集都有。

| 输入 | 默认 | 说明 |
|---|---|---|
| `section` | `""` | 空或 `all` 为全文（约 4K token）；或 `format`、`vocab`、`derived`、`slices`、`oracle`、`docs`、`economics` 之一 |
| `lang` | `en-US` | `zh-CN` 或 `en-US`；其他值回退到 `en-US` 并在回复里用 `note` 说明 |

输出：`{status, authority, section, lang, bytes, text, sections[]}`。未知章节是 `invalid`。上限：全文 9,000 字节，单节 3,000。只读。

### `gemarc_atlas_init`

创建项目：`atlas.toml`、`slices/`、`.atlas/red/`、`.atlas/history/`、`.gitignore`。只存在于 `atlas` 工具集。

| 输入 | 默认 | 说明 |
|---|---|---|
| `project` | 必填 | 项目名（小写字母、数字、连字符） |
| `title` | `""` | 标题，空则用项目名 |
| `mode` | `asis` | `asis`（根据现有代码和文档还原现状）或 `tobe`（根据用户需求设计新系统） |
| `languages` | `["zh-CN"]` | 文档语言，第一个是主语言 |
| `profile` | `software` | 词表档案 |
| `repos` | `[]` | 证据根别名（须在服务器的 `GEMARC_ATLAS_ROOTS` 里配置） |

输出：`{status:"created", project, path, mode, created[], next[]}`；`path` 是绝对路径，这是有意的：模型要用自己的文件写入能力把需求原文保存到它下面的 `user/`（init 已建好这个目录；用户对 TBD 的回答也原话保存在这里）。这是回复里唯一的主机路径，错误和诊断里没有。已有项目是 `invalid`（不覆盖，回复带 `path`，直接用 `gemarc_atlas_status` 继续）。参数过大（`repos` 超过 32 个、标题不是合法文本）在创建任何目录之前被拒绝。需要 `GEMARC_ALLOW_WRITE=1`。注解：写工具，非破坏性，非幂等。

### `gemarc_atlas_put`

校验并保存一个切片。这是撰写的主路径：没有 ERROR 就提交，有任何 ERROR 就不提交，但整段文本被保存为该切片的红稿（什么都不会丢）。

| 输入 | 默认 | 说明 |
|---|---|---|
| `project` | 必填 | 项目名 |
| `slice` | 必填 | 切片名 `[a-z0-9-]{1,40}`，按阅读顺序编号（`00-space`、`10-structure`、`20-scenarios` ...） |
| `text` | 必填 | 切片文本；超过 64 KiB 作为红稿保存并报 ST-008，超过 128 KiB 直接拒绝 |
| `mode` | `replace` | `replace` 整片替换；`patch` 只发改动的行：同一身份的行替换，`-id` 删除一行；有红稿时补丁打在红稿上 |
| `format` | `atl` | `atl`（默认）或 `jsonl` |
| `base` | `""` | 这次写入应当替换的 sha8（已提交版本或红稿的 sha8）；不一致返回冲突，什么都不写 |

输出：

- 提交：`{status:"committed", slice, format, sha8, counts, diagnostics, diagnostic_counts?（全为零时省略）, unchanged, previous, next[1 行], pending?, patch?}`。`next` 只有一行，照它做；完整列表用 `gemarc_atlas_status`（红稿之后、续接、大约每 5 次 put）。空补丁（只有 `#atlas 1`）会重新检查红稿。替换一行的补丁若没有自带 `@set` 行，沿用被替换行的默认值。同样的文本得到同样的 sha8 和 `unchanged:true`。
- 红稿：`{status:"invalid", red:true, draft_sha8, diagnostics, ..., next[]}`，带 `isError=true`。这是正常的修复循环的一部分：按 `next` 用 `mode=patch` 只改出错的行（先改列出的第一条错误，后面的往往是它的后果）。
- 其他拒绝（名字不合法、冲突、没有写权限）：`status:"invalid"`，带 `code`，没有写任何东西。

上限 4,500 字节（诊断部分约 2,800 字节，超出的用 `gemarc_atlas_check` 的 `offset` 取）。注解：写工具，破坏性（替换会归档旧版本，补丁会删除行；历史保留最近 10 个），幂等。需要 `GEMARC_ALLOW_WRITE=1`。

### `gemarc_atlas_check`

校验，永远不写。两个工具集的输入不同。

**`atlas`（本地）**

| 输入 | 默认 | 说明 |
|---|---|---|
| `project` | `""` | 校验项目；与 `text` 二选一 |
| `text` | `""` | 校验一段内联文本（authority 为 advisory，只看得到自己）；最多 128 KiB |
| `slice` | `""` | 只看这一个切片（有红稿时先看红稿，回复里 `source` 说明） |
| `mode` | `slice` | `slice`：引用后面切片的 id 记为待定（INFO）；`full`：整个项目的封闭世界，引用到没有定义的 id 都是 ERROR，存在红稿时拒绝（ST-009） |
| `rule` | `""` | 只保留一条规则或一个族（如 `RF`） |
| `offset` | `0` | 诊断翻页，与 put 回复里的 `more: offset=N` 对应 |
| `format` | `text` | `text` 或 `json` |

**`atlas_remote`（远程）**

| 输入 | 默认 | 说明 |
|---|---|---|
| `text` | 必填 | 一个切片的全文，最多 32 KB |
| `known` | `""` | 其他切片已定义的 id，每行一个 `<kind> <id>`，最多 32 KB；引用它们的地方就能解析 |
| `format` | `text` | `text` 或 `json` |

远程检查只做语法、词表和切片内部引用（`scope:"grammar+intra-slice"`），回复里 `authority:"advisory"`：权威结论在本地服务器上（`mode=full`）。

输出：`{status:"ok", authority, scope, mode, clean, counts, diagnostics, pending?, red_drafts?, source?, next[]}`；`clean` 为 false 表示有 ERROR，但工具本身的调用成功，所以 `status` 仍是 `ok`（请求被拒绝才是 `invalid`）。上限 4,500 字节。只读。

### `gemarc_atlas_status`

项目现在到哪一步、接下来做什么；丢了上下文后用它续上。只存在于 `atlas` 工具集。

| 输入 | 默认 | 说明 |
|---|---|---|
| `project` | 必填 | 项目名 |
| `view` | `next` | `next`、`report`、`dims`、`oracle`、`evidence` |
| `scope` | `""` | 只对 `report` 有意义：按层级或槽位前缀过滤 |

`view=next` 的输出：`{status, authority, view, done, phase, next[], blockers[], needsUser[], openBlocks?, counters?}`，整个回复不超过 1,200 字节（`needsUser` 最多两条加一个计数，`blockers` 最多五个，`next` 从中间删行但保留前三行和最后一行）。`counters` 是 `put` 记在 `.atlas/puts.json` 里的 `{puts, redRounds}`，所以重启后循环保护（同一切片 3 轮修复、put 预算）仍然有效，超限时 `next` 里有一行 `0 STOP`。`done` 为 true 时 `openBlocks` 列出自审时已确认存活的 BLOCK 发现（`next` 里有对应一行），续接的会话也看得到。`next` 至多 10 行，每行行首的数字是优先级（1 红稿或错误，2 BLOCK 槽位，3 未定义的 id，4 需求缺口，5 无期望的用例，6 不可达状态，7 覆盖），最后一行是 1D-5D 的就绪情况。`phase` 取 `collect`、`repair`、`author`、`needs-user`、`build`、`review`、`done`：`needs-user` 表示只剩只有用户能决定的事，必须停下来问用户。`done` 为 true 需要：没有红稿、没有 BLOCK、完整检查没有 ERROR、所需维度就绪、所有适用的门为绿，以及当前模型有自审记录。其余视图 4,500 字节上限。只读。

### `gemarc_atlas_index`

按需看已提交模型里有什么（紧凑的行），代替重读切片。只存在于 `atlas` 工具集。

| 输入 | 默认 | 说明 |
|---|---|---|
| `project` | 必填 | 项目名 |
| `scope` | `nodes` | `nodes`、`room:<id>`、`req:<id>`、`case:<id>`、`ids:<前缀>` |
| `with_edges` | `false` | 同时列出相关的边 |
| `ids` | `[]` | 只取这些 id |
| `detail` | `line` | `line` 为 `id 种类 名称`；`expanded` 为带定义处 `文件:行` 的记录 |
| `offset` | `0` | 翻页，回复里 `more: offset=N` |

输出：`{status, authority, scope, detail, total, offset, shown, text, more?, truncated?}`。上限 4,500 字节（`expanded` 3,000）。只读。

### `gemarc_atlas_review`

一次性自审（设计附录 A1 的 D15）。只存在于 `atlas` 工具集。`status` 的 phase 到 `review` 时调用：先 `report`，核对，只修复列出的问题，再 `record`。

| 输入 | 默认 | 说明 |
|---|---|---|
| `project` | 必填 | 项目名 |
| `action` | `report` | `report`（只读，从不写文件）或 `record`（写 `.atlas/review.json`，需要 `GEMARC_ALLOW_WRITE=1`） |
| `notes` | `""` | 只对 `record` 有意义，至多 4 KB；建议写 `fixed=RV-C2,RV-K1; open=RV-H1`，其中的 `fixed` 会被核对 |

`report` 的输出（至多 4,500 字节，同一个模型每次逐字节相同）：`{status, authority, action, schema, modelSha, mode, counts{BLOCK,GAP,INFO}, stats, findings[], oracle, record, sample[], compare, next[]}`。`findings` 按 BLOCK、GAP、INFO 排序，每条 `{id, sev, n, ids（前三个）, fix（一行修复提示）}`：

| 编号 | 级别 | 含义 |
|---|---|---|
| `RV-C1`、`C2`、`C3` | GAP | 需求没有节点、没有用例、用例里没有期望 |
| `RV-C4` | INFO | 不属于任何需求的节点 |
| `RV-C5`、`C6`、`C7` | GAP | 没有边的节点、没有期望的用例、不可达的状态机状态 |
| `RV-E1` | BLOCK | `Implemented` 的节点或边没有源码证据 |
| `RV-E2` | INFO | 继承证据的边数（`ev=^`） |
| `RV-E3` | GAP | `Inferred` 占比超过 30% |
| `RV-E4` | GAP / INFO | as-is：锚点没有核验印记（还没有印记文件时是 INFO，写作 `not verified yet`） |
| `RV-E5` | BLOCK / GAP | to-be：`user:` 引文在已保存的需求文本里找不到（没有保存文本时是 GAP） |
| `RV-H1` | GAP | `by=ai` 的政策 |
| `RV-H2` | BLOCK | 会打印 `[NOT-AUTHORED]` 的槽位 |
| `RV-H3` | GAP | 没有用户决定的 TBD |
| `RV-H4`、`H5` | BLOCK / GAP | 需求没有来源（to-be 是 BLOCK）；to-be 需求的来源引文不在已保存的文本里 |
| `RV-K1`、`K2`、`K4` | INFO | 近似重名、空房间或空楼层、种类与关系的合理性（只是提示，永远不是 ERROR） |
| `RV-K3` | GAP | 没有已定义房间的节点 |
| `RV-O1` | GAP | 预言（oracle）里用户还没确认的期望；预言还不存在时 `oracle` 写 `not available yet` |

`sample` 是固定 8 行的抽查样本（证据 3、边 2、需求 2、政策 1，不足时由其他类补满，由模型 sha 决定，所以稳定）；每行 `{at, k, id, claim, vs}`，`at` 是 `slices/<文件>:<行>`（相对项目的 `atlas/` 目录，打开文件就是该行），`claim` 是一句话的断言，`vs` 与 `compare` 说明拿它去对照什么（真实源码、需求原文）。

`record` 只有一次：必须在 phase 为 `review` 时调用（其余 `done` 条件都已满足），否则 `GEMARC_ATLAS_REVIEW_003`；已有有效记录时 `GEMARC_ATLAS_REVIEW_002`；没有写开关时 `GEMARC_AUTH_002`，不写任何文件。记录是 `.atlas/review.json`：`{schema, modelSha, mode, findingsAtReview, blockKeys, fixedAfter, notes}`（没有时间戳）。`fixedAfter` 是 `notes` 里声称已修复、且现在确实不再出现的编号。`record` 的回复里有 `done`、`phase`、仍然存活的 BLOCK 发现（写进最终报告交给用户）。

`status` 对记录的读法：模型与记录的 `modelSha` 一致，则记录有效；模型在记录之后又改过，只有同时满足"完整检查没有 ERROR（以及没有红稿、没有 BLOCK 槽位、其他门为绿）"和"没有新的 BLOCK 发现"才仍显示 `done`。"新的 BLOCK 发现"只比较模型本身能显示的那几类（`RV-E1`、`RV-H2`、`RV-H4`），读文件的检查（`RV-E5`、`RV-H5`）只在记录时判定。没有 `blockKeys` 的记录只认完全相同的模型。这条规则由 `review.accepts_changed_model` 实现，需要 `status.evaluate` 里的一个接入点来调用它；接入点落地之前，`status` 只认完全相同的模型（更严，不会误报 `done`）。

## 4. 注解表

下表与真实的 `tools/list` 逐项一致（测试强制）；`—` 表示该工具没有这一项（读工具没有 `destructiveHint` 和 `idempotentHint`）。每个工具在它所属的每个工具集里一行，最后一列是工具集。

| 工具 | 标题 | readOnly | destructive | idempotent | openWorld | 工具集 |
|---|---|---|---|---|---|---|
| `gemarc_atlas_brief` | Atlas work order | true | — | — | false | atlas |
| `gemarc_atlas_init` | Create an atlas project | false | false | false | false | atlas |
| `gemarc_atlas_put` | Store one slice | false | true | true | false | atlas |
| `gemarc_atlas_check` | Check an atlas project or text | true | — | — | false | atlas |
| `gemarc_atlas_status` | Atlas project status | true | — | — | false | atlas |
| `gemarc_atlas_index` | List what the atlas holds | true | — | — | false | atlas |
| `gemarc_atlas_review` | One-time self-review | false | false | false | false | atlas |
| `gemarc_atlas_brief` | Atlas work order | true | — | — | false | atlas_remote |
| `gemarc_atlas_check` | Check slice text (advisory) | true | — | — | false | atlas_remote |

## 5. 入口提示 `gemarc-atlas-create`

用户只说一句话（"创建5D设计"）就能推进到完成并自审一次。MCP 服务器不能主动推动客户端，所以入口是一个 MCP 提示：Claude Code 把它显示成斜杠命令，Codex 和 Hermes 可以用 `scripts/atlas_mcp.py get-prompt` 取出文本后粘贴。提示的文本就是驱动脚本，状态在项目目录里，所以随时可以说"继续"从 `gemarc_atlas_status` 续上。

参数：`requirement`（需求原文或已保存文件的路径）、`mode`（`asis` 或 `tobe`；不写时给了需求原文就是 `tobe`，否则 `asis`；其他值被拒绝）、`project`（小写字母、数字、连字符）、`lang`（`zh-CN`、`ja-JP` 或 `en-US`，也接受 `zh`、`ja`、`en`；默认 `zh-CN`，其他值被拒绝）。

流程：读工作指令，建项目，把需求原文保存到项目的 `user/`，然后循环 {`gemarc_atlas_status` 的 `view=next` -> 只写它点名的那个切片 -> `gemarc_atlas_put`；红稿用 `mode=patch` 修复}，直到 phase 到 `review`；然后恰好一次自审，再写最终报告。

停止条件（提示里逐条写明）：

- `needs-user`：停下，把 `needsUser` 原样问给用户，绝不替用户决定、绝不编造事实。
- 同一切片最多修复 3 轮，第 4 次仍红就报告 `blocked`，附最后一次诊断。
- `put` 总次数预算 = 8 + 2 x ceil(节点数/11)（来自 `gemarc_atlas_brief` 的 economics 一节"每 11 个节点约两次 put"，估不出节点数时按 40 个，即 16 次）；用完仍未到 review 就报告 `blocked`。
- 自审恰好一次：`gemarc_atlas_review` 先 `report` 取机械报告，核对后只修复列出的问题，再 `record`；`record` 之后不开第二轮自审，仍然存活的 BLOCK 发现写进最终报告交给用户。

`atlas_remote` 带一个简短的变体：只用 `gemarc_atlas_brief` 和 `gemarc_atlas_check`，切片保存在你自己的本地文件里，自审用对照源码抽查代替，结论是 advisory。

## 6. 还没有注册的工具

下面这些工具由后续任务注册（设计表 4.1）；注册时把名字从 `atlas_tools.PLANNED_TOOLS` 里删掉，在第 3 节加一节、在第 4 节加一行，并在 `src/gemarc_mcp/atlas_tools.py` 里加一个用 `@atlas_tool` 装饰的函数：

- `gemarc_atlas_simulate`、`gemarc_atlas_doc`、`gemarc_atlas_outline`、`gemarc_atlas_evidence`、`gemarc_atlas_build`、`gemarc_atlas_verify`。

在这些工具到位之前，`gemarc_atlas_status` 的 4D、5D 只报告不阻塞，`done` 不要求构建；自审工具 `gemarc_atlas_review` 已经注册，入口提示因此用真实的自审条款。

## 7. 命令行客户端

`scripts/atlas_mcp.py` 是一个只依赖标准库和官方 MCP SDK 的薄客户端，通过真实的 stdio 与服务器通话，供手工测试，也供没有 MCP 接入的无头代理使用：

```
python scripts/atlas_mcp.py list-tools
python scripts/atlas_mcp.py list-prompts
python scripts/atlas_mcp.py get-prompt gemarc-atlas-create mode=asis project=demo
python scripts/atlas_mcp.py call gemarc_atlas_status '{"project":"demo"}'
```

输出是紧凑 JSON，回复为 `isError` 时退出码非零。服务器环境不继承整个环境（SDK 只传过滤后的环境，与 Claude Code、Codex、Hermes 一致）：转发本 shell 里的 `GEMARC_*`，`GEMARC_TOOLSET` 默认 `atlas`。接入三个客户端的配置见 `docs/atlas/examples/README.md`。
