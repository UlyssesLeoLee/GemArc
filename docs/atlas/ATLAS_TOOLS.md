# GemArc Atlas：MCP 工具契约

本文是 Atlas 工具集的接口契约：每个工具的输入、输出、体积上限和安全注解。实现在 `src/gemarc_mcp/atlas_tools.py`（绑定）和 `src/gemarc_mcp/atlas/tools.py`（纯函数）。`tests/atlas/test_atlas_tool_surface.py` 逐项核对本文与真实的 `tools/list`：工具缺章节、注解对不上、引用了不存在的工具名，测试都会失败。

Atlas 是 v1 工具集旁边的另一套工具：AI 只写紧凑的、经校验的事实文本（切片），固定且经过测试的代码推导并检查其余一切。v1 的 16 个工具和 8 个无状态工具一个字节都没有变（`tests/atlas/test_atlas_v1_frozen.py` 钉住）。

## 1. 两个工具集

一个服务器进程只带一个工具集，由环境变量 `GEMARC_TOOLSET` 选择；要同时用 v1 和 Atlas，在客户端配置里注册两个服务器条目（并集不在 v1 范围内）。

| 工具集 | 用途 | 工具 | 状态 | 写入 |
|---|---|---|---|---|
| `atlas` | 本地 stdio，完整的撰写流程 | 见第 3 节 | 项目目录即状态 | `init`、`put`、`simulate`、`evidence`、`build`、`verify`、`review record`，需要 `GEMARC_ALLOW_WRITE=1` |
| `atlas_remote` | 远程（无状态、只读） | `gemarc_atlas_brief`、`gemarc_atlas_check` | 无，不保存任何东西 | 无 |

`atlas` 工具集共 13 个工具：设计表 4.1 的 12 个，加上附录 A1 的自审工具 `gemarc_atlas_review`。

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
| 类型错误 | 参数类型不对（例如把数字传给字符串参数）由 MCP SDK 在进入工具之前拒绝，回复是纯文本的 pydantic 消息（仍然带 `isError=true`），不是紧凑 JSON；多余的未知参数被忽略。值域错误（未知的 `lang`、负的 `offset` 或 `page`）由工具自己拒绝：`GEMARC_ATLAS_TOOL_001`，不会悄悄当成别的值。atlas 专有的拒绝消息是英文，`GEMARC_AUTH_*` 沿用 v1 的中文消息 |
| 路径 | 回复里除了 `init` 的 `path`（模型要用它保存需求原文，见该节）之外没有主机路径：`build` 的 `dist`、`verify` 的 `report` 是相对项目的（`atlas/dist`、`atlas/dist/.verify/gate-report.json`）；操作者的环境值（`GEMARC_ATLAS_BROWSER`）不进回复，也不进 `dist/.verify/gate-report.json`；浏览器门的页面级发现（`PAGE_ERRORS`、`CONSOLE_STDERR`、被挡的资源等）里的 `file://` 网址与盘符路径在回复和报告里只留文件名与行列（`smoke.scrub_host_paths`）。直接调用 `tools.build`、`tools.verify` 的返回值和命令行入口仍带完整的 `dist`、`report` 路径（项目在本机的位置，含用户名）：只有 MCP 层把它们改成相对路径 |
| 工作区 | `GEMARC_WORKSPACE_ROOT`（默认 `~/.gemarc/workspace`），在工具被调用时才解析，注册工具时不碰磁盘。项目在 `<工作区>/<项目>/atlas/` 下 |
| 证据根 | 环境变量 `GEMARC_ATLAS_ROOTS`（`别名=路径;别名=路径`）由用户配置；工具参数里只能写别名，不能写路径（`outline` 的 `root` 参数就是别名） |
| authority | 回复里的 `authority`：`local`（本地服务器的结论）或 `advisory`（远程或内联文本的参考性结论） |

## 3. 工具

下面每节先列输入（括号内是默认值），再列输出和上限。读工具的回复上限 4,500 字节（`brief` 的全文 9,000，单节 3,000 以内），`index` 的 `detail=expanded` 3,000。

### `gemarc_atlas_brief`

工作指令：一次读完就能开始写。覆盖目标、循环、要写什么、代码推导什么（你不要写）、修复方式、怎样读 `status`。两个工具集都有。

| 输入 | 默认 | 说明 |
|---|---|---|
| `section` | `""` | 空或 `all` 为全文（约 4K token）；或 `format`、`vocab`、`derived`、`slices`、`oracle`、`docs`、`economics` 之一 |
| `lang` | `en-US` | `zh-CN` 或 `en-US`；`ja-JP` 回退到 `en-US` 并在回复里用 `note` 说明；其他值是 `GEMARC_ATLAS_TOOL_001` |

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

