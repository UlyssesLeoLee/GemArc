# 阅读面规范：`gemarc-atlas-read/1`

状态：里程碑 R1 与 R2 的实现规范（R2 加 HTML 孪生，见第 10-12 节）。它描述 `src/gemarc_mcp/atlas/reading/` 现在做的事，页与清单的格式以本文为准。设计依据是 A3 设计（拆分的阅读面），它的第 0.2 节决定表（D-1…D-36）没有被改动。本文的示例 id 全部是合成 id。生成的页与卡以主语言（`meta.languages[0]`）出，zh-CN 为主；ja-JP 的固定文字取 en-US 文案（D-24）。

## 1. 它是什么

`dist/` 里除了单页查看器 `index.html`，多出一个目录形状的阅读面：AI 先读 `START.md`，再按问题选页；每页一个事实一个家，自带范围卡与可见的页尾哨兵行。**阅读面不改查看器**：`reading.build` 只读 `bundle`（`build.make_bundle` 的十键字典）与 `sealed_meta`，不改它们，也不写盘。

```
dist/
  START.md                   入口卡（card）
  manifest.jsonl             每个文件一行，最后写
  ids.tsv  refs.tsv          只 grep 的查表文件
  .ignore                    给 rg 的忽略表
  read/G01.md                术语与标记图例（card）
  read/A01.md                小项目：全部内容一页（page）
  read/1D/  R01.. T01.. E01..  P1D.md  INDEX.md      需求、待决与政策、证据
  read/2D/  S01.. D01.. M01..  P2D.md  INDEX.md      结构、数据、状态机
  read/3D/  W01..                                    空间
  read/4D/  C01..                                    用例（本版本只含结构）
  read/QA/  Q01                                      质量卡
  read/P3Q.md                3D、4D、QA 的折叠页
  docs/README.md             交接说明卡（ai:false）
  site/                      HTML 孪生（给浏览器与 WebFetch，不给 shell；镜像 START.md、read/**、screens/INDEX*.md、screens/NAV*.md）
```

`read/5D/`（干预页）不在本版本里：由里程碑 R3 写。HTML 孪生 `site/` 由里程碑 R2 写（第 10 节）；`ReadConfig.html` 为假时没有 `site/`，清单里每个文件的 `twin` 都是 `null`，**Markdown 的每个字节都不变**。

## 2. 页格式

一页是 `head + body + foot`，物理行，LF，UTF-8。`head` 是 8 行（续页 9 行），`foot` 以一个空行开头、以哨兵行结束。固定文字在 `derive/i18n/<lang>.json` 的 `read` 对象里，检查从同一批模板反推正则。

| 位置 | 内容 |
|---|---|
| 第 1 行 | `页 §S07 · <标题> · 7/34 · 共 N 行 · 页类 page`；`N` 是文件物理行数（含页尾）|
| 第 2 行 | `项目 <projectId> · <语言> · m=<model sha8> · 仅结构 · 核对是否过期: gemarc_atlas_index scope="stamp"` |
| 第 3-4 行 | `# <标题>`（多页族加 `· 第 i/n 页`）与一个空行 |
| 第 5-7 行 | 范围卡 `> 含:`、`> 不含:`、`> 读法:`，各一行，按字节上限裁成整项 |
| 续页第 8 行 | `> 续: 本页是 <单元 id> 的第 i/n 部分；其余部分: <页 id 列表>` |
| 页尾 | 空行；`邻页:` 行（上一页、下一页、索引、START，无者省略）；续页再加 `（未完：下一部分在 §<页>）` 或 `（本单元到此结束）`；末行哨兵 |
| 哨兵 | `页尾 §S07 · 共 N 行 · sha8 H · 以上为全部内容`，`H` 是去掉末行后全部字节的 SHA-256 前 8 位 |

