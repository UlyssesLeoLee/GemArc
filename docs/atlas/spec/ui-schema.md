# 界面族规范：`gemarc-atlas-ui/1`

状态：规范（normative），对应实现 `src/gemarc_mcp/atlas/protoui/`（U1：格式、词表、模型、展开、规则、存储）。布局、SVG、画面卡、画廊、PNG 与判定检查（UV-*）属于后续里程碑（U2 起），它们的规范另写在 `screens-contract.md`，本文不含。本文是中文，标识符、选项名、路径保持英文；所有示例都是合成的。

## 1. 它是什么

界面族是 GemArc Atlas 的**第二类产物**：一份给 AI 核对前端设计用的原型界面的**事实**。AI 只写事实（屏幕、状态变体、控件树、导航，可选链接到 5D），固定代码推导布局并渲染；AI 不写 HTML，也不写 SVG。

它与 5D 模型 `gemarc-atlas/1` 互相独立：

- 5D 模型有且只有它契约里的 13 个集合；界面不是第 14 个集合。把界面并进去，每改一个按钮都会使 5D 的 `model_sha`、封存、复核、门禁戳作废。
- 界面有自己的首行（`#atlas-ui 1`）、自己的目录、自己的切片名约定（`ui-` 开头）、自己的红稿、历史、计数、锁和哈希（`ui_sha8`）。
- 依赖是单向的：界面可以引用 5D 的 id（全部可选，有 5D 时才核对），5D 永远不引用界面。
- 两个解析器互不相认：5D 解析器读界面文件得 ST-001（首行不是 `#atlas 1`），界面解析器读 5D 切片同样得 ST-001。

## 2. 文件与目录

| 项 | 取值 |
|---|---|
| 作者文件 | UTF-8，首行 `#atlas-ui 1`（不带任何选项，版本号不是 1 是 ST-004），`//` 开头是注释行；只开放 `.atl`（`.jsonl` 不做） |
| 切片名 | `ui-` 加 1 到 37 个 `[a-z0-9-]`（合计至多 40 个字符）；`ui-verdict-` 开头的是判定切片 |
| 已提交切片 | `<project>/atlas/ui/<name>.atl` |
| 红稿 | `<project>/atlas/.atlas/ui/red/<name>.atl`（被拒绝的整份文本，永不丢失） |
| 历史 | `<project>/atlas/.atlas/ui/history/<name>.<sha8>.atl`（每个切片最近 10 版） |
| 计数、锁 | `<project>/atlas/.atlas/ui/puts.json`、`<project>/atlas/.atlas/ui/lock` |
| 大小 | 单个切片至多 64 KiB（ST-008，整个文件被拒绝）；超过 6 KiB 是 `SZ-001`（WARN，约 8 屏）；已提交切片加红稿合计至多 2 MiB |

界面存储**不在** `atlas/ui` 与 `atlas/.atlas/ui` 之外创建或改动任何东西：不碰 5D 的切片目录、5D 的锁、`puts.json`、`oracle.json`、`review.json`，也不触发 5D 的提交钩子（界面有自己的 `UI_COMMIT_HOOKS`）。界面的 `put` 按**首行**分流（见第 9 节）。

## 3. 作者格式

语法与 5D 的 `.atl` 相同（同一个记号器，复制进 `protoui/atl.py`，不共享代码）：

- `## name: col | col | opts` 开始一个段，表头必须原样抄写；一行里 `|` 分隔的文本单元格数量必须与表头一致（ST-005），单元格不能为空；之后是唯一一个选项单元格，由 `key=value` 组成，值可以是裸词、`"带引号字符串"`、`{json}` 或 `[json]`；列表用逗号连接。文本里的字面 `|` 写成 `\|`。
- `@set k=v` 给本段后面的行设标量默认值（`@set screen=SCR-LOGIN`），单元格写 `~` 取默认值，`@set k=` 清除；列表和 JSON 不能用 `@set`。
- `@final nav` 声明导航已写完（放行 UX-201、UX-202、UX-205）；`@pending id,id` 压住有意的前向引用。
- 文本单元格里出现像选项的内容（`kind=button`）是 ST-006：给该单元格加引号。含 ASCII 引号的文本单元格必须是一个完整的 JSON 字符串。
- 有语法缺陷的行被丢弃并报告，切片的其余部分照常读取；写了派生键是 DV-001（不是 ST-004）。

### 3.1 段、列与选项