输出：`{status:"created", project, path, mode, created[], next[]}`；`path` 是绝对路径，这是有意的：模型要用自己的文件写入能力把需求原文保存到它下面的 `user/`（init 已建好这个目录；用户对 TBD 的回答也原话保存在这里），没有别的办法告诉它写到哪里。这是 MCP 回复里唯一的主机路径（`build`、`verify` 给相对路径），错误和诊断里没有。工作区在某个证据根（`GEMARC_ATLAS_ROOTS`，即被分析的仓库）里面时不拒绝，但 `next` 的第一行是 `WARNING`，请操作者把 `GEMARC_WORKSPACE_ROOT` 挪到外面。已有项目是 `invalid`（不覆盖，回复带 `path`，直接用 `gemarc_atlas_status` 继续）。参数过大（`repos` 超过 32 个、标题不是合法文本）在创建任何目录之前被拒绝。需要 `GEMARC_ALLOW_WRITE=1`。注解：写工具，非破坏性，非幂等。

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
| `mode` | `slice` | `slice`：引用后面切片的 id 记为待定（INFO）；`full`：整个项目的封闭世界，引用到没有定义的 id 都是 ERROR，存在红稿时拒绝（ST-009）。`full` 跑第一波与第二波规则（`status.FULL_WAVE_SET = (1, 2)`）：CS-001、CS-005、CS-008、PT-003、SM-004/006/007、IV-001/002、OR-000 至 OR-005 不用点名就会运行，所以 `status`（完成契约）、`build`、命令行与 MCP 看到同样的发现；预设的合成模型（`tiny`、`small`、`reference`）在这里没有 ERROR 与 WARN，只有 INFO（如 CS-008、OR-005）；边界模型 `record-crossings` 故意带 4 条 PT-003 WARN，并且恰好这 4 条。**真实的 atlas 通常会带 PT-003 WARN**：模块把消息发布进事件记录是合法的跨类型边，端口类型由节点种类推出，没有逐条接受的办法，所以保持原样，只有节点种类写错时才改；WARN 不挡 `done`（`status` 的 done 行会写 `(N WARN open: ...)`，不写「clean」）。切片检查（`mode=slice`，也是 `put` 的检查）只跑第一波 |
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

`view=next` 的输出：`{status, authority, view, done, phase, next[], blockers[], needsUser[], openBlocks?, waived?, counters?}`，整个回复不超过 1,200 字节（`needsUser` 最多两条加一个计数，`blockers` 最多八个，`next` 从中间删行但保留前三行和最后一行）。`needsUser` 只写问用户的话，每条是一个完整的句子（至多 150 字节，更长的在词界截断并以 ` ...` 结尾，绝不在句中无标记地截断）；这之后 AI 要做什么（保存原话、写 policies 行、把 oracle 表交出去）写在 `next` 里，不写进 `needsUser`。`blockers` 是完整的记号（`gate-evidence:5/5 unverified`），不是被截断的句子。`waived` 列出被用户豁免的门（目前只有 `browser`）：豁免不是验证，`done` 的那一行写 `browser gate WAIVED by the user, the page is UNVERIFIED`，且不把它算进 `gates green`；`done` 时若还有 sealed 但没被用户确认的期望，另有一行 `N expectation(s) await the user`。`counters` 是 `put` 记在 `.atlas/puts.json` 里的 `{puts, redRounds}`，所以重启后循环保护（同一切片 3 轮修复、put 预算）仍然有效，超限时 `next` 里有一行 `0 STOP`。`done` 为 true 时 `openBlocks` 列出自审时已确认存活的 BLOCK 发现（`next` 里有对应一行），续接的会话也看得到。`next` 至多 10 行，每行行首的数字是优先级（1 红稿或错误，2 BLOCK 槽位，3 未定义的 id，4 需求缺口，5 无期望的用例，6 不可达状态，7 覆盖），最后一行是 1D-5D 的就绪情况。`phase` 取 `collect`、`repair`、`author`、`needs-user`、`build`、`review`、`done`：`needs-user` 表示只剩只有用户能决定的事，必须停下来问用户。`done` 为 true 需要：没有红稿、没有 BLOCK、完整检查没有 ERROR、所需维度就绪、所有适用的门为绿（第 6 节：oracle、evidence、docs、build、browser），以及当前模型有自审记录。其余视图 4,500 字节上限。只读。

### `gemarc_atlas_index`

按需看已提交模型里有什么（紧凑的行），代替重读切片。只存在于 `atlas` 工具集。