页 id 是族字母加两位序号（`S07`，超过 99 用三位），固定 id 有 `START`、`INDEX-<维度>[-<片>]`、`A01`、`P1D`、`P2D`、`P3Q`、`G01`。文本里一律写 `§S07`；文件名里没有 `§`、也没有模型 id。页号只在同一个 `m=` 内有效。

### 2.1 范围卡

- `含`：`<标签> <n>` 用 `、` 连接，后跟 `（<范围>，<顺序>，<完整|部分: n / m>）`。标签与页内小节标题一一对应（RS-122 用同一张对应表核对）。多族页只列每个族的主小节。
- `不含`：本族其余页（页 id 区间）与其它族的入口页。整项丢弃，不在 id 中间截断。
- `读法`：页内出现 `[N/A]`、`[NOT-AUTHORED:…]` 或等级词时必写图例；出现等级词时内联完整有序标尺（等级 id 按档位从高到低，文档里印的就是等级 id）。标尺放不下时降级成指向 `§G01` 的一句，并记 L 类 `RS-132`。

### 2.2 小节、表与计数

每个小节标题带条数：`### 出向连接（21）`；一个长单元的部分页写 `### 入向连接（67 / 161，第 59-125 行）`。每张**页构建器自己出的表**紧挨一行带条数的标题；碎片内部的表（用例的事件、实体的列、状态机的转移）属于它的块标题，整块原样印出，不加标题。块的第 2 行是 `` `id` · 种类 · 等级 ``，表的首格是 `` `id` ``。

### 2.3 逐字与视图

碎片（`bundle.fragments.items[].md`）逐字印出，一行不改。页构建器另外渲染的内容是**视图**，在 `ids.tsv` 里 `role=view:*`，不是家：入向连接行、需求的『落实它的要素』表、测试表、楼栋楼层房间表、相关图的引用行、续页的身份行、质量卡。续页之外，节点块末尾追加一行 `- 入向连接: k（见本页『入向连接』表）`。

### 2.4 长行与折叠（可逆）

任何物理行超过 480 个字符都要折叠，折叠可以逐字还原：

- 表里的长单元格换成 `见注 [k]`，全文在这张表下面以 `[k] …` 行给出；
- 其它长行在空格或字符边界折行，每个续行以 `（续）` 开头，被切开的空格留在续行行首；
- 页记录的 `fit` 是折叠的行数；L 类 `RS-132` 记一条带点名的 finding。

`parse.unfold_lines` 是还原函数，`RS-128` 在还原后的行上比较。

### 2.5 续页

单元（一个节点连同它的行、一个用例、一台状态机）整个放一页；放不下时在**表的行接缝**上切成部分，每一部分占一页，每页内容不超过续页容量。每一部分以身份行开头（`### <标题>（续 i/n）` 与 `` `<单元 id>` · <种类> ``），重复表头，表标题写本部分行数、总行数与行区间，范围卡多一条 `续:`。没有部分数上限；一个单元超过 3 页时 `ReadResult.findings` 多一条 INFO（`INFO-SPLIT`，建议在模型里拆分，不是规则、不阻断）；一个原子（单行）放不下一页时是 I 类 `RS-101`。

## 3. 家：每个事实印在哪

输入全集是整份 DD 文档的碎片（`always` 加上每个要素在 DD 级的闭包，**用 `bundle.closures` 的掩码，不用 `item.level`**）去掉结构组（`c1 c2 chmap xmap sign dg`），再加上只在 REQ 文档里的 `req` 组。每个碎片由它的组恰好分到一个族；没有家的组不是兜底，是 I 类 `RS-128`。