`conform` 与 `diffs` 只允许出现在 `ui-verdict-*` 切片里，`ui-verdict-*` 切片只允许这两个段；其余四个段只允许出现在普通 `ui-*` 切片里。放错了是 ST-004。因此判定行永远进不了原型模型，写判定不改变 `ui_sha8`。

```
## screens:  id | title | purpose | opts     opts: kind frame route start actor req group align
## variants: screen | name | note | opts      opts: kind machine state text
## widgets:  local | label | opts             opts: screen* kind* parent w h cols in bind emph data api req align
## nav:      from | to | label | opts         opts: on guard api case rule req alias
## conform:  screen | verdict | basis | opts  opts: sha checked repo
## diffs:    ref | kind | note | opts         opts: expected found basis
```

（`*` 必填，可由 `@set` 给。`conform` 的屏幕哈希选项叫 `sha`：位置列已经叫 `screen`，同名的选项会重复位置单元格。）

| 段.字段 | 取值与规则 |
|---|---|
| screens.id | `^[A-Za-z][A-Za-z0-9_-]{0,39}$`，不含 `. : # ~` |
| screens.kind | `page dialog sheet panel`，缺省 `page` |
| screens.frame | `desktop`（1280x800）`tablet`（834x1112）`phone`（390x844），缺省 `desktop` |
| screens.route | `^/[A-Za-z0-9._~/:{}-]{0,119}$` |
| screens.start | `true`/`false`，入口屏幕（可多个） |
| screens.actor | 5D 节点 id（链接，必须是 `kind=Actor`） |
| screens.req | 5D 需求 id 列表（链接） |
| screens.group | 短记号，流程组名 |
| screens.align / widgets.align | `start center end` |
| variants.name | `^[a-z][a-z0-9_]{0,23}$`，**不得是 `default`**（`default` 变体由工具派生，恒存在） |
| variants.kind | `loading empty error success locked other`，缺省 `other` |
| variants.machine / state | 5D 机器 id / 状态名（链接；`state` 必须与 `machine` 同时给） |
| variants.text | 界面文案（空态、出错态画进体内）；`note` 只是设计说明，不画进体内 |
| widgets.local | `^[a-z][a-z0-9_-]{0,31}$`，不得叫 `root` 或 `state`（UX-010：它们是每个屏幕 SVG 里根分组与状态条的 id）；完整 id 是 `<screen>.<local>` |
| widgets.screen | 屏幕 id，必填（通常 `@set screen=...`） |
| widgets.kind | 31 种控件种类之一，必填（见第 5 节） |
| widgets.parent | 同屏容器控件的 local，缺省是屏幕根；行序即兄弟顺序 |
| widgets.w / h | `fill`、`hug`、整数像素或 `Nfr` |
| widgets.cols | 1 到 12，仅 `grid` |
| widgets.in | 变体名列表（`default` 合法）：控件只在这些状态里出现；缺省是所有状态 |
| widgets.bind | 数据字段名 `^[A-Za-z][A-Za-z0-9_.]{0,39}$` |
| widgets.emph | `primary secondary danger muted` |
| widgets.data | JSON 对象，表格或列表的示例内容 |
| widgets.api | 5D 节点或边 id（链接） |
| nav.from | 屏幕 id 或 `<screen>.<local>`（控件必须是可点击种类，UX-003） |
| nav.to | 屏幕 id 或 `<screen>#<variant>` |
| nav.on | `tap submit change auto back timeout key`，缺省 `tap` |
| nav.guard | 用语言写的条件 |
| nav.case / rule | 5D 用例 id / 该用例某个事件的 `ruleId`（链接；`rule` 须与 `case` 同时给） |
| nav.alias | 同一对 from、to 的第二条以后必须带别名 |
| conform.verdict | `conforms differs unchecked` |
| diffs.kind | `differs missing extra unverifiable` |

**派生键**（作者写了是 DV-001）：screens 的 `variantNames widgetIds navOutIds navInIds`；variants 的 `id`；widgets 的 `id screenId parentId order depth childIds container navSource interactive`；nav 的 `id fromScreenId toScreenId`。

### 3.2 微型示例（合成）