| 输入 | 默认 | 说明 |
|---|---|---|
| `project` | 必填 | 项目名 |
| `scope` | `nodes` | `nodes`、`room:<id>`、`req:<id>`、`case:<id>`、`ids:<前缀>` |
| `with_edges` | `false` | 同时列出相关的边 |
| `ids` | `[]` | 只取这些 id |
| `detail` | `line` | `line` 为 `id 种类 名称`；`expanded` 为带定义处 `文件:行` 的记录 |
| `offset` | `0` | 翻页，回复里 `more: offset=N`；负数被拒绝 |

输出：`{status, authority, scope, detail, total, offset, shown, text, more?, truncated?}`。上限 4,500 字节（`expanded` 3,000）。只读。

### `gemarc_atlas_simulate`

对已登记的期望给出盲测判定（设计第 7 节）。这是写调用：先封存还能封存的期望，再运行判定，账本写在 `.atlas/oracle.json`。只存在于 `atlas` 工具集。

| 输入 | 默认 | 说明 |
|---|---|---|
| `project` | 必填 | 项目名 |
| `case` | `""` | 只看一个用例（可与 `intervention` 同用） |
| `intervention` | `""` | 只看一个干预（与它适用的用例成对） |
| `detail` | `count` | `count` 只说通过与否，以及被断言的节点里有**多少个**不符，从不说是哪些；`ids` 点名不符的节点，`trace` 再加有界的轨迹摘要：这两个是**揭示**，写进账本，揭示之后再改的期望会变成 `revised`，要用户确认 |

输出：`{status, authority, detail, modelSha8, summary{baselines, pairs}, pass[], fail[], refused[], next[], clean}`，上限 3,000 字节（拒绝回复也不超）。一个期望名里有没有定义的节点，回复 `OR-000 not registered`，不给判定。`clean` 为 true 才表示每个判定都跑了且全部通过。注解：写工具，非破坏性，幂等。需要 `GEMARC_ALLOW_WRITE=1`。

### `gemarc_atlas_doc`

派生文档的**结构**视图：只看结构，从不给引擎结果，所以不可能泄漏判定。只存在于 `atlas` 工具集。

| 输入 | 默认 | 说明 |
|---|---|---|
| `project` | 必填 | 项目名 |
| `unit` | `system` | `system`（全部元素）、`req:<id>`、`case:<id>`、`node:<id>`；直接写元素 id 也行 |
| `level` | `BD` | `REQ`、`BD` 或 `DD` |
| `lang` | `""` | 空则用项目的第一个语言 |
| `chapter` | `""` | 空为大纲；`1` 到 `12` 返回该章正文 |
| `page` | `0` | 章节分页，从 1 起（0 当作 1；负数被拒绝） |

输出（大纲）：`{status, authority, unit, level, lang, no, bytes, filled, total, ch, junk, links, markers, blocks, other, pages, sha8, next?}`，不超过 1 KiB；`ch` 每章一位数字（0 为空状态，1 有内容，2 有内容但含 `[NOT-AUTHORED]` 空位），`markers` 是挡住 `done` 的空位个数，`links` 与 `junk` 是文档检查的计数。输出（章节）：`{status, authority, chapter, page, pages, title, text}`，正文每页约 15 KiB（JSON 转义后仍在 20 KiB 硬上限内）。只读，不写任何文件。

### `gemarc_atlas_outline`

读源码的辅助：一个文件的 def、class、标题行，带行号和一个唯一的锚点片段，直接抄进证据行的 `anchor`。只存在于 `atlas` 工具集。

| 输入 | 默认 | 说明 |
|---|---|---|
| `root` | 必填 | 证据根的**别名**（由操作者在服务器环境 `GEMARC_ATLAS_ROOTS` 里设置）；写成路径会被拒绝 |
| `path` | 必填 | 相对于该根的文件路径；绝对路径、盘符、`..`、符号链接逃逸都是错误 |
| `offset` | `0` | 从第几个条目继续（回复里的 `next`） |

输出：`{status, authority, file, language, shown, total, text, next?, more?, warn?}`，`text` 每页不超过约 3.3 KB，整个回复 4,500 字节以内。文件大于 8 MiB 时在读取之前就拒绝（先 `stat`，`GEMARC_ATLAS_EV_006`；读取本身最多读 8 MiB 加一个字节），所以指向巨大文件的路径不会把它读进内存。`evidence` 对证据文件和项目 `user/` 下保存的需求文本用同一个读法（`evidence.read_text_file`）：过大的文件是 `NOT_TEXT`（`user:` 的引用是 `USER_TEXT_MISSING`），不读内容。`.env*` 只列键名，密钥类文件不给大纲，锚点经过凭据检查。只读。

### `gemarc_atlas_evidence`