| 族 | 维度 | 单元 | 碎片组 |
|---|---|---|---|
| S | 2D | 节点块 + 以它为起点的连接行 + 它的端口行 + 以它为终点的入向视图行；按（楼栋、楼层级别、房间、节点）顺序，房间优先 | `n.DD` `e.DD` `ports` |
| D | 2D | 数据表块；数据政策行 | `ent.DD` `pol:6:data` |
| M | 2D | 状态机块（转移表可续页） | `mach` |
| W | 3D | 楼栋行、楼层行、房间行 | 模型 `space` |
| R | 1D | 需求行、覆盖行、需求的验收项、落实它的要素视图；末尾是测试表 | `req` `cov` `acc`（需求的） |
| T | 1D | 待决行、约束行、非功能行、政策行；空组的空态句也写 | `tbd` `con` `nfr` `pol:*` |
| E | 1D | 证据行（含模型里没有被任何要素引用的证据，用同一个渲染函数） | `ev` |
| C | 4D | 用例块（事件表可续页）与它的验收项；未封存的用例写一句空态 | `case.DD` `acc`（用例的） |
| Q | QA | 质量字段原样印出、被扣下的项、排除表 | 无碎片 |
| G A P | 根 | 术语卡、单页、折叠页 | i18n |

- **`ifc` 不印**：接口行的每个单元格都是同编号连接行的单元格（DD 边表加了标签列之后），所以 `ifc` 列在 Q01 的排除表里，理由 `rebuildable-from:e.DD`。这句话由 `RS-128` **逐行机械核对**，不是断言；有一行不能重建就是 I 类 `ERROR`。
- 需求的验收项归 R 页，用例的验收项归 C 页（设计表把验收项都列在需求里；用例的验收项紧挨着它的期望更好读）。
- 相关图：每个小节后一张 `### 相关图（n）`，每页对同一个图只写一次，链接指向 `diagrams/<图 id>.mmd`。
- 本版本没有干预页（V），START 的 5D 行写原因与干预数量；用例页只含结构，不含运行结果（R3）。

## 4. 切页

只看 Markdown。`CONTENT(类) = cap_md[类] − 信封`，信封是 `reading.api.content_capacity` 按最坏位宽渲染一次的结果（续页用续页的信封）；`cap_md` 是 card 6,000、page 9,000，没有 wide。规划器**量真实渲染出来的行**，不估算：

```
对每个接缝（房间；其它族每个单元一个接缝）：
    放得下 → 放进开着的页；
    否则开着的页已满 60% 以上 → 收页，再放；
    否则把接缝里的单元逐个放，放不下就收页。
```

折叠：全部内容合计放得下 `A:page` 时只出 `A01`；否则 1D（R+T+E）、2D（S+D+M）、3D–QA（W+C+Q）各自合计放得下 `P:page` 时折成 `P1D`、`P2D`、`P3Q`，每族一个 `##` 分节，各带自己的 `> 含:` 行。`INDEX` 在一个维度有 ≥4 页时才写；超过 6,000 B 分片，每片链接全部分片。START 的维度表对每个族都有一行，零计数写「本项目没有」与原因。

## 5. 清单、`ids.tsv`、`refs.tsv`

**`manifest.jsonl`**：JSON Lines，键按字典序，没有时间戳，每行 ≤500 个字符（`sum` 截到 100，`range` 的 id 截到 60，仍超则先丢 `sum` 再丢 `range`）。第 1 行是头：`schema readSchema build classes files aiFiles aiBytes diagramFiles m lang project mode content blockers qaSha8 withheldCount ui`（可有 `u`）。每个文件一行：`p id k fam dim cls ai b l tok sha8 twin twinOf sum n i of range m fit`（`i`、`of` 是本页在本族的序号与总数；折叠页与单页另有 `fams`，列出它含的族，`serve` 靠它拒绝含 C、V 的页）。`k`：`start index page glossary doc prompt sheet diagram viewer data twin`（孪生行另有 `twinOf`，是它镜像的 md 页；屏幕卡的孪生是画廊页，`twinOf` 取卡的第一部分 `<enc>.card.md`；孪生的 `cls` 是 `twin`、`ai` 为假、`tok` 按每令牌 1.7 字节估算）。**不登记**：`build-info.json`、`screens/build-info.json`、清单自身、`.verify/**`；`oracle-sheet.md` 只登记存在（没有 `b l sha8`）。清单没登记的陌生文件只记 `RS-109` WARN。`finalize(dist_dir, result)` 在构建写完所有文件之后扫描目录，字节数、行数、`sha8` 取自磁盘，最后写清单。