```
#atlas-ui 1
// 借阅台原型界面:合成示例,不是真实系统。
## screens: id | title | purpose | opts
SCR-LOGIN | 登录 | 会员用邮箱和密码进入借阅台。 | kind=page frame=desktop route=/login start=true actor=usr.member req=RQ-1
SCR-HOME | 我的借阅 | 列出会员当前借出的书。 | kind=page frame=desktop route=/home req=RQ-1
## variants: screen | name | note | opts
SCR-LOGIN | error | 邮箱或密码不正确。 | kind=error
## widgets: local | label | opts
@set screen=SCR-LOGIN
form | 登录表单 | kind=form
email | 邮箱 | kind=input parent=form bind=email
submit | 登录 | kind=button parent=form emph=primary api=svc.loan
@set screen=SCR-HOME
loans | 借出的书 | kind=table w=fill
## nav: from | to | label | opts
SCR-LOGIN.submit | SCR-HOME | 凭证正确,进入首页 | on=submit
```

测试夹具 `tests/atlas/ui_fixtures/ui-10-login.atl` 是完整的两屏示例（对合成夹具 `lending_desk` 0 条诊断）；`ui_fixtures/sample12/` 是 12 屏、23 个变体、79 个控件、30 条导航的样例（8,833 字节）。

## 4. id 文法

| 对象 | id | 备注 |
|---|---|---|
| 屏幕 | `SCR-LOGIN` | 无 `. : # ~`；不得是 `INDEX NAV START CARD` 或 Windows 设备名（不分大小写，UX-009）；两个屏幕 id 不得只差大小写（UX-008） |
| 控件 | `SCR-LOGIN.email` | 第一个 `.` 是分隔；索引精确查表，不按 `.` 拆串推断 |
| 根分组 | `SCR-LOGIN.root` | `root` 是保留的 local |
| 状态 | `SCR-LOGIN#error` | `default` 恒存在；`#` 不在 5D 的 id 字符集里 |
| 导航 | `SCR-LOGIN.submit:go:SCR-HOME` | 超过 120 个字符时是 `h:` 加 `sha256("uinav\n" + from + "\n" + to)` 的前 32 位十六进制；有 `alias` 时即别名 |

屏幕、控件、变体、导航的 id 在界面内全局唯一，重复是 ST-002（两处位置都点名）。界面 id 可以与某个 5D id 相同（链接列有类型，不会混淆），但有 5D 时是 UX-005（WARN）。

## 5. 词表与 `kind` 到 DOM 的对照

词表只有 id（`protoui/vocab.py`），标签在 `protoui/i18n/{zh-CN,en-US}.json`；ja-JP 回退 en-US。`UI_VOCAB_VERSION` 在 id 增减时加 1。共 55 个带标签的 id：31 个控件种类、4 个屏幕种类、3 个框架、6 个状态种类、4 个强调、7 个触发。

每个控件种类有三个旗标：`container`（可作父）、`navSource`（导航可从它出发）、`interactive`（接收输入）。

| kind | 旗标 | 对应的 DOM（线索级） |
|---|---|---|
| `stack` | C | `<div>`，纵向排列 |
| `row` | C | `<div>`，横向排列 |
| `grid` | C | `<div>`，网格排列 |
| `card` | C N | `<article>` 或带边框的 `<div>` |
| `form` | C | `<form>` |
| `header` | C | `<header>` |
| `footer` | C | `<footer>` |
| `sidebar` | C | `<aside>`、`<nav>` |
| `toolbar` | C | `role=toolbar` |
| `tabs` | C | 子项为 `role=tab` 的容器 |
| `list` | C N | `<ul>`、`<ol>`、`role=list` |
| `heading` | - | `<h1>` 到 `<h6>` |
| `text` | - | `<p>`、`<span>` |
| `image` | N | `<img>`、`role=img` |
| `icon` | N | `<svg>`、`<i>` |
| `badge` | - | `<span>` |
| `divider` | - | `<hr>` |
| `chart` | - | `<canvas>`、`<svg>` |
| `table` | N | `<table>`、`role=grid` |
| `input` | I | `<input>`、`role=textbox` |
| `textarea` | I | `<textarea>` |
| `select` | I | `<select>`、`role=combobox` |
| `checkbox` | I | `<input type=checkbox>` |
| `radio` | I | `<input type=radio>` |
| `switch` | I | `role=switch` |
| `button` | N I | `<button>`、`role=button` |
| `link` | N I | `<a href>` |
| `tab` | N I | `role=tab` |
| `menu` | N I | `role=menu`、`<nav>` |
| `pagination` | I | `<nav>` 里的分页控件 |
| `custom` | - | 任意（带标签的占位块，逃生口） |