把证据行对着真实文件核验，并给通过的行盖印（`.atlas/evidence-stamp.json`）。证据根来自服务器环境 `GEMARC_ATLAS_ROOTS`；项目在 `atlas.toml` 里写了别名时，没写的别名不读。只存在于 `atlas` 工具集。

| 输入 | 默认 | 说明 |
|---|---|---|
| `project` | 必填 | 项目名 |
| `ids` | `[]` | 只核验这些证据 id；空为全部 |

输出：`{status, authority, rows, pass, warn, fail, skip, stamped, roots[], lines[], next[], claims?, stale?}`，上限 3,000 字节（`lines` 超出时截断并给 `more`）。`stale` 是通过了但盖印的那一块在盖印之后被改过的行数（EV-005）：这个工具不给它们重新盖印，发布会拒绝它们（只要有 Implemented 声明靠着它们），`next` 说要重读代码并用另一个锚点重新引用。每个不是绿色的行一行，带修复建议，建议**从不自动应用**；没有配置证据根的别名是 `SKIP`，绝不是通过。`claims` 给出 Implemented 声明的总数和仍未核验的个数（as-is 项目总有；别的项目只在有这类声明时有）：`status` 的 `evidence` 门和发布的证据规则都按声明判断，不按 `atlas.toml` 的 `mode` 判断：每个 Implemented 声明的源码证据都要有与当前文件、锚点相符的印记。注解：写工具，非破坏性，幂等。需要 `GEMARC_ALLOW_WRITE=1`。

### `gemarc_atlas_build`

写 `atlas/dist/`：`index.html`（查看器，一个自包含的文件）、`build-info.json`、`docs/`、`diagrams/`、`prompts/`，以及交给用户的 `oracle-sheet.md`（每条未确认的期望一行，外加一行 `APPROVE-ALL`）。只有 sealed 或 confirmed 的期望会在页面上显示结果，其余显示空状态。

| 输入 | 默认 | 说明 |
|---|---|---|
| `project` | 必填 | 项目名 |
| `release` | `false` | 发布构建：oracle 门（先跑判定和变异体）、证据（对源码重新核验，没有过期的戳）、没有 NOT-AUTHORED 空位、浏览器门通过（需要 `gate=smoke`）；暂存后才换进 `dist/`，被拒绝时 `dist/` 原样不动 |
| `docs` | `true` | `false` 不写 `docs/`、`diagrams/`、`prompts/`（这样 `status` 的构建门不会变绿） |
| `gate` | `none` | `smoke` 在写完页面后，用无头浏览器以 1024、1366、1600 宽度跑浏览器门 |