**`ids.tsv`**：头两行是 `#` 注释；列 `id kind role path line hdr name`；`kind` ∈ `node edge case event requirement test tbd evidence machine transition entity room floor building diagram`（本版本没有 `intervention`）；`role` 是 `home`（每个 (id, kind) 恰一行）或 `view:inbound`；`line` 是块的第 2 行或表行的行号，`hdr` 是最近标题行（块则是它自己的标题行）；`name` 截到 120 个字符，边的名字是 `<起点>><终点> <标签或关系>`。精确查用锚定的 `grep -P '^<id>\t' ids.tsv`。

**`refs.tsv`**：列 `target field owner path line`，`field` ∈ `req ev test tbd`。一条引用在**模型里有这条引用且属主的家把它印成了代码片段**时才列；表格会封顶（一行边只印 4 个依据），被封顶藏起来的引用没有行可指，只计数（`ReadResult.plan["unprinted_refs"]`，参考规模约 12-14%）。

## 6. 检查

`reading/check.py`。I 类只能由生成器缺陷引起，ERROR 使 `ReadResult.status == "invalid"`（该树不应写出）；L 类是作者数据能碰到的上限，构建绝不因它失败，渲染器按构造降级并给出带点名的 finding；T 类只在测试里跑。

| 类 | 规则 |
|---|---|
| I，本版本实现 | RS-101 页大小与页类，102 行长，103 信封与范围卡，104 哨兵（含孪生的计数与封印），105 同一个戳，106 链接（含孪生的地址），107 表标题与计数，109 清单与目录（含孪生的有无与互指），**110 孪生卫生（R2）**，111 续页，113 同一组数字，**114 孪生等于页（R2）**，116 一个事实一个家，117 文件名，120 入向连接，121 可达性，122 范围卡计数，123 图例与标尺，124 证据列，125 交接文件，128 守恒，129 START 维度表，131 refs.tsv |
| I，登记但本版本不适用 | RS-108、115、127（R3：结果块与图头部） |
| L | RS-112 页大小分布（WARN），RS-132 降级桶（折叠、标尺指针、START 超限时把客户端提示移到术语卡、`.mmd` 超限、**孪生超过 `cap_html`**），RS-101 里不经规划器的 md（START 等）超限，RS-102 里画廊内联的 SVG 长行，RS-117 的路径过长与大小写碰撞；L 类 finding 都进 `ReadResult.degraded`，带一条点名的 hint |
| T | RS-118 违禁词，119 确定性（三个 `PYTHONHASHSEED` 的子进程），126 `index.html` 不变，130、133 工具 |

每条 I 类检查有一个变异夹具：改写好的树里的一处，只有那一条红（`tests/atlas/test_read_checks.py`）。作者数据不能触发 I 类：检查对转义的方括号、作者文本里的 `§` 都不误报。

## 7. 公共接口

```python
reading.build(bundle, sealed_meta=None, cfg=ReadConfig(), ui=None) -> ReadResult      # 纯函数，不写盘
reading.finalize(dist_dir, result) -> Manifest                                           # 扫描目录，最后写 manifest.jsonl
reading.serve.load_page(dist, page_id, current=None) -> dict                             # 读已写出的文件，不重算任何结果
reading.serve.stamp_line(dist, current) -> str   reading.serve.page_list(dist, dim=None) -> [str]
reading.check.check_surface(src, surface, only=None) -> [Finding]   reading.check.surface_from_dist(dist) -> Surface
reading.render_site(files, lang, gallery=None, context=None, problems=None) -> {孪生路径: bytes}   # R2；也在 reading.api 里（加性）
reading.render_twin(path, text, lang, index=None) -> bytes   reading.api.visible_md(text) / visible_html(data) -> [原子]
```