（C = container，N = navSource，I = interactive。对照表只用于**线索级**检查：`scan` 可以报"标签不符？"，永远不报"一致"。）

## 6. 模型与哈希

`expand_ui(slices, five=None)` 得到的模型：`{schema, meta, screens, variants, widgets, nav}`，`schema == "gemarc-atlas-ui/1"`，键按规范 JSON 排序，数组保持作者顺序。`meta` 取 `projectId`、`title`、`languages`（来自项目配置，不含 5D 的事实）和 `profile: "ui"`。模型里没有像素位置（布局另算，有自己的整数 `layoutVersion`），没有判定，没有时间戳或路径。

- `ui_sha8`：规范字节（紧凑 JSON，一个结尾 LF）的 SHA-256 前 8 位十六进制。与 5D 的 `model_sha` 互不影响。
- `verdict_sha8`：判定行（`{"schema": "gemarc-atlas-ui-verdict/1", "conform", "diffs"}`）的同样算法；判定行不进 `ui_sha8`。
- `screen_sha8`：一屏**核对点**的哈希（`{"schema", "screen", "points": [[ref, value], ...]}` 的规范字节的 SHA-256 前 8 位），画面卡上的 `判定戳 sha=`；判定行的 `sha=` 抄它。只有该屏的某个核对点变了它才变（标题、用途、`req`、`data`、尺寸不动它）；与 `ui_sha8`、5D 的 `model_sha` 都无关。
- 派生字段：屏幕的 `variantNames`（`default` 在前）、`widgetIds`（控件树先序）、`navOutIds`、`navInIds`；控件的 `parentId`（根是 `null`）、`order`（兄弟序，从 0 起）、`depth`、`childIds`；导航的 `id`、`fromScreenId`、`toScreenId`。

## 7. 核对点

核对点是前端核对能对着比的声明，`id = <控件 id>@<列>` 等：

| 核对点 | 值 | 条件 |
|---|---|---|
| `<widget id>@kind` `@label` | 种类、文字 | 恒有 |
| `<widget id>@parent` | 父控件的 local | 有父控件 |
| `<widget id>@bind` `@api` `@emph` | 字段名、5D id、强调 | 有该选项 |
| `<widget id>@in` | 逗号连接的状态名 | 有 `in=` |
| `<widget id>@goto` | 逗号连接的导航目标 | 有从它出发的导航 |
| `<container id>@order` | 子控件 local 的有序列表 | 容器有 2 个以上子控件 |
| `<screen>.root@order` | 根下控件 local 的有序列表 | 根下有 2 个以上控件 |
| `<screen>@route` | 路由 | 有 `route` |
| `<screen>#<name>@state` | 变体种类 | 每个作者写的变体 |

`req`、`case`、`rule`、`data` 是到 5D 的链接或示例数据，不是界面事实，不计核对点。

## 8. 规则（`UX-*`，独立注册表）

规则有自己的注册表（`protoui/rules.py`），不进 5D 的共享注册表；严重度总取自本注册表。波次 1 在 slice 与 full 两种模式下都跑，波次 3 中标 **绑定** 的三条在 slice 模式下等 `@final nav`，标 full 的只在 full 模式跑。