输出：`{status, authority, dist（`atlas/dist`）, page{bytes, sha256}, modelSha8, parts, data, checks, findings, oracle, withheld, docs, gate, lines, release, sheet?, next[]}`，上限 2,000 字节。模型有 ERROR 时拒绝（`GEMARC_ATLAS_BUILD_001`，什么都不写）：这里的 ERROR 是默认完整检查（第一波加第二波）的，所以封存的期望被引擎否定（OR-001）、覆盖下限（OR-003）、干预声明与引擎不符（IV-001、IV-002）也算：判定失败的项目要先修好（`status` 的 phase 是 `repair`）再构建，不再是「写出页面、发布门再报 `mismatch`」；拒绝回复的 `message` 指向 `gemarc_atlas_check mode=full`，`diagnostics[].message` 在位置后带规则提示；`dist`、`out`、`dist/.verify`、`dist/docs` 等是符号链接、目录联接（junction）或其它重解析点（哪怕指向工作区之内），或解析到工作区之外时在动任何文件之前拒绝（`GEMARC_AUTH_001`，构建会删除并重写这些目录；`project.is_link` 用 `is_symlink`、`os.path.isjunction` 与重解析点属性三道判断，删除处再检查一次，绝不穿过链接删除）；不带 `release` 的构建遇到浏览器门 FAIL 时回复 `invalid`（`GEMARC_ATLAS_BUILD_030`），页面已经写出（页面的 sha 变了，所以之前的浏览器结论作废，要重新 `verify`）；**发布构建不是这样**：`release=true` 先在 `atlas/.atlas/release-stage/` 里组装整个 `dist/`，在那里依次过 oracle 门（先跑判定和变异体）、证据（对源码重新核验，见下）、文档里没有 NOT-AUTHORED、BD/RC/LB 构建检查和浏览器门，全部通过才把页面、`build-info.json`、`docs/`、`diagrams/`、`prompts/`、`.verify/` 逐项换进 `dist/`（`oracle-sheet.md` 不动）。发布还要求 `docs=true` 和 `gate=smoke`：`docs=false` 的发布会在换入时删掉此前的文档、图和提示词，所以和没有跑浏览器门一样，在暂存之前就被拒绝（阻挡项 `docs`）。任何一项挡住发布，回复 `invalid`（`GEMARC_ATLAS_BUILD_020`，不带 `page`，列出每一类阻挡项和一行行动提示），`dist/` 里此前的页面、文档、图、提示词、`build-info.json` 和 `.verify/` 一个字节也没变（暂存目录总会被删掉；唯一会写的是交给用户的 `oracle-sheet.md`，让用户有东西可以确认，所以回复写「no page or document of dist/ was changed」，而不是「dist/ 没变」；此前没有 `dist/` 时，被拒绝的发布会建出只含 `oracle-sheet.md` 的 `dist/`）。被拒绝的发布也不记「构建揭示」：期望的轨迹只在页面真的要换入 `dist/` 时才记入账本（所有门都过之后、换入之前），所以因为门没过而被拒绝的发布不会让之后对期望的诚实修改变成 `revised`（唯一的例外是换入本身失败：文件被锁时揭示已经记了，没有页面被看到，方向是保守的，之后改这些期望要你确认）；记完之后 oracle 门会对账本再判一次（同一指纹的期望是一个揭示单位，记录可能让兄弟项变成 `revised`），仍然关着就拒绝。换入时 Windows 上有程序把 `dist/` 里的文件开着（编辑器、预览、同步客户端）会让换入失败：`GEMARC_ATLAS_BUILD_021`（`invalid`，`message` 点名是哪一部分，叫你关掉后重新发布），已换入的部分全部回滚，`dist/` 原样。要看红色浏览器门的具体问题，用不带 `release` 的 `gate=smoke` 构建。发布构建的证据规则：每个 Implemented 声明的源码证据要有与当前行（仓库、文件、锚点）一致的戳（`EV-003`），而且戳对源码仍然成立（`EV-005`，发布时只读地重新核验，需要操作者配置的 `GEMARC_ATLAS_ROOTS`）：源码在盖戳之后移动了锚点，再跑一次 `gemarc_atlas_evidence` 重新盖戳；盖戳的那一块被改过，`evidence` 不会自己接受（它的回复有 `stale` 个数和一行 `next` 说明，不再写「all evidence rows verified」），要重读代码后把证据行用另一个锚点重新引用；锚点或文件没了、没有证据根，都拒绝。没有浏览器时 `gate=smoke` 的门是 SKIPPED，回复仍是 `ok`（页面已经写出），`gate:"SKIP"` 和第一行 `next` 醒目地说页面**没有**被验证，`verify` 才给出判定。`sheet` 是写好的 oracle 表（相对项目的路径），MCP 从不写用户的确认文件 `atlas/oracle-confirm.txt`。会替换 `dist/` 里自己拥有的部分，所以注解为破坏性、幂等。需要 `GEMARC_ALLOW_WRITE=1`。

### `gemarc_atlas_verify`

对已构建的页面跑浏览器门，并把结论绑定在**这一页**上（`dist/.verify/gate-stamp.json` 记模型 sha 和页面 sha256）：更早的构建留下的通过不能满足门。只存在于 `atlas` 工具集。

| 输入 | 默认 | 说明 |
|---|---|---|
| `project` | 必填 | 项目名 |
| `widths` | `[1024,1366,1600]` | 一到六个宽度（500 到 4000） |

输出：`{status, authority, gate, widths[], lines[], modelSha8, report（`atlas/dist/.verify/gate-report.json`）, shots, browser}`，上限 3,000 字节。只有每个宽度都 PASS 才是 `ok`；操作者用 `GEMARC_ATLAS_BROWSER` 指定的路径不出现在回复和报告里（写作 `the pinned browser`）；`status` 的 browser 门要求印记里 1024、1366、1600 三个宽度都 PASS，只传一个宽度的 `verify` 不会让 `done`；FAIL、SKIPPED（没有找到浏览器：没通过）和 INCONCLUSIVE（浏览器启动超时，机器忙：没通过，也没发现缺陷）都是 `invalid`（`GEMARC_ATLAS_BUILD_030`、`_031`、`_033`）；没有页面或页面不是当前模型的页面是 `_032`，浏览器不会启动。浏览器由 `GEMARC_ATLAS_BROWSER`（可选，指向 Edge 或 Chrome 的可执行文件）或默认安装位置找到：服务器环境被过滤、没有 `ProgramFiles*` 时（Codex、Hermes）也会按标准安装目录（`ProgramFiles(x86)`、`ProgramFiles`、`LOCALAPPDATA` 的默认值）找，不需要转发这些变量；Edge 装在别处时才需要设置 `GEMARC_ATLAS_BROWSER`。各宽度合起来约 150 秒（重试也计入这个预算：一个宽度的失败启动在剩余不足 10 秒时不再重试，重试最多得到剩下的时间；最后一次启动结束时杀进程树、读管道的清理另算，通常不到几秒，最坏十几秒）：第一个 INCONCLUSIVE 之后不再启动后面的宽度，没启动的宽度在 `widths[]` 里是 `INCONCLUSIVE`（`NOT_RUN`），回复的 `message` 与 `lines` 写明哪些宽度没有启动；浏览器输出有上限，超限是失败（`PROCESS`）而不是判定（见 `src/gemarc_mcp/atlas/gate/README.md`）。注解：写工具，非破坏性，幂等。需要 `GEMARC_ALLOW_WRITE=1`。