`ReadConfig(html, docs, sizes)`：`html` 为真时 `build` 同时写 `site/` 里的孪生（缺省 `False`，因为 `build` 是不写盘的纯函数；lane I 由 `atlas.toml [read] html`（缺省 true）构造它）；`docs` 为真时结果含 `docs/README.md`；`sizes` 是构建已知的文件大小（`index.html`），只用来在 START 里写取整的 KB。`ui` 是 `{"decision": "" | "yes" | "only" | "none", "reason", "entry", "hint"}`，START 第 ⑥ 节据此写。`sealed_meta = {"withheld": {"cases": [...], "interventions": [...]}, ...}`；本版本只用 `withheld`（Q01 的被扣下的项）。

`ReadResult`：`status`、`files`（路径到字节）、`records`（清单行）、`findings`（`Finding(rule, klass, severity, path, message, hint)`）、`header`、`plan`、`ids`、`refs`、`exclusions`；`errors` 是 I 类 ERROR，`degraded` 是 L 类规则 id 的排序列表。

`serve.load_page` 的失败码：`GEMARC_ATLAS_DOC_020` 页戳不是当前戳（不返回正文），`_021` 还没有构建，`_022` 没有这个页（列 3 个最接近的 id），`_023` 结果页（C、V 族，以及含这两族的折叠页与单页）是文件、不经此函数（回复带 `stamp`）。

## 8. 实测（R1，合成预设，zh-CN，种子 1-4 与 6-8）

| 预设 | S 页 | 阅读页（含 INDEX、G01） | md 字节 | 最大页 | 页字节中位 | 信封占页字节 | aiFiles | aiBytes | 构建 |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| tiny | 0（单页 A01） | 2 | 9.9 KB | 7.4 KB | 7.4 KB | 9.8% | 3 | 14 KB | 0.01 s |
| small | 7-8 | 20-21 | 96-104 KB | 8.8 KB | 6.1-7.6 KB | 12.6-14.3% | 21-22 | 100-108 KB | 0.11 s |
| reference | 33-35 | 61-64 | 426-438 KB | 8.8 KB | 7.6-7.9 KB | 10.5-10.8% | 62-65 | 430-442 KB | 0.5 s |
| refcjk | 44-47 | 84-89 | 614-628 KB | 8.8 KB | 7.7-8.0 KB | 10.2-10.6% | 85-90 | 617-632 KB | 0.5 s |

每个预设与种子：0 条 I 类 finding。`tests/atlas/test_read_plan.py` 把页数钉在这些测量值的 ±15%；改动须附 `tools/pilot/read_matrix.py` 打印的表。碎片改动（DD 边表的标签列、证据的仓库别名与行范围列）使 `index.html` 在参考规模多约 24 KB（refcjk 约 60 KB）。

## 9. 与设计不同的地方

1. 增加折叠页 `P3Q`（3D、4D、QA）：设计在 10-20 KB 一档页数超过 `2 + ceil(内容/5,400)` 时要求把 3D–QA 并入折叠，页 id 文法因此多一个 `P3Q`。
2. 小节标题用「出向连接」「入向连接」两个对称的标题，不用设计示例里的「连接」。
3. 用例的验收项归 C 页；R3 之前用例页没有结果块，也没有判定行。
4. 没有干预页（V）：见第 1 节（R3）。HTML 孪生见第 10 节。
5. `refs.tsv` 的全集是「模型里有且属主的家印出来了」的引用；被表格封顶藏起来的引用只计数。

## 10. HTML 孪生（里程碑 R2）