| id | 波 | 级别 | 检查什么 |
|---|---|---|---|
| ST-001..ST-008、DV-001 | 1 | ERROR | 语法与结构（由前端与展开器产生，意义同 5D） |
| ST-009 | 1 | ERROR（full） | 有红稿：先修红稿 |
| SZ-001 | 1 | WARN | 切片超过 6 KiB |
| UX-001 | 1 | ERROR（slice：待定或一次编辑拼写 WARN） | 引用了未定义的屏幕、控件或变体（`widgets.screen`、`parent`、`in`、`variants.screen`、`nav.from`、`nav.to`） |
| UX-002 | 1 | ERROR | 控件树不合法：父控件不是容器种类、父链有环、`tab` 不在 `tabs` 里 |
| UX-003 | 1 | ERROR | `nav.from` 的控件种类不可点击 |
| UX-006 | 1 | WARN | 两个屏幕同一个 `route` |
| UX-007 | 1 | WARN | 一屏超过 40 个控件 |
| UX-008 | 1 | ERROR | 两个屏幕 id 只差大小写 |
| UX-009 | 1 | ERROR | 屏幕 id 是 Windows 设备名或 `INDEX NAV START CARD` |
| UX-010 | 1 | ERROR | 控件 local 是 `root` 或 `state` |
| UX-011 | 1 | WARN | `title` 超过 40 个字符，或 `purpose`、`label`、`note`、`text` 超过 120 个字符 |
| UX-005 | 3 | WARN（full） | 界面 id 与某个 5D id 相同 |
| UX-201 | 3 | WARN，无入口屏幕时 INFO（绑定） | 从入口屏幕沿导航不可达 |
| UX-202 | 3 | WARN（绑定） | `page` 屏幕有入边、没有出边（`on=back` 算出边） |
| UX-203 | 3 | WARN（full） | 含 `table list chart` 的屏幕既无 loading 也无 empty 变体；含 `form` 的屏幕无 error 变体 |
| UX-204 | 3 | WARN（full） | `form` 里有输入类控件却没有 `button` |
| UX-205 | 3 | WARN（绑定） | `button`、`link` 既没有导航、也没有 `api`、也没有 `req` |
| UX-206 | 3 | ERROR | 到 5D 的链接解析；slice 模式待定或拼写 WARN，没有 5D 时合并为一条 INFO |
| UX-207 | 3 | INFO（full） | 界面引用过需求，但 5D 里还有需求没有被引用 |

判定规则 `UV-001` 到 `UV-008` 有自己的表（`protoui/verdict.py`），合同在 `screens-contract.md` 第 14 节；`put` 判定切片与全量 `check` 时运行。

布局规则 `UX-101` 到 `UX-104` 随布局一起注册，不在本里程碑。没有 `UX-004`。

**级联**：一行被前端拒绝（ST-001、ST-003 到 ST-007、DV-001）后，指向它本该定义的 id 的引用是后果，标记为 `derived`（只计数不列出）。屏幕 id 还按"差一次编辑"匹配（`SCR.HOME` 被拒绝，`SCR-HOME` 的引用是后果）；对其它已定义 id 的真实拼写错误不抑制。

## 9. 存储与分流

- `route(name, text)`：由**首行**决定族（`#atlas-ui 1` 界面，`#atlas 1` 5D）；名字的 `ui-` 前缀只作二次校验。名字以 `ui-` 开头而首行是 `#atlas 1`，得 `GEMARC_ATLAS_UI_008`；首行是 `#atlas-ui 1` 而名字不是 `ui-<name>`，得 `GEMARC_ATLAS_UI_009`。`atlas/slices` 里碰巧叫 `ui-layer` 的 5D 切片不是界面。
- `ui_present(paths)`：只看 `atlas/ui` 与 `atlas/.atlas/ui/red`。
- `put`：slice 模式校验（已提交部分在前，新文本在后，只报告新文本里的发现）。没有 ERROR 就提交（旧版本进历史，红稿清除）；有 ERROR 则什么都不提交，整份文本成为该切片的红稿。`mode=patch` 按行标识改红稿（没有红稿则改已提交版本）：屏幕按 `id`，变体按 `screen`+`name`，控件按 `screen`+`local`，导航按 `from`+`to`（+`alias`），`-<键单元格>` 删除；`base=<sha8>` 是乐观并发。
- 预算：原型切片的 `put` 总数 `4 + 2 x ceil(屏数/4)`（12 屏：10 次），判定切片 `2 + ceil(屏数/2)`；计数写 `atlas/.atlas/ui/puts.json`（`puts`、`verdictPuts`、逐切片的连续红稿数），不占 5D 的预算。
- 写入围栏：`atlas`、`atlas/.atlas`、`atlas/ui`、`atlas/.atlas/ui`、`red`、`history` 是链接、联接点或解析到工作区外时拒绝写入（`GEMARC_AUTH_001`）。

## 10. 版本

| 对象 | 版本 | 规则 |
|---|---|---|
| 界面模型 | `gemarc-atlas-ui/1` | /1 内只许加可选作者选项、词表 id、派生字段；新增必填选项或改语义必须升 /2 |
| 作者格式 | `#atlas-ui 1` | 版本号不是 1 是 ST-004 |
| 词表 | `UI_VOCAB_VERSION` | id 增减加 1 |
| 规则 id | 已发布不复用不改义 | 新规则只加新 id |
| 布局 | 整数 `layoutVersion`（现为 1） | 改盒坐标即加 1；同 `ui_sha8` 加同 `layoutVersion` 必须得到逐字节相同的 SVG |