### `gemarc_atlas_review`

一次性自审（设计附录 A1 的 D15）。只存在于 `atlas` 工具集。`status` 的 phase 到 `review` 时调用：先 `report`，核对，只修复列出的问题，再 `record`。

| 输入 | 默认 | 说明 |
|---|---|---|
| `project` | 必填 | 项目名 |
| `action` | `report` | `report`（只读，从不写文件）或 `record`（写 `.atlas/review.json`，需要 `GEMARC_ALLOW_WRITE=1`） |
| `notes` | `""` | 只对 `record` 有意义，**必填**（空或全是空白被拒绝），至多 4 KB；写 `fixed=RV-C2,RV-K1; open=RV-H1`，其中的 `fixed` 会被核对 |

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

`record` 只有一次：必须在 phase 为 `review` 时调用（其余 `done` 条件都已满足），否则 `GEMARC_ATLAS_REVIEW_003`；已有有效记录时 `GEMARC_ATLAS_REVIEW_002`；没有写开关时 `GEMARC_AUTH_002`，不写任何文件。通过这两道拒绝之后还有两道守卫（`GEMARC_ATLAS_REVIEW_001`，什么都不写）：`notes` 不能为空；在服务器进程里，必须先对**当前模型**做过一次 `report`（抽样行被看过；修复改了模型之后、以及进程重启之后都要再 `report` 一次，它只读）。命令行入口（每次调用是一个进程）只要求 `notes`。`report` 在记录已经过期（出现了新的 BLOCK）时，`next` 不再叫人去 `record`。记录是 `.atlas/review.json`：`{schema, modelSha, mode, findingsAtReview, blockKeys, fixedAfter, notes}`（没有时间戳）。`fixedAfter` 是 `notes` 里声称已修复、且现在确实不再出现的编号。`record` 的回复里有 `done`、`phase`、仍然存活的 BLOCK 发现（写进最终报告交给用户）。

`status` 对记录的读法：模型与记录的 `modelSha` 一致，则记录有效；模型在记录之后又改过，只有同时满足"完整检查没有 ERROR（以及没有红稿、没有 BLOCK 槽位、其他门为绿）"和"没有新的 BLOCK 发现"才仍显示 `done`。"新的 BLOCK 发现"只比较模型本身能显示的那几类（`RV-E1`、`RV-H2`、`RV-H4`），读文件的检查（`RV-E5`、`RV-H5`）只在记录时判定。没有 `blockKeys` 的记录只认完全相同的模型。这条规则由 `review.accepts_changed_model` 实现，`status.evaluate` 调用它。记录带 `blockKeys`（评审工具写的记录都带）而新出现了 BLOCK 发现时，`phase` 是 `author` 而不是 `review`：已经有记录，`record` 不会接受第二次（`GEMARC_ATLAS_REVIEW_002`），所以不会要求第二次自审。

## 4. 注解表

下表与真实的 `tools/list` 逐项一致（测试强制）；`—` 表示该工具没有这一项（读工具没有 `destructiveHint` 和 `idempotentHint`）。每个工具在它所属的每个工具集里一行，最后一列是工具集。

| 工具 | 标题 | readOnly | destructive | idempotent | openWorld | 工具集 |
|---|---|---|---|---|---|---|
| `gemarc_atlas_brief` | Work order | true | — | — | false | atlas |
| `gemarc_atlas_init` | Create project | false | false | false | false | atlas |
| `gemarc_atlas_put` | Store one slice | false | true | true | false | atlas |
| `gemarc_atlas_check` | Check project or text | true | — | — | false | atlas |
| `gemarc_atlas_status` | Project status | true | — | — | false | atlas |
| `gemarc_atlas_index` | List model contents | true | — | — | false | atlas |
| `gemarc_atlas_simulate` | Blind verdicts | false | false | true | false | atlas |
| `gemarc_atlas_doc` | Document outline | true | — | — | false | atlas |
| `gemarc_atlas_outline` | Source outline | true | — | — | false | atlas |
| `gemarc_atlas_evidence` | Verify evidence | false | false | true | false | atlas |
| `gemarc_atlas_build` | Build the atlas | false | true | true | false | atlas |
| `gemarc_atlas_verify` | Browser gate | false | false | true | false | atlas |
| `gemarc_atlas_review` | Self-review | false | false | false | false | atlas |
| `gemarc_atlas_brief` | Work order | true | — | — | false | atlas_remote |
| `gemarc_atlas_check` | Check slice text | true | — | — | false | atlas_remote |