**给谁读。** `site/` 是给浏览器与 `WebFetch` 读的，不给 shell 读（对一份 17 KB 的 HTML 做 `cat`，Codex 会保留头尾、截掉中间）；AI 默认读 md。START 第 ⑤ 节「不要整份读入」里有一行 `` `site/`（若存在） `` 说明这一点，**无论孪生是否写出都写这一行**：这样 `html=false` 不改变任何 md 字节（设计 D-4）。START 不链接 `site/` 里的任何文件（`ai:false`，RS-121）。

**路径镜像（D-2）。** `twin(p) = "site/" + 去掉 .md + ".html"`，对 `START.md`、`read/**`、`screens/INDEX*.md`、`screens/NAV*.md`；`docs/**`、`prompts/**`、`oracle-sheet.md`、`.mmd` 没有孪生。**唯一的例外**是屏幕卡：`screens/<enc>.card(-n).md` 的孪生是画廊页 `site/screens/<enc>.html`，由 lane U 写。`render_site` 因此跳过卡；调用方可以传 `gallery(卡路径, 卡文本) -> bytes | None` 把画廊交进来（这是 U 填补例外的钩子），指向卡的链接无论如何都指向画廊。lane I 渲染 U 的 `INDEX`、`NAV` 时，把 `reading.build` 的页作为 `context` 传入，它们指向 START 与阅读页的链接才是孪生的链接。

**孪生是 md 的纯后处理。** 一个 md 物理行变成一个 HTML 块，所以两边的原子序列按构造对齐：

| md | HTML |
|---|---|
| `#`…`######` | `<h1>`…`<h6>`；层级 ≥2 且**紧接着表头行**的标题变成那张表的 `<caption>`（文字不重复） |
| 块的 `### 名` + 下一行以代码片段开头 | `<section id="enc(id)">`，含 `<h3>`、身份行与后续行，到下一个 1-3 级标题为止 |
| `> …` 连续行 | 一个 `<aside>`，每行一个 `<p>`（范围卡） |
| `- …` 连续行 | 一个 `<ul>`，每行一个 `<li>` |
| 表 | `<div class="w"><table>`（`.w` 允许横向滚动）含 `<thead>`、`<tbody>`；首格恰是一个代码片段的行写 `<tr id="enc(id)">` |
| 围栏 | `<pre><code class="language-…">`（NAV 的 Mermaid 块） |
| 其它行 | `<p>`；`（续）` 续行保持独立的 `<p>`，孪生不展开 md 折过的行 |

**行内。** 一个函数 `tokenize` 同时服务渲染与 `visible_md`：代码片段原样（表里 `\|` 是 `|`），反斜杠加 ASCII 标点是那个标点，`&amp; &lt; &gt;` 是三个字符，`[文字](地址)` 是链接，其余都是字面。作者文本里的 `*`、`**`、`_` 两边都保持字面（设计 2.5 写「`visible` 去掉 `**`」；只在 md 一侧去掉会让作者文本把 I 类检查弄红，所以两侧都不去）。

**id。** `<section id>` 取块身份行的代码片段，`<tr id>` 取首格；**同一页里 id 第一次出现者得到属性，之后的相同 id 不带属性**，所以 `id` 按构造唯一（入向连接表的首格是终点节点，与节点块的 id 相同，它不带 id；边的家是出向表）。属性值是 `enc(id)`。`reading.render_html.block_ids` 是 md 到 id 序列的函数，RS-114 用它核对。

**链接。** md 的相对地址先解成 `dist/` 里的路径：目标有孪生就指向孪生（相对路径与 md 相同），否则指向真实文件，从孪生所在目录算（比页深一层，`../../diagrams/x.mmd` 在孪生里是 `../../../diagrams/x.mmd`）。正文里的 `§页id`（不在代码片段里、页在站点里）变成链接；不认识的页保持文字。`javascript:` 之类不会成为地址（它被当成相对路径）。