## 5. 入口提示 `gemarc-atlas-create`

用户只说一句话（"创建5D设计"）就能推进到完成并自审一次。MCP 服务器不能主动推动客户端，所以入口是一个 MCP 提示：Claude Code 把它显示成斜杠命令，Codex 和 Hermes 可以用 `scripts/atlas_mcp.py get-prompt` 取出文本后粘贴。提示的文本就是驱动脚本，状态在项目目录里，所以随时可以说"继续"从 `gemarc_atlas_status` 续上。

参数：`requirement`（需求原文或已保存文件的路径）、`mode`（`asis` 或 `tobe`；不写时给了需求原文就是 `tobe`，否则 `asis`；其他值被拒绝）、`project`（小写字母、数字、连字符）、`lang`（`zh-CN`、`ja-JP` 或 `en-US`，也接受 `zh`、`ja`、`en`；默认 `zh-CN`，其他值被拒绝）。

流程：读工作指令，建项目，把需求原文保存到项目的 `user/`，然后循环 {`gemarc_atlas_status` 的 `view=next` -> 只写它点名的那个切片 -> `gemarc_atlas_put`；红稿用 `mode=patch` 修复}，直到 phase 到 `build`；然后按 `status` 点名的顺序过各道门（`gemarc_atlas_simulate`，as-is 再 `gemarc_atlas_evidence`，`gemarc_atlas_build`，`gemarc_atlas_verify`）；phase 到 `review` 后恰好一次自审（`report`、只修列出的问题、必要时重做上面的门、`record`）；最后写最终报告，并把 `atlas/dist/oracle-sheet.md` 交给用户确认。

停止条件（提示里逐条写明）：

- `needs-user`：停下，把 `needsUser` 原样问给用户，绝不替用户决定、绝不编造事实。
- 同一切片最多修复 3 轮，第 4 次仍红就报告 `blocked`，附最后一次诊断。
- `put` 总次数预算 = 8 + 2 x ceil(节点数/11)（来自 `gemarc_atlas_brief` 的 economics 一节"每 11 个节点约两次 put"，估不出节点数时按 40 个，即 16 次）；用完仍未到 review 就报告 `blocked`。
- 自审恰好一次：`gemarc_atlas_review` 先 `report` 取机械报告，核对后只修复列出的问题，再 `record`；`record` 之后不开第二轮自审，仍然存活的 BLOCK 发现写进最终报告交给用户。
- 没有浏览器（`verify` 回复 SKIPPED）：停下问用户：装 Edge 或 Chrome，或由用户自己把理由写进 `atlas/waive-browser.txt`；绝不替用户写这个文件。
- as-is 没有配置证据根：`status` 给出 `needs-user`，停下，把 `needsUser` 原样交给用户，不要反复调用 `gemarc_atlas_evidence`。
- `done` 为 true 之后写最终报告时，先读 `status` 的 `openBlocks`（仍然打开的 BLOCK）、`waived` 和 `next` 里的 `await` 行，逐条写进去。

`atlas_remote` 带一个简短的变体：只用 `gemarc_atlas_brief` 和 `gemarc_atlas_check`，切片保存在你自己的本地文件里，自审用对照源码抽查代替，结论是 advisory。

## 6. 完成契约：`status` 的门

`gemarc_atlas_status` 的 `done` 只在下面全部成立时为 true；`phase` 为 `build` 表示还有门是红的，`next` 点名它们（每道门一行，写明要调用的工具）。门只**读**记录（账本、印记、`dist/`），`status` 从不运行判定、核验器或浏览器，所以它仍是只读调用。

| 条件 | 含义 |
|---|---|
| 结构 | 没有红稿，完整检查没有 ERROR（WARN 不挡；`next` 的 done 行写 `check full has no ERROR`，还有 WARN 时写 `(N WARN open: ...)`），没有 BLOCK 槽位，1D 到 5D 都就绪（只要有适用的 oracle 门，4D 和 5D 就是必需的：至少一个已封存的用例期望和一个已封存的干预期望） |
| `oracle` 门 | 每条期望都是 sealed 或 confirmed，且针对**当前**模型有通过的判定（`simulate`），没有空转（覆盖下限）。sealed 但用户还没确认的期望不挡路：它们写在 `oracle-sheet.md` 上、写进最终报告。被改动的期望（`revised`）只有用户能处理：但在 `build` 写出 `oracle-sheet.md` 之前没有可批准的东西，所以那时 `phase` 仍是 `build`（`next` 叫你 simulate、build），表写出之后才是 `needs-user` |
| `evidence` 门 | 按声明判断：每个 Implemented 声明的源码证据都有印记，且印记对应的文件和锚点与当前证据行一致（`gemarc_atlas_evidence`；一行后来核验失败（FAIL）就失去印记，这道门随之变红）。to-be 的上限是 Designed，不会有这类声明（EV-010），所以这道门对它是空的；判断看模型里的声明，不看 `atlas.toml` 的 `mode`。这些声明所在的别名没有任何可用的证据根时，AI 无事可做：`phase` 是 `needs-user`，`needsUser` 说明操作者要设置 `GEMARC_ATLAS_ROOTS 别名=<目录>`，或用户同意降低这些声明的 `verif=` |
| `docs` 门 | 派生文档里没有 `[NOT-AUTHORED]` 空位（`gemarc_atlas_doc` 的 `markers` 为 0） |
| `build` 门 | `dist/` 是当前模型的构建：页面没被改过，文档已写出（`gemarc_atlas_build`） |
| `browser` 门 | 浏览器门在**这一页**上以 1024、1366、1600 三个宽度通过（`gemarc_atlas_verify`，印记绑定页面 sha256）。没有浏览器时是醒目的 SKIPPED，**不算通过**，也挡着 `done`，出路在用户手里：装浏览器，或由用户把理由写进 `atlas/waive-browser.txt`（任何工具都不写这个文件，和 `oracle-confirm.txt` 一样是声明，不是认证）。FAIL 永远不被豁免覆盖。被豁免的 `done` 在 `status` 里带 `waived:["browser"]`，不算验证 |
| 自审 | 当前模型有恰好一次的自审记录。记录之后模型又改了：没有新的 BLOCK 发现就仍算数；出现了新的 BLOCK，`phase` 回到 `author`（`next` 点名新发现），修掉即可，**不会**要求第二次自审 |

这些门只在 MCP 工具和命令行入口里生效（`status.product_gates()`）；直接调用 `status.evaluate` 的单元测试看不到它们。

顺序和重做：`record` 只在 `phase` 为 `review`（其余条件都成立）时被接受，所以自审修复改了模型之后，要按 `status` 点名的顺序重做 `simulate`、`evidence`、`build`、`verify`，然后才 `record`。

## 7. 命令行客户端

`scripts/atlas_mcp.py` 是一个只依赖标准库和官方 MCP SDK 的薄客户端，通过真实的 stdio 与服务器通话，供手工测试，也供没有 MCP 接入的无头代理使用：

```
python scripts/atlas_mcp.py list-tools
python scripts/atlas_mcp.py list-prompts
python scripts/atlas_mcp.py get-prompt gemarc-atlas-create mode=asis project=demo
python scripts/atlas_mcp.py call gemarc_atlas_status '{"project":"demo"}'
```

输出是紧凑 JSON，回复为 `isError` 时退出码非零。服务器环境不继承整个环境（SDK 只传过滤后的环境，与 Claude Code、Codex、Hermes 一致）：转发本 shell 里的 `GEMARC_*`，`GEMARC_TOOLSET` 默认 `atlas`。接入三个客户端的配置见 `docs/atlas/examples/README.md`。

## 8. 工具包路由与远程检查

- `tools/make_kit.py` 生成 `dist-kit/atlas-kit.zip`（纯标准库的 atlas 包、启动器 `atlas.py`、README、规格；确定性；不提交）。ASGI 应用里的 `GET /kit/atlas.zip` 是本地的可选项：只有环境变量恰好是 `GEMARC_ATLAS_KIT=1` 才存在，别的值（包括空、`true`、` 1`）都和任何未知的 kit 一样回 404，方法限制与默认行为不变；atlas 包不在时（Vercel 的上传里就是这样）回 503 和 `{"error":"atlas_kit_unavailable","code":"GEMARC_ATLAS_KIT_001"}`。它不是工具，不进任何工具集。
- `python scripts/verify_remote.py <url> --atlas` 在一个运行 `GEMARC_TOOLSET=atlas_remote` 的服务器上多检查：两个只读工具、advisory 的切片检查（有效、损坏、超过 32 KB 三种输入）、回复里没有文件系统路径、kit 路由（`--atlas-kit on|off|auto`）。不带 `--atlas` 时脚本的行为不变；对 `stateless` 的部署带 `--atlas` 会失败，这是有意的（atlas 没有部署）。