**第 1 行与哨兵。** 孪生的第 1 行与哨兵用**逻辑计数**代替行数：`含 N 个块、M 张表`（`read.twin.*`）；N 是 `<section>` 数，M 是 `<table>` 数。哨兵里的 `sha8` 是它镜像的 md 页的 `sha8`。这两行不参与文字比较，只比其中的页 id、页类、计数、`sha8`。

**语言。** 孪生用**页自己的语言**（`render_html.detect_lang`：页第 2 行的第二个 `·` 记号）做固定文字，调用方传的 `lang` 只是页没有写明时的后备。界面模型有自己的语言（`ScreensConfig.lang` 缺省取界面模型的 `meta.languages[0]`），它的 INDEX、NAV 与画廊可以与项目主语言不同；RS-110 因此把孪生的 `lang` 与**它自己那一页**的语言比，不与项目主语言比。

**一行 ≤500 个字符。** 折行只发生在不改变可见文字的地方：`</td>`/`</th>` 之后，代替正文里的一个空格，或标签内 `>` 之前（`<code\n>` 是同一个元素）。从不在一段连续文字的两个字符之间折（回车在那里对浏览器是一个空格，会使 `visible` 不等）。折不动的行保持原样。

**头与样式。** `<!doctype html>`、`<html lang>`、`Content-Security-Policy` 的 `<meta>`（取 `htmlassemble.CSP_POLICY`，**导入不是复制**）、`<meta charset>`、viewport、`<title>`、`atlas-page|stamp|class|lang|schema` 五个 `<meta>`、指向 md 的 `<link rel="alternate" type="text/markdown">`、**一个** `<style>`（12 条规则，一规则一行，627 字节，每页相同）。无 `<script>`、无外链、无 `data:`。设计 2.5 写的策略是 `default-src 'none'; style-src 'unsafe-inline'; img-src data:`；任务与查看器页用同一常量，所以孪生里还带着查看器的 `script-src 'unsafe-inline'`，它对无脚本的孪生没有作用，RS-110 禁止 `<script>`。

**检查（I 类）。**

- RS-110 孪生卫生：doctype；没有 `script iframe frame object embed form base img audio video input button textarea`、没有 `on*` 属性、没有带协议或 `//` 的地址、没有 `data:` 地址、属性与样式里没有 ≥200 字符的 base64 串（只看属性与样式，不看正文：作者的长串不能使它变红）；恰一个 http-equiv 的 `<meta>`，且就是 `CSP_POLICY`；`lang` 等于项目语言；charset 在前 1,024 字节；有 `<title>`、viewport；样式就是那一个固定样式（≤2,000 B）；每张表有 `<caption>`**或落在某个块 `<section>` 里**（块内的表在 md 里也没有自己的标题行，RS-107 同样限定，设计没有预见这一点）；`id` 不重复。
- RS-114 孪生等于页：`visible_md` 与 `visible_html` 逐原子相等（第 1 行与哨兵除外）；`id` 序列等于 `block_ids`；`atlas-*` 五个 meta 与页一致；`alternate` 链接指向页；**文档子句**：`docs/<语言>/{REQ,BD,DD}.md` 在磁盘上时，每个有家的碎片 `md` 逐字出现在这三份文档里（文档不在时不适用；构建时的检查看不到文档，`read --check` 与 `verify` 看得到）。
- RS-104（孪生半）：第 1 行与哨兵是孪生模板，计数等于孪生里真实的块数与表数，`sha8` 与页码等于 md 页的；RS-106（孪生半）：每个 `<a href>` 与 `<link href>` 解析到清单里的文件，且指向孪生的目标不能写成 `.md`；RS-109（孪生半）：孪生开启时每个有孪生的 md 恰有一个孪生，清单两边互指（`twin`、`twinOf`、`cls twin`、`ai false`），没有无主的孪生；关闭时没有 `site/`、没有行指向孪生；RS-132（L）：孪生超过 `cap_html`（card 14,400、page 21,600）只降级。
- **画廊的例外（lane U 的请求 U2-6）**：画廊页满足卫生的第一组（没有脚本、外链、`data:`、事件属性）与一个**只收窄**查看器策略的策略（它的指令必须是 `CSP_POLICY` 指令的子集，`default-src 'none'`）；不受样式上限、`id` 唯一与表标题规则约束；RS-104 只核对哨兵里的 `sha8`；RS-114 只要求**卡所说的**（表格单元格、列表项、普通行；不含标题、表题、范围卡、页头页尾，也不含只写「（无）」的空态行）是画廊文字的子集；RS-102 里内联 SVG 的长行是 L 类降级。

## 11. 实测（R2，合成预设，zh-CN，种子 1-4 与 6-8；en-US、ja-JP 各取种子 1）

| 预设 | 孪生数 | md | 孪生 | 卡类字节比（孪生/md） | 页类字节比 | 最大单页比 | 最大孪生 | 孪生增量 |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| tiny | 3 | 14 KB | 25 KB | 1.76 | 1.90 | 1.90 | 14.3 KB（上限 21.6 KB） | 0.02 s |
| small | 21-22 | 106 KB | 206 KB | 2.14-2.15 | 1.92-1.94 | 3.07 | 18.1 KB | 0.1 s |
| reference | 62-65 | 432 KB | 790 KB | 2.08-2.11 | 1.81-1.82 | 2.70 | 17.7 KB | 0.35-0.5 s |
| refcjk | 85-90 | 618 KB | 1,026 KB | 2.06-2.08 | 1.63-1.65 | 2.71 | 15.4 KB | 0.4-0.7 s |

每个预设与种子：`status ok`，0 条 I 类 finding，md 逐字节等于 `html=false` 的构建。比值的预注册判据（T-R2）是**每个页类的总体比 ≤2.4**，并且每个孪生 ≤ 它的 `cap_html`；两条都过。**单页比不受 2.4 约束**：固定的头与样式约 1.3 KB，一页 976 字节的 md（`small` 的 1D INDEX）孪生是 2.9 KB（比 2.97），这是固定开销，不是标记膨胀；最大单页比出现在 `3D/W01`（房间表，单元格很短）与这类小卡上。设计 8.1 的止损线（页类总体比 >2.2 时精简标记）：卡类最高 2.15，未触发。

## 12. R2 与设计不同的地方

1. **块内的表没有 `<caption>`**，RS-110 把「每张表有 caption」限定为「有 caption 或落在块的 `<section>` 里」（见第 10 节）。
2. **作者文本里的 `**` 不在 md 侧被去掉**（见第 10 节「行内」）。
3. **START 的 `site/` 一行无条件写出**（设计没有指定它是否随 `html` 变化；D-4 要求 `html=false` 不改 md）。
4. **`ReadConfig.html` 缺省为假**，`atlas.toml [read] html` 缺省为真；lane I 负责把后者接到前者。
5. **`id` 规则「第一次出现者得 id」**取代设计里「每行 `<tr id>`」：入向连接表的首格与节点块的 id 相同，逐行都写会重复。
6. **CSP 取 `htmlassemble.CSP_POLICY`**（任务要求），不是设计 2.5 写的三指令版本。
7. **RS-114 的文档子句**只在文档在磁盘上时运行；`ReadConfig` 一侧的 `build` 看不到文档。
8. **画廊的子集规则**取「卡所说的」而不是「卡的全部原子」：卡续页重复范围卡与续页身份行，页头页尾（`邻页: …`）画廊用自己的导航写法，这些在画廊里没有同一行文字。
9. **孪生的逻辑计数只有块与表**（`含 N 个块、M 张表`），设计 2.5 的示例还有每张表的行数：一页的表可能有几十张，列出每张的行数会撑破 500 字符的行限；表行由 RS-114 的原子逐个核对，不靠计数。
10. **孪生用页自己的语言**，不用调用方传的语言（见第 10 节「语言」）。
